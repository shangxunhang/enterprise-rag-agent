"""
src/embedder.py
===============

Embedding 编码模块。

第一版为了不依赖网络下载模型，先使用本地哈希向量化方案。

注意：
1. 这个版本不是高质量语义 embedding。
2. 它只是为了先跑通 Mini-RAG 工程闭环。
3. 后续可以替换为 sentence-transformers / BGE / text2vec 等真实 embedding 模型。
"""

import hashlib
import re
from typing import List

import numpy as np


class HashTextEmbedder:
    """
    本地哈希文本向量化器。

    核心思想：
    1. 把文本切成 token
    2. 每个 token 通过 hash 映射到固定维度向量中的某一维
    3. 统计 token 出现次数
    4. 对最终向量做 L2 归一化

    优点：
    - 不需要联网
    - 不需要下载模型
    - 不依赖 torch
    - 可以先跑通 RAG 闭环

    缺点：
    - 不是真正的语义 embedding
    - 对同义词、复杂语义理解能力很弱
    """

    def __init__(self, embedding_dim: int = 384):
        """
        初始化哈希向量化器。

        Args:
            embedding_dim: 向量维度
        """
        self.embedding_dim = embedding_dim

    def _tokenize(self, text: str) -> List[str]:
        """
        简单分词。

        对中文：
        - 按单字切分

        对英文/数字：
        - 按连续单词切分

        Args:
            text: 输入文本

        Returns:
            token 列表
        """
        if not text:
            return []

        # 提取英文单词、数字、中文字符
        tokens = re.findall(r"[a-zA-Z0-9]+|[\u4e00-\u9fff]", text.lower())

        return tokens

    def _hash_token(self, token: str) -> int:
        """
        将 token 映射到向量下标。

        使用 md5 是为了保证每次运行结果稳定。
        Python 内置 hash() 每次进程可能不同，不适合这里。

        Args:
            token: 文本 token

        Returns:
            向量维度下标
        """
        hash_value = hashlib.md5(token.encode("utf-8")).hexdigest()
        hash_int = int(hash_value, 16)

        return hash_int % self.embedding_dim

    def _encode_one(self, text: str) -> np.ndarray:
        """
        编码单条文本。

        Args:
            text: 输入文本

        Returns:
            shape = (embedding_dim,) 的向量
        """
        vector = np.zeros(self.embedding_dim, dtype=np.float32)

        tokens = self._tokenize(text)

        for token in tokens:
            idx = self._hash_token(token)
            vector[idx] += 1.0

        # L2 归一化
        norm = np.linalg.norm(vector)

        if norm > 0:
            vector = vector / norm

        return vector.astype("float32")

    def encode_texts(self, texts: List[str]) -> np.ndarray:
        """
        批量编码文本。

        Args:
            texts: 文本列表

        Returns:
            embeddings: shape = (num_texts, embedding_dim)
        """
        embeddings = [self._encode_one(text) for text in texts]

        return np.vstack(embeddings).astype("float32")

    def encode_query(self, query: str) -> np.ndarray:
        """
        编码用户 query。

        Args:
            query: 用户问题

        Returns:
            query_embedding: shape = (1, embedding_dim)
        """
        embedding = self._encode_one(query)

        return embedding.reshape(1, -1).astype("float32")