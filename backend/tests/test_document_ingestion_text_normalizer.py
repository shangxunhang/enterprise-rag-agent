from __future__ import annotations

from pathlib import Path

from document_ingestion.config import load_document_config
from document_ingestion.text_normalizer import normalize_word_text


def test_word_caption_separator_and_controls_are_normalized() -> None:
    text, operations = normalize_word_text("表3\x1e1 业务应用系统现状表\r下一行\x01")
    assert text == "表3-1 业务应用系统现状表\n下一行"
    assert "normalized_word_caption_separator" in operations
    assert "normalized_word_line_breaks" in operations
    assert "removed_other_control_characters" in operations
    assert all(ord(character) >= 32 or character in {"\n", "\t"} for character in text)


def test_word_cell_markers_and_zero_width_characters_are_removed() -> None:
    text, operations = normalize_word_text("\u200b设备\r\x07数量\ufeff")
    assert text == "设备\n数量"
    assert "removed_zero_width_characters" in operations
    assert "removed_word_cell_markers" in operations


def test_word_full_v2_config_excludes_gold_reference_from_index() -> None:
    import os
    import pytest
    local_config = os.environ.get("RAG_LOCAL_WORD_FULL_V2_CONFIG")
    historical_filename = os.environ.get("RAG_LOCAL_HISTORICAL_SCHEME_FILENAME")
    if not local_config or not historical_filename:
        pytest.skip("requires original local Word config and reference filename; see docs/publication/LOCAL_ARCHIVE_TESTS.md")
    config_path = Path(local_config)
    configs = load_document_config(config_path)
    gold = configs[historical_filename]
    assert gold.governance == {
        "project_id": _local_historical_project_id(),
        "document_role": "gold_reference",
        "document_version": "0702",
        "is_gold_reference": True,
        "should_index": False,
    }


def _local_historical_project_id() -> str:
    """Original historical identity is private; never synthesize a replacement."""
    import os
    import pytest
    value = os.environ.get("RAG_LOCAL_HISTORICAL_PROJECT_ID")
    if not value:
        pytest.skip("requires local archive identity: RAG_LOCAL_HISTORICAL_PROJECT_ID")
    return value
