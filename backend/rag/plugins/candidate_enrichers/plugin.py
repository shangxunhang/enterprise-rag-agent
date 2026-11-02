"""Parent aggregation validates all original observations before lossy dedup."""

from __future__ import annotations

from collections import defaultdict
from copy import deepcopy
from typing import Any
from core.diagnostics import capture_pool, current_diagnostics

from rag.schema.Retrieval_Result_Schema import (
    build_retrieval_result_v2,
    _p2_binding,
    _p2_validate_child,
)
from rag.schema.candidate import CandidateSet


def _safe_float(value: Any, default: float = 0.0) -> float:
    try:
        return default if value is None else float(value)
    except Exception:
        return default


def _resource_pool(build_context: Any) -> Any:
    context = build_context if isinstance(build_context, dict) else {}
    pool = context.get("resource_pool")
    if pool is None:
        raise ValueError("candidate enricher requires build_context['resource_pool']")
    return pool


class ParentChildCandidateEnricher:
    def __init__(
        self, *, build_context: Any = None, top_k: int = 10,
        context_granularity: str = "parent", dedup_parent: bool = True,
    ) -> None:
        if context_granularity not in {"parent", "child"}:
            raise ValueError("context_granularity must be 'parent' or 'child'")
        self.top_k = max(1, int(top_k))
        self.context_granularity = context_granularity
        self.dedup_parent = bool(dedup_parent)
        self.parent_store = _resource_pool(build_context).get_parent_store()

    @staticmethod
    def _group_by_parent(
        candidates: list[dict[str, Any]], *, dedup_parent: bool = True,
    ) -> list[dict[str, Any]]:
        # Complete original observations survive until parent validation.  An
        # inconsistent wrapper can claim a different parent from its raw child;
        # both alleged cohorts retain that observation and cannot expand by
        # silently dropping it.  No new global parent identity registry exists.
        cohorts: dict[str, list[dict[str, Any]]] = defaultdict(list)
        lawful: list[tuple[dict[str, Any], dict[str, Any]]] = []
        for candidate in candidates:
            if not isinstance(candidate, dict):
                continue
            raw = candidate.get("child_chunk")
            claims: set[str] = set()
            for container in (candidate, raw):
                if isinstance(container, dict):
                    value = container.get("parent_chunk_id")
                    if isinstance(value, str) and value.strip():
                        claims.add(value.strip())
            for parent_id in claims:
                cohorts[parent_id].append(deepcopy(candidate))
            try:
                child = _p2_validate_child(raw, candidate)
            except ValueError:
                # Invalid hit: no matched IDs, score, profile or source authority.
                # It remains in every claimed cohort solely to deny expansion.
                continue
            lawful.append((deepcopy(candidate), child))

        bindings: dict[tuple[str, ...], list[tuple[dict[str, Any], dict[str, Any]]]] = defaultdict(list)
        for observation, child in lawful:
            bindings[_p2_binding(child)].append((observation, child))

        grouped: list[dict[str, Any]] = []
        for items in bindings.values():
            ordered = sorted(
                items,
                key=lambda value: _safe_float(value[0].get("fusion_score") or value[0].get("score")),
                reverse=True,
            )
            batches = [ordered] if dedup_parent else [[item] for item in ordered]
            for batch in batches:
                best = deepcopy(batch[0][0])
                primary = deepcopy(batch[0][1])
                matched: list[dict[str, Any]] = []
                ids: list[str] = []
                sources: set[str] = set()
                dense_scores: list[float] = []
                keyword_scores: list[float] = []
                for observation, child in batch:
                    child_id = child["child_chunk_id"]
                    if child_id not in ids:
                        ids.append(child_id)
                        matched.append(deepcopy(child))
                    for source in observation.get("retrieval_sources", []) or []:
                        sources.add(str(source))
                    if observation.get("dense_score") is not None:
                        dense_scores.append(_safe_float(observation["dense_score"]))
                    if observation.get("keyword_score") is not None:
                        keyword_scores.append(_safe_float(observation["keyword_score"]))
                best.update(
                    child_chunk=primary,
                    chunk_id=primary["child_chunk_id"],
                    child_chunk_id=primary["child_chunk_id"],
                    parent_chunk_id=primary["parent_chunk_id"],
                    doc_id=primary["doc_id"],
                    tenant_id=primary["tenant_id"], kb_id=primary["kb_id"],
                    matched_child_chunk_ids=ids, matched_child_chunks=matched,
                    matched_child_count=len(ids), retrieval_sources=sorted(sources),
                    _p2_raw_matched_candidates=deepcopy(cohorts[primary["parent_chunk_id"]]),
                )
                if dense_scores:
                    best["best_dense_score"] = max(dense_scores)
                if keyword_scores:
                    best["best_keyword_score"] = max(keyword_scores)
                grouped.append(best)

        # Distinct lawful bindings stay separate child-owned projections.  The
        # established score/rank/top_k algorithm is unchanged for lawful cohorts.
        grouped.sort(
            key=lambda value: _safe_float(value.get("fusion_score") or value.get("score")),
            reverse=True,
        )
        for rank, candidate in enumerate(grouped, start=1):
            candidate["rank"] = rank
            candidate["score"] = _safe_float(candidate.get("fusion_score") or candidate.get("score"))
        return grouped

    def enrich(self, candidate_set: CandidateSet) -> CandidateSet:
        fused = list(candidate_set.candidates)
        # The same complete raw-cohort check also applies to dedup_parent=False.
        grouped = self._group_by_parent(fused, dedup_parent=self.dedup_parent)
        selected = grouped[: self.top_k]
        capture_pool("R-PARENT", fused, unit_id="C07", operation_id="parent_before_grouping", query=candidate_set.query,
            phase="BEFORE_GROUPING", dedup_parent=self.dedup_parent)
        capture_pool("R-PARENT", grouped, unit_id="C07", operation_id="parent_before_top_k", query=candidate_set.query,
            phase="BEFORE_TOP_K", top_k=self.top_k)
        if current_diagnostics().enabled:
            capture_pool("R-PARENT", selected, unit_id="C07", operation_id="parent_after_top_k", query=candidate_set.query,
                phase="AFTER_TOP_K", top_k=self.top_k, drop_reason="PARENT_TOP_K", dropped_ids=[item.get("parent_chunk_id") for item in grouped[self.top_k:]])
        source_sets = dict(candidate_set.metadata.get("source_sets") or {})
        dense_meta = dict(source_sets.get("dense") or {})
        dense_hits = int(dense_meta.get("candidate_count") or 0)
        keyword_hits = int((source_sets.get("keyword") or {}).get("candidate_count") or 0)
        results: list[dict[str, Any]] = []
        for rank, candidate in enumerate(selected, start=1):
            child = candidate["child_chunk"]
            parent_id = child["parent_chunk_id"]
            parent = self.parent_store.get(parent_id)
            result = build_retrieval_result_v2(
                child_chunk=child, parent_chunk=parent,
                _raw_matched_candidates=candidate["_p2_raw_matched_candidates"],
                rank=rank,
                score=_safe_float(candidate.get("fusion_score") or candidate.get("score")),
                rerank_score=None,
                embedding_model=dense_meta.get("embedding_model"),
                embedding_version=dense_meta.get("embedding_version") or "embedding_v1",
                index_name=dense_meta.get("index_name"),
                vector_db=dense_meta.get("vector_db") or "none",
                context_granularity=self.context_granularity,
                metadata={
                    "retrieval_stage": "p2_hybrid_rrf_parent_backfill",
                    "retrieval_sources": candidate.get("retrieval_sources", []),
                    "fusion_score": _safe_float(candidate.get("fusion_score") or candidate.get("score")),
                    "rrf_k": candidate_set.metadata.get("rrf_k"),
                    "dense_rank": candidate.get("dense_rank"),
                    "dense_score": candidate.get("dense_score"),
                    "keyword_rank": candidate.get("keyword_rank"),
                    "keyword_score": candidate.get("keyword_score"),
                    "source_ranks": candidate.get("source_ranks", {}),
                    "source_scores": candidate.get("source_scores", {}),
                    "rrf_contributions": candidate.get("rrf_contributions", {}),
                    "dedup_parent": self.dedup_parent,
                    "matched_child_chunk_ids": candidate["matched_child_chunk_ids"],
                    "matched_child_chunks": candidate["matched_child_chunks"],
                    "matched_child_count": candidate["matched_child_count"],
                    "dense_hits": dense_hits, "keyword_hits": keyword_hits,
                    "fused_candidates": len(grouped),
                },
                extra={
                    "filter_expr": dense_meta.get("filter_expr"),
                    "keyword_doc_id": None,
                    "keyword_doc_ids": list((source_sets.get("keyword") or {}).get("doc_ids") or []),
                    "use_dense": "dense" in source_sets,
                    "use_keyword": "keyword" in source_sets,
                    "configured_retrieval_stack": True,
                },
            )
            results.append(result)
        capture_pool("R-PARENT", results, unit_id="C07", operation_id="parent_enriched", query=candidate_set.query, phase="RESULT")
        return CandidateSet(
            query=candidate_set.query, source_name="parent_child_enriched",
            candidates=results,
            metadata={
                **dict(candidate_set.metadata),
                "selected_count": len(results), "dedup_parent": self.dedup_parent,
                "context_granularity": self.context_granularity,
            },
        )
