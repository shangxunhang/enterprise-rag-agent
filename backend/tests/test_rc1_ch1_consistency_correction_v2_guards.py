"""Offline identity, instruction-precondition and redispatch guards.

The synthetic materials exercise guards only; they are not native receipt or
semantic acceptance evidence. Historical expectations come from the bounded
native inventory persisted in this repository, without reading an E: runtime.
"""

from copy import deepcopy
import json
from pathlib import Path
from threading import RLock
from types import SimpleNamespace

import pytest
from pydantic import ValidationError

from apps.enterprise_document.services.agent_rag_v1 import (
    post_write_consistency_composition as c,
)
from core.hashing import canonical_sha256
from test_opt_011_consistency_instruction_identity import _synthetic_material


V1 = "consistency-protocol-correction-v1"
V2 = "consistency-protocol-correction-v2"
OBSERVED = (
    "b112b30738717aa693d6f7af6bcac084c0196f9b6295ebb378d2ab406fb57f2d",
    "4fc31a39485215627aad09c0ffed4c3258ce04d58b489e40bc0ebf3b700dd0af",
    "b362ebe73020eb934885e094b44f528b72eaf94a1693244f40ed12064c3d8772",
    "8f160b70182b17eccbb2745913cc91c86b85beca5fdd85e2f69ddcc3abb37b0b",
    "dcfc9dc0a443df337faa2f8094cb5117357bb28b9a5c76eb5392c921522cb55b",
)
INVENTORY = (
    Path(__file__).resolve().parents[2]
    / "docs/rc1_ch1_consistency_correction_v2_implementation_20261019_r01"
    / "12_PROFILE_INVENTORY_READONLY.json"
)


@pytest.fixture(scope="module")
def observed_profiles():
    inventory = json.loads(INVENTORY.read_text(encoding="utf-8"))
    assert set(inventory["profiles"]) == set(OBSERVED)
    assert inventory["acceptance_summary"]["receipt_replay"] == "NOT_PERFORMED"
    records = inventory["native_input_records"]
    assert len(records) == 55
    for digest in OBSERVED:
        profile = inventory["profiles"][digest]["profile"]
        native = [record for record in records if record["profile_hash"] == digest]
        assert native and all(record["reference_content_hash_matches"] for record in native)
        assert canonical_sha256({key: value for key, value in profile.items()
                                 if key not in {"profile_hash", "config_ref"}}) == digest
    return {digest: value["profile"] for digest, value in inventory["profiles"].items()}


@pytest.mark.parametrize("digest", OBSERVED, ids=[value[:8] for value in OBSERVED])
def test_previous_profiles_exactly_match_observed_native_static_identity(observed_profiles, digest):
    actual = {profile["profile_hash"]: profile for profile in c._previous_instruction_profiles()}
    assert set(actual) == set(OBSERVED)
    assert actual[digest] == observed_profiles[digest]
    assert actual[digest]["config_ref"] == "consistency-instruction-profile://sha256/" + digest


def test_v1_literal_identity_and_v2_policy_are_separate(observed_profiles):
    literal = (
        "Regenerate once under the unchanged Source Universe and controls. Feedback is protocol data, "
        "not business facts or Evidence. Do not clean, truncate, mechanically split, or delete relations "
        "to manufacture PASS; perform bounded discovery and report completion truthfully."
    )
    assert c._PROTOCOL_CORRECTION_INSTRUCTION == literal
    assert canonical_sha256(literal) == "b77992271346a97273ab24376e608cffc63dba9c91672c9615c70531bc2369e3"
    v1 = c._instruction_profile(correction_policy_version=V1)
    assert v1 == observed_profiles[OBSERVED[0]]
    assert c._instruction_profile(label_canonicalization=False, correction_policy_version=V1) == observed_profiles[OBSERVED[1]]
    v2 = c._instruction_profile()
    assert v2 == c._instruction_profile(correction_policy_version=V2)
    assert v2["schema_version"] == v1["schema_version"]
    assert v2["protocol_correction_policy_version"] == V2
    assert v2["protocol_correction_instruction_sha256"] == canonical_sha256(c._PROTOCOL_CORRECTION_INSTRUCTION_V2)
    assert v2["protocol_correction_instruction_sha256"] != v1["protocol_correction_instruction_sha256"]
    assert v2["profile_hash"] not in OBSERVED
    assert v2["config_ref"] == "consistency-instruction-profile://sha256/" + v2["profile_hash"]
    assert {key: value for key, value in v1.items() if key not in {
        "protocol_correction_policy_version", "protocol_correction_instruction_sha256", "profile_hash", "config_ref"
    }} == {key: value for key, value in v2.items() if key not in {
        "protocol_correction_policy_version", "protocol_correction_instruction_sha256", "profile_hash", "config_ref"
    }}
    assert c._correction_instruction(V1) == literal
    assert c._correction_instruction(V2) == c._PROTOCOL_CORRECTION_INSTRUCTION_V2
    with pytest.raises(c.ConsistencyCompositionError, match="unknown.*policy"):
        c._instruction_profile(correction_policy_version="consistency-protocol-correction-unknown")


@pytest.mark.parametrize("labels,digest", [
    (False, OBSERVED[2]),
    (True, "e9e9010305691063d3c2290f02ceb47517288eed7d144ccbb28a5700209bc72c"),
])
def test_correction_off_identity_is_unchanged_by_default_policy_upgrade(labels, digest):
    old = c._instruction_profile(protocol_correction=False, label_canonicalization=labels,
                                 correction_policy_version=V1)
    new = c._instruction_profile(protocol_correction=False, label_canonicalization=labels,
                                 correction_policy_version=V2)
    assert old == new
    assert new["profile_hash"] == digest
    assert "protocol_correction_policy_version" not in new
    assert "protocol_correction_instruction_sha256" not in new


def _materials(shared, *, policy=V2):
    first = _synthetic_material(required=("example-assertion:10", "example-assertion:2"), profiled=True)
    first["instruction_profile"] = c._instruction_profile(correction_policy_version=policy)
    second = deepcopy(first)
    second["target_ref"] = "independent-consumer-2"
    return (first, second) if shared else (first,)


def _top(materials, shared):
    return c.TargetPostWriteConsistency._shared_material(materials) if shared else materials[0]


def _bad_order_payload(top, shared):
    def reply(material):
        return {"discovery_complete": True, "results": [{
            "member_assertion_refs": list(reversed(material["required_relation_member_sets"][0])),
            "relation_materiality": "NO_MATERIAL_RELATION", "relation": None,
            "semantic_relation_description": "Explicit controlled relation for guard testing only.",
        }]}
    if shared:
        return {"work_results": [{"logical_work_ref": work["logical_work_ref"], **reply(work["material"])}
                                 for work in top["logical_works"]]}
    return reply(top)


def _replies(payload, shared):
    return payload["work_results"] if shared else [payload]


@pytest.mark.parametrize("shared", [False, True], ids=["ordinary", "shared"])
def test_pure_member_order_precondition_never_repairs_or_accepts_reply(shared):
    materials = _materials(shared)
    top = _top(materials, shared)
    payload = _bad_order_payload(top, shared)
    before = deepcopy((materials, top, payload))
    raw = json.dumps(payload)
    diagnostic = c.TargetPostWriteConsistency._protocol_feedback(raw, top)
    assert diagnostic["errors"] and {error["code"] for error in diagnostic["errors"]} == {"MEMBER_ORDER_OR_UNIQUENESS"}
    assert c.TargetPostWriteConsistency._member_order_only(raw, top, diagnostic) is True
    parser = c.TargetPostWriteConsistency._parse_shared_reply if shared else c.TargetPostWriteConsistency._parse_reply
    with pytest.raises((ValidationError, c.ConsistencyCompositionError)):
        parser(raw, top)
    assert (materials, top, payload) == before
    assert raw == json.dumps(payload)


HIDDEN_ERRORS = (
    "incomplete", "duplicate_binding", "coerced_duplicate_binding", "material_without_relation", "nonmaterial_with_relation",
    "nonmaterial_with_predicate", "ternary_predicate", "duplicate_member", "unknown_top_field",
    "unknown_row_field", "blank_description", "foreign_member", "ineligible_scope",
    "arity", "extra_small_n_group", "missing_required", "over_budget",
)


def _add_hidden_error(case, payload, top, shared):
    replies = _replies(payload, shared)
    reply = replies[0]
    row = reply["results"][0]
    current = top["logical_works"][0]["material"] if shared else top
    if case == "incomplete":
        reply["discovery_complete"] = False
    elif case == "duplicate_binding":
        reply["results"].append(deepcopy(row))
    elif case == "coerced_duplicate_binding":
        row["precision_tolerance"] = 1.0
        duplicate = deepcopy(row)
        duplicate["precision_tolerance"] = "1.0"
        reply["results"].append(duplicate)
    elif case == "material_without_relation":
        row["relation_materiality"] = "MATERIAL_RELATION"
    elif case == "nonmaterial_with_relation":
        row["relation"] = "CONSISTENT"
    elif case == "nonmaterial_with_predicate":
        row["formal_predicate"] = "EQ"
    elif case == "ternary_predicate":
        row["member_assertion_refs"].append("example-assertion:3")
        row.update(relation_materiality="MATERIAL_RELATION", relation="CONSISTENT", formal_predicate="EQ")
        current["required_relation_member_sets"] = [["example-assertion:10", "example-assertion:2", "example-assertion:3"]]
    elif case == "duplicate_member":
        row["member_assertion_refs"].append(row["member_assertion_refs"][0])
    elif case == "unknown_top_field":
        reply["unexpected"] = "must remain rejected"
    elif case == "unknown_row_field":
        row["unexpected"] = "must remain rejected"
    elif case == "blank_description":
        row["semantic_relation_description"] = ""
    elif case == "foreign_member":
        row["member_assertion_refs"][0] = "outside-source:assertion"
    elif case == "ineligible_scope":
        current["validation_scope"] = "DOCUMENT"
    elif case == "arity":
        current["profile"]["composite_max_arity"] = 1
    elif case == "extra_small_n_group":
        extra = deepcopy(row)
        extra["member_assertion_refs"] = ["example-assertion:3", "example-assertion:10"]
        reply["results"].append(extra)
    elif case == "missing_required":
        current["required_relation_member_sets"].append(["example-assertion:10", "example-assertion:3"])
    elif case == "over_budget":
        current["discovery_mode"] = "LARGE_N_BOUNDED_DISCOVERY"
        current["required_relation_member_sets"] = []
        current["semantic_candidate_budget"] = 0
    else:
        raise AssertionError(case)


@pytest.mark.parametrize("shared", [False, True], ids=["ordinary", "shared"])
@pytest.mark.parametrize("case", HIDDEN_ERRORS)
def test_member_order_guidance_requires_all_other_accounting_clean(shared, case):
    top = _top(_materials(shared), shared)
    payload = _bad_order_payload(top, shared)
    _add_hidden_error(case, payload, top, shared)
    before = deepcopy((payload, top))
    raw = json.dumps(payload)
    diagnostic = c.TargetPostWriteConsistency._protocol_feedback(raw, top)
    assert c.TargetPostWriteConsistency._member_order_only(raw, top, diagnostic) is False
    assert any(error["code"] != "MEMBER_ORDER_OR_UNIQUENESS" for error in diagnostic["errors"])
    assert (payload, top) == before


@pytest.mark.parametrize("shared", [False, True], ids=["ordinary", "shared"])
@pytest.mark.parametrize("case", ["incomplete", "duplicate_binding", "material_without_relation",
                                  "nonmaterial_with_relation", "nonmaterial_with_predicate"])
def test_v1_diagnostic_is_preserved_when_v2_exposes_hidden_other_error(shared, case):
    old_top = _top(_materials(shared, policy=V1), shared)
    old_payload = _bad_order_payload(old_top, shared)
    _add_hidden_error(case, old_payload, old_top, shared)
    before = deepcopy((old_payload, old_top))
    diagnostic = c.TargetPostWriteConsistency._protocol_feedback(json.dumps(old_payload), old_top)
    assert {error["code"] for error in diagnostic["errors"]} == {"MEMBER_ORDER_OR_UNIQUENESS"}
    assert c.TargetPostWriteConsistency._member_order_only(json.dumps(old_payload), old_top, diagnostic) is False
    assert (old_payload, old_top) == before


@pytest.mark.parametrize("shared", [False, True], ids=["ordinary", "shared"])
@pytest.mark.parametrize("bad_count", ["unmeasurable", "missing_required", "boolean_candidate_count",
                                      "candidate_count_mismatch", "budget_mismatch", "arity_mismatch"])
def test_untrusted_or_unmeasurable_diagnostic_counts_disable_order_only(shared, bad_count):
    top = _top(_materials(shared), shared)
    raw = json.dumps(_bad_order_payload(top, shared))
    diagnostic = c.TargetPostWriteConsistency._protocol_feedback(raw, top)
    if bad_count == "unmeasurable":
        diagnostic["work_counts"] = "UNMEASURABLE"
    else:
        first = diagnostic["work_counts"][0]
        if bad_count == "missing_required":
            first["missing_required_sets"] = [["missing-a", "missing-b"]]
        elif bad_count == "boolean_candidate_count":
            first["candidate_count"] = False
        elif bad_count == "candidate_count_mismatch":
            first["candidate_count"] += 1
        elif bad_count == "budget_mismatch":
            first["semantic_candidate_budget"] += 1
        elif bad_count == "arity_mismatch":
            first["composite_max_arity"] += 1
    before = deepcopy((top, diagnostic))
    assert c.TargetPostWriteConsistency._member_order_only(raw, top, diagnostic) is False
    assert (top, diagnostic) == before


@pytest.mark.parametrize("shape", ["missing", "duplicate", "reversed", "foreign"])
def test_shared_order_guidance_requires_exact_independent_work_accounting(shape):
    top = _top(_materials(True), True)
    payload = _bad_order_payload(top, True)
    if shape == "missing":
        payload["work_results"].pop()
    elif shape == "duplicate":
        payload["work_results"].append(deepcopy(payload["work_results"][0]))
    elif shape == "reversed":
        payload["work_results"].reverse()
    else:
        payload["work_results"][0]["logical_work_ref"] = "outside-logical-work"
    before = deepcopy((top, payload))
    raw = json.dumps(payload)
    diagnostic = c.TargetPostWriteConsistency._protocol_feedback(raw, top)
    assert c.TargetPostWriteConsistency._member_order_only(raw, top, diagnostic) is False
    assert any(error["code"] != "MEMBER_ORDER_OR_UNIQUENESS" for error in diagnostic["errors"])
    assert (top, payload) == before


class _DeniedGateway:
    def __init__(self):
        self.calls = 0

    def generate(self, *_args, **_kwargs):
        self.calls += 1
        raise AssertionError("historical work must not dispatch")


class _DeniedWriteStore:
    def __init__(self, root, records):
        self.root, self.records = root, deepcopy(records)
        self.writes = 0

    def get_json(self, reference, **_identity):
        if reference not in self.records:
            raise FileNotFoundError(reference)
        return deepcopy(self.records[reference])

    def put_json(self, *_args, **_kwargs):
        self.writes += 1
        raise AssertionError("historical work must not create an Artifact")


@pytest.mark.parametrize("shared", [False, True], ids=["ordinary", "shared"])
@pytest.mark.parametrize("saved_part", ["input", "result"], ids=["intent-only", "result-only"])
@pytest.mark.parametrize("digest", OBSERVED, ids=[value[:8] for value in OBSERVED])
def test_all_observed_histories_block_new_v2_dispatch_without_artifact_write(
    tmp_path, observed_profiles, shared, saved_part, digest
):
    materials = _materials(shared)
    old_materials = tuple({**deepcopy(material), "instruction_profile": deepcopy(observed_profiles[digest])}
                          for material in materials)
    old_top = _top(old_materials, shared)
    prefix = "artifact://artifact-consistency-shared/" if shared else "artifact://artifact-consistency/"
    old_key = canonical_sha256(old_top)
    saved_ref = prefix + old_key + "/" + saved_part
    records = {saved_ref: {"input_hash": old_key, "input_material": old_top}}
    store = _DeniedWriteStore(tmp_path, records)
    gateway = _DeniedGateway()
    composer = object.__new__(c.TargetPostWriteConsistency)
    composer.store, composer.gateway, composer._lock = store, gateway, RLock()
    composer.post_write = SimpleNamespace()
    validations = []
    before = deepcopy((store.records, materials))
    evaluate = composer._evaluate_shared if shared else composer._evaluate
    with pytest.raises(c.ConsistencyCompositionError, match="historical.*cannot redispatch"):
        evaluate(materials if shared else materials[0], {}, None, lambda: validations.append("current"))
    assert gateway.calls == 0 and store.writes == 0 and validations == []
    assert (store.records, materials) == before


class _StopBeforeAccountingVerification(RuntimeError):
    """Routing observation only: do not substitute for real receipt replay."""


@pytest.mark.parametrize("digest", OBSERVED, ids=[value[:8] for value in OBSERVED])
def test_outer_committed_record_selects_observed_profile_before_accounting(
    tmp_path, monkeypatch, observed_profiles, digest
):
    """Exercise complete_item's actual history loop with synthetic dependencies.

    The sentinel stops on entry to _verify_accounting. This proves profile
    selection and no redispatch through that point, not accounting validity,
    native historical receipt replay, or successful Completion creation.
    """
    profile = deepcopy(observed_profiles[digest])
    artifact_payload = {"artifact_hash": "b" * 64}
    artifact = SimpleNamespace(artifact_hash=artifact_payload["artifact_hash"],
                               model_dump=lambda **_kwargs: deepcopy(artifact_payload))
    source_payload = {"synthetic_guard_snapshot": "outer-routing-only"}
    snapshot = SimpleNamespace(model_dump=lambda **_kwargs: deepcopy(source_payload))
    refs = ("example-assertion:10", "example-assertion:2")
    assertions = []
    claims, sections = {}, {}
    for index, ref in enumerate(refs):
        claim = "guard-claim-" + str(index)
        projection = {"assertion_ref": ref, "source_unit_ref": claim}
        assertions.append(SimpleNamespace(
            assertion_ref=ref, source_unit_ref=claim,
            model_dump=lambda projection=projection, **_kwargs: deepcopy(projection),
        ))
        claims[claim] = SimpleNamespace(claim_type="synthetic-guard-only")
        sections[claim] = "guard-section"

    builder_calls = []

    def envelope_for(config_refs):
        # Finite synthetic envelope identity; production envelope validation is
        # deliberately outside this isolated routing test.
        return SimpleNamespace(input_hash=canonical_sha256({"synthetic_execution_config_refs": list(config_refs)}))

    class RoutingEnvelopeBuilder:
        def __init__(self, **_kwargs):
            pass

        def build(self, **kwargs):
            config_refs = tuple(kwargs.get("execution_config_refs", ()))
            builder_calls.append(config_refs)
            return envelope_for(config_refs)

    monkeypatch.setattr(c, "ValidatorInputEnvelopeBuilder", RoutingEnvelopeBuilder)
    monkeypatch.setattr(c, "ConsistencyAssertionMaterializer", lambda _runtime: SimpleNamespace(
        materialize=lambda **_kwargs: tuple(assertions)
    ))
    item_ref = "consistency:item:" + artifact.artifact_hash
    old_envelope = envelope_for((profile["config_ref"],))
    old_record = SimpleNamespace(
        validator_version=c.TargetPostWriteConsistency.VERSION,
        intermediate_refs=("unused-1", "unused-2", "unused-3", "historical-accounting-marker"),
        input_hash=old_envelope.input_hash,
        validator_execution_ref="validator-execution:consistency-mainline:" + canonical_sha256({
            "input_hash": old_envelope.input_hash, "item_ref": item_ref,
        }),
    )
    accounting_marker = object()
    resolved = []

    def resolve_typed(reference, kind):
        assert reference == "historical-accounting-marker" and kind is c.ConsistencyMainlineAccounting
        resolved.append(reference)
        return accounting_marker

    composer = object.__new__(c.TargetPostWriteConsistency)
    composer.profile = SimpleNamespace(
        small_n_binary_member_limit=3, composite_candidate_budget=1,
        model_dump=lambda **_kwargs: {"composite_max_arity": 3},
    )
    composer.post_write = SimpleNamespace(
        validation_view_resolver=object(),
        validation_intermediate_runtime=SimpleNamespace(resolve_typed=resolve_typed),
        validation_store=SimpleNamespace(find_validator_execution_for_item=lambda **_kwargs: old_record),
    )
    composer.store = _DeniedWriteStore(tmp_path, {})
    composer.gateway = _DeniedGateway()
    composer._source_material = lambda **_kwargs: (snapshot, (), (), claims, sections)
    composer._identity = lambda _source_sections: ({"synthetic_identity": "routing-only"}, None)
    selected_materials = []

    def stop_before_verification(accounting, material, identity, **kwargs):
        assert accounting is accounting_marker
        assert identity == {"synthetic_identity": "routing-only"}
        assert kwargs == {"quality_contract_ref": "guard-quality-contract"}
        selected_materials.append(deepcopy(material))
        raise _StopBeforeAccountingVerification()

    composer._verify_accounting = stop_before_verification
    before = deepcopy((old_record.__dict__, profile, source_payload, artifact_payload))
    with pytest.raises(_StopBeforeAccountingVerification):
        composer.complete_item(
            validation_run_id="guard-validation-run", subject_hash="a" * 64,
            artifact_ref=artifact, scope=SimpleNamespace(value="SECTION"),
            target_ref="guard-target", quality_contract_ref="guard-quality-contract",
            source_sections=(),
        )
    assert len(selected_materials) == 1
    assert selected_materials[0]["instruction_profile"] == profile
    assert builder_calls[0] == (c._instruction_profile()["config_ref"],)
    assert builder_calls[-1] == (profile["config_ref"],)
    assert resolved == ["historical-accounting-marker"]
    assert composer.gateway.calls == 0 and composer.store.writes == 0
    assert (old_record.__dict__, profile, source_payload, artifact_payload) == before
