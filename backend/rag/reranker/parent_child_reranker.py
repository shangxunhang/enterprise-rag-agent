# -*- coding: utf-8 -*-
"""P3 父子检索重排：使用最终父级上下文而非仅命中子块打分。

输入是 P2 的 ``retrieval_result_v2``；Cross-Encoder 默认计算 Query 与 ``parent_text`` 的
相关度，写入 ``rerank_score`` 并更新排名。该层不执行召回、RRF 或 Context Packing。
"""

from __future__ import annotations

from copy import deepcopy
from typing import Any, Dict, Iterable, List, Optional
from core.diagnostics import capture_event, capture_pool, current_diagnostics


def _safe_float(value: Any, default: float = 0.0) -> float:
    """安全规范化原召回分数和 Cross-Encoder Logit。"""
    try:
        if value is None:
            return default
        return float(value)
    except Exception:
        return default


def _get_candidate_text(result: Dict[str, Any], text_field: str = "parent_text") -> str:
    """按指定字段取重排文本，并依次回退到父块、通用正文和子块正文。"""
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
        """保留 P2 顺序，把原 ``score`` 投影为 ``rerank_score``，供无模型测试使用。"""
        if current_diagnostics().enabled:
            capture_event("R-RERANK", {"phase": "REQUEST", "query": query, "text_field": text_field,
                "model": "noop", "implementation": "NoOpParentChildReranker",
                "input_ids": [item.get("parent_chunk_id") or item.get("chunk_id") for item in results],
                "actual_texts": [_get_candidate_text(item, text_field) for item in results]}, unit_id="C08", operation_id="noop_rerank")
        del query, text_field
        selected = [deepcopy(x) for x in results]
        capture_pool("R-RERANK", selected, unit_id="C08", operation_id="noop_before_top_k", phase="BEFORE_TOP_K", model="noop", top_k=top_k)
        if top_k is not None:
            selected = selected[: int(top_k)]
        for idx, item in enumerate(selected, start=1):
            item["rank"] = idx
            item["rerank_score"] = _safe_float(item.get("score"))
            meta = dict(item.get("metadata") or {})
            meta["retrieval_stage"] = "p3_noop_rerank"
            meta["reranker"] = "noop"
            item["metadata"] = meta
        capture_pool("R-RERANK", selected, unit_id="C08", operation_id="noop_after_top_k", phase="RESULT", model="noop", top_k=top_k)
        return selected


class ParentChildReranker:
    """面向父子检索结果的本地 Cross-Encoder 重排器。"""

    def __init__(
        self,
        model_name: str,
        *,
        device: str = "cuda",
        batch_size: int = 16,
        max_length: int = 512,
        local_files_only: bool = True,
    ):
        """延迟导入 PyTorch/Transformers 并加载本地 Cross-Encoder 资源。

        ``local_files_only`` 默认开启，避免运行时意外联网；最大序列长度和批量大小是显存与
        父块信息保留之间的直接权衡。
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
                if current_diagnostics().enabled:
                    try:
                        import hashlib
                        import json
                        token_ids = inputs["input_ids"].tolist()
                        masks = inputs.get("attention_mask")
                        lengths = [sum(mask) for mask in masks.tolist()] if masks is not None else [len(ids) for ids in token_ids]
                        capture_event("R-RERANK", {"phase": "TOKENIZED", "batch_start": start,
                            "model": self.model_name, "tokenizer": getattr(self.tokenizer, "name_or_path", type(self.tokenizer).__name__),
                            "max_length": self.max_length, "truncation": True, "padding": True,
                            "input_token_digest": hashlib.sha256(json.dumps(token_ids, separators=(",", ":")).encode()).hexdigest(),
                            "input_token_lengths": lengths}, unit_id="C08", operation_id="rerank_tokenized")
                    except Exception:
                        current_diagnostics().emit({"event_type": "R-RERANK", "evidence_key": "R-RERANK", "unit_id": "C08",
                            "payload": {"phase": "TOKEN_SUMMARY_UNAVAILABLE", "complete": False}})
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
        """对候选副本批量打分，保留重排前排名/分数并按 Logit 重新编号。"""
        if not query or not str(query).strip():
            raise ValueError("query cannot be empty")
        if not results:
            return []

        candidates = [deepcopy(x) for x in results]
        pairs = [[str(query), _get_candidate_text(x, text_field=text_field)] for x in candidates]
        capture_event("R-RERANK", {"phase": "REQUEST", "query": str(query), "text_field": text_field,
            "model": self.model_name, "max_length": self.max_length,
            "input_ids": [item.get("parent_chunk_id") or item.get("chunk_id") for item in candidates],
            "actual_pairs": pairs}, unit_id="C08", operation_id="parent_cross_encoder_request")
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
        capture_pool("R-RERANK", reranked, unit_id="C08", operation_id="cross_encoder_before_top_k", query=query,
            phase="BEFORE_TOP_K", top_k=top_k, text_field=text_field, model=self.model_name)
        if top_k is not None:
            reranked = reranked[: int(top_k)]
        for idx, item in enumerate(reranked, start=1):
            item["rank"] = idx
        capture_pool("R-RERANK", reranked, unit_id="C08", operation_id="cross_encoder_after_top_k", query=query,
            phase="RESULT", top_k=top_k, text_field=text_field, model=self.model_name)
        return reranked

    def close(self) -> None:
        """Release the owned cross-encoder and return CUDA cache to the pool."""
        model = getattr(self, "model", None)
        if model is None:
            return
        try:
            model.to("cpu")
        except Exception:
            pass
        self.model = None
        self.tokenizer = None
        try:
            import gc

            gc.collect()
            if self._torch.cuda.is_available():
                self._torch.cuda.empty_cache()
        except Exception:
            pass
