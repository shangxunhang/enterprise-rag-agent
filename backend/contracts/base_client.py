# =============================================================================
# 中文阅读说明：端口与协议定义模块，用于约束模块间依赖边界。
# 主要定义：BaseLLMClient。建议先从公开入口函数开始，再沿调用关系向下阅读。
# =============================================================================
"""Base LLM client interface."""

from __future__ import annotations

from abc import ABC, abstractmethod

from schemas.model import ModelRequestSchema, ModelResponseSchema


# 阅读注释（类）：封装 base llmclient，集中封装相关状态、依赖和行为。
class BaseLLMClient(ABC):
    """Base class for all LLM clients."""

    model_name: str

    # 阅读注释（函数）：生成 BaseLLMClient。
    @abstractmethod
    def generate(self, request: ModelRequestSchema) -> ModelResponseSchema:
        """Generate text from model request."""
        raise NotImplementedError