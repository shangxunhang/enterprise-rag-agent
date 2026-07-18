"""Runtime regression tests for AdaptiveRAGRouter dependencies."""

from rag.judge.adaptive_rag_router import AdaptiveRAGRouter


def test_adaptive_rag_router_constructs_and_routes_without_llm() -> None:
    router = AdaptiveRAGRouter(use_llm=False)

    decision = router.route(
        query="根据资料生成企业级 RAG-Agent 系统建设方案",
        task_type="scheme_generation",
    )

    assert decision.selected_strategy
    assert decision.latency_ms is not None
    assert decision.latency_ms >= 0
