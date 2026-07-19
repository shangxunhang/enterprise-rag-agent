# -*- coding: utf-8 -*-
# =============================================================================
# 中文阅读说明：RAG 核心模块，负责查询变换、召回、融合、重排、证据评估和上下文组装。
# 主要定义：safe_str、safe_int、safe_nullable_int、safe_list、safe_bool、sha256_text、build_embedding_key、build_vector_index_record_v1、build_vector_index_record_v2。建议先从公开入口函数开始，再沿调用关系向下阅读。
# =============================================================================
"""
rag/schema/VectorIndexRecord_Schema.py
===============================================

向量索引登记记录 schema 构造层。

职责：
1. 基于 SchemaConfig 中的 VECTOR_INDEX_RECORD_V1_TEMPLATE / V2_TEMPLATE 构造标准记录
2. 记录 chunk 与 embedding / vector index 之间的血缘关系
3. 不负责 embedding 生成，也不负责 Milvus 写入
"""

from copy import deepcopy
import hashlib
from typing import Any, Dict, List, Optional

from rag.configs.SchemaConfig import (
    VECTOR_INDEX_RECORD_V1_TEMPLATE,
    VECTOR_INDEX_RECORD_V2_TEMPLATE,
    DEFAULT_VECTOR_DB,
    DEFAULT_EMBEDDING_VERSION,
    DEFAULT_INDEXED_GRANULARITY,
    DEFAULT_INDEX_VERSION,
    current_time_str,
)


# 阅读注释（函数）：处理 safe str 相关逻辑。
def safe_str(value: Any, default: str = "") -> str:
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


# 阅读注释（函数）：处理 safe int 相关逻辑。
def safe_int(value: Any, default: int = -1) -> int:
    """处理 safe int 相关逻辑。

    参数:
        value: value，具体约束请结合类型标注和调用方确认。
        default: default，具体约束请结合类型标注和调用方确认。

    返回:
        int

    阅读提示:
        主要直接调用：int。
    """
    if value is None or value == "":
        return default
    try:
        return int(value)
    except Exception:
        return default


# 阅读注释（函数）：处理 safe nullable int 相关逻辑。
def safe_nullable_int(value: Any) -> Optional[int]:
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
def safe_list(value: Any) -> List[Any]:
    """处理 safe 列表 相关逻辑。

    参数:
        value: value，具体约束请结合类型标注和调用方确认。

    返回:
        List[Any]

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


# 阅读注释（函数）：处理 safe bool 相关逻辑。
def safe_bool(value: Any, default: bool = True) -> bool:
    """处理 safe bool 相关逻辑。

    参数:
        value: value，具体约束请结合类型标注和调用方确认。
        default: default，具体约束请结合类型标注和调用方确认。

    返回:
        bool

    阅读提示:
        主要直接调用：isinstance, lower, value.strip, bool。
    """
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "y", "active"}
    return bool(value)


# 阅读注释（函数）：处理 sha256 文本 相关逻辑。
def sha256_text(text: str) -> str:
    """处理 sha256 文本 相关逻辑。

    参数:
        text: 待处理文本。

    返回:
        str

    阅读提示:
        主要直接调用：hexdigest, hashlib.sha256, encode。
    """
    return hashlib.sha256((text or "").encode("utf-8")).hexdigest()


# 阅读注释（函数）：构建 embedding key。
def build_embedding_key(
    *,
    indexed_chunk_id: str,
    text_hash: str,
    embedding_model: str,
    embedding_version: str,
    index_name: str,
    index_version: str,
) -> str:
    """构建 embedding key。

    参数:
        indexed_chunk_id: indexed 文本块 标识，具体约束请结合类型标注和调用方确认。
        text_hash: 文本 hash，具体约束请结合类型标注和调用方确认。
        embedding_model: embedding 模型，具体约束请结合类型标注和调用方确认。
        embedding_version: embedding 版本，具体约束请结合类型标注和调用方确认。
        index_name: 索引 名称，具体约束请结合类型标注和调用方确认。
        index_version: 索引 版本，具体约束请结合类型标注和调用方确认。

    返回:
        str

    阅读提示:
        主要直接调用：join, hexdigest, hashlib.sha256, raw.encode。
    """
    raw = "|".join([
        indexed_chunk_id or "",
        text_hash or "",
        embedding_model or "",
        embedding_version or "",
        index_name or "",
        index_version or "",
    ])
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


# 阅读注释（函数）：构建 vector 索引 记录 v1。
def build_vector_index_record_v1(
    *,
    chunk: Dict[str, Any],
    embedding_model: str,
    embedding_dim: int,
    index_name: str,
    vector_db: str = DEFAULT_VECTOR_DB,
    embedding_version: str = DEFAULT_EMBEDDING_VERSION,
    created_at: Optional[str] = None,
    extra: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Build vector_index_record_v1 for flat chunk_v1 indexing."""
    record = deepcopy(VECTOR_INDEX_RECORD_V1_TEMPLATE)
    record.update({
        "chunk_id": safe_str(chunk.get("chunk_id")),
        "doc_id": safe_str(chunk.get("doc_id")),
        "source_type": safe_str(chunk.get("source_type"), "offline"),
        "embedding_model": embedding_model,
        "embedding_dim": int(embedding_dim),
        "index_name": index_name,
        "vector_db": vector_db,
        "title": chunk.get("title"),
        "section": chunk.get("section"),
        "page_start": safe_nullable_int(chunk.get("page_start")),
        "page_end": safe_nullable_int(chunk.get("page_end")),
        "source_unit_ids": [safe_str(x) for x in safe_list(chunk.get("source_unit_ids"))],
        "cleaning_version": safe_str(chunk.get("cleaning_version")),
        "chunk_version": safe_str(chunk.get("chunk_version")),
        "embedding_version": embedding_version,
        "created_at": created_at or current_time_str(),
        "extra": extra or {},
    })
    return record


# 阅读注释（函数）：构建 vector 索引 记录 v2。
def build_vector_index_record_v2(
    *,
    child_chunk: Dict[str, Any],
    embedding_model: str,
    embedding_dim: int,
    index_name: str,
    vector_db: str = DEFAULT_VECTOR_DB,
    embedding_version: str = DEFAULT_EMBEDDING_VERSION,
    index_version: str = DEFAULT_INDEX_VERSION,
    indexed_granularity: str = DEFAULT_INDEXED_GRANULARITY,
    vector_id: Optional[str] = None,
    created_at: Optional[str] = None,
    updated_at: Optional[str] = None,
    deleted_at: Optional[str] = None,
    extra: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Build vector_index_record_v2 for child_chunk_v1 indexing."""
    record = deepcopy(VECTOR_INDEX_RECORD_V2_TEMPLATE)
    chunk_id = safe_str(child_chunk.get("chunk_id") or child_chunk.get("child_chunk_id"))
    indexed_chunk_id = chunk_id
    vector_id_value = vector_id or indexed_chunk_id
    text = safe_str(child_chunk.get("text"))
    text_hash = safe_str(child_chunk.get("text_hash")) or sha256_text(text)
    index_version_value = index_version or DEFAULT_INDEX_VERSION
    embedding_key = build_embedding_key(
        indexed_chunk_id=indexed_chunk_id,
        text_hash=text_hash,
        embedding_model=embedding_model,
        embedding_version=embedding_version,
        index_name=index_name,
        index_version=index_version_value,
    )

    record.update({
        "vector_id": vector_id_value,
        "indexed_chunk_id": indexed_chunk_id,
        "chunk_id": chunk_id,
        "child_chunk_id": chunk_id,
        "parent_chunk_id": safe_str(child_chunk.get("parent_chunk_id")),
        "doc_id": safe_str(child_chunk.get("doc_id")),
        "source_type": safe_str(child_chunk.get("source_type"), "offline"),
        "indexed_granularity": indexed_granularity,
        "text_hash": text_hash,
        "embedding_key": embedding_key,
        "embedding_model": embedding_model,
        "embedding_dim": int(embedding_dim),
        "embedding_version": embedding_version,
        "index_name": index_name,
        "index_version": index_version_value,
        "vector_db": vector_db,
        "title": child_chunk.get("title"),
        "section": child_chunk.get("section"),
        "section_level": safe_nullable_int(child_chunk.get("section_level")),
        "page_start": safe_nullable_int(child_chunk.get("page_start")),
        "page_end": safe_nullable_int(child_chunk.get("page_end")),
        "child_index_in_parent": safe_nullable_int(child_chunk.get("child_index_in_parent")),
        "source_unit_ids": [safe_str(x) for x in safe_list(child_chunk.get("source_unit_ids"))],
        "cleaning_version": safe_str(child_chunk.get("cleaning_version")),
        "parent_chunk_version": safe_str(child_chunk.get("parent_chunk_version")),
        "child_chunk_version": safe_str(child_chunk.get("child_chunk_version")),
        "is_active": safe_bool(child_chunk.get("is_active"), True),
        "created_at": created_at or current_time_str(),
        "updated_at": updated_at,
        "deleted_at": deleted_at,
        "extra": extra or {},
    })
    return record
