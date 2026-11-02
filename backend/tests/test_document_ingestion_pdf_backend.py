from __future__ import annotations

import json
from pathlib import Path

from document_ingestion.pdf_backend.mineru_adapter import convert_mineru_output
from document_ingestion.pdf_backend.paddleocr_adapter import normalize_ocr_result
from document_ingestion.pdf_backend.parse_eval import evaluate_parse
from document_ingestion.pdf_backend.pdf_parse_pipeline import (
    should_fallback_to_paddleocr,
    validate_pdf,
)


def test_mineru_v1_output_maps_to_shared_schema(tmp_path: Path) -> None:
    source_pdf = tmp_path / "source.pdf"
    source_pdf.write_bytes(b"%PDF synthetic")
    auto_dir = tmp_path / "mineru" / "source" / "auto"
    auto_dir.mkdir(parents=True)
    (auto_dir / "source_content_list.json").write_text(
        json.dumps(
            [
                {
                    "type": "text",
                    "text": "第一章 项目概述",
                    "text_level": 1,
                    "bbox": [1, 2, 3, 4],
                    "page_idx": 0,
                },
                {
                    "type": "table",
                    "text": "<table><tr><td>值</td></tr></table>",
                    "page_idx": 0,
                },
            ],
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    output_root = tmp_path / "output"
    result = convert_mineru_output(
        mineru_dir=auto_dir,
        base_dir=output_root,
        doc_id="doc_test_pdf",
        source_pdf=source_pdf,
        overwrite=True,
    )
    records = [
        json.loads(line)
        for line in Path(result["parsed_jsonl"]).read_text(encoding="utf-8").splitlines()
    ]
    assert [record["content_type"] for record in records] == ["title", "table_block"]
    assert all(record["schema_version"] == "parsed_pdf_unit_v1" for record in records)
    assert all(record["source_format"] == "pdf" for record in records)
    assert all(record["block_id"] == record["parse_unit_id"] for record in records)
    assert all(record["text_length"] == len(record["text"]) for record in records)


def test_mineru_v2_page_nested_output_preserves_page_order(tmp_path: Path) -> None:
    source_pdf = tmp_path / "source.pdf"
    source_pdf.write_bytes(b"%PDF synthetic")
    auto_dir = tmp_path / "auto"
    auto_dir.mkdir()
    (auto_dir / "source_content_list_v2.json").write_text(
        json.dumps(
            [
                [
                    {"type": "title", "content": {"title_content": "标题", "level": 1}},
                    {"type": "text", "content": {"paragraph_content": "正文"}},
                ],
                [
                    {"type": "equation", "content": {"latex": "x=1"}},
                    {
                        "type": "table",
                        "content": {
                            "image_source": {"path": "images/table.jpg"},
                            "html": "",
                        },
                    },
                ],
            ],
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    result = convert_mineru_output(
        mineru_dir=auto_dir,
        base_dir=tmp_path / "output",
        doc_id="doc_v2",
        source_pdf=source_pdf,
        overwrite=True,
    )
    records = [
        json.loads(line)
        for line in Path(result["parsed_jsonl"]).read_text(encoding="utf-8").splitlines()
    ]
    assert [(record["page_no"], record["content_type"]) for record in records] == [
        (1, "title"),
        (1, "paragraph"),
        (2, "formula_block"),
        (2, "table_block"),
    ]
    table = records[-1]
    assert table["table_uri"].endswith("images\\table.jpg") or table["table_uri"].endswith("images/table.jpg")


def test_parse_eval_requests_ocr_for_low_text_coverage(tmp_path: Path) -> None:
    parsed = tmp_path / "parsed.jsonl"
    records = [
        {
            "schema_version": "parsed_pdf_unit_v1",
            "parse_unit_id": f"doc_p{page:04d}",
            "doc_id": "doc",
            "source_uri": "source.pdf",
            "source_name": "source.pdf",
            "source_format": "pdf",
            "page_no": page,
            "block_id": f"doc_p{page:04d}",
            "content_type": "paragraph",
            "text": "" if page > 1 else "正文",
            "text_length": 0 if page > 1 else 2,
            "image_uri": None,
            "table_uri": None,
            "parser": "mineru",
            "parse_version": "test",
            "ocr_used": False,
            "quality_score": None,
            "quality_flags": [],
            "created_at": "2026-08-02T00:00:00+00:00",
            "extra": {},
        }
        for page in range(1, 5)
    ]
    parsed.write_text(
        "\n".join(json.dumps(record, ensure_ascii=False) for record in records) + "\n",
        encoding="utf-8",
    )
    profile = tmp_path / "profile.json"
    profile.write_text(
        json.dumps({"doc_id": "doc", "page_count": 4, "parser": "mineru"}),
        encoding="utf-8",
    )
    report = evaluate_parse(
        parsed_jsonl=parsed,
        profile_path=profile,
        output_path=tmp_path / "eval" / "report.json",
        base_dir=tmp_path,
        min_page_text_len=1,
    )
    assert report["quality_decision"]["quality_level"] == "need_ocr"
    assert report["quality_decision"]["can_enter_spark_cleaning"] is False
    assert should_fallback_to_paddleocr(report) == (True, "quality_level_need_ocr")


def test_paddleocr_normalizer_supports_v2_and_v3_shapes() -> None:
    old = [[[[0, 0], [1, 0], [1, 1], [0, 1]], ("旧格式", 0.9)]]
    new = {"res": {"rec_texts": ["新格式"], "rec_scores": [0.8], "rec_boxes": [[1, 2, 3, 4]]}}
    assert normalize_ocr_result(old)[0]["text"] == "旧格式"
    assert normalize_ocr_result(new)[0]["text"] == "新格式"


def test_pdf_validation_never_renames_source(tmp_path: Path) -> None:
    source = tmp_path / "report.pdf.pdf"
    source.write_bytes(b"%PDF")
    assert validate_pdf(source) == source.resolve()
    assert source.exists()
    assert not (tmp_path / "report.pdf").exists()
