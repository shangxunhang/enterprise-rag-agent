"""提供不依赖具体模型 tokenizer 的确定性输入 Token 规划估算。

Context Manager 在模型加载前就需要排序和裁剪候选上下文，因此这里使用固定、可复现的
混合文本启发式，而不依赖供应商 tokenizer。该结果只用于预算规划；模型网关记录的实际
Token 用量才是运行事实源。
"""

from __future__ import annotations

import re


_CJK_RE = re.compile(r"[\u3400-\u4dbf\u4e00-\u9fff\uf900-\ufaff]")
_ASCII_WORD_RE = re.compile(r"[A-Za-z0-9_]+")


class DeterministicTokenEstimator:
    """按中日韩字符、ASCII 词段和剩余标点空白稳定估算 Token 数。"""

    @staticmethod
    def estimate(text: str) -> int:
        """返回非负 Token 估计值；非空文本至少计为一个 Token。

        中文字符按一比一保守计数；ASCII 字母数字词段按每四字符约一个 Token 向上取整；
        其余标点和空白按每两个字符约一个 Token 计数。算法不追求复刻特定 tokenizer，
        只保证同一文本始终得到同一规划结果。
        """
        if not text:
            return 0
        cjk = len(_CJK_RE.findall(text))
        ascii_words = sum(
            max(1, (len(item) + 3) // 4)
            for item in _ASCII_WORD_RE.findall(text)
        )
        accounted_ascii_chars = sum(
            len(item) for item in _ASCII_WORD_RE.findall(text)
        )
        remaining = max(0, len(text) - cjk - accounted_ascii_chars)
        punctuation_and_space = (remaining + 1) // 2
        return max(1, cjk + ascii_words + punctuation_and_space)
