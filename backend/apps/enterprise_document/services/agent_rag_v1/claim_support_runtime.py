"""Canonical shared CLAIM_EVIDENCE_SUPPORT runtime.

One exact Claim-support subject + exact Evidence target set + support semantic
contract version has at most one canonical support fact. Technical/execution
failure produces no candidate and therefore no canonical support commit.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
import json
from core.diagnostics import current_diagnostics, diagnostic_operation
from pathlib import Path
import threading
from typing import Callable

from apps.enterprise_document.schemas.agent_rag_v1.claim_support import (
    ClaimEvidenceSupportCandidateSchema,
    ClaimEvidenceSupportRecordSchema,
    ClaimEvidenceSupportRefSchema,
    ClaimSupportAuthorityKind,
    ClaimSupportSubjectRefSchema,
    EvidenceTargetSetRefSchema,
)
from apps.enterprise_document.schemas.agent_rag_v1.claims import ClaimRecordSchema
from apps.enterprise_document.schemas.agent_rag_v1.verification import (
    ClaimOperationContextSchema,
)
from apps.enterprise_document.services.agent_rag_v1.claim_verification_runtime import (
    ClaimVerificationRuntime,
)
from core.hashing import canonical_sha256
from core.persistence.crash_safe_jsonl import CrashSafeJsonlEventLog
from core.persistence.interprocess_lock import InterProcessFileLock
from core.runtime.side_effects import (
    CommittedResultResolution,
    CommittedResultResolutionStatus,
    JsonlSideEffectCommitStore,
    LogicalOperationRef,
    SideEffectClaim,
    SideEffectClaimStatus,
    SideEffectCommitStore,
    SideEffectCoordinationStatus,
    SideEffectIdentityConflict,
    validate_sif02_semantic_fingerprint,
)


CLAIM_EVIDENCE_SUPPORT_COMMIT = "CLAIM_EVIDENCE_SUPPORT_COMMIT"
_EVENT_SCHEMA_VERSION = "claim_support_event_v1"


class ClaimSupportRuntimeError(ValueError):
    """Base shared support runtime failure."""


class ClaimSupportIdentityCollision(ClaimSupportRuntimeError):
    """Same SupportRelationIdentity attempted a different canonical fact."""


class ClaimSupportCurrentnessError(ClaimSupportRuntimeError):
    """Claim/Evidence target/current lineage is stale or unauthorized."""


class ClaimSupportIntegrityError(ClaimSupportRuntimeError):
    """Canonical shared-support ledger is corrupt."""


class ClaimSupportOperationInProgress(ClaimSupportRuntimeError):
    """Another executor currently owns the same support commit."""


class ClaimSupportTechnicalFailure(ClaimSupportRuntimeError):
    """Technical/protocol/evaluator failure: NO canonical support candidate/commit."""


EvidenceTargetValidator = Callable[
    [EvidenceTargetSetRefSchema, ClaimOperationContextSchema],
    None,
]
SupportEvaluator = Callable[
    [
        ClaimSupportSubjectRefSchema,
        EvidenceTargetSetRefSchema,
        str,
        ClaimOperationContextSchema,
    ],
    ClaimEvidenceSupportCandidateSchema,
]
ForeignSubjectValidator = Callable[
    [ClaimSupportSubjectRefSchema, ClaimOperationContextSchema],
    None,
]


@dataclass(frozen=True)
class SupportEvaluationOutcome:
    existing_record: ClaimEvidenceSupportRecordSchema | None = None
    candidate: ClaimEvidenceSupportCandidateSchema | None = None

    def __post_init__(self) -> None:
        if (self.existing_record is None) == (self.candidate is None):
            raise ValueError(
                "SupportEvaluationOutcome requires exactly one existing_record/candidate"
            )


@dataclass(frozen=True)
class _StoredSupportOperation:
    operation_ref: LogicalOperationRef
    semantic_fingerprint: str
    result_ref: str
    record: ClaimEvidenceSupportRecordSchema


def _subject_key(subject: ClaimSupportSubjectRefSchema) -> tuple[str, str]:
    return (subject.authority_kind.value, subject.subject_ref)


def _identity_key(
    subject: ClaimSupportSubjectRefSchema,
    target: EvidenceTargetSetRefSchema,
    support_semantic_contract_version: str,
) -> tuple[str, str, str]:
    return (
        canonical_sha256(subject.model_dump(mode="json")),
        target.evidence_target_set_id,
        str(support_semantic_contract_version),
    )


class ClaimSupportRuntime:
    """Shared support owner with resolve-before-cognition semantics."""

    _LOCKS_GUARD = threading.Lock()
    _LOCKS: dict[str, threading.RLock] = {}

    def __init__(
        self,
        *,
        claim_runtime: ClaimVerificationRuntime,
        evidence_target_validator: EvidenceTargetValidator,
        support_evaluator: SupportEvaluator,
        foreign_subject_validator: ForeignSubjectValidator | None = None,
        ledger_path: str | Path = "data/runtime/agent_rag_v1/claim_support.jsonl",
        coordination_store: SideEffectCommitStore | None = None,
    ) -> None:
        if not callable(evidence_target_validator):
            raise TypeError("ClaimSupportRuntime requires evidence_target_validator")
        if not callable(support_evaluator):
            raise TypeError("ClaimSupportRuntime requires support_evaluator")
        if foreign_subject_validator is not None and not callable(
            foreign_subject_validator
        ):
            raise TypeError("foreign_subject_validator must be callable")
        self.claim_runtime = claim_runtime
        self.evidence_target_validator = evidence_target_validator
        self.support_evaluator = support_evaluator
        self.foreign_subject_validator = foreign_subject_validator
        self.ledger_path = Path(ledger_path)
        self.log = CrashSafeJsonlEventLog(self.ledger_path)
        self.lock_path = self.ledger_path.with_suffix(
            self.ledger_path.suffix + ".lock"
        )
        self.coordination_store = coordination_store or JsonlSideEffectCommitStore(
            self.ledger_path.with_suffix(
                self.ledger_path.suffix + ".coordination"
            )
        )
        lock_key = str(self.ledger_path.resolve())
        with self._LOCKS_GUARD:
            self._lock = self._LOCKS.setdefault(lock_key, threading.RLock())

    # ------------------------------------------------------------------
    # Deterministic identity / target materialization
    # ------------------------------------------------------------------

    @staticmethod
    def materialize_evidence_target_set(
        claim_support_subject_ref: ClaimSupportSubjectRefSchema,
        evidence_member_refs,
        target_set_contract_version: str,
    ) -> EvidenceTargetSetRefSchema:
        subject = ClaimSupportSubjectRefSchema.model_validate(
            claim_support_subject_ref
        )
        members = tuple(
            sorted(
                {
                    str(item).strip()
                    for item in evidence_member_refs
                    if str(item).strip()
                }
            )
        )
        contract = str(target_set_contract_version or "").strip()
        if not contract:
            raise ClaimSupportRuntimeError(
                "target_set_contract_version must be non-empty"
            )
        semantic_payload = {
            "claim_support_subject_ref": subject.model_dump(mode="json"),
            "evidence_member_refs": members,
            "target_set_contract_version": contract,
        }
        semantic_hash = canonical_sha256(semantic_payload)
        return EvidenceTargetSetRefSchema(
            evidence_target_set_id=(
                "evidence-target-set://sha256/" + semantic_hash
            ),
            claim_support_subject_ref=subject,
            evidence_member_refs=members,
            target_set_contract_version=contract,
            target_set_semantic_hash=semantic_hash,
        )

    @staticmethod
    def support_semantic_fingerprint(
        candidate: ClaimEvidenceSupportCandidateSchema,
    ) -> str:
        candidate = ClaimEvidenceSupportCandidateSchema.model_validate(candidate)
        return canonical_sha256(
            {
                "contract": "claim_evidence_support_semantics_v1",
                "claim_support_subject_ref": (
                    candidate.claim_support_subject_ref.model_dump(mode="json")
                ),
                "evidence_target_set_ref": (
                    candidate.evidence_target_set_ref.model_dump(mode="json")
                ),
                "support_semantic_contract_version": (
                    candidate.support_semantic_contract_version
                ),
                "relation": candidate.relation.value,
                "supporting_refs": candidate.supporting_refs,
                "contradicting_refs": candidate.contradicting_refs,
                "evaluator_policy_id": candidate.evaluator_policy_id,
                "evaluator_policy_version": candidate.evaluator_policy_version,
            }
        )

    @staticmethod
    def _support_ref(
        candidate: ClaimEvidenceSupportCandidateSchema,
        fingerprint: str,
    ) -> ClaimEvidenceSupportRefSchema:
        identity = {
            "claim_support_subject_ref": (
                candidate.claim_support_subject_ref.model_dump(mode="json")
            ),
            "evidence_target_set_ref": (
                candidate.evidence_target_set_ref.model_dump(mode="json")
            ),
            "support_semantic_contract_version": (
                candidate.support_semantic_contract_version
            ),
        }
        return ClaimEvidenceSupportRefSchema(
            claim_evidence_support_id=(
                "claim-evidence-support://sha256/"
                + canonical_sha256(identity)
            ),
            claim_support_subject_ref=candidate.claim_support_subject_ref,
            evidence_target_set_ref=candidate.evidence_target_set_ref,
            support_semantic_contract_version=(
                candidate.support_semantic_contract_version
            ),
            support_semantic_fingerprint=fingerprint,
        )

    @staticmethod
    def _operation_ref(
        *,
        subject: ClaimSupportSubjectRefSchema,
        target: EvidenceTargetSetRefSchema,
        support_semantic_contract_version: str,
        context: ClaimOperationContextSchema,
    ) -> LogicalOperationRef:
        lineage = context.active_lineage
        return LogicalOperationRef(
            logical_operation_id=(
                "claim-support-op:"
                + canonical_sha256(
                    {
                        "operation_kind": CLAIM_EVIDENCE_SUPPORT_COMMIT,
                        "support_relation_identity": {
                            "subject": subject.model_dump(mode="json"),
                            "target": target.model_dump(mode="json"),
                            "support_semantic_contract_version": (
                                support_semantic_contract_version
                            ),
                        },
                    }
                )
            ),
            operation_kind=CLAIM_EVIDENCE_SUPPORT_COMMIT,
            run_id=lineage.run_id,
            operation_scope_ref=target.evidence_target_set_id,
            attempt_scope_ref=lineage.attempt_scope_ref,
            attempt_id=lineage.attempt_id,
        )

    # ------------------------------------------------------------------
    # Currentness / subject validation
    # ------------------------------------------------------------------

    def _validate_subject(
        self,
        subject: ClaimSupportSubjectRefSchema,
        context: ClaimOperationContextSchema,
    ) -> ClaimRecordSchema | None:
        self.claim_runtime.validate_operation_context(context)
        if subject.authority_kind is ClaimSupportAuthorityKind.SIF07_CLAIM:
            record = self.claim_runtime.resolve_claim_by_id(subject.subject_ref)
            if record.claim_ref.claim_id != subject.subject_ref:
                raise ClaimSupportCurrentnessError(
                    "SIF07_CLAIM support subject resolves different ClaimRecord"
                )
            return record
        if self.foreign_subject_validator is None:
            raise ClaimSupportCurrentnessError(
                "foreign support subject has no authorized validator"
            )
        try:
            self.foreign_subject_validator(subject, context)
        except ClaimSupportRuntimeError:
            raise
        except Exception as exc:
            raise ClaimSupportCurrentnessError(
                "foreign support subject validation failed"
            ) from exc
        return None

    def _validate_target(
        self,
        target: EvidenceTargetSetRefSchema,
        context: ClaimOperationContextSchema,
    ) -> None:
        expected = self.materialize_evidence_target_set(
            target.claim_support_subject_ref,
            target.evidence_member_refs,
            target.target_set_contract_version,
        )
        if expected != target:
            raise ClaimSupportIdentityCollision(
                "EvidenceTargetSetRef is not deterministic"
            )
        try:
            self.evidence_target_validator(target, context)
        except ClaimSupportRuntimeError:
            raise
        except Exception as exc:
            raise ClaimSupportCurrentnessError(
                "exact Evidence target validation failed"
            ) from exc

    # ------------------------------------------------------------------
    # Canonical ledger
    # ------------------------------------------------------------------

    @diagnostic_operation("claim_support.load", "FS_PARSE_SCOPE")
    def _load_state_unlocked(
        self,
    ) -> tuple[
        dict[tuple[str, str, str], ClaimEvidenceSupportRecordSchema],
        dict[str, ClaimEvidenceSupportRecordSchema],
        dict[str, _StoredSupportOperation],
    ]:
        by_identity: dict[
            tuple[str, str, str],
            ClaimEvidenceSupportRecordSchema,
        ] = {}
        by_ref: dict[str, ClaimEvidenceSupportRecordSchema] = {}
        operations: dict[str, _StoredSupportOperation] = {}
        with current_diagnostics().span("claim_support.read", "FS_API_ELAPSED"):
            lines = self.log.read_complete_lines()
        for line_number, line in enumerate(
            lines,
            start=1,
        ):
            if not line.strip():
                continue
            try:
                event = json.loads(line)
            except json.JSONDecodeError as exc:
                raise ClaimSupportIntegrityError(
                    f"invalid Claim support JSON at line {line_number}"
                ) from exc
            if (
                not isinstance(event, dict)
                or event.get("schema_version") != _EVENT_SCHEMA_VERSION
                or event.get("event_type") != "support_committed"
            ):
                raise ClaimSupportIntegrityError(
                    "unknown canonical Claim support event"
                )
            try:
                operation_ref = LogicalOperationRef(
                    **dict(event["operation_ref"])
                )
                fingerprint = validate_sif02_semantic_fingerprint(
                    str(event["semantic_fingerprint"])
                )
                result_ref = str(event["result_ref"])
                record = ClaimEvidenceSupportRecordSchema.model_validate(
                    event["record"]
                )
            except Exception as exc:
                raise ClaimSupportIntegrityError(
                    "invalid canonical Claim support event payload"
                ) from exc
            if operation_ref.operation_kind != CLAIM_EVIDENCE_SUPPORT_COMMIT:
                raise ClaimSupportIntegrityError(
                    "Claim support event has wrong operation kind"
                )
            if record.committed_operation_id != operation_ref.logical_operation_id:
                raise ClaimSupportIntegrityError(
                    "Claim support record operation mismatch"
                )
            if record.support_ref.support_semantic_fingerprint != fingerprint:
                raise ClaimSupportIntegrityError(
                    "Claim support fingerprint mismatch"
                )
            expected_result = (
                "claim-evidence-support-record://sha256/"
                + canonical_sha256(record.support_ref.model_dump(mode="json"))
            )
            if result_ref != expected_result:
                raise ClaimSupportIntegrityError(
                    "Claim support result_ref mismatch"
                )
            identity = _identity_key(
                record.claim_support_subject_ref,
                record.evidence_target_set_ref,
                record.support_semantic_contract_version,
            )
            existing = by_identity.get(identity)
            if existing is not None and existing != record:
                raise ClaimSupportIntegrityError(
                    "duplicate support identity has conflicting record"
                )
            ref_id = record.support_ref.claim_evidence_support_id
            existing_ref = by_ref.get(ref_id)
            if existing_ref is not None and existing_ref != record:
                raise ClaimSupportIntegrityError(
                    "duplicate support ref has conflicting record"
                )
            if operation_ref.logical_operation_id in operations:
                raise ClaimSupportIntegrityError(
                    "duplicate support logical operation"
                )
            by_identity[identity] = record
            by_ref[ref_id] = record
            operations[operation_ref.logical_operation_id] = (
                _StoredSupportOperation(
                    operation_ref=operation_ref,
                    semantic_fingerprint=fingerprint,
                    result_ref=result_ref,
                    record=record,
                )
            )
        return by_identity, by_ref, operations

    def _claim_side_effect(
        self,
        operation_ref: LogicalOperationRef,
        fingerprint: str,
    ) -> SideEffectClaim:
        try:
            claim = self.coordination_store.claim(
                operation_ref.logical_operation_id,
                execution_id=f"claim-support-runtime:{threading.get_ident()}",
                description=operation_ref.operation_kind,
                semantic_fingerprint=fingerprint,
            )
        except SideEffectIdentityConflict as exc:
            raise ClaimSupportIdentityCollision(
                "support coordination fingerprint collision"
            ) from exc
        if claim.status is SideEffectClaimStatus.ALREADY_COMMITTED:
            raise ClaimSupportIntegrityError(
                "support coordination says committed while canonical source is absent"
            )
        if claim.status is SideEffectClaimStatus.ALREADY_IN_PROGRESS:
            raise ClaimSupportOperationInProgress(
                "support logical operation is already in progress"
            )
        return claim

    def _ack_side_effect(self, claim: SideEffectClaim, result_ref: str) -> None:
        try:
            if not self.coordination_store.mark_committed(
                claim,
                committed_result_refs=(result_ref,),
            ):
                resolution = self.coordination_store.resolve(
                    claim.side_effect_id,
                    semantic_fingerprint=claim.semantic_fingerprint,
                )
                if (
                    resolution.status is not SideEffectCoordinationStatus.COMMITTED
                    or resolution.committed_result_refs != (result_ref,)
                ):
                    raise ClaimSupportIntegrityError(
                        "support coordination acknowledgement conflict"
                    )
        except SideEffectIdentityConflict as exc:
            raise ClaimSupportIdentityCollision(
                "support coordination fingerprint collision"
            ) from exc

    def _reconcile(self, stored: _StoredSupportOperation) -> None:
        try:
            resolution = self.coordination_store.resolve(
                stored.operation_ref.logical_operation_id,
                semantic_fingerprint=stored.semantic_fingerprint,
            )
        except SideEffectIdentityConflict as exc:
            raise ClaimSupportIdentityCollision(
                "support coordination conflicts with canonical source"
            ) from exc
        if resolution.status is SideEffectCoordinationStatus.COMMITTED:
            if resolution.committed_result_refs != (stored.result_ref,):
                raise ClaimSupportIntegrityError(
                    "support coordination refs disagree with canonical source"
                )
            return
        if resolution.status is SideEffectCoordinationStatus.IN_PROGRESS:
            return
        claim = self.coordination_store.claim(
            stored.operation_ref.logical_operation_id,
            execution_id="claim-support-runtime:canonical-reconcile",
            description="reconcile canonical support",
            semantic_fingerprint=stored.semantic_fingerprint,
        )
        if claim.status is SideEffectClaimStatus.ACQUIRED:
            self._ack_side_effect(claim, stored.result_ref)
            return
        if claim.status is SideEffectClaimStatus.ALREADY_COMMITTED:
            if claim.committed_result_refs != (stored.result_ref,):
                raise ClaimSupportIntegrityError(
                    "reconciled support refs disagree"
                )
            return
        raise ClaimSupportOperationInProgress(
            "support reconciliation remains in progress"
        )

    # ------------------------------------------------------------------
    # Resolve-before-cognition / evaluation / commit
    # ------------------------------------------------------------------

    def resolve_support_by_identity(
        self,
        claim_support_subject_ref: ClaimSupportSubjectRefSchema,
        evidence_target_set_ref: EvidenceTargetSetRefSchema,
        support_semantic_contract_version: str,
    ) -> ClaimEvidenceSupportRecordSchema | None:
        subject = ClaimSupportSubjectRefSchema.model_validate(
            claim_support_subject_ref
        )
        target = EvidenceTargetSetRefSchema.model_validate(
            evidence_target_set_ref
        )
        contract = str(support_semantic_contract_version or "").strip()
        if target.claim_support_subject_ref != subject:
            raise ClaimSupportIdentityCollision(
                "support identity subject/target mismatch"
            )
        with self._lock, InterProcessFileLock(self.lock_path):
            by_identity, _by_ref, _operations = self._load_state_unlocked()
            return by_identity.get(_identity_key(subject, target, contract))

    def evaluate_support_candidate(
        self,
        claim_support_subject_ref: ClaimSupportSubjectRefSchema,
        evidence_target_set_ref: EvidenceTargetSetRefSchema,
        support_semantic_contract_version: str,
        operation_context: ClaimOperationContextSchema,
        *,
        support_evaluator=None,
    ) -> SupportEvaluationOutcome:
        subject = ClaimSupportSubjectRefSchema.model_validate(
            claim_support_subject_ref
        )
        target = EvidenceTargetSetRefSchema.model_validate(
            evidence_target_set_ref
        )
        context = ClaimOperationContextSchema.model_validate(operation_context)
        contract = str(support_semantic_contract_version or "").strip()
        if not contract:
            raise ClaimSupportRuntimeError(
                "support_semantic_contract_version must be non-empty"
            )

        existing = self.resolve_support_by_identity(
            subject,
            target,
            contract,
        )
        if existing is not None:
            return SupportEvaluationOutcome(existing_record=existing)

        self._validate_subject(subject, context)
        self._validate_target(target, context)
        try:
            raw_candidate = (self.support_evaluator if support_evaluator is None else support_evaluator)(
                subject,
                target,
                contract,
                context,
            )
            candidate = ClaimEvidenceSupportCandidateSchema.model_validate(
                raw_candidate
            )
        except ClaimSupportTechnicalFailure:
            raise
        except Exception as exc:
            raise ClaimSupportTechnicalFailure(
                "support evaluator failed or produced invalid canonical candidate"
            ) from exc

        if (
            candidate.claim_support_subject_ref != subject
            or candidate.evidence_target_set_ref != target
            or candidate.support_semantic_contract_version != contract
        ):
            raise ClaimSupportTechnicalFailure(
                "support evaluator candidate identity does not match requested relation"
            )
        return SupportEvaluationOutcome(candidate=candidate)

    def commit_support(
        self,
        candidate: ClaimEvidenceSupportCandidateSchema,
        operation_context: ClaimOperationContextSchema,
    ) -> ClaimEvidenceSupportRecordSchema:
        candidate = ClaimEvidenceSupportCandidateSchema.model_validate(candidate)
        context = ClaimOperationContextSchema.model_validate(operation_context)
        self._validate_subject(candidate.claim_support_subject_ref, context)
        self._validate_target(candidate.evidence_target_set_ref, context)
        fingerprint = self.support_semantic_fingerprint(candidate)
        support_ref = self._support_ref(candidate, fingerprint)
        operation_ref = self._operation_ref(
            subject=candidate.claim_support_subject_ref,
            target=candidate.evidence_target_set_ref,
            support_semantic_contract_version=(
                candidate.support_semantic_contract_version
            ),
            context=context,
        )
        identity = _identity_key(
            candidate.claim_support_subject_ref,
            candidate.evidence_target_set_ref,
            candidate.support_semantic_contract_version,
        )

        with self._lock, InterProcessFileLock(self.lock_path):
            by_identity, _by_ref, operations = self._load_state_unlocked()
            existing_identity = by_identity.get(identity)
            if existing_identity is not None:
                if (
                    existing_identity.support_ref.support_semantic_fingerprint
                    != fingerprint
                ):
                    raise ClaimSupportIdentityCollision(
                        "same SupportRelationIdentity has different semantic payload"
                    )
                stored = operations.get(
                    existing_identity.committed_operation_id
                )
                if stored is not None:
                    self._reconcile(stored)
                return existing_identity

            existing_operation = operations.get(
                operation_ref.logical_operation_id
            )
            if existing_operation is not None:
                if existing_operation.semantic_fingerprint != fingerprint:
                    raise ClaimSupportIdentityCollision(
                        "same support logical operation changed semantics"
                    )
                self._reconcile(existing_operation)
                return existing_operation.record

            # Recheck exact currentness immediately before canonical append.
            self._validate_subject(
                candidate.claim_support_subject_ref,
                context,
            )
            self._validate_target(
                candidate.evidence_target_set_ref,
                context,
            )
            record = ClaimEvidenceSupportRecordSchema(
                support_ref=support_ref,
                claim_support_subject_ref=candidate.claim_support_subject_ref,
                evidence_target_set_ref=candidate.evidence_target_set_ref,
                support_semantic_contract_version=(
                    candidate.support_semantic_contract_version
                ),
                relation=candidate.relation,
                supporting_refs=candidate.supporting_refs,
                contradicting_refs=candidate.contradicting_refs,
                evaluator_policy_id=candidate.evaluator_policy_id,
                evaluator_policy_version=candidate.evaluator_policy_version,
                evaluation_occurrence_ref=candidate.evaluation_occurrence_ref,
                committed_operation_id=operation_ref.logical_operation_id,
            )
            result_ref = (
                "claim-evidence-support-record://sha256/"
                + canonical_sha256(support_ref.model_dump(mode="json"))
            )
            side_effect = self._claim_side_effect(
                operation_ref,
                fingerprint,
            )
            canonical_appended = False
            try:
                event = {
                    "schema_version": _EVENT_SCHEMA_VERSION,
                    "event_type": "support_committed",
                    "operation_ref": asdict(operation_ref),
                    "semantic_fingerprint": fingerprint,
                    "result_ref": result_ref,
                    "record": record.model_dump(mode="json"),
                }
                self.log.append_serialized(
                    json.dumps(
                        event,
                        ensure_ascii=False,
                        sort_keys=True,
                        separators=(",", ":"),
                    )
                )
                canonical_appended = True
                self._ack_side_effect(side_effect, result_ref)
            except Exception:
                if not canonical_appended:
                    self.coordination_store.release(
                        side_effect,
                        reason="support_commit_failed_before_canonical_append",
                    )
                raise
            return record

    def resolve_support(
        self,
        ref: ClaimEvidenceSupportRefSchema,
    ) -> ClaimEvidenceSupportRecordSchema:
        ref = ClaimEvidenceSupportRefSchema.model_validate(ref)
        with self._lock, InterProcessFileLock(self.lock_path):
            _by_identity, by_ref, _operations = self._load_state_unlocked()
            record = by_ref.get(ref.claim_evidence_support_id)
            if record is None:
                raise ClaimSupportRuntimeError(
                    "ClaimEvidenceSupportRef does not resolve"
                )
            if record.support_ref != ref:
                raise ClaimSupportIntegrityError(
                    "ClaimEvidenceSupportRef resolves different record"
                )
            return record

    def resolve_committed_result(
        self,
        operation_ref: LogicalOperationRef,
        semantic_fingerprint: str,
    ) -> CommittedResultResolution:
        fingerprint = validate_sif02_semantic_fingerprint(
            semantic_fingerprint
        )
        with self._lock, InterProcessFileLock(self.lock_path):
            _by_identity, _by_ref, operations = self._load_state_unlocked()
            stored = operations.get(operation_ref.logical_operation_id)
            if stored is None:
                return CommittedResultResolution(
                    operation_ref.logical_operation_id,
                    fingerprint,
                    CommittedResultResolutionStatus.ABSENT,
                )
            if stored.operation_ref != operation_ref:
                return CommittedResultResolution(
                    operation_ref.logical_operation_id,
                    fingerprint,
                    CommittedResultResolutionStatus.INTEGRITY_INVALID,
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


__all__ = [
    "CLAIM_EVIDENCE_SUPPORT_COMMIT",
    "ClaimSupportCurrentnessError",
    "ClaimSupportIdentityCollision",
    "ClaimSupportIntegrityError",
    "ClaimSupportOperationInProgress",
    "ClaimSupportRuntime",
    "ClaimSupportRuntimeError",
    "ClaimSupportTechnicalFailure",
    "SupportEvaluationOutcome",
]
