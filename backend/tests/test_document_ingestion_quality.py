from __future__ import annotations

from document_ingestion.quality_checker import document_quality_flags, document_statistics


def _record(
    text: str,
    *,
    content_type: str = "paragraph",
    quality_flags: list[str] | None = None,
    image_uri: str | None = None,
    table_uri: str | None = None,
) -> dict[str, object]:
    return {
        "content_type": content_type,
        "text": text,
        "text_length": len(text),
        "quality_flags": quality_flags or [],
        "page_no": 1,
        "image_uri": image_uri,
        "table_uri": table_uri,
    }


def test_document_quality_rejects_large_replacement_character_ratio() -> None:
    records = [_record("正常文本����") for _ in range(3)]
    flags = document_quality_flags(records, source_size=1_000)
    assert "suspected_garbled" in flags
    assert "quality_rejected" in flags


def test_document_quality_rejects_control_character_ratio() -> None:
    records = [_record("正常\x03\x04文本") for _ in range(3)]
    flags = document_quality_flags(records, source_size=1_000)
    assert "control_characters" in flags
    assert "quality_rejected" in flags


def test_document_quality_rejects_many_garbled_blocks() -> None:
    records = [
        _record("乱码块一", quality_flags=["suspected_garbled"]),
        _record("乱码块二", quality_flags=["suspected_garbled"]),
        _record("正常块"),
    ]
    flags = document_quality_flags(records, source_size=1_000)
    assert "suspected_garbled" in flags
    assert "quality_rejected" in flags


def test_document_quality_rejects_large_source_with_tiny_extraction() -> None:
    flags = document_quality_flags([_record("少量正文")], source_size=200_000)
    assert "insufficient_extracted_text" in flags
    assert "quality_rejected" in flags


def test_document_quality_rejects_asset_only_document_when_assets_missing() -> None:
    records = [
        _record("", content_type="image_block", quality_flags=["asset_not_extracted"]),
        _record("", content_type="chart_block", quality_flags=["asset_not_extracted"]),
    ]
    flags = document_quality_flags(records, source_size=10_000)
    assert "asset_not_extracted" in flags
    assert "quality_rejected" in flags


def test_short_legitimate_document_still_passes_and_exposes_metrics() -> None:
    records = [_record("年CO₂排放量计算结果为12.34吨。")]
    assert document_quality_flags(records, source_size=14_811) == []
    statistics = document_statistics(records)
    metrics = statistics["quality_metrics"]
    assert metrics["searchable_text_length"] == len(records[0]["text"])
    assert metrics["replacement_character_count"] == 0
    assert metrics["control_character_count"] == 0
