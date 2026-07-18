"""Tokenizer-independent deterministic planning estimator.

This estimator is intentionally conservative and stable. It is not a substitute
for the model tokenizer; actual usage is still recorded by ModelGateway.
"""

from __future__ import annotations

import re


_CJK_RE = re.compile(r"[\u3400-\u4dbf\u4e00-\u9fff\uf900-\ufaff]")
_ASCII_WORD_RE = re.compile(r"[A-Za-z0-9_]+")


class DeterministicTokenEstimator:
    @staticmethod
    def estimate(text: str) -> int:
        if not text:
            return 0
        cjk = len(_CJK_RE.findall(text))
        ascii_words = sum(max(1, (len(item) + 3) // 4) for item in _ASCII_WORD_RE.findall(text))
        accounted_ascii_chars = sum(len(item) for item in _ASCII_WORD_RE.findall(text))
        remaining = max(0, len(text) - cjk - accounted_ascii_chars)
        punctuation_and_space = (remaining + 1) // 2
        return max(1, cjk + ascii_words + punctuation_and_space)
