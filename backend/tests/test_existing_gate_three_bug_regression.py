from __future__ import annotations

from types import SimpleNamespace

from agent.runtime.shared_state_schema import SharedStateSchema
from apps.enterprise_document.schemas.gate_decision import DeliveryMode
from apps.enterprise_document.schemas.project_input_schema import ProjectInputSchema
from apps.enterprise_document.schemas.scheme_writer_schema import (
    SchemeSectionSchema,
    SectionEvalSchema,
    SectionExecutionRequestSchema,
    SectionPlanSchema,
)
from apps.enterprise_document.services.gate_enforcement import GateEnforcementController
from apps.enterprise_document.services.scheme_writer.document_citation_registry import (
    DocumentCitationRegistry,
)
from apps.enterprise_document.services.scheme_writer.grounding_repair_service import (
    GroundingRepairService,
)
from apps.enterprise_document.services.scheme_writer.prompt_service import SectionPromptService
from apps.enterprise_document.services.scheme_writer.runtime_support import (
    SchemeWriterRuntimeSupport,
)
from apps.enterprise_document.services.scheme_writer.section_execution_coordinator import (
    SectionExecutionCoordinator,
)
from context_manager import LLMContextManager
from context_manager.policies import SectionGenerationContextPolicy
from rag.application.parent_child_retrieval import ParentChildRetrievalPipeline
from rag.catalog.document_profile import ClaimType
from rag.judge.crag_judge import CRAGJudge
from rag.mapping.evidence_mapper import EvidenceMapper
from rag.ports.quality import EvidenceAssessment, EvidenceJudgement
from rag.schema.evidence_assessment_v2 import EvidenceStatus
from rag.evidence.eligibility_policy import (
    EVIDENCE_USE_ELIGIBILITY_POLICY_ID,
    EVIDENCE_USE_ELIGIBILITY_POLICY_VERSION,
)
from contracts.evidence import build_evidence_bundle, project_evidence_bundle
from schemas.evidence_assessment import EvidenceAssessmentV2Schema, EvidenceRelevance
from schemas.evidence_source import (
    AuthorityLevel,
    DocumentType,
    EvidenceRole,
    ProjectRelation,
)
from schemas.citation import CitationSchema
from schemas.context import ContextBundleSchema, TaskContextSchema, UserContextSchema
from schemas.rag import (
    EvidenceBundleSchema,
    RAGContextSchema,
    RAGEvidenceAssessmentSchema,
    RetrievedChunkSchema,
)
from schemas.status import ExecutionStatus


NOW = "2026-08-05T14:00:00+00:00"


class _PromptRecorder:
    def __init__(self) -> None:
        self.prompt = ""
        self.system_prompt = ""

    def generate(self, prompt: str, **kwargs) -> str:
        self.prompt = prompt
        self.system_prompt = str(kwargs.get("system_prompt") or "")
        return (
            '{"relevance_label":"partial","score":0.6,'
            '"reason":"supports a reference method"}'
        )


def test_crag_prompt_separates_relevance_from_current_project_fact_authority() -> None:
    recorder = _PromptRecorder()
    judge = CRAGJudge(llm_generator=recorder, use_llm=True)

    judgement = judge.judge_chunk(
        query="政务云总体架构",
        result={
            "chunk_id": "history-1",
            "title": "历史政务云案例",
            "text": "该历史案例采用分层架构，可用于说明典型架构思路。",
        },
        rank=1,
    )

    assert judgement["relevance_label"] == "partial"
    full_prompt = recorder.system_prompt + "\n" + recorder.prompt
    assert "只判断片段是否与当前问题或章节主题相关" in full_prompt
    assert "不要判断片段能否证明当前项目事实" in full_prompt
    assert "不是当前项目事实" in full_prompt
    assert "不等于" in full_prompt
    assert "历史案例" in full_prompt
    assert "模板材料" in full_prompt


def test_crag_insufficient_collection_keeps_relevant_and_partial_survivors() -> None:
    results = [
        {"chunk_id": "relevant", "parent_chunk_id": "relevant", "metadata": {}},
        {"chunk_id": "partial", "parent_chunk_id": "partial", "metadata": {}},
        *[
            {
                "chunk_id": f"irrelevant-{index}",
                "parent_chunk_id": f"irrelevant-{index}",
                "metadata": {},
            }
            for index in range(1, 5)
        ],
    ]
    assessment = EvidenceAssessment(
        sufficient=False,
        confidence=0.2,
        reason="collection coverage is below threshold",
        item_judgements=(
            EvidenceJudgement(
                evidence_id="relevant",
                relevance="relevant",
                confidence=0.9,
                reason="directly relevant",
            ),
            EvidenceJudgement(
                evidence_id="partial",
                relevance="partial",
                confidence=0.6,
                reason="usable as a reference method",
            ),
            *tuple(
                EvidenceJudgement(
                    evidence_id=f"irrelevant-{index}",
                    relevance="irrelevant",
                    confidence=0.0,
                    reason="unrelated",
                )
                for index in range(1, 5)
            ),
        ),
    )

    _, selected, report = ParentChildRetrievalPipeline._apply_final_assessment(
        results=results,
        assessment=assessment,
    )

    assert [item["chunk_id"] for item in selected] == ["relevant", "partial"]
    assert report["assessment_sufficient"] is False
    assert report["output_count"] == 2
    assert report["rejected_irrelevant_ids"] == [
        "irrelevant-1",
        "irrelevant-2",
        "irrelevant-3",
        "irrelevant-4",
    ]


def test_crag_partial_only_survives_even_when_collection_is_insufficient() -> None:
    results = [
        {"chunk_id": "history-case", "parent_chunk_id": "history-case", "metadata": {}}
    ]
    assessment = EvidenceAssessment(
        sufficient=False,
        confidence=0.3,
        reason="reference evidence does not cover every requested topic",
        item_judgements=(
            EvidenceJudgement(
                evidence_id="history-case",
                relevance="partial",
                confidence=0.7,
                reason="supports a typical architecture as reference",
            ),
        ),
    )

    _, selected, report = ParentChildRetrievalPipeline._apply_final_assessment(
        results=results,
        assessment=assessment,
    )

    assert [item["chunk_id"] for item in selected] == ["history-case"]
    assert report["assessment_sufficient"] is False
    assert report["rejected_irrelevant_ids"] == []


def _gate_bundle(
    mode: DeliveryMode,
    *,
    marker: str = "initial",
    retrieval_scope: str = "document",
) -> EvidenceBundleSchema:
    if mode is DeliveryMode.GROUNDED:
        chunk = RetrievedChunkSchema(
            rank=1,
            matched_chunk_id=f"{marker}-child",
            context_chunk_id=f"{marker}-parent",
            child_chunk_id=f"{marker}-child",
            parent_chunk_id=f"{marker}-parent",
            doc_id=f"{marker}-doc",
            match_text=f"{marker.upper()}_SAFE_EVIDENCE",
            context_text=f"{marker.upper()}_SAFE_EVIDENCE",
        )
        citation = CitationSchema(
            citation_id=f"{marker}-source",
            source_type="document",
            source_document_id=chunk.doc_id,
            doc_id=chunk.doc_id,
            child_chunk_id=chunk.child_chunk_id,
            parent_chunk_id=chunk.parent_chunk_id,
            quote_text=chunk.context_text,
        )
        bundle = build_evidence_bundle(
            query="存储资源建设方案",
            rewritten_queries=[],
            selected_chunks=[chunk],
            dropped_chunks=[],
            citations=[citation],
            trace=None,
            max_context_chars=6000,
        )
        assessment = RAGEvidenceAssessmentSchema(
            assessment_state="completed",
            status=EvidenceStatus.SUFFICIENT,
            coverage_ratio=1.0,
            required_topics=["存储资源建设方案"],
            covered_topics=["存储资源建设方案"],
            supported_claim_types=[ClaimType.GENERAL_METHOD],
            authoritative_evidence_present=True,
            prompt_evidence_source="test",
            reason_codes=["initial_evidence_permitted"],
        )
        item_assessment = EvidenceAssessmentV2Schema(
            evidence_id=bundle.selected_evidence_ids[0],
            document_id=chunk.doc_id,
            document_type=DocumentType.CURRENT_PROJECT_REQUIREMENT,
            evidence_role=EvidenceRole.PROJECT_FACT,
            project_relation=ProjectRelation.CURRENT_PROJECT,
            authority_level=AuthorityLevel.AUTHORITATIVE,
            eligibility_semantic_policy_id=EVIDENCE_USE_ELIGIBILITY_POLICY_ID,
            eligibility_semantic_policy_version=EVIDENCE_USE_ELIGIBILITY_POLICY_VERSION,
            semantic_relevance=1.0,
            eligibility=True,
            relevance=EvidenceRelevance.RELEVANT,
            supported_topics=["存储资源建设方案"],
            supported_claim_types=[ClaimType.GENERAL_METHOD],
            reason_codes=["RECOVERY_GUARD_FIXTURE_ELIGIBLE"],
        )
        return EvidenceBundleSchema.model_validate(
            bundle.model_copy(
                update={
                    "task_id": "task_recovery_guard",
                    "run_id": "run_recovery_guard",
                    "retrieval_trace_id": f"{marker}_trace",
                    "retrieval_scope": retrieval_scope,
                    "item_assessments": [item_assessment],
                    "assessment": assessment,
                    "extra": {
                        "prompt_evidence_contract": {
                            "schema_version": "prompt_evidence_contract_v1",
                            "source": "test",
                        },
                        "prompt_evidence_source": "test",
                    },
                },
                deep=True,
            ).model_dump(mode="python", round_trip=True)
        )

    bundle = build_evidence_bundle(
        query="存储资源建设方案",
        rewritten_queries=[],
        selected_chunks=[],
        dropped_chunks=[],
        citations=[],
        trace=None,
        max_context_chars=6000,
    )
    assessment = RAGEvidenceAssessmentSchema(
        assessment_state="completed",
        status=EvidenceStatus.INSUFFICIENT,
        coverage_ratio=0.0,
        required_topics=["存储资源建设方案"],
        missing_topics=["存储资源建设方案"],
        prompt_evidence_source="test",
        reason_codes=["recovery_evidence_absent"],
    )
    return EvidenceBundleSchema.model_validate(
        bundle.model_copy(
            update={
                "task_id": "task_recovery_guard",
                "run_id": "run_recovery_guard",
                "retrieval_trace_id": f"{marker}_trace",
                "retrieval_scope": retrieval_scope,
                "assessment": assessment,
                "extra": {
                    "prompt_evidence_contract": {
                        "schema_version": "prompt_evidence_contract_v1",
                        "source": "test",
                    },
                    "prompt_evidence_source": "test",
                },
            },
            deep=True,
        ).model_dump(mode="python", round_trip=True)
    )


def _gate_output(mode: DeliveryMode) -> dict:
    return {"evidence": _gate_bundle(mode).model_dump(mode="json")}


class _RecoveryEvidenceService:
    def __init__(self, mode: str = "blocked_empty") -> None:
        self.mode = mode
        self.retrieve_calls = 0

    def retrieve(self, *args, **kwargs):
        del args, kwargs
        self.retrieve_calls += 1
        mode = (
            DeliveryMode.GROUNDED
            if self.mode == "permitted"
            else DeliveryMode.BLOCKED
        )
        return _gate_bundle(
            mode,
            marker="recovery",
            retrieval_scope="recovery",
        )

    def extract_rag_output(self, shared_state, result):
        del shared_state
        context, chunks, citations = project_evidence_bundle(result)
        return (
            context,
            chunks,
            citations,
            {"evidence": result.model_dump(mode="json")},
        )


class _QueryBuilder:
    def build(self, project_input, section_title, *, recovery: bool):
        del project_input, section_title
        return "恢复检索查询" if recovery else "章节检索查询"


class _GenerationService:
    def __init__(self) -> None:
        self.calls: list[dict] = []

    def generate_section(self, shared_state, **kwargs) -> SchemeSectionSchema:
        del shared_state
        self.calls.append(dict(kwargs))
        recovered = len(self.calls) > 1
        return SchemeSectionSchema(
            section_id=f"section_run_recovery_guard_{kwargs['section_order']:03d}",
            section_title=kwargs["section_title"],
            section_order=kwargs["section_order"],
            content=(
                "RECOVERY_VALID_SECTION" if recovered else "INITIAL_VALID_SECTION"
            ),
            status=ExecutionStatus.SUCCESS,
            eval_result=SectionEvalSchema(
                passed=recovered,
                checks={"citation_bound": recovered, "model_success": True},
                failures=[] if recovered else ["citation_bound"],
            ),
            extra={"generation_check": {}},
        )

    def build_gate_blocked_section(self, shared_state, **kwargs) -> SchemeSectionSchema:
        del shared_state
        return SchemeSectionSchema(
            section_id=f"section_run_recovery_guard_{kwargs['section_order']:03d}",
            section_title=kwargs["section_title"],
            section_order=kwargs["section_order"],
            content="RECOVERY_BLOCKED_SECTION",
            status=ExecutionStatus.PARTIAL_SUCCESS,
            eval_result=SectionEvalSchema(
                passed=False,
                checks={"citation_bound": False, "generation_allowed": False},
                failures=["writer_generation_blocked"],
            ),
            extra={"generation_check": {}, "generation_blocked": True},
        )


def _recovery_request(
    *,
    initial_mode: DeliveryMode = DeliveryMode.GROUNDED,
) -> SectionExecutionRequestSchema:
    state = SharedStateSchema(
        task_id="task_recovery_guard",
        run_id="run_recovery_guard",
        task_type="scheme_generation",
        user_input="生成政务云存储资源建设方案",
        context_bundle=ContextBundleSchema(
            user=UserContextSchema(user_query="生成政务云存储资源建设方案"),
            task=TaskContextSchema(
                task_id="task_recovery_guard",
                run_id="run_recovery_guard",
                task_type="scheme_generation",
            ),
        ),
        created_at=NOW,
    )
    project_input = ProjectInputSchema.model_validate(
        {
            "task_id": "task_recovery_guard",
            "task_type": "scheme_generation",
            "user_query": "生成政务云存储资源建设方案",
            "generation_requirements": {
                "required_sections": ["存储资源建设方案"],
                "citation_required_sections": ["存储资源建设方案"],
                "need_citation": True,
            },
            "output_schema": {
                "document_title": "政务云建设方案",
                "required_sections": ["存储资源建设方案"],
            },
        }
    )
    initial_bundle = _gate_bundle(initial_mode)
    return SectionExecutionRequestSchema(
        shared_state=state,
        document_id="document_recovery_guard",
        project_input=project_input,
        section_plan=SectionPlanSchema(
            section_id="section_plan_recovery_guard",
            section_title="存储资源建设方案",
            section_order=1,
            citation_required=True,
        ),
        document_evidence_bundle=initial_bundle,
        document_rag_context=initial_bundle.context,
        document_rag_output=_gate_output(initial_mode),
        document_evidence_assessment={
            "status": (
                "sufficient"
                if initial_mode is DeliveryMode.GROUNDED
                else "insufficient"
            )
        },
        document_tool_call_ids=["initial_trace"],
        section_retrieval_enabled=False,
        corrective_retrieval_enabled=True,
    )


def test_blocked_empty_recovery_does_not_replace_permitted_initial_evidence() -> None:
    generation = _GenerationService()
    coordinator = SectionExecutionCoordinator(
        evidence_service=_RecoveryEvidenceService(),
        query_builder=_QueryBuilder(),
        section_generation_service=generation,
        runtime_support=SchemeWriterRuntimeSupport(),
        generation_quality_metadata={
            "max_retrieval_rounds": 1,
            "max_rewrite_rounds": 1,
            "max_total_llm_calls": 8,
            "max_total_tokens": 24000,
        },
    )

    result = coordinator.execute(
        _recovery_request(),
        citation_registry=DocumentCitationRegistry(),
    )

    assert result.section.content == "INITIAL_VALID_SECTION"
    assert result.evidence.retrieval_scope == "document"
    assert "INITIAL_SAFE_EVIDENCE" in result.evidence.rag_context.context_text
    assert "RECOVERY_SAFE_EVIDENCE" not in result.evidence.rag_context.context_text
    assert result.evidence.generation_allowed is True
    assert result.evidence.metadata["corrective_recovery_rejected"] == (
        "empty_recovery_context"
    )
    assert len(generation.calls) == 1


def test_permitted_recovery_replaces_initial_evidence_and_regenerates() -> None:
    generation = _GenerationService()
    evidence_service = _RecoveryEvidenceService(mode="permitted")
    coordinator = SectionExecutionCoordinator(
        evidence_service=evidence_service,
        query_builder=_QueryBuilder(),
        section_generation_service=generation,
        runtime_support=SchemeWriterRuntimeSupport(),
        generation_quality_metadata={
            "max_retrieval_rounds": 1,
            "max_rewrite_rounds": 1,
            "max_total_llm_calls": 8,
            "max_total_tokens": 24000,
        },
    )

    result = coordinator.execute(
        _recovery_request(),
        citation_registry=DocumentCitationRegistry(),
    )

    assert result.section.content == "RECOVERY_VALID_SECTION"
    assert result.evidence.retrieval_scope == "recovery"
    assert "RECOVERY_SAFE_EVIDENCE" in result.evidence.rag_context.context_text
    assert "INITIAL_SAFE_EVIDENCE" not in result.evidence.rag_context.context_text
    assert result.evidence.generation_allowed is True
    assert result.evidence.metadata["corrective_recovery_accepted"] is True
    assert len(generation.calls) == 2
    assert evidence_service.retrieve_calls == 1


def test_initial_blocked_gate_stays_on_existing_blocked_path() -> None:
    generation = _GenerationService()
    evidence_service = _RecoveryEvidenceService(mode="permitted")
    coordinator = SectionExecutionCoordinator(
        evidence_service=evidence_service,
        query_builder=_QueryBuilder(),
        section_generation_service=generation,
        runtime_support=SchemeWriterRuntimeSupport(),
        generation_quality_metadata={
            "max_retrieval_rounds": 1,
            "max_rewrite_rounds": 1,
            "max_total_llm_calls": 8,
            "max_total_tokens": 24000,
        },
    )

    result = coordinator.execute(
        _recovery_request(initial_mode=DeliveryMode.BLOCKED),
        citation_registry=DocumentCitationRegistry(),
    )

    assert result.section.content == "RECOVERY_BLOCKED_SECTION"
    assert result.evidence.generation_allowed is False
    assert result.evidence.retrieval_scope == "document"
    assert evidence_service.retrieve_calls == 0
    assert generation.calls == []


def test_shadow_initial_business_block_still_executes_writer() -> None:
    generation = _GenerationService()
    generation.gate_enforcement_controller = GateEnforcementController("shadow")
    evidence_service = _RecoveryEvidenceService(mode="permitted")
    coordinator = SectionExecutionCoordinator(
        evidence_service=evidence_service,
        query_builder=_QueryBuilder(),
        section_generation_service=generation,
        runtime_support=SchemeWriterRuntimeSupport(),
        generation_quality_metadata={
            "max_retrieval_rounds": 1,
            "max_rewrite_rounds": 1,
            "max_total_llm_calls": 8,
            "max_total_tokens": 24000,
        },
    )

    result = coordinator.execute(
        _recovery_request(initial_mode=DeliveryMode.BLOCKED),
        citation_registry=DocumentCitationRegistry(),
    )

    assert generation.calls
    assert result.section.content != "RECOVERY_BLOCKED_SECTION"
    assert result.evidence.metadata["writer_gate_enforcement"] == {
        "enforcement_mode": "shadow",
        "enforcement_action": "continue",
        "would_block_in_strict": True,
        "failure_kind": "business",
        "gate_passed": False,
        "gate_id": "G06/G11",
    }


def test_writer_prompt_catalog_uses_governed_projection_not_raw_citation_quote() -> None:
    mapper = EvidenceMapper()
    chunks = mapper.chunks(
        [
            {
                "rank": 1,
                "child_chunk_id": "history-storage-child",
                "parent_chunk_id": "history-storage-parent",
                "doc_id": "history-storage-doc",
                "child_text": "历史项目容量324.2TB，配置8台XSSD，采用Kunpeng 920。",
                "parent_text": "历史项目容量324.2TB，配置8台XSSD，采用Kunpeng 920。",
                "prompt_visible_text": (
                    "该历史案例仅用于说明容量测算和资源规划方法，"
                    "具体容量、设备数量、品牌和配置不得作为本项目事实。"
                ),
                "metadata": {},
            }
        ]
    )
    citations = mapper.citations(chunks)

    assert len(citations) == 1
    assert "324.2TB" in (citations[0].quote_text or "")
    assert "prompt_visible_text" in citations[0].extra

    catalog = SectionPromptService.citation_catalog(citations)
    compact_catalog, _, _ = SectionGenerationContextPolicy._citation_catalog(
        citations,
        max_items=3,
        max_quote_chars=240,
    )
    for rendered in (catalog, compact_catalog):
        assert "容量测算和资源规划方法" in rendered
        assert "324.2TB" not in rendered
        assert "8台XSSD" not in rendered
        assert "Kunpeng 920" not in rendered


def test_writer_prompt_catalog_never_falls_back_to_raw_quote_without_projection() -> None:
    citation = CitationSchema(
        citation_id="C1",
        source_type="document",
        doc_id="history-security-doc",
        chunk_id="history-security-child",
        title="历史安全案例",
        quote_text="HTTPS、AES、手机号验证码、指纹识别、面部识别。",
        summary="历史案例，仅供内部审计。",
    )

    catalog = SectionPromptService.citation_catalog([citation])
    compact_catalog, _, _ = SectionGenerationContextPolicy._citation_catalog(
        [citation],
        max_items=3,
        max_quote_chars=240,
    )
    for rendered in (catalog, compact_catalog):
        assert "HTTPS" not in rendered
        assert "AES" not in rendered
        assert "手机号验证码" not in rendered
        assert "指纹识别" not in rendered
        assert "面部识别" not in rendered


def test_grounded_regeneration_compacts_existing_inputs_without_truncating_body() -> None:
    class _ModelService:
        def __init__(self) -> None:
            self.prompt = ""
            self.context_manager = LLMContextManager()
            self.model_gateway = SimpleNamespace(
                routing_capabilities=lambda **kwargs: {
                    "safe_max_output_tokens": 1024,
                    "safe_context_window": 8192,
                }
            )

        def call_model(self, shared_state, **kwargs):
            del shared_state
            self.prompt = str(kwargs["prompt"])
            return SimpleNamespace(success=True, content="重写后的正文")

    model_service = _ModelService()
    service = GroundingRepairService(
        model_service=model_service,
        prompt_service=SimpleNamespace(),
        citation_service=SimpleNamespace(),
    )
    project_input = ProjectInputSchema.model_validate(
        {
            "task_id": "task_context_budget",
            "tenant_id": "tenant_context_budget",
            "task_type": "scheme_generation",
            "user_query": "编制政务云安全设计章节",
            "project_name": "政务云扩容项目",
            "project_type": "government_cloud",
            "business_goal": "形成安全设计建议并明确待确认事项",
            "source_materials": [
                {
                    "material_type": "history_case",
                    "material_name": "历史案例",
                    "file_ids": ["raw-file-id"],
                    "doc_ids": ["raw-doc-id"],
                    "metadata": {"irrelevant_runtime_payload": "Y" * 1800},
                }
            ],
            "generation_requirements": {
                "required_sections": ["安全设计"],
                "citation_required_sections": ["安全设计"],
                "need_citation": True,
                "max_context_chars": 6000,
                "extra": {"irrelevant_generation_payload": "Z" * 1800},
            },
            "output_schema": {
                "document_title": "政务云建设方案",
                "required_sections": ["安全设计"],
            },
            "policy_requirements": ["遵循现有安全管理制度"],
            "missing_information": ["具体安全产品和参数待确认"],
            "metadata": {"irrelevant_task_payload": "X" * 1800},
            "extra": {
                "resource_configuration": {"security_strategy": "按风险分级设计"}
            },
        }
    )
    citation = CitationSchema(
        citation_id="C1",
        source_type="document",
        source_document_id="history-security-doc",
        chunk_id="history-security-child",
        title="历史安全案例",
        section="安全设计",
        quote_text="HTTPS、AES、手机号验证码、指纹识别、面部识别。",
        extra={"prompt_visible_text": "历史案例仅用于说明通用安全设计方法。"},
    )
    original_content = "原始失败正文：" + "需要删除无依据描述。" * 120

    service.regenerate_section_from_evidence(
        SimpleNamespace(
            task_id="task_context_budget",
            run_id="run_context_budget",
        ),
        original_content=original_content,
        section_id="section_security",
        section_title="安全设计",
        project_input=project_input,
        citations=[citation],
    )

    prompt = model_service.prompt
    assert len(prompt) <= project_input.generation_requirements.max_context_chars
    assert original_content in prompt
    assert "政务云扩容项目" in prompt
    assert "形成安全设计建议并明确待确认事项" in prompt
    assert "遵循现有安全管理制度" in prompt
    assert "具体安全产品和参数待确认" in prompt
    assert "历史案例仅用于说明通用安全设计方法" in prompt
    assert "task_context_budget" not in prompt
    assert "raw-file-id" not in prompt
    assert "raw-doc-id" not in prompt
    assert "max_context_chars" not in prompt
    assert "irrelevant_runtime_payload" not in prompt
    assert "irrelevant_generation_payload" not in prompt
    assert "irrelevant_task_payload" not in prompt
    assert "history-security-doc" not in prompt
    assert "history-security-child" not in prompt
    assert "HTTPS" not in prompt
    assert "AES" not in prompt
    assert "手机号验证码" not in prompt
    assert "指纹识别" not in prompt
    assert "面部识别" not in prompt
