# -*- coding: utf-8 -*-
# =============================================================================
# 中文阅读说明：命令行脚本模块，用于启动、验收、调试或离线维护。
# 主要定义：load_jsonl、find_keys、short、main。建议先从公开入口函数开始，再沿调用关系向下阅读。
# =============================================================================
r"""
Inspect Agent-RAG trace JSONL for evidence correction, Self-RAG and planning fields.

Usage:
D:\mysoftware\anaconda\envs\enterprise-rag-agent\python.exe ^
D:\MyCode\rag-agent\scripts\debug_trace_quality.py ^
--trace "D:\MyCode\rag-agent\data\runs\run_demo_xxx_trace.jsonl"
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any


TARGET_KEYS = {
    "retrieval_plan",
    "evidence_quality",
    "initial_assessment",
    "final_assessment",
    "corrective_retrieval",
    "correction_triggered",
    "self_rag",
    "agent_self_rag",
    "answer_check",
    "item_judgements",
    "need_rewrite",
    "problems",
}


# 阅读注释（函数）：加载 jsonl。
def load_jsonl(path: Path) -> list[dict[str, Any]]:
    """加载 jsonl。

    参数:
        path: 目标文件或目录路径。

    返回:
        list[dict[str, Any]]

    阅读提示:
        主要直接调用：path.open, enumerate, line.strip, records.append, json.loads, print。
    """
    records: list[dict[str, Any]] = []

    with path.open("r", encoding="utf-8") as file:
        for line_no, line in enumerate(file, start=1):
            line = line.strip()
            if not line:
                continue

            try:
                records.append(json.loads(line))
            except Exception as exc:
                print(f"[WARN] line {line_no} json parse failed: {exc}")

    return records


# 阅读注释（函数）：查找 keys。
def find_keys(
    obj: Any,
    target_keys: set[str],
    path: str = "$",
) -> list[tuple[str, Any]]:
    """查找 keys。

    参数:
        obj: obj，具体约束请结合类型标注和调用方确认。
        target_keys: target keys，具体约束请结合类型标注和调用方确认。
        path: 目标文件或目录路径。

    返回:
        list[tuple[str, Any]]

    阅读提示:
        主要直接调用：isinstance, obj.items, found.append, found.extend, find_keys, enumerate。
    """
    found: list[tuple[str, Any]] = []

    if isinstance(obj, dict):
        for key, value in obj.items():
            current_path = f"{path}.{key}"
            if key in target_keys:
                found.append((current_path, value))
            found.extend(find_keys(value, target_keys, current_path))

    elif isinstance(obj, list):
        for index, item in enumerate(obj):
            found.extend(find_keys(item, target_keys, f"{path}[{index}]"))

    return found


# 阅读注释（函数）：处理 short 相关逻辑。
def short(value: Any, max_len: int = 1200) -> str:
    """处理 short 相关逻辑。

    参数:
        value: value，具体约束请结合类型标注和调用方确认。
        max_len: max len，具体约束请结合类型标注和调用方确认。

    返回:
        str

    阅读提示:
        主要直接调用：json.dumps, len。
    """
    text = json.dumps(value, ensure_ascii=False, indent=2)
    if len(text) > max_len:
        return text[:max_len] + "\n... <truncated>"
    return text


# 阅读注释（函数）：处理 main 相关逻辑。
def main() -> None:
    """处理 main 相关逻辑。

    返回:
        None

    阅读提示:
        主要直接调用：argparse.ArgumentParser, parser.add_argument, parser.parse_args, resolve, expanduser, Path, trace_path.is_file, FileNotFoundError。
    """
    parser = argparse.ArgumentParser(
        description=(
            "Inspect trace fields for evidence correction, Self-RAG and "
            "Adaptive-RAG planning."
        ),
    )
    parser.add_argument(
        "--trace",
        required=True,
        help="Path to trace JSONL file.",
    )
    args = parser.parse_args()

    trace_path = Path(args.trace).expanduser().resolve()
    if not trace_path.is_file():
        raise FileNotFoundError(trace_path)

    records = load_jsonl(trace_path)

    print("=" * 100)
    print(f"Trace: {trace_path}")
    print(f"Records: {len(records)}")
    print("=" * 100)

    total_hits = 0

    for record_index, record in enumerate(records, start=1):
        hits = find_keys(record, TARGET_KEYS)
        if not hits:
            continue

        total_hits += len(hits)

        event_name = (
            record.get("event")
            or record.get("event_type")
            or record.get("step_name")
            or record.get("component_name")
            or record.get("name")
            or "unknown_event"
        )

        print("\n" + "-" * 100)
        print(f"[Record {record_index}] event={event_name}")
        print("-" * 100)

        for field_path, value in hits:
            print(f"\n### {field_path}")
            print(short(value))

    print("\n" + "=" * 100)
    print(f"Total matched fields: {total_hits}")
    print("=" * 100)

    if total_hits == 0:
        print("No C-RAG / Self-RAG / Adaptive-RAG fields found.")
        print("可能原因：")
        print("1. Agent 尚未使用新版 real_rag_tool.py / fake_scheme_writer_agent.py")
        print("2. RAG 主链尚未使用包含这些字段的实现")
        print("3. retrieval_plan 没有进入 RAG Trace")
        print("4. Trace 捕获时没有写入 metadata")


if __name__ == "__main__":
    main()
