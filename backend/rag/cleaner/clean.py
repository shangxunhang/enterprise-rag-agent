"""
src/cleaner.py
==============

文本清洗模块。

职责：
1. 清洗单篇文档文本
2. 批量清洗 documents

第一版只做基础规则清洗：
- 去掉首尾空白
- 统一换行符
- 合并多余空格
- 合并多余空行
"""

import re
from typing import List, Dict


def clean_text(text: str) -> str:
    """
    清洗单段文本。

    Args:
        text: 原始文本

    Returns:
        清洗后的文本
    """
    if text is None:
        return ""

    # 统一 Windows / Linux 换行
    text = text.replace("\r\n", "\n").replace("\r", "\n")

    # 去除每一行首尾空白
    lines = [line.strip() for line in text.split("\n")]

    # 重新拼接
    text = "\n".join(lines)

    # 多个空格合并成一个空格
    text = re.sub(r"[ \t]+", " ", text)

    # 多个空行合并成两个换行
    text = re.sub(r"\n{3,}", "\n\n", text)

    # 去掉整体首尾空白
    text = text.strip()

    return text


def clean_documents(documents: List[Dict]) -> List[Dict]:
    """
    批量清洗 documents。

    Args:
        documents: reader 输出的 documents 列表

    Returns:
        清洗后的 documents
    """
    cleaned_documents = []

    for doc in documents:
        cleaned_doc = {
            **doc,
            "text": clean_text(doc.get("text", "")),
        }

        cleaned_documents.append(cleaned_doc)

    return cleaned_documents