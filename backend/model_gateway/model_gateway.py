# =============================================================================
# 中文阅读说明：模型网关模块，用于屏蔽不同 LLM 提供方和本地模型调用差异。
# 主要定义：ModelGateway。建议先从公开入口函数开始，再沿调用关系向下阅读。
# =============================================================================
"""Facade over model registry, routing, invocation and observability."""

from __future__ import annotations

from typing import Any, Dict, Optional

from contracts.base_client import BaseLLMClient
from contracts.observability import TraceSink
from model_gateway.model_invoker import ModelInvoker
from model_gateway.model_observer import ModelCallObserver
from model_gateway.model_registry import ModelRegistry
from model_gateway.model_router import ModelRouter
from observability.trace_context import activate_span
from schemas.model import ModelRequestSchema, ModelResponseSchema
from schemas.status import ExecutionStatus


# 阅读注释（类）：封装 模型 网关，集中封装相关状态、依赖和行为。
class ModelGateway:
    """封装 模型 网关，集中封装相关状态、依赖和行为。"""
    # 阅读注释（函数）：初始化 ModelGateway，保存运行所需的依赖、配置或状态。
    def __init__(
        self,
        default_model_name: str = "fake_llm",
        run_trace_recorder: Optional[TraceSink] = None,
        *,
        registry: ModelRegistry | None = None,
        router: ModelRouter | None = None,
        invoker: ModelInvoker | None = None,
        observer: ModelCallObserver | None = None,
    ) -> None:
        """初始化 ModelGateway，保存运行所需的依赖、配置或状态。

        参数:
            default_model_name: default 模型 名称，具体约束请结合类型标注和调用方确认。
            run_trace_recorder: run Trace recorder，具体约束请结合类型标注和调用方确认。
            registry: 注册表，具体约束请结合类型标注和调用方确认。
            router: 路由器，具体约束请结合类型标注和调用方确认。
            invoker: invoker，具体约束请结合类型标注和调用方确认。
            observer: observer，具体约束请结合类型标注和调用方确认。

        返回:
            None

        阅读提示:
            主要直接调用：ModelRegistry, ModelRouter, ModelInvoker, ModelCallObserver。
        """
        self.default_model_name = default_model_name
        self.run_trace_recorder = run_trace_recorder
        self.registry = registry or ModelRegistry()
        self.router = router or ModelRouter(default_model_name)
        self.invoker = invoker or ModelInvoker(self.registry)
        self.observer = observer or ModelCallObserver(run_trace_recorder)
        # Compatibility alias for existing introspection/tests.
        self._clients = self.registry._clients

    # 阅读注释（函数）：注册 客户端。
    def register_client(self, client: BaseLLMClient) -> None:
        """注册 客户端。

        参数:
            client: 下游客户端。

        返回:
            None

        阅读提示:
            主要直接调用：self.registry.register。
        """
        self.registry.register(client)

    # 阅读注释（函数）：获取 客户端。
    def get_client(self, model_name: str) -> BaseLLMClient:
        """获取 客户端。

        参数:
            model_name: 模型 名称，具体约束请结合类型标注和调用方确认。

        返回:
            BaseLLMClient

        阅读提示:
            主要直接调用：self.registry.get。
        """
        return self.registry.get(model_name)

    # 阅读注释（函数）：记录 ModelGateway。
    def _record(
        self,
        request: ModelRequestSchema,
        event_type: str,
        status: str,
        payload: Dict[str, Any],
    ) -> None:
        """记录 ModelGateway。

        参数:
            request: 当前请求对象。
            event_type: 事件 类型，具体约束请结合类型标注和调用方确认。
            status: 状态，具体约束请结合类型标注和调用方确认。
            payload: 跨层传递的数据载荷。

        返回:
            None

        阅读提示:
            主要直接调用：self.observer.record。
        """
        self.observer.record(
            request,
            event_type=event_type,
            status=status,
            payload=payload,
            model_name=request.model_name or self.default_model_name,
        )

    # 阅读注释（函数）：生成 ModelGateway。
    def generate(self, request: ModelRequestSchema) -> ModelResponseSchema:
        """生成 ModelGateway。

        参数:
            request: 当前请求对象。

        返回:
            ModelResponseSchema

        阅读提示:
            主要直接调用：self.router.select, self.observer.start, activate_span, self.invoker.invoke, self.observer.finish。
        """
        model_name = self.router.select(request)
        span_handle = self.observer.start(request, model_name=model_name)
        with activate_span(span_handle):
            response = self.invoker.invoke(request, model_name)
        self.observer.finish(
            request,
            response,
            model_name=model_name,
            handle=span_handle,
        )
        return response
