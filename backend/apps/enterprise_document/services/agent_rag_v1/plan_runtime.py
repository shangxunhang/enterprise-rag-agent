"""Canonical SIF-05 SectionPlan runtime over SIF-03 ArtifactRuntime."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

from apps.enterprise_document.schemas.agent_rag_v1.artifacts import (
    ArtifactLogicalRefSchema,
    ArtifactVersionRefSchema,
)
from apps.enterprise_document.schemas.agent_rag_v1.facts import (
    FactSnapshotBindingSchema,
    FactSnapshotContentSchema,
)
from apps.enterprise_document.schemas.agent_rag_v1.lifecycle import (
    ActiveExecutionLineageSchema,
)
from apps.enterprise_document.schemas.pre_write_admission import (
    EvidenceRequirementsSnapshotSchema,
)
from apps.enterprise_document.schemas.scheme_writer.planning import (
    EvidenceRequirementsSourceRefSchema,
    SectionPlanBindingSchema,
    SectionPlanContentSchema,
)
from apps.enterprise_document.services.agent_rag_v1.artifact_runtime import ArtifactRuntime
from apps.enterprise_document.services.agent_rag_v1.fact_snapshot_runtime import (
    FactSnapshotRuntime,
)
from core.hashing import canonical_sha256
from core.runtime.side_effects import LogicalOperationRef


SECTION_PLAN_ARTIFACT_TYPE = "section_plan"
SECTION_PLAN_CONTENT_SCHEMA_VERSION = "section_plan_content_v1"


class SectionPlanRuntimeError(ValueError):
    """Base SIF-05 SectionPlan runtime failure."""


class SectionPlanSourceBindingError(SectionPlanRuntimeError):
    """One exact upstream source binding is missing, stale, or contradictory."""


class SectionPlanCurrentnessError(SectionPlanRuntimeError):
    """A SectionPlan point-in-time binding is stale or invalid."""


RequirementsResolver = Callable[
    [EvidenceRequirementsSourceRefSchema],
    EvidenceRequirementsSnapshotSchema,
]


@dataclass(frozen=True)
class SectionPlanCommitOperations:
    version_commit: LogicalOperationRef
    active_promotion: LogicalOperationRef


class SectionPlanRuntime:
    """Domain Plan owner; version/currentness mechanics remain ArtifactRuntime-owned."""

    def __init__(
        self,
        *,
        artifact_runtime: ArtifactRuntime,
        fact_snapshot_runtime: FactSnapshotRuntime,
        requirements_resolver: RequirementsResolver,
    ) -> None:
        if not callable(requirements_resolver):
            raise TypeError("SectionPlanRuntime requires requirements_resolver")
        self.artifact_runtime = artifact_runtime
        self.fact_snapshot_runtime = fact_snapshot_runtime
        self.requirements_resolver = requirements_resolver

    @staticmethod
    def logical_ref_for_scope(
        *,
        run_id: str,
        document_scope_ref: str,
        logical_artifact_id: str | None = None,
    ) -> ArtifactLogicalRefSchema:
        run = str(run_id or "").strip()
        scope = str(document_scope_ref or "").strip()
        if not run or not scope:
            raise SectionPlanRuntimeError(
                "run_id and document_scope_ref are required"
            )
        logical_id = str(logical_artifact_id or "").strip()
        if not logical_id:
            logical_id = "section-plan:" + canonical_sha256(
                {
                    "run_id": run,
                    "document_scope_ref": scope,
                    "artifact_type": SECTION_PLAN_ARTIFACT_TYPE,
                }
            )
        return ArtifactLogicalRefSchema(
            run_id=run,
            logical_artifact_id=logical_id,
            artifact_type=SECTION_PLAN_ARTIFACT_TYPE,
            domain_scope_ref=scope,
        )

    @staticmethod
    def _validate_logical_ref(logical_ref: ArtifactLogicalRefSchema) -> None:
        if logical_ref.artifact_type != SECTION_PLAN_ARTIFACT_TYPE:
            raise SectionPlanRuntimeError(
                "SectionPlan logical_ref requires artifact_type=section_plan"
            )

    def _resolve_requirements(
        self,
        source: EvidenceRequirementsSourceRefSchema,
    ) -> EvidenceRequirementsSnapshotSchema:
        try:
            snapshot = self.requirements_resolver(source)
        except Exception as exc:
            raise SectionPlanSourceBindingError(
                "EvidenceRequirements source resolution failed"
            ) from exc
        snapshot = EvidenceRequirementsSnapshotSchema.model_validate(snapshot)
        if (
            snapshot.schema_version != source.requirements_schema_version
            or snapshot.contract_version != source.requirements_contract_version
            or snapshot.requirements_sha256 != source.requirements_sha256
            or snapshot.source_profile_ref != source.source_profile_ref
            or snapshot.source_task_ref != source.source_task_ref
        ):
            raise SectionPlanSourceBindingError(
                "EvidenceRequirements source ref does not resolve exact canonical snapshot"
            )
        return snapshot

    def _validate_material_sources(
        self,
        content: SectionPlanContentSchema,
        fact_snapshot_binding: FactSnapshotBindingSchema,
    ) -> FactSnapshotContentSchema:
        if (
            fact_snapshot_binding.version_ref
            != content.fact_snapshot_version_ref
        ):
            raise SectionPlanSourceBindingError(
                "FactSnapshot commit guard does not match Plan material source version"
            )
        self.fact_snapshot_runtime.validate_binding_current(fact_snapshot_binding)
        fact_snapshot = self.fact_snapshot_runtime.read_binding(
            fact_snapshot_binding,
            require_current=True,
        )
        fact_ids = {item.fact_id for item in fact_snapshot.facts}

        resolved_requirements: dict[
            tuple[str, str, str], EvidenceRequirementsSnapshotSchema
        ] = {}
        for section in content.sections:
            for consistency_ref in section.consistency_refs:
                if (
                    consistency_ref.ref_kind == "fact"
                    and consistency_ref.ref_id not in fact_ids
                ):
                    raise SectionPlanSourceBindingError(
                        "fact consistency ref is absent from exact bound FactSnapshot: "
                        f"{consistency_ref.ref_id}"
                    )

            source = section.evidence_requirements_source
            if source is None:
                continue
            source_key = (
                source.requirements_sha256,
                source.source_profile_ref,
                source.source_task_ref,
            )
            snapshot = resolved_requirements.get(source_key)
            if snapshot is None:
                snapshot = self._resolve_requirements(source)
                resolved_requirements[source_key] = snapshot
            available_ids = {item.requirement_id for item in snapshot.requirements}
            missing = set(section.evidence_requirement_ids) - available_ids
            if missing:
                raise SectionPlanSourceBindingError(
                    "SectionPlan selected requirement IDs are absent from exact "
                    "EvidenceRequirementsSnapshot: "
                    + ", ".join(sorted(missing))
                )
        return fact_snapshot

    @staticmethod
    def semantic_fingerprint(
        logical_ref: ArtifactLogicalRefSchema,
        content: SectionPlanContentSchema,
    ) -> str:
        if logical_ref.artifact_type != SECTION_PLAN_ARTIFACT_TYPE:
            raise SectionPlanRuntimeError(
                "SectionPlan fingerprint requires artifact_type=section_plan"
            )
        return canonical_sha256(
            {
                "logical_ref": logical_ref.model_dump(mode="json"),
                "content": content.model_dump(mode="json"),
            }
        )

    def commit_plan(
        self,
        *,
        candidate_content: SectionPlanContentSchema,
        logical_ref: ArtifactLogicalRefSchema,
        fact_snapshot_binding: FactSnapshotBindingSchema,
        expected_head_revision: int,
        expected_active_version_id: str | None,
        operations: SectionPlanCommitOperations,
        commit_guard_context: ActiveExecutionLineageSchema | None = None,
    ) -> ArtifactVersionRefSchema:
        self._validate_logical_ref(logical_ref)
        content = SectionPlanContentSchema.model_validate(
            candidate_content.model_dump(mode="python", round_trip=True)
        )
        if (
            content.fact_snapshot_version_ref.logical_ref.run_id
            != logical_ref.run_id
        ):
            raise SectionPlanSourceBindingError(
                "SectionPlan must bind the canonical FactSnapshot from the same Run"
            )
        self._validate_material_sources(content, fact_snapshot_binding)
        fingerprint = self.semantic_fingerprint(logical_ref, content)

        version_ref = self.artifact_runtime.commit_version(
            logical_ref,
            content,
            SECTION_PLAN_CONTENT_SCHEMA_VERSION,
            operations.version_commit,
            fingerprint,
            commit_guard_context,
        )
        promotion_fingerprint = canonical_sha256(
            {
                "logical_ref": logical_ref.model_dump(mode="json"),
                "target_version_ref": version_ref.model_dump(mode="json"),
                "expected_head_revision": int(expected_head_revision),
                "expected_active_version_id": expected_active_version_id,
            }
        )
        self.artifact_runtime.promote_active(
            logical_ref,
            version_ref,
            expected_head_revision,
            expected_active_version_id,
            operations.active_promotion,
            promotion_fingerprint,
            commit_guard_context,
        )
        return version_ref

    def resolve_current_plan_binding(
        self,
        *,
        run_id: str,
        document_scope_ref: str,
        section_id: str,
        logical_artifact_id: str | None = None,
    ) -> SectionPlanBindingSchema | None:
        logical_ref = self.logical_ref_for_scope(
            run_id=run_id,
            document_scope_ref=document_scope_ref,
            logical_artifact_id=logical_artifact_id,
        )
        head = self.artifact_runtime.resolve_active(logical_ref)
        if head is None or head.active_version_ref is None:
            return None
        content_raw, canonical_ref = self.artifact_runtime.read_version(
            head.active_version_ref
        )
        content = SectionPlanContentSchema.model_validate(content_raw)
        normalized_section = str(section_id or "").strip()
        matches = [item for item in content.sections if item.section_id == normalized_section]
        if len(matches) != 1:
            raise SectionPlanCurrentnessError(
                "requested section_id does not resolve exactly once in active Plan"
            )
        return SectionPlanBindingSchema(
            plan_version_ref=canonical_ref,
            resolved_head_revision=head.head_revision,
            section_id=normalized_section,
        )

    def validate_plan_binding_current(
        self,
        binding: SectionPlanBindingSchema,
    ) -> SectionPlanBindingSchema:
        logical_ref = binding.plan_version_ref.logical_ref
        self._validate_logical_ref(logical_ref)
        head = self.artifact_runtime.resolve_active(logical_ref)
        if head is None or head.active_version_ref is None:
            raise SectionPlanCurrentnessError("SectionPlan has no active canonical head")
        if (
            head.head_revision != binding.resolved_head_revision
            or head.active_version_ref != binding.plan_version_ref
        ):
            raise SectionPlanCurrentnessError("SectionPlan binding is stale")
        content_raw, canonical_ref = self.artifact_runtime.read_version(
            binding.plan_version_ref
        )
        if canonical_ref != binding.plan_version_ref:
            raise SectionPlanCurrentnessError(
                "Artifact resolver returned a different Plan version"
            )
        content = SectionPlanContentSchema.model_validate(content_raw)
        if sum(1 for item in content.sections if item.section_id == binding.section_id) != 1:
            raise SectionPlanCurrentnessError(
                "bound section_id is absent or ambiguous in exact Plan version"
            )
        return binding


__all__ = [
    "SECTION_PLAN_ARTIFACT_TYPE",
    "SECTION_PLAN_CONTENT_SCHEMA_VERSION",
    "SectionPlanCommitOperations",
    "SectionPlanCurrentnessError",
    "SectionPlanRuntime",
    "SectionPlanRuntimeError",
    "SectionPlanSourceBindingError",
]
