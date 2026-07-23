# =============================================================================
# 中文阅读说明：模型网关模块，用于屏蔽不同 LLM 提供方和本地模型调用差异。
# 主要定义：LocalHuggingFaceRuntime。建议先从公开入口函数开始，再沿调用关系向下阅读。
# =============================================================================
"""Lazy HuggingFace model loading and tensor generation."""

from __future__ import annotations

from contextlib import contextmanager
from pathlib import Path
from threading import RLock
from typing import Any, Dict, Iterator

from core.runtime.execution_control import current_execution_control


# 阅读注释（类）：封装 本地 hugging face 运行时，负责驱动实际运行流程并维护执行状态。
class LocalHuggingFaceRuntime:
    """封装 本地 hugging face 运行时，负责驱动实际运行流程并维护执行状态。"""
    # 阅读注释（函数）：初始化 LocalHuggingFaceRuntime，保存运行所需的依赖、配置或状态。
    def __init__(
        self,
        model_path: str | Path,
        device: str = "cuda",
        *,
        registered_model: str | None = None,
    ) -> None:
        """初始化 LocalHuggingFaceRuntime，保存运行所需的依赖、配置或状态。

        参数:
            model_path: 模型 路径，具体约束请结合类型标注和调用方确认。
            device: device，具体约束请结合类型标注和调用方确认。

        返回:
            None

        阅读提示:
            主要直接调用：Path。
        """
        self.model_path = Path(model_path)
        self.device = device
        self.registered_model = str(
            registered_model or self.model_path.name
        )
        self.tokenizer = None
        self.model = None
        # One runtime maps to one mutable model/tokenizer pair.  Loading,
        # generation and unloading must never overlap for that pair.
        self._lifecycle_lock = RLock()

    @property
    def is_loaded(self) -> bool:
        with self._lifecycle_lock:
            return self._is_loaded_unlocked()

    def _is_loaded_unlocked(self) -> bool:
        return self.tokenizer is not None and self.model is not None

    @staticmethod
    def _checkpoint_execution() -> None:
        control = current_execution_control()
        if control is not None:
            control.checkpoint()

    def unload(self) -> None:
        """Release an on-demand local model and return CUDA cache to the pool."""
        with self._lifecycle_lock:
            if self.model is None and self.tokenizer is None:
                return
            self.model = None
            self.tokenizer = None
            import gc

            gc.collect()
            try:
                import torch

                if torch.cuda.is_available():
                    torch.cuda.empty_cache()
            except Exception:
                # Lifecycle cleanup must not hide the original model-call result.
                pass

    # 阅读注释（函数）：确保 loaded 满足运行约束。
    def ensure_loaded(self) -> None:
        """确保 loaded 满足运行约束。

        返回:
            None

        阅读提示:
            主要直接调用：self.model_path.exists, FileNotFoundError, AutoTokenizer.from_pretrained, str, torch.cuda.is_available, AutoModelForCausalLM.from_pretrained, self.model.to, self.model.eval。
        """
        self._checkpoint_execution()
        with self._lifecycle_lock:
            self._ensure_loaded_unlocked()

    def _ensure_loaded_unlocked(self) -> None:
        self._checkpoint_execution()
        if self._is_loaded_unlocked():
            return
        if not self.model_path.exists():
            raise FileNotFoundError(f"Local Qwen model path not found: {self.model_path}")
        import torch
        from transformers import AutoModelForCausalLM, AutoTokenizer

        actual_device = "cuda" if self.device == "cuda" and torch.cuda.is_available() else "cpu"
        load_device_map = "auto" if actual_device == "cuda" else None
        print(
            f"[ModelRuntime] registered_model={self.registered_model}",
            flush=True,
        )
        print(f"[ModelRuntime] model_path={self.model_path}", flush=True)
        print(
            f"[ModelRuntime] requested_device={self.device} "
            f"device_map={load_device_map}",
            flush=True,
        )
        # Build into locals first so a failed model load cannot publish a
        # half-loaded tokenizer/model pair to another thread.
        tokenizer = AutoTokenizer.from_pretrained(
            str(self.model_path), trust_remote_code=True
        )
        dtype = torch.float16 if actual_device == "cuda" else torch.float32
        model = AutoModelForCausalLM.from_pretrained(
            str(self.model_path),
            torch_dtype=dtype,
            device_map=load_device_map,
            trust_remote_code=True,
        )
        if actual_device == "cpu":
            model.to("cpu")
        model.eval()
        self.tokenizer = tokenizer
        self.model = model
        self.device = actual_device
        print(
            f"[ModelRuntime] hf_device_map="
            f"{getattr(model, 'hf_device_map', None)}",
            flush=True,
        )

    @contextmanager
    def call_session(self) -> Iterator[None]:
        """Keep the runtime loaded and exclusively owned for one full client call."""
        self._checkpoint_execution()
        with self._lifecycle_lock:
            self._ensure_loaded_unlocked()
            self._checkpoint_execution()
            yield

    # 阅读注释（函数）：生成 LocalHuggingFaceRuntime。
    def generate(self, prompt_text: str, generation_kwargs: Dict[str, Any]):
        """生成 LocalHuggingFaceRuntime。

        参数:
            prompt_text: 提示词 文本，具体约束请结合类型标注和调用方确认。
            generation_kwargs: 生成 kwargs，具体约束请结合类型标注和调用方确认。

        返回:
            未显式标注；请结合调用方和实际返回语句理解。

        阅读提示:
            主要直接调用：self.ensure_loaded, self.tokenizer, next, self.model.parameters, value.to, inputs.items, torch.no_grad, self.model.generate。
        """
        with self.call_session():
            import torch

            self._checkpoint_execution()
            inputs = self.tokenizer(prompt_text, return_tensors="pt")
            model_device = next(self.model.parameters()).device
            moved = {key: value.to(model_device) for key, value in inputs.items()}
            self._checkpoint_execution()
            with torch.no_grad():
                output_ids = self.model.generate(**moved, **generation_kwargs)
            # The canonical client decodes this output into a ModelResponse so
            # Gateway can persist actual token usage before its post-provider
            # cancellation checkpoint discards the response.
            return moved, output_ids, model_device
