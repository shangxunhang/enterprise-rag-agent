"""Phase 1 strict prospective contracts; no Provider or historical owner opens.

The existing fresh production-owner fixture supplies exact typed identities.
These are schema/identity tests, not production correction-flow acceptance.
"""
from copy import deepcopy
import json

import pytest

from core.hashing import canonical_sha256
from apps.enterprise_document.schemas.agent_rag_v1.verification import (
    VERIFIER_LOCAL_REGISTRATION_KEY, VERIFIER_LOCAL_CORRECTION_KEY,
    VerifierLocalCorrectionPolicyV1, VerifierOriginalRootV1,
    VerifierCandidateContractViolationV1, VerifierRecoveryArtifactChainV1,
    VerifierCandidateRejectedV1, VerifierCorrectionAuthorizedV1,
    VerifierCorrectionTerminatedV1, VerifierLocalCorrectionBindingV1,
    ClaimVerifierRecoveryControlEventV1, VerifierClaimInputSchema,
    build_verifier_local_correction_registration, verifier_local_correction_policy,
    build_verifier_semantic_input_sha256, build_verifier_registration_scope_sha256,
    build_verifier_original_root, verifier_recovery_operation_id,
    build_verifier_candidate_contract_violation,
    build_verifier_candidate_rejection, build_verifier_correction_authorization,
    build_verifier_correction_binding, build_verifier_correction_termination,
    build_verifier_recovery_control_event, build_verifier_recovery_feedback,
    verifier_feedback_sha256, validate_verifier_local_recovery_facts,
)
from backend.tests.test_bca014_i5_verifier_project_input_basis import fixture


def _input(fx, *, registered=True):
    data = fx.invocation.model_dump(mode="json")
    if registered:
        data["deterministic_validation_facts"][VERIFIER_LOCAL_REGISTRATION_KEY] = (
            build_verifier_local_correction_registration(data).model_dump(mode="json"))
    else:
        data["deterministic_validation_facts"].pop(VERIFIER_LOCAL_REGISTRATION_KEY, None)
    data["verifier_semantic_input_sha256"] = build_verifier_semantic_input_sha256(data)
    return VerifierClaimInputSchema.model_validate(data)


@pytest.fixture
def chain(tmp_path):
    fx = fixture(tmp_path)
    invocation = _input(fx)
    root = build_verifier_original_root(invocation)
    key = root.original_physical_occurrence.rsplit("/", 1)[-1]
    prefix = f"artifact://canonical-verifier-v2/{key}"
    reply = {"verdict": "VERIFIED", "positive_basis_refs": ["evidence:C1"],
        "rationale": "Schema fixture independent complete candidate", "issues": []}
    candidate, _ = fx.composer._parse_candidate_strict(reply, invocation=invocation,
        occurrence=root.original_physical_occurrence, receipt_ref=prefix + "/candidate")
    request = {"model_call_id": "model_call_verifier_" + key, "task_id": "test-task",
        "run_id": invocation.active_lineage.run_id, "messages": [{"role": "system", "content": "All original constraints"}]}
    response = {**{field: request[field] for field in ("model_call_id", "task_id", "run_id")},
        "success": True, "content": json.dumps(reply), "created_at": "schema-test"}
    artifact_chain = VerifierRecoveryArtifactChainV1(input_ref=prefix + "/input",
        input_sha256=canonical_sha256(invocation.model_dump(mode="json")), request_ref=prefix + "/request",
        request_sha256=canonical_sha256(request), response_ref=prefix + "/response", response_sha256=canonical_sha256(response),
        candidate_ref=prefix + "/candidate", candidate_sha256=canonical_sha256(candidate.model_dump(mode="json")),
        physical_occurrence=root.original_physical_occurrence)
    admissible = (invocation.verifier_project_input_basis.project_input_basis_ref,)
    violation = build_verifier_candidate_contract_violation(original_input_ref=artifact_chain.input_ref,
        original_input_sha256=artifact_chain.input_sha256, candidate_ref=artifact_chain.candidate_ref,
        candidate_sha256=artifact_chain.candidate_sha256, violating_refs=("evidence:C1",),
        allowed_authority_refs=invocation.allowed_authority_refs,
        allowed_authority_sha256=canonical_sha256(invocation.allowed_authority_refs),
        admissible_positive_basis_refs=admissible, admissible_positive_basis_sha256=canonical_sha256(admissible),
        binding_facts_ref=invocation.binding_facts_ref, claim_evidence_support_ref=invocation.claim_evidence_support_ref)
    rejection = build_verifier_candidate_rejection(root=root, correction_ordinal=0, rejected_input=invocation,
        candidate=candidate, request=request, response=response, artifact_chain=artifact_chain, violation=violation)
    material = {"claim": fx.claim.model_dump(mode="json"), "paragraph": "Complete paragraph",
        "binding": fx.binding.model_dump(mode="json"), "support": fx.support.model_dump(mode="json"),
        "project_input": fx.project.model_dump(mode="json"), "allowed": list(invocation.allowed_authority_refs),
        "admissible": list(admissible)}
    feedback = build_verifier_recovery_feedback(violation, material, request["messages"])
    grant = build_verifier_correction_authorization(root=root, rejection_ref=rejection.rejection_ref,
        rejection_sha256=rejection.rejection_sha256, feedback=feedback)
    binding = build_verifier_correction_binding(grant)
    termination = build_verifier_correction_termination(root=root, authorization_event_ref=grant.authorization_event_ref,
        grant_sha256=grant.grant_sha256, reason_code="PROVIDER_OUTCOME_UNKNOWN", reason_ref=prefix + "/response")
    return {"fx": fx, "input": invocation, "root": root, "rejection": rejection, "grant": grant,
        "binding": binding, "termination": termination, "feedback": feedback, "material": material}


def _child(chain):
    data = chain["input"].model_dump(mode="json")
    data["deterministic_validation_facts"].pop(VERIFIER_LOCAL_REGISTRATION_KEY)
    data["deterministic_validation_facts"][VERIFIER_LOCAL_CORRECTION_KEY] = chain["binding"].model_dump(mode="json")
    data["verifier_semantic_input_sha256"] = build_verifier_semantic_input_sha256(data)
    return VerifierClaimInputSchema.model_validate(data)


def test_policy_is_exact_prospective_and_single_slot():
    policy = verifier_local_correction_policy()
    assert policy.policy_id == "verifier_local_correction_policy_v1"
    assert policy.eligible_rule == "EVIDENCE_POSITIVE_BASIS_NOT_ADMITTED"
    assert policy.max_additional_calls == 1
    assert VerifierLocalCorrectionPolicyV1.model_validate(policy.model_dump(mode="json")) == policy


@pytest.mark.parametrize("field,value", [("policy_id", "v2"), ("eligible_rule", "OTHER"),
    ("max_additional_calls", 2), ("policy_sha256", "0" * 64), ("random_id", "split")])
def test_policy_unknown_semantics_and_hash_tamper_rejected(field, value):
    data = verifier_local_correction_policy().model_dump(mode="json")
    data[field] = value
    with pytest.raises(ValueError):
        VerifierLocalCorrectionPolicyV1.model_validate(data)


@pytest.mark.parametrize("change", ["auth", "policy", "feedback", "prompt", "random"])
def test_registration_mutex_excludes_mutable_profiles(chain, change):
    data = chain["input"].model_dump(mode="json")
    before = build_verifier_registration_scope_sha256(data)
    if change == "auth":
        data["verification_authorization_ref"] = "another-auth"
    else:
        data["deterministic_validation_facts"][change] = "different"
    assert build_verifier_registration_scope_sha256(data) == before


@pytest.mark.parametrize("field", ["run_id", "attempt_id", "attempt_scope_ref", "input_snapshot_version"])
def test_registration_mutex_binds_exact_execution_scope(chain, field):
    data = chain["input"].model_dump(mode="json")
    data["active_lineage"][field] = "different"
    assert build_verifier_registration_scope_sha256(data) != build_verifier_registration_scope_sha256(chain["input"])


def test_registration_is_true_semantic_input_and_old_input_remains_absent(chain):
    old = _input(chain["fx"], registered=False)
    assert old.verifier_semantic_input_sha256 != chain["input"].verifier_semantic_input_sha256
    assert validate_verifier_local_recovery_facts(old) == (None, None)
    assert VERIFIER_LOCAL_REGISTRATION_KEY not in old.model_dump(mode="json")["deterministic_validation_facts"]
    with pytest.raises(ValueError):
        build_verifier_original_root(old)


def test_root_fixed_initial_identity_and_child_cannot_become_root(chain):
    assert build_verifier_original_root(chain["input"]) == chain["root"]
    with pytest.raises(ValueError):
        build_verifier_original_root(_child(chain))


@pytest.mark.parametrize("field", ["original_input_ref", "original_input_sha256", "original_physical_occurrence",
    "original_root_ref", "original_root_sha256", "random_id"])
def test_root_tamper_fail_closed(chain, field):
    data = chain["root"].model_dump(mode="json")
    data[field] = "a" * 64
    with pytest.raises(ValueError):
        VerifierOriginalRootV1.model_validate(data)


def test_operation_namespaces_independent_fixed_slot_and_no_random_argument(chain):
    root = chain["root"]
    reject = verifier_recovery_operation_id("verifier_candidate_rejected", root, ordinal=0,
        candidate_ref=chain["rejection"].artifact_chain.candidate_ref)
    grant = verifier_recovery_operation_id("verifier_correction_authorized", root)
    terminate = verifier_recovery_operation_id("verifier_correction_terminated", root)
    assert len({reject, grant, terminate}) == 3
    assert grant == verifier_recovery_operation_id("verifier_correction_authorized", root.original_root_ref)
    with pytest.raises(ValueError):
        verifier_recovery_operation_id("verifier_correction_authorized", root, candidate_ref="new-id")
    with pytest.raises(ValueError):
        verifier_recovery_operation_id("verifier_correction_authorized", root, ordinal=2)
    with pytest.raises(ValueError):
        verifier_recovery_operation_id("unknown", root)


@pytest.mark.parametrize("kind", ["rejection", "grant", "termination"])
def test_control_event_roundtrip_is_distinct_from_result_v2(chain, kind):
    event = build_verifier_recovery_control_event(chain[kind])
    assert ClaimVerifierRecoveryControlEventV1.model_validate_json(event.model_dump_json()) == event
    assert event.schema_version == "claim_verifier_recovery_control_event_v1"
    assert "verification_result_ref" not in event.model_dump(mode="json")


@pytest.mark.parametrize("field", ["schema_version", "event_type", "operation_id", "semantic_fingerprint", "result_ref", "extra"])
def test_control_envelope_tamper_unknown_type_fail_closed(chain, field):
    data = build_verifier_recovery_control_event(chain["grant"]).model_dump(mode="json")
    data[field] = "b" * 64
    with pytest.raises(ValueError):
        ClaimVerifierRecoveryControlEventV1.model_validate(data)


@pytest.mark.parametrize("field", ["original_input_sha256", "candidate_sha256", "allowed_authority_sha256",
    "admissible_positive_basis_sha256", "rule_code", "detection_policy_version", "rejection_stage", "extra"])
def test_typed_violation_tamper_fail_closed(chain, field):
    data = chain["rejection"].violation.model_dump(mode="json")
    data[field] = "c" * 64
    with pytest.raises(ValueError):
        VerifierCandidateContractViolationV1.model_validate(data)


@pytest.mark.parametrize("field", ["root", "correction_ordinal", "candidate", "request", "response", "artifact_chain", "violation"])
def test_rejection_parent_content_tamper_fail_closed(chain, field):
    data = chain["rejection"].model_dump(mode="json")
    if field == "correction_ordinal":
        data[field] = 1
    elif field == "root":
        data[field]["original_input_sha256"] = "d" * 64
    elif field == "candidate":
        data[field]["semantic_rationale_hash"] = "d" * 64
    elif field == "request":
        data[field]["model_call_id"] = "another"
    elif field == "response":
        data[field]["success"] = False
    elif field == "artifact_chain":
        data[field]["candidate_ref"] = "another"
    else:
        data[field]["violating_refs"] = ["another"]
    with pytest.raises(ValueError):
        VerifierCandidateRejectedV1.model_validate(data)


def test_feedback_actual_bytes_full_material_and_all_verdicts(chain):
    feedback = chain["feedback"]
    assert feedback == build_verifier_recovery_feedback(chain["rejection"].violation,
        chain["material"], chain["rejection"].request["messages"])
    assert chain["grant"].feedback_sha256 == verifier_feedback_sha256(feedback)
    assert verifier_feedback_sha256(feedback + " ") != verifier_feedback_sha256(feedback)
    for text in ("evidence:C1", "not establish", "VERIFIED, UNSUPPORTED, CONFLICTED", "REJECTED", "Complete paragraph", "All original constraints"):
        assert text in feedback


@pytest.mark.parametrize("field", ["rejection_ref", "rejection_sha256", "policy", "authorization_ref",
    "authorization_sha256", "feedback", "feedback_sha256", "expected_head_revision", "correction_ordinal",
    "authorization_event_ref", "grant_sha256", "extra"])
def test_grant_parent_policy_auth_feedback_hash_tamper_fail_closed(chain, field):
    data = chain["grant"].model_dump(mode="json")
    if field == "policy":
        data[field]["policy_sha256"] = "e" * 64
    elif field in {"correction_ordinal", "expected_head_revision"}:
        data[field] = 2
    else:
        data[field] = "e" * 64
    with pytest.raises(ValueError):
        VerifierCorrectionAuthorizedV1.model_validate(data)


def test_child_binding_new_semantic_identity_and_original_auth_retained(chain):
    child = _child(chain)
    assert child.verifier_semantic_input_sha256 != chain["input"].verifier_semantic_input_sha256
    assert child.verification_authorization_ref == chain["input"].verification_authorization_ref
    assert child.verification_authorization_ref != chain["grant"].authorization_ref
    registration, binding = validate_verifier_local_recovery_facts(child)
    assert registration is None and binding == chain["binding"]


@pytest.mark.parametrize("mutation", ["binding_hash", "both_bindings", "auth", "support", "writer", "scope", "policy", "response", "facts"])
def test_child_invalid_binding_or_changed_sources_fail_closed(chain, mutation):
    data = _child(chain).model_dump(mode="json")
    if mutation == "binding_hash":
        data["deterministic_validation_facts"][VERIFIER_LOCAL_CORRECTION_KEY]["binding_sha256"] = "f" * 64
    elif mutation == "both_bindings":
        data["deterministic_validation_facts"][VERIFIER_LOCAL_REGISTRATION_KEY] = chain["input"].deterministic_validation_facts[VERIFIER_LOCAL_REGISTRATION_KEY]
    elif mutation == "auth":
        data["verification_authorization_ref"] = "another"
    elif mutation == "support":
        data["claim_evidence_support_ref"]["support_semantic_fingerprint"] = "f" * 64
    elif mutation == "writer":
        data["verifier_project_input_basis"]["project_input_sha256"] = "f" * 64
    elif mutation == "scope":
        data["active_lineage"]["attempt_scope_ref"] = "another"
    elif mutation == "policy":
        data["verifier_policy_version"] = "another"
    elif mutation == "response":
        data["response_contract"] = "another"
    else:
        data["deterministic_validation_facts"]["random_id"] = "another"
    data["verifier_semantic_input_sha256"] = build_verifier_semantic_input_sha256(data)
    with pytest.raises(ValueError):
        VerifierClaimInputSchema.model_validate(data)


@pytest.mark.parametrize("field", ["grant_sha256", "second_rejection_ref", "reason_code", "termination_sha256", "termination_ref", "extra"])
def test_termination_linkage_tamper_fail_closed(chain, field):
    data = chain["termination"].model_dump(mode="json")
    data[field] = "f" * 64
    with pytest.raises(ValueError):
        VerifierCorrectionTerminatedV1.model_validate(data)


def test_second_violation_requires_durable_parent_links(chain):
    with pytest.raises(ValueError):
        build_verifier_correction_termination(root=chain["root"], reason_code="SECOND_CONTRACT_VIOLATION", reason_ref="failure")


def test_binding_unknown_fields_and_hash_fail_closed(chain):
    data = chain["binding"].model_dump(mode="json")
    data["extra"] = "second-slot"
    with pytest.raises(ValueError):
        VerifierLocalCorrectionBindingV1.model_validate(data)


@pytest.mark.parametrize("key", [VERIFIER_LOCAL_REGISTRATION_KEY, VERIFIER_LOCAL_CORRECTION_KEY])
def test_explicit_null_recovery_fact_is_malformed_not_historical_absence(chain, key):
    data = _input(chain["fx"], registered=False).model_dump(mode="json")
    data["deterministic_validation_facts"][key] = None
    data["verifier_semantic_input_sha256"] = build_verifier_semantic_input_sha256(data)
    with pytest.raises(ValueError):
        VerifierClaimInputSchema.model_validate(data)
