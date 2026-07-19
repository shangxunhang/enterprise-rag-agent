# =============================================================================
# 中文阅读说明：RAG 核心模块，负责查询变换、召回、融合、重排、证据评估和上下文组装。
# 主要定义：LocalLLMGenerator。建议先从公开入口函数开始，再沿调用关系向下阅读。
# =============================================================================
"""
src/llm/local_llm.py
====================

本地大模型推理模块。

职责：
1. 从本地路径加载 tokenizer 和 causal LM
2. 接收 RAG prompt
3. 调用 model.generate()
4. 返回生成答案

当前主要适配 Qwen2.5-Instruct 这类 decoder-only instruct 模型。
"""

from pathlib import Path
from typing import Optional, Union

try:
    import torch
    from transformers import AutoTokenizer, AutoModelForCausalLM
except ModuleNotFoundError:  # pragma: no cover - import-time compatibility for config/tests
    torch = None
    AutoTokenizer = None
    AutoModelForCausalLM = None


# 阅读注释（类）：封装 本地 llmgenerator，集中封装相关状态、依赖和行为。
class LocalLLMGenerator:
    """
    本地 LLM 生成器。

    对外只暴露 generate(prompt)。
    RAG 其他模块不关心 tokenizer / model.generate 的细节。
    """

    # 阅读注释（函数）：初始化 LocalLLMGenerator，保存运行所需的依赖、配置或状态。
    def __init__(
        self,
        model_name: Union[str, Path],
        device: Optional[str] = None,
    ):
        """初始化 LocalLLMGenerator，保存运行所需的依赖、配置或状态。

        参数:
            model_name: 模型 名称，具体约束请结合类型标注和调用方确认。
            device: device，具体约束请结合类型标注和调用方确认。

        返回:
            未显式标注；请结合调用方和实际返回语句理解。

        阅读提示:
            主要直接调用：str, ImportError, torch.cuda.is_available, print, AutoTokenizer.from_pretrained, AutoModelForCausalLM.from_pretrained, self.model.to, self.model.eval。
        """
        self.model_name = str(model_name)

        if torch is None or AutoTokenizer is None or AutoModelForCausalLM is None:
            raise ImportError(
                "LocalLLMGenerator requires torch and transformers. "
                "Please install project dependencies in the agent conda environment before loading the local LLM."
            )

        if device is None:
            device = "cuda" if torch.cuda.is_available() else "cpu"

        self.device = device

        print("=" * 80)
        print("[LocalLLM] 正在加载本地大模型")
        print(f"[LocalLLM] model_name: {self.model_name}")
        print(f"[LocalLLM] device: {self.device}")

        self.tokenizer = AutoTokenizer.from_pretrained(
            self.model_name,
            trust_remote_code=True,
        )

        if self.tokenizer.pad_token is None:
            self.tokenizer.pad_token = self.tokenizer.eos_token

        torch_dtype = torch.float16 if self.device == "cuda" else torch.float32

        self.model = AutoModelForCausalLM.from_pretrained(
            self.model_name,
            torch_dtype=torch_dtype,
            trust_remote_code=True,
        )

        self.model.to(self.device)
        self.model.eval()

        print("[LocalLLM] 模型加载完成")
        print("=" * 80)

    # 阅读注释（函数）：构建 chat 提示词。
    def _build_chat_prompt(self, prompt: str, system_prompt: Optional[str] = None) -> str:
        """
        使用 tokenizer 的 chat_template 构造 Instruct 模型输入。

        Qwen2.5-Instruct 推荐使用 chat template。
        如果 tokenizer 没有 chat_template，则退化为 system + user 的纯文本 prompt。
        """
        final_system_prompt = system_prompt or "你是一个严谨的知识库问答助手。请严格根据提供的资料回答问题。"
        messages = [
            {
                "role": "system",
                "content": final_system_prompt,
            },
            {
                "role": "user",
                "content": prompt,
            },
        ]

        if getattr(self.tokenizer, "chat_template", None):
            return self.tokenizer.apply_chat_template(
                messages,
                tokenize=False,
                add_generation_prompt=True,
            )

        return f"{final_system_prompt}\n\n{prompt}"

    # 阅读注释（函数）：生成 LocalLLMGenerator。
    def generate(
        self,
        prompt: str,
        max_new_tokens: int = 256,
        temperature: float = 0.7,
        top_p: float = 0.9,
        do_sample: bool = False,
        system_prompt: Optional[str] = None,
    ) -> str:
        """
        根据 prompt 生成回答。

        Args:
            prompt: RAG prompt
            max_new_tokens: 最大新生成 token 数
            temperature: 采样温度
            top_p: nucleus sampling 参数
            do_sample: 是否采样。False 时偏确定性输出。
            system_prompt: 可选系统提示词。RAG 最终回答、RAG-Fusion 查询改写、HyDE 生成
                可以复用同一个本地模型，但使用不同 system prompt。

        Returns:
            answer: 模型生成的答案
        """
        if not prompt or not prompt.strip():
            raise ValueError("prompt 不能为空")

        final_prompt = self._build_chat_prompt(prompt, system_prompt=system_prompt)

        encoded = self.tokenizer(
            final_prompt,
            return_tensors="pt",
            truncation=True,
            max_length=4096,
        )

        input_ids = encoded["input_ids"].to(self.device)
        attention_mask = encoded["attention_mask"].to(self.device)

        with torch.no_grad():
            output_ids = self.model.generate(
                input_ids=input_ids,
                attention_mask=attention_mask,
                max_new_tokens=max_new_tokens,
                temperature=temperature,
                top_p=top_p,
                do_sample=do_sample,
                pad_token_id=self.tokenizer.pad_token_id,
                eos_token_id=self.tokenizer.eos_token_id,
            )

        # 只截取新生成部分，避免把 prompt 一起输出
        new_tokens = output_ids[0][input_ids.shape[-1]:]

        answer = self.tokenizer.decode(
            new_tokens,
            skip_special_tokens=True,
        )

        return answer.strip()