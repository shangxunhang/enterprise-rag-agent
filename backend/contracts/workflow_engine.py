"""定义与具体工作流框架无关的执行引擎端口。

当前主要实现由 LangGraph 运行时提供，但应用层和业务工作流只依赖本模块中的
协议、能力声明和标准结果模型。这样可在不修改业务编排的前提下替换执行引擎，
也能显式判断恢复、检查、取消和流式事件等可选能力是否受支持。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Iterable, Protocol, runtime_checkable

from agent.runtime.graph_state import GraphStateSchema
from agent.runtime.workflow_schema import WorkflowDefinitionSchema
from schemas.graph import WorkflowEngineResultSchema


class UnsupportedWorkflowCapabilityError(RuntimeError):
    """表示调用方请求了当前执行引擎明确不支持的可选能力。"""


@dataclass(frozen=True)
class WorkflowEngineCapabilities:
    """声明某个工作流引擎实现支持哪些可选控制能力。

    Attributes:
        resume: 是否能从持久化检查点继续执行。
        inspect: 是否能查询指定运行的当前快照或历史状态。
        cancel: 是否能把正在运行或待恢复的工作流推进为取消态。
        stream_events: 是否提供同步事件流。
        async_stream_events: 是否提供异步事件流。

    所有字段默认关闭，防止调用方把未声明能力误认为可用。
    """

    resume: bool = False
    inspect: bool = False
    cancel: bool = False
    stream_events: bool = False
    async_stream_events: bool = False


@runtime_checkable
class WorkflowEnginePort(Protocol):
    """声明业务工作流执行引擎必须提供的公共接口。

    Attributes:
        engine_name: 引擎实现的稳定名称，用于装配、日志和运行结果归因。
        engine_version: 当前实现版本，用于兼容性检查和问题定位。
        capabilities: 可选控制能力的显式声明。
    """

    engine_name: str
    engine_version: str
    capabilities: WorkflowEngineCapabilities

    def execute(
        self,
        workflow: WorkflowDefinitionSchema,
        graph_state: GraphStateSchema,
    ) -> WorkflowEngineResultSchema:
        """从给定规范状态开始执行一次完整工作流。

        Args:
            workflow: 已编译前的框架无关工作流定义。
            graph_state: 本次执行使用的规范图状态。

        Returns:
            包含终态、输出、错误和运行元数据的标准引擎结果。
        """

        ...

    def resume(
        self,
        workflow: WorkflowDefinitionSchema,
        graph_state: GraphStateSchema,
        resume_value: dict[str, Any],
    ) -> WorkflowEngineResultSchema:
        """向中断点提交恢复值并继续执行工作流。

        Args:
            workflow: 与原运行兼容的工作流定义。
            graph_state: 用于身份校验和恢复上下文补充的规范状态。
            resume_value: 提交给中断节点的人工决定或外部恢复数据。

        Returns:
            恢复执行后的标准引擎结果。

        Raises:
            UnsupportedWorkflowCapabilityError: 引擎未声明 ``resume`` 能力时抛出。
        """

        ...

    def inspect(
        self,
        workflow: WorkflowDefinitionSchema,
        *,
        run_id: str,
    ) -> dict[str, Any]:
        """查询指定运行的持久化状态或检查点信息。

        Args:
            workflow: 用于确定状态图和检查点命名空间的工作流定义。
            run_id: 要检查的运行标识。

        Returns:
            与具体引擎有关但可序列化的检查结果。
        """

        ...

    def cancel(
        self,
        workflow: WorkflowDefinitionSchema,
        graph_state: GraphStateSchema,
        *,
        reason: str = "workflow_cancelled",
    ) -> WorkflowEngineResultSchema:
        """请求取消工作流并返回规范化取消结果。

        Args:
            workflow: 被取消运行对应的工作流定义。
            graph_state: 当前规范状态，用于写入取消信息和构造终态结果。
            reason: 供日志、状态记录和调用方展示使用的取消原因。

        Returns:
            状态为取消的标准引擎结果。
        """

        ...

    def stream_events(
        self,
        workflow: WorkflowDefinitionSchema,
        graph_state: GraphStateSchema,
        *,
        resume_value: dict[str, Any] | None = None,
        runnable_config: dict[str, Any] | None = None,
    ) -> Iterable[Any]:
        """以同步迭代器形式输出工作流运行事件。

        Args:
            workflow: 要执行或恢复的工作流定义。
            graph_state: 初始规范状态。
            resume_value: 非空时表示从中断点恢复并提交该值。
            runnable_config: 传递给底层框架的运行配置，例如线程标识和回调。

        Returns:
            按底层引擎产生顺序输出的事件迭代器。
        """

        ...
