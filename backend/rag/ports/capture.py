# =============================================================================
# 中文阅读说明：RAG 核心模块，负责查询变换、召回、融合、重排、证据评估和上下文组装。
# 主要定义：RAGRunCapturePort。建议先从公开入口函数开始，再沿调用关系向下阅读。
# =============================================================================
"""RAG run-capture port."""
from __future__ import annotations

from typing import Any, Dict, Protocol


# 阅读注释（类）：封装 ragrun capture port，定义模块间调用契约，具体实现由适配器或插件提供。
class RAGRunCapturePort(Protocol):
    """封装 ragrun capture port，定义模块间调用契约，具体实现由适配器或插件提供。"""
    # 阅读注释（函数）：记录并沉淀 RAGRunCapturePort。
    def capture(self, record: Dict[str, Any]) -> Dict[str, Any]: ...
