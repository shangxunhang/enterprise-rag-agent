from __future__ import annotations

from datetime import datetime, timezone

import pytest

from apps.enterprise_document.schemas.agent_rag_v1 import (
    CanonicalBudgetVectorSchema,
    ExactRepairBasisRefSchema,
    ParagraphCandidateSchema,
    RepairIssueBindingSchema,
    SectionPlanSourceRefSchema,
)
from apps.enterprise_document.schemas.agent_rag_v1.verification import (
    VerificationVerdict,
    VerifierIssueLifecycleStatus,
    VerifierSemanticIssueCandidateSchema,
)
from apps.enterprise_document.services.agent_rag_v1 import (
    BudgetManager,
    CanonicalBudgetCapacityExceeded,
)
from apps.enterprise_document.services.agent_rag_v1.repair_authorization_runtime import (
    ParagraphRepairAuthorizationCurrentnessError,
    ParagraphRepairAuthorizationIntegrityError,
    ParagraphRepairAuthorizationRuntime,
    ParagraphRepairAuthorizationStore,
)
from core.hashing import canonical_sha256
from core.runtime.side_effects import CommittedResultResolutionStatus

from backend.tests.test_multi_agent_v3_claim_verification_runtime import (
    _context,
    _setup_verification_chain,
    _verification_context,
    _verifier_candidate,
)
from backend.tests.test_multi_agent_v3_paragraph_runtime import (
    _append,
    _candidate as _paragraph_candidate,
    _commit_promote,
)
from backend.tests.test_multi_agent_v3_research_runtime import (
    RUN_ID,
    SECTION_ID,
    TASK_ID,
)


NOW = datetime(2026, 9, 5, 12, 0, tzinfo=timezone.utc)


def _repair_fixture(tmp_path, *, repair_limit: int = 2):
    (
        env,
        claim_runtime,
        claim,
        binding,
        _support_runtime,
        support,
        _verification_state,
    ) = _setup_verification_chain(tmp_path / "verification")

    issue_candidate = VerifierSemanticIssueCandidateSchema(
        issue_slot="repair-issue:1",
        issue_code="SEMANTIC_REPAIR_REQUIRED",
        severity="major",
        semantic_category="semantic_conflict",
        message_hash=canonical_sha256("semantic repair required"),
        rationale_ref="trace://repair/issue-rationale",
        source_refs=("evidence:C1",),
        material_source_scope_key=f"section:{SECTION_ID}/evidence:C1",
    )
    verifier_candidate = _verifier_candidate(
        claim,
        binding,
        support,
        verdict=VerificationVerdict.CONFLICTED,
        verification_occurrence_ref="verification://repair-root",
        issue_candidates=(issue_candidate,),
        rationale="semantic repair needed",
    )
    committed = claim_runtime.commit_verification(
        claim.claim_ref,
        binding.binding_facts_ref,
        support.support_ref,
        verifier_candidate,
        None,
        _verification_context(env, verifier_candidate.verification_occurrence_ref),
    )
    issue = committed.issue_records[0]
    issue_head = committed.issue_heads[0]

    paragraph_runtime = claim_runtime.paragraph_runtime
    manifest_ref = paragraph_runtime.manifest_ref(
        run_id=RUN_ID,
        section_id=SECTION_ID,
        active_lineage=env["lineage"],
        write_instance_id=env["lineage"].attempt_id,
    )
    manifest = paragraph_runtime.load_manifest(manifest_ref)
    assert claim.paragraph_version_ref in manifest.members

    budget = BudgetManager.canonical(
        task_id=TASK_ID,
        run_id=RUN_ID,
        limits=CanonicalBudgetVectorSchema(repair_attempts=repair_limit),
        max_elapsed_seconds=3600,
        storage_path=tmp_path / "repair-budget.jsonl",
        policy_ref="budget-policy://repair-tests",
        policy_version="v1",
        clock=lambda: NOW,
    )
    store = ParagraphRepairAuthorizationStore(
        tmp_path / "paragraph-repair-authorizations.jsonl"
    )
    runtime = ParagraphRepairAuthorizationRuntime(
        lifecycle_service=env["lifecycle"],
        paragraph_runtime=paragraph_runtime,
        verification_runtime=claim_runtime,
        budget_manager=budget,
        store=store,
    )
    paragraph_runtime.authorization_validator = runtime.paragraph_authorization_validator

    issue_binding = RepairIssueBindingSchema(
        issue_ref=issue.issue_ref,
        expected_issue_head_revision=issue_head.issue_revision,
    )
    verification_ref = committed.verification_result.verification_result_ref
    section_plan_ref = SectionPlanSourceRefSchema(
        plan_version_ref=env["plan_version"],
        section_id=SECTION_ID,
    )
    semantic_basis = (
        ExactRepairBasisRefSchema(
            authority_kind="POST_WRITE_CONTROL_DECISION",
            exact_ref="post-write-control://repair-test/1",
            exact_hash="c" * 64,
        ),
    )

    return {
        "env": env,
        "claim_runtime": claim_runtime,
        "claim": claim,
        "paragraph_runtime": paragraph_runtime,
        "manifest_ref": manifest_ref,
        "budget": budget,
        "store": store,
        "runtime": runtime,
        "issue": issue,
        "issue_head": issue_head,
        "issue_binding": issue_binding,
        "verification_ref": verification_ref,
        "section_plan_ref": section_plan_ref,
        "semantic_basis": semantic_basis,
    }


def _authorize(fx, *, repair_policy_version: str = "v1"):
    return fx["runtime"].authorize(
        task_id=TASK_ID,
        run_id=RUN_ID,
        section_id=SECTION_ID,
        paragraph_logical_ref=fx["claim"].paragraph_version_ref.logical_ref,
        manifest_ref=fx["manifest_ref"],
        issue_bindings=(fx["issue_binding"],),
        verification_result_refs=(fx["verification_ref"],),
        semantic_repair_basis_refs=fx["semantic_basis"],
        section_plan_ref=fx["section_plan_ref"],
        fact_snapshot_ref=fx["env"]["fact_binding"].version_ref,
        evidence_package_ref=fx["env"]["package_version"],
        repair_policy_id="paragraph-repair-policy",
        repair_policy_version=repair_policy_version,
    )


def test_authorization_is_replay_stable_and_reserves_exactly_one_repair_attempt(
    tmp_path,
) -> None:
    fx = _repair_fixture(tmp_path, repair_limit=2)

    first = _authorize(fx)
    state_after_first = fx["budget"].snapshot()
    second = _authorize(fx)
    state_after_replay = fx["budget"].snapshot()

    assert first == second
    assert state_after_first.revision == state_after_replay.revision
    assert state_after_replay.used.repair_attempts == 0
    assert state_after_replay.active_reserved.repair_attempts == 1
    assert state_after_replay.remaining.repair_attempts == 1
    assert len(state_after_replay.reservations) == 1

    record = fx["store"].resolve_exact(first)
    resolution = fx["store"].resolve_committed_result(
        record.logical_operation_ref.to_runtime_ref(),
        record.semantic_fingerprint,
    )
    assert resolution.resolution is CommittedResultResolutionStatus.COMMITTED_SAME
    assert resolution.committed_result_refs == (record.repair_authorization_id,)


def test_pre_activation_release_consumes_zero_and_is_idempotent(tmp_path) -> None:
    fx = _repair_fixture(tmp_path, repair_limit=1)
    ref = _authorize(fx)

    first = fx["runtime"].release_unactivated(ref)
    second = fx["runtime"].release_unactivated(ref)
    state = fx["budget"].snapshot()

    assert first.status == "released"
    assert second.status == "released"
    assert first.actual.repair_attempts == 0
    assert state.used.repair_attempts == 0
    assert state.active_reserved.repair_attempts == 0
    assert state.remaining.repair_attempts == 1

    with pytest.raises(
        ParagraphRepairAuthorizationCurrentnessError,
        match="active admission",
    ):
        fx["runtime"].validate_for_dispatch(ref)


def test_semantic_activation_consumes_one_before_paragraph_revise_commit(
    tmp_path,
) -> None:
    fx = _repair_fixture(tmp_path, repair_limit=2)
    ref = _authorize(fx)
    record = fx["runtime"].validate_for_dispatch(ref)

    operation = fx["paragraph_runtime"].revise_operation(
        run_id=RUN_ID,
        section_id=SECTION_ID,
        write_instance_id=fx["env"]["lineage"].attempt_id,
        target_logical_ref=record.target.paragraph_logical_ref,
        base_version_ref=record.target.base_paragraph_version_ref,
        authorization_ref=record.repair_authorization_id,
    )
    base_content = fx["paragraph_runtime"].read_paragraph(
        record.target.base_paragraph_version_ref
    )
    candidate = ParagraphCandidateSchema(
        content_contract_version=base_content.content_contract_version,
        operation_ref=operation,
        source_binding=base_content.source_binding,
        role=base_content.role,
        text="The approved capacity remains 100 MW after semantic repair.",
    )

    with pytest.raises(
        ParagraphRepairAuthorizationCurrentnessError,
        match="activated/consumed",
    ):
        fx["paragraph_runtime"].commit_paragraph_version(
            candidate=candidate,
            active_lineage=fx["env"]["lineage"],
        )

    settlement = fx["runtime"].mark_semantic_repair_activated(ref)
    assert settlement.status == "consumed"
    assert settlement.actual.repair_attempts == 1

    version = fx["paragraph_runtime"].commit_paragraph_version(
        candidate=candidate,
        active_lineage=fx["env"]["lineage"],
    )
    assert version.logical_ref == record.target.paragraph_logical_ref

    state = fx["budget"].snapshot()
    assert state.used.repair_attempts == 1
    assert state.active_reserved.repair_attempts == 0
    assert state.remaining.repair_attempts == 1

    with pytest.raises(ParagraphRepairAuthorizationIntegrityError):
        fx["runtime"].release_unactivated(ref)


def test_semantic_activation_is_idempotent_and_failure_after_activation_keeps_usage(
    tmp_path,
) -> None:
    fx = _repair_fixture(tmp_path, repair_limit=1)
    ref = _authorize(fx)

    first = fx["runtime"].mark_semantic_repair_activated(ref)
    second = fx["runtime"].mark_semantic_repair_activated(ref)
    state = fx["budget"].snapshot()

    assert first.status == second.status == "consumed"
    assert first.actual.repair_attempts == second.actual.repair_attempts == 1
    assert state.used.repair_attempts == 1
    assert state.remaining.repair_attempts == 0


def test_resolved_issue_invalidates_dispatch_authorization(tmp_path) -> None:
    fx = _repair_fixture(tmp_path, repair_limit=1)
    ref = _authorize(fx)

    fx["claim_runtime"].transition_issue_lifecycle(
        fx["issue"].issue_ref,
        expected_head_revision=fx["issue_head"].issue_revision,
        expected_lifecycle_status=VerifierIssueLifecycleStatus.OPEN,
        target_status=VerifierIssueLifecycleStatus.RESOLVED,
        reason_ref="repair-test://issue-resolved",
        operation_context=_context(
            fx["env"],
            authorization_ref="repair-test://issue-transition",
            purpose="verifier-issue-lifecycle",
        ),
        resolution_ref="repair-test://resolution",
    )

    with pytest.raises(
        ParagraphRepairAuthorizationCurrentnessError,
        match="OPEN revision",
    ):
        fx["runtime"].validate_for_dispatch(ref)

    state = fx["budget"].snapshot()
    assert state.active_reserved.repair_attempts == 1
    assert state.used.repair_attempts == 0


def test_manifest_revision_change_invalidates_dispatch_authorization(tmp_path) -> None:
    fx = _repair_fixture(tmp_path, repair_limit=1)
    ref = _authorize(fx)
    manifest = fx["paragraph_runtime"].load_manifest(fx["manifest_ref"])

    extra_candidate = _paragraph_candidate(
        fx["env"],
        fx["paragraph_runtime"],
        slot="repair-stale-manifest:extra-paragraph",
        text="Additional paragraph after repair authorization.",
    )
    extra_version, _ = _commit_promote(
        fx["env"],
        fx["paragraph_runtime"],
        extra_candidate,
    )
    _append(
        fx["env"],
        fx["paragraph_runtime"],
        fx["manifest_ref"],
        expected_members=manifest.members,
        resulting_members=(*manifest.members, extra_version),
        revision=manifest.manifest_revision,
        authorization_ref=extra_candidate.operation_ref.authorization_ref,
        slot="repair-stale-manifest:append",
    )

    with pytest.raises(
        ParagraphRepairAuthorizationCurrentnessError,
        match="Paragraph/Manifest is stale",
    ):
        fx["runtime"].validate_for_dispatch(ref)


def test_exhausted_sif12_budget_rejects_distinct_second_repair_occurrence(
    tmp_path,
) -> None:
    fx = _repair_fixture(tmp_path, repair_limit=1)
    first = _authorize(fx, repair_policy_version="v1")
    fx["runtime"].mark_semantic_repair_activated(first)

    with pytest.raises(CanonicalBudgetCapacityExceeded):
        _authorize(fx, repair_policy_version="v2")

    state = fx["budget"].snapshot()
    assert state.used.repair_attempts == 1
    assert state.remaining.repair_attempts == 0
    assert len(state.reservations) == 1


def test_authorization_record_binds_exact_budget_and_sif02_identity(tmp_path) -> None:
    fx = _repair_fixture(tmp_path, repair_limit=2)
    ref = _authorize(fx)
    record = fx["store"].resolve_exact(ref)
    reservation = fx["budget"].resolve_reservation(
        record.budget_reservation_ref.reservation_id
    )

    assert record.repair_authorization_id == (
        "paragraph-repair-auth://sha256/"
        + canonical_sha256(
            {
                "contract": record.contract_version,
                "logical_operation_ref": record.logical_operation_ref.model_dump(
                    mode="json"
                ),
            }
        )
    )
    assert reservation.reservation_ref == record.budget_reservation_ref
    assert reservation.reservation_ref.owner_occurrence_ref == (
        record.repair_authorization_id
    )
    assert reservation.reservation_ref.operation_ref == (
        record.logical_operation_ref.logical_operation_id
    )
    assert reservation.requested == CanonicalBudgetVectorSchema(repair_attempts=1)
