"""通过 Context Manager 构造章节生成 Prompt 与可审计上下文包。

本服务先定义章节边界和候选上下文，再根据目标模型安全容量构建
``LLMContextPackageSchema``，最后交给版本化 Prompt 模板渲染。Prompt 文本只是投影，
结构化上下文包及其哈希才是复现本次模型输入选择的依据。
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, Dict, Iterable, List, Tuple

from agent.runtime.shared_state_schema import SharedStateSchema
from apps.enterprise_document.schemas.project_input_schema import ProjectInputSchema
from apps.enterprise_document.schemas.scheme_writer_schema import SchemeSectionSchema
from apps.enterprise_document.schemas.table_agent_schema import StructuredFactSchema
from apps.enterprise_document.services.project_input_normalization.input_contract import (
    requested_target_section_chars as project_requested_target_section_chars,
)
from context_manager import LLMContextManager, SectionGenerationContextPolicy
from context_manager.manager import ContextBudgetExceededError
from contracts.evidence import evidence_bundle_sha256, project_evidence_bundle
from model_gateway.model_contract import ModelRole
from prompt_manager.prompt_manager import PromptManager
from schemas.citation import CitationSchema
from schemas.citation import prompt_visible_citation_text
from schemas.context import ContextItemSchema, LLMContextPackageSchema
from schemas.prompt import PromptRenderResultSchema
from schemas.rag import EvidenceBundleSchema, RAGContextSchema


class TargetPromptContextIntegrityError(ValueError):
    """Target Prompt inputs cannot form one coherent managed context package."""


class TargetTechnicalRecoveryIntegrityError(ValueError):
    """A Target technical retry cannot preserve its locked semantic context."""


@dataclass(frozen=True)
class TargetTechnicalRecoveryContext:
    """Immutable technical carrier for reusing one Target Prompt snapshot.

    This is not a canonical business schema or an invocation authority. It keeps
    the already-rendered semantic/model context stable for bounded retry and
    compression mechanics.
    """

    initial_prompt_text: str
    prompt_id: str
    prompt_version: str
    context_package_json: str
    context_package_id: str
    context_sha256: str
    writer_evidence_semantics_json: str
    structured_writing_facts_json: str | None
    citation_ids: Tuple[str, ...]
    evidence_bundle_sha256: str
    task_id: str
    run_id: str
    section_id: str
    section_title: str
    section_order: int


def assert_target_recovery_semantic_items(
    package: LLMContextPackageSchema,
    recovery_context: TargetTechnicalRecoveryContext,
) -> None:
    """Prove mandatory locked semantic items survived a technical projection."""

    selected = {item.item_id: item for item in package.selected_items}
    semantics = selected.get("writer_evidence_semantics")
    if (
        semantics is None
        or not semantics.required
        or semantics.truncate_allowed
        or semantics.content != recovery_context.writer_evidence_semantics_json
    ):
        raise TargetTechnicalRecoveryIntegrityError(
            "Target recovery lost mandatory writer Evidence semantics"
        )
    structured = selected.get("structured_writing_facts")
    if recovery_context.structured_writing_facts_json is None:
        if structured is not None:
            raise TargetTechnicalRecoveryIntegrityError(
                "Target recovery introduced unlocked structured writing facts"
            )
    elif (
        structured is None
        or not structured.required
        or structured.truncate_allowed
        or structured.content != recovery_context.structured_writing_facts_json
    ):
        raise TargetTechnicalRecoveryIntegrityError(
            "Target recovery lost locked structured writing facts"
        )


def materialize_target_technical_recovery_context(
    recovery_context: TargetTechnicalRecoveryContext,
) -> LLMContextPackageSchema:
    """Validate and materialize an immutable Target technical context lock."""

    try:
        package = LLMContextPackageSchema.model_validate_json(
            recovery_context.context_package_json
        )
    except Exception as exc:
        raise TargetTechnicalRecoveryIntegrityError(
            "locked Target context package is not structurally valid"
        ) from exc
    expected_fields = {
        "package_id": recovery_context.context_package_id,
        "context_sha256": recovery_context.context_sha256,
        "task_id": recovery_context.task_id,
        "run_id": recovery_context.run_id,
        "section_id": recovery_context.section_id,
        "section_title": recovery_context.section_title,
    }
    for field_name, expected in expected_fields.items():
        if getattr(package, field_name) != expected:
            raise TargetTechnicalRecoveryIntegrityError(
                f"locked Target context {field_name} mismatch"
            )
    lineage = dict(package.lineage or {})
    expected_lineage = {
        "evidence_bundle_sha256": recovery_context.evidence_bundle_sha256,
        "task_id": recovery_context.task_id,
        "run_id": recovery_context.run_id,
        "section_id": recovery_context.section_id,
        "section_title": recovery_context.section_title,
        "section_order": recovery_context.section_order,
    }
    for field_name, expected in expected_lineage.items():
        if lineage.get(field_name) != expected:
            raise TargetTechnicalRecoveryIntegrityError(
                f"locked Target lineage {field_name} mismatch"
            )
    assert_target_recovery_semantic_items(package, recovery_context)
    if package.rendered_context not in recovery_context.initial_prompt_text:
        raise TargetTechnicalRecoveryIntegrityError(
            "locked Target Prompt does not contain its managed semantic context"
        )
    return package


@dataclass(frozen=True)
class TargetInitialSectionPromptResult:
    """Unwired Target Prompt plus its canonical Evidence-derived projections."""

    prompt_result: PromptRenderResultSchema
    context_package: LLMContextPackageSchema
    rag_context: RAGContextSchema
    citations: Tuple[CitationSchema, ...]
    evidence_bundle_sha256: str
    section_id: str
    section_title: str
    section_order: int
    technical_recovery_context: TargetTechnicalRecoveryContext


class SectionPromptService:
    """负责章节边界、引用目录、上下文预算和最终 Prompt 渲染。"""

    def __init__(
        self,
        *,
        context_manager: LLMContextManager,
        context_policy: SectionGenerationContextPolicy,
        prompt_manager: PromptManager | None,
        prompt_id: str,
        model_gateway: Any | None = None,
    ) -> None:
        self.context_manager = context_manager
        self.context_policy = context_policy
        self.prompt_manager = prompt_manager
        self.prompt_id = prompt_id
        self.model_gateway = model_gateway
    @staticmethod
    def citation_catalog(citations: Iterable[CitationSchema]) -> str:
        """把结构化引用渲染为修复/重写 Prompt 可消费的 JSON 目录。"""
        return json.dumps(
            [
                {
                    "citation_id": item.citation_id,
                    "marker": f"[{item.citation_id}]",
                    "source_document_id": item.source_document_id or item.doc_id,
                    "source_chunk_id": item.chunk_id or item.child_chunk_id or item.parent_chunk_id,
                    "title": item.title,
                    "section": item.section,
                    "quote_text": prompt_visible_citation_text(item),
                }
                for item in citations
            ],
            ensure_ascii=False,
            indent=2,
        )

    @staticmethod
    def requested_target_section_chars(project_input: ProjectInputSchema) -> int | None:
        """Return only a caller-specified initial writing goal, never a token-derived value."""

        return project_requested_target_section_chars(project_input)

    def _render_initial_prompt(
        self,
        *,
        variables: Dict[str, Any],
        context_package: LLMContextPackageSchema,
        section_title: str,
    ) -> PromptRenderResultSchema:
        """Render the optional soft target before the prompt/context snapshot is locked."""

        if self.prompt_manager and self.prompt_manager.exists(self.prompt_id):
            template = self.prompt_manager.load_template(self.prompt_id)
            if "target_section_chars" in template.variables:
                raise TargetPromptContextIntegrityError(
                    "configured section Prompt requires the legacy hard target_section_chars "
                    "variable; use a versioned optional soft-target template"
                )
            return self.prompt_manager.render(self.prompt_id, variables, strict=True)

        target_guidance = variables["target_section_guidance"]
        return PromptRenderResultSchema(
            prompt_id="fallback_section_prompt",
            prompt_name="Section generation fallback",
            prompt_version="v3",
            rendered_text=(
                f"请只编写《{variables['document_title']}》中的“{section_title}”章节正文。\n\n"
                f"本次模型上下文：\n{context_package.rendered_context}\n\n"
                f"{target_guidance}只输出本章节正文。"
            ),
            variables=variables,
            metadata={"source": "code_fallback"},
        )

    @staticmethod
    def _has_concrete_resource_input(project_input: ProjectInputSchema) -> bool:
        """判断 ProjectInput 是否包含可被正文复述的具体组织或资源事实。"""
        return bool(
            project_input.hardware_resources
            or project_input.total_staff is not None
            or project_input.functional_department_count is not None
            or project_input.business_department_count is not None
            or project_input.department_groups
            or project_input.extra.get("resource_configuration")
            or project_input.extra.get("capacity_requirements")
        )

    @classmethod
    def section_generation_contract(
        cls,
        section_title: str,
        project_input: ProjectInputSchema,
    ) -> str:
        """生成当前章节的动态写作边界和事实约束。

        Contract 明确当前标题、兄弟章节、允许表达的一般建议以及不得确定承诺的高风险
        事实。存在具体资源输入时只能复述或基于其推导；不存在时必须写测算维度、建议或
        待确认项，不能自行补数量和型号。
        """
        required_sections = list(
            dict.fromkeys(
                project_input.generation_requirements.required_sections
                or project_input.output_schema.required_sections
            )
        )
        sibling_titles = [
            item for item in required_sections if str(item).strip() != str(section_title).strip()
        ]
        sibling_text = "、".join(sibling_titles) if sibling_titles else "无"
        has_resource_input = cls._has_concrete_resource_input(project_input)
        resource_instruction = (
            "ProjectInput已提供部分资源事实，只能复述或推导这些已提供事实；"
            if has_resource_input
            else "ProjectInput未提供确定资源数据，涉及数量、型号、预算、工期、人员和性能时，"
                 "只能写测算维度、建议或待确认项；"
        )
        return (
            f"当前章节标题为‘{section_title}’，只围绕该标题的自然语义展开。"
            f"文档中的其他章节为：{sibling_text}；不要完整展开这些兄弟章节的内容。"
            "一般性目标、原则和技术建议可以正常表达；不得把未获ProjectInput或证据支持的"
            "数量、采购、人力、预算、工期、性能或既定事实写成确定承诺。"
            f"{resource_instruction}输入不足时明确标注待补充或需项目方确认。"
        )

    @staticmethod
    def _section_scope_violations(
        content: str,
        section_title: str,
    ) -> List[Dict[str, Any]]:
        """保留的章节越界检查扩展点；当前版本不在 Prompt 服务内重复判定。"""
        return []

    @staticmethod
    def writer_evidence_semantics(
        evidence_bundle: EvidenceBundleSchema,
    ) -> Dict[str, Any]:
        """Mechanically project writing-relevant facts from canonical Evidence."""

        assessment = evidence_bundle.assessment
        if assessment.assessment_state != "completed":
            raise TargetPromptContextIntegrityError(
                "canonical Evidence assessment must be completed before Target Prompt assembly"
            )
        return {
            "assessment_schema_version": assessment.schema_version,
            "assessment_state": assessment.assessment_state,
            "evidence_status": assessment.status.value if assessment.status else None,
            "coverage_ratio": assessment.coverage_ratio,
            "required_topics": list(assessment.required_topics),
            "covered_topics": list(assessment.covered_topics),
            "missing_topics": list(assessment.missing_topics),
            "conflicts": list(assessment.conflicts),
            "supported_claim_types": [
                item.value for item in assessment.supported_claim_types
            ],
            "forbidden_claim_types": [
                item.value for item in assessment.forbidden_claim_types
            ],
            "authoritative_evidence_present": (
                assessment.authoritative_evidence_present
            ),
            "historical_or_reference_evidence_present": (
                assessment.historical_or_reference_evidence_present
            ),
            "required_qualifiers": list(assessment.required_qualifiers),
            "reason_codes": list(assessment.reason_codes),
        }

    def render_target_initial_section_prompt(
        self,
        shared_state: SharedStateSchema,
        project_input: ProjectInputSchema,
        *,
        section_id: str,
        section_title: str,
        section_order: int,
        evidence_bundle: EvidenceBundleSchema,
        structured_facts: List[StructuredFactSchema],
        previous_sections: List[SchemeSectionSchema],
    ) -> TargetInitialSectionPromptResult:
        """Build an unwired Target Prompt from one canonical Evidence authority.

        The method intentionally has no independent RAG/citation input. It derives
        both projections and the writing-relevant assessment facts from the same
        canonical bundle, then locks them in one managed context package.
        """

        task_id = shared_state.task_id
        run_id = shared_state.run_id
        if project_input.task_id != task_id:
            raise TargetPromptContextIntegrityError(
                "ProjectInput task_id does not match Target Prompt task identity"
            )
        if evidence_bundle.task_id is not None and evidence_bundle.task_id != task_id:
            raise TargetPromptContextIntegrityError(
                "canonical Evidence task_id does not match Target Prompt task identity"
            )
        if evidence_bundle.run_id is not None and evidence_bundle.run_id != run_id:
            raise TargetPromptContextIntegrityError(
                "canonical Evidence run_id does not match Target Prompt run identity"
            )
        for fact in structured_facts:
            if fact.task_id != task_id or fact.run_id != run_id:
                raise TargetPromptContextIntegrityError(
                    "structured writing fact identity does not match Target Prompt identity"
                )

        evidence_semantics = self.writer_evidence_semantics(evidence_bundle)
        evidence_hash = evidence_bundle_sha256(evidence_bundle)
        rag_context, _, projected_citations = project_evidence_bundle(evidence_bundle)
        target_chars = self.requested_target_section_chars(project_input)
        build_request = self.context_policy.build_request(
            task_id=task_id,
            run_id=run_id,
            section_id=section_id,
            section_title=section_title,
            section_order=section_order,
            project_input=project_input,
            section_contract=self.section_generation_contract(
                section_title, project_input
            ),
            target_section_chars=target_chars,
            rag_context=rag_context,
            citations=projected_citations,
            previous_sections=previous_sections,
            evidence_bundle=evidence_bundle,
        )
        target_items = list(build_request.items)
        target_items.append(
            ContextItemSchema(
                item_id="writer_evidence_semantics",
                source_type="evidence_semantics",
                title="规范证据写作语义",
                content=json.dumps(
                    evidence_semantics,
                    ensure_ascii=False,
                    sort_keys=True,
                    separators=(",", ":"),
                ),
                priority=104,
                required=True,
                truncate_allowed=False,
                citation_ids=[item.citation_id for item in projected_citations],
                metadata={
                    "mechanical_projection": True,
                    "evidence_bundle_sha256": evidence_hash,
                },
            )
        )
        if structured_facts:
            target_items.append(
                ContextItemSchema(
                    item_id="structured_writing_facts",
                    source_type="structured_fact",
                    title="结构化写作事实",
                    content=json.dumps(
                        [
                            fact.model_dump(mode="json", exclude_none=True)
                            for fact in structured_facts
                        ],
                        ensure_ascii=False,
                        sort_keys=True,
                        separators=(",", ":"),
                    ),
                    priority=99,
                    required=True,
                    truncate_allowed=False,
                    metadata={"fact_count": len(structured_facts)},
                )
            )
        build_request = build_request.model_copy(
            update={
                "items": target_items,
                "lineage": {
                    **dict(build_request.lineage or {}),
                    "evidence_bundle_schema_version": evidence_bundle.schema_version,
                    "evidence_bundle_sha256": evidence_hash,
                    "section_id": section_id,
                    "section_title": section_title,
                    "section_order": section_order,
                    "task_id": task_id,
                    "run_id": run_id,
                    "evidence_projection_source": "EvidenceBundleSchema",
                },
                "metadata": {
                    **dict(build_request.metadata or {}),
                    "target_prompt_prep": True,
                },
            }
        )

        capability_plan: Dict[str, Any] = {}
        resolve_capabilities = getattr(self.model_gateway, "routing_capabilities", None)
        if callable(resolve_capabilities):
            capability_plan = dict(
                resolve_capabilities(model_role=ModelRole.SECTION_GENERATION.value)
            )
            configured_limit = project_input.generation_requirements.extra.get(
                "max_input_context_tokens"
            )
            safe_context_window = int(capability_plan["safe_context_window"])
            max_input_tokens = (
                min(safe_context_window, int(configured_limit))
                if configured_limit is not None
                else safe_context_window
            )
            reserved_output_tokens = min(
                int(project_input.generation_requirements.max_tokens_per_section),
                int(capability_plan["safe_max_output_tokens"]),
            )
            build_request = build_request.model_copy(
                update={
                    "max_input_tokens": max_input_tokens,
                    "reserved_output_tokens": reserved_output_tokens,
                    "metadata": {
                        **dict(build_request.metadata or {}),
                        "model_capability_plan": capability_plan,
                    },
                }
            )

        try:
            context_package = self.context_manager.build(build_request)
        except ContextBudgetExceededError as exc:
            raise TargetPromptContextIntegrityError(
                "required Target Prompt context cannot fit the managed context budget"
            ) from exc
        semantic_item = next(
            (
                item
                for item in context_package.selected_items
                if item.item_id == "writer_evidence_semantics"
            ),
            None,
        )
        if (
            semantic_item is None
            or not semantic_item.required
            or semantic_item.truncate_allowed
        ):
            raise TargetPromptContextIntegrityError(
                "required canonical Evidence semantics were not preserved"
            )

        variables = {
            "document_title": project_input.output_schema.document_title,
            "section_title": section_title,
            "section_order": section_order,
            "target_section_guidance": (
                f"建议目标篇幅：约 {target_chars} 个章节正文字符（含 Markdown 与空白）。\n"
                if target_chars is not None else ""
            ),
            "llm_context_text": context_package.rendered_context,
        }
        if target_chars is not None:
            variables["target_section_chars"] = target_chars
        result = self._render_initial_prompt(
            variables=variables,
            context_package=context_package,
            section_title=section_title,
        )
        result.extra = {
            **dict(result.extra or {}),
            "llm_context_package": context_package.model_dump(mode="json"),
        }
        result.metadata = {
            **dict(result.metadata or {}),
            "context_package_id": context_package.package_id,
            "context_sha256": context_package.context_sha256,
            "context_policy_id": context_package.metadata.get("policy_id"),
            "context_used_chars": context_package.budget.used_context_chars,
            "context_estimated_tokens": context_package.budget.estimated_input_tokens,
            "context_warning_count": len(context_package.warnings),
            "target_prompt_prep": True,
            "evidence_bundle_sha256": evidence_hash,
        }
        structured_item = next(
            (
                item
                for item in context_package.selected_items
                if item.item_id == "structured_writing_facts"
            ),
            None,
        )
        technical_recovery_context = TargetTechnicalRecoveryContext(
            initial_prompt_text=result.rendered_text,
            prompt_id=result.prompt_id,
            prompt_version=result.prompt_version,
            context_package_json=context_package.model_dump_json(),
            context_package_id=context_package.package_id,
            context_sha256=context_package.context_sha256,
            writer_evidence_semantics_json=semantic_item.content,
            structured_writing_facts_json=(
                structured_item.content if structured_item is not None else None
            ),
            citation_ids=tuple(
                item.citation_id for item in projected_citations
            ),
            evidence_bundle_sha256=evidence_hash,
            task_id=task_id,
            run_id=run_id,
            section_id=section_id,
            section_title=section_title,
            section_order=section_order,
        )
        return TargetInitialSectionPromptResult(
            prompt_result=result,
            context_package=context_package,
            rag_context=rag_context,
            citations=tuple(projected_citations),
            evidence_bundle_sha256=evidence_hash,
            section_id=section_id,
            section_title=section_title,
            section_order=section_order,
            technical_recovery_context=technical_recovery_context,
        )

    def render_section_prompt(
        self,
        shared_state: SharedStateSchema,
        project_input: ProjectInputSchema,
        section_id: str,
        section_title: str,
        section_order: int,
        rag_context: RAGContextSchema,
        citations: List[CitationSchema],
        previous_sections: List[SchemeSectionSchema],
        evidence_bundle: EvidenceBundleSchema | None = None,
    ) -> PromptRenderResultSchema:
        """构造上下文包并渲染一次章节生成 Prompt。

        先由 Context Policy 产生候选块，再用 ModelGateway 的角色能力收紧输入窗口和输出
        预留量。模板存在时执行严格变量渲染；模板缺失时使用代码内最小 fallback。无论哪种
        路径，结果都携带完整上下文包和哈希，供 Model Trace 与 DataCapture 关联。
        """
        target_chars = self.requested_target_section_chars(project_input)
        build_request = self.context_policy.build_request(
            task_id=shared_state.task_id,
            run_id=shared_state.run_id,
            section_id=section_id,
            section_title=section_title,
            section_order=section_order,
            project_input=project_input,
            section_contract=self.section_generation_contract(section_title, project_input),
            target_section_chars=target_chars,
            rag_context=rag_context,
            citations=citations,
            previous_sections=previous_sections,
            evidence_bundle=evidence_bundle,
        )
        capability_plan: dict[str, Any] = {}
        resolve_capabilities = getattr(self.model_gateway, "routing_capabilities", None)
        if callable(resolve_capabilities):
            capability_plan = dict(
                resolve_capabilities(model_role=ModelRole.SECTION_GENERATION.value)
            )
            configured_limit = project_input.generation_requirements.extra.get(
                "max_input_context_tokens"
            )
            safe_context_window = int(capability_plan["safe_context_window"])
            max_input_tokens = (
                min(safe_context_window, int(configured_limit))
                if configured_limit is not None
                else safe_context_window
            )
            reserved_output_tokens = min(
                int(project_input.generation_requirements.max_tokens_per_section),
                int(capability_plan["safe_max_output_tokens"]),
            )
            build_request = build_request.model_copy(
                update={
                    "max_input_tokens": max_input_tokens,
                    "reserved_output_tokens": reserved_output_tokens,
                    "metadata": {
                        **dict(build_request.metadata or {}),
                        "model_capability_plan": capability_plan,
                    },
                }
            )
        context_package = self.context_manager.build(build_request)
        variables = {
            "document_title": project_input.output_schema.document_title,
            "section_title": section_title,
            "section_order": section_order,
            "target_section_guidance": (
                f"建议目标篇幅：约 {target_chars} 个章节正文字符（含 Markdown 与空白）。\n"
                if target_chars is not None else ""
            ),
            "llm_context_text": context_package.rendered_context,
        }
        if target_chars is not None:
            variables["target_section_chars"] = target_chars
        result = self._render_initial_prompt(
            variables=variables,
            context_package=context_package,
            section_title=section_title,
        )

        result.extra = {
            **dict(result.extra or {}),
            "llm_context_package": context_package.model_dump(),
        }
        result.metadata = {
            **dict(result.metadata or {}),
            "context_package_id": context_package.package_id,
            "context_sha256": context_package.context_sha256,
            "context_policy_id": context_package.metadata.get("policy_id"),
            "context_used_chars": context_package.budget.used_context_chars,
            "context_estimated_tokens": context_package.budget.estimated_input_tokens,
            "context_warning_count": len(context_package.warnings),
        }
        return result
