# -*- coding: utf-8 -*-
# =============================================================================
# 中文阅读说明：离线评测模块，用于执行实验、评分、对比和报告生成。
# 主要定义：_safe_list、RagEvalCase、load_rag_eval_cases、index_cases_by_query、match_case_for_run。建议先从公开入口函数开始，再沿调用关系向下阅读。
# =============================================================================
"""
rag_template/eval/rag_eval_case_schema.py
=========================================

RAG evaluation case schema for parent-child RAG QA.

This module is intentionally lightweight and dependency-free. It supports JSONL
and JSON-list inputs, so the same eval cases can later be reused by a real RAGAS
runner, DeepSeek-as-judge, or an internal evaluator.
"""

from __future__ import annotations

from collections.abc import Mapping
import json
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional

from schemas.eval_schema import RAGEvalSampleSchema


# Legacy public name retained as a direct alias.  All RAG evaluation paths now
# validate against one canonical sample contract in schemas.eval_schema.
RagEvalCase = RAGEvalSampleSchema


# 阅读注释（函数）：加载 RAG 评测 cases。
def _load_raw_eval_payloads(path: str | Path) -> List[Dict[str, Any]]:
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
        raw_items = []
        for index, item in enumerate(raw, start=1):
            if not isinstance(item, dict):
                raise ValueError(f"JSON eval case item {index} must be an object")
            raw_items.append(item)
    else:
        for line_no, line in enumerate(text.splitlines(), start=1):
            line = line.strip()
            if not line:
                continue
            obj = json.loads(line)
            if not isinstance(obj, dict):
                raise ValueError(f"JSONL line {line_no} must be an object")
            raw_items.append(obj)

    return raw_items


def load_rag_eval_cases(path: str | Path) -> List[RagEvalCase]:
    """Load only canonical eval samples without hidden migration/defaulting."""

    return [
        RagEvalCase.model_validate(item, strict=True)
        for item in _load_raw_eval_payloads(path)
    ]


def migrate_legacy_rag_eval_case_v1_payload(
    payload: Mapping[str, Any],
    *,
    fallback_sample_id: str,
) -> Dict[str, Any]:
    """Explicitly migrate the legacy ``case_id`` alias to ``sample_id``."""

    if not isinstance(payload, Mapping):
        raise TypeError("legacy RAG eval migration requires an object payload")
    migrated = dict(payload)
    legacy_case_id = migrated.pop("case_id", None)
    explicit_sample_id = migrated.get("sample_id")
    if (
        legacy_case_id
        and explicit_sample_id
        and str(legacy_case_id) != str(explicit_sample_id)
    ):
        raise ValueError("case_id and sample_id must match when both are provided")
    if "sample_id" not in migrated:
        migrated["sample_id"] = (
            legacy_case_id
            if legacy_case_id is not None
            else fallback_sample_id
        )
    return migrated


def load_legacy_rag_eval_cases_v1(path: str | Path) -> List[RagEvalCase]:
    """Load pre-canonical cases through the explicit v1 migration adapter."""

    return [
        RagEvalCase.model_validate(
            migrate_legacy_rag_eval_case_v1_payload(
                item,
                fallback_sample_id=f"rag_case_{index:04d}",
            ),
            strict=True,
        )
        for index, item in enumerate(_load_raw_eval_payloads(path), start=1)
    ]


# 阅读注释（函数）：处理 索引 cases by 查询 相关逻辑。
def index_cases_by_query(cases: Iterable[RagEvalCase]) -> Dict[str, RagEvalCase]:
    """Build a query -> case index. Duplicate query keeps the first case."""
    index: Dict[str, RagEvalCase] = {}
    for case in cases:
        index.setdefault(case.query, case)
    return index


# 阅读注释（函数）：处理 match case for run 相关逻辑。
def match_case_for_run(run_record: Dict[str, Any], cases_by_query: Dict[str, RagEvalCase]) -> Optional[RagEvalCase]:
    """处理 match case for run 相关逻辑。

    参数:
        run_record: run 记录，具体约束请结合类型标注和调用方确认。
        cases_by_query: cases by 查询，具体约束请结合类型标注和调用方确认。

    返回:
        Optional[RagEvalCase]

    阅读提示:
        主要直接调用：strip, str, run_record.get, cases_by_query.get。
    """
    query = str(run_record.get("query") or "").strip()
    if not query:
        return None
    return cases_by_query.get(query)
