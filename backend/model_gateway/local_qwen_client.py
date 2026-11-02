"""基于统一提示词格式器和 HuggingFace 运行时实现的本地 Qwen 客户端。"""

from __future__ import annotations

import time
from pathlib import Path
from typing import Any, Dict

from contracts.base_client import BaseLLMClient
from model_gateway.chat_formatter import ChatPromptFormatter
from model_gateway.local_hf_runtime import LocalHuggingFaceRuntime
from schemas.model import ModelRequestSchema, ModelResponseSchema, TokenUsageSchema


class LocalQwenLLMClient(BaseLLMClient):
    """把统一模型请求转换为本地生成参数，并返回供应商无关响应。"""
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
        """保存注册名称和生成上限，并允许测试注入轻量运行时与格式器。"""
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

    @property
    def _tokenizer(self):
        """兼容旧测试代码的 tokenizer 代理；真实所有权属于 runtime。"""
        return self.runtime.tokenizer

    @_tokenizer.setter
    def _tokenizer(self, value):
        """把兼容赋值转发给 runtime，不建立第二份 tokenizer 状态。"""
        self.runtime.tokenizer = value

    @property
    def _model(self):
        """兼容旧测试代码的模型代理；真实所有权属于 runtime。"""
        return self.runtime.model

    @_model.setter
    def _model(self, value):
        """把兼容赋值转发给 runtime，不建立第二份模型状态。"""
        self.runtime.model = value

    def _ensure_loaded(self) -> None:
        """触发延迟加载，并同步 runtime 最终选择的实际设备。"""
        self.runtime.ensure_loaded()
        self.device = self.runtime.device

    def release(self) -> None:
        """Release model residency for on-demand profiles."""
        self.runtime.unload()

    def close(self) -> None:
        """Release local model resources during gateway shutdown."""
        self.runtime.unload()

    def _build_messages(self, request: ModelRequestSchema) -> list[dict[str, str]]:
        """兼容入口：委托格式器生成标准聊天消息序列。"""
        return self.formatter.messages(request)

    def _build_prompt_text(self, request: ModelRequestSchema) -> str:
        """在 runtime 会话内应用当前 tokenizer 的 chat template。"""
        with self.runtime.call_session():
            self.device = self.runtime.device
            return self.formatter.prompt_text(self.runtime.tokenizer, request)

    def generate(self, request: ModelRequestSchema) -> ModelResponseSchema:
        """在一个独占 runtime 会话内完成格式化、生成、解码和用量统计。

        这样按需释放不会发生在 Prompt 已格式化但模型尚未生成之间。请求的 ``max_tokens``
        仍受客户端实例 ``max_new_tokens`` 上限约束，采样参数只在 ``do_sample`` 为真时生效。
        """
        # Keep prompt formatting, generation and decoding in one exclusive
        # runtime session so an on-demand release cannot unload between them.
        with self.runtime.call_session():
            self.device = self.runtime.device
            return self._generate_in_session(request)

    def _generate_in_session(
        self,
        request: ModelRequestSchema,
    ) -> ModelResponseSchema:
        """Generate while ``runtime.call_session`` owns the runtime lock."""
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
