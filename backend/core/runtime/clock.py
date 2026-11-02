"""提供可替换的墙上时钟抽象。

运行时代码通过 ``Clock`` 获取可持久化时间戳，而不是直接调用 ``datetime.now``。
测试可注入固定时钟，从而稳定验证状态迁移、轨迹顺序和超时边界。耗时测量不使用
本模块，而使用基于单调时钟的 ``core.runtime.timing``。
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Protocol


class Clock(Protocol):
    """声明生成当前 UTC 时间字符串的最小接口。"""

    def now_iso(self) -> str:
        """返回带时区信息的当前 UTC ISO-8601 时间字符串。"""

        ...


class SystemClock:
    """使用操作系统当前时间实现 ``Clock`` 协议。"""

    def now_iso(self) -> str:
        """读取当前 UTC 时间并编码为 ISO-8601 字符串。

        Returns:
            例如 ``2026-07-27T08:00:00+00:00`` 的带时区字符串。
        """

        # 明确指定 UTC，避免无时区 datetime 在跨机器持久化后产生歧义。
        return datetime.now(timezone.utc).isoformat()
