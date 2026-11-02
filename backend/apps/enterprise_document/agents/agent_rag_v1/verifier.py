"""Verifier Agent：四种模式共享外壳但写入不同验证域。"""

from __future__ import annotations

from typing import Callable
from apps.enterprise_document.schemas.agent_rag_v1.verification import (
    VerifierClaimInputSchema, VerifierSemanticResultCandidateSchema,
)
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

    @staticmethod
    def evaluate_canonical_claim(
        invocation: VerifierClaimInputSchema,
        *,
        evaluator: Callable[[VerifierClaimInputSchema], VerifierSemanticResultCandidateSchema],
    ) -> VerifierSemanticResultCandidateSchema:
        """Target SIF-07 cognition only; canonical commit belongs to the caller.

        This typed entry does not revive the legacy Section/StatePatch protocol.
        The evaluator owns one bounded model request, not source or lifecycle truth.
        """
        invocation = VerifierClaimInputSchema.model_validate(invocation)
        if (invocation.claim_ref.claim_occurrence_ref != invocation.claim_occurrence_ref
                or invocation.claim_occurrence_ref.paragraph_version_ref != invocation.paragraph_version_ref
                or invocation.binding_facts_ref.claim_ref != invocation.claim_ref):
            raise ValueError("canonical Verifier input mixes Claim identities")
        result = VerifierSemanticResultCandidateSchema.model_validate(evaluator(invocation))
        if (result.claim_ref != invocation.claim_ref
                or result.binding_facts_ref != invocation.binding_facts_ref
                or result.claim_evidence_support_ref != invocation.claim_evidence_support_ref
                or result.verifier_semantic_input_sha256 != invocation.verifier_semantic_input_sha256):
            raise ValueError("canonical Verifier result does not bind its exact input")
        return result

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
