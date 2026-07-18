# -*- coding: utf-8 -*-
"""
rag_template/schema/VectorIndexRecord_Schema.py
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


def safe_str(value: Any, default: str = "") -> str:
    if value is None:
        return default
    if isinstance(value, str):
        return value
    return str(value)


def safe_int(value: Any, default: int = -1) -> int:
    if value is None or value == "":
        return default
    try:
        return int(value)
    except Exception:
        return default


def safe_nullable_int(value: Any) -> Optional[int]:
    if value is None or value == "":
        return None
    try:
        return int(value)
    except Exception:
        return None


def safe_list(value: Any) -> List[Any]:
    if value is None:
        return []
    if isinstance(value, list):
        return value
    if isinstance(value, tuple):
        return list(value)
    return [value]


def safe_bool(value: Any, default: bool = True) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "y", "active"}
    return bool(value)


def sha256_text(text: str) -> str:
    return hashlib.sha256((text or "").encode("utf-8")).hexdigest()


def build_embedding_key(
    *,
    indexed_chunk_id: str,
    text_hash: str,
    embedding_model: str,
    embedding_version: str,
    index_name: str,
    index_version: str,
) -> str:
    raw = "|".join([
        indexed_chunk_id or "",
        text_hash or "",
        embedding_model or "",
        embedding_version or "",
        index_name or "",
        index_version or "",
    ])
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


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
