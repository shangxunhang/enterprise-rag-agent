# =============================================================================
# 中文阅读说明：上下文管理模块，用于组织证据、历史状态和 Token 预算。
# 主要定义：DeterministicTokenEstimator。建议先从公开入口函数开始，再沿调用关系向下阅读。
# =============================================================================
"""Tokenizer-independent deterministic planning estimator.

This estimator is intentionally conservative and stable. It is not a substitute
for the model tokenizer; actual usage is still recorded by ModelGateway.
"""

from __future__ import annotations

import re


_CJK_RE = re.compile(r"[\u3400-\u4dbf\u4e00-\u9fff\uf900-\ufaff]")
_ASCII_WORD_RE = re.compile(r"[A-Za-z0-9_]+")


# 阅读注释（类）：封装 deterministic Token estimator，集中封装相关状态、依赖和行为。
class DeterministicTokenEstimator:
    """封装 deterministic Token estimator，集中封装相关状态、依赖和行为。"""
    # 阅读注释（函数）：处理 estimate 相关逻辑。
    @staticmethod
    def estimate(text: str) -> int:
        """处理 estimate 相关逻辑。

        参数:
            text: 待处理文本。

        返回:
            int

        阅读提示:
            主要直接调用：len, _CJK_RE.findall, sum, max, _ASCII_WORD_RE.findall。
        """
        if not text:
            return 0
        cjk = len(_CJK_RE.findall(text))
        ascii_words = sum(max(1, (len(item) + 3) // 4) for item in _ASCII_WORD_RE.findall(text))
        accounted_ascii_chars = sum(len(item) for item in _ASCII_WORD_RE.findall(text))
        remaining = max(0, len(text) - cjk - accounted_ascii_chars)
        punctuation_and_space = (remaining + 1) // 2
        return max(1, cjk + ascii_words + punctuation_and_space)
