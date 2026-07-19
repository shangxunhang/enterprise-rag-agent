# =============================================================================
# 中文阅读说明：RAG 核心模块，负责查询变换、召回、融合、重排、证据评估和上下文组装。
# 主要定义：_safe_int、_first、_last、_bounds、_unit_extra、FixedParentChildChunkerPlugin、StructuredParentChildChunkerPlugin、RecursiveParentChildChunkerPlugin、HeadingParentChildChunkerPlugin、ParagraphParentChildChunkerPlugin等。建议先从公开入口函数开始，再沿调用关系向下阅读。
# =============================================================================
"""Configuration-driven parent/child chunker plugins.

The online RAG stack consumes parent_chunk_v1 and child_chunk_v1.  This module
adapts several parent splitting strategies to that stable contract while child
chunks remain small fixed windows suitable for dense indexing.
"""
from __future__ import annotations

from collections import defaultdict
from typing import Any, Iterable, Sequence

from rag.chunker.ChildParentChunker import ChildParentChunker, ParentChildChunkResult
from rag.chunker.FixedSizeChunker import FixedSizeChunker
from rag.chunker.HeadingChunker import HeadingChunker
from rag.chunker.ParagraphChunker import ParagraphChunker
from rag.chunker.RecursiveChunker import RecursiveChunker
from rag.chunker.cleaned_text_unit_chunker import row_to_unit, should_skip_unit
from rag.configs.SchemaConfig import (
    DEFAULT_CHILD_CHUNK_VERSION,
    DEFAULT_CLEANING_VERSION,
    DEFAULT_PARENT_CHUNK_VERSION,
    DEFAULT_SOURCE_TYPE,
)
from rag.schema.Chunk_Schema import build_child_chunk_v1, build_parent_chunk_v1
from rag.util.text_utils import unique_keep_order


# 阅读注释（函数）：处理 safe int 相关逻辑。
def _safe_int(value: Any, default: int | None = None) -> int | None:
    """处理 safe int 相关逻辑。

    参数:
        value: value，具体约束请结合类型标注和调用方确认。
        default: default，具体约束请结合类型标注和调用方确认。

    返回:
        int | None

    阅读提示:
        主要直接调用：int。
    """
    try:
        return int(value) if value not in (None, "") else default
    except Exception:
        return default


# 阅读注释（函数）：处理 first 相关逻辑。
def _first(items: Sequence[dict[str, Any]], key: str, default: Any = None) -> Any:
    """处理 first 相关逻辑。

    参数:
        items: 待处理的数据项集合。
        key: key，具体约束请结合类型标注和调用方确认。
        default: default，具体约束请结合类型标注和调用方确认。

    返回:
        Any

    阅读提示:
        主要直接调用：item.get。
    """
    for item in items:
        value = item.get(key)
        if value not in (None, ""):
            return value
    return default


# 阅读注释（函数）：处理 last 相关逻辑。
def _last(items: Sequence[dict[str, Any]], key: str, default: Any = None) -> Any:
    """处理 last 相关逻辑。

    参数:
        items: 待处理的数据项集合。
        key: key，具体约束请结合类型标注和调用方确认。
        default: default，具体约束请结合类型标注和调用方确认。

    返回:
        Any

    阅读提示:
        主要直接调用：reversed, item.get。
    """
    for item in reversed(items):
        value = item.get(key)
        if value not in (None, ""):
            return value
    return default


# 阅读注释（函数）：处理 bounds 相关逻辑。
def _bounds(items: Sequence[dict[str, Any]], key: str, *, maximum: bool) -> int | None:
    """处理 bounds 相关逻辑。

    参数:
        items: 待处理的数据项集合。
        key: key，具体约束请结合类型标注和调用方确认。
        maximum: maximum，具体约束请结合类型标注和调用方确认。

    返回:
        int | None

    阅读提示:
        主要直接调用：_safe_int, item.get, max, min。
    """
    values = [_safe_int(item.get(key)) for item in items]
    values = [value for value in values if value is not None]
    if not values:
        return None
    return max(values) if maximum else min(values)


# 阅读注释（函数）：处理 unit extra 相关逻辑。
def _unit_extra(units: Sequence[dict[str, Any]], *, text: str, chunker_name: str) -> dict[str, Any]:
    """处理 unit extra 相关逻辑。

    参数:
        units: units，具体约束请结合类型标注和调用方确认。
        text: 待处理文本。
        chunker_name: chunker 名称，具体约束请结合类型标注和调用方确认。

    返回:
        dict[str, Any]

    阅读提示:
        主要直接调用：flags.extend, unit.get, _first, round, sum, len, unique_keep_order, u.get。
    """
    flags: list[Any] = []
    for unit in units:
        flags.extend(unit.get("quality_flags") or [])
    scores = [unit.get("quality_score") for unit in units if unit.get("quality_score") is not None]
    return {
        "chunker_name": chunker_name,
        "source_uri": _first(units, "source_uri"),
        "source_name": _first(units, "source_name"),
        "source_format": _first(units, "source_format"),
        "batch_id": _first(units, "batch_id"),
        "language": _first(units, "language", "unknown"),
        "quality_score": round(sum(scores) / len(scores), 4) if scores else None,
        "quality_flags": unique_keep_order(flags),
        "source_unit_count": len(unique_keep_order([u.get("unit_id") for u in units])),
        "source_unit_types": unique_keep_order([u.get("unit_type") for u in units]),
        "text_length": len(text),
    }


# 阅读注释（类）：封装 fixed 父块 子块 chunker 插件，作为可配置插件接入 RAG 或 Agent 主链。
class FixedParentChildChunkerPlugin:
    """Registry adapter over the existing production parent/child chunker."""

    # 阅读注释（函数）：初始化 FixedParentChildChunkerPlugin，保存运行所需的依赖、配置或状态。
    def __init__(
        self,
        *,
        parent_chunk_size: int = 1500,
        parent_chunk_overlap: int = 150,
        child_chunk_size: int = 500,
        child_chunk_overlap: int = 50,
        unit: str = "char",
        parent_chunk_version: str = DEFAULT_PARENT_CHUNK_VERSION,
        child_chunk_version: str = DEFAULT_CHILD_CHUNK_VERSION,
        deterministic_created_at: str | None = None,
        chunker_name: str = "fixed_parent_child",
        tokenizer_model_name: str | None = None,
        tokenizer_local_files_only: bool = True,
        build_context: Any = None,
    ) -> None:
        """初始化 FixedParentChildChunkerPlugin，保存运行所需的依赖、配置或状态。

        参数:
            parent_chunk_size: 父块 文本块 size，具体约束请结合类型标注和调用方确认。
            parent_chunk_overlap: 父块 文本块 overlap，具体约束请结合类型标注和调用方确认。
            child_chunk_size: 子块 文本块 size，具体约束请结合类型标注和调用方确认。
            child_chunk_overlap: 子块 文本块 overlap，具体约束请结合类型标注和调用方确认。
            unit: unit，具体约束请结合类型标注和调用方确认。
            parent_chunk_version: 父块 文本块 版本，具体约束请结合类型标注和调用方确认。
            child_chunk_version: 子块 文本块 版本，具体约束请结合类型标注和调用方确认。
            deterministic_created_at: deterministic created at，具体约束请结合类型标注和调用方确认。
            chunker_name: chunker 名称，具体约束请结合类型标注和调用方确认。
            tokenizer_model_name: tokenizer 模型 名称，具体约束请结合类型标注和调用方确认。
            tokenizer_local_files_only: tokenizer 本地 files only，具体约束请结合类型标注和调用方确认。
            build_context: build 上下文，具体约束请结合类型标注和调用方确认。

        返回:
            None

        阅读提示:
            主要直接调用：ChildParentChunker, bool。
        """
        self.created_at = deterministic_created_at
        self.delegate = ChildParentChunker(
            parent_chunk_size=parent_chunk_size,
            parent_chunk_overlap=parent_chunk_overlap,
            child_chunk_size=child_chunk_size,
            child_chunk_overlap=child_chunk_overlap,
            unit=unit,
            parent_chunk_strategy="fixed",
            child_chunk_strategy="fixed",
            parent_chunk_version=parent_chunk_version,
            child_chunk_version=child_chunk_version,
            chunker_name=chunker_name,
            tokenizer_model_name=tokenizer_model_name,
            tokenizer_local_files_only=tokenizer_local_files_only,
        )
        self._metadata = {
            "parent_strategy": "fixed",
            "child_strategy": "fixed",
            "parent_chunk_size": parent_chunk_size,
            "parent_chunk_overlap": parent_chunk_overlap,
            "child_chunk_size": child_chunk_size,
            "child_chunk_overlap": child_chunk_overlap,
            "unit": unit,
            "tokenizer_model_name": tokenizer_model_name,
            "tokenizer_local_files_only": bool(tokenizer_local_files_only),
        }

    # 阅读注释（函数）：处理 文本块 记录集合 相关逻辑。
    def chunk_records(self, records: Iterable[dict[str, Any]]) -> ParentChildChunkResult:
        """处理 文本块 记录集合 相关逻辑。

        参数:
            records: 记录集合，具体约束请结合类型标注和调用方确认。

        返回:
            ParentChildChunkResult

        阅读提示:
            主要直接调用：self.delegate.chunk_records, self._normalize_output。
        """
        result = self.delegate.chunk_records(records)
        self._normalize_output(result)
        return result

    # 阅读注释（函数）：规范化 输出。
    def _normalize_output(self, result: ParentChildChunkResult) -> None:
        """规范化 输出。

        参数:
            result: 待处理的结果对象。

        返回:
            None

        阅读提示:
            主要直接调用：getattr, plugin.to_dict, dict, record.get。
        """
        plugin = getattr(self, "plugin_metadata", None)
        plugin_dict = plugin.to_dict() if plugin is not None else {}
        for record in [*result.parents, *result.children]:
            if self.created_at:
                record["created_at"] = self.created_at
            extra = dict(record.get("extra") or {})
            extra["chunker_plugin"] = plugin_dict
            record["extra"] = extra

    # 阅读注释（函数）：处理 execution 元数据 相关逻辑。
    def execution_metadata(self) -> dict[str, Any]:
        """处理 execution 元数据 相关逻辑。

        返回:
            dict[str, Any]

        阅读提示:
            主要直接调用：dict。
        """
        return dict(self._metadata)


# 阅读注释（类）：封装 structured 父块 子块 chunker 插件，作为可配置插件接入 RAG 或 Agent 主链。
class StructuredParentChildChunkerPlugin:
    """Use a structural parent splitter and fixed child windows."""

    STRATEGY = "recursive"
    FLAT_CHUNKER = RecursiveChunker

    # 阅读注释（函数）：初始化 StructuredParentChildChunkerPlugin，保存运行所需的依赖、配置或状态。
    def __init__(
        self,
        *,
        parent_chunk_size: int = 1500,
        parent_chunk_overlap: int = 150,
        child_chunk_size: int = 500,
        child_chunk_overlap: int = 50,
        unit: str = "char",
        parent_chunk_version: str = DEFAULT_PARENT_CHUNK_VERSION,
        child_chunk_version: str = DEFAULT_CHILD_CHUNK_VERSION,
        deterministic_created_at: str | None = None,
        chunker_name: str | None = None,
        build_context: Any = None,
    ) -> None:
        """初始化 StructuredParentChildChunkerPlugin，保存运行所需的依赖、配置或状态。

        参数:
            parent_chunk_size: 父块 文本块 size，具体约束请结合类型标注和调用方确认。
            parent_chunk_overlap: 父块 文本块 overlap，具体约束请结合类型标注和调用方确认。
            child_chunk_size: 子块 文本块 size，具体约束请结合类型标注和调用方确认。
            child_chunk_overlap: 子块 文本块 overlap，具体约束请结合类型标注和调用方确认。
            unit: unit，具体约束请结合类型标注和调用方确认。
            parent_chunk_version: 父块 文本块 版本，具体约束请结合类型标注和调用方确认。
            child_chunk_version: 子块 文本块 版本，具体约束请结合类型标注和调用方确认。
            deterministic_created_at: deterministic created at，具体约束请结合类型标注和调用方确认。
            chunker_name: chunker 名称，具体约束请结合类型标注和调用方确认。
            build_context: build 上下文，具体约束请结合类型标注和调用方确认。

        返回:
            None

        阅读提示:
            主要直接调用：ValueError, int, lower, str, self.FLAT_CHUNKER, ChildParentChunker, max, min。
        """
        if parent_chunk_size <= 0 or child_chunk_size <= 0:
            raise ValueError("parent/child chunk sizes must be positive")
        if not 0 <= parent_chunk_overlap < parent_chunk_size:
            raise ValueError("invalid parent_chunk_overlap")
        if not 0 <= child_chunk_overlap < child_chunk_size:
            raise ValueError("invalid child_chunk_overlap")
        self.parent_chunk_size = int(parent_chunk_size)
        self.parent_chunk_overlap = int(parent_chunk_overlap)
        self.child_chunk_size = int(child_chunk_size)
        self.child_chunk_overlap = int(child_chunk_overlap)
        self.unit = str(unit or "char").lower()
        self.parent_chunk_version = str(parent_chunk_version)
        self.child_chunk_version = str(child_chunk_version)
        self.created_at = deterministic_created_at
        self.chunker_name = chunker_name or f"{self.STRATEGY}_parent_child"
        self.parent_splitter = self.FLAT_CHUNKER(
            chunk_size=self.parent_chunk_size,
            chunk_overlap=self.parent_chunk_overlap,
        )
        # Reuse its offset-aware child window implementation.
        self.child_helper = ChildParentChunker(
            parent_chunk_size=max(self.parent_chunk_size, self.child_chunk_size),
            parent_chunk_overlap=min(self.parent_chunk_overlap, max(self.parent_chunk_size, self.child_chunk_size) - 1),
            child_chunk_size=self.child_chunk_size,
            child_chunk_overlap=self.child_chunk_overlap,
            unit=self.unit,
            parent_chunk_strategy=self.STRATEGY,
            child_chunk_strategy="fixed",
            parent_chunk_version=self.parent_chunk_version,
            child_chunk_version=self.child_chunk_version,
            chunker_name=self.chunker_name,
        )

    # 阅读注释（函数）：处理 execution 元数据 相关逻辑。
    def execution_metadata(self) -> dict[str, Any]:
        """处理 execution 元数据 相关逻辑。

        返回:
            dict[str, Any]
        """
        return {
            "parent_strategy": self.STRATEGY,
            "child_strategy": "fixed",
            "parent_chunk_size": self.parent_chunk_size,
            "parent_chunk_overlap": self.parent_chunk_overlap,
            "child_chunk_size": self.child_chunk_size,
            "child_chunk_overlap": self.child_chunk_overlap,
            "unit": self.unit,
        }

    # 阅读注释（函数）：处理 文本块 记录集合 相关逻辑。
    def chunk_records(self, records: Iterable[dict[str, Any]]) -> ParentChildChunkResult:
        """处理 文本块 记录集合 相关逻辑。

        参数:
            records: 记录集合，具体约束请结合类型标注和调用方确认。

        返回:
            ParentChildChunkResult

        阅读提示:
            主要直接调用：defaultdict, row_to_unit, unit.get, should_skip_unit, append, str, sorted, self._chunk_doc。
        """
        groups: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for record in records:
            unit = row_to_unit(record)
            if unit.get("doc_id") and not should_skip_unit(unit):
                groups[str(unit["doc_id"])].append(unit)

        parents: list[dict[str, Any]] = []
        children: list[dict[str, Any]] = []
        global_child_index = 1
        for doc_id in sorted(groups):
            doc_parents, doc_children, global_child_index = self._chunk_doc(
                doc_id,
                sorted(groups[doc_id], key=lambda item: int(item.get("unit_order") or 0)),
                global_child_index,
            )
            parents.extend(doc_parents)
            children.extend(doc_children)
        return ParentChildChunkResult(parents=parents, children=children)

    # 阅读注释（函数）：处理 文本块 doc 相关逻辑。
    def _chunk_doc(
        self,
        doc_id: str,
        units: list[dict[str, Any]],
        global_child_index: int,
    ) -> tuple[list[dict[str, Any]], list[dict[str, Any]], int]:
        """处理 文本块 doc 相关逻辑。

        参数:
            doc_id: doc 标识，具体约束请结合类型标注和调用方确认。
            units: units，具体约束请结合类型标注和调用方确认。
            global_child_index: global 子块 索引，具体约束请结合类型标注和调用方确认。

        返回:
            tuple[list[dict[str, Any]], list[dict[str, Any]], int]

        阅读提示:
            主要直接调用：self._join_units, _last, _first, self.parent_splitter.chunk_document, enumerate, strip, str, flat.get。
        """
        text, spans = self._join_units(units)
        if not text:
            return [], [], global_child_index
        document = {
            "doc_id": doc_id,
            "text": text,
            "metadata": {
                "title": _last(units, "title"),
                "source": _first(units, "source_uri"),
                "source_path": _first(units, "source_uri"),
                "doc_type": _first(units, "source_format"),
            },
        }
        flat_chunks = self.parent_splitter.chunk_document(document)
        parents: list[dict[str, Any]] = []
        children: list[dict[str, Any]] = []

        for parent_index, flat in enumerate(flat_chunks, start=1):
            parent_text = str(flat.get("text") or "").strip()
            if not parent_text:
                continue
            metadata = flat.get("metadata") if isinstance(flat.get("metadata"), dict) else {}
            start = _safe_int(metadata.get("start_char"))
            end = _safe_int(metadata.get("end_char"))
            if start is None:
                start = text.find(parent_text)
            if end is None and start is not None and start >= 0:
                end = start + len(parent_text)
            parent_units = self._units_for_span(units, spans, start, end)
            if not parent_units:
                parent_units = units
            parent_id = f"{doc_id}_parent_{parent_index:06d}"
            child_windows = self.child_helper._split_text_windows_with_offsets(  # noqa: SLF001
                parent_text,
                chunk_size=self.child_chunk_size,
                overlap=self.child_chunk_overlap,
            )
            child_records: list[dict[str, Any]] = []
            for child_index, (child_text, child_start, child_end) in enumerate(child_windows, start=1):
                child_id = f"{parent_id}_child_{child_index:04d}"
                child = build_child_chunk_v1(
                    child_chunk_id=child_id,
                    parent_chunk_id=parent_id,
                    doc_id=doc_id,
                    text=child_text,
                    source_type=_first(parent_units, "source_type", DEFAULT_SOURCE_TYPE),
                    source_unit_ids=unique_keep_order([u.get("unit_id") for u in parent_units]),
                    title=_last(parent_units, "title"),
                    section=metadata.get("section") or _last(parent_units, "section"),
                    section_level=metadata.get("heading_level") or _last(parent_units, "section_level"),
                    page_start=_bounds(parent_units, "page_start", maximum=False),
                    page_end=_bounds(parent_units, "page_end", maximum=True),
                    child_chunk_index=global_child_index,
                    child_index_in_parent=child_index,
                    child_chunk_strategy="fixed",
                    char_start_in_parent=child_start,
                    char_end_in_parent=child_end,
                    cleaning_version=_last(parent_units, "cleaning_version", DEFAULT_CLEANING_VERSION),
                    parent_chunk_version=self.parent_chunk_version,
                    child_chunk_version=self.child_chunk_version,
                    created_at=self.created_at,
                    extra={
                        **_unit_extra(parent_units, text=child_text, chunker_name=self.chunker_name),
                        "parent_strategy": self.STRATEGY,
                    },
                )
                child_records.append(child)
                global_child_index += 1

            parent = build_parent_chunk_v1(
                parent_chunk_id=parent_id,
                doc_id=doc_id,
                text=parent_text,
                source_type=_first(parent_units, "source_type", DEFAULT_SOURCE_TYPE),
                source_unit_ids=unique_keep_order([u.get("unit_id") for u in parent_units]),
                child_chunk_ids=[item["child_chunk_id"] for item in child_records],
                title=_last(parent_units, "title"),
                section=metadata.get("section") or _last(parent_units, "section"),
                section_level=metadata.get("heading_level") or _last(parent_units, "section_level"),
                page_start=_bounds(parent_units, "page_start", maximum=False),
                page_end=_bounds(parent_units, "page_end", maximum=True),
                parent_chunk_index=parent_index,
                parent_chunk_strategy=self.STRATEGY,
                cleaning_version=_last(parent_units, "cleaning_version", DEFAULT_CLEANING_VERSION),
                parent_chunk_version=self.parent_chunk_version,
                created_at=self.created_at,
                extra={
                    **_unit_extra(parent_units, text=parent_text, chunker_name=self.chunker_name),
                    "parent_strategy": self.STRATEGY,
                    "source_char_start": start,
                    "source_char_end": end,
                },
            )
            plugin = getattr(self, "plugin_metadata", None)
            plugin_dict = plugin.to_dict() if plugin is not None else {}
            parent["extra"]["chunker_plugin"] = plugin_dict
            for child in child_records:
                child["extra"]["chunker_plugin"] = plugin_dict
            parents.append(parent)
            children.extend(child_records)
        return parents, children, global_child_index

    # 阅读注释（函数）：处理 join units 相关逻辑。
    @staticmethod
    def _join_units(units: Sequence[dict[str, Any]]) -> tuple[str, list[tuple[int, int]]]:
        """处理 join units 相关逻辑。

        参数:
            units: units，具体约束请结合类型标注和调用方确认。

        返回:
            tuple[str, list[tuple[int, int]]]

        阅读提示:
            主要直接调用：str, unit.get, spans.append, pieces.append, len, join。
        """
        pieces: list[str] = []
        spans: list[tuple[int, int]] = []
        cursor = 0
        for unit in units:
            value = str(unit.get("text") or "")
            if not value:
                spans.append((cursor, cursor))
                continue
            if pieces:
                pieces.append("\n")
                cursor += 1
            start = cursor
            pieces.append(value)
            cursor += len(value)
            spans.append((start, cursor))
        return "".join(pieces), spans

    # 阅读注释（函数）：处理 units for span 相关逻辑。
    @staticmethod
    def _units_for_span(
        units: Sequence[dict[str, Any]],
        spans: Sequence[tuple[int, int]],
        start: int | None,
        end: int | None,
    ) -> list[dict[str, Any]]:
        """处理 units for span 相关逻辑。

        参数:
            units: units，具体约束请结合类型标注和调用方确认。
            spans: spans，具体约束请结合类型标注和调用方确认。
            start: start，具体约束请结合类型标注和调用方确认。
            end: end，具体约束请结合类型标注和调用方确认。

        返回:
            list[dict[str, Any]]

        阅读提示:
            主要直接调用：zip。
        """
        if start is None or end is None or start < 0:
            return []
        return [unit for unit, (u_start, u_end) in zip(units, spans) if u_start < end and u_end > start]


# 阅读注释（类）：封装 recursive 父块 子块 chunker 插件，作为可配置插件接入 RAG 或 Agent 主链。
class RecursiveParentChildChunkerPlugin(StructuredParentChildChunkerPlugin):
    """封装 recursive 父块 子块 chunker 插件，作为可配置插件接入 RAG 或 Agent 主链。"""
    STRATEGY = "recursive"
    FLAT_CHUNKER = RecursiveChunker


# 阅读注释（类）：封装 heading 父块 子块 chunker 插件，作为可配置插件接入 RAG 或 Agent 主链。
class HeadingParentChildChunkerPlugin(StructuredParentChildChunkerPlugin):
    """封装 heading 父块 子块 chunker 插件，作为可配置插件接入 RAG 或 Agent 主链。"""
    STRATEGY = "heading"
    FLAT_CHUNKER = HeadingChunker


# 阅读注释（类）：封装 paragraph 父块 子块 chunker 插件，作为可配置插件接入 RAG 或 Agent 主链。
class ParagraphParentChildChunkerPlugin(StructuredParentChildChunkerPlugin):
    """封装 paragraph 父块 子块 chunker 插件，作为可配置插件接入 RAG 或 Agent 主链。"""
    STRATEGY = "paragraph"
    FLAT_CHUNKER = ParagraphChunker


# 阅读注释（类）：封装 fixed structured 父块 子块 chunker 插件，作为可配置插件接入 RAG 或 Agent 主链。
class FixedStructuredParentChildChunkerPlugin(StructuredParentChildChunkerPlugin):
    """Alternative fixed implementation used for registry parity tests."""

    STRATEGY = "fixed"
    FLAT_CHUNKER = FixedSizeChunker
