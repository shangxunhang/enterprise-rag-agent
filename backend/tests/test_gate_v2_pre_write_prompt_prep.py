from __future__ import annotations

import inspect
import json
from pathlib import Path

import pytest

from agent.runtime.shared_state_schema import SharedStateSchema
from apps.enterprise_document.schemas.project_input_schema import ProjectInputSchema
from apps.enterprise_document.services.scheme_writer.prompt_service import (
    SectionPromptService,
    TargetPromptContextIntegrityError,
)
from context_manager import LLMContextManager, SectionGenerationContextPolicy
from contracts.evidence import build_evidence_bundle, evidence_bundle_sha256
from rag.evidence.eligibility_policy import (
    EVIDENCE_USE_ELIGIBILITY_POLICY_ID,
    EVIDENCE_USE_ELIGIBILITY_POLICY_VERSION,
)
from schemas.citation import CitationSchema
from schemas.claim import ClaimType
from schemas.context import ContextBundleSchema, TaskContextSchema, UserContextSchema
from schemas.rag import (
    EvidenceBundleSchema,
    EvidenceStatus,
    RAGEvidenceAssessmentSchema,
    RAGContextSchema,
    RetrievedChunkSchema,
)
from schemas.evidence_assessment import (
    EvidenceAssessmentV2Schema,
    EvidenceRelevance,
)
from schemas.evidence_source import (
    AuthorityLevel,
    DocumentType,
    EvidenceRole,
    ProjectRelation,
)


TASK_ID = "task_p3b_prompt"
RUN_ID = "run_p3b_prompt"
SECTION_ID = "section_run_p3b_prompt_001"


def _shared_state() -> SharedStateSchema:
    return SharedStateSchema(
        task_id=TASK_ID,
        run_id=RUN_ID,
        task_type="scheme_generation",
        user_input="生成技术方案",
        context_bundle=ContextBundleSchema(
            user=UserContextSchema(user_query="生成技术方案"),
            task=TaskContextSchema(
                task_id=TASK_ID,
                run_id=RUN_ID,
                task_type="scheme_generation",
            ),
        ),
        created_at="2026-08-23T00:00:00+08:00",
    )


def _project_input() -> ProjectInputSchema:
    return ProjectInputSchema.model_validate(
        {
            "task_id": TASK_ID,
            "project_name": "P3-B Test Project",
            "task_type": "scheme_generation",
            "user_query": "生成技术方案",
            "generation_requirements": {
                "required_sections": ["技术方案"],
                "citation_required_sections": ["技术方案"],
                "max_context_chars": 6000,
                "max_tokens_per_section": 1024,
                "min_section_chars": 1,
            },
            "output_schema": {
                "document_title": "P3-B Test Scheme",
                "required_sections": ["技术方案"],
            },
        }
    )


def _pending_bundle() -> EvidenceBundleSchema:
    chunk = RetrievedChunkSchema(
        rank=1,
        matched_chunk_id="child_p3b",
        context_chunk_id="parent_p3b",
        child_chunk_id="child_p3b",
        parent_chunk_id="parent_p3b",
        doc_id="doc_p3b",
        matched_granularity="child",
        context_granularity="parent",
        match_text="统一身份认证是本项目的安全基础。",
        context_text="CANONICAL_EVIDENCE_CONTENT：统一身份认证是本项目的安全基础。",
        title="安全设计规范",
    )
    citation = CitationSchema(
        citation_id="caller_local_id",
        source_type="document",
        source_document_id="doc_p3b",
        doc_id="doc_p3b",
        child_chunk_id="child_p3b",
        parent_chunk_id="parent_p3b",
        quote_text="统一身份认证是本项目的安全基础。",
        title="安全设计规范",
    )
    bundle = build_evidence_bundle(
        query="技术方案身份认证",
        rewritten_queries=[],
        selected_chunks=[chunk],
        dropped_chunks=[],
        citations=[citation],
        trace=None,
        max_context_chars=6000,
    )
    payload = bundle.model_dump(mode="python", round_trip=True)
    payload.update({"task_id": TASK_ID, "run_id": RUN_ID})
    return EvidenceBundleSchema.model_validate(payload)


def _completed_bundle(*, missing_topic: str = "预算边界") -> EvidenceBundleSchema:
    bundle = _pending_bundle()
    assessment = RAGEvidenceAssessmentSchema(
        assessment_state="completed",
        status=EvidenceStatus.INSUFFICIENT,
        coverage_ratio=0.5,
        required_topics=["身份认证", missing_topic],
        covered_topics=["身份认证"],
        missing_topics=[missing_topic],
        supported_claim_types=["general_method"],
        forbidden_claim_types=["current_project_cost"],
        authoritative_evidence_present=True,
        historical_or_reference_evidence_present=False,
        required_qualifiers=["预算数字需项目方确认"],
        reason_codes=["PARTIAL_TOPIC_COVERAGE"],
    )
    item_assessment = EvidenceAssessmentV2Schema(
        evidence_id=bundle.selected_evidence_ids[0],
        document_id=bundle.items[0].doc_id,
        document_type=DocumentType.CURRENT_PROJECT_REQUIREMENT,
        evidence_role=EvidenceRole.PROJECT_FACT,
        project_relation=ProjectRelation.CURRENT_PROJECT,
        authority_level=AuthorityLevel.AUTHORITATIVE,
        eligibility_semantic_policy_id=EVIDENCE_USE_ELIGIBILITY_POLICY_ID,
        eligibility_semantic_policy_version=EVIDENCE_USE_ELIGIBILITY_POLICY_VERSION,
        semantic_relevance=1.0,
        eligibility=True,
        relevance=EvidenceRelevance.RELEVANT,
        supported_topics=["身份认证"],
        supported_claim_types=[ClaimType.GENERAL_METHOD],
        forbidden_claim_types=[ClaimType.CURRENT_PROJECT_COST],
        reason_codes=["PROMPT_PREP_ITEM_ELIGIBLE"],
    )
    payload = bundle.model_dump(mode="python", round_trip=True)
    payload["item_assessments"] = [
        item_assessment.model_dump(mode="python", round_trip=True)
    ]
    payload["assessment"] = assessment.model_dump(mode="python", round_trip=True)
    return EvidenceBundleSchema.model_validate(payload)


def _prompt_service() -> SectionPromptService:
    return SectionPromptService(
        context_manager=LLMContextManager(),
        context_policy=SectionGenerationContextPolicy(),
        prompt_manager=None,
        prompt_id="unused_in_test",
        model_gateway=None,
    )


def _render(bundle: EvidenceBundleSchema):
    return _prompt_service().render_target_initial_section_prompt(
        _shared_state(),
        _project_input(),
        section_id=SECTION_ID,
        section_title="技术方案",
        section_order=1,
        evidence_bundle=bundle,
        structured_facts=[],
        previous_sections=[],
    )


def test_target_prompt_uses_canonical_bundle_as_only_evidence_source() -> None:
    bundle = _completed_bundle()
    result = _render(bundle)

    assert result.rag_context.model_dump() == bundle.context.model_dump()
    assert [item.model_dump() for item in result.citations] == [
        item.model_dump() for item in bundle.citations
    ]
    assert "CANONICAL_EVIDENCE_CONTENT" in result.context_package.rendered_context
    assert result.evidence_bundle_sha256 == evidence_bundle_sha256(bundle)
    signature = inspect.signature(
        SectionPromptService.render_target_initial_section_prompt
    )
    assert "rag_context" not in signature.parameters
    assert "citations" not in signature.parameters
    with pytest.raises(TypeError):
        _prompt_service().render_target_initial_section_prompt(
            _shared_state(),
            _project_input(),
            section_id=SECTION_ID,
            section_title="技术方案",
            section_order=1,
            evidence_bundle=bundle,
            structured_facts=[],
            previous_sections=[],
            rag_context=RAGContextSchema(context_text="CALLER_OVERRIDE"),
        )


def test_completed_assessment_semantics_are_required_non_droppable_and_hashed() -> None:
    first = _render(_completed_bundle(missing_topic="预算边界"))
    second = _render(_completed_bundle(missing_topic="工期边界"))
    semantic_item = next(
        item
        for item in first.context_package.selected_items
        if item.item_id == "writer_evidence_semantics"
    )
    semantics = json.loads(semantic_item.content)

    assert semantic_item.required is True
    assert semantic_item.truncate_allowed is False
    assert semantics == {
        "assessment_schema_version": "rag_evidence_assessment_v1",
        "assessment_state": "completed",
        "authoritative_evidence_present": True,
        "conflicts": [],
        "coverage_ratio": 0.5,
        "covered_topics": ["身份认证"],
        "evidence_status": "insufficient",
        "forbidden_claim_types": ["current_project_cost"],
        "historical_or_reference_evidence_present": False,
        "missing_topics": ["预算边界"],
        "reason_codes": ["PARTIAL_TOPIC_COVERAGE"],
        "required_qualifiers": ["预算数字需项目方确认"],
        "required_topics": ["身份认证", "预算边界"],
        "supported_claim_types": ["general_method"],
    }
    assert first.context_package.context_sha256 != second.context_package.context_sha256
    assert first.context_package.lineage["evidence_bundle_schema_version"] == (
        "rag_evidence_contract_v1"
    )
    assert first.context_package.lineage["evidence_bundle_sha256"] == (
        first.evidence_bundle_sha256
    )
    assert first.context_package.lineage["section_id"] == SECTION_ID
    assert first.context_package.lineage["task_id"] == TASK_ID
    assert first.context_package.lineage["run_id"] == RUN_ID


def test_pending_assessment_fails_as_context_integrity_not_business_decision() -> None:
    with pytest.raises(
        TargetPromptContextIntegrityError,
        match="assessment must be completed",
    ):
        _render(_pending_bundle())


def test_target_prompt_source_has_no_control_plane_input_or_dependency() -> None:
    source = inspect.getsource(
        SectionPromptService.render_target_initial_section_prompt
    )
    forbidden = (
        "WriterAdmissionPolicy",
        "WriterAdmissionDecision",
        "WriterGateBinding",
        "WriterAdmissionEnforcement",
        "EnforcementResult",
        "RecoveryControlFacts",
        "PreWriteRouter",
        "PreWriteRouteDecision",
        "WriterGenerationClass",
        "generation_class",
        "route_decision_ref",
        "NORMAL_WRITE",
        "FALLBACK_WRITE",
    )
    for marker in forbidden:
        assert marker not in source


def test_legacy_render_section_prompt_keeps_rag_context_compatibility_behavior() -> None:
    rag_context = RAGContextSchema(
        context_text="LEGACY_RAG_CONTEXT_ONLY",
        used_context_chars=len("LEGACY_RAG_CONTEXT_ONLY"),
        context_item_count=1,
    )
    result = _prompt_service().render_section_prompt(
        _shared_state(),
        _project_input(),
        SECTION_ID,
        "技术方案",
        1,
        rag_context,
        [],
        [],
    )
    package = result.extra["llm_context_package"]
    assert "LEGACY_RAG_CONTEXT_ONLY" in package["rendered_context"]
    assert any(
        item["item_id"] == "rag_context_projection"
        for item in package["selected_items"]
    )


def test_target_prep_entries_have_only_canonical_p3b_consumers() -> None:
    backend_root = Path(__file__).parents[1]
    production_root = backend_root / "apps"
    prompt_uses: list[Path] = []
    content_uses: list[Path] = []
    for path in production_root.rglob("*.py"):
        text = path.read_text(encoding="utf-8")
        if "render_target_initial_section_prompt" in text:
            prompt_uses.append(path)
        if "generate_target_content" in text:
            content_uses.append(path)
    assert {path.name for path in prompt_uses} == {
        "prompt_service.py",
        "writer_invocation.py",
    }
    assert [path.name for path in content_uses] == ["section_generation_service.py"]
