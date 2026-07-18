# -*- coding: utf-8 -*-
"""
rag_template/reranker/parent_child_reranker.py
=============================================

P3 parent-child reranker:
- Input: retrieval_result_v2 list from P2 hybrid retriever.
- Score: query + parent_text by a cross-encoder reranker.
- Output: reranked retrieval_result_v2 list with rerank_score and updated rank.

职责边界：
- 只做 rerank，不做检索、不做 BM25、不做 RRF、不做 prompt packing。
- 第一版默认用 parent_text，因为最终进入 prompt 的上下文就是 parent。
"""

from __future__ import annotations

from copy import deepcopy
from typing import Any, Dict, Iterable, List, Optional


def _safe_float(value: Any, default: float = 0.0) -> float:
    try:
        if value is None:
            return default
        return float(value)
    except Exception:
        return default


def _get_candidate_text(result: Dict[str, Any], text_field: str = "parent_text") -> str:
    """Return the text used for rerank."""
    if text_field and result.get(text_field):
        return str(result.get(text_field) or "")
    return str(result.get("parent_text") or result.get("text") or result.get("child_text") or "")


class NoOpParentChildReranker:
    """No-op reranker for smoke tests.

    It preserves the P2 order and writes rerank_score = original score.
    Useful when the reranker model is not available yet.
    """

    def rerank(
        self,
        query: str,
        results: List[Dict[str, Any]],
        *,
        top_k: Optional[int] = None,
        text_field: str = "parent_text",
    ) -> List[Dict[str, Any]]:
        del query, text_field
        selected = [deepcopy(x) for x in results]
        if top_k is not None:
            selected = selected[: int(top_k)]
        for idx, item in enumerate(selected, start=1):
            item["rank"] = idx
            item["rerank_score"] = _safe_float(item.get("score"))
            meta = dict(item.get("metadata") or {})
            meta["retrieval_stage"] = "p3_noop_rerank"
            meta["reranker"] = "noop"
            item["metadata"] = meta
        return selected


class ParentChildReranker:
    """Cross-encoder reranker for parent-child retrieval results."""

    def __init__(
        self,
        model_name: str,
        *,
        device: str = "cuda",
        batch_size: int = 16,
        max_length: int = 512,
        local_files_only: bool = True,
    ):
        if not model_name:
            raise ValueError("model_name is required")
        self.model_name = str(model_name)
        self.device = device
        self.batch_size = int(batch_size)
        self.max_length = int(max_length)
        self.local_files_only = bool(local_files_only)

        # Lazy import: keep import of this module cheap when only running smoke tests.
        import torch
        from transformers import AutoModelForSequenceClassification, AutoTokenizer

        self._torch = torch

        print("=" * 80)
        print("[ParentChildReranker] Loading reranker model")
        print(f"[ParentChildReranker] model_name       = {self.model_name}")
        print(f"[ParentChildReranker] device           = {self.device}")
        print(f"[ParentChildReranker] batch_size       = {self.batch_size}")
        print(f"[ParentChildReranker] max_length       = {self.max_length}")
        print(f"[ParentChildReranker] local_files_only = {self.local_files_only}")

        self.tokenizer = AutoTokenizer.from_pretrained(
            self.model_name,
            local_files_only=self.local_files_only,
        )
        self.model = AutoModelForSequenceClassification.from_pretrained(
            self.model_name,
            local_files_only=self.local_files_only,
        )
        self.model.to(self.device)
        self.model.eval()
        print("[ParentChildReranker] Model loaded")
        print("=" * 80)

    def score_pairs(self, pairs: List[List[str]]) -> List[float]:
        """Score [[query, text], ...] pairs."""
        if not pairs:
            return []

        all_scores: List[float] = []
        with self._torch.no_grad():
            for start in range(0, len(pairs), self.batch_size):
                batch_pairs = pairs[start:start + self.batch_size]
                inputs = self.tokenizer(
                    batch_pairs,
                    padding=True,
                    truncation=True,
                    max_length=self.max_length,
                    return_tensors="pt",
                )
                inputs = {k: v.to(self.device) for k, v in inputs.items()}
                outputs = self.model(**inputs)
                logits = outputs.logits
                scores = logits.view(-1).detach().cpu().float().tolist()
                all_scores.extend(float(x) for x in scores)
        return all_scores

    def rerank(
        self,
        query: str,
        results: List[Dict[str, Any]],
        *,
        top_k: Optional[int] = None,
        text_field: str = "parent_text",
    ) -> List[Dict[str, Any]]:
        """Rerank retrieval_result_v2 records by query + parent_text."""
        if not query or not str(query).strip():
            raise ValueError("query cannot be empty")
        if not results:
            return []

        candidates = [deepcopy(x) for x in results]
        pairs = [[str(query), _get_candidate_text(x, text_field=text_field)] for x in candidates]
        scores = self.score_pairs(pairs)

        reranked: List[Dict[str, Any]] = []
        for item, score in zip(candidates, scores):
            item["rerank_score"] = float(score)
            meta = dict(item.get("metadata") or {})
            meta["retrieval_stage"] = "p3_rerank_parent_context"
            meta["reranker"] = self.model_name
            meta["rerank_text_field"] = text_field
            meta["pre_rerank_rank"] = item.get("rank")
            meta["pre_rerank_score"] = item.get("score")
            item["metadata"] = meta
            reranked.append(item)

        reranked.sort(key=lambda x: _safe_float(x.get("rerank_score"), float("-inf")), reverse=True)
        if top_k is not None:
            reranked = reranked[: int(top_k)]
        for idx, item in enumerate(reranked, start=1):
            item["rank"] = idx
        return reranked
