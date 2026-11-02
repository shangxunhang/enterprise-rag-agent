"""T12-05 focused deterministic Gate V2 Target Router acceptance tests."""

from __future__ import annotations

import pytest

from apps.enterprise_document.schemas.pre_write_admission import (
    CriterionEvidenceFactSchema,
    CriterionEvidenceSupportState,
    EvidenceCriterionSchema,
    EvidenceRequirementSchema,
    RequirementEvidenceFactSchema,
    WriterAdmissionAssessmentState,
    WriterAdmissionContractSchema,
    WriterAdmissionFactAssessmentSchema,
)
from apps.enterprise_document.schemas.pre_write_binding import (
    BindingApplicabilityResult,
    BindingApplicabilityState,
    WriterGateBindingRefSchema,
    WriterSubjectRefSchema,
)
from apps.enterprise_document.schemas.pre_write_control import (
    EnforcementAction,
    EnforcementBasis,
    EnforcementResultSchema,
    PreWriteRoute,
    RecoveryControlFactsSchema,
    RecoveryRoundOutcome,
)
from apps.enterprise_document.services.scheme_writer.pre_write_recovery import (
    PreWriteRecoveryController,
    RecoveryChainSnapshot,
)
from apps.enterprise_document.services.scheme_writer.pre_write_router import (
    PreWriteRouter,
    PreWriteRoutingIntegrityError,
    PreWriteRoutingNotReadyError,
)
from apps.enterprise_document.services.scheme_writer.writer_admission import (
    EvidenceRequirementsMaterializer,
    WriterAdmissionPolicy,
)
from apps.enterprise_document.services.scheme_writer.writer_admission_enforcement import (
    WriterAdmissionEnforcement,
)
from apps.enterprise_document.services.scheme_writer.writer_gate_binding import (
    WriterGateApplicabilityValidator,
    WriterGateBinder,
)
from contracts.evidence import evidence_bundle_sha256
from schemas.rag import EvidenceBundleSchema, RAGContextSchema


SUBJECT = WriterSubjectRefSchema(
    run_id="run-router",
    document_id="document-router",
    section_id="section-router",
)
WRITE_INSTANCE_ID = "write-instance-router"


def _requirements():
    return EvidenceRequirementsMaterializer.materialize(
        source_profile_ref="profile-router",
        source_task_ref="task-router",
        requirements=(
            EvidenceRequirementSchema(
                requirement_id="r1",
                criteria=(
                    EvidenceCriterionSchema(
                        criterion_id="c1", criterion="必须满足判据 C1"
                    ),
                ),
            ),
        ),
    )


def _bundle(query: str):
    return EvidenceBundleSchema(
        query=query,
        context=RAGContextSchema(context_text="", used_context_chars=0),
    )


def _chain(*, kind: str, name: str) -> RecoveryChainSnapshot:
    requirements = _requirements()
    bundle = _bundle(name)
    if kind == "pending":
        assessment = WriterAdmissionFactAssessmentSchema(
            assessment_id=f"assessment-{name}",
            assessment_state=WriterAdmissionAssessmentState.PENDING,
            requirements_schema_version=requirements.schema_version,
            requirements_contract_version=requirements.contract_version,
            requirements_sha256=requirements.requirements_sha256,
            evidence_bundle_schema_version=bundle.schema_version,
            evidence_bundle_sha256=evidence_bundle_sha256(bundle),
        )
    else:
        blocked = kind == "deny"
        criterion = CriterionEvidenceFactSchema(
            fact_id=f"criterion-{name}",
            requirement_id="r1",
            criterion_id="c1",
            support_state=(
                CriterionEvidenceSupportState.NOT_SUPPORTED
                if blocked
                else CriterionEvidenceSupportState.SUPPORTED
            ),
            evidence_ids=() if blocked else ("evidence-c1",),
        )
        assessment = WriterAdmissionFactAssessmentSchema(
            assessment_id=f"assessment-{name}",
            assessment_state=WriterAdmissionAssessmentState.COMPLETED,
            requirements_schema_version=requirements.schema_version,
            requirements_contract_version=requirements.contract_version,
            requirements_sha256=requirements.requirements_sha256,
            evidence_bundle_schema_version=bundle.schema_version,
            evidence_bundle_sha256=evidence_bundle_sha256(bundle),
            criterion_facts=(criterion,),
            requirement_facts=(
                RequirementEvidenceFactSchema(
                    fact_id=f"requirement-{name}",
                    requirement_id="r1",
                    support_state=(
                        RequirementEvidenceFactSchema.SupportState.NOT_SUPPORTED
                        if blocked
                        else RequirementEvidenceFactSchema.SupportState.SUPPORTED
                    ),
                    criterion_fact_ids=(criterion.fact_id,),
                ),
            ),
        )
    decision = WriterAdmissionPolicy(
        decision_id_factory=lambda: f"decision-{name}"
    ).evaluate(
        assessment=assessment,
        contract=WriterAdmissionContractSchema(),
    )
    binding = WriterGateBinder.bind(
        decision=decision,
        writer_subject_ref=SUBJECT,
        write_instance_id=WRITE_INSTANCE_ID,
    )
    return RecoveryChainSnapshot(
        evidence_bundle=bundle,
        requirements=requirements,
        assessment=assessment,
        decision=decision,
        binding=binding,
    )


def _binding_ref(chain: RecoveryChainSnapshot):
    return WriterGateBindingRefSchema(
        binding_id=chain.binding.binding_id,
        binding_schema_version=chain.binding.schema_version,
        binding_contract_version=chain.binding.contract_version,
    )


def _applicable(chain: RecoveryChainSnapshot):
    result = WriterGateApplicabilityValidator().validate(
        binding=chain.binding,
        current_writer_subject_ref=SUBJECT,
        current_write_instance_id=WRITE_INSTANCE_ID,
        current_decision=chain.decision,
        current_active_evidence_bundle_sha256=evidence_bundle_sha256(
            chain.evidence_bundle
        ),
        current_active_requirements_sha256=chain.requirements.requirements_sha256,
        current_active_binding_ref=_binding_ref(chain),
    )
    assert result.state is BindingApplicabilityState.APPLICABLE
    return result


def _enforcement(chain: RecoveryChainSnapshot, *, mode: str):
    return WriterAdmissionEnforcement().evaluate(
        applicability=_applicable(chain),
        decision=chain.decision,
        mode=mode,
    )


def _recovery_facts(
    chain: RecoveryChainSnapshot,
    *,
    rounds_limit: int = 2,
    mode_independent_state_revision: int | None = None,
):
    controller = PreWriteRecoveryController()
    state = controller.initialize_state(
        writer_subject_ref=SUBJECT,
        write_instance_id=WRITE_INSTANCE_ID,
        recovery_rounds_limit=rounds_limit,
        no_progress_rounds_limit=1,
    )
    if mode_independent_state_revision is not None:
        state = state.model_copy(
            update={"recovery_state_revision": mode_independent_state_revision}
        )
    return controller.evaluate_control_facts(
        chain=chain,
        applicability=_applicable(chain),
        runtime_state=state,
    )


@pytest.mark.parametrize(
    "kind,mode,rounds_limit,expected",
    [
        ("allow", "strict", 2, PreWriteRoute.NORMAL),
        ("allow", "shadow", 2, PreWriteRoute.NORMAL),
        ("deny", "shadow", 2, PreWriteRoute.NORMAL),
        ("deny", "shadow", 0, PreWriteRoute.NORMAL),
        ("deny", "strict", 2, PreWriteRoute.RECOVERY),
        ("deny", "strict", 0, PreWriteRoute.FALLBACK),
    ],
)
def test_prewrite_router_exact_deterministic_matrix(
    kind, mode, rounds_limit, expected
) -> None:
    chain = _chain(kind=kind, name=f"{kind}-{mode}-{rounds_limit}")
    applicability = _applicable(chain)
    route = PreWriteRouter.evaluate(
        writer_subject_ref=SUBJECT,
        write_instance_id=WRITE_INSTANCE_ID,
        decision=chain.decision,
        binding=chain.binding,
        applicability=applicability,
        enforcement=WriterAdmissionEnforcement().evaluate(
            applicability=applicability,
            decision=chain.decision,
            mode=mode,
        ),
        recovery_control=_recovery_facts(chain, rounds_limit=rounds_limit),
    )
    assert route.selected_route is expected
    if kind == "deny" and mode == "shadow":
        assert chain.decision.admission_outcome.value == "deny"


def test_option_d_authorization_snapshot_hash_covers_full_semantics() -> None:
    chain = _chain(kind="deny", name="option-d-hash")
    applicability = _applicable(chain)
    enforcement = _enforcement(chain, mode="strict")
    recovery = _recovery_facts(chain, rounds_limit=2)

    def digest(*, decision=chain.decision, binding=chain.binding,
               applicable=applicability, enforced=enforcement,
               recovery_facts=recovery, subject=SUBJECT) -> str:
        return PreWriteRouter.authorization_snapshot_sha256(
            writer_subject_ref=subject,
            write_instance_id=WRITE_INSTANCE_ID,
            decision=decision,
            binding=binding,
            applicability=applicable,
            enforcement=enforced,
            recovery_control=recovery_facts,
        )

    baseline = digest()
    assert digest() == baseline

    changed_assessment_ref = chain.decision.fact_assessment_ref.model_copy(
        update={"evidence_bundle_sha256": "f" * 64}
    )
    changed_decision = chain.decision.model_copy(
        update={"fact_assessment_ref": changed_assessment_ref}
    )
    changed_applicability = applicability.model_copy(
        update={"decision": changed_decision}
    )
    assert changed_decision.decision_id == chain.decision.decision_id
    assert changed_decision.schema_version == chain.decision.schema_version
    assert changed_decision.contract_version == chain.decision.contract_version
    assert digest(
        decision=changed_decision,
        applicable=changed_applicability,
    ) != baseline

    changed_enforcement = EnforcementResultSchema(
        decision_ref=enforcement.decision_ref,
        binding_ref=enforcement.binding_ref,
        mode=enforcement.mode,
        action=EnforcementAction.CONTINUE,
        would_block_in_strict=False,
        enforcement_basis=EnforcementBasis.BUSINESS_ADMISSION,
    )
    assert changed_enforcement.schema_version == enforcement.schema_version
    assert changed_enforcement.contract_version == enforcement.contract_version
    assert changed_enforcement.mode == enforcement.mode
    assert digest(enforced=changed_enforcement) != baseline

    changed_recovery = recovery.model_copy(
        update={"recovery_rounds_used": recovery.recovery_rounds_used + 1}
    )
    assert changed_recovery.recovery_state_revision == recovery.recovery_state_revision
    assert changed_recovery.schema_version == recovery.schema_version
    assert changed_recovery.contract_version == recovery.contract_version
    assert digest(recovery_facts=changed_recovery) != baseline

    changed_subject = SUBJECT.model_copy(update={"section_id": "section-router-other"})
    changed_binding = chain.binding.model_copy(
        update={"writer_subject_ref": changed_subject}
    )
    changed_binding_applicability = applicability.model_copy(
        update={"binding": changed_binding}
    )
    changed_recovery_subject = recovery.model_copy(
        update={"writer_subject_ref": changed_subject}
    )
    assert changed_binding.binding_id == chain.binding.binding_id
    assert changed_binding.schema_version == chain.binding.schema_version
    assert changed_binding.contract_version == chain.binding.contract_version
    assert digest(
        subject=changed_subject,
        binding=changed_binding,
        applicable=changed_binding_applicability,
        recovery_facts=changed_recovery_subject,
    ) != baseline


def test_route_decision_identity_converges_for_same_effective_snapshot() -> None:
    chain = _chain(kind="deny", name="same-snapshot")
    applicability = _applicable(chain)
    enforcement = _enforcement(chain, mode="strict")
    recovery = _recovery_facts(chain, rounds_limit=2)
    first = PreWriteRouter.evaluate(
        writer_subject_ref=SUBJECT,
        write_instance_id=WRITE_INSTANCE_ID,
        decision=chain.decision,
        binding=chain.binding,
        applicability=applicability,
        enforcement=enforcement,
        recovery_control=recovery,
    )
    second = PreWriteRouter.evaluate(
        writer_subject_ref=SUBJECT,
        write_instance_id=WRITE_INSTANCE_ID,
        decision=chain.decision,
        binding=chain.binding,
        applicability=applicability,
        enforcement=enforcement,
        recovery_control=recovery,
    )
    assert first == second
    assert first.route_decision_id == second.route_decision_id

    newer_recovery = recovery.model_copy(
        update={"recovery_state_revision": recovery.recovery_state_revision + 1}
    )
    changed = PreWriteRouter.evaluate(
        writer_subject_ref=SUBJECT,
        write_instance_id=WRITE_INSTANCE_ID,
        decision=chain.decision,
        binding=chain.binding,
        applicability=applicability,
        enforcement=enforcement,
        recovery_control=newer_recovery,
    )
    assert changed.route_decision_id != first.route_decision_id


def test_route_commit_revalidates_current_snapshot_and_expected_route() -> None:
    chain = _chain(kind="deny", name="commit")
    applicability = _applicable(chain)
    strict = _enforcement(chain, mode="strict")
    recovery = _recovery_facts(chain, rounds_limit=2)
    route = PreWriteRouter.evaluate(
        writer_subject_ref=SUBJECT,
        write_instance_id=WRITE_INSTANCE_ID,
        decision=chain.decision,
        binding=chain.binding,
        applicability=applicability,
        enforcement=strict,
        recovery_control=recovery,
    )
    assert route.selected_route is PreWriteRoute.RECOVERY
    assert (
        PreWriteRouter.validate_commit(
            route,
            writer_subject_ref=SUBJECT,
            write_instance_id=WRITE_INSTANCE_ID,
            decision=chain.decision,
            binding=chain.binding,
            applicability=applicability,
            enforcement=strict,
            recovery_control=recovery,
        )
        == route
    )

    shadow = _enforcement(chain, mode="shadow")
    with pytest.raises(PreWriteRoutingIntegrityError, match="stale|contradictory"):
        PreWriteRouter.validate_commit(
            route,
            writer_subject_ref=SUBJECT,
            write_instance_id=WRITE_INSTANCE_ID,
            decision=chain.decision,
            binding=chain.binding,
            applicability=applicability,
            enforcement=shadow,
            recovery_control=recovery,
        )

    contradictory = route.model_copy(update={"selected_route": PreWriteRoute.FALLBACK})
    with pytest.raises(PreWriteRoutingIntegrityError, match="stale|contradictory"):
        PreWriteRouter.validate_commit(
            contradictory,
            writer_subject_ref=SUBJECT,
            write_instance_id=WRITE_INSTANCE_ID,
            decision=chain.decision,
            binding=chain.binding,
            applicability=applicability,
            enforcement=strict,
            recovery_control=recovery,
        )


def test_router_rejects_unresolved_and_technical_integrity_inputs() -> None:
    pending = _chain(kind="pending", name="pending")
    pending_applicability = _applicable(pending)
    pending_enforcement = WriterAdmissionEnforcement().evaluate(
        applicability=pending_applicability,
        decision=pending.decision,
        mode="shadow",
    )
    pending_recovery = _recovery_facts(pending, rounds_limit=2)
    with pytest.raises(PreWriteRoutingNotReadyError):
        PreWriteRouter.evaluate(
            writer_subject_ref=SUBJECT,
            write_instance_id=WRITE_INSTANCE_ID,
            decision=pending.decision,
            binding=pending.binding,
            applicability=pending_applicability,
            enforcement=pending_enforcement,
            recovery_control=pending_recovery,
        )

    deny = _chain(kind="deny", name="technical")
    technical_applicability = BindingApplicabilityResult(
        state=BindingApplicabilityState.NOT_APPLICABLE
    )
    technical_enforcement = WriterAdmissionEnforcement().evaluate(
        applicability=technical_applicability,
        decision=deny.decision,
        mode="shadow",
    )
    assert technical_enforcement.enforcement_basis is EnforcementBasis.TECHNICAL_INTEGRITY
    with pytest.raises(PreWriteRoutingIntegrityError):
        PreWriteRouter.evaluate(
            writer_subject_ref=SUBJECT,
            write_instance_id=WRITE_INSTANCE_ID,
            decision=deny.decision,
            binding=deny.binding,
            applicability=technical_applicability,
            enforcement=technical_enforcement,
            recovery_control=_recovery_facts(deny, rounds_limit=2),
        )


def test_shadow_deny_normal_is_not_vetoed_by_recovery_specific_technical_condition() -> None:
    chain = _chain(kind="deny", name="shadow-technical-recovery")
    applicability = _applicable(chain)
    controller = PreWriteRecoveryController()
    state = controller.initialize_state(
        writer_subject_ref=SUBJECT,
        write_instance_id=WRITE_INSTANCE_ID,
        recovery_rounds_limit=2,
        no_progress_rounds_limit=1,
    )
    recovery = controller.evaluate_control_facts(
        chain=chain,
        applicability=applicability,
        runtime_state=state,
        dispatch_technically_blocked=True,
    )
    assert recovery.recovery_available is False
    assert recovery.recovery_exhausted is False
    route = PreWriteRouter.evaluate(
        writer_subject_ref=SUBJECT,
        write_instance_id=WRITE_INSTANCE_ID,
        decision=chain.decision,
        binding=chain.binding,
        applicability=applicability,
        enforcement=WriterAdmissionEnforcement().evaluate(
            applicability=applicability,
            decision=chain.decision,
            mode="shadow",
        ),
        recovery_control=recovery,
    )
    assert route.selected_route is PreWriteRoute.NORMAL


def test_router_does_not_guess_when_deny_block_has_no_business_recovery_state() -> None:
    chain = _chain(kind="deny", name="invalid-combination")
    applicability = _applicable(chain)
    enforcement = _enforcement(chain, mode="strict")
    valid = _recovery_facts(chain, rounds_limit=2)
    invalid = RecoveryControlFactsSchema(
        writer_subject_ref=valid.writer_subject_ref,
        write_instance_id=valid.write_instance_id,
        decision_ref=valid.decision_ref,
        binding_ref=valid.binding_ref,
        recovery_state_revision=valid.recovery_state_revision,
        recovery_available=False,
        recovery_exhausted=False,
        recovery_rounds_used=valid.recovery_rounds_used,
        recovery_rounds_limit=valid.recovery_rounds_limit,
        consecutive_no_progress_rounds=valid.consecutive_no_progress_rounds,
        no_progress_rounds_limit=valid.no_progress_rounds_limit,
        last_round_outcome=RecoveryRoundOutcome.NOT_STARTED,
    )
    assert enforcement.action is EnforcementAction.BLOCK
    with pytest.raises(PreWriteRoutingIntegrityError, match="requires exactly"):
        PreWriteRouter.evaluate(
            writer_subject_ref=SUBJECT,
            write_instance_id=WRITE_INSTANCE_ID,
            decision=chain.decision,
            binding=chain.binding,
            applicability=applicability,
            enforcement=enforcement,
            recovery_control=invalid,
        )


def test_router_rejects_stale_applicability_lineage_even_when_state_says_applicable() -> None:
    chain = _chain(kind="deny", name="lineage")
    other = _chain(kind="deny", name="other-lineage")
    forged_other_applicability = BindingApplicabilityResult(
        state=BindingApplicabilityState.APPLICABLE,
        decision=other.decision,
        binding=other.binding,
    )
    with pytest.raises(PreWriteRoutingIntegrityError, match="different Decision/Binding"):
        PreWriteRouter.evaluate(
            writer_subject_ref=SUBJECT,
            write_instance_id=WRITE_INSTANCE_ID,
            decision=chain.decision,
            binding=chain.binding,
            applicability=forged_other_applicability,
            enforcement=_enforcement(chain, mode="strict"),
            recovery_control=_recovery_facts(chain, rounds_limit=2),
        )


def test_route_identity_preimage_excludes_selected_route() -> None:
    chain = _chain(kind="deny", name="identity-preimage")
    applicability = _applicable(chain)
    enforcement = _enforcement(chain, mode="strict")
    recovery = _recovery_facts(chain, rounds_limit=2)
    route = PreWriteRouter.evaluate(
        writer_subject_ref=SUBJECT,
        write_instance_id=WRITE_INSTANCE_ID,
        decision=chain.decision,
        binding=chain.binding,
        applicability=applicability,
        enforcement=enforcement,
        recovery_control=recovery,
    )
    contradictory = route.model_copy(update={"selected_route": PreWriteRoute.FALLBACK})
    assert contradictory.route_decision_id == route.route_decision_id
    with pytest.raises(PreWriteRoutingIntegrityError):
        PreWriteRouter.validate_commit(
            contradictory,
            writer_subject_ref=SUBJECT,
            write_instance_id=WRITE_INSTANCE_ID,
            decision=chain.decision,
            binding=chain.binding,
            applicability=applicability,
            enforcement=enforcement,
            recovery_control=recovery,
        )
