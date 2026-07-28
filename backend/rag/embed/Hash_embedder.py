"""提供不依赖模型的确定性哈希向量器，用于 Mini-RAG 烟测。

该实现把中文单字和英文/数字连续词映射到固定维度并统计频次，再做 L2 归一化。
它只能验证分块、索引、检索和评测链是否闭环，不具备可靠语义表示能力，不能作为
生产 Embedding 或质量结论的依据。
"""

import hashlib
import re
from typing import List

import numpy as np


class HashTextEmbedder:
    """把 Token 通过稳定 MD5 哈希投影到固定维度向量。

    Attributes:
        embedding_dim: 输出向量维度。所有文档和查询必须使用同一维度才能比较。
    """

    def __init__(self, embedding_dim: int = 384) -> None:
        """保存哈希向量维度。

        Args:
            embedding_dim: 输出维度；调用方应传入正整数。

        当前历史实现不额外校验维度，非法值会在 NumPy 分配或取模时自然报错。
        """

        self.embedding_dim = embedding_dim

    def _tokenize(self, text: str) -> List[str]:
        """把文本切分为中文单字和英文/数字连续词。

        Args:
            text: 输入文本。

        Returns:
            转小写后的 Token 列表；空文本返回空列表。

        标点和空白被忽略，因此该实现不表达词序、句法或标点关系。
        """

        if not text:
            return []

        # 英文和数字按连续片段，中文按单个字符提取。
        return re.findall(
            r"[a-zA-Z0-9]+|[\u4e00-\u9fff]",
            text.lower(),
        )

    def _hash_token(self, token: str) -> int:
        """把 Token 稳定映射到向量下标。

        使用 MD5 而不是 Python 内置 ``hash``，因为内置哈希默认包含进程级随机盐，
        会导致相同文本跨运行产生不同向量。
        """

        # 第一步：计算 UTF-8 Token 的十六进制 MD5。
        hash_value = hashlib.md5(
            token.encode("utf-8")
        ).hexdigest()

        # 第二步：转换为整数并对向量维度取模。
        hash_int = int(hash_value, 16)
        return hash_int % self.embedding_dim

    def _encode_one(self, text: str) -> np.ndarray:
        """编码单条文本为 L2 归一化一维向量。

        Args:
            text: 输入文本。

        Returns:
            形状为 ``(embedding_dim,)`` 的 ``float32`` 向量。没有 Token 时为全零。
        """

        # 第一步：创建全零频次向量。
        vector = np.zeros(
            self.embedding_dim,
            dtype=np.float32,
        )

        # 第二步：每个 Token 在其哈希槽位累加一次；碰撞会合并频次。
        for token in self._tokenize(text):
            index = self._hash_token(token)
            vector[index] += 1.0

        # 第三步：非零向量做 L2 归一化，使点积可近似余弦相似度。
        norm = np.linalg.norm(vector)
        if norm > 0:
            vector = vector / norm

        return vector.astype("float32")

    def encode_texts(self, texts: List[str]) -> np.ndarray:
        """批量编码文本列表。

        Args:
            texts: 文本列表。

        Returns:
            形状为 ``(num_texts, embedding_dim)`` 的 ``float32`` 矩阵。

        空列表会由 ``np.vstack`` 抛出异常，和真实 TextEmbedder 的空批次拒绝语义一致。
        """

        embeddings = [self._encode_one(text) for text in texts]
        return np.vstack(embeddings).astype("float32")

    def encode_query(self, query: str) -> np.ndarray:
        """编码单个查询并保持二维查询矩阵协议。

        Returns:
            形状为 ``(1, embedding_dim)`` 的 ``float32`` 数组。
        """

        embedding = self._encode_one(query)
        return embedding.reshape(1, -1).astype("float32")
