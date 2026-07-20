# =============================================================================
# 中文阅读说明：模型网关模块，用于屏蔽不同 LLM 提供方和本地模型调用差异。
# 主要定义：LocalQwenLLMClient。建议先从公开入口函数开始，再沿调用关系向下阅读。
# =============================================================================
"""Thin Qwen client over prompt formatting and HuggingFace runtime services."""

from __future__ import annotations

import time
from pathlib import Path
from typing import Any, Dict

from contracts.base_client import BaseLLMClient
from model_gateway.chat_formatter import ChatPromptFormatter
from model_gateway.local_hf_runtime import LocalHuggingFaceRuntime
from schemas.model import ModelRequestSchema, ModelResponseSchema, TokenUsageSchema


# 阅读注释（类）：封装 本地 qwen llmclient，集中封装相关状态、依赖和行为。
class LocalQwenLLMClient(BaseLLMClient):
    """封装 本地 qwen llmclient，集中封装相关状态、依赖和行为。"""
    # 阅读注释（函数）：初始化 LocalQwenLLMClient，保存运行所需的依赖、配置或状态。
    def __init__(
        self,
        model_name: str,
        model_path: str | Path,
        device: str = "cuda",
        max_new_tokens: int = 256,
        *,
        runtime: LocalHuggingFaceRuntime | None = None,
        formatter: ChatPromptFormatter | None = None,
    ) -> None:
        """初始化 LocalQwenLLMClient，保存运行所需的依赖、配置或状态。

        参数:
            model_name: 模型 名称，具体约束请结合类型标注和调用方确认。
            model_path: 模型 路径，具体约束请结合类型标注和调用方确认。
            device: device，具体约束请结合类型标注和调用方确认。
            max_new_tokens: max new tokens，具体约束请结合类型标注和调用方确认。
            runtime: 运行时，具体约束请结合类型标注和调用方确认。
            formatter: formatter，具体约束请结合类型标注和调用方确认。

        返回:
            None

        阅读提示:
            主要直接调用：Path, LocalHuggingFaceRuntime, ChatPromptFormatter。
        """
        self.model_name = model_name
        self.model_path = Path(model_path)
        self.device = device
        self.max_new_tokens = max_new_tokens
        self.runtime = runtime or LocalHuggingFaceRuntime(
            self.model_path,
            device,
            registered_model=self.model_name,
        )
        self.formatter = formatter or ChatPromptFormatter()

    # 阅读注释（函数）：处理 tokenizer 相关逻辑。
    @property
    def _tokenizer(self):
        """处理 tokenizer 相关逻辑。

        返回:
            未显式标注；请结合调用方和实际返回语句理解。
        """
        return self.runtime.tokenizer

    # 阅读注释（函数）：处理 tokenizer 相关逻辑。
    @_tokenizer.setter
    def _tokenizer(self, value):
        """处理 tokenizer 相关逻辑。

        参数:
            value: value，具体约束请结合类型标注和调用方确认。

        返回:
            未显式标注；请结合调用方和实际返回语句理解。
        """
        self.runtime.tokenizer = value

    # 阅读注释（函数）：处理 模型 相关逻辑。
    @property
    def _model(self):
        """处理 模型 相关逻辑。

        返回:
            未显式标注；请结合调用方和实际返回语句理解。
        """
        return self.runtime.model

    # 阅读注释（函数）：处理 模型 相关逻辑。
    @_model.setter
    def _model(self, value):
        """处理 模型 相关逻辑。

        参数:
            value: value，具体约束请结合类型标注和调用方确认。

        返回:
            未显式标注；请结合调用方和实际返回语句理解。
        """
        self.runtime.model = value

    # 阅读注释（函数）：确保 loaded 满足运行约束。
    def _ensure_loaded(self) -> None:
        """确保 loaded 满足运行约束。

        返回:
            None

        阅读提示:
            主要直接调用：self.runtime.ensure_loaded。
        """
        self.runtime.ensure_loaded()
        self.device = self.runtime.device

    # 阅读注释（函数）：构建 消息集合。
    def _build_messages(self, request: ModelRequestSchema) -> list[dict[str, str]]:
        """构建 消息集合。

        参数:
            request: 当前请求对象。

        返回:
            list[dict[str, str]]

        阅读提示:
            主要直接调用：self.formatter.messages。
        """
        return self.formatter.messages(request)

    # 阅读注释（函数）：构建 提示词 文本。
    def _build_prompt_text(self, request: ModelRequestSchema) -> str:
        """构建 提示词 文本。

        参数:
            request: 当前请求对象。

        返回:
            str

        阅读提示:
            主要直接调用：self._ensure_loaded, self.formatter.prompt_text。
        """
        self._ensure_loaded()
        return self.formatter.prompt_text(self.runtime.tokenizer, request)

    # 阅读注释（函数）：生成 LocalQwenLLMClient。
    def generate(self, request: ModelRequestSchema) -> ModelResponseSchema:
        """生成 LocalQwenLLMClient。

        参数:
            request: 当前请求对象。

        返回:
            ModelResponseSchema

        阅读提示:
            主要直接调用：self._ensure_loaded, time.time, self.formatter.prompt_text, min, max, self.runtime.generate, int, strip。
        """
        self._ensure_loaded()
        started = time.time()
        prompt_text = self.formatter.prompt_text(self.runtime.tokenizer, request)
        max_new_tokens = min(
            request.max_tokens or self.max_new_tokens,
            self.max_new_tokens,
        )
        requested_generation = request.extra.get("generation_params")
        requested_generation = (
            requested_generation
            if isinstance(requested_generation, dict)
            else {}
        )
        temperature = request.temperature if request.temperature is not None else 0.0
        do_sample = bool(
            requested_generation.get("do_sample", temperature > 0)
        )
        generation_kwargs: Dict[str, Any] = {
            "max_new_tokens": max_new_tokens,
            "do_sample": do_sample,
            "pad_token_id": self.runtime.tokenizer.eos_token_id,
        }
        if do_sample:
            generation_kwargs["temperature"] = max(temperature, 1e-5)
            generation_kwargs["top_p"] = max(
                0.0,
                min(1.0, float(requested_generation.get("top_p", 0.9))),
            )

        inputs, output_ids, model_device = self.runtime.generate(
            prompt_text,
            generation_kwargs,
        )
        input_token_count = int(inputs["input_ids"].shape[-1])
        generated_ids = output_ids[0][input_token_count:]
        content = self.runtime.tokenizer.decode(
            generated_ids,
            skip_special_tokens=True,
        ).strip()
        latency_ms = int((time.time() - started) * 1000)
        completion_token_count = int(generated_ids.shape[-1])
        finish_reason = "stop"
        if (
            completion_token_count > 0
            and completion_token_count >= max_new_tokens
            and int(generated_ids[-1]) != int(self.runtime.tokenizer.eos_token_id)
        ):
            finish_reason = "length"
        return ModelResponseSchema(
            model_call_id=request.model_call_id,
            task_id=request.task_id,
            run_id=request.run_id,
            model_name=self.model_name,
            success=True,
            content=content,
            raw_output={
                "provider": "local_huggingface",
                "model_path": str(self.model_path),
                "device": str(model_device),
                "prompt_preview": prompt_text[:1000],
            },
            latency_ms=latency_ms,
            created_at=request.created_at,
            token_usage=TokenUsageSchema(
                prompt_tokens=input_token_count,
                completion_tokens=completion_token_count,
                total_tokens=input_token_count + completion_token_count,
            ),
            finish_reason=finish_reason,
            metadata={
                "client": "LocalQwenLLMClient",
                "model_path": str(self.model_path),
                "device": str(model_device),
                "call_purpose": request.extra.get("call_purpose"),
            },
        )
