# -*- coding: utf-8 -*-
"""把 canonical ``cleaned_text_unit_v1`` 聚合为平面 ``chunk_v1`` 的策略层。

处理顺序：严格校验 Spark Row/dict → 内部文本规范化 → 跳过图片与极短噪声 → 拆分超长
Unit → 按文档和 ``unit_order`` 聚合 → 通过 ``build_chunk_v1`` 构造标准记录。本文件不定义
Schema，也不承担 Spark 读写；Spark Job 只负责排序与 ``mapPartitions`` 编排。
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
from rag.schema.cleaned_text_unit import CleanedTextUnitSchema
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


def _row_to_dict(row: Any) -> Dict[str, Any]:
    """接受普通 dict 或 Spark Row；其他类型在生产边界直接拒绝。"""
    if isinstance(row, dict):
        return row
    if hasattr(row, "asDict"):
        return row.asDict(recursive=True)
    raise TypeError(f"Unsupported row type: {type(row)!r}")


def row_to_unit(row: Any) -> Dict[str, Any]:
    """先严格校验 canonical Unit，再执行仅供 Chunk 聚合使用的内部规范化。

    这里不是历史兼容映射器：字段补缺、类型强转、未知字段丢弃和旧协议迁移都不得发生在
    Pydantic 校验之前。校验通过后才允许规范正文、派生长度和读取来源解析单元 ID。
    """
    d = _row_to_dict(row)
    validated = CleanedTextUnitSchema.model_validate(d)
    projected = validated.model_dump(exclude={"schema_version"})

    # Normalization below is internal chunk preparation and happens only after
    # the producer payload has satisfied the canonical contract.
    text = normalize_text(projected.get("text"))
    extra = dict(projected.get("extra") or {})
    projected["unit_id"] = projected.get("unit_id") or extra.get("source_parse_unit_id")
    projected["text"] = text
    projected["text_length"] = len(text)
    projected["extra"] = extra
    return projected


# =========================================================
# unit filtering / preprocessing
# =========================================================


def should_skip_unit(unit: Dict[str, Any]) -> bool:
    """过滤空文本、图片块和不足十字符的 ``other/short_text`` 噪声。"""
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


def expand_long_units(units: List[Dict[str, Any]], chunk_size: int, overlap: int) -> List[Dict[str, Any]]:
    """按字符窗拆分超长 Unit，并生成稳定 part ID 与质量标记。

    该兜底只在单个来源 Unit 已超过最终 Chunk 上限时发生，避免后续聚合器永远无法满足
    预算。原 Unit 的其他来源和质量字段完整复制。
    """
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


def pick_language(units: List[Dict[str, Any]]) -> str:
    """以 Unit 数量众数选择 Chunk 语言；无输入时返回 ``unknown``。"""
    counts: Dict[str, int] = {}
    for u in units:
        lang = u.get("language") or "unknown"
        counts[lang] = counts.get(lang, 0) + 1
    if not counts:
        return "unknown"
    return sorted(counts.items(), key=lambda x: x[1], reverse=True)[0][0]


def compute_chunk_type(units: List[Dict[str, Any]]) -> str:
    """根据 Unit 类型集合判定纯表格、混合或普通文本 Chunk。"""
    unit_types = set([u.get("unit_type") or "other" for u in units])
    if unit_types == {"table"}:
        return "table"
    if "table" in unit_types:
        return "mixed"
    return "text"


def avg_quality_score(units: List[Dict[str, Any]]) -> Optional[float]:
    """对存在的 Unit 质量分求平均；全缺失时保持 ``None``。"""
    scores = [u.get("quality_score") for u in units if u.get("quality_score") is not None]
    if not scores:
        return None
    return round(sum(scores) / float(len(scores)), 4)


def collect_quality_flags(units: List[Dict[str, Any]], text_length: int, min_chunk_size: int) -> List[Any]:
    """稳定去重来源质量标记，并为过短聚合结果追加 ``short_chunk``。"""
    flags: List[Any] = []
    for u in units:
        flags.extend(u.get("quality_flags") or [])

    flags = unique_keep_order(flags)
    if text_length < min_chunk_size and "short_chunk" not in flags:
        flags.append("short_chunk")
    return flags


def _first_non_empty(units: List[Dict[str, Any]], key: str, default: Any = None) -> Any:
    """取首个非空来源字段，常用于文档级静态属性。"""
    for u in units:
        v = u.get(key)
        if v not in (None, ""):
            return v
    return default


def _last_non_empty(units: List[Dict[str, Any]], key: str, default: Any = None) -> Any:
    """取最后一个非空字段，适合当前章节标题和层级。"""
    for u in reversed(units):
        v = u.get(key)
        if v not in (None, ""):
            return v
    return default


# =========================================================
# flat chunk strategy
# =========================================================


def overlap_tail(units: List[Dict[str, Any]], overlap: int) -> List[Dict[str, Any]]:
    """从当前 Chunk 尾部保留完整 Unit，直到累计正文长度达到 overlap。

    overlap 参数以字符目标表达，但边界保持 Unit 完整，因此实际重叠可能略大于配置值。
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
    """聚合 Unit 正文、页码、章节、质量和清洗版本，并构造标准 ``chunk_v1``。"""
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
    """对单个文档按顺序贪心聚合 Unit，并在提交后保留 Unit 级 overlap。

    超长 Unit 会先被拆分；每次加入下一个 Unit 将超过字符上限时提交当前 Chunk。最后一个
    Buffer 即使低于 ``min_chunk_size`` 也会保留，并通过质量标记暴露而不是静默丢弃。
    """
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


def chunk_partition(
    rows_iter: Iterable[Any],
    chunk_size: int,
    overlap: int,
    min_chunk_size: int,
    batch_id_arg: Optional[str] = None,
    chunk_strategy: str = DEFAULT_CHUNK_STRATEGY,
    chunk_version: str = DEFAULT_CHUNK_VERSION,
) -> Iterator[str]:
    """Spark ``mapPartitions`` 入口：流式处理已按 doc_id/unit_order 排序的 Row。

    文档 ID 变化时立即提交前一文档，避免把整个分区全部载入内存；输出每行一个 JSONL
    ``chunk_v1``。调用方必须保证同一文档记录不会跨 Partition。
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


def chunk_records_for_doc(
    records: List[Dict[str, Any]],
    chunk_size: int,
    overlap: int,
    min_chunk_size: int,
    batch_id_arg: Optional[str] = None,
    chunk_strategy: str = DEFAULT_CHUNK_STRATEGY,
    chunk_version: str = DEFAULT_CHUNK_VERSION,
) -> List[Dict[str, Any]]:
    """非 Spark 测试入口：严格校验并排序一个文档的 Unit，再复用生产聚合逻辑。"""
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
