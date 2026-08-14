"""Focused contracts for Interface V2 Phase 2 RAG request closure."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

from apps.enterprise_document.schemas.project_input_schema import ProjectInputSchema
from apps.enterprise_document.services.scheme_writer.evidence_service import (
    SchemeEvidenceService,
)
from rag.context.context_gate import ContextRequirements
from rag.mapping.request_mapper import RAGRequestMapper
from rag.services.rag_service import FakeRAGService
from schemas.rag import (
    EvidenceRequirementsSchema,
    RAGDocumentContextSchema,
    RAGToolInputSchema,
    RetrievalAccessScopeSchema,
)


def _requirements() -> EvidenceRequirementsSchema:
    return EvidenceRequirementsSchema.from_topics(
        section_id="section-security",
        section_title="安全设计",
        section_type="security",
        topics=["访问控制", "日志审计"],
    )


def _document_context() -> RAGDocumentContextSchema:
    return RAGDocumentContextSchema(
        project_name="政务云扩容",
        document_title="政务云建设方案",
        required_sections=["安全设计", "安全设计"],
        citation_required_sections=["安全设计"],
    )


def _request(**updates: object) -> RAGToolInputSchema:
    data: dict[str, object] = {
        "task_id": "task-phase2",
        "run_id": "run-phase2",
        "agent_name": "ResearchAgent",
        "query": "政务云安全设计",
        "access_scope": RetrievalAccessScopeSchema(
            tenant_id="tenant-a",
            authorized_kb_ids=["kb-a"],
        ),
        "max_context_chars": 5000,
        "max_context_items": 4,
        "max_context_tokens": 2048,
        "retrieval_trace_id": "trace-phase2",
        "retrieval_scope": "section",
        "task_type": "scheme_generation",
        "evidence_requirements": _requirements(),
        "document_context": _document_context(),
    }
    data.update(updates)
    return RAGToolInputSchema(**data)


def test_typed_rag_request_json_round_trips_with_nested_contracts() -> None:
    request = _request()

    payload = request.model_dump(mode="json")
    reloaded = RAGToolInputSchema.model_validate(payload)

    assert reloaded == request
    assert reloaded.evidence_requirements == _requirements()
    assert reloaded.document_context == _document_context()


def test_extra_cannot_override_typed_request_authority() -> None:
    request = _request(
        extra={
            "retrieval_scope": "document",
            "retrieval_trace_id": "forged-trace",
            "task_type": "forged-task",
            "evidence_requirements": {"section_id": "forged"},
            "document_context": {"project_name": "forged"},
            "max_context_tokens": 999999,
        }
    )

    invocation = RAGRequestMapper().map(request)
    context = invocation.payload["extra_metadata"]

    assert context["retrieval_scope"] == "section"
    assert context["retrieval_trace_id"] == "trace-phase2"
    assert context["task_type"] == "scheme_generation"
    assert context["evidence_requirements"] == _requirements().model_dump(mode="json")
    assert context["document_context"] == _document_context().model_dump(mode="json")
    assert context["context_requirements"]["max_context_tokens"] == 2048
    assert context["request_metadata"] == request.extra


def test_request_mapper_projects_typed_fields_without_extra_metadata_authority() -> None:
    invocation = RAGRequestMapper().map(
        _request(extra={"diagnostic_label": "phase2"})
    )
    context = invocation.payload["extra_metadata"]

    assert "extra_metadata" not in context
    assert context["retrieval_scope"] == "section"
    assert context["task_type"] == "scheme_generation"
    assert context["context_requirements"] == {
        "max_context_chars": 5000,
        "max_context_items": 4,
        "max_context_tokens": 2048,
    }
    assert context["request_metadata"] == {"diagnostic_label": "phase2"}


def test_caller_budgets_only_narrow_rag_internal_limits() -> None:
    defaults = ContextRequirements(
        model_context_window=8192,
        prompt_reserved_tokens=1536,
        section_token_budget=4096,
        max_evidence_items=5,
        max_context_chars=12000,
    )

    expanded = ContextRequirements.from_mapping(
        {
            "max_context_tokens": 999999,
            "max_context_items": 99,
            "max_context_chars": 999999,
            "model_context_window": 999999,
            "prompt_reserved_tokens": 0,
        },
        defaults=defaults,
    )
    narrowed = ContextRequirements.from_mapping(
        {
            "max_context_tokens": 1024,
            "max_context_items": 2,
            "max_context_chars": 3000,
        },
        defaults=defaults,
    )

    assert expanded == defaults
    assert narrowed.model_context_window == defaults.model_context_window
    assert narrowed.prompt_reserved_tokens == defaults.prompt_reserved_tokens
    assert narrowed.section_token_budget == 1024
    assert narrowed.max_evidence_items == 2
    assert narrowed.max_context_chars == 3000


def test_scheme_evidence_service_writes_only_typed_canonical_fields() -> None:
    class _CaptureRAG:
        request: RAGToolInputSchema | None = None

        def retrieve(self, request: RAGToolInputSchema) -> None:
            self.request = request
            return None

    rag = _CaptureRAG()
    service = SchemeEvidenceService(
        rag_service=rag,
        agent_name="SchemeWriterAgent",
        retrieval_access_scope=RetrievalAccessScopeSchema(
            tenant_id="tenant-a",
            authorized_kb_ids=["kb-a"],
        ),
    )
    project_input = ProjectInputSchema.model_validate(
        {
            "task_id": "task-phase2",
            "tenant_id": "tenant-a",
            "task_type": "scheme_generation",
            "user_query": "生成政务云建设方案",
            "project_name": "政务云扩容",
            "project_type": "government_cloud",
            "generation_requirements": {
                "required_sections": ["安全设计"],
                "citation_required_sections": ["安全设计"],
                "max_context_chars": 5000,
                "extra": {
                    "max_evidence_context_tokens": 2048,
                    "max_evidence_items": 4,
                    "prompt_reserved_tokens": 1,
                },
            },
            "output_schema": {
                "document_title": "政务云建设方案",
                "required_sections": ["安全设计"],
            },
            "target_documents": ["现状报告"],
            "target_templates": ["方案模板"],
        }
    )

    service.retrieve(
        SimpleNamespace(
            task_id="task-phase2",
            run_id="run-phase2",
            task={"kb_ids": ["kb-a"]},
        ),
        project_input,
        scope="section",
        section_id="section-security",
        section_title="安全设计",
    )

    assert rag.request is not None
    request = rag.request
    assert request.retrieval_scope == "section"
    assert request.task_type == "scheme_generation"
    assert request.max_context_tokens == 2048
    assert request.max_context_items == 4
    assert request.evidence_requirements is not None
    assert request.document_context is not None
    assert request.extra == {
        "project_type": "government_cloud",
        "target_documents": ["现状报告"],
        "target_templates": ["方案模板"],
    }
    forbidden = {
        "retrieval_trace_id",
        "retrieval_scope",
        "task_type",
        "evidence_requirements",
        "document_context",
        "max_context_tokens",
        "max_context_chars",
        "max_context_items",
        "model_context_window",
        "prompt_reserved_tokens",
        "policy_id",
        "coverage_strategy",
        "claim_risk_level",
        "allow_framework_only",
        "extra_metadata",
    }
    assert forbidden.isdisjoint(request.extra)


def test_fake_rag_uses_typed_trace_and_scope_not_extra_conflicts() -> None:
    request = _request(
        extra={
            "retrieval_trace_id": "forged-trace",
            "retrieval_scope": "document",
        }
    )

    bundle = FakeRAGService().retrieve(request)

    assert bundle.retrieval_trace_id == "trace-phase2"
    assert bundle.extra["provider_output"]["extra"]["retrieval_scope"] == "section"


def test_production_request_path_has_no_legacy_canonical_extra_reads() -> None:
    backend_root = Path(__file__).resolve().parents[1]
    consumers = [
        backend_root / "rag/mapping/request_mapper.py",
        backend_root / "rag/mapping/result_mapper.py",
        backend_root / "rag/services/rag_service.py",
        backend_root / "rag/integrations/langchain/runtime_facade.py",
    ]
    forbidden_reads = (
        'request.extra.get("retrieval_scope")',
        'request.extra.get("retrieval_trace_id")',
        'request.extra.get("extra_metadata")',
    )
    for path in consumers:
        source = path.read_text(encoding="utf-8")
        assert all(item not in source for item in forbidden_reads), path

    producer_source = (
        backend_root
        / "apps/enterprise_document/services/scheme_writer/evidence_service.py"
    ).read_text(encoding="utf-8")
    assert '"extra_metadata"' not in producer_source
    assert '"model_context_window"' not in producer_source
    assert '"prompt_reserved_tokens"' not in producer_source
