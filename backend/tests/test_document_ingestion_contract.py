from __future__ import annotations

import json
from pathlib import Path

import pytest

from document_ingestion.contract import (
    SCHEMA_VERSION,
    TOP_LEVEL_FIELDS,
    read_jsonl,
    summarize_contract,
    validate_record,
)


def synthetic_record(content_type: str = "paragraph") -> dict[str, object]:
    text = {"title": "标题", "paragraph": "正文", "list": "- 项目"}.get(content_type, "")
    parse_unit_id = f"doc_fixture_p0001_u0001_{content_type}"
    return {
        "schema_version": SCHEMA_VERSION,
        "parse_unit_id": parse_unit_id,
        "doc_id": "doc_fixture",
        "source_uri": r"D:\fixtures\fixture.docx",
        "source_name": "fixture.docx",
        "source_format": "docx",
        "page_no": 1,
        "block_id": parse_unit_id,
        "content_type": content_type,
        "text": text,
        "text_length": len(text),
        "image_uri": None,
        "table_uri": None,
        "parser": "word",
        "parse_version": "word_adapter_v0.1",
        "ocr_used": False,
        "quality_score": 0.9,
        "quality_flags": [],
        "created_at": "2026-08-02T00:00:00+00:00",
        "extra": {"word_type": content_type, "unit_order_in_page": 1, "bbox": None},
    }


def test_synthetic_jsonl_contract_covers_shared_vocabulary(tmp_path: Path) -> None:
    content_types = [
        "title",
        "paragraph",
        "list",
        "image_block",
        "header",
        "footer",
        "formula_block",
        "chart_block",
    ]
    path = tmp_path / "fixture.jsonl"
    path.write_text(
        "".join(json.dumps(synthetic_record(value), ensure_ascii=False) + "\n" for value in content_types),
        encoding="utf-8",
    )
    records = read_jsonl(path)
    assert len(records) == len(content_types)
    for record in records:
        assert tuple(record) == TOP_LEVEL_FIELDS
        validate_record(record, word_output=True)


def test_real_mineru_contract_if_locally_available() -> None:
    contract_path = (
        Path(__file__).resolve().parents[2]
        / "document_ingestion"
        / "contracts"
        / "doc_001_native_text.mineru.parsed.jsonl"
    )
    if not contract_path.exists():
        pytest.skip("large local MinerU compatibility sample is intentionally not a test dependency")
    records = read_jsonl(contract_path)
    summary = summarize_contract(records)
    assert summary["record_count"] == 1183
    assert summary["schema_versions"] == {"parsed_pdf_unit_v1": 1183}
    assert set(summary["top_level_fields"]) == set(TOP_LEVEL_FIELDS)
    assert summary["block_id_equals_parse_unit_id"] is True
    assert {record["content_type"] for record in records} >= {
        "title",
        "paragraph",
        "list",
        "image_block",
        "header",
        "footer",
        "formula_block",
        "chart_block",
    }


def test_contract_rejects_new_top_level_fields() -> None:
    record = synthetic_record()
    record["word_style"] = "正文"
    with pytest.raises(Exception, match="top-level fields mismatch"):
        validate_record(record, word_output=True)
