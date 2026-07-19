"""Token-budgeted context packing and ContextGate contracts."""

from __future__ import annotations

from pathlib import Path

import pytest

from rag.config.static_retrieval import ComponentConfig, StaticRetrievalSpecLoader
from rag.context.context_gate import ContextGate, ContextRequirements
from rag.plugins.context_packers import DefaultContextPacker, LostInMiddleContextPacker
from rag.registry.component_registry import ComponentRegistry
from rag.registry.default_registrations import build_default_component_registry


PROJECT_ROOT = Path(__file__).resolve().parents[2]
SPEC_PATH = PROJECT_ROOT / "backend/rag/config/static_retrieval_v1.yaml"


def _results(*, text_size: int = 10) -> list[dict]:
    return [
        {
            "rank": index,
            "doc_id": f"doc-{index}",
            "chunk_id": f"chunk-{index}",
            "child_chunk_id": f"child-{index}",
            "parent_chunk_id": f"parent-{index}",
            "text": f"evidence-{index}-" + ("证据" * text_size),
            "score": 1.0 / index,
            "metadata": {},
        }
        for index in range(1, 7)
    ]


def _packers():
    spec = StaticRetrievalSpecLoader().load(SPEC_PATH)
    registry = build_default_component_registry()
    built = {
        config.name: registry.build(category="context_packer", config=config)
        for config in spec.context_packers
    }
    return spec, built["default"], built["lost_in_middle"]


def test_static_spec_hash_is_stable_and_declares_both_packers() -> None:
    loader = StaticRetrievalSpecLoader()
    first = loader.load(SPEC_PATH)
    second = loader.load(SPEC_PATH)

    assert {item.name for item in first.context_packers} == {
        "default",
        "lost_in_middle",
    }
    assert first.config_hash() == second.config_hash()
    assert len(first.config_hash()) == 64


def test_context_gate_selects_strategy_from_measured_candidate_size() -> None:
    spec, default, lost = _packers()
    requirements = ContextRequirements(
        model_context_window=8192,
        prompt_reserved_tokens=1024,
        section_token_budget=4096,
        max_evidence_items=5,
        max_context_chars=6000,
    )
    gate = ContextGate(
        default_packer=default,
        lost_in_middle_packer=lost,
        default_requirements=requirements,
        long_context_threshold_ratio=spec.context_gate.long_context_threshold_ratio,
    )

    short = gate.pack(_results(text_size=1), requirements=requirements)
    long = gate.pack(_results(text_size=500), requirements=requirements)

    assert short.packing_strategy == "default"
    assert long.packing_strategy == "lost_in_middle_aware"


def test_lost_in_middle_reorders_selected_evidence_to_prompt_edges() -> None:
    _, default, lost = _packers()

    lost_ids = [
        item["chunk_id"]
        for item in lost.pack(_results(), token_budget=4096, max_items=5).items
    ]
    default_ids = [
        item["chunk_id"]
        for item in default.pack(_results(), token_budget=4096, max_items=5).items
    ]

    assert lost_ids == ["chunk-1", "chunk-3", "chunk-5", "chunk-4", "chunk-2"]
    assert default_ids == ["chunk-1", "chunk-2", "chunk-3", "chunk-4", "chunk-5"]


def test_context_pack_never_exceeds_token_or_character_budget() -> None:
    packer = DefaultContextPacker(max_context_chars=2000, max_items=5)

    packed = packer.pack(
        _results(text_size=1000),
        token_budget=128,
        max_items=5,
        char_budget=800,
    )

    assert packed.tokens_used <= packed.token_budget == 128
    assert packed.used_chars <= packed.max_context_chars == 800
    assert packed.rendered_text == packed.context
    assert packed.truncated_item_ids


def test_context_pack_markers_match_expanded_child_citations() -> None:
    packer = DefaultContextPacker(max_context_chars=2000, max_items=2)
    result = {
        "rank": 1,
        "doc_id": "doc-1",
        "child_chunk_id": "child-1",
        "parent_chunk_id": "parent-1",
        "child_text": "认证采用 JWT。",
        "parent_text": "认证采用 JWT，并实施最小权限控制。",
        "text": "认证采用 JWT，并实施最小权限控制。",
        "metadata": {
            "matched_child_chunks": [
                {"child_chunk_id": "child-1", "text": "认证采用 JWT。"},
                {"child_chunk_id": "child-2", "text": "实施最小权限控制。"},
            ]
        },
    }

    packed = packer.pack([result], token_budget=512)

    assert packed.context.startswith("[C1] [C2]")
    assert [item["citation_id"] for item in packed.citations] == ["C1", "C2"]
    assert [item["child_chunk_id"] for item in packed.citations] == [
        "child-1",
        "child-2",
    ]


def test_context_pack_honors_small_character_budget_exactly() -> None:
    packer = DefaultContextPacker(max_context_chars=128, max_items=1)

    packed = packer.pack(
        _results(text_size=100),
        token_budget=512,
        char_budget=96,
    )

    assert packed.max_context_chars == 96
    assert packed.used_chars <= 96


def test_unknown_context_packer_fails_during_composition() -> None:
    with pytest.raises(ValueError, match="unknown RAG component"):
        build_default_component_registry().build(
            category="context_packer",
            config=ComponentConfig(name="missing"),
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
