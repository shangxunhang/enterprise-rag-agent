# =============================================================================
# 中文阅读说明：后端业务模块。
# 主要定义：load_trace_events、validate_trace_v2。建议先从公开入口函数开始，再沿调用关系向下阅读。
# =============================================================================
"""Trace v2 reader and invariant validator."""

from __future__ import annotations

import json
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any, Dict, Iterable, List


START_PHASES = {"start"}
TERMINAL_PHASES = {"end", "error"}


# 阅读注释（函数）：加载 Trace events。
def load_trace_events(path: str | Path) -> List[Dict[str, Any]]:
    """加载 Trace events。

    参数:
        path: 目标文件或目录路径。

    返回:
        List[Dict[str, Any]]

    阅读提示:
        主要直接调用：Path, trace_path.open, enumerate, line.strip, json.loads, ValueError, isinstance, events.append。
    """
    trace_path = Path(path)
    events: List[Dict[str, Any]] = []
    with trace_path.open("r", encoding="utf-8") as handle:
        for line_number, line in enumerate(handle, start=1):
            if not line.strip():
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError as exc:
                raise ValueError(
                    f"invalid trace JSONL at line {line_number}: {exc}"
                ) from exc
            if not isinstance(row, dict):
                raise ValueError(f"trace row {line_number} must be an object")
            events.append(row)
    return events


# 阅读注释（函数）：校验 Trace v2。
def validate_trace_v2(events: Iterable[Dict[str, Any]]) -> Dict[str, Any]:
    """校验 Trace v2。

    参数:
        events: events，具体约束请结合类型标注和调用方确认。

    返回:
        Dict[str, Any]

    阅读提示:
        主要直接调用：list, row.get, int, require, sorted, str, len, range。
    """
    rows = list(events)
    failures: List[Dict[str, Any]] = []
    if not rows:
        return {
            "schema_version": "trace_v2_validation_report_v1",
            "status": "failed",
            "event_count": 0,
            "span_count": 0,
            "failed_checks": [{"name": "events_non_empty", "details": {}}],
            "metrics": {},
        }

    trace_ids = {row.get("trace_id") for row in rows}
    run_ids = {row.get("run_id") for row in rows}
    task_ids = {row.get("task_id") for row in rows}
    schemas = {row.get("schema_version") for row in rows}
    sequences = [int(row.get("event_sequence") or 0) for row in rows]

    # 阅读注释（函数）：处理 require 相关逻辑。
    def require(name: str, condition: bool, details: Dict[str, Any]) -> None:
        """处理 require 相关逻辑。

        参数:
            name: 名称，具体约束请结合类型标注和调用方确认。
            condition: condition，具体约束请结合类型标注和调用方确认。
            details: details，具体约束请结合类型标注和调用方确认。

        返回:
            None

        阅读提示:
            主要直接调用：failures.append。
        """
        if not condition:
            failures.append({"name": name, "details": details})

    require(
        "schema_is_trace_v2",
        schemas == {"run_trace_event_v2"},
        {"schemas": sorted(str(item) for item in schemas)},
    )
    require(
        "single_trace_id",
        len(trace_ids) == 1 and None not in trace_ids,
        {"trace_ids": sorted(str(item) for item in trace_ids)},
    )
    require(
        "single_run_id",
        len(run_ids) == 1 and None not in run_ids,
        {"run_ids": sorted(str(item) for item in run_ids)},
    )
    require(
        "single_task_id",
        len(task_ids) == 1 and None not in task_ids,
        {"task_ids": sorted(str(item) for item in task_ids)},
    )
    require(
        "event_sequence_contiguous",
        sequences == list(range(1, len(rows) + 1)),
        {"sequences": sequences},
    )

    events_by_span: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
    for row in rows:
        span_id = row.get("span_id")
        require(
            "span_id_present",
            bool(span_id),
            {"event_id": row.get("event_id")},
        )
        if span_id:
            events_by_span[str(span_id)].append(row)

    all_span_ids = set(events_by_span)
    root_spans = set()
    for span_id, span_events in events_by_span.items():
        starts = [row for row in span_events if row.get("phase") in START_PHASES]
        terminals = [row for row in span_events if row.get("phase") in TERMINAL_PHASES]
        require(
            "span_has_single_start",
            len(starts) == 1,
            {"span_id": span_id, "start_count": len(starts)},
        )
        require(
            "span_has_single_terminal",
            len(terminals) == 1,
            {"span_id": span_id, "terminal_count": len(terminals)},
        )
        parent_ids = {row.get("parent_span_id") for row in span_events}
        require(
            "span_parent_is_stable",
            len(parent_ids) == 1,
            {"span_id": span_id, "parent_span_ids": list(parent_ids)},
        )
        parent_id = next(iter(parent_ids)) if parent_ids else None
        if parent_id is None:
            root_spans.add(span_id)
        else:
            require(
                "parent_span_exists",
                parent_id in all_span_ids,
                {"span_id": span_id, "parent_span_id": parent_id},
            )
        for row in terminals:
            require(
                "terminal_latency_present",
                row.get("latency_ms") is not None
                and int(row.get("latency_ms")) >= 0,
                {
                    "span_id": span_id,
                    "event_type": row.get("event_type"),
                    "latency_ms": row.get("latency_ms"),
                },
            )

    require(
        "single_root_span",
        len(root_spans) == 1,
        {"root_spans": sorted(root_spans)},
    )

    event_type_counts = Counter(str(row.get("event_type")) for row in rows)
    required_event_types = {
        "run_started",
        "run_finished",
        "workflow_started",
        "workflow_finished",
        "agent_started",
        "agent_finished",
        "tool_started",
        "tool_finished",
        "model_started",
        "model_finished",
    }
    missing_types = sorted(required_event_types - set(event_type_counts))
    require(
        "required_event_types_present",
        not missing_types,
        {"missing_event_types": missing_types},
    )

    tool_finishes = [
        row for row in rows if row.get("event_type") == "tool_finished"
    ]
    rag_tool_finishes = [
        row
        for row in tool_finishes
        if (row.get("output_summary") or {}).get("rag_evidence") is not None
    ]
    if rag_tool_finishes:
        require(
            "rag_lineage_present",
            all(
                bool((row.get("lineage") or {}).get("evidence_contract_sha256"))
                for row in rag_tool_finishes
            ),
            {
                "rag_tool_finish_count": len(rag_tool_finishes),
                "lineages": [row.get("lineage") for row in rag_tool_finishes],
            },
        )

    model_starts = [
        row for row in rows if row.get("event_type") == "model_started"
    ]
    require(
        "model_prompts_are_summarized",
        all(
            "prompt_sha256" in (row.get("input_summary") or {})
            and "prompt_chars" in (row.get("input_summary") or {})
            and not row.get("input_payload")
            for row in model_starts
        ),
        {"model_start_count": len(model_starts)},
    )

    status = "success" if not failures else "failed"
    return {
        "schema_version": "trace_v2_validation_report_v1",
        "status": status,
        "event_count": len(rows),
        "span_count": len(events_by_span),
        "root_span_id": next(iter(root_spans)) if len(root_spans) == 1 else None,
        "trace_id": next(iter(trace_ids)) if len(trace_ids) == 1 else None,
        "run_id": next(iter(run_ids)) if len(run_ids) == 1 else None,
        "task_id": next(iter(task_ids)) if len(task_ids) == 1 else None,
        "event_type_counts": dict(sorted(event_type_counts.items())),
        "metrics": {
            "model_call_count": event_type_counts.get("model_started", 0),
            "tool_call_count": event_type_counts.get("tool_started", 0),
            "agent_span_count": event_type_counts.get("agent_started", 0),
            "error_event_count": sum(
                1 for row in rows if row.get("phase") == "error"
            ),
            "max_event_sequence": max(sequences),
        },
        "failed_checks": failures,
    }
