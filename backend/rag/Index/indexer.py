"""
src/indexer.py
==============

FAISS 索引构建模块。

职责：
1. 根据 embedding 矩阵创建 FAISS index
2. 保存 FAISS index
3. 保存 chunk metadata

注意：
FAISS 主要保存向量。
chunk 的原文、来源、chunk_id 等信息需要单独保存到 chunk_meta.json。
"""

import json
from pathlib import Path
from typing import List, Dict

import faiss
import numpy as np


def build_faiss_index(embeddings: np.ndarray) -> faiss.Index:
    """
    构建 FAISS 向量索引。

    Args:
        embeddings: chunk embedding 矩阵，shape = (num_chunks, embedding_dim)

    Returns:
        faiss index
    """
    if embeddings.ndim != 2:
        raise ValueError("embeddings 必须是二维矩阵，shape = (num_chunks, embedding_dim)")

    num_chunks, embedding_dim = embeddings.shape

    if num_chunks == 0:
        raise ValueError("embeddings 为空，无法构建 FAISS index")

    # 因为 embedding 已经 normalize，所以内积等价于 cosine similarity
    index = faiss.IndexFlatIP(embedding_dim)

    index.add(embeddings.astype("float32"))

    return index


def save_faiss_index(index: faiss.Index, file_path: Path) -> None:
    """
    保存 FAISS index。

    Args:
        index: FAISS index
        file_path: 保存路径
    """
    file_path.parent.mkdir(parents=True, exist_ok=True)
    faiss.write_index(index, str(file_path))


def save_chunk_meta(chunks: List[Dict], file_path: Path) -> None:
    """
    保存 chunk metadata。

    Args:
        chunks: chunk 列表
        file_path: 保存路径
    """
    file_path.parent.mkdir(parents=True, exist_ok=True)

    with open(file_path, "w", encoding="utf-8") as f:
        json.dump(chunks, f, ensure_ascii=False, indent=2)


def load_chunks(file_path: Path) -> List[Dict]:
    """
    读取 chunks_old.json。

    Args:
        file_path: chunks_old.json 路径

    Returns:
        chunk 列表
    """
    with open(file_path, "r", encoding="utf-8") as f:
        chunks = json.load(f)

    return chunks


def load_faiss_index(file_path: Path) -> faiss.Index:
    """
    加载 FAISS index。

    Args:
        file_path: faiss.index 路径

    Returns:
        FAISS index
    """
    if not file_path.exists():
        raise FileNotFoundError(f"FAISS index 文件不存在: {file_path}")

    index = faiss.read_index(str(file_path))

    return index


def load_chunk_meta(file_path: Path) -> List[Dict]:
    """
    加载 chunk metadata。

    Args:
        file_path: chunk_meta.json 路径

    Returns:
        chunk metadata 列表
    """
    if not file_path.exists():
        raise FileNotFoundError(f"chunk_meta 文件不存在: {file_path}")

    with open(file_path, "r", encoding="utf-8") as f:
        chunk_meta = json.load(f)

    return chunk_meta
