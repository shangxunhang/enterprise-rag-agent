from __future__ import annotations

import json
from pathlib import Path

from document_ingestion.cli import build_parser, main


def test_cli_help_defines_required_interface(capsys) -> None:
    parser = build_parser()
    help_text = parser.format_help()
    assert "--input-dir" in help_text
    assert "--output-dir" in help_text
    assert "--document-config" in help_text
    assert "--dry-run" in help_text
    assert "--include" in help_text


def test_cli_dry_run_outputs_plan_without_artifacts(tmp_path: Path, capsys) -> None:
    input_root = tmp_path / "input"
    input_root.mkdir()
    (input_root / "only.docx").write_bytes(b"source")
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
    payload = json.loads(capsys.readouterr().out)
    assert payload["dry_run"] is True
    assert payload["documents"][0]["status"] == "planned"
    assert not output_root.exists()
