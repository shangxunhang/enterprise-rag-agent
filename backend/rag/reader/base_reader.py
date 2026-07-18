"""
src/rag_template/reader/base_reader.py
=====================================

Reader 抽象基类。

所有具体 Reader 都必须实现 read(path) 方法，
并统一返回 Document Schema 列表。
"""

from abc import ABC, abstractmethod
from pathlib import Path
from typing import List, Dict


class BaseReader(ABC):
    """
    Reader 抽象基类。

    不同文件格式的 Reader 继承这个类：
    - TxtReader
    - JsonReader
    - JsonlReader
    - PdfReader 后续再加
    """

    @abstractmethod
    def read(self, path: Path) -> List[Dict]:
        """
        读取一个文件，并返回标准 Document Schema 列表。

        Args:
            path: 文件路径

        Returns:
            documents: 标准 document 列表
        """
        pass