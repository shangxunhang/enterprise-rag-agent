# -*- coding: utf-8 -*-
"""
rag_template/ranker/rrf_fusion.py
=================================

P2 RRF fusion:
- Merge dense child hits and keyword child hits.
- Score by Reciprocal Rank Fusion instead of directly adding Milvus/BM25 scores.
- Preserve evidence fields for later parent backfill / rerank.

RRF formula:
    fusion_score = sum(1 / (rrf_k + rank_i))
"""

from __future__ import annotations

from copy import deepcopy
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence


def _safe_float(value: Any, default: float = 0.0) -> float:
    try:
        if value is None:
            return default
        return float(value)
    except Exception:
        return default


def _safe_int(value: Any, default: int = 10**9) -> int:
    try:
        if value is None:
            return default
        return int(value)
    except Exception:
        return default


def get_child_key(hit: Mapping[str, Any]) -> str:
    """Return stable child candidate key."""
    key = hit.get("child_chunk_id") or hit.get("chunk_id")
    if key:
        return str(key)
    child = hit.get("child_chunk") if isinstance(hit.get("child_chunk"), dict) else {}
    key = child.get("child_chunk_id") or child.get("chunk_id")
    return str(key or "")


def _ensure_source_fields(candidate: Dict[str, Any]) -> None:
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
    """Fuse multiple ranked child-hit lists using RRF.

    Args:
        ranked_lists: mapping from source name to ranked hits, for example
            {"dense": dense_hits, "keyword": keyword_hits}.
        rrf_k: RRF constant. Common default is 60.
        top_k: optional final cutoff.

    Returns:
        List of fused child candidates sorted by fusion_score desc.
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
