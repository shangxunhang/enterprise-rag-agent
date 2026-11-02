from __future__ import annotations

from stage_e_integrity_test_fixtures import evaluate, make_binding, make_section


VALID_CONTENT = "采用分阶段实施方法，形成闭环治理流程。"


def test_cross_document_writer_binding_reuse_is_rejected() -> None:
    binding = make_binding(allowed=["general_method"]).model_copy(
        update={"document_id": "document_other"}
    )
    section = make_section(content=VALID_CONTENT, binding=binding)
    result = evaluate(section)

    assert result.passed is False
    assert result.checks["writer_binding_identity_fields_present"] is True
    assert result.checks["writer_binding_section_identity_valid"] is False
    assert result.checks["writer_formal_deliverable_eligible"] is False
    assert result.metadata[
        "writer_binding_identity_mismatch_section_ids"
    ] == [section.section_id]


def test_cross_run_writer_binding_reuse_is_rejected() -> None:
    binding = make_binding(allowed=["general_method"]).model_copy(
        update={"run_id": "run_other"}
    )
    section = make_section(content=VALID_CONTENT, binding=binding)
    result = evaluate(section)

    assert result.passed is False
    assert result.checks["writer_binding_identity_fields_present"] is True
    assert result.checks["writer_binding_section_identity_valid"] is False
    assert result.checks["writer_formal_deliverable_eligible"] is False
    assert result.metadata[
        "writer_binding_identity_mismatch_section_ids"
    ] == [section.section_id]


def test_wrong_section_order_writer_binding_is_rejected() -> None:
    binding = make_binding(allowed=["general_method"], section_order=1)
    section = make_section(content=VALID_CONTENT, binding=binding).model_copy(
        update={"section_order": 2},
        deep=True,
    )
    result = evaluate(section)

    assert result.passed is False
    assert result.checks["writer_binding_identity_fields_present"] is True
    assert result.checks["writer_binding_section_identity_valid"] is False
    assert result.checks["writer_formal_deliverable_eligible"] is False
    assert result.metadata[
        "writer_binding_identity_mismatch_section_ids"
    ] == [section.section_id]
