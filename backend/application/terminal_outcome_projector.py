"""Pure, allowlisted public projection. Never resolves or writes business truth."""
from __future__ import annotations

import re
from typing import Any
from application.terminal_outcome_schema import TerminalDiagnostic, TerminalOutcomeResultV1
from apps.enterprise_document.schemas.agent_rag_v1.lifecycle import RunLifecycleStatus
from apps.enterprise_document.schemas.post_write.foundation import EnforcementAction, ValidationCompletionItemState
from core.hashing import canonical_sha256
from schemas.agent import AgentResultSchema
from schemas.common import ErrorSchema
from schemas.failure import FailureCategory, FailureOrigin
from schemas.status import ExecutionStatus


def opaque_ref(value: Any) -> str:
    if hasattr(value, "model_dump"):
        value = value.model_dump(mode="json")
    return "sha256:" + canonical_sha256(value)


def seal_terminal(data: dict) -> TerminalOutcomeResultV1:
    """Hash normalized JSON including defaults, not Python model repr strings."""
    normalized = TerminalOutcomeResultV1.model_construct(result_id="pending", **data).model_dump(mode="json")
    normalized.pop("result_id")
    return TerminalOutcomeResultV1(result_id=opaque_ref(normalized), **normalized)


def safe_identifier(value: str) -> str:
    # Validation only, never classification by text. Unsafe values stay internal.
    return value if re.fullmatch(r"[A-Za-z0-9_-]{1,128}", value) else opaque_ref(value)


def diagnostic(code: str, source: Any, message: str) -> TerminalDiagnostic:
    public_code = code if re.fullmatch(r"[A-Z][A-Z0-9_]{0,95}", code) else "DIAGNOSTIC_RECORDED"
    return TerminalDiagnostic(diagnostic_ref=opaque_ref(source), code=public_code, message=message)


def error_projection(error: ErrorSchema) -> dict:
    nested = error.details.get("workflow_error") or {}
    origin = error.details.get("failure_origin", nested.get("failure_origin", FailureOrigin.UNKNOWN))
    try:
        origin = FailureOrigin(origin)
    except ValueError:
        origin = FailureOrigin.UNKNOWN
    messages = {
        FailureCategory.RESOURCE_EXHAUSTED: "Required execution resources were exhausted. Review the unfinished work before recovery.",
        FailureCategory.VALIDATION: "A technical validation step could not complete. The result is not a quality failure verdict.",
        FailureCategory.CONFIGURATION: "Required execution configuration is unavailable. An operator must correct it before retrying.",
        FailureCategory.TIMEOUT: "An execution step timed out. Review the recovery state before retrying.",
        FailureCategory.BUSINESS_QUALITY: "The canonical business quality check reported a failure.",
        FailureCategory.EVIDENCE_INSUFFICIENT: "The current evidence did not meet the required conditions.",
        FailureCategory.CANCELLED: "Execution was cancelled.",
    }
    return dict(failure_category=error.failure_category, failure_origin=origin,
                requires_human=error.requires_human, requires_new_input=error.requires_new_input,
                retryable=error.retryable, recoverable=error.recoverable,
                diagnostics=(diagnostic(error.error_code, error, messages.get(error.failure_category,
                    "An execution step failed. Review the diagnostic reference before recovery.")),))


def project_terminal(*, task_id, run_id, execution_status, phase, run_status=None,
                     control=None, publication=None, completed=(), unfinished=(), artifacts=(),
                     error=None, fallback=False, recovery_attempts=0, recovery_refs=(),
                     artifact_exists=False) -> TerminalOutcomeResultV1:
    execution_status = ExecutionStatus(execution_status)
    values = error_projection(error) if error is not None else {}
    action = control.enforcement_action if control is not None else None
    best = action is EnforcementAction.OUTPUT_BEST_EFFORT
    partial = best or execution_status is ExecutionStatus.PARTIAL_SUCCESS
    status = execution_status
    if error is not None:
        status = (error.failure_category if error.failure_category in {
            FailureCategory.RESOURCE_EXHAUSTED, FailureCategory.BUSINESS_QUALITY,
            FailureCategory.EVIDENCE_INSUFFICIENT, FailureCategory.CANCELLED,
        } else ValidationCompletionItemState.TECHNICAL_FAILURE)
    if action is EnforcementAction.BLOCK:
        status = action
    elif best and error is None:
        status = EnforcementAction.OUTPUT_BEST_EFFORT
    diagnostics = list(values.pop("diagnostics", ()))
    if control is not None:
        for code in control.control_reason_codes:
            diagnostics.append(diagnostic(code, control.as_ref(),
                "Publication is blocked by the final control decision." if action is EnforcementAction.BLOCK
                else "The final quality and availability decision is retained."))
        for ref in control.unresolved_reason_refs:
            diagnostics.append(diagnostic("QUALITY_UNRESOLVED", ref,
                "A required quality check remains unresolved; this is not verified success."))
        for ref in control.blocking_fact_refs:
            diagnostics.append(diagnostic("QUALITY_FAIL", ref,
                "A canonical quality check failed; the final control decision governs availability."))
    if fallback:
        diagnostics.append(diagnostic("FALLBACK_WRITE", {"run": run_id, "fallback": True},
            "Content was generated through the committed fallback route using the available evidence."))
    data = dict(task_id=safe_identifier(task_id), run_id=safe_identifier(run_id),
                status=status, execution_status=execution_status, run_status=run_status,
                control_action=action, quality_state=control.quality_state_passthrough if control else None,
                availability_state=control.availability_projection.state if control else None,
                phase=safe_identifier(phase), completed_scope_refs=tuple(safe_identifier(x) for x in completed),
                unfinished_scope_refs=tuple(safe_identifier(x) for x in unfinished), artifacts=tuple(artifacts),
                artifact_exists=artifact_exists or bool(artifacts), publication_exists=publication is not None,
                publication_ref=publication.manifest.release_id if publication else None,
                is_partial=partial, is_best_effort=best, fallback_occurred=fallback,
                recovery_attempts=recovery_attempts, recovery_refs=tuple(opaque_ref(x) for x in recovery_refs),
                diagnostics=tuple(diagnostics), **values)
    return seal_terminal(data)


def terminal_payload(result: AgentResultSchema) -> dict:
    """Prefer the final SchemeWriter result, not intermediate stage snapshots."""
    if result.agent_name in {"SchemeWriterAgent", "FakeSchemeWriterAgent"}:
        return result.result
    children = result.result.get("sub_agent_results", ())
    for child in reversed(children):
        payload = child.get("result") or {}
        if child.get("agent_name") in {"SchemeWriterAgent", "FakeSchemeWriterAgent"}:
            return payload
    return result.result


def project_later_failure(outcome: TerminalOutcomeResultV1, *, error: ErrorSchema, phase: str):
    """Append a later execution failure without rewriting committed business axes."""
    failure = project_terminal(task_id=outcome.task_id, run_id=outcome.run_id,
        execution_status=ExecutionStatus.RETRYABLE_FAILED if error.retryable else ExecutionStatus.FAILED,
        phase=phase, error=error)
    data = outcome.model_dump(mode="python", exclude={"result_id"})
    for field in ("execution_status", "phase", "failure_category", "failure_origin", "requires_human",
                  "requires_new_input", "retryable", "recoverable"):
        data[field] = getattr(failure, field)
    data["status"] = EnforcementAction.BLOCK if outcome.control_action is EnforcementAction.BLOCK else failure.status
    data["diagnostics"] = (*outcome.diagnostics, *failure.diagnostics)
    data["unfinished_scope_refs"] = tuple(dict.fromkeys((*outcome.unfinished_scope_refs, safe_identifier(phase))))
    return seal_terminal(data)


def outcome_from_result(result: AgentResultSchema) -> dict:
    payload = terminal_payload(result)
    if payload.get("terminal_outcome") is not None:
        return TerminalOutcomeResultV1.model_validate(payload["terminal_outcome"]).model_dump(mode="json")
    # Legacy/early failure has no final Control truth. Do not infer it from HardGate.
    return project_terminal(task_id=result.task_id, run_id=result.run_id,
        execution_status=result.status, phase=result.error.failed_node or "workflow" if result.error else "workflow",
        error=result.error).model_dump(mode="json")


def public_terminal_result(summary: dict) -> dict:
    """Only this allowlisted projection is safe for external result consumers."""
    value = TerminalOutcomeResultV1.model_validate(summary["terminal_outcome"])
    return {"task_id": value.task_id, "run_id": value.run_id,
            "status": value.status.value, "terminal_outcome": value.model_dump(mode="json")}
