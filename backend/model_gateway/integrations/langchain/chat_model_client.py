"""Use any LangChain chat-model Runnable behind the existing ModelGateway port."""

from __future__ import annotations

import time
from typing import Any

from langchain_core.messages import BaseMessage
from langchain_core.runnables import Runnable

from contracts.base_client import BaseLLMClient
from model_gateway.integrations.langchain.message_mapping import (
    finish_reason_from_message,
    message_content_to_text,
    request_to_langchain_messages,
    token_usage_from_message,
)
from schemas.model import ModelRequestSchema, ModelResponseSchema


class LangChainChatModelClient(BaseLLMClient):
    """Adapt a LangChain chat model/Runnable to ``BaseLLMClient``.

    The enterprise boundary remains canonical:
        ModelRequestSchema -> ModelResponseSchema

    LangChain is a provider abstraction inside that boundary. Exceptions are
    intentionally allowed to bubble to ModelInvoker, which already normalizes
    them into the project's structured ModelGateway error contract.
    """

    def __init__(
        self,
        *,
        model_name: str,
        chat_model: Runnable[Any, BaseMessage],
        provider_name: str = "langchain",
    ) -> None:
        if not str(model_name).strip():
            raise ValueError("model_name cannot be empty")
        self.model_name = str(model_name).strip()
        self.chat_model = chat_model
        self.provider_name = str(provider_name or "langchain").strip()

    def generate(self, request: ModelRequestSchema) -> ModelResponseSchema:
        messages = request_to_langchain_messages(request)
        generation_kwargs: dict[str, Any] = {
            "temperature": float(request.temperature),
            "max_tokens": int(request.max_tokens),
        }

        config = {
            "run_name": f"model_gateway:{self.model_name}",
            "tags": [
                "enterprise-rag-agent",
                "model-gateway",
                "langchain",
                self.model_name,
            ],
            "metadata": {
                "task_id": request.task_id,
                "run_id": request.run_id,
                "model_call_id": request.model_call_id,
                "caller_agent": request.caller_agent,
                "call_purpose": request.extra.get("call_purpose"),
            },
        }

        started = time.monotonic()
        # Runnable.bind keeps provider-specific generation kwargs outside the
        # stable enterprise request schema while still using LangChain's
        # standard invocation path.
        runnable = self.chat_model.bind(**generation_kwargs)
        message = runnable.invoke(messages, config=config)
        latency_ms = int((time.monotonic() - started) * 1000)

        if not isinstance(message, BaseMessage):
            raise TypeError(
                "LangChain chat model must return BaseMessage, "
                f"got {type(message).__name__}"
            )

        response_metadata = getattr(message, "response_metadata", None)
        additional_kwargs = getattr(message, "additional_kwargs", None)

        return ModelResponseSchema(
            model_call_id=request.model_call_id,
            task_id=request.task_id,
            run_id=request.run_id,
            model_name=self.model_name,
            success=True,
            content=message_content_to_text(message.content),
            raw_output={
                "provider": self.provider_name,
                "langchain_message_type": message.__class__.__name__,
                "message_id": getattr(message, "id", None),
                "response_metadata": (
                    dict(response_metadata)
                    if isinstance(response_metadata, dict)
                    else {}
                ),
                "additional_kwargs": (
                    dict(additional_kwargs)
                    if isinstance(additional_kwargs, dict)
                    else {}
                ),
            },
            latency_ms=latency_ms,
            created_at=request.created_at,
            token_usage=token_usage_from_message(message),
            finish_reason=finish_reason_from_message(message) or "stop",
            metadata={
                "client": "LangChainChatModelClient",
                "provider": self.provider_name,
                "call_purpose": request.extra.get("call_purpose"),
            },
        )
