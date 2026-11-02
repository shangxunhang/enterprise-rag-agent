"""Canonical SIF-03 Artifact Runtime.

Artifact business truth lives in the durable Artifact ledger plus immutable content
materialization. SIF-02 coordination is consumed only as execution coordination;
Workflow State, checkpoint, Memory, and Trace are never currentness authorities here.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
import hashlib
import json
from core.diagnostics import current_diagnostics, diagnostic_operation
from pathlib import Path
import threading
from typing import Any

from apps.enterprise_document.schemas.agent_rag_v1.artifacts import (
    ArtifactActiveHeadSchema,
    ArtifactLogicalRefSchema,
    ArtifactPromotionReceiptSchema,
    ArtifactVersionRefSchema,
)
from apps.enterprise_document.schemas.agent_rag_v1.lifecycle import (
    ActiveExecutionLineageSchema,
)
from apps.enterprise_document.services.agent_rag_v1.artifact_store import (
    ArtifactIntegrityError,
    FileArtifactStore,
)
from apps.enterprise_document.services.agent_rag_v1.lifecycle_runtime import (
    LifecycleCurrentnessError,
    RunAttemptLifecycleService,
)
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


_EVENT_SCHEMA_VERSION = "artifact_runtime_event_v1"
_VERSION_OPERATION_KIND = "ARTIFACT_VERSION_COMMIT"
_PROMOTION_OPERATION_KIND = "ARTIFACT_ACTIVE_PROMOTION"


class ArtifactRuntimeError(ValueError):
    """Base failure for canonical Artifact runtime operations."""


class ArtifactIdentityCollision(ArtifactRuntimeError):
    """A stable Artifact or operation identity was reused incompatibly."""


class ArtifactVersionCollision(ArtifactRuntimeError):
    """An immutable Artifact version identity resolved to different material content."""


class ArtifactRuntimeIntegrityError(ArtifactRuntimeError):
    """Durable Artifact history/content is missing, corrupt, or contradictory."""


class ArtifactVersionNotFound(ArtifactRuntimeError):
    """The exact immutable Artifact version does not exist."""


class ArtifactCurrentnessError(ArtifactRuntimeError):
    """The requested active-head mutation is stale or invalid."""


class ArtifactOperationInProgress(ArtifactRuntimeError):
    """Another claimant currently coordinates the same SIF-02 logical operation."""


@dataclass(frozen=True)
class _StoredVersion:
    ref: ArtifactVersionRefSchema
    content_encoding: str
    result_ref: str
    version_ordinal: int


@dataclass(frozen=True)
class _StoredOperation:
    operation_ref: LogicalOperationRef
    semantic_fingerprint: str
    result_ref: str
    event_type: str


@dataclass
class _ArtifactLedgerState:
    logicals: dict[tuple[str, str], ArtifactLogicalRefSchema]
    versions: dict[tuple[str, str, str], _StoredVersion]
    version_ids_by_logical: dict[tuple[str, str], list[str]]
    heads: dict[tuple[str, str], ArtifactActiveHeadSchema]
    version_operations: dict[str, _StoredOperation]
    promotion_operations: dict[str, _StoredOperation]
    promotion_receipts: dict[str, ArtifactPromotionReceiptSchema]
    all_operations: dict[str, _StoredOperation]


def _logical_key(logical_ref: ArtifactLogicalRefSchema) -> tuple[str, str]:
    return (logical_ref.run_id, logical_ref.logical_artifact_id)


def _version_key(
    version_ref: ArtifactVersionRefSchema,
) -> tuple[str, str, str]:
    logical = version_ref.logical_ref
    return (
        logical.run_id,
        logical.logical_artifact_id,
        version_ref.artifact_version_id,
    )


def _canonical_digest(value: Any) -> str:
    serialized = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(serialized.encode("utf-8")).hexdigest()


def _operation_payload(operation_ref: LogicalOperationRef) -> dict[str, Any]:
    return asdict(operation_ref)


def _operation_from_payload(value: Any) -> LogicalOperationRef:
    if not isinstance(value, dict):
        raise ArtifactRuntimeIntegrityError("Artifact operation_ref must be an object")
    try:
        return LogicalOperationRef(**value)
    except Exception as exc:
        raise ArtifactRuntimeIntegrityError("Artifact operation_ref is invalid") from exc


def _version_result_ref(version_ref: ArtifactVersionRefSchema) -> str:
    digest = _canonical_digest(
        {
            "logical_ref": version_ref.logical_ref.model_dump(mode="json"),
            "artifact_version_id": version_ref.artifact_version_id,
        }
    )
    return f"artifact-version-ref://sha256/{digest}"


def _promotion_result_ref(receipt: ArtifactPromotionReceiptSchema) -> str:
    digest = _canonical_digest(receipt.model_dump(mode="json"))
    return f"artifact-promotion-receipt://sha256/{digest}"


def _deterministic_version_id(
    logical_ref: ArtifactLogicalRefSchema,
    operation_ref: LogicalOperationRef,
    semantic_fingerprint: str,
) -> str:
    digest = _canonical_digest(
        {
            "logical_ref": logical_ref.model_dump(mode="json"),
            "logical_operation_id": operation_ref.logical_operation_id,
            "semantic_fingerprint": semantic_fingerprint,
        }
    )
    return f"av_{digest}"


def _deterministic_content_ref(artifact_version_id: str) -> str:
    return f"artifact://runtime/{artifact_version_id}"


def _normalize_content(value: Any) -> tuple[bytes, str]:
    if isinstance(value, bytes):
        return value, "binary"
    if isinstance(value, bytearray):
        return bytes(value), "binary"
    if hasattr(value, "model_dump"):
        value = value.model_dump(mode="json", round_trip=True)
    try:
        content = json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise ArtifactRuntimeError(
            "typed Artifact content must be bytes or JSON-serializable"
        ) from exc
    return content, "json-utf8"


class ArtifactRuntime:
    """Single canonical owner for SIF-03 version/currentness semantics."""

    _LOCKS_GUARD = threading.Lock()
    _PATH_LOCKS: dict[str, threading.RLock] = {}

    def __init__(
        self,
        ledger_path: str | Path = "data/runtime/agent_rag_v1/artifact_runtime.jsonl",
        *,
        artifact_store: FileArtifactStore | None = None,
        lifecycle_service: RunAttemptLifecycleService | None = None,
        coordination_store: SideEffectCommitStore | None = None,
    ) -> None:
        self.ledger_path = Path(ledger_path)
        self.log = CrashSafeJsonlEventLog(self.ledger_path)
        self.lock_path = self.ledger_path.with_suffix(
            self.ledger_path.suffix + ".lock"
        )
        self.artifact_store = artifact_store or FileArtifactStore(
            self.ledger_path.parent / "artifacts"
        )
        self.lifecycle_service = lifecycle_service or RunAttemptLifecycleService()
        self.coordination_store = coordination_store or JsonlSideEffectCommitStore(
            self.ledger_path.with_suffix(self.ledger_path.suffix + ".coordination")
        )
        lock_key = str(self.ledger_path.resolve())
        with self._LOCKS_GUARD:
            self._lock = self._PATH_LOCKS.setdefault(lock_key, threading.RLock())
        self._verified_ledger_lines: list[str] | None = None
        self._verified_ledger_state: _ArtifactLedgerState | None = None

    @staticmethod
    def _validate_operation_kind(
        operation_ref: LogicalOperationRef,
        expected_kind: str,
    ) -> None:
        if operation_ref.operation_kind != expected_kind:
            raise ArtifactIdentityCollision(
                f"expected {expected_kind}, got {operation_ref.operation_kind}"
            )

    @staticmethod
    def _validate_operation_binding(
        logical_ref: ArtifactLogicalRefSchema,
        operation_ref: LogicalOperationRef,
    ) -> None:
        if operation_ref.run_id != logical_ref.run_id:
            raise ArtifactIdentityCollision(
                "Artifact logical_ref.run_id does not match operation_ref.run_id"
            )

    def _assert_commit_guard(
        self,
        operation_ref: LogicalOperationRef,
        commit_guard_context: ActiveExecutionLineageSchema | None,
    ) -> None:
        if operation_ref.attempt_scope_ref is None:
            if commit_guard_context is not None:
                raise ArtifactIdentityCollision(
                    "Run-level Artifact operation must not manufacture Attempt lineage"
                )
            self.lifecycle_service.assert_run_committable(operation_ref.run_id)
            return

        if commit_guard_context is None:
            raise LifecycleCurrentnessError(
                "attempt-scoped Artifact operation requires active lineage context"
            )
        if (
            commit_guard_context.run_id != operation_ref.run_id
            or commit_guard_context.attempt_scope_ref
            != operation_ref.attempt_scope_ref
            or commit_guard_context.attempt_id != operation_ref.attempt_id
        ):
            raise LifecycleCurrentnessError(
                "Artifact commit guard does not match operation Attempt lineage"
            )
        self.lifecycle_service.assert_attempt_committable(
            run_id=operation_ref.run_id,
            attempt_scope_ref=operation_ref.attempt_scope_ref,
            attempt_id=operation_ref.attempt_id or "",
            input_snapshot_version=commit_guard_context.input_snapshot_version,
        )

    @staticmethod
    def _copy_ledger_containers(state: _ArtifactLedgerState) -> _ArtifactLedgerState:
        # Leaves are frozen Artifact contracts / frozen operation records with
        # immutable fields. Only the replay's dictionaries and ordinal lists are
        # mutable; never let a caller change those in the verified snapshot.
        return _ArtifactLedgerState(
            logicals=dict(state.logicals),
            versions=dict(state.versions),
            version_ids_by_logical={key: list(ids) for key, ids in state.version_ids_by_logical.items()},
            heads=dict(state.heads),
            version_operations=dict(state.version_operations),
            promotion_operations=dict(state.promotion_operations),
            promotion_receipts=dict(state.promotion_receipts),
            all_operations=dict(state.all_operations),
        )

    @diagnostic_operation("artifact.load", "FS_PARSE_SCOPE")
    def _load_state_unlocked(self) -> _ArtifactLedgerState:
        # Keep reading the complete durable content under the existing locks.
        # Equal metadata is insufficient: same-size rewrites and peer commits
        # must still be detected. Recovery of a torn trailing frame also runs.
        with current_diagnostics().span("artifact.read", "FS_API_ELAPSED"):
            lines = self.log.read_complete_lines()
        if lines == self._verified_ledger_lines and self._verified_ledger_state is not None:
            return self._copy_ledger_containers(self._verified_ledger_state)
        state = _ArtifactLedgerState(
            logicals={},
            versions={},
            version_ids_by_logical={},
            heads={},
            version_operations={},
            promotion_operations={},
            promotion_receipts={},
            all_operations={},
        )
        for line_number, line in enumerate(
            lines,
            start=1,
        ):
            if not line.strip():
                continue
            try:
                payload = json.loads(line)
            except json.JSONDecodeError as exc:
                raise ArtifactRuntimeIntegrityError(
                    f"invalid Artifact ledger JSON at {self.ledger_path}:{line_number}"
                ) from exc
            if not isinstance(payload, dict):
                raise ArtifactRuntimeIntegrityError(
                    "Artifact ledger event must be an object"
                )
            if payload.get("schema_version") != _EVENT_SCHEMA_VERSION:
                raise ArtifactRuntimeIntegrityError(
                    "unknown Artifact ledger schema version"
                )

            event_type = payload.get("event_type")
            operation_ref = _operation_from_payload(payload.get("operation_ref"))
            try:
                fingerprint = validate_sif02_semantic_fingerprint(
                    payload.get("semantic_fingerprint")
                )
            except Exception as exc:
                raise ArtifactRuntimeIntegrityError(
                    "Artifact ledger semantic fingerprint is invalid"
                ) from exc

            if operation_ref.logical_operation_id in state.all_operations:
                raise ArtifactRuntimeIntegrityError(
                    "duplicate Artifact logical operation in canonical ledger"
                )

            if event_type == "version_committed":
                self._load_version_event(
                    state,
                    payload=payload,
                    operation_ref=operation_ref,
                    fingerprint=fingerprint,
                )
            elif event_type == "promotion_committed":
                self._load_promotion_event(
                    state,
                    payload=payload,
                    operation_ref=operation_ref,
                    fingerprint=fingerprint,
                )
            else:
                raise ArtifactRuntimeIntegrityError(
                    f"unknown Artifact ledger event_type: {event_type}"
                )

        # Publish a cache only after every original integrity/replay check passes.
        # A changed history is always replayed in full; content reads and live
        # lifecycle/currentness checks stay outside this derived ledger cache.
        if all(
            isinstance(value, (str, type(None)))
            for operation in state.all_operations.values()
            for value in vars(operation.operation_ref).values()
        ):
            self._verified_ledger_lines = lines
            self._verified_ledger_state = state
            return self._copy_ledger_containers(state)
        # The legacy dataclass decoder can accept non-scalar scope payloads.
        # Preserve that behavior without sharing a mutable leaf between reads.
        self._verified_ledger_lines = None
        self._verified_ledger_state = None
        return state

    @staticmethod
    def _register_logical(
        state: _ArtifactLedgerState,
        logical_ref: ArtifactLogicalRefSchema,
    ) -> tuple[str, str]:
        key = _logical_key(logical_ref)
        existing = state.logicals.get(key)
        if existing is not None and existing != logical_ref:
            raise ArtifactRuntimeIntegrityError(
                "Artifact logical identity collision in canonical ledger"
            )
        state.logicals[key] = logical_ref
        return key

    def _load_version_event(
        self,
        state: _ArtifactLedgerState,
        *,
        payload: dict[str, Any],
        operation_ref: LogicalOperationRef,
        fingerprint: str,
    ) -> None:
        if operation_ref.operation_kind != _VERSION_OPERATION_KIND:
            raise ArtifactRuntimeIntegrityError(
                "version event uses the wrong SIF-02 operation kind"
            )
        try:
            version_ref = ArtifactVersionRefSchema.model_validate(
                payload["version_ref"]
            )
        except Exception as exc:
            raise ArtifactRuntimeIntegrityError(
                "Artifact version_ref is invalid"
            ) from exc
        if operation_ref.run_id != version_ref.logical_ref.run_id:
            raise ArtifactRuntimeIntegrityError(
                "version operation Run does not match Artifact logical Run"
            )
        if version_ref.committed_by_operation_id != operation_ref.logical_operation_id:
            raise ArtifactRuntimeIntegrityError(
                "Artifact version committed_by_operation_id mismatch"
            )
        if version_ref.semantic_fingerprint != fingerprint:
            raise ArtifactRuntimeIntegrityError(
                "Artifact version semantic fingerprint mismatch"
            )
        if (
            version_ref.producer_attempt_scope_ref
            != operation_ref.attempt_scope_ref
            or version_ref.producer_attempt_id != operation_ref.attempt_id
        ):
            raise ArtifactRuntimeIntegrityError(
                "Artifact version Attempt provenance mismatch"
            )

        content_encoding = payload.get("content_encoding")
        if content_encoding not in {"json-utf8", "binary"}:
            raise ArtifactRuntimeIntegrityError(
                "Artifact version content encoding is invalid"
            )
        version_ordinal = payload.get("version_ordinal")
        logical_key = self._register_logical(state, version_ref.logical_ref)
        expected_ordinal = len(
            state.version_ids_by_logical.setdefault(logical_key, [])
        ) + 1
        if version_ordinal != expected_ordinal:
            raise ArtifactRuntimeIntegrityError(
                "Artifact version ordinal is non-monotonic"
            )

        key = _version_key(version_ref)
        if key in state.versions:
            raise ArtifactRuntimeIntegrityError(
                "duplicate immutable Artifact version identity"
            )
        result_ref = payload.get("result_ref")
        expected_result_ref = _version_result_ref(version_ref)
        if result_ref != expected_result_ref:
            raise ArtifactRuntimeIntegrityError(
                "Artifact version committed-result ref mismatch"
            )

        stored = _StoredVersion(
            ref=version_ref,
            content_encoding=content_encoding,
            result_ref=result_ref,
            version_ordinal=version_ordinal,
        )
        state.versions[key] = stored
        state.version_ids_by_logical[logical_key].append(
            version_ref.artifact_version_id
        )
        operation = _StoredOperation(
            operation_ref=operation_ref,
            semantic_fingerprint=fingerprint,
            result_ref=result_ref,
            event_type="version_committed",
        )
        state.version_operations[operation_ref.logical_operation_id] = operation
        state.all_operations[operation_ref.logical_operation_id] = operation
        state.heads.setdefault(
            logical_key,
            ArtifactActiveHeadSchema(
                logical_ref=version_ref.logical_ref,
                head_revision=0,
                active_version_ref=None,
            ),
        )

    def _load_promotion_event(
        self,
        state: _ArtifactLedgerState,
        *,
        payload: dict[str, Any],
        operation_ref: LogicalOperationRef,
        fingerprint: str,
    ) -> None:
        if operation_ref.operation_kind != _PROMOTION_OPERATION_KIND:
            raise ArtifactRuntimeIntegrityError(
                "promotion event uses the wrong SIF-02 operation kind"
            )
        try:
            receipt = ArtifactPromotionReceiptSchema.model_validate(
                payload["receipt"]
            )
        except Exception as exc:
            raise ArtifactRuntimeIntegrityError(
                "Artifact promotion receipt is invalid"
            ) from exc
        if operation_ref.run_id != receipt.logical_ref.run_id:
            raise ArtifactRuntimeIntegrityError(
                "promotion operation Run does not match Artifact logical Run"
            )
        if receipt.promotion_operation_id != operation_ref.logical_operation_id:
            raise ArtifactRuntimeIntegrityError(
                "Artifact promotion operation identity mismatch"
            )
        if receipt.semantic_fingerprint != fingerprint:
            raise ArtifactRuntimeIntegrityError(
                "Artifact promotion semantic fingerprint mismatch"
            )

        logical_key = self._register_logical(state, receipt.logical_ref)
        target_key = (
            receipt.logical_ref.run_id,
            receipt.logical_ref.logical_artifact_id,
            receipt.promoted_version_id,
        )
        target = state.versions.get(target_key)
        if target is None:
            raise ArtifactRuntimeIntegrityError(
                "Artifact promotion targets an unresolved version"
            )

        current = state.heads.get(
            logical_key,
            ArtifactActiveHeadSchema(
                logical_ref=receipt.logical_ref,
                head_revision=0,
                active_version_ref=None,
            ),
        )
        previous_id = (
            current.active_version_ref.artifact_version_id
            if current.active_version_ref is not None
            else None
        )
        if (
            receipt.previous_head_revision != current.head_revision
            or receipt.previous_active_version_id != previous_id
        ):
            raise ArtifactRuntimeIntegrityError(
                "Artifact promotion history does not form a valid currentness chain"
            )

        result_ref = payload.get("result_ref")
        expected_result_ref = _promotion_result_ref(receipt)
        if result_ref != expected_result_ref:
            raise ArtifactRuntimeIntegrityError(
                "Artifact promotion committed-result ref mismatch"
            )

        state.heads[logical_key] = ArtifactActiveHeadSchema(
            logical_ref=receipt.logical_ref,
            head_revision=receipt.resulting_head_revision,
            active_version_ref=target.ref,
        )
        operation = _StoredOperation(
            operation_ref=operation_ref,
            semantic_fingerprint=fingerprint,
            result_ref=result_ref,
            event_type="promotion_committed",
        )
        state.promotion_operations[operation_ref.logical_operation_id] = operation
        state.promotion_receipts[operation_ref.logical_operation_id] = receipt
        state.all_operations[operation_ref.logical_operation_id] = operation

    def _serialize_event(self, payload: dict[str, Any]) -> str:
        return json.dumps(
            payload,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )

    @staticmethod
    def _operation_matches(
        existing: _StoredOperation,
        operation_ref: LogicalOperationRef,
        fingerprint: str,
        *,
        event_type: str,
    ) -> bool:
        return (
            existing.event_type == event_type
            and existing.operation_ref == operation_ref
            and existing.semantic_fingerprint == fingerprint
        )

    def _physical_content(
        self,
        stored: _StoredVersion,
        *, run_identity=None,
    ) -> tuple[Any, ArtifactVersionRefSchema]:
        version_ref = stored.ref
        run_identity = run_identity or self.lifecycle_service.load_run_identity(version_ref.logical_ref.run_id)
        try:
            physical_ref, content = self.artifact_store.get_bytes_with_reference(
                version_ref.content_ref,
                task_id=run_identity.task_id,
                run_id=version_ref.logical_ref.run_id,
                attempt_id=version_ref.producer_attempt_id,
            )
        except (ArtifactIntegrityError, FileNotFoundError) as exc:
            raise ArtifactRuntimeIntegrityError(
                "canonical Artifact content is missing or corrupt"
            ) from exc

        if (
            physical_ref.artifact_type != version_ref.logical_ref.artifact_type
            or physical_ref.content_hash != version_ref.content_hash
            or physical_ref.schema_version != version_ref.content_schema_version
            or physical_ref.run_id != version_ref.logical_ref.run_id
        ):
            raise ArtifactRuntimeIntegrityError(
                "physical Artifact reference does not match canonical version metadata"
            )

        if stored.content_encoding == "binary":
            return content, version_ref
        try:
            return json.loads(content.decode("utf-8")), version_ref
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ArtifactRuntimeIntegrityError(
                "canonical JSON Artifact content is invalid"
            ) from exc

    def _canonical_resolution_from_state(
        self,
        state: _ArtifactLedgerState,
        operation_ref: LogicalOperationRef,
        fingerprint: str,
        *,
        event_type: str,
    ) -> CommittedResultResolution:
        existing = state.all_operations.get(operation_ref.logical_operation_id)
        if existing is None:
            return CommittedResultResolution(
                logical_operation_id=operation_ref.logical_operation_id,
                semantic_fingerprint=fingerprint,
                resolution=CommittedResultResolutionStatus.ABSENT,
            )
        if not self._operation_matches(
            existing,
            operation_ref,
            fingerprint,
            event_type=event_type,
        ):
            return CommittedResultResolution(
                logical_operation_id=operation_ref.logical_operation_id,
                semantic_fingerprint=fingerprint,
                resolution=CommittedResultResolutionStatus.COMMITTED_CONFLICT,
            )

        try:
            if event_type == "version_committed":
                stored = next(
                    version
                    for version in state.versions.values()
                    if version.result_ref == existing.result_ref
                )
                self._physical_content(stored)
            else:
                receipt = state.promotion_receipts[
                    operation_ref.logical_operation_id
                ]
                target = state.versions[
                    (
                        receipt.logical_ref.run_id,
                        receipt.logical_ref.logical_artifact_id,
                        receipt.promoted_version_id,
                    )
                ]
                self._physical_content(target)
        except Exception:
            return CommittedResultResolution(
                logical_operation_id=operation_ref.logical_operation_id,
                semantic_fingerprint=fingerprint,
                resolution=CommittedResultResolutionStatus.INTEGRITY_INVALID,
            )

        return CommittedResultResolution(
            logical_operation_id=operation_ref.logical_operation_id,
            semantic_fingerprint=fingerprint,
            resolution=CommittedResultResolutionStatus.COMMITTED_SAME,
            committed_result_refs=(existing.result_ref,),
        )

    def resolve_version_commit(
        self,
        operation_ref: LogicalOperationRef,
        semantic_fingerprint: str,
    ) -> CommittedResultResolution:
        self._validate_operation_kind(operation_ref, _VERSION_OPERATION_KIND)
        fingerprint = validate_sif02_semantic_fingerprint(semantic_fingerprint)
        with self._lock, InterProcessFileLock(self.lock_path):
            try:
                state = self._load_state_unlocked()
            except ArtifactRuntimeIntegrityError:
                return CommittedResultResolution(
                    logical_operation_id=operation_ref.logical_operation_id,
                    semantic_fingerprint=fingerprint,
                    resolution=CommittedResultResolutionStatus.INTEGRITY_INVALID,
                )
            return self._canonical_resolution_from_state(
                state,
                operation_ref,
                fingerprint,
                event_type="version_committed",
            )

    def resolve_promotion(
        self,
        operation_ref: LogicalOperationRef,
        semantic_fingerprint: str,
    ) -> CommittedResultResolution:
        self._validate_operation_kind(operation_ref, _PROMOTION_OPERATION_KIND)
        fingerprint = validate_sif02_semantic_fingerprint(semantic_fingerprint)
        with self._lock, InterProcessFileLock(self.lock_path):
            try:
                state = self._load_state_unlocked()
            except ArtifactRuntimeIntegrityError:
                return CommittedResultResolution(
                    logical_operation_id=operation_ref.logical_operation_id,
                    semantic_fingerprint=fingerprint,
                    resolution=CommittedResultResolutionStatus.INTEGRITY_INVALID,
                )
            return self._canonical_resolution_from_state(
                state,
                operation_ref,
                fingerprint,
                event_type="promotion_committed",
            )

    def resolve_committed_result(
        self,
        operation_ref: LogicalOperationRef,
        semantic_fingerprint: str,
    ) -> CommittedResultResolution:
        if operation_ref.operation_kind == _VERSION_OPERATION_KIND:
            return self.resolve_version_commit(operation_ref, semantic_fingerprint)
        if operation_ref.operation_kind == _PROMOTION_OPERATION_KIND:
            return self.resolve_promotion(operation_ref, semantic_fingerprint)
        raise ArtifactIdentityCollision(
            "ArtifactRuntime only resolves Artifact version/promotion operations"
        )

    def _claim_coordination(
        self,
        operation_ref: LogicalOperationRef,
        fingerprint: str,
    ) -> SideEffectClaim:
        try:
            claim = self.coordination_store.claim(
                operation_ref.logical_operation_id,
                execution_id=f"artifact-runtime:{threading.get_ident()}",
                description=operation_ref.operation_kind,
                semantic_fingerprint=fingerprint,
            )
        except SideEffectIdentityConflict as exc:
            raise ArtifactIdentityCollision(
                "SIF-02 coordination identity collision"
            ) from exc
        if claim.status is SideEffectClaimStatus.ALREADY_COMMITTED:
            raise ArtifactRuntimeIntegrityError(
                "SIF-02 coordination says committed while Artifact canonical source is absent"
            )
        if claim.status is SideEffectClaimStatus.ALREADY_IN_PROGRESS:
            raise ArtifactOperationInProgress(
                "Artifact logical operation is already in progress"
            )
        return claim

    def _ack_coordination(
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
                    raise ArtifactRuntimeIntegrityError(
                        "SIF-02 coordination acknowledgement conflicts with Artifact truth"
                    )
        except SideEffectIdentityConflict as exc:
            raise ArtifactIdentityCollision(
                "SIF-02 coordination fingerprint collision"
            ) from exc

    def _reconcile_coordination(
        self,
        operation_ref: LogicalOperationRef,
        fingerprint: str,
        result_ref: str,
    ) -> None:
        try:
            resolution = self.coordination_store.resolve(
                operation_ref.logical_operation_id,
                semantic_fingerprint=fingerprint,
            )
        except SideEffectIdentityConflict as exc:
            raise ArtifactIdentityCollision(
                "SIF-02 coordination fingerprint collision"
            ) from exc

        if resolution.status is SideEffectCoordinationStatus.COMMITTED:
            if resolution.committed_result_refs != (result_ref,):
                raise ArtifactRuntimeIntegrityError(
                    "SIF-02 committed-result refs disagree with canonical Artifact truth"
                )
            return
        if resolution.status is SideEffectCoordinationStatus.IN_PROGRESS:
            # Canonical Artifact truth wins. The bounded coordination lease may be
            # reconciled later; never rerun or roll back the already committed result.
            return

        claim = self.coordination_store.claim(
            operation_ref.logical_operation_id,
            execution_id="artifact-runtime:canonical-reconcile",
            description=f"reconcile:{operation_ref.operation_kind}",
            semantic_fingerprint=fingerprint,
        )
        if claim.status is SideEffectClaimStatus.ACQUIRED:
            self._ack_coordination(claim, result_ref)
            return
        if claim.status is SideEffectClaimStatus.ALREADY_COMMITTED:
            if claim.committed_result_refs != (result_ref,):
                raise ArtifactRuntimeIntegrityError(
                    "reconciled SIF-02 result refs disagree with Artifact truth"
                )

    def _existing_version_for_operation(
        self,
        state: _ArtifactLedgerState,
        operation_ref: LogicalOperationRef,
    ) -> _StoredVersion | None:
        operation = state.version_operations.get(operation_ref.logical_operation_id)
        if operation is None:
            return None
        for stored in state.versions.values():
            if stored.result_ref == operation.result_ref:
                return stored
        raise ArtifactRuntimeIntegrityError(
            "Artifact version operation points to no canonical version"
        )

    def _canonical_state_after_ambiguous_write_failure(
        self,
        operation_ref: LogicalOperationRef,
        fingerprint: str,
        *,
        event_type: str,
    ) -> CommittedResultResolution | None:
        """Re-resolve Artifact truth before deciding a failed append was uncommitted.

        A storage primitive may report an exception after bytes reached its durable
        commit point. In that ambiguous window the SIF-02 claim must not be released
        until the canonical Artifact source is re-read. None means the source itself
        cannot be trusted, so the caller fails closed and preserves the claim.
        """

        try:
            state = self._load_state_unlocked()
        except ArtifactRuntimeIntegrityError:
            return None
        return self._canonical_resolution_from_state(
            state,
            operation_ref,
            fingerprint,
            event_type=event_type,
        )

    def commit_version(
        self,
        logical_ref: ArtifactLogicalRefSchema,
        typed_content_or_content_source: Any,
        content_schema_version: str,
        operation_ref: LogicalOperationRef,
        semantic_fingerprint: str,
        commit_guard_context: ActiveExecutionLineageSchema | None,
    ) -> ArtifactVersionRefSchema:
        self._validate_operation_kind(operation_ref, _VERSION_OPERATION_KIND)
        self._validate_operation_binding(logical_ref, operation_ref)
        fingerprint = validate_sif02_semantic_fingerprint(semantic_fingerprint)
        content_schema_version = str(content_schema_version or "").strip()
        if not content_schema_version:
            raise ArtifactRuntimeError("content_schema_version must be non-empty")
        content, content_encoding = _normalize_content(
            typed_content_or_content_source
        )
        expected_content_hash = (
            "sha256:" + hashlib.sha256(content).hexdigest()
        )

        self._assert_commit_guard(operation_ref, commit_guard_context)

        with self._lock, InterProcessFileLock(self.lock_path):
            state = self._load_state_unlocked()
            resolution = self._canonical_resolution_from_state(
                state,
                operation_ref,
                fingerprint,
                event_type="version_committed",
            )
            if resolution.resolution is CommittedResultResolutionStatus.COMMITTED_CONFLICT:
                raise ArtifactIdentityCollision(
                    "Artifact version logical operation conflicts with canonical history"
                )
            if resolution.resolution is CommittedResultResolutionStatus.INTEGRITY_INVALID:
                raise ArtifactRuntimeIntegrityError(
                    "canonical Artifact version commit is integrity-invalid"
                )
            if resolution.resolution is CommittedResultResolutionStatus.COMMITTED_SAME:
                stored = self._existing_version_for_operation(state, operation_ref)
                if stored is None:
                    raise ArtifactRuntimeIntegrityError(
                        "canonical Artifact version result is unresolved"
                    )
                if (
                    stored.ref.logical_ref != logical_ref
                    or stored.ref.content_schema_version != content_schema_version
                    or stored.ref.content_hash != expected_content_hash
                ):
                    raise ArtifactVersionCollision(
                        "same Artifact operation/fingerprint was replayed with different material content"
                    )
                self._reconcile_coordination(
                    operation_ref,
                    fingerprint,
                    stored.result_ref,
                )
                return stored.ref

            logical_key = _logical_key(logical_ref)
            canonical_logical = state.logicals.get(logical_key)
            if canonical_logical is not None and canonical_logical != logical_ref:
                raise ArtifactIdentityCollision(
                    "logical Artifact key already exists with different immutable attributes"
                )

            self._assert_commit_guard(operation_ref, commit_guard_context)
            claim = self._claim_coordination(operation_ref, fingerprint)
            committed = False
            result_ref = ""
            try:
                # SIF-01 lineage is revalidated immediately before canonical write.
                self._assert_commit_guard(operation_ref, commit_guard_context)
                run_identity = self.lifecycle_service.load_run_identity(
                    logical_ref.run_id
                )
                version_id = _deterministic_version_id(
                    logical_ref,
                    operation_ref,
                    fingerprint,
                )
                content_ref = _deterministic_content_ref(version_id)
                physical_ref = self.artifact_store.put_bytes(
                    content,
                    artifact_type=logical_ref.artifact_type,
                    schema_version=content_schema_version,
                    created_by="artifact_runtime",
                    task_id=run_identity.task_id,
                    run_id=logical_ref.run_id,
                    attempt_id=operation_ref.attempt_id,
                    input_snapshot_version=(
                        commit_guard_context.input_snapshot_version
                        if commit_guard_context is not None
                        else None
                    ),
                    artifact_ref=content_ref,
                    content_encoding=content_encoding,
                )
                if physical_ref.content_hash != expected_content_hash:
                    raise ArtifactRuntimeIntegrityError(
                        "physical Artifact hash changed during commit"
                    )

                version_ref = ArtifactVersionRefSchema(
                    logical_ref=logical_ref,
                    artifact_version_id=version_id,
                    content_ref=content_ref,
                    content_hash=physical_ref.content_hash,
                    content_schema_version=content_schema_version,
                    committed_by_operation_id=operation_ref.logical_operation_id,
                    semantic_fingerprint=fingerprint,
                    producer_attempt_scope_ref=operation_ref.attempt_scope_ref,
                    producer_attempt_id=operation_ref.attempt_id,
                )
                logical_key = _logical_key(logical_ref)
                version_ordinal = len(
                    state.version_ids_by_logical.get(logical_key, [])
                ) + 1
                result_ref = _version_result_ref(version_ref)
                self.log.append_serialized(
                    self._serialize_event(
                        {
                            "schema_version": _EVENT_SCHEMA_VERSION,
                            "event_type": "version_committed",
                            "operation_ref": _operation_payload(operation_ref),
                            "semantic_fingerprint": fingerprint,
                            "version_ref": version_ref.model_dump(mode="json"),
                            "content_encoding": content_encoding,
                            "version_ordinal": version_ordinal,
                            "result_ref": result_ref,
                        }
                    )
                )
                committed = True
                verified_state = self._load_state_unlocked()
                verified = self._existing_version_for_operation(
                    verified_state,
                    operation_ref,
                )
                if verified is None or verified.ref != version_ref:
                    raise ArtifactRuntimeIntegrityError(
                        "durable Artifact version readback verification failed"
                    )
                self._ack_coordination(claim, result_ref)
                return version_ref
            except Exception:
                if committed:
                    # Canonical source may already be committed. Never release the
                    # coordination claim in that state; retry must resolve source first.
                    raise
                resolution_after_failure = (
                    self._canonical_state_after_ambiguous_write_failure(
                        operation_ref,
                        fingerprint,
                        event_type="version_committed",
                    )
                )
                if (
                    resolution_after_failure is not None
                    and resolution_after_failure.resolution
                    is CommittedResultResolutionStatus.ABSENT
                ):
                    self.coordination_store.release(
                        claim,
                        reason="artifact_version_commit_failed_before_canonical_commit",
                    )
                raise

    def read_version(
        self,
        version_ref: ArtifactVersionRefSchema,
    ) -> tuple[Any, ArtifactVersionRefSchema]:
        # Immutable scope identity is resolved before the Artifact owner fence.
        # Finalization reads must never nest Artifact -> lifecycle locks.
        run_identity = self.lifecycle_service.load_run_identity(version_ref.logical_ref.run_id)
        with self._lock, InterProcessFileLock(self.lock_path):
            state = self._load_state_unlocked()
            stored = state.versions.get(_version_key(version_ref))
            if stored is None:
                raise ArtifactVersionNotFound(
                    "canonical Artifact version does not exist"
                )
            if stored.ref != version_ref:
                raise ArtifactRuntimeIntegrityError(
                    "supplied ArtifactVersionRef does not match canonical metadata"
                )
            return self._physical_content(stored, run_identity=run_identity)

    def resolve_active(
        self,
        logical_ref: ArtifactLogicalRefSchema,
    ) -> ArtifactActiveHeadSchema | None:
        run_identity = self.lifecycle_service.load_run_identity(logical_ref.run_id)
        with self._lock, InterProcessFileLock(self.lock_path):
            state = self._load_state_unlocked()
            key = _logical_key(logical_ref)
            canonical_logical = state.logicals.get(key)
            if canonical_logical is None:
                return None
            if canonical_logical != logical_ref:
                raise ArtifactIdentityCollision(
                    "logical Artifact key resolves to different immutable attributes"
                )
            head = state.heads.get(
                key,
                ArtifactActiveHeadSchema(
                    logical_ref=logical_ref,
                    head_revision=0,
                    active_version_ref=None,
                ),
            )
            if head.active_version_ref is not None:
                stored = state.versions.get(_version_key(head.active_version_ref))
                if stored is None:
                    raise ArtifactRuntimeIntegrityError(
                        "Artifact head points to a missing version"
                    )
                self._physical_content(stored, run_identity=run_identity)
            return head

    def promote_active(
        self,
        logical_ref: ArtifactLogicalRefSchema,
        target_version_ref: ArtifactVersionRefSchema,
        expected_head_revision: int,
        expected_active_version_id: str | None,
        operation_ref: LogicalOperationRef,
        semantic_fingerprint: str,
        commit_guard_context: ActiveExecutionLineageSchema | None,
    ) -> ArtifactPromotionReceiptSchema:
        self._validate_operation_kind(operation_ref, _PROMOTION_OPERATION_KIND)
        self._validate_operation_binding(logical_ref, operation_ref)
        fingerprint = validate_sif02_semantic_fingerprint(semantic_fingerprint)
        if target_version_ref.logical_ref != logical_ref:
            raise ArtifactIdentityCollision(
                "promotion target belongs to a different logical Artifact"
            )
        if expected_head_revision < 0:
            raise ArtifactCurrentnessError(
                "expected_head_revision must be non-negative"
            )
        normalized_expected_active = (
            str(expected_active_version_id).strip()
            if expected_active_version_id is not None
            else None
        )
        if expected_active_version_id is not None and not normalized_expected_active:
            raise ArtifactCurrentnessError(
                "expected_active_version_id cannot be blank"
            )

        self._assert_commit_guard(operation_ref, commit_guard_context)

        with self._lock, InterProcessFileLock(self.lock_path):
            state = self._load_state_unlocked()
            resolution = self._canonical_resolution_from_state(
                state,
                operation_ref,
                fingerprint,
                event_type="promotion_committed",
            )
            if resolution.resolution is CommittedResultResolutionStatus.COMMITTED_CONFLICT:
                raise ArtifactIdentityCollision(
                    "Artifact promotion logical operation conflicts with canonical history"
                )
            if resolution.resolution is CommittedResultResolutionStatus.INTEGRITY_INVALID:
                raise ArtifactRuntimeIntegrityError(
                    "canonical Artifact promotion is integrity-invalid"
                )
            if resolution.resolution is CommittedResultResolutionStatus.COMMITTED_SAME:
                receipt = state.promotion_receipts[
                    operation_ref.logical_operation_id
                ]
                if (
                    receipt.logical_ref != logical_ref
                    or receipt.promoted_version_id
                    != target_version_ref.artifact_version_id
                    or receipt.previous_head_revision != expected_head_revision
                    or receipt.previous_active_version_id
                    != normalized_expected_active
                ):
                    raise ArtifactIdentityCollision(
                        "same promotion operation/fingerprint was replayed with different semantic inputs"
                    )
                result_ref = state.promotion_operations[
                    operation_ref.logical_operation_id
                ].result_ref
                self._reconcile_coordination(
                    operation_ref,
                    fingerprint,
                    result_ref,
                )
                return receipt

            stored_target = state.versions.get(_version_key(target_version_ref))
            if stored_target is None:
                raise ArtifactVersionNotFound(
                    "promotion target version is not canonically committed"
                )
            if stored_target.ref != target_version_ref:
                raise ArtifactRuntimeIntegrityError(
                    "promotion target metadata differs from canonical version"
                )
            self._physical_content(stored_target)

            logical_key = _logical_key(logical_ref)
            canonical_logical = state.logicals.get(logical_key)
            if canonical_logical != logical_ref:
                raise ArtifactIdentityCollision(
                    "promotion logical Artifact identity is unresolved or colliding"
                )
            current = state.heads.get(
                logical_key,
                ArtifactActiveHeadSchema(
                    logical_ref=logical_ref,
                    head_revision=0,
                    active_version_ref=None,
                ),
            )
            current_id = (
                current.active_version_ref.artifact_version_id
                if current.active_version_ref is not None
                else None
            )
            if current.head_revision != expected_head_revision:
                raise ArtifactCurrentnessError(
                    "EXPECTED_HEAD_REVISION_STALE"
                )
            if (
                normalized_expected_active is not None
                and current_id != normalized_expected_active
            ):
                raise ArtifactCurrentnessError(
                    "EXPECTED_ACTIVE_VERSION_STALE"
                )
            if current_id == target_version_ref.artifact_version_id:
                raise ArtifactCurrentnessError(
                    "target Artifact version is already current"
                )

            claim = self._claim_coordination(operation_ref, fingerprint)
            committed = False
            try:
                # Revalidate lifecycle immediately before the currentness mutation.
                self._assert_commit_guard(operation_ref, commit_guard_context)
                refreshed = self._load_state_unlocked()
                refreshed_current = refreshed.heads.get(
                    logical_key,
                    ArtifactActiveHeadSchema(
                        logical_ref=logical_ref,
                        head_revision=0,
                        active_version_ref=None,
                    ),
                )
                refreshed_id = (
                    refreshed_current.active_version_ref.artifact_version_id
                    if refreshed_current.active_version_ref is not None
                    else None
                )
                if refreshed_current.head_revision != expected_head_revision:
                    raise ArtifactCurrentnessError(
                        "EXPECTED_HEAD_REVISION_STALE"
                    )
                if (
                    normalized_expected_active is not None
                    and refreshed_id != normalized_expected_active
                ):
                    raise ArtifactCurrentnessError(
                        "EXPECTED_ACTIVE_VERSION_STALE"
                    )

                receipt = ArtifactPromotionReceiptSchema(
                    logical_ref=logical_ref,
                    promotion_operation_id=operation_ref.logical_operation_id,
                    semantic_fingerprint=fingerprint,
                    previous_active_version_id=refreshed_id,
                    promoted_version_id=target_version_ref.artifact_version_id,
                    previous_head_revision=refreshed_current.head_revision,
                    resulting_head_revision=refreshed_current.head_revision + 1,
                )
                result_ref = _promotion_result_ref(receipt)
                self.log.append_serialized(
                    self._serialize_event(
                        {
                            "schema_version": _EVENT_SCHEMA_VERSION,
                            "event_type": "promotion_committed",
                            "operation_ref": _operation_payload(operation_ref),
                            "semantic_fingerprint": fingerprint,
                            "receipt": receipt.model_dump(mode="json"),
                            "result_ref": result_ref,
                        }
                    )
                )
                committed = True
                verified_state = self._load_state_unlocked()
                if (
                    verified_state.promotion_receipts.get(
                        operation_ref.logical_operation_id
                    )
                    != receipt
                ):
                    raise ArtifactRuntimeIntegrityError(
                        "durable Artifact promotion readback verification failed"
                    )
                self._ack_coordination(claim, result_ref)
                return receipt
            except Exception:
                if committed:
                    raise
                resolution_after_failure = (
                    self._canonical_state_after_ambiguous_write_failure(
                        operation_ref,
                        fingerprint,
                        event_type="promotion_committed",
                    )
                )
                if (
                    resolution_after_failure is not None
                    and resolution_after_failure.resolution
                    is CommittedResultResolutionStatus.ABSENT
                ):
                    self.coordination_store.release(
                        claim,
                        reason="artifact_promotion_failed_before_canonical_commit",
                    )
                raise


__all__ = [
    "ArtifactCurrentnessError",
    "ArtifactIdentityCollision",
    "ArtifactOperationInProgress",
    "ArtifactRuntime",
    "ArtifactRuntimeError",
    "ArtifactRuntimeIntegrityError",
    "ArtifactVersionCollision",
    "ArtifactVersionNotFound",
]
