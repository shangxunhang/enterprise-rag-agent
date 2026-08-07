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
)
from rag.schema.evidence_assessment_v2 import EvidenceStatus
from schemas.status import ExecutionStatus


DOCUMENT_ID = "document_stage_e_integrity"
RUN_ID = "run_stage_e_integrity"


def make_binding(
    *,
    section_id: str = "section_stage_e_integrity_001",
    section_title: str = "实施方案",
    section_order: int = 1,
    mode: DeliveryMode = DeliveryMode.GROUNDED,
    allowed: list[str] | None = None,
    forbidden: list[str] | None = None,
    qualifiers: list[str] | None = None,
    human_review: bool = False,
    evidence_hash: str | None = "a" * 64,
    prompt_hash: str | None = "b" * 64,
) -> WriterGateBindingSchema:
    status = EvidenceStatus.SUFFICIENT
    if mode in {DeliveryMode.BLOCKED, DeliveryMode.FRAMEWORK_ONLY}:
        status = EvidenceStatus.ABSENT
    elif mode is DeliveryMode.HUMAN_REVIEW:
        status = EvidenceStatus.CONFLICTED
    gate = GateDecisionSchema(
        policy_id="stage_e_integrity_test_policy_v2",
        evidence_status=status,
        delivery_mode=mode,
        claim_risk_level=ClaimRiskLevel.HIGH,
        allowed_claim_types=allowed or [],
        forbidden_claim_types=forbidden or [],
        required_qualifiers=qualifiers or [],
        covered_topics=[section_title] if status is not EvidenceStatus.ABSENT else [],
        missing_topics=[] if status is not EvidenceStatus.ABSENT else [section_title],
        human_review_required=human_review,
        reason_codes=["stage_e_integrity_test"],
    )
    return WriterGateBindingSchema(
        section_id=section_id,
        section_title=section_title,
        section_order=section_order,
        document_id=DOCUMENT_ID,
        run_id=RUN_ID,
        gate_decision=gate,
        generation_allowed=mode
        in {
            DeliveryMode.GROUNDED,
            DeliveryMode.QUALIFIED,
            DeliveryMode.FRAMEWORK_ONLY,
        },
        evidence_contract_sha256=evidence_hash,
        prompt_evidence_source="context_pack_selected_results",
        prompt_evidence_contract_sha256=prompt_hash,
    )


def make_section(
    *,
    content: str,
    binding: WriterGateBindingSchema,
    status: ExecutionStatus = ExecutionStatus.SUCCESS,
    upstream_formal_eligible: bool | None = None,
) -> SchemeSectionSchema:
    if binding.generation_allowed:
        audit = WriterClaimAuditor().audit(content, binding=binding)
    else:
        audit = blocked_claim_audit(content, binding)
    recomputed = bool(
        binding.generation_allowed
        and binding.gate_decision.delivery_mode
        not in {DeliveryMode.BLOCKED, DeliveryMode.HUMAN_REVIEW}
        and not binding.gate_decision.human_review_required
        and audit.passed
        and status in {ExecutionStatus.SUCCESS, ExecutionStatus.PARTIAL_SUCCESS}
    )
    return SchemeSectionSchema(
        section_id=binding.section_id,
        section_title=binding.section_title,
        section_order=binding.section_order or 1,
        content=content,
        status=status,
        eval_result=SectionEvalSchema(
            passed=audit.passed,
            checks={"claim_audit_passed": audit.passed},
            failures=[] if audit.passed else ["claim_audit_failed"],
        ),
        extra={
            "writer_gate_binding": binding.model_dump(mode="json", exclude_none=True),
            "writer_execution_status": binding.execution_status.value,
            "claim_audit": audit.model_dump(mode="json", exclude_none=True),
            "required_qualifiers_applied": list(audit.applied_qualifiers),
            "formal_deliverable_eligible": (
                recomputed
                if upstream_formal_eligible is None
                else upstream_formal_eligible
            ),
            "need_human_review": bool(
                binding.gate_decision.human_review_required
                or audit.human_review_required
            ),
            "generation_blocked": not binding.generation_allowed,
            "generation_block_reason": (
                "writer_generation_blocked"
                if not binding.generation_allowed
                else None
            ),
        },
    )


def make_draft(*sections: SchemeSectionSchema) -> SchemeDraftSchema:
    ordered = list(sections)
    return SchemeDraftSchema(
        draft_id="draft_stage_e_integrity",
        document_id=DOCUMENT_ID,
        task_id="task_stage_e_integrity",
        run_id=RUN_ID,
        title="Stage E Integrity",
        full_text="\n\n".join(item.content for item in ordered),
        sections=ordered,
        required_sections=[item.section_title for item in ordered],
        created_at="2026-08-04T07:00:00+00:00",
    )


def evaluate(*sections: SchemeSectionSchema):
    return evaluate_scheme_draft(
        make_draft(*sections),
        citation_required=False,
        retrieved_chunk_ids=[],
        evidence_sufficient=True,
        workflow_complete=True,
    )
