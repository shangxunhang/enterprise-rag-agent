# =============================================================================
# 中文阅读说明：模型网关模块，用于屏蔽不同 LLM 提供方和本地模型调用差异。
# 主要定义：ModelInvoker。建议先从公开入口函数开始，再沿调用关系向下阅读。
# =============================================================================
"""Invoke registered model clients and normalize failures."""

from __future__ import annotations

import traceback

from core.error_factory import ErrorFactory
from model_gateway.model_registry import ModelRegistry
from schemas.model import ModelRequestSchema, ModelResponseSchema


# 阅读注释（类）：封装 模型 invoker，集中封装相关状态、依赖和行为。
class ModelInvoker:
    """封装 模型 invoker，集中封装相关状态、依赖和行为。"""
    # 阅读注释（函数）：初始化 ModelInvoker，保存运行所需的依赖、配置或状态。
    def __init__(
        self,
        registry: ModelRegistry,
        error_factory: ErrorFactory | None = None,
    ) -> None:
        """初始化 ModelInvoker，保存运行所需的依赖、配置或状态。

        参数:
            registry: 注册表，具体约束请结合类型标注和调用方确认。
            error_factory: 错误 工厂，具体约束请结合类型标注和调用方确认。

        返回:
            None

        阅读提示:
            主要直接调用：ErrorFactory。
        """
        self.registry = registry
        self.error_factory = error_factory or ErrorFactory()

    # 阅读注释（函数）：处理 invoke 相关逻辑。
    def invoke(
        self,
        request: ModelRequestSchema,
        model_name: str,
    ) -> ModelResponseSchema:
        """处理 invoke 相关逻辑。

        参数:
            request: 当前请求对象。
            model_name: 模型 名称，具体约束请结合类型标注和调用方确认。

        返回:
            ModelResponseSchema

        阅读提示:
            主要直接调用：generate, self.registry.get, self.error_factory.create, str, request.extra.get, traceback.format_exc, ModelResponseSchema。
        """
        try:
            return self.registry.get(model_name).generate(request)
        except Exception as exc:
            error = self.error_factory.create(
                error_code="MODEL_GATEWAY_CALL_FAILED",
                error_type=exc.__class__.__name__,
                message=str(exc),
                user_visible_message=f"模型 {model_name} 调用失败。",
                recoverable=True,
                retryable=True,
                failed_node=request.extra.get("section_id") or request.model_call_id,
                component="ModelGateway",
                agent_name=request.caller_agent,
                step_name=request.extra.get("call_purpose"),
                stack_trace=traceback.format_exc(),
            )
            return ModelResponseSchema(
                model_call_id=request.model_call_id,
                task_id=request.task_id,
                run_id=request.run_id,
                model_name=model_name,
                success=False,
                content="",
                raw_output={},
                error=error,
                error_message=error.message,
                created_at=request.created_at,
                finish_reason="error",
            )
