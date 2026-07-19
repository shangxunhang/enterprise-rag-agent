# =============================================================================
# 中文阅读说明：RAG 核心模块，负责查询变换、召回、融合、重排、证据评估和上下文组装。
# 主要定义：QueryTransformerPort。建议先从公开入口函数开始，再沿调用关系向下阅读。
# =============================================================================
"""Stable contract for configuration-driven query transformers."""

from __future__ import annotations

from typing import Protocol, TYPE_CHECKING, runtime_checkable

if TYPE_CHECKING:
    from rag.query.query_expander import QueryExpansionResult


# 阅读注释（类）：封装 查询 transformer port，定义模块间调用契约，具体实现由适配器或插件提供。
@runtime_checkable
class QueryTransformerPort(Protocol):
    """Transform a query-expansion state without knowing pipeline internals."""

    # 阅读注释（函数）：转换 QueryTransformerPort。
    def transform(
        self,
        state: "QueryExpansionResult",
    ) -> "QueryExpansionResult":
        """转换 QueryTransformerPort。

        参数:
            state: 工作流共享状态。

        返回:
            'QueryExpansionResult'
        """
        ...
