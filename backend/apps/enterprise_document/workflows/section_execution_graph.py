"""LangGraph-owned single-section execution lifecycle.

This graph replaces the production ``SectionExecutionCoordinator.execute``
loop.  The old coordinator remains a compatibility implementation, while the
production path owns retrieval, evidence routing, generation retries,
corrective retrieval and finalization as explicit graph nodes.
"""

from __future__ import annotations

import re
from typing import Any

from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.graph import END, START, StateGraph

from agent.runtime.graph_state import GraphStateSchema
from agent.runtime.langgraph_state import (
    LangGraphRuntimeStateCodec,
    WorkflowNamespaceCheckpointSaver,
)
from agent.runtime.workflow_stream_context import emit_workflow_custom_event
from agent.runtime.state_access import SharedStateWriter
from apps.enterprise_document.quality.budget import (
    TaskDrainingBudgetExceeded,
    WorkflowBudget,
    WorkflowBudgetExceeded,
    activate_workflow_budget,
    current_task_draining_budget,
)
from apps.enterprise_document.schemas.agent_rag_v1 import (
    ResearchRecommendedAction,
    RunningInvocationRecoveryInputSchema,
    VerifierAction,
)
from apps.enterprise_document.schemas.section_execution_graph import (
    SectionExecutionGraphPayloadSchema,
    SectionExecutionGraphState,
    SectionExecutionGraphStateCodec,
    SectionGraphExecutionRequestSchema,
)
from apps.enterprise_document.schemas.scheme_writer_schema import (
    SectionEvalSchema,
    SectionEvidenceBundleSchema,
    SectionExecutionRequestSchema,
    SectionExecutionResultSchema,
)
from apps.enterprise_document.services.output_validation import detect_truncation
from schemas.common import ErrorSourceSchema, WarningSchema
from schemas.status import ExecutionStatus

from ..runtime.agent_rag_v1.mainline_recovery import (
    MainlineAttemptGuard,
    MainlineReplanAttempt,
    MainlineRunningInvocationRecovery,
)
from ..services.agent_rag_v1.streaming_writer import (
    CheckpointParagraphPipeline,
)
from ..services.scheme_writer.document_citation_registry import (
    DocumentCitationRegistry,
)
from ..services.scheme_writer.section_execution_coordinator import (
    SectionExecutionCoordinator,
)


SECTION_GRAPH_NODE_NAMES = (
    "prepare_section",
    "retrieve_section_evidence",
    "evaluate_evidence_gate",
    "generate_section",
    "prepare_paragraph_pipeline",
    "persist_paragraph",
    "precheck_paragraph",
    "evaluate_generation",
    "retrieve_more",
    "corrective_retrieval",
    "finalize_section",
)


class SectionExecutionGraph:
    """Execute one section through an explicit checkpoint-safe subgraph."""

    def __init__(
        self,
        *,
        coordinator: SectionExecutionCoordinator,
        checkpointer: BaseCheckpointSaver | None = None,
    ) -> None:
        self.coordinator = coordinator
        self.evidence_service = coordinator.evidence_service
        self.query_builder = coordinator.query_builder
        self.section_generation_service = coordinator.section_generation_service
        self.paragraph_pipeline = CheckpointParagraphPipeline()
        self.attempt_guard = MainlineAttemptGuard()
        self.running_invocation_recovery = MainlineRunningInvocationRecovery()
        self.runtime_support = coordinator.runtime_support
        self.generation_quality_metadata = dict(
            coordinator.generation_quality_metadata or {}
        )
        self.section_agent_adapter = getattr(
            coordinator,
            "section_agent_adapter",
            None,
        )
        self.codec = SectionExecutionGraphStateCodec()
        self.graph_state_codec = LangGraphRuntimeStateCodec()
        self.state_writer = SharedStateWriter()
        self.checkpointer = checkpointer
        self._langgraph_checkpointer = (
            WorkflowNamespaceCheckpointSaver(checkpointer)
            if checkpointer is not None
            and not isinstance(checkpointer, WorkflowNamespaceCheckpointSaver)
            else checkpointer
        )

    @staticmethod
    def namespace(
        *,
        workflow_namespace: str,
        section_id: str,
    ) -> str:
        return f"{workflow_namespace}/sections/{section_id}"

    def checkpoint_config(
        self,
        *,
        workflow_namespace: str,
        run_id: str,
        section_id: str,
    ) -> dict[str, Any]:
        namespace = self.namespace(
            workflow_namespace=workflow_namespace,
            section_id=section_id,
        )
        return {
            "configurable": {
                "thread_id": run_id,
                "checkpoint_ns": namespace,
                "workflow_namespace": namespace,
            },
            "metadata": {
                "workflow_namespace": workflow_namespace,
                "section_id": section_id,
                "checkpoint_namespace": namespace,
                "graph_kind": "section_execution_subgraph",
            },
            "recursion_limit": 128,
        }

    def _restore(
        self,
        state: SectionExecutionGraphState,
    ) -> tuple[SectionExecutionGraphPayloadSchema, GraphStateSchema]:
        payload = self.codec.restore_payload(state)
        shared_state = self.codec.restore_graph_state(payload)
        return payload, shared_state

    def _update(
        self,
        payload: SectionExecutionGraphPayloadSchema,
        *,
        node_name: str,
        route: str,
        shared_state: GraphStateSchema | None = None,
        completed: bool | None = None,
    ) -> dict[str, Any]:
        updates: dict[str, Any] = {
            "current_node": node_name,
            "route": route,
            "executed_nodes": [*payload.executed_nodes, node_name],
            "transition_count": payload.transition_count + 1,
        }
        if shared_state is not None:
            updates["shared_state_payload"] = self.codec.dump_graph_state(shared_state)
        if completed is not None:
            updates["completed"] = completed
        updated = payload.model_copy(update=updates, deep=True)
        return {
            "section_payload": self.codec.dump_payload(updated),
            "current_section_node": node_name,
            "section_route": route,
        }

    @staticmethod
    def _route(state: SectionExecutionGraphState) -> str:
        return str(state.get("section_route") or "finalize")

    @staticmethod
    def _assessment(normalized: dict[str, Any]) -> dict[str, Any]:
        contract = normalized.get("evidence")
        contract = contract if isinstance(contract, dict) else {}
        assessment = contract.get("assessment")
        assessment = dict(assessment) if isinstance(assessment, dict) else {}
        raw_status = assessment.get("status") or "not_assessed"
        if hasattr(raw_status, "value"):
            raw_status = raw_status.value
        assessment["status"] = str(raw_status).strip().lower() or "not_assessed"
        details = assessment.get("details")
        assessment["details"] = dict(details) if isinstance(details, dict) else {}
        return assessment

    @staticmethod
    def _evidence_contract_sha(
        payload: SectionExecutionGraphPayloadSchema,
    ) -> str | None:
        context = payload.active_context or payload.document_rag_context
        return str(
            (context.extra or {}).get("evidence_contract_sha256") or ""
        ) or None

    @staticmethod
    def _active_evidence_ids(
        payload: SectionExecutionGraphPayloadSchema,
    ) -> list[str]:
        citation_ids = [
            str(item.citation_id).strip()
            for item in payload.active_citations
            if str(item.citation_id).strip()
        ]
        if citation_ids:
            return list(dict.fromkeys(citation_ids))
        chunk_ids = [
            str(getattr(item, "chunk_id", "") or "").strip()
            for item in payload.active_chunks
            if str(getattr(item, "chunk_id", "") or "").strip()
        ]
        return list(dict.fromkeys(chunk_ids))

    @staticmethod
    def _append_agent_payload(
        existing: list,
        item,
        *,
        metadata_field: str,
    ) -> list:
        metadata = dict(getattr(item, metadata_field, {}) or {})
        invocation_id = str(metadata.get("invocation_id") or "").strip()
        if not invocation_id:
            return [*existing, item]
        retained = [
            value
            for value in existing
            if str(
                dict(getattr(value, metadata_field, {}) or {}).get(
                    "invocation_id"
                )
                or ""
            ).strip()
            != invocation_id
        ]
        return [*retained, item]

    def _validate_agent_result_identity(
        self,
        payload: SectionExecutionGraphPayloadSchema,
        *,
        section_state,
        metadata: dict[str, Any],
    ) -> dict[str, Any]:
        """Reject stale Agent output before it can mutate the Section payload."""

        section_id = str(payload.section_id or payload.section_plan.section_id)
        return self.attempt_guard.validate_result_metadata(
            run_id=payload.run_id,
            section_id=section_id,
            section_state=section_state,
            project_input=payload.project_input,
            section_plan=payload.section_plan,
            metadata=metadata,
        )

    def _invoke_research_agent(
        self,
        payload: SectionExecutionGraphPayloadSchema,
        *,
        node_name: str,
        research_mode: str,
        retrieval_succeeded: bool,
        retrieval_scope: str | None = None,
        shared_state: GraphStateSchema | None = None,
        query: str = "",
        call_suffix: str = "",
        citation_registry: list | None = None,
    ) -> SectionExecutionGraphPayloadSchema:
        adapter = self.section_agent_adapter
        if adapter is None:
            return payload
        section_state = payload.section_state
        if section_state is None or payload.section_id is None:
            raise ValueError("ResearchAgent requires initialized section state")
        outcome = adapter.research(
            node_name=node_name,
            task_id=payload.task_id,
            run_id=payload.run_id,
            project_input=payload.project_input,
            section_plan=payload.section_plan,
            section_state=section_state,
            section_id=payload.section_id,
            section_title=str(payload.section_title or payload.section_plan.section_title),
            retrieval_scope=str(retrieval_scope or payload.active_scope),
            research_mode=research_mode,
            research_round=max(1, payload.recovery_count + 1),
            citation_required=payload.citation_required,
            assessment=dict(payload.active_assessment),
            accepted_evidence_ids=self._active_evidence_ids(payload),
            rejected_evidence_ids=[],
            evidence_contract_sha256=self._evidence_contract_sha(payload),
            tool_call_ids=list(payload.tool_call_ids),
            retrieval_succeeded=retrieval_succeeded,
            prior_evidence_package_ref=(
                section_state.research.evidence_package_ref
            ),
            budget=dict(payload.budget_state),
            shared_state=shared_state,
            query=query,
            call_suffix=call_suffix,
            citation_registry=list(citation_registry or []),
        )
        identity_audit = self._validate_agent_result_identity(
            payload,
            section_state=section_state,
            metadata=dict(outcome.payload.agent_metadata),
        )
        metadata = dict(payload.retrieval_metadata)
        metadata.update(
            {
                "research_agent_identity": identity_audit,
                "research_agent_action": (
                    outcome.payload.evidence_summary.recommended_action.value
                ),
                "research_agent_status": (
                    outcome.payload.evidence_summary.research_status.value
                ),
                "research_agent_invocation_id": (
                    outcome.payload.agent_metadata.get("invocation_id")
                ),
                "research_agent_fake_reasoner": bool(
                    outcome.payload.agent_metadata.get("fake_reasoner")
                ),
                "research_agent_tool_gateway": (
                    outcome.payload.agent_metadata.get("tool_gateway")
                ),
            }
        )
        updates: dict[str, Any] = {
            "section_state": outcome.section_state,
            "research_agent_payloads": self._append_agent_payload(
                list(payload.research_agent_payloads),
                outcome.payload,
                metadata_field="agent_metadata",
            ),
            "retrieval_metadata": metadata,
        }
        retrieval = outcome.retrieval
        if retrieval is not None:
            writer_gate = self.coordinator._writer_gate_binding(
                dict(retrieval.normalized),
                section_id=str(payload.section_id),
                section_title=str(
                    payload.section_title or payload.section_plan.section_title
                ),
                section_order=(
                    int(payload.section_order)
                    if payload.section_order is not None
                    else None
                ),
                document_id=payload.document_id,
                run_id=payload.run_id,
            )
            tool_call_ids = list(payload.tool_call_ids)
            if retrieval.retrieval_trace_id:
                tool_call_ids.append(retrieval.retrieval_trace_id)
            metadata.update(
                {
                    "research_tool_retrieval_success": retrieval.success,
                    "research_tool_error": retrieval.error_message,
                    "evidence_assessment_status": retrieval.assessment.get(
                        "status", "not_assessed"
                    ),
                    "evidence_assessment": dict(retrieval.assessment),
                    "writer_gate_bound": bool(writer_gate is not None),
                    "writer_gate_binding": (
                        writer_gate.model_dump(mode="json", exclude_none=True)
                        if writer_gate is not None
                        else None
                    ),
                }
            )
            updates.update(
                {
                    "tool_call_ids": list(dict.fromkeys(tool_call_ids)),
                    "active_assessment": dict(retrieval.assessment),
                    "active_writer_gate": writer_gate,
                    "retrieval_metadata": metadata,
                }
            )
            if retrieval.success:
                updates.update(
                    {
                        "citation_registry": list(retrieval.citation_registry),
                        "retrieved_chunks": [
                            *payload.retrieved_chunks,
                            *retrieval.chunks,
                        ],
                        "rag_outputs": [
                            *payload.rag_outputs,
                            dict(retrieval.normalized),
                        ],
                    }
                )
                if (
                    retrieval.context is not None
                    and retrieval.context.context_text.strip()
                ):
                    updates.update(
                        {
                            "active_context": retrieval.context,
                            "active_chunks": list(retrieval.chunks),
                            "active_citations": list(retrieval.citations),
                            "active_query": query,
                            "active_scope": str(
                                retrieval_scope or payload.active_scope
                            ),
                        }
                    )
        return payload.model_copy(update=updates, deep=True)

    def _invoke_writer_agent(
        self,
        payload: SectionExecutionGraphPayloadSchema,
        *,
        node_name: str,
        section,
    ) -> SectionExecutionGraphPayloadSchema:
        adapter = self.section_agent_adapter
        if adapter is None:
            return payload
        section_state = payload.section_state
        if section_state is None:
            raise ValueError("WriterAgent requires initialized section state")
        evidence_ref = (
            section_state.research.evidence_package_ref
            or f"state://section-evidence/{section.section_id}/{payload.active_scope}"
        )
        outcome = adapter.write(
            node_name=node_name,
            task_id=payload.task_id,
            run_id=payload.run_id,
            project_input=payload.project_input,
            section_plan=payload.section_plan,
            section_state=section_state,
            section=section,
            evidence_package_ref=evidence_ref,
            generation_attempt=payload.generation_attempt,
            citation_required=payload.citation_required,
            generation_check=dict(payload.generation_check),
            budget=dict(payload.budget_state),
        )
        identity_audit = self._validate_agent_result_identity(
            payload,
            section_state=section_state,
            metadata=dict(outcome.payload.writer_metadata),
        )
        projected_payload = self.paragraph_pipeline.writer_payload(
            section=section,
            artifacts=list(payload.paragraph_artifacts),
            draft_version=max(1, payload.generation_attempt),
            blocked_paragraph_id=payload.paragraph_blocked_id,
        )
        projected_payload = projected_payload.model_copy(
            update={
                "writer_metadata": {
                    **dict(outcome.payload.writer_metadata),
                    **dict(projected_payload.writer_metadata),
                    "agent_generation_complete": (
                        outcome.payload.generation_complete
                    ),
                    "fixed_projector": "round6_paragraph_checkpoint_projection",
                }
            },
            deep=True,
        )
        metadata = dict(payload.retrieval_metadata)
        metadata.update(
            {
                "writer_agent_identity": identity_audit,
                "writer_agent_status": (
                    projected_payload.writer_metadata.get("agent_status")
                ),
                "writer_agent_invocation_id": (
                    projected_payload.writer_metadata.get("invocation_id")
                ),
                "writer_agent_fake_reasoner": bool(
                    projected_payload.writer_metadata.get("fake_reasoner")
                ),
                "writer_agent_model_call_id": (
                    projected_payload.writer_metadata.get("model_call_id")
                ),
                "paragraph_pipeline_attached": True,
                "paragraph_artifact_count": len(payload.paragraph_artifacts),
            }
        )
        return payload.model_copy(
            update={
                "section_state": outcome.section_state,
                "writer_agent_payloads": self._append_agent_payload(
                    list(payload.writer_agent_payloads),
                    projected_payload,
                    metadata_field="writer_metadata",
                ),
                "retrieval_metadata": metadata,
                "budget_state": dict(outcome.budget_state),
            },
            deep=True,
        )

    def _invoke_verifier_agent(
        self,
        payload: SectionExecutionGraphPayloadSchema,
        *,
        node_name: str,
        section,
    ) -> SectionExecutionGraphPayloadSchema:
        adapter = self.section_agent_adapter
        if adapter is None:
            return payload
        section_state = payload.section_state
        if section_state is None:
            raise ValueError("VerifierAgent requires initialized section state")
        outcome = adapter.verify(
            node_name=node_name,
            task_id=payload.task_id,
            run_id=payload.run_id,
            project_input=payload.project_input,
            section_plan=payload.section_plan,
            section_state=section_state,
            section=section,
            generation_attempt=payload.generation_attempt,
            generation_check=dict(payload.generation_check),
            citation_required=payload.citation_required,
            evidence_blocked=payload.evidence_blocked,
            corrective_retrieval_enabled=payload.corrective_retrieval_enabled,
            budget=dict(payload.budget_state),
        )
        identity_audit = self._validate_agent_result_identity(
            payload,
            section_state=section_state,
            metadata=dict(outcome.payload.decision_metadata),
        )
        metadata = dict(payload.retrieval_metadata)
        metadata.update(
            {
                "verifier_agent_identity": identity_audit,
                "verifier_agent_action": outcome.payload.action.value,
                "verifier_agent_retrieval_route": (
                    outcome.payload.decision_metadata.get("retrieval_route")
                ),
                "verifier_agent_invocation_id": (
                    outcome.payload.decision_metadata.get("invocation_id")
                ),
                "verifier_agent_fake_reasoner": bool(
                    outcome.payload.decision_metadata.get("fake_reasoner")
                ),
                "verifier_agent_model_call_id": (
                    outcome.payload.decision_metadata.get("model_call_id")
                ),
            }
        )
        return payload.model_copy(
            update={
                "section_state": outcome.section_state,
                "verifier_agent_payloads": self._append_agent_payload(
                    list(payload.verifier_agent_payloads),
                    outcome.payload,
                    metadata_field="decision_metadata",
                ),
                "retrieval_metadata": metadata,
                "budget_state": dict(outcome.budget_state),
            },
            deep=True,
        )

    def _request(
        self,
        payload: SectionExecutionGraphPayloadSchema,
        shared_state: GraphStateSchema,
    ) -> SectionExecutionRequestSchema:
        return SectionExecutionRequestSchema(
            shared_state=shared_state,
            document_id=payload.document_id,
            project_input=payload.project_input,
            section_plan=payload.section_plan,
            structured_facts=list(payload.structured_facts),
            previous_sections=list(payload.previous_sections),
            document_rag_context=payload.document_rag_context,
            document_retrieved_chunks=list(payload.document_retrieved_chunks),
            document_citations=list(payload.document_citations),
            document_rag_output=dict(payload.document_rag_output),
            document_evidence_assessment=dict(
                payload.document_evidence_assessment
            ),
            document_tool_call_ids=list(payload.document_tool_call_ids),
            section_retrieval_enabled=payload.section_retrieval_enabled,
            corrective_retrieval_enabled=payload.corrective_retrieval_enabled,
            parallel_isolated=payload.parallel_isolated,
            budget_policy_override=dict(payload.budget_policy_override),
        )

    def _prepare_section(
        self,
        state: SectionExecutionGraphState,
    ) -> dict[str, Any]:
        payload, shared_state = self._restore(state)
        plan = payload.section_plan
        section_id = f"section_{payload.run_id}_{plan.section_order:03d}"
        required = list(
            payload.project_input.generation_requirements.required_sections
            or payload.project_input.output_schema.required_sections
        )
        citation_required_titles = set(
            payload.project_input.generation_requirements.citation_required_sections
        )
        citation_required = bool(
            plan.citation_required or plan.section_title in citation_required_titles
        )
        if not payload.parallel_isolated:
            self.state_writer.set_current_section(
                shared_state,
                section_id=section_id,
                section_title=plan.section_title,
            )
        registry = DocumentCitationRegistry.from_citations(
            payload.citation_registry or payload.document_citations
        )
        budget = WorkflowBudget.from_policy_metadata(
            {
                **self.generation_quality_metadata,
                **dict(payload.budget_policy_override),
            }
        )
        task_budget = current_task_draining_budget()
        task_budget_snapshot = task_budget.snapshot() if task_budget else {}
        document_writer_gate = self.coordinator._writer_gate_binding(
            dict(payload.document_rag_output),
            section_id=section_id,
            section_title=plan.section_title,
            section_order=plan.section_order,
            document_id=payload.document_id,
            run_id=payload.run_id,
        )
        updated = payload.model_copy(
            update={
                "section_id": section_id,
                "section_title": plan.section_title,
                "section_order": plan.section_order,
                "total_sections": len(required),
                "citation_required": citation_required,
                "citation_registry": registry.all(),
                "active_context": payload.document_rag_context,
                "active_chunks": list(payload.document_retrieved_chunks),
                "active_citations": list(payload.document_citations),
                "active_query": payload.project_input.user_query,
                "active_scope": "document",
                "active_assessment": dict(
                    payload.document_evidence_assessment
                ),
                "active_writer_gate": document_writer_gate,
                "tool_call_ids": list(payload.document_tool_call_ids),
                "retrieval_metadata": {
                    "section_retrieval_enabled": (
                        payload.section_retrieval_enabled
                    ),
                    "citation_required": citation_required,
                    "evidence_assessment_status": (
                        payload.document_evidence_assessment.get(
                            "status", "not_assessed"
                        )
                    ),
                    "writer_gate_bound": bool(document_writer_gate is not None),
                    "writer_gate_binding": (
                        document_writer_gate.model_dump(
                            mode="json", exclude_none=True
                        )
                        if document_writer_gate is not None
                        else None
                    ),
                },
                "budget_state": {
                    **budget.snapshot(),
                    "task_budget": task_budget_snapshot,
                    "task_draining": bool(
                        task_budget_snapshot.get("draining", False)
                    ),
                },
            },
            deep=True,
        )
        print(
            f"[Section {plan.section_order}/{len(required)}] START "
            f"{plan.section_title}",
            flush=True,
        )
        emit_workflow_custom_event(
            "section_started",
            node_name="prepare_section",
            section_id=section_id,
            status=ExecutionStatus.RUNNING.value,
            progress=(
                plan.section_order / len(required)
                if required
                else None
            ),
            payload={
                "section_title": plan.section_title,
                "section_order": plan.section_order,
                "total_sections": len(required),
            },
        )
        route = (
            "finalize"
            if payload.budget_exhaustion_resource is not None
            else "retrieve"
            if payload.section_retrieval_enabled and citation_required
            else "evidence_gate"
        )
        return self._update(
            updated,
            node_name="prepare_section",
            route=route,
            shared_state=shared_state,
        )

    def _retrieve_section_evidence(
        self,
        state: SectionExecutionGraphState,
    ) -> dict[str, Any]:
        payload, shared_state = self._restore(state)
        title = str(payload.section_title or payload.section_plan.section_title)
        order = int(payload.section_order or payload.section_plan.section_order)
        section_id = str(payload.section_id)
        query = self.query_builder.build(
            payload.project_input,
            title,
            recovery=False,
        )
        print(
            f"[SectionRAG] START section={title} scope=section "
            f"query={query[:100]!r}",
            flush=True,
        )
        emit_workflow_custom_event(
            "retrieval_started",
            node_name="retrieve_section_evidence",
            section_id=section_id,
            payload={"scope": "section", "query": query},
        )
        if (
            self.section_agent_adapter is not None
            and self.section_agent_adapter.uses_research_tool_gateway
        ):
            updated = self._invoke_research_agent(
                payload,
                node_name="retrieve_section_evidence",
                research_mode="initial",
                retrieval_succeeded=False,
                retrieval_scope="section",
                shared_state=shared_state,
                query=query,
                call_suffix=f"section_{order:03d}",
                citation_registry=list(payload.citation_registry),
            )
            success = bool(
                updated.retrieval_metadata.get(
                    "research_tool_retrieval_success"
                )
            )
            metadata = dict(updated.retrieval_metadata)
            metadata.update(
                {
                    "section_retrieval_success": success,
                    "section_citation_count": len(updated.active_citations),
                }
            )
            updated = updated.model_copy(
                update={"retrieval_metadata": metadata},
                deep=True,
            )
            emit_workflow_custom_event(
                "retrieval_completed",
                node_name="retrieve_section_evidence",
                section_id=section_id,
                status=(
                    ExecutionStatus.SUCCESS.value
                    if success
                    else ExecutionStatus.FAILED.value
                ),
                payload={"scope": "section", "query": query},
            )
            print(
                f"[SectionRAG] END   section={title} success={success} "
                f"chunks={len(updated.active_chunks)} "
                f"citations={len(updated.active_citations)}",
                flush=True,
            )
            return self._update(
                updated,
                node_name="retrieve_section_evidence",
                route="evidence_gate",
                shared_state=shared_state,
            )
        result = self.evidence_service.retrieve(
            shared_state,
            payload.project_input,
            query=query,
            scope="section",
            section_id=section_id,
            section_title=title,
            call_suffix=f"section_{order:03d}",
        )
        emit_workflow_custom_event(
            "retrieval_completed",
            node_name="retrieve_section_evidence",
            section_id=section_id,
            status=(
                ExecutionStatus.SUCCESS.value
                if result is not None and result.success
                else ExecutionStatus.FAILED.value
            ),
            payload={"scope": "section", "query": query},
        )
        tool_call_ids = list(payload.tool_call_ids)
        metadata = dict(payload.retrieval_metadata)
        registry = DocumentCitationRegistry.from_citations(
            payload.citation_registry
        )
        updates: dict[str, Any] = {}
        if result is not None:
            tool_call_ids.append(result.retrieval_trace_id)
        if result is not None and result.success:
            context, chunks, citations, normalized = (
                self.evidence_service.extract_rag_output(shared_state, result)
            )
            context, chunks, citations, normalized = registry.remap_bundle(
                context=context,
                chunks=chunks,
                citations=citations,
                normalized=normalized,
                scope="section",
                query=query,
            )
            assessment = self._assessment(normalized)
            writer_gate = self.coordinator._writer_gate_binding(
                normalized,
                section_id=section_id,
                section_title=title,
                section_order=int(payload.section_order or 0) or None,
                document_id=payload.document_id,
                run_id=payload.run_id,
            )
            metadata.update(
                {
                    "section_retrieval_success": True,
                    "section_citation_count": len(citations),
                    "evidence_assessment_status": assessment.get(
                        "status", "not_assessed"
                    ),
                    "evidence_assessment": assessment,
                    "writer_gate_bound": bool(writer_gate is not None),
                    "writer_gate_binding": (
                        writer_gate.model_dump(mode="json", exclude_none=True)
                        if writer_gate is not None
                        else None
                    ),
                }
            )
            updates.update(
                {
                    "citation_registry": registry.all(),
                    "retrieved_chunks": [*payload.retrieved_chunks, *chunks],
                    "rag_outputs": [*payload.rag_outputs, normalized],
                    "active_assessment": assessment,
                    "active_writer_gate": writer_gate,
                }
            )
            if context.context_text.strip():
                updates.update(
                    {
                        "active_context": context,
                        "active_chunks": list(chunks),
                        "active_citations": list(citations),
                        "active_query": query,
                        "active_scope": "section",
                    }
                )
            print(
                f"[SectionRAG] END   section={title} success=True "
                f"chunks={len(chunks)} citations={len(citations)}",
                flush=True,
            )
        else:
            metadata.update(
                {
                    "section_retrieval_success": False,
                    "section_retrieval_error": (
                        result.error_message if result else "not_configured"
                    ),
                }
            )
            print(
                f"[SectionRAG] END   section={title} success=False "
                "fallback=document_evidence",
                flush=True,
            )
        updates.update(
            {
                "tool_call_ids": list(dict.fromkeys(tool_call_ids)),
                "retrieval_metadata": metadata,
            }
        )
        updated = payload.model_copy(update=updates, deep=True)
        updated = self._invoke_research_agent(
            updated,
            node_name="retrieve_section_evidence",
            research_mode="initial",
            retrieval_succeeded=bool(result is not None and result.success),
        )
        return self._update(
            updated,
            node_name="retrieve_section_evidence",
            route="evidence_gate",
            shared_state=shared_state,
        )

    def _evaluate_evidence_gate(
        self,
        state: SectionExecutionGraphState,
    ) -> dict[str, Any]:
        payload, shared_state = self._restore(state)
        if self.section_agent_adapter is not None and not payload.research_agent_payloads:
            payload = self._invoke_research_agent(
                payload,
                node_name="evaluate_evidence_gate",
                research_mode="document_context",
                retrieval_succeeded=True,
            )
        research_action = (
            payload.research_agent_payloads[-1]
            .evidence_summary.recommended_action
            if payload.research_agent_payloads
            else None
        )
        writer_gate = payload.active_writer_gate
        writer_enforcement = (
            self.coordinator.resolve_writer_gate_enforcement(writer_gate)
        )
        research_enforcement = (
            self.coordinator.gate_enforcement_controller.resolve(
                gate_passed=(
                    research_action is not ResearchRecommendedAction.HUMAN_REVIEW
                )
            )
        )
        blocked = bool(
            writer_enforcement.should_block or research_enforcement.should_block
        )
        metadata = dict(payload.retrieval_metadata)
        metadata["gate_enforcement"] = {
            "writer_gate": writer_enforcement.as_metadata(gate_id="G06/G11"),
            "research_review": research_enforcement.as_metadata(
                gate_id="research_human_review"
            ),
        }
        updates: dict[str, Any] = {
            "evidence_blocked": blocked,
            "retrieval_metadata": metadata,
        }
        route = "generate"
        if blocked:
            title = str(payload.section_title)
            block_reason = (
                "writer_gate_missing"
                if writer_gate is None
                else (
                    "legacy_writer_artifact_incomplete"
                    if writer_gate.legacy_artifact_incomplete
                    else (
                        "writer_human_review_required"
                        if writer_gate.gate_decision.human_review_required
                        else "writer_generation_blocked"
                    )
                )
            )
            metadata.update(
                {
                    "normal_generation_blocked": True,
                    "generation_block_reason": block_reason,
                }
            )
            if writer_gate is not None:
                section = self.section_generation_service.build_gate_blocked_section(
                    shared_state,
                    project_input=payload.project_input,
                    section_title=title,
                    section_order=int(payload.section_order or 0),
                    rag_context=payload.active_context or payload.document_rag_context,
                    citations=list(payload.active_citations),
                    writer_gate_binding=writer_gate,
                )
            else:
                section = self.section_generation_service.build_insufficient_evidence_section(
                    shared_state,
                    project_input=payload.project_input,
                    section_title=title,
                    section_order=int(payload.section_order or 0),
                    rag_context=payload.active_context or payload.document_rag_context,
                    citations=list(payload.active_citations),
                    assessment={
                        **dict(payload.active_assessment),
                        "status": "not_assessed",
                        "reason": "final writer gate binding is missing",
                    },
                )
                section.extra = {
                    **dict(section.extra or {}),
                    "generation_block_reason": block_reason,
                    "writer_execution_status": "not_assessed",
                    "formal_deliverable_eligible": False,
                }
            updates.update(
                {
                    "section": section,
                    "generation_check": {},
                    "retrieval_metadata": metadata,
                }
            )
            route = "finalize"
            print(
                f"[WriterGate] BLOCK section={title} reason={block_reason}",
                flush=True,
            )
        updated = payload.model_copy(update=updates, deep=True)
        return self._update(
            updated,
            node_name="evaluate_evidence_gate",
            route=route,
            shared_state=shared_state,
        )

    def _generate_section(
        self,
        state: SectionExecutionGraphState,
    ) -> dict[str, Any]:
        payload, shared_state = self._restore(state)
        budget = WorkflowBudget.from_snapshot(payload.budget_state)
        request = self._request(payload, shared_state)
        try:
            with activate_workflow_budget(budget):
                section = self.section_generation_service.generate_section(
                    shared_state,
                    document_id=payload.document_id,
                    project_input=payload.project_input,
                    section_title=str(payload.section_title),
                    section_order=int(payload.section_order or 0),
                    rag_context=payload.active_context or payload.document_rag_context,
                    citations=list(payload.active_citations),
                    structured_facts=list(payload.structured_facts),
                    previous_sections=list(payload.previous_sections),
                    generation_attempt=payload.generation_attempt,
                    writer_gate_binding=payload.active_writer_gate,
                )
            exhaustion_resource = None
            exhaustion_limit = None
        except WorkflowBudgetExceeded as exc:
            section = self.coordinator._budget_fallback_section(
                request,
                section_id=str(payload.section_id),
                section_title=str(payload.section_title),
                section_order=int(payload.section_order or 0),
                fallback_section=payload.section,
            )
            exhaustion_resource = exc.resource
            exhaustion_limit = exc.limit
        generation_check = dict((section.extra or {}).get("generation_check") or {})
        emit_workflow_custom_event(
            "section_generated",
            node_name="generate_section",
            section_id=str(payload.section_id),
            status=section.status.value,
            payload={
                "section_title": payload.section_title,
                "section_order": payload.section_order,
                "generation_attempt": payload.generation_attempt,
            },
        )
        updated = payload.model_copy(
            update={
                "shared_state_payload": self.codec.dump_graph_state(shared_state),
                "section": section,
                "generation_check": generation_check,
                "budget_state": budget.snapshot(),
                "budget_exhaustion_resource": exhaustion_resource,
                "budget_exhaustion_limit": exhaustion_limit,
            },
            deep=True,
        )
        return self._update(
            updated,
            node_name="generate_section",
            route="prepare_paragraph_pipeline",
            shared_state=shared_state,
        )

    def _prepare_paragraph_pipeline(
        self,
        state: SectionExecutionGraphState,
    ) -> dict[str, Any]:
        payload, shared_state = self._restore(state)
        section = payload.section
        if section is None:
            raise ValueError("paragraph pipeline requires generated section")
        candidates = self.paragraph_pipeline.plan(
            section_id=section.section_id,
            content=section.content,
            paragraph_version=max(1, payload.generation_attempt),
            prior_paragraphs=list(payload.paragraph_artifacts),
        )
        candidate_ids = {
            str(item.paragraph_id)
            for item in candidates
            if item.paragraph_id is not None
        }
        retained_artifacts = [
            item
            for item in payload.paragraph_artifacts
            if item.paragraph_id in candidate_ids
            and any(
                candidate.paragraph_id == item.paragraph_id
                and candidate.paragraph_version == item.paragraph_version
                and candidate.content.strip() == item.content
                for candidate in candidates
            )
        ]
        retained_ids = {item.paragraph_id for item in retained_artifacts}
        retained_prechecks = [
            item
            for item in payload.paragraph_precheck_results
            if item.paragraph_id in retained_ids
            and any(
                artifact.paragraph_id == item.paragraph_id
                and artifact.paragraph_version == item.paragraph_version
                for artifact in retained_artifacts
            )
        ]
        retained_claims = [
            item
            for item in payload.paragraph_claims
            if item.paragraph_id in retained_ids
        ]
        prechecked_versions = {
            (item.paragraph_id, item.paragraph_version)
            for item in retained_prechecks
        }
        cursor = len(candidates)
        for index, candidate in enumerate(candidates):
            if (
                str(candidate.paragraph_id),
                candidate.paragraph_version,
            ) not in prechecked_versions:
                cursor = index
                break
        updated = payload.model_copy(
            update={
                "paragraph_candidates": candidates,
                "paragraph_artifacts": retained_artifacts,
                "paragraph_precheck_results": retained_prechecks,
                "paragraph_claims": retained_claims,
                "paragraph_cursor": cursor,
                "paragraph_blocked_id": None,
                "paragraph_pipeline_revision": max(
                    payload.paragraph_pipeline_revision,
                    payload.generation_attempt,
                ),
            },
            deep=True,
        )
        route = "persist_paragraph" if cursor < len(candidates) else "evaluate_generation"
        if route == "evaluate_generation":
            updated = self._invoke_writer_agent(
                updated,
                node_name="prepare_paragraph_pipeline",
                section=section,
            )
        emit_workflow_custom_event(
            "paragraph_pipeline_planned",
            node_name="prepare_paragraph_pipeline",
            section_id=section.section_id,
            status="success",
            payload={
                "candidate_count": len(candidates),
                "retained_count": len(retained_artifacts),
                "cursor": cursor,
            },
        )
        return self._update(
            updated,
            node_name="prepare_paragraph_pipeline",
            route=route,
            shared_state=shared_state,
        )

    def _persist_paragraph(
        self,
        state: SectionExecutionGraphState,
    ) -> dict[str, Any]:
        payload, shared_state = self._restore(state)
        if payload.paragraph_cursor >= len(payload.paragraph_candidates):
            raise ValueError("paragraph persist cursor has no candidate")
        candidate = payload.paragraph_candidates[payload.paragraph_cursor]
        artifact = self.paragraph_pipeline.persist(
            task_id=payload.task_id,
            run_id=payload.run_id,
            section_id=str(payload.section_id),
            candidate=candidate,
        )
        artifacts = list(payload.paragraph_artifacts)
        existing_index = next(
            (
                index
                for index, item in enumerate(artifacts)
                if item.paragraph_id == artifact.paragraph_id
            ),
            None,
        )
        changed = False
        if existing_index is None:
            artifacts.append(artifact)
            changed = True
        else:
            existing = artifacts[existing_index]
            if existing.paragraph_version == artifact.paragraph_version:
                self.paragraph_pipeline.assert_idempotent_commit(existing, artifact)
            elif artifact.paragraph_version > existing.paragraph_version:
                artifacts[existing_index] = artifact
                changed = True
            else:
                raise ValueError("paragraph replay attempted an older version")
        artifacts.sort(key=lambda item: item.order)
        prechecks = [
            item
            for item in payload.paragraph_precheck_results
            if not (
                item.paragraph_id == artifact.paragraph_id
                and item.paragraph_version != artifact.paragraph_version
            )
        ]
        updated = payload.model_copy(
            update={
                "paragraph_artifacts": artifacts,
                "paragraph_precheck_results": prechecks,
                "paragraph_pipeline_revision": (
                    payload.paragraph_pipeline_revision + 1
                    if changed
                    else payload.paragraph_pipeline_revision
                ),
            },
            deep=True,
        )
        emit_workflow_custom_event(
            "paragraph_committed",
            node_name="persist_paragraph",
            section_id=str(payload.section_id),
            status="success",
            payload={
                "paragraph_id": artifact.paragraph_id,
                "paragraph_version": artifact.paragraph_version,
                "content_hash": artifact.content_hash,
                "idempotent_replay": not changed,
            },
        )
        return self._update(
            updated,
            node_name="persist_paragraph",
            route="precheck_paragraph",
            shared_state=shared_state,
        )

    def _paragraph_section_snapshot(
        self,
        payload: SectionExecutionGraphPayloadSchema,
        *,
        blocked_paragraph_id: str | None,
    ):
        section = payload.section
        if section is None:
            raise ValueError("paragraph snapshot requires generated section")
        excluded = {blocked_paragraph_id} if blocked_paragraph_id else set()
        content = self.paragraph_pipeline.assemble_content(
            payload.paragraph_artifacts,
            exclude_ids=excluded,
        )
        checks = dict(
            section.eval_result.checks if section.eval_result is not None else {}
        )
        failures = list(
            section.eval_result.failures if section.eval_result is not None else []
        )
        warnings = list(
            section.eval_result.warnings if section.eval_result is not None else []
        )
        checks["paragraph_precheck_passed"] = blocked_paragraph_id is None
        if blocked_paragraph_id is not None:
            failure = f"paragraph_precheck_blocked:{blocked_paragraph_id}"
            if failure not in failures:
                failures.append(failure)
        passed = bool(
            blocked_paragraph_id is None
            and (section.eval_result is None or section.eval_result.passed)
        )
        status = section.status
        if blocked_paragraph_id is not None:
            status = (
                ExecutionStatus.PARTIAL_SUCCESS
                if content
                else ExecutionStatus.FAILED
            )
        return section.model_copy(
            update={
                "content": content,
                "status": status,
                "eval_result": SectionEvalSchema(
                    passed=passed,
                    checks=checks,
                    failures=failures,
                    warnings=warnings,
                    semantic_gate=(
                        section.eval_result.semantic_gate
                        if section.eval_result is not None
                        else None
                    ),
                ),
                "extra": {
                    **dict(section.extra or {}),
                    "paragraph_pipeline": {
                        "artifact_authority": "section_langgraph_checkpoint",
                        "paragraph_count": len(payload.paragraph_artifacts),
                        "precheck_count": len(
                            payload.paragraph_precheck_results
                        ),
                        "claim_count": len(payload.paragraph_claims),
                        "blocked_paragraph_id": blocked_paragraph_id,
                        "pipeline_revision": payload.paragraph_pipeline_revision,
                    },
                },
            },
            deep=True,
        )

    def _precheck_paragraph(
        self,
        state: SectionExecutionGraphState,
    ) -> dict[str, Any]:
        payload, shared_state = self._restore(state)
        if payload.paragraph_cursor >= len(payload.paragraph_candidates):
            raise ValueError("paragraph precheck cursor has no candidate")
        candidate = payload.paragraph_candidates[payload.paragraph_cursor]
        paragraph_id = str(candidate.paragraph_id)
        artifact = next(
            (
                item
                for item in payload.paragraph_artifacts
                if item.paragraph_id == paragraph_id
            ),
            None,
        )
        if artifact is None:
            raise ValueError("paragraph precheck requires committed artifact")
        snapshot_version = str(
            payload.section_state.attempt.input_snapshot_version
            if payload.section_state is not None
            and payload.section_state.attempt.input_snapshot_version
            else f"sha256:{payload.task_id}:{payload.run_id}"
        )
        fact_snapshot = self.paragraph_pipeline.fact_snapshot(
            task_id=payload.task_id,
            run_id=payload.run_id,
            section_id=str(payload.section_id),
            snapshot_version=snapshot_version,
            structured_facts=list(payload.structured_facts),
        )
        existing = next(
            (
                item
                for item in payload.paragraph_precheck_results
                if item.paragraph_id == paragraph_id
                and item.paragraph_version == artifact.paragraph_version
            ),
            None,
        )
        precheck = existing or self.paragraph_pipeline.precheck_paragraph(
            section_id=str(payload.section_id),
            artifact=artifact,
            fact_snapshot=fact_snapshot,
        )
        prechecks = [
            item
            for item in payload.paragraph_precheck_results
            if item.paragraph_id != paragraph_id
        ]
        prechecks.append(precheck)
        prechecks.sort(
            key=lambda item: next(
                artifact_item.order
                for artifact_item in payload.paragraph_artifacts
                if artifact_item.paragraph_id == item.paragraph_id
            )
        )
        claims = self.paragraph_pipeline.update_claims(
            section_id=str(payload.section_id),
            artifacts=list(payload.paragraph_artifacts),
            previous_claims=list(payload.paragraph_claims),
            changed_paragraph_ids={paragraph_id},
            fact_snapshot=fact_snapshot,
        )
        claims_by_id = {item.claim_id: item for item in claims}
        claims = sorted(claims_by_id.values(), key=lambda item: item.claim_id)
        generation_check = dict(payload.generation_check)
        generation_check.update(
            {
                "paragraph_precheck_passed": not precheck.blocking,
                "paragraph_precheck_blocking": precheck.blocking,
                "paragraph_precheck_issue_count": len(precheck.issues),
            }
        )
        updates: dict[str, Any] = {
            "paragraph_precheck_results": prechecks,
            "paragraph_claims": claims,
            "generation_check": generation_check,
            "paragraph_pipeline_revision": (
                payload.paragraph_pipeline_revision
                if existing is not None
                else payload.paragraph_pipeline_revision + 1
            ),
        }
        route = "persist_paragraph"
        if precheck.blocking:
            updates["paragraph_blocked_id"] = paragraph_id
            blocked_payload = payload.model_copy(update=updates, deep=True)
            section = self._paragraph_section_snapshot(
                blocked_payload,
                blocked_paragraph_id=paragraph_id,
            )
            blocked_payload = blocked_payload.model_copy(
                update={"section": section},
                deep=True,
            )
            blocked_payload = self._invoke_writer_agent(
                blocked_payload,
                node_name="precheck_paragraph",
                section=section,
            )
            updated = blocked_payload
            route = "evaluate_generation"
        else:
            cursor = payload.paragraph_cursor + 1
            prechecked_versions = {
                (item.paragraph_id, item.paragraph_version)
                for item in prechecks
            }
            while cursor < len(payload.paragraph_candidates):
                next_candidate = payload.paragraph_candidates[cursor]
                if (
                    str(next_candidate.paragraph_id),
                    next_candidate.paragraph_version,
                ) not in prechecked_versions:
                    break
                cursor += 1
            updates["paragraph_cursor"] = cursor
            updated = payload.model_copy(update=updates, deep=True)
            if cursor >= len(payload.paragraph_candidates):
                section = self._paragraph_section_snapshot(
                    updated,
                    blocked_paragraph_id=None,
                )
                updated = updated.model_copy(update={"section": section}, deep=True)
                updated = self._invoke_writer_agent(
                    updated,
                    node_name="precheck_paragraph",
                    section=section,
                )
                route = "evaluate_generation"
        emit_workflow_custom_event(
            "paragraph_prechecked",
            node_name="precheck_paragraph",
            section_id=str(payload.section_id),
            status="blocked" if precheck.blocking else "success",
            payload={
                "paragraph_id": paragraph_id,
                "paragraph_version": artifact.paragraph_version,
                "blocking": precheck.blocking,
                "claim_count": len(precheck.claims),
                "issue_count": len(precheck.issues),
            },
        )
        return self._update(
            updated,
            node_name="precheck_paragraph",
            route=route,
            shared_state=shared_state,
        )

    def _evaluate_generation(
        self,
        state: SectionExecutionGraphState,
    ) -> dict[str, Any]:
        payload, shared_state = self._restore(state)
        section = payload.section
        if section is None:
            raise ValueError("evaluate_generation requires section")
        payload = self._invoke_verifier_agent(
            payload,
            node_name="evaluate_generation",
            section=section,
        )
        budget = WorkflowBudget.from_snapshot(payload.budget_state)
        route = "finalize"
        updates: dict[str, Any] = {}
        metadata = dict(payload.retrieval_metadata)
        self_rag_rounds = list(payload.self_rag_retrieval_rounds)
        pending_round = (
            dict(payload.pending_self_rag_round)
            if payload.pending_self_rag_round is not None
            else None
        )
        if pending_round is not None:
            pending_round.update(
                {
                    "success": payload.budget_exhaustion_resource is None,
                    "check_after": dict(payload.generation_check),
                    "retrieved_chunk_count": len(payload.active_chunks),
                    "citation_count": len(payload.active_citations),
                }
            )
            if payload.budget_exhaustion_resource is not None:
                pending_round.update(
                    {
                        "error": "workflow_budget_exhausted",
                        "budget_resource": payload.budget_exhaustion_resource,
                        "budget_limit": payload.budget_exhaustion_limit,
                    }
                )
            self_rag_rounds.append(pending_round)
            metadata["self_rag_retrieval_rounds"] = list(self_rag_rounds)
            metadata["self_rag_retrieve_more_success"] = bool(
                pending_round.get("success")
            )
            metadata["self_rag_recheck"] = dict(payload.generation_check)
            updates.update(
                {
                    "self_rag_retrieval_rounds": self_rag_rounds,
                    "pending_self_rag_round": None,
                    "retrieval_metadata": metadata,
                }
            )
        verifier_payload = (
            payload.verifier_agent_payloads[-1]
            if payload.verifier_agent_payloads
            else None
        )
        paragraph_blocking = bool(
            payload.generation_check.get("paragraph_precheck_blocking")
        )
        if paragraph_blocking:
            try:
                budget.consume_rewrite_round()
                route = "generate"
                updates.update(
                    {
                        "generation_attempt": payload.generation_attempt + 1,
                        "budget_state": budget.snapshot(),
                    }
                )
            except WorkflowBudgetExceeded as exc:
                updates.update(
                    {
                        "budget_state": budget.snapshot(),
                        "budget_exhaustion_resource": exc.resource,
                        "budget_exhaustion_limit": exc.limit,
                    }
                )
        elif verifier_payload is not None:
            action = verifier_payload.action
            if action == VerifierAction.RESEARCH_MORE:
                desired_route = str(
                    verifier_payload.decision_metadata.get("retrieval_route")
                    or "retrieve_more"
                )
                if desired_route not in {"retrieve_more", "corrective_retrieval"}:
                    desired_route = "retrieve_more"
                if budget.retrieval_rounds < budget.max_retrieval_rounds:
                    route = desired_route
                else:
                    updates.update(
                        {
                            "budget_exhaustion_resource": "retrieval_rounds",
                            "budget_exhaustion_limit": budget.max_retrieval_rounds,
                        }
                    )
            elif action == VerifierAction.REWRITE:
                try:
                    budget.consume_rewrite_round()
                    route = "generate"
                    updates.update(
                        {
                            "generation_attempt": payload.generation_attempt + 1,
                            "budget_state": budget.snapshot(),
                        }
                    )
                except WorkflowBudgetExceeded as exc:
                    updates.update(
                        {
                            "budget_state": budget.snapshot(),
                            "budget_exhaustion_resource": exc.resource,
                            "budget_exhaustion_limit": exc.limit,
                        }
                    )
            else:
                route = "finalize"
        else:
            need_more = bool(payload.generation_check.get("need_retrieve_more"))
            citation_bound = bool(
                section.eval_result
                and section.eval_result.checks.get("citation_bound", False)
            )
            if need_more:
                if budget.retrieval_rounds < budget.max_retrieval_rounds:
                    route = "retrieve_more"
                else:
                    updates.update(
                        {
                            "budget_exhaustion_resource": "retrieval_rounds",
                            "budget_exhaustion_limit": budget.max_retrieval_rounds,
                        }
                    )
            elif (
                payload.citation_required
                and not payload.evidence_blocked
                and not citation_bound
                and payload.corrective_retrieval_enabled
            ):
                if budget.retrieval_rounds < budget.max_retrieval_rounds:
                    route = "corrective_retrieval"
                else:
                    updates.update(
                        {
                            "budget_exhaustion_resource": "retrieval_rounds",
                            "budget_exhaustion_limit": budget.max_retrieval_rounds,
                        }
                    )
        updated = payload.model_copy(update=updates, deep=True)
        return self._update(
            updated,
            node_name="evaluate_generation",
            route=route,
            shared_state=shared_state,
        )

    def _retrieve_recovery(
        self,
        state: SectionExecutionGraphState,
        *,
        corrective: bool,
    ) -> dict[str, Any]:
        payload, shared_state = self._restore(state)
        budget = WorkflowBudget.from_snapshot(payload.budget_state)
        try:
            budget.consume_retrieval_round()
        except WorkflowBudgetExceeded as exc:
            updated = payload.model_copy(
                update={
                    "budget_state": budget.snapshot(),
                    "budget_exhaustion_resource": exc.resource,
                    "budget_exhaustion_limit": exc.limit,
                },
                deep=True,
            )
            return self._update(
                updated,
                node_name=(
                    "corrective_retrieval" if corrective else "retrieve_more"
                ),
                route="finalize",
                shared_state=shared_state,
            )

        title = str(payload.section_title)
        order = int(payload.section_order or 0)
        recovery_count = payload.recovery_count + 1
        query = self.query_builder.build(
            payload.project_input,
            title,
            recovery=True,
        )
        scope = "recovery" if corrective else "self_rag_recovery"
        label = "CorrectiveSectionRAG" if corrective else "SelfRAGRetrieveMore"
        print(
            f"[{label}] START section={title} query={query[:100]!r}",
            flush=True,
        )
        if (
            self.section_agent_adapter is not None
            and self.section_agent_adapter.uses_research_tool_gateway
        ):
            self_rag_rounds = list(payload.self_rag_retrieval_rounds)
            pending_self_rag_round = (
                {
                    "round": budget.retrieval_rounds,
                    "query": query,
                    "check_before": dict(payload.generation_check),
                    "success": False,
                }
                if not corrective
                else None
            )
            updated = payload.model_copy(
                update={
                    "budget_state": budget.snapshot(),
                    "recovery_count": recovery_count,
                },
                deep=True,
            )
            call_suffix = (
                f"section_{order:03d}_recovery_{recovery_count}"
                if corrective
                else f"section_{order:03d}_self_rag_{recovery_count}"
            )
            updated = self._invoke_research_agent(
                updated,
                node_name=(
                    "corrective_retrieval" if corrective else "retrieve_more"
                ),
                research_mode=(
                    "corrective_retrieval"
                    if corrective
                    else "self_rag_recovery"
                ),
                retrieval_succeeded=False,
                retrieval_scope=scope,
                shared_state=shared_state,
                query=query,
                call_suffix=call_suffix,
                citation_registry=list(payload.citation_registry),
            )
            metadata = dict(updated.retrieval_metadata)
            success = bool(
                metadata.get("research_tool_retrieval_success")
            )
            route = "finalize"
            branch_updates: dict[str, Any] = {}
            assessment = dict(updated.active_assessment)
            writer_gate = updated.active_writer_gate
            writer_enforcement = (
                self.coordinator.resolve_writer_gate_enforcement(writer_gate)
            )
            metadata["writer_gate_enforcement"] = writer_enforcement.as_metadata(
                gate_id="G06/G11"
            )
            if (
                success
                and updated.active_context is not None
                and (
                    updated.active_context.context_text.strip()
                    or (
                        writer_gate is not None
                        and not writer_gate.ordinary_generation_permitted
                    )
                )
            ):
                if writer_enforcement.should_block:
                    block_reason = (
                        "writer_gate_missing_after_research_tool_retrieval"
                        if writer_gate is None
                        else (
                            "legacy_writer_artifact_incomplete_after_research_tool_retrieval"
                            if writer_gate.legacy_artifact_incomplete
                            else (
                                "writer_human_review_required_after_research_tool_retrieval"
                                if writer_gate.gate_decision.human_review_required
                                else "writer_generation_blocked_after_research_tool_retrieval"
                            )
                        )
                    )
                    if writer_gate is not None:
                        section = self.section_generation_service.build_gate_blocked_section(
                            shared_state,
                            project_input=payload.project_input,
                            section_title=title,
                            section_order=order,
                            rag_context=updated.active_context,
                            citations=list(updated.active_citations),
                            writer_gate_binding=writer_gate,
                        )
                    else:
                        section = self.section_generation_service.build_insufficient_evidence_section(
                            shared_state,
                            project_input=payload.project_input,
                            section_title=title,
                            section_order=order,
                            rag_context=updated.active_context,
                            citations=list(updated.active_citations),
                            assessment={
                                **assessment,
                                "status": "not_assessed",
                                "reason": "final writer gate binding is missing",
                            },
                        )
                        section.extra = {
                            **dict(section.extra or {}),
                            "generation_block_reason": block_reason,
                            "writer_execution_status": "not_assessed",
                            "formal_deliverable_eligible": False,
                        }
                    branch_updates.update(
                        {
                            "section": section,
                            "evidence_blocked": True,
                            "generation_check": {},
                        }
                    )
                    if pending_self_rag_round is not None:
                        completed_round = {
                            **pending_self_rag_round,
                            "error": block_reason,
                            "assessment": assessment,
                            "retrieved_chunk_count": len(updated.active_chunks),
                            "citation_count": len(updated.active_citations),
                        }
                        self_rag_rounds.append(completed_round)
                        metadata["self_rag_retrieval_rounds"] = list(
                            self_rag_rounds
                        )
                        metadata["self_rag_retrieve_more_success"] = False
                        branch_updates.update(
                            {
                                "self_rag_retrieval_rounds": self_rag_rounds,
                                "pending_self_rag_round": None,
                            }
                        )
                else:
                    branch_updates["generation_attempt"] = (
                        payload.generation_attempt + 1
                    )
                    if pending_self_rag_round is not None:
                        branch_updates["pending_self_rag_round"] = {
                            **pending_self_rag_round,
                            "retrieved_chunk_count": len(updated.active_chunks),
                            "citation_count": len(updated.active_citations),
                        }
                    route = "generate"
            elif pending_self_rag_round is not None:
                completed_round = {
                    **pending_self_rag_round,
                    "error": str(
                        metadata.get("research_tool_error")
                        or "empty_recovered_context"
                    ),
                    "retrieved_chunk_count": len(updated.active_chunks),
                    "citation_count": len(updated.active_citations),
                }
                self_rag_rounds.append(completed_round)
                metadata["self_rag_retrieval_rounds"] = list(
                    self_rag_rounds
                )
                metadata["self_rag_retrieve_more_success"] = False
                branch_updates.update(
                    {
                        "self_rag_retrieval_rounds": self_rag_rounds,
                        "pending_self_rag_round": None,
                    }
                )
            if corrective:
                metadata["corrective_retrieval_success"] = success
            elif route == "generate":
                metadata["self_rag_retrieve_more_success"] = True
            metadata["evidence_assessment"] = assessment
            branch_updates["retrieval_metadata"] = metadata
            updated = updated.model_copy(update=branch_updates, deep=True)
            if updated.research_agent_payloads:
                action = (
                    updated.research_agent_payloads[-1]
                    .evidence_summary.recommended_action
                )
                if route == "generate" and action != ResearchRecommendedAction.WRITE:
                    route = "finalize"
            print(
                f"[{label}] END   section={title} success={success} "
                f"chunks={len(updated.active_chunks)} "
                f"citations={len(updated.active_citations)}",
                flush=True,
            )
            return self._update(
                updated,
                node_name=(
                    "corrective_retrieval" if corrective else "retrieve_more"
                ),
                route=route,
                shared_state=shared_state,
            )
        result = self.evidence_service.retrieve(
            shared_state,
            payload.project_input,
            query=query,
            scope=scope,
            section_id=str(payload.section_id),
            section_title=title,
            call_suffix=(
                f"section_{order:03d}_recovery_{recovery_count}"
                if corrective
                else f"section_{order:03d}_self_rag_{recovery_count}"
            ),
        )
        registry = DocumentCitationRegistry.from_citations(
            payload.citation_registry
        )
        tool_call_ids = list(payload.tool_call_ids)
        metadata = dict(payload.retrieval_metadata)
        updates: dict[str, Any] = {
            "budget_state": budget.snapshot(),
            "recovery_count": recovery_count,
        }
        self_rag_rounds = list(payload.self_rag_retrieval_rounds)
        pending_self_rag_round = (
            {
                "round": budget.retrieval_rounds,
                "query": query,
                "check_before": dict(payload.generation_check),
                "success": False,
            }
            if not corrective
            else None
        )
        route = "finalize"
        if result is not None:
            tool_call_ids.append(result.retrieval_trace_id)
        if result is not None and result.success:
            context, chunks, citations, normalized = (
                self.evidence_service.extract_rag_output(shared_state, result)
            )
            context, chunks, citations, normalized = registry.remap_bundle(
                context=context,
                chunks=chunks,
                citations=citations,
                normalized=normalized,
                scope=scope,
                query=query,
            )
            assessment = self._assessment(normalized)
            writer_gate = self.coordinator._writer_gate_binding(
                normalized,
                section_id=str(payload.section_id),
                section_title=title,
                section_order=int(payload.section_order or 0) or None,
                document_id=payload.document_id,
                run_id=payload.run_id,
            )
            updates.update(
                {
                    "citation_registry": registry.all(),
                    "retrieved_chunks": [*payload.retrieved_chunks, *chunks],
                    "rag_outputs": [*payload.rag_outputs, normalized],
                    "active_assessment": assessment,
                    "active_writer_gate": writer_gate,
                }
            )
            metadata["writer_gate_bound"] = bool(writer_gate is not None)
            metadata["writer_gate_binding"] = (
                writer_gate.model_dump(mode="json", exclude_none=True)
                if writer_gate is not None
                else None
            )
            writer_enforcement = (
                self.coordinator.resolve_writer_gate_enforcement(writer_gate)
            )
            metadata["writer_gate_enforcement"] = writer_enforcement.as_metadata(
                gate_id="G06/G11"
            )
            if context.context_text.strip() or (
                writer_gate is not None
                and not writer_gate.ordinary_generation_permitted
            ):
                updates.update(
                    {
                        "active_context": context,
                        "active_chunks": list(chunks),
                        "active_citations": list(citations),
                        "active_query": query,
                        "active_scope": scope,
                    }
                )
                if writer_enforcement.should_block:
                    block_reason = (
                        "writer_gate_missing_after_retrieval"
                        if writer_gate is None
                        else (
                            "legacy_writer_artifact_incomplete_after_retrieval"
                            if writer_gate.legacy_artifact_incomplete
                            else (
                                "writer_human_review_required_after_retrieval"
                                if writer_gate.gate_decision.human_review_required
                                else "writer_generation_blocked_after_retrieval"
                            )
                        )
                    )
                    if writer_gate is not None:
                        section = self.section_generation_service.build_gate_blocked_section(
                            shared_state,
                            project_input=payload.project_input,
                            section_title=title,
                            section_order=order,
                            rag_context=context,
                            citations=list(citations),
                            writer_gate_binding=writer_gate,
                        )
                    else:
                        section = self.section_generation_service.build_insufficient_evidence_section(
                            shared_state,
                            project_input=payload.project_input,
                            section_title=title,
                            section_order=order,
                            rag_context=context,
                            citations=list(citations),
                            assessment={
                                **dict(assessment),
                                "status": "not_assessed",
                                "reason": "final writer gate binding is missing",
                            },
                        )
                        section.extra = {
                            **dict(section.extra or {}),
                            "generation_block_reason": block_reason,
                            "writer_execution_status": "not_assessed",
                            "formal_deliverable_eligible": False,
                        }
                    updates.update(
                        {
                            "section": section,
                            "evidence_blocked": True,
                            "generation_check": {},
                        }
                    )
                    if pending_self_rag_round is not None:
                        completed_round = {
                            **pending_self_rag_round,
                            "error": block_reason,
                            "assessment": assessment,
                            "retrieved_chunk_count": len(chunks),
                            "citation_count": len(citations),
                        }
                        self_rag_rounds.append(completed_round)
                        metadata["self_rag_retrieval_rounds"] = list(
                            self_rag_rounds
                        )
                        metadata["self_rag_retrieve_more_success"] = False
                        updates.update(
                            {
                                "self_rag_retrieval_rounds": self_rag_rounds,
                                "pending_self_rag_round": None,
                            }
                        )
                    route = "finalize"
                else:
                    updates["generation_attempt"] = payload.generation_attempt + 1
                    if pending_self_rag_round is not None:
                        updates["pending_self_rag_round"] = {
                            **pending_self_rag_round,
                            "retrieved_chunk_count": len(chunks),
                            "citation_count": len(citations),
                        }
                    route = "generate"
            else:
                if pending_self_rag_round is not None:
                    completed_round = {
                        **pending_self_rag_round,
                        "error": "empty_recovered_context",
                        "retrieved_chunk_count": len(chunks),
                        "citation_count": len(citations),
                    }
                    self_rag_rounds.append(completed_round)
                    metadata["self_rag_retrieval_rounds"] = list(
                        self_rag_rounds
                    )
                    metadata["self_rag_retrieve_more_success"] = False
                    updates.update(
                        {
                            "self_rag_retrieval_rounds": self_rag_rounds,
                            "pending_self_rag_round": None,
                        }
                    )
            if corrective:
                metadata["corrective_retrieval_success"] = True
            elif route == "generate":
                metadata["self_rag_retrieve_more_success"] = True
            metadata["evidence_assessment"] = assessment
            print(
                f"[{label}] END   section={title} success=True "
                f"chunks={len(chunks)} citations={len(citations)}",
                flush=True,
            )
        else:
            metadata[
                "corrective_retrieval_success"
                if corrective
                else "self_rag_retrieve_more_success"
            ] = False
            retrieval_error = result.error_message if result else "not_configured"
            metadata[
                "corrective_retrieval_error"
                if corrective
                else "self_rag_retrieve_more_error"
            ] = retrieval_error
            if pending_self_rag_round is not None:
                completed_round = {
                    **pending_self_rag_round,
                    "error": retrieval_error,
                }
                self_rag_rounds.append(completed_round)
                metadata["self_rag_retrieval_rounds"] = list(self_rag_rounds)
                updates.update(
                    {
                        "self_rag_retrieval_rounds": self_rag_rounds,
                        "pending_self_rag_round": None,
                    }
                )
            print(f"[{label}] END   section={title} success=False", flush=True)
        updates.update(
            {
                "tool_call_ids": list(dict.fromkeys(tool_call_ids)),
                "retrieval_metadata": metadata,
            }
        )
        updated = payload.model_copy(update=updates, deep=True)
        recovery_node = (
            "corrective_retrieval" if corrective else "retrieve_more"
        )
        updated = self._invoke_research_agent(
            updated,
            node_name=recovery_node,
            research_mode=(
                "corrective_retrieval"
                if corrective
                else "self_rag_recovery"
            ),
            retrieval_succeeded=bool(result is not None and result.success),
        )
        if updated.research_agent_payloads:
            action = (
                updated.research_agent_payloads[-1]
                .evidence_summary.recommended_action
            )
            if route == "generate" and action != ResearchRecommendedAction.WRITE:
                route = "finalize"
        return self._update(
            updated,
            node_name=recovery_node,
            route=route,
            shared_state=shared_state,
        )

    def _retrieve_more(
        self,
        state: SectionExecutionGraphState,
    ) -> dict[str, Any]:
        payload, _ = self._restore(state)
        update = self._retrieve_recovery(state, corrective=False)
        emit_workflow_custom_event(
            "section_repaired",
            node_name="retrieve_more",
            section_id=str(payload.section_id),
            payload={"repair_type": "self_rag_retrieval"},
        )
        return update

    def _corrective_retrieval(
        self,
        state: SectionExecutionGraphState,
    ) -> dict[str, Any]:
        payload, _ = self._restore(state)
        update = self._retrieve_recovery(state, corrective=True)
        emit_workflow_custom_event(
            "section_repaired",
            node_name="corrective_retrieval",
            section_id=str(payload.section_id),
            payload={"repair_type": "corrective_retrieval"},
        )
        return update

    def _finalize_section(
        self,
        state: SectionExecutionGraphState,
    ) -> dict[str, Any]:
        payload, shared_state = self._restore(state)
        section = payload.section
        if section is None:
            content = (
                f"“{payload.section_title}”章节未生成有效内容，需人工复核。"
            )
            section = self.section_generation_service.build_insufficient_evidence_section(
                shared_state,
                project_input=payload.project_input,
                section_title=str(payload.section_title),
                section_order=int(payload.section_order or 0),
                rag_context=payload.active_context or payload.document_rag_context,
                citations=list(payload.active_citations),
                assessment={"status": "not_assessed", "reason": content},
            )
        budget = WorkflowBudget.from_snapshot(payload.budget_state)
        if payload.budget_exhaustion_resource is not None:
            section = self.coordinator._mark_budget_exhausted(
                section,
                section_budget=budget,
                exhausted=WorkflowBudgetExceeded(
                    payload.budget_exhaustion_resource,
                    int(payload.budget_exhaustion_limit or 0),
                ),
            )
        active_context = payload.active_context or payload.document_rag_context
        contract_sha = str(
            (active_context.extra or {}).get("evidence_contract_sha256") or ""
        ) or None
        metadata = dict(payload.retrieval_metadata)
        metadata["evidence_assessment_status"] = payload.active_assessment.get(
            "status", "not_assessed"
        )
        metadata["evidence_assessment"] = dict(payload.active_assessment)
        if payload.self_rag_retrieval_rounds:
            metadata["self_rag_retrieval_rounds"] = list(
                payload.self_rag_retrieval_rounds
            )
            metadata["self_rag_retrieve_more_success"] = bool(
                payload.self_rag_retrieval_rounds[-1].get("success")
            )
            metadata["self_rag_recheck"] = dict(payload.generation_check)
        if bool(payload.generation_check.get("need_retrieve_more")):
            metadata["self_rag_retrieve_more_unresolved"] = True
            metadata["self_rag_retrieve_more_budget_exhausted"] = bool(
                budget.retrieval_rounds >= budget.max_retrieval_rounds
            )
        evidence = SectionEvidenceBundleSchema(
            section_id=str(payload.section_id),
            section_title=str(payload.section_title),
            retrieval_scope=payload.active_scope,
            query=payload.active_query,
            tool_call_ids=list(dict.fromkeys(payload.tool_call_ids)),
            rag_context=active_context,
            retrieved_chunks=list(payload.active_chunks),
            citations=list(payload.active_citations),
            recovery_count=payload.recovery_count,
            evidence_contract_sha256=contract_sha,
            writer_gate_binding=(
                payload.active_writer_gate.model_dump(
                    mode="json", exclude_none=True
                )
                if payload.active_writer_gate is not None
                else None
            ),
            generation_allowed=(
                payload.active_writer_gate.ordinary_generation_permitted
                if payload.active_writer_gate is not None
                else False
            ),
            metadata=metadata,
        )
        budget_usage = budget.snapshot()
        section = section.model_copy(deep=True)
        section.extra = {
            **dict(section.extra or {}),
            "evidence_scope": payload.active_scope,
            "evidence_query": payload.active_query,
            "evidence_tool_call_ids": list(dict.fromkeys(payload.tool_call_ids)),
            "evidence_contract_sha256": contract_sha,
            "corrective_retrieval_count": payload.recovery_count,
            "workflow_budget": budget_usage,
            "writer_gate_binding": (
                payload.active_writer_gate.model_dump(
                    mode="json", exclude_none=True
                )
                if payload.active_writer_gate is not None
                else None
            ),
            "writer_execution_status": (
                payload.active_writer_gate.execution_status.value
                if payload.active_writer_gate is not None
                else "not_assessed"
            ),
            "section_subgraph_nodes": [
                *payload.executed_nodes,
                "finalize_section",
            ],
            "section_agent_payload_counts": {
                "research": len(payload.research_agent_payloads),
                "writer": len(payload.writer_agent_payloads),
                "verifier": len(payload.verifier_agent_payloads),
            },
            "section_agent_actions": {
                "research": (
                    payload.research_agent_payloads[-1]
                    .evidence_summary.recommended_action.value
                    if payload.research_agent_payloads
                    else None
                ),
                "verifier": (
                    payload.verifier_agent_payloads[-1].action.value
                    if payload.verifier_agent_payloads
                    else None
                ),
            },
        }
        self.state_writer.add_generated_section(shared_state, section.section_id)
        result = SectionExecutionResultSchema(
            section=section,
            evidence=evidence,
            section_state=payload.section_state,
            retrieved_chunks=list(payload.retrieved_chunks),
            rag_outputs=list(payload.rag_outputs),
            citation_registry=list(payload.citation_registry),
            budget_usage=budget_usage,
            need_human_review=bool(
                (
                    payload.budget_exhaustion_resource is not None
                    and budget.human_review_on_exhaustion
                )
                or (
                    payload.active_writer_gate is not None
                    and payload.active_writer_gate.gate_decision.human_review_required
                )
                or payload.active_writer_gate is None
                or bool(
                    isinstance((section.extra or {}).get("claim_audit"), dict)
                    and not (section.extra or {}).get("claim_audit", {}).get(
                        "passed", False
                    )
                )
            ),
            warnings=list(section.warnings),
            error=section.error,
        )
        print(
            f"[Section {payload.section_order}/{payload.total_sections}] END   "
            f"{payload.section_title} status="
            f"{section.status.value if hasattr(section.status, 'value') else section.status} "
            f"chars={len(section.content)} truncated={section.truncation.truncated} "
            f"citations={len(section.citation_bindings)} "
            f"evidence_scope={payload.active_scope}",
            flush=True,
        )
        emit_workflow_custom_event(
            "section_completed",
            node_name="finalize_section",
            section_id=section.section_id,
            status=section.status.value,
            progress=(
                float(payload.section_order) / float(payload.total_sections)
                if payload.section_order and payload.total_sections
                else None
            ),
            payload={
                "section_title": payload.section_title,
                "section_order": payload.section_order,
                "citation_count": len(section.citation_bindings),
            },
        )
        updated = payload.model_copy(
            update={
                "section": section,
                "evidence": evidence,
                "result": result,
                "budget_state": budget_usage,
                "shared_state_payload": self.codec.dump_graph_state(shared_state),
            },
            deep=True,
        )
        return self._update(
            updated,
            node_name="finalize_section",
            route="end",
            shared_state=shared_state,
            completed=True,
        )

    def build_graph(self):
        builder = StateGraph(SectionExecutionGraphState)
        builder.add_node("prepare_section", self._prepare_section)
        builder.add_node(
            "retrieve_section_evidence",
            self._retrieve_section_evidence,
        )
        builder.add_node("evaluate_evidence_gate", self._evaluate_evidence_gate)
        builder.add_node("generate_section", self._generate_section)
        builder.add_node(
            "prepare_paragraph_pipeline",
            self._prepare_paragraph_pipeline,
        )
        builder.add_node("persist_paragraph", self._persist_paragraph)
        builder.add_node("precheck_paragraph", self._precheck_paragraph)
        builder.add_node("evaluate_generation", self._evaluate_generation)
        builder.add_node("retrieve_more", self._retrieve_more)
        builder.add_node("corrective_retrieval", self._corrective_retrieval)
        builder.add_node("finalize_section", self._finalize_section)

        builder.add_edge(START, "prepare_section")
        builder.add_conditional_edges(
            "prepare_section",
            self._route,
            {
                "retrieve": "retrieve_section_evidence",
                "evidence_gate": "evaluate_evidence_gate",
                "finalize": "finalize_section",
            },
        )
        builder.add_edge("retrieve_section_evidence", "evaluate_evidence_gate")
        builder.add_conditional_edges(
            "evaluate_evidence_gate",
            self._route,
            {"generate": "generate_section", "finalize": "finalize_section"},
        )
        builder.add_edge("generate_section", "prepare_paragraph_pipeline")
        builder.add_conditional_edges(
            "prepare_paragraph_pipeline",
            self._route,
            {
                "persist_paragraph": "persist_paragraph",
                "evaluate_generation": "evaluate_generation",
            },
        )
        builder.add_edge("persist_paragraph", "precheck_paragraph")
        builder.add_conditional_edges(
            "precheck_paragraph",
            self._route,
            {
                "persist_paragraph": "persist_paragraph",
                "evaluate_generation": "evaluate_generation",
            },
        )
        builder.add_conditional_edges(
            "evaluate_generation",
            self._route,
            {
                "retrieve_more": "retrieve_more",
                "corrective_retrieval": "corrective_retrieval",
                "generate": "generate_section",
                "finalize": "finalize_section",
            },
        )
        builder.add_conditional_edges(
            "retrieve_more",
            self._route,
            {"generate": "generate_section", "finalize": "finalize_section"},
        )
        builder.add_conditional_edges(
            "corrective_retrieval",
            self._route,
            {"generate": "generate_section", "finalize": "finalize_section"},
        )
        builder.add_edge("finalize_section", END)
        return builder.compile(checkpointer=self._langgraph_checkpointer)

    def execute(
        self,
        request: SectionGraphExecutionRequestSchema,
        *,
        shared_state: GraphStateSchema,
        citation_registry: DocumentCitationRegistry,
        workflow_namespace: str,
    ) -> SectionExecutionResultSchema:
        """Execute a serializable section subgraph and project state back."""

        section_id = (
            f"section_{shared_state.run_id}_{request.section_plan.section_order:03d}"
        )
        task_budget = current_task_draining_budget()
        task_budget_exhaustion: TaskDrainingBudgetExceeded | None = None
        task_budget_admitted = False
        if task_budget is not None:
            try:
                task_budget.begin_section(section_id)
                task_budget_admitted = True
            except TaskDrainingBudgetExceeded as exc:
                task_budget_exhaustion = exc

        initial = SectionExecutionGraphPayloadSchema(
            task_id=shared_state.task_id,
            run_id=shared_state.run_id,
            document_id=request.document_id,
            project_input=request.project_input,
            section_plan=request.section_plan,
            structured_facts=list(request.structured_facts),
            previous_sections=list(request.previous_sections),
            shared_state_payload=self.codec.dump_graph_state(shared_state),
            document_rag_context=request.document_rag_context,
            document_retrieved_chunks=list(request.document_retrieved_chunks),
            document_citations=list(request.document_citations),
            document_rag_output=dict(request.document_rag_output),
            document_evidence_assessment=dict(
                request.document_evidence_assessment
            ),
            document_tool_call_ids=list(request.document_tool_call_ids),
            section_retrieval_enabled=request.section_retrieval_enabled,
            corrective_retrieval_enabled=request.corrective_retrieval_enabled,
            parallel_isolated=request.parallel_isolated,
            budget_policy_override=dict(request.budget_policy_override),
            citation_registry=citation_registry.all(),
            budget_exhaustion_resource=(
                f"task_{task_budget_exhaustion.resource}"
                if task_budget_exhaustion is not None
                else None
            ),
            budget_exhaustion_limit=(
                task_budget_exhaustion.limit
                if task_budget_exhaustion is not None
                else None
            ),
            budget_state=(
                {
                    "task_budget": task_budget.snapshot(),
                    "task_draining": task_budget.draining,
                }
                if task_budget is not None
                else {}
            ),
        )
        state = self.codec.build_initial_state(initial)
        self.codec.assert_json_serializable(state)
        try:
            final = self.build_graph().invoke(
                state,
                config=self.checkpoint_config(
                    workflow_namespace=workflow_namespace,
                    run_id=shared_state.run_id,
                    section_id=section_id,
                ),
            )
        except BaseException:
            if task_budget is not None and task_budget_admitted:
                task_budget.abort_section(section_id)
            raise
        else:
            if task_budget is not None and task_budget_admitted:
                task_budget.finish_section(section_id)
        payload = self.codec.restore_payload(final)
        if payload.result is None:
            raise RuntimeError("section subgraph completed without result")
        restored_state = self.codec.restore_graph_state(payload)
        self.graph_state_codec.project_onto(shared_state, restored_state)
        citation_registry.restore(payload.citation_registry)
        return payload.result

    def replan(
        self,
        *,
        workflow_namespace: str,
        run_id: str,
        section_id: str,
        revised_plan: SectionPlanSchema,
        shared_state: GraphStateSchema,
        citation_registry: DocumentCitationRegistry,
    ) -> SectionExecutionResultSchema:
        """Start one superseding attempt in the existing Section namespace."""

        history = self.checkpoint_history(
            workflow_namespace=workflow_namespace,
            run_id=run_id,
            section_id=section_id,
        )
        persisted = None
        for checkpoint_tuple in history:
            checkpoint = getattr(checkpoint_tuple, "checkpoint", {})
            values = (
                checkpoint.get("channel_values", {})
                if isinstance(checkpoint, dict)
                else {}
            )
            raw = values.get("section_payload")
            if not isinstance(raw, dict):
                continue
            persisted = self.codec.restore_payload(
                {
                    "section_graph_state_version": values.get(
                        "section_graph_state_version",
                        1,
                    ),
                    "section_payload": raw,
                }
            )
            break
        if persisted is None:
            raise KeyError(
                f"section checkpoint not found: run_id={run_id} section_id={section_id}"
            )
        replanned = MainlineReplanAttempt.prepare(
            payload=persisted,
            revised_plan=revised_plan,
        )
        final = self.build_graph().invoke(
            self.codec.build_initial_state(replanned),
            config=self.checkpoint_config(
                workflow_namespace=workflow_namespace,
                run_id=run_id,
                section_id=section_id,
            ),
        )
        payload = self.codec.restore_payload(final)
        if payload.result is None:
            raise RuntimeError("replanned section subgraph completed without result")
        restored_state = self.codec.restore_graph_state(payload)
        self.graph_state_codec.project_onto(shared_state, restored_state)
        citation_registry.restore(payload.citation_registry)
        return payload.result

    def resume(
        self,
        *,
        workflow_namespace: str,
        run_id: str,
        section_id: str,
        shared_state: GraphStateSchema,
        citation_registry: DocumentCitationRegistry,
    ) -> SectionExecutionResultSchema:
        """Resume an incomplete Section graph from its latest durable cursor."""

        final = self.build_graph().invoke(
            None,
            config=self.checkpoint_config(
                workflow_namespace=workflow_namespace,
                run_id=run_id,
                section_id=section_id,
            ),
        )
        payload = self.codec.restore_payload(final)
        if payload.result is None:
            raise RuntimeError("resumed section subgraph completed without result")
        restored_state = self.codec.restore_graph_state(payload)
        self.graph_state_codec.project_onto(shared_state, restored_state)
        citation_registry.restore(payload.citation_registry)
        return payload.result

    def recover_or_reuse(
        self,
        *,
        workflow_namespace: str,
        task_id: str,
        run_id: str,
        document_id: str,
        section_id: str,
        project_input,
        section_plan: SectionPlanSchema,
        shared_state: GraphStateSchema,
        citation_registry: DocumentCitationRegistry,
    ) -> SectionExecutionResultSchema | None:
        """Reuse a completed Section or resume its exact durable cursor."""

        history = self.checkpoint_history(
            workflow_namespace=workflow_namespace,
            run_id=run_id,
            section_id=section_id,
        )
        persisted = None
        for checkpoint_tuple in history:
            checkpoint = getattr(checkpoint_tuple, "checkpoint", {})
            values = (
                checkpoint.get("channel_values", {})
                if isinstance(checkpoint, dict)
                else {}
            )
            if not isinstance(values.get("section_payload"), dict):
                continue
            persisted = self.codec.restore_payload(
                {
                    "section_graph_state_version": values.get(
                        "section_graph_state_version",
                        1,
                    ),
                    "section_payload": values["section_payload"],
                }
            )
            break
        if persisted is None:
            return None

        expected_snapshot = MainlineAttemptGuard.snapshot_version(
            project_input,
            section_plan,
        )
        persisted_snapshot = (
            str(persisted.section_state.attempt.input_snapshot_version or "")
            if persisted.section_state is not None
            else ""
        )
        if (
            persisted.task_id != task_id
            or persisted.run_id != run_id
            or persisted.document_id != document_id
            or persisted.section_id != section_id
            or persisted.project_input.model_dump(mode="json", round_trip=True)
            != project_input.model_dump(mode="json", round_trip=True)
            or persisted.section_plan.model_dump(mode="json", round_trip=True)
            != section_plan.model_dump(mode="json", round_trip=True)
            or (persisted_snapshot and persisted_snapshot != expected_snapshot)
        ):
            raise ValueError("section recovery immutable identity mismatch")

        if persisted.completed:
            if persisted.result is None:
                raise RuntimeError("completed Section checkpoint has no result")
            restored_state = self.codec.restore_graph_state(persisted)
            self.graph_state_codec.project_onto(shared_state, restored_state)
            citation_registry.restore(persisted.citation_registry)
            return persisted.result

        return self.resume(
            workflow_namespace=workflow_namespace,
            run_id=run_id,
            section_id=section_id,
            shared_state=shared_state,
            citation_registry=citation_registry,
        )

    def checkpoint_history(
        self,
        *,
        workflow_namespace: str,
        run_id: str,
        section_id: str,
    ) -> list:
        if self.checkpointer is None:
            return []
        return list(
            self.checkpointer.list(
                self.checkpoint_config(
                    workflow_namespace=workflow_namespace,
                    run_id=run_id,
                    section_id=section_id,
                )
            )
        )

    def recover_running_invocation(
        self,
        *,
        workflow_namespace: str,
        run_id: str,
        section_id: str,
        invocation: RunningInvocationRecoveryInputSchema,
    ):
        """Decide recovery from the latest persisted Section checkpoint only."""

        history = self.checkpoint_history(
            workflow_namespace=workflow_namespace,
            run_id=run_id,
            section_id=section_id,
        )
        for checkpoint_tuple in history:
            checkpoint = getattr(checkpoint_tuple, "checkpoint", {})
            values = (
                checkpoint.get("channel_values", {})
                if isinstance(checkpoint, dict)
                else {}
            )
            if not isinstance(values.get("section_payload"), dict):
                continue
            payload = self.codec.restore_payload(
                {
                    "section_graph_state_version": values.get(
                        "section_graph_state_version",
                        1,
                    ),
                    "section_payload": values["section_payload"],
                }
            )
            return self.running_invocation_recovery.decide(
                invocation=invocation,
                payload=payload,
            )
        raise KeyError(
            f"section checkpoint not found: run_id={run_id} section_id={section_id}"
        )
