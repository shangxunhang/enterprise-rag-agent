# -*- coding: utf-8 -*-
"""
rag_template/store/parent_chunk_store.py
=======================================

Parent chunk 本地存储读取层。

P1 目标：
- 从 parent_chunks.jsonl 或 Spark/本地输出目录加载 parent_chunk_v1。
- 提供 parent_chunk_id -> parent_chunk 的 O(1) 回填能力。
- 不负责向量检索、不负责 rerank、不负责 prompt 组装。
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, Iterable, Iterator, List, Optional


def _iter_jsonl_paths(path: str | Path) -> Iterator[Path]:
    p = Path(path)
    if p.is_file():
        yield p
        return
    if p.is_dir():
        candidates = sorted(
            x for x in p.iterdir()
            if x.is_file() and (x.name.startswith("part-") or x.suffix.lower() in {".jsonl", ".json"})
        )
        for item in candidates:
            yield item
        return
    raise FileNotFoundError(f"Parent chunk path not found: {path}")


def load_jsonl_dicts(path: str | Path) -> List[Dict[str, Any]]:
    records: List[Dict[str, Any]] = []
    for file_path in _iter_jsonl_paths(path):
        with file_path.open("r", encoding="utf-8") as f:
            for line_no, line in enumerate(f, start=1):
                line = line.strip()
                if not line:
                    continue
                try:
                    obj = json.loads(line)
                except json.JSONDecodeError as exc:
                    raise ValueError(f"Invalid JSONL: file={file_path}, line={line_no}, err={exc}") from exc
                if isinstance(obj, dict):
                    records.append(obj)
    return records


class ParentChunkStore:
    """In-memory parent_chunk_v1 store.

    第一版直接内存加载，适合单机 MVP / 本地测试。
    后续可以替换成 HDFS、MySQL、MongoDB 或对象存储，但对上层保持 get(parent_chunk_id) 接口不变。
    """

    def __init__(self, parent_chunks: Iterable[Dict[str, Any]]):
        self._parents: Dict[str, Dict[str, Any]] = {}
        self._load(parent_chunks)

    @classmethod
    def from_jsonl(cls, path: str | Path) -> "ParentChunkStore":
        return cls(load_jsonl_dicts(path))

    def _load(self, parent_chunks: Iterable[Dict[str, Any]]) -> None:
        for parent in parent_chunks:
            parent_id = parent.get("parent_chunk_id")
            if not parent_id:
                continue
            parent_id = str(parent_id)
            if parent_id in self._parents:
                raise ValueError(f"Duplicate parent_chunk_id: {parent_id}")
            self._parents[parent_id] = parent

    def get(self, parent_chunk_id: str, default: Optional[Dict[str, Any]] = None) -> Optional[Dict[str, Any]]:
        if not parent_chunk_id:
            return default
        return self._parents.get(str(parent_chunk_id), default)

    def must_get(self, parent_chunk_id: str) -> Dict[str, Any]:
        parent = self.get(parent_chunk_id)
        if parent is None:
            raise KeyError(f"parent_chunk_id not found: {parent_chunk_id}")
        return parent

    def has(self, parent_chunk_id: str) -> bool:
        return bool(parent_chunk_id) and str(parent_chunk_id) in self._parents

    def ids(self) -> List[str]:
        return list(self._parents.keys())

    def values(self) -> List[Dict[str, Any]]:
        return list(self._parents.values())

    def __len__(self) -> int:
        return len(self._parents)
