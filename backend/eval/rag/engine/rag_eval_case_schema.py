# -*- coding: utf-8 -*-
"""
rag_template/eval/rag_eval_case_schema.py
=========================================

RAG evaluation case schema for parent-child RAG QA.

This module is intentionally lightweight and dependency-free. It supports JSONL
and JSON-list inputs, so the same eval cases can later be reused by a real RAGAS
runner, DeepSeek-as-judge, or an internal evaluator.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional


def _safe_list(value: Any) -> List[str]:
    if value is None:
        return []
    if isinstance(value, list):
        return [str(x).strip() for x in value if str(x).strip()]
    if isinstance(value, tuple):
        return [str(x).strip() for x in value if str(x).strip()]
    if isinstance(value, str):
        if not value.strip():
            return []
        # Accept both comma-separated strings and a single keyword/id.
        if "," in value:
            return [x.strip() for x in value.split(",") if x.strip()]
        return [value.strip()]
    return [str(value).strip()]


@dataclass
class RagEvalCase:
    """One evaluation case for a RAG QA pipeline."""

    case_id: str
    query: str
    reference_answer: str = ""
    expected_doc_ids: List[str] = field(default_factory=list)
    expected_parent_chunk_ids: List[str] = field(default_factory=list)
    expected_child_chunk_ids: List[str] = field(default_factory=list)
    expected_keywords: List[str] = field(default_factory=list)
    answer_keywords: List[str] = field(default_factory=list)
    metadata: Dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_dict(cls, item: Dict[str, Any], index: int = 0) -> "RagEvalCase":
        query = str(item.get("query") or "").strip()
        if not query:
            raise ValueError(f"RagEvalCase[{index}] query cannot be empty")
        case_id = str(item.get("case_id") or f"rag_case_{index + 1:04d}").strip()
        return cls(
            case_id=case_id,
            query=query,
            reference_answer=str(item.get("reference_answer") or ""),
            expected_doc_ids=_safe_list(item.get("expected_doc_ids")),
            expected_parent_chunk_ids=_safe_list(item.get("expected_parent_chunk_ids")),
            expected_child_chunk_ids=_safe_list(item.get("expected_child_chunk_ids")),
            expected_keywords=_safe_list(item.get("expected_keywords")),
            answer_keywords=_safe_list(item.get("answer_keywords")),
            metadata=item.get("metadata") if isinstance(item.get("metadata"), dict) else {},
        )

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


def load_rag_eval_cases(path: str | Path) -> List[RagEvalCase]:
    """Load eval cases from JSONL or JSON-list file."""
    p = Path(path)
    if not p.exists():
        raise FileNotFoundError(f"RAG eval case file not found: {p}")

    text = p.read_text(encoding="utf-8").strip()
    if not text:
        return []

    raw_items: List[Dict[str, Any]] = []
    if p.suffix.lower() == ".json":
        raw = json.loads(text)
        if isinstance(raw, dict) and isinstance(raw.get("cases"), list):
            raw = raw["cases"]
        if not isinstance(raw, list):
            raise ValueError("JSON eval case file must be a list or {'cases': [...]} object")
        raw_items = [x for x in raw if isinstance(x, dict)]
    else:
        for line_no, line in enumerate(text.splitlines(), start=1):
            line = line.strip()
            if not line:
                continue
            obj = json.loads(line)
            if not isinstance(obj, dict):
                raise ValueError(f"JSONL line {line_no} must be an object")
            raw_items.append(obj)

    return [RagEvalCase.from_dict(item, index=i) for i, item in enumerate(raw_items)]


def index_cases_by_query(cases: Iterable[RagEvalCase]) -> Dict[str, RagEvalCase]:
    """Build a query -> case index. Duplicate query keeps the first case."""
    index: Dict[str, RagEvalCase] = {}
    for case in cases:
        index.setdefault(case.query, case)
    return index


def match_case_for_run(run_record: Dict[str, Any], cases_by_query: Dict[str, RagEvalCase]) -> Optional[RagEvalCase]:
    query = str(run_record.get("query") or "").strip()
    if not query:
        return None
    return cases_by_query.get(query)
