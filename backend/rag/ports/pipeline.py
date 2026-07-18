"""Structural ports for retrieval pipeline components."""
from __future__ import annotations

from typing import Any, Dict, List, Optional, Protocol


class RetrieverPort(Protocol):
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


class RerankerPort(Protocol):
    def rerank(
        self,
        *,
        query: str,
        results: List[Dict[str, Any]],
    ) -> List[Dict[str, Any]]: ...

    def execution_metadata(self) -> Dict[str, Any]: ...


class ContextPackerPort(Protocol):
    def pack(self, results: List[Dict[str, Any]]) -> Any: ...


class PromptBuilderPort(Protocol):
    def build(self, *, query: str, packed_context: str, citations: Any) -> Any: ...


class FlatRetrieverPort(Protocol):
    def search(
        self,
        query: str,
        top_k: int = 3,
        filter_expr: Optional[str] = None,
    ) -> List[Dict[str, Any]]: ...
