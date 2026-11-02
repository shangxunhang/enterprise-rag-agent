# -*- coding: utf-8 -*-
"""读取父块/子块 JSONL，并按身份提供父级上下文回填。

``ParentChunkStore`` 是只读内存索引：启动时加载父块文件，按 ``parent_chunk_id`` 建立
字典，检索命中子块后可 O(1) 恢复父块正文。JSONL 输入既支持普通文件，也支持 Spark
输出目录；记录字段会被标准化为父块协议，原始额外字段保存在 ``extra``。
"""

import json
from pathlib import Path
from typing import Any, Dict, Iterable, List, Union

from rag.configs.SchemaConfig import (
    DEFAULT_PARENT_CHUNK_SCHEMA_VERSION,
)


PathLike = Union[str, Path]


def _iter_jsonl_files(path: Path) -> Iterable[Path]:
    """枚举单个 JSONL 文件或目录中的数据文件。

    Args:
        path: 普通文件，或包含 ``part-*``、``.jsonl``、``.json`` 文件的目录。

    Yields:
        单文件直接返回自身；目录按文件名排序返回直接子文件。

    Raises:
        FileNotFoundError: 路径既不是文件也不是目录时抛出。
    """

    # 普通文件不限制扩展名，调用方显式指定即视为数据源。
    if path.is_file():
        yield path
        return

    # 目录模式兼容 Spark part-* 和常规 JSON/JSONL 文件。
    if path.is_dir():
        candidates = sorted(
            [
                item
                for item in path.iterdir()
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

    raise FileNotFoundError(f"Path not found: {path}")


def load_jsonl_dicts(path: PathLike) -> List[Dict[str, Any]]:
    """读取一个 JSONL 文件或兼容目录中的对象记录。

    Args:
        path: 输入文件或目录。

    Returns:
        按文件名和行顺序读取的对象列表。空行跳过，非对象 JSON 行也跳过。

    Raises:
        ValueError: 任一非空行不是合法 JSON 时，附带文件和行号抛出。
    """

    input_path = Path(path)
    records: List[Dict[str, Any]] = []

    # 第一步：按确定性文件顺序读取。
    for file_path in _iter_jsonl_files(input_path):
        with file_path.open("r", encoding="utf-8") as handle:
            # 第二步：逐行解析，空行不产生记录。
            for line_number, line in enumerate(handle, start=1):
                normalized = line.strip()
                if not normalized:
                    continue
                try:
                    item = json.loads(normalized)
                except json.JSONDecodeError as exc:
                    raise ValueError(
                        f"Invalid JSON line: file={file_path}, "
                        f"line={line_number}, err={exc}"
                    ) from exc

                # 只保留对象行，保持函数返回类型稳定。
                if isinstance(item, dict):
                    records.append(item)

    return records


def _normalize_parent_record(
    item: Dict[str, Any],
) -> Dict[str, Any]:
    """把兼容父块记录映射为稳定字段集合。

    ``parent_chunk_id`` 优先读取专用字段，缺失时回退通用 ``chunk_id``。不属于标准
    顶层字段的内容统一进入 ``extra``，防止旧版本扩展字段丢失。
    """

    # 第一步：读取父块身份和已有 extra。
    parent_chunk_id = str(
        item.get("parent_chunk_id") or item.get("chunk_id") or ""
    )
    extra = dict(item.get("extra") or {})

    # 第二步：收集未被标准字段消费的兼容扩展键。
    reserved_keys = {
        "schema_version",
        "chunk_id",
        "parent_chunk_id",
        "doc_id",
        "file_id",
        "tenant_id",
        "kb_id",
        "text",
        "chunk_index",
        "parent_index",
        "chunk_level",
        "child_chunk_ids",
        "source",
        "source_path",
        "title",
        "section",
        "section_level",
        "page_num",
        "page_start",
        "page_end",
        "start_char",
        "end_char",
        "token_count",
        "chunker_name",
        "chunker_version",
        "created_at",
        "language",
        "status",
        "security_level",
        "extra",
    }
    for key, value in item.items():
        if key not in reserved_keys and key not in extra:
            extra[key] = value

    # 第三步：构造完整父块投影；缺失身份和文本由调用方后续校验或跳过。
    return {
        "schema_version": item.get(
            "schema_version",
            DEFAULT_PARENT_CHUNK_SCHEMA_VERSION,
        ),
        "chunk_id": parent_chunk_id,
        "parent_chunk_id": parent_chunk_id,
        "doc_id": item.get("doc_id", ""),
        "file_id": item.get("file_id"),
        "tenant_id": item.get("tenant_id"),
        "kb_id": item.get("kb_id"),
        "text": str(item.get("text") or ""),
        "chunk_index": item.get(
            "chunk_index",
            item.get("parent_index", 0),
        ),
        "parent_index": item.get(
            "parent_index",
            item.get("chunk_index", 0),
        ),
        "chunk_level": item.get("chunk_level", 0),
        "child_chunk_ids": item.get("child_chunk_ids") or [],
        "source": item.get("source"),
        "source_path": item.get("source_path"),
        "title": item.get("title"),
        "section": item.get("section"),
        "section_level": item.get("section_level"),
        "page_num": item.get("page_num"),
        "page_start": item.get("page_start"),
        "page_end": item.get("page_end"),
        "start_char": item.get("start_char"),
        "end_char": item.get("end_char"),
        "token_count": item.get("token_count"),
        "chunker_name": item.get("chunker_name"),
        "chunker_version": item.get("chunker_version"),
        "created_at": item.get("created_at"),
        "language": item.get("language", "zh"),
        "status": item.get("status", "active"),
        "security_level": item.get(
            "security_level",
            "internal",
        ),
        "extra": extra,
    }


class ParentChunkStore:
    """在内存中按 ``parent_chunk_id`` 索引父块。"""

    def __init__(self, path: PathLike) -> None:
        """加载、标准化并索引父块文件。

        空 parent_chunk_id 记录会跳过；重复身份后出现的记录覆盖先前记录，符合字典
        构建语义。需要严格重复检测时应在离线验证阶段完成。
        """

        self.path = Path(path)
        raw_records = load_jsonl_dicts(self.path)
        self._records: Dict[str, Dict[str, Any]] = {}

        # 逐记录标准化并按父块身份写入内存索引。
        for raw_record in raw_records:
            record = _normalize_parent_record(raw_record)
            parent_chunk_id = record["parent_chunk_id"]
            if not parent_chunk_id:
                continue
            self._records[parent_chunk_id] = record

    def get(self, parent_chunk_id: str) -> Dict[str, Any]:
        """返回一个父块记录。

        Raises:
            KeyError: 身份为空或未找到时抛出，并包含来源文件路径便于排查。
        """

        key = str(parent_chunk_id or "")
        if not key:
            raise KeyError("parent_chunk_id cannot be empty")
        if key not in self._records:
            raise KeyError(
                f"Parent chunk not found: {key}, source={self.path}"
            )
        return self._records[key]

    def get_many(
        self,
        parent_chunk_ids: List[str],
        *,
        strict: bool = True,
    ) -> List[Dict[str, Any]]:
        """按输入顺序批量读取父块。

        Args:
            parent_chunk_ids: 父块身份列表，可包含重复值。
            strict: 为 True 时任一缺失立即抛出；为 False 时跳过缺失。

        Returns:
            与成功查找顺序一致的父块列表，不自动去重。
        """

        result: List[Dict[str, Any]] = []
        for parent_chunk_id in parent_chunk_ids:
            try:
                result.append(self.get(parent_chunk_id))
            except KeyError:
                if strict:
                    raise
        return result

    def __len__(self) -> int:
        """返回已索引的唯一父块数量。"""

        return len(self._records)

    def stats(self) -> Dict[str, Any]:
        """返回来源路径和父块数量的轻量统计。"""

        return {
            "source_path": str(self.path),
            "parent_count": len(self._records),
        }
