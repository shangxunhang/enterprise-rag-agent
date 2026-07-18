"""Output validation utilities for section generation."""

from __future__ import annotations

import json
import re
from typing import Optional

from apps.enterprise_document.schemas.scheme_writer_schema import TruncationCheckSchema


_TERMINAL_PUNCTUATION = ("。", "！", "？", ".", "!", "?", "；", ";", "：", ":", ")", "）", "]", "】", "}")


def _json_closed(text: str) -> Optional[bool]:
    stripped = text.strip()
    if not stripped or stripped[0] not in "[{":
        return None
    try:
        json.loads(stripped)
        return True
    except Exception:
        return False


def detect_truncation(
    text: str,
    finish_reason: Optional[str],
    min_chars: int = 0,
) -> TruncationCheckSchema:
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
