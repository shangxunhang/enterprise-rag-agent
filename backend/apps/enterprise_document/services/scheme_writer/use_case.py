"""Generated from the stable v7.5.1 SchemeWriter behavior."""



from agent.runtime.shared_state_schema import SharedStateSchema
from agent.runtime.state_access import SharedStateWriter
from apps.enterprise_document.schemas.scheme_writer_schema import (
    SchemeDraftSchema,
    SchemeGenerationOptionsSchema,
    SchemeSectionSchema,
    SchemeWriterInputSchema,
    SchemeWriterOutputSchema,
    SectionEvidenceBundleSchema,
)
from apps.enterprise_document.services.document_gate import evaluate_scheme_draft
from schemas.agent import AgentResultSchema
from schemas.common import ErrorSourceSchema, WarningSchema
from schemas.status import ExecutionStatus
from core.runtime.timing import MonotonicTimer, elapsed_ms
from .base import RuntimeBoundService
from .evidence_service import DocumentCitationRegistry


class SchemeGenerationUseCase(RuntimeBoundService):
    def run(self, shared_state: SharedStateSchema) -> AgentResultSchema:
        started_at = self._now_iso()
        state_writer = SharedStateWriter()
        try:
            project_input, table_analysis, structured_facts = self._read_inputs(shared_state)
            required_sections = list(
                project_input.generation_requirements.required_sections
                or project_input.output_schema.required_sections
            )
            if not required_sections:
                raise ValueError("required_sections is empty in ProjectInput")

            options = SchemeGenerationOptionsSchema(
                need_citation=project_input.generation_requirements.need_citation,
                citation_required_sections=list(
                    project_input.generation_requirements.citation_required_sections
                ),
                need_human_review=project_input.generation_requirements.need_human_review,
                max_context_chars=project_input.generation_requirements.max_context_chars,
                max_section_retries=project_input.generation_requirements.max_section_retries,
                max_tokens_per_section=project_input.generation_requirements.max_tokens_per_section,
                min_section_chars=project_input.generation_requirements.min_section_chars,
            )
            document_id = f"document_{shared_state.run_id}"
            document_plan = self._build_document_plan(
                run_id=shared_state.run_id,
                document_id=document_id,
                project_input=project_input,
                required_sections=required_sections,
                created_at=started_at,
            )
            print(
                f"[DocumentPlan] sections={len(document_plan.sections)} "
                f"source={document_plan.planning_source}",
                flush=True,
            )

            scheme_input = SchemeWriterInputSchema(
                task_id=shared_state.task_id,
                run_id=shared_state.run_id,
                user_input=project_input.user_query,
                requirements=project_input.generation_requirements.model_dump(),
                project_input=project_input,
                table_analysis=table_analysis,
                structured_facts=structured_facts,
                kb_ids=(shared_state.task or {}).get("kb_ids") or [],
                template_id=(shared_state.task or {}).get("template_id"),
                required_sections=required_sections,
                generation_options=options,
            )

            print(
                f"[RAG] START strategy={self.rag_retrieval_mode} query={project_input.user_query[:80]!r}",
                flush=True,
            )
            timer = MonotonicTimer()
            rag_started = timer.now()
            rag_result = self._call_rag_tool(shared_state, project_input)
            rag_elapsed_ms = elapsed_ms(timer, rag_started)
            print(
                f"[RAG] END   success={bool(rag_result and rag_result.success)} latency_ms={rag_elapsed_ms}",
                flush=True,
            )
            rag_context, chunks, citations, rag_output = self._extract_rag_output(
                shared_state, rag_result
            )
            citation_registry = DocumentCitationRegistry()
            rag_context, chunks, citations, rag_output = self._remap_bundle_citations(
                context=rag_context,
                chunks=chunks,
                citations=citations,
                normalized=rag_output,
                registry=citation_registry,
                scope="document",
                query=project_input.user_query,
            )
            print(
                f"[RAG] NORMALIZED chunks={len(chunks)} citations={len(citations)} "
                f"citation_ids={[item.citation_id for item in citations]}",
                flush=True,
            )
            all_chunks = list(chunks)
            all_rag_outputs: list[dict] = [rag_output]
            section_evidence_bundles: list[SectionEvidenceBundleSchema] = []
            section_retrieval_enabled = bool(
                project_input.generation_requirements.extra.get(
                    "enable_section_aware_retrieval",
                    self.rag_tool_name == "RealRAGTool",
                )
            )
            corrective_retrieval_enabled = bool(
                project_input.generation_requirements.extra.get(
                    "enable_corrective_section_retrieval",
                    section_retrieval_enabled,
                )
            )
            evidence_contract = rag_output.get("evidence") or {}
            evidence_assessment = (
                evidence_contract.get("assessment")
                if isinstance(evidence_contract, dict)
                else {}
            ) or {}
            assessment_status = str(
                evidence_assessment.get("status") or "not_assessed"
            )
            evidence_available = bool(
                evidence_assessment.get("evidence_available")
                if isinstance(evidence_assessment, dict)
                else (rag_context.context_item_count and citations)
            )
            semantic_sufficiency = (
                True
                if assessment_status == "sufficient"
                else False
                if assessment_status == "insufficient"
                else None
            )
            state_writer.set_evidence_context(
                shared_state,
                query=project_input.user_query,
                evidence_contract=evidence_contract,
                context_text=rag_context.context_text,
                retrieved_chunks=[item.model_dump() for item in chunks],
                citations=[item.model_dump() for item in citations],
                used_doc_ids=rag_context.used_doc_ids,
                evidence_available=evidence_available,
                assessment_status=assessment_status,
                evidence_sufficient=semantic_sufficiency,
            )

            if rag_result is None or not rag_result.success:
                underlying = rag_result.error if rag_result else None
                error = underlying or self._error(
                    "RAG_TOOL_FAILED",
                    rag_result.error_message if rag_result else "RAG tool is not configured",
                    node="evidence_retrieval",
                    retryable=True,
                    user_message="知识检索失败，文档生成任务已停止。",
                )
                state_writer.add_error(shared_state, error)
                return AgentResultSchema(
                    result_id=f"result_{shared_state.run_id}_scheme_failed",
                    task_id=shared_state.task_id,
                    run_id=shared_state.run_id,
                    agent_name=self.agent_name,
                    agent_type=self.agent_type,
                    status=ExecutionStatus.RETRYABLE_FAILED if error.retryable else ExecutionStatus.FAILED,
                    result_type="scheme_writer_output",
                    result={},
                    error=error,
                    error_message=error.message,
                    started_at=started_at,
                    finished_at=self._now_iso(),
                    need_human_review=True,
                )

            state_writer.initialize_generation(
                shared_state,
                document_id=document_id,
                document_title=project_input.output_schema.document_title,
                required_sections=required_sections,
            )

            sections: list[SchemeSectionSchema] = []
            total_sections = len(required_sections)
            citation_required_titles = set(options.citation_required_sections)
            print(f"[SchemeWriter] 开始逐章节生成，共 {total_sections} 章", flush=True)
            for section_plan in document_plan.sections:
                order = section_plan.section_order
                title = section_plan.section_title
                section_id = f"section_{shared_state.run_id}_{order:03d}"
                citation_required = bool(
                    section_plan.citation_required or title in citation_required_titles
                )
                print(
                    f"[Section {order}/{total_sections}] START {title}",
                    flush=True,
                )
                state_writer.set_current_section(
                    shared_state,
                    section_id=section_id,
                    section_title=title,
                )

                active_context = rag_context
                active_chunks = list(chunks)
                active_citations = list(citations)
                active_query = project_input.user_query
                active_scope = "document"
                tool_call_ids = [rag_result.tool_call_id] if rag_result else []
                recovery_count = 0
                retrieval_metadata: dict = {
                    "section_retrieval_enabled": section_retrieval_enabled,
                    "citation_required": citation_required,
                }

                if section_retrieval_enabled and citation_required:
                    section_query = self._build_section_query(
                        project_input, title, recovery=False
                    )
                    print(
                        f"[SectionRAG] START section={title} scope=section "
                        f"query={section_query[:100]!r}",
                        flush=True,
                    )
                    section_result = self._call_rag_tool(
                        shared_state,
                        project_input,
                        query=section_query,
                        scope="section",
                        section_id=section_id,
                        section_title=title,
                        call_suffix=f"section_{order:03d}",
                    )
                    if section_result is not None:
                        tool_call_ids.append(section_result.tool_call_id)
                    if section_result is not None and section_result.success:
                        section_context, section_chunks, section_citations, section_output = (
                            self._extract_rag_output(shared_state, section_result)
                        )
                        section_context, section_chunks, section_citations, section_output = (
                            self._remap_bundle_citations(
                                context=section_context,
                                chunks=section_chunks,
                                citations=section_citations,
                                normalized=section_output,
                                registry=citation_registry,
                                scope="section",
                                query=section_query,
                            )
                        )
                        all_chunks.extend(section_chunks)
                        all_rag_outputs.append(section_output)
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
                            section_result.error_message if section_result else "not_configured"
                        )
                        print(
                            f"[SectionRAG] END   section={title} success=False "
                            "fallback=document_evidence",
                            flush=True,
                        )

                section = self._generate_section(
                    shared_state,
                    document_id=document_id,
                    project_input=project_input,
                    section_title=title,
                    section_order=order,
                    rag_context=active_context,
                    citations=active_citations,
                    structured_facts=structured_facts,
                    previous_sections=sections,
                    generation_attempt=1,
                )

                citation_bound = bool(
                    section.eval_result
                    and section.eval_result.checks.get("citation_bound", False)
                )
                if (
                    citation_required
                    and not citation_bound
                    and corrective_retrieval_enabled
                ):
                    recovery_count = 1
                    recovery_query = self._build_section_query(
                        project_input, title, recovery=True
                    )
                    print(
                        f"[CorrectiveSectionRAG] START section={title} "
                        f"query={recovery_query[:100]!r}",
                        flush=True,
                    )
                    recovery_result = self._call_rag_tool(
                        shared_state,
                        project_input,
                        query=recovery_query,
                        scope="recovery",
                        section_id=section_id,
                        section_title=title,
                        call_suffix=f"section_{order:03d}_recovery_1",
                    )
                    if recovery_result is not None:
                        tool_call_ids.append(recovery_result.tool_call_id)
                    if recovery_result is not None and recovery_result.success:
                        recovery_context, recovery_chunks, recovery_citations, recovery_output = (
                            self._extract_rag_output(shared_state, recovery_result)
                        )
                        recovery_context, recovery_chunks, recovery_citations, recovery_output = (
                            self._remap_bundle_citations(
                                context=recovery_context,
                                chunks=recovery_chunks,
                                citations=recovery_citations,
                                normalized=recovery_output,
                                registry=citation_registry,
                                scope="recovery",
                                query=recovery_query,
                            )
                        )
                        all_chunks.extend(recovery_chunks)
                        all_rag_outputs.append(recovery_output)
                        if recovery_citations and recovery_context.context_text.strip():
                            active_context = recovery_context
                            active_chunks = recovery_chunks
                            active_citations = recovery_citations
                            active_query = recovery_query
                            active_scope = "recovery"
                            section = self._generate_section(
                                shared_state,
                                document_id=document_id,
                                project_input=project_input,
                                section_title=title,
                                section_order=order,
                                rag_context=active_context,
                                citations=active_citations,
                                structured_facts=structured_facts,
                                previous_sections=sections,
                                generation_attempt=2,
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
                            recovery_result.error_message if recovery_result else "not_configured"
                        )
                        print(
                            f"[CorrectiveSectionRAG] END   section={title} success=False",
                            flush=True,
                        )

                contract_sha = str(
                    (active_context.extra or {}).get("evidence_contract_sha256") or ""
                ) or None
                section_evidence_bundles.append(
                    SectionEvidenceBundleSchema(
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
                )
                section.extra = {
                    **dict(section.extra or {}),
                    "evidence_scope": active_scope,
                    "evidence_query": active_query,
                    "evidence_tool_call_ids": list(dict.fromkeys(tool_call_ids)),
                    "evidence_contract_sha256": contract_sha,
                    "corrective_retrieval_count": recovery_count,
                }
                sections.append(section)
                print(
                    f"[Section {order}/{total_sections}] END   {title} "
                    f"status={section.status.value if hasattr(section.status, 'value') else section.status} "
                    f"chars={len(section.content)} truncated={section.truncation.truncated} "
                    f"citations={len(section.citation_bindings)} evidence_scope={active_scope}",
                    flush=True,
                )
                state_writer.add_generated_section(shared_state, section.section_id)

            missing_sections = [
                title
                for title in required_sections
                if title not in {section.section_title for section in sections}
            ]
            full_text = "\n\n".join(
                f"{index}、{section.section_title}\n{section.content}"
                for index, section in enumerate(sections, start=1)
            )
            bindings = [binding for section in sections for binding in section.citation_bindings]
            citations = citation_registry.all()
            deduplicated_chunks = []
            seen_chunk_keys = set()
            for chunk in all_chunks:
                chunk_key = (
                    chunk.matched_chunk_id
                    or chunk.context_chunk_id
                    or chunk.child_chunk_id
                    or chunk.parent_chunk_id
                    or f"{chunk.doc_id}:{len(deduplicated_chunks)}"
                )
                if chunk_key in seen_chunk_keys:
                    continue
                seen_chunk_keys.add(chunk_key)
                deduplicated_chunks.append(chunk)
            chunks = deduplicated_chunks
            draft = SchemeDraftSchema(
                draft_id=f"draft_{shared_state.run_id}_scheme",
                document_id=document_id,
                task_id=shared_state.task_id,
                run_id=shared_state.run_id,
                title=project_input.output_schema.document_title or "项目建设方案",
                full_text=full_text,
                sections=sections,
                required_sections=required_sections,
                missing_sections=missing_sections,
                citation_bindings=bindings,
                truncation_detected=any(item.truncation.truncated for item in sections),
                summary=f"共生成{len(sections)}个章节。",
                created_at=started_at,
                updated_at=self._now_iso(),
            )
            known_chunk_ids = {
                value
                for chunk in chunks
                for value in (
                    chunk.matched_chunk_id,
                    chunk.context_chunk_id,
                    chunk.child_chunk_id,
                    chunk.parent_chunk_id,
                )
                if value
            }
            for chunk in chunks:
                known_chunk_ids.update(
                    str(item)
                    for item in (
                        (chunk.metadata or {}).get("matched_child_chunk_ids") or []
                    )
                    if item
                )
            evidence_available = bool(
                evidence_available
                or any(bundle.citations for bundle in section_evidence_bundles)
            )
            hard_gate = evaluate_scheme_draft(
                draft,
                citation_required=options.need_citation,
                citation_required_sections=options.citation_required_sections,
                retrieved_chunk_ids=known_chunk_ids,
                tool_failed=False,
                key_fields_valid=bool(
                    project_input.task_id
                    and project_input.task_type
                    and project_input.user_query
                    and required_sections
                ),
                output_schema_valid=bool(draft.full_text and draft.sections),
                # Existing hard gate checks evidence presence. Semantic sufficiency
                # remains "not_assessed" until the evidence grader stage.
                evidence_sufficient=evidence_available,
                workflow_complete=True,
            )
            print(
                f"[HardGate] passed={hard_gate.passed} failures={hard_gate.failures} "
                f"warnings={hard_gate.warnings}",
                flush=True,
            )
            has_partial_sections = any(
                section.status == ExecutionStatus.PARTIAL_SUCCESS
                for section in sections
            )
            if not hard_gate.passed:
                status = ExecutionStatus.FAILED
            elif has_partial_sections or hard_gate.warnings:
                status = ExecutionStatus.PARTIAL_SUCCESS
            else:
                status = ExecutionStatus.SUCCESS
            output_error = None
            if not hard_gate.passed:
                output_error = self._error(
                    "DOCUMENT_HARD_GATE_FAILED",
                    "; ".join(hard_gate.failures),
                    node="document_hard_gate",
                    retryable=True,
                    user_message="文档未通过强制质量校验，不能标记为成功。",
                )
                state_writer.add_error(shared_state, output_error)

            output_warnings = [
                WarningSchema(
                    warning_code="DOCUMENT_SOFT_GATE_WARNING",
                    message=message,
                    source=ErrorSourceSchema(
                        component="SchemeWriterAgent",
                        agent_name="SchemeWriterAgent",
                        step_name="document_hard_gate",
                    ),
                    created_at=self._now_iso(),
                )
                for message in hard_gate.warnings
            ]

            output = SchemeWriterOutputSchema(
                task_id=shared_state.task_id,
                run_id=shared_state.run_id,
                status=status,
                document_plan=document_plan,
                scheme_draft=draft,
                rag_context=rag_context,
                retrieved_chunks=chunks,
                citations=citations,
                section_evidence=section_evidence_bundles,
                hard_gate=hard_gate,
                warnings=output_warnings,
                error=output_error,
                need_human_review=True,
                extra={
                    "scheme_writer_input": scheme_input.model_dump(),
                    "document_plan": document_plan.model_dump(),
                    "rag_tool_output": rag_output,
                    "rag_tool_outputs": all_rag_outputs,
                    "section_evidence": [
                        item.model_dump() for item in section_evidence_bundles
                    ],
                    "section_aware_retrieval_enabled": section_retrieval_enabled,
                    "corrective_section_retrieval_enabled": corrective_retrieval_enabled,
                    "section_count": len(sections),
                    "citation_binding_count": len(bindings),
                    "partial_section_count": sum(
                        1 for section in sections
                        if section.status == ExecutionStatus.PARTIAL_SUCCESS
                    ),
                    "semantic_gate_enabled": self.enable_semantic_gate,
                },
            )
            enriched_rag_output = {
                **rag_output,
                "section_evidence": [
                    item.model_dump() for item in section_evidence_bundles
                ],
                "rag_tool_outputs": all_rag_outputs,
            }
            state_writer.set_scheme_outputs(
                shared_state,
                scheme_writer_input=scheme_input.model_dump(),
                scheme_writer_output=output.model_dump(),
                rag_tool_output=enriched_rag_output,
            )
            state_writer.set_final_result(shared_state, output.model_dump())
            self._capture(shared_state, scheme_input, output, enriched_rag_output)

            return AgentResultSchema(
                result_id=f"result_{shared_state.run_id}_scheme",
                task_id=shared_state.task_id,
                run_id=shared_state.run_id,
                agent_name=self.agent_name,
                agent_type=self.agent_type,
                status=status,
                result_type="scheme_writer_output",
                result={
                    "scheme_writer_output": output.model_dump(),
                    "scheme_draft": draft.model_dump(),
                },
                citations=citations,
                error=output_error,
                error_message=output_error.message if output_error else None,
                started_at=started_at,
                finished_at=self._now_iso(),
                need_human_review=True,
                metadata={
                    "output_schema": output.schema_version,
                    "hard_gate_passed": hard_gate.passed,
                    "section_count": len(sections),
                    "citation_binding_count": len(bindings),
                },
            )
        except Exception as exc:
            error = self._error(
                "SCHEME_WRITER_FAILED",
                exc,
                node=shared_state.current_step or self.agent_name,
                retryable=False,
                user_message="方案生成主链路发生错误。",
            )
            state_writer.add_error(shared_state, error)
            return AgentResultSchema(
                result_id=f"result_{shared_state.run_id}_scheme_exception",
                task_id=shared_state.task_id,
                run_id=shared_state.run_id,
                agent_name=self.agent_name,
                agent_type=self.agent_type,
                status=ExecutionStatus.FAILED,
                result_type="scheme_writer_output",
                result={},
                error=error,
                error_message=error.message,
                started_at=started_at,
                finished_at=self._now_iso(),
                need_human_review=True,
            )
