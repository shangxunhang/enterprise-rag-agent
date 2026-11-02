"""W15-S02: preserve structured failures across the real LangGraph boundary."""
from __future__ import annotations

import asyncio
import traceback
from types import SimpleNamespace

from core.retry_policy import RetryDecisionPolicy
from apps.enterprise_document.schemas.pre_write_writer import (
    WriterExecutionResultSchema, WriterExecutionStatus, WriterExecutionFailureKind,
)
from apps.enterprise_document.services.scheme_writer.section_execution_coordinator import PreWriteWriterExecutionFailed
from test_pre_write_target_schema_foundation import execution_key

import pytest
from langgraph.graph import END, START, StateGraph

from agent.runtime.business_node_execution_runner import WorkflowNodeExecutionRunner
from apps.enterprise_document.workflows.top_level_business_graph import (
    EnterpriseDocumentWorkflowEngine,
)

from apps.enterprise_document.services.scheme_writer.agent_integration.planner_adapter import (
    MainlinePlannerFailure,
)
from apps.enterprise_document.services.scheme_writer.agent_integration.section_agent_adapter import (
    MainlineResearchAgentFailure,
    MainlineSectionAgentFailure,
)
from apps.enterprise_document.services.scheme_writer.stage_service import (
    SchemeGenerationGraphPayloadSchema,
    SchemeGenerationStageFailure,
    SchemeGenerationStageService,
)
from apps.enterprise_document.services.scheme_writer.runtime_support import (
    SchemeWriterRuntimeSupport,
)
from schemas.common import ErrorSchema
from schemas.failure import FailureCategory


FAILURE_TYPES = (
    MainlineSectionAgentFailure,
    MainlineResearchAgentFailure,
    MainlinePlannerFailure,
    SchemeGenerationStageFailure,
)


@pytest.mark.parametrize("failure_type", FAILURE_TYPES, ids=lambda cls: cls.__name__)
@pytest.mark.parametrize("invoke_mode", ("sync", "async"))
def test_langgraph_preserves_original_failure_and_structured_error(
    failure_type, invoke_mode: str,
) -> None:
    """Do not let traceback restoration replace a domain carrier with setattr failure."""
    error = ErrorSchema(
        error_code="RESEARCH_PROVIDER_NOT_CONFIGURED",
        error_type="RuntimeError",
        message="canonical ToolGateway provider is not configured",
        recoverable=False,
        retryable=False,
        failure_category=FailureCategory.CONFIGURATION,
        failed_node="research",
        source={"component": "ResearchAgent"},
        details={"provider": "canonical_tool_gateway", "section_id": "section-1"},
    )
    failure = failure_type(error)
    cause = RuntimeError(error.message)

    def fail(_state: dict) -> dict:
        raise failure from cause

    builder = StateGraph(dict)
    builder.add_node("research", fail)
    builder.add_edge(START, "research")
    builder.add_edge("research", END)

    graph = builder.compile()
    with pytest.raises(failure_type) as caught:
        if invoke_mode == "sync":
            graph.invoke({})
        else:
            asyncio.run(graph.ainvoke({}))

    observed = caught.value
    assert observed is failure
    assert observed.error is error
    assert observed.error.error_code == "RESEARCH_PROVIDER_NOT_CONFIGURED"
    assert observed.error.failure_category is FailureCategory.CONFIGURATION
    assert observed.error.retryable is False
    assert observed.error.failed_node == "research"
    assert observed.error.details == {
        "provider": "canonical_tool_gateway",
        "section_id": "section-1",
    }
    assert observed.__cause__ is cause
    rendered = "".join(traceback.format_exception(type(observed), observed, observed.__traceback__))
    assert "canonical ToolGateway provider is not configured" in rendered
    assert "cannot assign to field" not in rendered
    assert any(frame.name == "fail" for frame in traceback.extract_tb(observed.__traceback__))


@pytest.mark.parametrize("failure_type", FAILURE_TYPES, ids=lambda cls: cls.__name__)
def test_stage_failure_normalization_retains_typed_original_details(failure_type) -> None:
    error = ErrorSchema(
        error_code="RESEARCH_PROVIDER_NOT_CONFIGURED",
        error_type="RuntimeError",
        message="canonical ToolGateway provider is not configured",
        recoverable=False,
        retryable=False,
        failure_category=FailureCategory.CONFIGURATION,
        failed_node="research",
        source={"component": "ResearchAgent"},
        details={"provider": "canonical_tool_gateway", "original_code": "TOOL_CONFIG"},
    )
    service = object.__new__(SchemeGenerationStageService)
    service.runtime_support = SchemeWriterRuntimeSupport()
    payload = SchemeGenerationGraphPayloadSchema(
        task_id="task-exception-propagation",
        run_id="run-exception-propagation",
        started_at="2026-09-07T00:00:00+00:00",
    )
    result = service.mark_technical_failure(
        payload, node_name="execute_sections", exc=failure_type(error),
    )
    assert result.terminal_error is not None
    assert result.terminal_error.model_dump(mode="json") == error.model_dump(mode="json")
    assert result.failed_business_node == "execute_sections"
    assert result.artifact_route == "technical_failure"


@pytest.mark.parametrize("failure_type", FAILURE_TYPES, ids=lambda cls: cls.__name__)
def test_worker_outcome_keeps_original_structured_agent_failure(failure_type) -> None:
    error = ErrorSchema(
        error_code="RESEARCH_PROVIDER_NOT_CONFIGURED",
        error_type="RuntimeError",
        message="canonical ToolGateway provider is not configured",
        recoverable=False,
        retryable=False,
        failure_category=FailureCategory.CONFIGURATION,
        failed_node="research",
        source={"component": "ResearchAgent"},
        details={"provider": "canonical_tool_gateway", "original_code": "TOOL_CONFIG"},
    )
    failure = failure_type(error)

    def fail(_control):
        raise failure

    runner = WorkflowNodeExecutionRunner(
        now_iso=lambda: "2026-09-07T00:00:00+00:00",
    )
    outcome = runner.execute(
        fail,
        execution_id="w15-failure-transport",
        commit_scope_id="w15-failure-transport-scope",
        timeout_seconds=5,
        thread_name="w15-failure-transport",
        attempt_succeeded=lambda _value: False,
    )
    assert outcome.kind == "error"
    assert outcome.exception is failure
    assert outcome.worker_still_active is False
    assert outcome.stack_trace is not None
    assert "canonical ToolGateway provider is not configured" in outcome.stack_trace
    engine = object.__new__(EnterpriseDocumentWorkflowEngine)
    observed = engine._error_from_outcome(node_name="execute_sections", outcome=outcome)
    assert observed is error
    assert observed.model_dump(mode="json") == error.model_dump(mode="json")


@pytest.mark.parametrize("boundary", ("worker", "stage"))
def test_durable_writer_error_survives_workflow_normalization(boundary):
    error = ErrorSchema(
        error_code="WRITER_ORIGINAL_FAILURE", error_type="RuntimeError",
        message="original writer dependency failure", retryable=False,
        failure_category=FailureCategory.CONFIGURATION,
        failed_node="writer", details={"specific_reason": "preserve-me"},
    )
    result = WriterExecutionResultSchema(
        logical_execution_key=execution_key(),
        status=WriterExecutionStatus.TECHNICAL_FAILURE,
        failure_kind=WriterExecutionFailureKind.MODEL_INVOCATION_FAILURE,
        error=error, generation_provenance={"origin": "test-regression"},
    )
    failure = PreWriteWriterExecutionFailed(result)
    service = object.__new__(SchemeGenerationStageService)
    service.runtime_support = SchemeWriterRuntimeSupport()
    if boundary == "worker":
        engine = object.__new__(EnterpriseDocumentWorkflowEngine)
        engine.stage_service = service
        engine.retry_policy = RetryDecisionPolicy()
        observed = engine._error_from_outcome(
            node_name="execute_sections",
            outcome=SimpleNamespace(kind="error", exception=failure),
        )
    else:
        payload = SchemeGenerationGraphPayloadSchema(
            task_id="task-writer-error", run_id="run-writer-error",
            started_at="2026-09-07T00:00:00+00:00",
        )
        observed = service.mark_technical_failure(
            payload, node_name="execute_sections", exc=failure,
        ).terminal_error
    assert observed.model_dump(mode="json") == error.model_dump(mode="json")
