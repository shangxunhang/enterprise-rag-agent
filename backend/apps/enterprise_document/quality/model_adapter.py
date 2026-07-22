"""Application hooks for the canonical ModelGateway call boundary."""

from __future__ import annotations

from typing import Any

from apps.enterprise_document.quality.budget import current_workflow_budget
from model_gateway.call_boundary import ModelCallBoundary, ModelGatewayTextGenerator, infer_model_role
from schemas.model import ModelRequestSchema, ModelResponseSchema


def reserve_current_workflow_budget(request: ModelRequestSchema) -> None:
    """Reserve the active workflow safety budget for one real LLM call."""
    budget = current_workflow_budget()
    if budget is not None:
        budget.reserve_llm_call(max_tokens=request.max_tokens)


def _response_hook(runtime_context: dict[str, Any]):
    def record(
        request: ModelRequestSchema,
        response: ModelResponseSchema,
    ) -> None:
        shared_state = runtime_context.get("shared_state")
        if shared_state is not None and hasattr(shared_state, "generated_outputs"):
            shared_state.generated_outputs[request.model_call_id] = response.model_dump()

    return record


def resolve_quality_generator(
    *,
    build_context: dict[str, Any],
    runtime_context: dict[str, Any] | None,
    purpose: str,
    call_suffix: str,
) -> Any | None:
    model_gateway = build_context.get("model_gateway")
    if model_gateway is not None:
        context = dict(runtime_context or {})
        # ``model_name`` is no longer a production routing decision.  It is used
        # only when a caller explicitly marks it as a test/debug override.
        explicit_override = None
        if bool(build_context.get("explicit_model_override")):
            explicit_override = str(build_context.get("model_name") or "").strip() or None
        return ModelCallBoundary(
            model_gateway=model_gateway,
            model_role=infer_model_role(purpose),
            model_name=explicit_override,
            runtime_context=context,
            default_purpose=purpose,
            call_suffix=call_suffix,
            budget_hook=reserve_current_workflow_budget,
            response_hook=_response_hook(context),
        )
    return build_context.get("quality_llm_generator")


__all__ = [
    "ModelCallBoundary",
    "ModelGatewayTextGenerator",
    "reserve_current_workflow_budget",
    "resolve_quality_generator",
]
