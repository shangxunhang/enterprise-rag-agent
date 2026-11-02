"""核心运行时抽象包。

该包提供可注入时钟和标识生成器等基础原语，使测试能够替换时间与随机标识；更具体的
执行控制、计时器和框架适配仍从各自子模块显式导入。
"""

from .clock import Clock, SystemClock
from .ids import IdGenerator, UuidIdGenerator

__all__ = ["Clock", "SystemClock", "IdGenerator", "UuidIdGenerator"]
