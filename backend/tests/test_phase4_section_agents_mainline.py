"""Round-4 acceptance for Research/Writer/Verifier in the production Section graph."""

from __future__ import annotations

from contextlib import ExitStack, closing
from dataclasses import replace
import json
import os
from pathlib import Path
import sqlite3
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


def test_mainline_invokes_three_projected_section_agents(tmp_path) -> None:
    # Prove production uses run_projected rather than the Standalone patch path.
    summary, factory = _run_fake(
        tmp_path,
        run_id="round4_three_projected_section_agents",
        patchers=(
            patch.object(
                ResearchAgent,
                "run",
                side_effect=AssertionError("Standalone ResearchAgent.run invoked"),
            ),
            patch.object(
                WriterAgent,
                "run",
                side_effect=AssertionError("Standalone WriterAgent.run invoked"),
            ),
            patch.object(
                VerifierAgent,
                "run",
                side_effect=AssertionError("Standalone VerifierAgent.run invoked"),
            ),
        ),
    )

    assert summary["status"] == "success"
    assert summary["deliverable_final_result"] is not None
    engine = factory.engines[0]
    assert engine.section_execution_graph is not None
    assert engine.section_execution_graph.section_agent_adapter is not None

    events = _trace_events(summary)
    for agent_name, expected_step in (
        ("ResearchAgent", "evaluate_evidence_gate"),
        ("WriterAgent", "precheck_paragraph"),
        ("VerifierAgent", "evaluate_generation"),
    ):
        started = [
            event
            for event in events
            if event.get("event_type") == "agent_started"
            and event.get("component_name") == agent_name
        ]
        finished = [
            event
            for event in events
            if event.get("event_type") == "agent_finished"
            and event.get("component_name") == agent_name
        ]
        assert len(started) == 8
        assert len(finished) == 8
        assert all(event.get("step_name") == expected_step for event in started)
        assert all(event.get("parent_span_id") for event in started)
        assert all(
            "section_graph" in (event.get("tags") or [])
            and "round5" in (event.get("tags") or [])
            and "production_adapter_reasoner" in (event.get("tags") or [])
            for event in [*started, *finished]
        )
        assert all(
            event["output_summary"]["proposed_state_patch_count"] == 0
            for event in finished
        )
        assert all(
            event["output_summary"]["tool_call_count"] == 0
            for event in finished
        )

    sections = summary["generated_draft"]["sections"]
    assert len(sections) == 8
    for section in sections:
        assert section["extra"]["section_agent_payload_counts"] == {
            "research": 1,
            "writer": 1,
            "verifier": 1,
        }
        assert section["extra"]["section_agent_actions"] == {
            "research": "write",
            "verifier": "accept",
        }


def test_section_checkpoint_persists_typed_agent_payloads(tmp_path) -> None:
    summary, factory = _run_fake(
        tmp_path,
        run_id="round4_section_agent_checkpoint_payloads",
    )
    assert summary["status"] == "success"
    engine = factory.engines[0]
    database = Path(engine.checkpointer.path)
    payloads = _latest_section_payloads(database)
    assert len(payloads) == 8

    for namespace, payload in payloads.items():
        assert namespace.startswith("scheme_generation_v2:v2.0/sections/")
        assert "multi_agent_shared_state" not in payload
        assert payload["completed"] is True
        assert payload["route"] == "end"
        assert len(payload["research_agent_payloads"]) == 1
        assert len(payload["writer_agent_payloads"]) == 1
        assert len(payload["verifier_agent_payloads"]) == 1

        research = payload["research_agent_payloads"][0]
        writer = payload["writer_agent_payloads"][0]
        verifier = payload["verifier_agent_payloads"][0]
        assert research["evidence_summary"]["recommended_action"] == "write"
        assert research["agent_metadata"]["fake_reasoner"] is False
        assert research["agent_metadata"]["production_adapter"] is True
        assert research["agent_metadata"]["proposed_state_patch_count"] == 0
        assert writer["generation_complete"] is True
        assert writer["writer_metadata"]["paragraph_pipeline_attached"] is True
        assert writer["writer_metadata"]["production_adapter"] is True
        assert writer["writer_metadata"]["model_call_id"]
        assert writer["writer_metadata"]["proposed_state_patch_count"] == 0
        assert writer["writer_metadata"]["artifact_authority"] == (
            "section_langgraph_checkpoint"
        )
        assert writer["draft_ref"].startswith("checkpoint://")
        assert writer["paragraphs"]
        assert writer["expected_paragraph_ids"] == [
            item["paragraph_id"] for item in writer["paragraphs"]
        ]
        for paragraph in writer["paragraphs"]:
            assert paragraph["content_ref"].startswith("checkpoint://")
            assert paragraph["content_hash"].startswith("sha256:")
            assert paragraph["content"].strip()
        assert verifier["action"] == "accept"
        assert verifier["decision_metadata"]["fake_reasoner"] is False
        assert verifier["decision_metadata"]["production_adapter"] is True
        assert verifier["decision_metadata"]["model_call_id"]
        assert verifier["decision_metadata"]["proposed_state_patch_count"] == 0
        assert verifier["verification_ref"].startswith("state://")

        section_state = payload["section_state"]
        assert section_state["lifecycle_status"] == "accepted"
        assert section_state["research"]["status"] == "completed"
        assert section_state["writing"]["status"] == "completed"
        assert section_state["writing"]["paragraph_refs"] == {
            item["paragraph_id"]: item["content_ref"]
            for item in writer["paragraphs"]
        }
        assert section_state["writing"]["expected_paragraph_ids"] == (
            writer["expected_paragraph_ids"]
        )
        assert section_state["verification"]["status"] == "completed"
        assert section_state["attempt"]["input_snapshot_version"].startswith(
            "sha256:"
        )


def test_verifier_research_more_drives_real_section_route(tmp_path) -> None:
    original = MainlineVerifierModelReasoner.invoke
    forced: set[str] = set()

    def force_one_research_more(self, *, context, invocation_input, tools):
        section_id = str(context.section_id or "")
        if section_id.endswith("_001") and section_id not in forced:
            forced.add(section_id)
            invocation = VerifierExecutionMode.SECTION_VERIFY
            return VerifierResultPayloadSchema(
                execution_mode=invocation,
                action=VerifierAction.RESEARCH_MORE,
                verification_ref=f"state://forced-verifier/{section_id}",
                checks={"forced_research_more": True},
                decision_metadata={
                    "reasoner": "round5_test_verifier",
                    "retrieval_route": "retrieve_more",
                    "fake_payload_only": False,
                    "production_adapter": True,
                },
            )
        return original(
            self,
            context=context,
            invocation_input=invocation_input,
            tools=tools,
        )

    summary, factory = _run_fake(
        tmp_path,
        run_id="round4_verifier_research_more_route",
        patchers=(
            patch.object(
                MainlineVerifierModelReasoner,
                "invoke",
                force_one_research_more,
            ),
        ),
    )
    assert summary["status"] == "success"
    first = summary["generated_draft"]["sections"][0]
    nodes = first["extra"]["section_subgraph_nodes"]
    assert "retrieve_more" in nodes
    assert nodes.count("generate_section") == 2
    assert nodes.count("evaluate_generation") == 2
    assert first["extra"]["section_agent_payload_counts"] == {
        "research": 2,
        "writer": 2,
        "verifier": 2,
    }

    engine = factory.engines[0]
    payloads = _latest_section_payloads(Path(engine.checkpointer.path))
    first_payload = next(
        value
        for value in payloads.values()
        if str(value.get("section_id", "")).endswith("_001")
    )
    assert [
        item["action"] for item in first_payload["verifier_agent_payloads"]
    ] == ["research_more", "accept"]
    assert len(first_payload["research_agent_payloads"]) == 2
    assert first_payload["research_agent_payloads"][-1]["research_mode"] == (
        "self_rag_recovery"
    )


def test_final_writer_gate_blocked_stops_before_writer_and_verifier(
    tmp_path,
) -> None:
    original = FakeRAGTool.run

    def blocked_gate(self, tool_call):
        result = original(self, tool_call)
        evidence = result.result["evidence"]
        extra = evidence["extra"]
        gate = extra["gate_decision"]
        gate.update(
            {
                "evidence_status": "absent",
                "delivery_mode": "blocked",
                "allowed_claim_types": [],
                "forbidden_claim_types": ["current_project_fact"],
                "covered_topics": [],
                "missing_topics": ["forced_stage_e_gap"],
                "human_review_required": False,
                "reason_codes": ["forced_stage_e_blocked_gate"],
            }
        )
        extra["generation_allowed"] = False
        return result

    summary, factory = _run_fake(
        tmp_path,
        run_id="round4_final_writer_gate_blocked",
        patchers=(patch.object(FakeRAGTool, "run", blocked_gate),),
    )
    assert summary["status"] == "failed"
    assert summary["deliverable_final_result"] is None
    assert summary["rejected_artifact"] is not None

    engine = factory.engines[0]
    payloads = _latest_section_payloads(Path(engine.checkpointer.path))
    assert payloads
    for payload in payloads.values():
        assert payload["active_writer_gate"]["gate_decision"][
            "delivery_mode"
        ] == "blocked"
        assert payload["active_writer_gate"]["generation_allowed"] is False
        assert payload["evidence_blocked"] is True
        assert payload["writer_agent_payloads"] == []
        assert payload["verifier_agent_payloads"] == []
        assert "generate_section" not in payload["executed_nodes"]
        assert payload["section"]["extra"]["writer_execution_status"] == (
            "blocked"
        )
        assert payload["section"]["extra"]["formal_deliverable_eligible"] is False


def test_legacy_insufficient_assessment_cannot_override_final_writer_gate(
    tmp_path,
) -> None:
    summary, factory = _run_fake(
        tmp_path,
        run_id="round4_research_gate_blocks_generation",
        patchers=(
            patch.object(
                SchemeGenerationStageService,
                "_evidence_assessment",
                return_value={
                    "status": "insufficient",
                    "evidence_available": False,
                    "details": {"evidence_gaps": ["forced_gap"]},
                },
            ),
        ),
    )
    assert summary["status"] == "failed"
    assert summary["deliverable_final_result"] is None
    assert summary["rejected_artifact"] is not None

    engine = factory.engines[0]
    payloads = _latest_section_payloads(Path(engine.checkpointer.path))
    governed = [
        payload
        for payload in payloads.values()
        if payload.get("citation_required") is True
    ]
    assert governed
    for payload in governed:
        assert payload["active_assessment"]["status"] == "insufficient"
        assert payload["active_writer_gate"]["gate_decision"][
            "delivery_mode"
        ] == "grounded"
        assert payload["active_writer_gate"]["generation_allowed"] is True
        assert payload["evidence_blocked"] is False
        assert payload["writer_agent_payloads"]
        assert payload["verifier_agent_payloads"]
        assert "generate_section" in payload["executed_nodes"]
