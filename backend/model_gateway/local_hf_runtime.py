"""Lazy HuggingFace model loading and tensor generation."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Dict


class LocalHuggingFaceRuntime:
    def __init__(self, model_path: str | Path, device: str = "cuda") -> None:
        self.model_path = Path(model_path)
        self.device = device
        self.tokenizer = None
        self.model = None

    def ensure_loaded(self) -> None:
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

    def generate(self, prompt_text: str, generation_kwargs: Dict[str, Any]):
        self.ensure_loaded()
        import torch

        inputs = self.tokenizer(prompt_text, return_tensors="pt")
        model_device = next(self.model.parameters()).device
        moved = {key: value.to(model_device) for key, value in inputs.items()}
        with torch.no_grad():
            output_ids = self.model.generate(**moved, **generation_kwargs)
        return moved, output_ids, model_device
