# -*- coding: utf-8 -*-
"""融合 dense 与 keyword 子块命中的 Reciprocal Rank Fusion 实现。

Milvus 距离和 BM25 分数不可直接相加，因此按每个来源的排名计算
``sum(1 / (rrf_k + rank_i))``。输出仍保留子块正文、父块标识、各来源原始排名/分数和
单次贡献，供后续父级回填、重排和审计使用。
"""

from __future__ import annotations

from copy import deepcopy
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence


def _safe_float(value: Any, default: float = 0.0) -> float:
    """安全转换来源分数；非法值不贡献融合排序。"""
    try:
        if value is None:
            return default
        return float(value)
    except Exception:
        return default


def _safe_int(value: Any, default: int = 10**9) -> int:
    """安全转换来源排名；非法值按极低优先级处理。"""
    try:
        if value is None:
            return default
        return int(value)
    except Exception:
        return default


def get_child_key(hit: Mapping[str, Any]) -> str:
    """从顶层或嵌套 ``child_chunk`` 中提取稳定子块身份。"""
    key = hit.get("child_chunk_id") or hit.get("chunk_id")
    if key:
        return str(key)
    child = hit.get("child_chunk") if isinstance(hit.get("child_chunk"), dict) else {}
    key = child.get("child_chunk_id") or child.get("chunk_id")
    return str(key or "")


def _ensure_source_fields(candidate: Dict[str, Any]) -> None:
    """为候选初始化多来源排名、分数、贡献和融合分数容器。"""
    candidate.setdefault("retrieval_sources", [])
    candidate.setdefault("source_ranks", {})
    candidate.setdefault("source_scores", {})
    candidate.setdefault("fusion_score", 0.0)
    candidate.setdefault("rrf_contributions", {})


def rrf_fuse(
    ranked_lists: Mapping[str, Sequence[Dict[str, Any]]],
    *,
    rrf_k: int = 60,
    top_k: Optional[int] = None,
) -> List[Dict[str, Any]]:
    """按子块身份融合多个有序来源列表，并按 RRF 分数降序返回。

    第一次出现的候选提供基础 payload；后续来源只补齐缺失的子块、父块或文档信息，不能
    覆盖更完整的已有内容。排序先看融合分数，再偏向任一来源中最好排名。``top_k`` 只在
    完成全量融合和重编号后截断。
    """
    if rrf_k < 1:
        raise ValueError("rrf_k must be >= 1")

    fused: Dict[str, Dict[str, Any]] = {}

    for source, hits in ranked_lists.items():
        if not hits:
            continue
        source_name = str(source)
        for fallback_rank, raw_hit in enumerate(hits, start=1):
            if not isinstance(raw_hit, dict):
                continue
            child_key = get_child_key(raw_hit)
            if not child_key:
                continue

            rank = _safe_int(raw_hit.get("rank"), fallback_rank)
            contribution = 1.0 / (float(rrf_k) + float(rank))

            if child_key not in fused:
                candidate = deepcopy(raw_hit)
                candidate["chunk_id"] = str(candidate.get("chunk_id") or child_key)
                candidate["child_chunk_id"] = str(candidate.get("child_chunk_id") or child_key)
                candidate.setdefault("parent_chunk_id", "")
                candidate.setdefault("doc_id", "")
                candidate.setdefault("child_chunk", raw_hit.get("child_chunk", {}))
                _ensure_source_fields(candidate)
                fused[child_key] = candidate
            else:
                candidate = fused[child_key]
                # Prefer richer child_chunk payload if the existing one is empty.
                if not candidate.get("child_chunk") and raw_hit.get("child_chunk"):
                    candidate["child_chunk"] = deepcopy(raw_hit.get("child_chunk"))
                # Fill missing parent/doc fields.
                if not candidate.get("parent_chunk_id") and raw_hit.get("parent_chunk_id"):
                    candidate["parent_chunk_id"] = str(raw_hit.get("parent_chunk_id"))
                if not candidate.get("doc_id") and raw_hit.get("doc_id"):
                    candidate["doc_id"] = str(raw_hit.get("doc_id"))
                _ensure_source_fields(candidate)

            if source_name not in candidate["retrieval_sources"]:
                candidate["retrieval_sources"].append(source_name)
            candidate["source_ranks"][source_name] = rank
            candidate["source_scores"][source_name] = _safe_float(raw_hit.get("score"))
            candidate["rrf_contributions"][source_name] = contribution
            candidate["fusion_score"] += contribution

            # Convenience fields for downstream logging.
            if source_name == "dense":
                candidate["dense_rank"] = rank
                candidate["dense_score"] = _safe_float(raw_hit.get("score"))
            elif source_name in {"keyword", "bm25"}:
                candidate["keyword_rank"] = rank
                candidate["keyword_score"] = _safe_float(raw_hit.get("score"))

    candidates = list(fused.values())
    candidates.sort(
        key=lambda x: (
            _safe_float(x.get("fusion_score")),
            -min(_safe_int(v) for v in x.get("source_ranks", {}).values()) if x.get("source_ranks") else 0,
        ),
        reverse=True,
    )

    for rank, candidate in enumerate(candidates, start=1):
        candidate["rank"] = rank
        candidate["score"] = _safe_float(candidate.get("fusion_score"))
        candidate["retrieval_source"] = "hybrid"

    if top_k is not None:
        return candidates[: int(top_k)]
    return candidates
