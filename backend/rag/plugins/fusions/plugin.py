"""RRF fusion plugins for source fusion and multi-query fusion."""

from __future__ import annotations

from typing import Any

from rag.ranker.rrf_fusion import rrf_fuse
from rag.retrieval.query_fusion import MultiQueryFusion
from rag.schema.candidate import CandidateSet


class ChildRRFFusionPlugin:
    """Fuse Dense/BM25 child hits for one retrieval query."""

    def __init__(
        self,
        *,
        build_context: Any = None,
        rrf_k: int = 60,
    ) -> None:
        del build_context
        self.rrf_k = max(1, int(rrf_k))

    def fuse(self, candidate_sets: list[CandidateSet]) -> CandidateSet:
        if not candidate_sets:
            return CandidateSet(query="", source_name="source_rrf", candidates=[])
        query = candidate_sets[0].query
        by_source = {
            item.source_name: list(item.candidates)
            for item in candidate_sets
        }
        fused = rrf_fuse(by_source, rrf_k=self.rrf_k, top_k=None)
        return CandidateSet(
            query=query,
            source_name="source_rrf",
            candidates=fused,
            metadata={
                "rrf_k": self.rrf_k,
                "source_sets": {
                    item.source_name: {
                        "candidate_count": len(item.candidates),
                        **dict(item.metadata),
                    }
                    for item in candidate_sets
                },
                "fused_count": len(fused),
            },
        )


class ParentRRFFusionPlugin:
    """Fuse parent-level result sets produced by multiple transformed queries."""

    def __init__(
        self,
        *,
        build_context: Any = None,
        rrf_k: int = 60,
        top_k: int = 10,
    ) -> None:
        del build_context
        self.rrf_k = max(1, int(rrf_k))
        self.top_k = max(1, int(top_k))
        self.backend = MultiQueryFusion()

    def fuse(self, candidate_sets: list[CandidateSet]) -> CandidateSet:
        if not candidate_sets:
            return CandidateSet(query="", source_name="query_rrf", candidates=[])
        query_results = {
            item.source_name: list(item.candidates)
            for item in candidate_sets
        }
        fused = self.backend.fuse(
            query_results,
            rrf_k=self.rrf_k,
            top_k=self.top_k,
        )
        return CandidateSet(
            query=candidate_sets[0].query,
            source_name="query_rrf",
            candidates=fused,
            metadata={
                "rrf_k": self.rrf_k,
                "top_k": self.top_k,
                "query_set_count": len(candidate_sets),
                "fused_count": len(fused),
            },
        )
