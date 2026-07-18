# -*- coding: utf-8 -*-
"""
rag_template/retriever/hybrid_parent_child_retriever.py
======================================================

P2 hybrid parent-child retriever:
Dense Milvus child retrieval + BM25 child retrieval + RRF fusion + parent backfill.

职责边界：
- P2 做 hybrid retrieval，不做 rerank，不做 prompt context packing，不做 LLM。
- 最终仍输出 retrieval_result_v2，方便 P3 接 reranker/context_packer。
"""

from __future__ import annotations

from collections import defaultdict
from typing import Any, Dict, List, Optional

from rag.ranker.rrf_fusion import rrf_fuse
from rag.legacy.schema.Retrieval_Result_Schema import build_retrieval_result_v2
from rag.store.parent_chunk_store import ParentChunkStore
from rag.retriever.milvus_child_retriever import MilvusChildRetriever
from rag.retriever.bm25_child_retriever import BM25ChildRetriever


def _safe_float(value: Any, default: float = 0.0) -> float:
    try:
        if value is None:
            return default
        return float(value)
    except Exception:
        return default


class HybridParentChildRetriever:
    """Dense + keyword hybrid retrieval over child chunks, with parent backfill."""

    def __init__(
        self,
        *,
        dense_retriever: Optional[MilvusChildRetriever],
        keyword_retriever: Optional[BM25ChildRetriever],
        parent_store: ParentChunkStore,
        context_granularity: str = "parent",
        rrf_k: int = 60,
        dedup_parent: bool = True,
    ):
        if dense_retriever is None and keyword_retriever is None:
            raise ValueError("At least one retriever is required")
        if context_granularity not in {"parent", "child"}:
            raise ValueError("context_granularity must be 'parent' or 'child'")
        self.dense_retriever = dense_retriever
        self.keyword_retriever = keyword_retriever
        self.parent_store = parent_store
        self.context_granularity = context_granularity
        self.rrf_k = int(rrf_k)
        self.dedup_parent = bool(dedup_parent)

    @staticmethod
    def _group_by_parent(candidates: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """Deduplicate fused child candidates by parent_chunk_id.

        Keep the best fused child candidate as representative and attach all matched child ids
        under the same parent into metadata-like fields. This keeps retrieval_result_v2 compact
        while preserving evidence for later citation/rerank.
        """
        groups: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
        missing_counter = 0
        for candidate in candidates:
            parent_id = candidate.get("parent_chunk_id") or ""
            if not parent_id:
                missing_counter += 1
                parent_id = f"__missing_parent__:{candidate.get('child_chunk_id') or missing_counter}"
            groups[str(parent_id)].append(candidate)

        deduped: List[Dict[str, Any]] = []
        for _, items in groups.items():
            items.sort(key=lambda x: _safe_float(x.get("fusion_score") or x.get("score")), reverse=True)
            best = items[0]
            matched_child_ids = []
            matched_child_chunks = []
            matched_sources = set()
            dense_scores = []
            keyword_scores = []
            for item in items:
                child_chunk = item.get("child_chunk") or {}
                child_id = (
                    item.get("child_chunk_id")
                    or item.get("chunk_id")
                    or child_chunk.get("chunk_id")
                    or child_chunk.get("child_chunk_id")
                )
                if child_id and child_id not in matched_child_ids:
                    matched_child_ids.append(child_id)
                    # Preserve the actual child evidence that contributed to
                    # the parent-level candidate.  Citation construction must
                    # bind to matched child text, not to an arbitrary prefix of
                    # the much larger parent context.
                    matched_child_chunks.append(
                        {
                            "chunk_id": child_id,
                            "child_chunk_id": child_id,
                            "parent_chunk_id": (
                                child_chunk.get("parent_chunk_id")
                                or item.get("parent_chunk_id")
                            ),
                            "doc_id": child_chunk.get("doc_id") or item.get("doc_id"),
                            "text": child_chunk.get("text") or item.get("child_text") or "",
                            "title": child_chunk.get("title") or item.get("title"),
                            "section": child_chunk.get("section") or item.get("section"),
                            "page_start": child_chunk.get("page_start") or item.get("page_start"),
                            "page_end": child_chunk.get("page_end") or item.get("page_end"),
                            "source_unit_ids": child_chunk.get("source_unit_ids") or [],
                        }
                    )
                for source in item.get("retrieval_sources", []) or []:
                    matched_sources.add(source)
                if item.get("dense_score") is not None:
                    dense_scores.append(_safe_float(item.get("dense_score")))
                if item.get("keyword_score") is not None:
                    keyword_scores.append(_safe_float(item.get("keyword_score")))

            best = dict(best)
            best["matched_child_chunk_ids"] = matched_child_ids
            best["matched_child_chunks"] = matched_child_chunks
            best["matched_child_count"] = len(matched_child_ids)
            best["retrieval_sources"] = sorted(matched_sources) if matched_sources else best.get("retrieval_sources", [])
            if dense_scores:
                best["best_dense_score"] = max(dense_scores)
            if keyword_scores:
                best["best_keyword_score"] = max(keyword_scores)
            deduped.append(best)

        deduped.sort(key=lambda x: _safe_float(x.get("fusion_score") or x.get("score")), reverse=True)
        for rank, candidate in enumerate(deduped, start=1):
            candidate["rank"] = rank
            candidate["score"] = _safe_float(candidate.get("fusion_score") or candidate.get("score"))
        return deduped

    def retrieve(
        self,
        query: str,
        *,
        final_top_k: int = 5,
        dense_top_k: int = 30,
        keyword_top_k: int = 30,
        filter_expr: Optional[str] = None,
        keyword_doc_id: Optional[str] = None,
        keyword_doc_ids: Optional[List[str]] = None,
        use_dense: bool = True,
        use_keyword: bool = True,
        dedup_parent: Optional[bool] = None,
    ) -> List[Dict[str, Any]]:
        if not query or not str(query).strip():
            raise ValueError("query cannot be empty")

        dense_hits: List[Dict[str, Any]] = []
        keyword_hits: List[Dict[str, Any]] = []

        if use_dense and self.dense_retriever is not None:
            dense_hits = self.dense_retriever.search(query=query, top_k=int(dense_top_k), filter_expr=filter_expr)

        if use_keyword and self.keyword_retriever is not None:
            keyword_hits = self.keyword_retriever.search(
                query=query,
                top_k=int(keyword_top_k),
                doc_id=keyword_doc_id,
                doc_ids=keyword_doc_ids,
            )

        fused = rrf_fuse(
            {
                "dense": dense_hits,
                "keyword": keyword_hits,
            },
            rrf_k=self.rrf_k,
            top_k=None,
        )

        use_dedup = self.dedup_parent if dedup_parent is None else bool(dedup_parent)
        if use_dedup:
            fused = self._group_by_parent(fused)

        selected = fused[: int(final_top_k)]
        results: List[Dict[str, Any]] = []

        embedding_model = self.dense_retriever.embedding_model if self.dense_retriever is not None else None
        embedding_version = self.dense_retriever.embedding_version if self.dense_retriever is not None else "embedding_v1"
        index_name = self.dense_retriever.collection_name if self.dense_retriever is not None else None
        vector_db = self.dense_retriever.vector_db if self.dense_retriever is not None else "none"

        for rank, candidate in enumerate(selected, start=1):
            child_chunk = candidate.get("child_chunk") or {}
            parent_id = candidate.get("parent_chunk_id") or child_chunk.get("parent_chunk_id")
            parent_chunk = self.parent_store.get(parent_id) if parent_id else None

            result = build_retrieval_result_v2(
                child_chunk=child_chunk,
                parent_chunk=parent_chunk,
                rank=rank,
                score=_safe_float(candidate.get("fusion_score") or candidate.get("score")),
                rerank_score=None,
                embedding_model=embedding_model,
                embedding_version=embedding_version,
                index_name=index_name,
                vector_db=vector_db,
                context_granularity=self.context_granularity,
                metadata={
                    "retrieval_stage": "p2_hybrid_rrf_parent_backfill",
                    "retrieval_sources": candidate.get("retrieval_sources", []),
                    "fusion_score": _safe_float(candidate.get("fusion_score") or candidate.get("score")),
                    "rrf_k": self.rrf_k,
                    "dense_rank": candidate.get("dense_rank"),
                    "dense_score": candidate.get("dense_score"),
                    "keyword_rank": candidate.get("keyword_rank"),
                    "keyword_score": candidate.get("keyword_score"),
                    "source_ranks": candidate.get("source_ranks", {}),
                    "source_scores": candidate.get("source_scores", {}),
                    "rrf_contributions": candidate.get("rrf_contributions", {}),
                    "parent_found": parent_chunk is not None,
                    "dedup_parent": use_dedup,
                    "matched_child_chunk_ids": candidate.get("matched_child_chunk_ids", [candidate.get("child_chunk_id")]),
                    "matched_child_chunks": candidate.get("matched_child_chunks", []),
                    "matched_child_count": candidate.get("matched_child_count", 1),
                    "dense_hits": len(dense_hits),
                    "keyword_hits": len(keyword_hits),
                    "fused_candidates": len(fused),
                },
                extra={
                    "filter_expr": filter_expr,
                    "keyword_doc_id": keyword_doc_id,
                    "keyword_doc_ids": keyword_doc_ids or [],
                    "use_dense": use_dense,
                    "use_keyword": use_keyword,
                },
            )
            results.append(result)

        return results
