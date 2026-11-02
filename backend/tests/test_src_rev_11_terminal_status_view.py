"""SRCREV-11: read-only status presentation from a sealed terminal outcome."""

from __future__ import annotations

from contextlib import ExitStack
import sqlite3
from unittest.mock import patch

import pytest

from application.terminal_outcome_projector import (
    project_later_failure,
    project_terminal,
    seal_terminal,
)
from application.terminal_outcome_schema import TerminalDiagnostic, TerminalOutcomeResultV1
from application.terminal_status_view import project_terminal_status
from apps.enterprise_document.schemas.agent_rag_v1.lifecycle import RunLifecycleStatus
from apps.enterprise_document.schemas.post_write.foundation import (
    AvailabilityState,
    EnforcementAction,
    OverallQualityState,
    ValidationCompletionItemState,
)
from apps.enterprise_document.services.agent_rag_v1.lifecycle_runtime import (
    RunAttemptLifecycleService,
)
from apps.enterprise_document.services.gate_enforcement import GateEnforcementController
from apps.enterprise_document.services.post_write.release import (
    PostWriteReleasePublicationService,
)
from model_gateway.fake_llm_client import FakeLLMClient
from schemas.common import ErrorSchema
from schemas.failure import FailureCategory
from schemas.status import ExecutionStatus


TASK_ID = "src11-task"
RUN_ID = "src11-run"
PHASE = "evaluate_document_gate"
RELEASE_REF = "post-write-release://sha256/" + "1" * 64


def _target_outcome(
    *,
    execution_status: ExecutionStatus = ExecutionStatus.SUCCESS,
    status: ExecutionStatus | EnforcementAction | ValidationCompletionItemState | None = None,
    control_action: EnforcementAction = EnforcementAction.OUTPUT,
    quality_state: OverallQualityState = OverallQualityState.PASS,
    availability_state: AvailabilityState = AvailabilityState.AVAILABLE,
    published: bool = True,
    artifact_exists: bool = True,
    diagnostics: tuple[TerminalDiagnostic, ...] = (),
) -> TerminalOutcomeResultV1:
    """Build a valid source fact; test expectations below remain handwritten."""
    base = project_terminal(
        task_id=TASK_ID,
        run_id=RUN_ID,
        execution_status=execution_status,
        phase=PHASE,
    )
    source = base.model_dump(mode="python", exclude={"result_id"})
    source.update(
        status=status if status is not None else execution_status,
        run_status=RunLifecycleStatus.SUCCESS,
        control_action=control_action,
        quality_state=quality_state,
        availability_state=availability_state,
        publication_exists=published,
        publication_ref=RELEASE_REF if published else None,
        artifact_exists=artifact_exists,
        is_partial=(
            execution_status is ExecutionStatus.PARTIAL_SUCCESS
            or control_action is EnforcementAction.OUTPUT_BEST_EFFORT
        ),
        is_best_effort=control_action is EnforcementAction.OUTPUT_BEST_EFFORT,
        diagnostics=diagnostics,
    )
    return seal_terminal(source)


def _view(outcome: TerminalOutcomeResultV1, *, source_mode: str = "verified_target") -> dict:
    return project_terminal_status(
        outcome,
        source_mode=source_mode,
        expected_task_id=TASK_ID,
        expected_run_id=RUN_ID,
        expected_phase=PHASE,
    )


@pytest.mark.unit
def test_status_01_pass_output_published() -> None:
    outcome = _target_outcome()

    assert _view(outcome) == {
        "status_projection_version": "terminal-status-view-v1",
        "source_mode": "verified_target",
        "result_id": outcome.result_id,
        "task_id": TASK_ID,
        "run_id": RUN_ID,
        "phase": PHASE,
        "execution_status": "success",
        "status": "success",
        "run_status": "success",
        "control_action": "OUTPUT",
        "gate_enforcement_status": "continued",
        "business_quality_status": "passed",
        "availability_state": "AVAILABLE",
        "publication_ref": RELEASE_REF,
        "publication_exists": True,
        "release_status": "published",
    }


@pytest.mark.unit
def test_status_02_fail_best_effort_published_retains_all_axes() -> None:
    outcome = _target_outcome(
        execution_status=ExecutionStatus.PARTIAL_SUCCESS,
        status=EnforcementAction.OUTPUT_BEST_EFFORT,
        control_action=EnforcementAction.OUTPUT_BEST_EFFORT,
        quality_state=OverallQualityState.FAIL,
        availability_state=AvailabilityState.BEST_EFFORT,
    )

    assert _view(outcome) == {
        "status_projection_version": "terminal-status-view-v1",
        "source_mode": "verified_target",
        "result_id": outcome.result_id,
        "task_id": TASK_ID,
        "run_id": RUN_ID,
        "phase": PHASE,
        "execution_status": "partial_success",
        "status": "OUTPUT_BEST_EFFORT",
        "run_status": "success",
        "control_action": "OUTPUT_BEST_EFFORT",
        "gate_enforcement_status": "continued",
        "business_quality_status": "failed",
        "availability_state": "BEST_EFFORT",
        "publication_ref": RELEASE_REF,
        "publication_exists": True,
        "release_status": "published",
    }


@pytest.mark.unit
def test_status_03_unresolved_differs_from_missing_quality() -> None:
    unresolved = _target_outcome(
        execution_status=ExecutionStatus.PARTIAL_SUCCESS,
        status=EnforcementAction.OUTPUT_BEST_EFFORT,
        control_action=EnforcementAction.OUTPUT_BEST_EFFORT,
        quality_state=OverallQualityState.UNRESOLVED,
        availability_state=AvailabilityState.BEST_EFFORT,
    )
    early = project_terminal(
        task_id=TASK_ID,
        run_id=RUN_ID,
        execution_status=ExecutionStatus.FAILED,
        phase=PHASE,
    )

    assert _view(unresolved)["business_quality_status"] == "unresolved"
    assert _view(early, source_mode="legacy_or_early")["business_quality_status"] is None


@pytest.mark.unit
def test_status_04_block_with_internal_artifact_is_not_published() -> None:
    outcome = _target_outcome(
        status=EnforcementAction.BLOCK,
        control_action=EnforcementAction.BLOCK,
        quality_state=OverallQualityState.FAIL,
        availability_state=AvailabilityState.WITHHELD,
        published=False,
    )
    assert outcome.artifact_exists is True

    assert _view(outcome) == {
        "status_projection_version": "terminal-status-view-v1",
        "source_mode": "verified_target",
        "result_id": outcome.result_id,
        "task_id": TASK_ID,
        "run_id": RUN_ID,
        "phase": PHASE,
        "execution_status": "success",
        "status": "BLOCK",
        "run_status": "success",
        "control_action": "BLOCK",
        "gate_enforcement_status": "blocked",
        "business_quality_status": "failed",
        "availability_state": "WITHHELD",
        "publication_ref": None,
        "publication_exists": False,
        "release_status": "not_published",
    }


@pytest.mark.unit
def test_status_05_verified_target_output_without_publication() -> None:
    outcome = _target_outcome(
        execution_status=ExecutionStatus.FAILED,
        status=ValidationCompletionItemState.TECHNICAL_FAILURE,
        published=False,
    )

    assert _view(outcome) == {
        "status_projection_version": "terminal-status-view-v1",
        "source_mode": "verified_target",
        "result_id": outcome.result_id,
        "task_id": TASK_ID,
        "run_id": RUN_ID,
        "phase": PHASE,
        "execution_status": "failed",
        "status": "TECHNICAL_FAILURE",
        "run_status": "success",
        "control_action": "OUTPUT",
        "gate_enforcement_status": "continued",
        "business_quality_status": "passed",
        "availability_state": "AVAILABLE",
        "publication_ref": None,
        "publication_exists": False,
        "release_status": "not_published",
    }


@pytest.mark.unit
def test_correct_action_projects_correction_required_without_publication() -> None:
    outcome = _target_outcome(
        status=EnforcementAction.CORRECT,
        control_action=EnforcementAction.CORRECT,
        quality_state=OverallQualityState.FAIL,
        availability_state=AvailabilityState.WITHHELD,
        published=False,
    )

    assert _view(outcome)["gate_enforcement_status"] == "correction_required"
    assert _view(outcome)["business_quality_status"] == "failed"
    assert _view(outcome)["release_status"] == "not_published"


@pytest.mark.unit
@pytest.mark.parametrize(
    ("execution_status", "failure_category", "expected_status"),
    [
        (ExecutionStatus.FAILED, FailureCategory.VALIDATION, "TECHNICAL_FAILURE"),
        (ExecutionStatus.FAILED, FailureCategory.RESOURCE_EXHAUSTED, "resource_exhausted"),
        (ExecutionStatus.CANCELLED, FailureCategory.CANCELLED, "cancelled"),
    ],
    ids=["early-validation", "resource-exhausted", "cancelled"],
)
def test_status_06_early_failure_does_not_invent_missing_business_axes(
    execution_status: ExecutionStatus,
    failure_category: FailureCategory,
    expected_status: str,
) -> None:
    error = ErrorSchema(
        error_code="CONTROLLED_EARLY_FAILURE",
        error_type="ControlledFailure",
        message="Controlled early failure",
        failure_category=failure_category,
    )
    outcome = project_terminal(
        task_id=TASK_ID,
        run_id=RUN_ID,
        execution_status=execution_status,
        phase=PHASE,
        error=error,
    )

    assert _view(outcome, source_mode="legacy_or_early") == {
        "status_projection_version": "terminal-status-view-v1",
        "source_mode": "legacy_or_early",
        "result_id": outcome.result_id,
        "task_id": TASK_ID,
        "run_id": RUN_ID,
        "phase": PHASE,
        "execution_status": execution_status.value,
        "status": expected_status,
        "run_status": None,
        "control_action": None,
        "gate_enforcement_status": None,
        "business_quality_status": None,
        "availability_state": None,
        "publication_ref": None,
        "publication_exists": False,
        "release_status": None,
    }


@pytest.mark.unit
def test_status_07_published_later_failure_keeps_publication_and_quality() -> None:
    published = _target_outcome()
    error = ErrorSchema(
        error_code="CONTROLLED_PERSIST_FAILURE",
        error_type="ControlledFailure",
        message="Controlled postpublication failure",
        failure_category=FailureCategory.VALIDATION,
    )
    outcome = project_later_failure(
        published,
        error=error,
        phase="persist_deliverable",
    )

    assert project_terminal_status(
        outcome,
        source_mode="verified_target",
        expected_task_id=TASK_ID,
        expected_run_id=RUN_ID,
        expected_phase="persist_deliverable",
    ) == {
        "status_projection_version": "terminal-status-view-v1",
        "source_mode": "verified_target",
        "result_id": outcome.result_id,
        "task_id": TASK_ID,
        "run_id": RUN_ID,
        "phase": "persist_deliverable",
        "execution_status": "failed",
        "status": "TECHNICAL_FAILURE",
        "run_status": "success",
        "control_action": "OUTPUT",
        "gate_enforcement_status": "continued",
        "business_quality_status": "passed",
        "availability_state": "AVAILABLE",
        "publication_ref": RELEASE_REF,
        "publication_exists": True,
        "release_status": "published",
    }
    assert published.result_id != outcome.result_id
    assert published.publication_ref == outcome.publication_ref
    assert published.quality_state == outcome.quality_state
    assert outcome.failure_category is FailureCategory.VALIDATION


@pytest.mark.unit
def test_status_08_shadow_counterfactual_diagnostic_does_not_override_control() -> None:
    diagnostic = TerminalDiagnostic(
        diagnostic_ref="sha256:" + "2" * 64,
        code="WOULD_BLOCK_IN_STRICT",
        message="Legacy shadow counterfactual",
    )
    outcome = _target_outcome(diagnostics=(diagnostic,))

    view = _view(outcome)
    assert view["control_action"] == "OUTPUT"
    assert view["gate_enforcement_status"] == "continued"
    assert view["business_quality_status"] == "passed"
    assert view["release_status"] == "published"
    assert "would_block_in_strict" not in view

    legacy = project_terminal(
        task_id=TASK_ID,
        run_id=RUN_ID,
        execution_status=ExecutionStatus.SUCCESS,
        phase=PHASE,
    )
    source = legacy.model_dump(mode="python", exclude={"result_id"})
    source["diagnostics"] = (diagnostic,)
    legacy = seal_terminal(source)
    legacy_view = _view(legacy, source_mode="legacy_or_early")
    assert legacy_view["gate_enforcement_status"] is None
    assert legacy_view["business_quality_status"] is None
    assert legacy_view["release_status"] is None
    assert "would_block_in_strict" not in legacy_view


@pytest.mark.unit
def test_status_10_projection_is_pure_and_preserves_sealed_input() -> None:
    outcome = _target_outcome()
    before = outcome.model_dump(mode="json")
    before_result_id = outcome.result_id

    def forbidden(*_args, **_kwargs):
        raise AssertionError("status projection called a business owner")

    with ExitStack() as stack:
        stack.enter_context(patch("builtins.open", side_effect=forbidden))
        stack.enter_context(patch.object(sqlite3, "connect", side_effect=forbidden))
        for owner, method in (
            (FakeLLMClient, "generate"),
            (GateEnforcementController, "resolve"),
            (PostWriteReleasePublicationService, "publish"),
            (RunAttemptLifecycleService, "claim_new_run"),
            (RunAttemptLifecycleService, "transition_run"),
        ):
            stack.enter_context(patch.object(owner, method, side_effect=forbidden))
        first = _view(outcome)
        second = _view(outcome)

    assert first == second
    assert first["result_id"] == before_result_id
    assert outcome.model_dump(mode="json") == before
    assert outcome.result_id == before_result_id


@pytest.mark.unit
def test_status_11_rejects_wrong_identity_phase_mode_and_missing_outcome() -> None:
    outcome = _target_outcome()

    with pytest.raises((TypeError, ValueError)):
        project_terminal_status(
            outcome,
            source_mode="guessed_target",
            expected_task_id=TASK_ID,
            expected_run_id=RUN_ID,
        )
    for task_id, run_id, phase in (
        ("wrong-task", RUN_ID, PHASE),
        (TASK_ID, "wrong-run", PHASE),
        (TASK_ID, RUN_ID, "wrong-phase"),
    ):
        with pytest.raises((TypeError, ValueError)):
            project_terminal_status(
                outcome,
                source_mode="verified_target",
                expected_task_id=task_id,
                expected_run_id=run_id,
                expected_phase=phase,
            )
    for invalid_outcome in (None, outcome.model_dump(mode="json")):
        with pytest.raises((TypeError, ValueError)):
            project_terminal_status(
                invalid_outcome,
                source_mode="verified_target",
                expected_task_id=TASK_ID,
                expected_run_id=RUN_ID,
            )
    for invalid_outcome in (
        outcome.model_copy(update={"schema_version": "terminal_outcome_v2"}),
        outcome.model_copy(update={"result_id": "sha256:" + "0" * 64}),
    ):
        with pytest.raises(ValueError):
            _view(invalid_outcome)
