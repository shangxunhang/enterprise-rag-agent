# =============================================================================
# 中文阅读说明：企业文档生成业务模块，负责方案规划、检索、章节生成、引用和验收。
# 主要定义：ProjectInputReader。建议先从公开入口函数开始，再沿调用关系向下阅读。
# =============================================================================
"""Read and validate caller-provided project input from workflow state."""

from __future__ import annotations

from agent.runtime.shared_state_schema import SharedStateSchema
from agent.runtime.state_access import SharedStateReader
from apps.enterprise_document.schemas.project_input_schema import ProjectInputSchema


# 阅读注释（类）：封装 项目 输入 reader，集中封装相关状态、依赖和行为。
class ProjectInputReader:
    """封装 项目 输入 reader，集中封装相关状态、依赖和行为。"""
    # 阅读注释（函数）：初始化 ProjectInputReader，保存运行所需的依赖、配置或状态。
    def __init__(self, state_reader: SharedStateReader | None = None) -> None:
        """初始化 ProjectInputReader，保存运行所需的依赖、配置或状态。

        参数:
            state_reader: 状态 reader，具体约束请结合类型标注和调用方确认。

        返回:
            None

        阅读提示:
            主要直接调用：SharedStateReader。
        """
        self.state_reader = state_reader or SharedStateReader()

    # 阅读注释（函数）：读取 ProjectInputReader。
    def read(self, state: SharedStateSchema) -> ProjectInputSchema:
        """读取 ProjectInputReader。

        参数:
            state: 工作流共享状态。

        返回:
            ProjectInputSchema

        阅读提示:
            主要直接调用：self.state_reader.project_input_candidates, isinstance, hasattr, candidate.model_dump, ProjectInputSchema.model_validate, ValueError。
        """
        for candidate in self.state_reader.project_input_candidates(state):
            if isinstance(candidate, ProjectInputSchema):
                return candidate
            if hasattr(candidate, "model_dump"):
                candidate = candidate.model_dump()
            if isinstance(candidate, dict) and candidate:
                return ProjectInputSchema.model_validate(candidate)
        raise ValueError(
            "PROJECT_INPUT_REQUIRED: caller must provide a validated "
            "ProjectInputSchema; the workflow no longer injects demo business facts."
        )
