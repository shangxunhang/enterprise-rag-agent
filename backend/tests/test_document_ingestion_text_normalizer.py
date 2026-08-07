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
    config_path = (
        Path(__file__).resolve().parents[2]
        / "document_ingestion"
        / "configs"
        / "word_full_v2.document-config.json"
    )
    configs = load_document_config(config_path)
    gold = configs["05.陕西省大件运输许可相关系统升级项目实施方案0702.doc"]
    assert gold.governance == {
        "project_id": "dajian_transport",
        "document_role": "gold_reference",
        "document_version": "0702",
        "is_gold_reference": True,
        "should_index": False,
    }
