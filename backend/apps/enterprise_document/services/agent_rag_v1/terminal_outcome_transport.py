"""Exact owner reads for lane 352. No Gate evaluation or publication here."""
from __future__ import annotations

from application.terminal_outcome_projector import opaque_ref, project_terminal, safe_identifier, diagnostic, seal_terminal, error_projection
from application.terminal_outcome_schema import TerminalArtifact
from apps.enterprise_document.schemas.post_write.foundation import EnforcementAction
from core.runtime.recovery import current_durable_recovery
from schemas.common import ErrorSchema
from schemas.failure import FailureCategory, FailureOrigin
from schemas.status import ExecutionStatus
from .budget_manager import CanonicalBudgetCapacityExceeded, CanonicalBudgetDeadlineExceeded, CanonicalBudgetDraining


class TargetPostWriteCompositionError(RuntimeError):
    """Target owner composition cannot prove one exact prerequisite."""


class TargetPostWriteInterrupted(TargetPostWriteCompositionError):
    """Private carrier of exact completed owner refs and the original exception."""
    def __init__(self, cause, completed_sections, completion=None):
        super().__init__(str(cause))
        self.cause = cause
        self.completed_sections = tuple(completed_sections)
        self.completion = completion


class TerminalOutcomeStageFailure(Exception):
    def __init__(self, error, outcome, *, completion=None):
        super().__init__(error.message)
        self.error = error
        self.terminal_outcome = outcome
        self.completion = completion


def declared_error(exc, runtime_support, *, node):
    """Preserve typed producer facts; unknown errors stay unknown, not guessed."""
    original = getattr(exc, "error", None)
    if isinstance(original, ErrorSchema):
        return original
    category = getattr(exc, "failure_category", FailureCategory.UNKNOWN)
    origin = getattr(exc, "failure_origin", FailureOrigin.UNKNOWN)
    if isinstance(exc, (CanonicalBudgetCapacityExceeded, CanonicalBudgetDeadlineExceeded, CanonicalBudgetDraining)):
        category, origin = FailureCategory.RESOURCE_EXHAUSTED, FailureOrigin.CANONICAL_BUDGET
    error = runtime_support.error(getattr(exc, "error_code", "SCHEME_GENERATION_STAGE_FAILED"),
        exc, node=node, failure_category=category,
        retryable=bool(getattr(exc, "retryable", False)),
        requires_human=bool(getattr(exc, "requires_human", True)),
        user_message="A workflow step could not complete.")
    return error.model_copy(update={"requires_new_input": bool(getattr(exc, "requires_new_input", error.requires_new_input)),
        "recoverable": bool(getattr(exc, "recoverable", error.recoverable)),
        "details": {**error.details, "failure_origin": FailureOrigin(origin).value,
        "technical_failure_ref": getattr(exc, "technical_failure_ref", None)}})


def project_post_write(composition, *, payload, sections, completion=None, error=None, recovery_errors=()):
    control = (composition.control_store.resolve_exact(completion.final_document.control_decision_ref)
               if completion else None)
    artifacts, completed, fallback = [], [], False
    for item in sections:
        section_control = composition.control_store.resolve_exact(item.control_decision_ref)
        eligibility = composition.section_eligibility.resolve(section_key=item.section_id,
            control_decision_ref=item.control_decision_ref)
        completed.append(item.section_id)
        generation = getattr(section_control.input_manifest, "generation_class", None)
        fallback |= generation is not None and generation.value == "fallback_write"
        if eligibility.candidate is not None:
            record = composition.artifact_service.resolve_exact(item.artifact_state_ref)
            content = record.canonical_payload_ref.get("content", "")
            # Resolve the exact owner record, not the checkpoint's display text.
            artifacts.append(TerminalArtifact(artifact_ref=opaque_ref(item.artifact_state_ref),
                section_id=safe_identifier(item.section_id), content=content,
                quality_state=section_control.quality_state_passthrough,
                availability_state=section_control.availability_projection.state))
    all_sections = [x.section.section_id for x in payload.section_execution_results]
    unfinished = [x for x in all_sections if x not in completed]
    if completion is None:
        unfinished.append("document_validation_and_release")
    elif completion.published_release is None:
        unfinished.append("publication")
    status = (ExecutionStatus.SUCCESS if control and control.enforcement_action is EnforcementAction.OUTPUT
              else ExecutionStatus.PARTIAL_SUCCESS if control else
              ExecutionStatus.RETRYABLE_FAILED if error and error.retryable else ExecutionStatus.FAILED)
    if error is not None:
        status = ExecutionStatus.RETRYABLE_FAILED if error.retryable else ExecutionStatus.FAILED
    # A completed, trustworthy Document remains reviewable when only the
    # downstream Publisher fails. Preserve Quality and report partial delivery.
    publication_failure = (
        completion.publication_failure if completion is not None else None
    )
    if publication_failure is not None and error is None:
        status = ExecutionStatus.PARTIAL_SUCCESS
    if control and control.enforcement_action is EnforcementAction.BLOCK:
        # Legacy execution truth is independent of withheld publication.
        status = ExecutionStatus.SUCCESS
        artifacts = []
    if (error is not None and error.failure_category is FailureCategory.RESOURCE_EXHAUSTED
            and artifacts):
        status = ExecutionStatus.PARTIAL_SUCCESS
    # Independent integrity/technical failure does not grant partial export.
    if error is not None and error.failure_category is not FailureCategory.RESOURCE_EXHAUSTED:
        artifacts = []
    run = composition.lifecycle_service.load_run_lifecycle(payload.run_id) if composition.lifecycle_service else None
    recovery = current_durable_recovery()
    outcome = project_terminal(task_id=payload.task_id, run_id=payload.run_id, execution_status=status,
        phase="evaluate_document_gate", run_status=run.status if run else None, control=control,
        publication=completion.published_release if completion else None, completed=completed,
        unfinished=unfinished, artifacts=artifacts if completion is None else (), error=error,
        fallback=fallback, recovery_attempts=recovery.attempt_number if recovery else 0,
        recovery_refs=(recovery.reason, *recovery_errors) if recovery else (),
        artifact_exists=bool(sections) or completion is not None)
    # Technical validation accounting can coexist with terminal BEST_EFFORT.
    # Keep each original category separately from the final quality axis.
    validation_refs = [x.validation_completion_ref for x in sections]
    if completion:
        validation_refs.append(completion.final_document.validation_completion_ref)
    extra = []
    if publication_failure is not None:
        extra.append(diagnostic(
            "TARGET_RELEASE_PUBLICATION_FAILED",
            publication_failure,
            "The formal publication attempt failed; the exact completed "
            "Document may still be available for human review.",
        ))
    for previous_error in recovery_errors:
        previous = error_projection(previous_error)
        extra.append(diagnostic(previous_error.error_code, previous_error,
            "A prior attempt failed; the actual durable recovery attempt is recorded.").model_copy(
                update={"failure_category": previous["failure_category"], "failure_origin": previous["failure_origin"]}))
    for ref in validation_refs:
        validation = composition.validation_store.resolve_completion(ref)
        for item in validation.items:
            failure = item.technical_failure_ref
            if failure is not None:
                category = FailureCategory(failure.get("category", FailureCategory.UNKNOWN))
                origin = FailureOrigin(failure.get("origin", FailureOrigin.UNKNOWN))
                extra.append(diagnostic(failure.get("error_code", "TECHNICAL_FAILURE"), failure,
                    "A quality validation step could not execute; its technical cause is retained.").model_copy(
                        update={"failure_category": category, "failure_origin": origin}))
    if extra:
        data = outcome.model_dump(mode="python", exclude={"result_id"})
        data["diagnostics"] = (*outcome.diagnostics, *extra)
        outcome = seal_terminal(data)
    return outcome
