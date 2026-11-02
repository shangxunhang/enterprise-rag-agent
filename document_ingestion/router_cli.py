"""Unified CLI routing Word and PDF files to their parser backends."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Sequence

if __package__ in {None, ""}:
    project_root = Path(__file__).resolve().parents[1]
    if str(project_root) not in sys.path:
        sys.path.insert(0, str(project_root))
    from document_ingestion.document_router import DocumentRouter
else:
    from .document_router import DocumentRouter


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m document_ingestion.router_cli",
        description=(
            "Route .doc/.docx to the Word COM parser and .pdf to the "
            "MinerU/PaddleOCR PDF pipeline."
        ),
    )
    parser.add_argument("--input-dir", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--document-config", type=Path)
    parser.add_argument(
        "--include",
        action="append",
        default=None,
        metavar="SOURCE_NAME",
        help="Process only an exact filename; repeat for more files",
    )
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument(
        "--pdf-python",
        type=Path,
        default=None,
        help=(
            "Python executable containing MinerU/PyMuPDF/PaddleOCR. "
            "Defaults to the current interpreter."
        ),
    )
    parser.add_argument("--mineru-cmd", default="mineru")
    parser.add_argument("--force-pdf", action="store_true")
    parser.add_argument("--force-paddleocr", action="store_true")
    parser.add_argument("--skip-mineru", action="store_true")
    parser.add_argument("--paddleocr-dpi", type=int, default=200)
    parser.add_argument("--paddleocr-lang", default="ch")
    parser.add_argument("--paddleocr-use-gpu", action="store_true")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    _configure_utf8_console()
    args = build_parser().parse_args(argv)
    try:
        result = DocumentRouter().run(
            input_dir=args.input_dir,
            output_dir=args.output_dir,
            document_config=args.document_config,
            include=args.include,
            dry_run=args.dry_run,
            pdf_python=args.pdf_python,
            mineru_cmd=args.mineru_cmd,
            force_pdf=args.force_pdf,
            force_paddleocr=args.force_paddleocr,
            skip_mineru=args.skip_mineru,
            paddleocr_dpi=args.paddleocr_dpi,
            paddleocr_lang=args.paddleocr_lang,
            paddleocr_use_gpu=args.paddleocr_use_gpu,
            progress=lambda message: print(message, flush=True),
        )
    except Exception as exc:
        print(
            json.dumps(
                {
                    "status": "error",
                    "type": type(exc).__name__,
                    "message": str(exc),
                },
                ensure_ascii=False,
            ),
            file=sys.stderr,
            flush=True,
        )
        return 2
    print(json.dumps(result.to_dict(), ensure_ascii=False, indent=2), flush=True)
    return result.exit_code


def _configure_utf8_console() -> None:
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if callable(reconfigure):
            try:
                reconfigure(encoding="utf-8", errors="strict")
            except (AttributeError, OSError):
                pass


if __name__ == "__main__":
    raise SystemExit(main())
