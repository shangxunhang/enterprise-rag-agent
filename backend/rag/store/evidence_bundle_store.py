"""RAG-owned immutable persistence for canonical EvidenceBundle snapshots.

This store is a physical lowering of the external RAG Evidence authority.  It is
not an Artifact store, Research history, Workflow state, or checkpoint.  The
opaque handle is meaningful only through this resolver; consumers must also
bind and verify the canonical EvidenceBundle SHA-256.
"""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import os
from pathlib import Path
import threading
from uuid import uuid4

from contracts.evidence import evidence_bundle_sha256
from core.hashing import canonical_sha256
from core.persistence.interprocess_lock import InterProcessFileLock
from schemas.rag import EvidenceBundleSchema


_SNAPSHOT_SCHEMA_VERSION = "rag_evidence_bundle_snapshot_v1"
_HANDLE_PREFIX = "rag-evidence-bundle://v1/"


class EvidenceBundleSnapshotStoreError(ValueError):
    """Base immutable EvidenceBundle snapshot-store failure."""


class EvidenceBundleSnapshotIntegrityError(EvidenceBundleSnapshotStoreError):
    """Persisted snapshot bytes, identity, or canonical content are invalid."""


class EvidenceBundleSnapshotNotFound(EvidenceBundleSnapshotStoreError):
    """An opaque canonical EvidenceBundle handle cannot be resolved."""


@dataclass(frozen=True)
class EvidenceBundleSnapshotRef:
    """Exact immutable RAG source binding material for downstream contracts."""

    handle: str
    evidence_bundle_sha256: str


class EvidenceBundleSnapshotStore:
    """Durable content-addressed EvidenceBundle snapshots with exact replay."""

    _LOCKS_GUARD = threading.Lock()
    _PATH_LOCKS: dict[str, threading.RLock] = {}

    def __init__(self, root: str | Path) -> None:
        self.root = Path(root).expanduser().resolve()
        self.root.mkdir(parents=True, exist_ok=True)
        self.lock_path = self.root / ".evidence-bundle-store.lock"
        key = str(self.root)
        with self._LOCKS_GUARD:
            self._lock = self._PATH_LOCKS.setdefault(key, threading.RLock())

    @staticmethod
    def _bundle_hash(bundle: EvidenceBundleSchema) -> str:
        return "sha256:" + evidence_bundle_sha256(bundle)

    @classmethod
    def _handle_for_bundle(cls, bundle: EvidenceBundleSchema) -> str:
        digest = canonical_sha256(
            {
                "snapshot_contract": _SNAPSHOT_SCHEMA_VERSION,
                "task_id": bundle.task_id,
                "run_id": bundle.run_id,
                "evidence_bundle_sha256": cls._bundle_hash(bundle),
            }
        )
        return _HANDLE_PREFIX + digest

    def _path_for_handle(self, handle: str) -> Path:
        normalized = str(handle or "").strip()
        if not normalized.startswith(_HANDLE_PREFIX):
            raise EvidenceBundleSnapshotIntegrityError(
                "invalid canonical EvidenceBundle handle"
            )
        opaque_id = normalized[len(_HANDLE_PREFIX) :]
        if len(opaque_id) != 64 or any(
            character not in "0123456789abcdef" for character in opaque_id
        ):
            raise EvidenceBundleSnapshotIntegrityError(
                "invalid canonical EvidenceBundle handle"
            )
        filename = hashlib.sha256(normalized.encode("utf-8")).hexdigest()
        return self.root / filename[:2] / f"{filename}.json"

    @staticmethod
    def _serialize(
        *,
        handle: str,
        bundle_hash: str,
        bundle: EvidenceBundleSchema,
    ) -> str:
        return (
            json.dumps(
                {
                    "schema_version": _SNAPSHOT_SCHEMA_VERSION,
                    "handle": handle,
                    "evidence_bundle_sha256": bundle_hash,
                    "bundle": bundle.model_dump(mode="json", round_trip=True),
                },
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            )
            + "\n"
        )

    def _read_snapshot(
        self,
        path: Path,
        *,
        expected_handle: str,
    ) -> tuple[EvidenceBundleSnapshotRef, EvidenceBundleSchema]:
        if not path.is_file():
            raise EvidenceBundleSnapshotNotFound(
                f"canonical EvidenceBundle snapshot does not exist: {expected_handle}"
            )
        try:
            raw = path.read_text(encoding="utf-8")
            if not raw.endswith("\n"):
                raise EvidenceBundleSnapshotIntegrityError(
                    "EvidenceBundle snapshot is not atomically complete"
                )
            envelope = json.loads(raw)
        except EvidenceBundleSnapshotIntegrityError:
            raise
        except Exception as exc:
            raise EvidenceBundleSnapshotIntegrityError(
                "EvidenceBundle snapshot envelope is invalid"
            ) from exc
        if not isinstance(envelope, dict):
            raise EvidenceBundleSnapshotIntegrityError(
                "EvidenceBundle snapshot envelope must be an object"
            )
        if envelope.get("schema_version") != _SNAPSHOT_SCHEMA_VERSION:
            raise EvidenceBundleSnapshotIntegrityError(
                "unknown EvidenceBundle snapshot schema version"
            )
        handle = str(envelope.get("handle") or "").strip()
        declared_hash = str(envelope.get("evidence_bundle_sha256") or "").strip()
        if handle != expected_handle:
            raise EvidenceBundleSnapshotIntegrityError(
                "EvidenceBundle snapshot handle identity mismatch"
            )
        try:
            bundle = EvidenceBundleSchema.model_validate(envelope.get("bundle"))
        except Exception as exc:
            raise EvidenceBundleSnapshotIntegrityError(
                "persisted EvidenceBundle payload is invalid"
            ) from exc
        actual_hash = self._bundle_hash(bundle)
        if declared_hash != actual_hash:
            raise EvidenceBundleSnapshotIntegrityError(
                "persisted EvidenceBundle canonical hash mismatch"
            )
        derived_handle = self._handle_for_bundle(bundle)
        if derived_handle != handle:
            raise EvidenceBundleSnapshotIntegrityError(
                "persisted EvidenceBundle handle/content identity mismatch"
            )
        return (
            EvidenceBundleSnapshotRef(
                handle=handle,
                evidence_bundle_sha256=actual_hash,
            ),
            bundle,
        )

    def persist(
        self,
        bundle: EvidenceBundleSchema,
    ) -> EvidenceBundleSnapshotRef:
        """Durably publish one canonical immutable snapshot or reuse the exact one."""

        canonical = EvidenceBundleSchema.model_validate(
            bundle.model_dump(mode="python", round_trip=True)
        )
        handle = self._handle_for_bundle(canonical)
        bundle_hash = self._bundle_hash(canonical)
        ref = EvidenceBundleSnapshotRef(
            handle=handle,
            evidence_bundle_sha256=bundle_hash,
        )
        path = self._path_for_handle(handle)
        serialized = self._serialize(
            handle=handle,
            bundle_hash=bundle_hash,
            bundle=canonical,
        )

        with self._lock, InterProcessFileLock(self.lock_path):
            if path.exists():
                existing_ref, existing_bundle = self._read_snapshot(
                    path,
                    expected_handle=handle,
                )
                if existing_ref != ref or existing_bundle != canonical:
                    raise EvidenceBundleSnapshotIntegrityError(
                        "EvidenceBundle handle already resolves different canonical content"
                    )
                return existing_ref

            path.parent.mkdir(parents=True, exist_ok=True)
            temporary = path.with_name(f".{path.name}.{uuid4().hex}.tmp")
            try:
                temporary.write_text(serialized, encoding="utf-8")
                with temporary.open("r+b") as file_handle:
                    os.fsync(file_handle.fileno())
                os.replace(temporary, path)
                # Verify the durable source before exposing its handle.
                stored_ref, stored_bundle = self._read_snapshot(
                    path,
                    expected_handle=handle,
                )
                if stored_ref != ref or stored_bundle != canonical:
                    raise EvidenceBundleSnapshotIntegrityError(
                        "EvidenceBundle durable readback verification failed"
                    )
            finally:
                if temporary.exists():
                    temporary.unlink()
        return ref

    def resolve(self, handle: str) -> EvidenceBundleSchema:
        """Resolve exactly one immutable canonical EvidenceBundle by opaque handle."""

        normalized = str(handle or "").strip()
        path = self._path_for_handle(normalized)
        with self._lock, InterProcessFileLock(self.lock_path):
            _ref, bundle = self._read_snapshot(
                path,
                expected_handle=normalized,
            )
        return bundle.model_copy(deep=True)

    def resolve_ref(self, handle: str) -> EvidenceBundleSnapshotRef:
        """Resolve and verify only the exact durable source binding metadata."""

        normalized = str(handle or "").strip()
        path = self._path_for_handle(normalized)
        with self._lock, InterProcessFileLock(self.lock_path):
            ref, _bundle = self._read_snapshot(
                path,
                expected_handle=normalized,
            )
        return ref


__all__ = [
    "EvidenceBundleSnapshotIntegrityError",
    "EvidenceBundleSnapshotNotFound",
    "EvidenceBundleSnapshotRef",
    "EvidenceBundleSnapshotStore",
    "EvidenceBundleSnapshotStoreError",
]
