"""Canonical Claim results reach their quality owner without legacy dispatch."""
from __future__ import annotations

import json
from unittest.mock import Mock

import pytest

from apps.enterprise_document.schemas.agent_rag_v1 import (
    VerifierAction,
    VerifierResultPayloadSchema,
)
from apps.enterprise_document.workflows.section_execution_graph import SectionExecutionGraph
import test_multi_agent_v3_citation_grounding_mainline as grounding
import test_multi_agent_v3_workflow_recovery as workflow
import test_phase6_paragraph_claim_mainline as paragraphs


def _routing_payload(hint):
    payload = paragraphs._section_payload(content="A retained paragraph for routing inspection.")
    section = payload.section.model_copy(deep=True)
    section.eval_result = section.eval_result.model_copy(
        update={"checks": {"citation_bound": hint != "unbound-citation"}}, deep=True,
    )
    checks = {
        "need_retrieve_more": hint == "need-more",
        "paragraph_precheck_blocking": hint == "paragraph-precheck",
    }
    verifier = []
    if hint in {"old-verifier-research", "old-verifier-rewrite"}:
        verifier = [VerifierResultPayloadSchema(
            execution_mode="section_verify",
            action=(VerifierAction.RESEARCH_MORE if hint == "old-verifier-research"
                    else VerifierAction.REWRITE),
            verification_ref="compat-verification://routing-test",
            decision_metadata={"retrieval_route": "corrective_retrieval"},
        )]
    return payload.model_copy(update={
        "section": section, "citation_required": True,
        "corrective_retrieval_enabled": True, "evidence_blocked": False,
        "generation_check": checks, "verifier_agent_payloads": verifier,
        "budget_state": {"max_retrieval_rounds": 1, "max_rewrite_rounds": 1},
    }, deep=True)


_HINTS = (
    ("unbound-citation", "corrective_retrieval"),
    ("need-more", "retrieve_more"),
    ("paragraph-precheck", "generate"),
    ("old-verifier-research", "corrective_retrieval"),
    ("old-verifier-rewrite", "generate"),
    ("no-legacy-trigger", "finalize"),
)


@pytest.mark.parametrize("hint,legacy_route", _HINTS, ids=[row[0] for row in _HINTS])
@pytest.mark.parametrize("canonical", [True, False], ids=["canonical", "compatibility"])
def test_quality_owner_controls_routing_without_changing_legacy(hint, legacy_route, canonical):
    payload = _routing_payload(hint)
    graph = paragraphs._section_graph()
    shared = object()
    graph._restore = Mock(return_value=(payload, shared))
    graph._update = Mock(side_effect=lambda value, **kwargs: {"payload": value, **kwargs})
    committed = payload.model_copy(update={"retrieval_metadata": {
        "canonical_claim_coverage_state": "FACTUAL_CLAIMS_MATERIALIZED",
        "canonical_claim_count": 1,
    }}, deep=True)
    graph._commit_canonical_claim_coverage = Mock(return_value=committed)
    graph._invoke_verifier_agent = Mock(return_value=payload)
    graph.claim_verification_runtime = object() if canonical else None

    result = graph._evaluate_generation({})

    assert result["route"] == ("finalize" if canonical else legacy_route)
    assert result["node_name"] == "evaluate_generation"
    assert result["shared_state"] is shared
    if canonical:
        graph._commit_canonical_claim_coverage.assert_called_once_with(payload)
        graph._invoke_verifier_agent.assert_not_called()
        # Preserve the exact producer payload, including conservative diagnostics.
        assert result["payload"] == committed
        assert result["payload"].generation_attempt == payload.generation_attempt
        assert result["payload"].budget_state == payload.budget_state
        assert result["payload"].generation_check == payload.generation_check
    else:
        graph._commit_canonical_claim_coverage.assert_not_called()
        graph._invoke_verifier_agent.assert_called_once()
        expected_increment = int(legacy_route == "generate")
        assert result["payload"].generation_attempt == payload.generation_attempt + expected_increment
        if expected_increment:
            assert result["payload"].budget_state["rewrite_rounds"] == 1


def test_canonical_coverage_failure_propagates_before_any_handoff():
    graph = paragraphs._section_graph()
    payload = _routing_payload("unbound-citation")
    graph._restore = Mock(return_value=(payload, object()))
    graph.claim_verification_runtime = object()
    failure = RuntimeError("canonical coverage producer failed")
    graph._commit_canonical_claim_coverage = Mock(side_effect=failure)
    graph._invoke_verifier_agent = Mock()
    graph._update = Mock()
    with pytest.raises(RuntimeError) as raised:
        graph._evaluate_generation({})
    assert raised.value is failure
    graph._invoke_verifier_agent.assert_not_called()
    graph._update.assert_not_called()


def test_actual_writer_without_citation_reaches_canonical_quality_and_exact_replay(tmp_path, monkeypatch):
    """Natural omitted citation exercises the production branch and durable owners."""
    project = workflow._w13_current_new_task_project_input(
        workflow._w13_single_section_project_input("task-gnd345-empty")
    )
    project.generation_requirements.need_citation = True
    project.generation_requirements.citation_required_sections = list(
        project.generation_requirements.required_sections
    )
    project.generation_requirements.extra.update({
        "enable_section_aware_retrieval": True,
        "enable_corrective_section_retrieval": True,
    })
    updates = []
    original_update = SectionExecutionGraph._update

    def observe(graph, payload, **kwargs):
        if kwargs.get("node_name") == "evaluate_generation":
            updates.append((payload.model_copy(deep=True), kwargs["route"]))
        return original_update(graph, payload, **kwargs)

    monkeypatch.setattr(SectionExecutionGraph, "_update", observe)
    data = grounding._grounding_run(tmp_path, "empty", project_override=project)
    engine, calls, observations, _arguments, summary = data
    assert len(updates) == 1
    evaluated, route = updates[0]
    assert evaluated.citation_required and evaluated.corrective_retrieval_enabled
    assert not evaluated.section.eval_result.checks.get("citation_bound", False)
    assert not evaluated.verifier_agent_payloads
    assert route == "finalize"
    assert len(evaluated.canonical_claim_coverage_proof_refs) == 1
    assert len(evaluated.canonical_claim_verification_refs) == 1
    assert evaluated.retrieval_metadata["canonical_claim_count"] == 1
    assert evaluated.retrieval_metadata["canonical_claim_verifier_invoked"] is True
    assert "[C" not in evaluated.section.content

    contracts = [request.extra.get("response_contract") for request in calls]
    assert contracts.count(grounding.extraction_tests.CONTRACT) == 1
    assert contracts.count(grounding.support_tests.SUPPORT_RESPONSE_CONTRACT) == 1
    assert contracts.count(grounding.ASSOCIATION_CONTRACT) == 0
    assert sum(request.extra.get("call_purpose") == "scheme_section_generation" for request in calls) == 1
    assert {row[0] for row in observations} == {"SECTION", "DOCUMENT"}
    for _scope, item, facts, completion, gate_ref in observations:
        # Empty occurrence domain is N/A; it is never fabricated citation PASS.
        assert item.state.value == "NOT_APPLICABLE"
        assert not facts
        assert any(row.state.value in {"FAIL", "INDETERMINATE"} for row in completion.items)
        gate = engine.stage_service.target_post_write_composition.gate_store.resolve_exact(gate_ref)
        assert gate.overall_quality_state.value in {"FAIL", "UNRESOLVED"}

    top = workflow._latest_w13_top_level_payload(engine)
    composition = engine.stage_service.target_post_write_composition
    control = composition.control_store.resolve_exact(top.target_post_write_control_decision_ref)
    assert control.enforcement_action.value == "OUTPUT_BEST_EFFORT"
    assert summary["status"] == "partial_success"
    rounds = [json.loads(line) for line in (tmp_path / "runs/research_runtime.jsonl").read_text("utf-8").splitlines() if line]
    assert len(rounds) == 1
    assert rounds[0]["record"]["execution_outcome"] == "COMPLETED"
    assert rounds[0]["resulting_progress"]["committed_round_count"] == 1
    assert engine.budget_runtime(top.task_id, top.run_id).canonical_state().used.logical_tool_calls == 1
    # Existing durable replay assertions cover exact Gate/Control/Release refs,
    # zero provider/scanner/support calls and no canonical business writes.
    grounding.test_grounding_outcomes_and_terminal_reopen_are_exact(("empty", data))
