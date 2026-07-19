# =============================================================================
# 中文阅读说明：RAG 核心模块，负责查询变换、召回、融合、重排、证据评估和上下文组装。
# 主要定义：chunk_fixed_size。建议先从公开入口函数开始，再沿调用关系向下阅读。
# =============================================================================
"""Shared fixed-size chunking algorithm.

The project historically exposed two import paths with slightly different legacy
chunk metadata.  This module owns the actual splitting/offset algorithm while
thin adapters preserve both public behaviours.
"""
from __future__ import annotations

from typing import Dict, List, Literal

from rag.schema.Chunk_Schema import build_chunk
from rag.util.text_utils import split_text_by_fixed_size

SchemaStyle = Literal["document", "fields"]


# 阅读注释（函数）：处理 文本块 fixed size 相关逻辑。
def chunk_fixed_size(
    document: Dict,
    *,
    chunk_size: int,
    chunk_overlap: int,
    schema_style: SchemaStyle = "fields",
) -> List[Dict]:
    """Split one document and map pieces to the requested legacy schema style."""
    text = document.get("text", "")
    chunk_texts = split_text_by_fixed_size(
        text=text,
        chunk_size=chunk_size,
        chunk_overlap=chunk_overlap,
    )

    chunks: List[Dict] = []
    cursor = 0
    for idx, chunk_text in enumerate(chunk_texts):
        if schema_style == "document":
            chunks.append(
                build_chunk(
                    doc=document,
                    chunk_text=chunk_text,
                    chunk_index=idx,
                    chunk_type="fixed_size",
                )
            )
            continue

        start_char = text.find(chunk_text, cursor)
        if start_char < 0:
            start_char = None
            end_char = None
        else:
            end_char = start_char + len(chunk_text)
            cursor = max(start_char + 1, end_char - chunk_overlap)

        chunks.append(
            build_chunk(
                doc_id=document["doc_id"],
                text=chunk_text,
                idx=idx,
                doc_metadata=document.get("metadata", {}),
                chunk_type="fixed",
                start_char=start_char,
                end_char=end_char,
            )
        )

    return chunks
