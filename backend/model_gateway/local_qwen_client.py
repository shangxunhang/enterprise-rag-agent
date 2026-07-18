"""Thin Qwen client over prompt formatting and HuggingFace runtime services."""

from __future__ import annotations

import time
from pathlib import Path
from typing import Any, Dict

from contracts.base_client import BaseLLMClient
from model_gateway.chat_formatter import ChatPromptFormatter
from model_gateway.local_hf_runtime import LocalHuggingFaceRuntime
from schemas.model import ModelRequestSchema, ModelResponseSchema, TokenUsageSchema


class LocalQwenLLMClient(BaseLLMClient):
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
        self.model_name = model_name
        self.model_path = Path(model_path)
        self.device = device
        self.max_new_tokens = max_new_tokens
        self.runtime = runtime or LocalHuggingFaceRuntime(self.model_path, device)
        self.formatter = formatter or ChatPromptFormatter()

    @property
    def _tokenizer(self):
        return self.runtime.tokenizer

    @_tokenizer.setter
    def _tokenizer(self, value):
        self.runtime.tokenizer = value

    @property
    def _model(self):
        return self.runtime.model

    @_model.setter
    def _model(self, value):
        self.runtime.model = value

    def _ensure_loaded(self) -> None:
        self.runtime.ensure_loaded()
        self.device = self.runtime.device

    def _build_messages(self, request: ModelRequestSchema) -> list[dict[str, str]]:
        return self.formatter.messages(request)

    def _build_prompt_text(self, request: ModelRequestSchema) -> str:
        self._ensure_loaded()
        return self.formatter.prompt_text(self.runtime.tokenizer, request)

    def generate(self, request: ModelRequestSchema) -> ModelResponseSchema:
        self._ensure_loaded()
        started = time.time()
        prompt_text = self.formatter.prompt_text(self.runtime.tokenizer, request)
        max_new_tokens = min(
            request.max_tokens or self.max_new_tokens,
            self.max_new_tokens,
        )
        temperature = request.temperature if request.temperature is not None else 0.0
        do_sample = temperature > 0
        generation_kwargs: Dict[str, Any] = {
            "max_new_tokens": max_new_tokens,
            "do_sample": do_sample,
            "pad_token_id": self.runtime.tokenizer.eos_token_id,
        }
        if do_sample:
            generation_kwargs["temperature"] = max(temperature, 1e-5)
            generation_kwargs["top_p"] = 0.9

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
