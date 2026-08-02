"""Checkpoint-safe state contracts for the enterprise document business graph."""

from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
import json
from typing import Any, Mapping

from pydantic import ConfigDict, Field, model_validator
from agent.runtime.langgraph_state import LangGraphRuntimeState
from apps.enterprise_document.schemas.project_input_schema import ProjectInputSchema
from apps.enterprise_document.schemas.agent_rag_v1.document_closure import (
    AssemblyValidationResultSchema,
    ConflictRepairPlanSchema,
    DocumentVerificationResultSchema,
    FinalArtifactReviewResultSchema,
    GenerationReportSchema,
)
from apps.enterprise_document.schemas.agent_rag_v1.workflow import VersionDecisionSchema
from apps.enterprise_document.schemas.scheme_writer_schema import (
    AttemptStatus,
    DeliverableFinalResultSchema,
    DocumentAssemblyResultSchema,
    DocumentPlanSchema,
    DocumentValidationStateSchema,
    DocumentValidationStatus,
    HardGateResultSchema,
    PlanProjectionStatus,
    PlanStateSchema,
    RejectedDraftArtifactSchema,
    SchemeDraftSchema,
    SchemeGenerationOptionsSchema,
    SchemeSectionSchema,
    SchemeWriterInputSchema,
    SchemeWriterOutputSchema,
    SectionEvidenceBundleSchema,
    SectionLifecycleStatus,
    SectionStateSchema,
    VerificationProjectionStatus,
    WritingProjectionStatus,
)
from apps.enterprise_document.schemas.table_agent_schema import (
    StructuredFactSchema,
    TableAnalysisSchema,
)
from schemas.agent import AgentResultSchema
from schemas.citation import CitationSchema
from schemas.common import ErrorSchema, SchemaBase, WarningSchema
from schemas.human_review import (
    HumanReviewDecisionSchema,
    HumanReviewRequestSchema,
    HumanReviewStatus,
)
from schemas.rag import RAGContextSchema, RetrievedChunkSchema
from schemas.status import ExecutionStatus


SCHEME_GENERATION_GRAPH_STATE_VERSION = 1


@dataclass(frozen=True)
class BusinessNodeContract:
    """Explicit runtime authority for one top-level business node."""

    node_name: str
    timeout_seconds: float
    max_retries: int
    declared_read_paths: tuple[str, ...]
    declared_graph_write_paths: tuple[str, ...]
    declared_payload_write_paths: tuple[str, ...]
    commit_policy: str = "success_only"
    failure_graph_write_paths: tuple[str, ...] = ()
    failure_payload_write_paths: tuple[str, ...] = ()
    failure_commit_error_codes: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if not self.node_name.strip():
            raise ValueError("business node contract requires node_name")
        if self.timeout_seconds <= 0:
            raise ValueError("business node timeout_seconds must be positive")
        if self.max_retries < 0:
            raise ValueError("business node max_retries cannot be negative")
        if self.commit_policy not in {
            "success_only",
            "always",
            "allow_partial_on_failure",
        }:
            raise ValueError(
                f"unsupported business node commit_policy={self.commit_policy!r}"
            )


class SchemeGenerationGraphPayloadSchema(SchemaBase):
    """Strict business payload persisted between top-level graph nodes."""

    model_config = ConfigDict(extra="forbid")

    schema_version: str = "scheme_generation_graph_payload_v1"
    task_id: str
    run_id: str
    workflow_namespace: str = "scheme_generation:unversioned"
    started_at: str | None = None

    project_input: ProjectInputSchema | None = None
    table_analysis: TableAnalysisSchema | None = None
    structured_facts: list[StructuredFactSchema] = Field(default_factory=list)

    required_sections: list[str] = Field(default_factory=list)
    required_evidence_sections: list[str] = Field(default_factory=list)
    generation_options: SchemeGenerationOptionsSchema | None = None
    document_id: str | None = None
    document_plan: DocumentPlanSchema | None = None
    plan_state: PlanStateSchema = Field(default_factory=PlanStateSchema)
    section_states: dict[str, SectionStateSchema] = Field(default_factory=dict)
    scheme_input: SchemeWriterInputSchema | None = None

    document_rag_context: RAGContextSchema | None = None
    document_retrieved_chunks: list[RetrievedChunkSchema] = Field(
        default_factory=list
    )
    document_citations: list[CitationSchema] = Field(default_factory=list)
    document_rag_output: dict[str, Any] = Field(default_factory=dict)
    document_evidence_assessment: dict[str, Any] = Field(default_factory=dict)
    document_evidence_available: bool = False
    document_assessment_status: str = "not_assessed"
    document_tool_call_ids: list[str] = Field(default_factory=list)

    section_retrieval_enabled: bool = False
    corrective_retrieval_enabled: bool = False
    sections: list[SchemeSectionSchema] = Field(default_factory=list)
    section_evidence: list[SectionEvidenceBundleSchema] = Field(default_factory=list)
    all_retrieved_chunks: list[RetrievedChunkSchema] = Field(default_factory=list)
    all_citations: list[CitationSchema] = Field(default_factory=list)
    all_rag_outputs: list[dict[str, Any]] = Field(default_factory=list)

    document_verification: DocumentVerificationResultSchema | None = None
    conflict_repair_plan: ConflictRepairPlanSchema | None = None
    conflict_repair_history: list[dict[str, Any]] = Field(default_factory=list)
    incremental_document_verification: DocumentVerificationResultSchema | None = None
    assembly: DocumentAssemblyResultSchema | None = None
    assembly_validation: AssemblyValidationResultSchema | None = None
    final_artifact_review: FinalArtifactReviewResultSchema | None = None
    version_decision: VersionDecisionSchema | None = None
    document_version: str | None = None
    generation_report: GenerationReportSchema | None = None
    hard_gate: HardGateResultSchema | None = None
    document_validation: DocumentValidationStateSchema = Field(
        default_factory=DocumentValidationStateSchema
    )
    output_status: ExecutionStatus | None = None
    output_error: ErrorSchema | None = None
    output_warnings: list[WarningSchema] = Field(default_factory=list)
    artifact_route: str = "pending"

    scheme_writer_output: SchemeWriterOutputSchema | None = None
    generated_draft: SchemeDraftSchema | None = None
    rejected_artifact: RejectedDraftArtifactSchema | None = None
    deliverable_final_result: DeliverableFinalResultSchema | None = None

    terminal_agent_result: AgentResultSchema | None = None
    terminal_error: ErrorSchema | None = None
    failed_business_node: str | None = None
    executed_nodes: list[str] = Field(default_factory=list)
    business_revision: int = 0
    capture_completed: bool = False

    review_required: bool = False
    review_decision: str | None = None
    review_feedback: str | None = None
    review_requested_at: str | None = None
    review_resumed_at: str | None = None
    resume_count: int = 0
    human_review_required: bool = False
    human_review_recommended: bool = False
    human_review_status: HumanReviewStatus = HumanReviewStatus.NOT_REQUIRED
    human_review_request: HumanReviewRequestSchema | None = None
    human_review_decision: HumanReviewDecisionSchema | None = None

    @staticmethod
    def _section_lifecycle_status(
        status: ExecutionStatus,
    ) -> SectionLifecycleStatus:
        mapping = {
            ExecutionStatus.SUCCESS: SectionLifecycleStatus.ACCEPTED,
            ExecutionStatus.PARTIAL_SUCCESS: (
                SectionLifecycleStatus.PARTIALLY_ACCEPTED
            ),
            ExecutionStatus.FAILED: SectionLifecycleStatus.FAILED,
            ExecutionStatus.RETRYABLE_FAILED: SectionLifecycleStatus.FAILED,
            ExecutionStatus.CANCELLED: SectionLifecycleStatus.FAILED,
            ExecutionStatus.SKIPPED: SectionLifecycleStatus.SKIPPED,
        }
        return mapping.get(status, SectionLifecycleStatus.GENERATED)

    @staticmethod
    def _attempt_status(status: ExecutionStatus) -> AttemptStatus:
        mapping = {
            ExecutionStatus.SUCCESS: AttemptStatus.COMPLETED,
            ExecutionStatus.PARTIAL_SUCCESS: AttemptStatus.PARTIAL,
            ExecutionStatus.FAILED: AttemptStatus.FAILED,
            ExecutionStatus.RETRYABLE_FAILED: AttemptStatus.FAILED,
            ExecutionStatus.CANCELLED: AttemptStatus.CANCELLED,
            ExecutionStatus.SKIPPED: AttemptStatus.COMPLETED,
        }
        return mapping.get(status, AttemptStatus.RUNNING)

    @model_validator(mode="after")
    def _project_canonical_business_state(
        self,
    ) -> "SchemeGenerationGraphPayloadSchema":
        """Project canonical plan/section/gate fields into typed checkpoint state.

        The projection is one-way. Existing fields remain authoritative until the
        later Agent integration rounds explicitly change node ownership.
        """

        plan_state = self.plan_state
        section_states = dict(self.section_states)
        if self.document_plan is not None:
            plan_revision = max(1, plan_state.plan_revision)
            plan_state = plan_state.model_copy(
                update={
                    "status": PlanProjectionStatus.READY,
                    "canonical_plan_id": self.document_plan.plan_id,
                    "plan_revision": plan_revision,
                    "active_plan_revision_id": (
                        plan_state.active_plan_revision_id
                        or self.document_plan.plan_id
                    ),
                    "section_order": [
                        section.section_id
                        for section in self.document_plan.sections
                    ],
                },
                deep=True,
            )
            for plan_section in self.document_plan.sections:
                existing = section_states.get(plan_section.section_id)
                if existing is None:
                    existing = SectionStateSchema(
                        section_id=plan_section.section_id,
                        section_title=plan_section.section_title,
                        section_order=plan_section.section_order,
                        lifecycle_status=SectionLifecycleStatus.PLANNED,
                        execution_group=plan_section.execution_group,
                        can_run_in_parallel=plan_section.can_run_in_parallel,
                        requires_global_context=(
                            plan_section.requires_global_context
                        ),
                        hard_dependencies=list(plan_section.depends_on),
                    )
                attempt = existing.attempt.model_copy(
                    update={
                        "plan_revision": plan_revision,
                        "input_snapshot_version": (
                            existing.attempt.input_snapshot_version
                            or plan_state.input_snapshot_version
                        ),
                    },
                    deep=True,
                )
                lifecycle_status = existing.lifecycle_status
                if lifecycle_status == SectionLifecycleStatus.PENDING:
                    lifecycle_status = SectionLifecycleStatus.PLANNED
                section_states[plan_section.section_id] = existing.model_copy(
                    update={
                        "section_title": plan_section.section_title,
                        "section_order": plan_section.section_order,
                        "execution_group": plan_section.execution_group,
                        "can_run_in_parallel": (
                            plan_section.can_run_in_parallel
                        ),
                        "requires_global_context": (
                            plan_section.requires_global_context
                        ),
                        "hard_dependencies": list(plan_section.depends_on),
                        "attempt": attempt,
                        "lifecycle_status": lifecycle_status,
                    },
                    deep=True,
                )

        for section in self.sections:
            existing = section_states.get(section.section_id)
            if existing is None:
                existing = SectionStateSchema(
                    section_id=section.section_id,
                    section_title=section.section_title,
                    section_order=section.section_order,
                )
            attempt = existing.attempt.model_copy(
                update={
                    "status": self._attempt_status(section.status),
                    "started_at": (
                        existing.attempt.started_at or section.started_at
                    ),
                    "finished_at": section.finished_at,
                    "last_error_ref": (
                        section.error.error_id if section.error else None
                    ),
                },
                deep=True,
            )
            writing_status = (
                WritingProjectionStatus.COMPLETED
                if section.status == ExecutionStatus.SUCCESS
                else WritingProjectionStatus.PARTIAL
                if section.status == ExecutionStatus.PARTIAL_SUCCESS
                else WritingProjectionStatus.FAILED
                if section.status
                in {
                    ExecutionStatus.FAILED,
                    ExecutionStatus.RETRYABLE_FAILED,
                    ExecutionStatus.CANCELLED,
                }
                else existing.writing.status
            )
            writing = existing.writing.model_copy(
                update={
                    "status": writing_status,
                    "draft_version": max(
                        existing.writing.draft_version,
                        1 if section.content else 0,
                    ),
                    "generation_complete": bool(section.content),
                },
                deep=True,
            )
            verification = existing.verification
            if section.eval_result is not None:
                verification = verification.model_copy(
                    update={
                        "status": VerificationProjectionStatus.COMPLETED,
                        "recommended_action": (
                            "accept" if section.eval_result.passed else "reject"
                        ),
                        "checks": dict(section.eval_result.checks),
                        "metadata": {
                            **dict(verification.metadata),
                            "failures": list(section.eval_result.failures),
                            "warnings": list(section.eval_result.warnings),
                        },
                    },
                    deep=True,
                )
            section_states[section.section_id] = existing.model_copy(
                update={
                    "section_title": section.section_title,
                    "section_order": section.section_order,
                    "lifecycle_status": self._section_lifecycle_status(
                        section.status
                    ),
                    "attempt": attempt,
                    "writing": writing,
                    "verification": verification,
                },
                deep=True,
            )

        document_validation = self.document_validation
        closure_issues = list(document_validation.issues)
        closure_warnings = list(document_validation.warnings)
        closure_checks = dict(document_validation.checks)
        closure_updates: dict[str, Any] = {}
        if self.document_verification is not None:
            closure_updates.update(
                {
                    "status": (
                        DocumentValidationStatus.PASSED
                        if self.document_verification.passed
                        else DocumentValidationStatus.FAILED
                    ),
                    "verification_ref": (
                        f"checkpoint://document-verification/{self.run_id}/"
                        f"r{max(1, self.business_revision)}"
                    ),
                }
            )
            closure_checks["document_verification_passed"] = (
                self.document_verification.passed
            )
            closure_issues.extend(
                conflict.message
                for conflict in self.document_verification.conflicts
            )
            if self.document_verification.repair_plan.repair_tasks:
                closure_updates["conflict_repair_plan_ref"] = (
                    f"checkpoint://conflict-repair-plan/{self.run_id}/"
                    f"r{max(1, self.business_revision)}"
                )
        if self.incremental_document_verification is not None:
            closure_checks["incremental_document_verification_passed"] = (
                self.incremental_document_verification.passed
            )
            closure_issues.extend(
                conflict.message
                for conflict in self.incremental_document_verification.conflicts
            )
            if not self.incremental_document_verification.passed:
                closure_updates["status"] = DocumentValidationStatus.FAILED
        if self.assembly_validation is not None:
            closure_updates["assembly_validation_ref"] = (
                f"checkpoint://assembly-validation/{self.run_id}/"
                f"r{max(1, self.business_revision)}"
            )
            closure_checks.update(
                {
                    f"assembly:{name}": bool(value)
                    for name, value in self.assembly_validation.checks.items()
                }
            )
            closure_issues.extend(self.assembly_validation.failures)
            closure_warnings.extend(self.assembly_validation.warnings)
            if not self.assembly_validation.passed:
                closure_updates["status"] = DocumentValidationStatus.FAILED
        if self.final_artifact_review is not None:
            closure_updates["final_artifact_review_ref"] = (
                f"checkpoint://final-artifact-review/{self.run_id}/"
                f"r{max(1, self.business_revision)}"
            )
            closure_checks["final_artifact_review_passed"] = (
                self.final_artifact_review.passed
            )
            closure_issues.extend(self.final_artifact_review.issue_codes)
            if not self.final_artifact_review.passed:
                closure_updates["status"] = DocumentValidationStatus.FAILED
        if self.generation_report is not None:
            closure_updates["generation_report_ref"] = (
                f"checkpoint://generation-report/{self.run_id}/"
                f"{self.generation_report.document_version}"
            )
        if self.document_version is not None:
            closure_updates["document_version"] = self.document_version
        if closure_updates or closure_issues or closure_warnings or closure_checks:
            document_validation = document_validation.model_copy(
                update={
                    **closure_updates,
                    "validation_revision": max(
                        1,
                        document_validation.validation_revision,
                    ),
                    "issues": list(
                        dict.fromkeys(
                            item for item in closure_issues if str(item).strip()
                        )
                    ),
                    "warnings": list(
                        dict.fromkeys(
                            item for item in closure_warnings if str(item).strip()
                        )
                    ),
                    "checks": closure_checks,
                },
                deep=True,
            )
        if self.hard_gate is not None:
            document_validation = document_validation.model_copy(
                update={
                    "status": (
                        DocumentValidationStatus.PASSED
                        if self.hard_gate.passed
                        else DocumentValidationStatus.FAILED
                    ),
                    "validation_revision": max(
                        1,
                        document_validation.validation_revision,
                    ),
                    "issues": list(
                        dict.fromkeys(
                            item
                            for item in self.hard_gate.failures
                            if str(item).strip()
                        )
                    ),
                    "warnings": list(
                        dict.fromkeys(
                            item
                            for item in self.hard_gate.warnings
                            if str(item).strip()
                        )
                    ),
                    "checks": dict(self.hard_gate.checks),
                },
                deep=True,
            )
        elif self.review_required and self.review_decision is None:
            document_validation = document_validation.model_copy(
                update={"status": DocumentValidationStatus.WAITING_HUMAN},
                deep=True,
            )

        self.plan_state = PlanStateSchema.model_validate(
            plan_state.model_dump(mode="python", round_trip=True)
        )
        self.section_states = {
            section_id: SectionStateSchema.model_validate(
                state.model_dump(mode="python", round_trip=True)
            )
            for section_id, state in section_states.items()
        }
        self.document_validation = DocumentValidationStateSchema.model_validate(
            document_validation.model_dump(mode="python", round_trip=True)
        )
        return self


class SchemeGenerationGraphState(LangGraphRuntimeState, total=False):
    """LangGraph-owned envelope for the explicit enterprise document graph."""

    scheme_generation_state_version: int
    scheme_generation_payload: dict[str, Any]
    current_business_node: str
    business_route: str
    human_review_response: Any


class SchemeGenerationGraphStateCodec:
    """Serialize and validate the Phase 2 business state boundary."""

    @staticmethod
    def dump_payload(
        payload: SchemeGenerationGraphPayloadSchema,
    ) -> dict[str, Any]:
        normalized = SchemeGenerationGraphPayloadSchema.model_validate(
            payload.model_dump(mode="python", round_trip=True)
        )
        serialized = normalized.model_dump(mode="json", round_trip=True)
        return json.loads(json.dumps(serialized, ensure_ascii=False))

    @staticmethod
    def restore_payload(
        runtime_state: Mapping[str, Any],
    ) -> SchemeGenerationGraphPayloadSchema:
        actual_version = runtime_state.get("scheme_generation_state_version")
        if (
            type(actual_version) is not int
            or actual_version != SCHEME_GENERATION_GRAPH_STATE_VERSION
        ):
            raise ValueError(
                "unsupported scheme generation graph state version: "
                f"expected={SCHEME_GENERATION_GRAPH_STATE_VERSION} "
                f"actual={actual_version!r}"
            )
        raw_payload = runtime_state.get("scheme_generation_payload")
        if not isinstance(raw_payload, Mapping):
            raise ValueError(
                "scheme generation graph state requires scheme_generation_payload"
            )
        payload = SchemeGenerationGraphPayloadSchema.model_validate(
            deepcopy(dict(raw_payload))
        )
        runtime_task_id = runtime_state.get("task_id")
        runtime_run_id = runtime_state.get("run_id")
        if runtime_task_id != payload.task_id:
            raise ValueError(
                "scheme generation task_id identity mismatch: "
                f"expected={payload.task_id!r} actual={runtime_task_id!r}"
            )
        if runtime_run_id != payload.run_id:
            raise ValueError(
                "scheme generation run_id identity mismatch: "
                f"expected={payload.run_id!r} actual={runtime_run_id!r}"
            )
        return payload

    def build_initial_update(
        self,
        payload: SchemeGenerationGraphPayloadSchema,
    ) -> SchemeGenerationGraphState:
        return {
            "scheme_generation_state_version": (
                SCHEME_GENERATION_GRAPH_STATE_VERSION
            ),
            "scheme_generation_payload": self.dump_payload(payload),
            "current_business_node": "not_started",
            "business_route": "continue",
        }

    @staticmethod
    def assert_json_serializable(runtime_state: Mapping[str, Any]) -> None:
        json.dumps(runtime_state, ensure_ascii=False)
