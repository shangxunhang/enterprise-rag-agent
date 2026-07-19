# -*- coding: utf-8 -*-
# =============================================================================
# 中文阅读说明：RAG 核心模块，负责查询变换、召回、融合、重排、证据评估和上下文组装。
# 主要定义：normalize_text、as_list、safe_str、safe_int、safe_float、unique_keep_order、split_text_by_fixed_size、split_long_text。建议先从公开入口函数开始，再沿调用关系向下阅读。
# =============================================================================
"""
rag_template/util/text_utils.py
===============================

通用文本与基础类型工具。

职责：
1. 提供不依赖 Spark、不依赖 schema 的纯工具函数。
2. 给普通 chunker、cleaned_text_unit_chunker、后续 parent-child chunker 复用。
3. 不负责构造 chunk schema；schema 构造统一放在 rag_template.schema.Chunk_Schema。
"""

from __future__ import annotations

import re
from typing import Any, Iterable, List, Optional


# 阅读注释（函数）：规范化 文本。
def normalize_text(text: Any) -> str:
    """统一做轻量文本规范化。"""
    if text is None:
        return ""

    text = str(text)
    text = text.replace("\u3000", " ")
    text = re.sub(r"[\t\r\f\v]+", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    text = re.sub(r"[ ]{2,}", " ", text)
    return text.strip()


# 阅读注释（函数）：处理 as 列表 相关逻辑。
def as_list(value: Any) -> List[Any]:
    """把 None / 标量 / tuple 统一转成 list。"""
    if value is None:
        return []
    if isinstance(value, list):
        return value
    if isinstance(value, tuple):
        return list(value)
    return [value]


# 阅读注释（函数）：处理 safe str 相关逻辑。
def safe_str(value: Any, default: str = "") -> str:
    """处理 safe str 相关逻辑。

    参数:
        value: value，具体约束请结合类型标注和调用方确认。
        default: default，具体约束请结合类型标注和调用方确认。

    返回:
        str

    阅读提示:
        主要直接调用：isinstance, str。
    """
    if value is None:
        return default
    if isinstance(value, str):
        return value
    return str(value)


# 阅读注释（函数）：处理 safe int 相关逻辑。
def safe_int(value: Any, default: Optional[int] = None) -> Optional[int]:
    """处理 safe int 相关逻辑。

    参数:
        value: value，具体约束请结合类型标注和调用方确认。
        default: default，具体约束请结合类型标注和调用方确认。

    返回:
        Optional[int]

    阅读提示:
        主要直接调用：int。
    """
    try:
        if value is None or value == "":
            return default
        return int(value)
    except Exception:
        return default


# 阅读注释（函数）：处理 safe float 相关逻辑。
def safe_float(value: Any, default: Optional[float] = None) -> Optional[float]:
    """处理 safe float 相关逻辑。

    参数:
        value: value，具体约束请结合类型标注和调用方确认。
        default: default，具体约束请结合类型标注和调用方确认。

    返回:
        Optional[float]

    阅读提示:
        主要直接调用：float。
    """
    try:
        if value is None or value == "":
            return default
        return float(value)
    except Exception:
        return default


# 阅读注释（函数）：处理 unique keep order 相关逻辑。
def unique_keep_order(values: Iterable[Any]) -> List[Any]:
    """按原顺序去重，并跳过 None。"""
    seen = set()
    out: List[Any] = []
    for v in values:
        if v is None:
            continue
        if v in seen:
            continue
        seen.add(v)
        out.append(v)
    return out


# 阅读注释（函数）：处理 split 文本 by fixed size 相关逻辑。
def split_text_by_fixed_size(text: str, chunk_size: int, chunk_overlap: int) -> List[str]:
    """
    按固定字符窗口切分文本。

    这是底层文本切分工具，不负责生成 chunk schema。
    """
    text = normalize_text(text)
    if not text:
        return []

    if chunk_size <= 0:
        raise ValueError("chunk_size must be positive")
    if chunk_overlap < 0:
        raise ValueError("chunk_overlap must be non-negative")
    if chunk_overlap >= chunk_size:
        raise ValueError("chunk_overlap must be smaller than chunk_size")

    chunks: List[str] = []
    start = 0
    step = chunk_size - chunk_overlap
    text_length = len(text)

    while start < text_length:
        end = min(start + chunk_size, text_length)
        chunk = text[start:end].strip()
        if chunk:
            chunks.append(chunk)
        if end >= text_length:
            break
        start += step

    return chunks


# 阅读注释（函数）：处理 split long 文本 相关逻辑。
def split_long_text(text: str, chunk_size: int, overlap: int) -> List[str]:
    """
    对超长 cleaned text unit 做兜底切分。

    语义上和 split_text_by_fixed_size 一致，只是保留这个命名，方便 chunker 代码表达：
    这是“长 unit 兜底拆分”，不是普通文档 chunk 策略。
    """
    return split_text_by_fixed_size(text=text, chunk_size=chunk_size, chunk_overlap=overlap)
