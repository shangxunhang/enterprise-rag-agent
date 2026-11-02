"""注册表可构造的两种 Context Packer 插件。

These classes expose separate implementations to the registry. The legacy
``ContextPacker`` facade remains available for backward compatibility, but the
new composition root never switches on ``packing_strategy``.
"""

from __future__ import annotations

from typing import Any

from rag.context.context_packer import ContextPacker


class DefaultContextPacker(ContextPacker):
    """保持 Rerank 顺序的上下文打包插件。"""
    plugin_name = "default"
    plugin_version = "v1"

    def __init__(self, *, build_context: Any = None, **params: Any) -> None:
        """忽略构造上下文，并强制使用 ``default`` 策略防止配置覆盖插件身份。"""
        del build_context
        params.pop("packing_strategy", None)
        super().__init__(packing_strategy="default", **params)


class LostInMiddleContextPacker(ContextPacker):
    """将高价值证据放到 Prompt 两端的上下文打包插件。"""
    plugin_name = "lost_in_middle"
    plugin_version = "v1"

    def __init__(self, *, build_context: Any = None, **params: Any) -> None:
        """忽略构造上下文，并强制使用 ``lost_in_middle_aware`` 策略。"""
        del build_context
        params.pop("packing_strategy", None)
        super().__init__(packing_strategy="lost_in_middle_aware", **params)
