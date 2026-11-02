"""Strong runtime contracts for the standalone Agent-RAG V1 vertical slice."""

from __future__ import annotations

from typing import Any, Dict, List, Literal, Optional

from pydantic import Field, model_validator

from apps.enterprise_document.schemas.agent_rag_v1 import (
    GenerationReportSchema,
    MultiAgentSharedStateSchema,
    SectionGenerationReportSchema,
    SectionStrategy,
    TaskBudgetLimitsSchema,
    TaskStatus,
)
from apps.enterprise_document.schemas.agent_rag_v1._base import ContractSchema


StandaloneScenario = Literal[
    "success",
    "research-more",
    "rewrite",
    "paragraph-repair",
    "replan",
    "partial-success",
    "failed",
    "budget-exhausted",
    "human-review",
    "crash-recovery",
]

RetrievalStatus = Literal[
    "sufficient",
    "partial",
    "insufficient",
    "conflicted",
    "empty",
    "retry_then_sufficient",
]


def _default_budget_limits() -> TaskBudgetLimitsSchema:
    return TaskBudgetLimitsSchema(
        max_model_calls=50,
        max_tool_calls=20,
        max_total_replans=3,
        max_total_rewrites=5,
        max_document_repair_tasks=3,
        max_wall_clock_seconds=120,
    )


class RuntimeSectionTemplateSchema(ContractSchema):
    section_id: str
    section_title: str
    section_order: int = Field(ge=0)
    strategy: SectionStrategy = SectionStrategy.TECHNICAL_GENERATE
    hard_dependencies: List[str] = Field(default_factory=list)
    consistency_references: List[str] = Field(default_factory=list)
    execution_group: Optional[str] = "technical"
    can_run_in_parallel: bool = True
    requires_global_context: bool = False
    paragraph_roles: List[str] = Field(
        default_factory=lambda: ["overview", "implementation"]
    )


class RuntimeTemplateSchema(ContractSchema):
    schema_version: str = "agent_rag_v1_runtime_template_v1"
    template_version: str
    document_title: str
    sections: List[RuntimeSectionTemplateSchema] = Field(min_length=1)

    @model_validator(mode="after")
    def _unique_sections(self) -> "RuntimeTemplateSchema":
        section_ids = [section.section_id for section in self.sections]
        if len(section_ids) != len(set(section_ids)):
            raise ValueError("runtime template section ids must be unique")
        known = set(section_ids)
        for section in self.sections:
            unknown = set(section.hard_dependencies) - known
            if unknown:
                raise ValueError(
                    f"unknown runtime section dependencies: {sorted(unknown)}"
                )
        return self


class MultiAgentProjectInputSchema(ContractSchema):
    schema_version: str = "multi_agent_project_input_v1"
    project_name: str = "示例企业建设项目"
    scenario: StandaloneScenario = "success"
    task_id: Optional[str] = None
    run_id: Optional[str] = None
    output_dir: str = "data/agent_rag_v1_standalone"
    max_parallel_sections: int = Field(default=2, ge=1, le=8)
    previous_document_version: Optional[str] = None
    budget_limits: TaskBudgetLimitsSchema = Field(
        default_factory=_default_budget_limits
    )
    metadata: Dict[str, Any] = Field(default_factory=dict)


class RetrievalRequestSchema(ContractSchema):
    task_id: str
    run_id: str
    section_id: str
    query_topics: List[str] = Field(default_factory=list)
    research_round: int = Field(ge=1)
    scenario: StandaloneScenario


class RetrievalResponseSchema(ContractSchema):
    status: RetrievalStatus
    evidence_ids: List[str] = Field(default_factory=list)
    supported_topics: List[str] = Field(default_factory=list)
    evidence_gaps: List[str] = Field(default_factory=list)
    conflicts: List[Dict[str, Any]] = Field(default_factory=list)
    source_refs: List[str] = Field(default_factory=list)


class ExportedDocumentPathsSchema(ContractSchema):
    document_path: str
    generation_report_path: str


class SectionAttemptRecordSchema(ContractSchema):
    section_id: str
    attempt_id: str
    execution_id: str
    plan_revision: int = Field(ge=1)
    status: Literal["active", "superseded", "completed", "failed"]


class RuntimeTraceSchema(ContractSchema):
    schema_version: str = "agent_rag_v1_runtime_trace_v1"
    agent_invocations: List[str] = Field(default_factory=list)
    router_actions: List[str] = Field(default_factory=list)
    section_batches: List[List[str]] = Field(default_factory=list)
    state_commit_count: int = Field(default=0, ge=0)
    retrieval_tool_calls: int = Field(default=0, ge=0)
    research_rounds: Dict[str, int] = Field(default_factory=dict)
    writer_revisions: Dict[str, int] = Field(default_factory=dict)
    verifier_rounds: Dict[str, int] = Field(default_factory=dict)
    plan_revisions: List[str] = Field(default_factory=list)
    attempt_records: List[SectionAttemptRecordSchema] = Field(
        default_factory=list
    )
    stale_write_rejected: bool = False
    recovery_performed: bool = False
    reused_paragraph_ids: List[str] = Field(default_factory=list)
    checkpoint_event_recovery: bool = False
    group_status: Optional[str] = None


class StandaloneRunResultSchema(ContractSchema):
    schema_version: str = "agent_rag_v1_standalone_run_result_v1"
    task_id: str
    run_id: str
    task_status: TaskStatus
    document_version: str
    section_results: List[SectionGenerationReportSchema] = Field(
        default_factory=list
    )
    document_artifact_ref: str
    generation_report_ref: str
    document_artifact_path: str
    generation_report_path: str
    checkpoint_path: str
    event_log_path: str
    writer_event_log_path: str
    assembly_validation_passed: bool
    final_artifact_review_passed: bool
    markdown_only: Literal[True] = True
    generation_report: GenerationReportSchema
    final_state: MultiAgentSharedStateSchema
    trace: RuntimeTraceSchema
