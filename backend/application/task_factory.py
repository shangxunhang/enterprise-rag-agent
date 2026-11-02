"""把规范 ``ProjectInput`` 映射为运行时 ``TaskSchema``。

``ProjectInputFactory`` 负责入口校验和 demo 适配，本工厂只负责运行时投影：补充
``run_id`` 与创建时间，展平常用材料标识，并把嵌套业务输入转换为任务状态和工作流
可以稳定读取的结构。
"""

from __future__ import annotations

from typing import Any, Dict, Optional

from application.project_input_factory import ProjectInputFactory
from apps.enterprise_document.services.project_input_normalization.input_contract import (
    CURRENT_TASK_EXECUTION_PROFILE,
)
from schemas.task import TaskSchema


class TaskFactory:
    """根据规范项目输入创建单次运行使用的任务对象。

    Attributes:
        project_inputs: 负责生产输入严格校验和 demo 输入适配的工厂。
    """

    def __init__(self, project_inputs: ProjectInputFactory | None = None) -> None:
        """创建任务工厂并补齐默认项目输入工厂。

        Args:
            project_inputs: 自定义项目输入工厂；未提供时创建默认实现。
        """

        # 允许测试或外层装配注入不同入口策略，默认使用正式工厂。
        self.project_inputs = project_inputs or ProjectInputFactory()

    def build(
        self,
        task_id: str,
        run_id: str,
        user_input: str,
        created_at: str,
        project_input: Optional[Dict[str, Any]] = None,
        *,
        allow_demo_defaults: bool = False,
    ) -> TaskSchema:
        """校验项目输入并构造可进入主链的标准任务。

        Args:
            task_id: 业务任务唯一标识。
            run_id: 本次执行唯一标识。
            user_input: 外部入口接收的用户请求文本。
            created_at: 任务对象创建时间。
            project_input: 调用方提交的项目输入载荷。
            allow_demo_defaults: 是否显式启用 demo 默认值适配。

        Returns:
            包含规范项目输入快照、材料索引范围和生成要求的 ``TaskSchema``。
        """

        # 第一步：委托 ProjectInputFactory 处理生产严格校验或显式 demo 补全。
        normalized = self.project_inputs.build(
            task_id=task_id,
            user_input=user_input,
            raw_project_input=project_input,
            allow_demo_defaults=allow_demo_defaults,
        )

        # 第二步：按素材声明顺序收集 file_id，并通过有序字典语义去重。
        file_ids = list(
            dict.fromkeys(
                file_id
                for material in normalized.source_materials
                for file_id in material.file_ids
                if file_id
            )
        )

        # 第三步：同样展平并有序去重文档标识，供 RAG 过滤和审计直接使用。
        doc_ids = list(
            dict.fromkeys(
                doc_id
                for material in normalized.source_materials
                for doc_id in material.doc_ids
                if doc_id
            )
        )

        # 第四步：兼容素材 metadata 中单个 kb_id 和 kb_ids 列表两种来源，统一转字符串。
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

        # 第五步：优先使用调用方元数据中的任务名称，否则构造稳定可读的默认名称。
        task_name = (
            str(normalized.metadata.get("task_name") or "").strip()
            or f"{normalized.task_type}:{normalized.project_name or normalized.task_id}"
        )
        task_metadata = dict(normalized.metadata)
        task_metadata["execution_profile"] = CURRENT_TASK_EXECUTION_PROFILE
        current_project_id = str(normalized.current_project_id or "").strip()
        if current_project_id:
            # Persist the runtime project identity in the existing task-state
            # metadata channel so resume/recover can rebuild the same evidence
            # boundary before constructing a retrieval-capable Supervisor.
            task_metadata["current_project_id"] = current_project_id

        # 第六步：创建运行时任务，并保留完整规范输入及常用嵌套结构的序列化快照。
        return TaskSchema(
            task_id=task_id,
            run_id=run_id,
            tenant_id=normalized.tenant_id,
            task_type=normalized.task_type,
            task_name=task_name,
            project_name=normalized.project_name,
            user_input=normalized.user_query,
            project_input=normalized.model_dump(),
            source_materials=[item.model_dump() for item in normalized.source_materials],
            generation_requirements=normalized.generation_requirements.model_dump(),
            output_schema=normalized.output_schema.model_dump(),
            metadata=task_metadata,
            file_ids=file_ids,
            doc_ids=doc_ids,
            kb_ids=kb_ids,
            created_at=created_at,
        )
