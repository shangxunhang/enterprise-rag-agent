# =============================================================================
# 中文阅读说明：RAG 核心模块，负责查询变换、召回、融合、重排、证据评估和上下文组装。
# 主要定义：TxtReader。建议先从公开入口函数开始，再沿调用关系向下阅读。
# =============================================================================
"""
src/txt_reader.py
=============

文档读取模块。

第一版只支持 TXT 文件。

职责：
1. 扫描 raw 数据目录
2. 读取所有 .txt 文件
3. 转成统一 documents 结构

注意：
reader 只负责“读”，不负责清洗、不负责切分。
"""

from pathlib import Path
from typing import List, Dict

from rag.reader.base_reader import BaseReader
from rag.schema.document_schema import build_document


# 阅读注释（类）：封装 txt reader，集中封装相关状态、依赖和行为。
class TxtReader(BaseReader):
    """封装 txt reader，集中封装相关状态、依赖和行为。"""
    # 阅读注释（函数）：读取 txt 文件。
    def read_txt_file(file_path: Path) -> str:
        """
        读取单个 txt 文件。

        Args:
            file_path: txt 文件路径

        Returns:
            文件中的原始文本
        """
        with open(file_path, "r", encoding="utf-8") as f:
            return f.read()

    # 阅读注释（函数）：读取 TxtReader。
    def read(self, path: Path) -> List[Dict]:
        """
        读取单个 txt 文件，并返回 Document Schema 列表。
        """
        with open(path, "r", encoding="utf-8") as f:
            text = f.read()

        if not text or not text.strip():
            print(f"[TxtReader] 跳过空文件: {path}")
            return []

        doc = build_document(
            file_path=path,
            text=text,
        )

        return [doc]
