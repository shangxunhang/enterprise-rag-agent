"""调用已注册模型客户端，并把供应商异常统一为结构化模型响应。"""

from __future__ import annotations

import traceback

from core.runtime.execution_control import WorkflowExecutionCancelled
from core.error_factory import ErrorFactory
from core.retry_policy import RetryDecisionPolicy, classify_failure
from model_gateway.model_registry import ModelRegistry
from schemas.model import ModelRequestSchema, ModelResponseSchema


class ModelInvoker:
    """模型调用的最内层边界：查找客户端、执行调用、分类失败和决定是否可重试。"""
    def __init__(
        self,
        registry: ModelRegistry,
        error_factory: ErrorFactory | None = None,
        retry_policy: RetryDecisionPolicy | None = None,
    ) -> None:
        """注入模型注册表、结构化错误工厂和统一重试判定策略。"""
        self.registry = registry
        self.error_factory = error_factory or ErrorFactory()
        self.retry_policy = retry_policy or RetryDecisionPolicy()

    def invoke(
        self,
        request: ModelRequestSchema,
        model_name: str,
    ) -> ModelResponseSchema:
        """执行一次模型调用，并保证普通异常以失败响应返回。

        工作流取消属于控制流，必须原样向上抛出，不能被误归类为模型不可用并触发回退。
        其他异常由最近事实源分类，再根据失败类别设置 ``recoverable/retryable``，同时保留
        原始异常类型和堆栈，供上层重试、观测和人工排障使用。
        """
        try:
            return self.registry.get(model_name).generate(request)
        except WorkflowExecutionCancelled:
            # Cancellation is control flow, not provider availability.  Let it
            # abort the logical call instead of normalizing it into fallback.
            raise
        except Exception as exc:
            failure_category = classify_failure(exc)
            retryable = self.retry_policy.should_retry(failure_category)
            error_type = str(
                getattr(exc, "original_error_type", exc.__class__.__name__)
            )
            error = self.error_factory.create(
                error_code="MODEL_GATEWAY_CALL_FAILED",
                error_type=error_type,
                message=str(exc),
                user_visible_message=f"模型 {model_name} 调用失败。",
                recoverable=retryable,
                retryable=retryable,
                failure_category=failure_category,
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
