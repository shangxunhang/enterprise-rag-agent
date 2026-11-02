from __future__ import annotations

import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from rag.catalog.document_profile import (
    AuthorityLevel,
    AuthorityProfileSchema,
    ChunkStrategy,
    ClaimType,
    ClassificationMethod,
    ClassificationProfileSchema,
    ContentCapabilitiesSchema,
    DocumentProfileSchema,
    DocumentType,
    EvidenceRole,
    ParserStrategy,
    ParsingProfileSchema,
    ProjectRelation,
    ProjectRelationSchema,
)
from rag.catalog.document_profile_projection import project_source_record
from rag.catalog.document_profile_registry import DocumentProfileRegistry
from rag.config.static_retrieval import ComponentConfig
from rag.offline.builder import OfflineIndexBuilder
from rag.offline.config import (
    EmbeddingBuildConfig,
    IndexStorageConfig,
    OfflineIndexBuildConfig,
    OutputConfig,
    SourceDatasetConfig,
)
from rag.offline.manifest import IndexManifest, sha256_file
from rag.plugins.chunkers.plugin import TypeAwareParentChildChunkerPlugin
from rag.util.jsonl_utils import load_jsonl_dicts


ROOT = Path(__file__).resolve().parents[2]
REAL_SOURCE = ROOT / "data/raw/jsonl/cleaned_text_unit_v1_batch_20260803.jsonl"
REAL_PROFILES = ROOT / "data/catalog/document_profiles_v1.jsonl"
GOLD_DOCUMENT_ID = "doc_a8144d5d4e2b2478bbdfc1cd"


def _write_jsonl(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "".join(
            json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n"
            for row in rows
        ),
        encoding="utf-8",
    )


def _profile(
    *,
    document_id: str,
    source_sha256: str,
    chunk_strategy: ChunkStrategy,
    document_type: DocumentType = DocumentType.HISTORICAL_CONSTRUCTION_SCHEME,
    evidence_role: EvidenceRole = EvidenceRole.HISTORICAL_CASE,
    relation: ProjectRelation = ProjectRelation.OTHER_PROJECT,
    project_id: str | None = "other-project",
    kb_id: str = "historical_case_kb",
    should_index: bool = True,
    is_gold_reference: bool = False,
) -> DocumentProfileSchema:
    if relation is ProjectRelation.GENERAL:
        project_id = None
    return DocumentProfileSchema(
        document_id=document_id,
        document_type=document_type,
        evidence_role=evidence_role,
        kb_id=kb_id,
        source_name=f"{document_id}.docx",
        project_relation=ProjectRelationSchema(
            relation=relation,
            project_id=project_id,
        ),
        authority=AuthorityProfileSchema(
            level=AuthorityLevel.REFERENCE,
            issuer="Stage C Test Authority",
            version="test-v1",
            effective_date="2026-01-01",
            expiration_date=None,
        ),
        content_capabilities=ContentCapabilitiesSchema(
            allowed_claim_types=[
                ClaimType.GENERAL_METHOD,
                ClaimType.TYPICAL_ARCHITECTURE,
                ClaimType.REFERENCE_CASE,
            ],
            forbidden_claim_types=[
                ClaimType.CURRENT_PROJECT_FACT,
                ClaimType.CURRENT_PROJECT_COST,
                ClaimType.CURRENT_PROJECT_QUANTITY,
                ClaimType.CURRENT_PROJECT_SCHEDULE,
                ClaimType.CURRENT_PROJECT_CONFIGURATION,
            ],
        ),
        parsing=ParsingProfileSchema(
            parser_strategy=ParserStrategy.HIERARCHICAL_DOCUMENT,
            chunk_strategy=chunk_strategy,
        ),
        classification=ClassificationProfileSchema(
            method=ClassificationMethod.HYBRID_REVIEW,
            confidence=1.0,
            reviewed=True,
            reviewer="stage-c-test",
            reason_codes=["test_fixture"],
        ),
        metadata={
            "source_sha256": source_sha256,
            "should_index": should_index,
            "is_gold_reference": is_gold_reference,
            "source_document_form": "construction_scheme",
            "taxonomy_mapping": None,
            "reviewed_at": "2026-08-03T19:30:00+08:00",
        },
    )


def _unit(
    *,
    document_id: str,
    source_sha256: str,
    unit_order: int = 1,
    text: str = "1.1 建设内容\n采用分层架构形成可复用能力。",
) -> dict:
    return {
        "schema_version": "cleaned_text_unit_v1",
        "unit_id": f"{document_id}_u{unit_order:04d}",
        "doc_id": document_id,
        "tenant_id": "legacy-tenant",
        "kb_id": "default",
        "file_id": None,
        "source_type": "document",
        "source_uri": f"D:/raw/{document_id}.docx",
        "source_name": f"{document_id}.docx",
        "source_format": "docx",
        "batch_id": "stage-c-test",
        "title": "测试文档",
        "section": "建设内容",
        "section_level": 2,
        "page_start": 1,
        "page_end": 1,
        "unit_type": "paragraph",
        "unit_order": unit_order,
        "text": text,
        "text_length": len(text),
        "language": "zh",
        "quality_score": 1.0,
        "quality_flags": [],
        "cleaning_version": "test-v1",
        "extra": {},
        "source_sha256": source_sha256,
        "should_index": True,
        "is_gold_reference": False,
    }


def _config(
    *,
    source_path: Path,
    profiles_path: Path,
    output_root: Path,
) -> OfflineIndexBuildConfig:
    return OfflineIndexBuildConfig(
        build_id="stage-c-test",
        dataset_version="stage-c-profiled-v2",
        source=SourceDatasetConfig(
            path=str(source_path),
            document_profiles_path=str(profiles_path),
            require_document_profiles=True,
        ),
        chunker=ComponentConfig(
            name="type_aware_parent_child",
            version="v2",
            enabled=True,
            params={
                "unit": "char",
                "require_document_profile": True,
            },
        ),
        embedding=EmbeddingBuildConfig(
            mode="hash",
            name="hash_embedding",
            version="stage_c_v1",
            dim=16,
        ),
        index=IndexStorageConfig(backend="artifacts_only"),
        outputs=OutputConfig(
            root_dir=str(output_root),
            active_pointer=str(output_root / "active_index.json"),
        ),
        deterministic_created_at="2026-08-03T17:15:00+08:00",
    )


def test_real_stage_b_catalog_projects_exactly_30_indexable_documents() -> None:
    registry = DocumentProfileRegistry.from_jsonl(REAL_PROFILES)
    catalog_sha = sha256_file(REAL_PROFILES)
    output: list[dict] = []
    skipped: set[str] = set()

    for row in load_jsonl_dicts(REAL_SOURCE):
        document_id = str(row["doc_id"])
        projected = project_source_record(
            row,
            profile=registry.get(document_id),
            catalog_sha256=catalog_sha,
        )
        if projected is None:
            skipped.add(document_id)
        else:
            output.append(projected)

    assert len({row["doc_id"] for row in output}) == 30
    assert skipped == {GOLD_DOCUMENT_ID}
    assert {row["kb_id"] for row in output} == {
        "project_fact_kb",
        "historical_case_kb",
        "policy_standard_kb",
        "technical_reference_kb",
        "template_style_kb",
    }


def test_projection_overrides_legacy_scope_and_embeds_claim_boundary() -> None:
    profile = _profile(
        document_id="doc-profiled",
        source_sha256="a" * 64,
        chunk_strategy=ChunkStrategy.HEADING_PARENT_PARAGRAPH_CHILD,
    )
    source = _unit(document_id="doc-profiled", source_sha256="a" * 64)
    source["source_name"] = "legacy-mojibake-name.docx"
    source["file_id"] = "legacy-mojibake-name.docx"
    projected = project_source_record(
        source,
        profile=profile,
        catalog_sha256="b" * 64,
    )

    assert projected is not None
    assert projected["tenant_id"] == "default"
    assert projected["kb_id"] == "historical_case_kb"
    assert projected["source_name"] == "doc-profiled.docx"
    assert projected["file_id"] == "doc-profiled.docx"
    boundary = projected["extra"]["document_profile"]
    assert boundary["evidence_role"] == "historical_case"
    assert boundary["parsing"]["chunk_strategy"] == "heading_parent_paragraph_child"
    assert "current_project_fact" in boundary["content_capabilities"]["forbidden_claim_types"]
    assert boundary["authority"]["version"] == "test-v1"
    assert boundary["authority"]["issuer"] == "Stage C Test Authority"
    assert boundary["authority"]["expiration_date"] is None
    assert boundary["source_document_form"] == "construction_scheme"
    assert "taxonomy_mapping" in boundary
    assert boundary["catalog_sha256"] == "b" * 64


def test_projection_rejects_source_hash_mismatch() -> None:
    profile = _profile(
        document_id="doc-profiled",
        source_sha256="a" * 64,
        chunk_strategy=ChunkStrategy.HEADING_PARENT_PARAGRAPH_CHILD,
    )
    with pytest.raises(ValueError, match="source hash does not match"):
        project_source_record(
            _unit(document_id="doc-profiled", source_sha256="c" * 64),
            profile=profile,
            catalog_sha256="b" * 64,
        )


def test_source_config_requires_catalog_path_in_strict_mode() -> None:
    with pytest.raises(ValidationError, match="document_profiles_path"):
        SourceDatasetConfig(
            path="source.jsonl",
            require_document_profiles=True,
        )


def test_type_aware_chunker_routes_documents_and_preserves_profiles() -> None:
    heading_profile = _profile(
        document_id="doc-heading",
        source_sha256="1" * 64,
        chunk_strategy=ChunkStrategy.HEADING_PARENT_PARAGRAPH_CHILD,
    )
    table_profile = _profile(
        document_id="doc-table",
        source_sha256="2" * 64,
        chunk_strategy=ChunkStrategy.TABLE_ROW_RECORD,
    )
    rows = [
        project_source_record(
            _unit(document_id="doc-heading", source_sha256="1" * 64),
            profile=heading_profile,
            catalog_sha256="f" * 64,
        ),
        project_source_record(
            _unit(
                document_id="doc-table",
                source_sha256="2" * 64,
                text="设备名称 | 数量 | 说明\n服务器 | 4 | 历史案例",
            ),
            profile=table_profile,
            catalog_sha256="f" * 64,
        ),
    ]
    result = TypeAwareParentChildChunkerPlugin(
        deterministic_created_at="2026-08-03T17:15:00+08:00"
    ).chunk_records([row for row in rows if row is not None])

    assert {item["doc_id"] for item in result.children} == {
        "doc-heading",
        "doc-table",
    }
    selected = {
        item["doc_id"]: item["extra"]["type_aware_chunking"][
            "selected_profile_strategy"
        ]
        for item in result.children
    }
    assert selected["doc-heading"] == "heading_parent_paragraph_child"
    assert selected["doc-table"] == "table_row_record"
    assert all(
        "document_profile" in item["extra"] for item in result.children
    )


def test_type_aware_chunker_rejects_missing_profile_metadata() -> None:
    row = _unit(document_id="doc-unprofiled", source_sha256="3" * 64)
    row.pop("source_sha256")
    row.pop("should_index")
    row.pop("is_gold_reference")
    with pytest.raises(ValueError, match="requires document_profile metadata"):
        TypeAwareParentChildChunkerPlugin().chunk_records([row])


def test_builder_creates_profile_aware_artifacts_without_activation(
    tmp_path: Path,
) -> None:
    source_path = tmp_path / "source.jsonl"
    profiles_path = tmp_path / "profiles.jsonl"
    output_root = tmp_path / "indexes"
    profile = _profile(
        document_id="doc-build",
        source_sha256="4" * 64,
        chunk_strategy=ChunkStrategy.HEADING_PARENT_PARAGRAPH_CHILD,
    )
    _write_jsonl(
        source_path,
        [_unit(document_id="doc-build", source_sha256="4" * 64)],
    )
    _write_jsonl(
        profiles_path,
        [profile.model_dump(mode="json", exclude_none=True)],
    )

    config = _config(
        source_path=source_path,
        profiles_path=profiles_path,
        output_root=output_root,
    )
    result = OfflineIndexBuilder(project_root=ROOT).build(config)
    manifest = IndexManifest.model_validate_json(
        Path(result.manifest_path).read_text(encoding="utf-8")
    )

    assert result.document_count == 1
    assert result.parent_chunk_count >= 1
    assert result.child_chunk_count >= 1
    assert manifest.source["document_profiles_sha256"] == sha256_file(
        profiles_path
    )
    assert manifest.source["profile_document_count"] == 1
    assert manifest.chunker["execution"]["route_document_counts"] == {
        "heading_parent_paragraph_child": 1
    }
    assert not (output_root / "active_index.json").exists()

    child = json.loads(
        (Path(result.output_dir) / "child_chunks.jsonl")
        .read_text(encoding="utf-8")
        .splitlines()[0]
    )
    assert child["kb_id"] == "historical_case_kb"
    assert child["extra"]["document_profile"]["profile_sha256"]


def test_profile_catalog_changes_composite_source_hash(tmp_path: Path) -> None:
    source_path = tmp_path / "source.jsonl"
    profiles_path = tmp_path / "profiles.jsonl"
    profile = _profile(
        document_id="doc-hash",
        source_sha256="5" * 64,
        chunk_strategy=ChunkStrategy.HEADING_PARENT_PARAGRAPH_CHILD,
    )
    _write_jsonl(
        source_path,
        [_unit(document_id="doc-hash", source_sha256="5" * 64)],
    )
    _write_jsonl(
        profiles_path,
        [profile.model_dump(mode="json", exclude_none=True)],
    )
    config = _config(
        source_path=source_path,
        profiles_path=profiles_path,
        output_root=tmp_path / "indexes",
    )
    builder = OfflineIndexBuilder(project_root=ROOT)
    before = builder.validate(config).source_hash

    payload = profile.model_dump(mode="json", exclude_none=True)
    payload["metadata"]["catalog_revision"] = "review-2"
    _write_jsonl(profiles_path, [payload])
    after = builder.validate(config).source_hash

    assert before != after


def test_builder_rejects_unprofiled_document(tmp_path: Path) -> None:
    source_path = tmp_path / "source.jsonl"
    profiles_path = tmp_path / "profiles.jsonl"
    profile = _profile(
        document_id="doc-known",
        source_sha256="6" * 64,
        chunk_strategy=ChunkStrategy.HEADING_PARENT_PARAGRAPH_CHILD,
    )
    _write_jsonl(
        source_path,
        [_unit(document_id="doc-unknown", source_sha256="7" * 64)],
    )
    _write_jsonl(
        profiles_path,
        [profile.model_dump(mode="json", exclude_none=True)],
    )
    config = _config(
        source_path=source_path,
        profiles_path=profiles_path,
        output_root=tmp_path / "indexes",
    )

    with pytest.raises(ValueError, match="unprofiled document"):
        OfflineIndexBuilder(project_root=ROOT).build(config)


def test_builder_excludes_gold_profile_even_if_source_record_is_stale(
    tmp_path: Path,
) -> None:
    source_path = tmp_path / "source.jsonl"
    profiles_path = tmp_path / "profiles.jsonl"
    profile = _profile(
        document_id="doc-gold",
        source_sha256="8" * 64,
        chunk_strategy=ChunkStrategy.HEADING_PARENT_PARAGRAPH_CHILD,
        document_type=DocumentType.CURRENT_PROJECT_DECISION,
        evidence_role=EvidenceRole.PROJECT_FACT,
        relation=ProjectRelation.CURRENT_PROJECT,
        project_id="current-project",
        kb_id="project_fact_kb",
        should_index=False,
        is_gold_reference=True,
    )
    # Producer flags are deliberately stale; Stage C catalog must win.
    source = _unit(document_id="doc-gold", source_sha256="8" * 64)
    source["should_index"] = True
    source["is_gold_reference"] = False
    _write_jsonl(source_path, [source])
    _write_jsonl(
        profiles_path,
        [profile.model_dump(mode="json", exclude_none=True)],
    )
    config = _config(
        source_path=source_path,
        profiles_path=profiles_path,
        output_root=tmp_path / "indexes",
    )

    with pytest.raises(ValueError, match="zero child chunks"):
        OfflineIndexBuilder(project_root=ROOT).build(config)
