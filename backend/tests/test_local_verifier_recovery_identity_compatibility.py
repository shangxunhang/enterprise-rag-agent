"""Exact immutable historical Result reads and unchanged prospective Candidate guards."""
from copy import deepcopy
from unittest.mock import patch

import pytest

from apps.enterprise_document.schemas.agent_rag_v1.verification import (
    VERIFIER_LOCAL_REGISTRATION_KEY, VerifierClaimInputSchema, build_verifier_semantic_input_sha256,
)
from apps.enterprise_document.services.agent_rag_v1.claim_verifier_composition import TargetClaimVerifierError
from backend.tests.local_verifier_recovery_helpers import create_fixture, provider_rows


PROMPT = "verifier_prompt_profile_sha256"
CAP = "verifier_output_cap_v1"
ENTRY = "verifier_reply_ref_order_v1"


def _without(invocation, fields):
    data = invocation.model_dump(mode="json")
    for field in fields:
        data["deterministic_validation_facts"].pop(field, None)
    data["verifier_semantic_input_sha256"] = build_verifier_semantic_input_sha256(data)
    return VerifierClaimInputSchema.model_validate(data)


def _candidate(fx, invocation):
    return fx.composer.parse_candidate({"verdict": "UNSUPPORTED", "positive_basis_refs": [],
        "rationale": "Exact historical owner judgment", "issues": []}, invocation=invocation,
        occurrence="historical-verifier://exact-candidate", receipt_ref="artifact://historical-verifier-judgment")[0]


@pytest.mark.parametrize("missing", [(ENTRY,), (CAP, ENTRY), (PROMPT, CAP, ENTRY)])
def test_truly_absent_historical_result_reads_exact_profiles_but_candidate_cannot_upgrade(tmp_path, missing):
    fx = create_fixture(tmp_path)
    build = fx.composer.build_input
    invocation, _ = build(fx.claim, fx.binding, fx.support, fx.context, _recorded_recovery_facts={})
    old = _without(invocation, missing)
    candidate = _candidate(fx, old)
    # A fresh owner seeded under its actual former input-builder shape. Every
    # source/currentness/provenance/commit guard remains the real owner method.
    def former_builder(*args, **kwargs):
        material, project = build(*args, **kwargs)
        return _without(material, (*missing, VERIFIER_LOCAL_REGISTRATION_KEY)), project
    with patch.object(fx.composer, "build_input", side_effect=former_builder):
        result = fx.runtime.commit_verification(fx.claim.claim_ref, fx.binding.binding_facts_ref,
            fx.support.support_ref, candidate, None, fx.context).verification_result
    assert VERIFIER_LOCAL_REGISTRATION_KEY not in result.deterministic_validation_facts
    if PROMPT in missing:
        assert result.deterministic_validation_facts == {"binding_semantic_fingerprint": fx.binding.binding_semantic_fingerprint}
    frozen, ledger = deepcopy(result.model_dump(mode="json")), fx.runtime.ledger_path.read_bytes()
    fx.composer.validate_v2_identity(fx.claim, fx.binding, fx.support, result, fx.context.active_lineage)
    with pytest.raises(TargetClaimVerifierError, match="stale"):
        fx.composer.validate_v2_identity(fx.claim, fx.binding, fx.support, candidate, fx.context.active_lineage)
    assert result.model_dump(mode="json") == frozen
    assert fx.runtime.ledger_path.read_bytes() == ledger
    assert provider_rows(fx) == []


def test_current_full_profile_input_without_registration_stays_usable_without_recovery(tmp_path):
    fx = create_fixture(tmp_path)
    invocation, _ = fx.composer.build_input(fx.claim, fx.binding, fx.support, fx.context, _recorded_recovery_facts={})
    candidate = _candidate(fx, invocation)
    fx.composer.validate_v2_identity(fx.claim, fx.binding, fx.support, candidate, fx.context.active_lineage)
    result = fx.runtime.commit_verification(fx.claim.claim_ref, fx.binding.binding_facts_ref,
        fx.support.support_ref, candidate, None, fx.context).verification_result
    assert VERIFIER_LOCAL_REGISTRATION_KEY not in result.deterministic_validation_facts
    with pytest.raises(ValueError, match="prospective"):
        from apps.enterprise_document.schemas.agent_rag_v1.verification import build_verifier_original_root
        build_verifier_original_root(invocation)
    assert provider_rows(fx) == []


def test_immutable_recorded_cap_is_readable_after_new_cap_but_old_candidate_is_stale(tmp_path):
    fx = create_fixture(tmp_path)
    invocation, _ = fx.composer.build_input(fx.claim, fx.binding, fx.support, fx.context, _recorded_recovery_facts={})
    candidate = _candidate(fx, invocation)
    result = fx.runtime.commit_verification(fx.claim.claim_ref, fx.binding.binding_facts_ref,
        fx.support.support_ref, candidate, None, fx.context).verification_result
    ledger = fx.runtime.ledger_path.read_bytes()
    with patch.object(fx.composer, "effective_output_cap", return_value=512):
        fx.composer.validate_v2_identity(fx.claim, fx.binding, fx.support, result, fx.context.active_lineage)
        with pytest.raises(TargetClaimVerifierError, match="stale"):
            fx.composer.validate_v2_identity(fx.claim, fx.binding, fx.support, candidate, fx.context.active_lineage)
    assert fx.runtime.ledger_path.read_bytes() == ledger
    assert provider_rows(fx) == []


@pytest.mark.parametrize("missing", [(ENTRY,), (CAP, ENTRY), (PROMPT, CAP, ENTRY)])
def test_missing_older_profiles_never_strips_actual_new_registration_from_result(tmp_path, missing):
    fx = create_fixture(tmp_path)
    build = fx.composer.build_input
    registered, _ = build(fx.claim, fx.binding, fx.support, fx.context)
    old = _without(registered, missing)
    candidate = _candidate(fx, old)
    def recorded_builder(*args, **kwargs):
        material, project = build(*args, **kwargs)
        return _without(material, missing), project
    with patch.object(fx.composer, "build_input", side_effect=recorded_builder):
        result = fx.runtime.commit_verification(fx.claim.claim_ref, fx.binding.binding_facts_ref,
            fx.support.support_ref, candidate, None, fx.context).verification_result
    registration = deepcopy(result.deterministic_validation_facts[VERIFIER_LOCAL_REGISTRATION_KEY])
    ledger = fx.runtime.ledger_path.read_bytes()
    fx.composer.validate_v2_identity(fx.claim, fx.binding, fx.support, result, fx.context.active_lineage)
    with pytest.raises(TargetClaimVerifierError, match="stale"):
        fx.composer.validate_v2_identity(fx.claim, fx.binding, fx.support, candidate, fx.context.active_lineage)
    assert result.deterministic_validation_facts[VERIFIER_LOCAL_REGISTRATION_KEY] == registration
    assert fx.runtime.ledger_path.read_bytes() == ledger
    assert provider_rows(fx) == []
