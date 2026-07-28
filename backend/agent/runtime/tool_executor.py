"""统一执行已注册工具，并记录结构化失败和 Trace v2 span。

``ToolExecutor`` 是 Agent 与具体工具之间的运行边界：它根据 ``tool_name`` 从注册表
取得实例，执行标准 ``ToolCallSchema``，把未处理异常归一化为 ``ToolResultSchema``，
并为开始、结束、耗时和数据血缘生成统一轨迹。工具自身只负责业务能力，不重复实现
这些横切逻辑。
"""

from __future__ import annotations

import traceback
from typing import Any

from contracts.observability import TraceSink
from core.error_factory import ErrorFactory
from core.retry_policy import RetryDecisionPolicy, classify_failure
from observability.trace_context import activate_span, current_span, new_span
from observability.trace_summary import (
    extract_tool_lineage,
    tool_call_summary,
    tool_result_summary,
)
from schemas.status import ExecutionStatus
from schemas.tool import ToolCallSchema, ToolResultSchema
from tools.tool_registry import ToolRegistry


class ToolExecutor:
    """集中处理工具查找、执行、错误归一化、耗时补全和轨迹记录。

    Attributes:
        tool_registry: 保存可调用工具实例的注册表。
        run_trace_recorder: 可选结构化轨迹接收器；为空时不产生轨迹副作用。
        error_factory: 把未处理异常转换为统一 ``ErrorSchema`` 的工厂。
        retry_policy: 根据规范失败类别判断当前工具失败是否允许重试。
    """

    def __init__(
        self,
        tool_registry: ToolRegistry,
        run_trace_recorder: TraceSink | None = None,
        *,
        error_factory: ErrorFactory | None = None,
        retry_policy: RetryDecisionPolicy | None = None,
    ) -> None:
        """创建工具执行器并补齐默认横切依赖。

        Args:
            tool_registry: 已完成装配的工具注册表。
            run_trace_recorder: 可选 Trace 输出端口。
            error_factory: 自定义错误工厂；未提供时使用默认实现。
            retry_policy: 自定义重试决策策略；未提供时使用默认策略。
        """

        # 第一步：保存工具注册表；执行器不负责创建或销毁工具实例。
        self.tool_registry = tool_registry

        # 第二步：保存可选轨迹端口，使纯内存测试可以关闭持久化副作用。
        self.run_trace_recorder = run_trace_recorder

        # 第三步：补齐统一错误工厂，保证异常路径也返回完整结构化错误。
        self.error_factory = error_factory or ErrorFactory()

        # 第四步：补齐重试策略；失败事实分类与是否重试保持两个独立阶段。
        self.retry_policy = retry_policy or RetryDecisionPolicy()

    def _trace(self, **kwargs: Any) -> None:
        """在已配置轨迹接收器时转发一个结构化事件。

        Args:
            **kwargs: 与 ``TraceSink.record`` 契约一致的事件字段。

        该辅助方法只消除重复空值判断，不捕获 sink 异常；轨迹写入失败是否影响主链
        由具体 sink 和更外层运行策略决定。
        """

        if self.run_trace_recorder is not None:
            self.run_trace_recorder.record(**kwargs)

    def execute(self, tool_call: ToolCallSchema) -> ToolResultSchema:
        """执行一次标准工具调用并返回规范结果。

        Args:
            tool_call: 包含任务身份、工具名称、参数和调用上下文的标准调用对象。

        Returns:
            工具自身返回的标准结果，或由执行器根据未处理异常构造的失败结果。
            返回前一定会补齐 ``latency_ms``。
        """

        # 第一步：为本次工具调用创建 client 类型 span，并继承当前活动父 span。
        span = new_span(
            run_id=tool_call.run_id,
            span_name=f"tool:{tool_call.tool_name}",
            span_kind="client",
            parent=current_span(),
        )

        # 第二步：记录工具开始事件。完整调用载荷用于审计，摘要用于快速查询和展示。
        self._trace(
            task_id=tool_call.task_id,
            run_id=tool_call.run_id,
            event_type="tool_started",
            component_type="tool",
            component_name=tool_call.tool_name,
            step_id=tool_call.step_id,
            step_name=tool_call.step_name,
            call_id=tool_call.tool_call_id,
            caller=tool_call.caller_agent,
            callee=tool_call.tool_name,
            tool_name=tool_call.tool_name,
            payload={"tool_call": tool_call.model_dump(mode="json")},
            input_summary=tool_call_summary(tool_call),
            status=ExecutionStatus.RUNNING.value,
            phase="start",
            trace_id=span.trace_id,
            span_id=span.span_id,
            parent_span_id=span.parent_span_id,
            span_name=span.span_name,
            span_kind=span.span_kind,
            started_at=span.started_at,
            tags=["trace_v2", "tool"],
        )

        # 第三步：激活当前 span，使工具内部产生的模型、检索或子工具调用自动关联。
        with activate_span(span):
            try:
                # 第四步：根据稳定名称从注册表取得已装配工具；未注册会进入统一异常路径。
                tool = self.tool_registry.get(tool_call.tool_name)

                # 第五步：执行工具标准入口。工具应自行返回 ``ToolResultSchema``。
                result = tool.run(tool_call)
            except Exception as exc:
                # 第六步：把真实异常映射为规范失败类别，不直接根据异常文本猜测重试。
                failure_category = classify_failure(exc)

                # 第七步：由独立策略判断该类别是否具备自动重试价值。
                retryable = self.retry_policy.should_retry(failure_category)

                # 第八步：构造包含来源、堆栈和重试属性的统一结构化错误。
                error = self.error_factory.create(
                    error_code="TOOL_EXECUTION_EXCEPTION",
                    error_type=exc.__class__.__name__,
                    message=str(exc),
                    user_visible_message=f"工具 {tool_call.tool_name} 执行失败。",
                    recoverable=retryable,
                    retryable=retryable,
                    failure_category=failure_category,
                    failed_node=tool_call.step_id or tool_call.tool_name,
                    component="ToolExecutor",
                    tool_name=tool_call.tool_name,
                    step_name=tool_call.step_name,
                    stack_trace=traceback.format_exc(),
                )

                # 第九步：把未处理异常归一化为标准工具失败结果，避免异常对象穿透 Agent。
                result = ToolResultSchema(
                    tool_call_id=tool_call.tool_call_id,
                    task_id=tool_call.task_id,
                    run_id=tool_call.run_id,
                    tool_name=tool_call.tool_name,
                    success=False,
                    status=(
                        ExecutionStatus.RETRYABLE_FAILED
                        if retryable
                        else ExecutionStatus.FAILED
                    ),
                    result={},
                    error=error,
                    error_message=error.message,
                    created_at=tool_call.created_at,
                )

        # 第十步：优先保留工具自己测得的耗时；缺失时使用外层 span 覆盖完整调用耗时。
        latency_ms = result.latency_ms
        if latency_ms is None:
            latency_ms = span.latency_ms()
        result.latency_ms = latency_ms

        # 第十一步：生成用于轨迹查询的有限结果摘要和工具数据血缘。
        summary = tool_result_summary(result)
        lineage = extract_tool_lineage(result)
        error = result.error

        # 第十二步：使用与开始事件相同的 trace/span 标识记录工具终态。
        self._trace(
            task_id=tool_call.task_id,
            run_id=tool_call.run_id,
            event_type="tool_finished",
            component_type="tool",
            component_name=tool_call.tool_name,
            step_id=tool_call.step_id,
            step_name=tool_call.step_name,
            call_id=tool_call.tool_call_id,
            caller=tool_call.caller_agent,
            callee=tool_call.tool_name,
            tool_name=tool_call.tool_name,
            payload={"tool_result": result.model_dump(mode="json")},
            output_summary=summary,
            lineage=lineage,
            status=result.status.value,
            error_message=(error.message if error else result.error_message),
            latency_ms=latency_ms,
            phase=("error" if error else "end"),
            trace_id=span.trace_id,
            span_id=span.span_id,
            parent_span_id=span.parent_span_id,
            span_name=span.span_name,
            span_kind=span.span_kind,
            started_at=span.started_at,
            tags=["trace_v2", "tool"],
        )

        # 第十三步：向 Agent 返回统一结果，不暴露工具实例或执行器内部状态。
        return result
