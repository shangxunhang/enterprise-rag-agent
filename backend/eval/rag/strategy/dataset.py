"""Dataset loading and hashing for RAG strategy evaluation."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

from .schemas import RAGEvalSample


def file_sha256(path: str | Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def load_eval_samples(path: str | Path) -> list[RAGEvalSample]:
    dataset_path = Path(path)
    if not dataset_path.is_file():
        raise FileNotFoundError(f"RAG strategy eval dataset not found: {dataset_path}")

    suffix = dataset_path.suffix.lower()
    if suffix == ".jsonl":
        payloads = []
        with dataset_path.open("r", encoding="utf-8-sig") as handle:
            for line_number, line in enumerate(handle, start=1):
                stripped = line.strip()
                if not stripped:
                    continue
                try:
                    payload = json.loads(stripped)
                except json.JSONDecodeError as exc:
                    raise ValueError(
                        f"invalid JSONL at line {line_number}: {dataset_path}"
                    ) from exc
                payloads.append(payload)
    elif suffix == ".json":
        payloads = json.loads(dataset_path.read_text(encoding="utf-8-sig"))
        if not isinstance(payloads, list):
            raise ValueError("JSON eval dataset root must be a list")
    else:
        raise ValueError("eval dataset must use .json or .jsonl")

    samples = [RAGEvalSample.model_validate(item) for item in payloads]
    if not samples:
        raise ValueError("eval dataset cannot be empty")
    ids = [item.sample_id for item in samples]
    if len(ids) != len(set(ids)):
        raise ValueError("eval dataset contains duplicate sample_id values")
    return samples
