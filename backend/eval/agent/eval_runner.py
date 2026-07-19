# =============================================================================
# 中文阅读说明：离线评测模块，用于执行实验、评分、对比和报告生成。
# 主要定义：EvalRunner、build_default_eval_samples。建议先从公开入口函数开始，再沿调用关系向下阅读。
# =============================================================================
"""Evaluation runner for the corrected section-oriented workflow."""

from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from eval.agent.hard_gate import extract_runtime_hard_failures
from schemas.eval_schema import (
    EvalExpectedSchema,
    EvalMetricsSchema,
    EvalResultSchema,
    EvalSampleSchema,
)


# 阅读注释（类）：封装 评测 runner，集中封装相关状态、依赖和行为。
class EvalRunner:
    """封装 评测 runner，集中封装相关状态、依赖和行为。"""
    # 阅读注释（函数）：初始化 EvalRunner，保存运行所需的依赖、配置或状态。
    def __init__(self, output_dir: str | Path = "data/eval_outputs", min_output_chars: int = 100) -> None:
        """初始化 EvalRunner，保存运行所需的依赖、配置或状态。

        参数:
            output_dir: 输出 dir，具体约束请结合类型标注和调用方确认。
            min_output_chars: min 输出 chars，具体约束请结合类型标注和调用方确认。

        返回:
            None

        阅读提示:
            主要直接调用：Path, self.output_dir.mkdir。
        """
        self.output_dir = Path(output_dir)
        self.output_dir.mkdir(parents=True, exist_ok=True)
        self.min_output_chars = min_output_chars

    # 阅读注释（函数）：处理 now iso 相关逻辑。
    @staticmethod
    def _now_iso() -> str:
        """处理 now iso 相关逻辑。

        返回:
            str

        阅读提示:
            主要直接调用：isoformat, datetime.now。
        """
        return datetime.now(timezone.utc).isoformat()

    # 阅读注释（函数）：处理 new 标识 相关逻辑。
    @staticmethod
    def _new_id(prefix: str) -> str:
        """处理 new 标识 相关逻辑。

        参数:
            prefix: prefix，具体约束请结合类型标注和调用方确认。

        返回:
            str

        阅读提示:
            主要直接调用：uuid.uuid4。
        """
        return f"{prefix}_{uuid.uuid4().hex[:12]}"

    # 阅读注释（函数）：处理 文件 exists 相关逻辑。
    @staticmethod
    def _file_exists(path: str | Path | None) -> bool:
        """处理 文件 exists 相关逻辑。

        参数:
            path: 目标文件或目录路径。

        返回:
            bool

        阅读提示:
            主要直接调用：bool, exists, Path。
        """
        return bool(path and Path(path).exists())

    # 阅读注释（函数）：读取 jsonl。
    @staticmethod
    def _read_jsonl(path: str | Path | None) -> List[Dict[str, Any]]:
        """读取 jsonl。

        参数:
            path: 目标文件或目录路径。

        返回:
            List[Dict[str, Any]]

        阅读提示:
            主要直接调用：exists, Path, json.loads, splitlines, read_text, line.strip。
        """
        if not path or not Path(path).exists():
            return []
        return [
            json.loads(line)
            for line in Path(path).read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]

    # 阅读注释（函数）：评估 summary。
    def evaluate_summary(self, sample: EvalSampleSchema, run_summary: Dict[str, Any]) -> EvalResultSchema:
        """评估 summary。

        参数:
            sample: sample，具体约束请结合类型标注和调用方确认。
            run_summary: run summary，具体约束请结合类型标注和调用方确认。

        返回:
            EvalResultSchema

        阅读提示:
            主要直接调用：run_summary.get, draft.get, item.get, all, sum, len, bool, self._file_exists。
        """
        draft = run_summary.get("scheme_draft") or {}
        writer_output = run_summary.get("scheme_writer_output") or {}
        content = draft.get("full_text") or draft.get("content") or ""
        sections = draft.get("sections") or []
        paths = run_summary.get("paths") or {}

        required_sections = sample.expected.required_sections
        section_titles = {item.get("section_title") for item in sections}
        has_required_sections = all(item in section_titles for item in required_sections)

        keywords = sample.expected.expected_keywords
        hit_count = sum(1 for keyword in keywords if keyword in content)
        keyword_hit_rate = hit_count / len(keywords) if keywords else 1.0

        citation_bindings = draft.get("citation_bindings") or []
        has_citations = bool(citation_bindings)
        citation_requirement_met = (
            not sample.expected.expected_citation_required or has_citations
        )

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

        hard_failures = extract_runtime_hard_failures(run_summary)
        hard_gate_passed = not hard_failures and bool(
            (writer_output.get("hard_gate") or {}).get("passed", True)
        )

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

        # Hard failures are vetoes. A high soft score can never override them.
        success = (
            not hard_failures
            and hard_gate_passed
            and checks["pipeline_success"]
            and has_required_sections
            and citation_requirement_met
        )

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

    # 阅读注释（函数）：评估 samples。
    def evaluate_samples(
        self,
        samples: List[EvalSampleSchema],
        run_func,
        run_id_prefix: str = "run_eval",
        output_root: str | Path = "data",
        clean_existing: bool = True,
    ) -> Dict[str, Any]:
        """评估 samples。

        参数:
            samples: samples，具体约束请结合类型标注和调用方确认。
            run_func: run func，具体约束请结合类型标注和调用方确认。
            run_id_prefix: run 标识 prefix，具体约束请结合类型标注和调用方确认。
            output_root: 输出 root，具体约束请结合类型标注和调用方确认。
            clean_existing: clean existing，具体约束请结合类型标注和调用方确认。

        返回:
            Dict[str, Any]

        阅读提示:
            主要直接调用：enumerate, run_func, results.append, self.evaluate_summary, self.build_report。
        """
        results: List[EvalResultSchema] = []
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
            results.append(self.evaluate_summary(sample, run_summary))
        return self.build_report(results)

    # 阅读注释（函数）：构建 report。
    def build_report(self, results: List[EvalResultSchema]) -> Dict[str, Any]:
        """构建 report。

        参数:
            results: 待处理的结果集合。

        返回:
            Dict[str, Any]

        阅读提示:
            主要直接调用：len, sum, item.metrics.extra.get, self._new_id, self._now_iso, item.model_dump。
        """
        total = len(results)
        success_count = sum(1 for item in results if item.metrics.success)
        hard_failure_count = sum(
            1
            for item in results
            if (item.metrics.extra.get("hard_failures") or [])
        )
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
                    sum(item.score or 0.0 for item in results) / total if total else 0.0
                ),
                "required_sections_rate": (
                    sum(1 for item in results if item.metrics.has_required_sections) / total
                    if total else 0.0
                ),
                "keyword_hit_rate": (
                    sum(item.metrics.context_keyword_hit or 0.0 for item in results) / total
                    if total else 0.0
                ),
            },
            "results": [item.model_dump() for item in results],
        }

    # 阅读注释（函数）：保存 report。
    def save_report(self, report: Dict[str, Any], report_name: Optional[str] = None) -> Dict[str, str]:
        """保存 report。

        参数:
            report: report，具体约束请结合类型标注和调用方确认。
            report_name: report 名称，具体约束请结合类型标注和调用方确认。

        返回:
            Dict[str, str]

        阅读提示:
            主要直接调用：strftime, datetime.now, json_path.write_text, json.dumps, jsonl_path.open, handle.write, str。
        """
        report_name = report_name or f"eval_report_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
        json_path = self.output_dir / f"{report_name}.json"
        jsonl_path = self.output_dir / f"{report_name}.jsonl"
        json_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        with jsonl_path.open("w", encoding="utf-8") as handle:
            for result in report["results"]:
                handle.write(json.dumps(result, ensure_ascii=False) + "\n")
        return {"json": str(json_path), "jsonl": str(jsonl_path)}


# 阅读注释（函数）：构建 default 评测 samples。
def build_default_eval_samples(created_at: Optional[str] = None) -> List[EvalSampleSchema]:
    """构建 default 评测 samples。

    参数:
        created_at: created at，具体约束请结合类型标注和调用方确认。

    返回:
        List[EvalSampleSchema]

    阅读提示:
        主要直接调用：isoformat, datetime.now, EvalSampleSchema, EvalExpectedSchema。
    """
    created_at = created_at or datetime.now(timezone.utc).isoformat()
    sections = ["项目概述", "建设目标", "技术方案", "实施与验收"]
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
