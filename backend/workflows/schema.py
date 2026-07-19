# =============================================================================
# 中文阅读说明：后端业务模块。
# 主要定义：以常量、Schema 导入或注册配置为主。建议先从公开入口函数开始，再沿调用关系向下阅读。
# =============================================================================
"""Compatibility import for the active enterprise-document workflow."""

from apps.enterprise_document.workflows.scheme_generation import (
    build_scheme_generation_workflow,
)

__all__ = ["build_scheme_generation_workflow"]
