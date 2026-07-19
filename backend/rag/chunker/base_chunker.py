# =============================================================================
# 中文阅读说明：RAG 核心模块，负责查询变换、召回、融合、重排、证据评估和上下文组装。
# 主要定义：BaseChunker。建议先从公开入口函数开始，再沿调用关系向下阅读。
# =============================================================================
# src/rag_template/chunker/base_chunker.py
from abc import ABC, abstractmethod
from typing import Dict, List


# 阅读注释（类）：封装 base chunker，集中封装相关状态、依赖和行为。
class BaseChunker(ABC):
    """
    Chunker 抽象基类。

    所有切分策略都统一输入 Document Schema，输出 Chunk Schema。
    对 RecursiveChunker / HeadingChunker 来说，chunk_size 与 chunk_overlap 按 token 数解释。
    """

    # 阅读注释（函数）：初始化 BaseChunker，保存运行所需的依赖、配置或状态。
    def __init__(self, chunk_size: int = 500, chunk_overlap: int = 50):
        """初始化 BaseChunker，保存运行所需的依赖、配置或状态。

        参数:
            chunk_size: 文本块 size，具体约束请结合类型标注和调用方确认。
            chunk_overlap: 文本块 overlap，具体约束请结合类型标注和调用方确认。

        返回:
            未显式标注；请结合调用方和实际返回语句理解。

        阅读提示:
            主要直接调用：ValueError。
        """
        if chunk_size <= 0:
            raise ValueError("chunk_size 必须大于 0")
        if chunk_overlap < 0:
            raise ValueError("chunk_overlap 不能小于 0")
        if chunk_overlap >= chunk_size:
            raise ValueError("chunk_overlap 必须小于 chunk_size")

        self.chunk_size = chunk_size
        self.chunk_overlap = chunk_overlap

    # 阅读注释（函数）：处理 文本块 documents 相关逻辑。
    def chunk_documents(self, documents: List[Dict]) -> List[Dict]:
        """处理 文本块 documents 相关逻辑。

        参数:
            documents: documents，具体约束请结合类型标注和调用方确认。

        返回:
            List[Dict]

        阅读提示:
            主要直接调用：all_chunks.extend, self.chunk_document。
        """
        all_chunks: List[Dict] = []
        for document in documents:
            all_chunks.extend(self.chunk_document(document))
        return all_chunks

    # 阅读注释（函数）：处理 文本块 文档 相关逻辑。
    @abstractmethod
    def chunk_document(self, document: Dict) -> List[Dict]:
        """处理 文本块 文档 相关逻辑。

        参数:
            document: 文档，具体约束请结合类型标注和调用方确认。

        返回:
            List[Dict]
        """
        pass
