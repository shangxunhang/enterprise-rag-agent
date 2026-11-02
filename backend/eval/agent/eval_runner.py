"""执行面向分章节建设方案工作流的端到端离线评测。

本模块负责三件事：调用真实运行入口生成一次任务产物、把运行摘要转换为结构化
评测结果、将多个结果聚合并写成 JSON/JSONL 报告。软评分用于比较整体完成度，
运行时硬失败和文档硬门禁具有否决权，不能被较高的平均分掩盖。
"""

from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

from eval.agent.hard_gate import extract_runtime_hard_failures
from schemas.eval_schema import (
    EvalExpectedSchema,
    EvalMetricsSchema,
    EvalResultSchema,
    EvalSampleSchema,
)


class EvalRunner:
    """运行建设方案样本并检查业务结果、可观测性和数据采集产物。

    Attributes:
        output_dir: 最终评测报告的输出目录。
        min_output_chars: 草稿正文通过最低长度检查所需的字符数。

    该类不负责构建生产运行时。调用方通过 ``run_func`` 注入真实任务入口，使评测
    与应用装配解耦，同时仍然验证实际主链生成的文件和结构化摘要。
    """

    def __init__(
        self,
        output_dir: str | Path = "data/eval_outputs",
        min_output_chars: int = 100,
    ) -> None:
        """初始化报告目录和最低正文长度阈值。

        Args:
            output_dir: 保存聚合报告的目录；不存在时立即创建。
            min_output_chars: 正文长度软检查阈值，不直接替代业务硬门禁。
        """

        # 第一步：标准化输出目录并确保后续写报告时父目录存在。
        self.output_dir = Path(output_dir)
        self.output_dir.mkdir(parents=True, exist_ok=True)

        # 第二步：保存可配置的软检查阈值，供每个样本统一使用。
        self.min_output_chars = min_output_chars

    @staticmethod
    def _now_iso() -> str:
        """返回带时区的 UTC ISO 时间戳。"""

        # 评测结果使用 UTC，避免不同时区执行产生不可比较的时间语义。
        return datetime.now(timezone.utc).isoformat()

    @staticmethod
    def _new_id(prefix: str) -> str:
        """生成带业务前缀的短随机标识。"""

        # 前缀用于区分报告和结果，随机后缀避免同一时刻并发执行重名。
        return f"{prefix}_{uuid.uuid4().hex[:12]}"

    @staticmethod
    def _file_exists(path: str | Path | None) -> bool:
        """判断可选路径是否指向一个已存在的文件系统对象。"""

        # 空路径直接视为不存在，避免 Path(None) 产生无关异常。
        return bool(path and Path(path).exists())

    @staticmethod
    def _read_jsonl(path: str | Path | None) -> List[Dict[str, Any]]:
        """读取 JSONL 文件中的非空对象行。

        Args:
            path: 可选 JSONL 路径。路径为空或不存在时返回空列表。

        Returns:
            按文件顺序解析的字典列表。

        Raises:
            json.JSONDecodeError: 任一非空行不是合法 JSON 时抛出。损坏的 Trace
                属于评测基础设施错误，不应静默跳过。
        """

        # 第一步：缺失文件返回空集合，由上层的 exists/complete 检查决定是否失败。
        if not path or not Path(path).exists():
            return []

        # 第二步：逐行解析并忽略纯空白行，保持 JSONL 的事件顺序。
        return [
            json.loads(line)
            for line in Path(path).read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]

    def evaluate_summary(
        self,
        sample: EvalSampleSchema,
        run_summary: Dict[str, Any],
    ) -> EvalResultSchema:
        """把一次真实运行摘要转换为结构化评测结果。

        Args:
            sample: 黄金样本及其章节、关键词和引用要求。
            run_summary: 主链运行入口返回的摘要，包含草稿、Writer 输出和产物路径。

        Returns:
            同时包含硬成功判定、软评分、细分检查和诊断信息的结果对象。
        """

        # 第一步：读取生成草稿和 Writer 输出；缺失字段按空结构处理以形成可诊断失败。
        draft = run_summary.get("generated_draft") or {}
        writer_output = run_summary.get("scheme_writer_output") or {}
        content = draft.get("full_text") or draft.get("content") or ""
        sections = draft.get("sections") or []
        paths = run_summary.get("paths") or {}

        # 第二步：用章节标题集合检查黄金样本要求的全部章节是否存在。
        required_sections = sample.expected.required_sections
        section_titles = {item.get("section_title") for item in sections}
        has_required_sections = all(
            section_title in section_titles
            for section_title in required_sections
        )

        # 第三步：计算预期关键词的字面覆盖率；无关键词约束时该项记为满分。
        keywords = sample.expected.expected_keywords
        hit_count = sum(1 for keyword in keywords if keyword in content)
        keyword_hit_rate = hit_count / len(keywords) if keywords else 1.0

        # 第四步：引用验收以已绑定到具体声明位置的 citation_bindings 为准。
        citation_bindings = draft.get("citation_bindings") or []
        has_citations = bool(citation_bindings)
        citation_requirement_met = (
            not sample.expected.expected_citation_required or has_citations
        )

        # 第五步：读取 Trace 并检查主链关键生命周期事件是否完整出现。
        trace_exists = self._file_exists(paths.get("trace"))
        trace_events = self._read_jsonl(paths.get("trace")) if trace_exists else []
        event_types = [item.get("event_type") for item in trace_events]
        required_trace_events = {
            "workflow_started",
            "agent_started",
            "agent_finished",
            "tool_started",
            "tool_finished",
            "model_started",
            "model_finished",
            "workflow_finished",
        }
        trace_complete = required_trace_events.issubset(set(event_types))

        # 第六步：复用运行时硬门禁提取器，保证离线评测与线上交付标准一致。
        hard_failures = extract_runtime_hard_failures(run_summary)
        hard_gate_passed = not hard_failures and bool(
            (writer_output.get("hard_gate") or {}).get("passed", True)
        )

        # 第七步：构造可单独诊断的软检查项，包括业务产物和可观测/采集基础设施。
        checks = {
            "pipeline_success": run_summary.get("status") == "success",
            "hard_gate_passed": hard_gate_passed,
            "has_required_sections": has_required_sections,
            "citation_requirement_met": citation_requirement_met,
            "output_length_ok": len(content) >= self.min_output_chars,
            "trace_exists": trace_exists,
            "trace_complete": trace_complete,
            "raw_capture_exists": self._file_exists(paths.get("raw_interactions")),
            "eval_capture_exists": self._file_exists(paths.get("eval_samples")),
        }
        passed_count = sum(checks.values())
        score = passed_count / len(checks)

        # 第八步：计算严格成功。硬失败、硬门禁、主链状态、章节或引用任一不满足都失败。
        success = (
            not hard_failures
            and hard_gate_passed
            and checks["pipeline_success"]
            and has_required_sections
            and citation_requirement_met
        )

        # 第九步：保存稳定指标字段，并将扩展检查和计数放入 extra 供报告诊断。
        metrics = EvalMetricsSchema(
            success=success,
            has_required_sections=has_required_sections,
            has_citations=has_citations,
            context_keyword_hit=keyword_hit_rate,
            extra={
                "checks": checks,
                "hard_failures": hard_failures,
                "passed_count": passed_count,
                "total_count": len(checks),
                "output_chars": len(content),
                "section_count": len(sections),
                "citation_binding_count": len(citation_bindings),
                "trace_event_count": len(trace_events),
            },
        )

        # 第十步：返回逐样本结果；正文只保留预览，完整内容仍由运行产物路径承载。
        return EvalResultSchema(
            eval_result_id=self._new_id("eval_result"),
            sample_id=sample.sample_id,
            task_id=run_summary.get("task_id", ""),
            run_id=run_summary.get("run_id", ""),
            task_type=sample.task_type,
            metrics=metrics,
            score=score,
            created_at=self._now_iso(),
            extra={
                "user_input": sample.user_input,
                "scheme_title": draft.get("title"),
                "scheme_preview": content[:500],
                "paths": paths,
                "hard_failures": hard_failures,
            },
        )

    def evaluate_samples(
        self,
        samples: List[EvalSampleSchema],
        run_func: Callable[..., Dict[str, Any]],
        run_id_prefix: str = "run_eval",
        output_root: str | Path = "data",
        clean_existing: bool = True,
    ) -> Dict[str, Any]:
        """依次运行全部样本并构建聚合报告。

        Args:
            samples: 待运行的结构化评测样本。
            run_func: 真实应用入口。必须接受本方法传入的关键字参数并返回运行摘要。
            run_id_prefix: 为本批次生成 run_id 时使用的前缀。
            output_root: 运行产物根目录，不是最终报告目录。
            clean_existing: 是否要求运行入口清理相同 run_id 的旧产物。

        Returns:
            ``build_report`` 生成的聚合报告字典。
        """

        results: List[EvalResultSchema] = []

        # 第一步：为每个样本生成确定性可读的 task/run 标识并调用真实主链。
        for index, sample in enumerate(samples, start=1):
            run_id = f"{run_id_prefix}_{sample.sample_id}_{index}"
            task_id = f"task_{run_id}"
            run_summary = run_func(
                user_input=sample.user_input,
                project_input=sample.project_input or None,
                run_id=run_id,
                task_id=task_id,
                output_root=output_root,
                clean_existing=clean_existing,
            )

            # 第二步：每次运行结束后立即评测，防止后续样本覆盖其内存摘要。
            results.append(self.evaluate_summary(sample, run_summary))

        # 第三步：所有样本完成后统一聚合，确保分母等于实际结果数。
        return self.build_report(results)

    def build_report(self, results: List[EvalResultSchema]) -> Dict[str, Any]:
        """把逐样本结果聚合为批次级报告。

        Args:
            results: 已完成评测的样本结果列表。

        Returns:
            包含批次标识、汇总指标和完整逐样本结果的字典。
        """

        # 第一步：统计严格成功和包含硬失败的样本数。
        total = len(results)
        success_count = sum(1 for item in results if item.metrics.success)
        hard_failure_count = sum(
            1
            for item in results
            if (item.metrics.extra.get("hard_failures") or [])
        )

        # 第二步：对可比较的软指标取宏平均；空结果集统一返回 0。
        return {
            "schema_version": "eval_report_v2",
            "eval_report_id": self._new_id("eval_report"),
            "created_at": self._now_iso(),
            "summary": {
                "total": total,
                "success_count": success_count,
                "success_rate": success_count / total if total else 0.0,
                "hard_failure_count": hard_failure_count,
                "average_score": (
                    sum(item.score or 0.0 for item in results) / total
                    if total
                    else 0.0
                ),
                "required_sections_rate": (
                    sum(
                        1
                        for item in results
                        if item.metrics.has_required_sections
                    )
                    / total
                    if total
                    else 0.0
                ),
                "keyword_hit_rate": (
                    sum(
                        item.metrics.context_keyword_hit or 0.0
                        for item in results
                    )
                    / total
                    if total
                    else 0.0
                ),
            },
            "results": [item.model_dump() for item in results],
        }

    def save_report(
        self,
        report: Dict[str, Any],
        report_name: Optional[str] = None,
    ) -> Dict[str, str]:
        """将同一报告写为便于阅读的 JSON 和便于流式处理的 JSONL。

        Args:
            report: ``build_report`` 返回的完整报告。
            report_name: 可选文件名前缀；省略时使用本地时间生成唯一名称。

        Returns:
            ``json`` 和 ``jsonl`` 两种产物的字符串路径。
        """

        # 第一步：确定共享文件名前缀，并推导两种格式的目标路径。
        report_name = report_name or (
            f"eval_report_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
        )
        json_path = self.output_dir / f"{report_name}.json"
        jsonl_path = self.output_dir / f"{report_name}.jsonl"

        # 第二步：完整 JSON 保存批次摘要和全部结果，便于人工审阅。
        json_path.write_text(
            json.dumps(report, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

        # 第三步：JSONL 每行写一个样本结果，便于后续数据分析和增量消费。
        with jsonl_path.open("w", encoding="utf-8") as handle:
            for result in report["results"]:
                handle.write(json.dumps(result, ensure_ascii=False) + "\n")

        # 第四步：返回实际路径，供 CLI 或上层报告索引展示。
        return {"json": str(json_path), "jsonl": str(jsonl_path)}


def build_default_eval_samples(
    created_at: Optional[str] = None,
) -> List[EvalSampleSchema]:
    """构建一条覆盖硬门禁、章节和引用要求的默认回归样本。

    Args:
        created_at: 可选固定时间戳，便于测试得到稳定数据；省略时使用当前 UTC。

    Returns:
        可直接传给 ``EvalRunner.evaluate_samples`` 的样本列表。
    """

    # 第一步：固定时间和标准章节集合，保证输入与黄金预期使用同一份章节定义。
    created_at = created_at or datetime.now(timezone.utc).isoformat()
    sections = ["项目概述", "建设目标", "技术方案", "实施与验收"]

    # 第二步：构造符合生产 ProjectInput 边界的最小建设方案输入。
    base_input = {
        "tenant_id": "eval_tenant",
        "project_name": "评测项目",
        "task_type": "scheme_generation",
        "user_query": "根据项目输入和知识库证据生成建设方案。",
        "source_materials": [],
        "generation_requirements": {
            "required_sections": sections,
            "need_citation": True,
            "min_section_chars": 80,
            "max_section_retries": 1,
        },
        "output_schema": {
            "document_title": "评测项目建设方案",
            "required_sections": sections,
        },
        "business_goal": "生成可追溯的建设方案文字草稿。",
    }

    # 第三步：把运行输入与黄金验收条件封装为严格 EvalSampleSchema。
    return [
        EvalSampleSchema(
            sample_id="scheme_generation_hard_gate_001",
            task_type="scheme_generation",
            user_input=base_input["user_query"],
            project_input=base_input,
            expected=EvalExpectedSchema(
                required_sections=sections,
                expected_keywords=["项目输入", "知识检索"],
                expected_citation_required=True,
            ),
            eval_set_id="scheme_generation_eval_v2",
            eval_set_version="v2.0",
            created_at=created_at,
        )
    ]
