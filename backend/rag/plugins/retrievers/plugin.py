"""Configuration-driven child-retriever plugins."""

from __future__ import annotations

from typing import Any

from rag.schema.candidate import CandidateSet, RetrievalRequest


def _resource_pool(build_context: Any) -> Any:
    context = build_context if isinstance(build_context, dict) else {}
    pool = context.get("resource_pool")
    if pool is None:
        raise ValueError("retriever plugin requires build_context['resource_pool']")
    return pool


class MilvusDenseChildRetrieverPlugin:
    source_name = "dense"

    def __init__(
        self,
        *,
        build_context: Any = None,
        top_k: int = 10,
    ) -> None:
        self.top_k = max(1, int(top_k))
        self.backend = _resource_pool(build_context).get_dense_retriever()

    def retrieve(self, request: RetrievalRequest) -> CandidateSet:
        hits = self.backend.search(
            query=request.query,
            top_k=self.top_k,
            filter_expr=request.filter_expr,
        )
        return CandidateSet(
            query=request.query,
            source_name=self.source_name,
            candidates=hits,
            metadata={
                "top_k": self.top_k,
                "hit_count": len(hits),
                "embedding_model": getattr(self.backend, "embedding_model", None),
                "embedding_version": getattr(
                    self.backend, "embedding_version", "embedding_v1"
                ),
                "index_name": getattr(self.backend, "collection_name", None),
                "vector_db": getattr(self.backend, "vector_db", "none"),
                "filter_expr": request.filter_expr,
            },
        )

    def close(self) -> None:
        close = getattr(self.backend, "close", None)
        if callable(close):
            close()


class BM25ChildRetrieverPlugin:
    source_name = "keyword"

    def __init__(
        self,
        *,
        build_context: Any = None,
        top_k: int = 10,
    ) -> None:
        self.top_k = max(1, int(top_k))
        self.backend = _resource_pool(build_context).get_keyword_retriever()

    def retrieve(self, request: RetrievalRequest) -> CandidateSet:
        hits = self.backend.search(
            query=request.query,
            top_k=self.top_k,
            doc_ids=list(request.doc_ids or []),
        )
        return CandidateSet(
            query=request.query,
            source_name=self.source_name,
            candidates=hits,
            metadata={
                "top_k": self.top_k,
                "hit_count": len(hits),
                "doc_ids": list(request.doc_ids or []),
            },
        )
