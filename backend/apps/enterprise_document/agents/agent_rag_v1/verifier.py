"""Verifier Agent：四种模式共享外壳但写入不同验证域。"""

from __future__ import annotations

from apps.enterprise_document.schemas.agent_rag_v1 import (
    AgentExecutionContextSchema,
    AgentInvocationStatus,
    AgentPayloadType,
    AgentRole,
    StatePatchRequestSchema,
    VerifierAction,
    VerifierExecutionMode,
    VerifierInvocationInputSchema,
    VerifierResultPayloadSchema,
)

from .base import StatelessBusinessAgent


class VerifierAgent(StatelessBusinessAgent):
    role = AgentRole.VERIFIER
    payload_type = AgentPayloadType.VERIFIER_RESULT
    input_schema = VerifierInvocationInputSchema
    payload_schema = VerifierResultPayloadSchema

    def _validate_payload(
        self,
        context: AgentExecutionContextSchema,
        invocation_input: VerifierInvocationInputSchema,
        payload: VerifierResultPayloadSchema,
    ) -> None:
        del context
        if payload.execution_mode != invocation_input.execution_mode:
            raise ValueError("verifier payload mode does not match invocation")
        if (
            invocation_input.execution_mode
            in {
                VerifierExecutionMode.PARAGRAPH_PRECHECK,
                VerifierExecutionMode.SECTION_VERIFY,
            }
            and not invocation_input.section_id
        ):
            raise ValueError("section verifier mode requires section_id")
        if (
            invocation_input.section_snapshot is not None
            and invocation_input.section_id is not None
            and invocation_input.section_snapshot.section_id
            != invocation_input.section_id
        ):
            raise ValueError("verifier section snapshot does not match invocation")

    def _build_patches(
        self,
        context: AgentExecutionContextSchema,
        invocation_input: VerifierInvocationInputSchema,
        payload: VerifierResultPayloadSchema,
    ) -> list[StatePatchRequestSchema]:
        if payload.execution_mode in {
            VerifierExecutionMode.PARAGRAPH_PRECHECK,
            VerifierExecutionMode.SECTION_VERIFY,
        }:
            domain = "section.verification"
            section_id = invocation_input.section_id
            values = {
                "execution_mode": payload.execution_mode,
                "recommended_action": payload.action,
                "verification_ref": payload.verification_ref,
                "issue_ids": payload.issue_ids,
                "accepted_paragraph_ids": payload.accepted_paragraph_ids,
                "rejected_paragraph_ids": payload.rejected_paragraph_ids,
            }
        else:
            domain = "document_validation"
            section_id = None
            values = {
                "verification_ref": payload.verification_ref,
                "status": payload.action.value,
                "issue_ids": payload.issue_ids,
            }
        return [
            self._patch(
                context,
                target_domain=domain,
                section_id=section_id,
                values=values,
                reason_code=f"verifier_{payload.execution_mode.value}_ready",
            )
        ]

    def _result_status(
        self,
        payload: VerifierResultPayloadSchema,
    ) -> AgentInvocationStatus:
        if payload.action == VerifierAction.HUMAN_REVIEW:
            return AgentInvocationStatus.BLOCKED
        if payload.action == VerifierAction.PARTIAL_ACCEPT:
            return AgentInvocationStatus.PARTIAL
        return AgentInvocationStatus.COMPLETED

    def _emitted_signals(
        self,
        payload: VerifierResultPayloadSchema,
    ) -> list[str]:
        return [payload.action.value]
