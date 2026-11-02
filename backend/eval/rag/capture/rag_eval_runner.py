"""运行基于 DataCapture JSONL 的轻量 RAG 评测并推进样本生命周期。

Runner 不仅计算指标，还负责把候选样本从 ``candidate`` 推进到 ``evaluated``，
并在外部策略明确给出 accepted/rejected 后继续推进到就绪状态。运行时拒绝样本不可
被离线评测重新晋级，防止质量流程绕过线上硬门禁。
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Literal, Mapping, Optional

from data_capture.lifecycle import (
    DataCaptureLifecycleStore,
    DataCaptureTransitionService,
)
from data_capture.migrations import migrate_data_capture_record_v1
from schemas.data_capture import DataCaptureRecordSchema

from .evaluator import RAGEvaluator
from .report_writer import write_reports
from .schemas import RAGEvalReportSchema, RAGEvalResultSchema


# 外部策略只允许给出明确接受或拒绝，不允许在评测层隐式推断政策结论。
PolicyDecision = Literal["accepted", "rejected"]
ReadinessTarget = Literal["training_ready", "eval_ready"]


def _now_iso() -> str:
    """返回带时区的当前 UTC ISO 时间戳。"""

    return datetime.now(timezone.utc).isoformat()


def _average(values: List[float]) -> float:
    """计算浮点列表的算术平均值，空列表返回零。"""

    # 报告需要在无样本时仍保持完整结构，因此不抛出除零异常。
    if not values:
        return 0.0
    return sum(values) / len(values)


def read_jsonl(path: str | Path) -> List[Dict[str, Any]]:
    """读取评测样本 JSONL 文件。

    Args:
        path: 每个非空行应为一个 JSON 对象的文件路径。

    Returns:
        按文件顺序解析的样本字典列表。

    Raises:
        FileNotFoundError: 目标文件不存在时抛出。
        json.JSONDecodeError: 任一非空行不是合法 JSON 时抛出。
    """

    # 第一步：标准化路径并拒绝不存在的输入，避免生成空报告掩盖路径错误。
    source_path = Path(path)
    if not source_path.exists():
        raise FileNotFoundError(f"Eval sample file not found: {source_path}")

    # 第二步：逐行解析并忽略空白行，保持样本原始顺序。
    records: List[Dict[str, Any]] = []
    for line in source_path.read_text(encoding="utf-8").splitlines():
        normalized = line.strip()
        if not normalized:
            continue
        records.append(json.loads(normalized))
    return records


class RAGEvalRunner:
    """评测采集样本、推进生命周期并生成批次报告。

    Attributes:
        evaluator: 将单条样本转换为 ``RAGEvalResultSchema`` 的评估器。
        lifecycle: 执行纯内存状态迁移和迁移规则校验的服务。
        lifecycle_store: 可选持久化生命周期仓库；存在时所有迁移写入 current/history。
    """

    def __init__(
        self,
        evaluator: Optional[RAGEvaluator] = None,
        lifecycle: Optional[DataCaptureTransitionService] = None,
        lifecycle_store: Optional[DataCaptureLifecycleStore] = None,
    ) -> None:
        """注入评估器和数据生命周期组件。

        未提供依赖时创建默认实现；构造阶段不读取文件，也不创建生命周期记录。
        """

        self.evaluator = evaluator or RAGEvaluator()
        self.lifecycle = lifecycle or DataCaptureTransitionService()
        self.lifecycle_store = lifecycle_store

    @staticmethod
    def _canonical_candidate(record: Dict[str, Any]) -> DataCaptureRecordSchema:
        """把 v1/v2 输入转换为严格的当前 DataCapture 协议。

        Args:
            record: JSONL 中解析出的原始对象。

        Returns:
            通过严格 Pydantic 校验的 v2 记录。

        Raises:
            TypeError: 输入不是字典时抛出。
            pydantic.ValidationError: 迁移后记录不符合当前协议时抛出。
        """

        # 第一步：确保 JSONL 行是对象，而不是数组、字符串或标量。
        if not isinstance(record, dict):
            raise TypeError("DataCapture input record must be a JSON object")

        # 第二步：只有显式 v1 记录走迁移器；v2 及未知版本直接交由严格校验拒绝。
        payload = (
            migrate_data_capture_record_v1(record)
            if record.get("schema_version") == "data_capture_record_v1"
            else record
        )

        # 第三步：以 strict=True 校验，避免宽松类型转换掩盖脏数据。
        return DataCaptureRecordSchema.model_validate(payload, strict=True)

    def evaluate_file(
        self,
        eval_samples_path: str | Path,
        report_id: Optional[str] = None,
        run_id: Optional[str] = None,
        policy_decisions: Optional[Mapping[str, PolicyDecision]] = None,
        readiness_target: Optional[ReadinessTarget] = None,
        lifecycle_store: Optional[DataCaptureLifecycleStore] = None,
    ) -> RAGEvalReportSchema:
        """评测一个 JSONL 文件并按政策推进每条记录的生命周期。

        Args:
            eval_samples_path: 待评测 DataCapture JSONL 文件。
            report_id: 可选固定报告标识。
            run_id: 可选报告级运行标识；省略时从最后一条结果推断。
            policy_decisions: 以 ``record_id`` 为键的外部接受/拒绝结论。
            readiness_target: 接受后要晋级的就绪状态；必须配合外部政策结论使用。
            lifecycle_store: 本次调用使用的持久化仓库；省略时只在内存中迁移。

        Returns:
            包含逐样本结果、宏平均和生命周期统计的报告。
        """

        # 第一步：读取全部原始记录，并准备结果和最终阶段计数器。
        source_path = Path(eval_samples_path)
        records = read_jsonl(source_path)
        results: List[RAGEvalResultSchema] = []
        lifecycle_stage_counts: Dict[str, int] = {}
        decisions = dict(policy_decisions or {})

        # 第二步：按文件顺序校验、评估并推进每条候选记录。
        for sample_index, record in enumerate(records, start=1):
            capture = self._canonical_candidate(record)
            result = self.evaluator.evaluate(record, sample_index=sample_index)
            decision = decisions.get(capture.record_id)

            # 持久化模式先初始化 current 快照；内存模式直接使用输入记录。
            current = (
                lifecycle_store.initialize(capture)
                if lifecycle_store is not None
                else capture
            )

            def transition(
                source: DataCaptureRecordSchema,
                target: str,
                *,
                policy_decision: PolicyDecision | None = None,
                quality_score: float | None = None,
            ) -> DataCaptureRecordSchema:
                """使用统一原因和权限信息执行一次生命周期迁移。"""

                # 持久化仓库负责并发版本、current 快照和历史事件写入。
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

                # 无仓库时仍复用同一迁移服务校验状态机，只是不产生持久化副作用。
                return self.lifecycle.transition(
                    source,
                    target,  # type: ignore[arg-type]
                    policy_decision=policy_decision,
                    quality_score=quality_score,
                )

            # 第三步：运行时拒绝是终止状态，离线流程不得重新接受或晋级。
            if current.capture_stage == "runtime_rejected":
                if decision is not None or readiness_target is not None:
                    raise ValueError(
                        "runtime_rejected capture cannot receive policy promotion: "
                        f"record_id={capture.record_id}"
                    )
                promoted = current

            # 第四步：正常候选先进入 evaluated，并写入本次总分作为质量事实。
            elif current.capture_stage == "candidate":
                promoted = transition(
                    current,
                    "evaluated",
                    quality_score=result.overall_score,
                )

                # 只有外部映射明确给出政策结论，评测器才推进 accepted/rejected。
                if decision is not None:
                    promoted = transition(
                        promoted,
                        decision,
                        policy_decision=decision,
                    )

                    # 只有 accepted 样本可以继续晋级 training_ready/eval_ready。
                    if decision == "accepted" and readiness_target is not None:
                        promoted = transition(promoted, readiness_target)
                elif readiness_target is not None:
                    raise ValueError(
                        "readiness_target requires an external policy_decision "
                        "for every sample"
                    )

            # 第五步：拒绝用旧候选重放覆盖仓库中已经推进的当前状态。
            else:
                raise ValueError(
                    "RAG eval cannot replay a source candidate over canonical "
                    "current state; "
                    f"record_id={capture.record_id} "
                    f"current_stage={current.capture_stage}"
                )

            # 第六步：累计最终阶段，并把完整生命周期结果附加到逐样本诊断中。
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

        # 第七步：未显式提供 run_id 时沿用最后一条样本的运行身份。
        inferred_run_id = run_id
        if inferred_run_id is None and results:
            inferred_run_id = results[-1].run_id

        # 第八步：未指定 report_id 时优先使用 run_id，否则使用本地时间作为后缀。
        resolved_report_id = report_id or (
            f"rag_eval_{inferred_run_id or datetime.now().strftime('%Y%m%d_%H%M%S')}"
        )

        # 第九步：分别对六项指标和总分做宏平均，并附加生命周期政策来源。
        return RAGEvalReportSchema(
            report_id=resolved_report_id,
            run_id=inferred_run_id,
            source_path=str(source_path),
            created_at=_now_iso(),
            total=len(results),
            average_overall_score=_average(
                [item.overall_score for item in results]
            ),
            average_context_precision=_average(
                [item.context_precision.score for item in results]
            ),
            average_context_recall_proxy=_average(
                [item.context_recall_proxy.score for item in results]
            ),
            average_faithfulness_proxy=_average(
                [item.faithfulness_proxy.score for item in results]
            ),
            average_answer_relevance_proxy=_average(
                [item.answer_relevance_proxy.score for item in results]
            ),
            average_citation_coverage=_average(
                [item.citation_coverage.score for item in results]
            ),
            average_completeness_proxy=_average(
                [item.completeness_proxy.score for item in results]
            ),
            results=results,
            metadata={
                "runner": "RAGEvalRunner",
                "metric_profile": "ragas_compatible_proxy_v0.1",
                "capture_lifecycle": {
                    "policy_source": (
                        "external_mapping"
                        if policy_decisions is not None
                        else "none"
                    ),
                    "readiness_target": readiness_target,
                    "final_stage_counts": lifecycle_stage_counts,
                },
            },
        )

    def evaluate_and_save(
        self,
        eval_samples_path: str | Path,
        output_dir: str | Path,
        report_name: Optional[str] = None,
        report_id: Optional[str] = None,
        run_id: Optional[str] = None,
        policy_decisions: Optional[Mapping[str, PolicyDecision]] = None,
        readiness_target: Optional[ReadinessTarget] = None,
    ) -> Dict[str, Any]:
        """评测样本、持久化生命周期并写出 JSON/Markdown 报告。

        Args:
            eval_samples_path: 待评测 JSONL 文件。
            output_dir: 报告输出目录；非标准来源路径时也用于推导生命周期目录。
            report_name: 可选报告文件名前缀。
            report_id: 可选报告协议标识。
            run_id: 可选报告级运行标识。
            policy_decisions: 外部接受/拒绝映射。
            readiness_target: 接受后可选晋级目标。

        Returns:
            包含完整报告和所有报告/生命周期产物路径的字典。
        """

        # 第一步：解析来源绝对路径，并选择唯一生命周期仓库。
        source_path = Path(eval_samples_path).expanduser().resolve()
        if self.lifecycle_store is not None:
            lifecycle_store = self.lifecycle_store
        elif source_path.parent.name == "eval_samples":
            # 标准目录布局下，captures 根目录就是 eval_samples 的父目录。
            lifecycle_store = DataCaptureLifecycleStore(source_path.parent.parent)
        else:
            # 非标准输入使用 output_dir/captures，避免污染任意来源目录。
            lifecycle_store = DataCaptureLifecycleStore(
                Path(output_dir).expanduser().resolve() / "captures"
            )

        # 第二步：在持久化生命周期仓库支持下完成评测和状态迁移。
        report = self.evaluate_file(
            eval_samples_path=source_path,
            report_id=report_id,
            run_id=run_id,
            policy_decisions=policy_decisions,
            readiness_target=readiness_target,
            lifecycle_store=lifecycle_store,
        )

        # 第三步：使用显式名称或报告 ID 写出 JSON 和 Markdown。
        resolved_report_name = report_name or report.report_id
        output_paths = write_reports(
            report=report,
            output_dir=output_dir,
            report_name=resolved_report_name,
        )

        # 第四步：从逐样本结果恢复最终记录身份，列出 current 快照路径。
        persisted_current_paths: List[str] = []
        for result in report.results:
            lifecycle_payload = dict(
                (result.extra or {}).get("capture_lifecycle") or {}
            )
            record_payload = lifecycle_payload.get("record")
            if not isinstance(record_payload, dict):
                continue

            record = DataCaptureRecordSchema.model_validate(record_payload)
            persisted_current_paths.append(
                str(lifecycle_store.current_dir / f"{record.record_id}.json")
            )

        # 第五步：返回可直接序列化的报告和全部产物位置。
        return {
            "report": report.model_dump(),
            "output_paths": {
                **output_paths,
                "capture_lifecycle_root": str(lifecycle_store.root),
                "capture_lifecycle_current": persisted_current_paths,
            },
        }
