# -*- coding: utf-8 -*-
"""提供不依赖 Schema、Spark 或模型的文本与基础类型工具。

这些纯函数被普通 Chunker、cleaned-text-unit Chunker 和父子块离线构建共同复用。
它们只负责规范化、类型转换、顺序去重和固定窗口切分，不创建 Chunk 身份或 metadata。
"""

from __future__ import annotations

import re
from typing import Any, Iterable, List, Optional


def normalize_text(text: Any) -> str:
    """执行保守的通用文本规范化。

    Args:
        text: 任意可转字符串值。

    Returns:
        全角空格转普通空格、制表/回车类空白压缩、过多空行压缩后的文本；
        ``None`` 返回空字符串。

    本函数不会删除标点或合并正常双换行，避免破坏段落和后续标题识别。
    """

    # 第一步：None 统一为空文本。
    if text is None:
        return ""

    # 第二步：字符串化并统一常见全角、控制空白。
    normalized = str(text)
    normalized = normalized.replace("\u3000", " ")
    normalized = re.sub(r"[\t\r\f\v]+", " ", normalized)

    # 第三步：最多保留一个空段落，并压缩连续普通空格。
    normalized = re.sub(r"\n{3,}", "\n\n", normalized)
    normalized = re.sub(r"[ ]{2,}", " ", normalized)

    return normalized.strip()


def as_list(value: Any) -> List[Any]:
    """把 ``None``、标量或元组统一转换为列表。

    已经是列表时保持原对象引用，元组转换为新列表，其他标量包装为单元素列表。
    """

    if value is None:
        return []
    if isinstance(value, list):
        return value
    if isinstance(value, tuple):
        return list(value)
    return [value]


def safe_str(value: Any, default: str = "") -> str:
    """尽力把值转换为字符串，``None`` 使用默认值。"""

    if value is None:
        return default
    if isinstance(value, str):
        return value
    return str(value)


def safe_int(
    value: Any,
    default: Optional[int] = None,
) -> Optional[int]:
    """尽力把值转换为整数，失败时返回默认值。

    该函数适用于宽松读取外部 metadata；严格配置校验不应使用它静默吞掉错误。
    """

    try:
        if value is None or value == "":
            return default
        return int(value)
    except Exception:
        return default


def safe_float(
    value: Any,
    default: Optional[float] = None,
) -> Optional[float]:
    """尽力把值转换为浮点数，失败时返回默认值。"""

    try:
        if value is None or value == "":
            return default
        return float(value)
    except Exception:
        return default


def unique_keep_order(values: Iterable[Any]) -> List[Any]:
    """跳过 ``None`` 并按首次出现顺序去重。

    Args:
        values: 元素必须可哈希，因为内部使用集合记录已出现值。

    Returns:
        保留首次出现元素的新列表。
    """

    seen = set()
    output: List[Any] = []

    # 单次遍历同时完成过滤和顺序去重。
    for value in values:
        if value is None or value in seen:
            continue
        seen.add(value)
        output.append(value)

    return output


def split_text_by_fixed_size(
    text: str,
    chunk_size: int,
    chunk_overlap: int,
) -> List[str]:
    """按字符固定窗口切分规范化文本。

    Args:
        text: 待切分文本。
        chunk_size: 每个窗口最大字符数，必须为正。
        chunk_overlap: 相邻窗口重叠字符数，必须位于 ``[0, chunk_size)``。

    Returns:
        去首尾空白后的非空文本块列表。

    Raises:
        ValueError: 大小或重叠参数不满足窗口推进约束时抛出。

    本函数不生成 Chunk ID，也不记录字符偏移；它只是底层文本切分原语。
    """

    # 第一步：规范化文本；空文本没有可切分内容。
    normalized = normalize_text(text)
    if not normalized:
        return []

    # 第二步：校验窗口参数，保证 step 始终为正。
    if chunk_size <= 0:
        raise ValueError("chunk_size must be positive")
    if chunk_overlap < 0:
        raise ValueError("chunk_overlap must be non-negative")
    if chunk_overlap >= chunk_size:
        raise ValueError(
            "chunk_overlap must be smaller than chunk_size"
        )

    chunks: List[str] = []
    start = 0
    step = chunk_size - chunk_overlap
    text_length = len(normalized)

    # 第三步：按固定步长推进窗口，末块自然截断到文本末尾。
    while start < text_length:
        end = min(start + chunk_size, text_length)
        chunk = normalized[start:end].strip()
        if chunk:
            chunks.append(chunk)

        # 已覆盖末尾时立即退出，避免多生成一个重叠空块。
        if end >= text_length:
            break
        start += step

    return chunks


def split_long_text(
    text: str,
    chunk_size: int,
    overlap: int,
) -> List[str]:
    """对超长 cleaned-text unit 执行固定窗口兜底切分。

    该命名表达调用语义：只有单个已清洗语义单元过长时才使用。底层算法与
    ``split_text_by_fixed_size`` 完全一致。
    """

    return split_text_by_fixed_size(
        text=text,
        chunk_size=chunk_size,
        chunk_overlap=overlap,
    )
