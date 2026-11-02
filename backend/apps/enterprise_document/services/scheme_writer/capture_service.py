"""把一次方案生成运行沉淀为原始交互、评测候选、SFT 候选或拒绝样本。

本服务只组织捕获内容和生命周期分类；幂等写入、尝试级延迟提交与训练可用性晋级由底层
DataCapture 实现负责。成功运行形成 candidate，失败运行只能形成 runtime_rejected。
"""


import logging
from typing import Any, Callable, Dict

from agent.runtime.shared_state_schema import SharedStateSchema
from apps.enterprise_document.schemas.scheme_writer_schema import SchemeWriterInputSchema, SchemeWriterOutputSchema
from schemas.status import ExecutionStatus
from contracts.observability import DataCaptureSink


LOGGER = logging.getLogger(__name__)


class SchemeCaptureService:
    """汇总生成、证据、模型和门禁血缘，并交给统一数据采集端口。"""

    def __init__(
        self,
        *,
        data_capture_recorder: DataCaptureSink | None,
        agent_name: str,
    ) -> None:
        self.data_capture_recorder = data_capture_recorder
        self.agent_name = agent_name
        self._storage_pressure_policy_resolver: Callable[[str], Any] | None = None
        self.last_storage_pressure_policy: Any | None = None

    def attach_storage_pressure_policy(
        self,
        resolver: Callable[[str], Any],
    ) -> None:
        """Attach one Wave-14 optional-capture suppression policy resolver."""

        if not callable(resolver):
            raise TypeError("storage pressure policy resolver must be callable")
        current = self._storage_pressure_policy_resolver
        if current is resolver:
            return
        if current is not None:
            raise RuntimeError("storage pressure policy resolver is already attached")
        self._storage_pressure_policy_resolver = resolver

    def capture(
        self,
        shared_state: SharedStateSchema,
        scheme_input: SchemeWriterInputSchema,
        output: SchemeWriterOutputSchema,
        rag_output: Dict[str, Any],
    ) -> bool:
        """记录一次方案生成的运行快照和候选样本。

        正常存储状态下记录 ``raw_interactions``；只有最终状态成功时才创建 eval/SFT
        candidate。SIF-13 HARD/CRITICAL 本地磁盘压力可在 payload 构造前抑制整次
        optional capture，并显式暴露 observability degradation。失败草稿进入 eval 与
        rejected 的 ``runtime_rejected`` 阶段。Capture 中的 RAG 与 Trace 字段是兼容
        快照，canonical 运行、检索和模型用量仍由各自账本负责。
        """
        if self.data_capture_recorder is None:
            return False
        if self._storage_pressure_policy_resolver is not None:
            policy = self._storage_pressure_policy_resolver(
                f"run:{shared_state.run_id}"
            )
            self.last_storage_pressure_policy = policy
            if bool(getattr(policy, "suppress_optional_capture", False)):
                LOGGER.warning(
                    "optional data capture suppressed by local-storage pressure: "
                    "run_id=%s state=%s observability=%s",
                    shared_state.run_id,
                    getattr(getattr(policy, "state", None), "value", None),
                    getattr(policy, "observability_status", None),
                )
                return False
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
        return True
