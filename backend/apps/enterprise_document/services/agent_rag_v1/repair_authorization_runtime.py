"""SIF-11 replay-stable Paragraph semantic repair authorization runtime."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Iterable

from apps.enterprise_document.schemas.agent_rag_v1.artifacts import (
    ArtifactLogicalRefSchema,
    ArtifactVersionRefSchema,
)
from apps.enterprise_document.schemas.agent_rag_v1.budgets import (
    BudgetReservationRefSchema,
    BudgetSettlementSchema,
    CanonicalBudgetVectorSchema,
)
from apps.enterprise_document.schemas.agent_rag_v1.paragraphs import (
    ParagraphOperationRefSchema,
    SectionParagraphManifestRefSchema,
)
from apps.enterprise_document.schemas.agent_rag_v1.repair import (
    ExactRepairBasisRefSchema,
    ParagraphRepairAuthorizationRecordSchema,
    ParagraphRepairAuthorizationRefSchema,
    ParagraphRepairLogicalOperationRefSchema,
    ParagraphRepairTargetBindingSchema,
    RepairIssueBindingSchema,
)
from apps.enterprise_document.schemas.agent_rag_v1.research import (
    SectionPlanSourceRefSchema,
)
from apps.enterprise_document.schemas.agent_rag_v1.verification import (
    VerificationResultRefSchema,
    VerifierIssueLifecycleStatus,
)
from apps.enterprise_document.services.agent_rag_v1.budget_manager import BudgetManager
from apps.enterprise_document.services.agent_rag_v1.claim_verification_runtime import (
    ClaimVerificationRuntime,
)
from apps.enterprise_document.services.agent_rag_v1.lifecycle_runtime import (
    RunAttemptLifecycleService,
)
from apps.enterprise_document.services.agent_rag_v1.paragraph_runtime import (
    ParagraphCurrentnessError,
    ParagraphRuntime,
)
from core.hashing import canonical_sha256
from core.persistence.crash_safe_jsonl import CrashSafeJsonlRecordSink
from core.persistence.interprocess_lock import InterProcessFileLock
from core.runtime.side_effects import (
    CommittedResultResolution,
    CommittedResultResolutionStatus,
    LogicalOperationRef,
)


PARAGRAPH_REPAIR_AUTHORIZATION_COMMIT = "PARAGRAPH_REPAIR_AUTHORIZATION_COMMIT"


class ParagraphRepairAuthorizationError(ValueError):
    pass


class ParagraphRepairAuthorizationIntegrityError(ParagraphRepairAuthorizationError):
    pass


class ParagraphRepairAuthorizationCurrentnessError(
    ParagraphCurrentnessError,
    ParagraphRepairAuthorizationError,
):
    pass


class ParagraphRepairAuthorizationStore:
    """Crash-safe immutable authorization record owner + SIF-02 resolver."""

    def __init__(
        self,
        path: str | Path = "data/runtime/agent_rag_v1/paragraph_repair_authorizations.jsonl",
        *,
        run_commit_guard=None,
        require_run_commit_guard: bool = False,
    ) -> None:
        self.path = Path(path)
        if require_run_commit_guard and not callable(run_commit_guard):
            raise ValueError("Target Repair authorization requires a Run commit guard")
        self.run_commit_guard = run_commit_guard
        self.require_run_commit_guard = require_run_commit_guard
        self.sink = CrashSafeJsonlRecordSink(self.path)
        self.lock_path = self.path.with_suffix(self.path.suffix + ".lock")

    @staticmethod
    def _decode(raw: dict) -> ParagraphRepairAuthorizationRecordSchema:
        try:
            return ParagraphRepairAuthorizationRecordSchema.model_validate(raw)
        except Exception as exc:
            raise ParagraphRepairAuthorizationIntegrityError(
                "corrupt ParagraphRepairAuthorization history"
            ) from exc

    def _records_unlocked(self) -> tuple[ParagraphRepairAuthorizationRecordSchema, ...]:
        records = tuple(self._decode(item) for item in self.sink.read_records())
        ids = [item.repair_authorization_id for item in records]
        operations = [item.logical_operation_ref.logical_operation_id for item in records]
        if len(ids) != len(set(ids)) or len(operations) != len(set(operations)):
            raise ParagraphRepairAuthorizationIntegrityError(
                "duplicate repair authorization identity in committed history"
            )
        return records

    def put_if_absent_or_same(
        self,
        record: ParagraphRepairAuthorizationRecordSchema,
    ) -> ParagraphRepairAuthorizationRefSchema:
        candidate = ParagraphRepairAuthorizationRecordSchema.model_validate(record)
        serialized = json.dumps(
            candidate.model_dump(mode="json"),
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        with InterProcessFileLock(self.lock_path):
            self._records_unlocked()
            if self.run_commit_guard is not None:
                self.run_commit_guard(candidate.run_id)
            try:
                self.sink.append_record_once(
                    serialized,
                    id_field="repair_authorization_id",
                    identity=candidate.repair_authorization_id,
                )
            except ValueError as exc:
                raise ParagraphRepairAuthorizationIntegrityError(str(exc)) from exc
            resolved = self._resolve_by_id_unlocked(candidate.repair_authorization_id)
            if resolved != candidate:
                raise ParagraphRepairAuthorizationIntegrityError(
                    "repair authorization idempotent commit mismatch"
                )
        return candidate.as_ref()

    def _resolve_by_id_unlocked(
        self,
        repair_authorization_id: str,
    ) -> ParagraphRepairAuthorizationRecordSchema:
        matches = [
            item
            for item in self._records_unlocked()
            if item.repair_authorization_id == repair_authorization_id
        ]
        if len(matches) != 1:
            raise ParagraphRepairAuthorizationIntegrityError(
                "required exact repair authorization is missing or ambiguous"
            )
        return matches[0]

    def resolve_exact(
        self,
        ref: ParagraphRepairAuthorizationRefSchema,
    ) -> ParagraphRepairAuthorizationRecordSchema:
        exact_ref = ParagraphRepairAuthorizationRefSchema.model_validate(ref)
        with InterProcessFileLock(self.lock_path):
            record = self._resolve_by_id_unlocked(exact_ref.repair_authorization_id)
        if record.as_ref() != exact_ref:
            raise ParagraphRepairAuthorizationIntegrityError(
                "repair authorization exact ref/fingerprint mismatch"
            )
        return record

    def resolve_by_id(
        self,
        repair_authorization_id: str,
    ) -> ParagraphRepairAuthorizationRecordSchema:
        normalized = str(repair_authorization_id or "").strip()
        if not normalized:
            raise ValueError("repair_authorization_id is required")
        with InterProcessFileLock(self.lock_path):
            return self._resolve_by_id_unlocked(normalized)

    def resolve_committed_result(
        self,
        operation_ref: LogicalOperationRef,
        semantic_fingerprint: str,
    ) -> CommittedResultResolution:
        fingerprint = str(semantic_fingerprint or "").strip()
        with InterProcessFileLock(self.lock_path):
            matches = [
                item
                for item in self._records_unlocked()
                if item.logical_operation_ref.logical_operation_id
                == operation_ref.logical_operation_id
            ]
        if not matches:
            return CommittedResultResolution(
                logical_operation_id=operation_ref.logical_operation_id,
                semantic_fingerprint=fingerprint,
                resolution=CommittedResultResolutionStatus.ABSENT,
            )
        if len(matches) != 1:
            return CommittedResultResolution(
                logical_operation_id=operation_ref.logical_operation_id,
                semantic_fingerprint=fingerprint,
                resolution=CommittedResultResolutionStatus.INTEGRITY_INVALID,
            )
        record = matches[0]
        if record.semantic_fingerprint != fingerprint:
            return CommittedResultResolution(
                logical_operation_id=operation_ref.logical_operation_id,
                semantic_fingerprint=fingerprint,
                resolution=CommittedResultResolutionStatus.COMMITTED_CONFLICT,
                committed_result_refs=(record.repair_authorization_id,),
            )
        return CommittedResultResolution(
            logical_operation_id=operation_ref.logical_operation_id,
            semantic_fingerprint=fingerprint,
            resolution=CommittedResultResolutionStatus.COMMITTED_SAME,
            committed_result_refs=(record.repair_authorization_id,),
        )


class ParagraphRepairAuthorizationRuntime:
    """One-root authorization owner; Writer/Paragraph consumes, never recreates."""

    def __init__(
        self,
        *,
        lifecycle_service: RunAttemptLifecycleService,
        paragraph_runtime: ParagraphRuntime,
        verification_runtime: ClaimVerificationRuntime,
        budget_manager: BudgetManager,
        store: ParagraphRepairAuthorizationStore,
    ) -> None:
        self.lifecycle_service = lifecycle_service
        self.paragraph_runtime = paragraph_runtime
        self.verification_runtime = verification_runtime
        self.budget_manager = budget_manager
        self.store = store
        # Repair always uses the already-owned lifecycle; it creates no owner.
        guard = lifecycle_service.assert_run_committable
        for owner in (store, budget_manager):
            existing = owner.run_commit_guard
            if existing is not None and existing != guard:
                raise ValueError("Repair requires the same canonical Run commit guard")
            owner.run_commit_guard = guard

    @staticmethod
    def _canonical_issue_bindings(
        issue_bindings: Iterable[RepairIssueBindingSchema],
    ) -> tuple[RepairIssueBindingSchema, ...]:
        items = tuple(
            sorted(
                (
                    RepairIssueBindingSchema.model_validate(item)
                    for item in issue_bindings
                ),
                key=lambda item: (
                    item.issue_ref.issue_id,
                    item.expected_issue_head_revision,
                ),
            )
        )
        if not items:
            raise ParagraphRepairAuthorizationError(
                "semantic repair requires at least one exact OPEN issue"
            )
        return items

    @staticmethod
    def _canonical_verification_refs(
        refs: Iterable[VerificationResultRefSchema],
    ) -> tuple[VerificationResultRefSchema, ...]:
        items = tuple(
            sorted(
                (
                    VerificationResultRefSchema.model_validate(item)
                    for item in refs
                ),
                key=lambda item: item.verification_result_id,
            )
        )
        if not items:
            raise ParagraphRepairAuthorizationError(
                "semantic repair requires exact VerificationResult refs"
            )
        return items

    @staticmethod
    def _canonical_basis_refs(
        refs: Iterable[ExactRepairBasisRefSchema],
        *,
        required: bool,
    ) -> tuple[ExactRepairBasisRefSchema, ...]:
        items = tuple(
            sorted(
                (
                    ExactRepairBasisRefSchema.model_validate(item)
                    for item in refs
                ),
                key=lambda item: (item.authority_kind, item.exact_ref),
            )
        )
        if required and not items:
            raise ParagraphRepairAuthorizationError(
                "semantic repair requires exact semantic repair basis refs"
            )
        return items

    def _resolve_target(
        self,
        *,
        run_id: str,
        section_id: str,
        paragraph_logical_ref: ArtifactLogicalRefSchema,
        manifest_ref: SectionParagraphManifestRefSchema,
    ) -> tuple[ParagraphRepairTargetBindingSchema, object]:
        lineage = self.lifecycle_service.resolve_active_lineage(
            run_id,
            f"section:{section_id}",
        )
        self.lifecycle_service.assert_attempt_committable(
            run_id=lineage.run_id,
            attempt_scope_ref=lineage.attempt_scope_ref,
            attempt_id=lineage.attempt_id,
            input_snapshot_version=lineage.input_snapshot_version,
        )
        if (
            manifest_ref.run_id != run_id
            or manifest_ref.section_id != section_id
            or manifest_ref.attempt_scope_ref != lineage.attempt_scope_ref
            or manifest_ref.attempt_id != lineage.attempt_id
            or manifest_ref.write_instance_id != lineage.attempt_id
        ):
            raise ParagraphRepairAuthorizationCurrentnessError(
                "repair Manifest is not owned by active Attempt"
            )
        try:
            binding = self.paragraph_runtime.resolve_paragraph_binding(
                logical_ref=paragraph_logical_ref,
                manifest_ref=manifest_ref,
            )
        except ParagraphCurrentnessError as exc:
            raise ParagraphRepairAuthorizationCurrentnessError(str(exc)) from exc
        if binding is None:
            raise ParagraphRepairAuthorizationCurrentnessError(
                "no exact current Paragraph/Manifest repair target"
            )
        target = ParagraphRepairTargetBindingSchema(
            run_id=run_id,
            section_id=section_id,
            paragraph_logical_ref=paragraph_logical_ref,
            base_paragraph_version_ref=binding.version_ref,
            manifest_ref=manifest_ref,
            resolved_manifest_revision=binding.resolved_manifest_revision,
            write_instance_id=lineage.attempt_id,
        )
        return target, lineage

    def _validate_issue_and_verification_currentness(
        self,
        *,
        issue_bindings: tuple[RepairIssueBindingSchema, ...],
        verification_refs: tuple[VerificationResultRefSchema, ...],
    ) -> None:
        for binding in issue_bindings:
            head = self.verification_runtime.resolve_issue_head(binding.issue_ref)
            if head is None:
                raise ParagraphRepairAuthorizationCurrentnessError(
                    "VerifierIssue has no current Head"
                )
            if (
                head.issue_revision != binding.expected_issue_head_revision
                or head.lifecycle_status is not VerifierIssueLifecycleStatus.OPEN
                or binding.expected_lifecycle_status
                is not VerifierIssueLifecycleStatus.OPEN
            ):
                raise ParagraphRepairAuthorizationCurrentnessError(
                    "VerifierIssue head is not exact expected OPEN revision"
                )
            if not any(
                binding.issue_ref.verification_result_ref == item
                for item in verification_refs
            ):
                raise ParagraphRepairAuthorizationIntegrityError(
                    "repair issue VerificationResult is absent from exact basis"
                )
        for ref in verification_refs:
            resolved = self.verification_runtime.resolve_verification_result(ref)
            if resolved.verification_result_ref != ref:
                raise ParagraphRepairAuthorizationIntegrityError(
                    "VerificationResult resolver returned another result"
                )

    def _budget_ref(
        self,
        *,
        task_id: str,
        run_id: str,
        attempt_scope_ref: str,
        attempt_id: str,
        repair_authorization_id: str,
        logical_operation_id: str,
    ) -> BudgetReservationRefSchema:
        reservation_id = "repair-budget://sha256/" + canonical_sha256(
            {
                "contract": "sif12_paragraph_semantic_repair_budget_v1",
                "repair_authorization_id": repair_authorization_id,
                "logical_operation_id": logical_operation_id,
            }
        )
        return BudgetReservationRefSchema(
            reservation_id=reservation_id,
            task_id=task_id,
            run_id=run_id,
            reservation_kind="PARAGRAPH_SEMANTIC_REPAIR",
            owner_occurrence_ref=repair_authorization_id,
            scope_ref=attempt_scope_ref,
            operation_ref=logical_operation_id,
            attempt_id=attempt_id,
        )

    def authorize(
        self,
        *,
        task_id: str,
        run_id: str,
        section_id: str,
        paragraph_logical_ref: ArtifactLogicalRefSchema,
        manifest_ref: SectionParagraphManifestRefSchema,
        issue_bindings: Iterable[RepairIssueBindingSchema],
        verification_result_refs: Iterable[VerificationResultRefSchema],
        semantic_repair_basis_refs: Iterable[ExactRepairBasisRefSchema],
        section_plan_ref: SectionPlanSourceRefSchema,
        repair_policy_id: str,
        repair_policy_version: str,
        recommendation_refs: Iterable[ExactRepairBasisRefSchema] = (),
        fact_snapshot_ref: ArtifactVersionRefSchema | None = None,
        evidence_package_ref: ArtifactVersionRefSchema | None = None,
    ) -> ParagraphRepairAuthorizationRefSchema:
        normalized_task_id = str(task_id or "").strip()
        if not normalized_task_id:
            raise ValueError("task_id is required")
        target, lineage = self._resolve_target(
            run_id=run_id,
            section_id=section_id,
            paragraph_logical_ref=ArtifactLogicalRefSchema.model_validate(
                paragraph_logical_ref
            ),
            manifest_ref=SectionParagraphManifestRefSchema.model_validate(manifest_ref),
        )
        issues = self._canonical_issue_bindings(issue_bindings)
        verification_refs = self._canonical_verification_refs(
            verification_result_refs
        )
        basis_refs = self._canonical_basis_refs(
            semantic_repair_basis_refs,
            required=True,
        )
        recommendations = self._canonical_basis_refs(
            recommendation_refs,
            required=False,
        )
        self._validate_issue_and_verification_currentness(
            issue_bindings=issues,
            verification_refs=verification_refs,
        )

        budget_state = self.budget_manager.snapshot()
        if (
            budget_state.task_id != normalized_task_id
            or budget_state.run_id != run_id
        ):
            raise ParagraphRepairAuthorizationIntegrityError(
                "canonical Budget belongs to another Task/Run"
            )
        operation_preimage = {
            "contract": "sif11_paragraph_repair_one_root_v2",
            "run_id": run_id,
            "section_id": section_id,
            "attempt_scope_ref": lineage.attempt_scope_ref,
            "attempt_id": lineage.attempt_id,
            "input_snapshot_version": lineage.input_snapshot_version,
            "target": target.model_dump(mode="json"),
            "issue_bindings": [item.model_dump(mode="json") for item in issues],
            "verification_result_refs": [
                item.model_dump(mode="json") for item in verification_refs
            ],
            "semantic_repair_basis_refs": [
                item.model_dump(mode="json") for item in basis_refs
            ],
            "recommendation_refs": [
                item.model_dump(mode="json") for item in recommendations
            ],
            "section_plan_ref": section_plan_ref.model_dump(mode="json"),
            "fact_snapshot_ref": (
                fact_snapshot_ref.model_dump(mode="json")
                if fact_snapshot_ref is not None
                else None
            ),
            "evidence_package_ref": (
                evidence_package_ref.model_dump(mode="json")
                if evidence_package_ref is not None
                else None
            ),
            "repair_policy_id": str(repair_policy_id or "").strip(),
            "repair_policy_version": str(repair_policy_version or "").strip(),
        }
        if not operation_preimage["repair_policy_id"] or not operation_preimage[
            "repair_policy_version"
        ]:
            raise ValueError("repair policy id/version are required")
        logical_operation_id = "paragraph-repair-op://sha256/" + canonical_sha256(
            operation_preimage
        )
        logical_operation_ref = ParagraphRepairLogicalOperationRefSchema(
            logical_operation_id=logical_operation_id,
            run_id=run_id,
            operation_scope_ref=target.paragraph_logical_ref.logical_artifact_id,
            attempt_scope_ref=lineage.attempt_scope_ref,
            attempt_id=lineage.attempt_id,
        )
        repair_authorization_id = "paragraph-repair-auth://sha256/" + canonical_sha256(
            {
                "contract": "sif11_paragraph_repair_one_root_v2",
                "logical_operation_ref": logical_operation_ref.model_dump(mode="json"),
            }
        )
        budget_ref = self._budget_ref(
            task_id=normalized_task_id,
            run_id=run_id,
            attempt_scope_ref=lineage.attempt_scope_ref,
            attempt_id=lineage.attempt_id,
            repair_authorization_id=repair_authorization_id,
            logical_operation_id=logical_operation_id,
        )

        # Same logical occurrence always maps to the same canonical SIF-12 reservation.
        self.budget_manager.reserve(
            budget_ref,
            CanonicalBudgetVectorSchema(repair_attempts=1),
            expected_revision=budget_state.revision,
        )

        kwargs = {
            "repair_authorization_id": repair_authorization_id,
            "run_id": run_id,
            "section_id": section_id,
            "attempt_scope_ref": lineage.attempt_scope_ref,
            "attempt_id": lineage.attempt_id,
            "input_snapshot_version": lineage.input_snapshot_version,
            "target": target,
            "issue_bindings": issues,
            "verification_result_refs": verification_refs,
            "semantic_repair_basis_refs": basis_refs,
            "recommendation_refs": recommendations,
            "section_plan_ref": SectionPlanSourceRefSchema.model_validate(
                section_plan_ref
            ),
            "fact_snapshot_ref": fact_snapshot_ref,
            "evidence_package_ref": evidence_package_ref,
            "repair_policy_id": operation_preimage["repair_policy_id"],
            "repair_policy_version": operation_preimage["repair_policy_version"],
            "budget_reservation_ref": budget_ref,
            "logical_operation_ref": logical_operation_ref,
        }
        placeholder = ParagraphRepairAuthorizationRecordSchema.model_construct(
            **kwargs,
            semantic_fingerprint="0" * 64,
        )
        payload = placeholder.model_dump(mode="json")
        payload.pop("semantic_fingerprint", None)
        record = ParagraphRepairAuthorizationRecordSchema(
            **kwargs,
            semantic_fingerprint=canonical_sha256(payload),
        )
        return self.store.put_if_absent_or_same(record)

    def _validate_record_currentness(
        self,
        record: ParagraphRepairAuthorizationRecordSchema,
    ) -> ParagraphRepairAuthorizationRecordSchema:
        lineage = self.lifecycle_service.assert_attempt_committable(
            run_id=record.run_id,
            attempt_scope_ref=record.attempt_scope_ref,
            attempt_id=record.attempt_id,
            input_snapshot_version=record.input_snapshot_version,
        )
        if lineage.attempt_id != record.target.manifest_ref.attempt_id:
            raise ParagraphRepairAuthorizationCurrentnessError(
                "repair authorization Attempt is no longer active"
            )
        binding = self.paragraph_runtime.resolve_paragraph_binding(
            logical_ref=record.target.paragraph_logical_ref,
            manifest_ref=record.target.manifest_ref,
        )
        if (
            binding is None
            or binding.version_ref != record.target.base_paragraph_version_ref
            or binding.resolved_manifest_revision
            != record.target.resolved_manifest_revision
        ):
            raise ParagraphRepairAuthorizationCurrentnessError(
                "repair target Paragraph/Manifest is stale"
            )
        self._validate_issue_and_verification_currentness(
            issue_bindings=record.issue_bindings,
            verification_refs=record.verification_result_refs,
        )
        return record

    def validate_for_dispatch(
        self,
        ref: ParagraphRepairAuthorizationRefSchema,
    ) -> ParagraphRepairAuthorizationRecordSchema:
        record = self._validate_record_currentness(self.store.resolve_exact(ref))
        reservation = self.budget_manager.resolve_reservation(
            record.budget_reservation_ref.reservation_id
        )
        if (
            reservation.reservation_ref != record.budget_reservation_ref
            or reservation.requested.repair_attempts != 1
            or reservation.status != "active"
        ):
            raise ParagraphRepairAuthorizationCurrentnessError(
                "repair SIF-12 reservation is not exact active admission"
            )
        return record

    def mark_semantic_repair_activated(
        self,
        ref: ParagraphRepairAuthorizationRefSchema,
    ) -> BudgetSettlementSchema:
        record = self.store.resolve_exact(ref)
        reservation = self.budget_manager.resolve_reservation(
            record.budget_reservation_ref.reservation_id
        )
        if reservation.reservation_ref != record.budget_reservation_ref:
            raise ParagraphRepairAuthorizationIntegrityError(
                "semantic repair Budget reservation ref mismatch"
            )
        if reservation.status == "consumed":
            if reservation.actual.repair_attempts != 1:
                raise ParagraphRepairAuthorizationIntegrityError(
                    "consumed semantic repair Budget has invalid actual usage"
                )
        elif reservation.status == "released":
            raise ParagraphRepairAuthorizationCurrentnessError(
                "released semantic repair admission cannot be activated"
            )
        else:
            self.validate_for_dispatch(ref)

        budget_state = self.budget_manager.snapshot()
        settlement = self.budget_manager.settle(
            record.budget_reservation_ref.reservation_id,
            CanonicalBudgetVectorSchema(repair_attempts=1),
            expected_revision=budget_state.revision,
        )
        if not isinstance(settlement, BudgetSettlementSchema):
            raise ParagraphRepairAuthorizationIntegrityError(
                "semantic repair requires canonical SIF-12 Budget mode"
            )
        return settlement

    def release_unactivated(
        self,
        ref: ParagraphRepairAuthorizationRefSchema,
    ) -> BudgetSettlementSchema:
        record = self.store.resolve_exact(ref)
        reservation = self.budget_manager.resolve_reservation(
            record.budget_reservation_ref.reservation_id
        )
        if reservation.status == "consumed":
            raise ParagraphRepairAuthorizationIntegrityError(
                "activated semantic repair Budget cannot be released"
            )
        budget_state = self.budget_manager.snapshot()
        settlement = self.budget_manager.release(
            record.budget_reservation_ref.reservation_id,
            expected_revision=budget_state.revision,
        )
        if not isinstance(settlement, BudgetSettlementSchema):
            raise ParagraphRepairAuthorizationIntegrityError(
                "semantic repair requires canonical SIF-12 Budget mode"
            )
        return settlement

    def paragraph_authorization_validator(
        self,
        operation: ParagraphOperationRefSchema,
        active_lineage,
    ) -> None:
        operation = ParagraphOperationRefSchema.model_validate(operation)
        if operation.authorization_kind != "REPAIR":
            return
        record = self.store.resolve_by_id(operation.authorization_ref)
        trusted = self._validate_record_currentness(record)
        reservation = self.budget_manager.resolve_reservation(
            trusted.budget_reservation_ref.reservation_id
        )
        if (
            reservation.reservation_ref != trusted.budget_reservation_ref
            or reservation.status != "consumed"
            or reservation.actual.repair_attempts != 1
        ):
            raise ParagraphRepairAuthorizationCurrentnessError(
                "Paragraph REVISE requires activated/consumed semantic repair Budget"
            )
        if (
            trusted.run_id != operation.run_id
            or trusted.section_id != operation.section_id
            or trusted.attempt_id != operation.write_instance_id
            or trusted.target.paragraph_logical_ref != operation.target_logical_ref
            or trusted.target.base_paragraph_version_ref != operation.base_version_ref
            or active_lineage.run_id != trusted.run_id
            or active_lineage.attempt_id != trusted.attempt_id
        ):
            raise ParagraphRepairAuthorizationCurrentnessError(
                "Paragraph REVISE does not match exact repair authorization"
            )


__all__ = [
    "PARAGRAPH_REPAIR_AUTHORIZATION_COMMIT",
    "ParagraphRepairAuthorizationCurrentnessError",
    "ParagraphRepairAuthorizationError",
    "ParagraphRepairAuthorizationIntegrityError",
    "ParagraphRepairAuthorizationRuntime",
    "ParagraphRepairAuthorizationStore",
]
