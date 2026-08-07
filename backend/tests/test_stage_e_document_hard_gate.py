from __future__ import annotations

from apps.enterprise_document.schemas.gate_decision import (
    ClaimRiskLevel,
    DeliveryMode,
    GateDecisionSchema,
)
from apps.enterprise_document.schemas.scheme_writer import (
    SchemeDraftSchema,
    SchemeSectionSchema,
    SectionEvalSchema,
)
from apps.enterprise_document.schemas.writer_governance import WriterGateBindingSchema
from apps.enterprise_document.services.document_gate import evaluate_scheme_draft
from apps.enterprise_document.services.scheme_writer.writer_governance import (
    WriterClaimAuditor,
    blocked_claim_audit,
    inject_required_qualifiers,
)
from rag.schema.evidence_assessment_v2 import EvidenceStatus
from schemas.status import ExecutionStatus


def _binding(
    mode: DeliveryMode,
    *,
    allowed: list[str] | None = None,
    forbidden: list[str] | None = None,
    qualifiers: list[str] | None = None,
    human_review: bool = False,
) -> WriterGateBindingSchema:
    status = EvidenceStatus.SUFFICIENT
    if mode is DeliveryMode.BLOCKED:
        status = EvidenceStatus.ABSENT
    elif mode is DeliveryMode.HUMAN_REVIEW:
        status = EvidenceStatus.CONFLICTED
    gate = GateDecisionSchema(
        policy_id="stage_e_document_gate_v1",
        evidence_status=status,
        delivery_mode=mode,
        claim_risk_level=ClaimRiskLevel.HIGH,
        allowed_claim_types=allowed or [],
        forbidden_claim_types=forbidden or [],
        required_qualifiers=qualifiers or [],
        covered_topics=["实施方法"] if status is not EvidenceStatus.ABSENT else [],
        missing_topics=["实施方法"] if status is EvidenceStatus.ABSENT else [],
        human_review_required=human_review,
        reason_codes=["stage_e_document_test"],
    )
    return WriterGateBindingSchema(
        section_id="section_e_001",
        section_title="实施方案",
        section_order=1,
        document_id="doc_e",
        run_id="run_e",
        gate_decision=gate,
        generation_allowed=mode
        in {
            DeliveryMode.GROUNDED,
            DeliveryMode.QUALIFIED,
            DeliveryMode.FRAMEWORK_ONLY,
        },
        evidence_contract_sha256="a" * 64,
        prompt_evidence_contract_sha256="b" * 64,
    )


def _section(
    binding: WriterGateBindingSchema | None,
    *,
    content: str,
    applied_qualifiers: list[str] | None = None,
    formal_eligible: bool | None = None,
) -> SchemeSectionSchema:
    extra = {}
    if binding is not None:
        if binding.generation_allowed:
            audit = WriterClaimAuditor().audit(
                content,
                binding=binding,
                applied_qualifiers=applied_qualifiers or [],
            )
        else:
            audit = blocked_claim_audit(content, binding)
        extra = {
            "writer_gate_binding": binding.model_dump(
                mode="json", exclude_none=True
            ),
            "writer_execution_status": binding.execution_status.value,
            "claim_audit": audit.model_dump(mode="json", exclude_none=True),
            "required_qualifiers_applied": list(applied_qualifiers or []),
            "formal_deliverable_eligible": (
                audit.passed
                if formal_eligible is None
                else bool(formal_eligible)
            ),
            "need_human_review": binding.gate_decision.human_review_required,
        }
    return SchemeSectionSchema(
        section_id="section_e_001",
        section_title="实施方案",
        section_order=1,
        content=content,
        status=ExecutionStatus.SUCCESS,
        eval_result=SectionEvalSchema(
            passed=True,
            checks={"content_nonempty": True},
        ),
        extra=extra,
    )


def _draft(section: SchemeSectionSchema) -> SchemeDraftSchema:
    return SchemeDraftSchema(
        draft_id="draft_e",
        document_id="doc_e",
        task_id="task_e",
        run_id="run_e",
        title="实施方案",
        full_text=section.content,
        sections=[section],
        required_sections=["实施方案"],
        created_at="2026-08-04T01:00:00+00:00",
    )


def _evaluate(section: SchemeSectionSchema):
    return evaluate_scheme_draft(
        _draft(section),
        citation_required=False,
        retrieved_chunk_ids=[],
        evidence_sufficient=True,
        workflow_complete=True,
    )


def test_document_gate_accepts_valid_grounded_writer_execution() -> None:
    binding = _binding(
        DeliveryMode.GROUNDED,
        allowed=["general_method"],
    )
    result = _evaluate(
        _section(binding, content="采用分阶段实施方法，形成闭环治理流程。")
    )
    assert result.checks["writer_gate_bound"] is True
    assert result.checks["writer_claim_audits_passed"] is True
    assert result.checks["writer_formal_deliverable_eligible"] is True
    assert result.metadata["writer_status_by_section"] == {
        "section_e_001": "grounded"
    }
    assert result.passed is True


def test_document_gate_rejects_section_without_writer_gate() -> None:
    result = _evaluate(
        _section(None, content="采用分阶段实施方法，形成闭环治理流程。")
    )
    assert result.passed is False
    assert result.checks["writer_gate_bound"] is False
    assert result.metadata["writer_gate_missing_section_ids"] == [
        "section_e_001"
    ]


def test_document_gate_rejects_blocked_diagnostic_section() -> None:
    binding = _binding(
        DeliveryMode.BLOCKED,
        forbidden=["current_project_fact"],
    )
    result = _evaluate(
        _section(
            binding,
            content="现有证据不足，当前不生成正式章节正文。",
            formal_eligible=False,
        )
    )
    assert result.passed is False
    assert result.checks["writer_generation_allowed"] is False
    assert result.checks["writer_formal_deliverable_eligible"] is False
    assert result.metadata["writer_blocked_section_ids"] == ["section_e_001"]


def test_document_gate_rejects_human_review_section() -> None:
    binding = _binding(
        DeliveryMode.HUMAN_REVIEW,
        forbidden=["current_project_cost"],
        human_review=True,
    )
    result = _evaluate(
        _section(
            binding,
            content="本章节已转入人工复核，当前不生成正式正文。",
            formal_eligible=False,
        )
    )
    assert result.passed is False
    assert result.checks["writer_human_review_clear"] is False
    assert result.metadata["writer_human_review_section_ids"] == [
        "section_e_001"
    ]


def test_document_gate_rejects_failed_post_generation_claim_audit() -> None:
    binding = _binding(
        DeliveryMode.GROUNDED,
        allowed=["general_method"],
        forbidden=["current_project_cost"],
    )
    section = _section(
        binding,
        content="本项目预算金额为250万元。",
        formal_eligible=False,
    )
    result = _evaluate(section)
    assert result.passed is False
    assert result.checks["writer_claim_audits_passed"] is False
    assert result.metadata["writer_claim_audit_failed_section_ids"] == [
        "section_e_001"
    ]


def test_document_gate_requires_qualified_gate_phrases_in_final_content() -> None:
    qualifiers = ["根据现有估算材料", "最终金额以审批或采购结果为准"]
    binding = _binding(
        DeliveryMode.QUALIFIED,
        allowed=["current_project_cost"],
        qualifiers=qualifiers,
        human_review=False,
    )
    raw = "本项目预算金额为250万元。"
    qualified, applied = inject_required_qualifiers(raw, binding)
    accepted = _evaluate(
        _section(
            binding,
            content=qualified,
            applied_qualifiers=applied,
        )
    )
    assert accepted.checks["writer_required_qualifiers_applied"] is True
    assert accepted.checks["writer_claim_audits_passed"] is True

    missing = _evaluate(
        _section(
            binding,
            content=raw,
            applied_qualifiers=[],
            formal_eligible=False,
        )
    )
    assert missing.passed is False
    assert missing.checks["writer_required_qualifiers_applied"] is False
    assert missing.metadata["writer_qualifier_failure_section_ids"] == [
        "section_e_001"
    ]
