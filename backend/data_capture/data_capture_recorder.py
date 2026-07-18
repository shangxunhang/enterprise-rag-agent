"""Data capture recorder."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, List, Optional

from core.runtime.clock import Clock, SystemClock
from core.runtime.ids import IdGenerator, UuidIdGenerator
from schemas.data_capture import DataCaptureRecordSchema


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

    def __init__(
        self,
        output_dir: str | Path = "data/captures",
        *,
        clock: Clock | None = None,
        id_generator: IdGenerator | None = None,
    ) -> None:
        self.output_dir = Path(output_dir)
        self.clock = clock or SystemClock()
        self.id_generator = id_generator or UuidIdGenerator()
        self.output_dir.mkdir(parents=True, exist_ok=True)

        for capture_type in self.VALID_CAPTURE_TYPES:
            (self.output_dir / capture_type).mkdir(parents=True, exist_ok=True)

    def _now_iso(self) -> str:
        return self.clock.now_iso()

    def _new_id(self, prefix: str) -> str:
        return self.id_generator.new_id(prefix)

    def _capture_path(self, capture_type: str, run_id: str) -> Path:
        if capture_type not in self.VALID_CAPTURE_TYPES:
            raise ValueError(f"Unsupported capture_type: {capture_type}")

        return self.output_dir / capture_type / f"{run_id}_{capture_type}.jsonl"

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
