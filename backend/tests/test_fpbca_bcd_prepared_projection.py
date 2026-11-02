"""B4 frozen shared projection/cost/priority controls; no model or provider."""
from unittest.mock import patch

from core.token_estimator import DeterministicTokenEstimator
from rag.context.context_packer import ContextPacker
from rag.context.context_gate import ContextGate, ContextRequirements


def rows():
    return [{"rank": i, "doc_id": f"doc-{i}", "chunk_id": f"child-{i}", "child_chunk_id": f"child-{i}",
        "parent_chunk_id": f"parent-{i}", "title": "受控标题" * 3, "section": "参数章节", "text": "证据正文" * 10,
        "metadata": {}} for i in range(1, 5)]


def test_b4_prepared_projection_reused_without_second_body_projection():
    default = ContextPacker(max_context_chars=10000, max_items=4, packing_strategy="default")
    lost = ContextPacker(max_context_chars=10000, max_items=4, packing_strategy="lost_in_middle_aware")
    projection = default.prepare_projection(rows())
    assert projection.estimated_tokens >= sum(DeterministicTokenEstimator.estimate(r["text"]) for r in rows())
    with patch.object(default, "_prompt_projection", side_effect=AssertionError("second projection")), patch.object(lost, "_prompt_projection", side_effect=AssertionError("second projection")):
        packs = [p.pack([], token_budget=1000, prepared_projection=projection) for p in (default, lost)]
    assert {r["child_chunk_id"] for r in packs[0].items} == {r["child_chunk_id"] for r in packs[1].items}


def test_b4_gate_measures_and_packs_same_prepared_projection():
    default = ContextPacker(max_context_chars=10000, max_items=4, packing_strategy="default")
    lost = ContextPacker(max_context_chars=10000, max_items=4, packing_strategy="lost_in_middle_aware")
    projection = default.prepare_projection(rows())
    gate = ContextGate(default_packer=default, lost_in_middle_packer=lost,
        default_requirements=ContextRequirements(8192, 1024, 2000, 4, 10000))
    with patch.object(default, "_prompt_projection", side_effect=AssertionError("duplicate preparation")), patch.object(lost, "_prompt_projection", side_effect=AssertionError("duplicate preparation")):
        pack = gate.pack([], prepared_projection=projection)
    assert pack.tokens_used == DeterministicTokenEstimator.estimate(pack.context)


def test_b4_borderline_strategy_independent_membership_real_final_cost():
    packers = [ContextPacker(max_context_chars=10000, max_items=4, packing_strategy=s) for s in ("default", "lost_in_middle_aware")]
    projection = packers[0].prepare_projection(rows())
    for budget in range(80, 750, 17):
        packs = [p.pack([], token_budget=budget, prepared_projection=projection) for p in packers]
        assert [sorted(r["child_chunk_id"] for r in p.items) for p in packs][0] == [sorted(r["child_chunk_id"] for r in p.items) for p in packs][1]
        assert [sorted(r["child_chunk_id"] for r in p.dropped_results) for p in packs][0] == [sorted(r["child_chunk_id"] for r in p.dropped_results) for p in packs][1]
        for pack in packs:
            assert pack.tokens_used <= budget
            assert pack.tokens_used == DeterministicTokenEstimator.estimate(pack.context)
            selected = sorted(int(r["rank"]) for r in pack.items)
            assert selected == list(range(1, len(selected) + 1))
