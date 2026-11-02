"""Repair owner races, late Writer historical-only proof, and Target startup."""
from concurrent.futures import ThreadPoolExecutor
import inspect
import threading

import pytest

from apps.enterprise_document.schemas.agent_rag_v1.paragraphs import ParagraphCandidateSchema
from apps.enterprise_document.schemas.pre_write_writer import WriterExecutionResultSchema, WriterExecutionStatus
from apps.enterprise_document.services.agent_rag_v1.lifecycle_runtime import LifecycleCurrentnessError
from apps.enterprise_document.services.agent_rag_v1.repair_authorization_runtime import (
    ParagraphRepairAuthorizationCurrentnessError, ParagraphRepairAuthorizationStore,
)
from core.persistence.interprocess_lock import InterProcessFileLock
from core.runtime.pre_write_execution_store import SQLitePreWriteExecutionStore, WriterExecutionState
from backend.tests.test_bca014_finalizing_lifecycle import _finalize
from backend.tests.test_bca014_finalizing_read_current import canonical_section
from backend.tests.test_multi_agent_v3_repair_authorization_runtime import _repair_fixture, _authorize
from backend.tests.test_gate_v2_pre_write_config_bootstrap import _build_p5_supervisor
from backend.tests.test_bca014_i3_mutation_owner_guards import sql_samples


def _bytes(paths):
    return {path: path.read_bytes() if path.exists() else b"" for path in paths}


def _revision_candidate(fx, record):
    runtime = fx["paragraph_runtime"]
    operation = runtime.revise_operation(run_id=record.run_id, section_id=record.section_id,
        write_instance_id=record.attempt_id, target_logical_ref=record.target.paragraph_logical_ref,
        base_version_ref=record.target.base_paragraph_version_ref, authorization_ref=record.repair_authorization_id)
    base = runtime.read_paragraph(record.target.base_paragraph_version_ref)
    return ParagraphCandidateSchema(content_contract_version=base.content_contract_version,
        operation_ref=operation, source_binding=base.source_binding, role=base.role,
        text="An attempted late revision.")


def test_one_finalizing_fixture_rejects_every_normal_owner(tmp_path, sql_samples):
    from apps.enterprise_document.schemas.agent_rag_v1.budgets import CanonicalBudgetVectorSchema
    fx = _repair_fixture(tmp_path)
    ref = _authorize(fx)
    record = fx["store"].resolve_exact(ref)
    candidate = _revision_candidate(fx, record)
    lifecycle = fx["runtime"].lifecycle_service
    paragraph = fx["paragraph_runtime"]
    paths = [fx["store"].path, fx["budget"].storage_path, paragraph.manifest_ledger_path,
             paragraph.artifact_runtime.ledger_path]
    before = _bytes(paths)
    state = fx["budget"].snapshot()
    from backend.tests.test_gate_v2_pre_write_execution_control import _writer_key
    from backend.tests.test_bca014_i3_mutation_owner_guards import SQL_KEYS, _sql_case, _dump
    from core.hashing import canonical_sha256
    writer = SQLitePreWriteExecutionStore(tmp_path / "writer.db",
        run_commit_guard=lifecycle.assert_run_committable, require_run_commit_guard=True)
    key = _writer_key()
    key = key.model_copy(update={"writer_subject_ref": key.writer_subject_ref.model_copy(
        update={"run_id": record.run_id})})
    writer.reserve_writer_execution(key, semantic_fingerprint="a" * 64)
    writer_before = _dump(writer.database_path)
    sql_cases = []
    for sql_key in SQL_KEYS:
        root = tmp_path / sql_key
        root.mkdir()
        db, owner, _ = _sql_case(root, sql_samples, sql_key, lifecycle.assert_run_committable)
        _, name, args, kwargs, _ = sql_samples[sql_key]
        # These test-authored DTOs carry a first-class Run field; all other
        # paths get that same exact owning Run from orchestration context.
        if sql_key.startswith("PostWriteEntryStore."):
            args = (args[0].model_copy(update={"identity": args[0].identity.model_copy(
                update={"run_id": record.run_id})}), *args[1:])
        elif sql_key.startswith("CorrectionAttemptStore."):
            material = args[0].model_dump(mode="json")
            material["run_id"] = record.run_id
            material.pop("projection_hash")
            created_at = material.pop("created_at_epoch")
            args = (type(args[0])(**material, projection_hash=canonical_sha256(material),
                                  created_at_epoch=created_at), *args[1:])
        sql_cases.append((db, owner, name, args, kwargs, _dump(db.path)))
    _finalize(lifecycle, lifecycle.load_run(record.run_id))
    calls = [
        lambda: _authorize(fx, repair_policy_version="i3-new"),
        lambda: fx["store"].put_if_absent_or_same(record),
        lambda: fx["runtime"].mark_semantic_repair_activated(ref),
        lambda: fx["runtime"].release_unactivated(ref),
        lambda: fx["budget"].reserve(record.budget_reservation_ref, CanonicalBudgetVectorSchema(repair_attempts=1), expected_revision=state.revision),
        lambda: fx["budget"].consume(record.budget_reservation_ref.reservation_id, CanonicalBudgetVectorSchema(repair_attempts=1), expected_revision=state.revision),
        lambda: fx["budget"].settle(record.budget_reservation_ref.reservation_id, CanonicalBudgetVectorSchema(repair_attempts=1), expected_revision=state.revision),
        lambda: fx["budget"].release(record.budget_reservation_ref.reservation_id, expected_revision=state.revision),
        lambda: fx["budget"].start_draining(expected_revision=state.revision),
        lambda: paragraph.commit_paragraph_version(candidate=candidate, active_lineage=fx["env"]["lineage"]),
        lambda: writer.reserve_writer_execution(key, semantic_fingerprint="a" * 64),
        lambda: writer.mark_writer_dispatched(key, semantic_fingerprint="a" * 64),
    ]
    for call in calls:
        with pytest.raises((LifecycleCurrentnessError, ParagraphRepairAuthorizationCurrentnessError)):
            call()
        assert _bytes(paths) == before
        assert _dump(writer.database_path) == writer_before
    for db, owner, name, args, kwargs, sql_before in sql_cases:
        with pytest.raises(LifecycleCurrentnessError):
            with db.run_write_guard_context(record.run_id):
                getattr(owner, name)(*args, **kwargs)
        assert _dump(db.path) == sql_before
    assert fx["store"].resolve_exact(ref) == record
    assert fx["budget"].snapshot() == state


@pytest.mark.parametrize("mutation", ["authorize", "activate"])
def test_repair_prebarrier_owner_commit_is_drainable_then_late_mutation_rejects(tmp_path, monkeypatch, mutation):
    fx = _repair_fixture(tmp_path)
    ref = _authorize(fx)
    record = fx["store"].resolve_exact(ref)
    lifecycle = fx["runtime"].lifecycle_service
    entered, release, attempted = threading.Event(), threading.Event(), threading.Event()
    if mutation == "authorize":
        # Use a new authorization occurrence, not an immutable row reuse.
        append = fx["store"].sink.append_record_once
        def held(*args, **kwargs):
            entered.set()
            assert release.wait(10)
            return append(*args, **kwargs)
        monkeypatch.setattr(fx["store"].sink, "append_record_once", held)
        call = lambda: _authorize(fx, repair_policy_version="i3-next")
        late = ParagraphRepairAuthorizationStore(fx["store"].path, run_commit_guard=lifecycle.assert_run_committable,
                                               require_run_commit_guard=True)
        target_lock = late.lock_path
        late_call = lambda: late.put_if_absent_or_same(record)
    else:
        append = fx["budget"]._canonical_log.append_serialized
        def held(value):
            entered.set()
            assert release.wait(10)
            return append(value)
        monkeypatch.setattr(fx["budget"]._canonical_log, "append_serialized", held)
        call = lambda: fx["runtime"].mark_semantic_repair_activated(ref)
        # Another Budget instance over the same ledger holds the same real locks.
        from apps.enterprise_document.services.agent_rag_v1.budget_manager import BudgetManager
        from backend.tests.test_multi_agent_v3_repair_authorization_runtime import NOW
        late = BudgetManager.canonical(task_id=fx["budget"].task_id, run_id=record.run_id,
            limits=fx["budget"].canonical_limits, max_elapsed_seconds=3600,
            storage_path=fx["budget"].storage_path, policy_ref=fx["budget"].policy_ref,
            clock=lambda: NOW, run_commit_guard=lifecycle.assert_run_committable, require_run_commit_guard=True)
        from backend.tests.test_bca014_i3_mutation_owner_guards import _ObservedLock
        late._lock = _ObservedLock(late._lock, attempted)
        target_lock = None
        late_call = lambda: late.start_draining(expected_revision=1)

    acquire = InterProcessFileLock.acquire
    def observed_acquire(lock):
        if target_lock is not None and lock.path == target_lock and entered.is_set():
            attempted.set()
        return acquire(lock)
    monkeypatch.setattr(InterProcessFileLock, "acquire", observed_acquire)
    with ThreadPoolExecutor(max_workers=2) as pool:
        writer = pool.submit(call)
        try:
            assert entered.wait(10)
            _finalize(lifecycle, lifecycle.load_run(record.run_id))
            rejected = pool.submit(late_call)
            assert attempted.wait(10)
            assert not rejected.done()
        finally:
            release.set()
        # Authorize may finish Budget before the barrier and still reject at
        # its separate authorization fence, if the barrier wins that interval.
        # Here the held append is after the authorization-store RUNNING check.
        result = writer.result(timeout=10)
        with pytest.raises(LifecycleCurrentnessError):
            rejected.result(timeout=10)
    if mutation == "authorize":
        assert fx["store"].resolve_exact(result).repair_policy_version == "i3-next"
    else:
        assert fx["budget"].snapshot().used.repair_attempts == 1


def test_late_dispatched_writer_result_cannot_change_selected_content(canonical_section, monkeypatch):
    from core.runtime.side_effects import LogicalOperationRef
    from core.hashing import canonical_sha256
    from model_gateway.fake_llm_client import FakeLLMClient
    from backend.tests.test_bca014_i3_mutation_owner_guards import _dump
    fx = canonical_section
    post_write = fx["post_write"]
    section = fx["arguments"]["source_sections"][0]
    lifecycle = post_write.lifecycle_service
    store = post_write.writer_execution_store
    selected_key = section.writer_execution_ref.logical_execution_key
    late_key = selected_key.model_copy(update={"write_instance_id": "late-unselected-i3"})
    fingerprint = "a" * 64
    store.reserve_writer_execution(late_key, semantic_fingerprint=fingerprint)
    assert store.mark_writer_dispatched(late_key, semantic_fingerprint=fingerprint)
    selected = store.get_writer_execution(selected_key)
    paragraph = post_write.claim_quality_projection.paragraph_runtime
    artifact = paragraph.artifact_runtime
    manifest = section.section_paragraph_manifest
    base_ref = manifest.members[0]
    head = artifact.resolve_active(base_ref.logical_ref)
    before = _bytes([artifact.ledger_path, paragraph.manifest_ledger_path])
    sql_before = _dump(post_write.database.path)
    run_id = selected_key.writer_subject_ref.run_id
    _finalize(lifecycle, lifecycle.load_run(run_id))

    def forbidden(*args, **kwargs):
        pytest.fail("late historical Writer result dispatched a provider")
    monkeypatch.setattr(FakeLLMClient, "generate", forbidden)
    result = WriterExecutionResultSchema(logical_execution_key=late_key, status=WriterExecutionStatus.SUCCESS,
        artifact=section.section.model_copy(update={"content": "late external content"}),
        generation_provenance={"generation_class": "normal_write"})
    assert store.persist_writer_result(late_key, semantic_fingerprint=fingerprint, result=result).state is WriterExecutionState.SUCCEEDED
    assert store.get_writer_execution(late_key).result == result
    assert store.get_writer_execution(selected_key) == selected
    assert _bytes(before) == before
    assert _dump(post_write.database.path) == sql_before
    assert artifact.resolve_active(base_ref.logical_ref) == head
    assert paragraph.load_manifest(manifest.manifest_ref) == manifest
    with pytest.raises(LifecycleCurrentnessError):
        store.reserve_writer_execution(late_key, semantic_fingerprint=fingerprint)
    with pytest.raises(LifecycleCurrentnessError):
        store.mark_writer_dispatched(late_key, semantic_fingerprint=fingerprint)
    operation = LogicalOperationRef(logical_operation_id="i3-late-promote", operation_kind="ARTIFACT_ACTIVE_PROMOTION",
        run_id=run_id, operation_scope_ref="paragraph")
    with pytest.raises(LifecycleCurrentnessError):
        artifact.promote_active(base_ref.logical_ref, base_ref, head.head_revision, head.active_version_ref.artifact_version_id,
                                operation, canonical_sha256("i3-late-promote"), None)
    base = paragraph.read_paragraph(base_ref)
    revision = paragraph.revise_operation(run_id=run_id, section_id=manifest.manifest_ref.section_id,
        write_instance_id=section.active_execution_lineage.attempt_id,
        target_logical_ref=base_ref.logical_ref, base_version_ref=base_ref,
        authorization_ref="late-unselected-i3")
    candidate = ParagraphCandidateSchema(content_contract_version=base.content_contract_version,
        operation_ref=revision, source_binding=base.source_binding, role=base.role, text="late external content")
    with pytest.raises(LifecycleCurrentnessError):
        paragraph.commit_paragraph_version(candidate=candidate, active_lineage=section.active_execution_lineage)
    manifest_operation = paragraph.manifest_operation(manifest_ref=manifest.manifest_ref,
        operation_kind="SECTION_PARAGRAPH_MANIFEST_UPDATE", authorization_ref="late-unselected-i3",
        stable_slot_ref="i3-late-manifest")
    with pytest.raises(LifecycleCurrentnessError):
        paragraph.update_manifest(operation=manifest_operation,
            expected_manifest_revision=manifest.manifest_revision,
            expected_generation_membership_state=manifest.generation_membership_state,
            expected_members=manifest.members, resulting_members=manifest.members,
            mutation_kind="late-result", active_lineage=section.active_execution_lineage)
    # Exact current projection still selects only the pre-barrier Writer chain.
    snapshot = post_write.claim_quality_projection.resolve_sources(post_write=post_write,
        artifact_ref=fx["arguments"]["artifact_ref"], scope=fx["arguments"]["scope"],
        source_sections=(section,), assembly_input_manifest_ref=None)
    assert snapshot.sections
    assert _bytes(before) == before and _dump(post_write.database.path) == sql_before


def test_target_startup_guards_share_one_lifecycle_and_repair_budget_provider(tmp_path):
    supervisor, _, _ = _build_p5_supervisor(tmp_path)
    try:
        engine = supervisor.workflow_engine
        composition = engine.stage_service.target_post_write_composition
        guard = composition.lifecycle_service.assert_run_committable
        assert composition.database.run_commit_guard == guard
        assert composition.writer_execution_store.run_commit_guard == guard
        assert composition.budget_manager_provider.run_commit_guard == guard
        assert composition.repair_provider.store.run_commit_guard == guard
        assert composition.repair_provider.budget_provider is composition.budget_manager_provider
        assert composition.database.require_run_commit_guard
        assert composition.writer_execution_store.require_run_commit_guard
    finally:
        supervisor.close()


@pytest.mark.parametrize("missing", ["writer", "budget", "postwrite", "repair"])
def test_target_startup_missing_guard_returns_no_runtime(tmp_path, monkeypatch, missing):
    from bootstrap.supervisor_factory import SupervisorFactory
    validate = SupervisorFactory._validate_target_startup
    def broken(**kwargs):
        composition = kwargs["target_post_write_composition"]
        if missing == "writer":
            kwargs["execution_store"].run_commit_guard = None
        elif missing == "budget":
            kwargs["canonical_budget_provider"].run_commit_guard = None
        elif missing == "postwrite":
            composition.database.run_commit_guard = None
        else:
            composition.repair_provider.store.run_commit_guard = None
        return validate(**kwargs)
    monkeypatch.setattr(SupervisorFactory, "_validate_target_startup", staticmethod(broken))
    with pytest.raises(RuntimeError, match="guard|guarded"):
        _build_p5_supervisor(tmp_path)


def test_writer_result_store_has_no_current_content_or_repair_side_effects():
    source = inspect.getsource(SQLitePreWriteExecutionStore.persist_writer_result)
    for forbidden in ("promote_active", "commit_paragraph", "update_manifest", "repair", "generate("):
        assert forbidden not in source


def test_target_criterion_evaluator_threads_exact_run_identity_before_gateway():
    from apps.enterprise_document.services.scheme_writer.writer_admission import (
        LLMCriterionSupportEvaluator, CriterionSupportTechnicalError,
    )
    from backend.tests.test_gate_v2_pre_write_admission_contract import (
        FakeCriterionGateway, _llm_payload, evidence_bundle, scheme_generation_minimal_profile,
    )
    gateway = FakeCriterionGateway(_llm_payload("supported", evidence_ids=("E1",)))
    evaluator = LLMCriterionSupportEvaluator(model_gateway=gateway, model_name="test",
                                           require_run_identity=True)
    requirement = scheme_generation_minimal_profile().requirements[0]
    kwargs = dict(requirement=requirement, criterion=requirement.criteria[0],
                  selected_evidence=tuple(evidence_bundle(selected=("E1",)).items))
    with pytest.raises(CriterionSupportTechnicalError, match="exact Task/Run"):
        evaluator.evaluate(**kwargs)
    assert gateway.calls == []
    evaluator.evaluate(**kwargs, task_id="exact-task-i3", run_id="exact-run-i3")
    assert gateway.calls[0].task_id == "exact-task-i3"
    assert gateway.calls[0].run_id == "exact-run-i3"
