"""Acceptance-only production orchestration slice for paired 8-section validation.

This module does not own business semantics. It starts the existing
EnterpriseDocumentWorkflowEngine exactly like its production execute() path, then
uses LangGraph's runtime interrupt_after support to stop immediately after the
existing evaluate_document_gate node has completed.

The resulting certification is based on canonical post-write owners:
RunCompletionAssessment + RunLifecycle + exact Section Control refs. The
SectionSourceBinding workflow_status is diagnostic for cross-section dependency
readiness and is intentionally not treated as post-write terminal authority.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from agent.runtime.graph_state import GraphStateSchema
from agent.runtime.workflow_schema import WorkflowDefinitionSchema
from apps.enterprise_document.schemas.post_write.foundation import (
    PostWriteControlDecisionRefSchema,
)
from apps.enterprise_document.schemas.scheme_generation_graph import (
    SchemeGenerationGraphPayloadSchema,
)
from apps.enterprise_document.workflows.top_level_business_graph import (
    EnterpriseDocumentWorkflowEngine,
)
from schemas.status import ExecutionStatus


DOCUMENT_GATE_NODE = "evaluate_document_gate"

PRODUCTION_PREFIX_THROUGH_DOCUMENT_GATE = (
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
    DOCUMENT_GATE_NODE,
)


class PairedProductionGraphFailure(RuntimeError):
    """Preserve a production failure that terminates before the document gate."""

    def __init__(self, payload: SchemeGenerationGraphPayloadSchema) -> None:
        self.payload = payload
        self.error = payload.terminal_error or payload.output_error
        self.failed_business_node = payload.failed_business_node
        self.executed_nodes = tuple(payload.executed_nodes)
        detail = (
            getattr(self.error, "message", None)
            or getattr(self.error, "error_message", None)
            or "production graph terminalized before evaluate_document_gate"
        )
        node = self.failed_business_node or (
            self.executed_nodes[-1] if self.executed_nodes else "unknown"
        )
        super().__init__(f"production failure before document gate at {node}: {detail}")


@dataclass(frozen=True)
class PairedDocumentGateSlice:
    """Exact output of the production graph stopped after document gate."""

    payload: SchemeGenerationGraphPayloadSchema
    final_runtime: dict[str, Any]
    inspection: dict[str, Any]
    certification: dict[str, Any]


def _build_initial_runtime(
    engine: EnterpriseDocumentWorkflowEngine,
    workflow: WorkflowDefinitionSchema,
    graph_state: GraphStateSchema,
) -> dict[str, Any]:
    """Mirror EnterpriseDocumentWorkflowEngine.execute() startup exactly."""

    engine._assert_new_run_storage_admission(graph_state.run_id)
    graph_state.workflow_engine_name = engine.engine_name
    graph_state.workflow_engine_version = engine.engine_version

    initial_graph_state = graph_state.model_copy(deep=True)
    initial_graph_state.status = ExecutionStatus.RUNNING
    runtime = engine.runtime_state_codec.build_initial_state(
        workflow,
        initial_graph_state,
    )
    payload = SchemeGenerationGraphPayloadSchema(
        task_id=graph_state.task_id,
        run_id=graph_state.run_id,
        workflow_namespace=engine.runtime_state_codec.workflow_namespace(workflow),
        started_at=engine.stage_service.runtime_support.now_iso(),
    )
    runtime.update(engine.business_state_codec.build_initial_update(payload))
    engine.business_state_codec.assert_json_serializable(runtime)
    return runtime


def _certify_document_gate(
    engine: EnterpriseDocumentWorkflowEngine,
    payload: SchemeGenerationGraphPayloadSchema,
) -> dict[str, Any]:
    """Read exact canonical owners after evaluate_document_gate completes."""

    composition = engine.stage_service.target_post_write_composition
    if composition is None:
        raise RuntimeError(
            "paired acceptance requires the production Target Post-write composition"
        )
    if payload.target_run_completion_assessment_ref is None:
        raise RuntimeError(
            "evaluate_document_gate completed without RunCompletionAssessment ref"
        )
    if payload.terminal_outcome is None:
        raise RuntimeError(
            "evaluate_document_gate completed without canonical terminal outcome"
        )

    assessment = composition.assessment_store.resolve_exact(
        payload.target_run_completion_assessment_ref
    )
    terminal_run = composition.lifecycle_service.load_run_lifecycle(payload.run_id)
    if terminal_run.status.value != "success":
        raise RuntimeError(
            "document gate did not terminalize the canonical Run as SUCCESS"
        )
    if (
        terminal_run.run_completion_assessment_ref
        != payload.target_run_completion_assessment_ref
    ):
        raise RuntimeError(
            "Run lifecycle terminal assessment differs from document-gate assessment"
        )
    if assessment.aggregate_result.value != "SUCCESS":
        raise RuntimeError(
            "RunCompletionAssessment aggregate result is not SUCCESS"
        )

    expected_section_ids = {
        result.section.section_id for result in payload.section_execution_results
    }
    controls: list[dict[str, Any]] = []
    observed_section_ids: set[str] = set()

    for envelope in assessment.section_completion_refs:
        if envelope.source_kind != "SECTION_CONTROL_DECISION":
            raise RuntimeError(
                "RunCompletion section completion is not a Section Control ref"
            )
        scope = str(envelope.source_scope_ref)
        prefix = "section:"
        if not scope.startswith(prefix) or not scope[len(prefix) :]:
            raise RuntimeError("RunCompletion Section scope is malformed")
        section_id = scope[len(prefix) :]
        if section_id in observed_section_ids:
            raise RuntimeError("RunCompletion contains duplicate Section scope")
        observed_section_ids.add(section_id)

        control_ref = PostWriteControlDecisionRefSchema.model_validate(
            envelope.exact_ref_payload
        )
        control = composition.control_store.resolve_exact(control_ref)
        eligibility = composition.section_eligibility.resolve(
            section_key=section_id,
            control_decision_ref=control_ref,
        )
        controls.append(
            {
                "section_id": section_id,
                "control_decision_ref": control_ref.model_dump(mode="json"),
                "enforcement_action": control.enforcement_action.value,
                "quality_state": control.quality_state_passthrough.value,
                "availability_state": control.availability_projection.state.value,
                "eligible_candidate": eligibility.candidate is not None,
                "selection_state": eligibility.selection_state.value,
                "reason_code": eligibility.reason_code,
            }
        )

    if observed_section_ids != expected_section_ids:
        raise RuntimeError(
            "RunCompletion Section completion set differs from executed Sections"
        )

    return {
        "authority": "target_post_write_run_completion",
        "run_id": payload.run_id,
        "document_id": payload.document_id,
        "run_lifecycle_status": terminal_run.status.value,
        "run_completion_assessment_ref": (
            payload.target_run_completion_assessment_ref.model_dump(mode="json")
        ),
        "run_completion_aggregate_result": assessment.aggregate_result.value,
        "terminal_outcome_status": payload.terminal_outcome.execution_status.value,
        "terminal_control_action": payload.terminal_outcome.control_action.value,
        "artifact_route": payload.artifact_route,
        "section_completion_count": len(controls),
        "sections": sorted(controls, key=lambda item: item["section_id"]),
        "source_workflow_status_is_terminal_authority": False,
    }


def invoke_through_document_gate(
    *,
    engine: EnterpriseDocumentWorkflowEngine,
    workflow: WorkflowDefinitionSchema,
    graph_state: GraphStateSchema,
) -> PairedDocumentGateSlice:
    """Execute the production graph and stop after canonical document gate.

    No production node is reimplemented. The compiled graph owns all business
    transitions from normalization through Target Post-write. The dynamic
    LangGraph interrupt prevents persistence/finalization nodes from running,
    which keeps this helper bounded to paired business acceptance rather than
    the subsequent full-E2E persistence/finalization phase.
    """

    runtime = _build_initial_runtime(engine, workflow, graph_state)
    graph = engine.build_graph(workflow)
    final_runtime = graph.invoke(
        runtime,
        config=engine._invoke_config(workflow, run_id=graph_state.run_id),
        interrupt_after=[DOCUMENT_GATE_NODE],
    )

    final_graph_state = engine.runtime_state_codec.restore_graph_state(
        final_runtime,
        workflow=workflow,
    )
    engine.runtime_state_codec.project_onto(graph_state, final_graph_state)
    payload = engine.business_state_codec.restore_payload(final_runtime)

    executed = tuple(payload.executed_nodes)
    if not executed or executed[-1] != DOCUMENT_GATE_NODE:
        if (
            payload.failed_business_node is not None
            or payload.terminal_error is not None
            or payload.artifact_route == "technical_failure"
            or (executed and executed[-1] == "finalize_workflow_failure")
        ):
            raise PairedProductionGraphFailure(payload)
        raise RuntimeError(
            "production graph did not stop immediately after evaluate_document_gate"
        )
    missing_prefix = [
        node
        for node in PRODUCTION_PREFIX_THROUGH_DOCUMENT_GATE
        if node not in executed
    ]
    if missing_prefix:
        raise RuntimeError(
            "production graph skipped required paired-acceptance nodes: "
            + ", ".join(missing_prefix)
        )

    inspection = engine.inspect(workflow, run_id=graph_state.run_id)
    next_nodes = tuple(inspection.get("next_nodes") or ())
    if not next_nodes:
        raise RuntimeError(
            "document-gate interrupt did not preserve a downstream graph cursor"
        )
    if any(
        node not in {"persist_deliverable", "persist_rejected"}
        for node in next_nodes
    ):
        raise RuntimeError(
            "unexpected node follows paired document-gate boundary: "
            + ", ".join(next_nodes)
        )

    certification = _certify_document_gate(engine, payload)
    return PairedDocumentGateSlice(
        payload=payload,
        final_runtime=dict(final_runtime),
        inspection=dict(inspection),
        certification=certification,
    )


__all__ = [
    "DOCUMENT_GATE_NODE",
    "PRODUCTION_PREFIX_THROUGH_DOCUMENT_GATE",
    "PairedProductionGraphFailure",
    "PairedDocumentGateSlice",
    "invoke_through_document_gate",
]
