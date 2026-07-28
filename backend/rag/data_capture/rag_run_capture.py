# -*- coding: utf-8 -*-
"""把一次完整 RAG 查询运行保存为可重放 JSONL 记录。

采集记录可用于调试、评测重放、SFT/DPO 候选构造和 Reranker 数据集构造，但运行时
成功并不自动等于训练或评测真值。本模块只保存运行轨迹；后续 Builder 必须与父工作流
最终结果关联后，才能决定样本是否可用。

在受控执行上下文中，写入会延迟到当前尝试成功后提交，并通过业务语义投影实现幂等；
没有执行上下文时则立即追加为不可训练的原始遥测记录。
"""

from __future__ import annotations

from copy import deepcopy
from pathlib import Path
from typing import Any, Dict

from core.persistence.crash_safe_jsonl import canonical_json_sha256
from core.runtime.execution_control import current_execution_control
from rag.data_capture.jsonl_writer import JsonlWriter


# 顶层运行诊断字段不改变 RAG 业务事实，因此从幂等语义投影中排除。
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

# 模型调用 envelope 中的调用身份、尝试列表和计时字段也属于运行诊断。
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
    """从字典中原地删除指定字段；非字典输入直接忽略。"""

    if not isinstance(container, dict):
        return
    for field in fields:
        container.pop(field, None)


def _project_model_call_envelopes(model_calls: Any) -> None:
    """从模型调用列表中移除运行级诊断字段。"""

    if not isinstance(model_calls, list):
        return
    for model_call in model_calls:
        _drop_fields(
            model_call,
            _MODEL_CALL_RUNTIME_DIAGNOSTIC_FIELDS,
        )


def _project_query_expansion_runtime(query_expansion: Any) -> None:
    """只移除具体 QueryExpansionResult 中 RAG-Fusion/HyDE 的延迟。"""

    # 第一步：确认查询扩展和其 metadata 都是对象。
    if not isinstance(query_expansion, dict):
        return
    metadata = query_expansion.get("metadata")
    if not isinstance(metadata, dict):
        return

    # 第二步：保留扩展查询、模型输出和策略，只删除不稳定的耗时字段。
    for runtime_component in ("rag_fusion", "hyde"):
        component = metadata.get(runtime_component)
        if isinstance(component, dict):
            component.pop("latency_ms", None)


def _project_evidence_assessment_runtime(evidence_quality: Any) -> None:
    """只移除 CRAG 证据评估 envelope 中的延迟字段。"""

    if not isinstance(evidence_quality, dict):
        return

    # 顶层评估耗时不影响证据结论。
    evidence_quality.pop("latency_ms", None)

    # 初始和最终评估报告中的耗时同样排除，评分、原因和证据细节全部保留。
    for assessment_name in (
        "initial_assessment",
        "final_assessment",
    ):
        assessment = evidence_quality.get(assessment_name)
        if not isinstance(assessment, dict):
            continue
        report = assessment.get("report")
        if isinstance(report, dict):
            report.pop("latency_ms", None)


def rag_run_semantic_projection(
    record: dict[str, Any],
) -> dict[str, Any]:
    """提取用于幂等判断的 RAG 业务语义投影。

    Args:
        record: 完整运行采集记录。

    Returns:
        深拷贝后的投影，移除尝试、计时和模型调用身份等不稳定诊断字段。

    检索结果、证据、引用、上下文、来源血缘和调用方 metadata 都被完整保留。这里不做
    递归“按字段名删除”，避免误删业务对象中恰好同名的合法字段。
    """

    # 第一步：深拷贝，投影过程不能修改真正待落盘的原始遥测记录。
    projection = deepcopy(record)

    # 第二步：移除执行尝试 envelope 和顶层诊断字段。
    projection.pop("attempt", None)
    _drop_fields(
        projection,
        _TOP_LEVEL_RUNTIME_DIAGNOSTIC_FIELDS,
    )

    # 第三步：清理顶层模型调用、查询扩展和证据评估中的运行时噪声。
    _project_model_call_envelopes(projection.get("model_calls"))
    _project_query_expansion_runtime(
        projection.get("query_expansion")
    )
    _project_evidence_assessment_runtime(
        projection.get("evidence_quality")
    )

    # 第四步：同样处理 metadata 内的兼容投影，但保留其他业务 metadata。
    metadata = projection.get("metadata")
    if isinstance(metadata, dict):
        metadata.pop("model_call_ids", None)
        metadata.pop("query_expansion_model_call_ids", None)
        _project_model_call_envelopes(
            metadata.get("model_calls")
        )
        _project_query_expansion_runtime(
            metadata.get("query_expansion")
        )
        _project_evidence_assessment_runtime(
            metadata.get("evidence_quality")
        )

    return projection


class RagRunCapture:
    """把每次 RAG Pipeline 运行追加到一个 JSONL 文件。"""

    def __init__(
        self,
        output_path: str | Path = "data/runs/rag_runs.jsonl",
    ) -> None:
        """初始化目标路径和崩溃安全写入器。"""

        self.output_path = Path(output_path)
        self.writer = JsonlWriter(self.output_path)

    def capture(self, record: Dict[str, Any]) -> Dict[str, Any]:
        """立即或延迟保存一条 RAG 运行记录。

        Args:
            record: 已组装的完整运行字典，通常包含 run_id、检索、证据、上下文和模型输出。

        Returns:
            写入状态字典。立即模式返回 ``saved=True``；受控执行模式返回
            ``deferred=True`` 和稳定 ``capture_id``。
        """

        # 第一步：读取当前尝试级执行控制；普通脚本环境通常没有该上下文。
        execution_control = current_execution_control()

        # 第二步：无执行控制时立即保存不可晋级的原始遥测。
        if execution_control is None:
            capture_record = {
                **record,
                "capture_semantics": (
                    "immutable_raw_attempt_telemetry_v1"
                ),
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

        # 第三步：受控执行中为本副作用分配尝试范围内的稳定身份。
        side_effect_id = execution_control.reserve_side_effect_id(
            "rag_run_capture"
        )

        # 第四步：构造尝试级遥测。即使检索成功，训练/评测资格仍保持 False。
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
            "training_eligible": False,
            "eval_eligible": False,
            "parent_workflow_outcome": "pending_join",
        }

        # 第五步：对排除运行噪声后的业务投影计算稳定指纹。
        semantic_fingerprint = canonical_json_sha256(
            rag_run_semantic_projection(capture_record)
        )

        # 第六步：定义成功提交时执行的幂等写入闭包。
        def append_record() -> None:
            self.writer.write_once(
                capture_record,
                id_field="capture_id",
                semantic_projection=rag_run_semantic_projection,
                semantic_fingerprint=semantic_fingerprint,
            )

        # 第七步：把外部副作用绑定到当前尝试成功；失败、取消或超时不会提交。
        execution_control.defer_external_commit(
            append_record,
            description=(
                f"rag_run_capture:{record.get('run_id')}"
            ),
            requires_success=True,
            side_effect_id=side_effect_id,
            semantic_fingerprint=semantic_fingerprint,
        )

        # 第八步：返回待提交信息，调用方无需假设文件已经可见。
        return {
            "saved": False,
            "deferred": True,
            "pending_commit": True,
            "output_path": str(self.output_path),
            "run_id": record.get("run_id"),
            "capture_id": side_effect_id,
        }

    def __call__(
        self,
        record: Dict[str, Any],
    ) -> Dict[str, Any]:
        """允许实例作为可调用采集函数使用。"""

        return self.capture(record)
