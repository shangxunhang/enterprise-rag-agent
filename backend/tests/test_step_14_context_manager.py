# =============================================================================
# 中文阅读说明：自动化测试模块，用于验证主链、边界条件和回归行为。
# 主要定义：test_context_manager_is_deterministic_and_bounded、test_context_manager_fails_when_required_untruncatable_item_cannot_fit、test_section_policy_preserves_evidence_citations_history_and_lineage、test_passthrough_package_preserves_auxiliary_prompt_exactly、_large_citations、_context_budget_project_input、test_section_policy_only_requires_catalog_for_citation_required_section、test_large_realistic_citation_catalog_is_bounded_without_killing_section、test_required_line_block_catalog_compacts_by_complete_entries。建议先从公开入口函数开始，再沿调用关系向下阅读。
# =============================================================================
from __future__ import annotations

import hashlib

import pytest

from apps.enterprise_document.schemas.project_input_schema import ProjectInputSchema
from apps.enterprise_document.schemas.scheme_writer_schema import SchemeSectionSchema
from context_manager.manager import ContextBudgetExceededError, LLMContextManager
from context_manager.policies import SectionGenerationContextPolicy
from schemas.citation import CitationSchema
from schemas.context import ContextBuildRequestSchema, ContextItemSchema
from schemas.rag import (
    EvidenceDisposition,
    RAGContextSchema,
    RAGEvidenceAssessmentSchema,
    RAGEvidenceContractSchema,
    RAGEvidenceItemSchema,
    RAGEvidenceLineageSchema,
)
from schemas.status import ExecutionStatus


# 阅读注释（函数）：处理 测试 上下文 管理器 is deterministic and bounded 相关逻辑。
def test_context_manager_is_deterministic_and_bounded() -> None:
    """处理 测试 上下文 管理器 is deterministic and bounded 相关逻辑。

    返回:
        None

    阅读提示:
        主要直接调用：ContextBuildRequestSchema, ContextItemSchema, LLMContextManager, manager.build, first.model_dump, second.model_dump, hexdigest, hashlib.sha256。
    """
    request = ContextBuildRequestSchema(
        task_id="task_1",
        run_id="run_1",
        call_purpose="test",
        max_context_chars=420,
        max_input_tokens=4000,
        reserved_output_tokens=128,
        safety_margin_tokens=64,
        items=[
            ContextItemSchema(
                item_id="system",
                source_type="system",
                title="系统约束",
                content="禁止编造。必须引用真实证据。",
                priority=110,
                required=True,
                truncate_allowed=False,
            ),
            ContextItemSchema(
                item_id="task",
                source_type="task",
                title="当前任务",
                content="生成政务云建设方案中的安全设计章节。",
                priority=100,
                required=True,
                truncate_allowed=False,
            ),
            ContextItemSchema(
                item_id="history",
                source_type="history",
                title="历史摘要",
                content="总体架构采用私有化部署。" * 80,
                priority=50,
            ),
        ],
    )
    manager = LLMContextManager()
    first = manager.build(request)
    second = manager.build(request)

    assert first.model_dump() == second.model_dump()
    assert first.context_sha256 == hashlib.sha256(
        first.rendered_context.encode("utf-8")
    ).hexdigest()
    assert first.budget.used_context_chars <= first.budget.max_context_chars
    assert first.budget.estimated_input_tokens <= (
        first.budget.max_input_tokens
        - first.budget.reserved_output_tokens
        - first.budget.safety_margin_tokens
    )
    selected_ids = {item.item_id for item in first.selected_items}
    assert {"system", "task"}.issubset(selected_ids)
    decision = next(item for item in first.decisions if item.item_id == "history")
    assert decision.action in {"truncated", "dropped"}


# 阅读注释（函数）：处理 测试 上下文 管理器 fails when required untruncatable 数据项 cannot fit 相关逻辑。
def test_context_manager_fails_when_required_untruncatable_item_cannot_fit() -> None:
    """处理 测试 上下文 管理器 fails when required untruncatable 数据项 cannot fit 相关逻辑。

    返回:
        None

    阅读提示:
        主要直接调用：ContextBuildRequestSchema, ContextItemSchema, pytest.raises, build, LLMContextManager。
    """
    request = ContextBuildRequestSchema(
        task_id="task_1",
        run_id="run_1",
        call_purpose="test",
        max_context_chars=256,
        max_input_tokens=512,
        reserved_output_tokens=1,
        safety_margin_tokens=0,
        items=[
            ContextItemSchema(
                item_id="required",
                source_type="system",
                title="系统约束",
                content="必须完整保留。" * 200,
                priority=100,
                required=True,
                truncate_allowed=False,
            )
        ],
    )
    with pytest.raises(ContextBudgetExceededError):
        LLMContextManager().build(request)


# 阅读注释（函数）：处理 测试 章节 策略 preserves 证据 citations history and lineage 相关逻辑。
def test_section_policy_preserves_evidence_citations_history_and_lineage() -> None:
    """处理 测试 章节 策略 preserves 证据 citations history and lineage 相关逻辑。

    返回:
        None

    阅读提示:
        主要直接调用：CitationSchema, RAGEvidenceItemSchema, RAGContextSchema, len, RAGEvidenceContractSchema, RAGEvidenceLineageSchema, RAGEvidenceAssessmentSchema, contract.model_dump。
    """
    citation = CitationSchema(
        citation_id="C1",
        source_type="document",
        doc_id="doc_security",
        chunk_id="child_1",
        title="政务云安全规范",
        quote_text="政务云应采用统一身份认证和审计。",
    )
    evidence = RAGEvidenceItemSchema(
        evidence_id="E1",
        disposition=EvidenceDisposition.SELECTED,
        rank=1,
        matched_chunk_id="child_1",
        context_chunk_id="parent_1",
        child_chunk_id="child_1",
        parent_chunk_id="parent_1",
        doc_id="doc_security",
        match_text="统一身份认证",
        context_text="政务云应采用统一身份认证、最小权限控制和审计机制。[C1]",
        citation_ids=["C1"],
    )
    rag_context = RAGContextSchema(
        context_text=evidence.context_text,
        used_context_chunk_ids=["parent_1"],
        matched_chunk_ids=["child_1"],
        used_doc_ids=["doc_security"],
        used_context_chars=len(evidence.context_text),
        context_item_count=1,
    )
    contract = RAGEvidenceContractSchema(
        query="政务云安全设计",
        items=[evidence],
        selected_evidence_ids=["E1"],
        citations=[citation],
        context=rag_context,
        lineage=RAGEvidenceLineageSchema(
            index_version="idx_v1",
            embedding_model="m3e-base",
        ),
        assessment=RAGEvidenceAssessmentSchema(),
    )
    rag_context.extra = {
        "evidence_contract": contract.model_dump(),
        "evidence_contract_sha256": "a" * 64,
    }
    project_input = ProjectInputSchema.model_validate(
        {
            "task_id": "task_1",
            "project_name": "某政务云",
            "project_type": "政务云",
            "task_type": "scheme_generation",
            "user_query": "生成政务云建设方案",
            "generation_requirements": {
                "required_sections": ["总体架构", "安全设计"],
                "citation_required_sections": ["安全设计"],
                "max_context_chars": 6000,
            },
            "output_schema": {
                "document_title": "某政务云建设方案",
                "required_sections": ["总体架构", "安全设计"],
            },
        }
    )
    previous = SchemeSectionSchema(
        section_id="section_1",
        section_title="总体架构",
        section_order=1,
        content="项目采用私有化部署和分层架构。",
        status=ExecutionStatus.SUCCESS,
    )
    policy = SectionGenerationContextPolicy()
    request = policy.build_request(
        task_id="task_1",
        run_id="run_1",
        section_id="section_2",
        section_title="安全设计",
        section_order=2,
        project_input=project_input,
        section_contract="只编写安全设计。",
        target_section_chars=800,
        rag_context=rag_context,
        citations=[citation],
        previous_sections=[previous],
    )
    package = LLMContextManager().build(request)

    item_by_id = {item.item_id: item for item in package.selected_items}
    assert item_by_id["E1"].citation_ids == ["C1"]
    assert "previous_sections" in item_by_id
    assert package.lineage["index_version"] == "idx_v1"
    assert package.lineage["embedding_model"] == "m3e-base"
    assert "[C1]" in package.rendered_context


# 阅读注释（函数）：处理 测试 passthrough package preserves auxiliary 提示词 exactly 相关逻辑。
def test_passthrough_package_preserves_auxiliary_prompt_exactly() -> None:
    """处理 测试 passthrough package preserves auxiliary 提示词 exactly 相关逻辑。

    返回:
        None

    阅读提示:
        主要直接调用：build_passthrough, LLMContextManager。
    """
    prompt = "只修复引用，不改变原文事实。"
    package = LLMContextManager().build_passthrough(
        task_id="task_1",
        run_id="run_1",
        call_purpose="scheme_citation_repair",
        content=prompt,
    )
    assert package.rendered_context == prompt
    assert package.selected_items[0].content == prompt
    assert package.metadata["policy_id"] == "compatibility_passthrough_context_policy_v1"


# 阅读注释（函数）：处理 large citations 相关逻辑。
def _large_citations(count: int = 8) -> list[CitationSchema]:
    """处理 large citations 相关逻辑。

    参数:
        count: count，具体约束请结合类型标注和调用方确认。

    返回:
        list[CitationSchema]

    阅读提示:
        主要直接调用：CitationSchema, range。
    """
    return [
        CitationSchema(
            citation_id=f"C{index}",
            source_type="document",
            doc_id=f"doc_{index}",
            chunk_id=f"chunk_{index}",
            title="政务云建设规范与安全技术要求" * 3,
            section=f"第{index}章 安全与架构",
            quote_text=(
                "政务云应覆盖身份认证、访问控制、日志审计、数据加密、"
                "平台架构和资源规划等建设要求。"
            )
            * 12,
        )
        for index in range(1, count + 1)
    ]


# 阅读注释（函数）：处理 上下文 预算 项目 输入 相关逻辑。
def _context_budget_project_input() -> ProjectInputSchema:
    """处理 上下文 预算 项目 输入 相关逻辑。

    返回:
        ProjectInputSchema

    阅读提示:
        主要直接调用：ProjectInputSchema.model_validate。
    """
    return ProjectInputSchema.model_validate(
        {
            "task_id": "task_context_budget",
            "project_name": "某政务云",
            "project_type": "政务云",
            "task_type": "scheme_generation",
            "user_query": "生成一个政务云方案",
            "generation_requirements": {
                "required_sections": ["项目概述", "建设内容", "技术方案", "安全设计"],
                "citation_required_sections": ["建设内容", "技术方案", "安全设计"],
                "max_context_chars": 6000,
                "max_tokens_per_section": 1024,
            },
            "output_schema": {
                "document_title": "某政务云建设方案",
                "required_sections": ["项目概述", "建设内容", "技术方案", "安全设计"],
            },
        }
    )


# 阅读注释（函数）：处理 测试 章节 策略 only requires catalog for 引用 required 章节 相关逻辑。
def test_section_policy_only_requires_catalog_for_citation_required_section() -> None:
    """处理 测试 章节 策略 only requires catalog for 引用 required 章节 相关逻辑。

    返回:
        None

    阅读提示:
        主要直接调用：_large_citations, RAGContextSchema, len, SectionGenerationContextPolicy, _context_budget_project_input, policy.build_request, next, overview_catalog.content.count。
    """
    citations = _large_citations()
    evidence_text = "政务云建设通用证据。" * 500
    rag_context = RAGContextSchema(
        context_text=evidence_text,
        context_item_count=1,
        used_context_chars=len(evidence_text),
    )
    policy = SectionGenerationContextPolicy()
    project_input = _context_budget_project_input()

    overview = policy.build_request(
        task_id=project_input.task_id,
        run_id="run_context_budget",
        section_id="section_001",
        section_title="项目概述",
        section_order=1,
        project_input=project_input,
        section_contract="只编写项目概述。",
        target_section_chars=1000,
        rag_context=rag_context,
        citations=citations,
        previous_sections=[],
    )
    safety = policy.build_request(
        task_id=project_input.task_id,
        run_id="run_context_budget",
        section_id="section_004",
        section_title="安全设计",
        section_order=4,
        project_input=project_input,
        section_contract="只编写安全设计。",
        target_section_chars=1000,
        rag_context=rag_context,
        citations=citations,
        previous_sections=[],
    )

    overview_catalog = next(item for item in overview.items if item.item_id == "citation_catalog")
    safety_catalog = next(item for item in safety.items if item.item_id == "citation_catalog")
    assert overview_catalog.required is False
    assert safety_catalog.required is True
    assert overview_catalog.truncate_allowed is True
    assert safety_catalog.truncate_allowed is True
    assert overview_catalog.metadata["catalog_citation_count"] == 3
    assert safety_catalog.metadata["catalog_citation_count"] == 6
    assert overview_catalog.content.count("[C") == 3
    assert safety_catalog.content.count("[C") == 6


# 阅读注释（函数）：处理 测试 large realistic 引用 catalog is bounded without killing 章节 相关逻辑。
def test_large_realistic_citation_catalog_is_bounded_without_killing_section() -> None:
    """处理 测试 large realistic 引用 catalog is bounded without killing 章节 相关逻辑。

    返回:
        None

    阅读提示:
        主要直接调用：_large_citations, RAGContextSchema, len, _context_budget_project_input, build_request, SectionGenerationContextPolicy, build, LLMContextManager。
    """
    citations = _large_citations()
    evidence_text = "政务云安全建设证据正文。" * 800
    rag_context = RAGContextSchema(
        context_text=evidence_text,
        context_item_count=1,
        used_context_chars=len(evidence_text),
    )
    project_input = _context_budget_project_input()
    request = SectionGenerationContextPolicy().build_request(
        task_id=project_input.task_id,
        run_id="run_context_budget",
        section_id="section_004",
        section_title="安全设计",
        section_order=4,
        project_input=project_input,
        section_contract="只编写安全设计。",
        target_section_chars=1000,
        rag_context=rag_context,
        citations=citations,
        previous_sections=[],
    )

    package = LLMContextManager().build(request)
    catalog = next(item for item in package.selected_items if item.item_id == "citation_catalog")
    assert catalog.required is True
    assert "[C1]" in catalog.content
    assert catalog.content.count("[C") >= 1
    assert package.budget.used_context_chars <= package.budget.max_context_chars
    assert package.budget.estimated_input_tokens <= (
        package.budget.max_input_tokens
        - package.budget.reserved_output_tokens
        - package.budget.safety_margin_tokens
    )


# 阅读注释（函数）：处理 测试 required line block catalog compacts by complete entries 相关逻辑。
def test_required_line_block_catalog_compacts_by_complete_entries() -> None:
    """处理 测试 required line block catalog compacts by complete entries 相关逻辑。

    返回:
        None

    阅读提示:
        主要直接调用：range, ContextBuildRequestSchema, ContextItemSchema, join, build, LLMContextManager, next, catalog.content.splitlines。
    """
    catalog_rows = [
        f"[C{index}] | 来源：规范{index} | 摘要：" + ("安全建设要求。" * 30)
        for index in range(1, 7)
    ]
    request = ContextBuildRequestSchema(
        task_id="task_catalog",
        run_id="run_catalog",
        call_purpose="test_catalog_compaction",
        max_context_chars=900,
        max_input_tokens=4000,
        reserved_output_tokens=128,
        safety_margin_tokens=64,
        items=[
            ContextItemSchema(
                item_id="system",
                source_type="system",
                title="系统约束",
                content="禁止编造。" * 20,
                priority=110,
                required=True,
                truncate_allowed=False,
            ),
            ContextItemSchema(
                item_id="citation_catalog",
                source_type="citation",
                title="可用引用目录",
                content="\n".join(catalog_rows),
                priority=100,
                required=True,
                truncate_allowed=True,
                metadata={
                    "compaction_strategy": "line_blocks",
                    "min_blocks": 1,
                },
            ),
        ],
    )

    package = LLMContextManager().build(request)
    catalog = next(item for item in package.selected_items if item.item_id == "citation_catalog")
    decision = next(item for item in package.decisions if item.item_id == "citation_catalog")
    retained_lines = [line for line in catalog.content.splitlines() if line.strip()]
    assert retained_lines
    assert all(line.startswith("[C") for line in retained_lines)
    assert all(" | 来源：" in line for line in retained_lines)
    assert decision.action == "truncated"
    assert "structured_line_compaction" in decision.reason
    assert catalog.metadata["context_retained_blocks"] == len(retained_lines)
