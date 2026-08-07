from __future__ import annotations

import json
from pathlib import Path

from document_ingestion.hashing import default_doc_id, sha256_file
from document_ingestion.pipeline import IngestionPipeline
from document_ingestion.schemas import ParsedContentUnitV1


class CountingWord:
    created_count = 0

    def __init__(self) -> None:
        type(self).created_count += 1
        self.owned_process_id = type(self).created_count
        self.process_exited = None

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, traceback) -> None:
        self.process_exited = True


class SimpleParser:
    def __init__(self, output_root: Path) -> None:
        self.output_root = output_root

    def parse(self, parse_path: Path, **kwargs: object) -> list[ParsedContentUnitV1]:
        source_path = kwargs["source_path"]
        doc_id = str(kwargs["doc_id"])
        parse_unit_id = f"{doc_id}_p0001_u0001_paragraph"
        return [
            ParsedContentUnitV1(
                parse_unit_id=parse_unit_id,
                doc_id=doc_id,
                source_uri=str(source_path),
                source_name=source_path.name,
                source_format=str(kwargs["source_format"]),
                page_no=1,
                block_id=parse_unit_id,
                content_type="paragraph",
                text="有效正文内容，足以通过确定性质量门。",
                quality_score=0.9,
                created_at=str(kwargs["created_at"]),
                extra={
                    "word_type": "paragraph",
                    "unit_order_in_page": 1,
                    "bbox": None,
                    "language": "zh",
                    "title_level": None,
                    "style_name": "正文",
                    "outline_level": 10,
                    "list_level": None,
                    "source_range_start": 0,
                    "source_range_end": 20,
                    "page_end": 1,
                },
            )
        ]


def _pipeline(**kwargs: object) -> IngestionPipeline:
    return IngestionPipeline(
        word_application_factory=CountingWord,  # type: ignore[arg-type]
        parser_factory=SimpleParser,  # type: ignore[arg-type]
        **kwargs,
    )


def _config(path: Path, source_name: str, doc_id: str) -> Path:
    path.write_text(
        json.dumps({"source_name": source_name, "doc_id": doc_id}, ensure_ascii=False),
        encoding="utf-8",
    )
    return path


def test_second_run_is_skipped_and_formal_output_bytes_do_not_change(tmp_path: Path) -> None:
    CountingWord.created_count = 0
    input_root = tmp_path / "input"
    input_root.mkdir()
    source = input_root / "source.docx"
    source.write_bytes(b"same source")
    output_root = tmp_path / "output"

    first = _pipeline().run(input_dir=input_root, output_dir=output_root)
    assert first.documents[0]["status"] == "success"
    doc_id = first.documents[0]["doc_id"]
    parsed = output_root / "parsed" / f"{doc_id}.word.parsed.jsonl"
    identity = output_root / "identities" / f"{doc_id}.source_identity.json"
    parsed_before = parsed.read_bytes()
    identity_before = identity.read_bytes()

    second = _pipeline().run(input_dir=input_root, output_dir=output_root)
    assert second.documents[0]["status"] == "skipped_unchanged"
    assert second.success_count == 1
    assert parsed.read_bytes() == parsed_before
    assert identity.read_bytes() == identity_before
    assert CountingWord.created_count == 1


def test_cross_batch_different_sha_same_explicit_doc_id_fails_closed(tmp_path: Path) -> None:
    CountingWord.created_count = 0
    input_root = tmp_path / "input"
    input_root.mkdir()
    source = input_root / "source.docx"
    source.write_bytes(b"source one")
    output_root = tmp_path / "output"
    config = _config(tmp_path / "config.json", source.name, "doc_fixed")

    first = _pipeline().run(input_dir=input_root, output_dir=output_root, document_config=config)
    assert first.documents[0]["status"] == "success"
    parsed = output_root / "parsed" / "doc_fixed.word.parsed.jsonl"
    identity = output_root / "identities" / "doc_fixed.source_identity.json"
    parsed_before = parsed.read_bytes()
    identity_before = identity.read_bytes()

    source.write_bytes(b"source two")
    second = _pipeline().run(input_dir=input_root, output_dir=output_root, document_config=config)
    assert second.documents[0]["status"] == "failed"
    assert second.documents[0]["error"]["type"] == "DocIdCollisionError"
    assert parsed.read_bytes() == parsed_before
    assert identity.read_bytes() == identity_before
    assert CountingWord.created_count == 1


def test_same_sha_different_filenames_produces_one_output_and_one_skip(tmp_path: Path) -> None:
    CountingWord.created_count = 0
    input_root = tmp_path / "input"
    input_root.mkdir()
    (input_root / "a.docx").write_bytes(b"identical")
    (input_root / "b.docx").write_bytes(b"identical")
    output_root = tmp_path / "output"

    result = _pipeline().run(input_dir=input_root, output_dir=output_root)
    statuses = [document["status"] for document in result.documents]
    assert statuses == ["success", "skipped_unchanged"]
    assert len(list((output_root / "parsed").glob("*.jsonl"))) == 1
    assert CountingWord.created_count == 1
    record = json.loads(next((output_root / "parsed").glob("*.jsonl")).read_text(encoding="utf-8"))
    assert record["source_name"] == "a.docx"


def test_existing_formal_output_without_identity_fails_closed(tmp_path: Path) -> None:
    CountingWord.created_count = 0
    input_root = tmp_path / "input"
    input_root.mkdir()
    source = input_root / "source.docx"
    source.write_bytes(b"source")
    digest = sha256_file(source)
    doc_id = default_doc_id(digest)
    output_root = tmp_path / "output"
    parsed_root = output_root / "parsed"
    parsed_root.mkdir(parents=True)
    parsed = parsed_root / f"{doc_id}.word.parsed.jsonl"
    parsed.write_bytes(b"legacy formal output")

    result = _pipeline().run(input_dir=input_root, output_dir=output_root)
    assert result.documents[0]["status"] == "failed"
    assert result.documents[0]["error"]["type"] == "OutputIdentityMissingError"
    assert parsed.read_bytes() == b"legacy formal output"
    assert not (output_root / "identities" / f"{doc_id}.source_identity.json").exists()
    assert CountingWord.created_count == 0


def test_identity_creation_failure_does_not_publish_parsed_output(tmp_path: Path) -> None:
    input_root = tmp_path / "input"
    input_root.mkdir()
    (input_root / "source.docx").write_bytes(b"source")
    output_root = tmp_path / "output"

    class FailingStore:
        def claim(self, **kwargs: object):
            raise OSError("simulated identity publication failure")

    result = _pipeline(identity_store_factory=lambda _: FailingStore()).run(  # type: ignore[arg-type]
        input_dir=input_root,
        output_dir=output_root,
    )
    assert result.documents[0]["status"] == "failed"
    assert not list((output_root / "parsed").glob("*.jsonl"))
