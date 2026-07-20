"""Focused acceptance for LangChain interoperability over ModelGateway."""

from __future__ import annotations

import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


PROJECT_ROOT = Path(__file__).resolve().parents[1]
BACKEND_ROOT = PROJECT_ROOT / "backend"
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from langchain_core.messages import AIMessage, BaseMessage
from langchain_core.runnables import RunnableLambda

from model_gateway.fake_llm_client import FakeLLMClient
from model_gateway.integrations.langchain import (
    LangChainChatModelClient,
    build_model_gateway_runnable,
)
from model_gateway.model_gateway import ModelGateway
from schemas.model import ModelRequestSchema


def _request(model_name: str, call_id: str) -> ModelRequestSchema:
    return ModelRequestSchema(
        model_call_id=call_id,
        task_id="task_langchain_model_gateway_acceptance",
        run_id="run_langchain_model_gateway_acceptance",
        model_name=model_name,
        caller_agent="LangChainModelGatewayAcceptance",
        prompt="生成一句模型网关验收文本。",
        system_prompt="你是企业级模型网关验收助手。",
        temperature=0.2,
        max_tokens=128,
        created_at=datetime.now(timezone.utc).isoformat(),
        extra={"call_purpose": "langchain_model_gateway_acceptance"},
    )


def _langchain_stub(
    messages: list[BaseMessage],
    **kwargs: Any,
) -> AIMessage:
    return AIMessage(
        content="LangChain ChatModel adapter OK",
        response_metadata={
            "finish_reason": "stop",
            "received_message_count": len(messages),
            "received_temperature": kwargs.get("temperature"),
            "received_max_tokens": kwargs.get("max_tokens"),
        },
        usage_metadata={
            "input_tokens": 10,
            "output_tokens": 6,
            "total_tokens": 16,
        },
    )


def main() -> int:
    # Direction A:
    # LangChain ChatModel/Runnable -> BaseLLMClient -> existing ModelGateway
    provider_gateway = ModelGateway(default_model_name="langchain_stub")
    provider_gateway.register_client(
        LangChainChatModelClient(
            model_name="langchain_stub",
            chat_model=RunnableLambda(_langchain_stub),
            provider_name="acceptance_stub",
        )
    )
    provider_response = provider_gateway.generate(
        _request("langchain_stub", "call_langchain_provider")
    )

    # Direction B:
    # existing ModelGateway -> LangChain Runnable
    existing_gateway = ModelGateway(default_model_name="fake_llm")
    existing_gateway.register_client(FakeLLMClient())
    gateway_runnable = build_model_gateway_runnable(existing_gateway)
    runnable_response = gateway_runnable.invoke(
        _request("fake_llm", "call_gateway_runnable")
    )

    summary = {
        "langchain_provider_inside_gateway": {
            "success": provider_response.success,
            "model_name": provider_response.model_name,
            "content": provider_response.content,
            "finish_reason": provider_response.finish_reason,
            "token_usage": provider_response.token_usage.model_dump(),
            "provider": provider_response.raw_output.get("provider"),
        },
        "model_gateway_as_langchain_runnable": {
            "success": runnable_response.success,
            "model_name": runnable_response.model_name,
            "output_type": runnable_response.__class__.__name__,
            "model_call_id": runnable_response.model_call_id,
        },
    }
    print(json.dumps(summary, ensure_ascii=False, indent=2))

    if not provider_response.success or not runnable_response.success:
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
