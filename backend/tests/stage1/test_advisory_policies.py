"""Stage-1 regression tests split by responsibility."""

from __future__ import annotations

from agent.agent_registry import AgentRegistry
from agent.base_agent import BaseAgent
from agent.runtime.shared_state_schema import SharedStateSchema
from agent.runtime.workflow_executor import WorkflowExecutor
from agent.runtime.workflow_schema import WorkflowDefinitionSchema, WorkflowStepSchema
from apps.enterprise_document.schemas.project_input_schema import ProjectInputSchema
from apps.enterprise_document.schemas.scheme_writer_schema import (
    SchemeDraftSchema,
    SchemeSectionSchema,
    SectionEvalSchema,
    TruncationCheckSchema,
)
from apps.enterprise_document.services.output_validation import detect_truncation
from eval.agent.hard_gate import evaluate_scheme_draft
from schemas.agent import AgentResultSchema
from schemas.citation import CitationBindingSchema
from schemas.common import ErrorSchema
from schemas.context import ContextBundleSchema, TaskContextSchema, UserContextSchema
from schemas.status import ExecutionStatus

NOW = "2026-07-14T00:00:00+00:00"

def test_project_fact_boundary_rejects_invented_resources() -> None:
    from apps.enterprise_document.agents.scheme_writer_agent import SchemeWriterAgent

    item = ProjectInputSchema.model_validate(
        {
            "task_id": "task_no_resources",
            "task_type": "scheme_generation",
            "user_query": "生成项目方案",
            "generation_requirements": {"required_sections": ["资源"]},
            "output_schema": {"required_sections": ["资源"]},
        }
    )
    violations = SchemeWriterAgent._project_fact_violations(
        "项目将采购两台GPU服务器，并组建5人技术团队。",
        item,
        [],
    )

    assert len(violations) >= 1
    assert all(
        entry["reason"] == "project_specific_fact_not_supported"
        for entry in violations
    )


def test_project_fact_boundary_accepts_qualified_or_supported_facts() -> None:
    from apps.enterprise_document.agents.scheme_writer_agent import SchemeWriterAgent
    from schemas.citation import CitationSchema

    item = ProjectInputSchema.model_validate(
        {
            "task_id": "task_supported",
            "task_type": "scheme_generation",
            "user_query": "建设RAG-Agent系统",
            "total_staff": 500,
            "generation_requirements": {"required_sections": ["说明"]},
            "output_schema": {"required_sections": ["说明"]},
        }
    )
    evidence = CitationSchema(
        citation_id="C1",
        source_type="document",
        doc_id="doc_security",
        chunk_id="child_security",
        quote_text="安全设计采用JWT认证，输入参数执行Schema验证，输出敏感字段脱敏。",
    )
    content = (
        "单位现有人员规模为500人。"
        "安全设计采用JWT认证并对输入参数执行Schema验证。"
        "GPU服务器数量需项目方确认。"
    )

    assert SchemeWriterAgent._project_fact_violations(content, item, [evidence]) == []


def test_project_fact_boundary_ignores_structural_enumeration() -> None:
    from apps.enterprise_document.agents.scheme_writer_agent import SchemeWriterAgent

    item = ProjectInputSchema.model_validate(
        {
            "task_id": "task_structure",
            "task_type": "scheme_generation",
            "user_query": "生成报告",
            "generation_requirements": {"required_sections": ["正文"]},
            "output_schema": {"required_sections": ["正文"]},
        }
    )
    assert SchemeWriterAgent._project_fact_violations(
        "本章节从以下四个方面展开说明。",
        item,
        [],
    ) == []


def test_stage1_minimal_gate_does_not_block_domain_content() -> None:
    from types import SimpleNamespace

    from apps.enterprise_document.agents.scheme_writer_agent import SchemeWriterAgent
    from schemas.model import ModelResponseSchema
    from schemas.rag import RAGContextSchema

    class InventingAgent(SchemeWriterAgent):
        def _call_model(self, *args, **kwargs):  # type: ignore[override]
            return ModelResponseSchema(
                model_call_id="invented",
                task_id="task_invented",
                run_id="run_invented",
                model_name="stub",
                success=True,
                content="项目将采购两台GPU服务器，并组建5人技术团队。",
                finish_reason="stop",
                created_at=NOW,
            )

    item = ProjectInputSchema.model_validate(
        {
            "task_id": "task_invented",
            "task_type": "scheme_generation",
            "user_query": "生成资源说明",
            "generation_requirements": {
                "required_sections": ["资源说明"],
                "citation_required_sections": [],
                "min_section_chars": 10,
            },
            "output_schema": {"required_sections": ["资源说明"]},
        }
    )
    section = InventingAgent()._generate_section(
        SimpleNamespace(run_id="run_invented", task_id="task_invented"),
        document_id="document_invented",
        project_input=item,
        section_title="资源说明",
        section_order=1,
        rag_context=RAGContextSchema(context_text="", max_context_chars=6000),
        citations=[],
        structured_facts=[],
        previous_sections=[],
    )

    assert section.status == ExecutionStatus.SUCCESS
    assert section.eval_result is not None
    assert section.eval_result.failures == []
    assert section.extra["project_fact_violations"] == []


def test_project_fact_boundary_ignores_generic_technical_design_terms() -> None:
    from apps.enterprise_document.agents.scheme_writer_agent import SchemeWriterAgent

    item = ProjectInputSchema.model_validate(
        {
            "task_id": "task_tech_terms",
            "task_type": "scheme_generation",
            "user_query": "建设RAG-Agent系统",
            "generation_requirements": {"required_sections": ["技术方案"]},
            "output_schema": {"required_sections": ["技术方案"]},
        }
    )
    content = (
        "系统可采用Docker和Kubernetes进行容器化部署。"
        "安全设计可采用JWT认证，并通过WebSocket推送运行事件。"
    )

    assert SchemeWriterAgent._project_fact_violations(content, item, []) == []


def test_project_fact_boundary_accepts_resource_recommendation_but_rejects_commitment() -> None:
    from apps.enterprise_document.agents.scheme_writer_agent import SchemeWriterAgent

    item = ProjectInputSchema.model_validate(
        {
            "task_id": "task_resource_recommendation",
            "task_type": "scheme_generation",
            "user_query": "生成资源配置建议",
            "generation_requirements": {"required_sections": ["资源配置"]},
            "output_schema": {"required_sections": ["资源配置"]},
        }
    )

    assert SchemeWriterAgent._project_fact_violations(
        "建议根据实际并发量测算服务器数量，GPU型号需项目方确认。",
        item,
        [],
    ) == []
    assert SchemeWriterAgent._project_fact_violations(
        "本项目将配置两台GPU服务器。",
        item,
        [],
    )


def test_deterministic_query_expansion_has_no_fixed_business_scenario() -> None:
    from rag.query.query_expander import QueryExpander

    expander = QueryExpander(use_llm=False)
    rewrites = expander._deterministic_rewrite_queries("分析设备故障", 5)
    combined = "\n".join(rewrites)

    assert "招投标" not in combined
    assert "功能点估算" not in combined
    assert "RAG Agent" not in combined
    assert all("分析设备故障" in item for item in rewrites)


def test_rag_quality_judge_has_no_fixed_business_noise_terms() -> None:
    from rag.judge.rag_quality_judge import CRAGJudge

    judge = CRAGJudge(use_llm=False)
    judgement = judge._deterministic_judge_chunk(
        query="分析设备故障",
        result={
            "chunk_id": "chunk_1",
            "text": "设备故障分析，同时包含招投标和功能点估算的历史资料。",
            "score": 0.5,
        },
        rank=1,
    )

    assert judgement["noise_terms"] == []


def test_project_fact_boundary_distinguishes_goal_training_and_resource_commitment() -> None:
    from apps.enterprise_document.agents.scheme_writer_agent import SchemeWriterAgent

    item = ProjectInputSchema.model_validate(
        {
            "task_id": "task_fact_types",
            "task_type": "scheme_generation",
            "user_query": "生成建设方案",
            "generation_requirements": {"required_sections": ["项目概述"]},
            "output_schema": {"required_sections": ["项目概述"]},
        }
    )

    allowed = (
        "系统建设：设计并开发一套完整的RAG-Agent系统，满足企业日常运营和决策需求。"
        "培训计划：为相关人员提供系统使用培训，确保他们能够熟练掌握系统功能。"
    )
    rejected = (
        "我们将采购最新的服务器和网络设备，并配置足够的内存和硬盘空间。"
        "同时招聘高级技术人员并组建项目小组。"
    )

    assert SchemeWriterAgent._project_fact_violations(allowed, item, []) == []
    violations = SchemeWriterAgent._project_fact_violations(rejected, item, [])
    assert len(violations) == 2


def test_legacy_scope_keyword_gate_is_disabled() -> None:
    from apps.enterprise_document.agents.scheme_writer_agent import SchemeWriterAgent

    content = (
        "本项目拟建设企业级RAG-Agent系统。\n"
        "技术选型采用React、Golang和Kubernetes。\n"
        "培训计划：组织相关人员进行系统使用培训。"
    )

    # Scope is now evaluated semantically against the dynamic section plan;
    # chapter-name keyword blacklists no longer produce hard failures.
    assert SchemeWriterAgent._section_scope_violations(content, "项目概述") == []


def test_resource_contract_degrades_to_sizing_principles_without_inputs() -> None:
    from apps.enterprise_document.agents.scheme_writer_agent import SchemeWriterAgent

    item = ProjectInputSchema.model_validate(
        {
            "task_id": "task_resource_contract",
            "task_type": "scheme_generation",
            "user_query": "生成资源配置",
            "generation_requirements": {"required_sections": ["资源配置"]},
            "output_schema": {"required_sections": ["资源配置"]},
        }
    )

    contract = SchemeWriterAgent._section_generation_contract("资源配置", item)
    assert "测算维度" in contract
    assert "采购" in contract
    assert "确定承诺" in contract
    assert "当前章节标题" in contract


def test_stage1_minimal_gate_does_not_invoke_semantic_rewrite() -> None:
    from types import SimpleNamespace

    from apps.enterprise_document.agents.scheme_writer_agent import SchemeWriterAgent
    from schemas.model import ModelResponseSchema
    from schemas.rag import RAGContextSchema

    class ValidationRewriteAgent(SchemeWriterAgent):
        def __init__(self):
            super().__init__()
            self.responses = [
                ModelResponseSchema(
                    model_call_id="initial_resource",
                    task_id="task_resource_rewrite",
                    run_id="run_resource_rewrite",
                    model_name="stub",
                    success=True,
                    content="本项目将采购两台GPU服务器，并招聘5名高级工程师。",
                    finish_reason="stop",
                    created_at=NOW,
                )
            ]

        def _call_model(self, *args, **kwargs):  # type: ignore[override]
            return self.responses.pop(0)

    item = ProjectInputSchema.model_validate(
        {
            "task_id": "task_resource_rewrite",
            "task_type": "scheme_generation",
            "user_query": "生成资源配置",
            "generation_requirements": {
                "required_sections": ["资源配置"],
                "citation_required_sections": [],
                "min_section_chars": 10,
                "max_section_retries": 1,
            },
            "output_schema": {"required_sections": ["资源配置"]},
        }
    )
    section = ValidationRewriteAgent()._generate_section(
        SimpleNamespace(run_id="run_resource_rewrite", task_id="task_resource_rewrite"),
        document_id="document_resource_rewrite",
        project_input=item,
        section_title="资源配置",
        section_order=1,
        rag_context=RAGContextSchema(context_text="", max_context_chars=6000),
        citations=[],
        structured_facts=[],
        previous_sections=[],
    )

    assert section.status == ExecutionStatus.SUCCESS
    assert section.extra["validation_rewrite_model_call_id"] is None
    assert section.extra["project_fact_violations"] == []


def test_semantic_scope_issue_is_partial_not_failed() -> None:
    from types import SimpleNamespace

    from apps.enterprise_document.agents.scheme_writer_agent import SchemeWriterAgent
    from apps.enterprise_document.schemas.scheme_writer_schema import (
        SemanticGateIssueSchema,
        SemanticGateResultSchema,
    )
    from schemas.model import ModelResponseSchema
    from schemas.rag import RAGContextSchema

    class _ScopeJudge:
        def judge(self, **kwargs):
            return (
                SemanticGateResultSchema(
                    decision="partial",
                    issues=[
                        SemanticGateIssueSchema(
                            issue_type="section_scope_drift",
                            severity="soft_failure",
                            claim="该段展开了兄弟章节内容",
                            reason="minor semantic scope drift",
                            recommended_action="human_review",
                            confidence=0.8,
                        )
                    ],
                ),
                None,
            )

    class _Agent(SchemeWriterAgent):
        def __init__(self):
            super().__init__(enable_semantic_gate=True)
            self.semantic_judge = _ScopeJudge()

        def _call_model(self, *args, **kwargs):  # type: ignore[override]
            return ModelResponseSchema(
                model_call_id="scope_generation",
                task_id="task_scope",
                run_id="run_scope",
                model_name="stub",
                success=True,
                content="本章节完成核心说明，同时略有跨章节展开，但正文完整。",
                finish_reason="stop",
                created_at=NOW,
            )

    item = ProjectInputSchema.model_validate(
        {
            "task_id": "task_scope",
            "task_type": "scheme_generation",
            "user_query": "生成报告",
            "generation_requirements": {
                "required_sections": ["章节A", "章节B"],
                "citation_required_sections": [],
                "min_section_chars": 10,
                "max_section_retries": 0,
            },
            "output_schema": {"required_sections": ["章节A", "章节B"]},
        }
    )
    section = _Agent()._generate_section(
        SimpleNamespace(run_id="run_scope", task_id="task_scope"),
        document_id="document_scope",
        project_input=item,
        section_title="章节A",
        section_order=1,
        rag_context=RAGContextSchema(context_text="", max_context_chars=6000),
        citations=[],
        structured_facts=[],
        previous_sections=[],
    )

    assert section.status == ExecutionStatus.PARTIAL_SUCCESS
    assert section.error is None
    assert section.eval_result is not None
    assert section.eval_result.failures == []
    assert "semantic:section_scope_drift" in section.eval_result.warnings


def test_semantic_judge_cannot_hide_explicit_unsupported_quantity() -> None:
    from apps.enterprise_document.services.semantic_section_judge import (
        SemanticSectionJudge,
    )

    issues = SemanticSectionJudge._merge_deterministic_candidates(
        [],
        [
            {
                "claim": "本项目将部署4台GPU服务器。",
                "reason": "project_specific_fact_not_supported",
            }
        ],
    )

    assert len(issues) == 1
    assert issues[0].severity == "hard_failure"
    assert issues[0].issue_type == "unsupported_quantitative_claim"


def test_semantic_judge_downgrades_scope_hard_failure_to_soft() -> None:
    from apps.enterprise_document.services.semantic_section_judge import (
        SemanticSectionJudge,
    )

    judge = SemanticSectionJudge(
        model_gateway=None,
        model_name="fake_llm",
    )
    issue = judge._normalize_issue(
        {
            "issue_type": "section_scope_drift",
            "severity": "hard_failure",
            "claim": "正文略微展开了相邻章节。",
            "reason": "scope drift",
            "recommended_action": "rewrite",
            "confidence": 0.99,
        }
    )

    assert issue is not None
    assert issue.severity == "soft_failure"


def test_semantic_judge_invalid_json_uses_conservative_fallback() -> None:
    from contracts.base_client import BaseLLMClient
    from apps.enterprise_document.services.semantic_section_judge import (
        SemanticSectionJudge,
    )
    from model_gateway.model_gateway import ModelGateway
    from schemas.model import ModelRequestSchema, ModelResponseSchema

    class _InvalidJsonClient(BaseLLMClient):
        model_name = "invalid_json_model"

        def generate(self, request: ModelRequestSchema) -> ModelResponseSchema:
            return ModelResponseSchema(
                model_call_id=request.model_call_id,
                task_id=request.task_id,
                run_id=request.run_id,
                model_name=self.model_name,
                success=True,
                content="这不是JSON",
                finish_reason="stop",
                created_at=request.created_at,
            )

    gateway = ModelGateway(default_model_name="invalid_json_model")
    gateway.register_client(_InvalidJsonClient())
    judge = SemanticSectionJudge(
        model_gateway=gateway,
        model_name="invalid_json_model",
    )
    item = ProjectInputSchema.model_validate(
        {
            "task_id": "task_invalid_json",
            "task_type": "scheme_generation",
            "user_query": "生成报告",
            "generation_requirements": {"required_sections": ["说明"]},
            "output_schema": {"required_sections": ["说明"]},
        }
    )
    result, response = judge.judge(
        task_id="task_invalid_json",
        run_id="run_invalid_json",
        created_at=NOW,
        section_id="section_invalid_json",
        section_title="说明",
        content="本项目将采购服务器。",
        project_input=item,
        citations=[],
        required_sections=["说明"],
        deterministic_candidates=[
            {
                "claim": "本项目将采购服务器。",
                "reason": "project_specific_fact_not_supported",
            }
        ],
        overlong=False,
    )

    assert response is not None
    assert result.fallback_used is True
    assert result.decision == "partial"
    assert result.issues[0].severity == "soft_failure"


def test_semantic_hard_issue_is_advisory_only_in_stage1() -> None:
    from types import SimpleNamespace

    from apps.enterprise_document.agents.scheme_writer_agent import SchemeWriterAgent
    from apps.enterprise_document.schemas.scheme_writer_schema import (
        SemanticGateIssueSchema,
        SemanticGateResultSchema,
    )
    from schemas.model import ModelResponseSchema
    from schemas.rag import RAGContextSchema

    class _Judge:
        def judge(self, **kwargs):
            return (
                SemanticGateResultSchema(
                    decision="fail",
                    issues=[
                        SemanticGateIssueSchema(
                            issue_type="unsupported_quantitative_claim",
                            severity="hard_failure",
                            claim="本项目部署4台服务器。",
                            reason="no project-input support",
                            recommended_action="human_review",
                            confidence=0.99,
                        )
                    ],
                ),
                None,
            )

    class _Agent(SchemeWriterAgent):
        def __init__(self):
            super().__init__(enable_semantic_gate=True)
            self.semantic_judge = _Judge()

        def _call_model(self, *args, **kwargs):  # type: ignore[override]
            return ModelResponseSchema(
                model_call_id="semantic_advisory_generation",
                task_id="task_semantic_advisory",
                run_id="run_semantic_advisory",
                model_name="stub",
                success=True,
                content="本项目部署4台服务器，并形成完整说明。",
                finish_reason="stop",
                created_at=NOW,
            )

    item = ProjectInputSchema.model_validate(
        {
            "task_id": "task_semantic_advisory",
            "task_type": "scheme_generation",
            "user_query": "生成说明",
            "generation_requirements": {
                "required_sections": ["说明"],
                "citation_required_sections": [],
                "min_section_chars": 10,
                "max_section_retries": 0,
            },
            "output_schema": {"required_sections": ["说明"]},
        }
    )
    section = _Agent()._generate_section(
        SimpleNamespace(run_id="run_semantic_advisory", task_id="task_semantic_advisory"),
        document_id="document_semantic_advisory",
        project_input=item,
        section_title="说明",
        section_order=1,
        rag_context=RAGContextSchema(context_text="", max_context_chars=6000),
        citations=[],
        structured_facts=[],
        previous_sections=[],
    )

    assert section.status == ExecutionStatus.PARTIAL_SUCCESS
    assert section.error is None
    assert section.eval_result.failures == []
    assert "semantic:unsupported_quantitative_claim" in section.eval_result.warnings

