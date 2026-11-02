"""为 RAG 配置和兼容适配器提供轻量类型转换。"""

from __future__ import annotations

from typing import Any, List


def split_csv(value: Any) -> List[str]:
    """把逗号分隔值或已有序列转换为非空字符串列表。

    Args:
        value: ``None``、列表、元组或任意可转字符串对象。

    Returns:
        去除每项首尾空白后的列表。标量字符串按英文逗号切分；列表和元组按元素
        处理，不进一步拆分元素内部逗号。
    """

    # None 表示未配置，统一返回空列表。
    if value is None:
        return []

    # 已有序列只执行字符串化、去空白和空值过滤。
    if isinstance(value, (list, tuple)):
        return [
            str(item).strip()
            for item in value
            if str(item).strip()
        ]

    # 标量按逗号拆分，兼容环境变量和简单配置文本。
    return [
        item.strip()
        for item in str(value).split(",")
        if item.strip()
    ]


def as_bool(value: Any, default: bool = False) -> bool:
    """把常见配置值转换为布尔值。

    Args:
        value: ``None``、布尔值或任意可转字符串对象。
        default: value 为 ``None`` 时返回的默认值。

    Returns:
        原生 bool 保持不变；字符串 ``1/true/yes/y/on``（忽略大小写和首尾空白）
        视为真，其他非空字符串视为假。
    """

    if value is None:
        return default
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in {
        "1",
        "true",
        "yes",
        "y",
        "on",
    }


def as_int(value: Any, default: int) -> int:
    """把可选配置值转换为整数。

    Args:
        value: 待转换值；``None`` 或空字符串表示未配置。
        default: 未配置时使用的整数默认值。

    Returns:
        默认值或 ``int(value)`` 的结果。

    Raises:
        ValueError: 非空值不能转换为整数时抛出。该异常应暴露配置错误，而不是静默
        回退默认值。
    """

    if value is None or value == "":
        return default
    return int(value)


def as_str_list(value: Any) -> List[str]:
    """把可选标量或序列转换为非空字符串列表。

    与 ``split_csv`` 不同，本函数不会拆分标量中的逗号，适用于一个值本身可能包含
    逗号的字段。
    """

    if value is None:
        return []
    if isinstance(value, (list, tuple)):
        return [
            str(item).strip()
            for item in value
            if str(item).strip()
        ]

    text = str(value).strip()
    return [text] if text else []
