# =============================================================================
# 中文阅读说明：后端业务模块。
# 主要定义：Timer、MonotonicTimer、elapsed_ms。建议先从公开入口函数开始，再沿调用关系向下阅读。
# =============================================================================
"""Monotonic timing abstraction for latency measurements."""
from __future__ import annotations

import time
from typing import Protocol


# 阅读注释（类）：封装 timer，集中封装相关状态、依赖和行为。
class Timer(Protocol):
    """封装 timer，集中封装相关状态、依赖和行为。"""
    # 阅读注释（函数）：处理 now 相关逻辑。
    def now(self) -> float:
        """Return a monotonic timestamp in seconds."""


# 阅读注释（类）：封装 monotonic timer，集中封装相关状态、依赖和行为。
class MonotonicTimer:
    """封装 monotonic timer，集中封装相关状态、依赖和行为。"""
    # 阅读注释（函数）：处理 now 相关逻辑。
    def now(self) -> float:
        """处理 now 相关逻辑。

        返回:
            float

        阅读提示:
            主要直接调用：time.perf_counter。
        """
        return time.perf_counter()


# 阅读注释（函数）：处理 elapsed ms 相关逻辑。
def elapsed_ms(timer: Timer, started_at: float) -> int:
    """处理 elapsed ms 相关逻辑。

    参数:
        timer: timer，具体约束请结合类型标注和调用方确认。
        started_at: started at，具体约束请结合类型标注和调用方确认。

    返回:
        int

    阅读提示:
        主要直接调用：int, max, timer.now。
    """
    return int(max(0.0, timer.now() - started_at) * 1000)
