"""向离线评测层重新导出运行时文档硬门禁。

硬门禁的唯一实现位于 enterprise-document 业务模块。评测代码只消费该实现，
不能在 ``eval`` 包中复制一套规则，否则运行时验收标准和离线评分会发生漂移。
"""

from apps.enterprise_document.services.document_gate import (
    evaluate_scheme_draft,
    extract_runtime_hard_failures,
)

# 明确公开面，避免调用方依赖本兼容模块中的其他导入细节。
__all__ = ["evaluate_scheme_draft", "extract_runtime_hard_failures"]
