"""构建、保存和加载旧版 FAISS 向量索引及 Chunk metadata。

FAISS 文件只保存向量和索引结构，Chunk 原文、来源和身份必须单独写入 metadata JSON。
调用方必须保证 Embedding 行顺序与 Chunk metadata 顺序完全一致，否则检索命中的向量
位置会映射到错误文档。
"""

import json
from pathlib import Path
from typing import Dict, List

import faiss
import numpy as np


def build_faiss_index(embeddings: np.ndarray) -> faiss.Index:
    """使用归一化 Embedding 构建内积索引。

    Args:
        embeddings: 形状为 ``(num_chunks, embedding_dim)`` 的二维矩阵。

    Returns:
        已写入全部向量的 ``faiss.IndexFlatIP``。

    Raises:
        ValueError: 输入不是二维矩阵或没有任何向量时抛出。

    上游 TextEmbedder 默认执行 L2 归一化，因此内积等价于余弦相似度；如果调用方
    提供未归一化向量，分数将同时受向量模长影响。
    """

    # 第一步：索引构建要求明确的样本轴和向量维度轴。
    if embeddings.ndim != 2:
        raise ValueError(
            "embeddings 必须是二维矩阵，"
            "shape = (num_chunks, embedding_dim)"
        )

    # 第二步：读取行数和维度，并拒绝空索引。
    num_chunks, embedding_dim = embeddings.shape
    if num_chunks == 0:
        raise ValueError("embeddings 为空，无法构建 FAISS index")

    # 第三步：创建不训练、精确搜索的 Flat Inner Product 索引。
    index = faiss.IndexFlatIP(embedding_dim)

    # 第四步：统一为 FAISS 支持的 float32 后按输入顺序写入。
    index.add(embeddings.astype("float32"))
    return index


def save_faiss_index(index: faiss.Index, file_path: Path) -> None:
    """创建父目录并把 FAISS 索引写入二进制文件。"""

    file_path.parent.mkdir(parents=True, exist_ok=True)
    faiss.write_index(index, str(file_path))


def save_chunk_meta(chunks: List[Dict], file_path: Path) -> None:
    """把与 FAISS 行顺序对应的 Chunk metadata 保存为 JSON。

    Args:
        chunks: 与索引向量一一对应、顺序一致的 Chunk 字典列表。
        file_path: 目标 JSON 路径。
    """

    # 第一步：确保输出目录存在。
    file_path.parent.mkdir(parents=True, exist_ok=True)

    # 第二步：完整保存 Chunk 列表，保留中文并使用缩进便于核查映射关系。
    with open(file_path, "w", encoding="utf-8") as handle:
        json.dump(
            chunks,
            handle,
            ensure_ascii=False,
            indent=2,
        )


def load_chunks(file_path: Path) -> List[Dict]:
    """读取旧版 ``chunks_old.json`` 或兼容 Chunk 列表文件。

    文件存在性和顶层类型没有在此额外校验，I/O 或 JSON 结构问题由标准库直接抛出。
    """

    with open(file_path, "r", encoding="utf-8") as handle:
        return json.load(handle)


def load_faiss_index(file_path: Path) -> faiss.Index:
    """从磁盘加载 FAISS 索引。

    Raises:
        FileNotFoundError: 目标文件不存在时抛出。
    """

    # 显式检查路径，提供比底层 FAISS 更清晰的中文错误信息。
    if not file_path.exists():
        raise FileNotFoundError(
            f"FAISS index 文件不存在: {file_path}"
        )

    return faiss.read_index(str(file_path))


def load_chunk_meta(file_path: Path) -> List[Dict]:
    """加载与 FAISS 行顺序对应的 Chunk metadata 列表。

    Raises:
        FileNotFoundError: metadata 文件不存在时抛出。
    """

    if not file_path.exists():
        raise FileNotFoundError(
            f"chunk_meta 文件不存在: {file_path}"
        )

    with open(file_path, "r", encoding="utf-8") as handle:
        return json.load(handle)
