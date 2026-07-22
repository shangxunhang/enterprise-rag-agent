# =============================================================================
# 中文阅读说明：RAG 核心模块，负责查询变换、召回、融合、重排、证据评估和上下文组装。
# 主要定义：_build_expander、_record_component、IdentityQueryTransformer、MultiQueryTransformer、HyDEQueryTransformer。建议先从公开入口函数开始，再沿调用关系向下阅读。
# =============================================================================
"""Built-in query-transformer plugins.

QueryExpander remains the algorithm provider. The retrieval pipeline selects
exactly one transformer through QueryTransformSelector.
"""

from __future__ import annotations

from typing import Any

from rag.query.query_expander import QueryExpander, QueryExpansionResult


# 阅读注释（函数）：构建 expander。
def _build_expander(
    *,
    build_context: Any,
    use_llm: bool | None,
    fallback_to_deterministic: bool,
) -> QueryExpander:
    """构建 expander。

    参数:
        build_context: build 上下文，具体约束请结合类型标注和调用方确认。
        use_llm: use LLM，具体约束请结合类型标注和调用方确认。
        fallback_to_deterministic: fallback to deterministic，具体约束请结合类型标注和调用方确认。

    返回:
        QueryExpander

    阅读提示:
        主要直接调用：isinstance, context.get, QueryExpander, bool, dict。
    """
    context = build_context if isinstance(build_context, dict) else {}
    configured_use_llm = context.get("enable_query_expansion_llm", True)
    return QueryExpander(
        llm_generator=context.get("query_llm_generator"),
        use_llm=(configured_use_llm if use_llm is None else bool(use_llm)),
        generation_params=dict(context.get("query_expansion_generation_params") or {}),
        fallback_to_deterministic=fallback_to_deterministic,
    )


# 阅读注释（函数）：记录 component。
def _record_component(
    state: QueryExpansionResult,
    *,
    plugin: Any,
    output_count: int,
    details: dict[str, Any] | None = None,
) -> None:
    """记录 component。

    参数:
        state: 工作流共享状态。
        plugin: 插件，具体约束请结合类型标注和调用方确认。
        output_count: 输出 count，具体约束请结合类型标注和调用方确认。
        details: details，具体约束请结合类型标注和调用方确认。

    返回:
        None

    阅读提示:
        主要直接调用：getattr, hasattr, metadata.to_dict, int, append, state.metadata.setdefault。
    """
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


# 阅读注释（类）：封装 identity 查询 transformer，集中封装相关状态、依赖和行为。
class IdentityQueryTransformer:
    """No-op transformer used as an explicit baseline plugin."""

    capability = "identity"

    # 阅读注释（函数）：初始化 IdentityQueryTransformer，保存运行所需的依赖、配置或状态。
    def __init__(self, *, build_context: Any = None, **params: Any) -> None:
        """初始化 IdentityQueryTransformer，保存运行所需的依赖、配置或状态。

        参数:
            build_context: build 上下文，具体约束请结合类型标注和调用方确认。
            **params: params，具体约束请结合类型标注和调用方确认。

        返回:
            None

        阅读提示:
            主要直接调用：join, sorted, ValueError。
        """
        del build_context
        if params:
            unexpected = ", ".join(sorted(params))
            raise ValueError(f"identity query transformer has no params: {unexpected}")

    # 阅读注释（函数）：转换 IdentityQueryTransformer。
    def transform(self, state: QueryExpansionResult) -> QueryExpansionResult:
        """转换 IdentityQueryTransformer。

        参数:
            state: 工作流共享状态。

        返回:
            QueryExpansionResult

        阅读提示:
            主要直接调用：state.retrieval_queries.insert, _record_component, len。
        """
        if state.original_query not in state.retrieval_queries:
            state.retrieval_queries.insert(0, state.original_query)
        _record_component(
            state,
            plugin=self,
            output_count=len(state.retrieval_queries),
        )
        return state


# 阅读注释（类）：封装 multi 查询 transformer，集中封装相关状态、依赖和行为。
class MultiQueryTransformer:
    """Generate RAG-Fusion query rewrites and append them to the query set."""

    capability = "multi_query"

    # 阅读注释（函数）：初始化 MultiQueryTransformer，保存运行所需的依赖、配置或状态。
    def __init__(
        self,
        *,
        build_context: Any = None,
        num_rewrites: int = 3,
        use_llm: bool | None = None,
        fallback_to_deterministic: bool = True,
    ) -> None:
        """初始化 MultiQueryTransformer，保存运行所需的依赖、配置或状态。

        参数:
            build_context: build 上下文，具体约束请结合类型标注和调用方确认。
            num_rewrites: num rewrites，具体约束请结合类型标注和调用方确认。
            use_llm: use LLM，具体约束请结合类型标注和调用方确认。
            fallback_to_deterministic: fallback to deterministic，具体约束请结合类型标注和调用方确认。

        返回:
            None

        阅读提示:
            主要直接调用：max, int, _build_expander, bool。
        """
        self.num_rewrites = max(0, int(num_rewrites))
        self.expander = _build_expander(
            build_context=build_context,
            use_llm=use_llm,
            fallback_to_deterministic=bool(fallback_to_deterministic),
        )

    # 阅读注释（函数）：转换 MultiQueryTransformer。
    def transform(self, state: QueryExpansionResult) -> QueryExpansionResult:
        """转换 MultiQueryTransformer。

        参数:
            state: 工作流共享状态。

        返回:
            QueryExpansionResult

        阅读提示:
            主要直接调用：self.expander.rewrite_queries, self.expander.dedup_keep_order, _record_component, len。
        """
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


# 阅读注释（类）：封装 hy dequery transformer，集中封装相关状态、依赖和行为。
class HyDEQueryTransformer:
    """Generate one hypothetical document and append it as a retrieval query."""

    capability = "hyde"

    # 阅读注释（函数）：初始化 HyDEQueryTransformer，保存运行所需的依赖、配置或状态。
    def __init__(
        self,
        *,
        build_context: Any = None,
        use_llm: bool | None = None,
        fallback_to_deterministic: bool = True,
    ) -> None:
        """初始化 HyDEQueryTransformer，保存运行所需的依赖、配置或状态。

        参数:
            build_context: build 上下文，具体约束请结合类型标注和调用方确认。
            use_llm: use LLM，具体约束请结合类型标注和调用方确认。
            fallback_to_deterministic: fallback to deterministic，具体约束请结合类型标注和调用方确认。

        返回:
            None

        阅读提示:
            主要直接调用：_build_expander, bool。
        """
        self.expander = _build_expander(
            build_context=build_context,
            use_llm=use_llm,
            fallback_to_deterministic=bool(fallback_to_deterministic),
        )

    # 阅读注释（函数）：转换 HyDEQueryTransformer。
    def transform(self, state: QueryExpansionResult) -> QueryExpansionResult:
        """转换 HyDEQueryTransformer。

        参数:
            state: 工作流共享状态。

        返回:
            QueryExpansionResult

        阅读提示:
            主要直接调用：self.expander.build_hypothetical_document, self.expander.dedup_keep_order, _record_component, len, bool。
        """
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
