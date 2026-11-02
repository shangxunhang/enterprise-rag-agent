from __future__ import annotations

import json
import sqlite3

import pytest

from apps.enterprise_document.schemas.post_write import (
    ArtifactScope,
    ArtifactStateRefSchema,
    ArtifactVisibleProjectionRefSchema,
    ExpectedItemExecutionAccountingSchema,
    ExpectedItemExecutionState,
    InputEnvelopeSchema,
    LogicalConsumerBindingSchema,
    PhysicalModelCallRefSchema,
    PostWriteEntryRefSchema,
    RequiredDimensionConditionSchema,
    StageDescriptorSchema,
    StagePlanSchema,
    ValidationCompletionItemSchema,
    ValidationCompletionItemState,
    ValidationExecutionEventSchema,
    ValidationFactFamily,
    ValidationFactSchema,
    ValidationIntermediateKind,
    ValidationIntermediateRefSchema,
    ValidationRunState,
    ValidationScope,
    ValidationSemanticState,
    ValidationTargetSelectorSchema,
    ValidatorExecutionRecordSchema,
    ValidatorTerminalExecutionState,
)
from apps.enterprise_document.services.post_write import (
    ModelCallCompatibilityGuard,
    PostWriteConcurrencyError,
    PostWritePersistenceIntegrityError,
    PostWriteSqliteDatabase,
    QualityDeliveryContractRegistry,
    QualityDeliveryContractResolver,
    QualityDeliveryContractStore,
    StagePlanConformanceGuard,
    ValidationCompletionBuilder,
    ValidationIntermediateRuntime,
    ValidationRunFactory,
    ValidationRunLifecycleService,
    ValidationStore,
    ValidationSubjectKeyBuilder,
)
from core.hashing import canonical_sha256


SHA_A = "a" * 64
SHA_B = "b" * 64


class _Ids:
    def __init__(self) -> None:
        self.value = 0

    def new_id(self, prefix: str) -> str:
        self.value += 1
        return f"{prefix}:{self.value}"


class _Clock:
    def __init__(self) -> None:
        self.value = 1000.0

    def now_epoch(self) -> float:
        self.value += 1.0
        return self.value


def _entry_ref(suffix: str = "1") -> PostWriteEntryRefSchema:
    return PostWriteEntryRefSchema(
        entry_id=f"entry-{suffix}",
        entry_schema_version="section_post_write_entry_v1",
        entry_contract_version="post_write_entry_binding_v1",
        entry_sha256=SHA_A if suffix == "1" else SHA_B,
    )


def _subject(entry=None, scope=ValidationScope.SECTION, target="section-1", contract="v1"):
    return ValidationSubjectKeyBuilder.build(
        post_write_entry_ref=entry or _entry_ref(),
        validation_scope=scope,
        target_selector=ValidationTargetSelectorSchema(
            selector_kind="section",
            target_ref=target,
            selector_contract_version="selector-v1",
        ),
        subject_contract_version=contract,
    )


def _database(tmp_path) -> PostWriteSqliteDatabase:
    return PostWriteSqliteDatabase(tmp_path / "post_write.sqlite3")


def _created_run(tmp_path, *, command_id="create-1"):
    database = _database(tmp_path)
    store = ValidationStore(database)
    clock = _Clock()
    factory = ValidationRunFactory(id_generator=_Ids(), clock=clock)
    run = factory.create(subject_key=_subject(), creation_command_id=command_id)
    lifecycle = ValidationRunLifecycleService(store=store, clock=clock)
    lifecycle.create(run)
    return database, store, lifecycle, run


def _fact(run_id: str, subject_hash: str, *, state=ValidationSemanticState.FAIL):
    base = {
        "schema_version": "validation_fact_v1",
        "contract_version": "validator-fact-v1",
        "fact_id": f"fact:{run_id}:item-1",
        "validation_run_id": run_id,
        "subject_hash": subject_hash,
        "producer_validator_id": "validator-a",
        "producer_validator_version": "v1",
        "family": ValidationFactFamily.REQUIREMENT_ADHERENCE,
        "target_ref": "item-1",
        "semantic_state": state,
        "payload": {"reason": "synthetic"},
    }
    return ValidationFactSchema(**base, fact_sha256=canonical_sha256(base))


def _stage_plan():
    stage = StageDescriptorSchema(
        stage_ref="stage:1",
        stage_version="v1",
        sequence_index=0,
        stage_input_schema_ref="schema://input/v1",
        stage_output_schema_ref="schema://output/v1",
        execution_policy_ref="policy://technical/v1",
        execution_policy_version="v1",
        timeout_seconds=10,
        max_attempts=2,
        retryable_failure_categories=("TIMEOUT",),
    )
    base = {
        "schema_version": "validator_stage_plan_v1",
        "stage_plan_ref": "stage-plan:validator-a:v1",
        "validator_id": "validator-a",
        "validator_version": "v1",
        "stage_plan_version": "v1",
        "stages": [stage.model_dump(mode="json")],
    }
    return StagePlanSchema(
        stage_plan_ref=base["stage_plan_ref"],
        validator_id="validator-a",
        validator_version="v1",
        stage_plan_version="v1",
        stages=(stage,),
        stage_plan_hash=canonical_sha256(base),
    )


def _input_envelope(run_id: str, subject_hash: str, plan: StagePlanSchema, expected_item_ref="item-1"):
    artifact_state_ref = ArtifactStateRefSchema(
        artifact_id="section-artifact:validation-fixture",
        artifact_version=1,
        artifact_hash=SHA_A,
        artifact_scope=ArtifactScope.SECTION,
    )
    projection_ref = ArtifactVisibleProjectionRefSchema(
        projection_id="artifact-visible://fixture/1",
        projection_schema_version="artifact_visible_projection_v1",
        projection_contract_version="artifact_visible_projection_v1",
        projection_sha256=SHA_B,
    )
    material = {
        "validation_run_id": run_id,
        "subject_hash": subject_hash,
        "artifact_state_ref": artifact_state_ref.model_dump(mode="json"),
        "artifact_visible_projection_ref": projection_ref.model_dump(mode="json"),
        "artifact_visible_projection_sha256": projection_ref.projection_sha256,
        "validator_id": plan.validator_id,
        "validator_version": plan.validator_version,
        "stage_plan_ref": plan.stage_plan_ref,
        "stage_plan_version": plan.stage_plan_version,
        "stage_plan_hash": plan.stage_plan_hash,
        "applicable_expected_validation_item_refs": [expected_item_ref],
        "exact_authority_refs": ["entry://1"],
        "execution_config_refs": ["config://technical/v1"],
        "execution_contract_version": "validator-execution-v1",
    }
    return InputEnvelopeSchema(
        execution_contract_version="validator-execution-v1",
        input_envelope_ref="input-envelope:1",
        validation_run_id=run_id,
        subject_hash=subject_hash,
        artifact_state_ref=artifact_state_ref,
        artifact_visible_projection_ref=projection_ref,
        artifact_visible_projection_sha256=projection_ref.projection_sha256,
        validator_id=plan.validator_id,
        validator_version=plan.validator_version,
        stage_plan_ref=plan.stage_plan_ref,
        stage_plan_version=plan.stage_plan_version,
        stage_plan_hash=plan.stage_plan_hash,
        applicable_expected_validation_item_refs=(expected_item_ref,),
        exact_authority_refs=("entry://1",),
        execution_config_refs=("config://technical/v1",),
        input_hash=canonical_sha256(material),
    )


def test_subject_identity_is_deterministic_and_changes_on_material_binding() -> None:
    first = _subject()
    assert first == _subject()
    assert first.subject_hash != _subject(entry=_entry_ref("2")).subject_hash
    assert first.subject_hash != _subject(target="section-2").subject_hash
    assert first.subject_hash != _subject(contract="v2").subject_hash
    assert first.subject_hash != _subject(scope=ValidationScope.DOCUMENT).subject_hash


def test_new_physical_execution_of_same_subject_gets_new_run_id(tmp_path) -> None:
    factory = ValidationRunFactory(id_generator=_Ids(), clock=_Clock())
    subject = _subject()
    first = factory.create(subject_key=subject, creation_command_id="c1")
    second = factory.create(subject_key=subject, creation_command_id="c2")
    assert first.validation_run_id != second.validation_run_id
    assert first.subject_key == second.subject_key
    assert first.execution_state is ValidationRunState.CREATED
    assert first.state_version == 1


def test_run_creation_and_event_are_atomic_and_creation_replay_survives_state_advance(
    tmp_path,
) -> None:
    database, store, lifecycle, run = _created_run(tmp_path)
    running = lifecycle.start(
        run.validation_run_id,
        expected_state_version=1,
        transition_command_id="start-1",
    )
    replay = store.create_run_with_event_if_absent_or_same(
        run,
        run.creation_command_id,
    )
    assert replay == running

    with database.read_transaction() as connection:
        events = connection.execute(
            "SELECT event_kind,run_event_sequence FROM validation_execution_events "
            "WHERE validation_run_id=? ORDER BY run_event_sequence",
            (run.validation_run_id,),
        ).fetchall()
    assert [(row[0], row[1]) for row in events] == [
        ("RUN_CREATED", 1),
        ("RUN_RUNNING", 2),
    ]


def test_run_creation_fault_after_row_insert_rolls_back_row_and_event(
    tmp_path, monkeypatch
) -> None:
    database = _database(tmp_path)
    store = ValidationStore(database)
    run = ValidationRunFactory(id_generator=_Ids(), clock=_Clock()).create(
        subject_key=_subject(),
        creation_command_id="create-fault",
    )
    original = ValidationStore._insert_event

    def fail_after_event(connection, event):
        original(connection, event)
        raise RuntimeError("fault after event insert")

    monkeypatch.setattr(store, "_insert_event", fail_after_event)
    with pytest.raises(RuntimeError, match="fault"):
        store.create_run_with_event_if_absent_or_same(run, run.creation_command_id)

    connection = sqlite3.connect(database.path)
    try:
        assert connection.execute(
            "SELECT COUNT(*) FROM validation_runs"
        ).fetchone()[0] == 0
        assert connection.execute(
            "SELECT COUNT(*) FROM validation_execution_events"
        ).fetchone()[0] == 0
    finally:
        connection.close()


def test_run_transition_cas_and_terminal_prerequisites(tmp_path) -> None:
    _database_obj, store, lifecycle, run = _created_run(tmp_path)
    running = lifecycle.start(
        run.validation_run_id,
        expected_state_version=1,
        transition_command_id="start",
    )
    assert running.state_version == 2

    with pytest.raises(PostWriteConcurrencyError):
        lifecycle.complete(
            run.validation_run_id,
            expected_state_version=1,
            transition_command_id="complete-stale",
        )
    with pytest.raises(PostWritePersistenceIntegrityError, match="Completion"):
        lifecycle.complete(
            run.validation_run_id,
            expected_state_version=2,
            transition_command_id="complete-no-completion",
        )

    fact_ref = store.put_fact_if_absent_or_same(
        _fact(run.validation_run_id, run.subject_key.subject_hash)
    )
    completion = ValidationCompletionBuilder.build(
        validation_run_id=run.validation_run_id,
        subject_hash=run.subject_key.subject_hash,
        items=(
            ValidationCompletionItemSchema(
                expected_validation_item_ref="item-1",
                required=True,
                state=ValidationCompletionItemState.FAIL,
                fact_refs=(fact_ref,),
            ),
        ),
    )
    completion_ref = store.put_completion_if_absent_or_same(completion)
    completed = lifecycle.complete(
        run.validation_run_id,
        expected_state_version=2,
        transition_command_id="complete",
    )
    assert completed.execution_state is ValidationRunState.COMPLETED
    assert completed.state_version == 3
    # Execution completed even though semantic quality state is FAIL.
    assert completion.items[0].state is ValidationCompletionItemState.FAIL

    with store.database.read_transaction() as connection:
        row = connection.execute(
            "SELECT * FROM validation_execution_events "
            "WHERE validation_run_id=? AND event_kind='RUN_COMPLETED'",
            (run.validation_run_id,),
        ).fetchone()
    event = ValidationExecutionEventSchema.model_validate_json(row["event_json"])
    assert event.payload["completion_ref"] == completion_ref.model_dump(mode="json")


def test_transition_command_is_idempotent_and_collision_fails(tmp_path) -> None:
    _database_obj, store, lifecycle, run = _created_run(tmp_path)
    first = lifecycle.start(
        run.validation_run_id,
        expected_state_version=1,
        transition_command_id="same-command",
    )
    replay = lifecycle.start(
        run.validation_run_id,
        expected_state_version=1,
        transition_command_id="same-command",
    )
    assert replay == first
    with pytest.raises(PostWritePersistenceIntegrityError, match="collision"):
        store.transition_run(
            run.validation_run_id,
            1,
            "same-command",
            ValidationRunState.TECHNICAL_FAILURE,
            {"failure_kind": "timeout", "ref": "error://1"},
        )


def test_completion_preserves_not_applicable_not_run_and_technical_failure() -> None:
    completion = ValidationCompletionBuilder.build(
        validation_run_id="run-1",
        subject_hash=SHA_A,
        items=(
            ValidationCompletionItemSchema(
                expected_validation_item_ref="a",
                required=False,
                state=ValidationCompletionItemState.NOT_APPLICABLE,
            ),
            ValidationCompletionItemSchema(
                expected_validation_item_ref="b",
                required=True,
                state=ValidationCompletionItemState.NOT_RUN,
            ),
            ValidationCompletionItemSchema(
                expected_validation_item_ref="c",
                required=True,
                state=ValidationCompletionItemState.TECHNICAL_FAILURE,
                technical_failure_ref={"failure_kind": "timeout"},
            ),
        ),
    )
    states = {item.expected_validation_item_ref: item.state for item in completion.items}
    assert states["a"] is ValidationCompletionItemState.NOT_APPLICABLE
    assert states["b"] is ValidationCompletionItemState.NOT_RUN
    assert states["c"] is ValidationCompletionItemState.TECHNICAL_FAILURE


def test_intermediate_is_event_backed_and_exact_hash_resolvable(tmp_path) -> None:
    _database_obj, store, _lifecycle, run = _created_run(tmp_path)
    payload = {"candidate_ids": ["c1", "c2"]}
    ref = ValidationIntermediateRefSchema(
        validation_run_id=run.validation_run_id,
        event_id="intermediate-1",
        intermediate_kind=ValidationIntermediateKind.GENERIC,
        intermediate_sha256=canonical_sha256(payload),
    )
    store.append_intermediate_if_absent_or_same(
        run.validation_run_id,
        "intermediate-1",
        payload,
        ref,
        producer_ref="validator-a",
        target_ref="item-1",
    )
    assert store.resolve_intermediate(ref) == payload


def test_validator_terminal_event_and_record_are_atomic(tmp_path, monkeypatch) -> None:
    database, store, lifecycle, run = _created_run(tmp_path)
    lifecycle.start(
        run.validation_run_id,
        expected_state_version=1,
        transition_command_id="start",
    )
    fact = _fact(run.validation_run_id, run.subject_key.subject_hash)
    fact_ref = store.put_fact_if_absent_or_same(fact)
    plan = _stage_plan()
    envelope = _input_envelope(
        run.validation_run_id,
        run.subject_key.subject_hash,
        plan,
    )
    event = store._event(
        validation_run_id=run.validation_run_id,
        event_id="validator-terminal-1",
        sequence=3,
        event_kind="VALIDATOR_COMPLETED",
        payload={"validator_execution_ref": "validator-exec-1"},
        occurred_at_epoch=2000.0,
    )
    record_base = {
        "schema_version": "validator_execution_record_v1",
        "validator_execution_ref": "validator-exec-1",
        "validation_run_id": run.validation_run_id,
        "subject_hash": run.subject_key.subject_hash,
        "validator_id": "validator-a",
        "validator_version": "v1",
        "stage_plan_ref": plan.stage_plan_ref,
        "stage_plan_version": plan.stage_plan_version,
        "input_hash": envelope.input_hash,
        "terminal_event_id": event.event_id,
        "terminal_execution_state": ValidatorTerminalExecutionState.COMPLETED,
        "expected_item_accounting": [
            ExpectedItemExecutionAccountingSchema(
                expected_validation_item_ref="item-1",
                execution_state=ExpectedItemExecutionState.COMPLETED,
                fact_refs=(fact_ref,),
            ).model_dump(mode="json")
        ],
        "produced_fact_refs": [fact_ref.model_dump(mode="json")],
        "intermediate_refs": [],
    }
    record = ValidatorExecutionRecordSchema(
        **record_base,
        record_sha256=canonical_sha256(record_base),
    )

    original_execute = sqlite3.Connection.execute
    # sqlite3.Connection methods are immutable on CPython; inject failure after
    # event through the store helper instead.
    original_insert_event = ValidationStore._insert_event

    def insert_then_fail(connection, terminal_event):
        original_insert_event(connection, terminal_event)
        raise RuntimeError("fault between terminal event and record")

    monkeypatch.setattr(store, "_insert_event", insert_then_fail)
    with pytest.raises(RuntimeError, match="fault between"):
        store.finalize_validator_execution(
            event,
            record,
            stage_plan=plan,
            input_envelope=envelope,
        )

    connection = sqlite3.connect(database.path)
    try:
        assert connection.execute(
            "SELECT COUNT(*) FROM validation_execution_events "
            "WHERE event_id='validator-terminal-1'"
        ).fetchone()[0] == 0
        assert connection.execute(
            "SELECT COUNT(*) FROM validator_execution_records "
            "WHERE validator_execution_ref='validator-exec-1'"
        ).fetchone()[0] == 0
    finally:
        connection.close()


def test_validation_write_replay_fails_closed_on_row_metadata_drift(
    tmp_path,
) -> None:
    database, store, _lifecycle, run = _created_run(tmp_path)
    fact = _fact(run.validation_run_id, run.subject_key.subject_hash)
    store.put_fact_if_absent_or_same(fact)

    connection = sqlite3.connect(database.path)
    try:
        connection.execute(
            "UPDATE validation_facts SET fact_contract_version='tampered' "
            "WHERE fact_id=?",
            (fact.fact_id,),
        )
        connection.commit()
    finally:
        connection.close()
    with pytest.raises(PostWritePersistenceIntegrityError, match="row/hash"):
        store.put_fact_if_absent_or_same(fact)

    completion = ValidationCompletionBuilder.build(
        validation_run_id=run.validation_run_id,
        subject_hash=run.subject_key.subject_hash,
        items=(
            ValidationCompletionItemSchema(
                expected_validation_item_ref="item-2",
                required=True,
                state=ValidationCompletionItemState.NOT_RUN,
            ),
        ),
    )
    store.put_completion_if_absent_or_same(completion)
    connection = sqlite3.connect(database.path)
    try:
        connection.execute(
            "UPDATE validation_completions "
            "SET completion_contract_version='tampered' "
            "WHERE validation_run_id=?",
            (run.validation_run_id,),
        )
        connection.commit()
    finally:
        connection.close()
    with pytest.raises(PostWritePersistenceIntegrityError, match="row/hash"):
        store.put_completion_if_absent_or_same(completion)


def test_validator_terminal_finalization_validates_refs_and_exact_pair(tmp_path) -> None:
    database, store, lifecycle, run = _created_run(tmp_path)
    lifecycle.start(
        run.validation_run_id,
        expected_state_version=1,
        transition_command_id="start",
    )
    fact = _fact(run.validation_run_id, run.subject_key.subject_hash)
    fact_ref = store.put_fact_if_absent_or_same(fact)
    plan = _stage_plan()
    envelope = _input_envelope(
        run.validation_run_id,
        run.subject_key.subject_hash,
        plan,
    )
    event = store._event(
        validation_run_id=run.validation_run_id,
        event_id="validator-terminal-exact",
        sequence=3,
        event_kind="VALIDATOR_COMPLETED",
        payload={"validator_execution_ref": "validator-exec-exact"},
        occurred_at_epoch=2000.0,
    )
    record_base = {
        "schema_version": "validator_execution_record_v1",
        "validator_execution_ref": "validator-exec-exact",
        "validation_run_id": run.validation_run_id,
        "subject_hash": run.subject_key.subject_hash,
        "validator_id": plan.validator_id,
        "validator_version": plan.validator_version,
        "stage_plan_ref": plan.stage_plan_ref,
        "stage_plan_version": plan.stage_plan_version,
        "input_hash": envelope.input_hash,
        "terminal_event_id": event.event_id,
        "terminal_execution_state": ValidatorTerminalExecutionState.COMPLETED,
        "expected_item_accounting": [
            ExpectedItemExecutionAccountingSchema(
                expected_validation_item_ref="item-1",
                execution_state=ExpectedItemExecutionState.COMPLETED,
                fact_refs=(fact_ref,),
            ).model_dump(mode="json")
        ],
        "produced_fact_refs": [fact_ref.model_dump(mode="json")],
        "intermediate_refs": [],
    }
    record = ValidatorExecutionRecordSchema(
        **record_base,
        record_sha256=canonical_sha256(record_base),
    )

    stored = store.finalize_validator_execution(
        event,
        record,
        stage_plan=plan,
        input_envelope=envelope,
    )
    assert stored == record
    assert store.resolve_validator_execution_record(record.validator_execution_ref) == record

    connection = sqlite3.connect(database.path)
    try:
        connection.execute(
            "UPDATE validator_execution_records SET validator_version='tampered' "
            "WHERE validator_execution_ref=?",
            (record.validator_execution_ref,),
        )
        connection.commit()
    finally:
        connection.close()
    with pytest.raises(PostWritePersistenceIntegrityError, match="row/hash"):
        store.resolve_validator_execution_record(record.validator_execution_ref)

    connection = sqlite3.connect(database.path)
    try:
        connection.execute(
            "UPDATE validator_execution_records SET validator_version=? "
            "WHERE validator_execution_ref=?",
            (record.validator_version, record.validator_execution_ref),
        )
        connection.execute(
            "UPDATE validation_execution_events SET event_hash=? "
            "WHERE validation_run_id=? AND event_id=?",
            (SHA_A, run.validation_run_id, event.event_id),
        )
        connection.commit()
    finally:
        connection.close()
    with pytest.raises(PostWritePersistenceIntegrityError, match="event row|event hash"):
        store.resolve_validator_execution_record(record.validator_execution_ref)


def test_validator_terminal_finalization_rejects_missing_fact_ref(tmp_path) -> None:
    database, store, lifecycle, run = _created_run(tmp_path)
    lifecycle.start(
        run.validation_run_id,
        expected_state_version=1,
        transition_command_id="start",
    )
    fact = _fact(run.validation_run_id, run.subject_key.subject_hash)
    fact_ref = store.put_fact_if_absent_or_same(fact)
    connection = sqlite3.connect(database.path)
    try:
        connection.execute(
            "DELETE FROM validation_facts WHERE fact_id=?",
            (fact.fact_id,),
        )
        connection.commit()
    finally:
        connection.close()

    plan = _stage_plan()
    envelope = _input_envelope(
        run.validation_run_id,
        run.subject_key.subject_hash,
        plan,
    )
    event = store._event(
        validation_run_id=run.validation_run_id,
        event_id="validator-terminal-missing-fact",
        sequence=3,
        event_kind="VALIDATOR_COMPLETED",
        payload={"validator_execution_ref": "validator-exec-missing-fact"},
        occurred_at_epoch=2000.0,
    )
    record_base = {
        "schema_version": "validator_execution_record_v1",
        "validator_execution_ref": "validator-exec-missing-fact",
        "validation_run_id": run.validation_run_id,
        "subject_hash": run.subject_key.subject_hash,
        "validator_id": plan.validator_id,
        "validator_version": plan.validator_version,
        "stage_plan_ref": plan.stage_plan_ref,
        "stage_plan_version": plan.stage_plan_version,
        "input_hash": envelope.input_hash,
        "terminal_event_id": event.event_id,
        "terminal_execution_state": ValidatorTerminalExecutionState.COMPLETED,
        "expected_item_accounting": [
            ExpectedItemExecutionAccountingSchema(
                expected_validation_item_ref="item-1",
                execution_state=ExpectedItemExecutionState.COMPLETED,
                fact_refs=(fact_ref,),
            ).model_dump(mode="json")
        ],
        "produced_fact_refs": [fact_ref.model_dump(mode="json")],
        "intermediate_refs": [],
    }
    record = ValidatorExecutionRecordSchema(
        **record_base,
        record_sha256=canonical_sha256(record_base),
    )

    with pytest.raises(PostWritePersistenceIntegrityError, match="missing ValidationFact"):
        store.finalize_validator_execution(
            event,
            record,
            stage_plan=plan,
            input_envelope=envelope,
        )

    with database.read_transaction() as connection:
        assert connection.execute(
            "SELECT COUNT(*) FROM validation_execution_events "
            "WHERE event_id=?",
            (event.event_id,),
        ).fetchone()[0] == 0
        assert connection.execute(
            "SELECT COUNT(*) FROM validator_execution_records "
            "WHERE validator_execution_ref=?",
            (record.validator_execution_ref,),
        ).fetchone()[0] == 0


@pytest.mark.parametrize("proof_backed", [False, True])
def test_validator_terminal_record_binds_exact_intermediate_refs(tmp_path, proof_backed) -> None:
    _database, store, lifecycle, run = _created_run(tmp_path)
    lifecycle.start(
        run.validation_run_id,
        expected_state_version=1,
        transition_command_id="start",
    )
    fact = _fact(run.validation_run_id, run.subject_key.subject_hash)
    fact_ref = None if proof_backed else store.put_fact_if_absent_or_same(fact)
    intermediate_value = ArtifactStateRefSchema(
        artifact_id="section-artifact:intermediate-terminal",
        artifact_version=1,
        artifact_hash=SHA_A,
        artifact_scope=ArtifactScope.SECTION,
    )
    intermediate_ref = ValidationIntermediateRuntime(store).commit(
        validation_run_id=run.validation_run_id,
        producer_ref="validator-a:target-materializer:v1",
        target_ref="target:intermediate-terminal",
        typed_intermediate=intermediate_value,
        intermediate_kind=ValidationIntermediateKind.TARGET_ACCOUNTING,
    )
    plan = _stage_plan()
    envelope = _input_envelope(
        run.validation_run_id,
        run.subject_key.subject_hash,
        plan,
    )
    event = store._event(
        validation_run_id=run.validation_run_id,
        event_id="validator-terminal-with-intermediate",
        sequence=4,
        event_kind="VALIDATOR_COMPLETED",
        payload={"validator_execution_ref": "validator-exec-with-intermediate"},
        occurred_at_epoch=2000.0,
    )
    record_base = {
        "schema_version": "validator_execution_record_v1",
        "validator_execution_ref": "validator-exec-with-intermediate",
        "validation_run_id": run.validation_run_id,
        "subject_hash": run.subject_key.subject_hash,
        "validator_id": plan.validator_id,
        "validator_version": plan.validator_version,
        "stage_plan_ref": plan.stage_plan_ref,
        "stage_plan_version": plan.stage_plan_version,
        "input_hash": envelope.input_hash,
        "terminal_event_id": event.event_id,
        "terminal_execution_state": ValidatorTerminalExecutionState.COMPLETED,
        "expected_item_accounting": [
            ExpectedItemExecutionAccountingSchema(
                expected_validation_item_ref="item-1",
                execution_state=ExpectedItemExecutionState.COMPLETED,
                fact_refs=() if proof_backed else (fact_ref,),
                positive_proof_refs=(intermediate_ref,) if proof_backed else (),
            ).model_dump(mode="json")
        ],
        "produced_fact_refs": [] if proof_backed else [fact_ref.model_dump(mode="json")],
        "intermediate_refs": [intermediate_ref.model_dump(mode="json")],
    }
    record = ValidatorExecutionRecordSchema(
        **record_base,
        record_sha256=canonical_sha256(record_base),
    )

    stored = store.finalize_validator_execution(
        event,
        record,
        stage_plan=plan,
        input_envelope=envelope,
    )
    replay = store.resolve_validator_execution_record(
        record.validator_execution_ref
    )

    assert stored.intermediate_refs == (intermediate_ref,)
    assert stored.expected_item_accounting[0].positive_proof_refs == (
        (intermediate_ref,) if proof_backed else ()
    )
    if proof_backed:
        assert not store.list_facts_for_run(run.validation_run_id)
        raw = record.model_dump(mode="json")
        raw["intermediate_refs"] = []
        with pytest.raises(ValueError, match="positive proofs must be bound"):
            ValidatorExecutionRecordSchema.model_validate(raw)
        raw = record.model_dump(mode="json")
        raw["expected_item_accounting"][0]["positive_proof_refs"][0]["validation_run_id"] = "foreign"
        with pytest.raises(ValueError, match="positive proofs must be bound"):
            ValidatorExecutionRecordSchema.model_validate(raw)
    assert replay == stored
    assert store.resolve_intermediate(intermediate_ref) == (
        intermediate_value.model_dump(mode="json")
    )


def test_historical_validator_record_without_intermediate_refs_replays_by_raw_json_hash(
    tmp_path,
) -> None:
    database, store, lifecycle, run = _created_run(tmp_path)
    lifecycle.start(
        run.validation_run_id,
        expected_state_version=1,
        transition_command_id="start",
    )
    event = store._event(
        validation_run_id=run.validation_run_id,
        event_id="validator-terminal-legacy-record",
        sequence=3,
        event_kind="VALIDATOR_COMPLETED",
        payload={"validator_execution_ref": "validator-exec-legacy-record"},
        occurred_at_epoch=2000.0,
    )
    store.append_execution_event_if_absent_or_same(event)

    plan = _stage_plan()
    envelope = _input_envelope(
        run.validation_run_id,
        run.subject_key.subject_hash,
        plan,
    )
    legacy_base = {
        "schema_version": "validator_execution_record_v1",
        "validator_execution_ref": "validator-exec-legacy-record",
        "validation_run_id": run.validation_run_id,
        "subject_hash": run.subject_key.subject_hash,
        "validator_id": plan.validator_id,
        "validator_version": plan.validator_version,
        "stage_plan_ref": plan.stage_plan_ref,
        "stage_plan_version": plan.stage_plan_version,
        "input_hash": envelope.input_hash,
        "terminal_event_id": event.event_id,
        "terminal_execution_state": ValidatorTerminalExecutionState.COMPLETED.value,
        "expected_item_accounting": [
            ExpectedItemExecutionAccountingSchema(
                expected_validation_item_ref="item-1",
                execution_state=ExpectedItemExecutionState.COMPLETED,
            ).model_dump(mode="json")
        ],
        "produced_fact_refs": [],
    }
    legacy_hash = canonical_sha256(legacy_base)
    legacy_record = {**legacy_base, "record_sha256": legacy_hash}
    with database.write_transaction(immediate=True) as connection:
        connection.execute(
            """
            INSERT INTO validator_execution_records(
                validator_execution_ref,validation_run_id,validator_id,
                validator_version,record_json,record_sha256,
                terminal_event_id,created_at_epoch
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                legacy_base["validator_execution_ref"],
                run.validation_run_id,
                plan.validator_id,
                plan.validator_version,
                json.dumps(
                    legacy_record,
                    sort_keys=True,
                    separators=(",", ":"),
                ),
                legacy_hash,
                event.event_id,
                2000.0,
            ),
        )

    replay = store.resolve_validator_execution_record(
        "validator-exec-legacy-record"
    )
    assert replay.record_sha256 == legacy_hash
    assert replay.intermediate_refs == ()


def test_stage_plan_and_input_hash_conformance() -> None:
    plan = _stage_plan()
    envelope = _input_envelope("run-1", SHA_A, plan)
    StagePlanConformanceGuard.validate(plan, envelope)

    bad = envelope.model_copy(update={"input_hash": SHA_B})
    with pytest.raises(PostWritePersistenceIntegrityError, match="input envelope"):
        StagePlanConformanceGuard.validate(plan, bad)


def test_physical_model_call_sharing_requires_exact_logical_consumers() -> None:
    consumer = LogicalConsumerBindingSchema(
        validation_run_id="run-1",
        validator_execution_ref="validator-exec-1",
        stage_ref="stage:1",
        expected_validation_item_ref="item-1",
    )
    ref = PhysicalModelCallRefSchema(
        model_call_id="model-call-1",
        provider_attempt_id="provider-attempt-1",
        request_fingerprint=SHA_A,
        response_fingerprint=SHA_B,
        logical_consumers=(consumer,),
    )
    ModelCallCompatibilityGuard.validate_share(
        ref,
        (consumer,),
        request_fingerprint=SHA_A,
    )
    with pytest.raises(PostWritePersistenceIntegrityError):
        ModelCallCompatibilityGuard.validate_share(
            ref,
            (
                consumer.model_copy(
                    update={"expected_validation_item_ref": "item-2"}
                ),
            ),
            request_fingerprint=SHA_A,
        )


def test_quality_contract_registry_preserves_historical_exact_versions(tmp_path) -> None:
    store = QualityDeliveryContractStore(_database(tmp_path))
    registry = QualityDeliveryContractRegistry(store)
    resolver = QualityDeliveryContractResolver(store)

    v1 = registry.register(
        contract_id="quality-main",
        contract_version="v1",
        required_dimensions=(
            RequiredDimensionConditionSchema(dimension_id="faithfulness"),
        ),
    )
    v2 = registry.register(
        contract_id="quality-main",
        contract_version="v2",
        required_dimensions=(
            RequiredDimensionConditionSchema(dimension_id="faithfulness"),
            RequiredDimensionConditionSchema(dimension_id="citation"),
        ),
    )

    assert resolver.resolve(v1).contract_version == "v1"
    assert resolver.resolve(v2).contract_version == "v2"
    assert resolver.resolve(v1).required_dimensions != resolver.resolve(v2).required_dimensions

    with pytest.raises(PostWritePersistenceIntegrityError, match="identity collision"):
        registry.register(
            contract_id="quality-main",
            contract_version="v1",
            required_dimensions=(
                RequiredDimensionConditionSchema(dimension_id="faithfulness"),
                RequiredDimensionConditionSchema(dimension_id="new-dimension"),
            ),
        )
    assert resolver.resolve(v1).required_dimensions == (
        RequiredDimensionConditionSchema(dimension_id="faithfulness"),
    )


def test_no_validation_subject_or_run_head_tables_exist(tmp_path) -> None:
    database = _database(tmp_path)
    with database.read_transaction() as connection:
        tables = {
            row[0]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            )
        }
    forbidden = {
        "validation_subjects",
        "validation_run_heads",
        "validation_run_state_revisions",
        "validation_run_selections",
        "physical_model_calls",
    }
    assert forbidden.isdisjoint(tables)
