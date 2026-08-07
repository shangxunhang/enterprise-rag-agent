"""Serial, failure-isolated Word ingestion pipeline."""

from __future__ import annotations

import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Iterable

from .atomic_writer import atomic_write_jsonl
from .config import DocumentConfig, load_document_config
from .contract import validate_record
from .exceptions import (
    ConversionError,
    DocumentIngestionError,
    PageNumberUnavailableError,
    ParseError,
    QualityRejectedError,
)
from .hashing import default_doc_id, sha256_file
from .manifest import write_batch_metadata
from .quality_checker import document_quality_flags, document_statistics
from .safe_paths import assert_within, safe_child, validate_safe_component
from .source_identity import SourceIdentityStore
from .word_application import WordApplication
from .word_converter import WordConverter
from .word_parser import WordParser


@dataclass(frozen=True, slots=True)
class BatchResult:
    created_at: str
    dry_run: bool
    documents: tuple[dict[str, Any], ...]

    @property
    def failure_count(self) -> int:
        return sum(document["status"] == "failed" for document in self.documents)

    @property
    def success_count(self) -> int:
        return sum(
            document["status"] in {"success", "planned", "skipped_unchanged"}
            for document in self.documents
        )

    @property
    def exit_code(self) -> int:
        return 1 if self.failure_count else 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "created_at": self.created_at,
            "dry_run": self.dry_run,
            "document_count": len(self.documents),
            "success_count": self.success_count,
            "failure_count": self.failure_count,
            "documents": list(self.documents),
        }


class IngestionPipeline:
    def __init__(
        self,
        *,
        word_application_factory: Callable[[], WordApplication] = WordApplication,
        parser_factory: Callable[[Path], WordParser] = WordParser,
        converter_factory: Callable[[Path], WordConverter] = WordConverter,
        identity_store_factory: Callable[[Path], SourceIdentityStore] = SourceIdentityStore,
    ) -> None:
        self.word_application_factory = word_application_factory
        self.parser_factory = parser_factory
        self.converter_factory = converter_factory
        self.identity_store_factory = identity_store_factory

    def run(
        self,
        *,
        input_dir: Path,
        output_dir: Path,
        document_config: Path | None = None,
        dry_run: bool = False,
        include: Iterable[str] | None = None,
        progress: Callable[[str], None] | None = None,
    ) -> BatchResult:
        input_root = input_dir.resolve(strict=True)
        if not input_root.is_dir():
            raise ValueError(f"input directory is not a directory: {input_root}")
        output_root = output_dir.resolve()
        if output_root == input_root or _is_relative_to(output_root, input_root):
            raise ValueError("output directory must not be inside the input directory")
        configs = load_document_config(document_config)
        sources = _discover_sources(input_root, include=include)
        total_sources = len(sources)
        _emit_progress(progress, f"Discovered {total_sources} Word file(s).")
        created_at = datetime.now(timezone.utc).isoformat()
        if not dry_run:
            for directory_name in ("identities", "normalized", "parsed", "images", "tables", "reports"):
                safe_child(output_root, directory_name).mkdir(parents=True, exist_ok=True)
        identities: dict[str, str] = {}
        results: list[dict[str, Any]] = []
        for source_index, source in enumerate(sources, 1):
            started_at = time.perf_counter()
            prefix = f"[{source_index}/{total_sources}]"
            _emit_progress(progress, f"{prefix} hashing {source.name}")
            source_sha256 = sha256_file(source)
            config = configs.get(source.name, DocumentConfig(source.name))
            doc_id = config.doc_id or default_doc_id(source_sha256)
            validate_safe_component(doc_id, label="doc_id")
            source_format = source.suffix.lower().removeprefix(".")
            base = {
                "source_uri": str(source),
                "source_name": source.name,
                "source_format": source_format,
                "source_sha256": source_sha256,
                "source_size": source.stat().st_size,
                "doc_id": doc_id,
                "created_at": created_at,
                "governance": config.governance,
            }
            previous_sha = identities.get(doc_id)
            if previous_sha is not None and previous_sha != source_sha256:
                collision_result = {
                    **base,
                    "status": "failed",
                    "parsed_uri": None,
                    "normalized_uri": None,
                    "statistics": None,
                    "quality_flags": ["parse_failed", "quality_rejected"],
                    "word_process_id": None,
                    "word_process_exited": None,
                    "error": {
                        "type": "DocIdCollision",
                        "message": f"doc_id {doc_id} maps to more than one source SHA-256",
                    },
                }
                results.append(collision_result)
                _emit_progress(
                    progress,
                    f"{prefix} failed DocIdCollision after {time.perf_counter() - started_at:.1f}s",
                )
                continue
            identities[doc_id] = source_sha256
            if dry_run:
                planned_result = {
                    **base,
                    "status": "planned",
                    "parsed_uri": f"parsed/{doc_id}.word.parsed.jsonl",
                    "normalized_uri": f"normalized/{doc_id}.docx" if source_format == "doc" else None,
                    "statistics": None,
                    "quality_flags": [],
                    "word_process_id": None,
                    "word_process_exited": None,
                    "error": None,
                }
                results.append(planned_result)
                _emit_progress(
                    progress,
                    f"{prefix} planned {source.name} after {time.perf_counter() - started_at:.1f}s",
                )
                continue
            _emit_progress(progress, f"{prefix} processing {source.name}")
            document_result = self._process_one(
                source=source,
                output_root=output_root,
                source_sha256=source_sha256,
                doc_id=doc_id,
                source_format=source_format,
                created_at=created_at,
                governance=config.governance,
                progress=lambda stage, prefix=prefix: _emit_progress(progress, f"{prefix} {stage}"),
            )
            results.append(document_result)
            elapsed = time.perf_counter() - started_at
            status = document_result["status"]
            statistics = document_result.get("statistics") or {}
            summary = ""
            if statistics:
                summary = (
                    f", records={statistics.get('record_count', 0)}, "
                    f"pages={statistics.get('page_count', 0)}, chars={statistics.get('total_text_length', 0)}"
                )
            _emit_progress(progress, f"{prefix} {status} in {elapsed:.1f}s{summary}: {source.name}")
        result = BatchResult(created_at, dry_run, tuple(results))
        if not dry_run:
            write_batch_metadata(output_root, created_at=created_at, documents=results)
        return result

    def _process_one(
        self,
        *,
        source: Path,
        output_root: Path,
        source_sha256: str,
        doc_id: str,
        source_format: str,
        created_at: str,
        governance: dict[str, Any],
        progress: Callable[[str], None] | None = None,
    ) -> dict[str, Any]:
        base: dict[str, Any] = {
            "source_uri": str(source),
            "source_name": source.name,
            "source_format": source_format,
            "source_sha256": source_sha256,
            "source_size": source.stat().st_size,
            "doc_id": doc_id,
            "created_at": created_at,
            "governance": governance,
        }
        word: WordApplication | None = None
        normalized_uri: str | None = None
        try:
            _emit_progress(progress, f"checking identity: {source.name}")
            identity_store = self.identity_store_factory(output_root)
            claim = identity_store.claim(
                doc_id=doc_id,
                source_sha256=source_sha256,
                source_format=source_format,
                source_name=source.name,
            )
            existing = identity_store.inspect_existing_parsed(claim.identity)
            if existing is not None:
                return {
                    **base,
                    "status": "skipped_unchanged",
                    "parsed_uri": existing.parsed_uri,
                    "normalized_uri": (
                        f"normalized/{doc_id}.docx"
                        if source_format == "doc" and (output_root / "normalized" / f"{doc_id}.docx").is_file()
                        else None
                    ),
                    "statistics": existing.statistics,
                    "quality_flags": existing.statistics["quality_flags"],
                    "word_process_id": None,
                    "word_process_exited": None,
                    "error": None,
                }

            _emit_progress(progress, f"starting private Word instance: {source.name}")
            word = self.word_application_factory()
            with word:
                parse_path = source
                if source_format == "doc":
                    _emit_progress(progress, f"converting DOC to DOCX: {source.name}")
                    conversion = self.converter_factory(output_root).convert(
                        source,
                        doc_id=doc_id,
                        source_sha256=source_sha256,
                        word=word,
                    )
                    parse_path = conversion.normalized_path
                    normalized_uri = f"normalized/{parse_path.name}"
                _emit_progress(progress, f"parsing Word content: {source.name}")
                units = self.parser_factory(output_root).parse(
                    parse_path,
                    source_path=source,
                    source_format=source_format,
                    source_sha256=source_sha256,
                    doc_id=doc_id,
                    created_at=created_at,
                    word=word,
                )
            if word.process_exited is False:
                raise ParseError(
                    f"private Microsoft Word process did not exit for {source.name}"
                )
            _emit_progress(progress, f"validating parsed units: {source.name}")
            records = [unit.to_dict() for unit in units]
            for record in records:
                validate_record(record, word_output=True)
            rejection_flags = document_quality_flags(records, source_size=source.stat().st_size)
            if rejection_flags:
                raise QualityRejectedError(rejection_flags)
            parsed_uri = f"parsed/{doc_id}.word.parsed.jsonl"
            parsed_path = output_root / Path(parsed_uri)
            assert_within(output_root, parsed_path)
            if parsed_path.exists():
                raise RuntimeError(f"formal parsed output appeared during processing: {parsed_path}")
            _emit_progress(progress, f"publishing parsed JSONL: {source.name}")
            atomic_write_jsonl(parsed_path, records, root=output_root)
            statistics = document_statistics(records)
            return {
                **base,
                "status": "success",
                "parsed_uri": parsed_uri,
                "normalized_uri": normalized_uri,
                "statistics": statistics,
                "quality_flags": statistics["quality_flags"],
                "word_process_id": word.owned_process_id,
                "word_process_exited": word.process_exited,
                "error": None,
            }
        except Exception as exc:
            flags = _failure_flags(exc)
            return {
                **base,
                "status": "failed",
                "parsed_uri": None,
                "normalized_uri": normalized_uri,
                "statistics": None,
                "quality_flags": flags,
                "word_process_id": word.owned_process_id if word is not None else None,
                "word_process_exited": word.process_exited if word is not None else None,
                "error": {"type": type(exc).__name__, "message": str(exc)},
            }


def _discover_sources(input_root: Path, *, include: Iterable[str] | None) -> list[Path]:
    requested = None
    if include is not None:
        requested = {validate_safe_component(name, label="included source name") for name in include}
    sources: list[Path] = []
    for candidate in input_root.iterdir():
        if not candidate.is_file() or candidate.suffix.lower() not in {".doc", ".docx"}:
            continue
        if requested is not None and candidate.name not in requested:
            continue
        sources.append(assert_within(input_root, candidate, must_exist=True))
    sources.sort(key=lambda path: (path.name.casefold(), path.name))
    if requested is not None:
        found = {path.name for path in sources}
        missing = sorted(requested - found)
        if missing:
            raise FileNotFoundError(f"included Word source files were not found: {missing}")
    return sources


def _failure_flags(exc: Exception) -> list[str]:
    if isinstance(exc, QualityRejectedError):
        return list(dict.fromkeys([*exc.flags, "quality_rejected"]))
    if isinstance(exc, PageNumberUnavailableError):
        return ["page_number_unavailable", "parse_failed", "quality_rejected"]
    if isinstance(exc, ConversionError):
        return ["conversion_failed"]
    if isinstance(exc, (ParseError, DocumentIngestionError)):
        return ["parse_failed"]
    return ["parse_failed"]


def _emit_progress(progress: Callable[[str], None] | None, message: str) -> None:
    if progress is not None:
        progress(message)


def _is_relative_to(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
        return True
    except ValueError:
        return False
