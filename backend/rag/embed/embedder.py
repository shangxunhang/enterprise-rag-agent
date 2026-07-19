# =============================================================================
# 中文阅读说明：RAG 核心模块，负责查询变换、召回、融合、重排、证据评估和上下文组装。
# 主要定义：TextEmbedder。建议先从公开入口函数开始，再沿调用关系向下阅读。
# =============================================================================
"""
src/embedder.py
===============

Embedding 编码模块。

职责：
1. 加载 sentence-transformers embedding 模型
2. 把文本列表编码成向量
3. 返回 numpy.ndarray 格式的 embedding 矩阵

注意：
embedder 只负责“文本转向量”，不负责 FAISS 建索引。
"""

from typing import List, Optional

import numpy as np
from sentence_transformers import SentenceTransformer

from rag.configs.RAGConfig import *


# 阅读注释（类）：封装 文本 embedder，集中封装相关状态、依赖和行为。
class TextEmbedder:
    """
    文本向量化器。

    使用 sentence-transformers 模型将文本编码为 dense embedding。
    """

    # 阅读注释（函数）：初始化 TextEmbedder，保存运行所需的依赖、配置或状态。
    def __init__(
            self,
            model_name: str,
            device: Optional[str] = None,
            batch_size: int = 32,
    ):
        """
        初始化 embedding 模型。

        Args:
            model_name: sentence-transformers 模型名称或本地路径
            device: 运行设备，例如 "cuda" / "cpu"。None 表示自动选择。
            batch_size: 批量编码时的 batch size
        """
        self.model_name = model_name
        self.device = device
        self.batch_size = batch_size

        self.model = SentenceTransformer(
            model_name_or_path=model_name,
            device=device,
        )

    # 阅读注释（函数）：处理 encode texts 相关逻辑。
    def encode_texts(self, texts: List[str]) -> np.ndarray:
        """
        批量编码文本。

        Args:
            texts: 文本列表

        Returns:
            embeddings: shape = (num_texts, embedding_dim)
        """
        if not texts:
            raise ValueError("texts 不能为空")

        embeddings = self.model.encode(
            texts,
            batch_size=self.batch_size,
            convert_to_numpy=True,
            normalize_embeddings=True,
            show_progress_bar=True,
        )

        return embeddings.astype("float32")

    # 阅读注释（函数）：处理 encode 查询 相关逻辑。
    def encode_query(self, query: str) -> np.ndarray:
        """
        编码单个 query。

        Args:
            query: 用户问题

        Returns:
            query_embedding: shape = (1, embedding_dim)
        """

        if not query or not query.strip():
            raise ValueError("query 不能为空")

        embedding = self.model.encode(
            [query],
            batch_size=1,
            convert_to_numpy=True,
            normalize_embeddings=True,
            show_progress_bar=False,
        )

        return embedding.reshape(1, -1).astype("float32")


if __name__ == "__main__":
     embedder = TextEmbedder(
         model_name=EMBEDDING_MODEL_NAME,
         device=EMBEDDING_DEVICE,
         batch_size=EMBEDDING_BATCH_SIZE,
     )

     q = embedder.encode_query("项目变更流程是什么")
     print("query shape:", q.shape)
