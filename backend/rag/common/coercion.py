# =============================================================================
# 中文阅读说明：RAG 核心模块，负责查询变换、召回、融合、重排、证据评估和上下文组装。
# 主要定义：split_csv、as_bool、as_int、as_str_list。建议先从公开入口函数开始，再沿调用关系向下阅读。
# =============================================================================
"""Coercion helpers shared by RAG adapters."""

from __future__ import annotations

from typing import Any, List


# 阅读注释（函数）：处理 split csv 相关逻辑。
def split_csv(value: Any) -> List[str]:
    """处理 split csv 相关逻辑。

    参数:
        value: value，具体约束请结合类型标注和调用方确认。

    返回:
        List[str]

    阅读提示:
        主要直接调用：isinstance, strip, str, item.strip, split。
    """
    if value is None:
        return []
    if isinstance(value, (list, tuple)):
        return [str(item).strip() for item in value if str(item).strip()]
    return [item.strip() for item in str(value).split(",") if item.strip()]


# 阅读注释（函数）：处理 as bool 相关逻辑。
def as_bool(value: Any, default: bool = False) -> bool:
    """处理 as bool 相关逻辑。

    参数:
        value: value，具体约束请结合类型标注和调用方确认。
        default: default，具体约束请结合类型标注和调用方确认。

    返回:
        bool

    阅读提示:
        主要直接调用：isinstance, lower, strip, str。
    """
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in {"1", "true", "yes", "y", "on"}


# 阅读注释（函数）：处理 as int 相关逻辑。
def as_int(value: Any, default: int) -> int:
    """处理 as int 相关逻辑。

    参数:
        value: value，具体约束请结合类型标注和调用方确认。
        default: default，具体约束请结合类型标注和调用方确认。

    返回:
        int

    阅读提示:
        主要直接调用：int。
    """
    if value is None or value == "":
        return default
    return int(value)


# 阅读注释（函数）：处理 as str 列表 相关逻辑。
def as_str_list(value: Any) -> List[str]:
    """处理 as str 列表 相关逻辑。

    参数:
        value: value，具体约束请结合类型标注和调用方确认。

    返回:
        List[str]

    阅读提示:
        主要直接调用：isinstance, strip, str。
    """
    if value is None:
        return []
    if isinstance(value, (list, tuple)):
        return [str(item).strip() for item in value if str(item).strip()]
    text = str(value).strip()
    return [text] if text else []
