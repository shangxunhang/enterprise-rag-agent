from __future__ import annotations

from pathlib import Path

import pytest

from rag.config.pipeline_config import ComponentConfig, PipelineConfigLoader
from rag.plugins.context_packers import DefaultContextPacker, LostInMiddleContextPacker
from rag.registry.component_registry import ComponentRegistry
from rag.registry.default_registrations import build_default_component_registry


PROJECT_ROOT = Path(__file__).resolve().parents[2]


def _results() -> list[dict]:
    return [
        {
            "rank": index,
            "doc_id": f"doc-{index}",
            "chunk_id": f"chunk-{index}",
            "child_chunk_id": f"child-{index}",
            "parent_chunk_id": f"parent-{index}",
            "text": f"evidence-{index}",
            "score": 1.0 / index,
            "metadata": {},
        }
        for index in range(1, 7)
    ]


def test_profile_loads_and_hash_is_stable() -> None:
    path = PROJECT_ROOT / "backend/rag/profiles/hybrid_v1.yaml"
    loader = PipelineConfigLoader()

    first = loader.load(path)
    second = loader.load(path)

    assert first.profile_id == "hybrid_v1"
    assert first.context_packer.name == "lost_in_middle"
    assert first.config_hash() == second.config_hash()
    assert len(first.config_hash()) == 64


def test_registry_builds_context_packer_from_external_config() -> None:
    profile = PipelineConfigLoader().load(
        PROJECT_ROOT / "backend/rag/profiles/hybrid_v1.yaml"
    )
    registry = build_default_component_registry()

    component = registry.build(
        category="context_packer",
        config=profile.context_packer,
    )

    assert isinstance(component, LostInMiddleContextPacker)
    assert component.plugin_metadata.name == "lost_in_middle"
    assert component.plugin_metadata.version == "v1"


def test_switching_profile_changes_implementation_without_pipeline_code_change() -> None:
    loader = PipelineConfigLoader()
    registry = build_default_component_registry()

    lost = registry.build(
        category="context_packer",
        config=loader.load(
            PROJECT_ROOT / "backend/rag/profiles/hybrid_v1.yaml"
        ).context_packer,
    )
    default = registry.build(
        category="context_packer",
        config=loader.load(
            PROJECT_ROOT / "backend/rag/profiles/hybrid_default_context_v1.yaml"
        ).context_packer,
    )

    assert isinstance(lost, LostInMiddleContextPacker)
    assert isinstance(default, DefaultContextPacker)

    lost_ids = [item["chunk_id"] for item in lost.pack(_results()).selected_results]
    default_ids = [item["chunk_id"] for item in default.pack(_results()).selected_results]

    assert lost_ids == ["chunk-1", "chunk-3", "chunk-2"]
    assert default_ids == ["chunk-1", "chunk-2", "chunk-3"]


def test_unknown_component_fails_during_composition() -> None:
    registry = build_default_component_registry()

    with pytest.raises(ValueError, match="unknown RAG component"):
        registry.build(
            category="context_packer",
            config=ComponentConfig(name="missing", version="v1"),
        )


def test_duplicate_registration_is_rejected() -> None:
    registry: ComponentRegistry[object] = ComponentRegistry()
    registry.register(
        category="context_packer",
        name="default",
        version="v1",
        builder=DefaultContextPacker,
    )

    with pytest.raises(ValueError, match="duplicate RAG component registration"):
        registry.register(
            category="context_packer",
            name="default",
            version="v1",
            builder=DefaultContextPacker,
        )
