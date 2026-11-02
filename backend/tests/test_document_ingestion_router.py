from __future__ import annotations

import json
import subprocess
from pathlib import Path

from document_ingestion.document_router import DocumentRouter
from document_ingestion.pipeline import BatchResult


class FakeWordPipeline:
    def run(self, *, input_dir: Path, output_dir: Path, include, progress=None, **kwargs):
        documents = []
        for name in include:
            if progress:
                progress(f"fake parse {name}")
            source = input_dir / name
            doc_id = "doc_" + ("a" * 24)
            documents.append(
                {
                    "source_name": name,
                    "status": "success",
                    "parsed_uri": f"parsed/{doc_id}.word.parsed.jsonl",
                    "normalized_uri": None,
                    "statistics": {"record_count": 1, "page_count": 1},
                    "quality_flags": [],
                    "word_process_id": 123,
                    "word_process_exited": True,
                    "error": None,
                }
            )
        return BatchResult("2026-08-02T00:00:00+00:00", False, tuple(documents))


def test_router_dry_run_routes_supported_extensions(tmp_path: Path) -> None:
    input_root = tmp_path / "input"
    input_root.mkdir()
    (input_root / "a.docx").write_bytes(b"word")
    (input_root / "b.pdf").write_bytes(b"pdf")
    (input_root / "ignored.txt").write_text("ignored", encoding="utf-8")

    result = DocumentRouter().run(
        input_dir=input_root,
        output_dir=tmp_path / "output",
        dry_run=True,
    )

    assert result.exit_code == 0
    assert len(result.documents) == 2
    assert {document["backend"] for document in result.documents} == {"word", "pdf"}
    assert {document["status"] for document in result.documents} == {"planned"}
    assert not (tmp_path / "output").exists()


def test_router_combines_word_pdf_and_governance_manifest(tmp_path: Path) -> None:
    input_root = tmp_path / "input"
    input_root.mkdir()
    word_source = input_root / "gold.docx"
    pdf_source = input_root / "evidence.pdf"
    word_source.write_bytes(b"word")
    pdf_source.write_bytes(b"pdf")
    config = tmp_path / "config.json"
    config.write_text(
        json.dumps(
            {
                "documents": [
                    {
                        "source_name": "gold.docx",
                        "document_role": "gold_reference",
                        "should_index": False,
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    output_root = tmp_path / "output"

    def fake_subprocess(command, *, cwd, text, check):
        base_dir = Path(command[command.index("--base-dir") + 1])
        doc_id = command[command.index("--doc-id") + 1]
        (base_dir / "parsed_jsonl").mkdir(parents=True, exist_ok=True)
        (base_dir / "profile").mkdir(parents=True, exist_ok=True)
        (base_dir / "eval_report").mkdir(parents=True, exist_ok=True)
        (base_dir / "parsed_jsonl" / f"{doc_id}.parsed.jsonl").write_text(
            json.dumps({"doc_id": doc_id}) + "\n", encoding="utf-8"
        )
        (base_dir / "profile" / f"{doc_id}.profile.json").write_text(
            json.dumps({"doc_id": doc_id, "parser": "mineru"}), encoding="utf-8"
        )
        (base_dir / "eval_report" / f"{doc_id}.parse_eval.json").write_text(
            json.dumps(
                {
                    "basic_validity": {"total_records": 1},
                    "coverage": {"page_count": 1, "text_coverage_ratio": 1.0},
                    "quality_decision": {
                        "quality_level": "pass",
                        "can_enter_spark_cleaning": True,
                        "fail_reasons": [],
                        "warning_reasons": [],
                        "need_ocr_reasons": [],
                        "need_review_reasons": [],
                    },
                }
            ),
            encoding="utf-8",
        )
        return subprocess.CompletedProcess(command, 0)

    router = DocumentRouter(
        word_pipeline_factory=FakeWordPipeline,
        subprocess_runner=fake_subprocess,
    )
    result = router.run(
        input_dir=input_root,
        output_dir=output_root,
        document_config=config,
        pdf_python=Path(__file__),
    )

    assert result.exit_code == 0
    assert {document["backend"] for document in result.documents} == {"word", "pdf"}
    word = next(document for document in result.documents if document["backend"] == "word")
    pdf = next(document for document in result.documents if document["backend"] == "pdf")
    assert word["governance"]["should_index"] is False
    assert word["parsed_uri"].startswith("word/parsed/")
    assert pdf["final_parser"] == "mineru"
    assert pdf["parsed_uri"].startswith("pdf/parsed_jsonl/")

    report = json.loads(
        (output_root / "reports" / "document_ingestion_report.json").read_text(
            encoding="utf-8"
        )
    )
    assert report["backend_counts"] == {"pdf": 1, "word": 1}
    manifest = [
        json.loads(line)
        for line in (output_root / "document_manifest.jsonl").read_text(
            encoding="utf-8"
        ).splitlines()
    ]
    assert len(manifest) == 2
    assert next(item for item in manifest if item["backend"] == "word")["governance"][
        "document_role"
    ] == "gold_reference"
    assert len(list((output_root / "identities").glob("*.json"))) == 2


def test_router_reports_missing_pdf_runtime_without_touching_word(tmp_path: Path) -> None:
    input_root = tmp_path / "input"
    input_root.mkdir()
    (input_root / "only.pdf").write_bytes(b"pdf")
    result = DocumentRouter().run(
        input_dir=input_root,
        output_dir=tmp_path / "output",
        pdf_python=tmp_path / "missing-python.exe",
    )
    assert result.exit_code == 1
    assert result.documents[0]["status"] == "failed"
    assert result.documents[0]["error"]["type"] == "PdfBackendError"
