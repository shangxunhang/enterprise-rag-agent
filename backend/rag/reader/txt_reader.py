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
from rag.legacy.schema.document_schema import build_document


class TxtReader(BaseReader):
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