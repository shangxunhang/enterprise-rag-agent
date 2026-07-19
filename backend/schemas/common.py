# =============================================================================
# 中文阅读说明：跨模块数据 Schema 定义模块。
# 主要定义：SchemaBase、ErrorSourceSchema、ErrorSchema、WarningSchema。建议先从公开入口函数开始，再沿调用关系向下阅读。
# =============================================================================
"""Common schemas."""

from __future__ import annotations

from typing import Any, Dict, Optional

from pydantic import BaseModel, ConfigDict, Field


# 阅读注释（类）：封装 Schema base，集中封装相关状态、依赖和行为。
class SchemaBase(BaseModel):
    """Base schema for all protocol schemas."""

    model_config = ConfigDict(arbitrary_types_allowed=True)


# 阅读注释（类）：封装 错误 source Schema，定义跨模块传递的数据结构与字段约束。
class ErrorSourceSchema(SchemaBase):
    """封装 错误 source Schema，定义跨模块传递的数据结构与字段约束。"""
    schema_version: str = "error_source_v1"

    component: Optional[str] = None
    agent_name: Optional[str] = None
    tool_name: Optional[str] = None
    step_name: Optional[str] = None

    extra: Dict[str, Any] = Field(default_factory=dict)


# 阅读注释（类）：封装 错误 Schema，定义跨模块传递的数据结构与字段约束。
class ErrorSchema(SchemaBase):
    """封装 错误 Schema，定义跨模块传递的数据结构与字段约束。"""
    schema_version: str = "error_v2"

    error_id: Optional[str] = None
    error_code: str
    error_type: str
    message: str
    error_message: Optional[str] = None
    user_visible_message: Optional[str] = None

    recoverable: bool = True
    retryable: bool = False
    failed_node: Optional[str] = None

    source: Optional[ErrorSourceSchema] = None
    details: Dict[str, Any] = Field(default_factory=dict)
    debug_info: Dict[str, Any] = Field(default_factory=dict)
    stack_trace: Optional[str] = None

    created_at: Optional[str] = None

    extra: Dict[str, Any] = Field(default_factory=dict)

    # 阅读注释（函数）：处理 模型 post init 相关逻辑。
    def model_post_init(self, __context: Any) -> None:
        """处理 模型 post init 相关逻辑。

        参数:
            __context: 上下文，具体约束请结合类型标注和调用方确认。

        返回:
            None
        """
        if self.error_message is None:
            self.error_message = self.message
        if self.user_visible_message is None:
            self.user_visible_message = self.message


# 阅读注释（类）：封装 warning Schema，定义跨模块传递的数据结构与字段约束。
class WarningSchema(SchemaBase):
    """封装 warning Schema，定义跨模块传递的数据结构与字段约束。"""
    schema_version: str = "warning_v1"

    warning_code: str
    message: str

    source: Optional[ErrorSourceSchema] = None
    details: Dict[str, Any] = Field(default_factory=dict)

    created_at: Optional[str] = None

    extra: Dict[str, Any] = Field(default_factory=dict)