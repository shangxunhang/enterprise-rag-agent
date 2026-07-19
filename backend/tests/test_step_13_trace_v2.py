# =============================================================================
# 中文阅读说明：自动化测试模块，用于验证主链、边界条件和回归行为。
# 主要定义：_rows、test_trace_v2_sequence_and_nested_span_context、test_tool_trace_records_evidence_lineage、test_model_trace_hashes_prompt_instead_of_copying_it、test_trace_validator_accepts_complete_fake_mainline_shape、test_tool_failure_is_recorded_as_error_span。建议先从公开入口函数开始，再沿调用关系向下阅读。
# =============================================================================
from __future__ import annotations

import json
from pathlib import Path

from agent.runtime.tool_executor import ToolExecutor
from data_capture.run_trace_recorder import JsonlRunTraceRecorder
from model_gateway.fake_llm_client import FakeLLMClient
from model_gateway.model_gateway import ModelGateway
from observability.trace_context import activate_span, new_span
from observability.trace_reader import load_trace_events, validate_trace_v2
from schemas.model import ModelRequestSchema
from schemas.tool import ToolCallSchema
from tools.fake_rag_tool import FakeRAGTool
from tools.tool_registry import ToolRegistry

NOW = "2026-07-17T00:00:00+00:00"


# 阅读注释（函数）：处理 rows 相关逻辑。
def _rows(path: Path) -> list[dict]:
    """处理 rows 相关逻辑。

    参数:
        path: 目标文件或目录路径。

    返回:
        list[dict]

    阅读提示:
        主要直接调用：json.loads, splitlines, path.read_text, line.strip。
    """
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


# 阅读注释（函数）：处理 测试 Trace v2 sequence and nested span 上下文 相关逻辑。
def test_trace_v2_sequence_and_nested_span_context(tmp_path: Path) -> None:
    """处理 测试 Trace v2 sequence and nested span 上下文 相关逻辑。

    参数:
        tmp_path: tmp 路径，具体约束请结合类型标注和调用方确认。

    返回:
        None

    阅读提示:
        主要直接调用：JsonlRunTraceRecorder, new_span, activate_span, recorder.record, _rows, all。
    """
    recorder = JsonlRunTraceRecorder(tmp_path)
    root = new_span(run_id="run_1", span_name="root", span_kind="server")
    with activate_span(root):
        recorder.record(
            task_id="task_1",
            run_id="run_1",
            event_type="run_started",
            component_type="runtime",
            component_name="root",
            phase="start",
            trace_id=root.trace_id,
            span_id=root.span_id,
            parent_span_id=root.parent_span_id,
            span_name=root.span_name,
            span_kind=root.span_kind,
        )
        child = new_span(run_id="run_1", span_name="child")
        recorder.record(
            task_id="task_1",
            run_id="run_1",
            event_type="child_started",
            component_type="unit",
            component_name="child",
            phase="start",
            trace_id=child.trace_id,
            span_id=child.span_id,
            parent_span_id=child.parent_span_id,
        )
        recorder.record(
            task_id="task_1",
            run_id="run_1",
            event_type="child_finished",
            component_type="unit",
            component_name="child",
            phase="end",
            trace_id=child.trace_id,
            span_id=child.span_id,
            parent_span_id=child.parent_span_id,
            latency_ms=0,
        )
        recorder.record(
            task_id="task_1",
            run_id="run_1",
            event_type="run_finished",
            component_type="runtime",
            component_name="root",
            phase="end",
            trace_id=root.trace_id,
            span_id=root.span_id,
            parent_span_id=root.parent_span_id,
            latency_ms=0,
        )

    rows = _rows(tmp_path / "run_1_trace.jsonl")
    assert [row["event_sequence"] for row in rows] == [1, 2, 3, 4]
    assert all(row["schema_version"] == "run_trace_event_v2" for row in rows)
    assert rows[1]["parent_span_id"] == root.span_id


# 阅读注释（函数）：处理 测试 工具 Trace 记录集合 证据 lineage 相关逻辑。
def test_tool_trace_records_evidence_lineage(tmp_path: Path) -> None:
    """处理 测试 工具 Trace 记录集合 证据 lineage 相关逻辑。

    参数:
        tmp_path: tmp 路径，具体约束请结合类型标注和调用方确认。

    返回:
        None

    阅读提示:
        主要直接调用：JsonlRunTraceRecorder, ToolRegistry, registry.register, FakeRAGTool, ToolExecutor, new_span, activate_span, executor.execute。
    """
    recorder = JsonlRunTraceRecorder(tmp_path)
    registry = ToolRegistry()
    registry.register(FakeRAGTool())
    executor = ToolExecutor(registry, recorder)
    root = new_span(run_id="run_tool", span_name="agent")
    with activate_span(root):
        result = executor.execute(
            ToolCallSchema(
                tool_call_id="tool_call_1",
                task_id="task_tool",
                run_id="run_tool",
                tool_name="FakeRAGTool",
                tool_input={"query": "安全设计", "retrieval_mode": "hybrid"},
                caller_agent="SchemeWriterAgent",
                created_at=NOW,
            )
        )
    assert result.success is True
    rows = _rows(tmp_path / "run_tool_trace.jsonl")
    finish = next(row for row in rows if row["event_type"] == "tool_finished")
    assert finish["parent_span_id"] == root.span_id
    assert finish["output_summary"]["rag_evidence"]["schema_version"] == "rag_evidence_contract_v1"
    assert finish["lineage"]["evidence_contract_sha256"]
    assert finish["output_summary"]["rag_evidence"]["selected_evidence_count"] == 1


# 阅读注释（函数）：处理 测试 模型 Trace hashes 提示词 instead of copying it 相关逻辑。
def test_model_trace_hashes_prompt_instead_of_copying_it(tmp_path: Path) -> None:
    """处理 测试 模型 Trace hashes 提示词 instead of copying it 相关逻辑。

    参数:
        tmp_path: tmp 路径，具体约束请结合类型标注和调用方确认。

    返回:
        None

    阅读提示:
        主要直接调用：JsonlRunTraceRecorder, ModelGateway, gateway.register_client, FakeLLMClient, new_span, activate_span, gateway.generate, ModelRequestSchema。
    """
    recorder = JsonlRunTraceRecorder(tmp_path)
    gateway = ModelGateway(default_model_name="fake_llm", run_trace_recorder=recorder)
    gateway.register_client(FakeLLMClient())
    root = new_span(run_id="run_model", span_name="agent")
    prompt = "这是一个不应完整复制到Trace中的长Prompt。" * 30
    with activate_span(root):
        response = gateway.generate(
            ModelRequestSchema(
                model_call_id="model_call_1",
                task_id="task_model",
                run_id="run_model",
                model_name="fake_llm",
                caller_agent="SchemeWriterAgent",
                prompt=prompt,
                created_at=NOW,
                extra={"call_purpose": "scheme_section_generation"},
            )
        )
    assert response.success is True
    rows = _rows(tmp_path / "run_model_trace.jsonl")
    start = next(row for row in rows if row["event_type"] == "model_started")
    assert start["input_payload"] == {}
    assert start["input_summary"]["prompt_chars"] == len(prompt)
    assert len(start["input_summary"]["prompt_sha256"]) == 64
    assert prompt not in json.dumps(start, ensure_ascii=False)


# 阅读注释（函数）：处理 测试 Trace validator accepts complete fake 主链 shape 相关逻辑。
def test_trace_validator_accepts_complete_fake_mainline_shape(tmp_path: Path) -> None:
    """处理 测试 Trace validator accepts complete fake 主链 shape 相关逻辑。

    参数:
        tmp_path: tmp 路径，具体约束请结合类型标注和调用方确认。

    返回:
        None

    阅读提示:
        主要直接调用：JsonlRunTraceRecorder, new_span, replace, recorder.record, validate_trace_v2, load_trace_events。
    """
    recorder = JsonlRunTraceRecorder(tmp_path)
    root = new_span(run_id="run_validate", span_name="run", span_kind="server")
    workflow = new_span(run_id="run_validate", span_name="workflow", parent=None)
    # Build a compact but complete hierarchy manually.
    events = [
        (root, "run_started", "runtime", "run", "start", None),
        (workflow, "workflow_started", "workflow", "wf", "start", root.span_id),
    ]
    # Recreate the workflow handle with explicit root parent for validation.
    from dataclasses import replace
    workflow = replace(workflow, trace_id=root.trace_id, parent_span_id=root.span_id)
    agent = new_span(run_id="run_validate", span_name="agent")
    agent = replace(agent, trace_id=root.trace_id, parent_span_id=workflow.span_id)
    tool = new_span(run_id="run_validate", span_name="tool")
    tool = replace(tool, trace_id=root.trace_id, parent_span_id=agent.span_id)
    model = new_span(run_id="run_validate", span_name="model")
    model = replace(model, trace_id=root.trace_id, parent_span_id=agent.span_id)
    ordered = [
        (root, "run_started", "runtime", "run", "start"),
        (workflow, "workflow_started", "workflow", "wf", "start"),
        (agent, "agent_started", "agent", "agent", "start"),
        (tool, "tool_started", "tool", "tool", "start"),
        (tool, "tool_finished", "tool", "tool", "end"),
        (model, "model_started", "model", "model", "start"),
        (model, "model_finished", "model", "model", "end"),
        (agent, "agent_finished", "agent", "agent", "end"),
        (workflow, "workflow_finished", "workflow", "wf", "end"),
        (root, "run_finished", "runtime", "run", "end"),
    ]
    for handle, event_type, component_type, component_name, phase in ordered:
        kwargs = {}
        if event_type == "model_started":
            kwargs["input_summary"] = {"prompt_sha256": "x" * 64, "prompt_chars": 1}
        recorder.record(
            task_id="task_validate",
            run_id="run_validate",
            event_type=event_type,
            component_type=component_type,
            component_name=component_name,
            phase=phase,
            trace_id=handle.trace_id,
            span_id=handle.span_id,
            parent_span_id=handle.parent_span_id,
            latency_ms=(0 if phase in {"end", "error"} else None),
            **kwargs,
        )
    report = validate_trace_v2(load_trace_events(tmp_path / "run_validate_trace.jsonl"))
    assert report["status"] == "success"
    assert report["failed_checks"] == []


# 阅读注释（函数）：处理 测试 工具 failure is recorded as 错误 span 相关逻辑。
def test_tool_failure_is_recorded_as_error_span(tmp_path: Path) -> None:
    """处理 测试 工具 failure is recorded as 错误 span 相关逻辑。

    参数:
        tmp_path: tmp 路径，具体约束请结合类型标注和调用方确认。

    返回:
        None

    阅读提示:
        主要直接调用：JsonlRunTraceRecorder, ToolRegistry, registry.register, BrokenTool, ToolExecutor, new_span, activate_span, executor.execute。
    """
    from contracts.base_tool import BaseTool

    # 阅读注释（类）：封装 broken 工具，集中封装相关状态、依赖和行为。
    class BrokenTool(BaseTool):
        """封装 broken 工具，集中封装相关状态、依赖和行为。"""
        tool_name = "BrokenTool"

        # 阅读注释（函数）：执行 BrokenTool 的主流程。
        def run(self, tool_call):
            """执行 BrokenTool 的主流程。

            参数:
                tool_call: 工具 call，具体约束请结合类型标注和调用方确认。

            返回:
                未显式标注；请结合调用方和实际返回语句理解。

            阅读提示:
                主要直接调用：RuntimeError。
            """
            raise RuntimeError("boom")

    recorder = JsonlRunTraceRecorder(tmp_path)
    registry = ToolRegistry()
    registry.register(BrokenTool())
    executor = ToolExecutor(registry, recorder)
    root = new_span(run_id="run_failure", span_name="agent")
    with activate_span(root):
        result = executor.execute(
            ToolCallSchema(
                tool_call_id="tool_call_failure",
                task_id="task_failure",
                run_id="run_failure",
                tool_name="BrokenTool",
                tool_input={},
                caller_agent="SchemeWriterAgent",
                created_at=NOW,
            )
        )
    assert result.success is False
    assert result.error is not None
    rows = _rows(tmp_path / "run_failure_trace.jsonl")
    finish = next(row for row in rows if row["event_type"] == "tool_finished")
    assert finish["phase"] == "error"
    assert finish["output_summary"]["error_code"] == "TOOL_EXECUTION_EXCEPTION"
    assert finish["status"] == "failed"
