"""H12: normalize two wire-order fields without relaxing canonical contracts."""
from copy import deepcopy
from itertools import permutations
import json

import pytest
from pydantic import ValidationError

from apps.enterprise_document.services.agent_rag_v1.post_write_consistency_composition import (
    ConsistencyCompositionError,
    ConsistencyRelationReply,
    ConsistencyRelationReplyRow,
    ConsistencySharedReply,
    TargetPostWriteConsistency,
)
from apps.enterprise_document.services.agent_rag_v1.post_write_requirement_adherence import (
    RequirementAdherenceCompositionError,
    RequirementAdherenceReply,
    RequirementAdherenceRow,
    TargetPostWriteRequirementAdherence,
)


# Exact code values/order observed in R10; no runtime records are imported here.
R10_REASON_CODES = ["SAME_POLICY_SCOPE", "ALIGNED_EVIDENCE_BOUNDARY"]
CANONICAL_REASON_CODES = sorted(R10_REASON_CODES)
WORK_REF = "consistency-work://section-a"
RESPONSE_REFS = ("artifact-unit://a", "artifact-unit://b", "artifact-unit://c")


def _consistency_material():
    return {
        "discovery_mode": "SMALL_N_EXHAUSTIVE_MEMBERS",
        "validation_scope": "SECTION",
        "required_relation_member_sets": [["assertion-a", "assertion-b"]],
        "semantic_candidate_budget": 2,
        "assertions": [{"assertion_ref": ref} for ref in ("assertion-a", "assertion-b")],
        "assertion_context": {
            ref: {"section_id": "section-a"} for ref in ("assertion-a", "assertion-b")
        },
        "profile": {"composite_max_arity": 3},
    }


def _consistency_payload(codes=None):
    return {
        "discovery_complete": True,
        "results": [{
            "member_assertion_refs": ["assertion-a", "assertion-b"],
            "relation_materiality": "MATERIAL_RELATION",
            "relation": "INCONSISTENT",
            "semantic_relation_description": "The same resource has incompatible quantities.",
            "role_bindings": ["resource-quantity"],
            "scope_condition_bindings": ["same-reporting-period"],
            "reason_codes": list(CANONICAL_REASON_CODES if codes is None else codes),
            "ambiguity_codes": [],
            "formal_predicate": None,
            "precision_tolerance": None,
        }],
    }


def _consistency_wire(payload, material, route):
    if route == "shared":
        return (
            {"work_results": [{"logical_work_ref": WORK_REF, **payload}]},
            {"logical_works": [{"logical_work_ref": WORK_REF, "material": material}]},
        )
    return payload, material


def _parse_consistency(raw, material, route):
    if route == "shared":
        return TargetPostWriteConsistency._parse_shared_reply(raw, material)
    return TargetPostWriteConsistency._parse_reply(raw, material)


def _assert_rejected(parser, raw, material, error, match):
    before_raw, before_material = deepcopy(raw), deepcopy(material)
    with pytest.raises(error, match=match):
        parser(raw, material)
    assert raw == before_raw
    assert material == before_material


@pytest.mark.parametrize("route", ["direct", "shared"])
@pytest.mark.parametrize("wire_format", ["dict", "json"])
def test_consistency_reason_order_preserves_semantics_and_input(route, wire_format):
    for codes in (R10_REASON_CODES, CANONICAL_REASON_CODES, [], ["ONLY_REASON"]):
        payload, material = _consistency_wire(
            _consistency_payload(codes), _consistency_material(), route
        )
        raw = json.dumps(payload) if wire_format == "json" else payload
        before_raw, before_material = deepcopy(raw), deepcopy(material)
        expected_row = _consistency_payload(sorted(codes))["results"][0]
        expected = (ConsistencyRelationReplyRow.model_validate(expected_row),)

        parsed = _parse_consistency(raw, material, route)

        assert parsed == ({WORK_REF: expected} if route == "shared" else expected)
        assert raw == before_raw
        assert material == before_material


@pytest.mark.parametrize("route", ["direct", "shared"])
@pytest.mark.parametrize("fault", [
    "duplicate_reasons", "nonstring_reason", "nonsequence_reasons",
    "foreign_assertion", "nonmaterial_relation", "other_field_order",
])
def test_consistency_reply_still_rejects_invalid_values(route, fault):
    # Other ordered fields must not be normalized as a side effect of H12.
    other_fields = (
        "member_assertion_refs", "role_bindings",
        "scope_condition_bindings", "ambiguity_codes",
    ) if fault == "other_field_order" else (None,)
    for field in other_fields:
        payload, material = _consistency_payload(), _consistency_material()
        row = payload["results"][0]
        error, match = ValidationError, "reason_codes"
        if fault == "duplicate_reasons":
            row["reason_codes"] = ["REPEATED", "REPEATED"]
        elif fault == "nonstring_reason":
            row["reason_codes"] = ["VALID", 7]
        elif fault == "nonsequence_reasons":
            row["reason_codes"] = "NOT_AN_ARRAY"
        elif fault == "foreign_assertion":
            material["discovery_mode"] = "LARGE_N_HIGH_RECALL"
            payload["results"].append({
                **deepcopy(row),
                "member_assertion_refs": ["assertion-a", "foreign-assertion"],
            })
            error, match = ConsistencyCompositionError, "escapes exact assertion domain"
        elif fault == "nonmaterial_relation":
            row["relation_materiality"] = "NO_MATERIAL_RELATION"
            match = "cannot fabricate a consistency relation"
        else:
            row[field] = (["assertion-b", "assertion-a"]
                          if field == "member_assertion_refs" else ["z", "a"])
            match = "canonical ordered-unique"
        raw, exact_material = _consistency_wire(payload, material, route)
        _assert_rejected(
            lambda value, context: _parse_consistency(value, context, route),
            raw, exact_material, error, match,
        )


def _requirement_material():
    return {
        "requirements": [{"requirement_id": "requirement-a"}, {"requirement_id": "requirement-b"}],
        "artifact_units": [{"unit_ref": ref} for ref in RESPONSE_REFS],
    }


def _requirement_payload(refs=RESPONSE_REFS):
    return {"results": [
        {"requirement_id": "requirement-b", "observation": "VIOLATED",
         "response_refs": list(refs), "reason_codes": ["REQUIREMENT_CONFLICT"]},
        {"requirement_id": "requirement-a", "observation": "INDETERMINATE",
         "response_refs": [], "reason_codes": ["NO_DEFINITE_ASSESSMENT"]},
    ]}


@pytest.mark.parametrize("wire_format", ["dict", "json"])
def test_requirement_ref_permutations_preserve_observations_and_input(wire_format):
    expected = tuple(RequirementAdherenceRow.model_validate(row) for row in sorted(
        _requirement_payload()["results"], key=lambda row: row["requirement_id"]
    ))
    for refs in permutations(RESPONSE_REFS):
        payload, material = _requirement_payload(refs), _requirement_material()
        raw = json.dumps(payload) if wire_format == "json" else payload
        before_raw, before_material = deepcopy(raw), deepcopy(material)

        parsed = TargetPostWriteRequirementAdherence.parse_reply(raw, material)

        assert parsed == expected
        assert raw == before_raw
        assert material == before_material


@pytest.mark.parametrize("fault", [
    "duplicate_refs", "nonstring_ref", "nonsequence_refs",
    "foreign_ref", "invalid_observation", "reason_code_order",
])
def test_requirement_reply_still_rejects_invalid_values(fault):
    raw, material = _requirement_payload(), _requirement_material()
    row = raw["results"][0]
    error, match = ValidationError, "response_refs"
    if fault == "duplicate_refs":
        row["response_refs"] = [RESPONSE_REFS[0], RESPONSE_REFS[0]]
    elif fault == "nonstring_ref":
        row["response_refs"] = [RESPONSE_REFS[0], 7]
    elif fault == "nonsequence_refs":
        row["response_refs"] = RESPONSE_REFS[0]
    elif fault == "foreign_ref":
        row["response_refs"] = ["foreign-artifact-unit"]
        error = RequirementAdherenceCompositionError
        match = "escape exact Artifact domain"
    elif fault == "invalid_observation":
        row["observation"] = "REWRITE_AS_PASS"
        match = "observation"
    else:
        row["reason_codes"] = ["Z_REASON", "A_REASON"]
        match = "reason_codes"
    _assert_rejected(
        TargetPostWriteRequirementAdherence.parse_reply, raw, material, error, match
    )


@pytest.mark.parametrize("contract", ["consistency", "shared_consistency", "requirement"])
def test_canonical_reply_models_remain_order_strict(contract):
    if contract == "requirement":
        model = RequirementAdherenceReply
        raw = _requirement_payload(tuple(reversed(RESPONSE_REFS)))
        field = "response_refs"
    else:
        raw = _consistency_payload(R10_REASON_CODES)
        model, field = ConsistencyRelationReply, "reason_codes"
        if contract == "shared_consistency":
            raw = {"work_results": [{"logical_work_ref": WORK_REF, **raw}]}
            model = ConsistencySharedReply
    before = deepcopy(raw)
    with pytest.raises(ValidationError, match=field):
        model.model_validate(raw)
    assert raw == before


def test_shared_logical_accounting_and_order_are_not_normalized():
    for fault in ("missing_work", "duplicate_work", "unsorted_work"):
        raw, material = _consistency_wire(
            _consistency_payload(), _consistency_material(), "shared"
        )
        error, match = ValidationError, "canonical ordered-unique"
        if fault == "missing_work":
            raw["work_results"] = []
            error, match = ConsistencyCompositionError, "exactly account every logical consumer"
        elif fault == "duplicate_work":
            raw["work_results"] *= 2
        else:
            second = deepcopy(raw["work_results"][0])
            second["logical_work_ref"] = "consistency-work://section-z"
            raw["work_results"].insert(0, second)
            material["logical_works"].append({
                "logical_work_ref": second["logical_work_ref"],
                "material": _consistency_material(),
            })
        _assert_rejected(
            TargetPostWriteConsistency._parse_shared_reply, raw, material, error, match
        )


def test_consistency_discovery_and_scope_checks_remain_strict():
    for route in ("direct", "shared"):
        for fault in ("incomplete", "missing_required", "foreign_scope"):
            payload, material = _consistency_payload(), _consistency_material()
            if fault == "incomplete":
                payload["discovery_complete"] = False
                match = "terminal accounting"
            elif fault == "missing_required":
                payload["results"] = []
                match = "omitted or added relation members"
            else:
                material["assertion_context"]["assertion-b"]["section_id"] = "section-b"
                match = "escapes exact scope or composite bound"
            raw, exact_material = _consistency_wire(payload, material, route)
            _assert_rejected(
                lambda value, context: _parse_consistency(value, context, route),
                raw, exact_material, ConsistencyCompositionError, match,
            )


def test_requirement_exact_accounting_remains_strict():
    for fault in ("missing_requirement", "foreign_requirement", "duplicate_requirement"):
        raw, material = _requirement_payload(), _requirement_material()
        error, match = RequirementAdherenceCompositionError, "omitted or added requirement"
        if fault == "missing_requirement":
            raw["results"].pop()
        elif fault == "foreign_requirement":
            raw["results"][0]["requirement_id"] = "foreign-requirement"
        else:
            raw["results"][0]["requirement_id"] = raw["results"][1]["requirement_id"]
            error, match = ValidationError, "duplicate requirement IDs"
        _assert_rejected(
            TargetPostWriteRequirementAdherence.parse_reply, raw, material, error, match
        )
