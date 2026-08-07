from __future__ import annotations

import pytest

from stage_e_integrity_test_fixtures import evaluate, make_binding, make_section


VALID_CONTENT = "采用分阶段实施方法，形成闭环治理流程。"


def _result_with_binding_update(**updates):
    binding = make_binding(allowed=["general_method"]).model_copy(
        update=updates
    )
    section = make_section(content=VALID_CONTENT, binding=binding)
    return evaluate(section), section


@pytest.mark.parametrize(
    ("field_name", "specific_code"),
    [
        ("section_order", "writer_binding_section_order_missing"),
        ("document_id", "writer_binding_document_id_missing"),
        ("run_id", "writer_binding_run_id_missing"),
    ],
)
def test_missing_mandatory_binding_identity_field_is_rejected(
    field_name: str,
    specific_code: str,
) -> None:
    result, section = _result_with_binding_update(**{field_name: None})

    assert result.passed is False
    assert result.checks["writer_binding_identity_fields_present"] is False
    assert result.checks["writer_binding_section_identity_valid"] is False
    assert result.checks["writer_formal_deliverable_eligible"] is False
    assert result.metadata["writer_status_by_section"][section.section_id] == (
        "not_assessed"
    )
    codes = result.metadata["writer_integrity_by_section"][section.section_id][
        "failure_codes"
    ]
    assert specific_code in codes
    assert "writer_binding_identity_fields_missing" in codes
    assert "legacy_writer_artifact_incomplete" in codes


@pytest.mark.parametrize(
    ("field_name", "binding_code", "audit_code"),
    [
        (
            "evidence_contract_sha256",
            "writer_evidence_contract_hash_missing",
            "writer_audit_evidence_contract_hash_missing",
        ),
        (
            "prompt_evidence_contract_sha256",
            "writer_prompt_evidence_contract_hash_missing",
            "writer_audit_prompt_evidence_contract_hash_missing",
        ),
    ],
)
def test_missing_mandatory_evidence_lineage_field_is_rejected(
    field_name: str,
    binding_code: str,
    audit_code: str,
) -> None:
    result, section = _result_with_binding_update(**{field_name: None})

    assert result.passed is False
    assert result.checks["writer_evidence_lineage_fields_present"] is False
    assert result.checks["writer_audit_evidence_lineage_valid"] is False
    assert result.checks["writer_formal_deliverable_eligible"] is False
    codes = result.metadata["writer_integrity_by_section"][section.section_id][
        "failure_codes"
    ]
    assert binding_code in codes
    assert audit_code in codes
    assert "writer_evidence_lineage_fields_missing" in codes
    assert "legacy_writer_artifact_incomplete" in codes


def test_all_mandatory_identity_and_lineage_fields_missing_is_rejected() -> None:
    result, section = _result_with_binding_update(
        section_order=None,
        document_id=None,
        run_id=None,
        evidence_contract_sha256=None,
        prompt_evidence_contract_sha256=None,
    )

    assert result.passed is False
    assert result.checks["writer_binding_identity_fields_present"] is False
    assert result.checks["writer_evidence_lineage_fields_present"] is False
    assert result.checks["writer_formal_deliverable_eligible"] is False
    assert result.metadata[
        "legacy_writer_artifact_incomplete_section_ids"
    ] == [section.section_id]


def test_complete_identity_and_lineage_control_is_deliverable() -> None:
    binding = make_binding(allowed=["general_method"])
    section = make_section(content=VALID_CONTENT, binding=binding)
    result = evaluate(section)

    assert binding.identity_fields_present is True
    assert binding.evidence_lineage_fields_present is True
    assert binding.ordinary_generation_permitted is True
    assert result.checks["writer_binding_identity_fields_present"] is True
    assert result.checks["writer_binding_section_identity_valid"] is True
    assert result.checks["writer_evidence_lineage_fields_present"] is True
    assert result.checks["writer_audit_evidence_lineage_valid"] is True
    assert result.checks["writer_formal_deliverable_eligible"] is True
    assert result.passed is True
