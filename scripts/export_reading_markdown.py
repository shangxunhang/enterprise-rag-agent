"""Local administrator CLI for SRCREV-03's published Markdown reading copy.

The caller must supply an exact TerminalOutcome receipt. This CLI does not
authenticate its checkpoint provenance or a tenant and never opens business
SQLite databases, runs a workflow, or recovers a publication.
"""

from __future__ import annotations

import argparse
from hashlib import sha256
import json
from pathlib import Path
import sys


BACKEND_ROOT = Path(__file__).resolve().parent.parent / "backend"
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from application.reading_export.delivery import MANIFEST_NAME  # noqa: E402
from application.reading_export.exporter import export_reading_markdown  # noqa: E402
from application.terminal_outcome_schema import TerminalOutcomeResultV1  # noqa: E402


def _unique_pairs(pairs: list[tuple[str, object]]) -> dict[str, object]:
    value: dict[str, object] = {}
    for key, item in pairs:
        if key in value:
            raise ValueError(f"duplicate TerminalOutcome JSON key: {key}")
        value[key] = item
    return value


def _reject_constant(value: str) -> None:
    raise ValueError(f"invalid TerminalOutcome JSON constant: {value}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--target-release-root", type=Path, required=True)
    parser.add_argument("--release-id", required=True)
    parser.add_argument("--terminal-outcome-json", type=Path)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args(argv)
    if args.terminal_outcome_json is None:
        parser.error("TERMINAL_OUTCOME_RECEIPT_REQUIRED: --terminal-outcome-json is required")
    try:
        raw = args.terminal_outcome_json.read_bytes()
        terminal_dict = json.loads(
            raw.decode("utf-8"), object_pairs_hook=_unique_pairs,
            parse_constant=_reject_constant,
        )
        terminal = TerminalOutcomeResultV1.model_validate(terminal_dict)
        receipt = export_reading_markdown(
            target_release_root=args.target_release_root,
            release_id=args.release_id,
            terminal_outcome=terminal,
            output_dir=args.output_dir,
            terminal_receipt_path=args.terminal_outcome_json,
        )
        manifest_bytes = (receipt.final_dir / MANIFEST_NAME).read_bytes()
        operation = {
            "status": receipt.status,
            "final_dir": str(receipt.final_dir),
            "export_identity": receipt.export_identity,
            "source_release_id": receipt.source_release_id,
            "terminal_outcome_result_id": receipt.terminal_outcome_result_id,
            "terminal_outcome_source": receipt.terminal_outcome_source,
            "authorization_boundary": "local_admin",
            "checkpoint_provenance": receipt.checkpoint_provenance,
            "files_sha256": dict(receipt.file_sha256s),
            "manifest_sha256_external": sha256(manifest_bytes).hexdigest(),
        }
        print(json.dumps(operation, ensure_ascii=False, sort_keys=True))
        return 0
    except (OSError, ValueError, TypeError, json.JSONDecodeError) as exc:
        print(json.dumps({"status": "ERROR", "error": str(exc)}, ensure_ascii=False),
              file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
