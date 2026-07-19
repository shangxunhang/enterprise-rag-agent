# -*- coding: utf-8 -*-
# =============================================================================
# 中文阅读说明：RAG 核心模块，负责查询变换、召回、融合、重排、证据评估和上下文组装。
# 主要定义：_row_to_dict、row_to_unit、should_skip_unit、expand_long_units、pick_language、compute_chunk_type、avg_quality_score、collect_quality_flags、_first_non_empty、_last_non_empty等。建议先从公开入口函数开始，再沿调用关系向下阅读。
# =============================================================================
"""
rag_template/chunker/cleaned_text_unit_chunker.py
=================================================

cleaned_text_unit_v1 -> flat chunk_v1 的 chunk 策略层。

职责边界：
1. 读取 Spark Row / dict 形式的 cleaned_text_unit_v1 记录并转换成内部 unit。
2. 过滤不应参与文本检索的 unit，例如 image、极短噪声 other。
3. 对超长 unit 做兜底切分。
4. 按 doc_id 内 unit_order 顺序聚合成 flat chunk。
5. 调用 rag_template.schema.Chunk_Schema.build_chunk_v1() 生成标准 chunk_v1。

注意：
- 本文件是 chunk 策略层，不定义 schema。
- 本文件直接调用 schema/Chunk_Schema.py，不调用 util/schema_builder.py。
- Spark job 只负责读写和 mapPartitions 编排，具体策略放在这里。
"""

from __future__ import annotations

import json
from typing import Any, Dict, Iterable, Iterator, List, Optional

from rag.configs.SchemaConfig import (
    DEFAULT_CHUNK_STRATEGY,
    DEFAULT_CHUNK_VERSION,
    DEFAULT_CLEANING_VERSION,
    DEFAULT_SOURCE_TYPE,
)
from rag.schema.Chunk_Schema import build_chunk_v1
from rag.util.text_utils import (
    as_list,
    normalize_text,
    safe_float,
    safe_int,
    split_long_text,
    unique_keep_order,
)


# =========================================================
# cleaned_text_unit normalizer
# =========================================================


# 阅读注释（函数）：处理 row to 字典 相关逻辑。
def _row_to_dict(row: Any) -> Dict[str, Any]:
    """处理 row to 字典 相关逻辑。

    参数:
        row: row，具体约束请结合类型标注和调用方确认。

    返回:
        Dict[str, Any]

    阅读提示:
        主要直接调用：isinstance, hasattr, row.asDict, TypeError, type。
    """
    if isinstance(row, dict):
        return row
    if hasattr(row, "asDict"):
        return row.asDict(recursive=True)
    raise TypeError(f"Unsupported row type: {type(row)!r}")


# 阅读注释（函数）：处理 row to unit 相关逻辑。
def row_to_unit(row: Any) -> Dict[str, Any]:
    """把 Spark Row / dict 统一转换成 chunk 策略内部使用的 unit。"""
    d = _row_to_dict(row)
    text = normalize_text(d.get("text"))

    extra = d.get("extra") or {}
    if not isinstance(extra, dict):
        extra = {}

    unit_id = d.get("unit_id") or extra.get("source_parse_unit_id")

    return {
        "unit_id": unit_id,
        "doc_id": d.get("doc_id"),
        "source_type": d.get("source_type") or DEFAULT_SOURCE_TYPE,
        "source_uri": d.get("source_uri"),
        "source_name": d.get("source_name"),
        "source_format": d.get("source_format"),
        "batch_id": d.get("batch_id"),
        "title": d.get("title"),
        "section": d.get("section"),
        "section_level": safe_int(d.get("section_level"), None),
        "page_start": safe_int(d.get("page_start"), None),
        "page_end": safe_int(d.get("page_end"), None),
        "unit_type": d.get("unit_type") or "other",
        "unit_order": safe_int(d.get("unit_order"), 0),
        "text": text,
        "text_length": len(text),
        "language": d.get("language") or "unknown",
        "quality_score": safe_float(d.get("quality_score"), None),
        "quality_flags": as_list(d.get("quality_flags")),
        "cleaning_version": d.get("cleaning_version") or DEFAULT_CLEANING_VERSION,
        "extra": extra,
    }


# =========================================================
# unit filtering / preprocessing
# =========================================================


# 阅读注释（函数）：判断是否应当执行 skip unit。
def should_skip_unit(unit: Dict[str, Any]) -> bool:
    """判断 cleaned text unit 是否应该跳过。"""
    text = unit.get("text") or ""
    unit_type = unit.get("unit_type") or "other"
    quality_flags = set(unit.get("quality_flags") or [])

    if not text.strip():
        return True

    # 图片块不参与文本 chunk。
    if unit_type == "image":
        return True

    # 第一版不删除所有 short_text，只过滤非常短且类型为 other 的噪声。
    if unit_type == "other" and "short_text" in quality_flags and len(text) < 10:
        return True

    return False


# 阅读注释（函数）：处理 expand long units 相关逻辑。
def expand_long_units(units: List[Dict[str, Any]], chunk_size: int, overlap: int) -> List[Dict[str, Any]]:
    """对超长 unit 做兜底拆分，避免单个 unit 直接撑爆 chunk_size。"""
    expanded: List[Dict[str, Any]] = []

    for unit in units:
        text = unit.get("text") or ""
        if len(text) <= chunk_size:
            expanded.append(unit)
            continue

        pieces = split_long_text(text, chunk_size=chunk_size, overlap=overlap)
        for idx, piece in enumerate(pieces, start=1):
            u = dict(unit)
            u["text"] = piece
            u["text_length"] = len(piece)
            u["unit_id"] = "%s_part_%04d" % (unit.get("unit_id"), idx)

            flags = list(u.get("quality_flags") or [])
            if "split_from_long_unit" not in flags:
                flags.append("split_from_long_unit")
            u["quality_flags"] = flags

            expanded.append(u)

    return expanded


# =========================================================
# chunk attribute computation
# =========================================================


# 阅读注释（函数）：处理 pick language 相关逻辑。
def pick_language(units: List[Dict[str, Any]]) -> str:
    """处理 pick language 相关逻辑。

    参数:
        units: units，具体约束请结合类型标注和调用方确认。

    返回:
        str

    阅读提示:
        主要直接调用：u.get, counts.get, sorted, counts.items。
    """
    counts: Dict[str, int] = {}
    for u in units:
        lang = u.get("language") or "unknown"
        counts[lang] = counts.get(lang, 0) + 1
    if not counts:
        return "unknown"
    return sorted(counts.items(), key=lambda x: x[1], reverse=True)[0][0]


# 阅读注释（函数）：计算 文本块 类型。
def compute_chunk_type(units: List[Dict[str, Any]]) -> str:
    """计算 文本块 类型。

    参数:
        units: units，具体约束请结合类型标注和调用方确认。

    返回:
        str

    阅读提示:
        主要直接调用：set, u.get。
    """
    unit_types = set([u.get("unit_type") or "other" for u in units])
    if unit_types == {"table"}:
        return "table"
    if "table" in unit_types:
        return "mixed"
    return "text"


# 阅读注释（函数）：处理 avg 质量 score 相关逻辑。
def avg_quality_score(units: List[Dict[str, Any]]) -> Optional[float]:
    """处理 avg 质量 score 相关逻辑。

    参数:
        units: units，具体约束请结合类型标注和调用方确认。

    返回:
        Optional[float]

    阅读提示:
        主要直接调用：u.get, round, sum, float, len。
    """
    scores = [u.get("quality_score") for u in units if u.get("quality_score") is not None]
    if not scores:
        return None
    return round(sum(scores) / float(len(scores)), 4)


# 阅读注释（函数）：收集 质量 flags。
def collect_quality_flags(units: List[Dict[str, Any]], text_length: int, min_chunk_size: int) -> List[Any]:
    """收集 质量 flags。

    参数:
        units: units，具体约束请结合类型标注和调用方确认。
        text_length: 文本 length，具体约束请结合类型标注和调用方确认。
        min_chunk_size: min 文本块 size，具体约束请结合类型标注和调用方确认。

    返回:
        List[Any]

    阅读提示:
        主要直接调用：flags.extend, u.get, unique_keep_order, flags.append。
    """
    flags: List[Any] = []
    for u in units:
        flags.extend(u.get("quality_flags") or [])

    flags = unique_keep_order(flags)
    if text_length < min_chunk_size and "short_chunk" not in flags:
        flags.append("short_chunk")
    return flags


# 阅读注释（函数）：处理 first non empty 相关逻辑。
def _first_non_empty(units: List[Dict[str, Any]], key: str, default: Any = None) -> Any:
    """处理 first non empty 相关逻辑。

    参数:
        units: units，具体约束请结合类型标注和调用方确认。
        key: key，具体约束请结合类型标注和调用方确认。
        default: default，具体约束请结合类型标注和调用方确认。

    返回:
        Any

    阅读提示:
        主要直接调用：u.get。
    """
    for u in units:
        v = u.get(key)
        if v not in (None, ""):
            return v
    return default


# 阅读注释（函数）：处理 last non empty 相关逻辑。
def _last_non_empty(units: List[Dict[str, Any]], key: str, default: Any = None) -> Any:
    """处理 last non empty 相关逻辑。

    参数:
        units: units，具体约束请结合类型标注和调用方确认。
        key: key，具体约束请结合类型标注和调用方确认。
        default: default，具体约束请结合类型标注和调用方确认。

    返回:
        Any

    阅读提示:
        主要直接调用：reversed, u.get。
    """
    for u in reversed(units):
        v = u.get(key)
        if v not in (None, ""):
            return v
    return default


# =========================================================
# flat chunk strategy
# =========================================================


# 阅读注释（函数）：处理 overlap tail 相关逻辑。
def overlap_tail(units: List[Dict[str, Any]], overlap: int) -> List[Dict[str, Any]]:
    """
    基于 unit 粒度实现 overlap。

    注意：这里不是字符级截断 overlap，而是保留尾部若干 unit，使得尾部文本长度 >= overlap。
    这样能尽量避免把一个 cleaned_text_unit 拦腰切开。
    """
    if overlap <= 0:
        return []

    tail: List[Dict[str, Any]] = []
    total = 0
    for u in reversed(units):
        tail.insert(0, u)
        total += len(u.get("text") or "")
        if total >= overlap:
            break
    return tail


# 阅读注释（函数）：构建 flat 文本块 v1。
def build_flat_chunk_v1(
    *,
    doc_id: str,
    chunk_index: int,
    units: List[Dict[str, Any]],
    min_chunk_size: int,
    batch_id_arg: Optional[str] = None,
    chunk_strategy: str = DEFAULT_CHUNK_STRATEGY,
    chunk_version: str = DEFAULT_CHUNK_VERSION,
) -> Dict[str, Any]:
    """根据聚合后的 units 调用 Chunk_Schema.build_chunk_v1() 生成标准 chunk_v1。"""
    texts = [u.get("text") or "" for u in units if u.get("text")]
    text = "\n".join(texts).strip()
    text_length = len(text)

    page_starts = [u.get("page_start") for u in units if u.get("page_start") is not None]
    page_ends = [u.get("page_end") for u in units if u.get("page_end") is not None]
    section_levels = [u.get("section_level") for u in units if u.get("section_level") is not None]

    source_unit_ids = unique_keep_order([u.get("unit_id") for u in units])
    cleaning_versions = unique_keep_order([u.get("cleaning_version") for u in units])

    chunk_type = compute_chunk_type(units)
    language = pick_language(units)
    quality_score = avg_quality_score(units)
    quality_flags = collect_quality_flags(units, text_length, min_chunk_size)

    extra = {
        "source_uri": _first_non_empty(units, "source_uri"),
        "source_name": _first_non_empty(units, "source_name"),
        "source_format": _first_non_empty(units, "source_format"),
        "batch_id": _first_non_empty(units, "batch_id", batch_id_arg) or batch_id_arg,
        "chunk_type": chunk_type,
        "language": language,
        "quality_score": quality_score,
        "quality_flags": quality_flags,
        "source_unit_count": len(source_unit_ids),
        "source_unit_types": unique_keep_order([u.get("unit_type") for u in units]),
        "cleaning_versions": cleaning_versions,
    }

    return build_chunk_v1(
        chunk_id="%s_chunk_%06d" % (doc_id, chunk_index),
        doc_id=doc_id,
        source_type=_first_non_empty(units, "source_type", DEFAULT_SOURCE_TYPE),
        text=text,
        source_unit_ids=source_unit_ids,
        title=_last_non_empty(units, "title"),
        section=_last_non_empty(units, "section"),
        section_level=section_levels[-1] if section_levels else None,
        page_start=min(page_starts) if page_starts else None,
        page_end=max(page_ends) if page_ends else None,
        chunk_index=chunk_index,
        chunk_strategy=chunk_strategy,
        cleaning_version=cleaning_versions[-1] if cleaning_versions else DEFAULT_CLEANING_VERSION,
        chunk_version=chunk_version,
        extra=extra,
    )


# 阅读注释（函数）：处理 文本块 units for doc 相关逻辑。
def chunk_units_for_doc(
    doc_id: str,
    units: List[Dict[str, Any]],
    chunk_size: int,
    overlap: int,
    min_chunk_size: int,
    batch_id_arg: Optional[str] = None,
    chunk_strategy: str = DEFAULT_CHUNK_STRATEGY,
    chunk_version: str = DEFAULT_CHUNK_VERSION,
) -> List[Dict[str, Any]]:
    """对单个 doc_id 下的 cleaned units 构造 flat chunk_v1。"""
    units = [u for u in units if not should_skip_unit(u)]
    units = expand_long_units(units, chunk_size=chunk_size, overlap=overlap)

    chunks: List[Dict[str, Any]] = []
    current: List[Dict[str, Any]] = []
    current_len = 0
    chunk_index = 1

    for unit in units:
        unit_len = len(unit.get("text") or "")
        sep_len = 1 if current else 0

        if current and current_len + sep_len + unit_len > chunk_size:
            chunk = build_flat_chunk_v1(
                doc_id=doc_id,
                chunk_index=chunk_index,
                units=current,
                min_chunk_size=min_chunk_size,
                batch_id_arg=batch_id_arg,
                chunk_strategy=chunk_strategy,
                chunk_version=chunk_version,
            )
            if chunk.get("text_length", 0) > 0:
                chunks.append(chunk)
                chunk_index += 1

            current = overlap_tail(current, overlap)
            current_len = sum([len(u.get("text") or "") for u in current]) + max(len(current) - 1, 0)

        current.append(unit)
        current_len += sep_len + unit_len

    if current:
        chunk = build_flat_chunk_v1(
            doc_id=doc_id,
            chunk_index=chunk_index,
            units=current,
            min_chunk_size=min_chunk_size,
            batch_id_arg=batch_id_arg,
            chunk_strategy=chunk_strategy,
            chunk_version=chunk_version,
        )
        if chunk.get("text_length", 0) > 0:
            chunks.append(chunk)

    return chunks


# 阅读注释（函数）：处理 文本块 partition 相关逻辑。
def chunk_partition(
    rows_iter: Iterable[Any],
    chunk_size: int,
    overlap: int,
    min_chunk_size: int,
    batch_id_arg: Optional[str] = None,
    chunk_strategy: str = DEFAULT_CHUNK_STRATEGY,
    chunk_version: str = DEFAULT_CHUNK_VERSION,
) -> Iterator[str]:
    """
    Spark mapPartitions 使用的入口。

    输入：同一 partition 内已按 doc_id / unit_order 排好序的 rows。
    输出：JSONL 字符串，每行一个 chunk_v1。
    """
    current_doc_id: Optional[str] = None
    current_units: List[Dict[str, Any]] = []

    for row in rows_iter:
        unit = row_to_unit(row)
        doc_id = unit.get("doc_id")
        if not doc_id:
            continue

        if current_doc_id is None:
            current_doc_id = doc_id

        if doc_id != current_doc_id:
            for chunk in chunk_units_for_doc(
                current_doc_id,
                current_units,
                chunk_size=chunk_size,
                overlap=overlap,
                min_chunk_size=min_chunk_size,
                batch_id_arg=batch_id_arg,
                chunk_strategy=chunk_strategy,
                chunk_version=chunk_version,
            ):
                yield json.dumps(chunk, ensure_ascii=False)

            current_doc_id = doc_id
            current_units = []

        current_units.append(unit)

    if current_doc_id is not None and current_units:
        for chunk in chunk_units_for_doc(
            current_doc_id,
            current_units,
            chunk_size=chunk_size,
            overlap=overlap,
            min_chunk_size=min_chunk_size,
            batch_id_arg=batch_id_arg,
            chunk_strategy=chunk_strategy,
            chunk_version=chunk_version,
        ):
            yield json.dumps(chunk, ensure_ascii=False)


# 阅读注释（函数）：处理 文本块 记录集合 for doc 相关逻辑。
def chunk_records_for_doc(
    records: List[Dict[str, Any]],
    chunk_size: int,
    overlap: int,
    min_chunk_size: int,
    batch_id_arg: Optional[str] = None,
    chunk_strategy: str = DEFAULT_CHUNK_STRATEGY,
    chunk_version: str = DEFAULT_CHUNK_VERSION,
) -> List[Dict[str, Any]]:
    """
    非 Spark 测试入口：输入一个 doc 的 cleaned_text_unit_v1 dict 列表，输出 chunk_v1 dict 列表。
    """
    units = [row_to_unit(r) for r in records]
    units = sorted(units, key=lambda u: safe_int(u.get("unit_order"), 0) or 0)
    doc_id = units[0].get("doc_id") if units else None
    if not doc_id:
        return []
    return chunk_units_for_doc(
        doc_id=doc_id,
        units=units,
        chunk_size=chunk_size,
        overlap=overlap,
        min_chunk_size=min_chunk_size,
        batch_id_arg=batch_id_arg,
        chunk_strategy=chunk_strategy,
        chunk_version=chunk_version,
    )
