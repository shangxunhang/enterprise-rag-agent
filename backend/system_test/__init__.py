# =============================================================================
# 中文阅读说明：系统级验收与审计模块，用于验证完整运行闭环。
# 主要定义：以常量、Schema 导入或注册配置为主。建议先从公开入口函数开始，再沿调用关系向下阅读。
# =============================================================================
"""System-level acceptance helpers."""

from .mainline_closure import run_fake_mainline_scenario
from .mainline_audit import audit_mainline

__all__ = ["run_fake_mainline_scenario", "audit_mainline"]
