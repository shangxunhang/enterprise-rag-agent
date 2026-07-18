# -*- coding: utf-8 -*-
"""
rag_template/data_capture/jsonl_writer.py
========================================

Small JSONL writer used by P4-lite DataCapture.

职责边界：
- 只负责把 dict 追加写入 JSONL。
- 不做业务字段拼装。
"""

from __future__ import annotations

import json
from dataclasses import asdict, is_dataclass
from datetime import date, datetime
from pathlib import Path
from typing import Any, Dict


def to_jsonable(value: Any) -> Any:
    """Convert common Python objects to JSON-serializable values."""
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, Path):
        return str(value)
    if is_dataclass(value):
        return to_jsonable(asdict(value))
    if isinstance(value, dict):
        return {str(k): to_jsonable(v) for k, v in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [to_jsonable(x) for x in value]
    if hasattr(value, "to_dict") and callable(value.to_dict):
        return to_jsonable(value.to_dict())
    return str(value)


class JsonlWriter:
    """Append-only JSONL writer."""

    def __init__(self, path: str | Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def write(self, record: Dict[str, Any]) -> Path:
        jsonable = to_jsonable(record)
        with self.path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(jsonable, ensure_ascii=False) + "\n")
        return self.path
