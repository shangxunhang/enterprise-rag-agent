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
from agent.runtime.state_access import SharedStateWriter
from apps.enterprise_document.quality.budget import (
    WorkflowBudget,
    WorkflowBudgetExceeded,
    activate_workflow_budget,
)
from apps.enterprise_document.schemas.section_execution_graph import (
    SectionExecutionGraphPayloadSchema,
    SectionExecutionGraphState,
    SectionExecutionGraphStateCodec,
    SectionGraphExecutionRequestSchema,
)
from apps.enterprise_document.schemas.scheme_writer_schema import (
    SectionEvidenceBundleSchema,
    SectionExecutionRequestSchema,
    SectionExecutionResultSchema,
)
from apps.enterprise_document.services.output_validation import detect_truncation
from schemas.common import ErrorSourceSchema, WarningSchema
from schemas.status import ExecutionStatus

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
        self.runtime_support = coordinator.runtime_support
        self.generation_quality_metadata = dict(
            coordinator.generation_quality_metadata or {}
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
            "recursion_limit": 64,
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
            document_evidence_assessment=dict(
                payload.document_evidence_assessment
            ),
            document_tool_call_ids=list(payload.document_tool_call_ids),
            section_retrieval_enabled=payload.section_retrieval_enabled,
            corrective_retrieval_enabled=payload.corrective_retrieval_enabled,
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
        self.state_writer.set_current_section(
            shared_state,
            section_id=section_id,
            section_title=plan.section_title,
        )
        registry = DocumentCitationRegistry.from_citations(
            payload.citation_registry or payload.document_citations
        )
        budget = WorkflowBudget.from_policy_metadata(
            self.generation_quality_metadata
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
                },
                "budget_state": budget.snapshot(),
            },
            deep=True,
        )
        print(
            f"[Section {plan.section_order}/{len(required)}] START "
            f"{plan.section_title}",
            flush=True,
        )
        route = (
            "retrieve"
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
        result = self.evidence_service.retrieve(
            shared_state,
            payload.project_input,
            query=query,
            scope="section",
            section_id=section_id,
            section_title=title,
            call_suffix=f"section_{order:03d}",
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
            metadata.update(
                {
                    "section_retrieval_success": True,
                    "section_citation_count": len(citations),
                    "evidence_assessment_status": assessment.get(
                        "status", "not_assessed"
                    ),
                    "evidence_assessment": assessment,
                }
            )
            updates.update(
                {
                    "citation_registry": registry.all(),
                    "retrieved_chunks": [*payload.retrieved_chunks, *chunks],
                    "rag_outputs": [*payload.rag_outputs, normalized],
                    "active_assessment": assessment,
                }
            )
            if citations and context.context_text.strip():
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
        blocked = bool(
            payload.citation_required
            and payload.active_assessment.get("status") == "insufficient"
        )
        updates: dict[str, Any] = {"evidence_blocked": blocked}
        route = "generate"
        if blocked:
            title = str(payload.section_title)
            metadata = dict(payload.retrieval_metadata)
            metadata.update(
                {
                    "normal_generation_blocked": True,
                    "generation_block_reason": "evidence_insufficient",
                }
            )
            section = self.section_generation_service.build_insufficient_evidence_section(
                shared_state,
                project_input=payload.project_input,
                section_title=title,
                section_order=int(payload.section_order or 0),
                rag_context=payload.active_context or payload.document_rag_context,
                citations=list(payload.active_citations),
                assessment=dict(payload.active_assessment),
            )
            updates.update(
                {
                    "section": section,
                    "generation_check": {},
                    "retrieval_metadata": metadata,
                }
            )
            route = "finalize"
            print(
                f"[EvidenceGate] BLOCK section={title} "
                "reason=evidence_insufficient",
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
            route="evaluate_generation",
            shared_state=shared_state,
        )

    def _evaluate_generation(
        self,
        state: SectionExecutionGraphState,
    ) -> dict[str, Any]:
        payload, shared_state = self._restore(state)
        budget = WorkflowBudget.from_snapshot(payload.budget_state)
        section = payload.section
        if section is None:
            raise ValueError("evaluate_generation requires section")
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
            updates.update(
                {
                    "citation_registry": registry.all(),
                    "retrieved_chunks": [*payload.retrieved_chunks, *chunks],
                    "rag_outputs": [*payload.rag_outputs, normalized],
                    "active_assessment": assessment,
                }
            )
            if citations and context.context_text.strip():
                updates.update(
                    {
                        "active_context": context,
                        "active_chunks": list(chunks),
                        "active_citations": list(citations),
                        "active_query": query,
                        "active_scope": scope,
                    }
                )
                if (
                    payload.citation_required
                    and assessment.get("status") == "insufficient"
                ):
                    section = self.section_generation_service.build_insufficient_evidence_section(
                        shared_state,
                        project_input=payload.project_input,
                        section_title=title,
                        section_order=order,
                        rag_context=context,
                        citations=list(citations),
                        assessment=assessment,
                    )
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
                            "error": "evidence_insufficient",
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
        return self._update(
            updated,
            node_name=(
                "corrective_retrieval" if corrective else "retrieve_more"
            ),
            route=route,
            shared_state=shared_state,
        )

    def _retrieve_more(
        self,
        state: SectionExecutionGraphState,
    ) -> dict[str, Any]:
        return self._retrieve_recovery(state, corrective=False)

    def _corrective_retrieval(
        self,
        state: SectionExecutionGraphState,
    ) -> dict[str, Any]:
        return self._retrieve_recovery(state, corrective=True)

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
            "section_subgraph_nodes": [
                *payload.executed_nodes,
                "finalize_section",
            ],
        }
        self.state_writer.add_generated_section(shared_state, section.section_id)
        result = SectionExecutionResultSchema(
            section=section,
            evidence=evidence,
            retrieved_chunks=list(payload.retrieved_chunks),
            rag_outputs=list(payload.rag_outputs),
            budget_usage=budget_usage,
            need_human_review=bool(
                payload.budget_exhaustion_resource is not None
                and budget.human_review_on_exhaustion
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
            },
        )
        builder.add_edge("retrieve_section_evidence", "evaluate_evidence_gate")
        builder.add_conditional_edges(
            "evaluate_evidence_gate",
            self._route,
            {"generate": "generate_section", "finalize": "finalize_section"},
        )
        builder.add_edge("generate_section", "evaluate_generation")
        builder.add_conditional_edges(
            "evaluate_generation",
            self._route,
            {
                "retrieve_more": "retrieve_more",
                "corrective_retrieval": "corrective_retrieval",
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
            document_evidence_assessment=dict(
                request.document_evidence_assessment
            ),
            document_tool_call_ids=list(request.document_tool_call_ids),
            section_retrieval_enabled=request.section_retrieval_enabled,
            corrective_retrieval_enabled=request.corrective_retrieval_enabled,
            citation_registry=citation_registry.all(),
        )
        state = self.codec.build_initial_state(initial)
        self.codec.assert_json_serializable(state)
        section_id = f"section_{shared_state.run_id}_{request.section_plan.section_order:03d}"
        final = self.build_graph().invoke(
            state,
            config=self.checkpoint_config(
                workflow_namespace=workflow_namespace,
                run_id=shared_state.run_id,
                section_id=section_id,
            ),
        )
        payload = self.codec.restore_payload(final)
        if payload.result is None:
            raise RuntimeError("section subgraph completed without result")
        restored_state = self.codec.restore_graph_state(payload)
        self.graph_state_codec.project_onto(shared_state, restored_state)
        citation_registry.restore(payload.citation_registry)
        return payload.result

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
