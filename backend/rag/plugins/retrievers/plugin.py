"""将 Milvus dense 与内存 BM25 子块召回器接入配置驱动插件协议。

插件从共享 ``resource_pool`` 获取重量级 Backend，只负责把 ``RetrievalRequest`` 中的授权
缩小条件传递给对应来源，并包装为 ``CandidateSet``。
"""

from __future__ import annotations

from typing import Any

from rag.schema.candidate import CandidateSet, RetrievalRequest


def _resource_pool(build_context: Any) -> Any:
    """从组合根构造上下文读取必需资源池；缺失时拒绝创建半配置插件。"""
    context = build_context if isinstance(build_context, dict) else {}
    pool = context.get("resource_pool")
    if pool is None:
        raise ValueError("retriever plugin requires build_context['resource_pool']")
    return pool


class MilvusDenseChildRetrieverPlugin:
    """向量子块来源插件，使用请求中的 Milvus ``filter_expr``。"""
    source_name = "dense"

    def __init__(
        self,
        *,
        build_context: Any = None,
        top_k: int = 10,
    ) -> None:
        """保存来源召回上限并从资源池取得共享 Milvus Backend。"""
        self.top_k = max(1, int(top_k))
        self.backend = _resource_pool(build_context).get_dense_retriever()

    def retrieve(self, request: RetrievalRequest) -> CandidateSet:
        """执行 dense 召回并附加 Embedding、Collection、向量库和过滤表达式血缘。"""
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
        """兼容独立使用时释放 Backend；共享资源池会去重关闭同一实例。"""
        close = getattr(self.backend, "close", None)
        if callable(close):
            close()


class BM25ChildRetrieverPlugin:
    """关键词子块来源插件，显式传递租户、KB、文件和文档范围。"""
    source_name = "keyword"

    def __init__(
        self,
        *,
        build_context: Any = None,
        top_k: int = 10,
    ) -> None:
        """保存来源召回上限并从资源池取得共享 BM25 Backend。"""
        self.top_k = max(1, int(top_k))
        self.backend = _resource_pool(build_context).get_keyword_retriever()

    def retrieve(self, request: RetrievalRequest) -> CandidateSet:
        """执行授权范围内关键词召回，并在 metadata 回显实际范围与命中数量。"""
        hits = self.backend.search(
            query=request.query,
            top_k=self.top_k,
            tenant_id=request.tenant_id,
            kb_ids=list(request.kb_ids or []),
            file_ids=list(request.file_ids or []),
            doc_ids=list(request.doc_ids or []),
        )
        return CandidateSet(
            query=request.query,
            source_name=self.source_name,
            candidates=hits,
            metadata={
                "top_k": self.top_k,
                "hit_count": len(hits),
                "tenant_id": request.tenant_id,
                "kb_ids": list(request.kb_ids or []),
                "file_ids": list(request.file_ids or []),
                "doc_ids": list(request.doc_ids or []),
            },
        )
