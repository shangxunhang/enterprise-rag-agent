"""定义底层大语言模型客户端的抽象基类。

该抽象面向具体模型供应商适配器，例如本地模型、OpenAI 兼容接口或其他远程
推理服务。上层业务不应依赖供应商 SDK，而应通过标准请求和响应模型调用实现类。
"""

from __future__ import annotations

from abc import ABC, abstractmethod

from schemas.model import ModelRequestSchema, ModelResponseSchema


class BaseLLMClient(ABC):
    """约束所有底层 LLM 客户端必须提供的最小能力。

    Attributes:
        model_name: 当前客户端实际调用的模型名称，用于日志、路由和用量归因。

    实现类负责把统一请求转换成供应商协议、执行调用并重新封装为统一响应；它们
    不应把供应商特有对象泄漏到上层。
    """

    model_name: str

    @abstractmethod
    def generate(self, request: ModelRequestSchema) -> ModelResponseSchema:
        """根据标准模型请求执行一次同步文本生成。

        Args:
            request: 已完成参数校验的统一模型请求，包含提示词、生成参数和调用
                上下文。

        Returns:
            统一模型响应，供网关、Agent 和数据采集模块继续处理。

        实现类应在该边界内完成协议转换和错误归一化；基类不提供默认行为。
        """

        raise NotImplementedError
