# =============================================================================
# 中文阅读说明：RAG 核心模块，负责查询变换、召回、融合、重排、证据评估和上下文组装。
# 主要定义：FaissRetriever。建议先从公开入口函数开始，再沿调用关系向下阅读。
# =============================================================================
"""
src/retriever.py
================

检索模块。

职责：
1. 加载 FAISS index
2. 加载 chunk metadata
3. 使用 embedder 将 query 编码成向量
4. 在 FAISS 中检索 top-k 相似 chunk
5. 根据 FAISS 返回的 indices 找回原始 chunk 信息

注意：
FAISS 只返回向量编号和相似度分数。
真正的 chunk 文本需要从 chunk_meta.json 中按编号取回。
"""

from pathlib import Path
from typing import List, Dict, Any

import faiss
import numpy as np

from rag.Index.indexer import load_faiss_index, load_chunk_meta
from rag.schema.Retrieval_Result_Schema import build_retrieval_result


# 阅读注释（类）：封装 faiss retriever，集中封装相关状态、依赖和行为。
class FaissRetriever:
    """
    基于 FAISS 的向量检索器。

    它不负责构建索引，只负责加载已有索引并检索。
    """

    # 阅读注释（函数）：初始化 FaissRetriever，保存运行所需的依赖、配置或状态。
    def __init__(
        self,
        index_path: Path,
        chunk_meta_path: Path,
        embedder,
    ):
        """
        初始化检索器。

        Args:
            index_path: faiss.index 文件路径
            chunk_meta_path: chunk_meta.json 文件路径
            embedder: embedding 对象，需要实现 encode_query(query: str)
        """
        self.index_path = index_path
        self.chunk_meta_path = chunk_meta_path
        self.embedder = embedder

        self.index: faiss.Index = load_faiss_index(index_path)
        self.chunk_meta: List[Dict[str, Any]] = load_chunk_meta(chunk_meta_path)

        self._check_index_and_meta()

    # 阅读注释（函数）：检查 索引 and meta。
    def _check_index_and_meta(self) -> None:
        """
        检查 FAISS index 中的向量数量是否和 chunk_meta 数量一致。

        如果不一致，说明 faiss.index 和 chunk_meta.json 不是同一批数据生成的。
        """
        if self.index.ntotal != len(self.chunk_meta):
            raise ValueError(
                f"FAISS index 向量数量和 chunk_meta 数量不一致: "
                f"index.ntotal={self.index.ntotal}, "
                f"len(chunk_meta)={len(self.chunk_meta)}"
            )

    # 阅读注释（函数）：搜索 FaissRetriever。
    def search(self, query: str, top_k: int = 3) -> List[Dict[str, Any]]:
        """
        根据 query 检索 top-k chunk。

        Args:
            query: 用户问题
            top_k: 返回的 chunk 数量

        Returns:
            检索结果列表，每个元素包含：
            - rank
            - score
            - chunk_id
            - doc_id
            - source
            - chunk_index
            - text
        """
        if not query or not query.strip():
            raise ValueError("query 不能为空")

        if top_k <= 0:
            raise ValueError("top_k 必须大于 0")

        # 如果 top_k 大于索引总数，就截断
        top_k = min(top_k, self.index.ntotal)

        # 1. query -> embedding
        query_embedding: np.ndarray = self.embedder.encode_query(query)

        if query_embedding.ndim != 2:
            raise ValueError("query_embedding 必须是二维矩阵，shape = (1, embedding_dim)")

        # 2. FAISS search
        scores, indices = self.index.search(
            query_embedding.astype("float32"),
            top_k,
        )

        # scores shape:  (1, top_k)
        # indices shape: (1, top_k)
        scores = scores[0]
        indices = indices[0]

        results = []

        for rank, (score, idx) in enumerate(zip(scores, indices), start=1):
            # FAISS 在某些情况下可能返回 -1，表示无效结果
            if idx == -1:
                continue

            chunk = self.chunk_meta[idx]
            metadata = chunk.get("metadata", {})

            result = build_retrieval_result(
                chunk=chunk,
                rank=rank,
                score=score,

            )

            results.append(result)

        return results



if __name__ == "__main__":
    index = faiss.read_index("D:/MyCode/rag-template/vector_store/faiss.index")
    print("index.d:", index.d)
    print("index.ntotal:", index.ntotal)
