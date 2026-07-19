# =============================================================================
# 中文阅读说明：命令行脚本模块，用于启动、验收、调试或离线维护。
# 主要定义：_now_iso、_bundle_by_title、_trace_events、main。建议先从公开入口函数开始，再沿调用关系向下阅读。
# =============================================================================
"""Run deterministic mainline closure scenarios before LangGraph migration.

This acceptance intentionally uses FakeRAG/FakeLLM so the recovery branches are
repeatable.  Real RAG + real Qwen remains the responsibility of ``run_demo.py``.
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List

PROJECT_ROOT = Path(__file__).resolve().parents[1]
BACKEND_ROOT = PROJECT_ROOT / "backend"
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))
if str(Path(__file__).resolve().parent) not in sys.path:
    sys.path.insert(0, str(Path(__file__).resolve().parent))

from run_demo import persist_end_to_end_artifacts
from system_test.mainline_audit import audit_mainline
from system_test.mainline_closure import run_fake_mainline_scenario


# 阅读注释（函数）：处理 now iso 相关逻辑。
def _now_iso() -> str:
    """处理 now iso 相关逻辑。

    返回:
        str

    阅读提示:
        主要直接调用：isoformat, datetime.now。
    """
    return datetime.now(timezone.utc).isoformat()


# 阅读注释（函数）：处理 bundle by title 相关逻辑。
def _bundle_by_title(summary: Dict[str, Any]) -> Dict[str, Dict[str, Any]]:
    """处理 bundle by title 相关逻辑。

    参数:
        summary: summary，具体约束请结合类型标注和调用方确认。

    返回:
        Dict[str, Dict[str, Any]]

    阅读提示:
        主要直接调用：summary.get, str, item.get, output.get。
    """
    output = summary.get("scheme_writer_output") or {}
    return {
        str(item.get("section_title") or ""): item
        for item in (output.get("section_evidence") or [])
    }


# 阅读注释（函数）：处理 Trace events 相关逻辑。
def _trace_events(summary: Dict[str, Any]) -> List[Dict[str, Any]]:
    """处理 Trace events 相关逻辑。

    参数:
        summary: summary，具体约束请结合类型标注和调用方确认。

    返回:
        List[Dict[str, Any]]

    阅读提示:
        主要直接调用：Path, json.loads, splitlines, trace_path.read_text, line.strip。
    """
    trace_path = Path(summary["paths"]["trace"])
    return [
        json.loads(line)
        for line in trace_path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


# 阅读注释（函数）：处理 main 相关逻辑。
def main() -> int:
    """处理 main 相关逻辑。

    返回:
        int

    阅读提示:
        主要直接调用：argparse.ArgumentParser, parser.add_argument, str, parser.parse_args, resolve, expanduser, Path, output_root.mkdir。
    """
    parser = argparse.ArgumentParser(description="Run mainline closure acceptance.")
    parser.add_argument(
        "--output-root",
        default=str(PROJECT_ROOT / "data/mainline_closure_acceptance"),
    )
    parser.add_argument(
        "--report-path",
        default=str(
            PROJECT_ROOT
            / "data/processed/indexes/mainline_closure_acceptance_report.json"
        ),
    )
    args = parser.parse_args()

    output_root = Path(args.output_root).expanduser().resolve()
    report_path = Path(args.report_path).expanduser().resolve()
    output_root.mkdir(parents=True, exist_ok=True)
    report_path.parent.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")

    corrective = run_fake_mainline_scenario(
        output_root / "corrective",
        run_id=f"mainline_corrective_{stamp}",
        rag_scenario="corrective_retrieval",
        llm_scenario="force_corrective_retrieval",
        enable_corrective_section_retrieval=True,
        citation_required_sections=["安全设计"],
    )
    collision = run_fake_mainline_scenario(
        output_root / "citation_collision",
        run_id=f"mainline_collision_{stamp}",
        rag_scenario="citation_collision",
        llm_scenario="always_grounded",
        enable_corrective_section_retrieval=False,
    )
    business_failure = run_fake_mainline_scenario(
        output_root / "business_failure",
        run_id=f"mainline_business_failure_{stamp}",
        rag_scenario="business_gate_failure",
        llm_scenario="force_business_gate_failure",
        enable_corrective_section_retrieval=True,
    )
    business_persisted = persist_end_to_end_artifacts(
        business_failure,
        runtime_preflight={
            "mode": "fake",
            "model_name": "fake_llm",
            "rag_tool": "FakeRAGTool",
            "scenario": "force_business_gate_failure",
        },
        expected_model_name="fake_llm",
        expected_real_rag=False,
    )
    audit = audit_mainline(PROJECT_ROOT)

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

    corrective_safety = _bundle_by_title(corrective).get("安全设计") or {}
    corrective_tool_events = [
        item
        for item in _trace_events(corrective)
        if item.get("event_type") == "tool_started"
    ]
    check(
        "corrective_retrieval_branch",
        corrective.get("status") == "success"
        and corrective_safety.get("recovery_count") == 1
        and corrective_safety.get("retrieval_scope") == "recovery"
        and any("recovery_1" in str(item.get("call_id") or "") for item in corrective_tool_events),
        {
            "status": corrective.get("status"),
            "safety_bundle": corrective_safety,
            "tool_call_ids": [item.get("call_id") for item in corrective_tool_events],
        },
    )

    collision_citations = (
        collision.get("scheme_writer_output") or {}
    ).get("citations") or []
    collision_ids = [item.get("citation_id") for item in collision_citations]
    check(
        "citation_collision_registry",
        collision.get("status") == "success"
        and collision_ids == ["C1", "C2", "C3", "C4"]
        and len(collision_ids) == len(set(collision_ids)),
        {
            "status": collision.get("status"),
            "citation_ids": collision_ids,
            "source_document_ids": [
                item.get("source_document_id") for item in collision_citations
            ],
        },
    )

    business_report_path = Path(business_persisted["report_path"])
    business_answer_path = Path(business_persisted["answer_path"])
    business_report = json.loads(business_report_path.read_text(encoding="utf-8"))
    check(
        "business_failure_artifacts_preserved",
        business_failure.get("status") == "failed"
        and business_answer_path.is_file()
        and bool(business_answer_path.read_text(encoding="utf-8").strip())
        and business_report.get("business_gate_failure") is True
        and (business_report.get("hard_gate") or {}).get("passed") is False,
        {
            "status": business_failure.get("status"),
            "answer_path": str(business_answer_path),
            "report_path": str(business_report_path),
            "hard_gate": business_report.get("hard_gate"),
        },
    )

    findings = {item["id"]: item for item in audit.get("findings") or []}
    check(
        "step_16_noop_and_coarse_node_audit",
        all(
            findings.get(item_id, {}).get("present") is False
            for item_id in (
                "generation_checker_noop",
                "repair_strategy_noop",
                "evidence_assessor_noop",
            )
        )
        and findings.get("section_aware_retrieval_present", {}).get("present")
        is True,
        {"audit_summary": audit.get("summary"), "findings": findings},
    )

    failed_checks = [item for item in checks if item["status"] != "passed"]
    report = {
        "schema_version": "mainline_closure_acceptance_report_v1",
        "status": "success" if not failed_checks else "failed",
        "stage": "pre_langgraph_mainline_closure",
        "created_at": _now_iso(),
        "summary": {
            "scenario_count": 3,
            "passed_check_count": len(checks) - len(failed_checks),
            "failed_check_count": len(failed_checks),
            "corrective_tool_call_count": len(corrective_tool_events),
            "collision_citation_count": len(collision_ids),
            "business_failure_report_saved": business_report_path.is_file(),
            "audit_active_risk_count": audit.get("summary", {}).get("active_risk_count"),
        },
        "scenario_paths": {
            "corrective_trace": corrective["paths"]["trace"],
            "collision_trace": collision["paths"]["trace"],
            "business_failure_trace": business_failure["paths"]["trace"],
            "business_failure_answer": str(business_answer_path),
            "business_failure_report": str(business_report_path),
        },
        "checks": checks,
        "audit": audit,
        "failed_checks": failed_checks,
    }
    report_path.write_text(
        json.dumps(report, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    print(json.dumps(report, ensure_ascii=False, indent=2))
    print(f"\nAcceptance report: {report_path}")

    if failed_checks:
        print("\n主链闭环验收失败")
        return 1

    print("\n========================================")
    print("主链闭环正式验收通过")
    print("Corrective Retrieval：通过")
    print("Citation碰撞重编号：通过")
    print("业务失败产物保留：通过")
    print("Step 16 noop/大节点审计：完成")
    print(f"审计活跃风险：{audit['summary']['active_risk_count']}")
    print(f"验收报告：{report_path}")
    print("========================================")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
