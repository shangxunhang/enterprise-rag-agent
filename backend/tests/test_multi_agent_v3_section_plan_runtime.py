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
from apps.enterprise_document.schemas.pre_write_admission import (
    EvidenceCriterionSchema,
    EvidenceRequirementSchema,
    EvidenceRequirementsSnapshotSchema,
)
from apps.enterprise_document.schemas.scheme_writer import (
    EvidenceRequirementsSourceRefSchema,
    PlannedFactRequirementSchema,
    PlanningConsistencyRefSchema,
    PlanningRiskHintSchema,
    SectionPlanContentSchema,
    SectionPlanEntrySchema,
    SectionStrategyCode,
)
from apps.enterprise_document.services.agent_rag_v1 import (
    ArtifactIdentityCollision,
    ArtifactRuntime,
    FactSnapshotRuntime,
    SectionPlanCommitOperations,
    SectionPlanCurrentnessError,
    SectionPlanRuntime,
    SectionPlanSourceBindingError,
)
from apps.enterprise_document.services.agent_rag_v1.lifecycle_runtime import (
    RunAttemptLifecycleService,
)
from core.runtime.side_effects import LogicalOperationRef


REQ_SHA = "a" * 64
REQUEST_SHA = hashlib.sha256(b"step5-plan-request").hexdigest()


class _Clock:
    def now_iso(self) -> str:
        return "2026-09-04T01:00:00+00:00"


class _Ids:
    def __init__(self) -> None:
        self.i = 0

    def new_id(self, prefix: str) -> str:
        self.i += 1
        return f"{prefix}_{self.i:03d}"


def _op(identity: str, kind: str) -> LogicalOperationRef:
    return LogicalOperationRef(
        logical_operation_id=identity,
        operation_kind=kind,
        run_id="run-plan-1",
        operation_scope_ref="document:doc-1",
    )


def _attempt_op(
    identity: str,
    kind: str,
    *,
    attempt_scope_ref: str,
    attempt_id: str,
) -> LogicalOperationRef:
    return LogicalOperationRef(
        logical_operation_id=identity,
        operation_kind=kind,
        run_id="run-plan-1",
        operation_scope_ref="document:doc-1",
        attempt_scope_ref=attempt_scope_ref,
        attempt_id=attempt_id,
    )


def _lifecycle(tmp_path) -> RunAttemptLifecycleService:
    service = RunAttemptLifecycleService(
        tmp_path / "lifecycle.jsonl",
        clock=_Clock(),
        id_generator=_Ids(),
    )
    created = service.claim_new_run(
        RunIdentitySchema(
            run_id="run-plan-1",
            task_id="task-plan-1",
            tenant_id="tenant",
            canonical_request_sha256=REQUEST_SHA,
        )
    )
    service.transition_run(
        "run-plan-1",
        created.lifecycle_revision,
        RunLifecycleStatus.RUNNING,
    )
    return service


def _requirements() -> EvidenceRequirementsSnapshotSchema:
    return EvidenceRequirementsSnapshotSchema(
        requirements_sha256=REQ_SHA,
        source_profile_ref="profile:plan:v1",
        source_task_ref="task-contract:plan:v1",
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


def _source_ref() -> EvidenceRequirementsSourceRefSchema:
    return EvidenceRequirementsSourceRefSchema(
        requirements_sha256=REQ_SHA,
        source_profile_ref="profile:plan:v1",
        source_task_ref="task-contract:plan:v1",
    )


def _runtimes(tmp_path):
    lifecycle = _lifecycle(tmp_path)
    artifact = ArtifactRuntime(
        tmp_path / "artifact-runtime.jsonl",
        lifecycle_service=lifecycle,
    )
    fact = FactSnapshotRuntime(
        artifact_runtime=artifact,
        source_binding_resolver=lambda binding: bool(binding.source_version_ref),
    )
    requirement_snapshot = _requirements()
    plan = SectionPlanRuntime(
        artifact_runtime=artifact,
        fact_snapshot_runtime=fact,
        requirements_resolver=lambda ref: requirement_snapshot,
    )
    return artifact, fact, plan


def _fact_binding(fact: FactSnapshotRuntime):
    source = FactSourceBindingSchema(
        source_type="project_input",
        source_ref="project://1",
        source_version_ref="project-version://1",
    )
    logical = fact.logical_ref_for_scope(
        run_id="run-plan-1",
        domain_scope_ref="project:doc-1",
    )
    content = fact.build_content_from_authoritative_sources(
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
    version = fact.commit_snapshot(
        logical_ref=logical,
        content=content,
        operation_ref=_op("fact-commit", "ARTIFACT_VERSION_COMMIT"),
    )
    return fact.promote_snapshot(
        logical_ref=logical,
        target_version_ref=version,
        expected_head_revision=0,
        expected_active_version_id=None,
        operation_ref=_op("fact-promote", "ARTIFACT_ACTIVE_PROMOTION"),
    )


def _entry(
    section_id: str = "s1",
    *,
    order: int = 0,
    deps: tuple[str, ...] = (),
    consistency=(),
    applicable: bool = True,
) -> SectionPlanEntrySchema:
    return SectionPlanEntrySchema(
        section_id=section_id,
        section_title=section_id,
        section_order=order,
        applicability="applicable" if applicable else "not_applicable",
        strategy=(
            SectionStrategyCode.RAG_GENERATE
            if applicable
            else SectionStrategyCode.SKIP
        ),
        citation_required=applicable,
        fact_requirements=(
            PlannedFactRequirementSchema(
                requirement_id=f"fact-req-{section_id}",
                fact_key="project.key",
                fact_type="project_fact",
                importance="required",
            ),
        ),
        evidence_requirements_source=_source_ref() if applicable else None,
        evidence_requirement_ids=("req-1",) if applicable else (),
        hard_dependencies=deps,
        consistency_refs=tuple(consistency),
        parallel_eligible=False,
    )


def _content(fact_ref: ArtifactVersionRefSchema, sections=None):
    return SectionPlanContentSchema(
        planning_contract_version="section-plan-semantics-v1",
        fact_snapshot_version_ref=fact_ref,
        sections=tuple(sections or [_entry()]),
    )


def test_section_plan_schema_rejects_unsatisfiable_dependency_and_cycle(tmp_path) -> None:
    _artifact, fact, _plan = _runtimes(tmp_path)
    binding = _fact_binding(fact)

    with pytest.raises(ValueError, match="not_applicable"):
        _content(
            binding.version_ref,
            [
                _entry("skip", order=0, applicable=False),
                _entry("downstream", order=1, deps=("skip",)),
            ],
        )

    with pytest.raises(ValueError, match="cycle"):
        _content(
            binding.version_ref,
            [
                _entry("a", order=0, deps=("b",)),
                _entry("b", order=1, deps=("a",)),
            ],
        )


def test_generated_section_consistency_ref_must_be_hard_dependency() -> None:
    with pytest.raises(ValueError, match="hard dependencies"):
        _entry(
            consistency=(
                PlanningConsistencyRefSchema(
                    ref_kind="generated_section",
                    ref_id="other",
                ),
            )
        )


def test_commit_and_current_binding_use_artifact_runtime(tmp_path) -> None:
    _artifact, fact, runtime = _runtimes(tmp_path)
    fact_binding = _fact_binding(fact)
    logical = runtime.logical_ref_for_scope(
        run_id="run-plan-1",
        document_scope_ref="document:doc-1",
    )

    version = runtime.commit_plan(
        candidate_content=_content(fact_binding.version_ref),
        logical_ref=logical,
        fact_snapshot_binding=fact_binding,
        expected_head_revision=0,
        expected_active_version_id=None,
        operations=SectionPlanCommitOperations(
            version_commit=_op("plan-commit-1", "ARTIFACT_VERSION_COMMIT"),
            active_promotion=_op("plan-promote-1", "ARTIFACT_ACTIVE_PROMOTION"),
        ),
    )
    binding = runtime.resolve_current_plan_binding(
        run_id="run-plan-1",
        document_scope_ref="document:doc-1",
        section_id="s1",
    )

    assert binding is not None
    assert binding.plan_version_ref == version
    assert binding.resolved_head_revision == 1
    assert runtime.validate_plan_binding_current(binding) == binding


def test_fact_consistency_ref_requires_exact_bound_fact_snapshot(tmp_path) -> None:
    _artifact, fact, runtime = _runtimes(tmp_path)
    fact_binding = _fact_binding(fact)
    content = _content(
        fact_binding.version_ref,
        [
            _entry(
                consistency=(
                    PlanningConsistencyRefSchema(
                        ref_kind="fact",
                        ref_id="missing-fact",
                    ),
                )
            )
        ],
    )
    with pytest.raises(SectionPlanSourceBindingError, match="absent"):
        runtime.commit_plan(
            candidate_content=content,
            logical_ref=runtime.logical_ref_for_scope(
                run_id="run-plan-1",
                document_scope_ref="document:doc-1",
            ),
            fact_snapshot_binding=fact_binding,
            expected_head_revision=0,
            expected_active_version_id=None,
            operations=SectionPlanCommitOperations(
                version_commit=_op("plan-bad-fact", "ARTIFACT_VERSION_COMMIT"),
                active_promotion=_op("plan-bad-fact-p", "ARTIFACT_ACTIVE_PROMOTION"),
            ),
        )


def test_requirement_ids_resolve_against_exact_snapshot(tmp_path) -> None:
    artifact, fact, _runtime = _runtimes(tmp_path)
    fact_binding = _fact_binding(fact)
    bad_runtime = SectionPlanRuntime(
        artifact_runtime=artifact,
        fact_snapshot_runtime=fact,
        requirements_resolver=lambda ref: _requirements().model_copy(
            update={
                "requirements": (
                    EvidenceRequirementSchema(
                        requirement_id="different",
                        criteria=(
                            EvidenceCriterionSchema(
                                criterion_id="c2",
                                criterion="different",
                            ),
                        ),
                    ),
                )
            }
        ),
    )
    with pytest.raises(SectionPlanSourceBindingError, match="absent"):
        bad_runtime.commit_plan(
            candidate_content=_content(fact_binding.version_ref),
            logical_ref=bad_runtime.logical_ref_for_scope(
                run_id="run-plan-1",
                document_scope_ref="document:doc-1",
            ),
            fact_snapshot_binding=fact_binding,
            expected_head_revision=0,
            expected_active_version_id=None,
            operations=SectionPlanCommitOperations(
                version_commit=_op("plan-bad-req", "ARTIFACT_VERSION_COMMIT"),
                active_promotion=_op("plan-bad-req-p", "ARTIFACT_ACTIVE_PROMOTION"),
            ),
        )


def test_cross_run_fact_snapshot_binding_is_rejected(tmp_path) -> None:
    _artifact, fact, runtime = _runtimes(tmp_path)
    fact_binding = _fact_binding(fact)
    foreign_logical = fact_binding.version_ref.logical_ref.model_copy(
        update={"run_id": "run-foreign"}
    )
    foreign_version = fact_binding.version_ref.model_copy(
        update={"logical_ref": foreign_logical}
    )
    foreign_binding = fact_binding.model_copy(
        update={"version_ref": foreign_version}
    )

    with pytest.raises(SectionPlanSourceBindingError, match="same Run"):
        runtime.commit_plan(
            candidate_content=_content(foreign_version),
            logical_ref=runtime.logical_ref_for_scope(
                run_id="run-plan-1",
                document_scope_ref="document:doc-1",
            ),
            fact_snapshot_binding=foreign_binding,
            expected_head_revision=0,
            expected_active_version_id=None,
            operations=SectionPlanCommitOperations(
                version_commit=_op("plan-cross-run", "ARTIFACT_VERSION_COMMIT"),
                active_promotion=_op(
                    "plan-cross-run-p",
                    "ARTIFACT_ACTIVE_PROMOTION",
                ),
            ),
        )


def test_fact_head_staleness_blocks_new_plan_commit(tmp_path) -> None:
    _artifact, fact, runtime = _runtimes(tmp_path)
    first_binding = _fact_binding(fact)

    source2 = FactSourceBindingSchema(
        source_type="project_input",
        source_ref="project://1",
        source_version_ref="project-version://2",
    )
    content2 = fact.build_content_from_authoritative_sources(
        normalization_contract_version="facts-v1",
        source_bindings=[source2],
        facts=[
            CanonicalFactRecordSchema(
                fact_id="fact-1",
                fact_type="project_fact",
                normalized_value={"value": 2},
                source_bindings=[source2],
            )
        ],
    )
    v2 = fact.commit_snapshot(
        logical_ref=first_binding.version_ref.logical_ref,
        content=content2,
        operation_ref=_op("fact-commit-2", "ARTIFACT_VERSION_COMMIT"),
    )
    fact.promote_snapshot(
        logical_ref=first_binding.version_ref.logical_ref,
        target_version_ref=v2,
        expected_head_revision=1,
        expected_active_version_id=first_binding.version_ref.artifact_version_id,
        operation_ref=_op("fact-promote-2", "ARTIFACT_ACTIVE_PROMOTION"),
    )

    with pytest.raises(Exception, match="stale"):
        runtime.commit_plan(
            candidate_content=_content(first_binding.version_ref),
            logical_ref=runtime.logical_ref_for_scope(
                run_id="run-plan-1",
                document_scope_ref="document:doc-1",
            ),
            fact_snapshot_binding=first_binding,
            expected_head_revision=0,
            expected_active_version_id=None,
            operations=SectionPlanCommitOperations(
                version_commit=_op("plan-stale-fact", "ARTIFACT_VERSION_COMMIT"),
                active_promotion=_op("plan-stale-fact-p", "ARTIFACT_ACTIVE_PROMOTION"),
            ),
        )


def test_old_section_binding_becomes_stale_after_replan(tmp_path) -> None:
    _artifact, fact, runtime = _runtimes(tmp_path)
    fact_binding = _fact_binding(fact)
    logical = runtime.logical_ref_for_scope(
        run_id="run-plan-1",
        document_scope_ref="document:doc-1",
    )
    v1 = runtime.commit_plan(
        candidate_content=_content(fact_binding.version_ref),
        logical_ref=logical,
        fact_snapshot_binding=fact_binding,
        expected_head_revision=0,
        expected_active_version_id=None,
        operations=SectionPlanCommitOperations(
            version_commit=_op("plan-v1", "ARTIFACT_VERSION_COMMIT"),
            active_promotion=_op("plan-v1-p", "ARTIFACT_ACTIVE_PROMOTION"),
        ),
    )
    b1 = runtime.resolve_current_plan_binding(
        run_id="run-plan-1",
        document_scope_ref="document:doc-1",
        section_id="s1",
    )
    assert b1 is not None

    runtime.commit_plan(
        candidate_content=_content(
            fact_binding.version_ref,
            [_entry("s1", order=0), _entry("s2", order=1)],
        ),
        logical_ref=logical,
        fact_snapshot_binding=fact_binding,
        expected_head_revision=1,
        expected_active_version_id=v1.artifact_version_id,
        operations=SectionPlanCommitOperations(
            version_commit=_op("plan-v2", "ARTIFACT_VERSION_COMMIT"),
            active_promotion=_op("plan-v2-p", "ARTIFACT_ACTIVE_PROMOTION"),
        ),
    )
    with pytest.raises(SectionPlanCurrentnessError, match="stale"):
        runtime.validate_plan_binding_current(b1)


def test_unordered_plan_entry_semantics_are_canonicalized_deterministically() -> None:
    first = SectionPlanEntrySchema(
        section_id="s",
        section_title="S",
        section_order=2,
        applicability="applicable",
        strategy=SectionStrategyCode.TECHNICAL_GENERATE,
        fact_requirements=(
            PlannedFactRequirementSchema(
                requirement_id="fact-b",
                fact_key="key-b",
                fact_type="project_fact",
                importance="optional",
            ),
            PlannedFactRequirementSchema(
                requirement_id="fact-a",
                fact_key="key-a",
                fact_type="project_fact",
                importance="required",
            ),
        ),
        hard_dependencies=("b", "a"),
        consistency_refs=(
            PlanningConsistencyRefSchema(
                ref_kind="generated_section",
                ref_id="b",
            ),
            PlanningConsistencyRefSchema(
                ref_kind="fact",
                ref_id="fact-z",
            ),
            PlanningConsistencyRefSchema(
                ref_kind="generated_section",
                ref_id="a",
            ),
            PlanningConsistencyRefSchema(
                ref_kind="fact",
                ref_id="fact-a",
            ),
        ),
        risk_hints=(
            PlanningRiskHintSchema(risk_code="risk-z", summary="z"),
            PlanningRiskHintSchema(risk_code="risk-a", summary="a"),
        ),
    )
    second = SectionPlanEntrySchema(
        section_id="s",
        section_title="S",
        section_order=2,
        applicability="applicable",
        strategy=SectionStrategyCode.TECHNICAL_GENERATE,
        fact_requirements=tuple(reversed(first.fact_requirements)),
        hard_dependencies=tuple(reversed(first.hard_dependencies)),
        consistency_refs=tuple(reversed(first.consistency_refs)),
        risk_hints=tuple(reversed(first.risk_hints)),
    )

    assert first == second
    assert first.model_dump(mode="json") == second.model_dump(mode="json")


def test_plan_commit_replay_converges_and_changed_material_fails_closed(tmp_path) -> None:
    _artifact, fact, runtime = _runtimes(tmp_path)
    fact_binding = _fact_binding(fact)
    logical = runtime.logical_ref_for_scope(
        run_id="run-plan-1",
        document_scope_ref="document:doc-1",
    )
    operations = SectionPlanCommitOperations(
        version_commit=_op("plan-replay", "ARTIFACT_VERSION_COMMIT"),
        active_promotion=_op("plan-replay-p", "ARTIFACT_ACTIVE_PROMOTION"),
    )
    content = _content(fact_binding.version_ref)

    first = runtime.commit_plan(
        candidate_content=content,
        logical_ref=logical,
        fact_snapshot_binding=fact_binding,
        expected_head_revision=0,
        expected_active_version_id=None,
        operations=operations,
    )
    replay = runtime.commit_plan(
        candidate_content=content,
        logical_ref=logical,
        fact_snapshot_binding=fact_binding,
        expected_head_revision=0,
        expected_active_version_id=None,
        operations=operations,
    )
    assert replay == first

    with pytest.raises(ArtifactIdentityCollision, match="conflicts"):
        runtime.commit_plan(
            candidate_content=_content(
                fact_binding.version_ref,
                [_entry("s1", order=0), _entry("s2", order=1)],
            ),
            logical_ref=logical,
            fact_snapshot_binding=fact_binding,
            expected_head_revision=1,
            expected_active_version_id=first.artifact_version_id,
            operations=operations,
        )


def test_attempt_scoped_plan_commit_forwards_commit_guard_context(tmp_path) -> None:
    artifact, fact, runtime = _runtimes(tmp_path)
    fact_binding = _fact_binding(fact)
    lifecycle = artifact.lifecycle_service
    scope = "document:doc-1"
    opened = lifecycle.open_attempt(
        "run-plan-1",
        scope,
        "input-snapshot-plan-v1",
    )
    running = lifecycle.activate_attempt(
        opened.identity.attempt_id,
        opened.lifecycle_revision,
    )
    lineage = lifecycle.resolve_active_lineage("run-plan-1", scope)
    logical = runtime.logical_ref_for_scope(
        run_id="run-plan-1",
        document_scope_ref=scope,
    )
    version = runtime.commit_plan(
        candidate_content=_content(fact_binding.version_ref),
        logical_ref=logical,
        fact_snapshot_binding=fact_binding,
        expected_head_revision=0,
        expected_active_version_id=None,
        operations=SectionPlanCommitOperations(
            version_commit=_attempt_op(
                "plan-attempt",
                "ARTIFACT_VERSION_COMMIT",
                attempt_scope_ref=scope,
                attempt_id=running.identity.attempt_id,
            ),
            active_promotion=_attempt_op(
                "plan-attempt-p",
                "ARTIFACT_ACTIVE_PROMOTION",
                attempt_scope_ref=scope,
                attempt_id=running.identity.attempt_id,
            ),
        ),
        commit_guard_context=lineage,
    )

    assert version.producer_attempt_scope_ref == scope
    assert version.producer_attempt_id == running.identity.attempt_id


def test_display_revision_and_execution_identity_are_not_material_plan_content(tmp_path) -> None:
    _artifact, fact, runtime = _runtimes(tmp_path)
    fact_binding = _fact_binding(fact)
    content = _content(fact_binding.version_ref)
    dumped = content.model_dump(mode="json")
    forbidden = {
        "plan_revision",
        "active_plan_version",
        "created_at",
        "attempt_id",
        "invocation_id",
        "execution_id",
        "trace_ref",
        "requires_global_context",
    }
    assert forbidden.isdisjoint(dumped)
    logical = runtime.logical_ref_for_scope(
        run_id="run-plan-1",
        document_scope_ref="document:doc-1",
    )
    assert runtime.semantic_fingerprint(logical, content)
