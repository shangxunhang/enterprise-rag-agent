# =============================================================================
# 中文阅读说明：后端业务模块。
# 主要定义：以常量、Schema 导入或注册配置为主。建议先从公开入口函数开始，再沿调用关系向下阅读。
# =============================================================================
"""Runtime primitives shared across application modules."""

from .clock import Clock, SystemClock
from .ids import IdGenerator, UuidIdGenerator

__all__ = ["Clock", "SystemClock", "IdGenerator", "UuidIdGenerator"]
