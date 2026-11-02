"""Keep the DeepSeek output-limit field at the SDK serialization boundary."""
from __future__ import annotations

from typing import Any

from langchain_openai import ChatOpenAI


class DeepSeekChatOpenAI(ChatOpenAI):
    """Preserve ChatOpenAI behavior while using DeepSeek's max_tokens field."""

    def _get_request_payload(self, input_: Any, *, stop=None, **kwargs: Any) -> dict:
        payload = super()._get_request_payload(input_, stop=stop, **kwargs)
        if "max_completion_tokens" in payload:
            limit = payload.pop("max_completion_tokens")
            if "max_tokens" in payload and payload["max_tokens"] != limit:
                raise ValueError("Conflicting DeepSeek output token limits")
            payload["max_tokens"] = limit
        return payload
