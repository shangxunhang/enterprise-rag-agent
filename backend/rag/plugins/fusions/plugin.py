"""将两级 RRF 算法接入 CandidateSet 插件协议。

``ChildRRFFusionPlugin`` 融合同一 Query 的 dense/BM25 子块来源；
``ParentRRFFusionPlugin`` 融合不同变换 Query 的父级候选。两者使用相同公式，但身份键、
输入分组和输出 metadata 不同。
"""

from __future__ import annotations

from typing import Any
from core.diagnostics import capture_pool, current_diagnostics

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
        """保存至少为 1 的 RRF 常数；该插件不需要运行资源。"""
        del build_context
        self.rrf_k = max(1, int(rrf_k))

    def fuse(self, candidate_sets: list[CandidateSet]) -> CandidateSet:
        """按来源名称组织子块候选并返回带来源统计的融合集合。"""
        if not candidate_sets:
            return CandidateSet(query="", source_name="source_rrf", candidates=[])
        query = candidate_sets[0].query
        by_source = {
            item.source_name: list(item.candidates)
            for item in candidate_sets
        }
        fused = rrf_fuse(by_source, rrf_k=self.rrf_k, top_k=None)
        if current_diagnostics().enabled:
            capture_pool("R-SOURCE-FUSION", fused, unit_id="C06", operation_id="child_rrf", query=query,
                phase="RESULT", rrf_k=self.rrf_k, input_source_ids={item.source_name: [row.get("child_chunk_id") or row.get("chunk_id") for row in item.candidates] for item in candidate_sets})
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
        """保存 RRF 常数和最终父级候选上限，并创建无状态融合 Backend。"""
        del build_context
        self.rrf_k = max(1, int(rrf_k))
        self.top_k = max(1, int(top_k))
        self.backend = MultiQueryFusion()

    def fuse(self, candidate_sets: list[CandidateSet]) -> CandidateSet:
        """按 Query 来源名称融合父级候选，并记录参与 Query 数和截断上限。"""
        if not candidate_sets:
            return CandidateSet(query="", source_name="query_rrf", candidates=[])
        query_results = {
            item.source_name: list(item.candidates)
            for item in candidate_sets
        }
        for item in candidate_sets:
            capture_pool("R-QUERY-FUSION", item.candidates, unit_id="C07", operation_id="query_rrf_input:" + item.source_name,
                query=item.query, phase="INPUT", query_label=item.source_name)
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
