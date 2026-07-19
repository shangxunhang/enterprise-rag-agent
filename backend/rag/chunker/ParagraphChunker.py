# =============================================================================
# 中文阅读说明：RAG 核心模块，负责查询变换、召回、融合、重排、证据评估和上下文组装。
# 主要定义：ParagraphChunker。建议先从公开入口函数开始，再沿调用关系向下阅读。
# =============================================================================
# chunker/paragraph_chunker.py

import re
from typing import List, Dict

from rag.chunker.base_chunker import BaseChunker
from rag.schema.Chunk_Schema import build_chunk


# 阅读注释（类）：封装 paragraph chunker，集中封装相关状态、依赖和行为。
class ParagraphChunker(BaseChunker):
    """
    段落切分。
    适合政策、报告、说明文档、普通长文档。
    """

    # 阅读注释（函数）：处理 文本块 文档 相关逻辑。
    def chunk_document(self, document: Dict) -> List[Dict]:
        """处理 文本块 文档 相关逻辑。

        参数:
            document: 文档，具体约束请结合类型标注和调用方确认。

        返回:
            List[Dict]

        阅读提示:
            主要直接调用：self._split_paragraphs, self._merge_short_paragraphs, enumerate, build_chunk, chunks.append。
        """
        text = document["text"]

        paragraphs = self._split_paragraphs(text)
        merged_paragraphs = self._merge_short_paragraphs(paragraphs)

        chunks = []

        for idx, para in enumerate(merged_paragraphs):
            chunk = build_chunk(
                doc=document,
                chunk_text=para,
                chunk_index=idx,
                chunk_type="paragraph",
            )
            chunks.append(chunk)

        return chunks

    # 阅读注释（函数）：处理 split paragraphs 相关逻辑。
    def _split_paragraphs(self, text: str) -> List[str]:
        """处理 split paragraphs 相关逻辑。

        参数:
            text: 待处理文本。

        返回:
            List[str]

        阅读提示:
            主要直接调用：re.split, p.strip。
        """
        paragraphs = re.split(r"\n\s*\n", text)
        return [p.strip() for p in paragraphs if p.strip()]

    # 阅读注释（函数）：合并 short paragraphs。
    def _merge_short_paragraphs(self, paragraphs: List[str]) -> List[str]:
        """合并 short paragraphs。

        参数:
            paragraphs: paragraphs，具体约束请结合类型标注和调用方确认。

        返回:
            List[str]

        阅读提示:
            主要直接调用：len, merged.append。
        """
        merged = []
        buffer = ""

        for para in paragraphs:
            if len(buffer) + len(para) <= self.chunk_size:
                buffer = buffer + "\n\n" + para if buffer else para
            else:
                if buffer:
                    merged.append(buffer)
                buffer = para

        if buffer:
            merged.append(buffer)

        return merged
