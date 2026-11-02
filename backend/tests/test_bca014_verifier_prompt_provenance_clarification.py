"""Offline regression for positive provenance presentation, without new authority.

These probes use the existing mechanical guard as the independent admission
oracle. A ref admitted to the prompt projection does not imply VERIFIED.
"""
import json
from types import SimpleNamespace
from unittest.mock import patch

import pytest
from pydantic import ValidationError

from apps.enterprise_document.schemas.agent_rag_v1.claim_support import SupportRelation
from apps.enterprise_document.schemas.agent_rag_v1.verification import (
    ClaimAuthorizedSourceRefSchema,
    ClaimBindingFactsCandidateSchema,
    ClaimBindingRelation,
    VerificationVerdict,
    VerifierClaimInputSchema,
    build_verifier_semantic_input_sha256,
)
from apps.enterprise_document.services.agent_rag_v1 import claim_verifier_composition as composition
from apps.enterprise_document.services.agent_rag_v1.claim_composition import ClaimSourceMaterial
from apps.enterprise_document.services.agent_rag_v1.claim_verification_runtime import (
    ClaimBindingError,
    ClaimVerificationRuntime,
)
from backend.tests import test_multi_agent_v3_claim_verification_runtime as owners
from backend.tests.test_bca014_i5_verifier_project_input_basis import Gateway, fixture as owner_fixture, model_material
from backend.tests.test_bca014_i5_verifier_v2_semantics import enriched_binding, parse
from core.hashing import canonical_sha256


PI = "writer-project-input-basis://test-exact"
FACT = "fact:test"
SNAPSHOT = "fact-snapshot:test"
PACKAGE = "evidence-package:test"
E1, E2 = "evidence:test-1", "evidence:test-2"
OLD_ROLE_SHA256 = "c2dea5d14d7a2152b6f0856c4f9dc59930327d69c01f926af57623a3cbacdc63"
OLD_TEMPLATE_SHA256 = "d1da3935da21e108bcdf7f3cfb186b50a17ba2e9a55cc2fcd98e8540aa9d288d"


def fixture(tmp_path, **kwargs):
    """Supply the new capability dependency within this new test module only."""
    cls = composition.TargetClaimVerifierComposition
    original_init = cls.__init__
    def controlled_init(self, *args, **options):
        if options.get("model_gateway") is None:
            options["model_gateway"] = Gateway()
        original_init(self, *args, **options)
    with patch.object(cls, "__init__", controlled_init):
        return owner_fixture(tmp_path, **kwargs)


def _matrix_binding(scenario):
    refs = [("FACT", FACT), ("FACT_SNAPSHOT", SNAPSHOT),
            ("EVIDENCE_PACKAGE", PACKAGE), ("EVIDENCE", E1), ("EVIDENCE", E2)]
    relation = {"supports": ClaimBindingRelation.SUPPORTS,
                "context_only": ClaimBindingRelation.CONTEXT_ONLY,
                "no_material_support": ClaimBindingRelation.NO_MATERIAL_SUPPORT,
                "contradicts": ClaimBindingRelation.CONTRADICTS,
                "conflicting_observations": ClaimBindingRelation.SUPPORTS,
                "ambiguous_kind": ClaimBindingRelation.CONTEXT_ONLY}[scenario]
    facts = [SimpleNamespace(source_kind=kind, exact_source_ref=ref,
        relation=relation if ref == E1 else ClaimBindingRelation.CONTEXT_ONLY)
        for kind, ref in refs]
    if scenario == "conflicting_observations":
        facts.append(SimpleNamespace(source_kind="EVIDENCE", exact_source_ref=E1,
            relation=ClaimBindingRelation.NO_MATERIAL_SUPPORT))
    if scenario == "ambiguous_kind":
        refs.append(("FACT", E1))
        facts.append(SimpleNamespace(source_kind="FACT", exact_source_ref=E1,
            relation=ClaimBindingRelation.CONTEXT_ONLY))
    return SimpleNamespace(exact_authorized_source_refs=tuple(
        ClaimAuthorizedSourceRefSchema(source_kind=kind, exact_source_ref=ref)
        for kind, ref in sorted(refs)), facts=tuple(facts))


def _guard_candidate(binding, positive, verdict=VerificationVerdict.UNSUPPORTED):
    return SimpleNamespace(allowed_authority_refs=tuple(sorted(
        {s.exact_source_ref for s in binding.exact_authorized_source_refs} | {PI})),
        verifier_project_input_basis=SimpleNamespace(project_input_basis_ref=PI),
        positive_basis_refs=tuple(sorted(positive)), recommended_verdict=verdict,
        issue_candidates=())


@pytest.mark.parametrize("relation", list(SupportRelation))
@pytest.mark.parametrize("supporting", [(), (E1,), (E2,), (E1, E2)])
@pytest.mark.parametrize("scenario", ["supports", "context_only", "no_material_support",
                                      "contradicts", "conflicting_observations", "ambiguous_kind"])
def test_positive_projection_equals_existing_non_verified_admission_guard(relation, supporting, scenario):
    binding = _matrix_binding(scenario)
    support = SimpleNamespace(relation=relation, supporting_refs=supporting)
    broad = _guard_candidate(binding, ()).allowed_authority_refs
    expected = []
    for ref in broad:
        try:
            ClaimVerificationRuntime.validate_v2_provenance(
                binding, support, _guard_candidate(binding, (ref,)))
        except ClaimBindingError:
            continue
        expected.append(ref)
    projected = composition.admissible_positive_basis_refs(binding, support, PI)
    assert projected == tuple(expected)
    assert SNAPSHOT not in projected and PACKAGE not in projected
    assert FACT in projected and PI in projected
    # The complete mixed subset must still satisfy the unchanged runtime guard.
    ClaimVerificationRuntime.validate_v2_provenance(
        binding, support, _guard_candidate(binding, projected))


@pytest.mark.parametrize("relation", [SupportRelation.CONTRADICTED, SupportRelation.INDETERMINATE])
def test_projected_positive_ref_does_not_override_verified_support_veto(relation):
    binding = _matrix_binding("supports")
    support = SimpleNamespace(relation=relation, supporting_refs=(E1,))
    assert PI in composition.admissible_positive_basis_refs(binding, support, PI)
    with pytest.raises(ClaimBindingError, match="VERIFIED forbidden"):
        ClaimVerificationRuntime.validate_v2_provenance(
            binding, support, _guard_candidate(binding, (PI,), VerificationVerdict.VERIFIED))


def test_projected_ref_does_not_override_binding_contradiction_or_supply_semantic_verdict():
    binding = _matrix_binding("contradicts")
    support = SimpleNamespace(relation=SupportRelation.SUPPORTED, supporting_refs=(E2,))
    assert FACT in composition.admissible_positive_basis_refs(binding, support, PI)
    with pytest.raises(ClaimBindingError, match="Binding contradiction"):
        ClaimVerificationRuntime.validate_v2_provenance(
            binding, support, _guard_candidate(binding, (FACT,), VerificationVerdict.VERIFIED))


@pytest.mark.parametrize("scope_ref", ["fact_snapshot:i5", "evidence_package:i5"])
def test_context_scope_can_remain_issue_provenance_but_is_never_repaired_positive(tmp_path, scope_ref):
    fx = enriched_binding(fixture(tmp_path, support_relation=SupportRelation.NOT_SUPPORTED))
    candidate = parse(fx, verdict="UNSUPPORTED", issues=[{
        "issue_code": "CONTEXT_SCOPE", "message": "Context is insufficient positive authority",
        "severity": "MINOR", "source_refs": [scope_ref]}])
    fx.runtime.validate_v2_provenance(fx.binding, fx.support, candidate)
    assert candidate.issue_candidates[0].source_refs == (scope_ref,)
    raw = {"verdict": "VERIFIED", "positive_basis_refs": sorted([
        "fact:i5", scope_ref, fx.invocation.verifier_project_input_basis.project_input_basis_ref]),
        "rationale": "Explicit model selection", "issues": []}
    invalid, reply = composition.TargetClaimVerifierComposition.parse_candidate(
        json.dumps(raw), invocation=fx.invocation, occurrence="clarification://counterexample",
        receipt_ref="artifact://clarification-counterexample")
    assert invalid.positive_basis_refs == tuple(raw["positive_basis_refs"])
    assert reply["positive_basis_refs"] == raw["positive_basis_refs"]
    before = fx.runtime.ledger_path.read_bytes()
    with pytest.raises(ClaimBindingError, match="source kind is inadmissible"):
        fx.runtime.validate_v2_provenance(fx.binding, fx.support, invalid)
    assert fx.runtime.ledger_path.read_bytes() == before


def _two_fact_fixture(tmp_path):
    fx = enriched_binding(fixture(tmp_path))
    old = fx.runtime._binding_record_candidate(fx.binding)
    source = next(s for s in old.exact_authorized_source_refs if s.source_kind == "FACT")
    fact = next(f for f in old.facts if f.source_kind == "FACT")
    sources = sorted((*old.exact_authorized_source_refs, source.model_copy(
        update={"exact_source_ref": "fact:second-owned-ref"})), key=lambda s: (s.source_kind, s.exact_source_ref))
    facts = sorted((*old.facts, fact.model_copy(update={"exact_source_ref": "fact:second-owned-ref"})),
        key=lambda f: (f.source_kind, f.exact_source_ref, f.relation.value, f.support_kind.value, f.source_scope_ref or ""))
    data = old.model_dump(mode="json")
    data.update(exact_authorized_source_refs=[s.model_dump(mode="json") for s in sources],
        facts=[f.model_dump(mode="json") for f in facts],
        source_set_hash=canonical_sha256([s.model_dump(mode="json") for s in sources]))
    provisional = ClaimBindingFactsCandidateSchema.model_validate(data)
    provisional = provisional.model_copy(update={"candidate_semantic_fingerprint":
        fx.runtime.binding_candidate_semantic_fingerprint(provisional)})
    fx.binding = fx.runtime.commit_binding_facts(provisional, owners._context(
        fx.env, authorization_ref="binding:two-fact-clarification", purpose="test-binding"))
    fx.invocation, _ = fx.composer.build_input(fx.claim, fx.binding, fx.support, fx.context)
    return fx


def test_fact_wrappers_preserve_exact_owner_association_under_reversed_row_order(tmp_path):
    fx = _two_fact_fixture(tmp_path)
    rows = []
    bodies = {}
    for index, source in enumerate(s for s in fx.binding.exact_authorized_source_refs if s.source_kind == "FACT"):
        body = {"fact_id": f"owned-fact-{index}", "fact_type": "capacity",
                "normalized_value": f"different-owned-value-{index}"}
        bodies[source.exact_source_ref] = dict(body)
        observation = next(f for f in fx.binding.facts if f.exact_source_ref == source.exact_source_ref)
        rows.append(ClaimSourceMaterial(source, observation, body))
    fx.composer.sources = SimpleNamespace(materialize_sources=lambda *args: tuple(reversed(rows)))
    with patch.object(fx.composer, "validate_verification_sources", return_value=None):
        invocation, material = fx.composer._material(fx.claim, fx.binding, fx.support, fx.context)
    assert {f["exact_source_ref"]: f["material"] for f in material["facts"]} == bodies
    assert all(set(f) == {"exact_source_ref", "material"} for f in material["facts"])
    assert {r.source_ref.exact_source_ref: r.material for r in rows} == bodies
    assert material["admissible_positive_basis_refs"] == list(composition.admissible_positive_basis_refs(
        fx.binding, fx.support, invocation.verifier_project_input_basis.project_input_basis_ref))


def _legacy_invocation(invocation):
    data = invocation.model_dump(mode="json")
    data["deterministic_validation_facts"].pop("verifier_prompt_profile_sha256")
    data["deterministic_validation_facts"].pop("verifier_output_cap_v1", None)
    data["deterministic_validation_facts"].pop("verifier_reply_ref_order_v1", None)
    data["verifier_semantic_input_sha256"] = build_verifier_semantic_input_sha256(data)
    return VerifierClaimInputSchema.model_validate(data)


def test_new_prompt_identity_and_task_projection_preserve_broad_issue_universe(tmp_path):
    fx = enriched_binding(fixture(tmp_path, support_relation=SupportRelation.NOT_SUPPORTED))
    fx.composer.model_gateway = Gateway()
    invocation, material = model_material(fx)  # Existing fixture intentionally has no new material key.
    assert "admissible_positive_basis_refs" not in material
    package, _ = fx.composer._package(material, occurrence="clarification://package",
        invocation=invocation, validate=lambda: None)
    binding = package.instruction_binding
    assert binding.system_instruction_version == "v2"
    assert binding.role_instruction_version == "v2-positive-provenance-v1"
    assert binding.prompt_template_version == "v2-positive-provenance-v1"
    assert binding.role_instruction_sha256 != OLD_ROLE_SHA256
    assert binding.prompt_template_sha256 != OLD_TEMPLATE_SHA256
    messages = package.model_input.final_input_material.inline_final_input_payload["messages"]
    tasks = [json.loads(m["content"]) for m in messages
             if m["content"].lstrip().startswith("{") and "allowed_authority_refs" in m["content"]]
    assert len(tasks) == 1
    task = tasks[0]
    assert task["allowed_authority_refs"] == list(invocation.allowed_authority_refs)
    assert task["admissible_positive_basis_refs"] == list(composition.admissible_positive_basis_refs(
        fx.binding, fx.support, invocation.verifier_project_input_basis.project_input_basis_ref))
    assert "fact_snapshot:i5" in task["allowed_authority_refs"]
    assert "fact_snapshot:i5" not in task["admissible_positive_basis_refs"]
    legacy = _legacy_invocation(invocation)
    assert legacy.verifier_semantic_input_sha256 != invocation.verifier_semantic_input_sha256
    assert legacy.authority_universe_sha256 == invocation.authority_universe_sha256
    assert legacy.verifier_policy_version == invocation.verifier_policy_version == "v2"
    assert legacy.response_contract == invocation.response_contract == "canonical_claim_verifier_v2"


@pytest.mark.parametrize("change", ["instruction_version", "role_content_hash", "fact_presentation_contract"])
def test_changed_prompt_profile_rejects_old_package_and_fresh_build_uses_new_physical_identity(tmp_path, change):
    fx = fixture(tmp_path)
    fx.composer.model_gateway = gateway = Gateway()
    old_invocation, old_material = model_material(fx)
    cls = type(fx.composer)
    old_profile = cls.prompt_profile_sha256()
    if change == "instruction_version":
        changed = patch.object(cls, "INSTRUCTION_VERSION", "v2-positive-provenance-test-change")
    elif change == "fact_presentation_contract":
        changed = patch.object(cls, "FACT_PRESENTATION_CONTRACT", "canonical-fact-exact-ref-test-change")
    else:
        system, role, template = cls._instructions()
        changed = patch.object(cls, "_instructions", staticmethod(
            lambda: (system, role + " Controlled hash change.", template)))
    with changed, patch.object(gateway, "routing_capabilities", wraps=gateway.routing_capabilities) as capabilities:
        assert cls.prompt_profile_sha256() != old_profile
        with pytest.raises(composition.TargetClaimVerifierError, match="current prompt profile"):
            fx.composer._package(old_material, occurrence="clarification://stale-profile",
                invocation=old_invocation, validate=lambda: None)
        assert capabilities.call_count == 0
        assert gateway.calls == []
        fresh_invocation, fresh_material = model_material(fx)
        assert fresh_invocation.deterministic_validation_facts["verifier_prompt_profile_sha256"] == cls.prompt_profile_sha256()
        assert fresh_invocation.verifier_semantic_input_sha256 != old_invocation.verifier_semantic_input_sha256
        assert fresh_invocation.authority_universe_sha256 == old_invocation.authority_universe_sha256
        old_key = canonical_sha256({"contract": "verifier-physical-occurrence-v2",
            "verifier_semantic_input_sha256": old_invocation.verifier_semantic_input_sha256})
        fresh_key = canonical_sha256({"contract": "verifier-physical-occurrence-v2",
            "verifier_semantic_input_sha256": fresh_invocation.verifier_semantic_input_sha256})
        assert fresh_key != old_key
        with patch.object(fx.composer, "_material", return_value=(fresh_invocation, fresh_material)):
            candidate = fx.composer._evaluate(fx.claim, fx.binding, fx.support, fx.context)
        assert len(gateway.calls) == 1  # In-process offline Gateway; no Provider or network.
        request = gateway.calls[0]
        assert request.model_call_id == "model_call_verifier_" + fresh_key
        assert candidate.verification_occurrence_ref == "claim-verification-v2://sha256/" + fresh_key
        assert request.extra["prompt_version"] == cls.INSTRUCTION_VERSION


@pytest.mark.parametrize("state", ["completed", "failed", "intent_only"])
def test_legacy_physical_receipt_presence_blocks_new_prompt_dispatch_without_rewriting(tmp_path, state):
    fx = fixture(tmp_path)
    fx.composer.model_gateway = gateway = Gateway()
    invocation, material = model_material(fx)
    legacy = _legacy_invocation(invocation)
    old_material = dict(material, invocation=legacy.model_dump(mode="json"))
    key = canonical_sha256({"contract": "verifier-physical-occurrence-v2",
        "verifier_semantic_input_sha256": legacy.verifier_semantic_input_sha256})
    input_ref = f"artifact://canonical-verifier-v2/{key}/input"
    result_ref = f"artifact://canonical-verifier-v2/{key}/candidate"
    identity = {"task_id": fx.runtime.lifecycle_service.load_run_identity(fx.context.active_lineage.run_id).task_id,
                "run_id": fx.context.active_lineage.run_id, "section_id": fx.claim.section_id}
    # The existence fence precedes parsing/upgrading legacy Context or candidates.
    # Minimal receipts exercise that boundary even if a historical payload is incomplete.
    old_input = {"schema_version": "canonical_verifier_input_v2", "input_material": old_material,
                 "input_fingerprint": legacy.verifier_semantic_input_sha256,
                 "context_package": {"frozen_legacy_payload": True}}
    fx.composer.store.put_json(old_input, artifact_ref=input_ref, artifact_type="canonical_verifier_input",
        schema_version="canonical_verifier_input_v2", created_by="legacy-test-owner", **identity)
    old_result = None
    if state != "intent_only":
        old_result = {"schema_version": "canonical_verifier_candidate_v2",
                      "input_fingerprint": legacy.verifier_semantic_input_sha256, "outcome": state}
        if state == "failed":
            old_result["failure"] = {"message": "Frozen old failure", "error_code": "OLD_FAILURE",
                                     "category": "validation", "origin": "unknown"}
        else:
            candidate, semantic = composition.TargetClaimVerifierComposition.parse_candidate(
                {"verdict": "UNSUPPORTED", "positive_basis_refs": [], "rationale": "Frozen old semantics", "issues": []},
                invocation=legacy, occurrence="claim-verification-v2://sha256/" + key, receipt_ref=result_ref)
            old_result.update(candidate=candidate.model_dump(mode="json"), semantic_result=semantic)
        fx.composer.store.put_json(old_result, artifact_ref=result_ref, artifact_type="canonical_verifier_candidate",
            schema_version="canonical_verifier_candidate_v2", created_by="legacy-test-owner", **identity)
    existing_references = fx.composer.store.list_references()
    with patch.object(fx.composer, "_material", return_value=(invocation, material)):
        with pytest.raises(composition.TargetClaimVerifierError):
            fx.composer._evaluate(fx.claim, fx.binding, fx.support, fx.context)
    assert gateway.calls == []
    assert fx.composer.store.get_json(input_ref, **identity) == old_input
    if old_result is not None:
        assert fx.composer.store.get_json(result_ref, **identity) == old_result
    assert fx.composer.store.list_references() == existing_references


def test_only_exact_old_typed_result_shape_is_readable_while_old_candidate_cannot_upgrade(tmp_path):
    fx = fixture(tmp_path)
    current_builder = fx.composer.build_input
    def old_builder(*args, **kwargs):
        # Construct recorded absence before hashing; never remove registration
        # from a persisted new root and call it historical.
        kwargs.update(_recorded_recovery_facts={}, _recorded_output_cap=None,
            _recorded_reply_ref_order=None)
        invocation, project = current_builder(*args, **kwargs)
        return _legacy_invocation(invocation), project
    legacy, _ = old_builder(fx.claim, fx.binding, fx.support, fx.context)
    assert legacy.deterministic_validation_facts == {
        "binding_semantic_fingerprint": fx.binding.binding_semantic_fingerprint}
    assert legacy.verifier_semantic_input_sha256 == build_verifier_semantic_input_sha256(legacy)
    key = canonical_sha256({"contract": "verifier-physical-occurrence-v2",
        "verifier_semantic_input_sha256": legacy.verifier_semantic_input_sha256})
    occurrence = "claim-verification-v2://sha256/" + key
    candidate_ref = "artifact://canonical-verifier-v2/" + key + "/candidate"
    candidate, _ = composition.TargetClaimVerifierComposition.parse_candidate({"verdict": "UNSUPPORTED",
        "positive_basis_refs": [], "rationale": "Historical typed semantics", "issues": []},
        invocation=legacy, occurrence=occurrence, receipt_ref=candidate_ref)
    # Produce a legitimate old-shaped committed result in a fresh temporary owner.
    with patch.object(fx.composer, "build_input", side_effect=old_builder):
        committed = fx.runtime.commit_verification(fx.claim.claim_ref, fx.binding.binding_facts_ref,
            fx.support.support_ref, candidate, None, fx.context)
    old_result = committed.verification_result
    assert old_result.deterministic_validation_facts == {
        "binding_semantic_fingerprint": fx.binding.binding_semantic_fingerprint}
    assert old_result.verifier_semantic_input_sha256 == legacy.verifier_semantic_input_sha256
    assert old_result.verification_occurrence_ref == occurrence
    assert old_result.semantic_rationale_ref == candidate_ref
    old_candidate_bytes = candidate.model_dump_json()
    old_result_bytes = old_result.model_dump_json()
    ledger_bytes = fx.runtime.ledger_path.read_bytes()
    refs = fx.composer.store.list_references()
    artifact_bytes = {ref.artifact_ref: fx.composer.store._path_for_ref(ref.artifact_ref).read_bytes() for ref in refs}
    head = fx.runtime.resolve_claim_head(fx.claim.claim_ref)
    assert head.active_verification_revision_ref.verification_revision == 1
    fx.composer.model_gateway = gateway = Gateway()
    # Exact Result reads never enter today's reply parsing/normalization.
    with patch.object(composition.TargetClaimVerifierComposition, "parse_candidate",
            side_effect=AssertionError("historical Result must not be reparsed")), \
            patch.object(composition.TargetClaimVerifierComposition, "normalize_reply_reference_order",
                side_effect=AssertionError("historical Result must not be normalized")):
        assert fx.runtime.resolve_verification_result(old_result.verification_result_ref) == old_result
        fx.composer.validate_v2_identity(fx.claim, fx.binding, fx.support, old_result, fx.context.active_lineage)
        # Already committed historical operations retain exact owner replay.
        # This returns revision 1, rather than upgrading the old candidate into
        # a fresh current-profile commit; current candidate admission below fails.
        replay = fx.runtime.commit_verification(fx.claim.claim_ref, fx.binding.binding_facts_ref,
            fx.support.support_ref, candidate, None, fx.context)
        assert replay.verification_result == old_result
        assert replay.verification_revision == committed.verification_revision
        assert replay.resulting_claim_head == head
        assert replay.receipt == committed.receipt
        with pytest.raises(composition.TargetClaimVerifierError, match="^Verifier V2 semantic input is stale$") as error:
            fx.composer.validate_v2_identity(fx.claim, fx.binding, fx.support, candidate, fx.context.active_lineage)
        assert error.value.error_code == "CLAIM_VERIFIER_FAILED"
    foreign_facts = {**old_result.deterministic_validation_facts, "unexpected_legacy_fact": True}
    with pytest.raises(ValidationError, match="Verifier semantic input hash mismatch"):
        fx.composer.validate_v2_identity(fx.claim, fx.binding, fx.support,
            old_result.model_copy(update={"deterministic_validation_facts": foreign_facts}), fx.context.active_lineage)
    assert gateway.calls == []
    assert candidate.model_dump_json() == old_candidate_bytes
    assert old_result.model_dump_json() == old_result_bytes
    assert fx.runtime.ledger_path.read_bytes() == ledger_bytes
    assert fx.runtime.resolve_claim_head(fx.claim.claim_ref) == head
    assert fx.composer.store.list_references() == refs
    assert {ref.artifact_ref: fx.composer.store._path_for_ref(ref.artifact_ref).read_bytes() for ref in refs} == artifact_bytes
