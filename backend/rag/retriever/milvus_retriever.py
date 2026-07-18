"""
milvus_retriever.py
===================

Milvus Lite 检索器模块。

本文件负责：
1. 使用 TextEmbedder 将 query 编码为向量
2. 调用 MilvusLiteStore 执行向量检索
3. 将 Milvus 原始结果转换为 RAG 项目统一检索结果格式

返回格式需要兼容 eval_runner / prompt_builder / reranker：

{
    "rank": 1,
    "score": 0.87,
    "doc_id": "...",
    "chunk_id": "...",
    "text": "...",
    "source": "...",
    "metadata": {...}
}
"""

from pathlib import Path
from typing import Any, Dict, List, Optional

from rag.vector_store.milvus_chunk_store import MilvusLiteStore


class MilvusRetriever:
    """
    Milvus Lite 检索器。

    职责：
        query -> query embedding -> Milvus search -> normalized results
    """

    def __init__(
        self,
        db_file: str | Path,
        collection_name: str,
        dim: int,
        embedder: Any,
    ):
        self.db_file = db_file
        self.collection_name = collection_name
        self.dim = dim
        self.embedder = embedder

        self.store = MilvusLiteStore(
            db_file=db_file,
            collection_name=collection_name,
            dim=dim,
        )

        if not self.store.has_collection():
            raise ValueError(
                f"Milvus collection 不存在: {collection_name}。"
                f"请先运行 scripts/build_milvus_lite_index.py"
            )

    def retrieve(
        self,
        query: str,
        top_k: int = 5,
        filter_expr: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        """
        执行 Milvus 向量检索。

        参数:
            query:
                用户问题。

            top_k:
                返回 top-k 结果。

            filter_expr:
                Milvus filter 表达式，例如：
                'doc_type == "policy"'
                'security_level == "internal"'
                'project_id == "PRJ-ALPHA-2026-001"'

        返回:
            标准化后的检索结果列表。
        """
        if not query or not query.strip():
            raise ValueError("query 不能为空")

        query_embedding = self.embedder.encode_query(query)

        # encode_query 返回 shape = (1, dim)
        query_vector = query_embedding[0].tolist()

        raw_results = self.store.search(
            query_vector=query_vector,
            top_k=top_k,
            filter_expr=filter_expr,
        )

        return self._normalize_results(raw_results)

    def search(
        self,
        query: str,
        top_k: int = 5,
        filter_expr: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        """
        search 是 retrieve 的别名，兼容 eval_runner。
        """
        return self.retrieve(
            query=query,
            top_k=top_k,
            filter_expr=filter_expr,
        )

    @staticmethod
    def _normalize_results(
        raw_results: List[Dict[str, Any]],
    ) -> List[Dict[str, Any]]:
        """
        将 Milvus search 原始结果转换为项目统一格式。

        MilvusClient.search 返回的每个 hit 通常包含：
            id
            distance
            entity
        """
        results: List[Dict[str, Any]] = []

        for index, hit in enumerate(raw_results):
            entity = hit.get("entity", {}) or {}

            metadata = {
                "doc_type": entity.get("doc_type", ""),
                "title": entity.get("title", ""),
                "chunk_index": entity.get("chunk_index"),
                "security_level": entity.get("security_level", ""),
                "project_id": entity.get("project_id", ""),
                "status": entity.get("status", ""),
                "is_latest": entity.get("is_latest"),
            }

            result = {
                "rank": index + 1,
                "score": float(hit.get("distance", 0.0)),
                "doc_id": str(entity.get("doc_id", "")),
                "chunk_id": str(entity.get("chunk_id", "")),
                "text": str(entity.get("text", "")),
                "source": str(entity.get("source", "")),
                "metadata": metadata,
            }

            results.append(result)

        return results