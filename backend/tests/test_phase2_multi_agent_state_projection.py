"""Round-2 acceptance for typed Multi-Agent state in production payloads."""

from __future__ import annotations

import json

from agent.runtime.graph_state import GraphStateSchema
from apps.enterprise_document.schemas.project_input_schema import ProjectInputSchema
from apps.enterprise_document.schemas.scheme_generation_graph import (
    SchemeGenerationGraphPayloadSchema,
    SchemeGenerationGraphStateCodec,
)
from apps.enterprise_document.schemas.scheme_writer_schema import (
    AttemptStateSchema,
    AttemptStatus,
    ClaimStateSchema,
    DocumentPlanSchema,
    DocumentValidationStatus,
    HardGateResultSchema,
    PlanProjectionStatus,
    ResearchProjectionStatus,
    ResearchStateSchema,
    SchemeSectionSchema,
    SectionEvidenceBundleSchema,
    SectionEvalSchema,
    SectionLifecycleStatus,
    SectionPlanSchema,
    SectionStateSchema,
    VerificationProjectionStatus,
    VerificationStateSchema,
    WritingProjectionStatus,
    WritingStateSchema,
)
from apps.enterprise_document.schemas.section_execution_graph import (
    SectionExecutionGraphPayloadSchema,
    SectionExecutionGraphStateCodec,
)
from schemas.context import ContextBundleSchema, TaskContextSchema, UserContextSchema
from schemas.rag import RAGContextSchema
from schemas.status import ExecutionStatus


_NOW = "2026-07-31T16:55:00+08:00"


def _project_input(task_id: str = "task-round2") -> ProjectInputSchema:
    return ProjectInputSchema(
        task_id=task_id,
        task_type="scheme_generation",
        user_query="生成建设方案",
        generation_requirements={
            "required_sections": ["项目概述"],
            "need_citation": False,
            "citation_required_sections": [],
        },
        output_schema={
            "document_title": "建设方案",
            "required_sections": ["项目概述"],
        },
    )


def _section_plan(run_id: str = "run-round2") -> SectionPlanSchema:
    return SectionPlanSchema(
        section_id=f"section_{run_id}_001",
        section_title="项目概述",
        section_order=1,
        citation_required=False,
    )


def _graph_state(
    *,
    task_id: str = "task-round2",
    run_id: str = "run-round2",
) -> GraphStateSchema:
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
        extra={"evidence_contract_sha256": "sha-round2"},
    )


def test_round2_uses_nested_payload_state_not_another_shared_state() -> None:
    assert "plan_state" in SchemeGenerationGraphPayloadSchema.model_fields
    assert "section_states" in SchemeGenerationGraphPayloadSchema.model_fields
    assert "document_validation" in SchemeGenerationGraphPayloadSchema.model_fields
    assert "section_state" in SectionExecutionGraphPayloadSchema.model_fields
    assert "multi_agent_shared_state" not in (
        SchemeGenerationGraphPayloadSchema.model_fields
    )
    assert "multi_agent_shared_state" not in (
        SectionExecutionGraphPayloadSchema.model_fields
    )

    state = SectionStateSchema(
        section_id="section-1",
        section_title="项目概述",
        section_order=1,
    )
    assert isinstance(state.attempt, AttemptStateSchema)
    assert isinstance(state.research, ResearchStateSchema)
    assert isinstance(state.writing, WritingStateSchema)
    assert isinstance(state.claims, ClaimStateSchema)
    assert isinstance(state.verification, VerificationStateSchema)


def test_top_level_payload_projects_plan_sections_and_document_validation() -> None:
    task_id = "task-round2-top"
    run_id = "run-round2-top"
    plan = _section_plan(run_id)
    document_plan = DocumentPlanSchema(
        plan_id=f"plan_{run_id}",
        document_id=f"document_{run_id}",
        document_title="建设方案",
        sections=[plan],
        created_at=_NOW,
    )
    section = SchemeSectionSchema(
        section_id=plan.section_id,
        section_title=plan.section_title,
        section_order=plan.section_order,
        content="项目概述正文。",
        status=ExecutionStatus.SUCCESS,
        eval_result=SectionEvalSchema(
            passed=True,
            checks={"content_present": True},
        ),
        started_at=_NOW,
        finished_at=_NOW,
    )
    payload = SchemeGenerationGraphPayloadSchema(
        task_id=task_id,
        run_id=run_id,
        project_input=_project_input(task_id),
        document_id=document_plan.document_id,
        document_plan=document_plan,
        sections=[section],
        hard_gate=HardGateResultSchema(
            passed=True,
            checks={"required_sections_present": True},
        ),
    )

    codec = SchemeGenerationGraphStateCodec()
    runtime_state = {
        "task_id": task_id,
        "run_id": run_id,
        **codec.build_initial_update(payload),
    }
    codec.assert_json_serializable(runtime_state)
    restored = codec.restore_payload(json.loads(json.dumps(runtime_state)))

    assert restored.plan_state.status == PlanProjectionStatus.READY
    assert restored.plan_state.canonical_plan_id == document_plan.plan_id
    assert restored.plan_state.plan_revision == 1
    assert restored.plan_state.section_order == [plan.section_id]

    projected = restored.section_states[plan.section_id]
    assert projected.lifecycle_status == SectionLifecycleStatus.ACCEPTED
    assert projected.attempt.status == AttemptStatus.COMPLETED
    assert projected.writing.status == WritingProjectionStatus.COMPLETED
    assert projected.writing.generation_complete is True
    assert projected.verification.status == VerificationProjectionStatus.COMPLETED
    assert projected.verification.checks == {"content_present": True}

    assert (
        restored.document_validation.status
        == DocumentValidationStatus.PASSED
    )
    assert restored.document_validation.validation_revision == 1
    assert restored.document_validation.checks == {
        "required_sections_present": True
    }


def test_section_payload_round_trip_persists_all_typed_substates() -> None:
    task_id = "task-round2-section"
    run_id = "run-round2-section"
    plan = _section_plan(run_id)
    context = _rag_context()
    section = SchemeSectionSchema(
        section_id=plan.section_id,
        section_title=plan.section_title,
        section_order=plan.section_order,
        content="已经生成的章节正文。",
        status=ExecutionStatus.SUCCESS,
        eval_result=SectionEvalSchema(
            passed=True,
            checks={"citation_bound": True},
        ),
        started_at=_NOW,
        finished_at=_NOW,
    )
    evidence = SectionEvidenceBundleSchema(
        section_id=plan.section_id,
        section_title=plan.section_title,
        query="项目概述",
        rag_context=context,
        tool_call_ids=["rag-call-1"],
        evidence_contract_sha256="sha-round2",
    )
    initial_state = SectionStateSchema(
        section_id=plan.section_id,
        section_title=plan.section_title,
        section_order=plan.section_order,
        claims=ClaimStateSchema(
            claim_ids=["claim-1"],
            active_claim_ids=["claim-1"],
            claim_set_version=1,
        ),
        writing=WritingStateSchema(
            paragraph_refs={"p001": "artifact://paragraph/p001"},
            paragraph_versions={"p001": 1},
            expected_paragraph_ids=["p001"],
            generation_complete=True,
        ),
        verification=VerificationStateSchema(
            accepted_paragraph_ids=["p001"],
        ),
    )
    payload = SectionExecutionGraphPayloadSchema(
        task_id=task_id,
        run_id=run_id,
        document_id=f"document_{run_id}",
        project_input=_project_input(task_id),
        section_plan=plan,
        shared_state_payload=SectionExecutionGraphStateCodec.dump_graph_state(
            _graph_state(task_id=task_id, run_id=run_id)
        ),
        document_rag_context=context,
        section_id=plan.section_id,
        section_title=plan.section_title,
        section_order=plan.section_order,
        active_context=context,
        active_assessment={"status": "sufficient"},
        tool_call_ids=["rag-call-1"],
        recovery_count=2,
        generation_attempt=3,
        generation_check={"citation_bound": True},
        section=section,
        evidence=evidence,
        section_state=initial_state,
        current_node="finalize_section",
        executed_nodes=[
            "prepare_section",
            "retrieve_section_evidence",
            "generate_section",
            "evaluate_generation",
            "finalize_section",
        ],
        transition_count=5,
        completed=True,
    )

    codec = SectionExecutionGraphStateCodec()
    runtime_state = codec.build_initial_state(payload)
    codec.assert_json_serializable(runtime_state)
    restored = codec.restore_payload(json.loads(json.dumps(runtime_state)))

    state = restored.section_state
    assert state is not None
    assert state.lifecycle_status == SectionLifecycleStatus.ACCEPTED
    assert state.attempt.attempt_number == 3
    assert state.attempt.plan_revision == 1
    assert state.attempt.status == AttemptStatus.COMPLETED
    assert state.research.status == ResearchProjectionStatus.COMPLETED
    assert state.research.research_rounds == 2
    assert state.research.tool_call_ids == ["rag-call-1"]
    assert state.research.evidence_contract_sha256 == "sha-round2"
    assert state.writing.status == WritingProjectionStatus.COMPLETED
    assert state.writing.paragraph_refs == {
        "p001": "artifact://paragraph/p001"
    }
    assert state.claims.claim_ids == ["claim-1"]
    assert state.verification.status == VerificationProjectionStatus.COMPLETED
    assert state.verification.accepted_paragraph_ids == ["p001"]
    assert state.verification.checks == {"citation_bound": True}


def test_legacy_checkpoint_payloads_without_projection_fields_restore() -> None:
    task_id = "task-round2-legacy"
    run_id = "run-round2-legacy"
    plan = _section_plan(run_id)
    document_plan = DocumentPlanSchema(
        plan_id=f"plan_{run_id}",
        document_id=f"document_{run_id}",
        document_title="建设方案",
        sections=[plan],
        created_at=_NOW,
    )
    top_payload = SchemeGenerationGraphPayloadSchema(
        task_id=task_id,
        run_id=run_id,
        document_plan=document_plan,
    )
    top_codec = SchemeGenerationGraphStateCodec()
    top_runtime = {
        "task_id": task_id,
        "run_id": run_id,
        **top_codec.build_initial_update(top_payload),
    }
    legacy_top = json.loads(json.dumps(top_runtime))
    for field_name in ("plan_state", "section_states", "document_validation"):
        legacy_top["scheme_generation_payload"].pop(field_name)
    restored_top = top_codec.restore_payload(legacy_top)
    assert restored_top.plan_state.status == PlanProjectionStatus.READY
    assert plan.section_id in restored_top.section_states
    assert (
        restored_top.document_validation.status
        == DocumentValidationStatus.PENDING
    )

    section_payload = SectionExecutionGraphPayloadSchema(
        task_id=task_id,
        run_id=run_id,
        document_id=document_plan.document_id,
        project_input=_project_input(task_id),
        section_plan=plan,
        shared_state_payload=SectionExecutionGraphStateCodec.dump_graph_state(
            _graph_state(task_id=task_id, run_id=run_id)
        ),
        document_rag_context=_rag_context(),
    )
    section_codec = SectionExecutionGraphStateCodec()
    legacy_section = json.loads(
        json.dumps(section_codec.build_initial_state(section_payload))
    )
    legacy_section["section_payload"].pop("section_state")
    restored_section = section_codec.restore_payload(legacy_section)
    assert restored_section.section_state is not None
    assert restored_section.section_state.section_id == plan.section_id
    assert (
        restored_section.section_state.lifecycle_status
        == SectionLifecycleStatus.PLANNED
    )


def test_projection_models_reject_cross_domain_identity_corruption() -> None:
    try:
        ClaimStateSchema(
            claim_ids=["claim-1"],
            active_claim_ids=["claim-2"],
        )
    except ValueError as exc:
        assert "unknown claim_ids" in str(exc)
    else:
        raise AssertionError("unknown active claim must be rejected")

    try:
        VerificationStateSchema(
            accepted_paragraph_ids=["p001"],
            rejected_paragraph_ids=["p001"],
        )
    except ValueError as exc:
        assert "must be disjoint" in str(exc)
    else:
        raise AssertionError("paragraph cannot be accepted and rejected")
