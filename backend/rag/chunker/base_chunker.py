# src/rag_template/chunker/base_chunker.py
from abc import ABC, abstractmethod
from typing import Dict, List


class BaseChunker(ABC):
    """
    Chunker 抽象基类。

    所有切分策略都统一输入 Document Schema，输出 Chunk Schema。
    对 RecursiveChunker / HeadingChunker 来说，chunk_size 与 chunk_overlap 按 token 数解释。
    """

    def __init__(self, chunk_size: int = 500, chunk_overlap: int = 50):
        if chunk_size <= 0:
            raise ValueError("chunk_size 必须大于 0")
        if chunk_overlap < 0:
            raise ValueError("chunk_overlap 不能小于 0")
        if chunk_overlap >= chunk_size:
            raise ValueError("chunk_overlap 必须小于 chunk_size")

        self.chunk_size = chunk_size
        self.chunk_overlap = chunk_overlap

    def chunk_documents(self, documents: List[Dict]) -> List[Dict]:
        all_chunks: List[Dict] = []
        for document in documents:
            all_chunks.extend(self.chunk_document(document))
        return all_chunks

    @abstractmethod
    def chunk_document(self, document: Dict) -> List[Dict]:
        pass
