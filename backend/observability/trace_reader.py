"""读取 Trace v2 JSONL，并验证完整运行调用链的不变量。

验证器检查 Schema、任务/运行/trace 身份、事件序号、span 开始与终止配对、父子关系、
关键事件覆盖、终态耗时、RAG 血缘和模型提示词摘要。它返回结构化报告而不是在第一个
失败处抛出，使一次检查能够暴露全部轨迹问题。
"""

from __future__ import annotations

import json
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any, Dict, Iterable, List


# 当前约定只有 start 事件打开一个 span。
START_PHASES = {"start"}
# end 和 error 都会关闭一个 span。
TERMINAL_PHASES = {"end", "error"}


def load_trace_events(path: str | Path) -> List[Dict[str, Any]]:
    """从 JSONL 文件按物理顺序加载结构化轨迹事件。

    Args:
        path: 单次运行的 Trace JSONL 文件路径。

    Returns:
        跳过空行后的事件字典列表，顺序与文件一致。

    Raises:
        ValueError: 某一非空行不是合法 JSON，或 JSON 顶层不是对象时抛出，并指出
            具体行号。
        OSError: 文件不存在或无法读取时由文件系统异常直接向上传播。
    """

    # 第一步：统一路径类型并建立有序事件结果列表。
    trace_path = Path(path)
    events: List[Dict[str, Any]] = []

    # 第二步：逐行读取，避免一次性解析整个大文件。
    with trace_path.open("r", encoding="utf-8") as handle:
        for line_number, line in enumerate(handle, start=1):
            # 空行不属于事件，也不参与后续序号验证。
            if not line.strip():
                continue

            try:
                # 第三步：每个非空行必须是独立完整 JSON 文档。
                row = json.loads(line)
            except json.JSONDecodeError as exc:
                raise ValueError(
                    f"invalid trace JSONL at line {line_number}: {exc}"
                ) from exc

            # 第四步：Trace v2 事件顶层必须是对象，数组或标量没有字段语义。
            if not isinstance(row, dict):
                raise ValueError(f"trace row {line_number} must be an object")

            events.append(row)

    return events


def validate_trace_v2(events: Iterable[Dict[str, Any]]) -> Dict[str, Any]:
    """验证一组事件是否构成完整且可审计的 Trace v2 运行。

    Args:
        events: 按实际写入顺序提供的事件字典可迭代对象。

    Returns:
        包含整体状态、事件/span 数量、身份、指标和全部失败检查项的报告字典。
        轨迹内容不满足不变量时返回 ``status='failed'``，而不是抛出异常。
    """

    # 第一步：物化输入，使后续多次遍历和顺序比较稳定。
    rows = list(events)
    failures: List[Dict[str, Any]] = []

    # 第二步：空轨迹无法证明任何运行事实，直接返回最小失败报告。
    if not rows:
        return {
            "schema_version": "trace_v2_validation_report_v1",
            "status": "failed",
            "event_count": 0,
            "span_count": 0,
            "failed_checks": [{"name": "events_non_empty", "details": {}}],
            "metrics": {},
        }

    # 第三步：收集运行级身份、Schema 和事件序号，用于基础一致性检查。
    trace_ids = {row.get("trace_id") for row in rows}
    run_ids = {row.get("run_id") for row in rows}
    task_ids = {row.get("task_id") for row in rows}
    schemas = {row.get("schema_version") for row in rows}
    sequences = [int(row.get("event_sequence") or 0) for row in rows]

    def require(name: str, condition: bool, details: Dict[str, Any]) -> None:
        """在条件失败时追加一个结构化检查结果。

        Args:
            name: 稳定检查名称。
            condition: 当前检查是否通过。
            details: 失败时用于定位问题的有限结构化信息。
        """

        # 验证器聚合所有问题，不在首个失败处中断。
        if not condition:
            failures.append({"name": name, "details": details})

    # 第四步：同一文件中的所有事件必须使用当前 Trace v2 Schema。
    require(
        "schema_is_trace_v2",
        schemas == {"run_trace_event_v2"},
        {"schemas": sorted(str(item) for item in schemas)},
    )

    # 第五步：一个运行文件只能描述一条 trace、一个 run 和一个 task。
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

    # 第六步：事件序号必须从 1 连续递增，并与文件物理顺序一致。
    require(
        "event_sequence_contiguous",
        sequences == list(range(1, len(rows) + 1)),
        {"sequences": sequences},
    )

    # 第七步：按 span_id 聚合事件，同时报告缺少 span_id 的单个事件。
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

    # 第八步：建立全部 span 标识集合，再逐 span 验证生命周期和父子关系。
    all_span_ids = set(events_by_span)
    root_spans = set()
    for span_id, span_events in events_by_span.items():
        starts = [row for row in span_events if row.get("phase") in START_PHASES]
        terminals = [
            row for row in span_events if row.get("phase") in TERMINAL_PHASES
        ]

        # 每个 span 恰好有一个开始和一个终止事件。
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

        # 同一个 span 的所有事件必须声明同一个父级。
        parent_ids = {row.get("parent_span_id") for row in span_events}
        require(
            "span_parent_is_stable",
            len(parent_ids) == 1,
            {"span_id": span_id, "parent_span_ids": list(parent_ids)},
        )

        parent_id = next(iter(parent_ids)) if parent_ids else None
        if parent_id is None:
            # 没有父级的 span 是根候选。
            root_spans.add(span_id)
        else:
            # 非根 span 的父级必须也存在于当前运行事件中。
            require(
                "parent_span_exists",
                parent_id in all_span_ids,
                {"span_id": span_id, "parent_span_id": parent_id},
            )

        # 每个终止事件必须提供非负延迟，保证调用耗时可统计。
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

    # 第九步：完整运行必须只有一个根 span。
    require(
        "single_root_span",
        len(root_spans) == 1,
        {"root_spans": sorted(root_spans)},
    )

    # 第十步：统计事件类型，并检查主链各层开始/结束事件是否全部出现。
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

    # 第十一步：只对输出摘要明确包含 RAG 证据的工具结束事件检查证据契约血缘。
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

    # 第十二步：模型开始事件只能保存提示词长度和哈希摘要，不能记录完整 input_payload。
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

    # 第十三步：根据聚合失败项确定整体状态，并返回可机器消费的验证报告。
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
