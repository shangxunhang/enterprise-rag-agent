from __future__ import annotations

import shutil
from pathlib import Path

import pytest
import yaml

from rag.adapters.legacy.evidence_mapper import LegacyEvidenceMapper
from rag.adapters.legacy.request_mapper import LegacyRAGRequestMapper
from rag.config.pipeline_config import PipelineConfigLoader
from rag.config.profile_catalog import OnlineRAGProfileCatalogValidator
from rag.plugins.context_packers import DefaultContextPacker
from rag.registry.default_registrations import build_default_component_registry
from schemas.rag import RAGToolInputSchema


PROJECT_ROOT = Path(__file__).resolve().parents[2]
PROFILE_DIR = PROJECT_ROOT / "backend/rag/profiles"


def test_relative_profile_path_is_independent_of_process_cwd(monkeypatch) -> None:
    monkeypatch.chdir(PROJECT_ROOT / "scripts")

    profile = PipelineConfigLoader().load(
        "backend/rag/profiles/self_rag_v1.yaml"
    )

    assert profile.profile_id == "self_rag_v1"




def test_cli_profile_path_resolves_against_project_root(monkeypatch) -> None:
    from mainline_runtime import resolve_project_path

    monkeypatch.chdir(PROJECT_ROOT / "scripts")
    resolved = resolve_project_path(
        "backend/rag/profiles/self_rag_v1.yaml",
        project_root=PROJECT_ROOT,
    )

    assert resolved == (
        PROJECT_ROOT / "backend/rag/profiles/self_rag_v1.yaml"
    ).resolve()


def test_explicit_project_root_resolves_relative_profile_path(tmp_path) -> None:
    root = tmp_path / "project"
    target = root / "backend/rag/profiles/hybrid_v1.yaml"
    target.parent.mkdir(parents=True)
    shutil.copy2(PROFILE_DIR / "hybrid_v1.yaml", target)

    profile = PipelineConfigLoader().load(
        "backend/rag/profiles/hybrid_v1.yaml",
        project_root=root,
    )

    assert profile.profile_id == "hybrid_v1"


def test_all_online_profiles_pass_schema_and_registry_startup_validation() -> None:
    report = OnlineRAGProfileCatalogValidator().validate(
        project_root=PROJECT_ROOT,
        registry=build_default_component_registry(),
    )

    assert report.profile_count == 11
    assert {item.profile_id for item in report.profiles} == {
        path.stem for path in PROFILE_DIR.glob("*.yaml")
    }
    assert all(item.component_count >= 11 for item in report.profiles)


def test_catalog_validation_rejects_unregistered_component(tmp_path) -> None:
    root = tmp_path / "project"
    copied = root / "backend/rag/profiles"
    shutil.copytree(PROFILE_DIR, copied)
    path = copied / "hybrid_v1.yaml"
    payload = yaml.safe_load(path.read_text(encoding="utf-8"))
    payload["evidence_grader"]["name"] = "missing_grader"
    path.write_text(
        yaml.safe_dump(payload, allow_unicode=True, sort_keys=False),
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="unregistered component"):
        OnlineRAGProfileCatalogValidator().validate(
            project_root=root,
            registry=build_default_component_registry(),
        )


def test_context_packer_reassigns_contiguous_agent_facing_ranks() -> None:
    packer = DefaultContextPacker(
        max_context_chars=6000,
        max_items=3,
        text_field="text",
    )
    packed = packer.pack(
        [
            {
                "rank": rank,
                "doc_id": f"doc-{rank}",
                "chunk_id": f"child-{rank}",
                "child_chunk_id": f"child-{rank}",
                "parent_chunk_id": f"parent-{rank}",
                "text": f"evidence-{rank}",
                "metadata": {},
            }
            for rank in (1, 3, 7)
        ]
    )

    assert [item["rank"] for item in packed.selected_results] == [1, 2, 3]
    assert [item["context_rank"] for item in packed.selected_results] == [1, 2, 3]
    assert [item["pre_context_rank"] for item in packed.selected_results] == [1, 3, 7]
    assert [item["metadata"]["pre_context_rank"] for item in packed.selected_results] == [
        1,
        3,
        7,
    ]


def test_evidence_mapper_guarantees_contiguous_output_ranks() -> None:
    chunks = LegacyEvidenceMapper().chunks(
        [
            {
                "rank": 1,
                "doc_id": "doc-a",
                "child_chunk_id": "child-a",
                "parent_chunk_id": "parent-a",
                "child_text": "a",
                "parent_text": "A",
            },
            {
                "rank": 3,
                "doc_id": "doc-b",
                "child_chunk_id": "child-b",
                "parent_chunk_id": "parent-b",
                "child_text": "b",
                "parent_text": "B",
            },
        ]
    )

    assert [item.rank for item in chunks] == [1, 2]
    assert [item.metadata["pre_output_rank"] for item in chunks] == [1, 3]


def test_legacy_strategy_name_no_longer_enables_online_plugins() -> None:
    request = RAGToolInputSchema(
        task_id="task-1",
        run_id="run-1",
        agent_name="SchemeWriterAgent",
        query="test query",
        retrieval_mode="c_rag_self_rag_hyde",
    )

    invocation = LegacyRAGRequestMapper().map(request)

    assert invocation.payload["enable_hyde"] is False
    assert invocation.payload["enable_crag"] is False
    assert invocation.payload["enable_self_rag"] is False


def test_online_pipeline_source_has_no_legacy_crag_strategy_name_branch() -> None:
    source = (
        PROJECT_ROOT / "backend/rag/application/parent_child_retrieval.py"
    ).read_text(encoding="utf-8")
    request_mapper_source = (
        PROJECT_ROOT / "backend/rag/adapters/legacy/request_mapper.py"
    ).read_text(encoding="utf-8")

    assert 'normalized in {\n                "c_rag"' not in source
    assert "strategy_lower" not in request_mapper_source
    assert "advisory_only" in source
