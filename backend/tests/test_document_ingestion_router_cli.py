from __future__ import annotations

import subprocess
import sys
from pathlib import Path

from document_ingestion.router_cli import build_parser, main


def test_router_cli_help_exposes_word_pdf_options() -> None:
    help_text = build_parser().format_help()
    assert "--input-dir" in help_text
    assert "--output-dir" in help_text
    assert "--pdf-python" in help_text
    assert "--mineru-cmd" in help_text
    assert "--force-paddleocr" in help_text


def test_router_cli_dry_run_does_not_require_pdf_dependencies(tmp_path: Path) -> None:
    input_root = tmp_path / "input"
    input_root.mkdir()
    (input_root / "a.docx").write_bytes(b"word")
    (input_root / "b.pdf").write_bytes(b"pdf")
    output_root = tmp_path / "output"
    exit_code = main(
        [
            "--input-dir",
            str(input_root),
            "--output-dir",
            str(output_root),
            "--dry-run",
        ]
    )
    assert exit_code == 0
    assert not output_root.exists()


def test_router_cli_supports_direct_script_execution() -> None:
    project_root = Path(__file__).resolve().parents[2]
    script = project_root / "document_ingestion" / "router_cli.py"
    completed = subprocess.run(
        [sys.executable, str(script), "--help"],
        cwd=project_root,
        capture_output=True,
        text=True,
        check=False,
    )
    assert completed.returncode == 0
    assert "--pdf-python" in completed.stdout
