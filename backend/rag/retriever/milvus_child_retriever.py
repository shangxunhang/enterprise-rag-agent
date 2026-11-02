# -*- coding: utf-8 -*-
"""P1 dense 子块召回：Query Embedding → Milvus child_chunk_v1 命中。

本类只负责子块向量检索与原始 Hit 规范化，不查询 Parent Store，也不执行 BM25、RRF、
重排或 Context Packing。``filter_expr`` 由更外层授权边界构造，本类只原样交给 Milvus。
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence

import numpy as np

try:
    from pymilvus import MilvusClient
except ImportError:  # Optional local-rag dependency; fail only on real use.
    MilvusClient = None  # type: ignore[assignment,misc]

from rag.configs.SchemaConfig import DEFAULT_EMBEDDING_VERSION, DEFAULT_VECTOR_DB
from rag.schema.structured_evidence import build_structured_evidence
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
    "document_type",
    "evidence_role",
    "project_relation",
    "project_id",
    "authority_level",
    "extra_json",
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
    """安全转换 Milvus distance/score；非法值不应中断整个结果集。"""
    if value is None:
        return default
    try:
        return float(value)
    except Exception:
        return default


def _parse_json_list(value: Any) -> List[Any]:
    """把 Milvus 中 JSON 字符串形式的来源 ID 恢复为列表。"""
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


def _parse_extra_json(value: Any) -> tuple[Dict[str, Any], str | None]:
    """恢复 Stage C ``extra_json``，失败时保留显式错误而不是静默丢 Profile。"""
    if value is None or value == "":
        return {}, "missing_extra_json"
    if isinstance(value, dict):
        return dict(value), None
    if not isinstance(value, str):
        return {}, "extra_json_not_string_or_object"
    try:
        parsed = json.loads(value)
    except Exception as exc:
        return {}, f"invalid_extra_json:{type(exc).__name__}"
    if not isinstance(parsed, dict):
        return {}, "extra_json_root_not_object"
    return dict(parsed), None


def normalize_milvus_child_hit(hit: Dict[str, Any], rank: int) -> Dict[str, Any]:
    """把一个 ``MilvusClient.search`` Hit 规范为稳定子块候选。

    ``child_chunk`` 保留全部实体字段；顶层只暴露融合和父块回填需要的身份、授权范围与
    score。原始 Hit 仍放在 ``raw_hit``，供调试而非业务消费。
    """
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
    parsed_extra, profile_parse_error = _parse_extra_json(entity.get("extra_json"))
    child_chunk["extra"] = parsed_extra
    if profile_parse_error:
        child_chunk["profile_parse_error"] = profile_parse_error
    document_profile = parsed_extra.get("document_profile")
    if isinstance(document_profile, dict):
        child_chunk["document_profile"] = dict(document_profile)
    type_aware = parsed_extra.get("type_aware_chunking")
    if isinstance(type_aware, dict):
        child_chunk["type_aware_chunking"] = dict(type_aware)
    structured = build_structured_evidence(child_chunk)
    child_chunk["structured_evidence"] = structured.model_dump(
        mode="json", exclude_none=True
    )

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
        "document_type": str(entity.get("document_type") or ""),
        "evidence_role": str(entity.get("evidence_role") or ""),
        "project_relation": str(entity.get("project_relation") or ""),
        "project_id": str(entity.get("project_id") or ""),
        "authority_level": str(entity.get("authority_level") or ""),
        "document_profile": (
            dict(child_chunk.get("document_profile") or {})
            if isinstance(child_chunk.get("document_profile"), dict)
            else {}
        ),
        "type_aware_chunking": (
            dict(child_chunk.get("type_aware_chunking") or {})
            if isinstance(child_chunk.get("type_aware_chunking"), dict)
            else {}
        ),
        "structured_evidence": dict(
            child_chunk.get("structured_evidence") or {}
        ),
        "profile_parse_error": child_chunk.get("profile_parse_error"),
        "child_chunk": child_chunk,
        "raw_hit": hit,
    }


class MilvusChildRetriever:
    """针对 ``child_chunk_v1`` Collection 的 dense 召回器。"""

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
        embedding_backend: Any | None = None,
        hash_embedding: bool = False,
        hash_dim: int = 768,
        output_fields: Optional[Sequence[str]] = None,
        vector_db: str = DEFAULT_VECTOR_DB,
    ):
        """连接现有 Milvus Lite 子块索引并配置 Query Embedding 实现。

        生产默认使用真实模型；``hash_embedding`` 只用于无模型测试。可注入共享
        ``embedding_backend`` 复用已加载资源。数据库或 Collection 缺失时立即失败，避免
        对空索引返回误导性结果。
        """
        self.db_file = str(db_file)
        self.collection_name = collection_name
        self.metric_type = metric_type
        self.vector_field = vector_field
        self.embedding_model = embedding_model or resolve_default_embedding_model()
        self.embedding_device = embedding_device
        self.embedding_batch_size = embedding_batch_size
        self.embedding_version = embedding_version
        self.embedding_backend = embedding_backend
        self.hash_embedding = hash_embedding
        self.hash_dim = int(hash_dim)
        self.output_fields = list(output_fields or DEFAULT_CHILD_OUTPUT_FIELDS)
        self.vector_db = vector_db

        if not Path(self.db_file).exists():
            raise FileNotFoundError(f"Milvus Lite db path not found: {self.db_file}")
        if MilvusClient is None:
            raise RuntimeError(
                "pymilvus is required for MilvusChildRetriever; "
                "install the local-rag extra"
            )

        self.client = MilvusClient(self.db_file)
        if not self.client.has_collection(self.collection_name):
            raise ValueError(f"Milvus collection not found: {self.collection_name}")

        try:
            self.client.load_collection(self.collection_name)
        except Exception:
            # Milvus Lite 有时无需显式 load；这里不阻断。
            pass

    def encode_query(self, query: str) -> np.ndarray:
        """按测试哈希、共享 Backend 或本地模型优先级生成一维 float32 Query 向量。"""
        if not query or not query.strip():
            raise ValueError("query cannot be empty")
        if self.hash_embedding:
            return encode_query_with_hash(query, dim=self.hash_dim)
        if self.embedding_backend is not None:
            return np.asarray(
                self.embedding_backend.encode_query(query),
                dtype=np.float32,
            ).reshape(-1)
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
        """用给定向量查询 Milvus，并按返回顺序规范化子块 Hit。

        本方法不重解释 ``distance`` 的方向；不同 metric 的比较语义应由索引配置与后续
        RRF/重排层记录，不能在这里统一改写为相似度。
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

    def search(
        self,
        query: str,
        top_k: int = 10,
        filter_expr: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        """编码文本 Query 后委托向量检索。"""
        query_vector = self.encode_query(query)
        return self.search_by_vector(query_vector=query_vector, top_k=top_k, filter_expr=filter_expr)

    def close(self) -> None:
        """幂等关闭 Milvus 客户端，并清空引用防止再次误用。"""
        client = getattr(self, "client", None)
        if client is None:
            return
        close = getattr(client, "close", None)
        if callable(close):
            close()
        self.client = None
