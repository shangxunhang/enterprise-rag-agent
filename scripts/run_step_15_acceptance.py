# =============================================================================
# 中文阅读说明：命令行脚本模块，用于启动、验收、调试或离线维护。
# 主要定义：_now_iso、_status、main。建议先从公开入口函数开始，再沿调用关系向下阅读。
# =============================================================================
"""Step 15 GraphState, Node I/O Contract and WorkflowEnginePort acceptance."""

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
    parser = argparse.ArgumentParser(description="Run Step 15 acceptance.")
    parser.add_argument(
        "--output-root",
        default=str(PROJECT_ROOT / "data" / "step_15_acceptance"),
    )
    parser.add_argument(
        "--report-path",
        default=str(
            PROJECT_ROOT
            / "data"
            / "processed"
            / "indexes"
            / "step_15_acceptance_report.json"
        ),
    )
    args = parser.parse_args()

    output_root = Path(args.output_root).expanduser().resolve()
    report_path = Path(args.report_path).expanduser().resolve()
    output_root.mkdir(parents=True, exist_ok=True)
    report_path.parent.mkdir(parents=True, exist_ok=True)

    run_id = f"step_15_acceptance_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
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

    supervisor = summary.get("supervisor_result") or {}
    result_payload = supervisor.get("result") or {}
    graph_state = result_payload.get("shared_state") or {}
    execution = result_payload.get("workflow_execution") or {}
    node_inputs = list(execution.get("node_inputs") or [])
    node_outputs = list(execution.get("node_outputs") or [])
    node_history = list(graph_state.get("node_history") or [])

    trace_path = Path(summary["paths"]["trace"])
    events = load_trace_events(trace_path)
    trace_validation = validate_trace_v2(events)
    agent_starts = [row for row in events if row.get("event_type") == "agent_started"]
    agent_finishes = [row for row in events if row.get("event_type") == "agent_finished"]

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
        _status(summary.get("status")) == "success",
        {"status": summary.get("status")},
    )
    check(
        "graph_state_is_canonical",
        graph_state.get("schema_version") == "graph_state_v1"
        and graph_state.get("workflow_engine_name") == "native_workflow_engine"
        and int(graph_state.get("graph_revision") or 0) == 2,
        {
            "schema_version": graph_state.get("schema_version"),
            "engine_name": graph_state.get("workflow_engine_name"),
            "engine_version": graph_state.get("workflow_engine_version"),
            "graph_revision": graph_state.get("graph_revision"),
        },
    )
    check(
        "workflow_engine_port_result",
        execution.get("schema_version") == "workflow_engine_result_v1"
        and execution.get("engine_name") == "native_workflow_engine"
        and execution.get("engine_version") == "v1"
        and _status(execution.get("status")) == "success",
        {
            "schema_version": execution.get("schema_version"),
            "engine_name": execution.get("engine_name"),
            "engine_version": execution.get("engine_version"),
            "status": execution.get("status"),
        },
    )
    expected_nodes = ["step_001", "step_002"]
    check(
        "graph_nodes_complete_in_order",
        execution.get("completed_node_ids") == expected_nodes
        and graph_state.get("completed_node_ids") == expected_nodes
        and len(node_history) == 2,
        {
            "engine_completed_node_ids": execution.get("completed_node_ids"),
            "state_completed_node_ids": graph_state.get("completed_node_ids"),
            "node_history_count": len(node_history),
        },
    )
    projection_ok = (
        len(node_inputs) == 2
        and node_inputs[0].get("declared_read_keys") == ["project_input"]
        and set(node_inputs[0].get("values") or {}) == {"project_input"}
        and node_inputs[1].get("declared_read_keys")
        == ["normalized_project_input", "structured_facts"]
        and set(node_inputs[1].get("values") or {})
        == {"normalized_project_input", "structured_facts"}
        and not node_inputs[1].get("missing_keys")
    )
    check(
        "node_inputs_are_declared_state_subsets",
        projection_ok,
        {
            "node_inputs": [
                {
                    "node_id": item.get("node_id"),
                    "declared_read_keys": item.get("declared_read_keys"),
                    "value_keys": list((item.get("values") or {}).keys()),
                    "missing_keys": item.get("missing_keys"),
                    "input_sha256": item.get("input_sha256"),
                }
                for item in node_inputs
            ]
        },
    )
    revisions = [
        (
            (item.get("state_delta") or {}).get("base_revision"),
            (item.get("state_delta") or {}).get("next_revision"),
        )
        for item in node_outputs
    ]
    delta_ok = (
        len(node_outputs) == 2
        and revisions == [(0, 1), (1, 2)]
        and all(
            (item.get("state_delta") or {}).get("schema_version")
            == "graph_state_delta_v1"
            and bool((item.get("state_delta") or {}).get("delta_sha256"))
            and (item.get("metadata") or {}).get("isolated_state_execution") is True
            for item in node_outputs
        )
    )
    check(
        "node_outputs_are_revisioned_deltas",
        delta_ok,
        {
            "revisions": revisions,
            "deltas": [
                {
                    "node_id": item.get("node_id"),
                    "delta_sha256": (item.get("state_delta") or {}).get("delta_sha256"),
                    "changed_paths": (item.get("state_delta") or {}).get("changed_paths"),
                    "observed_write_roots": (item.get("state_delta") or {}).get("observed_write_roots"),
                }
                for item in node_outputs
            ],
        },
    )
    final_state_hash = execution.get("final_state_sha256")
    check(
        "workflow_execution_state_revision_matches",
        int(execution.get("initial_revision") or 0) == 0
        and int(execution.get("final_revision") or 0) == 2
        and bool(final_state_hash),
        {
            "initial_revision": execution.get("initial_revision"),
            "final_revision": execution.get("final_revision"),
            "final_state_sha256": final_state_hash,
        },
    )
    sections = list((summary.get("scheme_draft") or {}).get("sections") or [])
    context_packages = [
        ((section.get("input") or {}).get("llm_context_package") or {})
        for section in sections
    ]
    check(
        "step_14_context_contract_preserved",
        len(sections) == 8
        and len(context_packages) == 8
        and all(
            item.get("schema_version") == "llm_context_package_v1"
            for item in context_packages
        ),
        {
            "section_count": len(sections),
            "context_package_count": len(context_packages),
        },
    )
    trace_graph_ok = (
        trace_validation.get("status") == "success"
        and len(agent_starts) == 2
        and len(agent_finishes) == 2
        and all(
            bool((item.get("input_summary") or {}).get("node_input_sha256"))
            and (item.get("input_summary") or {}).get("graph_state_schema")
            == "graph_state_v1"
            for item in agent_starts
        )
        and all(
            bool((item.get("output_summary") or {}).get("state_delta_sha256"))
            for item in agent_finishes
        )
    )
    check(
        "trace_records_graph_node_contracts",
        trace_graph_ok,
        {
            "trace_validation": trace_validation,
            "agent_start_summaries": [item.get("input_summary") for item in agent_starts],
            "agent_finish_summaries": [item.get("output_summary") for item in agent_finishes],
        },
    )

    failed_checks = [item for item in checks if item["status"] != "passed"]
    report = {
        "schema_version": "step_15_acceptance_report_v1",
        "status": "success" if not failed_checks else "failed",
        "stage": "step_15_graph_state_node_contract_workflow_engine_port",
        "created_at": _now_iso(),
        "run_id": run_id,
        "task_id": task_id,
        "mainline_status": summary.get("status"),
        "trace_path": str(trace_path),
        "graph_summary": {
            "state_schema_version": graph_state.get("schema_version"),
            "engine_name": execution.get("engine_name"),
            "engine_version": execution.get("engine_version"),
            "initial_revision": execution.get("initial_revision"),
            "final_revision": execution.get("final_revision"),
            "completed_node_ids": execution.get("completed_node_ids"),
            "node_input_count": len(node_inputs),
            "node_output_count": len(node_outputs),
            "node_history_count": len(node_history),
            "final_state_sha256": final_state_hash,
        },
        "trace_summary": {
            "event_count": len(events),
            "span_count": trace_validation.get("span_count"),
            "trace_id": trace_validation.get("trace_id"),
            "status": trace_validation.get("status"),
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
    print("\n========================================")
    if report["status"] == "success":
        print("Step 15正式验收通过")
        print(f"GraphState：{report['graph_summary']['state_schema_version']}")
        print(
            "Workflow Engine："
            f"{report['graph_summary']['engine_name']}/"
            f"{report['graph_summary']['engine_version']}"
        )
        print(
            "状态版本："
            f"{report['graph_summary']['initial_revision']} → "
            f"{report['graph_summary']['final_revision']}"
        )
        print(
            "完成节点："
            f"{', '.join(report['graph_summary']['completed_node_ids'] or [])}"
        )
        print(f"Node Input：{report['graph_summary']['node_input_count']}")
        print(f"Node Output：{report['graph_summary']['node_output_count']}")
        print(f"Trace事件：{report['trace_summary']['event_count']}")
        print(f"Trace Span：{report['trace_summary']['span_count']}")
        print("失败检查：0")
    else:
        print("Step 15验收失败")
        print(f"失败检查：{len(failed_checks)}")
    print("========================================")
    return 0 if report["status"] == "success" else 1


if __name__ == "__main__":
    raise SystemExit(main())
