from __future__ import annotations

from pathlib import Path

from apps.enterprise_document.schemas.gate_decision import DeliveryMode
from apps.enterprise_document.services.scheme_writer.evidence_gate_policy import (
    APPLICATION_EVIDENCE_GATE_POLICY_ID,
    EvidenceGatePolicy,
)
from apps.enterprise_document.services.scheme_writer.writer_governance import (
    extract_writer_gate_binding,
)
from rag.evidence.governance_v2 import OnlineEvidenceGovernanceV2
from schemas.rag import (
    EvidenceBundleSchema,
    RAGContextSchema,
    RAGEvidenceAssessmentSchema,
)
from test_support.stage_d_fixtures import evidence_requirements, retrieval_result


def _bundle(*, extra: dict | None = None) -> EvidenceBundleSchema:
    return EvidenceBundleSchema(
        query="访问控制",
        context=RAGContextSchema(
            context_text="",
            max_context_chars=6000,
            used_context_chars=0,
            context_item_count=0,
        ),
        assessment=RAGEvidenceAssessmentSchema(
            assessment_state="completed",
            status="sufficient",
            coverage_ratio=1.0,
            required_topics=["访问控制"],
            covered_topics=["访问控制"],
            supported_claim_types=["general_method"],
            authoritative_evidence_present=True,
            prompt_evidence_source="context_pack_selected_results",
        ),
        extra=dict(extra or {}),
    )


def _normalized(bundle: EvidenceBundleSchema) -> dict:
    return {"evidence": bundle.model_dump(mode="json"), "extra": {}}


def test_production_writer_gate_is_generated_from_bundle_assessment(monkeypatch) -> None:
    bundle = _bundle()
    observed_assessments: list[RAGEvidenceAssessmentSchema] = []
    original_evaluate = EvidenceGatePolicy.evaluate

    def record_evaluate(self, assessment, **kwargs):
        observed_assessments.append(assessment)
        return original_evaluate(self, assessment, **kwargs)

    monkeypatch.setattr(EvidenceGatePolicy, "evaluate", record_evaluate)
    binding = extract_writer_gate_binding(
        _normalized(bundle),
        evidence_bundle=bundle,
        section_id="section-run-001",
        section_title="访问控制",
        section_order=1,
        document_id="document-1",
        run_id="run-1",
    )

    assert binding is not None
    assert observed_assessments == [bundle.assessment]
    assert binding.gate_decision.policy_id == APPLICATION_EVIDENCE_GATE_POLICY_ID
    assert binding.gate_decision.delivery_mode is DeliveryMode.GROUNDED
    assert binding.generation_allowed is True
    assert binding.metadata["source"] == "application_evidence_gate_policy"


def test_old_bundle_gate_fields_cannot_override_application_authority() -> None:
    bundle = _bundle(
        extra={
            "gate_decision": {
                "delivery_mode": "blocked",
                "allowed_claim_types": [],
            },
            "generation_allowed": False,
        }
    )

    binding = extract_writer_gate_binding(
        _normalized(bundle),
        evidence_bundle=bundle,
        section_id="section-run-001",
        section_title="访问控制",
    )

    assert binding is not None
    assert binding.gate_decision.delivery_mode is DeliveryMode.GROUNDED
    assert binding.generation_allowed is True


def test_rag_governance_returns_evidence_facts_without_business_actions() -> None:
    result = OnlineEvidenceGovernanceV2().evaluate(
        query="预算金额",
        results=[retrieval_result()],
        runtime_context=evidence_requirements(),
    )
    payload = result.to_dict()

    assert result.public_assessment.assessment_state == "completed"
    assert "gate_decision" not in payload
    assert "generation_allowed" not in payload
    assert "human_review_required" not in payload


def test_rag_package_has_no_application_reverse_imports_or_gate_output() -> None:
    backend_root = Path(__file__).resolve().parents[1]
    rag_root = backend_root / "rag"
    python_sources = {
        path: path.read_text(encoding="utf-8")
        for path in rag_root.rglob("*.py")
    }

    assert all(
        "apps.enterprise_document" not in source
        for source in python_sources.values()
    )
    for forbidden in (
        "GateDecisionSchema",
        "DeliveryMode",
        "def _gate(",
        '"generation_allowed"',
        '"gate_decision"',
        "human_review_required",
        "fact_status_human_review_required",
    ):
        assert all(
            forbidden not in source for source in python_sources.values()
        )

    governance_source = python_sources[rag_root / "evidence" / "governance_v2.py"]
    for forbidden in (
        "GateDecisionSchema",
        "DeliveryMode",
        "def _claim_risk_level",
        "def _gate",
        "fact_status_human_review_required",
        '"generation_allowed"',
        '"gate_decision"',
    ):
        assert forbidden not in governance_source

    mapper_source = python_sources[rag_root / "mapping" / "result_mapper.py"]
    assert '"gate_decision"' not in mapper_source
    assert '"generation_allowed"' not in mapper_source
