# -*- coding: utf-8 -*-
# =============================================================================
# 中文阅读说明：RAG 核心模块，负责查询变换、召回、融合、重排、证据评估和上下文组装。
# 主要定义：resolve_default_embedding_model、encode_texts_with_hash、encode_query_with_hash、encode_texts_with_model、encode_query_with_model。建议先从公开入口函数开始，再沿调用关系向下阅读。
# =============================================================================
"""
rag_template/embed/embedding_service.py
======================================

Embedding 服务层。

职责：
1. 复用现有 TextEmbedder 进行真实 embedding。
2. 复用 HashTextEmbedder 进行 smoke test。
3. 统一返回 vectors / embedding_model / embedding_dim / embedding_version。

注意：
- TextEmbedder / sentence-transformers 采用 lazy import。
- 这样没有安装 sentence-transformers 时，hash-embedding smoke test 仍然能运行。
"""

from typing import Optional, Sequence, Tuple

import numpy as np

from rag.embed.Hash_embedder import HashTextEmbedder
from rag.configs.SchemaConfig import DEFAULT_EMBEDDING_VERSION


# 阅读注释（函数）：解析并确定 default embedding 模型。
def resolve_default_embedding_model() -> Optional[str]:
    """Try to reuse rag_template.configs.RAGConfig.EMBEDDING_MODEL_NAME."""
    try:
        from rag.configs.RAGConfig import EMBEDDING_MODEL_NAME  # type: ignore
        if EMBEDDING_MODEL_NAME:
            return str(EMBEDDING_MODEL_NAME)
    except Exception:
        return None
    return None


# 阅读注释（函数）：处理 encode texts with hash 相关逻辑。
def encode_texts_with_hash(texts: Sequence[str], dim: int) -> Tuple[np.ndarray, str, str]:
    """处理 encode texts with hash 相关逻辑。

    参数:
        texts: texts，具体约束请结合类型标注和调用方确认。
        dim: dim，具体约束请结合类型标注和调用方确认。

    返回:
        Tuple[np.ndarray, str, str]

    阅读提示:
        主要直接调用：HashTextEmbedder, embedder.encode_texts, list, vectors.astype。
    """
    embedder = HashTextEmbedder(embedding_dim=dim)
    vectors = embedder.encode_texts(list(texts))
    embedding_model = f"hash_embedding_dim_{dim}_for_smoke_test"
    embedding_version = "hash_embedding_v1.0"
    return vectors.astype("float32"), embedding_model, embedding_version


# 阅读注释（函数）：处理 encode 查询 with hash 相关逻辑。
def encode_query_with_hash(query: str, dim: int) -> np.ndarray:
    """处理 encode 查询 with hash 相关逻辑。

    参数:
        query: 当前检索或生成查询。
        dim: dim，具体约束请结合类型标注和调用方确认。

    返回:
        np.ndarray

    阅读提示:
        主要直接调用：HashTextEmbedder, astype, reshape, embedder.encode_query。
    """
    embedder = HashTextEmbedder(embedding_dim=dim)
    return embedder.encode_query(query).reshape(-1).astype("float32")


# 阅读注释（函数）：处理 encode texts with 模型 相关逻辑。
def encode_texts_with_model(
    texts: Sequence[str],
    model_name: Optional[str],
    device: str,
    batch_size: int,
    embedding_version: str = DEFAULT_EMBEDDING_VERSION,
) -> Tuple[np.ndarray, str, str]:
    """处理 encode texts with 模型 相关逻辑。

    参数:
        texts: texts，具体约束请结合类型标注和调用方确认。
        model_name: 模型 名称，具体约束请结合类型标注和调用方确认。
        device: device，具体约束请结合类型标注和调用方确认。
        batch_size: batch size，具体约束请结合类型标注和调用方确认。
        embedding_version: embedding 版本，具体约束请结合类型标注和调用方确认。

    返回:
        Tuple[np.ndarray, str, str]

    阅读提示:
        主要直接调用：resolve_default_embedding_model, ValueError, TextEmbedder, str, embedder.encode_texts, list, vectors.astype。
    """
    if not model_name:
        model_name = resolve_default_embedding_model()
    if not model_name:
        raise ValueError(
            "No embedding model resolved. Pass --embedding-model <local_model_path_or_name> "
            "or use --hash-embedding for smoke test."
        )

    from rag.embed.embedder import TextEmbedder

    embedder = TextEmbedder(
        model_name=str(model_name),
        device=device,
        batch_size=batch_size,
    )
    vectors = embedder.encode_texts(list(texts))
    return vectors.astype("float32"), str(model_name), embedding_version


# 阅读注释（函数）：处理 encode 查询 with 模型 相关逻辑。
def encode_query_with_model(
    query: str,
    model_name: str,
    device: str,
    batch_size: int = 1,
) -> np.ndarray:
    """处理 encode 查询 with 模型 相关逻辑。

    参数:
        query: 当前检索或生成查询。
        model_name: 模型 名称，具体约束请结合类型标注和调用方确认。
        device: device，具体约束请结合类型标注和调用方确认。
        batch_size: batch size，具体约束请结合类型标注和调用方确认。

    返回:
        np.ndarray

    阅读提示:
        主要直接调用：TextEmbedder, str, astype, reshape, embedder.encode_query。
    """
    from rag.embed.embedder import TextEmbedder

    embedder = TextEmbedder(
        model_name=str(model_name),
        device=device,
        batch_size=batch_size,
    )
    return embedder.encode_query(query).reshape(-1).astype("float32")
