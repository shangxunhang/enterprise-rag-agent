# -*- coding: utf-8 -*-
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


def _safe_float(value: Any, default: float = 0.0) -> float:
    if value is None:
        return default
    try:
        return float(value)
    except Exception:
        return default


def _parse_json_list(value: Any) -> List[Any]:
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
        "doc_id": str(entity.get("doc_id") or ""),
        "child_chunk": child_chunk,
        "raw_hit": hit,
    }


class MilvusChildRetriever:
    """Dense retriever over child_chunk_v1 collection."""

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

    def encode_query(self, query: str) -> np.ndarray:
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

    def search_by_vector(
        self,
        query_vector: np.ndarray,
        top_k: int = 10,
        filter_expr: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
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

    def search(
        self,
        query: str,
        top_k: int = 10,
        filter_expr: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        query_vector = self.encode_query(query)
        return self.search_by_vector(query_vector=query_vector, top_k=top_k, filter_expr=filter_expr)

    def close(self) -> None:
        close = getattr(self.client, "close", None)
        if callable(close):
            close()
