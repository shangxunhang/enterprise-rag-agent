"""把统一模型请求转换为本地因果语言模型可消费的聊天提示词。"""

from __future__ import annotations

from typing import Any

from schemas.model import ModelRequestSchema


class ChatPromptFormatter:
    """统一显式消息列表与 ``system_prompt + prompt`` 两种调用形式。"""
    @staticmethod
    def messages(request: ModelRequestSchema) -> list[dict[str, str]]:
        """返回标准 ``role/content`` 消息序列；显式 ``messages`` 优先级最高。"""
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
        """优先使用模型自带 chat template；缺失时生成稳定的纯文本角色前缀。"""
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
