from __future__ import annotations

import json
from pathlib import Path

import pytest

from rag.config.pipeline_config import ComponentConfig
from rag.offline.builder import OfflineIndexBuilder
from rag.offline.config import (
    EmbeddingBuildConfig,
    IndexStorageConfig,
    OfflineIndexBuildConfig,
    OfflineIndexConfigLoader,
    OutputConfig,
    SourceDatasetConfig,
)
from rag.offline.manifest import IndexManifest
from rag.registry.default_registrations import build_default_component_registry
from rag.store.parent_chunk_store import load_jsonl_dicts


SAMPLE_RECORDS = [
    {
        "unit_id": "d1_u1",
        "doc_id": "d1",
        "source_type": "offline",
        "source_uri": "test://d1",
        "source_name": "D1",
        "source_format": "md",
        "title": "D1",
        "section": "概述",
        "section_level": 1,
        "page_start": 1,
        "page_end": 1,
        "unit_type": "paragraph",
        "unit_order": 1,
        "text": "# 概述\n企业知识库提供制度、规范和项目材料。",
        "language": "zh",
        "quality_score": 0.9,
        "quality_flags": [],
        "cleaning_version": "clean_v1",
        "extra": {},
    },
    {
        "unit_id": "d1_u2",
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
        "text": "# 技术方案\n采用父子分块、混合检索、重排序和引用绑定。",
        "language": "zh",
        "quality_score": 0.95,
        "quality_flags": [],
        "cleaning_version": "clean_v1",
        "extra": {},
    },
]


def _write_jsonl(path: Path, records: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(json.dumps(item, ensure_ascii=False) + "\n" for item in records), encoding="utf-8")


def _config(tmp_path: Path, *, chunker_name: str = "fixed_parent_child", parent_size: int = 80) -> OfflineIndexBuildConfig:
    source = tmp_path / "units.jsonl"
    _write_jsonl(source, SAMPLE_RECORDS)
    return OfflineIndexBuildConfig(
        build_id=f"build_{chunker_name}",
        dataset_version="dataset_v1",
        source=SourceDatasetConfig(path=str(source)),
        chunker=ComponentConfig(
            name=chunker_name,
            version="v1",
            params={
                "parent_chunk_size": parent_size,
                "parent_chunk_overlap": 10,
                "child_chunk_size": 40,
                "child_chunk_overlap": 5,
                "unit": "char",
            },
        ),
        embedding=EmbeddingBuildConfig(mode="hash", name="hash_embedding", version="v1", dim=32),
        index=IndexStorageConfig(backend="artifacts_only"),
        outputs=OutputConfig(root_dir=str(tmp_path / "indexes"), update_active_pointer=False),
        deterministic_created_at="2026-07-16T00:00:00+08:00",
    )


def test_registry_contains_four_chunkers() -> None:
    registry = build_default_component_registry()
    names = [item.name for item in registry.list_components(category="chunker")]
    assert names == [
        "fixed_parent_child",
        "heading_parent_child",
        "paragraph_parent_child",
        "recursive_parent_child",
    ]


@pytest.mark.parametrize(
    "name",
    [
        "fixed_parent_child",
        "recursive_parent_child",
        "heading_parent_child",
        "paragraph_parent_child",
    ],
)
def test_all_chunkers_build_parent_child_contract(name: str) -> None:
    registry = build_default_component_registry()
    component = ComponentConfig(
        name=name,
        version="v1",
        params={
            "parent_chunk_size": 80,
            "parent_chunk_overlap": 10,
            "child_chunk_size": 40,
            "child_chunk_overlap": 5,
            "unit": "char",
            "deterministic_created_at": "2026-07-16T00:00:00+08:00",
        },
    )
    chunker = registry.build(category="chunker", config=component)
    result = chunker.chunk_records(SAMPLE_RECORDS)
    assert result.parents
    assert result.children
    parent_ids = {item["parent_chunk_id"] for item in result.parents}
    assert all(item["parent_chunk_id"] in parent_ids for item in result.children)
    assert all(item["created_at"] == "2026-07-16T00:00:00+08:00" for item in result.parents)
    assert all(item["extra"]["chunker_plugin"]["name"] == name for item in result.children)


def test_same_input_and_config_are_reproducible() -> None:
    registry = build_default_component_registry()
    component = ComponentConfig(
        name="heading_parent_child",
        version="v1",
        params={
            "parent_chunk_size": 80,
            "parent_chunk_overlap": 10,
            "child_chunk_size": 40,
            "child_chunk_overlap": 5,
            "unit": "char",
            "deterministic_created_at": "2026-07-16T00:00:00+08:00",
        },
    )
    first = registry.build(category="chunker", config=component).chunk_records(SAMPLE_RECORDS)
    second = registry.build(category="chunker", config=component).chunk_records(SAMPLE_RECORDS)
    assert first.parents == second.parents
    assert first.children == second.children



def test_operational_fields_do_not_change_index_version(tmp_path: Path) -> None:
    first = _config(tmp_path / "a")
    payload = first.model_dump(mode="python")
    payload["build_id"] = "another_build"
    payload["notes"] = "different notes"
    payload["outputs"]["root_dir"] = str(tmp_path / "different_output")
    second = OfflineIndexBuildConfig.model_validate(payload)
    assert first.index_version() == second.index_version()
    assert first.config_hash() != second.config_hash()

def test_index_version_changes_when_chunk_params_change(tmp_path: Path) -> None:
    first = _config(tmp_path / "a", parent_size=80)
    second = _config(tmp_path / "b", parent_size=81)
    assert first.index_version() != second.index_version()


def test_index_version_changes_by_chunker_strategy(tmp_path: Path) -> None:
    fixed = _config(tmp_path / "fixed", chunker_name="fixed_parent_child")
    heading = _config(tmp_path / "heading", chunker_name="heading_parent_child")
    assert fixed.index_version() != heading.index_version()


def test_builder_creates_immutable_manifest_and_artifacts(tmp_path: Path) -> None:
    config = _config(tmp_path)
    builder = OfflineIndexBuilder(project_root=tmp_path)
    result = builder.build(config)
    assert result.status == "success"
    manifest_path = Path(result.manifest_path or "")
    assert manifest_path.exists()
    manifest = IndexManifest.model_validate_json(manifest_path.read_text(encoding="utf-8"))
    assert manifest.index_version == config.index_version()
    assert manifest.document_count == 1
    assert manifest.parent_chunk_count > 0
    assert manifest.child_chunk_count > 0
    assert manifest.chunker["name"] == "fixed_parent_child"
    assert manifest.embedding["dim"] == 32
    for artifact in manifest.artifacts.values():
        assert Path(artifact.path).exists()
    children = load_jsonl_dicts(manifest.artifacts["child_chunks"].path)
    assert all(item["extra"]["offline_index_version"] == manifest.index_version for item in children)

    repeated = builder.build(config)
    assert repeated.status == "already_exists"
    assert repeated.index_version == result.index_version


def test_loader_resolves_project_relative_path() -> None:
    loader = OfflineIndexConfigLoader()
    resolved = loader.resolve_path("backend/rag/index_profiles/fixed_parent_child_smoke_v1.yaml")
    assert resolved.name == "fixed_parent_child_smoke_v1.yaml"


def test_active_index_resolver_and_runtime_override(tmp_path: Path) -> None:
    from rag.offline.manifest import ActiveIndexPointer, ArtifactRecord, IndexManifest, sha256_file
    from rag.offline.resolver import ActiveIndexResolver
    from rag.runtime.parent_child_runtime_factory import ParentChildRuntimeFactory
    from rag.tools.rag_tool import RAGToolConfig

    index_dir = tmp_path / "index"
    index_dir.mkdir()
    parent = index_dir / "parent_chunks.jsonl"
    child = index_dir / "child_chunks.jsonl"
    db = index_dir / "milvus.db"
    parent.write_text("{}\n", encoding="utf-8")
    child.write_text("{}\n", encoding="utf-8")
    db.write_bytes(b"fake")
    manifest = IndexManifest(
        index_version="idx_test_v1",
        build_id="b1",
        dataset_version="d1",
        config_hash="c" * 64,
        source_hash="s" * 64,
        chunker={"name": "fixed_parent_child", "version": "v1"},
        embedding={"mode": "model", "model": "m3e-base", "version": "local", "dim": 768},
        index={"backend": "milvus_lite", "collection_name": "rag_child_chunks_v1", "metric_type": "COSINE"},
        document_count=1,
        parent_chunk_count=1,
        child_chunk_count=1,
        artifacts={
            "parent_chunks": ArtifactRecord(path=str(parent), sha256=sha256_file(parent), record_count=1),
            "child_chunks": ArtifactRecord(path=str(child), sha256=sha256_file(child), record_count=1),
            "milvus_lite": ArtifactRecord(path=str(db), sha256=sha256_file(db), record_count=1),
        },
        created_at="2026-07-16T00:00:00+08:00",
        reproducibility_hash="r" * 64,
    )
    manifest_path = index_dir / "index_manifest.json"
    manifest.write(manifest_path)
    pointer_path = tmp_path / "active_index.json"
    ActiveIndexPointer(
        index_version="idx_test_v1",
        manifest_path=str(manifest_path),
        manifest_sha256=sha256_file(manifest_path),
    ).write(pointer_path)

    resolved = ActiveIndexResolver(verify_artifacts=True).resolve(pointer_path)
    assert resolved["index_version"] == "idx_test_v1"
    assert resolved["parent_file"] == str(parent)

    cfg = RAGToolConfig(active_index_pointer=str(pointer_path))
    runtime_cfg = ParentChildRuntimeFactory().resolve_config(cfg, tmp_path)
    assert runtime_cfg.index_version == "idx_test_v1"
    assert runtime_cfg.parent_file == str(parent)
    assert runtime_cfg.db_file == str(db)
    assert runtime_cfg.collection_name == "rag_child_chunks_v1"
    assert runtime_cfg.index_lineage["status"] == "active_manifest"


def test_artifacts_only_cannot_be_activated(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="milvus_lite"):
        OfflineIndexBuildConfig(
            build_id="b1",
            dataset_version="d1",
            source=SourceDatasetConfig(path=str(tmp_path / "x.jsonl")),
            chunker=ComponentConfig(name="fixed_parent_child", version="v1"),
            embedding=EmbeddingBuildConfig(mode="hash", dim=32),
            index=IndexStorageConfig(backend="artifacts_only"),
            outputs=OutputConfig(root_dir=str(tmp_path / "out"), update_active_pointer=True),
        )


def test_real_model_embedding_profile_is_default_smoke_config() -> None:
    loader = OfflineIndexConfigLoader()
    config = loader.load("backend/rag/index_profiles/fixed_parent_child_smoke_v1.yaml")
    assert config.embedding.mode == "model"
    assert config.embedding.name == "m3e_base"
    assert config.embedding.version == "local_m3e_base_v1"
    assert config.embedding.model_name == "D:/models/huggingface/embedding/m3e-base"
    assert config.embedding.device == "cuda"
    assert config.embedding.dim == 768


def test_model_embedding_build_records_real_model_lineage(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    import numpy as np

    source = tmp_path / "units.jsonl"
    model_dir = tmp_path / "m3e-base"
    model_dir.mkdir()
    _write_jsonl(source, SAMPLE_RECORDS)
    calls: dict[str, object] = {}

    def fake_encode_texts_with_model(texts, model_name, device, batch_size, embedding_version):
        calls.update(
            texts=list(texts),
            model_name=model_name,
            device=device,
            batch_size=batch_size,
            embedding_version=embedding_version,
        )
        return np.ones((len(texts), 768), dtype="float32"), str(model_name), str(embedding_version)

    monkeypatch.setattr("rag.offline.builder.encode_texts_with_model", fake_encode_texts_with_model)
    config = OfflineIndexBuildConfig(
        build_id="model_build",
        dataset_version="dataset_model_v1",
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
            mode="model",
            name="m3e_base",
            version="local_m3e_base_v1",
            model_name=str(model_dir),
            device="cuda",
            batch_size=32,
            dim=768,
        ),
        index=IndexStorageConfig(backend="artifacts_only"),
        outputs=OutputConfig(root_dir=str(tmp_path / "indexes")),
        deterministic_created_at="2026-07-16T00:00:00+08:00",
    )

    result = OfflineIndexBuilder(project_root=tmp_path).build(config)
    manifest = IndexManifest.model_validate_json(Path(result.manifest_path or "").read_text(encoding="utf-8"))
    assert calls["model_name"] == str(model_dir)
    assert calls["device"] == "cuda"
    assert calls["batch_size"] == 32
    assert manifest.embedding == {
        "mode": "model",
        "name": "m3e_base",
        "model": str(model_dir),
        "version": "local_m3e_base_v1",
        "dim": 768,
        "device": "cuda",
        "batch_size": 32,
        "normalize_embeddings": True,
    }


def test_model_embedding_dimension_mismatch_is_rejected(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    import numpy as np

    source = tmp_path / "units.jsonl"
    model_dir = tmp_path / "m3e-base"
    model_dir.mkdir()
    _write_jsonl(source, SAMPLE_RECORDS)

    monkeypatch.setattr(
        "rag.offline.builder.encode_texts_with_model",
        lambda texts, model_name, device, batch_size, embedding_version: (
            np.ones((len(texts), 384), dtype="float32"),
            str(model_name),
            str(embedding_version),
        ),
    )
    config = OfflineIndexBuildConfig(
        build_id="bad_dim",
        dataset_version="dataset_bad_dim_v1",
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
            mode="model",
            name="m3e_base",
            version="local_m3e_base_v1",
            model_name=str(model_dir),
            device="cuda",
            batch_size=32,
            dim=768,
        ),
        index=IndexStorageConfig(backend="artifacts_only"),
        outputs=OutputConfig(root_dir=str(tmp_path / "indexes")),
    )
    with pytest.raises(ValueError, match="embedding dimension mismatch"):
        OfflineIndexBuilder(project_root=tmp_path).build(config)


def test_validate_rejects_missing_explicit_local_embedding_model(tmp_path: Path) -> None:
    source = tmp_path / "units.jsonl"
    _write_jsonl(source, SAMPLE_RECORDS)
    config = OfflineIndexBuildConfig(
        build_id="missing_model",
        dataset_version="dataset_missing_model_v1",
        source=SourceDatasetConfig(path=str(source)),
        chunker=ComponentConfig(name="fixed_parent_child", version="v1"),
        embedding=EmbeddingBuildConfig(
            mode="model",
            name="m3e_base",
            version="local_m3e_base_v1",
            model_name=str(tmp_path / "missing-m3e-base"),
            dim=768,
        ),
        index=IndexStorageConfig(backend="artifacts_only"),
        outputs=OutputConfig(root_dir=str(tmp_path / "indexes")),
    )
    with pytest.raises(FileNotFoundError, match="embedding model path not found"):
        OfflineIndexBuilder(project_root=tmp_path).validate(config)
