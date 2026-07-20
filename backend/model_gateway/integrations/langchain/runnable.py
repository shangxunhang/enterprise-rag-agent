"""Expose the existing ModelGateway as a LangChain Runnable."""

from __future__ import annotations

from typing import Any

from langchain_core.runnables import Runnable, RunnableLambda

from model_gateway.model_gateway import ModelGateway
from schemas.model import ModelRequestSchema, ModelResponseSchema


def _coerce_request(value: Any) -> ModelRequestSchema:
    if isinstance(value, ModelRequestSchema):
        return value
    if isinstance(value, dict):
        return ModelRequestSchema.model_validate(value)
    raise TypeError(
        "ModelGateway runnable expects ModelRequestSchema or dict, "
        f"got {type(value).__name__}"
    )


def build_model_gateway_runnable(
    model_gateway: ModelGateway,
    *,
    run_name: str = "enterprise_model_gateway",
) -> Runnable[Any, ModelResponseSchema]:
    """Build a LangChain Runnable without bypassing routing/trace/error logic."""

    def invoke(value: Any) -> ModelResponseSchema:
        return model_gateway.generate(_coerce_request(value))

    return RunnableLambda(invoke).with_config(
        {
            "run_name": run_name,
            "tags": ["enterprise-rag-agent", "langchain", "model-gateway"],
        }
    )
