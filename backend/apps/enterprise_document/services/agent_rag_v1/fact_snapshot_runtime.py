"""Canonical SIF-04 FactSnapshot runtime over SIF-03 ArtifactRuntime.

The Domain layer owns fact meaning, source eligibility and deterministic normalized
content. ArtifactRuntime exclusively owns immutable version identity and active-head
currentness. Workflow/State/Checkpoint/Trace never manufacture FactSnapshot truth.
"""

from __future__ import annotations

from typing import Callable

from apps.enterprise_document.schemas.agent_rag_v1.artifacts import (
    ArtifactLogicalRefSchema,
    ArtifactVersionRefSchema,
)
from apps.enterprise_document.schemas.agent_rag_v1.facts import (
    CanonicalFactRecordSchema,
    FactSnapshotBindingSchema,
    FactSnapshotContentSchema,
    FactSourceBindingSchema,
)
from apps.enterprise_document.schemas.agent_rag_v1.lifecycle import (
    ActiveExecutionLineageSchema,
)
from apps.enterprise_document.services.agent_rag_v1.artifact_runtime import (
    ArtifactRuntime,
)
from core.hashing import canonical_sha256
from core.runtime.side_effects import LogicalOperationRef


FACT_SNAPSHOT_ARTIFACT_TYPE = "fact_snapshot"
FACT_SNAPSHOT_CONTENT_SCHEMA_VERSION = "fact_snapshot_content_v1"


class FactSnapshotError(ValueError):
    """Base failure for canonical FactSnapshot domain operations."""


class FactSnapshotSourceBindingInvalid(FactSnapshotError):
    """A material authoritative source cannot be resolved to an exact version."""


class FactSnapshotCurrentnessError(FactSnapshotError):
    """A point-in-time FactSnapshot binding is stale or contradictory."""


SourceBindingResolver = Callable[[FactSourceBindingSchema], bool]


class FactSnapshotRuntime:
    """SIF-04 Domain owner delegating all version/currentness mechanics to SIF-03."""

    def __init__(
        self,
        *,
        artifact_runtime: ArtifactRuntime,
        source_binding_resolver: SourceBindingResolver,
    ) -> None:
        if not callable(source_binding_resolver):
            raise TypeError("FactSnapshotRuntime requires a source binding resolver")
        self.artifact_runtime = artifact_runtime
        self.source_binding_resolver = source_binding_resolver

    @staticmethod
    def logical_ref_for_scope(
        *,
        run_id: str,
        domain_scope_ref: str,
        logical_artifact_id: str | None = None,
    ) -> ArtifactLogicalRefSchema:
        normalized_run = str(run_id or "").strip()
        normalized_scope = str(domain_scope_ref or "").strip()
        if not normalized_run or not normalized_scope:
            raise FactSnapshotError("run_id and domain_scope_ref are required")
        canonical_logical_id = (
            "fact-snapshot:"
            + canonical_sha256(
                {
                    "run_id": normalized_run,
                    "domain_scope_ref": normalized_scope,
                    "artifact_type": FACT_SNAPSHOT_ARTIFACT_TYPE,
                }
            )
        )
        requested_logical_id = str(logical_artifact_id or "").strip()
        if requested_logical_id and requested_logical_id != canonical_logical_id:
            raise FactSnapshotError(
                "FactSnapshot scope cannot allocate a competing logical_artifact_id"
            )
        logical_id = canonical_logical_id
        return ArtifactLogicalRefSchema(
            run_id=normalized_run,
            logical_artifact_id=logical_id,
            artifact_type=FACT_SNAPSHOT_ARTIFACT_TYPE,
            domain_scope_ref=normalized_scope,
        )

    def _validate_logical_ref(self, logical_ref: ArtifactLogicalRefSchema) -> None:
        if logical_ref.artifact_type != FACT_SNAPSHOT_ARTIFACT_TYPE:
            raise FactSnapshotError(
                "FactSnapshot logical_ref must use artifact_type=fact_snapshot"
            )

    def _validate_source_binding(self, binding: FactSourceBindingSchema) -> None:
        try:
            resolved = bool(self.source_binding_resolver(binding))
        except Exception as exc:
            raise FactSnapshotSourceBindingInvalid(
                f"FactSnapshot source resolver failed for {binding.source_ref}"
            ) from exc
        if not resolved:
            raise FactSnapshotSourceBindingInvalid(
                "FactSnapshot material source binding is not exactly resolvable: "
                f"{binding.source_ref}"
            )

    def build_content_from_authoritative_sources(
        self,
        *,
        normalization_contract_version: str,
        source_bindings: list[FactSourceBindingSchema],
        facts: list[CanonicalFactRecordSchema],
    ) -> FactSnapshotContentSchema:
        content = FactSnapshotContentSchema(
            normalization_contract_version=normalization_contract_version,
            source_bindings=source_bindings,
            facts=facts,
        )
        for binding in content.source_bindings:
            self._validate_source_binding(binding)
        return content

    @staticmethod
    def semantic_fingerprint(
        logical_ref: ArtifactLogicalRefSchema,
        content: FactSnapshotContentSchema,
    ) -> str:
        if logical_ref.artifact_type != FACT_SNAPSHOT_ARTIFACT_TYPE:
            raise FactSnapshotError(
                "FactSnapshot fingerprint requires artifact_type=fact_snapshot"
            )
        return canonical_sha256(
            {
                "logical_ref": logical_ref.model_dump(mode="json"),
                "content_schema_version": FACT_SNAPSHOT_CONTENT_SCHEMA_VERSION,
                "normalization_contract_version": content.normalization_contract_version,
                "source_bindings": [
                    item.model_dump(mode="json") for item in content.source_bindings
                ],
                "facts": [item.model_dump(mode="json") for item in content.facts],
            }
        )

    def commit_snapshot(
        self,
        *,
        logical_ref: ArtifactLogicalRefSchema,
        content: FactSnapshotContentSchema,
        operation_ref: LogicalOperationRef,
        commit_guard_context: ActiveExecutionLineageSchema | None = None,
    ) -> ArtifactVersionRefSchema:
        self._validate_logical_ref(logical_ref)
        canonical_content = FactSnapshotContentSchema.model_validate(
            content.model_dump(mode="python", round_trip=True)
        )
        for binding in canonical_content.source_bindings:
            self._validate_source_binding(binding)
        fingerprint = self.semantic_fingerprint(logical_ref, canonical_content)
        return self.artifact_runtime.commit_version(
            logical_ref,
            canonical_content,
            FACT_SNAPSHOT_CONTENT_SCHEMA_VERSION,
            operation_ref,
            fingerprint,
            commit_guard_context,
        )

    def promote_snapshot(
        self,
        *,
        logical_ref: ArtifactLogicalRefSchema,
        target_version_ref: ArtifactVersionRefSchema,
        expected_head_revision: int,
        expected_active_version_id: str | None,
        operation_ref: LogicalOperationRef,
        commit_guard_context: ActiveExecutionLineageSchema | None = None,
    ) -> FactSnapshotBindingSchema:
        self._validate_logical_ref(logical_ref)
        if target_version_ref.logical_ref != logical_ref:
            raise FactSnapshotCurrentnessError(
                "FactSnapshot promotion target belongs to a different logical scope"
            )
        if target_version_ref.content_schema_version != FACT_SNAPSHOT_CONTENT_SCHEMA_VERSION:
            raise FactSnapshotCurrentnessError(
                "FactSnapshot promotion target has the wrong content schema"
            )
        fingerprint = canonical_sha256(
            {
                "logical_ref": logical_ref.model_dump(mode="json"),
                "target_version_ref": target_version_ref.model_dump(mode="json"),
                "expected_head_revision": int(expected_head_revision),
                "expected_active_version_id": expected_active_version_id,
            }
        )
        receipt = self.artifact_runtime.promote_active(
            logical_ref,
            target_version_ref,
            expected_head_revision,
            expected_active_version_id,
            operation_ref,
            fingerprint,
            commit_guard_context,
        )
        return FactSnapshotBindingSchema(
            version_ref=target_version_ref,
            resolved_head_revision=receipt.resulting_head_revision,
        )

    def resolve_current_binding(
        self,
        logical_ref: ArtifactLogicalRefSchema,
    ) -> FactSnapshotBindingSchema | None:
        self._validate_logical_ref(logical_ref)
        head = self.artifact_runtime.resolve_active(logical_ref)
        if head is None or head.active_version_ref is None:
            return None
        return FactSnapshotBindingSchema(
            version_ref=head.active_version_ref,
            resolved_head_revision=head.head_revision,
        )

    def validate_binding_current(
        self,
        binding: FactSnapshotBindingSchema,
    ) -> FactSnapshotBindingSchema:
        logical_ref = binding.version_ref.logical_ref
        self._validate_logical_ref(logical_ref)
        head = self.artifact_runtime.resolve_active(logical_ref)
        if head is None or head.active_version_ref is None:
            raise FactSnapshotCurrentnessError(
                "FactSnapshot binding has no active canonical head"
            )
        if (
            head.head_revision != binding.resolved_head_revision
            or head.active_version_ref != binding.version_ref
        ):
            raise FactSnapshotCurrentnessError("FactSnapshot binding is stale")
        return binding

    def read_binding(
        self,
        binding: FactSnapshotBindingSchema,
        *,
        require_current: bool = False,
    ) -> FactSnapshotContentSchema:
        if require_current:
            self.validate_binding_current(binding)
        content, canonical_ref = self.artifact_runtime.read_version(binding.version_ref)
        if canonical_ref != binding.version_ref:
            raise FactSnapshotCurrentnessError(
                "FactSnapshot Artifact resolver returned a different version ref"
            )
        return FactSnapshotContentSchema.model_validate(content)


__all__ = [
    "FACT_SNAPSHOT_ARTIFACT_TYPE",
    "FACT_SNAPSHOT_CONTENT_SCHEMA_VERSION",
    "FactSnapshotCurrentnessError",
    "FactSnapshotError",
    "FactSnapshotRuntime",
    "FactSnapshotSourceBindingInvalid",
]
