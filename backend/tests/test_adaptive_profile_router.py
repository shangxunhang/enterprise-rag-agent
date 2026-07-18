from __future__ import annotations

from pathlib import Path

from bootstrap.agent_quality_factory import AgentQualityFactory
from bootstrap.runtime_options import RuntimeOptions
from model_gateway.fake_llm_client import FakeLLMClient
from model_gateway.model_gateway import ModelGateway
from rag.adapters.legacy.backend import LegacyRAGBackend
from rag.routing.runtime import AdaptiveProfileRouterRuntime


PROJECT_ROOT = Path(__file__).resolve().parents[2]
ROUTER_CONFIG = PROJECT_ROOT / "backend/rag/routing/adaptive_router_v1.yaml"


def _runtime() -> AdaptiveProfileRouterRuntime:
    return AdaptiveProfileRouterRuntime(
        config_file=ROUTER_CONFIG,
        project_root=PROJECT_ROOT,
    )


def test_router_config_and_profile_references_validate() -> None:
    report = _runtime().validation_report()

    assert report["status"] == "success"
    assert report["profile_count"] == 6
    assert report["rule_count"] == 5
    assert report["default_profile_id"] == "hybrid_v1"
    assert report["agent_quality_profile_id"] == "self_rag_v1"


def test_formal_grounded_generation_routes_to_corrective_self_rag() -> None:
    decision = _runtime().route(
        {
            "query": "根据资料生成企业级 RAG-Agent 系统建设方案",
            "extra_metadata": {
                "task_type": "scheme_generation",
                "required_sections": ["项目概述", "建设内容", "技术方案", "安全设计"],
                "citation_required_sections": ["建设内容", "技术方案", "安全设计"],
                "need_citation": True,
            },
        }
    )

    assert decision.selected_profile_id == "c_rag_corrective_self_rag_v1"
    assert decision.matched_rule_id == "formal_grounded_generation"
    assert decision.signals["uses_retrieval_confidence"] is False
    assert decision.signals["uses_quality_threshold"] is False


def test_router_selects_fusion_hyde_and_default_profiles() -> None:
    runtime = _runtime()

    short = runtime.route({"query": "这个怎么搞", "extra_metadata": {"need_citation": False}})
    abstract = runtime.route(
        {
            "query": "Transformer 注意力机制的本质是什么",
            "extra_metadata": {"need_citation": False},
        }
    )
    factual = runtime.route(
        {
            "query": "请从现有项目资料中明确给出项目正式名称、建设单位名称以及项目建设地点",
            "extra_metadata": {"need_citation": False},
        }
    )

    assert short.selected_profile_id == "rag_fusion_v1"
    assert abstract.selected_profile_id == "hyde_v1"
    assert factual.selected_profile_id == "hybrid_v1"


def test_explicit_profile_request_is_allowlisted_and_unknown_falls_back() -> None:
    runtime = _runtime()

    allowed = runtime.route(
        {
            "query": "test",
            "extra_metadata": {"requested_profile_id": "hyde_v1"},
        }
    )
    denied = runtime.route(
        {
            "query": "test",
            "extra_metadata": {"requested_profile_id": "not_registered"},
        }
    )

    assert allowed.selected_profile_id == "hyde_v1"
    assert allowed.method == "explicit_profile_request"
    assert denied.selected_profile_id == "hybrid_v1"
    assert denied.fallback_used is True


class _FakeRAGTool:
    def __init__(self, profile_path: Path) -> None:
        self.profile_path = profile_path
        self.calls: list[dict] = []

    def run(self, payload: dict) -> dict:
        self.calls.append(payload)
        return {
            "success": True,
            "data": {"query": payload["query"]},
            "metadata": {"pipeline_config_file": str(self.profile_path)},
        }


def test_backend_routes_before_building_selected_profile_and_attaches_decision() -> None:
    built: list[Path] = []

    def builder(path: Path) -> _FakeRAGTool:
        built.append(path)
        return _FakeRAGTool(path)

    backend = LegacyRAGBackend(
        PROJECT_ROOT,
        pipeline_config_file=ROUTER_CONFIG,
        tool_builder=builder,
    )
    result = backend.run(
        {
            "query": "根据资料生成企业级 RAG-Agent 系统建设方案",
            "extra_metadata": {
                "task_type": "scheme_generation",
                "required_sections": ["项目概述", "建设内容", "技术方案", "安全设计"],
                "citation_required_sections": ["建设内容", "技术方案", "安全设计"],
            },
        }
    )

    decision = result["data"]["adaptive_profile_router"]
    assert decision["selected_profile_id"] == "c_rag_corrective_self_rag_v1"
    assert built == [
        (PROJECT_ROOT / "backend/rag/profiles/c_rag_corrective_self_rag_v1.yaml").resolve()
    ]
    selected_tool = backend._routed_tools["c_rag_corrective_self_rag_v1"]
    routed_payload = selected_tool.calls[0]
    assert routed_payload["retrieval_strategy"] == "c_rag_corrective_self_rag_v1"
    assert "adaptive_profile_router" in routed_payload["extra_metadata"]


def test_backend_caches_one_runtime_per_selected_profile() -> None:
    built: list[Path] = []

    def builder(path: Path) -> _FakeRAGTool:
        built.append(path)
        return _FakeRAGTool(path)

    backend = LegacyRAGBackend(
        PROJECT_ROOT,
        pipeline_config_file=ROUTER_CONFIG,
        tool_builder=builder,
    )
    payload = {"query": "这个怎么搞", "extra_metadata": {"need_citation": False}}
    backend.run(payload)
    backend.run(payload)

    assert len(built) == 1
    assert built[0].name == "rag_fusion_v1.yaml"


def test_agent_quality_factory_uses_router_declared_static_quality_profile() -> None:
    gateway = ModelGateway(default_model_name="fake_llm")
    gateway.register_client(FakeLLMClient())
    options = RuntimeOptions(
        use_real_rag=True,
        rag_project_root=PROJECT_ROOT,
        rag_skip_rerank=False,
        retrieval_strategy="hybrid",
        enable_agent_self_rag=True,
        enable_semantic_gate=False,
        semantic_gate_model_name="fake_llm",
        rag_pipeline_config_file=ROUTER_CONFIG,
    )

    runtime = AgentQualityFactory().build(
        options=options,
        model_gateway=gateway,
        model_name="fake_llm",
    )

    assert runtime.metadata["profile_id"] == "self_rag_v1"
    assert (
        runtime.metadata["adaptive_profile_router"]["agent_quality_selection_mode"]
        == "static_router_profile_v1"
    )
    assert runtime.generation_checker.execution_metadata()["enabled"] is True
