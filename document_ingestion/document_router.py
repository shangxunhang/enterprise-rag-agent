"""Unified extension router for Word and PDF parser backends."""

from __future__ import annotations

import json
import subprocess
import sys
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Iterable

from .atomic_writer import atomic_write_json, atomic_write_jsonl
from .config import DocumentConfig, load_document_config
from .document_identity import DocumentIdentityStore
from .hashing import default_doc_id, sha256_file
from .pipeline import IngestionPipeline
from .safe_paths import assert_within, safe_child, validate_safe_component

SUPPORTED_FORMATS = {"doc", "docx", "pdf"}


@dataclass(frozen=True, slots=True)
class RouterBatchResult:
    created_at: str
    dry_run: bool
    documents: tuple[dict[str, Any], ...]

    @property
    def failure_count(self) -> int:
        return sum(document["status"] == "failed" for document in self.documents)

    @property
    def success_count(self) -> int:
        return len(self.documents) - self.failure_count

    @property
    def exit_code(self) -> int:
        return 1 if self.failure_count else 0

    def to_dict(self) -> dict[str, Any]:
        backend_counts: dict[str, int] = {}
        status_counts: dict[str, int] = {}
        for document in self.documents:
            backend = str(document.get("backend") or "unknown")
            status = str(document.get("status") or "unknown")
            backend_counts[backend] = backend_counts.get(backend, 0) + 1
            status_counts[status] = status_counts.get(status, 0) + 1
        return {
            "schema_version": "document_router_report_v1",
            "created_at": self.created_at,
            "dry_run": self.dry_run,
            "document_count": len(self.documents),
            "success_count": self.success_count,
            "failure_count": self.failure_count,
            "backend_counts": backend_counts,
            "status_counts": status_counts,
            "documents": list(self.documents),
        }


class DocumentRouter:
    def __init__(
        self,
        *,
        word_pipeline_factory: Callable[[], IngestionPipeline] = IngestionPipeline,
        subprocess_runner: Callable[..., subprocess.CompletedProcess[str]] = subprocess.run,
        identity_store_factory: Callable[[Path], DocumentIdentityStore] = DocumentIdentityStore,
    ) -> None:
        self.word_pipeline_factory = word_pipeline_factory
        self.subprocess_runner = subprocess_runner
        self.identity_store_factory = identity_store_factory

    def run(
        self,
        *,
        input_dir: Path,
        output_dir: Path,
        document_config: Path | None = None,
        include: Iterable[str] | None = None,
        dry_run: bool = False,
        pdf_python: Path | None = None,
        mineru_cmd: str = "mineru",
        force_pdf: bool = False,
        force_paddleocr: bool = False,
        skip_mineru: bool = False,
        paddleocr_dpi: int = 200,
        paddleocr_lang: str = "ch",
        paddleocr_use_gpu: bool = False,
        progress: Callable[[str], None] | None = None,
    ) -> RouterBatchResult:
        input_root = input_dir.resolve(strict=True)
        if not input_root.is_dir():
            raise ValueError(f"input directory is not a directory: {input_root}")
        output_root = output_dir.resolve()
        if output_root == input_root or _is_relative_to(output_root, input_root):
            raise ValueError("output directory must not be inside the input directory")

        sources = _discover_sources(input_root, include=include)
        configs = load_document_config(document_config)
        created_at = datetime.now(timezone.utc).isoformat()
        _emit(progress, f"Discovered {len(sources)} supported document(s).")

        planned = [
            _source_plan(source, configs.get(source.name, DocumentConfig(source.name)), created_at)
            for source in sources
        ]
        if dry_run:
            documents = tuple({**item, "status": "planned", "error": None} for item in planned)
            return RouterBatchResult(created_at, True, documents)

        for directory in ("identities", "reports", "word", "pdf"):
            safe_child(output_root, directory).mkdir(parents=True, exist_ok=True)

        identity_store = self.identity_store_factory(output_root)
        accepted: list[dict[str, Any]] = []
        results: list[dict[str, Any]] = []
        for index, item in enumerate(planned, 1):
            prefix = f"[{index}/{len(planned)}]"
            _emit(progress, f"{prefix} route={item['backend']} source={item['source_name']}")
            try:
                identity_store.claim(
                    doc_id=item["doc_id"],
                    source_sha256=item["source_sha256"],
                    source_format=item["source_format"],
                    source_name=item["source_name"],
                    backend=item["backend"],
                )
                accepted.append(item)
            except Exception as exc:
                results.append(
                    {
                        **item,
                        "status": "failed",
                        "parsed_uri": None,
                        "profile_uri": None,
                        "eval_uri": None,
                        "quality_level": None,
                        "can_enter_spark_cleaning": False,
                        "statistics": None,
                        "error": {"type": type(exc).__name__, "message": str(exc)},
                    }
                )
                _emit(progress, f"{prefix} identity claim failed: {exc}")

        word_items = [item for item in accepted if item["backend"] == "word"]
        pdf_items = [item for item in accepted if item["backend"] == "pdf"]

        if word_items:
            _emit(progress, f"Dispatching {len(word_items)} Word document(s).")
            word_result = self.word_pipeline_factory().run(
                input_dir=input_root,
                output_dir=output_root / "word",
                document_config=document_config,
                include=[item["source_name"] for item in word_items],
                progress=lambda message: _emit(progress, f"[word] {message}"),
            )
            item_by_name = {item["source_name"]: item for item in word_items}
            for document in word_result.documents:
                item = item_by_name[document["source_name"]]
                parsed_uri = document.get("parsed_uri")
                normalized_uri = document.get("normalized_uri")
                results.append(
                    {
                        **item,
                        "status": document["status"],
                        "parsed_uri": f"word/{parsed_uri}" if parsed_uri else None,
                        "normalized_uri": f"word/{normalized_uri}" if normalized_uri else None,
                        "profile_uri": None,
                        "eval_uri": None,
                        "quality_level": None,
                        "can_enter_spark_cleaning": document["status"] != "failed",
                        "statistics": document.get("statistics"),
                        "quality_flags": document.get("quality_flags") or [],
                        "word_process_id": document.get("word_process_id"),
                        "word_process_exited": document.get("word_process_exited"),
                        "error": document.get("error"),
                    }
                )

        if pdf_items:
            pdf_runtime = (pdf_python or Path(sys.executable)).resolve()
            for item in pdf_items:
                results.append(
                    self._run_pdf(
                        item=item,
                        output_root=output_root,
                        pdf_python=pdf_runtime,
                        mineru_cmd=mineru_cmd,
                        force_pdf=force_pdf,
                        force_paddleocr=force_paddleocr,
                        skip_mineru=skip_mineru,
                        paddleocr_dpi=paddleocr_dpi,
                        paddleocr_lang=paddleocr_lang,
                        paddleocr_use_gpu=paddleocr_use_gpu,
                        progress=progress,
                    )
                )

        order = {source.name: index for index, source in enumerate(sources)}
        results.sort(key=lambda document: order[document["source_name"]])
        batch = RouterBatchResult(created_at, False, tuple(results))
        report_root = output_root / "reports"
        atomic_write_json(
            report_root / "document_ingestion_report.json",
            batch.to_dict(),
            root=output_root,
        )
        atomic_write_jsonl(
            output_root / "document_manifest.jsonl",
            [_manifest_record(document) for document in results],
            root=output_root,
        )
        return batch

    def _run_pdf(
        self,
        *,
        item: dict[str, Any],
        output_root: Path,
        pdf_python: Path,
        mineru_cmd: str,
        force_pdf: bool,
        force_paddleocr: bool,
        skip_mineru: bool,
        paddleocr_dpi: int,
        paddleocr_lang: str,
        paddleocr_use_gpu: bool,
        progress: Callable[[str], None] | None,
    ) -> dict[str, Any]:
        started = time.perf_counter()
        pdf_root = output_root / "pdf"
        parsed_path = pdf_root / "parsed_jsonl" / f"{item['doc_id']}.parsed.jsonl"
        profile_path = pdf_root / "profile" / f"{item['doc_id']}.profile.json"
        eval_path = pdf_root / "eval_report" / f"{item['doc_id']}.parse_eval.json"
        existed_before = parsed_path.is_file() and profile_path.is_file() and eval_path.is_file()

        if not pdf_python.is_file():
            return _pdf_failure(item, f"PDF Python executable not found: {pdf_python}", started)

        command = [
            str(pdf_python),
            "-m",
            "document_ingestion.pdf_backend.pdf_parse_pipeline",
            "--input",
            item["source_uri"],
            "--base-dir",
            str(pdf_root),
            "--doc-id",
            item["doc_id"],
            "--mineru-cmd",
            mineru_cmd,
            "--paddleocr-dpi",
            str(paddleocr_dpi),
            "--paddleocr-lang",
            paddleocr_lang,
        ]
        if force_pdf:
            command.append("--force")
        if force_paddleocr:
            command.append("--force-paddleocr")
        if skip_mineru:
            command.append("--skip-mineru")
        if paddleocr_use_gpu:
            command.append("--paddleocr-use-gpu")

        _emit(progress, f"[pdf] starting {item['source_name']}")
        try:
            completed = self.subprocess_runner(
                command,
                cwd=str(Path(__file__).resolve().parents[1]),
                text=True,
                check=False,
            )
        except Exception as exc:
            return _pdf_failure(item, f"cannot start PDF backend: {exc}", started)

        if completed.returncode != 0:
            return _pdf_failure(
                item,
                f"PDF backend exited with code {completed.returncode}",
                started,
            )
        if not (parsed_path.is_file() and profile_path.is_file() and eval_path.is_file()):
            return _pdf_failure(item, "PDF backend did not publish complete outputs", started)

        try:
            profile = _load_json(profile_path)
            eval_report = _load_json(eval_path)
        except Exception as exc:
            return _pdf_failure(item, f"cannot read PDF outputs: {exc}", started)
        decision = eval_report.get("quality_decision") or {}
        coverage = eval_report.get("coverage") or {}
        validity = eval_report.get("basic_validity") or {}
        status = "skipped_unchanged" if existed_before and not force_pdf else "success"
        result = {
            **item,
            "status": status,
            "parsed_uri": f"pdf/parsed_jsonl/{parsed_path.name}",
            "normalized_uri": None,
            "profile_uri": f"pdf/profile/{profile_path.name}",
            "eval_uri": f"pdf/eval_report/{eval_path.name}",
            "quality_level": decision.get("quality_level"),
            "can_enter_spark_cleaning": bool(decision.get("can_enter_spark_cleaning")),
            "final_parser": profile.get("parser"),
            "statistics": {
                "record_count": validity.get("total_records"),
                "page_count": coverage.get("page_count"),
                "text_coverage_ratio": coverage.get("text_coverage_ratio"),
                "elapsed_seconds": round(time.perf_counter() - started, 2),
            },
            "quality_flags": list(
                dict.fromkeys(
                    [
                        *decision.get("fail_reasons", []),
                        *decision.get("warning_reasons", []),
                        *decision.get("need_ocr_reasons", []),
                        *decision.get("need_review_reasons", []),
                    ]
                )
            ),
            "error": None,
        }
        _emit(
            progress,
            f"[pdf] {status} parser={result['final_parser']} "
            f"quality={result['quality_level']} source={item['source_name']}",
        )
        return result


def _source_plan(
    source: Path,
    config: DocumentConfig,
    created_at: str,
) -> dict[str, Any]:
    source_format = source.suffix.lower().removeprefix(".")
    source_sha256 = sha256_file(source)
    doc_id = config.doc_id or default_doc_id(source_sha256)
    validate_safe_component(doc_id, label="doc_id")
    return {
        "source_uri": str(source),
        "source_name": source.name,
        "source_format": source_format,
        "source_sha256": source_sha256,
        "source_size": source.stat().st_size,
        "doc_id": doc_id,
        "backend": "pdf" if source_format == "pdf" else "word",
        "created_at": created_at,
        "governance": config.governance,
    }


def _discover_sources(
    input_root: Path,
    *,
    include: Iterable[str] | None,
) -> list[Path]:
    requested = None
    if include is not None:
        requested = {
            validate_safe_component(name, label="included source name") for name in include
        }
    sources = [
        assert_within(input_root, candidate, must_exist=True)
        for candidate in input_root.iterdir()
        if candidate.is_file()
        and candidate.suffix.lower().removeprefix(".") in SUPPORTED_FORMATS
        and (requested is None or candidate.name in requested)
    ]
    sources.sort(key=lambda path: (path.name.casefold(), path.name))
    if requested is not None:
        found = {source.name for source in sources}
        missing = sorted(requested - found)
        if missing:
            raise FileNotFoundError(f"included documents were not found: {missing}")
    return sources


def _load_json(path: Path) -> dict[str, Any]:
    with path.open("r", encoding="utf-8-sig") as handle:
        value = json.load(handle)
    if not isinstance(value, dict):
        raise TypeError(f"JSON object expected: {path}")
    return value


def _pdf_failure(
    item: dict[str, Any],
    message: str,
    started: float,
) -> dict[str, Any]:
    return {
        **item,
        "status": "failed",
        "parsed_uri": None,
        "normalized_uri": None,
        "profile_uri": None,
        "eval_uri": None,
        "quality_level": "fail",
        "can_enter_spark_cleaning": False,
        "final_parser": None,
        "statistics": {"elapsed_seconds": round(time.perf_counter() - started, 2)},
        "quality_flags": ["pdf_parse_failed"],
        "error": {"type": "PdfBackendError", "message": message},
    }


def _manifest_record(document: dict[str, Any]) -> dict[str, Any]:
    return {
        "schema_version": "document_router_manifest_v1",
        "doc_id": document["doc_id"],
        "source_uri": document["source_uri"],
        "source_name": document["source_name"],
        "source_format": document["source_format"],
        "source_sha256": document["source_sha256"],
        "backend": document["backend"],
        "status": document["status"],
        "parsed_uri": document.get("parsed_uri"),
        "normalized_uri": document.get("normalized_uri"),
        "profile_uri": document.get("profile_uri"),
        "eval_uri": document.get("eval_uri"),
        "quality_level": document.get("quality_level"),
        "can_enter_spark_cleaning": document.get("can_enter_spark_cleaning"),
        "governance": document.get("governance") or {},
        "created_at": document["created_at"],
        "error": document.get("error"),
    }


def _emit(progress: Callable[[str], None] | None, message: str) -> None:
    if progress is not None:
        progress(message)


def _is_relative_to(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
        return True
    except ValueError:
        return False
