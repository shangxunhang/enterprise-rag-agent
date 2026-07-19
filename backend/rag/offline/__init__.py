# =============================================================================
# 中文阅读说明：RAG 核心模块，负责查询变换、召回、融合、重排、证据评估和上下文组装。
# 主要定义：以常量、Schema 导入或注册配置为主。建议先从公开入口函数开始，再沿调用关系向下阅读。
# =============================================================================
from rag.offline.config import OfflineIndexBuildConfig, OfflineIndexConfigLoader
from rag.offline.lifecycle import (
    IndexActivationError,
    IndexLifecycleError,
    IndexLifecycleEvent,
    IndexLifecycleManager,
    IndexLifecycleResult,
    IndexLifecycleStatus,
    IndexNotFoundError,
    IndexRegistry,
    IndexRegistryEntry,
    IndexRollbackError,
)
from rag.offline.manifest import ActiveIndexPointer, IndexManifest
from rag.offline.resolver import ActiveIndexResolver

__all__ = [
    "OfflineIndexBuildConfig",
    "OfflineIndexConfigLoader",
    "IndexManifest",
    "ActiveIndexPointer",
    "ActiveIndexResolver",
    "IndexLifecycleManager",
    "IndexLifecycleError",
    "IndexActivationError",
    "IndexRollbackError",
    "IndexNotFoundError",
    "IndexRegistry",
    "IndexRegistryEntry",
    "IndexLifecycleEvent",
    "IndexLifecycleResult",
    "IndexLifecycleStatus",
]
