# =============================================================================
# 中文阅读说明：跨模块数据 Schema 定义模块。
# 主要定义：TaskStateEventSchema、TaskStateRecordSchema。建议先从公开入口函数开始，再沿调用关系向下阅读。
# =============================================================================
"""Task state schemas using the canonical status contract."""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from pydantic import Field

from .common import ErrorSchema, SchemaBase
from .status import ExecutionStatus


# 阅读注释（类）：封装 任务 状态 事件 Schema，定义跨模块传递的数据结构与字段约束。
class TaskStateEventSchema(SchemaBase):
    """封装 任务 状态 事件 Schema，定义跨模块传递的数据结构与字段约束。"""
    schema_version: str = "task_state_event_v2"

    event_id: str
    task_id: str
    run_id: str
    task_type: str
    event_type: str

    previous_status: Optional[ExecutionStatus] = None
    current_status: ExecutionStatus
    current_step: Optional[str] = None

    result_id: Optional[str] = None
    error: Optional[ErrorSchema] = None
    error_message: Optional[str] = None

    created_at: str
    metadata: Dict[str, Any] = Field(default_factory=dict)
    extra: Dict[str, Any] = Field(default_factory=dict)


# 阅读注释（类）：封装 任务 状态 记录 Schema，定义跨模块传递的数据结构与字段约束。
class TaskStateRecordSchema(SchemaBase):
    """封装 任务 状态 记录 Schema，定义跨模块传递的数据结构与字段约束。"""
    schema_version: str = "task_state_record_v2"

    task_id: str
    run_id: str
    task_type: str
    tenant_id: str = "default"

    task_name: Optional[str] = None
    project_name: Optional[str] = None
    user_input: Optional[str] = None

    status: ExecutionStatus
    current_step: Optional[str] = None

    created_at: str
    updated_at: str
    started_at: Optional[str] = None
    finished_at: Optional[str] = None

    result_id: Optional[str] = None
    error: Optional[ErrorSchema] = None
    error_message: Optional[str] = None

    events: List[TaskStateEventSchema] = Field(default_factory=list)
    metadata: Dict[str, Any] = Field(default_factory=dict)
    extra: Dict[str, Any] = Field(default_factory=dict)
