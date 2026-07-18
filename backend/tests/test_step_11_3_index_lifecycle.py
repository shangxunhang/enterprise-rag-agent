from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from rag.offline.lifecycle import (
    IndexActivationError,
    IndexLifecycleManager,
)
from rag.offline.manifest import ArtifactRecord, IndexManifest, fingerprint_path, sha256_file
from rag.offline.resolver import ActiveIndexResolver
from rag.tools.rag_tool import RAGTool, RAGToolConfig


class FakeVerificationReport:
    def __init__(self, status: str = "success") -> None:
        self.status = status
        self.metrics = {
            "failed_checks": [] if status == "success" else ["fake_failure"],
            "milvus_row_count": 1,
            "self_retrieval_hit_rate": 1.0 if status == "success" else 0.0,
        }


class FakeVerifier:
    def __init__(self, status: str = "success") -> None:
        self.status = status

    def verify(self, manifest_path, **kwargs):
        return FakeVerificationReport(self.status)


def _make_index(root: Path, version: str) -> Path:
    index_dir = root / "data" / "processed" / "indexes" / version
    index_dir.mkdir(parents=True)
    parent = index_dir / "parent_chunks.jsonl"
    child = index_dir / "child_chunks.jsonl"
    db = index_dir / "milvus.db"
    parent.write_text(json.dumps({"parent_chunk_id": f"p_{version}"}) + "\n", encoding="utf-8")
    child.write_text(
        json.dumps({"child_chunk_id": f"c_{version}", "parent_chunk_id": f"p_{version}"}) + "\n",
        encoding="utf-8",
    )
    db.write_bytes(f"db-{version}".encode())
    manifest = IndexManifest(
        index_version=version,
        build_id=f"build_{version}",
        dataset_version=f"dataset_{version}",
        config_hash="c" * 64,
        source_hash="s" * 64,
        chunker={"name": "fixed_parent_child", "version": "v1"},
        embedding={
            "mode": "model",
            "model": "m3e-base",
            "version": "m3e-v1",
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
        created_at="2026-07-17T00:00:00+00:00",
        reproducibility_hash="r" * 64,
    )
    manifest_path = index_dir / "index_manifest.json"
    manifest.write(manifest_path)
    return manifest_path


def _manager(root: Path, status: str = "success") -> IndexLifecycleManager:
    return IndexLifecycleManager(
        project_root=root,
        verifier_factory=lambda: FakeVerifier(status),
    )


def test_discover_register_activate_and_online_resolve(tmp_path: Path) -> None:
    manifest_path = _make_index(tmp_path, "idx_v1")
    manager = _manager(tmp_path)

    discovered = manager.discover()
    assert [item.index_version for item in discovered] == ["idx_v1"]

    result = manager.activate("idx_v1", actor="pytest", reason="acceptance")
    assert result.active_index_version == "idx_v1"
    assert result.previous_index_version is None

    resolved = ActiveIndexResolver(verify_artifacts=True).resolve(manager.pointer_path)
    assert resolved["index_version"] == "idx_v1"
    assert resolved["manifest_path"] == str(manifest_path.resolve())
    assert manager.status().active_index_version == "idx_v1"
    assert len(manager.history()) == 1
    assert not list(manager.pointer_path.parent.glob(".active_index.json.*.tmp"))


def test_failed_activation_keeps_previous_pointer(tmp_path: Path) -> None:
    _make_index(tmp_path, "idx_v1")
    _make_index(tmp_path, "idx_v2")
    good = _manager(tmp_path)
    good.discover()
    good.activate("idx_v1")
    pointer_before = manager_text = good.pointer_path.read_text(encoding="utf-8")

    failed = _manager(tmp_path, status="failed")
    with pytest.raises(IndexActivationError, match="verification failed"):
        failed.activate("idx_v2")

    assert failed.pointer_path.read_text(encoding="utf-8") == pointer_before
    assert json.loads(manager_text)["index_version"] == "idx_v1"


def test_rollback_restores_previous_index_and_records_audit(tmp_path: Path) -> None:
    _make_index(tmp_path, "idx_v1")
    _make_index(tmp_path, "idx_v2")
    manager = _manager(tmp_path)
    manager.discover()
    manager.activate("idx_v1", actor="pytest")
    manager.activate("idx_v2", actor="pytest")

    result = manager.rollback(actor="pytest", reason="bad retrieval")

    assert result.operation == "rollback"
    assert result.active_index_version == "idx_v1"
    assert manager.resolve_active()["index_version"] == "idx_v1"
    assert [event.operation for event in manager.history()] == [
        "activate",
        "activate",
        "rollback",
    ]


def test_discovery_ignores_failed_archives(tmp_path: Path) -> None:
    _make_index(tmp_path, "idx_valid")
    archived = _make_index(tmp_path, "idx_archived")
    failed_dir = archived.parent.with_name(archived.parent.name + ".failed_20260717")
    archived.parent.rename(failed_dir)

    versions = [item.index_version for item in _manager(tmp_path).discover()]
    assert versions == ["idx_valid"]


def test_resolver_verifies_directory_artifact_fingerprint(tmp_path: Path) -> None:
    manifest_path = _make_index(tmp_path, "idx_dir")
    manifest = IndexManifest.model_validate_json(manifest_path.read_text(encoding="utf-8"))
    db_dir = manifest_path.parent / "milvus_dir"
    db_dir.mkdir()
    (db_dir / "manifest.json").write_text("{}", encoding="utf-8")
    digest, metadata_only = fingerprint_path(db_dir)
    manifest.artifacts["milvus_lite"] = ArtifactRecord(
        path=str(db_dir),
        sha256=digest,
        record_count=1,
        kind="directory",
        metadata_only_paths=metadata_only,
    )
    manifest.write(manifest_path)

    manager = _manager(tmp_path)
    manager.discover()
    manager.activate("idx_dir", verify_artifact_hashes=True)
    resolved = manager.resolve_active(verify_artifacts=True)
    assert resolved["db_file"] == str(db_dir.resolve())


def test_resolver_allows_legacy_milvus_directory_operational_drift(tmp_path: Path) -> None:
    manifest_path = _make_index(tmp_path, "idx_legacy_dir")
    manifest = IndexManifest.model_validate_json(manifest_path.read_text(encoding="utf-8"))
    db_dir = manifest_path.parent / "milvus_dir"
    db_dir.mkdir()
    internal = db_dir / "manifest.json"
    internal.write_text('{"generation": 1}', encoding="utf-8")
    digest, metadata_only = fingerprint_path(db_dir)
    manifest.artifacts["milvus_lite"] = ArtifactRecord(
        path=str(db_dir),
        sha256=digest,
        record_count=1,
        kind="directory",
        metadata_only_paths=metadata_only,
    )
    manifest.write(manifest_path)

    manager = _manager(tmp_path)
    manager.discover()
    manager.activate("idx_legacy_dir", verify_artifact_hashes=True)

    # A read-only Milvus open may rewrite operational files. Resolver must not
    # interpret this as immutable data corruption.
    internal.write_text('{"generation": 2}', encoding="utf-8")
    resolved = manager.resolve_active(verify_artifacts=True)
    assert resolved["index_version"] == "idx_legacy_dir"



class FakeEngine:
    def __init__(self, version: str) -> None:
        self.version = version
        self.closed = False

    def close(self) -> None:
        self.closed = True


class FakeRuntimeFactory:
    def __init__(self, pointer_path: Path) -> None:
        self.pointer_path = pointer_path
        self.engines: list[FakeEngine] = []

    def resolve_config(self, config, project_root):
        return config

    def build(self, config, project_root):
        payload = json.loads(self.pointer_path.read_text(encoding="utf-8"))
        version = payload["index_version"]
        engine = FakeEngine(version)
        self.engines.append(engine)
        runtime_config = SimpleNamespace(index_version=version)
        return engine, runtime_config


class FakeRunner:
    def run(self, engine, config, tool_input, tool_name):
        return {"status": "success", "index_version": config.index_version}


def test_rag_tool_explicit_reload_swaps_engine_after_pointer_change(tmp_path: Path) -> None:
    pointer = tmp_path / "active_index.json"
    pointer.write_text(json.dumps({"index_version": "idx_v1"}), encoding="utf-8")
    factory = FakeRuntimeFactory(pointer)
    tool = RAGTool(
        RAGToolConfig(active_index_pointer=str(pointer)),
        project_root=tmp_path,
        runtime_factory=factory,
        runner=FakeRunner(),
    )
    tool.initialize()
    first_engine = tool.engine
    assert tool.config.index_version == "idx_v1"
    assert tool.active_index_changed() is False

    pointer.write_text(json.dumps({"index_version": "idx_v2"}), encoding="utf-8")
    assert tool.active_index_changed() is True
    result = tool.reload_active_index()

    assert result["status"] == "reloaded"
    assert result["previous_index_version"] == "idx_v1"
    assert result["index_version"] == "idx_v2"
    assert first_engine.closed is True
    assert tool.run({"query": "x"})["index_version"] == "idx_v2"


def test_rag_tool_reload_is_noop_when_pointer_unchanged(tmp_path: Path) -> None:
    pointer = tmp_path / "active_index.json"
    pointer.write_text(json.dumps({"index_version": "idx_v1"}), encoding="utf-8")
    factory = FakeRuntimeFactory(pointer)
    tool = RAGTool(
        RAGToolConfig(active_index_pointer=str(pointer)),
        project_root=tmp_path,
        runtime_factory=factory,
        runner=FakeRunner(),
    )
    tool.initialize()

    result = tool.reload_active_index()

    assert result["status"] == "unchanged"
    assert len(factory.engines) == 1


def test_post_activation_resolution_failure_restores_previous_pointer(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _make_index(tmp_path, "idx_v1")
    _make_index(tmp_path, "idx_v2")
    manager = _manager(tmp_path)
    manager.discover()
    manager.activate("idx_v1")
    before = manager.pointer_path.read_text(encoding="utf-8")

    def fail_resolve(self, pointer_path):
        raise ValueError("simulated online resolution failure")

    monkeypatch.setattr(
        "rag.offline.lifecycle.ActiveIndexResolver.resolve",
        fail_resolve,
    )
    with pytest.raises(ValueError, match="simulated online resolution failure"):
        manager.activate("idx_v2")

    assert manager.pointer_path.read_text(encoding="utf-8") == before
    assert len(manager.history()) == 1


class FailingRuntimeFactory(FakeRuntimeFactory):
    def build(self, config, project_root):
        payload = json.loads(self.pointer_path.read_text(encoding="utf-8"))
        if payload["index_version"] == "idx_bad":
            raise RuntimeError("simulated runtime build failure")
        return super().build(config, project_root)


def test_rag_tool_failed_reload_preserves_old_engine(tmp_path: Path) -> None:
    pointer = tmp_path / "active_index.json"
    pointer.write_text(json.dumps({"index_version": "idx_v1"}), encoding="utf-8")
    factory = FailingRuntimeFactory(pointer)
    tool = RAGTool(
        RAGToolConfig(active_index_pointer=str(pointer)),
        project_root=tmp_path,
        runtime_factory=factory,
        runner=FakeRunner(),
    )
    tool.initialize()
    old_engine = tool.engine

    pointer.write_text(json.dumps({"index_version": "idx_bad"}), encoding="utf-8")
    with pytest.raises(RuntimeError, match="simulated runtime build failure"):
        tool.reload_active_index()

    assert tool.engine is old_engine
    assert old_engine.closed is False
    assert tool.config.index_version == "idx_v1"


def test_manifest_change_after_registration_is_rejected(tmp_path: Path) -> None:
    manifest_path = _make_index(tmp_path, "idx_v1")
    manager = _manager(tmp_path)
    manager.discover()
    manifest_path.write_text(
        manifest_path.read_text(encoding="utf-8") + "\n",
        encoding="utf-8",
    )

    with pytest.raises(IndexActivationError, match="changed after registration"):
        manager.activate("idx_v1")

    assert not manager.pointer_path.exists()


def test_stale_lifecycle_lock_is_recovered(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _make_index(tmp_path, "idx_v1")
    manager = IndexLifecycleManager(
        project_root=tmp_path,
        verifier_factory=lambda: FakeVerifier("success"),
        stale_lock_seconds=60,
    )
    manager.discover()
    manager.lock_path.parent.mkdir(parents=True, exist_ok=True)
    manager.lock_path.write_text("stale", encoding="utf-8")
    old = manager.lock_path.stat().st_mtime - 120
    import os
    os.utime(manager.lock_path, (old, old))

    result = manager.activate("idx_v1")

    assert result.active_index_version == "idx_v1"
    assert not manager.lock_path.exists()
