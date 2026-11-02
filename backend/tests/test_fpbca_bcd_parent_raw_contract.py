"""DRAFT ONLY; future backend/tests/test_fpbca_bcd_parent_raw_contract.py.

Existing strict P2-F frozen contract; pure current StageD/native-parent fixtures.
No source/test export, parse, import, collection or execution while preparing.
Original eleven RED controls remain a separate preserved selector/file.
"""

from copy import deepcopy
import json
from types import SimpleNamespace

import pytest

from core.hashing import canonical_sha256
from rag.plugins.candidate_enrichers import ParentChildCandidateEnricher
from rag.schema.Chunk_Schema import build_parent_chunk_v1
from rag.schema.Retrieval_Result_Schema import build_retrieval_result_v2
from rag.schema.candidate import CandidateSet
from rag.schema.retrieval_candidate_v2 import build_retrieval_candidate_v2
from test_support.stage_d_fixtures import document_profile, retrieval_result


def _pair(child_id="child-1", parent_id="parent-source-1", profile=None):
    row = retrieval_result(
        child_id=child_id, parent_id=parent_id, profile=profile,
        text=f"INDEPENDENT CHILD {child_id}", representation_type="paragraph_text",
    )
    child = deepcopy(row["child_chunk"])
    child["document_profile"] = deepcopy(row["metadata"]["document_profile"])
    parent = build_parent_chunk_v1(
        parent_chunk_id=parent_id, doc_id=child["doc_id"],
        tenant_id=child["tenant_id"], kb_id=child["kb_id"],
        text="LAWFUL PARENT CONTEXT",
        child_chunk_ids=[child_id],
        created_at="2026-09-04T00:00:00Z",
        extra=deepcopy(child["extra"]),
    )
    return child, parent


def _result(child, parent, **kwargs):
    return build_retrieval_result_v2(
        child_chunk=child, parent_chunk=parent, rank=1, score=0.75, **kwargs
    )


def _hit(child, score=1.0, source="dense"):
    return {
        "child_chunk": deepcopy(child),
        "child_chunk_id": child["child_chunk_id"],
        "parent_chunk_id": child["parent_chunk_id"],
        "doc_id": child["doc_id"],
        "fusion_score": score,
        "score": score,
        "retrieval_sources": [source],
        "source_ranks": {source: 1},
        "source_scores": {source: score},
        "rrf_contributions": {source: 0.01},
        f"{source}_score": score,
    }


def _enrich(rows, parent, *, dedup_parent=True):
    resource = SimpleNamespace(
        get_parent_store=lambda: SimpleNamespace(get=lambda _: deepcopy(parent))
    )
    enricher = ParentChildCandidateEnricher(
        build_context={"resource_pool": resource}, dedup_parent=dedup_parent,
    )
    return enricher.enrich(CandidateSet(query="p2-source", source_name="controlled", candidates=rows))


def _assert_child_only(result, child):
    assert result["text"] == child["text"]
    assert result["child_text"] == child["text"]
    assert result["parent_text"] == ""
    assert result["context_granularity"] == "child"
    assert result["metadata"]["parent_found"] is False
    candidate = build_retrieval_candidate_v2(result)
    assert candidate.document_id == child["doc_id"]
    assert candidate.tenant_id == child["tenant_id"]
    assert candidate.kb_id == child["kb_id"]


@pytest.mark.parametrize("field", ["tenant_id", "kb_id", "doc_id", "parent_chunk_id", "child_id"])
@pytest.mark.parametrize("missing", ["absent", "empty", "null"])
def test_primary_raw_tuple_missing_never_borrows_parent_or_profile(field, missing):
    child, parent = _pair()
    keys = ("chunk_id", "child_chunk_id") if field == "child_id" else (field,)
    for key in keys:
        if missing == "absent":
            child.pop(key, None)
        else:
            child[key] = "" if missing == "empty" else None
    with pytest.raises(ValueError):
        _result(child, parent)


@pytest.mark.parametrize("field", ["document_profile", "document_id", "source_sha256"])
def test_primary_required_profile_missing_never_borrows_parent(field):
    child, parent = _pair()
    if field == "document_profile":
        child.pop(field)
        child["extra"].pop(field)
    else:
        child["document_profile"].pop(field)
        child["extra"]["document_profile"].pop(field)
    with pytest.raises(ValueError):
        _result(child, parent)


@pytest.mark.parametrize("attack", ["child_alias", "extra_alias", "raw_doc_profile", "raw_source_pin", "direct_extra_profile", "metadata_profile"])
def test_primary_alias_source_or_supplied_copy_conflicts_rejected(attack):
    child, parent = _pair()
    if attack == "child_alias":
        child["child_chunk_id"] = "alias-other-child"
    elif attack == "extra_alias":
        child["extra"]["child_chunk_id"] = "extra-other-child"
    elif attack == "raw_doc_profile":
        for profile in (child["document_profile"], child["extra"]["document_profile"]):
            profile["document_id"] = "other-document"
    elif attack == "raw_source_pin":
        child["source_sha256"] = "wrong-source-pin"
    elif attack == "direct_extra_profile":
        child["document_profile"]["authority"]["issuer"] = "other-issuer"
    else:
        child["metadata"] = {"document_profile": deepcopy(child["document_profile"])}
        child["metadata"]["document_profile"]["authority"]["issuer"] = "other-issuer"
    with pytest.raises(ValueError):
        _result(child, parent)


@pytest.mark.parametrize("field", ["tenant_id", "kb_id", "doc_id", "parent_chunk_id", "profile", "profile_document", "profile_source", "child_chunk_ids"])
def test_parent_mandatory_omission_denies_expansion(field):
    child, parent = _pair()
    if field == "profile":
        parent["extra"].pop("document_profile")
    elif field in {"profile_document", "profile_source"}:
        parent["extra"]["document_profile"].pop(
            "document_id" if field == "profile_document" else "source_sha256"
        )
    else:
        parent.pop(field)
    result = _result(child, parent)
    _assert_child_only(result, child)


@pytest.mark.parametrize("attack", ["wrong_membership", "empty_membership", "bad_member_type", "wrong_alias", "stale_child_alias", "conflicting_profile_copy"])
def test_parent_native_membership_alias_and_copy_fail_closed(attack):
    child, parent = _pair()
    if attack == "wrong_membership":
        parent["child_chunk_ids"] = ["unmatched-child"]
    elif attack == "empty_membership":
        parent["child_chunk_ids"] = []
    elif attack == "bad_member_type":
        parent["child_chunk_ids"] = [{"child": child["child_chunk_id"]}]
    elif attack == "wrong_alias":
        parent["chunk_id"] = "wrong-parent"
    elif attack == "stale_child_alias":
        parent["child_chunk_id"] = child["child_chunk_id"]
    else:
        parent["document_profile"] = deepcopy(parent["extra"]["document_profile"])
        parent["document_profile"]["authority"]["issuer"] = "other-issuer"
    _assert_child_only(_result(child, parent), child)


@pytest.mark.parametrize("semantic", ["issuer", "allowed_capability", "source", "optional_lineage", "parser", "taxonomy"])
def test_complete_parent_profile_changes_both_copies_with_stale_external_pin_denied(semantic):
    child, parent = _pair()
    parent["document_profile"] = deepcopy(parent["extra"]["document_profile"])
    child_profile_before = deepcopy(child["document_profile"])
    original_pin = child_profile_before["profile_sha256"]
    for profile in (parent["document_profile"], parent["extra"]["document_profile"]):
        if semantic == "issuer":
            profile["authority"]["issuer"] = "changed-issuer"
        elif semantic == "allowed_capability":
            profile["content_capabilities"]["allowed_claim_types"] = ["current_project_fact"]
        elif semantic == "source":
            profile["source_sha256"] = "changed-source-pin"
        elif semantic == "optional_lineage":
            profile["catalog_activation_ref"] = "different-offline-lineage"
        elif semantic == "parser":
            profile["parsing"]["parser_strategy"] = "different-parser"
        else:
            profile["taxonomy_mapping"] = {"observed": "different"}
        assert profile["profile_sha256"] == original_pin
    assert parent["document_profile"] == parent["extra"]["document_profile"]
    assert canonical_sha256(parent["document_profile"]) != canonical_sha256(child_profile_before)
    _assert_child_only(_result(child, parent), child)
    assert child["document_profile"] == child_profile_before


def test_native_parent_without_legacy_alias_and_without_new_optional_mandatories():
    child, parent = _pair()
    assert "chunk_id" not in parent and "child_chunk_id" not in parent
    for record in (child, parent):
        for key in ("file_id", "source_unit_ids", "text_hash"):
            record.pop(key, None)
    result = _result(child, parent)
    assert result["text"] == parent["text"]
    assert result["metadata"]["parent_found"] is True
    assert result["context_granularity"] == "parent"
    assert result["chunk_id"] == child["child_chunk_id"]


@pytest.mark.parametrize("dedup_parent", [True, False])
@pytest.mark.parametrize("attack", ["missing_raw_tenant", "duplicate_alias_conflict", "wrapper_parent_conflict", "wrapper_profile_conflict"])
def test_all_original_members_before_id_dedup_and_false_path(dedup_parent, attack):
    child, parent = _pair()
    good = _hit(child, score=1.0, source="dense")
    bad = _hit(deepcopy(child), score=999.0, source="invalid-source")
    if attack == "missing_raw_tenant":
        bad["child_chunk"].pop("tenant_id")
        bad["tenant_id"] = child["tenant_id"]  # wrapper cannot repair raw absence
    elif attack == "duplicate_alias_conflict":
        bad["child_chunk"]["child_chunk_id"] = "stale-child-alias"
    elif attack == "wrapper_parent_conflict":
        bad["parent_chunk_id"] = "wrapper-other-parent"
    else:
        bad["document_profile"] = deepcopy(child["document_profile"])
        bad["document_profile"]["authority"]["issuer"] = "wrapper-other-issuer"
    before = deepcopy([good, bad, parent])
    enriched = _enrich([good, bad], parent, dedup_parent=dedup_parent)
    assert [good, bad, parent] == before
    assert len(enriched.candidates) == 1
    result = enriched.candidates[0]
    _assert_child_only(result, child)
    assert result["score"] == 1.0
    assert result["metadata"]["matched_child_chunk_ids"] == [child["child_chunk_id"]]
    assert result["metadata"]["matched_child_count"] == 1
    assert result["metadata"]["retrieval_sources"] == ["dense"]
    assert "invalid-source" not in json.dumps(result, ensure_ascii=False)
    assert "stale-child-alias" not in json.dumps(result, ensure_ascii=False)


@pytest.mark.parametrize("dedup_parent", [True, False])
def test_every_independent_lawful_child_needs_actual_parent_membership(dedup_parent):
    child, parent = _pair()
    second, _ = _pair(child_id="child-2", parent_id=child["parent_chunk_id"])
    rows = [_hit(child, score=2.0), _hit(second, score=1.0, source="keyword")]
    result = _enrich(rows, parent, dedup_parent=dedup_parent)
    assert len(result.candidates) == (1 if dedup_parent else 2)
    for candidate in result.candidates:
        assert candidate["parent_text"] == ""
        assert candidate["context_granularity"] == "child"
        assert candidate["metadata"]["parent_found"] is False


def test_entire_invalid_group_is_omitted_and_does_not_supply_scores():
    child, parent = _pair()
    child.pop("tenant_id")
    assert _enrich([_hit(child, score=999)], parent).candidates == []


def test_distinct_lawful_bindings_same_parent_id_remain_separate_child_projections():
    child, parent = _pair()
    other_profile = document_profile(document_id="doc-other", tenant_id="tenant-other")
    other, _ = _pair(child_id="child-other", parent_id=child["parent_chunk_id"], profile=other_profile)
    parent["child_chunk_ids"].append(other["child_chunk_id"])
    result = _enrich([_hit(child, score=2), _hit(other, score=1, source="keyword")], parent)
    assert len(result.candidates) == 2
    expected = {child["child_chunk_id"]: child, other["child_chunk_id"]: other}
    for item in result.candidates:
        own = expected[item["child_chunk_id"]]
        _assert_child_only(item, own)
        assert item["metadata"]["matched_child_chunk_ids"] == [own["child_chunk_id"]]
        assert item["metadata"]["document_profile"]["document_id"] == own["doc_id"]


def test_lawful_two_child_parent_keeps_scores_sources_utility_and_inputs():
    child, parent = _pair()
    second, _ = _pair(child_id="child-2", parent_id=child["parent_chunk_id"])
    parent["child_chunk_ids"].append(second["child_chunk_id"])
    rows = [_hit(child, score=2.0, source="dense"), _hit(second, score=1.0, source="keyword")]
    before = deepcopy([rows, parent])
    result = _enrich(rows, parent).candidates
    assert [rows, parent] == before
    assert len(result) == 1
    assert result[0]["text"] == parent["text"]
    assert result[0]["score"] == 2.0
    assert result[0]["metadata"]["parent_found"] is True
    assert result[0]["metadata"]["matched_child_chunk_ids"] == [child["child_chunk_id"], second["child_chunk_id"]]
    assert result[0]["metadata"]["retrieval_sources"] == ["dense", "keyword"]
    assert result[0]["metadata"]["source_ranks"] == rows[0]["source_ranks"]
    assert result[0]["metadata"]["rrf_contributions"] == rows[0]["rrf_contributions"]


def test_rejected_parent_removed_from_all_semantic_and_caller_overlay_channels():
    child, parent = _pair()
    marker = "P2_REJECTED_PARENT_SECRET"
    parent.update(tenant_id="tenant-other", text=marker, title=marker, section=marker)
    parent["metadata"] = {"parent_debug": marker}
    parent["extra"]["document_profile"]["authority"]["issuer"] = marker
    profile = deepcopy(parent["extra"]["document_profile"])
    metadata = {
        "parent_found": True, "parent_text": marker, "title": marker,
        "section": marker, "governance": {"issuer": marker},
        "parent_metadata": {"body": marker}, "document_profile": profile,
        "retrieval_candidate_v2_error": {"message": marker},
        "retrieval_sources": ["dense"],
    }
    extra = {"parent_body": marker, "document_profile": profile, "governance": marker}
    before = deepcopy([child, parent, metadata, extra])
    result = _result(child, parent, metadata=metadata, extra=extra)
    assert [child, parent, metadata, extra] == before
    _assert_child_only(result, child)
    assert marker not in json.dumps(result, ensure_ascii=False)
    assert "retrieval_candidate_v2_error" not in result["metadata"]


def test_same_id_caller_matched_metadata_cannot_override_raw_child_body():
    child, parent = _pair()
    forged = deepcopy(child)
    forged.update(text="P2_METADATA_PARENT_BODY", title="P2_METADATA_PARENT_BODY")
    result = _result(child, parent, metadata={
        "matched_child_chunks": [forged],
        "matched_child_chunk_ids": [child["child_chunk_id"]],
        "matched_child_count": 1,
        "validated": True,
    })
    _assert_child_only(result, child)
    assert "P2_METADATA_PARENT_BODY" not in json.dumps(result, ensure_ascii=False)
    assert result["metadata"]["matched_child_chunks"][0]["text"] == child["text"]
    assert "validated" not in result["metadata"]


def test_direct_builder_revalidates_every_supplied_raw_matched_child():
    child, parent = _pair()
    bad = deepcopy(child)
    bad["chunk_id"] = "foreign-child"
    bad["child_chunk_id"] = "foreign-child"
    bad["tenant_id"] = "tenant-other"  # existing unchanged profile makes it invalid
    result = _result(child, parent, metadata={
        "matched_child_chunks": [child, bad],
        "matched_child_chunk_ids": [child["child_chunk_id"], "foreign-child"],
        "matched_child_count": 2,
    })
    _assert_child_only(result, child)
    assert result["metadata"]["matched_child_chunk_ids"] == [child["child_chunk_id"]]
    assert "foreign-child" not in json.dumps(result["metadata"], ensure_ascii=False)


@pytest.mark.parametrize("container_name", ["extra", "metadata"])
@pytest.mark.parametrize("field", [
    "tenant_id", "kb_id", "doc_id", "document_id", "chunk_id",
    "child_chunk_id", "parent_chunk_id", "source_sha256", "profile_sha256",
])
def test_supplied_nested_child_identity_scalar_copies_are_never_silently_skipped(container_name, field):
    for invalid in (None, "", "different-nested-identity"):
        child, parent = _pair()
        child.setdefault(container_name, {})[field] = invalid
        before = canonical_sha256(child)
        with pytest.raises(ValueError):
            _result(child, parent)
        assert canonical_sha256(child) == before


@pytest.mark.parametrize("field", ["tenant_id", "kb_id", "doc_id", "parent_chunk_id", "child_id"])
def test_nested_identity_copies_cannot_establish_a_missing_direct_raw_tuple(field):
    child, parent = _pair()
    if field == "child_id":
        values = {"chunk_id": child["chunk_id"], "child_chunk_id": child["child_chunk_id"]}
        child.pop("chunk_id")
        child.pop("child_chunk_id")
    else:
        values = {field: child.pop(field)}
    for container_name in ("extra", "metadata"):
        child.setdefault(container_name, {}).update(values)
    with pytest.raises(ValueError):
        _result(child, parent)


@pytest.mark.parametrize("container_name", ["extra", "metadata"])
@pytest.mark.parametrize("field", ["chunk_id", "child_chunk_id", "parent_chunk_id"])
def test_supplied_nested_parent_aliases_bind_native_parent_identity(container_name, field):
    for invalid in (None, "", "different-nested-parent"):
        child, parent = _pair()
        parent.setdefault(container_name, {})[field] = invalid
        before = canonical_sha256(parent)
        _assert_child_only(_result(child, parent), child)
        assert canonical_sha256(parent) == before


@pytest.mark.parametrize("container_name", ["extra", "metadata"])
def test_supplied_nested_wrapper_identity_copies_reject_without_parent_expansion(container_name):
    for field in ("chunk_id", "child_chunk_id", "parent_chunk_id"):
        for invalid in (None, "", "different-wrapper-identity"):
            child, parent = _pair()
            valid = _hit(child, score=1.0)
            invalid_hit = _hit(child, score=999.0, source="keyword")
            invalid_hit.setdefault(container_name, {})[field] = invalid
            output = _enrich([valid, invalid_hit], parent)
            assert len(output.candidates) == 1
            _assert_child_only(output.candidates[0], child)
            assert output.candidates[0]["score"] == 1.0
            assert output.candidates[0]["metadata"]["retrieval_sources"] == ["dense"]


def test_lawful_parent_nested_aliases_are_optional_and_when_supplied_equal_native_identity():
    child, parent = _pair()
    assert "chunk_id" not in parent and "child_chunk_id" not in parent
    for container_name in ("extra", "metadata"):
        parent.setdefault(container_name, {}).update({
            "chunk_id": parent["parent_chunk_id"],
            "child_chunk_id": parent["parent_chunk_id"],
            "parent_chunk_id": parent["parent_chunk_id"],
        })
    result = _result(child, parent)
    assert result["context_granularity"] == "parent"
    assert result["parent_text"] == parent["text"]


def test_actual_nullable_profile_governance_and_lineage_are_not_new_identity_requirements():
    profile = document_profile(
        document_type="policy_standard", evidence_role="policy_standard",
        project_relation="general", project_id=None,
    )
    child, parent = _pair(profile=profile)
    # StageC projection/base_extra lawfully carries these None values.
    for record in (child, parent):
        for container_name in ("extra", "metadata"):
            record.setdefault(container_name, {})["project_id"] = None
    result = _result(child, parent)
    candidate = build_retrieval_candidate_v2(result)
    assert candidate.project_id is None
    assert candidate.document_profile["authority"]["effective_date"] is None
    assert candidate.document_profile["authority"]["expiration_date"] is None
    assert candidate.document_profile["taxonomy_mapping"] is None
    assert result["context_granularity"] == "parent"
