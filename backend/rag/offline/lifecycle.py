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
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Any, Iterable

from rag.offline.manifest import (
    ActiveIndexPointer,
    IndexManifest,
    atomic_write_text,
    sha256_file,
)
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

    def to_dict(self) -> dict[str, Any]:
        """返回 JSON 兼容结果字典。"""

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
    ) -> None:
        """解析控制面路径并确保根目录存在。"""

        self.root_dir = Path(root_dir).expanduser().resolve()
        self.root_dir.mkdir(parents=True, exist_ok=True)
        self.registry_path = self.root_dir / registry_file
        self.history_path = self.root_dir / history_file

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
        if not isinstance(indexes, list):
            raise IndexLifecycleError(
                "index registry 'indexes' must be a list"
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
        root_dir: str | Path,
        *,
        active_pointer_path: str | Path | None = None,
        registry: IndexRegistry | None = None,
        verifier: OfflineIndexVerifier | None = None,
        manifest_name: str = "index_manifest.json",
    ) -> None:
        """初始化索引控制面根目录和依赖。"""

        self.root_dir = Path(root_dir).expanduser().resolve()
        self.root_dir.mkdir(parents=True, exist_ok=True)
        self.active_pointer_path = (
            _resolve_from(self.root_dir, active_pointer_path)
            if active_pointer_path is not None
            else self.root_dir / "active_index.json"
        )
        self.registry = registry or IndexRegistry(self.root_dir)
        self.verifier = verifier or OfflineIndexVerifier()
        self.manifest_name = manifest_name

    def _manifest_path_for_version(self, index_version: str) -> Path:
        """返回标准版本目录中的 Manifest 路径。"""

        return (
            self.root_dir / index_version / self.manifest_name
        ).resolve()

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
        """发现 root 下标准二级目录中的 Manifest，排除 pointer 和注册表文件。"""

        manifests = sorted(
            path.resolve()
            for path in self.root_dir.glob(
                f"*/{self.manifest_name}"
            )
            if path.is_file()
        )
        return [
            path
            for path in manifests
            if path != self.active_pointer_path
            and path != self.registry.registry_path
        ]

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

        # 第二步：保留首次注册时间和旧 metadata，更新 Manifest 内容与状态。
        previous = self.registry.get(manifest.index_version)
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

    def list_indexes(self) -> list[IndexRegistryEntry]:
        """返回注册表中的全部索引条目。"""

        return self.registry.list_entries()

    def verify(
        self,
        manifest_path: str | Path,
        **verify_kwargs: Any,
    ) -> OfflineIndexVerificationResult:
        """执行深度验证并更新注册表状态与事件历史。

        验证失败不会抛出 ``IndexActivationError``，而是返回 failed report；激活流程会
        根据该结果决定是否拒绝切换。
        """

        # 第一步：读取或隐式注册目标 Manifest。
        manifest_file, manifest, manifest_hash = self._read_manifest(
            manifest_path
        )
        previous = self.registry.get(manifest.index_version)
        if previous is None:
            previous = self.register(manifest_file)

        # 第二步：运行完整验证器并记录验证时间。
        verification = self.verifier.verify(
            manifest_file,
            **verify_kwargs,
        )
        verified_at = _utc_now()

        # 第三步：根据验证结果设置 VERIFIED 或 FAILED 状态，并保存失败检查列表。
        status = (
            IndexLifecycleStatus.VERIFIED
            if verification.passed
            else IndexLifecycleStatus.FAILED
        )
        last_error = (
            None
            if verification.passed
            else {
                "error_type": "IndexVerificationFailed",
                "message": "index verification failed",
                "failed_checks": verification.metrics.get(
                    "failed_checks",
                    [],
                ),
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
                "verification_status": verification.status,
                "verification_metrics": verification.metrics,
            },
        )
        self.registry.upsert(entry)

        # 第四步：追加成功或失败验证事件。
        self.registry.append_event(
            self._event(
                event_type="verified",
                index_version=manifest.index_version,
                previous_index_version=self.active_index_version(),
                status=verification.status,
                manifest_path=str(manifest_file),
                details={
                    "metrics": verification.metrics,
                    "failed_checks": verification.metrics.get(
                        "failed_checks",
                        [],
                    ),
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

    def activate(
        self,
        manifest_path: str | Path,
        *,
        verification_result: OfflineIndexVerificationResult | None = None,
        verify_kwargs: dict[str, Any] | None = None,
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
            manifest_path
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
        verification = verification_result or self.verify(
            manifest_file,
            **(verify_kwargs or {}),
        )

        # 第三步：验证报告必须成功且明确属于目标 Manifest/版本。
        if not verification.passed:
            raise IndexActivationError(
                "index activation rejected because verification failed: "
                f"{verification.metrics.get('failed_checks', [])}"
            )
        if (
            Path(verification.manifest_path).resolve()
            != manifest_file.resolve()
            or verification.index_version != manifest.index_version
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
                    "verification_status": verification.status,
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
            self.registry.append_event(
                self._event(
                    event_type="activated",
                    index_version=manifest.index_version,
                    previous_index_version=previous_index_version,
                    status="success",
                    manifest_path=str(manifest_file),
                    details={
                        "manifest_sha256": manifest_hash,
                        "verification_metrics": verification.metrics,
                    },
                )
            )

            return IndexLifecycleResult(
                operation="activate",
                status="success",
                index_version=manifest.index_version,
                previous_index_version=previous_index_version,
                manifest_path=str(manifest_file),
                active_pointer_path=str(
                    self.active_pointer_path
                ),
                verification=verification.to_dict(),
                registry_entry=active_entry.to_dict(),
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

            if isinstance(exc, IndexActivationError):
                raise
            raise IndexActivationError(
                f"index activation failed: "
                f"{type(exc).__name__}: {exc}"
            ) from exc

    def rollback(
        self,
        target_index_version: str,
        *,
        verify_kwargs: dict[str, Any] | None = None,
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
        self.registry.append_event(
            self._event(
                event_type="rolled_back",
                index_version=target_index_version,
                previous_index_version=current_pointer.index_version,
                status="success",
                manifest_path=result.manifest_path,
                details={
                    "activated_via": "rollback",
                    "verification": result.verification,
                },
            )
        )

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
        )

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
