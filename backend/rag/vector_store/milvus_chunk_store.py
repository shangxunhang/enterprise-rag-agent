# -*- coding: utf-8 -*-
"""把旧版扁平 Chunk 与向量写入 Milvus 集合。

该实现服务普通 ``chunk_v1`` 索引，不表达父子块关系。它定义显式集合 Schema、向量
索引参数、Chunk 到物理记录的映射和批量插入。新的父子块主链应使用
``milvus_child_chunk_store``，避免把父级上下文和子级匹配字段混入扁平集合。
"""

from __future__ import annotations

import json
from typing import Any, Dict, List, Sequence

import numpy as np

from rag.configs.SchemaConfig import (
    DEFAULT_CHUNK_SCHEMA_VERSION,
    DEFAULT_EMBEDDING_VERSION,
    DEFAULT_STATUS,
)


# Milvus VARCHAR 字段长度上限。过长值在物理映射阶段截断。
DEFAULT_ID_MAX_LENGTH = 512
DEFAULT_DOC_ID_MAX_LENGTH = 512
DEFAULT_FILE_ID_MAX_LENGTH = 512
DEFAULT_TENANT_ID_MAX_LENGTH = 256
DEFAULT_KB_ID_MAX_LENGTH = 256
DEFAULT_TITLE_MAX_LENGTH = 2048
DEFAULT_SECTION_MAX_LENGTH = 2048
DEFAULT_SOURCE_MAX_LENGTH = 2048
DEFAULT_SECURITY_LEVEL_MAX_LENGTH = 64
DEFAULT_STATUS_MAX_LENGTH = 64
DEFAULT_MODEL_MAX_LENGTH = 1024
DEFAULT_VERSION_MAX_LENGTH = 256
DEFAULT_INDEX_NAME_MAX_LENGTH = 512
DEFAULT_EXTRA_JSON_MAX_LENGTH = 16384


def _safe_str(
    value: Any,
    *,
    max_length: int | None = None,
) -> str:
    """把可选值转换为字符串，并按字段上限截断。"""

    text = "" if value is None else str(value)
    if max_length is not None and len(text) > max_length:
        text = text[:max_length]
    return text


def _safe_int(value: Any, default: int = -1) -> int:
    """尽力转换整数，失败时返回默认值。"""

    try:
        if value is None or value == "":
            return default
        return int(value)
    except Exception:
        return default


def _safe_float_list(
    vector: Any,
    expected_dim: int,
) -> List[float]:
    """把向量转换为一维 float32 列表并校验维度。

    Raises:
        ValueError: 实际向量长度与集合维度不一致时抛出。
    """

    # 第一步：统一为一维 float32 数组。
    array = np.asarray(vector, dtype=np.float32).reshape(-1)

    # 第二步：维度不匹配会破坏 Milvus Schema，禁止截断或补零。
    if array.shape[0] != expected_dim:
        raise ValueError(
            f"Vector dim mismatch: expected={expected_dim}, "
            f"actual={array.shape[0]}"
        )
    return array.tolist()


def _extra_json(extra: Any) -> str:
    """把扩展 metadata 序列化为确定性紧凑 JSON 并限制长度。"""

    payload = extra if isinstance(extra, dict) else {}
    text = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    )
    return _safe_str(
        text,
        max_length=DEFAULT_EXTRA_JSON_MAX_LENGTH,
    )


def build_chunk_collection_schema(
    client: Any,
    *,
    dim: int,
    max_text_chars: int = 8192,
):
    """构造旧版扁平 Chunk 的 Milvus Schema。

    Args:
        client: 提供 ``create_schema`` 的 MilvusClient。
        dim: 向量维度。
        max_text_chars: Chunk 正文最大字符数。

    Returns:
        包含 Chunk 身份、检索作用域、来源、位置、Embedding 血缘和向量字段的 Schema。
    """

    from pymilvus import DataType

    # 第一步：禁用动态字段，所有物理字段必须在此显式声明。
    schema = client.create_schema(
        auto_id=False,
        enable_dynamic_field=False,
    )

    # 第二步：chunk_id 是调用方生成的稳定主键。
    schema.add_field(
        field_name="chunk_id",
        datatype=DataType.VARCHAR,
        is_primary=True,
        max_length=DEFAULT_ID_MAX_LENGTH,
    )

    # 第三步：持久化租户、知识库、文件和文档作用域，供在线强制过滤。
    schema.add_field(
        field_name="doc_id",
        datatype=DataType.VARCHAR,
        max_length=DEFAULT_DOC_ID_MAX_LENGTH,
    )
    schema.add_field(
        field_name="file_id",
        datatype=DataType.VARCHAR,
        max_length=DEFAULT_FILE_ID_MAX_LENGTH,
    )
    schema.add_field(
        field_name="tenant_id",
        datatype=DataType.VARCHAR,
        max_length=DEFAULT_TENANT_ID_MAX_LENGTH,
    )
    schema.add_field(
        field_name="kb_id",
        datatype=DataType.VARCHAR,
        max_length=DEFAULT_KB_ID_MAX_LENGTH,
    )

    # 第四步：正文和展示来源字段用于检索返回与引用展示。
    schema.add_field(
        field_name="text",
        datatype=DataType.VARCHAR,
        max_length=max_text_chars,
    )
    schema.add_field(
        field_name="title",
        datatype=DataType.VARCHAR,
        max_length=DEFAULT_TITLE_MAX_LENGTH,
    )
    schema.add_field(
        field_name="section",
        datatype=DataType.VARCHAR,
        max_length=DEFAULT_SECTION_MAX_LENGTH,
    )
    schema.add_field(
        field_name="section_level",
        datatype=DataType.INT64,
    )
    schema.add_field(
        field_name="source",
        datatype=DataType.VARCHAR,
        max_length=DEFAULT_SOURCE_MAX_LENGTH,
    )

    # 第五步：页码和字符偏移支持来源定位。
    schema.add_field(
        field_name="page_start",
        datatype=DataType.INT64,
    )
    schema.add_field(
        field_name="page_end",
        datatype=DataType.INT64,
    )
    schema.add_field(
        field_name="start_char",
        datatype=DataType.INT64,
    )
    schema.add_field(
        field_name="end_char",
        datatype=DataType.INT64,
    )
    schema.add_field(
        field_name="token_count",
        datatype=DataType.INT64,
    )
    schema.add_field(
        field_name="chunk_index",
        datatype=DataType.INT64,
    )
    schema.add_field(
        field_name="chunk_level",
        datatype=DataType.INT64,
    )

    # 第六步：保留协议、状态、安全等级和 Embedding/索引血缘。
    schema.add_field(
        field_name="schema_version",
        datatype=DataType.VARCHAR,
        max_length=DEFAULT_VERSION_MAX_LENGTH,
    )
    schema.add_field(
        field_name="status",
        datatype=DataType.VARCHAR,
        max_length=DEFAULT_STATUS_MAX_LENGTH,
    )
    schema.add_field(
        field_name="security_level",
        datatype=DataType.VARCHAR,
        max_length=DEFAULT_SECURITY_LEVEL_MAX_LENGTH,
    )
    schema.add_field(
        field_name="embedding_model",
        datatype=DataType.VARCHAR,
        max_length=DEFAULT_MODEL_MAX_LENGTH,
    )
    schema.add_field(
        field_name="embedding_version",
        datatype=DataType.VARCHAR,
        max_length=DEFAULT_VERSION_MAX_LENGTH,
    )
    schema.add_field(
        field_name="embedding_dim",
        datatype=DataType.INT64,
    )
    schema.add_field(
        field_name="index_name",
        datatype=DataType.VARCHAR,
        max_length=DEFAULT_INDEX_NAME_MAX_LENGTH,
    )
    schema.add_field(
        field_name="index_version",
        datatype=DataType.VARCHAR,
        max_length=DEFAULT_VERSION_MAX_LENGTH,
    )

    # 第七步：扩展字段保存为 JSON 字符串，避免动态 Schema 漂移。
    schema.add_field(
        field_name="extra_json",
        datatype=DataType.VARCHAR,
        max_length=DEFAULT_EXTRA_JSON_MAX_LENGTH,
    )

    # 第八步：向量字段维度必须与 Embedding 配置一致。
    schema.add_field(
        field_name="vector",
        datatype=DataType.FLOAT_VECTOR,
        dim=dim,
    )
    return schema


def create_or_reset_chunk_collection(
    client: Any,
    *,
    collection_name: str,
    dim: int,
    metric_type: str = "COSINE",
    recreate: bool = True,
    max_text_chars: int = 8192,
) -> None:
    """创建或按配置重建旧版扁平 Chunk 集合。"""

    # 第一步：集合已存在时根据 recreate 决定删除重建或直接复用。
    if client.has_collection(collection_name):
        if recreate:
            client.drop_collection(collection_name)
        else:
            return

    # 第二步：构造显式集合 Schema。
    schema = build_chunk_collection_schema(
        client,
        dim=dim,
        max_text_chars=max_text_chars,
    )

    # 第三步：为 vector 创建自动索引并绑定度量类型。
    index_params = client.prepare_index_params()
    index_params.add_index(
        field_name="vector",
        metric_type=metric_type,
        index_type="AUTOINDEX",
    )

    # 第四步：创建集合。
    client.create_collection(
        collection_name=collection_name,
        schema=schema,
        index_params=index_params,
    )


def build_milvus_chunk_record(
    *,
    chunk: Dict[str, Any],
    vector: Any,
    embedding_model: str,
    embedding_dim: int,
    embedding_version: str = DEFAULT_EMBEDDING_VERSION,
    max_text_chars: int = 8192,
    index_name: str | None = None,
    index_version: str | None = None,
) -> Dict[str, Any]:
    """把普通 Chunk 和同一行向量映射为 Milvus 物理记录。

    Raises:
        ValueError: chunk_id/doc_id 为空或向量维度不匹配时抛出。
    """

    # 第一步：主键和文档身份必须存在。
    chunk_id = _safe_str(
        chunk.get("chunk_id"),
        max_length=DEFAULT_ID_MAX_LENGTH,
    )
    if not chunk_id:
        raise ValueError("chunk_id cannot be empty")

    doc_id = _safe_str(
        chunk.get("doc_id"),
        max_length=DEFAULT_DOC_ID_MAX_LENGTH,
    )
    if not doc_id:
        raise ValueError("doc_id cannot be empty")

    # 第二步：统一页码兼容字段。
    page_num = _safe_int(chunk.get("page_num"), -1)
    page_start = _safe_int(
        chunk.get("page_start"),
        page_num,
    )
    page_end = _safe_int(
        chunk.get("page_end"),
        page_num,
    )

    # 第三步：构造与集合 Schema 完全一致的扁平记录。
    return {
        "chunk_id": chunk_id,
        "doc_id": doc_id,
        "file_id": _safe_str(
            chunk.get("file_id"),
            max_length=DEFAULT_FILE_ID_MAX_LENGTH,
        ),
        "tenant_id": _safe_str(
            chunk.get("tenant_id"),
            max_length=DEFAULT_TENANT_ID_MAX_LENGTH,
        ),
        "kb_id": _safe_str(
            chunk.get("kb_id"),
            max_length=DEFAULT_KB_ID_MAX_LENGTH,
        ),
        "text": _safe_str(
            chunk.get("text"),
            max_length=max_text_chars,
        ),
        "title": _safe_str(
            chunk.get("title"),
            max_length=DEFAULT_TITLE_MAX_LENGTH,
        ),
        "section": _safe_str(
            chunk.get("section"),
            max_length=DEFAULT_SECTION_MAX_LENGTH,
        ),
        "section_level": _safe_int(
            chunk.get("section_level"),
            -1,
        ),
        "source": _safe_str(
            chunk.get("source"),
            max_length=DEFAULT_SOURCE_MAX_LENGTH,
        ),
        "page_start": page_start,
        "page_end": page_end,
        "start_char": _safe_int(
            chunk.get("start_char"),
            -1,
        ),
        "end_char": _safe_int(
            chunk.get("end_char"),
            -1,
        ),
        "token_count": _safe_int(
            chunk.get("token_count"),
            -1,
        ),
        "chunk_index": _safe_int(
            chunk.get("chunk_index"),
            -1,
        ),
        "chunk_level": _safe_int(
            chunk.get("chunk_level"),
            0,
        ),
        "schema_version": _safe_str(
            chunk.get("schema_version")
            or DEFAULT_CHUNK_SCHEMA_VERSION,
            max_length=DEFAULT_VERSION_MAX_LENGTH,
        ),
        "status": _safe_str(
            chunk.get("status") or DEFAULT_STATUS,
            max_length=DEFAULT_STATUS_MAX_LENGTH,
        ),
        "security_level": _safe_str(
            chunk.get("security_level") or "internal",
            max_length=DEFAULT_SECURITY_LEVEL_MAX_LENGTH,
        ),
        "embedding_model": _safe_str(
            embedding_model,
            max_length=DEFAULT_MODEL_MAX_LENGTH,
        ),
        "embedding_version": _safe_str(
            embedding_version,
            max_length=DEFAULT_VERSION_MAX_LENGTH,
        ),
        "embedding_dim": int(embedding_dim),
        "index_name": _safe_str(
            index_name,
            max_length=DEFAULT_INDEX_NAME_MAX_LENGTH,
        ),
        "index_version": _safe_str(
            index_version,
            max_length=DEFAULT_VERSION_MAX_LENGTH,
        ),
        "extra_json": _extra_json(chunk.get("extra")),
        "vector": _safe_float_list(vector, embedding_dim),
    }


def insert_chunk_records(
    *,
    client: Any,
    collection_name: str,
    records: Sequence[Dict[str, Any]],
    batch_size: int = 128,
) -> int:
    """按批次插入扁平 Chunk 记录并返回实际插入数量。"""

    if not records:
        return 0

    inserted = 0
    normalized_batch_size = max(1, int(batch_size))

    # 逐批插入；响应缺少 insert_count 时按批次长度回退。
    for start in range(0, len(records), normalized_batch_size):
        batch = list(records[start : start + normalized_batch_size])
        response = client.insert(
            collection_name=collection_name,
            data=batch,
        )
        inserted += int(
            (response or {}).get("insert_count", len(batch))
        )

    return inserted
