"""定义业务模块访问证据检索能力时依赖的公共端口。

该边界只负责检索、筛选并组织可引用证据，不负责根据证据生成最终业务文本。
检索与生成分离后，证据质量、引用覆盖和生成质量可以分别评测和替换。
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from schemas.rag import EvidenceBundleSchema, RAGToolInputSchema


@runtime_checkable
class RAGServicePort(Protocol):
    """声明标准证据检索服务必须提供的能力。

    实现可以使用向量检索、关键词检索、混合检索和重排，但必须返回统一的证据包，
    不能在该端口内直接生成业务答案。
    """

    def retrieve(self, request: RAGToolInputSchema) -> EvidenceBundleSchema:
        """根据标准检索请求返回可追溯的规范化证据集合。

        Args:
            request: 包含查询、过滤条件、召回参数和业务上下文的检索输入。

        Returns:
            统一证据包，供生成、引用绑定、质量判定和审计环节复用。
        """

        ...
