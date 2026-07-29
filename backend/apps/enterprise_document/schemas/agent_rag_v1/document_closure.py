"""文档级验证、修复、装配、报告和不可变发布协议。"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Dict, List, Literal, Optional

from pydantic import Field

from ._base import ContractSchema
from .enums import RepairAction, RepairTaskStatus, SectionStatus, TaskStatus


class DocumentConflictSchema(ContractSchema):
    conflict_id: str
    conflict_type: str
    severity: str
    section_ids: List[str] = Field(default_factory=list)
    target_section_ids: List[str] = Field(default_factory=list)
    claim_ids: List[str] = Field(default_factory=list)
    authoritative_fact_ref: Optional[str] = None
    message: str


class RepairTaskSchema(ContractSchema):
    repair_id: str
    priority: int = Field(ge=1)
    conflict_ids: List[str] = Field(default_factory=list)
    target_section_ids: List[str] = Field(default_factory=list)
    action: RepairAction
    authoritative_fact_refs: List[str] = Field(default_factory=list)
    prerequisite_repair_ids: List[str] = Field(default_factory=list)
    status: RepairTaskStatus = RepairTaskStatus.PENDING


class ConflictRepairPlanSchema(ContractSchema):
    schema_version: str = "conflict_repair_plan_v1"
    repair_tasks: List[RepairTaskSchema] = Field(default_factory=list)


class DocumentVerificationResultSchema(ContractSchema):
    schema_version: str = "document_verification_result_v1"
    mode: Literal["full_logic", "incremental_logic"]
    checked_section_ids: List[str] = Field(default_factory=list)
    conflicts: List[DocumentConflictSchema] = Field(default_factory=list)
    repair_plan: ConflictRepairPlanSchema
    passed: bool


class SectionGenerationReportSchema(ContractSchema):
    section_id: str
    section_title: str
    status: SectionStatus
    deliverable_paragraph_ids: List[str] = Field(default_factory=list)
    failure_stage: Optional[str] = None
    reason: Optional[str] = None
    recoverable: bool = False
    recovery_action: Optional[str] = None


class PartialSuccessReasonSchema(ContractSchema):
    section_id: str
    stage: str
    what_happened: str
    stop_reason: str
    recoverable: bool
    recovery_action: str


class GenerationReportSchema(ContractSchema):
    schema_version: str = "generation_report_v1"
    task_id: str
    run_id: str
    task_status: TaskStatus
    document_version: str
    section_results: List[SectionGenerationReportSchema] = Field(
        default_factory=list
    )
    partial_success_reasons: List[PartialSuccessReasonSchema] = Field(
        default_factory=list
    )
    source_versions: Dict[str, str] = Field(default_factory=dict)
    budget_usage: Dict[str, Any] = Field(default_factory=dict)
    error_refs: List[str] = Field(default_factory=list)
    generated_at: datetime


class AssembledDocumentSchema(ContractSchema):
    schema_version: str = "assembled_document_v1"
    task_id: str
    run_id: str
    task_status: TaskStatus
    document_title: str
    document_artifact_ref: str
    content_hash: str
    included_section_ids: List[str] = Field(default_factory=list)
    omitted_section_ids: List[str] = Field(default_factory=list)
    generation_report_ref: str


class AssemblyValidationResultSchema(ContractSchema):
    schema_version: str = "assembly_validation_result_v1"
    passed: bool
    checks: Dict[str, bool] = Field(default_factory=dict)
    failures: List[str] = Field(default_factory=list)
    warnings: List[str] = Field(default_factory=list)


class FinalArtifactReviewResultSchema(ContractSchema):
    schema_version: str = "final_artifact_review_result_v1"
    passed: bool
    issue_codes: List[str] = Field(default_factory=list)
    route_to: Optional[
        Literal["document_assembler", "section_verify", "supervisor"]
    ] = None
    affected_section_ids: List[str] = Field(default_factory=list)


class PublishedDocumentVersionSchema(ContractSchema):
    schema_version: str = "published_document_version_v1"
    task_id: str
    run_id: str
    document_version: str
    task_status: TaskStatus
    document_artifact_ref: str
    generation_report_ref: str
    published_at: datetime
