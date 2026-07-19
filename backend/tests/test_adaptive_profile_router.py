"""Adaptive intent planning over one static retrieval topology."""

from __future__ import annotations

from pathlib import Path

import pytest

from bootstrap.agent_quality_factory import AgentQualityFactory
from bootstrap.runtime_options import RuntimeOptions
from model_gateway.fake_llm_client import FakeLLMClient
from model_gateway.model_gateway import ModelGateway
from rag.planning.retrieval_planner import AdaptiveRetrievalPlanner
from rag.services.rag_service import RAGService


PROJECT_ROOT = Path(__file__).resolve().parents[2]
STATIC_SPEC = PROJECT_ROOT / "backend/rag/config/static_retrieval_v1.yaml"
INTENT_POLICY = PROJECT_ROOT / "backend/rag/config/intent_policy_v1.yaml"
GATE_POLICY = PROJECT_ROOT / "backend/rag/config/retrieval_gate_policy_v1.yaml"
GENERATION_POLICY = (
    PROJECT_ROOT
    / "backend/apps/enterprise_document/config/grounded_generation_v1.yaml"
)


def test_short_query_selects_exactly_one_multi_query_mode() -> None:
    plan = AdaptiveRetrievalPlanner().plan(query="how?")

    assert plan.query_transform_mode == "multi_query"
    assert plan.correction_budget == 1


def test_abstract_query_selects_exactly_one_hyde_mode() -> None:
    plan = AdaptiveRetrievalPlanner().plan(
        query="Explain the architecture mechanism behind transformer attention"
    )

    assert plan.query_transform_mode == "hyde"


def test_planner_does_not_predict_crag_from_citation_requirement() -> None:
    plan = AdaptiveRetrievalPlanner().plan(
        query="What is the deployment model?",
        request_context={"need_citation": True},
    )

    assert plan.query_transform_mode == "identity"
    assert "enable_corrective_retrieval" not in plan.to_dict()
    assert "evidence_sufficient" not in plan.to_dict()


def test_formal_task_changes_query_transform_but_not_correction_budget() -> None:
    plan = AdaptiveRetrievalPlanner(correction_budget=2).plan(
        query="Create an enterprise implementation report according to evidence",
        request_context={
            "task_type": "scheme_generation",
            "need_citation": True,
        },
    )

    assert plan.query_transform_mode == "multi_query"
    assert plan.correction_budget == 2


def test_request_override_is_mutually_exclusive_and_budget_only() -> None:
    plan = AdaptiveRetrievalPlanner().plan(
        query="short",
        request_context={
            "retrieval_plan_overrides": {
                "query_transform_mode": "hyde",
                "correction_budget": 0,
            }
        },
    )

    assert plan.query_transform_mode == "hyde"
    assert plan.correction_budget == 0

    with pytest.raises(ValueError, match="query_transform_mode"):
        AdaptiveRetrievalPlanner().plan(
            query="short",
            request_context={
                "retrieval_plan_overrides": {
                    "query_transform_mode": "multi_query+hyde"
                }
            },
        )


def test_service_uses_one_static_spec_and_two_small_policies() -> None:
    service = RAGService(
        PROJECT_ROOT,
        static_retrieval_spec_file=STATIC_SPEC,
        intent_policy_file=INTENT_POLICY,
        retrieval_gate_policy_file=GATE_POLICY,
    )

    config = service.retrieval_runtime.config
    assert Path(config.static_retrieval_spec_file) == STATIC_SPEC
    assert Path(config.intent_policy_file) == INTENT_POLICY
    assert Path(config.retrieval_gate_policy_file) == GATE_POLICY


def test_generation_quality_policy_is_independent_from_static_retrieval_spec() -> None:
    gateway = ModelGateway(default_model_name="fake_llm")
    gateway.register_client(FakeLLMClient())
    options = RuntimeOptions(
        use_real_rag=True,
        rag_project_root=PROJECT_ROOT,
        enable_agent_self_rag=True,
        enable_semantic_gate=False,
        semantic_gate_model_name="fake_llm",
        rag_static_retrieval_spec_file=STATIC_SPEC,
        rag_intent_policy_file=INTENT_POLICY,
        rag_retrieval_gate_policy_file=GATE_POLICY,
        grounded_generation_policy_file=GENERATION_POLICY,
    )

    runtime = AgentQualityFactory().build(
        options=options,
        model_gateway=gateway,
        model_name="fake_llm",
    )

    assert runtime.metadata["policy_id"] == "grounded_generation_v1"
    assert runtime.metadata["schema_version"] == "grounded_generation_policy_v1"
    assert "static_retrieval_spec" not in runtime.metadata
