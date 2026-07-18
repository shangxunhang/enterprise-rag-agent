# -*- coding: utf-8 -*-
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


def safe_list(value: Any) -> List[Any]:
    if value is None:
        return []
    if isinstance(value, list):
        return value
    if isinstance(value, tuple):
        return list(value)
    return [value]


def safe_dict(value: Any) -> Dict[str, Any]:
    return value if isinstance(value, dict) else {}


def json_dumps_compact(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def truncate_text(text: str, max_chars: int) -> str:
    if len(text) <= max_chars:
        return text
    return text[:max_chars]


def build_milvus_schema_for_chunk_v1(dim: int, max_text_chars: int):
    schema = MilvusClient.create_schema(auto_id=False, enable_dynamic_field=False)
    schema.add_field(field_name="chunk_id", datatype=DataType.VARCHAR, is_primary=True, max_length=512)
    schema.add_field(field_name="vector", datatype=DataType.FLOAT_VECTOR, dim=dim)

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


def create_or_reset_chunk_collection(
    client: MilvusClient,
    collection_name: str,
    dim: int,
    metric_type: str,
    recreate: bool,
    max_text_chars: int,
) -> None:
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


def build_milvus_chunk_record(
    chunk: Dict[str, Any],
    vector: np.ndarray,
    embedding_model: str,
    embedding_dim: int,
    embedding_version: str,
    max_text_chars: int,
) -> Dict[str, Any]:
    return {
        "chunk_id": safe_str(chunk.get("chunk_id")),
        "vector": vector.astype(np.float32).tolist(),
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


def insert_records(
    client: MilvusClient,
    collection_name: str,
    records: Sequence[Dict[str, Any]],
    batch_size: int,
) -> int:
    total = 0
    for start in range(0, len(records), batch_size):
        batch = list(records[start: start + batch_size])
        result = client.insert(collection_name=collection_name, data=batch)
        total += len(batch)
        print(f"Inserted batch: {len(batch)}, total={total}, result={result}")
    return total


def search_smoke_test(
    client: MilvusClient,
    collection_name: str,
    query_vector: np.ndarray,
    top_k: int,
    metric_type: str,
) -> List[Dict[str, Any]]:
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
