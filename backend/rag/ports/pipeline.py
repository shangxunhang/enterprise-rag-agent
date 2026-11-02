"""旧字典型 RAG Pipeline 组件协议。

这些端口服务现有父子检索链和兼容调用方，输入输出主要是 ``dict``。新配置驱动组合优先
使用 ``retrieval_components.py`` 的 ``CandidateSet`` 协议；迁移期间两套对象不可直接
混用，必须经过显式适配。
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional, Protocol, runtime_checkable


class RetrieverPort(Protocol):
    """执行 dense/keyword/融合检索并返回兼容字典结果。"""
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


@runtime_checkable
class RerankerPort(Protocol):
    """按当前 Query 重排兼容检索结果，并暴露实际执行元数据。"""
    def rerank(
        self,
        *,
        query: str,
        results: List[Dict[str, Any]],
    ) -> List[Dict[str, Any]]: ...

    def execution_metadata(self) -> Dict[str, Any]: ...


@runtime_checkable
class ContextPackerPort(Protocol):
    """在 Token 与条目预算内选择结果并组装生成上下文。"""
    def pack(
        self,
        results: List[Dict[str, Any]],
        *,
        token_budget: int | None = None,
        max_items: int | None = None,
    ) -> Any: ...


class PromptBuilderPort(Protocol):
    """把 Query、已打包上下文和引用目录渲染为下游 Prompt 对象。"""
    def build(self, *, query: str, packed_context: str, citations: Any) -> Any: ...


class FlatRetrieverPort(Protocol):
    """最小平面向量检索接口，供早期 Mini-RAG 与兼容适配器使用。"""
    def search(
        self,
        query: str,
        top_k: int = 3,
        filter_expr: Optional[str] = None,
    ) -> List[Dict[str, Any]]: ...
