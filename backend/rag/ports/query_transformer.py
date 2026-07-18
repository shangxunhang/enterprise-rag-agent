"""Stable contract for configuration-driven query transformers."""

from __future__ import annotations

from typing import Protocol, TYPE_CHECKING

if TYPE_CHECKING:
    from rag.query.query_expander import QueryExpansionResult


class QueryTransformerPort(Protocol):
    """Transform a query-expansion state without knowing pipeline internals."""

    def transform(
        self,
        state: "QueryExpansionResult",
    ) -> "QueryExpansionResult":
        ...
