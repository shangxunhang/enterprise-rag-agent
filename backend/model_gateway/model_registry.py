# =============================================================================
# 中文阅读说明：模型网关模块，用于屏蔽不同 LLM 提供方和本地模型调用差异。
# 主要定义：ModelRegistry。建议先从公开入口函数开始，再沿调用关系向下阅读。
# =============================================================================
"""Model client registry."""

from __future__ import annotations

from typing import Dict

from contracts.base_client import BaseLLMClient


# 阅读注释（类）：封装 模型 注册表，集中封装相关状态、依赖和行为。
class ModelRegistry:
    """封装 模型 注册表，集中封装相关状态、依赖和行为。"""
    # 阅读注释（函数）：初始化 ModelRegistry，保存运行所需的依赖、配置或状态。
    def __init__(self) -> None:
        """初始化 ModelRegistry，保存运行所需的依赖、配置或状态。

        返回:
            None
        """
        self._clients: Dict[str, BaseLLMClient] = {}

    # 阅读注释（函数）：注册 ModelRegistry。
    def register(self, client: BaseLLMClient) -> None:
        """注册 ModelRegistry。

        参数:
            client: 下游客户端。

        返回:
            None

        阅读提示:
            主要直接调用：ValueError。
        """
        if client.model_name in self._clients:
            raise ValueError(f"Model client already registered: {client.model_name}")
        self._clients[client.model_name] = client

    # 阅读注释（函数）：获取 ModelRegistry。
    def get(self, model_name: str) -> BaseLLMClient:
        """获取 ModelRegistry。

        参数:
            model_name: 模型 名称，具体约束请结合类型标注和调用方确认。

        返回:
            BaseLLMClient

        阅读提示:
            主要直接调用：KeyError。
        """
        if model_name not in self._clients:
            raise KeyError(f"Model client not found: {model_name}")
        return self._clients[model_name]

    # 阅读注释（函数）：处理 contains 相关逻辑。
    def contains(self, model_name: str) -> bool:
        """处理 contains 相关逻辑。

        参数:
            model_name: 模型 名称，具体约束请结合类型标注和调用方确认。

        返回:
            bool
        """
        return model_name in self._clients

    # 阅读注释（函数）：处理 names 相关逻辑。
    def names(self) -> list[str]:
        """处理 names 相关逻辑。

        返回:
            list[str]

        阅读提示:
            主要直接调用：list。
        """
        return list(self._clients)
