"""编排一个章节从证据选择到最终章节结果的完整生命周期。

协调器负责普通/恢复检索、证据充分性门禁、章节生成、Self-RAG 补检索、引用恢复、证据
汇总和工作流预算快照；具体检索与生成算法仍由注入服务实现。它不负责整篇文档装配、
Document Hard Gate、最终 Capture 或 AgentResult 映射。
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from apps.enterprise_document.quality.budget import (
    WorkflowBudget,
    WorkflowBudgetExceeded,
    activate_workflow_budget,
)
from apps.enterprise_document.schemas.writer_governance import WriterGateBindingSchema
from apps.enterprise_document.schemas.scheme_writer_schema import (
    SchemeSectionSchema,
    SectionEvalSchema,
    SectionEvidenceBundleSchema,
    SectionExecutionRequestSchema,
    SectionExecutionResultSchema,
)
from apps.enterprise_document.services.gate_enforcement import (
    GateEnforcementController,
    GateEnforcementDecision,
    GateFailureKind,
)
from apps.enterprise_document.services.output_validation import detect_truncation
from agent.runtime.state_access import SharedStateWriter
from rag.evidence.contract import bundle_first_assessment_projection, evidence_bundle_sha256
from schemas.common import ErrorSourceSchema, WarningSchema
from schemas.rag import EvidenceBundleSchema
from schemas.status import ExecutionStatus

from .document_citation_registry import DocumentCitationRegistry
from .evidence_service import SchemeEvidenceService
from .runtime_support import SchemeWriterRuntimeSupport
from .section_generation_service import SectionGenerationService
from .section_retrieval_query_builder import SectionRetrievalQueryBuilder
from .writer_governance import extract_writer_gate_binding

if TYPE_CHECKING:
    from .agent_integration import MainlineSectionAgentAdapter


class SectionExecutionCoordinator:
    """串联单章节子流程，并将所有检索历史和最终有效证据一起返回。"""

    def __init__(
        self,
        *,
        evidence_service: SchemeEvidenceService,
        query_builder: SectionRetrievalQueryBuilder,
        section_generation_service: SectionGenerationService,
        runtime_support: SchemeWriterRuntimeSupport,
        generation_quality_metadata: dict | None,
    ) -> None:
        self.evidence_service = evidence_service
        self.query_builder = query_builder
        self.section_generation_service = section_generation_service
        self.gate_enforcement_controller = getattr(
            section_generation_service,
            "gate_enforcement_controller",
            GateEnforcementController(),
        )
        self.runtime_support = runtime_support
        self.generation_quality_metadata = dict(generation_quality_metadata or {})
        self.section_agent_adapter: MainlineSectionAgentAdapter | None = None

    def resolve_writer_gate_enforcement(
        self,
        binding: WriterGateBindingSchema | None,
    ) -> GateEnforcementDecision:
        """Resolve writer-gate blocking with a strict-compatible test seam."""

        if binding is None or binding.legacy_artifact_incomplete:
            return self.gate_enforcement_controller.resolve(
                gate_passed=False,
                failure_kind=GateFailureKind.TECHNICAL,
            )
        return self.gate_enforcement_controller.resolve(
            gate_passed=binding.ordinary_generation_permitted,
            failure_kind=GateFailureKind.BUSINESS,
        )

    def writer_gate_blocks(self, binding: WriterGateBindingSchema | None) -> bool:
        return self.resolve_writer_gate_enforcement(binding).should_block

    def attach_section_agent_adapter(
        self,
        adapter: "MainlineSectionAgentAdapter",
    ) -> None:
        """Attach production-owned Agents without changing legacy execution."""

        self.section_agent_adapter = adapter

    @staticmethod
    def _evidence_assessment_from_bundle(bundle: EvidenceBundleSchema) -> dict:
        return bundle_first_assessment_projection(bundle)

    @staticmethod
    def _writer_gate_binding(
        normalized_output: dict,
        *,
        evidence_bundle: EvidenceBundleSchema | None = None,
        section_id: str,
        section_title: str,
        section_order: int | None = None,
        document_id: str | None = None,
        run_id: str | None = None,
    ) -> WriterGateBindingSchema | None:
        return extract_writer_gate_binding(
            normalized_output,
            evidence_bundle=evidence_bundle,
            section_id=section_id,
            section_title=section_title,
            section_order=section_order,
            document_id=document_id,
            run_id=run_id,
            evidence_contract_sha256=None,
        )

    def _budget_fallback_section(
        self,
        request: SectionExecutionRequestSchema,
        *,
        section_id: str,
        section_title: str,
        section_order: int,
        fallback_section: SchemeSectionSchema | None = None,
    ) -> SchemeSectionSchema:
        """工作流硬预算耗尽时保留当前最佳章节，或构造需人工复核的最小占位章节。"""

        if fallback_section is not None:
            return fallback_section.model_copy(deep=True)

        shared_state = request.shared_state
        initial_call_id = f"model_call_{shared_state.run_id}_{section_id}"
        raw_response = shared_state.generated_outputs.get(initial_call_id)
        raw_response = raw_response if isinstance(raw_response, dict) else {}
        content = str(raw_response.get("content") or "").strip()
        if not content:
            content = (
                f"“{section_title}”章节已达到工作流预算上限，自动生成与修复已停止，"
                "需人工复核后继续处理。"
            )
        finish_reason = raw_response.get("finish_reason") or "stop"
        truncation = detect_truncation(content, str(finish_reason), 0)
        return SchemeSectionSchema(
            section_id=section_id,
            section_title=section_title,
            section_order=section_order,
            input={"project_input": request.project_input.model_dump()},
            model_output=str(raw_response.get("content") or ""),
            content=content,
            status=ExecutionStatus.PARTIAL_SUCCESS,
            truncation=truncation,
            eval_result=SectionEvalSchema(
                passed=True,
                checks={
                    "content_nonempty": bool(content),
                    "workflow_budget_available": False,
                },
                failures=[],
                warnings=[],
            ),
            started_at=self.runtime_support.now_iso(),
            finished_at=self.runtime_support.now_iso(),
        )

    def _mark_budget_exhausted(
        self,
        section: SchemeSectionSchema,
        *,
        section_budget: WorkflowBudget,
        exhausted: WorkflowBudgetExceeded,
    ) -> SchemeSectionSchema:
        """记录预算耗尽，但不把已有硬失败错误降级为 partial success。"""

        section = section.model_copy(deep=True)
        budget_usage = section_budget.snapshot()
        need_human_review = bool(section_budget.human_review_on_exhaustion)
        details = {
            "resource": exhausted.resource,
            "limit": exhausted.limit,
            "budget_snapshot": budget_usage,
            "need_human_review": need_human_review,
        }
        warning_name = f"workflow_budget_exhausted:{exhausted.resource}"
        warning = WarningSchema(
            warning_code="WORKFLOW_BUDGET_EXHAUSTED",
            message=(
                f"章节工作流预算耗尽：{exhausted.resource} limit={exhausted.limit}；"
                "已停止继续生成或修复。"
            ),
            source=ErrorSourceSchema(
                component="SectionExecutionCoordinator",
                agent_name="SchemeWriterAgent",
                step_name=section.section_id,
            ),
            details=details,
            created_at=self.runtime_support.now_iso(),
        )
        failed_statuses = {
            ExecutionStatus.FAILED,
            ExecutionStatus.RETRYABLE_FAILED,
        }
        has_independent_hard_failure = bool(
            section.status in failed_statuses
            or section.error is not None
            or (
                section.eval_result is not None
                and bool(section.eval_result.failures)
            )
        )
        if has_independent_hard_failure:
            # Budget exhaustion is an additional condition, not a reason to
            # downgrade an already failed section into partial success.
            if section.status not in failed_statuses:
                section.status = ExecutionStatus.FAILED
        else:
            section.status = ExecutionStatus.PARTIAL_SUCCESS
        if not any(
            item.warning_code == "WORKFLOW_BUDGET_EXHAUSTED"
            and (item.details or {}).get("resource") == exhausted.resource
            for item in section.warnings
        ):
            section.warnings.append(warning)

        if section.eval_result is None:
            section.eval_result = SectionEvalSchema(
                passed=True,
                checks={"workflow_budget_available": False},
                failures=[],
                warnings=[warning_name],
            )
        else:
            checks = dict(section.eval_result.checks or {})
            checks["workflow_budget_available"] = False
            eval_warnings = list(section.eval_result.warnings or [])
            if warning_name not in eval_warnings:
                eval_warnings.append(warning_name)
            section.eval_result = section.eval_result.model_copy(
                update={"checks": checks, "warnings": eval_warnings}
            )

        section.extra = {
            **dict(section.extra or {}),
            "workflow_budget_exhausted": True,
            "workflow_budget_exhaustion": details,
            "need_human_review": need_human_review,
        }
        section.finished_at = self.runtime_support.now_iso()
        return section

    def execute(
        self,
        request: SectionExecutionRequestSchema,
        *,
        citation_registry: DocumentCitationRegistry,
    ) -> SectionExecutionResultSchema:
        """Execute one section while preserving the current retrieval/generation behavior."""

        shared_state = request.shared_state
        project_input = request.project_input
        section_plan = request.section_plan
        structured_facts = request.structured_facts
        previous_sections = request.previous_sections

        order = section_plan.section_order
        title = section_plan.section_title
        section_id = f"section_{shared_state.run_id}_{order:03d}"
        required_sections = list(
            project_input.generation_requirements.required_sections
            or project_input.output_schema.required_sections
        )
        total_sections = len(required_sections)
        citation_required_titles = set(
            project_input.generation_requirements.citation_required_sections
        )
        citation_required = bool(
            section_plan.citation_required or title in citation_required_titles
        )

        state_writer = SharedStateWriter()
        if not request.parallel_isolated:
            state_writer.set_current_section(
                shared_state,
                section_id=section_id,
                section_title=title,
            )
        print(
            f"[Section {order}/{total_sections}] START {title}",
            flush=True,
        )

        section_budget = WorkflowBudget.from_policy_metadata(
            {
                **self.generation_quality_metadata,
                **dict(request.budget_policy_override),
            }
        )

        active_evidence_bundle = request.document_evidence_bundle
        active_context = request.document_rag_context
        active_chunks = list(request.document_retrieved_chunks)
        active_citations = list(request.document_citations)
        active_query = project_input.user_query
        active_scope = "document"
        active_writer_gate = self._writer_gate_binding(
            dict(request.document_rag_output),
            evidence_bundle=active_evidence_bundle,
            section_id=section_id,
            section_title=title,
            section_order=order,
            document_id=request.document_id,
            run_id=shared_state.run_id,
        )
        tool_call_ids = list(request.document_tool_call_ids)
        recovery_count = 0
        retrieval_metadata: dict = {
            "section_retrieval_enabled": request.section_retrieval_enabled,
            "citation_required": citation_required,
            "writer_gate_bound": bool(active_writer_gate is not None),
        }
        retrieved_chunks = []
        rag_outputs: list[dict] = []
        budget_exhaustion: WorkflowBudgetExceeded | None = None

        # One section owns one budget lifecycle. ContextVar remains only the
        # propagation mechanism used by deeper generation/quality components.
        with activate_workflow_budget(section_budget):
            if request.section_retrieval_enabled and citation_required:
                section_query = self.query_builder.build(
                    project_input, title, recovery=False
                )
                print(
                    f"[SectionRAG] START section={title} scope=section "
                    f"query={section_query[:100]!r}",
                    flush=True,
                )
                section_result = self.evidence_service.retrieve(
                    shared_state,
                    project_input,
                    query=section_query,
                    scope="section",
                    section_id=section_id,
                    section_title=title,
                    call_suffix=f"section_{order:03d}",
                )
                if section_result is not None:
                    tool_call_ids.append(section_result.retrieval_trace_id)
                if section_result is not None and section_result.success:
                    (
                        section_context,
                        section_chunks,
                        section_citations,
                        section_output,
                    ) = self.evidence_service.extract_rag_output(
                        shared_state,
                        section_result,
                    )
                    (
                        section_context,
                        section_chunks,
                        section_citations,
                        section_output,
                    ) = citation_registry.remap_bundle(
                        context=section_context,
                        chunks=section_chunks,
                        citations=section_citations,
                        normalized=section_output,
                        scope="section",
                        query=section_query,
                    )
                    retrieved_chunks.extend(section_chunks)
                    rag_outputs.append(
                        SchemeEvidenceService.retrieval_history_projection(
                            section_output,
                            scope="section",
                            evidence_bundle=(
                                section_result
                                if isinstance(section_result, EvidenceBundleSchema)
                                else None
                            ),
                        )
                    )
                    section_assessment = self._evidence_assessment_from_bundle(
                        section_result
                    )
                    section_writer_gate = self._writer_gate_binding(
                        section_output,
                        evidence_bundle=section_result,
                        section_id=section_id,
                        section_title=title,
                        section_order=order,
                        document_id=request.document_id,
                        run_id=shared_state.run_id,
                    )
                    retrieval_metadata["writer_gate_bound"] = bool(
                        section_writer_gate is not None
                    )
                    if section_context.context_text.strip():
                        active_evidence_bundle = section_result
                        active_context = section_context
                        active_chunks = section_chunks
                        active_citations = section_citations
                        active_query = section_query
                        active_scope = "section"
                        active_writer_gate = section_writer_gate
                    retrieval_metadata["section_retrieval_success"] = True
                    retrieval_metadata["section_citation_count"] = len(
                        section_citations
                    )
                    print(
                        f"[SectionRAG] END   section={title} success=True "
                        f"chunks={len(section_chunks)} citations={len(section_citations)}",
                        flush=True,
                    )
                else:
                    retrieval_metadata["section_retrieval_success"] = False
                    retrieval_metadata["section_retrieval_error"] = (
                        section_result.error_message
                        if section_result
                        else "not_configured"
                    )
                    print(
                        f"[SectionRAG] END   section={title} success=False "
                        "fallback=document_evidence",
                        flush=True,
                    )

            writer_gate_missing = active_writer_gate is None
            writer_enforcement = (
                self.resolve_writer_gate_enforcement(active_writer_gate)
            )
            evidence_blocked = writer_enforcement.should_block
            retrieval_metadata["writer_gate_enforcement"] = (
                writer_enforcement.as_metadata(gate_id="G06/G11")
            )
            if evidence_blocked:
                block_reason = (
                    "writer_gate_missing"
                    if writer_gate_missing
                    else (
                        "legacy_writer_artifact_incomplete"
                        if active_writer_gate.legacy_artifact_incomplete
                        else (
                            "writer_human_review_required"
                            if active_writer_gate.gate_decision.human_review_required
                            else "writer_generation_blocked"
                        )
                    )
                )
                retrieval_metadata["normal_generation_blocked"] = True
                retrieval_metadata["generation_block_reason"] = block_reason
                print(
                    f"[WriterGate] BLOCK section={title} reason={block_reason}",
                    flush=True,
                )
                if active_writer_gate is not None:
                    section = self.section_generation_service.build_gate_blocked_section(
                        shared_state,
                        project_input=project_input,
                        section_title=title,
                        section_order=order,
                        rag_context=active_context,
                        citations=active_citations,
                        writer_gate_binding=active_writer_gate,
                    )
                else:
                    section = self.section_generation_service.build_insufficient_evidence_section(
                        shared_state,
                        project_input=project_input,
                        section_title=title,
                        section_order=order,
                        rag_context=active_context,
                        citations=active_citations,
                        assessment={
                            **bundle_first_assessment_projection(
                                active_evidence_bundle,
                            ),
                            "status": "not_assessed",
                            "reason": "final writer gate binding is missing",
                        },
                    )
                    section.extra = {
                        **dict(section.extra or {}),
                        "generation_block_reason": "writer_gate_missing",
                        "writer_execution_status": "not_assessed",
                        "formal_deliverable_eligible": False,
                    }
            else:
                try:
                    section = self.section_generation_service.generate_section(
                        shared_state,
                        document_id=request.document_id,
                        project_input=project_input,
                        section_title=title,
                        section_order=order,
                        rag_context=active_context,
                        citations=active_citations,
                        structured_facts=structured_facts,
                        previous_sections=previous_sections,
                        evidence_bundle=active_evidence_bundle,
                        generation_attempt=1,
                        writer_gate_binding=active_writer_gate,
                    )
                except WorkflowBudgetExceeded as exc:
                    budget_exhaustion = exc
                    section = self._budget_fallback_section(
                        request,
                        section_id=section_id,
                        section_title=title,
                        section_order=order,
                    )

            generation_check = (
                {}
                if budget_exhaustion is not None
                else dict((section.extra or {}).get("generation_check") or {})
            )
            self_rag_rounds: list[dict] = []
            while (
                bool(generation_check.get("need_retrieve_more"))
                and section_budget.retrieval_rounds
                < section_budget.max_retrieval_rounds
            ):
                section_budget.consume_retrieval_round()
                recovery_count += 1
                round_number = section_budget.retrieval_rounds
                self_rag_query = self.query_builder.build(
                    project_input,
                    title,
                    recovery=True,
                )
                round_trace = {
                    "round": round_number,
                    "query": self_rag_query,
                    "check_before": dict(generation_check),
                    "success": False,
                }
                print(
                    f"[SelfRAGRetrieveMore] START section={title} "
                    f"round={round_number} query={self_rag_query[:100]!r}",
                    flush=True,
                )
                self_rag_result = self.evidence_service.retrieve(
                    shared_state,
                    project_input,
                    query=self_rag_query,
                    scope="self_rag_recovery",
                    section_id=section_id,
                    section_title=title,
                    call_suffix=f"section_{order:03d}_self_rag_{recovery_count}",
                )
                if self_rag_result is not None:
                    tool_call_ids.append(self_rag_result.retrieval_trace_id)
                if self_rag_result is None or not self_rag_result.success:
                    round_trace["error"] = (
                        self_rag_result.error_message
                        if self_rag_result is not None
                        else "not_configured"
                    )
                    self_rag_rounds.append(round_trace)
                    break

                (
                    recovered_context,
                    recovered_chunks,
                    recovered_citations,
                    recovered_output,
                ) = self.evidence_service.extract_rag_output(
                    shared_state,
                    self_rag_result,
                )
                candidate_registry = DocumentCitationRegistry.from_citations(
                    citation_registry.all()
                )
                (
                    recovered_context,
                    recovered_chunks,
                    recovered_citations,
                    recovered_output,
                ) = candidate_registry.remap_bundle(
                    context=recovered_context,
                    chunks=recovered_chunks,
                    citations=recovered_citations,
                    normalized=recovered_output,
                    scope="self_rag_recovery",
                    query=self_rag_query,
                )
                rag_outputs.append(
                    SchemeEvidenceService.retrieval_history_projection(
                        recovered_output,
                        scope="self_rag_recovery",
                        evidence_bundle=self_rag_result,
                    )
                )
                recovered_assessment = self._evidence_assessment_from_bundle(
                    self_rag_result
                )
                recovered_writer_gate = self._writer_gate_binding(
                    recovered_output,
                    evidence_bundle=self_rag_result,
                    section_id=section_id,
                    section_title=title,
                    section_order=order,
                    document_id=request.document_id,
                    run_id=shared_state.run_id,
                )
                current_generation_permitted = bool(
                    active_writer_gate is not None
                    and not self.writer_gate_blocks(active_writer_gate)
                )
                recovered_context_available = bool(
                    recovered_context.context_text.strip()
                )
                recovered_generation_permitted = bool(
                    recovered_context_available
                    and recovered_writer_gate is not None
                    and not self.writer_gate_blocks(recovered_writer_gate)
                )
                if (
                    not recovered_generation_permitted
                    and current_generation_permitted
                ):
                    rejection_reason = (
                        "empty_recovered_context"
                        if not recovered_context_available
                        else "recovery_not_ordinary_generation_permitted"
                    )
                    retrieval_metadata["self_rag_recovery_rejected"] = (
                        rejection_reason
                    )
                    round_trace.update(
                        {
                            "error": rejection_reason,
                            "recovery_rejected": True,
                            "retained_scope": active_scope,
                            "retrieved_chunk_count": len(recovered_chunks),
                            "citation_count": len(recovered_citations),
                        }
                    )
                    self_rag_rounds.append(round_trace)
                    print(
                        f"[SelfRAGRetrieveMore] KEEP section={title} "
                        f"reason={rejection_reason} retained_scope={active_scope}",
                        flush=True,
                    )
                    break
                if not recovered_context_available and (
                    recovered_writer_gate is None
                    or recovered_writer_gate.ordinary_generation_permitted
                ):
                    round_trace["error"] = "empty_recovered_context"
                    self_rag_rounds.append(round_trace)
                    break

                # Application accepted the recovery candidate. Only now may its
                # document citation labels and full chunk projection be committed.
                citation_registry.restore(candidate_registry.all())
                retrieved_chunks.extend(recovered_chunks)
                active_evidence_bundle = self_rag_result
                active_context = recovered_context
                active_chunks = recovered_chunks
                active_citations = recovered_citations
                active_query = self_rag_query
                active_scope = "self_rag_recovery"
                active_writer_gate = recovered_writer_gate
                retrieval_metadata["writer_gate_bound"] = bool(
                    active_writer_gate is not None
                )
                if self.writer_gate_blocks(active_writer_gate):
                    evidence_blocked = True
                    block_reason = (
                        "writer_gate_missing_after_self_rag_retrieval"
                        if active_writer_gate is None
                        else (
                            "legacy_writer_artifact_incomplete_after_self_rag_retrieval"
                            if active_writer_gate.legacy_artifact_incomplete
                            else (
                                "writer_human_review_required_after_self_rag_retrieval"
                                if active_writer_gate.gate_decision.human_review_required
                                else "writer_generation_blocked_after_self_rag_retrieval"
                            )
                        )
                    )
                    retrieval_metadata["normal_generation_blocked"] = True
                    retrieval_metadata["generation_block_reason"] = block_reason
                    if active_writer_gate is not None:
                        section = self.section_generation_service.build_gate_blocked_section(
                            shared_state,
                            project_input=project_input,
                            section_title=title,
                            section_order=order,
                            rag_context=active_context,
                            citations=active_citations,
                            writer_gate_binding=active_writer_gate,
                        )
                    else:
                        section = self.section_generation_service.build_insufficient_evidence_section(
                            shared_state,
                            project_input=project_input,
                            section_title=title,
                            section_order=order,
                            rag_context=active_context,
                            citations=active_citations,
                            assessment={
                                **bundle_first_assessment_projection(
                                    active_evidence_bundle,
                                ),
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
                    generation_check = {}
                    round_trace.update(
                        {
                            "error": block_reason,
                            "assessment": recovered_assessment,
                            "retrieved_chunk_count": len(recovered_chunks),
                            "citation_count": len(recovered_citations),
                        }
                    )
                    self_rag_rounds.append(round_trace)
                    print(
                        f"[WriterGate] BLOCK section={title} reason={block_reason}",
                        flush=True,
                    )
                    break

                try:
                    candidate_section = self.section_generation_service.generate_section(
                        shared_state,
                        document_id=request.document_id,
                        project_input=project_input,
                        section_title=title,
                        section_order=order,
                        rag_context=active_context,
                        citations=active_citations,
                        structured_facts=structured_facts,
                        previous_sections=previous_sections,
                        evidence_bundle=active_evidence_bundle,
                        generation_attempt=round_number + 1,
                        writer_gate_binding=active_writer_gate,
                    )
                except WorkflowBudgetExceeded as exc:
                    budget_exhaustion = exc
                    section = self._budget_fallback_section(
                        request,
                        section_id=section_id,
                        section_title=title,
                        section_order=order,
                        fallback_section=section,
                    )
                    generation_check = {}
                    round_trace.update(
                        {
                            "error": "workflow_budget_exhausted",
                            "budget_resource": exc.resource,
                            "budget_limit": exc.limit,
                            "retrieved_chunk_count": len(recovered_chunks),
                            "citation_count": len(recovered_citations),
                        }
                    )
                    self_rag_rounds.append(round_trace)
                    break
                else:
                    section = candidate_section
                    generation_check = dict(
                        (section.extra or {}).get("generation_check") or {}
                    )
                    round_trace.update(
                        {
                            "success": True,
                            "check_after": dict(generation_check),
                            "retrieved_chunk_count": len(recovered_chunks),
                            "citation_count": len(recovered_citations),
                        }
                    )
                    self_rag_rounds.append(round_trace)
                    print(
                        f"[SelfRAGRetrieveMore] END   section={title} "
                        f"round={round_number} success=True "
                        f"need_more={generation_check.get('need_retrieve_more')}",
                        flush=True,
                    )

            if self_rag_rounds:
                retrieval_metadata["self_rag_retrieval_rounds"] = self_rag_rounds
                retrieval_metadata["self_rag_retrieve_more_success"] = bool(
                    self_rag_rounds[-1].get("success")
                )
                retrieval_metadata["self_rag_recheck"] = dict(generation_check)
            if bool(generation_check.get("need_retrieve_more")):
                retrieval_metadata["self_rag_retrieve_more_unresolved"] = True
                retrieval_budget_exhausted = (
                    section_budget.retrieval_rounds
                    >= section_budget.max_retrieval_rounds
                )
                retrieval_metadata["self_rag_retrieve_more_budget_exhausted"] = (
                    retrieval_budget_exhausted
                )
                if retrieval_budget_exhausted and budget_exhaustion is None:
                    budget_exhaustion = WorkflowBudgetExceeded(
                        "retrieval_rounds",
                        section_budget.max_retrieval_rounds,
                    )

            citation_bound = bool(
                section.eval_result
                and section.eval_result.checks.get("citation_bound", False)
            )
            corrective_retrieval_needed = bool(
                citation_required
                and not evidence_blocked
                and not citation_bound
                and request.corrective_retrieval_enabled
            )
            if (
                corrective_retrieval_needed
                and budget_exhaustion is None
                and section_budget.retrieval_rounds
                < section_budget.max_retrieval_rounds
            ):
                section_budget.consume_retrieval_round()
                recovery_count += 1
                recovery_query = self.query_builder.build(
                    project_input,
                    title,
                    recovery=True,
                )
                print(
                    f"[CorrectiveSectionRAG] START section={title} "
                    f"query={recovery_query[:100]!r}",
                    flush=True,
                )
                recovery_result = self.evidence_service.retrieve(
                    shared_state,
                    project_input,
                    query=recovery_query,
                    scope="recovery",
                    section_id=section_id,
                    section_title=title,
                    call_suffix=f"section_{order:03d}_recovery_{recovery_count}",
                )
                if recovery_result is not None:
                    tool_call_ids.append(recovery_result.retrieval_trace_id)
                if recovery_result is not None and recovery_result.success:
                    (
                        recovery_context,
                        recovery_chunks,
                        recovery_citations,
                        recovery_output,
                    ) = self.evidence_service.extract_rag_output(
                        shared_state,
                        recovery_result,
                    )
                    candidate_registry = DocumentCitationRegistry.from_citations(
                        citation_registry.all()
                    )
                    (
                        recovery_context,
                        recovery_chunks,
                        recovery_citations,
                        recovery_output,
                    ) = candidate_registry.remap_bundle(
                        context=recovery_context,
                        chunks=recovery_chunks,
                        citations=recovery_citations,
                        normalized=recovery_output,
                        scope="recovery",
                        query=recovery_query,
                    )
                    rag_outputs.append(
                        SchemeEvidenceService.retrieval_history_projection(
                            recovery_output,
                            scope="recovery",
                            evidence_bundle=recovery_result,
                        )
                    )
                    recovery_assessment = self._evidence_assessment_from_bundle(
                        recovery_result
                    )
                    recovery_writer_gate = self._writer_gate_binding(
                        recovery_output,
                        evidence_bundle=recovery_result,
                        section_id=section_id,
                        section_title=title,
                        section_order=order,
                        document_id=request.document_id,
                        run_id=shared_state.run_id,
                    )
                    current_generation_permitted = bool(
                        active_writer_gate is not None
                        and not self.writer_gate_blocks(active_writer_gate)
                    )
                    recovery_context_available = bool(
                        recovery_context.context_text.strip()
                    )
                    recovery_generation_permitted = bool(
                        recovery_context_available
                        and recovery_writer_gate is not None
                        and not self.writer_gate_blocks(recovery_writer_gate)
                    )
                    if recovery_generation_permitted:
                        citation_registry.restore(candidate_registry.all())
                        retrieved_chunks.extend(recovery_chunks)
                        active_evidence_bundle = (
                            recovery_result
                            if isinstance(recovery_result, EvidenceBundleSchema)
                            else None
                        )
                        active_context = recovery_context
                        active_chunks = recovery_chunks
                        active_citations = recovery_citations
                        active_query = recovery_query
                        active_scope = "recovery"
                        active_writer_gate = recovery_writer_gate
                        retrieval_metadata["corrective_recovery_accepted"] = True
                        try:
                            candidate_section = self.section_generation_service.generate_section(
                                shared_state,
                                document_id=request.document_id,
                                project_input=project_input,
                                section_title=title,
                                section_order=order,
                                rag_context=active_context,
                                citations=active_citations,
                                structured_facts=structured_facts,
                                previous_sections=previous_sections,
                                evidence_bundle=active_evidence_bundle,
                                generation_attempt=recovery_count + 1,
                                writer_gate_binding=active_writer_gate,
                            )
                        except WorkflowBudgetExceeded as exc:
                            budget_exhaustion = exc
                            section = self._budget_fallback_section(
                                request,
                                section_id=section_id,
                                section_title=title,
                                section_order=order,
                                fallback_section=section,
                            )
                        else:
                            section = candidate_section
                    elif current_generation_permitted:
                        rejection_reason = (
                            "empty_recovery_context"
                            if not recovery_context_available
                            else "recovery_not_ordinary_generation_permitted"
                        )
                        retrieval_metadata["corrective_recovery_accepted"] = False
                        retrieval_metadata["corrective_recovery_rejected"] = (
                            rejection_reason
                        )
                        retrieval_metadata["corrective_retained_scope"] = active_scope
                        print(
                            f"[CorrectiveSectionRAG] KEEP section={title} "
                            f"reason={rejection_reason} retained_scope={active_scope}",
                            flush=True,
                        )
                    elif recovery_context_available or recovery_writer_gate is not None:
                        # Preserve the existing Phase 4B acceptance semantics when
                        # both current and candidate are blocked, but commit only
                        # after that Application acceptance decision.
                        citation_registry.restore(candidate_registry.all())
                        retrieved_chunks.extend(recovery_chunks)
                        active_evidence_bundle = (
                            recovery_result
                            if isinstance(recovery_result, EvidenceBundleSchema)
                            else None
                        )
                        active_context = recovery_context
                        active_chunks = recovery_chunks
                        active_citations = recovery_citations
                        active_query = recovery_query
                        active_scope = "recovery"
                        active_writer_gate = recovery_writer_gate
                        evidence_blocked = True
                        block_reason = (
                            "writer_gate_missing_after_corrective_retrieval"
                            if active_writer_gate is None
                            else (
                                "legacy_writer_artifact_incomplete_after_corrective_retrieval"
                                if active_writer_gate.legacy_artifact_incomplete
                                else (
                                    "writer_human_review_required_after_corrective_retrieval"
                                    if active_writer_gate.gate_decision.human_review_required
                                    else "writer_generation_blocked_after_corrective_retrieval"
                                )
                            )
                        )
                        retrieval_metadata["corrective_generation_skipped"] = block_reason
                        retrieval_metadata["generation_block_reason"] = block_reason
                        if active_writer_gate is not None:
                            section = self.section_generation_service.build_gate_blocked_section(
                                shared_state,
                                project_input=project_input,
                                section_title=title,
                                section_order=order,
                                rag_context=active_context,
                                citations=active_citations,
                                writer_gate_binding=active_writer_gate,
                            )
                    retrieval_metadata["corrective_retrieval_success"] = True
                    retrieval_metadata["corrective_citation_count"] = len(
                        recovery_citations
                    )
                    print(
                        f"[CorrectiveSectionRAG] END   section={title} success=True "
                        f"chunks={len(recovery_chunks)} citations={len(recovery_citations)} "
                        f"final_status={section.status.value if hasattr(section.status, 'value') else section.status}",
                        flush=True,
                    )
                else:
                    retrieval_metadata["corrective_retrieval_success"] = False
                    retrieval_metadata["corrective_retrieval_error"] = (
                        recovery_result.error_message
                        if recovery_result
                        else "not_configured"
                    )
                    print(
                        f"[CorrectiveSectionRAG] END   section={title} success=False",
                        flush=True,
                    )

            final_citation_bound = bool(
                section.eval_result
                and section.eval_result.checks.get("citation_bound", False)
            )
            if (
                citation_required
                and not evidence_blocked
                and not final_citation_bound
                and request.corrective_retrieval_enabled
                and budget_exhaustion is None
                and section_budget.retrieval_rounds
                >= section_budget.max_retrieval_rounds
            ):
                retrieval_metadata["corrective_retrieval_budget_exhausted"] = True
                budget_exhaustion = WorkflowBudgetExceeded(
                    "retrieval_rounds",
                    section_budget.max_retrieval_rounds,
                )

            if budget_exhaustion is not None:
                section = self._mark_budget_exhausted(
                    section,
                    section_budget=section_budget,
                    exhausted=budget_exhaustion,
                )
                retrieval_metadata["workflow_budget_exhausted"] = True
                retrieval_metadata["workflow_budget_exhaustion"] = {
                    "resource": budget_exhaustion.resource,
                    "limit": budget_exhaustion.limit,
                    "need_human_review": bool(
                        section_budget.human_review_on_exhaustion
                    ),
                }

        contract_sha = (
            evidence_bundle_sha256(active_evidence_bundle)
            if active_evidence_bundle is not None
            else None
        )
        evidence = SectionEvidenceBundleSchema(
            section_id=section_id,
            section_title=title,
            evidence_bundle=active_evidence_bundle,
            tool_call_ids=list(dict.fromkeys(tool_call_ids)),
            recovery_count=recovery_count,
            evidence_contract_sha256=contract_sha,
            writer_gate_binding=(
                active_writer_gate.model_dump(mode="json", exclude_none=True)
                if active_writer_gate is not None
                else None
            ),
            metadata=retrieval_metadata,
        )
        budget_usage = section_budget.snapshot()
        section.extra = {
            **dict(section.extra or {}),
            "evidence_scope": active_scope,
            "evidence_query": active_query,
            "evidence_tool_call_ids": list(dict.fromkeys(tool_call_ids)),
            "evidence_contract_sha256": contract_sha,
            "corrective_retrieval_count": recovery_count,
            "workflow_budget": budget_usage,
            "writer_gate_binding": (
                active_writer_gate.model_dump(mode="json", exclude_none=True)
                if active_writer_gate is not None
                else None
            ),
            "writer_execution_status": (
                active_writer_gate.execution_status.value
                if active_writer_gate is not None
                else "not_assessed"
            ),
        }
        state_writer.add_generated_section(shared_state, section.section_id)
        print(
            f"[Section {order}/{total_sections}] END   {title} "
            f"status={section.status.value if hasattr(section.status, 'value') else section.status} "
            f"chars={len(section.content)} truncated={section.truncation.truncated} "
            f"citations={len(section.citation_bindings)} evidence_scope={active_scope}",
            flush=True,
        )

        return SectionExecutionResultSchema(
            section=section,
            evidence=evidence,
            retrieved_chunks=retrieved_chunks,
            rag_outputs=rag_outputs,
            citation_registry=citation_registry.all(),
            budget_usage=budget_usage,
            need_human_review=bool(
                (
                    budget_exhaustion is not None
                    and section_budget.human_review_on_exhaustion
                )
                or (
                    active_writer_gate is not None
                    and active_writer_gate.gate_decision.human_review_required
                )
                or active_writer_gate is None
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
