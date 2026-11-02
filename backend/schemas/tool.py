"""定义工具注册信息、标准调用和标准结果协议。

Agent 和工作流通过 ``ToolCallSchema`` 调用工具，通过 ``ToolResultSchema`` 接收结果；
注册表可使用 ``ToolSpecSchema`` 描述能力和超时重试建议。结果中的 ``status`` 是规范
事实源，``success`` 会在校验阶段与其对齐，避免两个字段互相矛盾。
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from pydantic import Field, model_validator

from schemas.common import ErrorSchema, SchemaBase
from schemas.status import ExecutionStatus


class ToolSpecSchema(SchemaBase):
    """描述一个可注册工具的稳定能力、协议和运行限制。"""

    # 当前工具规格契约版本。
    schema_version: str = "tool_spec_v1"

    # 工具注册和调用使用的稳定唯一名称。
    tool_name: str
    # 工具能力类别，例如 rag、sql、template、model、parser、export 或 rule_check。
    tool_type: str

    # 面向 Agent、管理界面和人工阅读的能力说明。
    description: Optional[str] = None

    # 输入 Schema 的稳定名称或可解析引用。
    input_schema: str
    # 输出 Schema 的稳定名称或可解析引用。
    output_schema: str

    # 该工具明确支持的任务类型；空集合的解释由注册策略决定。
    supported_task_types: List[str] = Field(default_factory=list)

    # 单次工具调用建议超时秒数。
    timeout_seconds: int = 300
    # 规格层建议的最大重试次数；最终重试仍由运行时策略决定。
    max_retries: int = 0

    # 是否允许装配层把工具注册为当前可调用实例。
    is_active: bool = True

    # 尚未进入稳定工具规格契约的扩展字段。
    extra: Dict[str, Any] = Field(default_factory=dict)


class ToolCallSchema(SchemaBase):
    """表示 Agent 或工作流发起的一次标准工具调用。"""

    # 当前工具调用契约版本。
    schema_version: str = "tool_call_v1"

    # 单次工具调用唯一标识，用于结果、轨迹和重试关联。
    tool_call_id: str
    # 调用所属业务任务标识。
    task_id: str
    # 调用所属单次运行标识。
    run_id: str

    # 要从注册表解析的工具名称。
    tool_name: str
    # 已按工具输入契约组织的结构化参数。
    tool_input: Dict[str, Any] = Field(default_factory=dict)

    # 发起调用的 Agent 名称。
    caller_agent: Optional[str] = None
    # 调用所在工作流步骤标识。
    step_id: Optional[str] = None
    # 调用所在工作流步骤名称。
    step_name: Optional[str] = None

    # 调用对象创建时间。
    created_at: str

    # 尚未进入稳定调用契约的扩展字段。
    extra: Dict[str, Any] = Field(default_factory=dict)
    # 调用用途、租户或追踪相关元数据。
    metadata: Dict[str, Any] = Field(default_factory=dict)


class ToolResultSchema(SchemaBase):
    """表示工具执行后的规范结果、错误和耗时。

    ``status`` 可以表达部分成功和可重试失败等细粒度终态；``success`` 是兼容布尔视图，
    模型校验后只有成功或部分成功状态会将其设为 ``True``。
    """

    # 当前工具结果契约版本。
    schema_version: str = "tool_result_v1"

    # 对应 ``ToolCallSchema.tool_call_id``。
    tool_call_id: str
    # 结果所属业务任务标识。
    task_id: str
    # 结果所属单次运行标识。
    run_id: str

    # 实际执行的工具名称。
    tool_name: str
    # 兼容成功布尔值；校验后由规范状态重新计算。
    success: bool
    # 规范执行状态；未提供时根据原始 success 推导成功或失败。
    status: Optional[ExecutionStatus] = None

    # 工具成功或部分成功时产生的结构化输出。
    result: Dict[str, Any] = Field(default_factory=dict)

    # 工具失败时最接近来源创建的结构化错误。
    error: Optional[ErrorSchema] = None
    # 兼容和快速展示使用的错误摘要。
    error_message: Optional[str] = None

    # 工具实际开始时间。
    started_at: Optional[str] = None
    # 工具实际结束时间。
    finished_at: Optional[str] = None
    # 工具调用毫秒耗时；工具未提供时可由外层执行器补齐。
    latency_ms: Optional[int] = None

    # 结果对象创建时间。
    created_at: str

    # 尚未进入稳定结果契约的扩展字段。
    extra: Dict[str, Any] = Field(default_factory=dict)
    # 检索策略、供应商或运行上下文等结果元数据。
    metadata: Dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def align_status(self) -> "ToolResultSchema":
        """以规范状态统一 ``status`` 和兼容 ``success``。

        Returns:
            字段已经对齐的当前工具结果对象。
        """

        # 第一步：旧调用方未提供状态时，根据原始 success 补成最基本成功/失败状态。
        if self.status is None:
            self.status = (
                ExecutionStatus.SUCCESS if self.success else ExecutionStatus.FAILED
            )

        # 第二步：规范状态成为事实源；部分成功仍表示工具产生了可用结果。
        self.success = self.status in {
            ExecutionStatus.SUCCESS,
            ExecutionStatus.PARTIAL_SUCCESS,
        }

        return self
