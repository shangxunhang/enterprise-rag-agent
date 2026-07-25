from __future__ import annotations

import json
import time
from typing import Any

import pytest
from langgraph.checkpoint.memory import InMemorySaver
from pydantic_core import PydanticSerializationError

from agent.agent_registry import AgentRegistry
from agent.base_agent import BaseAgent
from agent.runtime.graph_state import GraphStateSchema
from agent.runtime.langgraph_state import LangGraphRuntimeStateCodec
from agent.runtime.langgraph_workflow_engine import LangGraphWorkflowEngine
from agent.runtime.workflow_schema import WorkflowDefinitionSchema, WorkflowStepSchema
from core.runtime.execution_control import current_execution_control
from core.runtime.side_effects import InMemorySideEffectCommitStore
from schemas.agent import AgentResultSchema
from schemas.common import ErrorSchema
from schemas.context import ContextBundleSchema, TaskContextSchema, UserContextSchema
from schemas.status import ExecutionStatus


NOW = "2026-07-25T00:00:00+00:00"


def _state(*, run_id: str = "phase1-run", task_id: str | None = None) -> GraphStateSchema:
    resolved_task_id = task_id or f"task-{run_id}"
    return GraphStateSchema(
        task_id=resolved_task_id,
        run_id=run_id,
        task_type="phase1-test",
        user_input="verify serializable LangGraph state",
        context_bundle=ContextBundleSchema(
            user=UserContextSchema(user_query="verify serializable LangGraph state"),
            task=TaskContextSchema(
                task_id=resolved_task_id,
                run_id=run_id,
                task_type="phase1-test",
            ),
        ),
        created_at=NOW,
    )


def _workflow(
    *,
    agent_name: str,
    workflow_id: str = "phase1_serializable",
    workflow_version: str = "v1",
    output_keys: list[str] | None = None,
    **step_overrides: Any,
) -> WorkflowDefinitionSchema:
    step_values: dict[str, Any] = {
        "step_id": "phase1-node",
        "step_name": "phase1 node",
        "step_type": "agent",
        "target_name": agent_name,
        "output_keys": list(output_keys or []),
        "order": 1,
    }
    step_values.update(step_overrides)
    return WorkflowDefinitionSchema(
        workflow_id=workflow_id,
        workflow_name="Phase 1 serializable state",
        task_type="phase1-test",
        workflow_version=workflow_version,
        steps=[WorkflowStepSchema(**step_values)],
        created_at=NOW,
    )


def _registry(agent: BaseAgent) -> AgentRegistry:
    registry = AgentRegistry()
    registry.register(agent)
    return registry


def _latest_checkpoint(
    saver: InMemorySaver,
    *,
    run_id: str,
    workflow: WorkflowDefinitionSchema,
):
    config = LangGraphRuntimeStateCodec().checkpoint_config(
        workflow,
        run_id=run_id,
    )
    checkpoint = saver.get_tuple(config)
    assert checkpoint is not None
    return checkpoint


class _FinalResultAgent(BaseAgent):
    agent_name = "Phase1FinalResultAgent"
    agent_type = "sub_agent"

    def __init__(self) -> None:
        self.received_states: list[GraphStateSchema] = []

    def run(self, shared_state: GraphStateSchema) -> AgentResultSchema:
        self.received_states.append(shared_state)
        shared_state.final_result = {"run_id": shared_state.run_id}
        return AgentResultSchema(
            result_id=f"result-{shared_state.run_id}",
            task_id=shared_state.task_id,
            run_id=shared_state.run_id,
            agent_name=self.agent_name,
            agent_type=self.agent_type,
            status=ExecutionStatus.SUCCESS,
            result_type="phase1",
            result={"run_id": shared_state.run_id},
        )


class _MarkerAgent(BaseAgent):
    agent_type = "sub_agent"

    def __init__(self, *, agent_name: str, marker: str) -> None:
        self.agent_name = agent_name
        self.marker = marker

    def run(self, shared_state: GraphStateSchema) -> AgentResultSchema:
        shared_state.final_result = {"marker": self.marker}
        return AgentResultSchema(
            result_id=f"result-{self.marker}",
            task_id=shared_state.task_id,
            run_id=shared_state.run_id,
            agent_name=self.agent_name,
            agent_type=self.agent_type,
            status=ExecutionStatus.SUCCESS,
            result_type="phase1-marker",
            result={"marker": self.marker},
        )


def test_same_run_id_different_workflow_ids_use_distinct_checkpoint_namespaces() -> None:
    saver = InMemorySaver()
    agent_a = _MarkerAgent(agent_name="WorkflowAAgent", marker="workflow-a")
    agent_b = _MarkerAgent(agent_name="WorkflowBAgent", marker="workflow-b")
    registry = AgentRegistry()
    registry.register(agent_a)
    registry.register(agent_b)
    engine = LangGraphWorkflowEngine(registry, checkpointer=saver)
    workflow_a = _workflow(
        agent_name=agent_a.agent_name,
        workflow_id="workflow-a",
        output_keys=["final_result"],
    )
    workflow_b = _workflow(
        agent_name=agent_b.agent_name,
        workflow_id="workflow-b",
        output_keys=["final_result"],
    )

    engine.execute(workflow_a, _state(run_id="shared-run"))
    engine.execute(workflow_b, _state(run_id="shared-run"))

    checkpoint_a = _latest_checkpoint(
        saver,
        run_id="shared-run",
        workflow=workflow_a,
    )
    checkpoint_b = _latest_checkpoint(
        saver,
        run_id="shared-run",
        workflow=workflow_b,
    )
    runtime_a = checkpoint_a.checkpoint["channel_values"]
    runtime_b = checkpoint_b.checkpoint["channel_values"]
    state_a = LangGraphRuntimeStateCodec().restore_graph_state(
        runtime_a, workflow=workflow_a
    )
    state_b = LangGraphRuntimeStateCodec().restore_graph_state(
        runtime_b, workflow=workflow_b
    )

    assert checkpoint_a.config["configurable"]["checkpoint_ns"] == "workflow-a:v1"
    assert checkpoint_b.config["configurable"]["checkpoint_ns"] == "workflow-b:v1"
    assert runtime_a["workflow_id"] == "workflow-a"
    assert runtime_b["workflow_id"] == "workflow-b"
    assert state_a.final_result == {"marker": "workflow-a"}
    assert state_b.final_result == {"marker": "workflow-b"}


def test_same_run_and_workflow_id_different_versions_are_checkpoint_isolated() -> None:
    saver = InMemorySaver()
    agent_v1 = _MarkerAgent(agent_name="WorkflowV1Agent", marker="version-v1")
    agent_v2 = _MarkerAgent(agent_name="WorkflowV2Agent", marker="version-v2")
    registry = AgentRegistry()
    registry.register(agent_v1)
    registry.register(agent_v2)
    engine = LangGraphWorkflowEngine(registry, checkpointer=saver)
    workflow_v1 = _workflow(
        agent_name=agent_v1.agent_name,
        workflow_id="scheme-generation",
        workflow_version="v1",
        output_keys=["final_result"],
    )
    workflow_v2 = _workflow(
        agent_name=agent_v2.agent_name,
        workflow_id="scheme-generation",
        workflow_version="v2",
        output_keys=["final_result"],
    )

    engine.execute(workflow_v1, _state(run_id="shared-version-run"))
    engine.execute(workflow_v2, _state(run_id="shared-version-run"))

    checkpoint_v1 = _latest_checkpoint(
        saver,
        run_id="shared-version-run",
        workflow=workflow_v1,
    )
    checkpoint_v2 = _latest_checkpoint(
        saver,
        run_id="shared-version-run",
        workflow=workflow_v2,
    )
    runtime_v1 = checkpoint_v1.checkpoint["channel_values"]
    runtime_v2 = checkpoint_v2.checkpoint["channel_values"]
    state_v1 = LangGraphRuntimeStateCodec().restore_graph_state(
        runtime_v1, workflow=workflow_v1
    )
    state_v2 = LangGraphRuntimeStateCodec().restore_graph_state(
        runtime_v2, workflow=workflow_v2
    )

    assert checkpoint_v1.config["configurable"]["checkpoint_ns"] == (
        "scheme-generation:v1"
    )
    assert checkpoint_v2.config["configurable"]["checkpoint_ns"] == (
        "scheme-generation:v2"
    )
    assert state_v1.final_result == {"marker": "version-v1"}
    assert state_v2.final_result == {"marker": "version-v2"}


def test_unknown_runtime_state_version_is_rejected() -> None:
    codec = LangGraphRuntimeStateCodec()
    workflow = _workflow(agent_name=_FinalResultAgent.agent_name)
    runtime = codec.build_initial_state(workflow, _state())
    runtime["runtime_state_version"] = 999

    with pytest.raises(ValueError, match="runtime state version"):
        codec.restore_graph_state(runtime)


def test_missing_runtime_state_version_is_rejected() -> None:
    codec = LangGraphRuntimeStateCodec()
    workflow = _workflow(agent_name=_FinalResultAgent.agent_name)
    runtime = codec.build_initial_state(workflow, _state())
    runtime.pop("runtime_state_version")

    with pytest.raises(ValueError, match="runtime state version"):
        codec.restore_graph_state(runtime)


def test_runtime_and_payload_run_id_mismatch_is_rejected() -> None:
    codec = LangGraphRuntimeStateCodec()
    workflow = _workflow(agent_name=_FinalResultAgent.agent_name)
    runtime = codec.build_initial_state(workflow, _state())
    runtime["run_id"] = "runtime-run"
    runtime["graph_state_payload"]["run_id"] = "payload-run"

    with pytest.raises(ValueError, match="run_id"):
        codec.restore_graph_state(runtime)


def test_runtime_and_payload_task_id_mismatch_is_rejected() -> None:
    codec = LangGraphRuntimeStateCodec()
    workflow = _workflow(agent_name=_FinalResultAgent.agent_name)
    runtime = codec.build_initial_state(workflow, _state())
    runtime["task_id"] = "runtime-task"
    runtime["graph_state_payload"]["task_id"] = "payload-task"

    with pytest.raises(ValueError, match="task_id"):
        codec.restore_graph_state(runtime)


def test_workflow_identity_mismatch_is_rejected() -> None:
    codec = LangGraphRuntimeStateCodec()
    workflow_a = _workflow(
        agent_name=_FinalResultAgent.agent_name,
        workflow_id="workflow-a",
    )
    workflow_b = _workflow(
        agent_name=_FinalResultAgent.agent_name,
        workflow_id="workflow-b",
    )
    runtime = codec.build_initial_state(workflow_a, _state())

    with pytest.raises(ValueError, match="workflow"):
        codec.restore_graph_state(runtime, workflow=workflow_b)


def test_checkpoint_contains_only_json_serializable_runtime_state() -> None:
    saver = InMemorySaver()
    agent = _FinalResultAgent()
    state = _state()
    workflow = _workflow(
        agent_name=agent.agent_name,
        output_keys=["final_result"],
    )

    LangGraphWorkflowEngine(
        _registry(agent),
        checkpointer=saver,
    ).execute(workflow, state)

    checkpoint = _latest_checkpoint(
        saver,
        run_id=state.run_id,
        workflow=workflow,
    )
    runtime = checkpoint.checkpoint["channel_values"]
    assert "graph_state" not in runtime
    assert isinstance(runtime["graph_state_payload"], dict)
    assert runtime["runtime_state_version"] == 1
    assert runtime["workflow_id"] == workflow.workflow_id
    assert runtime["workflow_version"] == workflow.workflow_version
    json.dumps(runtime, ensure_ascii=False)


def test_live_service_object_is_rejected_before_entering_runtime_state() -> None:
    class _LiveService:
        pass

    state = _state()
    state.extra["service"] = _LiveService()
    workflow = _workflow(agent_name=_FinalResultAgent.agent_name)

    with pytest.raises((PydanticSerializationError, TypeError)):
        LangGraphRuntimeStateCodec().build_initial_state(workflow, state)


def test_checkpoint_restore_is_equivalent_to_projected_final_state() -> None:
    saver = InMemorySaver()
    agent = _FinalResultAgent()
    state = _state()
    workflow = _workflow(
        agent_name=agent.agent_name,
        output_keys=["final_result"],
    )

    LangGraphWorkflowEngine(
        _registry(agent),
        checkpointer=saver,
    ).execute(workflow, state)

    runtime = _latest_checkpoint(
        saver,
        run_id=state.run_id,
        workflow=workflow,
    ).checkpoint["channel_values"]
    restored = LangGraphRuntimeStateCodec().restore_graph_state(
        runtime, workflow=workflow
    )
    assert restored.model_dump(mode="json") == state.model_dump(mode="json")
    assert restored.status == ExecutionStatus.SUCCESS


def test_thread_id_isolation_and_workflow_namespace_metadata() -> None:
    saver = InMemorySaver()
    agent = _FinalResultAgent()
    workflow = _workflow(
        agent_name=agent.agent_name,
        workflow_id="phase1_isolation",
        output_keys=["final_result"],
    )
    engine = LangGraphWorkflowEngine(
        _registry(agent),
        checkpointer=saver,
    )
    first = _state(run_id="phase1-run-a")
    second = _state(run_id="phase1-run-b")

    engine.execute(workflow, first)
    engine.execute(workflow, second)

    first_checkpoint = _latest_checkpoint(
        saver,
        run_id=first.run_id,
        workflow=workflow,
    )
    second_checkpoint = _latest_checkpoint(
        saver,
        run_id=second.run_id,
        workflow=workflow,
    )
    first_state = LangGraphRuntimeStateCodec().restore_graph_state(
        first_checkpoint.checkpoint["channel_values"],
        workflow=workflow,
    )
    second_state = LangGraphRuntimeStateCodec().restore_graph_state(
        second_checkpoint.checkpoint["channel_values"],
        workflow=workflow,
    )
    expected_namespace = f"{workflow.workflow_id}:{workflow.workflow_version}"

    assert first_checkpoint.config["configurable"]["thread_id"] == first.run_id
    assert second_checkpoint.config["configurable"]["thread_id"] == second.run_id
    assert first_checkpoint.metadata["checkpoint_namespace"] == expected_namespace
    assert second_checkpoint.metadata["checkpoint_namespace"] == expected_namespace
    assert first_state.run_id == first.run_id
    assert second_state.run_id == second.run_id
    assert first_state.final_result != second_state.final_result


def test_step_node_returns_partial_update_not_mutated_runtime_state() -> None:
    agent = _FinalResultAgent()
    workflow = _workflow(
        agent_name=agent.agent_name,
        output_keys=["final_result"],
    )
    state = _state()
    engine = LangGraphWorkflowEngine(_registry(agent))
    engine.state_controller.start_workflow(state)
    runtime = LangGraphRuntimeStateCodec().build_initial_state(workflow, state)
    step = workflow.steps[0]
    node = engine._build_step_node(
        workflow=workflow,
        step=step,
        next_step_id=None,
        max_transitions=16,
    )

    update = node(runtime)

    assert isinstance(update, dict)
    assert "graph_state_payload" in update
    assert "graph_state" not in update
    assert set(update) < set(runtime)
    assert runtime["graph_state_payload"]["final_result"] is None
    assert update["graph_state_payload"]["final_result"] == {"run_id": state.run_id}


def test_legacy_agent_never_receives_external_or_canonical_runtime_state() -> None:
    saver = InMemorySaver()
    agent = _FinalResultAgent()
    external_state = _state()
    workflow = _workflow(
        agent_name=agent.agent_name,
        output_keys=["final_result"],
    )

    LangGraphWorkflowEngine(
        _registry(agent),
        checkpointer=saver,
    ).execute(workflow, external_state)

    assert len(agent.received_states) == 1
    received = agent.received_states[0]
    assert received is not external_state
    received.extra["late_agent_mutation"] = True
    assert "late_agent_mutation" not in external_state.extra
    checkpoint_runtime = _latest_checkpoint(
        saver,
        run_id=external_state.run_id,
        workflow=workflow,
    ).checkpoint["channel_values"]
    assert "late_agent_mutation" not in checkpoint_runtime["graph_state_payload"]["extra"]


def test_write_contract_violation_is_not_committed_to_checkpoint() -> None:
    class _ViolatingAgent(BaseAgent):
        agent_name = "Phase1ViolatingAgent"
        agent_type = "sub_agent"

        def run(self, shared_state: GraphStateSchema) -> AgentResultSchema:
            shared_state.contexts["undeclared"] = "must-not-commit"
            return AgentResultSchema(
                result_id="violation",
                task_id=shared_state.task_id,
                run_id=shared_state.run_id,
                agent_name=self.agent_name,
                agent_type=self.agent_type,
                status=ExecutionStatus.SUCCESS,
                result_type="violation",
                result={},
            )

    saver = InMemorySaver()
    agent = _ViolatingAgent()
    state = _state()
    workflow = _workflow(
        agent_name=agent.agent_name,
        output_keys=["final_result"],
        workflow_id="phase1_write_contract",
    )

    result = LangGraphWorkflowEngine(
        _registry(agent),
        checkpointer=saver,
    ).execute(workflow, state)

    checkpoint_state = LangGraphRuntimeStateCodec().restore_graph_state(
        _latest_checkpoint(
            saver,
            run_id=state.run_id,
            workflow=workflow,
        ).checkpoint["channel_values"],
        workflow=workflow,
    )
    assert result.status == ExecutionStatus.FAILED
    assert result.error is not None
    assert result.error.error_code == "STATE_WRITE_CONTRACT_VIOLATION"
    assert "undeclared" not in state.contexts
    assert "undeclared" not in checkpoint_state.contexts


def test_allowed_failure_partial_commit_is_present_in_checkpoint() -> None:
    class _BusinessGateAgent(BaseAgent):
        agent_name = "Phase1BusinessGateAgent"
        agent_type = "sub_agent"

        def run(self, shared_state: GraphStateSchema) -> AgentResultSchema:
            shared_state.final_result = {"partial": True}
            error = ErrorSchema(
                error_code="BUSINESS_GATE_FAILED",
                error_type="BusinessGateError",
                message="quality gate failed",
                retryable=False,
                failed_node=self.agent_name,
            )
            return AgentResultSchema(
                result_id="business-gate",
                task_id=shared_state.task_id,
                run_id=shared_state.run_id,
                agent_name=self.agent_name,
                agent_type=self.agent_type,
                status=ExecutionStatus.FAILED,
                result_type="business_gate",
                result={},
                error=error,
            )

    saver = InMemorySaver()
    agent = _BusinessGateAgent()
    state = _state()
    workflow = _workflow(
        agent_name=agent.agent_name,
        workflow_id="phase1_failure_partial",
        output_keys=["final_result"],
        commit_policy="allow_partial_on_failure",
        failure_write_paths=["final_result"],
        failure_commit_error_codes=["BUSINESS_GATE_FAILED"],
    )

    result = LangGraphWorkflowEngine(
        _registry(agent),
        checkpointer=saver,
    ).execute(workflow, state)

    checkpoint_state = LangGraphRuntimeStateCodec().restore_graph_state(
        _latest_checkpoint(
            saver,
            run_id=state.run_id,
            workflow=workflow,
        ).checkpoint["channel_values"],
        workflow=workflow,
    )
    assert result.status == ExecutionStatus.FAILED
    assert result.node_outputs[0].metadata["commit_decision"] == "failure_commit_partial"
    assert state.final_result == {"partial": True}
    assert checkpoint_state.final_result == {"partial": True}


def test_timeout_late_output_is_not_committed_to_checkpoint() -> None:
    class _SlowAgent(BaseAgent):
        agent_name = "Phase1SlowAgent"
        agent_type = "sub_agent"

        def run(self, shared_state: GraphStateSchema) -> AgentResultSchema:
            time.sleep(0.08)
            shared_state.final_result = {"late": True}
            return AgentResultSchema(
                result_id="slow",
                task_id=shared_state.task_id,
                run_id=shared_state.run_id,
                agent_name=self.agent_name,
                agent_type=self.agent_type,
                status=ExecutionStatus.SUCCESS,
                result_type="slow",
                result={},
            )

    saver = InMemorySaver()
    agent = _SlowAgent()
    state = _state()
    workflow = _workflow(
        agent_name=agent.agent_name,
        workflow_id="phase1_timeout",
        output_keys=["final_result"],
        timeout_seconds=0.01,
    )
    engine = LangGraphWorkflowEngine(
        _registry(agent),
        checkpointer=saver,
    )

    result = engine.execute(workflow, state)
    assert engine.wait_for_idle(timeout_seconds=0.5)

    checkpoint_state = LangGraphRuntimeStateCodec().restore_graph_state(
        _latest_checkpoint(
            saver,
            run_id=state.run_id,
            workflow=workflow,
        ).checkpoint["channel_values"],
        workflow=workflow,
    )
    assert result.status == ExecutionStatus.FAILED
    assert result.error is not None
    assert result.error.error_code == "WORKFLOW_NODE_TIMEOUT"
    assert state.final_result is None
    assert checkpoint_state.final_result is None


def test_retry_checkpoint_does_not_duplicate_side_effect_commit() -> None:
    committed: list[str] = []

    class _RetrySideEffectAgent(BaseAgent):
        agent_name = "Phase1RetrySideEffectAgent"
        agent_type = "sub_agent"

        def __init__(self) -> None:
            self.calls = 0

        def run(self, shared_state: GraphStateSchema) -> AgentResultSchema:
            self.calls += 1
            control = current_execution_control()
            assert control is not None
            control.defer_external_commit(
                lambda: committed.append(f"commit-{self.calls}"),
                description="phase1 checkpoint side effect",
                side_effect_id="phase1-stable-side-effect",
                semantic_fingerprint="phase1-payload-v1",
            )
            status = (
                ExecutionStatus.RETRYABLE_FAILED
                if self.calls == 1
                else ExecutionStatus.SUCCESS
            )
            return AgentResultSchema(
                result_id=f"side-effect-{self.calls}",
                task_id=shared_state.task_id,
                run_id=shared_state.run_id,
                agent_name=self.agent_name,
                agent_type=self.agent_type,
                status=status,
                result_type="side_effect",
                result={},
            )

    saver = InMemorySaver()
    side_effect_store = InMemorySideEffectCommitStore()
    agent = _RetrySideEffectAgent()
    state = _state()
    workflow = _workflow(
        agent_name=agent.agent_name,
        workflow_id="phase1_side_effect_retry",
        max_retries=1,
    )

    result = LangGraphWorkflowEngine(
        _registry(agent),
        checkpointer=saver,
        side_effect_commit_store=side_effect_store,
    ).execute(workflow, state)

    assert result.status == ExecutionStatus.SUCCESS
    assert agent.calls == 2
    assert committed == ["commit-2"]
    checkpoint = _latest_checkpoint(
        saver,
        run_id=state.run_id,
        workflow=workflow,
    )
    assert checkpoint.checkpoint["channel_values"]["retry_count"] == 1
