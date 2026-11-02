"""离线索引构建配置、Manifest、激活解析和生命周期管理入口。"""

from rag.offline.config import OfflineIndexBuildConfig, OfflineIndexConfigLoader
from rag.offline.lifecycle import (
    IndexActivationError,
    IndexLifecycleError,
    IndexLifecycleEvent,
    IndexLifecycleLockTimeout,
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

# 包级 API 覆盖离线索引从配置、构建产物到激活/回滚的完整控制面。
__all__ = [
    "OfflineIndexBuildConfig",
    "OfflineIndexConfigLoader",
    "IndexManifest",
    "ActiveIndexPointer",
    "ActiveIndexResolver",
    "IndexLifecycleManager",
    "IndexLifecycleError",
    "IndexLifecycleLockTimeout",
    "IndexActivationError",
    "IndexRollbackError",
    "IndexNotFoundError",
    "IndexRegistry",
    "IndexRegistryEntry",
    "IndexLifecycleEvent",
    "IndexLifecycleResult",
    "IndexLifecycleStatus",
]
