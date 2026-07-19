# =============================================================================
# 中文阅读说明：模型网关模块，用于屏蔽不同 LLM 提供方和本地模型调用差异。
# 主要定义：ChatPromptFormatter。建议先从公开入口函数开始，再沿调用关系向下阅读。
# =============================================================================
"""Chat message and prompt formatting for local causal models."""

from __future__ import annotations

from typing import Any

from schemas.model import ModelRequestSchema


# 阅读注释（类）：封装 chat 提示词 formatter，集中封装相关状态、依赖和行为。
class ChatPromptFormatter:
    """封装 chat 提示词 formatter，集中封装相关状态、依赖和行为。"""
    # 阅读注释（函数）：处理 消息集合 相关逻辑。
    @staticmethod
    def messages(request: ModelRequestSchema) -> list[dict[str, str]]:
        """处理 消息集合 相关逻辑。

        参数:
            request: 当前请求对象。

        返回:
            list[dict[str, str]]

        阅读提示:
            主要直接调用：str, messages.append。
        """
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

    # 阅读注释（函数）：处理 提示词 文本 相关逻辑。
    def prompt_text(self, tokenizer: Any, request: ModelRequestSchema) -> str:
        """处理 提示词 文本 相关逻辑。

        参数:
            tokenizer: tokenizer，具体约束请结合类型标注和调用方确认。
            request: 当前请求对象。

        返回:
            str

        阅读提示:
            主要直接调用：self.messages, hasattr, tokenizer.apply_chat_template, join。
        """
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
