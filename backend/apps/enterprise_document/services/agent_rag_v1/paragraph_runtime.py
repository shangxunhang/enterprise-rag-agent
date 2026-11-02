"""Canonical SIF-06 Paragraph runtime over SIF-03 ArtifactRuntime.

ParagraphRuntime owns Paragraph Domain specialization and the minimal
SectionParagraphManifest membership/order cursor. Immutable Paragraph bytes and
ActiveHead currentness remain exclusively owned by ArtifactRuntime.
"""

from __future__ import annotations

from dataclasses import asdict
import json
from pathlib import Path
import threading
from typing import Callable, Iterable

from apps.enterprise_document.schemas.agent_rag_v1.artifacts import (
    ArtifactLogicalRefSchema,
    ArtifactPromotionReceiptSchema,
    ArtifactVersionRefSchema,
)
from apps.enterprise_document.schemas.agent_rag_v1.lifecycle import (
    ActiveExecutionLineageSchema,
)
from apps.enterprise_document.schemas.agent_rag_v1.paragraphs import (
    ParagraphBindingSchema,
    ParagraphCandidateSchema,
    ParagraphContentSchema,
    ParagraphCreationOccurrenceRefSchema,
    ParagraphOperationRefSchema,
    ParagraphWriterSourceBindingSchema,
    SectionParagraphManifestCommitReceiptSchema,
    SectionParagraphManifestOperationRefSchema,
    SectionParagraphManifestRefSchema,
    SectionParagraphManifestSchema,
)
from apps.enterprise_document.schemas.pre_write_writer import WriterLogicalExecutionKey
from apps.enterprise_document.services.agent_rag_v1.artifact_runtime import (
    ArtifactIdentityCollision,
    ArtifactRuntime,
    ArtifactRuntimeIntegrityError,
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
    SideEffectCoordinationStatus,
    SideEffectIdentityConflict,
    SideEffectCommitStore,
    validate_sif02_semantic_fingerprint,
)


PARAGRAPH_ARTIFACT_TYPE = "paragraph"
PARAGRAPH_CONTENT_SCHEMA_VERSION = "paragraph_content_v1"
PARAGRAPH_VERSION_COMMIT = "PARAGRAPH_VERSION_COMMIT"
PARAGRAPH_ACTIVE_PROMOTION = "PARAGRAPH_ACTIVE_PROMOTION"
SECTION_PARAGRAPH_MANIFEST_UPDATE = "SECTION_PARAGRAPH_MANIFEST_UPDATE"
SECTION_PARAGRAPH_MANIFEST_SEAL = "SECTION_PARAGRAPH_MANIFEST_SEAL"

_EVENT_SCHEMA_VERSION = "paragraph_manifest_event_v1"
_ARTIFACT_VERSION_COMMIT = "ARTIFACT_VERSION_COMMIT"
_ARTIFACT_ACTIVE_PROMOTION = "ARTIFACT_ACTIVE_PROMOTION"


class ParagraphRuntimeError(ValueError):
    """Base SIF-06 Paragraph runtime failure."""


class ParagraphIdentityCollision(ParagraphRuntimeError):
    """One replay-stable Paragraph/Manifest operation changed semantics."""


class ParagraphCurrentnessError(ParagraphRuntimeError):
    """Paragraph ActiveHead or Manifest membership is stale/incoherent."""


class ParagraphSourceBindingError(ParagraphRuntimeError):
    """Writer/Plan/Fact/Evidence/Context provenance cannot be proven."""


class ParagraphManifestConflict(ParagraphRuntimeError):
    """Manifest expected-revision/member CAS failed."""


class ParagraphManifestIntegrityError(ParagraphRuntimeError):
    """Canonical Manifest history is corrupt or contradictory."""


class ParagraphOperationInProgress(ParagraphRuntimeError):
    """Another executor currently owns the same Manifest logical operation."""


ParagraphAuthorizationValidator = Callable[
    [ParagraphOperationRefSchema, ActiveExecutionLineageSchema],
    None,
]
ParagraphSourceValidator = Callable[
    [ParagraphWriterSourceBindingSchema, ActiveExecutionLineageSchema],
    None,
]
ManifestAuthorizationValidator = Callable[
    [SectionParagraphManifestOperationRefSchema, ActiveExecutionLineageSchema],
    None,
]


def _ref_key(ref: SectionParagraphManifestRefSchema) -> tuple[str, str, str, str, str]:
    return (
        ref.run_id,
        ref.section_id,
        ref.attempt_scope_ref,
        ref.attempt_id,
        ref.write_instance_id,
    )


def _member_sequence_sha256(
    members: Iterable[ArtifactVersionRefSchema],
) -> str:
    return canonical_sha256(
        [item.model_dump(mode="json") for item in tuple(members)]
    )


def _receipt_result_ref(
    receipt: SectionParagraphManifestCommitReceiptSchema,
) -> str:
    return (
        "section-paragraph-manifest-receipt://sha256/"
        + canonical_sha256(receipt.model_dump(mode="json"))
    )


class ParagraphRuntime:
    """SIF-06 Domain owner; SIF-03 remains Paragraph version/head authority."""

    _LOCKS_GUARD = threading.Lock()
    _LOCKS: dict[str, threading.RLock] = {}

    def __init__(
        self,
        *,
        artifact_runtime: ArtifactRuntime,
        authorization_validator: ParagraphAuthorizationValidator,
        source_validator: ParagraphSourceValidator,
        manifest_authorization_validator: ManifestAuthorizationValidator,
        manifest_ledger_path: str | Path = (
            "data/runtime/agent_rag_v1/paragraph_manifest.jsonl"
        ),
        coordination_store: SideEffectCommitStore | None = None,
    ) -> None:
        if not callable(authorization_validator):
            raise TypeError("ParagraphRuntime requires authorization_validator")
        if not callable(source_validator):
            raise TypeError("ParagraphRuntime requires source_validator")
        if not callable(manifest_authorization_validator):
            raise TypeError(
                "ParagraphRuntime requires manifest_authorization_validator"
            )
        self.artifact_runtime = artifact_runtime
        self.lifecycle_service = artifact_runtime.lifecycle_service
        self.authorization_validator = authorization_validator
        self.source_validator = source_validator
        self.manifest_authorization_validator = manifest_authorization_validator
        self.manifest_ledger_path = Path(manifest_ledger_path)
        self.manifest_log = CrashSafeJsonlEventLog(self.manifest_ledger_path)
        self.manifest_lock_path = self.manifest_ledger_path.with_suffix(
            self.manifest_ledger_path.suffix + ".lock"
        )
        self.coordination_store = coordination_store or JsonlSideEffectCommitStore(
            self.manifest_ledger_path.with_suffix(
                self.manifest_ledger_path.suffix + ".coordination"
            )
        )
        lock_key = str(self.manifest_ledger_path.resolve())
        with self._LOCKS_GUARD:
            self._lock = self._LOCKS.setdefault(lock_key, threading.RLock())

    # ------------------------------------------------------------------
    # Deterministic Paragraph identity / operation lowering
    # ------------------------------------------------------------------

    @staticmethod
    def initial_writer_authorization_ref(
        writer_key: WriterLogicalExecutionKey,
    ) -> str:
        key = WriterLogicalExecutionKey.model_validate(writer_key)
        return (
            "writer-logical-execution://sha256/"
            + canonical_sha256(key.model_dump(mode="json"))
        )

    @staticmethod
    def creation_occurrence(
        *,
        run_id: str,
        section_id: str,
        write_instance_id: str,
        authorization_kind: str,
        authorization_ref: str,
        creation_slot_ref: str,
    ) -> ParagraphCreationOccurrenceRefSchema:
        return ParagraphCreationOccurrenceRefSchema(
            run_id=run_id,
            section_id=section_id,
            write_instance_id=write_instance_id,
            authorization_kind=authorization_kind,
            authorization_ref=authorization_ref,
            creation_slot_ref=creation_slot_ref,
        )

    @staticmethod
    def logical_ref_for_creation(
        occurrence: ParagraphCreationOccurrenceRefSchema,
    ) -> ArtifactLogicalRefSchema:
        normalized = ParagraphCreationOccurrenceRefSchema.model_validate(occurrence)
        return ArtifactLogicalRefSchema(
            run_id=normalized.run_id,
            logical_artifact_id=(
                "paragraph:"
                + canonical_sha256(
                    {
                        "contract": "paragraph_logical_v1",
                        "creation_occurrence": normalized.model_dump(mode="json"),
                    }
                )
            ),
            artifact_type=PARAGRAPH_ARTIFACT_TYPE,
            domain_scope_ref=f"section:{normalized.section_id}",
        )

    @classmethod
    def create_operation(
        cls,
        occurrence: ParagraphCreationOccurrenceRefSchema,
        *,
        operation_kind: str = "CREATE",
    ) -> ParagraphOperationRefSchema:
        if operation_kind not in {"CREATE", "STRUCTURAL_CREATE"}:
            raise ParagraphRuntimeError(
                "creation operation_kind must be CREATE or STRUCTURAL_CREATE"
            )
        occurrence = ParagraphCreationOccurrenceRefSchema.model_validate(occurrence)
        logical_ref = cls.logical_ref_for_creation(occurrence)
        operation_id = "paragraph-op:" + canonical_sha256(
            {
                "contract": "paragraph_operation_v1",
                "creation_occurrence": occurrence.model_dump(mode="json"),
                "operation_kind": operation_kind,
            }
        )
        return ParagraphOperationRefSchema(
            run_id=occurrence.run_id,
            section_id=occurrence.section_id,
            write_instance_id=occurrence.write_instance_id,
            paragraph_operation_id=operation_id,
            operation_kind=operation_kind,
            target_logical_ref=logical_ref,
            creation_occurrence_ref=occurrence,
            authorization_kind=occurrence.authorization_kind,
            authorization_ref=occurrence.authorization_ref,
        )

    @staticmethod
    def revise_operation(
        *,
        run_id: str,
        section_id: str,
        write_instance_id: str,
        target_logical_ref: ArtifactLogicalRefSchema,
        base_version_ref: ArtifactVersionRefSchema,
        authorization_ref: str,
    ) -> ParagraphOperationRefSchema:
        operation_id = "paragraph-op:" + canonical_sha256(
            {
                "contract": "paragraph_revise_operation_v1",
                "run_id": run_id,
                "section_id": section_id,
                "write_instance_id": write_instance_id,
                "target_logical_ref": target_logical_ref.model_dump(mode="json"),
                "base_version_ref": base_version_ref.model_dump(mode="json"),
                "authorization_kind": "REPAIR",
                "authorization_ref": authorization_ref,
            }
        )
        return ParagraphOperationRefSchema(
            run_id=run_id,
            section_id=section_id,
            write_instance_id=write_instance_id,
            paragraph_operation_id=operation_id,
            operation_kind="REVISE",
            target_logical_ref=target_logical_ref,
            base_version_ref=base_version_ref,
            authorization_kind="REPAIR",
            authorization_ref=authorization_ref,
        )

    @staticmethod
    def _paragraph_domain_operation(
        operation: ParagraphOperationRefSchema,
        *,
        operation_kind: str,
        lineage: ActiveExecutionLineageSchema,
    ) -> LogicalOperationRef:
        if operation_kind not in {
            PARAGRAPH_VERSION_COMMIT,
            PARAGRAPH_ACTIVE_PROMOTION,
        }:
            raise ParagraphRuntimeError("unknown Paragraph domain operation kind")
        return LogicalOperationRef(
            logical_operation_id=(
                "paragraph-domain-op:"
                + canonical_sha256(
                    {
                        "kind": operation_kind,
                        "operation": operation.model_dump(mode="json"),
                    }
                )
            ),
            operation_kind=operation_kind,
            run_id=operation.run_id,
            operation_scope_ref=operation.paragraph_operation_id,
            attempt_scope_ref=lineage.attempt_scope_ref,
            attempt_id=lineage.attempt_id,
        )

    @staticmethod
    def _artifact_child_operation(
        domain_operation: LogicalOperationRef,
        *,
        artifact_operation_kind: str,
    ) -> LogicalOperationRef:
        if artifact_operation_kind not in {
            _ARTIFACT_VERSION_COMMIT,
            _ARTIFACT_ACTIVE_PROMOTION,
        }:
            raise ParagraphRuntimeError("unknown Artifact child operation kind")
        return LogicalOperationRef(
            logical_operation_id=(
                "paragraph-artifact-child:"
                + canonical_sha256(
                    {
                        "parent_domain_operation": asdict(domain_operation),
                        "artifact_operation_kind": artifact_operation_kind,
                    }
                )
            ),
            operation_kind=artifact_operation_kind,
            run_id=domain_operation.run_id,
            operation_scope_ref=domain_operation.operation_scope_ref,
            attempt_scope_ref=domain_operation.attempt_scope_ref,
            attempt_id=domain_operation.attempt_id,
        )

    @staticmethod
    def semantic_fingerprint(candidate: ParagraphCandidateSchema) -> str:
        candidate = ParagraphCandidateSchema.model_validate(candidate)
        return canonical_sha256(
            {
                "contract": "paragraph_version_semantics_v1",
                "logical_ref": candidate.operation_ref.target_logical_ref.model_dump(
                    mode="json"
                ),
                "content_schema_version": PARAGRAPH_CONTENT_SCHEMA_VERSION,
                "content_contract_version": candidate.content_contract_version,
                "operation_ref": candidate.operation_ref.model_dump(mode="json"),
                "role": candidate.role,
                "text": candidate.text,
                "source_binding": candidate.source_binding.model_dump(mode="json"),
            }
        )

    @staticmethod
    def promotion_fingerprint(
        *,
        candidate: ParagraphCandidateSchema,
        target_version_ref: ArtifactVersionRefSchema,
        expected_head_revision: int,
        expected_active_version_id: str | None,
    ) -> str:
        candidate = ParagraphCandidateSchema.model_validate(candidate)
        return canonical_sha256(
            {
                "contract": "paragraph_active_promotion_v1",
                "operation_ref": candidate.operation_ref.model_dump(mode="json"),
                "target_version_ref": target_version_ref.model_dump(mode="json"),
                "expected_head_revision": expected_head_revision,
                "expected_active_version_id": expected_active_version_id,
                "source_binding": candidate.source_binding.model_dump(mode="json"),
            }
        )

    # ------------------------------------------------------------------
    # Paragraph version / ActiveHead
    # ------------------------------------------------------------------

    def _validate_lineage(
        self,
        operation: ParagraphOperationRefSchema,
        lineage: ActiveExecutionLineageSchema,
    ) -> None:
        lineage = ActiveExecutionLineageSchema.model_validate(lineage)
        if (
            operation.run_id != lineage.run_id
            or lineage.attempt_scope_ref != f"section:{operation.section_id}"
            or operation.write_instance_id != lineage.attempt_id
        ):
            raise ParagraphCurrentnessError(
                "Paragraph operation does not match active SIF-01 Attempt"
            )
        self.lifecycle_service.assert_attempt_committable(
            run_id=lineage.run_id,
            attempt_scope_ref=lineage.attempt_scope_ref,
            attempt_id=lineage.attempt_id,
            input_snapshot_version=lineage.input_snapshot_version,
        )

    def _validate_candidate(
        self,
        candidate: ParagraphCandidateSchema,
        lineage: ActiveExecutionLineageSchema,
    ) -> ParagraphCandidateSchema:
        candidate = ParagraphCandidateSchema.model_validate(candidate)
        operation = candidate.operation_ref
        expected_logical = operation.target_logical_ref
        if operation.operation_kind in {"CREATE", "STRUCTURAL_CREATE"}:
            occurrence = operation.creation_occurrence_ref
            if occurrence is None:
                raise ParagraphIdentityCollision(
                    "Paragraph creation occurrence is absent"
                )
            if self.logical_ref_for_creation(occurrence) != expected_logical:
                raise ParagraphIdentityCollision(
                    "Paragraph logical identity does not match creation occurrence"
                )
            expected_operation = self.create_operation(
                occurrence,
                operation_kind=operation.operation_kind,
            )
            if expected_operation != operation:
                raise ParagraphIdentityCollision(
                    "Paragraph CREATE operation identity is not deterministic"
                )
        else:
            expected_operation = self.revise_operation(
                run_id=operation.run_id,
                section_id=operation.section_id,
                write_instance_id=operation.write_instance_id,
                target_logical_ref=operation.target_logical_ref,
                base_version_ref=operation.base_version_ref,
                authorization_ref=operation.authorization_ref,
            )
            if expected_operation != operation:
                raise ParagraphIdentityCollision(
                    "Paragraph REVISE operation identity is not deterministic"
                )
        self._validate_lineage(operation, lineage)
        try:
            self.authorization_validator(operation, lineage)
            self.source_validator(candidate.source_binding, lineage)
        except ParagraphRuntimeError:
            raise
        except Exception as exc:
            raise ParagraphSourceBindingError(
                "Paragraph source/authorization validation failed"
            ) from exc

        source = candidate.source_binding
        if (
            source.writer_subject_ref.run_id != operation.run_id
            or source.writer_subject_ref.section_id != operation.section_id
            or source.write_instance_id != operation.write_instance_id
        ):
            raise ParagraphSourceBindingError(
                "Paragraph source binding disagrees with operation identity"
            )
        if operation.authorization_kind == "INITIAL_WRITER":
            writer_key = WriterLogicalExecutionKey(
                writer_subject_ref=source.writer_subject_ref,
                write_instance_id=source.write_instance_id,
                route_decision_ref=source.route_decision_ref,
            )
            expected_authorization = self.initial_writer_authorization_ref(writer_key)
            if operation.authorization_ref != expected_authorization:
                raise ParagraphSourceBindingError(
                    "Paragraph INITIAL_WRITER authorization does not bind its exact Writer key"
                )
        return candidate

    def commit_paragraph_version(
        self,
        *,
        candidate: ParagraphCandidateSchema,
        active_lineage: ActiveExecutionLineageSchema,
    ) -> ArtifactVersionRefSchema:
        candidate = self._validate_candidate(candidate, active_lineage)
        operation = candidate.operation_ref
        logical_ref = operation.target_logical_ref
        if operation.operation_kind == "REVISE":
            head = self.artifact_runtime.resolve_active(logical_ref)
            base = operation.base_version_ref
            if (
                base is None
                or head is None
                or head.active_version_ref != base
            ):
                raise ParagraphCurrentnessError(
                    "Paragraph REVISE base is not the exact active version"
                )
        fingerprint = self.semantic_fingerprint(candidate)
        domain_op = self._paragraph_domain_operation(
            operation,
            operation_kind=PARAGRAPH_VERSION_COMMIT,
            lineage=active_lineage,
        )
        child_op = self._artifact_child_operation(
            domain_op,
            artifact_operation_kind=_ARTIFACT_VERSION_COMMIT,
        )
        content = ParagraphContentSchema(
            content_contract_version=candidate.content_contract_version,
            role=candidate.role,
            text=candidate.text,
            source_binding=candidate.source_binding,
        )
        self._validate_candidate(candidate, active_lineage)
        return self.artifact_runtime.commit_version(
            logical_ref,
            content,
            PARAGRAPH_CONTENT_SCHEMA_VERSION,
            child_op,
            fingerprint,
            active_lineage,
        )

    def promote_paragraph_active(
        self,
        *,
        candidate: ParagraphCandidateSchema,
        target_version_ref: ArtifactVersionRefSchema,
        expected_head_revision: int,
        expected_active_version_id: str | None,
        active_lineage: ActiveExecutionLineageSchema,
    ) -> ArtifactPromotionReceiptSchema:
        candidate = self._validate_candidate(candidate, active_lineage)
        operation = candidate.operation_ref
        if target_version_ref.logical_ref != operation.target_logical_ref:
            raise ParagraphCurrentnessError(
                "Paragraph promotion target belongs to another logical Paragraph"
            )
        if target_version_ref.content_schema_version != PARAGRAPH_CONTENT_SCHEMA_VERSION:
            raise ParagraphCurrentnessError(
                "Paragraph promotion target has wrong content schema"
            )
        if operation.operation_kind == "REVISE":
            base = operation.base_version_ref
            if (
                base is None
                or expected_active_version_id != base.artifact_version_id
            ):
                raise ParagraphCurrentnessError(
                    "Paragraph REVISE promotion must compare against exact base"
                )
        fingerprint = self.promotion_fingerprint(
            candidate=candidate,
            target_version_ref=target_version_ref,
            expected_head_revision=expected_head_revision,
            expected_active_version_id=expected_active_version_id,
        )
        domain_op = self._paragraph_domain_operation(
            operation,
            operation_kind=PARAGRAPH_ACTIVE_PROMOTION,
            lineage=active_lineage,
        )
        child_op = self._artifact_child_operation(
            domain_op,
            artifact_operation_kind=_ARTIFACT_ACTIVE_PROMOTION,
        )
        self._validate_candidate(candidate, active_lineage)
        # Membership is deliberately unresolved until a distinct Manifest CAS
        # succeeds. Promotion therefore returns only the SIF-03 receipt; callers
        # obtain ParagraphBinding later through resolve_paragraph_binding().
        return self.artifact_runtime.promote_active(
            operation.target_logical_ref,
            target_version_ref,
            expected_head_revision,
            expected_active_version_id,
            child_op,
            fingerprint,
            active_lineage,
        )

    def recover_paragraph_commit(
        self,
        operation_ref: LogicalOperationRef,
        semantic_fingerprint: str,
    ) -> CommittedResultResolution:
        """Resolve a Paragraph domain op through its unique SIF-03 child op."""

        fingerprint = validate_sif02_semantic_fingerprint(semantic_fingerprint)
        if operation_ref.operation_kind == PARAGRAPH_VERSION_COMMIT:
            child_kind = _ARTIFACT_VERSION_COMMIT
        elif operation_ref.operation_kind == PARAGRAPH_ACTIVE_PROMOTION:
            child_kind = _ARTIFACT_ACTIVE_PROMOTION
        else:
            raise ParagraphRuntimeError(
                "recover_paragraph_commit requires Paragraph version/promotion op"
            )
        child = self._artifact_child_operation(
            operation_ref,
            artifact_operation_kind=child_kind,
        )
        resolved = self.artifact_runtime.resolve_committed_result(
            child,
            fingerprint,
        )
        return CommittedResultResolution(
            logical_operation_id=operation_ref.logical_operation_id,
            semantic_fingerprint=fingerprint,
            resolution=resolved.resolution,
            committed_result_refs=resolved.committed_result_refs,
        )

    def read_paragraph(
        self,
        version_ref: ArtifactVersionRefSchema,
    ) -> ParagraphContentSchema:
        if (
            version_ref.logical_ref.artifact_type != PARAGRAPH_ARTIFACT_TYPE
            or version_ref.content_schema_version != PARAGRAPH_CONTENT_SCHEMA_VERSION
        ):
            raise ParagraphCurrentnessError("not a canonical Paragraph version")
        raw, canonical_ref = self.artifact_runtime.read_version(version_ref)
        if canonical_ref != version_ref:
            raise ParagraphCurrentnessError(
                "Paragraph Artifact resolver returned another version"
            )
        return ParagraphContentSchema.model_validate(raw)

    # ------------------------------------------------------------------
    # SectionParagraphManifest
    # ------------------------------------------------------------------

    @staticmethod
    def manifest_ref(
        *,
        run_id: str,
        section_id: str,
        active_lineage: ActiveExecutionLineageSchema,
        write_instance_id: str,
    ) -> SectionParagraphManifestRefSchema:
        return SectionParagraphManifestRefSchema(
            run_id=run_id,
            section_id=section_id,
            attempt_scope_ref=active_lineage.attempt_scope_ref,
            attempt_id=active_lineage.attempt_id,
            write_instance_id=write_instance_id,
        )

    @staticmethod
    def manifest_operation(
        *,
        manifest_ref: SectionParagraphManifestRefSchema,
        operation_kind: str,
        authorization_ref: str,
        stable_slot_ref: str,
    ) -> SectionParagraphManifestOperationRefSchema:
        if operation_kind not in {
            SECTION_PARAGRAPH_MANIFEST_UPDATE,
            SECTION_PARAGRAPH_MANIFEST_SEAL,
        }:
            raise ParagraphRuntimeError("unknown Manifest operation kind")
        op_id = "paragraph-manifest-op:" + canonical_sha256(
            {
                "contract": "section_paragraph_manifest_operation_v1",
                "manifest_ref": manifest_ref.model_dump(mode="json"),
                "operation_kind": operation_kind,
                "authorization_ref": authorization_ref,
                "stable_slot_ref": stable_slot_ref,
            }
        )
        return SectionParagraphManifestOperationRefSchema(
            manifest_ref=manifest_ref,
            manifest_operation_id=op_id,
            operation_kind=operation_kind,
            authorization_ref=authorization_ref,
        )

    @staticmethod
    def manifest_logical_operation(
        operation: SectionParagraphManifestOperationRefSchema,
    ) -> LogicalOperationRef:
        op = SectionParagraphManifestOperationRefSchema.model_validate(operation)
        return LogicalOperationRef(
            logical_operation_id=(
                "paragraph-manifest-domain:"
                + canonical_sha256(
                    {
                        "operation": op.model_dump(mode="json"),
                    }
                )
            ),
            operation_kind=op.operation_kind,
            run_id=op.manifest_ref.run_id,
            operation_scope_ref=op.manifest_operation_id,
            attempt_scope_ref=op.manifest_ref.attempt_scope_ref,
            attempt_id=op.manifest_ref.attempt_id,
        )

    def _manifest_initial(
        self,
        ref: SectionParagraphManifestRefSchema,
    ) -> SectionParagraphManifestSchema:
        return SectionParagraphManifestSchema(
            manifest_ref=ref,
            manifest_revision=0,
            generation_membership_state="OPEN",
            members=(),
        )

    def _load_manifest_state_unlocked(
        self,
    ) -> tuple[
        dict[tuple[str, str, str, str, str], SectionParagraphManifestSchema],
        dict[
            str,
            tuple[
                SectionParagraphManifestCommitReceiptSchema,
                str,
            ],
        ],
    ]:
        manifests: dict[
            tuple[str, str, str, str, str],
            SectionParagraphManifestSchema,
        ] = {}
        operations: dict[
            str,
            tuple[SectionParagraphManifestCommitReceiptSchema, str],
        ] = {}
        for line_number, line in enumerate(
            self.manifest_log.read_complete_lines(),
            start=1,
        ):
            if not line.strip():
                continue
            try:
                payload = json.loads(line)
            except json.JSONDecodeError as exc:
                raise ParagraphManifestIntegrityError(
                    f"invalid Manifest JSON at line {line_number}"
                ) from exc
            if (
                not isinstance(payload, dict)
                or payload.get("schema_version") != _EVENT_SCHEMA_VERSION
                or payload.get("event_type") != "manifest_committed"
            ):
                raise ParagraphManifestIntegrityError(
                    "unknown Paragraph Manifest event"
                )
            try:
                receipt = SectionParagraphManifestCommitReceiptSchema.model_validate(
                    payload["receipt"]
                )
            except Exception as exc:
                raise ParagraphManifestIntegrityError(
                    "invalid Paragraph Manifest receipt"
                ) from exc
            result_ref = str(payload.get("result_ref") or "").strip()
            if result_ref != _receipt_result_ref(receipt):
                raise ParagraphManifestIntegrityError(
                    "Paragraph Manifest receipt result ref mismatch"
                )
            logical_op = self.manifest_logical_operation(receipt.operation_ref)
            existing = operations.get(logical_op.logical_operation_id)
            if existing is not None:
                raise ParagraphManifestIntegrityError(
                    "duplicate Paragraph Manifest logical operation"
                )
            ref = receipt.operation_ref.manifest_ref
            key = _ref_key(ref)
            current = manifests.get(key, self._manifest_initial(ref))
            if (
                current.manifest_revision != receipt.prior_manifest_revision
                or current.generation_membership_state
                != receipt.prior_generation_membership_state
                or _member_sequence_sha256(current.members)
                != receipt.prior_member_sequence_sha256
            ):
                raise ParagraphManifestIntegrityError(
                    "Paragraph Manifest history is non-contiguous"
                )
            if (
                _member_sequence_sha256(receipt.resulting_members)
                != receipt.resulting_member_sequence_sha256
            ):
                raise ParagraphManifestIntegrityError(
                    "Paragraph Manifest resulting member hash mismatch"
                )
            resulting = SectionParagraphManifestSchema(
                manifest_ref=ref,
                manifest_revision=receipt.resulting_manifest_revision,
                generation_membership_state=(
                    receipt.resulting_generation_membership_state
                ),
                members=receipt.resulting_members,
            )
            manifests[key] = resulting
            operations[logical_op.logical_operation_id] = (receipt, result_ref)
        return manifests, operations

    def load_manifest(
        self,
        manifest_ref: SectionParagraphManifestRefSchema,
    ) -> SectionParagraphManifestSchema:
        ref = SectionParagraphManifestRefSchema.model_validate(manifest_ref)
        with self._lock, InterProcessFileLock(self.manifest_lock_path):
            manifests, _ = self._load_manifest_state_unlocked()
            return manifests.get(_ref_key(ref), self._manifest_initial(ref))

    def _validate_manifest_lineage(
        self,
        manifest_ref: SectionParagraphManifestRefSchema,
        active_lineage: ActiveExecutionLineageSchema,
    ) -> None:
        if (
            manifest_ref.run_id != active_lineage.run_id
            or manifest_ref.attempt_scope_ref != active_lineage.attempt_scope_ref
            or manifest_ref.attempt_id != active_lineage.attempt_id
            or manifest_ref.write_instance_id != active_lineage.attempt_id
        ):
            raise ParagraphCurrentnessError(
                "Paragraph Manifest does not match active SIF-01 Attempt"
            )
        self.lifecycle_service.assert_attempt_committable(
            run_id=active_lineage.run_id,
            attempt_scope_ref=active_lineage.attempt_scope_ref,
            attempt_id=active_lineage.attempt_id,
            input_snapshot_version=active_lineage.input_snapshot_version,
        )

    def _validate_manifest_members(
        self,
        manifest_ref: SectionParagraphManifestRefSchema,
        members: tuple[ArtifactVersionRefSchema, ...],
    ) -> None:
        SectionParagraphManifestSchema(
            manifest_ref=manifest_ref,
            manifest_revision=max(1, len(members)),
            generation_membership_state="OPEN",
            members=members,
        )
        for member in members:
            head = self.artifact_runtime.resolve_active(member.logical_ref)
            if (
                head is None
                or head.active_version_ref != member
            ):
                raise ParagraphCurrentnessError(
                    "Manifest can only publish exact active Paragraph versions"
                )
            self.read_paragraph(member)

    @staticmethod
    def manifest_update_fingerprint(
        *,
        operation: SectionParagraphManifestOperationRefSchema,
        expected_manifest_revision: int,
        expected_generation_membership_state: str,
        expected_members: tuple[ArtifactVersionRefSchema, ...],
        resulting_members: tuple[ArtifactVersionRefSchema, ...],
        mutation_kind: str,
    ) -> str:
        return canonical_sha256(
            {
                "contract": "section_paragraph_manifest_update_v1",
                "operation": operation.model_dump(mode="json"),
                "expected_manifest_revision": expected_manifest_revision,
                "expected_generation_membership_state": (
                    expected_generation_membership_state
                ),
                "expected_members": [
                    item.model_dump(mode="json") for item in expected_members
                ],
                "mutation_kind": str(mutation_kind or "").strip(),
                "resulting_members": [
                    item.model_dump(mode="json") for item in resulting_members
                ],
                "resulting_generation_membership_state": (
                    expected_generation_membership_state
                ),
            }
        )

    @staticmethod
    def manifest_seal_fingerprint(
        *,
        operation: SectionParagraphManifestOperationRefSchema,
        expected_manifest_revision: int,
        expected_members: tuple[ArtifactVersionRefSchema, ...],
    ) -> str:
        return canonical_sha256(
            {
                "contract": "section_paragraph_manifest_seal_v1",
                "operation": operation.model_dump(mode="json"),
                "expected_manifest_revision": expected_manifest_revision,
                "expected_generation_membership_state": "OPEN",
                "expected_members": [
                    item.model_dump(mode="json") for item in expected_members
                ],
                "resulting_generation_membership_state": "SEALED",
            }
        )

    def resolve_manifest_commit(
        self,
        operation_ref: LogicalOperationRef,
        semantic_fingerprint: str,
    ) -> CommittedResultResolution:
        fingerprint = validate_sif02_semantic_fingerprint(semantic_fingerprint)
        if operation_ref.operation_kind not in {
            SECTION_PARAGRAPH_MANIFEST_UPDATE,
            SECTION_PARAGRAPH_MANIFEST_SEAL,
        }:
            raise ParagraphRuntimeError("not a Paragraph Manifest operation")
        with self._lock, InterProcessFileLock(self.manifest_lock_path):
            _, operations = self._load_manifest_state_unlocked()
            existing = operations.get(operation_ref.logical_operation_id)
            if existing is None:
                return CommittedResultResolution(
                    operation_ref.logical_operation_id,
                    fingerprint,
                    CommittedResultResolutionStatus.ABSENT,
                )
            receipt, result_ref = existing
            expected_logical = self.manifest_logical_operation(
                receipt.operation_ref
            )
            if expected_logical != operation_ref:
                return CommittedResultResolution(
                    operation_ref.logical_operation_id,
                    fingerprint,
                    CommittedResultResolutionStatus.INTEGRITY_INVALID,
                )
            if receipt.semantic_fingerprint != fingerprint:
                return CommittedResultResolution(
                    operation_ref.logical_operation_id,
                    fingerprint,
                    CommittedResultResolutionStatus.COMMITTED_CONFLICT,
                )
            return CommittedResultResolution(
                operation_ref.logical_operation_id,
                fingerprint,
                CommittedResultResolutionStatus.COMMITTED_SAME,
                (result_ref,),
            )

    def _claim_manifest(
        self,
        operation_ref: LogicalOperationRef,
        fingerprint: str,
    ) -> SideEffectClaim:
        try:
            claim = self.coordination_store.claim(
                operation_ref.logical_operation_id,
                execution_id=f"paragraph-runtime:{threading.get_ident()}",
                description=operation_ref.operation_kind,
                semantic_fingerprint=fingerprint,
            )
        except SideEffectIdentityConflict as exc:
            raise ParagraphIdentityCollision(
                "Paragraph Manifest SIF-02 coordination collision"
            ) from exc
        if claim.status is SideEffectClaimStatus.ALREADY_COMMITTED:
            raise ParagraphManifestIntegrityError(
                "coordination says committed while Manifest canonical source is absent"
            )
        if claim.status is SideEffectClaimStatus.ALREADY_IN_PROGRESS:
            raise ParagraphOperationInProgress(
                "Paragraph Manifest operation is already in progress"
            )
        return claim

    def _ack_manifest(
        self,
        claim: SideEffectClaim,
        result_ref: str,
    ) -> None:
        try:
            if not self.coordination_store.mark_committed(
                claim,
                committed_result_refs=(result_ref,),
            ):
                resolved = self.coordination_store.resolve(
                    claim.side_effect_id,
                    semantic_fingerprint=claim.semantic_fingerprint,
                )
                if (
                    resolved.status is not SideEffectCoordinationStatus.COMMITTED
                    or resolved.committed_result_refs != (result_ref,)
                ):
                    raise ParagraphManifestIntegrityError(
                        "Paragraph Manifest coordination acknowledgement conflicts"
                    )
        except SideEffectIdentityConflict as exc:
            raise ParagraphIdentityCollision(
                "Paragraph Manifest coordination fingerprint collision"
            ) from exc

    def _reconcile_manifest_coordination(
        self,
        operation_ref: LogicalOperationRef,
        fingerprint: str,
        result_ref: str,
    ) -> None:
        try:
            resolved = self.coordination_store.resolve(
                operation_ref.logical_operation_id,
                semantic_fingerprint=fingerprint,
            )
        except SideEffectIdentityConflict as exc:
            raise ParagraphIdentityCollision(
                "Paragraph Manifest coordination fingerprint collision"
            ) from exc
        if resolved.status is SideEffectCoordinationStatus.COMMITTED:
            if resolved.committed_result_refs != (result_ref,):
                raise ParagraphManifestIntegrityError(
                    "Paragraph Manifest coordination result disagrees with canonical receipt"
                )
            return
        if resolved.status is SideEffectCoordinationStatus.IN_PROGRESS:
            # Canonical Manifest truth wins over a lagging bounded coordination
            # lease. Never rerun/roll back an already committed membership effect.
            return
        claim = self.coordination_store.claim(
            operation_ref.logical_operation_id,
            execution_id="paragraph-runtime:canonical-reconcile",
            description=f"reconcile:{operation_ref.operation_kind}",
            semantic_fingerprint=fingerprint,
        )
        if claim.status is SideEffectClaimStatus.ACQUIRED:
            self._ack_manifest(claim, result_ref)
            return
        if claim.status is SideEffectClaimStatus.ALREADY_COMMITTED:
            if claim.committed_result_refs != (result_ref,):
                raise ParagraphManifestIntegrityError(
                    "reconciled Manifest refs disagree with canonical receipt"
                )
            return
        raise ParagraphOperationInProgress(
            "Paragraph Manifest coordination reconciliation is still in progress"
        )

    def _append_manifest_receipt(
        self,
        receipt: SectionParagraphManifestCommitReceiptSchema,
    ) -> str:
        result_ref = _receipt_result_ref(receipt)
        payload = {
            "schema_version": _EVENT_SCHEMA_VERSION,
            "event_type": "manifest_committed",
            "receipt": receipt.model_dump(mode="json"),
            "result_ref": result_ref,
        }
        self.manifest_log.append_serialized(
            json.dumps(
                payload,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            )
        )
        return result_ref

    def update_manifest(
        self,
        *,
        operation: SectionParagraphManifestOperationRefSchema,
        expected_manifest_revision: int,
        expected_generation_membership_state: str,
        expected_members: Iterable[ArtifactVersionRefSchema],
        resulting_members: Iterable[ArtifactVersionRefSchema],
        mutation_kind: str,
        active_lineage: ActiveExecutionLineageSchema,
    ) -> SectionParagraphManifestCommitReceiptSchema:
        operation = SectionParagraphManifestOperationRefSchema.model_validate(operation)
        if operation.operation_kind != SECTION_PARAGRAPH_MANIFEST_UPDATE:
            raise ParagraphRuntimeError("update_manifest requires UPDATE operation")
        expected = tuple(expected_members)
        resulting = tuple(resulting_members)
        if not str(mutation_kind or "").strip():
            raise ParagraphRuntimeError("Manifest mutation_kind is required")
        self._validate_manifest_lineage(operation.manifest_ref, active_lineage)
        try:
            self.manifest_authorization_validator(operation, active_lineage)
        except ParagraphRuntimeError:
            raise
        except Exception as exc:
            raise ParagraphSourceBindingError(
                "Manifest update authorization validation failed"
            ) from exc
        fingerprint = self.manifest_update_fingerprint(
            operation=operation,
            expected_manifest_revision=expected_manifest_revision,
            expected_generation_membership_state=(
                expected_generation_membership_state
            ),
            expected_members=expected,
            resulting_members=resulting,
            mutation_kind=mutation_kind,
        )
        logical_op = self.manifest_logical_operation(operation)

        with self._lock, InterProcessFileLock(self.manifest_lock_path):
            manifests, operations = self._load_manifest_state_unlocked()
            existing = operations.get(logical_op.logical_operation_id)
            if existing is not None:
                receipt, result_ref = existing
                if receipt.semantic_fingerprint != fingerprint:
                    raise ParagraphIdentityCollision(
                        "same Manifest UPDATE operation changed semantics"
                    )
                self._reconcile_manifest_coordination(
                    logical_op,
                    fingerprint,
                    result_ref,
                )
                return receipt

            current = manifests.get(
                _ref_key(operation.manifest_ref),
                self._manifest_initial(operation.manifest_ref),
            )
            if (
                current.manifest_revision != expected_manifest_revision
                or current.generation_membership_state
                != expected_generation_membership_state
                or current.members != expected
            ):
                raise ParagraphManifestConflict(
                    "Paragraph Manifest expected state is stale"
                )
            self._validate_manifest_lineage(operation.manifest_ref, active_lineage)
            self._validate_manifest_members(operation.manifest_ref, resulting)
            claim = self._claim_manifest(logical_op, fingerprint)
            canonical_appended = False
            try:
                self._validate_manifest_lineage(
                    operation.manifest_ref,
                    active_lineage,
                )
                receipt = SectionParagraphManifestCommitReceiptSchema(
                    operation_ref=operation,
                    semantic_fingerprint=fingerprint,
                    prior_manifest_revision=current.manifest_revision,
                    resulting_manifest_revision=current.manifest_revision + 1,
                    prior_generation_membership_state=(
                        current.generation_membership_state
                    ),
                    resulting_generation_membership_state=(
                        current.generation_membership_state
                    ),
                    prior_member_sequence_sha256=_member_sequence_sha256(
                        current.members
                    ),
                    resulting_members=resulting,
                    resulting_member_sequence_sha256=_member_sequence_sha256(
                        resulting
                    ),
                )
                result_ref = self._append_manifest_receipt(receipt)
                canonical_appended = True
                self._ack_manifest(claim, result_ref)
                return receipt
            except Exception:
                # Once the canonical line is committed it wins over a lagging
                # SIF-02 acknowledgement; never release/reapply that mutation.
                if not canonical_appended:
                    self.coordination_store.release(
                        claim,
                        reason="manifest_update_failed_before_canonical_commit",
                    )
                raise

    def seal_manifest(
        self,
        *,
        operation: SectionParagraphManifestOperationRefSchema,
        expected_manifest_revision: int,
        expected_members: Iterable[ArtifactVersionRefSchema],
        active_lineage: ActiveExecutionLineageSchema,
    ) -> SectionParagraphManifestCommitReceiptSchema:
        operation = SectionParagraphManifestOperationRefSchema.model_validate(operation)
        if operation.operation_kind != SECTION_PARAGRAPH_MANIFEST_SEAL:
            raise ParagraphRuntimeError("seal_manifest requires SEAL operation")
        expected = tuple(expected_members)
        if not expected:
            raise ParagraphManifestConflict("cannot seal an empty Paragraph Manifest")
        self._validate_manifest_lineage(operation.manifest_ref, active_lineage)
        try:
            self.manifest_authorization_validator(operation, active_lineage)
        except ParagraphRuntimeError:
            raise
        except Exception as exc:
            raise ParagraphSourceBindingError(
                "Manifest seal authorization validation failed"
            ) from exc
        fingerprint = self.manifest_seal_fingerprint(
            operation=operation,
            expected_manifest_revision=expected_manifest_revision,
            expected_members=expected,
        )
        logical_op = self.manifest_logical_operation(operation)

        with self._lock, InterProcessFileLock(self.manifest_lock_path):
            manifests, operations = self._load_manifest_state_unlocked()
            existing = operations.get(logical_op.logical_operation_id)
            if existing is not None:
                receipt, result_ref = existing
                if receipt.semantic_fingerprint != fingerprint:
                    raise ParagraphIdentityCollision(
                        "same Manifest SEAL operation changed semantics"
                    )
                self._reconcile_manifest_coordination(
                    logical_op,
                    fingerprint,
                    result_ref,
                )
                return receipt
            current = manifests.get(
                _ref_key(operation.manifest_ref),
                self._manifest_initial(operation.manifest_ref),
            )
            if (
                current.manifest_revision != expected_manifest_revision
                or current.generation_membership_state != "OPEN"
                or current.members != expected
            ):
                raise ParagraphManifestConflict(
                    "Paragraph Manifest seal expected state is stale"
                )
            self._validate_manifest_lineage(operation.manifest_ref, active_lineage)
            self._validate_manifest_members(operation.manifest_ref, expected)
            claim = self._claim_manifest(logical_op, fingerprint)
            canonical_appended = False
            try:
                receipt = SectionParagraphManifestCommitReceiptSchema(
                    operation_ref=operation,
                    semantic_fingerprint=fingerprint,
                    prior_manifest_revision=current.manifest_revision,
                    resulting_manifest_revision=current.manifest_revision + 1,
                    prior_generation_membership_state="OPEN",
                    resulting_generation_membership_state="SEALED",
                    prior_member_sequence_sha256=_member_sequence_sha256(
                        current.members
                    ),
                    resulting_members=current.members,
                    resulting_member_sequence_sha256=_member_sequence_sha256(
                        current.members
                    ),
                )
                result_ref = self._append_manifest_receipt(receipt)
                canonical_appended = True
                self._ack_manifest(claim, result_ref)
                return receipt
            except Exception:
                if not canonical_appended:
                    self.coordination_store.release(
                        claim,
                        reason="manifest_seal_failed_before_canonical_commit",
                    )
                raise

    def _resolve_current_manifest_lineage(
        self,
        manifest_ref: SectionParagraphManifestRefSchema,
    ) -> ActiveExecutionLineageSchema:
        lineage = self.lifecycle_service.resolve_active_lineage(
            manifest_ref.run_id,
            manifest_ref.attempt_scope_ref,
        )
        if (
            lineage.attempt_id != manifest_ref.attempt_id
            or manifest_ref.write_instance_id != lineage.attempt_id
        ):
            raise ParagraphCurrentnessError(
                "Paragraph Manifest lifecycle is not the active Attempt"
            )
        return lineage

    def resolve_paragraph_binding(
        self,
        *,
        logical_ref: ArtifactLogicalRefSchema,
        manifest_ref: SectionParagraphManifestRefSchema,
    ) -> ParagraphBindingSchema | None:
        if logical_ref.artifact_type != PARAGRAPH_ARTIFACT_TYPE:
            raise ParagraphCurrentnessError("logical_ref is not a Paragraph")
        self._resolve_current_manifest_lineage(manifest_ref)
        head = self.artifact_runtime.resolve_active(logical_ref)
        if head is None or head.active_version_ref is None:
            return None
        manifest = self.load_manifest(manifest_ref)
        if head.active_version_ref not in manifest.members:
            return None
        return ParagraphBindingSchema(
            version_ref=head.active_version_ref,
            resolved_head_revision=head.head_revision,
            manifest_ref=manifest_ref,
            resolved_manifest_revision=manifest.manifest_revision,
        )

    def validate_paragraph_binding_current(
        self,
        binding: ParagraphBindingSchema,
        *,
        require_source_fresh: bool = False,
    ) -> ParagraphContentSchema:
        binding = ParagraphBindingSchema.model_validate(binding)
        head = self.artifact_runtime.resolve_active(binding.version_ref.logical_ref)
        if (
            head is None
            or head.active_version_ref != binding.version_ref
            or head.head_revision != binding.resolved_head_revision
        ):
            raise ParagraphCurrentnessError("Paragraph SELF_CURRENT is false")
        self._resolve_current_manifest_lineage(binding.manifest_ref)
        manifest = self.load_manifest(binding.manifest_ref)
        if (
            manifest.manifest_revision != binding.resolved_manifest_revision
            or binding.version_ref not in manifest.members
        ):
            raise ParagraphCurrentnessError(
                "Paragraph MEMBERSHIP_CURRENT is false"
            )
        content = self.read_paragraph(binding.version_ref)
        if require_source_fresh:
            lineage = self._resolve_current_manifest_lineage(
                binding.manifest_ref
            )
            try:
                self.source_validator(content.source_binding, lineage)
            except Exception as exc:
                raise ParagraphSourceBindingError(
                    "Paragraph SOURCE_FRESH is false"
                ) from exc
        return content

    def assemble_manifest_text(
        self,
        manifest_ref: SectionParagraphManifestRefSchema,
        *,
        separator: str = "\n\n",
    ) -> str:
        manifest = self.load_manifest(manifest_ref)
        parts = [self.read_paragraph(member).text for member in manifest.members]
        return separator.join(parts)


__all__ = [
    "PARAGRAPH_ACTIVE_PROMOTION",
    "PARAGRAPH_ARTIFACT_TYPE",
    "PARAGRAPH_CONTENT_SCHEMA_VERSION",
    "PARAGRAPH_VERSION_COMMIT",
    "SECTION_PARAGRAPH_MANIFEST_SEAL",
    "SECTION_PARAGRAPH_MANIFEST_UPDATE",
    "ParagraphCurrentnessError",
    "ParagraphIdentityCollision",
    "ParagraphManifestConflict",
    "ParagraphManifestIntegrityError",
    "ParagraphOperationInProgress",
    "ParagraphRuntime",
    "ParagraphRuntimeError",
    "ParagraphSourceBindingError",
]
