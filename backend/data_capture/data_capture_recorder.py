# =============================================================================
# 中文阅读说明：运行数据沉淀模块，用于记录后训练与评测所需样本。
# 主要定义：JsonlDataCaptureRecorder。建议先从公开入口函数开始，再沿调用关系向下阅读。
# =============================================================================
"""Data capture recorder."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, List, Optional

from core.runtime.clock import Clock, SystemClock
from core.runtime.ids import IdGenerator, UuidIdGenerator
from schemas.data_capture import DataCaptureRecordSchema


# 阅读注释（类）：封装 jsonl 数据 capture recorder，集中封装相关状态、依赖和行为。
class JsonlDataCaptureRecorder:
    """Record categorized captured data into JSONL files.

    Output example:

    data/captures/sft_candidates/run_001_sft_candidates.jsonl
    data/captures/eval_samples/run_001_eval_samples.jsonl
    """

    VALID_CAPTURE_TYPES = {
        "raw_interactions",
        "sft_candidates",
        "dpo_candidates",
        "eval_samples",
        "human_reviews",
        "rejected",
    }

    # 阅读注释（函数）：初始化 JsonlDataCaptureRecorder，保存运行所需的依赖、配置或状态。
    def __init__(
        self,
        output_dir: str | Path = "data/captures",
        *,
        clock: Clock | None = None,
        id_generator: IdGenerator | None = None,
    ) -> None:
        """初始化 JsonlDataCaptureRecorder，保存运行所需的依赖、配置或状态。

        参数:
            output_dir: 输出 dir，具体约束请结合类型标注和调用方确认。
            clock: clock，具体约束请结合类型标注和调用方确认。
            id_generator: 标识 generator，具体约束请结合类型标注和调用方确认。

        返回:
            None

        阅读提示:
            主要直接调用：Path, SystemClock, UuidIdGenerator, self.output_dir.mkdir, mkdir。
        """
        self.output_dir = Path(output_dir)
        self.clock = clock or SystemClock()
        self.id_generator = id_generator or UuidIdGenerator()
        self.output_dir.mkdir(parents=True, exist_ok=True)

        for capture_type in self.VALID_CAPTURE_TYPES:
            (self.output_dir / capture_type).mkdir(parents=True, exist_ok=True)

    # 阅读注释（函数）：处理 now iso 相关逻辑。
    def _now_iso(self) -> str:
        """处理 now iso 相关逻辑。

        返回:
            str

        阅读提示:
            主要直接调用：self.clock.now_iso。
        """
        return self.clock.now_iso()

    # 阅读注释（函数）：处理 new 标识 相关逻辑。
    def _new_id(self, prefix: str) -> str:
        """处理 new 标识 相关逻辑。

        参数:
            prefix: prefix，具体约束请结合类型标注和调用方确认。

        返回:
            str

        阅读提示:
            主要直接调用：self.id_generator.new_id。
        """
        return self.id_generator.new_id(prefix)

    # 阅读注释（函数）：记录并沉淀 路径。
    def _capture_path(self, capture_type: str, run_id: str) -> Path:
        """记录并沉淀 路径。

        参数:
            capture_type: capture 类型，具体约束请结合类型标注和调用方确认。
            run_id: 本次运行唯一标识。

        返回:
            Path

        阅读提示:
            主要直接调用：ValueError。
        """
        if capture_type not in self.VALID_CAPTURE_TYPES:
            raise ValueError(f"Unsupported capture_type: {capture_type}")

        return self.output_dir / capture_type / f"{run_id}_{capture_type}.jsonl"

    # 阅读注释（函数）：记录 JsonlDataCaptureRecorder。
    def record(
            self,
            capture_type: str,
            task_id: str,
            run_id: str,
            user_input: Optional[str] = None,
            rewritten_query: Optional[str] = None,
            rag_context: Optional[Dict[str, Any]] = None,
            structured_facts: Optional[Dict[str, Any]] = None,
            prompt: Optional[str] = None,
            model_output: Optional[str] = None,
            final_output: Optional[str] = None,
            human_feedback: Optional[Dict[str, Any]] = None,
            label: Optional[Dict[str, Any]] = None,
            quality_score: Optional[float] = None,
            quality_flags: Optional[List[str]] = None,
            need_human_review: bool = True,
            is_usable_for_training: bool = False,
            is_usable_for_eval: bool = False,
            trace_id: Optional[str] = None,
            source_component_type: Optional[str] = None,
            source_component_name: Optional[str] = None,
            source_event_type: Optional[str] = None,
            related_trace_event_ids: Optional[List[str]] = None,
            related_file_ids: Optional[List[str]] = None,
            metadata: Optional[Dict[str, Any]] = None,
            extra: Optional[Dict[str, Any]] = None,
            retrieved_chunks: Optional[List[Dict[str, Any]]] = None,
            citations: Optional[List[Dict[str, Any]]] = None,
            rag_trace: Optional[Dict[str, Any]] = None,
            prompt_info: Optional[Dict[str, Any]] = None,
            model_info: Optional[Dict[str, Any]] = None,
            eval_sample: Optional[Dict[str, Any]] = None,
    ) -> DataCaptureRecordSchema:
        """记录 JsonlDataCaptureRecorder。

        参数:
            capture_type: capture 类型，具体约束请结合类型标注和调用方确认。
            task_id: 任务唯一标识。
            run_id: 本次运行唯一标识。
            user_input: user 输入，具体约束请结合类型标注和调用方确认。
            rewritten_query: rewritten 查询，具体约束请结合类型标注和调用方确认。
            rag_context: RAG 上下文，具体约束请结合类型标注和调用方确认。
            structured_facts: structured facts，具体约束请结合类型标注和调用方确认。
            prompt: 提示词，具体约束请结合类型标注和调用方确认。
            model_output: 模型 输出，具体约束请结合类型标注和调用方确认。
            final_output: final 输出，具体约束请结合类型标注和调用方确认。
            human_feedback: human 反馈，具体约束请结合类型标注和调用方确认。
            label: label，具体约束请结合类型标注和调用方确认。
            quality_score: 质量 score，具体约束请结合类型标注和调用方确认。
            quality_flags: 质量 flags，具体约束请结合类型标注和调用方确认。
            need_human_review: need human review，具体约束请结合类型标注和调用方确认。
            is_usable_for_training: is usable for training，具体约束请结合类型标注和调用方确认。
            is_usable_for_eval: is usable for 评测，具体约束请结合类型标注和调用方确认。
            trace_id: Trace 标识，具体约束请结合类型标注和调用方确认。
            source_component_type: source component 类型，具体约束请结合类型标注和调用方确认。
            source_component_name: source component 名称，具体约束请结合类型标注和调用方确认。
            source_event_type: source 事件 类型，具体约束请结合类型标注和调用方确认。
            related_trace_event_ids: related Trace 事件 标识集合，具体约束请结合类型标注和调用方确认。
            related_file_ids: related 文件 标识集合，具体约束请结合类型标注和调用方确认。
            metadata: 随对象传递的元数据。
            extra: extra，具体约束请结合类型标注和调用方确认。
            retrieved_chunks: retrieved chunks，具体约束请结合类型标注和调用方确认。
            citations: 引用信息集合。
            rag_trace: RAG Trace，具体约束请结合类型标注和调用方确认。
            prompt_info: 提示词 info，具体约束请结合类型标注和调用方确认。
            model_info: 模型 info，具体约束请结合类型标注和调用方确认。
            eval_sample: 评测 sample，具体约束请结合类型标注和调用方确认。

        返回:
            DataCaptureRecordSchema

        阅读提示:
            主要直接调用：DataCaptureRecordSchema, self._new_id, self._now_iso, self._capture_path, path.open, f.write, json.dumps, record.model_dump。
        """
        record = DataCaptureRecordSchema(
            record_id=self._new_id("capture"),
            capture_type=capture_type,
            task_id=task_id,
            run_id=run_id,
            trace_id=trace_id,
            source_component_type=source_component_type,
            source_component_name=source_component_name,
            source_event_type=source_event_type,
            user_input=user_input,
            rewritten_query=rewritten_query,
            rag_context=rag_context or {},
            structured_facts=structured_facts or {},
            prompt=prompt,
            model_output=model_output,
            final_output=final_output,
            human_feedback=human_feedback or {},
            label=label or {},
            quality_score=quality_score,
            quality_flags=quality_flags or [],
            need_human_review=need_human_review,
            is_usable_for_training=is_usable_for_training,
            is_usable_for_eval=is_usable_for_eval,
            related_trace_event_ids=related_trace_event_ids or [],
            related_file_ids=related_file_ids or [],
            created_at=self._now_iso(),
            metadata=metadata or {},
            extra=extra or {},
            retrieved_chunks=retrieved_chunks or [],
            citations=citations or [],
            rag_trace=rag_trace or {},
            prompt_info=prompt_info or {},
            model_info=model_info or {},
            eval_sample=eval_sample or {},
        )

        path = self._capture_path(capture_type, run_id)

        with path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(record.model_dump(), ensure_ascii=False) + "\n")

        return record
