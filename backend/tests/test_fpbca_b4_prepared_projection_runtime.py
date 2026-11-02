"""B4 prepared projection/admission controls with unchanged exact renderer oracles."""
from __future__ import annotations

from copy import deepcopy
from unittest.mock import patch

import pytest

from core.token_estimator import DeterministicTokenEstimator as Estimator
from rag.context.context_gate import ContextGate, ContextRequirements
from rag.context.context_packer import ContextPacker
from backend.tests.test_rag_lost_in_middle_priority_admission import (
    _results, _oracle, _assert_exact, _maximum_prefix, SUFFIX,
)


def _actors(**overrides):
    settings = dict(max_context_chars=10000, max_items=4, text_field="text",
                    dedup_parent=True, include_metadata=True)
    settings.update(overrides)
    return tuple(ContextPacker(**settings, packing_strategy=strategy)
                 for strategy in ("default", "lost_in_middle_aware"))


def _gate(default, lost, *, ratio=0.65):
    return ContextGate(default_packer=default, lost_in_middle_packer=lost,
        default_requirements=ContextRequirements(8192, 1024, 2000, 4, 10000),
        long_context_threshold_ratio=ratio)


@pytest.mark.parametrize("ratio, expected_strategy", [
    (0.05, "lost_in_middle_aware"), (0.95, "default"),
])
def test_gate_one_prepare_one_admit_then_one_actor_presentation(ratio, expected_strategy):
    rows = _results(count=4, body_chars=160)
    default, lost = _actors()
    prepared, admitted, yielded = [], [], []
    original_prepare, original_admit = default.prepare_projection, default.admit_projection

    def source():
        for row in rows:
            yielded.append(row["input_identity"])
            yield row

    def prepare(*args, **kwargs):
        value = original_prepare(*args, **kwargs)
        prepared.append(value)
        return value

    def admit(*args, **kwargs):
        value = original_admit(*args, **kwargs)
        admitted.append(value)
        return value

    with patch.object(default, "prepare_projection", side_effect=prepare) as prepare_call, \
         patch.object(default, "admit_projection", side_effect=admit) as admit_call, \
         patch.object(default, "_prompt_projection", wraps=default._prompt_projection) as body_calls, \
         patch.object(lost, "prepare_projection", side_effect=AssertionError("second actor prepare")), \
         patch.object(lost, "admit_projection", side_effect=AssertionError("second actor admission")), \
         patch.object(lost, "_prompt_projection", side_effect=AssertionError("second actor projection")), \
         patch.object(default, "pack", wraps=default.pack) as default_pack, \
         patch.object(lost, "pack", wraps=lost.pack) as lost_pack:
        packed = _gate(default, lost, ratio=ratio).pack(source())
    assert prepare_call.call_count == admit_call.call_count == 1
    assert body_calls.call_count == 4
    assert yielded == [row["input_identity"] for row in rows]
    assert len(prepared) == len(admitted) == 1 and admitted[0].projection is prepared[0]
    assert default_pack.call_count + lost_pack.call_count == 1
    called = lost_pack if expected_strategy == "lost_in_middle_aware" else default_pack
    assert called.call_count == 1 and packed.packing_strategy == expected_strategy
    assert called.call_args.kwargs["prepared_projection"] is prepared[0]
    assert called.call_args.kwargs["prepared_admission"] is admitted[0]
    assert (admitted[0].max_render_cost.tokens > int(2000 * ratio)) == (expected_strategy == "lost_in_middle_aware")
    assert {row["input_identity"] for row in packed.items} == {row["input_identity"] for row in rows}
    _assert_exact(packed, _oracle(lost if called is lost_pack else default, rows))


def test_equal_governed_visible_projection_ignores_vast_hidden_raw_pressure():
    short = _results(count=4, body_chars=2)
    for row in short:
        row["metadata"] = {"hidden_debug": {"raw_blob": "small"},
            "evidence_assessment_v2": {"evidence_role":"template_style", "metadata":{
                "structured_evidence":{"template":{"section_title":"方法章节",
                    "required_fields":["架构", "过程"], "style_hints":["保持边界"]}}}}}
    long = deepcopy(short)
    for row in long:
        row["text"] = row["parent_text"] = "隐藏原文数量999999台。" * 5000
        row["metadata"]["hidden_debug"]["raw_blob"] = "hidden-metadata" * 10000
    default, lost = _actors()
    short_projection = default.prepare_projection(short)
    long_projection = default.prepare_projection(long)
    assert short_projection.estimated_tokens == long_projection.estimated_tokens
    assert short_projection.estimated_chars == long_projection.estimated_chars
    admissions = []
    original = default.admit_projection

    def observe(*args, **kwargs):
        value = original(*args, **kwargs)
        admissions.append(value)
        return value

    with patch.object(default, "admit_projection", side_effect=observe), \
         patch.object(default, "_prompt_projection", side_effect=AssertionError("re-projected body")), \
         patch.object(lost, "_prompt_projection", side_effect=AssertionError("re-projected body")):
        gate = _gate(default, lost)
        left = gate.pack((), prepared_projection=short_projection)
        right = gate.pack((), prepared_projection=long_projection)
    assert len(admissions) == 2
    assert admissions[0].max_render_cost == admissions[1].max_render_cost
    assert left.packing_strategy == right.packing_strategy
    assert left.context == right.context
    assert [row["input_identity"] for row in left.items] == [row["input_identity"] for row in right.items]
    assert [row["input_identity"] for row in left.dropped_results] == [row["input_identity"] for row in right.dropped_results]
    # Native provenance legitimately differs; hidden source quotes are retained,
    # not erased to manufacture pressure equivalence.
    assert left.citations[0]["quote_text"] != right.citations[0]["quote_text"]
    assert all("999999" not in row["prompt_visible_text"] for row in right.items)
    assert left.tokens_used == right.tokens_used == Estimator.estimate(left.context)


def test_input_and_all_public_nested_outputs_cannot_mutate_reused_projection_admission():
    rows = _results(count=3, body_chars=160)
    for row in rows:
        row["metadata"] = {"nested":{"values":["owned"]},
            "retrieval_sources":["local_fixture"],
            "matched_child_chunks":[{"child_chunk_id":row["child_chunk_id"],
                "text":"原生子块引用", "title":"原生标题"}]}
    default, lost = _actors(max_items=2)
    projection = default.prepare_projection(rows)
    admission = default.admit_projection(projection, token_budget=2000, char_budget=8000)
    expected = [deepcopy(actor.pack((), token_budget=2000, char_budget=8000,
        prepared_projection=projection, prepared_admission=admission).to_dict()) for actor in (default, lost)]
    canonical_cost = admission.max_render_cost
    for row in rows:
        row["text"] = row["title"] = "external input mutation"
        row["metadata"]["nested"]["values"].append("external input")
        row["metadata"]["retrieval_sources"].append("external input")
        row["metadata"]["matched_child_chunks"][0]["text"] = "external input"
    for actor, baseline in zip((default, lost), expected):
        with patch.object(actor, "prepare_projection", side_effect=AssertionError("reprepare")), \
             patch.object(actor, "admit_projection", side_effect=AssertionError("readmit")), \
             patch.object(actor, "_prompt_projection", side_effect=AssertionError("reproject")):
            packed = actor.pack((), token_budget=2000, char_budget=8000,
                prepared_projection=projection, prepared_admission=admission)
            assert packed.to_dict() == baseline
            assert packed.items and packed.citations and packed.dropped_results
            packed.items[0]["text"] = "external selected body"
            packed.items[0]["metadata"]["nested"]["values"].append("external selected")
            packed.items[0]["metadata"]["matched_child_chunks"][0]["text"] = "external selected"
            packed.citations[0]["retrieval_sources"].append("external citation")
            packed.citations[0]["quote_text"] = "external citation"
            packed.dropped_results[0]["metadata"]["nested"]["values"].append("external dropped")
            packed.dropped_results[0]["metadata"]["retrieval_sources"].append("external dropped")
            packed.dropped_results[0]["text"] = "external dropped body"
            reused = actor.pack((), token_budget=2000, char_budget=8000,
                prepared_projection=projection, prepared_admission=admission)
            assert reused.to_dict() == baseline
    assert admission.max_render_cost == canonical_cost


def test_admission_requires_exact_projection_identity_even_for_identical_rows():
    default, _lost = _actors()
    rows = _results(count=2)
    first, second = default.prepare_projection(rows), default.prepare_projection(deepcopy(rows))
    admission = default.admit_projection(first, token_budget=2000, char_budget=8000)
    with patch.object(default, "admit_projection", side_effect=AssertionError("identity mismatch readmit")), \
         pytest.raises(ValueError, match="different projection"):
        default.pack((), token_budget=2000, char_budget=8000,
            prepared_projection=second, prepared_admission=admission)


@pytest.mark.parametrize("settings, max_items", [
    ({"text_field":"parent_text"}, None), ({"dedup_parent":False}, None),
    ({"include_metadata":False}, None), ({}, 3),
])
def test_prepared_representation_drift_is_rejected_without_admission(settings, max_items):
    default, _lost = _actors()
    projection = default.prepare_projection(_results(count=2))
    admission = default.admit_projection(projection, token_budget=2000, char_budget=8000)
    different, _unused = _actors(**settings)
    with patch.object(different, "admit_projection", side_effect=AssertionError("representation mismatch readmit")), \
         pytest.raises(ValueError, match="representation"):
        different.pack((), token_budget=2000, char_budget=8000, max_items=max_items,
            prepared_projection=projection, prepared_admission=admission)


@pytest.mark.parametrize("tokens, chars, actor_cap", [
    (1999, 8000, 10000), (2000, 7999, 10000), (2000, 8000, 7000),
])
def test_effective_budget_drift_rejects_without_eviction_or_second_admission(tokens, chars, actor_cap):
    default, _lost = _actors()
    projection = default.prepare_projection(_results(count=2))
    admission = default.admit_projection(projection, token_budget=2000, char_budget=8000)
    different, _unused = _actors(max_context_chars=actor_cap)
    with patch.object(different, "admit_projection", side_effect=AssertionError("budget mismatch readmit")), \
         pytest.raises(ValueError, match="budget"):
        different.pack((), token_budget=tokens, char_budget=chars,
            prepared_projection=projection, prepared_admission=admission)


def test_gate_resolves_both_actor_character_caps_before_its_only_admission():
    default, _unused = _actors(max_context_chars=6000)
    _unused, lost = _actors(max_context_chars=3500)
    observed = []
    original = default.admit_projection

    def observe(*args, **kwargs):
        value = original(*args, **kwargs)
        observed.append(value)
        return value

    with patch.object(default, "admit_projection", side_effect=observe), \
         patch.object(lost, "admit_projection", side_effect=AssertionError("actor cap readmission")):
        packed = _gate(default, lost, ratio=0.05).pack(_results(count=4, body_chars=160))
    assert len(observed) == 1 and observed[0].char_budget == packed.max_context_chars == 3500
    assert packed.used_chars <= observed[0].max_render_cost.chars <= 3500
    assert len(packed.items) == 4 and packed.packing_strategy == "lost_in_middle_aware"


def test_canonical_max_cost_reuses_original_exact_multichild_citation_oracle():
    rows = _results(count=2)
    rows[0]["metadata"]["matched_child_chunks"] = [
        {"child_chunk_id":f"s{i}", "text":f"来源{i}"} for i in range(1, 9)]
    rows[1]["metadata"]["matched_child_chunks"] = [
        {"child_chunk_id":"s1", "text":"共享子块"},
        {"child_chunk_id":"s10", "text":"第十引用"},
        {"child_chunk_id":"c2", "text":"第十一引用"}]
    default, lost = _actors()
    oracles = [_oracle(actor, rows) for actor in (default, lost)]
    tokens, chars = max(value.tokens for value in oracles), max(value.chars for value in oracles)
    projection = default.prepare_projection(rows)
    admission = default.admit_projection(projection, token_budget=tokens, char_budget=chars)
    assert admission.max_render_cost.tokens == tokens and admission.max_render_cost.chars == chars
    with patch.object(default, "admit_projection", side_effect=AssertionError("final readmission")), \
         patch.object(lost, "admit_projection", side_effect=AssertionError("final readmission")):
        packs = [actor.pack((), token_budget=tokens, char_budget=chars,
            prepared_projection=projection, prepared_admission=admission) for actor in (default, lost)]
    for packed, oracle in zip(packs, oracles):
        _assert_exact(packed, oracle)
        assert [row["citation_id"] for row in packed.citations] == [f"C{i}" for i in range(1, 12)]
        assert len([row for row in packed.citations if row["child_chunk_id"] == "s1"]) == 1


def test_both_presentations_preserve_original_maximal_prefix_and_provenance_oracle():
    rows = _results(count=3, body_chars=1800)
    rows[1]["text"], rows[2]["text"] = "后续候选" * 100, "小"
    default, lost = _actors()
    expected = _maximum_prefix(lost, rows[0], tokens=1000, chars=480)
    assert expected is not None and expected.endswith(SUFFIX)
    oracles = [_oracle(actor, rows[:1], texts={"c1":expected}) for actor in (default, lost)]
    # For one member the lost tag is a strict added cost, so this unchanged
    # exhaustive original oracle is the maximum-render envelope prefix oracle.
    assert oracles[1].tokens >= oracles[0].tokens and oracles[1].chars >= oracles[0].chars
    for later in rows[1:]:
        trials = [_oracle(actor, [rows[0], later], texts={"c1":expected}) for actor in (default, lost)]
        assert max(value.tokens for value in trials) > 1000 or max(value.chars for value in trials) > 480
    projection = default.prepare_projection(rows)
    admission = default.admit_projection(projection, token_budget=1000, char_budget=480)
    with patch.object(default, "admit_projection", side_effect=AssertionError("prefix readmission")), \
         patch.object(lost, "admit_projection", side_effect=AssertionError("prefix readmission")):
        packs = [actor.pack((), token_budget=1000, char_budget=480,
            prepared_projection=projection, prepared_admission=admission) for actor in (default, lost)]
    for packed, oracle in zip(packs, oracles):
        assert [row["chunk_id"] for row in packed.items] == ["c1"]
        assert packed.items[0]["prompt_visible_text"] == expected
        assert packed.items[0]["metadata"]["context_truncated"] is True
        assert packed.items[0]["pre_context_rank"] == 1
        assert packed.truncated_item_ids == ["p1"]
        assert [row["chunk_id"] for row in packed.dropped_results] == ["c2", "c3"]
        _assert_exact(packed, oracle)
