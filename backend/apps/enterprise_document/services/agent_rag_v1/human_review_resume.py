"""Validate a persisted human decision and choose the minimum resume stage."""

from __future__ import annotations

from apps.enterprise_document.schemas.agent_rag_v1 import (
    HumanReviewResumeRequestSchema,
    HumanReviewResumeResultSchema,
    HumanReviewStateSchema,
    MultiAgentSharedStateSchema,
    ResumeRequestSchema,
    SectionStatus,
)

from .resume_router import ResumeRouter


class HumanReviewResumeError(ValueError):
    pass


class HumanReviewResumeService:
    def __init__(self, resume_router: ResumeRouter | None = None) -> None:
        self.resume_router = resume_router or ResumeRouter()

    def decide(
        self,
        state: MultiAgentSharedStateSchema,
        request: HumanReviewResumeRequestSchema,
    ) -> HumanReviewResumeResultSchema:
        if request.task_id != state.state_meta.task_id:
            raise HumanReviewResumeError("human review task identity mismatch")
        if request.input_snapshot_version != state.state_meta.input_snapshot_version:
            raise HumanReviewResumeError("human review decision snapshot is stale")
        if state.runtime_control.draining or state.runtime_control.cancelled:
            raise HumanReviewResumeError("task is not accepting resume work")
        review = state.human_reviews.get(request.review_id)
        if review is None:
            raise HumanReviewResumeError("human review request not found")
        if review.status not in {"pending", "waiting"}:
            raise HumanReviewResumeError("human review has already been resolved")

        section_ids = [review.section_id] if review.section_id else [
            section.section_id
            for section in sorted(
                state.section_states.values(),
                key=lambda item: item.section_order,
            )
            if section.applicable
            and section.execution.status != SectionStatus.SKIPPED
        ]
        if not section_ids:
            raise HumanReviewResumeError("human review has no resumable sections")
        updated_review = HumanReviewStateSchema(
            **{
                **review.model_dump(mode="python"),
                "status": "resolved",
                "decision_ref": request.decision_ref,
            }
        )
        resume = self.resume_router.decide(
            ResumeRequestSchema(
                task_id=request.task_id,
                section_ids=section_ids,
                trigger_kind=request.trigger_kind,
                trigger_ref=request.decision_ref,
            )
        )
        return HumanReviewResumeResultSchema(
            review=updated_review,
            resume_decision=resume,
        )
