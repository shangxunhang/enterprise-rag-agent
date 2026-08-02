"""Checkpoint-safe contracts for the single-section LangGraph subgraph."""

from __future__ import annotations

from copy import deepcopy
import json
from typing import Any, Mapping, TypedDict

from pydantic import ConfigDict, Field, model_validator

from agent.runtime.graph_state import GraphStateSchema
from apps.enterprise_document.schemas.agent_rag_v1.claims import ClaimSchema
from apps.enterprise_document.schemas.agent_rag_v1.payloads import (
    ParagraphArtifactSchema,
    ResearchResultPayloadSchema,
    VerifierResultPayloadSchema,
    WriterResultPayloadSchema,
)
from apps.enterprise_document.schemas.agent_rag_v1.writer_pipeline import (
    GeneratedParagraphInputSchema,
    ParagraphPrecheckResultSchema,
)
from apps.enterprise_document.schemas.project_input_schema import ProjectInputSchema
from apps.enterprise_document.schemas.scheme_writer_schema import (
    AttemptStatus,
    ClaimProjectionStatus,
    ResearchProjectionStatus,
    SchemeSectionSchema,
    SectionEvidenceBundleSchema,
    SectionExecutionResultSchema,
    SectionLifecycleStatus,
    SectionPlanSchema,
    SectionStateSchema,
    VerificationProjectionStatus,
    WritingProjectionStatus,
)
from apps.enterprise_document.schemas.table_agent_schema import StructuredFactSchema
from schemas.citation import CitationSchema
from schemas.common import ErrorSchema, SchemaBase, WarningSchema
from schemas.rag import RAGContextSchema, RetrievedChunkSchema
from schemas.status import ExecutionStatus


SECTION_EXECUTION_GRAPH_STATE_VERSION = 1


class SectionGraphExecutionRequestSchema(SchemaBase):
    """Production section-subgraph input without live runtime objects."""

    model_config = ConfigDict(extra="forbid")

    schema_version: str = "section_graph_execution_request_v1"
    document_id: str
    project_input: ProjectInputSchema
    section_plan: SectionPlanSchema
    structured_facts: list[StructuredFactSchema] = Field(default_factory=list)
    previous_sections: list[SchemeSectionSchema] = Field(default_factory=list)
    document_rag_context: RAGContextSchema
    document_retrieved_chunks: list[RetrievedChunkSchema] = Field(default_factory=list)
    document_citations: list[CitationSchema] = Field(default_factory=list)
    document_evidence_assessment: dict[str, Any] = Field(default_factory=dict)
    document_tool_call_ids: list[str] = Field(default_factory=list)
    section_retrieval_enabled: bool = True
    corrective_retrieval_enabled: bool = True
    parallel_isolated: bool = False
    budget_policy_override: dict[str, Any] = Field(default_factory=dict)


class SectionExecutionGraphPayloadSchema(SchemaBase):
    """Serializable state for one section lifecycle.

    Services and live registries never enter this payload.  ``shared_state_payload``
    is a JSON projection restored into an isolated ``GraphStateSchema`` for each
    node and serialized again after validated mutations.
    """

    model_config = ConfigDict(extra="forbid")

    schema_version: str = "section_execution_graph_payload_v1"
    task_id: str
    run_id: str
    document_id: str
    project_input: ProjectInputSchema
    section_plan: SectionPlanSchema
    structured_facts: list[StructuredFactSchema] = Field(default_factory=list)
    previous_sections: list[SchemeSectionSchema] = Field(default_factory=list)

    shared_state_payload: dict[str, Any]
    document_rag_context: RAGContextSchema
    document_retrieved_chunks: list[RetrievedChunkSchema] = Field(default_factory=list)
    document_citations: list[CitationSchema] = Field(default_factory=list)
    document_evidence_assessment: dict[str, Any] = Field(default_factory=dict)
    document_tool_call_ids: list[str] = Field(default_factory=list)
    section_retrieval_enabled: bool = True
    corrective_retrieval_enabled: bool = True
    parallel_isolated: bool = False
    budget_policy_override: dict[str, Any] = Field(default_factory=dict)

    section_id: str | None = None
    section_title: str | None = None
    section_order: int | None = None
    total_sections: int = 0
    citation_required: bool = False
    section_state: SectionStateSchema | None = None

    citation_registry: list[CitationSchema] = Field(default_factory=list)
    active_context: RAGContextSchema | None = None
    active_chunks: list[RetrievedChunkSchema] = Field(default_factory=list)
    active_citations: list[CitationSchema] = Field(default_factory=list)
    active_query: str = ""
    active_scope: str = "document"
    active_assessment: dict[str, Any] = Field(default_factory=dict)
    tool_call_ids: list[str] = Field(default_factory=list)
    retrieved_chunks: list[RetrievedChunkSchema] = Field(default_factory=list)
    rag_outputs: list[dict[str, Any]] = Field(default_factory=list)
    retrieval_metadata: dict[str, Any] = Field(default_factory=dict)

    budget_state: dict[str, Any] = Field(default_factory=dict)
    research_agent_payloads: list[ResearchResultPayloadSchema] = Field(
        default_factory=list
    )
    writer_agent_payloads: list[WriterResultPayloadSchema] = Field(
        default_factory=list
    )
    verifier_agent_payloads: list[VerifierResultPayloadSchema] = Field(
        default_factory=list
    )

    # Round-6 Paragraph/Claim pipeline. These fields are persisted in the
    # existing Section checkpoint namespace; they are not another Shared State.
    paragraph_candidates: list[GeneratedParagraphInputSchema] = Field(
        default_factory=list
    )
    paragraph_artifacts: list[ParagraphArtifactSchema] = Field(
        default_factory=list
    )
    paragraph_precheck_results: list[ParagraphPrecheckResultSchema] = Field(
        default_factory=list
    )
    paragraph_claims: list[ClaimSchema] = Field(default_factory=list)
    paragraph_cursor: int = Field(default=0, ge=0)
    paragraph_blocked_id: str | None = None
    paragraph_pipeline_revision: int = Field(default=0, ge=0)

    recovery_count: int = 0
    generation_attempt: int = 1
    generation_check: dict[str, Any] = Field(default_factory=dict)
    self_rag_retrieval_rounds: list[dict[str, Any]] = Field(default_factory=list)
    pending_self_rag_round: dict[str, Any] | None = None
    evidence_blocked: bool = False
    budget_exhaustion_resource: str | None = None
    budget_exhaustion_limit: int | None = None

    section: SchemeSectionSchema | None = None
    evidence: SectionEvidenceBundleSchema | None = None
    result: SectionExecutionResultSchema | None = None
    warnings: list[WarningSchema] = Field(default_factory=list)
    error: ErrorSchema | None = None

    route: str = "prepare"
    current_node: str = "not_started"
    executed_nodes: list[str] = Field(default_factory=list)
    transition_count: int = 0
    completed: bool = False

    @model_validator(mode="after")
    def _validate_paragraph_pipeline(
        self,
    ) -> "SectionExecutionGraphPayloadSchema":
        candidate_ids = [
            str(item.paragraph_id or "").strip()
            for item in self.paragraph_candidates
        ]
        candidate_orders = [item.order for item in self.paragraph_candidates]
        if any(not item for item in candidate_ids):
            raise ValueError("paragraph candidates require stable ids")
        if len(candidate_ids) != len(set(candidate_ids)):
            raise ValueError("paragraph candidate ids must be unique")
        if len(candidate_orders) != len(set(candidate_orders)):
            raise ValueError("paragraph candidate orders must be unique")
        artifact_ids = [item.paragraph_id for item in self.paragraph_artifacts]
        artifact_orders = [item.order for item in self.paragraph_artifacts]
        if len(artifact_ids) != len(set(artifact_ids)):
            raise ValueError("paragraph artifact ids must be unique")
        if len(artifact_orders) != len(set(artifact_orders)):
            raise ValueError("paragraph artifact orders must be unique")
        if set(artifact_ids) - set(candidate_ids):
            raise ValueError("committed paragraph is not in the active plan")
        if self.paragraph_cursor > len(self.paragraph_candidates):
            raise ValueError("paragraph cursor exceeds candidate count")
        for artifact in self.paragraph_artifacts:
            artifact.verify_content_integrity()
        precheck_ids = [
            item.paragraph_id for item in self.paragraph_precheck_results
        ]
        if len(precheck_ids) != len(set(precheck_ids)):
            raise ValueError("paragraph precheck result ids must be unique")
        if set(precheck_ids) - set(artifact_ids):
            raise ValueError("paragraph precheck cannot precede artifact commit")
        claim_ids = [item.claim_id for item in self.paragraph_claims]
        if len(claim_ids) != len(set(claim_ids)):
            raise ValueError("paragraph claim ids must be unique")
        if self.paragraph_blocked_id is not None and (
            self.paragraph_blocked_id not in artifact_ids
        ):
            raise ValueError("blocked paragraph must already be committed")
        return self

    @staticmethod
    def _terminal_lifecycle(status: ExecutionStatus) -> SectionLifecycleStatus:
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
    def _terminal_attempt(status: ExecutionStatus) -> AttemptStatus:
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
    def _project_section_lifecycle(self) -> "SectionExecutionGraphPayloadSchema":
        """Build a typed section projection from the current canonical fields."""

        section_id = self.section_id or self.section_plan.section_id
        section_title = self.section_title or self.section_plan.section_title
        section_order = (
            self.section_order
            if self.section_order is not None
            else self.section_plan.section_order
        )
        state = self.section_state or SectionStateSchema(
            section_id=section_id,
            section_title=section_title,
            section_order=section_order,
            lifecycle_status=SectionLifecycleStatus.PLANNED,
            execution_group=self.section_plan.execution_group,
            can_run_in_parallel=self.section_plan.can_run_in_parallel,
            requires_global_context=self.section_plan.requires_global_context,
            hard_dependencies=list(self.section_plan.depends_on),
        )

        attempt_number = max(state.attempt.attempt_number, self.generation_attempt)
        attempt_status = state.attempt.status
        if self.completed and self.section is not None:
            attempt_status = self._terminal_attempt(self.section.status)
        elif self.current_node != "not_started":
            attempt_status = AttemptStatus.RUNNING
        attempt = state.attempt.model_copy(
            update={
                "attempt_id": (
                    state.attempt.attempt_id
                    or f"{self.run_id}:{section_id}:attempt:{attempt_number}"
                ),
                "attempt_number": attempt_number,
                "execution_id": (
                    state.attempt.execution_id
                    or f"{self.run_id}:{section_id}:execution"
                ),
                "plan_revision": max(1, state.attempt.plan_revision),
                "status": attempt_status,
                "started_at": (
                    state.attempt.started_at
                    or (self.section.started_at if self.section else None)
                ),
                "finished_at": (
                    self.section.finished_at
                    if self.completed and self.section is not None
                    else None
                ),
                "last_error_ref": (
                    self.error.error_id
                    if self.error is not None
                    else self.section.error.error_id
                    if self.section is not None and self.section.error is not None
                    else state.attempt.last_error_ref
                ),
            },
            deep=True,
        )

        research_status = state.research.status
        assessment_status = str(
            self.active_assessment.get("status") or ""
        ).strip().lower()
        if self.evidence_blocked or assessment_status == "insufficient":
            research_status = ResearchProjectionStatus.INSUFFICIENT
        elif self.current_node in {
            "retrieve_section_evidence",
            "retrieve_more",
            "corrective_retrieval",
        } and not self.completed:
            research_status = ResearchProjectionStatus.RUNNING
        elif assessment_status == "sufficient" or self.active_chunks:
            research_status = ResearchProjectionStatus.COMPLETED
        contract_sha = None
        if self.active_context is not None:
            contract_sha = str(
                (self.active_context.extra or {}).get(
                    "evidence_contract_sha256"
                )
                or ""
            ) or None
        research = state.research.model_copy(
            update={
                "status": research_status,
                "evidence_contract_sha256": (
                    contract_sha or state.research.evidence_contract_sha256
                ),
                "research_rounds": max(
                    state.research.research_rounds,
                    self.recovery_count,
                ),
                "tool_call_ids": list(dict.fromkeys(self.tool_call_ids)),
                "recommended_action": (
                    state.research.recommended_action
                    or "block_generation"
                    if research_status == ResearchProjectionStatus.INSUFFICIENT
                    else state.research.recommended_action
                    or "write"
                    if research_status == ResearchProjectionStatus.COMPLETED
                    else state.research.recommended_action
                ),
                "assessment": dict(self.active_assessment),
            },
            deep=True,
        )

        paragraph_pipeline_active = bool(
            self.paragraph_candidates
            or self.paragraph_artifacts
            or self.paragraph_precheck_results
            or self.paragraph_pipeline_revision
        )
        if paragraph_pipeline_active:
            writing_status = state.writing.status
            draft_version = max(
                state.writing.draft_version,
                self.generation_attempt,
            )
            paragraph_refs = {
                item.paragraph_id: item.content_ref
                for item in self.paragraph_artifacts
            }
            paragraph_versions = {
                item.paragraph_id: item.paragraph_version
                for item in self.paragraph_artifacts
            }
            expected_paragraph_ids = [
                str(item.paragraph_id)
                for item in self.paragraph_candidates
                if item.paragraph_id is not None
            ]
            generation_complete = bool(expected_paragraph_ids) and (
                [item.paragraph_id for item in self.paragraph_artifacts]
                == expected_paragraph_ids
                and len(self.paragraph_precheck_results)
                == len(self.paragraph_artifacts)
                and self.paragraph_blocked_id is None
            )
            if self.current_node in {
                "generate_section",
                "prepare_paragraph_pipeline",
                "persist_paragraph",
                "precheck_paragraph",
            } and not self.completed:
                writing_status = WritingProjectionStatus.RUNNING
            if self.paragraph_blocked_id is not None:
                writing_status = WritingProjectionStatus.PARTIAL
            elif generation_complete:
                writing_status = WritingProjectionStatus.COMPLETED
            elif self.section is not None and self.section.status in {
                ExecutionStatus.FAILED,
                ExecutionStatus.RETRYABLE_FAILED,
                ExecutionStatus.CANCELLED,
            }:
                writing_status = WritingProjectionStatus.FAILED
            generation_cursor = (
                self.paragraph_blocked_id
                or (
                    self.paragraph_artifacts[-1].paragraph_id
                    if self.paragraph_artifacts
                    else state.writing.generation_cursor
                )
            )
            writing = state.writing.model_copy(
                update={
                    "status": writing_status,
                    "draft_ref": (
                        f"checkpoint://section-draft/{section_id}/v{draft_version}"
                        if self.paragraph_artifacts
                        else state.writing.draft_ref
                    ),
                    "draft_version": draft_version,
                    "paragraph_refs": paragraph_refs,
                    "paragraph_versions": paragraph_versions,
                    "expected_paragraph_ids": expected_paragraph_ids,
                    "generation_complete": generation_complete,
                    "generation_cursor": generation_cursor,
                    "metadata": {
                        **dict(state.writing.metadata),
                        "paragraph_pipeline_revision": (
                            self.paragraph_pipeline_revision
                        ),
                        "artifact_authority": (
                            "section_langgraph_checkpoint"
                        ),
                        "blocked_paragraph_id": self.paragraph_blocked_id,
                    },
                },
                deep=True,
            )
        else:
            writing_status = state.writing.status
            generation_complete = state.writing.generation_complete
            draft_version = state.writing.draft_version
            if self.current_node == "generate_section" and not self.completed:
                writing_status = WritingProjectionStatus.RUNNING
            if self.section is not None:
                generation_complete = bool(self.section.content)
                draft_version = max(draft_version, self.generation_attempt)
                if self.section.status == ExecutionStatus.SUCCESS:
                    writing_status = WritingProjectionStatus.COMPLETED
                elif self.section.status == ExecutionStatus.PARTIAL_SUCCESS:
                    writing_status = WritingProjectionStatus.PARTIAL
                elif self.section.status in {
                    ExecutionStatus.FAILED,
                    ExecutionStatus.RETRYABLE_FAILED,
                    ExecutionStatus.CANCELLED,
                }:
                    writing_status = WritingProjectionStatus.FAILED
            writing = state.writing.model_copy(
                update={
                    "status": writing_status,
                    "draft_version": draft_version,
                    "generation_complete": generation_complete,
                },
                deep=True,
            )

        if paragraph_pipeline_active:
            claim_status = state.claims.status
            if self.current_node == "precheck_paragraph" and not self.completed:
                claim_status = ClaimProjectionStatus.RUNNING
            elif self.paragraph_claims or generation_complete:
                claim_status = ClaimProjectionStatus.COMPLETED
            elif writing_status == WritingProjectionStatus.FAILED:
                claim_status = ClaimProjectionStatus.FAILED
            claim_ids = [item.claim_id for item in self.paragraph_claims]
            claims = state.claims.model_copy(
                update={
                    "status": claim_status,
                    "claim_ids": claim_ids,
                    "active_claim_ids": claim_ids,
                    "claim_set_version": self.paragraph_pipeline_revision,
                    "extraction_ref": (
                        f"checkpoint://section-claims/{section_id}/"
                        f"v{self.paragraph_pipeline_revision}"
                        if self.paragraph_claims
                        else state.claims.extraction_ref
                    ),
                    "metadata": {
                        **dict(state.claims.metadata),
                        "paragraph_precheck_count": len(
                            self.paragraph_precheck_results
                        ),
                    },
                },
                deep=True,
            )
        else:
            claims = state.claims

        verification = state.verification
        checks = {
            str(name): bool(value)
            for name, value in self.generation_check.items()
        }
        if self.section is not None and self.section.eval_result is not None:
            checks = dict(self.section.eval_result.checks)
        if checks:
            action = (
                verification.recommended_action
                or "research_more"
                if bool(self.generation_check.get("need_retrieve_more"))
                else verification.recommended_action
                or "accept"
                if all(checks.values())
                else verification.recommended_action
                or "rewrite"
            )
            verification = verification.model_copy(
                update={
                    "status": VerificationProjectionStatus.COMPLETED,
                    "execution_mode": "section_verify",
                    "recommended_action": action,
                    "verification_rounds": max(
                        1,
                        verification.verification_rounds,
                    ),
                    "checks": checks,
                },
                deep=True,
            )
        elif self.current_node == "evaluate_generation" and not self.completed:
            verification = verification.model_copy(
                update={
                    "status": VerificationProjectionStatus.RUNNING,
                    "execution_mode": "section_verify",
                },
                deep=True,
            )

        lifecycle_status = state.lifecycle_status
        if self.completed and self.section is not None:
            lifecycle_status = self._terminal_lifecycle(self.section.status)
        else:
            node_status = {
                "not_started": SectionLifecycleStatus.PLANNED,
                "prepare_section": SectionLifecycleStatus.PLANNED,
                "retrieve_section_evidence": SectionLifecycleStatus.RESEARCHING,
                "retrieve_more": SectionLifecycleStatus.RESEARCHING,
                "corrective_retrieval": SectionLifecycleStatus.RESEARCHING,
                "evaluate_evidence_gate": (
                    SectionLifecycleStatus.BLOCKED
                    if self.evidence_blocked
                    else SectionLifecycleStatus.EVIDENCE_READY
                ),
                "generate_section": SectionLifecycleStatus.GENERATING,
                "prepare_paragraph_pipeline": SectionLifecycleStatus.GENERATING,
                "persist_paragraph": SectionLifecycleStatus.GENERATING,
                "precheck_paragraph": (
                    SectionLifecycleStatus.PAUSED_FOR_PRECHECK
                    if self.paragraph_blocked_id
                    else SectionLifecycleStatus.GENERATING
                ),
                "evaluate_generation": SectionLifecycleStatus.VERIFYING,
                "finalize_section": (
                    self._terminal_lifecycle(self.section.status)
                    if self.section is not None
                    else SectionLifecycleStatus.FAILED
                ),
            }
            lifecycle_status = node_status.get(
                self.current_node,
                lifecycle_status,
            )

        state = state.model_copy(
            update={
                "section_id": section_id,
                "section_title": section_title,
                "section_order": section_order,
                "lifecycle_status": lifecycle_status,
                "execution_group": self.section_plan.execution_group,
                "can_run_in_parallel": self.section_plan.can_run_in_parallel,
                "requires_global_context": (
                    self.section_plan.requires_global_context
                ),
                "hard_dependencies": list(self.section_plan.depends_on),
                "attempt": attempt,
                "research": research,
                "writing": writing,
                "claims": claims,
                "verification": verification,
            },
            deep=True,
        )
        state = SectionStateSchema.model_validate(
            state.model_dump(mode="python", round_trip=True)
        )
        self.section_state = state
        if self.result is not None:
            self.result = self.result.model_copy(
                update={"section_state": state},
                deep=True,
            )
        return self


class SectionExecutionGraphState(TypedDict, total=False):
    section_graph_state_version: int
    section_payload: dict[str, Any]
    current_section_node: str
    section_route: str


class SectionExecutionGraphStateCodec:
    """Serialize and validate section graph state identity."""

    @staticmethod
    def dump_payload(payload: SectionExecutionGraphPayloadSchema) -> dict[str, Any]:
        normalized = SectionExecutionGraphPayloadSchema.model_validate(
            payload.model_dump(mode="python", round_trip=True)
        )
        value = normalized.model_dump(mode="json", round_trip=True)
        return json.loads(json.dumps(value, ensure_ascii=False))

    @staticmethod
    def dump_graph_state(state: GraphStateSchema) -> dict[str, Any]:
        value = state.model_dump(mode="json", round_trip=True)
        return json.loads(json.dumps(value, ensure_ascii=False))

    @staticmethod
    def restore_graph_state(payload: SectionExecutionGraphPayloadSchema) -> GraphStateSchema:
        state = GraphStateSchema.model_validate(deepcopy(payload.shared_state_payload))
        if state.task_id != payload.task_id:
            raise ValueError(
                "section graph task_id identity mismatch: "
                f"expected={payload.task_id!r} actual={state.task_id!r}"
            )
        if state.run_id != payload.run_id:
            raise ValueError(
                "section graph run_id identity mismatch: "
                f"expected={payload.run_id!r} actual={state.run_id!r}"
            )
        return state

    @staticmethod
    def restore_payload(
        runtime_state: Mapping[str, Any],
    ) -> SectionExecutionGraphPayloadSchema:
        actual = runtime_state.get("section_graph_state_version")
        if type(actual) is not int or actual != SECTION_EXECUTION_GRAPH_STATE_VERSION:
            raise ValueError(
                "unsupported section graph state version: "
                f"expected={SECTION_EXECUTION_GRAPH_STATE_VERSION} actual={actual!r}"
            )
        raw = runtime_state.get("section_payload")
        if not isinstance(raw, Mapping):
            raise ValueError("section graph state requires section_payload")
        payload = SectionExecutionGraphPayloadSchema.model_validate(deepcopy(dict(raw)))
        return payload

    def build_initial_state(
        self,
        payload: SectionExecutionGraphPayloadSchema,
    ) -> SectionExecutionGraphState:
        return {
            "section_graph_state_version": SECTION_EXECUTION_GRAPH_STATE_VERSION,
            "section_payload": self.dump_payload(payload),
            "current_section_node": "not_started",
            "section_route": "prepare",
        }

    @staticmethod
    def assert_json_serializable(state: Mapping[str, Any]) -> None:
        json.dumps(state, ensure_ascii=False)
