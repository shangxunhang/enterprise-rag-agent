# -*- coding: utf-8 -*-
# =============================================================================
# 中文阅读说明：RAG 核心模块，负责查询变换、召回、融合、重排、证据评估和上下文组装。
# 主要定义：safe_str、safe_int、safe_list、safe_dict、json_dumps_compact、truncate_text、sha256_text、build_embedding_key、_json_field_length、build_milvus_schema_for_child_chunk_v1等。建议先从公开入口函数开始，再沿调用关系向下阅读。
# =============================================================================
"""
rag_template/vector_store/milvus_child_chunk_store.py
====================================================

Milvus Lite child_chunk_v1 向量存储层。

职责：
1. 创建适配 child_chunk_v1 的 Milvus collection schema。
2. 构造 Milvus 物理入库 record。
3. 批量 insert。
4. 可选 search smoke test。

设计约定：
- Milvus 只索引 child_chunk_v1。
- chunk_id == child_chunk_id，作为 Milvus primary key。
- parent_chunk_id 必须入 Milvus metadata，后续检索命中 child 后用它回填 parent。
- parent_chunk_v1 暂时不入向量库，落 JSONL/HDFS/MySQL 均可。
"""

from __future__ import annotations

import json
import hashlib
from typing import Any, Dict, List, Sequence

import numpy as np
from pymilvus import DataType, MilvusClient

from rag.configs.SchemaConfig import DEFAULT_INDEX_VERSION


DEFAULT_INDEXED_GRANULARITY = "child"


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


# 阅读注释（函数）：处理 safe 字典 相关逻辑。
def safe_dict(value: Any) -> Dict[str, Any]:
    """处理 safe 字典 相关逻辑。

    参数:
        value: value，具体约束请结合类型标注和调用方确认。

    返回:
        Dict[str, Any]

    阅读提示:
        主要直接调用：isinstance。
    """
    return value if isinstance(value, dict) else {}


# 阅读注释（函数）：处理 JSON dumps compact 相关逻辑。
def json_dumps_compact(value: Any) -> str:
    """处理 JSON dumps compact 相关逻辑。

    参数:
        value: value，具体约束请结合类型标注和调用方确认。

    返回:
        str

    阅读提示:
        主要直接调用：json.dumps。
    """
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


# 阅读注释（函数）：处理 truncate 文本 相关逻辑。
def truncate_text(text: str, max_chars: int) -> str:
    """处理 truncate 文本 相关逻辑。

    参数:
        text: 待处理文本。
        max_chars: max chars，具体约束请结合类型标注和调用方确认。

    返回:
        str

    阅读提示:
        主要直接调用：len。
    """
    if len(text) <= max_chars:
        return text
    return text[:max_chars]


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


# 阅读注释（函数）：处理 JSON field length 相关逻辑。
def _json_field_length(max_text_chars: int) -> int:
    # Milvus VARCHAR 最大 65535。给 JSON 字段保守一点，避免 schema 过大。
    """处理 JSON field length 相关逻辑。

    参数:
        max_text_chars: max 文本 chars，具体约束请结合类型标注和调用方确认。

    返回:
        int

    阅读提示:
        主要直接调用：min, max。
    """
    return min(max(max_text_chars, 4096), 65535)


# 阅读注释（函数）：构建 milvus Schema for 子块 文本块 v1。
def build_milvus_schema_for_child_chunk_v1(dim: int, max_text_chars: int):
    """Build physical Milvus schema for child_chunk_v1 indexing."""
    schema = MilvusClient.create_schema(auto_id=False, enable_dynamic_field=False)

    # Primary key / vector.
    schema.add_field(field_name="chunk_id", datatype=DataType.VARCHAR, is_primary=True, max_length=512)
    schema.add_field(field_name="vector", datatype=DataType.FLOAT_VECTOR, dim=int(dim))
    schema.add_field(field_name="vector_id", datatype=DataType.VARCHAR, max_length=512)
    schema.add_field(field_name="indexed_chunk_id", datatype=DataType.VARCHAR, max_length=512)

    # Parent-child linkage.
    schema.add_field(field_name="child_chunk_id", datatype=DataType.VARCHAR, max_length=512)
    schema.add_field(field_name="parent_chunk_id", datatype=DataType.VARCHAR, max_length=512)
    schema.add_field(field_name="doc_id", datatype=DataType.VARCHAR, max_length=512)
    schema.add_field(field_name="source_type", datatype=DataType.VARCHAR, max_length=64)
    schema.add_field(field_name="indexed_granularity", datatype=DataType.VARCHAR, max_length=64)

    # Child text and location.
    schema.add_field(field_name="text", datatype=DataType.VARCHAR, max_length=min(max_text_chars, 65535))
    schema.add_field(field_name="text_length", datatype=DataType.INT64)
    schema.add_field(field_name="token_count", datatype=DataType.INT64)
    schema.add_field(field_name="text_hash", datatype=DataType.VARCHAR, max_length=128)
    schema.add_field(field_name="source_unit_ids_json", datatype=DataType.VARCHAR, max_length=_json_field_length(max_text_chars))
    schema.add_field(field_name="title", datatype=DataType.VARCHAR, max_length=2048)
    schema.add_field(field_name="section", datatype=DataType.VARCHAR, max_length=2048)
    schema.add_field(field_name="section_level", datatype=DataType.INT64)
    schema.add_field(field_name="page_start", datatype=DataType.INT64)
    schema.add_field(field_name="page_end", datatype=DataType.INT64)

    # Child chunk positions.
    schema.add_field(field_name="child_chunk_index", datatype=DataType.INT64)
    schema.add_field(field_name="child_index_in_parent", datatype=DataType.INT64)
    schema.add_field(field_name="child_chunk_strategy", datatype=DataType.VARCHAR, max_length=512)
    schema.add_field(field_name="char_start_in_parent", datatype=DataType.INT64)
    schema.add_field(field_name="char_end_in_parent", datatype=DataType.INT64)

    # Versions / lineage.
    schema.add_field(field_name="cleaning_version", datatype=DataType.VARCHAR, max_length=256)
    schema.add_field(field_name="parent_chunk_version", datatype=DataType.VARCHAR, max_length=256)
    schema.add_field(field_name="child_chunk_version", datatype=DataType.VARCHAR, max_length=256)
    schema.add_field(field_name="chunk_created_at", datatype=DataType.VARCHAR, max_length=128)
    schema.add_field(field_name="extra_json", datatype=DataType.VARCHAR, max_length=_json_field_length(max_text_chars))
    schema.add_field(field_name="child_chunk_schema_version", datatype=DataType.VARCHAR, max_length=128)

    # Embedding metadata.
    schema.add_field(field_name="embedding_model", datatype=DataType.VARCHAR, max_length=1024)
    schema.add_field(field_name="embedding_dim", datatype=DataType.INT64)
    schema.add_field(field_name="embedding_version", datatype=DataType.VARCHAR, max_length=256)
    schema.add_field(field_name="embedding_key", datatype=DataType.VARCHAR, max_length=128)
    schema.add_field(field_name="index_version", datatype=DataType.VARCHAR, max_length=256)
    schema.add_field(field_name="is_active", datatype=DataType.BOOL)
    return schema


# 阅读注释（函数）：创建 or reset 子块 文本块 collection。
def create_or_reset_child_chunk_collection(
    client: MilvusClient,
    collection_name: str,
    dim: int,
    metric_type: str,
    recreate: bool,
    max_text_chars: int,
) -> None:
    """Create child_chunk_v1 collection; optionally drop existing one."""
    if client.has_collection(collection_name):
        if recreate:
            client.drop_collection(collection_name)
            print(f"Dropped existing collection: {collection_name}")
        else:
            print(f"Collection already exists: {collection_name}")
            return

    schema = build_milvus_schema_for_child_chunk_v1(dim=dim, max_text_chars=max_text_chars)
    index_params = client.prepare_index_params()
    index_params.add_index(
        field_name="vector",
        index_type="AUTOINDEX",
        metric_type=metric_type,
    )
    client.create_collection(
        collection_name=collection_name,
        schema=schema,
        index_params=index_params,
    )
    print(f"Collection created: {collection_name}, dim={dim}")
    print(f"Vector index created: AUTOINDEX / {metric_type}")


# 阅读注释（函数）：构建 milvus 子块 文本块 记录。
def build_milvus_child_chunk_record(
    child_chunk: Dict[str, Any],
    vector: np.ndarray,
    embedding_model: str,
    embedding_dim: int,
    embedding_version: str,
    max_text_chars: int,
    index_name: str = "",
    index_version: str = DEFAULT_INDEX_VERSION,
) -> Dict[str, Any]:
    """Convert child_chunk_v1 + vector into physical Milvus record."""
    child_chunk_id = safe_str(child_chunk.get("child_chunk_id") or child_chunk.get("chunk_id"))
    chunk_id = safe_str(child_chunk.get("chunk_id") or child_chunk_id)
    text = safe_str(child_chunk.get("text"))
    text_hash = safe_str(child_chunk.get("text_hash")) or sha256_text(text)
    index_version_value = index_version or DEFAULT_INDEX_VERSION
    embedding_key = build_embedding_key(
        indexed_chunk_id=child_chunk_id,
        text_hash=text_hash,
        embedding_model=embedding_model,
        embedding_version=embedding_version,
        index_name=index_name or "",
        index_version=index_version_value,
    )

    return {
        "chunk_id": chunk_id,
        "vector": vector.astype(np.float32).tolist(),
        "vector_id": chunk_id,
        "indexed_chunk_id": child_chunk_id,

        "child_chunk_id": child_chunk_id,
        "parent_chunk_id": safe_str(child_chunk.get("parent_chunk_id")),
        "doc_id": safe_str(child_chunk.get("doc_id")),
        "source_type": safe_str(child_chunk.get("source_type"), "offline"),
        "indexed_granularity": DEFAULT_INDEXED_GRANULARITY,

        "text": truncate_text(text, max_text_chars),
        "text_length": safe_int(child_chunk.get("text_length"), len(text)),
        "token_count": safe_int(child_chunk.get("token_count"), len(text)),
        "text_hash": text_hash,
        "source_unit_ids_json": json_dumps_compact(safe_list(child_chunk.get("source_unit_ids"))),
        "title": safe_str(child_chunk.get("title")),
        "section": safe_str(child_chunk.get("section")),
        "section_level": safe_int(child_chunk.get("section_level"), -1),
        "page_start": safe_int(child_chunk.get("page_start"), -1),
        "page_end": safe_int(child_chunk.get("page_end"), -1),

        "child_chunk_index": safe_int(child_chunk.get("child_chunk_index"), -1),
        "child_index_in_parent": safe_int(child_chunk.get("child_index_in_parent"), -1),
        "child_chunk_strategy": safe_str(child_chunk.get("child_chunk_strategy")),
        "char_start_in_parent": safe_int(child_chunk.get("char_start_in_parent"), -1),
        "char_end_in_parent": safe_int(child_chunk.get("char_end_in_parent"), -1),

        "cleaning_version": safe_str(child_chunk.get("cleaning_version")),
        "parent_chunk_version": safe_str(child_chunk.get("parent_chunk_version")),
        "child_chunk_version": safe_str(child_chunk.get("child_chunk_version")),
        "chunk_created_at": safe_str(child_chunk.get("created_at")),
        "extra_json": json_dumps_compact(safe_dict(child_chunk.get("extra"))),
        "child_chunk_schema_version": safe_str(child_chunk.get("schema_version"), "child_chunk_v1"),

        "embedding_model": embedding_model,
        "embedding_dim": int(embedding_dim),
        "embedding_version": embedding_version,
        "embedding_key": embedding_key,
        "index_version": index_version_value,
        "is_active": bool(child_chunk.get("is_active", True)),
    }


# 阅读注释（函数）：处理 insert 子块 文本块 记录集合 相关逻辑。
def insert_child_chunk_records(
    client: MilvusClient,
    collection_name: str,
    records: Sequence[Dict[str, Any]],
    batch_size: int,
) -> int:
    """处理 insert 子块 文本块 记录集合 相关逻辑。

    参数:
        client: 下游客户端。
        collection_name: collection 名称，具体约束请结合类型标注和调用方确认。
        records: 记录集合，具体约束请结合类型标注和调用方确认。
        batch_size: batch size，具体约束请结合类型标注和调用方确认。

    返回:
        int

    阅读提示:
        主要直接调用：range, len, list, client.insert, print。
    """
    total = 0
    for start in range(0, len(records), batch_size):
        batch = list(records[start:start + batch_size])
        result = client.insert(collection_name=collection_name, data=batch)
        total += len(batch)
        print(f"Inserted batch: {len(batch)}, total={total}, result={result}")
    return total


# 阅读注释（函数）：搜索 子块 文本块 smoke 测试。
def search_child_chunk_smoke_test(
    client: MilvusClient,
    collection_name: str,
    query_vector: np.ndarray,
    top_k: int,
    metric_type: str,
) -> List[Dict[str, Any]]:
    """Run a minimal search and return Milvus hits with parent_chunk_id output."""
    try:
        client.load_collection(collection_name)
    except Exception as exc:
        print(f"WARN: load_collection skipped or failed: {exc}")

    result = client.search(
        collection_name=collection_name,
        data=[query_vector.astype(np.float32).tolist()],
        anns_field="vector",
        limit=top_k,
        search_params={"metric_type": metric_type},
        output_fields=[
            "chunk_id",
            "vector_id",
            "indexed_chunk_id",
            "child_chunk_id",
            "parent_chunk_id",
            "doc_id",
            "source_type",
            "indexed_granularity",
            "text",
            "text_hash",
            "title",
            "section",
            "page_start",
            "page_end",
            "child_chunk_index",
            "child_index_in_parent",
            "child_chunk_version",
            "parent_chunk_version",
            "embedding_model",
            "embedding_version",
            "embedding_key",
            "index_version",
            "is_active",
        ],
    )
    if not result:
        return []
    return result[0]
