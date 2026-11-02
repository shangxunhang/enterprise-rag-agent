"""Round-4 acceptance for Research/Writer/Verifier in the production Section graph."""

from __future__ import annotations

from contextlib import ExitStack, closing
from dataclasses import replace
import json
import os
from pathlib import Path
import sqlite3
import pytest
from unittest.mock import patch

from agent.runtime.official_checkpoint_saver import CompressedJsonPlusSerializer
from application.mainline_service import MainlineApplicationService
from apps.enterprise_document.agents.agent_rag_v1 import (
    ResearchAgent,
    VerifierAgent,
    WriterAgent,
)
from apps.enterprise_document.agents.scheme_writer_agent import SchemeWriterAgent
from apps.enterprise_document.schemas.agent_rag_v1 import (
    VerifierAction,
    VerifierExecutionMode,
    VerifierResultPayloadSchema,
)
from apps.enterprise_document.services.scheme_writer.agent_integration.section_agent_reasoners import (
    MainlineVerifierModelReasoner,
)
from apps.enterprise_document.services.scheme_writer.section_execution_coordinator import (
    SectionExecutionCoordinator,
)
from apps.enterprise_document.services.scheme_writer.stage_service import (
    SchemeGenerationStageService,
)
from apps.enterprise_document.services.scheme_writer.use_case import (
    SchemeGenerationUseCase,
)
from apps.enterprise_document.workflows.top_level_business_graph import (
    EnterpriseDocumentWorkflowEngine,
)
from bootstrap.supervisor_factory import SupervisorFactory
from core.config import get_settings
from tools.fake_rag_tool import FakeRAGTool


class _InspectingFactory(SupervisorFactory):
    def __init__(self) -> None:
        super().__init__()
        self.engines: list[EnterpriseDocumentWorkflowEngine] = []

    def build(self, **kwargs):
        supervisor = super().build(**kwargs)
        engine = supervisor.workflow_engine
        assert isinstance(engine, EnterpriseDocumentWorkflowEngine)
        self.engines.append(engine)
        return supervisor


def _run_fake(
    output_root: Path,
    *,
    run_id: str,
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
        for patcher in patchers:
            stack.enter_context(patcher)
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


def _trace_events(summary: dict) -> list[dict]:
    path = Path(summary["paths"]["trace"])
    return [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def _latest_section_payloads(database: Path) -> dict[str, dict]:
    serializer = CompressedJsonPlusSerializer()
    latest: dict[str, tuple[str, bytes]] = {}
    with closing(sqlite3.connect(database)) as connection:
        rows = connection.execute(
            "SELECT checkpoint_ns, type, checkpoint "
            "FROM checkpoints "
            "WHERE checkpoint_ns LIKE '%/sections/%' "
            "ORDER BY checkpoint_ns, checkpoint_id"
        ).fetchall()
    for namespace, payload_type, checkpoint in rows:
        latest[str(namespace)] = (str(payload_type), checkpoint)
    result: dict[str, dict] = {}
    for namespace, typed_checkpoint in latest.items():
        checkpoint = serializer.loads_typed(typed_checkpoint)
        result[namespace] = dict(
            (checkpoint.get("channel_values") or {}).get("section_payload")
            or {}
        )
    return result


def test_mainline_invokes_three_projected_section_agents(canonical_section_mainline) -> None:
    # Historical nodeID retained; canonical refs replaced retired agent payload truth.
    data = canonical_section_mainline
    graph = data["engine"].section_execution_graph
    payloads = _canonical_completed_payloads(data)
    for payload in payloads:
        assert payload.current_research_round_binding is not None
        graph.research_runtime.validate_round_binding_current(payload.current_research_round_binding)
        assert payload.writer_execution_ref is not None
        assert payload.writer_generation_invocation_ref is not None
        assert payload.canonical_claim_verification_refs
        source = payload.result.canonical_section_source_binding
        assert data["runtime"].validate_source(source, require_accepted=True) == source
        assert "invoke_pre_write_writer" in payload.executed_nodes
        assert "generate_section" not in payload.executed_nodes
    purposes = [call.extra.get("call_purpose") for call in data["calls"]]
    assert purposes.count("scheme_section_generation") == 3
    assert purposes.count("claim_verification") == 3


def test_section_checkpoint_persists_typed_agent_payloads(canonical_section_mainline) -> None:
    data = canonical_section_mainline
    for payload in _canonical_completed_payloads(data):
        assert payload.completed and payload.route == "end"
        assert payload.result is not None
        source = payload.result.canonical_section_source_binding
        assert source.plan_binding == payload.canonical_plan_binding
        assert source.producer_attempt_id == payload.active_execution_lineage.attempt_id
        assert source.writer_execution_ref == payload.writer_execution_ref
        assert source.writer_invocation_ref == payload.writer_generation_invocation_ref
        assert source.paragraph_manifest == payload.section_paragraph_manifest
        assert source.paragraph_manifest.generation_membership_state == "SEALED"
        assert source.verification_refs == tuple(payload.canonical_claim_verification_refs)
        assert source.coverage_proof_refs == tuple(payload.canonical_claim_coverage_proof_refs)
        for ref in source.verification_refs:
            claim, result, view = data["runtime"].read_active_verified_source(ref.claim_ref, source)
            assert claim.claim_ref == ref.claim_ref
            assert result == data["runtime"].claim_runtime.resolve_verification_result(ref)
            assert view.active_verified_claim


def test_verifier_research_more_drives_real_section_route(tmp_path) -> None:
    # The frozen cutover routes recovery through Gate authority, not legacy Verifier actions.
    import test_multi_agent_v3_prewrite_gate_integration as canonical
    canonical.test_gate_recovery_dispatch_binds_exact_route_and_replays_same_research_round(tmp_path)


def test_final_writer_gate_blocked_stops_before_writer_and_verifier(tmp_path) -> None:
    import test_gate_v2_pre_write_routing_fallback as canonical
    canonical.test_recovery_route_cannot_form_writer_invocation(tmp_path)


def test_removed_legacy_assessment_cannot_override_final_writer_gate(tmp_path) -> None:
    import test_multi_agent_v3_prewrite_gate_integration as canonical
    assert not hasattr(SchemeGenerationStageService, "_evidence_assessment")
    canonical.test_current_research_package_drives_gate_with_exact_plan_requirements(tmp_path / "exact")
    canonical.test_stale_or_bundle_mismatched_package_fails_before_gate_assessment(tmp_path / "stale")


@pytest.fixture(scope="module")
def canonical_section_mainline(tmp_path_factory):
    """A-350-17: real canonical owners, with retired agent entrypoints fenced."""
    from test_multi_agent_v3_cross_section_context_mainline import _run_dependency_mainline
    with ExitStack() as stack:
        for agent in (ResearchAgent, WriterAgent, VerifierAgent):
            stack.enter_context(patch.object(agent, "run", side_effect=AssertionError("retired standalone agent called")))
        yield _run_dependency_mainline(tmp_path_factory, "VERIFIED")


def _canonical_completed_payloads(data):
    from apps.enterprise_document.schemas.section_execution_graph import SectionExecutionGraphPayloadSchema
    rows = _latest_section_payloads(Path(data["engine"].checkpointer.path))
    assert len(rows) == 3
    return [SectionExecutionGraphPayloadSchema.model_validate(row) for row in rows.values()]
