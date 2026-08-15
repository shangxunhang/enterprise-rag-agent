"""Explicit, runtime-safe LangGraph ownership of enterprise document workflow."""

from __future__ import annotations

from dataclasses import dataclass
import re
import time
from typing import Any, Callable, Iterable
from uuid import uuid4

from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.graph import END, START, StateGraph
from langgraph.types import Command, interrupt

from agent.runtime.business_node_execution_runner import (
    WorkflowNodeExecutionOutcome,
    WorkflowNodeExecutionRunner,
)
from agent.runtime.graph_state import GraphStateSchema
from agent.runtime.graph_state_ops import (
    GraphStateApplier,
    GraphStateDiffer,
    GraphStateWriteContract,
    StateWriteContractViolation,
)
from agent.runtime.langgraph_state import (
    LangGraphRuntimeStateCodec,
    WorkflowNamespaceCheckpointSaver,
)
from agent.runtime.workflow_schema import WorkflowDefinitionSchema
from apps.enterprise_document.quality.budget import (
    TaskDrainingBudget,
    activate_task_draining_budget,
)
from apps.enterprise_document.schemas.scheme_generation_graph import (
    SchemeGenerationGraphPayloadSchema,
    SchemeGenerationGraphState,
    SchemeGenerationGraphStateCodec,
)
from apps.enterprise_document.services.project_input_normalization import (
    ProjectInputNormalizationUseCase,
)
from apps.enterprise_document.workflows.section_execution_graph import (
    SectionExecutionGraph,
)
from apps.enterprise_document.services.scheme_writer.stage_service import (
    SchemeGenerationStageFailure,
    SchemeGenerationStageService,
)
from contracts.observability import TraceSink
from contracts.workflow_engine import WorkflowEngineCapabilities
from core.retry_policy import RetryDecisionPolicy, classify_failure
from core.runtime.recovery import (
    DurableRecoveryContext,
    activate_durable_recovery,
)
from core.runtime.side_effects import (
    InMemorySideEffectCommitStore,
    SideEffectCommitStore,
)
from observability.trace_context import activate_span, current_span, new_span
from schemas.agent import AgentResultSchema
from schemas.common import ErrorSchema
from schemas.failure import FailureCategory
from schemas.graph import (
    GraphNodeExecutionRecordSchema,
    GraphNodeInputSchema,
    GraphNodeOutputSchema,
    WorkflowEngineResultSchema,
    stable_graph_hash,
)
from schemas.human_review import (
    HumanReviewDecisionSchema,
    HumanReviewRequestSchema,
    HumanReviewStatus,
)
from schemas.status import ExecutionStatus, is_failure
from schemas.workflow_stream import WorkflowStreamEventSchema


PHASE2_BUSINESS_NODE_NAMES = (
    "normalize_project_input",
    "plan_document",
    "retrieve_document_evidence",
    "initialize_generation",
    "execute_sections",
    "document_verify",
    "repair_document_conflicts",
    "incremental_document_reverify",
    "assemble_document",
    "validate_document_assembly",
    "final_artifact_review",
    "decide_document_version",
    "evaluate_document_gate",
    "build_generation_report",
    "persist_deliverable",
    "persist_rejected",
    "finalize_workflow_result",
    "finalize_workflow_failure",
)

_SUCCESS_SEQUENCE = [
    "normalize_project_input",
    "plan_document",
    "retrieve_document_evidence",
    "initialize_generation",
    "execute_sections",
    "document_verify",
    "repair_document_conflicts",
    "incremental_document_reverify",
    "assemble_document",
    "validate_document_assembly",
    "final_artifact_review",
    "decide_document_version",
    "evaluate_document_gate",
    "build_generation_report",
    "persist_deliverable",
    "finalize_workflow_result",
]
_GATE_ROUTES = {
    "deliverable": "persist_deliverable",
    "rejected": "persist_rejected",
    "technical_failure": "finalize_workflow_failure",
}


@dataclass(frozen=True)
class BusinessNodeContract:
    """Declared state boundary and runtime policy for one business stage."""

    node_name: str
    read_keys: tuple[str, ...] = ()
    graph_write_paths: tuple[str, ...] = ()
    payload_write_paths: tuple[str, ...] = ()
    timeout_seconds: float = 1000.0
    max_retries: int = 0


@dataclass(frozen=True)
class _ValidatedBusinessStage:
    graph_state: GraphStateSchema
    payload: SchemeGenerationGraphPayloadSchema
    graph_delta: Any
    payload_changed_paths: tuple[str, ...]


def phase2_success_node_sequence() -> list[str]:
    return list(_SUCCESS_SEQUENCE)


def phase2_gate_routes() -> dict[str, str]:
    return dict(_GATE_ROUTES)


def phase2_failure_route() -> list[str]:
    return ["finalize_workflow_failure", END]


def phase2_checkpoint_identity(
    workflow: WorkflowDefinitionSchema,
    *,
    run_id: str,
) -> dict[str, Any]:
    return dict(
        LangGraphRuntimeStateCodec().checkpoint_config(
            workflow,
            run_id=run_id,
        )
    )


def _default_node_contracts() -> dict[str, BusinessNodeContract]:
    return {
        "normalize_project_input": BusinessNodeContract(
            node_name="normalize_project_input",
            read_keys=("requirements", "task", "user_input"),
            graph_write_paths=(
                "context_bundle.business.project_input",
                "context_bundle.business.source_materials",
                "context_bundle.business.missing_information",
                "context_bundle.business.conflicting_information",
                "context_bundle.business.manual_boundaries",
                "contexts.project_input",
                "contexts.table_agent_output",
                "structured_facts",
            ),
            payload_write_paths=(
                "project_input",
                "table_analysis",
                "structured_facts",
            ),
        ),
        "plan_document": BusinessNodeContract(
            node_name="plan_document",
            read_keys=("project_input", "structured_facts"),
            payload_write_paths=(
                "started_at",
                "project_input",
                "table_analysis",
                "structured_facts",
                "required_sections",
                "required_evidence_sections",
                "generation_options",
                "document_id",
                "document_plan",
                "plan_state",
                "scheme_input",
            ),
        ),
        "retrieve_document_evidence": BusinessNodeContract(
            node_name="retrieve_document_evidence",
            read_keys=("project_input", "document_plan"),
            payload_write_paths=(
                "document_evidence_bundle",
                "document_tool_call_ids",
                "section_retrieval_enabled",
                "corrective_retrieval_enabled",
                "all_citations",
                "all_rag_outputs",
            ),
        ),
        "initialize_generation": BusinessNodeContract(
            node_name="initialize_generation",
            read_keys=("document_id", "required_sections"),
            graph_write_paths=("context_bundle.generation",),
            payload_write_paths=("sections", "section_evidence"),
        ),
        "execute_sections": BusinessNodeContract(
            node_name="execute_sections",
            timeout_seconds=1800.0,
            read_keys=(
                "document_plan",
                "document_evidence_bundle",
                "structured_facts",
            ),
            graph_write_paths=(
                "context_bundle.generation",
                "generated_outputs",
                "errors",
                "context_bundle.runtime.errors",
                "warnings",
                "context_bundle.runtime.warnings",
            ),
            payload_write_paths=(
                "sections",
                "section_states",
                "section_evidence",
                "all_citations",
                "all_rag_outputs",
                "document_validation",
            ),
        ),
        "document_verify": BusinessNodeContract(
            node_name="document_verify",
            read_keys=("sections", "section_states", "project_input"),
            payload_write_paths=(
                "document_verification",
                "conflict_repair_plan",
                "document_validation",
            ),
        ),
        "repair_document_conflicts": BusinessNodeContract(
            node_name="repair_document_conflicts",
            read_keys=("document_verification", "section_states"),
            payload_write_paths=(
                "conflict_repair_plan",
                "conflict_repair_history",
                "document_validation",
            ),
        ),
        "incremental_document_reverify": BusinessNodeContract(
            node_name="incremental_document_reverify",
            read_keys=(
                "conflict_repair_history",
                "sections",
                "section_states",
            ),
            payload_write_paths=(
                "incremental_document_verification",
                "document_validation",
            ),
        ),
        "assemble_document": BusinessNodeContract(
            node_name="assemble_document",
            read_keys=("sections", "section_evidence", "all_citations"),
            payload_write_paths=(
                "assembly",
                "generated_draft",
                "all_citations",
            ),
        ),
        "validate_document_assembly": BusinessNodeContract(
            node_name="validate_document_assembly",
            read_keys=("assembly", "required_sections"),
            payload_write_paths=(
                "assembly_validation",
                "document_validation",
            ),
        ),
        "final_artifact_review": BusinessNodeContract(
            node_name="final_artifact_review",
            read_keys=("assembly", "assembly_validation", "project_input"),
            payload_write_paths=(
                "final_artifact_review",
                "document_validation",
            ),
        ),
        "decide_document_version": BusinessNodeContract(
            node_name="decide_document_version",
            read_keys=("project_input",),
            payload_write_paths=(
                "version_decision",
                "document_version",
                "document_validation",
            ),
        ),
        "evaluate_document_gate": BusinessNodeContract(
            node_name="evaluate_document_gate",
            read_keys=("assembly", "generation_options"),
            graph_write_paths=(
                "errors",
                "context_bundle.runtime.errors",
                "warnings",
                "context_bundle.runtime.warnings",
            ),
            payload_write_paths=(
                "hard_gate",
                "output_status",
                "output_error",
                "output_warnings",
                "artifact_route",
            ),
        ),
        "build_generation_report": BusinessNodeContract(
            node_name="build_generation_report",
            read_keys=(
                "hard_gate",
                "document_version",
                "sections",
                "section_states",
            ),
            payload_write_paths=(
                "generation_report",
                "document_validation",
            ),
        ),
        "persist_deliverable": BusinessNodeContract(
            node_name="persist_deliverable",
            read_keys=(
                "assembly",
                "hard_gate",
                "output_status",
                "generation_report",
                "version_decision",
                "document_version",
            ),
            graph_write_paths=(
                "contexts.scheme_writer_input",
                "contexts.scheme_writer_output",
                "contexts.rag_tool_output",
                "generated_outputs",
                "generated_draft",
                "rejected_artifact",
                "deliverable_final_result",
            ),
            payload_write_paths=(
                "scheme_writer_output",
                "generated_draft",
                "rejected_artifact",
                "deliverable_final_result",
                "release_publication",
                "capture_completed",
            ),
        ),
        "persist_rejected": BusinessNodeContract(
            node_name="persist_rejected",
            read_keys=("assembly", "hard_gate", "output_status"),
            graph_write_paths=(
                "contexts.scheme_writer_input",
                "contexts.scheme_writer_output",
                "contexts.rag_tool_output",
                "generated_outputs",
                "generated_draft",
                "rejected_artifact",
                "deliverable_final_result",
            ),
            payload_write_paths=(
                "scheme_writer_output",
                "generated_draft",
                "rejected_artifact",
                "deliverable_final_result",
                "capture_completed",
            ),
        ),
        "apply_human_review": BusinessNodeContract(
            node_name="apply_human_review",
            read_keys=("hard_gate", "artifact_route"),
            payload_write_paths=(
                "review_required",
                "review_decision",
                "review_feedback",
                "review_requested_at",
                "review_resumed_at",
                "resume_count",
                "human_review_required",
                "human_review_recommended",
                "human_review_status",
                "human_review_decision",
                "artifact_route",
                "output_status",
                "output_error",
            ),
        ),
        "finalize_workflow_result": BusinessNodeContract(
            node_name="finalize_workflow_result",
            read_keys=("scheme_writer_output", "artifact_route"),
            graph_write_paths=(
                "status",
                "final_result",
                "context_bundle.runtime.status",
            ),
            payload_write_paths=("terminal_agent_result", "terminal_error"),
        ),
        "finalize_workflow_failure": BusinessNodeContract(
            node_name="finalize_workflow_failure",
            read_keys=("terminal_error", "failed_business_node"),
            graph_write_paths=(
                "status",
                "final_result",
                "errors",
                "context_bundle.runtime.errors",
                "context_bundle.runtime.status",
                "generated_draft",
                "rejected_artifact",
                "deliverable_final_result",
            ),
            payload_write_paths=(
                "output_status",
                "terminal_agent_result",
                "terminal_error",
                "deliverable_final_result",
                "rejected_artifact",
                "artifact_route",
            ),
        ),
    }


class EnterpriseDocumentWorkflowEngine:
    """Application-owned workflow engine with shared Phase 1 runtime safety."""

    engine_name = "enterprise_document_langgraph_engine"
    engine_version = "phase8-recovery-retirement-v1"
    capabilities = WorkflowEngineCapabilities(
        resume=True,
        inspect=True,
        cancel=True,
        stream_events=True,
        async_stream_events=False,
    )

    def __init__(
        self,
        *,
        stage_service: SchemeGenerationStageService,
        normalization_use_case: ProjectInputNormalizationUseCase | None = None,
        run_trace_recorder: TraceSink | None = None,
        checkpointer: BaseCheckpointSaver | None = None,
        side_effect_commit_store: SideEffectCommitStore | None = None,
        runtime_state_codec: LangGraphRuntimeStateCodec | None = None,
        business_state_codec: SchemeGenerationGraphStateCodec | None = None,
        node_execution_runner: WorkflowNodeExecutionRunner | None = None,
        node_contracts: dict[str, BusinessNodeContract] | None = None,
    ) -> None:
        self.stage_service = stage_service
        self.normalization_use_case = (
            normalization_use_case or ProjectInputNormalizationUseCase()
        )
        self.trace_sink = run_trace_recorder
        self.checkpointer = checkpointer or InMemorySaver()
        self._langgraph_checkpointer = WorkflowNamespaceCheckpointSaver(
            self.checkpointer
        )
        self.side_effect_commit_store = (
            side_effect_commit_store or InMemorySideEffectCommitStore()
        )
        self.runtime_state_codec = runtime_state_codec or LangGraphRuntimeStateCodec()
        self.business_state_codec = (
            business_state_codec or SchemeGenerationGraphStateCodec()
        )
        self.differ = GraphStateDiffer()
        self.applier = GraphStateApplier()
        self.write_contract = GraphStateWriteContract()
        self.retry_policy = RetryDecisionPolicy()
        self.node_execution_runner = (
            node_execution_runner
            or WorkflowNodeExecutionRunner(
                side_effect_commit_store=self.side_effect_commit_store,
                now_iso=self.stage_service.runtime_support.now_iso,
            )
        )
        self.node_contracts = dict(node_contracts or _default_node_contracts())
        coordinator = getattr(
            self.stage_service,
            "section_execution_coordinator",
            None,
        )
        self.section_execution_graph = (
            SectionExecutionGraph(
                coordinator=coordinator,
                checkpointer=self.checkpointer,
            )
            if coordinator is not None
            else None
        )
        attach_section_graph = getattr(
            self.stage_service,
            "attach_section_execution_graph",
            None,
        )
        if self.section_execution_graph is not None and callable(attach_section_graph):
            attach_section_graph(self.section_execution_graph)

    @property
    def active_worker_count(self) -> int:
        return self.node_execution_runner.active_worker_count

    def cancel_active_workers(self, reason: str = "workflow_shutdown") -> int:
        return self.node_execution_runner.cancel_active_workers(reason)

    def wait_for_idle(self, timeout_seconds: float | None = None) -> bool:
        return self.node_execution_runner.wait_for_idle(timeout_seconds)

    def defer_until_idle(self, callback: Callable[[], None]) -> bool:
        return self.node_execution_runner.defer_until_idle(callback)

    def _restore(
        self,
        runtime_state: SchemeGenerationGraphState,
        workflow: WorkflowDefinitionSchema,
    ) -> tuple[GraphStateSchema, SchemeGenerationGraphPayloadSchema]:
        graph_state = self.runtime_state_codec.restore_graph_state(
            runtime_state,
            workflow=workflow,
        )
        payload = self.business_state_codec.restore_payload(runtime_state)
        return graph_state, payload

    def _record_started(
        self,
        *,
        node_name: str,
        graph_state: GraphStateSchema,
        payload: SchemeGenerationGraphPayloadSchema,
        workflow: WorkflowDefinitionSchema,
    ):
        handle = new_span(
            run_id=graph_state.run_id,
            span_name=f"business_node:{node_name}",
            span_kind="internal",
            parent=current_span(),
        )
        if self.trace_sink is not None:
            namespace = self.runtime_state_codec.workflow_namespace(workflow)
            self.trace_sink.record(
                task_id=graph_state.task_id,
                run_id=graph_state.run_id,
                event_type="agent_started",
                component_type="agent",
                component_name=node_name,
                workflow_id=workflow.workflow_id,
                workflow_version=workflow.workflow_version,
                step_id=node_name,
                step_name=node_name,
                agent_name=node_name,
                caller=workflow.workflow_id,
                callee=node_name,
                status=ExecutionStatus.RUNNING.value,
                phase="start",
                trace_id=handle.trace_id,
                span_id=handle.span_id,
                parent_span_id=handle.parent_span_id,
                span_name=handle.span_name,
                span_kind=handle.span_kind,
                started_at=handle.started_at,
                input_summary={
                    "node_name": node_name,
                    "task_id": graph_state.task_id,
                    "run_id": graph_state.run_id,
                    "workflow_id": workflow.workflow_id,
                    "workflow_version": workflow.workflow_version,
                    "graph_state_schema": graph_state.schema_version,
                    "graph_revision": graph_state.graph_revision,
                    "business_revision": payload.business_revision,
                    "checkpoint_thread_id": graph_state.run_id,
                    "checkpoint_namespace": namespace,
                },
                tags=["trace_v2", "agent", "workflow_step", "phase2_business_node"],
            )
        return handle

    def _record_finished(
        self,
        *,
        node_name: str,
        graph_state: GraphStateSchema,
        payload: SchemeGenerationGraphPayloadSchema,
        workflow: WorkflowDefinitionSchema,
        handle,
        status: ExecutionStatus,
        error: ErrorSchema | None,
    ) -> None:
        if self.trace_sink is None:
            return
        self.trace_sink.record(
            task_id=graph_state.task_id,
            run_id=graph_state.run_id,
            event_type="agent_finished",
            component_type="agent",
            component_name=node_name,
            workflow_id=workflow.workflow_id,
            workflow_version=workflow.workflow_version,
            step_id=node_name,
            step_name=node_name,
            agent_name=node_name,
            caller=workflow.workflow_id,
            callee=node_name,
            status=status.value,
            error_message=error.message if error is not None else None,
            phase="error" if error is not None else "end",
            trace_id=handle.trace_id,
            span_id=handle.span_id,
            parent_span_id=handle.parent_span_id,
            span_name=handle.span_name,
            span_kind=handle.span_kind,
            started_at=handle.started_at,
            latency_ms=handle.latency_ms(),
            output_summary={
                "node_name": node_name,
                "status": status.value,
                "error_code": error.error_code if error is not None else None,
                "graph_revision": graph_state.graph_revision,
                "business_revision": payload.business_revision,
                "checkpoint_thread_id": graph_state.run_id,
                "checkpoint_namespace": self.runtime_state_codec.workflow_namespace(
                    workflow
                ),
            },
            tags=["trace_v2", "agent", "workflow_step", "phase2_business_node"],
        )

    @staticmethod
    def _read_value(
        graph_state: GraphStateSchema,
        payload: SchemeGenerationGraphPayloadSchema,
        key: str,
    ) -> tuple[bool, Any]:
        if hasattr(payload, key):
            value = getattr(payload, key)
            return value is not None, value
        if hasattr(graph_state, key):
            value = getattr(graph_state, key)
            return value is not None, value
        return False, None

    def _build_node_input(
        self,
        *,
        workflow: WorkflowDefinitionSchema,
        contract: BusinessNodeContract,
        graph_state: GraphStateSchema,
        payload: SchemeGenerationGraphPayloadSchema,
    ) -> GraphNodeInputSchema:
        values: dict[str, Any] = {}
        missing: list[str] = []
        for key in contract.read_keys:
            found, value = self._read_value(graph_state, payload, key)
            values[key] = {
                "present": found,
                "value_sha256": stable_graph_hash(value) if found else None,
            }
            if not found:
                missing.append(key)
        hash_payload = {
            "node_id": contract.node_name,
            "workflow_id": workflow.workflow_id,
            "state_revision": graph_state.graph_revision,
            "declared_read_keys": list(contract.read_keys),
            "values": values,
            "missing_keys": missing,
        }
        return GraphNodeInputSchema(
            node_id=contract.node_name,
            node_name=contract.node_name,
            node_type="business_stage",
            target_name=contract.node_name,
            workflow_id=workflow.workflow_id,
            workflow_version=workflow.workflow_version,
            task_id=graph_state.task_id,
            run_id=graph_state.run_id,
            state_revision=graph_state.graph_revision,
            declared_read_keys=list(contract.read_keys),
            values=values,
            missing_keys=missing,
            input_sha256=stable_graph_hash(hash_payload),
            metadata={
                "phase2_business_node": True,
                "business_payload_schema": payload.schema_version,
            },
        )

    def _validate_stage(
        self,
        *,
        node_name: str,
        contract: BusinessNodeContract,
        before_graph: GraphStateSchema,
        before_payload: SchemeGenerationGraphPayloadSchema,
        stage: Callable[
            [GraphStateSchema, SchemeGenerationGraphPayloadSchema],
            SchemeGenerationGraphPayloadSchema,
        ],
    ) -> _ValidatedBusinessStage:
        working_graph = before_graph.model_copy(deep=True)
        working_payload = before_payload.model_copy(deep=True)
        returned_payload = stage(working_graph, working_payload)
        if not isinstance(returned_payload, SchemeGenerationGraphPayloadSchema):
            raise TypeError(
                f"business stage {node_name} returned unsupported payload type: "
                f"{type(returned_payload).__name__}"
            )
        graph_delta = self.differ.diff(
            node_id=node_name,
            before=before_graph,
            after=working_graph,
            declared_write_keys=(),
            declared_write_paths=contract.graph_write_paths,
            discard_legacy_engine_owned_mutations=False,
        )
        before_payload_data = before_payload.model_dump(mode="python")
        after_payload_data = returned_payload.model_dump(mode="python")
        payload_changed_paths = self.differ.changed_paths(
            before_payload_data,
            after_payload_data,
        )
        self.write_contract.validate(
            changed_paths=payload_changed_paths,
            allowed_paths=contract.payload_write_paths,
        )
        committed_graph = before_graph.model_copy(deep=True)
        self.applier.apply(committed_graph, graph_delta)
        return _ValidatedBusinessStage(
            graph_state=committed_graph,
            payload=returned_payload.model_copy(deep=True),
            graph_delta=graph_delta,
            payload_changed_paths=tuple(sorted(set(payload_changed_paths))),
        )

    def _error_from_outcome(
        self,
        *,
        node_name: str,
        outcome: WorkflowNodeExecutionOutcome,
    ) -> ErrorSchema:
        if outcome.kind == "timeout":
            return self.stage_service.runtime_support.error(
                "WORKFLOW_NODE_TIMEOUT",
                f"business node {node_name} exceeded its timeout",
                node=node_name,
                retryable=self.retry_policy.should_retry(
                    FailureCategory.TIMEOUT,
                    worker_still_active=outcome.worker_still_active,
                ),
                failure_category=FailureCategory.TIMEOUT,
                requires_human=True,
                user_message=f"业务节点 {node_name} 执行超时。",
            )
        if outcome.kind == "cancelled":
            return self.stage_service.runtime_support.error(
                "WORKFLOW_NODE_CANCELLED",
                str(outcome.exception or "workflow execution cancelled"),
                node=node_name,
                retryable=False,
                failure_category=FailureCategory.CANCELLED,
                requires_human=True,
                user_message=f"业务节点 {node_name} 已取消。",
            )
        if outcome.kind == "no_result":
            return self.stage_service.runtime_support.error(
                "WORKFLOW_NODE_NO_RESULT",
                f"business node {node_name} returned no result",
                node=node_name,
                retryable=True,
                failure_category=FailureCategory.TRANSIENT,
                requires_human=True,
            )
        if outcome.kind == "external_commit_error":
            return self.stage_service.runtime_support.error(
                "WORKFLOW_EXTERNAL_COMMIT_FAILED",
                str(outcome.exception or "external commit failed"),
                node=node_name,
                retryable=True,
                failure_category=FailureCategory.TRANSIENT,
                requires_human=True,
            )
        exc = outcome.exception
        if isinstance(exc, StateWriteContractViolation):
            return self.stage_service.runtime_support.error(
                "STATE_WRITE_CONTRACT_VIOLATION",
                exc,
                node=node_name,
                retryable=False,
                failure_category=FailureCategory.VALIDATION,
                requires_human=True,
            )
        if isinstance(exc, SchemeGenerationStageFailure):
            return exc.error
        if isinstance(exc, Exception):
            category = classify_failure(exc)
            return self.stage_service.runtime_support.error(
                "SCHEME_GENERATION_STAGE_FAILED",
                exc,
                node=node_name,
                retryable=self.retry_policy.should_retry(category),
                failure_category=category,
                requires_human=True,
                user_message="方案生成顶层业务节点执行失败。",
            )
        return self.stage_service.runtime_support.error(
            "WORKFLOW_NODE_CANCELLED",
            str(exc or "workflow execution cancelled"),
            node=node_name,
            retryable=False,
            failure_category=FailureCategory.CANCELLED,
            requires_human=True,
        )

    @staticmethod
    def _failure_payload(
        payload: SchemeGenerationGraphPayloadSchema,
        *,
        node_name: str,
        error: ErrorSchema,
    ) -> SchemeGenerationGraphPayloadSchema:
        return payload.model_copy(
            update={
                "terminal_error": error,
                "failed_business_node": node_name,
                "artifact_route": "technical_failure",
            },
            deep=True,
        )

    def _stage_status(
        self,
        *,
        node_name: str,
        payload: SchemeGenerationGraphPayloadSchema,
        error: ErrorSchema | None,
    ) -> ExecutionStatus:
        if error is not None:
            return (
                ExecutionStatus.RETRYABLE_FAILED
                if error.retryable
                else ExecutionStatus.FAILED
            )
        if payload.terminal_agent_result is not None:
            return payload.terminal_agent_result.status
        if node_name == "evaluate_document_gate" and payload.output_status is not None:
            return payload.output_status
        return ExecutionStatus.SUCCESS

    def _stage_agent_result(
        self,
        *,
        node_name: str,
        graph_state: GraphStateSchema,
        payload: SchemeGenerationGraphPayloadSchema,
        status: ExecutionStatus,
        error: ErrorSchema | None,
        outcome: WorkflowNodeExecutionOutcome,
    ) -> AgentResultSchema:
        if node_name in {
            "finalize_workflow_result",
            "finalize_workflow_failure",
        } and payload.terminal_agent_result is not None:
            result = payload.terminal_agent_result.model_copy(deep=True)
            result.latency_ms = outcome.latency_ms
            return result
        return AgentResultSchema(
            result_id=f"result_{graph_state.run_id}_{node_name}",
            task_id=graph_state.task_id,
            run_id=graph_state.run_id,
            agent_name=node_name,
            agent_type="sub_agent",
            status=status,
            result_type="business_stage_result",
            result={
                "node_name": node_name,
                "business_route": payload.artifact_route,
                "business_revision": payload.business_revision,
            },
            warnings=list(payload.output_warnings) if node_name == "evaluate_document_gate" else [],
            error=error,
            error_message=error.message if error is not None else None,
            started_at=outcome.started_at,
            finished_at=outcome.finished_at,
            latency_ms=outcome.latency_ms,
            need_human_review=bool(error is not None),
            metadata={
                "phase2_business_node": True,
                "worker_still_active": outcome.worker_still_active,
            },
        )

    def _append_execution_record(
        self,
        *,
        runtime_state: SchemeGenerationGraphState,
        node_input: GraphNodeInputSchema,
        outcome: WorkflowNodeExecutionOutcome,
        validated: _ValidatedBusinessStage | None,
        graph_state: GraphStateSchema,
        payload: SchemeGenerationGraphPayloadSchema,
        status: ExecutionStatus,
        error: ErrorSchema | None,
        workflow: WorkflowDefinitionSchema,
        attempt_history: list[dict[str, Any]],
    ) -> dict[str, Any]:
        node_name = node_input.node_id
        if validated is None:
            graph_delta = self.differ.diff(
                node_id=node_name,
                before=graph_state,
                after=graph_state.model_copy(deep=True),
                declared_write_keys=(),
                declared_write_paths=(),
                discard_legacy_engine_owned_mutations=False,
            )
            committed_graph = graph_state.model_copy(deep=True)
            self.applier.apply(committed_graph, graph_delta)
            payload_changed_paths: tuple[str, ...] = ()
        else:
            graph_delta = validated.graph_delta
            committed_graph = validated.graph_state.model_copy(deep=True)
            payload_changed_paths = validated.payload_changed_paths

        executed_nodes = [*payload.executed_nodes, node_name]
        committed_payload = payload.model_copy(
            update={
                "executed_nodes": executed_nodes,
                "business_revision": payload.business_revision + 1,
            },
            deep=True,
        )
        committed_graph.current_node_id = node_name
        if node_name not in committed_graph.completed_node_ids:
            committed_graph.completed_node_ids.append(node_name)

        result = self._stage_agent_result(
            node_name=node_name,
            graph_state=committed_graph,
            payload=committed_payload,
            status=status,
            error=error,
            outcome=outcome,
        )
        node_output = GraphNodeOutputSchema(
            node_id=node_name,
            node_name=node_name,
            node_type="business_stage",
            target_name=node_name,
            status=status,
            result=result,
            state_delta=graph_delta,
            started_at=outcome.started_at,
            finished_at=outcome.finished_at,
            latency_ms=outcome.latency_ms,
            warnings=list(result.warnings),
            error=error,
            metadata={
                "phase2_business_node": True,
                "payload_changed_paths": list(payload_changed_paths),
                "timeout_seconds": self.node_contracts[node_name].timeout_seconds,
                "execution_control": outcome.execution_control.metadata(),
                "external_commit_summary": outcome.external_commit_summary,
                "worker_still_active": outcome.worker_still_active,
                "attempt_count": len(attempt_history),
                "retry_count": max(0, len(attempt_history) - 1),
                "attempt_history": list(attempt_history),
            },
        )
        committed_graph.node_history.append(
            GraphNodeExecutionRecordSchema(
                node_id=node_name,
                node_name=node_name,
                target_name=node_name,
                status=status,
                input_sha256=node_input.input_sha256,
                delta_sha256=graph_delta.delta_sha256,
                base_revision=graph_delta.base_revision,
                next_revision=graph_delta.next_revision,
                changed_paths=list(graph_delta.changed_paths),
                started_at=outcome.started_at,
                finished_at=outcome.finished_at,
                latency_ms=outcome.latency_ms,
                metadata={"phase2_business_node": True},
            )
        )

        route = "technical_failure" if error is not None else "continue"
        if node_name in {
            "evaluate_document_gate",
            "build_generation_report",
            "apply_human_review",
        } and error is None:
            route = committed_payload.artifact_route
        update: dict[str, Any] = {
            "graph_state_payload": self.runtime_state_codec.dump_graph_state(
                committed_graph
            ),
            "scheme_generation_payload": self.business_state_codec.dump_payload(
                committed_payload
            ),
            "node_inputs": [
                *runtime_state.get("node_inputs", []),
                self.runtime_state_codec.dump_node_input(node_input),
            ],
            "node_outputs": [
                *runtime_state.get("node_outputs", []),
                self.runtime_state_codec.dump_node_output(node_output),
            ],
            "node_results": [
                *runtime_state.get("node_results", []),
                self.runtime_state_codec.dump_agent_result(result),
            ],
            "current_business_node": node_name,
            "business_route": route,
            "transition_count": int(runtime_state.get("transition_count", 0)) + 1,
            "retry_count": int(runtime_state.get("retry_count", 0))
            + max(0, len(attempt_history) - 1),
        }
        if node_name in {
            "finalize_workflow_result",
            "finalize_workflow_failure",
        }:
            update.update(
                {
                    "workflow_complete": True,
                    "terminal_reason": (
                        "business_complete"
                        if node_name == "finalize_workflow_result"
                        else "technical_failure"
                    ),
                    "terminal_failure_status": (
                        status.value
                        if status
                        not in {
                            ExecutionStatus.SUCCESS,
                            ExecutionStatus.PARTIAL_SUCCESS,
                        }
                        else None
                    ),
                    "next_node": "__workflow_end__",
                }
            )
        return update

    def _run_stage(
        self,
        runtime_state: SchemeGenerationGraphState,
        *,
        workflow: WorkflowDefinitionSchema,
        node_name: str,
        stage: Callable[
            [GraphStateSchema, SchemeGenerationGraphPayloadSchema],
            SchemeGenerationGraphPayloadSchema,
        ],
    ) -> dict[str, Any]:
        graph_state, payload = self._restore(runtime_state, workflow)
        before_graph = graph_state.model_copy(deep=True)
        before_payload = payload.model_copy(deep=True)
        contract = self.node_contracts[node_name]
        node_input = self._build_node_input(
            workflow=workflow,
            contract=contract,
            graph_state=before_graph,
            payload=before_payload,
        )
        handle = self._record_started(
            node_name=node_name,
            graph_state=before_graph,
            payload=before_payload,
            workflow=workflow,
        )
        print(f"[Workflow] START step={node_name} target={node_name}", flush=True)

        validated: _ValidatedBusinessStage | None = None
        error: ErrorSchema | None = None
        committed_payload = before_payload
        committed_graph = before_graph
        attempt_history: list[dict[str, Any]] = []
        max_attempts = max(1, int(contract.max_retries) + 1)
        outcome: WorkflowNodeExecutionOutcome | None = None

        with activate_span(handle):
            for attempt in range(1, max_attempts + 1):
                outcome = self.node_execution_runner.execute(
                    lambda _control: self._validate_stage(
                        node_name=node_name,
                        contract=contract,
                        before_graph=before_graph,
                        before_payload=before_payload,
                        stage=stage,
                    ),
                    execution_id=(
                        f"{before_graph.run_id}:{node_name}:{attempt}:"
                        f"{time.monotonic_ns()}"
                    ),
                    commit_scope_id=(
                        f"{before_graph.run_id}:{node_name}:"
                        f"{node_input.input_sha256}"
                    ),
                    timeout_seconds=contract.timeout_seconds,
                    thread_name=(
                        f"business-{node_name}-{before_graph.run_id}-attempt-{attempt}"
                    ),
                    attempt_succeeded=lambda value: isinstance(
                        value, _ValidatedBusinessStage
                    ),
                )
                if outcome.kind == "ok" and isinstance(
                    outcome.value, _ValidatedBusinessStage
                ):
                    validated = outcome.value
                    committed_payload = validated.payload
                    committed_graph = validated.graph_state
                    attempt_history.append(
                        {
                            "attempt": attempt,
                            "kind": outcome.kind,
                            "status": ExecutionStatus.SUCCESS.value,
                            "latency_ms": outcome.latency_ms,
                            "retry": False,
                        }
                    )
                    break

                attempt_error = self._error_from_outcome(
                    node_name=node_name,
                    outcome=outcome,
                )
                retry_allowed = bool(
                    attempt < max_attempts
                    and attempt_error.retryable
                    and outcome.kind != "cancelled"
                    and not outcome.worker_still_active
                )
                attempt_history.append(
                    {
                        "attempt": attempt,
                        "kind": outcome.kind,
                        "status": (
                            ExecutionStatus.RETRYABLE_FAILED.value
                            if attempt_error.retryable
                            else ExecutionStatus.FAILED.value
                        ),
                        "error_code": attempt_error.error_code,
                        "latency_ms": outcome.latency_ms,
                        "retry": retry_allowed,
                    }
                )
                if retry_allowed:
                    print(
                        f"[Workflow] RETRY step={node_name} "
                        f"attempt={attempt + 1}/{max_attempts} "
                        f"error={attempt_error.error_code}",
                        flush=True,
                    )
                    continue

                error = attempt_error
                committed_payload = self._failure_payload(
                    before_payload,
                    node_name=node_name,
                    error=error,
                )
                committed_graph = before_graph
                break

        if outcome is None:  # pragma: no cover - max_attempts is always >= 1
            raise RuntimeError(f"business node {node_name} executed no attempts")

        status = self._stage_status(
            node_name=node_name,
            payload=committed_payload,
            error=error,
        )
        update = self._append_execution_record(
            runtime_state=runtime_state,
            node_input=node_input,
            outcome=outcome,
            validated=validated,
            graph_state=committed_graph,
            payload=committed_payload,
            status=status,
            error=error,
            workflow=workflow,
            attempt_history=attempt_history,
        )
        restored_payload = self.business_state_codec.restore_payload(
            {**runtime_state, **update}
        )
        restored_graph = self.runtime_state_codec.restore_graph_state(
            {**runtime_state, **update},
            workflow=workflow,
        )
        self._record_finished(
            node_name=node_name,
            graph_state=restored_graph,
            payload=restored_payload,
            workflow=workflow,
            handle=handle,
            status=status,
            error=error or (
                restored_payload.output_error
                if node_name == "evaluate_document_gate"
                else None
            ),
        )
        print(
            f"[Workflow] END   step={node_name} target={node_name} "
            f"status={status.value} revision={restored_graph.graph_revision}",
            flush=True,
        )
        return update

    def _execute_sections_with_task_budget(
        self,
        graph_state: GraphStateSchema,
        payload: SchemeGenerationGraphPayloadSchema,
    ) -> SchemeGenerationGraphPayloadSchema:
        """Run Section scheduling under one checkpoint-projected draining budget."""

        project_input = payload.project_input
        if project_input is None:
            raise ValueError("execute_sections requires project_input")
        policy = dict(
            project_input.generation_requirements.extra.get("task_budget") or {}
        )
        validation_metadata = dict(payload.document_validation.metadata)
        budget = TaskDrainingBudget.from_snapshot(
            validation_metadata.get("task_budget"),
            policy_metadata={"task_budget": policy},
        )
        with activate_task_draining_budget(budget):
            updated = self.stage_service.execute_sections(graph_state, payload)
        validation = updated.document_validation.model_copy(
            update={
                "metadata": {
                    **dict(updated.document_validation.metadata),
                    "task_budget": budget.snapshot(),
                    "budget_integration_round": "round8",
                }
            },
            deep=True,
        )
        return updated.model_copy(
            update={"document_validation": validation},
            deep=True,
        )

    def _normalize_project_input(
        self,
        graph_state: GraphStateSchema,
        payload: SchemeGenerationGraphPayloadSchema,
    ) -> SchemeGenerationGraphPayloadSchema:
        output = self.normalization_use_case.execute(graph_state)
        return payload.model_copy(
            update={
                "project_input": output.project_input,
                "table_analysis": output.table_analysis,
                "structured_facts": list(output.structured_facts),
            },
            deep=True,
        )

    @staticmethod
    def _route_after_stage(runtime_state: SchemeGenerationGraphState) -> str:
        return (
            "technical_failure"
            if runtime_state.get("business_route") == "technical_failure"
            else "continue"
        )

    def _route_after_gate(
        self,
        runtime_state: SchemeGenerationGraphState,
    ) -> str:
        payload = self.business_state_codec.restore_payload(runtime_state)
        require_review = bool(
            payload.project_input
            and payload.project_input.generation_requirements.extra.get(
                "require_human_approval",
                False,
            )
        )
        if require_review and payload.review_decision is None:
            return "human_review"
        route = str(runtime_state.get("business_route") or "")
        return route if route in _GATE_ROUTES else "technical_failure"

    @staticmethod
    def _active_input_snapshot(
        payload: SchemeGenerationGraphPayloadSchema,
    ) -> str:
        snapshot = str(payload.plan_state.input_snapshot_version or "").strip()
        if snapshot:
            return snapshot
        if payload.project_input is None:
            return "snapshot:uninitialized"
        return "sha256:" + stable_graph_hash(
            payload.project_input.model_dump(mode="json", round_trip=True)
        )

    def _human_review_interrupt(
        self,
        runtime_state: SchemeGenerationGraphState,
        *,
        workflow: WorkflowDefinitionSchema,
    ) -> dict[str, Any]:
        graph_state, payload = self._restore(runtime_state, workflow)
        review_request = HumanReviewRequestSchema(
            task_id=graph_state.task_id,
            run_id=graph_state.run_id,
            workflow_id=workflow.workflow_id,
            workflow_version=workflow.workflow_version,
            document_id=payload.document_id,
            hard_gate_passed=bool(payload.hard_gate and payload.hard_gate.passed),
            artifact_route=payload.artifact_route,
            warnings=[
                item.model_dump(mode="json") for item in payload.output_warnings
            ],
            requested_at=self.stage_service.runtime_support.now_iso(),
            metadata={
                "input_snapshot_version": self._active_input_snapshot(payload),
                "plan_revision": payload.plan_state.plan_revision,
                "resume_stage": "persist_artifact",
                "recovery_integration_round": "round8",
            },
        )
        response = interrupt(review_request.model_dump(mode="json"))
        return {
            "human_review_response": response,
            "current_business_node": "human_review_interrupt",
            "business_route": "review_received",
        }

    def _apply_human_review(
        self,
        graph_state: GraphStateSchema,
        payload: SchemeGenerationGraphPayloadSchema,
        response: Any,
    ) -> SchemeGenerationGraphPayloadSchema:
        raw = dict(response) if isinstance(response, dict) else {}
        raw_metadata = dict(raw.get("metadata") or {})
        raw_decision = str(
            raw.get("decision")
            or ("approved" if raw.get("approved") is True else "")
            or ("rejected" if raw.get("approved") is False else "")
        ).strip().lower()
        cancelled = raw_decision in {"cancel", "cancelled", "canceled"}
        expected_snapshot = self._active_input_snapshot(payload)
        if not cancelled:
            provided_snapshot = raw.get("input_snapshot_version")
            if provided_snapshot is None:
                provided_snapshot = raw_metadata.get("input_snapshot_version")
            if not isinstance(provided_snapshot, str) or not provided_snapshot:
                raise ValueError("human review input_snapshot_version is required")
            if provided_snapshot != expected_snapshot:
                raise ValueError("human review decision snapshot is stale")
            provided_task_id = raw.get("task_id")
            if not isinstance(provided_task_id, str) or not provided_task_id:
                raise ValueError("human review task_id is required")
            if provided_task_id != graph_state.task_id:
                raise ValueError("human review task identity mismatch")
            provided_run_id = raw.get("run_id")
            if not isinstance(provided_run_id, str) or not provided_run_id:
                raise ValueError("human review run_id is required")
            if provided_run_id != graph_state.run_id:
                raise ValueError("human review run identity mismatch")
        approved = raw_decision in {"approve", "approved", "accept", "accepted"}
        decision = (
            "approved"
            if approved
            else ("cancelled" if cancelled else "rejected")
        )
        feedback = str(raw.get("feedback") or raw.get("comment") or "").strip() or None
        decided_at = self.stage_service.runtime_support.now_iso()
        review_decision = HumanReviewDecisionSchema(
            decision=(
                HumanReviewStatus.APPROVED
                if approved
                else (
                    HumanReviewStatus.CANCELLED
                    if cancelled
                    else HumanReviewStatus.REJECTED
                )
            ),
            feedback=feedback,
            decided_at=decided_at,
            reviewer_id=str(raw.get("reviewer_id") or "").strip() or None,
            metadata={
                **raw_metadata,
                "input_snapshot_version": expected_snapshot,
                "resume_stage": "persist_artifact",
                "recovery_integration_round": "round8",
            },
        )
        artifact_route = payload.artifact_route
        output_status = payload.output_status
        output_error = payload.output_error
        if not approved:
            artifact_route = "rejected"
            output_status = (
                ExecutionStatus.CANCELLED
                if cancelled
                else ExecutionStatus.FAILED
            )
            output_error = self.stage_service.runtime_support.error(
                (
                    "WORKFLOW_CANCELLED"
                    if cancelled
                    else "HUMAN_REVIEW_REJECTED"
                ),
                feedback
                or (
                    "workflow cancelled during human review"
                    if cancelled
                    else "human reviewer rejected the generated draft"
                ),
                node="apply_human_review",
                retryable=False,
                failure_category=(
                    FailureCategory.CANCELLED
                    if cancelled
                    else FailureCategory.BUSINESS_QUALITY
                ),
                requires_human=not cancelled,
                user_message=(
                    "工作流已在人工审核阶段取消。"
                    if cancelled
                    else "人工审核未通过，当前草稿不能作为正式交付物。"
                ),
            )
        return payload.model_copy(
            update={
                "review_required": True,
                "review_decision": decision,
                "review_feedback": feedback,
                "review_requested_at": (
                    payload.review_requested_at
                    or self.stage_service.runtime_support.now_iso()
                ),
                "review_resumed_at": decided_at,
                "resume_count": payload.resume_count + 1,
                "human_review_required": False,
                "human_review_recommended": False,
                "human_review_status": review_decision.decision,
                "human_review_decision": review_decision,
                "artifact_route": artifact_route,
                "output_status": output_status,
                "output_error": output_error,
            },
            deep=True,
        )

    @staticmethod
    def _route_after_review(runtime_state: SchemeGenerationGraphState) -> str:
        route = str(runtime_state.get("business_route") or "")
        return route if route in _GATE_ROUTES else "technical_failure"

    def build_graph(self, workflow: WorkflowDefinitionSchema):
        """Build the explicit application graph; legacy step topology is ignored."""

        builder = StateGraph(SchemeGenerationGraphState)

        def business_node(node_name: str, stage: Callable):
            return lambda state: self._run_stage(
                state,
                workflow=workflow,
                node_name=node_name,
                stage=stage,
            )

        builder.add_node(
            "normalize_project_input",
            business_node("normalize_project_input", self._normalize_project_input),
        )
        builder.add_node(
            "plan_document",
            business_node(
                "plan_document", self.stage_service.prepare_document_plan
            ),
        )
        builder.add_node(
            "retrieve_document_evidence",
            business_node(
                "retrieve_document_evidence",
                self.stage_service.retrieve_document_evidence,
            ),
        )
        builder.add_node(
            "initialize_generation",
            business_node(
                "initialize_generation", self.stage_service.initialize_generation
            ),
        )
        builder.add_node(
            "execute_sections",
            business_node(
                "execute_sections",
                self._execute_sections_with_task_budget,
            ),
        )
        builder.add_node(
            "document_verify",
            business_node("document_verify", self.stage_service.document_verify),
        )
        builder.add_node(
            "repair_document_conflicts",
            business_node(
                "repair_document_conflicts",
                self.stage_service.repair_document_conflicts,
            ),
        )
        builder.add_node(
            "incremental_document_reverify",
            business_node(
                "incremental_document_reverify",
                self.stage_service.incremental_document_reverify,
            ),
        )
        builder.add_node(
            "assemble_document",
            business_node("assemble_document", self.stage_service.assemble_document),
        )
        builder.add_node(
            "validate_document_assembly",
            business_node(
                "validate_document_assembly",
                self.stage_service.validate_document_assembly,
            ),
        )
        builder.add_node(
            "final_artifact_review",
            business_node(
                "final_artifact_review",
                self.stage_service.final_artifact_review,
            ),
        )
        builder.add_node(
            "decide_document_version",
            business_node(
                "decide_document_version",
                self.stage_service.decide_document_version,
            ),
        )
        builder.add_node(
            "evaluate_document_gate",
            business_node(
                "evaluate_document_gate", self.stage_service.evaluate_document_gate
            ),
        )
        builder.add_node(
            "build_generation_report",
            business_node(
                "build_generation_report",
                self.stage_service.build_generation_report,
            ),
        )
        # 人工审核节点
        builder.add_node(
            "human_review_interrupt",
            lambda state: self._human_review_interrupt(
                state,
                workflow=workflow,
            ),
        )
        #  人工审核节点
        builder.add_node(
            "apply_human_review",
            lambda state: self._run_stage(
                state,
                workflow=workflow,
                node_name="apply_human_review",
                stage=lambda graph_state, payload: self._apply_human_review(
                    graph_state,
                    payload,
                    state.get("human_review_response"),
                ),
            ),
        )
        builder.add_node(
            "persist_deliverable",
            business_node(
                "persist_deliverable", self.stage_service.persist_deliverable
            ),
        )
        builder.add_node(
            "persist_rejected",
            business_node("persist_rejected", self.stage_service.persist_rejected),
        )
        builder.add_node(
            "finalize_workflow_result",
            business_node(
                "finalize_workflow_result",
                self.stage_service.finalize_workflow_result,
            ),
        )
        builder.add_node(
            "finalize_workflow_failure",
            business_node(
                "finalize_workflow_failure",
                self.stage_service.finalize_workflow_failure,
            ),
        )

        builder.add_edge(START, "normalize_project_input")
        linear_routes = [
            ("normalize_project_input", "plan_document"),
            ("plan_document", "retrieve_document_evidence"),
            ("retrieve_document_evidence", "initialize_generation"),
            ("initialize_generation", "execute_sections"),
            ("execute_sections", "document_verify"),
            ("document_verify", "repair_document_conflicts"),
            (
                "repair_document_conflicts",
                "incremental_document_reverify",
            ),
            ("incremental_document_reverify", "assemble_document"),
            ("assemble_document", "validate_document_assembly"),
            ("validate_document_assembly", "final_artifact_review"),
            ("final_artifact_review", "decide_document_version"),
            ("decide_document_version", "evaluate_document_gate"),
            ("evaluate_document_gate", "build_generation_report"),
        ]
        for source, destination in linear_routes:
            builder.add_conditional_edges(
                source,
                self._route_after_stage,
                {
                    "continue": destination,
                    "technical_failure": "finalize_workflow_failure",
                },
            )
        builder.add_conditional_edges(
            "build_generation_report",
            self._route_after_gate,
            {
                **_GATE_ROUTES,
                "human_review": "human_review_interrupt",
            },
        )
        builder.add_edge("human_review_interrupt", "apply_human_review")
        builder.add_conditional_edges(
            "apply_human_review",
            self._route_after_review,
            _GATE_ROUTES,
        )
        for persist_node in ("persist_deliverable", "persist_rejected"):
            builder.add_conditional_edges(
                persist_node,
                self._route_after_stage,
                {
                    "continue": "finalize_workflow_result",
                    "technical_failure": "finalize_workflow_failure",
                },
            )
        builder.add_edge("finalize_workflow_result", END)
        builder.add_edge("finalize_workflow_failure", END)

        graph_name = re.sub(
            r"[^A-Za-z0-9_-]+",
            "_",
            f"{workflow.workflow_id}__{workflow.workflow_version}__phase2",
        ).strip("_")
        return builder.compile(
            checkpointer=self._langgraph_checkpointer,
            name=graph_name or "enterprise_document_phase2",
        )

    def _invoke_config(
        self,
        workflow: WorkflowDefinitionSchema,
        *,
        run_id: str,
    ) -> dict[str, Any]:
        return {
            **phase2_checkpoint_identity(workflow, run_id=run_id),
            "recursion_limit": 64,
            "metadata": {
                "workflow_id": workflow.workflow_id,
                "workflow_version": workflow.workflow_version,
                "checkpoint_namespace": self.runtime_state_codec.workflow_namespace(
                    workflow
                ),
                "business_graph": "enterprise_document_phase8",
            },
        }

    def checkpoint_history(
        self,
        workflow: WorkflowDefinitionSchema,
        *,
        run_id: str,
    ) -> list:
        return list(
            self.checkpointer.list(
                phase2_checkpoint_identity(workflow, run_id=run_id)
            )
        )

    @staticmethod
    def _interrupt_payloads(final_runtime: dict[str, Any]) -> list[dict[str, Any]]:
        values = final_runtime.get("__interrupt__") or []
        payloads: list[dict[str, Any]] = []
        for value in values:
            raw = getattr(value, "value", value)
            payloads.append(dict(raw) if isinstance(raw, dict) else {"value": raw})
        return payloads

    def _checkpoint_business_nodes(
        self,
        workflow: WorkflowDefinitionSchema,
        *,
        run_id: str,
    ) -> tuple[list, list[str]]:
        history = self.checkpoint_history(workflow, run_id=run_id)
        nodes: list[str] = []
        for checkpoint_tuple in reversed(history):
            checkpoint = getattr(checkpoint_tuple, "checkpoint", {})
            channel_values = (
                checkpoint.get("channel_values", {})
                if isinstance(checkpoint, dict)
                else {}
            )
            node_name = str(
                channel_values.get("current_business_node") or ""
            ).strip()
            if node_name and node_name != "not_started" and (
                not nodes or nodes[-1] != node_name
            ):
                nodes.append(node_name)
        return history, nodes

    def _result_from_runtime(
        self,
        *,
        workflow: WorkflowDefinitionSchema,
        graph_state: GraphStateSchema,
        final_runtime: dict[str, Any],
        initial_revision: int,
        resumed: bool,
    ) -> WorkflowEngineResultSchema:
        final_graph_state = self.runtime_state_codec.restore_graph_state(
            final_runtime,
            workflow=workflow,
        )
        final_payload = self.business_state_codec.restore_payload(final_runtime)
        interrupts = self._interrupt_payloads(final_runtime)
        paused = bool(interrupts)
        terminal_result = final_payload.terminal_agent_result
        if not paused and terminal_result is None:
            raise RuntimeError("business graph completed without terminal AgentResult")

        if paused:
            final_graph_state.status = ExecutionStatus.PAUSED
            final_graph_state.context_bundle.runtime.status = ExecutionStatus.PAUSED
        self.runtime_state_codec.project_onto(graph_state, final_graph_state)

        node_inputs = self.runtime_state_codec.restore_node_inputs(final_runtime)
        node_outputs = self.runtime_state_codec.restore_node_outputs(final_runtime)
        node_results = self.runtime_state_codec.restore_node_results(final_runtime)
        completed_node_ids = [item.node_id for item in node_outputs]
        history, checkpoint_business_nodes = self._checkpoint_business_nodes(
            workflow,
            run_id=graph_state.run_id,
        )
        status = ExecutionStatus.PAUSED if paused else terminal_result.status
        error = None if paused else terminal_result.error
        return WorkflowEngineResultSchema(
            engine_name=self.engine_name,
            engine_version=self.engine_version,
            workflow_id=workflow.workflow_id,
            workflow_version=workflow.workflow_version,
            task_id=graph_state.task_id,
            run_id=graph_state.run_id,
            status=status,
            node_inputs=node_inputs,
            node_outputs=node_outputs,
            node_results=node_results,
            completed_node_ids=completed_node_ids,
            initial_revision=initial_revision,
            final_revision=graph_state.graph_revision,
            final_state_sha256=stable_graph_hash(
                graph_state.model_dump(mode="json")
            ),
            error=error,
            metadata={
                "workflow_complete": not paused,
                "workflow_paused": paused,
                "terminal_reason": (
                    "human_review_interrupt"
                    if paused
                    else final_runtime.get("terminal_reason")
                ),
                "interrupts": interrupts,
                "human_review_required": paused,
                "human_review_recommended": (
                    False
                    if paused or final_payload.human_review_decision is not None
                    else bool(
                        final_payload.output_status
                        == ExecutionStatus.PARTIAL_SUCCESS
                        or (
                            final_payload.output_error is not None
                            and final_payload.output_error.requires_human
                        )
                    )
                ),
                "human_review_status": (
                    HumanReviewStatus.PENDING.value
                    if paused
                    else final_payload.human_review_status.value
                ),
                "human_review_request": interrupts[0] if interrupts else None,
                "human_review_decision": (
                    final_payload.human_review_decision.model_dump(mode="json")
                    if final_payload.human_review_decision is not None
                    else None
                ),
                "resumed": resumed,
                "resume_count": final_payload.resume_count,
                "phase2_top_level_graph": True,
                "phase2_runtime_safety": True,
                "phase3_section_subgraph": True,
                "phase4_durable_resume": True,
                "phase5_langchain_runnable_takeover": True,
                "legacy_workflow_path_retired": True,
                "business_node_sequence": list(final_payload.executed_nodes),
                "business_node_count": len(final_payload.executed_nodes),
                "checkpoint_enabled": True,
                "checkpoint_thread_id": graph_state.run_id,
                "checkpoint_namespace": self.runtime_state_codec.workflow_namespace(
                    workflow
                ),
                "checkpoint_history_count": len(history),
                "checkpoint_business_nodes": checkpoint_business_nodes,
                "workflow_definition_topology_ignored": True,
                "section_subgraph_enabled": True,
                "section_parallelism_enabled": bool(
                    self.stage_service.section_max_concurrency > 1
                ),
                "section_max_concurrency": (
                    self.stage_service.section_max_concurrency
                ),
                "section_parallelism_scheduler": (
                    "langgraph_safe_independent_batch_v1"
                ),
                "active_background_workers": self.active_worker_count,
                "timeout_guard": "shared_worker_deadline_v1",
                "write_contract": "phase2_graph_and_payload_declared_paths_v1",
            },
        )

    def execute(
        self,
        workflow: WorkflowDefinitionSchema,
        graph_state: GraphStateSchema,
    ) -> WorkflowEngineResultSchema:
        """Start a durable business graph and return terminal or paused state."""

        initial_revision = graph_state.graph_revision
        graph_state.workflow_engine_name = self.engine_name
        graph_state.workflow_engine_version = self.engine_version
        initial_graph_state = graph_state.model_copy(deep=True)
        initial_graph_state.status = ExecutionStatus.RUNNING
        runtime = self.runtime_state_codec.build_initial_state(
            workflow,
            initial_graph_state,
        )
        payload = SchemeGenerationGraphPayloadSchema(
            task_id=graph_state.task_id,
            run_id=graph_state.run_id,
            workflow_namespace=self.runtime_state_codec.workflow_namespace(workflow),
            started_at=self.stage_service.runtime_support.now_iso(),
        )
        runtime.update(self.business_state_codec.build_initial_update(payload))
        self.business_state_codec.assert_json_serializable(runtime)
        final_runtime = self.build_graph(workflow).invoke(
            runtime,
            config=self._invoke_config(workflow, run_id=graph_state.run_id),
        )
        return self._result_from_runtime(
            workflow=workflow,
            graph_state=graph_state,
            final_runtime=final_runtime,
            initial_revision=initial_revision,
            resumed=False,
        )

    def _latest_checkpoint_payload(
        self,
        workflow: WorkflowDefinitionSchema,
        *,
        run_id: str,
    ) -> SchemeGenerationGraphPayloadSchema | None:
        latest = self.checkpointer.get_tuple(
            phase2_checkpoint_identity(workflow, run_id=run_id)
        )
        if latest is None:
            return None
        checkpoint = getattr(latest, "checkpoint", {})
        values = (
            checkpoint.get("channel_values", {})
            if isinstance(checkpoint, dict)
            else {}
        )
        raw = values.get("scheme_generation_payload")
        if not isinstance(raw, dict):
            return None
        return self.business_state_codec.restore_payload(
            {
                "scheme_generation_state_version": values.get(
                    "scheme_generation_state_version"
                ),
                "scheme_generation_payload": raw,
                "task_id": values.get("task_id") or raw.get("task_id"),
                "run_id": values.get("run_id") or raw.get("run_id"),
            }
        )

    def resume(
        self,
        workflow: WorkflowDefinitionSchema,
        graph_state: GraphStateSchema,
        resume_value: dict[str, Any],
    ) -> WorkflowEngineResultSchema:
        """Resume from the durable checkpoint without replaying completed nodes."""

        persisted = self._latest_checkpoint_payload(
            workflow,
            run_id=graph_state.run_id,
        )
        if persisted is None:
            raise RuntimeError("durable checkpoint does not contain business state")
        if persisted is not None:
            raw_metadata = dict(resume_value.get("metadata") or {})
            cancelled = str(resume_value.get("decision") or "").strip().lower() in {
                "cancel",
                "cancelled",
                "canceled",
            }
            expected_snapshot = self._active_input_snapshot(persisted)
            if not cancelled:
                provided_snapshot = resume_value.get("input_snapshot_version")
                if provided_snapshot is None:
                    provided_snapshot = raw_metadata.get("input_snapshot_version")
                if not isinstance(provided_snapshot, str) or not provided_snapshot:
                    raise ValueError("human review input_snapshot_version is required")
                if provided_snapshot != expected_snapshot:
                    raise ValueError("human review decision snapshot is stale")
                provided_task_id = resume_value.get("task_id")
                if not isinstance(provided_task_id, str) or not provided_task_id:
                    raise ValueError("human review task_id is required")
                if provided_task_id != persisted.task_id:
                    raise ValueError("human review task identity mismatch")
                provided_run_id = resume_value.get("run_id")
                if not isinstance(provided_run_id, str) or not provided_run_id:
                    raise ValueError("human review run_id is required")
                if provided_run_id != persisted.run_id:
                    raise ValueError("human review run identity mismatch")

        initial_revision = graph_state.graph_revision
        final_runtime = self.build_graph(workflow).invoke(
            Command(resume=dict(resume_value)),
            config=self._invoke_config(workflow, run_id=graph_state.run_id),
        )
        return self._result_from_runtime(
            workflow=workflow,
            graph_state=graph_state,
            final_runtime=final_runtime,
            initial_revision=initial_revision,
            resumed=True,
        )

    def recovery_plan(
        self,
        workflow: WorkflowDefinitionSchema,
        *,
        run_id: str,
    ) -> dict[str, Any]:
        """Locate a durable, non-human checkpoint from which recovery may branch."""

        inspection = self.inspect(workflow, run_id=run_id)
        if inspection.get("interrupts"):
            raise ValueError(
                "human review is waiting; inspect the run and use resume_run"
            )
        if not inspection.get("checkpoint_config") or not inspection.get(
            "graph_state"
        ):
            raise ValueError("durable checkpoint is required for recovery")
        if inspection.get("next_nodes"):
            return {
                "checkpoint_config": self._recovery_checkpoint_config(
                    workflow,
                    inspection["checkpoint_config"],
                    run_id=run_id,
                ),
                "reason": "unfinished_top_level_checkpoint",
                "failed_business_node": None,
            }

        persisted = self._latest_checkpoint_payload(workflow, run_id=run_id)
        if persisted is None:
            raise ValueError("durable business checkpoint is required for recovery")
        failed_node = str(persisted.failed_business_node or "").strip()
        if failed_node not in _SUCCESS_SEQUENCE:
            raise ValueError("checkpoint does not prove a recoverable interruption")
        failed_index = _SUCCESS_SEQUENCE.index(failed_node)
        if failed_index == 0:
            raise ValueError("checkpoint has no safe recovery predecessor")
        predecessor = _SUCCESS_SEQUENCE[failed_index - 1]

        for checkpoint_tuple in self.checkpoint_history(workflow, run_id=run_id):
            checkpoint = getattr(checkpoint_tuple, "checkpoint", {})
            values = (
                checkpoint.get("channel_values", {})
                if isinstance(checkpoint, dict)
                else {}
            )
            if str(values.get("current_business_node") or "") != predecessor:
                continue
            if values.get("task_id") != persisted.task_id or values.get(
                "run_id"
            ) != persisted.run_id:
                raise ValueError("recovery checkpoint identity mismatch")
            config = getattr(checkpoint_tuple, "config", None)
            if not isinstance(config, dict):
                raise ValueError("recovery checkpoint config is unavailable")
            return {
                "checkpoint_config": self._recovery_checkpoint_config(
                    workflow,
                    config,
                    run_id=run_id,
                ),
                "reason": "terminal_failure_checkpoint_branch",
                "failed_business_node": failed_node,
                "predecessor": predecessor,
            }
        raise ValueError("recoverable predecessor checkpoint was not found")

    def _recovery_checkpoint_config(
        self,
        workflow: WorkflowDefinitionSchema,
        config: dict[str, Any],
        *,
        run_id: str,
    ) -> dict[str, Any]:
        """Restore the logical namespace carrier for an exact checkpoint."""

        base = self._invoke_config(workflow, run_id=run_id)
        supplied = dict(config.get("configurable") or {})
        checkpoint_id = supplied.get("checkpoint_id")
        if not checkpoint_id:
            raise ValueError("recovery checkpoint id is unavailable")
        return {
            **base,
            "configurable": {
                **dict(base["configurable"]),
                # LangGraph treats a non-empty top-level checkpoint_ns as a
                # subgraph selector.  The saver adapter restores the logical
                # namespace from workflow_namespace at its own boundary.
                "checkpoint_ns": "",
                "checkpoint_id": checkpoint_id,
            },
        }

    def recover(
        self,
        workflow: WorkflowDefinitionSchema,
        graph_state: GraphStateSchema,
        *,
        recovery_attempt: int,
        recovery_reason: str,
        recovery_policy: str,
    ) -> WorkflowEngineResultSchema:
        """Continue a durable checkpoint without replaying completed Sections."""

        plan = self.recovery_plan(workflow, run_id=graph_state.run_id)
        initial_revision = graph_state.graph_revision
        context = DurableRecoveryContext(
            attempt_number=recovery_attempt,
            reason=recovery_reason,
            policy=recovery_policy,
        )
        with activate_durable_recovery(context):
            final_runtime = self.build_graph(workflow).invoke(
                None,
                config=plan["checkpoint_config"],
            )
        return self._result_from_runtime(
            workflow=workflow,
            graph_state=graph_state,
            final_runtime=final_runtime,
            initial_revision=initial_revision,
            resumed=True,
        )

    def inspect(
        self,
        workflow: WorkflowDefinitionSchema,
        *,
        run_id: str,
    ) -> dict[str, Any]:
        """Inspect durable cursor and interrupt state for reconciliation tooling."""

        config = self._invoke_config(workflow, run_id=run_id)
        # LangGraph 1.2.x interprets a non-empty top-level checkpoint_ns as a
        # subgraph lookup before the saver adapter can restore our namespace.
        # Query with the standard key empty and carry the real namespace in the
        # compatibility field consumed by WorkflowNamespaceCheckpointSaver.
        query_config = {
            **config,
            "configurable": {
                **dict(config.get("configurable", {})),
                "checkpoint_ns": "",
            },
        }
        snapshot = self.build_graph(workflow).get_state(query_config)
        interrupt_items = list(snapshot.interrupts)
        latest = self.checkpointer.get_tuple(
            phase2_checkpoint_identity(workflow, run_id=run_id)
        )
        if latest is not None:
            for _, channel, value in latest.pending_writes or []:
                if channel != "__interrupt__":
                    continue
                if isinstance(value, (list, tuple)):
                    interrupt_items.extend(value)
                else:
                    interrupt_items.append(value)
        interrupts: list[dict[str, Any]] = []
        seen_interrupt_ids: set[str] = set()
        for item in interrupt_items:
            interrupt_id = str(getattr(item, "id", "") or "")
            dedup_key = interrupt_id or stable_graph_hash(
                getattr(item, "value", item)
            )
            if dedup_key in seen_interrupt_ids:
                continue
            seen_interrupt_ids.add(dedup_key)
            interrupts.append(
                {
                    "id": interrupt_id or None,
                    "value": getattr(item, "value", item),
                }
            )
        next_nodes = list(snapshot.next)
        if interrupts and not next_nodes:
            next_nodes = ["human_review_interrupt"]
        runtime_values = dict(snapshot.values or {})
        restored_graph_state = (
            self.runtime_state_codec.restore_graph_state(
                runtime_values,
                workflow=workflow,
            )
            if runtime_values.get("graph_state_payload")
            else None
        )
        restored_payload = (
            self.business_state_codec.restore_payload(runtime_values)
            if runtime_values.get("scheme_generation_payload")
            else None
        )
        return {
            "run_id": run_id,
            "workflow_id": workflow.workflow_id,
            "workflow_version": workflow.workflow_version,
            "checkpoint_config": dict(snapshot.config),
            "next_nodes": next_nodes,
            "interrupts": interrupts,
            "graph_state": (
                restored_graph_state.model_dump(mode="json")
                if restored_graph_state is not None
                else None
            ),
            "human_review_required": bool(interrupts),
            "human_review_status": (
                HumanReviewStatus.PENDING.value
                if interrupts
                else HumanReviewStatus.NOT_REQUIRED.value
            ),
            "created_at": snapshot.created_at,
            "metadata": dict(snapshot.metadata or {}),
            "resume_count": (
                restored_payload.resume_count
                if restored_payload is not None
                else 0
            ),
        }

    def _merged_invoke_config(
        self,
        workflow: WorkflowDefinitionSchema,
        *,
        run_id: str,
        runnable_config: dict[str, Any] | None,
    ) -> dict[str, Any]:
        base = self._invoke_config(workflow, run_id=run_id)
        if not runnable_config:
            return base
        supplied = dict(runnable_config)
        merged = {
            **supplied,
            **base,
            "configurable": {
                **dict(supplied.get("configurable", {})),
                **dict(base.get("configurable", {})),
            },
            "metadata": {
                **dict(supplied.get("metadata", {})),
                **dict(base.get("metadata", {})),
            },
        }
        if supplied.get("tags"):
            merged["tags"] = list(supplied["tags"])
        if supplied.get("callbacks") is not None:
            merged["callbacks"] = supplied["callbacks"]
        return merged

    def cancel(
        self,
        workflow: WorkflowDefinitionSchema,
        graph_state: GraphStateSchema,
        *,
        reason: str = "workflow_cancelled",
    ) -> WorkflowEngineResultSchema:
        """Persist cancellation through the same interrupted graph path."""

        self.cancel_active_workers(reason)
        return self.resume(
            workflow,
            graph_state,
            {
                "decision": HumanReviewStatus.CANCELLED.value,
                "feedback": reason,
            },
        )

    def stream_events(
        self,
        workflow: WorkflowDefinitionSchema,
        graph_state: GraphStateSchema,
        *,
        resume_value: dict[str, Any] | None = None,
        runnable_config: dict[str, Any] | None = None,
    ) -> Iterable[WorkflowStreamEventSchema]:
        """Map LangGraph stream modes to the stable application event schema."""

        initial_revision = graph_state.graph_revision
        graph = self.build_graph(workflow)
        config = self._merged_invoke_config(
            workflow,
            run_id=graph_state.run_id,
            runnable_config=runnable_config,
        )
        if resume_value is None:
            graph_state.workflow_engine_name = self.engine_name
            graph_state.workflow_engine_version = self.engine_version
            initial_state = graph_state.model_copy(deep=True)
            initial_state.status = ExecutionStatus.RUNNING
            runtime = self.runtime_state_codec.build_initial_state(
                workflow,
                initial_state,
            )
            payload = SchemeGenerationGraphPayloadSchema(
                task_id=graph_state.task_id,
                run_id=graph_state.run_id,
                workflow_namespace=self.runtime_state_codec.workflow_namespace(
                    workflow
                ),
                started_at=self.stage_service.runtime_support.now_iso(),
            )
            runtime.update(self.business_state_codec.build_initial_update(payload))
            stream_input: Any = runtime
            initial_event_type = "workflow_started"
        else:
            stream_input = Command(resume=dict(resume_value))
            initial_event_type = "workflow_resumed"

        stream_session_id = uuid4().hex
        if resume_value is None:
            resume_count = 0
            stream_kind = "initial"
        else:
            inspection = self.inspect(workflow, run_id=graph_state.run_id)
            resume_count = int(inspection.get("resume_count", 0)) + 1
            decision = str(resume_value.get("decision") or "").strip().lower()
            stream_kind = (
                "cancel"
                if decision == HumanReviewStatus.CANCELLED.value
                else "resume"
            )
        sequence = 0

        def event(
            event_type: str,
            *,
            status: ExecutionStatus,
            node_name: str | None = None,
            section_id: str | None = None,
            progress: float | None = None,
            payload: dict[str, Any] | None = None,
            error: ErrorSchema | None = None,
            metadata: dict[str, Any] | None = None,
        ) -> WorkflowStreamEventSchema:
            nonlocal sequence
            sequence += 1
            return WorkflowStreamEventSchema(
                event_id=(
                    f"{graph_state.run_id}:{resume_count}:"
                    f"{stream_session_id}:{sequence:08d}:{event_type}"
                ),
                sequence=sequence,
                stream_session_id=stream_session_id,
                resume_count=resume_count,
                stream_kind=stream_kind,
                timestamp=self.stage_service.runtime_support.now_iso(),
                task_id=graph_state.task_id,
                run_id=graph_state.run_id,
                workflow_id=workflow.workflow_id,
                workflow_version=workflow.workflow_version,
                node_name=node_name,
                section_id=section_id,
                event_type=event_type,
                status=status,
                progress=progress,
                payload=dict(payload or {}),
                error=error,
                metadata={
                    **dict(metadata or {}),
                    "stream_session_id": stream_session_id,
                    "resume_count": resume_count,
                    "stream_kind": stream_kind,
                },
            )

        yield event(
            initial_event_type,
            status=ExecutionStatus.RUNNING,
            payload={
                "resumed": resume_value is not None,
                "review_decision": (
                    resume_value.get("decision")
                    if resume_value is not None
                    else None
                ),
            },
        )
        if resume_value is not None:
            yield event(
                "human_review_resumed",
                status=ExecutionStatus.RUNNING,
                node_name="human_review_interrupt",
                payload={
                    "decision": resume_value.get("decision"),
                    "feedback_present": bool(resume_value.get("feedback")),
                },
            )

        final_runtime: dict[str, Any] = {}
        interrupt_values: list[Any] = []
        for raw_event in graph.stream(
            stream_input,
            config=config,
            stream_mode=["tasks", "updates", "checkpoints", "custom"],
            subgraphs=True,
            version="v2",
        ):
            raw_type = str(raw_event.get("type") or "")
            data = raw_event.get("data")
            if raw_type == "checkpoints" and isinstance(data, dict):
                values = data.get("values")
                if isinstance(values, dict):
                    final_runtime = dict(values)
                configurable = (
                    data.get("config", {}).get("configurable", {})
                    if isinstance(data.get("config"), dict)
                    else {}
                )
                yield event(
                    "checkpoint_created",
                    status=ExecutionStatus.RUNNING,
                    payload={
                        "checkpoint_id": configurable.get("checkpoint_id"),
                        "step": (
                            data.get("metadata", {}).get("step")
                            if isinstance(data.get("metadata"), dict)
                            else None
                        ),
                    },
                )
                continue
            if raw_type == "updates" and isinstance(data, dict):
                raw_interrupts = data.get("__interrupt__")
                if raw_interrupts:
                    interrupt_values.extend(list(raw_interrupts))
                continue
            if raw_type == "custom" and isinstance(data, dict):
                custom_status = ExecutionStatus(
                    data.get("status", ExecutionStatus.RUNNING.value)
                )
                yield event(
                    str(data["event_type"]),
                    status=custom_status,
                    node_name=data.get("node_name"),
                    section_id=data.get("section_id"),
                    progress=data.get("progress"),
                    payload=data.get("payload"),
                )
                continue
            if raw_type != "tasks" or not isinstance(data, dict):
                continue
            node_name = str(data.get("name") or "")
            if not node_name or node_name.startswith("__"):
                continue
            task_started = "input" in data and "result" not in data
            task_error = data.get("error")
            if task_started:
                yield event(
                    "node_started",
                    status=ExecutionStatus.RUNNING,
                    node_name=node_name,
                )
                if node_name == "retrieve_document_evidence":
                    yield event(
                        "retrieval_started",
                        status=ExecutionStatus.RUNNING,
                        node_name=node_name,
                    )
                continue
            node_event_type = (
                "node_failed" if task_error is not None else "node_completed"
            )
            node_status = (
                ExecutionStatus.FAILED
                if task_error is not None
                else ExecutionStatus.SUCCESS
            )
            yield event(
                node_event_type,
                status=node_status,
                node_name=node_name,
                payload={
                    "interrupt_count": len(data.get("interrupts") or []),
                },
            )
            if node_name == "retrieve_document_evidence":
                yield event(
                    "retrieval_completed",
                    status=node_status,
                    node_name=node_name,
                )
            if node_name in {"persist_deliverable", "persist_rejected"}:
                yield event(
                    "artifact_persisted",
                    status=node_status,
                    node_name=node_name,
                    payload={
                        "artifact_route": (
                            "deliverable"
                            if node_name == "persist_deliverable"
                            else "rejected"
                        )
                    },
                )

        if interrupt_values:
            final_runtime["__interrupt__"] = interrupt_values
        if not final_runtime:
            raise RuntimeError("LangGraph stream completed without durable state")
        result = self._result_from_runtime(
            workflow=workflow,
            graph_state=graph_state,
            final_runtime=final_runtime,
            initial_revision=initial_revision,
            resumed=resume_value is not None,
        )
        result_payload = result.model_dump(mode="json")
        if result.status == ExecutionStatus.PAUSED:
            yield event(
                "human_review_required",
                status=result.status,
                node_name="human_review_interrupt",
                payload={"result": result_payload},
            )
        elif result.status == ExecutionStatus.CANCELLED:
            yield event(
                "workflow_cancelled",
                status=result.status,
                payload={"result": result_payload},
                error=result.error,
            )
        elif is_failure(result.status):
            yield event(
                "workflow_failed",
                status=result.status,
                payload={"result": result_payload},
                error=result.error,
            )
        else:
            yield event(
                "workflow_completed",
                status=result.status,
                payload={"result": result_payload},
            )
