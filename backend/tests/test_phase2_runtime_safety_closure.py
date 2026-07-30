"""Adversarial closure tests for the production Phase 2 workflow engine."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from contextlib import ExitStack
from dataclasses import replace
import importlib.util
import inspect
import threading
import os
from pathlib import Path
import time
from typing import Callable
from unittest.mock import patch

import pytest

from application.mainline_service import MainlineApplicationService
from agent.runtime.graph_state import GraphStateSchema
from agent.runtime.workflow_schema import WorkflowDefinitionSchema
from apps.enterprise_document.agents.scheme_writer_agent import SchemeWriterAgent
from apps.enterprise_document.schemas.scheme_generation_graph import (
    SchemeGenerationGraphPayloadSchema,
)
from apps.enterprise_document.services.scheme_writer.evidence_service import (
    SchemeEvidenceService,
)
from apps.enterprise_document.services.scheme_writer.stage_service import (
    SchemeGenerationStageService,
)
from apps.enterprise_document.services.scheme_writer.use_case import (
    SchemeGenerationUseCase,
)
from apps.enterprise_document.workflows.top_level_business_graph import (
    EnterpriseDocumentWorkflowEngine,
    phase2_checkpoint_identity,
)
from bootstrap.supervisor_factory import SupervisorFactory
from core.config import get_settings
from core.retry_policy import RetryDecisionPolicy, classify_failure
from core.runtime.execution_control import (
    WorkflowExecutionCancelled,
    current_execution_control,
)
from rag.services.rag_service import RAGService
from schemas.context import ContextBundleSchema, TaskContextSchema, UserContextSchema
from schemas.failure import FailureCategory
from schemas.rag import RAGToolInputSchema


SUCCESS_SEQUENCE = [
    "normalize_project_input",
    "plan_document",
    "retrieve_document_evidence",
    "initialize_generation",
    "execute_sections",
    "assemble_document",
    "evaluate_document_gate",
    "persist_deliverable",
    "finalize_workflow_result",
]
TECHNICAL_FAILURE_SEQUENCE = [
    "normalize_project_input",
    "plan_document",
    "retrieve_document_evidence",
    "finalize_workflow_failure",
]


class _InspectingSupervisorFactory(SupervisorFactory):
    def __init__(
        self,
        mutate_engine: Callable[[EnterpriseDocumentWorkflowEngine], None]
        | None = None,
    ) -> None:
        super().__init__()
        self.mutate_engine = mutate_engine
        self.engines: list[EnterpriseDocumentWorkflowEngine] = []

    def build(self, **kwargs):
        supervisor = super().build(**kwargs)
        engine = supervisor.workflow_engine
        assert isinstance(engine, EnterpriseDocumentWorkflowEngine)
        self.engines.append(engine)
        if self.mutate_engine is not None:
            self.mutate_engine(engine)
        return supervisor


def _run_production_fake(
    output_root: Path,
    *,
    run_id: str,
    patchers=(),
    mutate_engine: Callable[[EnterpriseDocumentWorkflowEngine], None]
    | None = None,
) -> tuple[dict, _InspectingSupervisorFactory]:
    settings = replace(
        get_settings(),
        data_root=output_root,
        default_model_name="fake_llm",
        supervisor_model_name="fake_llm",
        enable_llm_routing=False,
        trace_enabled=True,
        data_capture_enabled=True,
    )
    environment = {
        "USE_REAL_RAG_TOOL": "false",
        "FAKE_RAG_SCENARIO": "default",
        "FAKE_LLM_SCENARIO": "default",
        "ENABLE_AGENT_SELF_RAG": "false",
        "ENABLE_SEMANTIC_GATE": "false",
    }
    factory = _InspectingSupervisorFactory(mutate_engine)
    with ExitStack() as stack:
        stack.enter_context(patch.dict(os.environ, environment))
        stack.enter_context(
            patch.object(
                SchemeWriterAgent,
                "run",
                side_effect=AssertionError("coarse SchemeWriterAgent.run invoked"),
            )
        )
        stack.enter_context(
            patch.object(
                SchemeGenerationUseCase,
                "run",
                side_effect=AssertionError("legacy SchemeGenerationUseCase.run invoked"),
            )
        )
        for patcher in patchers:
            stack.enter_context(patcher)
        summary = MainlineApplicationService(
            supervisor_factory=factory
        ).run(
            project_root=Path(__file__).resolve().parents[2],
            user_input="生成企业级 RAG-Agent 系统建设方案",
            run_id=run_id,
            output_root=output_root,
            clean_existing=True,
            settings=settings,
            allow_demo_defaults=True,
            enable_agent_self_rag=False,
        )
    return summary, factory


def _execution(summary: dict) -> dict:
    return summary["supervisor_result"]["result"]["workflow_execution"]


def _error_code(summary: dict) -> str | None:
    error = _execution(summary).get("error") or {}
    return error.get("error_code")


def _rag_contract_failure_bundle(*, run_id: str):
    class BrokenRetrievalRuntime:
        def retrieve(self, payload):
            del payload
            raise AttributeError(
                "type object 'ParentChunkStore' has no attribute 'from_jsonl'"
            )

    service = RAGService(
        Path(__file__).resolve().parents[2],
        retrieval_runtime=BrokenRetrievalRuntime(),
        allow_legacy_unscoped=True,
    )
    return service.retrieve(
        RAGToolInputSchema(
            task_id=f"task_{run_id}",
            run_id=run_id,
            agent_name="SchemeWriterAgent",
            query="forced contract failure",
        )
    )


def _load_run_demo_module():
    script = Path(__file__).resolve().parents[2] / "scripts" / "run_demo.py"
    spec = importlib.util.spec_from_file_location(
        "phase2_runtime_safety_run_demo",
        script,
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _capture_canonical_state(
    engine: EnterpriseDocumentWorkflowEngine,
    sink: list,
) -> None:
    original_execute = engine.execute

    def execute(workflow, graph_state):
        sink.append(graph_state)
        return original_execute(workflow, graph_state)

    engine.execute = execute


@pytest.mark.parametrize(
    "exc",
    [
        AttributeError("dependency has no attribute 'legacy_factory'"),
        TypeError("Builder() got an unexpected keyword argument 'legacy_path'"),
        ImportError("cannot import name 'LegacyRegistry'"),
    ],
)
def test_dependency_contract_failures_are_configuration_and_not_retryable(
    exc,
) -> None:
    category = classify_failure(exc)
    assert category == FailureCategory.CONFIGURATION
    assert RetryDecisionPolicy().should_retry(category) is False


@pytest.fixture(scope="module")
def successful_execution(tmp_path_factory) -> dict:
    summary, _ = _run_production_fake(
        tmp_path_factory.mktemp("phase2-runtime-success"),
        run_id="phase2_runtime_success_records",
    )
    return summary


def test_business_node_contracts_and_worker_lifecycle_are_production_features() -> None:
    assert hasattr(EnterpriseDocumentWorkflowEngine, "active_worker_count")
    assert hasattr(EnterpriseDocumentWorkflowEngine, "cancel_active_workers")
    assert hasattr(EnterpriseDocumentWorkflowEngine, "defer_until_idle")
    assert "_active_workflow" not in inspect.getsource(
        EnterpriseDocumentWorkflowEngine
    )


def test_success_path_returns_every_actual_business_node(
    successful_execution,
) -> None:
    execution = _execution(successful_execution)

    assert len(execution["node_inputs"]) == 9
    assert len(execution["node_outputs"]) == 9
    assert len(execution["node_results"]) == 9
    assert execution["completed_node_ids"] == SUCCESS_SEQUENCE


def test_technical_failure_returns_only_actual_business_nodes(tmp_path) -> None:
    summary, _ = _run_production_fake(
        tmp_path,
        run_id="phase2_runtime_failure_records",
        patchers=(
            patch.object(
                SchemeGenerationStageService,
                "retrieve_document_evidence",
                side_effect=RuntimeError("forced retrieval failure"),
            ),
        ),
    )
    execution = _execution(summary)

    assert len(execution["node_inputs"]) == 4
    assert len(execution["node_outputs"]) == 4
    assert len(execution["node_results"]) == 4
    assert execution["completed_node_ids"] == TECHNICAL_FAILURE_SEQUENCE


def test_rag_contract_failure_preserves_error_across_all_result_layers(
    tmp_path,
) -> None:
    run_id = "phase2_rag_contract_failure"
    rag_failure = _rag_contract_failure_bundle(run_id=run_id)
    assert rag_failure.error is not None
    assert rag_failure.error.error_code == "RAG_RETRIEVAL_FAILED"
    assert rag_failure.error.error_type == "AttributeError"
    assert "ParentChunkStore" in rag_failure.error.message
    assert rag_failure.error.failure_category == FailureCategory.CONFIGURATION
    assert rag_failure.error.retryable is False
    assert rag_failure.error.failed_node is None

    summary, _ = _run_production_fake(
        tmp_path,
        run_id=run_id,
        patchers=(
            patch.object(
                SchemeEvidenceService,
                "retrieve",
                return_value=rag_failure,
            ),
        ),
    )
    execution = _execution(summary)
    retrieval_result = next(
        item
        for item in execution["node_results"]
        if item["agent_name"] == "retrieve_document_evidence"
    )
    terminal_result = execution["node_results"][-1]
    workflow_error = execution["error"]
    report = _load_run_demo_module()._build_e2e_report(
        summary,
        runtime_preflight={},
        validation=None,
    )

    errors = [
        retrieval_result["error"],
        terminal_result["error"],
        workflow_error,
        report["error"],
    ]
    for error in errors:
        assert error["error_code"] == "RAG_RETRIEVAL_FAILED"
        assert error["error_type"] == "AttributeError"
        assert "ParentChunkStore" in error["message"]
        assert error["failed_node"] == "retrieve_document_evidence"
        assert error["failure_category"] == "configuration"
        assert error["retryable"] is False

    assert summary["status"] == "failed"
    assert execution["completed_node_ids"] == TECHNICAL_FAILURE_SEQUENCE


def test_non_retrieval_contract_failure_keeps_its_own_node_name(tmp_path) -> None:
    summary, _ = _run_production_fake(
        tmp_path,
        run_id="phase2_plan_contract_failure",
        patchers=(
            patch.object(
                SchemeGenerationStageService,
                "prepare_document_plan",
                side_effect=AttributeError(
                    "PlannerRegistry has no attribute 'resolve_contract'"
                ),
            ),
        ),
    )

    error = _execution(summary)["error"]
    assert error["error_code"] == "SCHEME_GENERATION_STAGE_FAILED"
    assert error["failed_node"] == "plan_document"
    assert error["failure_category"] == "configuration"
    assert error["retryable"] is False


def test_undeclared_graph_state_write_is_blocked_and_rolled_back(tmp_path) -> None:
    canonical_states: list = []
    original = SchemeGenerationStageService.prepare_document_plan

    def corrupt_tenant(self, shared_state, payload):
        updated = original(self, shared_state, payload)
        shared_state.tenant_id = "UNDECLARED_CORRUPTION"
        return updated

    summary, _ = _run_production_fake(
        tmp_path,
        run_id="phase2_runtime_graph_contract",
        patchers=(
            patch.object(
                SchemeGenerationStageService,
                "prepare_document_plan",
                corrupt_tenant,
            ),
        ),
        mutate_engine=lambda engine: _capture_canonical_state(
            engine, canonical_states
        ),
    )

    assert _error_code(summary) == "STATE_WRITE_CONTRACT_VIOLATION"
    assert canonical_states[0].tenant_id != "UNDECLARED_CORRUPTION"
    assert _execution(summary)["completed_node_ids"] == [
        "normalize_project_input",
        "plan_document",
        "finalize_workflow_failure",
    ]


def test_undeclared_payload_write_is_blocked_and_rolled_back(tmp_path) -> None:
    original = SchemeGenerationStageService.prepare_document_plan

    def corrupt_route(self, shared_state, payload):
        updated = original(self, shared_state, payload)
        return updated.model_copy(
            update={"artifact_route": "deliverable"},
            deep=True,
        )

    summary, _ = _run_production_fake(
        tmp_path,
        run_id="phase2_runtime_payload_contract",
        patchers=(
            patch.object(
                SchemeGenerationStageService,
                "prepare_document_plan",
                corrupt_route,
            ),
        ),
    )

    assert _error_code(summary) == "STATE_WRITE_CONTRACT_VIOLATION"
    assert summary["deliverable_final_result"] is None


def test_workflow_execution_cancelled_is_normalized_and_task_is_terminal(
    tmp_path,
) -> None:
    summary, _ = _run_production_fake(
        tmp_path,
        run_id="phase2_runtime_cancelled",
        patchers=(
            patch.object(
                SchemeGenerationStageService,
                "prepare_document_plan",
                side_effect=WorkflowExecutionCancelled("forced cancellation"),
            ),
        ),
    )

    assert _error_code(summary) == "WORKFLOW_NODE_CANCELLED"
    error = _execution(summary)["error"]
    assert error["failed_node"] == "plan_document"
    assert error["failure_category"] == "cancelled"
    assert summary["task_state"]["status"] != "running"


def test_hard_timeout_returns_before_sleep_and_discards_late_state(tmp_path) -> None:
    canonical_states: list = []
    engine_durations: list[float] = []
    original = SchemeGenerationStageService.prepare_document_plan

    def slow_stage(self, shared_state, payload):
        time.sleep(1.0)
        shared_state.tenant_id = "LATE_RESULT"
        return original(self, shared_state, payload)

    def configure(engine: EnterpriseDocumentWorkflowEngine) -> None:
        _capture_canonical_state(engine, canonical_states)
        wrapped_execute = engine.execute

        def timed_execute(workflow, graph_state):
            started = time.monotonic()
            try:
                return wrapped_execute(workflow, graph_state)
            finally:
                engine_durations.append(time.monotonic() - started)

        engine.execute = timed_execute
        engine.node_contracts["plan_document"] = replace(
            engine.node_contracts["plan_document"],
            timeout_seconds=0.01,
        )

    summary, factory = _run_production_fake(
        tmp_path,
        run_id="phase2_runtime_hard_timeout",
        patchers=(
            patch.object(
                SchemeGenerationStageService,
                "prepare_document_plan",
                slow_stage,
            ),
        ),
        mutate_engine=configure,
    )
    engine = factory.engines[0]
    assert engine.wait_for_idle(timeout_seconds=1.0)

    assert engine_durations and engine_durations[0] < 0.75
    assert _error_code(summary) == "WORKFLOW_NODE_TIMEOUT"
    error = _execution(summary)["error"]
    assert error["failed_node"] == "plan_document"
    assert error["failure_category"] == "timeout"
    assert canonical_states[0].tenant_id != "LATE_RESULT"
    assert summary["task_state"]["status"] != "running"


def test_hard_timeout_discards_deferred_external_side_effect(tmp_path) -> None:
    committed: list[str] = []
    original = SchemeGenerationStageService.prepare_document_plan

    def slow_stage(self, shared_state, payload):
        control = current_execution_control()
        assert control is not None
        control.defer_external_commit(
            lambda: committed.append("committed"),
            description="phase2-late-side-effect",
            side_effect_id=(
                f"phase2-late-side-effect:{shared_state.run_id}:plan_document"
            ),
        )
        time.sleep(0.15)
        return original(self, shared_state, payload)

    def configure(engine: EnterpriseDocumentWorkflowEngine) -> None:
        engine.node_contracts["plan_document"] = replace(
            engine.node_contracts["plan_document"],
            timeout_seconds=0.01,
        )

    summary, factory = _run_production_fake(
        tmp_path,
        run_id="phase2_runtime_late_side_effect",
        patchers=(
            patch.object(
                SchemeGenerationStageService,
                "prepare_document_plan",
                slow_stage,
            ),
        ),
        mutate_engine=configure,
    )
    engine = factory.engines[0]
    assert engine.wait_for_idle(timeout_seconds=1.0)

    assert _error_code(summary) == "WORKFLOW_NODE_TIMEOUT"
    assert committed == []


def test_retry_uses_stable_side_effect_authority_and_commits_once(tmp_path) -> None:
    attempts: list[int] = []
    committed: list[str] = []
    original = SchemeGenerationStageService.prepare_document_plan

    def flaky_stage(self, shared_state, payload):
        attempt = len(attempts) + 1
        attempts.append(attempt)
        control = current_execution_control()
        assert control is not None
        control.defer_external_commit(
            lambda: committed.append(f"commit:{shared_state.run_id}"),
            description="phase2-retry-side-effect",
            side_effect_id=(
                f"phase2-retry-side-effect:{shared_state.run_id}:plan_document"
            ),
        )
        if attempt == 1:
            raise ConnectionError("temporarily unavailable")
        return original(self, shared_state, payload)

    def configure(engine: EnterpriseDocumentWorkflowEngine) -> None:
        engine.node_contracts["plan_document"] = replace(
            engine.node_contracts["plan_document"],
            max_retries=1,
        )

    summary, _ = _run_production_fake(
        tmp_path,
        run_id="phase2_runtime_retry_exactly_once",
        patchers=(
            patch.object(
                SchemeGenerationStageService,
                "prepare_document_plan",
                flaky_stage,
            ),
        ),
        mutate_engine=configure,
    )

    execution = _execution(summary)
    plan_output = next(
        item
        for item in execution["node_outputs"]
        if item["node_id"] == "plan_document"
    )
    assert attempts == [1, 2]
    assert committed == ["commit:phase2_runtime_retry_exactly_once"]
    assert plan_output["metadata"]["attempt_count"] == 2
    assert plan_output["metadata"]["retry_count"] == 1
    assert plan_output["metadata"]["attempt_history"][0]["retry"] is True
    assert plan_output["metadata"]["attempt_history"][1]["retry"] is False


def test_same_engine_concurrent_workflows_keep_explicit_identity() -> None:
    now = "2026-07-25T00:00:00+00:00"

    class _RuntimeSupport:
        @staticmethod
        def now_iso() -> str:
            return now

    class _NoopStageService:
        runtime_support = _RuntimeSupport()

        @staticmethod
        def _stage(shared_state, payload):
            return payload

        prepare_document_plan = _stage
        retrieve_document_evidence = _stage
        initialize_generation = _stage
        execute_sections = _stage
        assemble_document = _stage
        evaluate_document_gate = _stage
        persist_deliverable = _stage
        persist_rejected = _stage
        finalize_workflow_result = _stage
        finalize_workflow_failure = _stage

    engine = EnterpriseDocumentWorkflowEngine(
        stage_service=_NoopStageService(),
    )
    observed: list[tuple[str, str, str]] = []
    observed_lock = threading.Lock()

    def fake_run_stage(
        runtime_state,
        *,
        workflow,
        node_name,
        stage,
    ):
        del stage
        with observed_lock:
            observed.append(
                (
                    str(runtime_state["workflow_id"]),
                    workflow.workflow_id,
                    str(runtime_state["run_id"]),
                )
            )
        return {
            "current_business_node": node_name,
            "business_route": (
                "deliverable"
                if node_name == "evaluate_document_gate"
                else "continue"
            ),
        }

    engine._run_stage = fake_run_stage

    def make_workflow(workflow_id: str, version: str):
        return WorkflowDefinitionSchema(
            workflow_id=workflow_id,
            workflow_name=workflow_id,
            task_type="scheme_generation",
            workflow_version=version,
            steps=[],
            created_at=now,
        )

    def make_runtime(workflow, run_id: str):
        task_id = f"task-{run_id}"
        graph_state = GraphStateSchema(
            task_id=task_id,
            run_id=run_id,
            task_type="scheme_generation",
            user_input="concurrency identity test",
            context_bundle=ContextBundleSchema(
                user=UserContextSchema(user_query="concurrency identity test"),
                task=TaskContextSchema(
                    task_id=task_id,
                    run_id=run_id,
                    task_type="scheme_generation",
                ),
            ),
            created_at=now,
        )
        runtime = engine.runtime_state_codec.build_initial_state(
            workflow,
            graph_state,
        )
        payload = SchemeGenerationGraphPayloadSchema(
            task_id=task_id,
            run_id=run_id,
            started_at=now,
        )
        runtime.update(engine.business_state_codec.build_initial_update(payload))
        return runtime

    workflow_a = make_workflow("concurrent-workflow-a", "v1")
    workflow_b = make_workflow("concurrent-workflow-b", "v2")
    runtime_a = make_runtime(workflow_a, "concurrent-run-a")
    runtime_b = make_runtime(workflow_b, "concurrent-run-b")
    graph_a = engine.build_graph(workflow_a)
    graph_b = engine.build_graph(workflow_b)

    with ThreadPoolExecutor(max_workers=2) as pool:
        future_a = pool.submit(
            graph_a.invoke,
            runtime_a,
            phase2_checkpoint_identity(workflow_a, run_id="concurrent-run-a"),
        )
        future_b = pool.submit(
            graph_b.invoke,
            runtime_b,
            phase2_checkpoint_identity(workflow_b, run_id="concurrent-run-b"),
        )
        final_a = future_a.result(timeout=5)
        final_b = future_b.result(timeout=5)

    assert final_a["workflow_id"] == workflow_a.workflow_id
    assert final_b["workflow_id"] == workflow_b.workflow_id
    assert len(observed) == 18
    assert all(envelope_id == closure_id for envelope_id, closure_id, _ in observed)
    assert {
        (closure_id, run_id)
        for _, closure_id, run_id in observed
    } == {
        (workflow_a.workflow_id, "concurrent-run-a"),
        (workflow_b.workflow_id, "concurrent-run-b"),
    }
    assert engine.checkpoint_history(
        workflow_a,
        run_id="concurrent-run-a",
    )
    assert engine.checkpoint_history(
        workflow_b,
        run_id="concurrent-run-b",
    )
