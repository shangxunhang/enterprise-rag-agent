# =============================================================================
# 中文阅读说明：单章节完整生命周期编排。
# 负责章节检索、证据门禁、生成、Self-RAG 恢复、引用恢复、证据汇总与预算快照。
# 不负责整篇文档组装、Document Hard Gate、最终 Capture 或 AgentResult。
# =============================================================================
"""Coordinate one complete section lifecycle without changing generation algorithms."""

from __future__ import annotations

from apps.enterprise_document.quality.budget import (
    WorkflowBudget,
    WorkflowBudgetExceeded,
    activate_workflow_budget,
)
from apps.enterprise_document.schemas.scheme_writer_schema import (
    SchemeSectionSchema,
    SectionEvalSchema,
    SectionEvidenceBundleSchema,
    SectionExecutionRequestSchema,
    SectionExecutionResultSchema,
)
from apps.enterprise_document.services.output_validation import detect_truncation
from agent.runtime.state_access import SharedStateWriter
from schemas.common import ErrorSourceSchema, WarningSchema
from schemas.status import ExecutionStatus

from .document_citation_registry import DocumentCitationRegistry
from .evidence_service import SchemeEvidenceService
from .runtime_support import SchemeWriterRuntimeSupport
from .section_generation_service import SectionGenerationService
from .section_retrieval_query_builder import SectionRetrievalQueryBuilder


class SectionExecutionCoordinator:
    """Own one section from evidence selection through final section result."""

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
        self.runtime_support = runtime_support
        self.generation_quality_metadata = dict(generation_quality_metadata or {})

    @staticmethod
    def _evidence_assessment(normalized_output: dict) -> dict:
        """Read one canonical evidence assessment from a normalized RAG output."""

        contract = normalized_output.get("evidence")
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

    def _budget_fallback_section(
        self,
        request: SectionExecutionRequestSchema,
        *,
        section_id: str,
        section_title: str,
        section_order: int,
        fallback_section: SchemeSectionSchema | None = None,
    ) -> SchemeSectionSchema:
        """Preserve the best available section when a hard workflow budget trips."""

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
        """Record budget exhaustion without masking any pre-existing hard failure."""

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
            self.generation_quality_metadata
        )

        active_context = request.document_rag_context
        active_chunks = list(request.document_retrieved_chunks)
        active_citations = list(request.document_citations)
        active_query = project_input.user_query
        active_scope = "document"
        active_assessment = dict(request.document_evidence_assessment)
        tool_call_ids = list(request.document_tool_call_ids)
        recovery_count = 0
        retrieval_metadata: dict = {
            "section_retrieval_enabled": request.section_retrieval_enabled,
            "citation_required": citation_required,
            "evidence_assessment_status": active_assessment.get(
                "status", "not_assessed"
            ),
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
                    rag_outputs.append(section_output)
                    section_assessment = self._evidence_assessment(section_output)
                    active_assessment = section_assessment
                    retrieval_metadata["evidence_assessment_status"] = (
                        section_assessment.get("status", "not_assessed")
                    )
                    retrieval_metadata["evidence_assessment"] = section_assessment
                    if section_citations and section_context.context_text.strip():
                        active_context = section_context
                        active_chunks = section_chunks
                        active_citations = section_citations
                        active_query = section_query
                        active_scope = "section"
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

            evidence_blocked = bool(
                citation_required
                and active_assessment.get("status") == "insufficient"
            )
            if evidence_blocked:
                retrieval_metadata["normal_generation_blocked"] = True
                retrieval_metadata["generation_block_reason"] = "evidence_insufficient"
                print(
                    f"[EvidenceGate] BLOCK section={title} "
                    "reason=evidence_insufficient",
                    flush=True,
                )
                section = (
                    self.section_generation_service.build_insufficient_evidence_section(
                        shared_state,
                        project_input=project_input,
                        section_title=title,
                        section_order=order,
                        rag_context=active_context,
                        citations=active_citations,
                        assessment=active_assessment,
                    )
                )
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
                        generation_attempt=1,
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
                (
                    recovered_context,
                    recovered_chunks,
                    recovered_citations,
                    recovered_output,
                ) = citation_registry.remap_bundle(
                    context=recovered_context,
                    chunks=recovered_chunks,
                    citations=recovered_citations,
                    normalized=recovered_output,
                    scope="self_rag_recovery",
                    query=self_rag_query,
                )
                retrieved_chunks.extend(recovered_chunks)
                rag_outputs.append(recovered_output)
                recovered_assessment = self._evidence_assessment(recovered_output)
                if not recovered_context.context_text.strip():
                    round_trace["error"] = "empty_recovered_context"
                    self_rag_rounds.append(round_trace)
                    break

                active_context = recovered_context
                active_chunks = recovered_chunks
                active_citations = recovered_citations
                active_query = self_rag_query
                active_scope = "self_rag_recovery"
                active_assessment = recovered_assessment
                if (
                    citation_required
                    and recovered_assessment.get("status") == "insufficient"
                ):
                    evidence_blocked = True
                    retrieval_metadata["normal_generation_blocked"] = True
                    retrieval_metadata["generation_block_reason"] = (
                        "evidence_insufficient_after_self_rag_retrieval"
                    )
                    section = (
                        self.section_generation_service.build_insufficient_evidence_section(
                            shared_state,
                            project_input=project_input,
                            section_title=title,
                            section_order=order,
                            rag_context=active_context,
                            citations=active_citations,
                            assessment=active_assessment,
                        )
                    )
                    generation_check = {}
                    round_trace.update(
                        {
                            "error": "evidence_insufficient",
                            "assessment": recovered_assessment,
                            "retrieved_chunk_count": len(recovered_chunks),
                            "citation_count": len(recovered_citations),
                        }
                    )
                    self_rag_rounds.append(round_trace)
                    print(
                        f"[EvidenceGate] BLOCK section={title} "
                        "reason=evidence_insufficient_after_self_rag_retrieval",
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
                        generation_attempt=round_number + 1,
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
                    (
                        recovery_context,
                        recovery_chunks,
                        recovery_citations,
                        recovery_output,
                    ) = citation_registry.remap_bundle(
                        context=recovery_context,
                        chunks=recovery_chunks,
                        citations=recovery_citations,
                        normalized=recovery_output,
                        scope="recovery",
                        query=recovery_query,
                    )
                    retrieved_chunks.extend(recovery_chunks)
                    rag_outputs.append(recovery_output)
                    recovery_assessment = self._evidence_assessment(recovery_output)
                    retrieval_metadata["corrective_evidence_assessment"] = (
                        recovery_assessment
                    )
                    if recovery_citations and recovery_context.context_text.strip():
                        if recovery_assessment.get("status") == "insufficient":
                            retrieval_metadata["corrective_generation_skipped"] = (
                                "evidence_insufficient"
                            )
                        else:
                            active_context = recovery_context
                            active_chunks = recovery_chunks
                            active_citations = recovery_citations
                            active_query = recovery_query
                            active_scope = "recovery"
                            active_assessment = recovery_assessment
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
                                    generation_attempt=recovery_count + 1,
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

        retrieval_metadata["evidence_assessment_status"] = active_assessment.get(
            "status", "not_assessed"
        )
        retrieval_metadata["evidence_assessment"] = active_assessment
        contract_sha = str(
            (active_context.extra or {}).get("evidence_contract_sha256") or ""
        ) or None
        evidence = SectionEvidenceBundleSchema(
            section_id=section_id,
            section_title=title,
            retrieval_scope=active_scope,
            query=active_query,
            tool_call_ids=list(dict.fromkeys(tool_call_ids)),
            rag_context=active_context,
            retrieved_chunks=active_chunks,
            citations=active_citations,
            recovery_count=recovery_count,
            evidence_contract_sha256=contract_sha,
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
            budget_usage=budget_usage,
            need_human_review=bool(
                budget_exhaustion is not None
                and section_budget.human_review_on_exhaustion
            ),
            warnings=list(section.warnings),
            error=section.error,
        )
