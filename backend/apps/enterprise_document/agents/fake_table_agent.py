# =============================================================================
# 中文阅读说明：企业文档生成业务模块，负责方案规划、检索、章节生成、引用和验收。
# 主要定义：FakeTableAgent。建议先从公开入口函数开始，再沿调用关系向下阅读。
# =============================================================================
"""Backward-compatible alias for the real project-input normalizer."""

from .project_input_normalizer_agent import ProjectInputNormalizerAgent


# 阅读注释（类）：封装 fake table Agent，负责接收状态、调用工具或服务并返回统一 Agent 结果。
class FakeTableAgent(ProjectInputNormalizerAgent):
    """Deprecated compatibility name. No fake business fallback remains."""

    agent_name = "FakeTableAgent"
