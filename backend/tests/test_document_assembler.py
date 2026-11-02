from apps.enterprise_document.schemas.scheme_writer_schema import (
    DocumentAssemblyRequestSchema,
    DocumentAssemblyResultSchema,
    SchemeSectionSchema,
    SectionEvidenceBundleSchema,
)
from apps.enterprise_document.services.scheme_writer.document_assembler import (
    DocumentAssembler,
)
from contracts.evidence import build_evidence_bundle
from rag.evidence.eligibility_policy import (
    EVIDENCE_USE_ELIGIBILITY_POLICY_ID,
    EVIDENCE_USE_ELIGIBILITY_POLICY_VERSION,
)
from schemas.citation import CitationBindingSchema, CitationSchema
from schemas.claim import ClaimType
from schemas.evidence_assessment import EvidenceAssessmentV2Schema, EvidenceRelevance
from schemas.evidence_source import (
    AuthorityLevel,
    DocumentType,
    EvidenceRole,
    ProjectRelation,
)
from schemas.rag import (
    EvidenceStatus,
    RAGEvidenceAssessmentSchema,
    RetrievedChunkSchema,
)
from schemas.status import ExecutionStatus


def _chunk(
    chunk_id: str,
    rank: int,
    *,
    parent_id: str,
    metadata: dict | None = None,
) -> RetrievedChunkSchema:
    return RetrievedChunkSchema(
        rank=rank,
        matched_chunk_id=chunk_id,
        context_chunk_id=parent_id,
        child_chunk_id=chunk_id,
        parent_chunk_id=parent_id,
        doc_id="doc_1",
        match_text=f"match-{chunk_id}",
        context_text=f"context-{chunk_id}",
        metadata=metadata or {},
    )


def _citation(citation_id: str = "C1") -> CitationSchema:
    return CitationSchema(
        citation_id=citation_id,
        source_type="document",
        doc_id="doc_1",
        source_document_id="doc_1",
        parent_chunk_id="parent_1",
        child_chunk_id="c1",
        quote_text="evidence quote",
    )


def _binding() -> CitationBindingSchema:
    return CitationBindingSchema(
        binding_id="binding_1",
        citation_id="C1",
        target_document_id="document_run_1",
        target_section_id="section_001",
        target_paragraph_id="p1",
        target_claim_id="claim_1",
        source_document_id="doc_1",
        source_chunk_id="c1",
        source_parent_chunk_id="parent_1",
        claim_text="已具备企业级知识检索能力。",
        quote_text="evidence quote",
    )


def _section(
    section_id: str,
    title: str,
    order: int,
    content: str,
    *,
    bindings: list[CitationBindingSchema] | None = None,
) -> SchemeSectionSchema:
    return SchemeSectionSchema(
        section_id=section_id,
        section_title=title,
        section_order=order,
        content=content,
        status=ExecutionStatus.SUCCESS,
        citation_bindings=bindings or [],
    )


def _bundle(
    *,
    status: str,
    citations: list[CitationSchema] | None = None,
    details: dict | None = None,
    with_evidence: bool = True,
):
    chunks = [_chunk("c1", 1, parent_id="parent_1")] if with_evidence else []
    bundle = build_evidence_bundle(
        query="evidence query",
        rewritten_queries=[],
        selected_chunks=chunks,
        dropped_chunks=[],
        citations=list(citations or []),
        trace=None,
        max_context_chars=6000,
    )
    normalized = status.strip().lower()
    if normalized == "not_assessed":
        assessment = RAGEvidenceAssessmentSchema(
            assessment_state="pending",
            status=None,
            evidence_available=bool(chunks),
            selected_evidence_count=len(chunks),
            citation_count=len(citations or []),
            details=dict(details or {}),
        )
    else:
        evidence_status = EvidenceStatus(normalized)
        assessment = RAGEvidenceAssessmentSchema(
            assessment_state="completed",
            status=evidence_status,
            evidence_available=bool(chunks),
            selected_evidence_count=len(chunks),
            citation_count=len(citations or []),
            coverage_ratio=1.0 if evidence_status is EvidenceStatus.SUFFICIENT else 0.0,
            details=dict(details or {}),
        )
    item_assessments = []
    if chunks and assessment.assessment_state == "completed":
        item_assessments = [
            EvidenceAssessmentV2Schema(
                evidence_id=bundle.selected_evidence_ids[0],
                document_id=chunks[0].doc_id,
                document_type=DocumentType.CURRENT_PROJECT_REQUIREMENT,
                evidence_role=EvidenceRole.PROJECT_FACT,
                project_relation=ProjectRelation.CURRENT_PROJECT,
                authority_level=AuthorityLevel.AUTHORITATIVE,
                eligibility_semantic_policy_id=EVIDENCE_USE_ELIGIBILITY_POLICY_ID,
                eligibility_semantic_policy_version=EVIDENCE_USE_ELIGIBILITY_POLICY_VERSION,
                semantic_relevance=1.0,
                eligibility=True,
                relevance=EvidenceRelevance.RELEVANT,
                supported_topics=["document assembler fixture"],
                supported_claim_types=[ClaimType.GENERAL_METHOD],
                reason_codes=["DOCUMENT_ASSEMBLER_FIXTURE_ELIGIBLE"],
            )
        ]
    return bundle.model_copy(
        update={
            "assessment": assessment,
            "item_assessments": item_assessments,
        },
        deep=True,
    )


def _evidence(
    title: str,
    *,
    status: str,
    citations: list[CitationSchema] | None = None,
) -> SectionEvidenceBundleSchema:
    return SectionEvidenceBundleSchema(
        section_id=f"section_{title}",
        section_title=title,
        evidence_bundle=_bundle(status=status, citations=citations),
    )


def test_document_assembler_builds_deterministic_document_aggregate() -> None:
    first_chunk = _chunk("c1", 1, parent_id="parent_1")
    duplicate_first = _chunk("c1", 2, parent_id="parent_duplicate")
    second_chunk = _chunk(
        "c2",
        3,
        parent_id="parent_2",
        metadata={"matched_child_chunk_ids": ["c2_alias"]},
    )
    sections = [
        _section(
            "section_001",
            "项目概述",
            1,
            "内容A",
            bindings=[_binding()],
        ),
        _section("section_002", "技术方案", 2, "内容B"),
    ]

    result = DocumentAssembler().assemble(
        DocumentAssemblyRequestSchema(
            task_id="task_1",
            run_id="run_1",
            document_id="document_run_1",
            document_title="企业级 RAG-Agent 建设方案",
            required_sections=["项目概述", "技术方案", "安全设计"],
            sections=sections,
            retrieved_chunks=[first_chunk, duplicate_first, second_chunk],
            citations=[_citation()],
            section_evidence=[
                _evidence("技术方案", status="sufficient", citations=[_citation()])
            ],
            document_evidence_bundle=_bundle(
                status="sufficient", citations=[_citation()]
            ),
            citation_required_sections=["技术方案"],
            created_at="2026-07-22T00:00:00+00:00",
            updated_at="2026-07-22T00:01:00+00:00",
        )
    )

    assert isinstance(result, DocumentAssemblyResultSchema)
    assert result.draft.full_text == "1、项目概述\n内容A\n\n2、技术方案\n内容B"
    assert result.draft.missing_sections == ["安全设计"]
    assert result.draft.summary == "共生成2个章节。"
    assert [chunk.matched_chunk_id for chunk in result.retrieved_chunks] == [
        "c1",
        "c2",
    ]
    assert {"c1", "parent_1", "c2", "parent_2", "c2_alias"}.issubset(
        result.known_chunk_ids
    )
    assert [binding.binding_id for binding in result.citation_bindings] == [
        "binding_1"
    ]
    assert [citation.citation_id for citation in result.citations] == ["C1"]
    assert result.evidence_available is True
    assert result.semantic_evidence_sufficient is True


def test_required_section_insufficient_evidence_overrides_document_sufficient() -> None:
    result = DocumentAssembler().assemble(
        DocumentAssemblyRequestSchema(
            task_id="task_1",
            run_id="run_1",
            document_id="document_run_1",
            document_title="建设方案",
            required_sections=["技术方案"],
            sections=[_section("section_001", "技术方案", 1, "内容")],
            section_evidence=[_evidence("技术方案", status="insufficient")],
            document_evidence_bundle=_bundle(status="sufficient"),
            citation_required_sections=["技术方案"],
            created_at="2026-07-22T00:00:00+00:00",
            updated_at="2026-07-22T00:01:00+00:00",
        )
    )

    assert result.evidence_available is True
    assert result.semantic_evidence_sufficient is False


def test_final_required_section_assessments_override_coarse_document_insufficient() -> None:
    result = DocumentAssembler().assemble(
        DocumentAssemblyRequestSchema(
            task_id="task_1",
            run_id="run_1",
            document_id="document_run_1",
            document_title="建设方案",
            required_sections=["技术方案", "安全设计"],
            sections=[
                _section("section_001", "技术方案", 1, "技术内容"),
                _section("section_002", "安全设计", 2, "安全内容"),
            ],
            section_evidence=[
                _evidence("技术方案", status="sufficient"),
                _evidence("安全设计", status="sufficient"),
            ],
            document_evidence_bundle=_bundle(status="insufficient"),
            citation_required_sections=["技术方案", "安全设计"],
            created_at="2026-07-22T00:00:00+00:00",
            updated_at="2026-07-22T00:01:00+00:00",
        )
    )

    assert result.semantic_evidence_sufficient is True


def test_missing_required_section_assessment_falls_back_to_document_status() -> None:
    result = DocumentAssembler().assemble(
        DocumentAssemblyRequestSchema(
            task_id="task_1",
            run_id="run_1",
            document_id="document_run_1",
            document_title="建设方案",
            required_sections=["技术方案", "安全设计"],
            sections=[
                _section("section_001", "技术方案", 1, "技术内容"),
                _section("section_002", "安全设计", 2, "安全内容"),
            ],
            section_evidence=[
                _evidence("技术方案", status="sufficient"),
            ],
            document_evidence_bundle=_bundle(status="insufficient"),
            citation_required_sections=["技术方案", "安全设计"],
            created_at="2026-07-22T00:00:00+00:00",
            updated_at="2026-07-22T00:01:00+00:00",
        )
    )

    assert result.semantic_evidence_sufficient is False


def test_not_assessed_evidence_does_not_fall_back_to_availability() -> None:
    result = DocumentAssembler().assemble(
        DocumentAssemblyRequestSchema(
            task_id="task_1",
            run_id="run_1",
            document_id="document_run_1",
            document_title="建设方案",
            required_sections=["项目概述"],
            sections=[_section("section_001", "项目概述", 1, "内容")],
            section_evidence=[
                _evidence("项目概述", status="not_assessed", citations=[_citation()])
            ],
            document_evidence_bundle=_bundle(
                status="not_assessed", with_evidence=False
            ),
            citation_required_sections=["项目概述"],
            created_at="2026-07-22T00:00:00+00:00",
            updated_at="2026-07-22T00:01:00+00:00",
        )
    )

    assert result.evidence_available is True
    assert result.semantic_evidence_sufficient is False
    assert result.evidence_requirements_satisfied is False


def test_document_assembly_contracts_are_available_from_compatibility_exports() -> None:
    from apps.enterprise_document.schemas.scheme_writer_schema import (
        DocumentAssemblyRequestSchema as CompatibilityRequest,
        DocumentAssemblyResultSchema as CompatibilityResult,
    )

    assert CompatibilityRequest is DocumentAssemblyRequestSchema
    assert CompatibilityResult is DocumentAssemblyResultSchema
