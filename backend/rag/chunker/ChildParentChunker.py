# -*- coding: utf-8 -*-
# =============================================================================
# 中文阅读说明：RAG 核心模块，负责查询变换、召回、融合、重排、证据评估和上下文组装。
# 主要定义：ParentChildChunkResult、ChildParentChunker。建议先从公开入口函数开始，再沿调用关系向下阅读。
# =============================================================================
"""
rag_template/chunker/ChildParentChunker.py
=========================================

cleaned_text_unit_v1 -> parent_chunk_v1 + child_chunk_v1 的父子块策略层。

职责边界：
1. 输入 cleaned_text_unit_v1 的 dict / Spark Row 列表。
2. 按 doc_id + unit_order 顺序生成 parent_chunk_v1。
3. 对每个 parent_chunk_v1 再生成 child_chunk_v1。
4. 回填 parent.child_chunk_ids / parent.child_count。
5. 不负责 embedding、不负责 Milvus、不负责文件读写。

核心策略：
- parent chunk：大块，保留完整上下文。
- child chunk：小块，用于向量检索。
- 后续入库时只 embedding / index child，检索命中 child 后用 parent_chunk_id 回填 parent_text。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, Iterable, List, Optional, Tuple

from rag.configs import RAGConfig
from rag.configs.SchemaConfig import (
    DEFAULT_CHILD_CHUNK_STRATEGY,
    DEFAULT_CHILD_CHUNK_VERSION,
    DEFAULT_CLEANING_VERSION,
    DEFAULT_PARENT_CHUNK_STRATEGY,
    DEFAULT_PARENT_CHUNK_VERSION,
    DEFAULT_SOURCE_TYPE,
)
from rag.schema.Chunk_Schema import build_child_chunk_v1, build_parent_chunk_v1
from rag.chunker.cleaned_text_unit_chunker import row_to_unit, should_skip_unit
from rag.util.text_utils import safe_int, split_text_by_fixed_size, unique_keep_order
from rag.util.token_utils import get_default_token_counter, get_token_counter


# 阅读注释（类）：封装 父块 子块 文本块 结果，集中封装相关状态、依赖和行为。
@dataclass
class ParentChildChunkResult:
    """父子块切分结果。"""

    parents: List[Dict[str, Any]]
    children: List[Dict[str, Any]]


# 阅读注释（类）：封装 子块 父块 chunker，集中封装相关状态、依赖和行为。
class ChildParentChunker:
    """父子块切分器。

    第一版实现固定窗口 small-to-big 策略：
    1. 先把 cleaned_text_unit 按顺序聚合成 parent。
    2. 再把 parent.text 切成 child。

    参数默认读取 RAGConfig：
    - PARENT_CHUNK_SIZE / PARENT_CHUNK_OVERLAP
    - CHILD_CHUNK_SIZE / CHILD_CHUNK_OVERLAP
    - PARENT_CHILD_CHUNK_UNIT
    """

    # 阅读注释（函数）：初始化 ChildParentChunker，保存运行所需的依赖、配置或状态。
    def __init__(
        self,
        parent_chunk_size: Optional[int] = None,
        parent_chunk_overlap: Optional[int] = None,
        child_chunk_size: Optional[int] = None,
        child_chunk_overlap: Optional[int] = None,
        unit: Optional[str] = None,
        parent_chunk_strategy: Optional[str] = None,
        child_chunk_strategy: Optional[str] = None,
        parent_chunk_version: Optional[str] = None,
        child_chunk_version: Optional[str] = None,
        chunker_name: Optional[str] = None,
        tokenizer_model_name: Optional[str] = None,
        tokenizer_local_files_only: bool = True,
    ) -> None:
        """初始化 ChildParentChunker，保存运行所需的依赖、配置或状态。

        参数:
            parent_chunk_size: 父块 文本块 size，具体约束请结合类型标注和调用方确认。
            parent_chunk_overlap: 父块 文本块 overlap，具体约束请结合类型标注和调用方确认。
            child_chunk_size: 子块 文本块 size，具体约束请结合类型标注和调用方确认。
            child_chunk_overlap: 子块 文本块 overlap，具体约束请结合类型标注和调用方确认。
            unit: unit，具体约束请结合类型标注和调用方确认。
            parent_chunk_strategy: 父块 文本块 strategy，具体约束请结合类型标注和调用方确认。
            child_chunk_strategy: 子块 文本块 strategy，具体约束请结合类型标注和调用方确认。
            parent_chunk_version: 父块 文本块 版本，具体约束请结合类型标注和调用方确认。
            child_chunk_version: 子块 文本块 版本，具体约束请结合类型标注和调用方确认。
            chunker_name: chunker 名称，具体约束请结合类型标注和调用方确认。
            tokenizer_model_name: tokenizer 模型 名称，具体约束请结合类型标注和调用方确认。
            tokenizer_local_files_only: tokenizer 本地 files only，具体约束请结合类型标注和调用方确认。

        返回:
            None

        阅读提示:
            主要直接调用：int, getattr, lower, str, self._validate_config, get_token_counter, bool, get_default_token_counter。
        """
        self.parent_chunk_size = int(
            parent_chunk_size
            if parent_chunk_size is not None
            else getattr(RAGConfig, "PARENT_CHUNK_SIZE", 1500)
        )
        self.parent_chunk_overlap = int(
            parent_chunk_overlap
            if parent_chunk_overlap is not None
            else getattr(RAGConfig, "PARENT_CHUNK_OVERLAP", 150)
        )
        self.child_chunk_size = int(
            child_chunk_size
            if child_chunk_size is not None
            else getattr(RAGConfig, "CHILD_CHUNK_SIZE", getattr(RAGConfig, "CHUNK_SIZE", 500))
        )
        self.child_chunk_overlap = int(
            child_chunk_overlap
            if child_chunk_overlap is not None
            else getattr(RAGConfig, "CHILD_CHUNK_OVERLAP", getattr(RAGConfig, "CHUNK_OVERLAP", 50))
        )
        self.unit = str(
            unit
            if unit is not None
            else getattr(RAGConfig, "PARENT_CHILD_CHUNK_UNIT", getattr(RAGConfig, "CHUNK_UNIT", "char"))
        ).lower()
        self.parent_chunk_strategy = (
            parent_chunk_strategy
            or getattr(RAGConfig, "PARENT_CHUNK_STRATEGY", DEFAULT_PARENT_CHUNK_STRATEGY)
        )
        self.child_chunk_strategy = (
            child_chunk_strategy
            or getattr(RAGConfig, "CHILD_CHUNK_STRATEGY", DEFAULT_CHILD_CHUNK_STRATEGY)
        )
        self.parent_chunk_version = (
            parent_chunk_version
            or getattr(RAGConfig, "PARENT_CHUNK_VERSION", DEFAULT_PARENT_CHUNK_VERSION)
        )
        self.child_chunk_version = (
            child_chunk_version
            or getattr(RAGConfig, "CHILD_CHUNK_VERSION", DEFAULT_CHILD_CHUNK_VERSION)
        )
        self.chunker_name = (
            chunker_name
            or getattr(RAGConfig, "PARENT_CHILD_CHUNKER_NAME", "fixed_parent_child_chunker_v1")
        )

        self._validate_config()
        if self.unit == "token":
            self.token_counter = (
                get_token_counter(
                    tokenizer_name=str(tokenizer_model_name),
                    local_files_only=bool(tokenizer_local_files_only),
                )
                if tokenizer_model_name
                else get_default_token_counter()
            )
        else:
            self.token_counter = None

    # =====================================================
    # public APIs
    # =====================================================

    # 阅读注释（函数）：处理 文本块 记录集合 for doc 相关逻辑。
    def chunk_records_for_doc(self, records: List[Dict[str, Any]]) -> ParentChildChunkResult:
        """输入一个 doc 的 cleaned_text_unit_v1 records，输出父子块。"""
        units = [row_to_unit(r) for r in records]
        units = self._prepare_units(units)

        if not units:
            return ParentChildChunkResult(parents=[], children=[])

        doc_id = units[0].get("doc_id")
        if not doc_id:
            return ParentChildChunkResult(parents=[], children=[])

        return self.chunk_units_for_doc(doc_id=doc_id, units=units)

    # 阅读注释（函数）：处理 文本块 记录集合 相关逻辑。
    def chunk_records(self, records: Iterable[Dict[str, Any]]) -> ParentChildChunkResult:
        """输入多个 doc 的 cleaned_text_unit_v1 records，按 doc_id 分组后输出父子块。"""
        groups: Dict[str, List[Dict[str, Any]]] = {}
        for row in records:
            unit = row_to_unit(row)
            doc_id = unit.get("doc_id")
            if not doc_id:
                continue
            groups.setdefault(doc_id, []).append(unit)

        all_parents: List[Dict[str, Any]] = []
        all_children: List[Dict[str, Any]] = []

        for doc_id in sorted(groups.keys()):
            result = self.chunk_units_for_doc(doc_id=doc_id, units=groups[doc_id])
            all_parents.extend(result.parents)
            all_children.extend(result.children)

        return ParentChildChunkResult(parents=all_parents, children=all_children)

    # 阅读注释（函数）：处理 文本块 units for doc 相关逻辑。
    def chunk_units_for_doc(self, doc_id: str, units: List[Dict[str, Any]]) -> ParentChildChunkResult:
        """输入单个 doc 的内部 unit 列表，输出 parent_chunk_v1 / child_chunk_v1。"""
        units = self._prepare_units(units)
        if not units:
            return ParentChildChunkResult(parents=[], children=[])

        parent_unit_groups = self._build_parent_unit_groups(units)

        parents: List[Dict[str, Any]] = []
        children: List[Dict[str, Any]] = []
        global_child_index = 1

        for parent_index, parent_units in enumerate(parent_unit_groups, start=1):
            parent_text, source_unit_spans = self._join_units_with_spans(parent_units)
            if not parent_text:
                continue

            parent_chunk_id = f"{doc_id}_parent_{parent_index:06d}"
            parent_source_unit_ids = unique_keep_order([u.get("unit_id") for u in parent_units])
            cleaning_versions = unique_keep_order([u.get("cleaning_version") for u in parent_units])

            parent_extra = self._build_common_extra(
                units=parent_units,
                text=parent_text,
                min_chunk_size=self.parent_chunk_size,
            )
            parent_extra.update(
                {
                    "chunker_name": self.chunker_name,
                    "length_unit": self.unit,
                    "source_unit_spans": source_unit_spans,
                    "parent_chunk_size": self.parent_chunk_size,
                    "parent_chunk_overlap": self.parent_chunk_overlap,
                    "child_chunk_size": self.child_chunk_size,
                    "child_chunk_overlap": self.child_chunk_overlap,
                }
            )

            child_records, global_child_index = self._build_children_for_parent(
                doc_id=doc_id,
                parent_chunk_id=parent_chunk_id,
                parent_text=parent_text,
                parent_units=parent_units,
                source_unit_spans=source_unit_spans,
                start_global_child_index=global_child_index,
            )

            child_chunk_ids = [c["child_chunk_id"] for c in child_records]

            parent = build_parent_chunk_v1(
                parent_chunk_id=parent_chunk_id,
                doc_id=doc_id,
                source_type=self._first_non_empty(parent_units, "source_type", DEFAULT_SOURCE_TYPE),
                text=parent_text,
                source_unit_ids=parent_source_unit_ids,
                child_chunk_ids=child_chunk_ids,
                child_count=len(child_chunk_ids),
                title=self._last_non_empty(parent_units, "title"),
                section=self._last_non_empty(parent_units, "section"),
                section_level=self._last_non_empty(parent_units, "section_level"),
                page_start=self._min_int(parent_units, "page_start"),
                page_end=self._max_int(parent_units, "page_end"),
                parent_chunk_index=parent_index,
                parent_chunk_strategy=self.parent_chunk_strategy,
                cleaning_version=cleaning_versions[-1] if cleaning_versions else DEFAULT_CLEANING_VERSION,
                parent_chunk_version=self.parent_chunk_version,
                token_count=self._measure(parent_text),
                extra=parent_extra,
            )

            parents.append(parent)
            children.extend(child_records)

        return ParentChildChunkResult(parents=parents, children=children)

    # =====================================================
    # parent construction
    # =====================================================

    # 阅读注释（函数）：准备 units。
    def _prepare_units(self, units: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """排序、过滤、长 unit 兜底拆分。"""
        prepared = [u for u in units if not should_skip_unit(u)]
        prepared = sorted(prepared, key=lambda u: safe_int(u.get("unit_order"), 0) or 0)
        prepared = self._expand_long_units(prepared, chunk_size=self.parent_chunk_size, overlap=self.parent_chunk_overlap)
        return prepared

    # 阅读注释（函数）：构建 父块 unit groups。
    def _build_parent_unit_groups(self, units: List[Dict[str, Any]]) -> List[List[Dict[str, Any]]]:
        """按 parent_chunk_size 聚合 unit，得到 parent 的 unit 组。"""
        groups: List[List[Dict[str, Any]]] = []
        current: List[Dict[str, Any]] = []
        current_len = 0

        for unit in units:
            text = unit.get("text") or ""
            unit_len = self._measure(text)
            sep_len = self._measure("\n") if current else 0

            if current and current_len + sep_len + unit_len > self.parent_chunk_size:
                groups.append(current)
                current = self._overlap_tail(current, self.parent_chunk_overlap)
                current_len = self._measure("\n".join([u.get("text") or "" for u in current])) if current else 0

            current.append(unit)
            current_len += sep_len + unit_len

        if current:
            groups.append(current)

        return groups

    # 阅读注释（函数）：处理 join units with spans 相关逻辑。
    def _join_units_with_spans(self, units: List[Dict[str, Any]]) -> Tuple[str, List[Dict[str, Any]]]:
        """把 parent 内 units 拼成文本，同时记录每个 unit 在 parent_text 中的 char span。"""
        pieces: List[str] = []
        spans: List[Dict[str, Any]] = []
        cursor = 0

        for unit in units:
            text = unit.get("text") or ""
            if not text:
                continue

            if pieces:
                pieces.append("\n")
                cursor += 1

            start = cursor
            pieces.append(text)
            cursor += len(text)
            end = cursor

            spans.append(
                {
                    "unit_id": unit.get("unit_id"),
                    "char_start": start,
                    "char_end": end,
                    "page_start": unit.get("page_start"),
                    "page_end": unit.get("page_end"),
                    "section": unit.get("section"),
                }
            )

        return "".join(pieces).strip(), spans

    # =====================================================
    # child construction
    # =====================================================

    # 阅读注释（函数）：构建 children for 父块。
    def _build_children_for_parent(
        self,
        *,
        doc_id: str,
        parent_chunk_id: str,
        parent_text: str,
        parent_units: List[Dict[str, Any]],
        source_unit_spans: List[Dict[str, Any]],
        start_global_child_index: int,
    ) -> Tuple[List[Dict[str, Any]], int]:
        """构建 children for 父块。

        参数:
            doc_id: doc 标识，具体约束请结合类型标注和调用方确认。
            parent_chunk_id: 父块 文本块 标识，具体约束请结合类型标注和调用方确认。
            parent_text: 父块 文本，具体约束请结合类型标注和调用方确认。
            parent_units: 父块 units，具体约束请结合类型标注和调用方确认。
            source_unit_spans: source unit spans，具体约束请结合类型标注和调用方确认。
            start_global_child_index: start global 子块 索引，具体约束请结合类型标注和调用方确认。

        返回:
            Tuple[List[Dict[str, Any]], int]

        阅读提示:
            主要直接调用：self._split_text_windows_with_offsets, unique_keep_order, u.get, enumerate, self._unit_ids_for_span, set, self._build_common_extra, child_extra.update。
        """
        windows = self._split_text_windows_with_offsets(
            parent_text,
            chunk_size=self.child_chunk_size,
            overlap=self.child_chunk_overlap,
        )

        children: List[Dict[str, Any]] = []
        global_child_index = start_global_child_index
        cleaning_versions = unique_keep_order([u.get("cleaning_version") for u in parent_units])

        for child_index_in_parent, (child_text, start, end) in enumerate(windows, start=1):
            if not child_text:
                continue

            child_chunk_id = f"{parent_chunk_id}_child_{child_index_in_parent:04d}"
            overlapped_unit_ids = self._unit_ids_for_span(source_unit_spans, start, end)
            child_units = [u for u in parent_units if u.get("unit_id") in set(overlapped_unit_ids)]
            if not child_units:
                child_units = parent_units

            child_extra = self._build_common_extra(
                units=child_units,
                text=child_text,
                min_chunk_size=self.child_chunk_size,
            )
            child_extra.update(
                {
                    "chunker_name": self.chunker_name,
                    "length_unit": self.unit,
                    "parent_chunk_size": self.parent_chunk_size,
                    "parent_chunk_overlap": self.parent_chunk_overlap,
                    "child_chunk_size": self.child_chunk_size,
                    "child_chunk_overlap": self.child_chunk_overlap,
                    "source_unit_spans_in_parent": [
                        s for s in source_unit_spans if s.get("unit_id") in set(overlapped_unit_ids)
                    ],
                }
            )

            child = build_child_chunk_v1(
                child_chunk_id=child_chunk_id,
                parent_chunk_id=parent_chunk_id,
                doc_id=doc_id,
                source_type=self._first_non_empty(parent_units, "source_type", DEFAULT_SOURCE_TYPE),
                text=child_text,
                source_unit_ids=overlapped_unit_ids,
                title=self._last_non_empty(child_units, "title"),
                section=self._last_non_empty(child_units, "section"),
                section_level=self._last_non_empty(child_units, "section_level"),
                page_start=self._min_int(child_units, "page_start"),
                page_end=self._max_int(child_units, "page_end"),
                child_chunk_index=global_child_index,
                child_index_in_parent=child_index_in_parent,
                child_chunk_strategy=self.child_chunk_strategy,
                char_start_in_parent=start,
                char_end_in_parent=end,
                cleaning_version=cleaning_versions[-1] if cleaning_versions else DEFAULT_CLEANING_VERSION,
                parent_chunk_version=self.parent_chunk_version,
                child_chunk_version=self.child_chunk_version,
                token_count=self._measure(child_text),
                extra=child_extra,
            )
            children.append(child)
            global_child_index += 1

        return children, global_child_index

    # =====================================================
    # text splitting / length calculation
    # =====================================================

    # 阅读注释（函数）：处理 measure 相关逻辑。
    def _measure(self, text: str) -> int:
        """处理 measure 相关逻辑。

        参数:
            text: 待处理文本。

        返回:
            int

        阅读提示:
            主要直接调用：self.token_counter.count, len。
        """
        if not text:
            return 0
        if self.unit == "token" and self.token_counter is not None:
            return self.token_counter.count(text)
        return len(text)

    # 阅读注释（函数）：处理 split 文本 windows with offsets 相关逻辑。
    def _split_text_windows_with_offsets(self, text: str, chunk_size: int, overlap: int) -> List[Tuple[str, int, int]]:
        """返回 [(chunk_text, char_start, char_end)]。

        - unit=token 且 tokenizer 支持 offset_mapping 时：按 token 窗口切，并返回真实 char offset。
        - 其他情况：按字符窗口切。
        """
        text = text or ""
        if not text.strip():
            return []

        if self.unit == "token" and self.token_counter is not None:
            tokenizer = getattr(self.token_counter, "tokenizer", None)
            if tokenizer is not None:
                try:
                    encoded = tokenizer(
                        text,
                        add_special_tokens=False,
                        return_offsets_mapping=True,
                    )
                    input_ids = encoded.get("input_ids") or []
                    offsets = encoded.get("offset_mapping") or []
                    if input_ids and offsets and len(input_ids) == len(offsets):
                        windows: List[Tuple[str, int, int]] = []
                        step = chunk_size - overlap
                        token_start = 0
                        while token_start < len(input_ids):
                            token_end = min(token_start + chunk_size, len(input_ids))
                            char_start = int(offsets[token_start][0])
                            char_end = int(offsets[token_end - 1][1])
                            piece = text[char_start:char_end].strip()
                            if piece:
                                # strip 后 char offset 仍保留原窗口边界，便于追溯。
                                windows.append((piece, char_start, char_end))
                            if token_end >= len(input_ids):
                                break
                            token_start += step
                        return windows
                except Exception:
                    pass

        return self._split_char_windows_with_offsets(text, chunk_size=chunk_size, overlap=overlap)

    # 阅读注释（函数）：处理 split char windows with offsets 相关逻辑。
    @staticmethod
    def _split_char_windows_with_offsets(text: str, chunk_size: int, overlap: int) -> List[Tuple[str, int, int]]:
        """处理 split char windows with offsets 相关逻辑。

        参数:
            text: 待处理文本。
            chunk_size: 文本块 size，具体约束请结合类型标注和调用方确认。
            overlap: overlap，具体约束请结合类型标注和调用方确认。

        返回:
            List[Tuple[str, int, int]]

        阅读提示:
            主要直接调用：ValueError, len, min, strip, windows.append。
        """
        if chunk_size <= 0:
            raise ValueError("chunk_size must be positive")
        if overlap < 0:
            raise ValueError("overlap must be non-negative")
        if overlap >= chunk_size:
            raise ValueError("overlap must be smaller than chunk_size")

        windows: List[Tuple[str, int, int]] = []
        step = chunk_size - overlap
        start = 0
        text_length = len(text)

        while start < text_length:
            end = min(start + chunk_size, text_length)
            piece = text[start:end].strip()
            if piece:
                windows.append((piece, start, end))
            if end >= text_length:
                break
            start += step

        return windows

    # 阅读注释（函数）：处理 expand long units 相关逻辑。
    def _expand_long_units(self, units: List[Dict[str, Any]], chunk_size: int, overlap: int) -> List[Dict[str, Any]]:
        """单个 unit 超过 parent_chunk_size 时，先拆成多个 pseudo unit。"""
        expanded: List[Dict[str, Any]] = []

        for unit in units:
            text = unit.get("text") or ""
            if self._measure(text) <= chunk_size:
                expanded.append(unit)
                continue

            # token 模式下优先按 token offset 切，否则回退字符窗口。
            windows = self._split_text_windows_with_offsets(text, chunk_size=chunk_size, overlap=overlap)
            if not windows:
                for piece in split_text_by_fixed_size(text, chunk_size, overlap):
                    windows.append((piece, 0, len(piece)))

            for idx, (piece, start, end) in enumerate(windows, start=1):
                new_unit = dict(unit)
                new_unit["text"] = piece
                new_unit["text_length"] = len(piece)
                new_unit["unit_id"] = f"{unit.get('unit_id')}_part_{idx:04d}"

                flags = list(new_unit.get("quality_flags") or [])
                if "split_from_long_unit" not in flags:
                    flags.append("split_from_long_unit")
                new_unit["quality_flags"] = flags

                extra = dict(new_unit.get("extra") or {})
                extra.update(
                    {
                        "source_unit_id_before_split": unit.get("unit_id"),
                        "char_start_in_source_unit": start,
                        "char_end_in_source_unit": end,
                    }
                )
                new_unit["extra"] = extra
                expanded.append(new_unit)

        return expanded

    # 阅读注释（函数）：处理 overlap tail 相关逻辑。
    def _overlap_tail(self, units: List[Dict[str, Any]], overlap: int) -> List[Dict[str, Any]]:
        """处理 overlap tail 相关逻辑。

        参数:
            units: units，具体约束请结合类型标注和调用方确认。
            overlap: overlap，具体约束请结合类型标注和调用方确认。

        返回:
            List[Dict[str, Any]]

        阅读提示:
            主要直接调用：reversed, tail.insert, self._measure, unit.get。
        """
        if overlap <= 0:
            return []

        tail: List[Dict[str, Any]] = []
        total = 0
        for unit in reversed(units):
            tail.insert(0, unit)
            total += self._measure(unit.get("text") or "")
            if total >= overlap:
                break
        return tail

    # =====================================================
    # metadata helpers
    # =====================================================

    # 阅读注释（函数）：构建 common extra。
    def _build_common_extra(self, *, units: List[Dict[str, Any]], text: str, min_chunk_size: int) -> Dict[str, Any]:
        """构建 common extra。

        参数:
            units: units，具体约束请结合类型标注和调用方确认。
            text: 待处理文本。
            min_chunk_size: min 文本块 size，具体约束请结合类型标注和调用方确认。

        返回:
            Dict[str, Any]

        阅读提示:
            主要直接调用：quality_flags.extend, unit.get, unique_keep_order, self._measure, quality_flags.append, u.get, round, sum。
        """
        quality_flags: List[Any] = []
        for unit in units:
            quality_flags.extend(unit.get("quality_flags") or [])
        quality_flags = unique_keep_order(quality_flags)
        if self._measure(text) < min_chunk_size and "short_chunk" not in quality_flags:
            quality_flags.append("short_chunk")

        unit_types = unique_keep_order([u.get("unit_type") for u in units])
        scores = [u.get("quality_score") for u in units if u.get("quality_score") is not None]
        quality_score = round(sum(scores) / float(len(scores)), 4) if scores else None

        languages: Dict[str, int] = {}
        for unit in units:
            lang = unit.get("language") or "unknown"
            languages[lang] = languages.get(lang, 0) + 1
        language = sorted(languages.items(), key=lambda x: x[1], reverse=True)[0][0] if languages else "unknown"

        if set(unit_types) == {"table"}:
            chunk_type = "table"
        elif "table" in unit_types:
            chunk_type = "mixed"
        else:
            chunk_type = "text"

        return {
            "source_uri": self._first_non_empty(units, "source_uri"),
            "source_name": self._first_non_empty(units, "source_name"),
            "source_format": self._first_non_empty(units, "source_format"),
            "batch_id": self._first_non_empty(units, "batch_id"),
            "chunk_type": chunk_type,
            "language": language,
            "quality_score": quality_score,
            "quality_flags": quality_flags,
            "source_unit_count": len(unique_keep_order([u.get("unit_id") for u in units])),
            "source_unit_types": unit_types,
            "cleaning_versions": unique_keep_order([u.get("cleaning_version") for u in units]),
            "tokenizer_backend": getattr(self.token_counter, "backend", None) if self.token_counter else None,
        }

    # 阅读注释（函数）：处理 unit 标识集合 for span 相关逻辑。
    @staticmethod
    def _unit_ids_for_span(source_unit_spans: List[Dict[str, Any]], start: int, end: int) -> List[Any]:
        """处理 unit 标识集合 for span 相关逻辑。

        参数:
            source_unit_spans: source unit spans，具体约束请结合类型标注和调用方确认。
            start: start，具体约束请结合类型标注和调用方确认。
            end: end，具体约束请结合类型标注和调用方确认。

        返回:
            List[Any]

        阅读提示:
            主要直接调用：span.get, int, unit_ids.append, unique_keep_order。
        """
        unit_ids = []
        for span in source_unit_spans:
            s = span.get("char_start")
            e = span.get("char_end")
            if s is None or e is None:
                continue
            # overlap 判断：[s, e) 与 [start, end) 有交集。
            if int(s) < end and int(e) > start:
                unit_ids.append(span.get("unit_id"))
        return unique_keep_order(unit_ids)

    # 阅读注释（函数）：处理 first non empty 相关逻辑。
    @staticmethod
    def _first_non_empty(units: List[Dict[str, Any]], key: str, default: Any = None) -> Any:
        """处理 first non empty 相关逻辑。

        参数:
            units: units，具体约束请结合类型标注和调用方确认。
            key: key，具体约束请结合类型标注和调用方确认。
            default: default，具体约束请结合类型标注和调用方确认。

        返回:
            Any

        阅读提示:
            主要直接调用：unit.get。
        """
        for unit in units:
            value = unit.get(key)
            if value not in (None, ""):
                return value
        return default

    # 阅读注释（函数）：处理 last non empty 相关逻辑。
    @staticmethod
    def _last_non_empty(units: List[Dict[str, Any]], key: str, default: Any = None) -> Any:
        """处理 last non empty 相关逻辑。

        参数:
            units: units，具体约束请结合类型标注和调用方确认。
            key: key，具体约束请结合类型标注和调用方确认。
            default: default，具体约束请结合类型标注和调用方确认。

        返回:
            Any

        阅读提示:
            主要直接调用：reversed, unit.get。
        """
        for unit in reversed(units):
            value = unit.get(key)
            if value not in (None, ""):
                return value
        return default

    # 阅读注释（函数）：处理 min int 相关逻辑。
    @staticmethod
    def _min_int(units: List[Dict[str, Any]], key: str) -> Optional[int]:
        """处理 min int 相关逻辑。

        参数:
            units: units，具体约束请结合类型标注和调用方确认。
            key: key，具体约束请结合类型标注和调用方确认。

        返回:
            Optional[int]

        阅读提示:
            主要直接调用：safe_int, u.get, min。
        """
        values = [safe_int(u.get(key), None) for u in units]
        values = [v for v in values if v is not None]
        return min(values) if values else None

    # 阅读注释（函数）：处理 max int 相关逻辑。
    @staticmethod
    def _max_int(units: List[Dict[str, Any]], key: str) -> Optional[int]:
        """处理 max int 相关逻辑。

        参数:
            units: units，具体约束请结合类型标注和调用方确认。
            key: key，具体约束请结合类型标注和调用方确认。

        返回:
            Optional[int]

        阅读提示:
            主要直接调用：safe_int, u.get, max。
        """
        values = [safe_int(u.get(key), None) for u in units]
        values = [v for v in values if v is not None]
        return max(values) if values else None

    # 阅读注释（函数）：校验 配置。
    def _validate_config(self) -> None:
        """校验 配置。

        返回:
            None

        阅读提示:
            主要直接调用：ValueError。
        """
        if self.parent_chunk_size <= 0:
            raise ValueError("parent_chunk_size must be positive")
        if self.child_chunk_size <= 0:
            raise ValueError("child_chunk_size must be positive")
        if self.parent_chunk_overlap < 0:
            raise ValueError("parent_chunk_overlap must be non-negative")
        if self.child_chunk_overlap < 0:
            raise ValueError("child_chunk_overlap must be non-negative")
        if self.parent_chunk_overlap >= self.parent_chunk_size:
            raise ValueError("parent_chunk_overlap must be smaller than parent_chunk_size")
        if self.child_chunk_overlap >= self.child_chunk_size:
            raise ValueError("child_chunk_overlap must be smaller than child_chunk_size")
        if self.parent_chunk_size < self.child_chunk_size:
            raise ValueError("parent_chunk_size should be >= child_chunk_size")
