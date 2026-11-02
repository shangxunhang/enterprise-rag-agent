"""Command-line entry point for the standalone Word parser backend."""

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
    from document_ingestion.pipeline import IngestionPipeline
else:
    from .pipeline import IngestionPipeline


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m document_ingestion.cli",
        description="Parse .doc/.docx with Microsoft Word COM into MinerU-compatible raw JSONL units.",
    )
    parser.add_argument("--input-dir", type=Path, required=True, help="Directory containing source .doc/.docx files")
    parser.add_argument("--output-dir", type=Path, required=True, help="Controlled output root")
    parser.add_argument("--document-config", type=Path, help="Optional JSON governance/doc_id configuration")
    parser.add_argument(
        "--include",
        action="append",
        default=None,
        metavar="SOURCE_NAME",
        help="Process only this exact source filename; repeat for multiple smoke files",
    )
    parser.add_argument("--dry-run", action="store_true", help="Hash and plan inputs without starting Word or writing outputs")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    _configure_utf8_console()
    args = build_parser().parse_args(argv)
    try:
        result = IngestionPipeline().run(
            input_dir=args.input_dir,
            output_dir=args.output_dir,
            document_config=args.document_config,
            dry_run=args.dry_run,
            include=args.include,
            progress=lambda message: print(message, file=sys.stderr, flush=True),
        )
    except Exception as exc:
        print(json.dumps({"status": "error", "type": type(exc).__name__, "message": str(exc)}, ensure_ascii=False), file=sys.stderr)
        return 2
    print(json.dumps(result.to_dict(), ensure_ascii=False, indent=2))
    return result.exit_code


def _configure_utf8_console() -> None:
    """Avoid Windows legacy-codepage failures for valid Unicode filenames."""

    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if callable(reconfigure):
            try:
                reconfigure(encoding="utf-8", errors="strict")
            except (AttributeError, OSError):
                pass


if __name__ == "__main__":
    raise SystemExit(main())
