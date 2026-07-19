# =============================================================================
# 中文阅读说明：离线评测模块，用于执行实验、评分、对比和报告生成。
# 主要定义：以常量、Schema 导入或注册配置为主。建议先从公开入口函数开始，再沿调用关系向下阅读。
# =============================================================================
"""Compatibility exports for the runtime document gate.

The canonical implementation lives in the enterprise-document application
module.  Offline evaluation consumes it; runtime code no longer depends on the
``eval`` package.
"""
from apps.enterprise_document.services.document_gate import (
    evaluate_scheme_draft,
    extract_runtime_hard_failures,
)

__all__ = ["evaluate_scheme_draft", "extract_runtime_hard_failures"]
