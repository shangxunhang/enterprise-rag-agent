# =============================================================================
# 中文阅读说明：RAG 核心模块，负责查询变换、召回、融合、重排、证据评估和上下文组装。
# 主要定义：DefaultContextPacker、LostInMiddleContextPacker。建议先从公开入口函数开始，再沿调用关系向下阅读。
# =============================================================================
"""Concrete context-packer plugins.

These classes expose separate implementations to the registry. The legacy
``ContextPacker`` facade remains available for backward compatibility, but the
new composition root never switches on ``packing_strategy``.
"""

from __future__ import annotations

from typing import Any

from rag.context.context_packer import ContextPacker


# 阅读注释（类）：封装 default 上下文 packer，集中封装相关状态、依赖和行为。
class DefaultContextPacker(ContextPacker):
    """封装 default 上下文 packer，集中封装相关状态、依赖和行为。"""
    plugin_name = "default"
    plugin_version = "v1"

    # 阅读注释（函数）：初始化 DefaultContextPacker，保存运行所需的依赖、配置或状态。
    def __init__(self, *, build_context: Any = None, **params: Any) -> None:
        """初始化 DefaultContextPacker，保存运行所需的依赖、配置或状态。

        参数:
            build_context: build 上下文，具体约束请结合类型标注和调用方确认。
            **params: params，具体约束请结合类型标注和调用方确认。

        返回:
            None

        阅读提示:
            主要直接调用：params.pop, __init__, super。
        """
        del build_context
        params.pop("packing_strategy", None)
        super().__init__(packing_strategy="default", **params)


# 阅读注释（类）：封装 lost in middle 上下文 packer，集中封装相关状态、依赖和行为。
class LostInMiddleContextPacker(ContextPacker):
    """封装 lost in middle 上下文 packer，集中封装相关状态、依赖和行为。"""
    plugin_name = "lost_in_middle"
    plugin_version = "v1"

    # 阅读注释（函数）：初始化 LostInMiddleContextPacker，保存运行所需的依赖、配置或状态。
    def __init__(self, *, build_context: Any = None, **params: Any) -> None:
        """初始化 LostInMiddleContextPacker，保存运行所需的依赖、配置或状态。

        参数:
            build_context: build 上下文，具体约束请结合类型标注和调用方确认。
            **params: params，具体约束请结合类型标注和调用方确认。

        返回:
            None

        阅读提示:
            主要直接调用：params.pop, __init__, super。
        """
        del build_context
        params.pop("packing_strategy", None)
        super().__init__(packing_strategy="lost_in_middle_aware", **params)
