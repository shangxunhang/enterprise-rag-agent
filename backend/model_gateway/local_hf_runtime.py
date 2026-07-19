# =============================================================================
# 中文阅读说明：模型网关模块，用于屏蔽不同 LLM 提供方和本地模型调用差异。
# 主要定义：LocalHuggingFaceRuntime。建议先从公开入口函数开始，再沿调用关系向下阅读。
# =============================================================================
"""Lazy HuggingFace model loading and tensor generation."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Dict


# 阅读注释（类）：封装 本地 hugging face 运行时，负责驱动实际运行流程并维护执行状态。
class LocalHuggingFaceRuntime:
    """封装 本地 hugging face 运行时，负责驱动实际运行流程并维护执行状态。"""
    # 阅读注释（函数）：初始化 LocalHuggingFaceRuntime，保存运行所需的依赖、配置或状态。
    def __init__(self, model_path: str | Path, device: str = "cuda") -> None:
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
        self.tokenizer = None
        self.model = None

    # 阅读注释（函数）：确保 loaded 满足运行约束。
    def ensure_loaded(self) -> None:
        """确保 loaded 满足运行约束。

        返回:
            None

        阅读提示:
            主要直接调用：self.model_path.exists, FileNotFoundError, AutoTokenizer.from_pretrained, str, torch.cuda.is_available, AutoModelForCausalLM.from_pretrained, self.model.to, self.model.eval。
        """
        if self.tokenizer is not None and self.model is not None:
            return
        if not self.model_path.exists():
            raise FileNotFoundError(f"Local Qwen model path not found: {self.model_path}")
        import torch
        from transformers import AutoModelForCausalLM, AutoTokenizer

        self.tokenizer = AutoTokenizer.from_pretrained(
            str(self.model_path), trust_remote_code=True
        )
        actual_device = "cuda" if self.device == "cuda" and torch.cuda.is_available() else "cpu"
        dtype = torch.float16 if actual_device == "cuda" else torch.float32
        self.model = AutoModelForCausalLM.from_pretrained(
            str(self.model_path),
            torch_dtype=dtype,
            device_map="auto" if actual_device == "cuda" else None,
            trust_remote_code=True,
        )
        if actual_device == "cpu":
            self.model.to("cpu")
        self.model.eval()
        self.device = actual_device

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
        self.ensure_loaded()
        import torch

        inputs = self.tokenizer(prompt_text, return_tensors="pt")
        model_device = next(self.model.parameters()).device
        moved = {key: value.to(model_device) for key, value in inputs.items()}
        with torch.no_grad():
            output_ids = self.model.generate(**moved, **generation_kwargs)
        return moved, output_ids, model_device
