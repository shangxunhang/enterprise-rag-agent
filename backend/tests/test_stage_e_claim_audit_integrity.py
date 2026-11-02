from __future__ import annotations

import copy

from apps.enterprise_document.schemas.gate_decision import DeliveryMode
from apps.enterprise_document.services.scheme_writer.writer_governance import (
    CLAIM_CLASSIFIER_ID,
    CLAIM_CLASSIFIER_POLICY_SHA256,
    CLAIM_CLASSIFIER_VERSION,
    SUBSTANTIVE_DETECTOR_VERSION,
    WriterClaimAuditor,
    content_sha256,
    inject_required_qualifiers,
    normalized_content_sha256,
    writer_gate_binding_sha256,
)
from stage_e_integrity_test_fixtures import evaluate, make_binding, make_section


def test_claim_audit_integrity_binds_exact_final_content_and_lineage() -> None:
    binding = make_binding(allowed=["general_method"])
    content = "采用分阶段实施方法，形成闭环治理流程。"
    audit = WriterClaimAuditor().audit(content, binding=binding)

    assert audit.passed is True
    assert audit.integrity.section_id == binding.section_id
    assert audit.integrity.section_title == binding.section_title
    assert audit.integrity.content_sha256 == content_sha256(content)
    assert audit.integrity.normalized_content_sha256 == normalized_content_sha256(
        content
    )
    assert audit.integrity.writer_gate_binding_sha256 == writer_gate_binding_sha256(
        binding
    )
    assert audit.integrity.evidence_contract_sha256 == (
        binding.evidence_contract_sha256
    )
    assert audit.integrity.prompt_evidence_contract_sha256 == (
        binding.prompt_evidence_contract_sha256
    )
    assert audit.integrity.classifier_id == CLAIM_CLASSIFIER_ID
    assert audit.integrity.classifier_version == CLAIM_CLASSIFIER_VERSION
    assert audit.integrity.classifier_policy_sha256 == (
        CLAIM_CLASSIFIER_POLICY_SHA256
    )
    assert audit.integrity.substantive_detector_version == (
        SUBSTANTIVE_DETECTOR_VERSION
    )
    assert audit.audit_created_at.tzinfo is not None


def test_content_hash_normalization_is_diagnostic_only() -> None:
    left = "第一行  \r\n第二行\r\n"
    right = "第一行\n第二行\n"
    assert content_sha256(left) != content_sha256(right)
    assert normalized_content_sha256(left) == normalized_content_sha256(right)


def test_post_audit_content_mutation_is_rejected_as_stale() -> None:
    binding = make_binding(allowed=["general_method"])
    section = make_section(
        content="采用分阶段实施方法，形成闭环治理流程。",
        binding=binding,
    )
    section.content = "采用分阶段实施方法，形成闭环治理流程并持续优化。"
    result = evaluate(section)

    assert result.passed is False
    assert result.checks["writer_audit_content_hash_valid"] is False
    assert result.metadata["writer_stale_claim_audit_section_ids"] == [
        section.section_id
    ]
    codes = result.metadata["writer_failure_code_section_ids"]
    assert codes["writer_claim_audit_stale"] == [section.section_id]


def test_policy_content_mutation_after_method_audit_is_rejected() -> None:
    binding = make_binding(allowed=["general_method"])
    section = make_section(
        content="采用分阶段实施方法，形成闭环治理流程。",
        binding=binding,
    )
    section.content = "遵循《数据安全法》开展建设。"
    result = evaluate(section)

    assert result.passed is False
    assert result.checks["writer_audit_content_hash_valid"] is False
    assert result.checks["writer_claim_audit_recomputed_valid"] is False


def test_qualified_qualifier_removal_breaks_hash_and_qualifier_integrity() -> None:
    qualifiers = ["根据现有估算材料", "最终金额以审批或采购结果为准"]
    binding = make_binding(
        mode=DeliveryMode.QUALIFIED,
        allowed=["current_project_cost"],
        qualifiers=qualifiers,
    )
    content, _ = inject_required_qualifiers(
        "本项目预算金额为250万元。",
        binding,
    )
    section = make_section(content=content, binding=binding)
    section.content = section.content.replace(qualifiers[1], "")
    result = evaluate(section)

    assert result.passed is False
    assert result.checks["writer_audit_content_hash_valid"] is False
    assert result.checks["writer_required_qualifiers_applied"] is False
    assert result.checks["writer_formal_deliverable_eligible"] is False


def test_writer_gate_binding_hash_mismatch_is_rejected() -> None:
    binding = make_binding(allowed=["general_method"])
    section = make_section(
        content="采用分阶段实施方法，形成闭环治理流程。",
        binding=binding,
    )
    audit = copy.deepcopy(section.extra["claim_audit"])
    audit["integrity"]["writer_gate_binding_sha256"] = "f" * 64
    section.extra["claim_audit"] = audit
    result = evaluate(section)

    assert result.passed is False
    assert result.checks["writer_audit_binding_hash_valid"] is False
    assert result.metadata["writer_failure_code_section_ids"][
        "writer_gate_binding_hash_mismatch"
    ] == [section.section_id]


def test_evidence_and_prompt_contract_hash_mismatch_are_rejected() -> None:
    binding = make_binding(allowed=["general_method"])
    section = make_section(
        content="采用分阶段实施方法，形成闭环治理流程。",
        binding=binding,
    )
    audit = copy.deepcopy(section.extra["claim_audit"])
    audit["integrity"]["evidence_contract_sha256"] = "c" * 64
    audit["integrity"]["prompt_evidence_contract_sha256"] = "d" * 64
    section.extra["claim_audit"] = audit
    result = evaluate(section)

    assert result.passed is False
    assert result.checks["writer_audit_evidence_lineage_valid"] is False


def test_generated_claim_span_and_text_mismatch_are_rejected() -> None:
    binding = make_binding(allowed=["general_method"])
    section = make_section(
        content="采用分阶段实施方法，形成闭环治理流程。",
        binding=binding,
    )
    audit = copy.deepcopy(section.extra["claim_audit"])
    audit["generated_claims"][0]["span_start"] += 1
    section.extra["claim_audit"] = audit
    result = evaluate(section)

    assert result.passed is False
    assert result.checks["writer_audit_claim_spans_valid"] is False
    assert result.metadata["writer_failure_code_section_ids"][
        "writer_generated_claim_span_mismatch"
    ] == [section.section_id]
