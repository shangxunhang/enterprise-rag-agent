from __future__ import annotations

from agent.agent_registry import AgentRegistry
from agent.base_agent import BaseAgent
from agent.runtime.graph_state import GraphStateSchema
from agent.runtime.graph_state_ops import GraphStateApplier, GraphStateProjector
from agent.runtime.node_adapter import LegacyAgentNodeAdapter
from agent.runtime.step_dispatcher import WorkflowStepDispatcher
from agent.runtime.steps.agent_step import AgentStepHandler
from agent.runtime.workflow_executor import WorkflowExecutor
from agent.runtime.workflow_schema import WorkflowDefinitionSchema, WorkflowStepSchema
from contracts.workflow_engine import WorkflowEnginePort
from schemas.agent import AgentResultSchema
from schemas.common import ErrorSchema
from schemas.context import ContextBundleSchema, TaskContextSchema, UserContextSchema
from schemas.status import ExecutionStatus

NOW = "2026-07-17T00:00:00+00:00"


class _WriteAgent(BaseAgent):
    agent_name = "WriteAgent"
    agent_type = "sub_agent"

    def run(self, shared_state) -> AgentResultSchema:
        shared_state.context_bundle.business.project_input = {"project": "政务云"}
        shared_state.contexts["project_input"] = {"project": "政务云"}
        shared_state.structured_facts.append({"fact": "500 users"})
        return AgentResultSchema(
            result_id="write_result",
            task_id=shared_state.task_id,
            run_id=shared_state.run_id,
            agent_name=self.agent_name,
            agent_type=self.agent_type,
            status=ExecutionStatus.SUCCESS,
            result_type="write",
            result={"ok": True},
        )


class _ReadAgent(BaseAgent):
    agent_name = "ReadAgent"
    agent_type = "sub_agent"

    def run(self, shared_state) -> AgentResultSchema:
        value = shared_state.context_bundle.business.project_input
        shared_state.final_result = {"read": value}
        return AgentResultSchema(
            result_id="read_result",
            task_id=shared_state.task_id,
            run_id=shared_state.run_id,
            agent_name=self.agent_name,
            agent_type=self.agent_type,
            status=ExecutionStatus.SUCCESS,
            result_type="read",
            result={"read": value},
        )


def _state() -> GraphStateSchema:
    return GraphStateSchema(
        task_id="task_1",
        run_id="run_1",
        task_type="test",
        user_input="生成政务云方案",
        requirements={"project_input": {"project": "raw"}},
        context_bundle=ContextBundleSchema(
            user=UserContextSchema(user_query="生成政务云方案"),
            task=TaskContextSchema(task_id="task_1", run_id="run_1", task_type="test"),
        ),
        created_at=NOW,
    )


def _workflow() -> WorkflowDefinitionSchema:
    return WorkflowDefinitionSchema(
        workflow_id="wf_graph",
        workflow_name="graph contract test",
        task_type="test",
        workflow_version="v1",
        steps=[
            WorkflowStepSchema(
                step_id="node_1",
                step_name="write",
                step_type="agent",
                target_name="WriteAgent",
                input_keys=["project_input"],
                output_keys=["normalized_project_input", "structured_facts"],
                order=1,
            ),
            WorkflowStepSchema(
                step_id="node_2",
                step_name="read",
                step_type="agent",
                target_name="ReadAgent",
                input_keys=["normalized_project_input", "structured_facts"],
                output_keys=["final_result"],
                order=2,
            ),
        ],
        created_at=NOW,
    )


def _registry() -> AgentRegistry:
    registry = AgentRegistry()
    registry.register(_WriteAgent())
    registry.register(_ReadAgent())
    return registry


def test_legacy_agent_executes_on_copy_and_only_delta_commit_mutates_state() -> None:
    state = _state()
    workflow = _workflow()
    step = workflow.steps[0]
    registry = _registry()
    dispatcher = WorkflowStepDispatcher([AgentStepHandler(registry)])
    node_input = GraphStateProjector().build_node_input(
        workflow=workflow,
        step=step,
        state=state,
    )

    output = LegacyAgentNodeAdapter(dispatcher).execute(
        step=step,
        node_input=node_input,
        state=state,
    )

    assert state.context_bundle.business.project_input == {}
    assert output.state_delta.base_revision == 0
    assert output.state_delta.next_revision == 1
    assert "contexts" in output.state_delta.observed_write_roots
    assert output.metadata["write_contract_passed"] is True
    assert output.metadata["isolated_state_execution"] is True

    GraphStateApplier().apply(state, output.state_delta)
    assert state.context_bundle.business.project_input == {"project": "政务云"}
    assert state.graph_revision == 1


def test_undeclared_state_write_is_rejected_and_not_committed() -> None:
    class _ViolatingAgent(BaseAgent):
        agent_name = "ViolatingAgent"
        agent_type = "sub_agent"

        def run(self, shared_state) -> AgentResultSchema:
            shared_state.contexts["undeclared_partial"] = "must_not_commit"
            return AgentResultSchema(
                result_id="violating_result",
                task_id=shared_state.task_id,
                run_id=shared_state.run_id,
                agent_name=self.agent_name,
                agent_type=self.agent_type,
                status=ExecutionStatus.SUCCESS,
                result_type="violation",
                result={},
            )

    registry = AgentRegistry()
    registry.register(_ViolatingAgent())
    workflow = WorkflowDefinitionSchema(
        workflow_id="wf_write_contract",
        workflow_name="write contract",
        task_type="test",
        workflow_version="v1",
        steps=[
            WorkflowStepSchema(
                step_id="violate",
                step_name="violate",
                step_type="agent",
                target_name="ViolatingAgent",
                output_keys=["final_result"],
                order=1,
            )
        ],
        created_at=NOW,
    )
    state = _state()

    execution = WorkflowExecutor(registry).execute(workflow, state)

    assert execution.status == ExecutionStatus.FAILED
    assert "undeclared_partial" not in state.contexts
    error = execution.node_results[0].error
    assert error is not None
    assert error.error_code == "STATE_WRITE_CONTRACT_VIOLATION"
    assert execution.node_outputs[0].metadata["write_contract_passed"] is False


def test_native_engine_uses_declared_node_inputs_and_revisioned_outputs() -> None:
    state = _state()
    engine = WorkflowExecutor(_registry())

    execution = engine.execute(_workflow(), state)

    assert isinstance(engine, WorkflowEnginePort)
    assert execution.status == ExecutionStatus.SUCCESS
    assert execution.engine_name == "native_workflow_engine"
    assert execution.final_revision == 2
    assert state.graph_revision == 2
    assert state.completed_node_ids == ["node_1", "node_2"]
    assert len(state.node_history) == 2
    assert state.final_result == {"read": {"project": "政务云"}}

    assert list(execution.node_inputs[0].values) == ["project_input"]
    assert set(execution.node_inputs[1].values) == {
        "normalized_project_input",
        "structured_facts",
    }
    assert execution.node_inputs[1].missing_keys == []
    assert execution.node_outputs[0].state_delta.base_revision == 0
    assert execution.node_outputs[1].state_delta.base_revision == 1
    assert execution.node_outputs[0].state_delta.delta_sha256
    assert execution.node_outputs[1].state_delta.delta_sha256


def test_graph_node_projection_reports_missing_declared_inputs() -> None:
    state = _state()
    workflow = _workflow()
    step = workflow.steps[1]

    node_input = GraphStateProjector().build_node_input(
        workflow=workflow,
        step=step,
        state=state,
    )

    assert set(node_input.values) == {
        "normalized_project_input",
        "structured_facts",
    }
    assert node_input.values["normalized_project_input"] == {}
    assert node_input.missing_keys == []
    assert node_input.input_sha256


def test_engine_failure_stops_before_following_node_and_retains_graph_record() -> None:
    class _FailAgent(BaseAgent):
        agent_name = "FailAgent"
        agent_type = "sub_agent"

        def run(self, shared_state) -> AgentResultSchema:
            return AgentResultSchema(
                result_id="failed",
                task_id=shared_state.task_id,
                run_id=shared_state.run_id,
                agent_name=self.agent_name,
                agent_type=self.agent_type,
                status=ExecutionStatus.FAILED,
                result_type="failure",
                result={},
            )

    registry = AgentRegistry()
    registry.register(_FailAgent())
    registry.register(_ReadAgent())
    workflow = WorkflowDefinitionSchema(
        workflow_id="wf_failure",
        workflow_name="failure",
        task_type="test",
        workflow_version="v1",
        steps=[
            WorkflowStepSchema(
                step_id="fail",
                step_name="fail",
                step_type="agent",
                target_name="FailAgent",
                order=1,
            ),
            WorkflowStepSchema(
                step_id="never",
                step_name="never",
                step_type="agent",
                target_name="ReadAgent",
                order=2,
            ),
        ],
        created_at=NOW,
    )
    state = _state()

    execution = WorkflowExecutor(registry).execute(workflow, state)

    assert execution.status == ExecutionStatus.FAILED
    assert execution.completed_node_ids == ["fail"]
    assert state.completed_node_ids == ["fail"]
    assert state.graph_revision == 1
    assert "never" not in state.workflow_step_states


def test_retryable_node_retries_without_committing_failed_attempt() -> None:
    class _RetryAgent(BaseAgent):
        agent_name = "RetryAgent"
        agent_type = "sub_agent"

        def __init__(self) -> None:
            self.calls = 0

        def run(self, shared_state) -> AgentResultSchema:
            self.calls += 1
            if self.calls == 1:
                return AgentResultSchema(
                    result_id="retry_1",
                    task_id=shared_state.task_id,
                    run_id=shared_state.run_id,
                    agent_name=self.agent_name,
                    agent_type=self.agent_type,
                    status=ExecutionStatus.RETRYABLE_FAILED,
                    result_type="retry",
                    result={},
                )
            shared_state.final_result = {"attempt": self.calls}
            return AgentResultSchema(
                result_id="retry_2",
                task_id=shared_state.task_id,
                run_id=shared_state.run_id,
                agent_name=self.agent_name,
                agent_type=self.agent_type,
                status=ExecutionStatus.SUCCESS,
                result_type="retry",
                result={"attempt": self.calls},
            )

    agent = _RetryAgent()
    registry = AgentRegistry()
    registry.register(agent)
    workflow = WorkflowDefinitionSchema(
        workflow_id="wf_retry",
        workflow_name="retry",
        task_type="test",
        workflow_version="v1",
        steps=[
            WorkflowStepSchema(
                step_id="retry",
                step_name="retry",
                step_type="agent",
                target_name="RetryAgent",
                output_keys=["final_result"],
                max_retries=1,
                order=1,
            )
        ],
        created_at=NOW,
    )
    state = _state()

    execution = WorkflowExecutor(registry).execute(workflow, state)

    assert execution.status == ExecutionStatus.SUCCESS
    assert agent.calls == 2
    assert state.final_result == {"attempt": 2}
    assert state.graph_revision == 1
    assert execution.metadata["retry_count"] == 1
    assert execution.node_outputs[0].metadata["attempt_count"] == 2
    assert execution.node_outputs[0].metadata["commit_decision"] == "success_commit"


def test_timeout_returns_structured_failure_and_late_state_is_not_committed() -> None:
    import time

    class _SlowAgent(BaseAgent):
        agent_name = "SlowAgent"
        agent_type = "sub_agent"

        def run(self, shared_state) -> AgentResultSchema:
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

    registry = AgentRegistry()
    registry.register(_SlowAgent())
    workflow = WorkflowDefinitionSchema(
        workflow_id="wf_timeout",
        workflow_name="timeout",
        task_type="test",
        workflow_version="v1",
        steps=[
            WorkflowStepSchema(
                step_id="slow",
                step_name="slow",
                step_type="agent",
                target_name="SlowAgent",
                output_keys=["final_result"],
                timeout_seconds=0.01,
                order=1,
            )
        ],
        created_at=NOW,
    )
    state = _state()

    execution = WorkflowExecutor(registry).execute(workflow, state)

    assert execution.status == ExecutionStatus.FAILED
    assert state.final_result is None
    error = execution.node_results[0].error
    assert error is not None
    assert error.error_code == "WORKFLOW_NODE_TIMEOUT"
    assert execution.node_outputs[0].metadata["commit_decision"] == "failure_discarded"


def test_business_failure_can_commit_only_declared_partial_outputs() -> None:
    class _BusinessGateAgent(BaseAgent):
        agent_name = "BusinessGateAgent"
        agent_type = "sub_agent"

        def run(self, shared_state) -> AgentResultSchema:
            shared_state.final_result = {"partial": True}
            shared_state.contexts["undeclared"] = "must_fail_contract"
            error = ErrorSchema(
                error_code="BUSINESS_GATE_FAILED",
                error_type="BusinessGateError",
                message="quality gate failed",
                retryable=False,
                failed_node=self.agent_name,
            )
            return AgentResultSchema(
                result_id="business_gate",
                task_id=shared_state.task_id,
                run_id=shared_state.run_id,
                agent_name=self.agent_name,
                agent_type=self.agent_type,
                status=ExecutionStatus.FAILED,
                result_type="business_gate",
                result={},
                error=error,
            )

    registry = AgentRegistry()
    registry.register(_BusinessGateAgent())
    workflow = WorkflowDefinitionSchema(
        workflow_id="wf_business_commit",
        workflow_name="business commit",
        task_type="test",
        workflow_version="v1",
        steps=[
            WorkflowStepSchema(
                step_id="business",
                step_name="business",
                step_type="agent",
                target_name="BusinessGateAgent",
                output_keys=["final_result"],
                commit_policy="allow_partial_on_failure",
                failure_write_paths=["final_result"],
                failure_commit_error_codes=["BUSINESS_GATE_FAILED"],
                order=1,
            )
        ],
        created_at=NOW,
    )
    state = _state()

    execution = WorkflowExecutor(registry).execute(workflow, state)

    # Undeclared writes fail before commit, even when partial failure commit is enabled.
    assert execution.status == ExecutionStatus.FAILED
    assert state.final_result is None
    assert "undeclared" not in state.contexts
    assert execution.node_results[0].error.error_code == "STATE_WRITE_CONTRACT_VIOLATION"


def test_allowed_business_failure_preserves_partial_result() -> None:
    class _BusinessGateAgent(BaseAgent):
        agent_name = "AllowedBusinessGateAgent"
        agent_type = "sub_agent"

        def run(self, shared_state) -> AgentResultSchema:
            shared_state.final_result = {"partial": True}
            error = ErrorSchema(
                error_code="BUSINESS_GATE_FAILED",
                error_type="BusinessGateError",
                message="quality gate failed",
                retryable=False,
                failed_node=self.agent_name,
            )
            return AgentResultSchema(
                result_id="business_gate_allowed",
                task_id=shared_state.task_id,
                run_id=shared_state.run_id,
                agent_name=self.agent_name,
                agent_type=self.agent_type,
                status=ExecutionStatus.FAILED,
                result_type="business_gate",
                result={},
                error=error,
            )

    registry = AgentRegistry()
    registry.register(_BusinessGateAgent())
    workflow = WorkflowDefinitionSchema(
        workflow_id="wf_business_commit_allowed",
        workflow_name="business commit allowed",
        task_type="test",
        workflow_version="v1",
        steps=[
            WorkflowStepSchema(
                step_id="business",
                step_name="business",
                step_type="agent",
                target_name="AllowedBusinessGateAgent",
                output_keys=["final_result"],
                commit_policy="allow_partial_on_failure",
                failure_write_paths=["final_result"],
                failure_commit_error_codes=["BUSINESS_GATE_FAILED"],
                order=1,
            )
        ],
        created_at=NOW,
    )
    state = _state()

    execution = WorkflowExecutor(registry).execute(workflow, state)

    assert execution.status == ExecutionStatus.FAILED
    assert state.final_result == {"partial": True}
    assert execution.node_outputs[0].metadata["commit_decision"] == "failure_commit_partial"


def test_explicit_failure_route_recovers_as_partial_success() -> None:
    class _RouteFailAgent(BaseAgent):
        agent_name = "RouteFailAgent"
        agent_type = "sub_agent"

        def run(self, shared_state) -> AgentResultSchema:
            return AgentResultSchema(
                result_id="route_fail",
                task_id=shared_state.task_id,
                run_id=shared_state.run_id,
                agent_name=self.agent_name,
                agent_type=self.agent_type,
                status=ExecutionStatus.FAILED,
                result_type="route_fail",
                result={},
            )

    class _RecoveryAgent(BaseAgent):
        agent_name = "RecoveryAgent"
        agent_type = "sub_agent"

        def run(self, shared_state) -> AgentResultSchema:
            shared_state.final_result = {"recovered": True}
            return AgentResultSchema(
                result_id="recovered",
                task_id=shared_state.task_id,
                run_id=shared_state.run_id,
                agent_name=self.agent_name,
                agent_type=self.agent_type,
                status=ExecutionStatus.SUCCESS,
                result_type="recovery",
                result={},
            )

    registry = AgentRegistry()
    registry.register(_RouteFailAgent())
    registry.register(_RecoveryAgent())
    workflow = WorkflowDefinitionSchema(
        workflow_id="wf_route_recovery",
        workflow_name="route recovery",
        task_type="test",
        workflow_version="v1",
        steps=[
            WorkflowStepSchema(
                step_id="first",
                step_name="first",
                step_type="agent",
                target_name="RouteFailAgent",
                on_failure="recover",
                order=1,
            ),
            WorkflowStepSchema(
                step_id="recover",
                step_name="recover",
                step_type="agent",
                target_name="RecoveryAgent",
                output_keys=["final_result"],
                order=2,
            ),
        ],
        created_at=NOW,
    )
    state = _state()

    execution = WorkflowExecutor(registry).execute(workflow, state)

    assert execution.status == ExecutionStatus.PARTIAL_SUCCESS
    assert state.final_result == {"recovered": True}
    assert execution.completed_node_ids == ["first", "recover"]
    assert execution.metadata["workflow_complete"] is True
    assert execution.metadata["recovered_failure_count"] == 1
