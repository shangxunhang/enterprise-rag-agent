"""Single-PDF pipeline: MinerU primary, PaddleOCR fallback.

This preserves the user's original PDF/OCR control flow while making it safe
inside the rag-agent repository.  Source PDFs are never renamed or modified.
"""

from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ACCEPT_LEVELS = {"pass", "pass_with_warnings", "need_review"}
FALLBACK_LEVELS = {"need_ocr", "fail"}


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def normalize_doc_id(value: str) -> str:
    result = str(value).strip()
    while result.lower().endswith(".pdf"):
        result = result[:-4]
    result = result.rstrip(". ")
    if not result:
        raise ValueError("doc_id cannot be empty")
    return result


def validate_pdf(path: Path) -> Path:
    resolved = path.resolve(strict=True)
    if not resolved.is_file() or resolved.suffix.lower() != ".pdf":
        raise ValueError(f"Input is not a PDF file: {resolved}")
    return resolved


def _run(command: list[str], *, cwd: Path) -> subprocess.CompletedProcess[str]:
    print("[CMD]", " ".join(command), flush=True)
    return subprocess.run(
        command,
        cwd=str(cwd),
        text=True,
        encoding="utf-8",
        errors="replace",
        check=True,
    )


def _load_json(path: Path) -> dict[str, Any]:
    with path.open("r", encoding="utf-8-sig") as handle:
        value = json.load(handle)
    return value if isinstance(value, dict) else {}


def _append_jsonl(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(value, ensure_ascii=False) + "\n")


def _ensure_dirs(base_dir: Path) -> None:
    for name in (
        "mineru_output",
        "paddleocr_output",
        "parsed_jsonl",
        "profile",
        "eval_report",
        "logs",
    ):
        (base_dir / name).mkdir(parents=True, exist_ok=True)


def _find_mineru_auto_dir(root: Path, source_stem: str) -> Path | None:
    if not root.exists():
        return None
    candidates = [
        path.parent for path in root.rglob(f"{source_stem}_content_list_v2.json")
    ]
    if not candidates:
        candidates = [path.parent for path in root.rglob("*_content_list_v2.json")]
    if not candidates:
        candidates = [path.parent for path in root.rglob("*_content_list.json")]
    return max(candidates, key=lambda path: path.stat().st_mtime) if candidates else None


def _run_module(
    python_executable: Path,
    module: str,
    arguments: list[str],
    *,
    project_root: Path,
) -> None:
    _run(
        [str(python_executable), "-m", module, *arguments],
        cwd=project_root,
    )


def _run_mineru(input_pdf: Path, output_root: Path, mineru_cmd: str) -> bool:
    try:
        _run(
            [mineru_cmd, "-p", str(input_pdf), "-o", str(output_root), "-b", "pipeline"],
            cwd=output_root.parent,
        )
        return True
    except Exception as exc:
        print(f"[MinerU] failed: {exc}", flush=True)
        return False


def _run_eval(
    *,
    python_executable: Path,
    project_root: Path,
    base_dir: Path,
    doc_id: str,
    output_name: str,
) -> dict[str, Any] | None:
    parsed = base_dir / "parsed_jsonl" / f"{doc_id}.parsed.jsonl"
    profile = base_dir / "profile" / f"{doc_id}.profile.json"
    output = base_dir / "eval_report" / output_name
    if not parsed.is_file():
        return None
    try:
        args = [
            "--parsed-jsonl",
            str(parsed),
            "--output",
            str(output),
            "--base-dir",
            str(base_dir),
        ]
        if profile.is_file():
            args.extend(["--profile", str(profile)])
        _run_module(
            python_executable,
            "document_ingestion.pdf_backend.parse_eval",
            args,
            project_root=project_root,
        )
        return _load_json(output)
    except Exception as exc:
        print(f"[parse_eval] failed: {exc}", flush=True)
        return None


def should_fallback_to_paddleocr(
    eval_report: dict[str, Any] | None,
    *,
    force_paddleocr: bool = False,
) -> tuple[bool, str]:
    if force_paddleocr:
        return True, "force_paddleocr"
    if eval_report is None:
        return True, "mineru_eval_missing"
    decision = eval_report.get("quality_decision") or {}
    quality_level = decision.get("quality_level")
    coverage = eval_report.get("coverage") or {}
    text_coverage = coverage.get("text_coverage_ratio", 0)
    page_count = coverage.get("page_count") or 0
    empty_pages = coverage.get("empty_text_pages") or []
    if quality_level in FALLBACK_LEVELS:
        return True, f"quality_level_{quality_level}"
    try:
        if float(text_coverage) < 0.5:
            return True, "low_text_coverage"
    except Exception:
        return True, "invalid_text_coverage"
    if page_count and len(empty_pages) / max(int(page_count), 1) > 0.5:
        return True, "too_many_empty_text_pages"
    return False, "mineru_accepted"


def _snapshot(base_dir: Path, doc_id: str, parser_name: str) -> None:
    mapping = (
        (
            base_dir / "parsed_jsonl" / f"{doc_id}.parsed.jsonl",
            base_dir / "parsed_jsonl" / f"{doc_id}.{parser_name}.parsed.jsonl",
        ),
        (
            base_dir / "profile" / f"{doc_id}.profile.json",
            base_dir / "profile" / f"{doc_id}.{parser_name}.profile.json",
        ),
    )
    for source, target in mapping:
        if source.is_file():
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, target)


def parse_one_pdf(
    *,
    input_pdf: Path,
    base_dir: Path,
    doc_id: str | None = None,
    mineru_cmd: str = "mineru",
    force: bool = False,
    force_paddleocr: bool = False,
    skip_mineru: bool = False,
    paddleocr_dpi: int = 200,
    paddleocr_lang: str = "ch",
    paddleocr_use_gpu: bool = False,
) -> dict[str, Any]:
    started = time.perf_counter()
    input_pdf = validate_pdf(input_pdf)
    base_dir = base_dir.resolve()
    project_root = Path(__file__).resolve().parents[2]
    python_executable = Path(sys.executable).resolve()
    doc_id = normalize_doc_id(doc_id or input_pdf.stem)
    _ensure_dirs(base_dir)

    final_parsed = base_dir / "parsed_jsonl" / f"{doc_id}.parsed.jsonl"
    final_profile = base_dir / "profile" / f"{doc_id}.profile.json"
    final_eval = base_dir / "eval_report" / f"{doc_id}.parse_eval.json"
    if final_parsed.is_file() and final_profile.is_file() and final_eval.is_file() and not force:
        report = _load_json(final_eval)
        decision = report.get("quality_decision") or {}
        return {
            "doc_id": doc_id,
            "source_pdf": str(input_pdf),
            "status": "skipped_existing",
            "final_parser": report.get("parser"),
            "quality_level": decision.get("quality_level"),
            "can_enter_spark_cleaning": decision.get("can_enter_spark_cleaning"),
            "fallback_reason": None,
            "elapsed_seconds": round(time.perf_counter() - started, 2),
            "created_at": _now_iso(),
        }

    result: dict[str, Any] = {
        "doc_id": doc_id,
        "source_pdf": str(input_pdf),
        "status": "failed",
        "final_parser": None,
        "quality_level": None,
        "can_enter_spark_cleaning": False,
        "fallback_reason": None,
        "error": None,
        "created_at": _now_iso(),
    }

    mineru_eval: dict[str, Any] | None = None
    if not skip_mineru and not force_paddleocr:
        print(f"========== MinerU parse: {doc_id} ==========", flush=True)
        mineru_ok = _run_mineru(input_pdf, base_dir / "mineru_output", mineru_cmd)
        auto_dir = _find_mineru_auto_dir(base_dir / "mineru_output", input_pdf.stem)
        if mineru_ok and auto_dir is not None:
            try:
                _run_module(
                    python_executable,
                    "document_ingestion.pdf_backend.mineru_adapter",
                    [
                        "--mineru-dir",
                        str(auto_dir),
                        "--base-dir",
                        str(base_dir),
                        "--doc-id",
                        doc_id,
                        "--source-pdf",
                        str(input_pdf),
                        "--overwrite",
                    ],
                    project_root=project_root,
                )
                mineru_eval = _run_eval(
                    python_executable=python_executable,
                    project_root=project_root,
                    base_dir=base_dir,
                    doc_id=doc_id,
                    output_name=f"{doc_id}.mineru_eval.json",
                )
                if mineru_eval:
                    shutil.copy2(
                        base_dir / "eval_report" / f"{doc_id}.mineru_eval.json",
                        base_dir / "eval_report" / f"{doc_id}.mineru.parse_eval.json",
                    )
                    _snapshot(base_dir, doc_id, "mineru")
            except Exception as exc:
                print(f"[MinerU adapter] failed: {exc}", flush=True)
        else:
            print("[MinerU] no usable auto directory; fallback will be evaluated", flush=True)

    fallback, reason = should_fallback_to_paddleocr(
        mineru_eval,
        force_paddleocr=force_paddleocr or skip_mineru,
    )
    result["fallback_reason"] = reason

    if not fallback and mineru_eval is not None:
        source_eval = base_dir / "eval_report" / f"{doc_id}.mineru_eval.json"
        if source_eval.is_file():
            shutil.copy2(source_eval, final_eval)
        decision = mineru_eval.get("quality_decision") or {}
        result.update(
            {
                "status": "success",
                "final_parser": "mineru",
                "quality_level": decision.get("quality_level"),
                "can_enter_spark_cleaning": decision.get("can_enter_spark_cleaning"),
            }
        )
    else:
        print(f"========== PaddleOCR fallback: {doc_id}, reason={reason} ==========", flush=True)
        try:
            arguments = [
                "--input-pdf",
                str(input_pdf),
                "--base-dir",
                str(base_dir),
                "--doc-id",
                doc_id,
                "--dpi",
                str(paddleocr_dpi),
                "--lang",
                paddleocr_lang,
                "--overwrite",
            ]
            if paddleocr_use_gpu:
                arguments.append("--use-gpu")
            _run_module(
                python_executable,
                "document_ingestion.pdf_backend.paddleocr_adapter",
                arguments,
                project_root=project_root,
            )
            paddle_eval = _run_eval(
                python_executable=python_executable,
                project_root=project_root,
                base_dir=base_dir,
                doc_id=doc_id,
                output_name=f"{doc_id}.parse_eval.json",
            )
            _snapshot(base_dir, doc_id, "paddleocr")
            if paddle_eval is None:
                result["error"] = "paddleocr_eval_failed"
            else:
                decision = paddle_eval.get("quality_decision") or {}
                result.update(
                    {
                        "status": "success",
                        "final_parser": "paddleocr",
                        "quality_level": decision.get("quality_level"),
                        "can_enter_spark_cleaning": decision.get("can_enter_spark_cleaning"),
                    }
                )
        except Exception as exc:
            result["error"] = f"paddleocr_failed: {exc}"

    result["elapsed_seconds"] = round(time.perf_counter() - started, 2)
    _append_jsonl(base_dir / "logs" / "parse_jobs.jsonl", result)
    print("========== PDF Parse Result ==========", flush=True)
    print(json.dumps(result, ensure_ascii=False, indent=2), flush=True)
    return result


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="PDF parse pipeline: MinerU primary + PaddleOCR fallback")
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--base-dir", type=Path, required=True)
    parser.add_argument("--doc-id")
    parser.add_argument("--mineru-cmd", default="mineru")
    parser.add_argument("--force", action="store_true")
    parser.add_argument("--force-paddleocr", action="store_true")
    parser.add_argument("--skip-mineru", action="store_true")
    parser.add_argument("--paddleocr-dpi", type=int, default=200)
    parser.add_argument("--paddleocr-lang", default="ch")
    parser.add_argument("--paddleocr-use-gpu", action="store_true")
    return parser


def main() -> int:
    args = build_parser().parse_args()
    result = parse_one_pdf(
        input_pdf=args.input,
        base_dir=args.base_dir,
        doc_id=args.doc_id,
        mineru_cmd=args.mineru_cmd,
        force=args.force,
        force_paddleocr=args.force_paddleocr,
        skip_mineru=args.skip_mineru,
        paddleocr_dpi=args.paddleocr_dpi,
        paddleocr_lang=args.paddleocr_lang,
        paddleocr_use_gpu=args.paddleocr_use_gpu,
    )
    return 0 if result["status"] in {"success", "skipped_existing"} else 1


if __name__ == "__main__":
    raise SystemExit(main())
