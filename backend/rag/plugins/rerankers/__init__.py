"""真实 BGE Cross-Encoder 与显式 NoOp 父级重排插件。"""

from .plugin import (
    BGEParentCrossEncoderRerankerPlugin,
    NoOpParentRerankerPlugin,
)

__all__ = [
    "BGEParentCrossEncoderRerankerPlugin",
    "NoOpParentRerankerPlugin",
]
