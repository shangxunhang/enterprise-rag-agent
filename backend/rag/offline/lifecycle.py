# =============================================================================
# 中文阅读说明：RAG 核心模块，负责查询变换、召回、融合、重排、证据评估和上下文组装。
# 主要定义：IndexLifecycleError、IndexNotFoundError、IndexActivationError、IndexRollbackError、IndexLifecycleLockTimeout、IndexRegistryEntry、IndexRegistry、IndexLifecycleEvent、IndexLifecycleResult、IndexLifecycleStatus等。建议先从公开入口函数开始，再沿调用关系向下阅读。
# =============================================================================
"""Controlled lifecycle management for immutable offline RAG indexes.

The index artifacts and manifest are immutable. Only the small active pointer is
mutable. This module owns discovery, registration, pre-activation verification,
atomic pointer replacement, audit history, and rollback.
"""
from __future__ import annotations

import json
import os
import time
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Iterator, Literal

from pydantic import BaseModel, ConfigDict, Field

from rag.offline.manifest import (
    ActiveIndexPointer,
    IndexManifest,
    atomic_write_text,
    sha256_file,
)
from rag.offline.resolver import ActiveIndexResolver
from rag.offline.verification import OfflineIndexVerifier


# 阅读注释（类）：封装 索引 lifecycle 错误，集中封装相关状态、依赖和行为。
class IndexLifecycleError(RuntimeError):
    """Base lifecycle failure."""


# 阅读注释（类）：封装 索引 not found 错误，集中封装相关状态、依赖和行为。
class IndexNotFoundError(IndexLifecycleError):
    """Requested index version is not registered or discoverable."""


# 阅读注释（类）：封装 索引 activation 错误，集中封装相关状态、依赖和行为。
class IndexActivationError(IndexLifecycleError):
    """Index failed activation policy or verification."""


# 阅读注释（类）：封装 索引 rollback 错误，集中封装相关状态、依赖和行为。
class IndexRollbackError(IndexLifecycleError):
    """No safe rollback target is available."""


# 阅读注释（类）：封装 索引 lifecycle lock timeout，集中封装相关状态、依赖和行为。
class IndexLifecycleLockTimeout(IndexLifecycleError):
    """Another lifecycle operation holds the activation lock."""


# 阅读注释（类）：封装 索引 注册表 entry，集中封装相关状态、依赖和行为。
class IndexRegistryEntry(BaseModel):
    """封装 索引 注册表 entry，集中封装相关状态、依赖和行为。"""
    model_config = ConfigDict(extra="forbid")

    index_version: str
    manifest_path: str
    manifest_sha256: str
    dataset_version: str
    backend: str
    collection_name: str | None = None
    embedding_model: str | None = None
    embedding_version: str | None = None
    embedding_dim: int | None = None
    document_count: int
    parent_chunk_count: int
    child_chunk_count: int
    created_at: str
    registered_at: str


# 阅读注释（类）：封装 索引 注册表，集中封装相关状态、依赖和行为。
class IndexRegistry(BaseModel):
    """封装 索引 注册表，集中封装相关状态、依赖和行为。"""
    model_config = ConfigDict(extra="forbid")

    schema_version: Literal["rag_index_registry_v1"] = "rag_index_registry_v1"
    updated_at: str
    indexes: dict[str, IndexRegistryEntry] = Field(default_factory=dict)


# 阅读注释（类）：封装 索引 lifecycle 事件，集中封装相关状态、依赖和行为。
class IndexLifecycleEvent(BaseModel):
    """封装 索引 lifecycle 事件，集中封装相关状态、依赖和行为。"""
    model_config = ConfigDict(extra="forbid")

    schema_version: Literal["rag_index_lifecycle_event_v1"] = (
        "rag_index_lifecycle_event_v1"
    )
    event_id: str
    timestamp: str
    operation: Literal["activate", "rollback"]
    actor: str
    reason: str | None = None
    from_index_version: str | None = None
    to_index_version: str
    pointer_path: str
    previous_pointer: dict[str, Any] | None = None
    active_pointer: dict[str, Any]
    verification_status: str
    verification_metrics: dict[str, Any] = Field(default_factory=dict)


# 阅读注释（类）：封装 索引 lifecycle 结果，集中封装相关状态、依赖和行为。
class IndexLifecycleResult(BaseModel):
    """封装 索引 lifecycle 结果，集中封装相关状态、依赖和行为。"""
    model_config = ConfigDict(extra="forbid")

    status: Literal["success"] = "success"
    operation: Literal["activate", "rollback"]
    active_index_version: str
    previous_index_version: str | None = None
    pointer_path: str
    manifest_path: str
    verification_status: str
    verification_metrics: dict[str, Any] = Field(default_factory=dict)
    event_id: str


# 阅读注释（类）：封装 索引 lifecycle 状态，集中封装相关状态、依赖和行为。
class IndexLifecycleStatus(BaseModel):
    """封装 索引 lifecycle 状态，集中封装相关状态、依赖和行为。"""
    model_config = ConfigDict(extra="forbid")

    schema_version: Literal["rag_index_lifecycle_status_v1"] = (
        "rag_index_lifecycle_status_v1"
    )
    pointer_path: str
    active_index_version: str | None = None
    active_manifest_path: str | None = None
    active_manifest_sha256: str | None = None
    registered_index_count: int
    history_event_count: int


# 阅读注释（函数）：处理 now iso 相关逻辑。
def _now_iso() -> str:
    """处理 now iso 相关逻辑。

    返回:
        str

    阅读提示:
        主要直接调用：isoformat, datetime.now。
    """
    return datetime.now(timezone.utc).isoformat()


# 阅读注释（函数）：处理 JSON 文本 相关逻辑。
def _json_text(payload: Any) -> str:
    """处理 JSON 文本 相关逻辑。

    参数:
        payload: 跨层传递的数据载荷。

    返回:
        str

    阅读提示:
        主要直接调用：json.dumps。
    """
    return json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True)


# 阅读注释（类）：封装 索引 lifecycle 管理器，集中封装相关状态、依赖和行为。
class IndexLifecycleManager:
    """Manage the active pointer without mutating immutable index artifacts."""

    # 阅读注释（函数）：初始化 IndexLifecycleManager，保存运行所需的依赖、配置或状态。
    def __init__(
        self,
        *,
        project_root: str | Path,
        indexes_root: str | Path = "data/processed/indexes",
        pointer_path: str | Path = "data/processed/indexes/active_index.json",
        registry_path: str | Path = "data/processed/indexes/index_registry.json",
        history_path: str | Path = "data/processed/indexes/index_lifecycle_history.jsonl",
        lock_path: str | Path = "data/processed/indexes/.index_lifecycle.lock",
        verifier_factory: Callable[[], Any] | None = None,
        lock_timeout_seconds: float = 15.0,
        stale_lock_seconds: float = 3600.0,
    ) -> None:
        """初始化 IndexLifecycleManager，保存运行所需的依赖、配置或状态。

        参数:
            project_root: 项目 root，具体约束请结合类型标注和调用方确认。
            indexes_root: indexes root，具体约束请结合类型标注和调用方确认。
            pointer_path: pointer 路径，具体约束请结合类型标注和调用方确认。
            registry_path: 注册表 路径，具体约束请结合类型标注和调用方确认。
            history_path: history 路径，具体约束请结合类型标注和调用方确认。
            lock_path: lock 路径，具体约束请结合类型标注和调用方确认。
            verifier_factory: verifier 工厂，具体约束请结合类型标注和调用方确认。
            lock_timeout_seconds: lock timeout seconds，具体约束请结合类型标注和调用方确认。
            stale_lock_seconds: stale lock seconds，具体约束请结合类型标注和调用方确认。

        返回:
            None

        阅读提示:
            主要直接调用：resolve, expanduser, Path, self._resolve, max, float。
        """
        self.project_root = Path(project_root).expanduser().resolve()
        self.indexes_root = self._resolve(indexes_root)
        self.pointer_path = self._resolve(pointer_path)
        self.registry_path = self._resolve(registry_path)
        self.history_path = self._resolve(history_path)
        self.lock_path = self._resolve(lock_path)
        self.verifier_factory = verifier_factory or OfflineIndexVerifier
        self.lock_timeout_seconds = max(0.1, float(lock_timeout_seconds))
        self.stale_lock_seconds = max(60.0, float(stale_lock_seconds))

    # 阅读注释（函数）：解析并确定 IndexLifecycleManager。
    def _resolve(self, path: str | Path) -> Path:
        """解析并确定 IndexLifecycleManager。

        参数:
            path: 目标文件或目录路径。

        返回:
            Path

        阅读提示:
            主要直接调用：expanduser, Path, candidate.is_absolute, candidate.resolve, resolve。
        """
        candidate = Path(path).expanduser()
        return candidate.resolve() if candidate.is_absolute() else (
            self.project_root / candidate
        ).resolve()

    # 阅读注释（函数）：处理 exclusive lock 相关逻辑。
    @contextmanager
    def _exclusive_lock(self) -> Iterator[None]:
        """处理 exclusive lock 相关逻辑。

        返回:
            Iterator[None]

        阅读提示:
            主要直接调用：self.lock_path.parent.mkdir, time.monotonic, os.open, time.time, self.lock_path.stat, self.lock_path.unlink, IndexLifecycleLockTimeout, time.sleep。
        """
        self.lock_path.parent.mkdir(parents=True, exist_ok=True)
        deadline = time.monotonic() + self.lock_timeout_seconds
        descriptor: int | None = None
        while descriptor is None:
            try:
                descriptor = os.open(
                    self.lock_path,
                    os.O_CREAT | os.O_EXCL | os.O_WRONLY,
                )
            except FileExistsError:
                try:
                    lock_age = time.time() - self.lock_path.stat().st_mtime
                except FileNotFoundError:
                    continue
                if lock_age > self.stale_lock_seconds:
                    try:
                        self.lock_path.unlink()
                    except FileNotFoundError:
                        pass
                    continue
                if time.monotonic() >= deadline:
                    raise IndexLifecycleLockTimeout(
                        f"index lifecycle lock timeout: {self.lock_path}"
                    )
                time.sleep(0.05)
        try:
            payload = {
                "pid": os.getpid(),
                "created_at": _now_iso(),
            }
            os.write(descriptor, _json_text(payload).encode("utf-8"))
            os.fsync(descriptor)
            yield
        finally:
            if descriptor is not None:
                os.close(descriptor)
            try:
                self.lock_path.unlink()
            except FileNotFoundError:
                pass

    # 阅读注释（函数）：判断 archived manifest。
    @staticmethod
    def _is_archived_manifest(path: Path) -> bool:
        """判断 archived manifest。

        参数:
            path: 目标文件或目录路径。

        返回:
            bool

        阅读提示:
            主要直接调用：any。
        """
        return any(".failed_" in part or ".archived_" in part for part in path.parts)

    # 阅读注释（函数）：处理 entry from manifest 相关逻辑。
    def _entry_from_manifest(self, manifest_path: Path) -> IndexRegistryEntry:
        """处理 entry from manifest 相关逻辑。

        参数:
            manifest_path: manifest 路径，具体约束请结合类型标注和调用方确认。

        返回:
            IndexRegistryEntry

        阅读提示:
            主要直接调用：resolve, manifest_path.expanduser, manifest_file.is_file, FileNotFoundError, IndexManifest.model_validate_json, manifest_file.read_text, IndexRegistryEntry, str。
        """
        manifest_file = manifest_path.expanduser().resolve()
        if not manifest_file.is_file():
            raise FileNotFoundError(f"index manifest not found: {manifest_file}")
        manifest = IndexManifest.model_validate_json(
            manifest_file.read_text(encoding="utf-8")
        )
        return IndexRegistryEntry(
            index_version=manifest.index_version,
            manifest_path=str(manifest_file),
            manifest_sha256=sha256_file(manifest_file),
            dataset_version=manifest.dataset_version,
            backend=str(manifest.index.get("backend") or ""),
            collection_name=(
                str(manifest.index.get("collection_name"))
                if manifest.index.get("collection_name")
                else None
            ),
            embedding_model=(
                str(manifest.embedding.get("model"))
                if manifest.embedding.get("model")
                else None
            ),
            embedding_version=(
                str(manifest.embedding.get("version"))
                if manifest.embedding.get("version")
                else None
            ),
            embedding_dim=(
                int(manifest.embedding.get("dim"))
                if manifest.embedding.get("dim") is not None
                else None
            ),
            document_count=manifest.document_count,
            parent_chunk_count=manifest.parent_chunk_count,
            child_chunk_count=manifest.child_chunk_count,
            created_at=manifest.created_at,
            registered_at=_now_iso(),
        )

    # 阅读注释（函数）：加载 注册表。
    def _load_registry(self) -> IndexRegistry:
        """加载 注册表。

        返回:
            IndexRegistry

        阅读提示:
            主要直接调用：self.registry_path.is_file, IndexRegistry, _now_iso, IndexRegistry.model_validate_json, self.registry_path.read_text。
        """
        if not self.registry_path.is_file():
            return IndexRegistry(updated_at=_now_iso())
        return IndexRegistry.model_validate_json(
            self.registry_path.read_text(encoding="utf-8")
        )

    # 阅读注释（函数）：写入 注册表。
    def _write_registry(self, registry: IndexRegistry) -> None:
        """写入 注册表。

        参数:
            registry: 注册表，具体约束请结合类型标注和调用方确认。

        返回:
            None

        阅读提示:
            主要直接调用：registry.model_copy, _now_iso, atomic_write_text, _json_text, updated.model_dump。
        """
        updated = registry.model_copy(update={"updated_at": _now_iso()})
        atomic_write_text(
            self.registry_path,
            _json_text(updated.model_dump(mode="json")),
        )

    # 阅读注释（函数）：处理 discover 相关逻辑。
    def discover(self, *, persist: bool = True) -> list[IndexRegistryEntry]:
        """处理 discover 相关逻辑。

        参数:
            persist: persist，具体约束请结合类型标注和调用方确认。

        返回:
            list[IndexRegistryEntry]

        阅读提示:
            主要直接调用：self.indexes_root.mkdir, self._load_registry, sorted, self.indexes_root.rglob, self._is_archived_manifest, self._entry_from_manifest, discovered.get, IndexLifecycleError。
        """
        self.indexes_root.mkdir(parents=True, exist_ok=True)
        registry = self._load_registry()
        discovered: dict[str, IndexRegistryEntry] = {}
        for manifest_path in sorted(self.indexes_root.rglob("index_manifest.json")):
            if self._is_archived_manifest(manifest_path):
                continue
            try:
                entry = self._entry_from_manifest(manifest_path)
            except Exception:
                continue
            existing = discovered.get(entry.index_version)
            if existing and existing.manifest_path != entry.manifest_path:
                raise IndexLifecycleError(
                    "duplicate index_version discovered at different manifests: "
                    f"{entry.index_version}"
                )
            discovered[entry.index_version] = entry
        registry.indexes = discovered
        if persist:
            self._write_registry(registry)
        return sorted(registry.indexes.values(), key=lambda item: item.index_version)

    # 阅读注释（函数）：注册 IndexLifecycleManager。
    def register(self, manifest_path: str | Path) -> IndexRegistryEntry:
        """注册 IndexLifecycleManager。

        参数:
            manifest_path: manifest 路径，具体约束请结合类型标注和调用方确认。

        返回:
            IndexRegistryEntry

        阅读提示:
            主要直接调用：self._entry_from_manifest, self._resolve, self._exclusive_lock, self._load_registry, registry.indexes.get, IndexLifecycleError, self._write_registry。
        """
        entry = self._entry_from_manifest(self._resolve(manifest_path))
        with self._exclusive_lock():
            registry = self._load_registry()
            existing = registry.indexes.get(entry.index_version)
            if existing and existing.manifest_path != entry.manifest_path:
                raise IndexLifecycleError(
                    f"index version collision: {entry.index_version}"
                )
            registry.indexes[entry.index_version] = entry
            self._write_registry(registry)
        return entry

    # 阅读注释（函数）：列出 indexes。
    def list_indexes(self, *, refresh: bool = True) -> list[IndexRegistryEntry]:
        """列出 indexes。

        参数:
            refresh: refresh，具体约束请结合类型标注和调用方确认。

        返回:
            list[IndexRegistryEntry]

        阅读提示:
            主要直接调用：self.discover, sorted, indexes.values, self._load_registry。
        """
        if refresh:
            return self.discover(persist=True)
        return sorted(
            self._load_registry().indexes.values(),
            key=lambda item: item.index_version,
        )

    # 阅读注释（函数）：查找 entry。
    def _find_entry(self, index_version: str) -> IndexRegistryEntry:
        """查找 entry。

        参数:
            index_version: 索引 版本，具体约束请结合类型标注和调用方确认。

        返回:
            IndexRegistryEntry

        阅读提示:
            主要直接调用：self._load_registry, registry.indexes.get, self.discover, indexes.get, IndexNotFoundError。
        """
        registry = self._load_registry()
        entry = registry.indexes.get(index_version)
        if entry is None:
            self.discover(persist=True)
            entry = self._load_registry().indexes.get(index_version)
        if entry is None:
            raise IndexNotFoundError(f"index version not found: {index_version}")
        return entry

    # 阅读注释（函数）：读取 pointer。
    def _read_pointer(self) -> ActiveIndexPointer | None:
        """读取 pointer。

        返回:
            ActiveIndexPointer | None

        阅读提示:
            主要直接调用：self.pointer_path.is_file, ActiveIndexPointer.model_validate_json, self.pointer_path.read_text。
        """
        if not self.pointer_path.is_file():
            return None
        return ActiveIndexPointer.model_validate_json(
            self.pointer_path.read_text(encoding="utf-8")
        )

    # 阅读注释（函数）：验证 entry。
    def _verify_entry(
        self,
        entry: IndexRegistryEntry,
        *,
        verify_artifact_hashes: bool,
        verify_milvus: bool,
        self_retrieval_samples: int,
    ) -> Any:
        """验证 entry。

        参数:
            entry: entry，具体约束请结合类型标注和调用方确认。
            verify_artifact_hashes: verify artifact hashes，具体约束请结合类型标注和调用方确认。
            verify_milvus: verify milvus，具体约束请结合类型标注和调用方确认。
            self_retrieval_samples: Self 检索 samples，具体约束请结合类型标注和调用方确认。

        返回:
            Any

        阅读提示:
            主要直接调用：Path, sha256_file, IndexActivationError, self.verifier_factory, verifier.verify, str, list, get。
        """
        manifest_file = Path(entry.manifest_path)
        current_manifest_hash = sha256_file(manifest_file)
        if current_manifest_hash != entry.manifest_sha256:
            raise IndexActivationError(
                "registered index manifest changed after registration: "
                f"index_version={entry.index_version}"
            )
        if entry.backend != "milvus_lite":
            raise IndexActivationError(
                "only backend=milvus_lite can be activated for online runtime; "
                f"got {entry.backend!r}"
            )
        verifier = self.verifier_factory()
        report = verifier.verify(
            entry.manifest_path,
            verify_artifact_hashes=verify_artifact_hashes,
            verify_milvus=verify_milvus,
            self_retrieval_samples=self_retrieval_samples,
        )
        if str(report.status) != "success":
            failed = list((getattr(report, "metrics", {}) or {}).get("failed_checks", []))
            raise IndexActivationError(
                "index pre-activation verification failed: "
                f"index_version={entry.index_version}, failed_checks={failed}"
            )
        return report

    # 阅读注释（函数）：处理 append history 相关逻辑。
    def _append_history(self, event: IndexLifecycleEvent) -> None:
        """处理 append history 相关逻辑。

        参数:
            event: 事件，具体约束请结合类型标注和调用方确认。

        返回:
            None

        阅读提示:
            主要直接调用：self.history_path.parent.mkdir, json.dumps, event.model_dump, self.history_path.open, handle.write, handle.flush, os.fsync, handle.fileno。
        """
        self.history_path.parent.mkdir(parents=True, exist_ok=True)
        line = json.dumps(event.model_dump(mode="json"), ensure_ascii=False) + "\n"
        with self.history_path.open("a", encoding="utf-8", newline="") as handle:
            handle.write(line)
            handle.flush()
            os.fsync(handle.fileno())

    # 阅读注释（函数）：处理 history 相关逻辑。
    def history(self) -> list[IndexLifecycleEvent]:
        """处理 history 相关逻辑。

        返回:
            list[IndexLifecycleEvent]

        阅读提示:
            主要直接调用：self.history_path.is_file, splitlines, self.history_path.read_text, line.strip, events.append, IndexLifecycleEvent.model_validate_json。
        """
        if not self.history_path.is_file():
            return []
        events: list[IndexLifecycleEvent] = []
        for line in self.history_path.read_text(encoding="utf-8").splitlines():
            if line.strip():
                events.append(IndexLifecycleEvent.model_validate_json(line))
        return events

    # 阅读注释（函数）：处理 activate locked 相关逻辑。
    def _activate_locked(
        self,
        *,
        index_version: str,
        operation: Literal["activate", "rollback"],
        actor: str,
        reason: str | None,
        verify_artifact_hashes: bool,
        verify_milvus: bool,
        self_retrieval_samples: int,
    ) -> IndexLifecycleResult:
        """处理 activate locked 相关逻辑。

        参数:
            index_version: 索引 版本，具体约束请结合类型标注和调用方确认。
            operation: operation，具体约束请结合类型标注和调用方确认。
            actor: actor，具体约束请结合类型标注和调用方确认。
            reason: reason，具体约束请结合类型标注和调用方确认。
            verify_artifact_hashes: verify artifact hashes，具体约束请结合类型标注和调用方确认。
            verify_milvus: verify milvus，具体约束请结合类型标注和调用方确认。
            self_retrieval_samples: Self 检索 samples，具体约束请结合类型标注和调用方确认。

        返回:
            IndexLifecycleResult

        阅读提示:
            主要直接调用：self._find_entry, self._verify_entry, self._read_pointer, ActiveIndexPointer, IndexLifecycleEvent, uuid.uuid4, _now_iso, str。
        """
        entry = self._find_entry(index_version)
        report = self._verify_entry(
            entry,
            verify_artifact_hashes=verify_artifact_hashes,
            verify_milvus=verify_milvus,
            self_retrieval_samples=self_retrieval_samples,
        )
        previous = self._read_pointer()
        pointer = ActiveIndexPointer(
            index_version=entry.index_version,
            manifest_path=entry.manifest_path,
            manifest_sha256=entry.manifest_sha256,
        )
        event = IndexLifecycleEvent(
            event_id=f"idxevt_{uuid.uuid4().hex}",
            timestamp=_now_iso(),
            operation=operation,
            actor=actor,
            reason=reason,
            from_index_version=previous.index_version if previous else None,
            to_index_version=entry.index_version,
            pointer_path=str(self.pointer_path),
            previous_pointer=(previous.model_dump(mode="json") if previous else None),
            active_pointer=pointer.model_dump(mode="json"),
            verification_status=str(report.status),
            verification_metrics=dict(getattr(report, "metrics", {}) or {}),
        )

        try:
            pointer.write(self.pointer_path)

            # Re-read through the online resolver. This validates the exact
            # pointer representation consumed by ParentChildRuntimeFactory.
            resolved = ActiveIndexResolver(
                verify_manifest_hash=True,
                verify_artifacts=False,
            ).resolve(self.pointer_path)
            if resolved["index_version"] != entry.index_version:
                raise IndexActivationError(
                    "post-activation online resolution mismatch"
                )
            self._append_history(event)
        except Exception:
            # Pointer and audit history form one lifecycle transaction. If the
            # new pointer cannot be resolved or audited, restore the previous
            # pointer rather than leaving an untracked online state.
            if previous is None:
                try:
                    self.pointer_path.unlink()
                except FileNotFoundError:
                    pass
            else:
                previous.write(self.pointer_path)
            raise
        return IndexLifecycleResult(
            operation=operation,
            active_index_version=entry.index_version,
            previous_index_version=previous.index_version if previous else None,
            pointer_path=str(self.pointer_path),
            manifest_path=entry.manifest_path,
            verification_status=str(report.status),
            verification_metrics=dict(getattr(report, "metrics", {}) or {}),
            event_id=event.event_id,
        )

    # 阅读注释（函数）：处理 activate 相关逻辑。
    def activate(
        self,
        index_version: str,
        *,
        actor: str = "manual",
        reason: str | None = None,
        verify_artifact_hashes: bool = True,
        verify_milvus: bool = True,
        self_retrieval_samples: int = 3,
    ) -> IndexLifecycleResult:
        """处理 activate 相关逻辑。

        参数:
            index_version: 索引 版本，具体约束请结合类型标注和调用方确认。
            actor: actor，具体约束请结合类型标注和调用方确认。
            reason: reason，具体约束请结合类型标注和调用方确认。
            verify_artifact_hashes: verify artifact hashes，具体约束请结合类型标注和调用方确认。
            verify_milvus: verify milvus，具体约束请结合类型标注和调用方确认。
            self_retrieval_samples: Self 检索 samples，具体约束请结合类型标注和调用方确认。

        返回:
            IndexLifecycleResult

        阅读提示:
            主要直接调用：self._exclusive_lock, self._activate_locked。
        """
        with self._exclusive_lock():
            return self._activate_locked(
                index_version=index_version,
                operation="activate",
                actor=actor,
                reason=reason,
                verify_artifact_hashes=verify_artifact_hashes,
                verify_milvus=verify_milvus,
                self_retrieval_samples=self_retrieval_samples,
            )

    # 阅读注释（函数）：处理 rollback 相关逻辑。
    def rollback(
        self,
        target_index_version: str | None = None,
        *,
        actor: str = "manual",
        reason: str | None = None,
        verify_artifact_hashes: bool = True,
        verify_milvus: bool = True,
        self_retrieval_samples: int = 3,
    ) -> IndexLifecycleResult:
        """处理 rollback 相关逻辑。

        参数:
            target_index_version: target 索引 版本，具体约束请结合类型标注和调用方确认。
            actor: actor，具体约束请结合类型标注和调用方确认。
            reason: reason，具体约束请结合类型标注和调用方确认。
            verify_artifact_hashes: verify artifact hashes，具体约束请结合类型标注和调用方确认。
            verify_milvus: verify milvus，具体约束请结合类型标注和调用方确认。
            self_retrieval_samples: Self 检索 samples，具体约束请结合类型标注和调用方确认。

        返回:
            IndexLifecycleResult

        阅读提示:
            主要直接调用：self._exclusive_lock, self._read_pointer, IndexRollbackError, self.history, next, reversed, str, latest.previous_pointer.get。
        """
        with self._exclusive_lock():
            current = self._read_pointer()
            if current is None:
                raise IndexRollbackError("cannot rollback without an active index")

            target = target_index_version
            if not target:
                events = self.history()
                latest = next(
                    (
                        event
                        for event in reversed(events)
                        if event.to_index_version == current.index_version
                        and event.previous_pointer is not None
                    ),
                    None,
                )
                if latest is None:
                    raise IndexRollbackError(
                        "no previous active index recorded; provide target_index_version"
                    )
                target = str(latest.previous_pointer.get("index_version") or "")
            if not target:
                raise IndexRollbackError("rollback target index version is empty")
            if target == current.index_version:
                raise IndexRollbackError("rollback target is already active")

            return self._activate_locked(
                index_version=target,
                operation="rollback",
                actor=actor,
                reason=reason,
                verify_artifact_hashes=verify_artifact_hashes,
                verify_milvus=verify_milvus,
                self_retrieval_samples=self_retrieval_samples,
            )

    # 阅读注释（函数）：解析并确定 active。
    def resolve_active(self, *, verify_artifacts: bool = True) -> dict[str, Any]:
        """解析并确定 active。

        参数:
            verify_artifacts: verify artifacts，具体约束请结合类型标注和调用方确认。

        返回:
            dict[str, Any]

        阅读提示:
            主要直接调用：resolve, ActiveIndexResolver。
        """
        return ActiveIndexResolver(
            verify_manifest_hash=True,
            verify_artifacts=verify_artifacts,
        ).resolve(self.pointer_path)

    # 阅读注释（函数）：处理 状态 相关逻辑。
    def status(self) -> IndexLifecycleStatus:
        """处理 状态 相关逻辑。

        返回:
            IndexLifecycleStatus

        阅读提示:
            主要直接调用：self._load_registry, self._read_pointer, IndexLifecycleStatus, str, len, self.history。
        """
        registry = self._load_registry()
        pointer = self._read_pointer()
        return IndexLifecycleStatus(
            pointer_path=str(self.pointer_path),
            active_index_version=pointer.index_version if pointer else None,
            active_manifest_path=pointer.manifest_path if pointer else None,
            active_manifest_sha256=pointer.manifest_sha256 if pointer else None,
            registered_index_count=len(registry.indexes),
            history_event_count=len(self.history()),
        )
