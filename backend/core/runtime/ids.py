"""提供可替换的运行时标识生成策略。

业务代码通过 ``IdGenerator`` 创建 task、run、trace 等带前缀标识。默认实现使用
随机 UUID；兼容实现额外保留历史时间戳格式。抽象生成器后，测试可以注入确定性
实现，不必依赖真实随机数和当前时间。
"""

from __future__ import annotations

from datetime import datetime
import uuid
from typing import Protocol

from core.runtime.clock import Clock, SystemClock


class IdGenerator(Protocol):
    """声明创建带业务前缀唯一标识的最小接口。"""

    def new_id(self, prefix: str) -> str:
        """根据给定前缀创建新的标识字符串。

        Args:
            prefix: 表明标识用途的稳定前缀，例如 ``task`` 或 ``run``。

        Returns:
            包含前缀且在正常使用范围内唯一的字符串。
        """

        ...


class UuidIdGenerator:
    """使用 UUID4 随机值生成简短前缀标识。"""

    def new_id(self, prefix: str) -> str:
        """生成 ``<prefix>_<12位十六进制>`` 格式的标识。

        Args:
            prefix: 业务用途前缀。

        Returns:
            由前缀和 UUID4 前 12 位十六进制组成的标识。
        """

        # 第一步：生成随机 UUID4，并截取足以满足项目运行规模的 12 位十六进制。
        random_suffix = uuid.uuid4().hex[:12]

        # 第二步：保留业务前缀，方便日志检索和人工识别标识类型。
        return f"{prefix}_{random_suffix}"


class TimestampedUuidIdGenerator:
    """生成历史兼容的 ``prefix_YYYYmmddTHHMMSSZ_<8hex>`` 标识。

    Attributes:
        clock: 提供 UTC 时间字符串的可替换时钟。
    """

    def __init__(self, clock: Clock | None = None) -> None:
        """创建时间戳 UUID 生成器。

        Args:
            clock: 自定义时钟；未提供时使用系统 UTC 时钟。
        """

        # 使用可注入时钟，使测试能够固定时间戳部分。
        self.clock = clock or SystemClock()

    def new_id(self, prefix: str) -> str:
        """生成带 UTC 秒级时间戳和随机后缀的兼容标识。

        Args:
            prefix: 业务用途前缀。

        Returns:
            历史格式标识，例如 ``run_20260727T080000Z_a1b2c3d4``。
        """

        # 第一步：读取统一时钟的 ISO 时间并解析为 datetime。
        current_time = datetime.fromisoformat(self.clock.now_iso())

        # 第二步：格式化为历史接口要求的紧凑 UTC 时间戳部分。
        timestamp = current_time.strftime("%Y%m%dT%H%M%SZ")

        # 第三步：追加 UUID4 的 8 位随机后缀，区分同一秒内创建的多个对象。
        random_suffix = uuid.uuid4().hex[:8]

        # 第四步：组合业务前缀、时间戳和随机后缀。
        return f"{prefix}_{timestamp}_{random_suffix}"
