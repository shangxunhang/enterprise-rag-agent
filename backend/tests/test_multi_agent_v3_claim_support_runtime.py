from __future__ import annotations

import pytest

from apps.enterprise_document.schemas.agent_rag_v1.claim_support import (
    ClaimEvidenceSupportCandidateSchema,
    ClaimSupportAuthorityKind,
    ClaimSupportSubjectRefSchema,
    SupportRelation,
)
from apps.enterprise_document.services.agent_rag_v1.claim_support_runtime import (
    ClaimSupportCurrentnessError,
    ClaimSupportIdentityCollision,
    ClaimSupportRuntime,
    ClaimSupportTechnicalFailure,
)
from backend.tests.test_multi_agent_v3_claim_verification_runtime import (
    _commit_claim,
    _context,
)


def _setup_support_runtime(tmp_path):
    env, _paragraph, claim_runtime, _version, _manifest, _candidate, result, *_ = (
        _commit_claim(tmp_path / "claim")
    )
    claim_record = result.records[0]
    subject = ClaimSupportSubjectRefSchema(
        authority_kind=ClaimSupportAuthorityKind.SIF07_CLAIM,
        subject_ref=claim_record.claim_ref.claim_id,
    )
    calls = {"evaluator": 0, "target": 0}
    state = {
        "technical_failure": False,
        "relation": SupportRelation.SUPPORTED,
        "supporting_refs": ("evidence:C1",),
        "contradicting_refs": (),
    }

    def _target_validator(target, context) -> None:
        claim_runtime.validate_operation_context(context)
        calls["target"] += 1

    def _evaluator(subject_arg, target, contract, context):
        claim_runtime.validate_operation_context(context)
        calls["evaluator"] += 1
        if state["technical_failure"]:
            raise ClaimSupportTechnicalFailure("provider timeout")
        return ClaimEvidenceSupportCandidateSchema(
            claim_support_subject_ref=subject_arg,
            evidence_target_set_ref=target,
            support_semantic_contract_version=contract,
            relation=state["relation"],
            supporting_refs=tuple(state["supporting_refs"]),
            contradicting_refs=tuple(state["contradicting_refs"]),
            evaluation_occurrence_ref="support-eval://1",
            evaluator_policy_id="shared-support-evaluator",
            evaluator_policy_version="v1",
        )

    runtime = ClaimSupportRuntime(
        claim_runtime=claim_runtime,
        evidence_target_validator=_target_validator,
        support_evaluator=_evaluator,
        ledger_path=tmp_path / "claim-support.jsonl",
    )
    context = _context(
        env,
        authorization_ref="support-evaluation://authorized-1",
        purpose="claim-evidence-support",
    )
    return env, claim_runtime, claim_record, subject, runtime, context, calls, state


def test_evidence_target_set_identity_is_tagged_sorted_and_deterministic(tmp_path) -> None:
    _env, _claim_runtime, _claim, subject, runtime, _context0, *_ = (
        _setup_support_runtime(tmp_path)
    )
    first = runtime.materialize_evidence_target_set(
        subject,
        ["evidence:C2", "evidence:C1", "evidence:C1"],
        "target-set-v1",
    )
    replay = runtime.materialize_evidence_target_set(
        subject,
        ["evidence:C1", "evidence:C2"],
        "target-set-v1",
    )

    assert first == replay
    assert first.claim_support_subject_ref.authority_kind is (
        ClaimSupportAuthorityKind.SIF07_CLAIM
    )
    assert first.evidence_member_refs == ("evidence:C1", "evidence:C2")
    assert first.evidence_target_set_id.startswith("evidence-target-set://sha256/")


def test_support_evaluate_commit_resolve_exact_record(tmp_path) -> None:
    _env, _claim_runtime, _claim, subject, runtime, context, calls, _state = (
        _setup_support_runtime(tmp_path)
    )
    target = runtime.materialize_evidence_target_set(
        subject,
        ["evidence:C1"],
        "target-set-v1",
    )
    outcome = runtime.evaluate_support_candidate(
        subject,
        target,
        "support-contract-v1",
        context,
    )
    assert outcome.existing_record is None
    assert outcome.candidate is not None
    record = runtime.commit_support(outcome.candidate, context)

    assert record.relation is SupportRelation.SUPPORTED
    assert runtime.resolve_support(record.support_ref) == record
    assert runtime.resolve_support_by_identity(
        subject,
        target,
        "support-contract-v1",
    ) == record
    assert calls["evaluator"] == 1


def test_existing_support_is_resolved_before_fresh_cognition(tmp_path) -> None:
    _env, _claim_runtime, _claim, subject, runtime, context, calls, state = (
        _setup_support_runtime(tmp_path)
    )
    target = runtime.materialize_evidence_target_set(
        subject,
        ["evidence:C1"],
        "target-set-v1",
    )
    first_outcome = runtime.evaluate_support_candidate(
        subject,
        target,
        "support-contract-v1",
        context,
    )
    first = runtime.commit_support(first_outcome.candidate, context)
    state["technical_failure"] = True

    replay = runtime.evaluate_support_candidate(
        subject,
        target,
        "support-contract-v1",
        context,
    )
    assert replay.existing_record == first
    assert replay.candidate is None
    assert calls["evaluator"] == 1


def test_same_support_identity_different_semantics_fails_closed(tmp_path) -> None:
    _env, _claim_runtime, _claim, subject, runtime, context, _calls, _state = (
        _setup_support_runtime(tmp_path)
    )
    target = runtime.materialize_evidence_target_set(
        subject,
        ["evidence:C1"],
        "target-set-v1",
    )
    first = ClaimEvidenceSupportCandidateSchema(
        claim_support_subject_ref=subject,
        evidence_target_set_ref=target,
        support_semantic_contract_version="support-contract-v1",
        relation=SupportRelation.SUPPORTED,
        supporting_refs=("evidence:C1",),
        evaluation_occurrence_ref="support-eval://supported",
        evaluator_policy_id="shared-support-evaluator",
        evaluator_policy_version="v1",
    )
    runtime.commit_support(first, context)

    conflict = ClaimEvidenceSupportCandidateSchema(
        claim_support_subject_ref=subject,
        evidence_target_set_ref=target,
        support_semantic_contract_version="support-contract-v1",
        relation=SupportRelation.CONTRADICTED,
        contradicting_refs=("evidence:C1",),
        evaluation_occurrence_ref="support-eval://contradicted",
        evaluator_policy_id="shared-support-evaluator",
        evaluator_policy_version="v1",
    )
    with pytest.raises(ClaimSupportIdentityCollision, match="different semantic payload"):
        runtime.commit_support(conflict, context)


def test_different_exact_evidence_target_sets_may_have_distinct_support_facts(
    tmp_path,
) -> None:
    _env, _claim_runtime, _claim, subject, runtime, context, _calls, _state = (
        _setup_support_runtime(tmp_path)
    )
    target1 = runtime.materialize_evidence_target_set(
        subject,
        ["evidence:C1"],
        "target-set-v1",
    )
    target2 = runtime.materialize_evidence_target_set(
        subject,
        ["evidence:C2"],
        "target-set-v1",
    )
    first = runtime.commit_support(
        ClaimEvidenceSupportCandidateSchema(
            claim_support_subject_ref=subject,
            evidence_target_set_ref=target1,
            support_semantic_contract_version="support-contract-v1",
            relation=SupportRelation.SUPPORTED,
            supporting_refs=("evidence:C1",),
            evaluation_occurrence_ref="support-eval://target1",
            evaluator_policy_id="shared-support-evaluator",
            evaluator_policy_version="v1",
        ),
        context,
    )
    second = runtime.commit_support(
        ClaimEvidenceSupportCandidateSchema(
            claim_support_subject_ref=subject,
            evidence_target_set_ref=target2,
            support_semantic_contract_version="support-contract-v1",
            relation=SupportRelation.NOT_SUPPORTED,
            evaluation_occurrence_ref="support-eval://target2",
            evaluator_policy_id="shared-support-evaluator",
            evaluator_policy_version="v1",
        ),
        context,
    )
    assert first.support_ref != second.support_ref
    assert first.claim_support_subject_ref == second.claim_support_subject_ref


def test_technical_failure_creates_zero_canonical_support_truth(tmp_path) -> None:
    _env, _claim_runtime, _claim, subject, runtime, context, calls, state = (
        _setup_support_runtime(tmp_path)
    )
    target = runtime.materialize_evidence_target_set(
        subject,
        ["evidence:C1"],
        "target-set-v1",
    )
    state["technical_failure"] = True

    with pytest.raises(ClaimSupportTechnicalFailure):
        runtime.evaluate_support_candidate(
            subject,
            target,
            "support-contract-v1",
            context,
        )
    assert runtime.resolve_support_by_identity(
        subject,
        target,
        "support-contract-v1",
    ) is None
    assert calls["evaluator"] == 1


def test_invalid_evidence_membership_is_protocol_failure_and_no_commit(tmp_path) -> None:
    _env, claim_runtime, claim, subject, _runtime, context, _calls, _state = (
        _setup_support_runtime(tmp_path)
    )

    def _target_validator(target, context_arg) -> None:
        claim_runtime.validate_operation_context(context_arg)

    def _bad_evaluator(subject_arg, target, contract, context_arg):
        del context_arg
        return ClaimEvidenceSupportCandidateSchema(
            claim_support_subject_ref=subject_arg,
            evidence_target_set_ref=target,
            support_semantic_contract_version=contract,
            relation=SupportRelation.SUPPORTED,
            supporting_refs=("evidence:OUTSIDE",),
            evaluation_occurrence_ref="support-eval://bad-membership",
            evaluator_policy_id="shared-support-evaluator",
            evaluator_policy_version="v1",
        )

    runtime = ClaimSupportRuntime(
        claim_runtime=claim_runtime,
        evidence_target_validator=_target_validator,
        support_evaluator=_bad_evaluator,
        ledger_path=tmp_path / "bad-membership-support.jsonl",
    )
    target = runtime.materialize_evidence_target_set(
        subject,
        ["evidence:C1"],
        "target-set-v1",
    )
    with pytest.raises(ClaimSupportTechnicalFailure, match="invalid canonical candidate"):
        runtime.evaluate_support_candidate(
            subject,
            target,
            "support-contract-v1",
            context,
        )
    assert runtime.resolve_support_by_identity(
        subject,
        target,
        "support-contract-v1",
    ) is None


def test_empty_evidence_target_can_semantically_commit_not_supported(tmp_path) -> None:
    _env, _claim_runtime, _claim, subject, runtime, context, _calls, state = (
        _setup_support_runtime(tmp_path)
    )
    target = runtime.materialize_evidence_target_set(
        subject,
        [],
        "target-set-v1",
    )
    assert target.evidence_member_refs == ()
    state["relation"] = SupportRelation.NOT_SUPPORTED
    state["supporting_refs"] = ()
    state["contradicting_refs"] = ()

    outcome = runtime.evaluate_support_candidate(
        subject,
        target,
        "support-contract-v1",
        context,
    )
    record = runtime.commit_support(outcome.candidate, context)
    assert record.relation is SupportRelation.NOT_SUPPORTED


def test_technical_failure_then_legal_retry_success_creates_exactly_one_fact(
    tmp_path,
) -> None:
    _env, _claim_runtime, _claim, subject, runtime, context, calls, state = (
        _setup_support_runtime(tmp_path)
    )
    target = runtime.materialize_evidence_target_set(
        subject,
        ["evidence:C1"],
        "target-set-v1",
    )
    state["technical_failure"] = True
    with pytest.raises(ClaimSupportTechnicalFailure):
        runtime.evaluate_support_candidate(
            subject,
            target,
            "support-contract-v1",
            context,
        )
    assert runtime.resolve_support_by_identity(
        subject,
        target,
        "support-contract-v1",
    ) is None

    state["technical_failure"] = False
    outcome = runtime.evaluate_support_candidate(
        subject,
        target,
        "support-contract-v1",
        context,
    )
    record = runtime.commit_support(outcome.candidate, context)
    replay = runtime.evaluate_support_candidate(
        subject,
        target,
        "support-contract-v1",
        context,
    )
    assert replay.existing_record == record
    assert calls["evaluator"] == 2


def test_foreign_subject_without_explicit_validator_fails_closed(tmp_path) -> None:
    _env, _claim_runtime, _claim, _subject, runtime, context, _calls, _state = (
        _setup_support_runtime(tmp_path)
    )
    foreign = ClaimSupportSubjectRefSchema(
        authority_kind=ClaimSupportAuthorityKind.POST_WRITE_FOREIGN_LOCAL,
        subject_ref="post-write-foreign://claim/1",
    )
    target = runtime.materialize_evidence_target_set(
        foreign,
        ["evidence:C1"],
        "target-set-v1",
    )
    with pytest.raises(ClaimSupportCurrentnessError, match="no authorized validator"):
        runtime.evaluate_support_candidate(
            foreign,
            target,
            "support-contract-v1",
            context,
        )
