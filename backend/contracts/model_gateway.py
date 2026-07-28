"""定义应用层调用模型网关时依赖的端口协议。

该协议把业务代码与具体模型注册表、供应商客户端、重试策略和用量统计实现隔离。
应用层只提交统一请求并接收统一响应，不直接选择或初始化底层模型客户端。
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from schemas.model import ModelRequestSchema, ModelResponseSchema


@runtime_checkable
class ModelGatewayPort(Protocol):
    """声明模型网关必须提供的同步生成能力。

    使用 ``Protocol`` 而不是继承式抽象基类，使现有实现只要满足方法签名即可被
    注入；``runtime_checkable`` 则允许装配阶段进行运行时结构检查。
    """

    def generate(self, request: ModelRequestSchema) -> ModelResponseSchema:
        """通过已配置的模型路由和运行时边界执行一次生成。

        Args:
            request: 与供应商无关的标准模型请求。

        Returns:
            已归一化的模型响应；供应商原始响应不应穿透该端口。
        """

        ...
