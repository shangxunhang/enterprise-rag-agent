"""从 ``TaskSchema`` 构造规范工作流上下文包。

任务对象保留入口输入快照和展平索引字段，本工厂把它们投影为用户、任务、业务、
生成和运行时五类上下文。后续 Agent 应优先读取 ``ContextBundleSchema``，避免各自
重复解释原始 ``project_input``。
"""

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


class ContextBundleFactory:
    """把运行时任务投影为具有明确所有权的规范上下文集合。"""

    def build(self, task: TaskSchema) -> ContextBundleSchema:
        """根据任务快照创建初始上下文包。

        Args:
            task: 已通过入口工厂构造的标准任务。

        Returns:
            初始状态为 ``PENDING`` 的上下文包；证据和生成中间结果尚为空。
        """

        # 第一步：取得完整规范项目输入快照；兼容旧任务可能传入空值的情况。
        project_input = task.project_input or {}

        # 第二步：一次性构造各领域上下文，避免先创建半完整对象再逐字段修改。
        return ContextBundleSchema(
            # 用户上下文只保存身份、会话和原始请求，不混入工作流结果。
            user=UserContextSchema(
                user_id=task.user_id,
                tenant_id=task.tenant_id,
                session_id=task.session_id,
                user_query=task.user_input,
                metadata=task.metadata,
            ),
            # 任务上下文保存本次运行身份和稳定生成契约。
            task=TaskContextSchema(
                task_id=task.task_id,
                run_id=task.run_id,
                task_type=task.task_type,
                project_name=task.project_name,
                generation_requirements=task.generation_requirements,
                output_schema=task.output_schema,
                metadata=task.metadata,
            ),
            # 业务上下文保留项目事实、素材以及入口已经声明的缺失、冲突和人工边界。
            business=BusinessContextSchema(
                project_input=project_input,
                source_materials=task.source_materials,
                missing_information=project_input.get("missing_information") or [],
                conflicting_information=project_input.get("conflicting_information") or [],
                manual_boundaries=project_input.get("manual_boundaries") or [],
                metadata=project_input.get("metadata") or {},
            ),
            # 生成上下文从输出契约提取标题和章节，供规划及章节生成节点直接使用。
            generation=GenerationContextSchema(
                document_title=(task.output_schema or {}).get("document_title"),
                required_sections=(task.output_schema or {}).get("required_sections") or [],
            ),
            # 工作流尚未启动，因此规范运行时上下文初始化为待执行态。
            runtime=RuntimeContextSchema(status=ExecutionStatus.PENDING),
        )
