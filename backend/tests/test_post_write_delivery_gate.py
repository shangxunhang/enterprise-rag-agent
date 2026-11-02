from __future__ import annotations

import sqlite3

import pytest

from apps.enterprise_document.schemas.post_write import (
    ActiveComparisonMode,
    ActiveSectionBindingProjectionSchema,
    CurrentnessMatchState,
    CurrentnessState,
    ArtifactScope,
    ArtifactStateRefSchema,
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
from apps.enterprise_document.schemas.post_write.gate import (
    FactPolicyRuleSchema,
    QualityPolicyEffect,
)
from apps.enterprise_document.schemas.pre_write_binding import WriterSubjectRefSchema
from apps.enterprise_document.schemas.pre_write_control import PreWriteRouteDecisionRefSchema
from apps.enterprise_document.schemas.pre_write_writer import (
    WriterExecutionRefSchema,
    WriterGenerationClass,
    WriterGenerationInvocationRefSchema,
    WriterLogicalExecutionKey,
)
from apps.enterprise_document.services.post_write import (
    ArtifactStateService,
    ArtifactStateStore,
    CurrentnessResolver,
    GateDecisionStore,
    PostWriteEntryIdentityBuilder,
    PostWriteEntryStore,
    PostWriteSqliteDatabase,
    QualityDeliveryContractRegistry,
    QualityDeliveryContractStore,
    RequirementMaterializer,
    RequirementStore,
    SectionPostWriteEntryAssembler,
    ValidationCompletionBuilder,
    ValidationRunFactory,
    ValidationRunLifecycleService,
    ValidationStore,
    ValidationSubjectKeyBuilder,
)
from apps.enterprise_document.services.post_write.gate import (
    DeliveryGateService,
    GateInputIntegrityError,
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
        return f"{prefix}:gate:{self.counter}"


class _Clock:
    def __init__(self) -> None:
        self.value = 1000.0

    def now_epoch(self) -> float:
        self.value += 1.0
        return self.value


def _writer_refs():
    subject = WriterSubjectRefSchema(
        run_id="run-gate-1",
        document_id="doc-gate-1",
        section_id="section-gate-1",
    )
    route = PreWriteRouteDecisionRefSchema(route_decision_id="route-gate-1")
    key = WriterLogicalExecutionKey(
        writer_subject_ref=subject,
        write_instance_id="write-gate-1",
        route_decision_ref=route,
    )
    execution = WriterExecutionRefSchema(
        logical_execution_key=key,
        result_sha256=SHA_A,
    )
    invocation = WriterGenerationInvocationRefSchema(
        logical_execution_key=key,
        invocation_sha256=SHA_B,
    )
    return route, execution, invocation


def _fact(
    run_id: str,
    subject_hash: str,
    *,
    fact_id: str,
    family: ValidationFactFamily,
    state: ValidationSemanticState,
    target_ref: str,
) -> ValidationFactSchema:
    base = {
        "schema_version": "validation_fact_v1",
        "contract_version": "validator-fact-v1",
        "fact_id": fact_id,
        "validation_run_id": run_id,
        "subject_hash": subject_hash,
        "producer_validator_id": "validator-gate-fixture",
        "producer_validator_version": "v1",
        "family": family,
        "target_ref": target_ref,
        "semantic_state": state,
        "payload": {"fixture": fact_id},
    }
    return ValidationFactSchema(**base, fact_sha256=canonical_sha256(base))


def _gate_fixture(
    tmp_path,
    *,
    fact_states: tuple[
        tuple[str, ValidationFactFamily, ValidationSemanticState, str], ...
    ],
    completion_states: tuple[
        tuple[str, bool, ValidationCompletionItemState, str | None], ...
    ],
    rules: tuple[FactPolicyRuleSchema, ...] = (),
    conditions: tuple[RequiredDimensionConditionSchema, ...] | None = None,
    completion_proof_provider=None,
):
    database = PostWriteSqliteDatabase(tmp_path / "post_write.sqlite3")
    route, execution, invocation = _writer_refs()

    requirement_ref = RequirementMaterializer(
        RequirementStore(database)
    ).materialize(
        semantic_contract_version="post-write-requirements-v1",
        source_business_ref="business://gate/v1",
        source_task_ref="task://gate/v1",
        requirements=(
            PostWriteRequirementEntrySchema(
                requirement_id="req-gate-1",
                requirement_kind="business",
                statement="must preserve exact supported semantics",
            ),
        ),
    )

    contract_registry = QualityDeliveryContractRegistry(
        QualityDeliveryContractStore(database)
    )
    contract_ref = contract_registry.register(
        contract_id="quality-gate-main",
        contract_version="v1",
        required_dimensions=conditions
        or (
            RequiredDimensionConditionSchema(
                dimension_id="faithfulness",
                fact_families=(ValidationFactFamily.FAITHFULNESS,),
            ),
        ),
        fact_policy_rules=rules,
    )

    artifact_service = ArtifactStateService(store=ArtifactStateStore(database))
    artifact_ref = artifact_service.create_initial(
        artifact_id="section-artifact:run-gate-1:doc-gate-1:section-gate-1",
        artifact_scope=ArtifactScope.SECTION,
        canonical_payload_ref={
            "payload_kind": "section",
            "text_sha256": SHA_A,
        },
        origin=InitialWriterArtifactOriginSchema(
            writer_execution_ref=execution,
            writer_invocation_ref=invocation,
        ),
    )
    evidence_ref = EvidenceSnapshotRefSchema(
        evidence_bundle_schema_version="evidence_bundle_v2",
        evidence_bundle_sha256=SHA_B,
        writer_invocation_ref=invocation,
    )
    evidence_binding = PostWriteEvidenceBindingRef(
        binding_kind=PostWriteEvidenceBindingKind.SECTION_WRITER_SNAPSHOT,
        section_writer_snapshot_ref=evidence_ref,
    )
    identity_builder = PostWriteEntryIdentityBuilder()
    entry_store = PostWriteEntryStore(database)
    entry_ref = SectionPostWriteEntryAssembler(
        store=entry_store,
        identity_builder=identity_builder,
    ).assemble_and_persist(
        run_id="run-gate-1",
        document_id="doc-gate-1",
        section_id="section-gate-1",
        artifact_state_ref=artifact_ref,
        writer_execution_ref=execution,
        writer_invocation_ref=invocation,
        route_decision_ref=route,
        evidence_binding=evidence_binding,
        requirement_ref=requirement_ref,
        quality_delivery_contract_ref=contract_ref,
        generation_class=WriterGenerationClass.NORMAL_WRITE,
    )
    projection = ActiveSectionBindingProjectionSchema(
        run_id="run-gate-1",
        document_id="doc-gate-1",
        section_id="section-gate-1",
        artifact_state_ref=artifact_ref,
        evidence_binding=evidence_binding,
        requirement_ref=requirement_ref,
        quality_delivery_contract_ref=contract_ref,
        writer_execution_ref=execution,
        writer_invocation_ref=invocation,
        route_decision_ref=route,
        generation_class=WriterGenerationClass.NORMAL_WRITE,
    )
    currentness = CurrentnessResolver(
        entry_store=entry_store,
        identity_builder=identity_builder,
    ).prove(
        historical_entry_ref=entry_ref,
        active_projection=projection,
        comparison_mode=ActiveComparisonMode.MATERIALIZED_ENTRY,
        active_materialized_post_write_entry_ref=entry_ref,
    )

    subject_key = ValidationSubjectKeyBuilder.build(
        post_write_entry_ref=entry_ref,
        validation_scope=ValidationScope.SECTION,
        target_selector=ValidationTargetSelectorSchema(
            selector_kind="section",
            target_ref="section-gate-1",
            selector_contract_version="selector-v1",
        ),
        subject_contract_version="subject-v1",
    )
    clock = _Clock()
    validation_store = ValidationStore(database)
    lifecycle = ValidationRunLifecycleService(
        store=validation_store,
        clock=clock,
    )
    run = ValidationRunFactory(id_generator=_Ids(), clock=clock).create(
        subject_key=subject_key,
        creation_command_id="validation-create-gate",
    )
    lifecycle.create(run)
    run = lifecycle.start(
        run.validation_run_id,
        expected_state_version=1,
        transition_command_id="validation-start-gate",
    )

    facts: dict[str, ValidationFactSchema] = {}
    refs = {}
    for fact_id, family, state, target_ref in fact_states:
        fact = _fact(
            run.validation_run_id,
            subject_key.subject_hash,
            fact_id=fact_id,
            family=family,
            state=state,
            target_ref=target_ref,
        )
        facts[fact_id] = fact
        refs[fact_id] = validation_store.put_fact_if_absent_or_same(fact)

    completion_items = []
    for expected_ref, required, state, fact_id in completion_states:
        fact_refs = ()
        if fact_id is not None:
            fact_refs = (refs[fact_id],)
        technical_failure_ref = None
        if state is ValidationCompletionItemState.TECHNICAL_FAILURE:
            technical_failure_ref = {
                "failure_kind": "MODEL_TIMEOUT",
                "failure_ref": f"failure://{expected_ref}",
            }
        completion_items.append(
            ValidationCompletionItemSchema(
                expected_validation_item_ref=expected_ref,
                required=required,
                state=state,
                fact_refs=fact_refs,
                positive_proof_refs=(
                    completion_proof_provider(validation_store, run, expected_ref)
                    if completion_proof_provider is not None and state is ValidationCompletionItemState.PASS else ()
                ),
                indeterminate_proof_refs=(
                    completion_proof_provider(validation_store, run, expected_ref)
                    if completion_proof_provider is not None and state is ValidationCompletionItemState.INDETERMINATE else ()
                ),
                technical_failure_ref=technical_failure_ref,
            )
        )
    completion = ValidationCompletionBuilder.build(
        validation_run_id=run.validation_run_id,
        subject_hash=subject_key.subject_hash,
        items=tuple(completion_items),
    )
    completion_ref = validation_store.put_completion_if_absent_or_same(
        completion
    )
    lifecycle.complete(
        run.validation_run_id,
        expected_state_version=2,
        transition_command_id="validation-complete-gate",
    )

    guard = GateInputIntegrityGuard(
        validation_store=validation_store,
        entry_store=entry_store,
        contract_store=QualityDeliveryContractStore(database),
    )
    service = DeliveryGateService(
        input_guard=guard,
        decision_store=GateDecisionStore(database),
        clock=lambda: 2000.0,
    )
    return {
        "database": database,
        "entry_ref": entry_ref,
        "artifact_ref": artifact_ref,
        "contract_ref": contract_ref,
        "currentness": currentness,
        "validation_store": validation_store,
        "run_id": run.validation_run_id,
        "subject_hash": subject_key.subject_hash,
        "completion": completion,
        "completion_ref": completion_ref,
        "facts": facts,
        "fact_refs": refs,
        "service": service,
        "guard": guard,
    }


def _evaluate(fixture):
    return fixture["service"].evaluate(
        validation_run_id=fixture["run_id"],
        validation_completion_ref=fixture["completion_ref"],
        quality_delivery_contract_ref=fixture["contract_ref"],
        currentness_proof=fixture["currentness"],
    )


def test_all_required_current_complete_and_satisfied_passes(tmp_path) -> None:
    fx = _gate_fixture(
        tmp_path,
        fact_states=(
            (
                "fact-faithful-pass",
                ValidationFactFamily.FAITHFULNESS,
                ValidationSemanticState.PASS,
                "claim-1",
            ),
        ),
        completion_states=(
            (
                "faithfulness:item-1",
                True,
                ValidationCompletionItemState.PASS,
                "fact-faithful-pass",
            ),
        ),
    )
    decision = _evaluate(fx)

    assert decision.overall_quality_state.value == "PASS"
    assert decision.blocking_fact_refs == ()
    assert decision.currentness_proof.currentness_state.value == "CURRENT"
    assert decision.gate_input_manifest.validation_fact_refs == (
        fx["fact_refs"]["fact-faithful-pass"],
    )


def test_explicit_contract_blocker_produces_fail(tmp_path) -> None:
    rule = FactPolicyRuleSchema(
        policy_rule_ref="rule:block-faithfulness-fail",
        fact_family=ValidationFactFamily.FAITHFULNESS,
        semantic_states=(ValidationSemanticState.FAIL,),
        effect=QualityPolicyEffect.BLOCKING,
        reason_code="FAITHFULNESS_FAIL_BLOCKS",
    )
    fx = _gate_fixture(
        tmp_path,
        fact_states=(
            (
                "fact-faithful-fail",
                ValidationFactFamily.FAITHFULNESS,
                ValidationSemanticState.FAIL,
                "claim-1",
            ),
        ),
        completion_states=(
            (
                "faithfulness:item-1",
                True,
                ValidationCompletionItemState.FAIL,
                "fact-faithful-fail",
            ),
        ),
        rules=(rule,),
    )
    decision = _evaluate(fx)

    assert decision.overall_quality_state.value == "FAIL"
    assert decision.blocking_fact_refs == (
        fx["fact_refs"]["fact-faithful-fail"],
    )


def test_proven_blocker_survives_unresolved_sibling(tmp_path) -> None:
    rule = FactPolicyRuleSchema(
        policy_rule_ref="rule:block-faithfulness-fail",
        fact_family=ValidationFactFamily.FAITHFULNESS,
        semantic_states=(ValidationSemanticState.FAIL,),
        effect=QualityPolicyEffect.BLOCKING,
        reason_code="FAITHFULNESS_FAIL_BLOCKS",
    )
    fx = _gate_fixture(
        tmp_path,
        fact_states=(
            (
                "fact-faithful-fail",
                ValidationFactFamily.FAITHFULNESS,
                ValidationSemanticState.FAIL,
                "claim-1",
            ),
        ),
        completion_states=(
            (
                "faithfulness:item-1",
                True,
                ValidationCompletionItemState.FAIL,
                "fact-faithful-fail",
            ),
            (
                "citation:item-1",
                True,
                ValidationCompletionItemState.TECHNICAL_FAILURE,
                None,
            ),
        ),
        rules=(rule,),
        conditions=(
            RequiredDimensionConditionSchema(
                dimension_id="faithfulness",
                fact_families=(ValidationFactFamily.FAITHFULNESS,),
            ),
            RequiredDimensionConditionSchema(
                dimension_id="citation",
                fact_families=(ValidationFactFamily.CITATION_INTEGRITY,),
            ),
        ),
    )
    decision = _evaluate(fx)

    assert decision.overall_quality_state.value == "FAIL"
    assert any(
        reason.startswith("COMPLETION_TECHNICAL_FAILURE:")
        for reason in decision.unresolved_reason_refs
    )


def test_no_blocker_with_required_not_run_is_unresolved(tmp_path) -> None:
    fx = _gate_fixture(
        tmp_path,
        fact_states=(
            (
                "fact-faithful-pass",
                ValidationFactFamily.FAITHFULNESS,
                ValidationSemanticState.PASS,
                "claim-1",
            ),
        ),
        completion_states=(
            (
                "faithfulness:item-1",
                True,
                ValidationCompletionItemState.PASS,
                "fact-faithful-pass",
            ),
            (
                "citation:item-1",
                True,
                ValidationCompletionItemState.NOT_RUN,
                None,
            ),
        ),
        conditions=(
            RequiredDimensionConditionSchema(
                dimension_id="faithfulness",
                fact_families=(ValidationFactFamily.FAITHFULNESS,),
            ),
        ),
    )
    decision = _evaluate(fx)
    assert decision.overall_quality_state.value == "UNRESOLVED"


def test_warning_only_issue_can_pass_with_warning_ref(tmp_path) -> None:
    warning_rule = FactPolicyRuleSchema(
        policy_rule_ref="rule:warn-citation",
        fact_family=ValidationFactFamily.CITATION_INTEGRITY,
        semantic_states=(ValidationSemanticState.FAIL,),
        effect=QualityPolicyEffect.WARNING,
        reason_code="CITATION_WARNING_ONLY",
    )
    fx = _gate_fixture(
        tmp_path,
        fact_states=(
            (
                "fact-faithful-pass",
                ValidationFactFamily.FAITHFULNESS,
                ValidationSemanticState.PASS,
                "claim-1",
            ),
            (
                "fact-citation-fail",
                ValidationFactFamily.CITATION_INTEGRITY,
                ValidationSemanticState.FAIL,
                "citation-1",
            ),
        ),
        completion_states=(
            (
                "faithfulness:item-1",
                True,
                ValidationCompletionItemState.PASS,
                "fact-faithful-pass",
            ),
            (
                "citation:item-1",
                False,
                ValidationCompletionItemState.FAIL,
                "fact-citation-fail",
            ),
        ),
        rules=(warning_rule,),
        conditions=(
            RequiredDimensionConditionSchema(
                dimension_id="faithfulness",
                fact_families=(ValidationFactFamily.FAITHFULNESS,),
            ),
        ),
    )
    decision = _evaluate(fx)

    assert decision.overall_quality_state.value == "PASS"
    assert fx["fact_refs"]["fact-citation-fail"] in decision.warning_fact_refs


def test_stale_trusted_currentness_overrides_blocker_to_unresolved(tmp_path) -> None:
    rule = FactPolicyRuleSchema(
        policy_rule_ref="rule:block-faithfulness-fail",
        fact_family=ValidationFactFamily.FAITHFULNESS,
        semantic_states=(ValidationSemanticState.FAIL,),
        effect=QualityPolicyEffect.BLOCKING,
        reason_code="FAITHFULNESS_FAIL_BLOCKS",
    )
    fx = _gate_fixture(
        tmp_path,
        fact_states=(
            (
                "fact-faithful-fail",
                ValidationFactFamily.FAITHFULNESS,
                ValidationSemanticState.FAIL,
                "claim-1",
            ),
        ),
        completion_states=(
            (
                "faithfulness:item-1",
                True,
                ValidationCompletionItemState.FAIL,
                "fact-faithful-fail",
            ),
        ),
        rules=(rule,),
    )
    stale = fx["currentness"].model_copy(
        update={
            "currentness_state": CurrentnessState.STALE,
            "match_state": CurrentnessMatchState.IDENTITY_MISMATCH,
        }
    )
    payload = stale.model_dump(mode="json")
    payload.pop("proof_sha256", None)
    stale = stale.model_copy(update={"proof_sha256": canonical_sha256(payload)})

    decision = fx["service"].evaluate(
        validation_run_id=fx["run_id"],
        validation_completion_ref=fx["completion_ref"],
        quality_delivery_contract_ref=fx["contract_ref"],
        currentness_proof=stale,
    )
    assert decision.overall_quality_state.value == "UNRESOLVED"
    assert "CURRENTNESS_STALE" in decision.unresolved_reason_refs


def test_gate_enumerates_full_fact_manifest_not_caller_subset(tmp_path) -> None:
    rule = FactPolicyRuleSchema(
        policy_rule_ref="rule:block-citation",
        fact_family=ValidationFactFamily.CITATION_INTEGRITY,
        semantic_states=(ValidationSemanticState.FAIL,),
        effect=QualityPolicyEffect.BLOCKING,
        reason_code="CITATION_FAIL_BLOCKS",
    )
    fx = _gate_fixture(
        tmp_path,
        fact_states=(
            (
                "fact-faithful-pass",
                ValidationFactFamily.FAITHFULNESS,
                ValidationSemanticState.PASS,
                "claim-1",
            ),
            (
                "fact-citation-fail",
                ValidationFactFamily.CITATION_INTEGRITY,
                ValidationSemanticState.FAIL,
                "citation-1",
            ),
        ),
        completion_states=(
            (
                "faithfulness:item-1",
                True,
                ValidationCompletionItemState.PASS,
                "fact-faithful-pass",
            ),
            (
                "citation:item-1",
                False,
                ValidationCompletionItemState.FAIL,
                "fact-citation-fail",
            ),
        ),
        rules=(rule,),
        conditions=(
            RequiredDimensionConditionSchema(
                dimension_id="faithfulness",
                fact_families=(ValidationFactFamily.FAITHFULNESS,),
            ),
        ),
    )
    decision = _evaluate(fx)

    ids = [
        ref.fact_id for ref in decision.gate_input_manifest.validation_fact_refs
    ]
    assert ids == ["fact-citation-fail", "fact-faithful-pass"]
    assert decision.overall_quality_state.value == "FAIL"


def test_corrupt_currentness_is_technical_failure_and_writes_no_decision(tmp_path) -> None:
    fx = _gate_fixture(
        tmp_path,
        fact_states=(
            (
                "fact-faithful-pass",
                ValidationFactFamily.FAITHFULNESS,
                ValidationSemanticState.PASS,
                "claim-1",
            ),
        ),
        completion_states=(
            (
                "faithfulness:item-1",
                True,
                ValidationCompletionItemState.PASS,
                "fact-faithful-pass",
            ),
        ),
    )
    corrupt = fx["currentness"].model_copy(
        update={"proof_sha256": "0" * 64}
    )
    with pytest.raises(GateInputIntegrityError, match="CurrentnessProof"):
        fx["service"].evaluate(
            validation_run_id=fx["run_id"],
            validation_completion_ref=fx["completion_ref"],
            quality_delivery_contract_ref=fx["contract_ref"],
            currentness_proof=corrupt,
        )

    with fx["database"].read_transaction() as connection:
        assert connection.execute(
            "SELECT COUNT(*) FROM delivery_gate_decisions"
        ).fetchone()[0] == 0


def test_same_exact_gate_input_is_idempotent_and_replay_does_not_reaggregate(
    tmp_path,
) -> None:
    fx = _gate_fixture(
        tmp_path,
        fact_states=(
            (
                "fact-faithful-pass",
                ValidationFactFamily.FAITHFULNESS,
                ValidationSemanticState.PASS,
                "claim-1",
            ),
        ),
        completion_states=(
            (
                "faithfulness:item-1",
                True,
                ValidationCompletionItemState.PASS,
                "fact-faithful-pass",
            ),
        ),
    )
    first = _evaluate(fx)
    second = _evaluate(fx)

    assert first == second
    assert first.as_ref() == second.as_ref()
    assert fx["service"].resolve_exact(first.as_ref()) == first
    with fx["database"].read_transaction() as connection:
        assert connection.execute(
            "SELECT COUNT(*) FROM delivery_gate_decisions"
        ).fetchone()[0] == 1


def test_ambiguous_contract_policy_is_technical_failure_and_writes_no_decision(
    tmp_path,
) -> None:
    rules = (
        FactPolicyRuleSchema(
            policy_rule_ref="rule:ambiguous:block",
            fact_family=ValidationFactFamily.FAITHFULNESS,
            semantic_states=(ValidationSemanticState.FAIL,),
            effect=QualityPolicyEffect.BLOCKING,
            reason_code="BLOCK",
        ),
        FactPolicyRuleSchema(
            policy_rule_ref="rule:ambiguous:warn",
            fact_family=ValidationFactFamily.FAITHFULNESS,
            semantic_states=(ValidationSemanticState.FAIL,),
            effect=QualityPolicyEffect.WARNING,
            reason_code="WARN",
        ),
    )
    fx = _gate_fixture(
        tmp_path,
        fact_states=(
            (
                "fact-faithful-fail",
                ValidationFactFamily.FAITHFULNESS,
                ValidationSemanticState.FAIL,
                "claim-1",
            ),
        ),
        completion_states=(
            (
                "faithfulness:item-1",
                True,
                ValidationCompletionItemState.FAIL,
                "fact-faithful-fail",
            ),
        ),
        rules=rules,
    )

    with pytest.raises(GateInputIntegrityError, match="ambiguous policy"):
        _evaluate(fx)

    with fx["database"].read_transaction() as connection:
        assert connection.execute(
            "SELECT COUNT(*) FROM delivery_gate_decisions"
        ).fetchone()[0] == 0


def test_missing_completion_fact_is_technical_failure_and_writes_no_decision(
    tmp_path,
) -> None:
    fx = _gate_fixture(
        tmp_path,
        fact_states=(
            (
                "fact-faithful-pass",
                ValidationFactFamily.FAITHFULNESS,
                ValidationSemanticState.PASS,
                "claim-1",
            ),
        ),
        completion_states=(
            (
                "faithfulness:item-1",
                True,
                ValidationCompletionItemState.PASS,
                "fact-faithful-pass",
            ),
        ),
    )
    with fx["database"].write_transaction(immediate=True) as connection:
        connection.execute(
            "DELETE FROM validation_facts WHERE fact_id=?",
            ("fact-faithful-pass",),
        )

    with pytest.raises(GateInputIntegrityError, match="missing/conflicting Fact"):
        _evaluate(fx)

    with fx["database"].read_transaction() as connection:
        assert connection.execute(
            "SELECT COUNT(*) FROM delivery_gate_decisions"
        ).fetchone()[0] == 0


def test_currentness_material_change_creates_new_gate_decision_identity(
    tmp_path,
) -> None:
    fx = _gate_fixture(
        tmp_path,
        fact_states=(
            (
                "fact-faithful-pass",
                ValidationFactFamily.FAITHFULNESS,
                ValidationSemanticState.PASS,
                "claim-1",
            ),
        ),
        completion_states=(
            (
                "faithfulness:item-1",
                True,
                ValidationCompletionItemState.PASS,
                "fact-faithful-pass",
            ),
        ),
    )
    current = _evaluate(fx)
    stale = fx["currentness"].model_copy(
        update={
            "currentness_state": CurrentnessState.STALE,
            "match_state": CurrentnessMatchState.IDENTITY_MISMATCH,
        }
    )
    payload = stale.model_dump(mode="json")
    payload.pop("proof_sha256", None)
    stale = stale.model_copy(update={"proof_sha256": canonical_sha256(payload)})
    historical = fx["service"].evaluate(
        validation_run_id=fx["run_id"],
        validation_completion_ref=fx["completion_ref"],
        quality_delivery_contract_ref=fx["contract_ref"],
        currentness_proof=stale,
    )

    assert current.delivery_gate_decision_id != historical.delivery_gate_decision_id
    assert current.overall_quality_state.value == "PASS"
    assert historical.overall_quality_state.value == "UNRESOLVED"
    with fx["database"].read_transaction() as connection:
        assert connection.execute(
            "SELECT COUNT(*) FROM delivery_gate_decisions"
        ).fetchone()[0] == 2


def test_gate_does_not_read_raw_artifact_or_evidence_payload(tmp_path) -> None:
    fx = _gate_fixture(
        tmp_path,
        fact_states=(
            (
                "fact-faithful-pass",
                ValidationFactFamily.FAITHFULNESS,
                ValidationSemanticState.PASS,
                "claim-1",
            ),
        ),
        completion_states=(
            (
                "faithfulness:item-1",
                True,
                ValidationCompletionItemState.PASS,
                "fact-faithful-pass",
            ),
        ),
    )
    decision = _evaluate(fx)

    manifest = decision.gate_input_manifest
    assert manifest.artifact_state_ref == fx["artifact_ref"]
    assert not hasattr(manifest, "artifact_payload")
    assert not hasattr(manifest, "evidence_payload")


def test_post_write_v1_database_migrates_exactly_to_current_version(tmp_path) -> None:
    from apps.enterprise_document.services.post_write import persistence as p

    path = tmp_path / "post_write_v1.sqlite3"
    v3_tables = {
        "correction_policies",
        "post_write_control_decisions",
        "correction_attempt_projections",
        "document_binding_values",
    }
    v3_indexes = {
        "idx_post_write_control_decisions_run",
        "idx_correction_attempt_projections_run",
    }

    connection = sqlite3.connect(path)
    try:
        connection.execute(p._METADATA_DDL)
        for table, ddl in p._TABLE_DDL.items():
            if table == "delivery_gate_decisions" or table in v3_tables:
                continue
            connection.execute(ddl)
        for name, ddl in p._INDEX_DDL.items():
            if name == "idx_delivery_gate_decisions_run" or name in v3_indexes:
                continue
            connection.execute(ddl)
        connection.execute(
            "INSERT INTO post_write_metadata(key,value) VALUES(?,?)",
            ("schema_version", p.LEGACY_POST_WRITE_SCHEMA_VERSION),
        )
        connection.commit()
    finally:
        connection.close()

    database = PostWriteSqliteDatabase(path)
    with database.read_transaction() as connection:
        assert (
            connection.execute(
                "SELECT value FROM post_write_metadata WHERE key='schema_version'"
            ).fetchone()[0]
            == p.POST_WRITE_SCHEMA_VERSION
        )
        tables = {
            row[0]
            for row in connection.execute(
                "SELECT name FROM sqlite_master "
                "WHERE type='table' AND name NOT LIKE 'sqlite_%'"
            )
        }
    assert "delivery_gate_decisions" in tables
    assert v3_tables.issubset(tables)
