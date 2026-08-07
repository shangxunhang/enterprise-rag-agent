from __future__ import annotations

from copy import deepcopy

import pytest
from pydantic import ValidationError

from rag.evidence.governance_v2 import OnlineEvidenceGovernanceV2
from rag.schema.retrieval_candidate_v2 import build_retrieval_candidate_v2

from test_support.stage_d_fixtures import evidence_requirements, retrieval_result


@pytest.mark.parametrize(
    ("field_name", "bad_value", "message"),
    [
        ("tenant_id", "tenant-other", "tenant_id"),
        ("kb_id", "template_style_kb", "kb_id"),
        ("document_type", "policy_standard", "document_type"),
        ("evidence_role", "historical_case", "evidence_role"),
        ("project_relation", "other_project", "project_relation"),
        ("project_id", "project-other", "project_id"),
        ("authority_level", "reference", "authority_level"),
        (
            "allowed_claim_types",
            ["current_project_fact"],
            "allowed_claim_types",
        ),
        (
            "forbidden_claim_types",
            ["current_project_cost"],
            "both allowed and forbidden",
        ),
    ],
)
def test_candidate_scalar_or_claim_boundary_mismatch_is_rejected(
    field_name: str,
    bad_value: object,
    message: str,
) -> None:
    result = retrieval_result()
    result[field_name] = bad_value

    with pytest.raises((ValidationError, ValueError), match=message):
        build_retrieval_candidate_v2(result)


def test_candidate_claim_lists_use_set_semantics_but_still_reject_duplicates() -> None:
    result = retrieval_result()
    result["allowed_claim_types"] = [
        "current_project_fact",
        "current_project_cost",
    ]
    candidate = build_retrieval_candidate_v2(result)
    assert {item.value for item in candidate.allowed_claim_types} == {
        "current_project_cost",
        "current_project_fact",
    }

    duplicate = deepcopy(result)
    duplicate["allowed_claim_types"] = [
        "current_project_cost",
        "current_project_cost",
        "current_project_fact",
    ]
    with pytest.raises(ValidationError, match="must be unique"):
        build_retrieval_candidate_v2(duplicate)


def test_candidate_allowed_and_forbidden_intersection_is_rejected() -> None:
    result = retrieval_result()
    result["allowed_claim_types"] = [
        "current_project_cost",
        "current_project_fact",
    ]
    result["forbidden_claim_types"] = ["current_project_cost"]
    with pytest.raises(ValidationError, match="both allowed and forbidden"):
        build_retrieval_candidate_v2(result)


def test_governance_records_candidate_contract_error_and_rejected_id() -> None:
    result = retrieval_result()
    result["kb_id"] = "template_style_kb"
    governance = OnlineEvidenceGovernanceV2().evaluate(
        query="预算金额",
        results=[result],
        runtime_context=evidence_requirements(),
    )

    assert governance.candidates == ()
    assert governance.rejected_candidate_ids == ("child-1",)
    assert len(governance.candidate_contract_errors) == 1
    error = governance.candidate_contract_errors[0]
    assert error["candidate_id"] == "child-1"
    assert "kb_id" in error["message"]
    payload = governance.to_dict()
    assert payload["candidate_contract_errors"][0]["candidate_id"] == "child-1"
