"""RPH-016: normalize external certainty-code representation, never Support truth."""

from copy import deepcopy
from itertools import permutations
import json

import pytest
from pydantic import ValidationError

from apps.enterprise_document.schemas.agent_rag_v1.claim_support import (
    ClaimSupportAuthorityKind,
    ClaimSupportSubjectRefSchema,
)
from apps.enterprise_document.services.agent_rag_v1.claim_support_composition import (
    SupportCertaintyObservationSchema,
    SupportModelReply,
    TargetClaimSupportComposition,
)
from apps.enterprise_document.services.agent_rag_v1.claim_support_runtime import (
    ClaimSupportRuntime,
)
from core.hashing import canonical_sha256


# Exact reason-code values/order from R5, without business text or runtime data.
R5_CODES = (
    "EVIDENCE_IS_OTHER_PROJECT_REFERENCE_ONLY",
    "CLAIM_GENERAL_ARCHITECTURE_NOT_EXPLICITLY_STATED",
    "NO_EXPLICIT_CERTAINTY_QUALIFIER_IN_EVIDENCE",
)
CANONICAL_CODES = (
    "CLAIM_GENERAL_ARCHITECTURE_NOT_EXPLICITLY_STATED",
    "EVIDENCE_IS_OTHER_PROJECT_REFERENCE_ONLY",
    "NO_EXPLICIT_CERTAINTY_QUALIFIER_IN_EVIDENCE",
)
EVIDENCE_REFS = ("evidence-item://test/a", "evidence-item://test/b")


@pytest.fixture
def support_scope():
    subject = ClaimSupportSubjectRefSchema(
        authority_kind=ClaimSupportAuthorityKind.SIF07_CLAIM,
        subject_ref="claim://test/certainty-representation",
    )
    target = ClaimSupportRuntime.materialize_evidence_target_set(
        subject, EVIDENCE_REFS, "claim-authorized-evidence-v1"
    )
    return {
        "subject": subject,
        "target": target,
        "occurrence": "claim-support-evaluation://test/certainty-representation",
    }


def reply(codes=CANONICAL_CODES):
    return {
        "relation": "PARTIALLY_SUPPORTED",
        "supporting_refs": list(EVIDENCE_REFS),
        "contradicting_refs": [],
        "certainty_relation": "INDETERMINATE",
        "certainty_reason_codes": list(codes),
    }


def parse(raw, scope):
    return TargetClaimSupportComposition.parse_current_reply(raw, **scope)


@pytest.mark.parametrize("wire_format", ["json", "dict-list", "dict-tuple"])
def test_r5_unique_unsorted_codes_are_admitted_without_mutating_input(
    wire_format, support_scope
):
    raw = reply(R5_CODES)
    if wire_format == "dict-tuple":
        raw["certainty_reason_codes"] = R5_CODES
    before = deepcopy(raw)
    original_codes = raw["certainty_reason_codes"]
    candidate, certainty = parse(
        json.dumps(raw) if wire_format == "json" else raw, support_scope
    )
    expected_candidate, expected_certainty = parse(reply(), support_scope)
    assert candidate == expected_candidate
    assert certainty == expected_certainty
    assert certainty["certainty_relation"] == "INDETERMINATE"
    assert tuple(certainty["reason_codes"]) == CANONICAL_CODES
    assert raw == before
    assert raw["certainty_reason_codes"] is original_codes


def test_unique_reason_permutations_share_exact_observation_identity(
    support_scope,
):
    expected_candidate, expected_certainty = parse(reply(), support_scope)
    expected_receipt = SupportCertaintyObservationSchema.model_validate(
        {
            **expected_certainty,
            "physical_support_receipt_ref": "artifact://test/support/candidate",
            "input_fingerprint": "test-exact-input-fingerprint",
        }
    )
    expected_hash = canonical_sha256(expected_receipt.model_dump(mode="json"))
    for order in permutations(R5_CODES):
        candidate, certainty = parse(reply(order), support_scope)
        receipt = SupportCertaintyObservationSchema.model_validate(
            {
                **certainty,
                "physical_support_receipt_ref": expected_receipt.physical_support_receipt_ref,
                "input_fingerprint": expected_receipt.input_fingerprint,
            }
        )
        assert candidate == expected_candidate
        assert receipt == expected_receipt
        assert canonical_sha256(receipt.model_dump(mode="json")) == expected_hash
        assert candidate.claim_support_subject_ref == support_scope["subject"]
        assert candidate.evidence_target_set_ref == support_scope["target"]
        assert candidate.evaluation_occurrence_ref == support_scope["occurrence"]


@pytest.mark.parametrize("wire_format", ["json", "dict-list", "dict-tuple"])
def test_repeated_codes_remain_invalid_without_mutating_input(wire_format, support_scope):
    for order in permutations(R5_CODES):
        codes = (*order, order[0], order[-1])
        raw = reply(codes)
        if wire_format == "dict-tuple":
            raw["certainty_reason_codes"] = codes
        before = deepcopy(raw)
        with pytest.raises(ValidationError, match="canonical ordered-unique"):
            parse(json.dumps(raw) if wire_format == "json" else raw, support_scope)
        assert raw == before


@pytest.mark.parametrize("certainty_relation", ["ALIGNED", "ESCALATED", "INDETERMINATE"])
def test_code_representation_does_not_change_independent_semantic_outputs(
    certainty_relation, support_scope
):
    raw = reply(R5_CODES)
    raw["certainty_relation"] = certainty_relation
    candidate, certainty = parse(raw, support_scope)
    assert candidate.relation.value == "PARTIALLY_SUPPORTED"
    assert candidate.supporting_refs == EVIDENCE_REFS
    assert candidate.contradicting_refs == ()
    assert certainty["certainty_relation"] == certainty_relation
    assert tuple(certainty["reason_codes"]) == CANONICAL_CODES


def test_normalization_preserves_exact_strings_without_trimming_or_dropping():
    raw = reply(("Z", "", "  A  ", "a"))
    parsed = TargetClaimSupportComposition._reply_model(raw)
    assert parsed.certainty_reason_codes == ("", "  A  ", "Z", "a")
    assert raw["certainty_reason_codes"] == ["Z", "", "  A  ", "a"]


@pytest.mark.parametrize("codes", [R5_CODES, (*CANONICAL_CODES, CANONICAL_CODES[0])],
                         ids=["unsorted", "repeated"])
def test_direct_reply_and_persisted_receipt_contracts_remain_strict(codes):
    with pytest.raises(ValidationError, match="canonical ordered-unique"):
        SupportModelReply.model_validate(reply(codes))
    with pytest.raises(ValidationError, match="canonical ordered-unique"):
        SupportCertaintyObservationSchema.model_validate(
            {
                "certainty_relation": "INDETERMINATE",
                "reason_codes": codes,
                "physical_support_receipt_ref": "artifact://test/support/candidate",
                "input_fingerprint": "test-exact-input-fingerprint",
            }
        )


@pytest.mark.parametrize("value", [None, "CODE", 1, [1], [{"code": "X"}], {"code": "X"}],
                         ids=["null", "string", "integer", "integer-member", "object-member", "object"])
def test_invalid_code_shapes_still_fail_original_schema_without_mutation(value, support_scope):
    raw = reply()
    raw["certainty_reason_codes"] = value
    before = deepcopy(raw)
    with pytest.raises(ValidationError):
        parse(raw, support_scope)
    assert raw == before


@pytest.mark.parametrize("raw", [
    '{"relation":"NOT_SUPPORTED","supporting_refs":[],"contradicting_refs":[],"certainty_relation":"INDETERMINATE","certainty_reason_codes":[],"certainty_reason_codes":[]}',
    '{"relation":"NOT_SUPPORTED","relation":"SUPPORTED","supporting_refs":[],"contradicting_refs":[],"certainty_relation":"INDETERMINATE"}',
], ids=["duplicate-reason-key", "duplicate-relation-key"])
def test_duplicate_json_keys_remain_invalid(raw, support_scope):
    with pytest.raises(ValueError, match="duplicate support JSON key"):
        parse(raw, support_scope)


def test_extra_fields_still_fail_even_with_normalizable_codes(support_scope):
    raw = reply(R5_CODES)
    raw["verification_verdict"] = "VERIFIED"
    before = deepcopy(raw)
    with pytest.raises(ValidationError, match="Extra inputs are not permitted"):
        parse(raw, support_scope)
    assert raw == before


@pytest.mark.parametrize("changes, message", [
    ({"supporting_refs": [EVIDENCE_REFS[0], EVIDENCE_REFS[0]]}, "supporting refs must be unique"),
    ({"relation": "CONTRADICTED", "supporting_refs": [], "contradicting_refs": [EVIDENCE_REFS[0], EVIDENCE_REFS[0]]}, "contradicting refs must be unique"),
    ({"supporting_refs": ["evidence-item://foreign"]}, "escape exact Evidence target set"),
    ({"relation": "INDETERMINATE", "contradicting_refs": [EVIDENCE_REFS[0]]}, "both supporting and contradicting"),
    ({"supporting_refs": []}, "positive support requires"),
    ({"relation": "NOT_SUPPORTED"}, "NOT_SUPPORTED cannot assert"),
    ({"relation": "CONTRADICTED", "supporting_refs": []}, "CONTRADICTED requires"),
    ({"relation": "PASS"}, None),
    ({"certainty_relation": "VERIFIED"}, None),
    ({"certainty_relation": None}, "certainty reason codes require"),
], ids=["duplicate-support", "duplicate-contradiction", "foreign-evidence", "overlap",
        "positive-empty", "unsupported-positive-refs", "contradicted-empty",
        "invalid-support-relation", "invalid-certainty-relation", "reasons-without-relation"])
def test_adjacent_provenance_and_semantic_constraints_still_fail(changes, message, support_scope):
    raw = reply()
    raw.update(changes)
    with pytest.raises(ValidationError, match=message):
        parse(raw, support_scope)


def test_evidence_order_is_canonicalized_without_deduplicating(support_scope):
    raw = reply()
    raw["supporting_refs"] = list(reversed(EVIDENCE_REFS))
    candidate, _ = parse(raw, support_scope)
    assert candidate.supporting_refs == EVIDENCE_REFS
    assert raw["supporting_refs"] == list(reversed(EVIDENCE_REFS))


def test_legacy_support_only_reply_stays_legacy_and_current_requires_certainty(support_scope):
    legacy = {"relation": "NOT_SUPPORTED", "supporting_refs": [], "contradicting_refs": []}
    candidate = TargetClaimSupportComposition.parse_reply(legacy, **support_scope)
    assert candidate.relation.value == "NOT_SUPPORTED"
    assert candidate.evidence_target_set_ref == support_scope["target"]
    assert "certainty_relation" not in legacy
    assert "certainty_reason_codes" not in legacy
    with pytest.raises(ValueError, match="lacks FTH-03 certainty observation"):
        parse(legacy, support_scope)


def test_already_canonical_reply_object_keeps_exact_values(support_scope):
    model = SupportModelReply.model_validate(reply())
    candidate, certainty = parse(model, support_scope)
    expected_candidate, expected_certainty = parse(reply(), support_scope)
    assert candidate == expected_candidate
    assert certainty == expected_certainty
    assert model.certainty_reason_codes == CANONICAL_CODES
