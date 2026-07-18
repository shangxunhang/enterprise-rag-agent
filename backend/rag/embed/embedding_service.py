# -*- coding: utf-8 -*-
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


def resolve_default_embedding_model() -> Optional[str]:
    """Try to reuse rag_template.configs.RAGConfig.EMBEDDING_MODEL_NAME."""
    try:
        from rag.configs.RAGConfig import EMBEDDING_MODEL_NAME  # type: ignore
        if EMBEDDING_MODEL_NAME:
            return str(EMBEDDING_MODEL_NAME)
    except Exception:
        return None
    return None


def encode_texts_with_hash(texts: Sequence[str], dim: int) -> Tuple[np.ndarray, str, str]:
    embedder = HashTextEmbedder(embedding_dim=dim)
    vectors = embedder.encode_texts(list(texts))
    embedding_model = f"hash_embedding_dim_{dim}_for_smoke_test"
    embedding_version = "hash_embedding_v1.0"
    return vectors.astype("float32"), embedding_model, embedding_version


def encode_query_with_hash(query: str, dim: int) -> np.ndarray:
    embedder = HashTextEmbedder(embedding_dim=dim)
    return embedder.encode_query(query).reshape(-1).astype("float32")


def encode_texts_with_model(
    texts: Sequence[str],
    model_name: Optional[str],
    device: str,
    batch_size: int,
    embedding_version: str = DEFAULT_EMBEDDING_VERSION,
) -> Tuple[np.ndarray, str, str]:
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


def encode_query_with_model(
    query: str,
    model_name: str,
    device: str,
    batch_size: int = 1,
) -> np.ndarray:
    from rag.embed.embedder import TextEmbedder

    embedder = TextEmbedder(
        model_name=str(model_name),
        device=device,
        batch_size=batch_size,
    )
    return embedder.encode_query(query).reshape(-1).astype("float32")
