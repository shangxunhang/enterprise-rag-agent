# =============================================================================
# 中文阅读说明：跨模块数据 Schema 定义模块。
# 主要定义：ToolSpecSchema、ToolCallSchema、ToolResultSchema。建议先从公开入口函数开始，再沿调用关系向下阅读。
# =============================================================================
"""Tool registry and tool call/result schemas."""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from pydantic import Field, model_validator

from schemas.common import ErrorSchema, SchemaBase
from schemas.status import ExecutionStatus


# 阅读注释（类）：封装 工具 spec Schema，定义跨模块传递的数据结构与字段约束。
class ToolSpecSchema(SchemaBase):
    """封装 工具 spec Schema，定义跨模块传递的数据结构与字段约束。"""
    schema_version: str = "tool_spec_v1"

    tool_name: str
    tool_type: str  # rag | sql | template | model | parser | export | rule_check | other

    description: Optional[str] = None

    input_schema: str
    output_schema: str

    supported_task_types: List[str] = Field(default_factory=list)

    timeout_seconds: int = 300
    max_retries: int = 0

    is_active: bool = True

    extra: Dict[str, Any] = Field(default_factory=dict)


# 阅读注释（类）：封装 工具 call Schema，定义跨模块传递的数据结构与字段约束。
class ToolCallSchema(SchemaBase):
    """封装 工具 call Schema，定义跨模块传递的数据结构与字段约束。"""
    schema_version: str = "tool_call_v1"

    tool_call_id: str
    task_id: str
    run_id: str

    tool_name: str
    tool_input: Dict[str, Any] = Field(default_factory=dict)

    caller_agent: Optional[str] = None
    step_id: Optional[str] = None
    step_name: Optional[str] = None

    created_at: str

    extra: Dict[str, Any] = Field(default_factory=dict)
    metadata: Dict[str, Any] = Field(default_factory=dict)


# 阅读注释（类）：封装 工具 结果 Schema，定义跨模块传递的数据结构与字段约束。
class ToolResultSchema(SchemaBase):
    """封装 工具 结果 Schema，定义跨模块传递的数据结构与字段约束。"""
    schema_version: str = "tool_result_v1"

    tool_call_id: str
    task_id: str
    run_id: str

    tool_name: str
    success: bool
    status: Optional[ExecutionStatus] = None

    result: Dict[str, Any] = Field(default_factory=dict)

    error: Optional[ErrorSchema] = None
    error_message: Optional[str] = None

    started_at: Optional[str] = None
    finished_at: Optional[str] = None
    latency_ms: Optional[int] = None

    created_at: str

    extra: Dict[str, Any] = Field(default_factory=dict)
    metadata: Dict[str, Any] = Field(default_factory=dict)

    # 阅读注释（函数）：处理 align 状态 相关逻辑。
    @model_validator(mode="after")
    def align_status(self) -> "ToolResultSchema":
        """处理 align 状态 相关逻辑。

        返回:
            'ToolResultSchema'

        阅读提示:
            主要直接调用：model_validator。
        """
        if self.status is None:
            self.status = (
                ExecutionStatus.SUCCESS if self.success else ExecutionStatus.FAILED
            )
        self.success = self.status in {
            ExecutionStatus.SUCCESS,
            ExecutionStatus.PARTIAL_SUCCESS,
        }
        return self
