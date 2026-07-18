# -*- coding: utf-8 -*-
r"""
Inspect Agent-RAG trace JSONL for C-RAG / Self-RAG / Adaptive-RAG fields.

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
    "adaptive_rag",
    "c_rag",
    "self_rag",
    "agent_self_rag",
    "answer_check",
    "pre_crag_result_count",
    "post_crag_result_count",
    "original_retrieval_strategy",
    "effective_retrieval_strategy",
    "chunk_judgements",
    "c_rag_judgement",
    "need_rewrite",
    "problems",
}


def load_jsonl(path: Path) -> list[dict[str, Any]]:
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


def find_keys(
    obj: Any,
    target_keys: set[str],
    path: str = "$",
) -> list[tuple[str, Any]]:
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


def short(value: Any, max_len: int = 1200) -> str:
    text = json.dumps(value, ensure_ascii=False, indent=2)
    if len(text) > max_len:
        return text[:max_len] + "\n... <truncated>"
    return text


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Inspect trace fields for C-RAG, Self-RAG and Adaptive-RAG.",
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
        print("3. retrieval_strategy 没有传入 RAG")
        print("4. Trace 捕获时没有写入 metadata")


if __name__ == "__main__":
    main()
