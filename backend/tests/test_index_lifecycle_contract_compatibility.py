from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from rag.offline.lifecycle import (
    IndexActivationError,
    IndexLifecycleLockTimeout,
    IndexLifecycleManager,
    IndexRegistry,
)
from rag.offline.manifest import ArtifactRecord, IndexManifest, sha256_file


class _SuccessfulVerifier:
    def verify(self, *args, **kwargs):
        del args, kwargs
        return SimpleNamespace(status="success", metrics={})


class _FailedVerifier:
    def verify(self, *args, **kwargs):
        del args, kwargs
        return SimpleNamespace(
            status="failed",
            metrics={"failed_checks": ["injected"]},
        )


class _RaisingVerifier:
    def verify(self, *args, **kwargs):
        del args, kwargs
        raise RuntimeError("injected verification failure")


def _make_index(indexes_root: Path, version: str = "idx_v1") -> Path:
    index_dir = indexes_root / version
    index_dir.mkdir(parents=True)
    parent = index_dir / "parent_chunks.jsonl"
    child = index_dir / "child_chunks.jsonl"
    db = index_dir / "milvus.db"
    parent.write_text(json.dumps({"parent_chunk_id": "p1"}) + "\n", encoding="utf-8")
    child.write_text(
        json.dumps({"child_chunk_id": "c1", "parent_chunk_id": "p1"}) + "\n",
        encoding="utf-8",
    )
    db.write_bytes(b"milvus-test")
    manifest = IndexManifest(
        index_version=version,
        build_id=f"build_{version}",
        dataset_version="dataset_v1",
        config_hash="c" * 64,
        source_hash="s" * 64,
        chunker={"name": "fixed_parent_child", "version": "v1"},
        embedding={
            "mode": "model",
            "model": "m3e-base",
            "version": "v1",
            "dim": 768,
        },
        index={
            "backend": "milvus_lite",
            "collection_name": "rag_child_chunks",
            "metric_type": "COSINE",
        },
        document_count=1,
        parent_chunk_count=1,
        child_chunk_count=1,
        artifacts={
            "parent_chunks": ArtifactRecord(
                path=str(parent), sha256=sha256_file(parent), record_count=1
            ),
            "child_chunks": ArtifactRecord(
                path=str(child), sha256=sha256_file(child), record_count=1
            ),
            "milvus_lite": ArtifactRecord(
                path=str(db), sha256=sha256_file(db), record_count=1
            ),
        },
        created_at="2026-07-30T00:00:00+00:00",
        reproducibility_hash="r" * 64,
    )
    manifest_path = index_dir / "index_manifest.json"
    manifest.write(manifest_path)
    return manifest_path


def test_new_and_legacy_constructor_paths_are_equivalent(tmp_path: Path) -> None:
    indexes_root = tmp_path / "indexes"
    pointer_path = tmp_path / "control" / "active.json"
    registry_path = tmp_path / "control" / "registry.json"
    history_path = tmp_path / "audit" / "history.jsonl"
    lock_path = indexes_root / ".index_lifecycle.lock"

    new_manager = IndexLifecycleManager(
        indexes_root,
        active_pointer_path=pointer_path,
        registry=IndexRegistry(
            indexes_root,
            registry_path=registry_path,
            history_path=history_path,
        ),
        verifier=_SuccessfulVerifier(),
    )
    legacy_manager = IndexLifecycleManager(
        project_root=tmp_path,
        indexes_root=indexes_root,
        pointer_path=pointer_path,
        registry_path=registry_path,
        history_path=history_path,
        lock_path=lock_path,
        verifier_factory=_SuccessfulVerifier,
    )

    assert legacy_manager.root_dir == new_manager.root_dir
    assert legacy_manager.pointer_path == new_manager.active_pointer_path
    assert legacy_manager.registry_path == new_manager.registry_path
    assert legacy_manager.history_path == new_manager.history_path
    assert legacy_manager.lock_path == new_manager.lock_path


def test_new_interface_activates_by_manifest_path(tmp_path: Path) -> None:
    indexes_root = tmp_path / "indexes"
    manifest_path = _make_index(indexes_root)
    manager = IndexLifecycleManager(
        indexes_root,
        verifier=_SuccessfulVerifier(),
    )
    manager.register(manifest_path)

    result = manager.activate(manifest_path, verify_milvus=False)

    assert result.index_version == "idx_v1"
    assert manager.read_active_pointer().index_version == "idx_v1"
    assert manager.registry_path == indexes_root / "index_registry.json"
    assert manager.history_path == indexes_root / "index_lifecycle_history.jsonl"


def test_new_and_legacy_entrypoints_share_activation_semantics(tmp_path: Path) -> None:
    indexes_root = tmp_path / "indexes"
    manifest_path = _make_index(indexes_root)
    pointer_path = tmp_path / "active.json"
    registry_path = tmp_path / "registry.json"
    history_path = tmp_path / "history.jsonl"

    manager = IndexLifecycleManager(
        project_root=tmp_path,
        indexes_root=indexes_root,
        pointer_path=pointer_path,
        registry_path=registry_path,
        history_path=history_path,
        lock_path=tmp_path / "lifecycle.lock",
        verifier_factory=_SuccessfulVerifier,
    )
    manager.register(manifest_path)
    result = manager.activate("idx_v1", verify_milvus=False)

    assert result.active_index_version == "idx_v1"
    assert manager.resolve_active()["index_version"] == "idx_v1"
    assert pointer_path.is_file()
    assert registry_path.is_file()
    assert history_path.is_file()


def test_first_activation_failure_leaves_no_pointer(tmp_path: Path) -> None:
    indexes_root = tmp_path / "indexes"
    manifest_path = _make_index(indexes_root)
    manager = IndexLifecycleManager(
        indexes_root,
        verifier=_FailedVerifier(),
    )
    manager.register(manifest_path)

    with pytest.raises(IndexActivationError, match="verification failed"):
        manager.activate("idx_v1", verify_milvus=False)

    assert not manager.pointer_path.exists()


def test_raised_verification_error_preserves_existing_pointer(
    tmp_path: Path,
) -> None:
    indexes_root = tmp_path / "indexes"
    manifest_path = _make_index(indexes_root)
    pointer_path = tmp_path / "active.json"
    registry_path = tmp_path / "registry.json"
    history_path = tmp_path / "history.jsonl"
    common = {
        "project_root": tmp_path,
        "indexes_root": indexes_root,
        "pointer_path": pointer_path,
        "registry_path": registry_path,
        "history_path": history_path,
        "lock_path": tmp_path / "lifecycle.lock",
    }
    successful = IndexLifecycleManager(
        **common,
        verifier_factory=_SuccessfulVerifier,
    )
    successful.register(manifest_path)
    successful.activate("idx_v1", verify_milvus=False)
    before = pointer_path.read_bytes()

    failing = IndexLifecycleManager(
        **common,
        verifier_factory=_RaisingVerifier,
    )
    with pytest.raises(RuntimeError, match="verification failure"):
        failing.activate("idx_v1", verify_milvus=False)

    assert pointer_path.read_bytes() == before


def test_legacy_registry_and_history_files_remain_readable(
    tmp_path: Path,
) -> None:
    indexes_root = tmp_path / "indexes"
    manifest_path = _make_index(indexes_root)
    registry_path = tmp_path / "registry.json"
    history_path = tmp_path / "history.jsonl"
    pointer_path = tmp_path / "active.json"
    registry_path.write_text(
        json.dumps(
            {
                "schema_version": "rag_index_registry_v1",
                "updated_at": "2026-07-30T00:00:00+00:00",
                "indexes": {
                    "idx_v1": {
                        "index_version": "idx_v1",
                        "manifest_path": str(manifest_path.resolve()),
                        "manifest_sha256": sha256_file(manifest_path),
                        "dataset_version": "dataset_v1",
                        "backend": "milvus_lite",
                        "document_count": 1,
                        "parent_chunk_count": 1,
                        "child_chunk_count": 1,
                        "created_at": "2026-07-30T00:00:00+00:00",
                        "registered_at": "2026-07-30T00:00:00+00:00",
                    }
                },
            }
        ),
        encoding="utf-8",
    )
    history_path.write_text(
        json.dumps(
            {
                "schema_version": "rag_index_lifecycle_event_v1",
                "event_id": "idxevt_legacy",
                "timestamp": "2026-07-30T00:01:00+00:00",
                "operation": "activate",
                "actor": "legacy",
                "reason": "compatibility",
                "from_index_version": None,
                "to_index_version": "idx_v1",
                "pointer_path": str(pointer_path),
                "previous_pointer": None,
                "active_pointer": {
                    "index_version": "idx_v1",
                    "manifest_path": str(manifest_path.resolve()),
                    "manifest_sha256": sha256_file(manifest_path),
                },
                "verification_status": "success",
                "verification_metrics": {},
            }
        )
        + "\n",
        encoding="utf-8",
    )
    manager = IndexLifecycleManager(
        project_root=tmp_path,
        indexes_root=indexes_root,
        pointer_path=pointer_path,
        registry_path=registry_path,
        history_path=history_path,
        lock_path=tmp_path / "lifecycle.lock",
        verifier_factory=_SuccessfulVerifier,
    )

    assert manager.list_indexes(refresh=False)[0].index_version == "idx_v1"
    assert manager.history()[0].operation == "activate"
    assert manager.history()[0].actor == "legacy"


def test_non_stale_lock_is_not_stolen(tmp_path: Path) -> None:
    manager = IndexLifecycleManager(
        project_root=tmp_path,
        indexes_root=tmp_path / "indexes",
        lock_path=tmp_path / "lifecycle.lock",
        verifier_factory=_SuccessfulVerifier,
        lock_timeout_seconds=0.1,
        stale_lock_seconds=60,
    )
    manager.lock_path.parent.mkdir(parents=True, exist_ok=True)
    manager.lock_path.write_text("owned", encoding="utf-8")

    with pytest.raises(IndexLifecycleLockTimeout):
        manager.activate("missing")

    assert manager.lock_path.read_text(encoding="utf-8") == "owned"
