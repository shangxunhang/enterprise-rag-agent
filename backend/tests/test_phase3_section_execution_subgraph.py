"""Acceptance tests for the production section execution subgraph."""

from __future__ import annotations

from contextlib import ExitStack
from dataclasses import replace
import inspect
import os
from pathlib import Path
import sqlite3
from unittest.mock import patch

from application.mainline_service import MainlineApplicationService
from apps.enterprise_document.agents.scheme_writer_agent import SchemeWriterAgent
from apps.enterprise_document.quality.budget import WorkflowBudget
from apps.enterprise_document.services.scheme_writer.document_citation_registry import (
    DocumentCitationRegistry,
)
from apps.enterprise_document.services.scheme_writer.section_execution_coordinator import (
    SectionExecutionCoordinator,
)
from apps.enterprise_document.services.scheme_writer.use_case import (
    SchemeGenerationUseCase,
)
from apps.enterprise_document.workflows.section_execution_graph import (
    SECTION_GRAPH_NODE_NAMES,
    SectionExecutionGraph,
)
from apps.enterprise_document.workflows.top_level_business_graph import (
    EnterpriseDocumentWorkflowEngine,
)
from bootstrap.supervisor_factory import SupervisorFactory
from core.config import get_settings
from schemas.citation import CitationSchema


class _InspectingFactory(SupervisorFactory):
    def __init__(self) -> None:
        super().__init__()
        self.engines: list[EnterpriseDocumentWorkflowEngine] = []
        self.model_facades: list[object] = []
        self.rag_facades: list[object] = []

    def build(self, **kwargs):
        supervisor = super().build(**kwargs)
        engine = supervisor.workflow_engine
        assert isinstance(engine, EnterpriseDocumentWorkflowEngine)
        self.engines.append(engine)
        self.model_facades.append(supervisor.model_gateway)
        self.rag_facades.append(engine.stage_service.evidence_service.rag_service)
        return supervisor


def _run_fake(output_root: Path, *, run_id: str):
    from bca014_i10_capacity_fixtures import explicit_fake_startup_policy
    profile,default_path,original=explicit_fake_startup_policy(output_root,run_id)
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
        "GROUNDED_GENERATION_POLICY_FILE": str(profile),
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
    assert default_path.read_bytes()==original
    return summary, factory


def test_section_subgraph_declares_real_business_nodes_and_routes() -> None:
    source = inspect.getsource(SectionExecutionGraph.build_graph)
    assert set(SECTION_GRAPH_NODE_NAMES) == {
        "prepare_section",
        "retrieve_section_evidence",
        "evaluate_evidence_gate",
        "evaluate_pre_write_admission",
        "bind_pre_write_gate",
        "evaluate_pre_write_enforcement",
        "commit_pre_write_route",
        "dispatch_pre_write_recovery",
        "execute_pre_write_recovery",
        "complete_pre_write_recovery_dispatch",
        "invoke_pre_write_writer",
        "generate_section",
        "prepare_paragraph_pipeline",
        "persist_paragraph",
        "precheck_paragraph",
        "evaluate_generation",
        "retrieve_more",
        "corrective_retrieval",
        "finalize_section",
    }
    for node_name in SECTION_GRAPH_NODE_NAMES:
        assert f'"{node_name}"' in source
    assert "add_conditional_edges" in source
    assert "while " not in source


def test_production_path_uses_section_subgraph_not_legacy_coordinator(tmp_path) -> None:
    summary, factory = _run_fake(
        tmp_path,
        run_id="phase3_production_section_subgraph",
    )
    from bca014_i10_capacity_fixtures import assert_native_publication
    assert_native_publication(summary,factory.engines[0])
    assert summary["status"] in {"success", "partial_success"}
    assert summary["deliverable_final_result"] is not None
    engine = factory.engines[0]
    assert engine.section_execution_graph is not None
    assert engine.stage_service.section_execution_graph is engine.section_execution_graph

    sections = summary["generated_draft"]["sections"]
    assert len(sections) == 8
    for section in sections:
        nodes = section["extra"]["section_subgraph_nodes"]
        assert nodes[0] == "prepare_section"
        assert nodes[-1] == "finalize_section"
        assert "invoke_pre_write_writer" in nodes
        assert "generate_section" not in nodes


def test_section_checkpoints_use_independent_namespaces(tmp_path) -> None:
    run_id = "phase3_section_checkpoint_namespaces"
    _, factory = _run_fake(tmp_path, run_id=run_id)
    engine = factory.engines[0]
    database = Path(engine.checkpointer.path)
    assert database.is_file()
    with sqlite3.connect(database) as connection:
        namespaces = {
            row[0]
            for row in connection.execute(
                "SELECT DISTINCT checkpoint_ns FROM checkpoints"
            )
        }
    section_namespaces = sorted(
        value for value in namespaces if "/sections/section_" in value
    )
    assert len(section_namespaces) == 8
    assert all(value.startswith("scheme_generation_v2:v2.0/sections/") for value in section_namespaces)
    assert len(set(section_namespaces)) == 8


def test_budget_snapshot_restores_consumed_counters() -> None:
    budget = WorkflowBudget.from_policy_metadata(
        {
            "max_retrieval_rounds": 3,
            "max_rewrite_rounds": 2,
            "max_total_llm_calls": 7,
            "max_total_tokens": 4096,
        }
    )
    budget.consume_retrieval_round()
    budget.consume_rewrite_round()
    budget.reserve_llm_call(max_tokens=512)
    restored = WorkflowBudget.from_snapshot(budget.snapshot())
    assert restored.snapshot() == budget.snapshot()


def test_citation_registry_restore_keeps_stable_ids() -> None:
    original = CitationSchema(
        citation_id="C7",
        source_type="document",
        quote_text="stable evidence",
        title="Evidence",
    )
    registry = DocumentCitationRegistry.from_citations([original])
    remapped, mapping = registry.register(
        [
            original.model_copy(update={"citation_id": "temporary"})
        ],
        scope="section",
        query="query",
    )
    assert remapped[0].citation_id == "C7"
    assert mapping["temporary"] == "C7"
    assert [item.citation_id for item in registry.all()] == ["C7"]
