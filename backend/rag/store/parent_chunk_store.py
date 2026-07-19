# -*- coding: utf-8 -*-
# =============================================================================
# 中文阅读说明：RAG 核心模块，负责查询变换、召回、融合、重排、证据评估和上下文组装。
# 主要定义：_iter_jsonl_paths、load_jsonl_dicts、ParentChunkStore。建议先从公开入口函数开始，再沿调用关系向下阅读。
# =============================================================================
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


# 阅读注释（函数）：处理 iter jsonl paths 相关逻辑。
def _iter_jsonl_paths(path: str | Path) -> Iterator[Path]:
    """处理 iter jsonl paths 相关逻辑。

    参数:
        path: 目标文件或目录路径。

    返回:
        Iterator[Path]

    阅读提示:
        主要直接调用：Path, p.is_file, p.is_dir, sorted, p.iterdir, x.is_file, x.name.startswith, x.suffix.lower。
    """
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


# 阅读注释（函数）：加载 jsonl dicts。
def load_jsonl_dicts(path: str | Path) -> List[Dict[str, Any]]:
    """加载 jsonl dicts。

    参数:
        path: 目标文件或目录路径。

    返回:
        List[Dict[str, Any]]

    阅读提示:
        主要直接调用：_iter_jsonl_paths, file_path.open, enumerate, line.strip, json.loads, ValueError, isinstance, records.append。
    """
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


# 阅读注释（类）：封装 父块 文本块 store，集中封装相关状态、依赖和行为。
class ParentChunkStore:
    """In-memory parent_chunk_v1 store.

    第一版直接内存加载，适合单机 MVP / 本地测试。
    后续可以替换成 HDFS、MySQL、MongoDB 或对象存储，但对上层保持 get(parent_chunk_id) 接口不变。
    """

    # 阅读注释（函数）：初始化 ParentChunkStore，保存运行所需的依赖、配置或状态。
    def __init__(self, parent_chunks: Iterable[Dict[str, Any]]):
        """初始化 ParentChunkStore，保存运行所需的依赖、配置或状态。

        参数:
            parent_chunks: 父块 chunks，具体约束请结合类型标注和调用方确认。

        返回:
            未显式标注；请结合调用方和实际返回语句理解。

        阅读提示:
            主要直接调用：self._load。
        """
        self._parents: Dict[str, Dict[str, Any]] = {}
        self._load(parent_chunks)

    # 阅读注释（函数）：根据 jsonl 创建 ParentChunkStore。
    @classmethod
    def from_jsonl(cls, path: str | Path) -> "ParentChunkStore":
        """根据 jsonl 创建 ParentChunkStore。

        参数:
            path: 目标文件或目录路径。

        返回:
            'ParentChunkStore'

        阅读提示:
            主要直接调用：cls, load_jsonl_dicts。
        """
        return cls(load_jsonl_dicts(path))

    # 阅读注释（函数）：加载 ParentChunkStore。
    def _load(self, parent_chunks: Iterable[Dict[str, Any]]) -> None:
        """加载 ParentChunkStore。

        参数:
            parent_chunks: 父块 chunks，具体约束请结合类型标注和调用方确认。

        返回:
            None

        阅读提示:
            主要直接调用：parent.get, str, ValueError。
        """
        for parent in parent_chunks:
            parent_id = parent.get("parent_chunk_id")
            if not parent_id:
                continue
            parent_id = str(parent_id)
            if parent_id in self._parents:
                raise ValueError(f"Duplicate parent_chunk_id: {parent_id}")
            self._parents[parent_id] = parent

    # 阅读注释（函数）：获取 ParentChunkStore。
    def get(self, parent_chunk_id: str, default: Optional[Dict[str, Any]] = None) -> Optional[Dict[str, Any]]:
        """获取 ParentChunkStore。

        参数:
            parent_chunk_id: 父块 文本块 标识，具体约束请结合类型标注和调用方确认。
            default: default，具体约束请结合类型标注和调用方确认。

        返回:
            Optional[Dict[str, Any]]

        阅读提示:
            主要直接调用：self._parents.get, str。
        """
        if not parent_chunk_id:
            return default
        return self._parents.get(str(parent_chunk_id), default)

    # 阅读注释（函数）：处理 must get 相关逻辑。
    def must_get(self, parent_chunk_id: str) -> Dict[str, Any]:
        """处理 must get 相关逻辑。

        参数:
            parent_chunk_id: 父块 文本块 标识，具体约束请结合类型标注和调用方确认。

        返回:
            Dict[str, Any]

        阅读提示:
            主要直接调用：self.get, KeyError。
        """
        parent = self.get(parent_chunk_id)
        if parent is None:
            raise KeyError(f"parent_chunk_id not found: {parent_chunk_id}")
        return parent

    # 阅读注释（函数）：判断是否存在 ParentChunkStore。
    def has(self, parent_chunk_id: str) -> bool:
        """判断是否存在 ParentChunkStore。

        参数:
            parent_chunk_id: 父块 文本块 标识，具体约束请结合类型标注和调用方确认。

        返回:
            bool

        阅读提示:
            主要直接调用：bool, str。
        """
        return bool(parent_chunk_id) and str(parent_chunk_id) in self._parents

    # 阅读注释（函数）：处理 标识集合 相关逻辑。
    def ids(self) -> List[str]:
        """处理 标识集合 相关逻辑。

        返回:
            List[str]

        阅读提示:
            主要直接调用：list, self._parents.keys。
        """
        return list(self._parents.keys())

    # 阅读注释（函数）：处理 values 相关逻辑。
    def values(self) -> List[Dict[str, Any]]:
        """处理 values 相关逻辑。

        返回:
            List[Dict[str, Any]]

        阅读提示:
            主要直接调用：list, self._parents.values。
        """
        return list(self._parents.values())

    # 阅读注释（函数）：处理 len 相关逻辑。
    def __len__(self) -> int:
        """处理 len 相关逻辑。

        返回:
            int

        阅读提示:
            主要直接调用：len。
        """
        return len(self._parents)
