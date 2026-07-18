"""Inspect the latest Agent-RAG capture files."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any


PROJECT_ROOT = Path(__file__).resolve().parents[1]
BACKEND_ROOT = PROJECT_ROOT / "backend"

if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from core.config import get_settings


def _latest_raw_file(captures_dir: Path) -> Path:
    raw_dir = captures_dir / "raw_interactions"
    files = list(raw_dir.glob("*_raw_interactions.jsonl"))

    if not files:
        raise FileNotFoundError(
            f"No *_raw_interactions.jsonl files found in {raw_dir}"
        )

    return max(files, key=lambda path: path.stat().st_mtime)


def _read_last_record(path: Path) -> dict[str, Any]:
    if not path.is_file():
        raise FileNotFoundError(path)

    lines = [
        line
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]

    if not lines:
        raise ValueError(f"Capture file is empty: {path}")

    return json.loads(lines[-1])


def _print_record(name: str, path: Path) -> None:
    print("=" * 100)
    print(name, path)

    record = _read_last_record(path)

    print("schema_version:", record.get("schema_version"))
    print("capture_type:", record.get("capture_type"))
    print("user_input:", record.get("user_input"))
    print("top-level keys:", list(record.keys()))

    rag_context = record.get("rag_context") or {}
    print("rag_context chars:", len(rag_context.get("context_text", "")))
    print("retrieved_chunks:", len(record.get("retrieved_chunks") or []))
    print("citations:", len(record.get("citations") or []))
    print("rag_trace keys:", list((record.get("rag_trace") or {}).keys()))

    print("prompt chars:", len(record.get("prompt") or ""))
    print("model_output chars:", len(record.get("model_output") or ""))
    print("final_output chars:", len(record.get("final_output") or ""))

    print("prompt_info:", (record.get("prompt_info") or {}).get("prompt_id"))
    print("model_info:", (record.get("model_info") or {}).get("model_name"))
    print("eval keys:", list((record.get("eval_sample") or {}).keys()))


def main() -> None:
    settings = get_settings()

    parser = argparse.ArgumentParser(
        description="Inspect the latest Agent-RAG capture records.",
    )
    parser.add_argument(
        "--captures-dir",
        type=str,
        default=str(settings.data_capture_dir),
        help="Capture root. Default: settings.data_capture_dir.",
    )
    args = parser.parse_args()

    captures_dir = Path(args.captures_dir).expanduser().resolve()
    latest_raw = _latest_raw_file(captures_dir)
    run_id = latest_raw.name.removesuffix("_raw_interactions.jsonl")

    paths = {
        "raw": captures_dir / "raw_interactions" / f"{run_id}_raw_interactions.jsonl",
        "sft": captures_dir / "sft_candidates" / f"{run_id}_sft_candidates.jsonl",
        "eval": captures_dir / "eval_samples" / f"{run_id}_eval_samples.jsonl",
    }

    print("latest run_id:", run_id)

    for name, path in paths.items():
        _print_record(name, path)


if __name__ == "__main__":
    main()
