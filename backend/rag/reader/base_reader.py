# =============================================================================
# 中文阅读说明：RAG 核心模块，负责查询变换、召回、融合、重排、证据评估和上下文组装。
# 主要定义：BaseReader。建议先从公开入口函数开始，再沿调用关系向下阅读。
# =============================================================================
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


# 阅读注释（类）：封装 base reader，集中封装相关状态、依赖和行为。
class BaseReader(ABC):
    """
    Reader 抽象基类。

    不同文件格式的 Reader 继承这个类：
    - TxtReader
    - JsonReader
    - JsonlReader
    - PdfReader 后续再加
    """

    # 阅读注释（函数）：读取 BaseReader。
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