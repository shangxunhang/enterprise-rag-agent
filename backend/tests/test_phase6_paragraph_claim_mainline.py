"""Round-6 acceptance for checkpoint-owned Paragraph and Claim pipelines."""

from __future__ import annotations

import hashlib
from pathlib import Path
from types import SimpleNamespace

from agent.runtime.graph_state import GraphStateSchema
from apps.enterprise_document.schemas.project_input_schema import ProjectInputSchema
from apps.enterprise_document.schemas.section_execution_graph import (
    SectionExecutionGraphPayloadSchema,
    SectionExecutionGraphStateCodec,
)
from apps.enterprise_document.schemas.scheme_writer_schema import (
    SchemeSectionSchema,
    SectionEvalSchema,
    SectionPlanSchema,
)
from apps.enterprise_document.schemas.table_agent_schema import StructuredFactSchema
from apps.enterprise_document.services.agent_rag_v1.streaming_writer import (
    CheckpointParagraphPipeline,
)
from apps.enterprise_document.workflows.section_execution_graph import (
    SectionExecutionGraph,
)
from schemas.context import ContextBundleSchema, TaskContextSchema, UserContextSchema
from schemas.rag import RAGContextSchema
from schemas.status import ExecutionStatus
from test_phase4_section_agents_mainline import (
    _latest_section_payloads,
    _run_fake,
    canonical_section_mainline,
    _canonical_completed_payloads,
)


_NOW = "2026-08-01T13:30:00+08:00"


def _project_input(task_id: str) -> ProjectInputSchema:
    return ProjectInputSchema(
        task_id=task_id,
        task_type="scheme_generation",
        user_query="生成建设方案",
        generation_requirements={
            "required_sections": ["技术方案"],
            "need_citation": False,
            "citation_required_sections": [],
        },
        output_schema={
            "document_title": "建设方案",
            "required_sections": ["技术方案"],
        },
    )


def _graph_state(*, task_id: str, run_id: str) -> GraphStateSchema:
    return GraphStateSchema(
        task_id=task_id,
        run_id=run_id,
        task_type="scheme_generation",
        user_input="生成建设方案",
        context_bundle=ContextBundleSchema(
            user=UserContextSchema(user_query="生成建设方案"),
            task=TaskContextSchema(
                task_id=task_id,
                run_id=run_id,
                task_type="scheme_generation",
            ),
        ),
        created_at=_NOW,
    )


def _rag_context() -> RAGContextSchema:
    return RAGContextSchema(
        context_text="",
        used_context_chars=0,
        context_item_count=0,
    )


def _section_payload(
    *,
    content: str,
    structured_facts: list[StructuredFactSchema] | None = None,
) -> SectionExecutionGraphPayloadSchema:
    task_id = "task-round6-paragraph"
    run_id = "run-round6-paragraph"
    section_id = f"section_{run_id}_001"
    plan = SectionPlanSchema(
        section_id=section_id,
        section_title="技术方案",
        section_order=1,
        citation_required=False,
    )
    section = SchemeSectionSchema(
        section_id=section_id,
        section_title=plan.section_title,
        section_order=plan.section_order,
        content=content,
        status=ExecutionStatus.SUCCESS,
        eval_result=SectionEvalSchema(
            passed=True,
            checks={"content_nonempty": True},
        ),
        started_at=_NOW,
        finished_at=_NOW,
    )
    pipeline = CheckpointParagraphPipeline()
    candidates = pipeline.plan(
        section_id=section_id,
        content=content,
        paragraph_version=1,
    )
    return SectionExecutionGraphPayloadSchema(
        task_id=task_id,
        run_id=run_id,
        document_id=f"document_{run_id}",
        project_input=_project_input(task_id),
        section_plan=plan,
        structured_facts=list(structured_facts or []),
        shared_state_payload=SectionExecutionGraphStateCodec.dump_graph_state(
            _graph_state(task_id=task_id, run_id=run_id)
        ),
        section_id=section_id,
        section_title=plan.section_title,
        section_order=plan.section_order,
        section=section,
        paragraph_candidates=candidates,
        paragraph_cursor=0,
        paragraph_pipeline_revision=1,
        current_node="prepare_paragraph_pipeline",
    )


def _restore_update(
    codec: SectionExecutionGraphStateCodec,
    state: dict,
    update: dict,
) -> SectionExecutionGraphPayloadSchema:
    return codec.restore_payload({**state, **update})


def _section_graph() -> SectionExecutionGraph:
    coordinator = SimpleNamespace(
        evidence_service=None,
        query_builder=None,
        section_generation_service=None,
        runtime_support=None,
        generation_quality_metadata={},
        section_agent_adapter=None,
    )
    return SectionExecutionGraph(coordinator=coordinator)


def test_paragraph_identity_and_checkpoint_artifact_are_replay_stable() -> None:
    pipeline = CheckpointParagraphPipeline()
    section_id = "section-stable"
    first = pipeline.plan(
        section_id=section_id,
        content="第一段。\n\n第二段。",
        paragraph_version=1,
    )
    replay = pipeline.plan(
        section_id=section_id,
        content="第一段。\n\n第二段。",
        paragraph_version=1,
    )

    assert [item.paragraph_id for item in first] == [
        item.paragraph_id for item in replay
    ]
    assert len({item.paragraph_id for item in first}) == 2

    artifact = pipeline.persist(
        task_id="task-stable",
        run_id="run-stable",
        section_id=section_id,
        candidate=first[0],
    )
    assert artifact.content_ref.startswith("checkpoint://")
    assert artifact.content == "第一段。"
    assert artifact.content_hash == (
        "sha256:" + hashlib.sha256("第一段。".encode("utf-8")).hexdigest()
    )
    artifact.verify_content_integrity()


def test_replayed_persist_node_does_not_duplicate_committed_paragraph() -> None:
    graph = _section_graph()
    codec = SectionExecutionGraphStateCodec()
    payload = _section_payload(content="第一段。\n\n第二段。")

    first_state = codec.build_initial_state(payload)
    first_update = graph._persist_paragraph(first_state)
    first = _restore_update(codec, first_state, first_update)
    first_revision = first.paragraph_pipeline_revision
    assert len(first.paragraph_artifacts) == 1

    replay_state = codec.build_initial_state(first)
    replay_update = graph._persist_paragraph(replay_state)
    replay = _restore_update(codec, replay_state, replay_update)
    assert len(replay.paragraph_artifacts) == 1
    assert replay.paragraph_artifacts == first.paragraph_artifacts
    assert replay.paragraph_pipeline_revision == first_revision
    assert replay.route == "precheck_paragraph"


def test_blocking_excel_precheck_stops_before_later_paragraphs() -> None:
    fact = StructuredFactSchema(
        fact_id="excel-server-quantity",
        task_id="task-round6-paragraph",
        run_id="run-round6-paragraph",
        fact_type="device_summary",
        content="服务器",
        source_type="table",
        source_ids=["excel://device-list/row-1"],
        created_at=_NOW,
        extra={
            "device_name": "服务器",
            "quantity": "2",
            "unit": "台",
        },
    )
    graph = _section_graph()
    codec = SectionExecutionGraphStateCodec()
    payload = _section_payload(
        content="部署服务器3台。\n\n第二段不应被提交。",
        structured_facts=[fact],
    )

    persist_state = codec.build_initial_state(payload)
    persisted = _restore_update(
        codec,
        persist_state,
        graph._persist_paragraph(persist_state),
    )
    precheck_state = codec.build_initial_state(persisted)
    checked = _restore_update(
        codec,
        precheck_state,
        graph._precheck_paragraph(precheck_state),
    )

    first_id = str(payload.paragraph_candidates[0].paragraph_id)
    second_id = str(payload.paragraph_candidates[1].paragraph_id)
    assert checked.route == "evaluate_generation"
    assert checked.paragraph_blocked_id == first_id
    assert [item.paragraph_id for item in checked.paragraph_artifacts] == [first_id]
    assert second_id not in {
        item.paragraph_id for item in checked.paragraph_artifacts
    }
    assert checked.paragraph_precheck_results[0].blocking is True
    assert checked.generation_check["paragraph_precheck_blocking"] is True
    assert checked.section is not None
    assert checked.section.status == ExecutionStatus.FAILED
    assert checked.section.content == ""


def test_mainline_checkpoint_contains_paragraph_atoms_and_claim_projection(canonical_section_mainline) -> None:
    data = canonical_section_mainline
    for payload in _canonical_completed_payloads(data):
        candidates = payload.paragraph_candidates
        artifacts = payload.paragraph_artifacts
        prechecks = payload.paragraph_precheck_results
        assert candidates
        assert len(artifacts) == len(candidates) == len(prechecks)
        assert payload.paragraph_cursor == len(candidates)
        assert payload.paragraph_blocked_id is None
        assert payload.completed and payload.route == "end"
        expected_ids = [item.paragraph_id for item in candidates]
        assert [item.paragraph_id for item in artifacts] == expected_ids
        assert [item.paragraph_id for item in prechecks] == expected_ids
        for artifact in artifacts:
            artifact.verify_content_integrity()
            assert artifact.content.strip()
        assert payload.section.content == "\n\n".join(item.content for item in artifacts)
        source = payload.result.canonical_section_source_binding
        assert source.paragraph_manifest == payload.section_paragraph_manifest
        assert source.paragraph_manifest.generation_membership_state == "SEALED"
        assert len(source.paragraph_manifest.members) == len(artifacts)
        assert source.coverage_proof_refs and source.verification_refs
        assert data["runtime"].validate_source(source, require_accepted=True) == source
        for version, artifact in zip(source.paragraph_manifest.members, artifacts):
            canonical = data["runtime"].paragraph_runtime.read_paragraph(version)
            assert canonical.text == artifact.content
