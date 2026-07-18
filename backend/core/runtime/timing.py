"""Monotonic timing abstraction for latency measurements."""
from __future__ import annotations

import time
from typing import Protocol


class Timer(Protocol):
    def now(self) -> float:
        """Return a monotonic timestamp in seconds."""


class MonotonicTimer:
    def now(self) -> float:
        return time.perf_counter()


def elapsed_ms(timer: Timer, started_at: float) -> int:
    return int(max(0.0, timer.now() - started_at) * 1000)
