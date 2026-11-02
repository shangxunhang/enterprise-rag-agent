"""PWA-004 phase 1: bounded, opt-in review-draft continuation contracts.

Offline only: production durable owners, local ScriptedProvider, no network,
no paid model or formal E2E. All real receipts are in pytest's temporary root.
"""
from __future__ import annotations

from copy import deepcopy
import importlib.util
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from apps.enterprise_document.services.agent_rag_v1.claim_verification_runtime import (
    ClaimBindingError, ClaimVerificationRuntime, VerifierSupportVerdictAdmissionError,
    VerifierCandidateContractViolationError,
)
from apps.enterprise_document.workflows.section_execution_graph import (
    SectionExecutionGraph, PWA004_REJECTION_KEY, PWA004_REVIEW_POLICY,
)
from apps.enterprise_document.services.scheme_writer.stage_service import (
    SchemeGenerationStageService,
)
from apps.enterprise_document.schemas.agent_rag_v1.claim_support import SupportRelation
from apps.enterprise_document.schemas.agent_rag_v1.verification import (
    VerificationVerdict,
)
from backend.tests.local_verifier_recovery_helpers import (
    create_fixture, reopen_fixture, legal_reply, provider_rows, verify,
)


def _actual_forbidden(tmp_path):
    fx = create_fixture(tmp_path, [legal_reply("VERIFIED", ["@all_evidence"])],
                        support_relation="CONTRADICTED")
    with pytest.raises(VerifierSupportVerdictAdmissionError) as caught:
        verify(fx)
    return fx, caught.value


def _review_graph(fx):
    graph = object.__new__(SectionExecutionGraph)
    graph.coordinator = SimpleNamespace(
        claim_verifier_composition=fx.composer,
        claim_support_composition=fx.support_composition,
    )
    graph.claim_verification_runtime = fx.runtime
    return graph


def test_exact_support_verdict_guard_still_rejects_without_canonical_result(tmp_path):
    fx, error = _actual_forbidden(tmp_path)
    assert error.rule_code == "VERIFIER_VERIFIED_FORBIDDEN_BY_SHARED_SUPPORT"
    assert error.claim_ref == fx.claim.claim_ref
    assert error.support_ref == fx.support.support_ref
    assert error.support_relation == "CONTRADICTED"
    assert isinstance(error, ClaimBindingError)
    assert not isinstance(error, VerifierCandidateContractViolationError)
    assert fx.runtime.resolve_claim_head(fx.claim.claim_ref) is None
    assert len(provider_rows(fx)) == 1
    restored = reopen_fixture(tmp_path)
    with pytest.raises(VerifierSupportVerdictAdmissionError):
        verify(restored)
    assert len(provider_rows(restored)) == 1


def test_exact_immutable_candidate_receipt_and_recovered_read(tmp_path):
    fx, error = _actual_forbidden(tmp_path)
    graph = _review_graph(fx)
    row = graph._pwa004_rejection_receipt(
        error, expected_claim=fx.claim, expected_binding=fx.binding,
        expected_support=fx.support, expected_lineage=fx.context.active_lineage,
    )
    assert row["candidate_ref"].endswith("/candidate")
    assert row["claim_ref"] == fx.claim.claim_ref.model_dump(mode="json")
    assert row["attempt_id"] == fx.context.active_lineage.attempt_id
    restored = reopen_fixture(tmp_path)
    graph = _review_graph(restored)
    payload = SimpleNamespace(
        task_id=restored.lifecycle.load_run_identity(restored.context.active_lineage.run_id).task_id,
        run_id=restored.context.active_lineage.run_id,
        section_id=restored.claim.section_id,
        active_execution_lineage=restored.context.active_lineage,
        retrieval_metadata={PWA004_REJECTION_KEY: [row]},
    )
    graph._pwa004_validate_recovered_rejections(payload)
    assert len(provider_rows(restored)) == 1
    wrong = deepcopy(row)
    wrong["candidate_ref"] = "artifact://canonical-verifier-v2/not-the-candidate/candidate"
    payload.retrieval_metadata = {PWA004_REJECTION_KEY: [wrong]}
    with pytest.raises((ValueError, RuntimeError)):
        graph._pwa004_validate_recovered_rejections(payload)
    assert len(provider_rows(restored)) == 1


def test_in_memory_indeterminate_guard_does_not_upgrade_to_verified(tmp_path):
    fx, error = _actual_forbidden(tmp_path)
    contradictory = fx.support
    unknown = contradictory.model_copy(update={
        "relation": SupportRelation.INDETERMINATE,
        "supporting_refs": (), "contradicting_refs": (),
    })
    with pytest.raises(VerifierSupportVerdictAdmissionError) as caught:
        ClaimVerificationRuntime.validate_v2_provenance(fx.binding, unknown, error.candidate)
    assert caught.value.support_relation == "INDETERMINATE"
    assert fx.runtime.resolve_claim_head(fx.claim.claim_ref) is None


def test_review_policy_explicit_optin_and_no_parallel_silent_fallback():
    def project(extra):
        return SimpleNamespace(generation_requirements=SimpleNamespace(extra=extra))
    assert not SchemeGenerationStageService._pwa004_opted_in(project({}))
    assert not SectionExecutionGraph._pwa004_review_enabled(project({}))
    assert SchemeGenerationStageService._pwa004_opted_in(project(
        {"verifier_rejection_policy": PWA004_REVIEW_POLICY}))
    assert SectionExecutionGraph._pwa004_review_enabled(project(
        {"verifier_rejection_policy": PWA004_REVIEW_POLICY}))
    with pytest.raises(ValueError):
        SchemeGenerationStageService._pwa004_opted_in(project(
            {"verifier_rejection_policy": "unreviewed" }))


def test_future_strict_launch_policy_exact_and_default_unchanged():
    root = Path(__file__).resolve().parents[2]
    p = root / "scripts/run_post_phase1_full8.py"
    spec = importlib.util.spec_from_file_location("pwa004_launcher_unit", p)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    contract = module._read_json(
        root / "docs/full8_r04_launch_preparation_20261026_r01/R04_NATIVE_LAUNCH_CONTRACT_CANDIDATE.json")
    original_path = contract["projection"]["original_project"]
    if not Path(original_path).is_file():
        pytest.skip("Original pinned business input unavailable")
    original = module._read_json(original_path)
    historical = deepcopy(original)
    historical["task_id"] = "task-pwa004-prospective-strict"
    historical["generation_requirements"]["extra"]["verifier_decision_profile"] = module.STRICT_V3_PROFILE
    allowed = ["task_id", module.STRICT_V3_PROFILE_PATH]
    module._validate_strict_v3_project_projection(
        original_path, historical, allowed,
        task_id=historical["task_id"], run_id="run-pwa004-prospective-strict")
    proposed = deepcopy(historical)
    proposed["generation_requirements"]["extra"]["verifier_rejection_policy"] = module.PWA004_REJECTION_POLICY
    future_allow = allowed + [module.PWA004_REJECTION_POLICY_PATH]
    module._validate_strict_v3_project_projection(
        original_path, proposed, future_allow,
        task_id=proposed["task_id"], run_id="run-pwa004-prospective-strict")
    with pytest.raises(module.LaunchContractError):
        module._validate_strict_v3_project_projection(
            original_path, proposed, allowed,
            task_id=proposed["task_id"], run_id="run-pwa004-prospective-strict")
    polluted = deepcopy(proposed)
    polluted["generation_requirements"]["required_sections"] = ["unauthorized new scope"]
    with pytest.raises(module.LaunchContractError):
        module._validate_strict_v3_project_projection(
            original_path, polluted, future_allow,
            task_id=polluted["task_id"], run_id="run-pwa004-prospective-strict")



def test_offline_full8_stage_review_draft_continues_eighth_section(tmp_path):
    """Stage-only fault seam; not evidence of an original M6 admission failure.

    The Fake Provider/RAG runs the production graph. One *returned* section
    is altered after its canonical source was committed to simulate the Stage
    receiving review-only material. Native M6 / absent-source integration
    requires its own test and cannot be inferred from this synthetic seam.
    """
    from unittest.mock import patch
    from apps.enterprise_document.services.scheme_writer.input_service import SchemeInputService
    from apps.enterprise_document.workflows.section_execution_graph import SectionExecutionGraph
    from schemas.status import ExecutionStatus
    from test_phase3_section_execution_subgraph import _run_fake

    original_read = SchemeInputService.read_inputs
    original_execute = SectionExecutionGraph.execute
    section_orders = []

    def add_policy(service, shared_state):
        project, tables, facts = original_read(service, shared_state)
        writing = project.generation_requirements
        changed = writing.model_copy(update={"extra": {
            **dict(writing.extra or {}),
            "verifier_rejection_policy": PWA004_REVIEW_POLICY,
        }}, deep=True)
        return project.model_copy(update={"generation_requirements": changed}, deep=True), tables, facts

    def mock_review_only(graph, request, *, shared_state, citation_registry, workflow_namespace):
        if request.section_plan.section_order == 8:
            assert all(section.section_order != 7 for section in request.previous_sections)
        result = original_execute(graph, request, shared_state=shared_state,
                                  citation_registry=citation_registry,
                                  workflow_namespace=workflow_namespace)
        section_orders.append(result.section.section_order)
        if result.section.section_order != 7:
            return result
        row = {"rule_code": "VERIFIER_VERIFIED_FORBIDDEN_BY_SHARED_SUPPORT",
               "attempt_id": result.active_execution_lineage.attempt_id,
               "candidate_ref": "artifact://synthetic-local-test/candidate",
               "claim_ref": {"claim_id": "synthetic-review-only"}}
        section = result.section.model_copy(update={
            "status": ExecutionStatus.FAILED,
            "extra": {**dict(result.section.extra or {}), PWA004_REJECTION_KEY: [row]},
        }, deep=True)
        return result.model_copy(update={
            "section": section, "canonical_section_source_binding": None,
            "need_human_review": True,
        }, deep=True)

    with patch.object(SchemeInputService, "read_inputs", add_policy), patch.object(
            SectionExecutionGraph, "execute", mock_review_only):
        summary, _factory = _run_fake(tmp_path, run_id="pwa004_review_stage_fake_full8")
    assert len(section_orders) == 8, section_orders
    assert summary["status"] == "failed"
    terminal_error = summary["supervisor_result"]["error"]
    assert terminal_error["error_code"] == "VERIFIER_ADMISSION_INCOMPLETE"
    assert terminal_error["retryable"] is False
    assert terminal_error["details"]["c1_triggering_attempt_id"]
    assert summary["task_state"]["status"] == "failed"
    draft = summary["generated_draft"]
    assert draft is not None and len(draft["sections"]) == 8
    assert draft["sections"][6]["status"] == "failed"
    assert draft["sections"][7]["section_order"] == 8
    assert draft["extra"]["pwa004_review_draft"]["rejections"][0]["section_id"] == draft["sections"][6]["section_id"]
    assert summary["target_release_publication"] is None



def test_real_section_claim_loop_catches_durable_m6_rejection_without_redispatch(tmp_path):
    """Production Section method + durable Verifier/M6 owner, fake upstream adapters.

    The extraction/binding/support results are seeded from real committed owner
    records; this is NOT a full Planner/RAG/Writer/E2E execution.
    """
    from unittest.mock import patch
    from apps.enterprise_document.schemas.agent_rag_v1.claims import ClaimCoverageAccountingState
    from apps.enterprise_document.workflows import section_execution_graph as section_module

    fx = create_fixture(tmp_path, [legal_reply("VERIFIED", ["@all_evidence"])],
                        support_relation="CONTRADICTED")

    class CandidatePayload(SimpleNamespace):
        def model_copy(self, *, update, deep=True):
            return CandidatePayload(**{**vars(self), **update})

    committed = SimpleNamespace(
        records=(fx.claim,),
        coverage_accounting=SimpleNamespace(
            accounting_state=ClaimCoverageAccountingState.FACTUAL_CLAIMS_MATERIALIZED),
        coverage_proof_ref=SimpleNamespace(coverage_proof_id="durable-fake-coverage-proof"))
    bind = SimpleNamespace(bind_claim_records=lambda **kwargs: (fx.binding,))
    shared_support = SimpleNamespace(
        runtime=fx.support_composition.runtime,
        support_bindings=lambda **kwargs: (fx.support,))
    extractor = SimpleNamespace(
        claim_runtime=fx.runtime,
        extract_and_commit=lambda **kwargs: committed)
    graph = object.__new__(SectionExecutionGraph)
    graph.coordinator = SimpleNamespace(
        claim_extraction_runtime=extractor,
        claim_binding_composition=bind,
        claim_support_composition=shared_support,
        claim_verifier_composition=fx.composer,
        section_dependencies=None,
        memory_composition=None,
    )
    graph.claim_verification_runtime = fx.runtime
    graph.paragraph_runtime = fx.paragraph
    graph._canonical_paragraph_projection = lambda payload: (
        SimpleNamespace(generation_membership_state="SEALED"), (object(),), ())
    section = fx.claim.section_id
    project = SimpleNamespace(generation_requirements=SimpleNamespace(
        extra={"verifier_rejection_policy": PWA004_REVIEW_POLICY}))
    payload = CandidatePayload(
        task_id=fx.lifecycle.load_run_identity(fx.context.active_lineage.run_id).task_id,
        run_id=fx.context.active_lineage.run_id,
        section_id=section,
        active_execution_lineage=fx.context.active_lineage,
        writer_generation_invocation_ref=SimpleNamespace(
            invocation_sha256="f" * 64),
        retrieval_metadata={},
        project_input=project,
    )
    # No new fake Provider or fabricated Claim/Support authority; the adapter
    # only matches an already committed fixture's operation context.
    with patch.object(section_module, "ClaimOperationContextSchema",
                      side_effect=lambda **kwargs: fx.context):
        actual = graph._commit_canonical_claim_coverage(payload)
        again = graph._commit_canonical_claim_coverage(payload)
    assert actual.retrieval_metadata[PWA004_REJECTION_KEY]
    assert actual.retrieval_metadata == again.retrieval_metadata
    assert actual.canonical_claim_verification_refs == []
    assert fx.runtime.resolve_claim_head(fx.claim.claim_ref) is None
    assert len(provider_rows(fx)) == 1


def test_review_only_skips_hard_dependency_from_precommit_planner(tmp_path):
    """Valid Planner edge BEFORE canonical commit; Stage must avoid dependent dispatch.

    Section 7 still uses a synthetic Stage result to isolate scheduler behavior.
    The hard dependency is installed at the Planner adapter's output, before
    ensure_section_plan commits that immutable plan.
    """
    from dataclasses import replace
    from unittest.mock import patch
    from apps.enterprise_document.services.scheme_writer.agent_integration.planner_adapter import MainlinePlannerAdapter
    from apps.enterprise_document.services.scheme_writer.input_service import SchemeInputService
    from apps.enterprise_document.workflows.section_execution_graph import SectionExecutionGraph
    from schemas.status import ExecutionStatus
    from test_phase3_section_execution_subgraph import _run_fake

    original_read = SchemeInputService.read_inputs
    original_plan = MainlinePlannerAdapter.plan
    original_execute = SectionExecutionGraph.execute
    seen = []

    def policy(service, state):
        project, tables, facts = original_read(service, state)
        req = project.generation_requirements
        req = req.model_copy(update={"extra": {**dict(req.extra or {}),
            "verifier_rejection_policy": PWA004_REVIEW_POLICY}}, deep=True)
        return project.model_copy(update={"generation_requirements": req}, deep=True), tables, facts

    def plan_before_commit(adapter, **kwargs):
        outcome = original_plan(adapter, **kwargs)
        members = list(outcome.document_plan.sections)
        assert len(members) == 8
        members[7] = members[7].model_copy(update={"depends_on": [members[6].section_id]}, deep=True)
        plan = outcome.document_plan.model_copy(update={"sections": members}, deep=True)
        return replace(outcome, document_plan=plan)

    def reviewed_result(graph, request, *, shared_state, citation_registry, workflow_namespace):
        assert request.section_plan.section_order != 8, "Dependent Writer was improperly dispatched"
        result = original_execute(graph, request, shared_state=shared_state,
            citation_registry=citation_registry, workflow_namespace=workflow_namespace)
        seen.append(result.section.section_order)
        if result.section.section_order != 7:
            return result
        refusal = {"rule_code": "VERIFIER_VERIFIED_FORBIDDEN_BY_SHARED_SUPPORT",
            "attempt_id": result.active_execution_lineage.attempt_id,
            "candidate_ref": "artifact://synthetic-review-dependency/candidate",
            "claim_ref": {"claim_id": "synthetic-dependency"}}
        section = result.section.model_copy(update={
            "status": ExecutionStatus.FAILED,
            "extra": {**dict(result.section.extra or {}), PWA004_REJECTION_KEY: [refusal]},
        }, deep=True)
        return result.model_copy(update={
            "section": section, "canonical_section_source_binding": None,
            "need_human_review": True,
        }, deep=True)

    with patch.object(SchemeInputService, "read_inputs", policy), patch.object(
        MainlinePlannerAdapter, "plan", plan_before_commit
    ), patch.object(SectionExecutionGraph, "execute", reviewed_result):
        summary, _ = _run_fake(tmp_path, run_id="pwa004_precommit_hard_dep")
    assert seen == list(range(1, 8)), summary["supervisor_result"].get("error")
    assert summary["status"] == "failed"
    draft = summary["generated_draft"]
    assert draft is not None and len(draft["sections"]) == 7
    assert len(draft["missing_sections"]) == 1
    review = draft["extra"]["pwa004_review_draft"]
    assert len(review["hard_dependency_blocked"]) == 1
    assert review["hard_dependency_blocked"][0]["reason_code"] == "PWA004_HARD_DEPENDENCY_UNVERIFIED"
    assert summary["target_release_publication"] is None


def test_tampered_committed_plan_dependency_fails_closed_before_dispatch(tmp_path):
    """Negative guard: changed dependencies after Plan freeze must never dispatch."""
    from unittest.mock import patch
    from apps.enterprise_document.services.scheme_writer.input_service import SchemeInputService
    from apps.enterprise_document.services.scheme_writer.stage_service import SchemeGenerationStageService
    from apps.enterprise_document.workflows.section_execution_graph import SectionExecutionGraph
    from schemas.status import ExecutionStatus
    from test_phase3_section_execution_subgraph import _run_fake

    original_read = SchemeInputService.read_inputs
    original_plan = SchemeGenerationStageService.prepare_document_plan
    original_execute = SectionExecutionGraph.execute
    visited = []

    def enable_policy(service, shared_state):
        project, tables, facts = original_read(service, shared_state)
        requirements = project.generation_requirements
        updated = requirements.model_copy(update={"extra": {
            **dict(requirements.extra or {}),
            "verifier_rejection_policy": PWA004_REVIEW_POLICY}}, deep=True)
        return project.model_copy(update={"generation_requirements": updated}, deep=True), tables, facts

    def hard_depend(service, shared_state, payload):
        generated = original_plan(service, shared_state, payload)
        document_plan = generated.document_plan
        ordered = list(document_plan.sections)
        assert len(ordered) == 8
        ordered[7] = ordered[7].model_copy(update={"depends_on": [ordered[6].section_id]}, deep=True)
        return generated.model_copy(update={
            "document_plan": document_plan.model_copy(update={"sections": ordered}, deep=True),
        }, deep=True)

    def synthetic_review(graph, request, *, shared_state, citation_registry, workflow_namespace):
        assert request.section_plan.section_order != 8, "hard-dependent Writer must not run"
        result = original_execute(graph, request, shared_state=shared_state,
                                  citation_registry=citation_registry,
                                  workflow_namespace=workflow_namespace)
        visited.append(result.section.section_order)
        if result.section.section_order != 7:
            return result
        row = {"rule_code": "VERIFIER_VERIFIED_FORBIDDEN_BY_SHARED_SUPPORT",
               "attempt_id": result.active_execution_lineage.attempt_id,
               "candidate_ref": "artifact://synthetic-dependency-test/candidate",
               "claim_ref": {"claim_id": "synthetic-dependency"}}
        section = result.section.model_copy(update={
            "status": ExecutionStatus.FAILED,
            "extra": {**dict(result.section.extra or {}), PWA004_REJECTION_KEY: [row]},
        }, deep=True)
        return result.model_copy(update={
            "section": section, "canonical_section_source_binding": None,
            "need_human_review": True,
        }, deep=True)

    with patch.object(SchemeInputService, "read_inputs", enable_policy), patch.object(
            SchemeGenerationStageService, "prepare_document_plan", hard_depend), patch.object(
            SectionExecutionGraph, "execute", synthetic_review):
        summary, _ = _run_fake(tmp_path, run_id="pwa004_review_hard_dependency_fake")
    # The Plan Source was committed before the test injected its altered edge.
    # Existing canonical Plan identity rejects it before any Section dispatch.
    # This is NOT a passing test of the review-only dependency skip branch.
    assert visited == []
    assert summary["status"] == "failed"
    assert summary["supervisor_result"]["error"]["error_type"] == "WorkflowCutoverIntegrityError"
    assert summary["generated_draft"] == {}
    assert summary["target_release_publication"] is None
