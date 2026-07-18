"""
src/rag_template/reranker/reranker.py
====================================

Reranker 模块。

职责：
1. 接收 query 和 FAISS 召回的 chunks
2. 使用 cross-encoder reranker 对 query-chunk pair 打分
3. 按 rerank_score 重排
4. 返回 top-k chunks
"""

from typing import List, Dict

import torch
from transformers import AutoTokenizer, AutoModelForSequenceClassification


class TextReranker:
    """
    基于 HuggingFace CrossEncoder 的文本重排器。
    """

    def __init__(
        self,
        model_name: str,
        device: str = "cuda",
        batch_size: int = 16,
    ):
        self.model_name = model_name
        self.device = device
        self.batch_size = batch_size

        print("=" * 80)
        print("[Reranker] 正在加载 reranker 模型")
        print(f"[Reranker] model_name: {model_name}")
        print(f"[Reranker] device: {device}")

        self.tokenizer = AutoTokenizer.from_pretrained(model_name)
        self.model = AutoModelForSequenceClassification.from_pretrained(model_name)
        self.model.to(device)
        self.model.eval()

        print("[Reranker] 模型加载完成")
        print("=" * 80)

    @torch.no_grad()
    def score_pairs(self, pairs: List[List[str]]) -> List[float]:
        """
        对 query-text pairs 打分。

        Args:
            pairs: [[query, text], ...]

        Returns:
            scores: rerank 分数列表
        """
        all_scores = []

        for start in range(0, len(pairs), self.batch_size):
            batch_pairs = pairs[start:start + self.batch_size]

            inputs = self.tokenizer(
                batch_pairs,
                padding=True,
                truncation=True,
                max_length=512,
                return_tensors="pt",
            )

            inputs = {k: v.to(self.device) for k, v in inputs.items()}

            outputs = self.model(**inputs)

            logits = outputs.logits

            # 多数 reranker 输出 shape 为 [batch, 1]
            scores = logits.view(-1).detach().cpu().float().tolist()

            all_scores.extend(scores)

        return all_scores

    def rerank(
        self,
        query: str,
        retrieved_chunks: List[Dict],
        top_k: int,
    ) -> List[Dict]:
        """
        对 FAISS 召回结果进行重排。

        Args:
            query: 用户问题
            retrieved_chunks: FAISS top-N 结果
            top_k: rerank 后保留数量

        Returns:
            reranked_chunks: 重排后的 top-k chunks
        """
        if not retrieved_chunks:
            return []

        pairs = [
            [query, chunk.get("text", "")]
            for chunk in retrieved_chunks
        ]

        rerank_scores = self.score_pairs(pairs)

        reranked_chunks = []

        for chunk, rerank_score in zip(retrieved_chunks, rerank_scores):
            new_chunk = dict(chunk)
            new_chunk["rerank_score"] = float(rerank_score)
            reranked_chunks.append(new_chunk)

        reranked_chunks = sorted(
            reranked_chunks,
            key=lambda x: x.get("rerank_score", float("-inf")),
            reverse=True,
        )

        final_chunks = reranked_chunks[:top_k]

        # 重新更新 rank
        for idx, chunk in enumerate(final_chunks, start=1):
            chunk["rank"] = idx

        return final_chunks