# =============================================================================
# 中文阅读说明：应用层主链编排模块。
# 主要定义：TaskFactory。建议先从公开入口函数开始，再沿调用关系向下阅读。
# =============================================================================
"""Map canonical ProjectInput to TaskSchema."""

from __future__ import annotations

from typing import Any, Dict, Optional

from application.project_input_factory import ProjectInputFactory
from schemas.task import TaskSchema


# 阅读注释（类）：封装 任务 工厂，负责根据配置装配并返回运行实例。
class TaskFactory:
    """封装 任务 工厂，负责根据配置装配并返回运行实例。"""
    # 阅读注释（函数）：初始化 TaskFactory，保存运行所需的依赖、配置或状态。
    def __init__(self, project_inputs: ProjectInputFactory | None = None) -> None:
        """初始化 TaskFactory，保存运行所需的依赖、配置或状态。

        参数:
            project_inputs: 项目 inputs，具体约束请结合类型标注和调用方确认。

        返回:
            None

        阅读提示:
            主要直接调用：ProjectInputFactory。
        """
        self.project_inputs = project_inputs or ProjectInputFactory()

    # 阅读注释（函数）：构建 TaskFactory。
    def build(
        self,
        task_id: str,
        run_id: str,
        user_input: str,
        created_at: str,
        project_input: Optional[Dict[str, Any]] = None,
        *,
        allow_demo_defaults: bool = True,
    ) -> TaskSchema:
        """构建 TaskFactory。

        参数:
            task_id: 任务唯一标识。
            run_id: 本次运行唯一标识。
            user_input: user 输入，具体约束请结合类型标注和调用方确认。
            created_at: created at，具体约束请结合类型标注和调用方确认。
            project_input: 规范化后的项目输入。
            allow_demo_defaults: allow 演示 defaults，具体约束请结合类型标注和调用方确认。

        返回:
            TaskSchema

        阅读提示:
            主要直接调用：self.project_inputs.build, list, dict.fromkeys, str, material.metadata.get, strip, TaskSchema, normalized.metadata.get。
        """
        normalized = self.project_inputs.build(
            task_id=task_id,
            user_input=user_input,
            raw_project_input=project_input,
            allow_demo_defaults=allow_demo_defaults,
        )
        file_ids = list(
            dict.fromkeys(
                file_id
                for material in normalized.source_materials
                for file_id in material.file_ids
                if file_id
            )
        )
        doc_ids = list(
            dict.fromkeys(
                doc_id
                for material in normalized.source_materials
                for doc_id in material.doc_ids
                if doc_id
            )
        )
        kb_ids = list(
            dict.fromkeys(
                str(kb_id)
                for material in normalized.source_materials
                for kb_id in (
                    material.metadata.get("kb_ids")
                    or (
                        [material.metadata.get("kb_id")]
                        if material.metadata.get("kb_id")
                        else []
                    )
                )
                if str(kb_id).strip()
            )
        )
        return TaskSchema(
            task_id=task_id,
            run_id=run_id,
            tenant_id=normalized.tenant_id,
            task_type=normalized.task_type,
            task_name=(
                str(normalized.metadata.get("task_name") or "").strip()
                or f"{normalized.task_type}:{normalized.project_name or normalized.task_id}"
            ),
            project_name=normalized.project_name,
            user_input=normalized.user_query,
            project_input=normalized.model_dump(),
            source_materials=[item.model_dump() for item in normalized.source_materials],
            generation_requirements=normalized.generation_requirements.model_dump(),
            output_schema=normalized.output_schema.model_dump(),
            metadata=normalized.metadata,
            file_ids=file_ids,
            doc_ids=doc_ids,
            kb_ids=kb_ids,
            created_at=created_at,
        )
