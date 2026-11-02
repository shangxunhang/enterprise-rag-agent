"""独立于 RAG Engine 外壳的多 Query 父级候选 RRF 融合。

输入按 Query 标签分组，每组内部已有排名；融合键优先采用 ``parent/context_chunk_id``，
因此同一父级上下文被不同改写 Query 命中时只保留一条候选，并累加倒数排名贡献。
"""

from __future__ import annotations

from typing import Any, Dict, List
from core.diagnostics import capture_pool


class MultiQueryFusion:
    """融合原 Query、改写 Query 和 HyDE Query 的父级候选。"""
    @staticmethod
    def result_key(result: Dict[str, Any]) -> str:
        """选择最能代表生成上下文身份的稳定键，并回退到命中 Chunk。"""
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
        """把排名转为整数，缺失或非法时使用列表位置。"""
        try:
            return fallback if value is None else int(value)
        except Exception:
            return fallback

    @staticmethod
    def safe_score(value: Any, default: float = 0.0) -> float:
        """把分数转为浮点数，非法值不参与排序。"""
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
        """按 ``1 / (rrf_k + rank)`` 累加多 Query 贡献并返回前 ``top_k``。

        metadata 保留每个 Query 的来源排名、单次贡献和参与 Query 列表；最终 ``score``
        改为 Query Fusion 分数，而原召回分数仅作为同分时的次级排序依据。
        """
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
        capture_pool("R-QUERY-FUSION", values, unit_id="C07", operation_id="query_rrf_before_top_k",
            phase="BEFORE_TOP_K", rrf_k=rrf_k, top_k=top_k)
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
        capture_pool("R-QUERY-FUSION", selected, unit_id="C07", operation_id="query_rrf_after_top_k",
            phase="RESULT", rrf_k=rrf_k, top_k=top_k, drop_reason="QUERY_FUSION_TOP_K")
        return selected
