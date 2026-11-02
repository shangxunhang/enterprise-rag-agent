"""提供基于单调时钟的运行耗时测量抽象。

延迟统计必须使用不会因系统校时而倒退的单调时钟，因此本模块与生成业务时间戳的
``core.runtime.clock`` 分离。测试可注入自定义 ``Timer``，稳定控制开始和结束值。
"""

from __future__ import annotations

import time
from typing import Protocol


class Timer(Protocol):
    """声明返回单调秒数时间点的最小接口。"""

    def now(self) -> float:
        """返回只适合计算时间差、不适合持久化展示的单调时间点。"""

        ...


class MonotonicTimer:
    """使用高分辨率性能计数器实现单调计时。"""

    def now(self) -> float:
        """读取当前高分辨率单调时间点，单位为秒。"""

        # perf_counter 适合测量短期耗时，不受系统时钟手工调整影响。
        return time.perf_counter()


def elapsed_ms(timer: Timer, started_at: float) -> int:
    """计算从给定起点到当前时间的非负整数毫秒耗时。

    Args:
        timer: 提供当前单调时间点的计时器。
        started_at: 由同一个计时器先前生成的开始时间点。

    Returns:
        向下取整后的非负毫秒数。
    """

    # 第一步：计算秒数差，并将异常倒退值钳制为零，避免产生负延迟指标。
    elapsed_seconds = max(0.0, timer.now() - started_at)

    # 第二步：转换为整数毫秒，保持日志和指标 Schema 的稳定整数类型。
    return int(elapsed_seconds * 1000)
