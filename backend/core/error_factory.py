"""集中构造跨模块传递的结构化运行错误。

所有调用方通过 ``ErrorFactory`` 统一填充错误来源、失败类别、时间和扩展字段，避免
各模块手工拼装出不一致的 ``ErrorSchema``。失败类别描述事实，重试和交付动作仍由
上层策略决定。
"""

from __future__ import annotations

from typing import Any, Dict, Optional

from core.runtime.clock import Clock, SystemClock
from schemas.common import ErrorSchema, ErrorSourceSchema
from schemas.failure import FailureCategory


# 只登记语义不依赖底层异常细节、在全项目中稳定不变的错误码映射。
# MODEL_GATEWAY_CALL_FAILED 等通用包装码必须由最接近事实来源的调用方显式提供类别，
# 不能在此处根据名字猜测可用性、超时或配置错误。
FIXED_FAILURE_CATEGORY_BY_ERROR_CODE: dict[str, FailureCategory] = {
    # 工作流定义或路由形成非法循环。
    "WORKFLOW_ROUTING_CYCLE": FailureCategory.CONFIGURATION,
    # 工作流结束但未满足完整结果契约。
    "WORKFLOW_INCOMPLETE": FailureCategory.VALIDATION,
    # 节点收到明确取消信号。
    "WORKFLOW_NODE_CANCELLED": FailureCategory.CANCELLED,
    # 节点尝试写入未授权状态路径或提交非法增量。
    "STATE_WRITE_CONTRACT_VIOLATION": FailureCategory.VALIDATION,
    # 节点运行结束却没有产生标准结果，通常可由重新执行恢复。
    "WORKFLOW_NODE_NO_RESULT": FailureCategory.TRANSIENT,
    # 节点单次尝试超过规定时间。
    "WORKFLOW_NODE_TIMEOUT": FailureCategory.TIMEOUT,
    # 章节产物未通过不可降级的业务质量门禁。
    "SECTION_HARD_GATE_FAILED": FailureCategory.BUSINESS_QUALITY,
    # RAG 工具边界无法提供检索服务。
    "RAG_TOOL_FAILED": FailureCategory.AVAILABILITY,
}


class ErrorFactory:
    """根据统一规则创建 ``ErrorSchema``。

    Attributes:
        clock: 为错误记录生成创建时间的可替换时钟。
    """

    def __init__(self, clock: Optional[Clock] = None) -> None:
        """创建错误工厂。

        Args:
            clock: 自定义时钟；未提供时使用系统 UTC 时钟。
        """

        # 注入时钟后，测试可以稳定断言错误创建时间。
        self.clock = clock or SystemClock()

    def create(
        self,
        *,
        error_code: str,
        error_type: str,
        message: str,
        component: str,
        user_visible_message: Optional[str] = None,
        recoverable: bool = False,
        retryable: bool = False,
        failure_category: FailureCategory | str | None = None,
        requires_human: bool = False,
        requires_new_input: bool = False,
        failed_node: Optional[str] = None,
        agent_name: Optional[str] = None,
        tool_name: Optional[str] = None,
        step_name: Optional[str] = None,
        stack_trace: Optional[str] = None,
        details: Optional[Dict[str, Any]] = None,
        debug_info: Optional[Dict[str, Any]] = None,
        extra: Optional[Dict[str, Any]] = None,
    ) -> ErrorSchema:
        """校验失败类别并创建完整结构化错误。

        Args:
            error_code: 稳定机器错误码，用于策略、统计和测试判断。
            error_type: 更宽泛的错误类型或异常类别名称。
            message: 面向开发、日志和审计的详细错误信息。
            component: 最先确认并封装该错误的组件名称。
            user_visible_message: 可安全直接展示给最终用户的简化信息。
            recoverable: 在补充条件或采取恢复动作后是否可能继续处理。
            retryable: 当前失败是否允许按策略自动重试。
            failure_category: 规范事实类别；未登记错误码必须显式提供。
            requires_human: 是否需要人工判断、审批或修正。
            requires_new_input: 是否必须由调用方补充新的业务输入。
            failed_node: 发生失败的工作流节点标识或名称。
            agent_name: 与失败直接关联的 Agent 名称。
            tool_name: 与失败直接关联的工具名称。
            step_name: 与失败直接关联的工作流步骤名称。
            stack_trace: 可选且应已脱敏的堆栈文本。
            details: 允许进入业务错误记录的结构化事实详情。
            debug_info: 仅供诊断的结构化调试信息。
            extra: 尚未进入稳定错误契约的扩展字段。

        Returns:
            字段完整、类别一致且带创建时间的 ``ErrorSchema``。

        Raises:
            ValueError: 未登记错误码没有显式类别，或显式类别与固定映射冲突时抛出。
        """

        # 第一步：查询错误码是否具有项目级固定失败类别。
        expected_category = FIXED_FAILURE_CATEGORY_BY_ERROR_CODE.get(error_code)

        # 第二步：没有显式类别时，只允许使用已经登记且语义稳定的固定映射。
        if failure_category is None:
            if expected_category is None:
                raise ValueError(
                    "failure_category must be explicit for unregistered error_code: "
                    f"{error_code}"
                )
            normalized_category = expected_category
        else:
            # 第三步：把字符串或枚举统一规范为 FailureCategory，非法值由枚举校验抛出。
            normalized_category = FailureCategory(failure_category)

        # 第四步：固定错误码不允许调用方覆盖成另一个类别，防止同码异义。
        if expected_category is not None and normalized_category != expected_category:
            raise ValueError(
                f"error_code={error_code} requires failure_category="
                f"{expected_category.value}, got {normalized_category.value}"
            )

        # 第五步：构造统一来源对象，集中描述组件、Agent、工具和步骤定位信息。
        source = ErrorSourceSchema(
            component=component,
            agent_name=agent_name,
            tool_name=tool_name,
            step_name=step_name,
        )

        # 第六步：复制可变字典并填充统一时间，避免错误对象共享调用方可变引用。
        return ErrorSchema(
            error_code=error_code,
            error_type=error_type,
            message=message,
            user_visible_message=user_visible_message,
            recoverable=recoverable,
            retryable=retryable,
            failure_category=normalized_category,
            requires_human=requires_human,
            requires_new_input=requires_new_input,
            failed_node=failed_node,
            source=source,
            details=dict(details or {}),
            debug_info=dict(debug_info or {}),
            stack_trace=stack_trace,
            created_at=self.clock.now_iso(),
            extra=dict(extra or {}),
        )
