"""Candidate-evidence validation and project-adaptation contracts."""

from __future__ import annotations

from typing import List, Literal, Optional

from pydantic import Field, model_validator

from ._base import ContractSchema


class CandidateEvidenceItemSchema(ContractSchema):
    schema_version: str = "candidate_evidence_item_v1"
    evidence_id: str
    source_ref: str
    source_kind: Literal[
        "customer_material",
        "vendor_material",
        "enterprise_knowledge",
        "historical_scheme",
        "policy_corpus",
    ]
    content: str
    mentioned_project_names: List[str] = Field(default_factory=list)
    device_name: Optional[str] = None
    claimed_model: Optional[str] = None
    general_technical_content: bool = False

    @model_validator(mode="after")
    def _model_claim_requires_device(self) -> "CandidateEvidenceItemSchema":
        if self.claimed_model and not self.device_name:
            raise ValueError("claimed model requires device_name")
        return self


class ValidatedEvidenceItemSchema(ContractSchema):
    schema_version: str = "validated_evidence_item_v1"
    evidence_id: str
    source_ref: str
    sanitized_content: str
    project_adapted: bool = False
    validation_status: Literal["validated"] = "validated"
    validation_reason_codes: List[str] = Field(default_factory=list)


class RejectedCandidateEvidenceSchema(ContractSchema):
    schema_version: str = "rejected_candidate_evidence_v1"
    evidence_id: str
    source_ref: str
    validation_status: Literal["conflicted", "rejected"]
    reason_code: str


class EvidenceSanitizationResultSchema(ContractSchema):
    schema_version: str = "evidence_sanitization_result_v1"
    accepted: List[ValidatedEvidenceItemSchema] = Field(default_factory=list)
    rejected: List[RejectedCandidateEvidenceSchema] = Field(default_factory=list)

    @model_validator(mode="after")
    def _evidence_ids_are_disjoint(self) -> "EvidenceSanitizationResultSchema":
        accepted_ids = [item.evidence_id for item in self.accepted]
        rejected_ids = [item.evidence_id for item in self.rejected]
        if len(accepted_ids) != len(set(accepted_ids)):
            raise ValueError("accepted evidence ids must be unique")
        if len(rejected_ids) != len(set(rejected_ids)):
            raise ValueError("rejected evidence ids must be unique")
        if set(accepted_ids) & set(rejected_ids):
            raise ValueError("evidence cannot be both accepted and rejected")
        return self
