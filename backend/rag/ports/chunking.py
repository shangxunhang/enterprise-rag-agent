# =============================================================================
# 中文阅读说明：RAG 核心模块，负责查询变换、召回、融合、重排、证据评估和上下文组装。
# 主要定义：ChunkerPort。建议先从公开入口函数开始，再沿调用关系向下阅读。
# =============================================================================
"""Stable contracts for offline chunk building."""
from __future__ import annotations

from typing import Any, Iterable, Protocol, runtime_checkable

from rag.chunker.ChildParentChunker import ParentChildChunkResult


# 阅读注释（类）：封装 chunker port，定义模块间调用契约，具体实现由适配器或插件提供。
@runtime_checkable
class ChunkerPort(Protocol):
    """Convert cleaned_text_unit_v1 records into parent/child chunks."""

    # 阅读注释（函数）：处理 文本块 记录集合 相关逻辑。
    def chunk_records(
        self,
        records: Iterable[dict[str, Any]],
    ) -> ParentChildChunkResult: ...

    # 阅读注释（函数）：处理 execution 元数据 相关逻辑。
    def execution_metadata(self) -> dict[str, Any]: ...
