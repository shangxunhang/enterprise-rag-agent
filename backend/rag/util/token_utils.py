# src/rag_template/util/token_utils.py
"""
Token 级长度计算工具。

优先使用 HuggingFace tokenizer；如果本地 tokenizer 不可用，则回退到轻量规则 tokenizer。
这样 RecursiveChunker / HeadingChunker 可以按 token 控制 chunk_size，而不是按字符数。
"""

from __future__ import annotations

import re
from functools import lru_cache
from pathlib import Path
from typing import List, Optional


class TokenCounter:
    """
    Token 计数器。

    - 有可用 HuggingFace tokenizer 时：使用 tokenizer.encode(..., add_special_tokens=False)
    - 没有 tokenizer 时：使用规则回退，中文按单字，英文/数字按连续词，标点单独计数
    """

    _FALLBACK_PATTERN = re.compile(r"[\u4e00-\u9fff]|[A-Za-z0-9_]+|[^\w\s]", re.UNICODE)

    def __init__(self, tokenizer_name: Optional[str] = None, local_files_only: bool = True):
        self.tokenizer_name = tokenizer_name
        self.local_files_only = local_files_only
        self.tokenizer = self._load_tokenizer(tokenizer_name, local_files_only)

    @staticmethod
    def _load_tokenizer(tokenizer_name: Optional[str], local_files_only: bool):
        if not tokenizer_name:
            return None

        try:
            from transformers import AutoTokenizer
        except Exception:
            return None

        try:
            # 如果是本地路径但不存在，直接回退，避免 transformers 抛 repo_id 错误。
            maybe_path = Path(str(tokenizer_name))
            if (":" in str(tokenizer_name) or str(tokenizer_name).startswith("/")) and not maybe_path.exists():
                return None

            return AutoTokenizer.from_pretrained(
                tokenizer_name,
                local_files_only=local_files_only,
                trust_remote_code=True,
            )
        except Exception:
            return None

    def tokenize(self, text: str) -> List:
        if not text:
            return []
        if self.tokenizer is not None:
            return self.tokenizer.encode(text, add_special_tokens=False)
        return self._FALLBACK_PATTERN.findall(text)

    def count(self, text: str) -> int:
        return len(self.tokenize(text))

    @property
    def backend(self) -> str:
        if self.tokenizer is not None:
            return "huggingface"
        return "fallback_regex"


@lru_cache(maxsize=8)
def get_token_counter(
    tokenizer_name: Optional[str] = None,
    local_files_only: bool = True,
) -> TokenCounter:
    return TokenCounter(tokenizer_name=tokenizer_name, local_files_only=local_files_only)


def get_default_token_counter() -> TokenCounter:
    """
    从 RAGConfig 读取默认 tokenizer。

    优先级：
    1. CHUNK_TOKENIZER_MODEL_NAME
    2. EMBEDDING_MODEL_NAME
    3. fallback_regex
    """
    tokenizer_name = None
    local_files_only = True

    try:
        from rag.configs import RAGConfig

        tokenizer_name = getattr(RAGConfig, "CHUNK_TOKENIZER_MODEL_NAME", None)
        if tokenizer_name is None:
            tokenizer_name = getattr(RAGConfig, "EMBEDDING_MODEL_NAME", None)
        local_files_only = getattr(RAGConfig, "CHUNK_TOKENIZER_LOCAL_FILES_ONLY", True)
    except Exception:
        tokenizer_name = None

    return get_token_counter(str(tokenizer_name) if tokenizer_name else None, local_files_only)
