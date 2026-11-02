"""SIF-11 one-root semantic Paragraph repair authorization contracts."""

from __future__ import annotations

from typing import Annotated, Literal

from pydantic import ConfigDict, Field, StringConstraints, model_validator

from core.hashing import canonical_sha256
from core.runtime.side_effects import LogicalOperationRef

from ._base import ContractSchema
from .artifacts import ArtifactLogicalRefSchema, ArtifactVersionRefSchema
from .budgets import BudgetReservationRefSchema
from .paragraphs import SectionParagraphManifestRefSchema
from .research import SectionPlanSourceRefSchema
from .verification import (
    VerificationResultRefSchema,
    VerifierIssueLifecycleStatus,
    VerifierIssueRefSchema,
)


NonEmptyStr = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1)]
Sha256Hex = Annotated[
    str,
    StringConstraints(strip_whitespace=True, pattern=r"^[0-9a-f]{64}$"),
]


class _FrozenRepairContract(ContractSchema):
    model_config = ConfigDict(
        arbitrary_types_allowed=True,
        extra="forbid",
        validate_assignment=True,
        frozen=True,
    )


class ParagraphRepairTargetBindingSchema(_FrozenRepairContract):
    schema_version: Literal["paragraph_repair_target_binding_v1"] = (
        "paragraph_repair_target_binding_v1"
    )
    run_id: NonEmptyStr
    section_id: NonEmptyStr
    paragraph_logical_ref: ArtifactLogicalRefSchema
    base_paragraph_version_ref: ArtifactVersionRefSchema
    manifest_ref: SectionParagraphManifestRefSchema
    resolved_manifest_revision: int = Field(ge=1)
    write_instance_id: NonEmptyStr

    @model_validator(mode="after")
    def _target_exact(self) -> "ParagraphRepairTargetBindingSchema":
        logical = self.paragraph_logical_ref
        if (
            logical.artifact_type != "paragraph"
            or logical.run_id != self.run_id
            or logical.domain_scope_ref != f"section:{self.section_id}"
        ):
            raise ValueError("repair target logical Paragraph scope mismatch")
        if self.base_paragraph_version_ref.logical_ref != logical:
            raise ValueError("repair base version belongs to another Paragraph")
        manifest = self.manifest_ref
        if (
            manifest.run_id != self.run_id
            or manifest.section_id != self.section_id
            or manifest.attempt_scope_ref != f"section:{self.section_id}"
            or manifest.write_instance_id != self.write_instance_id
        ):
            raise ValueError("repair target Manifest lineage mismatch")
        return self


class RepairIssueBindingSchema(_FrozenRepairContract):
    schema_version: Literal["repair_issue_binding_v1"] = "repair_issue_binding_v1"
    issue_ref: VerifierIssueRefSchema
    expected_issue_head_revision: int = Field(ge=1)
    expected_lifecycle_status: Literal[VerifierIssueLifecycleStatus.OPEN] = (
        VerifierIssueLifecycleStatus.OPEN
    )


class ExactRepairBasisRefSchema(_FrozenRepairContract):
    """Typed exact-ref envelope without importing Post-write schemas."""

    schema_version: Literal["exact_repair_basis_ref_v1"] = "exact_repair_basis_ref_v1"
    authority_kind: NonEmptyStr
    exact_ref: NonEmptyStr
    exact_hash: Sha256Hex | None = None


class ParagraphRepairLogicalOperationRefSchema(_FrozenRepairContract):
    schema_version: Literal["paragraph_repair_logical_operation_ref_v1"] = (
        "paragraph_repair_logical_operation_ref_v1"
    )
    logical_operation_id: NonEmptyStr
    operation_kind: Literal["PARAGRAPH_REPAIR_AUTHORIZATION_COMMIT"] = (
        "PARAGRAPH_REPAIR_AUTHORIZATION_COMMIT"
    )
    run_id: NonEmptyStr
    operation_scope_ref: NonEmptyStr
    attempt_scope_ref: NonEmptyStr
    attempt_id: NonEmptyStr

    def to_runtime_ref(self) -> LogicalOperationRef:
        return LogicalOperationRef(
            logical_operation_id=self.logical_operation_id,
            operation_kind=self.operation_kind,
            run_id=self.run_id,
            operation_scope_ref=self.operation_scope_ref,
            attempt_scope_ref=self.attempt_scope_ref,
            attempt_id=self.attempt_id,
        )


class ParagraphRepairAuthorizationRefSchema(_FrozenRepairContract):
    schema_version: Literal["paragraph_repair_authorization_ref_v2"] = (
        "paragraph_repair_authorization_ref_v2"
    )
    repair_authorization_id: NonEmptyStr
    semantic_fingerprint: Sha256Hex


class ParagraphRepairAuthorizationRecordSchema(_FrozenRepairContract):
    schema_version: Literal["paragraph_repair_authorization_v2"] = (
        "paragraph_repair_authorization_v2"
    )
    contract_version: Literal["sif11_paragraph_repair_one_root_v2"] = (
        "sif11_paragraph_repair_one_root_v2"
    )
    repair_authorization_id: NonEmptyStr
    run_id: NonEmptyStr
    section_id: NonEmptyStr
    attempt_scope_ref: NonEmptyStr
    attempt_id: NonEmptyStr
    input_snapshot_version: NonEmptyStr
    repair_kind: Literal["SAME_PARAGRAPH_REVISE"] = "SAME_PARAGRAPH_REVISE"
    target: ParagraphRepairTargetBindingSchema
    issue_bindings: tuple[RepairIssueBindingSchema, ...] = Field(min_length=1)
    verification_result_refs: tuple[VerificationResultRefSchema, ...] = Field(
        min_length=1
    )
    semantic_repair_basis_refs: tuple[ExactRepairBasisRefSchema, ...] = Field(
        min_length=1
    )
    recommendation_refs: tuple[ExactRepairBasisRefSchema, ...] = ()
    section_plan_ref: SectionPlanSourceRefSchema
    fact_snapshot_ref: ArtifactVersionRefSchema | None = None
    evidence_package_ref: ArtifactVersionRefSchema | None = None
    repair_policy_id: NonEmptyStr
    repair_policy_version: NonEmptyStr
    budget_reservation_ref: BudgetReservationRefSchema
    logical_operation_ref: ParagraphRepairLogicalOperationRefSchema
    semantic_fingerprint: Sha256Hex

    @model_validator(mode="after")
    def _authorization_exact(self) -> "ParagraphRepairAuthorizationRecordSchema":
        if self.attempt_scope_ref != f"section:{self.section_id}":
            raise ValueError("repair authorization Attempt scope must target Section")
        if (
            self.target.run_id != self.run_id
            or self.target.section_id != self.section_id
            or self.target.manifest_ref.attempt_scope_ref != self.attempt_scope_ref
            or self.target.manifest_ref.attempt_id != self.attempt_id
            or self.target.write_instance_id != self.attempt_id
        ):
            raise ValueError("repair authorization target/Attempt lineage mismatch")
        operation = self.logical_operation_ref
        if (
            operation.run_id != self.run_id
            or operation.operation_scope_ref
            != self.target.paragraph_logical_ref.logical_artifact_id
            or operation.attempt_scope_ref != self.attempt_scope_ref
            or operation.attempt_id != self.attempt_id
        ):
            raise ValueError("repair authorization logical operation mismatch")
        reservation = self.budget_reservation_ref
        if (
            reservation.run_id != self.run_id
            or reservation.reservation_kind != "PARAGRAPH_SEMANTIC_REPAIR"
            or reservation.scope_ref != self.attempt_scope_ref
            or reservation.operation_ref != operation.logical_operation_id
            or reservation.attempt_id != self.attempt_id
            or reservation.owner_occurrence_ref != self.repair_authorization_id
        ):
            raise ValueError("repair authorization SIF-12 reservation mismatch")
        if self.section_plan_ref.section_id != self.section_id:
            raise ValueError("repair SectionPlan source targets another Section")
        if self.section_plan_ref.plan_version_ref.logical_ref.run_id != self.run_id:
            raise ValueError("repair SectionPlan source belongs to another Run")
        for name in ("fact_snapshot_ref", "evidence_package_ref"):
            ref = getattr(self, name)
            if ref is not None and ref.logical_ref.run_id != self.run_id:
                raise ValueError(f"{name} belongs to another Run")

        issue_keys = [
            (item.issue_ref.issue_id, item.expected_issue_head_revision)
            for item in self.issue_bindings
        ]
        if issue_keys != sorted(issue_keys) or len(issue_keys) != len(set(issue_keys)):
            raise ValueError("repair issue bindings must be canonical ordered-unique")
        verification_ids = [
            item.verification_result_id for item in self.verification_result_refs
        ]
        if verification_ids != sorted(verification_ids) or len(
            verification_ids
        ) != len(set(verification_ids)):
            raise ValueError(
                "verification_result_refs must be canonical ordered-unique"
            )
        for name in ("semantic_repair_basis_refs", "recommendation_refs"):
            refs = tuple(getattr(self, name))
            keys = [(item.authority_kind, item.exact_ref) for item in refs]
            if keys != sorted(keys) or len(keys) != len(set(keys)):
                raise ValueError(f"{name} must be canonical ordered-unique")

        expected_id = "paragraph-repair-auth://sha256/" + canonical_sha256(
            {
                "contract": self.contract_version,
                "logical_operation_ref": operation.model_dump(mode="json"),
            }
        )
        if self.repair_authorization_id != expected_id:
            raise ValueError("ParagraphRepairAuthorization id mismatch")

        payload = self.model_dump(mode="json")
        payload.pop("semantic_fingerprint", None)
        if canonical_sha256(payload) != self.semantic_fingerprint:
            raise ValueError("ParagraphRepairAuthorization fingerprint mismatch")
        return self

    def as_ref(self) -> ParagraphRepairAuthorizationRefSchema:
        return ParagraphRepairAuthorizationRefSchema(
            repair_authorization_id=self.repair_authorization_id,
            semantic_fingerprint=self.semantic_fingerprint,
        )


__all__ = [
    "ExactRepairBasisRefSchema",
    "ParagraphRepairAuthorizationRecordSchema",
    "ParagraphRepairAuthorizationRefSchema",
    "ParagraphRepairLogicalOperationRefSchema",
    "ParagraphRepairTargetBindingSchema",
    "RepairIssueBindingSchema",
]
