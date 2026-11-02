"""SDC-EDGE-003: bounded X06 producer-to-consumer source fidelity cases.

Each case is a new valid synthetic capture copied from the native/Fake fixture.
The old capture and all prior probes remain byte-for-byte unchanged. CRAG and
Governance legal controls use their existing pure production implementations.
"""
from __future__ import annotations

from copy import deepcopy
from hashlib import sha256
import json

import pytest

from eval.full_chain.snapshot import diagnostic_capture_complete
from rag.application.parent_child_retrieval import ParentChildRetrievalPipeline
from rag.evidence.governance_v2 import OnlineEvidenceGovernanceV2
from rag.ports.quality import EvidenceAssessment, EvidenceJudgement
from test_full_chain_eval_correction_iia_ir import _native_capture, _rewrite, _safe_report
from test_full_chain_eval_edge_content_fidelity import _pool, _pool_digests, _remove_nested_fields


BOUNDARIES = ("final_filter_input", "final_filter_output", "governance_input", "governance_output")
ENDPOINTS = ("selected_source", *BOUNDARIES)
CASES = [pytest.param("replace", boundary, field, False, id=f"replace-{boundary}-{field}")
    for boundary in BOUNDARIES for field in ("text", "parent_text", "child_text", "index_version", "doc_id")]
CASES += [pytest.param("replace", boundary, "context_text", False, id=f"replace-{boundary}-context_text")
    for boundary in BOUNDARIES]
CASES += [pytest.param("assessment", "", location, False, id="assessment-" + location)
    for location in ("earlier_root", "nested_governance")]
CASES += [pytest.param("legal", "", control, True, id="legal-" + control)
    for control in ("normal", "crag_filter", "governance_filter", "annotations")]
CASES += [pytest.param("missing", endpoint, "", None, id="missing-" + endpoint) for endpoint in ENDPOINTS]
CASES += [pytest.param("unknown", "", proof, None, id="unknown-" + proof)
    for proof in ("source_text", "source_version", "source_identity", "duplicate_input", "duplicate_output", "ambiguous_operation")]
CASES += [pytest.param("corrected", "", control, expected, id="corrected-" + control)
    for control, expected in (("normal", True), ("source_input_mismatch", False), ("nonresult_pool", None),
        ("duplicate_result", None))]
CASES += [pytest.param("binding", "", field, None, id="binding-" + field) for field in ("query", "round")]


def _items(rows, operation):
    return [row["item"] for row in _pool(rows, operation)[1]]


def _replace_items(rows, operation, new_items):
    _, item_rows = _pool(rows, operation)
    assert len(new_items) <= len(item_rows)
    removed = {id(row) for row in item_rows[len(new_items):]}
    rows[:] = [row for row in rows if id(row) not in removed]
    for row, item in zip(item_rows, new_items):
        row["item"] = deepcopy(item)


def _governance(rows):
    """Run the existing pure policy and reproduce its actual annotated output."""
    before, _ = _pool(rows, "governance_input")
    inputs = _items(rows, "governance_input")
    result = OnlineEvidenceGovernanceV2().evaluate(query=before["query"], results=deepcopy(inputs))
    eligible = set(result.eligible_candidate_ids)
    assessments = {item.evidence_id: item.model_dump(mode="json", exclude_none=True)
        for item in result.item_assessments}
    outputs = []
    for raw in inputs:
        item = deepcopy(raw)
        identifier = item.get("child_chunk_id") or item.get("chunk_id")
        metadata = dict(item.get("metadata") or {})
        if identifier in assessments:
            metadata["evidence_assessment_v2"] = assessments[identifier]
        metadata["evidence_eligibility_v2"] = identifier in eligible
        item["metadata"] = metadata
        if identifier in eligible:
            outputs.append(item)
    after, _ = _pool(rows, "governance_output")
    after["governance"] = result.to_dict()
    _replace_items(rows, "governance_output", outputs)
    return result, outputs


def _restrict_second_source_profile(value):
    """A new synthetic source profile with no permitted claim capability."""
    if isinstance(value, dict):
        if "profile_sha256" in value:
            value["profile_sha256"] = "b" * 64
        profile = value.get("document_profile")
        if isinstance(profile, dict):
            profile["content_capabilities"]["allowed_claim_types"] = []
        for child in value.values():
            _restrict_second_source_profile(child)
    elif isinstance(value, list):
        for child in value:
            _restrict_second_source_profile(child)


def _legal_case(rows, control):
    if control == "normal":
        # Native output must contain a newly attached assessment, not a copied
        # assessment falsely required to be equal at the governance boundary.
        assert all("evidence_assessment_v2" not in (item.get("metadata") or {})
            for item in _items(rows, "governance_input"))
        assert all("evidence_assessment_v2" in (item.get("metadata") or {})
            for item in _items(rows, "governance_output"))
        return
    if control == "governance_filter":
        candidates = _items(rows, "initial_rerank_output")
        assert len(candidates) >= 2
        restricted_id = candidates[1]["child_chunk_id"]
        for row in rows:
            if row.get("event_type") == "POOL_ITEM" and row["item"].get("child_chunk_id") == restricted_id:
                _restrict_second_source_profile(row["item"])
        result, outputs = _governance(rows)
        assert outputs and restricted_id in result.rejected_candidate_ids
        assert len(outputs) < len(_items(rows, "governance_input"))
        return
    source = deepcopy(_items(rows, "initial_rerank_output"))
    judgements = tuple(EvidenceJudgement(item["child_chunk_id"],
        "irrelevant" if control == "crag_filter" and index == 1 else "relevant", 1.0,
        "fixed pure CRAG control") for index, item in enumerate(source))
    assessment = EvidenceAssessment(True, 1.0, "fixed bounded final assessment", item_judgements=judgements)
    annotated, selected, filter_report = ParentChildRetrievalPipeline._apply_final_assessment(
        results=source, assessment=assessment)
    _replace_items(rows, "final_filter_input", annotated)
    _replace_items(rows, "final_filter_output", selected)
    _pool(rows, "final_filter_output")[0]["filter_report"] = filter_report
    _replace_items(rows, "governance_input", selected)
    _, outputs = _governance(rows)
    assert outputs and all("crag_final_judgement" in item["metadata"] for item in selected)
    if control == "crag_filter":
        assert len(selected) < len(source) and filter_report["rejected_irrelevant_ids"]
    else:
        assert len(outputs) == len(source)
        for item in outputs:
            item["metadata"]["fixture_stage_annotation"] = "permitted governance annotation"
        _replace_items(rows, "governance_output", outputs)


def _remove_endpoint(rows, endpoint):
    if endpoint == "selected_source":
        pool_ids = {row["pool_id"] for row in rows if row.get("event_type") == "POOL_BEGIN"
            and row.get("evidence_key") == "R-RERANK" and row.get("phase") == "RESULT"}
    else:
        pool_ids = {_pool(rows, endpoint)[0]["pool_id"]}
    rows[:] = [row for row in rows if row.get("pool_id") not in pool_ids]
    if endpoint == "final_filter_input":
        # Earlier assessment INPUT is a different operation and remains present.
        assert _pool(rows, "assessment_input:0", "INPUT")[1]


def _unknown_case(rows, proof):
    if proof in {"source_text", "source_version", "source_identity"}:
        fields = {"source_text": {"text", "parent_text", "child_text", "match_text", "context_text", "prompt_visible_text"},
            "source_version": {"index_version", "source_sha256", "source_version", "document_version"},
            "source_identity": {"tenant_id"}}[proof]
        for operation in ("initial_rerank_output", *BOUNDARIES):
            for item in _items(rows, operation):
                _remove_nested_fields(item, fields)
    elif proof in {"duplicate_input", "duplicate_output"}:
        operation = "final_filter_input" if proof == "duplicate_input" else "governance_output"
        _, items = _pool(rows, operation)
        assert len(items) >= 2
        items[1]["item"] = deepcopy(items[0]["item"])
    else:
        begin, _ = _pool(rows, "final_filter_output")
        duplicates = [deepcopy(row) for row in rows if row.get("pool_id") == begin["pool_id"]]
        for row in duplicates:
            row["pool_id"] += "-ambiguous"
        anchor = next(index for index, row in enumerate(rows)
            if row.get("event_type") == "POOL_BEGIN" and row.get("operation_ref") == "governance_input")
        rows[anchor:anchor] = duplicates


def _corrected_case(rows, control):
    begin, _ = _pool(rows, "initial_rerank_output")
    output = [deepcopy(row) for row in rows if row.get("pool_id") == begin["pool_id"]]
    for row in output:
        row["pool_id"] += "-corrected"
        row.update(evidence_key="R-CORRECT", unit_id="C10", operation_ref="correction:1", round=1,
            phase="DECISION" if control == "nonresult_pool" else "RESULT")
    if control == "source_input_mismatch":
        next(row for row in output if row.get("event_type") == "POOL_ITEM")["item"]["parent_text"] = (
            "synthetic corrected source that the following final input did not preserve")
    decision = next(row for row in rows if row.get("event_type") == "R-CORRECT"
        and row.get("payload", {}).get("phase") == "DECISION")
    correction_begin = deepcopy(decision)
    correction_begin["operation_ref"] = "correction:1"
    correction_begin["payload"] = {"phase": "BEGIN", "round": 1, "query": begin["query"],
        "queries": ["fixed synthetic corrective query"], "merge_original_candidates": True}
    anchor = next(index for index, row in enumerate(rows)
        if row.get("event_type") == "POOL_BEGIN" and row.get("operation_ref") == "final_filter_input")
    if control == "duplicate_result":
        duplicate = deepcopy(output)
        for row in duplicate:
            row["pool_id"] += "-ambiguous"
        output += duplicate
    rows[anchor:anchor] = [correction_begin, *output]
    for operation in BOUNDARIES:
        _pool(rows, operation)[0]["round"] = 1


def _assessment_replacement(rows, location):
    assessments = {item["child_chunk_id"]: deepcopy(item["metadata"]["evidence_assessment_v2"])
        for item in _items(rows, "governance_output")}
    for operation in ("initial_rerank_output", *BOUNDARIES):
        for item in _items(rows, operation):
            if location == "earlier_root":
                metadata = item.setdefault("metadata", {})
            else:
                metadata = item.setdefault("child_chunk", {}).setdefault("metadata", {})
            metadata["evidence_assessment_v2"] = deepcopy(assessments[item["child_chunk_id"]])
    operation = "final_filter_input" if location == "earlier_root" else "governance_output"
    item = _items(rows, operation)[0]
    metadata = item["metadata"] if location == "earlier_root" else item["child_chunk"]["metadata"]
    assessment = metadata["evidence_assessment_v2"]
    assert isinstance(assessment["eligibility"], bool)
    assessment["eligibility"] = not assessment["eligibility"]


@pytest.mark.parametrize("kind,boundary,detail,expected", CASES)
def test_x06_adjacent_source_content_fidelity(kind, boundary, detail, expected, tmp_path):
    original_path = _native_capture(tmp_path)
    original_bytes = original_path.read_bytes()
    rows = [json.loads(line) for line in original_bytes.splitlines()]
    # This recorded version belongs only to the new test fixture, not history.
    for row in rows:
        if row.get("event_type") == "POOL_ITEM":
            row["item"]["index_version"] = "x06-fixture-index-v1"
    if kind == "replace":
        if detail == "context_text":
            for operation in ("initial_rerank_output", *BOUNDARIES):
                for item in _items(rows, operation):
                    item["context_text"] = item["text"]
        item = _items(rows, boundary)[0]
        assert detail in item
        item[detail] = "synthetic unauthorized source replacement"
    elif kind == "legal":
        _legal_case(rows, detail)
    elif kind == "missing":
        _remove_endpoint(rows, boundary)
    elif kind == "unknown":
        _unknown_case(rows, detail)
    elif kind == "corrected":
        _corrected_case(rows, detail)
    elif kind == "assessment":
        _assessment_replacement(rows, detail)
    else:
        _pool(rows, "governance_input")[0][detail] = 1 if detail == "round" else "unrelated endpoint query"
    _pool_digests(rows)
    case_path = original_path.parent / "x06_content_case.jsonl"
    _rewrite(case_path, rows)
    snapshot, report, machine = _safe_report(case_path, tmp_path)
    assert diagnostic_capture_complete(snapshot) and not snapshot.limitations
    all_edges = [edge for edge in report.edge_fidelity if edge.edge_id == "X06"]
    assert all_edges and any(edge.source_refs for edge in all_edges)
    edges = [edge for edge in all_edges if edge.payload.get("round") == 1] if kind == "corrected" else all_edges
    assert edges and all(edge.assertion_result is expected for edge in edges), [
        (edge.payload.get("round"), edge.reason, edge.payload) for edge in all_edges]
    state = "UNMEASURED" if expected is None else "MEASURED"
    assert all(edge.measurement_state == state for edge in edges)
    if kind == "corrected":
        assert all(edge.assertion_result is None for edge in all_edges if edge.payload.get("round") != 1)
    persisted = [edge for edge in machine["edge_fidelity"] if edge["edge_id"] == "X06"
        and (kind != "corrected" or edge["payload"].get("round") == 1)]
    assert len(persisted) == len(edges)
    assert all(edge["assertion_result"] is expected and edge["measurement_state"] == state for edge in persisted)
    assert original_path.read_bytes() == original_bytes
