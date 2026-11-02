"""Canonical SIF-05 EvidencePackage and SIF-08 Research contracts.

EvidencePackage is a section-scoped Domain projection over one exact immutable
RAG EvidenceBundle.  ResearchRound/ResearchProgress own only Research execution
progress.  Neither surface owns Evidence assessment, Gate decisions, Workflow
routing, Budget truth, or Artifact currentness.
"""

from __future__ import annotations

from enum import Enum
from typing import Annotated, Any, Literal

from pydantic import ConfigDict, Field, StringConstraints, field_validator, model_validator

from apps.enterprise_document.schemas.pre_write_control import (
    PreWriteRouteDecisionRefSchema,
)
from core.runtime.side_effects import LogicalOperationRef

from ._base import ContractSchema
from .artifacts import ArtifactVersionRefSchema


NonEmptyStr = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1)]
ContentSha256 = Annotated[
    str,
    StringConstraints(
        strip_whitespace=True,
        pattern=r"^sha256:[0-9a-f]{64}$",
    ),
]


class _FrozenResearchContract(ContractSchema):
    model_config = ConfigDict(
        arbitrary_types_allowed=True,
        extra="forbid",
        validate_assignment=True,
        frozen=True,
    )


class CanonicalEvidenceBundleBindingSchema(_FrozenResearchContract):
    schema_version: Literal["canonical_evidence_bundle_binding_v1"] = (
        "canonical_evidence_bundle_binding_v1"
    )
    evidence_bundle_handle: NonEmptyStr
    evidence_bundle_sha256: ContentSha256


class SectionPlanSourceRefSchema(_FrozenResearchContract):
    schema_version: Literal["section_plan_source_ref_v1"] = "section_plan_source_ref_v1"
    plan_version_ref: ArtifactVersionRefSchema
    section_id: NonEmptyStr

    @model_validator(mode="after")
    def _binds_section_plan(self) -> "SectionPlanSourceRefSchema":
        if self.plan_version_ref.logical_ref.artifact_type != "section_plan":
            raise ValueError("SectionPlan source requires artifact_type=section_plan")
        if self.plan_version_ref.content_schema_version != "section_plan_content_v1":
            raise ValueError(
                "SectionPlan source requires section_plan_content_v1 Artifact content"
            )
        return self


class EvidencePackageSelectionSchema(_FrozenResearchContract):
    schema_version: Literal["evidence_package_selection_v1"] = (
        "evidence_package_selection_v1"
    )
    evidence_id: NonEmptyStr
    requirement_ids: tuple[NonEmptyStr, ...] = ()

    @model_validator(mode="before")
    @classmethod
    def _canonicalize_requirement_ids(cls, value: Any) -> Any:
        if isinstance(value, cls) or not isinstance(value, dict):
            return value
        payload = dict(value)
        raw = payload.get("requirement_ids")
        if isinstance(raw, (list, tuple)):
            normalized = [str(item).strip() for item in raw]
            if any(not item for item in normalized):
                raise ValueError("requirement_ids cannot contain blank values")
            if len(normalized) != len(set(normalized)):
                raise ValueError("requirement_ids must be unique")
            payload["requirement_ids"] = sorted(normalized)
        return payload


class EvidencePackageContentSchema(_FrozenResearchContract):
    schema_version: Literal["evidence_package_content_v1"] = (
        "evidence_package_content_v1"
    )
    projection_contract_version: NonEmptyStr
    section_plan_source: SectionPlanSourceRefSchema
    evidence_bundle_binding: CanonicalEvidenceBundleBindingSchema
    direct_fact_snapshot_version_ref: ArtifactVersionRefSchema | None = None
    selections: tuple[EvidencePackageSelectionSchema, ...] = ()

    @model_validator(mode="after")
    def _validate_content(self) -> "EvidencePackageContentSchema":
        evidence_ids = [item.evidence_id for item in self.selections]
        if len(evidence_ids) != len(set(evidence_ids)):
            raise ValueError("EvidencePackage selections require unique evidence_id values")
        direct_fact = self.direct_fact_snapshot_version_ref
        if direct_fact is not None:
            if direct_fact.logical_ref.artifact_type != "fact_snapshot":
                raise ValueError(
                    "direct FactSnapshot source requires artifact_type=fact_snapshot"
                )
            if direct_fact.content_schema_version != "fact_snapshot_content_v1":
                raise ValueError(
                    "direct FactSnapshot source requires fact_snapshot_content_v1"
                )
        return self


class EvidencePackageBindingSchema(_FrozenResearchContract):
    schema_version: Literal["evidence_package_binding_v1"] = (
        "evidence_package_binding_v1"
    )
    version_ref: ArtifactVersionRefSchema
    resolved_head_revision: int = Field(ge=1)

    @model_validator(mode="after")
    def _binds_evidence_package(self) -> "EvidencePackageBindingSchema":
        if self.version_ref.logical_ref.artifact_type != "evidence_package":
            raise ValueError(
                "EvidencePackage binding requires artifact_type=evidence_package"
            )
        if self.version_ref.content_schema_version != "evidence_package_content_v1":
            raise ValueError(
                "EvidencePackage binding requires evidence_package_content_v1"
            )
        return self


class ResearchRoundRefSchema(_FrozenResearchContract):
    schema_version: Literal["research_round_ref_v1"] = "research_round_ref_v1"
    run_id: NonEmptyStr
    section_id: NonEmptyStr
    attempt_scope_ref: NonEmptyStr
    attempt_id: NonEmptyStr
    research_round_id: NonEmptyStr


class ResearchRoundAuthorizationBindingSchema(_FrozenResearchContract):
    schema_version: Literal["research_round_authorization_binding_v1"] = (
        "research_round_authorization_binding_v1"
    )
    authorization_ref: NonEmptyStr
    authorization_kind: Literal["WORKFLOW_RESEARCH", "GATE_RECOVERY_RESEARCH"]
    route_decision_ref: PreWriteRouteDecisionRefSchema | None = None
    gate_recovery_dispatch_occurrence_ref: NonEmptyStr | None = None
    gate_recovery_state_revision: int | None = Field(default=None, ge=1)

    @model_validator(mode="after")
    def _validate_authorization_shape(
        self,
    ) -> "ResearchRoundAuthorizationBindingSchema":
        if self.authorization_kind == "WORKFLOW_RESEARCH":
            if (
                self.route_decision_ref is not None
                or self.gate_recovery_dispatch_occurrence_ref is not None
                or self.gate_recovery_state_revision is not None
            ):
                raise ValueError(
                    "WORKFLOW_RESEARCH V1 cannot manufacture Gate recovery lineage"
                )
            return self

        if self.route_decision_ref is None:
            raise ValueError("GATE_RECOVERY_RESEARCH requires route_decision_ref")
        if self.gate_recovery_dispatch_occurrence_ref is None:
            raise ValueError(
                "GATE_RECOVERY_RESEARCH requires dispatch occurrence ref"
            )
        if self.gate_recovery_state_revision is None:
            raise ValueError(
                "GATE_RECOVERY_RESEARCH requires gate recovery state revision"
            )
        if self.authorization_ref != self.gate_recovery_dispatch_occurrence_ref:
            raise ValueError(
                "Gate recovery authorization_ref must equal dispatch occurrence ref"
            )
        return self


class ResearchProgressStateSchema(_FrozenResearchContract):
    schema_version: Literal["research_progress_state_v1"] = "research_progress_state_v1"
    run_id: NonEmptyStr
    section_id: NonEmptyStr
    attempt_scope_ref: NonEmptyStr
    attempt_id: NonEmptyStr
    progress_revision: int = Field(ge=0)
    committed_round_count: int = Field(ge=0)
    last_committed_round_ref: ResearchRoundRefSchema | None = None

    @model_validator(mode="after")
    def _validate_progress(self) -> "ResearchProgressStateSchema":
        if self.progress_revision != self.committed_round_count:
            raise ValueError(
                "Research progress revision and committed round count advance together"
            )
        if (self.committed_round_count == 0) != (
            self.last_committed_round_ref is None
        ):
            raise ValueError(
                "last_committed_round_ref must exist exactly after the first commit"
            )
        ref = self.last_committed_round_ref
        if ref is not None and (
            ref.run_id != self.run_id
            or ref.section_id != self.section_id
            or ref.attempt_scope_ref != self.attempt_scope_ref
            or ref.attempt_id != self.attempt_id
        ):
            raise ValueError(
                "last committed Research round must belong to the same progress scope"
            )
        return self


class ResearchRoundExecutionOutcome(str, Enum):
    COMPLETED = "COMPLETED"
    COMPLETED_WITH_TECHNICAL_DEGRADATION = "COMPLETED_WITH_TECHNICAL_DEGRADATION"
    TECHNICAL_FAILURE = "TECHNICAL_FAILURE"


class ResearchPackageProgressSchema(_FrozenResearchContract):
    schema_version: Literal["research_package_progress_v1"] = (
        "research_package_progress_v1"
    )
    prior_evidence_package_version_ref: ArtifactVersionRefSchema | None = None
    resulting_evidence_package_version_ref: ArtifactVersionRefSchema | None = None
    material_package_change: Literal[
        "NEW_PACKAGE",
        "CHANGED_PACKAGE",
        "NO_MATERIAL_PACKAGE_CHANGE",
        "UNAVAILABLE_DUE_TO_TECHNICAL_FAILURE",
    ]

    @model_validator(mode="after")
    def _validate_package_progress(self) -> "ResearchPackageProgressSchema":
        prior = self.prior_evidence_package_version_ref
        result = self.resulting_evidence_package_version_ref
        for name, ref in (("prior", prior), ("resulting", result)):
            if ref is not None and (
                ref.logical_ref.artifact_type != "evidence_package"
                or ref.content_schema_version != "evidence_package_content_v1"
            ):
                raise ValueError(
                    f"{name} package ref must resolve evidence_package_content_v1"
                )
        change = self.material_package_change
        if change == "NEW_PACKAGE":
            if prior is not None or result is None:
                raise ValueError("NEW_PACKAGE requires prior absent and result present")
        elif change == "CHANGED_PACKAGE":
            if prior is None or result is None or prior == result:
                raise ValueError(
                    "CHANGED_PACKAGE requires distinct prior and resulting versions"
                )
        elif change == "NO_MATERIAL_PACKAGE_CHANGE":
            if prior is None or result is None or prior != result:
                raise ValueError(
                    "NO_MATERIAL_PACKAGE_CHANGE requires equal prior/result versions"
                )
        else:
            if result is not None:
                raise ValueError(
                    "technical-failure package progress cannot fabricate a result version"
                )
        return self


def _logical_operation_key(value: LogicalOperationRef) -> tuple[str, str]:
    return (value.logical_operation_id, value.operation_kind)


class ResearchRoundRecordSchema(_FrozenResearchContract):
    schema_version: Literal["research_round_record_v1"] = "research_round_record_v1"
    round_ref: ResearchRoundRefSchema
    round_ordinal: int = Field(ge=1)
    authorization_binding: ResearchRoundAuthorizationBindingSchema
    prior_committed_round_ref: ResearchRoundRefSchema | None = None
    source_plan_version_ref: ArtifactVersionRefSchema
    execution_outcome: ResearchRoundExecutionOutcome
    final_evidence_bundle_binding: CanonicalEvidenceBundleBindingSchema | None = None
    package_progress: ResearchPackageProgressSchema
    tool_operation_refs: tuple[LogicalOperationRef, ...] = ()
    budget_receipt_refs: tuple[NonEmptyStr, ...] = ()

    @field_validator("tool_operation_refs")
    @classmethod
    def _tool_operations_are_ordered_unique(
        cls,
        values: tuple[LogicalOperationRef, ...],
    ) -> tuple[LogicalOperationRef, ...]:
        keys = [_logical_operation_key(item) for item in values]
        if len(keys) != len(set(keys)):
            raise ValueError("tool_operation_refs must be ordered-unique")
        return values

    @field_validator("budget_receipt_refs")
    @classmethod
    def _budget_receipts_are_ordered_unique(
        cls,
        values: tuple[str, ...],
    ) -> tuple[str, ...]:
        if len(values) != len(set(values)):
            raise ValueError("budget_receipt_refs must be ordered-unique")
        return values

    @model_validator(mode="after")
    def _validate_round(self) -> "ResearchRoundRecordSchema":
        ref = self.round_ref
        source = self.source_plan_version_ref
        if source.logical_ref.artifact_type != "section_plan":
            raise ValueError("ResearchRound source must be a SectionPlan Artifact")
        if source.content_schema_version != "section_plan_content_v1":
            raise ValueError("ResearchRound source must be section_plan_content_v1")
        prior = self.prior_committed_round_ref
        if prior is not None and (
            prior.run_id != ref.run_id
            or prior.section_id != ref.section_id
            or prior.attempt_scope_ref != ref.attempt_scope_ref
            or prior.attempt_id != ref.attempt_id
        ):
            raise ValueError("prior Research round must belong to the same Attempt scope")
        for operation in self.tool_operation_refs:
            if operation.run_id != ref.run_id:
                raise ValueError("tool operation refs must belong to the same Run")
        if self.execution_outcome is ResearchRoundExecutionOutcome.TECHNICAL_FAILURE:
            if self.final_evidence_bundle_binding is not None:
                raise ValueError(
                    "TECHNICAL_FAILURE cannot fabricate final EvidenceBundle truth"
                )
            if (
                self.package_progress.material_package_change
                != "UNAVAILABLE_DUE_TO_TECHNICAL_FAILURE"
            ):
                raise ValueError(
                    "TECHNICAL_FAILURE requires unavailable package progress"
                )
        else:
            if self.final_evidence_bundle_binding is None:
                raise ValueError(
                    "valid Research completion requires exact EvidenceBundle binding"
                )
            if self.package_progress.resulting_evidence_package_version_ref is None:
                raise ValueError(
                    "valid Research completion requires committed EvidencePackage"
                )
            if (
                self.package_progress.material_package_change
                == "UNAVAILABLE_DUE_TO_TECHNICAL_FAILURE"
            ):
                raise ValueError(
                    "valid Research completion cannot use technical-failure package progress"
                )
        return self


class ResearchRoundBindingSchema(_FrozenResearchContract):
    schema_version: Literal["research_round_binding_v1"] = "research_round_binding_v1"
    round_ref: ResearchRoundRefSchema
    resolved_progress_revision: int = Field(ge=1)


__all__ = [
    "CanonicalEvidenceBundleBindingSchema",
    "EvidencePackageBindingSchema",
    "EvidencePackageContentSchema",
    "EvidencePackageSelectionSchema",
    "ResearchPackageProgressSchema",
    "ResearchProgressStateSchema",
    "ResearchRoundAuthorizationBindingSchema",
    "ResearchRoundBindingSchema",
    "ResearchRoundExecutionOutcome",
    "ResearchRoundRecordSchema",
    "ResearchRoundRefSchema",
    "SectionPlanSourceRefSchema",
]
