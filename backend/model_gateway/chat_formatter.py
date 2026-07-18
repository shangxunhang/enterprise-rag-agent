"""Chat message and prompt formatting for local causal models."""

from __future__ import annotations

from typing import Any

from schemas.model import ModelRequestSchema


class ChatPromptFormatter:
    @staticmethod
    def messages(request: ModelRequestSchema) -> list[dict[str, str]]:
        if request.messages:
            return [
                {"role": str(item["role"]), "content": str(item["content"])}
                for item in request.messages
            ]
        messages: list[dict[str, str]] = []
        if request.system_prompt:
            messages.append({"role": "system", "content": request.system_prompt})
        messages.append({"role": "user", "content": request.prompt})
        return messages

    def prompt_text(self, tokenizer: Any, request: ModelRequestSchema) -> str:
        messages = self.messages(request)
        if hasattr(tokenizer, "apply_chat_template"):
            return tokenizer.apply_chat_template(
                messages,
                tokenize=False,
                add_generation_prompt=True,
            )
        return "\n".join(
            [f"{item['role']}: {item['content']}" for item in messages]
            + ["assistant:"]
        )
