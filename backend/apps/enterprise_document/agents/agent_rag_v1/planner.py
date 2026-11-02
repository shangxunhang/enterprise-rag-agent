"""Planner Agent：只产出计划结果和 execution_plan Patch 建议。"""

from __future__ import annotations

from apps.enterprise_document.schemas.agent_rag_v1 import (
    AgentExecutionContextSchema,
    AgentInvocationStatus,
    AgentPayloadType,
    AgentRole,
    PlannerInvocationInputSchema,
    PlannerResultPayloadSchema,
    StatePatchRequestSchema,
)

from .base import StatelessBusinessAgent


class PlannerAgent(StatelessBusinessAgent):
    role = AgentRole.PLANNER
    payload_type = AgentPayloadType.PLANNER_RESULT
    input_schema = PlannerInvocationInputSchema
    payload_schema = PlannerResultPayloadSchema

    def _validate_payload(
        self,
        context: AgentExecutionContextSchema,
        invocation_input: PlannerInvocationInputSchema,
        payload: PlannerResultPayloadSchema,
    ) -> None:
        del context
        if invocation_input.project_input is None:
            return
        plan = payload.document_plan
        if plan is None:
            raise ValueError(
                "production planner result requires canonical DocumentPlanSchema"
            )
        if plan.document_id != invocation_input.document_id:
            raise ValueError("planner result document_id does not match invocation")
        if payload.plan_revision != invocation_input.plan_revision:
            raise ValueError("planner result revision does not match invocation")
        section_ids = [section.section_id for section in plan.sections]
        if payload.section_task_ids != section_ids:
            raise ValueError("planner result section_task_ids do not match plan")
        section_titles = [section.section_title for section in plan.sections]
        if section_titles != invocation_input.required_sections:
            raise ValueError("planner result sections do not match required_sections")

    def _build_patches(
        self,
        context: AgentExecutionContextSchema,
        invocation_input: PlannerInvocationInputSchema,
        payload: PlannerResultPayloadSchema,
    ) -> list[StatePatchRequestSchema]:
        del invocation_input
        return [
            self._patch(
                context,
                target_domain="execution_plan",
                section_id=None,
                values={
                    "plan_ref": payload.plan_ref,
                    "plan_version": payload.plan_revision,
                    "active_plan_revision_id": (
                        f"plan-revision:{payload.plan_revision}"
                    ),
                    "section_order": payload.section_task_ids,
                },
                reason_code="planner_result_ready",
            )
        ]

    def _result_status(
        self,
        payload: PlannerResultPayloadSchema,
    ) -> AgentInvocationStatus:
        del payload
        return AgentInvocationStatus.COMPLETED

    def _emitted_signals(
        self,
        payload: PlannerResultPayloadSchema,
    ) -> list[str]:
        del payload
        return ["plan_ready"]
