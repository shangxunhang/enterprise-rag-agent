from __future__ import annotations

from dataclasses import dataclass
import hashlib

import pytest

from apps.enterprise_document.schemas.agent_rag_v1.lifecycle import (
    RunCompletionAggregateResult,
    RunIdentitySchema,
    RunFinalizationBarrierSchema,
    RunLifecycleStatus,
)
from apps.enterprise_document.services.agent_rag_v1.lifecycle_runtime import (
    LifecycleIntegrityError,
    RunAttemptLifecycleService,
)
from apps.enterprise_document.services.agent_rag_v1.run_completion_assessment_store import (
    RunCompletionAssessmentStore,
    RunCompletionAssessmentStoreConflict,
)
from apps.enterprise_document.services.agent_rag_v1.run_completion_runtime import (
    RUN_COMPLETION_ASSESSMENT_COMMIT,
    RunCompletionAssessmentBuilder,
    RunCompletionAssessmentPrerequisiteError,
    RunCompletionAssessmentStaleError,
    RunCompletionAssessmentTerminalizationResolver,
    RunCompletionTerminalizationSnapshot,
    exact_ref_envelope,
)
from apps.enterprise_document.schemas.post_write import (
    ActiveComparisonMode,
    ArtifactScope,
    ArtifactUsabilityIntegrityProofSchema,
    ArtifactUsabilityIntegrityState,
    CurrentnessProofSchema,
    CurrentnessState,
    DocumentCorrectionDispatchMode,
    EnforcementAction,
)
from core.hashing import canonical_sha256
from core.runtime.side_effects import (
    CommittedResultResolutionStatus,
    LogicalOperationRef,
)

from backend.tests.test_post_write_section_document_lifecycle import (
    _document_gate_control_fixture,
)


REQUEST_HASH = hashlib.sha256(b"wave12-run-request").hexdigest()


class _FixedClock:
    def now_iso(self) -> str:
        return "2026-09-05T15:00:00+00:00"


class _SequentialIds:
    def __init__(self) -> None:
        self.value = 0

    def new_id(self, prefix: str) -> str:
        self.value += 1
        return f"{prefix}:wave12:{self.value:03d}"


def _running_lifecycle(
    path,
    *,
    resolver=None,
) -> tuple[RunAttemptLifecycleService, object]:
    service = RunAttemptLifecycleService(
        path,
        clock=_FixedClock(),
        id_generator=_SequentialIds(),
        run_completion_assessment_resolver=resolver,
    )
    created = service.claim_new_run(
        RunIdentitySchema(
            run_id="run-wave11",
            task_id="task-wave12",
            tenant_id="tenant-wave12",
            canonical_request_sha256=REQUEST_HASH,
        )
    )
    running = service.transition_run(
        "run-wave11",
        created.lifecycle_revision,
        RunLifecycleStatus.RUNNING,
    )
    return service, running


def _section_completion_ref(section_key: str, *, revision: int = 1):
    payload = {
        "schema_version": "section_completion_ref_v1",
        "section_key": section_key,
        "completion_revision": revision,
        "completion_sha256": canonical_sha256(
            {"section_key": section_key, "completion_revision": revision}
        ),
    }
    return exact_ref_envelope(
        source_authority="MULTI_AGENT_SECTION_COMPLETION",
        source_kind="SECTION_COMPLETION",
        source_scope_ref=f"section:{section_key}",
        exact_ref=payload,
    )


def _mutation_ref(name: str, *, revision: int = 1):
    payload = {
        "schema_version": "post_write_mutation_closure_ref_v1",
        "mutation_id": f"mutation:{name}",
        "mutation_revision": revision,
        "mutation_sha256": canonical_sha256(
            {"name": name, "revision": revision}
        ),
    }
    return exact_ref_envelope(
        source_authority="POST_WRITE_MUTATION_CLOSURE",
        source_kind="POST_WRITE_MUTATION",
        source_scope_ref=f"mutation:{name}",
        exact_ref=payload,
    )


def _final_control(fx, *, correcting: bool):
    ref = fx["service"].decide(
        delivery_gate_decision_ref=fx["gate"].as_ref(),
        artifact_state_ref=fx["document"].document_artifact_ref,
        correction_policy_ref=fx["policy_ref"],
        fresh_currentness_proof=fx["currentness"],
        artifact_usability_integrity_proof=fx["usability"],
        correction_lineage_projection=fx["lineage"],
        generation_class=None,
        assembly_input_manifest_ref=fx["document"].manifest_ref,
        document_correction_dispatch_projection=(
            fx["dispatch"] if correcting else None
        ),
        created_at_epoch=5100.0 if correcting else 5101.0,
    )
    return fx["control_store"].resolve_exact(ref)


def _barrier_for_control(control):
    payload = {
        "schema_version": "run_finalization_barrier_v1",
        "run_id": "run-wave11",
        "final_control_decision_ref": exact_ref_envelope(
            source_authority="POST_WRITE_CONTROL",
            source_kind="POST_WRITE_CONTROL_DECISION",
            source_scope_ref=control.control_decision_id,
            exact_ref=control.as_ref(),
        ).model_dump(mode="json"),
        "barrier_contract_version": "v1",
    }
    return RunFinalizationBarrierSchema(
        **payload, barrier_sha256=canonical_sha256(payload)
    )


def _barrier_closure(barrier):
    return exact_ref_envelope(
        source_authority="RUN_LIFECYCLE",
        source_kind="RUN_FINALIZATION_BARRIER",
        source_scope_ref=f"run:{barrier.run_id}",
        exact_ref=barrier.as_ref(),
    )


def _enter_finalizing(lifecycle, running, barrier):
    return lifecycle.transition_run(
        running.identity.run_id, running.lifecycle_revision,
        RunLifecycleStatus.FINALIZING, run_finalization_barrier=barrier,
    )


def _assessment_fixture(tmp_path):
    post_write = _document_gate_control_fixture(tmp_path / "post-write")
    final_control = _final_control(post_write, correcting=False)
    assert final_control.enforcement_action is EnforcementAction.OUTPUT_BEST_EFFORT

    builder = RunCompletionAssessmentBuilder()
    section_refs = (
        _section_completion_ref("A"),
        _section_completion_ref("B"),
    )
    barrier = _barrier_for_control(final_control)
    mutation_refs = (_mutation_ref("document-validation"), _barrier_closure(barrier))
    record = builder.build_success(
        run_id="run-wave11",
        section_completion_refs=section_refs,
        document_artifact_ref=post_write["document"].document_artifact_ref,
        document_post_write_entry_ref=post_write["document_entry_ref"],
        post_write_control_decision=final_control,
        fresh_document_currentness_proof=post_write["currentness"],
        post_write_mutation_closure_refs=mutation_refs,
        active_repair_attempt_refs=(),
    )
    store = RunCompletionAssessmentStore(
        tmp_path / "run-completion-assessments.jsonl"
    )
    return {
        "post_write": post_write,
        "final_control": final_control,
        "builder": builder,
        "section_refs": section_refs,
        "mutation_refs": mutation_refs,
        "record": record,
        "barrier": barrier,
        "store": store,
    }


def test_assessment_identity_and_semantic_fingerprint_are_replay_stable(tmp_path) -> None:
    fx = _assessment_fixture(tmp_path)
    first = fx["record"]
    second = fx["builder"].build_success(
        run_id="run-wave11",
        section_completion_refs=reversed(fx["section_refs"]),
        document_artifact_ref=fx["post_write"]["document"].document_artifact_ref,
        document_post_write_entry_ref=fx["post_write"]["document_entry_ref"],
        post_write_control_decision=fx["final_control"],
        fresh_document_currentness_proof=fx["post_write"]["currentness"],
        post_write_mutation_closure_refs=reversed(fx["mutation_refs"]),
        active_repair_attempt_refs=(),
    )

    assert first == second
    assert first.aggregate_result is RunCompletionAggregateResult.SUCCESS
    assert first.committed_operation_ref.operation_kind == (
        RUN_COMPLETION_ASSESSMENT_COMMIT
    )
    assert first.document_artifact_ref.exact_ref_payload[
        "artifact_scope"
    ] == ArtifactScope.DOCUMENT.value
    assert first.post_write_control_decision_hash == (
        fx["final_control"].control_decision_hash
    )


def test_assessment_store_put_if_absent_or_same_and_sif02_resolution(tmp_path) -> None:
    fx = _assessment_fixture(tmp_path)
    first = fx["store"].put_if_absent_or_same(fx["record"])
    second = fx["store"].put_if_absent_or_same(fx["record"])

    assert first == second == fx["record"].as_ref()
    assert fx["store"].resolve_exact(first) == fx["record"]

    operation = fx["record"].committed_operation_ref
    resolution = fx["store"].resolve_committed_result(
        LogicalOperationRef(
            logical_operation_id=operation.logical_operation_id,
            operation_kind=operation.operation_kind,
            run_id=operation.run_id,
            operation_scope_ref=operation.operation_scope_ref,
        ),
        fx["record"].semantic_fingerprint,
    )
    assert resolution.resolution is CommittedResultResolutionStatus.COMMITTED_SAME
    assert resolution.committed_result_refs == (fx["record"].assessment_id,)


def test_same_assessment_identity_different_section_semantics_fails_closed(
    tmp_path,
) -> None:
    fx = _assessment_fixture(tmp_path)
    fx["store"].put_if_absent_or_same(fx["record"])

    conflicting = fx["builder"].build_success(
        run_id="run-wave11",
        section_completion_refs=(
            _section_completion_ref("A", revision=2),
            _section_completion_ref("B"),
        ),
        document_artifact_ref=fx["post_write"]["document"].document_artifact_ref,
        document_post_write_entry_ref=fx["post_write"]["document_entry_ref"],
        post_write_control_decision=fx["final_control"],
        fresh_document_currentness_proof=fx["post_write"]["currentness"],
        post_write_mutation_closure_refs=fx["mutation_refs"],
        active_repair_attempt_refs=(),
    )

    assert conflicting.assessment_id == fx["record"].assessment_id
    assert conflicting.semantic_fingerprint != fx["record"].semantic_fingerprint
    with pytest.raises(
        RunCompletionAssessmentStoreConflict,
        match="conflicting semantic fingerprint",
    ):
        fx["store"].put_if_absent_or_same(conflicting)


def test_mutation_closure_change_creates_new_assessment_identity(tmp_path) -> None:
    fx = _assessment_fixture(tmp_path)
    changed = fx["builder"].build_success(
        run_id="run-wave11",
        section_completion_refs=fx["section_refs"],
        document_artifact_ref=fx["post_write"]["document"].document_artifact_ref,
        document_post_write_entry_ref=fx["post_write"]["document_entry_ref"],
        post_write_control_decision=fx["final_control"],
        fresh_document_currentness_proof=fx["post_write"]["currentness"],
        post_write_mutation_closure_refs=(
            *fx["mutation_refs"],
            _mutation_ref("repair-round", revision=1),
        ),
        active_repair_attempt_refs=(),
    )

    assert changed.assessment_id != fx["record"].assessment_id


def test_correcting_control_cannot_produce_success_assessment(tmp_path) -> None:
    fx = _assessment_fixture(tmp_path)
    correcting = _final_control(fx["post_write"], correcting=True)
    assert correcting.enforcement_action is EnforcementAction.CORRECT
    assert (
        correcting.document_correction_dispatch_projection.correction_mode
        is DocumentCorrectionDispatchMode.ASSEMBLY_MECHANICAL
    )

    with pytest.raises(
        RunCompletionAssessmentPrerequisiteError,
        match="mutation-capable CORRECT",
    ):
        fx["builder"].build_success(
            run_id="run-wave11",
            section_completion_refs=fx["section_refs"],
            document_artifact_ref=fx["post_write"]["document"].document_artifact_ref,
            document_post_write_entry_ref=fx["post_write"]["document_entry_ref"],
            post_write_control_decision=correcting,
            fresh_document_currentness_proof=fx["post_write"]["currentness"],
            post_write_mutation_closure_refs=fx["mutation_refs"],
            active_repair_attempt_refs=(),
        )


def test_active_repair_attempt_blocks_success_assessment(tmp_path) -> None:
    fx = _assessment_fixture(tmp_path)

    with pytest.raises(
        RunCompletionAssessmentPrerequisiteError,
        match="active MA repair Attempt",
    ):
        fx["builder"].build_success(
            run_id="run-wave11",
            section_completion_refs=fx["section_refs"],
            document_artifact_ref=fx["post_write"]["document"].document_artifact_ref,
            document_post_write_entry_ref=fx["post_write"]["document_entry_ref"],
            post_write_control_decision=fx["final_control"],
            fresh_document_currentness_proof=fx["post_write"]["currentness"],
            post_write_mutation_closure_refs=fx["mutation_refs"],
            active_repair_attempt_refs=("repair-attempt:still-open",),
        )


def test_stale_document_currentness_blocks_success_assessment(tmp_path) -> None:
    fx = _assessment_fixture(tmp_path)
    proof = fx["post_write"]["currentness"]
    payload = proof.model_dump(mode="json")
    payload["currentness_state"] = "STALE"
    payload["match_state"] = "SELECTION_MISMATCH"
    payload.pop("proof_sha256", None)
    stale = CurrentnessProofSchema(
        **payload,
        proof_sha256=canonical_sha256(payload),
    )

    with pytest.raises(
        RunCompletionAssessmentStaleError,
        match="not CURRENT",
    ):
        fx["builder"].build_success(
            run_id="run-wave11",
            section_completion_refs=fx["section_refs"],
            document_artifact_ref=fx["post_write"]["document"].document_artifact_ref,
            document_post_write_entry_ref=fx["post_write"]["document_entry_ref"],
            post_write_control_decision=fx["final_control"],
            fresh_document_currentness_proof=stale,
            post_write_mutation_closure_refs=fx["mutation_refs"],
            active_repair_attempt_refs=(),
        )


def test_success_transition_requires_exact_committed_assessment_ref(tmp_path) -> None:
    fx = _assessment_fixture(tmp_path / "completion")
    lifecycle, running = _running_lifecycle(
        tmp_path / "lifecycle.jsonl",
        resolver=None,
    )

    running = _enter_finalizing(lifecycle, running, fx["barrier"])

    with pytest.raises(
        LifecycleIntegrityError,
        match="requires exact RunCompletionAssessmentRef",
    ):
        lifecycle.transition_run(
            "run-wave11",
            running.lifecycle_revision,
            RunLifecycleStatus.SUCCESS,
        )

    ref = fx["store"].put_if_absent_or_same(fx["record"])
    no_resolver = lifecycle
    with pytest.raises(
        LifecycleIntegrityError,
        match="committed assessment resolver",
    ):
        no_resolver.transition_run(
            "run-wave11",
            running.lifecycle_revision,
            RunLifecycleStatus.SUCCESS,
            run_completion_assessment_ref=ref,
        )


def test_crash_after_assessment_commit_reopens_and_terminalizes_exact_ref(
    tmp_path,
) -> None:
    lifecycle_path = tmp_path / "lifecycle.jsonl"
    fx = _assessment_fixture(tmp_path / "completion")
    assessment_ref = fx["store"].put_if_absent_or_same(fx["record"])

    state = {
        "control": fx["final_control"],
        "proof": fx["post_write"]["currentness"],
        "active_repairs": (),
    }
    resolver = RunCompletionAssessmentTerminalizationResolver(
        store=fx["store"],
        builder=fx["builder"],
        snapshot_provider=lambda _record: RunCompletionTerminalizationSnapshot(
            post_write_control_decision=state["control"],
            fresh_document_currentness_proof=state["proof"],
            active_repair_attempt_refs=state["active_repairs"],
        ),
    )
    lifecycle, running = _running_lifecycle(
        lifecycle_path,
        resolver=resolver.resolve_exact,
    )
    running = _enter_finalizing(lifecycle, running, fx["barrier"])

    # Simulate crash: assessment is durable while Run remains FINALIZING.
    assert fx["store"].resolve_exact(assessment_ref) == fx["record"]
    assert lifecycle.load_run_lifecycle("run-wave11").status is RunLifecycleStatus.FINALIZING

    reopened_store = RunCompletionAssessmentStore(
        tmp_path / "completion" / "run-completion-assessments.jsonl"
    )
    reopened_resolver = RunCompletionAssessmentTerminalizationResolver(
        store=reopened_store,
        builder=fx["builder"],
        snapshot_provider=lambda _record: RunCompletionTerminalizationSnapshot(
            post_write_control_decision=state["control"],
            fresh_document_currentness_proof=state["proof"],
            active_repair_attempt_refs=state["active_repairs"],
        ),
    )
    reopened_lifecycle = RunAttemptLifecycleService(
        lifecycle_path,
        clock=_FixedClock(),
        id_generator=_SequentialIds(),
        run_completion_assessment_resolver=reopened_resolver.resolve_exact,
    )
    terminal = reopened_lifecycle.transition_run(
        "run-wave11",
        running.lifecycle_revision,
        RunLifecycleStatus.SUCCESS,
        reason_ref="completion:assessment",
        run_completion_assessment_ref=assessment_ref,
    )

    assert terminal.status is RunLifecycleStatus.SUCCESS
    assert terminal.run_completion_assessment_ref == assessment_ref
    assert terminal.finished_at is not None


def test_assessment_stale_before_terminalization_cannot_terminalize(tmp_path) -> None:
    lifecycle_path = tmp_path / "lifecycle.jsonl"
    fx = _assessment_fixture(tmp_path / "completion")
    assessment_ref = fx["store"].put_if_absent_or_same(fx["record"])

    correcting_control = _final_control(
        fx["post_write"],
        correcting=True,
    )
    state = {
        "control": correcting_control,
        "proof": fx["post_write"]["currentness"],
    }
    resolver = RunCompletionAssessmentTerminalizationResolver(
        store=fx["store"],
        builder=fx["builder"],
        snapshot_provider=lambda _record: RunCompletionTerminalizationSnapshot(
            post_write_control_decision=state["control"],
            fresh_document_currentness_proof=state["proof"],
        ),
    )
    lifecycle, running = _running_lifecycle(
        lifecycle_path,
        resolver=resolver.resolve_exact,
    )
    running = _enter_finalizing(lifecycle, running, fx["barrier"])

    with pytest.raises(
        LifecycleIntegrityError,
        match="cannot be resolved exactly",
    ):
        lifecycle.transition_run(
            "run-wave11",
            running.lifecycle_revision,
            RunLifecycleStatus.SUCCESS,
            run_completion_assessment_ref=assessment_ref,
        )

    assert lifecycle.load_run_lifecycle("run-wave11").status is RunLifecycleStatus.FINALIZING


def test_non_success_terminal_state_cannot_bind_success_assessment(tmp_path) -> None:
    fx = _assessment_fixture(tmp_path / "completion")
    ref = fx["store"].put_if_absent_or_same(fx["record"])
    lifecycle, running = _running_lifecycle(
        tmp_path / "lifecycle.jsonl",
        resolver=fx["store"].resolve_exact,
    )

    with pytest.raises(
        LifecycleIntegrityError,
        match="non-SUCCESS transition",
    ):
        lifecycle.transition_run(
            "run-wave11",
            running.lifecycle_revision,
            RunLifecycleStatus.PARTIAL_SUCCESS,
            run_completion_assessment_ref=ref,
        )


def test_core_lifecycle_modules_do_not_import_post_write() -> None:
    from pathlib import Path

    root = Path(__file__).resolve().parents[2]
    for relative in (
        "backend/apps/enterprise_document/schemas/agent_rag_v1/lifecycle.py",
        "backend/apps/enterprise_document/services/agent_rag_v1/lifecycle_runtime.py",
    ):
        text = (root / relative).read_text(encoding="utf-8")
        assert "schemas.post_write" not in text
        assert "services.post_write" not in text
        assert "from .post_write" not in text


def test_no_run_completion_assessment_head_exists() -> None:
    import apps.enterprise_document.schemas.agent_rag_v1.lifecycle as lifecycle

    assert not hasattr(lifecycle, "RunCompletionAssessmentHead")
    assert not hasattr(lifecycle, "RunCompletionAssessmentHeadSchema")



def test_final_block_may_still_produce_success_completion_truth(tmp_path) -> None:
    fx = _assessment_fixture(tmp_path)
    usability_payload = fx["post_write"]["usability"].model_dump(mode="json")
    usability_payload["state"] = ArtifactUsabilityIntegrityState.INTEGRITY_INVALID.value
    usability_payload["artifact_record_hash"] = None
    usability_payload["reason_codes"] = ["TEST_DOCUMENT_INTEGRITY_INVALID"]
    usability_payload.pop("proof_sha256", None)
    unusable = ArtifactUsabilityIntegrityProofSchema(
        **usability_payload,
        proof_sha256=canonical_sha256(usability_payload),
    )
    ref = fx["post_write"]["service"].decide(
        delivery_gate_decision_ref=fx["post_write"]["gate"].as_ref(),
        artifact_state_ref=fx["post_write"]["document"].document_artifact_ref,
        correction_policy_ref=fx["post_write"]["policy_ref"],
        fresh_currentness_proof=fx["post_write"]["currentness"],
        artifact_usability_integrity_proof=unusable,
        correction_lineage_projection=fx["post_write"]["lineage"],
        generation_class=None,
        assembly_input_manifest_ref=fx["post_write"]["document"].manifest_ref,
        document_correction_dispatch_projection=None,
        created_at_epoch=5200.0,
    )
    blocked = fx["post_write"]["control_store"].resolve_exact(ref)
    assert blocked.enforcement_action is EnforcementAction.BLOCK

    completion = fx["builder"].build_success(
        run_id="run-wave11",
        section_completion_refs=fx["section_refs"],
        document_artifact_ref=fx["post_write"]["document"].document_artifact_ref,
        document_post_write_entry_ref=fx["post_write"]["document_entry_ref"],
        post_write_control_decision=blocked,
        fresh_document_currentness_proof=fx["post_write"]["currentness"],
        post_write_mutation_closure_refs=fx["mutation_refs"],
        active_repair_attempt_refs=(),
    )
    assert completion.aggregate_result is RunCompletionAggregateResult.SUCCESS
    assert completion.post_write_control_decision_ref.exact_ref_payload[
        "control_decision_id"
    ] == blocked.control_decision_id


def test_empty_completion_set_rejected_when_manifest_has_evaluated_sections(tmp_path) -> None:
    fx = _assessment_fixture(tmp_path)
    # V2 permits zero evaluated Sections at schema/build level. The exact
    # Assembly invariant still rejects omission of this INCLUDED Section.
    from apps.enterprise_document.services.agent_rag_v1.run_finalization_currentness import assert_assessment_sections
    from apps.enterprise_document.services.post_write.persistence import PostWritePersistenceIntegrityError
    assessment = fx["builder"].build_success(
            run_id="run-wave11",
            section_completion_refs=(),
            document_artifact_ref=fx["post_write"]["document"].document_artifact_ref,
            document_post_write_entry_ref=fx["post_write"]["document_entry_ref"],
            post_write_control_decision=fx["final_control"],
            fresh_document_currentness_proof=fx["post_write"]["currentness"],
            post_write_mutation_closure_refs=fx["mutation_refs"],
            active_repair_attempt_refs=(),
        )
    manifest = fx['post_write']['env']['binding_store'].resolve_assembly_manifest(
        fx['post_write']['document'].manifest_ref)
    with pytest.raises(PostWritePersistenceIntegrityError, match='completion set'):
        assert_assessment_sections(assessment, manifest)
