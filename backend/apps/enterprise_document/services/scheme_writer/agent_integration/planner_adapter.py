"""Connect the stateless PlannerAgent to the production plan_document node.

The adapter owns context construction, Trace v2 events, error normalization and
fixed projection into the existing business payload. It never applies the
Standalone StatePatch protocol and never creates a second Shared State.
"""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
from typing import Any, Mapping

from contracts.observability import TraceSink
from observability.trace_context import activate_span, current_span, new_span

from apps.enterprise_document.agents.agent_rag_v1 import PlannerAgent
from apps.enterprise_document.schemas.agent_rag_v1 import (
    AgentExecutionContextSchema,
    AgentInvocationStatus,
    AgentRole,
    PlannerExecutionMode,
    PlannerInvocationInputSchema,
    PlannerResultPayloadSchema,
    WorkflowErrorCategory,
    WorkflowRetryability,
)
from apps.enterprise_document.schemas.agent_rag_v1._base import ContractSchema
from apps.enterprise_document.schemas.project_input_schema import ProjectInputSchema
from apps.enterprise_document.schemas.agent_rag_v1.lifecycle import ActiveExecutionLineageSchema
from apps.enterprise_document.schemas.scheme_writer.planning import DocumentPlanSchema
from schemas.common import ErrorSchema, ErrorSourceSchema
from schemas.failure import FailureCategory
from schemas.status import ExecutionStatus
from schemas.trace import TraceCorrelationSchema

from ..document_planning_service import DocumentPlanningService


class _PlannerToolSession:
    """Planner V1 has no production tools in Round 3."""

    def __init__(self) -> None:
        self.call_refs: list[str] = []

    def call(self, tool_name: str, arguments: Mapping[str, Any]):
        del tool_name, arguments
        raise PermissionError("Round-3 production PlannerAgent has no tool access")


class _PlannerToolGateway:
    """Minimal gateway seam without a second ToolExecutionStore."""

    @staticmethod
    def session(context: AgentExecutionContextSchema) -> _PlannerToolSession:
        del context
        return _PlannerToolSession()


class _DeterministicPlannerReasoner:
    """Deterministic section skeleton with an optional governed fact proposer."""

    def __init__(self, planning_service: DocumentPlanningService, fact_requirement_runtime: Any | None = None) -> None:
        self.planning_service = planning_service
        self.fact_requirement_runtime = fact_requirement_runtime

    def invoke(
        self,
        *,
        context: AgentExecutionContextSchema,
        invocation_input: ContractSchema,
        tools: _PlannerToolSession,
    ) -> PlannerResultPayloadSchema:
        del tools
        invocation = PlannerInvocationInputSchema.model_validate(invocation_input)
        project_input = invocation.project_input
        document_id = invocation.document_id
        created_at = invocation.created_at
        if project_input is None or not document_id or not created_at:
            raise ValueError(
                "production planner requires project_input, document_id and created_at"
            )
        if not invocation.required_sections:
            raise ValueError("production planner requires required_sections")

        plan = self.planning_service.build_document_plan(
            run_id=context.run_id,
            document_id=document_id,
            project_input=project_input,
            required_sections=list(invocation.required_sections),
            created_at=created_at,
        )
        if self.fact_requirement_runtime is not None:
            plan = self.fact_requirement_runtime.enrich(
                context=context, project_input=project_input, document_plan=plan,
            )
        plan = plan.model_copy(
            update={
                "planning_source": invocation.planning_source,
                "metadata": {
                    **dict(plan.metadata),
                    "planner_agent": "PlannerAgent",
                    "planner_execution_mode": context.execution_mode,
                    "planner_invocation_id": context.invocation_id,
                    "plan_revision": invocation.plan_revision,
                },
            },
            deep=True,
        )
        section_ids = [section.section_id for section in plan.sections]
        return PlannerResultPayloadSchema(
            plan_ref=(
                f"state://document-plan/{plan.plan_id}/v{invocation.plan_revision}"
            ),
            plan_revision=invocation.plan_revision,
            section_task_ids=section_ids,
            affected_section_ids=section_ids,
            document_plan=plan,
            planner_metadata={
                "reasoner": "deterministic_document_planning_service",
                "planning_source": invocation.planning_source,
            },
        )


@dataclass(frozen=True)
class MainlinePlannerOutcome:
    document_plan: DocumentPlanSchema
    plan_revision: int
    input_snapshot_version: str
    invocation_id: str
    execution_id: str
    attempt_id: str
    trace_ref: str
    emitted_signals: tuple[str, ...]
    metadata: dict[str, Any]


# Exception transport must allow LangGraph to restore __traceback__.
@dataclass
class MainlinePlannerFailure(Exception):
    error: ErrorSchema

    def __str__(self) -> str:
        return self.error.message


class MainlinePlannerAdapter:
    """Invoke PlannerAgent and project only its canonical plan into mainline."""

    def __init__(
        self,
        *,
        planner_agent: PlannerAgent,
        trace_sink: TraceSink | None = None,
    ) -> None:
        self.planner_agent = planner_agent
        self.trace_sink = trace_sink

    @staticmethod
    def _snapshot_version(
        project_input: ProjectInputSchema,
        required_sections: list[str],
    ) -> str:
        payload = {
            "project_input": project_input.model_dump(mode="json", round_trip=True),
            "required_sections": list(required_sections),
        }
        serialized = json.dumps(
            payload,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        return "sha256:" + hashlib.sha256(serialized.encode("utf-8")).hexdigest()

    @staticmethod
    def _failure_category(category: WorkflowErrorCategory) -> FailureCategory:
        if category in {
            WorkflowErrorCategory.AGENT_OUTPUT_INVALID,
            WorkflowErrorCategory.SCHEMA_VIOLATION,
            WorkflowErrorCategory.BUSINESS_INPUT_MISSING,
        }:
            return FailureCategory.VALIDATION
        if category == WorkflowErrorCategory.EVIDENCE_INSUFFICIENT:
            return FailureCategory.EVIDENCE_INSUFFICIENT
        if category == WorkflowErrorCategory.BUDGET_EXHAUSTED:
            return FailureCategory.RESOURCE_EXHAUSTED
        if category == WorkflowErrorCategory.TRANSIENT_INFRASTRUCTURE:
            return FailureCategory.TRANSIENT
        return FailureCategory.UNKNOWN

    @classmethod
    def _normalize_error(cls, result) -> ErrorSchema:
        workflow_error = result.error
        if workflow_error is None:
            return ErrorSchema(
                error_code="PLANNER_AGENT_FAILED",
                error_type="PlannerAgentFailure",
                message="PlannerAgent failed without a structured error.",
                recoverable=False,
                retryable=False,
                failure_category=FailureCategory.UNKNOWN,
                failed_node="plan_document",
                source=ErrorSourceSchema(
                    component="MainlinePlannerAdapter",
                    agent_name="PlannerAgent",
                    step_name="plan_document",
                ),
            )
        retryable = workflow_error.retryability != WorkflowRetryability.NOT_RETRYABLE
        return ErrorSchema(
            error_id=workflow_error.error_id,
            error_code=workflow_error.error_code,
            error_type="PlannerAgentFailure",
            message=workflow_error.message,
            recoverable=retryable,
            retryable=retryable,
            failure_category=cls._failure_category(workflow_error.category),
            failed_node="plan_document",
            source=ErrorSourceSchema(
                component="MainlinePlannerAdapter",
                agent_name="PlannerAgent",
                step_name="plan_document",
            ),
            details={
                "workflow_error": workflow_error.model_dump(mode="json"),
            },
        )

    def _record_started(
        self,
        *,
        context: AgentExecutionContextSchema,
        section_count: int,
        handle,
    ) -> None:
        if self.trace_sink is None:
            return
        self.trace_sink.record(
            task_id=context.task_id,
            run_id=context.run_id,
            event_type="agent_started",
            component_type="agent",
            component_name="PlannerAgent",
            step_id="plan_document:planner_agent",
            step_name="plan_document",
            agent_name="PlannerAgent",
            caller="plan_document",
            callee="PlannerAgent",
            status=ExecutionStatus.RUNNING.value,
            correlation=TraceCorrelationSchema(
                attempt_id=context.attempt_id,
                agent_invocation_id=context.invocation_id,
                execution_id=context.execution_id,
                logical_operation_id=context.logical_operation_id,
                artifact_refs=list(context.authorized_artifact_refs),
            ),
            phase="start",
            trace_id=handle.trace_id,
            span_id=handle.span_id,
            parent_span_id=handle.parent_span_id,
            span_name=handle.span_name,
            span_kind=handle.span_kind,
            started_at=handle.started_at,
            input_summary={
                "invocation_id": context.invocation_id,
                "execution_mode": context.execution_mode,
                "input_snapshot_version": context.input_snapshot_version,
                "required_section_count": section_count,
                "authorized_tool_count": 0,
            },
            tags=["trace_v2", "agent", "planner", "mainline"],
        )

    def _record_finished(
        self,
        *,
        context: AgentExecutionContextSchema,
        result,
        handle,
        error: ErrorSchema | None,
    ) -> None:
        if self.trace_sink is None:
            return
        payload = result.payload
        section_count = (
            len(payload.section_task_ids)
            if isinstance(payload, PlannerResultPayloadSchema)
            else 0
        )
        self.trace_sink.record(
            task_id=context.task_id,
            run_id=context.run_id,
            event_type="agent_finished",
            component_type="agent",
            component_name="PlannerAgent",
            step_id="plan_document:planner_agent",
            step_name="plan_document",
            agent_name="PlannerAgent",
            caller="plan_document",
            callee="PlannerAgent",
            status=(
                ExecutionStatus.SUCCESS.value
                if error is None
                else ExecutionStatus.FAILED.value
            ),
            correlation=TraceCorrelationSchema(
                attempt_id=context.attempt_id,
                agent_invocation_id=context.invocation_id,
                execution_id=context.execution_id,
                logical_operation_id=context.logical_operation_id,
                artifact_refs=list(context.authorized_artifact_refs),
            ),
            error=error,
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
                "invocation_id": context.invocation_id,
                "agent_status": result.status.value,
                "plan_revision": (
                    payload.plan_revision
                    if isinstance(payload, PlannerResultPayloadSchema)
                    else None
                ),
                "section_count": section_count,
                "proposed_state_patch_count": len(result.proposed_state_patches),
                "error_code": error.error_code if error is not None else None,
            },
            tags=["trace_v2", "agent", "planner", "mainline"],
        )

    def plan(
        self,
        *,
        task_id: str,
        run_id: str,
        project_input: ProjectInputSchema,
        required_sections: list[str],
        document_id: str,
        created_at: str,
        plan_revision: int = 1,
        current_plan_ref: str | None = None,
        active_execution_lineage: ActiveExecutionLineageSchema | None = None,
        canonical_fact_snapshot_ref: str | None = None,
    ) -> MainlinePlannerOutcome:
        snapshot_version = self._snapshot_version(project_input, required_sections)
        invocation_id = f"{run_id}:planner:initial:v{plan_revision}"
        planner_attempt_id = f"{invocation_id}:attempt:1"
        task_ref = f"state://task/{task_id}"
        source_ref = f"state://project-input/{snapshot_version}"
        if active_execution_lineage is not None:
            lineage = ActiveExecutionLineageSchema.model_validate(active_execution_lineage)
            if (lineage.run_id != run_id or lineage.attempt_scope_ref != f"planning:{document_id}"
                    or not canonical_fact_snapshot_ref):
                raise ValueError("Planner requires coherent canonical planning lineage and Fact ref")
            snapshot_version = lineage.input_snapshot_version
            planner_attempt_id = lineage.attempt_id
            invocation_id = f"{run_id}:planner:initial:{planner_attempt_id}"
            source_ref = canonical_fact_snapshot_ref
        context = AgentExecutionContextSchema(
            invocation_id=invocation_id,
            logical_operation_id=f"{run_id}:plan_document",
            task_id=task_id,
            run_id=run_id,
            agent_role=AgentRole.PLANNER,
            execution_mode=PlannerExecutionMode.INITIAL_PLAN.value,
            execution_id=f"{invocation_id}:execution",
            attempt_id=planner_attempt_id,
            input_snapshot_version=snapshot_version,
            expected_domain_versions={},
            task_context_ref=task_ref,
            source_snapshot_ref=source_ref,
            execution_plan_ref=current_plan_ref,
            authorized_artifact_refs=([canonical_fact_snapshot_ref] if canonical_fact_snapshot_ref else []),
            authorized_tool_names=[],
            writable_state_paths=[],
            budget={"planning_calls": 1},
            trigger={"node": "plan_document"},
            trace_context={"trace_ref": f"trace://{invocation_id}"},
        )
        invocation = PlannerInvocationInputSchema(
            template_schema_ref=task_ref,
            source_inventory_ref=source_ref,
            fact_snapshot_ref=source_ref,
            current_plan_ref=current_plan_ref,
            target_section_ids=[],
            constraints={
                "generation_requirements": (
                    project_input.generation_requirements.model_dump(mode="json")
                ),
                "output_schema": project_input.output_schema.model_dump(mode="json"),
            },
            project_input=project_input,
            document_id=document_id,
            required_sections=list(required_sections),
            created_at=created_at,
            plan_revision=plan_revision,
            planning_source="planner_agent",
        )
        handle = new_span(
            run_id=run_id,
            span_name="agent:PlannerAgent",
            span_kind="internal",
            parent=current_span(),
        )
        self._record_started(
            context=context,
            section_count=len(required_sections),
            handle=handle,
        )
        with activate_span(handle):
            result = self.planner_agent.run_projected(context, invocation)
        error = None
        if result.status != AgentInvocationStatus.COMPLETED:
            error = self._normalize_error(result)
        self._record_finished(
            context=context,
            result=result,
            handle=handle,
            error=error,
        )
        if error is not None:
            raise MainlinePlannerFailure(error)
        if result.proposed_state_patches:
            raise MainlinePlannerFailure(
                ErrorSchema(
                    error_code="PLANNER_STATE_AUTHORITY_VIOLATION",
                    error_type="PlannerStateAuthorityViolation",
                    message=(
                        "Mainline PlannerAgent returned StatePatch proposals; "
                        "production projection must be fixed by the node adapter."
                    ),
                    recoverable=False,
                    retryable=False,
                    failure_category=FailureCategory.VALIDATION,
                    failed_node="plan_document",
                    source=ErrorSourceSchema(
                        component="MainlinePlannerAdapter",
                        agent_name="PlannerAgent",
                        step_name="plan_document",
                    ),
                )
            )
        payload = result.payload
        if not isinstance(payload, PlannerResultPayloadSchema):
            raise MainlinePlannerFailure(
                ErrorSchema(
                    error_code="PLANNER_PAYLOAD_MISSING",
                    error_type="PlannerPayloadMissing",
                    message="PlannerAgent completed without PlannerResultPayloadSchema.",
                    recoverable=False,
                    retryable=False,
                    failure_category=FailureCategory.VALIDATION,
                    failed_node="plan_document",
                )
            )
        plan = payload.document_plan
        if plan is None:
            raise MainlinePlannerFailure(
                ErrorSchema(
                    error_code="PLANNER_CANONICAL_PLAN_MISSING",
                    error_type="PlannerCanonicalPlanMissing",
                    message="PlannerAgent completed without a canonical DocumentPlanSchema.",
                    recoverable=False,
                    retryable=False,
                    failure_category=FailureCategory.VALIDATION,
                    failed_node="plan_document",
                )
            )
        # Re-validate independently after crossing the Agent boundary. This is
        # the existing PlanValidator fact source: Pydantic checks ID uniqueness,
        # dependency existence and DAG acyclicity.
        canonical_plan = DocumentPlanSchema.model_validate(
            plan.model_dump(mode="python", round_trip=True)
        )
        return MainlinePlannerOutcome(
            document_plan=canonical_plan,
            plan_revision=payload.plan_revision,
            input_snapshot_version=snapshot_version,
            invocation_id=result.invocation_id,
            execution_id=result.execution_id,
            attempt_id=result.attempt_id,
            trace_ref=result.trace_ref,
            emitted_signals=tuple(result.emitted_signals),
            metadata={
                **dict(payload.planner_metadata),
                "agent_role": result.agent_role.value,
                "agent_status": result.status.value,
                "execution_mode": result.execution_mode,
                "invocation_id": result.invocation_id,
                "execution_id": result.execution_id,
                "attempt_id": result.attempt_id,
                "trace_ref": result.trace_ref,
                "emitted_signals": list(result.emitted_signals),
                "proposed_state_patch_count": 0,
            },
        )


def build_mainline_planner_adapter(
    *,
    planning_service: DocumentPlanningService,
    trace_sink: TraceSink | None = None,
    fact_requirement_runtime: Any | None = None,
) -> MainlinePlannerAdapter:
    """Compose Planner; production injects its governed fact-requirement runtime."""

    agent = PlannerAgent(
        reasoner=_DeterministicPlannerReasoner(planning_service, fact_requirement_runtime),
        tool_gateway=_PlannerToolGateway(),  # type: ignore[arg-type]
    )
    return MainlinePlannerAdapter(
        planner_agent=agent,
        trace_sink=trace_sink,
    )
