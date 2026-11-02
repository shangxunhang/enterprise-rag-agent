"""对 Reader 输出执行确定性的基础文本清洗。

清洗范围刻意保持保守：统一换行、去除行首尾空白、压缩行内空格和过多空行。
本模块不删除标点、不做语言纠错，也不改变文档元数据，避免清洗阶段破坏后续引用
定位和语义结构。
"""

import re
from typing import Dict, List


def clean_text(text: str) -> str:
    """清洗一段文本并保留段落边界。

    Args:
        text: 原始文本；兼容调用方传入 ``None``。

    Returns:
        统一换行和空白后的文本。空输入返回空字符串。
    """

    # 第一步：None 视为空文本，避免后续字符串方法报错。
    if text is None:
        return ""

    # 第二步：把 Windows 和旧 Mac 换行统一为 LF。
    text = text.replace("\r\n", "\n").replace("\r", "\n")

    # 第三步：逐行去除首尾空白，不合并不同文本行。
    lines = [line.strip() for line in text.split("\n")]
    text = "\n".join(lines)

    # 第四步：把同一行中的连续空格或制表符压缩为一个普通空格。
    text = re.sub(r"[ \t]+", " ", text)

    # 第五步：三个及以上连续换行压缩为一个空段落，即两个换行。
    text = re.sub(r"\n{3,}", "\n\n", text)

    # 第六步：去除整段文本首尾残留空白。
    return text.strip()


def clean_documents(documents: List[Dict]) -> List[Dict]:
    """批量清洗文档列表中的 ``text`` 字段。

    Args:
        documents: Reader 输出的文档字典列表。

    Returns:
        新文档列表。每个字典通过展开复制，除 ``text`` 外的身份和 metadata 保持
        原值；输入字典本身不会被修改。
    """

    cleaned_documents: List[Dict] = []

    # 逐文档创建新字典，防止清洗副作用污染 Reader 缓存或调用方原始数据。
    for document in documents:
        cleaned_document = {
            **document,
            "text": clean_text(document.get("text", "")),
        }
        cleaned_documents.append(cleaned_document)

    return cleaned_documents
