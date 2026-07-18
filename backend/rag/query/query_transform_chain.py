"""Configuration-driven query-transform execution chain."""

from __future__ import annotations

from typing import Iterable

from rag.ports.query_transformer import QueryTransformerPort
from rag.query.query_expander import QueryExpansionResult


class QueryTransformChain:
    """Execute registered query transformers in configured order."""

    def __init__(
        self,
        transformers: Iterable[QueryTransformerPort],
        *,
        profile_id: str | None = None,
        profile_version: str | None = None,
    ) -> None:
        self.transformers = list(transformers)
        self.profile_id = str(profile_id or "").strip() or None
        self.profile_version = str(profile_version or "").strip() or None

    def transform(
        self,
        query: str,
        *,
        strategy_label: str = "hybrid",
    ) -> QueryExpansionResult:
        normalized_query = str(query or "").strip()
        if not normalized_query:
            raise ValueError("query cannot be empty")

        state = QueryExpansionResult(
            original_query=normalized_query,
            strategy=str(strategy_label or "hybrid").strip().lower(),
            retrieval_queries=[normalized_query],
            metadata={
                "mode": "configured_query_transform_chain",
                "profile_id": self.profile_id,
                "profile_version": self.profile_version,
                "transformers": [],
            },
        )
        for transformer in self.transformers:
            state = transformer.transform(state)
        state.retrieval_queries = self._dedup_keep_order(state.retrieval_queries)
        state.metadata["query_count"] = len(state.retrieval_queries)
        return state

    @staticmethod
    def _dedup_keep_order(items: list[str]) -> list[str]:
        seen: set[str] = set()
        output: list[str] = []
        for item in items:
            text = str(item or "").strip()
            if not text:
                continue
            key = text.lower()
            if key in seen:
                continue
            seen.add(key)
            output.append(text)
        return output
