"""配置驱动查询变换器的稳定端口。"""

from __future__ import annotations

from typing import Protocol, TYPE_CHECKING, runtime_checkable

if TYPE_CHECKING:
    from rag.query.query_expander import QueryExpansionResult


@runtime_checkable
class QueryTransformerPort(Protocol):
    """在不了解检索 Pipeline 内部结构的情况下变换 QueryExpansionResult。

    变换器应返回完整新状态或兼容副本，保留原查询、改写查询、HyDE 等已有血缘，而不是
    只返回裸字符串列表。
    """
    def transform(
        self,
        state: "QueryExpansionResult",
    ) -> "QueryExpansionResult":
        """应用一次确定性或模型驱动查询变换，并返回更新后的扩展状态。"""
        ...
