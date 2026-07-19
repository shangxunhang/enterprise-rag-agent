# =============================================================================
# 中文阅读说明：企业文档生成业务模块，负责方案规划、检索、章节生成、引用和验收。
# 主要定义：StructuredFactExtractor。建议先从公开入口函数开始，再沿调用关系向下阅读。
# =============================================================================
"""Extract typed facts from canonical ProjectInput."""

from __future__ import annotations

from agent.runtime.shared_state_schema import SharedStateSchema
from apps.enterprise_document.schemas.project_input_schema import ProjectInputSchema
from apps.enterprise_document.schemas.table_agent_schema import StructuredFactSchema
from .summary_service import ProjectInputSummaryService


# 阅读注释（类）：封装 structured fact extractor，集中封装相关状态、依赖和行为。
class StructuredFactExtractor:
    """封装 structured fact extractor，集中封装相关状态、依赖和行为。"""
    # 阅读注释（函数）：初始化 StructuredFactExtractor，保存运行所需的依赖、配置或状态。
    def __init__(self, summaries: ProjectInputSummaryService | None = None) -> None:
        """初始化 StructuredFactExtractor，保存运行所需的依赖、配置或状态。

        参数:
            summaries: summaries，具体约束请结合类型标注和调用方确认。

        返回:
            None

        阅读提示:
            主要直接调用：ProjectInputSummaryService。
        """
        self.summaries = summaries or ProjectInputSummaryService()

    # 阅读注释（函数）：提取 StructuredFactExtractor。
    def extract(
        self,
        state: SharedStateSchema,
        project_input: ProjectInputSchema,
        created_at: str,
    ) -> list[StructuredFactSchema]:
        """提取 StructuredFactExtractor。

        参数:
            state: 工作流共享状态。
            project_input: 规范化后的项目输入。
            created_at: created at，具体约束请结合类型标注和调用方确认。

        返回:
            list[StructuredFactSchema]

        阅读提示:
            主要直接调用：add_fact, self.summaries.organization_summary, self.summaries.hardware_summary, join。
        """
        facts: list[StructuredFactSchema] = []

        # 阅读注释（函数）：处理 add fact 相关逻辑。
        def add_fact(suffix: str, fact_type: str, content: str, confidence: float = 1.0) -> None:
            """处理 add fact 相关逻辑。

            参数:
                suffix: suffix，具体约束请结合类型标注和调用方确认。
                fact_type: fact 类型，具体约束请结合类型标注和调用方确认。
                content: 待处理内容。
                confidence: 置信度，具体约束请结合类型标注和调用方确认。

            返回:
                None

            阅读提示:
                主要直接调用：content.strip, facts.append, StructuredFactSchema。
            """
            if not content.strip():
                return
            facts.append(
                StructuredFactSchema(
                    fact_id=f"fact_{state.run_id}_{suffix}",
                    task_id=state.task_id,
                    run_id=state.run_id,
                    fact_type=fact_type,
                    content=content,
                    source_type="project_input",
                    source_ids=[project_input.task_id],
                    confidence=confidence,
                    created_at=created_at,
                )
            )

        add_fact("business_goal", "business_goal", project_input.business_goal)
        add_fact(
            "organization_scale",
            "project_scale",
            self.summaries.organization_summary(project_input),
        )
        add_fact("hardware", "hardware_resource", self.summaries.hardware_summary(project_input))
        add_fact(
            "manual_boundary",
            "manual_boundary",
            "；".join(
                f"{item.item}由{item.handled_by}处理"
                + (f"：{item.description}" if item.description else "")
                for item in project_input.manual_boundaries
            ),
        )
        if project_input.missing_information:
            add_fact(
                "missing_information",
                "missing_information",
                "当前待补充信息：" + "；".join(project_input.missing_information),
                0.95,
            )
        return facts
