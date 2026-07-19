# -*- coding: utf-8 -*-
# =============================================================================
# 中文阅读说明：RAG 核心模块，负责查询变换、召回、融合、重排、证据评估和上下文组装。
# 主要定义：ensure_parent、iter_jsonl_paths、load_jsonl_dicts、write_jsonl。建议先从公开入口函数开始，再沿调用关系向下阅读。
# =============================================================================
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


# 阅读注释（函数）：确保 父块 满足运行约束。
def ensure_parent(path: str | Path) -> None:
    """确保 父块 满足运行约束。

    参数:
        path: 目标文件或目录路径。

    返回:
        None

    阅读提示:
        主要直接调用：Path, str, parent.mkdir。
    """
    p = Path(path)
    parent = p.parent
    if parent and str(parent) not in ("", "."):
        parent.mkdir(parents=True, exist_ok=True)


# 阅读注释（函数）：处理 iter jsonl paths 相关逻辑。
def iter_jsonl_paths(input_path: str | Path) -> Iterable[Path]:
    """处理 iter jsonl paths 相关逻辑。

    参数:
        input_path: 输入 路径，具体约束请结合类型标注和调用方确认。

    返回:
        Iterable[Path]

    阅读提示:
        主要直接调用：Path, p.is_file, p.is_dir, sorted, p.iterdir, x.is_file, x.name.startswith, x.suffix.lower。
    """
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


# 阅读注释（函数）：加载 jsonl dicts。
def load_jsonl_dicts(input_path: str | Path, max_records: Optional[int] = None) -> List[Dict[str, Any]]:
    """加载 jsonl dicts。

    参数:
        input_path: 输入 路径，具体约束请结合类型标注和调用方确认。
        max_records: max 记录集合，具体约束请结合类型标注和调用方确认。

    返回:
        List[Dict[str, Any]]

    阅读提示:
        主要直接调用：iter_jsonl_paths, path.open, enumerate, line.strip, json.loads, ValueError, isinstance, records.append。
    """
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


# 阅读注释（函数）：写入 jsonl。
def write_jsonl(records: Iterable[Dict[str, Any]], output_path: str | Path) -> None:
    """写入 jsonl。

    参数:
        records: 记录集合，具体约束请结合类型标注和调用方确认。
        output_path: 输出 路径，具体约束请结合类型标注和调用方确认。

    返回:
        None

    阅读提示:
        主要直接调用：ensure_parent, open, Path, f.write, json.dumps。
    """
    ensure_parent(output_path)
    with Path(output_path).open("w", encoding="utf-8") as f:
        for rec in records:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")
