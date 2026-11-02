"""Regression coverage for the production Self-RAG judge contract."""

from __future__ import annotations

import importlib


_RESULT_KEYS = {
    "enabled",
    "method",
    "is_supported",
    "faithfulness_label",
    "answer_relevance_label",
    "need_rewrite",
    "need_retrieve_more",
    "unsupported_claims",
    "problems",
    "score",
    "latency_ms",
    "raw_output",
    "metadata",
    "verification_mode",
    "quality_mode_degraded",
}


def _plugins_module():
    return importlib.import_module(
        "apps.enterprise_document.quality.plugins"
    )


def _supported_payload(checker) -> dict:
    return checker.check(
        query="数据库备份策略",
        answer="数据库采用每周全量备份和每日增量备份。",
        context="数据库采用每周全量备份和每日增量备份。",
        citations=[{"citation_id": "C1"}],
    )


def test_quality_plugins_module_imports() -> None:
    module = _plugins_module()

    assert callable(module.SelfRAGLiteGenerationCheckerPlugin)


def test_self_rag_plugin_constructs_with_contract_metadata() -> None:
    checker = _plugins_module().SelfRAGLiteGenerationCheckerPlugin(
        use_llm=False
    )

    metadata = checker.execution_metadata()
    assert metadata == {
        "enabled": True,
        "mode": "self_rag_lite",
        "use_llm": False,
        "llm_available": False,
        "model_gateway_available": False,
        "fallback_to_deterministic": True,
        "noise_terms": [],
    }


def test_supported_generation_returns_passing_output_contract() -> None:
    checker = _plugins_module().SelfRAGLiteGenerationCheckerPlugin(
        use_llm=False
    )

    result = _supported_payload(checker)

    assert _RESULT_KEYS <= result.keys()
    assert result["enabled"] is True
    assert result["method"] == "deterministic_fallback"
    assert result["verification_mode"] == "deterministic_primary"
    assert result["quality_mode_degraded"] is False
    assert result["is_supported"] is True
    assert result["need_rewrite"] is False
    assert result["need_retrieve_more"] is False
    assert result["faithfulness_label"] == "high"
    assert result["answer_relevance_label"] in {"high", "medium"}
    assert result["score"] >= 0.55


def test_unsupported_generation_does_not_return_passing_result() -> None:
    checker = _plugins_module().SelfRAGLiteGenerationCheckerPlugin(
        use_llm=False
    )

    result = checker.check(
        query="数据库备份策略",
        answer="员工食堂每天供应咖啡和甜点。",
        context="数据库采用每周全量备份和每日增量备份。",
        citations=[],
    )

    assert _RESULT_KEYS <= result.keys()
    assert result["is_supported"] is False
    assert result["need_rewrite"] is True
    assert result["need_retrieve_more"] is True
    assert result["faithfulness_label"] == "low"
    assert result["answer_relevance_label"] == "low"
    assert result["score"] < 0.55
    assert result["problems"]


def test_missing_optional_model_dependency_degrades_deterministically() -> None:
    checker = _plugins_module().SelfRAGLiteGenerationCheckerPlugin(
        build_context={"enable_quality_llm": True},
        use_llm=True,
        fallback_to_deterministic=True,
    )

    result = _supported_payload(checker)

    assert _RESULT_KEYS <= result.keys()
    assert result["method"] == "deterministic_fallback"
    assert result["verification_mode"] == "degraded_deterministic_fallback"
    assert result["quality_mode_degraded"] is True
    assert result["metadata"]["llm_enabled"] is False
    assert result["is_supported"] is True


def test_top_level_business_graph_imports() -> None:
    module = importlib.import_module(
        "apps.enterprise_document.workflows.top_level_business_graph"
    )

    assert callable(module.EnterpriseDocumentWorkflowEngine)
