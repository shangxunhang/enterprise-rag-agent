# =============================================================================
# 中文阅读说明：RAG 核心模块，负责查询变换、召回、融合、重排、证据评估和上下文组装。
# 主要定义：RetrieverPort、RerankerPort、ContextPackerPort、PromptBuilderPort、FlatRetrieverPort。建议先从公开入口函数开始，再沿调用关系向下阅读。
# =============================================================================
"""Structural ports for retrieval pipeline components."""
from __future__ import annotations

from typing import Any, Dict, List, Optional, Protocol, runtime_checkable


# 阅读注释（类）：封装 retriever port，定义模块间调用契约，具体实现由适配器或插件提供。
class RetrieverPort(Protocol):
    """封装 retriever port，定义模块间调用契约，具体实现由适配器或插件提供。"""
    # 阅读注释（函数）：检索 RetrieverPort。
    def retrieve(
        self,
        *,
        query: str,
        dense_top_k: int,
        keyword_top_k: int,
        final_top_k: int,
        filter_expr: Optional[str] = None,
        keyword_doc_ids: Optional[List[str]] = None,
    ) -> List[Dict[str, Any]]: ...


# 阅读注释（类）：封装 reranker port，定义模块间调用契约，具体实现由适配器或插件提供。
@runtime_checkable
class RerankerPort(Protocol):
    """封装 reranker port，定义模块间调用契约，具体实现由适配器或插件提供。"""
    # 阅读注释（函数）：对 RerankerPort 重新排序。
    def rerank(
        self,
        *,
        query: str,
        results: List[Dict[str, Any]],
    ) -> List[Dict[str, Any]]: ...

    # 阅读注释（函数）：处理 execution 元数据 相关逻辑。
    def execution_metadata(self) -> Dict[str, Any]: ...


# 阅读注释（类）：封装 上下文 packer port，定义模块间调用契约，具体实现由适配器或插件提供。
@runtime_checkable
class ContextPackerPort(Protocol):
    """封装 上下文 packer port，定义模块间调用契约，具体实现由适配器或插件提供。"""
    # 阅读注释（函数）：压缩并组装 ContextPackerPort。
    def pack(
        self,
        results: List[Dict[str, Any]],
        *,
        token_budget: int | None = None,
        max_items: int | None = None,
    ) -> Any: ...


# 阅读注释（类）：封装 提示词 builder port，定义模块间调用契约，具体实现由适配器或插件提供。
class PromptBuilderPort(Protocol):
    """封装 提示词 builder port，定义模块间调用契约，具体实现由适配器或插件提供。"""
    # 阅读注释（函数）：构建 PromptBuilderPort。
    def build(self, *, query: str, packed_context: str, citations: Any) -> Any: ...


# 阅读注释（类）：封装 flat retriever port，定义模块间调用契约，具体实现由适配器或插件提供。
class FlatRetrieverPort(Protocol):
    """封装 flat retriever port，定义模块间调用契约，具体实现由适配器或插件提供。"""
    # 阅读注释（函数）：搜索 FlatRetrieverPort。
    def search(
        self,
        query: str,
        top_k: int = 3,
        filter_expr: Optional[str] = None,
    ) -> List[Dict[str, Any]]: ...
