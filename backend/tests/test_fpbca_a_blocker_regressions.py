"""Regression closure for the four confirmed FPBCA class-A blockers."""

from __future__ import annotations

from contextlib import ExitStack
from dataclasses import replace
import json
import os
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from application.mainline_service import MainlineApplicationService
from apps.enterprise_document.schemas.agent_rag_v1.lifecycle import RunLifecycleStatus
from apps.enterprise_document.schemas.project_input_schema import ProjectInputSchema
from apps.enterprise_document.services.agent_rag_v1.claim_composition import (
    TargetClaimRuntimeValidators,
)
from apps.enterprise_document.services.agent_rag_v1.claim_support_composition import (
    SUPPORT_RESPONSE_CONTRACT,
)
from apps.enterprise_document.services.agent_rag_v1.claim_verifier_composition import (
    VERIFIER_RESPONSE_CONTRACT,
)
from apps.enterprise_document.services.agent_rag_v1.lifecycle_runtime import (
    LifecycleCurrentnessError,
)
from apps.enterprise_document.services.agent_rag_v1.writer_evidence_projection import (
    _structured,
)
from apps.enterprise_document.workflows.section_execution_graph import (
    SectionExecutionGraph,
)
from apps.enterprise_document.workflows.top_level_business_graph import (
    EnterpriseDocumentWorkflowEngine,
)
from core.config import get_settings
from core.hashing import canonical_sha256
from core.runtime.execution_control import current_execution_control
from model_gateway.fake_llm_client import FakeLLMClient
from rag.services.rag_service import FakeRAGService
from schemas.rag import EvidenceBundleSchema

import test_multi_agent_v3_claim_materialization_mainline as extraction_tests
import test_multi_agent_v3_claim_support_mainline as support_tests
import test_multi_agent_v3_workflow_recovery as workflow_tests
from test_phase2_runtime_safety_closure import _run_production_fake


RAW = "RAW_EVIDENCE_MARKER::must-never-reach-semantic-consumers"
VISIBLE = (
    "【历史案例，仅供参考】\n"
    "来源章节：历史方案\n"
    "允许用途：general_method。\n"
    "原始历史正文未向生成模型暴露。"
)
CLAIM = "可采用分层冗余架构组织系统能力。"


def test_fpbca_ew_001_source_span_text_is_not_writer_semantic_payload() -> None:
    structured = {
        "schema_version": "structured_evidence_v1",
        "representation_type": "fact_record",
        "original_strategy": "fact_record",
        "actual_strategy": "fact_record",
        "fact": {
            "record_id": "fact-1",
            "fact_category": "cost",
            "fact_status": "estimated",
            "source_span": {
                "unit_order_start": 7,
                "unit_order_end": 7,
                "text": RAW,
            },
        },
        "fallback": {"used": False},
    }
    projected = _structured(structured, "/candidate/structured_evidence")
    encoded = json.dumps(projected, ensure_ascii=False)

    assert RAW not in encoded
    assert projected["fact"]["source_span"] == {
        "unit_order_start": 7,
        "unit_order_end": 7,
    }
    assert projected["fact"]["source_span_ref"]["sha256"] == canonical_sha256(
        structured["fact"]["source_span"]
    )


class _Assessment:
    metadata = {
        "fact_visible_in_prompt": False,
        "required_qualifiers": ["historical only"],
        "structured_evidence": {"source_span": {"text": RAW}},
    }

    def model_dump(self, *, mode="json", exclude=None):
        del mode
        value = {
            "schema_version": "evidence_assessment_v2",
            "evidence_id": "E1",
            "eligibility": True,
            "supported_claim_types": ["general_method"],
            "forbidden_claim_types": ["current_project_quantity"],
            "metadata": dict(self.metadata),
        }
        for key in exclude or ():
            value.pop(key, None)
        return value


def test_cr_a_001_claim_semantic_material_is_writer_visible_only() -> None:
    item = SimpleNamespace(
        evidence_id="E1",
        doc_id="doc-1",
        title="历史方案",
        match_text=RAW,
        context_text=VISIBLE,
    )
    material, observed = TargetClaimRuntimeValidators._visible_evidence_material(
        item,
        _Assessment(),
    )
    encoded = json.dumps(material, ensure_ascii=False)

    assert RAW not in encoded
    assert "match_text" not in material
    assert material["context_text"] == observed == VISIBLE
    assert material["context_text_sha256"] == canonical_sha256(VISIBLE)
    assert material["assessment"]["usage_constraints"] == {
        "fact_visible_in_prompt": False,
        "required_qualifiers": ["historical only"],
    }
    assert "metadata" not in material["assessment"]


@pytest.mark.parametrize(
    "target_live,expected_gate",
    [(True, "evaluate_document_gate"), (False, "build_generation_report")],
)
def test_recovery_final_result_includes_direct_gate_and_persist_predecessors(
    target_live: bool,
    expected_gate: str,
) -> None:
    engine = object.__new__(EnterpriseDocumentWorkflowEngine)
    engine.stage_service = SimpleNamespace(
        target_post_write_composition=object() if target_live else None
    )
    assert engine._recovery_predecessors("finalize_workflow_result") == (
        expected_gate,
        "persist_deliverable",
        "persist_rejected",
    )


def test_fpa_rec_001_target_live_recovery_uses_actual_route_predecessor() -> None:
    import test_fpbca_a3_v2_1_recovery as native_recovery

    rows = [
        native_recovery.checkpoint_row(
            "actual-predecessor", "final_artifact_review", "continue", None, 1,
        ),
        native_recovery.checkpoint_row(
            "failed-gate", "evaluate_document_gate", "technical_failure",
            "actual-predecessor", 2, failed="evaluate_document_gate",
        ),
    ]
    engine, workflow = native_recovery.engine_from_rows(
        rows, "failed-gate", target=True,
    )

    plan = engine.recovery_plan(workflow, run_id=native_recovery.RUN)

    assert plan["predecessor"] == "final_artifact_review"
    assert plan["checkpoint_config"]["configurable"]["checkpoint_id"] == (
        "actual-predecessor"
    )
    assert plan["checkpoint_config"]["configurable"]["thread_id"] == native_recovery.RUN
    assert plan["checkpoint_config"]["configurable"]["checkpoint_ns"] == ""
    assert plan["checkpoint_config"]["configurable"]["workflow_namespace"] == native_recovery.NS
    native_config = engine._recovery_native_config(
        workflow, plan["checkpoint_config"], run_id=native_recovery.RUN,
    )
    assert native_config["configurable"]["checkpoint_ns"] == native_recovery.NS
    assert rows[0].config["configurable"]["checkpoint_ns"] == native_recovery.NS
    assert engine.checkpointer.history_reads == 0


def test_fpa_rec_002_cancel_terminalizes_run_task_and_commit_authority(
    tmp_path: Path,
) -> None:
    original = SectionExecutionGraph._prepare_section

    def cancel_after_prepare(graph, state):
        value = original(graph, state)
        control = current_execution_control()
        assert control is not None
        control.cancel("fpbca-regression-cancel")
        return value

    summary, factory = _run_production_fake(
        tmp_path / "cancel-runtime",
        run_id="fpbca-rec-002",
        patchers=(
            patch.object(
                SectionExecutionGraph,
                "_prepare_section",
                cancel_after_prepare,
            ),
        ),
    )
    engine = factory.engines[0]
    assert engine.wait_for_idle(timeout_seconds=2.0)
    assert engine.active_worker_count == 0

    run = engine.lifecycle_service.load_run_lifecycle(summary["run_id"])
    assert summary["terminal_outcome"]["status"] == "cancelled"
    assert summary["terminal_outcome"]["execution_status"] == "cancelled"
    assert summary["terminal_outcome"]["run_status"] == "cancelled"
    assert summary["task_state"]["status"] == "cancelled"
    assert summary["status"] == "cancelled"
    assert run.status is RunLifecycleStatus.CANCELLED
    assert run.finished_at is not None
    with pytest.raises(LifecycleCurrentnessError):
        engine.lifecycle_service.assert_run_committable(summary["run_id"])


def test_cr_a_001_physical_support_and_verifier_never_receive_raw_match_text(
    tmp_path: Path,
) -> None:
    task_id = "task-fpbca-visible-authority"
    run_id = "run-fpbca-visible-authority"
    project = workflow_tests._w13_current_new_task_project_input(
        workflow_tests._w13_single_section_project_input(task_id)
    )
    raw_project = project.model_dump(mode="json")
    raw_project["generation_requirements"]["need_citation"] = True
    raw_project["generation_requirements"]["citation_required_sections"] = list(
        raw_project["generation_requirements"]["required_sections"]
    )
    project = ProjectInputSchema.model_validate(raw_project)

    calls = []
    original_generate = FakeLLMClient.generate
    original_retrieve = FakeRAGService.retrieve

    def evidence_fixture(service, req):
        raw = original_retrieve(service, req).model_dump(mode="json")
        item = raw["items"][0]
        item["match_text"] = RAW
        item["context_text"] = VISIBLE
        item["title"] = "历史方案"
        metadata = dict(item.get("metadata") or {})
        metadata.update(
            prompt_visible_text=VISIBLE,
            prompt_projection_mode="historical_reference_restricted",
            raw_fact_visibility="restricted",
        )
        item["metadata"] = metadata
        for assessment in raw["item_assessments"]:
            assessment["evidence_role"] = "historical_case"
            assessment["project_relation"] = "other_project"
            assessment["supported_claim_types"] = ["general_method"]
            assessment["forbidden_claim_types"] = sorted(
                set(assessment.get("forbidden_claim_types") or [])
                | {
                    "current_project_quantity",
                    "current_project_configuration",
                    "current_project_cost",
                    "current_project_schedule",
                    "current_project_fact",
                }
            )
            assessment.setdefault("metadata", {})["structured_evidence"] = {
                "source_span": {"text": RAW}
            }
        raw["assessment"]["supported_claim_types"] = ["general_method"]
        raw["assessment"]["historical_or_reference_evidence_present"] = True
        raw["assessment"]["authoritative_evidence_present"] = False
        raw["context"]["context_text"] = VISIBLE
        raw["context"]["used_context_chars"] = len(VISIBLE)
        return EvidenceBundleSchema.model_validate(raw)

    def provider(client, req):
        calls.append(req.model_copy(deep=True))
        response = original_generate(client, req)
        purpose = req.extra.get("response_contract")
        if req.extra.get("call_purpose") == "scheme_section_generation":
            return response.model_copy(update={"content": CLAIM * 8 + " [C1]"}, deep=True)
        if purpose == extraction_tests.CONTRACT:
            units = extraction_tests._task_contract(req)["coverage_units"]
            payload = {
                "units": [
                    {
                        "unit_id": unit["unit_id"],
                        "accounting_state": "FACTUAL_CLAIMS_MATERIALIZED",
                        "claims": [
                            {
                                "quote": CLAIM,
                                "occurrence_index": 0,
                                "claim_type": "general_method",
                            }
                        ],
                    }
                    for unit in units
                ]
            }
            return response.model_copy(
                update={"content": json.dumps(payload, ensure_ascii=False)},
                deep=True,
            )
        if purpose == SUPPORT_RESPONSE_CONTRACT:
            payload = support_tests._json_message(req, "support_evidence")
            refs = [item["exact_source_ref"] for item in payload["support_evidence"]]
            assert refs
            answer = {
                "relation": "SUPPORTED",
                "supporting_refs": refs[:1],
                "contradicting_refs": [],
                "certainty_relation": "ALIGNED",
                "certainty_reason_codes": ["VISIBLE_AUTHORITY_REGRESSION"],
            }
            return response.model_copy(update={"content": json.dumps(answer)}, deep=True)
        if purpose == VERIFIER_RESPONSE_CONTRACT:
            basis = support_tests._json_message(
                req,
                "admissible_positive_basis_refs",
            )["admissible_positive_basis_refs"]
            evidence_basis = next(
                ref for ref in basis if ref.startswith("evidence-item://")
            )
            answer = {
                "verdict": "VERIFIED",
                "positive_basis_refs": [evidence_basis],
                "rationale": "Controlled visible-evidence regression.",
                "issues": [],
            }
            return response.model_copy(update={"content": json.dumps(answer)}, deep=True)
        return response

    factory = workflow_tests._W13InspectingFactory()
    settings = replace(
        get_settings(),
        data_root=tmp_path,
        default_model_name="fake_llm",
        supervisor_model_name="fake_llm",
        enable_llm_routing=False,
        trace_enabled=True,
        data_capture_enabled=True,
        gate_enforcement_mode="strict",
        gate_recovery_rounds_limit=0,
    )
    with ExitStack() as stack:
        stack.enter_context(
            patch.dict(
                os.environ,
                {
                    "USE_REAL_RAG_TOOL": "false",
                    "FAKE_RAG_SCENARIO": "default",
                    "FAKE_LLM_SCENARIO": "default",
                    "ENABLE_AGENT_SELF_RAG": "false",
                    "ENABLE_SEMANTIC_GATE": "false",
                },
            )
        )
        stack.enter_context(patch.object(FakeRAGService, "retrieve", evidence_fixture))
        stack.enter_context(patch.object(FakeLLMClient, "generate", provider))
        MainlineApplicationService(supervisor_factory=factory).run(
            project_root=Path(__file__).resolve().parents[2],
            user_input=project.user_query,
            task_id=task_id,
            run_id=run_id,
            output_root=tmp_path,
            clean_existing=True,
            settings=settings,
            project_input=project.model_dump(mode="json"),
            allow_demo_defaults=False,
            enable_agent_self_rag=False,
        )

    writer = [
        request
        for request in calls
        if request.extra.get("call_purpose") == "scheme_section_generation"
    ]
    support = [
        request
        for request in calls
        if request.extra.get("call_purpose") == "claim_support"
    ]
    verifier = [
        request
        for request in calls
        if request.extra.get("call_purpose") == "claim_verification"
    ]
    assert len(writer) == len(support) == len(verifier) == 1

    writer_wire = "\n".join(message["content"] for message in writer[0].messages)
    support_wire = "\n".join(message["content"] for message in support[0].messages)
    verifier_wire = "\n".join(message["content"] for message in verifier[0].messages)
    support_payload = support_tests._json_message(
        support[0],
        "support_evidence",
    )["support_evidence"]

    assert RAW not in writer_wire
    assert RAW not in support_wire
    assert RAW not in verifier_wire
    assert support_payload[0]["context_text"] == VISIBLE
    assert support_payload[0]["context_text_sha256"] == canonical_sha256(VISIBLE)
    assert "match_text" not in support_payload[0]
    assert "metadata" not in support_payload[0]["assessment"]
