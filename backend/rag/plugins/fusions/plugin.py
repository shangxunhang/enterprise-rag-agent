# =============================================================================
# 中文阅读说明：RAG 核心模块，负责查询变换、召回、融合、重排、证据评估和上下文组装。
# 主要定义：ChildRRFFusionPlugin、ParentRRFFusionPlugin。建议先从公开入口函数开始，再沿调用关系向下阅读。
# =============================================================================
"""RRF fusion plugins for source fusion and multi-query fusion."""

from __future__ import annotations

from typing import Any

from rag.ranker.rrf_fusion import rrf_fuse
from rag.retrieval.query_fusion import MultiQueryFusion
from rag.schema.candidate import CandidateSet


# 阅读注释（类）：封装 子块 rrffusion 插件，作为可配置插件接入 RAG 或 Agent 主链。
class ChildRRFFusionPlugin:
    """Fuse Dense/BM25 child hits for one retrieval query."""

    # 阅读注释（函数）：初始化 ChildRRFFusionPlugin，保存运行所需的依赖、配置或状态。
    def __init__(
        self,
        *,
        build_context: Any = None,
        rrf_k: int = 60,
    ) -> None:
        """初始化 ChildRRFFusionPlugin，保存运行所需的依赖、配置或状态。

        参数:
            build_context: build 上下文，具体约束请结合类型标注和调用方确认。
            rrf_k: rrf k，具体约束请结合类型标注和调用方确认。

        返回:
            None

        阅读提示:
            主要直接调用：max, int。
        """
        del build_context
        self.rrf_k = max(1, int(rrf_k))

    # 阅读注释（函数）：融合 ChildRRFFusionPlugin。
    def fuse(self, candidate_sets: list[CandidateSet]) -> CandidateSet:
        """融合 ChildRRFFusionPlugin。

        参数:
            candidate_sets: candidate sets，具体约束请结合类型标注和调用方确认。

        返回:
            CandidateSet

        阅读提示:
            主要直接调用：CandidateSet, list, rrf_fuse, len, dict。
        """
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


# 阅读注释（类）：封装 父块 rrffusion 插件，作为可配置插件接入 RAG 或 Agent 主链。
class ParentRRFFusionPlugin:
    """Fuse parent-level result sets produced by multiple transformed queries."""

    # 阅读注释（函数）：初始化 ParentRRFFusionPlugin，保存运行所需的依赖、配置或状态。
    def __init__(
        self,
        *,
        build_context: Any = None,
        rrf_k: int = 60,
        top_k: int = 10,
    ) -> None:
        """初始化 ParentRRFFusionPlugin，保存运行所需的依赖、配置或状态。

        参数:
            build_context: build 上下文，具体约束请结合类型标注和调用方确认。
            rrf_k: rrf k，具体约束请结合类型标注和调用方确认。
            top_k: top k，具体约束请结合类型标注和调用方确认。

        返回:
            None

        阅读提示:
            主要直接调用：max, int, MultiQueryFusion。
        """
        del build_context
        self.rrf_k = max(1, int(rrf_k))
        self.top_k = max(1, int(top_k))
        self.backend = MultiQueryFusion()

    # 阅读注释（函数）：融合 ParentRRFFusionPlugin。
    def fuse(self, candidate_sets: list[CandidateSet]) -> CandidateSet:
        """融合 ParentRRFFusionPlugin。

        参数:
            candidate_sets: candidate sets，具体约束请结合类型标注和调用方确认。

        返回:
            CandidateSet

        阅读提示:
            主要直接调用：CandidateSet, list, self.backend.fuse, len。
        """
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
