# -*- coding: utf-8 -*-
# =============================================================================
# 中文阅读说明：RAG 核心模块，负责查询变换、召回、融合、重排、证据评估和上下文组装。
# 主要定义：_safe_str、_safe_nullable_int、_safe_list、build_retrieval_result、build_retrieval_result_v2。建议先从公开入口函数开始，再沿调用关系向下阅读。
# =============================================================================
"""
rag/schema/Retrieval_Result_Schema.py
============================================

Retrieval result schema 构造层。

职责：
1. 保留 Mini-RAG / flat chunk 的 retrieval_result_v1 构造函数。
2. 增加父子块检索结果 retrieval_result_v2：child 命中，parent 回填。
3. 不负责向量检索、rerank、parent store 查询，只负责标准 dict 构造。
"""

from copy import deepcopy
from typing import Any, Dict, Optional

from rag.configs.SchemaConfig import (
    RETRIEVAL_RESULT_TEMPLATE,
    RETRIEVAL_RESULT_V2_TEMPLATE,
    CHUNK_METADATA_TEMPLATE,
    DEFAULT_SOURCE_TYPE,
    DEFAULT_VECTOR_DB,
    DEFAULT_EMBEDDING_VERSION,
)


# 阅读注释（函数）：处理 safe str 相关逻辑。
def _safe_str(value: Any, default: str = "") -> str:
    """处理 safe str 相关逻辑。

    参数:
        value: value，具体约束请结合类型标注和调用方确认。
        default: default，具体约束请结合类型标注和调用方确认。

    返回:
        str

    阅读提示:
        主要直接调用：isinstance, str。
    """
    if value is None:
        return default
    if isinstance(value, str):
        return value
    return str(value)


# 阅读注释（函数）：处理 safe nullable int 相关逻辑。
def _safe_nullable_int(value: Any) -> Optional[int]:
    """处理 safe nullable int 相关逻辑。

    参数:
        value: value，具体约束请结合类型标注和调用方确认。

    返回:
        Optional[int]

    阅读提示:
        主要直接调用：int。
    """
    if value is None or value == "":
        return None
    try:
        return int(value)
    except Exception:
        return None


# 阅读注释（函数）：处理 safe 列表 相关逻辑。
def _safe_list(value: Any) -> list:
    """处理 safe 列表 相关逻辑。

    参数:
        value: value，具体约束请结合类型标注和调用方确认。

    返回:
        list

    阅读提示:
        主要直接调用：isinstance, list。
    """
    if value is None:
        return []
    if isinstance(value, list):
        return value
    if isinstance(value, tuple):
        return list(value)
    return [value]


# 阅读注释（函数）：构建 检索 结果。
def build_retrieval_result(
    chunk: Dict[str, Any],
    rank: int,
    score: float,
    rerank_score: Optional[float] = None,
) -> Dict[str, Any]:
    """Build retrieval_result_v1 for flat chunk retrieval."""
    result = deepcopy(RETRIEVAL_RESULT_TEMPLATE)
    result["rank"] = rank
    result["score"] = score
    result["rerank_score"] = rerank_score
    result["chunk_id"] = chunk.get("chunk_id")
    result["doc_id"] = chunk.get("doc_id")
    result["text"] = chunk.get("text", "")
    result["metadata"] = chunk.get("metadata", deepcopy(CHUNK_METADATA_TEMPLATE))
    return result


# 阅读注释（函数）：构建 检索 结果 v2。
def build_retrieval_result_v2(
    *,
    child_chunk: Dict[str, Any],
    parent_chunk: Optional[Dict[str, Any]] = None,
    rank: int,
    score: float,
    rerank_score: Optional[float] = None,
    embedding_model: Optional[str] = None,
    embedding_version: str = DEFAULT_EMBEDDING_VERSION,
    index_name: Optional[str] = None,
    vector_db: str = DEFAULT_VECTOR_DB,
    context_granularity: str = "parent",
    metadata: Optional[Dict[str, Any]] = None,
    extra: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Build retrieval_result_v2 for parent-child RAG.

    约定：
    - 向量检索命中的粒度是 child。
    - prompt 默认使用 parent_text；如果 parent_chunk 缺失，则回退到 child_text。
    - chunk_id 必须等于 child_chunk_id，便于兼容通用向量库主键。
    """
    parent_chunk = parent_chunk or {}
    child_chunk_id = _safe_str(child_chunk.get("chunk_id") or child_chunk.get("child_chunk_id"))
    parent_chunk_id = _safe_str(
        child_chunk.get("parent_chunk_id") or parent_chunk.get("parent_chunk_id")
    )

    child_text = _safe_str(child_chunk.get("text"))
    parent_text = _safe_str(parent_chunk.get("text"))
    context_text = parent_text if context_granularity == "parent" and parent_text else child_text

    result = deepcopy(RETRIEVAL_RESULT_V2_TEMPLATE)
    result.update({
        "rank": rank,
        "score": score,
        "rerank_score": rerank_score,
        "chunk_id": child_chunk_id,
        "child_chunk_id": child_chunk_id,
        "parent_chunk_id": parent_chunk_id,
        "doc_id": _safe_str(child_chunk.get("doc_id") or parent_chunk.get("doc_id")),
        "matched_granularity": "child",
        "context_granularity": context_granularity,
        "child_text": child_text,
        "parent_text": parent_text,
        "text": context_text,
        "title": child_chunk.get("title") or parent_chunk.get("title"),
        "section": child_chunk.get("section") or parent_chunk.get("section"),
        "page_start": _safe_nullable_int(child_chunk.get("page_start") or parent_chunk.get("page_start")),
        "page_end": _safe_nullable_int(child_chunk.get("page_end") or parent_chunk.get("page_end")),
        "child_index_in_parent": _safe_nullable_int(child_chunk.get("child_index_in_parent")),
        "source_type": _safe_str(child_chunk.get("source_type") or parent_chunk.get("source_type"), DEFAULT_SOURCE_TYPE),
        "source_unit_ids": [
            _safe_str(x)
            for x in _safe_list(child_chunk.get("source_unit_ids") or parent_chunk.get("source_unit_ids"))
        ],
        "cleaning_version": _safe_str(child_chunk.get("cleaning_version") or parent_chunk.get("cleaning_version")),
        "parent_chunk_version": _safe_str(child_chunk.get("parent_chunk_version") or parent_chunk.get("parent_chunk_version")),
        "child_chunk_version": _safe_str(child_chunk.get("child_chunk_version")),
        "embedding_model": embedding_model,
        "embedding_version": embedding_version,
        "index_name": index_name,
        "vector_db": vector_db,
        "metadata": metadata or {},
        "extra": extra or {},
    })
    return result
