"""内置 Query Transformer 插件。

QueryExpander remains the algorithm provider. The retrieval pipeline selects
exactly one transformer through QueryTransformSelector.
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
    """从组合根读取 Query LLM、生成参数和启用开关，构造共享算法服务。"""
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
    """把实际插件身份、实现路径和输出 Query 数追加到扩展血缘。"""
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

    capability = "identity"

    def __init__(self, *, build_context: Any = None, **params: Any) -> None:
        """显式基线插件不接受参数；未知参数直接报错而非静默忽略。"""
        del build_context
        if params:
            unexpected = ", ".join(sorted(params))
            raise ValueError(f"identity query transformer has no params: {unexpected}")

    def transform(self, state: QueryExpansionResult) -> QueryExpansionResult:
        """确保原 Query 在检索集合首位，并记录显式无变换基线。"""
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

    capability = "multi_query"

    def __init__(
        self,
        *,
        build_context: Any = None,
        num_rewrites: int = 3,
        use_llm: bool | None = None,
        fallback_to_deterministic: bool = True,
    ) -> None:
        """配置改写数量，并构造可 LLM/确定性回退的 QueryExpander。"""
        self.num_rewrites = max(0, int(num_rewrites))
        self.expander = _build_expander(
            build_context=build_context,
            use_llm=use_llm,
            fallback_to_deterministic=bool(fallback_to_deterministic),
        )

    def transform(self, state: QueryExpansionResult) -> QueryExpansionResult:
        """生成 RAG-Fusion 改写，稳定去重后追加到现有检索 Query 集合。"""
        rewrites, details = self.expander.rewrite_queries(
            query=state.original_query,
            num_rewrites=self.num_rewrites,
            runtime_context=state.runtime_context,
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

    capability = "hyde"

    def __init__(
        self,
        *,
        build_context: Any = None,
        use_llm: bool | None = None,
        fallback_to_deterministic: bool = True,
    ) -> None:
        """构造只生成一条假想文档的 QueryExpander。"""
        self.expander = _build_expander(
            build_context=build_context,
            use_llm=use_llm,
            fallback_to_deterministic=bool(fallback_to_deterministic),
        )

    def transform(self, state: QueryExpansionResult) -> QueryExpansionResult:
        """生成 HyDE 文本并作为额外检索 Query 追加；空结果不改变 Query 集合。"""
        hyde_query, details = self.expander.build_hypothetical_document(
            query=state.original_query,
            runtime_context=state.runtime_context,
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
