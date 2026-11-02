"""基于 ``RetrievalRequest/CandidateSet`` 的新检索组合端口。

这些协议用于配置驱动的召回、来源融合、跨 Query 融合和候选补全。组件交换统一候选对象，
不暴露 Milvus/BM25 等具体后端返回结构。
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from rag.schema.candidate import CandidateSet, RetrievalRequest


@runtime_checkable
class CandidateRetrieverPort(Protocol):
    """执行一个来源的召回，并返回带来源、排名和血缘的候选集合。"""

    def retrieve(self, request: RetrievalRequest) -> CandidateSet: ...


@runtime_checkable
class SourceFusionPort(Protocol):
    """融合同一 Query 的多种召回来源，例如 dense 与 BM25。"""
    def fuse(self, candidate_sets: list[CandidateSet]) -> CandidateSet: ...


@runtime_checkable
class QueryFusionPort(Protocol):
    """融合原 Query、改写 Query 和 HyDE 等多 Query 的父级候选。"""
    def fuse(self, candidate_sets: list[CandidateSet]) -> CandidateSet: ...


@runtime_checkable
class CandidateEnricherPort(Protocol):
    """补齐候选正文、父级上下文或元数据，但不改变检索请求授权范围。"""

    def enrich(self, candidate_set: CandidateSet) -> CandidateSet: ...
