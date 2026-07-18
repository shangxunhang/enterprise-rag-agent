# -*- coding: utf-8 -*-
"""
rag_template/util/jsonl_utils.py
================================

JSONL 文件读写工具。

职责：
1. 读取单个 JSONL 文件或 Spark 输出目录中的 part-* 文件
2. 写出 JSONL 文件
3. 创建输出目录

不负责：
1. schema 标准化
2. embedding
3. Milvus 入库
"""

from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional
import json


def ensure_parent(path: str | Path) -> None:
    p = Path(path)
    parent = p.parent
    if parent and str(parent) not in ("", "."):
        parent.mkdir(parents=True, exist_ok=True)


def iter_jsonl_paths(input_path: str | Path) -> Iterable[Path]:
    p = Path(input_path)
    if p.is_file():
        yield p
        return

    if p.is_dir():
        candidates = sorted([
            x for x in p.iterdir()
            if x.is_file() and (
                x.name.startswith("part-") or x.suffix.lower() in {".jsonl", ".json"}
            )
        ])
        for item in candidates:
            yield item
        return

    raise FileNotFoundError(f"Input path not found: {input_path}")


def load_jsonl_dicts(input_path: str | Path, max_records: Optional[int] = None) -> List[Dict[str, Any]]:
    records: List[Dict[str, Any]] = []
    for path in iter_jsonl_paths(input_path):
        with path.open("r", encoding="utf-8") as f:
            for line_no, line in enumerate(f, start=1):
                line = line.strip()
                if not line:
                    continue
                try:
                    obj = json.loads(line)
                except json.JSONDecodeError as exc:
                    raise ValueError(
                        f"Invalid JSON line: file={path}, line={line_no}, err={exc}"
                    ) from exc
                if isinstance(obj, dict):
                    records.append(obj)
                if max_records is not None and len(records) >= max_records:
                    return records
    return records


def write_jsonl(records: Iterable[Dict[str, Any]], output_path: str | Path) -> None:
    ensure_parent(output_path)
    with Path(output_path).open("w", encoding="utf-8") as f:
        for rec in records:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")
