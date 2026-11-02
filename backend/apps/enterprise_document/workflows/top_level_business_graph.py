"""Explicit, runtime-safe LangGraph ownership of enterprise document workflow."""

from __future__ import annotations

from hashlib import sha256
from pathlib import Path
from core.hashing import canonical_sha256
from apps.enterprise_document.schemas.scheme_generation_graph import (
    BudgetDocumentContinuationSchema, BudgetDocumentCursorSchema,
    BudgetDocumentRefusalSchema, BudgetDocumentWaitPacketSchema,
)
from apps.enterprise_document.services.scheme_writer.stage_service import BudgetDocumentAuthorizationWait

from apps.enterprise_document.services.agent_rag_v1.terminal_outcome_transport import (
    TerminalOutcomeStageFailure, declared_error,
)
from apps.enterprise_document.services.agent_rag_v1.budget_manager import (
    CanonicalBudgetCapacityExceeded, CanonicalBudgetDeadlineExceeded, CanonicalBudgetDraining,
)
from apps.enterprise_document.services.agent_rag_v1.claim_support_composition import TargetClaimSupportError
from apps.enterprise_document.services.agent_rag_v1.claim_verifier_composition import TargetClaimVerifierError

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
from apps.enterprise_document.services.scheme_writer.agent_integration.planner_adapter import (
    MainlinePlannerFailure,
)
from apps.enterprise_document.services.scheme_writer.agent_integration.section_agent_adapter import (
    MainlineSectionAgentFailure,
)
from apps.enterprise_document.services.scheme_writer.section_execution_coordinator import PreWriteWriterExecutionFailed
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
    "blocked": "finalize_workflow_result",
    "technical_failure": "finalize_workflow_failure",
}
_RECOVERY_PARENT_CHAIN_LIMIT = 4096


@dataclass(frozen=True)
class BusinessRouteEdge:
    """One active edge, including the router that owns its effective label."""

    source: str
    route: str | None
    destination: str
    router_kind: str | None


def _effective_business_route(
    router_kind: str, runtime_state: SchemeGenerationGraphState,
) -> str:
    """Pure route authority shared by graph execution and checkpoint recovery."""

    if router_kind == "stage":
        return (
            "technical_failure"
            if runtime_state.get("business_route") == "technical_failure"
            else "continue"
        )
    if router_kind == "gate":
        route = str(runtime_state.get("business_route") or "")
        return route if route in _GATE_ROUTES or route == "budget_wait" else "technical_failure"
    raise ValueError("business edge router kind is unavailable")


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
                "section_execution_results",
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
                "terminal_outcome",
                "target_document_artifact_ref",
                "target_post_write_control_decision_ref",
                "target_run_completion_assessment_ref",
                "target_release_publication",
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
                "terminal_outcome",
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
        lifecycle_service=None,
        artifact_runtime=None,
        fact_snapshot_runtime=None,
        section_plan_runtime=None,
        research_runtime=None,
        paragraph_runtime=None,
        claim_runtime=None,
        memory_runtime=None,
        context_runtime=None,
        budget_runtime=None,
        tool_runtime=None,
        storage_lifecycle_service=None,
        disk_budget_guard=None,
        storage_filesystem_roots=(),
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
        # Wave-13 composition refs.  These are injected owner handles, not graph
        # state and not a competing authority layer.  Existing narrow tests may
        # omit them; Target production startup validates the required set.
        self.lifecycle_service = lifecycle_service
        self.artifact_runtime = artifact_runtime
        self.fact_snapshot_runtime = fact_snapshot_runtime
        self.section_plan_runtime = section_plan_runtime
        self.research_runtime = research_runtime
        self.paragraph_runtime = paragraph_runtime
        self.claim_runtime = claim_runtime
        self.memory_runtime = memory_runtime
        self.context_runtime = context_runtime
        self.budget_runtime = budget_runtime
        self.tool_runtime = tool_runtime
        self.storage_lifecycle_service = storage_lifecycle_service
        self.disk_budget_guard = disk_budget_guard
        self.storage_filesystem_roots = tuple(storage_filesystem_roots)
        self.last_storage_admission = ()
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

    def _assert_new_run_storage_admission(self, run_id: str) -> None:
        """Reject only NEW durable Runs under CRITICAL local-storage pressure."""

        if self.disk_budget_guard is None or not self.storage_filesystem_roots:
            self.last_storage_admission = ()
            return
        self.last_storage_admission = (
            self.disk_budget_guard.assert_new_durable_run_allowed(
                self.storage_filesystem_roots,
                f"run:{run_id}",
            )
        )

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
        error = self._error_from_outcome_without_lineage(
            node_name=node_name, outcome=outcome,
        )
        trigger = getattr(outcome.exception, "c1_triggering_attempt_id", None)
        if trigger is not None:
            retained = error.details.get("c1_triggering_attempt_id")
            if retained is not None and retained != trigger:
                raise ValueError("worker failure Attempt identity transport disagrees")
            error = error.model_copy(update={"details": {
                **error.details, "c1_triggering_attempt_id": trigger,
            }}, deep=True)
        return error

    def _error_from_outcome_without_lineage(
        self,
        *,
        node_name: str,
        outcome: WorkflowNodeExecutionOutcome,
    ) -> ErrorSchema:
        if outcome.kind == "timeout":
            # Compatibility transport for a retained terminal timeout. The
            # shared runner no longer emits this for aggregate elapsed time.
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
        if isinstance(exc, TerminalOutcomeStageFailure):
            return exc.error
        if isinstance(exc, StateWriteContractViolation):
            return self.stage_service.runtime_support.error(
                "STATE_WRITE_CONTRACT_VIOLATION",
                exc,
                node=node_name,
                retryable=False,
                failure_category=FailureCategory.VALIDATION,
                requires_human=True,
            )
        if isinstance(exc, PreWriteWriterExecutionFailed):
            return ErrorSchema.model_validate(exc.result.model_dump(mode="json")["error"])
        if isinstance(
            exc,
            (SchemeGenerationStageFailure, MainlineSectionAgentFailure, MainlinePlannerFailure),
        ):
            # The worker has already classified these typed Agent failures.
            # Preserve their error code, source and details at the graph boundary.
            return exc.error
        if isinstance(exc, (CanonicalBudgetCapacityExceeded, CanonicalBudgetDeadlineExceeded, CanonicalBudgetDraining)):
            # Capacity can be denied during Section execution, before post-write's
            # typed carrier exists. Reuse its canonical mapping at this boundary.
            return declared_error(exc, self.stage_service.runtime_support, node=node_name)
        if isinstance(exc, (TargetClaimSupportError, TargetClaimVerifierError)):
            # Preserve the owner's declared facts; the graph still owns retry policy.
            error = declared_error(exc, self.stage_service.runtime_support, node=node_name)
            return error.model_copy(update={
                "retryable": self.retry_policy.should_retry(classify_failure(exc)),
            })
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

    def _maybe_seal_writer_review_copy(self, *, workflow, before_graph, before_payload):
        """Best-effort local derivative; never participate in Node/Retry authority."""
        try:
            from langgraph.checkpoint.sqlite import SqliteSaver
            if not self._target_post_write_live() or not isinstance(self.checkpointer, SqliteSaver):
                return None
            runtime = getattr(self.stage_service, "target_workflow_runtime", None)
            dependencies = getattr(runtime, "section_dependencies", None)
            runs_dir = getattr(self.stage_service.target_post_write_composition, "runs_dir", None)
            if dependencies is None or runs_dir is None:
                return None
            from apps.enterprise_document.services.agent_rag_v1.writer_review_copy import (
                resolve_committed_predecessor, validate_exact_writer_document,
                seal_local_writer_copy,
            )
            snapshot = resolve_committed_predecessor(self, workflow=workflow,
                before_graph=before_graph, before_payload=before_payload)
            source = validate_exact_writer_document(snapshot, section_dependencies=dependencies)
            return seal_local_writer_copy(runs_dir=runs_dir, source=source)
        except Exception as exc:
            # Exception messages may contain source data; emit only a bounded type.
            try:
                print(f"[WriterCopy] unavailable reason={type(exc).__name__}", flush=True)
            except Exception:
                pass
            return None

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
        if node_name == "final_artifact_review":
            self._maybe_seal_writer_review_copy(workflow=workflow,
                before_graph=before_graph, before_payload=before_payload)
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

                if node_name == "evaluate_document_gate" and isinstance(
                    outcome.exception, BudgetDocumentAuthorizationWait):
                    update=self._hold_document_budget(runtime_state,workflow=workflow,
                        graph_state=before_graph,payload=before_payload,node_input=node_input,outcome=outcome)
                    self._record_finished(node_name=node_name,graph_state=before_graph,
                        payload=self.business_state_codec.restore_payload({**runtime_state,**update}),
                        workflow=workflow,handle=handle,status=ExecutionStatus.PAUSED,error=None)
                    return update

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
                if isinstance(outcome.exception, TerminalOutcomeStageFailure):
                    failure = outcome.exception
                    terminal_updates = {"terminal_outcome": failure.terminal_outcome}
                    completion = failure.completion
                    if completion is not None:
                        if (completion.terminal_run.identity.task_id, completion.terminal_run.identity.run_id) != (before_payload.task_id, before_payload.run_id):
                            raise ValueError("completed Target failure belongs to another Run")
                        terminal_updates.update(
                            target_document_artifact_ref=completion.final_document.artifact_state_ref,
                            target_post_write_control_decision_ref=completion.final_document.control_decision_ref,
                            target_run_completion_assessment_ref=completion.run_completion_assessment_ref,
                            target_release_publication=completion.published_release,
                        )
                    committed_payload = committed_payload.model_copy(update=terminal_updates)
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
        """Run Target Sections under canonical SIF-12 Budget authority only."""

        if callable(self.budget_runtime):
            manager = self.budget_runtime(payload.task_id, payload.run_id)
            updated = self.stage_service.execute_sections(graph_state, payload)
            validation = updated.document_validation.model_copy(
                update={
                    "metadata": {
                        **dict(updated.document_validation.metadata),
                        "task_budget": manager.canonical_state().model_dump(
                            mode="json"
                        ),
                        "budget_authority": "canonical_sif12",
                        "legacy_task_draining_budget_active": False,
                        "budget_integration_round": "wave13",
                    }
                },
                deep=True,
            )
            return updated.model_copy(
                update={"document_validation": validation},
                deep=True,
            )

        # Compatibility-only fallback for isolated legacy tests that construct
        # an engine without the Target owner graph.
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
                    "budget_authority": "legacy_compatibility_only",
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
        return _effective_business_route("stage", runtime_state)

    def _route_after_gate(
        self,
        runtime_state: SchemeGenerationGraphState,
    ) -> str:
        # Wave-13 Target production has no Human Review authorization route.
        # Compatibility review fields may persist in the schema, but cannot
        # intervene between canonical Control, RunCompletion, and Release.
        return _effective_business_route("gate", runtime_state)

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
        return _effective_business_route("gate", runtime_state)

    def _target_post_write_live(self) -> bool:
        return getattr(
            getattr(self, "stage_service", None),
            "target_post_write_composition", None,
        ) is not None

    def _active_route_edges(self) -> tuple[BusinessRouteEdge, ...]:
        """Return the immutable full topology consumed by graph and recovery."""

        target_post_write_live = self._target_post_write_live()
        routes = [
            ("normalize_project_input", "plan_document"),
            ("plan_document", "retrieve_document_evidence"),
            ("retrieve_document_evidence", "initialize_generation"),
            ("initialize_generation", "execute_sections"),
            ("execute_sections", "document_verify"),
            ("document_verify", "repair_document_conflicts"),
            ("repair_document_conflicts", "incremental_document_reverify"),
            ("incremental_document_reverify", "assemble_document"),
            ("assemble_document", "validate_document_assembly"),
            ("validate_document_assembly", "final_artifact_review"),
        ]
        if target_post_write_live:
            routes.append(("final_artifact_review", "evaluate_document_gate"))
        else:
            routes.extend(
                [
                    ("final_artifact_review", "decide_document_version"),
                    ("decide_document_version", "evaluate_document_gate"),
                    ("evaluate_document_gate", "build_generation_report"),
                ]
            )
        edges = [BusinessRouteEdge(START, None, "normalize_project_input", None)]
        for source, destination in routes:
            edges.extend((
                BusinessRouteEdge(source, "continue", destination, "stage"),
                BusinessRouteEdge(source, "technical_failure", "finalize_workflow_failure", "stage"),
            ))
        gate_source = (
            "evaluate_document_gate" if target_post_write_live
            else "build_generation_report"
        )
        edges.extend(
            BusinessRouteEdge(gate_source, route, destination, "gate")
            for route, destination in _GATE_ROUTES.items()
        )
        if target_post_write_live:
            edges.extend((
                BusinessRouteEdge("evaluate_document_gate","budget_wait","budget_authorization_interrupt","gate"),
                BusinessRouteEdge("budget_authorization_interrupt","continue","evaluate_document_gate","stage"),
                BusinessRouteEdge("budget_authorization_interrupt","technical_failure","finalize_workflow_failure","stage"),
            ))
        for source in ("persist_deliverable", "persist_rejected"):
            edges.extend((
                BusinessRouteEdge(source, "continue", "finalize_workflow_result", "stage"),
                BusinessRouteEdge(source, "technical_failure", "finalize_workflow_failure", "stage"),
            ))
        edges.extend((
            BusinessRouteEdge("finalize_workflow_result", None, END, None),
            BusinessRouteEdge("finalize_workflow_failure", None, END, None),
        ))
        return tuple(edges)

    def _effective_route_label(
        self, source: str, runtime_state: SchemeGenerationGraphState,
        *, route_edges: tuple[BusinessRouteEdge, ...] | None = None,
    ) -> str:
        edges = route_edges if route_edges is not None else self._active_route_edges()
        kinds = {edge.router_kind for edge in edges if edge.source == source}
        if len(kinds) != 1 or None in kinds:
            raise ValueError("business source has no unique active router")
        return _effective_business_route(kinds.pop(), runtime_state)

    def build_graph(self, workflow: WorkflowDefinitionSchema):
        """Build the explicit application graph; legacy step topology is ignored."""

        builder = StateGraph(SchemeGenerationGraphState)
        target_post_write_live = self._target_post_write_live()
        route_edges = self._active_route_edges()

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
        if not target_post_write_live:
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
        if not target_post_write_live:
            builder.add_node(
                "build_generation_report",
                business_node(
                    "build_generation_report",
                    self.stage_service.build_generation_report,
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

        if target_post_write_live:
            builder.add_node("budget_authorization_interrupt",
                lambda state: self._budget_authorization_interrupt(state,workflow=workflow))

        conditional: dict[str, dict[str, str]] = {}
        for edge in route_edges:
            if edge.router_kind is None:
                builder.add_edge(edge.source, edge.destination)
            else:
                conditional.setdefault(edge.source, {})[edge.route] = edge.destination
        for source, destinations in conditional.items():
            builder.add_conditional_edges(
                source,
                lambda state, source=source: self._effective_route_label(
                    source, state, route_edges=route_edges,
                ),
                destinations,
            )

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
        budget_paused = bool(paused and final_payload.pause_purpose == "BUDGET_AUTHORIZATION"
            and all(value.get("pause_purpose") == "BUDGET_AUTHORIZATION" for value in interrupts))
        if paused and final_payload.pause_purpose == "BUDGET_AUTHORIZATION" and not budget_paused:
            raise ValueError("Budget pause has mixed or unproven interrupt purpose")
        budget_packet = self.resolve_budget_wait(workflow,run_id=graph_state.run_id) if budget_paused else None
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
        terminal_error = terminal_result.error if terminal_result is not None else None
        if terminal_error is not None and "lifecycle_composite_transition" in terminal_error.details:
            # Result projection only: retain the same primary error and its exact
            # owner receipt without changing persisted intermediate node history.
            original_error = terminal_error.model_dump(mode="json", exclude={
                "details": {"lifecycle_composite_transition"},
            })
            node_results = [
                row.model_copy(update={"error": terminal_error.model_copy(deep=True)}, deep=True)
                if row.error is not None and row.error.model_dump(mode="json", exclude={
                    "details": {"lifecycle_composite_transition"},
                }) == original_error else row
                for row in node_results
            ]
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
                    "budget_authorization_interrupt" if budget_paused else "human_review_interrupt"
                    if paused
                    else final_runtime.get("terminal_reason")
                ),
                "interrupts": interrupts,
                "pause_purpose": "BUDGET_AUTHORIZATION" if budget_paused else None,
                "budget_wait": budget_packet.model_dump(mode="json") if budget_packet is not None else None,
                "budget_wait_ref": final_payload.budget_wait_ref if budget_paused else None,
                "budget_wait_sha256": final_payload.budget_wait_sha256 if budget_paused else None,
                "budget_wait_event_ref": final_payload.budget_wait_event_ref if budget_paused else None,
                "budget_wait_event_sha256": final_payload.budget_wait_event_sha256 if budget_paused else None,
                "human_review_required": paused and not budget_paused,
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
                    HumanReviewStatus.NOT_REQUIRED.value if budget_paused else HumanReviewStatus.PENDING.value
                    if paused
                    else final_payload.human_review_status.value
                ),
                "human_review_request": interrupts[0] if interrupts and not budget_paused else None,
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
                "timeout_guard": "aggregate_observation_explicit_cancel_v1",
                "write_contract": "phase2_graph_and_payload_declared_paths_v1",
            },
        )

    def _budget_owners(self):
        post_write = getattr(self.stage_service, "target_post_write_composition", None)
        if post_write is None or post_write.lifecycle_service is None or post_write.citation_grounding is None:
            raise ValueError("Budget DOCUMENT continuation requires actual Target owners")
        return post_write, post_write.lifecycle_service, post_write.citation_grounding

    @staticmethod
    def _budget_source_epoch():
        root = Path(__file__).resolve().parents[4]
        names = (
            "backend/apps/enterprise_document/workflows/top_level_business_graph.py",
            "backend/apps/enterprise_document/services/scheme_writer/stage_service.py",
            "backend/apps/enterprise_document/services/agent_rag_v1/post_write_citation_grounding.py",
            "backend/apps/enterprise_document/services/agent_rag_v1/post_write_composition.py",
            "backend/apps/enterprise_document/services/agent_rag_v1/claim_support_composition.py",
            "backend/apps/enterprise_document/services/agent_rag_v1/claim_support_runtime.py",
            "backend/apps/enterprise_document/services/post_write/shared_claim_support.py",
            "backend/apps/enterprise_document/services/post_write/citation.py",
            "backend/apps/enterprise_document/services/agent_rag_v1/lifecycle_runtime.py",
            "backend/apps/enterprise_document/services/agent_rag_v1/budget_manager.py",
            "backend/apps/enterprise_document/services/agent_rag_v1/model_tool_budget_runtime.py",
            "backend/model_gateway/model_gateway.py",
            "backend/agent/runtime/business_node_execution_runner.py",
            "backend/core/runtime/execution_control.py",
            "backend/apps/enterprise_document/services/agent_rag_v1/artifact_store.py",
            "backend/agent/supervisor_agent.py",
            "backend/application/mainline_service.py",
            "backend/bootstrap/supervisor_factory.py",
            "backend/core/persistence/interprocess_lock.py",
        )
        return {name: sha256((root / name).read_bytes()).hexdigest() for name in names}

    @staticmethod
    def _budget_checkpoint_material(values):
        return {name: values.get(name) for name in (
            "graph_state_payload", "scheme_generation_payload", "node_inputs", "node_outputs",
            "node_results", "transition_count", "current_business_node", "business_route")}

    def _validate_budget_cursor(self, workflow, packet, *, payload=None, validate_live_sources=True):
        post_write, lifecycle, citation = self._budget_owners()
        cursor = packet.cursor
        if (cursor.workflow_id != workflow.workflow_id or cursor.workflow_version != workflow.workflow_version
                or cursor.workflow_namespace != self.runtime_state_codec.workflow_namespace(workflow)
                or cursor.source_epoch != self._budget_source_epoch()):
            raise ValueError("Budget cursor workflow/source epoch changed")
        native = self._recovery_native_config(workflow, cursor.checkpoint_config, run_id=cursor.run_id)
        predecessor = self.checkpointer.get_tuple(native)
        if predecessor is None:
            raise ValueError("Budget original predecessor checkpoint is absent")
        values, original, count, _ = self._recovery_checkpoint_values(
            workflow, predecessor, task_id=cursor.task_id, run_id=cursor.run_id)
        if (values.get("current_business_node") != cursor.predecessor_business_node
                or self._effective_route_label(cursor.predecessor_business_node, values) != cursor.predecessor_effective_route
                or count != cursor.predecessor_transition_count
                or tuple(values.get("node_outputs") or ()) != cursor.predecessor_committed_node_refs
                or stable_graph_hash(self._budget_checkpoint_material(values)) != cursor.checkpoint_state_sha256):
            raise ValueError("Budget cursor exact predecessor/route/committed outputs changed")
        supplied = payload or original
        if (supplied.task_id != cursor.task_id or supplied.run_id != cursor.run_id
                or supplied.document_id != cursor.document_id or supplied.project_input is None
                or canonical_sha256(supplied.project_input.model_dump(mode="json")) != cursor.project_input_sha256
                or self._active_input_snapshot(supplied) != cursor.input_snapshot_version
                or tuple(supplied.section_execution_results) != cursor.source_sections):
            raise ValueError("Budget cursor request/input/materialization sources changed")
        identity = lifecycle.load_run_identity(cursor.run_id)
        if (identity.task_id != cursor.task_id or identity.canonical_request_sha256 != cursor.canonical_request_sha256
                or cursor.canonical_request_ref != "run-identity:" + cursor.run_id):
            raise ValueError("Budget cursor original Run request identity changed")
        # The immutable captured envelope remains exact after lawful terminal
        # heads advance. Pending entry paths also require current owner heads.
        materialization_ref = cursor.budget_materialization_ref
        materialization_envelope, materialization = citation.store.get_json_with_reference(
            materialization_ref.artifact_ref, task_id=cursor.task_id, run_id=cursor.run_id)
        if (materialization_envelope != materialization_ref
                or materialization.get("schema_version") != "budget_document_materialization_v1"
                or (materialization.get("task_id"), materialization.get("run_id"), materialization.get("document_id"))
                    != (cursor.task_id, cursor.run_id, cursor.document_id)
                or materialization.get("project_sha256") != cursor.project_input_sha256
                or materialization.get("source_sections") != [row.model_dump(mode="json") for row in cursor.source_sections]):
            raise ValueError("Budget immutable captured materialization differs from original cursor")
        refusal = packet.refusal
        if validate_live_sources:
            post_write.validate_budget_document_current(refusal.model_dump(mode="json"),
                materialization_ref=materialization_ref,
                task_id=cursor.task_id, run_id=cursor.run_id, document_id=cursor.document_id,
                project_input=supplied.project_input, section_execution_results=cursor.source_sections)
        verified = citation.resolve_budget_refusal(receipt_ref=refusal.native_denial_ref,
            expected_sha256=refusal.native_denial_sha256,
            identity={"task_id":refusal.task_id,"run_id":refusal.run_id,"section_id":refusal.section_id})
        if BudgetDocumentRefusalSchema.model_validate(verified) != refusal:
            raise ValueError("Budget wait native first-dispatch refusal changed")
        envelope, closure = citation.store.get_json_with_reference(packet.runner_closure_ref,
            task_id=cursor.task_id, run_id=cursor.run_id, input_snapshot_version=cursor.input_snapshot_version)
        control = closure.get("execution_control") or {}
        if (envelope.content_hash != "sha256:" + packet.runner_closure_sha256
                or closure.get("schema_version") != "document_budget_runner_closure_v1"
                or closure.get("task_id") != cursor.task_id or closure.get("run_id") != cursor.run_id
                or closure.get("source_epoch") != cursor.source_epoch
                or closure.get("outcome_kind") != "error" or closure.get("worker_still_active") is not False
                or closure.get("active_background_workers_observed") != 0
                or control.get("cancel_requested") is not False
                or control.get("pending_external_commits") != 0
                or control.get("external_commit_state") != "committed"):
            raise ValueError("Budget wait lacks exact original joined runner/control closure")
        native_input = GraphNodeInputSchema.model_validate(closure.get("node_input"))
        if ((native_input.node_id,native_input.task_id,native_input.run_id,
             native_input.workflow_id,native_input.workflow_version)
                != ("evaluate_document_gate",cursor.task_id,cursor.run_id,cursor.workflow_id,cursor.workflow_version)
                or closure.get("pending_node") != "evaluate_document_gate"
                or closure.get("native_denial_ref") != refusal.native_denial_ref
                or closure.get("native_denial_sha256") != refusal.native_denial_sha256
                or closure.get("phase_proof_ref") != refusal.phase_proof_ref
                or closure.get("phase_proof_sha256") != refusal.phase_proof_sha256
                or closure.get("semantic_input_sha256") != refusal.semantic_input_sha256
                or closure.get("budget_materialization_ref") != cursor.budget_materialization_ref.model_dump(mode="json")
                or closure.get("checkpoint_state_sha256") != cursor.checkpoint_state_sha256
                or closure.get("run_lifecycle_revision") != packet.run_preimage.lifecycle_revision
                or closure.get("child_preimage_sha256") != packet.child_preimage_sha256):
            raise ValueError("Budget original runner closure belongs to different pending input/refusal/preimage")
        return original

    def _budget_pending_update(self, runtime_state, payload, wait, packet):
        held = payload.model_copy(update={
            "pause_purpose":"BUDGET_AUTHORIZATION",
            "budget_wait_ref":wait.wait_ref,"budget_wait_sha256":wait.wait_sha256,
            "budget_wait_event_ref":wait.event_ref,"budget_wait_event_sha256":wait.event_sha256,
            "budget_materialization_ref":packet.cursor.budget_materialization_ref,
            "budget_document_continuation":None,
            "artifact_route":"budget_wait",
            "terminal_outcome":None,"terminal_error":None,"failed_business_node":None,
        },deep=True)
        return {"scheme_generation_payload":self.business_state_codec.dump_payload(held),
            "current_business_node":runtime_state.get("current_business_node"),
            "business_route":"budget_wait","workflow_complete":False}

    def _hold_document_budget(self, runtime_state, *, workflow, graph_state, payload, node_input, outcome):
        post_write, lifecycle, citation = self._budget_owners()
        failure = outcome.exception
        control = outcome.execution_control.metadata()
        if (not isinstance(failure,BudgetDocumentAuthorizationWait) or outcome.kind != "error"
                or outcome.worker_still_active or self.active_worker_count != 0
                or control.get("cancel_requested") or control.get("pending_external_commits") != 0
                or control.get("external_commit_state") != "committed"):
            raise ValueError("Budget hold requires actual closed original worker/control")
        preimage = lifecycle.budget_preimage(payload.run_id)
        if preimage.run.status.value != "running":
            raise ValueError("Budget hold canonical Run is no longer RUNNING")
        latest = self.checkpointer.get_tuple(phase2_checkpoint_identity(workflow,run_id=payload.run_id))
        if latest is None:
            raise ValueError("Budget hold original predecessor checkpoint is missing")
        values, original, count, _ = self._recovery_checkpoint_values(workflow,latest,
            task_id=payload.task_id,run_id=payload.run_id)
        if (values.get("current_business_node") != "final_artifact_review"
                or self._effective_route_label("final_artifact_review",values) != "continue"
                or original.project_input != payload.project_input
                or original.section_execution_results != payload.section_execution_results):
            raise ValueError("Budget hold has no exact pending DOCUMENT predecessor")
        source_epoch = self._budget_source_epoch()
        cursor = BudgetDocumentCursorSchema(task_id=payload.task_id,run_id=payload.run_id,
            document_id=payload.document_id,canonical_request_ref="run-identity:"+payload.run_id,
            canonical_request_sha256=preimage.run.identity.canonical_request_sha256,
            project_input_sha256=canonical_sha256(payload.project_input.model_dump(mode="json")),
            input_snapshot_version=self._active_input_snapshot(payload),workflow_id=workflow.workflow_id,
            workflow_version=workflow.workflow_version,workflow_namespace=payload.workflow_namespace,
            checkpoint_config=self._recovery_native_config(workflow,latest.config,run_id=payload.run_id),
            checkpoint_state_sha256=stable_graph_hash(self._budget_checkpoint_material(values)),
            predecessor_transition_count=count,predecessor_committed_node_refs=tuple(values["node_outputs"]),
            source_sections=tuple(payload.section_execution_results),
            budget_materialization_ref=failure.materialization_ref,
            source_epoch=source_epoch,source_epoch_sha256=canonical_sha256(source_epoch))
        closure = {"schema_version":"document_budget_runner_closure_v1","task_id":payload.task_id,
            "run_id":payload.run_id,"source_epoch":source_epoch,"outcome_kind":outcome.kind,
            "worker_still_active":outcome.worker_still_active,
            "active_background_workers_observed":self.active_worker_count,
            "execution_control":control,"external_commit_summary":outcome.external_commit_summary,
            "node_input":node_input.model_dump(mode="json"),"started_at":outcome.started_at,
            "finished_at":outcome.finished_at,"latency_ms":outcome.latency_ms,
            "capacity_diagnostic":failure.error.model_dump(mode="json")}
        closure.update({
            "pending_node":"evaluate_document_gate",
            "native_denial_ref":failure.refusal.native_denial_ref,
            "native_denial_sha256":failure.refusal.native_denial_sha256,
            "phase_proof_ref":failure.refusal.phase_proof_ref,
            "phase_proof_sha256":failure.refusal.phase_proof_sha256,
            "semantic_input_sha256":failure.refusal.semantic_input_sha256,
            "budget_materialization_ref":failure.materialization_ref.model_dump(mode="json"),
            "checkpoint_state_sha256":cursor.checkpoint_state_sha256,
            "run_lifecycle_revision":preimage.run.lifecycle_revision,
            "child_preimage_sha256":preimage.child_preimage_sha256,
        })
        closure_ref=citation.store.put_json(closure,
            artifact_ref="artifact://document-budget-runner-closure/"+canonical_sha256(closure),
            artifact_type="document_budget_runner_closure",schema_version=closure["schema_version"],
            created_by="EnterpriseDocumentWorkflowEngine",task_id=payload.task_id,run_id=payload.run_id,
            input_snapshot_version=cursor.input_snapshot_version)
        packet=BudgetDocumentWaitPacketSchema(refusal=failure.refusal,cursor=cursor,
            run_preimage=preimage.run,current_children=preimage.current_children,
            child_preimage_sha256=preimage.child_preimage_sha256,
            runner_closure_ref=closure_ref.artifact_ref,
            runner_closure_sha256=closure_ref.content_hash.removeprefix("sha256:"))
        self._validate_budget_cursor(workflow,packet,payload=payload)
        material=packet.model_dump(mode="json")
        packet_ref=citation.store.put_json(material,
            artifact_ref="artifact://document-budget-wait/"+canonical_sha256(material),
            artifact_type="document_budget_wait",schema_version=packet.schema_version,
            created_by="EnterpriseDocumentWorkflowEngine",task_id=payload.task_id,run_id=payload.run_id,
            input_snapshot_version=cursor.input_snapshot_version)
        wait=lifecycle.pause_for_budget(payload.run_id,preimage.run.lifecycle_revision,
            expected_current_children=preimage.current_children,
            operation_key="budget-pause:"+packet_ref.artifact_ref,
            wait_ref=packet_ref.artifact_ref,wait_sha256=packet_ref.content_hash.removeprefix("sha256:"),
            semantic_operation_ref=packet.refusal.semantic_operation_ref,
            semantic_input_sha256=packet.refusal.semantic_input_sha256,
            artifact_version=str(packet.refusal.artifact_version))
        return self._budget_pending_update(runtime_state,payload,wait,packet)

    def recover_budget_pause_from_wait_packet(self, workflow, *, task_id, run_id, wait_ref, wait_sha256):
        """Recover only a native positive joined wait packet before PAUSE.

        No refusal scan, generic restart, fresh admission, grant, or business
        node invocation. A result without this exact native cursor/closure is
        indeterminate and must not be upgraded from process/trace absence.
        """
        _, lifecycle, citation = self._budget_owners()
        if self.active_worker_count != 0:
            raise ValueError("Budget pause recovery has an active local worker")
        if (not isinstance(wait_ref, str) or not isinstance(wait_sha256, str)
                or len(wait_sha256) != 64
                or any(ch not in "0123456789abcdef" for ch in wait_sha256)):
            raise ValueError("Budget pause recovery requires exact immutable packet ref/hash")
        envelope, raw = citation.store.get_json_with_reference(
            wait_ref, task_id=task_id, run_id=run_id)
        if (envelope.content_hash != "sha256:" + wait_sha256
                or envelope.artifact_type != "document_budget_wait"
                or envelope.schema_version != "document_budget_wait_packet_v1"
                or not envelope.immutable
                or envelope.created_by != "EnterpriseDocumentWorkflowEngine"
                or wait_ref != "artifact://document-budget-wait/" + canonical_sha256(raw)):
            raise ValueError("Budget pause recovery native wait artifact differs")
        packet = BudgetDocumentWaitPacketSchema.model_validate(raw)
        if ((packet.cursor.task_id, packet.cursor.run_id) != (task_id, run_id)
                or envelope.input_snapshot_version != packet.cursor.input_snapshot_version):
            raise ValueError("Budget pause recovery packet identity/input differs")
        self._validate_budget_cursor(workflow, packet)
        current = lifecycle.current_budget_transition(run_id)
        preimage = lifecycle.budget_preimage(run_id)
        if current is not None:
            if (current.kind != "PAUSE" or current.wait_ref != wait_ref
                    or current.wait_sha256 != wait_sha256):
                raise ValueError("Budget pause recovery cannot replace another wait/resume")
            _, native_packet = self._budget_packet_for_transition(workflow, current)
            if (native_packet != packet or preimage.run != current.resulting_run
                    or preimage.current_children != current.current_children
                    or preimage.child_preimage_sha256 != current.child_preimage_sha256):
                raise ValueError("Budget pause recovery existing native PAUSE is not exact")
        else:
            if (preimage.run != packet.run_preimage
                    or preimage.current_children != packet.current_children
                    or preimage.child_preimage_sha256 != packet.child_preimage_sha256):
                raise ValueError("Budget pause recovery full current Run/Children changed")
            latest = self.checkpointer.get_tuple(
                phase2_checkpoint_identity(workflow, run_id=run_id))
            if (latest is None
                    or self._recovery_native_config(workflow, latest.config, run_id=run_id)
                       != packet.cursor.checkpoint_config
                    or stable_graph_hash(self._budget_checkpoint_material(
                        latest.checkpoint["channel_values"]))
                       != packet.cursor.checkpoint_state_sha256
                    or self.active_worker_count != 0):
                raise ValueError("Budget pause recovery latest predecessor/worker changed")
            lifecycle.pause_for_budget(run_id, packet.run_preimage.lifecycle_revision,
                expected_current_children=packet.current_children,
                operation_key="budget-pause:" + wait_ref,
                wait_ref=wait_ref, wait_sha256=wait_sha256,
                semantic_operation_ref=packet.refusal.semantic_operation_ref,
                semantic_input_sha256=packet.refusal.semantic_input_sha256,
                artifact_version=str(packet.refusal.artifact_version))
        # This projection reads the just-created native PAUSE again and enters
        # only the exact budget authorization interrupt. It never executes the
        # pending DOCUMENT gate, and does not mark that gate completed.
        predecessor = self.checkpointer.get_tuple(packet.cursor.checkpoint_config)
        if predecessor is None:
            raise ValueError("Budget pause recovery original checkpoint disappeared")
        graph_state = self.runtime_state_codec.restore_graph_state(
            predecessor.checkpoint["channel_values"], workflow=workflow)
        observation = self.ensure_budget_wait_checkpoint(workflow, graph_state)
        current = lifecycle.current_budget_transition(run_id)
        if (current is None or current.kind != "PAUSE"
                or current.wait_ref != wait_ref or current.wait_sha256 != wait_sha256):
            raise ValueError("Budget pause recovery native PAUSE changed during projection")
        return observation

    def resolve_budget_wait(self, workflow, *, run_id):
        _, lifecycle, _ = self._budget_owners()
        payload = self._latest_checkpoint_payload(workflow,run_id=run_id)
        current = lifecycle.current_budget_transition(run_id)
        if current is None:
            raise ValueError("No native current budget wait/resume authority")
        wait, packet = self._budget_packet_for_transition(workflow,current,payload=payload)
        live = lifecycle.budget_preimage(run_id)
        if live.current_children != wait.current_children or live.child_preimage_sha256 != wait.child_preimage_sha256:
            raise ValueError("Budget wait complete current Child set changed")
        return packet

    def _budget_authorization_interrupt(self, runtime_state, *, workflow):
        graph_state,payload=self._restore(runtime_state,workflow)
        response=interrupt({"pause_purpose":"BUDGET_AUTHORIZATION","task_id":payload.task_id,
            "run_id":payload.run_id,"budget_wait_ref":payload.budget_wait_ref,
            "budget_wait_sha256":payload.budget_wait_sha256,
            "budget_wait_event_ref":payload.budget_wait_event_ref,
            "budget_wait_event_sha256":payload.budget_wait_event_sha256,
            "pending_node":"evaluate_document_gate"})
        if not isinstance(response,dict) or response.get("pause_purpose") != "BUDGET_AUTHORIZATION":
            raise ValueError("Budget interrupt requires separate trusted budget continuation")
        if response.get("cancelled"):
            error=self.stage_service.runtime_support.error("WORKFLOW_NODE_CANCELLED",
                str(response.get("reason") or "explicit cancellation"),node="evaluate_document_gate",
                retryable=False,failure_category=FailureCategory.CANCELLED,requires_human=True)
            cancelled=self._failure_payload(payload,node_name="evaluate_document_gate",error=error)
            return {"scheme_generation_payload":self.business_state_codec.dump_payload(cancelled),
                "business_route":"technical_failure"}
        continuation=BudgetDocumentContinuationSchema.model_validate(response.get("continuation"))
        proof=self.validate_budget_execution_cursor(workflow,graph_state=graph_state,continuation=continuation)
        if proof["phase"] == "INDETERMINATE":
            raise ValueError("Budget DOCUMENT admission intent is unresolved; no redispatch")
        packet=self.resolve_budget_wait(workflow,run_id=payload.run_id)
        resumed=payload.model_copy(update={"pause_purpose":None,"artifact_route":"pending",
            "budget_document_continuation":continuation,
            "budget_materialization_ref":packet.cursor.budget_materialization_ref,
            "resume_count":payload.resume_count+1},deep=True)
        return {"scheme_generation_payload":self.business_state_codec.dump_payload(resumed),
            "business_route":"continue"}

    def ensure_budget_wait_checkpoint(self, workflow, graph_state):
        packet=self.resolve_budget_wait(workflow,run_id=graph_state.run_id)
        _,lifecycle,_=self._budget_owners()
        wait=lifecycle.current_budget_transition(graph_state.run_id)
        if wait is None or wait.kind != "PAUSE":
            raise ValueError("Budget checkpoint projection requires native PAUSED wait")
        inspection=self.inspect(workflow,run_id=graph_state.run_id)
        graph=self.build_graph(workflow)
        if inspection.get("pause_purpose")=="BUDGET_AUTHORIZATION":
            _,_,_,_,mode=self._budget_latest_pending(workflow,graph_state=graph_state,wait=wait,packet=packet)
            if mode == "INTERRUPTED":
                self.validate_budget_wait_for_resume(workflow,graph_state,wait=packet)
                return inspection
            if mode != "CONTROL_PENDING":
                raise ValueError("Budget projection cannot enter another pending node")
            graph.invoke(None,config=self._invoke_config(workflow,run_id=graph_state.run_id), durability="sync")
        else:
            latest=self.checkpointer.get_tuple(phase2_checkpoint_identity(workflow,run_id=graph_state.run_id))
            original=self.checkpointer.get_tuple(packet.cursor.checkpoint_config).checkpoint["channel_values"]
            if (latest is None
                    or self._recovery_native_config(workflow,latest.config,run_id=graph_state.run_id)!=packet.cursor.checkpoint_config
                    or stable_graph_hash(self._budget_checkpoint_material(latest.checkpoint["channel_values"]))!=packet.cursor.checkpoint_state_sha256):
                raise ValueError("Budget PAUSE projection latest checkpoint differs from exact original predecessor")
            original_payload=self.business_state_codec.restore_payload(original)
            update=self._budget_pending_update(original,original_payload,wait,packet)
            graph.update_state(self._recovery_checkpoint_config(workflow,latest.config,run_id=graph_state.run_id),update,as_node="evaluate_document_gate")
            graph.invoke(None,config=self._invoke_config(workflow,run_id=graph_state.run_id), durability="sync")
        self.validate_budget_wait_for_resume(workflow,graph_state,wait=packet)
        return self.inspect(workflow,run_id=graph_state.run_id)

    def _budget_packet_for_transition(self, workflow, transition, *, payload=None, validate_live_sources=True):
        _, lifecycle, citation = self._budget_owners()
        wait = (transition if transition.kind == "PAUSE" else lifecycle.get_budget_transition(
            transition.wait_event_ref, transition.wait_event_sha256))
        envelope, raw = citation.store.get_json_with_reference(wait.wait_ref,
            task_id=wait.resulting_run.identity.task_id, run_id=wait.resulting_run.identity.run_id)
        if envelope.content_hash != "sha256:" + wait.wait_sha256:
            raise ValueError("Budget native wait packet content hash changed")
        packet = BudgetDocumentWaitPacketSchema.model_validate(raw)
        if (packet.run_preimage.lifecycle_revision + 1 != wait.resulting_run.lifecycle_revision
                or packet.current_children != wait.current_children
                or packet.child_preimage_sha256 != wait.child_preimage_sha256
                or packet.refusal.semantic_operation_ref != wait.semantic_operation_ref
                or packet.refusal.semantic_input_sha256 != wait.semantic_input_sha256
                or str(packet.refusal.artifact_version) != wait.artifact_version):
            raise ValueError("Budget packet/native lifecycle complete binding differs")
        unchanged_run_fields = {"status","lifecycle_revision","updated_at"}
        if (packet.run_preimage.status.value != "running" or wait.resulting_run.status.value != "paused"
                or packet.run_preimage.model_dump(mode="json",exclude=unchanged_run_fields)
                   != wait.resulting_run.model_dump(mode="json",exclude=unchanged_run_fields)):
            raise ValueError("Budget native wait full Run preimage differs")
        self._validate_budget_cursor(workflow, packet, payload=payload,
            validate_live_sources=validate_live_sources)
        return wait, packet

    def _budget_latest_pending(self, workflow, *, graph_state, wait, packet, continuation=None):
        latest = self.checkpointer.get_tuple(phase2_checkpoint_identity(workflow, run_id=graph_state.run_id))
        if latest is None:
            raise ValueError("Budget pending checkpoint is absent")
        values, payload, count, _ = self._recovery_checkpoint_values(
            workflow, latest, task_id=graph_state.task_id, run_id=graph_state.run_id)
        cursor = packet.cursor
        if (count != cursor.predecessor_transition_count
                or tuple(values.get("node_outputs") or ()) != cursor.predecessor_committed_node_refs
                or values.get("current_business_node") != cursor.predecessor_business_node
                or payload.failed_business_node is not None or payload.terminal_error is not None
                or payload.terminal_agent_result is not None or payload.capture_completed
                or payload.budget_wait_ref != wait.wait_ref or payload.budget_wait_sha256 != wait.wait_sha256
                or payload.budget_wait_event_ref != wait.event_ref
                or payload.budget_wait_event_sha256 != wait.event_sha256
                or payload.budget_materialization_ref != cursor.budget_materialization_ref):
            raise ValueError("Budget pending checkpoint/committed source occurrence differs")
        self._validate_budget_cursor(workflow, packet, payload=payload)
        inspection = self.inspect(workflow, run_id=graph_state.run_id)
        if (payload.pause_purpose == "BUDGET_AUTHORIZATION"
                and payload.budget_document_continuation is None
                and values.get("business_route") == "budget_wait"
                and inspection.get("pause_purpose") == "BUDGET_AUTHORIZATION"
                and inspection.get("next_nodes") == ["budget_authorization_interrupt"]):
            mode = "INTERRUPTED" if inspection.get("interrupts") else "CONTROL_PENDING"
        elif (continuation is not None and payload.pause_purpose is None
                and payload.budget_document_continuation == continuation
                and values.get("business_route") == "continue"
                and not inspection.get("interrupts")
                and inspection.get("next_nodes") == ["evaluate_document_gate"]):
            mode = "PENDING_DOCUMENT"
        else:
            raise ValueError("Budget pending cursor is neither exact budget interrupt nor approved DOCUMENT gate")
        restored = self.runtime_state_codec.restore_graph_state(values, workflow=workflow)
        return latest, values, payload, restored, mode

    def validate_budget_wait_for_resume(self, workflow, graph_state, wait=None):
        """Passive pre-resume checks while PAUSED; no write/committable bypass."""
        _, lifecycle, _ = self._budget_owners()
        current = lifecycle.current_budget_transition(graph_state.run_id)
        if current is None or current.kind != "PAUSE":
            raise ValueError("Budget approval/resume requires native current PAUSED wait")
        native_wait, packet = self._budget_packet_for_transition(workflow, current)
        if wait is not None and wait != packet:
            raise ValueError("Supplied wait differs from immutable native budget packet")
        preimage = lifecycle.budget_preimage(graph_state.run_id)
        if (preimage.run != native_wait.resulting_run
                or preimage.current_children != native_wait.current_children
                or preimage.child_preimage_sha256 != native_wait.child_preimage_sha256):
            raise ValueError("Budget approval/resume native PAUSED Run/Children changed")
        latest, values, payload, restored, mode = self._budget_latest_pending(workflow,
            graph_state=graph_state, wait=native_wait, packet=packet)
        if mode != "INTERRUPTED":
            raise ValueError("Budget authorization checkpoint is not parked at exact interrupt")
        return {"phase":"PAUSED_DOCUMENT_WAIT", "graph_state":restored,
            "wait_ref":native_wait.wait_ref, "wait_sha256":native_wait.wait_sha256,
            "source_epoch_sha256":packet.cursor.source_epoch_sha256,
            "checkpoint_config":packet.cursor.checkpoint_config,
            "checkpoint_state_sha256":packet.cursor.checkpoint_state_sha256,
            "pending_checkpoint_config":self._recovery_native_config(workflow,latest.config,run_id=graph_state.run_id),
            "pending_checkpoint_state_sha256":stable_graph_hash(self._budget_checkpoint_material(values))}

    def validate_budget_execution_cursor(self, workflow, *, graph_state, continuation, original_claim=None):
        """Native protocol eligibility; caller separately proves old process closed."""
        post_write, lifecycle, citation = self._budget_owners()
        bound = BudgetDocumentContinuationSchema.model_validate(continuation)
        resume = lifecycle.get_budget_transition(bound.resume_event_ref,bound.resume_event_sha256)
        if (resume.kind != "RESUME" or resume.resulting_run.identity.task_id != graph_state.task_id
                or resume.resulting_run.identity.run_id != graph_state.run_id
                or resume.grant_ref != bound.accepted_grant_ref or resume.grant_sha256 != bound.accepted_grant_sha256):
            raise ValueError("Budget continuation differs from native approved resume")
        persisted = self._latest_checkpoint_payload(workflow,run_id=graph_state.run_id)
        wait, packet = self._budget_packet_for_transition(workflow,resume,payload=persisted,
            validate_live_sources=False)
        if (bound.predecessor_result_ref != packet.refusal.native_denial_ref
                or bound.predecessor_result_sha256 != packet.refusal.native_denial_sha256):
            raise ValueError("Budget continuation differs from exact original failed DOCUMENT receipt")
        if original_claim is not None:
            identity = original_claim.get("identity") or {}
            if (original_claim.get("schema_version") != "budget_document_occurrence_claim_v1"
                    or original_claim.get("source_epoch_sha256") != packet.cursor.source_epoch_sha256
                    or original_claim.get("checkpoint_state_sha256") != packet.cursor.checkpoint_state_sha256
                    or original_claim.get("checkpoint_config") != packet.cursor.checkpoint_config
                    or original_claim.get("wait_ref") != wait.wait_ref
                    or original_claim.get("wait_sha256") != wait.wait_sha256
                    or identity.get("task_id") != graph_state.task_id
                    or identity.get("run_id") != graph_state.run_id
                    or identity.get("resume_occurrence_id") != resume.resume_occurrence_id
                    or identity.get("wait_event_ref") != wait.event_ref
                    or identity.get("wait_event_sha256") != wait.event_sha256
                    or identity.get("grant_ref") != resume.grant_ref
                    or identity.get("grant_sha256") != resume.grant_sha256
                    or identity.get("resume_event_ref") != resume.event_ref
                    or identity.get("resume_event_sha256") != resume.event_sha256
                    or identity.get("child_preimage_sha256") != resume.child_preimage_sha256
                    or identity.get("continuation") != bound.model_dump(mode="json")):
                raise ValueError("Cold claim differs from native source/checkpoint/approved occurrence")
        basis = {"graph_state":graph_state,"checkpoint_config":packet.cursor.checkpoint_config,
            "checkpoint_state_sha256":packet.cursor.checkpoint_state_sha256,
            "source_epoch_sha256":packet.cursor.source_epoch_sha256,
            "resume_occurrence_id":resume.resume_occurrence_id,
            "wait_ref":wait.wait_ref,"wait_sha256":wait.wait_sha256}
        inspection = self.inspect(workflow,run_id=graph_state.run_id)
        current = lifecycle.load_run_lifecycle(graph_state.run_id)
        terminal = inspection.get("terminal_agent_result")
        if terminal is not None and not inspection.get("next_nodes") and not inspection.get("interrupts"):
            native_result = AgentResultSchema.model_validate(terminal)
            owner_outcome = persisted.terminal_outcome
            if (native_result.task_id != graph_state.task_id or native_result.run_id != graph_state.run_id
                    or owner_outcome is None or owner_outcome.run_status != current.status
                    or native_result.status != owner_outcome.execution_status
                    or native_result.result.get("terminal_outcome") != owner_outcome.model_dump(mode="json")):
                raise ValueError("Budget terminal checkpoint differs from exact owner status axes")
            if current.status.value == "success":
                if (persisted.target_run_completion_assessment_ref != current.run_completion_assessment_ref
                        or current.run_completion_assessment_ref is None):
                    raise ValueError("Budget terminal checkpoint lacks exact canonical assessment")
                post_write.terminalization_resolver.resolve_exact(current.run_completion_assessment_ref)
                release = persisted.target_release_publication
                if release is not None:
                    native_ref, manifest = post_write.release_store.get_json_with_reference(
                        release.manifest_artifact.artifact_ref,task_id=graph_state.task_id,run_id=graph_state.run_id)
                    if native_ref != release.manifest_artifact or manifest != release.manifest.model_dump(mode="json"):
                        raise ValueError("Budget terminal checkpoint Release differs from native manifest")
            elif current.status.value not in {"failed","cancelled","partial_success"}:
                raise ValueError("Budget checkpoint result has no canonical terminal authority")
            return {**basis,"phase":"GRAPH_COMPLETED",
                "graph_state":GraphStateSchema.model_validate(inspection["graph_state"]),
                "terminal_agent_result":native_result}
        # Immutable terminal reads never grant entry permission. Every pending
        # execution path restores the unchanged live failed-head/source guard.
        self._validate_budget_cursor(workflow, packet, payload=persisted)
        preimage = lifecycle.budget_preimage(graph_state.run_id)
        if (preimage.run != resume.resulting_run or preimage.current_children != resume.current_children
                or preimage.child_preimage_sha256 != resume.child_preimage_sha256):
            raise ValueError("Budget continuation canonical RUNNING Run/full Children changed")
        latest, values, payload, restored, mode = self._budget_latest_pending(workflow,
            graph_state=graph_state,wait=wait,packet=packet,continuation=bound)
        slot = citation.read_budget_document_admission(bound,identity={
            name:getattr(packet.refusal,name) for name in ("task_id","run_id","section_id")},
            include_grounding_commit=True)
        if mode not in {"INTERRUPTED","PENDING_DOCUMENT"}:
            raise ValueError("Approved budget execution requires an actual parked/pending DOCUMENT cursor")
        phase = slot.get("phase")
        if phase not in {"PRE_CITATION_INTENT_SAFE","COMPLETED","INDETERMINATE"}:
            raise ValueError("Citation native admission classifier returned unknown phase")
        return {**basis,"phase":phase,"graph_state":restored,
            "pending_checkpoint_config":self._recovery_native_config(workflow,latest.config,run_id=graph_state.run_id),
            "pending_checkpoint_state_sha256":stable_graph_hash(self._budget_checkpoint_material(values)),
            "pending_cursor_mode":mode,"native_admission":slot}

    def resume_budget_document(self, workflow, graph_state, continuation):
        bound = BudgetDocumentContinuationSchema.model_validate(continuation)
        proof = self.validate_budget_execution_cursor(workflow,graph_state=graph_state,continuation=bound)
        if proof["phase"] == "INDETERMINATE":
            raise ValueError("Budget DOCUMENT admission intent is unresolved; no redispatch")
        if proof["phase"] == "GRAPH_COMPLETED":
            latest=self.checkpointer.get_tuple(phase2_checkpoint_identity(workflow,run_id=graph_state.run_id))
            return self._result_from_runtime(workflow=workflow,graph_state=graph_state,
                final_runtime=latest.checkpoint["channel_values"],initial_revision=graph_state.graph_revision,resumed=True)
        command = (Command(resume={"pause_purpose":"BUDGET_AUTHORIZATION",
            "continuation":bound.model_dump(mode="json")})
            if proof["pending_cursor_mode"] == "INTERRUPTED" else None)
        initial_revision = graph_state.graph_revision
        final_runtime = self.build_graph(workflow).invoke(command,
            config=self._invoke_config(workflow,run_id=graph_state.run_id), durability="sync")
        return self._result_from_runtime(workflow=workflow,graph_state=graph_state,
            final_runtime=final_runtime,initial_revision=initial_revision,resumed=True)

    def execute(
        self,
        workflow: WorkflowDefinitionSchema,
        graph_state: GraphStateSchema,
    ) -> WorkflowEngineResultSchema:
        """Start a durable business graph and return terminal or paused state."""

        self._assert_new_run_storage_admission(graph_state.run_id)
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
            durability="sync",
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
        if persisted.pause_purpose == "BUDGET_AUTHORIZATION":
            decision = str(resume_value.get("decision") or "").strip().lower()
            if decision not in {"cancel","cancelled","canceled"}:
                raise ValueError("Budget pause requires separate trusted Budget authorization/resume")
            initial_revision = graph_state.graph_revision
            final_runtime = self.build_graph(workflow).invoke(Command(resume={
                "pause_purpose":"BUDGET_AUTHORIZATION","cancelled":True,
                "reason":str(resume_value.get("feedback") or resume_value.get("reason") or "explicit cancellation")}),
                config=self._invoke_config(workflow,run_id=graph_state.run_id), durability="sync")
            return self._result_from_runtime(workflow=workflow,graph_state=graph_state,
                final_runtime=final_runtime,initial_revision=initial_revision,resumed=True)
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
            durability="sync",
        )
        return self._result_from_runtime(
            workflow=workflow,
            graph_state=graph_state,
            final_runtime=final_runtime,
            initial_revision=initial_revision,
            resumed=True,
        )

    def _recovery_predecessors(self, failed_node: str) -> tuple[str, ...]:
        """Compatibility introspection only; recovery requires exact labeled edges."""

        return tuple(dict.fromkeys(
            edge.source for edge in self._active_route_edges()
            if edge.destination == failed_node and edge.router_kind is not None
        ))

    def _recovery_native_config(
        self, workflow: WorkflowDefinitionSchema, config: dict[str, Any],
        *, run_id: str,
    ) -> dict[str, Any]:
        """Validate supplied scope before constructing a native saver query."""

        if not isinstance(config, dict) or not isinstance(config.get("configurable"), dict):
            raise ValueError("recovery checkpoint config is unavailable")
        supplied = config["configurable"]
        namespace = self.runtime_state_codec.workflow_namespace(workflow)
        checkpoint_id = supplied.get("checkpoint_id")
        physical_namespace = supplied.get("checkpoint_ns")
        carrier = supplied.get("workflow_namespace")
        if (
            supplied.get("thread_id") != run_id
            or not isinstance(checkpoint_id, str) or not checkpoint_id
            or physical_namespace not in ("", namespace)
            or carrier not in (None, namespace)
            or (physical_namespace == "" and carrier != namespace)
        ):
            raise ValueError("recovery checkpoint identity mismatch")
        native = phase2_checkpoint_identity(workflow, run_id=run_id)
        native["configurable"]["checkpoint_id"] = checkpoint_id
        return native

    def _recovery_checkpoint_values(
        self, workflow: WorkflowDefinitionSchema, checkpoint_tuple: Any,
        *, task_id: str, run_id: str,
    ) -> tuple[dict[str, Any], SchemeGenerationGraphPayloadSchema, int, str]:
        checkpoint = getattr(checkpoint_tuple, "checkpoint", None)
        values = checkpoint.get("channel_values") if isinstance(checkpoint, dict) else None
        if not isinstance(values, dict):
            raise ValueError("recovery checkpoint runtime state is unavailable")
        graph = self.runtime_state_codec.restore_graph_state(values, workflow=workflow)
        payload = self.business_state_codec.restore_payload(values)
        if (
            graph.task_id != task_id or graph.run_id != run_id
            or payload.task_id != task_id or payload.run_id != run_id
            or payload.workflow_namespace != self.runtime_state_codec.workflow_namespace(workflow)
        ):
            raise ValueError("recovery checkpoint identity mismatch")
        outputs = values.get("node_outputs")
        count = values.get("transition_count")
        node = values.get("current_business_node")
        if (
            type(count) is not int or count < 0 or not isinstance(outputs, list)
            or payload.business_revision != count or len(outputs) != count
            or len(payload.executed_nodes) != count
            or any(not isinstance(output, dict) for output in outputs)
            or [output.get("node_id") for output in outputs] != payload.executed_nodes
            or (count and (outputs[-1].get("node_id") != node or graph.current_node_id != node))
        ):
            raise ValueError("recovery business occurrence commit proof is unavailable")
        signature = stable_graph_hash({
            "business_revision": payload.business_revision,
            "transition_count": count,
            "node_outputs": outputs,
            "executed_nodes": payload.executed_nodes,
            "current_business_node": node,
            "business_route": values.get("business_route"),
            "failed_business_node": payload.failed_business_node,
            "terminal_error": payload.terminal_error.model_dump(mode="json") if payload.terminal_error else None,
        })
        return values, payload, count, signature

    @staticmethod
    def _recovery_primary_error(error: Any) -> Any:
        """Compare the primary error without its later owner acknowledgement."""
        if not isinstance(error, dict):
            return error
        details = error.get("details")
        if not isinstance(details, dict) or "lifecycle_composite_transition" not in details:
            return error
        return {
            **error,
            "details": {key: value for key, value in details.items()
                        if key != "lifecycle_composite_transition"},
        }

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
        task_id = inspection["graph_state"].get("task_id")
        if not isinstance(task_id, str) or not task_id or inspection["graph_state"].get("run_id") != run_id:
            raise ValueError("recovery checkpoint identity mismatch")
        inspected = self._recovery_native_config(
            workflow, inspection["checkpoint_config"], run_id=run_id,
        )
        latest = self.checkpointer.get_tuple(phase2_checkpoint_identity(workflow, run_id=run_id))
        if latest is None:
            raise ValueError("durable business checkpoint is required for recovery")
        latest_config = self._recovery_native_config(workflow, latest.config, run_id=run_id)
        if latest_config["configurable"]["checkpoint_id"] != inspected["configurable"]["checkpoint_id"]:
            raise ValueError("current recovery checkpoint changed during inspection")
        values, persisted, count, signature = self._recovery_checkpoint_values(
            workflow, latest, task_id=task_id, run_id=run_id,
        )
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

        failed_node = str(persisted.failed_business_node or "").strip()
        if (
            not failed_node or persisted.terminal_error is None
            or persisted.terminal_error.failed_node != failed_node
        ):
            raise ValueError("checkpoint does not prove a recoverable interruption")
        error = self._recovery_primary_error(persisted.terminal_error.model_dump(mode="json"))
        occurrences = [
            index + 1 for index, output in enumerate(values["node_outputs"])
            if output.get("node_id") == failed_node
            and self._recovery_primary_error(output.get("error")) == error
            and output.get("status") in {
                ExecutionStatus.FAILED.value, ExecutionStatus.RETRYABLE_FAILED.value,
                ExecutionStatus.CANCELLED.value,
            }
        ]
        if not occurrences:
            raise ValueError("current failed business occurrence cannot be proven")
        failed_count = occurrences[-1]
        route_edges = self._active_route_edges()
        visited: set[tuple[str, str, str]] = set()
        reads = 0

        def read_exact(config: dict[str, Any]):
            nonlocal reads
            native = self._recovery_native_config(workflow, config, run_id=run_id)
            identity = native["configurable"]
            key = (identity["thread_id"], identity["checkpoint_ns"], identity["checkpoint_id"])
            if key in visited or reads >= _RECOVERY_PARENT_CHAIN_LIMIT:
                raise ValueError("recovery parent chain has a cycle or exceeds its bound")
            visited.add(key)
            reads += 1
            row = self.checkpointer.get_tuple(native)
            if row is None:
                raise ValueError("recovery parent checkpoint is missing")
            actual = self._recovery_native_config(workflow, row.config, run_id=run_id)
            if actual["configurable"]["checkpoint_id"] != key[2]:
                raise ValueError("recovery parent checkpoint identity mismatch")
            state = self._recovery_checkpoint_values(workflow, row, task_id=task_id, run_id=run_id)
            return row, state

        row, state = read_exact(latest.config)
        while True:
            values, _payload, count, signature = state
            parent_config = getattr(row, "parent_config", None)
            if not parent_config:
                raise ValueError("checkpoint has no safe recovery predecessor")
            parent, parent_state = read_exact(parent_config)
            parent_values, _parent_payload, parent_count, parent_signature = parent_state
            if parent_count == count:
                if parent_signature != signature:
                    raise ValueError("physical checkpoint copy changes business occurrence proof")
            elif (
                parent_count != count - 1
                or values["node_outputs"][:-1] != parent_values["node_outputs"]
            ):
                raise ValueError("recovery parent does not prove the prior business commit")
            elif count == failed_count:
                if (
                    values.get("current_business_node") != failed_node or parent_count == 0
                    or _payload.failed_business_node != failed_node
                    or _payload.terminal_error is None
                    or self._recovery_primary_error(_payload.terminal_error.model_dump(mode="json")) != error
                ):
                    raise ValueError("current failed business occurrence cannot be proven")
                predecessor = parent_values.get("current_business_node")
                label = self._effective_route_label(predecessor, parent_values, route_edges=route_edges)
                if not any(
                    edge.source == predecessor and edge.route == label
                    and edge.destination == failed_node and edge.router_kind is not None
                    for edge in route_edges
                ):
                    raise ValueError("nearest recovery predecessor has no matching effective edge")
                # The prior occurrence may itself have several physical carry
                # rows. Return its actual business commit, not a later copy.
                while getattr(parent, "parent_config", None):
                    prior, prior_state = read_exact(parent.parent_config)
                    prior_values, _prior_payload, prior_count, prior_signature = prior_state
                    if prior_count == parent_count:
                        if prior_signature != parent_signature:
                            raise ValueError("physical checkpoint copy changes business occurrence proof")
                        parent, parent_state = prior, prior_state
                        parent_values, _parent_payload, parent_count, parent_signature = parent_state
                    else:
                        if (
                            prior_count != parent_count - 1
                            or parent_values["node_outputs"][:-1] != prior_values["node_outputs"]
                        ):
                            raise ValueError("recovery predecessor business commit proof is unavailable")
                        break
                return {
                    "checkpoint_config": self._recovery_checkpoint_config(workflow, parent.config, run_id=run_id),
                    "reason": "terminal_failure_checkpoint_branch",
                    "failed_business_node": failed_node,
                    "predecessor": predecessor,
                }
            if parent_count < failed_count:
                raise ValueError("current failed business occurrence cannot be proven")
            row, state = parent, parent_state

    def _recovery_checkpoint_config(
        self,
        workflow: WorkflowDefinitionSchema,
        config: dict[str, Any],
        *,
        run_id: str,
    ) -> dict[str, Any]:
        """Restore the logical namespace carrier for an exact checkpoint."""

        base = self._invoke_config(workflow, run_id=run_id)
        native = self._recovery_native_config(workflow, config, run_id=run_id)
        checkpoint_id = native["configurable"]["checkpoint_id"]
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
        if self.memory_runtime is not None:
            self.memory_runtime.reconcile_run(graph_state.run_id)
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
                durability="sync",
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
        budget_purpose = bool(restored_payload is not None
            and restored_payload.pause_purpose == "BUDGET_AUTHORIZATION"
            and all(isinstance(row["value"],dict) and row["value"].get("pause_purpose")=="BUDGET_AUTHORIZATION" for row in interrupts))
        if interrupts and not next_nodes:
            next_nodes = ["budget_authorization_interrupt" if budget_purpose else "human_review_interrupt"]
        return {
            "run_id": run_id,
            "workflow_id": workflow.workflow_id,
            "workflow_version": workflow.workflow_version,
            "checkpoint_config": dict(snapshot.config),
            "next_nodes": next_nodes,
            "terminal_agent_result": (
                restored_payload.terminal_agent_result.model_dump(mode="json")
                if restored_payload is not None and restored_payload.terminal_agent_result is not None
                and not next_nodes and not interrupts else None
            ),
            "interrupts": interrupts,
            "graph_state": (
                restored_graph_state.model_dump(mode="json")
                if restored_graph_state is not None
                else None
            ),
            "pause_purpose":"BUDGET_AUTHORIZATION" if budget_purpose else None,
            "budget_wait_ref":restored_payload.budget_wait_ref if budget_purpose else None,
            "budget_wait_sha256":restored_payload.budget_wait_sha256 if budget_purpose else None,
            "budget_wait_event_ref":restored_payload.budget_wait_event_ref if budget_purpose else None,
            "budget_wait_event_sha256":restored_payload.budget_wait_event_sha256 if budget_purpose else None,
            "human_review_required": bool(interrupts) and not budget_purpose,
            "human_review_status": (
                HumanReviewStatus.PENDING.value
                if interrupts and not budget_purpose
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
            self._assert_new_run_storage_admission(graph_state.run_id)
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
            persisted=self._latest_checkpoint_payload(workflow,run_id=graph_state.run_id)
            if persisted is not None and persisted.pause_purpose=="BUDGET_AUTHORIZATION":
                decision=str(resume_value.get("decision") or "").strip().lower()
                if decision not in {"cancel","cancelled","canceled"}:
                    raise ValueError("Budget pause requires separate trusted Budget authorization/resume")
                resume_value={"pause_purpose":"BUDGET_AUTHORIZATION","cancelled":True,
                    "decision":"cancelled","reason":str(resume_value.get("feedback") or resume_value.get("reason") or "explicit cancellation")}
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
                "budget_authorization_cancelled" if resume_value.get("pause_purpose")=="BUDGET_AUTHORIZATION" else "human_review_resumed",
                status=ExecutionStatus.RUNNING,
                node_name="budget_authorization_interrupt" if resume_value.get("pause_purpose")=="BUDGET_AUTHORIZATION" else "human_review_interrupt",
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
            durability="sync",
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
                "budget_authorization_required" if result.metadata.get("pause_purpose")=="BUDGET_AUTHORIZATION" else "human_review_required",
                status=result.status,
                node_name="budget_authorization_interrupt" if result.metadata.get("pause_purpose")=="BUDGET_AUTHORIZATION" else "human_review_interrupt",
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
