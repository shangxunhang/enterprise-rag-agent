# =============================================================================
# 中文阅读说明：RAG 核心模块，负责查询变换、召回、融合、重排、证据评估和上下文组装。
# 主要定义：CandidateRetrieverPort、FusionPort、CandidateEnricherPort。建议先从公开入口函数开始，再沿调用关系向下阅读。
# =============================================================================
"""Stable ports for configuration-driven retrieval composition."""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from rag.schema.candidate import CandidateSet, RetrievalRequest


# 阅读注释（类）：封装 candidate retriever port，定义模块间调用契约，具体实现由适配器或插件提供。
@runtime_checkable
class CandidateRetrieverPort(Protocol):
    """封装 candidate retriever port，定义模块间调用契约，具体实现由适配器或插件提供。"""
    # 阅读注释（函数）：检索 CandidateRetrieverPort。
    def retrieve(self, request: RetrievalRequest) -> CandidateSet: ...


# 阅读注释（类）：封装 融合 port，定义模块间调用契约，具体实现由适配器或插件提供。
@runtime_checkable
class SourceFusionPort(Protocol):
    """Fuse multiple retrieval sources for one query."""
    def fuse(self, candidate_sets: list[CandidateSet]) -> CandidateSet: ...


@runtime_checkable
class QueryFusionPort(Protocol):
    """Fuse parent-level candidates from multiple transformed queries."""
    def fuse(self, candidate_sets: list[CandidateSet]) -> CandidateSet: ...


@runtime_checkable
class CandidateEnricherPort(Protocol):
    """封装 candidate enricher port，定义模块间调用契约，具体实现由适配器或插件提供。"""
    # 阅读注释（函数）：补充并丰富 CandidateEnricherPort。
    def enrich(self, candidate_set: CandidateSet) -> CandidateSet: ...
