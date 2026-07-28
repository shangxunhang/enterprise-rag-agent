"""使用 Sentence Transformers 把文本或查询编码为归一化 Dense 向量。

``TextEmbedder`` 只拥有模型加载、批量编码和资源释放，不负责分块、索引或检索。
所有输出统一为 ``float32`` NumPy 数组，并启用 L2 归一化，便于 COSINE/IP 检索链
使用稳定向量尺度。
"""

from typing import List, Optional

import numpy as np
from sentence_transformers import SentenceTransformer

from rag.configs.RAGConfig import *


class TextEmbedder:
    """封装一个 SentenceTransformer 模型及其编码参数。

    Attributes:
        model_name: 模型名称或本地目录。
        device: ``cuda``、``cpu`` 或 ``None`` 自动选择。
        batch_size: 文档批量编码大小。
        model: 当前持有的 SentenceTransformer；``close`` 后为 ``None``。
    """

    def __init__(
        self,
        model_name: str,
        device: Optional[str] = None,
        batch_size: int = 32,
    ) -> None:
        """加载 SentenceTransformer 模型。

        Args:
            model_name: Hugging Face 模型标识或本地模型路径。
            device: 推理设备；省略时由 SentenceTransformer 自动选择。
            batch_size: ``encode_texts`` 使用的批大小。

        模型在构造阶段立即加载，适合离线索引或长生命周期 Retriever 复用；短生命周期
        调用应在结束后显式 ``close`` 释放 GPU 内存。
        """

        self.model_name = model_name
        self.device = device
        self.batch_size = batch_size

        # 由 SentenceTransformer 负责加载 tokenizer、encoder 和 pooling 配置。
        self.model = SentenceTransformer(
            model_name_or_path=model_name,
            device=device,
        )

    def encode_texts(self, texts: List[str]) -> np.ndarray:
        """批量编码文档文本。

        Args:
            texts: 非空文本列表。

        Returns:
            形状为 ``(num_texts, embedding_dim)`` 的 L2 归一化 ``float32`` 矩阵。

        Raises:
            ValueError: 输入列表为空时抛出。
        """

        # 空批次通常表示上游分块或过滤错误，显式拒绝而不是返回形状不明的数组。
        if not texts:
            raise ValueError("texts 不能为空")

        # 批量编码时显示进度条，方便离线大规模索引观察进度。
        embeddings = self.model.encode(
            texts,
            batch_size=self.batch_size,
            convert_to_numpy=True,
            normalize_embeddings=True,
            show_progress_bar=True,
        )

        # Milvus/FAISS 通常要求 float32，避免模型默认 dtype 造成协议不一致。
        return embeddings.astype("float32")

    def encode_query(self, query: str) -> np.ndarray:
        """编码单个用户查询。

        Args:
            query: 非空查询文本。

        Returns:
            形状为 ``(1, embedding_dim)`` 的 L2 归一化 ``float32`` 矩阵。

        Raises:
            ValueError: 查询为空或仅包含空白时抛出。
        """

        if not query or not query.strip():
            raise ValueError("query 不能为空")

        # 使用单元素批次保持与文档编码相同的模型路径，并关闭进度条减少在线日志噪声。
        embedding = self.model.encode(
            [query],
            batch_size=1,
            convert_to_numpy=True,
            normalize_embeddings=True,
            show_progress_bar=False,
        )

        # reshape 强制保持二维查询协议，即使底层模型返回一维数组。
        return embedding.reshape(1, -1).astype("float32")

    def close(self) -> None:
        """释放模型引用并尽力清理 CUDA 缓存。

        方法具备幂等性。模型迁移到 CPU 或 PyTorch 清理失败时不会抛出，避免资源清理
        异常覆盖主流程原始结果。
        """

        # 已关闭实例无需重复处理。
        model = getattr(self, "model", None)
        if model is None:
            return

        # 第一步：尽力把权重移回 CPU，释放设备占用。
        try:
            model.to("cpu")
        except Exception:
            pass

        # 第二步：断开实例对模型的强引用。
        self.model = None

        # 第三步：触发垃圾回收并在 CUDA 可用时清空缓存。
        try:
            import gc
            import torch

            gc.collect()
            if torch.cuda.is_available():
                torch.cuda.empty_cache()
        except Exception:
            pass


# 直接执行时进行一次查询编码烟测，作为模块导入不会加载默认模型。
if __name__ == "__main__":
    embedder = TextEmbedder(
        model_name=EMBEDDING_MODEL_NAME,
        device=EMBEDDING_DEVICE,
        batch_size=EMBEDDING_BATCH_SIZE,
    )
    query_vector = embedder.encode_query("项目变更流程是什么")
    print("query shape:", query_vector.shape)
