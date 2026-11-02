from __future__ import annotations

from datetime import datetime, timezone
import json
from concurrent.futures import ThreadPoolExecutor

import pytest

from apps.enterprise_document.schemas.agent_rag_v1.lifecycle import (
    RunLifecycleStatus,
)
from apps.enterprise_document.schemas.post_write import (
    ArtifactScope,
    ArtifactUsabilityIntegrityProofSchema,
    ArtifactUsabilityIntegrityState,
    EnforcementAction,
    PostWriteReleaseMode,
    PublicationReuseState,
    ValidationSemanticState,
)
from apps.enterprise_document.services.agent_rag_v1.artifact_store import (
    FileArtifactStore,
)
from apps.enterprise_document.services.agent_rag_v1.run_completion_assessment_store import (
    RunCompletionAssessmentStore,
)
from apps.enterprise_document.services.agent_rag_v1.run_completion_runtime import (
    RunCompletionAssessmentBuilder,
    RunCompletionAssessmentTerminalizationResolver,
    RunCompletionTerminalizationSnapshot,
)
from apps.enterprise_document.services.post_write import (
    ArtifactCanonicalPayloadMaterializer,
    ArtifactOutputConformanceVerifier,
    ArtifactUsabilityIntegrityResolver,
    ArtifactValidationViewResolver,
    CorrectionPolicyRegistry,
    CorrectionPolicyStore,
    CurrentnessResolver,
    DocumentBindingStore,
    OutputConformanceRegistry,
    PostWriteEntryIdentityBuilder,
    PostWriteReleaseCommitFence,
    PostWriteReleaseEligibilityError,
    PostWriteReleaseInputIntegrityGuard,
    PostWriteReleaseIntegrityError,
    PostWriteReleasePublicationService,
)

from core.hashing import canonical_sha256

from backend.tests.test_multi_agent_v3_release_lineage import (
    _final_control,
    _barrier_for_control,
    _barrier_closure,
    _enter_finalizing,
    _mutation_ref,
    _running_lifecycle,
    _section_completion_ref,
)
from backend.tests.test_post_write_section_document_lifecycle import (
    _document_gate_control_fixture,
)


def _release_fixture(
    tmp_path,
    *,
    validation_state=ValidationSemanticState.PASS,
    publication_target_id="target://primary",
    fault_injector=None,
):
    output_store = FileArtifactStore(tmp_path / "validated-output")
    publication_store = FileArtifactStore(tmp_path / "published-output")
    registry = OutputConformanceRegistry()
    registry.register(
        output_format="json",
        adapter_contract_version="json-visible-v1",
        adapter=lambda raw: json.loads(raw.decode("utf-8")),
    )
    verifier = ArtifactOutputConformanceVerifier(
        artifact_store=output_store,
        registry=registry,
    )
    release_binding = {}

    def transform(env, document):
        previous = env["artifact_store"].resolve_exact(
            document.document_artifact_ref
        )
        visible_payload = (
            ArtifactCanonicalPayloadMaterializer.business_payload(
                previous.canonical_payload_ref
            )
        )
        projection = ArtifactValidationViewResolver.build_projection(
            artifact_scope=ArtifactScope.DOCUMENT,
            logical_target_ref="document-logical:wave11",
            visible_payload=visible_payload,
        )
        output_ref = output_store.put_json(
            visible_payload,
            artifact_type="validated_document_output",
            schema_version="validated_document_output_v1",
            created_by="wave12b-test-renderer",
            task_id="task-wave12",
            run_id="run-wave11",
            input_snapshot_version=str(
                document.document_artifact_ref.artifact_version + 1
            ),
            artifact_ref=(
                "validated-output://run-wave11/doc-wave11/json"
            ),
        )
        binding = verifier.build_output_binding(
            output_format="json",
            renderer_contract_version="json-renderer-v1",
            output_artifact_ref=output_ref,
        )
        proof = verifier.prove_precommit(
            artifact_id=document.document_artifact_ref.artifact_id,
            artifact_scope=ArtifactScope.DOCUMENT,
            canonical_payload_ref=previous.canonical_payload_ref,
            visible_projection=projection,
            output_binding=binding,
        )
        new_ref = env["artifact_service"].append_next_version(
            expected_parent_ref=document.document_artifact_ref,
            canonical_payload_ref=previous.canonical_payload_ref,
            origin=previous.origin,
            visible_projection_ref=projection.as_ref(),
            visible_projection=projection,
            validated_output_bindings=(binding,),
            output_conformance_proofs=(proof,),
        )
        release_binding.update(
            {
                "projection": projection,
                "output_ref": output_ref,
                "binding": binding,
                "proof": proof,
            }
        )
        return new_ref

    post_write = _document_gate_control_fixture(
        tmp_path / "post-write",
        document_artifact_transform=transform,
        validation_state=validation_state,
    )
    final_control = _final_control(
        post_write,
        correcting=False,
    )

    barrier = _barrier_for_control(final_control)
    builder = RunCompletionAssessmentBuilder()
    assessment = builder.build_success(
        run_id="run-wave11",
        section_completion_refs=(
            _section_completion_ref("A"),
            _section_completion_ref("B"),
        ),
        document_artifact_ref=(
            post_write["document"].document_artifact_ref
        ),
        document_post_write_entry_ref=post_write["document_entry_ref"],
        post_write_control_decision=final_control,
        fresh_document_currentness_proof=post_write["currentness"],
        post_write_mutation_closure_refs=(
            _mutation_ref("document-validation"),
            _barrier_closure(barrier),
        ),
        active_repair_attempt_refs=(),
    )
    assessment_store = RunCompletionAssessmentStore(
        tmp_path / "run-completion-assessments.jsonl"
    )
    assessment_ref = assessment_store.put_if_absent_or_same(
        assessment
    )

    terminalization = RunCompletionAssessmentTerminalizationResolver(
        store=assessment_store,
        builder=builder,
        snapshot_provider=lambda _record: (
            RunCompletionTerminalizationSnapshot(
                post_write_control_decision=final_control,
                fresh_document_currentness_proof=(
                    post_write["currentness"]
                ),
            )
        ),
    )
    lifecycle, running = _running_lifecycle(
        tmp_path / "run-lifecycle.jsonl",
        resolver=terminalization.resolve_exact,
    )
    running = _enter_finalizing(lifecycle, running, barrier)
    terminal = lifecycle.transition_run(
        "run-wave11",
        running.lifecycle_revision,
        RunLifecycleStatus.SUCCESS,
        reason_ref="completion:wave12b",
        run_completion_assessment_ref=assessment_ref,
    )

    currentness_resolver = CurrentnessResolver(
        entry_store=post_write["env"]["entry_store"],
        identity_builder=PostWriteEntryIdentityBuilder(),
        document_binding_store=DocumentBindingStore(
            post_write["env"]["database"]
        ),
    )
    guard = PostWriteReleaseInputIntegrityGuard(
        artifact_state_store=post_write["env"]["artifact_store"],
        control_store=post_write["control_store"],
        currentness_resolver=currentness_resolver,
        artifact_usability_resolver=ArtifactUsabilityIntegrityResolver(
            post_write["env"]["artifact_store"]
        ),
        output_conformance_verifier=verifier,
        run_completion_assessment_resolver=(
            assessment_store.resolve_exact
        ),
        run_lifecycle_revision_resolver=(
            lifecycle.load_run_lifecycle_revision
        ),
    )
    service = PostWriteReleasePublicationService(
        publication_store=publication_store,
        input_guard=guard,
        release_commit_fence=PostWriteReleaseCommitFence(
            post_write["env"]["database"]
        ),
        clock=lambda: datetime(
            2026, 9, 5, 16, 0, tzinfo=timezone.utc
        ),
        fault_injector=fault_injector,
    )
    request = service.build_request(
        release_contract_version=service.release_contract_version,
        task_id="task-wave12",
        run_id="run-wave11",
        document_logical_id="document-logical:wave11",
        document_artifact_ref=(
            post_write["document"].document_artifact_ref
        ),
        post_write_control_decision_ref=final_control.as_ref(),
        output_format="json",
        validated_output_binding_sha256=(
            release_binding["binding"].binding_sha256
        ),
        publication_target_id=publication_target_id,
    )
    return {
        "post_write": post_write,
        "final_control": final_control,
        "assessment": assessment,
        "assessment_ref": assessment_ref,
        "assessment_store": assessment_store,
        "lifecycle": lifecycle,
        "terminal": terminal,
        "release_binding": release_binding,
        "output_store": output_store,
        "publication_store": publication_store,
        "guard": guard,
        "service": service,
        "request": request,
    }


def _publish(fx, *, supplier=None):
    return fx["service"].publish(
        request=fx["request"],
        run_completion_assessment_ref=fx["assessment_ref"],
        run_lifecycle_revision=fx["terminal"].lifecycle_revision,
        active_document_binding_supplier=(
            supplier
            if supplier is not None
            else lambda: fx["post_write"]["active_projection"]
        ),
    )


def test_standard_target_release_publishes_exact_prevalidated_bytes(tmp_path) -> None:
    fx = _release_fixture(
        tmp_path,
        validation_state=ValidationSemanticState.PASS,
    )
    assert fx["final_control"].enforcement_action is EnforcementAction.OUTPUT

    published = _publish(fx)

    assert published.reuse_state is PublicationReuseState.NEW_COMMIT
    assert published.manifest.release_mode is PostWriteReleaseMode.STANDARD
    assert (
        published.manifest.enforcement_action_passthrough
        is EnforcementAction.OUTPUT
    )
    assert published.manifest.quality_state_passthrough.value == "PASS"
    assert (
        published.manifest.release_payload_sha256
        == fx["release_binding"]["binding"].output_payload_sha256
    )


def test_best_effort_fail_quality_is_formally_publishable_with_diagnostics(
    tmp_path,
) -> None:
    fx = _release_fixture(
        tmp_path,
        validation_state=ValidationSemanticState.FAIL,
    )
    assert (
        fx["final_control"].enforcement_action
        is EnforcementAction.OUTPUT_BEST_EFFORT
    )

    published = _publish(fx)

    assert published.manifest.release_mode is PostWriteReleaseMode.BEST_EFFORT
    assert published.manifest.quality_state_passthrough.value == "FAIL"
    assert published.manifest.blocking_fact_refs


def test_exact_request_replay_restores_historical_manifest_without_republish(
    tmp_path,
) -> None:
    fx = _release_fixture(tmp_path)
    first = _publish(fx)
    second = _publish(fx)

    assert first.reuse_state is PublicationReuseState.NEW_COMMIT
    assert second.reuse_state is PublicationReuseState.EXACT_REQUEST_REPLAY
    assert second.manifest == first.manifest
    assert second.manifest_artifact == first.manifest_artifact


def test_same_output_new_valid_control_request_reuses_existing_side_effect(
    tmp_path,
) -> None:
    fx = _release_fixture(tmp_path)
    first = _publish(fx)

    new_policy_ref = CorrectionPolicyRegistry(
        CorrectionPolicyStore(fx["post_write"]["env"]["database"])
    ).register(
        policy_id="document-correction-policy-v2",
        policy_version="v2",
        rules=(),
    )
    new_control_ref = fx["post_write"]["service"].decide(
        delivery_gate_decision_ref=fx["post_write"]["gate"].as_ref(),
        artifact_state_ref=fx["post_write"]["document"].document_artifact_ref,
        correction_policy_ref=new_policy_ref,
        fresh_currentness_proof=fx["post_write"]["currentness"],
        artifact_usability_integrity_proof=fx["post_write"]["usability"],
        correction_lineage_projection=fx["post_write"]["lineage"],
        generation_class=None,
        assembly_input_manifest_ref=fx["post_write"]["document"].manifest_ref,
        document_correction_dispatch_projection=None,
        created_at_epoch=5300.0,
    )
    new_control = fx["post_write"]["control_store"].resolve_exact(
        new_control_ref
    )
    assert new_control.as_ref() != fx["final_control"].as_ref()

    request2 = fx["service"].build_request(
        release_contract_version=fx["service"].release_contract_version,
        task_id="task-wave12",
        run_id="run-wave11",
        document_logical_id="document-logical:wave11",
        document_artifact_ref=(
            fx["post_write"]["document"].document_artifact_ref
        ),
        post_write_control_decision_ref=new_control.as_ref(),
        output_format="json",
        validated_output_binding_sha256=(
            fx["release_binding"]["binding"].binding_sha256
        ),
        publication_target_id="target://primary",
    )
    restored = fx["service"].publish(
        request=request2,
        run_completion_assessment_ref=fx["assessment_ref"],
        run_lifecycle_revision=fx["terminal"].lifecycle_revision,
        active_document_binding_supplier=lambda: (_ for _ in ()).throw(
            AssertionError("historical replay must not request fresh binding")
        ),
    )

    assert (
        restored.reuse_state
        is PublicationReuseState.ALREADY_PUBLISHED_SAME_OUTPUT
    )
    assert restored.manifest == first.manifest
    assert (
        restored.manifest.post_write_control_decision_ref
        == fx["final_control"].as_ref()
    )


def test_stale_active_document_blocks_new_publication(tmp_path) -> None:
    fx = _release_fixture(tmp_path)
    record = fx["post_write"]["env"]["artifact_store"].resolve_exact(
        fx["post_write"]["document"].document_artifact_ref
    )
    assert record.parent_artifact_ref is not None
    stale = fx["post_write"]["active_projection"].model_copy(
        update={"artifact_state_ref": record.parent_artifact_ref}
    )

    with pytest.raises(
        PostWriteReleaseEligibilityError,
        match="currentness|stale",
    ):
        _publish(fx, supplier=lambda: stale)


def test_historical_replay_ignores_later_current_staleness(tmp_path) -> None:
    fx = _release_fixture(tmp_path)
    first = _publish(fx)

    second = _publish(
        fx,
        supplier=lambda: (_ for _ in ()).throw(
            AssertionError("existing manifest must short-circuit fresh proof")
        ),
    )
    assert second.reuse_state is PublicationReuseState.EXACT_REQUEST_REPLAY
    assert second.manifest == first.manifest


def test_correct_action_cannot_target_release(tmp_path) -> None:
    fx = _release_fixture(
        tmp_path,
        validation_state=ValidationSemanticState.FAIL,
    )
    correcting = _final_control(
        fx["post_write"],
        correcting=True,
    )
    assert correcting.enforcement_action is EnforcementAction.CORRECT
    request = fx["service"].build_request(
        release_contract_version=fx["service"].release_contract_version,
        task_id="task-wave12",
        run_id="run-wave11",
        document_logical_id="document-logical:wave11",
        document_artifact_ref=(
            fx["post_write"]["document"].document_artifact_ref
        ),
        post_write_control_decision_ref=correcting.as_ref(),
        output_format="json",
        validated_output_binding_sha256=(
            fx["release_binding"]["binding"].binding_sha256
        ),
        publication_target_id="target://correct-forbidden",
    )

    with pytest.raises(
        PostWriteReleaseEligibilityError,
        match="CORRECT/BLOCK",
    ):
        fx["service"].publish(
            request=request,
            run_completion_assessment_ref=fx["assessment_ref"],
            run_lifecycle_revision=fx["terminal"].lifecycle_revision,
            active_document_binding_supplier=lambda: (
                fx["post_write"]["active_projection"]
            ),
        )


def test_nonterminal_run_revision_cannot_authorize_new_publication(tmp_path) -> None:
    fx = _release_fixture(tmp_path)

    with pytest.raises(
        PostWriteReleaseEligibilityError,
        match="terminal SUCCESS",
    ):
        fx["service"].publish(
            request=fx["request"],
            run_completion_assessment_ref=fx["assessment_ref"],
            run_lifecycle_revision=1,
            active_document_binding_supplier=lambda: (
                fx["post_write"]["active_projection"]
            ),
        )


def test_missing_exact_output_binding_fails_before_publication(tmp_path) -> None:
    fx = _release_fixture(tmp_path)
    request = fx["request"].model_copy(
        update={"validated_output_binding_sha256": "f" * 64}
    )
    payload = request.model_dump(mode="json")
    payload.pop("request_sha256", None)
    request = request.model_copy(
        update={"request_sha256": canonical_sha256(payload)}
    )

    with pytest.raises(
        PostWriteReleaseEligibilityError,
        match="validated output binding",
    ):
        fx["service"].publish(
            request=request,
            run_completion_assessment_ref=fx["assessment_ref"],
            run_lifecycle_revision=fx["terminal"].lifecycle_revision,
            active_document_binding_supplier=lambda: (
                fx["post_write"]["active_projection"]
            ),
        )


def test_pre_manifest_crash_leaves_no_committed_publication(tmp_path) -> None:
    def crash(stage: str) -> None:
        if stage == "after_payload_before_fence":
            raise RuntimeError("simulated crash")

    fx = _release_fixture(tmp_path, fault_injector=crash)
    _, key = fx["service"].publication_identity(fx["request"])
    with pytest.raises(RuntimeError, match="simulated crash"):
        _publish(fx)

    with pytest.raises(FileNotFoundError):
        fx["publication_store"].get_json(
            fx["service"]._manifest_ref(key),
            task_id="task-wave12",
        )


def test_manifest_commit_then_response_loss_recovers_exact_publication(tmp_path) -> None:
    def crash(stage: str) -> None:
        if stage == "after_manifest_before_response":
            raise RuntimeError("simulated response loss")

    fx = _release_fixture(tmp_path, fault_injector=crash)
    with pytest.raises(RuntimeError, match="simulated response loss"):
        _publish(fx)

    fx["service"].fault_injector = None
    restored = _publish(fx)
    assert restored.reuse_state is PublicationReuseState.EXACT_REQUEST_REPLAY


def test_concurrent_exact_publication_converges_to_one_manifest(tmp_path) -> None:
    fx = _release_fixture(tmp_path)

    def invoke():
        return _publish(fx).reuse_state

    with ThreadPoolExecutor(max_workers=2) as pool:
        states = list(pool.map(lambda _: invoke(), range(2)))

    assert sorted(item.value for item in states) == [
        PublicationReuseState.EXACT_REQUEST_REPLAY.value,
        PublicationReuseState.NEW_COMMIT.value,
    ]


def test_publication_identity_excludes_control_and_changes_for_target(tmp_path) -> None:
    fx = _release_fixture(tmp_path)
    _, key1 = fx["service"].publication_identity(fx["request"])
    other_target = fx["service"].build_request(
        release_contract_version=fx["service"].release_contract_version,
        task_id="task-wave12",
        run_id="run-wave11",
        document_logical_id="document-logical:wave11",
        document_artifact_ref=(
            fx["post_write"]["document"].document_artifact_ref
        ),
        post_write_control_decision_ref=fx["final_control"].as_ref(),
        output_format="json",
        validated_output_binding_sha256=(
            fx["release_binding"]["binding"].binding_sha256
        ),
        publication_target_id="target://secondary",
    )
    _, key2 = fx["service"].publication_identity(other_target)

    assert key1 != key2


def test_release_request_and_manifest_have_no_document_generation_class(tmp_path) -> None:
    fx = _release_fixture(
        tmp_path,
        validation_state=ValidationSemanticState.FAIL,
    )
    published = _publish(fx)
    assert "generation_class" not in fx["request"].model_dump(mode="json")
    assert "generation_class" not in published.manifest.model_dump(mode="json")



def test_unresolved_quality_best_effort_is_formally_publishable(tmp_path) -> None:
    fx = _release_fixture(
        tmp_path,
        validation_state=ValidationSemanticState.INDETERMINATE,
    )
    assert (
        fx["final_control"].enforcement_action
        is EnforcementAction.OUTPUT_BEST_EFFORT
    )

    published = _publish(fx)

    assert published.manifest.release_mode is PostWriteReleaseMode.BEST_EFFORT
    assert published.manifest.quality_state_passthrough.value == "UNRESOLVED"
    assert published.manifest.unresolved_reason_refs


def test_block_action_cannot_target_release(tmp_path) -> None:
    fx = _release_fixture(
        tmp_path,
        validation_state=ValidationSemanticState.PASS,
    )
    usable = fx["post_write"]["usability"]
    payload = usable.model_dump(mode="json")
    payload["state"] = ArtifactUsabilityIntegrityState.INTEGRITY_INVALID.value
    payload["artifact_record_hash"] = None
    payload["reason_codes"] = ["TEST_RELEASE_BLOCK"]
    payload.pop("proof_sha256", None)
    unusable = ArtifactUsabilityIntegrityProofSchema(
        **payload,
        proof_sha256=canonical_sha256(payload),
    )
    block_ref = fx["post_write"]["service"].decide(
        delivery_gate_decision_ref=fx["post_write"]["gate"].as_ref(),
        artifact_state_ref=fx["post_write"]["document"].document_artifact_ref,
        correction_policy_ref=fx["post_write"]["policy_ref"],
        fresh_currentness_proof=fx["post_write"]["currentness"],
        artifact_usability_integrity_proof=unusable,
        correction_lineage_projection=fx["post_write"]["lineage"],
        generation_class=None,
        assembly_input_manifest_ref=fx["post_write"]["document"].manifest_ref,
        document_correction_dispatch_projection=None,
        created_at_epoch=5400.0,
    )
    blocked = fx["post_write"]["control_store"].resolve_exact(block_ref)
    assert blocked.enforcement_action is EnforcementAction.BLOCK

    request = fx["service"].build_request(
        release_contract_version=fx["service"].release_contract_version,
        task_id="task-wave12",
        run_id="run-wave11",
        document_logical_id="document-logical:wave11",
        document_artifact_ref=fx["post_write"]["document"].document_artifact_ref,
        post_write_control_decision_ref=blocked.as_ref(),
        output_format="json",
        validated_output_binding_sha256=(
            fx["release_binding"]["binding"].binding_sha256
        ),
        publication_target_id="target://block-forbidden",
    )
    with pytest.raises(
        PostWriteReleaseEligibilityError,
        match="CORRECT/BLOCK",
    ):
        fx["service"].publish(
            request=request,
            run_completion_assessment_ref=fx["assessment_ref"],
            run_lifecycle_revision=fx["terminal"].lifecycle_revision,
            active_document_binding_supplier=lambda: (
                fx["post_write"]["active_projection"]
            ),
        )


def test_committed_manifest_tamper_fails_historical_replay_verification(tmp_path) -> None:
    fx = _release_fixture(tmp_path)
    published = _publish(fx)
    manifest_path = fx["publication_store"]._path_for_ref(
        published.manifest_artifact.artifact_ref
    )
    manifest_path.write_text("{not-json", encoding="utf-8")

    with pytest.raises(
        PostWriteReleaseIntegrityError,
        match="manifest cannot be read|manifest is corrupt",
    ):
        _publish(fx)


def test_premanifest_orphan_then_stale_retry_cannot_newly_commit(tmp_path) -> None:
    def crash(stage: str) -> None:
        if stage == "after_payload_before_fence":
            raise RuntimeError("simulated orphan payload")

    fx = _release_fixture(tmp_path, fault_injector=crash)
    _, key = fx["service"].publication_identity(fx["request"])
    with pytest.raises(RuntimeError, match="simulated orphan payload"):
        _publish(fx)
    fx["service"].fault_injector = None

    record = fx["post_write"]["env"]["artifact_store"].resolve_exact(
        fx["post_write"]["document"].document_artifact_ref
    )
    assert record.parent_artifact_ref is not None
    stale = fx["post_write"]["active_projection"].model_copy(
        update={"artifact_state_ref": record.parent_artifact_ref}
    )
    with pytest.raises(PostWriteReleaseEligibilityError):
        _publish(fx, supplier=lambda: stale)

    with pytest.raises(FileNotFoundError):
        fx["publication_store"].get_json(
            fx["service"]._manifest_ref(key),
            task_id="task-wave12",
        )


def test_new_artifact_or_output_binding_changes_publication_identity(tmp_path) -> None:
    fx = _release_fixture(tmp_path)
    _, baseline = fx["service"].publication_identity(fx["request"])

    changed_artifact = fx["request"].document_artifact_ref.model_copy(
        update={
            "artifact_version": fx["request"].document_artifact_ref.artifact_version + 1,
            "artifact_hash": "e" * 64,
        }
    )
    artifact_request = fx["service"].build_request(
        release_contract_version=fx["service"].release_contract_version,
        task_id="task-wave12",
        run_id="run-wave11",
        document_logical_id="document-logical:wave11",
        document_artifact_ref=changed_artifact,
        post_write_control_decision_ref=fx["final_control"].as_ref(),
        output_format="json",
        validated_output_binding_sha256=(
            fx["release_binding"]["binding"].binding_sha256
        ),
        publication_target_id="target://primary",
    )
    output_request = fx["service"].build_request(
        release_contract_version=fx["service"].release_contract_version,
        task_id="task-wave12",
        run_id="run-wave11",
        document_logical_id="document-logical:wave11",
        document_artifact_ref=fx["request"].document_artifact_ref,
        post_write_control_decision_ref=fx["final_control"].as_ref(),
        output_format="json",
        validated_output_binding_sha256="d" * 64,
        publication_target_id="target://primary",
    )

    assert fx["service"].publication_identity(artifact_request)[1] != baseline
    assert fx["service"].publication_identity(output_request)[1] != baseline
