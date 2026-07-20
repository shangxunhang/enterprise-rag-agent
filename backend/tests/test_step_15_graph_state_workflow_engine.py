# =============================================================================
# 中文阅读说明：自动化测试模块，用于验证主链、边界条件和回归行为。
# 主要定义：_WriteAgent、_ReadAgent、_state、_workflow、_registry、test_legacy_agent_executes_on_copy_and_only_delta_commit_mutates_state、test_undeclared_state_write_is_rejected_and_not_committed、test_langgraph_engine_uses_declared_node_inputs_and_revisioned_outputs、test_graph_node_projection_reports_missing_declared_inputs、test_engine_failure_stops_before_following_node_and_retains_graph_record等。建议先从公开入口函数开始，再沿调用关系向下阅读。
# =============================================================================
from __future__ import annotations

from agent.agent_registry import AgentRegistry
from agent.base_agent import BaseAgent
from agent.runtime.graph_state import GraphStateSchema
from agent.runtime.graph_state_ops import GraphStateApplier, GraphStateProjector
from agent.runtime.langgraph_workflow_engine import LangGraphWorkflowEngine
from agent.runtime.node_adapter import AgentNodeAdapter
from agent.runtime.workflow_schema import WorkflowDefinitionSchema, WorkflowStepSchema
from contracts.workflow_engine import WorkflowEnginePort
from schemas.agent import AgentResultSchema
from schemas.common import ErrorSchema
from schemas.context import ContextBundleSchema, TaskContextSchema, UserContextSchema
from schemas.status import ExecutionStatus

NOW = "2026-07-17T00:00:00+00:00"


# 阅读注释（类）：封装 write Agent，负责接收状态、调用工具或服务并返回统一 Agent 结果。
class _WriteAgent(BaseAgent):
    """封装 write Agent，负责接收状态、调用工具或服务并返回统一 Agent 结果。"""
    agent_name = "WriteAgent"
    agent_type = "sub_agent"

    # 阅读注释（函数）：执行 _WriteAgent 的主流程。
    def run(self, shared_state) -> AgentResultSchema:
        """执行 _WriteAgent 的主流程。

        参数:
            shared_state: shared 状态，具体约束请结合类型标注和调用方确认。

        返回:
            AgentResultSchema

        阅读提示:
            主要直接调用：shared_state.structured_facts.append, AgentResultSchema。
        """
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


# 阅读注释（类）：封装 read Agent，负责接收状态、调用工具或服务并返回统一 Agent 结果。
class _ReadAgent(BaseAgent):
    """封装 read Agent，负责接收状态、调用工具或服务并返回统一 Agent 结果。"""
    agent_name = "ReadAgent"
    agent_type = "sub_agent"

    # 阅读注释（函数）：执行 _ReadAgent 的主流程。
    def run(self, shared_state) -> AgentResultSchema:
        """执行 _ReadAgent 的主流程。

        参数:
            shared_state: shared 状态，具体约束请结合类型标注和调用方确认。

        返回:
            AgentResultSchema

        阅读提示:
            主要直接调用：AgentResultSchema。
        """
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


# 阅读注释（函数）：处理 状态 相关逻辑。
def _state() -> GraphStateSchema:
    """处理 状态 相关逻辑。

    返回:
        GraphStateSchema

    阅读提示:
        主要直接调用：GraphStateSchema, ContextBundleSchema, UserContextSchema, TaskContextSchema。
    """
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


# 阅读注释（函数）：处理 工作流 相关逻辑。
def _workflow() -> WorkflowDefinitionSchema:
    """处理 工作流 相关逻辑。

    返回:
        WorkflowDefinitionSchema

    阅读提示:
        主要直接调用：WorkflowDefinitionSchema, WorkflowStepSchema。
    """
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


# 阅读注释（函数）：处理 注册表 相关逻辑。
def _registry() -> AgentRegistry:
    """处理 注册表 相关逻辑。

    返回:
        AgentRegistry

    阅读提示:
        主要直接调用：AgentRegistry, registry.register, _WriteAgent, _ReadAgent。
    """
    registry = AgentRegistry()
    registry.register(_WriteAgent())
    registry.register(_ReadAgent())
    return registry


# 阅读注释（函数）：处理 测试 legacy Agent executes on copy and only delta commit mutates 状态 相关逻辑。
def test_legacy_agent_executes_on_copy_and_only_delta_commit_mutates_state() -> None:
    """处理 测试 legacy Agent executes on copy and only delta commit mutates 状态 相关逻辑。

    返回:
        None

    阅读提示:
        主要直接调用：_state, _workflow, _registry, WorkflowStepDispatcher, AgentStepHandler, build_node_input, GraphStateProjector, execute。
    """
    state = _state()
    workflow = _workflow()
    step = workflow.steps[0]
    registry = _registry()
    node_input = GraphStateProjector().build_node_input(
        workflow=workflow,
        step=step,
        state=state,
    )

    output = AgentNodeAdapter(registry).execute(
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


# 阅读注释（函数）：处理 测试 undeclared 状态 write is rejected and not committed 相关逻辑。
def test_undeclared_state_write_is_rejected_and_not_committed() -> None:
    """处理 测试 undeclared 状态 write is rejected and not committed 相关逻辑。

    返回:
        None

    阅读提示:
        主要直接调用：AgentRegistry, registry.register, _ViolatingAgent, WorkflowDefinitionSchema, WorkflowStepSchema, _state, execute, LangGraphWorkflowEngine。
    """
    # 阅读注释（类）：封装 violating Agent，负责接收状态、调用工具或服务并返回统一 Agent 结果。
    class _ViolatingAgent(BaseAgent):
        """封装 violating Agent，负责接收状态、调用工具或服务并返回统一 Agent 结果。"""
        agent_name = "ViolatingAgent"
        agent_type = "sub_agent"

        # 阅读注释（函数）：执行 _ViolatingAgent 的主流程。
        def run(self, shared_state) -> AgentResultSchema:
            """执行 _ViolatingAgent 的主流程。

            参数:
                shared_state: shared 状态，具体约束请结合类型标注和调用方确认。

            返回:
                AgentResultSchema

            阅读提示:
                主要直接调用：AgentResultSchema。
            """
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

    execution = LangGraphWorkflowEngine(registry).execute(workflow, state)

    assert execution.status == ExecutionStatus.FAILED
    assert "undeclared_partial" not in state.contexts
    error = execution.node_results[0].error
    assert error is not None
    assert error.error_code == "STATE_WRITE_CONTRACT_VIOLATION"
    assert execution.node_outputs[0].metadata["write_contract_passed"] is False


# 阅读注释（函数）：处理 测试 LangGraph engine uses declared node inputs and revisioned outputs 相关逻辑。
def test_langgraph_engine_uses_declared_node_inputs_and_revisioned_outputs() -> None:
    """处理 测试 LangGraph engine uses declared node inputs and revisioned outputs 相关逻辑。

    返回:
        None

    阅读提示:
        主要直接调用：_state, LangGraphWorkflowEngine, _registry, engine.execute, _workflow, isinstance, len, list。
    """
    state = _state()
    engine = LangGraphWorkflowEngine(_registry())

    execution = engine.execute(_workflow(), state)

    assert isinstance(engine, WorkflowEnginePort)
    assert execution.status == ExecutionStatus.SUCCESS
    assert execution.engine_name == "langgraph_workflow_engine"
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


# 阅读注释（函数）：处理 测试 graph node projection reports missing declared inputs 相关逻辑。
def test_graph_node_projection_reports_missing_declared_inputs() -> None:
    """处理 测试 graph node projection reports missing declared inputs 相关逻辑。

    返回:
        None

    阅读提示:
        主要直接调用：_state, _workflow, build_node_input, GraphStateProjector, set。
    """
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


# 阅读注释（函数）：处理 测试 engine failure stops before following node and retains graph 记录 相关逻辑。
def test_engine_failure_stops_before_following_node_and_retains_graph_record() -> None:
    """处理 测试 engine failure stops before following node and retains graph 记录 相关逻辑。

    返回:
        None

    阅读提示:
        主要直接调用：AgentRegistry, registry.register, _FailAgent, _ReadAgent, WorkflowDefinitionSchema, WorkflowStepSchema, _state, execute。
    """
    # 阅读注释（类）：封装 fail Agent，负责接收状态、调用工具或服务并返回统一 Agent 结果。
    class _FailAgent(BaseAgent):
        """封装 fail Agent，负责接收状态、调用工具或服务并返回统一 Agent 结果。"""
        agent_name = "FailAgent"
        agent_type = "sub_agent"

        # 阅读注释（函数）：执行 _FailAgent 的主流程。
        def run(self, shared_state) -> AgentResultSchema:
            """执行 _FailAgent 的主流程。

            参数:
                shared_state: shared 状态，具体约束请结合类型标注和调用方确认。

            返回:
                AgentResultSchema

            阅读提示:
                主要直接调用：AgentResultSchema。
            """
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

    execution = LangGraphWorkflowEngine(registry).execute(workflow, state)

    assert execution.status == ExecutionStatus.FAILED
    assert execution.completed_node_ids == ["fail"]
    assert state.completed_node_ids == ["fail"]
    assert state.graph_revision == 1
    assert "never" not in state.workflow_step_states


# 阅读注释（函数）：处理 测试 retryable node retries without committing failed attempt 相关逻辑。
def test_retryable_node_retries_without_committing_failed_attempt() -> None:
    """处理 测试 retryable node retries without committing failed attempt 相关逻辑。

    返回:
        None

    阅读提示:
        主要直接调用：_RetryAgent, AgentRegistry, registry.register, WorkflowDefinitionSchema, WorkflowStepSchema, _state, execute, LangGraphWorkflowEngine。
    """
    # 阅读注释（类）：封装 retry Agent，负责接收状态、调用工具或服务并返回统一 Agent 结果。
    class _RetryAgent(BaseAgent):
        """封装 retry Agent，负责接收状态、调用工具或服务并返回统一 Agent 结果。"""
        agent_name = "RetryAgent"
        agent_type = "sub_agent"

        # 阅读注释（函数）：初始化 _RetryAgent，保存运行所需的依赖、配置或状态。
        def __init__(self) -> None:
            """初始化 _RetryAgent，保存运行所需的依赖、配置或状态。

            返回:
                None
            """
            self.calls = 0

        # 阅读注释（函数）：执行 _RetryAgent 的主流程。
        def run(self, shared_state) -> AgentResultSchema:
            """执行 _RetryAgent 的主流程。

            参数:
                shared_state: shared 状态，具体约束请结合类型标注和调用方确认。

            返回:
                AgentResultSchema

            阅读提示:
                主要直接调用：AgentResultSchema。
            """
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

    execution = LangGraphWorkflowEngine(registry).execute(workflow, state)

    assert execution.status == ExecutionStatus.SUCCESS
    assert agent.calls == 2
    assert state.final_result == {"attempt": 2}
    assert state.graph_revision == 1
    assert execution.metadata["retry_count"] == 1
    assert execution.node_outputs[0].metadata["attempt_count"] == 2
    assert execution.node_outputs[0].metadata["commit_decision"] == "success_commit"


# 阅读注释（函数）：处理 测试 timeout returns structured failure and late 状态 is not committed 相关逻辑。
def test_timeout_returns_structured_failure_and_late_state_is_not_committed() -> None:
    """处理 测试 timeout returns structured failure and late 状态 is not committed 相关逻辑。

    返回:
        None

    阅读提示:
        主要直接调用：AgentRegistry, registry.register, _SlowAgent, WorkflowDefinitionSchema, WorkflowStepSchema, _state, execute, LangGraphWorkflowEngine。
    """
    import time

    # 阅读注释（类）：封装 slow Agent，负责接收状态、调用工具或服务并返回统一 Agent 结果。
    class _SlowAgent(BaseAgent):
        """封装 slow Agent，负责接收状态、调用工具或服务并返回统一 Agent 结果。"""
        agent_name = "SlowAgent"
        agent_type = "sub_agent"

        # 阅读注释（函数）：执行 _SlowAgent 的主流程。
        def run(self, shared_state) -> AgentResultSchema:
            """执行 _SlowAgent 的主流程。

            参数:
                shared_state: shared 状态，具体约束请结合类型标注和调用方确认。

            返回:
                AgentResultSchema

            阅读提示:
                主要直接调用：time.sleep, AgentResultSchema。
            """
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

    execution = LangGraphWorkflowEngine(registry).execute(workflow, state)

    assert execution.status == ExecutionStatus.FAILED
    assert state.final_result is None
    error = execution.node_results[0].error
    assert error is not None
    assert error.error_code == "WORKFLOW_NODE_TIMEOUT"
    assert execution.node_outputs[0].metadata["commit_decision"] == "failure_discarded"


# 阅读注释（函数）：处理 测试 business failure can commit only declared partial outputs 相关逻辑。
def test_business_failure_can_commit_only_declared_partial_outputs() -> None:
    """处理 测试 business failure can commit only declared partial outputs 相关逻辑。

    返回:
        None

    阅读提示:
        主要直接调用：AgentRegistry, registry.register, _BusinessGateAgent, WorkflowDefinitionSchema, WorkflowStepSchema, _state, execute, LangGraphWorkflowEngine。
    """
    # 阅读注释（类）：封装 business gate Agent，负责接收状态、调用工具或服务并返回统一 Agent 结果。
    class _BusinessGateAgent(BaseAgent):
        """封装 business gate Agent，负责接收状态、调用工具或服务并返回统一 Agent 结果。"""
        agent_name = "BusinessGateAgent"
        agent_type = "sub_agent"

        # 阅读注释（函数）：执行 _BusinessGateAgent 的主流程。
        def run(self, shared_state) -> AgentResultSchema:
            """执行 _BusinessGateAgent 的主流程。

            参数:
                shared_state: shared 状态，具体约束请结合类型标注和调用方确认。

            返回:
                AgentResultSchema

            阅读提示:
                主要直接调用：ErrorSchema, AgentResultSchema。
            """
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

    execution = LangGraphWorkflowEngine(registry).execute(workflow, state)

    # Undeclared writes fail before commit, even when partial failure commit is enabled.
    assert execution.status == ExecutionStatus.FAILED
    assert state.final_result is None
    assert "undeclared" not in state.contexts
    assert execution.node_results[0].error.error_code == "STATE_WRITE_CONTRACT_VIOLATION"


# 阅读注释（函数）：处理 测试 allowed business failure preserves partial 结果 相关逻辑。
def test_allowed_business_failure_preserves_partial_result() -> None:
    """处理 测试 allowed business failure preserves partial 结果 相关逻辑。

    返回:
        None

    阅读提示:
        主要直接调用：AgentRegistry, registry.register, _BusinessGateAgent, WorkflowDefinitionSchema, WorkflowStepSchema, _state, execute, LangGraphWorkflowEngine。
    """
    # 阅读注释（类）：封装 business gate Agent，负责接收状态、调用工具或服务并返回统一 Agent 结果。
    class _BusinessGateAgent(BaseAgent):
        """封装 business gate Agent，负责接收状态、调用工具或服务并返回统一 Agent 结果。"""
        agent_name = "AllowedBusinessGateAgent"
        agent_type = "sub_agent"

        # 阅读注释（函数）：执行 _BusinessGateAgent 的主流程。
        def run(self, shared_state) -> AgentResultSchema:
            """执行 _BusinessGateAgent 的主流程。

            参数:
                shared_state: shared 状态，具体约束请结合类型标注和调用方确认。

            返回:
                AgentResultSchema

            阅读提示:
                主要直接调用：ErrorSchema, AgentResultSchema。
            """
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

    execution = LangGraphWorkflowEngine(registry).execute(workflow, state)

    assert execution.status == ExecutionStatus.FAILED
    assert state.final_result == {"partial": True}
    assert execution.node_outputs[0].metadata["commit_decision"] == "failure_commit_partial"


# 阅读注释（函数）：处理 测试 explicit failure 路由 recovers as partial success 相关逻辑。
def test_explicit_failure_route_recovers_as_partial_success() -> None:
    """处理 测试 explicit failure 路由 recovers as partial success 相关逻辑。

    返回:
        None

    阅读提示:
        主要直接调用：AgentRegistry, registry.register, _RouteFailAgent, _RecoveryAgent, WorkflowDefinitionSchema, WorkflowStepSchema, _state, execute。
    """
    # 阅读注释（类）：封装 路由 fail Agent，负责接收状态、调用工具或服务并返回统一 Agent 结果。
    class _RouteFailAgent(BaseAgent):
        """封装 路由 fail Agent，负责接收状态、调用工具或服务并返回统一 Agent 结果。"""
        agent_name = "RouteFailAgent"
        agent_type = "sub_agent"

        # 阅读注释（函数）：执行 _RouteFailAgent 的主流程。
        def run(self, shared_state) -> AgentResultSchema:
            """执行 _RouteFailAgent 的主流程。

            参数:
                shared_state: shared 状态，具体约束请结合类型标注和调用方确认。

            返回:
                AgentResultSchema

            阅读提示:
                主要直接调用：AgentResultSchema。
            """
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

    # 阅读注释（类）：封装 recovery Agent，负责接收状态、调用工具或服务并返回统一 Agent 结果。
    class _RecoveryAgent(BaseAgent):
        """封装 recovery Agent，负责接收状态、调用工具或服务并返回统一 Agent 结果。"""
        agent_name = "RecoveryAgent"
        agent_type = "sub_agent"

        # 阅读注释（函数）：执行 _RecoveryAgent 的主流程。
        def run(self, shared_state) -> AgentResultSchema:
            """执行 _RecoveryAgent 的主流程。

            参数:
                shared_state: shared 状态，具体约束请结合类型标注和调用方确认。

            返回:
                AgentResultSchema

            阅读提示:
                主要直接调用：AgentResultSchema。
            """
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

    execution = LangGraphWorkflowEngine(registry).execute(workflow, state)

    assert execution.status == ExecutionStatus.PARTIAL_SUCCESS
    assert state.final_result == {"recovered": True}
    assert execution.completed_node_ids == ["first", "recover"]
    assert execution.metadata["workflow_complete"] is True
    assert execution.metadata["recovered_failure_count"] == 1
