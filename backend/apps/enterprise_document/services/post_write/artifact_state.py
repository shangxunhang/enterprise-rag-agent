"""Post-write ArtifactState structural service.

This service implements SECTION/DOCUMENT persistence and expected-parent CAS using
the shared Post-write SQLite. It does not create a second generic Artifact authority.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable

from apps.enterprise_document.schemas.post_write.foundation import (
    ArtifactOriginSchema,
    ArtifactOutputConformanceProofSchema,
    ArtifactScope,
    ArtifactStateRecordSchema,
    ArtifactStateRefSchema,
    ArtifactValidatedOutputBindingSchema,
    ArtifactVisibleProjectionRefSchema,
    ArtifactVisibleProjectionSchema,
    OutputConformanceState,
)
from apps.enterprise_document.services.agent_rag_v1.artifact_store import (
    FileArtifactStore,
)
from core.hashing import canonical_sha256

from .persistence import (
    ArtifactStateStore,
    PostWritePersistenceIntegrityError,
)


class ArtifactStateIntegrityError(PostWritePersistenceIntegrityError):
    pass


class ArtifactStaleParentError(ArtifactStateIntegrityError):
    pass


class ArtifactCanonicalPayloadMaterializer:
    _TECHNICAL_SEMANTIC_KEYS = frozenset(
        {"visible_projection_sha256", "validated_output_semantics"}
    )

    @classmethod
    def business_payload(
        cls,
        canonical_payload_ref: dict[str, Any],
    ) -> dict[str, Any]:
        payload = dict(canonical_payload_ref)
        for key in cls._TECHNICAL_SEMANTIC_KEYS:
            payload.pop(key, None)
        return payload

    @staticmethod
    def with_visible_projection_semantics(
        canonical_payload_ref: dict[str, Any],
        visible_projection_ref: ArtifactVisibleProjectionRefSchema | None,
    ) -> dict[str, Any]:
        payload = dict(canonical_payload_ref)
        if visible_projection_ref is None:
            return payload
        projection_ref = ArtifactVisibleProjectionRefSchema.model_validate(
            visible_projection_ref
        )
        existing = payload.get("visible_projection_sha256")
        if (
            existing is not None
            and existing != projection_ref.projection_sha256
        ):
            raise ArtifactStateIntegrityError(
                "caller-provided visible_projection_sha256 contradicts exact projection"
            )
        payload["visible_projection_sha256"] = (
            projection_ref.projection_sha256
        )
        return payload

    @staticmethod
    def with_validated_output_semantics(
        canonical_payload_ref: dict[str, Any],
        validated_output_bindings: tuple[ArtifactValidatedOutputBindingSchema, ...],
    ) -> dict[str, Any]:
        payload = dict(canonical_payload_ref)
        if not validated_output_bindings:
            return payload
        bindings = tuple(
            sorted(
                (
                    ArtifactValidatedOutputBindingSchema.model_validate(item)
                    for item in validated_output_bindings
                ),
                key=lambda item: (item.output_format, item.binding_sha256),
            )
        )
        semantics = [
            {
                "output_format": item.output_format,
                "renderer_contract_version": item.renderer_contract_version,
                "output_payload_sha256": item.output_payload_sha256,
            }
            for item in bindings
        ]
        existing = payload.get("validated_output_semantics")
        if existing is not None and existing != semantics:
            raise ArtifactStateIntegrityError(
                "caller-provided validated_output_semantics contradict exact bindings"
            )
        payload["validated_output_semantics"] = semantics
        return payload

    @staticmethod
    def artifact_hash(
        *,
        artifact_scope: ArtifactScope,
        canonical_payload_ref: dict[str, Any],
    ) -> str:
        return canonical_sha256(
            {
                "artifact_scope": artifact_scope.value,
                "canonical_payload_ref": canonical_payload_ref,
            }
        )

    @staticmethod
    def record_hash(record_without_hash: dict[str, Any]) -> str:
        return canonical_sha256(record_without_hash)

    def materialize(
        self,
        *,
        artifact_id: str,
        artifact_version: int,
        artifact_scope: ArtifactScope,
        canonical_payload_ref: dict[str, Any],
        origin: ArtifactOriginSchema,
        parent_artifact_ref: ArtifactStateRefSchema | None = None,
        rendered_output_ref: dict[str, Any] | None = None,
        visible_projection_ref: ArtifactVisibleProjectionRefSchema | None = None,
        visible_projection: ArtifactVisibleProjectionSchema | None = None,
        validated_output_bindings: tuple[ArtifactValidatedOutputBindingSchema, ...] = (),
        output_conformance_proofs: tuple[ArtifactOutputConformanceProofSchema, ...] = (),
    ) -> ArtifactStateRecordSchema:
        business_payload = self.business_payload(canonical_payload_ref)
        if visible_projection is None:
            auto_payload = {
                "schema_version": "artifact_visible_projection_v1",
                "projection_contract_version": (
                    "artifact_visible_projection_v1"
                ),
                "artifact_scope": artifact_scope.value,
                "logical_target_ref": artifact_id,
                "visible_payload": business_payload,
            }
            auto_sha256 = canonical_sha256(auto_payload)
            visible_projection = ArtifactVisibleProjectionSchema(
                projection_contract_version=(
                    "artifact_visible_projection_v1"
                ),
                projection_id=(
                    "artifact-visible://sha256/" + auto_sha256
                ),
                artifact_scope=artifact_scope,
                logical_target_ref=artifact_id,
                visible_payload=business_payload,
                projection_sha256=auto_sha256,
            )
            visible_projection_ref = visible_projection.as_ref()
        bindings = tuple(
            sorted(
                (
                    ArtifactValidatedOutputBindingSchema.model_validate(item)
                    for item in validated_output_bindings
                ),
                key=lambda item: (item.output_format, item.binding_sha256),
            )
        )
        if visible_projection is not None:
            exact_projection = ArtifactVisibleProjectionSchema.model_validate(
                visible_projection
            )
            if visible_projection_ref is None:
                visible_projection_ref = exact_projection.as_ref()
            elif visible_projection_ref != exact_projection.as_ref():
                raise ArtifactStateIntegrityError(
                    "visible projection value/ref mismatch before Artifact commit"
                )
        canonical_payload_ref = self.with_visible_projection_semantics(
            business_payload,
            visible_projection_ref,
        )
        canonical_payload_ref = self.with_validated_output_semantics(
            canonical_payload_ref,
            bindings,
        )
        artifact_ref = ArtifactStateRefSchema(
            artifact_id=artifact_id,
            artifact_version=artifact_version,
            artifact_hash=self.artifact_hash(
                artifact_scope=artifact_scope,
                canonical_payload_ref=canonical_payload_ref,
            ),
            artifact_scope=artifact_scope,
        )
        payload = {
            "schema_version": "artifact_state_record_v1",
            "artifact_ref": artifact_ref.model_dump(mode="json"),
            "parent_artifact_ref": (
                parent_artifact_ref.model_dump(mode="json")
                if parent_artifact_ref is not None
                else None
            ),
            "canonical_payload_ref": canonical_payload_ref,
            "rendered_output_ref": rendered_output_ref,
            "visible_projection_ref": (
                visible_projection_ref.model_dump(mode="json")
                if visible_projection_ref is not None
                else None
            ),
            "visible_projection": (
                visible_projection.model_dump(mode="json")
                if visible_projection is not None
                else None
            ),
            "validated_output_bindings": [
                item.model_dump(mode="json") for item in bindings
            ],
            "output_conformance_proofs": [
                item.model_dump(mode="json") for item in output_conformance_proofs
            ],
            "origin": origin.model_dump(mode="json"),
        }
        return ArtifactStateRecordSchema(
            artifact_ref=artifact_ref,
            parent_artifact_ref=parent_artifact_ref,
            canonical_payload_ref=canonical_payload_ref,
            rendered_output_ref=rendered_output_ref,
            visible_projection_ref=visible_projection_ref,
            visible_projection=visible_projection,
            validated_output_bindings=bindings,
            output_conformance_proofs=tuple(output_conformance_proofs),
            origin=origin,
            record_hash=self.record_hash(payload),
        )


class ArtifactStateService:
    def __init__(
        self,
        *,
        store: ArtifactStateStore,
        materializer: ArtifactCanonicalPayloadMaterializer | None = None,
    ) -> None:
        self.store = store
        self.materializer = materializer or ArtifactCanonicalPayloadMaterializer()

    def create_initial(
        self,
        *,
        artifact_id: str,
        artifact_scope: ArtifactScope,
        canonical_payload_ref: dict[str, Any],
        origin: ArtifactOriginSchema,
        rendered_output_ref: dict[str, Any] | None = None,
        visible_projection_ref: ArtifactVisibleProjectionRefSchema | None = None,
        visible_projection: ArtifactVisibleProjectionSchema | None = None,
        validated_output_bindings: tuple[ArtifactValidatedOutputBindingSchema, ...] = (),
        output_conformance_proofs: tuple[ArtifactOutputConformanceProofSchema, ...] = (),
    ) -> ArtifactStateRefSchema:
        record = self.materializer.materialize(
            artifact_id=artifact_id,
            artifact_version=1,
            artifact_scope=artifact_scope,
            canonical_payload_ref=canonical_payload_ref,
            origin=origin,
            rendered_output_ref=rendered_output_ref,
            visible_projection_ref=visible_projection_ref,
            visible_projection=visible_projection,
            validated_output_bindings=validated_output_bindings,
            output_conformance_proofs=output_conformance_proofs,
        )
        return self.store.create_initial_if_absent_or_same(record)

    def append_next_version(
        self,
        *,
        expected_parent_ref: ArtifactStateRefSchema,
        canonical_payload_ref: dict[str, Any],
        origin: ArtifactOriginSchema,
        rendered_output_ref: dict[str, Any] | None = None,
        visible_projection_ref: ArtifactVisibleProjectionRefSchema | None = None,
        visible_projection: ArtifactVisibleProjectionSchema | None = None,
        validated_output_bindings: tuple[ArtifactValidatedOutputBindingSchema, ...] = (),
        output_conformance_proofs: tuple[ArtifactOutputConformanceProofSchema, ...] = (),
    ) -> ArtifactStateRefSchema:
        record = self.materializer.materialize(
            artifact_id=expected_parent_ref.artifact_id,
            artifact_version=expected_parent_ref.artifact_version + 1,
            artifact_scope=expected_parent_ref.artifact_scope,
            canonical_payload_ref=canonical_payload_ref,
            origin=origin,
            parent_artifact_ref=expected_parent_ref,
            rendered_output_ref=rendered_output_ref,
            visible_projection_ref=visible_projection_ref,
            visible_projection=visible_projection,
            validated_output_bindings=validated_output_bindings,
            output_conformance_proofs=output_conformance_proofs,
        )
        return self.store.append_next_version(
            record,
            expected_parent_ref=expected_parent_ref,
        )

    def resolve_exact(self, ref: ArtifactStateRefSchema, *, connection=None) -> ArtifactStateRecordSchema:
        return self.store.resolve_exact(ref, connection=connection)


class ArtifactValidationViewResolver:
    """Exact Artifact-owned projection resolver used by later validators."""

    def __init__(self, service: ArtifactStateService) -> None:
        self.service = service

    @staticmethod
    def build_projection(
        *,
        artifact_scope: ArtifactScope,
        logical_target_ref: str,
        visible_payload: dict[str, Any],
        projection_contract_version: str = "artifact_visible_projection_v1",
    ) -> ArtifactVisibleProjectionSchema:
        payload = {
            "schema_version": "artifact_visible_projection_v1",
            "projection_contract_version": str(
                projection_contract_version
            ).strip(),
            "artifact_scope": artifact_scope.value,
            "logical_target_ref": str(logical_target_ref).strip(),
            "visible_payload": dict(visible_payload),
        }
        projection_sha256 = canonical_sha256(payload)
        return ArtifactVisibleProjectionSchema(
            projection_contract_version=payload[
                "projection_contract_version"
            ],
            projection_id="artifact-visible://sha256/" + projection_sha256,
            artifact_scope=artifact_scope,
            logical_target_ref=payload["logical_target_ref"],
            visible_payload=dict(visible_payload),
            projection_sha256=projection_sha256,
        )

    def resolve(
        self,
        ref: ArtifactStateRefSchema,
    ) -> ArtifactVisibleProjectionSchema:
        return self.resolve_projection(ref)

    def resolve_canonical_payload(
        self,
        ref: ArtifactStateRefSchema,
    ) -> dict[str, Any]:
        record = self.service.resolve_exact(ref)
        return dict(record.canonical_payload_ref)

    def resolve_projection(
        self,
        ref: ArtifactStateRefSchema,
        *, connection=None,
    ) -> ArtifactVisibleProjectionSchema:
        record = self.service.resolve_exact(ref, connection=connection)
        if (
            record.visible_projection is None
            or record.visible_projection_ref is None
        ):
            raise ArtifactStateIntegrityError(
                "Artifact has no exact visible projection"
            )
        if record.visible_projection.as_ref() != record.visible_projection_ref:
            raise ArtifactStateIntegrityError(
                "Artifact visible projection value/ref mismatch"
            )
        return record.visible_projection


class OutputConformanceTechnicalError(ArtifactStateIntegrityError):
    pass


OutputConformanceAdapter = Callable[[bytes], dict[str, Any]]


@dataclass(frozen=True)
class _OutputConformanceAdapterRegistration:
    output_format: str
    adapter_contract_version: str
    adapter: OutputConformanceAdapter


class OutputConformanceRegistry:
    def __init__(self) -> None:
        self._adapters: dict[str, _OutputConformanceAdapterRegistration] = {}

    def register(
        self,
        *,
        output_format: str,
        adapter_contract_version: str,
        adapter: OutputConformanceAdapter,
    ) -> None:
        normalized = str(output_format or "").strip()
        contract = str(adapter_contract_version or "").strip()
        if not normalized or not contract or not callable(adapter):
            raise ValueError("invalid output conformance adapter registration")
        incoming = _OutputConformanceAdapterRegistration(
            output_format=normalized,
            adapter_contract_version=contract,
            adapter=adapter,
        )
        existing = self._adapters.get(normalized)
        if existing is not None and existing != incoming:
            raise ValueError("output conformance adapter already registered")
        self._adapters[normalized] = incoming

    def resolve(
        self,
        output_format: str,
    ) -> _OutputConformanceAdapterRegistration:
        normalized = str(output_format or "").strip()
        registration = self._adapters.get(normalized)
        if registration is None:
            raise OutputConformanceTechnicalError(
                f"unsupported output conformance format: {normalized}"
            )
        return registration


class ArtifactOutputConformanceVerifier:
    """Deterministic output/projection integrity verifier."""

    def __init__(
        self,
        *,
        artifact_store: FileArtifactStore,
        registry: OutputConformanceRegistry,
        conformance_contract_version: str = "artifact_output_conformance_v1",
    ) -> None:
        self.artifact_store = artifact_store
        self.registry = registry
        self.conformance_contract_version = str(
            conformance_contract_version or ""
        ).strip()
        if not self.conformance_contract_version:
            raise ValueError("conformance_contract_version is required")

    @staticmethod
    def build_output_binding(
        *,
        output_format: str,
        renderer_contract_version: str,
        output_artifact_ref,
    ) -> ArtifactValidatedOutputBindingSchema:
        content_hash = str(output_artifact_ref.content_hash)
        if not content_hash.startswith("sha256:"):
            raise OutputConformanceTechnicalError(
                "output Artifact lacks sha256 content hash"
            )
        output_payload_sha256 = content_hash.removeprefix("sha256:")
        binding_sha256 = canonical_sha256(
            {
                "schema_version": "artifact_validated_output_binding_v1",
                "output_format": str(output_format).strip(),
                "renderer_contract_version": str(
                    renderer_contract_version
                ).strip(),
                "output_payload_sha256": output_payload_sha256,
            }
        )
        return ArtifactValidatedOutputBindingSchema(
            output_format=str(output_format).strip(),
            renderer_contract_version=str(
                renderer_contract_version
            ).strip(),
            output_artifact_ref=output_artifact_ref,
            output_payload_sha256=output_payload_sha256,
            binding_sha256=binding_sha256,
        )

    def prove_precommit(
        self,
        *,
        artifact_id: str,
        artifact_scope: ArtifactScope,
        canonical_payload_ref: dict[str, Any],
        visible_projection: ArtifactVisibleProjectionSchema,
        output_binding: ArtifactValidatedOutputBindingSchema,
    ) -> ArtifactOutputConformanceProofSchema:
        import hashlib

        projection = ArtifactVisibleProjectionSchema.model_validate(
            visible_projection
        )
        binding = ArtifactValidatedOutputBindingSchema.model_validate(
            output_binding
        )
        if projection.artifact_scope is not artifact_scope:
            raise OutputConformanceTechnicalError(
                "visible projection Artifact scope mismatch"
            )
        augmented = (
            ArtifactCanonicalPayloadMaterializer.with_visible_projection_semantics(
                ArtifactCanonicalPayloadMaterializer.business_payload(
                    canonical_payload_ref
                ),
                projection.as_ref(),
            )
        )
        augmented = (
            ArtifactCanonicalPayloadMaterializer.with_validated_output_semantics(
                augmented,
                (binding,),
            )
        )
        artifact_hash = ArtifactCanonicalPayloadMaterializer.artifact_hash(
            artifact_scope=artifact_scope,
            canonical_payload_ref=augmented,
        )
        try:
            output_bytes = self.artifact_store.get_bytes(
                binding.output_artifact_ref.artifact_ref,
                task_id=binding.output_artifact_ref.task_id,
                run_id=binding.output_artifact_ref.run_id,
                attempt_id=binding.output_artifact_ref.attempt_id,
                input_snapshot_version=(
                    binding.output_artifact_ref.input_snapshot_version
                ),
            )
            if hashlib.sha256(output_bytes).hexdigest() != binding.output_payload_sha256:
                raise OutputConformanceTechnicalError(
                    "output bytes hash mismatch"
                )
            registration = self.registry.resolve(binding.output_format)
            extracted_visible_payload = registration.adapter(output_bytes)
            if not isinstance(extracted_visible_payload, dict):
                raise TypeError("conformance adapter must return dict")
        except OutputConformanceTechnicalError:
            raise
        except Exception as exc:
            raise OutputConformanceTechnicalError(
                "output conformance extraction failed"
            ) from exc

        extracted_projection_payload = {
            "schema_version": projection.schema_version,
            "projection_contract_version": (
                projection.projection_contract_version
            ),
            "artifact_scope": projection.artifact_scope.value,
            "logical_target_ref": projection.logical_target_ref,
            "visible_payload": extracted_visible_payload,
        }
        extracted_sha256 = canonical_sha256(
            extracted_projection_payload
        )
        state = (
            OutputConformanceState.CONFORMING
            if extracted_sha256 == projection.projection_sha256
            else OutputConformanceState.NONCONFORMING
        )
        reasons = (
            ()
            if state is OutputConformanceState.CONFORMING
            else ("VISIBLE_PROJECTION_MISMATCH",)
        )
        kwargs = {
            "conformance_contract_version": (
                self.conformance_contract_version
            ),
            "adapter_contract_version": (
                registration.adapter_contract_version
            ),
            "artifact_id": str(artifact_id).strip(),
            "artifact_hash": artifact_hash,
            "visible_projection_ref": projection.as_ref(),
            "visible_projection_sha256": projection.projection_sha256,
            "validated_output_binding_sha256": binding.binding_sha256,
            "output_format": binding.output_format,
            "renderer_contract_version": (
                binding.renderer_contract_version
            ),
            "output_payload_sha256": binding.output_payload_sha256,
            "extracted_visible_projection_sha256": extracted_sha256,
            "state": state,
            "reason_codes": reasons,
        }
        placeholder = ArtifactOutputConformanceProofSchema.model_construct(
            **kwargs,
            proof_sha256="0" * 64,
        )
        payload = placeholder.model_dump(mode="json")
        payload.pop("proof_sha256", None)
        return ArtifactOutputConformanceProofSchema(
            **kwargs,
            proof_sha256=canonical_sha256(payload),
        )

    def resolve_bound_conforming(
        self,
        *,
        record: ArtifactStateRecordSchema,
        output_binding: ArtifactValidatedOutputBindingSchema,
    ) -> ArtifactOutputConformanceProofSchema:
        import hashlib

        binding = ArtifactValidatedOutputBindingSchema.model_validate(
            output_binding
        )
        matching = [
            item
            for item in record.output_conformance_proofs
            if item.validated_output_binding_sha256 == binding.binding_sha256
            and item.output_format == binding.output_format
        ]
        if len(matching) != 1:
            raise ArtifactStateIntegrityError(
                "exact output conformance proof is missing or ambiguous"
            )
        proof = matching[0]
        if proof.state is not OutputConformanceState.CONFORMING:
            raise ArtifactStateIntegrityError(
                "selected output binding is NONCONFORMING"
            )
        if (
            record.visible_projection is None
            or record.visible_projection_ref is None
            or proof.visible_projection_ref != record.visible_projection_ref
            or proof.visible_projection_sha256
            != record.visible_projection_ref.projection_sha256
            or proof.artifact_id != record.artifact_ref.artifact_id
            or proof.artifact_hash != record.artifact_ref.artifact_hash
            or proof.output_payload_sha256 != binding.output_payload_sha256
            or proof.renderer_contract_version
            != binding.renderer_contract_version
        ):
            raise ArtifactStateIntegrityError(
                "output conformance proof exact binding mismatch"
            )
        try:
            output_bytes = self.artifact_store.get_bytes(
                binding.output_artifact_ref.artifact_ref,
                task_id=binding.output_artifact_ref.task_id,
                run_id=binding.output_artifact_ref.run_id,
                attempt_id=binding.output_artifact_ref.attempt_id,
                input_snapshot_version=(
                    binding.output_artifact_ref.input_snapshot_version
                ),
            )
        except Exception as exc:
            raise ArtifactStateIntegrityError(
                "validated output Artifact cannot be resolved exactly"
            ) from exc
        if hashlib.sha256(output_bytes).hexdigest() != binding.output_payload_sha256:
            raise ArtifactStateIntegrityError(
                "validated output bytes hash mismatch"
            )
        return proof


__all__ = [
    "ArtifactCanonicalPayloadMaterializer",
    "ArtifactOutputConformanceVerifier",
    "ArtifactStateIntegrityError",
    "ArtifactStateService",
    "ArtifactStaleParentError",
    "ArtifactValidationViewResolver",
    "OutputConformanceAdapter",
    "OutputConformanceRegistry",
    "OutputConformanceTechnicalError",
]
