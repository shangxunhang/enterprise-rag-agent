# =============================================================================
# 中文阅读说明：企业文档生成业务模块，负责方案规划、检索、章节生成、引用和验收。
# 主要定义：_json_closed、detect_truncation。建议先从公开入口函数开始，再沿调用关系向下阅读。
# =============================================================================
"""Output validation utilities for section generation."""

from __future__ import annotations

import json
import re
from typing import Optional

from apps.enterprise_document.schemas.scheme_writer_schema import TruncationCheckSchema


_TERMINAL_PUNCTUATION = ("。", "！", "？", ".", "!", "?", "；", ";", "：", ":", ")", "）", "]", "】", "}")


# 阅读注释（函数）：处理 JSON closed 相关逻辑。
def _json_closed(text: str) -> Optional[bool]:
    """处理 JSON closed 相关逻辑。

    参数:
        text: 待处理文本。

    返回:
        Optional[bool]

    阅读提示:
        主要直接调用：text.strip, json.loads。
    """
    stripped = text.strip()
    if not stripped or stripped[0] not in "[{":
        return None
    try:
        json.loads(stripped)
        return True
    except Exception:
        return False


# 阅读注释（函数）：处理 detect truncation 相关逻辑。
def detect_truncation(
    text: str,
    finish_reason: Optional[str],
    min_chars: int = 0,
) -> TruncationCheckSchema:
    """处理 detect truncation 相关逻辑。

    参数:
        text: 待处理文本。
        finish_reason: finish reason，具体约束请结合类型标注和调用方确认。
        min_chars: min chars，具体约束请结合类型标注和调用方确认。

    返回:
        TruncationCheckSchema

    阅读提示:
        主要直接调用：strip, lower, reasons.append, _json_closed, stripped.endswith, re.search, len, TruncationCheckSchema。
    """
    reasons: list[str] = []
    stripped = (text or "").strip()

    normalized_reason = (finish_reason or "").lower()
    if normalized_reason in {"length", "max_tokens", "token_limit"}:
        reasons.append("finish_reason indicates token limit")

    json_closed = _json_closed(stripped)
    if json_closed is False:
        reasons.append("structured output is not closed")

    sentence_complete = True
    if stripped:
        sentence_complete = stripped.endswith(_TERMINAL_PUNCTUATION)
        if not sentence_complete:
            tail = stripped[-40:]
            if re.search(r"[,，、（(\-—]$", tail):
                reasons.append("last sentence appears incomplete")
    else:
        sentence_complete = False
        reasons.append("empty output")

    if len(stripped) < min_chars:
        reasons.append(f"output shorter than minimum {min_chars} chars")

    return TruncationCheckSchema(
        truncated=bool(reasons),
        reasons=reasons,
        finish_reason=finish_reason,
        json_closed=json_closed,
        sentence_complete=sentence_complete,
        output_chars=len(stripped),
    )
