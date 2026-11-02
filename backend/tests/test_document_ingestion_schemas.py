from __future__ import annotations

from document_ingestion.contract import SCHEMA_VERSION, TOP_LEVEL_FIELDS
from document_ingestion.schemas import ParsedContentUnitV1


def make_unit(**overrides: object) -> ParsedContentUnitV1:
    values = {
        "parse_unit_id": "doc_abc_p0002_u0003_paragraph",
        "doc_id": "doc_abc",
        "source_uri": r"D:\raw\abc.doc",
        "source_name": "abc.doc",
        "source_format": "doc",
        "page_no": 2,
        "block_id": "doc_abc_p0002_u0003_paragraph",
        "content_type": "paragraph",
        "text": "正文 123",
        "created_at": "2026-08-02T00:00:00+00:00",
        "extra": {"word_type": "paragraph", "unit_order_in_page": 3, "bbox": None},
        "quality_score": 0.9,
    }
    values.update(overrides)
    return ParsedContentUnitV1(**values)  # type: ignore[arg-type]


def test_word_unit_serializes_exact_shared_schema() -> None:
    record = make_unit().to_dict()
    assert tuple(record) == TOP_LEVEL_FIELDS
    assert record["schema_version"] == SCHEMA_VERSION
    assert record["parser"] == "word"
    assert record["parse_version"] == "word_adapter_v0.1"
    assert record["ocr_used"] is False
    assert record["block_id"] == record["parse_unit_id"]
    assert record["text_length"] == len(record["text"])
    assert set(record["source_format"]) <= set("docx")


def test_created_at_can_change_without_identity_drift() -> None:
    first = make_unit(created_at="2026-08-02T00:00:00+00:00").to_dict()
    second = make_unit(created_at="2026-08-03T00:00:00+00:00").to_dict()
    for field in ("doc_id", "parse_unit_id", "block_id", "page_no"):
        assert first[field] == second[field]
    assert first["created_at"] != second["created_at"]
