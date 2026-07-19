# =============================================================================
# 中文阅读说明：自动化测试模块，用于验证主链、边界条件和回归行为。
# 主要定义：FakeVerificationReport、FakeVerifier、_make_index、_manager、test_discover_register_activate_and_online_resolve、test_failed_activation_keeps_previous_pointer、test_rollback_restores_previous_index_and_records_audit、test_discovery_ignores_failed_archives、test_resolver_verifies_directory_artifact_fingerprint、test_resolver_allows_legacy_milvus_directory_operational_drift等。建议先从公开入口函数开始，再沿调用关系向下阅读。
# =============================================================================
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
from rag.runtime.retrieval_runtime import RetrievalRuntime, RetrievalRuntimeConfig


# 阅读注释（类）：封装 fake verification report，集中封装相关状态、依赖和行为。
class FakeVerificationReport:
    """封装 fake verification report，集中封装相关状态、依赖和行为。"""
    # 阅读注释（函数）：初始化 FakeVerificationReport，保存运行所需的依赖、配置或状态。
    def __init__(self, status: str = "success") -> None:
        """初始化 FakeVerificationReport，保存运行所需的依赖、配置或状态。

        参数:
            status: 状态，具体约束请结合类型标注和调用方确认。

        返回:
            None
        """
        self.status = status
        self.metrics = {
            "failed_checks": [] if status == "success" else ["fake_failure"],
            "milvus_row_count": 1,
            "self_retrieval_hit_rate": 1.0 if status == "success" else 0.0,
        }


# 阅读注释（类）：封装 fake verifier，集中封装相关状态、依赖和行为。
class FakeVerifier:
    """封装 fake verifier，集中封装相关状态、依赖和行为。"""
    # 阅读注释（函数）：初始化 FakeVerifier，保存运行所需的依赖、配置或状态。
    def __init__(self, status: str = "success") -> None:
        """初始化 FakeVerifier，保存运行所需的依赖、配置或状态。

        参数:
            status: 状态，具体约束请结合类型标注和调用方确认。

        返回:
            None
        """
        self.status = status

    # 阅读注释（函数）：验证 FakeVerifier。
    def verify(self, manifest_path, **kwargs):
        """验证 FakeVerifier。

        参数:
            manifest_path: manifest 路径，具体约束请结合类型标注和调用方确认。
            **kwargs: 额外关键字参数。

        返回:
            未显式标注；请结合调用方和实际返回语句理解。

        阅读提示:
            主要直接调用：FakeVerificationReport。
        """
        return FakeVerificationReport(self.status)


# 阅读注释（函数）：生成 索引。
def _make_index(root: Path, version: str) -> Path:
    """生成 索引。

    参数:
        root: root，具体约束请结合类型标注和调用方确认。
        version: 版本，具体约束请结合类型标注和调用方确认。

    返回:
        Path

    阅读提示:
        主要直接调用：index_dir.mkdir, parent.write_text, json.dumps, child.write_text, db.write_bytes, encode, IndexManifest, ArtifactRecord。
    """
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


# 阅读注释（函数）：处理 管理器 相关逻辑。
def _manager(root: Path, status: str = "success") -> IndexLifecycleManager:
    """处理 管理器 相关逻辑。

    参数:
        root: root，具体约束请结合类型标注和调用方确认。
        status: 状态，具体约束请结合类型标注和调用方确认。

    返回:
        IndexLifecycleManager

    阅读提示:
        主要直接调用：IndexLifecycleManager。
    """
    return IndexLifecycleManager(
        project_root=root,
        verifier_factory=lambda: FakeVerifier(status),
    )


# 阅读注释（函数）：处理 测试 discover register activate and 在线 resolve 相关逻辑。
def test_discover_register_activate_and_online_resolve(tmp_path: Path) -> None:
    """处理 测试 discover register activate and 在线 resolve 相关逻辑。

    参数:
        tmp_path: tmp 路径，具体约束请结合类型标注和调用方确认。

    返回:
        None

    阅读提示:
        主要直接调用：_make_index, _manager, manager.discover, manager.activate, resolve, ActiveIndexResolver, str, manifest_path.resolve。
    """
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


# 阅读注释（函数）：处理 测试 failed activation keeps previous pointer 相关逻辑。
def test_failed_activation_keeps_previous_pointer(tmp_path: Path) -> None:
    """处理 测试 failed activation keeps previous pointer 相关逻辑。

    参数:
        tmp_path: tmp 路径，具体约束请结合类型标注和调用方确认。

    返回:
        None

    阅读提示:
        主要直接调用：_make_index, _manager, good.discover, good.activate, good.pointer_path.read_text, pytest.raises, failed.activate, failed.pointer_path.read_text。
    """
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


# 阅读注释（函数）：处理 测试 rollback restores previous 索引 and 记录集合 audit 相关逻辑。
def test_rollback_restores_previous_index_and_records_audit(tmp_path: Path) -> None:
    """处理 测试 rollback restores previous 索引 and 记录集合 audit 相关逻辑。

    参数:
        tmp_path: tmp 路径，具体约束请结合类型标注和调用方确认。

    返回:
        None

    阅读提示:
        主要直接调用：_make_index, _manager, manager.discover, manager.activate, manager.rollback, manager.resolve_active, manager.history。
    """
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


# 阅读注释（函数）：处理 测试 discovery ignores failed archives 相关逻辑。
def test_discovery_ignores_failed_archives(tmp_path: Path) -> None:
    """处理 测试 discovery ignores failed archives 相关逻辑。

    参数:
        tmp_path: tmp 路径，具体约束请结合类型标注和调用方确认。

    返回:
        None

    阅读提示:
        主要直接调用：_make_index, archived.parent.with_name, archived.parent.rename, discover, _manager。
    """
    _make_index(tmp_path, "idx_valid")
    archived = _make_index(tmp_path, "idx_archived")
    failed_dir = archived.parent.with_name(archived.parent.name + ".failed_20260717")
    archived.parent.rename(failed_dir)

    versions = [item.index_version for item in _manager(tmp_path).discover()]
    assert versions == ["idx_valid"]


# 阅读注释（函数）：处理 测试 resolver verifies directory artifact fingerprint 相关逻辑。
def test_resolver_verifies_directory_artifact_fingerprint(tmp_path: Path) -> None:
    """处理 测试 resolver verifies directory artifact fingerprint 相关逻辑。

    参数:
        tmp_path: tmp 路径，具体约束请结合类型标注和调用方确认。

    返回:
        None

    阅读提示:
        主要直接调用：_make_index, IndexManifest.model_validate_json, manifest_path.read_text, db_dir.mkdir, write_text, fingerprint_path, ArtifactRecord, str。
    """
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


# 阅读注释（函数）：处理 测试 resolver allows legacy milvus directory operational drift 相关逻辑。
def test_resolver_allows_legacy_milvus_directory_operational_drift(tmp_path: Path) -> None:
    """处理 测试 resolver allows legacy milvus directory operational drift 相关逻辑。

    参数:
        tmp_path: tmp 路径，具体约束请结合类型标注和调用方确认。

    返回:
        None

    阅读提示:
        主要直接调用：_make_index, IndexManifest.model_validate_json, manifest_path.read_text, db_dir.mkdir, internal.write_text, fingerprint_path, ArtifactRecord, str。
    """
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



# 阅读注释（类）：封装 fake engine，负责驱动实际运行流程并维护执行状态。
class FakeEngine:
    """封装 fake engine，负责驱动实际运行流程并维护执行状态。"""
    # 阅读注释（函数）：初始化 FakeEngine，保存运行所需的依赖、配置或状态。
    def __init__(self, version: str) -> None:
        """初始化 FakeEngine，保存运行所需的依赖、配置或状态。

        参数:
            version: 版本，具体约束请结合类型标注和调用方确认。

        返回:
            None
        """
        self.version = version
        self.closed = False

    # 阅读注释（函数）：释放 FakeEngine 持有的资源。
    def close(self) -> None:
        """释放 FakeEngine 持有的资源。

        返回:
            None
        """
        self.closed = True


# 阅读注释（类）：封装 fake 运行时 工厂，负责根据配置装配并返回运行实例。
class FakeRuntimeFactory:
    """封装 fake 运行时 工厂，负责根据配置装配并返回运行实例。"""
    # 阅读注释（函数）：初始化 FakeRuntimeFactory，保存运行所需的依赖、配置或状态。
    def __init__(self, pointer_path: Path) -> None:
        """初始化 FakeRuntimeFactory，保存运行所需的依赖、配置或状态。

        参数:
            pointer_path: pointer 路径，具体约束请结合类型标注和调用方确认。

        返回:
            None
        """
        self.pointer_path = pointer_path
        self.engines: list[FakeEngine] = []

    # 阅读注释（函数）：解析并确定 配置。
    def resolve_config(self, config, project_root):
        """解析并确定 配置。

        参数:
            config: 运行配置。
            project_root: 项目 root，具体约束请结合类型标注和调用方确认。

        返回:
            未显式标注；请结合调用方和实际返回语句理解。
        """
        return config

    # 阅读注释（函数）：构建 FakeRuntimeFactory。
    def build(self, config, project_root):
        """构建 FakeRuntimeFactory。

        参数:
            config: 运行配置。
            project_root: 项目 root，具体约束请结合类型标注和调用方确认。

        返回:
            未显式标注；请结合调用方和实际返回语句理解。

        阅读提示:
            主要直接调用：json.loads, self.pointer_path.read_text, FakeEngine, self.engines.append, SimpleNamespace。
        """
        payload = json.loads(self.pointer_path.read_text(encoding="utf-8"))
        version = payload["index_version"]
        engine = FakeEngine(version)
        self.engines.append(engine)
        runtime_config = SimpleNamespace(index_version=version)
        return engine, runtime_config


# 阅读注释（类）：封装 fake runner，集中封装相关状态、依赖和行为。
# 阅读注释（函数）：处理 测试 RAG 工具 explicit reload swaps engine after pointer change 相关逻辑。
def test_rag_tool_explicit_reload_swaps_engine_after_pointer_change(tmp_path: Path) -> None:
    """处理 测试 RAG 工具 explicit reload swaps engine after pointer change 相关逻辑。

    参数:
        tmp_path: tmp 路径，具体约束请结合类型标注和调用方确认。

    返回:
        None

    阅读提示:
        主要直接调用：pointer.write_text, json.dumps, FakeRuntimeFactory, RAGTool, RAGToolConfig, str, FakeRunner, tool.initialize。
    """
    pointer = tmp_path / "active_index.json"
    pointer.write_text(json.dumps({"index_version": "idx_v1"}), encoding="utf-8")
    factory = FakeRuntimeFactory(pointer)
    tool = RetrievalRuntime(
        RetrievalRuntimeConfig(active_index_pointer=str(pointer)),
        project_root=tmp_path,
        runtime_factory=factory,
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
    assert tool.config.index_version == "idx_v2"


# 阅读注释（函数）：处理 测试 RAG 工具 reload is noop when pointer unchanged 相关逻辑。
def test_rag_tool_reload_is_noop_when_pointer_unchanged(tmp_path: Path) -> None:
    """处理 测试 RAG 工具 reload is noop when pointer unchanged 相关逻辑。

    参数:
        tmp_path: tmp 路径，具体约束请结合类型标注和调用方确认。

    返回:
        None

    阅读提示:
        主要直接调用：pointer.write_text, json.dumps, FakeRuntimeFactory, RAGTool, RAGToolConfig, str, FakeRunner, tool.initialize。
    """
    pointer = tmp_path / "active_index.json"
    pointer.write_text(json.dumps({"index_version": "idx_v1"}), encoding="utf-8")
    factory = FakeRuntimeFactory(pointer)
    tool = RetrievalRuntime(
        RetrievalRuntimeConfig(active_index_pointer=str(pointer)),
        project_root=tmp_path,
        runtime_factory=factory,
    )
    tool.initialize()

    result = tool.reload_active_index()

    assert result["status"] == "unchanged"
    assert len(factory.engines) == 1


# 阅读注释（函数）：处理 测试 post activation resolution failure restores previous pointer 相关逻辑。
def test_post_activation_resolution_failure_restores_previous_pointer(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """处理 测试 post activation resolution failure restores previous pointer 相关逻辑。

    参数:
        tmp_path: tmp 路径，具体约束请结合类型标注和调用方确认。
        monkeypatch: monkeypatch，具体约束请结合类型标注和调用方确认。

    返回:
        None

    阅读提示:
        主要直接调用：_make_index, _manager, manager.discover, manager.activate, manager.pointer_path.read_text, monkeypatch.setattr, pytest.raises, len。
    """
    _make_index(tmp_path, "idx_v1")
    _make_index(tmp_path, "idx_v2")
    manager = _manager(tmp_path)
    manager.discover()
    manager.activate("idx_v1")
    before = manager.pointer_path.read_text(encoding="utf-8")

    # 阅读注释（函数）：处理 fail resolve 相关逻辑。
    def fail_resolve(self, pointer_path):
        """处理 fail resolve 相关逻辑。

        参数:
            pointer_path: pointer 路径，具体约束请结合类型标注和调用方确认。

        返回:
            未显式标注；请结合调用方和实际返回语句理解。

        阅读提示:
            主要直接调用：ValueError。
        """
        raise ValueError("simulated online resolution failure")

    monkeypatch.setattr(
        "rag.offline.lifecycle.ActiveIndexResolver.resolve",
        fail_resolve,
    )
    with pytest.raises(ValueError, match="simulated online resolution failure"):
        manager.activate("idx_v2")

    assert manager.pointer_path.read_text(encoding="utf-8") == before
    assert len(manager.history()) == 1


# 阅读注释（类）：封装 failing 运行时 工厂，负责根据配置装配并返回运行实例。
class FailingRuntimeFactory(FakeRuntimeFactory):
    """封装 failing 运行时 工厂，负责根据配置装配并返回运行实例。"""
    # 阅读注释（函数）：构建 FailingRuntimeFactory。
    def build(self, config, project_root):
        """构建 FailingRuntimeFactory。

        参数:
            config: 运行配置。
            project_root: 项目 root，具体约束请结合类型标注和调用方确认。

        返回:
            未显式标注；请结合调用方和实际返回语句理解。

        阅读提示:
            主要直接调用：json.loads, self.pointer_path.read_text, RuntimeError, build, super。
        """
        payload = json.loads(self.pointer_path.read_text(encoding="utf-8"))
        if payload["index_version"] == "idx_bad":
            raise RuntimeError("simulated runtime build failure")
        return super().build(config, project_root)


# 阅读注释（函数）：处理 测试 RAG 工具 failed reload preserves old engine 相关逻辑。
def test_rag_tool_failed_reload_preserves_old_engine(tmp_path: Path) -> None:
    """处理 测试 RAG 工具 failed reload preserves old engine 相关逻辑。

    参数:
        tmp_path: tmp 路径，具体约束请结合类型标注和调用方确认。

    返回:
        None

    阅读提示:
        主要直接调用：pointer.write_text, json.dumps, FailingRuntimeFactory, RAGTool, RAGToolConfig, str, FakeRunner, tool.initialize。
    """
    pointer = tmp_path / "active_index.json"
    pointer.write_text(json.dumps({"index_version": "idx_v1"}), encoding="utf-8")
    factory = FailingRuntimeFactory(pointer)
    tool = RetrievalRuntime(
        RetrievalRuntimeConfig(active_index_pointer=str(pointer)),
        project_root=tmp_path,
        runtime_factory=factory,
    )
    tool.initialize()
    old_engine = tool.engine

    pointer.write_text(json.dumps({"index_version": "idx_bad"}), encoding="utf-8")
    with pytest.raises(RuntimeError, match="simulated runtime build failure"):
        tool.reload_active_index()

    assert tool.engine is old_engine
    assert old_engine.closed is False
    assert tool.config.index_version == "idx_v1"


# 阅读注释（函数）：处理 测试 manifest change after registration is rejected 相关逻辑。
def test_manifest_change_after_registration_is_rejected(tmp_path: Path) -> None:
    """处理 测试 manifest change after registration is rejected 相关逻辑。

    参数:
        tmp_path: tmp 路径，具体约束请结合类型标注和调用方确认。

    返回:
        None

    阅读提示:
        主要直接调用：_make_index, _manager, manager.discover, manifest_path.write_text, manifest_path.read_text, pytest.raises, manager.activate, manager.pointer_path.exists。
    """
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


# 阅读注释（函数）：处理 测试 stale lifecycle lock is recovered 相关逻辑。
def test_stale_lifecycle_lock_is_recovered(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """处理 测试 stale lifecycle lock is recovered 相关逻辑。

    参数:
        tmp_path: tmp 路径，具体约束请结合类型标注和调用方确认。
        monkeypatch: monkeypatch，具体约束请结合类型标注和调用方确认。

    返回:
        None

    阅读提示:
        主要直接调用：_make_index, IndexLifecycleManager, manager.discover, manager.lock_path.parent.mkdir, manager.lock_path.write_text, manager.lock_path.stat, os.utime, manager.activate。
    """
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
