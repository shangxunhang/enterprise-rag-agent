# -*- coding: utf-8 -*-
"""读取单个 JSONL 文件或 Spark ``part-*`` 输出目录，并写出 JSONL。

本模块只处理文件发现和逐行 JSON 对象读写，不负责 Schema 标准化、Embedding 或
Milvus 入库。输入目录只扫描直接子文件，不递归扫描更深层目录。
"""

import json
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional


def ensure_parent(path: str | Path) -> None:
    """为目标文件创建非空父目录。

    Args:
        path: 即将写入的文件路径。

    当前目录文件（父目录为 ``.``）不触发 mkdir；其他父目录按需递归创建。
    """

    # 第一步：统一为 Path 并提取父目录。
    target = Path(path)
    parent = target.parent

    # 第二步：只有实际目录层级存在时才创建，避免对空路径执行无意义操作。
    if parent and str(parent) not in ("", "."):
        parent.mkdir(parents=True, exist_ok=True)


def iter_jsonl_paths(input_path: str | Path) -> Iterable[Path]:
    """枚举一个 JSONL 文件或目录中的候选数据文件。

    Args:
        input_path: 普通文件，或包含 ``part-*``、``.jsonl``、``.json`` 文件的目录。

    Yields:
        单文件直接返回自身；目录按文件名排序返回直接子文件。

    Raises:
        FileNotFoundError: 路径既不是文件也不是目录时抛出。
    """

    # 第一步：普通文件不限制扩展名，调用方明确指定即视为候选数据源。
    source = Path(input_path)
    if source.is_file():
        yield source
        return

    # 第二步：目录模式兼容 Spark part-* 输出和常规 JSON/JSONL 文件。
    if source.is_dir():
        candidates = sorted(
            [
                item
                for item in source.iterdir()
                if item.is_file()
                and (
                    item.name.startswith("part-")
                    or item.suffix.lower() in {".jsonl", ".json"}
                )
            ]
        )
        for item in candidates:
            yield item
        return

    raise FileNotFoundError(f"Input path not found: {input_path}")


def load_jsonl_dicts(
    input_path: str | Path,
    max_records: Optional[int] = None,
) -> List[Dict[str, Any]]:
    """按文件和行顺序读取 JSON 对象记录。

    Args:
        input_path: 单文件或 ``iter_jsonl_paths`` 支持的目录。
        max_records: 可选全局最大记录数，跨多个文件累计。

    Returns:
        解析后为字典的记录列表。非字典 JSON 行按原行为静默跳过。

    Raises:
        ValueError: 任一非空行不是合法 JSON 时，附带文件和行号后抛出。
    """

    records: List[Dict[str, Any]] = []

    # 第一步：按确定性文件顺序读取所有候选文件。
    for path in iter_jsonl_paths(input_path):
        with path.open("r", encoding="utf-8") as handle:
            # 第二步：逐行解析，空行不占 max_records 配额。
            for line_number, line in enumerate(handle, start=1):
                normalized = line.strip()
                if not normalized:
                    continue

                try:
                    item = json.loads(normalized)
                except json.JSONDecodeError as exc:
                    raise ValueError(
                        f"Invalid JSON line: file={path}, "
                        f"line={line_number}, err={exc}"
                    ) from exc

                # 第三步：只收集对象行，保持本工具的返回类型契约。
                if isinstance(item, dict):
                    records.append(item)

                # 第四步：达到全局上限后立即停止后续文件读取。
                if (
                    max_records is not None
                    and len(records) >= max_records
                ):
                    return records

    return records


def write_jsonl(
    records: Iterable[Dict[str, Any]],
    output_path: str | Path,
) -> None:
    """覆盖写入 UTF-8 JSONL 文件。

    Args:
        records: 按期望输出顺序提供的字典可迭代对象。
        output_path: 目标文件路径。

    每条记录使用紧凑单行 JSON，并保留中文字符。该写入不是追加或原子发布；需要
    崩溃安全和幂等语义时应使用 ``CrashSafeJsonlRecordSink``。
    """

    # 第一步：创建目标父目录。
    ensure_parent(output_path)

    # 第二步：覆盖目标文件并逐记录写入一行。
    with Path(output_path).open("w", encoding="utf-8") as handle:
        for record in records:
            handle.write(
                json.dumps(record, ensure_ascii=False) + "\n"
            )
