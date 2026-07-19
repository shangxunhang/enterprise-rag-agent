# =============================================================================
# 中文阅读说明：后端业务模块。
# 主要定义：IdGenerator、UuidIdGenerator、TimestampedUuidIdGenerator。建议先从公开入口函数开始，再沿调用关系向下阅读。
# =============================================================================
"""Identifier generation abstraction."""

from __future__ import annotations

import uuid
from typing import Protocol


# 阅读注释（类）：封装 标识 generator，集中封装相关状态、依赖和行为。
class IdGenerator(Protocol):
    """封装 标识 generator，集中封装相关状态、依赖和行为。"""
    # 阅读注释（函数）：处理 new 标识 相关逻辑。
    def new_id(self, prefix: str) -> str:
        """Create a new prefixed identifier."""


# 阅读注释（类）：封装 uuid 标识 generator，集中封装相关状态、依赖和行为。
class UuidIdGenerator:
    """封装 uuid 标识 generator，集中封装相关状态、依赖和行为。"""
    # 阅读注释（函数）：处理 new 标识 相关逻辑。
    def new_id(self, prefix: str) -> str:
        """处理 new 标识 相关逻辑。

        参数:
            prefix: prefix，具体约束请结合类型标注和调用方确认。

        返回:
            str

        阅读提示:
            主要直接调用：uuid.uuid4。
        """
        return f"{prefix}_{uuid.uuid4().hex[:12]}"


# 阅读注释（类）：封装 timestamped uuid 标识 generator，集中封装相关状态、依赖和行为。
class TimestampedUuidIdGenerator:
    """Generate the historical ``prefix_YYYYmmddTHHMMSSZ_<8hex>`` ids."""

    # 阅读注释（函数）：初始化 TimestampedUuidIdGenerator，保存运行所需的依赖、配置或状态。
    def __init__(self, clock=None) -> None:
        """初始化 TimestampedUuidIdGenerator，保存运行所需的依赖、配置或状态。

        参数:
            clock: clock，具体约束请结合类型标注和调用方确认。

        返回:
            None

        阅读提示:
            主要直接调用：SystemClock。
        """
        from core.runtime.clock import SystemClock
        self.clock = clock or SystemClock()

    # 阅读注释（函数）：处理 new 标识 相关逻辑。
    def new_id(self, prefix: str) -> str:
        """处理 new 标识 相关逻辑。

        参数:
            prefix: prefix，具体约束请结合类型标注和调用方确认。

        返回:
            str

        阅读提示:
            主要直接调用：strftime, datetime.fromisoformat, self.clock.now_iso, uuid.uuid4。
        """
        from datetime import datetime
        timestamp = datetime.fromisoformat(self.clock.now_iso()).strftime("%Y%m%dT%H%M%SZ")
        return f"{prefix}_{timestamp}_{uuid.uuid4().hex[:8]}"
