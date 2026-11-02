"""V6 real DeepSeek badcase: immutable native Claim/Support/Verifier diagnosis.

All tests are offline. Never writes to old Run, starts a provider request,
coerces a real verdict, or grants a new Local Verifier Recovery policy.
"""
from __future__ import annotations

import base64
import json
from pathlib import Path

import pytest

from apps.enterprise_document.schemas.agent_rag_v1.claim_support import (
    ClaimEvidenceSupportRecordSchema,
    SupportRelation,
)
from apps.enterprise_document.schemas.agent_rag_v1.verification import (
    ClaimBindingFactsRecordSchema,
    VerifierSemanticResultCandidateSchema,
    VerificationVerdict,
    EVIDENCE_POSITIVE_BASIS_NOT_ADMITTED,
    verifier_local_correction_policy,
)
from apps.enterprise_document.services.agent_rag_v1.claim_verification_runtime import (
    ClaimBindingError,
    ClaimVerificationRuntime,
    VerifierCandidateContractViolationError,
)

ROOT = Path("D:/MyCode/_FPBCA_REAL_E2E_20261009_20261009T112450Z")
FAILED_CLAIM = (
    "claim://sha256/80ce841bd0f2df211e789796238739a5d1d65c248a4a0d40e72be51ed15e957f"
)
CANDIDATE_OCCURRENCE = (
    "47277f69e340e1b9be95e2e2d379ed93732557957dc36dbe7c4941e5faeaf1c2"
)


def _jsonl(path: Path) -> list[dict]:
    assert path.is_file(), f"missing exact frozen old Run file: {path}"
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def _frozen():
    journal = ROOT / "business-runtime/runs"
    support = next(
        item["record"]
        for item in _jsonl(journal / "claim_support.jsonl")
        if item["record"]["claim_support_subject_ref"]["subject_ref"] == FAILED_CLAIM
    )
    events = _jsonl(journal / "claim_verification.jsonl")
    claim = next(
        claim for event in events if event["event_type"] == "claim_records_committed"
        for claim in event["claim_records"]
        if claim["claim_ref"]["claim_id"] == FAILED_CLAIM
    )
    binding = next(
        event["record"]
        for event in events if event["event_type"] == "binding_facts_committed"
        if event["record"]["claim_ref"]["claim_id"] == FAILED_CLAIM
    )
    candidates = []
    for file in (journal / "artifacts").rglob("*.json"):
        wrapper = json.loads(file.read_text(encoding="utf-8"))
        ref = wrapper["reference"]["artifact_ref"]
        if CANDIDATE_OCCURRENCE in ref and ref.endswith("/candidate"):
            candidates.append(json.loads(base64.b64decode(wrapper["content_base64"]).decode("utf-8")))
    assert len(candidates) == 1
    response = candidates[0]
    assert response["schema_version"] == "canonical_verifier_candidate_v2"
    assert response["outcome"] == "completed"
    result = VerifierSemanticResultCandidateSchema.model_validate(response["candidate"])
    return (
        claim,
        ClaimBindingFactsRecordSchema.model_validate(binding),
        ClaimEvidenceSupportRecordSchema.model_validate(support),
        result,
        response,
    )


def test_exact_frozen_claim_not_an_unambiguous_confirmed_project_parameter():
    claim, binding, support, candidate, response = _frozen()
    assert claim["claim_type"] == "current_project_fact"
    value = claim["normalized_value"]
    assert "上述信息的缺失将直接影响" in value
    assert "资源规模测算" in value
    assert "规划假设或建议性设计" in value
    assert "不视为本项目已确认或已完成的建设事实" in value
    facts = binding.facts
    assert len(facts) == 7
    assert {f.relation.value for f in facts} == {"CONTEXT_ONLY"}
    assert len([f for f in facts if f.source_kind == "FACT"]) == 2
    assert len([f for f in facts if f.source_kind == "EVIDENCE"]) == 3
    assert not support.supporting_refs
    assert not support.contradicting_refs
    assert support.relation is SupportRelation.INDETERMINATE


def test_real_original_verifier_verdict_disagrees_with_shared_support():
    claim, binding, support, candidate, response = _frozen()
    assert candidate.recommended_verdict is VerificationVerdict.VERIFIED
    assert any(
        issue.issue_code == "SHARED_SUPPORT_INDETERMINATE"
        for issue in candidate.issue_candidates
    )
    kinds = {ref.exact_source_ref: ref.source_kind for ref in binding.exact_authorized_source_refs}
    positive = set(candidate.positive_basis_refs)
    assert positive
    assert candidate.verifier_project_input_basis.project_input_basis_ref in positive
    assert len([ref for ref in positive if kinds.get(ref) == "FACT"]) == 2
    assert not [ref for ref in positive if kinds.get(ref) == "EVIDENCE"]
    assert candidate.claim_ref.claim_id == claim["claim_ref"]["claim_id"]
    assert candidate.claim_evidence_support_ref == support.support_ref


def test_original_prod_m6_guard_rejects_without_synthesizing_recovery():
    _claim, binding, support, candidate, _response = _frozen()
    with pytest.raises(ClaimBindingError, match="VERIFIED forbidden by unresolved/contradictory shared Support") as error:
        ClaimVerificationRuntime.validate_v2_provenance(binding, support, candidate)
    # An ineligible generic admission failure is not an old V1 correctable
    # EVIDENCE-positive-basis violation.
    assert not isinstance(error.value, VerifierCandidateContractViolationError)
    policy = verifier_local_correction_policy()
    assert policy.eligible_rule == EVIDENCE_POSITIVE_BASIS_NOT_ADMITTED
    assert policy.max_additional_calls == 1


def test_hypothetical_non_verified_is_guard_admissible_but_not_a_real_verdict():
    _claim, binding, support, candidate, _response = _frozen()
    # Counterfactual pure in-memory variant ONLY. The native response is untouched.
    downgraded = candidate.model_copy(
        update={"recommended_verdict": VerificationVerdict.UNSUPPORTED},
    )
    ClaimVerificationRuntime.validate_v2_provenance(binding, support, downgraded)
    assert candidate.recommended_verdict is VerificationVerdict.VERIFIED


def test_support_not_supported_counterfactual_cannot_be_a_legitimate_fix():
    _claim, binding, support, candidate, _response = _frozen()
    hypothetical = support.model_copy(
        update={"relation": SupportRelation.NOT_SUPPORTED}
    )
    # Only a mechanical admission result, not semantic truth; this shows why
    # changing the committed Support enum would be especially dangerous.
    ClaimVerificationRuntime.validate_v2_provenance(binding, hypothetical, candidate)
    assert support.relation is SupportRelation.INDETERMINATE


def test_exact_failed_run_remains_terminal_without_an_existing_slot():
    process = json.loads((ROOT / "audit/PROCESS_RECEIPT.json").read_text(encoding="utf-8"))
    execution = json.loads((ROOT / "business-runtime/execution.json").read_text(encoding="utf-8"))
    assert process["business_status"] == "failed"
    assert process["provider_attempts"] == 36
    assert not process["timed_out"]
    assert process["resources"][0]["active_workers"] == 0
    assert execution["terminal_outcome"]["status"] == "TECHNICAL_FAILURE"
    assert execution["terminal_outcome"]["recovery_attempts"] == 0
    assert not execution["terminal_outcome"]["publication_exists"]
    events = _jsonl(ROOT / "business-runtime/runs/claim_verification.jsonl")
    assert all(item["event_type"] not in {
        "verifier_correction_authorized", "verifier_candidate_rejected"
    } for item in events)
