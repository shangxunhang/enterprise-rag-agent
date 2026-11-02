"""Step 3.3 typed Trace correlation runtime acceptance tests."""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest

from agent.runtime.tool_executor import ToolExecutor
from apps.enterprise_document.schemas.agent_rag_v1 import (
    AgentExecutionContextSchema,
    AgentRole,
)
from apps.enterprise_document.services.scheme_writer.agent_integration.section_agent_adapter import (
    MainlineSectionAgentAdapter,
)
from contracts.observability import TraceSink
from data_capture.run_trace_recorder import JsonlRunTraceRecorder
from model_gateway.fake_llm_client import FakeLLMClient
from model_gateway.model_gateway import ModelGateway
from observability.pre_write_trace import PreWriteTraceEmitter
from observability.sqlite_trace_sink import SQLiteTraceSink
from observability.trace_context import activate_span, new_span
from schemas.common import ErrorSchema
from schemas.failure import FailureCategory, FailureOrigin
from schemas.model import ModelRequestSchema
from schemas.tool import ToolCallSchema
from schemas.trace import RunTraceEventSchema, TraceCorrelationSchema, TraceRefSchema
from tools.fake_rag_tool import FakeRAGTool
from tools.tool_registry import ToolRegistry

from backend.tests.test_gate_v2_pre_write_trace import (
    _binding_ref,
    _decision_ref,
    _route_ref,
    _subject_ref,
)
from apps.enterprise_document.schemas.pre_write_control import PreWriteRoute


NOW = "2026-09-03T00:00:00+00:00"


class _RecordingSink:
    def __init__(self) -> None:
        self.calls: list[dict] = []

    def record(self, **kwargs):
        self.calls.append(kwargs)
        return kwargs


def _agent_context() -> AgentExecutionContextSchema:
    return AgentExecutionContextSchema(
        invocation_id="invocation-1",
        logical_operation_id="logical-op-1",
        task_id="task-1",
        run_id="run-1",
        section_id="section-1",
        agent_role=AgentRole.RESEARCH,
        execution_mode="default",
        execution_id="execution-1",
        attempt_id="attempt-1",
        input_snapshot_version="snapshot-1",
        task_context_ref="artifact://task-context/1",
        source_snapshot_ref="artifact://source-snapshot/1",
        section_state_ref="artifact://section-state/section-1",
        authorized_artifact_refs=["artifact://facts/1"],
        authorized_tool_names=["knowledge.retrieve"],
    )


def test_typed_correlation_round_trip_preserves_distinct_identity_domains() -> None:
    correlation = TraceCorrelationSchema(
        attempt_scope_ref="section://section-1",
        attempt_id="attempt-1",
        section_id="section-1",
        agent_invocation_id="invocation-1",
        execution_id="execution-1",
        logical_operation_id="logical-op-1",
        model_call_id="model-call-1",
        provider_attempt_id="provider-attempt-1",
        provider_attempt_ordinal=1,
        tool_operation_id="tool-op-1",
        tool_attempt_id="tool-attempt-1",
        tool_attempt_ordinal=1,
        prewrite_route_decision_ref={
            "route_decision_id": "route-1",
            "route_decision_schema_version": "pre_write_route_decision_v1",
            "route_decision_contract_version": "pre_write_routing_v1",
        },
        retrieval_trace_id="rag-trace-1",
        artifact_refs=["artifact://section/1"],
        evidence_refs=["evidence://bundle/1"],
    )
    event = RunTraceEventSchema(
        event_id="event-1",
        event_sequence=1,
        trace_id="trace-run-1",
        run_id="run-1",
        task_id="task-1",
        span_id="span-1",
        event_type="agent_started",
        component_type="agent",
        component_name="ResearchAgent",
        correlation=correlation,
        failure_origin=FailureOrigin.UNKNOWN,
        created_at=NOW,
    )

    reopened = RunTraceEventSchema.model_validate_json(event.model_dump_json())
    assert reopened.correlation == correlation
    assert reopened.trace_id == "trace-run-1"
    assert reopened.span_id == "span-1"
    assert reopened.correlation.logical_operation_id == "logical-op-1"
    assert reopened.correlation.execution_id == "execution-1"
    assert reopened.correlation.attempt_id == "attempt-1"
    assert reopened.correlation.agent_invocation_id == "invocation-1"
    assert len(
        {
            reopened.trace_id,
            reopened.span_id,
            reopened.correlation.logical_operation_id,
            reopened.correlation.execution_id,
            reopened.correlation.attempt_id,
            reopened.correlation.agent_invocation_id,
        }
    ) == 6

    ref = TraceRefSchema(
        trace_id=reopened.trace_id,
        run_id=reopened.run_id,
        span_id=reopened.span_id,
        anchor_event_id=reopened.event_id,
    )
    assert ref.model_dump(mode="json")["run_id"] == "run-1"


def test_invalid_physical_attempt_and_retry_correlation_fail_closed() -> None:
    with pytest.raises(ValueError, match="provider_attempt_id"):
        TraceCorrelationSchema(
            model_call_id="model-1",
            provider_attempt_id="provider-1",
        )
    with pytest.raises(ValueError, match="model_call_id"):
        TraceCorrelationSchema(
            provider_attempt_id="provider-1",
            provider_attempt_ordinal=1,
        )
    with pytest.raises(ValueError, match="tool_operation_id"):
        TraceCorrelationSchema(
            tool_attempt_id="tool-attempt-1",
            tool_attempt_ordinal=1,
        )
    with pytest.raises(ValueError, match="attempt_scope_ref"):
        TraceCorrelationSchema(attempt_scope_ref="section://1")
    with pytest.raises(ValueError, match="retry correlation"):
        TraceCorrelationSchema(
            retry_successor_operation_ref="retry-op-1",
            predecessor_attempt_ref="attempt-1",
        )


def test_cross_run_parent_span_is_rejected_but_explicit_refs_remain_possible() -> None:
    run_a = new_span(run_id="run-a", span_name="run-a")
    with activate_span(run_a):
        with pytest.raises(ValueError, match="same run_id"):
            new_span(run_id="run-b", span_name="illegal-child")

    run_b = new_span(run_id="run-b", span_name="run-b")
    assert run_a.trace_id != run_b.trace_id
    correlation = TraceCorrelationSchema(
        external_trace_refs=[
            f"trace-ref:{run_a.run_id}:{run_a.trace_id}:{run_a.span_id}"
        ]
    )
    assert correlation.external_trace_refs


def test_jsonl_and_sqlite_preserve_typed_correlation_and_structured_failure(
    tmp_path: Path,
) -> None:
    correlation = TraceCorrelationSchema(
        attempt_id="attempt-1",
        agent_invocation_id="invocation-1",
        logical_operation_id="logical-op-1",
    )
    error = ErrorSchema(
        error_code="TRACE_TEST_FAILURE",
        error_type="RuntimeError",
        message="typed failure",
        failure_category=FailureCategory.TRANSIENT,
        retryable=True,
    )

    jsonl = JsonlRunTraceRecorder(tmp_path / "jsonl")
    jsonl.record(
        task_id="task-1",
        run_id="run-1",
        event_type="agent_finished",
        component_type="agent",
        component_name="ResearchAgent",
        correlation=correlation,
        error=error,
        failure_origin=FailureOrigin.EXECUTION_LIFECYCLE,
        phase="error",
    )
    row = json.loads(
        (tmp_path / "jsonl" / "run-1_trace.jsonl").read_text(encoding="utf-8")
    )
    assert row["correlation"]["logical_operation_id"] == "logical-op-1"
    assert row["failure_origin"] == "execution_lifecycle"
    assert row["error"]["error_code"] == "TRACE_TEST_FAILURE"

    database = tmp_path / "trace.sqlite3"
    SQLiteTraceSink(database).record(
        task_id="task-1",
        run_id="run-1",
        event_type="agent_finished",
        component_type="agent",
        component_name="ResearchAgent",
        correlation=correlation,
        error=error,
        failure_origin=FailureOrigin.EXECUTION_LIFECYCLE,
        phase="error",
    )
    # Reopen through a new sink instance before reading the persisted envelope.
    SQLiteTraceSink(database)
    with sqlite3.connect(str(database)) as connection:
        serialized = str(
            connection.execute(
                "SELECT event_json FROM trace_events ORDER BY event_sequence LIMIT 1"
            ).fetchone()[0]
        )
    reopened = RunTraceEventSchema.model_validate_json(serialized)
    assert reopened.correlation.agent_invocation_id == "invocation-1"
    assert reopened.failure_origin is FailureOrigin.EXECUTION_LIFECYCLE
    assert reopened.error is not None
    assert reopened.error.error_code == "TRACE_TEST_FAILURE"


def test_model_and_tool_events_emit_logical_and_physical_typed_correlation(
    tmp_path: Path,
) -> None:
    recorder = JsonlRunTraceRecorder(tmp_path)
    gateway = ModelGateway(default_model_name="fake_llm", run_trace_recorder=recorder)
    gateway.register_client(FakeLLMClient())
    response = gateway.generate(
        ModelRequestSchema(
            model_call_id="model-call-1",
            task_id="task-model",
            run_id="run-model",
            model_name="fake_llm",
            caller_agent="ResearchAgent",
            prompt="hello",
            created_at=NOW,
            extra={
                "attempt_id": "attempt-model",
                "agent_invocation_id": "invocation-model",
                "execution_id": "execution-model",
                "logical_operation_id": "logical-model-op",
            },
        )
    )
    assert response.success
    model_rows = [
        json.loads(line)
        for line in (tmp_path / "run-model_trace.jsonl")
        .read_text(encoding="utf-8")
        .splitlines()
        if line.strip()
    ]
    model_start = next(row for row in model_rows if row["event_type"] == "model_started")
    model_corr = model_start["correlation"]
    assert model_corr["model_call_id"] == "model-call-1"
    assert model_corr["provider_attempt_id"].startswith(
        "provider_attempt_model-call-1_"
    )
    assert model_corr["provider_attempt_ordinal"] == 1
    assert model_corr["logical_operation_id"] == "logical-model-op"

    registry = ToolRegistry()
    registry.register(FakeRAGTool())
    executor = ToolExecutor(registry, recorder)
    result = executor.execute(
        ToolCallSchema(
            tool_call_id="tool-call-1",
            task_id="task-tool",
            run_id="run-tool",
            tool_name="FakeRAGTool",
            tool_input={"query": "typed trace"},
            caller_agent="ResearchAgent",
            created_at=NOW,
            metadata={
                "attempt_id": "attempt-tool",
                "agent_invocation_id": "invocation-tool",
                "execution_id": "execution-tool",
                "logical_operation_id": "logical-tool-op",
            },
        )
    )
    assert result.success
    tool_rows = [
        json.loads(line)
        for line in (tmp_path / "run-tool_trace.jsonl")
        .read_text(encoding="utf-8")
        .splitlines()
        if line.strip()
    ]
    tool_start = next(row for row in tool_rows if row["event_type"] == "tool_started")
    tool_corr = tool_start["correlation"]
    assert tool_corr["tool_operation_id"] == "logical-tool-op"
    assert tool_corr["tool_attempt_id"].startswith("tool_attempt_")
    assert tool_corr["tool_attempt_ordinal"] == 1
    assert tool_corr["attempt_id"] == "attempt-tool"


def test_mainline_agent_event_uses_invocation_execution_attempt_and_logical_operation() -> None:
    sink = _RecordingSink()
    adapter = object.__new__(MainlineSectionAgentAdapter)
    adapter.trace_sink = sink
    adapter.reasoner_kind = "fake"
    adapter.integration_round = "trace-test"

    context = _agent_context()
    handle = new_span(run_id=context.run_id, span_name="agent:research")
    adapter._record_started(
        context=context,
        node_name="research",
        handle=handle,
    )
    correlation = sink.calls[0]["correlation"]
    assert isinstance(correlation, TraceCorrelationSchema)
    assert correlation.attempt_id == "attempt-1"
    assert correlation.agent_invocation_id == "invocation-1"
    assert correlation.execution_id == "execution-1"
    assert correlation.logical_operation_id == "logical-op-1"
    assert correlation.artifact_refs == ["artifact://facts/1"]


def test_gate_route_ref_is_typed_correlation_not_trace_authority() -> None:
    sink = _RecordingSink()
    emitter = PreWriteTraceEmitter(sink)
    route_ref = _route_ref("route-typed")
    emitter.emit_route_decision(
        task_id="task-1",
        run_id="run-1",
        writer_subject_ref=_subject_ref(),
        write_instance_id="write-1",
        route_decision_ref=route_ref,
        decision_ref=_decision_ref(),
        binding_ref=_binding_ref(),
        selected_route=PreWriteRoute.NORMAL,
    )
    call = sink.calls[0]
    correlation = call["correlation"]
    assert correlation.prewrite_route_decision_ref == route_ref.model_dump(mode="json")
    assert correlation.decision_refs == ["route-typed"]
    # The authoritative projection remains producer-owned payload; correlation
    # only points to the same already-produced ref.
    assert call["payload"]["route_decision_ref"] == route_ref.model_dump(mode="json")


def test_missing_optional_correlation_is_deterministic() -> None:
    event = RunTraceEventSchema(
        event_id="event-empty",
        event_sequence=1,
        trace_id="trace-run-1",
        run_id="run-1",
        task_id="task-1",
        event_type="heartbeat",
        component_type="runtime",
        component_name="runtime",
        created_at=NOW,
    )
    assert event.correlation == TraceCorrelationSchema()
    assert event.failure_origin is FailureOrigin.UNKNOWN
