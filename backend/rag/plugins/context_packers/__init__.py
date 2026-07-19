# =============================================================================
# 中文阅读说明：RAG 核心模块，负责查询变换、召回、融合、重排、证据评估和上下文组装。
# 主要定义：以常量、Schema 导入或注册配置为主。建议先从公开入口函数开始，再沿调用关系向下阅读。
# =============================================================================
from rag.plugins.context_packers.plugin import (
    DefaultContextPacker,
    LostInMiddleContextPacker,
)

__all__ = ["DefaultContextPacker", "LostInMiddleContextPacker"]
