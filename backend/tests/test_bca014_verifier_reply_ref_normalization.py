"""Offline external ref-order normalization, strict guards and frozen history.

The optional real-wire hook reads exact frozen a3 files in runtime storage.
Business/source text is never embedded in this portable test module.
"""
import base64
from copy import deepcopy
import hashlib
from itertools import permutations, product
import json
import os
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from apps.enterprise_document.schemas.agent_rag_v1.claim_support import SupportRelation
from apps.enterprise_document.schemas.agent_rag_v1.verification import (
    ClaimBindingFactSchema, ClaimBindingRelation, VerificationVerdict,
    VerifierClaimInputSchema, build_verifier_semantic_input_sha256,
    VERIFIER_LOCAL_REGISTRATION_KEY, VERIFIER_LOCAL_CORRECTION_KEY,
)
from apps.enterprise_document.services.agent_rag_v1 import claim_verifier_composition as composition
from apps.enterprise_document.services.agent_rag_v1.claim_verification_runtime import (
    ClaimBindingError, ClaimCurrentnessError, ClaimVerificationRuntime,
)
from backend.tests.test_bca014_i5_verifier_project_input_basis import Gateway, model_material
from backend.tests.test_bca014_i5_verifier_v2_semantics import enriched_binding
from backend.tests.test_bca014_verifier_prompt_provenance_clarification import (
    _two_fact_fixture, fixture,
)
from core.hashing import canonical_sha256
from schemas.model import ModelResponseSchema


Composer = composition.TargetClaimVerifierComposition
ENTRY_KEY = "verifier_reply_ref_order_v1"
PERMUTATIONS = tuple(permutations(range(3)))
A3_DIR = Path(r"D:\MyCode\_POST_BCA014_REAL_BUSINESS_E2E_20261003_161008_c0090159\attempt3\diagnosis_attempt3_readonly\1442119ff72f583617ab6ba1e72f4bd14b53ce6ca1736dc6d6a785b43b2f88a2")
A3_INPUT_SHA256 = "32bf4871d65f9e3e6ecbd8047c70c1dad9adb8fc6fc6ab20e5f9edbaea22c1ac"
A3_RESPONSE_SHA256 = "a4f14f03624bc30143e3dafa3faac07e178bfae35f98646d9c8b3986c12a2525"
A3_CONTENT_SHA256 = "a71bf8c45cfac015f3ac5439ae671c02ee5604053d8b8e185b182be6fea1fa51"


def _reply(fx):
    pi = fx.invocation.verifier_project_input_basis.project_input_basis_ref
    facts = sorted(s.exact_source_ref for s in fx.binding.exact_authorized_source_refs if s.source_kind == "FACT")
    return {"verdict": "UNSUPPORTED", "positive_basis_refs": sorted([*facts, pi]),
        "rationale": "Preserve this exact rationale; admission is not a verdict.", "issues": [
            {"issue_code": "FIRST", "message": "First exact explanation.", "severity": "MAJOR",
             "source_refs": sorted([facts[0], pi, "fact_snapshot:i5"])},
            {"issue_code": "SECOND", "message": "Second exact explanation.", "severity": "MINOR",
             "source_refs": sorted([facts[1], "evidence:C1", "evidence_package:i5"])}]}


def _parse(fx, raw, *, invocation=None):
    return Composer.parse_candidate(raw, invocation=invocation or fx.invocation,
        occurrence="offline-ref-order://exact-occurrence", receipt_ref="artifact://offline-ref-order")


def _rebuild(invocation, *, omit_entry=False, omit_cap=False, omit_prompt=False, entry=None):
    data = invocation.model_dump(mode="json")
    facts = data["deterministic_validation_facts"]
    if omit_entry:
        facts.pop(ENTRY_KEY, None)
    elif entry is not None:
        facts[ENTRY_KEY] = entry
    if omit_cap:
        facts.pop("verifier_output_cap_v1", None)
    if omit_prompt:
        facts.pop("verifier_prompt_profile_sha256", None)
    data["verifier_semantic_input_sha256"] = build_verifier_semantic_input_sha256(data)
    return VerifierClaimInputSchema.model_validate(data)


@pytest.fixture(scope="module")
def ref_owner(tmp_path_factory):
    return _two_fact_fixture(tmp_path_factory.mktemp("ref-order-owner"))


@pytest.mark.parametrize("positive_order,first_order,second_order", tuple(product(PERMUTATIONS, repeat=3)))
def test_all_216_set_field_permutations_have_identical_internal_result(ref_owner, positive_order, first_order, second_order):
    canonical = _reply(ref_owner)
    raw = deepcopy(canonical)
    raw["positive_basis_refs"] = [canonical["positive_basis_refs"][i] for i in positive_order]
    raw["issues"][0]["source_refs"] = [canonical["issues"][0]["source_refs"][i] for i in first_order]
    raw["issues"][1]["source_refs"] = [canonical["issues"][1]["source_refs"][i] for i in second_order]
    before = deepcopy(raw)
    normalized, trace = composition.normalize_reply_reference_order(raw)
    candidate, semantic = _parse(ref_owner, raw)
    expected, _ = _parse(ref_owner, canonical)
    assert candidate == expected
    assert semantic == normalized == canonical
    assert raw == before
    assert set(normalized["positive_basis_refs"]) == set(raw["positive_basis_refs"])
    assert [i["issue_code"] for i in normalized["issues"]] == ["FIRST", "SECOND"]
    assert [i.issue_slot for i in candidate.issue_candidates] == ["claim-issue-0", "claim-issue-1"]
    assert candidate.semantic_rationale_hash == canonical_sha256(raw["rationale"])
    assert candidate.recommended_verdict is VerificationVerdict.UNSUPPORTED
    assert trace["raw_encoding"] == "canonical-json-object"
    assert trace["raw_reply_sha256"] == hashlib.sha256(json.dumps(raw, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest()
    assert trace["parsed_reply_sha256"] == canonical_sha256(raw)
    assert trace["normalized_reply_sha256"] == canonical_sha256(canonical)
    ref_owner.runtime.validate_v2_provenance(ref_owner.binding, ref_owner.support, candidate)


@pytest.mark.parametrize("order", PERMUTATIONS)
def test_exact_utf8_wire_hash_is_distinct_from_canonical_object_hash(ref_owner, order):
    raw = _reply(ref_owner)
    raw["positive_basis_refs"] = [raw["positive_basis_refs"][i] for i in order]
    raw["rationale"] = "Exact Unicode explanation: 保留原文。"
    wire = "\n" + json.dumps(raw, ensure_ascii=False, indent=2) + "\n"
    normalized, trace = composition.normalize_reply_reference_order(wire)
    assert trace["raw_encoding"] == "utf8"
    assert trace["raw_reply_sha256"] == hashlib.sha256(wire.encode("utf-8")).hexdigest()
    assert trace["normalized_reply_sha256"] == canonical_sha256(normalized)
    assert trace["normalizer_version"] == Composer.REPLY_REF_ORDER_VERSION
    assert trace["normalizer_profile_sha256"] == Composer.reply_ref_order_profile_sha256()
    assert normalized["rationale"] == raw["rationale"]
    assert _parse(ref_owner, wire)[1] == normalized


@pytest.mark.parametrize("field", ["positive", "first_issue", "second_issue"])
def test_duplicate_refs_are_not_deduplicated_and_remain_strictly_rejected(ref_owner, field):
    raw = _reply(ref_owner)
    refs = raw["positive_basis_refs"] if field == "positive" else raw["issues"][field == "second_issue"]["source_refs"]
    refs[:] = [refs[2], refs[0], refs[2], refs[1]]
    before = deepcopy(raw)
    normalized, _ = composition.normalize_reply_reference_order(raw)
    normalized_refs = normalized["positive_basis_refs"] if field == "positive" else normalized["issues"][field == "second_issue"]["source_refs"]
    assert normalized_refs == sorted(refs) and len(normalized_refs) == 4
    assert raw == before
    with pytest.raises(ValueError, match="sorted and unique|unique"):
        _parse(ref_owner, raw)


def test_normalized_duplicate_issues_still_fail_original_uniqueness_guard(ref_owner):
    raw = _reply(ref_owner)
    raw["issues"][1] = deepcopy(raw["issues"][0])
    raw["issues"][1]["source_refs"].reverse()
    normalized, _ = composition.normalize_reply_reference_order(raw)
    assert normalized["issues"][0] == normalized["issues"][1]
    with pytest.raises(ValueError, match="issues must be unique"):
        _parse(ref_owner, raw)


@pytest.mark.parametrize("field", ["positive_basis_refs", "source_refs"])
@pytest.mark.parametrize("invalid", [None, "ref", 1, True, {}, [1], [None], [[]], [{"ref": "x"}]])
def test_wrong_ref_field_types_are_not_coerced_or_repaired(ref_owner, field, invalid):
    raw = _reply(ref_owner)
    target = raw if field == "positive_basis_refs" else raw["issues"][0]
    target[field] = deepcopy(invalid)
    before = deepcopy(raw)
    with pytest.raises((ValueError, TypeError)):
        _parse(ref_owner, raw)
    assert raw == before


@pytest.mark.parametrize("defect", ["truncated", "duplicate_object_key", "missing_positive", "missing_issue_refs", "extra_field", "null_issue"])
def test_complete_json_and_original_reply_shape_remain_required(ref_owner, defect):
    raw = _reply(ref_owner)
    if defect == "truncated":
        raw = json.dumps(raw)[:-5]
    elif defect == "duplicate_object_key":
        raw = json.dumps(raw)[:-1] + ',"verdict":"VERIFIED"}'
    elif defect == "missing_positive":
        del raw["positive_basis_refs"]
    elif defect == "missing_issue_refs":
        del raw["issues"][0]["source_refs"]
    elif defect == "extra_field":
        raw["invented_authority"] = []
    else:
        raw["issues"][0] = None
    with pytest.raises((ValueError, TypeError)):
        _parse(ref_owner, raw)


def test_issue_sequence_business_text_and_member_bytes_are_never_normalized(ref_owner):
    raw = _reply(ref_owner)
    raw["issues"].reverse()
    raw["issues"][0]["source_refs"] = [" whitespace is preserved ", "z", "a"]
    normalized, _ = composition.normalize_reply_reference_order(raw)
    expected = deepcopy(raw)
    expected["positive_basis_refs"].sort()
    for issue in expected["issues"]:
        issue["source_refs"].sort()
    assert normalized == expected
    assert " whitespace is preserved " in normalized["issues"][0]["source_refs"]
    assert [i["issue_code"] for i in normalized["issues"]] == ["SECOND", "FIRST"]


@pytest.mark.parametrize("field", ["positive", "issue"])
def test_sorting_cannot_admit_out_of_scope_members(ref_owner, field):
    raw = _reply(ref_owner)
    refs = raw["positive_basis_refs"] if field == "positive" else raw["issues"][0]["source_refs"]
    refs[:] = ["unknown-authority:outside", *reversed(refs)]
    with pytest.raises(ValueError, match="authority|provenance"):
        _parse(ref_owner, raw)


@pytest.mark.parametrize("ref", ["fact_snapshot:i5", "evidence_package:i5"])
def test_scope_refs_remain_illegal_positive_even_after_external_sorting(ref_owner, ref):
    raw = _reply(ref_owner)
    raw["positive_basis_refs"] = [ref, *reversed(raw["positive_basis_refs"])]
    candidate, _ = _parse(ref_owner, raw)
    with pytest.raises(ClaimBindingError, match="source kind"):
        ref_owner.runtime.validate_v2_provenance(ref_owner.binding, ref_owner.support, candidate)


@pytest.mark.parametrize("contradiction", ["support", "binding"])
def test_sorting_does_not_override_original_verified_contradiction_fences(tmp_path, contradiction):
    options = {"support_relation": SupportRelation.CONTRADICTED} if contradiction == "support" else {"binding_relation": ClaimBindingRelation.CONTRADICTS}
    fx = enriched_binding(fixture(tmp_path, **options))
    raw = {"verdict": "VERIFIED", "positive_basis_refs": [fx.invocation.verifier_project_input_basis.project_input_basis_ref, "fact:i5"],
        "rationale": "Controlled declaration, not an automatic successful verdict", "issues": []}
    candidate, _ = _parse(fx, raw)
    before = fx.runtime.ledger_path.read_bytes()
    with pytest.raises(ClaimBindingError, match="contradict"):
        fx.runtime.validate_v2_provenance(fx.binding, fx.support, candidate)
    assert fx.runtime.ledger_path.read_bytes() == before


@pytest.mark.parametrize("binding", [None, {}, {"normalizer_version": "unknown", "normalizer_profile_sha256": "a" * 64},
    {"normalizer_version": "verifier-ref-order-v1", "normalizer_profile_sha256": "f" * 64}])
def test_unknown_or_invalid_entry_binding_is_not_automatically_upgraded(ref_owner, binding):
    data = ref_owner.invocation.model_dump(mode="json")
    data["deterministic_validation_facts"][ENTRY_KEY] = binding
    data["verifier_semantic_input_sha256"] = build_verifier_semantic_input_sha256(data)
    invocation = VerifierClaimInputSchema.model_validate(data)
    with pytest.raises((ValueError, composition.TargetClaimVerifierError)):
        _parse(ref_owner, _reply(ref_owner), invocation=invocation)


def test_new_parser_profile_changes_identity_without_changing_prompt_or_policy(ref_owner):
    current = ref_owner.invocation
    old = _rebuild(current, omit_entry=True)
    binding = current.deterministic_validation_facts[ENTRY_KEY]
    assert binding == Composer.reply_ref_order_binding()
    assert old.verifier_semantic_input_sha256 != current.verifier_semantic_input_sha256
    assert old.authority_universe_sha256 == current.authority_universe_sha256
    assert old.response_contract == current.response_contract == "canonical_claim_verifier_v2"
    assert old.verifier_policy_version == current.verifier_policy_version == "v2"
    assert old.deterministic_validation_facts["verifier_prompt_profile_sha256"] == current.deterministic_validation_facts["verifier_prompt_profile_sha256"]
    assert old.deterministic_validation_facts["verifier_output_cap_v1"] == current.deterministic_validation_facts["verifier_output_cap_v1"]
    raw = _reply(ref_owner)
    raw["issues"][1]["source_refs"].reverse()
    with pytest.raises(ValueError, match="sorted and unique"):
        _parse(ref_owner, raw, invocation=old)
    assert _parse(ref_owner, raw)[1]["issues"][1]["source_refs"] == sorted(raw["issues"][1]["source_refs"])


class WireGateway(Gateway):
    def __init__(self, content):
        super().__init__()
        self.content = content

    def generate(self, request):
        self.calls.append(request)
        return ModelResponseSchema(model_call_id=request.model_call_id, task_id=request.task_id,
            run_id=request.run_id, model_name="controlled-offline-ref-order", success=True, created_at="test",
            content=self.content)


def _dispatch(fx):
    with patch.object(fx.composer, "_material", side_effect=lambda *args: model_material(fx)):
        return fx.composer._evaluate(fx.claim, fx.binding, fx.support, fx.context)


def _candidate_receipt(fx):
    ref = next(r for r in fx.composer.store.list_references() if r.artifact_type == "canonical_verifier_candidate")
    return ref, fx.composer.store.get_json(ref.artifact_ref, task_id=ref.task_id, run_id=ref.run_id)


@pytest.mark.parametrize("state", ["completed", "failed", "intent_only"])
def test_old_physical_receipts_never_redispatch_or_reinterpret_with_new_entry(tmp_path, state):
    fx = fixture(tmp_path)
    gateway = WireGateway('{"verdict":"VERIFIED"}')
    fx.composer.model_gateway = gateway
    # Prospective registration is absent at construction, not removed from an
    # already registered/persisted input. Only this node uses the old fixture.
    old, _ = fx.composer.build_input(fx.claim, fx.binding, fx.support, fx.context,
        _recorded_recovery_facts={}, _recorded_reply_ref_order=None)
    assert VERIFIER_LOCAL_REGISTRATION_KEY not in old.deterministic_validation_facts
    assert VERIFIER_LOCAL_CORRECTION_KEY not in old.deterministic_validation_facts
    assert ENTRY_KEY not in old.deterministic_validation_facts
    assert old.verifier_semantic_input_sha256 == build_verifier_semantic_input_sha256(old)
    invocation, current_material = model_material(fx)
    material = deepcopy(current_material)
    material["invocation"] = old.model_dump(mode="json")
    key = canonical_sha256({"contract": "verifier-physical-occurrence-v2", "verifier_semantic_input_sha256": old.verifier_semantic_input_sha256})
    root = "artifact://canonical-verifier-v2/" + key
    occurrence = "claim-verification-v2://sha256/" + key
    assert Composer._physical(old) == (key, occurrence, root)
    assert Composer._physical(invocation)[0] != key
    identity = {"task_id": fx.env["lifecycle"].load_run_identity(fx.context.active_lineage.run_id).task_id,
        "run_id": fx.context.active_lineage.run_id, "section_id": fx.claim.section_id}
    # The parser-entry change does not alter Context instructions. Build a valid
    # isolated package with recorded prompt/cap and the OLD physical target;
    # neither this package-only invocation nor the persisted input is registered.
    context_invocation, _ = fx.composer.build_input(fx.claim, fx.binding, fx.support, fx.context,
        _recorded_recovery_facts={})
    assert VERIFIER_LOCAL_REGISTRATION_KEY not in context_invocation.deterministic_validation_facts
    package, _ = fx.composer._package(material, occurrence=occurrence,
        invocation=context_invocation, validate=lambda: None)
    assert package.invocation_subject.invocation_id == occurrence
    payload = {"schema_version": "canonical_verifier_input_v2", "input_material": material,
        "input_fingerprint": old.verifier_semantic_input_sha256, "context_package": package.model_dump(mode="json")}
    assert not any(ref.artifact_ref.startswith("artifact://canonical-verifier-v2/")
        for ref in fx.composer.store.list_references())
    fx.composer.store.put_json(payload, artifact_ref=root + "/input", artifact_type="canonical_verifier_input",
        schema_version="canonical_verifier_input_v2", created_by="controlled-historical-owner", **identity)
    if state != "intent_only":
        result = {"schema_version": "canonical_verifier_candidate_v2", "input_fingerprint": old.verifier_semantic_input_sha256, "outcome": state}
        if state == "failed":
            result["failure"] = {"message": "Frozen unsorted authority rejection", "error_code": "ValidationError", "category": "validation", "origin": "unknown"}
        else:
            candidate, semantic = Composer.parse_candidate({"verdict": "UNSUPPORTED", "positive_basis_refs": [], "rationale": "Frozen old successful parse", "issues": []},
                invocation=old, occurrence=occurrence, receipt_ref=root + "/candidate")
            result.update(candidate=candidate.model_dump(mode="json"), semantic_result=semantic)
        fx.composer.store.put_json(result, artifact_ref=root + "/candidate", artifact_type="canonical_verifier_candidate",
            schema_version="canonical_verifier_candidate_v2", created_by="controlled-historical-owner", **identity)
    refs = fx.composer.store.list_references()
    before = {r.artifact_ref: fx.composer.store.get_json(r.artifact_ref, task_id=r.task_id, run_id=r.run_id) for r in refs}
    before_bytes = {r.artifact_ref: fx.composer.store._path_for_ref(r.artifact_ref).read_bytes() for r in refs}
    ledger_bytes = fx.runtime.ledger_path.read_bytes()
    assert fx.runtime.resolve_claim_head(fx.claim.claim_ref) is None
    def recorded_material(*args, _recorded_invocation=None, **kwargs):
        # The shared _dispatch helper ignores this argument. Respect the real
        # production selection of the immutable OLD invocation in this node.
        if _recorded_invocation is None:
            selected, selected_material = invocation, deepcopy(current_material)
        else:
            facts = _recorded_invocation.deterministic_validation_facts
            selected, _ = fx.composer.build_input(fx.claim, fx.binding, fx.support, fx.context,
                _recorded_output_cap=facts.get(Composer.OUTPUT_CAP_BINDING_KEY),
                _recorded_reply_ref_order=facts.get(ENTRY_KEY), _recorded_recovery_facts={})
            assert selected == _recorded_invocation == old
            selected_material = deepcopy(material)
        return selected, selected_material
    # Old completed semantics are strictly revalidated as recorded. No old raw
    # wire goes through the current external parser or ref-order normalizer.
    with patch.object(fx.composer, "_material", side_effect=recorded_material), \
            patch.object(Composer, "parse_candidate", side_effect=AssertionError("old wire must not be reinterpreted")), \
            patch.object(Composer, "normalize_reply_reference_order", side_effect=AssertionError("old wire must not be normalized")):
        if state == "completed":
            replay = fx.composer._evaluate(fx.claim, fx.binding, fx.support, fx.context)
            assert replay == candidate
            assert replay.model_dump(mode="json") == result["candidate"]
            assert replay.verifier_semantic_input_sha256 == old.verifier_semantic_input_sha256
            assert replay.verification_occurrence_ref == occurrence
            assert replay.semantic_rationale_ref == root + "/candidate"
            assert VERIFIER_LOCAL_REGISTRATION_KEY not in replay.deterministic_validation_facts
            assert ENTRY_KEY not in replay.deterministic_validation_facts
            with pytest.raises(composition.TargetClaimVerifierError, match="^Verifier V2 semantic input is stale$") as error:
                fx.composer.validate_v2_identity(fx.claim, fx.binding, fx.support, replay, fx.context.active_lineage)
            assert error.value.error_code == "CLAIM_VERIFIER_FAILED"
            with pytest.raises(ClaimCurrentnessError, match="^Verifier Writer/ProjectInput/semantic identity is stale$") as error:
                fx.runtime.commit_verification(fx.claim.claim_ref, fx.binding.binding_facts_ref,
                    fx.support.support_ref, replay, None, fx.context)
            assert isinstance(error.value.__cause__, composition.TargetClaimVerifierError)
            assert error.value.__cause__.error_code == "CLAIM_VERIFIER_FAILED"
            assert str(error.value.__cause__) == "Verifier V2 semantic input is stale"
        else:
            with pytest.raises(composition.TargetClaimVerifierError) as error:
                fx.composer._evaluate(fx.claim, fx.binding, fx.support, fx.context)
            if state == "failed":
                assert error.value.error_code == result["failure"]["error_code"] == "ValidationError"
                assert str(error.value) == result["failure"]["message"]
                assert error.value.failure_category.value == result["failure"]["category"]
                assert error.value.failure_origin.value == result["failure"]["origin"]
            else:
                assert error.value.error_code == "VERIFIER_INDETERMINATE_EXECUTION"
                assert str(error.value) == "Verifier intent exists without durable response; no automatic redispatch"
                assert error.value.failure_category.value == "unknown"
    assert gateway.calls == []
    assert fx.composer.store.list_references() == refs
    assert {r.artifact_ref: fx.composer.store.get_json(r.artifact_ref, task_id=r.task_id, run_id=r.run_id) for r in refs} == before
    assert {r.artifact_ref: fx.composer.store._path_for_ref(r.artifact_ref).read_bytes() for r in refs} == before_bytes
    assert fx.runtime.ledger_path.read_bytes() == ledger_bytes
    assert fx.runtime.resolve_claim_head(fx.claim.claim_ref) is None


@pytest.mark.parametrize("old_shape", ["before_entry", "before_cap", "before_prompt", "known_entry"])
def test_typed_historical_results_keep_recorded_parser_cap_and_prompt_identity(tmp_path, old_shape):
    fx = fixture(tmp_path)
    omit_entry = old_shape != "known_entry"
    omit_cap = old_shape in {"before_cap", "before_prompt"}
    omit_prompt = old_shape == "before_prompt"
    old = _rebuild(fx.invocation, omit_entry=omit_entry, omit_cap=omit_cap, omit_prompt=omit_prompt)
    candidate, _ = _parse(fx, {"verdict": "UNSUPPORTED", "positive_basis_refs": [], "rationale": "Exact historical semantics", "issues": []}, invocation=old)
    build = fx.composer.build_input
    def historical(*args, **kwargs):
        invocation, project = build(*args, **kwargs)
        return _rebuild(invocation, omit_entry=omit_entry, omit_cap=omit_cap, omit_prompt=omit_prompt), project
    with patch.object(fx.composer, "build_input", side_effect=historical):
        result = fx.runtime.commit_verification(fx.claim.claim_ref, fx.binding.binding_facts_ref,
            fx.support.support_ref, candidate, None, fx.context).verification_result
    frozen, ledger = result.model_dump(mode="json"), fx.runtime.ledger_path.read_bytes()
    fx.composer.verifier_max_output_tokens = 2048
    fx.composer.validate_v2_identity(fx.claim, fx.binding, fx.support, result, fx.context.active_lineage)
    assert result.model_dump(mode="json") == frozen
    assert fx.runtime.ledger_path.read_bytes() == ledger
    with pytest.raises(composition.TargetClaimVerifierError):
        fx.composer.validate_v2_identity(fx.claim, fx.binding, fx.support, candidate, fx.context.active_lineage)


@pytest.mark.parametrize("state", ["completed", "failed"])
def test_durable_receipt_preserves_exact_wire_and_normalization_trace(tmp_path, state):
    fx = _two_fact_fixture(tmp_path)
    raw = _reply(fx)
    raw["issues"][1]["source_refs"].reverse()
    if state == "failed":
        raw["issues"][1]["source_refs"].append(raw["issues"][1]["source_refs"][0])
    wire = json.dumps(raw, ensure_ascii=False, indent=2) + "\n"
    fx.composer.model_gateway = gateway = WireGateway(wire)
    if state == "completed":
        _dispatch(fx)
    else:
        with pytest.raises(composition.TargetClaimVerifierError):
            _dispatch(fx)
    assert len(gateway.calls) == 1
    _, receipt = _candidate_receipt(fx)
    entry = receipt["reply_entry"]
    assert entry["raw_reply"] == wire
    assert entry["raw_reply_sha256"] == hashlib.sha256(wire.encode("utf-8")).hexdigest()
    assert entry["normalizer_version"] == Composer.REPLY_REF_ORDER_VERSION
    assert entry["normalizer_profile_sha256"] == Composer.reply_ref_order_profile_sha256()
    normalized, _ = composition.normalize_reply_reference_order(wire)
    assert entry["normalized_reply"] == normalized
    assert entry["normalized_reply_sha256"] == canonical_sha256(normalized)
    assert entry["parsed_reply_sha256"] == canonical_sha256(raw)
    assert entry["normalization_status"] == "completed"  # Sorting succeeded even when original strict duplicate guard rejects.
    assert Composer._validate_reply_entry_receipt(entry, invocation=fx.invocation) == normalized
    assert receipt["outcome"] == state
    assert raw["issues"][1]["source_refs"] != sorted(raw["issues"][1]["source_refs"])


@pytest.mark.parametrize("wire", [
    '{"verdict":"UNSUPPORTED","positive_basis_refs":[],"rationale":"Incomplete","issues":[',
    '{"verdict":"UNSUPPORTED","positive_basis_refs":null,"rationale":"Wrong type","issues":[]}',
    '{"verdict":"UNSUPPORTED","positive_basis_refs":[],"rationale":"Duplicate object key","issues":[],"verdict":"VERIFIED"}',
])
def test_invalid_wire_failed_trace_is_preserved_without_json_or_member_repair(tmp_path, wire):
    fx = fixture(tmp_path)
    fx.composer.model_gateway = gateway = WireGateway(wire)
    with pytest.raises(composition.TargetClaimVerifierError):
        _dispatch(fx)
    reference, receipt = _candidate_receipt(fx)
    entry = receipt["reply_entry"]
    assert receipt["outcome"] == "failed"
    assert entry["raw_reply"] == wire
    assert entry["raw_reply_sha256"] == hashlib.sha256(wire.encode("utf-8")).hexdigest()
    assert entry["normalization_status"] == "not_completed"
    assert entry["normalized_reply"] is None and entry["normalized_reply_sha256"] is None
    assert isinstance(entry["normalization_error_type"], str)
    assert Composer._validate_reply_entry_receipt(entry, invocation=fx.invocation) is None
    corrupted = deepcopy(entry)
    corrupted["raw_reply_sha256"] = "f" * 64
    with pytest.raises(composition.TargetClaimVerifierError):
        Composer._validate_reply_entry_receipt(corrupted, invocation=fx.invocation)
    failure = receipt["failure"]
    refs = fx.composer.store.list_references()
    with patch.object(Composer, "normalize_reply_reference_order", side_effect=AssertionError("failed history must not be reinterpreted")):
        with pytest.raises(composition.TargetClaimVerifierError) as error:
            _dispatch(fx)
    assert error.value.error_code == failure["error_code"]
    assert str(error.value) == failure["message"]
    assert len(gateway.calls) == 1 and fx.composer.store.list_references() == refs
    assert fx.composer.store.get_json(reference.artifact_ref, task_id=reference.task_id, run_id=reference.run_id) == receipt


@pytest.mark.parametrize("defect", ["missing_entry", "unavailable", "not_completed_valid_trace", "raw_text", "raw_hash", "parsed_hash", "normalized_member", "normalized_hash", "version", "profile_hash", "status", "changed_paths", "business_text"])
def test_completed_receipt_trace_tampering_fails_without_dispatch_or_storage_write(tmp_path, defect):
    fx = _two_fact_fixture(tmp_path)
    raw = _reply(fx)
    raw["positive_basis_refs"].reverse()
    fx.composer.model_gateway = gateway = WireGateway(json.dumps(raw))
    _dispatch(fx)
    reference, receipt = _candidate_receipt(fx)
    refs = fx.composer.store.list_references()
    damaged = deepcopy(receipt)
    if defect == "missing_entry":
        del damaged["reply_entry"]
    elif defect == "unavailable":
        damaged["reply_entry_available"] = False
        damaged["reply_entry"] = None
    elif defect == "not_completed_valid_trace":
        entry = Composer._initial_reply_entry('{"issues":[')
        entry["normalization_error_type"] = "JSONDecodeError"
        assert Composer._validate_reply_entry_receipt(entry, invocation=fx.invocation) is None
        damaged["reply_entry"] = entry
    elif defect == "raw_text":
        damaged["reply_entry"]["raw_reply"] += " "
    elif defect == "raw_hash":
        damaged["reply_entry"]["raw_reply_sha256"] = "f" * 64
    elif defect == "parsed_hash":
        damaged["reply_entry"]["parsed_reply_sha256"] = "f" * 64
    elif defect == "normalized_member":
        damaged["reply_entry"]["normalized_reply"]["positive_basis_refs"][0] = "unknown:tampered"
    elif defect == "normalized_hash":
        damaged["reply_entry"]["normalized_reply_sha256"] = "f" * 64
    elif defect == "version":
        damaged["reply_entry"]["normalizer_version"] = "unknown"
    elif defect == "profile_hash":
        damaged["reply_entry"]["normalizer_profile_sha256"] = "f" * 64
    elif defect == "changed_paths":
        damaged["reply_entry"]["changed_ref_paths"] = []
    elif defect == "business_text":
        normalized = damaged["reply_entry"]["normalized_reply"]
        normalized["issues"][0]["message"] = "Changed explanation despite a recomputed structure hash"
        damaged["reply_entry"]["normalized_reply_sha256"] = canonical_sha256(normalized)
    else:
        damaged["reply_entry"]["normalization_status"] = "failed"
    original = fx.composer.store.get_json
    def read(ref, **identity):
        return deepcopy(damaged) if ref == reference.artifact_ref else original(ref, **identity)
    with patch.object(fx.composer.store, "get_json", side_effect=read), pytest.raises(composition.TargetClaimVerifierError):
        _dispatch(fx)
    assert len(gateway.calls) == 1
    assert fx.composer.store.list_references() == refs
    assert original(reference.artifact_ref, task_id=reference.task_id, run_id=reference.run_id) == receipt


def _real_cases():
    explicit = os.environ.get("BCA014_VERIFIER_REF_ORDER_COUNTEREXAMPLE_DIR")
    cases = [pytest.param(None, id="portable-external-order-entry")]
    if explicit:
        cases.append(pytest.param(Path(explicit), id="frozen-real-attempt3-wire"))
    return cases


@pytest.mark.parametrize("counterexample_dir", _real_cases())
def test_frozen_real_a3_wire_only_loses_order_blocker_and_keeps_actual_verdict(counterexample_dir):
    if counterexample_dir is None:
        raw = {"verdict": "UNSUPPORTED", "positive_basis_refs": ["z", "a"],
            "rationale": "Portable unchanged declaration", "issues": [{"issue_code": "P", "message": "Portable exact text", "severity": "MINOR", "source_refs": ["y", "b"]}]}
        normalized, _ = composition.normalize_reply_reference_order(raw)
        assert normalized == {**raw, "positive_basis_refs": ["a", "z"],
            "issues": [{**raw["issues"][0], "source_refs": ["b", "y"]}]}
        return
    assert counterexample_dir.resolve() == A3_DIR.resolve()
    input_path = counterexample_dir / "input_exact_artifact_envelope.json"
    response_path = counterexample_dir / "response_exact_observed_record.jsonl"
    input_bytes, response_bytes = input_path.read_bytes(), response_path.read_bytes()
    assert hashlib.sha256(input_bytes).hexdigest() == A3_INPUT_SHA256
    assert hashlib.sha256(response_bytes).hexdigest() == A3_RESPONSE_SHA256
    envelope = json.loads(input_bytes)
    assert envelope["content_encoding"] == "json-utf8"
    material = json.loads(base64.b64decode(envelope["content_base64"]))["input_material"]
    observed = json.loads(response_bytes)["response"]
    wire = observed["content"]
    assert hashlib.sha256(wire.encode("utf-8")).hexdigest() == A3_CONTENT_SHA256
    assert observed["finish_reason"] == "stop" and observed["token_usage"]["completion_tokens"] == 583
    old = VerifierClaimInputSchema.model_validate(material["invocation"])
    assert ENTRY_KEY not in old.deterministic_validation_facts
    with pytest.raises(ValueError, match="sorted and unique"):
        Composer.parse_candidate(wire, invocation=old, occurrence="offline-a3://frozen", receipt_ref="offline-a3://wire")
    fresh_copy = _rebuild(old, entry=Composer.reply_ref_order_binding())
    candidate, normalized = Composer.parse_candidate(wire, invocation=fresh_copy,
        occurrence="offline-a3://new-entry-copy", receipt_ref="offline-a3://wire")
    raw = json.loads(wire)
    expected = deepcopy(raw)
    expected["positive_basis_refs"].sort()
    for issue in expected["issues"]:
        issue["source_refs"].sort()
    assert normalized == expected
    assert candidate.recommended_verdict.value == raw["verdict"]
    assert [i.issue_code for i in candidate.issue_candidates] == [i["issue_code"] for i in raw["issues"]]
    assert [i.message_hash for i in candidate.issue_candidates] == [canonical_sha256(i["message"]) for i in raw["issues"]]
    binding = SimpleNamespace(exact_authorized_source_refs=old.exact_relevant_source_refs,
        facts=tuple(ClaimBindingFactSchema.model_validate(i) for i in material["binding_facts"]))
    shared = material["shared_support"]
    support = SimpleNamespace(relation=SupportRelation(shared["relation"]), supporting_refs=tuple(shared["supporting_refs"]))
    def guard_outcome(value):
        try:
            ClaimVerificationRuntime.validate_v2_provenance(binding, support, value)
        except ClaimBindingError as error:
            return {"status": "REJECTED", "error_type": type(error).__name__, "message": str(error)}
        return {"status": "ADMITTED"}
    old_strict_candidate, _ = Composer._parse_candidate_strict(expected, invocation=old,
        occurrence="offline-a3://manually-sorted-control", receipt_ref="offline-a3://wire")
    guard = guard_outcome(candidate)
    assert guard == guard_outcome(old_strict_candidate)
    assert input_path.read_bytes() == input_bytes and response_path.read_bytes() == response_bytes
    controlled_output = os.environ.get("BCA014_VERIFIER_REF_ORDER_OFFLINE_AUDIT_DIR")
    if controlled_output:
        target = Path(controlled_output).resolve()
        allowed = (A3_DIR.parents[2] / "attempt4-offline-ref-normalization").resolve()
        assert target.is_relative_to(allowed)
        target.mkdir(parents=True, exist_ok=True)
        normalized_copy, trace = composition.normalize_reply_reference_order(wire)
        entry = {**trace, "raw_reply": wire, "normalized_reply": normalized_copy, "normalization_status": "completed"}
        assert Composer._validate_reply_entry_receipt(entry, invocation=fresh_copy) == normalized_copy
        with (target / "REAL_A3_OFFLINE_NORMALIZATION_TRACE.json").open("x", encoding="utf-8", newline="\n") as stream:
            json.dump({"schema_version": "offline_a3_reply_order_copy_v1", "scope": "New offline copy; original a3 owner and wire are untouched",
                "input_source": {"path": str(input_path), "sha256": A3_INPUT_SHA256},
                "response_source": {"path": str(response_path), "sha256": A3_RESPONSE_SHA256},
                "reply_entry": entry, "parsed_candidate": candidate.model_dump(mode="json"),
                "mechanical_provenance_guard": guard, "commit_verification_called": False,
                "provider_or_retrieval_calls": 0, "business_quality_certified": False, "released": False}, stream, ensure_ascii=False, indent=2)
            stream.write("\n")
    # No commit_verification, runtime owner, Provider, release or publication action.
