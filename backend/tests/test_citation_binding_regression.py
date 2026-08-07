# =============================================================================
# 中文阅读说明：自动化测试模块，用于验证主链、边界条件和回归行为。
# 主要定义：citation、project_input、_RepairStubAgent、CitationBindingRegressionTest。建议先从公开入口函数开始，再沿调用关系向下阅读。
# =============================================================================
"""Regression tests for citation repair, grounding fallback and hard gate."""

from __future__ import annotations

import unittest
from types import SimpleNamespace

from agent.runtime.shared_state_schema import SharedStateSchema
from apps.enterprise_document.agents.scheme_writer_agent import SchemeWriterAgent
from apps.enterprise_document.schemas.gate_decision import (
    ClaimRiskLevel,
    DeliveryMode,
    GateDecisionSchema,
)
from apps.enterprise_document.schemas.project_input_schema import ProjectInputSchema
from apps.enterprise_document.schemas.scheme_writer_schema import (
    SchemeDraftSchema,
    SchemeSectionSchema,
    SectionEvalSchema,
    TruncationCheckSchema,
)
from apps.enterprise_document.schemas.writer_governance import WriterGateBindingSchema
from apps.enterprise_document.services.scheme_writer.citation_integrity_service import (
    CitationOccurrenceIntegrityGate,
)
from apps.enterprise_document.services.scheme_writer.citation_service import CitationService
from eval.agent.hard_gate import evaluate_scheme_draft
from rag.schema.evidence_assessment_v2 import EvidenceStatus
from schemas.citation import CitationBindingSchema, CitationSchema
from schemas.context import ContextBundleSchema, TaskContextSchema, UserContextSchema
from schemas.model import ModelResponseSchema
from schemas.rag import RAGContextSchema
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


def _final_binding_shared_state() -> SharedStateSchema:
    return SharedStateSchema(
        task_id="task_citation_final",
        run_id="run_citation_final",
        task_type="scheme_generation",
        user_input="生成实施方案",
        context_bundle=ContextBundleSchema(
            user=UserContextSchema(user_query="生成实施方案"),
            task=TaskContextSchema(
                task_id="task_citation_final",
                run_id="run_citation_final",
                task_type="scheme_generation",
            ),
        ),
        created_at="2026-08-05T05:00:00+00:00",
    )


def _final_binding_project_input() -> ProjectInputSchema:
    return ProjectInputSchema.model_validate(
        {
            "task_id": "task_citation_final",
            "task_type": "scheme_generation",
            "user_query": "生成实施方案",
            "generation_requirements": {
                "required_sections": ["实施方案"],
                "need_citation": True,
                "citation_required_sections": ["实施方案"],
                "min_section_chars": 1,
                "max_section_retries": 0,
            },
            "output_schema": {"required_sections": ["实施方案"]},
        }
    )


def _final_writer_binding() -> WriterGateBindingSchema:
    return WriterGateBindingSchema(
        section_id="section_run_citation_final_001",
        section_title="实施方案",
        section_order=1,
        document_id="doc_citation_final",
        run_id="run_citation_final",
        gate_decision=GateDecisionSchema(
            policy_id="citation_final_binding_test_v1",
            evidence_status=EvidenceStatus.SUFFICIENT,
            delivery_mode=DeliveryMode.QUALIFIED,
            claim_risk_level=ClaimRiskLevel.LOW,
            allowed_claim_types=["general_method"],
            forbidden_claim_types=[],
            required_qualifiers=["基于现有参考资料"],
            covered_topics=["实施方案"],
            missing_topics=[],
            human_review_required=False,
            reason_codes=["reference_evidence_requires_qualification"],
        ),
        generation_allowed=True,
        evidence_contract_sha256="a" * 64,
        prompt_evidence_source="context_pack_selected_results",
        prompt_evidence_contract_sha256="b" * 64,
    )


def _final_binding_citations() -> list[CitationSchema]:
    return [
        CitationSchema(
            citation_id="cite-method",
            source_type="document_chunk",
            doc_id="source-method",
            chunk_id="chunk-method",
            title="实施方法",
            quote_text="实施方法采用分阶段推进和闭环治理原则。",
            confidence=0.95,
        ),
        CitationSchema(
            citation_id="cite-acceptance",
            source_type="document_chunk",
            doc_id="source-acceptance",
            chunk_id="chunk-acceptance",
            title="验收方法",
            quote_text="验收方法按照功能验证和证据留痕流程执行。",
            confidence=0.95,
        ),
    ]


def test_qualifier_injection_rebuilds_final_citation_binding_identity() -> None:
    class _TwoCitationAgent(SchemeWriterAgent):
        def call_model(self, *args, **kwargs):  # type: ignore[override]
            del args, kwargs
            return ModelResponseSchema(
                model_call_id="citation-final-model",
                task_id="task_citation_final",
                run_id="run_citation_final",
                model_name="citation-final-stub",
                success=True,
                content=(
                    "实施方法采用分阶段推进和闭环治理原则。\n\n"
                    "验收方法按照功能验证和证据留痕流程执行。"
                ),
                finish_reason="stop",
                created_at="2026-08-05T05:00:00+00:00",
            )

    citations = _final_binding_citations()
    section = _TwoCitationAgent(
        enable_semantic_gate=False
    ).section_generation_service.generate_section(
        _final_binding_shared_state(),
        document_id="doc_citation_final",
        project_input=_final_binding_project_input(),
        section_title="实施方案",
        section_order=1,
        rag_context=RAGContextSchema(
            context_text="\n".join(item.quote_text or "" for item in citations)
        ),
        citations=citations,
        structured_facts=[],
        previous_sections=[],
        writer_gate_binding=_final_writer_binding(),
    )

    assert section.content.startswith("证据限定：基于现有参考资料。")
    occurrences = CitationOccurrenceIntegrityGate.extract_marker_occurrences(
        section_id=section.section_id,
        content=section.content,
    )
    integrity = CitationOccurrenceIntegrityGate().evaluate(
        sections=[section],
        bindings=section.citation_bindings,
        full_text=section.content,
    )
    expected_bindings = CitationService.supported_bindings(
        CitationService.bind_citations(
            document_id="doc_citation_final",
            section_id=section.section_id,
            content=section.content,
            citations=citations,
        ),
        citations,
    )

    assert len(occurrences) == 2
    assert len(section.citation_bindings) == 2
    assert integrity.metadata["verified_binding_occurrence_count"] == 2
    assert integrity.metadata["orphan_marker_occurrence_ids"] == []
    assert integrity.metadata["orphan_binding_ids"] == []
    assert integrity.checks["citation_marker_binding_bijection"] is True
    assert integrity.passed is True

    fields = (
        "target_paragraph_id",
        "target_claim_id",
        "target_marker_index",
        "marker_ordinal",
        "target_marker_start",
        "target_marker_end",
        "claim_text",
        "citation_id",
    )
    actual_projection = [tuple(getattr(item, field) for field in fields) for item in section.citation_bindings]
    expected_projection = [tuple(getattr(item, field) for field in fields) for item in expected_bindings]
    assert actual_projection == expected_projection


if __name__ == "__main__":
    unittest.main()
