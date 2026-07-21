# -*- coding: utf-8 -*-
# =============================================================================
# 中文阅读说明：RAG 核心模块，负责查询变换、召回、融合、重排、证据评估和上下文组装。
# 主要定义：_safe_float、_parse_json_list、normalize_milvus_child_hit、MilvusChildRetriever。建议先从公开入口函数开始，再沿调用关系向下阅读。
# =============================================================================
"""
rag_template/retriever/milvus_child_retriever.py
================================================

P1 dense child retriever：
query -> embedding -> Milvus child_chunk_v1 search -> normalized child hits。

职责边界：
- 只检索 child_chunk_v1。
- 不回填 parent，不做 BM25，不做 RRF，不做 rerank。
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence

import numpy as np
from pymilvus import MilvusClient

from rag.configs.SchemaConfig import DEFAULT_EMBEDDING_VERSION, DEFAULT_VECTOR_DB
from rag.embed.embedding_service import (
    encode_query_with_hash,
    encode_query_with_model,
    resolve_default_embedding_model,
)


DEFAULT_CHILD_OUTPUT_FIELDS = [
    "chunk_id",
    "vector_id",
    "indexed_chunk_id",
    "child_chunk_id",
    "parent_chunk_id",
    "tenant_id",
    "kb_id",
    "file_id",
    "doc_id",
    "source_type",
    "indexed_granularity",
    "text",
    "text_length",
    "text_hash",
    "token_count",
    "source_unit_ids_json",
    "title",
    "section",
    "section_level",
    "page_start",
    "page_end",
    "child_chunk_index",
    "child_index_in_parent",
    "child_chunk_strategy",
    "char_start_in_parent",
    "char_end_in_parent",
    "cleaning_version",
    "parent_chunk_version",
    "child_chunk_version",
    "chunk_created_at",
    "child_chunk_schema_version",
    "embedding_model",
    "embedding_dim",
    "embedding_version",
    "embedding_key",
    "index_version",
    "is_active",
]


# 阅读注释（函数）：处理 safe float 相关逻辑。
def _safe_float(value: Any, default: float = 0.0) -> float:
    """处理 safe float 相关逻辑。

    参数:
        value: value，具体约束请结合类型标注和调用方确认。
        default: default，具体约束请结合类型标注和调用方确认。

    返回:
        float

    阅读提示:
        主要直接调用：float。
    """
    if value is None:
        return default
    try:
        return float(value)
    except Exception:
        return default


# 阅读注释（函数）：解析 JSON 列表。
def _parse_json_list(value: Any) -> List[Any]:
    """解析 JSON 列表。

    参数:
        value: value，具体约束请结合类型标注和调用方确认。

    返回:
        List[Any]

    阅读提示:
        主要直接调用：isinstance, json.loads。
    """
    if value is None or value == "":
        return []
    if isinstance(value, list):
        return value
    if not isinstance(value, str):
        return [value]
    try:
        parsed = json.loads(value)
    except Exception:
        return []
    return parsed if isinstance(parsed, list) else [parsed]


# 阅读注释（函数）：规范化 milvus 子块 hit。
def normalize_milvus_child_hit(hit: Dict[str, Any], rank: int) -> Dict[str, Any]:
    """Normalize one MilvusClient.search hit into a stable child hit dict."""
    entity = hit.get("entity", {}) if isinstance(hit, dict) else {}
    if not isinstance(entity, dict):
        entity = {}

    score = hit.get("distance", hit.get("score", hit.get("similarity", None)))
    child_chunk_id = entity.get("child_chunk_id") or entity.get("chunk_id") or hit.get("id")
    chunk_id = entity.get("chunk_id") or child_chunk_id

    child_chunk = dict(entity)
    child_chunk["chunk_id"] = str(chunk_id) if chunk_id is not None else ""
    child_chunk["child_chunk_id"] = str(child_chunk_id) if child_chunk_id is not None else child_chunk["chunk_id"]
    child_chunk["source_unit_ids"] = _parse_json_list(entity.get("source_unit_ids_json"))

    return {
        "rank": rank,
        "score": _safe_float(score),
        "distance": _safe_float(score),
        "retrieval_source": "dense",
        "chunk_id": child_chunk["chunk_id"],
        "child_chunk_id": child_chunk["child_chunk_id"],
        "parent_chunk_id": str(entity.get("parent_chunk_id") or ""),
        "tenant_id": str(entity.get("tenant_id") or ""),
        "kb_id": str(entity.get("kb_id") or ""),
        "file_id": str(entity.get("file_id") or ""),
        "doc_id": str(entity.get("doc_id") or ""),
        "child_chunk": child_chunk,
        "raw_hit": hit,
    }


# 阅读注释（类）：封装 milvus 子块 retriever，集中封装相关状态、依赖和行为。
class MilvusChildRetriever:
    """Dense retriever over child_chunk_v1 collection."""

    # 阅读注释（函数）：初始化 MilvusChildRetriever，保存运行所需的依赖、配置或状态。
    def __init__(
        self,
        db_file: str | Path,
        collection_name: str = "rag_child_chunks",
        metric_type: str = "COSINE",
        vector_field: str = "vector",
        embedding_model: Optional[str] = None,
        embedding_device: str = "cuda",
        embedding_batch_size: int = 1,
        embedding_version: str = DEFAULT_EMBEDDING_VERSION,
        hash_embedding: bool = False,
        hash_dim: int = 768,
        output_fields: Optional[Sequence[str]] = None,
        vector_db: str = DEFAULT_VECTOR_DB,
    ):
        """初始化 MilvusChildRetriever，保存运行所需的依赖、配置或状态。

        参数:
            db_file: db 文件，具体约束请结合类型标注和调用方确认。
            collection_name: collection 名称，具体约束请结合类型标注和调用方确认。
            metric_type: 指标 类型，具体约束请结合类型标注和调用方确认。
            vector_field: vector field，具体约束请结合类型标注和调用方确认。
            embedding_model: embedding 模型，具体约束请结合类型标注和调用方确认。
            embedding_device: embedding device，具体约束请结合类型标注和调用方确认。
            embedding_batch_size: embedding batch size，具体约束请结合类型标注和调用方确认。
            embedding_version: embedding 版本，具体约束请结合类型标注和调用方确认。
            hash_embedding: hash embedding，具体约束请结合类型标注和调用方确认。
            hash_dim: hash dim，具体约束请结合类型标注和调用方确认。
            output_fields: 输出 fields，具体约束请结合类型标注和调用方确认。
            vector_db: vector db，具体约束请结合类型标注和调用方确认。

        返回:
            未显式标注；请结合调用方和实际返回语句理解。

        阅读提示:
            主要直接调用：str, resolve_default_embedding_model, int, list, exists, Path, FileNotFoundError, MilvusClient。
        """
        self.db_file = str(db_file)
        self.collection_name = collection_name
        self.metric_type = metric_type
        self.vector_field = vector_field
        self.embedding_model = embedding_model or resolve_default_embedding_model()
        self.embedding_device = embedding_device
        self.embedding_batch_size = embedding_batch_size
        self.embedding_version = embedding_version
        self.hash_embedding = hash_embedding
        self.hash_dim = int(hash_dim)
        self.output_fields = list(output_fields or DEFAULT_CHILD_OUTPUT_FIELDS)
        self.vector_db = vector_db

        if not Path(self.db_file).exists():
            raise FileNotFoundError(f"Milvus Lite db path not found: {self.db_file}")

        self.client = MilvusClient(self.db_file)
        if not self.client.has_collection(self.collection_name):
            raise ValueError(f"Milvus collection not found: {self.collection_name}")

        try:
            self.client.load_collection(self.collection_name)
        except Exception:
            # Milvus Lite 有时无需显式 load；这里不阻断。
            pass

    # 阅读注释（函数）：处理 encode 查询 相关逻辑。
    def encode_query(self, query: str) -> np.ndarray:
        """处理 encode 查询 相关逻辑。

        参数:
            query: 当前检索或生成查询。

        返回:
            np.ndarray

        阅读提示:
            主要直接调用：query.strip, ValueError, encode_query_with_hash, encode_query_with_model。
        """
        if not query or not query.strip():
            raise ValueError("query cannot be empty")
        if self.hash_embedding:
            return encode_query_with_hash(query, dim=self.hash_dim)
        if not self.embedding_model:
            raise ValueError("embedding_model is required unless hash_embedding=True")
        return encode_query_with_model(
            query=query,
            model_name=self.embedding_model,
            device=self.embedding_device,
            batch_size=self.embedding_batch_size,
        )

    # 阅读注释（函数）：搜索 by vector。
    def search_by_vector(
        self,
        query_vector: np.ndarray,
        top_k: int = 10,
        filter_expr: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        """搜索 by vector。

        参数:
            query_vector: 查询 vector，具体约束请结合类型标注和调用方确认。
            top_k: top k，具体约束请结合类型标注和调用方确认。
            filter_expr: filter expr，具体约束请结合类型标注和调用方确认。

        返回:
            List[Dict[str, Any]]

        阅读提示:
            主要直接调用：ValueError, reshape, np.asarray, vector.tolist, int, self.client.search, normalize_milvus_child_hit, enumerate。
        """
        if query_vector is None:
            raise ValueError("query_vector cannot be None")
        vector = np.asarray(query_vector, dtype=np.float32).reshape(-1)

        kwargs: Dict[str, Any] = {
            "collection_name": self.collection_name,
            "data": [vector.tolist()],
            "anns_field": self.vector_field,
            "limit": int(top_k),
            "search_params": {"metric_type": self.metric_type},
            "output_fields": self.output_fields,
        }
        if filter_expr:
            kwargs["filter"] = filter_expr

        result = self.client.search(**kwargs)
        hits = result[0] if result else []
        return [normalize_milvus_child_hit(hit, rank=i) for i, hit in enumerate(hits, start=1)]

    # 阅读注释（函数）：搜索 MilvusChildRetriever。
    def search(
        self,
        query: str,
        top_k: int = 10,
        filter_expr: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        """搜索 MilvusChildRetriever。

        参数:
            query: 当前检索或生成查询。
            top_k: top k，具体约束请结合类型标注和调用方确认。
            filter_expr: filter expr，具体约束请结合类型标注和调用方确认。

        返回:
            List[Dict[str, Any]]

        阅读提示:
            主要直接调用：self.encode_query, self.search_by_vector。
        """
        query_vector = self.encode_query(query)
        return self.search_by_vector(query_vector=query_vector, top_k=top_k, filter_expr=filter_expr)

    # 阅读注释（函数）：释放 MilvusChildRetriever 持有的资源。
    def close(self) -> None:
        """释放 MilvusChildRetriever 持有的资源。

        返回:
            None

        阅读提示:
            主要直接调用：getattr, callable, close。
        """
        close = getattr(self.client, "close", None)
        if callable(close):
            close()
