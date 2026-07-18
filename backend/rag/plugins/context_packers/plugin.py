"""Concrete context-packer plugins.

These classes expose separate implementations to the registry. The legacy
``ContextPacker`` facade remains available for backward compatibility, but the
new composition root never switches on ``packing_strategy``.
"""

from __future__ import annotations

from typing import Any

from rag.context.context_packer import ContextPacker


class DefaultContextPacker(ContextPacker):
    plugin_name = "default"
    plugin_version = "v1"

    def __init__(self, *, build_context: Any = None, **params: Any) -> None:
        del build_context
        params.pop("packing_strategy", None)
        super().__init__(packing_strategy="default", **params)


class LostInMiddleContextPacker(ContextPacker):
    plugin_name = "lost_in_middle"
    plugin_version = "v1"

    def __init__(self, *, build_context: Any = None, **params: Any) -> None:
        del build_context
        params.pop("packing_strategy", None)
        super().__init__(packing_strategy="lost_in_middle_aware", **params)
