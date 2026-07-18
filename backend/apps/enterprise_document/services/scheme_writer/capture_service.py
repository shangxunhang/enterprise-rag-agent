"""Generated from the stable v7.5.1 SchemeWriter behavior."""


from typing import Any, Dict

from agent.runtime.shared_state_schema import SharedStateSchema
from apps.enterprise_document.schemas.scheme_writer_schema import SchemeWriterInputSchema, SchemeWriterOutputSchema
from schemas.status import ExecutionStatus
from .base import RuntimeBoundService


class SchemeCaptureService(RuntimeBoundService):
    def _capture(
        self,
        shared_state: SharedStateSchema,
        scheme_input: SchemeWriterInputSchema,
        output: SchemeWriterOutputSchema,
        rag_output: Dict[str, Any],
    ) -> None:
        if self.data_capture_recorder is None:
            return
        draft = output.scheme_draft
        payload = draft.model_dump() if draft else {}
        common = dict(
            task_id=shared_state.task_id,
            run_id=shared_state.run_id,
            trace_id=f"trace_{shared_state.run_id}",
            user_input=shared_state.user_input,
            rag_context=(output.rag_context.model_dump() if output.rag_context else {}),
            retrieved_chunks=[item.model_dump() for item in output.retrieved_chunks],
            citations=[item.model_dump() for item in output.citations],
            rag_trace=rag_output.get("trace") or {},
            structured_facts={"scheme_writer_input": scheme_input.model_dump()},
            prompt="\n\n".join(section.prompt for section in (draft.sections if draft else [])),
            model_output="\n\n".join(section.model_output for section in (draft.sections if draft else [])),
            final_output=(draft.full_text if draft else ""),
            eval_sample={
                "required_sections": scheme_input.required_sections,
                "hard_gate": output.hard_gate.model_dump() if output.hard_gate else {},
                "sections": [section.model_dump() for section in (draft.sections if draft else [])],
            },
            source_component_type="agent",
            source_component_name=self.agent_name,
            source_event_type="agent_finished",
            quality_score=1.0 if output.status == ExecutionStatus.SUCCESS else 0.0,
            need_human_review=True,
            is_usable_for_training=output.status == ExecutionStatus.SUCCESS,
            is_usable_for_eval=True,
            metadata={
                "output_schema": output.schema_version,
                "status": output.status.value,
            },
        )
        self.data_capture_recorder.record(capture_type="raw_interactions", **common)
        self.data_capture_recorder.record(capture_type="eval_samples", **common)
        if output.status == ExecutionStatus.SUCCESS:
            self.data_capture_recorder.record(capture_type="sft_candidates", **common)
        else:
            self.data_capture_recorder.record(capture_type="rejected", **common)
