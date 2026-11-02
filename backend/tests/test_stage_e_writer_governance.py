from __future__ import annotations

from unittest.mock import Mock

import pytest

from agent.runtime.shared_state_schema import SharedStateSchema
from apps.enterprise_document.agents.scheme_writer_agent import SchemeWriterAgent
from apps.enterprise_document.schemas.gate_decision import (
    ClaimRiskLevel,
    DeliveryMode,
    GateDecisionSchema,
)
from apps.enterprise_document.schemas.project_input_schema import ProjectInputSchema
from apps.enterprise_document.schemas.writer_governance import WriterGateBindingSchema
from apps.enterprise_document.services.scheme_writer.runtime_support import (
    SchemeWriterRuntimeSupport,
)
from apps.enterprise_document.services.scheme_writer.section_generation_service import (
    SectionGenerationService,
)
from apps.enterprise_document.services.scheme_writer.writer_governance import (
    CLAIM_CLASSIFIER_POLICY_SHA256,
    CLAIM_CLASSIFIER_VERSION,
    DeterministicClaimClassifier,
    WriterClaimAuditor,
    extract_writer_gate_binding,
    inject_required_qualifiers,
    writer_gate_prompt_contract,
)
from rag.schema.evidence_assessment_v2 import EvidenceStatus
from schemas.context import ContextBundleSchema, TaskContextSchema, UserContextSchema
from schemas.model import ModelResponseSchema
from schemas.rag import (
    EvidenceBundleSchema,
    RAGContextSchema,
    RAGEvidenceAssessmentSchema,
)


def _gate(
    mode: DeliveryMode,
    *,
    allowed: list[str] | None = None,
    forbidden: list[str] | None = None,
    qualifiers: list[str] | None = None,
    human_review: bool = False,
) -> GateDecisionSchema:
    status = EvidenceStatus.SUFFICIENT
    if mode in {DeliveryMode.BLOCKED, DeliveryMode.FRAMEWORK_ONLY}:
        status = EvidenceStatus.INSUFFICIENT
    elif mode is DeliveryMode.HUMAN_REVIEW:
        status = EvidenceStatus.CONFLICTED
    return GateDecisionSchema(
        policy_id="stage_e_test_policy_v1",
        evidence_status=status,
        delivery_mode=mode,
        claim_risk_level=ClaimRiskLevel.HIGH,
        allowed_claim_types=allowed or [],
        forbidden_claim_types=forbidden or [],
        required_qualifiers=qualifiers or [],
        covered_topics=["预算金额"] if status is not EvidenceStatus.INSUFFICIENT else [],
        missing_topics=["预算金额"] if status is EvidenceStatus.INSUFFICIENT else [],
        human_review_required=human_review,
        reason_codes=["stage_e_test"],
    )


def _binding(
    mode: DeliveryMode,
    *,
    allowed: list[str] | None = None,
    forbidden: list[str] | None = None,
    qualifiers: list[str] | None = None,
    human_review: bool = False,
) -> WriterGateBindingSchema:
    return WriterGateBindingSchema(
        section_id="section_run_e_001",
        section_title="投资估算",
        section_order=1,
        document_id="doc_e",
        run_id="run_e",
        gate_decision=_gate(
            mode,
            allowed=allowed,
            forbidden=forbidden,
            qualifiers=qualifiers,
            human_review=human_review,
        ),
        generation_allowed=mode
        in {
            DeliveryMode.GROUNDED,
            DeliveryMode.QUALIFIED,
            DeliveryMode.FRAMEWORK_ONLY,
        },
        evidence_contract_sha256="a" * 64,
        prompt_evidence_source="context_pack_selected_results",
        prompt_evidence_contract_sha256="b" * 64,
    )


def _shared_state() -> SharedStateSchema:
    return SharedStateSchema(
        task_id="task_e",
        run_id="run_e",
        task_type="scheme_generation",
        user_input="生成投资估算",
        context_bundle=ContextBundleSchema(
            user=UserContextSchema(user_query="生成投资估算"),
            task=TaskContextSchema(
                task_id="task_e",
                run_id="run_e",
                task_type="scheme_generation",
            ),
        ),
        created_at="2026-08-04T01:00:00+00:00",
    )


def _project_input() -> ProjectInputSchema:
    return ProjectInputSchema.model_validate(
        {
            "task_id": "task_e",
            "task_type": "scheme_generation",
            "user_query": "生成投资估算",
            "generation_requirements": {
                "required_sections": ["投资估算"],
                "citation_required_sections": [],
                "min_section_chars": 1,
            },
        }
    )


def test_writer_binding_produces_application_gate_from_public_assessment() -> None:
    assessment = RAGEvidenceAssessmentSchema(
        assessment_state="completed",
        status="sufficient",
        coverage_ratio=1.0,
        required_topics=["投资估算"],
        covered_topics=["投资估算"],
        supported_claim_types=["general_method"],
        authoritative_evidence_present=True,
        prompt_evidence_source="context_pack_selected_results",
    )
    evidence_bundle = EvidenceBundleSchema(
        query="投资估算",
        context=RAGContextSchema(
            context_text="",
            max_context_chars=6000,
            used_context_chars=0,
            context_item_count=0,
        ),
        assessment=assessment,
        extra={
            "prompt_evidence_source": "context_pack_selected_results",
            # These contradictory legacy fields must not drive the decision.
            "gate_decision": _gate(
                DeliveryMode.BLOCKED,
                forbidden=["general_method"],
            ).model_dump(mode="json"),
            "generation_allowed": False,
        },
    )
    normalized = {
        "evidence": evidence_bundle.model_dump(mode="json"),
        "extra": {
            # Old RAG actions must be ignored by the Application producer.
            "gate_decision": _gate(
                DeliveryMode.BLOCKED,
                forbidden=["general_method"],
            ).model_dump(mode="json"),
            "generation_allowed": False,
        },
    }
    binding = extract_writer_gate_binding(
        normalized,
        evidence_bundle=evidence_bundle,
        section_id="section_run_e_001",
        section_title="投资估算",
    )
    assert binding is not None
    assert binding.gate_decision.delivery_mode is DeliveryMode.GROUNDED
    assert binding.generation_allowed is True


def test_writer_binding_requires_public_assessment_not_old_gate_fields() -> None:
    normalized = {
        "evidence": {
            "schema_version": "rag_evidence_contract_v1",
            "assessment": {},
            "extra": {},
        },
        "extra": {
            "gate_decision": _gate(
                DeliveryMode.GROUNDED,
                allowed=["general_method"],
            ).model_dump(mode="json"),
            "generation_allowed": True,
        },
    }
    assert (
        extract_writer_gate_binding(
            normalized,
            section_id="section_run_e_001",
            section_title="投资估算",
        )
        is None
    )


def test_qualified_writer_injects_every_required_qualifier() -> None:
    qualifiers = ["根据现有估算材料", "最终金额以审批或采购结果为准"]
    binding = _binding(
        DeliveryMode.QUALIFIED,
        allowed=["current_project_cost"],
        qualifiers=qualifiers,
        human_review=True,
    )
    content, applied = inject_required_qualifiers(
        "本项目预算金额为250万元。",
        binding,
    )
    assert applied == qualifiers
    assert all(item in content for item in qualifiers)
    audit = WriterClaimAuditor().audit(
        content,
        binding=binding,
        applied_qualifiers=applied,
    )
    assert audit.passed is True
    assert [item.claim_type.value for item in audit.generated_claims] == [
        "current_project_cost"
    ]


def test_post_generation_claim_audit_rejects_claim_outside_allowed_set() -> None:
    binding = _binding(
        DeliveryMode.GROUNDED,
        allowed=["general_method"],
        forbidden=["current_project_cost"],
    )
    audit = WriterClaimAuditor().audit(
        "本项目预算金额为250万元。",
        binding=binding,
    )
    assert audit.passed is False
    assert audit.generated_claims[0].claim_type.value == "current_project_cost"
    assert audit.violations[0].violation_code == "forbidden_claim_type_generated"


def test_writer_prompt_contract_contains_final_gate_fields() -> None:
    binding = _binding(
        DeliveryMode.QUALIFIED,
        allowed=["current_project_cost"],
        qualifiers=["根据现有估算材料"],
        human_review=True,
    )
    prompt = writer_gate_prompt_contract(binding)
    assert "delivery_mode: qualified" in prompt
    assert "allowed_claim_types: current_project_cost" in prompt
    assert "required_qualifiers: 根据现有估算材料" in prompt
    assert "human_review_required: true" in prompt


@pytest.mark.parametrize(
    "sentence",
    [
        "计算资源规模应结合并发量和性能目标进行测算。",
        "存储方案应考虑容量增长和备份周期。",
        "网络规划应结合业务区域和安全域进行设计。",
        "典型政务云可采用分层架构。",
    ],
)
def test_method_sentences_are_not_classified_as_current_project_configuration(
    sentence: str,
) -> None:
    result = DeterministicClaimClassifier().classify_sentence(sentence)
    assert "current_project_configuration" not in {
        item.value for item in result.claim_types
    }


@pytest.mark.parametrize(
    ("sentence", "expected_claim_type"),
    [
        ("本项目采用 Hadoop 集群。", "current_project_configuration"),
        ("本次建设部署三台服务器。", "current_project_configuration"),
        ("本项目预算为 500 万元。", "current_project_cost"),
    ],
)
def test_high_risk_project_claims_remain_deterministically_classified(
    sentence: str,
    expected_claim_type: str,
) -> None:
    result = DeterministicClaimClassifier().classify_sentence(sentence)
    assert expected_claim_type in {item.value for item in result.claim_types}


def test_specific_product_recommendation_remains_fail_closed_without_permission() -> None:
    binding = _binding(
        DeliveryMode.GROUNDED,
        allowed=["general_method"],
    )
    audit = WriterClaimAuditor().audit(
        "在没有具体选型证据时建议采用阿里云 OSS。",
        binding=binding,
    )
    assert audit.passed is False
    assert audit.violations


def test_writer_prompt_translates_missing_high_risk_permissions_to_actions() -> None:
    binding = _binding(
        DeliveryMode.QUALIFIED,
        allowed=["general_method", "typical_architecture", "reference_case"],
        qualifiers=["依据当前可用材料"],
    )
    prompt = writer_gate_prompt_contract(binding)
    assert "不得把具体组件、产品、品牌、型号或选型写成本项目确定配置" in prompt
    assert "不得确定服务器数量、节点数、容量和并发规模" in prompt
    assert "不得给出项目金额、预算、概算和造价结论" in prompt
    assert "不得确定工期、上线时间和实施周期" in prompt
    assert "历史案例和通用材料不得改写为本项目既定事实" in prompt
    assert "建设原则、测算维度、选择方法、典型架构、参考方案和待确认项" in prompt


def test_classifier_policy_identity_changes_with_configuration_rule() -> None:
    assert CLAIM_CLASSIFIER_VERSION != "2.0.0"
    assert len(CLAIM_CLASSIFIER_POLICY_SHA256) == 64


def test_framework_only_rejects_current_project_fact() -> None:
    binding = _binding(
        DeliveryMode.FRAMEWORK_ONLY,
        allowed=["template_structure", "style_guidance"],
        forbidden=["current_project_fact"],
    )
    audit = WriterClaimAuditor().audit(
        "本项目形成统一业务协同能力。",
        binding=binding,
    )
    assert audit.passed is False
    assert [item.claim_type.value for item in audit.generated_claims] == [
        "current_project_fact"
    ]
    assert audit.violations[0].violation_code == "forbidden_claim_type_generated"


@pytest.mark.parametrize(
    "binding_update",
    [
        {"document_id": None},
        {"evidence_contract_sha256": None},
    ],
)
def test_incomplete_identity_or_lineage_skips_model_and_routes_review(
    binding_update: dict[str, object],
) -> None:
    model_service = Mock()
    service = SectionGenerationService(
        runtime_support=SchemeWriterRuntimeSupport(),
        prompt_service=Mock(),
        model_service=model_service,
        citation_service=Mock(),
        grounding_repair_service=Mock(),
        advisory_service=Mock(),
        semantic_judge=Mock(),
        enable_semantic_gate=False,
        semantic_gate_model_name="",
        generation_checker=None,
        repair_strategy=None,
        generation_quality_metadata={},
    )
    binding = _binding(
        DeliveryMode.GROUNDED,
        allowed=["general_method"],
    ).model_copy(update=binding_update)

    section = service.generate_section(
        _shared_state(),
        document_id="doc_e",
        project_input=_project_input(),
        section_title="投资估算",
        section_order=1,
        rag_context=RAGContextSchema(context_text="参考实施方法"),
        citations=[],
        structured_facts=[],
        previous_sections=[],
        writer_gate_binding=binding,
    )

    model_service.call_model.assert_not_called()
    assert section.status.value == "partial_success"
    assert section.extra["writer_execution_status"] == "not_assessed"
    assert section.extra["generation_block_reason"] == (
        "legacy_writer_artifact_incomplete"
    )
    assert section.extra["need_human_review"] is True
    assert section.extra["formal_deliverable_eligible"] is False
    assert section.extra["claim_audit"]["execution_status"] == "not_assessed"
    assert section.extra["claim_audit"]["violations"][0][
        "violation_code"
    ] == "legacy_writer_artifact_incomplete"


def test_blocked_gate_returns_without_model_call() -> None:
    model_service = Mock()
    service = SectionGenerationService(
        runtime_support=SchemeWriterRuntimeSupport(),
        prompt_service=Mock(),
        model_service=model_service,
        citation_service=Mock(),
        grounding_repair_service=Mock(),
        advisory_service=Mock(),
        semantic_judge=Mock(),
        enable_semantic_gate=False,
        semantic_gate_model_name="",
        generation_checker=None,
        repair_strategy=None,
        generation_quality_metadata={},
    )
    binding = _binding(
        DeliveryMode.BLOCKED,
        forbidden=["current_project_cost"],
    )
    section = service.generate_section(
        _shared_state(),
        document_id="doc_e",
        project_input=_project_input(),
        section_title="投资估算",
        section_order=1,
        rag_context=RAGContextSchema(context_text=""),
        citations=[],
        structured_facts=[],
        previous_sections=[],
        writer_gate_binding=binding,
    )
    model_service.call_model.assert_not_called()
    assert section.extra["writer_execution_status"] == "blocked"
    assert section.extra["formal_deliverable_eligible"] is False
    assert section.extra["generation_blocked"] is True
    assert section.model_output == ""


def test_qualified_generate_section_injects_qualifier_and_passes_claim_audit() -> None:
    class _QualifiedAgent(SchemeWriterAgent):
        def call_model(self, *args, **kwargs):  # type: ignore[override]
            del args, kwargs
            return ModelResponseSchema(
                model_call_id="stage-e-qualified-model",
                task_id="task_e",
                run_id="run_e",
                model_name="stage-e-stub",
                success=True,
                content="采用分阶段实施方法，形成闭环治理流程。",
                finish_reason="stop",
                created_at="2026-08-04T01:00:00+00:00",
            )

    binding = _binding(
        DeliveryMode.QUALIFIED,
        allowed=["general_method"],
        qualifiers=["基于现有参考资料"],
    )
    section = _QualifiedAgent(
        enable_semantic_gate=False
    ).section_generation_service.generate_section(
        _shared_state(),
        document_id="doc_e",
        project_input=_project_input(),
        section_title="投资估算",
        section_order=1,
        rag_context=RAGContextSchema(context_text="参考实施方法"),
        citations=[],
        structured_facts=[],
        previous_sections=[],
        writer_gate_binding=binding,
    )
    assert "基于现有参考资料" in section.content
    assert "【最终证据门禁约束｜不得覆盖】" in section.prompt
    assert "delivery_mode: qualified" in section.prompt
    assert "allowed_claim_types: general_method" in section.prompt
    assert "required_qualifiers: 基于现有参考资料" in section.prompt
    assert section.extra["writer_execution_status"] == "qualified"
    assert section.extra["claim_audit"]["passed"] is True
    assert section.extra["formal_deliverable_eligible"] is True
    assert section.eval_result is not None
    assert section.eval_result.checks["claim_audit_passed"] is True


def test_generate_section_fails_when_model_emits_forbidden_claim() -> None:
    class _ViolatingAgent(SchemeWriterAgent):
        def call_model(self, *args, **kwargs):  # type: ignore[override]
            del args, kwargs
            return ModelResponseSchema(
                model_call_id="stage-e-violating-model",
                task_id="task_e",
                run_id="run_e",
                model_name="stage-e-stub",
                success=True,
                content="本项目预算金额为250万元。",
                finish_reason="stop",
                created_at="2026-08-04T01:00:00+00:00",
            )

    binding = _binding(
        DeliveryMode.GROUNDED,
        allowed=["general_method"],
        forbidden=["current_project_cost"],
    )
    section = _ViolatingAgent(
        enable_semantic_gate=False
    ).section_generation_service.generate_section(
        _shared_state(),
        document_id="doc_e",
        project_input=_project_input(),
        section_title="投资估算",
        section_order=1,
        rag_context=RAGContextSchema(context_text="参考实施方法"),
        citations=[],
        structured_facts=[],
        previous_sections=[],
        writer_gate_binding=binding,
    )
    assert section.status.value == "failed"
    assert section.error is not None
    assert section.error.error_code == "SECTION_CLAIM_AUDIT_FAILED"
    assert section.extra["claim_audit"]["passed"] is False
    assert section.extra["formal_deliverable_eligible"] is False


def test_human_review_gate_returns_without_model_call() -> None:
    model_service = Mock()
    service = SectionGenerationService(
        runtime_support=SchemeWriterRuntimeSupport(),
        prompt_service=Mock(),
        model_service=model_service,
        citation_service=Mock(),
        grounding_repair_service=Mock(),
        advisory_service=Mock(),
        semantic_judge=Mock(),
        enable_semantic_gate=False,
        semantic_gate_model_name="",
        generation_checker=None,
        repair_strategy=None,
        generation_quality_metadata={},
    )
    binding = _binding(
        DeliveryMode.HUMAN_REVIEW,
        forbidden=["current_project_cost"],
        human_review=True,
    )
    section = service.generate_section(
        _shared_state(),
        document_id="doc_e",
        project_input=_project_input(),
        section_title="投资估算",
        section_order=1,
        rag_context=RAGContextSchema(context_text="冲突证据"),
        citations=[],
        structured_facts=[],
        previous_sections=[],
        writer_gate_binding=binding,
    )
    model_service.call_model.assert_not_called()
    assert section.extra["writer_execution_status"] == "human_review"
    assert section.extra["need_human_review"] is True
    assert section.extra["formal_deliverable_eligible"] is False
