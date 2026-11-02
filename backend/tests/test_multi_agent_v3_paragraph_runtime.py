from __future__ import annotations

from types import SimpleNamespace

import pytest

from apps.enterprise_document.schemas.agent_rag_v1 import (
    ParagraphBindingSchema,
    ParagraphCandidateSchema,
    ParagraphCreationOccurrenceRefSchema,
    ParagraphWriterSourceBindingSchema,
    SectionParagraphManifestRefSchema,
)
from apps.enterprise_document.schemas.agent_rag_v1.writer_pipeline import (
    ParagraphPrecheckResultSchema,
)
from apps.enterprise_document.schemas.agent_rag_v1.paragraphs import (
    ParagraphContentSchema,
)
from apps.enterprise_document.schemas.pre_write_binding import (
    WriterGateBindingRefSchema,
    WriterSubjectRefSchema,
)
from apps.enterprise_document.schemas.pre_write_control import (
    PreWriteRouteDecisionRefSchema,
)
from apps.enterprise_document.schemas.pre_write_writer import (
    WriterGenerationClass,
    WriterGenerationInvocationSchema,
    WriterLogicalExecutionKey,
)
from apps.enterprise_document.services.agent_rag_v1.paragraph_runtime import (
    PARAGRAPH_ACTIVE_PROMOTION,
    PARAGRAPH_VERSION_COMMIT,
    SECTION_PARAGRAPH_MANIFEST_SEAL,
    SECTION_PARAGRAPH_MANIFEST_UPDATE,
    ParagraphCurrentnessError,
    ParagraphIdentityCollision,
    ParagraphManifestConflict,
    ParagraphRuntime,
)
from apps.enterprise_document.schemas.agent_rag_v1.research import (
    SectionPlanSourceRefSchema,
)
from core.hashing import canonical_sha256
from core.runtime.side_effects import (
    CommittedResultResolutionStatus,
    SideEffectCoordinationStatus,
)

from backend.tests.test_gate_v2_pre_write_e2e import (
    _P4WriterInvocation,
    _p4_coordinator,
)
from backend.tests.test_multi_agent_v3_prewrite_gate_integration import (
    _P4DurableWriterInvocation,
    _apply as _graph_apply,
    _canonical_payload,
    _commit_initial_research_round,
)
from backend.tests.test_multi_agent_v3_research_runtime import (
    RUN_ID,
    SECTION_ID,
    _setup,
)


def _runtime(tmp_path):
    env = _setup(tmp_path / "upstream")
    validated_ops: list[str] = []
    validated_sources: list[str] = []
    validated_manifest_ops: list[str] = []

    def _authorization(operation, lineage) -> None:
        assert operation.run_id == lineage.run_id
        assert operation.write_instance_id == lineage.attempt_id
        validated_ops.append(operation.paragraph_operation_id)

    def _source(binding, lineage) -> None:
        assert binding.writer_subject_ref.run_id == lineage.run_id
        assert binding.write_instance_id == lineage.attempt_id
        validated_sources.append(binding.context_package_id)

    def _manifest_authorization(operation, lineage) -> None:
        assert operation.manifest_ref.attempt_id == lineage.attempt_id
        validated_manifest_ops.append(operation.manifest_operation_id)

    runtime = ParagraphRuntime(
        artifact_runtime=env["artifact"],
        authorization_validator=_authorization,
        source_validator=_source,
        manifest_authorization_validator=_manifest_authorization,
        manifest_ledger_path=tmp_path / "paragraph-manifest.jsonl",
    )
    return env, runtime, validated_ops, validated_sources, validated_manifest_ops


def _writer_key(env) -> WriterLogicalExecutionKey:
    lineage = env["lineage"]
    return WriterLogicalExecutionKey(
        writer_subject_ref=WriterSubjectRefSchema(
            run_id=RUN_ID,
            document_id="doc-1",
            section_id=SECTION_ID,
        ),
        write_instance_id=lineage.attempt_id,
        route_decision_ref=PreWriteRouteDecisionRefSchema(
            route_decision_id="pre-write-route-step7-b6"
        ),
    )


def _source_binding(env) -> ParagraphWriterSourceBindingSchema:
    key = _writer_key(env)
    return ParagraphWriterSourceBindingSchema(
        writer_subject_ref=key.writer_subject_ref,
        write_instance_id=key.write_instance_id,
        route_decision_ref=key.route_decision_ref,
        writer_gate_binding_ref=WriterGateBindingRefSchema(
            binding_id="writer-gate-binding-step7-b6"
        ),
        generation_class=WriterGenerationClass.NORMAL_WRITE,
        section_plan_source=SectionPlanSourceRefSchema(
            plan_version_ref=env["plan_version"],
            section_id=SECTION_ID,
        ),
        fact_snapshot_version_ref=env["fact_binding"].version_ref,
        evidence_package_version_ref=env["package_version"],
        context_package_id="context-package-step7-b6",
        context_sha256="c" * 64,
    )


def _candidate(
    env,
    runtime: ParagraphRuntime,
    *,
    slot: str = "initial-slot:0001",
    text: str = "Canonical paragraph one.",
    role: str = "body",
) -> ParagraphCandidateSchema:
    key = _writer_key(env)
    authorization_ref = runtime.initial_writer_authorization_ref(key)
    occurrence = runtime.creation_occurrence(
        run_id=RUN_ID,
        section_id=SECTION_ID,
        write_instance_id=env["lineage"].attempt_id,
        authorization_kind="INITIAL_WRITER",
        authorization_ref=authorization_ref,
        creation_slot_ref=slot,
    )
    operation = runtime.create_operation(occurrence)
    return ParagraphCandidateSchema(
        content_contract_version="paragraph-prose-v1",
        operation_ref=operation,
        source_binding=_source_binding(env),
        role=role,
        text=text,
    )


def _manifest_ref(env, runtime: ParagraphRuntime) -> SectionParagraphManifestRefSchema:
    return runtime.manifest_ref(
        run_id=RUN_ID,
        section_id=SECTION_ID,
        active_lineage=env["lineage"],
        write_instance_id=env["lineage"].attempt_id,
    )


def _commit_promote(env, runtime: ParagraphRuntime, candidate: ParagraphCandidateSchema):
    version = runtime.commit_paragraph_version(
        candidate=candidate,
        active_lineage=env["lineage"],
    )
    head = env["artifact"].resolve_active(candidate.operation_ref.target_logical_ref)
    assert head is not None
    receipt = runtime.promote_paragraph_active(
        candidate=candidate,
        target_version_ref=version,
        expected_head_revision=head.head_revision,
        expected_active_version_id=(
            head.active_version_ref.artifact_version_id
            if head.active_version_ref is not None
            else None
        ),
        active_lineage=env["lineage"],
    )
    return version, receipt


def _append(
    env,
    runtime: ParagraphRuntime,
    manifest_ref: SectionParagraphManifestRefSchema,
    *,
    expected_members,
    resulting_members,
    revision: int,
    authorization_ref: str,
    slot: str,
    state: str = "OPEN",
    mutation_kind: str = "APPEND",
):
    operation = runtime.manifest_operation(
        manifest_ref=manifest_ref,
        operation_kind=SECTION_PARAGRAPH_MANIFEST_UPDATE,
        authorization_ref=authorization_ref,
        stable_slot_ref=slot,
    )
    receipt = runtime.update_manifest(
        operation=operation,
        expected_manifest_revision=revision,
        expected_generation_membership_state=state,
        expected_members=tuple(expected_members),
        resulting_members=tuple(resulting_members),
        mutation_kind=mutation_kind,
        active_lineage=env["lineage"],
    )
    return operation, receipt


def test_creation_occurrence_identity_is_replay_stable_and_order_role_free(
    tmp_path,
) -> None:
    env, runtime, *_ = _runtime(tmp_path)
    key = _writer_key(env)
    auth = runtime.initial_writer_authorization_ref(key)
    first = runtime.creation_occurrence(
        run_id=RUN_ID,
        section_id=SECTION_ID,
        write_instance_id=env["lineage"].attempt_id,
        authorization_kind="INITIAL_WRITER",
        authorization_ref=auth,
        creation_slot_ref="writer-slot:alpha",
    )
    replay = ParagraphCreationOccurrenceRefSchema.model_validate(
        first.model_dump(mode="python")
    )

    op1 = runtime.create_operation(first)
    op2 = runtime.create_operation(replay)

    assert op1 == op2
    assert op1.target_logical_ref == op2.target_logical_ref
    assert "order" not in first.model_dump()
    assert "role" not in first.model_dump()


def test_candidate_text_or_role_changes_fingerprint_not_logical_identity(
    tmp_path,
) -> None:
    env, runtime, *_ = _runtime(tmp_path)
    first = _candidate(env, runtime, text="A", role="body")
    changed = _candidate(env, runtime, text="B", role="warning")

    assert first.operation_ref == changed.operation_ref
    assert (
        first.operation_ref.target_logical_ref
        == changed.operation_ref.target_logical_ref
    )
    assert runtime.semantic_fingerprint(first) != runtime.semantic_fingerprint(changed)


def test_version_commit_and_active_promotion_are_distinct_domain_and_child_ops(
    tmp_path,
) -> None:
    env, runtime, *_ = _runtime(tmp_path)
    candidate = _candidate(env, runtime)
    version_domain = runtime._paragraph_domain_operation(
        candidate.operation_ref,
        operation_kind=PARAGRAPH_VERSION_COMMIT,
        lineage=env["lineage"],
    )
    promotion_domain = runtime._paragraph_domain_operation(
        candidate.operation_ref,
        operation_kind=PARAGRAPH_ACTIVE_PROMOTION,
        lineage=env["lineage"],
    )
    version_child = runtime._artifact_child_operation(
        version_domain,
        artifact_operation_kind="ARTIFACT_VERSION_COMMIT",
    )
    promotion_child = runtime._artifact_child_operation(
        promotion_domain,
        artifact_operation_kind="ARTIFACT_ACTIVE_PROMOTION",
    )

    assert version_domain.logical_operation_id != promotion_domain.logical_operation_id
    assert version_domain.operation_kind == PARAGRAPH_VERSION_COMMIT
    assert promotion_domain.operation_kind == PARAGRAPH_ACTIVE_PROMOTION
    assert version_child.operation_kind == "ARTIFACT_VERSION_COMMIT"
    assert promotion_child.operation_kind == "ARTIFACT_ACTIVE_PROMOTION"
    assert version_child.logical_operation_id != promotion_child.logical_operation_id


def test_commit_replay_reuses_same_version_and_semantic_drift_collides(
    tmp_path,
) -> None:
    env, runtime, *_ = _runtime(tmp_path)
    candidate = _candidate(env, runtime)
    first = runtime.commit_paragraph_version(
        candidate=candidate,
        active_lineage=env["lineage"],
    )
    replay = runtime.commit_paragraph_version(
        candidate=candidate,
        active_lineage=env["lineage"],
    )
    assert replay == first

    drift = candidate.model_copy(update={"text": "materially changed"})
    with pytest.raises(Exception):
        runtime.commit_paragraph_version(
            candidate=drift,
            active_lineage=env["lineage"],
        )


def test_version_commit_before_promotion_is_not_self_current_and_recovers(
    tmp_path,
) -> None:
    env, runtime, *_ = _runtime(tmp_path)
    candidate = _candidate(env, runtime)
    version = runtime.commit_paragraph_version(
        candidate=candidate,
        active_lineage=env["lineage"],
    )
    head = env["artifact"].resolve_active(candidate.operation_ref.target_logical_ref)
    assert head is not None
    assert head.head_revision == 0
    assert head.active_version_ref is None

    restarted = ParagraphRuntime(
        artifact_runtime=env["artifact"],
        authorization_validator=runtime.authorization_validator,
        source_validator=runtime.source_validator,
        manifest_authorization_validator=runtime.manifest_authorization_validator,
        manifest_ledger_path=runtime.manifest_ledger_path,
    )
    same_version = restarted.commit_paragraph_version(
        candidate=candidate,
        active_lineage=env["lineage"],
    )
    assert same_version == version
    receipt = restarted.promote_paragraph_active(
        candidate=candidate,
        target_version_ref=version,
        expected_head_revision=0,
        expected_active_version_id=None,
        active_lineage=env["lineage"],
    )
    assert receipt.resulting_head_revision == 1


def test_manifest_initial_state_is_open_empty_and_promoted_unlisted_is_not_member(
    tmp_path,
) -> None:
    env, runtime, *_ = _runtime(tmp_path)
    manifest_ref = _manifest_ref(env, runtime)
    initial = runtime.load_manifest(manifest_ref)
    assert initial.manifest_revision == 0
    assert initial.generation_membership_state == "OPEN"
    assert initial.members == ()

    candidate = _candidate(env, runtime)
    version, _ = _commit_promote(env, runtime, candidate)
    assert runtime.resolve_paragraph_binding(
        logical_ref=version.logical_ref,
        manifest_ref=manifest_ref,
    ) is None


def test_manifest_append_publishes_exact_active_version_and_binding(
    tmp_path,
) -> None:
    env, runtime, validated_ops, validated_sources, validated_manifest_ops = _runtime(
        tmp_path
    )
    candidate = _candidate(env, runtime)
    version, _ = _commit_promote(env, runtime, candidate)
    manifest_ref = _manifest_ref(env, runtime)
    operation, receipt = _append(
        env,
        runtime,
        manifest_ref,
        expected_members=(),
        resulting_members=(version,),
        revision=0,
        authorization_ref=candidate.operation_ref.paragraph_operation_id,
        slot="append:paragraph-1",
    )

    current = runtime.load_manifest(manifest_ref)
    assert receipt.resulting_manifest_revision == 1
    assert current.manifest_revision == 1
    assert current.members == (version,)
    binding = runtime.resolve_paragraph_binding(
        logical_ref=version.logical_ref,
        manifest_ref=manifest_ref,
    )
    assert binding is not None
    assert runtime.validate_paragraph_binding_current(binding).text == candidate.text
    assert candidate.operation_ref.paragraph_operation_id in validated_ops
    assert validated_sources
    assert operation.manifest_operation_id in validated_manifest_ops


def test_manifest_same_operation_changed_result_is_collision_and_cas_is_strict(
    tmp_path,
) -> None:
    env, runtime, *_ = _runtime(tmp_path)
    p1 = _candidate(env, runtime, slot="slot:1", text="P1")
    v1, _ = _commit_promote(env, runtime, p1)
    p2 = _candidate(env, runtime, slot="slot:2", text="P2")
    v2, _ = _commit_promote(env, runtime, p2)
    manifest_ref = _manifest_ref(env, runtime)
    operation = runtime.manifest_operation(
        manifest_ref=manifest_ref,
        operation_kind=SECTION_PARAGRAPH_MANIFEST_UPDATE,
        authorization_ref=p1.operation_ref.paragraph_operation_id,
        stable_slot_ref="append:first",
    )
    runtime.update_manifest(
        operation=operation,
        expected_manifest_revision=0,
        expected_generation_membership_state="OPEN",
        expected_members=(),
        resulting_members=(v1,),
        mutation_kind="APPEND",
        active_lineage=env["lineage"],
    )

    with pytest.raises(ParagraphIdentityCollision):
        runtime.update_manifest(
            operation=operation,
            expected_manifest_revision=0,
            expected_generation_membership_state="OPEN",
            expected_members=(),
            resulting_members=(v2,),
            mutation_kind="APPEND",
            active_lineage=env["lineage"],
        )

    other = runtime.manifest_operation(
        manifest_ref=manifest_ref,
        operation_kind=SECTION_PARAGRAPH_MANIFEST_UPDATE,
        authorization_ref=p2.operation_ref.paragraph_operation_id,
        stable_slot_ref="append:second",
    )
    with pytest.raises(ParagraphManifestConflict):
        runtime.update_manifest(
            operation=other,
            expected_manifest_revision=0,
            expected_generation_membership_state="OPEN",
            expected_members=(),
            resulting_members=(v1, v2),
            mutation_kind="APPEND",
            active_lineage=env["lineage"],
        )


def test_historical_manifest_replay_after_later_revision_never_rolls_back(
    tmp_path,
) -> None:
    env, runtime, *_ = _runtime(tmp_path)
    p1 = _candidate(env, runtime, slot="slot:1", text="P1")
    v1, _ = _commit_promote(env, runtime, p1)
    p2 = _candidate(env, runtime, slot="slot:2", text="P2")
    v2, _ = _commit_promote(env, runtime, p2)
    manifest_ref = _manifest_ref(env, runtime)

    op1, receipt1 = _append(
        env,
        runtime,
        manifest_ref,
        expected_members=(),
        resulting_members=(v1,),
        revision=0,
        authorization_ref=p1.operation_ref.paragraph_operation_id,
        slot="append:1",
    )
    _op2, _receipt2 = _append(
        env,
        runtime,
        manifest_ref,
        expected_members=(v1,),
        resulting_members=(v1, v2),
        revision=1,
        authorization_ref=p2.operation_ref.paragraph_operation_id,
        slot="append:2",
    )
    assert runtime.load_manifest(manifest_ref).manifest_revision == 2

    replay = runtime.update_manifest(
        operation=op1,
        expected_manifest_revision=0,
        expected_generation_membership_state="OPEN",
        expected_members=(),
        resulting_members=(v1,),
        mutation_kind="APPEND",
        active_lineage=env["lineage"],
    )
    assert replay == receipt1
    current = runtime.load_manifest(manifest_ref)
    assert current.manifest_revision == 2
    assert current.members == (v1, v2)

    logical = runtime.manifest_logical_operation(op1)
    fingerprint = runtime.manifest_update_fingerprint(
        operation=op1,
        expected_manifest_revision=0,
        expected_generation_membership_state="OPEN",
        expected_members=(),
        resulting_members=(v1,),
        mutation_kind="APPEND",
    )
    resolution = runtime.resolve_manifest_commit(logical, fingerprint)
    assert resolution.resolution is CommittedResultResolutionStatus.COMMITTED_SAME


def test_repair_creates_new_version_preserves_logical_identity_and_replaces_manifest(
    tmp_path,
) -> None:
    env, runtime, *_ = _runtime(tmp_path)
    original = _candidate(env, runtime, text="V1")
    v1, _ = _commit_promote(env, runtime, original)
    manifest_ref = _manifest_ref(env, runtime)
    _append(
        env,
        runtime,
        manifest_ref,
        expected_members=(),
        resulting_members=(v1,),
        revision=0,
        authorization_ref=original.operation_ref.paragraph_operation_id,
        slot="append:v1",
    )
    binding_v1 = runtime.resolve_paragraph_binding(
        logical_ref=v1.logical_ref,
        manifest_ref=manifest_ref,
    )
    assert binding_v1 is not None

    revise_op = runtime.revise_operation(
        run_id=RUN_ID,
        section_id=SECTION_ID,
        write_instance_id=env["lineage"].attempt_id,
        target_logical_ref=v1.logical_ref,
        base_version_ref=v1,
        authorization_ref="paragraph-repair-authorization://1",
    )
    revised = ParagraphCandidateSchema(
        content_contract_version="paragraph-prose-v1",
        operation_ref=revise_op,
        source_binding=_source_binding(env),
        role="body",
        text="V2 repaired",
    )
    v2 = runtime.commit_paragraph_version(
        candidate=revised,
        active_lineage=env["lineage"],
    )
    receipt = runtime.promote_paragraph_active(
        candidate=revised,
        target_version_ref=v2,
        expected_head_revision=1,
        expected_active_version_id=v1.artifact_version_id,
        active_lineage=env["lineage"],
    )
    assert receipt.previous_active_version_id == v1.artifact_version_id
    assert v2.logical_ref == v1.logical_ref
    assert v2 != v1

    with pytest.raises(ParagraphCurrentnessError, match="SELF_CURRENT"):
        runtime.validate_paragraph_binding_current(binding_v1)

    _append(
        env,
        runtime,
        manifest_ref,
        expected_members=(v1,),
        resulting_members=(v2,),
        revision=1,
        authorization_ref=revise_op.authorization_ref,
        slot="replace:v2",
        mutation_kind="REPLACE_VERSION",
    )
    binding_v2 = runtime.resolve_paragraph_binding(
        logical_ref=v2.logical_ref,
        manifest_ref=manifest_ref,
    )
    assert binding_v2 is not None
    assert runtime.validate_paragraph_binding_current(binding_v2).text == "V2 repaired"
    assert runtime.read_paragraph(v1).text == "V1"


def test_reorder_changes_manifest_only_and_not_paragraph_versions(
    tmp_path,
) -> None:
    env, runtime, *_ = _runtime(tmp_path)
    p1 = _candidate(env, runtime, slot="slot:1", text="P1")
    v1, _ = _commit_promote(env, runtime, p1)
    p2 = _candidate(env, runtime, slot="slot:2", text="P2")
    v2, _ = _commit_promote(env, runtime, p2)
    manifest_ref = _manifest_ref(env, runtime)
    _append(
        env,
        runtime,
        manifest_ref,
        expected_members=(),
        resulting_members=(v1, v2),
        revision=0,
        authorization_ref=p1.operation_ref.authorization_ref,
        slot="initial-members",
        mutation_kind="INSERT",
    )
    _append(
        env,
        runtime,
        manifest_ref,
        expected_members=(v1, v2),
        resulting_members=(v2, v1),
        revision=1,
        authorization_ref="paragraph-reorder-authorization://1",
        slot="reorder:1",
        mutation_kind="REORDER",
    )

    current = runtime.load_manifest(manifest_ref)
    assert current.members == (v2, v1)
    assert runtime.assemble_manifest_text(manifest_ref) == "P2\n\nP1"
    assert env["artifact"].resolve_active(v1.logical_ref).active_version_ref == v1
    assert env["artifact"].resolve_active(v2.logical_ref).active_version_ref == v2


def test_manifest_seal_is_exactly_once_and_remains_sealed_for_repairs(
    tmp_path,
) -> None:
    env, runtime, *_ = _runtime(tmp_path)
    candidate = _candidate(env, runtime)
    version, _ = _commit_promote(env, runtime, candidate)
    manifest_ref = _manifest_ref(env, runtime)
    _append(
        env,
        runtime,
        manifest_ref,
        expected_members=(),
        resulting_members=(version,),
        revision=0,
        authorization_ref=candidate.operation_ref.authorization_ref,
        slot="append:before-seal",
    )
    seal_op = runtime.manifest_operation(
        manifest_ref=manifest_ref,
        operation_kind=SECTION_PARAGRAPH_MANIFEST_SEAL,
        authorization_ref="workflow-generation-complete://1",
        stable_slot_ref="seal:initial-generation",
    )
    first = runtime.seal_manifest(
        operation=seal_op,
        expected_manifest_revision=1,
        expected_members=(version,),
        active_lineage=env["lineage"],
    )
    replay = runtime.seal_manifest(
        operation=seal_op,
        expected_manifest_revision=1,
        expected_members=(version,),
        active_lineage=env["lineage"],
    )
    assert replay == first
    current = runtime.load_manifest(manifest_ref)
    assert current.manifest_revision == 2
    assert current.generation_membership_state == "SEALED"


def test_old_manifest_binding_fails_after_new_attempt_even_if_history_still_exists(
    tmp_path,
) -> None:
    env, runtime, *_ = _runtime(tmp_path)
    candidate = _candidate(env, runtime)
    version, _ = _commit_promote(env, runtime, candidate)
    manifest_ref = _manifest_ref(env, runtime)
    _append(
        env,
        runtime,
        manifest_ref,
        expected_members=(),
        resulting_members=(version,),
        revision=0,
        authorization_ref=candidate.operation_ref.authorization_ref,
        slot="append:old-attempt",
    )
    binding = runtime.resolve_paragraph_binding(
        logical_ref=version.logical_ref,
        manifest_ref=manifest_ref,
    )
    assert binding is not None

    opened = env["lifecycle"].open_attempt(
        RUN_ID,
        env["lineage"].attempt_scope_ref,
        "input-snapshot-paragraph-new-attempt",
    )
    env["lifecycle"].activate_attempt(
        opened.identity.attempt_id,
        opened.lifecycle_revision,
    )

    with pytest.raises(ParagraphCurrentnessError, match="lifecycle"):
        runtime.validate_paragraph_binding_current(binding)


def test_paragraph_content_schema_contains_no_order_or_version_integer_authority(
    tmp_path,
) -> None:
    env, runtime, *_ = _runtime(tmp_path)
    candidate = _candidate(env, runtime)
    version, _ = _commit_promote(env, runtime, candidate)
    content = runtime.read_paragraph(version)
    assert isinstance(content, ParagraphContentSchema)
    assert set(content.model_dump()) == {
        "schema_version",
        "content_contract_version",
        "role",
        "text",
        "source_binding",
    }
    assert not hasattr(content, "order")
    assert not hasattr(content, "paragraph_version")

class _B6DurableWriter(_P4DurableWriterInvocation):
    def _build_durable(self, *args, **kwargs):
        prepared = _P4WriterInvocation._build(*args, **kwargs)
        payload = prepared.invocation.model_dump(
            mode="python",
            round_trip=True,
        )
        payload["unified_writer_input_snapshot"] = {
            **dict(prepared.invocation.unified_writer_input_snapshot),
            "target_prompt_lock": {
                "context_package_id": "context-package-b6",
                "context_sha256": "d" * 64,
            },
        }
        invocation = WriterGenerationInvocationSchema.model_validate(payload)
        self.last_invocation = invocation
        return SimpleNamespace(
            invocation=invocation,
            logical_execution_key=prepared.logical_execution_key,
            semantic_fingerprint=canonical_sha256(invocation.model_dump(mode="json")),
        )


def _canonical_paragraph_graph(tmp_path, *, writer=None):
    env = _setup(tmp_path / "research")
    initial = _commit_initial_research_round(env)
    writer = writer or _B6DurableWriter()
    coordinator = _p4_coordinator(
        writer=writer,
        recovery_rounds_limit=2,
    )
    coordinator.attach_research_runtime(env["research"])

    runtime = ParagraphRuntime(
        artifact_runtime=env["artifact"],
        authorization_validator=lambda operation, lineage: (
            None
            if (
                operation.run_id == lineage.run_id
                and operation.write_instance_id == lineage.attempt_id
            )
            else (_ for _ in ()).throw(ValueError("paragraph authorization mismatch"))
        ),
        source_validator=lambda binding, lineage: (
            None
            if (
                binding.writer_subject_ref.run_id == lineage.run_id
                and binding.write_instance_id == lineage.attempt_id
            )
            else (_ for _ in ()).throw(ValueError("paragraph source mismatch"))
        ),
        manifest_authorization_validator=lambda operation, lineage: (
            None
            if operation.manifest_ref.attempt_id == lineage.attempt_id
            else (_ for _ in ()).throw(ValueError("manifest authorization mismatch"))
        ),
        manifest_ledger_path=tmp_path / "paragraph-manifest-graph.jsonl",
    )
    coordinator.attach_paragraph_runtime(runtime)
    graph = __import__(
        "apps.enterprise_document.workflows.section_execution_graph",
        fromlist=["SectionExecutionGraph"],
    ).SectionExecutionGraph(coordinator=coordinator)
    payload = _canonical_payload(graph, env, initial.binding)
    state = graph.codec.build_initial_state(payload)
    for method_name in (
        "_evaluate_pre_write_admission",
        "_bind_pre_write_gate",
        "_evaluate_pre_write_enforcement",
        "_commit_pre_write_route",
        "_invoke_pre_write_writer",
    ):
        state = _graph_apply(graph, state, method_name)
    return env, runtime, writer, graph, state


def test_graph_canonical_paragraph_path_never_calls_checkpoint_persist(tmp_path) -> None:
    env, runtime, _writer, graph, state = _canonical_paragraph_graph(tmp_path)
    graph.paragraph_pipeline.persist = lambda **kwargs: (_ for _ in ()).throw(
        AssertionError("legacy CheckpointParagraphPipeline.persist must be unreachable")
    )

    state = _graph_apply(graph, state, "_prepare_paragraph_pipeline")
    prepared = graph.codec.restore_payload(state)
    assert len(prepared.canonical_paragraph_candidates) == 1
    assert prepared.section_paragraph_manifest is not None
    assert prepared.section_paragraph_manifest.manifest_revision == 0
    assert prepared.paragraph_artifacts == []

    state = _graph_apply(graph, state, "_persist_paragraph")
    committed = graph.codec.restore_payload(state)
    assert committed.section_paragraph_manifest is not None
    assert committed.section_paragraph_manifest.manifest_revision == 1
    assert len(committed.paragraph_bindings) == 1
    assert len(committed.paragraph_artifacts) == 1
    assert not committed.paragraph_artifacts[0].content_ref.startswith("checkpoint://")
    assert runtime.read_paragraph(
        committed.paragraph_bindings[0].version_ref
    ).text == "canonical Writer artifact"


def test_graph_manifest_ahead_repairs_stale_checkpoint_without_duplicate_commit(
    tmp_path,
) -> None:
    _env, runtime, _writer, graph, state = _canonical_paragraph_graph(tmp_path)
    prepared_state = _graph_apply(graph, state, "_prepare_paragraph_pipeline")
    first = _graph_apply(graph, prepared_state, "_persist_paragraph")
    first_payload = graph.codec.restore_payload(first)
    version = first_payload.paragraph_bindings[0].version_ref
    manifest_ref = first_payload.section_paragraph_manifest.manifest_ref
    before = runtime.load_manifest(manifest_ref)
    assert before.manifest_revision == 1

    replay = _graph_apply(graph, prepared_state, "_persist_paragraph")
    replay_payload = graph.codec.restore_payload(replay)
    after = runtime.load_manifest(manifest_ref)

    assert after == before
    assert replay_payload.section_paragraph_manifest == after
    assert replay_payload.paragraph_bindings[0].version_ref == version
    head = runtime.artifact_runtime.resolve_active(version.logical_ref)
    assert head is not None
    assert head.head_revision == 1
    assert head.active_version_ref == version


def test_graph_section_snapshot_ignores_checkpoint_paragraph_projection(
    tmp_path,
) -> None:
    _env, _runtime, _writer, graph, state = _canonical_paragraph_graph(tmp_path)
    state = _graph_apply(graph, state, "_prepare_paragraph_pipeline")
    state = _graph_apply(graph, state, "_persist_paragraph")
    payload = graph.codec.restore_payload(state)
    tampered = payload.model_copy(
        update={"paragraph_artifacts": []},
        deep=True,
    )

    projected = graph._paragraph_section_snapshot(
        tampered,
        blocked_paragraph_id=None,
    )
    assert projected.content == "canonical Writer artifact"
    assert projected.extra["paragraph_pipeline"]["artifact_authority"] == (
        "paragraph_runtime"
    )


def test_graph_legacy_precheck_blocking_is_observation_only_under_b6(
    tmp_path,
) -> None:
    _env, runtime, _writer, graph, state = _canonical_paragraph_graph(tmp_path)
    state = _graph_apply(graph, state, "_prepare_paragraph_pipeline")
    state = _graph_apply(graph, state, "_persist_paragraph")
    committed = graph.codec.restore_payload(state)
    artifact = committed.paragraph_artifacts[0]
    manifest_before = runtime.load_manifest(
        committed.section_paragraph_manifest.manifest_ref
    )

    graph.paragraph_pipeline.precheck_paragraph = lambda **kwargs: (
        ParagraphPrecheckResultSchema(
            section_id=str(committed.section_id),
            paragraph_id=artifact.paragraph_id,
            paragraph_version=artifact.paragraph_version,
            passed=False,
            blocking=True,
            claims=[],
            issues=[],
        )
    )
    state = _graph_apply(graph, state, "_precheck_paragraph")
    checked = graph.codec.restore_payload(state)
    manifest_after = runtime.load_manifest(
        checked.section_paragraph_manifest.manifest_ref
    )

    assert manifest_after == manifest_before
    assert checked.paragraph_blocked_id is None
    assert checked.generation_check["paragraph_precheck_blocking"] is False
    assert checked.generation_check["paragraph_precheck_compat_blocking"] is True
    assert checked.section.content == "canonical Writer artifact"
    assert state["section_route"] == "evaluate_generation"


def test_graph_canonical_paragraph_source_requires_managed_context_identity(
    tmp_path,
) -> None:
    writer = _P4DurableWriterInvocation()
    _env, _runtime, _writer, graph, state = _canonical_paragraph_graph(
        tmp_path,
        writer=writer,
    )

    with pytest.raises(ValueError, match="managed Context identity"):
        graph._prepare_paragraph_pipeline(state)


def test_manifest_replay_reconciles_missing_coordination_from_canonical_receipt(
    tmp_path,
) -> None:
    env, runtime, *_ = _runtime(tmp_path)
    candidate = _candidate(env, runtime)
    version, _ = _commit_promote(env, runtime, candidate)
    manifest_ref = _manifest_ref(env, runtime)
    operation, receipt = _append(
        env,
        runtime,
        manifest_ref,
        expected_members=(),
        resulting_members=(version,),
        revision=0,
        authorization_ref=candidate.operation_ref.authorization_ref,
        slot="append:coordination-reconcile",
    )
    coordination_path = runtime.manifest_ledger_path.with_suffix(
        runtime.manifest_ledger_path.suffix + ".coordination"
    )
    assert coordination_path.exists()
    coordination_path.unlink()

    restarted = ParagraphRuntime(
        artifact_runtime=env["artifact"],
        authorization_validator=runtime.authorization_validator,
        source_validator=runtime.source_validator,
        manifest_authorization_validator=runtime.manifest_authorization_validator,
        manifest_ledger_path=runtime.manifest_ledger_path,
    )
    replay = restarted.update_manifest(
        operation=operation,
        expected_manifest_revision=0,
        expected_generation_membership_state="OPEN",
        expected_members=(),
        resulting_members=(version,),
        mutation_kind="APPEND",
        active_lineage=env["lineage"],
    )
    assert replay == receipt
    logical = restarted.manifest_logical_operation(operation)
    fingerprint = restarted.manifest_update_fingerprint(
        operation=operation,
        expected_manifest_revision=0,
        expected_generation_membership_state="OPEN",
        expected_members=(),
        resulting_members=(version,),
        mutation_kind="APPEND",
    )
    coordination = restarted.coordination_store.resolve(
        logical.logical_operation_id,
        semantic_fingerprint=fingerprint,
    )
    assert coordination.status is SideEffectCoordinationStatus.COMMITTED


def test_structural_split_merge_remove_use_manifest_only_and_keep_history(
    tmp_path,
) -> None:
    env, runtime, *_ = _runtime(tmp_path)
    original = _candidate(env, runtime, slot="structural:original", text="Original")
    v0, _ = _commit_promote(env, runtime, original)
    manifest_ref = _manifest_ref(env, runtime)
    _append(
        env,
        runtime,
        manifest_ref,
        expected_members=(),
        resulting_members=(v0,),
        revision=0,
        authorization_ref=original.operation_ref.authorization_ref,
        slot="append:original",
    )

    split_auth = "paragraph-repair-authorization://split-1"
    split_versions = []
    for slot, text in (("split:child:1", "Child 1"), ("split:child:2", "Child 2")):
        occurrence = runtime.creation_occurrence(
            run_id=RUN_ID,
            section_id=SECTION_ID,
            write_instance_id=env["lineage"].attempt_id,
            authorization_kind="REPAIR",
            authorization_ref=split_auth,
            creation_slot_ref=slot,
        )
        operation = runtime.create_operation(
            occurrence,
            operation_kind="STRUCTURAL_CREATE",
        )
        candidate = ParagraphCandidateSchema(
            content_contract_version="paragraph-prose-v1",
            operation_ref=operation,
            source_binding=_source_binding(env),
            role="body",
            text=text,
        )
        split_versions.append(_commit_promote(env, runtime, candidate)[0])

    split_op = runtime.manifest_operation(
        manifest_ref=manifest_ref,
        operation_kind=SECTION_PARAGRAPH_MANIFEST_UPDATE,
        authorization_ref=split_auth,
        stable_slot_ref="manifest:split:1",
    )
    runtime.update_manifest(
        operation=split_op,
        expected_manifest_revision=1,
        expected_generation_membership_state="OPEN",
        expected_members=(v0,),
        resulting_members=tuple(split_versions),
        mutation_kind="SPLIT",
        active_lineage=env["lineage"],
    )
    after_split = runtime.load_manifest(manifest_ref)
    assert after_split.members == tuple(split_versions)
    assert all(item.logical_ref != v0.logical_ref for item in split_versions)
    assert runtime.read_paragraph(v0).text == "Original"

    merge_auth = "paragraph-repair-authorization://merge-1"
    merge_occurrence = runtime.creation_occurrence(
        run_id=RUN_ID,
        section_id=SECTION_ID,
        write_instance_id=env["lineage"].attempt_id,
        authorization_kind="REPAIR",
        authorization_ref=merge_auth,
        creation_slot_ref="merge:result:1",
    )
    merge_candidate = ParagraphCandidateSchema(
        content_contract_version="paragraph-prose-v1",
        operation_ref=runtime.create_operation(
            merge_occurrence,
            operation_kind="STRUCTURAL_CREATE",
        ),
        source_binding=_source_binding(env),
        role="body",
        text="Merged",
    )
    merged, _ = _commit_promote(env, runtime, merge_candidate)
    merge_op = runtime.manifest_operation(
        manifest_ref=manifest_ref,
        operation_kind=SECTION_PARAGRAPH_MANIFEST_UPDATE,
        authorization_ref=merge_auth,
        stable_slot_ref="manifest:merge:1",
    )
    runtime.update_manifest(
        operation=merge_op,
        expected_manifest_revision=2,
        expected_generation_membership_state="OPEN",
        expected_members=tuple(split_versions),
        resulting_members=(merged,),
        mutation_kind="MERGE",
        active_lineage=env["lineage"],
    )
    after_merge = runtime.load_manifest(manifest_ref)
    assert after_merge.members == (merged,)
    assert merged.logical_ref not in {item.logical_ref for item in split_versions}

    remove_auth = "paragraph-repair-authorization://remove-1"
    remove_op = runtime.manifest_operation(
        manifest_ref=manifest_ref,
        operation_kind=SECTION_PARAGRAPH_MANIFEST_UPDATE,
        authorization_ref=remove_auth,
        stable_slot_ref="manifest:remove:1",
    )
    runtime.update_manifest(
        operation=remove_op,
        expected_manifest_revision=3,
        expected_generation_membership_state="OPEN",
        expected_members=(merged,),
        resulting_members=(),
        mutation_kind="REMOVE",
        active_lineage=env["lineage"],
    )
    assert runtime.load_manifest(manifest_ref).members == ()
    assert runtime.read_paragraph(merged).text == "Merged"
    assert runtime.read_paragraph(split_versions[0]).text == "Child 1"
    assert runtime.read_paragraph(split_versions[1]).text == "Child 2"
