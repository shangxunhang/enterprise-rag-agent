"""Deterministic unit and document quality rules."""

from __future__ import annotations

import re
from collections import Counter
from typing import Any, Iterable, Mapping

_COUNTED_CONTENT_TYPES = (
    "title",
    "paragraph",
    "list",
    "table_block",
    "image_block",
    "chart_block",
    "formula_block",
    "header",
    "footer",
)

REPLACEMENT_RATIO_THRESHOLD = 0.01
CONTROL_RATIO_THRESHOLD = 0.005
GARBLED_BLOCK_RATIO_THRESHOLD = 0.20
EMPTY_TEXT_BLOCK_RATIO_THRESHOLD = 0.50
LARGE_SOURCE_SIZE_BYTES = 100_000
LARGE_SOURCE_MIN_SEARCHABLE_CHARS = 100

_SEARCHABLE_TYPES = {"title", "paragraph", "list", "table_block", "formula_block"}
_ASSET_TYPES = {"image_block", "chart_block"}


def language_of(text: str) -> str:
    chinese = len(re.findall(r"[\u3400-\u9fff]", text))
    latin = len(re.findall(r"[A-Za-z]", text))
    if chinese and latin:
        return "mixed"
    if chinese:
        return "zh"
    if latin:
        return "en"
    return "unknown"


def unit_quality(
    text: str,
    *,
    content_type: str,
    initial_flags: Iterable[str] = (),
    has_asset: bool = False,
) -> tuple[float | None, list[str]]:
    flags = list(dict.fromkeys(initial_flags))
    if not text and content_type not in {"image_block", "chart_block"}:
        flags.append("empty_text")
    elif 0 < len(text) < 20:
        flags.append("short_text")
    control_count = sum(1 for character in text if ord(character) < 32 and character not in "\n\t")
    if control_count:
        flags.append("control_characters")
    replacement_count = text.count("\ufffd")
    if replacement_count >= max(2, int(len(text) * 0.01)):
        flags.append("suspected_garbled")
    flags = list(dict.fromkeys(flags))
    if content_type in {"image_block", "chart_block"} and not text:
        return (None if has_asset else 0.25), flags
    score = 0.9
    deductions = {
        "short_text": 0.15,
        "empty_text": 0.9,
        "suspected_garbled": 0.5,
        "control_characters": 0.25,
        "asset_not_extracted": 0.3,
        "table_structure_degraded": 0.25,
        "formula_structure_degraded": 0.3,
    }
    score -= sum(deductions.get(flag, 0.0) for flag in flags)
    return round(min(1.0, max(0.0, score)), 2), flags


def document_statistics(records: Iterable[Mapping[str, Any]]) -> dict[str, Any]:
    materialized = list(records)
    type_counts = Counter(record["content_type"] for record in materialized)
    flags = sorted({flag for record in materialized for flag in record["quality_flags"]})
    pages = [int(record["page_no"]) for record in materialized]
    result: dict[str, Any] = {
        "record_count": len(materialized),
        "total_text_length": sum(int(record["text_length"]) for record in materialized),
        "page_count": max(pages, default=0),
        "quality_flags": flags,
        "quality_metrics": _document_quality_metrics(materialized),
    }
    for content_type in _COUNTED_CONTENT_TYPES:
        key = {
            "table_block": "table_count",
            "image_block": "image_count",
            "chart_block": "chart_count",
            "formula_block": "formula_count",
            "header": "header_count",
            "footer": "footer_count",
        }.get(content_type, f"{content_type}_count")
        result[key] = type_counts[content_type]
    return result


def document_quality_flags(records: Iterable[Mapping[str, Any]], *, source_size: int) -> list[str]:
    materialized = list(records)
    if not materialized:
        return ["parse_failed", "quality_rejected"]
    metrics = _document_quality_metrics(materialized)
    flags: list[str] = []

    if metrics["replacement_character_count"] >= 2 and metrics["replacement_character_ratio"] >= REPLACEMENT_RATIO_THRESHOLD:
        flags.append("suspected_garbled")
    if metrics["control_character_count"] >= 2 and metrics["control_character_ratio"] >= CONTROL_RATIO_THRESHOLD:
        flags.append("control_characters")
    if metrics["suspected_garbled_block_count"] >= 2 and metrics["suspected_garbled_block_ratio"] >= GARBLED_BLOCK_RATIO_THRESHOLD:
        flags.append("suspected_garbled")
    if metrics["empty_text_block_count"] >= 3 and metrics["empty_text_block_ratio"] >= EMPTY_TEXT_BLOCK_RATIO_THRESHOLD:
        flags.append("empty_text")
    if (
        source_size >= LARGE_SOURCE_SIZE_BYTES
        and metrics["searchable_text_length"] < LARGE_SOURCE_MIN_SEARCHABLE_CHARS
        and metrics["valid_structure_block_count"] == 0
    ):
        flags.append("insufficient_extracted_text")
    if metrics["searchable_text_length"] == 0 and metrics["asset_block_count"] > 0 and metrics["extracted_asset_count"] == 0:
        flags.append("asset_not_extracted")
    if metrics["searchable_text_length"] == 0 and metrics["valid_structure_block_count"] == 0:
        flags.append("parse_failed")

    if flags:
        flags.append("quality_rejected")
    return list(dict.fromkeys(flags))


def _document_quality_metrics(records: Iterable[Mapping[str, Any]]) -> dict[str, int | float]:
    materialized = list(records)
    text = "".join(str(record.get("text", "")) for record in materialized)
    total_characters = len(text)
    replacement_count = text.count("\ufffd")
    control_count = sum(
        1
        for character in text
        if ord(character) < 32 and character not in "\n\t"
    )
    searchable_records = [
        record for record in materialized if record.get("content_type") in _SEARCHABLE_TYPES
    ]
    searchable_text_length = sum(int(record.get("text_length", 0)) for record in searchable_records)
    garbled_count = sum(
        "suspected_garbled" in record.get("quality_flags", []) for record in materialized
    )
    empty_count = sum(
        int(record.get("text_length", 0)) == 0 for record in searchable_records
    )
    asset_records = [record for record in materialized if record.get("content_type") in _ASSET_TYPES]
    extracted_asset_count = sum(bool(record.get("image_uri")) for record in asset_records)
    valid_structure_count = sum(
        1
        for record in materialized
        if record.get("content_type") in {"table_block", "formula_block"}
        and (int(record.get("text_length", 0)) > 0 or bool(record.get("table_uri")))
    )
    return {
        "total_character_count": total_characters,
        "replacement_character_count": replacement_count,
        "replacement_character_ratio": replacement_count / max(1, total_characters),
        "control_character_count": control_count,
        "control_character_ratio": control_count / max(1, total_characters),
        "searchable_text_length": searchable_text_length,
        "searchable_block_count": len(searchable_records),
        "suspected_garbled_block_count": garbled_count,
        "suspected_garbled_block_ratio": garbled_count / max(1, len(materialized)),
        "empty_text_block_count": empty_count,
        "empty_text_block_ratio": empty_count / max(1, len(searchable_records)),
        "asset_block_count": len(asset_records),
        "extracted_asset_count": extracted_asset_count,
        "valid_structure_block_count": valid_structure_count,
    }
