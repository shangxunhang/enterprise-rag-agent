# =============================================================================
# 中文阅读说明：RAG 核心模块，负责查询变换、召回、融合、重排、证据评估和上下文组装。
# 主要定义：_resource_pool、MilvusDenseChildRetrieverPlugin、BM25ChildRetrieverPlugin。建议先从公开入口函数开始，再沿调用关系向下阅读。
# =============================================================================
"""Configuration-driven child-retriever plugins."""

from __future__ import annotations

from typing import Any

from rag.schema.candidate import CandidateSet, RetrievalRequest


# 阅读注释（函数）：处理 resource pool 相关逻辑。
def _resource_pool(build_context: Any) -> Any:
    """处理 resource pool 相关逻辑。

    参数:
        build_context: build 上下文，具体约束请结合类型标注和调用方确认。

    返回:
        Any

    阅读提示:
        主要直接调用：isinstance, context.get, ValueError。
    """
    context = build_context if isinstance(build_context, dict) else {}
    pool = context.get("resource_pool")
    if pool is None:
        raise ValueError("retriever plugin requires build_context['resource_pool']")
    return pool


# 阅读注释（类）：封装 milvus dense 子块 retriever 插件，作为可配置插件接入 RAG 或 Agent 主链。
class MilvusDenseChildRetrieverPlugin:
    """封装 milvus dense 子块 retriever 插件，作为可配置插件接入 RAG 或 Agent 主链。"""
    source_name = "dense"

    # 阅读注释（函数）：初始化 MilvusDenseChildRetrieverPlugin，保存运行所需的依赖、配置或状态。
    def __init__(
        self,
        *,
        build_context: Any = None,
        top_k: int = 10,
    ) -> None:
        """初始化 MilvusDenseChildRetrieverPlugin，保存运行所需的依赖、配置或状态。

        参数:
            build_context: build 上下文，具体约束请结合类型标注和调用方确认。
            top_k: top k，具体约束请结合类型标注和调用方确认。

        返回:
            None

        阅读提示:
            主要直接调用：max, int, get_dense_retriever, _resource_pool。
        """
        self.top_k = max(1, int(top_k))
        self.backend = _resource_pool(build_context).get_dense_retriever()

    # 阅读注释（函数）：检索 MilvusDenseChildRetrieverPlugin。
    def retrieve(self, request: RetrievalRequest) -> CandidateSet:
        """检索 MilvusDenseChildRetrieverPlugin。

        参数:
            request: 当前请求对象。

        返回:
            CandidateSet

        阅读提示:
            主要直接调用：self.backend.search, CandidateSet, len, getattr。
        """
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

    # 阅读注释（函数）：释放 MilvusDenseChildRetrieverPlugin 持有的资源。
    def close(self) -> None:
        """释放 MilvusDenseChildRetrieverPlugin 持有的资源。

        返回:
            None

        阅读提示:
            主要直接调用：getattr, callable, close。
        """
        close = getattr(self.backend, "close", None)
        if callable(close):
            close()


# 阅读注释（类）：封装 bm25 子块 retriever 插件，作为可配置插件接入 RAG 或 Agent 主链。
class BM25ChildRetrieverPlugin:
    """封装 bm25 子块 retriever 插件，作为可配置插件接入 RAG 或 Agent 主链。"""
    source_name = "keyword"

    # 阅读注释（函数）：初始化 BM25ChildRetrieverPlugin，保存运行所需的依赖、配置或状态。
    def __init__(
        self,
        *,
        build_context: Any = None,
        top_k: int = 10,
    ) -> None:
        """初始化 BM25ChildRetrieverPlugin，保存运行所需的依赖、配置或状态。

        参数:
            build_context: build 上下文，具体约束请结合类型标注和调用方确认。
            top_k: top k，具体约束请结合类型标注和调用方确认。

        返回:
            None

        阅读提示:
            主要直接调用：max, int, get_keyword_retriever, _resource_pool。
        """
        self.top_k = max(1, int(top_k))
        self.backend = _resource_pool(build_context).get_keyword_retriever()

    # 阅读注释（函数）：检索 BM25ChildRetrieverPlugin。
    def retrieve(self, request: RetrievalRequest) -> CandidateSet:
        """检索 BM25ChildRetrieverPlugin。

        参数:
            request: 当前请求对象。

        返回:
            CandidateSet

        阅读提示:
            主要直接调用：self.backend.search, list, CandidateSet, len。
        """
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
