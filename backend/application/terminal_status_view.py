"""Pure compatibility view of an already assembled terminal outcome.

The caller owns authorization and the source-mode assertion. In particular,
``verified_target`` means the Target completion boundary checked the exact
publication owner; a legacy or early-failure default of ``False`` does not.
This view never grants publication, retry, or lifecycle authority.
"""
from __future__ import annotations

from typing import Literal

from application.terminal_outcome_schema import TerminalOutcomeResultV1
from apps.enterprise_document.schemas.post_write.foundation import (
    EnforcementAction,
    OverallQualityState,
)


STATUS_PROJECTION_VERSION = "terminal-status-view-v1"
StatusSourceMode = Literal["verified_target", "legacy_or_early"]

_ENFORCEMENT_STATUS = {
    EnforcementAction.OUTPUT: "continued",
    EnforcementAction.OUTPUT_BEST_EFFORT: "continued",
    EnforcementAction.BLOCK: "blocked",
    EnforcementAction.CORRECT: "correction_required",
}
_QUALITY_STATUS = {
    OverallQualityState.PASS: "passed",
    OverallQualityState.FAIL: "failed",
    OverallQualityState.UNRESOLVED: "unresolved",
}


def project_terminal_status(
    outcome: TerminalOutcomeResultV1,
    *,
    source_mode: StatusSourceMode,
    expected_task_id: str,
    expected_run_id: str,
    expected_phase: str | None = None,
) -> dict[str, str | bool | None]:
    """Return a new display view, preserving absent owner facts as ``None``.

    Expected identities come from the authorized caller, not from the outcome.
    The caller must choose source_mode from its explicit assembly/read boundary;
    the outcome's phase, status, diagnostics, or publication bool cannot prove
    that a missing publication was checked.
    """
    if source_mode not in ("verified_target", "legacy_or_early"):
        raise ValueError("unsupported terminal status source mode")
    if not isinstance(outcome, TerminalOutcomeResultV1):
        raise TypeError("terminal status view requires TerminalOutcomeResultV1")
    # A copied Pydantic model can bypass its validators. Revalidate the content
    # identity as well as the schema without changing the supplied instance.
    value = TerminalOutcomeResultV1.model_validate(outcome.model_dump(mode="json"))
    if (value.task_id, value.run_id) != (expected_task_id, expected_run_id):
        raise ValueError("terminal status view identity differs from expected Run")
    if expected_phase is not None and value.phase != expected_phase:
        raise ValueError("terminal status view phase differs from expected phase")

    action = value.control_action
    quality = value.quality_state
    return {
        "status_projection_version": STATUS_PROJECTION_VERSION,
        "source_mode": source_mode,
        "result_id": value.result_id,
        "task_id": value.task_id,
        "run_id": value.run_id,
        "phase": value.phase,
        "execution_status": value.execution_status.value,
        "status": value.status.value,
        "run_status": value.run_status.value if value.run_status is not None else None,
        "control_action": action.value if action is not None else None,
        "gate_enforcement_status": _ENFORCEMENT_STATUS[action] if action is not None else None,
        "business_quality_status": _QUALITY_STATUS[quality] if quality is not None else None,
        "availability_state": value.availability_state.value if value.availability_state is not None else None,
        "publication_ref": value.publication_ref,
        "publication_exists": value.publication_exists,
        "release_status": (
            "published" if value.publication_exists else
            "publication_unverified" if any(
                item.code == "TARGET_RELEASE_PUBLICATION_FAILED"
                for item in value.diagnostics
            ) else
            "not_published" if source_mode == "verified_target" else None
        ),
    }
