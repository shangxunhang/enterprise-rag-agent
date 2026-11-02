"""SDC-EDGE-001/002: source-bound fidelity through persisted OBSERVE reports.

New, isolated synthetic producer faults retain valid frames and finite-pool
digests. Native fixtures use the existing Fake resources; packing fixtures use
only the production pure ContextPacker. No old probe or source evidence changes.
"""
from __future__ import annotations

from copy import deepcopy
from hashlib import sha256
import json

import pytest

from core.diagnostics import DiagnosticConfig, DiagnosticSession
from eval.full_chain.snapshot import diagnostic_capture_complete
from rag.context.context_packer import ContextPacker
from rag.reranker.parent_child_reranker import _get_candidate_text
from test_full_chain_eval_correction_iia_ir import _native_capture, _rewrite, _safe_report


def _pool(rows, operation, phase=None):
    begins = [row for row in rows if row.get("event_type") == "POOL_BEGIN"
        and row.get("operation_ref") == operation
        and (phase is None or row.get("phase", "RESULT") == phase)]
    assert len(begins) == 1, (operation, phase, begins)
    begin = begins[0]
    items = [row for row in rows if row.get("event_type") == "POOL_ITEM"
        and row.get("pool_id") == begin["pool_id"]]
    return begin, items


def _pool_digests(rows):
    """Seal new synthetic output faithfully, separate from file tampering."""
    for begin in [row for row in rows if row.get("event_type") == "POOL_BEGIN"]:
        items = [row for row in rows if row.get("event_type") == "POOL_ITEM"
            and row.get("pool_id") == begin["pool_id"]]
        for ordinal, row in enumerate(items):
            row["ordinal"] = ordinal
        begin["declared_count"] = len(items)
        end = next(row for row in rows if row.get("event_type") == "POOL_END"
            and row.get("pool_id") == begin["pool_id"])
        end["actual_count"] = end["declared_count"] = len(items)
        end["end_digest"] = sha256(b"".join(json.dumps(row["item"], ensure_ascii=False,
            sort_keys=True, separators=(",", ":")).encode() + b"\n" for row in items)).hexdigest()


def _assert_report(path, tmp_path, edge_id, expected):
    snapshot, report, machine = _safe_report(path, tmp_path)
    assert diagnostic_capture_complete(snapshot) and not snapshot.limitations
    edges = [edge for edge in report.edge_fidelity if edge.edge_id == edge_id]
    assert edges and all(edge.source_refs and edge.target_refs for edge in edges)
    assert all(edge.assertion_result is expected for edge in edges), [
        (edge.reason, edge.payload) for edge in edges]
    state = "UNMEASURED" if expected is None else "MEASURED"
    assert all(edge.measurement_state == state for edge in edges)
    persisted = [edge for edge in machine["edge_fidelity"] if edge["edge_id"] == edge_id]
    assert len(persisted) == len(edges)
    assert all(edge["assertion_result"] is expected and edge["measurement_state"] == state
        for edge in persisted)
    return edges


def _native_case(tmp_path, mutation, expected):
    original_path = _native_capture(tmp_path)
    original_bytes = original_path.read_bytes()
    rows = [json.loads(line) for line in original_bytes.splitlines()]
    # Explicit fixture version makes index-version mutation independent of the
    # native profile's source digest, without inventing any historical evidence.
    for row in rows:
        if row.get("event_type") == "POOL_ITEM":
            row["item"]["index_version"] = "edge-fixture-index-v1"
    mutation(rows)
    _pool_digests(rows)
    case_path = original_path.parent / "edge_content_case.jsonl"
    _rewrite(case_path, rows)
    _assert_report(case_path, tmp_path, "X05", expected)
    assert original_path.read_bytes() == original_bytes


@pytest.mark.parametrize("field", ["parent_text", "text", "child_text", "index_version", "doc_id"])
def test_x05_same_identity_does_not_authorize_source_replacement(field, tmp_path):
    def mutate(rows):
        _, items = _pool(rows, "initial_rerank_output")
        item = items[0]["item"]
        assert field in item
        item[field] = "synthetic unauthorized replacement"
    _native_case(tmp_path, mutate, False)


def test_x05_source_version_in_original_profile_is_immutable(tmp_path):
    def mutate(rows):
        _, items = _pool(rows, "initial_rerank_output")
        item = items[0]["item"]
        profiles = [item.get("document_profile"),
            (item.get("metadata") or {}).get("document_profile"),
            (item.get("child_chunk") or {}).get("document_profile")]
        profile = next(profile for profile in profiles
            if isinstance(profile, dict) and "source_sha256" in profile)
        profile["source_sha256"] = "f" * 64
    _native_case(tmp_path, mutate, False)


def test_x05_same_parent_cannot_bind_to_another_producer_child(tmp_path):
    def mutate(rows):
        _, items = _pool(rows, "initial_rerank_output")
        assert len(items) >= 2
        items[0]["item"]["child_chunk_id"] = items[1]["item"]["child_chunk_id"]
    _native_case(tmp_path, mutate, False)


def _request(rows):
    requests = [row for row in rows if (row.get("evidence_key") or row.get("event_type")) == "R-RERANK"
        and row.get("payload", {}).get("phase") == "REQUEST"]
    assert len(requests) == 1
    return requests[0]


def test_x05_recorded_request_must_bind_the_original_producer_query(tmp_path):
    def mutate(rows):
        _request(rows)["payload"]["query"] = "synthetic unrelated producer query"
    _native_case(tmp_path, mutate, False)


@pytest.mark.parametrize("text_field", ["parent_text", "text", "child_text", "configured_but_absent"])
def test_x05_recorded_text_field_and_production_fallback_are_supported(text_field, tmp_path):
    def mutate(rows):
        _, inputs = _pool(rows, "initial_rerank_input", "INPUT")
        request = _request(rows)["payload"]
        request["text_field"] = text_field
        request["actual_texts"] = [_get_candidate_text(row["item"], text_field=text_field) for row in inputs]
        assert all(request["actual_texts"])
    _native_case(tmp_path, mutate, True)


@pytest.mark.parametrize("case", ["normal", "changed_selected_custom_body"])
def test_x05_active_custom_text_field_preserves_its_original_source(case, tmp_path):
    def mutate(rows):
        for row in rows:
            if row.get("event_type") == "POOL_ITEM":
                item = row["item"]
                item["custom_body"] = "original custom evidence for " + str(item.get("parent_chunk_id") or item.get("chunk_id"))
        _, inputs = _pool(rows, "initial_rerank_input", "INPUT")
        request = _request(rows)["payload"]
        request["text_field"] = "custom_body"
        request["actual_texts"] = [_get_candidate_text(row["item"], text_field="custom_body") for row in inputs]
        if case == "changed_selected_custom_body":
            _, outputs = _pool(rows, "initial_rerank_output")
            outputs[0]["item"]["custom_body"] = "synthetic unauthorized custom evidence replacement"
    _native_case(tmp_path, mutate, True if case == "normal" else False)


@pytest.mark.parametrize("proof", ["actual_texts", "text_field", "query", "typed_actual_pairs"])
def test_x05_missing_recorded_request_proof_is_unknown(proof, tmp_path):
    def mutate(rows):
        request = _request(rows)
        payload = request["payload"]
        if proof != "typed_actual_pairs":
            payload.pop(proof)
            return
        request["operation_ref"] = "parent_cross_encoder_request"
        payload.update(model="synthetic-captured-cross-encoder", implementation="ParentChildReranker")
        payload.pop("actual_pairs", None)
        for row in rows:
            if row.get("operation_ref") in {"noop_before_top_k", "noop_after_top_k"}:
                row["operation_ref"] = row["operation_ref"].replace("noop_", "cross_encoder_", 1)
                row["model"] = payload["model"]
    _native_case(tmp_path, mutate, None)


@pytest.mark.parametrize("case", ["normal", "wrong_query", "wrong_text"])
def test_x05_synthetic_cross_encoder_request_pairs_are_authoritative(case, tmp_path):
    def mutate(rows):
        _, inputs = _pool(rows, "initial_rerank_input", "INPUT")
        request = _request(rows)
        # This is a typed diagnostic fixture, never a cross-encoder execution.
        request["operation_ref"] = "parent_cross_encoder_request"
        payload = request["payload"]
        payload.update(model="synthetic-captured-cross-encoder", implementation="ParentChildReranker")
        texts = [_get_candidate_text(row["item"], text_field=payload["text_field"]) for row in inputs]
        payload["actual_pairs"] = [[payload["query"], text] for text in texts]
        # A correct untyped alternate field cannot rescue incorrect typed pairs.
        payload["actual_texts"] = texts
        if case == "wrong_query":
            payload["actual_pairs"][0][0] = "different producer query"
        elif case == "wrong_text":
            payload["actual_pairs"][0][1] = "different producer content"
        for row in rows:
            if row.get("operation_ref") in {"noop_before_top_k", "noop_after_top_k"}:
                row["operation_ref"] = row["operation_ref"].replace("noop_", "cross_encoder_", 1)
                row["model"] = payload["model"]
    _native_case(tmp_path, mutate, True if case == "normal" else False)


def test_x05_request_kind_must_bind_to_the_selected_producer_operation(tmp_path):
    def mutate(rows):
        _, inputs = _pool(rows, "initial_rerank_input", "INPUT")
        request = _request(rows)
        request["operation_ref"] = "parent_cross_encoder_request"
        payload = request["payload"]
        payload["actual_pairs"] = [[payload["query"], _get_candidate_text(row["item"],
            text_field=payload["text_field"])] for row in inputs]
        # Keep noop_after_top_k: the recorded cross-encoder request belongs to
        # another producer kind, despite matching candidate IDs and text.
    _native_case(tmp_path, mutate, False)


@pytest.mark.parametrize("transform", ["scores_and_order", "top_k_filter"])
def test_x05_legal_ranking_and_selection_preserve_source_content(transform, tmp_path):
    def mutate(rows):
        for operation in ("noop_after_top_k", "initial_rerank_output"):
            begin, items = _pool(rows, operation)
            if transform == "top_k_filter":
                removed = {id(row) for row in items[1:]}
                rows[:] = [row for row in rows if id(row) not in removed]
                begin["top_k"] = 1
            else:
                original = [deepcopy(row["item"]) for row in items]
                for rank, (row, item) in enumerate(zip(items, reversed(original)), 1):
                    item.update(rank=rank, score=50.0 - rank, rerank_score=100.0 - rank)
                    item["metadata"]["pre_rerank_rank"] = 100 + rank
                    item["metadata"]["retrieval_stage"] = "synthetic_score_only_transform"
                    row["item"] = item
    _native_case(tmp_path, mutate, True)


def test_x05_native_parent_enrichment_is_not_reranker_content_corruption(tmp_path):
    def inspect(rows):
        _, items = _pool(rows, "initial_rerank_input", "INPUT")
        assert any(item["item"].get("parent_text") != item["item"].get("child_text")
            for item in items), "the control must contain a real enriched parent representation"
    _native_case(tmp_path, inspect, True)


def _remove_nested_fields(value, names):
    if isinstance(value, dict):
        for name in names:
            value.pop(name, None)
        for child in value.values():
            _remove_nested_fields(child, names)
    elif isinstance(value, list):
        for child in value:
            _remove_nested_fields(child, names)


@pytest.mark.parametrize("proof", ["source_text", "source_version", "tenant_identity", "duplicate_input", "duplicate_output"])
def test_x05_missing_or_ambiguous_source_proof_is_unknown(proof, tmp_path):
    def mutate(rows):
        _, inputs = _pool(rows, "initial_rerank_input", "INPUT")
        _, outputs = _pool(rows, "initial_rerank_output")
        if proof == "duplicate_input":
            inputs[1]["item"] = deepcopy(inputs[0]["item"])
        elif proof == "duplicate_output":
            outputs[1]["item"] = deepcopy(outputs[0]["item"])
        else:
            names = {"source_text": {"text", "parent_text", "child_text", "match_text", "context_text", "prompt_visible_text"},
                "source_version": {"index_version", "source_sha256", "source_version", "document_version"},
                "tenant_identity": {"tenant_id"}}[proof]
            for row in inputs + outputs:
                _remove_nested_fields(row["item"], names)
    _native_case(tmp_path, mutate, None)


def _candidate(identifier, *, mode="legacy_unprojected", text="source evidence for the permitted method"):
    item = {"child_chunk_id": "child-" + identifier, "chunk_id": "child-" + identifier,
        "parent_chunk_id": "parent-" + identifier, "doc_id": "doc-" + identifier,
        "tenant_id": "edge-tenant", "index_version": "edge-index-v1", "rank": 1,
        "score": 0.8, "rerank_score": 0.9, "title": "Shared source chapter", "section": "Method",
        "text": text, "parent_text": text, "child_text": text, "metadata": {}}
    if mode != "legacy_unprojected":
        assessment = {"evidence_role": "current_project_source", "project_relation": "current_project",
            "supported_claim_types": ["general_method"], "forbidden_claim_types": []}
        if mode == "historical_reference_restricted":
            assessment.update(evidence_role="historical_case", project_relation="other_project",
                forbidden_claim_types=["current_project_quantity"])
        elif mode == "template_structure_only":
            assessment.update(evidence_role="template_style", metadata={"structured_evidence": {
                "template": {"section_title": "Implementation", "required_fields": ["method", "schedule"],
                    "style_hints": ["state the evidence first"]}}})
        item["metadata"]["evidence_assessment_v2"] = assessment
    return item


def _production_pack(source, *, strategy="default", truncated=False, text_field="text"):
    packer = ContextPacker(max_context_chars=800 if truncated else 12000, max_items=5,
        include_metadata=False, packing_strategy=strategy, text_field=text_field)
    pack = packer.pack(source, token_budget=180 if truncated else 5000).to_dict()
    assert pack["selected_results"]
    if truncated:
        assert pack["truncated_item_ids"] and pack["selected_results"][0]["metadata"]["context_truncated"]
    return pack


def _packing_report(tmp_path, source, pack, expected):
    original = deepcopy(source)
    source_root = tmp_path / "source"
    source_root.mkdir()
    session = DiagnosticSession(DiagnosticConfig(source_root / "capture", source_root,
        "edge-packing", flush_records=1, max_queue_events=1024))
    with session:
        session.emit({"event_type": "R-SCOPE", "evidence_key": "R-SCOPE",
            "payload": {"query": "source-bound projection"}})
        session.emit_pool({"evidence_key": "R-GOV", "operation_ref": "governance_output",
            "phase": "RESULT", "query": "source-bound projection", "declared_count": len(source)}, source)
        session.emit({"event_type": "R-PACK", "evidence_key": "R-PACK", "operation_ref": "context_pack",
            "payload": {"phase": "RESULT", "context_pack": pack}})
    assert session.health()["completeness"] == "COMPLETE" and session.lost_count == 0
    _assert_report(session.path, tmp_path, "X07", expected)
    assert source == original


@pytest.mark.parametrize("mode", ["historical_reference_restricted", "template_structure_only",
    "governed_full_text", "legacy_unprojected"])
def test_x07_production_legal_projection_is_source_bound(mode, tmp_path):
    source = [_candidate("A", mode=mode)]
    pack = _production_pack(source)
    selected = pack["selected_results"][0]
    assert selected["metadata"]["prompt_projection_mode"] == mode
    if mode in {"historical_reference_restricted", "template_structure_only"}:
        assert selected["prompt_visible_text"] != source[0]["parent_text"]
    _packing_report(tmp_path, source, pack, True)


def test_x07_production_position_reordering_does_not_replace_evidence(tmp_path):
    source = [_candidate(str(index), mode="governed_full_text", text=f"source evidence number {index}")
        for index in range(1, 6)]
    pack = _production_pack(source, strategy="lost_in_middle_aware")
    assert [item["child_chunk_id"] for item in pack["selected_results"]] == [
        "child-1", "child-3", "child-5", "child-4", "child-2"]
    _packing_report(tmp_path, source, pack, True)


def test_x07_one_item_and_context_consistent_corruption_is_rejected(tmp_path):
    source = [_candidate("A", mode="historical_reference_restricted"),
        _candidate("B", mode="historical_reference_restricted")]
    pack = _production_pack(source)
    selected = pack["selected_results"][0]
    original = selected["prompt_visible_text"]
    replacement = "This project has already approved 999999 servers: synthetic unauthorized fact."
    selected["prompt_visible_text"] = selected["context_text"] = replacement
    pack["context"] = pack["context"].replace(original, replacement, 1)
    pack["rendered_text"] = pack["context"]
    assert all(item["prompt_visible_text"] in pack["context"] for item in pack["selected_results"])
    assert pack["selected_count"] == len(pack["selected_results"]) == 2
    _packing_report(tmp_path, source, pack, False)


def test_x07_known_projection_mode_cannot_override_governed_source_policy(tmp_path):
    source = [_candidate("A", mode="historical_reference_restricted")]
    pack = _production_pack(source)
    pack["selected_results"][0]["metadata"]["prompt_projection_mode"] = "governed_full_text"
    _packing_report(tmp_path, source, pack, False)


def test_x07_same_identity_cannot_pack_a_different_source_version(tmp_path):
    source = [_candidate("A", mode="governed_full_text")]
    pack = _production_pack(source)
    pack["selected_results"][0]["index_version"] = "wrong-source-index-v2"
    _packing_report(tmp_path, source, pack, False)


def test_x07_legitimate_custom_field_without_recorded_selection_is_unknown(tmp_path):
    source = [_candidate("A", mode="governed_full_text")]
    source[0]["custom_excerpt"] = "a permitted custom excerpt selected by the original pure packer"
    pack = _production_pack(source, text_field="custom_excerpt")
    assert pack["selected_results"][0]["prompt_visible_text"] == source[0]["custom_excerpt"]
    assert "text_field" not in pack
    _packing_report(tmp_path, source, pack, None)


def test_x07_production_first_item_budget_truncation_is_reconstructible(tmp_path):
    source = [_candidate("A", text="证据方法和边界。" * 350)]
    pack = _production_pack(source, truncated=True)
    visible = pack["selected_results"][0]["prompt_visible_text"]
    assert visible.endswith("\n...[TRUNCATED]")
    assert source[0]["text"].startswith(visible.removesuffix("\n...[TRUNCATED]"))
    _packing_report(tmp_path, source, pack, True)


@pytest.mark.parametrize("strategy", ["default", "lost_in_middle_aware"])
def test_x07_character_budget_truncation_reconstructs_source_metadata_header(strategy, tmp_path):
    source = [_candidate(str(index), text="a long source method and its recorded boundaries " * 30)
        for index in range(1, 4)]
    packer = ContextPacker(max_context_chars=450, max_items=3,
        include_metadata=True, packing_strategy=strategy)
    pack = packer.pack(source, token_budget=5000).to_dict()
    assert len(pack["selected_results"]) == 1
    assert pack["selected_results"][0]["metadata"]["context_truncated"]
    assert pack["used_chars"] <= 450 and "doc_id:" in pack["context"]
    _packing_report(tmp_path, source, pack, True)


def test_x07_arbitrary_source_prefix_is_not_proven_budget_truncation(tmp_path):
    source = [_candidate("A", text="证据方法和边界。" * 350)]
    pack = _production_pack(source, truncated=True)
    selected = pack["selected_results"][0]
    original = selected["prompt_visible_text"]
    replacement = source[0]["text"][:20] + "\n...[TRUNCATED]"
    selected["prompt_visible_text"] = selected["context_text"] = replacement
    pack["context"] = pack["context"].replace(original, replacement, 1)
    pack["rendered_text"] = pack["context"]
    assert replacement in pack["context"]
    _packing_report(tmp_path, source, pack, False)


@pytest.mark.parametrize("proof", ["child_alias", "parent_context_alias", "profile_digest_alias"])
def test_x07_conflicting_source_aliases_remain_unknown(proof, tmp_path):
    source = [_candidate("A", mode="governed_full_text")]
    item = source[0]
    if proof == "child_alias":
        item["chunk_id"] = "conflicting-child-A"
    elif proof == "parent_context_alias":
        item.update(context_granularity="parent", context_chunk_id="conflicting-parent-A")
    else:
        item["profile_sha256"] = "a" * 64
        item["metadata"]["document_profile"] = {"document_id": item["doc_id"], "tenant_id": item["tenant_id"],
            "source_sha256": "c" * 64, "profile_sha256": "b" * 64}
    pack = _production_pack(source)
    selected = pack["selected_results"][0]
    assert selected["text"] == item["text"] and selected["parent_text"] == item["parent_text"]
    for key in ("chunk_id", "child_chunk_id", "parent_chunk_id", "context_chunk_id", "context_granularity", "profile_sha256"):
        assert selected.get(key) == item.get(key)
    _packing_report(tmp_path, source, pack, None)


def test_x07_child_granularity_keeps_parent_and_context_id_meanings_distinct(tmp_path):
    source = [_candidate("A", mode="governed_full_text")]
    source[0].update(context_granularity="child", context_chunk_id=source[0]["child_chunk_id"])
    assert source[0]["context_chunk_id"] != source[0]["parent_chunk_id"]
    pack = _production_pack(source)
    _packing_report(tmp_path, source, pack, True)


@pytest.mark.parametrize("proof", ["source_text", "projection_mode", "future_projection_mode", "source_policy",
    "raw_field_ambiguity", "source_identity", "duplicate_source", "duplicate_target", "malformed_target_metadata",
    "contradictory_tenant", "contradictory_index_version"])
def test_x07_missing_or_ambiguous_projection_proof_is_unknown(proof, tmp_path):
    mode = "historical_reference_restricted" if proof == "source_policy" else "governed_full_text"
    source = [_candidate("A", mode=mode)]
    pack = _production_pack(source)
    if proof == "source_text":
        _remove_nested_fields(source[0], {"text", "parent_text", "child_text", "match_text"})
    elif proof == "projection_mode":
        pack["selected_results"][0]["metadata"].pop("prompt_projection_mode")
    elif proof == "future_projection_mode":
        pack["selected_results"][0]["metadata"]["prompt_projection_mode"] = "future_projector_v99"
    elif proof == "source_policy":
        source[0]["metadata"].pop("evidence_assessment_v2")
    elif proof == "raw_field_ambiguity":
        source[0]["child_text"] = "a different child representation with no recorded chosen field"
        pack["selected_results"][0]["child_text"] = source[0]["child_text"]
    elif proof == "source_identity":
        source[0].pop("tenant_id")
        pack["selected_results"][0].pop("tenant_id")
    elif proof == "duplicate_source":
        source.append(deepcopy(source[0]))
    elif proof == "duplicate_target":
        pack["selected_results"].append(deepcopy(pack["selected_results"][0]))
        pack["selected_count"] = len(pack["selected_results"])
    elif proof == "malformed_target_metadata":
        pack["selected_results"][0]["metadata"] = ["malformed projection proof"]
    elif proof == "contradictory_tenant":
        profile = {"tenant_id": "another-tenant", "source_sha256": "a" * 64}
        source[0]["metadata"]["document_profile"] = profile
        pack["selected_results"][0]["metadata"]["document_profile"] = deepcopy(profile)
    else:
        source[0]["metadata"]["index_version"] = "contradictory-index-v2"
        pack["selected_results"][0]["metadata"]["index_version"] = "contradictory-index-v2"
    _packing_report(tmp_path, source, pack, None)


@pytest.mark.parametrize("proof", ["token_budget", "max_context_chars", "truncated_source_id", "truncation_reason"])
def test_x07_truncation_missing_effective_boundaries_is_unknown(proof, tmp_path):
    source = [_candidate("A", text="证据方法和边界。" * 350)]
    pack = _production_pack(source, truncated=True)
    if proof in {"token_budget", "max_context_chars"}:
        pack.pop(proof)
    else:
        pack["selected_results"][0]["metadata"].pop(proof)
    _packing_report(tmp_path, source, pack, None)
