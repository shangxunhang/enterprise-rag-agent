"""SIF-13 reachability-safe storage lifecycle and semantic GC.

This module owns retention/reachability/deletion orchestration only.  It does
not own business currentness, Release, Recovery, Memory freshness, or SIF-12
Budget truth.  Every protected root and physical delete capability is supplied
by the owning runtime/store.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
import hashlib
import json
from pathlib import Path
import sqlite3
from time import time
from typing import Callable, Iterable, Protocol, Sequence

from apps.enterprise_document.schemas.artifact_reference import ArtifactReferenceSchema
from apps.enterprise_document.services.agent_rag_v1.artifact_store import FileArtifactStore


class StorageLifecycleError(RuntimeError):
    pass


class StorageIntegrityError(StorageLifecycleError):
    pass


class StorageObjectClass(str, Enum):
    CANONICAL = "CANONICAL"
    PROJECTION = "PROJECTION"
    RECOVERY = "RECOVERY"
    AUDIT = "AUDIT"
    EPHEMERAL = "EPHEMERAL"


class CanonicalStorageSubtype(str, Enum):
    DOMAIN = "CANONICAL_DOMAIN"
    CONTROL = "CANONICAL_CONTROL"
    COORDINATION = "CANONICAL_COORDINATION"


class RetentionClass(str, Enum):
    R0_PROTECTED = "R0_PROTECTED"
    R1_LONG_HISTORY = "R1_LONG_HISTORY"
    R2_RECOVERY_WINDOW = "R2_RECOVERY_WINDOW"
    R3_AUDIT_DEBUG_WINDOW = "R3_AUDIT_DEBUG_WINDOW"
    R4_DISPOSABLE = "R4_DISPOSABLE"


class StorageReferenceKind(str, Enum):
    STRONG_PROTECT = "STRONG_PROTECT"
    WEAK_PROVENANCE = "WEAK_PROVENANCE"
    OWNED_CHILD = "OWNED_CHILD"
    EXTERNAL_REF = "EXTERNAL_REF"


@dataclass(frozen=True)
class StorageFamilyClassification:
    """Frozen semantic class for one object family, independent of carrier."""

    object_family: str
    storage_class: StorageObjectClass
    canonical_subtype: CanonicalStorageSubtype | None = None
    durable_by_default: bool = True

    def __post_init__(self) -> None:
        if not str(self.object_family or "").strip():
            raise ValueError("storage family cannot be blank")
        if (
            self.storage_class is StorageObjectClass.CANONICAL
            and self.canonical_subtype is None
        ):
            raise ValueError("CANONICAL storage family requires subtype")
        if (
            self.storage_class is not StorageObjectClass.CANONICAL
            and self.canonical_subtype is not None
        ):
            raise ValueError("non-CANONICAL storage family cannot carry subtype")


class StorageFamilyRegistry:
    """Classification-only registry; it grants no physical delete authority."""

    def __init__(self, entries: Iterable[StorageFamilyClassification]) -> None:
        by_family: dict[str, StorageFamilyClassification] = {}
        for raw in entries:
            entry = StorageFamilyClassification(
                object_family=str(raw.object_family).strip(),
                storage_class=raw.storage_class,
                canonical_subtype=raw.canonical_subtype,
                durable_by_default=bool(raw.durable_by_default),
            )
            if entry.object_family in by_family:
                raise StorageIntegrityError(
                    f"duplicate storage family classification: {entry.object_family}"
                )
            by_family[entry.object_family] = entry
        self._by_family = by_family

    def resolve(self, object_family: str) -> StorageFamilyClassification:
        key = str(object_family or "").strip()
        try:
            return self._by_family[key]
        except KeyError as exc:
            raise StorageIntegrityError(
                f"unclassified managed storage family: {key or '<blank>'}"
            ) from exc

    def all(self) -> tuple[StorageFamilyClassification, ...]:
        return tuple(self._by_family[key] for key in sorted(self._by_family))

    def represented_classes(self) -> frozenset[StorageObjectClass]:
        return frozenset(item.storage_class for item in self._by_family.values())


FROZEN_STORAGE_FAMILY_REGISTRY = StorageFamilyRegistry(
    (
        StorageFamilyClassification("run_lifecycle", StorageObjectClass.CANONICAL, CanonicalStorageSubtype.CONTROL),
        StorageFamilyClassification("attempt_lifecycle", StorageObjectClass.CANONICAL, CanonicalStorageSubtype.CONTROL),
        StorageFamilyClassification("sif02_coordination", StorageObjectClass.CANONICAL, CanonicalStorageSubtype.COORDINATION),
        StorageFamilyClassification("artifact_version", StorageObjectClass.CANONICAL, CanonicalStorageSubtype.DOMAIN),
        StorageFamilyClassification("artifact_active_head", StorageObjectClass.CANONICAL, CanonicalStorageSubtype.DOMAIN),
        StorageFamilyClassification("fact_snapshot", StorageObjectClass.CANONICAL, CanonicalStorageSubtype.DOMAIN),
        StorageFamilyClassification("section_plan", StorageObjectClass.CANONICAL, CanonicalStorageSubtype.DOMAIN),
        StorageFamilyClassification("evidence_package", StorageObjectClass.CANONICAL, CanonicalStorageSubtype.DOMAIN),
        StorageFamilyClassification("paragraph", StorageObjectClass.CANONICAL, CanonicalStorageSubtype.DOMAIN),
        StorageFamilyClassification("claim_verification_issue", StorageObjectClass.CANONICAL, CanonicalStorageSubtype.DOMAIN),
        StorageFamilyClassification("research_round_progress", StorageObjectClass.CANONICAL, CanonicalStorageSubtype.DOMAIN),
        StorageFamilyClassification("gate_control", StorageObjectClass.CANONICAL, CanonicalStorageSubtype.CONTROL),
        StorageFamilyClassification("canonical_budget", StorageObjectClass.CANONICAL, CanonicalStorageSubtype.CONTROL),
        StorageFamilyClassification("post_write_release_integrity", StorageObjectClass.CANONICAL, CanonicalStorageSubtype.CONTROL),
        StorageFamilyClassification("durable_memory_record", StorageObjectClass.PROJECTION),
        StorageFamilyClassification("memory_selector_index", StorageObjectClass.PROJECTION),
        StorageFamilyClassification("generation_report", StorageObjectClass.PROJECTION),
        StorageFamilyClassification("post_write_validated_output", StorageObjectClass.PROJECTION),
        StorageFamilyClassification("langgraph_checkpoint", StorageObjectClass.RECOVERY),
        StorageFamilyClassification("langgraph_checkpoint_writes", StorageObjectClass.RECOVERY),
        StorageFamilyClassification("recovery_journal", StorageObjectClass.RECOVERY),
        StorageFamilyClassification("run_trace_event", StorageObjectClass.AUDIT),
        StorageFamilyClassification("data_capture_record", StorageObjectClass.AUDIT),
        StorageFamilyClassification("raw_model_capture", StorageObjectClass.AUDIT),
        StorageFamilyClassification("raw_tool_capture", StorageObjectClass.AUDIT),
        StorageFamilyClassification("context_replay_capture", StorageObjectClass.AUDIT),
        StorageFamilyClassification("eval_result", StorageObjectClass.AUDIT),
        StorageFamilyClassification("agent_candidate", StorageObjectClass.EPHEMERAL, durable_by_default=False),
        StorageFamilyClassification("context_package", StorageObjectClass.EPHEMERAL, durable_by_default=False),
        StorageFamilyClassification("temp_file_lock_buffer", StorageObjectClass.EPHEMERAL, durable_by_default=False),
    )
)


class GCRevalidationState(str, Enum):
    ELIGIBLE = "ELIGIBLE"
    REVOKED = "REVOKED"
    ALREADY_ABSENT = "ALREADY_ABSENT"
    BLOCKED = "BLOCKED"


class GCDeletionState(str, Enum):
    DELETED = "DELETED"
    SKIPPED = "SKIPPED"
    ALREADY_ABSENT = "ALREADY_ABSENT"
    FAILED = "FAILED"


@dataclass(frozen=True)
class StorageReference:
    target_ref: str
    kind: StorageReferenceKind

    def __post_init__(self) -> None:
        if not str(self.target_ref or "").strip():
            raise ValueError("storage reference target_ref cannot be blank")


@dataclass(frozen=True)
class StorageObjectRecord:
    object_ref: str
    object_family: str
    storage_class: StorageObjectClass
    retention_class: RetentionClass
    scope_ref: str
    canonical_subtype: CanonicalStorageSubtype | None = None
    created_at_epoch: float = 0.0
    retention_anchor_epoch: float | None = None
    retention_expires_at_epoch: float | None = None
    managed_by_local_sif13: bool = True
    current_protected: bool = False
    recovery_protected: bool = False
    release_protected: bool = False
    explicitly_pinned: bool = False
    investigation_or_reconciliation_held: bool = False
    references: tuple[StorageReference, ...] = ()
    estimated_bytes: int = 0
    physical_identity: str | None = None
    metadata: dict[str, object] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not str(self.object_ref or "").strip():
            raise ValueError("object_ref cannot be blank")
        if not str(self.object_family or "").strip():
            raise ValueError("object_family cannot be blank")
        if not str(self.scope_ref or "").strip():
            raise ValueError("scope_ref cannot be blank")
        if self.estimated_bytes < 0:
            raise ValueError("estimated_bytes cannot be negative")
        if (
            self.storage_class is StorageObjectClass.CANONICAL
            and self.canonical_subtype is None
        ):
            raise ValueError("CANONICAL object requires canonical_subtype")
        if (
            self.storage_class is not StorageObjectClass.CANONICAL
            and self.canonical_subtype is not None
        ):
            raise ValueError("non-CANONICAL object cannot carry canonical_subtype")
        if (
            self.retention_class is RetentionClass.R0_PROTECTED
            and self.retention_expires_at_epoch is not None
        ):
            raise ValueError("R0 protection cannot expire by age")


class StorageFamilyAdapter(Protocol):
    def enumerate_objects(self, scope_ref: str) -> Sequence[StorageObjectRecord]: ...
    def resolve_object(self, object_ref: str) -> StorageObjectRecord | None: ...
    def resolve_protected_roots(self, scope_ref: str) -> Sequence[str]: ...
    def can_delete(self, record: StorageObjectRecord) -> bool: ...
    def delete_object_group(self, record: StorageObjectRecord) -> int: ...


class StorageCatalog(Protocol):
    """Owner-supplied exact storage inventory/capability boundary."""

    def scope_is_terminal_or_gc_closed(self, scope_ref: str) -> bool: ...

    def enumerate_objects(self, scope_ref: str) -> Sequence[StorageObjectRecord]: ...

    def resolve_object(self, object_ref: str) -> StorageObjectRecord | None: ...

    def resolve_protected_roots(self, scope_ref: str) -> Sequence[str]: ...

    def can_delete(self, record: StorageObjectRecord) -> bool: ...

    def delete_object_group(self, record: StorageObjectRecord) -> int: ...


class CompositeStorageCatalog:
    """Compose read/delete capabilities without becoming business authority."""

    def __init__(
        self,
        *,
        scope_terminal_resolver: Callable[[str], bool],
        adapters: Iterable[StorageFamilyAdapter] = (),
    ) -> None:
        if not callable(scope_terminal_resolver):
            raise TypeError("scope_terminal_resolver must be callable")
        self.scope_terminal_resolver = scope_terminal_resolver
        self.adapters = tuple(adapters)

    def scope_is_terminal_or_gc_closed(self, scope_ref: str) -> bool:
        return bool(self.scope_terminal_resolver(scope_ref))

    def enumerate_objects(self, scope_ref: str) -> tuple[StorageObjectRecord, ...]:
        objects: list[StorageObjectRecord] = []
        for adapter in self.adapters:
            objects.extend(adapter.enumerate_objects(scope_ref))
        refs = [item.object_ref for item in objects]
        if len(refs) != len(set(refs)):
            raise StorageIntegrityError(
                "multiple storage adapters claim the same object_ref"
            )
        return tuple(sorted(objects, key=lambda item: item.object_ref))

    def resolve_object(self, object_ref: str) -> StorageObjectRecord | None:
        matches = [
            item
            for adapter in self.adapters
            if (item := adapter.resolve_object(object_ref)) is not None
        ]
        if len(matches) > 1:
            raise StorageIntegrityError(
                "multiple storage adapters resolve the same object_ref"
            )
        return matches[0] if matches else None

    def resolve_protected_roots(self, scope_ref: str) -> tuple[str, ...]:
        roots: list[str] = []
        for adapter in self.adapters:
            roots.extend(adapter.resolve_protected_roots(scope_ref))
        if len(roots) != len(set(roots)):
            raise StorageIntegrityError("protected root identity is duplicated")
        return tuple(sorted(roots))

    def _adapter_for(self, object_ref: str) -> StorageFamilyAdapter:
        matches = [
            adapter
            for adapter in self.adapters
            if adapter.resolve_object(object_ref) is not None
        ]
        if len(matches) != 1:
            raise StorageIntegrityError(
                "exact object does not have one physical owner adapter"
            )
        return matches[0]

    def can_delete(self, record: StorageObjectRecord) -> bool:
        return self._adapter_for(record.object_ref).can_delete(record)

    def delete_object_group(self, record: StorageObjectRecord) -> int:
        return self._adapter_for(record.object_ref).delete_object_group(record)


class FileArtifactStorageAdapter:
    """Map one FileArtifactStore into SIF-13 through caller-owned semantics."""

    def __init__(
        self,
        store: FileArtifactStore,
        *,
        object_family: str,
        storage_class: StorageObjectClass,
        retention_class_resolver: Callable[[ArtifactReferenceSchema], RetentionClass],
        scope_ref_resolver: Callable[[ArtifactReferenceSchema], str],
        retention_expires_resolver: Callable[[ArtifactReferenceSchema], float | None],
        protection_resolver: Callable[[ArtifactReferenceSchema], dict[str, bool]] | None = None,
        references_resolver: Callable[[ArtifactReferenceSchema], Sequence[StorageReference]] | None = None,
        canonical_subtype: CanonicalStorageSubtype | None = None,
        managed_by_local_sif13: bool = True,
        delete_supported: bool = True,
    ) -> None:
        self.store = store
        self.object_family = str(object_family or "").strip()
        if not self.object_family:
            raise ValueError("object_family cannot be blank")
        self.storage_class = storage_class
        self.retention_class_resolver = retention_class_resolver
        self.scope_ref_resolver = scope_ref_resolver
        self.retention_expires_resolver = retention_expires_resolver
        self.protection_resolver = protection_resolver or (lambda _ref: {})
        self.references_resolver = references_resolver or (lambda _ref: ())
        self.canonical_subtype = canonical_subtype
        self.managed_by_local_sif13 = bool(managed_by_local_sif13)
        self.delete_supported = bool(delete_supported)

    def _record(self, reference: ArtifactReferenceSchema) -> StorageObjectRecord:
        protection = dict(self.protection_resolver(reference) or {})
        return StorageObjectRecord(
            object_ref=reference.artifact_ref,
            object_family=self.object_family,
            storage_class=self.storage_class,
            canonical_subtype=self.canonical_subtype,
            retention_class=self.retention_class_resolver(reference),
            scope_ref=self.scope_ref_resolver(reference),
            retention_expires_at_epoch=self.retention_expires_resolver(reference),
            managed_by_local_sif13=self.managed_by_local_sif13,
            current_protected=bool(protection.get("current_protected", False)),
            recovery_protected=bool(protection.get("recovery_protected", False)),
            release_protected=bool(protection.get("release_protected", False)),
            explicitly_pinned=bool(protection.get("explicitly_pinned", False)),
            investigation_or_reconciliation_held=bool(
                protection.get("investigation_or_reconciliation_held", False)
            ),
            references=tuple(self.references_resolver(reference)),
            estimated_bytes=reference.size_bytes,
            physical_identity=reference.content_hash,
        )

    def enumerate_objects(self, scope_ref: str) -> tuple[StorageObjectRecord, ...]:
        return tuple(
            self._record(reference)
            for reference in self.store.list_references()
            if self.scope_ref_resolver(reference) == scope_ref
        )

    def resolve_object(self, object_ref: str) -> StorageObjectRecord | None:
        for reference in self.store.list_references():
            if reference.artifact_ref == object_ref:
                return self._record(reference)
        return None

    def resolve_protected_roots(self, scope_ref: str) -> tuple[str, ...]:
        return tuple(
            record.object_ref
            for record in self.enumerate_objects(scope_ref)
            if (
                record.retention_class is RetentionClass.R0_PROTECTED
                or record.current_protected
                or record.recovery_protected
                or record.release_protected
                or record.explicitly_pinned
                or record.investigation_or_reconciliation_held
            )
        )

    def can_delete(self, record: StorageObjectRecord) -> bool:
        return self.delete_supported and self.managed_by_local_sif13

    def delete_object_group(self, record: StorageObjectRecord) -> int:
        if not self.can_delete(record):
            raise StorageLifecycleError("FileArtifactStore object delete is not authorized")
        if not record.physical_identity:
            raise StorageIntegrityError("FileArtifactStore object lacks exact content hash")
        return self.store.delete_exact(
            record.object_ref,
            expected_content_hash=record.physical_identity,
        )


class SQLiteCheckpointStorageAdapter:
    """Object-aware adapter for official LangGraph SQLite checkpoints.

    One semantic checkpoint deletion group is exactly one checkpoint row plus
    its owned ``writes`` rows.  Parent/history edges are WEAK.  The adapter
    never calls ``delete_thread`` and never treats file mtime as authority.
    """

    CHECKPOINT_PREFIX = "checkpoint://sha256/"
    WRITES_PREFIX = "checkpoint-writes://sha256/"

    def __init__(
        self,
        path: str | Path,
        *,
        scope_terminal_resolver: Callable[[str], bool],
        pinned_checkpoint_resolver: Callable[[str, str, str], bool] | None = None,
    ) -> None:
        self.path = Path(path).expanduser().resolve()
        if not callable(scope_terminal_resolver):
            raise TypeError("scope_terminal_resolver must be callable")
        self.scope_terminal_resolver = scope_terminal_resolver
        self.pinned_checkpoint_resolver = (
            pinned_checkpoint_resolver or (lambda _thread, _ns, _checkpoint: False)
        )

    @staticmethod
    def _scope(thread_id: str) -> str:
        return f"run:{thread_id}"

    @classmethod
    def _checkpoint_ref(cls, thread_id: str, namespace: str, checkpoint_id: str) -> str:
        digest = hashlib.sha256(
            json.dumps(
                [thread_id, namespace, checkpoint_id],
                ensure_ascii=False,
                separators=(",", ":"),
            ).encode("utf-8")
        ).hexdigest()
        return cls.CHECKPOINT_PREFIX + digest

    @classmethod
    def _writes_ref(cls, thread_id: str, namespace: str, checkpoint_id: str) -> str:
        digest = hashlib.sha256(
            json.dumps(
                [thread_id, namespace, checkpoint_id, "writes"],
                ensure_ascii=False,
                separators=(",", ":"),
            ).encode("utf-8")
        ).hexdigest()
        return cls.WRITES_PREFIX + digest

    def _rows(self, thread_id: str | None = None) -> list[sqlite3.Row]:
        if not self.path.is_file():
            return []
        connection = sqlite3.connect(str(self.path))
        connection.row_factory = sqlite3.Row
        try:
            where = " WHERE thread_id = ?" if thread_id is not None else ""
            params = (thread_id,) if thread_id is not None else ()
            return list(
                connection.execute(
                    "SELECT thread_id, checkpoint_ns, checkpoint_id, "
                    "parent_checkpoint_id, type, length(checkpoint) AS checkpoint_bytes, "
                    "length(metadata) AS metadata_bytes "
                    "FROM checkpoints" + where + " "
                    "ORDER BY thread_id, checkpoint_ns, checkpoint_id",
                    params,
                )
            )
        finally:
            connection.close()

    def _write_stats(
        self,
        thread_id: str,
        namespace: str,
        checkpoint_id: str,
    ) -> tuple[int, int]:
        connection = sqlite3.connect(str(self.path))
        try:
            row = connection.execute(
                "SELECT COUNT(*), COALESCE(SUM(length(value)), 0) FROM writes "
                "WHERE thread_id=? AND checkpoint_ns=? AND checkpoint_id=?",
                (thread_id, namespace, checkpoint_id),
            ).fetchone()
            return int(row[0]), int(row[1])
        finally:
            connection.close()

    def _records_for_row(self, row: sqlite3.Row) -> tuple[StorageObjectRecord, ...]:
        thread_id = str(row["thread_id"])
        namespace = str(row["checkpoint_ns"])
        checkpoint_id = str(row["checkpoint_id"])
        scope_ref = self._scope(thread_id)
        terminal = bool(self.scope_terminal_resolver(scope_ref))
        pinned = bool(
            self.pinned_checkpoint_resolver(thread_id, namespace, checkpoint_id)
        )
        checkpoint_ref = self._checkpoint_ref(thread_id, namespace, checkpoint_id)
        write_count, write_bytes = self._write_stats(thread_id, namespace, checkpoint_id)
        references: list[StorageReference] = []
        parent_id = row["parent_checkpoint_id"]
        if parent_id:
            references.append(
                StorageReference(
                    self._checkpoint_ref(thread_id, namespace, str(parent_id)),
                    StorageReferenceKind.WEAK_PROVENANCE,
                )
            )
        writes_ref = self._writes_ref(thread_id, namespace, checkpoint_id)
        if write_count:
            references.append(
                StorageReference(writes_ref, StorageReferenceKind.OWNED_CHILD)
            )
        checkpoint_record = StorageObjectRecord(
            object_ref=checkpoint_ref,
            object_family="langgraph_checkpoint",
            storage_class=StorageObjectClass.RECOVERY,
            retention_class=RetentionClass.R2_RECOVERY_WINDOW,
            scope_ref=scope_ref,
            # Active/recoverable scope is conservatively protected.  Wave14
            # never destructively GCs active scope, so over-retention is safe.
            recovery_protected=not terminal,
            explicitly_pinned=pinned,
            references=tuple(references),
            estimated_bytes=int(row["checkpoint_bytes"] or 0)
            + int(row["metadata_bytes"] or 0)
            + write_bytes,
            physical_identity=json.dumps(
                [thread_id, namespace, checkpoint_id],
                ensure_ascii=False,
                separators=(",", ":"),
            ),
            # R2 hold closes semantically when the owning Run is terminal;
            # this is not file-age/mtime retention.
            retention_expires_at_epoch=(0.0 if terminal else None),
            metadata={
                "thread_id": thread_id,
                "checkpoint_ns": namespace,
                "checkpoint_id": checkpoint_id,
                "parent_checkpoint_id": (
                    str(parent_id) if parent_id is not None else None
                ),
                "write_count": write_count,
            },
        )
        if not write_count:
            return (checkpoint_record,)
        writes_record = StorageObjectRecord(
            object_ref=writes_ref,
            object_family="langgraph_checkpoint_writes",
            storage_class=StorageObjectClass.RECOVERY,
            retention_class=RetentionClass.R2_RECOVERY_WINDOW,
            scope_ref=scope_ref,
            # Writes are protected only through the checkpoint OWNED_CHILD
            # edge; they are not independent recovery roots.
            recovery_protected=False,
            explicitly_pinned=False,
            estimated_bytes=write_bytes,
            physical_identity=json.dumps(
                [thread_id, namespace, checkpoint_id, "writes"],
                ensure_ascii=False,
                separators=(",", ":"),
            ),
            retention_expires_at_epoch=(0.0 if terminal else None),
            metadata={
                "thread_id": thread_id,
                "checkpoint_ns": namespace,
                "checkpoint_id": checkpoint_id,
                "write_count": write_count,
                "owned_by_checkpoint_ref": checkpoint_ref,
            },
        )
        return checkpoint_record, writes_record

    def enumerate_objects(self, scope_ref: str) -> tuple[StorageObjectRecord, ...]:
        prefix = "run:"
        raw = str(scope_ref or "")
        if not raw.startswith(prefix) or not raw[len(prefix):]:
            return ()
        records: list[StorageObjectRecord] = []
        for row in self._rows(raw[len(prefix):]):
            records.extend(self._records_for_row(row))
        return tuple(records)

    def resolve_object(self, object_ref: str) -> StorageObjectRecord | None:
        for row in self._rows():
            for record in self._records_for_row(row):
                if record.object_ref == object_ref:
                    return record
        return None

    def resolve_protected_roots(self, scope_ref: str) -> tuple[str, ...]:
        return tuple(
            record.object_ref
            for record in self.enumerate_objects(scope_ref)
            if record.recovery_protected or record.explicitly_pinned
        )

    def can_delete(self, record: StorageObjectRecord) -> bool:
        # writes are deleted only as the parent's owned semantic group.
        return record.object_family == "langgraph_checkpoint"

    def delete_object_group(self, record: StorageObjectRecord) -> int:
        if record.object_family != "langgraph_checkpoint":
            raise StorageLifecycleError(
                "checkpoint writes may only be deleted through checkpoint owner group"
            )
        thread_id = str(record.metadata.get("thread_id") or "")
        namespace = str(record.metadata.get("checkpoint_ns") or "")
        checkpoint_id = str(record.metadata.get("checkpoint_id") or "")
        expected_ref = self._checkpoint_ref(thread_id, namespace, checkpoint_id)
        if expected_ref != record.object_ref:
            raise StorageIntegrityError("checkpoint physical identity changed")
        if not self.path.is_file():
            return 0
        connection = sqlite3.connect(str(self.path), isolation_level=None)
        try:
            connection.execute("PRAGMA busy_timeout = 5000")
            connection.execute("BEGIN IMMEDIATE")
            current = connection.execute(
                "SELECT length(checkpoint), length(metadata) FROM checkpoints "
                "WHERE thread_id=? AND checkpoint_ns=? AND checkpoint_id=?",
                (thread_id, namespace, checkpoint_id),
            ).fetchone()
            if current is None:
                connection.rollback()
                return 0
            writes = connection.execute(
                "SELECT COALESCE(SUM(length(value)), 0) FROM writes "
                "WHERE thread_id=? AND checkpoint_ns=? AND checkpoint_id=?",
                (thread_id, namespace, checkpoint_id),
            ).fetchone()
            bytes_freed = int(current[0] or 0) + int(current[1] or 0) + int(writes[0] or 0)
            connection.execute(
                "DELETE FROM checkpoints WHERE thread_id=? AND checkpoint_ns=? AND checkpoint_id=?",
                (thread_id, namespace, checkpoint_id),
            )
            connection.execute(
                "DELETE FROM writes WHERE thread_id=? AND checkpoint_ns=? AND checkpoint_id=?",
                (thread_id, namespace, checkpoint_id),
            )
            connection.commit()
            return bytes_freed
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()


@dataclass(frozen=True)
class ReachabilityGraph:
    scope_ref: str
    root_refs: tuple[str, ...]
    strongly_reachable_refs: tuple[str, ...]
    weak_edges: tuple[tuple[str, str], ...]
    external_edges: tuple[tuple[str, str], ...]
    unresolved_strong_edges: tuple[tuple[str, str], ...]

    @property
    def integrity_ok(self) -> bool:
        return not self.unresolved_strong_edges


@dataclass(frozen=True)
class GCCandidate:
    object_ref: str
    object_family: str
    storage_class: StorageObjectClass
    retention_class: RetentionClass
    scope_ref: str
    planned_record_fingerprint: str
    eligible: bool
    eligibility_reasons: tuple[str, ...]
    weak_parent_refs: tuple[str, ...]
    planned_owned_children: tuple[str, ...]
    estimated_bytes: int


@dataclass(frozen=True)
class GCPlan:
    plan_id: str
    policy_version: str
    scope_ref: str
    planned_at_epoch: float
    terminal_or_gc_closed: bool
    graph: ReachabilityGraph
    candidates: tuple[GCCandidate, ...]
    blocked_reasons: tuple[str, ...]

    @property
    def destructive_allowed(self) -> bool:
        return self.terminal_or_gc_closed and not self.blocked_reasons


@dataclass(frozen=True)
class GCRevalidationResult:
    candidate: GCCandidate
    state: GCRevalidationState
    reasons: tuple[str, ...]
    current_record: StorageObjectRecord | None = None


@dataclass(frozen=True)
class GCDeletionResult:
    object_ref: str
    state: GCDeletionState
    bytes_freed: int = 0
    reasons: tuple[str, ...] = ()


@dataclass(frozen=True)
class GCRunAudit:
    gc_run_id: str
    policy_version: str
    scope_ref: str
    dry_run: bool
    planned_count: int
    eligible_count: int
    deleted_count: int
    skipped_after_revalidation_count: int
    failed_count: int
    bytes_estimated: int
    bytes_freed: int
    blocked_reasons: tuple[str, ...]
    deletion_results: tuple[GCDeletionResult, ...]
    started_at_epoch: float
    finished_at_epoch: float


class StorageLifecycleService:
    """SIF-13 PLAN -> REVALIDATE -> DELETE coordinator."""

    STRONG_KINDS = frozenset(
        {
            StorageReferenceKind.STRONG_PROTECT,
            StorageReferenceKind.OWNED_CHILD,
        }
    )

    def __init__(
        self,
        catalog: StorageCatalog,
        *,
        family_registry: StorageFamilyRegistry = FROZEN_STORAGE_FAMILY_REGISTRY,
        policy_version: str = "sif13-storage-v1",
        clock=time,
    ) -> None:
        self.catalog = catalog
        self.family_registry = family_registry
        self.policy_version = str(policy_version or "").strip()
        if not self.policy_version:
            raise ValueError("policy_version cannot be blank")
        self.clock = clock

    @staticmethod
    def _fingerprint(record: StorageObjectRecord) -> str:
        payload = {
            "object_ref": record.object_ref,
            "object_family": record.object_family,
            "storage_class": record.storage_class.value,
            "canonical_subtype": (
                record.canonical_subtype.value if record.canonical_subtype else None
            ),
            "retention_class": record.retention_class.value,
            "scope_ref": record.scope_ref,
            "retention_anchor_epoch": record.retention_anchor_epoch,
            "retention_expires_at_epoch": record.retention_expires_at_epoch,
            "managed_by_local_sif13": record.managed_by_local_sif13,
            "current_protected": record.current_protected,
            "recovery_protected": record.recovery_protected,
            "release_protected": record.release_protected,
            "explicitly_pinned": record.explicitly_pinned,
            "investigation_or_reconciliation_held": (
                record.investigation_or_reconciliation_held
            ),
            "references": [
                {"target_ref": item.target_ref, "kind": item.kind.value}
                for item in record.references
            ],
            "physical_identity": record.physical_identity,
        }
        encoded = json.dumps(
            payload,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
        ).encode("utf-8")
        return hashlib.sha256(encoded).hexdigest()

    def classify_family(
        self,
        object_family: str,
    ) -> tuple[StorageObjectClass, CanonicalStorageSubtype | None]:
        entry = self.family_registry.resolve(object_family)
        return entry.storage_class, entry.canonical_subtype

    def managed_family_inventory(self) -> tuple[StorageFamilyClassification, ...]:
        return self.family_registry.all()

    def _validate_record_classification(self, record: StorageObjectRecord) -> None:
        frozen = self.family_registry.resolve(record.object_family)
        if (
            frozen.storage_class is not record.storage_class
            or frozen.canonical_subtype is not record.canonical_subtype
        ):
            raise StorageIntegrityError(
                "physical adapter classification conflicts with frozen family registry"
            )

    def _validated_scope_records(
        self,
        scope_ref: str,
    ) -> dict[str, StorageObjectRecord]:
        raw_records = tuple(self.catalog.enumerate_objects(scope_ref))
        refs = [item.object_ref for item in raw_records]
        if len(refs) != len(set(refs)):
            raise StorageIntegrityError(
                "storage catalog returned duplicate object refs for one scope"
            )
        records: dict[str, StorageObjectRecord] = {}
        for record in raw_records:
            if record.scope_ref != scope_ref:
                raise StorageIntegrityError(
                    "storage adapter returned object outside requested scope"
                )
            self._validate_record_classification(record)
            records[record.object_ref] = record
        return records

    def classify_object(
        self,
        object_ref: str,
    ) -> tuple[StorageObjectClass, CanonicalStorageSubtype | None, RetentionClass]:
        record = self.catalog.resolve_object(object_ref)
        if record is None:
            raise StorageIntegrityError(
                f"managed storage object does not resolve exactly: {object_ref}"
            )
        self._validate_record_classification(record)
        return (
            record.storage_class,
            record.canonical_subtype,
            record.retention_class,
        )

    def resolve_protected_roots(self, scope_ref: str) -> tuple[str, ...]:
        records = self._validated_scope_records(scope_ref)
        explicit = set(self.catalog.resolve_protected_roots(scope_ref))
        for record in records.values():
            if (
                record.retention_class is RetentionClass.R0_PROTECTED
                or record.current_protected
                or record.recovery_protected
                or record.release_protected
                or record.explicitly_pinned
                or record.investigation_or_reconciliation_held
            ):
                explicit.add(record.object_ref)
        missing = sorted(ref for ref in explicit if ref not in records)
        if missing:
            raise StorageIntegrityError(
                "protected root does not resolve in local catalog: "
                + ", ".join(missing)
            )
        return tuple(sorted(explicit))

    def build_reachability_graph(self, scope_ref: str) -> ReachabilityGraph:
        records = self._validated_scope_records(scope_ref)
        roots = self.resolve_protected_roots(scope_ref)
        reachable: set[str] = set()
        weak_edges: set[tuple[str, str]] = set()
        external_edges: set[tuple[str, str]] = set()
        unresolved: set[tuple[str, str]] = set()
        pending = list(roots)
        while pending:
            parent_ref = pending.pop()
            if parent_ref in reachable:
                continue
            parent = records.get(parent_ref)
            if parent is None:
                unresolved.add(("<root>", parent_ref))
                continue
            reachable.add(parent_ref)
            for edge in parent.references:
                if edge.kind is StorageReferenceKind.WEAK_PROVENANCE:
                    weak_edges.add((parent_ref, edge.target_ref))
                    continue
                if edge.kind is StorageReferenceKind.EXTERNAL_REF:
                    external_edges.add((parent_ref, edge.target_ref))
                    continue
                if edge.kind in self.STRONG_KINDS:
                    if edge.target_ref not in records:
                        unresolved.add((parent_ref, edge.target_ref))
                    else:
                        pending.append(edge.target_ref)
        return ReachabilityGraph(
            scope_ref=scope_ref,
            root_refs=roots,
            strongly_reachable_refs=tuple(sorted(reachable)),
            weak_edges=tuple(sorted(weak_edges)),
            external_edges=tuple(sorted(external_edges)),
            unresolved_strong_edges=tuple(sorted(unresolved)),
        )

    @staticmethod
    def _retention_expired(record: StorageObjectRecord, now_epoch: float) -> bool:
        if record.retention_class is RetentionClass.R0_PROTECTED:
            return False
        if record.retention_class is RetentionClass.R4_DISPOSABLE:
            return (
                record.retention_expires_at_epoch is None
                or record.retention_expires_at_epoch <= now_epoch
            )
        return (
            record.retention_expires_at_epoch is not None
            and record.retention_expires_at_epoch <= now_epoch
        )

    def _candidate_for(
        self,
        record: StorageObjectRecord,
        *,
        now_epoch: float,
        graph: ReachabilityGraph,
        terminal_or_gc_closed: bool,
        records: dict[str, StorageObjectRecord],
    ) -> GCCandidate:
        reasons: list[str] = []
        eligible = True
        if not record.managed_by_local_sif13:
            eligible = False
            reasons.append("EXTERNAL_OR_UNMANAGED")
        if not terminal_or_gc_closed:
            eligible = False
            reasons.append("SCOPE_NOT_TERMINAL_OR_GC_CLOSED")
        if record.object_ref in graph.strongly_reachable_refs:
            eligible = False
            reasons.append("STRONGLY_REACHABLE")
        if not self._retention_expired(record, now_epoch):
            eligible = False
            reasons.append("RETENTION_HOLD_ACTIVE")
        if record.explicitly_pinned:
            eligible = False
            reasons.append("EXPLICIT_PIN")
        if record.current_protected:
            eligible = False
            reasons.append("CURRENT_PROTECTED")
        if record.recovery_protected:
            eligible = False
            reasons.append("RECOVERY_PROTECTED")
        if record.release_protected:
            eligible = False
            reasons.append("RELEASE_PROTECTED")
        if record.investigation_or_reconciliation_held:
            eligible = False
            reasons.append("INVESTIGATION_OR_RECONCILIATION_HOLD")
        if graph.unresolved_strong_edges:
            eligible = False
            reasons.append("STRONG_REF_INTEGRITY_BLOCKED")
        owned_parent_refs = sorted(
            parent.object_ref
            for parent in records.values()
            for edge in parent.references
            if edge.kind is StorageReferenceKind.OWNED_CHILD
            and edge.target_ref == record.object_ref
        )
        if owned_parent_refs:
            eligible = False
            reasons.append("OWNED_CHILD_DELETE_THROUGH_PARENT")
        if not self.catalog.can_delete(record):
            eligible = False
            reasons.append("NO_SAFE_OBJECT_DELETE_CAPABILITY")

        weak_parents = sorted(
            parent
            for parent, target in graph.weak_edges
            if target == record.object_ref
        )
        owned_children = sorted(
            edge.target_ref
            for edge in record.references
            if edge.kind is StorageReferenceKind.OWNED_CHILD
            and edge.target_ref in records
        )
        if eligible:
            reasons.append("GC_ELIGIBLE")
        return GCCandidate(
            object_ref=record.object_ref,
            object_family=record.object_family,
            storage_class=record.storage_class,
            retention_class=record.retention_class,
            scope_ref=record.scope_ref,
            planned_record_fingerprint=self._fingerprint(record),
            eligible=eligible,
            eligibility_reasons=tuple(reasons),
            weak_parent_refs=tuple(weak_parents),
            planned_owned_children=tuple(owned_children),
            estimated_bytes=record.estimated_bytes,
        )

    def plan_gc(self, scope_ref: str, now: float | None = None) -> GCPlan:
        now_epoch = float(self.clock() if now is None else now)
        terminal = bool(
            self.catalog.scope_is_terminal_or_gc_closed(scope_ref)
        )
        records = self._validated_scope_records(scope_ref)
        graph = self.build_reachability_graph(scope_ref)
        blocked: list[str] = []
        if not terminal:
            blocked.append("SCOPE_NOT_TERMINAL_OR_GC_CLOSED")
        if graph.unresolved_strong_edges:
            blocked.append("UNRESOLVED_STRONG_REFERENCE")
        candidates = tuple(
            self._candidate_for(
                record,
                now_epoch=now_epoch,
                graph=graph,
                terminal_or_gc_closed=terminal,
                records=records,
            )
            for record in sorted(records.values(), key=lambda item: item.object_ref)
        )
        base = {
            "policy_version": self.policy_version,
            "scope_ref": scope_ref,
            "planned_at_epoch": now_epoch,
            "roots": list(graph.root_refs),
            "object_fingerprints": {
                item.object_ref: self._fingerprint(item)
                for item in sorted(records.values(), key=lambda item: item.object_ref)
            },
        }
        plan_id = "gc-plan://sha256/" + hashlib.sha256(
            json.dumps(base, sort_keys=True, separators=(",", ":")).encode("utf-8")
        ).hexdigest()
        return GCPlan(
            plan_id=plan_id,
            policy_version=self.policy_version,
            scope_ref=scope_ref,
            planned_at_epoch=now_epoch,
            terminal_or_gc_closed=terminal,
            graph=graph,
            candidates=candidates,
            blocked_reasons=tuple(blocked),
        )

    def revalidate_gc_candidate(
        self,
        candidate: GCCandidate,
    ) -> GCRevalidationResult:
        current = self.catalog.resolve_object(candidate.object_ref)
        if current is None:
            return GCRevalidationResult(
                candidate=candidate,
                state=GCRevalidationState.ALREADY_ABSENT,
                reasons=("OBJECT_ALREADY_ABSENT",),
            )
        if current.scope_ref != candidate.scope_ref:
            return GCRevalidationResult(
                candidate=candidate,
                state=GCRevalidationState.BLOCKED,
                reasons=("SCOPE_IDENTITY_CHANGED",),
                current_record=current,
            )
        fresh_plan = self.plan_gc(candidate.scope_ref)
        fresh = next(
            (
                item
                for item in fresh_plan.candidates
                if item.object_ref == candidate.object_ref
            ),
            None,
        )
        if fresh is None:
            return GCRevalidationResult(
                candidate=candidate,
                state=GCRevalidationState.BLOCKED,
                reasons=("OBJECT_NOT_ENUMERATED_BY_OWNER",),
                current_record=current,
            )
        if fresh_plan.blocked_reasons:
            return GCRevalidationResult(
                candidate=candidate,
                state=GCRevalidationState.BLOCKED,
                reasons=fresh_plan.blocked_reasons,
                current_record=current,
            )
        if not fresh.eligible:
            return GCRevalidationResult(
                candidate=candidate,
                state=GCRevalidationState.REVOKED,
                reasons=fresh.eligibility_reasons,
                current_record=current,
            )
        return GCRevalidationResult(
            candidate=fresh,
            state=GCRevalidationState.ELIGIBLE,
            reasons=("REVALIDATED_GC_ELIGIBLE",),
            current_record=current,
        )

    def delete_gc_candidate(self, candidate: GCCandidate) -> GCDeletionResult:
        revalidated = self.revalidate_gc_candidate(candidate)
        if revalidated.state is GCRevalidationState.ALREADY_ABSENT:
            return GCDeletionResult(
                object_ref=candidate.object_ref,
                state=GCDeletionState.ALREADY_ABSENT,
                reasons=revalidated.reasons,
            )
        if revalidated.state is not GCRevalidationState.ELIGIBLE:
            return GCDeletionResult(
                object_ref=candidate.object_ref,
                state=GCDeletionState.SKIPPED,
                reasons=revalidated.reasons,
            )
        current = revalidated.current_record
        if current is None:
            raise StorageIntegrityError(
                "eligible revalidation did not return current object"
            )
        try:
            freed = int(self.catalog.delete_object_group(current))
        except Exception as exc:
            return GCDeletionResult(
                object_ref=candidate.object_ref,
                state=GCDeletionState.FAILED,
                reasons=(f"DELETE_FAILED:{type(exc).__name__}",),
            )
        if freed < 0:
            raise StorageIntegrityError("delete adapter returned negative freed bytes")
        if self.catalog.resolve_object(current.object_ref) is not None:
            return GCDeletionResult(
                object_ref=candidate.object_ref,
                state=GCDeletionState.FAILED,
                reasons=("POST_DELETE_OWNER_STILL_PRESENT",),
            )
        surviving_children = tuple(
            child_ref
            for child_ref in revalidated.candidate.planned_owned_children
            if self.catalog.resolve_object(child_ref) is not None
        )
        if surviving_children:
            return GCDeletionResult(
                object_ref=candidate.object_ref,
                state=GCDeletionState.FAILED,
                bytes_freed=freed,
                reasons=(
                    "POST_DELETE_OWNED_CHILD_STILL_PRESENT:" + ",".join(surviving_children),
                ),
            )
        return GCDeletionResult(
            object_ref=candidate.object_ref,
            state=GCDeletionState.DELETED,
            bytes_freed=freed,
            reasons=("DELETED_AFTER_REVALIDATION_AND_EXACT_POSTCHECK",),
        )

    def run_terminal_scope_gc(
        self,
        scope_ref: str,
        dry_run: bool = True,
    ) -> GCRunAudit:
        started = float(self.clock())
        plan = self.plan_gc(scope_ref, now=started)
        eligible = tuple(item for item in plan.candidates if item.eligible)
        deletion_results: list[GCDeletionResult] = []
        if not dry_run and plan.destructive_allowed:
            for candidate in eligible:
                deletion_results.append(self.delete_gc_candidate(candidate))
        deleted_count = sum(
            item.state is GCDeletionState.DELETED for item in deletion_results
        )
        skipped_count = sum(
            item.state
            in {GCDeletionState.SKIPPED, GCDeletionState.ALREADY_ABSENT}
            for item in deletion_results
        )
        failed_count = sum(
            item.state is GCDeletionState.FAILED for item in deletion_results
        )
        finished = float(self.clock())
        run_base = {
            "policy_version": self.policy_version,
            "scope_ref": scope_ref,
            "plan_id": plan.plan_id,
            "dry_run": bool(dry_run),
            "started_at_epoch": started,
        }
        gc_run_id = "gc-run://sha256/" + hashlib.sha256(
            json.dumps(run_base, sort_keys=True, separators=(",", ":")).encode("utf-8")
        ).hexdigest()
        return GCRunAudit(
            gc_run_id=gc_run_id,
            policy_version=self.policy_version,
            scope_ref=scope_ref,
            dry_run=bool(dry_run),
            planned_count=len(plan.candidates),
            eligible_count=len(eligible),
            deleted_count=deleted_count,
            skipped_after_revalidation_count=skipped_count,
            failed_count=failed_count,
            bytes_estimated=sum(item.estimated_bytes for item in eligible),
            bytes_freed=sum(item.bytes_freed for item in deletion_results),
            blocked_reasons=plan.blocked_reasons,
            deletion_results=tuple(deletion_results),
            started_at_epoch=started,
            finished_at_epoch=finished,
        )


__all__ = [
    "CanonicalStorageSubtype",
    "CompositeStorageCatalog",
    "FROZEN_STORAGE_FAMILY_REGISTRY",
    "FileArtifactStorageAdapter",
    "GCCandidate",
    "GCDeletionResult",
    "GCDeletionState",
    "GCPlan",
    "GCRevalidationResult",
    "GCRevalidationState",
    "GCRunAudit",
    "ReachabilityGraph",
    "RetentionClass",
    "SQLiteCheckpointStorageAdapter",
    "StorageCatalog",
    "StorageFamilyAdapter",
    "StorageFamilyClassification",
    "StorageFamilyRegistry",
    "StorageIntegrityError",
    "StorageLifecycleError",
    "StorageLifecycleService",
    "StorageObjectClass",
    "StorageObjectRecord",
    "StorageReference",
    "StorageReferenceKind",
]
