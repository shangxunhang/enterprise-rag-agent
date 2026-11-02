"""Optional per-source governance configuration."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .safe_paths import validate_safe_component


@dataclass(frozen=True, slots=True)
class DocumentConfig:
    source_name: str
    doc_id: str | None = None
    governance: dict[str, Any] = field(default_factory=dict)


def load_document_config(path: Path | None) -> dict[str, DocumentConfig]:
    if path is None:
        return {}
    with path.resolve(strict=True).open("r", encoding="utf-8") as handle:
        raw = json.load(handle)
    if isinstance(raw, dict) and "source_name" in raw:
        entries = [raw]
    elif isinstance(raw, dict) and isinstance(raw.get("documents"), list):
        entries = raw["documents"]
    elif isinstance(raw, list):
        entries = raw
    else:
        raise ValueError("document config must be an object, array, or {'documents': [...]} object")
    result: dict[str, DocumentConfig] = {}
    for index, entry in enumerate(entries):
        if not isinstance(entry, dict):
            raise ValueError(f"document config entry {index} must be an object")
        source_name = validate_safe_component(entry.get("source_name"), label="source_name")
        doc_id = entry.get("doc_id")
        if doc_id is not None:
            validate_safe_component(doc_id, label="doc_id")
        if source_name in result:
            raise ValueError(f"duplicate document config for {source_name}")
        governance = {key: value for key, value in entry.items() if key not in {"source_name", "doc_id"}}
        result[source_name] = DocumentConfig(source_name, doc_id, governance)
    return result
