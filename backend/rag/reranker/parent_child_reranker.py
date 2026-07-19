# -*- coding: utf-8 -*-
# =============================================================================
# 中文阅读说明：RAG 核心模块，负责查询变换、召回、融合、重排、证据评估和上下文组装。
# 主要定义：_safe_float、_get_candidate_text、NoOpParentChildReranker、ParentChildReranker。建议先从公开入口函数开始，再沿调用关系向下阅读。
# =============================================================================
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
        if value is None:
            return default
        return float(value)
    except Exception:
        return default


# 阅读注释（函数）：获取 candidate 文本。
def _get_candidate_text(result: Dict[str, Any], text_field: str = "parent_text") -> str:
    """Return the text used for rerank."""
    if text_field and result.get(text_field):
        return str(result.get(text_field) or "")
    return str(result.get("parent_text") or result.get("text") or result.get("child_text") or "")


# 阅读注释（类）：封装 no op 父块 子块 reranker，集中封装相关状态、依赖和行为。
class NoOpParentChildReranker:
    """No-op reranker for smoke tests.

    It preserves the P2 order and writes rerank_score = original score.
    Useful when the reranker model is not available yet.
    """

    # 阅读注释（函数）：对 NoOpParentChildReranker 重新排序。
    def rerank(
        self,
        query: str,
        results: List[Dict[str, Any]],
        *,
        top_k: Optional[int] = None,
        text_field: str = "parent_text",
    ) -> List[Dict[str, Any]]:
        """对 NoOpParentChildReranker 重新排序。

        参数:
            query: 当前检索或生成查询。
            results: 待处理的结果集合。
            top_k: top k，具体约束请结合类型标注和调用方确认。
            text_field: 文本 field，具体约束请结合类型标注和调用方确认。

        返回:
            List[Dict[str, Any]]

        阅读提示:
            主要直接调用：deepcopy, int, enumerate, _safe_float, item.get, dict。
        """
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


# 阅读注释（类）：封装 父块 子块 reranker，集中封装相关状态、依赖和行为。
class ParentChildReranker:
    """Cross-encoder reranker for parent-child retrieval results."""

    # 阅读注释（函数）：初始化 ParentChildReranker，保存运行所需的依赖、配置或状态。
    def __init__(
        self,
        model_name: str,
        *,
        device: str = "cuda",
        batch_size: int = 16,
        max_length: int = 512,
        local_files_only: bool = True,
    ):
        """初始化 ParentChildReranker，保存运行所需的依赖、配置或状态。

        参数:
            model_name: 模型 名称，具体约束请结合类型标注和调用方确认。
            device: device，具体约束请结合类型标注和调用方确认。
            batch_size: batch size，具体约束请结合类型标注和调用方确认。
            max_length: max length，具体约束请结合类型标注和调用方确认。
            local_files_only: 本地 files only，具体约束请结合类型标注和调用方确认。

        返回:
            未显式标注；请结合调用方和实际返回语句理解。

        阅读提示:
            主要直接调用：ValueError, str, int, bool, print, AutoTokenizer.from_pretrained, AutoModelForSequenceClassification.from_pretrained, self.model.to。
        """
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

    # 阅读注释（函数）：计算 pairs 的评分。
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

    # 阅读注释（函数）：对 ParentChildReranker 重新排序。
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
