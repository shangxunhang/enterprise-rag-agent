# =============================================================================
# 中文阅读说明：RAG 核心模块，负责查询变换、召回、融合、重排、证据评估和上下文组装。
# 主要定义：_safe_float、_resource_pool、ParentChildCandidateEnricher。建议先从公开入口函数开始，再沿调用关系向下阅读。
# =============================================================================
"""Parent-child candidate enrichment and parent backfill plugin."""

from __future__ import annotations

from collections import defaultdict
from typing import Any

from rag.schema.Retrieval_Result_Schema import build_retrieval_result_v2
from rag.schema.candidate import CandidateSet


# 阅读注释（函数）：处理 safe float 相关逻辑。
def _safe_float(value: Any, default: float = 0.0) -> float:
    """处理 safe float 相关逻辑。

    参数:
        value: value，具体约束请结合类型标注和调用方确认。
        default: default，具体约束请结合类型标注和调用方确认。

    返回:
        float

    阅读提示:
        主要直接调用：float。
    """
    try:
        return default if value is None else float(value)
    except Exception:
        return default


# 阅读注释（函数）：处理 resource pool 相关逻辑。
def _resource_pool(build_context: Any) -> Any:
    """处理 resource pool 相关逻辑。

    参数:
        build_context: build 上下文，具体约束请结合类型标注和调用方确认。

    返回:
        Any

    阅读提示:
        主要直接调用：isinstance, context.get, ValueError。
    """
    context = build_context if isinstance(build_context, dict) else {}
    pool = context.get("resource_pool")
    if pool is None:
        raise ValueError(
            "candidate enricher requires build_context['resource_pool']"
        )
    return pool


# 阅读注释（类）：封装 父块 子块 candidate enricher，集中封装相关状态、依赖和行为。
class ParentChildCandidateEnricher:
    """Deduplicate fused child hits by parent and build retrieval_result_v2."""

    # 阅读注释（函数）：初始化 ParentChildCandidateEnricher，保存运行所需的依赖、配置或状态。
    def __init__(
        self,
        *,
        build_context: Any = None,
        top_k: int = 10,
        context_granularity: str = "parent",
        dedup_parent: bool = True,
    ) -> None:
        """初始化 ParentChildCandidateEnricher，保存运行所需的依赖、配置或状态。

        参数:
            build_context: build 上下文，具体约束请结合类型标注和调用方确认。
            top_k: top k，具体约束请结合类型标注和调用方确认。
            context_granularity: 上下文 granularity，具体约束请结合类型标注和调用方确认。
            dedup_parent: dedup 父块，具体约束请结合类型标注和调用方确认。

        返回:
            None

        阅读提示:
            主要直接调用：ValueError, max, int, bool, get_parent_store, _resource_pool。
        """
        if context_granularity not in {"parent", "child"}:
            raise ValueError("context_granularity must be 'parent' or 'child'")
        self.top_k = max(1, int(top_k))
        self.context_granularity = context_granularity
        self.dedup_parent = bool(dedup_parent)
        self.parent_store = _resource_pool(build_context).get_parent_store()

    # 阅读注释（函数）：处理 group by 父块 相关逻辑。
    @staticmethod
    def _group_by_parent(
        candidates: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        """处理 group by 父块 相关逻辑。

        参数:
            candidates: candidates，具体约束请结合类型标注和调用方确认。

        返回:
            list[dict[str, Any]]

        阅读提示:
            主要直接调用：defaultdict, candidate.get, append, str, groups.values, items.sort, dict, set。
        """
        groups: dict[str, list[dict[str, Any]]] = defaultdict(list)
        missing_counter = 0
        for candidate in candidates:
            parent_id = candidate.get("parent_chunk_id") or ""
            if not parent_id:
                missing_counter += 1
                parent_id = (
                    f"__missing_parent__:"
                    f"{candidate.get('child_chunk_id') or missing_counter}"
                )
            groups[str(parent_id)].append(candidate)

        deduped: list[dict[str, Any]] = []
        for items in groups.values():
            items.sort(
                key=lambda item: _safe_float(
                    item.get("fusion_score") or item.get("score")
                ),
                reverse=True,
            )
            best = dict(items[0])
            matched_child_ids: list[str] = []
            matched_child_chunks: list[dict[str, Any]] = []
            matched_sources: set[str] = set()
            dense_scores: list[float] = []
            keyword_scores: list[float] = []

            for item in items:
                child_chunk = item.get("child_chunk") or {}
                child_id = (
                    item.get("child_chunk_id")
                    or item.get("chunk_id")
                    or child_chunk.get("chunk_id")
                    or child_chunk.get("child_chunk_id")
                )
                if child_id and child_id not in matched_child_ids:
                    matched_child_ids.append(str(child_id))
                    matched_child_chunks.append(
                        {
                            "chunk_id": child_id,
                            "child_chunk_id": child_id,
                            "parent_chunk_id": (
                                child_chunk.get("parent_chunk_id")
                                or item.get("parent_chunk_id")
                            ),
                            "doc_id": child_chunk.get("doc_id") or item.get("doc_id"),
                            "text": (
                                child_chunk.get("text")
                                or item.get("child_text")
                                or ""
                            ),
                            "title": child_chunk.get("title") or item.get("title"),
                            "section": (
                                child_chunk.get("section") or item.get("section")
                            ),
                            "page_start": (
                                child_chunk.get("page_start")
                                or item.get("page_start")
                            ),
                            "page_end": (
                                child_chunk.get("page_end") or item.get("page_end")
                            ),
                            "source_unit_ids": (
                                child_chunk.get("source_unit_ids") or []
                            ),
                        }
                    )
                for source in item.get("retrieval_sources", []) or []:
                    matched_sources.add(str(source))
                if item.get("dense_score") is not None:
                    dense_scores.append(_safe_float(item.get("dense_score")))
                if item.get("keyword_score") is not None:
                    keyword_scores.append(_safe_float(item.get("keyword_score")))

            best["matched_child_chunk_ids"] = matched_child_ids
            best["matched_child_chunks"] = matched_child_chunks
            best["matched_child_count"] = len(matched_child_ids)
            best["retrieval_sources"] = (
                sorted(matched_sources)
                if matched_sources
                else best.get("retrieval_sources", [])
            )
            if dense_scores:
                best["best_dense_score"] = max(dense_scores)
            if keyword_scores:
                best["best_keyword_score"] = max(keyword_scores)
            deduped.append(best)

        deduped.sort(
            key=lambda item: _safe_float(
                item.get("fusion_score") or item.get("score")
            ),
            reverse=True,
        )
        for rank, candidate in enumerate(deduped, start=1):
            candidate["rank"] = rank
            candidate["score"] = _safe_float(
                candidate.get("fusion_score") or candidate.get("score")
            )
        return deduped

    # 阅读注释（函数）：补充并丰富 ParentChildCandidateEnricher。
    def enrich(self, candidate_set: CandidateSet) -> CandidateSet:
        """补充并丰富 ParentChildCandidateEnricher。

        参数:
            candidate_set: candidate set，具体约束请结合类型标注和调用方确认。

        返回:
            CandidateSet

        阅读提示:
            主要直接调用：list, self._group_by_parent, dict, candidate_set.metadata.get, source_sets.get, int, dense_meta.get, get。
        """
        fused = list(candidate_set.candidates)
        grouped = self._group_by_parent(fused) if self.dedup_parent else fused
        selected = grouped[: self.top_k]
        source_sets = dict(candidate_set.metadata.get("source_sets") or {})
        dense_meta = dict(source_sets.get("dense") or {})
        dense_hits = int(dense_meta.get("candidate_count") or 0)
        keyword_hits = int(
            (source_sets.get("keyword") or {}).get("candidate_count") or 0
        )
        results: list[dict[str, Any]] = []

        for rank, candidate in enumerate(selected, start=1):
            child_chunk = candidate.get("child_chunk") or {}
            parent_id = (
                candidate.get("parent_chunk_id")
                or child_chunk.get("parent_chunk_id")
            )
            parent_chunk = self.parent_store.get(parent_id) if parent_id else None
            result = build_retrieval_result_v2(
                child_chunk=child_chunk,
                parent_chunk=parent_chunk,
                rank=rank,
                score=_safe_float(
                    candidate.get("fusion_score") or candidate.get("score")
                ),
                rerank_score=None,
                embedding_model=dense_meta.get("embedding_model"),
                embedding_version=(
                    dense_meta.get("embedding_version") or "embedding_v1"
                ),
                index_name=dense_meta.get("index_name"),
                vector_db=dense_meta.get("vector_db") or "none",
                context_granularity=self.context_granularity,
                metadata={
                    "retrieval_stage": "p2_hybrid_rrf_parent_backfill",
                    "retrieval_sources": candidate.get("retrieval_sources", []),
                    "fusion_score": _safe_float(
                        candidate.get("fusion_score") or candidate.get("score")
                    ),
                    "rrf_k": candidate_set.metadata.get("rrf_k"),
                    "dense_rank": candidate.get("dense_rank"),
                    "dense_score": candidate.get("dense_score"),
                    "keyword_rank": candidate.get("keyword_rank"),
                    "keyword_score": candidate.get("keyword_score"),
                    "source_ranks": candidate.get("source_ranks", {}),
                    "source_scores": candidate.get("source_scores", {}),
                    "rrf_contributions": candidate.get("rrf_contributions", {}),
                    "parent_found": parent_chunk is not None,
                    "dedup_parent": self.dedup_parent,
                    "matched_child_chunk_ids": candidate.get(
                        "matched_child_chunk_ids",
                        [candidate.get("child_chunk_id")],
                    ),
                    "matched_child_chunks": candidate.get(
                        "matched_child_chunks", []
                    ),
                    "matched_child_count": candidate.get(
                        "matched_child_count", 1
                    ),
                    "dense_hits": dense_hits,
                    "keyword_hits": keyword_hits,
                    "fused_candidates": len(grouped),
                },
                extra={
                    "filter_expr": dense_meta.get("filter_expr"),
                    "keyword_doc_id": None,
                    "keyword_doc_ids": list(
                        (source_sets.get("keyword") or {}).get("doc_ids") or []
                    ),
                    "use_dense": "dense" in source_sets,
                    "use_keyword": "keyword" in source_sets,
                    "configured_retrieval_stack": True,
                },
            )
            results.append(result)

        return CandidateSet(
            query=candidate_set.query,
            source_name="parent_child_enriched",
            candidates=results,
            metadata={
                **dict(candidate_set.metadata),
                "selected_count": len(results),
                "dedup_parent": self.dedup_parent,
                "context_granularity": self.context_granularity,
            },
        )
