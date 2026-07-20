# =============================================================================
# 中文阅读说明：Agent 与 Workflow 模块，负责任务路由、状态编排、工具调用和结果协议。
# 主要定义：WorkflowStepSchema、WorkflowDefinitionSchema。建议先从公开入口函数开始，再沿调用关系向下阅读。
# =============================================================================
"""Workflow schemas."""

from __future__ import annotations

from typing import Any, Dict, List, Literal, Optional
from pydantic import Field, model_validator

from schemas.common import SchemaBase


# 阅读注释（类）：封装 工作流 step Schema，定义跨模块传递的数据结构与字段约束。
class WorkflowStepSchema(SchemaBase):
    """封装 工作流 step Schema，定义跨模块传递的数据结构与字段约束。"""
    schema_version: str = "workflow_step_v1"

    step_id: str
    step_name: str
    step_type: str  # agent | tool | model | rule_check | export

    target_name: str  # Agent name or Tool name

    order: int

    input_keys: List[str] = Field(default_factory=list)
    output_keys: List[str] = Field(default_factory=list)

    # ``output_keys`` are logical workflow outputs. ``write_paths`` are the
    # physical GraphState paths that the node is allowed to mutate.
    write_paths: List[str] = Field(default_factory=list)

    on_success: str = "next"
    on_failure: str = "fail_task"

    max_retries: int = Field(default=0, ge=0)
    timeout_seconds: float = Field(default=1000.0, gt=0)

    # Successful/partial-success nodes always commit their validated delta.
    # Failed nodes default to discarding business-state changes. A business
    # gate may explicitly preserve selected partial outputs.
    commit_policy: Literal[
        "success_only", "allow_partial_on_failure", "always"
    ] = "success_only"
    failure_write_paths: List[str] = Field(default_factory=list)
    failure_commit_error_codes: List[str] = Field(default_factory=list)

    extra: Dict[str, Any] = Field(default_factory=dict)


# 阅读注释（类）：封装 工作流 definition Schema，定义跨模块传递的数据结构与字段约束。
class WorkflowDefinitionSchema(SchemaBase):
    """封装 工作流 definition Schema，定义跨模块传递的数据结构与字段约束。"""
    schema_version: str = "workflow_definition_v1"

    workflow_id: str
    workflow_name: str
    task_type: str
    workflow_version: str

    description: Optional[str] = None

    steps: List[WorkflowStepSchema] = Field(default_factory=list)

    is_active: bool = True

    created_at: str
    updated_at: Optional[str] = None

    extra: Dict[str, Any] = Field(default_factory=dict)

    # 阅读注释（函数）：校验 工作流 graph。
    @model_validator(mode="after")
    def validate_workflow_graph(self) -> "WorkflowDefinitionSchema":
        """校验 工作流 graph。

        返回:
            'WorkflowDefinitionSchema'

        阅读提示:
            主要直接调用：len, set, ValueError, strip, str, model_validator。
        """
        step_ids = [item.step_id for item in self.steps]
        if len(step_ids) != len(set(step_ids)):
            raise ValueError("workflow step_id values must be unique")
        orders = [item.order for item in self.steps]
        if len(orders) != len(set(orders)):
            raise ValueError("workflow step order values must be unique")

        special_routes = {"next", "fail_task", "end", "complete", "stop"}
        known_steps = set(step_ids)
        for step in self.steps:
            for field_name, route in (
                ("on_success", step.on_success),
                ("on_failure", step.on_failure),
            ):
                route = str(route or "").strip()
                if not route:
                    raise ValueError(f"{step.step_id}.{field_name} cannot be empty")
                if route not in special_routes and route not in known_steps:
                    raise ValueError(
                        f"{step.step_id}.{field_name} references unknown step: {route}"
                    )
                if route == step.step_id:
                    raise ValueError(
                        f"{step.step_id}.{field_name} cannot route to itself"
                    )
        return self