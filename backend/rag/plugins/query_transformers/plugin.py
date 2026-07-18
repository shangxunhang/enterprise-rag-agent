"""Built-in query-transformer plugins.

The legacy QueryExpander remains the algorithm provider for this migration
slice. The retrieval pipeline only sees QueryTransformChain and never branches
on concrete query-expansion strategy names.
"""

from __future__ import annotations

from typing import Any

from rag.query.query_expander import QueryExpander, QueryExpansionResult


def _build_expander(
    *,
    build_context: Any,
    use_llm: bool | None,
    fallback_to_deterministic: bool,
) -> QueryExpander:
    context = build_context if isinstance(build_context, dict) else {}
    configured_use_llm = context.get("enable_query_expansion_llm", True)
    return QueryExpander(
        llm_generator=context.get("query_llm_generator"),
        use_llm=(configured_use_llm if use_llm is None else bool(use_llm)),
        generation_params=dict(context.get("query_expansion_generation_params") or {}),
        fallback_to_deterministic=fallback_to_deterministic,
    )


def _record_component(
    state: QueryExpansionResult,
    *,
    plugin: Any,
    output_count: int,
    details: dict[str, Any] | None = None,
) -> None:
    metadata = getattr(plugin, "plugin_metadata", None)
    item = (
        metadata.to_dict()
        if metadata is not None and hasattr(metadata, "to_dict")
        else {
            "category": "query_transformer",
            "name": plugin.__class__.__name__,
            "version": "unknown",
            "implementation": (
                f"{plugin.__class__.__module__}.{plugin.__class__.__qualname__}"
            ),
        }
    )
    item["output_query_count"] = int(output_count)
    if details:
        item["details"] = details
    state.metadata.setdefault("transformers", []).append(item)


class IdentityQueryTransformer:
    """No-op transformer used as an explicit baseline plugin."""

    def __init__(self, *, build_context: Any = None, **params: Any) -> None:
        del build_context
        if params:
            unexpected = ", ".join(sorted(params))
            raise ValueError(f"identity query transformer has no params: {unexpected}")

    def transform(self, state: QueryExpansionResult) -> QueryExpansionResult:
        if state.original_query not in state.retrieval_queries:
            state.retrieval_queries.insert(0, state.original_query)
        _record_component(
            state,
            plugin=self,
            output_count=len(state.retrieval_queries),
        )
        return state


class MultiQueryTransformer:
    """Generate RAG-Fusion query rewrites and append them to the query set."""

    def __init__(
        self,
        *,
        build_context: Any = None,
        num_rewrites: int = 3,
        use_llm: bool | None = None,
        fallback_to_deterministic: bool = True,
    ) -> None:
        self.num_rewrites = max(0, int(num_rewrites))
        self.expander = _build_expander(
            build_context=build_context,
            use_llm=use_llm,
            fallback_to_deterministic=bool(fallback_to_deterministic),
        )

    def transform(self, state: QueryExpansionResult) -> QueryExpansionResult:
        rewrites, details = self.expander.rewrite_queries(
            query=state.original_query,
            num_rewrites=self.num_rewrites,
        )
        state.rewritten_queries = self.expander.dedup_keep_order(
            [*state.rewritten_queries, *rewrites]
        )
        state.retrieval_queries = self.expander.dedup_keep_order(
            [*state.retrieval_queries, *rewrites]
        )
        state.metadata["rag_fusion"] = details
        _record_component(
            state,
            plugin=self,
            output_count=len(state.retrieval_queries),
            details={"rewrite_count": len(rewrites)},
        )
        return state


class HyDEQueryTransformer:
    """Generate one hypothetical document and append it as a retrieval query."""

    def __init__(
        self,
        *,
        build_context: Any = None,
        use_llm: bool | None = None,
        fallback_to_deterministic: bool = True,
    ) -> None:
        self.expander = _build_expander(
            build_context=build_context,
            use_llm=use_llm,
            fallback_to_deterministic=bool(fallback_to_deterministic),
        )

    def transform(self, state: QueryExpansionResult) -> QueryExpansionResult:
        hyde_query, details = self.expander.build_hypothetical_document(
            query=state.original_query,
        )
        state.hyde_query = hyde_query
        if hyde_query:
            state.retrieval_queries = self.expander.dedup_keep_order(
                [*state.retrieval_queries, hyde_query]
            )
        state.metadata["hyde"] = details
        _record_component(
            state,
            plugin=self,
            output_count=len(state.retrieval_queries),
            details={"hyde_generated": bool(hyde_query)},
        )
        return state
