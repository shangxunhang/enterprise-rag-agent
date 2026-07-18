"""Multi-query RRF fusion independent from the RAG engine shell."""

from __future__ import annotations

from typing import Any, Dict, List


class MultiQueryFusion:
    @staticmethod
    def result_key(result: Dict[str, Any]) -> str:
        return str(
            result.get("parent_chunk_id")
            or result.get("context_chunk_id")
            or result.get("child_chunk_id")
            or result.get("matched_chunk_id")
            or result.get("chunk_id")
            or ""
        )

    @staticmethod
    def safe_rank(value: Any, fallback: int) -> int:
        try:
            return fallback if value is None else int(value)
        except Exception:
            return fallback

    @staticmethod
    def safe_score(value: Any, default: float = 0.0) -> float:
        try:
            return default if value is None else float(value)
        except Exception:
            return default

    def fuse(
        self,
        query_results: Dict[str, List[Dict[str, Any]]],
        *,
        rrf_k: int,
        top_k: int,
    ) -> List[Dict[str, Any]]:
        if not query_results:
            return []
        fused: Dict[str, Dict[str, Any]] = {}
        for query_label, results in query_results.items():
            for fallback_rank, raw_result in enumerate(results or [], start=1):
                if not isinstance(raw_result, dict):
                    continue
                key = self.result_key(raw_result)
                if not key:
                    continue
                rank = self.safe_rank(raw_result.get("rank"), fallback_rank)
                contribution = 1.0 / (float(rrf_k) + float(rank))
                if key not in fused:
                    item = dict(raw_result)
                    metadata = dict(item.get("metadata") or {})
                    metadata.setdefault("query_fusion_source_ranks", {})
                    metadata.setdefault("query_fusion_contributions", {})
                    metadata.setdefault("query_fusion_queries", [])
                    metadata["query_fusion_score"] = 0.0
                    metadata["query_fusion_stage"] = "rag_fusion_multi_query_rrf"
                    item["metadata"] = metadata
                    fused[key] = item
                item = fused[key]
                metadata = item.setdefault("metadata", {})
                metadata.setdefault("query_fusion_source_ranks", {})[query_label] = rank
                metadata.setdefault("query_fusion_contributions", {})[
                    query_label
                ] = contribution
                if query_label not in metadata.setdefault("query_fusion_queries", []):
                    metadata["query_fusion_queries"].append(query_label)
                metadata["query_fusion_score"] = self.safe_score(
                    metadata.get("query_fusion_score")
                ) + contribution
        values = list(fused.values())
        values.sort(
            key=lambda item: (
                self.safe_score(
                    (item.get("metadata") or {}).get("query_fusion_score")
                ),
                self.safe_score(item.get("score")),
            ),
            reverse=True,
        )
        selected = values[: int(top_k)]
        for rank, item in enumerate(selected, start=1):
            metadata = item.setdefault("metadata", {})
            item["rank"] = rank
            item["score"] = self.safe_score(
                metadata.get("query_fusion_score") or item.get("score")
            )
            metadata["retrieval_stage"] = "p2_rag_fusion_parent_backfill"
            sources = metadata.get("retrieval_sources", [])
            if "query_fusion" not in sources:
                metadata["retrieval_sources"] = list(sources) + ["query_fusion"]
        return selected
