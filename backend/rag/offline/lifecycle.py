"""管理版本化离线索引的发现、注册、验证、激活和回滚。

生命周期管理器是 active pointer 的唯一写入方。激活流程先严格解析 Manifest，再运行
完整索引验证；只有验证成功才把新 pointer 原子替换为 active。若替换后出现异常，会
恢复旧 pointer 或删除首次创建的新 pointer。注册表和事件日志仅用于控制面审计，
Manifest 与 active pointer 仍是在线索引事实源。
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import threading
import time
from contextlib import contextmanager
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from enum import Enum
from functools import wraps
from pathlib import Path
from typing import Any, Callable, Iterable, Iterator

from rag.offline.manifest import (
    ActiveIndexPointer,
    IndexManifest,
    atomic_write_text,
    sha256_file,
)
from rag.offline.resolver import ActiveIndexResolver
from rag.offline.verification import (
    OfflineIndexVerificationResult,
    OfflineIndexVerifier,
)


def _utc_now() -> str:
    """返回带时区的当前 UTC ISO 时间戳。"""

    return datetime.now(timezone.utc).isoformat()


def _canonical_json(payload: Any) -> str:
    """把对象序列化为保留中文、排序键的紧凑 JSON。"""

    return json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )


def _stable_hash(payload: Any) -> str:
    """计算任意 JSON 兼容对象 canonical 表示的 SHA-256。"""

    return hashlib.sha256(
        _canonical_json(payload).encode("utf-8")
    ).hexdigest()


def _resolve_from(base: Path, raw_path: str | Path) -> Path:
    """把绝对或相对路径解析为规范化绝对路径。

    相对路径以调用方提供的 ``base`` 为基准，而不是当前进程工作目录。
    """

    candidate = Path(raw_path).expanduser()
    if candidate.is_absolute():
        return candidate.resolve()
    return (base / candidate).resolve()


class IndexLifecycleError(RuntimeError):
    """离线索引控制面操作的共同异常基类。"""


class IndexActivationError(IndexLifecycleError):
    """目标索引无法通过验证或无法完成 active pointer 切换。"""


class IndexRollbackError(IndexLifecycleError):
    """回滚目标非法、缺失或回滚切换失败。"""


class IndexNotFoundError(IndexLifecycleError):
    """请求的索引版本或 Manifest 无法找到。"""


class IndexLifecycleLockTimeout(IndexLifecycleError):
    """另一个生命周期操作在超时时间内持续持有互斥锁。"""


class IndexLifecycleStatus(str, Enum):
    """注册表中的离线索引生命周期状态。"""

    # REGISTERED 表示已发现并记录，但尚未通过激活前验证。
    REGISTERED = "registered"

    # VERIFIED 表示通过验证，但不一定是当前 active。
    VERIFIED = "verified"

    # ACTIVE 表示当前 active pointer 指向该版本。
    ACTIVE = "active"

    # ROLLED_BACK 表示该版本曾经 active，后被回滚切走。
    ROLLED_BACK = "rolled_back"

    # FAILED 表示最近一次验证或切换失败。
    FAILED = "failed"


@dataclass(frozen=True)
class IndexRegistryEntry:
    """描述注册表中一个索引版本的控制面状态。

    Attributes:
        index_version: Manifest 内稳定索引版本。
        manifest_path: Manifest 绝对路径。
        manifest_sha256: Manifest 当前内容哈希。
        status: 当前生命周期状态字符串。
        dataset_version: 数据集逻辑版本。
        config_hash: 构建配置哈希。
        reproducibility_hash: Manifest 综合可重复性哈希。
        registered_at: 首次进入注册表的时间。
        updated_at: 最近状态更新时间。
        last_verified_at: 最近成功或失败验证时间。
        last_error: 最近失败的结构化错误摘要。
        metadata: 控制面扩展信息。
    """

    index_version: str
    manifest_path: str
    manifest_sha256: str
    status: str
    dataset_version: str
    config_hash: str
    reproducibility_hash: str
    registered_at: str
    updated_at: str
    last_verified_at: str | None = None
    last_error: dict[str, Any] | None = None
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        """返回 JSON 兼容 dataclass 字典。"""

        return asdict(self)

    def model_dump(self, **_: Any) -> dict[str, Any]:
        """兼容历史 Pydantic 公共契约。"""

        return self.to_dict()

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> "IndexRegistryEntry":
        """从注册表 JSON 对象恢复条目。"""

        return cls(
            index_version=str(payload["index_version"]),
            manifest_path=str(payload["manifest_path"]),
            manifest_sha256=str(payload["manifest_sha256"]),
            status=str(payload["status"]),
            dataset_version=str(payload["dataset_version"]),
            config_hash=str(payload["config_hash"]),
            reproducibility_hash=str(
                payload["reproducibility_hash"]
            ),
            registered_at=str(payload["registered_at"]),
            updated_at=str(payload["updated_at"]),
            last_verified_at=(
                str(payload["last_verified_at"])
                if payload.get("last_verified_at")
                else None
            ),
            last_error=(
                dict(payload["last_error"])
                if isinstance(payload.get("last_error"), dict)
                else None
            ),
            metadata=dict(payload.get("metadata") or {}),
        )


@dataclass(frozen=True)
class IndexLifecycleEvent:
    """记录一次注册、验证、激活、回滚或失败事件。

    Attributes:
        event_id: 由事件业务内容稳定计算的 ID。
        event_type: 操作类型。
        created_at: 事件时间。
        index_version: 当前操作目标版本。
        previous_index_version: 切换前 active 版本。
        status: 操作结果状态。
        manifest_path: 目标 Manifest 路径。
        active_pointer_path: pointer 路径。
        details: 验证摘要、错误、调用参数等诊断信息。
    """

    event_id: str
    event_type: str
    created_at: str
    index_version: str
    previous_index_version: str | None
    status: str
    manifest_path: str
    active_pointer_path: str
    details: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        """返回 JSON 兼容事件字典。"""

        return asdict(self)

    def model_dump(self, **_: Any) -> dict[str, Any]:
        """兼容历史 Pydantic 公共契约。"""

        return self.to_dict()

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> "IndexLifecycleEvent":
        """从历史 JSONL 记录恢复事件。"""

        return cls(
            event_id=str(payload["event_id"]),
            event_type=str(payload["event_type"]),
            created_at=str(payload["created_at"]),
            index_version=str(payload["index_version"]),
            previous_index_version=(
                str(payload["previous_index_version"])
                if payload.get("previous_index_version") is not None
                else None
            ),
            status=str(payload["status"]),
            manifest_path=str(payload["manifest_path"]),
            active_pointer_path=str(payload["active_pointer_path"]),
            details=dict(payload.get("details") or {}),
        )


@dataclass(frozen=True)
class IndexLifecycleResult:
    """返回一次生命周期操作后的最终索引状态。

    Attributes:
        operation: ``activate``、``rollback``、``verify`` 或 ``register``。
        status: 操作结果状态。
        index_version: 操作目标版本。
        previous_index_version: 操作前 active 版本。
        manifest_path: 目标 Manifest。
        active_pointer_path: pointer 路径。
        verification: 可选完整验证报告。
        registry_entry: 操作后注册表条目。
    """

    operation: str
    status: str
    index_version: str
    previous_index_version: str | None
    manifest_path: str
    active_pointer_path: str
    verification: dict[str, Any] | None
    registry_entry: dict[str, Any]
    event_id: str | None = None

    @property
    def active_index_version(self) -> str:
        """兼容历史结果字段。"""

        return self.index_version

    @property
    def pointer_path(self) -> str:
        """兼容历史结果字段。"""

        return self.active_pointer_path

    @property
    def verification_status(self) -> str:
        """兼容历史结果字段。"""

        return str((self.verification or {}).get("status") or "success")

    @property
    def verification_metrics(self) -> dict[str, Any]:
        """兼容历史结果字段。"""

        return dict((self.verification or {}).get("metrics") or {})

    def to_dict(self) -> dict[str, Any]:
        """返回 JSON 兼容结果字典。"""

        return asdict(self)

    def model_dump(self, **_: Any) -> dict[str, Any]:
        """兼容历史 Pydantic 公共契约。"""

        payload = self.to_dict()
        payload.update(
            {
                "active_index_version": self.active_index_version,
                "pointer_path": self.pointer_path,
                "verification_status": self.verification_status,
                "verification_metrics": self.verification_metrics,
            }
        )
        return payload


@dataclass(frozen=True)
class LegacyIndexLifecycleEvent:
    """历史 ``history()`` API 的只读事件投影。"""

    event_id: str
    timestamp: str
    operation: str
    actor: str
    reason: str | None
    from_index_version: str | None
    to_index_version: str
    pointer_path: str
    previous_pointer: dict[str, Any] | None
    active_pointer: dict[str, Any]
    verification_status: str
    verification_metrics: dict[str, Any] = field(default_factory=dict)

    def model_dump(self, **_: Any) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class IndexLifecycleSummary:
    """历史 ``status()`` API 的只读状态投影。"""

    pointer_path: str
    active_index_version: str | None
    active_manifest_path: str | None
    active_manifest_sha256: str | None
    registered_index_count: int
    history_event_count: int
    schema_version: str = "rag_index_lifecycle_status_v1"

    def model_dump(self, **_: Any) -> dict[str, Any]:
        return asdict(self)


class IndexRegistry:
    """以原子 JSON 快照保存索引注册表，以 JSONL 保存不可变事件历史。

    Attributes:
        root_dir: 索引控制面根目录。
        registry_path: 当前注册表快照。
        history_path: 追加式生命周期事件流。
    """

    SCHEMA_VERSION = "rag_index_registry_v1"

    def __init__(
        self,
        root_dir: str | Path,
        *,
        registry_file: str = "index_registry.json",
        history_file: str = "index_lifecycle_history.jsonl",
        registry_path: str | Path | None = None,
        history_path: str | Path | None = None,
    ) -> None:
        """解析控制面路径并确保根目录存在。"""

        self.root_dir = Path(root_dir).expanduser().resolve()
        self.root_dir.mkdir(parents=True, exist_ok=True)
        self.registry_path = (
            _resolve_from(self.root_dir, registry_path)
            if registry_path is not None
            else self.root_dir / registry_file
        )
        self.history_path = (
            _resolve_from(self.root_dir, history_path)
            if history_path is not None
            else self.root_dir / history_file
        )

    def _load_payload(self) -> dict[str, Any]:
        """读取注册表快照，文件不存在时返回空协议。"""

        if not self.registry_path.is_file():
            return {
                "schema_version": self.SCHEMA_VERSION,
                "updated_at": None,
                "indexes": [],
            }

        payload = json.loads(
            self.registry_path.read_text(encoding="utf-8")
        )
        if not isinstance(payload, dict):
            raise IndexLifecycleError(
                f"invalid index registry root: {self.registry_path}"
            )
        if payload.get("schema_version") != self.SCHEMA_VERSION:
            raise IndexLifecycleError(
                "unsupported index registry schema: "
                f"{payload.get('schema_version')!r}"
            )
        indexes = payload.get("indexes")
        if isinstance(indexes, dict):
            migrated: list[dict[str, Any]] = []
            for index_version, legacy_entry in indexes.items():
                if not isinstance(legacy_entry, dict):
                    continue
                manifest_path = str(
                    legacy_entry.get("manifest_path") or ""
                )
                manifest: IndexManifest | None = None
                try:
                    manifest_file = Path(manifest_path).expanduser().resolve()
                    if manifest_file.is_file():
                        manifest = IndexManifest.model_validate_json(
                            manifest_file.read_text(encoding="utf-8")
                        )
                except Exception:
                    manifest = None
                registered_at = str(
                    legacy_entry.get("registered_at")
                    or payload.get("updated_at")
                    or _utc_now()
                )
                migrated.append(
                    {
                        "index_version": str(index_version),
                        "manifest_path": manifest_path,
                        "manifest_sha256": str(
                            legacy_entry.get("manifest_sha256") or ""
                        ),
                        "status": IndexLifecycleStatus.REGISTERED.value,
                        "dataset_version": str(
                            legacy_entry.get("dataset_version")
                            or (
                                manifest.dataset_version
                                if manifest is not None
                                else ""
                            )
                        ),
                        "config_hash": (
                            manifest.config_hash
                            if manifest is not None
                            else str(legacy_entry.get("config_hash") or "")
                        ),
                        "reproducibility_hash": (
                            manifest.reproducibility_hash
                            if manifest is not None
                            else str(
                                legacy_entry.get("reproducibility_hash") or ""
                            )
                        ),
                        "registered_at": registered_at,
                        "updated_at": str(
                            payload.get("updated_at") or registered_at
                        ),
                        "last_verified_at": None,
                        "last_error": None,
                        "metadata": {
                            "migrated_from_legacy_registry": True,
                            "backend": legacy_entry.get("backend"),
                            "collection_name": legacy_entry.get(
                                "collection_name"
                            ),
                            "embedding_model": legacy_entry.get(
                                "embedding_model"
                            ),
                            "embedding_version": legacy_entry.get(
                                "embedding_version"
                            ),
                            "embedding_dim": legacy_entry.get(
                                "embedding_dim"
                            ),
                        },
                    }
                )
            payload = {**payload, "indexes": migrated}
            indexes = migrated
        if not isinstance(indexes, list):
            raise IndexLifecycleError(
                "index registry 'indexes' must be a list or legacy object"
            )
        return payload

    def list_entries(self) -> list[IndexRegistryEntry]:
        """按 index_version 排序返回全部注册条目。"""

        payload = self._load_payload()
        return sorted(
            (
                IndexRegistryEntry.from_dict(item)
                for item in payload["indexes"]
                if isinstance(item, dict)
            ),
            key=lambda item: item.index_version,
        )

    def get(self, index_version: str) -> IndexRegistryEntry | None:
        """按版本查找注册条目，未找到返回 ``None``。"""

        for entry in self.list_entries():
            if entry.index_version == index_version:
                return entry
        return None

    def upsert(self, entry: IndexRegistryEntry) -> IndexRegistryEntry:
        """按 index_version 插入或替换条目并原子发布快照。"""

        # 第一步：读取现有条目并用字典按版本覆盖当前条目。
        entries = {
            item.index_version: item
            for item in self.list_entries()
        }
        entries[entry.index_version] = entry

        # 第二步：按版本排序写出完整快照，保证 diff 和哈希稳定。
        payload = {
            "schema_version": self.SCHEMA_VERSION,
            "updated_at": _utc_now(),
            "indexes": [
                entries[key].to_dict()
                for key in sorted(entries)
            ],
        }
        atomic_write_text(
            self.registry_path,
            json.dumps(payload, ensure_ascii=False, indent=2),
        )
        return entry

    def append_event(self, event: IndexLifecycleEvent) -> Path:
        """追加一条生命周期事件并 fsync。

        历史文件不是注册表事实源，但必须尽量持久化，用于追溯操作顺序和失败原因。
        """

        self.history_path.parent.mkdir(
            parents=True,
            exist_ok=True,
        )
        with self.history_path.open(
            "a",
            encoding="utf-8",
            newline="\n",
        ) as handle:
            handle.write(
                json.dumps(
                    event.to_dict(),
                    ensure_ascii=False,
                    sort_keys=True,
                )
                + "\n"
            )
            handle.flush()
            os.fsync(handle.fileno())
        return self.history_path

    def read_events(self) -> list[IndexLifecycleEvent]:
        """读取完整且可解析的生命周期事件。"""

        if not self.history_path.is_file():
            return []
        events: list[IndexLifecycleEvent] = []
        for raw_line in self.history_path.read_text(
            encoding="utf-8"
        ).splitlines():
            line = raw_line.strip()
            if not line:
                continue
            try:
                payload = json.loads(line)
                if not isinstance(payload, dict):
                    continue
                if "event_type" in payload:
                    events.append(IndexLifecycleEvent.from_dict(payload))
                    continue
                if "operation" in payload:
                    active_pointer = dict(
                        payload.get("active_pointer") or {}
                    )
                    operation = str(payload.get("operation") or "")
                    events.append(
                        IndexLifecycleEvent(
                            event_id=str(payload["event_id"]),
                            event_type=(
                                "rolled_back"
                                if operation == "rollback"
                                else "activated"
                            ),
                            created_at=str(payload.get("timestamp") or ""),
                            index_version=str(
                                payload.get("to_index_version") or ""
                            ),
                            previous_index_version=(
                                str(payload["from_index_version"])
                                if payload.get("from_index_version")
                                is not None
                                else None
                            ),
                            status="success",
                            manifest_path=str(
                                active_pointer.get("manifest_path") or ""
                            ),
                            active_pointer_path=str(
                                payload.get("pointer_path") or ""
                            ),
                            details={
                                "actor": payload.get("actor"),
                                "reason": payload.get("reason"),
                                "verification_status": payload.get(
                                    "verification_status"
                                ),
                                "verification_metrics": payload.get(
                                    "verification_metrics"
                                )
                                or {},
                            },
                        )
                    )
            except (KeyError, TypeError, ValueError, json.JSONDecodeError):
                # JSONL 最后一行若因进程崩溃不完整，不应污染此前完整历史。
                continue
        return events


def _lifecycle_locked(method: Callable[..., Any]) -> Callable[..., Any]:
    """让公共写操作统一经过 manager 文件锁。"""

    @wraps(method)
    def wrapped(self: "IndexLifecycleManager", *args: Any, **kwargs: Any) -> Any:
        with self._exclusive_lock():
            return method(self, *args, **kwargs)

    return wrapped


class IndexLifecycleManager:
    """协调索引注册、验证、active pointer 切换和回滚。

    Attributes:
        root_dir: 版本目录和控制文件公共根目录。
        active_pointer_path: 在线运行时读取的 pointer。
        registry: 当前注册表和历史事件存储。
        verifier: 激活前质量门禁。
        manifest_name: 每个版本目录中的 Manifest 文件名。
    """

    def __init__(
        self,
        root_dir: str | Path | None = None,
        *,
        active_pointer_path: str | Path | None = None,
        registry: IndexRegistry | None = None,
        verifier: OfflineIndexVerifier | None = None,
        manifest_name: str = "index_manifest.json",
        project_root: str | Path | None = None,
        indexes_root: str | Path | None = None,
        pointer_path: str | Path | None = None,
        registry_path: str | Path | None = None,
        history_path: str | Path | None = None,
        lock_path: str | Path | None = None,
        verifier_factory: Callable[[], Any] | None = None,
        lock_timeout_seconds: float = 15.0,
        stale_lock_seconds: float = 3600.0,
    ) -> None:
        """初始化新版依赖，并把历史构造参数严格映射到同一内部对象。"""

        legacy_mode = project_root is not None
        if root_dir is None and not legacy_mode:
            raise TypeError("root_dir or project_root is required")
        if not legacy_mode and any(
            value is not None
            for value in (
                indexes_root,
                pointer_path,
                registry_path,
                history_path,
                lock_path,
                verifier_factory,
            )
        ):
            raise TypeError(
                "legacy lifecycle arguments require project_root"
            )

        if legacy_mode:
            resolved_project_root = Path(project_root).expanduser().resolve()
            legacy_indexes_root = (
                indexes_root or "data/processed/indexes"
            )
            legacy_pointer_path = (
                pointer_path
                or "data/processed/indexes/active_index.json"
            )
            legacy_registry_path = (
                registry_path
                or "data/processed/indexes/index_registry.json"
            )
            legacy_history_path = (
                history_path
                or "data/processed/indexes/index_lifecycle_history.jsonl"
            )
            legacy_lock_path = (
                lock_path
                or "data/processed/indexes/.index_lifecycle.lock"
            )
            resolved_legacy_root = _resolve_from(
                resolved_project_root,
                legacy_indexes_root,
            )
            if root_dir is not None:
                resolved_root_dir = Path(root_dir).expanduser().resolve()
                if resolved_root_dir != resolved_legacy_root:
                    raise ValueError(
                        "root_dir conflicts with project_root + indexes_root"
                    )
            effective_root = resolved_legacy_root
        else:
            resolved_project_root = Path(root_dir).expanduser().resolve()
            effective_root = resolved_project_root

        self._legacy_mode = legacy_mode
        self.root_dir = effective_root
        self.root_dir.mkdir(parents=True, exist_ok=True)
        self.project_root = resolved_project_root
        self.indexes_root = self.root_dir

        legacy_pointer = (
            _resolve_from(self.project_root, legacy_pointer_path)
            if legacy_mode
            else None
        )
        new_pointer = (
            _resolve_from(self.root_dir, active_pointer_path)
            if active_pointer_path is not None
            else None
        )
        if (
            legacy_pointer is not None
            and new_pointer is not None
            and legacy_pointer != new_pointer
        ):
            raise ValueError(
                "active_pointer_path conflicts with legacy pointer_path"
            )
        self.active_pointer_path = (
            new_pointer
            or legacy_pointer
            or (self.root_dir / "active_index.json")
        )
        self.pointer_path = self.active_pointer_path

        if registry is not None and legacy_mode:
            expected_registry = _resolve_from(
                self.project_root,
                legacy_registry_path,
            )
            expected_history = _resolve_from(
                self.project_root,
                legacy_history_path,
            )
            if (
                registry.registry_path != expected_registry
                or registry.history_path != expected_history
            ):
                raise ValueError(
                    "registry object conflicts with legacy registry/history paths"
                )
        self.registry = registry or IndexRegistry(
            self.root_dir,
            registry_path=(
                _resolve_from(self.project_root, legacy_registry_path)
                if legacy_mode
                else None
            ),
            history_path=(
                _resolve_from(self.project_root, legacy_history_path)
                if legacy_mode
                else None
            ),
        )
        self.registry_path = self.registry.registry_path
        self.history_path = self.registry.history_path

        if verifier is not None and verifier_factory is not None:
            raise ValueError("verifier conflicts with verifier_factory")
        self._verifier = verifier
        self.verifier_factory = verifier_factory or OfflineIndexVerifier

        self.lock_path = (
            _resolve_from(self.project_root, legacy_lock_path)
            if legacy_mode
            else self.root_dir / ".index_lifecycle.lock"
        )
        self.lock_timeout_seconds = max(
            0.1,
            float(lock_timeout_seconds),
        )
        self.stale_lock_seconds = max(
            0.1,
            float(stale_lock_seconds),
        )
        self._lock_owner_thread_id: int | None = None
        self._lock_depth = 0
        self.manifest_name = manifest_name

    @property
    def verifier(self) -> Any:
        """返回显式验证器，或按历史工厂契约延迟构造一次。"""

        if self._verifier is None:
            self._verifier = self.verifier_factory()
        return self._verifier

    @contextmanager
    def _exclusive_lock(self) -> Iterator[None]:
        """获取跨进程文件锁；同一 manager/线程内允许重入。"""

        owner = threading.get_ident()
        if self._lock_owner_thread_id == owner:
            self._lock_depth += 1
            try:
                yield
            finally:
                self._lock_depth -= 1
            return

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

        self._lock_owner_thread_id = owner
        self._lock_depth = 1
        try:
            payload = json.dumps(
                {"pid": os.getpid(), "created_at": _utc_now()},
                ensure_ascii=False,
                sort_keys=True,
            ).encode("utf-8")
            os.write(descriptor, payload)
            os.fsync(descriptor)
            yield
        finally:
            self._lock_depth = 0
            self._lock_owner_thread_id = None
            if descriptor is not None:
                os.close(descriptor)
            try:
                self.lock_path.unlink()
            except FileNotFoundError:
                pass

    def _manifest_path_for_version(self, index_version: str) -> Path:
        """返回标准版本目录中的 Manifest 路径。"""

        return (
            self.root_dir / index_version / self.manifest_name
        ).resolve()

    def _manifest_path_for_target(self, target: str | Path) -> Path:
        """同时解析新版 Manifest 路径入口和历史 index_version 入口。"""

        raw = str(target)
        registered = self.registry.get(raw)
        if registered is not None:
            return Path(registered.manifest_path).expanduser().resolve()

        standard = self._manifest_path_for_version(raw)
        if standard.is_file():
            return standard

        return _resolve_from(self.root_dir, target)

    @staticmethod
    def _verification_status(verification: Any) -> str:
        return str(getattr(verification, "status", "failed"))

    @classmethod
    def _verification_passed(cls, verification: Any) -> bool:
        explicit = getattr(verification, "passed", None)
        return bool(explicit) if explicit is not None else (
            cls._verification_status(verification) == "success"
        )

    @staticmethod
    def _verification_metrics(verification: Any) -> dict[str, Any]:
        return dict(getattr(verification, "metrics", {}) or {})

    @classmethod
    def _verification_to_dict(
        cls,
        verification: Any,
        *,
        manifest_file: Path,
        index_version: str,
    ) -> dict[str, Any]:
        to_dict = getattr(verification, "to_dict", None)
        if callable(to_dict):
            return dict(to_dict())
        return {
            "schema_version": "offline_index_verification_report_v1",
            "status": cls._verification_status(verification),
            "manifest_path": str(
                getattr(verification, "manifest_path", manifest_file)
            ),
            "index_version": str(
                getattr(verification, "index_version", index_version)
            ),
            "checks": [],
            "metrics": cls._verification_metrics(verification),
            "self_retrieval": [],
        }

    def _read_manifest(
        self,
        manifest_path: str | Path,
    ) -> tuple[Path, IndexManifest, str]:
        """严格读取 Manifest 并返回绝对路径、对象和文件哈希。"""

        manifest_file = _resolve_from(
            self.root_dir,
            manifest_path,
        )
        if not manifest_file.is_file():
            raise IndexNotFoundError(
                f"index manifest not found: {manifest_file}"
            )
        try:
            manifest = IndexManifest.model_validate_json(
                manifest_file.read_text(encoding="utf-8")
            )
        except Exception as exc:
            raise IndexLifecycleError(
                f"invalid index manifest: {manifest_file}: "
                f"{type(exc).__name__}: {exc}"
            ) from exc
        return manifest_file, manifest, sha256_file(manifest_file)

    def _pointer_manifest_path(
        self,
        pointer: ActiveIndexPointer,
    ) -> Path:
        """解析 pointer 内 Manifest 路径。"""

        return _resolve_from(
            self.active_pointer_path.parent,
            pointer.manifest_path,
        )

    def read_active_pointer(self) -> ActiveIndexPointer | None:
        """读取当前 active pointer，不存在时返回 ``None``。"""

        if not self.active_pointer_path.is_file():
            return None
        try:
            return ActiveIndexPointer.model_validate_json(
                self.active_pointer_path.read_text(
                    encoding="utf-8"
                )
            )
        except Exception as exc:
            raise IndexLifecycleError(
                f"invalid active index pointer: "
                f"{self.active_pointer_path}: "
                f"{type(exc).__name__}: {exc}"
            ) from exc

    def active_index_version(self) -> str | None:
        """返回当前 active 索引版本；无 pointer 时返回 ``None``。"""

        pointer = self.read_active_pointer()
        return pointer.index_version if pointer else None

    def _entry_from_manifest(
        self,
        *,
        manifest_file: Path,
        manifest: IndexManifest,
        manifest_hash: str,
        status: IndexLifecycleStatus,
        previous_entry: IndexRegistryEntry | None = None,
        last_verified_at: str | None = None,
        last_error: dict[str, Any] | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> IndexRegistryEntry:
        """从 Manifest 和旧条目构造新的注册表快照条目。"""

        now = _utc_now()
        return IndexRegistryEntry(
            index_version=manifest.index_version,
            manifest_path=str(manifest_file),
            manifest_sha256=manifest_hash,
            status=status.value,
            dataset_version=manifest.dataset_version,
            config_hash=manifest.config_hash,
            reproducibility_hash=manifest.reproducibility_hash,
            registered_at=(
                previous_entry.registered_at
                if previous_entry is not None
                else now
            ),
            updated_at=now,
            last_verified_at=(
                last_verified_at
                if last_verified_at is not None
                else (
                    previous_entry.last_verified_at
                    if previous_entry
                    else None
                )
            ),
            last_error=last_error,
            metadata={
                **(
                    dict(previous_entry.metadata)
                    if previous_entry
                    else {}
                ),
                **(metadata or {}),
            },
        )

    def _event(
        self,
        *,
        event_type: str,
        index_version: str,
        previous_index_version: str | None,
        status: str,
        manifest_path: str,
        details: dict[str, Any] | None = None,
    ) -> IndexLifecycleEvent:
        """构造内容稳定的生命周期事件和事件 ID。"""

        created_at = _utc_now()
        base = {
            "event_type": event_type,
            "created_at": created_at,
            "index_version": index_version,
            "previous_index_version": previous_index_version,
            "status": status,
            "manifest_path": manifest_path,
            "active_pointer_path": str(
                self.active_pointer_path
            ),
            "details": details or {},
        }
        return IndexLifecycleEvent(
            event_id=f"index_event_{_stable_hash(base)[:20]}",
            **base,
        )

    def discover_manifests(self) -> list[Path]:
        """递归发现 Manifest，并排除失败/归档目录和控制面文件。"""

        manifests = sorted(
            path.resolve()
            for path in self.root_dir.rglob(self.manifest_name)
            if path.is_file()
            and not any(
                ".failed_" in part or ".archived_" in part
                for part in path.parts
            )
        )
        return [
            path
            for path in manifests
            if path != self.active_pointer_path
            and path != self.registry.registry_path
        ]

    @_lifecycle_locked
    def register(
        self,
        manifest_path: str | Path,
    ) -> IndexRegistryEntry:
        """把一个有效 Manifest 加入注册表，不执行深度产物验证。"""

        # 第一步：读取 Manifest，并要求其位于与 index_version 同名的标准版本目录。
        manifest_file, manifest, manifest_hash = self._read_manifest(
            manifest_path
        )
        expected = self._manifest_path_for_version(
            manifest.index_version
        )
        if manifest_file != expected:
            raise IndexLifecycleError(
                "manifest path does not match immutable version layout: "
                f"expected={expected} actual={manifest_file}"
            )

        # 第二步：同一版本不得注册到另一个 Manifest 路径。
        previous = self.registry.get(manifest.index_version)
        if (
            previous is not None
            and Path(previous.manifest_path).resolve() != manifest_file
        ):
            raise IndexLifecycleError(
                "index version collision: "
                f"{manifest.index_version}"
            )

        entry = self._entry_from_manifest(
            manifest_file=manifest_file,
            manifest=manifest,
            manifest_hash=manifest_hash,
            status=IndexLifecycleStatus.REGISTERED,
            previous_entry=previous,
        )
        entry = self.registry.upsert(entry)

        # 第三步：追加注册事件。
        self.registry.append_event(
            self._event(
                event_type="registered",
                index_version=manifest.index_version,
                previous_index_version=self.active_index_version(),
                status="success",
                manifest_path=str(manifest_file),
                details={"manifest_sha256": manifest_hash},
            )
        )
        return entry

    def discover_and_register(self) -> list[IndexRegistryEntry]:
        """发现所有标准 Manifest，并逐一注册。"""

        return [
            self.register(path)
            for path in self.discover_manifests()
        ]

    def discover(self, *, persist: bool = True) -> list[IndexRegistryEntry]:
        """兼容历史发现 API；无效 Manifest 跳过，版本碰撞仍显式失败。"""

        discovered: dict[str, IndexRegistryEntry] = {}
        for path in self.discover_manifests():
            try:
                entry = self.register(path) if persist else (
                    self._entry_from_discovered_manifest(path)
                )
            except IndexLifecycleError as exc:
                if "collision" in str(exc) or "duplicate index_version" in str(exc):
                    raise
                continue
            except Exception:
                continue
            existing = discovered.get(entry.index_version)
            if (
                existing is not None
                and existing.manifest_path != entry.manifest_path
            ):
                raise IndexLifecycleError(
                    "duplicate index_version discovered at different manifests: "
                    f"{entry.index_version}"
                )
            discovered[entry.index_version] = entry
        return [discovered[key] for key in sorted(discovered)]

    def _entry_from_discovered_manifest(
        self,
        manifest_path: str | Path,
    ) -> IndexRegistryEntry:
        manifest_file, manifest, manifest_hash = self._read_manifest(
            manifest_path
        )
        expected = self._manifest_path_for_version(manifest.index_version)
        if manifest_file != expected:
            raise IndexLifecycleError(
                "manifest path does not match immutable version layout: "
                f"expected={expected} actual={manifest_file}"
            )
        return self._entry_from_manifest(
            manifest_file=manifest_file,
            manifest=manifest,
            manifest_hash=manifest_hash,
            status=IndexLifecycleStatus.REGISTERED,
            previous_entry=self.registry.get(manifest.index_version),
        )

    def list_indexes(
        self,
        *,
        refresh: bool | None = None,
    ) -> list[IndexRegistryEntry]:
        """返回注册表条目；旧构造默认刷新，新构造默认读取快照。"""

        effective_refresh = self._legacy_mode if refresh is None else refresh
        return self.discover(persist=True) if effective_refresh else (
            self.registry.list_entries()
        )

    @_lifecycle_locked
    def verify(
        self,
        manifest_path: str | Path,
        **verify_kwargs: Any,
    ) -> Any:
        """执行深度验证，并拒绝注册后被篡改的 Manifest。"""

        manifest_file, manifest, manifest_hash = self._read_manifest(
            manifest_path
        )
        previous = self.registry.get(manifest.index_version)
        if previous is None:
            previous = self.register(manifest_file)
        else:
            if Path(previous.manifest_path).resolve() != manifest_file:
                raise IndexActivationError(
                    "registered index manifest path changed after registration: "
                    f"index_version={manifest.index_version}"
                )
            if previous.manifest_sha256 != manifest_hash:
                raise IndexActivationError(
                    "registered index manifest changed after registration: "
                    f"index_version={manifest.index_version}"
                )

        verification = self.verifier.verify(
            manifest_file,
            **verify_kwargs,
        )
        verified_at = _utc_now()
        passed = self._verification_passed(verification)
        verification_status = self._verification_status(verification)
        metrics = self._verification_metrics(verification)

        status = (
            IndexLifecycleStatus.VERIFIED
            if passed
            else IndexLifecycleStatus.FAILED
        )
        last_error = (
            None
            if passed
            else {
                "error_type": "IndexVerificationFailed",
                "message": "index verification failed",
                "failed_checks": metrics.get("failed_checks", []),
            }
        )
        entry = self._entry_from_manifest(
            manifest_file=manifest_file,
            manifest=manifest,
            manifest_hash=manifest_hash,
            status=status,
            previous_entry=previous,
            last_verified_at=verified_at,
            last_error=last_error,
            metadata={
                "verification_status": verification_status,
                "verification_metrics": metrics,
            },
        )
        self.registry.upsert(entry)
        self.registry.append_event(
            self._event(
                event_type="verified",
                index_version=manifest.index_version,
                previous_index_version=self.active_index_version(),
                status=verification_status,
                manifest_path=str(manifest_file),
                details={
                    "metrics": metrics,
                    "failed_checks": metrics.get("failed_checks", []),
                },
            )
        )
        return verification

    def _relative_manifest_path(self, manifest_file: Path) -> str:
        """尽量把 Manifest 路径写成相对 pointer 目录的可移动路径。"""

        try:
            return str(
                manifest_file.relative_to(
                    self.active_pointer_path.parent
                )
            )
        except ValueError:
            return str(manifest_file)

    def _write_pointer(
        self,
        *,
        manifest_file: Path,
        manifest: IndexManifest,
        manifest_hash: str,
    ) -> ActiveIndexPointer:
        """构造并原子发布 active pointer。"""

        pointer = ActiveIndexPointer(
            index_version=manifest.index_version,
            manifest_path=self._relative_manifest_path(
                manifest_file
            ),
            manifest_sha256=manifest_hash,
        )
        pointer.write(self.active_pointer_path)
        return pointer

    def _restore_pointer_bytes(
        self,
        previous_bytes: bytes | None,
    ) -> None:
        """在激活失败后恢复旧 pointer，或删除首次创建的新 pointer。"""

        if previous_bytes is None:
            try:
                self.active_pointer_path.unlink()
            except FileNotFoundError:
                pass
            return

        # ``atomic_write_text`` 只接受文本；pointer 协议固定 UTF-8 JSON。
        atomic_write_text(
            self.active_pointer_path,
            previous_bytes.decode("utf-8"),
        )

    def _mark_previous_status(
        self,
        *,
        previous_index_version: str | None,
        new_status: IndexLifecycleStatus,
    ) -> None:
        """在切换成功后更新旧 active 条目的生命周期状态。"""

        if not previous_index_version:
            return
        previous = self.registry.get(previous_index_version)
        if previous is None:
            return
        updated = IndexRegistryEntry(
            **{
                **previous.to_dict(),
                "status": new_status.value,
                "updated_at": _utc_now(),
            }
        )
        self.registry.upsert(updated)

    @_lifecycle_locked
    def activate(
        self,
        manifest_path: str | Path,
        *,
        verification_result: OfflineIndexVerificationResult | Any | None = None,
        verify_kwargs: dict[str, Any] | None = None,
        actor: str = "manual",
        reason: str | None = None,
        verify_artifact_hashes: bool = True,
        verify_milvus: bool = True,
        self_retrieval_samples: int = 3,
    ) -> IndexLifecycleResult:
        """验证目标索引并原子切换 active pointer。

        Args:
            manifest_path: 目标 Manifest。
            verification_result: 可选调用方已完成的验证报告。
            verify_kwargs: 未提供报告时传给验证器的参数。

        Raises:
            IndexActivationError: 验证失败、报告目标不匹配或 pointer 切换失败时抛出。
        """

        # 第一步：读取目标 Manifest 和切换前 active 状态，并备份旧 pointer 原始字节。
        manifest_file, manifest, manifest_hash = self._read_manifest(
            self._manifest_path_for_target(manifest_path)
        )
        registered_entry = self.registry.get(manifest.index_version)
        if registered_entry is not None:
            if Path(registered_entry.manifest_path).resolve() != manifest_file:
                raise IndexActivationError(
                    "registered index manifest path changed after registration: "
                    f"index_version={manifest.index_version}"
                )
            if registered_entry.manifest_sha256 != manifest_hash:
                raise IndexActivationError(
                    "registered index manifest changed after registration: "
                    f"index_version={manifest.index_version}"
                )
        if str(manifest.index.get("backend") or "") != "milvus_lite":
            raise IndexActivationError(
                "only backend=milvus_lite can be activated for online runtime; "
                f"got {manifest.index.get('backend')!r}"
            )

        previous_pointer = self.read_active_pointer()
        previous_index_version = (
            previous_pointer.index_version
            if previous_pointer
            else None
        )
        previous_bytes = (
            self.active_pointer_path.read_bytes()
            if self.active_pointer_path.is_file()
            else None
        )

        # 第二步：使用调用方报告或现场执行完整验证。
        effective_verify_kwargs = dict(verify_kwargs or {})
        effective_verify_kwargs.setdefault(
            "verify_artifact_hashes",
            verify_artifact_hashes,
        )
        effective_verify_kwargs.setdefault("verify_milvus", verify_milvus)
        effective_verify_kwargs.setdefault(
            "self_retrieval_samples",
            self_retrieval_samples,
        )
        verification = verification_result or self.verify(
            manifest_file,
            **effective_verify_kwargs,
        )
        verification_status = self._verification_status(verification)
        verification_metrics = self._verification_metrics(verification)

        # 第三步：验证报告必须成功；若报告声明目标，也必须与激活目标一致。
        if not self._verification_passed(verification):
            raise IndexActivationError(
                "index activation rejected because verification failed: "
                f"{verification_metrics.get('failed_checks', [])}"
            )
        report_manifest_path = getattr(
            verification,
            "manifest_path",
            None,
        )
        report_index_version = getattr(
            verification,
            "index_version",
            None,
        )
        if (
            report_manifest_path is not None
            and Path(report_manifest_path).resolve() != manifest_file.resolve()
        ) or (
            report_index_version is not None
            and report_index_version != manifest.index_version
        ):
            raise IndexActivationError(
                "verification result does not match activation target"
            )

        try:
            # 第四步：原子发布新 pointer；成功后才更新注册表状态。
            self._write_pointer(
                manifest_file=manifest_file,
                manifest=manifest,
                manifest_hash=manifest_hash,
            )
            resolved = ActiveIndexResolver(
                verify_manifest_hash=True,
                verify_artifacts=False,
            ).resolve(self.active_pointer_path)
            if resolved["index_version"] != manifest.index_version:
                raise IndexActivationError(
                    "post-activation online resolution mismatch"
                )

            previous_entry = self.registry.get(
                manifest.index_version
            )
            active_entry = self._entry_from_manifest(
                manifest_file=manifest_file,
                manifest=manifest,
                manifest_hash=manifest_hash,
                status=IndexLifecycleStatus.ACTIVE,
                previous_entry=previous_entry,
                last_verified_at=_utc_now(),
                last_error=None,
                metadata={
                    "verification_status": verification_status,
                    "activated_at": _utc_now(),
                },
            )
            active_entry = self.registry.upsert(active_entry)

            # 第五步：若切换到不同版本，把旧 active 标记为已验证但非 active。
            if previous_index_version != manifest.index_version:
                self._mark_previous_status(
                    previous_index_version=previous_index_version,
                    new_status=IndexLifecycleStatus.VERIFIED,
                )

            # 第六步：追加成功激活事件。
            event = self._event(
                event_type="activated",
                index_version=manifest.index_version,
                previous_index_version=previous_index_version,
                status="success",
                manifest_path=str(manifest_file),
                details={
                    "manifest_sha256": manifest_hash,
                    "verification_status": verification_status,
                    "verification_metrics": verification_metrics,
                    "actor": actor,
                    "reason": reason,
                },
            )
            self.registry.append_event(event)

            return IndexLifecycleResult(
                operation="activate",
                status="success",
                index_version=manifest.index_version,
                previous_index_version=previous_index_version,
                manifest_path=str(manifest_file),
                active_pointer_path=str(
                    self.active_pointer_path
                ),
                verification=self._verification_to_dict(
                    verification,
                    manifest_file=manifest_file,
                    index_version=manifest.index_version,
                ),
                registry_entry=active_entry.to_dict(),
                event_id=event.event_id,
            )

        except Exception as exc:
            # 第七步：任何切换后异常都恢复旧 pointer，防止在线状态停留在半完成版本。
            try:
                self._restore_pointer_bytes(previous_bytes)
            except Exception as restore_exc:
                raise IndexActivationError(
                    "activation failed and active pointer restore also failed: "
                    f"activation={type(exc).__name__}: {exc}; "
                    f"restore={type(restore_exc).__name__}: {restore_exc}"
                ) from exc

            # 第八步：把目标条目标记 FAILED，并记录失败事件。
            previous_entry = self.registry.get(
                manifest.index_version
            )
            failed_entry = self._entry_from_manifest(
                manifest_file=manifest_file,
                manifest=manifest,
                manifest_hash=manifest_hash,
                status=IndexLifecycleStatus.FAILED,
                previous_entry=previous_entry,
                last_error={
                    "error_type": type(exc).__name__,
                    "message": str(exc),
                },
            )
            self.registry.upsert(failed_entry)
            self.registry.append_event(
                self._event(
                    event_type="activation_failed",
                    index_version=manifest.index_version,
                    previous_index_version=previous_index_version,
                    status="failed",
                    manifest_path=str(manifest_file),
                    details={
                        "error_type": type(exc).__name__,
                        "message": str(exc),
                    },
                )
            )

            raise

    @_lifecycle_locked
    def rollback(
        self,
        target_index_version: str | None = None,
        *,
        verify_kwargs: dict[str, Any] | None = None,
        actor: str = "manual",
        reason: str | None = None,
        verify_artifact_hashes: bool = True,
        verify_milvus: bool = True,
        self_retrieval_samples: int = 3,
    ) -> IndexLifecycleResult:
        """验证并激活一个历史版本，把当前版本标记为 rolled_back。

        Raises:
            IndexRollbackError: 当前没有 active、目标与当前相同、目标不存在或激活失败。
        """

        # 第一步：回滚必须存在当前 active，且目标必须是另一个版本。
        current_pointer = self.read_active_pointer()
        if current_pointer is None:
            raise IndexRollbackError(
                "cannot rollback without an active index"
            )
        if target_index_version is None:
            latest = next(
                (
                    event
                    for event in reversed(self.registry.read_events())
                    if event.event_type in {"activated", "rolled_back"}
                    and event.index_version == current_pointer.index_version
                    and event.previous_index_version is not None
                ),
                None,
            )
            if latest is None:
                raise IndexRollbackError(
                    "no previous active index recorded; provide target_index_version"
                )
            target_index_version = latest.previous_index_version

        if target_index_version == current_pointer.index_version:
            raise IndexRollbackError(
                "rollback target is already active"
            )

        # 第二步：优先从注册表解析目标 Manifest；缺失时尝试标准版本目录。
        target_entry = self.registry.get(target_index_version)
        target_manifest = (
            Path(target_entry.manifest_path)
            if target_entry is not None
            else self._manifest_path_for_version(
                target_index_version
            )
        )
        if not target_manifest.is_file():
            raise IndexRollbackError(
                f"rollback target not found: {target_index_version}"
            )

        try:
            # 第三步：复用完整 activate 流程，包括验证和 pointer 恢复保护。
            result = self.activate(
                target_manifest,
                verify_kwargs=verify_kwargs,
                actor=actor,
                reason=reason,
                verify_artifact_hashes=verify_artifact_hashes,
                verify_milvus=verify_milvus,
                self_retrieval_samples=self_retrieval_samples,
            )
        except Exception as exc:
            if isinstance(exc, IndexRollbackError):
                raise
            raise IndexRollbackError(
                f"rollback failed: {type(exc).__name__}: {exc}"
            ) from exc

        # 第四步：目标已成功 active 后，把原 active 标记为 ROLLED_BACK。
        self._mark_previous_status(
            previous_index_version=current_pointer.index_version,
            new_status=IndexLifecycleStatus.ROLLED_BACK,
        )

        # 第五步：追加显式回滚事件，区别于底层 activate 事件。
        rollback_event = self._event(
            event_type="rolled_back",
            index_version=target_index_version,
            previous_index_version=current_pointer.index_version,
            status="success",
            manifest_path=result.manifest_path,
            details={
                "activated_via": "rollback",
                "verification": result.verification,
                "actor": actor,
                "reason": reason,
            },
        )
        self.registry.append_event(rollback_event)

        # 第六步：返回 rollback 语义的结果对象和目标最新注册表状态。
        target_after = self.registry.get(target_index_version)
        return IndexLifecycleResult(
            operation="rollback",
            status="success",
            index_version=target_index_version,
            previous_index_version=current_pointer.index_version,
            manifest_path=result.manifest_path,
            active_pointer_path=result.active_pointer_path,
            verification=result.verification,
            registry_entry=(
                target_after.to_dict()
                if target_after is not None
                else result.registry_entry
            ),
            event_id=rollback_event.event_id,
        )

    def resolve_active(
        self,
        *,
        verify_artifacts: bool = True,
    ) -> dict[str, Any]:
        """按在线运行时正式 Resolver 解析当前 active pointer。"""

        return ActiveIndexResolver(
            verify_manifest_hash=True,
            verify_artifacts=verify_artifacts,
        ).resolve(self.active_pointer_path)

    def _legacy_pointer_payload(
        self,
        index_version: str | None,
    ) -> dict[str, Any] | None:
        if not index_version:
            return None
        entry = self.registry.get(index_version)
        if entry is None:
            return {"index_version": index_version}
        return {
            "index_version": index_version,
            "manifest_path": entry.manifest_path,
            "manifest_sha256": entry.manifest_sha256,
        }

    def history(self) -> list[LegacyIndexLifecycleEvent]:
        """返回历史公共契约的激活/回滚审计投影。"""

        control_events = [
            event
            for event in self.registry.read_events()
            if event.status == "success"
            and event.event_type in {"activated", "rolled_back"}
        ]
        projected: list[LegacyIndexLifecycleEvent] = []
        for index, event in enumerate(control_events):
            if event.event_type == "activated" and index + 1 < len(
                control_events
            ):
                following = control_events[index + 1]
                if (
                    following.event_type == "rolled_back"
                    and following.index_version == event.index_version
                    and following.previous_index_version
                    == event.previous_index_version
                ):
                    continue

            details = dict(event.details)
            verification_payload = dict(
                details.get("verification") or {}
            )
            verification_metrics = dict(
                details.get("verification_metrics")
                or verification_payload.get("metrics")
                or {}
            )
            verification_status = str(
                details.get("verification_status")
                or verification_payload.get("status")
                or "success"
            )
            projected.append(
                LegacyIndexLifecycleEvent(
                    event_id=event.event_id,
                    timestamp=event.created_at,
                    operation=(
                        "rollback"
                        if event.event_type == "rolled_back"
                        else "activate"
                    ),
                    actor=str(details.get("actor") or "manual"),
                    reason=(
                        str(details["reason"])
                        if details.get("reason") is not None
                        else None
                    ),
                    from_index_version=event.previous_index_version,
                    to_index_version=event.index_version,
                    pointer_path=event.active_pointer_path,
                    previous_pointer=self._legacy_pointer_payload(
                        event.previous_index_version
                    ),
                    active_pointer=(
                        self._legacy_pointer_payload(event.index_version)
                        or {"index_version": event.index_version}
                    ),
                    verification_status=verification_status,
                    verification_metrics=verification_metrics,
                )
            )
        return projected

    def status(self) -> IndexLifecycleSummary:
        """返回历史公共契约的只读生命周期状态。"""

        pointer = self.read_active_pointer()
        return IndexLifecycleSummary(
            pointer_path=str(self.active_pointer_path),
            active_index_version=(
                pointer.index_version if pointer is not None else None
            ),
            active_manifest_path=(
                pointer.manifest_path if pointer is not None else None
            ),
            active_manifest_sha256=(
                pointer.manifest_sha256 if pointer is not None else None
            ),
            registered_index_count=len(self.registry.list_entries()),
            history_event_count=len(self.history()),
        )

    @_lifecycle_locked
    def delete_version(
        self,
        index_version: str,
        *,
        allow_active: bool = False,
    ) -> None:
        """删除一个版本目录。

        Args:
            index_version: 目标版本。
            allow_active: 是否允许删除当前 active 版本，默认禁止。

        Raises:
            IndexLifecycleError: 目标 active 且未显式允许时抛出。
            IndexNotFoundError: 版本目录不存在时抛出。

        删除不会移除注册表历史，条目会更新为 FAILED 并记录 deletion metadata。
        """

        # 第一步：保护当前 active 版本。
        if (
            not allow_active
            and self.active_index_version() == index_version
        ):
            raise IndexLifecycleError(
                "cannot delete active index version"
            )

        # 第二步：版本目录必须位于标准根目录布局。
        version_dir = self.root_dir / index_version
        if not version_dir.is_dir():
            raise IndexNotFoundError(
                f"index version directory not found: {version_dir}"
            )

        # 第三步：递归删除版本产物。
        shutil.rmtree(version_dir)

        # 第四步：保留注册表条目但标记失败和删除时间，维护审计轨迹。
        entry = self.registry.get(index_version)
        if entry is not None:
            updated = IndexRegistryEntry(
                **{
                    **entry.to_dict(),
                    "status": IndexLifecycleStatus.FAILED.value,
                    "updated_at": _utc_now(),
                    "last_error": {
                        "error_type": "IndexVersionDeleted",
                        "message": "index version directory was deleted",
                    },
                    "metadata": {
                        **entry.metadata,
                        "deleted_at": _utc_now(),
                    },
                }
            )
            self.registry.upsert(updated)

        # 第五步：追加删除事件。
        self.registry.append_event(
            self._event(
                event_type="deleted",
                index_version=index_version,
                previous_index_version=self.active_index_version(),
                status="success",
                manifest_path=str(
                    self._manifest_path_for_version(index_version)
                ),
                details={"allow_active": allow_active},
            )
        )

    def import_entries(
        self,
        manifests: Iterable[str | Path],
    ) -> list[IndexRegistryEntry]:
        """批量注册显式 Manifest 路径。"""

        return [self.register(path) for path in manifests]
