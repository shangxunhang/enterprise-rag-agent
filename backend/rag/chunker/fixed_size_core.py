"""两个历史 FixedSizeChunker import 路径共用的实际切分与偏移算法。

The project historically exposed two import paths with slightly different legacy
chunk metadata.  This module owns the actual splitting/offset algorithm while
thin adapters preserve both public behaviours.
"""
from __future__ import annotations

from typing import Dict, List, Literal

from rag.schema.Chunk_Schema import build_chunk
from rag.util.text_utils import split_text_by_fixed_size

SchemaStyle = Literal["document", "fields"]


def chunk_fixed_size(
    document: Dict,
    *,
    chunk_size: int,
    chunk_overlap: int,
    schema_style: SchemaStyle = "fields",
) -> List[Dict]:
    """按固定字符窗切分一个 Document，并映射到指定历史 Schema 风格。

    ``document`` 风格直接委托旧 ``build_chunk(doc=...)``；``fields`` 风格还会从上次游标
    向后定位正文偏移。重复文本无法可靠定位时，``start_char/end_char`` 保留为空，而不是
    猜测一个错误位置。
    """
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
