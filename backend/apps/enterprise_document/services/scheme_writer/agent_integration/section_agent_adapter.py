"""Research/Writer/Verifier integration for the production Section graph.

Round 4 uses deterministic fake reasoners. Round 5 composes a Research ToolGateway
with the existing EvidenceService/RAGService and composes Writer/Verifier
reasoners with the existing ModelGateway. Both modes use the same fixed
production projector and never acquire direct GraphState write authority.
"""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
from typing import Any, Mapping

from agent.runtime.graph_state import GraphStateSchema
from apps.enterprise_document.quality.budget import (
    WorkflowBudget,
    activate_workflow_budget,
)

from contracts.model_gateway import ModelGatewayPort
from contracts.observability import TraceSink
from observability.trace_context import activate_span, current_span, new_span

from apps.enterprise_document.agents.agent_rag_v1 import (
    ResearchAgent,
    VerifierAgent,
    WriterAgent,
)
from apps.enterprise_document.schemas.agent_rag_v1 import (
    AgentExecutionContextSchema,
    AgentExecutionResultSchema,
    AgentInvocationStatus,
    AgentRole,
    ResearchEvidencePackageSchema,
    ResearchInvocationInputSchema,
    ResearchRecommendedAction,
    ResearchResultPayloadSchema,
    ResearchStatus,
    RejectedEvidenceSchema,
    SectionStrategy,
    VerifierAction,
    VerifierExecutionMode,
    VerifierInvocationInputSchema,
    VerifierResultPayloadSchema,
    WorkflowErrorCategory,
    WorkflowRetryability,
    WriterInvocationInputSchema,
    WriterResultPayloadSchema,
)
from apps.enterprise_document.schemas.agent_rag_v1._base import ContractSchema
from apps.enterprise_document.schemas.project_input_schema import ProjectInputSchema
from apps.enterprise_document.schemas.scheme_writer.document import SchemeSectionSchema
from apps.enterprise_document.schemas.scheme_writer.planning import SectionPlanSchema
from apps.enterprise_document.schemas.scheme_writer.state_projection import (
    ResearchProjectionStatus,
    SectionLifecycleStatus,
    SectionStateSchema,
    VerificationProjectionStatus,
    WritingProjectionStatus,
)
from schemas.common import ErrorSchema, ErrorSourceSchema
from schemas.failure import FailureCategory
from schemas.citation import CitationSchema
from schemas.status import ExecutionStatus

from ..evidence_service import SchemeEvidenceService
from .section_agent_ports import (
    BoundResearchToolRequest,
    MainlineResearchRetrieval,
    MainlineResearchToolGateway,
)
from .section_agent_reasoners import (
    MainlineResearchReasoner,
    MainlineVerifierModelReasoner,
    MainlineWriterModelReasoner,
)


class _NoToolSession:
    """Round-4 fake reasoners cannot call tools."""

    def __init__(self) -> None:
        self.call_refs: list[str] = []

    def call(self, tool_name: str, arguments: Mapping[str, Any]):
        del tool_name, arguments
        raise PermissionError("Round-4 fake section reasoners have no tool access")


class _NoToolGateway:
    """Minimal Agent gateway without a second ToolExecutionStore."""

    @staticmethod
    def session(context: AgentExecutionContextSchema) -> _NoToolSession:
        del context
        return _NoToolSession()


def _normalized_assessment_gaps(assessment: Mapping[str, Any]) -> list[str]:
    details = assessment.get("details")
    details = dict(details) if isinstance(details, Mapping) else {}
    candidates: list[Any] = []
    for key in (
        "gaps",
        "evidence_gaps",
        "missing_topics",
        "reasons",
        "reason_codes",
    ):
        value = details.get(key)
        if isinstance(value, list):
            candidates.extend(value)
        elif value not in (None, ""):
            candidates.append(value)
    reason = assessment.get("reason")
    if reason not in (None, ""):
        candidates.append(reason)
    normalized = [str(item).strip() for item in candidates if str(item).strip()]
    return list(dict.fromkeys(normalized))


class _Round4FakeResearchReasoner:
    """Derive a typed Research payload from canonical retrieval facts."""

    def invoke(
        self,
        *,
        context: AgentExecutionContextSchema,
        invocation_input: ContractSchema,
        tools: _NoToolSession,
    ) -> ResearchResultPayloadSchema:
        del tools
        invocation = ResearchInvocationInputSchema.model_validate(invocation_input)
        assessment = dict(invocation.evidence_assessment)
        raw_status = str(assessment.get("status") or "not_assessed").strip().lower()
        gaps = _normalized_assessment_gaps(assessment)

        if not invocation.retrieval_succeeded:
            research_status = ResearchStatus.CONFLICTED
            action = ResearchRecommendedAction.HUMAN_REVIEW
            if "retrieval_failed" not in gaps:
                gaps.append("retrieval_failed")
        elif raw_status == "insufficient":
            research_status = ResearchStatus.INSUFFICIENT
            action = ResearchRecommendedAction.PARTIAL_DELIVERY
            if not gaps:
                gaps.append("evidence_insufficient")
        elif raw_status == "sufficient":
            research_status = ResearchStatus.SUFFICIENT
            action = ResearchRecommendedAction.WRITE
        elif invocation.accepted_evidence_ids:
            research_status = ResearchStatus.SUFFICIENT
            action = ResearchRecommendedAction.WRITE
        elif not invocation.citation_required:
            research_status = ResearchStatus.SUFFICIENT
            action = ResearchRecommendedAction.WRITE
        else:
            research_status = ResearchStatus.INSUFFICIENT
            action = ResearchRecommendedAction.PARTIAL_DELIVERY
            if not gaps:
                gaps.append("evidence_not_assessed")

        rejected = [
            RejectedEvidenceSchema(
                evidence_id=evidence_id,
                reason_code="not_selected_by_canonical_retrieval",
            )
            for evidence_id in invocation.rejected_evidence_ids
        ]
        return ResearchResultPayloadSchema(
            section_id=invocation.section_id,
            evidence_package_ref=(
                f"state://section-evidence/{invocation.section_id}/"
                f"{invocation.retrieval_scope}/r{invocation.research_round}"
            ),
            evidence_summary=ResearchEvidencePackageSchema(
                section_id=invocation.section_id,
                accepted_evidence_ids=list(invocation.accepted_evidence_ids),
                rejected_evidence=rejected,
                supported_topics=list(invocation.evidence_topics),
                evidence_gaps=gaps,
                conflicts=(
                    [
                        {
                            "reason": "retrieval_failed",
                            "assessment": assessment,
                        }
                    ]
                    if not invocation.retrieval_succeeded
                    else []
                ),
                research_status=research_status,
                recommended_action=action,
            ),
            evidence_contract_sha256=invocation.evidence_contract_sha256,
            tool_call_ids=list(invocation.tool_call_ids),
            research_round=invocation.research_round,
            research_mode=invocation.research_mode,
            agent_metadata={
                "reasoner": "round4_fake_research_reasoner",
                "canonical_assessment_status": raw_status,
                "retrieval_scope": invocation.retrieval_scope,
                "fake_payload_only": True,
            },
        )


class _Round4FakeWriterReasoner:
    """Project the canonical generated Section into a typed Writer payload."""

    def invoke(
        self,
        *,
        context: AgentExecutionContextSchema,
        invocation_input: ContractSchema,
        tools: _NoToolSession,
    ) -> WriterResultPayloadSchema:
        del tools
        invocation = WriterInvocationInputSchema.model_validate(invocation_input)
        section = invocation.section_snapshot
        if section is None:
            raise ValueError("Round-4 Writer reasoner requires section_snapshot")
        canonical = SchemeSectionSchema.model_validate(
            section.model_dump(mode="python", round_trip=True)
        )
        generation_complete = bool(canonical.content.strip())
        return WriterResultPayloadSchema(
            section_id=invocation.section_id,
            draft_ref=(
                f"state://section-draft/{invocation.section_id}/"
                f"v{invocation.generation_attempt}"
            ),
            draft_version=invocation.generation_attempt,
            paragraphs=[],
            generation_complete=generation_complete,
            expected_paragraph_ids=[],
            generation_cursor=invocation.generation_cursor,
            section_snapshot=canonical,
            writer_metadata={
                "reasoner": "round4_fake_writer_reasoner",
                "canonical_section_status": canonical.status.value,
                "content_chars": len(canonical.content),
                "fake_payload_only": True,
                "paragraph_pipeline_attached": False,
            },
        )


class _Round4FakeVerifierReasoner:
    """Derive a Section route recommendation from canonical evaluation facts."""

    @staticmethod
    def _issue_id(section_id: str, message: str) -> str:
        digest = hashlib.sha256(
            f"{section_id}|{message}".encode("utf-8")
        ).hexdigest()[:16]
        return f"issue:{section_id}:{digest}"

    def invoke(
        self,
        *,
        context: AgentExecutionContextSchema,
        invocation_input: ContractSchema,
        tools: _NoToolSession,
    ) -> VerifierResultPayloadSchema:
        del tools
        invocation = VerifierInvocationInputSchema.model_validate(invocation_input)
        section = invocation.section_snapshot
        if section is None or invocation.section_id is None:
            raise ValueError("Round-4 Verifier reasoner requires a Section snapshot")
        canonical = SchemeSectionSchema.model_validate(
            section.model_dump(mode="python", round_trip=True)
        )
        checks = {
            str(name): bool(value)
            for name, value in invocation.generation_check.items()
        }
        failures: list[str] = []
        warnings: list[str] = []
        if canonical.eval_result is not None:
            checks = dict(canonical.eval_result.checks)
            failures = list(canonical.eval_result.failures)
            warnings = list(canonical.eval_result.warnings)

        need_more = bool(invocation.generation_check.get("need_retrieve_more"))
        citation_bound = bool(
            canonical.eval_result
            and canonical.eval_result.checks.get("citation_bound", False)
        )
        retrieval_route: str | None = None
        if need_more:
            action = VerifierAction.RESEARCH_MORE
            retrieval_route = "retrieve_more"
        elif (
            invocation.citation_required
            and not invocation.evidence_blocked
            and not citation_bound
            and invocation.corrective_retrieval_enabled
        ):
            action = VerifierAction.RESEARCH_MORE
            retrieval_route = "corrective_retrieval"
        elif canonical.status == ExecutionStatus.SUCCESS and (
            canonical.eval_result is None or canonical.eval_result.passed
        ):
            action = VerifierAction.ACCEPT
        elif canonical.status == ExecutionStatus.PARTIAL_SUCCESS:
            action = VerifierAction.PARTIAL_ACCEPT
        elif canonical.status in {
            ExecutionStatus.FAILED,
            ExecutionStatus.RETRYABLE_FAILED,
            ExecutionStatus.CANCELLED,
        }:
            action = VerifierAction.REJECT
        elif canonical.eval_result is not None and not canonical.eval_result.passed:
            action = VerifierAction.REJECT
        else:
            action = VerifierAction.ACCEPT

        issue_messages = list(dict.fromkeys([*failures, *warnings]))
        issue_ids = [
            self._issue_id(invocation.section_id, message)
            for message in issue_messages
        ]
        return VerifierResultPayloadSchema(
            execution_mode=invocation.execution_mode,
            action=action,
            issue_ids=issue_ids,
            accepted_paragraph_ids=[],
            rejected_paragraph_ids=[],
            verification_ref=(
                f"state://section-verification/{invocation.section_id}/"
                f"v{invocation.generation_attempt}"
            ),
            checks=checks,
            decision_metadata={
                "reasoner": "round4_fake_verifier_reasoner",
                "retrieval_route": retrieval_route,
                "canonical_section_status": canonical.status.value,
                "failure_count": len(failures),
                "warning_count": len(warnings),
                "fake_payload_only": True,
            },
        )


@dataclass(frozen=True)
class MainlineResearchOutcome:
    payload: ResearchResultPayloadSchema
    section_state: SectionStateSchema
    result: AgentExecutionResultSchema
    retrieval: MainlineResearchRetrieval | None = None


@dataclass(frozen=True)
class MainlineWriterOutcome:
    payload: WriterResultPayloadSchema
    section_state: SectionStateSchema
    result: AgentExecutionResultSchema
    budget_state: Mapping[str, Any]


@dataclass(frozen=True)
class MainlineVerifierOutcome:
    payload: VerifierResultPayloadSchema
    section_state: SectionStateSchema
    result: AgentExecutionResultSchema
    budget_state: Mapping[str, Any]


@dataclass(frozen=True)
class MainlineSectionAgentFailure(Exception):
    error: ErrorSchema

    def __str__(self) -> str:
        return self.error.message


class MainlineSectionAgentAdapter:
    """Invoke Section Agents and return fixed production projections."""

    def __init__(
        self,
        *,
        research_agent: ResearchAgent,
        writer_agent: WriterAgent,
        verifier_agent: VerifierAgent,
        trace_sink: TraceSink | None = None,
        research_tool_gateway: MainlineResearchToolGateway | None = None,
        integration_round: str = "round4",
        reasoner_kind: str = "fake",
    ) -> None:
        self.research_agent = research_agent
        self.writer_agent = writer_agent
        self.verifier_agent = verifier_agent
        self.trace_sink = trace_sink
        self.research_tool_gateway = research_tool_gateway
        self.integration_round = integration_round
        self.reasoner_kind = reasoner_kind

    @property
    def uses_research_tool_gateway(self) -> bool:
        return self.research_tool_gateway is not None

    @staticmethod
    def _snapshot_version(
        project_input: ProjectInputSchema,
        section_plan: SectionPlanSchema,
    ) -> str:
        serialized = json.dumps(
            {
                "project_input": project_input.model_dump(
                    mode="json",
                    round_trip=True,
                ),
                "section_plan": section_plan.model_dump(
                    mode="json",
                    round_trip=True,
                ),
            },
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
            WorkflowErrorCategory.PERMISSION_VIOLATION,
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
    def _normalize_error(
        cls,
        *,
        result: AgentExecutionResultSchema,
        role: AgentRole,
        node_name: str,
    ) -> ErrorSchema:
        workflow_error = result.error
        if workflow_error is None:
            return ErrorSchema(
                error_code=f"{role.value.upper()}_AGENT_FAILED",
                error_type="SectionAgentFailure",
                message=f"{role.value} Agent failed without a structured error.",
                recoverable=False,
                retryable=False,
                failure_category=FailureCategory.UNKNOWN,
                failed_node=node_name,
                source=ErrorSourceSchema(
                    component="MainlineSectionAgentAdapter",
                    agent_name=f"{role.value.title()}Agent",
                    step_name=node_name,
                ),
            )
        retryable = workflow_error.retryability != WorkflowRetryability.NOT_RETRYABLE
        return ErrorSchema(
            error_id=workflow_error.error_id,
            error_code=workflow_error.error_code,
            error_type="SectionAgentFailure",
            message=workflow_error.message,
            recoverable=retryable,
            retryable=retryable,
            failure_category=cls._failure_category(workflow_error.category),
            failed_node=node_name,
            source=ErrorSourceSchema(
                component="MainlineSectionAgentAdapter",
                agent_name=f"{role.value.title()}Agent",
                step_name=node_name,
            ),
            details={"workflow_error": workflow_error.model_dump(mode="json")},
        )

    @staticmethod
    def _agent_name(role: AgentRole) -> str:
        return f"{role.value.title()}Agent"

    def _record_started(
        self,
        *,
        context: AgentExecutionContextSchema,
        node_name: str,
        handle,
    ) -> None:
        if self.trace_sink is None:
            return
        agent_name = self._agent_name(context.agent_role)
        self.trace_sink.record(
            task_id=context.task_id,
            run_id=context.run_id,
            event_type="agent_started",
            component_type="agent",
            component_name=agent_name,
            step_id=f"{node_name}:{context.agent_role.value}_agent",
            step_name=node_name,
            agent_name=agent_name,
            caller=node_name,
            callee=agent_name,
            status=ExecutionStatus.RUNNING.value,
            phase="start",
            trace_id=handle.trace_id,
            span_id=handle.span_id,
            parent_span_id=handle.parent_span_id,
            span_name=handle.span_name,
            span_kind=handle.span_kind,
            started_at=handle.started_at,
            input_summary={
                "invocation_id": context.invocation_id,
                "section_id": context.section_id,
                "execution_mode": context.execution_mode,
                "input_snapshot_version": context.input_snapshot_version,
                "authorized_tool_count": len(context.authorized_tool_names),
                "fake_reasoner": self.reasoner_kind == "fake",
                "reasoner_kind": self.reasoner_kind,
                "integration_round": self.integration_round,
            },
            tags=[
                "trace_v2",
                "agent",
                context.agent_role.value,
                "section_graph",
                self.integration_round,
                f"{self.reasoner_kind}_reasoner",
            ],
        )

    def _record_finished(
        self,
        *,
        context: AgentExecutionContextSchema,
        node_name: str,
        result: AgentExecutionResultSchema,
        handle,
        error: ErrorSchema | None,
    ) -> None:
        if self.trace_sink is None:
            return
        agent_name = self._agent_name(context.agent_role)
        payload = result.payload
        action = None
        if isinstance(payload, ResearchResultPayloadSchema):
            action = payload.evidence_summary.recommended_action.value
        elif isinstance(payload, VerifierResultPayloadSchema):
            action = payload.action.value
        elif isinstance(payload, WriterResultPayloadSchema):
            action = (
                "complete" if payload.generation_complete else "partial"
            )
        self.trace_sink.record(
            task_id=context.task_id,
            run_id=context.run_id,
            event_type="agent_finished",
            component_type="agent",
            component_name=agent_name,
            step_id=f"{node_name}:{context.agent_role.value}_agent",
            step_name=node_name,
            agent_name=agent_name,
            caller=node_name,
            callee=agent_name,
            status=(
                ExecutionStatus.SUCCESS.value
                if error is None
                else ExecutionStatus.FAILED.value
            ),
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
                "section_id": context.section_id,
                "agent_status": result.status.value,
                "recommended_action": action,
                "proposed_state_patch_count": len(result.proposed_state_patches),
                "tool_call_count": len(result.tool_call_refs),
                "error_code": error.error_code if error is not None else None,
                "fake_reasoner": self.reasoner_kind == "fake",
                "reasoner_kind": self.reasoner_kind,
                "integration_round": self.integration_round,
            },
            tags=[
                "trace_v2",
                "agent",
                context.agent_role.value,
                "section_graph",
                self.integration_round,
                f"{self.reasoner_kind}_reasoner",
            ],
        )

    def _context(
        self,
        *,
        role: AgentRole,
        mode: str,
        node_name: str,
        task_id: str,
        run_id: str,
        section_id: str,
        section_state: SectionStateSchema,
        project_input: ProjectInputSchema,
        section_plan: SectionPlanSchema,
        phase_key: str,
        authorized_refs: list[str],
        budget: Mapping[str, Any],
        authorized_tool_names: list[str] | None = None,
    ) -> AgentExecutionContextSchema:
        snapshot = self._snapshot_version(project_input, section_plan)
        attempt_number = max(1, section_state.attempt.attempt_number)
        attempt_id = (
            section_state.attempt.attempt_id
            or f"{run_id}:{section_id}:attempt:{attempt_number}"
        )
        invocation_id = (
            f"{run_id}:{section_id}:{role.value}:{phase_key}:"
            f"a{attempt_number}"
        )
        source_ref = f"state://section-input/{snapshot}"
        section_ref = f"state://section-state/{section_id}"
        return AgentExecutionContextSchema(
            invocation_id=invocation_id,
            logical_operation_id=f"{run_id}:{section_id}:{node_name}",
            task_id=task_id,
            run_id=run_id,
            section_id=section_id,
            agent_role=role,
            execution_mode=mode,
            execution_id=f"{invocation_id}:execution",
            attempt_id=attempt_id,
            input_snapshot_version=snapshot,
            expected_domain_versions={},
            task_context_ref=f"state://task/{task_id}",
            source_snapshot_ref=source_ref,
            execution_plan_ref=f"state://section-plan/{section_plan.section_id}",
            section_state_ref=section_ref,
            authorized_artifact_refs=list(dict.fromkeys(authorized_refs)),
            authorized_tool_names=list(
                dict.fromkeys(authorized_tool_names or [])
            ),
            writable_state_paths=[],
            budget=dict(budget),
            trigger={"node": node_name, "phase_key": phase_key},
            trace_context={
                "trace_ref": f"trace://{invocation_id}",
                "integration_round": self.integration_round,
                "reasoner_kind": self.reasoner_kind,
            },
        )

    def _invoke(
        self,
        *,
        agent,
        context: AgentExecutionContextSchema,
        invocation: ContractSchema,
        node_name: str,
    ) -> AgentExecutionResultSchema:
        handle = new_span(
            run_id=context.run_id,
            span_name=f"agent:{self._agent_name(context.agent_role)}",
            span_kind="internal",
            parent=current_span(),
        )
        self._record_started(context=context, node_name=node_name, handle=handle)
        with activate_span(handle):
            result = agent.run_projected(context, invocation)
        error = None
        if result.status == AgentInvocationStatus.FAILED:
            error = self._normalize_error(
                result=result,
                role=context.agent_role,
                node_name=node_name,
            )
        elif result.proposed_state_patches:
            error = ErrorSchema(
                error_code="SECTION_AGENT_STATE_AUTHORITY_VIOLATION",
                error_type="SectionAgentStateAuthorityViolation",
                message=(
                    f"{self._agent_name(context.agent_role)} returned StatePatch "
                    "proposals in the production Section graph."
                ),
                recoverable=False,
                retryable=False,
                failure_category=FailureCategory.VALIDATION,
                failed_node=node_name,
                source=ErrorSourceSchema(
                    component="MainlineSectionAgentAdapter",
                    agent_name=self._agent_name(context.agent_role),
                    step_name=node_name,
                ),
            )
        self._record_finished(
            context=context,
            node_name=node_name,
            result=result,
            handle=handle,
            error=error,
        )
        if error is not None:
            raise MainlineSectionAgentFailure(error)
        return result

    def _result_metadata(
        self,
        context: AgentExecutionContextSchema,
        result: AgentExecutionResultSchema,
    ) -> dict[str, Any]:
        return {
            "agent_role": result.agent_role.value,
            "agent_status": result.status.value,
            "execution_mode": result.execution_mode,
            "invocation_id": result.invocation_id,
            "execution_id": result.execution_id,
            "attempt_id": result.attempt_id,
            "input_snapshot_version": result.input_snapshot_version,
            "trace_ref": result.trace_ref,
            "emitted_signals": list(result.emitted_signals),
            "proposed_state_patch_count": 0,
            "tool_call_refs": list(result.tool_call_refs),
            "fake_reasoner": self.reasoner_kind == "fake",
            "reasoner_kind": self.reasoner_kind,
            "integration_round": self.integration_round,
            "mainline_attached": True,
        }

    def research(
        self,
        *,
        node_name: str,
        task_id: str,
        run_id: str,
        project_input: ProjectInputSchema,
        section_plan: SectionPlanSchema,
        section_state: SectionStateSchema,
        section_id: str,
        section_title: str,
        retrieval_scope: str,
        research_mode: str,
        research_round: int,
        citation_required: bool,
        assessment: Mapping[str, Any],
        accepted_evidence_ids: list[str],
        rejected_evidence_ids: list[str],
        evidence_contract_sha256: str | None,
        tool_call_ids: list[str],
        retrieval_succeeded: bool,
        prior_evidence_package_ref: str | None,
        budget: Mapping[str, Any],
        shared_state: GraphStateSchema | None = None,
        query: str = "",
        call_suffix: str = "",
        citation_registry: list[CitationSchema] | None = None,
    ) -> MainlineResearchOutcome:
        use_tool = bool(
            self.research_tool_gateway is not None
            and shared_state is not None
            and str(query).strip()
        )
        phase_key = f"{research_mode}:r{research_round}"
        context = self._context(
            role=AgentRole.RESEARCH,
            mode=research_mode,
            node_name=node_name,
            task_id=task_id,
            run_id=run_id,
            section_id=section_id,
            section_state=section_state,
            project_input=project_input,
            section_plan=section_plan,
            phase_key=phase_key,
            authorized_refs=(
                [prior_evidence_package_ref]
                if prior_evidence_package_ref
                else []
            ),
            budget=budget,
            authorized_tool_names=(
                [MainlineResearchToolGateway.tool_name] if use_tool else []
            ),
        )
        source_ref = context.source_snapshot_ref
        section_ref = context.section_state_ref
        assert section_ref is not None
        invocation = ResearchInvocationInputSchema(
            section_id=section_id,
            section_task_ref=section_ref,
            fact_snapshot_ref=source_ref,
            source_inventory_ref=source_ref,
            evidence_topics=[section_title],
            prior_evidence_package_ref=prior_evidence_package_ref,
            research_mode=research_mode,
            retrieval_scope=retrieval_scope,
            research_round=research_round,
            query=str(query),
            section_title=section_title,
            call_suffix=call_suffix,
            citation_required=citation_required,
            evidence_assessment=dict(assessment),
            accepted_evidence_ids=list(accepted_evidence_ids),
            rejected_evidence_ids=list(rejected_evidence_ids),
            evidence_contract_sha256=evidence_contract_sha256,
            tool_call_ids=list(tool_call_ids),
            retrieval_succeeded=retrieval_succeeded,
        )
        retrieval: MainlineResearchRetrieval | None = None
        if use_tool:
            assert self.research_tool_gateway is not None
            assert shared_state is not None
            bound = BoundResearchToolRequest(
                shared_state=shared_state,
                project_input=project_input,
                section_id=section_id,
                section_title=section_title,
                query=str(query),
                scope=retrieval_scope,
                call_suffix=(call_suffix or f"section_{section_plan.section_order:03d}"),
                citation_registry=list(citation_registry or []),
            )
            with self.research_tool_gateway.bind(bound):
                result = self._invoke(
                    agent=self.research_agent,
                    context=context,
                    invocation=invocation,
                    node_name=node_name,
                )
            retrieval = bound.execution
            if retrieval is None:
                raise MainlineSectionAgentFailure(
                    ErrorSchema(
                        error_code="RESEARCH_TOOL_RESULT_MISSING",
                        error_type="ResearchToolResultMissing",
                        message=(
                            "ResearchAgent completed without a bound EvidenceService "
                            "tool result."
                        ),
                        recoverable=False,
                        retryable=False,
                        failure_category=FailureCategory.VALIDATION,
                        failed_node=node_name,
                    )
                )
        else:
            result = self._invoke(
                agent=self.research_agent,
                context=context,
                invocation=invocation,
                node_name=node_name,
            )
        payload = result.payload
        if not isinstance(payload, ResearchResultPayloadSchema):
            raise MainlineSectionAgentFailure(
                ErrorSchema(
                    error_code="RESEARCH_PAYLOAD_MISSING",
                    error_type="ResearchPayloadMissing",
                    message="ResearchAgent completed without ResearchResultPayloadSchema.",
                    recoverable=False,
                    retryable=False,
                    failure_category=FailureCategory.VALIDATION,
                    failed_node=node_name,
                )
            )
        payload = payload.model_copy(
            update={
                "agent_metadata": {
                    **dict(payload.agent_metadata),
                    **self._result_metadata(context, result),
                }
            },
            deep=True,
        )
        summary = payload.evidence_summary
        status = {
            ResearchStatus.SUFFICIENT: ResearchProjectionStatus.COMPLETED,
            ResearchStatus.INSUFFICIENT: ResearchProjectionStatus.INSUFFICIENT,
            ResearchStatus.EXHAUSTED: ResearchProjectionStatus.INSUFFICIENT,
            ResearchStatus.CONFLICTED: ResearchProjectionStatus.FAILED,
        }[summary.research_status]
        rejected_ids = [item.evidence_id for item in summary.rejected_evidence]
        research_state = section_state.research.model_copy(
            update={
                "status": status,
                "evidence_package_ref": payload.evidence_package_ref,
                "evidence_contract_sha256": payload.evidence_contract_sha256,
                "accepted_evidence_ids": list(summary.accepted_evidence_ids),
                "rejected_evidence_ids": rejected_ids,
                "evidence_gaps": list(summary.evidence_gaps),
                "research_rounds": max(
                    section_state.research.research_rounds,
                    payload.research_round,
                ),
                "tool_call_ids": list(payload.tool_call_ids),
                "recommended_action": summary.recommended_action.value,
                "metadata": {
                    **dict(section_state.research.metadata),
                    **dict(payload.agent_metadata),
                },
            },
            deep=True,
        )
        lifecycle = (
            SectionLifecycleStatus.EVIDENCE_READY
            if summary.recommended_action == ResearchRecommendedAction.WRITE
            else SectionLifecycleStatus.BLOCKED
            if summary.recommended_action
            in {
                ResearchRecommendedAction.HUMAN_REVIEW,
                ResearchRecommendedAction.PARTIAL_DELIVERY,
            }
            else SectionLifecycleStatus.RESEARCHING
        )
        updated_state = section_state.model_copy(
            update={
                "lifecycle_status": lifecycle,
                "attempt": section_state.attempt.model_copy(
                    update={
                        "input_snapshot_version": context.input_snapshot_version,
                    },
                    deep=True,
                ),
                "research": research_state,
            },
            deep=True,
        )
        return MainlineResearchOutcome(
            payload=payload,
            section_state=SectionStateSchema.model_validate(
                updated_state.model_dump(mode="python", round_trip=True)
            ),
            result=result,
            retrieval=retrieval,
        )

    def write(
        self,
        *,
        node_name: str,
        task_id: str,
        run_id: str,
        project_input: ProjectInputSchema,
        section_plan: SectionPlanSchema,
        section_state: SectionStateSchema,
        section: SchemeSectionSchema,
        evidence_package_ref: str,
        generation_attempt: int,
        citation_required: bool,
        generation_check: Mapping[str, Any],
        budget: Mapping[str, Any],
    ) -> MainlineWriterOutcome:
        section_id = section.section_id
        authorized_refs = [evidence_package_ref]
        if section_state.writing.draft_ref:
            authorized_refs.append(section_state.writing.draft_ref)
        context = self._context(
            role=AgentRole.WRITER,
            mode="section_generate",
            node_name=node_name,
            task_id=task_id,
            run_id=run_id,
            section_id=section_id,
            section_state=section_state,
            project_input=project_input,
            section_plan=section_plan,
            phase_key=f"generation:v{generation_attempt}",
            authorized_refs=authorized_refs,
            budget=budget,
        )
        strategy = (
            SectionStrategy.RAG_GENERATE
            if citation_required
            else SectionStrategy.TECHNICAL_GENERATE
        )
        invocation = WriterInvocationInputSchema(
            section_id=section_id,
            strategy=strategy,
            fact_snapshot_ref=context.source_snapshot_ref,
            evidence_package_ref=evidence_package_ref,
            base_draft_ref=section_state.writing.draft_ref,
            verifier_issue_refs=[],
            generation_cursor=section_state.writing.generation_cursor,
            section_snapshot=section,
            generation_attempt=generation_attempt,
            citation_required=citation_required,
            generation_check=dict(generation_check),
        )
        runtime_budget = WorkflowBudget.from_snapshot(dict(budget))
        with activate_workflow_budget(runtime_budget):
            result = self._invoke(
                agent=self.writer_agent,
                context=context,
                invocation=invocation,
                node_name=node_name,
            )
        payload = result.payload
        if not isinstance(payload, WriterResultPayloadSchema):
            raise MainlineSectionAgentFailure(
                ErrorSchema(
                    error_code="WRITER_PAYLOAD_MISSING",
                    error_type="WriterPayloadMissing",
                    message="WriterAgent completed without WriterResultPayloadSchema.",
                    recoverable=False,
                    retryable=False,
                    failure_category=FailureCategory.VALIDATION,
                    failed_node=node_name,
                )
            )
        payload = payload.model_copy(
            update={
                "writer_metadata": {
                    **dict(payload.writer_metadata),
                    **self._result_metadata(context, result),
                }
            },
            deep=True,
        )
        canonical = payload.section_snapshot
        if canonical is None:
            raise MainlineSectionAgentFailure(
                ErrorSchema(
                    error_code="WRITER_SECTION_SNAPSHOT_MISSING",
                    error_type="WriterSectionSnapshotMissing",
                    message="WriterAgent payload omitted the canonical Section snapshot.",
                    recoverable=False,
                    retryable=False,
                    failure_category=FailureCategory.VALIDATION,
                    failed_node=node_name,
                )
            )
        canonical = SchemeSectionSchema.model_validate(
            canonical.model_dump(mode="python", round_trip=True)
        )
        if canonical.model_dump(mode="json") != section.model_dump(mode="json"):
            raise MainlineSectionAgentFailure(
                ErrorSchema(
                    error_code="WRITER_CANONICAL_SECTION_DRIFT",
                    error_type="WriterCanonicalSectionDrift",
                    message=(
                        "Round-4 fake Writer payload changed the canonical Section; "
                        "only fixed projection is allowed."
                    ),
                    recoverable=False,
                    retryable=False,
                    failure_category=FailureCategory.VALIDATION,
                    failed_node=node_name,
                )
            )
        writing_status = (
            WritingProjectionStatus.COMPLETED
            if result.status == AgentInvocationStatus.COMPLETED
            else WritingProjectionStatus.PARTIAL
        )
        writing_state = section_state.writing.model_copy(
            update={
                "status": writing_status,
                "draft_ref": payload.draft_ref,
                "draft_version": payload.draft_version,
                "paragraph_refs": {
                    item.paragraph_id: item.content_ref
                    for item in payload.paragraphs
                },
                "paragraph_versions": {
                    item.paragraph_id: item.paragraph_version
                    for item in payload.paragraphs
                },
                "expected_paragraph_ids": list(payload.expected_paragraph_ids),
                "generation_complete": payload.generation_complete,
                "generation_cursor": payload.generation_cursor,
                "change_manifest_ref": payload.change_manifest_ref,
                "metadata": {
                    **dict(section_state.writing.metadata),
                    **dict(payload.writer_metadata),
                },
            },
            deep=True,
        )
        updated_state = section_state.model_copy(
            update={
                "lifecycle_status": SectionLifecycleStatus.GENERATED,
                "attempt": section_state.attempt.model_copy(
                    update={
                        "input_snapshot_version": context.input_snapshot_version,
                    },
                    deep=True,
                ),
                "writing": writing_state,
            },
            deep=True,
        )
        return MainlineWriterOutcome(
            payload=payload,
            section_state=SectionStateSchema.model_validate(
                updated_state.model_dump(mode="python", round_trip=True)
            ),
            result=result,
            budget_state=runtime_budget.snapshot(),
        )

    def verify(
        self,
        *,
        node_name: str,
        task_id: str,
        run_id: str,
        project_input: ProjectInputSchema,
        section_plan: SectionPlanSchema,
        section_state: SectionStateSchema,
        section: SchemeSectionSchema,
        generation_attempt: int,
        generation_check: Mapping[str, Any],
        citation_required: bool,
        evidence_blocked: bool,
        corrective_retrieval_enabled: bool,
        budget: Mapping[str, Any],
    ) -> MainlineVerifierOutcome:
        section_id = section.section_id
        target_ref = (
            section_state.writing.draft_ref
            or f"state://section-draft/{section_id}/v{generation_attempt}"
        )
        context = self._context(
            role=AgentRole.VERIFIER,
            mode=VerifierExecutionMode.SECTION_VERIFY.value,
            node_name=node_name,
            task_id=task_id,
            run_id=run_id,
            section_id=section_id,
            section_state=section_state,
            project_input=project_input,
            section_plan=section_plan,
            phase_key=f"section_verify:v{generation_attempt}",
            authorized_refs=[target_ref],
            budget=budget,
        )
        invocation = VerifierInvocationInputSchema(
            execution_mode=VerifierExecutionMode.SECTION_VERIFY,
            target_artifact_ref=target_ref,
            fact_snapshot_ref=context.source_snapshot_ref,
            section_id=section_id,
            claim_ids=list(section_state.claims.active_claim_ids),
            binding_refs=[],
            verified_section_summary_refs=[],
            section_snapshot=section,
            generation_attempt=generation_attempt,
            generation_check=dict(generation_check),
            citation_required=citation_required,
            evidence_blocked=evidence_blocked,
            corrective_retrieval_enabled=corrective_retrieval_enabled,
        )
        runtime_budget = WorkflowBudget.from_snapshot(dict(budget))
        with activate_workflow_budget(runtime_budget):
            result = self._invoke(
                agent=self.verifier_agent,
                context=context,
                invocation=invocation,
                node_name=node_name,
            )
        payload = result.payload
        if not isinstance(payload, VerifierResultPayloadSchema):
            raise MainlineSectionAgentFailure(
                ErrorSchema(
                    error_code="VERIFIER_PAYLOAD_MISSING",
                    error_type="VerifierPayloadMissing",
                    message="VerifierAgent completed without VerifierResultPayloadSchema.",
                    recoverable=False,
                    retryable=False,
                    failure_category=FailureCategory.VALIDATION,
                    failed_node=node_name,
                )
            )
        payload = payload.model_copy(
            update={
                "decision_metadata": {
                    **dict(payload.decision_metadata),
                    **self._result_metadata(context, result),
                }
            },
            deep=True,
        )
        verification_state = section_state.verification.model_copy(
            update={
                "status": VerificationProjectionStatus.COMPLETED,
                "execution_mode": payload.execution_mode.value,
                "recommended_action": payload.action.value,
                "verification_ref": payload.verification_ref,
                "issue_ids": list(payload.issue_ids),
                "accepted_paragraph_ids": list(payload.accepted_paragraph_ids),
                "rejected_paragraph_ids": list(payload.rejected_paragraph_ids),
                "verification_rounds": max(
                    1,
                    section_state.verification.verification_rounds + 1,
                ),
                "checks": dict(payload.checks),
                "metadata": {
                    **dict(section_state.verification.metadata),
                    **dict(payload.decision_metadata),
                },
            },
            deep=True,
        )
        updated_state = section_state.model_copy(
            update={
                "lifecycle_status": SectionLifecycleStatus.VERIFYING,
                "attempt": section_state.attempt.model_copy(
                    update={
                        "input_snapshot_version": context.input_snapshot_version,
                    },
                    deep=True,
                ),
                "verification": verification_state,
            },
            deep=True,
        )
        return MainlineVerifierOutcome(
            payload=payload,
            section_state=SectionStateSchema.model_validate(
                updated_state.model_dump(mode="python", round_trip=True)
            ),
            result=result,
            budget_state=runtime_budget.snapshot(),
        )


def build_round4_fake_section_agent_adapter(
    *,
    trace_sink: TraceSink | None = None,
) -> MainlineSectionAgentAdapter:
    """Compose Round-4 Agents without real RAG or ModelGateway adapters."""

    gateway = _NoToolGateway()
    return MainlineSectionAgentAdapter(
        research_agent=ResearchAgent(
            reasoner=_Round4FakeResearchReasoner(),
            tool_gateway=gateway,  # type: ignore[arg-type]
        ),
        writer_agent=WriterAgent(
            reasoner=_Round4FakeWriterReasoner(),
            tool_gateway=gateway,  # type: ignore[arg-type]
        ),
        verifier_agent=VerifierAgent(
            reasoner=_Round4FakeVerifierReasoner(),
            tool_gateway=gateway,  # type: ignore[arg-type]
        ),
        trace_sink=trace_sink,
        integration_round="round4",
        reasoner_kind="fake",
    )


def build_round5_section_agent_adapter(
    *,
    evidence_service: SchemeEvidenceService,
    model_gateway: ModelGatewayPort,
    trace_sink: TraceSink | None = None,
) -> MainlineSectionAgentAdapter:
    """Compose production ToolGateway and ModelGateway reasoners.

    The Research Agent receives the existing EvidenceService through an
    invocation-scoped gateway. Writer and Verifier receive the exact ModelGateway
    instance created by the Supervisor composition root. No client, vector store
    or provider is initialized in an Agent.
    """

    research_gateway = MainlineResearchToolGateway(
        evidence_service=evidence_service
    )
    no_tool_gateway = _NoToolGateway()
    return MainlineSectionAgentAdapter(
        research_agent=ResearchAgent(
            reasoner=MainlineResearchReasoner(),
            tool_gateway=research_gateway,  # type: ignore[arg-type]
        ),
        writer_agent=WriterAgent(
            reasoner=MainlineWriterModelReasoner(
                model_gateway=model_gateway
            ),
            tool_gateway=no_tool_gateway,  # type: ignore[arg-type]
        ),
        verifier_agent=VerifierAgent(
            reasoner=MainlineVerifierModelReasoner(
                model_gateway=model_gateway
            ),
            tool_gateway=no_tool_gateway,  # type: ignore[arg-type]
        ),
        trace_sink=trace_sink,
        research_tool_gateway=research_gateway,
        integration_round="round5",
        reasoner_kind="production_adapter",
    )
