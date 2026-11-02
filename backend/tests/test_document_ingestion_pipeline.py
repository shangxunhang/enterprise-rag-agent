from __future__ import annotations

import json
from pathlib import Path

from document_ingestion.exceptions import ParseError
from document_ingestion.pipeline import IngestionPipeline
from document_ingestion.schemas import ParsedContentUnitV1


class FakeWord:
    next_pid = 1000

    def __init__(self) -> None:
        self.owned_process_id = FakeWord.next_pid
        FakeWord.next_pid += 1
        self.process_exited = None

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, traceback) -> None:
        self.process_exited = True


class FakeParser:
    def __init__(self, output_root: Path) -> None:
        self.output_root = output_root

    def parse(self, parse_path: Path, **kwargs: object) -> list[ParsedContentUnitV1]:
        source_path = kwargs["source_path"]
        if source_path.name == "broken.docx":
            raise ParseError("simulated corrupt Word document")
        doc_id = str(kwargs["doc_id"])
        parse_unit_id = f"{doc_id}_p0001_u0001_paragraph"
        return [
            ParsedContentUnitV1(
                parse_unit_id=parse_unit_id,
                doc_id=doc_id,
                source_uri=str(source_path),
                source_name=source_path.name,
                source_format="docx",
                page_no=1,
                block_id=parse_unit_id,
                content_type="paragraph",
                text=f"valid body for {source_path.name}",
                quality_score=0.9,
                created_at=str(kwargs["created_at"]),
                extra={
                    "word_type": "paragraph",
                    "unit_order_in_page": 1,
                    "bbox": None,
                    "language": "en",
                    "title_level": None,
                    "style_name": "Normal",
                    "outline_level": 10,
                    "list_level": None,
                    "source_range_start": 0,
                    "source_range_end": 10,
                    "page_end": 1,
                },
            )
        ]


def test_batch_failure_isolation_and_nonzero_exit(tmp_path: Path) -> None:
    input_root = tmp_path / "input"
    input_root.mkdir()
    for name in ("one.docx", "broken.docx", "two.docx"):
        (input_root / name).write_bytes((name + " source").encode())
    output_root = tmp_path / "output"
    pipeline = IngestionPipeline(
        word_application_factory=FakeWord,  # type: ignore[arg-type]
        parser_factory=FakeParser,  # type: ignore[arg-type]
    )
    result = pipeline.run(input_dir=input_root, output_dir=output_root)
    assert result.exit_code == 1
    assert result.success_count == 2
    assert result.failure_count == 1
    assert sorted(path.name for path in (output_root / "parsed").glob("*.jsonl")) == sorted(
        f"{document['doc_id']}.word.parsed.jsonl"
        for document in result.documents
        if document["status"] == "success"
    )
    report = json.loads((output_root / "reports" / "ingestion_report.json").read_text(encoding="utf-8"))
    assert report["success_count"] == 2
    assert report["failure_count"] == 1
    broken = next(document for document in report["documents"] if document["source_name"] == "broken.docx")
    assert broken["quality_flags"] == ["parse_failed"]
    assert all(
        document["word_process_exited"] is True
        for document in result.documents
        if document["status"] in {"success", "failed"}
    )
    manifest_lines = (output_root / "manifest.jsonl").read_text(encoding="utf-8").splitlines()
    assert len(manifest_lines) == 3
    assert all("valid body" not in line for line in manifest_lines)
    assert all((output_root / name).is_dir() for name in ("normalized", "parsed", "images", "tables", "reports"))


def test_progress_callback_reports_discovery_and_per_file_status(tmp_path: Path) -> None:
    input_root = tmp_path / "input"
    input_root.mkdir()
    (input_root / "only.docx").write_bytes(b"source")
    messages: list[str] = []

    result = IngestionPipeline().run(
        input_dir=input_root,
        output_dir=tmp_path / "output",
        dry_run=True,
        progress=messages.append,
    )

    assert result.exit_code == 0
    assert messages[0] == "Discovered 1 Word file(s)."
    assert any("[1/1] hashing only.docx" in message for message in messages)
    assert any("[1/1] planned only.docx" in message for message in messages)


def test_dry_run_never_starts_word_or_writes_output(tmp_path: Path) -> None:
    input_root = tmp_path / "input"
    input_root.mkdir()
    (input_root / "only.docx").write_bytes(b"source")
    output_root = tmp_path / "output"

    def forbidden_word():
        raise AssertionError("dry-run must not start Word")

    result = IngestionPipeline(word_application_factory=forbidden_word).run(
        input_dir=input_root,
        output_dir=output_root,
        dry_run=True,
    )
    assert result.exit_code == 0
    assert result.documents[0]["status"] == "planned"
    assert not output_root.exists()
