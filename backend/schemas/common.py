"""定义跨模块复用的基础 Schema、结构化错误和告警。

业务、运行时、Agent、工具和评测模型都继承 ``SchemaBase``。错误模型同时承载机器
错误码、失败事实类别、恢复属性、来源定位和用户可见信息；告警只描述不阻断执行的
异常情况，不能替代失败终态。
"""

from __future__ import annotations

from typing import Any, Dict, Optional

from pydantic import BaseModel, ConfigDict, Field

from schemas.failure import FailureCategory


class SchemaBase(BaseModel):
    """所有项目协议 Schema 的公共 Pydantic 基类。

    当前允许任意 Python 类型，兼容少量运行时对象进入扩展字段。跨进程、持久化和
    API 边界仍应使用 ``model_dump(mode="json")`` 或更严格的领域 Schema，不能据此
    假设任意对象都可安全序列化。
    """

    model_config = ConfigDict(arbitrary_types_allowed=True)


class ErrorSourceSchema(SchemaBase):
    """定位一个结构化错误最接近的确认来源。

    组件、Agent、工具和步骤字段可以同时存在：``component`` 表示封装错误的运行时
    边界，其余字段进一步指出具体业务执行位置。
    """

    # 当前错误来源契约版本。
    schema_version: str = "error_source_v1"

    # 最先识别并构造错误的组件，例如 ToolExecutor 或 ModelGateway。
    component: Optional[str] = None
    # 与错误直接关联的 Agent 名称。
    agent_name: Optional[str] = None
    # 与错误直接关联的工具名称。
    tool_name: Optional[str] = None
    # 错误发生时的工作流步骤名称。
    step_name: Optional[str] = None

    # 尚未进入稳定来源契约的扩展定位信息。
    extra: Dict[str, Any] = Field(default_factory=dict)


class ErrorSchema(SchemaBase):
    """跨模块传递、持久化和审计的规范错误对象。

    ``failure_category`` 只描述错误事实，``retryable``、``recoverable`` 和人工要求描述
    当前处理策略。它们不能互相推导，例如暂时可恢复的错误不一定允许自动重试。
    """

    # 当前结构化错误契约版本。
    schema_version: str = "error_v2"

    # 可选错误实例标识，用于跨事件关联同一次失败。
    error_id: Optional[str] = None
    # 稳定机器错误码，供策略、统计和测试判断。
    error_code: str
    # 宽泛错误类型或原始异常类名。
    error_type: str
    # 面向开发、日志和审计的主要错误描述。
    message: str
    # 历史兼容错误文本；未提供时自动复制 ``message``。
    error_message: Optional[str] = None
    # 可安全直接展示给最终用户的信息；未提供时当前版本回退为 ``message``。
    user_visible_message: Optional[str] = None

    # 采取外部动作或补充条件后是否可能恢复。
    recoverable: bool = True
    # 是否允许当前自动化策略再次执行。
    retryable: bool = False
    # 失败事实所属规范类别。
    failure_category: FailureCategory = FailureCategory.UNKNOWN
    # 是否必须由人工判断、审批或修正。
    requires_human: bool = False
    # 是否必须由调用方补充新的业务输入。
    requires_new_input: bool = False
    # 最接近失败位置的节点标识或名称。
    failed_node: Optional[str] = None

    # 组件、Agent、工具和步骤来源定位。
    source: Optional[ErrorSourceSchema] = None
    # 允许进入业务错误记录的结构化事实详情。
    details: Dict[str, Any] = Field(default_factory=dict)
    # 仅供诊断使用的结构化调试信息，写入前应完成脱敏。
    debug_info: Dict[str, Any] = Field(default_factory=dict)
    # 可选堆栈文本；持久化前必须完成敏感信息清理。
    stack_trace: Optional[str] = None

    # 错误对象创建时间，通常由统一 ``Clock`` 生成。
    created_at: Optional[str] = None

    # 尚未进入稳定错误契约的扩展字段。
    extra: Dict[str, Any] = Field(default_factory=dict)

    def model_post_init(self, __context: Any) -> None:
        """在模型构造后补齐两个兼容消息字段。

        Args:
            __context: Pydantic 传入的初始化上下文；当前实现不使用。

        该逻辑只在调用方省略字段时补值，不覆盖显式提供的用户文案或兼容错误文本。
        """

        # 第一步：旧调用方读取 error_message 时，默认获得主要错误描述。
        if self.error_message is None:
            self.error_message = self.message

        # 第二步：没有单独用户文案时保持历史行为；高风险边界应显式提供安全文案。
        if self.user_visible_message is None:
            self.user_visible_message = self.message


class WarningSchema(SchemaBase):
    """描述不阻断当前执行、但需要向后续节点或调用方暴露的告警。"""

    # 当前告警契约版本。
    schema_version: str = "warning_v1"

    # 稳定机器告警码，用于聚合、过滤和质量规则判断。
    warning_code: str
    # 面向开发或业务调用方的告警说明。
    message: str

    # 产生告警的组件和执行位置。
    source: Optional[ErrorSourceSchema] = None
    # 与告警相关的结构化事实详情。
    details: Dict[str, Any] = Field(default_factory=dict)

    # 告警创建时间。
    created_at: Optional[str] = None

    # 尚未进入稳定告警契约的扩展字段。
    extra: Dict[str, Any] = Field(default_factory=dict)
