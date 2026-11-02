"""341: canonical source bindings from real Paragraph/Plan/Fact/Evidence owners."""
from __future__ import annotations

from contextlib import ExitStack
from dataclasses import replace
import json
import os
from pathlib import Path
from unittest.mock import patch

import pytest

from application.mainline_service import MainlineApplicationService
from apps.enterprise_document.schemas.agent_rag_v1.verification import (
    ClaimBindingFactsRefSchema, ClaimBindingRelation,
)
from apps.enterprise_document.services.agent_rag_v1.claim_composition import (
    TargetClaimCompositionError, TargetClaimRuntimeValidators,
)
from apps.enterprise_document.services.agent_rag_v1.claim_pipeline import TargetClaimMaterializer
from core.config import get_settings
from core.hashing import canonical_sha256
from model_gateway.fake_llm_client import FakeLLMClient
from rag.services.rag_service import FakeRAGService
from schemas.rag import EvidenceBundleSchema
from schemas.claim import ClaimType
import test_multi_agent_v3_claim_materialization_mainline as extraction_tests
import test_multi_agent_v3_default_planner_facts as planner_tests
import test_multi_agent_v3_workflow_recovery as workflow_tests


def test_binding_composer_has_a_real_source_producer():
    assert callable(getattr(TargetClaimRuntimeValidators, "bind_claim_records", None))
    assert callable(getattr(TargetClaimRuntimeValidators, "materialize_sources", None))


@pytest.fixture(scope="module", params=("normal", "fallback"))
def binding_mainline(tmp_path_factory, request):
    route = request.param
    root = tmp_path_factory.mktemp("claim341-" + route)
    task_id, run_id = "task341-" + route, "run341-" + route
    project = workflow_tests._w13_current_new_task_project_input(workflow_tests._w13_single_section_project_input(task_id)).model_copy(
        update={"business_goal": "计划部署12台服务器，建设安全、可靠的政务云平台。"}
    )
    calls, observations = [], []
    original_generate = FakeLLMClient.generate
    original_retrieve = FakeRAGService.retrieve

    def scoped_evidence(service, req):
        # Explicit upstream fixture restriction, before any canonical snapshot is stored.
        # Generic FakeRAG allows broader Claim types; never assume it is historical-only.
        raw = original_retrieve(service, req).model_dump(mode="json")
        for item in raw["item_assessments"]:
            item["supported_claim_types"] = ["general_method"]
            item["forbidden_claim_types"] = sorted(set(item.get("forbidden_claim_types", [])) | {"current_project_quantity"})
        raw["assessment"]["supported_claim_types"] = ["general_method"]
        raw["assessment"]["forbidden_claim_types"] = sorted(set(raw["assessment"].get("forbidden_claim_types", [])) | {"current_project_quantity"})
        return EvidenceBundleSchema.model_validate(raw)

    # This is an observation hook, not a replacement binding producer.
    original_bind = TargetClaimRuntimeValidators.bind_claim_records

    def observe(composer, *, claim_runtime, records, operation_context):
        model_call_count_before = len(calls)
        try:
            result = original_bind(composer, claim_runtime=claim_runtime, records=records,
                                   operation_context=operation_context)
        except Exception as exc:
            raise AssertionError(f"Binding failure: {exc!r}; cause={exc.__cause__!r}") from exc
        before = claim_runtime.ledger_path.read_bytes()
        replay = original_bind(composer, claim_runtime=claim_runtime, records=records,
                               operation_context=operation_context)
        assert replay == result
        assert claim_runtime.ledger_path.read_bytes() == before
        for claim, binding in zip(records, result):
            assert claim_runtime.resolve_claim_head(claim.claim_ref) is None, "Binding itself cannot create a verdict"
            sources = composer.materialize_sources(claim, operation_context.active_lineage)
            candidate = claim_runtime._binding_record_candidate(binding)
            # Currentness/identity validation also rechecks the actual observation.
            composer.validate_binding_sources(candidate, operation_context.active_lineage)
            with patch.object(composer, "claim_resolver", side_effect=AssertionError("Claim lock re-entry")):
                composer.validate_binding_sources(candidate, operation_context.active_lineage)
            # Exact source-currentness failure must reject; never treat it as empty evidence.
            original_head = composer.artifact_runtime.resolve_active
            for stale_version in {item.artifact_version_ref for item in binding.exact_authorized_source_refs}:
                def missing_head(logical, target=stale_version.logical_ref):
                    return None if logical == target else original_head(logical)
                with patch.object(composer.artifact_runtime, "resolve_active", missing_head):
                    with pytest.raises((ValueError, TargetClaimCompositionError)):
                        composer.validate_binding_sources(candidate, operation_context.active_lineage)
            bad_fact = binding.facts[0].model_copy(update={"relation": ClaimBindingRelation.SUPPORTS})
            tampered = candidate.model_copy(update={"facts": (bad_fact, *binding.facts[1:])}, deep=True)
            with pytest.raises((ValueError, TargetClaimCompositionError)):
                composer.validate_binding_sources(tampered, operation_context.active_lineage)
            bad_source = candidate.exact_authorized_source_refs[0].model_copy(
                update={"exact_source_ref": "evidence-item://sha256/forged"})
            wrong = candidate.model_copy(update={"exact_authorized_source_refs": (bad_source, *candidate.exact_authorized_source_refs[1:])}, deep=True)
            with pytest.raises((ValueError, TargetClaimCompositionError)):
                composer.validate_binding_sources(wrong, operation_context.active_lineage)
            assert claim_runtime.ledger_path.read_bytes() == before
            observations.append((claim, binding, sources))
        assert len(calls) == model_call_count_before, "deterministic binding must not call a model"
        return result

    def provider(client, req):
        calls.append(req.model_copy(deep=True))
        response = original_generate(client, req)
        if req.extra.get("response_contract") == planner_tests.CONTRACT:
            sections = planner_tests._planning_contract(req)["section_contracts"]
            proposal = {"sections": [
                {"section_id": section["section_id"], "fact_requirements": [
                    {"fact_key": "project_input.business_goal", "importance": "required",
                     "purpose": "使用调用方明确提供的项目目标。"}
                ]} for section in sections
            ]}
            return response.model_copy(
                update={"content": json.dumps(proposal, ensure_ascii=False)}, deep=True
            )
        if req.extra.get("call_purpose") == "scheme_section_generation":
            return response.model_copy(update={"content": extraction_tests.TEXT * 4}, deep=True)
        if req.extra.get("response_contract") == extraction_tests.CONTRACT:
            units = extraction_tests._task_contract(req)["coverage_units"]
            payload = {"units": [{"unit_id": unit["unit_id"], "accounting_state": "FACTUAL_CLAIMS_MATERIALIZED",
                        "claims": [{"quote": extraction_tests.TEXT, "occurrence_index": i, "claim_type": kind}
                                   for i, kind in enumerate(("general_method", "current_project_quantity"))]}
                                  for unit in units]}
            return response.model_copy(update={"content": json.dumps(payload, ensure_ascii=False)}, deep=True)
        if route == "fallback" and req.extra.get("response_contract") == "criterion_support_evaluator_v1":
            payload = json.loads(response.content)
            payload.update(support_state="not_supported", evidence_ids=[], conflicts=[])
            return response.model_copy(update={"content": json.dumps(payload)}, deep=True)
        return response

    factory = workflow_tests._W13InspectingFactory()
    settings = replace(get_settings(), data_root=root, default_model_name="fake_llm",
                       supervisor_model_name="fake_llm", enable_llm_routing=False,
                       trace_enabled=True, data_capture_enabled=True,
                       gate_enforcement_mode="strict", gate_recovery_rounds_limit=0)
    with ExitStack() as stack:
        stack.enter_context(patch.dict(os.environ, {"USE_REAL_RAG_TOOL": "false", "FAKE_RAG_SCENARIO": "default",
                           "FAKE_LLM_SCENARIO": "default", "ENABLE_AGENT_SELF_RAG": "false", "ENABLE_SEMANTIC_GATE": "false"}))
        stack.enter_context(patch.object(FakeLLMClient, "generate", provider))
        stack.enter_context(patch.object(FakeRAGService, "retrieve", scoped_evidence))
        stack.enter_context(patch.object(TargetClaimRuntimeValidators, "bind_claim_records", observe))
        summary = MainlineApplicationService(supervisor_factory=factory).run(
            project_root=Path(__file__).resolve().parents[2], user_input=project.user_query,
            task_id=task_id, run_id=run_id, output_root=root, clean_existing=True,
            settings=settings, project_input=project.model_dump(mode="json"),
            allow_demo_defaults=False, enable_agent_self_rag=False,
        )
    engine = factory.engines[0]
    if summary["status"] != "partial_success":
        error = workflow_tests._latest_w13_top_level_payload(engine).terminal_error
        raise AssertionError(error.model_dump(mode="json") if error else summary["status"])
    assert len(observations) == 2
    return engine, observations, calls, summary


def test_real_claim_binding_refs_are_persisted_not_only_in_metadata(binding_mainline):
    engine, observations, _, _ = binding_mainline
    section = workflow_tests._latest_w13_section_payload(engine)
    refs = section.retrieval_metadata["canonical_claim_binding_facts_refs"]
    assert len(refs) == 2
    runtime = engine.section_execution_graph.claim_verification_runtime
    stored = [runtime.resolve_binding_facts(ClaimBindingFactsRefSchema.model_validate(ref)) for ref in refs]
    assert stored == [binding for _, binding, _ in observations]
    assert section.retrieval_metadata["canonical_claim_binding_count"] == 2


def test_binding_sources_are_exact_and_claim_eligible(binding_mainline):
    _, observations, _, _ = binding_mainline
    evidence_counts = {}
    for claim, binding, sources in observations:
        selected = [row for row in sources if row.source_ref.source_kind == "EVIDENCE"]
        evidence_counts[claim.claim_type] = len(selected)
        eligible = set(TargetClaimMaterializer.eligible_evidence_ids(
            next(row.material["bundle"] for row in sources if row.source_ref.source_kind == "EVIDENCE_PACKAGE"),
            ClaimType(claim.claim_type),
        ))
        for row in selected:
            assert row.material["evidence_id"] in eligible
            assert row.source_ref.artifact_version_ref.logical_ref.artifact_type == "evidence_package"
            assert row.source_ref.source_scope_ref == "section:" + claim.section_id
            assert row.source_ref.exact_source_ref.startswith("evidence-item://sha256/")
        assert binding.source_set_hash == canonical_sha256([
            item.model_dump(mode="json") for item in binding.exact_authorized_source_refs
        ])
    assert evidence_counts["general_method"] > 0, "positive test requires actual eligible RAG Evidence"
    assert evidence_counts["current_project_quantity"] == 0, "fixture Evidence explicitly forbids current project quantity"


def test_v2_fact_snapshot_with_requirement_materializes_current_claim_sources(binding_mainline):
    _, observations, _, _ = binding_mainline
    for _, _, sources in observations:
        snapshot = next(
            row for row in sources if row.source_ref.source_kind == "FACT_SNAPSHOT"
        )
        assert len(snapshot.material["fact_requirements"]) == 1
        assert snapshot.material["fact_requirements"][0]["fact_key"] == "project_input.business_goal"
        assert snapshot.material["fact_requirements"][0]["importance"] == "required"
        assert snapshot.material["facts"]
        assert snapshot.material["facts"][0]["fact_id"].endswith("_business_goal")


def test_empty_evidence_has_scope_records_not_fabricated_evidence(binding_mainline):
    _, observations, _, _ = binding_mainline
    _, binding, sources = next(row for row in observations if row[0].claim_type == "current_project_quantity")
    assert {row.source_ref.source_kind for row in sources} == {
        "EVIDENCE_PACKAGE", "FACT_SNAPSHOT", "FACT",
    }
    assert all(
        row.material["fact_type"] == "business_goal"
        for row in sources if row.source_ref.source_kind == "FACT"
    )
    assert all(item.relation is ClaimBindingRelation.CONTEXT_ONLY for item in binding.facts)
    assert all(item.source_kind != "EVIDENCE" for item in binding.exact_authorized_source_refs)


def test_src_rev_04_unmarked_wrong_project_number_still_reaches_fact_and_verifier(binding_mainline):
    engine, observations, calls, _ = binding_mainline
    section = workflow_tests._latest_w13_section_payload(engine)
    assert "2台服务器" in section.section.content
    assert "[fact_" not in section.section.content
    assert "12台服务器" not in section.section.content
    quantity = next(row for row in observations if row[0].claim_type == "current_project_quantity")
    claim, binding, sources = quantity
    facts = [row for row in sources if row.source_ref.source_kind == "FACT"]
    assert facts and any("12台服务器" in row.material["normalized_value"] for row in facts)
    assert all(item.relation is ClaimBindingRelation.CONTEXT_ONLY for item in binding.facts)
    assert any(req.extra.get("call_purpose") == "claim_verification" for req in calls)
    runtime = engine.section_execution_graph.claim_verification_runtime
    assert not runtime.resolve_active_verified_claim(claim.claim_ref).active_verified_claim


def test_binding_does_not_create_verdict_or_repeat_model_calls(binding_mainline):
    engine, observations, calls, summary = binding_mainline
    runtime = engine.section_execution_graph.claim_verification_runtime
    assert all(runtime.resolve_claim_head(claim.claim_ref) is not None for claim, _, _ in observations)
    assert all(not runtime.resolve_active_verified_claim(claim.claim_ref).active_verified_claim for claim, _, _ in observations)
    assert len([req for req in calls if req.extra.get("call_purpose") == "claim_materialization"]) == 1
    assert len([req for req in calls if req.extra.get("call_purpose") == "claim_verification"]) == 2
    assert len([req for req in calls if req.extra.get("call_purpose") == "claim_support"]) == 1
    assert summary["status"] == "partial_success"
