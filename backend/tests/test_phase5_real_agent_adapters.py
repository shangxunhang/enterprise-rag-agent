"""Round-5 acceptance for production RAG and ModelGateway Agent adapters."""

from __future__ import annotations

from contextlib import ExitStack
from dataclasses import replace
import inspect
import json
import os
from pathlib import Path
from unittest.mock import patch

from application.mainline_service import MainlineApplicationService
from apps.enterprise_document.services.scheme_writer.agent_integration.section_agent_adapter import (
    MainlineSectionAgentAdapter,
)
from apps.enterprise_document.services.scheme_writer.agent_integration.section_agent_ports import (
    MainlineResearchToolGateway,
)
from apps.enterprise_document.services.scheme_writer.agent_integration.section_agent_reasoners import (
    MainlineResearchReasoner,
    MainlineVerifierModelReasoner,
    MainlineWriterModelReasoner,
)
from apps.enterprise_document.services.scheme_writer.evidence_service import (
    SchemeEvidenceService,
)
from apps.enterprise_document.workflows.top_level_business_graph import (
    EnterpriseDocumentWorkflowEngine,
)
from bootstrap.supervisor_factory import SupervisorFactory
from core.config import get_settings
from model_gateway.integrations.langchain import LangChainModelGatewayFacade
from mainline_runtime import build_project_input


class _InspectingFactory(SupervisorFactory):
    def __init__(self) -> None:
        super().__init__()
        self.supervisors: list[object] = []
        self.engines: list[EnterpriseDocumentWorkflowEngine] = []

    def build(self, **kwargs):
        supervisor = super().build(**kwargs)
        engine = supervisor.workflow_engine
        assert isinstance(engine, EnterpriseDocumentWorkflowEngine)
        self.supervisors.append(supervisor)
        self.engines.append(engine)
        return supervisor


def _run_fake(
    output_root: Path,
    *,
    run_id: str,
    enable_section_retrieval: bool = False,
    patchers=(),
):
    settings = replace(
        get_settings(),
        data_root=output_root,
        default_model_name="fake_llm",
        supervisor_model_name="fake_llm",
        enable_llm_routing=False,
        trace_enabled=True,
        data_capture_enabled=True,
    )
    environment = {
        "USE_REAL_RAG_TOOL": "false",
        "FAKE_RAG_SCENARIO": "default",
        "FAKE_LLM_SCENARIO": "default",
        "ENABLE_AGENT_SELF_RAG": "false",
        "ENABLE_SEMANTIC_GATE": "false",
    }
    project_input = None
    allow_demo_defaults = True
    task_id = None
    if enable_section_retrieval:
        task_id = f"task_{run_id}"
        project_input = build_project_input(
            task_id,
            "生成企业级 RAG-Agent 系统建设方案",
            allow_demo_defaults=True,
        ).model_dump(mode="json")
        project_input["generation_requirements"]["extra"].update(
            {
                "enable_section_aware_retrieval": True,
                "enable_corrective_section_retrieval": False,
            }
        )
        allow_demo_defaults = False

    factory = _InspectingFactory()
    with ExitStack() as stack:
        stack.enter_context(patch.dict(os.environ, environment))
        for patcher in patchers:
            stack.enter_context(patcher)
        summary = MainlineApplicationService(
            supervisor_factory=factory
        ).run(
            project_root=Path(__file__).resolve().parents[2],
            user_input="生成企业级 RAG-Agent 系统建设方案",
            run_id=run_id,
            task_id=task_id,
            output_root=output_root,
            clean_existing=True,
            settings=settings,
            project_input=project_input,
            allow_demo_defaults=allow_demo_defaults,
            enable_agent_self_rag=False,
        )
    return summary, factory


def _trace_events(summary: dict) -> list[dict]:
    path = Path(summary["paths"]["trace"])
    return [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def test_round5_composition_reuses_existing_production_ports(tmp_path) -> None:
    summary, factory = _run_fake(
        tmp_path,
        run_id="round5_composition_ports",
    )
    assert summary["status"] == "success"
    supervisor = factory.supervisors[0]
    engine = factory.engines[0]
    section_graph = engine.section_execution_graph
    assert section_graph is not None
    adapter = section_graph.section_agent_adapter
    assert isinstance(adapter, MainlineSectionAgentAdapter)
    assert adapter.integration_round == "round5"
    assert adapter.reasoner_kind == "production_adapter"
    assert adapter.uses_research_tool_gateway is True
    assert adapter.research_tool_gateway is not None
    assert (
        adapter.research_tool_gateway.evidence_service
        is engine.stage_service.evidence_service
    )
    assert isinstance(adapter.research_agent.reasoner, MainlineResearchReasoner)
    assert isinstance(adapter.writer_agent.reasoner, MainlineWriterModelReasoner)
    assert isinstance(adapter.verifier_agent.reasoner, MainlineVerifierModelReasoner)
    assert adapter.writer_agent.reasoner.model_gateway is supervisor.model_gateway
    assert adapter.verifier_agent.reasoner.model_gateway is supervisor.model_gateway


def test_research_agent_tool_gateway_owns_section_retrieval(tmp_path) -> None:
    gateway_calls: list[dict] = []
    evidence_calls: list[dict] = []
    original_gateway = MainlineResearchToolGateway.invoke_bound
    original_retrieve = SchemeEvidenceService.retrieve

    def record_gateway(
        self,
        context,
        request,
        *,
        call_id,
        tool_name,
        arguments,
    ):
        gateway_calls.append(
            {
                "role": context.agent_role.value,
                "section_id": context.section_id,
                "tool_name": tool_name,
                "scope": arguments.get("scope"),
                "call_id": call_id,
            }
        )
        return original_gateway(
            self,
            context,
            request,
            call_id=call_id,
            tool_name=tool_name,
            arguments=arguments,
        )

    def record_retrieve(self, shared_state, project_input, **kwargs):
        evidence_calls.append(
            {
                "scope": kwargs.get("scope", "document"),
                "section_id": kwargs.get("section_id"),
            }
        )
        return original_retrieve(self, shared_state, project_input, **kwargs)

    summary, _factory = _run_fake(
        tmp_path,
        run_id="round5_research_tool_gateway",
        enable_section_retrieval=True,
        patchers=(
            patch.object(
                MainlineResearchToolGateway,
                "invoke_bound",
                record_gateway,
            ),
            patch.object(
                SchemeEvidenceService,
                "retrieve",
                record_retrieve,
            ),
        ),
    )
    assert summary["status"] == "success"
    required = {
        item["section_title"]
        for item in summary["scheme_writer_output"]["section_evidence"]
        if item["retrieval_scope"] == "section"
    }
    assert required
    section_evidence_calls = [
        item for item in evidence_calls if item["scope"] == "section"
    ]
    assert len(gateway_calls) == len(required)
    assert len(section_evidence_calls) == len(required)
    assert all(item["role"] == "research" for item in gateway_calls)
    assert all(
        item["tool_name"] == "knowledge.retrieve" for item in gateway_calls
    )
    assert all(item["scope"] == "section" for item in gateway_calls)

    finished = [
        event
        for event in _trace_events(summary)
        if event.get("event_type") == "agent_finished"
        and event.get("component_name") == "ResearchAgent"
        and event.get("step_name") == "retrieve_section_evidence"
    ]
    assert len(finished) == len(required)
    assert all(event["output_summary"]["tool_call_count"] == 1 for event in finished)
    assert all(event["output_summary"]["fake_reasoner"] is False for event in finished)


def test_writer_and_verifier_use_composed_model_gateway(tmp_path) -> None:
    requests: list[object] = []
    original_generate = LangChainModelGatewayFacade.generate

    def record_generate(self, request):
        requests.append(request)
        return original_generate(self, request)

    summary, factory = _run_fake(
        tmp_path,
        run_id="round5_model_gateway_reasoners",
        patchers=(
            patch.object(
                LangChainModelGatewayFacade,
                "generate",
                record_generate,
            ),
        ),
    )
    assert summary["status"] == "success"
    writer_requests = [
        request
        for request in requests
        if request.caller_agent == "WriterAgent"
    ]
    verifier_requests = [
        request
        for request in requests
        if request.caller_agent == "VerifierAgent"
    ]
    assert len(writer_requests) == 8
    assert len(verifier_requests) == 8
    assert all(request.model_role == "section_generation" for request in writer_requests)
    assert all(request.model_role == "semantic_gate" for request in verifier_requests)
    assert all(
        request.extra.get("reasoner_contract") == "mainline_section_agent_v1"
        for request in [*writer_requests, *verifier_requests]
    )

    adapter = factory.engines[0].section_execution_graph.section_agent_adapter
    assert adapter.writer_agent.reasoner.model_gateway is factory.supervisors[0].model_gateway
    assert adapter.verifier_agent.reasoner.model_gateway is factory.supervisors[0].model_gateway


def test_round5_agent_adapters_do_not_construct_clients() -> None:
    sources = "\n".join(
        [
            inspect.getsource(MainlineResearchToolGateway),
            inspect.getsource(MainlineResearchReasoner),
            inspect.getsource(MainlineWriterModelReasoner),
            inspect.getsource(MainlineVerifierModelReasoner),
        ]
    )
    forbidden = (
        "MilvusClient(",
        "ModelGateway(",
        "RAGService(",
        "LocalQwen",
        "AutoModel",
        "AutoTokenizer",
        "transformers",
    )
    assert all(marker not in sources for marker in forbidden)
