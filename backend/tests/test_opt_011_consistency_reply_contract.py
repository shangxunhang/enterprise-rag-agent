"""OPT-011 acceptance baseline for the existing Consistency reply parsers.

These independent wire examples protect the response acceptance domain while the
request instructions and execution identity change. No model call is involved.
"""

from copy import deepcopy
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


# Lexical order puts item-10 before item-2; these are deliberately not aliases
# assigned by the parser and are long enough to catch abbreviated-ID examples.
A = "canonical-assertion://sha256/item-10-" + "a" * 40
B = "canonical-assertion://sha256/item-2-" + "b" * 40
C = "canonical-assertion://sha256/item-3-" + "c" * 40
D = "canonical-assertion://sha256/item-4-" + "d" * 40
assert (A, B, C, D) == tuple(sorted((A, B, C, D)))


def _material(
    *,
    required=((A, B),),
    mode="SMALL_N_EXHAUSTIVE_MEMBERS",
    scope="SECTION",
    sections=None,
    budget=1,
    arity=3,
):
    exact_sections = sections or {ref: "section-a" for ref in (A, B, C, D)}
    return {
        "discovery_mode": mode,
        "validation_scope": scope,
        "required_relation_member_sets": [list(members) for members in required],
        "semantic_candidate_budget": budget,
        "assertions": [{"assertion_ref": ref} for ref in (A, B, C, D)],
        "assertion_context": {
            ref: {"section_id": exact_sections[ref]} for ref in (A, B, C, D)
        },
        "profile": {"composite_max_arity": arity},
    }


def _row(
    members=(A, B),
    *,
    materiality="MATERIAL_RELATION",
    relation="CONSISTENT",
    description="Same resource and reporting period",
    **changes,
):
    return {
        "member_assertion_refs": list(members),
        "relation_materiality": materiality,
        "relation": relation,
        "semantic_relation_description": description,
        "role_bindings": ["resource-quantity"],
        "scope_condition_bindings": ["same-reporting-period"],
        "reason_codes": ["EXACT_SCOPE"],
        "ambiguity_codes": [],
        "formal_predicate": None,
        "precision_tolerance": None,
        **changes,
    }


def _reply(*rows, complete=True):
    return {"discovery_complete": complete, "results": list(rows)}


def _parse(payload, material, route):
    if route == "ordinary":
        return TargetPostWriteConsistency._parse_reply(payload, material)
    work_ref = "consistency-logical-work://sha256/example"
    shared = {"work_results": [{"logical_work_ref": work_ref, **payload}]}
    shared_material = {"logical_works": [{"logical_work_ref": work_ref, "material": material}]}
    return TargetPostWriteConsistency._parse_shared_reply(shared, shared_material)[work_ref]


@pytest.mark.parametrize("route", ["ordinary", "shared"])
@pytest.mark.parametrize(
    ("materiality", "relation"),
    [
        ("MATERIAL_RELATION", "CONSISTENT"),
        ("NO_MATERIAL_RELATION", None),
        ("INDETERMINATE", None),
        ("MATERIAL_RELATION", "INDETERMINATE"),
    ],
)
def test_cons_03_and_10_independent_legal_relations(route, materiality, relation):
    row = _row(materiality=materiality, relation=relation)
    payload = _reply(row)
    material = _material()
    wire = json.dumps(payload)

    assert ConsistencyRelationReply.model_validate(payload).results[0].role_bindings == (
        "resource-quantity",
    )
    if route == "shared":
        ConsistencySharedReply.model_validate(
            {"work_results": [{"logical_work_ref": "example", **payload}]}
        )
    parsed = _parse(wire if route == "ordinary" else payload, material, route)
    assert len(parsed) == 1
    assert (parsed[0].relation_materiality, parsed[0].relation) == (
        materiality,
        relation,
    )


@pytest.mark.parametrize("route", ["ordinary", "shared"])
@pytest.mark.parametrize(
    ("fault", "value"),
    [
        ("role_bindings", [{"role": "x"}]),
        ("role_bindings", [7]),
        ("scope_condition_bindings", [{"scope": "x"}]),
        ("ambiguity_codes", [False]),
        ("unknown_reply_field", "must-not-be-dropped"),
    ],
)
def test_cons_04_invalid_types_and_unknown_fields_remain_rejected(route, fault, value):
    row = _row()
    row[fault] = value
    payload = _reply(row)
    raw_before = deepcopy(payload)
    material = _material()
    material_before = deepcopy(material)

    with pytest.raises(ValidationError):
        _parse(payload, material, route)
    assert payload == raw_before
    assert material == material_before


@pytest.mark.parametrize("route", ["ordinary", "shared"])
def test_cons_04_top_level_unknown_field_and_invalid_json_remain_rejected(route):
    payload = _reply(_row())
    payload["unknown_reply_field"] = "must-not-be-dropped"
    with pytest.raises(ValidationError):
        _parse(payload, _material(), route)
    if route == "ordinary":
        with pytest.raises(json.JSONDecodeError):
            _parse("{", _material(), route)
    else:
        with pytest.raises(json.JSONDecodeError):
            TargetPostWriteConsistency._parse_shared_reply("{", {"logical_works": []})


@pytest.mark.parametrize("route", ["ordinary", "shared"])
@pytest.mark.parametrize("fault", ["member_order", "duplicate_member", "foreign", "scope", "arity"])
def test_cons_05_member_domain_scope_and_current_arity_stay_strict(route, fault):
    material = _material()
    row = _row()
    rows = [row]
    if fault == "member_order":
        row["member_assertion_refs"] = [B, A]
    elif fault == "duplicate_member":
        row["member_assertion_refs"] = [A, A]
    elif fault == "foreign":
        material["discovery_mode"] = "LARGE_N_HIGH_RECALL"
        rows.append(_row((A, "foreign-assertion://sha256/1"), description="Foreign"))
    elif fault == "scope":
        material["validation_scope"] = "DOCUMENT"
    else:
        material["discovery_mode"] = "LARGE_N_HIGH_RECALL"
        material["profile"]["composite_max_arity"] = 2
        rows.append(_row((A, B, C), description="Composite relation"))

    with pytest.raises((ValidationError, ConsistencyCompositionError)):
        _parse(_reply(*rows), material, route)


@pytest.mark.parametrize("route", ["ordinary", "shared"])
@pytest.mark.parametrize(
    ("materiality", "relation", "members", "predicate"),
    [
        ("MATERIAL_RELATION", None, (A, B), None),
        ("NO_MATERIAL_RELATION", "CONSISTENT", (A, B), None),
        ("INDETERMINATE", "INDETERMINATE", (A, B), None),
        ("NO_MATERIAL_RELATION", None, (A, B), "EQ"),
        ("INDETERMINATE", None, (A, B), "EQ"),
        ("MATERIAL_RELATION", "CONSISTENT", (A, B, C), "EQ"),
    ],
)
def test_cons_06_cross_field_and_formal_restrictions_remain_strict(
    route, materiality, relation, members, predicate
):
    row = _row(members, materiality=materiality, relation=relation, formal_predicate=predicate)
    material = _material(required=(members,))
    with pytest.raises(ValidationError):
        _parse(_reply(row), material, route)


def test_cons_06_formal_direction_uses_canonical_lexical_member_order():
    row = ConsistencyRelationReplyRow.model_validate(
        _row(formal_predicate="LT", precision_tolerance=None)
    )
    candidate = TargetPostWriteConsistency._formal_candidate(row, {})
    assert candidate is not None
    assert (candidate.lhs_assertion_ref, candidate.rhs_assertion_ref) == (A, B)
    assert candidate.predicate == "LT"
    assert candidate.precision_tolerance is None


@pytest.mark.parametrize("route", ["ordinary", "shared"])
def test_cons_07_same_members_with_distinct_semantic_bindings_remain_accepted(route):
    first = _row(description="Same reporting period")
    second = _row(description="Same unit basis", role_bindings=["unit-comparability"])
    payload = _reply(first, second)
    assert len(ConsistencyRelationReply.model_validate(payload).results) == 2
    assert len(_parse(payload, _material(), route)) == 2
    with pytest.raises(ValidationError, match="repeats a semantic relation binding"):
        _parse(_reply(first, deepcopy(first)), _material(), route)


@pytest.mark.parametrize("route", ["ordinary", "shared"])
@pytest.mark.parametrize(
    "fault", ["small_missing", "small_extra", "large_missing", "large_excess", "incomplete"]
)
def test_cons_08_discovery_accounting_remains_strict(route, fault):
    material = _material()
    rows = [_row()]
    complete = True
    if fault == "small_missing":
        rows = []
    elif fault == "small_extra":
        rows.append(_row((A, C), description="Extra relation"))
    elif fault == "large_missing":
        material["discovery_mode"] = "LARGE_N_HIGH_RECALL"
        rows = [_row((A, C), description="Other relation")]
    elif fault == "large_excess":
        material["discovery_mode"] = "LARGE_N_HIGH_RECALL"
        rows.extend(
            [
                _row((A, C), description="First extra"),
                _row((A, D), description="Second extra"),
            ]
        )
    else:
        complete = False

    with pytest.raises(ConsistencyCompositionError):
        _parse(_reply(*rows, complete=complete), material, route)


def _shared_two_works():
    sections = {A: "section-a", B: "section-a", C: "section-a", D: "section-b"}
    section_material = _material(required=((A, B),), sections=sections)
    document_material = _material(
        required=((C, D),), scope="DOCUMENT", sections=sections, budget=2
    )
    refs = ("consistency-logical-work://sha256/a", "consistency-logical-work://sha256/b")
    material = {
        "logical_works": [
            {"logical_work_ref": refs[0], "material": section_material},
            {"logical_work_ref": refs[1], "material": document_material},
        ]
    }
    reply = {
        "work_results": [
            {"logical_work_ref": refs[0], **_reply(_row())},
            {"logical_work_ref": refs[1], **_reply(_row((C, D)))},
        ]
    }
    return material, reply, refs


def test_cons_09_shared_work_constraints_are_independently_applied():
    material, reply, refs = _shared_two_works()
    parsed = TargetPostWriteConsistency._parse_shared_reply(reply, material)
    assert tuple(parsed) == refs
    assert parsed[refs[0]][0].member_assertion_refs == (A, B)
    assert parsed[refs[1]][0].member_assertion_refs == (C, D)


@pytest.mark.parametrize("fault", ["missing", "extra", "duplicate", "out_of_order", "borrowed_scope"])
def test_cons_09_shared_missing_extra_duplicate_order_and_scope_stay_strict(fault):
    material, reply, refs = _shared_two_works()
    works = reply["work_results"]
    if fault == "missing":
        works.pop()
    elif fault == "extra":
        works.append({"logical_work_ref": "consistency-logical-work://sha256/c", **_reply()})
    elif fault == "duplicate":
        works.append(deepcopy(works[1]))
    elif fault == "out_of_order":
        works.reverse()
    else:
        works[0]["results"] = [_row((C, D))]
        works[1]["results"] = [_row((A, B))]
    with pytest.raises((ValidationError, ConsistencyCompositionError)):
        TargetPostWriteConsistency._parse_shared_reply(reply, material)


@pytest.mark.parametrize("route", ["ordinary", "shared"])
def test_cons_10_document_global_zero_candidate_complete_is_valid(route):
    material = _material(required=(), mode="DOCUMENT_GLOBAL_BOUNDED_DISCOVERY", scope="DOCUMENT_GLOBAL")
    assert _parse(_reply(), material, route) == ()
    with pytest.raises(ConsistencyCompositionError, match="terminal accounting"):
        _parse(_reply(complete=False), material, route)


@pytest.mark.parametrize("route", ["ordinary", "shared"])
def test_cons_11_reason_wire_order_only_is_normalized_without_mutating_input(route):
    payload = _reply(_row(reason_codes=["Z_REASON", "A_REASON"]))
    before = deepcopy(payload)
    parsed = _parse(payload, _material(), route)
    assert parsed[0].reason_codes == ("A_REASON", "Z_REASON")
    assert payload == before


@pytest.mark.parametrize("route", ["ordinary", "shared"])
@pytest.mark.parametrize("bad_codes", [["R", "R"], ["R", 7], [{"code": "R"}], "R"])
def test_cons_11_duplicate_or_malformed_reason_codes_remain_rejected(route, bad_codes):
    with pytest.raises(ValidationError):
        _parse(_reply(_row(reason_codes=bad_codes)), _material(), route)


@pytest.mark.parametrize("route", ["ordinary", "shared"])
@pytest.mark.parametrize(
    "field", ["member_assertion_refs", "scope_condition_bindings", "ambiguity_codes"]
)
def test_cons_11_other_ordered_arrays_are_not_normalized(route, field):
    row = _row()
    row[field] = [B, A] if field == "member_assertion_refs" else ["z", "a"]
    with pytest.raises(ValidationError, match="canonical ordered-unique"):
        _parse(_reply(row), _material(), route)


@pytest.mark.parametrize("route", ["ordinary", "shared"])
@pytest.mark.parametrize("roles", [[], ["role"], ["z", "a"], ["role2", "role10", "Role"], ["资源", "方法"]])
def test_cons_12_role_wire_order_preserves_semantics_and_inputs(route, roles):
    # CH1-TF-001: canonicalize set-like role labels, never formal operands.
    row = _row(role_bindings=roles, reason_codes=["Z_REASON", "A_REASON"], formal_predicate="LT")
    payload = _reply(row)
    material = _material()
    before, material_before = deepcopy(payload), deepcopy(material)
    normalized = TargetPostWriteConsistency._normalize_reason_order(payload)
    assert TargetPostWriteConsistency._normalize_reason_order(normalized) == normalized
    parsed = _parse(payload, material, route)
    expected = {**row, "role_bindings": sorted(roles), "reason_codes": ["A_REASON", "Z_REASON"]}
    assert parsed[0].model_dump(mode="json") == expected
    assert payload == before
    assert material == material_before
    candidate = TargetPostWriteConsistency._formal_candidate(parsed[0], {})
    assert (candidate.lhs_assertion_ref, candidate.rhs_assertion_ref, candidate.predicate) == (A, B, "LT")


@pytest.mark.parametrize("route", ["ordinary", "shared"])
@pytest.mark.parametrize("bad_roles", [["R", "R"], ["R", 7], ["R", False], [{"role": "R"}], "R", None])
def test_cons_12_duplicate_or_malformed_roles_remain_rejected(route, bad_roles):
    payload = _reply(_row(role_bindings=bad_roles))
    before = deepcopy(payload)
    with pytest.raises(ValidationError):
        _parse(payload, _material(), route)
    assert payload == before


@pytest.mark.parametrize("route", ["ordinary", "shared"])
def test_cons_12_normalization_does_not_hide_duplicate_semantic_bindings(route):
    first = _row(role_bindings=["a", "z"])
    second = _row(role_bindings=["z", "a"])
    payload = _reply(first, second)
    before = deepcopy(payload)
    with pytest.raises(ValidationError, match="repeats a semantic relation binding"):
        _parse(payload, _material(), route)
    assert payload == before


def test_cons_12_strict_schema_itself_still_rejects_unsorted_roles():
    with pytest.raises(ValidationError, match="role_bindings must be canonical ordered-unique"):
        ConsistencyRelationReply.model_validate(_reply(_row(role_bindings=["z", "a"])))
