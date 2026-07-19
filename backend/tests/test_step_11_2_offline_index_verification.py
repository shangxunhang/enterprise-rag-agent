# =============================================================================
# 中文阅读说明：自动化测试模块，用于验证主链、边界条件和回归行为。
# 主要定义：_write_jsonl、_build_artifacts_only、test_verifier_passes_complete_artifact_index、test_verifier_detects_corrupted_vector_artifact、_FakeMilvusClient、test_verifier_checks_milvus_count_and_self_retrieval、test_verifier_treats_legacy_milvus_directory_as_semantic_artifact、test_real_profile_is_model_milvus_and_does_not_activate、test_fixed_chunker_accepts_explicit_tokenizer_configuration、test_sha256_path_supports_directory_artifacts等。建议先从公开入口函数开始，再沿调用关系向下阅读。
# =============================================================================
from __future__ import annotations

import json
from pathlib import Path

import numpy as np

from rag.config.static_retrieval import ComponentConfig
from rag.offline.builder import OfflineIndexBuilder
from rag.offline.config import (
    EmbeddingBuildConfig,
    IndexStorageConfig,
    OfflineIndexBuildConfig,
    OutputConfig,
    SourceDatasetConfig,
)
from rag.offline.manifest import ArtifactRecord, IndexManifest, sha256_file, sha256_path
from rag.offline.verification import OfflineIndexVerifier


RECORDS = [
    {
        "unit_id": "u1",
        "doc_id": "d1",
        "source_type": "offline",
        "source_uri": "test://d1",
        "source_name": "D1",
        "source_format": "md",
        "title": "D1",
        "section": "建设目标",
        "section_level": 1,
        "page_start": 1,
        "page_end": 1,
        "unit_type": "paragraph",
        "unit_order": 1,
        "text": "建设企业知识库，提供制度规范和历史项目材料检索能力。",
        "language": "zh",
        "quality_score": 0.9,
        "quality_flags": [],
        "cleaning_version": "clean_v1",
        "extra": {},
    },
    {
        "unit_id": "u2",
        "doc_id": "d1",
        "source_type": "offline",
        "source_uri": "test://d1",
        "source_name": "D1",
        "source_format": "md",
        "title": "D1",
        "section": "技术方案",
        "section_level": 1,
        "page_start": 2,
        "page_end": 2,
        "unit_type": "paragraph",
        "unit_order": 2,
        "text": "采用父子分块、混合检索、重排序和引用绑定。",
        "language": "zh",
        "quality_score": 0.95,
        "quality_flags": [],
        "cleaning_version": "clean_v1",
        "extra": {},
    },
]


# 阅读注释（函数）：写入 jsonl。
def _write_jsonl(path: Path, records: list[dict]) -> None:
    """写入 jsonl。

    参数:
        path: 目标文件或目录路径。
        records: 记录集合，具体约束请结合类型标注和调用方确认。

    返回:
        None

    阅读提示:
        主要直接调用：path.parent.mkdir, path.write_text, join, json.dumps。
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "".join(json.dumps(item, ensure_ascii=False) + "\n" for item in records),
        encoding="utf-8",
    )


# 阅读注释（函数）：构建 artifacts only。
def _build_artifacts_only(tmp_path: Path):
    """构建 artifacts only。

    参数:
        tmp_path: tmp 路径，具体约束请结合类型标注和调用方确认。

    返回:
        未显式标注；请结合调用方和实际返回语句理解。

    阅读提示:
        主要直接调用：_write_jsonl, OfflineIndexBuildConfig, SourceDatasetConfig, str, ComponentConfig, EmbeddingBuildConfig, IndexStorageConfig, OutputConfig。
    """
    source = tmp_path / "units.jsonl"
    _write_jsonl(source, RECORDS)
    config = OfflineIndexBuildConfig(
        build_id="step_11_2_unit",
        dataset_version="dataset_v1",
        source=SourceDatasetConfig(path=str(source)),
        chunker=ComponentConfig(
            name="fixed_parent_child",
            version="v1",
            params={
                "parent_chunk_size": 80,
                "parent_chunk_overlap": 10,
                "child_chunk_size": 40,
                "child_chunk_overlap": 5,
                "unit": "char",
            },
        ),
        embedding=EmbeddingBuildConfig(
            mode="hash",
            name="hash_embedding",
            version="v1",
            dim=32,
        ),
        index=IndexStorageConfig(backend="artifacts_only"),
        outputs=OutputConfig(root_dir=str(tmp_path / "indexes")),
        deterministic_created_at="2026-07-17T00:00:00+08:00",
    )
    result = OfflineIndexBuilder(project_root=tmp_path).build(config)
    return result, config


# 阅读注释（函数）：处理 测试 verifier passes complete artifact 索引 相关逻辑。
def test_verifier_passes_complete_artifact_index(tmp_path: Path) -> None:
    """处理 测试 verifier passes complete artifact 索引 相关逻辑。

    参数:
        tmp_path: tmp 路径，具体约束请结合类型标注和调用方确认。

    返回:
        None

    阅读提示:
        主要直接调用：_build_artifacts_only, verify, OfflineIndexVerifier, IndexManifest.model_validate_json, read_text, Path, str, resolve。
    """
    result, config = _build_artifacts_only(tmp_path)
    verification = OfflineIndexVerifier().verify(result.manifest_path, verify_milvus=False)
    assert verification.status == "success"
    assert verification.metrics["child_chunk_count"] == result.child_chunk_count
    assert verification.metrics["actual_counts"]["vector_rows"] == result.child_chunk_count
    assert verification.metrics["vector_norm"]["min"] > 0.99

    manifest = IndexManifest.model_validate_json(Path(result.manifest_path).read_text(encoding="utf-8"))
    assert manifest.source is not None
    assert manifest.source["path"] == str(Path(config.source.path).resolve())
    assert manifest.source["record_count"] == len(RECORDS)


# 阅读注释（函数）：处理 测试 verifier detects corrupted vector artifact 相关逻辑。
def test_verifier_detects_corrupted_vector_artifact(tmp_path: Path) -> None:
    """处理 测试 verifier detects corrupted vector artifact 相关逻辑。

    参数:
        tmp_path: tmp 路径，具体约束请结合类型标注和调用方确认。

    返回:
        None

    阅读提示:
        主要直接调用：_build_artifacts_only, IndexManifest.model_validate_json, read_text, Path, np.load, np.save, verify, OfflineIndexVerifier。
    """
    result, _ = _build_artifacts_only(tmp_path)
    manifest = IndexManifest.model_validate_json(Path(result.manifest_path).read_text(encoding="utf-8"))
    vector_path = Path(manifest.artifacts["vectors"].path)
    vectors = np.load(vector_path, allow_pickle=False)
    np.save(vector_path, vectors[:-1], allow_pickle=False)

    verification = OfflineIndexVerifier().verify(
        result.manifest_path,
        verify_artifact_hashes=False,
        verify_milvus=False,
    )
    assert verification.status == "failed"
    assert "artifact_record_counts" in verification.metrics["failed_checks"]


# 阅读注释（类）：封装 fake milvus 客户端，集中封装相关状态、依赖和行为。
class _FakeMilvusClient:
    """封装 fake milvus 客户端，集中封装相关状态、依赖和行为。"""
    # 阅读注释（函数）：初始化 _FakeMilvusClient，保存运行所需的依赖、配置或状态。
    def __init__(self, uri: str, *, child_ids: list[str]):
        """初始化 _FakeMilvusClient，保存运行所需的依赖、配置或状态。

        参数:
            uri: uri，具体约束请结合类型标注和调用方确认。
            child_ids: 子块 标识集合，具体约束请结合类型标注和调用方确认。

        返回:
            未显式标注；请结合调用方和实际返回语句理解。
        """
        self.uri = uri
        self.child_ids = child_ids
        self.search_calls = 0
        self.closed = False

    # 阅读注释（函数）：判断是否存在 collection。
    def has_collection(self, collection_name: str) -> bool:
        """判断是否存在 collection。

        参数:
            collection_name: collection 名称，具体约束请结合类型标注和调用方确认。

        返回:
            bool
        """
        return collection_name == "rag_child_chunks"

    # 阅读注释（函数）：加载 collection。
    def load_collection(self, collection_name: str) -> None:
        """加载 collection。

        参数:
            collection_name: collection 名称，具体约束请结合类型标注和调用方确认。

        返回:
            None
        """
        return None

    # 阅读注释（函数）：获取 collection stats。
    def get_collection_stats(self, collection_name: str):
        """获取 collection stats。

        参数:
            collection_name: collection 名称，具体约束请结合类型标注和调用方确认。

        返回:
            未显式标注；请结合调用方和实际返回语句理解。

        阅读提示:
            主要直接调用：len。
        """
        return {"row_count": len(self.child_ids)}

    # 阅读注释（函数）：搜索 _FakeMilvusClient。
    def search(self, **kwargs):
        """搜索 _FakeMilvusClient。

        参数:
            **kwargs: 额外关键字参数。

        返回:
            未显式标注；请结合调用方和实际返回语句理解。
        """
        child_id = self.child_ids[self.search_calls]
        self.search_calls += 1
        return [[{"id": child_id, "distance": 1.0, "entity": {"child_chunk_id": child_id}}]]

    # 阅读注释（函数）：释放 _FakeMilvusClient 持有的资源。
    def close(self) -> None:
        """释放 _FakeMilvusClient 持有的资源。

        返回:
            None
        """
        self.closed = True


# 阅读注释（函数）：处理 测试 verifier checks milvus count and Self 检索 相关逻辑。
def test_verifier_checks_milvus_count_and_self_retrieval(tmp_path: Path) -> None:
    """处理 测试 verifier checks milvus count and Self 检索 相关逻辑。

    参数:
        tmp_path: tmp 路径，具体约束请结合类型标注和调用方确认。

    返回:
        None

    阅读提示:
        主要直接调用：index_dir.mkdir, range, np.eye, _write_jsonl, np.save, db_path.mkdir, write_text, ArtifactRecord。
    """
    index_dir = tmp_path / "index"
    index_dir.mkdir()
    parents = [
        {"parent_chunk_id": "p1", "doc_id": "d1", "text": "parent 1"},
        {"parent_chunk_id": "p2", "doc_id": "d2", "text": "parent 2"},
        {"parent_chunk_id": "p3", "doc_id": "d3", "text": "parent 3"},
    ]
    children = [
        {
            "chunk_id": f"c{i}",
            "child_chunk_id": f"c{i}",
            "parent_chunk_id": f"p{i}",
            "doc_id": f"d{i}",
            "text": f"child {i}",
            "extra": {"offline_index_version": "idx_test"},
        }
        for i in range(1, 4)
    ]
    vectors = np.eye(3, dtype="float32")
    vector_records = [{"vector_id": f"c{i}"} for i in range(1, 4)]

    parent_path = index_dir / "parent_chunks.jsonl"
    child_path = index_dir / "child_chunks.jsonl"
    vector_record_path = index_dir / "vector_index_records.jsonl"
    vector_path = index_dir / "child_vectors.npy"
    db_path = index_dir / "milvus.db"
    _write_jsonl(parent_path, parents)
    _write_jsonl(child_path, children)
    _write_jsonl(vector_record_path, vector_records)
    np.save(vector_path, vectors, allow_pickle=False)
    db_path.mkdir()
    (db_path / "manifest.json").write_text("{}", encoding="utf-8")

    artifacts = {
        "parent_chunks": ArtifactRecord(path=str(parent_path), sha256=sha256_file(parent_path), record_count=3),
        "child_chunks": ArtifactRecord(path=str(child_path), sha256=sha256_file(child_path), record_count=3),
        "vectors": ArtifactRecord(path=str(vector_path), sha256=sha256_file(vector_path), record_count=3),
        "vector_index_records": ArtifactRecord(
            path=str(vector_record_path), sha256=sha256_file(vector_record_path), record_count=3
        ),
        "milvus_lite": ArtifactRecord(
            path=str(db_path),
            sha256=sha256_path(db_path),
            record_count=3,
            kind="directory",
        ),
    }
    manifest = IndexManifest(
        index_version="idx_test",
        build_id="build_test",
        dataset_version="dataset_test",
        config_hash="c" * 64,
        source_hash="s" * 64,
        source={"path": "units.jsonl", "record_count": 3},
        chunker={"name": "fixed_parent_child", "version": "v1"},
        embedding={"mode": "model", "model": "m3e-base", "version": "v1", "dim": 3},
        index={"backend": "milvus_lite", "collection_name": "rag_child_chunks", "metric_type": "COSINE"},
        document_count=3,
        parent_chunk_count=3,
        child_chunk_count=3,
        artifacts=artifacts,
        created_at="2026-07-17T00:00:00+08:00",
        reproducibility_hash="r" * 64,
    )
    manifest_path = index_dir / "index_manifest.json"
    manifest.write(manifest_path)

    fake = _FakeMilvusClient(str(db_path), child_ids=["c1", "c2", "c3"])
    verifier = OfflineIndexVerifier(milvus_client_factory=lambda _: fake)
    verification = verifier.verify(manifest_path, self_retrieval_samples=3, self_retrieval_top_k=1)

    assert verification.status == "success"
    assert verification.metrics["milvus_row_count"] == 3
    assert verification.metrics["self_retrieval_hit_rate"] == 1.0
    assert fake.closed is True


# 阅读注释（函数）：处理 测试 verifier treats legacy milvus directory as semantic artifact 相关逻辑。
def test_verifier_treats_legacy_milvus_directory_as_semantic_artifact(tmp_path: Path) -> None:
    """处理 测试 verifier treats legacy milvus directory as semantic artifact 相关逻辑。

    参数:
        tmp_path: tmp 路径，具体约束请结合类型标注和调用方确认。

    返回:
        None

    阅读提示:
        主要直接调用：index_dir.mkdir, np.ones, np.sqrt, _write_jsonl, np.save, db_path.mkdir, internal.write_text, IndexManifest。
    """
    index_dir = tmp_path / "index"
    index_dir.mkdir()
    parents = [{"parent_chunk_id": "p1", "doc_id": "d1", "text": "parent"}]
    children = [{
        "chunk_id": "c1",
        "child_chunk_id": "c1",
        "parent_chunk_id": "p1",
        "doc_id": "d1",
        "text": "child",
        "extra": {"offline_index_version": "idx_legacy"},
    }]
    vectors = np.ones((1, 3), dtype="float32") / np.sqrt(3.0)
    vector_records = [{"vector_id": "c1"}]

    parent_path = index_dir / "parent_chunks.jsonl"
    child_path = index_dir / "child_chunks.jsonl"
    vector_record_path = index_dir / "vector_index_records.jsonl"
    vector_path = index_dir / "child_vectors.npy"
    db_path = index_dir / "milvus.db"
    _write_jsonl(parent_path, parents)
    _write_jsonl(child_path, children)
    _write_jsonl(vector_record_path, vector_records)
    np.save(vector_path, vectors, allow_pickle=False)
    db_path.mkdir()
    internal = db_path / "manifest.json"
    internal.write_text('{"generation": 1}', encoding="utf-8")

    manifest = IndexManifest(
        index_version="idx_legacy",
        build_id="build_legacy",
        dataset_version="dataset_legacy",
        config_hash="c" * 64,
        source_hash="s" * 64,
        source={"path": "units.jsonl", "record_count": 1},
        chunker={"name": "fixed_parent_child", "version": "v1"},
        embedding={"mode": "model", "model": "m3e-base", "version": "v1", "dim": 3},
        index={"backend": "milvus_lite", "collection_name": "rag_child_chunks", "metric_type": "COSINE"},
        document_count=1,
        parent_chunk_count=1,
        child_chunk_count=1,
        artifacts={
            "parent_chunks": ArtifactRecord(path=str(parent_path), sha256=sha256_file(parent_path), record_count=1),
            "child_chunks": ArtifactRecord(path=str(child_path), sha256=sha256_file(child_path), record_count=1),
            "vectors": ArtifactRecord(path=str(vector_path), sha256=sha256_file(vector_path), record_count=1),
            "vector_index_records": ArtifactRecord(path=str(vector_record_path), sha256=sha256_file(vector_record_path), record_count=1),
            # Legacy artifact: directory hash and no explicit integrity_mode.
            "milvus_lite": ArtifactRecord(path=str(db_path), sha256=sha256_path(db_path), record_count=1, kind="directory"),
        },
        created_at="2026-07-17T00:00:00+08:00",
        reproducibility_hash="r" * 64,
    )
    manifest_path = index_dir / "index_manifest.json"
    manifest.write(manifest_path)

    # Simulate Milvus rewriting internal operational files after a read.
    internal.write_text('{"generation": 2}', encoding="utf-8")

    fake = _FakeMilvusClient(str(db_path), child_ids=["c1"])
    verification = OfflineIndexVerifier(
        milvus_client_factory=lambda _: fake
    ).verify(manifest_path, self_retrieval_samples=1, self_retrieval_top_k=1)

    assert verification.status == "success"
    hash_check = next(item for item in verification.checks if item.name == "artifact_sha256")
    assert hash_check.details["legacy_semantic_inferred"] == ["milvus_lite"]


# 阅读注释（函数）：处理 测试 real 策略配置 is 模型 milvus and does not activate 相关逻辑。
def test_real_profile_is_model_milvus_and_does_not_activate() -> None:
    """处理 测试 real 策略配置 is 模型 milvus and does not activate 相关逻辑。

    返回:
        None

    阅读提示:
        主要直接调用：load, OfflineIndexConfigLoader, endswith。
    """
    from rag.offline.config import OfflineIndexConfigLoader

    config = OfflineIndexConfigLoader().load(
        "backend/rag/index_profiles/m3e_milvus_lite_real_v1.yaml"
    )
    assert config.embedding.mode == "model"
    assert config.embedding.dim == 768
    assert config.index.backend == "milvus_lite"
    assert config.outputs.update_active_pointer is False
    assert config.chunker.params["tokenizer_model_name"].endswith("m3e-base")


# 阅读注释（函数）：处理 测试 fixed chunker accepts explicit tokenizer configuration 相关逻辑。
def test_fixed_chunker_accepts_explicit_tokenizer_configuration(monkeypatch) -> None:
    """处理 测试 fixed chunker accepts explicit tokenizer configuration 相关逻辑。

    参数:
        monkeypatch: monkeypatch，具体约束请结合类型标注和调用方确认。

    返回:
        None

    阅读提示:
        主要直接调用：monkeypatch.setattr, chunker_plugin.FixedParentChildChunkerPlugin, chunker.execution_metadata。
    """
    from rag.plugins.chunkers import plugin as chunker_plugin

    captured = {}

    # 阅读注释（类）：封装 counter，集中封装相关状态、依赖和行为。
    class _Counter:
        """封装 counter，集中封装相关状态、依赖和行为。"""
        backend = "fake"
        tokenizer = None

        # 阅读注释（函数）：处理 count 相关逻辑。
        def count(self, text):
            """处理 count 相关逻辑。

            参数:
                text: 待处理文本。

            返回:
                未显式标注；请结合调用方和实际返回语句理解。

            阅读提示:
                主要直接调用：len。
            """
            return len(text)

        # 阅读注释（函数）：处理 tokenize 相关逻辑。
        def tokenize(self, text):
            """处理 tokenize 相关逻辑。

            参数:
                text: 待处理文本。

            返回:
                未显式标注；请结合调用方和实际返回语句理解。

            阅读提示:
                主要直接调用：list。
            """
            return list(text)

    # 阅读注释（函数）：处理 fake get Token counter 相关逻辑。
    def fake_get_token_counter(tokenizer_name, local_files_only):
        """处理 fake get Token counter 相关逻辑。

        参数:
            tokenizer_name: tokenizer 名称，具体约束请结合类型标注和调用方确认。
            local_files_only: 本地 files only，具体约束请结合类型标注和调用方确认。

        返回:
            未显式标注；请结合调用方和实际返回语句理解。

        阅读提示:
            主要直接调用：_Counter。
        """
        captured["tokenizer_name"] = tokenizer_name
        captured["local_files_only"] = local_files_only
        return _Counter()

    monkeypatch.setattr(
        "rag.chunker.ChildParentChunker.get_token_counter",
        fake_get_token_counter,
    )
    chunker = chunker_plugin.FixedParentChildChunkerPlugin(
        parent_chunk_size=80,
        parent_chunk_overlap=10,
        child_chunk_size=40,
        child_chunk_overlap=5,
        unit="token",
        tokenizer_model_name="D:/models/m3e-base",
        tokenizer_local_files_only=True,
    )
    assert captured == {
        "tokenizer_name": "D:/models/m3e-base",
        "local_files_only": True,
    }
    assert chunker.execution_metadata()["tokenizer_model_name"] == "D:/models/m3e-base"


# 阅读注释（函数）：处理 测试 sha256 路径 supports directory artifacts 相关逻辑。
def test_sha256_path_supports_directory_artifacts(tmp_path: Path) -> None:
    """处理 测试 sha256 路径 supports directory artifacts 相关逻辑。

    参数:
        tmp_path: tmp 路径，具体约束请结合类型标注和调用方确认。

    返回:
        None

    阅读提示:
        主要直接调用：mkdir, write_text, sha256_path。
    """
    artifact_dir = tmp_path / "milvus.db"
    (artifact_dir / "collections" / "rag_child_chunks").mkdir(parents=True)
    (artifact_dir / "collections" / "rag_child_chunks" / "manifest.json").write_text(
        '{"row_count": 2}', encoding="utf-8"
    )
    first = sha256_path(artifact_dir)
    second = sha256_path(artifact_dir)
    assert first == second

    (artifact_dir / "collections" / "rag_child_chunks" / "manifest.json").write_text(
        '{"row_count": 3}', encoding="utf-8"
    )
    assert sha256_path(artifact_dir) != first


# 阅读注释（函数）：处理 测试 builder milvus 路径 closes reopens and verifies count 相关逻辑。
def test_builder_milvus_path_closes_reopens_and_verifies_count(tmp_path: Path, monkeypatch) -> None:
    """处理 测试 builder milvus 路径 closes reopens and verifies count 相关逻辑。

    参数:
        tmp_path: tmp 路径，具体约束请结合类型标注和调用方确认。
        monkeypatch: monkeypatch，具体约束请结合类型标注和调用方确认。

    返回:
        None

    阅读提示:
        主要直接调用：_write_jsonl, types.ModuleType, monkeypatch.setitem, OfflineIndexBuildConfig, SourceDatasetConfig, str, ComponentConfig, EmbeddingBuildConfig。
    """
    import sys
    import types

    source = tmp_path / "units.jsonl"
    _write_jsonl(source, RECORDS)
    clients = []
    persisted_count = {"value": 0}

    # 阅读注释（类）：封装 build 客户端，集中封装相关状态、依赖和行为。
    class _BuildClient:
        """封装 build 客户端，集中封装相关状态、依赖和行为。"""
        # 阅读注释（函数）：初始化 _BuildClient，保存运行所需的依赖、配置或状态。
        def __init__(self, uri: str):
            """初始化 _BuildClient，保存运行所需的依赖、配置或状态。

            参数:
                uri: uri，具体约束请结合类型标注和调用方确认。

            返回:
                未显式标注；请结合调用方和实际返回语句理解。

            阅读提示:
                主要直接调用：Path, db_dir.mkdir, write_text, clients.append。
            """
            self.uri = uri
            self.closed = False
            db_dir = Path(uri)
            db_dir.mkdir(parents=True, exist_ok=True)
            (db_dir / "manifest.json").write_text("{}", encoding="utf-8")
            clients.append(self)

        # 阅读注释（函数）：判断是否存在 collection。
        def has_collection(self, collection_name: str) -> bool:
            """判断是否存在 collection。

            参数:
                collection_name: collection 名称，具体约束请结合类型标注和调用方确认。

            返回:
                bool
            """
            return collection_name == "rag_child_chunks"

        # 阅读注释（函数）：获取 collection stats。
        def get_collection_stats(self, collection_name: str):
            """获取 collection stats。

            参数:
                collection_name: collection 名称，具体约束请结合类型标注和调用方确认。

            返回:
                未显式标注；请结合调用方和实际返回语句理解。
            """
            assert collection_name == "rag_child_chunks"
            return {"row_count": persisted_count["value"]}

        # 阅读注释（函数）：释放 _BuildClient 持有的资源。
        def close(self) -> None:
            """释放 _BuildClient 持有的资源。

            返回:
                None
            """
            self.closed = True

    fake_pymilvus = types.ModuleType("pymilvus")
    fake_pymilvus.MilvusClient = _BuildClient
    monkeypatch.setitem(sys.modules, "pymilvus", fake_pymilvus)

    fake_store = types.ModuleType("rag.vector_store.milvus_child_chunk_store")
    fake_store.create_or_reset_child_chunk_collection = lambda **kwargs: None
    fake_store.build_milvus_child_chunk_record = lambda child_chunk, vector, **kwargs: {
        "chunk_id": child_chunk["child_chunk_id"],
        "vector": vector.tolist(),
    }

    # 阅读注释（函数）：处理 fake insert 相关逻辑。
    def fake_insert(records, **kwargs):
        """处理 fake insert 相关逻辑。

        参数:
            records: 记录集合，具体约束请结合类型标注和调用方确认。
            **kwargs: 额外关键字参数。

        返回:
            未显式标注；请结合调用方和实际返回语句理解。

        阅读提示:
            主要直接调用：len。
        """
        persisted_count["value"] = len(records)
        return len(records)

    fake_store.insert_child_chunk_records = fake_insert
    monkeypatch.setitem(sys.modules, "rag.vector_store.milvus_child_chunk_store", fake_store)

    config = OfflineIndexBuildConfig(
        build_id="milvus_build",
        dataset_version="dataset_milvus_v1",
        source=SourceDatasetConfig(path=str(source)),
        chunker=ComponentConfig(
            name="fixed_parent_child",
            version="v1",
            params={
                "parent_chunk_size": 80,
                "parent_chunk_overlap": 10,
                "child_chunk_size": 40,
                "child_chunk_overlap": 5,
                "unit": "char",
            },
        ),
        embedding=EmbeddingBuildConfig(mode="hash", name="hash_embedding", version="v1", dim=32),
        index=IndexStorageConfig(backend="milvus_lite", collection_name="rag_child_chunks"),
        outputs=OutputConfig(root_dir=str(tmp_path / "indexes"), update_active_pointer=False),
        deterministic_created_at="2026-07-17T00:00:00+08:00",
    )
    result = OfflineIndexBuilder(project_root=tmp_path).build(config)
    manifest = IndexManifest.model_validate_json(Path(result.manifest_path).read_text(encoding="utf-8"))

    assert "milvus_lite" in manifest.artifacts
    assert manifest.artifacts["milvus_lite"].kind == "directory"
    assert manifest.artifacts["milvus_lite"].integrity_mode == "milvus_semantic_v1"
    assert len(clients) == 2
    assert all(client.closed for client in clients)


# 阅读注释（函数）：处理 测试 directory fingerprint persists 元数据 only fallback 相关逻辑。
def test_directory_fingerprint_persists_metadata_only_fallback(
    tmp_path: Path,
    monkeypatch,
) -> None:
    """处理 测试 directory fingerprint persists 元数据 only fallback 相关逻辑。

    参数:
        tmp_path: tmp 路径，具体约束请结合类型标注和调用方确认。
        monkeypatch: monkeypatch，具体约束请结合类型标注和调用方确认。

    返回:
        None

    阅读提示:
        主要直接调用：artifact_dir.mkdir, locked_file.write_bytes, monkeypatch.setattr, fingerprint_path, sha256_path, set。
    """
    from rag.offline.manifest import fingerprint_path

    artifact_dir = tmp_path / "milvus.db"
    artifact_dir.mkdir()
    locked_file = artifact_dir / "locked.internal"
    locked_file.write_bytes(b"milvus-data")

    original_open = Path.open

    # 阅读注释（函数）：处理 guarded open 相关逻辑。
    def guarded_open(self: Path, *args, **kwargs):
        """处理 guarded open 相关逻辑。

        参数:
            *args: 额外位置参数。
            **kwargs: 额外关键字参数。

        返回:
            未显式标注；请结合调用方和实际返回语句理解。

        阅读提示:
            主要直接调用：PermissionError, original_open。
        """
        if self == locked_file:
            raise PermissionError("simulated Windows exclusive lock")
        return original_open(self, *args, **kwargs)

    monkeypatch.setattr(Path, "open", guarded_open)
    first_hash, metadata_only_paths = fingerprint_path(artifact_dir)

    assert metadata_only_paths == ["locked.internal"]

    monkeypatch.setattr(Path, "open", original_open)
    second_hash = sha256_path(
        artifact_dir,
        metadata_only_paths=set(metadata_only_paths),
    )

    assert second_hash == first_hash


# 阅读注释（函数）：处理 测试 directory fingerprint detects same size content change 相关逻辑。
def test_directory_fingerprint_detects_same_size_content_change(tmp_path: Path) -> None:
    """处理 测试 directory fingerprint detects same size content change 相关逻辑。

    参数:
        tmp_path: tmp 路径，具体约束请结合类型标注和调用方确认。

    返回:
        None

    阅读提示:
        主要直接调用：artifact_dir.mkdir, manifest_file.write_text, sha256_path。
    """
    artifact_dir = tmp_path / "milvus.db"
    artifact_dir.mkdir()
    manifest_file = artifact_dir / "manifest.json"
    manifest_file.write_text('{"row_count": 2}', encoding="utf-8")
    before = sha256_path(artifact_dir)

    manifest_file.write_text('{"row_count": 3}', encoding="utf-8")
    after = sha256_path(artifact_dir)

    assert before != after
