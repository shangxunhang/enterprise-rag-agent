# =============================================================================
# 中文阅读说明：Agent 与 Workflow 模块，负责任务路由、状态编排、工具调用和结果协议。
# 主要定义：ContextBundleFactory。建议先从公开入口函数开始，再沿调用关系向下阅读。
# =============================================================================
"""Construction of canonical workflow context from TaskSchema."""

from __future__ import annotations

from schemas.context import (
    BusinessContextSchema,
    ContextBundleSchema,
    GenerationContextSchema,
    RuntimeContextSchema,
    TaskContextSchema,
    UserContextSchema,
)
from schemas.status import ExecutionStatus
from schemas.task import TaskSchema


# 阅读注释（类）：封装 上下文 bundle 工厂，负责根据配置装配并返回运行实例。
class ContextBundleFactory:
    """封装 上下文 bundle 工厂，负责根据配置装配并返回运行实例。"""
    # 阅读注释（函数）：构建 ContextBundleFactory。
    def build(self, task: TaskSchema) -> ContextBundleSchema:
        """构建 ContextBundleFactory。

        参数:
            task: 待执行的任务对象。

        返回:
            ContextBundleSchema

        阅读提示:
            主要直接调用：ContextBundleSchema, UserContextSchema, TaskContextSchema, BusinessContextSchema, project_input.get, GenerationContextSchema, get, RuntimeContextSchema。
        """
        project_input = task.project_input or {}
        return ContextBundleSchema(
            user=UserContextSchema(
                user_id=task.user_id,
                tenant_id=task.tenant_id,
                session_id=task.session_id,
                user_query=task.user_input,
                metadata=task.metadata,
            ),
            task=TaskContextSchema(
                task_id=task.task_id,
                run_id=task.run_id,
                task_type=task.task_type,
                project_name=task.project_name,
                generation_requirements=task.generation_requirements,
                output_schema=task.output_schema,
                metadata=task.metadata,
            ),
            business=BusinessContextSchema(
                project_input=project_input,
                source_materials=task.source_materials,
                missing_information=project_input.get("missing_information") or [],
                conflicting_information=project_input.get("conflicting_information") or [],
                manual_boundaries=project_input.get("manual_boundaries") or [],
                metadata=project_input.get("metadata") or {},
            ),
            generation=GenerationContextSchema(
                document_title=(task.output_schema or {}).get("document_title"),
                required_sections=(task.output_schema or {}).get("required_sections") or [],
            ),
            runtime=RuntimeContextSchema(status=ExecutionStatus.PENDING),
        )
