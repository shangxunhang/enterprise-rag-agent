"""Deterministic recovery policy for invocations left in running state."""

from __future__ import annotations

from typing import Any
import re

from apps.enterprise_document.schemas.agent_rag_v1 import (
    InvocationRecoveryAction,
    InvocationRecoveryDecisionSchema,
    RunningInvocationRecoveryContextSchema,
    RunningInvocationRecoveryInputSchema,
)
from apps.enterprise_document.schemas.pre_write_writer import (
    WriterExecutionRefSchema, WriterExecutionStatus, WriterGenerationInvocationRefSchema,
    WriterGenerationInvocationSchema, WriterLogicalExecutionKey,
)
from apps.enterprise_document.schemas.agent_rag_v1.claim_support import ClaimEvidenceSupportRefSchema
from core.hashing import canonical_sha256
from core.runtime.pre_write_execution_store import WriterExecutionState
from core.runtime.side_effects import CommittedResultResolutionStatus
from .lifecycle_runtime import RunAttemptLifecycleService


class RunningInvocationRecoveryService:
    """Read exact native results without granting another external call."""

    def __init__(self, *, writer_execution_store=None, support_runtime=None,
                 verification_runtime=None, lifecycle_service=None) -> None:
        self.writer_execution_store = writer_execution_store
        self.support_runtime = support_runtime
        self.verification_runtime = verification_runtime
        guarded_owner = getattr(getattr(writer_execution_store, "run_commit_guard", None), "__self__", None)
        self.lifecycle_service = (lifecycle_service if lifecycle_service is not None
                                  else guarded_owner if isinstance(guarded_owner, RunAttemptLifecycleService) else None)

    @staticmethod
    def _indeterminate() -> dict[str, Any]:
        return {"outcome": "INDETERMINATE", "result": None, "result_ref": None}

    def _writer_identity_matches(self, invocation, key, generation) -> bool:
        if (generation.writer_subject_ref != key.writer_subject_ref
                or generation.write_instance_id != key.write_instance_id
                or generation.route_decision_ref != key.route_decision_ref):
            return False
        snapshot = generation.model_dump(mode="json")["unified_writer_input_snapshot"]
        package = snapshot.get("target_context_package") or {}
        subject = package.get("invocation_subject") or {}
        scope = f"section:{key.writer_subject_ref.section_id}"
        operation = "writer-model-call:" + canonical_sha256({
            "run_id": key.writer_subject_ref.run_id,
            "section_id": key.writer_subject_ref.section_id,
            "attempt_id": invocation.attempt_id,
            "route_decision_id": key.route_decision_ref.route_decision_id,
        })
        # The key deliberately has no Attempt ordinal column. Read its existing
        # lifecycle owner; never infer the ordinal from caller transport text.
        if self.lifecycle_service is None:
            return False
        attempt = self.lifecycle_service.load_attempt(invocation.attempt_id)
        run = self.lifecycle_service.load_run_lifecycle(invocation.run_id)
        expected_invocation_id = (f"{invocation.run_id}:{key.writer_subject_ref.section_id}:writer:"
                                  f"a{attempt.identity.attempt_ordinal}")
        if (run.identity.task_id != invocation.task_id
                or attempt.identity.run_id != invocation.run_id
                or attempt.identity.attempt_scope_ref != scope
                or attempt.identity.input_snapshot_version != invocation.input_snapshot_version
                or invocation.invocation_id != expected_invocation_id):
            return False
        if (snapshot.get("task_id") != invocation.task_id
                or snapshot.get("run_id") != invocation.run_id
                or key.writer_subject_ref.run_id != invocation.run_id
                or key.write_instance_id != invocation.attempt_id
                or subject.get("run_id") != invocation.run_id
                or subject.get("attempt_id") != invocation.attempt_id
                or subject.get("consumer_scope_ref") != scope
                or subject.get("logical_operation_id") != invocation.idempotency_key
                or invocation.idempotency_key != operation
                or invocation.execution_id != f"{invocation.invocation_id}:execution"
                or re.fullmatch(re.escape(
                    f"{invocation.run_id}:{key.writer_subject_ref.section_id}:writer:a")
                    + r"[1-9][0-9]*", invocation.invocation_id) is None):
            return False
        # Production ContextSubject has no snapshot field. Its exact native
        # ContextPackage source binding carries the canonical lineage instead.
        lineages = [row.get("source_binding", {}).get("exact_source_ref", {}).get("lineage")
                    for row in package.get("candidate_decisions", [])
                    if row.get("source_binding", {}).get("source_family") == "WORKFLOW_EXECUTION_METADATA"]
        if lineages:
            return (len(lineages) == 1 and isinstance(lineages[0], dict)
                    and lineages[0].get("run_id") == invocation.run_id
                    and lineages[0].get("attempt_scope_ref") == scope
                    and lineages[0].get("attempt_id") == invocation.attempt_id
                    and lineages[0].get("input_snapshot_version") == invocation.input_snapshot_version)
        # Legacy exact invocation snapshots may carry this field themselves;
        # absence is uncertainty, never a guessed production schema field.
        return subject.get("input_snapshot_version") == invocation.input_snapshot_version

    def reconcile_writer(self, invocation, key, semantic_fingerprint, *,
                         result_ref=None, generation_invocation_ref=None,
                         generation_invocation=None) -> dict[str, Any]:
        if self.writer_execution_store is None:
            return self._indeterminate()
        try:
            key = WriterLogicalExecutionKey.model_validate(key)
            record = self.writer_execution_store.get_writer_execution(key)
            if (record is None or record.semantic_fingerprint != semantic_fingerprint
                    or record.state not in {WriterExecutionState.SUCCEEDED, WriterExecutionState.CONFIRMED_FAILED}
                    or record.result is None or record.result.logical_execution_key != key):
                return self._indeterminate()
            exact_ref = WriterExecutionRefSchema(logical_execution_key=key,
                result_sha256=canonical_sha256(record.result.model_dump(mode="json")))
            if result_ref is not None and WriterExecutionRefSchema.model_validate(result_ref) != exact_ref:
                return self._indeterminate()
            exact_generation_ref = WriterGenerationInvocationRefSchema(
                logical_execution_key=key, invocation_sha256=semantic_fingerprint)
            if (generation_invocation_ref is not None
                    and WriterGenerationInvocationRefSchema.model_validate(generation_invocation_ref) != exact_generation_ref):
                return self._indeterminate()
            if record.state is WriterExecutionState.SUCCEEDED:
                result = self.writer_execution_store.resolve_writer_execution(exact_ref)
                generation = self.writer_execution_store.resolve_writer_generation_invocation(exact_generation_ref)
                if (generation_invocation is not None
                        and WriterGenerationInvocationSchema.model_validate(generation_invocation) != generation):
                    return self._indeterminate()
                outcome = "RECOVERED_DURABLE_RESULT"
            else:
                result = record.result
                if result.status is not WriterExecutionStatus.TECHNICAL_FAILURE or generation_invocation is None:
                    return self._indeterminate()
                generation = WriterGenerationInvocationSchema.model_validate(generation_invocation)
                outcome = "DURABLE_FAILURE"
            if (canonical_sha256(generation.model_dump(mode="json")) != semantic_fingerprint
                    or not self._writer_identity_matches(invocation, key, generation)):
                return self._indeterminate()
            return {"outcome": outcome, "result": result, "result_ref": exact_ref,
                    "generation_invocation": generation, "generation_invocation_ref": exact_generation_ref}
        except (ValueError, KeyError, TypeError, AttributeError):
            return self._indeterminate()

    def reconcile_support(self, operation_ref, semantic_fingerprint, result_ref):
        if self.support_runtime is None:
            return self._indeterminate()
        try:
            result_ref = ClaimEvidenceSupportRefSchema.model_validate(result_ref)
            resolution = self.support_runtime.resolve_committed_result(operation_ref, semantic_fingerprint)
            expected = "claim-evidence-support-record://sha256/" + canonical_sha256(result_ref.model_dump(mode="json"))
            if (resolution.resolution is not CommittedResultResolutionStatus.COMMITTED_SAME
                    or resolution.committed_result_refs != (expected,)):
                return self._indeterminate()
            result = self.support_runtime.resolve_support(result_ref)
            if result.committed_operation_id != operation_ref.logical_operation_id:
                return self._indeterminate()
            return {"outcome": "RECOVERED_DURABLE_RESULT", "result": result, "result_ref": result_ref}
        except (ValueError, KeyError, TypeError, AttributeError):
            return self._indeterminate()

    def reconcile_verification(self, operation_ref, semantic_fingerprint, result_ref, revision_ref):
        if self.verification_runtime is None:
            return self._indeterminate()
        try:
            committed = self.verification_runtime.resolve_committed_verification(
                operation_ref, semantic_fingerprint, result_ref, revision_ref)
            return {"outcome": "RECOVERED_DURABLE_RESULT", "result": committed.verification_result,
                    "revision": committed.verification_revision, "result_ref": result_ref}
        except (ValueError, KeyError, TypeError, AttributeError):
            return self._indeterminate()

    def decide(
        self,
        invocation: RunningInvocationRecoveryInputSchema,
        context: RunningInvocationRecoveryContextSchema,
        *, writer_key=None, writer_semantic_fingerprint=None,
        writer_execution_ref=None, writer_invocation_ref=None,
        writer_generation_invocation=None,
    ) -> InvocationRecoveryDecisionSchema:
        if (
            invocation.task_id != context.task_id
            or invocation.run_id != context.run_id
        ):
            return self._decision(
                invocation,
                InvocationRecoveryAction.DISCARD_STALE,
                "invocation_identity_stale",
            )
        if (
            invocation.input_snapshot_version != context.input_snapshot_version
            or invocation.execution_id not in context.active_execution_ids
            or invocation.attempt_id not in context.active_attempt_ids
        ):
            return self._decision(
                invocation,
                InvocationRecoveryAction.DISCARD_STALE,
                "attempt_or_snapshot_stale",
            )
        if context.draining or context.cancelled:
            return self._decision(
                invocation,
                InvocationRecoveryAction.DO_NOT_RESUME,
                "task_not_accepting_recovery_calls",
            )
        if writer_key is not None and writer_semantic_fingerprint is not None:
            view = self.reconcile_writer(invocation, writer_key, writer_semantic_fingerprint,
                result_ref=writer_execution_ref, generation_invocation_ref=writer_invocation_ref,
                generation_invocation=writer_generation_invocation)
            if view["outcome"] == "RECOVERED_DURABLE_RESULT":
                return self._decision(invocation, InvocationRecoveryAction.NO_ACTION,
                                      view["outcome"], result_ref=view["result_ref"].result_sha256)
            if view["outcome"] == "DURABLE_FAILURE":
                return self._decision(invocation, InvocationRecoveryAction.DO_NOT_RESUME, view["outcome"])
        # Caller flags, provider query state, physical receipts and policy do
        # not prove an irreversible absence of effects. Writer has no PNE cut.
        return self._decision(
            invocation,
            InvocationRecoveryAction.DO_NOT_RESUME,
            "INDETERMINATE",
        )

    @staticmethod
    def _decision(
        invocation: RunningInvocationRecoveryInputSchema,
        action: InvocationRecoveryAction,
        reason_code: str,
        *,
        idempotency_key: str | None = None,
        result_ref: str | None = None,
        starts_new_call: bool = False,
    ) -> InvocationRecoveryDecisionSchema:
        return InvocationRecoveryDecisionSchema(
            invocation_id=invocation.invocation_id,
            action=action,
            reason_code=reason_code,
            idempotency_key=idempotency_key,
            result_ref=result_ref,
            starts_new_call=starts_new_call,
        )
