"""Contract tests for LangChain interoperability over ModelGateway."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from langchain_core.messages import AIMessage, BaseMessage
from langchain_core.runnables import RunnableLambda

from model_gateway.fake_llm_client import FakeLLMClient
from model_gateway.integrations.langchain import (
    LangChainChatModelClient,
    build_model_gateway_runnable,
)
from model_gateway.model_gateway import ModelGateway
from schemas.model import ModelRequestSchema, ModelResponseSchema


def _request(
    *,
    model_name: str,
    prompt: str = "生成一句测试文本",
) -> ModelRequestSchema:
    return ModelRequestSchema(
        model_call_id="model_call_langchain_test",
        task_id="task_langchain_test",
        run_id="run_langchain_test",
        model_name=model_name,
        caller_agent="LangChainModelGatewayTest",
        prompt=prompt,
        system_prompt="你是测试助手。",
        temperature=0.2,
        max_tokens=128,
        created_at=datetime.now(timezone.utc).isoformat(),
        extra={"call_purpose": "langchain_model_gateway_acceptance"},
    )


def _stub_chat_model() -> RunnableLambda:
    def invoke(
        messages: list[BaseMessage],
        **kwargs: Any,
    ) -> AIMessage:
        assert messages[0].type == "system"
        assert messages[1].type == "human"
        assert kwargs["temperature"] == 0.2
        assert kwargs["max_tokens"] == 128
        return AIMessage(
            content="LangChain provider response",
            response_metadata={"finish_reason": "stop"},
            usage_metadata={
                "input_tokens": 11,
                "output_tokens": 7,
                "total_tokens": 18,
            },
        )

    return RunnableLambda(invoke)


def test_langchain_chat_model_can_be_registered_in_existing_gateway() -> None:
    gateway = ModelGateway(default_model_name="langchain_stub")
    gateway.register_client(
        LangChainChatModelClient(
            model_name="langchain_stub",
            chat_model=_stub_chat_model(),
            provider_name="test_langchain",
        )
    )

    response = gateway.generate(_request(model_name="langchain_stub"))

    assert response.success is True
    assert response.content == "LangChain provider response"
    assert response.model_name == "langchain_stub"
    assert response.finish_reason == "stop"
    assert response.token_usage.prompt_tokens == 11
    assert response.token_usage.completion_tokens == 7
    assert response.token_usage.total_tokens == 18
    assert response.raw_output["provider"] == "test_langchain"


def test_model_gateway_can_be_invoked_as_langchain_runnable() -> None:
    gateway = ModelGateway(default_model_name="fake_llm")
    gateway.register_client(FakeLLMClient())
    runnable = build_model_gateway_runnable(gateway)

    response = runnable.invoke(_request(model_name="fake_llm"))

    assert isinstance(response, ModelResponseSchema)
    assert response.success is True
    assert response.model_name == "fake_llm"
    assert response.model_call_id == "model_call_langchain_test"


def test_model_gateway_runnable_accepts_dict_without_losing_contract() -> None:
    gateway = ModelGateway(default_model_name="fake_llm")
    gateway.register_client(FakeLLMClient())
    runnable = build_model_gateway_runnable(gateway)

    request = _request(model_name="fake_llm", prompt="dict input")
    response = runnable.invoke(request.model_dump())

    assert response.task_id == request.task_id
    assert response.run_id == request.run_id
    assert response.success is True


def test_langchain_provider_failure_is_normalized_by_existing_model_invoker() -> None:
    def fail(messages: list[BaseMessage], **kwargs: Any) -> AIMessage:
        raise RuntimeError("provider unavailable")

    gateway = ModelGateway(default_model_name="langchain_fail")
    gateway.register_client(
        LangChainChatModelClient(
            model_name="langchain_fail",
            chat_model=RunnableLambda(fail),
        )
    )

    response = gateway.generate(_request(model_name="langchain_fail"))

    assert response.success is False
    assert response.error is not None
    assert response.error.error_code == "MODEL_GATEWAY_CALL_FAILED"
    assert response.finish_reason == "error"
    assert "provider unavailable" in (response.error_message or "")


def test_existing_fake_client_behavior_remains_unchanged() -> None:
    gateway = ModelGateway(default_model_name="fake_llm")
    gateway.register_client(FakeLLMClient())

    direct = gateway.generate(_request(model_name="fake_llm"))
    via_runnable = build_model_gateway_runnable(gateway).invoke(
        _request(model_name="fake_llm")
    )

    assert direct.success is True
    assert via_runnable.success is True
    assert direct.content == via_runnable.content
    assert direct.model_name == via_runnable.model_name == "fake_llm"
