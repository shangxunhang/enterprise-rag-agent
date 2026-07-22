# =============================================================================
# 中文阅读说明：方案章节生成主流程：组装上下文、调用模型、处理截断、绑定引用，并执行 Self-RAG 检查与修复。
# 主要定义：SectionGenerationService。建议先从公开入口函数开始，再沿调用关系向下阅读。
# =============================================================================
"""Generated from the stable v7.5.1 SchemeWriter behavior."""


from typing import Any, Dict, List, Optional, Tuple

from agent.runtime.shared_state_schema import SharedStateSchema
from apps.enterprise_document.quality.budget import current_workflow_budget
from apps.enterprise_document.schemas.project_input_schema import ProjectInputSchema
from apps.enterprise_document.schemas.scheme_writer_schema import SemanticGateResultSchema, SchemeSectionSchema, SectionEvalSchema
from apps.enterprise_document.schemas.table_agent_schema import StructuredFactSchema
from apps.enterprise_document.services.output_validation import detect_truncation
from schemas.citation import CitationSchema
from schemas.common import ErrorSourceSchema, WarningSchema
from schemas.model import ModelResponseSchema
from schemas.rag import RAGContextSchema
from schemas.status import ExecutionStatus
from apps.enterprise_document.services.semantic_section_judge import SemanticSectionJudge
from .advisory_service import SectionAdvisoryService
from .citation_service import CitationService
from .constants import CITATION_PATTERN as _CITATION_PATTERN
from .grounding_repair_service import GroundingRepairService
from .model_service import SectionModelService
from .prompt_service import SectionPromptService
from .runtime_support import SchemeWriterRuntimeSupport
from .section_content_recovery import SectionContentRecovery
from .section_quality_evaluator import SectionQualityEvaluator


# 阅读注释（类）：封装 章节 生成 服务，封装一组可复用的业务能力。
class SectionGenerationService:
    """封装 章节 生成 服务，封装一组可复用的业务能力。"""

    def __init__(
        self,
        *,
        runtime_support: SchemeWriterRuntimeSupport,
        prompt_service: SectionPromptService,
        model_service: SectionModelService,
        citation_service: CitationService,
        grounding_repair_service: GroundingRepairService,
        advisory_service: SectionAdvisoryService,
        semantic_judge: SemanticSectionJudge,
        enable_semantic_gate: bool,
        semantic_gate_model_name: str,
        generation_checker: object | None,
        repair_strategy: object | None,
        generation_quality_metadata: Dict[str, Any] | None,
    ) -> None:
        self.runtime_support = runtime_support
        self.prompt_service = prompt_service
        self.model_service = model_service
        self.citation_service = citation_service
        self.grounding_repair_service = grounding_repair_service
        self.advisory_service = advisory_service
        self.semantic_judge = semantic_judge
        self.enable_semantic_gate = enable_semantic_gate
        self.semantic_gate_model_name = semantic_gate_model_name
        self.generation_checker = generation_checker
        self.repair_strategy = repair_strategy
        self.generation_quality_metadata = dict(
            generation_quality_metadata or {}
        )
        self.content_recovery = SectionContentRecovery(
            model_service=self.model_service,
            prompt_service=self.prompt_service,
        )
        self.quality_evaluator = SectionQualityEvaluator(
            runtime_support=self.runtime_support,
        )

    def build_insufficient_evidence_section(
        self,
        shared_state: SharedStateSchema,
        *,
        project_input: ProjectInputSchema,
        section_title: str,
        section_order: int,
        rag_context: RAGContextSchema,
        citations: List[CitationSchema],
        assessment: Dict[str, Any] | None = None,
    ) -> SchemeSectionSchema:
        """Fail closed when an evidence-required section lacks sufficient evidence."""

        started_at = self.runtime_support.now_iso()
        section_id = f"section_{shared_state.run_id}_{section_order:03d}"
        assessment = dict(assessment or {})
        reason = str(
            assessment.get("reason")
            or assessment.get("details", {}).get("final_assessment", {}).get("reason")
            or "检索与纠正检索后仍未获得足以支撑本章节的可靠证据"
        ).strip()
        content = (
            f"现有知识库证据不足，无法在不引入未经证实信息的前提下可靠编写“{section_title}”章节。"
            "请补充与本章节直接相关的项目资料、政策依据、技术规范或经授权知识库内容后重新生成。"
        )
        truncation = detect_truncation(
            content,
            "stop",
            project_input.generation_requirements.min_section_chars,
        )
        warning_name = "evidence_insufficient:normal_generation_blocked"
        warning = WarningSchema(
            warning_code="SECTION_EVIDENCE_INSUFFICIENT",
            message=warning_name,
            source=ErrorSourceSchema(
                component="SchemeWriterAgent",
                agent_name="SchemeWriterAgent",
                step_name=section_id,
            ),
            details={
                "section_title": section_title,
                "assessment": assessment,
                "reason": reason,
            },
            created_at=self.runtime_support.now_iso(),
        )
        return SchemeSectionSchema(
            section_id=section_id,
            section_title=section_title,
            section_order=section_order,
            input={
                "project_input": project_input.model_dump(),
                "rag_context": rag_context.model_dump(),
                "available_citation_ids": [item.citation_id for item in citations],
                "evidence_assessment": assessment,
            },
            prompt="",
            model_output="",
            content=content,
            status=ExecutionStatus.PARTIAL_SUCCESS,
            citation_ids=[],
            citation_bindings=[],
            truncation=truncation,
            eval_result=SectionEvalSchema(
                passed=True,
                checks={
                    "evidence_sufficient": False,
                    "normal_generation_blocked": True,
                    "content_nonempty": True,
                    "not_truncated": not truncation.truncated,
                    "citation_bound": False,
                },
                failures=[],
                warnings=[warning_name],
            ),
            warnings=[warning],
            started_at=started_at,
            finished_at=self.runtime_support.now_iso(),
            extra={
                "generation_blocked": True,
                "generation_block_reason": "evidence_insufficient",
                "evidence_assessment": assessment,
            },
        )

    # 阅读注释（函数）：生成 章节。
    def generate_section(
        self,
        shared_state: SharedStateSchema,
        *,
        document_id: str,
        project_input: ProjectInputSchema,
        section_title: str,
        section_order: int,
        rag_context: RAGContextSchema,
        citations: List[CitationSchema],
        structured_facts: List[StructuredFactSchema],
        previous_sections: List[SchemeSectionSchema],
        generation_attempt: int = 1,
    ) -> SchemeSectionSchema:
        """生成 章节。

        参数:
            shared_state: shared 状态，具体约束请结合类型标注和调用方确认。
            document_id: 文档 标识，具体约束请结合类型标注和调用方确认。
            project_input: 规范化后的项目输入。
            section_title: 章节 title，具体约束请结合类型标注和调用方确认。
            section_order: 章节 order，具体约束请结合类型标注和调用方确认。
            rag_context: RAG 上下文，具体约束请结合类型标注和调用方确认。
            citations: 引用信息集合。
            structured_facts: structured facts，具体约束请结合类型标注和调用方确认。
            previous_sections: previous sections，具体约束请结合类型标注和调用方确认。
            generation_attempt: 生成 attempt，具体约束请结合类型标注和调用方确认。

        返回:
            SchemeSectionSchema

        阅读提示:
            主要直接调用：self.runtime_support.now_iso, self.prompt_service.render_section_prompt, dict, get, self.model_service.call_model, self.runtime_support.error, SchemeSectionSchema, project_input.model_dump。
        """
        started_at = self.runtime_support.now_iso()
        section_id = f"section_{shared_state.run_id}_{section_order:03d}"
        model_section_id = (
            section_id
            if generation_attempt <= 1
            else f"{section_id}_attempt_{generation_attempt}"
        )
        # 生成阶段 1：组装章节边界、项目事实、RAG 证据和历史章节，并渲染 Prompt。
        prompt_result = self.prompt_service.render_section_prompt(
            shared_state,
            project_input,
            section_id,
            section_title,
            section_order,
            rag_context,
            citations,
            previous_sections,
        )
        context_package = dict(
            (prompt_result.extra or {}).get("llm_context_package") or {}
        )
        # 生成阶段 2：通过 ModelGateway 调用 LLM 生成章节。
        response = self.model_service.call_model(
            shared_state,
            prompt=prompt_result.rendered_text,
            section_id=model_section_id,
            section_title=section_title,
            project_input=project_input,
            available_citation_ids=[item.citation_id for item in citations],
            context_package=context_package,
            prompt_id=prompt_result.prompt_id,
            prompt_version=prompt_result.prompt_version,
        )
        if not response.success:
            error = response.error or self.runtime_support.error(
                "SECTION_MODEL_CALL_FAILED",
                response.error_message or "model call failed",
                node=section_id,
                retryable=True,
                user_message=f"‘{section_title}’章节生成失败。",
            )
            return SchemeSectionSchema(
                section_id=section_id,
                section_title=section_title,
                section_order=section_order,
                input={
                    "project_input": project_input.model_dump(),
                    "llm_context_package": context_package,
                },
                prompt=prompt_result.rendered_text,
                model_output=response.content,
                content=response.content,
                status=ExecutionStatus.RETRYABLE_FAILED,
                error=error,
                truncation=detect_truncation("", response.finish_reason),
                eval_result=SectionEvalSchema(
                    passed=False,
                    checks={"model_success": False},
                    failures=["模型调用失败"],
                ),
                started_at=started_at,
                finished_at=self.runtime_support.now_iso(),
            )

        continuation_response: Optional[ModelResponseSchema] = None
        recovery = self.content_recovery.recover(
            shared_state,
            response=response,
            model_section_id=model_section_id,
            section_title=section_title,
            project_input=project_input,
            citations=citations,
            rag_context=rag_context,
        )
        content = recovery.content
        truncation = recovery.truncation
        truncation_retry_responses = recovery.truncation_retry_responses
        truncation_recovery_strategy = recovery.truncation_recovery_strategy
        compression_response = recovery.compression_response
        compression_fallback_strategy = recovery.compression_fallback_strategy
        target_section_chars = recovery.target_section_chars
        max_section_chars = recovery.max_section_chars
        overlong = recovery.overlong

        # Semantic review is optional and advisory only in stage 1.  The core
        # runtime must not spend one extra model call per section, rewrite the
        # generated prose, or fail a document because a vertical-domain judge
        # disagrees with the content.  When explicitly enabled, its output is
        # recorded as warnings/data-capture for later policy work.
        required_section_titles = list(
            dict.fromkeys(
                project_input.generation_requirements.required_sections
                or project_input.output_schema.required_sections
            )
        )
        validation_rewrite_response: Optional[ModelResponseSchema] = None
        semantic_gate_recheck_response: Optional[ModelResponseSchema] = None
        deterministic_fact_candidates: list[Dict[str, Any]] = []
        semantic_gate_response: Optional[ModelResponseSchema] = None
        if self.enable_semantic_gate:
            deterministic_fact_candidates = self.advisory_service.project_fact_violations(
                content, project_input, citations
            )
            print(
                f"[SemanticGate] START section={section_title} "
                f"model={self.semantic_gate_model_name} "
                f"candidates={len(deterministic_fact_candidates)} overlong={overlong}",
                flush=True,
            )
            semantic_gate, semantic_gate_response = self.semantic_judge.judge(
                task_id=shared_state.task_id,
                run_id=shared_state.run_id,
                created_at=self.runtime_support.now_iso(),
                section_id=model_section_id,
                section_title=section_title,
                content=content,
                project_input=project_input,
                citations=citations,
                required_sections=required_section_titles,
                deterministic_candidates=deterministic_fact_candidates,
                overlong=overlong,
            )
            if semantic_gate_response is not None:
                shared_state.generated_outputs[
                    semantic_gate_response.model_call_id
                ] = semantic_gate_response.model_dump()
            print(
                f"[SemanticGate] END   section={section_title} "
                f"decision={semantic_gate.decision} issues={len(semantic_gate.issues)} "
                f"fallback={semantic_gate.fallback_used} advisory_only=True",
                flush=True,
            )
        else:
            semantic_gate = SemanticGateResultSchema(
                decision="pass",
                summary="semantic gate disabled for stage-1 minimal hard gate",
                fallback_used=True,
            )
            print(
                f"[SemanticGate] SKIP  section={section_title} advisory_only=True",
                flush=True,
            )

        semantic_gate_evaluated_content = content

        required_citation_sections = set(
            project_input.generation_requirements.citation_required_sections
        )
        citation_required = section_title in required_citation_sections
        citation_repair_response: Optional[ModelResponseSchema] = None
        grounded_regeneration_response: Optional[ModelResponseSchema] = None

        # Never trust model-emitted markers directly. Remove every marker-like
        # token, then reconstruct citations from the strict grounding policy.
        # This also prevents optional sections from displaying a marker that
        # has no corresponding CitationBinding.
        content = _CITATION_PATTERN.sub("", content)
        deterministic_matches: list[Tuple[str, str, float]] = []
        if citations:
            content, deterministic_matches = self.citation_service.insert_deterministic_citations(
                content, citations
            )
        bindings = self.citation_service.supported_bindings(
            self.citation_service.bind_citations(
                document_id=document_id,
                section_id=section_id,
                content=content,
                citations=citations,
            ),
            citations,
        )
        if citation_required or deterministic_matches:
            print(
                f"[CitationLinker] section={section_title} "
                f"deterministic_matches={[(item[0], round(item[2], 4)) for item in deterministic_matches]} "
                f"bindings={len(bindings)}",
                flush=True,
            )

        # Keep the LLM repair only as a final fallback.  Its output is accepted
        # only when the resulting claim-to-evidence bindings pass the same
        # deterministic grounding check.
        if citation_required and not bindings and citations:
            print(
                f"[CitationRepair] START section={section_title} available={len(citations)}",
                flush=True,
            )
            repaired_content, citation_repair_response = self.grounding_repair_service.repair_section_citations(
                shared_state,
                content=content,
                section_id=model_section_id,
                section_title=section_title,
                project_input=project_input,
                citations=citations,
            )
            repaired_bindings = self.citation_service.supported_bindings(
                self.citation_service.bind_citations(
                    document_id=document_id,
                    section_id=section_id,
                    content=repaired_content,
                    citations=citations,
                ),
                citations,
            )
            if repaired_bindings:
                content = repaired_content
                bindings = repaired_bindings
            print(
                f"[CitationRepair] END   section={section_title} bindings={len(bindings)}",
                flush=True,
            )

        # Do not append a copied evidence sentence merely to satisfy the gate.
        # If the prose itself is unsupported, rewrite it under an evidence-only
        # contract.  Failure to produce a grounded rewrite keeps the section
        # failed instead of manufacturing a formal success.
        if (
            citation_required
            and not bindings
            and citations
            and project_input.generation_requirements.max_section_retries > 0
        ):
            print(
                f"[GroundedRegeneration] START section={section_title} "
                f"available={len(citations)}",
                flush=True,
            )
            grounded_regeneration_response = self.grounding_repair_service.regenerate_section_from_evidence(
                shared_state,
                original_content=content,
                section_id=model_section_id,
                section_title=section_title,
                project_input=project_input,
                citations=citations,
            )
            if (
                grounded_regeneration_response.success
                and grounded_regeneration_response.content.strip()
            ):
                candidate_content = grounded_regeneration_response.content.strip()
                candidate_truncation = detect_truncation(
                    candidate_content,
                    grounded_regeneration_response.finish_reason,
                    project_input.generation_requirements.min_section_chars,
                )
                candidate_bindings = self.citation_service.supported_bindings(
                    self.citation_service.bind_citations(
                        document_id=document_id,
                        section_id=section_id,
                        content=candidate_content,
                        citations=citations,
                    ),
                    citations,
                )
                if candidate_bindings and not candidate_truncation.truncated:
                    content = candidate_content
                    bindings = candidate_bindings
                    truncation = candidate_truncation
            print(
                f"[GroundedRegeneration] END   section={section_title} "
                f"bindings={len(bindings)}",
                flush=True,
            )

        # Agent-level Self-RAG must inspect the final section after CitationLinker
        # and any grounding repair.  The configured RepairStrategy gets one local
        # rewrite attempt; rewritten text is never trusted directly and must pass
        # citation rebuilding plus a second checker pass before acceptance.
        generation_check_result: Optional[Dict[str, Any]] = None
        repair_result: Optional[Dict[str, Any]] = None
        repair_recheck_result: Optional[Dict[str, Any]] = None
        repair_accepted = False
        generation_checker = self.generation_checker
        repair_strategy = self.repair_strategy
        quality_query = (
            f"{project_input.user_query}\n当前章节：{section_title}\n"
            f"章节边界：{self.prompt_service.section_generation_contract(section_title, project_input)}"
        )
        citation_payload = [item.model_dump() for item in citations]
        binding_payload = [item.model_dump() for item in bindings]
        quality_runtime_context = {
            "task_id": shared_state.task_id,
            "run_id": shared_state.run_id,
            "section_id": section_id,
            "section_title": section_title,
            "generation_attempt": generation_attempt,
            "caller_agent": "SchemeWriterAgent",
            "shared_state": shared_state,
            "agent_final_section": True,
            "model_extra": {
                "document_id": document_id,
                "document_title": project_input.output_schema.document_title,
            },
        }
        # 生成阶段 5：GroundedGenerationPolicy 启用 Self-RAG-lite 时检查证据支持。
        if generation_checker is not None:
            print(
                f"[GenerationChecker] START section={section_title} "
                f"bindings={len(bindings)}",
                flush=True,
            )
            generation_check_result = generation_checker.check(
                query=quality_query,
                answer=content,
                context=rag_context.context_text,
                citations=citation_payload,
                citation_bindings=binding_payload,
                runtime_context={
                    **quality_runtime_context,
                    "call_suffix": "agent_self_rag_pre",
                },
            )
            if generation_check_result is not None:
                print(
                    f"[GenerationChecker] END   section={section_title} "
                    f"supported={generation_check_result.get('is_supported')} "
                    f"need_rewrite={generation_check_result.get('need_rewrite')} "
                    f"need_retrieve_more={generation_check_result.get('need_retrieve_more')} "
                    f"score={generation_check_result.get('score')}",
                    flush=True,
                )
            else:
                print(
                    f"[GenerationChecker] SKIP  section={section_title} mode=noop",
                    flush=True,
                )

        if (
            repair_strategy is not None
            and generation_check_result is not None
            and bool(generation_check_result.get("need_rewrite"))
            and not bool(generation_check_result.get("need_retrieve_more"))
        ):
            budget = current_workflow_budget()
            if budget is not None:
                budget.consume_rewrite_round()
            print(
                f"[RepairStrategy] START section={section_title}",
                flush=True,
            )
            # 生成阶段 6：need_rewrite 为真时执行局部改写；修复结果不能直接放行。
            repair_output = repair_strategy.repair(
                query=quality_query,
                answer=content,
                context=rag_context.context_text,
                citations=citation_payload,
                citation_bindings=binding_payload,
                check_result=generation_check_result,
                runtime_context={
                    **quality_runtime_context,
                    "call_suffix": "agent_local_rewrite",
                },
            )
            repair_result = dict(repair_output.report or {})
            repair_result["repaired"] = bool(repair_output.repaired)
            if repair_output.repaired and repair_output.answer.strip():
                candidate_content = _CITATION_PATTERN.sub(
                    "", repair_output.answer.strip()
                )
                if citations:
                    candidate_content, _ = self.citation_service.insert_deterministic_citations(
                        candidate_content, citations
                    )
                candidate_bindings = self.citation_service.supported_bindings(
                    self.citation_service.bind_citations(
                        document_id=document_id,
                        section_id=section_id,
                        content=candidate_content,
                        citations=citations,
                    ),
                    citations,
                )
                candidate_truncation = detect_truncation(
                    candidate_content,
                    "stop",
                    project_input.generation_requirements.min_section_chars,
                )
                candidate_citation_ok = (not citation_required) or bool(
                    candidate_bindings
                )
                if (
                    candidate_content.strip()
                    and not candidate_truncation.truncated
                    and candidate_citation_ok
                ):
                    # 改写后重新绑定引用并再次执行 Self-RAG 检查，只有复检通过才接受候选文本。
                    candidate_check = generation_checker.check(
                        query=quality_query,
                        answer=candidate_content,
                        context=rag_context.context_text,
                        citations=citation_payload,
                        citation_bindings=[
                            item.model_dump() for item in candidate_bindings
                        ],
                        runtime_context={
                            **quality_runtime_context,
                            "call_suffix": "agent_self_rag_post",
                        },
                    )
                    repair_recheck_result = candidate_check
                    candidate_supported = (
                        candidate_check is None
                        or (
                            bool(candidate_check.get("is_supported"))
                            and not bool(candidate_check.get("need_rewrite"))
                        )
                    )
                    if candidate_supported:
                        content = candidate_content
                        bindings = candidate_bindings
                        truncation = candidate_truncation
                        generation_check_result = candidate_check
                        repair_accepted = True
                repair_result.update(
                    {
                        "accepted": repair_accepted,
                        "candidate_binding_count": len(candidate_bindings),
                        "candidate_truncated": candidate_truncation.truncated,
                    }
                )
            else:
                repair_result["accepted"] = False
            print(
                f"[RepairStrategy] END   section={section_title} "
                f"repaired={repair_result.get('repaired')} "
                f"accepted={repair_result.get('accepted', False)}",
                flush=True,
            )

        citation_ok = (not citation_required) or bool(bindings)

        # Citation repair / grounded regeneration may have replaced the prose.
        # Re-run the semantic judge only when the non-marker text actually
        # changed, so ordinary deterministic citation insertion adds no extra
        # model call.
        final_semantic_plain = _CITATION_PATTERN.sub("", content).strip()
        evaluated_semantic_plain = _CITATION_PATTERN.sub(
            "", semantic_gate_evaluated_content
        ).strip()
        if self.enable_semantic_gate and final_semantic_plain != evaluated_semantic_plain:
            deterministic_fact_candidates = self.advisory_service.project_fact_violations(
                content, project_input, citations
            )
            semantic_gate, final_semantic_response = self.semantic_judge.judge(
                task_id=shared_state.task_id,
                run_id=shared_state.run_id,
                created_at=self.runtime_support.now_iso(),
                section_id=model_section_id,
                section_title=section_title,
                content=content,
                project_input=project_input,
                citations=citations,
                required_sections=required_section_titles,
                deterministic_candidates=deterministic_fact_candidates,
                overlong=len(content) > max_section_chars,
                call_suffix="_final",
            )
            if final_semantic_response is not None:
                shared_state.generated_outputs[
                    final_semantic_response.model_call_id
                ] = final_semantic_response.model_dump()

        quality = self.quality_evaluator.evaluate(
            section_id=section_id,
            section_title=section_title,
            content=content,
            truncation=truncation,
            max_section_chars=max_section_chars,
            citation_ok=citation_ok,
            semantic_gate=semantic_gate,
            generation_check_result=generation_check_result,
            repair_result=repair_result,
            repair_accepted=repair_accepted,
            truncation_recovery_strategy=truncation_recovery_strategy,
            compression_fallback_strategy=compression_fallback_strategy,
        )
        status = quality.status
        error = quality.error
        section_warnings = quality.warnings
        semantic_scope_issues = quality.semantic_scope_issues

        return SchemeSectionSchema(
            section_id=section_id,
            section_title=section_title,
            section_order=section_order,
            input={
                "project_input": project_input.model_dump(),
                "rag_context": rag_context.model_dump(),
                "available_citation_ids": [item.citation_id for item in citations],
                "llm_context_package": context_package,
            },
            prompt=prompt_result.rendered_text,
            model_output=response.content,
            content=content,
            status=status,
            error=error,
            citation_ids=list(dict.fromkeys(item.citation_id for item in bindings)),
            citation_bindings=bindings,
            source_fact_ids=[item.fact_id for item in structured_facts],
            truncation=truncation,
            eval_result=quality.eval_result,
            warnings=section_warnings,
            started_at=started_at,
            finished_at=self.runtime_support.now_iso(),
            extra={
                "context_package_id": context_package.get("package_id"),
                "context_sha256": context_package.get("context_sha256"),
                "context_budget": context_package.get("budget") or {},
                "context_decisions": context_package.get("decisions") or [],
                "context_warnings": context_package.get("warnings") or [],
                "prompt_id": prompt_result.prompt_id,
                "prompt_version": prompt_result.prompt_version,
                "model_call_id": response.model_call_id,
                "continuation_model_call_id": (
                    continuation_response.model_call_id if continuation_response else None
                ),
                "truncation_retry_model_call_ids": [
                    item.model_call_id for item in truncation_retry_responses
                ],
                "truncation_recovery_strategy": truncation_recovery_strategy,
                "compression_model_call_id": (
                    compression_response.model_call_id if compression_response else None
                ),
                "compression_fallback_strategy": compression_fallback_strategy,
                "generation_attempt": generation_attempt,
                "validation_rewrite_model_call_id": (
                    validation_rewrite_response.model_call_id
                    if validation_rewrite_response
                    else None
                ),
                "citation_repair_model_call_id": (
                    citation_repair_response.model_call_id
                    if citation_repair_response
                    else None
                ),
                "grounded_regeneration_model_call_id": (
                    grounded_regeneration_response.model_call_id
                    if grounded_regeneration_response
                    else None
                ),
                "project_fact_violations": deterministic_fact_candidates,
                "section_scope_violations": [
                    item.model_dump() for item in semantic_scope_issues
                ],
                "semantic_gate": semantic_gate.model_dump(),
                "semantic_gate_model_call_id": semantic_gate.model_call_id,
                "semantic_gate_recheck_model_call_id": (
                    semantic_gate_recheck_response.model_call_id
                    if semantic_gate_recheck_response
                    else None
                ),
                "generation_quality_pipeline": dict(
                    self.generation_quality_metadata
                ),
                "generation_check": generation_check_result,
                "repair_result": repair_result,
                "repair_recheck": repair_recheck_result,
                "repair_accepted": repair_accepted,
                "target_section_chars": target_section_chars,
                "max_section_chars": max_section_chars,
            },
        )
