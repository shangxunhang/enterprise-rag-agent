"""Architecture contracts for the stage-1.5 modular-monolith refactor."""
from __future__ import annotations

import json
from pathlib import Path

from agent.runtime.shared_state_schema import SharedStateSchema
from agent.runtime.state_access import SharedStateWriter
from agent.runtime.step_dispatcher import WorkflowStepDispatcher
from agent.runtime.workflow_schema import WorkflowStepSchema
from apps.enterprise_document.schemas.scheme_writer import SchemeDraftSchema as NewDraft
from apps.enterprise_document.schemas.scheme_writer_schema import SchemeDraftSchema as CompatDraft
from data_capture.run_trace_recorder import JsonlRunTraceRecorder
from model_gateway.fake_llm_client import FakeLLMClient
from model_gateway.model_gateway import ModelGateway
from rag.chunker.FixedSizeChunker import FixedSizeChunker as LegacyFixedChunker
from rag.chunker.fixed_chunker import FixedSizeChunker as CanonicalFixedChunker
from schemas.context import ContextBundleSchema, TaskContextSchema, UserContextSchema
from schemas.model import ModelRequestSchema
from schemas.status import ExecutionStatus
from task.task_manager import JsonlTaskManager
from schemas.task import TaskSchema

NOW = "2026-07-15T00:00:00+00:00"


class FixedClock:
    def now_iso(self) -> str:
        return NOW


class FixedIds:
    def new_id(self, prefix: str) -> str:
        return f"{prefix}_fixed"


def _state() -> SharedStateSchema:
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


def test_scheme_schema_compatibility_exports_same_class() -> None:
    assert CompatDraft is NewDraft


def test_shared_state_writer_keeps_runtime_mutation_centralized() -> None:
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


def test_trace_recorder_accepts_clock_and_id_ports(tmp_path: Path) -> None:
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


def test_task_manager_accepts_clock_and_id_ports(tmp_path: Path) -> None:
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


def test_model_gateway_facade_uses_registry_router_invoker() -> None:
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


def test_workflow_dispatcher_returns_structured_unsupported_failure() -> None:
    state = _state()
    step = WorkflowStepSchema(
        step_id="tool_step",
        step_name="tool",
        step_type="tool",
        target_name="MissingToolHandler",
        order=1,
    )
    result = WorkflowStepDispatcher([]).execute(step, state)
    assert result.status == ExecutionStatus.FAILED
    assert result.error is not None
    assert result.error.error_code == "UNSUPPORTED_WORKFLOW_STEP_TYPE"


def test_fixed_chunkers_share_algorithm_but_preserve_legacy_metadata() -> None:
    document = {"doc_id": "doc_1", "text": "abcdefgh", "metadata": {}}
    legacy = LegacyFixedChunker(chunk_size=4, chunk_overlap=1).chunk_document(document)
    canonical = CanonicalFixedChunker(chunk_size=4, chunk_overlap=1).chunk_document(document)
    assert [item["text"] for item in legacy] == [item["text"] for item in canonical]
    assert legacy[0]["metadata"]["extra"]["chunk_type"] == "fixed_size"
    assert canonical[0]["metadata"]["extra"]["chunk_type"] == "fixed"
    assert canonical[0]["metadata"]["extra"]["start_char"] == 0


def test_runtime_packages_do_not_depend_on_eval_package() -> None:
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
