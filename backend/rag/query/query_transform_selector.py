"""Select exactly one configured query transformer for each request."""

from __future__ import annotations

from collections.abc import Iterable

from rag.planning.retrieval_planner import QueryTransformMode
from rag.ports.query_transformer import QueryTransformerPort
from rag.query.query_expander import QueryExpansionResult


class QueryTransformSelector:
    """Execute one mutually exclusive transformer selected by intent policy."""

    def __init__(
        self,
        transformers: Iterable[QueryTransformerPort],
        *,
        spec_id: str | None = None,
        spec_version: str | None = None,
    ) -> None:
        self.spec_id = str(spec_id or "").strip() or None
        self.spec_version = str(spec_version or "").strip() or None
        self._transformers: dict[str, QueryTransformerPort] = {}
        for transformer in transformers:
            capability = str(
                getattr(transformer, "capability", transformer.__class__.__name__)
            ).strip()
            if capability in self._transformers:
                raise ValueError(f"duplicate query transformer capability: {capability}")
            self._transformers[capability] = transformer
        missing = {"identity", "multi_query", "hyde"} - set(self._transformers)
        if missing:
            raise ValueError(
                "static retrieval spec must provide query transformers: "
                + ", ".join(sorted(missing))
            )

    def transform(
        self,
        query: str,
        *,
        mode: QueryTransformMode,
        runtime_context: dict | None = None,
    ) -> QueryExpansionResult:
        normalized_query = str(query or "").strip()
        if not normalized_query:
            raise ValueError("query cannot be empty")
        try:
            transformer = self._transformers[mode]
        except KeyError as exc:
            raise ValueError(f"unsupported query transform mode: {mode}") from exc
        state = QueryExpansionResult(
            original_query=normalized_query,
            strategy=mode,
            retrieval_queries=[normalized_query],
            metadata={
                "mode": "mutually_exclusive_query_transform",
                "query_transform_mode": mode,
                "spec_id": self.spec_id,
                "spec_version": self.spec_version,
                "transformers": [],
            },
            runtime_context=dict(runtime_context or {}),
        )
        transformed = transformer.transform(state)
        transformed.retrieval_queries = self._dedup(transformed.retrieval_queries)
        transformed.metadata["query_count"] = len(transformed.retrieval_queries)
        transformed.metadata["query_transform_mode"] = mode
        return transformed

    @staticmethod
    def _dedup(items: list[str]) -> list[str]:
        seen: set[str] = set()
        output: list[str] = []
        for item in items:
            text = str(item or "").strip()
            key = text.lower()
            if text and key not in seen:
                seen.add(key)
                output.append(text)
        return output
