from __future__ import annotations

import copy

from stage_e_integrity_test_fixtures import evaluate, make_binding, make_section


def test_cross_section_writer_binding_reuse_is_rejected() -> None:
    binding_a = make_binding(
        section_id="section_A",
        section_title="章节A",
        allowed=["general_method"],
    )
    binding_b = make_binding(
        section_id="section_B",
        section_title="章节B",
        allowed=["general_method"],
    )
    section_a = make_section(
        content="采用分阶段实施方法，形成闭环治理流程。",
        binding=binding_a,
    )
    section_b = make_section(
        content="采用分阶段实施方法，形成闭环治理流程。",
        binding=binding_b,
    )
    section_b.extra["writer_gate_binding"] = copy.deepcopy(
        section_a.extra["writer_gate_binding"]
    )
    result = evaluate(section_b)

    assert result.passed is False
    assert result.checks["writer_binding_section_identity_valid"] is False
    assert result.metadata["writer_binding_identity_mismatch_section_ids"] == [
        section_b.section_id
    ]


def test_cross_section_claim_audit_reuse_is_rejected() -> None:
    binding_a = make_binding(
        section_id="section_A",
        section_title="章节A",
        allowed=["general_method"],
    )
    binding_b = make_binding(
        section_id="section_B",
        section_title="章节B",
        allowed=["general_method"],
    )
    section_a = make_section(
        content="采用分阶段实施方法，形成闭环治理流程。",
        binding=binding_a,
    )
    section_b = make_section(
        content="采用分阶段实施方法，形成闭环治理流程。",
        binding=binding_b,
    )
    section_b.extra["claim_audit"] = copy.deepcopy(section_a.extra["claim_audit"])
    result = evaluate(section_b)

    assert result.passed is False
    assert result.checks["writer_audit_section_identity_valid"] is False
    assert result.metadata["writer_audit_identity_mismatch_section_ids"] == [
        section_b.section_id
    ]


def test_generated_claim_section_reuse_is_rejected() -> None:
    binding = make_binding(allowed=["general_method"])
    section = make_section(
        content="采用分阶段实施方法，形成闭环治理流程。",
        binding=binding,
    )
    audit = copy.deepcopy(section.extra["claim_audit"])
    audit["generated_claims"][0]["section_id"] = "section_other"
    section.extra["claim_audit"] = audit
    result = evaluate(section)

    assert result.passed is False
    assert result.metadata["writer_audit_identity_mismatch_section_ids"] == [
        section.section_id
    ]
