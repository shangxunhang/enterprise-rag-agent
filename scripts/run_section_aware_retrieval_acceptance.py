# =============================================================================
# 中文阅读说明：命令行脚本模块，用于启动、验收、调试或离线维护。
# 主要定义：_now_iso、_status、main。建议先从公开入口函数开始，再沿调用关系向下阅读。
# =============================================================================
"""Accept section-aware evidence retrieval and document-wide citation registry."""

from __future__ import annotations

import argparse
import json
import os
import sys
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
from mainline_runtime import build_project_input
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


# 阅读注释（函数）：处理 状态 相关逻辑。
def _status(value: Any) -> str:
    """处理 状态 相关逻辑。

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


# 阅读注释（函数）：处理 main 相关逻辑。
def main() -> int:
    """处理 main 相关逻辑。

    返回:
        int

    阅读提示:
        主要直接调用：argparse.ArgumentParser, parser.add_argument, str, parser.parse_args, resolve, expanduser, Path, output_root.mkdir。
    """
    parser = argparse.ArgumentParser(
        description="Run section-aware evidence retrieval acceptance."
    )
    parser.add_argument(
        "--output-root",
        default=str(PROJECT_ROOT / "data" / "section_aware_retrieval_acceptance"),
    )
    parser.add_argument(
        "--report-path",
        default=str(
            PROJECT_ROOT
            / "data"
            / "processed"
            / "indexes"
            / "section_aware_retrieval_acceptance_report.json"
        ),
    )
    args = parser.parse_args()

    output_root = Path(args.output_root).expanduser().resolve()
    report_path = Path(args.report_path).expanduser().resolve()
    output_root.mkdir(parents=True, exist_ok=True)
    report_path.parent.mkdir(parents=True, exist_ok=True)

    run_id = f"section_aware_acceptance_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
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
            run_trace_dir=output_root / "runs",
            data_capture_dir=output_root / "captures",
            eval_output_dir=output_root / "eval_outputs",
            task_state_dir=output_root / "tasks",
            default_model_name="fake_llm",
            supervisor_model_name="fake_llm",
            enable_llm_routing=False,
            trace_enabled=True,
            data_capture_enabled=True,
        )
        project_input = build_project_input(
            task_id,
            "生成一个政务云建设方案",
            allow_demo_defaults=True,
        ).model_dump()
        project_input["generation_requirements"]["extra"].update(
            {
                "enable_section_aware_retrieval": True,
                "enable_corrective_section_retrieval": False,
            }
        )
        summary = run_demo(
            user_input="生成一个政务云建设方案",
            run_id=run_id,
            task_id=task_id,
            output_root=output_root,
            clean_existing=True,
            settings=settings,
            enable_agent_self_rag=False,
            project_input=project_input,
            allow_demo_defaults=False,
        )
    finally:
        for key, value in old_env.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value
        get_settings(reload=True)

    output = summary.get("scheme_writer_output") or {}
    draft = summary.get("scheme_draft") or {}
    sections = list(draft.get("sections") or [])
    bundles = list(output.get("section_evidence") or [])
    required = set(
        project_input["generation_requirements"]["citation_required_sections"]
    )
    bundle_by_title = {item.get("section_title"): item for item in bundles}
    section_by_title = {item.get("section_title"): item for item in sections}
    citations = list(output.get("citations") or [])

    events = load_trace_events(summary["paths"]["trace"])
    trace_validation = validate_trace_v2(events)
    tool_started = [
        item for item in events if item.get("event_type") == "tool_started"
    ]

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
        "mainline_success",
        _status(summary.get("status")) == "success",
        {"status": summary.get("status")},
    )
    check(
        "every_section_has_evidence_bundle",
        len(sections) == len(bundles) and bool(sections),
        {"section_count": len(sections), "bundle_count": len(bundles)},
    )
    required_scopes = {
        title: (bundle_by_title.get(title) or {}).get("retrieval_scope")
        for title in required
    }
    check(
        "citation_required_sections_use_section_rag",
        bool(required)
        and all(scope == "section" for scope in required_scopes.values()),
        {"required_scopes": required_scopes},
    )
    expected_tool_calls = 1 + len(required)
    check(
        "tool_trace_records_document_and_section_retrieval",
        len(tool_started) == expected_tool_calls,
        {
            "expected_tool_calls": expected_tool_calls,
            "actual_tool_calls": len(tool_started),
            "tool_call_ids": [item.get("call_id") for item in tool_started],
        },
    )
    citation_ids = [item.get("citation_id") for item in citations]
    check(
        "document_citation_ids_are_unique",
        bool(citation_ids) and len(citation_ids) == len(set(citation_ids)),
        {"citation_ids": citation_ids},
    )
    lineage_matches = {}
    for title in required:
        bundle = bundle_by_title.get(title) or {}
        section = section_by_title.get(title) or {}
        package = (section.get("input") or {}).get("llm_context_package") or {}
        lineage_query = (package.get("lineage") or {}).get(
            "evidence_contract_query"
        )
        lineage_matches[title] = {
            "bundle_query": bundle.get("query"),
            "lineage_query": lineage_query,
            "matched": bool(bundle.get("query"))
            and bundle.get("query") == lineage_query,
        }
    check(
        "context_package_uses_section_evidence_lineage",
        bool(lineage_matches)
        and all(item["matched"] for item in lineage_matches.values()),
        {"lineage_matches": lineage_matches},
    )
    check(
        "trace_v2_still_valid",
        trace_validation.get("status") == "success",
        trace_validation,
    )

    failed_checks = [item for item in checks if item["status"] != "passed"]
    report = {
        "schema_version": "section_aware_retrieval_acceptance_report_v1",
        "status": "success" if not failed_checks else "failed",
        "stage": "section_aware_evidence_retrieval_v1",
        "created_at": _now_iso(),
        "run_id": run_id,
        "task_id": task_id,
        "mainline_status": summary.get("status"),
        "trace_path": summary["paths"]["trace"],
        "summary": {
            "section_count": len(sections),
            "evidence_bundle_count": len(bundles),
            "citation_required_section_count": len(required),
            "tool_call_count": len(tool_started),
            "document_citation_count": len(citations),
            "corrective_retrieval_count": sum(
                int(item.get("recovery_count") or 0) for item in bundles
            ),
        },
        "checks": checks,
        "failed_checks": failed_checks,
    }
    report_path.write_text(
        json.dumps(report, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    print(json.dumps(report, ensure_ascii=False, indent=2))
    print(f"\nAcceptance report: {report_path}")
    if failed_checks:
        print("\n章节级证据调度验收失败")
        return 1

    print("\n========================================")
    print("章节级证据调度正式验收通过")
    print(f"章节数量：{len(sections)}")
    print(f"章节Evidence Bundle：{len(bundles)}")
    print(f"强制引用章节：{len(required)}")
    print(f"RAG Tool调用：{len(tool_started)}")
    print(f"文档级Citation：{len(citations)}")
    print("失败检查：0")
    print("========================================")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
