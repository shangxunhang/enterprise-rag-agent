from __future__ import annotations

import copy

from apps.enterprise_document.services.scheme_writer.writer_governance import (
    CLAIM_CLASSIFIER_POLICY_SHA256,
)
from stage_e_integrity_test_fixtures import evaluate, make_binding, make_section


def _valid_section():
    binding = make_binding(allowed=["general_method"])
    return make_section(
        content="采用分阶段实施方法，形成闭环治理流程。",
        binding=binding,
    )


def test_document_gate_accepts_only_recomputed_formal_eligibility() -> None:
    section = _valid_section()
    result = evaluate(section)

    assert result.passed is True
    assert result.checks["writer_formal_eligibility_recomputed"] is True
    assert result.checks["writer_formal_deliverable_eligible"] is True
    integrity = result.metadata["writer_integrity_by_section"][section.section_id]
    assert integrity["recomputed_formal_deliverable_eligible"] is True
    assert integrity["upstream_formal_deliverable_eligible"] is True


def test_true_formal_boolean_cannot_override_stale_audit() -> None:
    section = _valid_section()
    section.content += "正文已被修改。"
    section.extra["formal_deliverable_eligible"] = True
    result = evaluate(section)

    assert result.passed is False
    assert result.checks["writer_audit_content_hash_valid"] is False
    assert result.checks["writer_formal_deliverable_eligible"] is False
    integrity = result.metadata["writer_integrity_by_section"][section.section_id]
    assert integrity["upstream_formal_deliverable_eligible"] is True
    assert integrity["recomputed_formal_deliverable_eligible"] is False


def test_false_formal_boolean_mismatch_is_rejected_even_when_artifact_is_valid() -> None:
    section = _valid_section()
    section.extra["formal_deliverable_eligible"] = False
    result = evaluate(section)

    assert result.passed is False
    assert result.checks["writer_formal_eligibility_recomputed"] is False
    assert result.metadata[
        "writer_formal_eligibility_mismatch_section_ids"
    ] == [section.section_id]


def test_forged_audit_passed_boolean_cannot_override_current_reaudit() -> None:
    section = _valid_section()
    audit = copy.deepcopy(section.extra["claim_audit"])
    audit["generated_claims"] = []
    audit["violations"] = []
    audit["checks"] = {
        "generation_allowed": True,
        "claim_types_within_allowed_set": True,
        "all_substantive_sentences_classified": True,
        "required_qualifiers_visible": True,
        "classifier_policy_supported": True,
    }
    audit["passed"] = True
    section.extra["claim_audit"] = audit
    section.extra["formal_deliverable_eligible"] = True
    result = evaluate(section)

    assert result.passed is False
    assert result.checks["writer_claim_audit_recomputed_valid"] is False
    assert result.metadata["writer_failure_code_section_ids"][
        "writer_claim_audit_recomputation_mismatch"
    ] == [section.section_id]


def test_allowed_forbidden_and_qualifier_boundaries_are_recomputed() -> None:
    for field, replacement in (
        ("allowed_claim_types", ["template_structure"]),
        ("forbidden_claim_types", ["current_project_cost"]),
        ("required_qualifiers", ["伪造限定语"]),
    ):
        section = _valid_section()
        audit = copy.deepcopy(section.extra["claim_audit"])
        audit[field] = replacement
        section.extra["claim_audit"] = audit
        result = evaluate(section)
        assert result.passed is False
        assert result.checks["writer_audit_claim_boundary_valid"] is False


def test_execution_status_mismatch_is_rejected() -> None:
    section = _valid_section()
    audit = copy.deepcopy(section.extra["claim_audit"])
    audit["execution_status"] = "qualified"
    section.extra["claim_audit"] = audit
    result = evaluate(section)

    assert result.passed is False
    assert result.checks["writer_audit_claim_boundary_valid"] is False


def test_unknown_classifier_policy_is_rejected() -> None:
    section = _valid_section()
    audit = copy.deepcopy(section.extra["claim_audit"])
    assert audit["integrity"]["classifier_policy_sha256"] == (
        CLAIM_CLASSIFIER_POLICY_SHA256
    )
    audit["integrity"]["classifier_policy_sha256"] = "0" * 64
    section.extra["claim_audit"] = audit
    result = evaluate(section)

    assert result.passed is False
    assert result.checks["writer_classifier_policy_valid"] is False
