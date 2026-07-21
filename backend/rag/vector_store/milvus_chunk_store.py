# -*- coding: utf-8 -*-
# =============================================================================
# 中文阅读说明：RAG 核心模块，负责查询变换、召回、融合、重排、证据评估和上下文组装。
# 主要定义：safe_str、safe_int、safe_list、safe_dict、json_dumps_compact、truncate_text、build_milvus_schema_for_chunk_v1、create_or_reset_chunk_collection、build_milvus_chunk_record、insert_records等。建议先从公开入口函数开始，再沿调用关系向下阅读。
# =============================================================================
"""
rag_template/vector_store/milvus_chunk_store.py
==============================================

Milvus Lite chunk 向量存储层。

职责：
1. 创建适配 chunk_v1 的 Milvus collection schema
2. 构造 Milvus 物理入库 record
3. 批量 insert
4. 可选 search smoke test

不负责：
1. JSONL 读取
2. embedding 生成
3. vector_index_record 构造
"""

from typing import Any, Dict, List, Sequence, Optional
import json

import numpy as np
from pymilvus import DataType, MilvusClient


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


# 阅读注释（函数）：构建 milvus Schema for 文本块 v1。
def build_milvus_schema_for_chunk_v1(dim: int, max_text_chars: int):
    """构建 milvus Schema for 文本块 v1。

    参数:
        dim: dim，具体约束请结合类型标注和调用方确认。
        max_text_chars: max 文本 chars，具体约束请结合类型标注和调用方确认。

    返回:
        未显式标注；请结合调用方和实际返回语句理解。

    阅读提示:
        主要直接调用：MilvusClient.create_schema, schema.add_field, min。
    """
    schema = MilvusClient.create_schema(auto_id=False, enable_dynamic_field=False)
    schema.add_field(field_name="chunk_id", datatype=DataType.VARCHAR, is_primary=True, max_length=512)
    schema.add_field(field_name="vector", datatype=DataType.FLOAT_VECTOR, dim=dim)

    schema.add_field(field_name="tenant_id", datatype=DataType.VARCHAR, max_length=256)
    schema.add_field(field_name="kb_id", datatype=DataType.VARCHAR, max_length=512)
    schema.add_field(field_name="file_id", datatype=DataType.VARCHAR, max_length=512)
    schema.add_field(field_name="doc_id", datatype=DataType.VARCHAR, max_length=512)
    schema.add_field(field_name="source_type", datatype=DataType.VARCHAR, max_length=64)
    schema.add_field(field_name="text", datatype=DataType.VARCHAR, max_length=min(max_text_chars, 65535))
    schema.add_field(field_name="text_length", datatype=DataType.INT64)
    schema.add_field(field_name="token_count", datatype=DataType.INT64)
    schema.add_field(field_name="source_unit_ids_json", datatype=DataType.VARCHAR, max_length=65535)
    schema.add_field(field_name="title", datatype=DataType.VARCHAR, max_length=2048)
    schema.add_field(field_name="section", datatype=DataType.VARCHAR, max_length=2048)
    schema.add_field(field_name="section_level", datatype=DataType.INT64)
    schema.add_field(field_name="page_start", datatype=DataType.INT64)
    schema.add_field(field_name="page_end", datatype=DataType.INT64)
    schema.add_field(field_name="chunk_index", datatype=DataType.INT64)
    schema.add_field(field_name="chunk_strategy", datatype=DataType.VARCHAR, max_length=512)
    schema.add_field(field_name="cleaning_version", datatype=DataType.VARCHAR, max_length=256)
    schema.add_field(field_name="chunk_version", datatype=DataType.VARCHAR, max_length=256)
    schema.add_field(field_name="chunk_created_at", datatype=DataType.VARCHAR, max_length=128)
    schema.add_field(field_name="extra_json", datatype=DataType.VARCHAR, max_length=65535)
    schema.add_field(field_name="chunk_schema_version", datatype=DataType.VARCHAR, max_length=128)
    schema.add_field(field_name="input_schema_version", datatype=DataType.VARCHAR, max_length=128)

    schema.add_field(field_name="embedding_model", datatype=DataType.VARCHAR, max_length=1024)
    schema.add_field(field_name="embedding_dim", datatype=DataType.INT64)
    schema.add_field(field_name="embedding_version", datatype=DataType.VARCHAR, max_length=256)
    return schema


# 阅读注释（函数）：创建 or reset 文本块 collection。
def create_or_reset_chunk_collection(
    client: MilvusClient,
    collection_name: str,
    dim: int,
    metric_type: str,
    recreate: bool,
    max_text_chars: int,
) -> None:
    """创建 or reset 文本块 collection。

    参数:
        client: 下游客户端。
        collection_name: collection 名称，具体约束请结合类型标注和调用方确认。
        dim: dim，具体约束请结合类型标注和调用方确认。
        metric_type: 指标 类型，具体约束请结合类型标注和调用方确认。
        recreate: recreate，具体约束请结合类型标注和调用方确认。
        max_text_chars: max 文本 chars，具体约束请结合类型标注和调用方确认。

    返回:
        None

    阅读提示:
        主要直接调用：client.has_collection, client.drop_collection, print, build_milvus_schema_for_chunk_v1, client.prepare_index_params, index_params.add_index, client.create_collection。
    """
    if client.has_collection(collection_name):
        if recreate:
            client.drop_collection(collection_name)
            print(f"Dropped existing collection: {collection_name}")
        else:
            print(f"Collection already exists: {collection_name}")
            return

    schema = build_milvus_schema_for_chunk_v1(dim=dim, max_text_chars=max_text_chars)
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


# 阅读注释（函数）：构建 milvus 文本块 记录。
def build_milvus_chunk_record(
    chunk: Dict[str, Any],
    vector: np.ndarray,
    embedding_model: str,
    embedding_dim: int,
    embedding_version: str,
    max_text_chars: int,
) -> Dict[str, Any]:
    """构建 milvus 文本块 记录。

    参数:
        chunk: 文本块，具体约束请结合类型标注和调用方确认。
        vector: vector，具体约束请结合类型标注和调用方确认。
        embedding_model: embedding 模型，具体约束请结合类型标注和调用方确认。
        embedding_dim: embedding dim，具体约束请结合类型标注和调用方确认。
        embedding_version: embedding 版本，具体约束请结合类型标注和调用方确认。
        max_text_chars: max 文本 chars，具体约束请结合类型标注和调用方确认。

    返回:
        Dict[str, Any]

    阅读提示:
        主要直接调用：safe_str, chunk.get, tolist, vector.astype, truncate_text, safe_int, len, json_dumps_compact。
    """
    return {
        "chunk_id": safe_str(chunk.get("chunk_id")),
        "vector": vector.astype(np.float32).tolist(),
        "tenant_id": safe_str(chunk.get("tenant_id"), "default"),
        "kb_id": safe_str(chunk.get("kb_id"), "default"),
        "file_id": safe_str(chunk.get("file_id")),
        "doc_id": safe_str(chunk.get("doc_id")),
        "source_type": safe_str(chunk.get("source_type"), "offline"),
        "text": truncate_text(safe_str(chunk.get("text")), max_text_chars),
        "text_length": safe_int(chunk.get("text_length"), len(safe_str(chunk.get("text")))),
        "token_count": safe_int(chunk.get("token_count"), len(safe_str(chunk.get("text")))),
        "source_unit_ids_json": json_dumps_compact(safe_list(chunk.get("source_unit_ids"))),
        "title": safe_str(chunk.get("title")),
        "section": safe_str(chunk.get("section")),
        "section_level": safe_int(chunk.get("section_level"), -1),
        "page_start": safe_int(chunk.get("page_start"), -1),
        "page_end": safe_int(chunk.get("page_end"), -1),
        "chunk_index": safe_int(chunk.get("chunk_index"), -1),
        "chunk_strategy": safe_str(chunk.get("chunk_strategy")),
        "cleaning_version": safe_str(chunk.get("cleaning_version")),
        "chunk_version": safe_str(chunk.get("chunk_version")),
        "chunk_created_at": safe_str(chunk.get("created_at")),
        "extra_json": json_dumps_compact(safe_dict(chunk.get("extra"))),
        "chunk_schema_version": safe_str(chunk.get("schema_version"), "chunk_v1"),
        "input_schema_version": safe_str(chunk.get("input_schema_version"), "chunk_v1"),
        "embedding_model": embedding_model,
        "embedding_dim": int(embedding_dim),
        "embedding_version": embedding_version,
    }


# 阅读注释（函数）：处理 insert 记录集合 相关逻辑。
def insert_records(
    client: MilvusClient,
    collection_name: str,
    records: Sequence[Dict[str, Any]],
    batch_size: int,
) -> int:
    """处理 insert 记录集合 相关逻辑。

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
        batch = list(records[start: start + batch_size])
        result = client.insert(collection_name=collection_name, data=batch)
        total += len(batch)
        print(f"Inserted batch: {len(batch)}, total={total}, result={result}")
    return total


# 阅读注释（函数）：搜索 smoke 测试。
def search_smoke_test(
    client: MilvusClient,
    collection_name: str,
    query_vector: np.ndarray,
    top_k: int,
    metric_type: str,
) -> List[Dict[str, Any]]:
    """搜索 smoke 测试。

    参数:
        client: 下游客户端。
        collection_name: collection 名称，具体约束请结合类型标注和调用方确认。
        query_vector: 查询 vector，具体约束请结合类型标注和调用方确认。
        top_k: top k，具体约束请结合类型标注和调用方确认。
        metric_type: 指标 类型，具体约束请结合类型标注和调用方确认。

    返回:
        List[Dict[str, Any]]

    阅读提示:
        主要直接调用：client.load_collection, print, client.search, tolist, query_vector.astype。
    """
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
            "tenant_id",
            "kb_id",
            "file_id",
            "doc_id",
            "source_type",
            "text",
            "title",
            "section",
            "page_start",
            "page_end",
            "chunk_index",
            "chunk_version",
            "embedding_model",
            "embedding_version",
        ],
    )
    if not result:
        return []
    return result[0]
