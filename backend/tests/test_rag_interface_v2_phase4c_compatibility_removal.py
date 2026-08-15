from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from agent.runtime.graph_state import GraphStateSchema
from agent.runtime.state_access import SharedStateWriter
from apps.enterprise_document.schemas.project_input_schema import ProjectInputSchema
from apps.enterprise_document.schemas.scheme_generation_graph import (
    SCHEME_GENERATION_GRAPH_STATE_VERSION,
    SchemeGenerationGraphPayloadSchema,
    SchemeGenerationGraphStateCodec,
)
from apps.enterprise_document.schemas.scheme_writer.document import SchemeSectionSchema
from apps.enterprise_document.schemas.scheme_writer.document_assembly import (
    DocumentAssemblyRequestSchema,
)
from apps.enterprise_document.schemas.scheme_writer.evidence import SectionEvidenceBundleSchema
from apps.enterprise_document.schemas.scheme_writer.output import SchemeWriterOutputSchema
from apps.enterprise_document.schemas.scheme_writer.section_execution import (
    SectionExecutionResultSchema,
)
from apps.enterprise_document.schemas.scheme_writer.state_projection import ResearchStateSchema
from apps.enterprise_document.schemas.section_execution_graph import (
    SECTION_EXECUTION_GRAPH_STATE_VERSION,
    SectionExecutionGraphPayloadSchema,
    SectionExecutionGraphStateCodec,
)
from apps.enterprise_document.services.scheme_writer.document_assembler import DocumentAssembler
from apps.enterprise_document.services.scheme_writer.document_citation_registry import (
    DocumentCitationRegistry,
)
from apps.enterprise_document.services.scheme_writer.evidence_service import SchemeEvidenceService
from apps.enterprise_document.services.scheme_writer.legacy_evidence_adapter import (
    LegacyEvidenceAdapter,
)
from apps.enterprise_document.services.scheme_writer.stage_service import SchemeGenerationStageService
from apps.enterprise_document.workflows.section_execution_graph import SectionExecutionGraph
from apps.enterprise_document.services.scheme_writer.writer_governance import (
    extract_writer_gate_binding,
)
from context_manager.policies import SectionGenerationContextPolicy
from rag.evidence.contract import (
    RAGEvidenceContractBuilder,
    RAGEvidenceContractReader,
    evidence_bundle_sha256,
)
from schemas.citation import CitationSchema
from schemas.context import ContextBundleSchema, TaskContextSchema, UserContextSchema
from schemas.rag import (
    EvidenceStatus,
    RAGEvidenceAssessmentSchema,
    RAGContextSchema,
    RetrievedChunkSchema,
)
from schemas.status import ExecutionStatus
from schemas.tool import ToolResultSchema

from backend.tests.test_rag_interface_v2_phase4b_canonical_carrier import (
    _distinct_assessed_bundle,
    _run_gateway_graph_recovery,
    _run_graph_recovery,
    _writer_gate,
)


BACKEND_ROOT = Path(__file__).resolve().parents[1]
PRODUCTION_ROOTS = (
    BACKEND_ROOT / "apps",
    BACKEND_ROOT / "agent",
    BACKEND_ROOT / "context_manager",
    BACKEND_ROOT / "observability",
    BACKEND_ROOT / "schemas",
)


def _python_sources() -> dict[Path, str]:
    result: dict[Path, str] = {}
    for root in PRODUCTION_ROOTS:
        for path in root.rglob("*.py"):
            result[path] = path.read_text(encoding="utf-8")
    return result


def _chunk(marker: str = "accepted") -> RetrievedChunkSchema:
    return RetrievedChunkSchema(
        rank=1,
        matched_chunk_id=f"child_{marker}",
        context_chunk_id=f"parent_{marker}",
        child_chunk_id=f"child_{marker}",
        parent_chunk_id=f"parent_{marker}",
        doc_id=f"doc_{marker}",
        match_text=f"match {marker}",
        context_text=f"context {marker}",
        title=f"Evidence {marker}",
    )


def _citation(marker: str = "accepted", citation_id: str = "source-local") -> CitationSchema:
    return CitationSchema(
        citation_id=citation_id,
        source_type="document",
        source_document_id=f"doc_{marker}",
        doc_id=f"doc_{marker}",
        child_chunk_id=f"child_{marker}",
        parent_chunk_id=f"parent_{marker}",
        quote_text=f"context {marker}",
        title=f"Evidence {marker}",
    )


def _bundle(
    marker: str = "accepted",
    *,
    status: EvidenceStatus = EvidenceStatus.SUFFICIENT,
    scope: str = "section",
    quality_mode_degraded: bool = False,
):
    bundle = RAGEvidenceContractBuilder.build(
        query=f"query {marker}",
        rewritten_queries=[],
        selected_chunks=[_chunk(marker)],
        dropped_chunks=[],
        citations=[_citation(marker)],
        trace=None,
        max_context_chars=6000,
    )
    assessment = RAGEvidenceAssessmentSchema(
        assessment_state="completed",
        status=status,
        evidence_available=True,
        selected_evidence_count=1,
        citation_count=1,
        coverage_ratio=1.0 if status is EvidenceStatus.SUFFICIENT else 0.0,
        required_topics=[marker],
        covered_topics=[marker] if status is EvidenceStatus.SUFFICIENT else [],
        missing_topics=[marker] if status is EvidenceStatus.INSUFFICIENT else [],
        authoritative_evidence_present=status is EvidenceStatus.SUFFICIENT,
        prompt_evidence_source="phase4c_test",
        details={"quality_mode_degraded": quality_mode_degraded},
    )
    return bundle.model_copy(
        update={
            "task_id": "task_phase4c",
            "run_id": "run_phase4c",
            "retrieval_trace_id": f"trace_{marker}",
            "retrieval_scope": scope,
            "assessment": assessment,
        },
        deep=True,
    )


def _shared_state() -> GraphStateSchema:
    return GraphStateSchema(
        task_id="task_phase4c",
        run_id="run_phase4c",
        task_type="scheme_generation",
        user_input="生成建设方案",
        context_bundle=ContextBundleSchema(
            user=UserContextSchema(user_query="生成建设方案"),
            task=TaskContextSchema(
                task_id="task_phase4c",
                run_id="run_phase4c",
                task_type="scheme_generation",
            ),
        ),
        created_at="2026-08-14T22:00:00+08:00",
    )


def _project_input() -> ProjectInputSchema:
    return ProjectInputSchema(
        task_id="task_phase4c",
        task_type="scheme_generation",
        user_query="生成建设方案",
        generation_requirements={
            "required_sections": ["技术方案"],
            "need_citation": True,
            "citation_required_sections": ["技术方案"],
        },
        output_schema={
            "document_title": "建设方案",
            "required_sections": ["技术方案"],
        },
    )


def _section_payload(bundle=None) -> SectionExecutionGraphPayloadSchema:
    from apps.enterprise_document.schemas.scheme_writer.planning import SectionPlanSchema

    bundle = bundle or _bundle("checkpoint", scope="document")
    plan = SectionPlanSchema(
        section_id="section_phase4c_001",
        section_title="技术方案",
        section_order=1,
        citation_required=True,
    )
    return SectionExecutionGraphPayloadSchema(
        task_id="task_phase4c",
        run_id="run_phase4c",
        document_id="document_phase4c",
        project_input=_project_input(),
        section_plan=plan,
        shared_state_payload=SectionExecutionGraphStateCodec.dump_graph_state(
            _shared_state()
        ),
        document_evidence_bundle=bundle,
        section_id=plan.section_id,
        section_title=plan.section_title,
        section_order=1,
        total_sections=1,
        citation_required=True,
        citation_registry=list(bundle.citations),
        active_evidence_bundle=bundle,
        active_context=bundle.context,
        active_chunks=list(RAGEvidenceContractReader.projections(bundle)[1]),
        active_citations=list(bundle.citations),
        active_query=bundle.query,
        active_scope=bundle.retrieval_scope,
    )


def test_production_projection_to_bundle_reconstruction_is_zero_except_legacy_ingress() -> None:
    sources = _python_sources()
    illegal_hits: list[str] = []
    legal_hit = False
    for path, source in sources.items():
        rel = path.relative_to(BACKEND_ROOT).as_posix()
        if "RAGEvidenceContractSchema.model_validate" in source:
            if rel.endswith(
                "apps/enterprise_document/services/scheme_writer/legacy_evidence_adapter.py"
            ):
                legal_hit = True
            else:
                illegal_hits.append(rel)
        if "EvidenceBundleSchema.model_validate" in source:
            illegal_hits.append(rel)
    assert legal_hit is True
    assert illegal_hits == []


def test_context_manager_never_recovers_bundle_from_rag_context_extra() -> None:
    bundle = _bundle("context")
    context = bundle.context.model_copy(deep=True)
    context.extra = {
        "evidence_contract": bundle.model_dump(mode="json", round_trip=True),
        "evidence_contract_sha256": evidence_bundle_sha256(bundle),
    }
    request = SectionGenerationContextPolicy().build_request(
        task_id="task_phase4c",
        run_id="run_phase4c",
        section_id="section_phase4c_001",
        section_title="技术方案",
        section_order=1,
        project_input=_project_input(),
        section_contract="仅使用提供证据",
        target_section_chars=800,
        rag_context=context,
        citations=list(bundle.citations),
        previous_sections=[],
        evidence_bundle=None,
    )
    assert request.lineage == {}
    assert "_evidence_contract(" not in (
        BACKEND_ROOT / "context_manager/policies.py"
    ).read_text(encoding="utf-8")


def test_writer_governance_has_no_normalized_dict_to_bundle_fallback() -> None:
    bundle = _bundle("gate")
    normalized = {"evidence": bundle.model_dump(mode="json", round_trip=True), "extra": {}}
    assert extract_writer_gate_binding(
        normalized,
        section_id="section_001",
        section_title="技术方案",
    ) is None
    binding = extract_writer_gate_binding(
        normalized,
        evidence_bundle=bundle,
        section_id="section_001",
        section_title="技术方案",
    )
    assert binding is not None
    assert binding.generation_allowed is True


def test_citation_registry_is_projection_only_and_canonical_hash_is_immutable() -> None:
    bundle = _bundle("hash")
    canonical_hash = evidence_bundle_sha256(bundle)
    context, chunks, citations = RAGEvidenceContractReader.projections(bundle)
    registry = DocumentCitationRegistry.from_citations(
        [_citation("preexisting", citation_id="C1")]
    )
    remapped_context, _, remapped_citations, normalized = registry.remap_bundle(
        context=context,
        chunks=chunks,
        citations=citations,
        normalized={"evidence": bundle.model_dump(mode="json", round_trip=True)},
        scope="section",
        query=bundle.query,
    )
    assert remapped_citations[0].citation_id == "C2"
    assert evidence_bundle_sha256(bundle) == canonical_hash
    assert bundle.citations[0].citation_id == "C1"
    assert "evidence" not in normalized
    assert "evidence_contract" not in remapped_context.extra
    source = (
        BACKEND_ROOT
        / "apps/enterprise_document/services/scheme_writer/document_citation_registry.py"
    ).read_text(encoding="utf-8")
    assert "model_validate(" not in source


def test_typed_normalized_output_and_rag_context_do_not_carry_full_bundle() -> None:
    bundle = _bundle("normalized")
    service = SchemeEvidenceService(rag_service=None, agent_name="phase4c-test")
    context, chunks, citations, normalized = service.extract_rag_output(
        _shared_state(), bundle
    )
    assert chunks and citations
    assert "evidence" not in normalized
    assert "evidence_contract" not in normalized
    assert "evidence_contract" not in context.extra
    assert normalized["evidence_contract_sha256"] == evidence_bundle_sha256(bundle)
    assert normalized["retrieval_trace_id"] == bundle.retrieval_trace_id


def test_context_bundle_evidence_and_all_declarative_wiring_are_retired() -> None:
    assert "evidence" not in ContextBundleSchema.model_fields
    assert not hasattr(SharedStateWriter, "set_evidence_context")
    forbidden = (
        "context_bundle.evidence",
        "set_evidence_context",
        '"evidence_context"',
    )
    for relative in (
        "agent/runtime/graph_state_ops.py",
        "agent/runtime/workflow_observer.py",
        "apps/enterprise_document/workflows/scheme_generation.py",
        "apps/enterprise_document/workflows/top_level_business_graph.py",
    ):
        source = (BACKEND_ROOT / relative).read_text(encoding="utf-8")
        for token in forbidden:
            assert token not in source, (relative, token)


def test_application_duplicate_evidence_state_is_retired() -> None:
    retired = {
        "document_rag_context",
        "document_retrieved_chunks",
        "document_citations",
        "document_rag_output",
        "document_evidence_assessment",
        "document_evidence_available",
        "document_assessment_status",
        "all_retrieved_chunks",
    }
    assert not retired & set(SchemeGenerationGraphPayloadSchema.model_fields)
    assert "active_assessment" not in SectionExecutionGraphPayloadSchema.model_fields
    assert "assessment" not in ResearchStateSchema.model_fields
    assert "document_evidence_assessment" not in (
        __import__(
            "apps.enterprise_document.schemas.scheme_writer.section_execution",
            fromlist=["SectionExecutionRequestSchema"],
        ).SectionExecutionRequestSchema.model_fields
    )


def test_section_evidence_schema_has_only_frozen_persisted_fields_and_derives_views() -> None:
    bundle = _bundle("section-final", scope="recovery")
    item = SectionEvidenceBundleSchema(
        section_id="section_001",
        section_title="技术方案",
        evidence_bundle=bundle,
        tool_call_ids=[bundle.retrieval_trace_id],
        recovery_count=1,
        evidence_contract_sha256=evidence_bundle_sha256(bundle),
        writer_gate_binding={"generation_allowed": True},
        metadata={"diagnostic": True},
    )
    assert set(SectionEvidenceBundleSchema.model_fields) == {
        "schema_version",
        "section_id",
        "section_title",
        "evidence_bundle",
        "tool_call_ids",
        "recovery_count",
        "evidence_contract_sha256",
        "writer_gate_binding",
        "metadata",
    }
    dumped = item.model_dump(mode="json", round_trip=True)
    for retired in (
        "retrieval_scope",
        "query",
        "rag_context",
        "retrieved_chunks",
        "citations",
        "generation_allowed",
    ):
        assert retired not in dumped
    assert item.retrieval_scope == "recovery"
    assert item.query == bundle.query
    assert item.rag_context.context_text == bundle.context.context_text
    assert item.retrieved_chunks
    assert item.citations
    assert item.generation_allowed is True


def test_document_context_chunks_and_citations_are_derived_from_canonical_bundle() -> None:
    bundle = _bundle("document", scope="document")
    context, chunks, citations = RAGEvidenceContractReader.projections(bundle)
    assert context.context_text == bundle.context.context_text
    assert chunks[0].child_chunk_id == "child_document"
    assert citations[0].citation_ref == bundle.citations[0].citation_ref
    assert "all_retrieved_chunks" not in SchemeGenerationGraphPayloadSchema.model_fields


def test_document_assembler_preserves_quality_mode_degraded_from_canonical_assessment() -> None:
    document_bundle = _bundle(
        "quality",
        scope="document",
        quality_mode_degraded=True,
    )
    section_bundle = _bundle("section-quality", scope="section")
    section = SchemeSectionSchema(
        section_id="section_001",
        section_title="技术方案",
        section_order=1,
        content="技术内容",
        status=ExecutionStatus.SUCCESS,
    )
    result = DocumentAssembler().assemble(
        DocumentAssemblyRequestSchema(
            task_id="task_phase4c",
            run_id="run_phase4c",
            document_id="document_phase4c",
            document_title="建设方案",
            required_sections=["技术方案"],
            sections=[section],
            retrieved_chunks=[*RAGEvidenceContractReader.projections(document_bundle)[1]],
            citations=list(document_bundle.citations),
            section_evidence=[
                SectionEvidenceBundleSchema(
                    section_id="section_001",
                    section_title="技术方案",
                    evidence_bundle=section_bundle,
                )
            ],
            document_evidence_bundle=document_bundle,
            citation_required_sections=["技术方案"],
            created_at="2026-08-14T22:00:00+08:00",
            updated_at="2026-08-14T22:01:00+08:00",
        )
    )
    assert result.draft.extra["quality_mode_degraded"] is True


def test_direct_rejected_candidate_citation_does_not_commit_or_consume_label() -> None:
    active = _distinct_assessed_bundle(
        status=EvidenceStatus.SUFFICIENT,
        query="active",
        scope="section",
        marker="PHASE4C_DIRECT_ACTIVE",
    )
    candidate = _distinct_assessed_bundle(
        status=EvidenceStatus.INSUFFICIENT,
        query="candidate",
        scope="recovery",
        marker="PHASE4C_DIRECT_REJECTED",
    )
    restored = _run_graph_recovery(
        active_bundle=active,
        active_gate=_writer_gate(permitted=True, bundle=active),
        candidate_bundle=candidate,
        candidate_gate=_writer_gate(permitted=False, bundle=candidate),
        corrective=True,
    )
    registry_refs = {item.citation_ref for item in restored.citation_registry}
    assert candidate.citations[0].citation_ref not in registry_refs
    registry = DocumentCitationRegistry.from_citations(restored.citation_registry)
    accepted, _ = registry.register(
        [_citation("next-accepted", citation_id="local-next")],
        scope="section",
        query="next accepted",
    )
    assert accepted[0].citation_id == "C2"


def test_research_gateway_rejected_candidate_citation_does_not_commit() -> None:
    active = _distinct_assessed_bundle(
        status=EvidenceStatus.SUFFICIENT,
        query="active gateway",
        scope="section",
        marker="PHASE4C_GATEWAY_ACTIVE",
    )
    candidate = _distinct_assessed_bundle(
        status=EvidenceStatus.INSUFFICIENT,
        query="candidate gateway",
        scope="self_rag_recovery",
        marker="PHASE4C_GATEWAY_REJECTED",
    )
    restored = _run_gateway_graph_recovery(
        active_bundle=active,
        active_gate=_writer_gate(permitted=True, bundle=active),
        candidate_bundle=candidate,
        candidate_gate=_writer_gate(permitted=False, bundle=candidate),
        corrective=False,
    )
    registry_refs = {item.citation_ref for item in restored.citation_registry}
    assert candidate.citations[0].citation_ref not in registry_refs


def test_parallel_fan_in_commits_only_final_accepted_bundle_citations() -> None:
    accepted_bundle = _bundle("parallel-accepted", scope="section")
    accepted_citation = accepted_bundle.citations[0]
    diagnostic_citation = _citation("parallel-rejected", citation_id="C99")
    section = SchemeSectionSchema(
        section_id="section_parallel_001",
        section_title="技术方案",
        section_order=1,
        content="正文 [C1]",
        model_output="正文 [C1]",
        prompt="引用 [C1]",
        status=ExecutionStatus.SUCCESS,
    )
    result = SectionExecutionResultSchema(
        section=section,
        evidence=SectionEvidenceBundleSchema(
            section_id=section.section_id,
            section_title=section.section_title,
            evidence_bundle=accepted_bundle,
            evidence_contract_sha256=evidence_bundle_sha256(accepted_bundle),
        ),
        citation_registry=[accepted_citation, diagnostic_citation],
    )
    registry = DocumentCitationRegistry.from_citations(
        [_citation("preexisting", citation_id="C1")]
    )
    stage = object.__new__(SchemeGenerationStageService)
    merged = stage._merge_parallel_section_result(
        result,
        document_id="document_phase4c",
        citation_registry=registry,
    )
    refs = {item.citation_ref for item in merged.citation_registry}
    assert accepted_citation.citation_ref in refs
    assert diagnostic_citation.citation_ref not in refs
    assert len(merged.citation_registry) == 2
    assert merged.evidence.evidence_bundle == accepted_bundle
    dumped = merged.evidence.model_dump(mode="json", round_trip=True)
    assert "rag_context" not in dumped
    assert "retrieved_chunks" not in dumped
    assert "citations" not in dumped


def test_scheme_writer_output_and_terminal_projection_cannot_carry_section_full_bundles() -> None:
    assert SchemeWriterOutputSchema.model_fields["section_evidence"].annotation != list[
        SectionEvidenceBundleSchema
    ]
    source = (
        BACKEND_ROOT / "apps/enterprise_document/services/scheme_writer/stage_service.py"
    ).read_text(encoding="utf-8")
    assert '"section_evidence": [\n                    item.model_dump()' not in source
    assert '"section_evidence": section_evidence_summaries' in source
    assert '"document": document_rag_summary' in source


def test_capture_observer_and_rag_tool_output_do_not_depend_on_full_bundle() -> None:
    capture_source = (
        BACKEND_ROOT / "apps/enterprise_document/services/scheme_writer/capture_service.py"
    ).read_text(encoding="utf-8")
    observer_source = (BACKEND_ROOT / "agent/runtime/workflow_observer.py").read_text(
        encoding="utf-8"
    )
    stage_source = (
        BACKEND_ROOT / "apps/enterprise_document/services/scheme_writer/stage_service.py"
    ).read_text(encoding="utf-8")
    assert "evidence_bundle" not in capture_source
    assert "context_bundle.evidence" not in observer_source
    assert '"evidence": document_bundle.model_dump' not in stage_source
    assert 'state.contexts["rag_tool_output"] = rag_tool_output' in (
        BACKEND_ROOT / "agent/runtime/state_access.py"
    ).read_text(encoding="utf-8")


def test_top_level_v1_checkpoint_rejected_and_v2_round_trip_supported() -> None:
    bundle = _bundle("top-checkpoint", scope="document")
    payload = SchemeGenerationGraphPayloadSchema(
        task_id="task_phase4c",
        run_id="run_phase4c",
        document_evidence_bundle=bundle,
    )
    codec = SchemeGenerationGraphStateCodec()
    state = {
        "task_id": payload.task_id,
        "run_id": payload.run_id,
        **codec.build_initial_update(payload),
    }
    assert SCHEME_GENERATION_GRAPH_STATE_VERSION == 2
    with pytest.raises(ValueError, match="unsupported scheme generation graph state version"):
        codec.restore_payload({**state, "scheme_generation_state_version": 1})
    restored = codec.restore_payload(json.loads(json.dumps(state)))
    assert restored.document_evidence_bundle == bundle


def test_section_v1_checkpoint_rejected_and_v2_round_trip_supported() -> None:
    payload = _section_payload()
    codec = SectionExecutionGraphStateCodec()
    state = codec.build_initial_state(payload)
    assert SECTION_EXECUTION_GRAPH_STATE_VERSION == 2
    with pytest.raises(ValueError, match="unsupported section graph state version"):
        codec.restore_payload({**state, "section_graph_state_version": 1})
    with pytest.raises(ValueError, match="unsupported section graph state version"):
        codec.restore_payload({key: value for key, value in state.items() if key != "section_graph_state_version"})
    restored = codec.restore_payload(json.loads(json.dumps(state)))
    assert restored.document_evidence_bundle == payload.document_evidence_bundle
    assert restored.active_evidence_bundle == payload.active_evidence_bundle


def test_checkpoint_restore_paths_do_not_default_missing_version_to_v1() -> None:
    for relative in (
        "apps/enterprise_document/workflows/section_execution_graph.py",
        "apps/enterprise_document/workflows/top_level_business_graph.py",
    ):
        source = (BACKEND_ROOT / relative).read_text(encoding="utf-8")
        assert 'state_version",\n                        1' not in source
        assert 'state_version", 1' not in source


def _recoverable_section_result(payload: SectionExecutionGraphPayloadSchema) -> SectionExecutionResultSchema:
    bundle = payload.active_evidence_bundle or payload.document_evidence_bundle
    assert bundle is not None
    section = SchemeSectionSchema(
        section_id=str(payload.section_id),
        section_title=str(payload.section_title),
        section_order=int(payload.section_order or 1),
        content="recovered section",
        status=ExecutionStatus.SUCCESS,
    )
    return SectionExecutionResultSchema(
        section=section,
        evidence=SectionEvidenceBundleSchema(
            section_id=section.section_id,
            section_title=section.section_title,
            evidence_bundle=bundle,
            evidence_contract_sha256=evidence_bundle_sha256(bundle),
        ),
        citation_registry=list(bundle.citations),
    )


def _section_graph_for_recovery(
    persisted: SectionExecutionGraphPayloadSchema,
) -> SectionExecutionGraph:
    graph = object.__new__(SectionExecutionGraph)
    graph.codec = SectionExecutionGraphStateCodec()
    graph.graph_state_codec = MagicMock()
    checkpoint = {
        "channel_values": {
            "section_graph_state_version": SECTION_EXECUTION_GRAPH_STATE_VERSION,
            "section_payload": persisted.model_dump(mode="json", round_trip=True),
        }
    }
    graph.checkpoint_history = MagicMock(
        return_value=[SimpleNamespace(checkpoint=checkpoint)]
    )
    return graph


def test_completed_section_recover_or_reuse_returns_checkpoint_result_without_resume() -> None:
    base = _section_payload()
    result = _recoverable_section_result(base)
    persisted = base.model_copy(
        update={"completed": True, "route": "end", "result": result},
        deep=True,
    )
    graph = _section_graph_for_recovery(persisted)
    graph.resume = MagicMock(side_effect=AssertionError("completed section must not resume"))
    shared_state = _shared_state()
    registry = DocumentCitationRegistry()
    reused = graph.recover_or_reuse(
        workflow_namespace="scheme_generation_v2:v2.0",
        task_id=persisted.task_id,
        run_id=persisted.run_id,
        document_id=persisted.document_id,
        section_id=str(persisted.section_id),
        project_input=persisted.project_input,
        section_plan=persisted.section_plan,
        shared_state=shared_state,
        citation_registry=registry,
    )
    assert reused.section == result.section
    assert reused.evidence == result.evidence
    assert reused.citation_registry == result.citation_registry
    assert reused.section_state is not None
    graph.resume.assert_not_called()
    assert [item.model_dump(mode="json") for item in registry.all()] == [
        item.model_dump(mode="json") for item in persisted.citation_registry
    ]


def test_incomplete_section_recover_or_reuse_resumes_exact_v2_cursor() -> None:
    persisted = _section_payload().model_copy(
        update={"completed": False, "route": "generate_section", "result": None},
        deep=True,
    )
    expected = _recoverable_section_result(persisted)
    graph = _section_graph_for_recovery(persisted)
    graph.resume = MagicMock(return_value=expected)
    shared_state = _shared_state()
    registry = DocumentCitationRegistry()
    resumed = graph.recover_or_reuse(
        workflow_namespace="scheme_generation_v2:v2.0",
        task_id=persisted.task_id,
        run_id=persisted.run_id,
        document_id=persisted.document_id,
        section_id=str(persisted.section_id),
        project_input=persisted.project_input,
        section_plan=persisted.section_plan,
        shared_state=shared_state,
        citation_registry=registry,
    )
    assert resumed == expected
    graph.resume.assert_called_once_with(
        workflow_namespace="scheme_generation_v2:v2.0",
        run_id=persisted.run_id,
        section_id=str(persisted.section_id),
        shared_state=shared_state,
        citation_registry=registry,
    )


def test_legacy_evidence_adapter_remains_single_legal_upgrade_boundary_without_reexport() -> None:
    legacy_chunk = _chunk("legacy")
    legacy_citation = _citation("legacy", citation_id="legacy-source")
    result = ToolResultSchema(
        tool_call_id="tool_phase4c_legacy",
        task_id="task_phase4c",
        run_id="run_phase4c",
        tool_name="legacy_rag",
        success=True,
        result={
            "query": "legacy query",
            "retrieved_chunks": [legacy_chunk.model_dump(mode="json")],
            "citations": [legacy_citation.model_dump(mode="json")],
        },
        created_at="2026-08-14T22:00:00+08:00",
    )
    upgraded = LegacyEvidenceAdapter.upgrade(_shared_state(), result)
    assert upgraded.query == "legacy query"
    assert upgraded.citations
    context, chunks, citations, normalized = LegacyEvidenceAdapter.extract(
        _shared_state(), result
    )
    assert context.context_text
    assert chunks and citations
    assert "evidence" not in normalized
    assert "evidence_contract" not in normalized
    assert "evidence_contract" not in context.extra


def test_scheme_evidence_service_upgrades_legacy_result_before_returning_to_application() -> None:
    legacy_chunk = _chunk("service-legacy")
    legacy_citation = _citation("service-legacy", citation_id="legacy-local")
    legacy_result = ToolResultSchema(
        tool_call_id="tool_service_legacy",
        task_id="task_phase4c",
        run_id="run_phase4c",
        tool_name="legacy_rag",
        success=True,
        result={
            "query": "service legacy query",
            "retrieved_chunks": [legacy_chunk.model_dump(mode="json")],
            "citations": [legacy_citation.model_dump(mode="json")],
        },
        created_at="2026-08-14T22:00:00+08:00",
    )
    rag_service = MagicMock()
    rag_service.retrieve.return_value = legacy_result
    service = SchemeEvidenceService(rag_service=rag_service, agent_name="phase4c-test")
    upgraded = service.retrieve(
        _shared_state(),
        _project_input(),
        query="service legacy query",
        scope="document",
    )
    assert upgraded is not None
    assert upgraded.__class__.__name__ == "EvidenceBundleSchema"
    assert upgraded.query == "service legacy query"
    assert upgraded.citations


def test_phase4b_recovery_acceptance_regression_still_holds() -> None:
    blocked = _distinct_assessed_bundle(
        status=EvidenceStatus.INSUFFICIENT,
        query="blocked",
        scope="section",
        marker="PHASE4C_BLOCKED",
    )
    permitted = _distinct_assessed_bundle(
        status=EvidenceStatus.SUFFICIENT,
        query="permitted",
        scope="recovery",
        marker="PHASE4C_PERMITTED",
    )
    accepted = _run_graph_recovery(
        active_bundle=blocked,
        active_gate=_writer_gate(permitted=False, bundle=blocked),
        candidate_bundle=permitted,
        candidate_gate=_writer_gate(permitted=True, bundle=permitted),
        corrective=True,
    )
    assert accepted.active_evidence_bundle == permitted

    current = _distinct_assessed_bundle(
        status=EvidenceStatus.SUFFICIENT,
        query="current permitted",
        scope="section",
        marker="PHASE4C_CURRENT",
    )
    newer = _distinct_assessed_bundle(
        status=EvidenceStatus.SUFFICIENT,
        query="new permitted",
        scope="self_rag_recovery",
        marker="PHASE4C_NEW",
    )
    promoted = _run_graph_recovery(
        active_bundle=current,
        active_gate=_writer_gate(permitted=True, bundle=current),
        candidate_bundle=newer,
        candidate_gate=_writer_gate(permitted=True, bundle=newer),
        corrective=False,
    )
    assert promoted.active_evidence_bundle == newer
