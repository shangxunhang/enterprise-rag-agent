# -*- coding: utf-8 -*-
# =============================================================================
# 中文阅读说明：RAG 核心模块，负责查询变换、召回、融合、重排、证据评估和上下文组装。
# 主要定义：RagRunCapture。建议先从公开入口函数开始，再沿调用关系向下阅读。
# =============================================================================
"""
rag_template/data_capture/rag_run_capture.py
===========================================

P4-lite RAG run capture.

It writes a full single-query RAG trace into JSONL for later:
- debugging
- eval replay
- SFT candidate construction
- DPO / preference candidate construction
- reranker dataset construction

职责边界：
- 只保存运行轨迹，不筛选训练样本。
- 后续 DatasetBuilder 再从 runs 中构造 SFT/DPO/RAG eval 数据。
"""

from __future__ import annotations

from copy import deepcopy
from pathlib import Path
from typing import Any, Dict

from core.persistence.crash_safe_jsonl import canonical_json_sha256
from core.runtime.execution_control import current_execution_control
from rag.data_capture.jsonl_writer import JsonlWriter


_TOP_LEVEL_RUNTIME_DIAGNOSTIC_FIELDS = frozenset(
    {
        "attempt_id",
        "created_at",
        "execution_id",
        "finished_at",
        "latency_ms",
        "started_at",
        "timestamp",
        "timestamp_epoch",
    }
)

_MODEL_CALL_RUNTIME_DIAGNOSTIC_FIELDS = frozenset(
    {
        "attempt_id",
        "created_at",
        "execution_id",
        "finished_at",
        "latency_ms",
        "model_call_id",
        "provider_attempts",
        "started_at",
        "timestamp",
        "timestamp_epoch",
    }
)


def _drop_fields(container: Any, fields: frozenset[str]) -> None:
    if not isinstance(container, dict):
        return
    for field in fields:
        container.pop(field, None)


def _project_model_call_envelopes(model_calls: Any) -> None:
    if not isinstance(model_calls, list):
        return
    for model_call in model_calls:
        _drop_fields(model_call, _MODEL_CALL_RUNTIME_DIAGNOSTIC_FIELDS)


def _project_query_expansion_runtime(query_expansion: Any) -> None:
    """Remove timings only from the concrete QueryExpansionResult envelope."""

    if not isinstance(query_expansion, dict):
        return
    metadata = query_expansion.get("metadata")
    if not isinstance(metadata, dict):
        return
    for runtime_component in ("rag_fusion", "hyde"):
        component = metadata.get(runtime_component)
        if isinstance(component, dict):
            component.pop("latency_ms", None)


def _project_evidence_assessment_runtime(evidence_quality: Any) -> None:
    """Remove timings only from concrete CRAG assessment report paths."""

    if not isinstance(evidence_quality, dict):
        return
    evidence_quality.pop("latency_ms", None)
    for assessment_name in ("initial_assessment", "final_assessment"):
        assessment = evidence_quality.get(assessment_name)
        if not isinstance(assessment, dict):
            continue
        report = assessment.get("report")
        if isinstance(report, dict):
            report.pop("latency_ms", None)


def rag_run_semantic_projection(record: dict[str, Any]) -> dict[str, Any]:
    """Project RAG business semantics using explicit runtime paths.

    Retrieval, evidence, citation, context, source-lineage, and caller metadata
    subtrees are deliberately preserved without recursive field-name filtering.
    """

    projection = deepcopy(record)
    projection.pop("attempt", None)
    _drop_fields(projection, _TOP_LEVEL_RUNTIME_DIAGNOSTIC_FIELDS)

    _project_model_call_envelopes(projection.get("model_calls"))
    _project_query_expansion_runtime(projection.get("query_expansion"))
    _project_evidence_assessment_runtime(projection.get("evidence_quality"))

    metadata = projection.get("metadata")
    if isinstance(metadata, dict):
        metadata.pop("model_call_ids", None)
        metadata.pop("query_expansion_model_call_ids", None)
        _project_model_call_envelopes(metadata.get("model_calls"))
        _project_query_expansion_runtime(metadata.get("query_expansion"))
        _project_evidence_assessment_runtime(metadata.get("evidence_quality"))

    return projection


# 阅读注释（类）：封装 RAG run capture，集中封装相关状态、依赖和行为。
class RagRunCapture:
    """Capture one RAG pipeline run as one JSONL record."""

    # 阅读注释（函数）：初始化 RagRunCapture，保存运行所需的依赖、配置或状态。
    def __init__(self, output_path: str | Path = "data/runs/rag_runs.jsonl"):
        """初始化 RagRunCapture，保存运行所需的依赖、配置或状态。

        参数:
            output_path: 输出 路径，具体约束请结合类型标注和调用方确认。

        返回:
            未显式标注；请结合调用方和实际返回语句理解。

        阅读提示:
            主要直接调用：Path, JsonlWriter。
        """
        self.output_path = Path(output_path)
        self.writer = JsonlWriter(self.output_path)

    # 阅读注释（函数）：记录并沉淀 RagRunCapture。
    def capture(self, record: Dict[str, Any]) -> Dict[str, Any]:
        """记录并沉淀 RagRunCapture。

        参数:
            record: 记录，具体约束请结合类型标注和调用方确认。

        返回:
            Dict[str, Any]

        阅读提示:
            主要直接调用：self.writer.write, str, record.get。
        """
        execution_control = current_execution_control()
        if execution_control is None:
            capture_record = {
                **record,
                "capture_semantics": "immutable_raw_attempt_telemetry_v1",
                "training_eligible": False,
                "eval_eligible": False,
                "parent_workflow_outcome": "not_joined",
            }
            path = self.writer.write(capture_record)
            return {
                "saved": True,
                "deferred": False,
                "output_path": str(path),
                "run_id": record.get("run_id"),
            }

        side_effect_id = execution_control.reserve_side_effect_id("rag_run_capture")
        capture_record = {
            **record,
            "capture_id": side_effect_id,
            "capture_semantics": "attempt_scoped_telemetry_v1",
            "attempt": {
                "execution_id": execution_control.execution_id,
                "commit_scope_id": execution_control.commit_scope_id,
                "outcome": "succeeded",
                "cancelled": False,
                "timeout": False,
            },
            # A successful retrieval attempt is not training/eval truth. A
            # downstream builder must join the terminal parent workflow result.
            "training_eligible": False,
            "eval_eligible": False,
            "parent_workflow_outcome": "pending_join",
        }
        semantic_fingerprint = canonical_json_sha256(
            rag_run_semantic_projection(capture_record)
        )

        def append_record() -> None:
            self.writer.write_once(
                capture_record,
                id_field="capture_id",
                semantic_projection=rag_run_semantic_projection,
                semantic_fingerprint=semantic_fingerprint,
            )

        execution_control.defer_external_commit(
            append_record,
            description=f"rag_run_capture:{record.get('run_id')}",
            requires_success=True,
            side_effect_id=side_effect_id,
            semantic_fingerprint=semantic_fingerprint,
        )
        return {
            "saved": False,
            "deferred": True,
            "pending_commit": True,
            "output_path": str(self.output_path),
            "run_id": record.get("run_id"),
            "capture_id": side_effect_id,
        }

    # 阅读注释（函数）：以可调用对象形式执行 RagRunCapture 的核心逻辑。
    def __call__(self, record: Dict[str, Any]) -> Dict[str, Any]:
        """以可调用对象形式执行 RagRunCapture 的核心逻辑。

        参数:
            record: 记录，具体约束请结合类型标注和调用方确认。

        返回:
            Dict[str, Any]

        阅读提示:
            主要直接调用：self.capture。
        """
        return self.capture(record)
