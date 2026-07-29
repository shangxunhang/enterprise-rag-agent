"""根据输入变化和恢复原因选择最小恢复入口。"""

from __future__ import annotations

from apps.enterprise_document.schemas.agent_rag_v1 import (
    ResumeDecisionSchema,
    ResumeRequestSchema,
    ResumeStage,
    ResumeTriggerKind,
)


class ResumeRouter:
    _STAGES = {
        ResumeTriggerKind.EXCEL_CHANGED: ResumeStage.PLANNER,
        ResumeTriggerKind.TEMPLATE_CHANGED: ResumeStage.PLANNER,
        ResumeTriggerKind.STRATEGY_MAY_CHANGE: ResumeStage.PLANNER,
        ResumeTriggerKind.VERIFIER_REPLAN: ResumeStage.PLANNER,
        ResumeTriggerKind.NEW_MATERIAL: ResumeStage.RESEARCH,
        ResumeTriggerKind.VERIFIER_REQUESTED_EVIDENCE: ResumeStage.RESEARCH,
        ResumeTriggerKind.EVIDENCE_READY_REWRITE: ResumeStage.WRITER,
        ResumeTriggerKind.DRAFT_REVISED: ResumeStage.VERIFIER,
    }
    _INVALIDATED = {
        ResumeStage.PLANNER: [
            "execution_plan",
            "section.research",
            "section.writing",
            "section.claims",
            "section.verification",
            "section.execution",
        ],
        ResumeStage.RESEARCH: [
            "section.research",
            "section.writing",
            "section.claims",
            "section.verification",
        ],
        ResumeStage.WRITER: [
            "section.writing",
            "section.claims",
            "section.verification",
        ],
        ResumeStage.VERIFIER: [
            "section.verification",
        ],
    }
    _PRESERVED = {
        ResumeStage.PLANNER: [],
        ResumeStage.RESEARCH: ["execution_plan"],
        ResumeStage.WRITER: ["execution_plan", "section.research"],
        ResumeStage.VERIFIER: [
            "execution_plan",
            "section.research",
            "section.writing",
            "section.claims",
        ],
    }

    def decide(self, request: ResumeRequestSchema) -> ResumeDecisionSchema:
        stage = self._STAGES[request.trigger_kind]
        return ResumeDecisionSchema(
            stage=stage,
            affected_section_ids=request.section_ids,
            invalidated_domains=self._INVALIDATED[stage],
            preserved_domains=self._PRESERVED[stage],
            reason_code=request.trigger_kind.value,
        )
