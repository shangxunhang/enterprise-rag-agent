"""Canonical SIF-05 EvidencePackage + SIF-08 Research runtime.

The runtime deliberately composes existing authorities instead of replacing them:

- RAG remains canonical EvidenceBundle owner; an injected opaque-handle resolver
  proves the exact immutable Bundle.
- ArtifactRuntime owns EvidencePackage immutable versions and active head.
- RunAttemptLifecycleService owns active Attempt commit eligibility.
- SIF-02 owns logical-operation coordination.
- this module owns only SIF-08 immutable ResearchRound history and its atomic
  ResearchProgressState successor cursor.

State/checkpoint/Trace are projections and are never recovery truth here.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
import json
from pathlib import Path
import threading
from typing import Any, Callable

from apps.enterprise_document.schemas.agent_rag_v1.artifacts import (
    ArtifactLogicalRefSchema,
    ArtifactVersionRefSchema,
)
from apps.enterprise_document.schemas.agent_rag_v1.facts import (
    FactSnapshotBindingSchema,
)
from apps.enterprise_document.schemas.agent_rag_v1.lifecycle import (
    ActiveExecutionLineageSchema,
)
from apps.enterprise_document.schemas.agent_rag_v1.research import (
    CanonicalEvidenceBundleBindingSchema,
    EvidencePackageBindingSchema,
    EvidencePackageContentSchema,
    ResearchProgressStateSchema,
    ResearchRoundAuthorizationBindingSchema,
    ResearchRoundBindingSchema,
    ResearchRoundExecutionOutcome,
    ResearchRoundRecordSchema,
    ResearchRoundRefSchema,
)
from apps.enterprise_document.schemas.pre_write_admission import (
    EvidenceRequirementsSnapshotSchema,
)
from apps.enterprise_document.schemas.scheme_writer.planning import (
    EvidenceRequirementsSourceRefSchema,
    SectionPlanBindingSchema,
    SectionPlanContentSchema,
)
from apps.enterprise_document.services.agent_rag_v1.artifact_runtime import (
    ArtifactRuntime,
)
from apps.enterprise_document.services.agent_rag_v1.fact_snapshot_runtime import (
    FactSnapshotRuntime,
)
from apps.enterprise_document.services.agent_rag_v1.lifecycle_runtime import (
    RunAttemptLifecycleService,
)
from apps.enterprise_document.services.agent_rag_v1.plan_runtime import (
    SectionPlanRuntime,
)
from contracts.evidence import evidence_bundle_sha256
from core.hashing import canonical_sha256
from core.persistence.crash_safe_jsonl import CrashSafeJsonlEventLog
from core.persistence.interprocess_lock import InterProcessFileLock
from core.runtime.side_effects import (
    CommittedResultResolution,
    CommittedResultResolutionStatus,
    JsonlSideEffectCommitStore,
    LogicalOperationRef,
    SideEffectClaimStatus,
    SideEffectCommitStore,
    SideEffectCoordinationStatus,
    SideEffectIdentityConflict,
    validate_sif02_semantic_fingerprint,
)
from schemas.rag import EvidenceBundleSchema


EVIDENCE_PACKAGE_ARTIFACT_TYPE = "evidence_package"
EVIDENCE_PACKAGE_CONTENT_SCHEMA_VERSION = "evidence_package_content_v1"
RESEARCH_ROUND_COMMIT_OPERATION_KIND = "RESEARCH_ROUND_COMMIT"
_RESEARCH_EVENT_SCHEMA_VERSION = "research_runtime_event_v1"


class ResearchRuntimeError(ValueError):
    """Base error for canonical Research runtime failures."""


class EvidencePackageSourceBindingError(ResearchRuntimeError):
    """An exact Package material source is missing, stale, or contradictory."""


class EvidencePackageCurrentnessError(ResearchRuntimeError):
    """An EvidencePackage point-in-time binding is stale or invalid."""


class ResearchIdentityCollision(ResearchRuntimeError):
    """A replay-stable Research identity was reused with different semantics."""


class ResearchProgressConflict(ResearchRuntimeError):
    """Expected Research progress CAS no longer matches canonical state."""


class ResearchRuntimeIntegrityError(ResearchRuntimeError):
    """Durable Research history is missing, corrupt, or contradictory."""


class ResearchRoundNotFound(ResearchRuntimeError):
    """The exact immutable ResearchRoundRecord is absent."""


class ResearchOperationInProgress(ResearchRuntimeError):
    """Another claimant is coordinating the same ResearchRound commit."""


EvidenceBundleResolver = Callable[[str], EvidenceBundleSchema]
ResearchAuthorizationValidator = Callable[..., None]


@dataclass(frozen=True)
class EvidencePackageCommitOperations:
    version_commit: LogicalOperationRef
    active_promotion: LogicalOperationRef


@dataclass(frozen=True)
class ResearchRoundCommitResult:
    record: ResearchRoundRecordSchema
    progress: ResearchProgressStateSchema
    binding: ResearchRoundBindingSchema
    reused: bool


@dataclass(frozen=True)
class _StoredRound:
    record: ResearchRoundRecordSchema
    operation_ref: LogicalOperationRef
    semantic_fingerprint: str
    result_ref: str
    progress: ResearchProgressStateSchema


@dataclass
class _ResearchLedgerState:
    rounds: dict[tuple[str, str, str, str, str], _StoredRound]
    operations: dict[str, _StoredRound]
    progress: dict[tuple[str, str, str, str], ResearchProgressStateSchema]


def _round_key(
    ref: ResearchRoundRefSchema,
) -> tuple[str, str, str, str, str]:
    return (
        ref.run_id,
        ref.section_id,
        ref.attempt_scope_ref,
        ref.attempt_id,
        ref.research_round_id,
    )


def _progress_key(
    *,
    run_id: str,
    section_id: str,
    attempt_scope_ref: str,
    attempt_id: str,
) -> tuple[str, str, str, str]:
    return (run_id, section_id, attempt_scope_ref, attempt_id)


def _operation_payload(operation_ref: LogicalOperationRef) -> dict[str, Any]:
    return asdict(operation_ref)


def _operation_from_payload(value: Any) -> LogicalOperationRef:
    if not isinstance(value, dict):
        raise ResearchRuntimeIntegrityError("Research operation_ref must be an object")
    try:
        return LogicalOperationRef(**value)
    except Exception as exc:
        raise ResearchRuntimeIntegrityError("Research operation_ref is invalid") from exc


def _sha256_binding(bundle: EvidenceBundleSchema) -> str:
    return "sha256:" + evidence_bundle_sha256(bundle)


def _round_scope_ref(ref: ResearchRoundRefSchema) -> str:
    digest = canonical_sha256(ref.model_dump(mode="json", round_trip=True))
    return f"research-round-ref://sha256/{digest}"


def _round_result_ref(
    ref: ResearchRoundRefSchema,
    progress_revision: int,
) -> str:
    digest = canonical_sha256(
        {
            "round_ref": ref.model_dump(mode="json", round_trip=True),
            "progress_revision": int(progress_revision),
        }
    )
    return f"research-round-result://sha256/{digest}"


class ResearchRuntime:
    """Canonical owner for SIF-08 Research Round/Progress semantics."""

    _LOCKS_GUARD = threading.Lock()
    _PATH_LOCKS: dict[str, threading.RLock] = {}

    def __init__(
        self,
        *,
        artifact_runtime: ArtifactRuntime,
        section_plan_runtime: SectionPlanRuntime,
        fact_snapshot_runtime: FactSnapshotRuntime,
        evidence_bundle_resolver: EvidenceBundleResolver,
        authorization_validator: ResearchAuthorizationValidator,
        lifecycle_service: RunAttemptLifecycleService | None = None,
        ledger_path: str | Path = "data/runtime/agent_rag_v1/research_runtime.jsonl",
        coordination_store: SideEffectCommitStore | None = None,
    ) -> None:
        if not callable(evidence_bundle_resolver):
            raise TypeError("ResearchRuntime requires evidence_bundle_resolver")
        if not callable(authorization_validator):
            raise TypeError("ResearchRuntime requires authorization_validator")
        self.artifact_runtime = artifact_runtime
        self.section_plan_runtime = section_plan_runtime
        self.fact_snapshot_runtime = fact_snapshot_runtime
        self.evidence_bundle_resolver = evidence_bundle_resolver
        self.authorization_validator = authorization_validator
        self.lifecycle_service = (
            lifecycle_service
            or getattr(artifact_runtime, "lifecycle_service", None)
            or RunAttemptLifecycleService()
        )
        self.ledger_path = Path(ledger_path)
        self.log = CrashSafeJsonlEventLog(self.ledger_path)
        self.lock_path = self.ledger_path.with_suffix(self.ledger_path.suffix + ".lock")
        self.coordination_store = coordination_store or JsonlSideEffectCommitStore(
            self.ledger_path.with_suffix(
                self.ledger_path.suffix + ".coordination"
            )
        )
        lock_key = str(self.ledger_path.resolve())
        with self._LOCKS_GUARD:
            self._lock = self._PATH_LOCKS.setdefault(lock_key, threading.RLock())

    # ------------------------------------------------------------------
    # SIF-05 EvidencePackage
    # ------------------------------------------------------------------

    @staticmethod
    def evidence_package_logical_ref(
        *,
        run_id: str,
        section_id: str,
        logical_artifact_id: str | None = None,
    ) -> ArtifactLogicalRefSchema:
        run = str(run_id or "").strip()
        section = str(section_id or "").strip()
        if not run or not section:
            raise ResearchRuntimeError("run_id and section_id are required")
        scope = f"section:{section}"
        logical_id = str(logical_artifact_id or "").strip()
        if not logical_id:
            logical_id = "evidence-package:" + canonical_sha256(
                {
                    "run_id": run,
                    "section_id": section,
                    "artifact_type": EVIDENCE_PACKAGE_ARTIFACT_TYPE,
                }
            )
        return ArtifactLogicalRefSchema(
            run_id=run,
            logical_artifact_id=logical_id,
            artifact_type=EVIDENCE_PACKAGE_ARTIFACT_TYPE,
            domain_scope_ref=scope,
        )

    @staticmethod
    def _validate_package_logical_ref(
        logical_ref: ArtifactLogicalRefSchema,
        section_id: str,
    ) -> None:
        if logical_ref.artifact_type != EVIDENCE_PACKAGE_ARTIFACT_TYPE:
            raise ResearchRuntimeError(
                "EvidencePackage logical_ref requires artifact_type=evidence_package"
            )
        expected_scope = f"section:{str(section_id or '').strip()}"
        if logical_ref.domain_scope_ref != expected_scope:
            raise ResearchRuntimeError(
                "EvidencePackage logical_ref must identify the canonical Section scope"
            )

    def resolve_evidence_bundle(
        self,
        binding: CanonicalEvidenceBundleBindingSchema,
    ) -> EvidenceBundleSchema:
        try:
            bundle = self.evidence_bundle_resolver(binding.evidence_bundle_handle)
        except Exception as exc:
            raise EvidencePackageSourceBindingError(
                "canonical EvidenceBundle handle resolution failed"
            ) from exc
        bundle = EvidenceBundleSchema.model_validate(bundle)
        if _sha256_binding(bundle) != binding.evidence_bundle_sha256:
            raise EvidencePackageSourceBindingError(
                "EvidenceBundle handle/hash binding does not resolve exact canonical content"
            )
        return bundle

    def _resolve_fresh_plan_section(
        self,
        plan_binding: SectionPlanBindingSchema,
    ) -> tuple[SectionPlanContentSchema, Any]:
        self.section_plan_runtime.validate_plan_binding_current(plan_binding)
        raw_plan, canonical_ref = self.artifact_runtime.read_version(
            plan_binding.plan_version_ref
        )
        if canonical_ref != plan_binding.plan_version_ref:
            raise EvidencePackageSourceBindingError(
                "Plan resolver returned a different immutable version"
            )
        plan = SectionPlanContentSchema.model_validate(raw_plan)
        fact_head = self.artifact_runtime.resolve_active(
            plan.fact_snapshot_version_ref.logical_ref
        )
        if (
            fact_head is None
            or fact_head.active_version_ref != plan.fact_snapshot_version_ref
        ):
            raise EvidencePackageSourceBindingError(
                "bound SectionPlan FactSnapshot source is stale"
            )
        matches = [
            item
            for item in plan.sections
            if item.section_id == plan_binding.section_id
        ]
        if len(matches) != 1:
            raise EvidencePackageSourceBindingError(
                "Package section does not resolve exactly once in bound Plan"
            )
        section = matches[0]

        source = section.evidence_requirements_source
        if source is not None:
            try:
                snapshot = self.section_plan_runtime.requirements_resolver(source)
            except Exception as exc:
                raise EvidencePackageSourceBindingError(
                    "Package EvidenceRequirements source resolution failed"
                ) from exc
            if (
                snapshot.schema_version != source.requirements_schema_version
                or snapshot.contract_version != source.requirements_contract_version
                or snapshot.requirements_sha256 != source.requirements_sha256
                or snapshot.source_profile_ref != source.source_profile_ref
                or snapshot.source_task_ref != source.source_task_ref
            ):
                raise EvidencePackageSourceBindingError(
                    "Package Plan Requirement source no longer resolves exact canonical snapshot"
                )
        return plan, section

    def resolve_research_requirements(
        self,
        plan_binding: SectionPlanBindingSchema,
    ) -> tuple[EvidenceRequirementsSnapshotSchema | None, tuple[str, ...]]:
        """Resolve the exact Plan-selected Evidence Requirement source for Research."""

        _plan, section = self._resolve_fresh_plan_section(plan_binding)
        source = section.evidence_requirements_source
        selected_ids = tuple(section.evidence_requirement_ids)
        if source is None:
            if selected_ids:
                raise EvidencePackageSourceBindingError(
                    "Plan has Evidence requirement ids without source"
                )
            return None, ()
        try:
            snapshot = self.section_plan_runtime.requirements_resolver(source)
        except Exception as exc:
            raise EvidencePackageSourceBindingError(
                "Research EvidenceRequirements source resolution failed"
            ) from exc
        available = {item.requirement_id for item in snapshot.requirements}
        unknown = set(selected_ids) - available
        if unknown:
            raise EvidencePackageSourceBindingError(
                "Plan-selected Evidence requirement ids are absent from exact snapshot: "
                + ", ".join(sorted(unknown))
            )
        return snapshot, selected_ids

    def _read_plan_for_package(
        self,
        *,
        plan_binding: SectionPlanBindingSchema,
        content: EvidencePackageContentSchema,
    ) -> tuple[SectionPlanContentSchema, Any]:
        if (
            content.section_plan_source.plan_version_ref
            != plan_binding.plan_version_ref
            or content.section_plan_source.section_id != plan_binding.section_id
        ):
            raise EvidencePackageSourceBindingError(
                "Package immutable Plan source does not match current Plan commit guard"
            )
        return self._resolve_fresh_plan_section(plan_binding)

    def _validate_package_sources(
        self,
        *,
        logical_ref: ArtifactLogicalRefSchema,
        content: EvidencePackageContentSchema,
        plan_binding: SectionPlanBindingSchema,
        direct_fact_snapshot_binding: FactSnapshotBindingSchema | None,
    ) -> EvidenceBundleSchema:
        self._validate_package_logical_ref(
            logical_ref,
            content.section_plan_source.section_id,
        )
        if content.section_plan_source.plan_version_ref.logical_ref.run_id != logical_ref.run_id:
            raise EvidencePackageSourceBindingError(
                "EvidencePackage Plan source must belong to the same Run"
            )
        plan, section = self._read_plan_for_package(
            plan_binding=plan_binding,
            content=content,
        )

        direct_fact = content.direct_fact_snapshot_version_ref
        if direct_fact is None:
            if direct_fact_snapshot_binding is not None:
                raise EvidencePackageSourceBindingError(
                    "non-material direct FactSnapshot guard must not be supplied"
                )
        else:
            if direct_fact_snapshot_binding is None:
                raise EvidencePackageSourceBindingError(
                    "direct FactSnapshot source requires exact current binding guard"
                )
            if direct_fact_snapshot_binding.version_ref != direct_fact:
                raise EvidencePackageSourceBindingError(
                    "direct FactSnapshot content/ref guard mismatch"
                )
            if direct_fact != plan.fact_snapshot_version_ref:
                raise EvidencePackageSourceBindingError(
                    "direct FactSnapshot must equal the exact Fact version bound by Plan"
                )
            self.fact_snapshot_runtime.validate_binding_current(
                direct_fact_snapshot_binding
            )

        bundle = self.resolve_evidence_bundle(content.evidence_bundle_binding)
        if bundle.run_id is not None and bundle.run_id != logical_ref.run_id:
            raise EvidencePackageSourceBindingError(
                "EvidenceBundle Run identity conflicts with Package Run"
            )
        selected = set(bundle.selected_evidence_ids)
        dropped = set(bundle.dropped_evidence_ids)
        allowed_requirements = set(section.evidence_requirement_ids)
        seen: set[str] = set()
        for selection in content.selections:
            if selection.evidence_id in seen:
                raise EvidencePackageSourceBindingError(
                    "EvidencePackage cannot select one Evidence ID twice"
                )
            seen.add(selection.evidence_id)
            if selection.evidence_id not in selected:
                raise EvidencePackageSourceBindingError(
                    "Package selection is absent from exact Bundle.selected_evidence_ids"
                )
            if selection.evidence_id in dropped:
                raise EvidencePackageSourceBindingError(
                    "Package cannot select exact Bundle dropped Evidence"
                )
            if not set(selection.requirement_ids) <= allowed_requirements:
                raise EvidencePackageSourceBindingError(
                    "Package requirement mapping exceeds bound Plan selection"
                )
            if (
                section.evidence_requirements_source is None
                and selection.requirement_ids
            ):
                raise EvidencePackageSourceBindingError(
                    "Package cannot invent Requirement IDs absent from Plan"
                )
        return bundle

    @staticmethod
    def evidence_package_semantic_fingerprint(
        logical_ref: ArtifactLogicalRefSchema,
        content: EvidencePackageContentSchema,
    ) -> str:
        if logical_ref.artifact_type != EVIDENCE_PACKAGE_ARTIFACT_TYPE:
            raise ResearchRuntimeError(
                "Package fingerprint requires artifact_type=evidence_package"
            )
        return canonical_sha256(
            {
                "logical_ref": logical_ref.model_dump(mode="json"),
                "content": content.model_dump(mode="json"),
            }
        )

    def commit_evidence_package(
        self,
        *,
        candidate_content: EvidencePackageContentSchema,
        logical_ref: ArtifactLogicalRefSchema,
        plan_binding: SectionPlanBindingSchema,
        expected_head_revision: int,
        expected_active_version_id: str | None,
        operations: EvidencePackageCommitOperations,
        commit_guard_context: ActiveExecutionLineageSchema,
        direct_fact_snapshot_binding: FactSnapshotBindingSchema | None = None,
    ) -> ArtifactVersionRefSchema:
        content = EvidencePackageContentSchema.model_validate(
            candidate_content.model_dump(mode="python", round_trip=True)
        )
        self._validate_package_sources(
            logical_ref=logical_ref,
            content=content,
            plan_binding=plan_binding,
            direct_fact_snapshot_binding=direct_fact_snapshot_binding,
        )
        fingerprint = self.evidence_package_semantic_fingerprint(
            logical_ref,
            content,
        )

        # SIF-05 explicitly permits a new Research Round to reuse the already
        # current immutable Package when material semantics are unchanged.
        head = self.artifact_runtime.resolve_active(logical_ref)
        if head is not None and head.active_version_ref is not None:
            raw_current, current_ref = self.artifact_runtime.read_version(
                head.active_version_ref
            )
            current_content = EvidencePackageContentSchema.model_validate(raw_current)
            if (
                current_ref.semantic_fingerprint == fingerprint
                and current_content == content
            ):
                return current_ref

        version_ref = self.artifact_runtime.commit_version(
            logical_ref,
            content,
            EVIDENCE_PACKAGE_CONTENT_SCHEMA_VERSION,
            operations.version_commit,
            fingerprint,
            commit_guard_context,
        )
        promotion_fingerprint = canonical_sha256(
            {
                "logical_ref": logical_ref.model_dump(mode="json"),
                "target_version_ref": version_ref.model_dump(mode="json"),
                "expected_head_revision": int(expected_head_revision),
                "expected_active_version_id": expected_active_version_id,
            }
        )
        self.artifact_runtime.promote_active(
            logical_ref,
            version_ref,
            expected_head_revision,
            expected_active_version_id,
            operations.active_promotion,
            promotion_fingerprint,
            commit_guard_context,
        )
        return version_ref

    def resolve_current_evidence_package_binding(
        self,
        *,
        run_id: str,
        section_id: str,
        logical_artifact_id: str | None = None,
    ) -> EvidencePackageBindingSchema | None:
        logical_ref = self.evidence_package_logical_ref(
            run_id=run_id,
            section_id=section_id,
            logical_artifact_id=logical_artifact_id,
        )
        head = self.artifact_runtime.resolve_active(logical_ref)
        if head is None or head.active_version_ref is None:
            return None
        raw, canonical_ref = self.artifact_runtime.read_version(
            head.active_version_ref
        )
        content = EvidencePackageContentSchema.model_validate(raw)
        if content.section_plan_source.section_id != section_id:
            raise ResearchRuntimeIntegrityError(
                "EvidencePackage active head resolves a different Section"
            )
        return EvidencePackageBindingSchema(
            version_ref=canonical_ref,
            resolved_head_revision=head.head_revision,
        )

    def validate_evidence_package_binding_current(
        self,
        binding: EvidencePackageBindingSchema,
        *,
        require_source_fresh: bool = True,
    ) -> EvidencePackageContentSchema:
        logical_ref = binding.version_ref.logical_ref
        if logical_ref.artifact_type != EVIDENCE_PACKAGE_ARTIFACT_TYPE:
            raise EvidencePackageCurrentnessError(
                "EvidencePackage binding has wrong Artifact type"
            )
        head = self.artifact_runtime.resolve_active(logical_ref)
        if head is None or head.active_version_ref is None:
            raise EvidencePackageCurrentnessError(
                "EvidencePackage has no active canonical head"
            )
        if (
            head.head_revision != binding.resolved_head_revision
            or head.active_version_ref != binding.version_ref
        ):
            raise EvidencePackageCurrentnessError("EvidencePackage binding is stale")
        raw, canonical_ref = self.artifact_runtime.read_version(binding.version_ref)
        if canonical_ref != binding.version_ref:
            raise EvidencePackageCurrentnessError(
                "Artifact resolver returned a different Package version"
            )
        content = EvidencePackageContentSchema.model_validate(raw)
        if require_source_fresh:
            section = content.section_plan_source
            plan_head = self.artifact_runtime.resolve_active(
                section.plan_version_ref.logical_ref
            )
            if (
                plan_head is None
                or plan_head.active_version_ref != section.plan_version_ref
            ):
                raise EvidencePackageCurrentnessError(
                    "EvidencePackage material SectionPlan source is stale"
                )
            try:
                self._resolve_fresh_plan_section(
                    SectionPlanBindingSchema(
                        plan_version_ref=section.plan_version_ref,
                        section_id=section.section_id,
                        resolved_head_revision=plan_head.head_revision,
                    )
                )
            except EvidencePackageSourceBindingError as exc:
                raise EvidencePackageCurrentnessError(str(exc)) from exc
            if content.direct_fact_snapshot_version_ref is not None:
                fact_head = self.artifact_runtime.resolve_active(
                    content.direct_fact_snapshot_version_ref.logical_ref
                )
                if (
                    fact_head is None
                    or fact_head.active_version_ref
                    != content.direct_fact_snapshot_version_ref
                ):
                    raise EvidencePackageCurrentnessError(
                        "EvidencePackage direct FactSnapshot source is stale"
                    )
            self.resolve_evidence_bundle(content.evidence_bundle_binding)
        return content

    # ------------------------------------------------------------------
    # SIF-08 ResearchRound / ResearchProgress
    # ------------------------------------------------------------------

    @staticmethod
    def derive_round_ref(
        *,
        run_id: str,
        section_id: str,
        authorization_binding: ResearchRoundAuthorizationBindingSchema,
        active_attempt: ActiveExecutionLineageSchema,
    ) -> ResearchRoundRefSchema:
        run = str(run_id or "").strip()
        section = str(section_id or "").strip()
        if not run or not section:
            raise ResearchRuntimeError("run_id and section_id are required")
        if active_attempt.run_id != run:
            raise ResearchIdentityCollision(
                "active Attempt lineage belongs to a different Run"
            )
        research_round_id = "rr_" + canonical_sha256(
            {
                "contract": "research_round_id_v1",
                "run_id": run,
                "section_id": section,
                "attempt_scope_ref": active_attempt.attempt_scope_ref,
                "attempt_id": active_attempt.attempt_id,
                "authorization_kind": authorization_binding.authorization_kind,
                "authorization_ref": authorization_binding.authorization_ref,
            }
        )
        return ResearchRoundRefSchema(
            run_id=run,
            section_id=section,
            attempt_scope_ref=active_attempt.attempt_scope_ref,
            attempt_id=active_attempt.attempt_id,
            research_round_id=research_round_id,
        )

    def authorize_round_ref(
        self,
        *,
        run_id: str,
        section_id: str,
        source_plan_binding: SectionPlanBindingSchema,
        authorization_binding: ResearchRoundAuthorizationBindingSchema,
        active_attempt: ActiveExecutionLineageSchema,
        authorization_context: object | None = None,
    ) -> ResearchRoundRefSchema:
        """Validate all pre-side-effect Research owners and return stable RoundRef."""

        if source_plan_binding.section_id != str(section_id or "").strip():
            raise EvidencePackageSourceBindingError(
                "Research authorization Plan binding targets a different Section"
            )
        if active_attempt.run_id != str(run_id or "").strip():
            raise ResearchIdentityCollision(
                "Research authorization Attempt belongs to a different Run"
            )
        current = self.lifecycle_service.assert_attempt_committable(
            run_id=active_attempt.run_id,
            attempt_scope_ref=active_attempt.attempt_scope_ref,
            attempt_id=active_attempt.attempt_id,
            input_snapshot_version=active_attempt.input_snapshot_version,
        )
        self._resolve_fresh_plan_section(source_plan_binding)
        round_ref = self.derive_round_ref(
            run_id=run_id,
            section_id=section_id,
            authorization_binding=authorization_binding,
            active_attempt=current,
        )
        if authorization_context is None:
            self.authorization_validator(authorization_binding, round_ref)
        else:
            self.authorization_validator(authorization_binding, round_ref, authorization_context)
        return round_ref

    @staticmethod
    def round_operation_ref(round_ref: ResearchRoundRefSchema) -> LogicalOperationRef:
        scope_ref = _round_scope_ref(round_ref)
        logical_operation_id = "research-round-commit:" + canonical_sha256(
            {
                "operation_kind": RESEARCH_ROUND_COMMIT_OPERATION_KIND,
                "round_scope_ref": scope_ref,
            }
        )
        return LogicalOperationRef(
            logical_operation_id=logical_operation_id,
            operation_kind=RESEARCH_ROUND_COMMIT_OPERATION_KIND,
            run_id=round_ref.run_id,
            operation_scope_ref=scope_ref,
            attempt_scope_ref=round_ref.attempt_scope_ref,
            attempt_id=round_ref.attempt_id,
        )

    @staticmethod
    def _initial_progress(
        *,
        run_id: str,
        section_id: str,
        attempt_scope_ref: str,
        attempt_id: str,
    ) -> ResearchProgressStateSchema:
        return ResearchProgressStateSchema(
            run_id=run_id,
            section_id=section_id,
            attempt_scope_ref=attempt_scope_ref,
            attempt_id=attempt_id,
            progress_revision=0,
            committed_round_count=0,
            last_committed_round_ref=None,
        )

    def _load_state_unlocked(self) -> _ResearchLedgerState:
        state = _ResearchLedgerState(rounds={}, operations={}, progress={})
        for line_number, line in enumerate(
            self.log.read_complete_lines(),
            start=1,
        ):
            if not line.strip():
                continue
            try:
                payload = json.loads(line)
            except Exception as exc:
                raise ResearchRuntimeIntegrityError(
                    f"invalid Research JSON at {self.ledger_path}:{line_number}"
                ) from exc
            if not isinstance(payload, dict):
                raise ResearchRuntimeIntegrityError(
                    "Research runtime event must be a JSON object"
                )
            if payload.get("schema_version") != _RESEARCH_EVENT_SCHEMA_VERSION:
                raise ResearchRuntimeIntegrityError(
                    "unknown Research runtime event schema version"
                )
            if payload.get("event_type") != "round_committed":
                raise ResearchRuntimeIntegrityError(
                    "unknown Research runtime event type"
                )
            operation_ref = _operation_from_payload(payload.get("operation_ref"))
            fingerprint = validate_sif02_semantic_fingerprint(
                str(payload.get("semantic_fingerprint") or "")
            )
            try:
                record = ResearchRoundRecordSchema.model_validate(
                    payload.get("record")
                )
                resulting_progress = ResearchProgressStateSchema.model_validate(
                    payload.get("resulting_progress")
                )
            except Exception as exc:
                raise ResearchRuntimeIntegrityError(
                    f"invalid Research record/progress at {self.ledger_path}:{line_number}"
                ) from exc
            result_ref = str(payload.get("result_ref") or "").strip()
            if not result_ref:
                raise ResearchRuntimeIntegrityError(
                    "Research committed event requires result_ref"
                )
            self._validate_round_operation_binding(record, operation_ref)
            if self.research_round_semantic_fingerprint(record) != fingerprint:
                raise ResearchRuntimeIntegrityError(
                    "persisted Research semantic fingerprint does not match record"
                )
            if operation_ref.logical_operation_id in state.operations:
                raise ResearchRuntimeIntegrityError(
                    "Research logical operation committed more than once"
                )
            rkey = _round_key(record.round_ref)
            if rkey in state.rounds:
                raise ResearchRuntimeIntegrityError(
                    "ResearchRound identity committed more than once"
                )
            pkey = _progress_key(
                run_id=record.round_ref.run_id,
                section_id=record.round_ref.section_id,
                attempt_scope_ref=record.round_ref.attempt_scope_ref,
                attempt_id=record.round_ref.attempt_id,
            )
            previous = state.progress.get(
                pkey,
                self._initial_progress(
                    run_id=record.round_ref.run_id,
                    section_id=record.round_ref.section_id,
                    attempt_scope_ref=record.round_ref.attempt_scope_ref,
                    attempt_id=record.round_ref.attempt_id,
                ),
            )
            if record.prior_committed_round_ref != previous.last_committed_round_ref:
                raise ResearchRuntimeIntegrityError(
                    "ResearchRound prior ref breaks canonical progress chain"
                )
            if record.round_ordinal != previous.committed_round_count + 1:
                raise ResearchRuntimeIntegrityError(
                    "ResearchRound ordinal is not the next canonical progress ordinal"
                )
            expected_progress = ResearchProgressStateSchema(
                run_id=previous.run_id,
                section_id=previous.section_id,
                attempt_scope_ref=previous.attempt_scope_ref,
                attempt_id=previous.attempt_id,
                progress_revision=previous.progress_revision + 1,
                committed_round_count=previous.committed_round_count + 1,
                last_committed_round_ref=record.round_ref,
            )
            if resulting_progress != expected_progress:
                raise ResearchRuntimeIntegrityError(
                    "ResearchRound event does not atomically expose its progress successor"
                )
            stored = _StoredRound(
                record=record,
                operation_ref=operation_ref,
                semantic_fingerprint=fingerprint,
                result_ref=result_ref,
                progress=resulting_progress,
            )
            state.rounds[rkey] = stored
            state.operations[operation_ref.logical_operation_id] = stored
            state.progress[pkey] = resulting_progress
        return state

    @staticmethod
    def _validate_round_operation_binding(
        record: ResearchRoundRecordSchema,
        operation_ref: LogicalOperationRef,
    ) -> None:
        ref = record.round_ref
        expected_scope = _round_scope_ref(ref)
        if operation_ref.operation_kind != RESEARCH_ROUND_COMMIT_OPERATION_KIND:
            raise ResearchIdentityCollision(
                "ResearchRound commit requires RESEARCH_ROUND_COMMIT operation"
            )
        if (
            operation_ref.run_id != ref.run_id
            or operation_ref.attempt_scope_ref != ref.attempt_scope_ref
            or operation_ref.attempt_id != ref.attempt_id
            or operation_ref.operation_scope_ref != expected_scope
        ):
            raise ResearchIdentityCollision(
                "ResearchRound logical operation does not bind exact Round identity"
            )

    @staticmethod
    def research_round_semantic_fingerprint(
        record: ResearchRoundRecordSchema,
    ) -> str:
        return canonical_sha256(record.model_dump(mode="json", round_trip=True))

    def resolve_progress(
        self,
        *,
        run_id: str,
        section_id: str,
        attempt_scope_ref: str,
        attempt_id: str,
    ) -> ResearchProgressStateSchema:
        key = _progress_key(
            run_id=str(run_id or "").strip(),
            section_id=str(section_id or "").strip(),
            attempt_scope_ref=str(attempt_scope_ref or "").strip(),
            attempt_id=str(attempt_id or "").strip(),
        )
        if not all(key):
            raise ValueError("complete Research progress identity is required")
        with self._lock, InterProcessFileLock(self.lock_path):
            state = self._load_state_unlocked()
            current = state.progress.get(key)
            if current is None:
                current = self._initial_progress(
                    run_id=key[0],
                    section_id=key[1],
                    attempt_scope_ref=key[2],
                    attempt_id=key[3],
                )
            return current.model_copy(deep=True)

    def resolve_round(
        self,
        round_ref: ResearchRoundRefSchema,
    ) -> ResearchRoundRecordSchema:
        ref = ResearchRoundRefSchema.model_validate(round_ref)
        with self._lock, InterProcessFileLock(self.lock_path):
            state = self._load_state_unlocked()
            stored = state.rounds.get(_round_key(ref))
            if stored is None:
                raise ResearchRoundNotFound(ref.research_round_id)
            return stored.record.model_copy(deep=True)

    def resolve_current_round_binding(
        self,
        *,
        run_id: str,
        section_id: str,
        attempt_scope_ref: str,
        attempt_id: str,
    ) -> ResearchRoundBindingSchema | None:
        progress = self.resolve_progress(
            run_id=run_id,
            section_id=section_id,
            attempt_scope_ref=attempt_scope_ref,
            attempt_id=attempt_id,
        )
        if progress.last_committed_round_ref is None:
            return None
        return ResearchRoundBindingSchema(
            round_ref=progress.last_committed_round_ref,
            resolved_progress_revision=progress.progress_revision,
        )

    def validate_round_binding_current(
        self,
        binding: ResearchRoundBindingSchema,
    ) -> ResearchRoundRecordSchema:
        ref = binding.round_ref
        progress = self.resolve_progress(
            run_id=ref.run_id,
            section_id=ref.section_id,
            attempt_scope_ref=ref.attempt_scope_ref,
            attempt_id=ref.attempt_id,
        )
        if (
            progress.progress_revision != binding.resolved_progress_revision
            or progress.last_committed_round_ref != ref
        ):
            raise ResearchProgressConflict("ResearchRound binding is stale")
        return self.resolve_round(ref)

    def resolve_committed_result(
        self,
        operation_ref: LogicalOperationRef,
        semantic_fingerprint: str,
    ) -> CommittedResultResolution:
        fingerprint = validate_sif02_semantic_fingerprint(semantic_fingerprint)
        if operation_ref.operation_kind != RESEARCH_ROUND_COMMIT_OPERATION_KIND:
            raise ResearchIdentityCollision(
                "Research committed-result resolver received wrong operation kind"
            )
        with self._lock, InterProcessFileLock(self.lock_path):
            state = self._load_state_unlocked()
            stored = state.operations.get(operation_ref.logical_operation_id)
            if stored is not None:
                if stored.operation_ref != operation_ref:
                    return CommittedResultResolution(
                        operation_ref.logical_operation_id,
                        fingerprint,
                        CommittedResultResolutionStatus.COMMITTED_CONFLICT,
                    )
                if stored.semantic_fingerprint != fingerprint:
                    return CommittedResultResolution(
                        operation_ref.logical_operation_id,
                        fingerprint,
                        CommittedResultResolutionStatus.COMMITTED_CONFLICT,
                    )
                return CommittedResultResolution(
                    operation_ref.logical_operation_id,
                    fingerprint,
                    CommittedResultResolutionStatus.COMMITTED_SAME,
                    (stored.result_ref,),
                )
            try:
                coordination = self.coordination_store.resolve(
                    operation_ref.logical_operation_id,
                    semantic_fingerprint=fingerprint,
                )
            except SideEffectIdentityConflict:
                return CommittedResultResolution(
                    operation_ref.logical_operation_id,
                    fingerprint,
                    CommittedResultResolutionStatus.COMMITTED_CONFLICT,
                )
            if coordination.status is SideEffectCoordinationStatus.COMMITTED:
                return CommittedResultResolution(
                    operation_ref.logical_operation_id,
                    fingerprint,
                    CommittedResultResolutionStatus.INTEGRITY_INVALID,
                )
            if coordination.status is SideEffectCoordinationStatus.IN_PROGRESS:
                return CommittedResultResolution(
                    operation_ref.logical_operation_id,
                    fingerprint,
                    CommittedResultResolutionStatus.IN_PROGRESS,
                )
            return CommittedResultResolution(
                operation_ref.logical_operation_id,
                fingerprint,
                CommittedResultResolutionStatus.ABSENT,
            )

    def _assert_attempt_current(
        self,
        record: ResearchRoundRecordSchema,
        commit_guard_context: ActiveExecutionLineageSchema,
    ) -> ActiveExecutionLineageSchema:
        ref = record.round_ref
        if (
            commit_guard_context.run_id != ref.run_id
            or commit_guard_context.attempt_scope_ref != ref.attempt_scope_ref
            or commit_guard_context.attempt_id != ref.attempt_id
        ):
            raise ResearchIdentityCollision(
                "Research commit guard does not match Round Attempt identity"
            )
        return self.lifecycle_service.assert_attempt_committable(
            run_id=ref.run_id,
            attempt_scope_ref=ref.attempt_scope_ref,
            attempt_id=ref.attempt_id,
            input_snapshot_version=commit_guard_context.input_snapshot_version,
        )

    def _validate_round_sources(
        self,
        *,
        record: ResearchRoundRecordSchema,
        source_plan_binding: SectionPlanBindingSchema,
        resulting_package_binding: EvidencePackageBindingSchema | None,
        authorization_context: object | None = None,
    ) -> None:
        if (
            source_plan_binding.plan_version_ref != record.source_plan_version_ref
            or source_plan_binding.section_id != record.round_ref.section_id
        ):
            raise EvidencePackageSourceBindingError(
                "ResearchRound exact Plan source/Section guard mismatch"
            )
        # SOURCE_FRESH means more than the Plan Artifact's own active head:
        # the exact FactSnapshot materially bound by that Plan must still be the
        # active canonical Fact head as well. This applies to successful and
        # TECHNICAL_FAILURE rounds alike.
        self._resolve_fresh_plan_section(source_plan_binding)

        if authorization_context is None:
            self.authorization_validator(record.authorization_binding, record.round_ref)
        else:
            self.authorization_validator(
                record.authorization_binding, record.round_ref, authorization_context,
            )

        if record.execution_outcome is ResearchRoundExecutionOutcome.TECHNICAL_FAILURE:
            if resulting_package_binding is not None:
                raise ResearchRuntimeIntegrityError(
                    "technical-failure Round cannot bind a resulting Package"
                )
            return

        if resulting_package_binding is None:
            raise EvidencePackageCurrentnessError(
                "valid Research completion requires current EvidencePackage binding"
            )
        if (
            resulting_package_binding.version_ref
            != record.package_progress.resulting_evidence_package_version_ref
        ):
            raise EvidencePackageCurrentnessError(
                "ResearchRound resulting Package ref/guard mismatch"
            )
        package_content = self.validate_evidence_package_binding_current(
            resulting_package_binding,
            require_source_fresh=True,
        )
        if (
            package_content.evidence_bundle_binding
            != record.final_evidence_bundle_binding
        ):
            raise EvidencePackageSourceBindingError(
                "ResearchRound final Bundle differs from committed Package source"
            )
        self.resolve_evidence_bundle(package_content.evidence_bundle_binding)

    @staticmethod
    def _assert_expected_progress(
        current: ResearchProgressStateSchema,
        *,
        record: ResearchRoundRecordSchema,
        expected_progress_revision: int,
        expected_committed_round_count: int,
        expected_last_committed_round_ref: ResearchRoundRefSchema | None,
    ) -> None:
        if (
            current.progress_revision != expected_progress_revision
            or current.committed_round_count != expected_committed_round_count
            or current.last_committed_round_ref
            != expected_last_committed_round_ref
        ):
            raise ResearchProgressConflict("EXPECTED_PROGRESS_STALE")
        if record.prior_committed_round_ref != current.last_committed_round_ref:
            raise ResearchProgressConflict(
                "ResearchRound prior ref does not match expected canonical progress"
            )
        if record.round_ordinal != current.committed_round_count + 1:
            raise ResearchProgressConflict(
                "ResearchRound ordinal does not match expected canonical progress"
            )

    def commit_round(
        self,
        *,
        round_candidate: ResearchRoundRecordSchema,
        operation_ref: LogicalOperationRef,
        source_plan_binding: SectionPlanBindingSchema,
        commit_guard_context: ActiveExecutionLineageSchema,
        expected_progress_revision: int,
        expected_committed_round_count: int,
        expected_last_committed_round_ref: ResearchRoundRefSchema | None,
        resulting_package_binding: EvidencePackageBindingSchema | None = None,
        authorization_context: object | None = None,
    ) -> ResearchRoundCommitResult:
        record = ResearchRoundRecordSchema.model_validate(
            round_candidate.model_dump(mode="python", round_trip=True)
        )
        self._validate_round_operation_binding(record, operation_ref)
        active = self._assert_attempt_current(record, commit_guard_context)
        derived = self.derive_round_ref(
            run_id=record.round_ref.run_id,
            section_id=record.round_ref.section_id,
            authorization_binding=record.authorization_binding,
            active_attempt=active,
        )
        if derived != record.round_ref:
            raise ResearchIdentityCollision(
                "ResearchRoundRef is not the deterministic authorization occurrence identity"
            )
        self._validate_round_sources(
            record=record,
            source_plan_binding=source_plan_binding,
            resulting_package_binding=resulting_package_binding,
            authorization_context=authorization_context,
        )
        fingerprint = self.research_round_semantic_fingerprint(record)

        with self._lock, InterProcessFileLock(self.lock_path):
            state = self._load_state_unlocked()
            stored = state.operations.get(operation_ref.logical_operation_id)
            if stored is not None:
                if (
                    stored.operation_ref != operation_ref
                    or stored.semantic_fingerprint != fingerprint
                ):
                    raise ResearchIdentityCollision(
                        "same Research logical operation conflicts with canonical history"
                    )
                if stored.record != record:
                    raise ResearchRuntimeIntegrityError(
                        "same Research operation/fingerprint resolved a different record"
                    )
                self._reconcile_coordination(stored)
                return ResearchRoundCommitResult(
                    record=stored.record,
                    progress=stored.progress,
                    binding=ResearchRoundBindingSchema(
                        round_ref=stored.record.round_ref,
                        resolved_progress_revision=stored.progress.progress_revision,
                    ),
                    reused=True,
                )

            pkey = _progress_key(
                run_id=record.round_ref.run_id,
                section_id=record.round_ref.section_id,
                attempt_scope_ref=record.round_ref.attempt_scope_ref,
                attempt_id=record.round_ref.attempt_id,
            )
            current = state.progress.get(
                pkey,
                self._initial_progress(
                    run_id=record.round_ref.run_id,
                    section_id=record.round_ref.section_id,
                    attempt_scope_ref=record.round_ref.attempt_scope_ref,
                    attempt_id=record.round_ref.attempt_id,
                ),
            )
            self._assert_expected_progress(
                current,
                record=record,
                expected_progress_revision=expected_progress_revision,
                expected_committed_round_count=expected_committed_round_count,
                expected_last_committed_round_ref=expected_last_committed_round_ref,
            )

            # Canonical source first, coordination second. Revalidate all owners
            # immediately before the single atomic semantic successor append.
            self._assert_attempt_current(record, commit_guard_context)
            self._validate_round_sources(
                record=record,
                source_plan_binding=source_plan_binding,
                resulting_package_binding=resulting_package_binding,
                authorization_context=authorization_context,
            )
            claim = self.coordination_store.claim(
                operation_ref.logical_operation_id,
                execution_id=f"research-runtime:{record.round_ref.research_round_id}",
                description="canonical ResearchRound successor commit",
                semantic_fingerprint=fingerprint,
            )
            if claim.status is SideEffectClaimStatus.ALREADY_COMMITTED:
                raise ResearchRuntimeIntegrityError(
                    "SIF-02 says Research committed but canonical Research source is absent"
                )
            if claim.status is SideEffectClaimStatus.ALREADY_IN_PROGRESS:
                raise ResearchOperationInProgress(
                    operation_ref.logical_operation_id
                )

            appended = False
            result_ref = ""
            try:
                self._assert_attempt_current(record, commit_guard_context)
                self._validate_round_sources(
                    record=record,
                    source_plan_binding=source_plan_binding,
                    resulting_package_binding=resulting_package_binding,
                    authorization_context=authorization_context,
                )
                latest = self._load_state_unlocked()
                latest_current = latest.progress.get(
                    pkey,
                    self._initial_progress(
                        run_id=record.round_ref.run_id,
                        section_id=record.round_ref.section_id,
                        attempt_scope_ref=record.round_ref.attempt_scope_ref,
                        attempt_id=record.round_ref.attempt_id,
                    ),
                )
                self._assert_expected_progress(
                    latest_current,
                    record=record,
                    expected_progress_revision=expected_progress_revision,
                    expected_committed_round_count=expected_committed_round_count,
                    expected_last_committed_round_ref=expected_last_committed_round_ref,
                )
                resulting_progress = ResearchProgressStateSchema(
                    run_id=latest_current.run_id,
                    section_id=latest_current.section_id,
                    attempt_scope_ref=latest_current.attempt_scope_ref,
                    attempt_id=latest_current.attempt_id,
                    progress_revision=latest_current.progress_revision + 1,
                    committed_round_count=latest_current.committed_round_count + 1,
                    last_committed_round_ref=record.round_ref,
                )
                result_ref = _round_result_ref(
                    record.round_ref,
                    resulting_progress.progress_revision,
                )
                self.log.append_serialized(
                    json.dumps(
                        {
                            "schema_version": _RESEARCH_EVENT_SCHEMA_VERSION,
                            "event_type": "round_committed",
                            "operation_ref": _operation_payload(operation_ref),
                            "semantic_fingerprint": fingerprint,
                            "record": record.model_dump(
                                mode="json",
                                round_trip=True,
                            ),
                            "resulting_progress": resulting_progress.model_dump(
                                mode="json",
                                round_trip=True,
                            ),
                            "result_ref": result_ref,
                        },
                        ensure_ascii=False,
                        sort_keys=True,
                        separators=(",", ":"),
                    )
                )
                appended = True
                verified = self._load_state_unlocked().operations.get(
                    operation_ref.logical_operation_id
                )
                if (
                    verified is None
                    or verified.record != record
                    or verified.progress != resulting_progress
                ):
                    raise ResearchRuntimeIntegrityError(
                        "durable ResearchRound/progress readback verification failed"
                    )
                self.coordination_store.mark_committed(
                    claim,
                    committed_result_refs=(result_ref,),
                )
                return ResearchRoundCommitResult(
                    record=record,
                    progress=resulting_progress,
                    binding=ResearchRoundBindingSchema(
                        round_ref=record.round_ref,
                        resolved_progress_revision=resulting_progress.progress_revision,
                    ),
                    reused=False,
                )
            except Exception:
                if not appended:
                    # Release only when canonical source proves the operation did
                    # not reach its atomic append commit point.
                    after = self._load_state_unlocked().operations.get(
                        operation_ref.logical_operation_id
                    )
                    if after is None:
                        self.coordination_store.release(
                            claim,
                            reason="research_round_commit_failed_before_canonical_commit",
                        )
                raise
            finally:
                self.coordination_store.finish_execution(claim)

    def _reconcile_coordination(self, stored: _StoredRound) -> None:
        try:
            coordination = self.coordination_store.resolve(
                stored.operation_ref.logical_operation_id,
                semantic_fingerprint=stored.semantic_fingerprint,
            )
        except SideEffectIdentityConflict as exc:
            raise ResearchRuntimeIntegrityError(
                "Research SIF-02 coordination conflicts with canonical source"
            ) from exc
        if coordination.status is SideEffectCoordinationStatus.COMMITTED:
            if coordination.committed_result_refs != (stored.result_ref,):
                raise ResearchRuntimeIntegrityError(
                    "Research coordination result refs differ from canonical source"
                )
            return
        if coordination.status is SideEffectCoordinationStatus.IN_PROGRESS:
            # Canonical Research source is already committed. Exactly like
            # ArtifactRuntime, a lagging bounded SIF-02 coordination lease must
            # never cause the committed Round to rerun, roll back, or become
            # unavailable merely because acknowledgement crashed after append.
            return
        claim = self.coordination_store.claim(
            stored.operation_ref.logical_operation_id,
            execution_id=f"research-reconcile:{stored.record.round_ref.research_round_id}",
            description="reconcile canonical ResearchRound commit",
            semantic_fingerprint=stored.semantic_fingerprint,
        )
        if claim.status is not SideEffectClaimStatus.ACQUIRED:
            raise ResearchRuntimeIntegrityError(
                "Research coordination reconciliation could not acquire absent slot"
            )
        try:
            self.coordination_store.mark_committed(
                claim,
                committed_result_refs=(stored.result_ref,),
            )
        finally:
            self.coordination_store.finish_execution(claim)


__all__ = [
    "EVIDENCE_PACKAGE_ARTIFACT_TYPE",
    "EVIDENCE_PACKAGE_CONTENT_SCHEMA_VERSION",
    "RESEARCH_ROUND_COMMIT_OPERATION_KIND",
    "EvidenceBundleResolver",
    "EvidencePackageCommitOperations",
    "EvidencePackageCurrentnessError",
    "EvidencePackageSourceBindingError",
    "ResearchAuthorizationValidator",
    "ResearchIdentityCollision",
    "ResearchOperationInProgress",
    "ResearchProgressConflict",
    "ResearchRoundCommitResult",
    "ResearchRoundNotFound",
    "ResearchRuntime",
    "ResearchRuntimeError",
    "ResearchRuntimeIntegrityError",
]
