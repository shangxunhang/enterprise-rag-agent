"""Adversarial closure tests for high-risk facts and citation occurrences."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from apps.enterprise_document.agents.scheme_writer_agent import SchemeWriterAgent
from apps.enterprise_document.schemas.project_input_schema import ProjectInputSchema
from apps.enterprise_document.schemas.scheme_writer_schema import (
    DocumentAssemblyRequestSchema,
    SchemeDraftSchema,
    SchemeSectionSchema,
    SectionEvalSchema,
    TruncationCheckSchema,
)
from apps.enterprise_document.services.document_gate import evaluate_scheme_draft
from apps.enterprise_document.services.scheme_writer.advisory_service import (
    SectionAdvisoryService,
)
from apps.enterprise_document.services.scheme_writer.citation_service import (
    CitationService,
)
from apps.enterprise_document.services.scheme_writer.document_assembler import (
    DocumentAssembler,
)
from apps.enterprise_document.services.scheme_writer.high_risk_claim_service import (
    HighRiskClaimExtractor,
    HighRiskClaimGate,
)
from apps.enterprise_document.services.scheme_writer.trusted_fact_ledger import (
    TrustedFactLedgerBuilder,
)
from schemas.citation import CitationBindingSchema, CitationSchema
from schemas.model import ModelResponseSchema
from schemas.rag import RAGContextSchema
from schemas.status import ExecutionStatus


NOW = "2026-07-25T00:00:00+00:00"


def _project_input(
    *,
    user_query: str = "生成资源配置说明",
    business_goal: str = "",
    metadata: dict | None = None,
    **project_fields,
) -> ProjectInputSchema:
    payload = {
        "task_id": "task-p1-integrity",
        "task_type": "scheme_generation",
        "user_query": user_query,
        "business_goal": business_goal,
        "generation_requirements": {
            "required_sections": ["资源配置"],
            "citation_required_sections": [],
            "min_section_chars": 8,
            "max_section_retries": 0,
        },
        "output_schema": {"required_sections": ["资源配置"]},
        "metadata": dict(metadata or {}),
    }
    payload.update(project_fields)
    return ProjectInputSchema.model_validate(payload)


def _violations(
    content: str,
    project_input: ProjectInputSchema | None = None,
) -> list[dict]:
    return SectionAdvisoryService.mandatory_project_fact_violations(
        content,
        project_input or _project_input(),
    )


@pytest.mark.parametrize(
    ("project_fields", "content"),
    [
        (
            {"user_query": "部署4台服务器"},
            "本项目部署4台服务器。",
        ),
        (
            {"business_goal": "部署4台服务器"},
            "本项目部署4台服务器。",
        ),
    ],
)
def test_free_text_fields_never_support_high_risk_fact(
    project_fields: dict,
    content: str,
) -> None:
    assert _violations(content, _project_input(**project_fields))


@pytest.mark.parametrize(
    "content",
    [
        "不部署原有服务器并新增4台服务器。",
        "不部署原有服务器且新增4台服务器。",
        "是否扩容已确认本项目部署4台服务器。",
        "是否调整网络，但本项目部署4台服务器。",
        "本项目部署1000台服务器建议后续按需扩容。",
        "本项目部署1000台服务器可考虑分期实施。",
        "原则上部署1000台服务器。",
        "建议部署1000台服务器。",
        "部署1000台服务器，具体数量待确认。",
        "不部署4台服务器。",
        "是否部署4台服务器。",
    ],
)
def test_every_concrete_high_risk_value_requires_support(content: str) -> None:
    violations = _violations(content)

    assert violations
    assert all(
        item["reason"] == "mandatory_project_fact_not_supported"
        for item in violations
    )


@pytest.mark.parametrize(
    "content",
    [
        "服务器数量待确认。",
        "服务器规模尚未确定。",
        "本方案暂不形成服务器数量承诺。",
    ],
)
def test_high_risk_language_without_concrete_value_remains_allowed(
    content: str,
) -> None:
    assert _violations(content) == []


def test_only_exact_structured_fact_identity_supports_server_count() -> None:
    supported = _project_input(metadata={"server_count": 4})
    wrong_department = _project_input(functional_department_count=4)
    wrong_gpu = _project_input(metadata={"gpu_count": 4})

    assert _violations("本项目部署4台服务器。", supported) == []
    assert _violations("本项目部署4台服务器。", wrong_department)
    assert _violations("本项目部署4台服务器。", wrong_gpu)


def test_trusted_ledger_excludes_free_text_and_keeps_allowlisted_metadata() -> None:
    free_text_only = TrustedFactLedgerBuilder().build(
        _project_input(
            user_query="部署4台服务器",
            business_goal="部署4台服务器",
        )
    )
    structured = TrustedFactLedgerBuilder().build(
        _project_input(metadata={"server_count": 4, "unrelated_count": 4})
    )

    assert free_text_only == ()
    assert [fact.identity for fact in structured] == [
        ("hardware_count", "server", 4, "count")
    ]


def test_extractor_emits_exact_identities_and_source_spans() -> None:
    content = (
        "配置4台服务器、8张GPU和20个节点，"
        "项目工期为6个月，预算100万元，准确率达到95%。"
    )

    claims = HighRiskClaimExtractor().extract(content)

    assert {
        (claim.fact_type, claim.subject, claim.value, claim.unit)
        for claim in claims
    } >= {
        ("hardware_count", "server", 4, "count"),
        ("hardware_count", "gpu", 8, "count"),
        ("hardware_count", "node", 20, "count"),
        ("duration", "project", 6, "month"),
        ("finance", "budget", 1_000_000, "yuan"),
        ("performance", "accuracy", 95, "percent"),
    }
    assert all(
        content[claim.span_start:claim.span_end] == claim.claim_text
        for claim in claims
    )


def test_ambiguous_scope_fails_closed_even_when_value_is_structured() -> None:
    result = HighRiskClaimGate().evaluate(
        "是否不部署4台服务器。",
        project_input=_project_input(metadata={"server_count": 4}),
    )

    assert result.violations
    assert result.violations[0]["commitment"] == "ambiguous"
    assert (
        result.violations[0]["reason"]
        == "ambiguous_high_risk_claim_scope"
    )


def _binding(
    *,
    binding_id: str,
    citation_id: str = "C1",
    claim_text: str,
    claim_id: str,
    verified: bool = True,
) -> CitationBindingSchema:
    return CitationBindingSchema(
        binding_id=binding_id,
        citation_id=citation_id,
        target_document_id="document-p1-integrity",
        target_section_id="section-p1-integrity",
        target_paragraph_id="section-p1-integrity_paragraph_001",
        target_claim_id=claim_id,
        source_document_id="source-p1-integrity",
        source_chunk_id="chunk-p1-integrity",
        claim_text=claim_text,
        quote_text=claim_text,
        metadata={"grounding_verified": verified},
    )


def _citation() -> CitationSchema:
    return CitationSchema(
        citation_id="C1",
        source_type="rag",
        source_document_id="source-p1-integrity",
        chunk_id="chunk-p1-integrity",
        quote_text="第一项事实。第二项事实。",
    )


def test_reconciliation_validates_each_same_id_marker_occurrence() -> None:
    content = "第一项事实。[C1]\n\n第二项事实。[C1]"
    first_only = _binding(
        binding_id="binding-first",
        claim_text="第一项事实。",
        claim_id="section-p1-integrity_paragraph_001_claim_001",
    )

    reconciled = CitationService.reconcile_citation_markers(
        content,
        [first_only],
    )

    assert reconciled.count("[C1]") == 1
    assert "第一项事实。[C1]" in reconciled
    assert "第二项事实。[C1]" not in reconciled


def _draft(
    content: str,
    bindings: list[CitationBindingSchema],
    *,
    project_input: ProjectInputSchema | None = None,
    mandatory_violations: list[dict] | None = None,
) -> SchemeDraftSchema:
    section = SchemeSectionSchema(
        section_id="section-p1-integrity",
        section_title="资源配置",
        section_order=1,
        input=(
            {"project_input": project_input.model_dump()}
            if project_input is not None
            else {}
        ),
        content=content,
        status=ExecutionStatus.SUCCESS,
        citation_ids=list(dict.fromkeys(item.citation_id for item in bindings)),
        citation_bindings=list(bindings),
        truncation=TruncationCheckSchema(truncated=False),
        eval_result=SectionEvalSchema(passed=True),
        extra={
            "mandatory_project_fact_violations": list(
                mandatory_violations or []
            )
        },
    )
    return SchemeDraftSchema(
        draft_id="draft-p1-integrity",
        document_id="document-p1-integrity",
        task_id="task-p1-integrity",
        run_id="run-p1-integrity",
        title="资源配置报告",
        full_text=f"1、资源配置\n{content}",
        sections=[section],
        required_sections=["资源配置"],
        citation_bindings=list(bindings),
        created_at=NOW,
    )


def _evaluate(
    draft: SchemeDraftSchema,
    *,
    canonical_citations: list[CitationSchema] | None = None,
):
    return evaluate_scheme_draft(
        draft,
        citation_required=False,
        retrieved_chunk_ids=["chunk-p1-integrity"],
        canonical_citations=list(canonical_citations or []),
    )


def test_document_gate_rechecks_final_text_instead_of_trusting_section_extra() -> None:
    draft = _draft(
        "本项目部署4台服务器。",
        [],
        project_input=_project_input(),
        mandatory_violations=[],
    )

    result = _evaluate(draft)

    assert result.checks["high_risk_claims_grounded"] is False
    assert result.passed is False


def test_final_text_duplicate_fact_cannot_reuse_one_verified_occurrence() -> None:
    citation = CitationSchema(
        citation_id="C1",
        source_type="rag",
        source_document_id="source-p1-integrity",
        chunk_id="chunk-p1-integrity",
        quote_text="本项目部署4台服务器。",
    )
    content = "本项目部署4台服务器。[C1]"
    raw_binding = CitationService.bind_citations(
        "document-p1-integrity",
        "section-p1-integrity",
        content,
        [citation],
    )[0]
    verified_binding = raw_binding.model_copy(
        update={"metadata": {"grounding_verified": True}}
    )
    draft = _draft(
        content,
        [verified_binding],
        project_input=_project_input(),
    )
    draft.full_text += "\n\n本项目部署4台服务器。"

    result = _evaluate(draft, canonical_citations=[citation])

    assert result.checks["high_risk_claims_grounded"] is False
    assert result.passed is False


def test_same_id_bindings_cannot_both_target_one_marker_occurrence() -> None:
    first = _binding(
        binding_id="binding-first-a",
        claim_text="第一项事实。",
        claim_id="shared-claim",
    )
    duplicate = _binding(
        binding_id="binding-first-b",
        claim_text="第一项事实。",
        claim_id="shared-claim",
    )
    draft = _draft(
        "第一项事实。[C1]\n\n第二项事实。[C1]",
        [first, duplicate],
    )

    result = _evaluate(draft, canonical_citations=[_citation()])

    assert result.checks["citation_marker_binding_bijection"] is False
    assert result.passed is False


def test_binding_claim_must_point_to_a_real_marker_occurrence() -> None:
    nonexistent = _binding(
        binding_id="binding-nonexistent",
        claim_text="正文中不存在的事实。",
        claim_id="missing-claim",
    )
    draft = _draft("第一项事实。[C1]", [nonexistent])

    result = _evaluate(draft, canonical_citations=[_citation()])

    assert result.checks["citation_orphan_bindings_absent"] is False
    assert result.passed is False


def test_section_finalize_orphan_marker_is_rejected() -> None:
    binding = _binding(
        binding_id="binding-first",
        claim_text="第一项事实。",
        claim_id="section-p1-integrity_paragraph_001_claim_001",
    )
    draft = _draft("第一项事实。[C1]", [binding])
    draft.sections[0].content += "\n\n后加事实。[C1]"
    draft.full_text += "\n\n后加事实。[C1]"

    result = _evaluate(draft, canonical_citations=[_citation()])

    assert result.checks["citation_orphan_markers_absent"] is False
    assert result.checks["citation_marker_binding_bijection"] is False
    assert result.passed is False


def test_section_finalize_orphan_binding_is_rejected() -> None:
    binding = _binding(
        binding_id="binding-first",
        claim_text="第一项事实。",
        claim_id="section-p1-integrity_paragraph_001_claim_001",
    )
    draft = _draft("第一项事实。[C1]", [binding])
    draft.sections[0].content = "第一项事实。"
    draft.full_text = "1、资源配置\n第一项事实。"

    result = _evaluate(draft, canonical_citations=[_citation()])

    assert result.checks["citation_orphan_bindings_absent"] is False
    assert result.passed is False


def test_full_text_marker_mutation_after_assembly_is_rejected() -> None:
    binding = _binding(
        binding_id="binding-first",
        claim_text="第一项事实。",
        claim_id="section-p1-integrity_paragraph_001_claim_001",
    )
    draft = _draft("第一项事实。[C1]", [binding])
    draft.full_text += "\n后加孤立引用。[C1]"

    result = _evaluate(draft, canonical_citations=[_citation()])

    assert result.checks["citation_orphan_markers_absent"] is False
    assert (
        result.metadata["final_text_occurrences_match_sections"] is False
    )
    assert result.passed is False


def test_full_text_claim_mutation_with_same_marker_id_is_rejected() -> None:
    binding = _binding(
        binding_id="binding-first",
        claim_text="第一项事实。",
        claim_id="section-p1-integrity_paragraph_001_claim_001",
    )
    draft = _draft("第一项事实。[C1]", [binding])
    draft.full_text = "1、资源配置\n被篡改的不同事实。[C1]"

    result = _evaluate(draft, canonical_citations=[_citation()])

    assert result.checks["citation_orphan_markers_absent"] is False
    assert (
        result.metadata["final_text_occurrences_match_sections"] is False
    )
    assert result.passed is False


def test_unverified_occurrence_is_rejected_by_named_integrity_check() -> None:
    unverified = _binding(
        binding_id="binding-unverified",
        claim_text="第一项事实。",
        claim_id="section-p1-integrity_paragraph_001_claim_001",
        verified=False,
    )
    draft = _draft("第一项事实。[C1]", [unverified])

    result = _evaluate(draft, canonical_citations=[_citation()])

    assert result.checks["citation_marker_occurrences_verified"] is False
    assert result.passed is False


def test_mainline_final_artifact_blocks_free_text_supported_fact() -> None:
    project_input = _project_input(user_query="部署4台服务器")

    class _Agent(SchemeWriterAgent):
        def call_model(self, *args, **kwargs):  # type: ignore[override]
            del args, kwargs
            return ModelResponseSchema(
                model_call_id="mainline-p1-integrity",
                task_id=project_input.task_id,
                run_id="run-p1-integrity",
                model_name="stub",
                success=True,
                content="本项目部署4台服务器。",
                finish_reason="stop",
                created_at=NOW,
            )

    agent = _Agent(enable_semantic_gate=False)
    section = agent.section_generation_service.generate_section(
        SimpleNamespace(
            run_id="run-p1-integrity",
            task_id=project_input.task_id,
            generated_outputs={},
        ),
        document_id="document-p1-integrity",
        project_input=project_input,
        section_title="资源配置",
        section_order=1,
        rag_context=RAGContextSchema(context_text="", max_context_chars=6000),
        citations=[],
        structured_facts=[],
        previous_sections=[],
    )
    assembled = DocumentAssembler().assemble(
        DocumentAssemblyRequestSchema(
            task_id=project_input.task_id,
            run_id="run-p1-integrity",
            document_id="document-p1-integrity",
            document_title="资源配置报告",
            required_sections=["资源配置"],
            sections=[section],
            citations=[],
            citation_required_sections=[],
            created_at=NOW,
            updated_at=NOW,
        )
    )

    result = _evaluate(assembled.draft)

    assert section.extra["mandatory_project_fact_violations"]
    assert result.checks["high_risk_claims_grounded"] is False
    assert result.passed is False
