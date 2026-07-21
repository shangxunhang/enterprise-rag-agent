# =============================================================================
# 中文阅读说明：命令行脚本模块，用于启动、验收、调试或离线维护。
# 主要定义：_now_iso、_status_value、_build_span_tree、main。建议先从公开入口函数开始，再沿调用关系向下阅读。
# =============================================================================
"""Step 14 Context Manager v1 acceptance on the complete fake mainline.

The script verifies bounded context packages, evidence lineage, incremental
history use and Trace v2 summaries without loading real GPU models.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from collections import defaultdict
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List

PROJECT_ROOT = Path(__file__).resolve().parents[1]
BACKEND_ROOT = PROJECT_ROOT / "backend"
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))
if str(Path(__file__).resolve().parent) not in sys.path:
    sys.path.insert(0, str(Path(__file__).resolve().parent))

from core.config import get_settings
from observability.trace_reader import load_trace_events, validate_trace_v2
from run_demo import run_demo


# 阅读注释（函数）：处理 now iso 相关逻辑。
def _now_iso() -> str:
    """处理 now iso 相关逻辑。

    返回:
        str

    阅读提示:
        主要直接调用：isoformat, datetime.now。
    """
    return datetime.now(timezone.utc).isoformat()


# 阅读注释（函数）：处理 状态 value 相关逻辑。
def _status_value(value: Any) -> str:
    """处理 状态 value 相关逻辑。

    参数:
        value: value，具体约束请结合类型标注和调用方确认。

    返回:
        str

    阅读提示:
        主要直接调用：hasattr, lower, str, text.startswith, text.split。
    """
    if hasattr(value, "value"):
        value = value.value
    text = str(value or "").lower()
    return text.split(".", 1)[-1] if text.startswith("executionstatus.") else text


# 阅读注释（函数）：构建 span tree。
def _build_span_tree(events: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """构建 span tree。

    参数:
        events: events，具体约束请结合类型标注和调用方确认。

    返回:
        List[Dict[str, Any]]

    阅读提示:
        主要直接调用：row.get, defaultdict, starts.items, append, node, children.get。
    """
    starts = {
        row["span_id"]: row
        for row in events
        if row.get("phase") == "start" and row.get("span_id")
    }
    terminals = {
        row["span_id"]: row
        for row in events
        if row.get("phase") in {"end", "error"} and row.get("span_id")
    }
    children: Dict[str | None, List[str]] = defaultdict(list)
    for span_id, row in starts.items():
        children[row.get("parent_span_id")].append(span_id)

    # 阅读注释（函数）：处理 node 相关逻辑。
    def node(span_id: str) -> Dict[str, Any]:
        """处理 node 相关逻辑。

        参数:
            span_id: span 标识，具体约束请结合类型标注和调用方确认。

        返回:
            Dict[str, Any]

        阅读提示:
            主要直接调用：terminals.get, start.get, terminal.get, node, children.get。
        """
        start = starts[span_id]
        terminal = terminals.get(span_id) or {}
        return {
            "span_id": span_id,
            "parent_span_id": start.get("parent_span_id"),
            "span_name": start.get("span_name"),
            "span_kind": start.get("span_kind"),
            "component_type": start.get("component_type"),
            "component_name": start.get("component_name"),
            "status": terminal.get("status"),
            "latency_ms": terminal.get("latency_ms"),
            "children": [node(child) for child in children.get(span_id, [])],
        }

    return [node(span_id) for span_id in children.get(None, [])]


# 阅读注释（函数）：处理 main 相关逻辑。
def main() -> int:
    """处理 main 相关逻辑。

    返回:
        int

    阅读提示:
        主要直接调用：argparse.ArgumentParser, parser.add_argument, str, parser.parse_args, resolve, expanduser, Path, output_root.mkdir。
    """
    parser = argparse.ArgumentParser(description="Run Step 14 Context Manager v1 acceptance.")
    parser.add_argument(
        "--output-root",
        default=str(PROJECT_ROOT / "data" / "step_14_acceptance"),
    )
    parser.add_argument(
        "--report-path",
        default=str(
            PROJECT_ROOT
            / "data"
            / "processed"
            / "indexes"
            / "step_14_acceptance_report.json"
        ),
    )
    args = parser.parse_args()

    output_root = Path(args.output_root).expanduser().resolve()
    report_path = Path(args.report_path).expanduser().resolve()
    output_root.mkdir(parents=True, exist_ok=True)
    report_path.parent.mkdir(parents=True, exist_ok=True)

    run_id = f"step_14_acceptance_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
    task_id = f"task_{run_id}"

    old_env = {
        key: os.environ.get(key)
        for key in (
            "USE_REAL_RAG_TOOL",
            "ENABLE_AGENT_SELF_RAG",
            "ENABLE_SEMANTIC_GATE",
        )
    }
    try:
        os.environ["USE_REAL_RAG_TOOL"] = "false"
        os.environ["ENABLE_AGENT_SELF_RAG"] = "false"
        os.environ["ENABLE_SEMANTIC_GATE"] = "false"

        base = get_settings(reload=True)
        settings = replace(
            base,
            data_root=output_root,
            default_model_name="fake_llm",
            supervisor_model_name="fake_llm",
            enable_llm_routing=True,
            trace_enabled=True,
            data_capture_enabled=True,
        )
        summary = run_demo(
            user_input="生成一份可追溯的企业级RAG-Agent建设方案。",
            run_id=run_id,
            task_id=task_id,
            output_root=output_root,
            clean_existing=True,
            settings=settings,
            enable_agent_self_rag=False,
            allow_demo_defaults=True,
        )
    finally:
        for key, value in old_env.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value
        get_settings(reload=True)

    trace_path = Path(summary["paths"]["trace"])
    events = load_trace_events(trace_path)
    validation = validate_trace_v2(events)

    checks: List[Dict[str, Any]] = []

    # 阅读注释（函数）：检查 main。
    def check(name: str, condition: bool, details: Dict[str, Any]) -> None:
        """检查 main。

        参数:
            name: 名称，具体约束请结合类型标注和调用方确认。
            condition: condition，具体约束请结合类型标注和调用方确认。
            details: details，具体约束请结合类型标注和调用方确认。

        返回:
            None

        阅读提示:
            主要直接调用：checks.append。
        """
        checks.append(
            {
                "name": name,
                "status": "passed" if condition else "failed",
                "details": details,
            }
        )

    check(
        "mainline_result_unchanged",
        _status_value(summary.get("status")) == "success",
        {"status": summary.get("status")},
    )
    check(
        "trace_file_exists",
        trace_path.is_file(),
        {"trace_path": str(trace_path)},
    )
    check(
        "trace_v2_invariants",
        validation["status"] == "success",
        validation,
    )

    tool_finishes = [row for row in events if row.get("event_type") == "tool_finished"]
    rag_finishes = [
        row
        for row in tool_finishes
        if (row.get("output_summary") or {}).get("rag_evidence")
    ]
    check(
        "rag_evidence_lineage_recorded",
        bool(rag_finishes)
        and all(
            bool((row.get("lineage") or {}).get("evidence_contract_sha256"))
            for row in rag_finishes
        ),
        {
            "rag_tool_finish_count": len(rag_finishes),
            "lineages": [row.get("lineage") for row in rag_finishes],
        },
    )

    model_starts = [row for row in events if row.get("event_type") == "model_started"]
    check(
        "model_payload_is_bounded_summary",
        bool(model_starts)
        and all(
            not row.get("input_payload")
            and bool((row.get("input_summary") or {}).get("prompt_sha256"))
            for row in model_starts
        ),
        {"model_start_count": len(model_starts)},
    )

    run_finished = [row for row in events if row.get("event_type") == "run_finished"]
    check(
        "terminal_status_matches_mainline",
        len(run_finished) == 1
        and _status_value(run_finished[0].get("status"))
        == _status_value(summary.get("status")),
        {
            "mainline_status": summary.get("status"),
            "trace_statuses": [row.get("status") for row in run_finished],
        },
    )

    sections = list((summary.get("scheme_draft") or {}).get("sections") or [])
    packages = [
        ((section.get("input") or {}).get("llm_context_package") or {})
        for section in sections
    ]
    valid_packages = [
        package for package in packages
        if package.get("schema_version") == "llm_context_package_v1"
    ]
    check(
        "every_section_has_context_package",
        bool(sections) and len(valid_packages) == len(sections),
        {
            "section_count": len(sections),
            "valid_package_count": len(valid_packages),
            "package_ids": [item.get("package_id") for item in valid_packages],
        },
    )
    budget_ok = all(
        int((item.get("budget") or {}).get("used_context_chars") or 0)
        <= int((item.get("budget") or {}).get("max_context_chars") or 0)
        and int((item.get("budget") or {}).get("estimated_input_tokens") or 0)
        <= (
            int((item.get("budget") or {}).get("max_input_tokens") or 0)
            - int((item.get("budget") or {}).get("reserved_output_tokens") or 0)
            - int((item.get("budget") or {}).get("safety_margin_tokens") or 0)
        )
        for item in valid_packages
    )
    check(
        "context_budget_is_enforced",
        bool(valid_packages) and budget_ok,
        {"budgets": [item.get("budget") for item in valid_packages]},
    )
    required_ok = all(
        all(
            decision.get("action") == "selected"
            for decision in (item.get("decisions") or [])
            if decision.get("required")
        )
        for item in valid_packages
    )
    check(
        "required_context_items_are_preserved",
        bool(valid_packages) and required_ok,
        {
            "required_decisions": [
                [
                    decision for decision in (item.get("decisions") or [])
                    if decision.get("required")
                ]
                for item in valid_packages
            ]
        },
    )
    history_counts = [
        sum(
            1 for item in (package.get("selected_items") or [])
            if item.get("source_type") == "history"
        )
        for package in valid_packages
    ]
    check(
        "later_sections_receive_bounded_history",
        len(history_counts) >= 2
        and history_counts[0] == 0
        and any(value > 0 for value in history_counts[1:]),
        {"history_item_counts": history_counts},
    )
    lineage_ok = all(
        bool((item.get("lineage") or {}).get("index_version"))
        and bool((item.get("lineage") or {}).get("embedding_model"))
        for item in valid_packages
    )
    check(
        "evidence_lineage_reaches_llm_context",
        bool(valid_packages) and lineage_ok,
        {"lineages": [item.get("lineage") for item in valid_packages]},
    )
    prompts_use_package = all(
        "## 本次模型上下文" in str(section.get("prompt") or "")
        and "## 当前项目结构化输入" not in str(section.get("prompt") or "")
        for section in sections
    )
    check(
        "prompt_consumes_context_package_projection",
        bool(sections) and prompts_use_package,
        {"prompt_count": len(sections)},
    )
    model_contexts = [
        (row.get("input_summary") or {}).get("llm_context") or {}
        for row in model_starts
    ]
    managed_count = sum(1 for item in model_contexts if item.get("managed") is True)
    check(
        "all_model_calls_have_context_package_summary",
        bool(model_contexts) and managed_count == len(model_contexts),
        {
            "model_call_count": len(model_contexts),
            "managed_context_count": managed_count,
        },
    )

    failed_checks = [item for item in checks if item["status"] != "passed"]
    report = {
        "schema_version": "step_14_acceptance_report_v1",
        "status": "success" if not failed_checks else "failed",
        "stage": "step_14_context_manager_v1",
        "created_at": _now_iso(),
        "run_id": run_id,
        "task_id": task_id,
        "trace_path": str(trace_path),
        "mainline_status": summary.get("status"),
        "trace_validation": validation,
        "trace_summary": {
            "schema_version": "run_trace_event_v2",
            "event_count": len(events),
            "span_count": validation.get("span_count"),
            "trace_id": validation.get("trace_id"),
            "event_type_counts": validation.get("event_type_counts"),
            "metrics": validation.get("metrics"),
        },
        "context_summary": {
            "section_count": len(sections),
            "package_count": len(valid_packages),
            "package_ids": [item.get("package_id") for item in valid_packages],
            "history_item_counts": history_counts,
            "managed_model_call_count": managed_count,
        },
        "span_tree": _build_span_tree(events),
        "checks": checks,
        "failed_checks": failed_checks,
    }
    report_path.write_text(
        json.dumps(report, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    print(json.dumps(report, ensure_ascii=False, indent=2))
    print(f"\nAcceptance report: {report_path}")
    return 0 if report["status"] == "success" else 1


if __name__ == "__main__":
    raise SystemExit(main())
