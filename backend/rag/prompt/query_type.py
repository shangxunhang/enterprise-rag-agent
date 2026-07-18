"""
query_type.py
=============

RAG 问题类型识别模块。

本文件负责根据用户 query 判断问题类型，
用于 Prompt Router 选择不同的 prompt 模板。

当前第一版采用规则匹配。
后续可以升级为：
1. LLM 分类
2. 小模型分类
3. 规则 + LLM 混合分类
"""

from enum import Enum


class QueryType(str, Enum):
    """
    RAG 问题类型枚举。
    """

    DEFINITION = "definition"      # 是什么 / 定义 / 概念
    REASON = "reason"              # 为什么 / 原因
    PROCESS = "process"            # 流程 / 步骤 / 怎么做
    COMPARISON = "comparison"      # 对比 / 区别 / 分别适合
    LIST = "list"                  # 有哪些 / 包括哪些
    DEFAULT = "default"            # 默认问答
    RUN = "rule"


def detect_query_type(query: str) -> QueryType:
    """
    根据 query 判断问题类型。

    参数:
        query:
            用户问题。

    返回:
        QueryType:
            问题类型。
    """
    if not query:
        return QueryType.DEFAULT

    query = query.strip()

    if any(word in query for word in ["是什么", "什么是", "定义", "概念"]):
        return QueryType.DEFINITION

    if any(word in query for word in ["为什么", "为何", "原因", "怎么会"]):
        return QueryType.REASON

    if any(word in query for word in ["流程", "步骤", "怎么做", "如何", "怎么办"]):
        return QueryType.PROCESS

    if any(word in query for word in ["区别", "对比", "比较", "分别", "适合什么场景"]):
        return QueryType.COMPARISON

    if any(word in query for word in ["有哪些", "包括哪些", "包含哪些", "有什么"]):
        return QueryType.LIST
    if any(word in query for word in ["不能", "禁止", "必须", "不得", "权限", "合规", "安全"]):
        return QueryType.RULE

    return QueryType.DEFAULT