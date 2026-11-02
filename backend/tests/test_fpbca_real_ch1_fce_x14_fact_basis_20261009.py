"""FCE X14 retrospective real DeepSeek counterexamples (read-only).

The frozen native M6 accepted four FACT/ProjectInput-positive candidates
which the current FCE X14 semantic checker flags false. This is an evaluation
contract mismatch; it is NOT evidence that their semantic claims are correct.
"""
import json
from pathlib import Path

import pytest

from apps.enterprise_document.schemas.agent_rag_v1.claim_support import (
    ClaimEvidenceSupportRecordSchema,
)
from apps.enterprise_document.schemas.agent_rag_v1.verification import (
    ClaimBindingFactsRecordSchema,
    VerifierSemanticResultCandidateSchema,
)
from apps.enterprise_document.services.agent_rag_v1.claim_verification_runtime import (
    ClaimVerificationRuntime,
)
from eval.full_chain.domains.generation_claim import check_generation_edge

BASE = Path("D:/MyCode/_FPBCA_REAL_E2E_20261009_20261009T112450Z/business-runtime/runs")


def _rows(filename):
    return [
        json.loads(line)
        for line in (BASE / filename).read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def _native():
    verification = _rows("claim_verification.jsonl")
    support = {
        row["record"]["claim_support_subject_ref"]["subject_ref"]: row["record"]
        for row in _rows("claim_support.jsonl")
    }
    binding = {
        row["record"]["claim_ref"]["claim_id"]: row["record"]
        for row in verification if row["event_type"] == "binding_facts_committed"
    }
    committed = [
        row for row in verification if row["event_type"] == "verification_committed"
    ]
    assert len(committed) == 5
    return support, binding, committed


@pytest.mark.parametrize(
    ("ordinal", "expected_native_fce"),
    [(1, False), (2, True), (3, False), (4, False), (5, False)],
)
def test_real_m6_admitted_verification_and_fce_x14_projection_disagree_for_fact_basis(
    ordinal, expected_native_fce,
):
    supports, bindings, committed = _native()
    event = committed[ordinal - 1]
    candidate = VerifierSemanticResultCandidateSchema.model_validate(
        event["verifier_candidate"]
    )
    result = event["verification_result"]
    cid = candidate.claim_ref.claim_id
    support = ClaimEvidenceSupportRecordSchema.model_validate(supports[cid])
    binding = ClaimBindingFactsRecordSchema.model_validate(bindings[cid])
    assert candidate.recommended_verdict.value == "VERIFIED"
    # This is the real M6 mechanical rule. Semantic truth is a separate Gold question.
    ClaimVerificationRuntime.validate_v2_provenance(binding, support, candidate)
    assert result["verdict"] == "VERIFIED"
    observation = check_generation_edge(
        "X14", {"support":supports[cid]}, {"result":result},
        subject_id="frozen-fpbca-ch1",
    )
    assert observation.reason == "NATIVE_VERIFIER_EXACT_SUPPORT_AND_POSITIVE_BASIS"
    assert observation.measurement_state == "MEASURED"
    assert observation.assertion_result is expected_native_fce
    if not expected_native_fce:
        # The incorrectly rejected member is one or more canonical FACT or
        # ProjectInput refs. Neither is required to be a Support.EVIDENCE ref.
        assert any(ref.startswith("fact-record://") for ref in result["positive_basis_refs"])
        assert result["project_input_basis_ref"] in result["positive_basis_refs"]


def test_frozen_evaluation_formal_report_four_x14_failure_flags_are_not_historical_m6_violations():
    evaluation = Path(
        "E:/rag-agent-evaluation/REAL_CH1_5STEP_FCE_20261009_20261009T140419Z"
        "/formal_full_chain_report/evaluation_report.json"
    )
    assert evaluation.is_file()
    report = json.loads(evaluation.read_text(encoding="utf-8"))
    x14 = [edge for edge in report["edge_fidelity"] if edge["edge_id"] == "X14"]
    assert len(x14) == 8
    assert sum(edge["assertion_result"] is False for edge in x14) == 4
    assert sum(edge["assertion_result"] is True for edge in x14) == 1
    assert sum(edge["assertion_result"] is None for edge in x14) == 3
    assert report["diagnostic_readiness"] == "NOT_ESTABLISHED"
