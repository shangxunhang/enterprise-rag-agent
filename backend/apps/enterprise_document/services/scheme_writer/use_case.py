# =============================================================================
# 中文阅读说明：方案生成用例总编排：文档规划、文档级/章节级检索、逐章生成、恢复检索、组装与硬门禁。
# 主要定义：SchemeGenerationUseCase。建议先从公开入口函数开始，再沿调用关系向下阅读。
# =============================================================================
"""Generated from the stable v7.5.1 SchemeWriter behavior."""



from agent.runtime.shared_state_schema import SharedStateSchema
from agent.runtime.state_access import SharedStateWriter
from apps.enterprise_document.quality.budget import WorkflowBudget, activate_workflow_budget
from apps.enterprise_document.schemas.scheme_writer_schema import (
    DocumentAssemblyRequestSchema,
    SchemeGenerationOptionsSchema,
    SchemeSectionSchema,
    SchemeWriterInputSchema,
    SchemeWriterOutputSchema,
    SectionEvidenceBundleSchema,
    SectionExecutionRequestSchema,
)
from apps.enterprise_document.services.document_gate import evaluate_scheme_draft
from schemas.agent import AgentResultSchema
from schemas.common import ErrorSourceSchema, WarningSchema
from schemas.status import ExecutionStatus
from core.runtime.timing import MonotonicTimer, elapsed_ms
from .capture_service import SchemeCaptureService
from .document_assembler import DocumentAssembler
from .document_citation_registry import DocumentCitationRegistry
from .document_planning_service import DocumentPlanningService
from .evidence_service import SchemeEvidenceService
from .input_service import SchemeInputService
from .runtime_support import SchemeWriterRuntimeSupport
from .section_execution_coordinator import SectionExecutionCoordinator


# 阅读注释（类）：封装 scheme 生成 use case，集中封装相关状态、依赖和行为。
class SchemeGenerationUseCase:
    """封装 scheme 生成 use case，集中封装相关状态、依赖和行为。"""

    def __init__(
        self,
        *,
        input_service: SchemeInputService,
        evidence_service: SchemeEvidenceService,
        section_execution_coordinator: SectionExecutionCoordinator,
        document_assembler: DocumentAssembler,
        document_planning_service: DocumentPlanningService,
        capture_service: SchemeCaptureService,
        runtime_support: SchemeWriterRuntimeSupport,
        agent_name: str,
        agent_type: str,
        rag_service_name: str,
        enable_semantic_gate: bool,
    ) -> None:
        self.input_service = input_service
        self.evidence_service = evidence_service
        self.section_execution_coordinator = section_execution_coordinator
        self.document_assembler = document_assembler
        self.document_planning_service = document_planning_service
        self.capture_service = capture_service
        self.runtime_support = runtime_support
        self.agent_name = agent_name
        self.agent_type = agent_type
        self.rag_service_name = rag_service_name
        self.enable_semantic_gate = enable_semantic_gate

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

    # 阅读注释（函数）：执行 SchemeGenerationUseCase 的主流程。
    def run(self, shared_state: SharedStateSchema) -> AgentResultSchema:
        """执行 SchemeGenerationUseCase 的主流程。

        参数:
            shared_state: shared 状态，具体约束请结合类型标注和调用方确认。

        返回:
            AgentResultSchema

        阅读提示:
            主要直接调用：self.runtime_support.now_iso, SharedStateWriter, self.input_service.read_inputs, list, ValueError, SchemeGenerationOptionsSchema, self.document_planning_service.build_document_plan, print。
        """
        started_at = self.runtime_support.now_iso()
        state_writer = SharedStateWriter()
        try:
            # 阶段 A：从 Workflow 共享状态读取并校验规范化后的项目输入、表格分析和结构化事实。
            project_input, table_analysis, structured_facts = self.input_service.read_inputs(
                shared_state
            )
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
            # 阶段 B：根据 required_sections 构建确定性的 DocumentPlan；当前不是 LLM 自主规划章节。
            document_plan = self.document_planning_service.build_document_plan(
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
                f"[RAG] START query={project_input.user_query[:80]!r}",
                flush=True,
            )
            timer = MonotonicTimer()
            rag_started = timer.now()
            # 阶段 C：执行一次文档级 RAG，为整篇方案取得可复用的通用证据。
            # 文档级检索发生在 section quality loop 之前，因此必须拥有独立的
            # auxiliary model-call safety fuse，避免 Query Rewrite / HyDE / CRAG /
            # Corrective Planner 逃逸出 WorkflowBudget 统计范围。
            document_budget_config = {
                "max_retrieval_rounds": 4,
                "max_rewrite_rounds": 2,
                "max_total_llm_calls": 16,
                "max_total_tokens": 32000,
                "human_review_on_exhaustion": True,
                **dict(
                    project_input.generation_requirements.extra.get(
                        "document_rag_budget", {}
                    )
                    or {}
                ),
            }
            document_rag_budget = WorkflowBudget.from_policy_metadata(
                document_budget_config
            )
            with activate_workflow_budget(document_rag_budget):
                rag_result = self.evidence_service.retrieve(
                    shared_state, project_input
                )
            rag_elapsed_ms = elapsed_ms(timer, rag_started)
            print(
                f"[RAG] END   success={bool(rag_result and rag_result.success)} latency_ms={rag_elapsed_ms}",
                flush=True,
            )
            rag_context, chunks, citations, rag_output = self.evidence_service.extract_rag_output(
                shared_state, rag_result
            )
            rag_output.setdefault("extra", {})["document_rag_model_budget"] = (
                document_rag_budget.snapshot()
            )
            # 创建全局引用注册表，把文档级和章节级检索结果映射到稳定且不冲突的引用编号。
            citation_registry = DocumentCitationRegistry()
            rag_context, chunks, citations, rag_output = citation_registry.remap_bundle(
                context=rag_context,
                chunks=chunks,
                citations=citations,
                normalized=rag_output,
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
                    self.rag_service_name == "RAGService",
                )
            )
            corrective_retrieval_enabled = bool(
                project_input.generation_requirements.extra.get(
                    "enable_corrective_section_retrieval",
                    section_retrieval_enabled,
                )
            )
            evidence_contract = rag_output.get("evidence") or {}
            evidence_assessment = self._evidence_assessment(rag_output)
            assessment_status = evidence_assessment["status"]
            raw_evidence_available = evidence_assessment.get("evidence_available")
            evidence_available = bool(
                raw_evidence_available
                if raw_evidence_available is not None
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
                error = underlying or self.runtime_support.error(
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
                    finished_at=self.runtime_support.now_iso(),
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
            print(f"[SchemeWriter] 开始逐章节生成，共 {total_sections} 章", flush=True)
            # 阶段 D：逐章节执行。章节内部的检索、生成与恢复闭环由唯一 Coordinator 持有。
            document_tool_call_ids = (
                [rag_result.retrieval_trace_id] if rag_result is not None else []
            )
            for section_plan in document_plan.sections:
                section_result = self.section_execution_coordinator.execute(
                    SectionExecutionRequestSchema(
                        shared_state=shared_state,
                        document_id=document_id,
                        project_input=project_input,
                        section_plan=section_plan,
                        structured_facts=list(structured_facts),
                        previous_sections=list(sections),
                        document_rag_context=rag_context,
                        document_retrieved_chunks=list(chunks),
                        document_citations=list(citations),
                        document_evidence_assessment=dict(evidence_assessment),
                        document_tool_call_ids=list(document_tool_call_ids),
                        section_retrieval_enabled=section_retrieval_enabled,
                        corrective_retrieval_enabled=corrective_retrieval_enabled,
                    ),
                    citation_registry=citation_registry,
                )
                sections.append(section_result.section)
                section_evidence_bundles.append(section_result.evidence)
                all_chunks.extend(section_result.retrieved_chunks)
                all_rag_outputs.extend(section_result.rag_outputs)

            # 阶段 E：确定性文档聚合。这里只把已完成章节和证据整理为完整 Draft，
            # 不调用 RAG/LLM、不写 SharedState，也不执行最终 Hard Gate。
            assembly = self.document_assembler.assemble(
                DocumentAssemblyRequestSchema(
                    task_id=shared_state.task_id,
                    run_id=shared_state.run_id,
                    document_id=document_id,
                    document_title=(
                        project_input.output_schema.document_title or "项目建设方案"
                    ),
                    required_sections=list(required_sections),
                    sections=list(sections),
                    retrieved_chunks=list(all_chunks),
                    citations=citation_registry.all(),
                    section_evidence=list(section_evidence_bundles),
                    document_evidence_available=evidence_available,
                    document_assessment_status=assessment_status,
                    citation_required_sections=list(
                        options.citation_required_sections
                    ),
                    created_at=started_at,
                    updated_at=self.runtime_support.now_iso(),
                )
            )
            draft = assembly.draft
            chunks = list(assembly.retrieved_chunks)
            citations = list(assembly.citations)
            bindings = list(assembly.citation_bindings)
            known_chunk_ids = set(assembly.known_chunk_ids)
            semantic_evidence_sufficient = assembly.semantic_evidence_sufficient
            # 阶段 F：文档级硬门禁，检查章节完整性、引用约束、关键字段和输出 Schema。
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
                evidence_sufficient=semantic_evidence_sufficient,
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
                output_error = self.runtime_support.error(
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
                    created_at=self.runtime_support.now_iso(),
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
            # 阶段 G：沉淀任务输入、检索证据、章节结果和质量信息，为评测及后训练数据构建提供原始记录。
            self.capture_service.capture(
                shared_state, scheme_input, output, enriched_rag_output
            )

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
                finished_at=self.runtime_support.now_iso(),
                need_human_review=True,
                metadata={
                    "output_schema": output.schema_version,
                    "hard_gate_passed": hard_gate.passed,
                    "section_count": len(sections),
                    "citation_binding_count": len(bindings),
                },
            )
        except Exception as exc:
            error = self.runtime_support.error(
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
                finished_at=self.runtime_support.now_iso(),
                need_human_review=True,
            )
