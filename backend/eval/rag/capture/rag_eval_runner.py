# =============================================================================
# 中文阅读说明：离线评测模块，用于执行实验、评分、对比和报告生成。
# 主要定义：_now_iso、_average、read_jsonl、RAGEvalRunner。建议先从公开入口函数开始，再沿调用关系向下阅读。
# =============================================================================
"""RAGAS-compatible lightweight RAG eval runner."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Literal, Mapping, Optional

from data_capture.lifecycle import DataCaptureLifecycleStore, DataCaptureTransitionService
from data_capture.migrations import migrate_data_capture_record_v1
from schemas.data_capture import DataCaptureRecordSchema

from .evaluator import RAGEvaluator
from .report_writer import write_reports
from .schemas import RAGEvalReportSchema, RAGEvalResultSchema


# 阅读注释（函数）：处理 now iso 相关逻辑。
def _now_iso() -> str:
    """处理 now iso 相关逻辑。

    返回:
        str

    阅读提示:
        主要直接调用：isoformat, datetime.now。
    """
    return datetime.now(timezone.utc).isoformat()


# 阅读注释（函数）：处理 average 相关逻辑。
def _average(values: List[float]) -> float:
    """处理 average 相关逻辑。

    参数:
        values: values，具体约束请结合类型标注和调用方确认。

    返回:
        float

    阅读提示:
        主要直接调用：sum, len。
    """
    if not values:
        return 0.0
    return sum(values) / len(values)


# 阅读注释（函数）：读取 jsonl。
def read_jsonl(path: str | Path) -> List[Dict[str, Any]]:
    """读取 jsonl。

    参数:
        path: 目标文件或目录路径。

    返回:
        List[Dict[str, Any]]

    阅读提示:
        主要直接调用：Path, path.exists, FileNotFoundError, splitlines, path.read_text, line.strip, records.append, json.loads。
    """
    path = Path(path)
    if not path.exists():
        raise FileNotFoundError(f"Eval sample file not found: {path}")

    records: List[Dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        records.append(json.loads(line))
    return records


# 阅读注释（类）：封装 rageval runner，集中封装相关状态、依赖和行为。
class RAGEvalRunner:
    """封装 rageval runner，集中封装相关状态、依赖和行为。"""
    # 阅读注释（函数）：初始化 RAGEvalRunner，保存运行所需的依赖、配置或状态。
    def __init__(
        self,
        evaluator: Optional[RAGEvaluator] = None,
        lifecycle: Optional[DataCaptureTransitionService] = None,
        lifecycle_store: Optional[DataCaptureLifecycleStore] = None,
    ) -> None:
        """初始化 RAGEvalRunner，保存运行所需的依赖、配置或状态。

        参数:
            evaluator: evaluator，具体约束请结合类型标注和调用方确认。

        返回:
            None

        阅读提示:
            主要直接调用：RAGEvaluator。
        """
        self.evaluator = evaluator or RAGEvaluator()
        self.lifecycle = lifecycle or DataCaptureTransitionService()
        self.lifecycle_store = lifecycle_store

    @staticmethod
    def _canonical_candidate(record: Dict[str, Any]) -> DataCaptureRecordSchema:
        """Validate v2 directly or route v1 through the explicit migration."""

        if not isinstance(record, dict):
            raise TypeError("DataCapture input record must be a JSON object")
        payload = (
            migrate_data_capture_record_v1(record)
            if record.get("schema_version") == "data_capture_record_v1"
            else record
        )
        return DataCaptureRecordSchema.model_validate(payload, strict=True)

    # 阅读注释（函数）：评估 文件。
    def evaluate_file(
        self,
        eval_samples_path: str | Path,
        report_id: Optional[str] = None,
        run_id: Optional[str] = None,
        policy_decisions: Optional[Mapping[str, Literal["accepted", "rejected"]]] = None,
        readiness_target: Optional[Literal["training_ready", "eval_ready"]] = None,
        lifecycle_store: Optional[DataCaptureLifecycleStore] = None,
    ) -> RAGEvalReportSchema:
        """评估 文件。

        参数:
            eval_samples_path: 评测 samples 路径，具体约束请结合类型标注和调用方确认。
            report_id: report 标识，具体约束请结合类型标注和调用方确认。
            run_id: 本次运行唯一标识。

        返回:
            RAGEvalReportSchema

        阅读提示:
            主要直接调用：Path, read_jsonl, enumerate, results.append, self.evaluator.evaluate, strftime, datetime.now, RAGEvalReportSchema。
        """
        eval_samples_path = Path(eval_samples_path)
        records = read_jsonl(eval_samples_path)

        results: List[RAGEvalResultSchema] = []
        lifecycle_stage_counts: Dict[str, int] = {}
        decisions = dict(policy_decisions or {})
        for idx, record in enumerate(records, start=1):
            capture = self._canonical_candidate(record)
            result = self.evaluator.evaluate(record, sample_index=idx)
            decision = decisions.get(capture.record_id)
            current = lifecycle_store.initialize(capture) if lifecycle_store is not None else capture

            def transition(
                source: DataCaptureRecordSchema,
                target: str,
                *,
                policy_decision: Literal["accepted", "rejected"] | None = None,
                quality_score: float | None = None,
            ) -> DataCaptureRecordSchema:
                if lifecycle_store is not None:
                    if target == "evaluated":
                        reason = "rag_evaluation_completed"
                        authority = "RAGEvalRunner"
                    elif target in {"accepted", "rejected"}:
                        reason = "external_policy_decision"
                        authority = "external_policy_decisions_input"
                    else:
                        reason = "readiness_promotion"
                        authority = "RAGEvalRunner"
                    return lifecycle_store.transition(
                        source.record_id,
                        target,  # type: ignore[arg-type]
                        transition_service=self.lifecycle,
                        reason=reason,
                        authority=authority,
                        policy_decision=policy_decision,
                        quality_score=quality_score,
                    )
                return self.lifecycle.transition(
                    source,
                    target,  # type: ignore[arg-type]
                    policy_decision=policy_decision,
                    quality_score=quality_score,
                )

            if current.capture_stage == "runtime_rejected":
                if decision is not None or readiness_target is not None:
                    raise ValueError(
                        "runtime_rejected capture cannot receive policy promotion: "
                        f"record_id={capture.record_id}"
                    )
                promoted = current
            elif current.capture_stage == "candidate":
                promoted = transition(
                    current,
                    "evaluated",
                    quality_score=result.overall_score,
                )
                if decision is not None:
                    promoted = transition(
                        promoted,
                        decision,
                        policy_decision=decision,
                    )
                    if decision == "accepted" and readiness_target is not None:
                        promoted = transition(promoted, readiness_target)
                elif readiness_target is not None:
                    raise ValueError(
                        "readiness_target requires an external policy_decision for every sample"
                    )
            else:
                raise ValueError(
                    "RAG eval cannot replay a source candidate over canonical current state; "
                    f"record_id={capture.record_id} current_stage={current.capture_stage}"
                )

            lifecycle_stage_counts[promoted.capture_stage] = (
                lifecycle_stage_counts.get(promoted.capture_stage, 0) + 1
            )
            result.extra = {
                **dict(result.extra or {}),
                "capture_lifecycle": {
                    "source_stage": capture.capture_stage,
                    "final_stage": promoted.capture_stage,
                    "policy_decision": decision,
                    "readiness_target": readiness_target,
                    "record": promoted.model_dump(mode="json"),
                },
            }
            results.append(result)

        inferred_run_id = run_id
        if inferred_run_id is None and results:
            inferred_run_id = results[-1].run_id

        report_id = report_id or f"rag_eval_{inferred_run_id or datetime.now().strftime('%Y%m%d_%H%M%S')}"

        return RAGEvalReportSchema(
            report_id=report_id,
            run_id=inferred_run_id,
            source_path=str(eval_samples_path),
            created_at=_now_iso(),
            total=len(results),
            average_overall_score=_average([x.overall_score for x in results]),
            average_context_precision=_average([x.context_precision.score for x in results]),
            average_context_recall_proxy=_average([x.context_recall_proxy.score for x in results]),
            average_faithfulness_proxy=_average([x.faithfulness_proxy.score for x in results]),
            average_answer_relevance_proxy=_average([x.answer_relevance_proxy.score for x in results]),
            average_citation_coverage=_average([x.citation_coverage.score for x in results]),
            average_completeness_proxy=_average([x.completeness_proxy.score for x in results]),
            results=results,
            metadata={
                "runner": "RAGEvalRunner",
                "metric_profile": "ragas_compatible_proxy_v0.1",
                "capture_lifecycle": {
                    "policy_source": (
                        "external_mapping" if policy_decisions is not None else "none"
                    ),
                    "readiness_target": readiness_target,
                    "final_stage_counts": lifecycle_stage_counts,
                },
            },
        )

    # 阅读注释（函数）：评估 and save。
    def evaluate_and_save(
        self,
        eval_samples_path: str | Path,
        output_dir: str | Path,
        report_name: Optional[str] = None,
        report_id: Optional[str] = None,
        run_id: Optional[str] = None,
        policy_decisions: Optional[Mapping[str, Literal["accepted", "rejected"]]] = None,
        readiness_target: Optional[Literal["training_ready", "eval_ready"]] = None,
    ) -> Dict[str, Any]:
        """评估 and save。

        参数:
            eval_samples_path: 评测 samples 路径，具体约束请结合类型标注和调用方确认。
            output_dir: 输出 dir，具体约束请结合类型标注和调用方确认。
            report_name: report 名称，具体约束请结合类型标注和调用方确认。
            report_id: report 标识，具体约束请结合类型标注和调用方确认。
            run_id: 本次运行唯一标识。

        返回:
            Dict[str, Any]

        阅读提示:
            主要直接调用：self.evaluate_file, write_reports, report.model_dump。
        """
        source_path = Path(eval_samples_path).expanduser().resolve()
        if self.lifecycle_store is not None:
            lifecycle_store = self.lifecycle_store
        elif source_path.parent.name == "eval_samples":
            lifecycle_store = DataCaptureLifecycleStore(source_path.parent.parent)
        else:
            lifecycle_store = DataCaptureLifecycleStore(
                Path(output_dir).expanduser().resolve() / "captures"
            )

        report = self.evaluate_file(
            eval_samples_path=eval_samples_path,
            report_id=report_id,
            run_id=run_id,
            policy_decisions=policy_decisions,
            readiness_target=readiness_target,
            lifecycle_store=lifecycle_store,
        )

        report_name = report_name or report.report_id
        output_paths = write_reports(report=report, output_dir=output_dir, report_name=report_name)

        persisted_current_paths: list[str] = []
        for result in report.results:
            lifecycle_payload = dict((result.extra or {}).get("capture_lifecycle") or {})
            record_payload = lifecycle_payload.get("record")
            if not isinstance(record_payload, dict):
                continue
            record = DataCaptureRecordSchema.model_validate(record_payload)
            persisted_current_paths.append(
                str(lifecycle_store.current_dir / f"{record.record_id}.json")
            )

        return {
            "report": report.model_dump(),
            "output_paths": {
                **output_paths,
                "capture_lifecycle_root": str(lifecycle_store.root),
                "capture_lifecycle_current": persisted_current_paths,
            },
        }
