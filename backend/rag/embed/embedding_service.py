# -*- coding: utf-8 -*-
"""统一真实模型和哈希烟测两种 Embedding 编码入口。

服务层返回向量、模型身份和版本，供离线索引 Manifest/VectorIndexRecord 记录血缘。
真实 ``TextEmbedder`` 采用延迟导入，因此未安装 sentence-transformers 的环境仍可
使用 ``HashTextEmbedder`` 跑通无模型烟测。
"""

from typing import Optional, Sequence, Tuple

import numpy as np
from core.diagnostics import capture_event, current_diagnostics

from rag.configs.SchemaConfig import DEFAULT_EMBEDDING_VERSION
from rag.embed.Hash_embedder import HashTextEmbedder


def resolve_default_embedding_model() -> Optional[str]:
    """从旧版 RAGConfig 读取默认 Embedding 模型路径。

    Returns:
        非空 ``EMBEDDING_MODEL_NAME`` 的字符串；配置导入失败或未配置时返回 ``None``。
    """

    # 延迟导入避免 Hash smoke test 被真实模型依赖和本地路径配置阻塞。
    try:
        from rag.configs.RAGConfig import EMBEDDING_MODEL_NAME

        if EMBEDDING_MODEL_NAME:
            return str(EMBEDDING_MODEL_NAME)
    except Exception:
        return None

    return None


def encode_texts_with_hash(
    texts: Sequence[str],
    dim: int,
) -> Tuple[np.ndarray, str, str]:
    """使用确定性哈希向量器批量编码文本。

    Args:
        texts: 待编码文本序列。
        dim: 哈希向量维度。

    Returns:
        ``(float32_vectors, embedding_model, embedding_version)``。模型名明确包含
        ``for_smoke_test``，防止该结果被误认为语义 Embedding。
    """

    # 第一步：为指定维度创建无模型向量器。
    embedder = HashTextEmbedder(embedding_dim=dim)

    # 第二步：序列转换为列表后批量编码，统一输出 float32。
    inputs = list(texts)
    capture_event("I-ENCODE", {"phase": "REQUEST", "texts": inputs, "model": "hash_for_smoke_test", "dim": dim,
        "semantic_embedding": False}, unit_id="A06", operation_id="hash_encode")
    vectors = embedder.encode_texts(inputs)
    capture_event("I-ENCODE", {"phase": "RESULT", "shape": list(vectors.shape), "model": "hash_for_smoke_test",
        "row_ordinals": list(range(len(inputs))), "vectors_copied": False}, unit_id="A06", operation_id="hash_encode")

    # 第三步：返回可进入索引血缘的显式烟测身份。
    embedding_model = f"hash_embedding_dim_{dim}_for_smoke_test"
    embedding_version = "hash_embedding_v1.0"
    return (
        vectors.astype("float32"),
        embedding_model,
        embedding_version,
    )


def encode_query_with_hash(query: str, dim: int) -> np.ndarray:
    """使用哈希向量器编码单个查询为一维 ``float32`` 向量。"""

    # HashTextEmbedder 返回二维查询矩阵；检索适配器需要一维向量时在此展平。
    embedder = HashTextEmbedder(embedding_dim=dim)
    return (
        embedder.encode_query(query)
        .reshape(-1)
        .astype("float32")
    )


def encode_texts_with_model(
    texts: Sequence[str],
    model_name: Optional[str],
    device: str,
    batch_size: int,
    embedding_version: str = DEFAULT_EMBEDDING_VERSION,
) -> Tuple[np.ndarray, str, str]:
    """使用 SentenceTransformer 批量编码文本并返回模型血缘。

    Args:
        texts: 待编码文本序列。
        model_name: 显式模型路径或名称；为空时尝试旧版默认配置。
        device: 模型运行设备。
        batch_size: 文档编码批大小。
        embedding_version: 写入索引记录的 Embedding 版本。

    Returns:
        ``(float32_vectors, resolved_model_name, embedding_version)``。

    Raises:
        ValueError: 显式参数和默认配置都无法解析模型时抛出。
    """

    # 第一步：优先使用调用方显式模型，缺失时回退旧版默认配置。
    if not model_name:
        model_name = resolve_default_embedding_model()
    if not model_name:
        raise ValueError(
            "No embedding model resolved. Pass --embedding-model "
            "<local_model_path_or_name> or use --hash-embedding "
            "for smoke test."
        )

    # 第二步：只在真实模型路径被选择时导入 SentenceTransformer 适配器。
    from rag.embed.embedder import TextEmbedder

    # 第三步：创建模型并批量编码。当前历史函数不主动 close，调用生命周期由上层负责。
    embedder = TextEmbedder(
        model_name=str(model_name),
        device=device,
        batch_size=batch_size,
    )
    inputs = list(texts)
    if current_diagnostics().enabled:
        capture_event("I-ENCODE", {"phase": "REQUEST", "texts": inputs, "model": str(model_name),
            "batch_size": batch_size, "normalize_embeddings": True,
            "max_seq_length": getattr(getattr(embedder, "model", None), "max_seq_length", None),
            "tokenizer_visible_input": "NOT_RECORDED"}, unit_id="A06", operation_id="model_encode")
    vectors = embedder.encode_texts(inputs)
    capture_event("I-ENCODE", {"phase": "RESULT", "shape": list(vectors.shape), "model": str(model_name),
        "row_ordinals": list(range(len(inputs))), "vectors_copied": False}, unit_id="A06", operation_id="model_encode")

    # 第四步：返回统一 dtype 和实际模型身份。
    return (
        vectors.astype("float32"),
        str(model_name),
        embedding_version,
    )


def encode_query_with_model(
    query: str,
    model_name: str,
    device: str,
    batch_size: int = 1,
) -> np.ndarray:
    """使用 SentenceTransformer 编码查询为一维 ``float32`` 向量。

    Args:
        query: 非空查询文本。
        model_name: 模型路径或名称。
        device: 模型运行设备。
        batch_size: 保留与 TextEmbedder 构造接口一致，单查询默认 1。
    """

    # 延迟导入保证 Hash-only 环境不需要安装 sentence-transformers。
    from rag.embed.embedder import TextEmbedder

    embedder = TextEmbedder(
        model_name=str(model_name),
        device=device,
        batch_size=batch_size,
    )

    # TextEmbedder 返回二维查询矩阵；在线检索组件通常需要一维数组。
    return (
        embedder.encode_query(query)
        .reshape(-1)
        .astype("float32")
    )
