"""Message and response mapping between enterprise model schemas and LangChain."""

from __future__ import annotations

from typing import Any

from langchain_core.messages import (
    AIMessage,
    BaseMessage,
    HumanMessage,
    SystemMessage,
)

from schemas.model import ModelRequestSchema, TokenUsageSchema


def request_to_langchain_messages(request: ModelRequestSchema) -> list[BaseMessage]:
    """Map the stable enterprise request schema into LangChain messages.

    Tool-role messages are deliberately not inferred here because the current
    ModelRequestSchema stores only ``role`` and ``content`` and therefore does
    not carry the tool_call_id required to reconstruct a real ToolMessage.
    """
    raw_messages = list(request.messages or [])
    if not raw_messages:
        messages: list[BaseMessage] = []
        if request.system_prompt:
            messages.append(SystemMessage(content=request.system_prompt))
        messages.append(HumanMessage(content=request.prompt))
        return messages

    mapped: list[BaseMessage] = []
    for item in raw_messages:
        role = str(item.get("role") or "").strip().lower()
        content = str(item.get("content") or "")
        if role == "system":
            mapped.append(SystemMessage(content=content))
        elif role in {"user", "human"}:
            mapped.append(HumanMessage(content=content))
        elif role in {"assistant", "ai"}:
            mapped.append(AIMessage(content=content))
        elif role == "tool":
            raise ValueError(
                "ModelRequestSchema tool messages cannot be losslessly mapped to "
                "LangChain ToolMessage because tool_call_id is not present"
            )
        else:
            raise ValueError(f"Unsupported model message role: {role!r}")
    return mapped


def message_content_to_text(content: Any) -> str:
    """Normalize LangChain message content into the enterprise text contract."""
    if isinstance(content, str):
        return content.strip()
    if isinstance(content, list):
        parts: list[str] = []
        for item in content:
            if isinstance(item, str):
                parts.append(item)
            elif isinstance(item, dict):
                text = item.get("text")
                if text is not None:
                    parts.append(str(text))
        return "\n".join(part for part in parts if part).strip()
    return str(content or "").strip()


def token_usage_from_message(message: BaseMessage) -> TokenUsageSchema:
    """Extract normalized token usage from modern LangChain AIMessage metadata."""
    usage = getattr(message, "usage_metadata", None)
    if isinstance(usage, dict):
        prompt_tokens = usage.get("input_tokens")
        completion_tokens = usage.get("output_tokens")
        total_tokens = usage.get("total_tokens")
        return TokenUsageSchema(
            prompt_tokens=(
                int(prompt_tokens) if prompt_tokens is not None else None
            ),
            completion_tokens=(
                int(completion_tokens) if completion_tokens is not None else None
            ),
            total_tokens=int(total_tokens) if total_tokens is not None else None,
            extra={"langchain_usage_metadata": dict(usage)},
        )

    response_metadata = getattr(message, "response_metadata", None)
    if isinstance(response_metadata, dict):
        raw = response_metadata.get("token_usage") or response_metadata.get("usage")
        if isinstance(raw, dict):
            prompt_tokens = (
                raw.get("prompt_tokens")
                if raw.get("prompt_tokens") is not None
                else raw.get("input_tokens")
            )
            completion_tokens = (
                raw.get("completion_tokens")
                if raw.get("completion_tokens") is not None
                else raw.get("output_tokens")
            )
            total_tokens = raw.get("total_tokens")
            return TokenUsageSchema(
                prompt_tokens=(
                    int(prompt_tokens) if prompt_tokens is not None else None
                ),
                completion_tokens=(
                    int(completion_tokens) if completion_tokens is not None else None
                ),
                total_tokens=(
                    int(total_tokens) if total_tokens is not None else None
                ),
                extra={"provider_token_usage": dict(raw)},
            )

    return TokenUsageSchema()


def finish_reason_from_message(message: BaseMessage) -> str | None:
    response_metadata = getattr(message, "response_metadata", None)
    if not isinstance(response_metadata, dict):
        return None
    value = (
        response_metadata.get("finish_reason")
        or response_metadata.get("stop_reason")
        or response_metadata.get("finish_details")
    )
    if isinstance(value, dict):
        value = value.get("type") or value.get("reason")
    return str(value) if value is not None else None
