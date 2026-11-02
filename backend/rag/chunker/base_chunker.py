"""旧 Document→Chunk 策略的统一抽象基类。

该抽象服务平面 Fixed/Recursive/Heading 等历史 Chunker；父子分块和 cleaned_text_unit
离线管线使用独立协议。``chunk_size`` 与 ``chunk_overlap`` 的单位由具体策略说明，基类
只保证数值约束和批量分发。
"""
from abc import ABC, abstractmethod
from typing import Dict, List


class BaseChunker(ABC):
    """
    Chunker 抽象基类。

    所有切分策略都统一输入 Document Schema，输出 Chunk Schema。
    对 RecursiveChunker / HeadingChunker 来说，chunk_size 与 chunk_overlap 按 token 数解释。
    """

    def __init__(self, chunk_size: int = 500, chunk_overlap: int = 50):
        """校验窗口与重叠：窗口必须为正，重叠必须非负且严格小于窗口。"""
        if chunk_size <= 0:
            raise ValueError("chunk_size 必须大于 0")
        if chunk_overlap < 0:
            raise ValueError("chunk_overlap 不能小于 0")
        if chunk_overlap >= chunk_size:
            raise ValueError("chunk_overlap 必须小于 chunk_size")

        self.chunk_size = chunk_size
        self.chunk_overlap = chunk_overlap

    def chunk_documents(self, documents: List[Dict]) -> List[Dict]:
        """按输入顺序切分每个 Document，并平铺所有 Chunk 结果。"""
        all_chunks: List[Dict] = []
        for document in documents:
            all_chunks.extend(self.chunk_document(document))
        return all_chunks

    @abstractmethod
    def chunk_document(self, document: Dict) -> List[Dict]:
        """把一个标准 Document 映射为当前策略的 Chunk 列表。"""
        pass
