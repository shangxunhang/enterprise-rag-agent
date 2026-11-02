from __future__ import annotations

import hashlib

import pytest

from apps.enterprise_document.schemas.agent_rag_v1.claims import (
    ArtifactClaimCoverageUnitSchema,
    ClaimCandidateSchema,
    ClaimCoverageAccountingSchema,
    ClaimCoverageAccountingState,
    ClaimExtractionOrigin,
    ClaimTextSpanSchema,
)
from apps.enterprise_document.schemas.agent_rag_v1.claim_support import (
    ClaimEvidenceSupportCandidateSchema,
    ClaimSupportAuthorityKind,
    ClaimSupportSubjectRefSchema,
    SupportRelation,
)
from apps.enterprise_document.schemas.agent_rag_v1.enums import ClaimLifecycleStatus
from apps.enterprise_document.schemas.agent_rag_v1.verification import (
    ClaimAuthorizedSourceRefSchema,
    ClaimBindingFactSchema,
    ClaimBindingFactsCandidateSchema,
    ClaimBindingRelation,
    ClaimBindingSupportKind,
    ClaimOperationContextSchema,
    VerificationVerdict,
    VerifierIssueLifecycleStatus,
    VerifierSemanticIssueCandidateSchema,
    VerifierSemanticResultCandidateSchema,
)
from apps.enterprise_document.services.agent_rag_v1.claim_pipeline import (
    ClaimBindingResolver,
    TargetClaimMaterializer,
)
from apps.enterprise_document.services.agent_rag_v1.claim_support_runtime import (
    ClaimSupportRuntime,
)
from apps.enterprise_document.services.agent_rag_v1.claim_verification_runtime import (
    ClaimBindingError,
    ClaimCoverageError,
    ClaimCurrentnessError,
    ClaimHeadConflict,
    ClaimIdentityCollision,
    ClaimVerificationRuntime,
    VerifierIssueHeadConflict,
)
from core.hashing import canonical_sha256
from schemas.claim import ClaimType
from backend.tests.test_trusted_evidence_v1_eligibility_contract import (
    _assessment as _eligibility_assessment,
    _completed_bundle as _eligibility_bundle,
)

from backend.tests.test_multi_agent_v3_paragraph_runtime import (
    _append,
    _candidate as _paragraph_candidate,
    _commit_promote,
    _manifest_ref,
    _runtime as _paragraph_runtime,
)
from backend.tests.test_multi_agent_v3_research_runtime import RUN_ID, SECTION_ID


_V2_COMPOSERS = {}


def _setup_claim_runtime(tmp_path):
    env, paragraph_runtime, *_ = _paragraph_runtime(tmp_path / "paragraph")
    paragraph_candidate = _paragraph_candidate(
        env,
        paragraph_runtime,
        slot="claim-source:paragraph-1",
        text="The approved capacity is 100 MW.",
    )
    paragraph_version, _ = _commit_promote(
        env,
        paragraph_runtime,
        paragraph_candidate,
    )
    manifest_ref = _manifest_ref(env, paragraph_runtime)
    _append(
        env,
        paragraph_runtime,
        manifest_ref,
        expected_members=(),
        resulting_members=(paragraph_version,),
        revision=0,
        authorization_ref=paragraph_candidate.operation_ref.authorization_ref,
        slot="claim-source:manifest-append",
    )

    source_checks: list[str] = []
    source_state = {"current": True}

    def _binding_source_validator(candidate, lineage) -> None:
        if not source_state["current"]:
            raise ValueError("source stale")
        assert lineage.attempt_id == env["lineage"].attempt_id
        source_checks.extend(
            item.exact_source_ref
            for item in candidate.exact_authorized_source_refs
        )

    runtime = ClaimVerificationRuntime(
        paragraph_runtime=paragraph_runtime,
        binding_source_validator=_binding_source_validator,
        ledger_path=tmp_path / "claim-verification.jsonl",
    )
    return (
        env,
        paragraph_runtime,
        runtime,
        paragraph_version,
        manifest_ref,
        source_checks,
        source_state,
    )


def _context(env, *, authorization_ref="claim-extraction://deterministic-1", purpose="claim-records"):
    return ClaimOperationContextSchema(
        active_lineage=env["lineage"],
        authorization_ref=authorization_ref,
        operation_purpose=purpose,
    )


def _claim_candidate(
    runtime: ClaimVerificationRuntime,
    paragraph_version,
    *,
    normalized_value=100,
    extraction_occurrence_ref="claim-extraction://deterministic-1",
):
    text = "The approved capacity is 100 MW."
    span_hash = hashlib.sha256(text.encode("utf-8")).hexdigest()
    value_hash = canonical_sha256(normalized_value)
    occurrence = runtime.derive_claim_occurrence_ref(
        run_id=RUN_ID,
        section_id=SECTION_ID,
        paragraph_version_ref=paragraph_version,
        claim_type="capacity",
        start_offset=0,
        end_offset=len(text),
        text_span_hash=span_hash,
        span_locator_policy_id="exact-character-offset",
        span_locator_policy_version="v1",
        normalized_value_hash=value_hash,
        normalization_policy_id="numeric-canonical",
        normalization_policy_version="v1",
        claim_identity_policy_id="claim-occurrence",
        claim_identity_policy_version="v1",
    )
    return ClaimCandidateSchema(
        claim_occurrence_ref=occurrence,
        paragraph_version_ref=paragraph_version,
        section_id=SECTION_ID,
        claim_type="capacity",
        text_span=ClaimTextSpanSchema(
            start_offset=0,
            end_offset=len(text),
            text=text,
        ),
        text_span_hash=span_hash,
        normalized_value=normalized_value,
        normalized_value_hash=value_hash,
        unit="MW",
        extraction_origin=ClaimExtractionOrigin.DETERMINISTIC_EXTRACTOR,
        extraction_occurrence_ref=extraction_occurrence_ref,
    )


def _coverage(
    paragraph_version,
    candidate: ClaimCandidateSchema | None,
    *,
    end_offset: int | None = None,
    state: ClaimCoverageAccountingState | None = None,
):
    text = "The approved capacity is 100 MW."
    final_state = state or (
        ClaimCoverageAccountingState.FACTUAL_CLAIMS_MATERIALIZED
        if candidate is not None
        else ClaimCoverageAccountingState.NO_APPLICABLE_FACTUAL_CLAIM
    )
    return ClaimCoverageAccountingSchema(
        coverage_scope_ref=f"section:{SECTION_ID}",
        paragraph_version_refs=(paragraph_version,),
        coverage_units=(
            ArtifactClaimCoverageUnitSchema(
                paragraph_version_ref=paragraph_version,
                coverage_unit_id="coverage-unit:paragraph-1",
                start_offset=0,
                end_offset=end_offset if end_offset is not None else len(text),
                accounting_state=final_state,
                claim_occurrence_refs=(
                    (candidate.claim_occurrence_ref,)
                    if candidate is not None
                    else ()
                ),
            ),
        ),
        accounting_state=final_state,
        extraction_occurrence_ref=(
            candidate.extraction_occurrence_ref
            if candidate is not None
            else "claim-extraction://deterministic-none"
        ),
        coverage_semantic_contract_version="claim-coverage-v1",
    )


def _commit_claim(tmp_path):
    (
        env,
        paragraph_runtime,
        runtime,
        paragraph_version,
        manifest_ref,
        source_checks,
        source_state,
    ) = _setup_claim_runtime(tmp_path)
    candidate = _claim_candidate(runtime, paragraph_version)
    accounting = _coverage(paragraph_version, candidate)
    result = runtime.commit_claim_records(
        (candidate,),
        accounting,
        _context(env),
    )
    return (
        env,
        paragraph_runtime,
        runtime,
        paragraph_version,
        manifest_ref,
        candidate,
        result,
        source_checks,
        source_state,
    )


def _binding_candidate(runtime, claim_record, *, source_ref="evidence:C1"):
    sources = (
        ClaimAuthorizedSourceRefSchema(
            source_kind="EVIDENCE",
            exact_source_ref=source_ref,
            source_scope_ref=f"section:{SECTION_ID}",
        ),
    )
    facts = (
        ClaimBindingFactSchema(
            source_kind="EVIDENCE",
            exact_source_ref=source_ref,
            support_kind=ClaimBindingSupportKind.DIRECT_SUPPORT,
            relation=ClaimBindingRelation.SUPPORTS,
            source_currentness_proof_ref="source-currentness://proof-1",
            source_scope_ref=f"section:{SECTION_ID}",
        ),
    )
    source_hash = canonical_sha256(
        [item.model_dump(mode="json") for item in sources]
    )
    base = dict(
        claim_ref=claim_record.claim_ref,
        claim_occurrence_ref=claim_record.claim_occurrence_ref,
        paragraph_version_ref=claim_record.paragraph_version_ref,
        extraction_occurrence_ref=claim_record.extraction_occurrence_ref,
        exact_authorized_source_refs=sources,
        facts=facts,
        source_set_hash=source_hash,
        resolver_policy_id="eligibility-authorized-binding",
        resolver_policy_version="v1",
        binding_purpose="claim-verification",
        candidate_semantic_fingerprint="0" * 64,
    )
    provisional = ClaimBindingFactsCandidateSchema(**base)
    return provisional.model_copy(
        update={
            "candidate_semantic_fingerprint": (
                runtime.binding_candidate_semantic_fingerprint(provisional)
            )
        }
    )


def test_claim_record_exists_before_support_or_verification(tmp_path) -> None:
    env, _paragraph, runtime, paragraph_version, _manifest, candidate, result, *_ = (
        _commit_claim(tmp_path)
    )
    assert len(result.records) == 1
    record = result.records[0]
    assert record.paragraph_version_ref == paragraph_version
    assert record.claim_ref == runtime.derive_claim_ref(candidate.claim_occurrence_ref)
    dumped = record.model_dump(mode="json")
    assert "verification_status" not in dumped
    assert "lifecycle_status" not in dumped
    assert "source_bindings" not in dumped
    assert result.coverage_proof_ref.coverage_scope_ref == f"section:{SECTION_ID}"
    assert runtime.resolve_claim(record.claim_ref) == record
    assert runtime.resolve_claim_coverage(result.coverage_proof_ref) == (
        result.coverage_accounting
    )


def test_claim_record_commit_replay_is_exact_and_no_duplicate_truth(tmp_path) -> None:
    env, _paragraph, runtime, paragraph_version, _manifest, candidate, first, *_ = (
        _commit_claim(tmp_path)
    )
    replay = runtime.commit_claim_records(
        (candidate,),
        _coverage(paragraph_version, candidate),
        _context(env),
    )
    assert replay == first
    assert replay.records[0].claim_ref.claim_id == first.records[0].claim_ref.claim_id


def test_lossless_coverage_must_reach_exact_paragraph_end(tmp_path) -> None:
    env, _paragraph, runtime, paragraph_version, *_ = _setup_claim_runtime(tmp_path)
    text = "The approved capacity is 100 MW."
    incomplete = _coverage(
        paragraph_version,
        None,
        end_offset=len(text) - 1,
        state=ClaimCoverageAccountingState.NO_APPLICABLE_FACTUAL_CLAIM,
    )
    with pytest.raises(ClaimCoverageError, match="exact Paragraph end"):
        runtime.commit_claim_records(
            (),
            incomplete,
            _context(
                env,
                authorization_ref="claim-extraction://deterministic-none",
            ),
        )


def test_zero_claims_require_explicit_no_applicable_coverage(tmp_path) -> None:
    env, _paragraph, runtime, paragraph_version, *_ = _setup_claim_runtime(tmp_path)
    accounting = _coverage(
        paragraph_version,
        None,
        state=ClaimCoverageAccountingState.NO_APPLICABLE_FACTUAL_CLAIM,
    )
    result = runtime.commit_claim_records(
        (),
        accounting,
        _context(
            env,
            authorization_ref="claim-extraction://deterministic-none",
        ),
    )
    assert result.records == ()
    assert (
        runtime.resolve_claim_coverage(result.coverage_proof_ref).accounting_state
        is ClaimCoverageAccountingState.NO_APPLICABLE_FACTUAL_CLAIM
    )


def test_claim_span_and_normalized_hash_are_verified_against_exact_paragraph(
    tmp_path,
) -> None:
    env, _paragraph, runtime, paragraph_version, *_ = _setup_claim_runtime(tmp_path)
    candidate = _claim_candidate(runtime, paragraph_version)
    bad = candidate.model_copy(update={"normalized_value_hash": "f" * 64})
    with pytest.raises((ClaimIdentityCollision, ValueError)):
        runtime.commit_claim_records(
            (bad,),
            _coverage(paragraph_version, candidate),
            _context(env),
        )


def test_paragraph_v2_makes_v1_claim_candidate_ineligible_without_rebinding(
    tmp_path,
) -> None:
    (
        env,
        paragraph_runtime,
        runtime,
        v1,
        manifest_ref,
        candidate,
        first,
        *_,
    ) = _commit_claim(tmp_path)
    assert runtime.resolve_claim(first.records[0].claim_ref) == first.records[0]

    revise_op = paragraph_runtime.revise_operation(
        run_id=RUN_ID,
        section_id=SECTION_ID,
        write_instance_id=env["lineage"].attempt_id,
        target_logical_ref=v1.logical_ref,
        base_version_ref=v1,
        authorization_ref="paragraph-repair-authorization://claim-test",
    )
    from apps.enterprise_document.schemas.agent_rag_v1 import ParagraphCandidateSchema
    from backend.tests.test_multi_agent_v3_paragraph_runtime import _source_binding

    revised = ParagraphCandidateSchema(
        content_contract_version="paragraph-prose-v1",
        operation_ref=revise_op,
        source_binding=_source_binding(env),
        role="body",
        text="The approved capacity is 120 MW.",
    )
    v2 = paragraph_runtime.commit_paragraph_version(
        candidate=revised,
        active_lineage=env["lineage"],
    )
    paragraph_runtime.promote_paragraph_active(
        candidate=revised,
        target_version_ref=v2,
        expected_head_revision=1,
        expected_active_version_id=v1.artifact_version_id,
        active_lineage=env["lineage"],
    )
    current_manifest = paragraph_runtime.load_manifest(manifest_ref)
    replace_op = paragraph_runtime.manifest_operation(
        manifest_ref=manifest_ref,
        operation_kind="SECTION_PARAGRAPH_MANIFEST_UPDATE",
        authorization_ref=revise_op.authorization_ref,
        stable_slot_ref="claim-test:replace-v2",
    )
    paragraph_runtime.update_manifest(
        operation=replace_op,
        expected_manifest_revision=current_manifest.manifest_revision,
        expected_generation_membership_state=current_manifest.generation_membership_state,
        expected_members=current_manifest.members,
        resulting_members=(v2,),
        mutation_kind="REPLACE_VERSION",
        active_lineage=env["lineage"],
    )

    # Historical replay resolves the already committed Claim result even
    # though V1 is no longer current.
    replay = runtime.commit_claim_records(
        (candidate,),
        _coverage(v1, candidate),
        _context(env),
    )
    assert replay == first

    # A new extraction occurrence is a new logical operation and cannot create
    # another effect against stale Paragraph V1.
    stale_candidate = _claim_candidate(
        runtime,
        v1,
        extraction_occurrence_ref="claim-extraction://after-v2",
    )
    with pytest.raises(ClaimCurrentnessError, match="not exact current"):
        runtime.commit_claim_records(
            (stale_candidate,),
            _coverage(v1, stale_candidate),
            _context(
                env,
                authorization_ref="claim-extraction://after-v2",
            ),
        )
    assert runtime.resolve_claim(first.records[0].claim_ref) == first.records[0]


def test_binding_facts_commit_is_deterministic_and_source_currentness_checked(
    tmp_path,
) -> None:
    env, _paragraph, runtime, _version, _manifest, _candidate0, result, checks, *_ = (
        _commit_claim(tmp_path)
    )
    claim = result.records[0]
    candidate = _binding_candidate(runtime, claim)
    context = _context(
        env,
        authorization_ref="claim-binding://1",
        purpose="claim-binding",
    )
    first = runtime.commit_binding_facts(candidate, context)
    replay = runtime.commit_binding_facts(candidate, context)

    assert replay == first
    assert runtime.resolve_binding_facts(first.binding_facts_ref) == first
    assert checks.count("evidence:C1") >= 1
    assert first.claim_ref == claim.claim_ref
    assert first.source_set_hash == candidate.source_set_hash


def test_binding_source_set_hash_mismatch_fails_closed(tmp_path) -> None:
    env, _paragraph, runtime, _version, _manifest, _candidate0, result, *_ = (
        _commit_claim(tmp_path)
    )
    candidate = _binding_candidate(runtime, result.records[0])
    bad = candidate.model_copy(update={"source_set_hash": "e" * 64})
    with pytest.raises(ClaimBindingError, match="source_set_hash"):
        runtime.commit_binding_facts(
            bad,
            _context(
                env,
                authorization_ref="claim-binding://bad-hash",
                purpose="claim-binding",
            ),
        )


def test_binding_source_staleness_preserves_history_but_blocks_new_identity(
    tmp_path,
) -> None:
    env, _paragraph, runtime, _version, _manifest, _candidate0, result, _checks, source_state = (
        _commit_claim(tmp_path)
    )
    claim = result.records[0]
    original = _binding_candidate(runtime, claim, source_ref="evidence:C1")
    original_context = _context(
        env,
        authorization_ref="claim-binding://current",
        purpose="claim-binding-current",
    )
    first = runtime.commit_binding_facts(original, original_context)

    source_state["current"] = False

    # Historical exact BindingFacts remain resolvable/replayable.
    replay = runtime.commit_binding_facts(original, original_context)
    assert replay == first
    assert runtime.resolve_binding_facts(first.binding_facts_ref) == first

    # A genuinely new exact source set has no canonical result yet and must
    # revalidate currentness before first commit.
    new_identity = _binding_candidate(runtime, claim, source_ref="evidence:C2")
    with pytest.raises(ClaimCurrentnessError, match="source became stale"):
        runtime.commit_binding_facts(
            new_identity,
            _context(
                env,
                authorization_ref="claim-binding://stale-new-source",
                purpose="claim-binding-stale-new-source",
            ),
        )


def test_same_claim_can_have_new_binding_revision_for_new_exact_source_set(
    tmp_path,
) -> None:
    env, _paragraph, runtime, _version, _manifest, _candidate0, result, *_ = (
        _commit_claim(tmp_path)
    )
    claim = result.records[0]
    first = runtime.commit_binding_facts(
        _binding_candidate(runtime, claim, source_ref="evidence:C1"),
        _context(
            env,
            authorization_ref="claim-binding://source-c1",
            purpose="claim-binding-c1",
        ),
    )
    second = runtime.commit_binding_facts(
        _binding_candidate(runtime, claim, source_ref="evidence:C2"),
        _context(
            env,
            authorization_ref="claim-binding://source-c2",
            purpose="claim-binding-c2",
        ),
    )
    assert first.claim_ref == second.claim_ref
    assert first.binding_facts_ref != second.binding_facts_ref
    assert runtime.resolve_binding_facts(first.binding_facts_ref) == first
    assert runtime.resolve_binding_facts(second.binding_facts_ref) == second



def _setup_verification_chain(
    tmp_path,
    *,
    support_relation: SupportRelation = SupportRelation.SUPPORTED,
    binding_relation: ClaimBindingRelation = ClaimBindingRelation.SUPPORTS,
):
    (
        env,
        _paragraph_runtime0,
        claim_runtime,
        _paragraph_version,
        _manifest,
        _claim_candidate0,
        claim_commit,
        _source_checks,
        _source_state,
    ) = _commit_claim(tmp_path / "claim")
    claim_record = claim_commit.records[0]

    binding_candidate = _binding_candidate(claim_runtime, claim_record)
    if binding_relation is not ClaimBindingRelation.SUPPORTS:
        source = binding_candidate.exact_authorized_source_refs[0]
        fact = ClaimBindingFactSchema(
            source_kind=source.source_kind,
            exact_source_ref=source.exact_source_ref,
            support_kind=ClaimBindingSupportKind.AUTHORITATIVE,
            relation=binding_relation,
            source_currentness_proof_ref="source-currentness://verification-chain",
            source_scope_ref=source.source_scope_ref,
        )
        provisional = binding_candidate.model_copy(
            update={
                "facts": (fact,),
                "candidate_semantic_fingerprint": "0" * 64,
            }
        )
        binding_candidate = provisional.model_copy(
            update={
                "candidate_semantic_fingerprint": (
                    claim_runtime.binding_candidate_semantic_fingerprint(
                        provisional
                    )
                )
            }
        )
    binding_record = claim_runtime.commit_binding_facts(
        binding_candidate,
        _context(
            env,
            authorization_ref="claim-binding://verification-chain",
            purpose="claim-binding-verification",
        ),
    )

    support_subject = ClaimSupportSubjectRefSchema(
        authority_kind=ClaimSupportAuthorityKind.SIF07_CLAIM,
        subject_ref=claim_record.claim_ref.claim_id,
    )
    support_runtime = ClaimSupportRuntime(
        claim_runtime=claim_runtime,
        evidence_target_validator=lambda target, context: (
            claim_runtime.validate_operation_context(context)
        ),
        support_evaluator=lambda *args, **kwargs: (_ for _ in ()).throw(
            AssertionError("direct support commit test should not invoke evaluator")
        ),
        ledger_path=tmp_path / "claim-support.jsonl",
    )
    target = support_runtime.materialize_evidence_target_set(
        support_subject,
        ["evidence:C1"],
        "target-set-v1",
    )
    support_candidate = ClaimEvidenceSupportCandidateSchema(
        claim_support_subject_ref=support_subject,
        evidence_target_set_ref=target,
        support_semantic_contract_version="support-contract-v1",
        relation=support_relation,
        supporting_refs=(
            ("evidence:C1",)
            if support_relation in {SupportRelation.SUPPORTED, SupportRelation.PARTIALLY_SUPPORTED}
            else ()
        ),
        contradicting_refs=(
            ("evidence:C1",)
            if support_relation is SupportRelation.CONTRADICTED
            else ()
        ),
        evaluation_occurrence_ref="support-eval://verification-chain",
        evaluator_policy_id="shared-support-evaluator",
        evaluator_policy_version="v1",
    )
    support_record = support_runtime.commit_support(
        support_candidate,
        _context(
            env,
            authorization_ref="support-commit://verification-chain",
            purpose="claim-support-verification",
        ),
    )

    verification_state = {"current": True}

    def _verification_source_validator(binding, support, lineage) -> None:
        if not verification_state["current"]:
            raise ValueError("verification source stale")
        assert binding.claim_ref == claim_record.claim_ref
        assert support.claim_support_subject_ref.subject_ref == (
            claim_record.claim_ref.claim_id
        )
        assert lineage.attempt_id == env["lineage"].attempt_id

    claim_runtime.support_resolver = support_runtime.resolve_support
    claim_runtime.verification_source_validator = _verification_source_validator

    # Fresh physical Writer receipt: no production migration or synthetic authority.
    from types import SimpleNamespace
    from apps.enterprise_document.schemas.pre_write_writer import WriterGenerationInvocationSchema, WriterLogicalExecutionKey, WriterExecutionResultSchema, WriterExecutionStatus
    from apps.enterprise_document.schemas.scheme_writer.document import SchemeSectionSchema
    from apps.enterprise_document.schemas.project_input_schema import ProjectInputSchema
    from core.runtime.pre_write_execution_store import SQLitePreWriteExecutionStore
    from apps.enterprise_document.services.agent_rag_v1.claim_verifier_composition import TargetClaimVerifierComposition
    from backend.tests.test_trusted_evidence_v1_eligibility_contract import _pending_bundle
    source = claim_runtime.paragraph_runtime.read_paragraph(claim_record.paragraph_version_ref).source_binding
    key = WriterLogicalExecutionKey(writer_subject_ref=source.writer_subject_ref,
        write_instance_id=source.write_instance_id, route_decision_ref=source.route_decision_ref)
    project = ProjectInputSchema(task_id="test-task", task_type="document", user_query="Test exact project input", total_staff=12, generation_requirements={"required_sections": ["Test"]})
    invocation = WriterGenerationInvocationSchema(writer_subject_ref=key.writer_subject_ref,
        write_instance_id=key.write_instance_id, route_decision_ref=key.route_decision_ref,
        generation_class=source.generation_class, canonical_evidence_input={**_pending_bundle().model_dump(mode="json"), "evidence_bundle_sha256": canonical_sha256(_pending_bundle().model_dump(mode="json"))},
        unified_writer_input_snapshot={"project_input": project.model_dump(mode="json")})
    store = SQLitePreWriteExecutionStore(tmp_path / "writer.sqlite3")
    digest = canonical_sha256(invocation.model_dump(mode="json"))
    store.reserve_writer_execution(key, semantic_fingerprint=digest)
    store.mark_writer_dispatched(key, semantic_fingerprint=digest)
    store.persist_writer_result(key, semantic_fingerprint=digest, result=WriterExecutionResultSchema(
        logical_execution_key=key, status=WriterExecutionStatus.SUCCESS,
        artifact=SchemeSectionSchema(section_id=claim_record.section_id, section_title="Test", section_order=1,
            content="The approved capacity is 100 MW.", input={"writer_generation_invocation": invocation.model_dump(mode="json")}),
        generation_provenance={"generation_class": source.generation_class}))
    composer = TargetClaimVerifierComposition(claim_runtime=claim_runtime,
        support_composition=SimpleNamespace(sources=None, runtime=support_runtime),
        model_gateway=None, writer_execution_store=store)
    claim_runtime.verification_identity_validator = composer.validate_v2_identity
    env["verifier"] = composer
    _V2_COMPOSERS[claim_record.claim_ref.claim_id] = (composer, env)

    return (
        env,
        claim_runtime,
        claim_record,
        binding_record,
        support_runtime,
        support_record,
        verification_state,
    )


def _verifier_candidate(
    claim_record,
    binding_record,
    support_record,
    *,
    verdict: VerificationVerdict = VerificationVerdict.VERIFIED,
    verification_occurrence_ref: str = "verification://1",
    issue_candidates=(),
    rationale: str = "Verifier semantic rationale",
):
    composer, env = _V2_COMPOSERS[claim_record.claim_ref.claim_id]
    invocation, _ = composer.build_input(claim_record, binding_record, support_record,
        _verification_context(env, verification_occurrence_ref))
    return VerifierSemanticResultCandidateSchema(
        **invocation.model_dump(exclude={"schema_version"}),
        positive_basis_refs=("evidence:C1",) if verdict is VerificationVerdict.VERIFIED else (),
        recommended_verdict=verdict, semantic_rationale_hash=canonical_sha256(rationale),
        semantic_rationale_ref="trace://verifier/rationale-1", issue_candidates=tuple(issue_candidates),
        verification_occurrence_ref=verification_occurrence_ref,
    )


def _verification_context(env, occurrence: str):
    return _context(
        env,
        authorization_ref=occurrence,
        purpose="claim-verification",
    )


def test_verification_consumes_existing_claim_binding_and_support(tmp_path) -> None:
    (
        env,
        runtime,
        claim,
        binding,
        _support_runtime,
        support,
        _verification_state,
    ) = _setup_verification_chain(tmp_path)
    candidate = _verifier_candidate(claim, binding, support)

    committed = runtime.commit_verification(
        claim.claim_ref,
        binding.binding_facts_ref,
        support.support_ref,
        candidate,
        None,
        _verification_context(env, candidate.verification_occurrence_ref),
    )

    assert committed.verification_result.verdict is VerificationVerdict.VERIFIED
    assert committed.verification_revision.verification_revision == 1
    assert committed.resulting_claim_head.head_revision == 1
    assert committed.resulting_claim_head.lifecycle_status is ClaimLifecycleStatus.ACTIVE
    assert runtime.resolve_claim(claim.claim_ref) == claim
    assert runtime.resolve_binding_facts(binding.binding_facts_ref) == binding
    assert runtime.resolve_verification_result(
        committed.verification_result.verification_result_ref
    ) == committed.verification_result
    assert runtime.resolve_claim_head(claim.claim_ref) == (
        committed.resulting_claim_head
    )


def test_supported_relation_does_not_automatically_manufacture_verified(tmp_path) -> None:
    env, runtime, claim, binding, _support_runtime, support, _state = (
        _setup_verification_chain(tmp_path)
    )
    candidate = _verifier_candidate(
        claim,
        binding,
        support,
        verdict=VerificationVerdict.UNSUPPORTED,
        verification_occurrence_ref="verification://unsupported",
    )
    committed = runtime.commit_verification(
        claim.claim_ref,
        binding.binding_facts_ref,
        support.support_ref,
        candidate,
        None,
        _verification_context(env, candidate.verification_occurrence_ref),
    )
    assert committed.verification_result.verdict is VerificationVerdict.UNSUPPORTED
    view = runtime.resolve_active_verified_claim(claim.claim_ref)
    assert view.lifecycle_active is True
    assert view.verdict_verified is False
    assert view.active_verified_claim is False


@pytest.mark.parametrize(
    "support_relation",
    [SupportRelation.CONTRADICTED, SupportRelation.NOT_SUPPORTED],
)
def test_non_support_relation_cannot_commit_verified(
    tmp_path,
    support_relation,
) -> None:
    env, runtime, claim, binding, _support_runtime, support, _state = (
        _setup_verification_chain(
            tmp_path,
            support_relation=support_relation,
        )
    )
    candidate = _verifier_candidate(
        claim,
        binding,
        support,
        verdict=VerificationVerdict.VERIFIED,
        verification_occurrence_ref=f"verification://blocked-{support_relation.value}",
    )
    with pytest.raises(ClaimBindingError, match="shared Support|Support relation"):
        runtime.commit_verification(
            claim.claim_ref,
            binding.binding_facts_ref,
            support.support_ref,
            candidate,
            None,
            _verification_context(env, candidate.verification_occurrence_ref),
        )


def test_binding_contradiction_cannot_commit_verified_even_if_support_says_supported(
    tmp_path,
) -> None:
    env, runtime, claim, binding, _support_runtime, support, _state = (
        _setup_verification_chain(
            tmp_path,
            support_relation=SupportRelation.SUPPORTED,
            binding_relation=ClaimBindingRelation.CONTRADICTS,
        )
    )
    candidate = _verifier_candidate(
        claim,
        binding,
        support,
        verdict=VerificationVerdict.VERIFIED,
        verification_occurrence_ref="verification://binding-contradiction",
    )
    with pytest.raises(ClaimBindingError, match="contradiction"):
        runtime.commit_verification(
            claim.claim_ref,
            binding.binding_facts_ref,
            support.support_ref,
            candidate,
            None,
            _verification_context(env, candidate.verification_occurrence_ref),
        )


def test_claim_head_expected_revision_is_strict_cas(tmp_path) -> None:
    env, runtime, claim, binding, _support_runtime, support, _state = (
        _setup_verification_chain(tmp_path)
    )
    first_candidate = _verifier_candidate(
        claim,
        binding,
        support,
        verification_occurrence_ref="verification://cas-1",
    )
    first = runtime.commit_verification(
        claim.claim_ref,
        binding.binding_facts_ref,
        support.support_ref,
        first_candidate,
        None,
        _verification_context(env, first_candidate.verification_occurrence_ref),
    )

    second_candidate = _verifier_candidate(
        claim,
        binding,
        support,
        verification_occurrence_ref="verification://cas-2",
    )
    with pytest.raises(ClaimHeadConflict, match="does not match"):
        runtime.commit_verification(
            claim.claim_ref,
            binding.binding_facts_ref,
            support.support_ref,
            second_candidate,
            None,
            _verification_context(env, second_candidate.verification_occurrence_ref),
        )
    assert runtime.resolve_claim_head(claim.claim_ref) == (
        first.resulting_claim_head
    )


def test_reverification_same_semantics_gets_new_result_and_historical_replay_no_rollback(
    tmp_path,
) -> None:
    env, runtime, claim, binding, _support_runtime, support, _state = (
        _setup_verification_chain(tmp_path)
    )
    c1 = _verifier_candidate(
        claim,
        binding,
        support,
        verification_occurrence_ref="verification://history-1",
    )
    first = runtime.commit_verification(
        claim.claim_ref,
        binding.binding_facts_ref,
        support.support_ref,
        c1,
        None,
        _verification_context(env, c1.verification_occurrence_ref),
    )
    c2 = _verifier_candidate(
        claim,
        binding,
        support,
        verification_occurrence_ref="verification://history-2",
    )
    second = runtime.commit_verification(
        claim.claim_ref,
        binding.binding_facts_ref,
        support.support_ref,
        c2,
        first.resulting_claim_head,
        _verification_context(env, c2.verification_occurrence_ref),
    )

    assert second.verification_revision.verification_revision == 2
    assert second.resulting_claim_head.head_revision == 2
    assert (
        first.verification_result.verification_result_ref
        != second.verification_result.verification_result_ref
    )

    replay_first = runtime.commit_verification(
        claim.claim_ref,
        binding.binding_facts_ref,
        support.support_ref,
        c1,
        None,
        _verification_context(env, c1.verification_occurrence_ref),
    )
    assert replay_first == first
    assert runtime.resolve_claim_head(claim.claim_ref) == (
        second.resulting_claim_head
    )


def test_verifier_issue_is_committed_with_initial_open_head_and_transition_is_idempotent(
    tmp_path,
) -> None:
    env, runtime, claim, binding, _support_runtime, support, _state = (
        _setup_verification_chain(tmp_path)
    )
    issue_candidate = VerifierSemanticIssueCandidateSchema(
        issue_slot="issue-slot:1",
        issue_code="CLAIM_REVIEW_REQUIRED",
        severity="major",
        semantic_category="semantic_conflict",
        message_hash=canonical_sha256("review required"),
        rationale_ref="trace://verifier/issue-rationale",
        source_refs=("evidence:C1",),
        material_source_scope_key="section:s1/evidence:C1",
    )
    candidate = _verifier_candidate(
        claim,
        binding,
        support,
        verdict=VerificationVerdict.CONFLICTED,
        verification_occurrence_ref="verification://issue-1",
        issue_candidates=(issue_candidate,),
    )
    committed = runtime.commit_verification(
        claim.claim_ref,
        binding.binding_facts_ref,
        support.support_ref,
        candidate,
        None,
        _verification_context(env, candidate.verification_occurrence_ref),
    )
    assert len(committed.issue_records) == 1
    issue = committed.issue_records[0]
    head = committed.issue_heads[0]
    assert head.issue_revision == 1
    assert head.lifecycle_status is VerifierIssueLifecycleStatus.OPEN
    assert runtime.resolve_issue(issue.issue_ref) == issue
    assert runtime.resolve_issue_head(issue.issue_ref) == head

    context = _context(
        env,
        authorization_ref="issue-resolution://1",
        purpose="verifier-issue-lifecycle",
    )
    receipt = runtime.transition_issue_lifecycle(
        issue.issue_ref,
        expected_head_revision=1,
        expected_lifecycle_status=VerifierIssueLifecycleStatus.OPEN,
        target_status=VerifierIssueLifecycleStatus.RESOLVED,
        reason_ref="issue-resolution://reason-1",
        operation_context=context,
        resolution_ref="issue-resolution://artifact-1",
    )
    replay = runtime.transition_issue_lifecycle(
        issue.issue_ref,
        expected_head_revision=1,
        expected_lifecycle_status=VerifierIssueLifecycleStatus.OPEN,
        target_status=VerifierIssueLifecycleStatus.RESOLVED,
        reason_ref="issue-resolution://reason-1",
        operation_context=context,
        resolution_ref="issue-resolution://artifact-1",
    )
    assert replay == receipt
    assert runtime.resolve_issue_head(issue.issue_ref) == receipt.resulting_head
    assert receipt.resulting_head.lifecycle_status is VerifierIssueLifecycleStatus.RESOLVED


def test_stale_issue_head_transition_fails_without_mutation(tmp_path) -> None:
    env, runtime, claim, binding, _support_runtime, support, _state = (
        _setup_verification_chain(tmp_path)
    )
    issue_candidate = VerifierSemanticIssueCandidateSchema(
        issue_slot="issue-slot:stale",
        issue_code="STALE_TEST",
        severity="minor",
        semantic_category="test",
        message_hash=canonical_sha256("stale"),
        source_refs=("evidence:C1",),
    )
    candidate = _verifier_candidate(
        claim,
        binding,
        support,
        verdict=VerificationVerdict.CONFLICTED,
        verification_occurrence_ref="verification://issue-stale",
        issue_candidates=(issue_candidate,),
    )
    committed = runtime.commit_verification(
        claim.claim_ref,
        binding.binding_facts_ref,
        support.support_ref,
        candidate,
        None,
        _verification_context(env, candidate.verification_occurrence_ref),
    )
    issue = committed.issue_records[0]
    with pytest.raises(VerifierIssueHeadConflict, match="stale"):
        runtime.transition_issue_lifecycle(
            issue.issue_ref,
            expected_head_revision=2,
            expected_lifecycle_status=VerifierIssueLifecycleStatus.OPEN,
            target_status=VerifierIssueLifecycleStatus.INVALIDATED,
            reason_ref="issue-invalidate://stale",
            operation_context=_context(
                env,
                authorization_ref="issue-invalidate://stale",
                purpose="verifier-issue-lifecycle",
            ),
        )
    assert runtime.resolve_issue_head(issue.issue_ref).issue_revision == 1


def test_claim_lifecycle_transition_is_separate_from_verification_verdict(tmp_path) -> None:
    env, runtime, claim, binding, _support_runtime, support, _state = (
        _setup_verification_chain(tmp_path)
    )
    candidate = _verifier_candidate(
        claim,
        binding,
        support,
        verification_occurrence_ref="verification://lifecycle",
    )
    verified = runtime.commit_verification(
        claim.claim_ref,
        binding.binding_facts_ref,
        support.support_ref,
        candidate,
        None,
        _verification_context(env, candidate.verification_occurrence_ref),
    )
    receipt = runtime.transition_claim_lifecycle(
        claim.claim_ref,
        expected_head_revision=verified.resulting_claim_head.head_revision,
        expected_lifecycle_status=ClaimLifecycleStatus.ACTIVE,
        target_status=ClaimLifecycleStatus.INVALIDATED,
        reason_ref="claim-lifecycle://invalidate-source-change",
        operation_context=_context(
            env,
            authorization_ref="claim-lifecycle://invalidate-source-change",
            purpose="claim-lifecycle",
        ),
    )
    assert receipt.resulting_head.lifecycle_status is ClaimLifecycleStatus.INVALIDATED
    assert (
        receipt.resulting_head.active_verification_revision_ref
        == verified.verification_revision.revision_ref
    )
    view = runtime.resolve_active_verified_claim(claim.claim_ref)
    assert view.verdict_verified is True
    assert view.lifecycle_active is False
    assert view.active_verified_claim is False


def test_source_staleness_makes_active_verified_claim_ineligible_without_mutating_history(
    tmp_path,
) -> None:
    env, runtime, claim, binding, _support_runtime, support, verification_state = (
        _setup_verification_chain(tmp_path)
    )
    candidate = _verifier_candidate(
        claim,
        binding,
        support,
        verification_occurrence_ref="verification://source-stale",
    )
    committed = runtime.commit_verification(
        claim.claim_ref,
        binding.binding_facts_ref,
        support.support_ref,
        candidate,
        None,
        _verification_context(env, candidate.verification_occurrence_ref),
    )
    before = runtime.resolve_active_verified_claim(claim.claim_ref)
    assert before.active_verified_claim is True

    verification_state["current"] = False
    after = runtime.resolve_active_verified_claim(claim.claim_ref)
    assert after.binding_source_current is False
    assert after.active_verified_claim is False
    assert runtime.resolve_verification_result(
        committed.verification_result.verification_result_ref
    ) == committed.verification_result


def test_target_claim_materializer_builds_canonical_candidate_and_coverage(
    tmp_path,
) -> None:
    env, _paragraph, runtime, paragraph_version, *_ = _setup_claim_runtime(tmp_path)
    materializer = TargetClaimMaterializer()
    text = "The approved capacity is 100 MW."
    candidate = materializer.build_candidate(
        run_id=RUN_ID,
        section_id=SECTION_ID,
        paragraph_version_ref=paragraph_version,
        paragraph_text=text,
        claim_type=ClaimType.CURRENT_PROJECT_QUANTITY,
        start_offset=0,
        end_offset=len(text),
        normalized_value=100,
        unit="MW",
        extraction_origin=ClaimExtractionOrigin.DETERMINISTIC_EXTRACTOR,
        extraction_occurrence_ref="target-claim-extraction://1",
        span_locator_policy_id="exact-character-offset",
        span_locator_policy_version="v1",
        normalization_policy_id="numeric-canonical",
        normalization_policy_version="v1",
        claim_identity_policy_id="claim-occurrence",
        claim_identity_policy_version="v1",
    )
    materialized = materializer.materialize(
        paragraph_version_ref=paragraph_version,
        paragraph_text=text,
        candidates=(candidate,),
        extraction_occurrence_ref="target-claim-extraction://1",
        coverage_scope_ref=f"section:{SECTION_ID}",
        coverage_semantic_contract_version="claim-coverage-v1",
    )
    committed = runtime.commit_claim_records(
        materialized.candidates,
        materialized.coverage_accounting,
        _context(
            env,
            authorization_ref="target-claim-extraction://1",
            purpose="target-claim-records",
        ),
    )

    assert committed.records[0].claim_type == ClaimType.CURRENT_PROJECT_QUANTITY.value
    assert committed.coverage_accounting.accounting_state is (
        ClaimCoverageAccountingState.FACTUAL_CLAIMS_MATERIALIZED
    )


def test_target_zero_claim_coverage_is_indeterminate_without_explicit_proof(
    tmp_path,
) -> None:
    _env, _paragraph, _runtime, paragraph_version, *_ = _setup_claim_runtime(tmp_path)
    materializer = TargetClaimMaterializer()
    accounting = materializer.build_whole_paragraph_coverage(
        paragraph_version_ref=paragraph_version,
        paragraph_text="The approved capacity is 100 MW.",
        candidates=(),
        extraction_occurrence_ref="target-claim-extraction://none",
        coverage_scope_ref=f"section:{SECTION_ID}",
        coverage_semantic_contract_version="claim-coverage-v1",
        no_applicable_factual_claim_proven=False,
    )
    assert accounting.accounting_state is ClaimCoverageAccountingState.INDETERMINATE


def test_target_eligibility_projection_uses_typed_item_assessment_only() -> None:
    assessment = _eligibility_assessment().model_copy(
        update={
            "supported_claim_types": [ClaimType.CURRENT_PROJECT_QUANTITY],
        }
    )
    bundle = _eligibility_bundle(item_assessments=[assessment])
    eligible = TargetClaimMaterializer.eligible_evidence_ids(
        bundle,
        ClaimType.CURRENT_PROJECT_QUANTITY,
    )
    assert eligible == ("E1",)

    # Contradictory metadata on the underlying chunk/bundle is not consulted.
    assert TargetClaimMaterializer.eligible_evidence_ids(
        bundle,
        ClaimType.CURRENT_PROJECT_FACT,
    ) == ()


def test_target_binding_resolver_validates_caller_owned_exact_source_refs(
    tmp_path,
) -> None:
    env, _paragraph, runtime, paragraph_version, *_ = _setup_claim_runtime(tmp_path)
    materializer = TargetClaimMaterializer()
    text = "The approved capacity is 100 MW."
    candidate = materializer.build_candidate(
        run_id=RUN_ID,
        section_id=SECTION_ID,
        paragraph_version_ref=paragraph_version,
        paragraph_text=text,
        claim_type=ClaimType.CURRENT_PROJECT_QUANTITY,
        start_offset=0,
        end_offset=len(text),
        normalized_value=100,
        unit="MW",
        extraction_origin=ClaimExtractionOrigin.DETERMINISTIC_EXTRACTOR,
        extraction_occurrence_ref="target-claim-extraction://eligibility",
        span_locator_policy_id="exact-character-offset",
        span_locator_policy_version="v1",
        normalization_policy_id="numeric-canonical",
        normalization_policy_version="v1",
        claim_identity_policy_id="claim-occurrence",
        claim_identity_policy_version="v1",
    )
    coverage = materializer.build_whole_paragraph_coverage(
        paragraph_version_ref=paragraph_version,
        paragraph_text=text,
        candidates=(candidate,),
        extraction_occurrence_ref=candidate.extraction_occurrence_ref,
        coverage_scope_ref=f"section:{SECTION_ID}",
        coverage_semantic_contract_version="claim-coverage-v1",
    )
    claim = runtime.commit_claim_records(
        (candidate,),
        coverage,
        _context(
            env,
            authorization_ref=candidate.extraction_occurrence_ref,
            purpose="target-claim-records-eligibility",
        ),
    ).records[0]

    assessment = _eligibility_assessment().model_copy(
        update={
            "supported_claim_types": [ClaimType.CURRENT_PROJECT_QUANTITY],
        }
    )
    bundle = _eligibility_bundle(item_assessments=[assessment])
    resolver = ClaimBindingResolver()
    source = ClaimAuthorizedSourceRefSchema(
        source_kind="EVIDENCE",
        exact_source_ref="evidence-snapshot://bundle-1/E1",
        source_scope_ref=f"section:{SECTION_ID}",
    )
    validated = resolver.validate_target_authorized_sources(
        claim_record=claim,
        bundle=bundle,
        exact_authorized_source_refs=(source,),
        evidence_id_for_source_ref=lambda ref: "E1",
    )
    assert validated == (source,)

    with pytest.raises(ValueError, match="not Claim-eligible"):
        resolver.validate_target_authorized_sources(
            claim_record=claim,
            bundle=bundle,
            exact_authorized_source_refs=(source,),
            evidence_id_for_source_ref=lambda ref: "E999",
        )
