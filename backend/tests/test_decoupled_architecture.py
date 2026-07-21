# =============================================================================
# 中文阅读说明：自动化测试模块，用于验证主链、边界条件和回归行为。
# 主要定义：FixedClock、FixedIds、_state、test_scheme_schema_compatibility_exports_same_class、test_shared_state_writer_keeps_runtime_mutation_centralized、test_trace_recorder_accepts_clock_and_id_ports、test_task_manager_accepts_clock_and_id_ports、test_model_gateway_facade_uses_registry_router_invoker、test_workflow_dispatcher_returns_structured_unsupported_failure、test_fixed_chunkers_share_algorithm_but_preserve_legacy_metadata等。建议先从公开入口函数开始，再沿调用关系向下阅读。
# =============================================================================
"""Architecture contracts for the stage-1.5 modular-monolith refactor."""
from __future__ import annotations

import json
import re
from pathlib import Path

from agent.runtime.shared_state_schema import SharedStateSchema
from agent.runtime.state_access import SharedStateWriter
from apps.enterprise_document.schemas.scheme_writer import SchemeDraftSchema as NewDraft
from apps.enterprise_document.schemas.scheme_writer_schema import SchemeDraftSchema as CompatDraft
from data_capture.run_trace_recorder import JsonlRunTraceRecorder
from model_gateway.fake_llm_client import FakeLLMClient
from model_gateway.model_gateway import ModelGateway
from rag.chunker.FixedSizeChunker import FixedSizeChunker as LegacyFixedChunker
from rag.chunker.fixed_chunker import FixedSizeChunker as CanonicalFixedChunker
from schemas.context import ContextBundleSchema, TaskContextSchema, UserContextSchema
from schemas.model import ModelRequestSchema
from task.task_manager import JsonlTaskManager
from schemas.task import TaskSchema

NOW = "2026-07-15T00:00:00+00:00"


# 阅读注释（类）：封装 fixed clock，集中封装相关状态、依赖和行为。
class FixedClock:
    """封装 fixed clock，集中封装相关状态、依赖和行为。"""
    # 阅读注释（函数）：处理 now iso 相关逻辑。
    def now_iso(self) -> str:
        """处理 now iso 相关逻辑。

        返回:
            str
        """
        return NOW


# 阅读注释（类）：封装 fixed 标识集合，集中封装相关状态、依赖和行为。
class FixedIds:
    """封装 fixed 标识集合，集中封装相关状态、依赖和行为。"""
    # 阅读注释（函数）：处理 new 标识 相关逻辑。
    def new_id(self, prefix: str) -> str:
        """处理 new 标识 相关逻辑。

        参数:
            prefix: prefix，具体约束请结合类型标注和调用方确认。

        返回:
            str
        """
        return f"{prefix}_fixed"


# 阅读注释（函数）：处理 状态 相关逻辑。
def _state() -> SharedStateSchema:
    """处理 状态 相关逻辑。

    返回:
        SharedStateSchema

    阅读提示:
        主要直接调用：SharedStateSchema, ContextBundleSchema, UserContextSchema, TaskContextSchema。
    """
    return SharedStateSchema(
        task_id="task_1",
        run_id="run_1",
        task_type="test",
        user_input="test",
        context_bundle=ContextBundleSchema(
            user=UserContextSchema(user_query="test"),
            task=TaskContextSchema(task_id="task_1", run_id="run_1", task_type="test"),
        ),
        created_at=NOW,
    )


# 阅读注释（函数）：处理 测试 scheme Schema compatibility exports same class 相关逻辑。
def test_scheme_schema_compatibility_exports_same_class() -> None:
    """处理 测试 scheme Schema compatibility exports same class 相关逻辑。

    返回:
        None
    """
    assert CompatDraft is NewDraft


# 阅读注释（函数）：处理 测试 shared 状态 writer keeps 运行时 mutation centralized 相关逻辑。
def test_shared_state_writer_keeps_runtime_mutation_centralized() -> None:
    """处理 测试 shared 状态 writer keeps 运行时 mutation centralized 相关逻辑。

    返回:
        None

    阅读提示:
        主要直接调用：_state, SharedStateWriter, writer.set_evidence_context, writer.set_final_result。
    """
    state = _state()
    writer = SharedStateWriter()
    writer.set_evidence_context(
        state,
        query="q",
        context_text="ctx",
        retrieved_chunks=[{"chunk_id": "c1"}],
        citations=[{"citation_id": "C1"}],
        used_doc_ids=["d1"],
        evidence_sufficient=True,
    )
    writer.set_final_result(state, {"status": "success"})
    assert state.context_bundle.evidence.query == "q"
    assert state.final_result == {"status": "success"}


# 阅读注释（函数）：处理 测试 Trace recorder accepts clock and 标识 ports 相关逻辑。
def test_trace_recorder_accepts_clock_and_id_ports(tmp_path: Path) -> None:
    """处理 测试 Trace recorder accepts clock and 标识 ports 相关逻辑。

    参数:
        tmp_path: tmp 路径，具体约束请结合类型标注和调用方确认。

    返回:
        None

    阅读提示:
        主要直接调用：JsonlRunTraceRecorder, FixedClock, FixedIds, recorder.record, json.loads, strip, read_text。
    """
    recorder = JsonlRunTraceRecorder(
        tmp_path,
        clock=FixedClock(),
        id_generator=FixedIds(),
    )
    event = recorder.record(
        task_id="task_1",
        run_id="run_1",
        event_type="test",
        component_type="unit",
        component_name="contract",
    )
    assert event.event_id == "event_fixed"
    assert event.created_at == NOW
    row = json.loads((tmp_path / "run_1_trace.jsonl").read_text().strip())
    assert row["event_id"] == "event_fixed"


# 阅读注释（函数）：处理 测试 任务 管理器 accepts clock and 标识 ports 相关逻辑。
def test_task_manager_accepts_clock_and_id_ports(tmp_path: Path) -> None:
    """处理 测试 任务 管理器 accepts clock and 标识 ports 相关逻辑。

    参数:
        tmp_path: tmp 路径，具体约束请结合类型标注和调用方确认。

    返回:
        None

    阅读提示:
        主要直接调用：JsonlTaskManager, FixedClock, FixedIds, TaskSchema, manager.create_task。
    """
    manager = JsonlTaskManager(tmp_path, clock=FixedClock(), id_generator=FixedIds())
    task = TaskSchema(
        task_id="task_1",
        run_id="run_1",
        task_type="test",
        user_input="test",
        project_input={},
        created_at=NOW,
    )
    record = manager.create_task(task)
    assert record.created_at == NOW
    assert record.events[0].event_id == "task_event_fixed"


# 阅读注释（函数）：处理 测试 模型 网关 facade uses 注册表 路由器 invoker 相关逻辑。
def test_model_gateway_facade_uses_registry_router_invoker() -> None:
    """处理 测试 模型 网关 facade uses 注册表 路由器 invoker 相关逻辑。

    返回:
        None

    阅读提示:
        主要直接调用：ModelGateway, gateway.register_client, FakeLLMClient, gateway.generate, ModelRequestSchema。
    """
    gateway = ModelGateway(default_model_name="fake_llm")
    gateway.register_client(FakeLLMClient())
    response = gateway.generate(
        ModelRequestSchema(
            model_call_id="call_1",
            task_id="task_1",
            run_id="run_1",
            model_name="",
            prompt="hello",
            created_at=NOW,
        )
    )
    assert response.success is True
    assert response.model_name == "fake_llm"


# 阅读注释（函数）：处理 测试 fixed chunkers share algorithm but preserve legacy 元数据 相关逻辑。
def test_fixed_chunkers_share_algorithm_but_preserve_legacy_metadata() -> None:
    """处理 测试 fixed chunkers share algorithm but preserve legacy 元数据 相关逻辑。

    返回:
        None

    阅读提示:
        主要直接调用：chunk_document, LegacyFixedChunker, CanonicalFixedChunker。
    """
    document = {"doc_id": "doc_1", "text": "abcdefgh", "metadata": {}}
    legacy = LegacyFixedChunker(chunk_size=4, chunk_overlap=1).chunk_document(document)
    canonical = CanonicalFixedChunker(chunk_size=4, chunk_overlap=1).chunk_document(document)
    assert [item["text"] for item in legacy] == [item["text"] for item in canonical]
    assert legacy[0]["metadata"]["extra"]["chunk_type"] == "fixed_size"
    assert canonical[0]["metadata"]["extra"]["chunk_type"] == "fixed"
    assert canonical[0]["metadata"]["extra"]["start_char"] == 0


# 阅读注释（函数）：处理 测试 运行时 packages do not depend on 评测 package 相关逻辑。
def test_production_code_does_not_write_compatibility_contexts_directly() -> None:
    """Compatibility state is a one-way projection owned by SharedStateWriter."""
    root = Path(__file__).resolve().parents[1]
    allowed = {
        (root / "agent/runtime/state_access.py").resolve(),
    }
    assignment = re.compile(r"\b(?:state|shared_state)\.contexts\[[^\]]+\]\s*=")
    offenders: list[str] = []
    for path in root.rglob("*.py"):
        resolved = path.resolve()
        if resolved in allowed or "tests" in path.parts or "__pycache__" in path.parts:
            continue
        if assignment.search(path.read_text(encoding="utf-8")):
            offenders.append(str(path.relative_to(root)))
    assert offenders == []


def test_runtime_packages_do_not_depend_on_eval_package() -> None:
    """处理 测试 运行时 packages do not depend on 评测 package 相关逻辑。

    返回:
        None

    阅读提示:
        主要直接调用：resolve, Path, runtime_root.rglob, path.read_text, offenders.append, str, path.relative_to。
    """
    root = Path(__file__).resolve().parents[1]
    runtime_roots = [
        root / "agent",
        root / "application",
        root / "apps",
        root / "bootstrap",
        root / "model_gateway",
        root / "rag",
    ]
    offenders = []
    for runtime_root in runtime_roots:
        for path in runtime_root.rglob("*.py"):
            source = path.read_text(encoding="utf-8")
            if "from eval." in source or "import eval." in source:
                offenders.append(str(path.relative_to(root)))
    assert offenders == []
