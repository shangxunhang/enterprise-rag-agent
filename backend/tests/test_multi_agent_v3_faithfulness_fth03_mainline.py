from __future__ import annotations

from contextlib import ExitStack
from dataclasses import replace
import json
import os
from pathlib import Path
from unittest.mock import patch

import pytest

from application.mainline_service import MainlineApplicationService
from apps.enterprise_document.schemas.agent_rag_v1.claim_support import SupportRelation
from apps.enterprise_document.services.agent_rag_v1.claim_support_composition import (
    SUPPORT_RESPONSE_CONTRACT,
)
from apps.enterprise_document.services.agent_rag_v1.post_write_composition import (
    TargetPostWriteComposition,
)
from core.config import get_settings
from model_gateway.fake_llm_client import FakeLLMClient
from rag.services.rag_service import FakeRAGService
from schemas.rag import EvidenceBundleSchema

import test_multi_agent_v3_claim_materialization_mainline as extraction_tests
import test_multi_agent_v3_claim_support_mainline as support_tests
import test_multi_agent_v3_citation_integrity_mainline as citation_tests
import test_multi_agent_v3_workflow_recovery as workflow_tests


TEXT = support_tests.TEXT


def _run(
    root: Path,
    *,
    certainty: str,
    support_relation: str = "SUPPORTED",
):
    task_id = f"task-fth349-{certainty.lower()}-{support_relation.lower()}"
    run_id = f"run-fth349-{certainty.lower()}-{support_relation.lower()}"
    project = workflow_tests._w13_current_new_task_project_input(workflow_tests._w13_single_section_project_input(task_id))
    calls = []
    captures = []
    original_generate = FakeLLMClient.generate
    original_retrieve = FakeRAGService.retrieve
    original_complete = TargetPostWriteComposition._complete_validation

    def evidence_fixture(service, request):
        raw = original_retrieve(service, request).model_dump(mode="json")
        for item in raw["item_assessments"]:
            item["supported_claim_types"] = ["general_method"]
        raw["assessment"]["supported_claim_types"] = ["general_method"]
        return EvidenceBundleSchema.model_validate(raw)

    def provider(client, request):
        calls.append(request.model_copy(deep=True))
        response = original_generate(client, request)
        if request.extra.get("call_purpose") == "scheme_section_generation":
            return response.model_copy(update={"content": TEXT * 6}, deep=True)
        if request.extra.get("response_contract") == extraction_tests.CONTRACT:
            units = extraction_tests._task_contract(request)["coverage_units"]
            payload = {
                "units": [
                    {
                        "unit_id": unit["unit_id"],
                        "accounting_state": "FACTUAL_CLAIMS_MATERIALIZED",
                        "claims": [
                            {
                                "quote": TEXT,
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
        if request.extra.get("response_contract") == SUPPORT_RESPONSE_CONTRACT:
            evidence = support_tests._json_message(
                request, "support_evidence"
            )["support_evidence"]
            refs = [item["exact_source_ref"] for item in evidence]
            relation = SupportRelation(support_relation)
            payload = {
                "relation": relation.value,
                "supporting_refs": (
                    refs[:1]
                    if relation in {
                        SupportRelation.SUPPORTED,
                        SupportRelation.PARTIALLY_SUPPORTED,
                    }
                    else []
                ),
                "contradicting_refs": (
                    refs[:1]
                    if relation is SupportRelation.CONTRADICTED
                    else []
                ),
                "certainty_relation": certainty,
                "certainty_reason_codes": [f"TEST_FTH03_{certainty}"],
            }
            return response.model_copy(
                update={"content": json.dumps(payload, ensure_ascii=False)},
                deep=True,
            )
        if request.extra.get("response_contract") == "canonical_claim_verifier_v2" and support_relation == "SUPPORTED":
            # This test supplies an independent validity oracle; certainty is
            # still evaluated separately by the actual FTH-03 implementation.
            shared = support_tests._json_message(request, "shared_support")["shared_support"]
            allowed = support_tests._json_message(request, "allowed_authority_refs")["allowed_authority_refs"]
            basis = shared["supporting_refs"][:1]
            assert shared["relation"] == "SUPPORTED" and basis
            assert all(ref in allowed for ref in basis)
            return response.model_copy(update={"content": json.dumps({
                "verdict": "VERIFIED", "positive_basis_refs": basis,
                "rationale": "Controlled independent validity oracle for FTH-03 separation.",
                "issues": []}, ensure_ascii=False)}, deep=True)
        return response

    def complete(composition, **kwargs):
        outcome = original_complete(composition, **kwargs)
        completion = composition.validation_store.resolve_completion(outcome[0])
        fth03 = next(
            item
            for item in completion.items
            if item.expected_validation_item_ref.startswith("faithfulness:item:")
        )
        shared = next(
            item
            for item in completion.items
            if item.expected_validation_item_ref.startswith("faithfulness:shared:")
        )
        fth03_facts = tuple(
            composition.validation_store.resolve_fact(ref) for ref in fth03.fact_refs
        )
        shared_facts = tuple(
            composition.validation_store.resolve_fact(ref) for ref in shared.fact_refs
        )
        captures.append(
            (dict(kwargs), fth03, fth03_facts, shared, shared_facts, outcome)
        )
        return outcome

    settings = replace(
        get_settings(),
        data_root=root,
        default_model_name="fake_llm",
        supervisor_model_name="fake_llm",
        enable_llm_routing=False,
        trace_enabled=True,
        data_capture_enabled=True,
        gate_enforcement_mode="strict",
        gate_recovery_rounds_limit=0,
    )
    factory = workflow_tests._W13InspectingFactory()
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
        stack.enter_context(
            patch.object(TargetPostWriteComposition, "_complete_validation", complete)
        )
        summary = MainlineApplicationService(supervisor_factory=factory).run(
            project_root=Path(__file__).resolve().parents[2],
            task_id=task_id,
            run_id=run_id,
            user_input=project.user_query,
            output_root=root,
            clean_existing=True,
            settings=settings,
            project_input=project.model_dump(mode="json"),
            allow_demo_defaults=False,
            enable_agent_self_rag=False,
        )
    engine = factory.engines[0]
    top = workflow_tests._latest_w13_top_level_payload(engine)
    return engine, calls, captures, summary, top


@pytest.mark.parametrize(
    ("certainty", "expected"),
    [("ALIGNED", "PASS"), ("INDETERMINATE", "INDETERMINATE")],
)
def test_fth03_actual_factory_section_and_document_are_independent_without_new_model_call(
    tmp_path, certainty, expected
):
    engine, calls, captures, _summary, top = _run(
        tmp_path,
        certainty=certainty,
    )
    assert top.terminal_error is None
    assert len(captures) == 2
    assert {item[0]["scope"].value for item in captures} == {"SECTION", "DOCUMENT"}

    support_calls = [
        request
        for request in calls
        if request.extra.get("call_purpose") == "claim_support"
    ]
    assert len(support_calls) == 1
    assert not [
        request
        for request in calls
        if "faithfulness" in str(request.extra.get("call_purpose") or "").lower()
    ]

    fact_ids = []
    receipt_refs = []
    for _kwargs, fth03, facts, _shared, _shared_facts, _outcome in captures:
        assert fth03.state.value == expected
        assert facts
        assert {fact.semantic_state.value for fact in facts} == {expected}
        assert {
            tuple(fact.payload["rule_ids"]) for fact in facts
        } == {("PW-R-FTH-03",)}
        for fact in facts:
            observation = fact.payload["certainty_observation"]
            assert observation["certainty_relation"] == certainty
            assert observation["applicable_requirement_ids"] == []
            receipt_refs.append(observation["physical_support_receipt_ref"])
            fact_ids.append(fact.fact_id)
    assert len(set(receipt_refs)) == 1
    assert len(set(fact_ids)) == 2

    composition = engine.stage_service.target_post_write_composition
    final_control = composition.control_store.resolve_exact(
        top.target_post_write_control_decision_ref
    )
    final_gate = composition.gate_store.resolve_exact(
        final_control.delivery_gate_decision_ref
    )
    condition = next(
        item
        for item in final_gate.quality_policy_projection.condition_results
        if item.dimension_id == "faithfulness"
    )
    assert condition.state.value == (
        "SATISFIED" if certainty == "ALIGNED" else "UNRESOLVED"
    )


def test_fth03_escalated_is_a_real_faithfulness_failure_even_with_supported_claim(
    tmp_path,
):
    _engine, _calls, captures, _summary, _top = _run(
        tmp_path,
        certainty="ESCALATED",
        support_relation="SUPPORTED",
    )
    assert captures
    first = captures[0]
    _kwargs, fth03, facts, shared, shared_facts, _outcome = first
    assert fth03.state.value == "FAIL"
    assert facts and {fact.semantic_state.value for fact in facts} == {"FAIL"}
    assert shared.state.value == "PASS"
    assert shared_facts and "PASS" in {fact.semantic_state.value for fact in shared_facts}
    assert all(
        fact.payload["support_relation"] == "SUPPORTED" for fact in facts
    )
    assert all(
        fact.payload["certainty_observation"]["certainty_relation"] == "ESCALATED"
        for fact in facts
    )


def test_fth03_aligned_does_not_rescue_not_supported_shared_faithfulness(
    tmp_path,
):
    _engine, _calls, captures, _summary, _top = _run(
        tmp_path,
        certainty="ALIGNED",
        support_relation="NOT_SUPPORTED",
    )
    assert captures
    _kwargs, fth03, facts, shared, shared_facts, _outcome = captures[0]
    assert fth03.state.value == "PASS"
    assert facts and {fact.semantic_state.value for fact in facts} == {"PASS"}
    assert shared.state.value == "FAIL"
    assert shared_facts and "FAIL" in {fact.semantic_state.value for fact in shared_facts}


def test_fth03_terminal_replay_reuses_exact_support_certainty_receipt(
    tmp_path,
):
    engine, _calls, _captures, _summary, top = _run(
        tmp_path,
        certainty="ALIGNED",
    )
    composition = citation_tests._reopen(engine)
    before = citation_tests._durable_state(engine)
    with patch.object(
        FakeLLMClient,
        "generate",
        side_effect=AssertionError("terminal FTH-03 replay called model"),
    ):
        replay = composition.complete_target_post_write(
            task_id=top.task_id,
            run_id=top.run_id,
            document_id=top.document_id,
            project_input=top.project_input,
            section_execution_results=tuple(top.section_execution_results),
        )
    assert replay.published_release.reuse_state.value == "EXACT_REQUEST_REPLAY"
    assert before == citation_tests._durable_state(engine)
