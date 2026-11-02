from __future__ import annotations

import json
from pathlib import Path

import numpy as np

from rag.catalog.document_profile_projection import project_source_record
from rag.catalog.document_profile_registry import DocumentProfileRegistry
from rag.offline.builder import OfflineIndexBuilder
from rag.offline.manifest import IndexManifest, sha256_file
from rag.schema.BM25Record_Schema import build_bm25_record_v1
from rag.schema.VectorIndexRecord_Schema import build_vector_index_record_v2
from rag.vector_store.milvus_child_chunk_store import (
    build_milvus_child_chunk_record,
)
from test_stage_c_profile_aware_indexing import (
    REAL_PROFILES,
    REAL_SOURCE,
    _config,
    _profile,
    _unit,
    _write_jsonl,
)
from rag.catalog.document_profile import ChunkStrategy


REQUIRED_PROFILE_FIELDS = {
    "schema_version",
    "document_id",
    "source_sha256",
    "document_type",
    "evidence_role",
    "kb_id",
    "tenant_id",
    "project_relation",
    "project_id",
    "authority",
    "content_capabilities",
    "parsing",
    "classification",
    "source_document_form",
    "taxonomy_mapping",
    "should_index",
    "is_gold_reference",
    "profile_sha256",
    "catalog_sha256",
}


def _jsonl(path: Path) -> list[dict]:
    return [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def _profile_from_record(record: dict) -> dict:
    extra = record.get("extra")
    if isinstance(extra, dict) and isinstance(extra.get("document_profile"), dict):
        return extra["document_profile"]
    direct = record.get("document_profile")
    assert isinstance(direct, dict)
    return direct


def test_real_0618_profile_projection_keeps_authority_and_taxonomy() -> None:
    registry = DocumentProfileRegistry.from_jsonl(REAL_PROFILES)
    profile = registry.get("doc_da8cd497ed9aa16c5cd1dd16")
    source = next(
        json.loads(line)
        for line in REAL_SOURCE.read_text(encoding="utf-8").splitlines()
        if '"doc_id": "doc_da8cd497ed9aa16c5cd1dd16"' in line
        or '"doc_id":"doc_da8cd497ed9aa16c5cd1dd16"' in line
    )
    projected = project_source_record(
        source,
        profile=profile,
        catalog_sha256=sha256_file(REAL_PROFILES),
    )
    assert projected is not None
    canonical = projected["extra"]["document_profile"]
    assert REQUIRED_PROFILE_FIELDS.issubset(canonical)
    assert canonical["source_document_form"] == "feasibility_study"
    assert canonical["authority"]["level"] == "reference"
    assert canonical["authority"]["version"] == "0618"
    assert canonical["taxonomy_mapping"] == (
        "current_project_requirement_due_to_v1_taxonomy"
    )
    for field in (
        "issuer",
        "effective_date",
        "expiration_date",
    ):
        assert field in canonical["authority"]


def test_profile_lineage_is_identical_across_all_file_artifacts(
    tmp_path: Path,
) -> None:
    source_path = tmp_path / "source.jsonl"
    profiles_path = tmp_path / "profiles.jsonl"
    output_root = tmp_path / "indexes"
    profile = _profile(
        document_id="doc-lineage",
        source_sha256="8" * 64,
        chunk_strategy=ChunkStrategy.HEADING_PARENT_PARAGRAPH_CHILD,
    )
    rows = [
        _unit(
            document_id="doc-lineage",
            source_sha256="8" * 64,
            unit_order=1,
            text="第一章 建设方案",
        ),
        _unit(
            document_id="doc-lineage",
            source_sha256="8" * 64,
            unit_order=2,
            text="建设内容采用分层架构。",
        ),
    ]
    rows[0]["unit_type"] = "title"
    _write_jsonl(source_path, rows)
    _write_jsonl(
        profiles_path,
        [profile.model_dump(mode="json", exclude_none=False)],
    )
    config = _config(
        source_path=source_path,
        profiles_path=profiles_path,
        output_root=output_root,
    )
    result = OfflineIndexBuilder(project_root=Path.cwd()).build(config)
    output = Path(result.output_dir)
    manifest = IndexManifest.model_validate_json(
        (output / "index_manifest.json").read_text(encoding="utf-8")
    )

    expected_artifacts = {
        "profiled_cleaned_text_units",
        "parent_chunks",
        "child_chunks",
        "vectors",
        "vector_index_records",
        "bm25_records",
    }
    assert expected_artifacts.issubset(manifest.artifacts)

    units = _jsonl(output / "profiled_cleaned_text_units.jsonl")
    parents = _jsonl(output / "parent_chunks.jsonl")
    children = _jsonl(output / "child_chunks.jsonl")
    vectors = _jsonl(output / "vector_index_records.jsonl")
    bm25 = _jsonl(output / "bm25_records.jsonl")
    assert len(vectors) == len(children) == len(bm25)

    all_records = [*units, *parents, *children, *vectors, *bm25]
    profiles = [_profile_from_record(record) for record in all_records]
    assert all(REQUIRED_PROFILE_FIELDS.issubset(item) for item in profiles)
    profile_hashes = {item["profile_sha256"] for item in profiles}
    assert len(profile_hashes) == 1
    canonical = profiles[0]
    assert canonical["authority"] == {
        "level": "reference",
        "issuer": "Stage C Test Authority",
        "version": "test-v1",
        "effective_date": "2026-01-01",
        "expiration_date": None,
    }
    assert canonical["source_document_form"] == "construction_scheme"
    assert "taxonomy_mapping" in canonical
    assert canonical["content_capabilities"]["allowed_claim_types"]
    assert canonical["content_capabilities"]["forbidden_claim_types"]

    child_by_id = {item["child_chunk_id"]: item for item in children}
    vector_by_id = {item["child_chunk_id"]: item for item in vectors}
    bm25_by_id = {item["child_chunk_id"]: item for item in bm25}
    assert set(child_by_id) == set(vector_by_id) == set(bm25_by_id)
    for child_id, child in child_by_id.items():
        assert vector_by_id[child_id]["extra"]["document_profile"] == child["extra"]["document_profile"]
        assert vector_by_id[child_id]["extra"]["type_aware_chunking"] == child["extra"]["type_aware_chunking"]
        assert bm25_by_id[child_id]["document_profile"] == child["extra"]["document_profile"]
        assert bm25_by_id[child_id]["type_aware_chunking"] == child["extra"]["type_aware_chunking"]
        assert bm25_by_id[child_id]["parent_chunk_id"] == child["parent_chunk_id"]


def test_vector_and_bm25_reject_missing_profile_lineage() -> None:
    child = {
        "child_chunk_id": "child-1",
        "chunk_id": "child-1",
        "parent_chunk_id": "parent-1",
        "doc_id": "doc-1",
        "tenant_id": "default",
        "kb_id": "project_fact_kb",
        "text": "测试",
        "extra": {},
    }
    try:
        build_vector_index_record_v2(
            child_chunk=child,
            embedding_model="test",
            embedding_dim=8,
            index_name="index",
            extra={"_require_profile_lineage": True},
        )
    except ValueError as exc:
        assert "document_profile" in str(exc)
    else:
        raise AssertionError("missing vector lineage must fail")

    try:
        build_bm25_record_v1(
            child_chunk=child,
            dataset_version="v1",
            offline_index_config_hash="hash",
        )
    except ValueError as exc:
        assert "document_profile" in str(exc)
    else:
        raise AssertionError("missing BM25 lineage must fail")


def test_milvus_extra_json_preserves_canonical_profile_and_chunking() -> None:
    profile = {
        "schema_version": "document_profile_v1",
        "document_id": "doc-1",
        "source_sha256": "a" * 64,
        "document_type": "current_project_requirement",
        "evidence_role": "project_fact",
        "kb_id": "project_fact_kb",
        "tenant_id": "default",
        "project_relation": "current_project",
        "project_id": _local_historical_project_id(),
        "authority": {
            "level": "reference",
            "issuer": None,
            "version": "0618",
            "effective_date": None,
            "expiration_date": None,
        },
        "content_capabilities": {
            "allowed_claim_types": ["general_method"],
            "forbidden_claim_types": ["current_project_fact"],
        },
        "parsing": {
            "parser_strategy": "hierarchical_document",
            "chunk_strategy": "fact_record",
            "fallback_chunk_strategy": "fixed_window_fallback",
        },
        "classification": {
            "method": "hybrid_review",
            "confidence": 1.0,
            "reviewed": True,
            "reviewed_by": "reviewer",
            "reviewed_at": None,
            "reason_codes": [],
        },
        "source_document_form": "feasibility_study",
        "taxonomy_mapping": "current_project_requirement_due_to_v1_taxonomy",
        "should_index": True,
        "is_gold_reference": False,
        "profile_sha256": "b" * 64,
        "catalog_sha256": "c" * 64,
    }
    chunking = {
        "strategy": "fact_record",
        "representation_type": "fact_record",
        "parser_version": "word-v1",
        "chunker_version": "child-v2",
        "structure_path": ["投资估算"],
        "fallback_used": False,
        "fallback_reason": None,
        "fallback_strategy": None,
        "original_strategy": "fact_record",
        "actual_strategy": "fact_record",
    }
    child = {
        "child_chunk_id": "child-1",
        "chunk_id": "child-1",
        "parent_chunk_id": "parent-1",
        "doc_id": "doc-1",
        "tenant_id": "default",
        "kb_id": "project_fact_kb",
        "file_id": "doc-1.docx",
        "text": "估算投资500万元",
        "extra": {
            "document_profile": profile,
            "type_aware_chunking": chunking,
        },
    }
    row = build_milvus_child_chunk_record(
        child,
        np.zeros(8, dtype=np.float32),
        embedding_model="test",
        embedding_dim=8,
        embedding_version="v1",
        index_name="index",
        index_version="idx-v1",
    )
    extra = json.loads(row["extra_json"])
    assert extra["document_profile"] == profile
    assert extra["type_aware_chunking"] == chunking
    assert row["document_type"] == profile["document_type"]
    assert row["evidence_role"] == profile["evidence_role"]
    assert row["project_relation"] == profile["project_relation"]
    assert row["project_id"] == profile["project_id"]
    assert row["authority_level"] == "reference"


def _local_historical_project_id() -> str:
    """Original historical identity is private; never synthesize a replacement."""
    import os
    import pytest
    value = os.environ.get("RAG_LOCAL_HISTORICAL_PROJECT_ID")
    if not value:
        pytest.skip("requires local archive identity: RAG_LOCAL_HISTORICAL_PROJECT_ID")
    return value
