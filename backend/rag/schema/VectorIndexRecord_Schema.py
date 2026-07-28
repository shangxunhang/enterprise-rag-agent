# -*- coding: utf-8 -*-
"""Chunk 与 Embedding/向量索引之间的血缘记录构造层。

v1 对应平面 ``chunk_v1``，v2 对应实际索引子块的父子检索数据。记录包含内容哈希、模型
版本、索引版本、向量库和生命周期字段；本模块不生成 Embedding，也不写入 Milvus。
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
    """把可选标量规范为字符串。"""
    if value is None:
        return default
    if isinstance(value, str):
        return value
    return str(value)


def safe_int(value: Any, default: int = -1) -> int:
    """尽力转换整数，非法值使用显式默认值。"""
    if value is None or value == "":
        return default
    try:
        return int(value)
    except Exception:
        return default


def safe_nullable_int(value: Any) -> Optional[int]:
    """尽力转换可空位置字段，非法值保留为 ``None``。"""
    if value is None or value == "":
        return None
    try:
        return int(value)
    except Exception:
        return None


def safe_list(value: Any) -> List[Any]:
    """把空值、元组或标量统一为列表。"""
    if value is None:
        return []
    if isinstance(value, list):
        return value
    if isinstance(value, tuple):
        return list(value)
    return [value]


def safe_bool(value: Any, default: bool = True) -> bool:
    """兼容布尔、常见字符串和其他真值输入。"""
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "y", "active"}
    return bool(value)


def sha256_text(text: str) -> str:
    """返回被索引正文的稳定内容哈希。"""
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
    """基于 Chunk 内容、Embedding 模型和索引版本生成可复现缓存/去重键。"""
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
    """为平面 ``chunk_v1`` 构造 v1 向量索引登记记录。"""
    record = deepcopy(VECTOR_INDEX_RECORD_V1_TEMPLATE)
    record.update({
        "chunk_id": safe_str(chunk.get("chunk_id")),
        "tenant_id": safe_str(chunk.get("tenant_id"), "default"),
        "kb_id": safe_str(chunk.get("kb_id"), "default"),
        "file_id": safe_str(chunk.get("file_id")),
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
    """为实际被索引的 ``child_chunk_v1`` 构造带父级和版本血缘的 v2 记录。"""
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
        "tenant_id": safe_str(child_chunk.get("tenant_id"), "default"),
        "kb_id": safe_str(child_chunk.get("kb_id"), "default"),
        "file_id": safe_str(child_chunk.get("file_id")),
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
