# -*- coding: utf-8 -*-
# =============================================================================
# 中文阅读说明：RAG 核心模块，负责查询变换、召回、融合、重排、证据评估和上下文组装。
# 主要定义：ParentChildRetriever。建议先从公开入口函数开始，再沿调用关系向下阅读。
# =============================================================================
"""
rag_template/retriever/parent_child_retriever.py
================================================

P1 parent-child retriever：
Milvus dense child hits -> parent_chunk_id 回填 parent -> retrieval_result_v2。

职责边界：
- P1 只做 dense retrieval + parent backfill。
- 不做 BM25，不做 RRF，不做 rerank，不做 prompt context packing。
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from rag.schema.Retrieval_Result_Schema import build_retrieval_result_v2
from rag.store.parent_chunk_store import ParentChunkStore
from rag.retriever.milvus_child_retriever import MilvusChildRetriever


# 阅读注释（类）：封装 父块 子块 retriever，集中封装相关状态、依赖和行为。
class ParentChildRetriever:
    """Combine Milvus child retrieval with parent chunk backfill."""

    # 阅读注释（函数）：初始化 ParentChildRetriever，保存运行所需的依赖、配置或状态。
    def __init__(
        self,
        child_retriever: MilvusChildRetriever,
        parent_store: ParentChunkStore,
        context_granularity: str = "parent",
        dedup_parent: bool = False,
    ):
        """初始化 ParentChildRetriever，保存运行所需的依赖、配置或状态。

        参数:
            child_retriever: 子块 retriever，具体约束请结合类型标注和调用方确认。
            parent_store: 父块 store，具体约束请结合类型标注和调用方确认。
            context_granularity: 上下文 granularity，具体约束请结合类型标注和调用方确认。
            dedup_parent: dedup 父块，具体约束请结合类型标注和调用方确认。

        返回:
            未显式标注；请结合调用方和实际返回语句理解。

        阅读提示:
            主要直接调用：ValueError。
        """
        if context_granularity not in {"parent", "child"}:
            raise ValueError("context_granularity must be 'parent' or 'child'")
        self.child_retriever = child_retriever
        self.parent_store = parent_store
        self.context_granularity = context_granularity
        self.dedup_parent = dedup_parent

    # 阅读注释（函数）：处理 deduplicate by 父块 相关逻辑。
    def _deduplicate_by_parent(self, child_hits: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """处理 deduplicate by 父块 相关逻辑。

        参数:
            child_hits: 子块 hits，具体约束请结合类型标注和调用方确认。

        返回:
            List[Dict[str, Any]]

        阅读提示:
            主要直接调用：set, hit.get, seen.add, deduped.append。
        """
        seen = set()
        deduped: List[Dict[str, Any]] = []
        for hit in child_hits:
            parent_id = hit.get("parent_chunk_id") or ""
            # 没有 parent_id 的异常数据，不强行合并。
            key = parent_id or f"__missing_parent__:{hit.get('child_chunk_id')}"
            if key in seen:
                continue
            seen.add(key)
            deduped.append(hit)
        return deduped

    # 阅读注释（函数）：检索 ParentChildRetriever。
    def retrieve(
        self,
        query: str,
        top_k: int = 5,
        child_top_k: Optional[int] = None,
        filter_expr: Optional[str] = None,
        dedup_parent: Optional[bool] = None,
    ) -> List[Dict[str, Any]]:
        """Return retrieval_result_v2 list.

        Args:
            query: 用户问题。
            top_k: 最终返回条数。
            child_top_k: Milvus child 初召回条数；None 时等于 top_k。
            filter_expr: Milvus metadata filter，例如 `doc_id == "doc1"`。
            dedup_parent: 是否按 parent_chunk_id 去重；None 使用实例默认值。
        """
        if not query or not query.strip():
            raise ValueError("query cannot be empty")

        dense_k = int(child_top_k or top_k)
        child_hits = self.child_retriever.search(query=query, top_k=dense_k, filter_expr=filter_expr)

        use_dedup = self.dedup_parent if dedup_parent is None else bool(dedup_parent)
        if use_dedup:
            child_hits = self._deduplicate_by_parent(child_hits)

        results: List[Dict[str, Any]] = []
        for rank, hit in enumerate(child_hits[: int(top_k)], start=1):
            child_chunk = hit.get("child_chunk", {})
            parent_id = hit.get("parent_chunk_id") or child_chunk.get("parent_chunk_id")
            parent_chunk = self.parent_store.get(parent_id) if parent_id else None

            result = build_retrieval_result_v2(
                child_chunk=child_chunk,
                parent_chunk=parent_chunk,
                rank=rank,
                score=float(hit.get("score") or 0.0),
                rerank_score=None,
                embedding_model=self.child_retriever.embedding_model,
                embedding_version=self.child_retriever.embedding_version,
                index_name=self.child_retriever.collection_name,
                vector_db=self.child_retriever.vector_db,
                context_granularity=self.context_granularity,
                metadata={
                    "retrieval_stage": "p1_dense_parent_backfill",
                    "retrieval_sources": ["dense"],
                    "dense_rank": hit.get("rank"),
                    "dense_score": hit.get("score"),
                    "parent_found": parent_chunk is not None,
                },
                extra={
                    "filter_expr": filter_expr,
                    "dedup_parent": use_dedup,
                },
            )
            results.append(result)
        return results
