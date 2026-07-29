"""Deterministic recovery policy for invocations left in running state."""

from __future__ import annotations

from apps.enterprise_document.schemas.agent_rag_v1 import (
    InvocationRecoveryAction,
    InvocationRecoveryDecisionSchema,
    RunningInvocationRecoveryContextSchema,
    RunningInvocationRecoveryInputSchema,
    UnderlyingInvocationStatus,
)


class RunningInvocationRecoveryService:
    """Apply Architecture Spec section 32.3 without starting work itself."""

    def decide(
        self,
        invocation: RunningInvocationRecoveryInputSchema,
        context: RunningInvocationRecoveryContextSchema,
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
        if invocation.completion_event_persisted:
            return self._decision(
                invocation,
                InvocationRecoveryAction.NO_ACTION,
                "completion_event_already_persisted",
                result_ref=invocation.result_ref,
            )
        if invocation.result_persisted:
            return self._decision(
                invocation,
                InvocationRecoveryAction.SUPPLEMENT_EVENT,
                "result_persisted_event_missing",
                result_ref=invocation.result_ref,
            )
        if invocation.underlying_queryable:
            if invocation.underlying_status == UnderlyingInvocationStatus.COMPLETED:
                return self._decision(
                    invocation,
                    InvocationRecoveryAction.LOAD_AND_COMMIT,
                    "original_invocation_completed",
                )
            if invocation.underlying_status == UnderlyingInvocationStatus.RUNNING:
                return self._decision(
                    invocation,
                    InvocationRecoveryAction.RESUME_MONITORING,
                    "original_invocation_still_running",
                )
        return self._decision(
            invocation,
            InvocationRecoveryAction.IDEMPOTENT_RETRY,
            "original_invocation_unavailable",
            idempotency_key=invocation.idempotency_key,
            starts_new_call=True,
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
