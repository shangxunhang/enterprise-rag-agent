from __future__ import annotations

import hashlib

import pytest

from apps.enterprise_document.schemas.agent_rag_v1.artifacts import ArtifactVersionRefSchema
from apps.enterprise_document.schemas.agent_rag_v1.facts import (
    CanonicalFactRecordSchema,
    FactSourceBindingSchema,
)
from apps.enterprise_document.schemas.agent_rag_v1.lifecycle import (
    RunIdentitySchema,
    RunLifecycleStatus,
)
from apps.enterprise_document.schemas.agent_rag_v1.research import (
    CanonicalEvidenceBundleBindingSchema,
    EvidencePackageContentSchema,
    EvidencePackageSelectionSchema,
    ResearchPackageProgressSchema,
    ResearchRoundAuthorizationBindingSchema,
    ResearchRoundExecutionOutcome,
    ResearchRoundRecordSchema,
    SectionPlanSourceRefSchema,
)
from apps.enterprise_document.schemas.pre_write_admission import (
    EvidenceCriterionSchema,
    EvidenceRequirementSchema,
    EvidenceRequirementsSnapshotSchema,
)
from apps.enterprise_document.schemas.scheme_writer import (
    EvidenceRequirementsSourceRefSchema,
    SectionPlanContentSchema,
    SectionPlanEntrySchema,
    SectionStrategyCode,
)
from apps.enterprise_document.services.agent_rag_v1 import (
    ArtifactRuntime,
    EvidencePackageCommitOperations,
    EvidencePackageCurrentnessError,
    EvidencePackageSourceBindingError,
    FactSnapshotRuntime,
    ResearchIdentityCollision,
    ResearchProgressConflict,
    ResearchRuntime,
    SectionPlanCommitOperations,
    SectionPlanRuntime,
)
from apps.enterprise_document.services.agent_rag_v1.lifecycle_runtime import (
    RunAttemptLifecycleService,
)
from contracts.evidence import build_evidence_bundle, evidence_bundle_sha256
from core.runtime.side_effects import (
    LogicalOperationRef,
    SideEffectClaim,
    SideEffectClaimStatus,
    SideEffectCoordinationResolution,
    SideEffectCoordinationStatus,
)
from schemas.citation import CitationSchema
from schemas.rag import RetrievedChunkSchema


RUN_ID = "run-research-1"
TASK_ID = "task-research-1"
SECTION_ID = "s1"
REQUEST_SHA = hashlib.sha256(b"step6-research-request").hexdigest()
REQ_SHA = "a" * 64


class _Clock:
    def now_iso(self) -> str:
        return "2026-09-04T08:00:00+00:00"


class _CrashAfterCanonicalAppendCoordinationStore:
    """Fault-injection SIF-02 store: canonical append succeeds, ack crashes."""

    def __init__(self) -> None:
        self.side_effect_id: str | None = None
        self.fingerprint: str | None = None

    def claim(
        self,
        side_effect_id: str,
        *,
        execution_id: str,
        description: str,
        semantic_fingerprint: str | None = None,
    ) -> SideEffectClaim:
        if self.side_effect_id is None:
            self.side_effect_id = side_effect_id
            self.fingerprint = semantic_fingerprint
            return SideEffectClaim(
                status=SideEffectClaimStatus.ACQUIRED,
                side_effect_id=side_effect_id,
                execution_id=execution_id,
                description=description,
                claim_token="crash-window-token",
                semantic_fingerprint=semantic_fingerprint,
            )
        return SideEffectClaim(
            status=SideEffectClaimStatus.ALREADY_IN_PROGRESS,
            side_effect_id=side_effect_id,
            execution_id=execution_id,
            description=description,
            semantic_fingerprint=semantic_fingerprint,
        )

    def resolve(
        self,
        side_effect_id: str,
        *,
        semantic_fingerprint: str | None = None,
    ) -> SideEffectCoordinationResolution:
        assert side_effect_id == self.side_effect_id
        assert semantic_fingerprint == self.fingerprint
        return SideEffectCoordinationResolution(
            status=SideEffectCoordinationStatus.IN_PROGRESS,
            side_effect_id=side_effect_id,
            semantic_fingerprint=semantic_fingerprint,
        )

    def mark_committed(self, claim, *, committed_result_refs=()):
        del claim, committed_result_refs
        raise RuntimeError("simulated coordination ack crash")

    def release(self, claim, *, reason: str) -> bool:
        del claim, reason
        return True

    def wait_for_resolution(self, side_effect_id: str, *, timeout_seconds: float) -> None:
        del side_effect_id, timeout_seconds

    def finish_execution(self, claim) -> None:
        del claim


class _Ids:
    def __init__(self) -> None:
        self.i = 0

    def new_id(self, prefix: str) -> str:
        self.i += 1
        return f"{prefix}_{self.i:03d}"


def _op(
    identity: str,
    kind: str,
    *,
    attempt_scope_ref: str | None = None,
    attempt_id: str | None = None,
) -> LogicalOperationRef:
    return LogicalOperationRef(
        logical_operation_id=identity,
        operation_kind=kind,
        run_id=RUN_ID,
        operation_scope_ref=f"section:{SECTION_ID}",
        attempt_scope_ref=attempt_scope_ref,
        attempt_id=attempt_id,
    )


def _requirements() -> EvidenceRequirementsSnapshotSchema:
    return EvidenceRequirementsSnapshotSchema(
        requirements_sha256=REQ_SHA,
        source_profile_ref="profile:research:v1",
        source_task_ref="task-contract:research:v1",
        requirements=(
            EvidenceRequirementSchema(
                requirement_id="req-1",
                criteria=(
                    EvidenceCriterionSchema(
                        criterion_id="criterion-1",
                        criterion="must have evidence",
                    ),
                ),
            ),
        ),
    )


def _requirements_ref() -> EvidenceRequirementsSourceRefSchema:
    return EvidenceRequirementsSourceRefSchema(
        requirements_sha256=REQ_SHA,
        source_profile_ref="profile:research:v1",
        source_task_ref="task-contract:research:v1",
    )


def _bundle():
    chunk = RetrievedChunkSchema(
        rank=1,
        matched_chunk_id="child-1",
        context_chunk_id="parent-1",
        child_chunk_id="child-1",
        parent_chunk_id="parent-1",
        doc_id="doc-1",
        match_text="matched evidence",
        context_text="canonical evidence text",
        title="Evidence",
    )
    citation = CitationSchema(
        citation_id="source-local",
        source_type="document",
        source_document_id="doc-1",
        doc_id="doc-1",
        child_chunk_id="child-1",
        parent_chunk_id="parent-1",
        quote_text="canonical evidence text",
        title="Evidence",
    )
    bundle = build_evidence_bundle(
        query="research query",
        rewritten_queries=[],
        selected_chunks=[chunk],
        dropped_chunks=[],
        citations=[citation],
        trace=None,
        max_context_chars=6000,
    )
    return bundle.model_copy(
        update={
            "task_id": TASK_ID,
            "run_id": RUN_ID,
            "retrieval_trace_id": "trace-research-1",
            "retrieval_scope": "section",
        },
        deep=True,
    )


def _setup(tmp_path, *, coordination_store=None):
    lifecycle = RunAttemptLifecycleService(
        tmp_path / "lifecycle.jsonl",
        clock=_Clock(),
        id_generator=_Ids(),
    )
    created = lifecycle.claim_new_run(
        RunIdentitySchema(
            run_id=RUN_ID,
            task_id=TASK_ID,
            tenant_id="tenant",
            canonical_request_sha256=REQUEST_SHA,
        )
    )
    lifecycle.transition_run(
        RUN_ID,
        created.lifecycle_revision,
        RunLifecycleStatus.RUNNING,
    )

    artifact = ArtifactRuntime(
        tmp_path / "artifact-runtime.jsonl",
        lifecycle_service=lifecycle,
    )
    fact = FactSnapshotRuntime(
        artifact_runtime=artifact,
        source_binding_resolver=lambda binding: bool(binding.source_version_ref),
    )
    requirements = _requirements()
    plan = SectionPlanRuntime(
        artifact_runtime=artifact,
        fact_snapshot_runtime=fact,
        requirements_resolver=lambda ref: requirements,
    )

    source = FactSourceBindingSchema(
        source_type="project_input",
        source_ref="project://research",
        source_version_ref="project-version://1",
    )
    fact_logical = fact.logical_ref_for_scope(
        run_id=RUN_ID,
        domain_scope_ref="project:doc-1",
    )
    fact_content = fact.build_content_from_authoritative_sources(
        normalization_contract_version="facts-v1",
        source_bindings=[source],
        facts=[
            CanonicalFactRecordSchema(
                fact_id="fact-1",
                fact_type="project_fact",
                normalized_value={"value": 1},
                source_bindings=[source],
            )
        ],
    )
    fact_version = fact.commit_snapshot(
        logical_ref=fact_logical,
        content=fact_content,
        operation_ref=_op("fact-commit", "ARTIFACT_VERSION_COMMIT"),
    )
    fact_binding = fact.promote_snapshot(
        logical_ref=fact_logical,
        target_version_ref=fact_version,
        expected_head_revision=0,
        expected_active_version_id=None,
        operation_ref=_op("fact-promote", "ARTIFACT_ACTIVE_PROMOTION"),
    )

    plan_logical = plan.logical_ref_for_scope(
        run_id=RUN_ID,
        document_scope_ref="document:doc-1",
    )
    plan_content = SectionPlanContentSchema(
        planning_contract_version="section-plan-semantics-v1",
        fact_snapshot_version_ref=fact_binding.version_ref,
        sections=(
            SectionPlanEntrySchema(
                section_id=SECTION_ID,
                section_title="Section 1",
                section_order=0,
                applicability="applicable",
                strategy=SectionStrategyCode.RAG_GENERATE,
                citation_required=True,
                evidence_requirements_source=_requirements_ref(),
                evidence_requirement_ids=("req-1",),
            ),
        ),
    )
    plan_version = plan.commit_plan(
        candidate_content=plan_content,
        logical_ref=plan_logical,
        fact_snapshot_binding=fact_binding,
        expected_head_revision=0,
        expected_active_version_id=None,
        operations=SectionPlanCommitOperations(
            version_commit=_op("plan-commit", "ARTIFACT_VERSION_COMMIT"),
            active_promotion=_op("plan-promote", "ARTIFACT_ACTIVE_PROMOTION"),
        ),
    )
    plan_binding = plan.resolve_current_plan_binding(
        run_id=RUN_ID,
        document_scope_ref="document:doc-1",
        section_id=SECTION_ID,
    )
    assert plan_binding is not None
    assert plan_binding.plan_version_ref == plan_version

    attempt_scope = f"section:{SECTION_ID}"
    opened = lifecycle.open_attempt(
        RUN_ID,
        attempt_scope,
        "input-snapshot-research-v1",
    )
    running = lifecycle.activate_attempt(
        opened.identity.attempt_id,
        opened.lifecycle_revision,
    )
    lineage = lifecycle.resolve_active_lineage(RUN_ID, attempt_scope)
    assert running.identity.attempt_id == lineage.attempt_id

    bundle = _bundle()
    bundle_handle = "rag-evidence-bundle://opaque/bundle-1"
    bundles = {bundle_handle: bundle}
    validated_authorizations: list[str] = []

    def _authorization_validator(binding, round_ref) -> None:
        assert round_ref.run_id == RUN_ID
        assert round_ref.section_id == SECTION_ID
        validated_authorizations.append(binding.authorization_ref)

    research = ResearchRuntime(
        artifact_runtime=artifact,
        section_plan_runtime=plan,
        fact_snapshot_runtime=fact,
        evidence_bundle_resolver=lambda handle: bundles[handle],
        authorization_validator=_authorization_validator,
        lifecycle_service=lifecycle,
        ledger_path=tmp_path / "research-runtime.jsonl",
        coordination_store=coordination_store,
    )
    bundle_binding = CanonicalEvidenceBundleBindingSchema(
        evidence_bundle_handle=bundle_handle,
        evidence_bundle_sha256="sha256:" + evidence_bundle_sha256(bundle),
    )
    package_content = EvidencePackageContentSchema(
        projection_contract_version="evidence-package-projection-v1",
        section_plan_source=SectionPlanSourceRefSchema(
            plan_version_ref=plan_version,
            section_id=SECTION_ID,
        ),
        evidence_bundle_binding=bundle_binding,
        selections=(
            EvidencePackageSelectionSchema(
                evidence_id="E1",
                requirement_ids=("req-1",),
            ),
        ),
    )
    package_logical = research.evidence_package_logical_ref(
        run_id=RUN_ID,
        section_id=SECTION_ID,
    )
    package_version = research.commit_evidence_package(
        candidate_content=package_content,
        logical_ref=package_logical,
        plan_binding=plan_binding,
        expected_head_revision=0,
        expected_active_version_id=None,
        operations=EvidencePackageCommitOperations(
            version_commit=_op(
                "package-commit-1",
                "ARTIFACT_VERSION_COMMIT",
                attempt_scope_ref=attempt_scope,
                attempt_id=lineage.attempt_id,
            ),
            active_promotion=_op(
                "package-promote-1",
                "ARTIFACT_ACTIVE_PROMOTION",
                attempt_scope_ref=attempt_scope,
                attempt_id=lineage.attempt_id,
            ),
        ),
        commit_guard_context=lineage,
    )
    package_binding = research.resolve_current_evidence_package_binding(
        run_id=RUN_ID,
        section_id=SECTION_ID,
    )
    assert package_binding is not None
    assert package_binding.version_ref == package_version

    return {
        "lifecycle": lifecycle,
        "artifact": artifact,
        "fact": fact,
        "fact_binding": fact_binding,
        "plan": plan,
        "plan_logical": plan_logical,
        "plan_binding": plan_binding,
        "plan_version": plan_version,
        "lineage": lineage,
        "bundle": bundle,
        "bundle_binding": bundle_binding,
        "bundles": bundles,
        "research": research,
        "package_content": package_content,
        "package_logical": package_logical,
        "package_version": package_version,
        "package_binding": package_binding,
        "validated_authorizations": validated_authorizations,
    }


def _round_record(env, *, authorization_ref: str = "workflow-start-research://1"):
    research: ResearchRuntime = env["research"]
    lineage = env["lineage"]
    authorization = ResearchRoundAuthorizationBindingSchema(
        authorization_kind="WORKFLOW_RESEARCH",
        authorization_ref=authorization_ref,
    )
    round_ref = research.derive_round_ref(
        run_id=RUN_ID,
        section_id=SECTION_ID,
        authorization_binding=authorization,
        active_attempt=lineage,
    )
    record = ResearchRoundRecordSchema(
        round_ref=round_ref,
        round_ordinal=1,
        authorization_binding=authorization,
        source_plan_version_ref=env["plan_version"],
        execution_outcome=ResearchRoundExecutionOutcome.COMPLETED,
        final_evidence_bundle_binding=env["bundle_binding"],
        package_progress=ResearchPackageProgressSchema(
            resulting_evidence_package_version_ref=env["package_version"],
            material_package_change="NEW_PACKAGE",
        ),
        tool_operation_refs=(
            _op(
                "knowledge-retrieve-1",
                "TOOL_INVOCATION",
                attempt_scope_ref=lineage.attempt_scope_ref,
                attempt_id=lineage.attempt_id,
            ),
        ),
        budget_receipt_refs=("budget-receipt://research-1",),
    )
    return record


def test_evidence_package_binds_exact_bundle_plan_and_requirement(tmp_path) -> None:
    env = _setup(tmp_path)
    content = env["research"].validate_evidence_package_binding_current(
        env["package_binding"]
    )
    assert content == env["package_content"]
    assert content.evidence_bundle_binding == env["bundle_binding"]
    assert content.selections[0].requirement_ids == ("req-1",)


def test_package_reuses_current_version_when_material_semantics_are_unchanged(
    tmp_path,
) -> None:
    env = _setup(tmp_path)
    lineage = env["lineage"]
    replay = env["research"].commit_evidence_package(
        candidate_content=env["package_content"],
        logical_ref=env["package_logical"],
        plan_binding=env["plan_binding"],
        expected_head_revision=1,
        expected_active_version_id=env["package_version"].artifact_version_id,
        operations=EvidencePackageCommitOperations(
            version_commit=_op(
                "package-commit-unused",
                "ARTIFACT_VERSION_COMMIT",
                attempt_scope_ref=lineage.attempt_scope_ref,
                attempt_id=lineage.attempt_id,
            ),
            active_promotion=_op(
                "package-promote-unused",
                "ARTIFACT_ACTIVE_PROMOTION",
                attempt_scope_ref=lineage.attempt_scope_ref,
                attempt_id=lineage.attempt_id,
            ),
        ),
        commit_guard_context=lineage,
    )
    assert replay == env["package_version"]
    head = env["artifact"].resolve_active(env["package_logical"])
    assert head is not None
    assert head.head_revision == 1


def test_package_rejects_hash_only_or_wrong_opaque_bundle_binding(tmp_path) -> None:
    env = _setup(tmp_path)
    bad = env["package_content"].model_copy(
        update={
            "evidence_bundle_binding": env["bundle_binding"].model_copy(
                update={"evidence_bundle_sha256": "sha256:" + "b" * 64}
            )
        }
    )
    lineage = env["lineage"]
    with pytest.raises(EvidencePackageSourceBindingError, match="handle/hash"):
        env["research"].commit_evidence_package(
            candidate_content=bad,
            logical_ref=env["package_logical"],
            plan_binding=env["plan_binding"],
            expected_head_revision=1,
            expected_active_version_id=env["package_version"].artifact_version_id,
            operations=EvidencePackageCommitOperations(
                version_commit=_op(
                    "package-bad-hash",
                    "ARTIFACT_VERSION_COMMIT",
                    attempt_scope_ref=lineage.attempt_scope_ref,
                    attempt_id=lineage.attempt_id,
                ),
                active_promotion=_op(
                    "package-bad-hash-p",
                    "ARTIFACT_ACTIVE_PROMOTION",
                    attempt_scope_ref=lineage.attempt_scope_ref,
                    attempt_id=lineage.attempt_id,
                ),
            ),
            commit_guard_context=lineage,
        )


def test_package_rejects_evidence_or_requirement_not_selected_by_exact_sources(
    tmp_path,
) -> None:
    env = _setup(tmp_path)
    lineage = env["lineage"]
    bad_evidence = env["package_content"].model_copy(
        update={
            "selections": (
                EvidencePackageSelectionSchema(
                    evidence_id="E999",
                    requirement_ids=("req-1",),
                ),
            )
        }
    )
    with pytest.raises(EvidencePackageSourceBindingError, match="selected_evidence_ids"):
        env["research"].commit_evidence_package(
            candidate_content=bad_evidence,
            logical_ref=env["package_logical"],
            plan_binding=env["plan_binding"],
            expected_head_revision=1,
            expected_active_version_id=env["package_version"].artifact_version_id,
            operations=EvidencePackageCommitOperations(
                version_commit=_op(
                    "package-bad-evidence",
                    "ARTIFACT_VERSION_COMMIT",
                    attempt_scope_ref=lineage.attempt_scope_ref,
                    attempt_id=lineage.attempt_id,
                ),
                active_promotion=_op(
                    "package-bad-evidence-p",
                    "ARTIFACT_ACTIVE_PROMOTION",
                    attempt_scope_ref=lineage.attempt_scope_ref,
                    attempt_id=lineage.attempt_id,
                ),
            ),
            commit_guard_context=lineage,
        )

    bad_requirement = env["package_content"].model_copy(
        update={
            "selections": (
                EvidencePackageSelectionSchema(
                    evidence_id="E1",
                    requirement_ids=("req-other",),
                ),
            )
        }
    )
    with pytest.raises(EvidencePackageSourceBindingError, match="requirement"):
        env["research"].commit_evidence_package(
            candidate_content=bad_requirement,
            logical_ref=env["package_logical"],
            plan_binding=env["plan_binding"],
            expected_head_revision=1,
            expected_active_version_id=env["package_version"].artifact_version_id,
            operations=EvidencePackageCommitOperations(
                version_commit=_op(
                    "package-bad-req",
                    "ARTIFACT_VERSION_COMMIT",
                    attempt_scope_ref=lineage.attempt_scope_ref,
                    attempt_id=lineage.attempt_id,
                ),
                active_promotion=_op(
                    "package-bad-req-p",
                    "ARTIFACT_ACTIVE_PROMOTION",
                    attempt_scope_ref=lineage.attempt_scope_ref,
                    attempt_id=lineage.attempt_id,
                ),
            ),
            commit_guard_context=lineage,
        )


def test_authorize_round_ref_validates_current_sources_before_research_side_effect(
    tmp_path,
) -> None:
    env = _setup(tmp_path)
    authorization = ResearchRoundAuthorizationBindingSchema(
        authorization_kind="WORKFLOW_RESEARCH",
        authorization_ref="workflow-start-research://pre-side-effect",
    )

    ref = env["research"].authorize_round_ref(
        run_id=RUN_ID,
        section_id=SECTION_ID,
        source_plan_binding=env["plan_binding"],
        authorization_binding=authorization,
        active_attempt=env["lineage"],
    )

    assert ref.run_id == RUN_ID
    assert ref.section_id == SECTION_ID
    assert ref.attempt_id == env["lineage"].attempt_id
    assert env["validated_authorizations"][-1] == authorization.authorization_ref
    snapshot, selected_ids = env["research"].resolve_research_requirements(
        env["plan_binding"]
    )
    assert snapshot == _requirements()
    assert selected_ids == ("req-1",)


def test_round_commit_advances_atomic_progress_once_and_replay_reuses(tmp_path) -> None:
    env = _setup(tmp_path)
    record = _round_record(env)
    operation = env["research"].round_operation_ref(record.round_ref)

    first = env["research"].commit_round(
        round_candidate=record,
        operation_ref=operation,
        source_plan_binding=env["plan_binding"],
        resulting_package_binding=env["package_binding"],
        commit_guard_context=env["lineage"],
        expected_progress_revision=0,
        expected_committed_round_count=0,
        expected_last_committed_round_ref=None,
    )
    assert first.reused is False
    assert first.progress.progress_revision == 1
    assert first.progress.committed_round_count == 1
    assert first.progress.last_committed_round_ref == record.round_ref
    assert first.binding.resolved_progress_revision == 1
    assert env["validated_authorizations"]
    assert set(env["validated_authorizations"]) == {
        record.authorization_binding.authorization_ref
    }

    replay = env["research"].commit_round(
        round_candidate=record,
        operation_ref=operation,
        source_plan_binding=env["plan_binding"],
        resulting_package_binding=env["package_binding"],
        commit_guard_context=env["lineage"],
        expected_progress_revision=0,
        expected_committed_round_count=0,
        expected_last_committed_round_ref=None,
    )
    assert replay.reused is True
    assert replay.progress == first.progress
    current = env["research"].resolve_progress(
        run_id=RUN_ID,
        section_id=SECTION_ID,
        attempt_scope_ref=env["lineage"].attempt_scope_ref,
        attempt_id=env["lineage"].attempt_id,
    )
    assert current.committed_round_count == 1


def test_same_research_operation_with_changed_material_fails_closed(tmp_path) -> None:
    env = _setup(tmp_path)
    record = _round_record(env)
    operation = env["research"].round_operation_ref(record.round_ref)
    env["research"].commit_round(
        round_candidate=record,
        operation_ref=operation,
        source_plan_binding=env["plan_binding"],
        resulting_package_binding=env["package_binding"],
        commit_guard_context=env["lineage"],
        expected_progress_revision=0,
        expected_committed_round_count=0,
        expected_last_committed_round_ref=None,
    )
    changed = record.model_copy(
        update={"budget_receipt_refs": ("budget-receipt://different",)}
    )
    with pytest.raises(ResearchIdentityCollision, match="conflicts"):
        env["research"].commit_round(
            round_candidate=changed,
            operation_ref=operation,
            source_plan_binding=env["plan_binding"],
            resulting_package_binding=env["package_binding"],
            commit_guard_context=env["lineage"],
            expected_progress_revision=0,
            expected_committed_round_count=0,
            expected_last_committed_round_ref=None,
        )


def test_new_authorization_gets_new_round_identity_and_stale_progress_cannot_fork(
    tmp_path,
) -> None:
    env = _setup(tmp_path)
    first = _round_record(env)
    first_op = env["research"].round_operation_ref(first.round_ref)
    committed = env["research"].commit_round(
        round_candidate=first,
        operation_ref=first_op,
        source_plan_binding=env["plan_binding"],
        resulting_package_binding=env["package_binding"],
        commit_guard_context=env["lineage"],
        expected_progress_revision=0,
        expected_committed_round_count=0,
        expected_last_committed_round_ref=None,
    )

    auth2 = ResearchRoundAuthorizationBindingSchema(
        authorization_kind="WORKFLOW_RESEARCH",
        authorization_ref="workflow-research-more://2",
    )
    ref2 = env["research"].derive_round_ref(
        run_id=RUN_ID,
        section_id=SECTION_ID,
        authorization_binding=auth2,
        active_attempt=env["lineage"],
    )
    assert ref2 != first.round_ref
    second = ResearchRoundRecordSchema(
        round_ref=ref2,
        round_ordinal=2,
        authorization_binding=auth2,
        prior_committed_round_ref=first.round_ref,
        source_plan_version_ref=env["plan_version"],
        execution_outcome=ResearchRoundExecutionOutcome.COMPLETED,
        final_evidence_bundle_binding=env["bundle_binding"],
        package_progress=ResearchPackageProgressSchema(
            prior_evidence_package_version_ref=env["package_version"],
            resulting_evidence_package_version_ref=env["package_version"],
            material_package_change="NO_MATERIAL_PACKAGE_CHANGE",
        ),
    )
    with pytest.raises(ResearchProgressConflict, match="EXPECTED_PROGRESS_STALE"):
        env["research"].commit_round(
            round_candidate=second,
            operation_ref=env["research"].round_operation_ref(ref2),
            source_plan_binding=env["plan_binding"],
            resulting_package_binding=env["package_binding"],
            commit_guard_context=env["lineage"],
            expected_progress_revision=0,
            expected_committed_round_count=0,
            expected_last_committed_round_ref=None,
        )

    result2 = env["research"].commit_round(
        round_candidate=second,
        operation_ref=env["research"].round_operation_ref(ref2),
        source_plan_binding=env["plan_binding"],
        resulting_package_binding=env["package_binding"],
        commit_guard_context=env["lineage"],
        expected_progress_revision=committed.progress.progress_revision,
        expected_committed_round_count=committed.progress.committed_round_count,
        expected_last_committed_round_ref=first.round_ref,
    )
    assert result2.progress.committed_round_count == 2
    assert result2.record.package_progress.material_package_change == (
        "NO_MATERIAL_PACKAGE_CHANGE"
    )


def test_replay_uses_canonical_round_when_coordination_ack_crashed(tmp_path) -> None:
    coordination = _CrashAfterCanonicalAppendCoordinationStore()
    env = _setup(tmp_path, coordination_store=coordination)
    record = _round_record(env)
    operation = env["research"].round_operation_ref(record.round_ref)

    with pytest.raises(RuntimeError, match="simulated coordination ack crash"):
        env["research"].commit_round(
            round_candidate=record,
            operation_ref=operation,
            source_plan_binding=env["plan_binding"],
            resulting_package_binding=env["package_binding"],
            commit_guard_context=env["lineage"],
            expected_progress_revision=0,
            expected_committed_round_count=0,
            expected_last_committed_round_ref=None,
        )

    # The JSONL append is the canonical semantic commit point even though the
    # acknowledgement crashed. Replay must resolve that source, never rerun.
    assert env["research"].resolve_round(record.round_ref) == record
    replay = env["research"].commit_round(
        round_candidate=record,
        operation_ref=operation,
        source_plan_binding=env["plan_binding"],
        resulting_package_binding=env["package_binding"],
        commit_guard_context=env["lineage"],
        expected_progress_revision=0,
        expected_committed_round_count=0,
        expected_last_committed_round_ref=None,
    )
    assert replay.reused is True
    assert replay.progress.committed_round_count == 1
    assert replay.progress.progress_revision == 1


def test_terminal_technical_failure_commits_progress_without_fake_evidence(tmp_path) -> None:
    env = _setup(tmp_path)
    authorization = ResearchRoundAuthorizationBindingSchema(
        authorization_kind="WORKFLOW_RESEARCH",
        authorization_ref="workflow-start-research://technical-failure",
    )
    ref = env["research"].derive_round_ref(
        run_id=RUN_ID,
        section_id=SECTION_ID,
        authorization_binding=authorization,
        active_attempt=env["lineage"],
    )
    record = ResearchRoundRecordSchema(
        round_ref=ref,
        round_ordinal=1,
        authorization_binding=authorization,
        source_plan_version_ref=env["plan_version"],
        execution_outcome=ResearchRoundExecutionOutcome.TECHNICAL_FAILURE,
        package_progress=ResearchPackageProgressSchema(
            material_package_change="UNAVAILABLE_DUE_TO_TECHNICAL_FAILURE",
        ),
    )
    result = env["research"].commit_round(
        round_candidate=record,
        operation_ref=env["research"].round_operation_ref(ref),
        source_plan_binding=env["plan_binding"],
        resulting_package_binding=None,
        commit_guard_context=env["lineage"],
        expected_progress_revision=0,
        expected_committed_round_count=0,
        expected_last_committed_round_ref=None,
    )
    assert result.progress.committed_round_count == 1
    assert result.record.final_evidence_bundle_binding is None
    assert result.record.package_progress.resulting_evidence_package_version_ref is None


def test_plan_own_head_current_but_bound_fact_stale_blocks_package_and_round(tmp_path) -> None:
    env = _setup(tmp_path)
    source = FactSourceBindingSchema(
        source_type="project_input",
        source_ref="project://research",
        source_version_ref="project-version://2",
    )
    fact_content_v2 = env["fact"].build_content_from_authoritative_sources(
        normalization_contract_version="facts-v1",
        source_bindings=[source],
        facts=[
            CanonicalFactRecordSchema(
                fact_id="fact-1",
                fact_type="project_fact",
                normalized_value={"value": 2},
                source_bindings=[source],
            )
        ],
    )
    fact_logical = env["fact_binding"].version_ref.logical_ref
    fact_v2 = env["fact"].commit_snapshot(
        logical_ref=fact_logical,
        content=fact_content_v2,
        operation_ref=_op("fact-v2", "ARTIFACT_VERSION_COMMIT"),
    )
    env["fact"].promote_snapshot(
        logical_ref=fact_logical,
        target_version_ref=fact_v2,
        expected_head_revision=1,
        expected_active_version_id=env["fact_binding"].version_ref.artifact_version_id,
        operation_ref=_op("fact-v2-p", "ARTIFACT_ACTIVE_PROMOTION"),
    )

    # The Plan Artifact itself is still the active Plan head, but its material
    # Fact source is no longer current. Package SOURCE_FRESH must therefore fail.
    with pytest.raises(
        EvidencePackageCurrentnessError,
        match="FactSnapshot source is stale",
    ):
        env["research"].validate_evidence_package_binding_current(
            env["package_binding"],
            require_source_fresh=True,
        )

    authorization = ResearchRoundAuthorizationBindingSchema(
        authorization_kind="WORKFLOW_RESEARCH",
        authorization_ref="workflow-start-research://fact-stale-failure",
    )
    ref = env["research"].derive_round_ref(
        run_id=RUN_ID,
        section_id=SECTION_ID,
        authorization_binding=authorization,
        active_attempt=env["lineage"],
    )
    record = ResearchRoundRecordSchema(
        round_ref=ref,
        round_ordinal=1,
        authorization_binding=authorization,
        source_plan_version_ref=env["plan_version"],
        execution_outcome=ResearchRoundExecutionOutcome.TECHNICAL_FAILURE,
        package_progress=ResearchPackageProgressSchema(
            material_package_change="UNAVAILABLE_DUE_TO_TECHNICAL_FAILURE",
        ),
    )
    with pytest.raises(
        EvidencePackageSourceBindingError,
        match="FactSnapshot source is stale",
    ):
        env["research"].commit_round(
            round_candidate=record,
            operation_ref=env["research"].round_operation_ref(ref),
            source_plan_binding=env["plan_binding"],
            resulting_package_binding=None,
            commit_guard_context=env["lineage"],
            expected_progress_revision=0,
            expected_committed_round_count=0,
            expected_last_committed_round_ref=None,
        )


def test_stale_attempt_cannot_commit_research_round(tmp_path) -> None:
    env = _setup(tmp_path)
    record = _round_record(env)
    lifecycle = env["lifecycle"]
    old_lineage = env["lineage"]
    opened = lifecycle.open_attempt(
        RUN_ID,
        old_lineage.attempt_scope_ref,
        "input-snapshot-research-v2",
    )
    lifecycle.activate_attempt(opened.identity.attempt_id, opened.lifecycle_revision)

    with pytest.raises(Exception, match="commit-eligible|current"):
        env["research"].commit_round(
            round_candidate=record,
            operation_ref=env["research"].round_operation_ref(record.round_ref),
            source_plan_binding=env["plan_binding"],
            resulting_package_binding=env["package_binding"],
            commit_guard_context=old_lineage,
            expected_progress_revision=0,
            expected_committed_round_count=0,
            expected_last_committed_round_ref=None,
        )


def test_package_remains_self_current_but_source_stale_after_replan(tmp_path) -> None:
    env = _setup(tmp_path)
    v1 = env["plan_version"]
    content_v2 = SectionPlanContentSchema(
        planning_contract_version="section-plan-semantics-v2",
        fact_snapshot_version_ref=env["fact_binding"].version_ref,
        sections=(
            SectionPlanEntrySchema(
                section_id=SECTION_ID,
                section_title="Section 1 revised",
                section_order=0,
                applicability="applicable",
                strategy=SectionStrategyCode.RAG_GENERATE,
                citation_required=True,
                evidence_requirements_source=_requirements_ref(),
                evidence_requirement_ids=("req-1",),
            ),
        ),
    )
    env["plan"].commit_plan(
        candidate_content=content_v2,
        logical_ref=env["plan_logical"],
        fact_snapshot_binding=env["fact_binding"],
        expected_head_revision=1,
        expected_active_version_id=v1.artifact_version_id,
        operations=SectionPlanCommitOperations(
            version_commit=_op("plan-v2", "ARTIFACT_VERSION_COMMIT"),
            active_promotion=_op("plan-v2-p", "ARTIFACT_ACTIVE_PROMOTION"),
        ),
    )
    head = env["artifact"].resolve_active(env["package_logical"])
    assert head is not None
    assert head.active_version_ref == env["package_version"]
    with pytest.raises(EvidencePackageCurrentnessError, match="Plan.*stale|source is stale"):
        env["research"].validate_evidence_package_binding_current(
            env["package_binding"],
            require_source_fresh=True,
        )
