# =============================================================================
# 中文阅读说明：RAG 核心模块，负责查询变换、召回、融合、重排、证据评估和上下文组装。
# 主要定义：TokenCounter、get_token_counter、get_default_token_counter。建议先从公开入口函数开始，再沿调用关系向下阅读。
# =============================================================================
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


# 阅读注释（类）：封装 Token counter，集中封装相关状态、依赖和行为。
class TokenCounter:
    """
    Token 计数器。

    - 有可用 HuggingFace tokenizer 时：使用 tokenizer.encode(..., add_special_tokens=False)
    - 没有 tokenizer 时：使用规则回退，中文按单字，英文/数字按连续词，标点单独计数
    """

    _FALLBACK_PATTERN = re.compile(r"[\u4e00-\u9fff]|[A-Za-z0-9_]+|[^\w\s]", re.UNICODE)

    # 阅读注释（函数）：初始化 TokenCounter，保存运行所需的依赖、配置或状态。
    def __init__(self, tokenizer_name: Optional[str] = None, local_files_only: bool = True):
        """初始化 TokenCounter，保存运行所需的依赖、配置或状态。

        参数:
            tokenizer_name: tokenizer 名称，具体约束请结合类型标注和调用方确认。
            local_files_only: 本地 files only，具体约束请结合类型标注和调用方确认。

        返回:
            未显式标注；请结合调用方和实际返回语句理解。

        阅读提示:
            主要直接调用：self._load_tokenizer。
        """
        self.tokenizer_name = tokenizer_name
        self.local_files_only = local_files_only
        self.tokenizer = self._load_tokenizer(tokenizer_name, local_files_only)

    # 阅读注释（函数）：加载 tokenizer。
    @staticmethod
    def _load_tokenizer(tokenizer_name: Optional[str], local_files_only: bool):
        """加载 tokenizer。

        参数:
            tokenizer_name: tokenizer 名称，具体约束请结合类型标注和调用方确认。
            local_files_only: 本地 files only，具体约束请结合类型标注和调用方确认。

        返回:
            未显式标注；请结合调用方和实际返回语句理解。

        阅读提示:
            主要直接调用：Path, str, startswith, maybe_path.exists, AutoTokenizer.from_pretrained。
        """
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

    # 阅读注释（函数）：处理 tokenize 相关逻辑。
    def tokenize(self, text: str) -> List:
        """处理 tokenize 相关逻辑。

        参数:
            text: 待处理文本。

        返回:
            List

        阅读提示:
            主要直接调用：self.tokenizer.encode, self._FALLBACK_PATTERN.findall。
        """
        if not text:
            return []
        if self.tokenizer is not None:
            return self.tokenizer.encode(text, add_special_tokens=False)
        return self._FALLBACK_PATTERN.findall(text)

    # 阅读注释（函数）：处理 count 相关逻辑。
    def count(self, text: str) -> int:
        """处理 count 相关逻辑。

        参数:
            text: 待处理文本。

        返回:
            int

        阅读提示:
            主要直接调用：len, self.tokenize。
        """
        return len(self.tokenize(text))

    # 阅读注释（函数）：处理 后端实现 相关逻辑。
    @property
    def backend(self) -> str:
        """处理 后端实现 相关逻辑。

        返回:
            str
        """
        if self.tokenizer is not None:
            return "huggingface"
        return "fallback_regex"


# 阅读注释（函数）：获取 Token counter。
@lru_cache(maxsize=8)
def get_token_counter(
    tokenizer_name: Optional[str] = None,
    local_files_only: bool = True,
) -> TokenCounter:
    """获取 Token counter。

    参数:
        tokenizer_name: tokenizer 名称，具体约束请结合类型标注和调用方确认。
        local_files_only: 本地 files only，具体约束请结合类型标注和调用方确认。

    返回:
        TokenCounter

    阅读提示:
        主要直接调用：TokenCounter, lru_cache。
    """
    return TokenCounter(tokenizer_name=tokenizer_name, local_files_only=local_files_only)


# 阅读注释（函数）：获取 default Token counter。
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
