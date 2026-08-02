"""Research Agent：受控检索后只写 section.research。"""

from __future__ import annotations

from apps.enterprise_document.schemas.agent_rag_v1 import (
    AgentExecutionContextSchema,
    AgentInvocationStatus,
    AgentPayloadType,
    AgentRole,
    ResearchInvocationInputSchema,
    ResearchRecommendedAction,
    ResearchResultPayloadSchema,
    StatePatchRequestSchema,
)

from .base import StatelessBusinessAgent


class ResearchAgent(StatelessBusinessAgent):
    role = AgentRole.RESEARCH
    payload_type = AgentPayloadType.RESEARCH_RESULT
    input_schema = ResearchInvocationInputSchema
    payload_schema = ResearchResultPayloadSchema

    def _validate_payload(
        self,
        context: AgentExecutionContextSchema,
        invocation_input: ResearchInvocationInputSchema,
        payload: ResearchResultPayloadSchema,
    ) -> None:
        if payload.section_id != context.section_id:
            raise ValueError("research payload section does not match context")
        if payload.evidence_summary.section_id != payload.section_id:
            raise ValueError("research evidence summary section does not match payload")
        if payload.research_round < 0:
            raise ValueError("research_round cannot be negative")

    def _build_patches(
        self,
        context: AgentExecutionContextSchema,
        invocation_input: ResearchInvocationInputSchema,
        payload: ResearchResultPayloadSchema,
    ) -> list[StatePatchRequestSchema]:
        del invocation_input
        summary = payload.evidence_summary
        return [
            self._patch(
                context,
                target_domain="section.research",
                section_id=payload.section_id,
                values={
                    "evidence_package_ref": payload.evidence_package_ref,
                    "evidence_ids": summary.accepted_evidence_ids,
                    "evidence_gap_count": len(summary.evidence_gaps),
                },
                reason_code="research_result_ready",
            )
        ]

    def _result_status(
        self,
        payload: ResearchResultPayloadSchema,
    ) -> AgentInvocationStatus:
        action = payload.evidence_summary.recommended_action
        if action == ResearchRecommendedAction.HUMAN_REVIEW:
            return AgentInvocationStatus.BLOCKED
        if action == ResearchRecommendedAction.PARTIAL_DELIVERY:
            return AgentInvocationStatus.PARTIAL
        return AgentInvocationStatus.COMPLETED

    def _emitted_signals(
        self,
        payload: ResearchResultPayloadSchema,
    ) -> list[str]:
        return [payload.evidence_summary.recommended_action.value]
