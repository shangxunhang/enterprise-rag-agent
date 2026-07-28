"""Final production takeover tests for LangChain Runnable and legacy retirement."""

from __future__ import annotations

from contextlib import ExitStack
from dataclasses import replace
import inspect
import json
import os
from pathlib import Path
from unittest.mock import patch

from application.mainline_service import MainlineApplicationService
from apps.enterprise_document.agents.scheme_writer_agent import SchemeWriterAgent
from apps.enterprise_document.services.scheme_writer.section_execution_coordinator import (
    SectionExecutionCoordinator,
)
from apps.enterprise_document.services.scheme_writer.use_case import (
    SchemeGenerationUseCase,
)
from apps.enterprise_document.workflows.top_level_business_graph import (
    EnterpriseDocumentWorkflowEngine,
)
from bootstrap.supervisor_factory import SupervisorFactory
from core.config import get_settings
from model_gateway.integrations.langchain.runtime_facade import (
    LangChainModelGatewayFacade,
)
from rag.integrations.langchain.runtime_facade import LangChainRAGServiceFacade


class _InspectingFactory(SupervisorFactory):
    def __init__(self) -> None:
        super().__init__()
        self.engines: list[EnterpriseDocumentWorkflowEngine] = []
        self.model_facades: list[LangChainModelGatewayFacade] = []
        self.rag_facades: list[LangChainRAGServiceFacade] = []

    def build(self, **kwargs):
        supervisor = super().build(**kwargs)
        engine = supervisor.workflow_engine
        assert isinstance(engine, EnterpriseDocumentWorkflowEngine)
        assert isinstance(supervisor.model_gateway, LangChainModelGatewayFacade)
        rag_service = engine.stage_service.evidence_service.rag_service
        assert isinstance(rag_service, LangChainRAGServiceFacade)
        self.engines.append(engine)
        self.model_facades.append(supervisor.model_gateway)
        self.rag_facades.append(rag_service)
        return supervisor


def _run_final_fake(output_root: Path, *, run_id: str):
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
    factory = _InspectingFactory()
    with ExitStack() as stack:
        stack.enter_context(patch.dict(os.environ, environment))
        stack.enter_context(
            patch.object(
                SchemeWriterAgent,
                "run",
                side_effect=AssertionError("legacy SchemeWriterAgent.run invoked"),
            )
        )
        stack.enter_context(
            patch.object(
                SchemeGenerationUseCase,
                "run",
                side_effect=AssertionError("legacy SchemeGenerationUseCase.run invoked"),
            )
        )
        stack.enter_context(
            patch.object(
                SectionExecutionCoordinator,
                "execute",
                side_effect=AssertionError(
                    "legacy SectionExecutionCoordinator.execute invoked"
                ),
            )
        )
        summary = MainlineApplicationService(
            supervisor_factory=factory
        ).run(
            project_root=Path(__file__).resolve().parents[2],
            user_input="生成企业级 RAG-Agent 系统建设方案",
            run_id=run_id,
            output_root=output_root,
            clean_existing=True,
            settings=settings,
            allow_demo_defaults=True,
            enable_agent_self_rag=False,
        )
    return summary, factory


def _trace_counts(path: str | Path) -> dict[str, int]:
    counts: dict[str, int] = {}
    for line in Path(path).read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        event = json.loads(line)
        event_type = str(event.get("event_type") or "")
        counts[event_type] = counts.get(event_type, 0) + 1
    return counts


def test_composition_root_installs_langchain_facades() -> None:
    source = inspect.getsource(SupervisorFactory.build)
    assert "build_model_gateway_runnable" in source
    assert "LangChainModelGatewayFacade" in source
    assert "build_rag_runnable" in source
    assert "LangChainRAGServiceFacade" in source
    assert "create_sqlite_checkpoint_resource" in source


def test_production_mainline_uses_runnables_and_retires_legacy_paths(tmp_path) -> None:
    summary, factory = _run_final_fake(
        tmp_path,
        run_id="phase5_production_runnable_takeover",
    )
    assert summary["status"] in {"success", "partial_success"}
    assert summary["deliverable_final_result"] is not None
    model_facade = factory.model_facades[0]
    rag_facade = factory.rag_facades[0]
    assert model_facade.runnable_invocation_count > 0
    assert rag_facade.runnable_invocation_count > 0

    execution = summary["supervisor_result"]["result"]["workflow_execution"]
    metadata = execution["metadata"]
    assert metadata["phase3_section_subgraph"] is True
    assert metadata["phase4_durable_resume"] is True
    assert metadata["phase5_langchain_runnable_takeover"] is True
    assert metadata["legacy_workflow_path_retired"] is True
    assert metadata["section_subgraph_enabled"] is True

    counts = _trace_counts(summary["paths"]["trace"])
    assert model_facade.runnable_invocation_count == counts["model_started"]
    assert rag_facade.runnable_invocation_count == counts["rag_started"]


def test_runnable_facades_preserve_canonical_ports() -> None:
    assert hasattr(LangChainModelGatewayFacade, "generate")
    assert hasattr(LangChainRAGServiceFacade, "retrieve")
    model_source = inspect.getsource(LangChainModelGatewayFacade.invoke)
    rag_source = inspect.getsource(LangChainRAGServiceFacade.invoke)
    assert ".runnable.invoke(" in model_source
    assert ".runnable.invoke(" in rag_source
    assert "ModelRequestSchema" in inspect.getsource(
        LangChainModelGatewayFacade
    )
    assert "RAGToolInputSchema" in inspect.getsource(LangChainRAGServiceFacade)
    for facade in (
        LangChainModelGatewayFacade,
        LangChainRAGServiceFacade,
    ):
        assert facade.capabilities.invoke is True
        assert facade.capabilities.ainvoke is True
        assert facade.capabilities.batch is True
        assert facade.capabilities.stream_final_response is True
        assert facade.capabilities.token_streaming is False
        assert facade.capabilities.native_async_provider is False
        assert facade.supports_token_streaming is False
        assert facade.supports_native_async is False


def test_production_graph_no_longer_depends_on_legacy_topology_interpreter() -> None:
    source = inspect.getsource(EnterpriseDocumentWorkflowEngine.build_graph)
    assert "workflow.steps" not in source
    assert ".order" not in source
    assert "on_success" not in source
    assert "on_failure" not in source
    assert "StateGraph" in source
    assert "human_review_interrupt" in source
    assert "SectionExecutionCoordinator.execute" not in inspect.getsource(
        EnterpriseDocumentWorkflowEngine
    )
