"""用确定性规则识别旧版知识库问答的查询类型。

分类结果只用于选择 Prompt 模板，不改变检索策略。当前规则按固定优先级匹配中文
触发词；一个查询同时命中多类时，返回代码中最先检查的类型。复杂意图识别应由
新的路由器或模型分类器承担。
"""

from enum import Enum


class QueryType(str, Enum):
    """旧版 Prompt Router 支持的问题类型。"""

    # 定义、原因、流程、对比和列表分别对应不同回答结构。
    DEFINITION = "definition"
    REASON = "reason"
    PROCESS = "process"
    COMPARISON = "comparison"
    LIST = "list"

    # DEFAULT 用于未命中任何规则的通用问答。
    DEFAULT = "default"

    # 历史成员名为 RUN，但值对应制度/规则类模板；保留名称以兼容旧调用方。
    RUN = "rule"


def detect_query_type(query: str) -> QueryType:
    """根据中文触发词返回问题类型。

    Args:
        query: 用户查询文本。

    Returns:
        首个命中的 ``QueryType``；空查询或无匹配规则时返回 ``DEFAULT``。
    """

    # 第一步：空值没有可分类语义，使用通用模板。
    if not query:
        return QueryType.DEFAULT

    # 第二步：去除首尾空白，但不改写大小写或标点。
    query = query.strip()

    # 第三步：按固定优先级检查定义类触发词。
    if any(
        word in query
        for word in ["是什么", "什么是", "定义", "概念"]
    ):
        return QueryType.DEFINITION

    # 第四步：检查原因解释类问题。
    if any(
        word in query
        for word in ["为什么", "为何", "原因", "怎么会"]
    ):
        return QueryType.REASON

    # 第五步：检查流程、步骤和操作类问题。
    if any(
        word in query
        for word in ["流程", "步骤", "怎么做", "如何", "怎么办"]
    ):
        return QueryType.PROCESS

    # 第六步：检查对比、区别和场景选择类问题。
    if any(
        word in query
        for word in ["区别", "对比", "比较", "分别", "适合什么场景"]
    ):
        return QueryType.COMPARISON

    # 第七步：检查枚举和组成项类问题。
    if any(
        word in query
        for word in ["有哪些", "包括哪些", "包含哪些", "有什么"]
    ):
        return QueryType.LIST

    # 第八步：检查制度、权限、安全和强制规则类问题。
    if any(
        word in query
        for word in ["不能", "禁止", "必须", "不得", "权限", "合规", "安全"]
    ):
        return QueryType.RUN

    return QueryType.DEFAULT
