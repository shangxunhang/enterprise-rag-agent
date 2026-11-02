"""RWP-SEL-001 D1-D28: priority admission, then frozen presentation.

All checks are offline.  The cost oracle reconstructs the unchanged public
rendering format independently; it does not call the new admission renderer or
``pack`` to decide whether an expected membership fits.
"""

from __future__ import annotations

import ast
from collections import Counter
from copy import copy, deepcopy
from dataclasses import dataclass
import hashlib
from pathlib import Path
from typing import Any

import pytest
import yaml

from core.token_estimator import DeterministicTokenEstimator as Estimator
from rag.context.context_gate import ContextGate, ContextRequirements
from rag.context.context_packer import ContextPacker
from rag.evidence.citation_sources import citation_sources
from rag.plugins.context_packers import DefaultContextPacker, LostInMiddleContextPacker


REPO = Path(__file__).resolve().parents[2]
STRATEGIES = ("default", "lost_in_middle_aware")
SUFFIX = "\n...[TRUNCATED]"
HISTORICAL_PROBE = (
    REPO
    / "docs/rwp_prod_002_independent_problem_solution_audit_20261006_s06459926"
    / "phase_a_probe.py"
)


def _results(count: int = 5, body_chars: int = 120) -> list[dict[str, Any]]:
    """Use the original independent witness's equal-size source identities."""
    assert body_chars % 2 == 0
    return [
        {
            "rank": i,
            "input_identity": f"entry-{i}",
            "doc_id": f"d{i}",
            "chunk_id": f"c{i}",
            "child_chunk_id": f"c{i}",
            "parent_chunk_id": f"p{i}",
            "text": "证据" * (body_chars // 2),
            "parent_text": "证据" * (body_chars // 2),
            "title": f"T{i}",
            "section": f"S{i}",
            "page_start": 1,
            "page_end": 1,
            "score": 1.0,
            "rerank_score": 6 - i,
            "metadata": {},
        }
        for i in range(1, count + 1)
    ]


def _packer(
    strategy: str = "lost_in_middle_aware", *, metadata: bool = False, max_items: int = 5
) -> ContextPacker:
    return ContextPacker(
        max_context_chars=12000,
        max_items=max_items,
        include_metadata=metadata,
        packing_strategy=strategy,
    )


def _ids(pack) -> list[str]:
    return [item["chunk_id"] for item in pack.selected_results]


def _drop_reason(item: dict[str, Any]) -> str:
    return item["metadata"]["context_drop_reason"]


def _sources(item: dict[str, Any]):
    # Citation derivation is unchanged and belongs to a separate module.
    return citation_sources(
        metadata=item.get("metadata") or {},
        fallback_child_id=(
            item.get("child_chunk_id")
            or item.get("matched_chunk_id")
            or item.get("chunk_id")
        ),
        fallback_quote_text=(
            item.get("match_text") or item.get("child_text") or item.get("text")
        ),
        fallback_title=item.get("title"),
        fallback_section=item.get("section"),
        fallback_page_start=item.get("page_start"),
        fallback_page_end=item.get("page_end"),
    )


def _str(value: Any) -> str:
    return "" if value is None else str(value)


def _tag(strategy: str, position: int, total: int) -> str:
    if strategy == "default":
        return ""
    if total == 1:
        return "【高优先级证据】"
    if position == 1:
        return "【高优先级证据-开头】"
    if position == total:
        return "【高优先级证据-结尾】"
    return "【补充证据】"


@dataclass(frozen=True)
class _Oracle:
    context: str
    chars: int
    tokens: int
    citation_bindings: tuple[tuple[str, str, int], ...]
    item_citation_ids: tuple[tuple[str, ...], ...]


def _oracle(
    packer: ContextPacker,
    priority_members: list[dict[str, Any]],
    *,
    texts: dict[str, str] | None = None,
    total_override: int | None = None,
) -> _Oracle:
    """Exact target-format cost, without an admission loop or packer renderer."""
    if packer.packing_strategy == "lost_in_middle_aware":
        display = priority_members[::2] + priority_members[1::2][::-1]
    else:
        display = list(priority_members)
    total = len(display) if total_override is None else total_override
    aliases: dict[str, str] = {}
    bindings: list[tuple[str, str, int]] = []
    item_ids: list[tuple[str, ...]] = []
    parts: list[str] = []
    for position, row in enumerate(display, 1):
        ids: list[str] = []
        for source in _sources(row):
            if source.child_id not in aliases:
                aliases[source.child_id] = f"C{len(aliases) + 1}"
                bindings.append((aliases[source.child_id], source.child_id, position))
            cid = aliases[source.child_id]
            if cid not in ids:
                ids.append(cid)
        marker = " ".join(f"[{cid}]" for cid in ids) or f"[C{position}]"
        tag = _tag(packer.packing_strategy, position, total)
        if texts is not None and row["chunk_id"] in texts:
            text = texts[row["chunk_id"]]
        elif "prompt_visible_text" in row:
            text = row["prompt_visible_text"]
        else:
            # Projection/horizon policy is unchanged by this implementation.
            text = packer._prompt_projection(row)[0].strip()
        if packer.include_metadata:
            page_start, page_end = row.get("page_start"), row.get("page_end")
            if page_start is not None and page_end is not None:
                page = f"{page_start}~{page_end}"
            elif page_start is not None:
                page = str(page_start)
            else:
                page = ""
            try:
                score = float(row.get("score") or 0.0)
            except (TypeError, ValueError):
                score = 0.0
            tag_line = f"{tag}\n" if tag else ""
            header = (
                f"{marker}\n{tag_line}"
                f"doc_id: {_str(row.get('doc_id'))}\n"
                f"parent_chunk_id: {_str(row.get('parent_chunk_id'))}\n"
                f"child_chunk_id: {_str(row.get('child_chunk_id'))}\n"
                f"title: {_str(row.get('title'))}\n"
                f"section: {_str(row.get('section'))}\n"
                f"page: {page}\nscore: {score}\n"
                f"rerank_score: {row.get('rerank_score')}\n"
                f"retrieval_sources: {(row.get('metadata') or {}).get('retrieval_sources', [])}\n"
                "text:\n"
            )
        else:
            header = f"{marker}" + (f"\n{tag}" if tag else "") + "\n"
        parts.append(f"{header}{text}".strip())
        item_ids.append(tuple(ids))
    context = "\n\n".join(parts)
    return _Oracle(
        context,
        len(context),
        Estimator.estimate(context),
        tuple(bindings),
        tuple(item_ids),
    )


def _assert_exact(pack, oracle: _Oracle) -> None:
    assert pack.context == oracle.context
    assert pack.used_chars == oracle.chars == len(pack.context)
    assert pack.tokens_used == oracle.tokens == Estimator.estimate(pack.context)
    assert pack.used_chars <= pack.max_context_chars
    assert pack.tokens_used <= pack.token_budget
    assert tuple(
        (row["citation_id"], row["child_chunk_id"], row["context_rank"])
        for row in pack.citations
    ) == oracle.citation_bindings
    assert tuple(
        tuple(row["metadata"]["prompt_citation_ids"])
        for row in pack.selected_results
    ) == oracle.item_citation_ids


@dataclass(frozen=True)
class _AdmissionEnvelope:
    chars: int
    tokens: int


def _admission_envelope(
    packer: ContextPacker,
    priority_members: list[dict[str, Any]],
    *,
    texts: dict[str, str] | None = None,
) -> _AdmissionEnvelope:
    """Independent reserve for both complete public presentation formats."""
    costs = []
    for strategy in STRATEGIES:
        renderer = copy(packer)
        renderer.packing_strategy = strategy
        costs.append(_oracle(renderer, priority_members, texts=texts))
    return _AdmissionEnvelope(
        chars=max(cost.chars for cost in costs),
        tokens=max(cost.tokens for cost in costs),
    )


def _maximum_prefix(
    packer: ContextPacker, row: dict[str, Any], *, tokens: int, chars: int
) -> str | None:
    """Exhaustive independent oracle for the binary search's maximality."""
    text = packer._prompt_projection(row)[0].strip()
    best = None
    for size in range(len(text) + 1):
        candidate = text[:size].rstrip() + SUFFIX
        cost = _admission_envelope(packer, [row], texts={row["chunk_id"]: candidate})
        if cost.tokens <= tokens and cost.chars <= chars:
            best = candidate
    return best


def test_d01_wide_budget_preserves_lost_permutation() -> None:
    rows = _results()
    lost = _packer()
    pack = lost.pack(rows, token_budget=12000)
    assert _ids(pack) == ["c1", "c3", "c5", "c4", "c2"]
    assert {row["pre_context_rank"] for row in pack.items} == {1, 2, 3, 4, 5}
    assert [row["context_rank"] for row in pack.items] == [1, 2, 3, 4, 5]
    assert not pack.dropped_results
    _assert_exact(pack, _oracle(lost, rows))


@pytest.mark.parametrize("limiter", ["token", "char"])
def test_d02_tight_equal_size_admits_priority_top_two(limiter: str) -> None:
    rows, lost = _results(), _packer()
    pair = _oracle(lost, rows[:2])
    kwargs = {
        "token_budget": pair.tokens if limiter == "token" else 12000,
        "char_budget": pair.chars if limiter == "char" else 12000,
    }
    pack = lost.pack(rows, **kwargs)
    assert _ids(pack) == ["c1", "c2"]
    assert len(pack.dropped_results) == 3
    assert not pack.truncated_item_ids
    _assert_exact(pack, pair)


def test_d03_three_admitted_items_present_as_one_three_two() -> None:
    rows, lost = _results(count=3), _packer()
    pack = lost.pack(rows, token_budget=12000)
    assert _ids(pack) == ["c1", "c3", "c2"]
    assert [row["pre_context_rank"] for row in pack.items] == [1, 3, 2]
    _assert_exact(pack, _oracle(lost, rows))


def test_d04_large_rank_two_does_not_block_rank_three_filler() -> None:
    rows, lost = _results(count=3), _packer()
    rows[1]["text"] = "证据" * 2000
    pair = _oracle(lost, [rows[0], rows[2]])
    pack = lost.pack(rows, token_budget=pair.tokens, char_budget=12000)
    assert _ids(pack) == ["c1", "c3"]
    assert [row["chunk_id"] for row in pack.dropped_results] == ["c2"]
    assert _drop_reason(pack.dropped_results[0]) == "token_budget_overflow"
    assert not pack.truncated_item_ids
    _assert_exact(pack, pair)


@pytest.mark.parametrize("strategy", STRATEGIES)
def test_d05_metadata_headers_are_part_of_exact_cost(strategy: str) -> None:
    rows, packer = _results(), _packer(strategy, metadata=True)
    rows[1]["title"] = "精确来源标题" * 20
    rows[1]["metadata"]["retrieval_sources"] = ["dense", "bm25"]
    pair = _oracle(packer, rows[:2])
    envelope = _admission_envelope(packer, rows[:2])
    pack = packer.pack(rows, token_budget=12000, char_budget=envelope.chars)
    assert _ids(pack) == ["c1", "c2"]
    assert "retrieval_sources: ['dense', 'bm25']" in pack.context
    assert rows[1]["title"] in pack.context
    _assert_exact(pack, pair)


@pytest.mark.parametrize("strategy", STRATEGIES)
def test_d06_multiple_citations_are_costed_and_bound_to_selected_sources(strategy: str) -> None:
    rows, packer = _results(), _packer(strategy, metadata=True)
    for row in rows:
        row["metadata"]["matched_child_chunks"] = [
            {"child_chunk_id": f"{row['chunk_id']}s{i}", "text": f"引用证据{i}"}
            for i in range(1, 7)
        ]
    pair = _oracle(packer, rows[:2])
    envelope = _admission_envelope(packer, rows[:2])
    pack = packer.pack(rows, token_budget=envelope.tokens)
    assert _ids(pack) == ["c1", "c2"]
    assert len(pack.citations) == 14
    assert all(row["parent_chunk_id"] in {"p1", "p2"} for row in pack.citations)
    _assert_exact(pack, pair)


@pytest.mark.parametrize("strategy", STRATEGIES)
def test_d07_first_selected_truncation_is_maximal_and_auditable(strategy: str) -> None:
    rows, packer = _results(count=1, body_chars=1800), _packer(strategy, metadata=True)
    expected = _maximum_prefix(packer, rows[0], tokens=1000, chars=480)
    assert expected is not None
    assert _admission_envelope(packer, rows).chars > 480
    pack = packer.pack(rows, token_budget=1000, char_budget=480)
    assert _ids(pack) == ["c1"]
    chosen = pack.items[0]
    assert chosen["prompt_visible_text"] == expected == chosen["context_text"]
    assert chosen["metadata"]["context_truncated"] is True
    assert chosen["metadata"]["truncated_source_id"] == "p1"
    assert chosen["metadata"]["truncation_reason"] == "token_or_character_budget"
    assert chosen["metadata"]["prompt_visible_char_count"] == len(expected)
    assert chosen["metadata"]["prompt_visible_token_count"] == Estimator.estimate(expected)
    assert pack.truncated_item_ids == ["p1"]
    _assert_exact(pack, _oracle(packer, rows, texts={"c1": expected}))


def test_d08_later_trial_recomputes_position_cost_without_shortening_prefix() -> None:
    rows, lost = _results(count=2, body_chars=1800), _packer()
    rows[1]["text"] = "小"
    single = lost.pack(rows[:1], token_budget=1000, char_budget=400)
    frozen = single.items[0]["prompt_visible_text"]
    pair = _oracle(lost, rows, texts={"c1": frozen})
    assert pair.chars > 400
    assert "【高优先级证据-开头】" in pair.context
    assert "【高优先级证据-结尾】" in pair.context
    combined = lost.pack(rows, token_budget=1000, char_budget=400)
    assert _ids(combined) == ["c1"]
    assert combined.items[0]["prompt_visible_text"] == frozen
    assert combined.items[0]["metadata"] == single.items[0]["metadata"]
    assert [row["chunk_id"] for row in combined.dropped_results] == ["c2"]
    _assert_exact(combined, _oracle(lost, rows[:1], texts={"c1": frozen}))


def test_d09_parent_dedup_preserves_first_priority_source() -> None:
    rows, lost = _results(count=3), _packer()
    rows[1]["parent_chunk_id"] = rows[0]["parent_chunk_id"]
    pack = lost.pack(rows, token_budget=12000)
    assert _ids(pack) == ["c1", "c3"]
    assert [(row["chunk_id"], _drop_reason(row)) for row in pack.dropped_results] == [
        ("c2", "duplicate_parent")
    ]
    assert [row["pre_context_rank"] for row in pack.items] == [1, 3]


def test_d10_call_max_items_override_defines_candidate_horizon() -> None:
    rows, lost = _results(count=6), _packer(max_items=5)
    pack = lost.pack(rows, token_budget=12000, max_items=3)
    assert _ids(pack) == ["c1", "c3", "c2"]
    assert [(row["chunk_id"], _drop_reason(row)) for row in pack.dropped_results] == [
        ("c4", "max_items"), ("c5", "max_items"), ("c6", "max_items")
    ]
    _assert_exact(pack, _oracle(lost, rows[:3]))


def test_d11_nested_input_is_immutable_across_trials_and_final_render() -> None:
    rows, lost = _results(), _packer(metadata=True)
    rows[0]["metadata"]["matched_child_chunks"] = [
        {"child_chunk_id": "shared", "text": "引用正文", "title": "来源标题"}
    ]
    original = deepcopy(rows)
    budget = _oracle(lost, rows[:2]).tokens
    pack = lost.pack(iter(rows), token_budget=budget)
    assert _ids(pack) == ["c1", "c2"]
    assert rows == original
    assert all("_pack_text" not in row for row in pack.items + pack.dropped_results)
    assert all("_prompt_projection_metadata" not in row for row in pack.items + pack.dropped_results)


@pytest.mark.parametrize("case", ["wide", "token", "char", "metadata", "citation", "truncation"])
def test_d12_default_strategy_keeps_all_legal_budget_modes(case: str) -> None:
    packer = _packer("default", metadata=case in {"metadata", "citation", "truncation"})
    rows = _results()
    kwargs = {"token_budget": 12000, "char_budget": 12000}
    if case == "citation":
        rows[0]["metadata"]["matched_child_chunks"] = [
            {"child_chunk_id": "extra", "text": "另一引用来源"}
        ]
    if case == "truncation":
        rows[0]["text"] = "证据" * 1500
        kwargs.update(token_budget=1000, char_budget=480)
        expected = _maximum_prefix(packer, rows[0], tokens=1000, chars=480)
        assert expected is not None
        pack = packer.pack(rows, **kwargs)
        assert _ids(pack) == ["c1"]
        assert pack.items[0]["prompt_visible_text"] == expected
        _assert_exact(pack, _oracle(packer, rows[:1], texts={"c1": expected}))
    else:
        members = rows if case == "wide" else rows[:2]
        expected = _oracle(packer, members)
        envelope = _admission_envelope(packer, members)
        if case == "token":
            kwargs["token_budget"] = envelope.tokens
        elif case != "wide":
            kwargs["char_budget"] = envelope.chars
        pack = packer.pack(rows, **kwargs)
        assert _ids(pack) == [row["chunk_id"] for row in members]
        assert not pack.truncated_item_ids
        _assert_exact(pack, expected)
    assert "【高优先级证据" not in pack.context
    assert [row["rank"] for row in pack.items] == list(range(1, len(pack.items) + 1))


def test_d13_total_items_uses_actual_two_member_count_not_five_item_horizon() -> None:
    rows, lost = _results(), _packer()
    pair = _oracle(lost, rows[:2])
    pack = lost.pack(rows, token_budget=pair.tokens)
    assert _ids(pack) == ["c1", "c2"]
    assert pack.context.count("【高优先级证据-开头】") == 1
    assert pack.context.count("【高优先级证据-结尾】") == 1
    assert "【补充证据】" not in pack.context
    assert pack.context != _oracle(lost, rows[:2], total_override=5).context
    _assert_exact(pack, pair)


def test_d14_unchanged_static_yaml_and_context_gate_fix_original_witness() -> None:
    raw = yaml.safe_load((REPO / "backend/rag/config/static_retrieval_v1.yaml").read_text(encoding="utf-8"))
    configs = {item["name"]: item["params"] for item in raw["context_packers"]}
    default = DefaultContextPacker(**configs["default"])
    lost = LostInMiddleContextPacker(**configs["lost_in_middle"])
    gate_config = dict(raw["context_gate"])
    ratio = gate_config.pop("long_context_threshold_ratio")
    requirements = ContextRequirements(**gate_config)
    gate = ContextGate(
        default_packer=default,
        lost_in_middle_packer=lost,
        default_requirements=requirements,
        long_context_threshold_ratio=ratio,
    )
    rows = _results(body_chars=1500)
    assert sum(Estimator.estimate(row["text"]) for row in rows) > int(requirements.evidence_token_budget * ratio)
    pack = gate.pack(rows)
    assert pack.packing_strategy == "lost_in_middle_aware"
    assert _ids(pack) == ["c1", "c2"]
    assert not pack.truncated_item_ids
    _assert_exact(pack, _oracle(lost, rows[:2]))
    control = default.pack(
        rows,
        token_budget=requirements.evidence_token_budget,
        max_items=requirements.max_evidence_items,
        char_budget=requirements.max_context_chars,
    )
    assert _ids(control) == ["c1", "c2"]


@pytest.mark.parametrize("metadata,limiter,citations", [(False, "token", 1), (False, "char", 1), (True, "token", 1), (True, "char", 1), (True, "token", 6)])
def test_d15_original_independent_probe_inputs_replay_under_new_contract(metadata: bool, limiter: str, citations: int) -> None:
    # Execute only the historical input-construction function, without running
    # its old assertions, audit hook, main routine, imports or any filesystem I/O.
    source_bytes = HISTORICAL_PROBE.read_bytes()
    module = ast.parse(source_bytes.decode("utf-8"))
    function = next(node for node in module.body if isinstance(node, ast.FunctionDef) and node.name == "results")
    isolated = ast.Module(body=[function], type_ignores=[])
    namespace: dict[str, Any] = {}
    exec(compile(isolated, str(HISTORICAL_PROBE), "exec"), namespace)
    rows = namespace["results"](citations_per_item=citations)
    lost = _packer(metadata=metadata)
    pair_final = _oracle(lost, rows[:2])
    pair_horizon = _oracle(lost, rows[:2], total_override=5)
    kwargs = {
        "token_budget": max(pair_final.tokens, pair_horizon.tokens) if limiter == "token" else 12000,
        "char_budget": max(pair_final.chars, pair_horizon.chars) if limiter == "char" else 12000,
    }
    triple = _oracle(lost, rows[:3])
    assert triple.tokens > kwargs["token_budget"] or triple.chars > kwargs["char_budget"]
    assert "['c1', 'c3']" in source_bytes.decode("utf-8")
    before = deepcopy(rows)
    pack = lost.pack(rows, **kwargs)
    assert _ids(pack) == ["c1", "c2"]
    assert not pack.truncated_item_ids
    _assert_exact(pack, pair_final)
    assert rows == before
    assert HISTORICAL_PROBE.read_bytes() == source_bytes


def test_d16_out_of_scope_historical_method_projection_loss_remains_visible() -> None:
    row = _results(count=1)[0]
    method = "采用分层架构并进行交叉验证的历史建设方法。"
    row["text"] = method + "其他项目新增8台设备，容量324.2TB。"
    row["parent_text"] = row["text"]
    row["title"] = "历史项目章节123"
    row["metadata"]["evidence_assessment_v2"] = {
        "evidence_role": "historical_case",
        "project_relation": "other_project",
        "supported_claim_types": ["general_method", "reference_case"],
        "forbidden_claim_types": ["current_project_quantity"],
    }
    pack = _packer().pack([row], token_budget=12000)
    visible = pack.items[0]["prompt_visible_text"]
    assert method not in visible  # RWP-PROD-001 is deliberately still unresolved.
    assert "8台" not in visible and "324.2TB" not in visible
    assert "历史项目章节123" in visible  # RWP-REP-004 is unchanged.
    assert pack.items[0]["text"] == row["text"]
    assert pack.items[0]["metadata"]["prompt_projection_mode"] == "historical_reference_restricted"


@pytest.mark.parametrize("strategy", STRATEGIES)
def test_d17_exact_token_boundary_admits_equality_and_rejects_one_less(strategy: str) -> None:
    rows, packer = _results(count=2), _packer(strategy, metadata=True)
    pair = _oracle(packer, rows)
    envelope = _admission_envelope(packer, rows)
    equal = packer.pack(rows, token_budget=envelope.tokens)
    assert _ids(equal) == ["c1", "c2"]
    assert envelope.tokens == equal.token_budget
    assert equal.tokens_used <= equal.token_budget
    _assert_exact(equal, pair)
    less = packer.pack(rows, token_budget=envelope.tokens - 1)
    assert _ids(less) == ["c1"]
    assert not less.truncated_item_ids
    assert [row["chunk_id"] for row in less.dropped_results] == ["c2"]
    _assert_exact(less, _oracle(packer, rows[:1]))


@pytest.mark.parametrize("strategy", STRATEGIES)
def test_d18_exact_char_boundary_admits_equality_and_rejects_one_less(strategy: str) -> None:
    rows, packer = _results(count=2), _packer(strategy, metadata=True)
    pair = _oracle(packer, rows)
    envelope = _admission_envelope(packer, rows)
    equal = packer.pack(rows, token_budget=12000, char_budget=envelope.chars)
    assert _ids(equal) == ["c1", "c2"]
    assert envelope.chars == equal.max_context_chars
    assert equal.used_chars <= equal.max_context_chars
    _assert_exact(equal, pair)
    less = packer.pack(rows, token_budget=12000, char_budget=envelope.chars - 1)
    assert _ids(less) == ["c1"]
    assert not less.truncated_item_ids
    _assert_exact(less, _oracle(packer, rows[:1]))


def test_d19_guard_true_but_no_legal_zero_prefix_drops_and_continues() -> None:
    rows, lost = _results(count=2, body_chars=1800), _packer(metadata=True)
    rows[0]["title"] = "来源" * 500
    rows[1]["text"] = "小证据"
    assert 400 > 300 and 1000 > 64  # Existing guard permits an attempt.
    zero = _oracle(lost, rows[:1], texts={"c1": SUFFIX})
    assert zero.chars > 400
    assert _maximum_prefix(lost, rows[0], tokens=1000, chars=400) is None
    pack = lost.pack(rows, token_budget=1000, char_budget=400)
    assert _ids(pack) == ["c2"]
    assert [_drop_reason(row) for row in pack.dropped_results] == ["token_budget_overflow"]
    assert not pack.truncated_item_ids
    assert [row["child_chunk_id"] for row in pack.citations] == ["c2"]
    _assert_exact(pack, _oracle(lost, rows[1:]))


def test_d20_first_selected_owner_can_be_rank_two_but_never_later_admitted_candidate() -> None:
    rows, lost = _results(count=2, body_chars=1800), _packer(metadata=True)
    rows[0]["title"] = "来源" * 500
    expected = _maximum_prefix(lost, rows[1], tokens=1000, chars=480)
    assert expected is not None
    pack = lost.pack(rows, token_budget=1000, char_budget=480)
    assert _ids(pack) == ["c2"]
    assert pack.items[0]["pre_context_rank"] == 2
    assert pack.items[0]["metadata"]["context_truncated"] is True
    assert pack.items[0]["prompt_visible_text"] == expected
    assert pack.truncated_item_ids == ["p2"]
    _assert_exact(pack, _oracle(lost, rows[1:], texts={"c2": expected}))
    reverse = _results(count=2, body_chars=1800)
    reverse[0]["text"] = "小证据"
    no_later_truncation = lost.pack(reverse, token_budget=1000, char_budget=480)
    assert _ids(no_later_truncation) == ["c1"]
    assert no_later_truncation.items[0]["metadata"]["context_truncated"] is False
    assert no_later_truncation.truncated_item_ids == []
    assert [row["chunk_id"] for row in no_later_truncation.dropped_results] == ["c2"]


def test_d21_zero_one_two_selected_have_exact_position_semantics() -> None:
    rows, lost = _results(count=2), _packer()
    empty = lost.pack(rows, token_budget=1, char_budget=1)
    assert empty.items == empty.citations == []
    assert empty.context == "" and empty.used_chars == empty.tokens_used == 0
    assert len(empty.dropped_results) == 2
    one = lost.pack(rows[:1], token_budget=12000)
    assert one.context.count("【高优先级证据】") == 1
    assert "-开头" not in one.context and "-结尾" not in one.context
    two = lost.pack(rows, token_budget=12000)
    assert two.context.count("【高优先级证据-开头】") == 1
    assert two.context.count("【高优先级证据-结尾】") == 1
    _assert_exact(empty, _oracle(lost, []))
    _assert_exact(one, _oracle(lost, rows[:1]))
    _assert_exact(two, _oracle(lost, rows))


def test_d22_shared_sources_and_c9_c10_c11_cost_without_trial_alias_leak() -> None:
    rows, lost = _results(count=3), _packer(metadata=True)
    # Eight expanded child sources plus c1 give C1..C9 in the first item.
    rows[0]["metadata"]["matched_child_chunks"] = [
        {"child_chunk_id": f"s{i}", "text": f"证据来源{i}"} for i in range(1, 9)
    ]
    # Reuse s1; two new sources cross C9 -> C10 -> C11.  c2 is already
    # among those sources, so fallback does not accidentally add C12.
    rows[1]["metadata"]["matched_child_chunks"] = [
        {"child_chunk_id": "s1", "text": "共享子块"},
        {"child_chunk_id": "s10", "text": "第十引用"},
        {"child_chunk_id": "c2", "text": "第十一引用"},
    ]
    rows[2]["text"] = "证据" * 1000
    pair = _oracle(lost, rows[:2])
    assert pair.item_citation_ids == (tuple(f"C{i}" for i in range(1, 10)), ("C1", "C10", "C11"))
    # Measure actual marker digits, not a one-character alias approximation.
    assert len("[C10]") == len("[C9]") + 1
    assert len("[C11]") == len("[C9]") + 1
    pack = lost.pack(rows, token_budget=pair.tokens, char_budget=pair.chars)
    assert _ids(pack) == ["c1", "c2"]
    assert [row["citation_id"] for row in pack.citations] == [f"C{i}" for i in range(1, 12)]
    assert len([row for row in pack.citations if row["child_chunk_id"] == "s1"]) == 1
    assert not any(row["child_chunk_id"] == "c3" for row in pack.citations)
    assert "[C12]" not in pack.context
    _assert_exact(pack, pair)
    assert lost.pack(rows, token_budget=pair.tokens, char_budget=pair.chars).to_dict() == pack.to_dict()
    too_small = lost.pack(rows[:2], token_budget=12000, char_budget=pair.chars - 1)
    assert _ids(too_small) == ["c1"]
    assert [row["citation_id"] for row in too_small.citations] == [f"C{i}" for i in range(1, 10)]
    assert "[C10]" not in too_small.context and "[C11]" not in too_small.context
    _assert_exact(too_small, _oracle(lost, rows[:1]))


def test_d23_duplicate_and_projected_empty_do_not_change_priority_facts() -> None:
    rows, lost = _results(count=5), _packer()
    rows[1]["parent_chunk_id"] = "p1"
    # A truthy configured text is stripped to empty despite retained raw parent.
    rows[2]["text"] = "   "
    candidates, pre_dropped = lost._collect_candidates(rows)
    assert [row["chunk_id"] for row in candidates] == ["c1", "c4", "c5"]
    assert [(row["chunk_id"], _drop_reason(row)) for row in pre_dropped] == [
        ("c2", "duplicate_parent"), ("c3", "empty_text")
    ]
    pair = _oracle(lost, [rows[0], rows[3]])
    pack = lost.pack(rows, token_budget=pair.tokens)
    assert _ids(pack) == ["c1", "c4"]
    assert [row["pre_context_rank"] for row in pack.items] == [1, 4]
    assert {_drop_reason(row) for row in pack.dropped_results} == {
        "duplicate_parent", "empty_text", "token_budget_exhausted"
    }
    _assert_exact(pack, pair)


def test_d24_maximal_frozen_prefix_hash_survives_all_lower_rank_trials() -> None:
    rows, lost = _results(count=3, body_chars=1800), _packer(metadata=True)
    rows[1]["text"], rows[2]["text"] = "后续候选" * 100, "小"
    expected = _maximum_prefix(lost, rows[0], tokens=1000, chars=480)
    assert expected is not None
    before = lost.pack(rows[:1], token_budget=1000, char_budget=480)
    original_hash = hashlib.sha256(expected.encode("utf-8")).hexdigest()
    for later in rows[1:]:
        trial = _oracle(lost, [rows[0], later], texts={"c1": expected})
        assert trial.chars > 480 or trial.tokens > 1000
    after = lost.pack(rows, token_budget=1000, char_budget=480)
    assert _ids(after) == ["c1"]
    assert {row["chunk_id"] for row in after.dropped_results} == {"c2", "c3"}
    frozen = after.items[0]["prompt_visible_text"]
    assert frozen == expected == before.items[0]["prompt_visible_text"]
    assert hashlib.sha256(frozen.encode("utf-8")).hexdigest() == original_hash
    assert after.items[0]["metadata"] == before.items[0]["metadata"]
    assert after.citations == before.citations
    _assert_exact(after, _oracle(lost, rows[:1], texts={"c1": expected}))


def test_d25_rank_six_cannot_fill_after_horizon_budget_failures() -> None:
    rows, lost = _results(count=6), _packer(max_items=5)
    for row in rows[1:5]:
        row["text"] = "证据" * 2000
    rows[5]["text"] = "小"
    would_fit = _oracle(lost, [rows[0], rows[5]])
    pack = lost.pack(rows, token_budget=would_fit.tokens, char_budget=12000)
    assert _ids(pack) == ["c1"]
    assert [(row["chunk_id"], _drop_reason(row)) for row in pack.dropped_results if row["chunk_id"] == "c6"] == [("c6", "max_items")]
    assert {row["chunk_id"] for row in pack.dropped_results if _drop_reason(row).startswith("token_budget_")} == {"c2", "c3", "c4", "c5"}
    assert not any(row["child_chunk_id"] == "c6" for row in pack.citations)


def test_d26_equal_scores_keep_upstream_stable_input_priority() -> None:
    rows, lost = _results(), _packer()
    for row in rows:
        row["score"] = row["rerank_score"] = 0.5
    # Deliberately disagree with numeric rank/chunk ID order.
    rows = [rows[3], rows[1], rows[4], rows[0], rows[2]]
    pair = _oracle(lost, rows[:2])
    pack = lost.pack(rows, token_budget=pair.tokens)
    assert _ids(pack) == ["c4", "c2"]
    assert [row["pre_context_rank"] for row in pack.items] == [4, 2]
    _assert_exact(pack, pair)
    wide = lost.pack(rows, token_budget=12000)
    assert _ids(wide) == ["c4", "c5", "c3", "c1", "c2"]


def test_d27_repeated_pack_and_final_render_are_deterministic() -> None:
    class RecordingPacker(ContextPacker):
        def __init__(self):
            super().__init__(max_context_chars=12000, max_items=5, include_metadata=True)
            self.render_calls: list[tuple[str, int, int, str, tuple[str, ...], str]] = []

        def _format_item(self, result, context_rank, text, total_items, citation_ids=None):
            rendered = super()._format_item(result, context_rank, text, total_items, citation_ids)
            self.render_calls.append((result["chunk_id"], context_rank, total_items, text, tuple(citation_ids or []), rendered))
            return rendered

    rows, lost = _results(count=3), RecordingPacker()
    expected = _oracle(lost, rows)
    outputs = [lost.pack(deepcopy(rows), token_budget=expected.tokens).to_dict() for _ in range(3)]
    assert outputs[0] == outputs[1] == outputs[2]
    assert len({hashlib.sha256(value["context"].encode("utf-8")).hexdigest() for value in outputs}) == 1
    final_calls = lost.render_calls[-3:]
    assert [call[0] for call in final_calls] == ["c1", "c3", "c2"]
    assert [call[1] for call in final_calls] == [1, 2, 3]
    assert all(call[2] == 3 for call in final_calls)
    assert "\n\n".join(call[-1] for call in final_calls) == expected.context
    # The last successful admission trial and final rendering have identical
    # item inputs/results; no final budget eviction or stale total is possible.
    assert lost.render_calls[-6:-3] == final_calls
    _assert_exact(lost.pack(rows, token_budget=expected.tokens), expected)


def test_d28_two_level_partitions_are_complete_exclusive_and_identity_preserving() -> None:
    rows, lost = _results(count=6), _packer(max_items=3)
    # Two input records deliberately share chunk/source identity; their distinct
    # input_identity values ensure an accidental set collapse cannot pass.
    rows[1]["chunk_id"] = rows[0]["chunk_id"]
    rows[1]["child_chunk_id"] = rows[0]["child_chunk_id"]
    rows[1]["parent_chunk_id"] = rows[0]["parent_chunk_id"]
    rows[2]["text"] = "   "
    rows[3]["text"] = "证据" * 2000
    original = deepcopy(rows)
    horizon, pre_dropped = lost._collect_candidates(rows)
    pair = _oracle(lost, [rows[0], rows[4]])
    pack = lost.pack(rows, token_budget=pair.tokens)
    pre_reasons = {"duplicate_parent", "empty_text", "max_items"}
    final_pre = [row for row in pack.dropped_results if _drop_reason(row) in pre_reasons]
    budget_dropped = [row for row in pack.dropped_results if _drop_reason(row) not in pre_reasons]

    def identities(values):
        return Counter(row["input_identity"] for row in values)

    def assert_partition(universe, left, right):
        u, a, b = identities(universe), identities(left), identities(right)
        assert not (set(a) & set(b))
        assert a + b == u
        assert all(count == 1 for count in (a + b).values())

    assert identities(final_pre) == identities(pre_dropped)
    assert_partition(rows, final_pre, horizon)
    assert_partition(horizon, pack.items, budget_dropped)
    assert not (set(identities(pack.items)) & set(identities(pack.dropped_results)))
    assert identities(pack.items + pack.dropped_results) == identities(rows)
    assert [row["input_identity"] for row in horizon] == ["entry-1", "entry-4", "entry-5"]
    assert [row["input_identity"] for row in pack.items] == ["entry-1", "entry-5"]
    assert [row["input_identity"] for row in budget_dropped] == ["entry-4"]
    assert {(row["input_identity"], _drop_reason(row)) for row in final_pre} == {
        ("entry-2", "duplicate_parent"), ("entry-3", "empty_text"), ("entry-6", "max_items")
    }
    by_identity = {row["input_identity"]: row for row in original}
    for row in pack.items + pack.dropped_results:
        source = by_identity[row["input_identity"]]
        for key in ("chunk_id", "child_chunk_id", "parent_chunk_id", "doc_id", "text"):
            assert row[key] == source[key]
        if row in pack.items:
            assert row["pre_context_rank"] == source["rank"]
    assert rows == original
    _assert_exact(pack, pair)
