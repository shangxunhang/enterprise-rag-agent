"""Canonical M6 Claim existence/coverage/binding/verification runtime.

This module is the deterministic Claim-domain owner. Claim existence and
coverage commit before shared support/verification. State/checkpoint projections
never create Claim, BindingFacts, Verification, Head, or Issue truth.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
import hashlib
import json
from core.diagnostics import current_diagnostics, diagnostic_operation
from pathlib import Path
import threading
from typing import Callable, Iterable

from apps.enterprise_document.schemas.agent_rag_v1.claim_support import (
    ClaimEvidenceSupportRecordSchema,
    ClaimEvidenceSupportRefSchema,
    ClaimSupportAuthorityKind,
    SupportRelation,
)
from apps.enterprise_document.schemas.agent_rag_v1.claims import (
    ClaimCandidateSchema,
    ClaimCoverageAccountingSchema,
    ClaimCoverageAccountingState,
    ClaimCoverageProofRefSchema,
    ClaimOccurrenceRefSchema,
    ClaimRecordCommitReceiptSchema,
    ClaimRecordSchema,
    ClaimRefSchema,
)
from apps.enterprise_document.schemas.agent_rag_v1.enums import ClaimLifecycleStatus
from apps.enterprise_document.schemas.agent_rag_v1.lifecycle import (
    ActiveExecutionLineageSchema,
)
from apps.enterprise_document.schemas.agent_rag_v1.verification import (
    ActiveVerifiedClaimViewSchema,
    ClaimBindingFactsCandidateSchema,
    ClaimBindingFactsRecordSchema,
    ClaimBindingFactsRefSchema,
    ClaimCurrentHeadSchema,
    ClaimLifecycleTransitionReceiptSchema,
    ClaimOperationContextSchema,
    ClaimVerificationCommitReceiptSchema,
    ClaimVerificationRevisionRecordSchema,
    ClaimVerificationRevisionRefSchema,
    VerificationResultRecordSchema,
    VerificationResultRefSchema,
    VerificationVerdict,
    VerifierIssueHeadSchema,
    VerifierIssueLifecycleStatus,
    VerifierIssueLifecycleTransitionReceiptSchema,
    VerifierIssueRecordSchema,
    VerifierIssueRefSchema,
    VerifierSemanticIssueCandidateSchema,
    VerifierSemanticResultCandidateSchema,
    VerifierClaimInputSchema,
    VERIFIER_LOCAL_REGISTRATION_KEY,
    VERIFIER_LOCAL_CORRECTION_KEY,
    VERIFIER_RECOVERY_CONTROL_VERSION,
    ClaimVerifierRecoveryControlEventV1,
    VerifierOriginalRootV1,
    VerifierCandidateContractViolationV1,
    VerifierRecoveryArtifactChainV1,
    VerifierCandidateRejectedV1,
    VerifierCorrectionAuthorizedV1,
    VerifierCorrectionTerminatedV1,
    VerifierLocalCorrectionBindingV1,
    build_verifier_registration_scope_sha256,
    build_verifier_candidate_contract_violation,
    build_verifier_candidate_rejection,
    build_verifier_correction_authorization,
    build_verifier_correction_termination,
    build_verifier_recovery_control_event,
    build_verifier_recovery_feedback,
)
from apps.enterprise_document.services.agent_rag_v1.paragraph_runtime import (
    ParagraphCurrentnessError,
    ParagraphRuntime,
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


CLAIM_RECORD_COMMIT = "CLAIM_RECORD_COMMIT"
CLAIM_BINDING_FACTS_COMMIT = "CLAIM_BINDING_FACTS_COMMIT"
CLAIM_VERIFICATION_COMMIT = "CLAIM_VERIFICATION_COMMIT"
CLAIM_LIFECYCLE_TRANSITION = "CLAIM_LIFECYCLE_TRANSITION"
VERIFIER_ISSUE_LIFECYCLE_TRANSITION = "VERIFIER_ISSUE_LIFECYCLE_TRANSITION"

_EVENT_SCHEMA_VERSION = "claim_verification_event_v1"


class ClaimVerificationRuntimeError(ValueError):
    """Base M6 Claim runtime failure."""


class ClaimIdentityCollision(ClaimVerificationRuntimeError):
    """One replay-stable Claim operation changed semantics."""


class ClaimCurrentnessError(ClaimVerificationRuntimeError):
    """Claim/Paragraph/source currentness cannot be proven."""


class ClaimCoverageError(ClaimVerificationRuntimeError):
    """Claim extraction and exhaustive coverage accounting disagree."""


class ClaimBindingError(ClaimVerificationRuntimeError):
    """BindingFacts are unauthorized, stale, or semantically inconsistent."""


class VerifierSupportVerdictAdmissionError(ClaimBindingError):
    """One admitted identity, but a VERIFIED verdict forbidden by shared Support.

    This is not a local-correction authorization or a canonical negative verdict.
    The immutable Candidate was already durably stored by its producer.
    """

    rule_code = "VERIFIER_VERIFIED_FORBIDDEN_BY_SHARED_SUPPORT"

    def __init__(self, *, binding, support, candidate) -> None:
        self.candidate = VerifierSemanticResultCandidateSchema.model_validate(candidate)
        self.claim_ref = self.candidate.claim_ref
        self.claim_occurrence_ref = self.candidate.claim_occurrence_ref
        self.active_lineage = self.candidate.active_lineage
        self.binding_facts_ref = binding.binding_facts_ref
        self.support_ref = support.support_ref
        self.support_relation = support.relation.value
        self.verifier_semantic_input_sha256 = self.candidate.verifier_semantic_input_sha256
        super().__init__("VERIFIED forbidden by unresolved/contradictory shared Support")


class VerifierCandidateContractViolationError(ClaimBindingError):
    """Only the unchanged Evidence positive-basis intersection predicate."""

    def __init__(self, violation: VerifierCandidateContractViolationV1) -> None:
        self.violation = VerifierCandidateContractViolationV1.model_validate(violation)
        self.rule_code = self.violation.rule_code
        super().__init__(
            "Evidence positive basis requires shared supporting refs with admissible Binding observations"
        )


class ClaimHeadConflict(ClaimVerificationRuntimeError):
    """Expected ClaimCurrentHead CAS failed."""


class VerifierIssueHeadConflict(ClaimVerificationRuntimeError):
    """Expected VerifierIssueHead CAS failed."""


class ClaimVerificationIntegrityError(ClaimVerificationRuntimeError):
    """Canonical Claim ledger is corrupt or contradictory."""


class ClaimOperationInProgress(ClaimVerificationRuntimeError):
    """Another executor owns the same canonical logical operation."""


BindingSourceValidator = Callable[
    [ClaimBindingFactsCandidateSchema, ActiveExecutionLineageSchema],
    None,
]
SupportResolver = Callable[
    [ClaimEvidenceSupportRefSchema],
    ClaimEvidenceSupportRecordSchema,
]
VerificationSourceValidator = Callable[
    [
        ClaimBindingFactsRecordSchema,
        ClaimEvidenceSupportRecordSchema,
        ActiveExecutionLineageSchema,
    ],
    None,
]


@dataclass(frozen=True)
class ClaimRecordCommitResult:
    records: tuple[ClaimRecordSchema, ...]
    coverage_accounting: ClaimCoverageAccountingSchema
    coverage_proof_ref: ClaimCoverageProofRefSchema
    receipt: ClaimRecordCommitReceiptSchema


@dataclass(frozen=True)
class ClaimVerificationCommitResult:
    verification_result: VerificationResultRecordSchema
    verification_revision: ClaimVerificationRevisionRecordSchema
    resulting_claim_head: ClaimCurrentHeadSchema
    issue_records: tuple[VerifierIssueRecordSchema, ...]
    issue_heads: tuple[VerifierIssueHeadSchema, ...]
    receipt: ClaimVerificationCommitReceiptSchema


@dataclass(frozen=True)
class _StoredOperation:
    operation_ref: LogicalOperationRef
    semantic_fingerprint: str
    result_ref: str
    event_type: str
    payload: dict


@dataclass
class _LedgerState:
    claims: dict[str, ClaimRecordSchema]
    coverage: dict[str, ClaimCoverageAccountingSchema]
    coverage_receipts: dict[str, ClaimRecordCommitReceiptSchema]
    bindings: dict[str, ClaimBindingFactsRecordSchema]
    verification_results: dict[str, VerificationResultRecordSchema]
    verification_revisions: dict[tuple[str, int], ClaimVerificationRevisionRecordSchema]
    claim_heads: dict[str, ClaimCurrentHeadSchema]
    issues: dict[str, VerifierIssueRecordSchema]
    issue_heads: dict[str, VerifierIssueHeadSchema]
    operations: dict[str, _StoredOperation]
    verifier_recovery_roots: dict[str, VerifierOriginalRootV1]
    verifier_recovery_scopes: dict[str, str]
    verifier_rejections: dict[str, tuple[VerifierCandidateRejectedV1, ...]]
    verifier_correction_grants: dict[str, VerifierCorrectionAuthorizedV1]
    verifier_correction_terminations: dict[str, VerifierCorrectionTerminatedV1]
    verifier_control_operations: dict[str, ClaimVerifierRecoveryControlEventV1]


def _result_ref(prefix: str, value: object) -> str:
    if hasattr(value, "model_dump"):
        payload = value.model_dump(mode="json")
    else:
        payload = value
    return f"{prefix}://sha256/{canonical_sha256(payload)}"


class ClaimVerificationRuntime:
    """Canonical Claim existence / coverage / binding owner."""

    _LOCKS_GUARD = threading.Lock()
    _LOCKS: dict[str, threading.RLock] = {}

    def __init__(
        self,
        *,
        paragraph_runtime: ParagraphRuntime,
        binding_source_validator: BindingSourceValidator,
        support_resolver: SupportResolver | None = None,
        verification_source_validator: VerificationSourceValidator | None = None,
        deterministic_validation_policy_version: str = (
            "claim-verification-deterministic-v2"
        ),
        ledger_path: str | Path = (
            "data/runtime/agent_rag_v1/claim_verification.jsonl"
        ),
        coordination_store: SideEffectCommitStore | None = None,
    ) -> None:
        if not callable(binding_source_validator):
            raise TypeError(
                "ClaimVerificationRuntime requires binding_source_validator"
            )
        policy_version = str(deterministic_validation_policy_version or "").strip()
        if not policy_version:
            raise TypeError("deterministic_validation_policy_version must be non-empty")
        self.paragraph_runtime = paragraph_runtime
        self.lifecycle_service = paragraph_runtime.lifecycle_service
        self.binding_source_validator = binding_source_validator
        self.support_resolver = support_resolver
        self.verification_source_validator = verification_source_validator
        # Optional production read adapters; original write/commit guards remain separate.
        self.read_lineage_resolver = None
        self.read_binding_source_validator = None
        self.read_verification_source_validator = None
        self.verification_identity_validator = None
        self.deterministic_validation_policy_version = policy_version
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
    # Generic SIF-01 / SIF-02 helpers
    # ------------------------------------------------------------------

    def validate_operation_context(
        self,
        context: ClaimOperationContextSchema,
    ) -> ActiveExecutionLineageSchema:
        """Public read/guard seam for downstream M6 owners."""

        return self._validate_operation_context(context)

    def _validate_operation_context(
        self,
        context: ClaimOperationContextSchema,
    ) -> ActiveExecutionLineageSchema:
        context = ClaimOperationContextSchema.model_validate(context)
        lineage = context.active_lineage
        current = self.lifecycle_service.assert_attempt_committable(
            run_id=lineage.run_id,
            attempt_scope_ref=lineage.attempt_scope_ref,
            attempt_id=lineage.attempt_id,
            input_snapshot_version=lineage.input_snapshot_version,
        )
        if current != lineage:
            raise ClaimCurrentnessError(
                "Claim operation context is not the exact active SIF-01 lineage"
            )
        return current

    @staticmethod
    def _operation_ref(
        *,
        operation_kind: str,
        operation_identity: object,
        context: ClaimOperationContextSchema,
    ) -> LogicalOperationRef:
        lineage = context.active_lineage
        return LogicalOperationRef(
            logical_operation_id=(
                "claim-domain-op:"
                + canonical_sha256(
                    {
                        "operation_kind": operation_kind,
                        "operation_identity": operation_identity,
                        "authorization_ref": context.authorization_ref,
                        "operation_purpose": context.operation_purpose,
                        "run_id": lineage.run_id,
                        "attempt_scope_ref": lineage.attempt_scope_ref,
                        "attempt_id": lineage.attempt_id,
                    }
                )
            ),
            operation_kind=operation_kind,
            run_id=lineage.run_id,
            operation_scope_ref=context.operation_purpose,
            attempt_scope_ref=lineage.attempt_scope_ref,
            attempt_id=lineage.attempt_id,
        )

    def _claim_side_effect(
        self,
        operation_ref: LogicalOperationRef,
        fingerprint: str,
    ) -> SideEffectClaim:
        try:
            claim = self.coordination_store.claim(
                operation_ref.logical_operation_id,
                execution_id=f"claim-verification-runtime:{threading.get_ident()}",
                description=operation_ref.operation_kind,
                semantic_fingerprint=fingerprint,
            )
        except SideEffectIdentityConflict as exc:
            raise ClaimIdentityCollision(
                "Claim SIF-02 coordination fingerprint collision"
            ) from exc
        if claim.status is SideEffectClaimStatus.ALREADY_COMMITTED:
            raise ClaimVerificationIntegrityError(
                "coordination says committed while canonical Claim result is absent"
            )
        if claim.status is SideEffectClaimStatus.ALREADY_IN_PROGRESS:
            raise ClaimOperationInProgress(
                "Claim logical operation is already in progress"
            )
        return claim

    def _ack_side_effect(
        self,
        claim: SideEffectClaim,
        result_ref: str,
    ) -> None:
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
                    raise ClaimVerificationIntegrityError(
                        "Claim coordination acknowledgement disagrees with canonical source"
                    )
        except SideEffectIdentityConflict as exc:
            raise ClaimIdentityCollision(
                "Claim SIF-02 acknowledgement collision"
            ) from exc

    def _reconcile_coordination(
        self,
        stored: _StoredOperation,
    ) -> None:
        try:
            resolution = self.coordination_store.resolve(
                stored.operation_ref.logical_operation_id,
                semantic_fingerprint=stored.semantic_fingerprint,
            )
        except SideEffectIdentityConflict as exc:
            raise ClaimIdentityCollision(
                "Claim coordination conflicts with canonical source"
            ) from exc
        if resolution.status is SideEffectCoordinationStatus.COMMITTED:
            if resolution.committed_result_refs != (stored.result_ref,):
                raise ClaimVerificationIntegrityError(
                    "Claim coordination result refs disagree with canonical source"
                )
            return
        if resolution.status is SideEffectCoordinationStatus.IN_PROGRESS:
            # Canonical source wins; never rerun a committed Domain mutation.
            return
        claim = self.coordination_store.claim(
            stored.operation_ref.logical_operation_id,
            execution_id="claim-verification-runtime:canonical-reconcile",
            description=f"reconcile:{stored.operation_ref.operation_kind}",
            semantic_fingerprint=stored.semantic_fingerprint,
        )
        if claim.status is SideEffectClaimStatus.ACQUIRED:
            self._ack_side_effect(claim, stored.result_ref)
            return
        if claim.status is SideEffectClaimStatus.ALREADY_COMMITTED:
            if claim.committed_result_refs != (stored.result_ref,):
                raise ClaimVerificationIntegrityError(
                    "reconciled Claim refs disagree with canonical source"
                )
            return
        raise ClaimOperationInProgress(
            "Claim coordination reconciliation remains in progress"
        )

    def resolve_committed_result(
        self,
        operation_ref: LogicalOperationRef,
        semantic_fingerprint: str,
    ) -> CommittedResultResolution:
        fingerprint = validate_sif02_semantic_fingerprint(semantic_fingerprint)
        with self._lock, InterProcessFileLock(self.lock_path):
            state = self._load_state_unlocked()
            stored = state.operations.get(operation_ref.logical_operation_id)
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

    def resolve_committed_verification(
        self, operation_ref: LogicalOperationRef, semantic_fingerprint: str,
        result_ref: VerificationResultRefSchema,
        revision_ref: ClaimVerificationRevisionRefSchema,
    ) -> ClaimVerificationCommitResult:
        """Read one exact native commit and revision without replay or ACK."""
        fingerprint = validate_sif02_semantic_fingerprint(semantic_fingerprint)
        result_ref = VerificationResultRefSchema.model_validate(result_ref)
        revision_ref = ClaimVerificationRevisionRefSchema.model_validate(revision_ref)
        with self._lock, InterProcessFileLock(self.lock_path):
            state = self._load_state_unlocked()
            stored = state.operations.get(operation_ref.logical_operation_id)
            if (stored is None or stored.event_type != "verification_committed"
                    or stored.operation_ref != operation_ref
                    or stored.semantic_fingerprint != fingerprint):
                raise ClaimVerificationIntegrityError("exact committed Verification operation does not resolve")
            committed = self._stored_verification_result(stored)
            if (committed.verification_result.verification_result_ref != result_ref
                    or committed.verification_revision.revision_ref != revision_ref
                    or stored.result_ref != _result_ref("claim-verification-commit", committed.receipt)):
                raise ClaimVerificationIntegrityError("committed Verification result/revision identity mismatch")
            return committed

    # ------------------------------------------------------------------
    # Canonical ledger
    # ------------------------------------------------------------------

    @staticmethod
    def _logical_operation_from_payload(raw: dict) -> LogicalOperationRef:
        try:
            return LogicalOperationRef(**raw)
        except Exception as exc:
            raise ClaimVerificationIntegrityError(
                "invalid committed Claim logical operation"
            ) from exc

    @diagnostic_operation("claim_verification.load", "FS_PARSE_SCOPE")
    def _load_state_unlocked(self) -> _LedgerState:
        state = _LedgerState(
            claims={},
            coverage={},
            coverage_receipts={},
            bindings={},
            verification_results={},
            verification_revisions={},
            claim_heads={},
            issues={},
            issue_heads={},
            operations={},
            verifier_recovery_roots={},
            verifier_recovery_scopes={},
            verifier_rejections={},
            verifier_correction_grants={},
            verifier_correction_terminations={},
            verifier_control_operations={},
        )
        with current_diagnostics().span("claim_verification.read", "FS_API_ELAPSED"):
            lines = self.log.read_complete_lines()
        for line_number, line in enumerate(lines, start=1):
            if not line.strip():
                continue
            try:
                event = json.loads(line)
            except json.JSONDecodeError as exc:
                raise ClaimVerificationIntegrityError(
                    f"invalid Claim ledger JSON at line {line_number}"
                ) from exc
            if isinstance(event, dict) and event.get("schema_version") == VERIFIER_RECOVERY_CONTROL_VERSION:
                try:
                    control = ClaimVerifierRecoveryControlEventV1.model_validate(event)
                    self._apply_verifier_control_unlocked(state, control)
                except ClaimVerificationIntegrityError:
                    raise
                except Exception as exc:
                    raise ClaimVerificationIntegrityError(
                        f"invalid Verifier recovery control at line {line_number}"
                    ) from exc
                continue
            if (
                not isinstance(event, dict)
                or event.get("schema_version") != _EVENT_SCHEMA_VERSION
            ):
                raise ClaimVerificationIntegrityError("unknown Claim ledger event")
            event_type = str(event.get("event_type") or "")
            operation_ref = self._logical_operation_from_payload(
                dict(event.get("operation_ref") or {})
            )
            fingerprint = validate_sif02_semantic_fingerprint(
                str(event.get("semantic_fingerprint") or "")
            )
            result_ref = str(event.get("result_ref") or "").strip()
            if not result_ref:
                raise ClaimVerificationIntegrityError(
                    "Claim ledger event has no result_ref"
                )
            if operation_ref.logical_operation_id in state.operations:
                raise ClaimVerificationIntegrityError(
                    "duplicate Claim logical operation in canonical ledger"
                )

            if event_type == "claim_records_committed":
                receipt = ClaimRecordCommitReceiptSchema.model_validate(
                    event.get("receipt")
                )
                accounting = ClaimCoverageAccountingSchema.model_validate(
                    event.get("coverage_accounting")
                )
                records = tuple(
                    ClaimRecordSchema.model_validate(item)
                    for item in list(event.get("claim_records") or [])
                )
                if receipt.committed_operation_id != operation_ref.logical_operation_id:
                    raise ClaimVerificationIntegrityError(
                        "ClaimRecord receipt operation mismatch"
                    )
                if receipt.semantic_fingerprint != fingerprint:
                    raise ClaimVerificationIntegrityError(
                        "ClaimRecord receipt fingerprint mismatch"
                    )
                if tuple(item.claim_ref for item in records) != receipt.claim_refs:
                    raise ClaimVerificationIntegrityError(
                        "ClaimRecord receipt refs do not match records"
                    )
                proof = receipt.coverage_proof_ref
                expected_result_ref = _result_ref("claim-record-commit", receipt)
                if expected_result_ref != result_ref:
                    raise ClaimVerificationIntegrityError(
                        "ClaimRecord result_ref mismatch"
                    )
                for record in records:
                    if canonical_sha256(record.normalized_value) != record.normalized_value_hash:
                        raise ClaimVerificationIntegrityError(
                            "canonical ClaimRecord normalized_value_hash mismatch"
                        )
                    if hashlib.sha256(record.text_span.text.encode("utf-8")).hexdigest() != record.text_span_hash:
                        raise ClaimVerificationIntegrityError(
                            "canonical ClaimRecord text_span_hash mismatch"
                        )
                    existing = state.claims.get(record.claim_ref.claim_id)
                    if existing is not None and existing != record:
                        raise ClaimVerificationIntegrityError(
                            "canonical ClaimRecord identity collision"
                        )
                    state.claims[record.claim_ref.claim_id] = record
                if proof.coverage_proof_id in state.coverage:
                    raise ClaimVerificationIntegrityError(
                        "duplicate Claim coverage proof identity"
                    )
                state.coverage[proof.coverage_proof_id] = accounting
                state.coverage_receipts[proof.coverage_proof_id] = receipt

            elif event_type == "binding_facts_committed":
                record = ClaimBindingFactsRecordSchema.model_validate(
                    event.get("record")
                )
                if record.committed_operation_id != operation_ref.logical_operation_id:
                    raise ClaimVerificationIntegrityError(
                        "BindingFacts committed operation mismatch"
                    )
                if record.binding_semantic_fingerprint != fingerprint:
                    raise ClaimVerificationIntegrityError(
                        "BindingFacts fingerprint mismatch"
                    )
                expected_result_ref = _result_ref(
                    "claim-binding-facts",
                    record.binding_facts_ref,
                )
                if expected_result_ref != result_ref:
                    raise ClaimVerificationIntegrityError(
                        "BindingFacts result_ref mismatch"
                    )
                key = record.binding_facts_ref.binding_facts_id
                existing = state.bindings.get(key)
                if existing is not None and existing != record:
                    raise ClaimVerificationIntegrityError(
                        "canonical BindingFacts identity collision"
                    )
                state.bindings[key] = record
            elif event_type == "verification_committed":
                raw_result = event.get("verification_result")
                if not isinstance(raw_result, dict) or raw_result.get("schema_version") != "verification_result_record_v2":
                    raise ClaimVerificationIntegrityError("active verification ledger requires Result V2; rebuild old/mixed/unknown state")
                receipt = ClaimVerificationCommitReceiptSchema.model_validate(
                    event.get("receipt")
                )
                verification = VerificationResultRecordSchema.model_validate(
                    event.get("verification_result")
                )
                revision = ClaimVerificationRevisionRecordSchema.model_validate(
                    event.get("verification_revision")
                )
                issues = tuple(
                    VerifierIssueRecordSchema.model_validate(item)
                    for item in list(event.get("issue_records") or [])
                )
                issue_heads = tuple(
                    VerifierIssueHeadSchema.model_validate(item)
                    for item in list(event.get("issue_heads") or [])
                )
                if receipt.committed_operation_id != operation_ref.logical_operation_id:
                    raise ClaimVerificationIntegrityError(
                        "Verification receipt operation mismatch"
                    )
                if receipt.semantic_fingerprint != fingerprint:
                    raise ClaimVerificationIntegrityError(
                        "Verification receipt fingerprint mismatch"
                    )
                if receipt.verification_result_ref != verification.verification_result_ref:
                    raise ClaimVerificationIntegrityError(
                        "Verification receipt/result ref mismatch"
                    )
                if receipt.verification_revision_ref != revision.revision_ref:
                    raise ClaimVerificationIntegrityError(
                        "Verification receipt/revision ref mismatch"
                    )
                if tuple(item.issue_ref for item in issues) != receipt.issue_refs:
                    raise ClaimVerificationIntegrityError(
                        "Verification receipt issue refs mismatch"
                    )
                if verification.issue_refs != receipt.issue_refs:
                    raise ClaimVerificationIntegrityError(
                        "VerificationResult issue refs mismatch"
                    )
                expected_result_ref = _result_ref(
                    "claim-verification-commit",
                    receipt,
                )
                if result_ref != expected_result_ref:
                    raise ClaimVerificationIntegrityError(
                        "Verification commit result_ref mismatch"
                    )
                claim_id = verification.claim_ref.claim_id
                claim_record = state.claims.get(claim_id)
                if claim_record is None or claim_record.claim_ref != verification.claim_ref:
                    raise ClaimVerificationIntegrityError(
                        "Verification references missing/different ClaimRecord"
                    )
                binding = state.bindings.get(
                    verification.binding_facts_ref.binding_facts_id
                )
                if binding is None or binding.binding_facts_ref != verification.binding_facts_ref:
                    raise ClaimVerificationIntegrityError(
                        "Verification references missing/different BindingFacts"
                    )
                if (
                    verification.claim_occurrence_ref
                    != claim_record.claim_occurrence_ref
                    or verification.paragraph_version_ref
                    != claim_record.paragraph_version_ref
                    or revision.claim_ref != verification.claim_ref
                    or revision.verification_result_ref
                    != verification.verification_result_ref
                    or revision.binding_facts_ref
                    != verification.binding_facts_ref
                    or revision.claim_evidence_support_ref
                    != verification.claim_evidence_support_ref
                ):
                    raise ClaimVerificationIntegrityError(
                        "Verification/Revision canonical subjects disagree"
                    )

                candidate = VerifierSemanticResultCandidateSchema.model_validate(event.get("verifier_candidate"))
                self._validate_candidate_recovery_control_unlocked(state, candidate)
                if (candidate.claim_ref != verification.claim_ref
                        or candidate.binding_facts_ref != verification.binding_facts_ref
                        or candidate.claim_evidence_support_ref != verification.claim_evidence_support_ref
                        or candidate.verifier_semantic_input_sha256 != verification.verifier_semantic_input_sha256
                        or self.verifier_semantic_fingerprint(candidate) != verification.verifier_semantic_result_fingerprint
                        or verification.binding_source_set_hash != binding.source_set_hash
                        or verification.verification_result_ref.verification_semantic_fingerprint != fingerprint):
                    raise ClaimVerificationIntegrityError("Result V2 candidate/binding identity mismatch")
                result_candidate = self.candidate_from_result(verification, binding, issues)
                # The optional logical grouping hint is carried by the exact candidate;
                # Issue V1 continues to persist its derived logical id.
                result_candidate = result_candidate.model_copy(update={"issue_candidates": tuple(
                    item.model_copy(update={"material_source_scope_key": original.material_source_scope_key})
                    for item in result_candidate.issue_candidates
                    for original in candidate.issue_candidates if original.issue_slot == item.issue_slot)})
                if self.verifier_semantic_fingerprint(result_candidate) != self.verifier_semantic_fingerprint(candidate):
                    raise ClaimVerificationIntegrityError("Result V2 differs from exact candidate semantics")
                if self.verification_commit_semantic_fingerprint(claim_record=claim_record,
                        binding_record=binding, support_record=verification.claim_evidence_support_ref,
                        verifier_candidate=candidate) != fingerprint:
                    raise ClaimVerificationIntegrityError("Result V2 commit semantic fingerprint mismatch")

                current_head = state.claim_heads.get(claim_id)
                resulting_head = receipt.resulting_claim_head
                if current_head is None:
                    if (
                        resulting_head.head_revision != 1
                        or resulting_head.expected_previous_head_revision is not None
                        or revision.verification_revision != 1
                        or revision.previous_verification_revision_ref is not None
                    ):
                        raise ClaimVerificationIntegrityError(
                            "initial Claim verification/head is not revision 1"
                        )
                else:
                    expected_revision = (
                        current_head.active_verification_revision_ref.verification_revision
                        + 1
                    )
                    if (
                        resulting_head.head_revision != current_head.head_revision + 1
                        or resulting_head.expected_previous_head_revision
                        != current_head.head_revision
                        or revision.verification_revision != expected_revision
                        or revision.previous_verification_revision_ref
                        != current_head.active_verification_revision_ref
                    ):
                        raise ClaimVerificationIntegrityError(
                            "Claim verification/head history is non-contiguous"
                        )
                if (
                    resulting_head.claim_ref != verification.claim_ref
                    or resulting_head.active_verification_revision_ref
                    != revision.revision_ref
                ):
                    raise ClaimVerificationIntegrityError(
                        "Claim head does not point to committed verification revision"
                    )

                result_id = verification.verification_result_ref.verification_result_id
                if result_id in state.verification_results:
                    raise ClaimVerificationIntegrityError(
                        "duplicate VerificationResult identity"
                    )
                revision_key = (
                    revision.claim_ref.claim_id,
                    revision.verification_revision,
                )
                if revision_key in state.verification_revisions:
                    raise ClaimVerificationIntegrityError(
                        "duplicate ClaimVerificationRevision identity"
                    )
                state.verification_results[result_id] = verification
                state.verification_revisions[revision_key] = revision
                state.claim_heads[claim_id] = resulting_head

                if len(issues) != len(issue_heads):
                    raise ClaimVerificationIntegrityError(
                        "VerifierIssue record/head cardinality mismatch"
                    )
                heads_by_id = {
                    item.issue_ref.issue_id: item for item in issue_heads
                }
                for issue in issues:
                    issue_id = issue.issue_ref.issue_id
                    issue_head = heads_by_id.get(issue_id)
                    if issue_head is None:
                        raise ClaimVerificationIntegrityError(
                            "VerifierIssue has no coherent initial Head"
                        )
                    if (
                        issue_head.issue_ref != issue.issue_ref
                        or issue_head.issue_revision != 1
                        or issue_head.lifecycle_status
                        is not VerifierIssueLifecycleStatus.OPEN
                    ):
                        raise ClaimVerificationIntegrityError(
                            "VerifierIssue initial Head must be revision 1 OPEN"
                        )
                    if issue_id in state.issues or issue_id in state.issue_heads:
                        raise ClaimVerificationIntegrityError(
                            "duplicate VerifierIssue identity"
                        )
                    state.issues[issue_id] = issue
                    state.issue_heads[issue_id] = issue_head

            elif event_type == "claim_lifecycle_transition":
                receipt = ClaimLifecycleTransitionReceiptSchema.model_validate(
                    event.get("receipt")
                )
                if receipt.committed_operation_id != operation_ref.logical_operation_id:
                    raise ClaimVerificationIntegrityError(
                        "Claim lifecycle receipt operation mismatch"
                    )
                if receipt.semantic_fingerprint != fingerprint:
                    raise ClaimVerificationIntegrityError(
                        "Claim lifecycle receipt fingerprint mismatch"
                    )
                if result_ref != _result_ref("claim-lifecycle-transition", receipt):
                    raise ClaimVerificationIntegrityError(
                        "Claim lifecycle result_ref mismatch"
                    )
                claim_id = receipt.prior_head.claim_ref.claim_id
                current_head = state.claim_heads.get(claim_id)
                if current_head != receipt.prior_head:
                    raise ClaimVerificationIntegrityError(
                        "Claim lifecycle transition prior head is not current history"
                    )
                resulting = receipt.resulting_head
                if (
                    resulting.claim_ref != receipt.prior_head.claim_ref
                    or resulting.active_verification_revision_ref
                    != receipt.prior_head.active_verification_revision_ref
                    or resulting.head_revision != receipt.prior_head.head_revision + 1
                    or resulting.expected_previous_head_revision
                    != receipt.prior_head.head_revision
                ):
                    raise ClaimVerificationIntegrityError(
                        "Claim lifecycle transition head is non-contiguous"
                    )
                state.claim_heads[claim_id] = resulting

            elif event_type == "issue_lifecycle_transition":
                receipt = (
                    VerifierIssueLifecycleTransitionReceiptSchema.model_validate(
                        event.get("receipt")
                    )
                )
                if receipt.committed_operation_id != operation_ref.logical_operation_id:
                    raise ClaimVerificationIntegrityError(
                        "Issue lifecycle receipt operation mismatch"
                    )
                if receipt.semantic_fingerprint != fingerprint:
                    raise ClaimVerificationIntegrityError(
                        "Issue lifecycle receipt fingerprint mismatch"
                    )
                if result_ref != _result_ref("verifier-issue-transition", receipt):
                    raise ClaimVerificationIntegrityError(
                        "Issue lifecycle result_ref mismatch"
                    )
                issue_id = receipt.prior_head.issue_ref.issue_id
                current_head = state.issue_heads.get(issue_id)
                if current_head != receipt.prior_head:
                    raise ClaimVerificationIntegrityError(
                        "Issue lifecycle transition prior head is not current history"
                    )
                resulting = receipt.resulting_head
                if (
                    resulting.issue_ref != receipt.prior_head.issue_ref
                    or resulting.issue_revision != receipt.prior_head.issue_revision + 1
                ):
                    raise ClaimVerificationIntegrityError(
                        "Issue lifecycle transition head is non-contiguous"
                    )
                state.issue_heads[issue_id] = resulting

            else:
                raise ClaimVerificationIntegrityError(
                    f"unsupported Claim ledger event_type={event_type}"
                )

            state.operations[operation_ref.logical_operation_id] = _StoredOperation(
                operation_ref=operation_ref,
                semantic_fingerprint=fingerprint,
                result_ref=result_ref,
                event_type=event_type,
                payload=event,
            )
        return state

    def _append_event(
        self,
        *,
        event_type: str,
        operation_ref: LogicalOperationRef,
        semantic_fingerprint: str,
        result_ref: str,
        payload: dict,
    ) -> None:
        event = {
            "schema_version": _EVENT_SCHEMA_VERSION,
            "event_type": event_type,
            "operation_ref": asdict(operation_ref),
            "semantic_fingerprint": semantic_fingerprint,
            "result_ref": result_ref,
            **payload,
        }
        self.log.append_serialized(
            json.dumps(
                event,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            )
        )

    # ------------------------------------------------------------------
    # Prospective local Verifier control. The journal, not Artifact/Budget or
    # an expiring coordination lease, is the permanent slot authority.
    # ------------------------------------------------------------------

    @staticmethod
    def verifier_input_from_candidate(candidate) -> VerifierClaimInputSchema:
        return VerifierClaimInputSchema.model_validate({
            key: getattr(candidate, key)
            for key in VerifierClaimInputSchema.model_fields
            if key != "schema_version"
        })

    def _verifier_artifact_identity(self, invocation) -> dict:
        return {
            "task_id": self.lifecycle_service.load_run_identity(
                invocation.active_lineage.run_id
            ).task_id,
            "run_id": invocation.active_lineage.run_id,
            "section_id": invocation.claim_occurrence_ref.section_id,
        }

    def _verifier_read_artifact(self, ref, invocation):
        try:
            reference, payload = self.paragraph_runtime.artifact_runtime.artifact_store.get_json_with_reference(
                ref, **self._verifier_artifact_identity(invocation)
            )
            tail = ref.rsplit("/", 1)[-1]
            expected = {
                "input": ("canonical_verifier_input", "canonical_verifier_input_v2"),
                "request": ("canonical_verifier_request", "canonical_verifier_request_v1"),
                "response": ("canonical_verifier_response", "canonical_verifier_response_v1"),
                "candidate": ("canonical_verifier_candidate", "canonical_verifier_candidate_v2"),
            }.get(tail)
            if expected is None or (reference.artifact_type, reference.schema_version) != expected:
                raise ClaimVerificationIntegrityError("Verifier recovery Artifact metadata differs from exact physical ref")
            return payload
        except Exception as exc:
            raise ClaimVerificationIntegrityError(
                "Verifier recovery exact immutable Artifact does not resolve"
            ) from exc

    def _validate_original_root_unlocked(self, state, root):
        root = VerifierOriginalRootV1.model_validate(root)
        invocation = root.original_input
        scope = build_verifier_registration_scope_sha256(invocation)
        prior_ref = state.verifier_recovery_scopes.get(scope)
        if prior_ref is not None and prior_ref != root.original_root_ref:
            raise ClaimVerificationIntegrityError("Verifier original registration scope has a different root")
        prior_root = state.verifier_recovery_roots.get(root.original_root_ref)
        if prior_root is not None:
            if prior_root != root:
                raise ClaimVerificationIntegrityError("Verifier original root changed immutable contents")
            return root
        claim = state.claims.get(invocation.claim_ref.claim_id)
        binding = state.bindings.get(invocation.binding_facts_ref.binding_facts_id)
        if (claim is None or claim.claim_ref != invocation.claim_ref
                or self.derive_claim_ref(claim.claim_occurrence_ref) != claim.claim_ref
                or claim.claim_occurrence_ref != invocation.claim_occurrence_ref
                or claim.paragraph_version_ref != invocation.paragraph_version_ref
                or binding is None or binding.binding_facts_ref != invocation.binding_facts_ref
                or binding.claim_ref != claim.claim_ref):
            raise ClaimVerificationIntegrityError("Verifier root does not bind exact canonical Claim/Binding")
        receipt = self._verifier_read_artifact(root.original_input_ref, invocation)
        if (not isinstance(receipt, dict)
                or receipt.get("schema_version") != "canonical_verifier_input_v2"
                or receipt.get("input_fingerprint") != invocation.verifier_semantic_input_sha256
                or receipt.get("input_material", {}).get("invocation") != invocation.model_dump(mode="json")):
            raise ClaimVerificationIntegrityError("Verifier root lacks its prospectively registered immutable initial intent")
        # Defense in depth for callers of M6 APIs: a caller cannot retrospectively
        # attach a policy to an older same-scope intent or manufacture a second
        # initial root by changing authorization/profile/feedback/random input.
        store = self.paragraph_runtime.artifact_runtime.artifact_store
        identity = self._verifier_artifact_identity(invocation)
        for reference in store.list_references():
            if not reference.artifact_ref.startswith("artifact://canonical-verifier-v2/") or not reference.artifact_ref.endswith("/input"):
                continue
            historical = store.get_json(reference.artifact_ref, task_id=reference.task_id,
                run_id=reference.run_id, section_id=reference.section_id)
            if not isinstance(historical, dict) or historical.get("schema_version") != "canonical_verifier_input_v2":
                raise ClaimVerificationIntegrityError("Verifier original registration encounters unknown historical intent")
            other = VerifierClaimInputSchema.model_validate(historical.get("input_material", {}).get("invocation"))
            if build_verifier_registration_scope_sha256(other) != scope:
                continue
            self._verifier_read_artifact(reference.artifact_ref, other)
            if VERIFIER_LOCAL_CORRECTION_KEY in other.deterministic_validation_facts:
                child = VerifierLocalCorrectionBindingV1.model_validate(
                    other.deterministic_validation_facts[VERIFIER_LOCAL_CORRECTION_KEY]
                )
                if child.authorization.root != root:
                    raise ClaimVerificationIntegrityError("Verifier same-scope child targets a different original root")
                continue
            if other != invocation or reference.artifact_ref != root.original_input_ref:
                raise ClaimVerificationIntegrityError("historical Verifier initial root cannot be replaced or registered retrospectively")
        return root

    def _verifier_rejected_artifacts_unlocked(self, rejected_input, candidate, chain):
        """Read exact production receipts and replay the original parser only."""
        from apps.enterprise_document.services.agent_rag_v1.claim_verifier_composition import (
            TargetClaimVerifierComposition,
        )
        from schemas.model import ModelRequestSchema, ModelResponseSchema
        from schemas.context import ContextPackageSchema

        invocation = VerifierClaimInputSchema.model_validate(rejected_input)
        candidate = VerifierSemanticResultCandidateSchema.model_validate(candidate)
        chain = VerifierRecoveryArtifactChainV1.model_validate(chain)
        input_receipt = self._verifier_read_artifact(chain.input_ref, invocation)
        request = self._verifier_read_artifact(chain.request_ref, invocation)
        response_receipt = self._verifier_read_artifact(chain.response_ref, invocation)
        candidate_receipt = self._verifier_read_artifact(chain.candidate_ref, invocation)
        if (input_receipt.get("schema_version") != "canonical_verifier_input_v2"
                or input_receipt.get("input_fingerprint") != invocation.verifier_semantic_input_sha256
                or input_receipt.get("input_material", {}).get("invocation") != invocation.model_dump(mode="json")
                or input_receipt.get("request_ref") != chain.request_ref
                or input_receipt.get("request") != request):
            raise ClaimVerificationIntegrityError("Verifier rejected input/request durable linkage mismatch")
        if (not isinstance(response_receipt, dict)
                or set(response_receipt) != {"schema_version", "input_fingerprint", "request_ref", "request_sha256", "response", "budget_settlement_confirmed"}
                or response_receipt.get("schema_version") != "canonical_verifier_response_v1"
                or response_receipt.get("input_fingerprint") != invocation.verifier_semantic_input_sha256
                or response_receipt.get("request_ref") != chain.request_ref
                or response_receipt.get("request_sha256") != canonical_sha256(request)
                or response_receipt.get("budget_settlement_confirmed") is not True):
            raise ClaimVerificationIntegrityError("Verifier rejection lacks complete response and confirmed canonical settlement")
        response = response_receipt["response"]
        parsed_request = ModelRequestSchema.model_validate(request)
        parsed_response = ModelResponseSchema.model_validate(response)
        package = ContextPackageSchema.model_validate(input_receipt.get("context_package"))
        key = chain.physical_occurrence.rsplit("/", 1)[-1]
        expected_summary = {"managed": True, "package_id": package.package_ref.context_package_id,
            "context_sha256": package.model_input.final_model_input_sha256}
        if (package.invocation_subject.invocation_id != chain.physical_occurrence
                or package.invocation_subject.logical_operation_id != chain.physical_occurrence
                or parsed_request.messages != package.model_input.final_input_material.inline_final_input_payload.get("messages")
                or parsed_request.max_tokens != package.budget_binding.reserved_output_tokens
                or parsed_request.extra.get("llm_context_summary") != expected_summary):
            raise ClaimVerificationIntegrityError("Verifier recovery request differs from exact recorded Context/response cap")
        if (not parsed_response.success or parsed_response.finish_reason == "length"
                or parsed_request.model_call_id != "model_call_verifier_" + key
                or (parsed_request.model_call_id, parsed_request.task_id, parsed_request.run_id)
                != (parsed_response.model_call_id, parsed_response.task_id, parsed_response.run_id)
                or parsed_request.extra.get("logical_operation_id") != chain.physical_occurrence
                or parsed_request.extra.get("attempt_id") != invocation.active_lineage.attempt_id
                or parsed_request.extra.get("attempt_scope_ref") != invocation.active_lineage.attempt_scope_ref
                or parsed_request.extra.get("input_snapshot_version") != invocation.active_lineage.input_snapshot_version
                or parsed_request.run_id != invocation.active_lineage.run_id
                or parsed_request.task_id != self._verifier_artifact_identity(invocation)["task_id"]):
            raise ClaimVerificationIntegrityError("Verifier rejected Provider identity/completion differs from exact intent")
        if (candidate_receipt.get("schema_version") != "canonical_verifier_candidate_v2"
                or candidate_receipt.get("outcome") != "completed"
                or candidate_receipt.get("input_fingerprint") != invocation.verifier_semantic_input_sha256
                or candidate_receipt.get("candidate") != candidate.model_dump(mode="json")
                or chain.input_sha256 != canonical_sha256(invocation.model_dump(mode="json"))
                or chain.request_sha256 != canonical_sha256(request)
                or chain.response_sha256 != canonical_sha256(response)
                or chain.candidate_sha256 != canonical_sha256(candidate.model_dump(mode="json"))):
            raise ClaimVerificationIntegrityError("Verifier rejected durable parent/candidate hash mismatch")
        normalized = TargetClaimVerifierComposition._recorded_reply_entry(candidate_receipt, invocation=invocation)
        if normalized is not None:
            if candidate_receipt["reply_entry"]["raw_reply"] != parsed_response.content:
                raise ClaimVerificationIntegrityError("Verifier rejected candidate raw reply differs from complete response")
        else:
            normalized = json.loads(parsed_response.content)
        reparsed, semantics = TargetClaimVerifierComposition._parse_candidate_strict(
            normalized, invocation=invocation, occurrence=chain.physical_occurrence,
            receipt_ref=chain.candidate_ref,
        )
        if reparsed != candidate or semantics != candidate_receipt.get("semantic_result"):
            raise ClaimVerificationIntegrityError("Verifier rejected candidate does not replay its immutable original reply")
        return input_receipt, request, response

    def _verifier_exact_records_unlocked(self, state, invocation):
        claim = state.claims.get(invocation.claim_ref.claim_id)
        binding = state.bindings.get(invocation.binding_facts_ref.binding_facts_id)
        if (claim is None or claim.claim_ref != invocation.claim_ref
                or binding is None or binding.binding_facts_ref != invocation.binding_facts_ref):
            raise ClaimVerificationIntegrityError("Verifier recovery exact Claim/Binding do not resolve")
        support = self._require_support_record(invocation.claim_evidence_support_ref)
        return claim, binding, support

    def _verifier_control_head_absent_unlocked(self, state, root):
        if state.claim_heads.get(root.original_input.claim_ref.claim_id) is not None:
            raise ClaimHeadConflict("Verifier local correction requires no formal Result/head")

    def _validate_correction_grant_unlocked(self, state, material, *, require_head_absent=True):
        facts = material.deterministic_validation_facts
        raw_binding = facts.get(VERIFIER_LOCAL_CORRECTION_KEY)
        if raw_binding is None:
            raise ClaimBindingError("Verifier child lacks its exact M6 correction binding")
        binding = VerifierLocalCorrectionBindingV1.model_validate(raw_binding)
        grant = binding.authorization
        root_ref = grant.root.original_root_ref
        if state.verifier_correction_terminations.get(root_ref) is not None:
            raise ClaimBindingError("Verifier correction is TERMINATED")
        if (state.verifier_recovery_roots.get(root_ref) != grant.root
                or state.verifier_correction_grants.get(root_ref) != grant):
            raise ClaimBindingError("Verifier child grant does not resolve to the exact permanent M6 slot1")
        if build_verifier_registration_scope_sha256(material) != build_verifier_registration_scope_sha256(grant.root.original_input):
            raise ClaimBindingError("Verifier child changes original root scope")
        if require_head_absent:
            self._verifier_control_head_absent_unlocked(state, grant.root)
        return grant

    def _validate_candidate_recovery_control_unlocked(self, state, material):
        """Never call public M6 resolvers from the M6 owner critical section."""
        scope = build_verifier_registration_scope_sha256(material)
        root_ref = state.verifier_recovery_scopes.get(scope)
        if VERIFIER_LOCAL_CORRECTION_KEY in material.deterministic_validation_facts:
            return self._validate_correction_grant_unlocked(state, material)
        if root_ref is not None:
            if root_ref in state.verifier_correction_terminations:
                raise ClaimBindingError("Verifier original root is TERMINATED")
            root = state.verifier_recovery_roots[root_ref]
            if self.verifier_input_from_candidate(material) != root.original_input:
                raise ClaimBindingError("Verifier initial root cannot change registration/auth/profile")
            if state.verifier_rejections.get(root_ref):
                raise ClaimBindingError("rejected original Verifier candidate cannot be repaired or recommitted; exact child grant required")
        return None

    def _apply_verifier_control_unlocked(self, state, event):
        payload = event.payload
        root = self._validate_original_root_unlocked(state, payload.root)
        root_ref = root.original_root_ref
        if event.operation_id in state.verifier_control_operations:
            raise ClaimVerificationIntegrityError("duplicate Verifier local control operation in canonical journal")
        if root_ref in state.verifier_correction_terminations:
            raise ClaimVerificationIntegrityError("Verifier control event follows TERMINATED")
        if state.claim_heads.get(root.original_input.claim_ref.claim_id) is not None:
            raise ClaimVerificationIntegrityError("Verifier local control follows a formal Result/head")
        if isinstance(payload, VerifierCandidateRejectedV1):
            self._verifier_rejected_artifacts_unlocked(payload.rejected_input, payload.candidate, payload.artifact_chain)
            claim, binding, support = self._verifier_exact_records_unlocked(state, payload.rejected_input)
            try:
                self.validate_v2_provenance(binding, support, payload.candidate)
            except VerifierCandidateContractViolationError as exc:
                if exc.violation != payload.violation:
                    raise ClaimVerificationIntegrityError("Verifier rejection differs from actual unchanged provenance guard")
            else:
                raise ClaimVerificationIntegrityError("legal Verifier candidate cannot be a recoverable rejection")
            prior = state.verifier_rejections.get(root_ref, ())
            if any(item.correction_ordinal == payload.correction_ordinal for item in prior):
                raise ClaimVerificationIntegrityError("duplicate Verifier rejection ordinal")
            if payload.correction_ordinal == 1:
                self._validate_correction_grant_unlocked(state, payload.rejected_input)
            elif prior or root_ref in state.verifier_correction_grants:
                raise ClaimVerificationIntegrityError("original Verifier rejection follows a correction grant")
            state.verifier_rejections[root_ref] = (*prior, payload)
        elif isinstance(payload, VerifierCorrectionAuthorizedV1):
            if root_ref in state.verifier_correction_grants:
                raise ClaimVerificationIntegrityError("duplicate permanent Verifier correction slot1")
            rejections = state.verifier_rejections.get(root_ref, ())
            rejection = next((item for item in rejections if item.correction_ordinal == 0), None)
            if (rejection is None or rejection.rejection_ref != payload.rejection_ref
                    or rejection.rejection_sha256 != payload.rejection_sha256):
                raise ClaimVerificationIntegrityError("Verifier correction grant lacks its exact original rejection")
            input_receipt, request, _response = self._verifier_rejected_artifacts_unlocked(
                rejection.rejected_input, rejection.candidate, rejection.artifact_chain
            )
            feedback = build_verifier_recovery_feedback(rejection.violation, input_receipt["input_material"], request["messages"])
            if feedback != payload.feedback:
                raise ClaimVerificationIntegrityError("Verifier grant feedback differs from deterministic complete original materials")
            state.verifier_correction_grants[root_ref] = payload
        else:
            grant = state.verifier_correction_grants.get(root_ref)
            if ((grant is None and payload.authorization_event_ref is not None)
                    or (grant is not None and (payload.authorization_event_ref != grant.authorization_event_ref
                        or payload.grant_sha256 != grant.grant_sha256))):
                raise ClaimVerificationIntegrityError("Verifier termination targets a different/missing grant")
            if payload.second_rejection_ref is not None:
                second = next((item for item in state.verifier_rejections.get(root_ref, ())
                    if item.correction_ordinal == 1), None)
                if (second is None or second.rejection_ref != payload.second_rejection_ref
                        or second.rejection_sha256 != payload.second_rejection_sha256):
                    raise ClaimVerificationIntegrityError("Verifier termination second rejection does not resolve")
            state.verifier_correction_terminations[root_ref] = payload
        state.verifier_recovery_roots[root_ref] = root
        state.verifier_recovery_scopes[build_verifier_registration_scope_sha256(root.original_input)] = root_ref
        state.verifier_control_operations[event.operation_id] = event

    def _append_verifier_control_unlocked(self, state, payload):
        event = build_verifier_recovery_control_event(payload)
        self._apply_verifier_control_unlocked(state, event)
        self.log.append_serialized(json.dumps(event.model_dump(mode="json"), ensure_ascii=False, sort_keys=True, separators=(",", ":")))
        return payload

    def _validate_verifier_recovery_context(self, invocation, context):
        context = ClaimOperationContextSchema.model_validate(context)
        if (invocation.verification_authorization_ref != context.authorization_ref
                or any(getattr(invocation.active_lineage, key) != getattr(context.active_lineage, key)
                    for key in ("run_id", "attempt_id", "attempt_scope_ref", "input_snapshot_version"))):
            raise ClaimBindingError("Verifier recovery context changes original authorization/scope")
        return self._validate_operation_context(context)

    def _eligible_verifier_violation_unlocked(self, state, candidate, context):
        invocation = self.verifier_input_from_candidate(candidate)
        lineage = self._validate_verifier_recovery_context(invocation, context)
        claim, binding, support = self._verifier_exact_records_unlocked(state, invocation)
        try:
            self._validate_verification_currentness(claim_record=claim, binding_record=binding,
                support_record=support, verifier_candidate=candidate, lineage=lineage)
        except VerifierCandidateContractViolationError as exc:
            return exc.violation
        raise ClaimBindingError("legal Verifier candidate is not eligible for local correction")

    def record_verifier_candidate_rejection(self, root, rejected_input, candidate, artifact_chain, operation_context):
        root = VerifierOriginalRootV1.model_validate(root)
        invocation = VerifierClaimInputSchema.model_validate(rejected_input)
        candidate = VerifierSemanticResultCandidateSchema.model_validate(candidate)
        chain = VerifierRecoveryArtifactChainV1.model_validate(artifact_chain)
        with self._lock, InterProcessFileLock(self.lock_path):
            state = self._load_state_unlocked()
            self._validate_original_root_unlocked(state, root)
            if root.original_root_ref in state.verifier_correction_terminations:
                raise ClaimBindingError("Verifier original root is TERMINATED")
            self._verifier_control_head_absent_unlocked(state, root)
            ordinal = 1 if VERIFIER_LOCAL_CORRECTION_KEY in invocation.deterministic_validation_facts else 0
            if ordinal:
                self._validate_correction_grant_unlocked(state, invocation)
            input_receipt, request, response = self._verifier_rejected_artifacts_unlocked(invocation, candidate, chain)
            violation = self._eligible_verifier_violation_unlocked(state, candidate, operation_context)
            rejection = build_verifier_candidate_rejection(root=root, correction_ordinal=ordinal,
                rejected_input=invocation, candidate=candidate, request=request, response=response,
                artifact_chain=chain, violation=violation)
            event = build_verifier_recovery_control_event(rejection)
            prior = state.verifier_control_operations.get(event.operation_id)
            if prior is not None:
                if prior != event:
                    raise ClaimIdentityCollision("same Verifier rejection operation changed immutable semantics")
                return prior.payload
            self._validate_verifier_recovery_context(invocation, operation_context)
            return self._append_verifier_control_unlocked(state, rejection)

    def authorize_verifier_correction(self, rejection, operation_context):
        rejection = VerifierCandidateRejectedV1.model_validate(rejection)
        root = rejection.root
        with self._lock, InterProcessFileLock(self.lock_path):
            state = self._load_state_unlocked()
            self._validate_original_root_unlocked(state, root)
            if root.original_root_ref in state.verifier_correction_terminations:
                raise ClaimBindingError("Verifier correction is TERMINATED")
            self._verifier_control_head_absent_unlocked(state, root)
            if rejection.correction_ordinal != 0 or rejection not in state.verifier_rejections.get(root.original_root_ref, ()):
                raise ClaimBindingError("Verifier correction requires the exact durable original rejection")
            input_receipt, request, _response = self._verifier_rejected_artifacts_unlocked(
                rejection.rejected_input, rejection.candidate, rejection.artifact_chain)
            violation = self._eligible_verifier_violation_unlocked(state, rejection.candidate, operation_context)
            if violation != rejection.violation:
                raise ClaimBindingError("Verifier original typed violation changed before authorization")
            feedback = build_verifier_recovery_feedback(violation, input_receipt["input_material"], request["messages"])
            grant = build_verifier_correction_authorization(root=root, rejection_ref=rejection.rejection_ref,
                rejection_sha256=rejection.rejection_sha256, feedback=feedback)
            prior = state.verifier_correction_grants.get(root.original_root_ref)
            if prior is not None:
                if prior != grant:
                    raise ClaimIdentityCollision("same permanent Verifier slot1 changed authorization/feedback")
                return prior
            # The native owner currentness path and unchanged typed guard run
            # again immediately before the single authorization append.
            self._eligible_verifier_violation_unlocked(state, rejection.candidate, operation_context)
            return self._append_verifier_control_unlocked(state, grant)

    def validate_verifier_correction_dispatch(self, child_input, operation_context):
        invocation = VerifierClaimInputSchema.model_validate(child_input)
        with self._lock, InterProcessFileLock(self.lock_path):
            state = self._load_state_unlocked()
            grant = self._validate_correction_grant_unlocked(state, invocation)
            lineage = self._validate_verifier_recovery_context(invocation, operation_context)
            claim, binding, support = self._verifier_exact_records_unlocked(state, invocation)
            self._validate_verification_currentness(claim_record=claim, binding_record=binding,
                support_record=support, verifier_candidate=invocation, lineage=lineage,
                validate_provenance=False)
            return grant

    def terminate_verifier_correction(self, root, reason_code, operation_context, rejection=None, reason_ref=None):
        root = VerifierOriginalRootV1.model_validate(root)
        with self._lock, InterProcessFileLock(self.lock_path):
            state = self._load_state_unlocked()
            self._validate_original_root_unlocked(state, root)
            grant = state.verifier_correction_grants.get(root.original_root_ref)
            second = VerifierCandidateRejectedV1.model_validate(rejection) if rejection is not None else None
            termination = build_verifier_correction_termination(root=root, reason_code=reason_code,
                reason_ref=reason_ref or "verifier-local-control:" + reason_code,
                authorization_event_ref=grant.authorization_event_ref if grant else None,
                grant_sha256=grant.grant_sha256 if grant else None,
                second_rejection_ref=second.rejection_ref if second else None,
                second_rejection_sha256=second.rejection_sha256 if second else None)
            prior = state.verifier_correction_terminations.get(root.original_root_ref)
            if prior is not None:
                if prior != termination:
                    raise ClaimIdentityCollision("same permanent Verifier termination changed semantics")
                return prior
            self._verifier_control_head_absent_unlocked(state, root)
            # Stale external sources are precisely why termination may be
            # recorded. The ordinary lifecycle write fence remains RUNNING-only.
            self._validate_verifier_recovery_context(root.original_input, operation_context)
            self._validate_verifier_recovery_context(root.original_input, operation_context)
            return self._append_verifier_control_unlocked(state, termination)

    def resolve_verifier_local_recovery(self, root):
        with self._lock, InterProcessFileLock(self.lock_path):
            state = self._load_state_unlocked()
            if isinstance(root, str):
                root_ref = root
                resolved = state.verifier_recovery_roots.get(root_ref)
            else:
                resolved = self._validate_original_root_unlocked(state, root)
                root_ref = resolved.original_root_ref
            return {"root": resolved, "rejections": state.verifier_rejections.get(root_ref, ()),
                "authorization": state.verifier_correction_grants.get(root_ref),
                "termination": state.verifier_correction_terminations.get(root_ref)}

    # ------------------------------------------------------------------
    # W7.1 Claim existence + lossless coverage
    # ------------------------------------------------------------------

    @staticmethod
    def derive_claim_occurrence_ref(
        *,
        run_id: str,
        section_id: str,
        paragraph_version_ref,
        claim_type: str,
        start_offset: int,
        end_offset: int,
        text_span_hash: str,
        span_locator_policy_id: str,
        span_locator_policy_version: str,
        normalized_value_hash: str,
        normalization_policy_id: str,
        normalization_policy_version: str,
        claim_identity_policy_id: str,
        claim_identity_policy_version: str,
    ) -> ClaimOccurrenceRefSchema:
        return ClaimOccurrenceRefSchema(
            run_id=run_id,
            section_id=section_id,
            paragraph_version_ref=paragraph_version_ref,
            claim_type=claim_type,
            start_offset=start_offset,
            end_offset=end_offset,
            text_span_hash=text_span_hash,
            span_locator_policy_id=span_locator_policy_id,
            span_locator_policy_version=span_locator_policy_version,
            normalized_value_hash=normalized_value_hash,
            normalization_policy_id=normalization_policy_id,
            normalization_policy_version=normalization_policy_version,
            claim_identity_policy_id=claim_identity_policy_id,
            claim_identity_policy_version=claim_identity_policy_version,
        )

    @staticmethod
    def derive_claim_ref(
        occurrence: ClaimOccurrenceRefSchema,
    ) -> ClaimRefSchema:
        occurrence = ClaimOccurrenceRefSchema.model_validate(occurrence)
        return ClaimRefSchema(
            claim_id=(
                "claim://sha256/"
                + canonical_sha256(occurrence.model_dump(mode="json"))
            ),
            claim_occurrence_ref=occurrence,
        )

    @staticmethod
    def _candidate_semantic_payload(candidate: ClaimCandidateSchema) -> dict:
        return {
            "claim_ref": ClaimVerificationRuntime.derive_claim_ref(
                candidate.claim_occurrence_ref
            ).model_dump(mode="json"),
            "claim_occurrence_ref": candidate.claim_occurrence_ref.model_dump(
                mode="json"
            ),
            "paragraph_version_ref": candidate.paragraph_version_ref.model_dump(
                mode="json"
            ),
            "section_id": candidate.section_id,
            "claim_type": candidate.claim_type,
            "text_span": candidate.text_span.model_dump(mode="json"),
            "text_span_hash": candidate.text_span_hash,
            "normalized_value": candidate.normalized_value,
            "normalized_value_hash": candidate.normalized_value_hash,
            "unit": candidate.unit,
        }

    def _resolve_exact_current_paragraph(
        self,
        *,
        paragraph_version_ref,
        section_id: str,
        lineage: ActiveExecutionLineageSchema,
    ):
        if lineage.attempt_scope_ref != f"section:{section_id}":
            raise ClaimCurrentnessError(
                "Claim operation Attempt scope does not target Claim Section"
            )
        manifest_ref = self.paragraph_runtime.manifest_ref(
            run_id=lineage.run_id,
            section_id=section_id,
            active_lineage=lineage,
            write_instance_id=lineage.attempt_id,
        )
        binding = self.paragraph_runtime.resolve_paragraph_binding(
            logical_ref=paragraph_version_ref.logical_ref,
            manifest_ref=manifest_ref,
        )
        if binding is None or binding.version_ref != paragraph_version_ref:
            raise ClaimCurrentnessError(
                "Claim Paragraph version is not exact current Section membership"
            )
        try:
            content = self.paragraph_runtime.validate_paragraph_binding_current(
                binding
            )
        except ParagraphCurrentnessError as exc:
            raise ClaimCurrentnessError(str(exc)) from exc
        return binding, content

    def _validate_claim_candidate(
        self,
        candidate: ClaimCandidateSchema,
        lineage: ActiveExecutionLineageSchema,
    ) -> ClaimCandidateSchema:
        candidate = ClaimCandidateSchema.model_validate(candidate)
        occurrence = candidate.claim_occurrence_ref
        if occurrence.run_id != lineage.run_id:
            raise ClaimCurrentnessError("Claim candidate belongs to another Run")
        _binding, paragraph = self._resolve_exact_current_paragraph(
            paragraph_version_ref=candidate.paragraph_version_ref,
            section_id=candidate.section_id,
            lineage=lineage,
        )
        if candidate.text_span.end_offset > len(paragraph.text):
            raise ClaimIdentityCollision("Claim text span escapes canonical Paragraph")
        exact_text = paragraph.text[
            candidate.text_span.start_offset : candidate.text_span.end_offset
        ]
        if exact_text != candidate.text_span.text:
            raise ClaimIdentityCollision(
                "Claim text span does not match canonical Paragraph bytes"
            )
        expected_span_hash = hashlib.sha256(
            candidate.text_span.text.encode("utf-8")
        ).hexdigest()
        if candidate.text_span_hash != expected_span_hash:
            raise ClaimIdentityCollision("Claim text_span_hash mismatch")
        expected_value_hash = canonical_sha256(candidate.normalized_value)
        if candidate.normalized_value_hash != expected_value_hash:
            raise ClaimIdentityCollision("Claim normalized_value_hash mismatch")

        expected_occurrence = self.derive_claim_occurrence_ref(
            run_id=lineage.run_id,
            section_id=candidate.section_id,
            paragraph_version_ref=candidate.paragraph_version_ref,
            claim_type=candidate.claim_type,
            start_offset=candidate.text_span.start_offset,
            end_offset=candidate.text_span.end_offset,
            text_span_hash=candidate.text_span_hash,
            span_locator_policy_id=occurrence.span_locator_policy_id,
            span_locator_policy_version=occurrence.span_locator_policy_version,
            normalized_value_hash=candidate.normalized_value_hash,
            normalization_policy_id=occurrence.normalization_policy_id,
            normalization_policy_version=occurrence.normalization_policy_version,
            claim_identity_policy_id=occurrence.claim_identity_policy_id,
            claim_identity_policy_version=occurrence.claim_identity_policy_version,
        )
        if expected_occurrence != occurrence:
            raise ClaimIdentityCollision(
                "Claim occurrence is not the deterministic candidate occurrence"
            )
        return candidate

    def _validate_coverage(
        self,
        *,
        candidates: tuple[ClaimCandidateSchema, ...],
        accounting: ClaimCoverageAccountingSchema,
        lineage: ActiveExecutionLineageSchema,
    ) -> None:
        accounting = ClaimCoverageAccountingSchema.model_validate(accounting)
        if any(
            item.extraction_occurrence_ref != accounting.extraction_occurrence_ref
            for item in candidates
        ):
            raise ClaimCoverageError(
                "Claim candidates and coverage use different extraction occurrence"
            )
        paragraph_refs = set(accounting.paragraph_version_refs)
        if {item.paragraph_version_ref for item in candidates} - paragraph_refs:
            raise ClaimCoverageError(
                "Claim candidate Paragraph falls outside coverage scope"
            )

        units_by_paragraph: dict[str, list] = {}
        for unit in accounting.coverage_units:
            units_by_paragraph.setdefault(
                unit.paragraph_version_ref.artifact_version_id,
                [],
            ).append(unit)

        for paragraph_ref in accounting.paragraph_version_refs:
            _binding, content = self._resolve_exact_current_paragraph(
                paragraph_version_ref=paragraph_ref,
                section_id=paragraph_ref.logical_ref.domain_scope_ref.removeprefix(
                    "section:"
                ),
                lineage=lineage,
            )
            units = sorted(
                units_by_paragraph.get(paragraph_ref.artifact_version_id, []),
                key=lambda item: (item.start_offset, item.end_offset),
            )
            if not units:
                raise ClaimCoverageError("coverage has no unit for Paragraph")
            if units[0].start_offset != 0:
                raise ClaimCoverageError("coverage does not begin at Paragraph offset 0")
            if units[-1].end_offset != len(content.text):
                raise ClaimCoverageError(
                    "coverage does not reach exact Paragraph end"
                )
            for left, right in zip(units, units[1:]):
                if left.end_offset != right.start_offset:
                    raise ClaimCoverageError(
                        "coverage partition is not lossless/contiguous"
                    )

        candidate_occurrences = {
            canonical_sha256(item.claim_occurrence_ref.model_dump(mode="json"))
            for item in candidates
        }
        covered_occurrences = {
            canonical_sha256(occurrence.model_dump(mode="json"))
            for unit in accounting.coverage_units
            for occurrence in unit.claim_occurrence_refs
        }
        if candidate_occurrences != covered_occurrences:
            raise ClaimCoverageError(
                "coverage Claim occurrence set differs from ClaimCandidate set"
            )
        if (
            not candidates
            and accounting.accounting_state
            is ClaimCoverageAccountingState.FACTUAL_CLAIMS_MATERIALIZED
        ):
            raise ClaimCoverageError(
                "zero Claim candidates cannot prove factual claims materialized"
            )

    def _claim_record_operation(
        self,
        *,
        candidates: tuple[ClaimCandidateSchema, ...],
        accounting: ClaimCoverageAccountingSchema,
        context: ClaimOperationContextSchema,
    ) -> LogicalOperationRef:
        identity = {
            "coverage_scope_ref": accounting.coverage_scope_ref,
            "paragraph_version_refs": [
                item.model_dump(mode="json")
                for item in accounting.paragraph_version_refs
            ],
            "extraction_occurrence_ref": accounting.extraction_occurrence_ref,
            "coverage_semantic_contract_version": (
                accounting.coverage_semantic_contract_version
            ),
            "claim_occurrences": sorted(
                canonical_sha256(
                    item.claim_occurrence_ref.model_dump(mode="json")
                )
                for item in candidates
            ),
            "identity_policies": sorted(
                {
                    (
                        item.claim_occurrence_ref.claim_identity_policy_id,
                        item.claim_occurrence_ref.claim_identity_policy_version,
                        item.claim_occurrence_ref.normalization_policy_id,
                        item.claim_occurrence_ref.normalization_policy_version,
                        item.claim_occurrence_ref.span_locator_policy_id,
                        item.claim_occurrence_ref.span_locator_policy_version,
                    )
                    for item in candidates
                }
            ),
        }
        return self._operation_ref(
            operation_kind=CLAIM_RECORD_COMMIT,
            operation_identity=identity,
            context=context,
        )

    @staticmethod
    def claim_record_semantic_fingerprint(
        *,
        candidates: Iterable[ClaimCandidateSchema],
        accounting: ClaimCoverageAccountingSchema,
    ) -> str:
        candidate_tuple = tuple(candidates)
        return canonical_sha256(
            {
                "contract": "claim_record_commit_semantics_v1",
                "claims": sorted(
                    (
                        ClaimVerificationRuntime._candidate_semantic_payload(item)
                        for item in candidate_tuple
                    ),
                    key=lambda item: item["claim_ref"]["claim_id"],
                ),
                "coverage_accounting": accounting.model_dump(mode="json"),
            }
        )

    def commit_claim_records(
        self,
        claim_candidates: Iterable[ClaimCandidateSchema],
        coverage_accounting_candidate: ClaimCoverageAccountingSchema,
        operation_context: ClaimOperationContextSchema,
    ) -> ClaimRecordCommitResult:
        context = ClaimOperationContextSchema.model_validate(operation_context)
        candidates = tuple(
            ClaimCandidateSchema.model_validate(item)
            for item in claim_candidates
        )
        claim_ids = [
            self.derive_claim_ref(item.claim_occurrence_ref).claim_id
            for item in candidates
        ]
        if len(claim_ids) != len(set(claim_ids)):
            raise ClaimIdentityCollision(
                "ClaimCandidate batch contains duplicate canonical Claim identity"
            )
        accounting = ClaimCoverageAccountingSchema.model_validate(
            coverage_accounting_candidate
        )
        operation_ref = self._claim_record_operation(
            candidates=candidates,
            accounting=accounting,
            context=context,
        )
        fingerprint = self.claim_record_semantic_fingerprint(
            candidates=candidates,
            accounting=accounting,
        )

        with self._lock, InterProcessFileLock(self.lock_path):
            state = self._load_state_unlocked()
            existing = state.operations.get(operation_ref.logical_operation_id)
            if existing is not None:
                if existing.semantic_fingerprint != fingerprint:
                    raise ClaimIdentityCollision(
                        "same CLAIM_RECORD_COMMIT changed semantics"
                    )
                self._reconcile_coordination(existing)
                receipt = ClaimRecordCommitReceiptSchema.model_validate(
                    existing.payload["receipt"]
                )
                records = tuple(
                    ClaimRecordSchema.model_validate(item)
                    for item in existing.payload["claim_records"]
                )
                return ClaimRecordCommitResult(
                    records=records,
                    coverage_accounting=ClaimCoverageAccountingSchema.model_validate(
                        existing.payload["coverage_accounting"]
                    ),
                    coverage_proof_ref=receipt.coverage_proof_ref,
                    receipt=receipt,
                )

            lineage = self._validate_operation_context(context)
            candidates = tuple(
                self._validate_claim_candidate(item, lineage)
                for item in candidates
            )
            self._validate_coverage(
                candidates=candidates,
                accounting=accounting,
                lineage=lineage,
            )
            records = tuple(
                ClaimRecordSchema(
                    claim_ref=self.derive_claim_ref(item.claim_occurrence_ref),
                    claim_occurrence_ref=item.claim_occurrence_ref,
                    paragraph_version_ref=item.paragraph_version_ref,
                    section_id=item.section_id,
                    claim_type=item.claim_type,
                    text_span=item.text_span,
                    text_span_hash=item.text_span_hash,
                    normalized_value=item.normalized_value,
                    normalized_value_hash=item.normalized_value_hash,
                    unit=item.unit,
                    extraction_occurrence_ref=item.extraction_occurrence_ref,
                    claim_record_commit_ref=operation_ref.logical_operation_id,
                )
                for item in sorted(
                    candidates,
                    key=lambda item: self.derive_claim_ref(
                        item.claim_occurrence_ref
                    ).claim_id,
                )
            )
            proof = ClaimCoverageProofRefSchema(
                coverage_proof_id=(
                    "claim-coverage://sha256/"
                    + canonical_sha256(
                        {
                            "coverage_scope_ref": accounting.coverage_scope_ref,
                            "paragraph_version_refs": [
                                item.model_dump(mode="json")
                                for item in accounting.paragraph_version_refs
                            ],
                            "coverage_semantics": accounting.model_dump(mode="json"),
                            "claim_refs": [
                                item.claim_ref.model_dump(mode="json")
                                for item in records
                            ],
                        }
                    )
                ),
                coverage_scope_ref=accounting.coverage_scope_ref,
                coverage_semantic_fingerprint=canonical_sha256(
                    accounting.model_dump(mode="json")
                ),
            )
            receipt = ClaimRecordCommitReceiptSchema(
                committed_operation_id=operation_ref.logical_operation_id,
                semantic_fingerprint=fingerprint,
                claim_refs=tuple(item.claim_ref for item in records),
                coverage_proof_ref=proof,
            )
            result_ref = _result_ref("claim-record-commit", receipt)
            claim = self._claim_side_effect(operation_ref, fingerprint)
            canonical_appended = False
            try:
                self._validate_operation_context(context)
                self._append_event(
                    event_type="claim_records_committed",
                    operation_ref=operation_ref,
                    semantic_fingerprint=fingerprint,
                    result_ref=result_ref,
                    payload={
                        "receipt": receipt.model_dump(mode="json"),
                        "claim_records": [
                            item.model_dump(mode="json") for item in records
                        ],
                        "coverage_accounting": accounting.model_dump(mode="json"),
                    },
                )
                canonical_appended = True
                self._ack_side_effect(claim, result_ref)
            except Exception:
                if not canonical_appended:
                    self.coordination_store.release(
                        claim,
                        reason="claim_record_commit_failed_before_canonical_append",
                    )
                raise
            return ClaimRecordCommitResult(
                records=records,
                coverage_accounting=accounting,
                coverage_proof_ref=proof,
                receipt=receipt,
            )

    def resolve_claim_by_id(self, claim_id: str) -> ClaimRecordSchema:
        normalized = str(claim_id or "").strip()
        if not normalized:
            raise ClaimVerificationRuntimeError("claim_id must be non-empty")
        with self._lock, InterProcessFileLock(self.lock_path):
            state = self._load_state_unlocked()
            record = state.claims.get(normalized)
            if record is None:
                raise ClaimVerificationRuntimeError("ClaimRecord does not resolve")
            if self.derive_claim_ref(record.claim_occurrence_ref) != record.claim_ref:
                raise ClaimVerificationIntegrityError(
                    "canonical ClaimRecord claim_ref is not deterministic"
                )
            return record

    def resolve_claim(self, claim_ref: ClaimRefSchema) -> ClaimRecordSchema:
        ref = ClaimRefSchema.model_validate(claim_ref)
        expected = self.derive_claim_ref(ref.claim_occurrence_ref)
        if expected != ref:
            raise ClaimIdentityCollision("ClaimRef is not deterministic")
        with self._lock, InterProcessFileLock(self.lock_path):
            state = self._load_state_unlocked()
            record = state.claims.get(ref.claim_id)
            if record is None:
                raise ClaimVerificationRuntimeError("ClaimRecord does not resolve")
            if record.claim_ref != ref:
                raise ClaimVerificationIntegrityError(
                    "ClaimRef resolved different canonical ClaimRecord"
                )
            return record

    def resolve_claim_coverage(
        self,
        coverage_proof_ref: ClaimCoverageProofRefSchema,
    ) -> ClaimCoverageAccountingSchema:
        ref = ClaimCoverageProofRefSchema.model_validate(coverage_proof_ref)
        with self._lock, InterProcessFileLock(self.lock_path):
            state = self._load_state_unlocked()
            accounting = state.coverage.get(ref.coverage_proof_id)
            receipt = state.coverage_receipts.get(ref.coverage_proof_id)
            if accounting is None or receipt is None:
                raise ClaimVerificationRuntimeError(
                    "ClaimCoverageProof does not resolve"
                )
            if receipt.coverage_proof_ref != ref:
                raise ClaimVerificationIntegrityError(
                    "ClaimCoverageProof ref resolves different canonical proof"
                )
            if (
                canonical_sha256(accounting.model_dump(mode="json"))
                != ref.coverage_semantic_fingerprint
            ):
                raise ClaimVerificationIntegrityError(
                    "ClaimCoverageProof semantic fingerprint mismatch"
                )
            return accounting

    # ------------------------------------------------------------------
    # W7.2 deterministic BindingFacts
    # ------------------------------------------------------------------

    @staticmethod
    def _authorized_source_hash(
        candidate: ClaimBindingFactsCandidateSchema,
    ) -> str:
        return canonical_sha256(
            [
                item.model_dump(mode="json")
                for item in candidate.exact_authorized_source_refs
            ]
        )

    @staticmethod
    def binding_candidate_semantic_fingerprint(
        candidate: ClaimBindingFactsCandidateSchema,
    ) -> str:
        candidate = ClaimBindingFactsCandidateSchema.model_validate(candidate)
        return canonical_sha256(
            {
                "contract": "claim_binding_facts_semantics_v1",
                "claim_ref": candidate.claim_ref.model_dump(mode="json"),
                "claim_occurrence_ref": candidate.claim_occurrence_ref.model_dump(
                    mode="json"
                ),
                "paragraph_version_ref": candidate.paragraph_version_ref.model_dump(
                    mode="json"
                ),
                "exact_authorized_source_refs": [
                    item.model_dump(mode="json")
                    for item in candidate.exact_authorized_source_refs
                ],
                "facts": [
                    item.model_dump(mode="json") for item in candidate.facts
                ],
                "source_set_hash": candidate.source_set_hash,
                "resolver_policy_id": candidate.resolver_policy_id,
                "resolver_policy_version": candidate.resolver_policy_version,
                "binding_purpose": candidate.binding_purpose,
            }
        )

    def _binding_operation(
        self,
        *,
        candidate: ClaimBindingFactsCandidateSchema,
        context: ClaimOperationContextSchema,
    ) -> LogicalOperationRef:
        return self._operation_ref(
            operation_kind=CLAIM_BINDING_FACTS_COMMIT,
            operation_identity={
                "claim_ref": candidate.claim_ref.model_dump(mode="json"),
                "paragraph_version_ref": candidate.paragraph_version_ref.model_dump(
                    mode="json"
                ),
                "exact_authorized_source_refs": [
                    item.model_dump(mode="json")
                    for item in candidate.exact_authorized_source_refs
                ],
                "resolver_policy_id": candidate.resolver_policy_id,
                "resolver_policy_version": candidate.resolver_policy_version,
                "binding_purpose": candidate.binding_purpose,
            },
            context=context,
        )

    @classmethod
    def binding_ref_for_candidate(cls, candidate: ClaimBindingFactsCandidateSchema) -> ClaimBindingFactsRefSchema:
        """Pure existing identity derivation; resolving a ref never commits a binding."""
        candidate = ClaimBindingFactsCandidateSchema.model_validate(candidate)
        fingerprint = cls.binding_candidate_semantic_fingerprint(candidate)
        if candidate.source_set_hash != cls._authorized_source_hash(candidate):
            raise ClaimBindingError("BindingFacts source_set_hash mismatch")
        if candidate.candidate_semantic_fingerprint != fingerprint:
            raise ClaimBindingError("BindingFacts candidate_semantic_fingerprint mismatch")
        return ClaimBindingFactsRefSchema(
            binding_facts_id=(
                "claim-binding-facts://sha256/"
                + canonical_sha256({
                    "claim_ref": candidate.claim_ref.model_dump(mode="json"),
                    "source_set_hash": candidate.source_set_hash,
                    "resolver_policy_id": candidate.resolver_policy_id,
                    "resolver_policy_version": candidate.resolver_policy_version,
                    "binding_purpose": candidate.binding_purpose,
                })
            ),
            claim_ref=candidate.claim_ref, source_set_hash=candidate.source_set_hash,
            binding_semantic_fingerprint=fingerprint,
        )

    def commit_binding_facts(
        self,
        candidate: ClaimBindingFactsCandidateSchema,
        operation_context: ClaimOperationContextSchema,
    ) -> ClaimBindingFactsRecordSchema:
        context = ClaimOperationContextSchema.model_validate(operation_context)
        candidate = ClaimBindingFactsCandidateSchema.model_validate(candidate)
        claim = self.resolve_claim(candidate.claim_ref)
        if (
            claim.claim_occurrence_ref != candidate.claim_occurrence_ref
            or claim.paragraph_version_ref != candidate.paragraph_version_ref
            or claim.extraction_occurrence_ref != candidate.extraction_occurrence_ref
        ):
            raise ClaimBindingError(
                "BindingFacts candidate does not bind exact canonical ClaimRecord"
            )
        expected_source_hash = self._authorized_source_hash(candidate)
        if candidate.source_set_hash != expected_source_hash:
            raise ClaimBindingError("BindingFacts source_set_hash mismatch")
        expected_candidate_fp = self.binding_candidate_semantic_fingerprint(candidate)
        if candidate.candidate_semantic_fingerprint != expected_candidate_fp:
            raise ClaimBindingError(
                "BindingFacts candidate_semantic_fingerprint mismatch"
            )
        operation_ref = self._binding_operation(
            candidate=candidate,
            context=context,
        )
        fingerprint = expected_candidate_fp
        binding_ref = self.binding_ref_for_candidate(candidate)

        with self._lock, InterProcessFileLock(self.lock_path):
            state = self._load_state_unlocked()
            existing = state.operations.get(operation_ref.logical_operation_id)
            if existing is not None:
                if existing.semantic_fingerprint != fingerprint:
                    raise ClaimIdentityCollision(
                        "same CLAIM_BINDING_FACTS_COMMIT changed semantics"
                    )
                self._reconcile_coordination(existing)
                return ClaimBindingFactsRecordSchema.model_validate(
                    existing.payload["record"]
                )
            existing_by_identity = state.bindings.get(binding_ref.binding_facts_id)
            if existing_by_identity is not None:
                if (
                    existing_by_identity.binding_semantic_fingerprint
                    != fingerprint
                ):
                    raise ClaimIdentityCollision(
                        "BindingFacts semantic identity already has different payload"
                    )
                return existing_by_identity

            lineage = self._validate_operation_context(context)
            self._resolve_exact_current_paragraph(
                paragraph_version_ref=claim.paragraph_version_ref,
                section_id=claim.section_id,
                lineage=lineage,
            )
            try:
                self.binding_source_validator(candidate, lineage)
            except Exception as exc:
                raise ClaimCurrentnessError(
                    "BindingFacts source became stale before commit"
                ) from exc

            record = ClaimBindingFactsRecordSchema(
                binding_facts_ref=binding_ref,
                claim_ref=candidate.claim_ref,
                claim_occurrence_ref=candidate.claim_occurrence_ref,
                paragraph_version_ref=candidate.paragraph_version_ref,
                extraction_occurrence_ref=candidate.extraction_occurrence_ref,
                exact_authorized_source_refs=candidate.exact_authorized_source_refs,
                facts=candidate.facts,
                source_set_hash=candidate.source_set_hash,
                resolver_policy_id=candidate.resolver_policy_id,
                resolver_policy_version=candidate.resolver_policy_version,
                binding_purpose=candidate.binding_purpose,
                binding_semantic_fingerprint=fingerprint,
                committed_operation_id=operation_ref.logical_operation_id,
            )
            result_ref = _result_ref(
                "claim-binding-facts",
                record.binding_facts_ref,
            )
            side_effect = self._claim_side_effect(operation_ref, fingerprint)
            canonical_appended = False
            try:
                self._append_event(
                    event_type="binding_facts_committed",
                    operation_ref=operation_ref,
                    semantic_fingerprint=fingerprint,
                    result_ref=result_ref,
                    payload={"record": record.model_dump(mode="json")},
                )
                canonical_appended = True
                self._ack_side_effect(side_effect, result_ref)
            except Exception:
                if not canonical_appended:
                    self.coordination_store.release(
                        side_effect,
                        reason="binding_facts_failed_before_canonical_append",
                    )
                raise
            return record

    def resolve_binding_facts(
        self,
        ref: ClaimBindingFactsRefSchema,
    ) -> ClaimBindingFactsRecordSchema:
        ref = ClaimBindingFactsRefSchema.model_validate(ref)
        with self._lock, InterProcessFileLock(self.lock_path):
            state = self._load_state_unlocked()
            record = state.bindings.get(ref.binding_facts_id)
            if record is None:
                raise ClaimVerificationRuntimeError(
                    "ClaimBindingFactsRef does not resolve"
                )
            if record.binding_facts_ref != ref:
                raise ClaimVerificationIntegrityError(
                    "ClaimBindingFactsRef resolves different record"
                )
            return record


    # ------------------------------------------------------------------
    # W7.4 Verification / Claim head / VerifierIssue lifecycle
    # ------------------------------------------------------------------

    def _require_support_record(
        self,
        ref: ClaimEvidenceSupportRefSchema,
    ) -> ClaimEvidenceSupportRecordSchema:
        if self.support_resolver is None:
            raise ClaimVerificationRuntimeError(
                "verification requires configured shared support resolver"
            )
        record = self.support_resolver(ref)
        record = ClaimEvidenceSupportRecordSchema.model_validate(record)
        if record.support_ref != ref:
            raise ClaimVerificationIntegrityError(
                "shared support resolver returned different support record"
            )
        return record

    @staticmethod
    def _binding_record_candidate(
        record: ClaimBindingFactsRecordSchema,
    ) -> ClaimBindingFactsCandidateSchema:
        return ClaimBindingFactsCandidateSchema(
            claim_ref=record.claim_ref,
            claim_occurrence_ref=record.claim_occurrence_ref,
            paragraph_version_ref=record.paragraph_version_ref,
            extraction_occurrence_ref=record.extraction_occurrence_ref,
            exact_authorized_source_refs=record.exact_authorized_source_refs,
            facts=record.facts,
            source_set_hash=record.source_set_hash,
            resolver_policy_id=record.resolver_policy_id,
            resolver_policy_version=record.resolver_policy_version,
            binding_purpose=record.binding_purpose,
            candidate_semantic_fingerprint=record.binding_semantic_fingerprint,
        )

    @staticmethod
    def verifier_semantic_fingerprint(
        candidate: VerifierSemanticResultCandidateSchema,
    ) -> str:
        candidate = VerifierSemanticResultCandidateSchema.model_validate(candidate)
        material = candidate.model_dump(mode="json", exclude={"diagnostic_metadata"})
        material["issue_candidates"] = [i.model_dump(mode="json") for i in sorted(candidate.issue_candidates, key=lambda i: i.issue_slot)]
        return canonical_sha256({"contract": "verifier_semantic_candidate_v2", "candidate": material})

    @staticmethod
    def candidate_from_result(result, binding, issues):
        from apps.enterprise_document.schemas.agent_rag_v1.verification import VerifierProjectInputBasisV1
        data = result.model_dump(mode="json")
        data["verifier_project_input_basis"] = VerifierProjectInputBasisV1(
            writer_generation_invocation_ref=result.writer_generation_invocation_ref,
            project_input_sha256=result.project_input_sha256,
            project_input_basis_ref=result.project_input_basis_ref).model_dump(mode="json")
        data["exact_relevant_source_refs"] = [i.model_dump(mode="json") for i in binding.exact_authorized_source_refs]
        data["allowed_authority_refs"] = sorted({i.exact_source_ref for i in binding.exact_authorized_source_refs} | {result.project_input_basis_ref})
        data["recommended_verdict"] = result.verdict.value
        data["issue_candidates"] = [VerifierSemanticIssueCandidateSchema(**{
            key: getattr(issue, key) for key in ("issue_slot", "issue_code", "severity", "semantic_category",
                "message_hash", "rationale_ref", "source_refs", "material_source_scope_key") if hasattr(issue, key)
        }).model_dump(mode="json") for issue in issues]
        return VerifierSemanticResultCandidateSchema.model_validate({key: value for key, value in data.items()
            if key in VerifierSemanticResultCandidateSchema.model_fields and key != "schema_version"})

    def verification_commit_semantic_fingerprint(
        self,
        *,
        claim_record: ClaimRecordSchema,
        binding_record: ClaimBindingFactsRecordSchema,
        support_record: ClaimEvidenceSupportRecordSchema,
        verifier_candidate: VerifierSemanticResultCandidateSchema,
    ) -> str:
        return canonical_sha256(
            {
                "contract": "claim_verification_commit_semantics_v2",
                "verifier_semantic_input_sha256": verifier_candidate.verifier_semantic_input_sha256,
                "claim_record": claim_record.model_dump(mode="json"),
                "binding_semantic_fingerprint": (
                    binding_record.binding_semantic_fingerprint
                ),
                "support_semantic_fingerprint": (
                    (support_record.support_ref if hasattr(support_record, "support_ref") else support_record).support_semantic_fingerprint
                ),
                "verifier_semantic_fingerprint": self.verifier_semantic_fingerprint(
                    verifier_candidate
                ),
                "deterministic_validation_policy_version": (
                    self.deterministic_validation_policy_version
                ),
            }
        )

    def _verification_operation(
        self,
        *,
        claim_record: ClaimRecordSchema,
        binding_record: ClaimBindingFactsRecordSchema,
        support_record: ClaimEvidenceSupportRecordSchema,
        verifier_candidate: VerifierSemanticResultCandidateSchema,
        context: ClaimOperationContextSchema,
    ) -> LogicalOperationRef:
        return self._operation_ref(
            operation_kind=CLAIM_VERIFICATION_COMMIT,
            operation_identity={
                "claim_ref": claim_record.claim_ref.model_dump(mode="json"),
                "paragraph_version_ref": claim_record.paragraph_version_ref.model_dump(
                    mode="json"
                ),
                "binding_facts_ref": binding_record.binding_facts_ref.model_dump(
                    mode="json"
                ),
                "claim_evidence_support_ref": support_record.support_ref.model_dump(
                    mode="json"
                ),
                "verifier_semantic_input_sha256": verifier_candidate.verifier_semantic_input_sha256,
                "verification_occurrence_ref": verifier_candidate.verification_occurrence_ref,
                "verifier_policy_id": verifier_candidate.verifier_policy_id,
                "verifier_policy_version": verifier_candidate.verifier_policy_version,
            },
            context=context,
        )

    def _validate_verification_currentness(
        self,
        *,
        claim_record: ClaimRecordSchema,
        binding_record: ClaimBindingFactsRecordSchema,
        support_record: ClaimEvidenceSupportRecordSchema,
        verifier_candidate: VerifierSemanticResultCandidateSchema,
        lineage: ActiveExecutionLineageSchema,
        validate_provenance: bool = True,
    ) -> None:
        self._resolve_exact_current_paragraph(
            paragraph_version_ref=claim_record.paragraph_version_ref,
            section_id=claim_record.section_id,
            lineage=lineage,
        )
        binding_candidate = self._binding_record_candidate(binding_record)
        try:
            self.binding_source_validator(binding_candidate, lineage)
        except Exception as exc:
            raise ClaimCurrentnessError(
                "Verification BindingFacts sources are stale"
            ) from exc
        if self.verification_source_validator is None:
            raise ClaimVerificationRuntimeError(
                "verification requires configured verification_source_validator"
            )
        try:
            self.verification_source_validator(
                binding_record,
                support_record,
                lineage,
            )
        except Exception as exc:
            raise ClaimCurrentnessError(
                "Verification support/source currentness validation failed"
            ) from exc

        if binding_record.claim_ref != claim_record.claim_ref:
            raise ClaimBindingError(
                "Verification BindingFacts belong to another Claim"
            )
        if (
            binding_record.claim_occurrence_ref
            != claim_record.claim_occurrence_ref
            or binding_record.paragraph_version_ref
            != claim_record.paragraph_version_ref
        ):
            raise ClaimBindingError(
                "Verification BindingFacts do not bind exact Claim occurrence"
            )
        support_subject = support_record.claim_support_subject_ref
        if (
            support_subject.authority_kind
            is not ClaimSupportAuthorityKind.SIF07_CLAIM
            or support_subject.subject_ref != claim_record.claim_ref.claim_id
        ):
            raise ClaimBindingError(
                "Verification shared support does not target canonical SIF-07 Claim"
            )
        if support_record.support_ref != verifier_candidate.claim_evidence_support_ref:
            raise ClaimBindingError(
                "Verifier candidate shared support ref is stale"
            )
        if binding_record.binding_facts_ref != verifier_candidate.binding_facts_ref:
            raise ClaimBindingError(
                "Verifier candidate BindingFacts ref is stale"
            )
        if verifier_candidate.claim_ref != claim_record.claim_ref:
            raise ClaimBindingError(
                "Verifier candidate Claim ref is stale"
            )

        authorized_refs = {
            item.exact_source_ref
            for item in binding_record.exact_authorized_source_refs
        }
        if not set(
            support_record.evidence_target_set_ref.evidence_member_refs
        ) <= authorized_refs:
            raise ClaimBindingError(
                "shared support Evidence target escapes exact authorized Binding sources"
            )

        if self.verification_identity_validator is None:
            raise ClaimCurrentnessError("Verifier V2 identity resolver is absent")
        try:
            self.verification_identity_validator(claim_record, binding_record, support_record, verifier_candidate, lineage)
        except Exception as exc:
            raise ClaimCurrentnessError("Verifier Writer/ProjectInput/semantic identity is stale") from exc
        if validate_provenance:
            self.validate_v2_provenance(binding_record, support_record, verifier_candidate)

    @staticmethod
    def validate_v2_provenance(binding, support, candidate):
        """Mechanical admission only; semantic sufficiency remains Provider-owned."""
        allowed = {i.exact_source_ref for i in binding.exact_authorized_source_refs}
        allowed.add(candidate.verifier_project_input_basis.project_input_basis_ref)
        if set(candidate.allowed_authority_refs) != allowed:
            raise ClaimBindingError("derived authority universe mismatch")
        kinds = {}
        for source in binding.exact_authorized_source_refs:
            kinds.setdefault(source.exact_source_ref, set()).add(source.source_kind)
        positive = set(candidate.positive_basis_refs)
        pi = candidate.verifier_project_input_basis.project_input_basis_ref
        if any(ref != pi and kinds.get(ref) not in ({"FACT"}, {"EVIDENCE"}) for ref in positive):
            raise ClaimBindingError("positive basis source kind is inadmissible")
        if candidate.recommended_verdict is VerificationVerdict.VERIFIED:
            if not positive:
                raise ClaimBindingError("VERIFIED requires positive basis")
            if support.relation in {SupportRelation.CONTRADICTED, SupportRelation.INDETERMINATE}:
                if isinstance(candidate, VerifierSemanticResultCandidateSchema):
                    raise VerifierSupportVerdictAdmissionError(
                        binding=binding, support=support, candidate=candidate,
                    )
                raise ClaimBindingError("VERIFIED forbidden by unresolved/contradictory shared Support")
            if any(f.relation.value == "CONTRADICTS" for f in binding.facts):
                raise ClaimBindingError("VERIFIED is forbidden by authoritative Binding contradiction")
        evidence = {ref for ref in positive if kinds.get(ref) == {"EVIDENCE"}}
        if evidence:
            if support.relation in {SupportRelation.NOT_SUPPORTED, SupportRelation.INDETERMINATE}:
                raise ClaimBindingError("Evidence positive basis forbidden by Support relation")
            admissible = {f.exact_source_ref for f in binding.facts if f.source_kind == "EVIDENCE"
                          and f.relation.value in {"SUPPORTS", "CONTEXT_ONLY"}} - {
                          f.exact_source_ref for f in binding.facts if f.relation.value == "NO_MATERIAL_SUPPORT"}
            if not evidence <= set(support.supporting_refs) or not evidence <= admissible:
                if not isinstance(candidate, VerifierSemanticResultCandidateSchema):
                    raise ClaimBindingError(
                        "Evidence positive basis requires shared supporting refs with admissible Binding observations"
                    )
                invocation = ClaimVerificationRuntime.verifier_input_from_candidate(candidate)
                key = canonical_sha256({"contract": "verifier-physical-occurrence-v2",
                    "verifier_semantic_input_sha256": invocation.verifier_semantic_input_sha256})
                admitted_evidence = admissible & set(support.supporting_refs)
                admitted_positive = tuple(sorted({pi} | {ref for ref, domains in kinds.items() if domains == {"FACT"}}
                    | admitted_evidence))
                allowed_refs = tuple(sorted(allowed))
                violation = build_verifier_candidate_contract_violation(
                    original_input_ref=f"artifact://canonical-verifier-v2/{key}/input",
                    original_input_sha256=canonical_sha256(invocation.model_dump(mode="json")),
                    candidate_ref=f"artifact://canonical-verifier-v2/{key}/candidate",
                    candidate_sha256=canonical_sha256(candidate.model_dump(mode="json")),
                    violating_refs=tuple(sorted(evidence - admitted_evidence)),
                    allowed_authority_refs=allowed_refs, allowed_authority_sha256=canonical_sha256(allowed_refs),
                    admissible_positive_basis_refs=admitted_positive,
                    admissible_positive_basis_sha256=canonical_sha256(admitted_positive),
                    binding_facts_ref=binding.binding_facts_ref, claim_evidence_support_ref=support.support_ref)
                raise VerifierCandidateContractViolationError(violation)
        if not positive <= allowed or any(not set(i.source_refs) <= allowed for i in candidate.issue_candidates):
            raise ClaimBindingError("Verifier provenance escapes authority universe")

    @staticmethod
    def _issue_logical_id(
        *,
        claim_record: ClaimRecordSchema,
        candidate: VerifierSemanticIssueCandidateSchema,
    ) -> str:
        material_scope = (
            candidate.material_source_scope_key
            or canonical_sha256(candidate.source_refs)
        )
        return (
            "verifier-issue-logical://sha256/"
            + canonical_sha256(
                {
                    "claim_ref": claim_record.claim_ref.model_dump(mode="json"),
                    "paragraph_version_ref": (
                        claim_record.paragraph_version_ref.model_dump(mode="json")
                    ),
                    "issue_code": candidate.issue_code,
                    "semantic_category": candidate.semantic_category,
                    "material_source_scope_key": material_scope,
                }
            )
        )

    @staticmethod
    def _verification_result_ref(
        *,
        claim_record: ClaimRecordSchema,
        operation_ref: LogicalOperationRef,
        semantic_fingerprint: str,
    ) -> VerificationResultRefSchema:
        return VerificationResultRefSchema(
            verification_result_id=(
                "verification-result://sha256/"
                + canonical_sha256(
                    {
                        "claim_ref": claim_record.claim_ref.model_dump(mode="json"),
                        "committed_operation_id": operation_ref.logical_operation_id,
                        "verification_semantic_fingerprint": semantic_fingerprint,
                    }
                )
            ),
            claim_ref=claim_record.claim_ref,
            verification_semantic_fingerprint=semantic_fingerprint,
        )

    @staticmethod
    def _stored_verification_result(
        stored: _StoredOperation,
    ) -> ClaimVerificationCommitResult:
        return ClaimVerificationCommitResult(
            verification_result=VerificationResultRecordSchema.model_validate(
                stored.payload["verification_result"]
            ),
            verification_revision=(
                ClaimVerificationRevisionRecordSchema.model_validate(
                    stored.payload["verification_revision"]
                )
            ),
            resulting_claim_head=ClaimCurrentHeadSchema.model_validate(
                stored.payload["receipt"]["resulting_claim_head"]
            ),
            issue_records=tuple(
                VerifierIssueRecordSchema.model_validate(item)
                for item in stored.payload.get("issue_records", [])
            ),
            issue_heads=tuple(
                VerifierIssueHeadSchema.model_validate(item)
                for item in stored.payload.get("issue_heads", [])
            ),
            receipt=ClaimVerificationCommitReceiptSchema.model_validate(
                stored.payload["receipt"]
            ),
        )

    def commit_verification(
        self,
        claim_ref: ClaimRefSchema,
        binding_facts_ref: ClaimBindingFactsRefSchema,
        claim_evidence_support_ref: ClaimEvidenceSupportRefSchema,
        verifier_candidate: VerifierSemanticResultCandidateSchema,
        expected_claim_head: ClaimCurrentHeadSchema | None,
        operation_context: ClaimOperationContextSchema,
    ) -> ClaimVerificationCommitResult:
        context = ClaimOperationContextSchema.model_validate(operation_context)
        claim_ref = ClaimRefSchema.model_validate(claim_ref)
        binding_facts_ref = ClaimBindingFactsRefSchema.model_validate(
            binding_facts_ref
        )
        support_ref = ClaimEvidenceSupportRefSchema.model_validate(
            claim_evidence_support_ref
        )
        verifier_candidate = VerifierSemanticResultCandidateSchema.model_validate(
            verifier_candidate.model_dump(mode="json") if hasattr(verifier_candidate, "model_dump") else verifier_candidate
        )

        claim_record = self.resolve_claim(claim_ref)
        binding_record = self.resolve_binding_facts(binding_facts_ref)
        support_record = self._require_support_record(support_ref)
        if (
            binding_record.claim_ref != claim_ref
            or verifier_candidate.claim_ref != claim_ref
            or verifier_candidate.binding_facts_ref != binding_facts_ref
            or verifier_candidate.claim_evidence_support_ref != support_ref
        ):
            raise ClaimBindingError(
                "Verification exact Claim/Binding/Support refs disagree"
            )

        if (verifier_candidate.verification_authorization_ref != context.authorization_ref
                or any(getattr(verifier_candidate.active_lineage, key) != getattr(context.active_lineage, key)
                       for key in ("run_id", "attempt_id", "attempt_scope_ref", "input_snapshot_version"))):
            raise ClaimBindingError("Verifier candidate authorization/scope differs from commit")
        operation_ref = self._verification_operation(
            claim_record=claim_record,
            binding_record=binding_record,
            support_record=support_record,
            verifier_candidate=verifier_candidate,
            context=context,
        )
        fingerprint = self.verification_commit_semantic_fingerprint(
            claim_record=claim_record,
            binding_record=binding_record,
            support_record=support_record,
            verifier_candidate=verifier_candidate,
        )

        with self._lock, InterProcessFileLock(self.lock_path):
            state = self._load_state_unlocked()
            existing = state.operations.get(operation_ref.logical_operation_id)
            if existing is not None:
                if existing.semantic_fingerprint != fingerprint:
                    raise ClaimIdentityCollision(
                        "same CLAIM_VERIFICATION_COMMIT changed semantics"
                    )
                if existing.event_type != "verification_committed":
                    raise ClaimVerificationIntegrityError(
                        "verification operation resolves wrong event type"
                    )
                self._reconcile_coordination(existing)
                return self._stored_verification_result(existing)

            lineage = self._validate_operation_context(context)
            self._validate_candidate_recovery_control_unlocked(state, verifier_candidate)
            self._validate_verification_currentness(
                claim_record=claim_record,
                binding_record=binding_record,
                support_record=support_record,
                verifier_candidate=verifier_candidate,
                lineage=lineage,
            )
            current_head = state.claim_heads.get(claim_ref.claim_id)
            if expected_claim_head != current_head:
                raise ClaimHeadConflict(
                    "expected ClaimCurrentHead does not match canonical current head"
                )
            if current_head is not None and (
                current_head.lifecycle_status is not ClaimLifecycleStatus.ACTIVE
            ):
                raise ClaimHeadConflict(
                    "reverification requires ACTIVE Claim lifecycle"
                )

            verification_revision = (
                1
                if current_head is None
                else (
                    current_head.active_verification_revision_ref.verification_revision
                    + 1
                )
            )
            revision_ref = ClaimVerificationRevisionRefSchema(
                claim_ref=claim_ref,
                verification_revision=verification_revision,
            )
            previous_revision_ref = (
                None
                if current_head is None
                else current_head.active_verification_revision_ref
            )
            result_ref = self._verification_result_ref(
                claim_record=claim_record,
                operation_ref=operation_ref,
                semantic_fingerprint=fingerprint,
            )

            issue_candidates = tuple(
                sorted(
                    verifier_candidate.issue_candidates,
                    key=lambda item: item.issue_slot,
                )
            )
            issue_refs: list[VerifierIssueRefSchema] = []
            issue_records: list[VerifierIssueRecordSchema] = []
            issue_heads: list[VerifierIssueHeadSchema] = []
            for issue_candidate in issue_candidates:
                authorized_refs = set(verifier_candidate.allowed_authority_refs)
                if not set(issue_candidate.source_refs) <= authorized_refs:
                    raise ClaimBindingError(
                        "Verifier issue source ref escapes authorized Binding sources"
                    )
                logical_id = self._issue_logical_id(
                    claim_record=claim_record,
                    candidate=issue_candidate,
                )
                issue_ref = VerifierIssueRefSchema(
                    issue_id=(
                        "verifier-issue://sha256/"
                        + canonical_sha256(
                            {
                                "verification_result_ref": result_ref.model_dump(
                                    mode="json"
                                ),
                                "issue_slot": issue_candidate.issue_slot,
                                "issue_logical_id": logical_id,
                            }
                        )
                    ),
                    issue_logical_id=logical_id,
                    issue_slot=issue_candidate.issue_slot,
                    verification_result_ref=result_ref,
                )
                issue_record = VerifierIssueRecordSchema(
                    issue_ref=issue_ref,
                    issue_logical_id=logical_id,
                    issue_slot=issue_candidate.issue_slot,
                    claim_ref=claim_ref,
                    paragraph_version_ref=claim_record.paragraph_version_ref,
                    verification_result_ref=result_ref,
                    issue_code=issue_candidate.issue_code,
                    severity=issue_candidate.severity,
                    semantic_category=issue_candidate.semantic_category,
                    message_hash=issue_candidate.message_hash,
                    rationale_ref=issue_candidate.rationale_ref,
                    source_refs=issue_candidate.source_refs,
                    created_by_operation_id=operation_ref.logical_operation_id,
                )
                issue_head = VerifierIssueHeadSchema(
                    issue_ref=issue_ref,
                    issue_revision=1,
                    lifecycle_status=VerifierIssueLifecycleStatus.OPEN,
                    head_updated_by_operation_id=operation_ref.logical_operation_id,
                )
                issue_refs.append(issue_ref)
                issue_records.append(issue_record)
                issue_heads.append(issue_head)

            verifier_semantic_fp = self.verifier_semantic_fingerprint(
                verifier_candidate
            )
            verification_result = VerificationResultRecordSchema(
                verification_result_ref=result_ref,
                claim_ref=claim_ref,
                claim_occurrence_ref=claim_record.claim_occurrence_ref,
                paragraph_version_ref=claim_record.paragraph_version_ref,
                binding_facts_ref=binding_facts_ref,
                claim_evidence_support_ref=support_ref,
                verifier_semantic_result_fingerprint=verifier_semantic_fp,
                verdict=verifier_candidate.recommended_verdict,
                issue_refs=tuple(issue_refs),
                semantic_rationale_hash=verifier_candidate.semantic_rationale_hash,
                semantic_rationale_ref=verifier_candidate.semantic_rationale_ref,
                verifier_policy_id=verifier_candidate.verifier_policy_id,
                verifier_policy_version=verifier_candidate.verifier_policy_version,
                binding_source_set_hash=binding_record.source_set_hash,
                **verifier_candidate.verifier_project_input_basis.model_dump(exclude={"schema_version"}),
                authority_universe_sha256=verifier_candidate.authority_universe_sha256,
                verifier_semantic_input_sha256=verifier_candidate.verifier_semantic_input_sha256,
                positive_basis_refs=verifier_candidate.positive_basis_refs,
                active_lineage=verifier_candidate.active_lineage,
                verification_authorization_ref=verifier_candidate.verification_authorization_ref,
                response_contract=verifier_candidate.response_contract,
                deterministic_validation_facts=verifier_candidate.deterministic_validation_facts,
                verification_occurrence_ref=verifier_candidate.verification_occurrence_ref,
                committed_operation_id=operation_ref.logical_operation_id,
            )
            revision_record = ClaimVerificationRevisionRecordSchema(
                revision_ref=revision_ref,
                claim_ref=claim_ref,
                verification_revision=verification_revision,
                verification_result_ref=result_ref,
                binding_facts_ref=binding_facts_ref,
                claim_evidence_support_ref=support_ref,
                previous_verification_revision_ref=previous_revision_ref,
                source_set_hash=binding_record.source_set_hash,
                committed_operation_id=operation_ref.logical_operation_id,
            )
            resulting_head = ClaimCurrentHeadSchema(
                claim_ref=claim_ref,
                head_revision=(
                    1 if current_head is None else current_head.head_revision + 1
                ),
                active_verification_revision_ref=revision_ref,
                lifecycle_status=(
                    ClaimLifecycleStatus.ACTIVE
                    if current_head is None
                    else current_head.lifecycle_status
                ),
                expected_previous_head_revision=(
                    None if current_head is None else current_head.head_revision
                ),
                head_updated_by_operation_id=operation_ref.logical_operation_id,
            )
            receipt = ClaimVerificationCommitReceiptSchema(
                committed_operation_id=operation_ref.logical_operation_id,
                semantic_fingerprint=fingerprint,
                verification_result_ref=result_ref,
                verification_revision_ref=revision_ref,
                resulting_claim_head=resulting_head,
                issue_refs=tuple(issue_refs),
            )
            commit_result_ref = _result_ref(
                "claim-verification-commit",
                receipt,
            )
            side_effect = self._claim_side_effect(
                operation_ref,
                fingerprint,
            )
            canonical_appended = False
            try:
                # Revalidate immediately before the single semantic append.
                lineage = self._validate_operation_context(context)
                self._validate_candidate_recovery_control_unlocked(state, verifier_candidate)
                self._validate_verification_currentness(
                    claim_record=claim_record,
                    binding_record=binding_record,
                    support_record=support_record,
                    verifier_candidate=verifier_candidate,
                    lineage=lineage,
                )
                self._append_event(
                    event_type="verification_committed",
                    operation_ref=operation_ref,
                    semantic_fingerprint=fingerprint,
                    result_ref=commit_result_ref,
                    payload={
                        "receipt": receipt.model_dump(mode="json"),
                        "verifier_candidate": verifier_candidate.model_dump(mode="json"),
                        "verification_result": verification_result.model_dump(
                            mode="json"
                        ),
                        "verification_revision": revision_record.model_dump(
                            mode="json"
                        ),
                        "issue_records": [
                            item.model_dump(mode="json")
                            for item in issue_records
                        ],
                        "issue_heads": [
                            item.model_dump(mode="json")
                            for item in issue_heads
                        ],
                    },
                )
                canonical_appended = True
                self._ack_side_effect(side_effect, commit_result_ref)
            except Exception:
                if not canonical_appended:
                    self.coordination_store.release(
                        side_effect,
                        reason="verification_failed_before_canonical_append",
                    )
                raise
            return ClaimVerificationCommitResult(
                verification_result=verification_result,
                verification_revision=revision_record,
                resulting_claim_head=resulting_head,
                issue_records=tuple(issue_records),
                issue_heads=tuple(issue_heads),
                receipt=receipt,
            )

    def resolve_current_generation_sources(self, *, run_id, section_id, paragraph_version_refs):
        """Read canonical active Claim/head/revision/Result under one owner lock.

        Exact current Paragraph membership bounds enumeration. Missing heads
        fail closed. This grants no mutation or cross-owner currentness proof.
        """
        versions = tuple(paragraph_version_refs)
        with self._lock, InterProcessFileLock(self.lock_path):
            state = self._load_state_unlocked()
            rows = []
            for claim in state.claims.values():
                if (claim.claim_occurrence_ref.run_id != run_id or claim.section_id != section_id
                        or claim.paragraph_version_ref not in versions):
                    continue
                head = state.claim_heads.get(claim.claim_ref.claim_id)
                if head is None:
                    raise ClaimVerificationIntegrityError("current generation Claim has no head")
                if head.lifecycle_status is not ClaimLifecycleStatus.ACTIVE:
                    continue
                revision = state.verification_revisions.get((claim.claim_ref.claim_id,
                    head.active_verification_revision_ref.verification_revision))
                if revision is None or revision.revision_ref != head.active_verification_revision_ref:
                    raise ClaimVerificationIntegrityError("current generation active revision is missing or differs")
                result = state.verification_results.get(revision.verification_result_ref.verification_result_id)
                if (result is None or result.verification_result_ref != revision.verification_result_ref
                        or result.claim_ref != claim.claim_ref or head.claim_ref != claim.claim_ref
                        or revision.claim_ref != claim.claim_ref):
                    raise ClaimVerificationIntegrityError("current generation Result V2 is missing or differs")
                rows.append((claim, head, revision, result))
            return tuple(sorted(rows, key=lambda row: row[0].claim_ref.claim_id))

    def resolve_claim_head(
        self,
        claim_ref: ClaimRefSchema,
    ) -> ClaimCurrentHeadSchema | None:
        ref = ClaimRefSchema.model_validate(claim_ref)
        self.resolve_claim(ref)
        with self._lock, InterProcessFileLock(self.lock_path):
            state = self._load_state_unlocked()
            return state.claim_heads.get(ref.claim_id)

    def resolve_verification_result(
        self,
        ref: VerificationResultRefSchema,
    ) -> VerificationResultRecordSchema:
        ref = VerificationResultRefSchema.model_validate(ref)
        with self._lock, InterProcessFileLock(self.lock_path):
            state = self._load_state_unlocked()
            record = state.verification_results.get(ref.verification_result_id)
            if record is None:
                raise ClaimVerificationRuntimeError(
                    "VerificationResultRef does not resolve"
                )
            if record.verification_result_ref != ref:
                raise ClaimVerificationIntegrityError(
                    "VerificationResultRef resolves different record"
                )
            return record

    def transition_claim_lifecycle(
        self,
        claim_ref: ClaimRefSchema,
        expected_head_revision: int,
        expected_lifecycle_status: ClaimLifecycleStatus,
        target_status: ClaimLifecycleStatus,
        reason_ref: str,
        operation_context: ClaimOperationContextSchema,
        *,
        successor_claim_refs: Iterable[ClaimRefSchema] = (),
    ) -> ClaimLifecycleTransitionReceiptSchema:
        context = ClaimOperationContextSchema.model_validate(operation_context)
        claim_ref = ClaimRefSchema.model_validate(claim_ref)
        self.resolve_claim(claim_ref)
        reason = str(reason_ref or "").strip()
        if not reason:
            raise ClaimVerificationRuntimeError("Claim lifecycle reason_ref required")
        successors = tuple(
            sorted(
                (
                    ClaimRefSchema.model_validate(item)
                    for item in successor_claim_refs
                ),
                key=lambda item: item.claim_id,
            )
        )
        if target_status not in {
            ClaimLifecycleStatus.SUPERSEDED,
            ClaimLifecycleStatus.INVALIDATED,
            ClaimLifecycleStatus.REMOVED,
        }:
            raise ClaimHeadConflict("unsupported Target Claim lifecycle transition")
        if expected_lifecycle_status is not ClaimLifecycleStatus.ACTIVE:
            raise ClaimHeadConflict("Target V1 only transitions Claim from ACTIVE")
        if target_status is ClaimLifecycleStatus.SUPERSEDED and not successors:
            raise ClaimHeadConflict("SUPERSEDED requires successor Claim ref")
        if target_status is not ClaimLifecycleStatus.SUPERSEDED and successors:
            raise ClaimHeadConflict(
                "successor Claim refs are only valid for SUPERSEDED"
            )
        operation_ref = self._operation_ref(
            operation_kind=CLAIM_LIFECYCLE_TRANSITION,
            operation_identity={
                "claim_ref": claim_ref.model_dump(mode="json"),
                "target_status": target_status.value,
                "reason_ref": reason,
                "successor_claim_refs": [
                    item.model_dump(mode="json") for item in successors
                ],
            },
            context=context,
        )
        fingerprint = canonical_sha256(
            {
                "contract": "claim_lifecycle_transition_v1",
                "claim_ref": claim_ref.model_dump(mode="json"),
                "expected_head_revision": expected_head_revision,
                "from_lifecycle": expected_lifecycle_status.value,
                "to_lifecycle": target_status.value,
                "successor_claim_refs": [
                    item.model_dump(mode="json") for item in successors
                ],
                "reason_ref": reason,
                "transition_policy_version": "claim-lifecycle-v1",
            }
        )
        with self._lock, InterProcessFileLock(self.lock_path):
            state = self._load_state_unlocked()
            existing = state.operations.get(operation_ref.logical_operation_id)
            if existing is not None:
                if existing.semantic_fingerprint != fingerprint:
                    raise ClaimIdentityCollision(
                        "same CLAIM_LIFECYCLE_TRANSITION changed semantics"
                    )
                if existing.event_type != "claim_lifecycle_transition":
                    raise ClaimVerificationIntegrityError(
                        "Claim lifecycle operation resolves wrong event"
                    )
                self._reconcile_coordination(existing)
                return ClaimLifecycleTransitionReceiptSchema.model_validate(
                    existing.payload["receipt"]
                )
            self._validate_operation_context(context)
            current = state.claim_heads.get(claim_ref.claim_id)
            if current is None:
                raise ClaimHeadConflict(
                    "Claim lifecycle transition requires existing ClaimCurrentHead"
                )
            if (
                current.head_revision != expected_head_revision
                or current.lifecycle_status is not expected_lifecycle_status
            ):
                raise ClaimHeadConflict("Claim lifecycle expected head is stale")
            for successor in successors:
                if successor.claim_id == claim_ref.claim_id:
                    raise ClaimHeadConflict("Claim cannot supersede itself")
                if successor.claim_id not in state.claims:
                    raise ClaimHeadConflict(
                        "SUPERSEDED successor ClaimRecord does not resolve"
                    )
            resulting = ClaimCurrentHeadSchema(
                claim_ref=claim_ref,
                head_revision=current.head_revision + 1,
                active_verification_revision_ref=(
                    current.active_verification_revision_ref
                ),
                lifecycle_status=target_status,
                expected_previous_head_revision=current.head_revision,
                head_updated_by_operation_id=operation_ref.logical_operation_id,
            )
            receipt = ClaimLifecycleTransitionReceiptSchema(
                committed_operation_id=operation_ref.logical_operation_id,
                semantic_fingerprint=fingerprint,
                prior_head=current,
                resulting_head=resulting,
                reason_ref=reason,
                successor_claim_refs=successors,
            )
            result_ref = _result_ref("claim-lifecycle-transition", receipt)
            side_effect = self._claim_side_effect(operation_ref, fingerprint)
            canonical_appended = False
            try:
                self._append_event(
                    event_type="claim_lifecycle_transition",
                    operation_ref=operation_ref,
                    semantic_fingerprint=fingerprint,
                    result_ref=result_ref,
                    payload={"receipt": receipt.model_dump(mode="json")},
                )
                canonical_appended = True
                self._ack_side_effect(side_effect, result_ref)
            except Exception:
                if not canonical_appended:
                    self.coordination_store.release(
                        side_effect,
                        reason="claim_lifecycle_failed_before_canonical_append",
                    )
                raise
            return receipt

    def resolve_issue(
        self,
        issue_ref: VerifierIssueRefSchema,
    ) -> VerifierIssueRecordSchema:
        ref = VerifierIssueRefSchema.model_validate(issue_ref)
        with self._lock, InterProcessFileLock(self.lock_path):
            state = self._load_state_unlocked()
            record = state.issues.get(ref.issue_id)
            if record is None:
                raise ClaimVerificationRuntimeError(
                    "VerifierIssueRef does not resolve"
                )
            if record.issue_ref != ref:
                raise ClaimVerificationIntegrityError(
                    "VerifierIssueRef resolves different record"
                )
            return record

    def resolve_issue_head(
        self,
        issue_ref: VerifierIssueRefSchema,
    ) -> VerifierIssueHeadSchema | None:
        ref = VerifierIssueRefSchema.model_validate(issue_ref)
        self.resolve_issue(ref)
        with self._lock, InterProcessFileLock(self.lock_path):
            state = self._load_state_unlocked()
            return state.issue_heads.get(ref.issue_id)

    def transition_issue_lifecycle(
        self,
        issue_ref: VerifierIssueRefSchema,
        expected_head_revision: int,
        expected_lifecycle_status: VerifierIssueLifecycleStatus,
        target_status: VerifierIssueLifecycleStatus,
        reason_ref: str,
        operation_context: ClaimOperationContextSchema,
        *,
        resolution_ref: str | None = None,
        superseded_by_issue_ref: VerifierIssueRefSchema | None = None,
    ) -> VerifierIssueLifecycleTransitionReceiptSchema:
        context = ClaimOperationContextSchema.model_validate(operation_context)
        issue_ref = VerifierIssueRefSchema.model_validate(issue_ref)
        self.resolve_issue(issue_ref)
        reason = str(reason_ref or "").strip()
        if not reason:
            raise ClaimVerificationRuntimeError("Issue lifecycle reason_ref required")
        if expected_lifecycle_status is not VerifierIssueLifecycleStatus.OPEN:
            raise VerifierIssueHeadConflict(
                "Target V1 only transitions VerifierIssue from OPEN"
            )
        if target_status not in {
            VerifierIssueLifecycleStatus.RESOLVED,
            VerifierIssueLifecycleStatus.SUPERSEDED,
            VerifierIssueLifecycleStatus.INVALIDATED,
        }:
            raise VerifierIssueHeadConflict(
                "unsupported Target VerifierIssue lifecycle transition"
            )
        normalized_resolution = (
            str(resolution_ref).strip() if resolution_ref is not None else None
        )
        if target_status is VerifierIssueLifecycleStatus.RESOLVED:
            if not normalized_resolution:
                raise VerifierIssueHeadConflict(
                    "RESOLVED issue requires resolution_ref"
                )
        elif normalized_resolution:
            raise VerifierIssueHeadConflict(
                "resolution_ref is only valid for RESOLVED issue"
            )
        superseded_ref = (
            VerifierIssueRefSchema.model_validate(superseded_by_issue_ref)
            if superseded_by_issue_ref is not None
            else None
        )
        if target_status is VerifierIssueLifecycleStatus.SUPERSEDED:
            if superseded_ref is None:
                raise VerifierIssueHeadConflict(
                    "SUPERSEDED issue requires superseded_by_issue_ref"
                )
        elif superseded_ref is not None:
            raise VerifierIssueHeadConflict(
                "superseded_by_issue_ref only valid for SUPERSEDED"
            )

        operation_ref = self._operation_ref(
            operation_kind=VERIFIER_ISSUE_LIFECYCLE_TRANSITION,
            operation_identity={
                "issue_ref": issue_ref.model_dump(mode="json"),
                "target_status": target_status.value,
                "reason_ref": reason,
                "resolution_ref": normalized_resolution,
                "superseded_by_issue_ref": (
                    superseded_ref.model_dump(mode="json")
                    if superseded_ref is not None
                    else None
                ),
            },
            context=context,
        )
        fingerprint = canonical_sha256(
            {
                "contract": "verifier_issue_lifecycle_transition_v1",
                "issue_ref": issue_ref.model_dump(mode="json"),
                "expected_head_revision": expected_head_revision,
                "from_lifecycle": expected_lifecycle_status.value,
                "to_lifecycle": target_status.value,
                "reason_ref": reason,
                "resolution_ref": normalized_resolution,
                "superseded_by_issue_ref": (
                    superseded_ref.model_dump(mode="json")
                    if superseded_ref is not None
                    else None
                ),
            }
        )
        with self._lock, InterProcessFileLock(self.lock_path):
            state = self._load_state_unlocked()
            existing = state.operations.get(operation_ref.logical_operation_id)
            if existing is not None:
                if existing.semantic_fingerprint != fingerprint:
                    raise ClaimIdentityCollision(
                        "same VERIFIER_ISSUE_LIFECYCLE_TRANSITION changed semantics"
                    )
                if existing.event_type != "issue_lifecycle_transition":
                    raise ClaimVerificationIntegrityError(
                        "Issue lifecycle operation resolves wrong event"
                    )
                self._reconcile_coordination(existing)
                return (
                    VerifierIssueLifecycleTransitionReceiptSchema.model_validate(
                        existing.payload["receipt"]
                    )
                )
            self._validate_operation_context(context)
            current = state.issue_heads.get(issue_ref.issue_id)
            if current is None:
                raise VerifierIssueHeadConflict(
                    "Issue lifecycle transition requires current Head"
                )
            if (
                current.issue_revision != expected_head_revision
                or current.lifecycle_status is not expected_lifecycle_status
            ):
                raise VerifierIssueHeadConflict(
                    "VerifierIssue expected head is stale"
                )
            if superseded_ref is not None:
                if superseded_ref.issue_id == issue_ref.issue_id:
                    raise VerifierIssueHeadConflict(
                        "VerifierIssue cannot supersede itself"
                    )
                if superseded_ref.issue_id not in state.issues:
                    raise VerifierIssueHeadConflict(
                        "superseding VerifierIssue does not resolve"
                    )
            resulting = VerifierIssueHeadSchema(
                issue_ref=issue_ref,
                issue_revision=current.issue_revision + 1,
                lifecycle_status=target_status,
                resolution_ref=normalized_resolution,
                superseded_by_issue_ref=superseded_ref,
                head_updated_by_operation_id=operation_ref.logical_operation_id,
            )
            receipt = VerifierIssueLifecycleTransitionReceiptSchema(
                committed_operation_id=operation_ref.logical_operation_id,
                semantic_fingerprint=fingerprint,
                prior_head=current,
                resulting_head=resulting,
                reason_ref=reason,
            )
            result_ref = _result_ref("verifier-issue-transition", receipt)
            side_effect = self._claim_side_effect(operation_ref, fingerprint)
            canonical_appended = False
            try:
                self._append_event(
                    event_type="issue_lifecycle_transition",
                    operation_ref=operation_ref,
                    semantic_fingerprint=fingerprint,
                    result_ref=result_ref,
                    payload={"receipt": receipt.model_dump(mode="json")},
                )
                canonical_appended = True
                self._ack_side_effect(side_effect, result_ref)
            except Exception:
                if not canonical_appended:
                    self.coordination_store.release(
                        side_effect,
                        reason="issue_lifecycle_failed_before_canonical_append",
                    )
                raise
            return receipt

    def resolve_active_verified_claim(
        self,
        claim_ref: ClaimRefSchema,
        *, strict_currentness: bool = False,
    ) -> ActiveVerifiedClaimViewSchema:
        ref = ClaimRefSchema.model_validate(claim_ref)
        claim = self.resolve_claim(ref)
        with self._lock, InterProcessFileLock(self.lock_path):
            state = self._load_state_unlocked()
            head = state.claim_heads.get(ref.claim_id)
            if head is None:
                return ActiveVerifiedClaimViewSchema(
                    claim_ref=ref,
                    claim_head_current=False,
                    paragraph_self_current=False,
                    paragraph_membership_current=False,
                    binding_source_current=False,
                    verification_current=False,
                    lifecycle_active=False,
                    verdict_verified=False,
                    active_verified_claim=False,
                )
            revision = state.verification_revisions.get(
                (
                    ref.claim_id,
                    head.active_verification_revision_ref.verification_revision,
                )
            )
            verification = (
                state.verification_results.get(
                    revision.verification_result_ref.verification_result_id
                )
                if revision is not None
                else None
            )
            binding = (
                state.bindings.get(revision.binding_facts_ref.binding_facts_id)
                if revision is not None
                else None
            )

        claim_head_current = self.resolve_claim_head(ref) == head
        paragraph_self_current = False
        paragraph_membership_current = False
        try:
            artifact_head = self.paragraph_runtime.artifact_runtime.resolve_active(
                claim.paragraph_version_ref.logical_ref
            )
            paragraph_self_current = bool(
                artifact_head is not None
                and artifact_head.active_version_ref
                == claim.paragraph_version_ref
            )
            lineage = (self.read_lineage_resolver(claim) if self.read_lineage_resolver is not None
                       else self.lifecycle_service.resolve_active_lineage(
                           claim.claim_occurrence_ref.run_id, f"section:{claim.section_id}"))
            manifest_ref = self.paragraph_runtime.manifest_ref(
                run_id=claim.claim_occurrence_ref.run_id,
                section_id=claim.section_id,
                active_lineage=lineage,
                write_instance_id=lineage.attempt_id,
            )
            manifest = self.paragraph_runtime.load_manifest(manifest_ref)
            paragraph_membership_current = (
                claim.paragraph_version_ref in manifest.members
            )
        except Exception:
            if strict_currentness:
                raise
            paragraph_membership_current = False

        verification_current = bool(
            revision is not None
            and verification is not None
            and verification.verification_result_ref
            == revision.verification_result_ref
        )
        binding_source_current = False
        if binding is not None and verification is not None:
            try:
                lineage = (self.read_lineage_resolver(claim) if self.read_lineage_resolver is not None
                           else self.lifecycle_service.resolve_active_lineage(
                               claim.claim_occurrence_ref.run_id, f"section:{claim.section_id}"))
                (self.read_binding_source_validator or self.binding_source_validator)(
                    self._binding_record_candidate(binding),
                    lineage,
                )
                support = self._require_support_record(
                    verification.claim_evidence_support_ref
                )
                if self.verification_source_validator is None:
                    raise ClaimCurrentnessError(
                        "verification source validator is absent"
                    )
                (self.read_verification_source_validator or self.verification_source_validator)(
                    binding,
                    support,
                    lineage,
                )
                if self.verification_identity_validator is None:
                    raise ClaimCurrentnessError("Verifier V2 identity resolver is absent")
                self.verification_identity_validator(claim, binding, support, verification, lineage)
                binding_source_current = True
            except Exception:
                if strict_currentness:
                    raise
                binding_source_current = False
                verification_current = False

        verification_current = bool(verification_current and paragraph_self_current
                                    and paragraph_membership_current and binding_source_current)
        lifecycle_active = head.lifecycle_status is ClaimLifecycleStatus.ACTIVE
        verdict_verified = bool(
            verification is not None
            and verification.verdict is VerificationVerdict.VERIFIED
        )
        active = all(
            (
                claim_head_current,
                paragraph_self_current,
                paragraph_membership_current,
                binding_source_current,
                verification_current,
                lifecycle_active,
                verdict_verified,
            )
        )
        return ActiveVerifiedClaimViewSchema(
            claim_ref=ref,
            claim_head_current=claim_head_current,
            paragraph_self_current=paragraph_self_current,
            paragraph_membership_current=paragraph_membership_current,
            binding_source_current=binding_source_current,
            verification_current=verification_current,
            lifecycle_active=lifecycle_active,
            verdict_verified=verdict_verified,
            active_verified_claim=active,
        )


__all__ = [
    "CLAIM_BINDING_FACTS_COMMIT",
    "CLAIM_LIFECYCLE_TRANSITION",
    "CLAIM_RECORD_COMMIT",
    "CLAIM_VERIFICATION_COMMIT",
    "VERIFIER_ISSUE_LIFECYCLE_TRANSITION",
    "ClaimBindingError",
    "VerifierCandidateContractViolationError",
    "ClaimCoverageError",
    "ClaimCurrentnessError",
    "ClaimHeadConflict",
    "ClaimIdentityCollision",
    "ClaimOperationInProgress",
    "ClaimRecordCommitResult",
    "ClaimVerificationIntegrityError",
    "ClaimVerificationRuntime",
    "ClaimVerificationRuntimeError",
    "VerifierIssueHeadConflict",
]
