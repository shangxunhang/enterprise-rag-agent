"""Checkpoint-safe state contracts for the enterprise document business graph."""

from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
import json
from typing import Any, Mapping

from pydantic import ConfigDict, Field
from agent.runtime.langgraph_state import LangGraphRuntimeState
from apps.enterprise_document.schemas.project_input_schema import ProjectInputSchema
from apps.enterprise_document.schemas.scheme_writer_schema import (
    DeliverableFinalResultSchema,
    DocumentAssemblyResultSchema,
    DocumentPlanSchema,
    HardGateResultSchema,
    RejectedDraftArtifactSchema,
    SchemeDraftSchema,
    SchemeGenerationOptionsSchema,
    SchemeSectionSchema,
    SchemeWriterInputSchema,
    SchemeWriterOutputSchema,
    SectionEvidenceBundleSchema,
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

    assembly: DocumentAssemblyResultSchema | None = None
    hard_gate: HardGateResultSchema | None = None
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
        serialized = payload.model_dump(mode="json", round_trip=True)
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
