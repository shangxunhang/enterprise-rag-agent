"""Compatibility contract shared with the existing MinerU adapter output."""

from __future__ import annotations

import json
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any, Iterable, Mapping

from .exceptions import ContractValidationError

SCHEMA_VERSION = "parsed_pdf_unit_v1"
PARSER_NAME = "word"
PARSE_VERSION = "word_adapter_v0.1"

TOP_LEVEL_FIELDS = (
    "schema_version",
    "parse_unit_id",
    "doc_id",
    "source_uri",
    "source_name",
    "source_format",
    "page_no",
    "block_id",
    "content_type",
    "text",
    "text_length",
    "image_uri",
    "table_uri",
    "parser",
    "parse_version",
    "ocr_used",
    "quality_score",
    "quality_flags",
    "created_at",
    "extra",
)

WORD_CONTENT_TYPES = frozenset(
    {
        "title",
        "paragraph",
        "list",
        "header",
        "footer",
        "table_block",
        "image_block",
        "formula_block",
        "chart_block",
    }
)


def validate_record(record: Mapping[str, Any], *, word_output: bool = False) -> None:
    """Validate one raw parsed content unit without changing its schema."""

    actual_fields = set(record)
    expected_fields = set(TOP_LEVEL_FIELDS)
    if actual_fields != expected_fields:
        missing = sorted(expected_fields - actual_fields)
        extra = sorted(actual_fields - expected_fields)
        raise ContractValidationError(f"top-level fields mismatch: missing={missing}, extra={extra}")
    if record["schema_version"] != SCHEMA_VERSION:
        raise ContractValidationError("schema_version must remain parsed_pdf_unit_v1")
    if not isinstance(record["parse_unit_id"], str) or not record["parse_unit_id"]:
        raise ContractValidationError("parse_unit_id must be a non-empty string")
    if record["block_id"] != record["parse_unit_id"]:
        raise ContractValidationError("block_id must equal parse_unit_id")
    if not isinstance(record["page_no"], int) or isinstance(record["page_no"], bool) or record["page_no"] < 1:
        raise ContractValidationError("page_no must be an integer >= 1")
    if not isinstance(record["text"], str):
        raise ContractValidationError("text must be a string")
    if record["text_length"] != len(record["text"]):
        raise ContractValidationError("text_length must equal len(text)")
    if record["image_uri"] is not None and not isinstance(record["image_uri"], str):
        raise ContractValidationError("image_uri must be a string or null")
    if record["table_uri"] is not None and not isinstance(record["table_uri"], str):
        raise ContractValidationError("table_uri must be a string or null")
    score = record["quality_score"]
    if score is not None and (isinstance(score, bool) or not isinstance(score, (int, float)) or not 0 <= score <= 1):
        raise ContractValidationError("quality_score must be null or a number in [0, 1]")
    if not isinstance(record["quality_flags"], list) or not all(
        isinstance(flag, str) for flag in record["quality_flags"]
    ):
        raise ContractValidationError("quality_flags must be an array of strings")
    if not isinstance(record["extra"], dict):
        raise ContractValidationError("extra must be an object")
    if word_output:
        if record["parser"] != PARSER_NAME or record["parse_version"] != PARSE_VERSION:
            raise ContractValidationError("Word parser identity is invalid")
        if record["ocr_used"] is not False:
            raise ContractValidationError("Word parsing must not claim OCR usage")
        if record["source_format"] not in {"doc", "docx"}:
            raise ContractValidationError("Word source_format must be doc or docx")
        if record["content_type"] not in WORD_CONTENT_TYPES:
            raise ContractValidationError(f"unsupported Word content_type: {record['content_type']}")


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    """Read every non-empty JSONL line and validate its JSON object shape."""

    records: list[dict[str, Any]] = []
    with path.open("r", encoding="utf-8") as handle:
        for line_number, line in enumerate(handle, 1):
            if not line.strip():
                continue
            try:
                value = json.loads(line)
            except json.JSONDecodeError as exc:
                raise ContractValidationError(f"invalid JSON at line {line_number}: {exc}") from exc
            if not isinstance(value, dict):
                raise ContractValidationError(f"line {line_number} is not a JSON object")
            records.append(value)
    return records


def summarize_contract(records: Iterable[Mapping[str, Any]]) -> dict[str, Any]:
    """Return deterministic structural statistics for a parsed JSONL contract."""

    materialized = list(records)
    if not materialized:
        return {"record_count": 0}
    field_types: dict[str, set[str]] = defaultdict(set)
    nullable: Counter[str] = Counter()
    extra_keys: set[str] = set()
    for record in materialized:
        for field in TOP_LEVEL_FIELDS:
            value = record.get(field)
            if value is None:
                nullable[field] += 1
            else:
                field_types[field].add(type(value).__name__)
        extra = record.get("extra")
        if isinstance(extra, dict):
            extra_keys.update(extra)
    return {
        "record_count": len(materialized),
        "top_level_fields": list(materialized[0]),
        "schema_versions": dict(Counter(record.get("schema_version") for record in materialized)),
        "content_types": dict(Counter(record.get("content_type") for record in materialized)),
        "field_types": {key: sorted(values) for key, values in field_types.items()},
        "nullable_fields": dict(nullable),
        "extra_keys": sorted(extra_keys),
        "parse_unit_id_first": materialized[0].get("parse_unit_id"),
        "parse_unit_id_last": materialized[-1].get("parse_unit_id"),
        "block_id_equals_parse_unit_id": all(
            record.get("block_id") == record.get("parse_unit_id") for record in materialized
        ),
    }
