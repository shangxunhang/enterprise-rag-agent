# =============================================================================
# 中文阅读说明：后端业务模块。
# 主要定义：Clock、SystemClock。建议先从公开入口函数开始，再沿调用关系向下阅读。
# =============================================================================
"""Clock abstraction used to make runtime code deterministic in tests."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Protocol


# 阅读注释（类）：封装 clock，集中封装相关状态、依赖和行为。
class Clock(Protocol):
    """封装 clock，集中封装相关状态、依赖和行为。"""
    # 阅读注释（函数）：处理 now iso 相关逻辑。
    def now_iso(self) -> str:
        """Return the current UTC time in ISO-8601 format."""


# 阅读注释（类）：封装 system clock，集中封装相关状态、依赖和行为。
class SystemClock:
    """封装 system clock，集中封装相关状态、依赖和行为。"""
    # 阅读注释（函数）：处理 now iso 相关逻辑。
    def now_iso(self) -> str:
        """处理 now iso 相关逻辑。

        返回:
            str

        阅读提示:
            主要直接调用：isoformat, datetime.now。
        """
        return datetime.now(timezone.utc).isoformat()
