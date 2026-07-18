"""Invoke registered model clients and normalize failures."""

from __future__ import annotations

import traceback

from core.error_factory import ErrorFactory
from model_gateway.model_registry import ModelRegistry
from schemas.model import ModelRequestSchema, ModelResponseSchema


class ModelInvoker:
    def __init__(
        self,
        registry: ModelRegistry,
        error_factory: ErrorFactory | None = None,
    ) -> None:
        self.registry = registry
        self.error_factory = error_factory or ErrorFactory()

    def invoke(
        self,
        request: ModelRequestSchema,
        model_name: str,
    ) -> ModelResponseSchema:
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
