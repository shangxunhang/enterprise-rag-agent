"""语义切分策略占位实现。

当前类尚未实现 Embedding 相似度断点或句群聚类，``chunk_document`` 仍返回 ``None``，
因此不能注册到生产 Chunker Factory。保留该文件仅用于未来接口兼容与实现位置说明。
"""
from typing import Dict, List

from rag.chunker.base_chunker import BaseChunker


class SemanticChunker(BaseChunker):
    """未完成的语义 Chunker 协议占位，禁止当作可用策略。"""

    def chunk_document(self, document: Dict) -> List[Dict]:
        """当前未实现；保留原返回行为以避免伪装成有效切分结果。"""
        return None