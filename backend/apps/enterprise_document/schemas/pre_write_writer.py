"""Target initial-Writer invocation and execution-result contracts."""

from __future__ import annotations

from collections.abc import Iterator, Mapping, Sequence
from enum import Enum
from typing import Annotated, Literal, cast

from pydantic import (
    BaseModel,
    BeforeValidator,
    ConfigDict,
    Field,
    JsonValue,
    PlainSerializer,
    StringConstraints,
    TypeAdapter,
    model_serializer,
    model_validator,
)

from apps.enterprise_document.schemas.pre_write_binding import WriterSubjectRefSchema
from apps.enterprise_document.schemas.pre_write_control import (
    PreWriteRouteDecisionRefSchema,
)
from schemas.common import ErrorSchema
from schemas.rag import EvidenceBundleSchema


_NonEmptyStr = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1)]
_Sha256Hex = Annotated[
    str,
    StringConstraints(
        strip_whitespace=True,
        min_length=64,
        max_length=64,
        pattern=r"^[0-9a-f]{64}$",
    ),
]
_JSON_VALUE_ADAPTER = TypeAdapter(JsonValue)


class _FrozenJsonList(Sequence[object]):
    """Recursively immutable JSON array with ordinary sequence read semantics."""

    __slots__ = ("_values",)

    def __init__(self, values: Sequence[object]) -> None:
        object.__setattr__(self, "_values", tuple(_freeze_json(item) for item in values))

    def __getitem__(self, index: int | slice) -> object:
        return self._values[index]

    def __len__(self) -> int:
        return len(self._values)

    def __iter__(self) -> Iterator[object]:
        return iter(self._values)

    def __setattr__(self, name: str, value: object) -> None:
        raise TypeError("canonical JSON arrays are immutable")

    def __repr__(self) -> str:
        return repr(self._values)

    def __eq__(self, other: object) -> bool:
        if isinstance(other, Sequence) and not isinstance(other, (str, bytes)):
            return list(self) == list(other)
        return False


class _FrozenJsonMap(Mapping[str, object]):
    """Recursively immutable JSON object with mapping and attribute reads."""

    __slots__ = ("_items", "_lookup")

    def __init__(self, values: Mapping[str, object]) -> None:
        normalized: list[tuple[str, object]] = []
        seen: set[str] = set()
        for raw_key, value in values.items():
            key = raw_key.strip()
            if not key:
                raise ValueError("canonical JSON object keys must be non-empty")
            if key in seen:
                raise ValueError("canonical JSON object keys must be unique")
            seen.add(key)
            normalized.append((key, _freeze_json(value)))
        items = tuple(normalized)
        object.__setattr__(self, "_items", items)
        object.__setattr__(self, "_lookup", dict(items))

    def __getitem__(self, key: str) -> object:
        return self._lookup[key]

    def __iter__(self) -> Iterator[str]:
        return (key for key, _ in self._items)

    def __len__(self) -> int:
        return len(self._items)

    def __getattr__(self, name: str) -> object:
        try:
            return self._lookup[name]
        except KeyError as exc:
            raise AttributeError(name) from exc

    def __setattr__(self, name: str, value: object) -> None:
        raise TypeError("canonical JSON objects are immutable")

    def __repr__(self) -> str:
        return repr(dict(self._items))

    def __eq__(self, other: object) -> bool:
        if isinstance(other, Mapping):
            return dict(self.items()) == dict(other.items())
        return False


def _freeze_json(value: object) -> object:
    """Copy one validated JSON value into recursive immutable containers."""

    if isinstance(value, (_FrozenJsonMap, _FrozenJsonList)):
        return value
    validated = _JSON_VALUE_ADAPTER.validate_python(value)
    if isinstance(validated, dict):
        return _FrozenJsonMap(validated)
    if isinstance(validated, list):
        return _FrozenJsonList(validated)
    return validated


def _freeze_json_object(value: object) -> _FrozenJsonMap:
    frozen = _freeze_json(value)
    if not isinstance(frozen, _FrozenJsonMap):
        raise ValueError("value must be a JSON object")
    return frozen


def _json_value(value: object) -> JsonValue:
    """Return ordinary JSON containers for Pydantic dump/checkpoint payloads."""

    if isinstance(value, _FrozenJsonMap):
        return cast(JsonValue, {key: _json_value(item) for key, item in value.items()})
    if isinstance(value, _FrozenJsonList):
        return cast(JsonValue, [_json_value(item) for item in value])
    return cast(JsonValue, value)


_FrozenJsonObject = Annotated[
    _FrozenJsonMap,
    BeforeValidator(
        _freeze_json_object,
        json_schema_input_type=dict[_NonEmptyStr, JsonValue],
    ),
    PlainSerializer(_json_value, return_type=dict[str, JsonValue]),
]


def _freeze_scheme_section_snapshot(value: object) -> _FrozenJsonMap:
    """Validate and copy one Writer-produced Artifact into result-owned history."""

    # Local import avoids a schema-package initialization cycle:
    # pre_write_writer -> scheme_writer.__init__ -> section_execution -> pre_write_writer.
    from apps.enterprise_document.schemas.scheme_writer.document import SchemeSectionSchema

    artifact = SchemeSectionSchema.model_validate(_json_value(value))
    return _FrozenJsonMap(artifact.model_dump(mode="json"))


_FrozenSchemeSectionSnapshot = Annotated[
    _FrozenJsonMap,
    BeforeValidator(
        _freeze_scheme_section_snapshot,
        json_schema_input_type=dict[str, JsonValue],
    ),
    PlainSerializer(_json_value, return_type=dict[str, JsonValue]),
]


def _freeze_error_snapshot(value: object) -> _FrozenJsonMap:
    """Validate and copy technical failure data into result-owned history."""

    error = ErrorSchema.model_validate(_json_value(value))
    return _FrozenJsonMap(error.model_dump(mode="json"))


_FrozenErrorSnapshot = Annotated[
    _FrozenJsonMap,
    BeforeValidator(_freeze_error_snapshot, json_schema_input_type=ErrorSchema),
    PlainSerializer(_json_value, return_type=dict[str, JsonValue]),
]


class _FrozenSchema(BaseModel):
    model_config = ConfigDict(
        arbitrary_types_allowed=True,
        extra="forbid",
        frozen=True,
    )


class _CanonicalEvidenceInputSchema(_FrozenSchema):
    """Invocation-locked canonical Evidence value plus its content identity."""

    snapshot: _FrozenJsonObject
    evidence_bundle_sha256: _Sha256Hex

    @model_validator(mode="before")
    @classmethod
    def _validate_and_lock_evidence(cls, value: object) -> object:
        if isinstance(value, cls):
            return value
        if not isinstance(value, Mapping):
            raise ValueError("canonical_evidence_input must be a JSON object")
        data = dict(value)
        evidence_hash = data.pop("evidence_bundle_sha256", None)
        raw_snapshot = data.pop("snapshot", data)
        if data and raw_snapshot is not data:
            raise ValueError("canonical Evidence input contains unexpected fields")
        bundle = EvidenceBundleSchema.model_validate(_json_value(raw_snapshot))
        return {
            "snapshot": bundle.model_dump(mode="json"),
            "evidence_bundle_sha256": evidence_hash,
        }

    @model_serializer(mode="plain")
    def _serialize_flat_snapshot(self) -> dict[str, JsonValue]:
        payload = cast(dict[str, JsonValue], _json_value(self.snapshot))
        return {**payload, "evidence_bundle_sha256": self.evidence_bundle_sha256}

    def __getattr__(self, name: str) -> object:
        try:
            return super().__getattr__(name)
        except AttributeError:
            snapshot = object.__getattribute__(self, "snapshot")
            try:
                return snapshot[name]
            except KeyError as exc:
                raise AttributeError(name) from exc


class WriterGenerationClass(str, Enum):
    NORMAL_WRITE = "normal_write"
    FALLBACK_WRITE = "fallback_write"


class WriterLogicalExecutionKey(_FrozenSchema):
    writer_subject_ref: WriterSubjectRefSchema
    write_instance_id: _NonEmptyStr
    route_decision_ref: PreWriteRouteDecisionRefSchema


class WriterExecutionRefSchema(_FrozenSchema):
    logical_execution_key: WriterLogicalExecutionKey
    result_schema_version: Literal["writer_execution_result_v1"] = "writer_execution_result_v1"
    result_contract_version: Literal["writer_execution_result_control_v1"] = "writer_execution_result_control_v1"
    result_sha256: _Sha256Hex


class WriterGenerationInvocationRefSchema(_FrozenSchema):
    logical_execution_key: WriterLogicalExecutionKey
    invocation_schema_version: Literal["writer_generation_invocation_v1"] = "writer_generation_invocation_v1"
    invocation_contract_version: Literal["writer_generation_execution_v1"] = "writer_generation_execution_v1"
    invocation_sha256: _Sha256Hex


class WriterGenerationInvocationSchema(_FrozenSchema):
    schema_version: Literal["writer_generation_invocation_v1"] = (
        "writer_generation_invocation_v1"
    )
    contract_version: Literal["writer_generation_execution_v1"] = (
        "writer_generation_execution_v1"
    )
    writer_subject_ref: WriterSubjectRefSchema
    write_instance_id: _NonEmptyStr
    route_decision_ref: PreWriteRouteDecisionRefSchema
    generation_class: WriterGenerationClass
    canonical_evidence_input: _CanonicalEvidenceInputSchema
    unified_writer_input_snapshot: _FrozenJsonObject
    technical_execution_constraints: _FrozenJsonObject = Field(
        default_factory=lambda: _FrozenJsonMap({})
    )

    @model_validator(mode="after")
    def _semantic_snapshot_is_present(self) -> "WriterGenerationInvocationSchema":
        if not self.unified_writer_input_snapshot:
            raise ValueError("unified_writer_input_snapshot must not be empty")
        return self


class WriterExecutionStatus(str, Enum):
    SUCCESS = "success"
    TECHNICAL_FAILURE = "technical_failure"


class WriterExecutionFailureKind(str, Enum):
    INVOCATION_INTEGRITY_FAILURE = "invocation_integrity_failure"
    UNSUPPORTED_INVOCATION_CONTRACT = "unsupported_invocation_contract"
    CONTEXT_INTEGRITY_FAILURE = "context_integrity_failure"
    MODEL_OR_PROVIDER_UNAVAILABLE = "model_or_provider_unavailable"
    MODEL_TIMEOUT = "model_timeout"
    MODEL_INVOCATION_FAILURE = "model_invocation_failure"
    EMPTY_OR_UNUSABLE_OUTPUT = "empty_or_unusable_output"
    STRUCTURAL_OUTPUT_FAILURE = "structural_output_failure"
    UNRECOVERABLE_TRUNCATION = "unrecoverable_truncation"
    RUNTIME_RESOURCE_FAILURE = "runtime_resource_failure"


class WriterExecutionResultSchema(_FrozenSchema):
    schema_version: Literal["writer_execution_result_v1"] = (
        "writer_execution_result_v1"
    )
    contract_version: Literal["writer_execution_result_control_v1"] = (
        "writer_execution_result_control_v1"
    )
    logical_execution_key: WriterLogicalExecutionKey
    status: WriterExecutionStatus
    artifact: _FrozenSchemeSectionSnapshot | None = None
    failure_kind: WriterExecutionFailureKind | None = None
    error: _FrozenErrorSnapshot | None = None
    generation_provenance: _FrozenJsonObject = Field(min_length=1)
    technical_execution_metadata: _FrozenJsonObject = Field(
        default_factory=lambda: _FrozenJsonMap({})
    )

    @model_validator(mode="after")
    def _validate_disjoint_result(self) -> "WriterExecutionResultSchema":
        if self.status is WriterExecutionStatus.SUCCESS:
            if self.artifact is None:
                raise ValueError("successful Writer execution requires an artifact")
            if self.failure_kind is not None or self.error is not None:
                raise ValueError("successful Writer execution cannot contain failure data")
            subject = self.logical_execution_key.writer_subject_ref
            if self.artifact.section_id != subject.section_id:
                raise ValueError("artifact section_id must match Writer subject")
        else:
            if self.artifact is not None:
                raise ValueError("technical failure cannot contain a valid artifact")
            if self.failure_kind is None or self.error is None:
                raise ValueError("technical failure requires failure_kind and error")
        return self


__all__ = [
    "WriterExecutionFailureKind",
    "WriterExecutionRefSchema",
    "WriterExecutionResultSchema",
    "WriterExecutionStatus",
    "WriterGenerationClass",
    "WriterGenerationInvocationRefSchema",
    "WriterGenerationInvocationSchema",
    "WriterLogicalExecutionKey",
]
