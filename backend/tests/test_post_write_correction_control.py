from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from apps.enterprise_document.schemas.agent_rag_v1.budgets import (
    CanonicalBudgetStateSchema,
    CanonicalBudgetVectorSchema,
)
from apps.enterprise_document.schemas.post_write import (
    ActiveComparisonMode,
    ActiveSectionBindingProjectionSchema,
    ArtifactScope,
    EvidenceSnapshotRefSchema,
    InitialWriterArtifactOriginSchema,
    PostWriteEvidenceBindingKind,
    PostWriteEvidenceBindingRef,
    PostWriteRequirementEntrySchema,
    RequiredDimensionConditionSchema,
    ValidationCompletionItemSchema,
    ValidationCompletionItemState,
    ValidationFactFamily,
    ValidationFactSchema,
    ValidationScope,
    ValidationSemanticState,
    ValidationTargetSelectorSchema,
)
from apps.enterprise_document.schemas.post_write.control import (
    ArtifactUsabilityIntegrityProofSchema,
    ArtifactUsabilityIntegrityState,
    CorrectabilityState,
    CorrectionPolicyRuleSchema,
    CorrectionProjectionState,
)
from apps.enterprise_document.schemas.post_write.currentness import (
    CurrentnessProofSchema,
    CurrentnessState,
)
from apps.enterprise_document.schemas.post_write.gate import (
    FactPolicyRuleSchema,
    QualityPolicyEffect,
)
from apps.enterprise_document.schemas.pre_write_binding import WriterSubjectRefSchema
from apps.enterprise_document.schemas.pre_write_control import (
    PreWriteRouteDecisionRefSchema,
)
from apps.enterprise_document.schemas.pre_write_writer import (
    WriterExecutionRefSchema,
    WriterGenerationClass,
    WriterGenerationInvocationRefSchema,
    WriterLogicalExecutionKey,
)
from apps.enterprise_document.services.post_write import (
    ArtifactStateService,
    ArtifactStateStore,
    ArtifactUsabilityIntegrityResolver,
    CanonicalRepairBudgetProjectionAdapter,
    ControlDecisionStore,
    CorrectionAttemptStore,
    CorrectionExecutor,
    CorrectionPolicyRegistry,
    CorrectionPolicyResolver,
    CorrectionPolicyStore,
    CurrentnessResolver,
    GateDecisionStore,
    PostWriteControlInputIntegrityGuard,
    PostWriteControlService,
    PostWriteEntryIdentityBuilder,
    PostWriteEntryStore,
    PostWriteSqliteDatabase,
    QualityDeliveryContractRegistry,
    QualityDeliveryContractStore,
    RequirementMaterializer,
    RequirementStore,
    SectionPostWriteEntryAssembler,
    SemanticCorrectionExecutorForbidden,
    ValidationCompletionBuilder,
    ValidationRunFactory,
    ValidationRunLifecycleService,
    ValidationStore,
    ValidationSubjectKeyBuilder,
)
from apps.enterprise_document.services.post_write.gate import (
    DeliveryGateService,
    GateInputIntegrityGuard,
)
from core.hashing import canonical_sha256


SHA_A = "a" * 64
SHA_B = "b" * 64


class _Ids:
    def __init__(self) -> None:
        self.counter = 0

    def new_id(self, prefix: str) -> str:
        self.counter += 1
        return f"{prefix}:control:{self.counter}"


class _Clock:
    def __init__(self) -> None:
        self.value = 1000.0

    def now_epoch(self) -> float:
        self.value += 1.0
        return self.value


def _writer_refs(generation_class: WriterGenerationClass):
    subject = WriterSubjectRefSchema(
        run_id="run-control-1",
        document_id="doc-control-1",
        section_id="section-control-1",
    )
    route = PreWriteRouteDecisionRefSchema(route_decision_id="route-control-1")
    key = WriterLogicalExecutionKey(
        writer_subject_ref=subject,
        write_instance_id="write-control-1",
        route_decision_ref=route,
    )
    return (
        route,
        WriterExecutionRefSchema(logical_execution_key=key, result_sha256=SHA_A),
        WriterGenerationInvocationRefSchema(
            logical_execution_key=key,
            invocation_sha256=SHA_B,
        ),
        generation_class,
    )


def _fact(*, run_id: str, subject_hash: str, state: ValidationSemanticState):
    base = {
        "schema_version": "validation_fact_v1",
        "contract_version": "validator-fact-v1",
        "fact_id": "fact-control-faithfulness",
        "validation_run_id": run_id,
        "subject_hash": subject_hash,
        "producer_validator_id": "validator-control-fixture",
        "producer_validator_version": "v1",
        "family": ValidationFactFamily.FAITHFULNESS,
        "target_ref": "claim-control-1",
        "semantic_state": state,
        "payload": {"fixture": "control"},
    }
    return ValidationFactSchema(**base, fact_sha256=canonical_sha256(base))


def _fixture(
    tmp_path,
    *,
    quality: str,
    generation_class: WriterGenerationClass = WriterGenerationClass.NORMAL_WRITE,
    repair_remaining: int = 2,
):
    database = PostWriteSqliteDatabase(tmp_path / "post_write.sqlite3")
    route, execution, invocation, generation_class = _writer_refs(generation_class)

    requirement_ref = RequirementMaterializer(
        RequirementStore(database)
    ).materialize(
        semantic_contract_version="post-write-requirements-v1",
        source_business_ref="business://control/v1",
        source_task_ref="task://control/v1",
        requirements=(
            PostWriteRequirementEntrySchema(
                requirement_id="req-control-1",
                requirement_kind="business",
                statement="must preserve exact supported semantics",
            ),
        ),
    )

    gate_rules = ()
    if quality == "FAIL":
        gate_rules = (
            FactPolicyRuleSchema(
                policy_rule_ref="rule:block-control-faithfulness",
                fact_family=ValidationFactFamily.FAITHFULNESS,
                semantic_states=(ValidationSemanticState.FAIL,),
                effect=QualityPolicyEffect.BLOCKING,
                reason_code="CONTROL_FAITHFULNESS_FAIL_BLOCKS",
            ),
        )

    quality_contract_ref = QualityDeliveryContractRegistry(
        QualityDeliveryContractStore(database)
    ).register(
        contract_id="quality-control-main",
        contract_version="v1",
        required_dimensions=(
            RequiredDimensionConditionSchema(
                dimension_id="faithfulness",
                fact_families=(ValidationFactFamily.FAITHFULNESS,),
            ),
        ),
        fact_policy_rules=gate_rules,
    )

    artifact_store = ArtifactStateStore(database)
    artifact_ref = ArtifactStateService(store=artifact_store).create_initial(
        artifact_id="section-artifact:run-control-1:section-control-1",
        artifact_scope=ArtifactScope.SECTION,
        canonical_payload_ref={"payload_kind": "section", "text_sha256": SHA_A},
        origin=InitialWriterArtifactOriginSchema(
            writer_execution_ref=execution,
            writer_invocation_ref=invocation,
        ),
    )
    evidence_binding = PostWriteEvidenceBindingRef(
        binding_kind=PostWriteEvidenceBindingKind.SECTION_WRITER_SNAPSHOT,
        section_writer_snapshot_ref=EvidenceSnapshotRefSchema(
            evidence_bundle_schema_version="evidence_bundle_v2",
            evidence_bundle_sha256=SHA_B,
            writer_invocation_ref=invocation,
        ),
    )
    identity_builder = PostWriteEntryIdentityBuilder()
    entry_store = PostWriteEntryStore(database)
    entry_ref = SectionPostWriteEntryAssembler(
        store=entry_store,
        identity_builder=identity_builder,
    ).assemble_and_persist(
        run_id="run-control-1",
        document_id="doc-control-1",
        section_id="section-control-1",
        artifact_state_ref=artifact_ref,
        writer_execution_ref=execution,
        writer_invocation_ref=invocation,
        route_decision_ref=route,
        evidence_binding=evidence_binding,
        requirement_ref=requirement_ref,
        quality_delivery_contract_ref=quality_contract_ref,
        generation_class=generation_class,
    )
    active_projection = ActiveSectionBindingProjectionSchema(
        run_id="run-control-1",
        document_id="doc-control-1",
        section_id="section-control-1",
        artifact_state_ref=artifact_ref,
        evidence_binding=evidence_binding,
        requirement_ref=requirement_ref,
        quality_delivery_contract_ref=quality_contract_ref,
        writer_execution_ref=execution,
        writer_invocation_ref=invocation,
        route_decision_ref=route,
        generation_class=generation_class,
    )
    currentness = CurrentnessResolver(
        entry_store=entry_store,
        identity_builder=identity_builder,
    ).prove(
        historical_entry_ref=entry_ref,
        active_projection=active_projection,
        comparison_mode=ActiveComparisonMode.MATERIALIZED_ENTRY,
        active_materialized_post_write_entry_ref=entry_ref,
    )

    subject_key = ValidationSubjectKeyBuilder.build(
        post_write_entry_ref=entry_ref,
        validation_scope=ValidationScope.SECTION,
        target_selector=ValidationTargetSelectorSchema(
            selector_kind="section",
            target_ref="section-control-1",
            selector_contract_version="selector-v1",
        ),
        subject_contract_version="subject-v1",
    )
    clock = _Clock()
    validation_store = ValidationStore(database)
    lifecycle = ValidationRunLifecycleService(store=validation_store, clock=clock)
    run = ValidationRunFactory(id_generator=_Ids(), clock=clock).create(
        subject_key=subject_key,
        creation_command_id="validation-create-control",
    )
    lifecycle.create(run)
    lifecycle.start(
        run.validation_run_id,
        expected_state_version=1,
        transition_command_id="validation-start-control",
    )

    fact_state = (
        ValidationSemanticState.PASS
        if quality == "PASS"
        else ValidationSemanticState.FAIL
    )
    fact = _fact(
        run_id=run.validation_run_id,
        subject_hash=subject_key.subject_hash,
        state=fact_state,
    )
    fact_ref = validation_store.put_fact_if_absent_or_same(fact)
    completion_state = (
        ValidationCompletionItemState.PASS
        if quality == "PASS"
        else ValidationCompletionItemState.FAIL
    )
    completion = ValidationCompletionBuilder.build(
        validation_run_id=run.validation_run_id,
        subject_hash=subject_key.subject_hash,
        items=(
            ValidationCompletionItemSchema(
                expected_validation_item_ref="faithfulness:item-control",
                required=True,
                state=completion_state,
                fact_refs=(fact_ref,),
            ),
        ),
    )
    completion_ref = validation_store.put_completion_if_absent_or_same(completion)
    lifecycle.complete(
        run.validation_run_id,
        expected_state_version=2,
        transition_command_id="validation-complete-control",
    )

    gate_store = GateDecisionStore(database)
    gate_service = DeliveryGateService(
        input_guard=GateInputIntegrityGuard(
            validation_store=validation_store,
            entry_store=entry_store,
            contract_store=QualityDeliveryContractStore(database),
        ),
        decision_store=gate_store,
        clock=lambda: 2000.0,
    )
    gate = gate_service.evaluate(
        validation_run_id=run.validation_run_id,
        validation_completion_ref=completion_ref,
        quality_delivery_contract_ref=quality_contract_ref,
        currentness_proof=currentness,
    )

    correctable_condition_ref = (
        "fact-block://fact-control-faithfulness/"
        "rule:block-control-faithfulness"
    )
    correction_rules = ()
    if quality == "FAIL":
        correction_rules = (
            CorrectionPolicyRuleSchema(
                policy_rule_ref="correction-rule:faithfulness",
                blocking_condition_ref=correctable_condition_ref,
                correctability=CorrectabilityState.CORRECTABLE_ARTIFACT_SIDE,
                reason_code="REPAIR_FAITHFULNESS",
            ),
        )
    policy_store = CorrectionPolicyStore(database)
    policy_ref = CorrectionPolicyRegistry(policy_store).register(
        policy_id="correction-policy-main",
        policy_version="v1",
        rules=correction_rules,
    )

    now = datetime(2026, 9, 5, tzinfo=timezone.utc)
    limit = 2
    used = limit - repair_remaining
    budget_state = CanonicalBudgetStateSchema(
        task_id="task-control-1",
        run_id="run-control-1",
        policy_ref="budget-policy://control",
        policy_version="v1",
        limits=CanonicalBudgetVectorSchema(repair_attempts=limit),
        used=CanonicalBudgetVectorSchema(repair_attempts=used),
        active_reserved=CanonicalBudgetVectorSchema(),
        remaining=CanonicalBudgetVectorSchema(repair_attempts=repair_remaining),
        exhausted_dimensions=[] if repair_remaining else ["repair_attempts"],
        draining=False,
        revision=7,
        started_at=now,
        deadline_at=now + timedelta(hours=1),
        reservations=[],
    )
    lineage = CanonicalRepairBudgetProjectionAdapter.project(
        target_artifact_ref=artifact_ref,
        budget_state=budget_state,
    )
    usability = ArtifactUsabilityIntegrityResolver(artifact_store).resolve(
        artifact_state_ref=artifact_ref,
        currentness_proof=currentness,
    )
    control_store = ControlDecisionStore(database)
    control_service = PostWriteControlService(
        integrity_guard=PostWriteControlInputIntegrityGuard(
            gate_store=gate_store,
            correction_policy_resolver=CorrectionPolicyResolver(policy_store),
        ),
        control_store=control_store,
    )
    return {
        "database": database,
        "artifact_ref": artifact_ref,
        "artifact_store": artifact_store,
        "currentness": currentness,
        "usability": usability,
        "gate": gate,
        "policy_ref": policy_ref,
        "lineage": lineage,
        "control_store": control_store,
        "control_service": control_service,
        "generation_class": generation_class,
    }


def _decide(fx, **overrides):
    kwargs = {
        "delivery_gate_decision_ref": fx["gate"].as_ref(),
        "artifact_state_ref": fx["artifact_ref"],
        "correction_policy_ref": fx["policy_ref"],
        "fresh_currentness_proof": fx["currentness"],
        "artifact_usability_integrity_proof": fx["usability"],
        "correction_lineage_projection": fx["lineage"],
        "generation_class": fx["generation_class"],
        "created_at_epoch": 3000.0,
    }
    kwargs.update(overrides)
    ref = fx["control_service"].decide(**kwargs)
    return fx["control_store"].resolve_exact(ref)


def _rehash_currentness(proof: CurrentnessProofSchema, state: CurrentnessState):
    payload = proof.model_dump(mode="json")
    payload["currentness_state"] = state.value
    payload.pop("proof_sha256", None)
    return CurrentnessProofSchema(
        **payload,
        proof_sha256=canonical_sha256(payload),
    )


def test_pass_current_trusted_outputs(tmp_path) -> None:
    fx = _fixture(tmp_path, quality="PASS")
    decision = _decide(fx)

    assert decision.enforcement_action.value == "OUTPUT"
    assert decision.availability_projection.state.value == "AVAILABLE"
    assert decision.quality_state_passthrough.value == "PASS"
    assert decision.correction_projection.state is CorrectionProjectionState.NOT_NEEDED


def test_fail_normal_write_correctable_and_budgeted_requests_exact_correction(
    tmp_path,
) -> None:
    fx = _fixture(tmp_path, quality="FAIL", repair_remaining=2)
    decision = _decide(fx)

    assert decision.enforcement_action.value == "CORRECT"
    assert decision.availability_projection.state.value == "WITHHELD"
    assert (
        decision.correction_projection.state
        is CorrectionProjectionState.ELIGIBLE_AND_REQUESTED
    )
    authorization = decision.correction_projection.authorization
    assert authorization is not None
    assert authorization.target_artifact_ref == fx["artifact_ref"]
    assert authorization.repair_attempts_remaining == 2
    assert authorization.target_generation_class is WriterGenerationClass.NORMAL_WRITE


def test_fallback_write_never_auto_corrects_and_uses_terminal_best_effort(tmp_path) -> None:
    fx = _fixture(
        tmp_path,
        quality="FAIL",
        generation_class=WriterGenerationClass.FALLBACK_WRITE,
        repair_remaining=2,
    )
    decision = _decide(fx)

    assert decision.enforcement_action.value == "OUTPUT_BEST_EFFORT"
    assert decision.availability_projection.state.value == "BEST_EFFORT"
    assert decision.correction_projection.authorization is None
    assert (
        decision.correction_projection.state
        is CorrectionProjectionState.NOT_ELIGIBLE
    )


def test_exhausted_sif12_repair_budget_is_not_recreated_by_control(tmp_path) -> None:
    fx = _fixture(tmp_path, quality="FAIL", repair_remaining=0)
    decision = _decide(fx)

    assert decision.enforcement_action.value == "OUTPUT_BEST_EFFORT"
    assert decision.correction_projection.state is CorrectionProjectionState.EXHAUSTED
    assert decision.correction_projection.authorization is None


def test_fresh_stale_currentness_blocks_even_with_historical_gate_fail(tmp_path) -> None:
    fx = _fixture(tmp_path, quality="FAIL")
    stale = _rehash_currentness(fx["currentness"], CurrentnessState.STALE)
    usability = ArtifactUsabilityIntegrityResolver(fx["artifact_store"]).resolve(
        artifact_state_ref=fx["artifact_ref"],
        currentness_proof=stale,
    )
    decision = _decide(
        fx,
        fresh_currentness_proof=stale,
        artifact_usability_integrity_proof=usability,
    )

    assert decision.enforcement_action.value == "BLOCK"
    assert decision.availability_projection.state.value == "WITHHELD"
    assert decision.correction_projection.state is CorrectionProjectionState.STALE_INPUT


def test_untrusted_artifact_blocks_even_when_quality_passes(tmp_path) -> None:
    fx = _fixture(tmp_path, quality="PASS")
    usability_payload = fx["usability"].model_dump(mode="json")
    usability_payload["state"] = ArtifactUsabilityIntegrityState.INTEGRITY_INVALID.value
    usability_payload["artifact_record_hash"] = None
    usability_payload["reason_codes"] = ["TEST_INTEGRITY_INVALID"]
    usability_payload.pop("proof_sha256", None)
    unusable = ArtifactUsabilityIntegrityProofSchema(
        **usability_payload,
        proof_sha256=canonical_sha256(usability_payload),
    )

    decision = _decide(fx, artifact_usability_integrity_proof=unusable)
    assert decision.enforcement_action.value == "BLOCK"
    assert decision.availability_projection.state.value == "WITHHELD"


def test_same_exact_control_input_is_idempotent_across_audit_timestamp(tmp_path) -> None:
    fx = _fixture(tmp_path, quality="PASS")
    first = _decide(fx, created_at_epoch=3000.0)
    second = _decide(fx, created_at_epoch=9999.0)

    assert first.as_ref() == second.as_ref()
    assert first.control_decision_hash == second.control_decision_hash


def test_correction_executor_refuses_semantic_model_prose_path() -> None:
    executor = CorrectionExecutor()
    with pytest.raises(SemanticCorrectionExecutorForbidden):
        executor.execute_semantic(prompt="rewrite")


def test_correction_attempt_store_is_projection_only_by_api_surface(tmp_path) -> None:
    store = CorrectionAttemptStore(
        PostWriteSqliteDatabase(tmp_path / "post_write.sqlite3")
    )
    for forbidden in (
        "reserve",
        "remaining_budget",
        "dispatch",
        "mark_dispatched",
        "consume_budget",
    ):
        assert not hasattr(store, forbidden)
