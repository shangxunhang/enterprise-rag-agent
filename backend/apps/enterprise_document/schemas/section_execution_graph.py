"""Checkpoint-safe contracts for the single-section LangGraph subgraph."""

from __future__ import annotations

from copy import deepcopy
import json
from typing import Any, Mapping, TypedDict

from pydantic import ConfigDict, Field

from agent.runtime.graph_state import GraphStateSchema
from apps.enterprise_document.schemas.project_input_schema import ProjectInputSchema
from apps.enterprise_document.schemas.scheme_writer_schema import (
    SchemeSectionSchema,
    SectionEvidenceBundleSchema,
    SectionExecutionResultSchema,
    SectionPlanSchema,
)
from apps.enterprise_document.schemas.table_agent_schema import StructuredFactSchema
from schemas.citation import CitationSchema
from schemas.common import ErrorSchema, SchemaBase, WarningSchema
from schemas.rag import RAGContextSchema, RetrievedChunkSchema


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


class SectionExecutionGraphState(TypedDict, total=False):
    section_graph_state_version: int
    section_payload: dict[str, Any]
    current_section_node: str
    section_route: str


class SectionExecutionGraphStateCodec:
    """Serialize and validate section graph state identity."""

    @staticmethod
    def dump_payload(payload: SectionExecutionGraphPayloadSchema) -> dict[str, Any]:
        value = payload.model_dump(mode="json", round_trip=True)
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
