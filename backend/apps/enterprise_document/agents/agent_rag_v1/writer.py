"""Writer Agent：按 Paragraph Artifact 结果建议 section.writing Patch。"""

from __future__ import annotations

from apps.enterprise_document.schemas.agent_rag_v1 import (
    AgentExecutionContextSchema,
    AgentInvocationStatus,
    AgentPayloadType,
    AgentRole,
    StatePatchRequestSchema,
    WriterInvocationInputSchema,
    WriterResultPayloadSchema,
)

from .base import StatelessBusinessAgent


class WriterAgent(StatelessBusinessAgent):
    role = AgentRole.WRITER
    payload_type = AgentPayloadType.WRITER_RESULT
    input_schema = WriterInvocationInputSchema
    payload_schema = WriterResultPayloadSchema

    def _validate_payload(
        self,
        context: AgentExecutionContextSchema,
        invocation_input: WriterInvocationInputSchema,
        payload: WriterResultPayloadSchema,
    ) -> None:
        if payload.section_id != context.section_id:
            raise ValueError("writer payload section does not match context")
        actual_ids = [paragraph.paragraph_id for paragraph in payload.paragraphs]
        if len(actual_ids) != len(set(actual_ids)):
            raise ValueError("writer paragraph ids must be unique")
        if payload.generation_complete and set(actual_ids) != set(
            payload.expected_paragraph_ids
        ):
            raise ValueError(
                "completed writer result must contain every expected paragraph"
            )

    def _build_patches(
        self,
        context: AgentExecutionContextSchema,
        invocation_input: WriterInvocationInputSchema,
        payload: WriterResultPayloadSchema,
    ) -> list[StatePatchRequestSchema]:
        del invocation_input
        return [
            self._patch(
                context,
                target_domain="section.writing",
                section_id=payload.section_id,
                values={
                    "draft_ref": payload.draft_ref,
                    "draft_version": payload.draft_version,
                    "paragraph_refs": {
                        item.paragraph_id: item.content_ref
                        for item in payload.paragraphs
                    },
                    "paragraph_versions": {
                        item.paragraph_id: item.paragraph_version
                        for item in payload.paragraphs
                    },
                    "expected_paragraph_ids": payload.expected_paragraph_ids,
                    "generation_complete": payload.generation_complete,
                    "change_manifest_ref": payload.change_manifest_ref,
                },
                reason_code="writer_result_ready",
            )
        ]

    def _result_status(
        self,
        payload: WriterResultPayloadSchema,
    ) -> AgentInvocationStatus:
        return (
            AgentInvocationStatus.COMPLETED
            if payload.generation_complete
            else AgentInvocationStatus.PARTIAL
        )

    def _emitted_signals(
        self,
        payload: WriterResultPayloadSchema,
    ) -> list[str]:
        return [
            (
                "section_generation_completed"
                if payload.generation_complete
                else "writer_partial"
            )
        ]
