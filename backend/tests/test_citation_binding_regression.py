# =============================================================================
# 中文阅读说明：自动化测试模块，用于验证主链、边界条件和回归行为。
# 主要定义：citation、project_input、_RepairStubAgent、CitationBindingRegressionTest。建议先从公开入口函数开始，再沿调用关系向下阅读。
# =============================================================================
"""Regression tests for citation repair, grounding fallback and hard gate."""

from __future__ import annotations

import unittest
from types import SimpleNamespace

from apps.enterprise_document.agents.scheme_writer_agent import SchemeWriterAgent
from apps.enterprise_document.schemas.project_input_schema import ProjectInputSchema
from apps.enterprise_document.schemas.scheme_writer_schema import (
    SchemeDraftSchema,
    SchemeSectionSchema,
    SectionEvalSchema,
    TruncationCheckSchema,
)
from eval.agent.hard_gate import evaluate_scheme_draft
from schemas.citation import CitationBindingSchema, CitationSchema
from schemas.model import ModelResponseSchema
from schemas.status import ExecutionStatus


NOW = "2026-07-13T00:00:00+00:00"


# 阅读注释（函数）：处理 引用 相关逻辑。
def citation(
    citation_id: str,
    quote_text: str,
    *,
    chunk_id: str,
    title: str = "测试资料",
) -> CitationSchema:
    """处理 引用 相关逻辑。

    参数:
        citation_id: 引用 标识，具体约束请结合类型标注和调用方确认。
        quote_text: quote 文本，具体约束请结合类型标注和调用方确认。
        chunk_id: 文本块 标识，具体约束请结合类型标注和调用方确认。
        title: title，具体约束请结合类型标注和调用方确认。

    返回:
        CitationSchema

    阅读提示:
        主要直接调用：CitationSchema。
    """
    return CitationSchema(
        citation_id=citation_id,
        source_type="document",
        doc_id="doc_test",
        source_document_id="doc_test",
        chunk_id=chunk_id,
        title=title,
        quote_text=quote_text,
    )


# 阅读注释（函数）：处理 项目 输入 相关逻辑。
def project_input() -> ProjectInputSchema:
    """处理 项目 输入 相关逻辑。

    返回:
        ProjectInputSchema

    阅读提示:
        主要直接调用：ProjectInputSchema.model_validate。
    """
    return ProjectInputSchema.model_validate(
        {
            "task_id": "task_test",
            "task_type": "scheme_generation",
            "user_query": "生成系统建设方案",
            "generation_requirements": {
                "required_sections": ["安全设计"],
                "citation_required_sections": ["安全设计"],
            },
            "output_schema": {
                "document_title": "测试建设方案",
                "required_sections": ["安全设计"],
            },
        }
    )


# 阅读注释（类）：封装 修复 stub Agent，负责接收状态、调用工具或服务并返回统一 Agent 结果。
class _RepairStubAgent(SchemeWriterAgent):
    """封装 修复 stub Agent，负责接收状态、调用工具或服务并返回统一 Agent 结果。"""
    # 阅读注释（函数）：初始化 _RepairStubAgent，保存运行所需的依赖、配置或状态。
    def __init__(self, response_content: str) -> None:
        """初始化 _RepairStubAgent，保存运行所需的依赖、配置或状态。

        参数:
            response_content: 响应 content，具体约束请结合类型标注和调用方确认。

        返回:
            None

        阅读提示:
            主要直接调用：__init__, super。
        """
        super().__init__()
        self.response_content = response_content

    # 阅读注释（函数）：处理 call 模型 相关逻辑。
    def call_model(self, *args, **kwargs) -> ModelResponseSchema:  # type: ignore[override]
        """处理 call 模型 相关逻辑。

        参数:
            *args: 额外位置参数。
            **kwargs: 额外关键字参数。

        返回:
            ModelResponseSchema

        阅读提示:
            主要直接调用：ModelResponseSchema。
        """
        return ModelResponseSchema(
            model_call_id="repair_stub",
            task_id="task_test",
            run_id="run_test",
            model_name="stub",
            success=True,
            content=self.response_content,
            created_at=NOW,
            finish_reason="stop",
        )


# 阅读注释（类）：封装 引用 绑定关系 regression 测试，集中封装相关状态、依赖和行为。
class CitationBindingRegressionTest(unittest.TestCase):
    """封装 引用 绑定关系 regression 测试，集中封装相关状态、依赖和行为。"""
    # 阅读注释（函数）：处理 测试 修复 rejects syntactic but unsupported marker 相关逻辑。
    def test_repair_rejects_syntactic_but_unsupported_marker(self) -> None:
        """处理 测试 修复 rejects syntactic but unsupported marker 相关逻辑。

        返回:
            None

        阅读提示:
            主要直接调用：citation, _RepairStubAgent, agent._repair_section_citations, SimpleNamespace, project_input, self.assertEqual。
        """
        original = "系统拟采用 TLS 与多因素认证增强访问安全。"
        unrelated = citation(
            "C1",
            "软件费用估算需要进行三级和四级功能点拆分。",
            chunk_id="chunk_finance",
        )
        agent = _RepairStubAgent(original + "[C1]")

        repaired, _ = agent.grounding_repair_service.repair_section_citations(
            SimpleNamespace(run_id="run_test"),
            content=original,
            section_id="section_security",
            section_title="安全设计",
            project_input=project_input(),
            citations=[unrelated],
        )

        self.assertEqual(original, repaired)

    # 阅读注释（函数）：处理 测试 hard gate checks each required 引用 章节 相关逻辑。
    def test_hard_gate_checks_each_required_citation_section(self) -> None:
        """处理 测试 hard gate checks each required 引用 章节 相关逻辑。

        返回:
            None

        阅读提示:
            主要直接调用：CitationBindingSchema, SchemeSectionSchema, TruncationCheckSchema, SectionEvalSchema, SchemeDraftSchema, evaluate_scheme_draft, self.assertFalse, self.assertIn。
        """
        binding = CitationBindingSchema(
            binding_id="binding_1",
            citation_id="C1",
            target_document_id="document_test",
            target_section_id="section_build",
            target_paragraph_id="paragraph_1",
            target_claim_id="claim_1",
            source_document_id="doc_test",
            source_chunk_id="chunk_build",
            claim_text="建设内容包括知识库构建。",
            quote_text="建设内容包括知识库构建。",
        )
        build_section = SchemeSectionSchema(
            section_id="section_build",
            section_title="建设内容",
            section_order=1,
            content="建设内容包括知识库构建。[C1]",
            status=ExecutionStatus.SUCCESS,
            citation_ids=["C1"],
            citation_bindings=[binding],
            truncation=TruncationCheckSchema(truncated=False),
            eval_result=SectionEvalSchema(passed=True),
        )
        security_section = SchemeSectionSchema(
            section_id="section_security",
            section_title="安全设计",
            section_order=2,
            content="安全设计待完善。",
            status=ExecutionStatus.SUCCESS,
            truncation=TruncationCheckSchema(truncated=False),
            eval_result=SectionEvalSchema(passed=True),
        )
        draft = SchemeDraftSchema(
            draft_id="draft_test",
            document_id="document_test",
            task_id="task_test",
            run_id="run_test",
            title="测试方案",
            full_text="建设内容包括知识库构建。[C1]\n\n安全设计待完善。",
            sections=[build_section, security_section],
            required_sections=["建设内容", "安全设计"],
            citation_bindings=[binding],
            created_at=NOW,
        )

        result = evaluate_scheme_draft(
            draft,
            citation_required=True,
            citation_required_sections=["建设内容", "安全设计"],
            retrieved_chunk_ids=["chunk_build"],
            evidence_sufficient=True,
        )

        self.assertFalse(result.passed)
        self.assertIn("安全设计", "；".join(result.failures))
        self.assertEqual(
            ["安全设计"],
            result.metadata["missing_citation_sections"],
        )


if __name__ == "__main__":
    unittest.main()
