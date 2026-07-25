# =============================================================================
# 中文阅读说明：企业文档生成业务模块，负责方案规划、检索、章节生成、引用和验收。
# 主要定义：SchemeCaptureService。建议先从公开入口函数开始，再沿调用关系向下阅读。
# =============================================================================
"""Generated from the stable v7.5.1 SchemeWriter behavior."""


from typing import Any, Dict

from agent.runtime.shared_state_schema import SharedStateSchema
from apps.enterprise_document.schemas.scheme_writer_schema import SchemeWriterInputSchema, SchemeWriterOutputSchema
from schemas.status import ExecutionStatus
from contracts.observability import DataCaptureSink


# 阅读注释（类）：封装 scheme capture 服务，封装一组可复用的业务能力。
class SchemeCaptureService:
    def __init__(
        self,
        *,
        data_capture_recorder: DataCaptureSink | None,
        agent_name: str,
    ) -> None:
        self.data_capture_recorder = data_capture_recorder
        self.agent_name = agent_name
    """封装 scheme capture 服务，封装一组可复用的业务能力。"""
    # 阅读注释（函数）：记录并沉淀 SchemeCaptureService。
    def capture(
        self,
        shared_state: SharedStateSchema,
        scheme_input: SchemeWriterInputSchema,
        output: SchemeWriterOutputSchema,
        rag_output: Dict[str, Any],
    ) -> None:
        """记录并沉淀 SchemeCaptureService。

        参数:
            shared_state: shared 状态，具体约束请结合类型标注和调用方确认。
            scheme_input: scheme 输入，具体约束请结合类型标注和调用方确认。
            output: 输出，具体约束请结合类型标注和调用方确认。
            rag_output: RAG 输出，具体约束请结合类型标注和调用方确认。

        返回:
            None

        阅读提示:
            主要直接调用：draft.model_dump, dict, output.rag_context.model_dump, item.model_dump, rag_output.get, scheme_input.model_dump, join, output.hard_gate.model_dump。
        """
        if self.data_capture_recorder is None:
            return
        draft = output.generated_draft
        deliverable = output.deliverable_final_result
        payload = draft.model_dump() if draft else {}
        trace_id = f"trace_{shared_state.run_id}"
        model_call_ids = list(shared_state.generated_outputs.keys())
        retrieval_trace_ids = list(
            dict.fromkeys(
                tool_call_id
                for section in (draft.sections if draft else [])
                for tool_call_id in list((section.extra or {}).get("evidence_tool_call_ids") or [])
            )
        )
        common = dict(
            task_id=shared_state.task_id,
            run_id=shared_state.run_id,
            trace_id=trace_id,
            user_input=shared_state.user_input,
            rag_context=(output.rag_context.model_dump() if output.rag_context else {}),
            retrieved_chunks=[item.model_dump() for item in output.retrieved_chunks],
            citations=[item.model_dump() for item in output.citations],
            rag_trace=rag_output.get("trace") or {},
            structured_facts={"scheme_writer_input": scheme_input.model_dump()},
            prompt="\n\n".join(section.prompt for section in (draft.sections if draft else [])),
            model_output="\n\n".join(section.model_output for section in (draft.sections if draft else [])),
            generated_draft_output=(draft.full_text if draft else ""),
            deliverable_output=(
                deliverable.draft.full_text if deliverable is not None else None
            ),
            eval_sample={
                "required_sections": scheme_input.required_sections,
                "hard_gate": output.hard_gate.model_dump() if output.hard_gate else {},
                "sections": [section.model_dump() for section in (draft.sections if draft else [])],
            },
            source_component_type="agent",
            source_component_name=self.agent_name,
            source_event_type="agent_finished",
            quality_score=None,
            need_human_review=True,
            lineage_refs={
                "trace_id": trace_id,
                "model_call_ids": model_call_ids,
                "retrieval_trace_ids": retrieval_trace_ids,
                "canonical_sources": {
                    "execution": "run_trace",
                    "retrieval": "rag_run",
                    "model_usage": "model_usage_ledger",
                    "sample": "data_capture",
                },
            },
            metadata={
                "output_schema": output.schema_version,
                "status": output.status.value,
                "quality_decision": "pending_evaluation",
                "artifact_lifecycle": {
                    "generated_draft": draft is not None,
                    "rejected_artifact": output.rejected_artifact is not None,
                    "deliverable_final_result": deliverable is not None,
                },
                "compatibility_snapshots": [
                    "rag_context",
                    "retrieved_chunks",
                    "citations",
                    "rag_trace",
                ],
            },
        )
        self.data_capture_recorder.record(
            capture_type="raw_interactions",
            **{**common, "capture_stage": "raw"},
        )
        if output.status == ExecutionStatus.SUCCESS:
            self.data_capture_recorder.record(
                capture_type="eval_samples",
                **{**common, "capture_stage": "candidate"},
            )
            self.data_capture_recorder.record(
                capture_type="sft_candidates",
                **{**common, "capture_stage": "candidate"},
            )
        else:
            rejected_common = {**common, "capture_stage": "runtime_rejected"}
            self.data_capture_recorder.record(
                capture_type="eval_samples",
                **rejected_common,
            )
            self.data_capture_recorder.record(
                capture_type="rejected",
                **rejected_common,
            )
