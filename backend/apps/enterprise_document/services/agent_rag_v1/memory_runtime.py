"""Canonical SIF-09 Durable Run Memory semantic runtime."""

from __future__ import annotations

import base64
from dataclasses import dataclass
from datetime import datetime, timezone
import json
from typing import Any, Callable, Iterable, Mapping

from apps.enterprise_document.schemas.agent_rag_v1.memory import (
    CanonicalMemorySourceRefEnvelopeSchema,
    DomainMemoryType,
    DurableMemoryRecordSchema,
    DurableMemoryRefSchema,
    MemoryFreshnessAssessmentSchema,
    MemoryFreshnessEligibility,
    MemoryLogicalOperationRefSchema,
    MemoryProjectionPayloadEnvelopeSchema,
    MemoryProjectionRequestSchema,
    MemoryQueryPurpose,
    MemoryQueryResultItemSchema,
    MemoryQueryResultSchema,
    MemoryQuerySchema,
    MemoryRecallMode,
    MemorySelectorIndexRecordSchema,
    MemorySelectorOperator,
    MemorySourceBindingSchema,
    TypedMemorySelectorClauseSchema,
)
from apps.enterprise_document.services.agent_rag_v1.memory_store import (
    MemoryStoreConflict,
    MemoryStoreError,
    MemoryStoreIntegrityError,
    PostgresDurableMemoryStore,
)
from core.hashing import canonical_sha256
from core.runtime.side_effects import (
    CommittedResultResolutionStatus,
    LogicalOperationRef,
)


MEMORY_PROJECTION_COMMIT = "MEMORY_PROJECTION_COMMIT"


class DurableMemoryRuntimeError(ValueError):
    """Base SIF-09 semantic runtime failure."""


class MemoryTypeUnregistered(DurableMemoryRuntimeError):
    pass


class MemorySourceRefInvalid(DurableMemoryRuntimeError):
    pass


class MemorySourceScopeMismatch(DurableMemoryRuntimeError):
    pass


class MemorySourceHashMismatch(DurableMemoryRuntimeError):
    pass


class MemorySourceSetHashMismatch(DurableMemoryRuntimeError):
    pass


class MemoryProjectionSchemaInvalid(DurableMemoryRuntimeError):
    pass


class MemoryProjectionHashMismatch(DurableMemoryRuntimeError):
    pass


class MemoryIdentityDerivationMismatch(DurableMemoryRuntimeError):
    pass


class MemoryOperationConflict(DurableMemoryRuntimeError):
    pass


class MemoryRecordIntegrityInvalid(DurableMemoryRuntimeError):
    pass


class MemorySelectorSchemaInvalid(DurableMemoryRuntimeError):
    pass


class MemoryCursorInvalid(DurableMemoryRuntimeError):
    pass


class MemoryIndexProjectionError(DurableMemoryRuntimeError):
    def __init__(self, memory_ref: DurableMemoryRefSchema, message: str) -> None:
        super().__init__(message)
        self.memory_ref = memory_ref


@dataclass(frozen=True)
class MemorySourceResolution:
    """Domain source resolver observation; never Memory-owned source truth."""

    exact_source_payload: Mapping[str, Any] | None
    source_semantic_fingerprint: str | None
    source_resolvable: bool
    integrity_valid: bool
    current: bool
    lifecycle_active: bool | None = None
    historical_eligible: bool = True
    reason_codes: tuple[str, ...] = ()


SourceResolver = Callable[
    [CanonicalMemorySourceRefEnvelopeSchema],
    MemorySourceResolution,
]
ProjectionValidator = Callable[[Mapping[str, Any]], None]
ProjectionBuilder = Callable[
    [
        MemoryProjectionRequestSchema,
        MemorySourceResolution,
        tuple[MemorySourceResolution, ...],
    ],
    Mapping[str, Any],
]
SourceBindingValidator = Callable[
    [
        MemoryProjectionRequestSchema,
        MemorySourceResolution,
        tuple[MemorySourceResolution, ...],
    ],
    None,
]
SelectorValidator = Callable[[Mapping[str, Any]], None]
SelectorProjector = Callable[[DurableMemoryRecordSchema], Mapping[str, Any]]
SelectorClauseValidator = Callable[[TypedMemorySelectorClauseSchema], None]


@dataclass(frozen=True)
class MemoryProjectionRegistration:
    memory_type: DomainMemoryType
    projection_schema_id: str
    projection_schema_version: str
    projection_policy_id: str
    projection_policy_version: str
    currentness_resolver_id: str
    currentness_policy_version: str
    projection_validator: ProjectionValidator
    projection_builder: ProjectionBuilder
    source_binding_validator: SourceBindingValidator


@dataclass(frozen=True)
class MemorySelectorRegistration:
    memory_type: DomainMemoryType
    selector_schema_id: str
    selector_schema_version: str
    allowed_keys: frozenset[str]
    selector_validator: SelectorValidator
    selector_projector: SelectorProjector
    selector_clause_validator: SelectorClauseValidator
    index_policy_id: str
    index_policy_version: str


@dataclass(frozen=True)
class MemoryReconcileResult:
    memory_ref: DurableMemoryRefSchema
    freshness: MemoryFreshnessAssessmentSchema
    selector_rebuilt: bool


def _sha256_ref(value: Any) -> str:
    return "sha256:" + canonical_sha256(value)


def _canonical_ref_key(value: Mapping[str, Any]) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    )


def _source_sort_key(
    item: CanonicalMemorySourceRefEnvelopeSchema,
) -> tuple[str, str, str, str, str]:
    return (
        item.source_authority,
        item.source_kind,
        item.source_scope_ref,
        item.logical_source_ref,
        _canonical_ref_key(item.exact_source_ref),
    )


def _logical_operation_schema(
    operation: LogicalOperationRef,
) -> MemoryLogicalOperationRefSchema:
    return MemoryLogicalOperationRefSchema(
        logical_operation_id=operation.logical_operation_id,
        operation_kind=operation.operation_kind,
        run_id=operation.run_id,
        operation_scope_ref=operation.operation_scope_ref,
        attempt_scope_ref=operation.attempt_scope_ref,
        attempt_id=operation.attempt_id,
    )


class DurableMemoryRuntime:
    """SIF-09 semantic owner over a PostgreSQL persistence adapter."""

    SELECTOR_FALLBACK_SCAN_LIMIT = 5000

    def __init__(
        self,
        *,
        store: PostgresDurableMemoryStore,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self.store = store
        self.clock = clock or (lambda: datetime.now(timezone.utc))
        self._source_resolvers: dict[
            tuple[str, str, str],
            SourceResolver,
        ] = {}
        self._projection_registrations: dict[
            tuple[DomainMemoryType, str, str, str, str],
            MemoryProjectionRegistration,
        ] = {}
        self._selector_registrations: dict[
            DomainMemoryType,
            MemorySelectorRegistration,
        ] = {}

    # ------------------------------------------------------------------
    # Registration
    # ------------------------------------------------------------------

    def register_source_resolver(
        self,
        *,
        source_authority: str,
        source_kind: str,
        exact_source_ref_schema_id: str,
        resolver: SourceResolver,
    ) -> None:
        key = (
            str(source_authority or "").strip(),
            str(source_kind or "").strip(),
            str(exact_source_ref_schema_id or "").strip(),
        )
        if not all(key) or not callable(resolver):
            raise ValueError("Memory source resolver registration is invalid")
        existing = self._source_resolvers.get(key)
        if existing is not None and existing is not resolver:
            raise ValueError("Memory source resolver registration already exists")
        self._source_resolvers[key] = resolver

    def register_projection(
        self,
        registration: MemoryProjectionRegistration,
    ) -> None:
        if not isinstance(registration.memory_type, DomainMemoryType):
            raise MemoryTypeUnregistered("unknown Memory type registration")
        if not all(
            str(value or "").strip()
            for value in (
                registration.projection_schema_id,
                registration.projection_schema_version,
                registration.projection_policy_id,
                registration.projection_policy_version,
                registration.currentness_resolver_id,
                registration.currentness_policy_version,
            )
        ):
            raise ValueError("Memory projection registration identifiers are required")
        if not all(
            callable(value)
            for value in (
                registration.projection_validator,
                registration.projection_builder,
                registration.source_binding_validator,
            )
        ):
            raise TypeError("Memory projection registration callbacks must be callable")
        key = (
            registration.memory_type,
            registration.projection_schema_id,
            registration.projection_schema_version,
            registration.projection_policy_id,
            registration.projection_policy_version,
        )
        existing = self._projection_registrations.get(key)
        if existing is not None and existing != registration:
            raise ValueError("Memory projection registration already exists")
        self._projection_registrations[key] = registration

    def register_selector(
        self,
        registration: MemorySelectorRegistration,
    ) -> None:
        if not isinstance(registration.memory_type, DomainMemoryType):
            raise MemoryTypeUnregistered("unknown Memory selector type")
        if not registration.allowed_keys:
            raise ValueError("Memory selector registration requires allowed_keys")
        if not all(
            callable(callback)
            for callback in (
                registration.selector_validator,
                registration.selector_projector,
                registration.selector_clause_validator,
            )
        ):
            raise TypeError("Memory selector callbacks must be callable")
        existing = self._selector_registrations.get(registration.memory_type)
        if existing is not None and existing != registration:
            raise ValueError("Memory selector registration already exists")
        self._selector_registrations[registration.memory_type] = registration

    # ------------------------------------------------------------------
    # Source binding / integrity
    # ------------------------------------------------------------------

    def _resolver_for(
        self,
        source: CanonicalMemorySourceRefEnvelopeSchema,
    ) -> SourceResolver:
        key = (
            source.source_authority,
            source.source_kind,
            source.exact_source_ref_schema_id,
        )
        resolver = self._source_resolvers.get(key)
        if resolver is None:
            raise MemorySourceRefInvalid(
                "no registered canonical Memory source resolver"
            )
        return resolver

    @staticmethod
    def _validate_source_envelope_hash(
        source: CanonicalMemorySourceRefEnvelopeSchema,
    ) -> None:
        expected = _sha256_ref(source.exact_source_ref)
        if source.exact_source_ref_sha256 != expected:
            raise MemorySourceHashMismatch(
                "canonical Memory exact_source_ref_sha256 mismatch"
            )

    def _resolve_source(
        self,
        source: CanonicalMemorySourceRefEnvelopeSchema,
        *,
        require_resolvable: bool,
    ) -> MemorySourceResolution:
        source = CanonicalMemorySourceRefEnvelopeSchema.model_validate(source)
        self._validate_source_envelope_hash(source)
        try:
            resolution = self._resolver_for(source)(source)
        except DurableMemoryRuntimeError:
            raise
        except Exception as exc:
            if require_resolvable:
                raise MemorySourceRefInvalid(
                    "canonical Memory source resolver failed"
                ) from exc
            return MemorySourceResolution(
                exact_source_payload=None,
                source_semantic_fingerprint=None,
                source_resolvable=False,
                integrity_valid=False,
                current=False,
                lifecycle_active=None,
                historical_eligible=False,
                reason_codes=("SOURCE_UNRESOLVABLE",),
            )
        if not isinstance(resolution, MemorySourceResolution):
            raise MemorySourceRefInvalid(
                "Memory source resolver returned invalid observation"
            )
        if not resolution.source_resolvable:
            if require_resolvable:
                raise MemorySourceRefInvalid("canonical Memory source is unresolved")
            return resolution
        if not resolution.integrity_valid:
            if require_resolvable:
                raise MemorySourceRefInvalid(
                    "canonical Memory source integrity is invalid"
                )
            return resolution
        if resolution.exact_source_payload is None:
            raise MemorySourceRefInvalid(
                "resolved Memory source lacks exact source payload"
            )
        if (
            _canonical_ref_key(resolution.exact_source_payload)
            != _canonical_ref_key(source.exact_source_ref)
        ):
            raise MemorySourceRefInvalid(
                "Memory source resolver returned different exact source ref"
            )
        if resolution.source_semantic_fingerprint != source.source_semantic_fingerprint:
            raise MemorySourceHashMismatch(
                "Memory source semantic fingerprint differs from source authority"
            )
        return resolution

    @staticmethod
    def source_set_sha256(
        primary: CanonicalMemorySourceRefEnvelopeSchema,
        dependencies: Iterable[CanonicalMemorySourceRefEnvelopeSchema],
    ) -> str:
        deps = tuple(sorted(dependencies, key=_source_sort_key))
        return _sha256_ref(
            {
                "primary_source": primary.model_dump(mode="json"),
                "dependency_sources": [
                    item.model_dump(mode="json") for item in deps
                ],
            }
        )

    @staticmethod
    def source_binding_fingerprint(
        *,
        primary: CanonicalMemorySourceRefEnvelopeSchema,
        dependencies: tuple[CanonicalMemorySourceRefEnvelopeSchema, ...],
        source_set_sha256: str,
        currentness_resolver_id: str,
        currentness_policy_version: str,
    ) -> str:
        return _sha256_ref(
            {
                "schema_version": "memory_source_binding_v1",
                "primary_source": primary.model_dump(mode="json"),
                "dependency_sources": [
                    item.model_dump(mode="json") for item in dependencies
                ],
                "source_set_sha256": source_set_sha256,
                "currentness_resolver_id": currentness_resolver_id,
                "currentness_policy_version": currentness_policy_version,
            }
        )

    def _projection_registration(
        self,
        request: MemoryProjectionRequestSchema,
    ) -> MemoryProjectionRegistration:
        key = (
            request.memory_type,
            request.projection_schema_id,
            request.projection_schema_version,
            request.projection_policy_id,
            request.projection_policy_version,
        )
        registration = self._projection_registrations.get(key)
        if registration is None:
            raise MemoryProjectionSchemaInvalid(
                "Memory projection schema/policy is not registered"
            )
        return registration

    def _resolve_projection_sources(
        self,
        request: MemoryProjectionRequestSchema,
        registration: MemoryProjectionRegistration,
        *,
        require_resolvable: bool,
    ) -> tuple[
        MemorySourceBindingSchema,
        MemorySourceResolution,
        tuple[MemorySourceResolution, ...],
    ]:
        primary = request.primary_source_ref
        dependencies = tuple(
            sorted(request.dependency_source_refs, key=_source_sort_key)
        )
        if dependencies != request.dependency_source_refs:
            raise MemorySourceSetHashMismatch(
                "Memory dependency source refs are not canonical"
            )
        primary_resolution = self._resolve_source(
            primary,
            require_resolvable=require_resolvable,
        )
        dependency_resolutions = tuple(
            self._resolve_source(
                item,
                require_resolvable=require_resolvable,
            )
            for item in dependencies
        )
        if require_resolvable:
            try:
                registration.source_binding_validator(
                    request,
                    primary_resolution,
                    dependency_resolutions,
                )
            except DurableMemoryRuntimeError:
                raise
            except Exception as exc:
                raise MemorySourceScopeMismatch(
                    "Memory source binding/domain scope validation failed"
                ) from exc
        source_set_hash = self.source_set_sha256(primary, dependencies)
        binding_fp = self.source_binding_fingerprint(
            primary=primary,
            dependencies=dependencies,
            source_set_sha256=source_set_hash,
            currentness_resolver_id=registration.currentness_resolver_id,
            currentness_policy_version=registration.currentness_policy_version,
        )
        return (
            MemorySourceBindingSchema(
                primary_source=primary,
                dependency_sources=dependencies,
                source_set_sha256=source_set_hash,
                currentness_resolver_id=registration.currentness_resolver_id,
                currentness_policy_version=registration.currentness_policy_version,
                source_binding_semantic_fingerprint=binding_fp,
            ),
            primary_resolution,
            dependency_resolutions,
        )

    # ------------------------------------------------------------------
    # Projection identity / commit
    # ------------------------------------------------------------------

    @staticmethod
    def derive_memory_ref(
        request: MemoryProjectionRequestSchema,
        source_binding: MemorySourceBindingSchema,
    ) -> DurableMemoryRefSchema:
        payload = {
            "contract": "durable_memory_ref_v1",
            "run_id": request.run_id,
            "memory_type": request.memory_type.value,
            "scope_ref": request.scope_ref,
            "source_binding_semantic_fingerprint": (
                source_binding.source_binding_semantic_fingerprint
            ),
            "projection_schema_id": request.projection_schema_id,
            "projection_schema_version": request.projection_schema_version,
            "projection_policy_id": request.projection_policy_id,
            "projection_policy_version": request.projection_policy_version,
        }
        return DurableMemoryRefSchema(
            run_id=request.run_id,
            memory_type=request.memory_type,
            scope_ref=request.scope_ref,
            memory_id="mem_" + canonical_sha256(payload),
        )

    @staticmethod
    def projection_operation(
        memory_ref: DurableMemoryRefSchema,
    ) -> LogicalOperationRef:
        return LogicalOperationRef(
            logical_operation_id=(
                "mproj_"
                + canonical_sha256(
                    {
                        "contract": "memory_projection_commit_v1",
                        "run_id": memory_ref.run_id,
                        "memory_id": memory_ref.memory_id,
                    }
                )
            ),
            operation_kind=MEMORY_PROJECTION_COMMIT,
            run_id=memory_ref.run_id,
            operation_scope_ref=memory_ref.memory_id,
        )

    @staticmethod
    def record_semantic_fingerprint(
        *,
        memory_ref: DurableMemoryRefSchema,
        source_binding: MemorySourceBindingSchema,
        projection: MemoryProjectionPayloadEnvelopeSchema,
        projection_policy_id: str,
        projection_policy_version: str,
    ) -> str:
        return _sha256_ref(
            {
                "memory_ref": memory_ref.model_dump(mode="json"),
                "source_binding": source_binding.model_dump(mode="json"),
                "projection_schema_id": projection.projection_schema_id,
                "projection_schema_version": projection.projection_schema_version,
                "projection_payload_sha256": projection.projection_payload_sha256,
                "projection_policy_id": projection_policy_id,
                "projection_policy_version": projection_policy_version,
            }
        )

    def _build_projection(
        self,
        request: MemoryProjectionRequestSchema,
    ) -> tuple[
        DurableMemoryRefSchema,
        MemorySourceBindingSchema,
        MemoryProjectionPayloadEnvelopeSchema,
        MemoryProjectionRegistration,
    ]:
        request = MemoryProjectionRequestSchema.model_validate(request)
        registration = self._projection_registration(request)
        binding, primary, dependencies = self._resolve_projection_sources(
            request,
            registration,
            require_resolvable=True,
        )
        try:
            payload = dict(
                registration.projection_builder(
                    request,
                    primary,
                    dependencies,
                )
            )
            registration.projection_validator(payload)
        except DurableMemoryRuntimeError:
            raise
        except Exception as exc:
            raise MemoryProjectionSchemaInvalid(
                "registered Memory projection failed validation"
            ) from exc
        projection = MemoryProjectionPayloadEnvelopeSchema(
            projection_schema_id=request.projection_schema_id,
            projection_schema_version=request.projection_schema_version,
            projection_payload=payload,
            projection_payload_sha256=_sha256_ref(payload),
        )
        return (
            self.derive_memory_ref(request, binding),
            binding,
            projection,
            registration,
        )

    def _validate_record_integrity(
        self,
        record: DurableMemoryRecordSchema,
    ) -> DurableMemoryRecordSchema:
        record = DurableMemoryRecordSchema.model_validate(record)
        self._validate_source_envelope_hash(record.source_binding.primary_source)
        for source in record.source_binding.dependency_sources:
            self._validate_source_envelope_hash(source)
        expected_set = self.source_set_sha256(
            record.source_binding.primary_source,
            record.source_binding.dependency_sources,
        )
        if expected_set != record.source_binding.source_set_sha256:
            raise MemorySourceSetHashMismatch("Memory source_set_sha256 mismatch")
        expected_binding_fp = self.source_binding_fingerprint(
            primary=record.source_binding.primary_source,
            dependencies=record.source_binding.dependency_sources,
            source_set_sha256=expected_set,
            currentness_resolver_id=record.source_binding.currentness_resolver_id,
            currentness_policy_version=record.source_binding.currentness_policy_version,
        )
        if (
            expected_binding_fp
            != record.source_binding.source_binding_semantic_fingerprint
        ):
            raise MemorySourceSetHashMismatch(
                "Memory source-binding semantic fingerprint mismatch"
            )
        expected_projection_hash = _sha256_ref(
            record.projection.projection_payload
        )
        if expected_projection_hash != record.projection.projection_payload_sha256:
            raise MemoryProjectionHashMismatch(
                "Memory projection payload hash mismatch"
            )
        pseudo_request = MemoryProjectionRequestSchema(
            run_id=record.memory_ref.run_id,
            memory_type=record.memory_ref.memory_type,
            scope_ref=record.memory_ref.scope_ref,
            primary_source_ref=record.source_binding.primary_source,
            dependency_source_refs=record.source_binding.dependency_sources,
            projection_schema_id=record.projection.projection_schema_id,
            projection_schema_version=record.projection.projection_schema_version,
            projection_policy_id=record.projection_policy_id,
            projection_policy_version=record.projection_policy_version,
        )
        expected_ref = self.derive_memory_ref(
            pseudo_request,
            record.source_binding,
        )
        if expected_ref != record.memory_ref:
            raise MemoryIdentityDerivationMismatch(
                "DurableMemoryRef derivation mismatch"
            )
        expected_operation = self.projection_operation(record.memory_ref)
        operation = record.committed_operation_ref
        if (
            operation.logical_operation_id
            != expected_operation.logical_operation_id
            or operation.operation_kind != expected_operation.operation_kind
            or operation.run_id != expected_operation.run_id
            or operation.operation_scope_ref
            != expected_operation.operation_scope_ref
        ):
            raise MemoryRecordIntegrityInvalid(
                "Memory committed operation derivation mismatch"
            )
        expected_record_fp = self.record_semantic_fingerprint(
            memory_ref=record.memory_ref,
            source_binding=record.source_binding,
            projection=record.projection,
            projection_policy_id=record.projection_policy_id,
            projection_policy_version=record.projection_policy_version,
        )
        if expected_record_fp != record.record_semantic_fingerprint:
            raise MemoryRecordIntegrityInvalid(
                "DurableMemoryRecord semantic fingerprint mismatch"
            )
        registration = self._projection_registration(pseudo_request)
        if (
            registration.currentness_resolver_id
            != record.source_binding.currentness_resolver_id
            or registration.currentness_policy_version
            != record.source_binding.currentness_policy_version
        ):
            raise MemoryRecordIntegrityInvalid(
                "persisted Memory currentness contract version is not registered"
            )
        try:
            registration.projection_validator(
                record.projection.projection_payload
            )
        except Exception as exc:
            raise MemoryProjectionSchemaInvalid(
                "persisted Memory projection no longer validates"
            ) from exc
        return record

    def project_source(
        self,
        request: MemoryProjectionRequestSchema,
    ) -> DurableMemoryRecordSchema:
        request = MemoryProjectionRequestSchema.model_validate(request)
        memory_ref, binding, projection, registration = self._build_projection(
            request
        )
        operation = self.projection_operation(memory_ref)
        fingerprint_ref = self.record_semantic_fingerprint(
            memory_ref=memory_ref,
            source_binding=binding,
            projection=projection,
            projection_policy_id=request.projection_policy_id,
            projection_policy_version=request.projection_policy_version,
        )
        fingerprint = fingerprint_ref.removeprefix("sha256:")

        resolution = self.store.resolve_memory_record(
            operation,
            fingerprint,
        )
        if resolution.resolution is CommittedResultResolutionStatus.COMMITTED_SAME:
            existing = self.store.get_memory_record(memory_ref)
            if existing is None:
                raise MemoryRecordIntegrityInvalid(
                    "Memory resolver says committed but record is missing"
                )
            return self._validate_record_integrity(existing)
        if resolution.resolution is CommittedResultResolutionStatus.COMMITTED_CONFLICT:
            raise MemoryOperationConflict(
                "MEMORY_PROJECTION_COMMIT resolved conflicting fingerprint"
            )
        if resolution.resolution is CommittedResultResolutionStatus.INTEGRITY_INVALID:
            raise MemoryRecordIntegrityInvalid(
                "MEMORY_PROJECTION_COMMIT canonical result is invalid"
            )
        if resolution.resolution is not CommittedResultResolutionStatus.ABSENT:
            raise DurableMemoryRuntimeError(
                "unexpected Memory committed-result resolution"
            )

        # Precommit source-first revalidation. A source need not still be active,
        # but it must remain committed/resolvable/integrity-valid.
        rebound, _primary, _deps = self._resolve_projection_sources(
            request,
            registration,
            require_resolvable=True,
        )
        if rebound != binding:
            raise MemoryOperationConflict(
                "Memory source binding changed before canonical commit"
            )
        record = DurableMemoryRecordSchema(
            memory_ref=memory_ref,
            source_binding=binding,
            projection=projection,
            projection_policy_id=request.projection_policy_id,
            projection_policy_version=request.projection_policy_version,
            committed_operation_ref=_logical_operation_schema(operation),
            record_semantic_fingerprint=fingerprint_ref,
            created_at=self.clock(),
        )
        try:
            committed = self.store.put_memory_record_if_absent_or_same(record)
        except MemoryStoreConflict as exc:
            raise MemoryOperationConflict(str(exc)) from exc
        except MemoryStoreIntegrityError as exc:
            raise MemoryRecordIntegrityInvalid(str(exc)) from exc
        committed = self._validate_record_integrity(committed)

        # Index is rebuildable/noncanonical. A failed index write cannot roll
        # back the already committed DurableMemoryRecord.
        if committed.memory_ref.memory_type in self._selector_registrations:
            try:
                self.upsert_selector_index(committed)
            except Exception as exc:
                raise MemoryIndexProjectionError(
                    committed.memory_ref,
                    "Memory committed but selector-index projection failed",
                ) from exc
        return committed

    def resolve_record(
        self,
        memory_ref: DurableMemoryRefSchema,
    ) -> DurableMemoryRecordSchema | None:
        ref = DurableMemoryRefSchema.model_validate(memory_ref)
        record = self.store.get_memory_record(ref)
        if record is None:
            return None
        return self._validate_record_integrity(record)

    # ------------------------------------------------------------------
    # Freshness
    # ------------------------------------------------------------------

    def assess_freshness(
        self,
        memory_record: DurableMemoryRecordSchema,
    ) -> MemoryFreshnessAssessmentSchema:
        record = self._validate_record_integrity(memory_record)
        sources = (
            record.source_binding.primary_source,
            *record.source_binding.dependency_sources,
        )
        observations = tuple(
            self._resolve_source(source, require_resolvable=False)
            for source in sources
        )
        primary = observations[0]
        dependencies = observations[1:]

        source_resolvable = all(item.source_resolvable for item in observations)
        integrity_valid = all(item.integrity_valid for item in observations)
        dependency_current: bool | str = (
            "NOT_APPLICABLE"
            if not dependencies
            else all(item.current for item in dependencies)
        )
        lifecycle_values = [
            item.lifecycle_active
            for item in observations
            if item.lifecycle_active is not None
        ]
        lifecycle_eligible: bool | str = (
            "NOT_APPLICABLE"
            if not lifecycle_values
            else all(lifecycle_values)
        )

        domain_chain_coherent = True
        if source_resolvable and integrity_valid:
            pseudo_request = MemoryProjectionRequestSchema(
                run_id=record.memory_ref.run_id,
                memory_type=record.memory_ref.memory_type,
                scope_ref=record.memory_ref.scope_ref,
                primary_source_ref=record.source_binding.primary_source,
                dependency_source_refs=record.source_binding.dependency_sources,
                projection_schema_id=record.projection.projection_schema_id,
                projection_schema_version=record.projection.projection_schema_version,
                projection_policy_id=record.projection_policy_id,
                projection_policy_version=record.projection_policy_version,
            )
            registration = self._projection_registration(pseudo_request)
            try:
                registration.source_binding_validator(
                    pseudo_request,
                    primary,
                    dependencies,
                )
            except Exception:
                domain_chain_coherent = False

        active = bool(
            source_resolvable
            and integrity_valid
            and domain_chain_coherent
            and primary.current
            and dependency_current is not False
            and lifecycle_eligible is not False
        )
        historical = bool(
            source_resolvable
            and integrity_valid
            and domain_chain_coherent
            and all(item.historical_eligible for item in observations)
        )
        if active:
            eligibility = MemoryFreshnessEligibility.ACTIVE_RECALL
        elif historical:
            eligibility = MemoryFreshnessEligibility.HISTORICAL_ONLY
        else:
            eligibility = MemoryFreshnessEligibility.INELIGIBLE

        reasons: list[str] = []
        for item in observations:
            for reason in item.reason_codes:
                normalized = str(reason or "").strip()
                if normalized and normalized not in reasons:
                    reasons.append(normalized)
        if (
            not domain_chain_coherent
            and "DOMAIN_SOURCE_CHAIN_INCOHERENT" not in reasons
        ):
            reasons.append("DOMAIN_SOURCE_CHAIN_INCOHERENT")
        if not reasons:
            reasons.append(
                "SOURCE_CURRENT"
                if eligibility is MemoryFreshnessEligibility.ACTIVE_RECALL
                else "SOURCE_SUPERSEDED"
                if eligibility is MemoryFreshnessEligibility.HISTORICAL_ONLY
                else "SOURCE_UNRESOLVABLE"
            )
        return MemoryFreshnessAssessmentSchema(
            memory_ref=record.memory_ref,
            assessed_source_set_sha256=record.source_binding.source_set_sha256,
            eligibility=eligibility,
            primary_source_current=primary.current,
            dependency_sources_current=dependency_current,
            lifecycle_eligible=lifecycle_eligible,
            source_resolvable=source_resolvable,
            integrity_valid=integrity_valid,
            assessment_reason_codes=tuple(reasons),
            assessed_with_resolver_id=record.source_binding.currentness_resolver_id,
            assessed_with_policy_version=record.source_binding.currentness_policy_version,
            assessed_at=self.clock(),
        )

    # ------------------------------------------------------------------
    # Selector index
    # ------------------------------------------------------------------

    def _selector_registration(
        self,
        memory_type: DomainMemoryType,
    ) -> MemorySelectorRegistration:
        registration = self._selector_registrations.get(memory_type)
        if registration is None:
            raise MemorySelectorSchemaInvalid(
                f"selector schema not registered for {memory_type.value}"
            )
        return registration

    @staticmethod
    def _index_fingerprint(
        *,
        record: DurableMemoryRecordSchema,
        registration: MemorySelectorRegistration,
        selector_values_sha256: str,
    ) -> str:
        return _sha256_ref(
            {
                "memory_ref": record.memory_ref.model_dump(mode="json"),
                "source_memory_fingerprint": record.record_semantic_fingerprint,
                "selector_schema_id": registration.selector_schema_id,
                "selector_schema_version": registration.selector_schema_version,
                "selector_values_sha256": selector_values_sha256,
                "index_policy_id": registration.index_policy_id,
                "index_policy_version": registration.index_policy_version,
            }
        )

    def upsert_selector_index(
        self,
        memory_record: DurableMemoryRecordSchema,
    ) -> MemorySelectorIndexRecordSchema:
        record = self._validate_record_integrity(memory_record)
        registration = self._selector_registration(
            record.memory_ref.memory_type
        )
        try:
            values = dict(registration.selector_projector(record))
            registration.selector_validator(values)
        except Exception as exc:
            raise MemorySelectorSchemaInvalid(
                "Memory selector projection failed registered schema"
            ) from exc
        unknown = set(values) - registration.allowed_keys
        if unknown:
            raise MemorySelectorSchemaInvalid(
                "Memory selector values contain unregistered keys: "
                + ", ".join(sorted(unknown))
            )
        values_hash = _sha256_ref(values)
        index = MemorySelectorIndexRecordSchema(
            memory_ref=record.memory_ref,
            source_memory_fingerprint=record.record_semantic_fingerprint,
            selector_schema_id=registration.selector_schema_id,
            selector_schema_version=registration.selector_schema_version,
            selector_values=values,
            selector_values_sha256=values_hash,
            index_policy_id=registration.index_policy_id,
            index_policy_version=registration.index_policy_version,
            index_record_fingerprint=self._index_fingerprint(
                record=record,
                registration=registration,
                selector_values_sha256=values_hash,
            ),
            built_at=self.clock(),
        )
        return self.store.upsert_selector_index(index)

    def _validate_index(
        self,
        index: MemorySelectorIndexRecordSchema,
        record: DurableMemoryRecordSchema,
    ) -> MemorySelectorIndexRecordSchema:
        index = MemorySelectorIndexRecordSchema.model_validate(index)
        if index.memory_ref != record.memory_ref:
            raise MemorySelectorSchemaInvalid(
                "SelectorIndex references different MemoryRecord"
            )
        if index.source_memory_fingerprint != record.record_semantic_fingerprint:
            raise MemorySelectorSchemaInvalid(
                "SelectorIndex source Memory fingerprint is stale"
            )
        registration = self._selector_registration(
            record.memory_ref.memory_type
        )
        if (
            index.selector_schema_id != registration.selector_schema_id
            or index.selector_schema_version
            != registration.selector_schema_version
            or index.index_policy_id != registration.index_policy_id
            or index.index_policy_version != registration.index_policy_version
        ):
            raise MemorySelectorSchemaInvalid(
                "SelectorIndex contract version is stale"
            )
        if _sha256_ref(index.selector_values) != index.selector_values_sha256:
            raise MemorySelectorSchemaInvalid(
                "SelectorIndex selector_values hash mismatch"
            )
        expected = self._index_fingerprint(
            record=record,
            registration=registration,
            selector_values_sha256=index.selector_values_sha256,
        )
        if expected != index.index_record_fingerprint:
            raise MemorySelectorSchemaInvalid(
                "SelectorIndex record fingerprint mismatch"
            )
        try:
            registration.selector_validator(index.selector_values)
        except Exception as exc:
            raise MemorySelectorSchemaInvalid(
                "SelectorIndex values fail registered schema"
            ) from exc
        return index

    def rebuild_selector_index(
        self,
        *,
        run_id: str,
        limit: int = 200,
    ) -> tuple[DurableMemoryRefSchema, ...]:
        normalized = str(run_id or "").strip()
        if not normalized:
            raise ValueError("run_id must be non-empty")
        self.store.delete_all_selector_index_for_run(normalized)
        rebuilt: list[DurableMemoryRefSchema] = []
        after: str | None = None
        scanned = 0
        while scanned < limit:
            page_limit = min(100, limit - scanned)
            page = self.store.enumerate_memory_records_for_reconcile(
                run_id=normalized,
                limit=page_limit,
                after_memory_id=after,
            )
            if not page:
                break
            scanned += len(page)
            for raw in page:
                record = self._validate_record_integrity(raw)
                if record.memory_ref.memory_type in self._selector_registrations:
                    self.upsert_selector_index(record)
                    rebuilt.append(record.memory_ref)
            after = page[-1].memory_ref.memory_id
            if len(page) < page_limit:
                break
        return tuple(rebuilt)

    # ------------------------------------------------------------------
    # Exact typed query / pagination
    # ------------------------------------------------------------------

    @staticmethod
    def normalized_query_fingerprint(query: MemoryQuerySchema) -> str:
        query = MemoryQuerySchema.model_validate(query)
        return _sha256_ref(
            {
                "contract": "memory_query_v1",
                "run_id": query.run_id,
                "scope_refs": query.scope_refs,
                "memory_types": [
                    item.value for item in query.memory_types
                ],
                "recall_mode": query.recall_mode.value,
                "source_kinds": query.source_kinds,
                "exact_source_refs": query.exact_source_refs,
                "selector_clauses": [
                    item.model_dump(mode="json")
                    for item in query.selector_clauses
                ],
                "query_purpose": query.query_purpose.value,
            }
        )

    def _query_index_contracts(
        self,
        query: MemoryQuerySchema,
    ) -> tuple[tuple[str, str, str, str, str], ...]:
        if not query.selector_clauses:
            return ()
        referenced = {
            (clause.selector_schema_id, clause.selector_schema_version)
            for clause in query.selector_clauses
        }
        types = (
            set(query.memory_types)
            if query.memory_types
            else set(self._selector_registrations)
        )
        contracts = []
        for memory_type in sorted(types, key=lambda item: item.value):
            registration = self._selector_registrations.get(memory_type)
            if registration is None:
                continue
            if (
                registration.selector_schema_id,
                registration.selector_schema_version,
            ) not in referenced:
                continue
            contracts.append(
                (
                    memory_type.value,
                    registration.selector_schema_id,
                    registration.selector_schema_version,
                    registration.index_policy_id,
                    registration.index_policy_version,
                )
            )
        return tuple(contracts)

    @staticmethod
    def _encode_cursor(
        *,
        run_id: str,
        query_fingerprint: str,
        last_key: tuple[str, str, str],
        index_contracts: tuple[tuple[str, str, str, str, str], ...],
    ) -> str:
        normalized_run_id = str(run_id or "").strip()
        if not normalized_run_id:
            raise MemoryCursorInvalid("Memory query cursor requires run_id")
        payload = {
            "schema_version": "memory_query_cursor_v1",
            "run_id": normalized_run_id,
            "normalized_query_fingerprint": query_fingerprint,
            "last_key": list(last_key),
            "index_contracts": [list(item) for item in index_contracts],
        }
        encoded = base64.urlsafe_b64encode(
            json.dumps(
                payload,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            ).encode("utf-8")
        ).decode("ascii")
        return encoded.rstrip("=")

    @staticmethod
    def _decode_cursor(
        cursor: str,
    ) -> dict[str, Any]:
        normalized = str(cursor or "").strip()
        if not normalized:
            raise MemoryCursorInvalid("Memory query cursor cannot be blank")
        try:
            padding = "=" * (-len(normalized) % 4)
            payload = json.loads(
                base64.urlsafe_b64decode(
                    (normalized + padding).encode("ascii")
                ).decode("utf-8")
            )
        except Exception as exc:
            raise MemoryCursorInvalid("Memory query cursor is malformed") from exc
        if (
            not isinstance(payload, dict)
            or payload.get("schema_version") != "memory_query_cursor_v1"
        ):
            raise MemoryCursorInvalid("unsupported Memory query cursor")
        return payload

    def _validate_selector_clauses(
        self,
        query: MemoryQuerySchema,
    ) -> None:
        registered_memory_types = {
            key[0] for key in self._projection_registrations
        }
        for memory_type in query.memory_types:
            if memory_type not in registered_memory_types:
                raise MemoryTypeUnregistered(
                    f"Memory query references unregistered type: {memory_type.value}"
                )

        requested_types = set(query.memory_types)
        for clause in query.selector_clauses:
            matching = [
                registration
                for registration in self._selector_registrations.values()
                if (
                    registration.selector_schema_id == clause.selector_schema_id
                    and registration.selector_schema_version
                    == clause.selector_schema_version
                    and (
                        not requested_types
                        or registration.memory_type in requested_types
                    )
                )
            ]
            if not matching:
                raise MemorySelectorSchemaInvalid(
                    "Memory query references unknown/inapplicable selector schema"
                )
            applicable = [
                item for item in matching if clause.key in item.allowed_keys
            ]
            if not applicable:
                raise MemorySelectorSchemaInvalid(
                    f"Memory query selector key is not registered: {clause.key}"
                )
            if clause.operator is MemorySelectorOperator.IN and not isinstance(
                clause.value,
                (list, tuple),
            ):
                raise MemorySelectorSchemaInvalid(
                    "Memory selector IN requires list/tuple value"
                )
            last_error: Exception | None = None
            accepted = False
            for registration in applicable:
                try:
                    registration.selector_clause_validator(clause)
                except Exception as exc:
                    last_error = exc
                    continue
                accepted = True
                break
            if not accepted:
                raise MemorySelectorSchemaInvalid(
                    "Memory selector clause fails registered type/canonicalization rules"
                ) from last_error

        if query.selector_clauses and not self._selector_candidate_memory_types(query):
            raise MemorySelectorSchemaInvalid(
                "Memory selector clauses have no common registered Memory type"
            )

    def _selector_candidate_memory_types(
        self,
        query: MemoryQuerySchema,
    ) -> tuple[DomainMemoryType, ...]:
        if not query.selector_clauses:
            return query.memory_types
        requested = set(query.memory_types)
        candidates = []
        for memory_type, registration in self._selector_registrations.items():
            if requested and memory_type not in requested:
                continue
            if all(
                registration.selector_schema_id == clause.selector_schema_id
                and registration.selector_schema_version
                == clause.selector_schema_version
                for clause in query.selector_clauses
            ):
                candidates.append(memory_type)
        return tuple(sorted(candidates, key=lambda item: item.value))

    @staticmethod
    def _selector_clause_matches(
        index: MemorySelectorIndexRecordSchema,
        clause: TypedMemorySelectorClauseSchema,
    ) -> bool:
        if (
            index.selector_schema_id != clause.selector_schema_id
            or index.selector_schema_version != clause.selector_schema_version
        ):
            return False
        values = index.selector_values
        if clause.key not in values:
            return False
        actual = values[clause.key]
        if clause.operator is MemorySelectorOperator.EQ:
            return actual == clause.value
        if clause.operator is MemorySelectorOperator.IN:
            return actual in tuple(clause.value)
        if clause.operator is MemorySelectorOperator.CONTAINS_SET_MEMBER:
            if not isinstance(actual, (list, tuple, set, frozenset)):
                return False
            return clause.value in actual
        raise MemorySelectorSchemaInvalid("unsupported Memory selector operator")

    @staticmethod
    def _record_has_exact_source_ref(
        record: DurableMemoryRecordSchema,
        exact_ref: Mapping[str, Any],
    ) -> bool:
        target = _canonical_ref_key(exact_ref)
        sources = (
            record.source_binding.primary_source,
            *record.source_binding.dependency_sources,
        )
        return any(
            _canonical_ref_key(source.exact_source_ref) == target
            for source in sources
        )

    @staticmethod
    def _recall_allowed(
        mode: MemoryRecallMode,
        eligibility: MemoryFreshnessEligibility,
    ) -> bool:
        if eligibility is MemoryFreshnessEligibility.INELIGIBLE:
            return False
        if mode is MemoryRecallMode.ACTIVE_ONLY:
            return eligibility is MemoryFreshnessEligibility.ACTIVE_RECALL
        if mode is MemoryRecallMode.HISTORICAL_ONLY:
            return eligibility is MemoryFreshnessEligibility.HISTORICAL_ONLY
        return eligibility in {
            MemoryFreshnessEligibility.ACTIVE_RECALL,
            MemoryFreshnessEligibility.HISTORICAL_ONLY,
        }

    def query(
        self,
        query_schema: MemoryQuerySchema,
    ) -> MemoryQueryResultSchema:
        query = MemoryQuerySchema.model_validate(query_schema)
        self._validate_selector_clauses(query)
        query_fp = self.normalized_query_fingerprint(query)
        index_contracts = self._query_index_contracts(query)
        last_key: tuple[str, str, str] | None = None
        if query.cursor is not None:
            cursor = self._decode_cursor(query.cursor)
            if cursor.get("run_id") != query.run_id:
                raise MemoryCursorInvalid("Memory cursor run_id mismatch")
            if cursor.get("normalized_query_fingerprint") != query_fp:
                raise MemoryCursorInvalid(
                    "Memory cursor query fingerprint mismatch"
                )
            if tuple(tuple(item) for item in cursor.get("index_contracts", [])) != (
                index_contracts
            ):
                raise MemoryCursorInvalid(
                    "Memory cursor selector/index contract version mismatch"
                )
            raw_last = cursor.get("last_key")
            if (
                not isinstance(raw_last, list)
                or len(raw_last) != 3
                or any(not str(item).strip() for item in raw_last)
            ):
                raise MemoryCursorInvalid("Memory cursor structural key invalid")
            last_key = tuple(str(item) for item in raw_last)  # type: ignore[assignment]

        candidates: list[
            tuple[
                tuple[str, str, str],
                DurableMemoryRecordSchema,
                MemoryFreshnessAssessmentSchema,
            ]
        ] = []
        exact_ref_filters = tuple(query.exact_source_refs)
        source_kind_filters = set(query.source_kinds)
        batch_size = 200

        def _accept_record(
            record: DurableMemoryRecordSchema,
            *,
            index: MemorySelectorIndexRecordSchema | None = None,
        ) -> tuple[
            tuple[str, str, str],
            DurableMemoryRecordSchema,
            MemoryFreshnessAssessmentSchema,
        ] | None:
            record = self._validate_record_integrity(record)
            sources = (
                record.source_binding.primary_source,
                *record.source_binding.dependency_sources,
            )
            if source_kind_filters and not any(
                source.source_kind in source_kind_filters
                for source in sources
            ):
                return None
            if exact_ref_filters and not all(
                self._record_has_exact_source_ref(record, exact_ref)
                for exact_ref in exact_ref_filters
            ):
                return None
            if query.selector_clauses:
                if index is None:
                    raise MemorySelectorSchemaInvalid(
                        "selector query requires a validated SelectorIndex record"
                    )
                if any(
                    not self._selector_clause_matches(index, clause)
                    for clause in query.selector_clauses
                ):
                    return None
            freshness = self.assess_freshness(record)
            if not self._recall_allowed(
                query.recall_mode,
                freshness.eligibility,
            ):
                return None
            key = (
                record.memory_ref.memory_type.value,
                record.memory_ref.scope_ref,
                record.memory_ref.memory_id,
            )
            return key, record, freshness

        if query.selector_clauses:
            selector_types = self._selector_candidate_memory_types(query)
            selector_type_values = tuple(item.value for item in selector_types)
            record_count = self.store.count_memory_records_exact(
                run_id=query.run_id,
                scope_refs=query.scope_refs,
                memory_types=selector_type_values,
            )
            index_count = self.store.count_selector_index(
                run_id=query.run_id,
                scope_refs=query.scope_refs,
                memory_types=selector_type_values,
            )
            fallback_required = record_count != index_count

            if not fallback_required:
                index_after = last_key
                while len(candidates) <= query.limit:
                    try:
                        indices = self.store.query_selector_index(
                            run_id=query.run_id,
                            scope_refs=query.scope_refs,
                            memory_types=selector_type_values,
                            after_key=index_after,
                            limit=batch_size,
                        )
                    except MemoryStoreIntegrityError:
                        fallback_required = True
                        break
                    if not indices:
                        break
                    exhausted = len(indices) < batch_size
                    for index in indices:
                        key = (
                            index.memory_ref.memory_type.value,
                            index.memory_ref.scope_ref,
                            index.memory_ref.memory_id,
                        )
                        index_after = key
                        record = self.resolve_record(index.memory_ref)
                        if record is None:
                            fallback_required = True
                            break
                        try:
                            index = self._validate_index(index, record)
                        except MemorySelectorSchemaInvalid:
                            fallback_required = True
                            break
                        accepted = _accept_record(record, index=index)
                        if accepted is not None:
                            candidates.append(accepted)
                            if len(candidates) > query.limit:
                                break
                    if fallback_required or len(candidates) > query.limit or exhausted:
                        break

            if fallback_required:
                candidates = []
                selector_scan_limit = self.SELECTOR_FALLBACK_SCAN_LIMIT
                if selector_scan_limit <= 0:
                    raise DurableMemoryRuntimeError(
                        "selector fallback scan limit must be positive"
                    )
                scan_after = last_key
                scanned = 0
                while len(candidates) <= query.limit:
                    remaining_scan = selector_scan_limit - scanned
                    if remaining_scan <= 0:
                        raise MemorySelectorSchemaInvalid(
                            "bounded selector fallback exhausted; "
                            "INDEX_REBUILD_REQUIRED"
                        )
                    page_limit = min(batch_size, remaining_scan)
                    records = self.store.query_memory_records_exact(
                        run_id=query.run_id,
                        scope_refs=query.scope_refs,
                        memory_types=selector_type_values,
                        after_key=scan_after,
                        limit=page_limit,
                    )
                    if not records:
                        break
                    scanned += len(records)
                    exhausted = len(records) < page_limit
                    for raw in records:
                        record = self._validate_record_integrity(raw)
                        key = (
                            record.memory_ref.memory_type.value,
                            record.memory_ref.scope_ref,
                            record.memory_ref.memory_id,
                        )
                        scan_after = key

                        sources = (
                            record.source_binding.primary_source,
                            *record.source_binding.dependency_sources,
                        )
                        if source_kind_filters and not any(
                            source.source_kind in source_kind_filters
                            for source in sources
                        ):
                            continue
                        if exact_ref_filters and not all(
                            self._record_has_exact_source_ref(record, exact_ref)
                            for exact_ref in exact_ref_filters
                        ):
                            continue

                        try:
                            index = self.store.get_selector_index(record.memory_ref)
                        except MemoryStoreIntegrityError:
                            index = None
                        if index is not None:
                            try:
                                index = self._validate_index(index, record)
                            except MemorySelectorSchemaInvalid:
                                index = None
                        if index is None:
                            index = self.upsert_selector_index(record)

                        accepted = _accept_record(record, index=index)
                        if accepted is not None:
                            candidates.append(accepted)
                            if len(candidates) > query.limit:
                                break

                    if len(candidates) > query.limit or exhausted:
                        break
                    if (
                        scanned >= selector_scan_limit
                        and len(candidates) <= query.limit
                    ):
                        raise MemorySelectorSchemaInvalid(
                            "bounded selector fallback exhausted; "
                            "INDEX_REBUILD_REQUIRED"
                        )
        else:
            scan_after = last_key
            while len(candidates) <= query.limit:
                records = self.store.query_memory_records_exact(
                    run_id=query.run_id,
                    scope_refs=query.scope_refs,
                    memory_types=tuple(item.value for item in query.memory_types),
                    after_key=scan_after,
                    limit=batch_size,
                )
                if not records:
                    break
                exhausted = len(records) < batch_size
                for raw in records:
                    record = self._validate_record_integrity(raw)
                    key = (
                        record.memory_ref.memory_type.value,
                        record.memory_ref.scope_ref,
                        record.memory_ref.memory_id,
                    )
                    scan_after = key
                    accepted = _accept_record(record)
                    if accepted is not None:
                        candidates.append(accepted)
                        if len(candidates) > query.limit:
                            break
                if len(candidates) > query.limit or exhausted:
                    break

        candidates.sort(key=lambda item: item[0])
        page = candidates[: query.limit]
        items = tuple(
            MemoryQueryResultItemSchema(
                memory_record=record,
                freshness_assessment=freshness,
            )
            for _key, record, freshness in page
        )
        next_cursor = None
        if len(candidates) > query.limit and page:
            next_cursor = self._encode_cursor(
                run_id=query.run_id,
                query_fingerprint=query_fp,
                last_key=page[-1][0],
                index_contracts=index_contracts,
            )
        return MemoryQueryResultSchema(
            run_id=query.run_id,
            normalized_query_fingerprint=query_fp,
            items=items,
            next_cursor=next_cursor,
        )

    # ------------------------------------------------------------------
    # Source-first reconciliation
    # ------------------------------------------------------------------

    def reconcile_source(
        self,
        memory_ref: DurableMemoryRefSchema,
    ) -> MemoryReconcileResult:
        record = self.resolve_record(memory_ref)
        if record is None:
            raise DurableMemoryRuntimeError(
                "reconcile_source cannot reconstruct source from absent MemoryRef; "
                "use project_source with canonical source request"
            )
        freshness = self.assess_freshness(record)
        index_rebuilt = False
        if record.memory_ref.memory_type in self._selector_registrations:
            index = self.store.get_selector_index(record.memory_ref)
            try:
                if index is None:
                    raise MemorySelectorSchemaInvalid("selector index missing")
                self._validate_index(index, record)
            except MemorySelectorSchemaInvalid:
                self.upsert_selector_index(record)
                index_rebuilt = True
        return MemoryReconcileResult(
            memory_ref=record.memory_ref,
            freshness=freshness,
            selector_rebuilt=index_rebuilt,
        )


__all__ = [
    "MEMORY_PROJECTION_COMMIT",
    "DurableMemoryRuntime",
    "DurableMemoryRuntimeError",
    "MemoryCursorInvalid",
    "MemoryIdentityDerivationMismatch",
    "MemoryIndexProjectionError",
    "MemoryOperationConflict",
    "MemoryProjectionRegistration",
    "MemoryProjectionSchemaInvalid",
    "MemoryQueryPurpose",
    "MemoryReconcileResult",
    "MemoryRecordIntegrityInvalid",
    "MemorySelectorRegistration",
    "MemorySelectorSchemaInvalid",
    "MemorySourceHashMismatch",
    "MemorySourceRefInvalid",
    "MemorySourceResolution",
    "MemorySourceScopeMismatch",
    "MemorySourceSetHashMismatch",
    "MemoryTypeUnregistered",
]
