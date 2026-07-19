# =============================================================================
# 中文阅读说明：模型网关模块，用于屏蔽不同 LLM 提供方和本地模型调用差异。
# 主要定义：ModelRouter。建议先从公开入口函数开始，再沿调用关系向下阅读。
# =============================================================================
"""Model selection policy."""

from __future__ import annotations

from schemas.model import ModelRequestSchema


# 阅读注释（类）：封装 模型 路由器，集中封装相关状态、依赖和行为。
class ModelRouter:
    """封装 模型 路由器，集中封装相关状态、依赖和行为。"""
    # 阅读注释（函数）：初始化 ModelRouter，保存运行所需的依赖、配置或状态。
    def __init__(self, default_model_name: str) -> None:
        """初始化 ModelRouter，保存运行所需的依赖、配置或状态。

        参数:
            default_model_name: default 模型 名称，具体约束请结合类型标注和调用方确认。

        返回:
            None
        """
        self.default_model_name = default_model_name

    # 阅读注释（函数）：选择 ModelRouter。
    def select(self, request: ModelRequestSchema) -> str:
        """选择 ModelRouter。

        参数:
            request: 当前请求对象。

        返回:
            str
        """
        return request.model_name or self.default_model_name
