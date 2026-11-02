from __future__ import annotations

from contextlib import ExitStack
from dataclasses import replace
import json
import os
from pathlib import Path
from unittest.mock import patch

from application.mainline_service import MainlineApplicationService
from agent.runtime.official_checkpoint_saver import create_sqlite_checkpoint_resource
from apps.enterprise_document.schemas.project_input_schema import ProjectInputSchema
from apps.enterprise_document.schemas.scheme_generation_graph import (
    SchemeGenerationGraphPayloadSchema,
    SchemeGenerationGraphStateCodec,
)
from apps.enterprise_document.schemas.scheme_writer.document import SchemeSectionSchema
from apps.enterprise_document.services.scheme_writer.document_citation_registry import (
    DocumentCitationRegistry,
)
from apps.enterprise_document.services.scheme_writer.evidence_service import SchemeEvidenceService
from apps.enterprise_document.services.scheme_writer.legacy_evidence_adapter import (
    LegacyEvidenceAdapter,
)
from apps.enterprise_document.services.scheme_writer.stage_service import (
    SchemeGenerationStageService,
)
from apps.enterprise_document.workflows.top_level_business_graph import (
    EnterpriseDocumentWorkflowEngine,
    phase2_checkpoint_identity,
)
from bootstrap.supervisor_factory import SupervisorFactory
from context_manager.manager import LLMContextManager
from context_manager.policies import SectionGenerationContextPolicy
from core.config import get_settings
from contracts.evidence import build_evidence_bundle, evidence_bundle_sha256
from run_demo import _section_report_rows, persist_end_to_end_artifacts
from schemas.citation import CitationSchema
from schemas.context import ContextBundleSchema, TaskContextSchema, UserContextSchema
from schemas.rag import (
    EvidenceDisposition,
    RAGContextSchema,
    RAGEvidenceAssessmentSchema,
    RAGEvidenceContractSchema,
    RAGEvidenceItemSchema,
    RAGEvidenceLineageSchema,
    RetrievedChunkSchema,
)
from schemas.status import ExecutionStatus
from schemas.tool import ToolResultSchema
from system_test.mainline_closure import run_fake_mainline_scenario


PROJECT_ROOT = Path(__file__).resolve().parents[2]
REVIEW_PREFIX = [
    "normalize_project_input",
    "plan_document",
    "retrieve_document_evidence",
    "initialize_generation",
    "execute_sections",
    "document_verify",
    "repair_document_conflicts",
    "incremental_document_reverify",
    "assemble_document",
    "validate_document_assembly",
    "final_artifact_review",
    "decide_document_version",
    "evaluate_document_gate",
    "build_generation_report",
]


class _RecordingFactory(SupervisorFactory):
    """Record concrete runtime objects so cross-instance freshness is assertable."""

    def __init__(self) -> None:
        super().__init__()
        self.records: list[dict[str, object]] = []
        self.workflows: list[object] = []

    def build(self, **kwargs):
        supervisor = super().build(**kwargs)
        engine = supervisor.workflow_engine
        assert isinstance(engine, EnterpriseDocumentWorkflowEngine)
        self.workflows.append(kwargs["workflow"])
        self.records.append(
            {
                "supervisor": supervisor,
                "engine": engine,
                "stage_service": engine.stage_service,
                "rag_facade": engine.stage_service.evidence_service.rag_service,
                "model_facade": supervisor.model_gateway,
                "checkpoint_path": Path(engine.checkpointer.path),
            }
        )
        return supervisor


_ORIGINAL_PREPARE = SchemeGenerationStageService.prepare_document_plan


def _require_human_review(self, graph_state, payload):
    result = _ORIGINAL_PREPARE(self, graph_state, payload)
    project_input = result.project_input
    assert project_input is not None
    requirements = project_input.generation_requirements.model_copy(
        update={
            "extra": {
                **dict(project_input.generation_requirements.extra),
                "require_human_approval": True,
            }
        },
        deep=True,
    )
    return result.model_copy(
        update={
            "project_input": project_input.model_copy(
                update={"generation_requirements": requirements},
                deep=True,
            )
        },
        deep=True,
    )


def _fake_settings(output_root: Path):
    return replace(
        get_settings(),
        data_root=output_root,
        default_model_name="fake_llm",
        supervisor_model_name="fake_llm",
        enable_llm_routing=False,
        trace_enabled=True,
        data_capture_enabled=True,
    )


def _fake_environment() -> dict[str, str]:
    return {
        "USE_REAL_RAG_TOOL": "false",
        "FAKE_RAG_SCENARIO": "default",
        "FAKE_LLM_SCENARIO": "default",
        "ENABLE_AGENT_SELF_RAG": "false",
        "ENABLE_SEMANTIC_GATE": "false",
    }


def _run_paused_fresh(
    output_root: Path,
    *,
    run_id: str,
    factory: _RecordingFactory,
):
    with ExitStack() as stack:
        stack.enter_context(patch.dict(os.environ, _fake_environment()))
        stack.enter_context(
            patch.object(
                SchemeGenerationStageService,
                "prepare_document_plan",
                _require_human_review,
            )
        )
        return MainlineApplicationService(supervisor_factory=factory).run(
            project_root=PROJECT_ROOT,
            user_input="生成需要人工审核的企业级 RAG-Agent 建设方案",
            run_id=run_id,
            output_root=output_root,
            clean_existing=True,
            settings=_fake_settings(output_root),
            allow_demo_defaults=True,
            enable_agent_self_rag=False,
        )


def _review_snapshot(summary: dict) -> str:
    execution = summary["supervisor_result"]["result"]["workflow_execution"]
    return execution["metadata"]["interrupts"][0]["metadata"][
        "input_snapshot_version"
    ]


def _persisted_top_level_payload(
    *,
    checkpoint_path: Path,
    workflow,
    run_id: str,
):
    resource = create_sqlite_checkpoint_resource(checkpoint_path)
    try:
        checkpoint = resource.saver.get_tuple(
            phase2_checkpoint_identity(workflow, run_id=run_id)
        )
        assert checkpoint is not None
        values = checkpoint.checkpoint["channel_values"]
        return SchemeGenerationGraphStateCodec.restore_payload(values)
    finally:
        resource.close()


def test_fresh_runtime_durable_resume_preserves_bundle_and_never_replays_document_retrieval(
    tmp_path,
) -> None:
    """T4D-01/T4D-02: real cross-instance SQLite resume with fail-on-call retrieval."""

    first_factory = _RecordingFactory()
    first_service = MainlineApplicationService(supervisor_factory=first_factory)
    with ExitStack() as stack:
        stack.enter_context(patch.dict(os.environ, _fake_environment()))
        stack.enter_context(
            patch.object(
                SchemeGenerationStageService,
                "prepare_document_plan",
                _require_human_review,
            )
        )
        paused = first_service.run(
            project_root=PROJECT_ROOT,
            user_input="生成需要人工审核的企业级 RAG-Agent 建设方案",
            run_id="phase4d_fresh_runtime_resume",
            output_root=tmp_path,
            clean_existing=True,
            settings=_fake_settings(tmp_path),
            allow_demo_defaults=True,
            enable_agent_self_rag=False,
        )

    assert paused["status"] == "paused"
    first_execution = paused["supervisor_result"]["result"]["workflow_execution"]
    assert first_execution["metadata"]["business_node_sequence"] == REVIEW_PREFIX
    assert first_execution["metadata"]["checkpoint_enabled"] is True
    assert len(first_factory.records) == 1
    first_record = first_factory.records[0]
    first_checkpoint_path = first_record["checkpoint_path"]
    assert isinstance(first_checkpoint_path, Path)
    assert first_checkpoint_path.is_file()

    before_payload = _persisted_top_level_payload(
        checkpoint_path=first_checkpoint_path,
        workflow=first_factory.workflows[0],
        run_id=paused["run_id"],
    )
    before_bundle = before_payload.document_evidence_bundle
    assert before_bundle is not None
    before_hash = evidence_bundle_sha256(before_bundle)
    before_trace_id = before_bundle.retrieval_trace_id
    assert before_trace_id
    first_rag_facade = first_record["rag_facade"]
    first_rag_calls = first_rag_facade.runnable_invocation_count
    assert first_rag_calls >= 1

    second_factory = _RecordingFactory()
    second_service = MainlineApplicationService(supervisor_factory=second_factory)
    assert second_service is not first_service
    assert second_factory is not first_factory

    with ExitStack() as stack:
        stack.enter_context(patch.dict(os.environ, _fake_environment()))
        stack.enter_context(
            patch.object(
                SchemeGenerationStageService,
                "retrieve_document_evidence",
                side_effect=AssertionError(
                    "completed document retrieval must not replay during durable resume"
                ),
            )
        )
        resumed = second_service.resume_run(
            project_root=PROJECT_ROOT,
            task_id=paused["task_id"],
            run_id=paused["run_id"],
            decision="approved",
            input_snapshot_version=_review_snapshot(paused),
            feedback="phase4d fresh-runtime resume approval",
            reviewer_id="phase4d-verifier",
            output_root=tmp_path,
            settings=_fake_settings(tmp_path),
            allow_demo_defaults=True,
            enable_agent_self_rag=False,
        )

    assert len(second_factory.records) == 1
    second_record = second_factory.records[0]
    assert second_record["supervisor"] is not first_record["supervisor"]
    assert second_record["engine"] is not first_record["engine"]
    assert second_record["stage_service"] is not first_record["stage_service"]
    assert second_record["rag_facade"] is not first_record["rag_facade"]
    assert second_record["model_facade"] is not first_record["model_facade"]
    assert second_record["checkpoint_path"] == first_checkpoint_path
    assert second_record["rag_facade"].runnable_invocation_count == 0
    assert first_record["rag_facade"].runnable_invocation_count == first_rag_calls

    execution = resumed["supervisor_result"]["result"]["workflow_execution"]
    assert execution["metadata"]["resumed"] is True
    assert execution["metadata"]["resume_count"] == 1
    assert execution["metadata"]["checkpoint_thread_id"] == paused["run_id"]
    assert execution["metadata"]["checkpoint_namespace"] == (
        first_execution["metadata"]["checkpoint_namespace"]
    )
    sequence = execution["metadata"]["business_node_sequence"]
    assert sequence[: len(REVIEW_PREFIX)] == REVIEW_PREFIX
    assert sequence.count("retrieve_document_evidence") == 1
    assert len(sequence) == len(set(sequence))

    after_payload = _persisted_top_level_payload(
        checkpoint_path=second_record["checkpoint_path"],
        workflow=second_factory.workflows[0],
        run_id=paused["run_id"],
    )
    after_bundle = after_payload.document_evidence_bundle
    assert after_bundle is not None
    assert evidence_bundle_sha256(after_bundle) == before_hash
    assert after_bundle.retrieval_trace_id == before_trace_id


def _source_chunk(marker: str) -> RetrievedChunkSchema:
    return RetrievedChunkSchema(
        rank=1,
        matched_chunk_id=f"child_{marker}",
        context_chunk_id=f"parent_{marker}",
        child_chunk_id=f"child_{marker}",
        parent_chunk_id=f"parent_{marker}",
        doc_id=f"doc_{marker}",
        match_text=f"quote {marker}",
        context_text=f"context {marker}",
        title=f"Source {marker}",
    )


def _source_citation(marker: str, local_id: str) -> CitationSchema:
    return CitationSchema(
        citation_id=local_id,
        source_type="document",
        source_document_id=f"doc_{marker}",
        doc_id=f"doc_{marker}",
        parent_chunk_id=f"parent_{marker}",
        child_chunk_id=f"child_{marker}",
        quote_text=f"quote {marker}",
        title=f"Source {marker}",
    )


def test_citation_registry_restores_from_real_top_level_checkpoint_shape_with_continuous_labels() -> None:
    """T4D-03: all_citations JSON checkpoint round-trip, restore, dedup and C4 continuity."""

    canonical_bundle = build_evidence_bundle(
        query="canonical citation bundle",
        rewritten_queries=[],
        selected_chunks=[
            _source_chunk("A"),
            _source_chunk("B"),
            _source_chunk("C"),
        ],
        dropped_chunks=[],
        citations=[
            _source_citation("A", "local-A"),
            _source_citation("B", "local-B"),
            _source_citation("C", "local-C"),
        ],
        trace=None,
        max_context_chars=6000,
    )
    canonical_hash = evidence_bundle_sha256(canonical_bundle)
    canonical_ids = [item.citation_id for item in canonical_bundle.citations]
    registry = DocumentCitationRegistry()
    initial, _ = registry.register(
        canonical_bundle.citations,
        scope="document",
        query="initial",
    )
    assert [item.citation_id for item in initial] == ["C1", "C2", "C3"]
    initial_refs = [item.citation_ref for item in initial]
    assert all(initial_refs)

    payload = SchemeGenerationGraphPayloadSchema(
        task_id="task_phase4d_citation",
        run_id="run_phase4d_citation",
        all_citations=registry.all(),
    )
    codec = SchemeGenerationGraphStateCodec()
    runtime_state = {
        "task_id": payload.task_id,
        "run_id": payload.run_id,
        **codec.build_initial_update(payload),
    }
    restored_payload = codec.restore_payload(
        json.loads(json.dumps(runtime_state, ensure_ascii=False))
    )
    restored = DocumentCitationRegistry.from_citations(restored_payload.all_citations)
    assert [item.citation_id for item in restored.all()] == ["C1", "C2", "C3"]

    a_again, a_map = restored.register(
        [_source_citation("A", "different-local-A")],
        scope="section",
        query="A again",
    )
    assert a_map == {"different-local-A": "C1"}
    assert a_again[0].citation_ref == initial_refs[0]
    assert len(restored.all()) == 3

    b_again, b_map = restored.register(
        [_source_citation("B", "different-local-B")],
        scope="recovery",
        query="B again",
    )
    assert b_map == {"different-local-B": "C2"}
    assert b_again[0].citation_ref == initial_refs[1]
    assert len(restored.all()) == 3

    d_first, d_map = restored.register(
        [_source_citation("D", "local-D")],
        scope="section",
        query="D first",
    )
    assert d_map == {"local-D": "C4"}
    d_ref = d_first[0].citation_ref
    assert d_ref
    assert len(restored.all()) == 4

    d_again, d_again_map = restored.register(
        [_source_citation("D", "another-local-D")],
        scope="self_rag_recovery",
        query="D again",
    )
    assert d_again_map == {"another-local-D": "C4"}
    assert d_again[0].citation_ref == d_ref
    assert len(restored.all()) == 4
    assert DocumentCitationRegistry.replace_markers(
        "A[different-local-A] B[different-local-B] D[another-local-D]",
        {**a_map, **b_map, **d_again_map},
    ) == "A[C1] B[C2] D[C4]"
    assert evidence_bundle_sha256(canonical_bundle) == canonical_hash
    assert [item.citation_id for item in canonical_bundle.citations] == canonical_ids


def _typed_context_fixture():
    citation = CitationSchema(
        citation_id="C1",
        source_type="document",
        doc_id="doc_security",
        child_chunk_id="child_1",
        title="政务云安全规范",
        quote_text="政务云应采用统一身份认证和审计。",
    )
    evidence = RAGEvidenceItemSchema(
        evidence_id="E1",
        disposition=EvidenceDisposition.SELECTED,
        rank=1,
        matched_chunk_id="child_1",
        context_chunk_id="parent_1",
        child_chunk_id="child_1",
        parent_chunk_id="parent_1",
        doc_id="doc_security",
        match_text="统一身份认证",
        context_text="政务云应采用统一身份认证、最小权限控制和审计机制。[C1]",
        citation_ids=["C1"],
    )
    rag_context = RAGContextSchema(
        context_text=evidence.context_text,
        used_context_chunk_ids=["parent_1"],
        matched_chunk_ids=["child_1"],
        used_doc_ids=["doc_security"],
        used_context_chars=len(evidence.context_text),
        context_item_count=1,
    )
    contract = RAGEvidenceContractSchema(
        query="政务云安全设计",
        items=[evidence],
        selected_evidence_ids=["E1"],
        citations=[citation],
        context=rag_context,
        lineage=RAGEvidenceLineageSchema(
            index_version="idx_v1",
            embedding_model="m3e-base",
        ),
        assessment=RAGEvidenceAssessmentSchema(),
    )
    project_input = ProjectInputSchema.model_validate(
        {
            "task_id": "task_phase4d_context",
            "project_name": "某政务云",
            "project_type": "政务云",
            "task_type": "scheme_generation",
            "user_query": "生成政务云建设方案",
            "generation_requirements": {
                "required_sections": ["总体架构", "安全设计"],
                "citation_required_sections": ["安全设计"],
                "max_context_chars": 6000,
            },
            "output_schema": {
                "document_title": "某政务云建设方案",
                "required_sections": ["总体架构", "安全设计"],
            },
        }
    )
    previous = SchemeSectionSchema(
        section_id="section_1",
        section_title="总体架构",
        section_order=1,
        content="项目采用私有化部署和分层架构。",
        status=ExecutionStatus.SUCCESS,
    )
    return citation, rag_context, contract, project_input, previous


def test_context_manager_typed_semantics_match_and_extra_full_bundle_cannot_reverse_recover() -> None:
    """T4D-04: typed positive parity plus illegal RAGContext.extra negative."""

    citation, rag_context, contract, project_input, previous = _typed_context_fixture()
    policy = SectionGenerationContextPolicy()
    common = {
        "task_id": "task_phase4d_context",
        "run_id": "run_phase4d_context",
        "section_id": "section_2",
        "section_title": "安全设计",
        "section_order": 2,
        "project_input": project_input,
        "section_contract": "只编写安全设计。",
        "target_section_chars": 800,
        "citations": [citation],
        "previous_sections": [previous],
    }

    positive_request = policy.build_request(
        **common,
        rag_context=rag_context,
        evidence_bundle=contract,
    )
    positive = LLMContextManager().build(positive_request)
    by_id = {item.item_id: item for item in positive.selected_items}
    assert "E1" in by_id
    assert by_id["E1"].citation_ids == ["C1"]
    assert "[C1]" in positive.rendered_context
    assert "previous_sections" in by_id
    assert positive.lineage["index_version"] == "idx_v1"
    assert positive.lineage["embedding_model"] == "m3e-base"
    assert positive.section_id == "section_2"
    assert positive.section_title == "安全设计"
    assert positive.task_id == "task_phase4d_context"
    assert positive.budget.used_context_chars <= positive.budget.max_context_chars
    assert positive.budget.estimated_input_tokens <= (
        positive.budget.max_input_tokens
        - positive.budget.reserved_output_tokens
        - positive.budget.safety_margin_tokens
    )

    poisoned_projection = rag_context.model_copy(deep=True)
    poisoned_projection.extra = {
        "evidence_contract": contract.model_dump(mode="json"),
        "evidence_contract_sha256": "f" * 64,
    }
    negative_request = policy.build_request(
        **common,
        rag_context=poisoned_projection,
        evidence_bundle=None,
    )
    negative = LLMContextManager().build(negative_request)
    negative_by_id = {item.item_id: item for item in negative.selected_items}
    assert "E1" not in negative_by_id
    assert "rag_context_projection" in negative_by_id
    assert "index_version" not in negative.lineage
    assert "embedding_model" not in negative.lineage


def _assert_no_full_bundle(value) -> None:
    if isinstance(value, dict):
        assert value.get("schema_version") != "rag_evidence_contract_v1"
        assert "evidence_bundle" not in value
        for item in value.values():
            _assert_no_full_bundle(item)
    elif isinstance(value, list):
        for item in value:
            _assert_no_full_bundle(item)


def test_real_lightweight_consumers_report_manifest_and_rag_tool_output_use_legal_sources(
    tmp_path,
) -> None:
    """T4D-05: exercise runtime output -> report -> manifest without retired full bundles."""

    summary = run_fake_mainline_scenario(
        tmp_path / "consumer",
        run_id="phase4d_real_lightweight_consumer",
        rag_scenario="citation_collision",
        llm_scenario="always_grounded",
        enable_corrective_section_retrieval=False,
    )
    output = summary["scheme_writer_output"]
    section_summaries = output["section_evidence"]
    assert section_summaries
    forbidden_summary_keys = {
        "retrieval_scope",
        "query",
        "citations",
        "metadata",
        "evidence_bundle",
    }
    assert all(not (forbidden_summary_keys & set(item)) for item in section_summaries)
    assert all("recovery_count" in item for item in section_summaries)
    assert all("tool_call_ids" in item for item in section_summaries)
    assert all("evidence_contract_sha256" in item for item in section_summaries)

    section_by_title = {
        item["section_title"]: item
        for item in (summary.get("generated_draft") or {}).get("sections", [])
    }
    rows = _section_report_rows(summary)
    assert rows
    summary_by_title = {item["section_title"]: item for item in section_summaries}
    for row in rows:
        title = row["section_title"]
        section_extra = section_by_title[title].get("extra") or {}
        assert row["evidence_scope"] == section_extra.get("evidence_scope")
        assert row["evidence_query"] == section_extra.get("evidence_query")
        assert row["recovery_count"] == summary_by_title[title]["recovery_count"]
        assert row["tool_call_ids"] == summary_by_title[title]["tool_call_ids"]
        assert row["citation_ids"] == list(section_by_title[title].get("citation_ids") or [])

    shared_state = summary["supervisor_result"]["result"]["shared_state"]
    rag_tool_output = shared_state["contexts"]["rag_tool_output"]
    restored_rag_tool_output = json.loads(json.dumps(rag_tool_output, ensure_ascii=False))
    assert restored_rag_tool_output["section_evidence"] == section_summaries
    assert restored_rag_tool_output["document"] is not None
    _assert_no_full_bundle(restored_rag_tool_output)
    _assert_no_full_bundle(output["section_evidence"])

    persisted = persist_end_to_end_artifacts(
        summary,
        runtime_preflight={
            "mode": "fake",
            "model_name": "fake_llm",
            "rag_tool": "FakeRAGTool",
        },
        expected_model_name="fake_llm",
        expected_real_rag=False,
        raise_on_validation_error=False,
    )
    report = persisted["report"]
    assert report["schema_version"] == "mainline_e2e_report_v1"
    assert report["sections"]
    assert Path(persisted["report_path"]).is_file()
    manifest_path = Path(summary["paths"]["experiment_manifest"])
    assert manifest_path.is_file()
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    assert manifest["run_id"] == summary["run_id"]
    assert manifest["task_id"] == summary["task_id"]


def _legacy_shared_state():
    from agent.runtime.shared_state_schema import SharedStateSchema

    return SharedStateSchema(
        task_id="task_phase4d_legacy",
        run_id="run_phase4d_legacy",
        task_type="scheme_generation",
        user_input="生成建设方案",
        context_bundle=ContextBundleSchema(
            user=UserContextSchema(user_query="生成建设方案"),
            task=TaskContextSchema(
                task_id="task_phase4d_legacy",
                run_id="run_phase4d_legacy",
                task_type="scheme_generation",
            ),
        ),
        created_at="2026-08-15T12:00:00+08:00",
    )


def test_legacy_adapter_upgrade_metadata_and_scheme_evidence_service_reenter_typed_path() -> None:
    """T4D-06: legacy ingress upgrades once, exports projections, then returns typed Bundle."""

    chunk = RetrievedChunkSchema(
        rank=1,
        matched_chunk_id="legacy_child",
        context_chunk_id="legacy_parent",
        child_chunk_id="legacy_child",
        parent_chunk_id="legacy_parent",
        doc_id="legacy_doc",
        match_text="采用统一身份认证。",
        context_text="安全设计采用统一身份认证，并记录审计日志。",
    )
    result = ToolResultSchema(
        tool_call_id="phase4d_legacy_call",
        task_id="task_phase4d_legacy",
        run_id="run_phase4d_legacy",
        tool_name="LegacyRAGTool",
        success=True,
        result={
            "query": "安全设计",
            "retrieved_chunks": [chunk.model_dump(mode="json")],
            "citations": [],
        },
        created_at="2026-08-15T12:00:00+08:00",
    )
    shared_state = _legacy_shared_state()
    upgraded = LegacyEvidenceAdapter.upgrade(shared_state, result)
    assert upgraded.extra["compatibility_upgrade"] is True
    assert upgraded.extra["upgraded_by"] == "LegacyEvidenceAdapter"
    assert upgraded.context.context_text
    assert upgraded.citations

    context, chunks, citations, normalized = LegacyEvidenceAdapter.extract(
        shared_state, result
    )
    assert context.context_text
    assert chunks and citations
    assert "evidence" not in normalized
    assert "evidence_contract" not in normalized
    assert "evidence_contract" not in context.extra

    rag_service = type(
        "LegacyRAGStub",
        (),
        {"retrieve": lambda self, request: result},
    )()
    service = SchemeEvidenceService(rag_service=rag_service, agent_name="phase4d-test")
    project_input = ProjectInputSchema(
        task_id="task_phase4d_legacy",
        task_type="scheme_generation",
        user_query="生成建设方案",
        generation_requirements={"required_sections": ["安全设计"]},
        output_schema={
            "document_title": "建设方案",
            "required_sections": ["安全设计"],
        },
    )
    typed = service.retrieve(
        shared_state,
        project_input,
        query="安全设计",
        scope="document",
    )
    assert isinstance(typed, RAGEvidenceContractSchema)
    assert typed.extra["compatibility_upgrade"] is True
