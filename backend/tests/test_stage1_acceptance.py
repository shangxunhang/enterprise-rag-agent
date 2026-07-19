# =============================================================================
# 中文阅读说明：自动化测试模块，用于验证主链、边界条件和回归行为。
# 主要定义：test_demo_project_title_is_derived_from_user_request、test_project_input_preserves_caller_defined_sections、test_truncation_detects_token_limit_and_unclosed_json、_FailingAgent、test_workflow_propagates_structured_failure_and_stops、test_hard_gate_rejects_unverified_citation_binding、test_production_project_input_rejects_demo_defaults、test_project_fact_boundary_rejects_invented_resources、test_project_fact_boundary_accepts_qualified_or_supported_facts、test_task_and_rag_filters_preserve_source_material_ids等。建议先从公开入口函数开始，再沿调用关系向下阅读。
# =============================================================================
"""Acceptance-level tests for the corrected stage-1 mainline contracts."""

from __future__ import annotations

from agent.agent_registry import AgentRegistry
from agent.base_agent import BaseAgent
from agent.runtime.graph_state import GraphStateSchema
from agent.runtime.native_workflow_engine import NativeWorkflowEngine
from agent.runtime.shared_state_schema import SharedStateSchema
from agent.runtime.workflow_schema import WorkflowDefinitionSchema, WorkflowStepSchema
from application.project_input_factory import ProjectInputFactory
from apps.enterprise_document.schemas.project_input_schema import ProjectInputSchema
from apps.enterprise_document.schemas.scheme_writer_schema import (
    SchemeDraftSchema,
    SchemeSectionSchema,
    SectionEvalSchema,
    TruncationCheckSchema,
)
from apps.enterprise_document.services.output_validation import detect_truncation
from apps.enterprise_document.services.scheme_writer.advisory_service import (
    SectionAdvisoryService,
)
from apps.enterprise_document.services.scheme_writer.document_planning_service import (
    DocumentPlanningService,
)
from apps.enterprise_document.services.scheme_writer.prompt_service import (
    SectionPromptService,
)
from eval.agent.hard_gate import evaluate_scheme_draft
from schemas.agent import AgentResultSchema
from schemas.citation import CitationBindingSchema
from schemas.common import ErrorSchema
from schemas.context import ContextBundleSchema, TaskContextSchema, UserContextSchema
from schemas.status import ExecutionStatus

NOW = "2026-07-14T00:00:00+00:00"


# 阅读注释（函数）：处理 测试 演示 项目 title is derived from user 请求 相关逻辑。
def test_demo_project_title_is_derived_from_user_request() -> None:
    """处理 测试 演示 项目 title is derived from user 请求 相关逻辑。

    返回:
        None

    阅读提示:
        主要直接调用：build, ProjectInputFactory。
    """
    item = ProjectInputFactory().build(
        task_id="task_demo_title",
        user_input="生成一个政务云建设方案",
        allow_demo_defaults=True,
    )

    assert item.output_schema.document_title == "政务云建设方案"
    assert "RAG-Agent" not in item.output_schema.document_title


# 阅读注释（函数）：处理 测试 项目 输入 preserves caller defined sections 相关逻辑。
def test_project_input_preserves_caller_defined_sections() -> None:
    """处理 测试 项目 输入 preserves caller defined sections 相关逻辑。

    返回:
        None

    阅读提示:
        主要直接调用：ProjectInputSchema.model_validate。
    """
    item = ProjectInputSchema.model_validate(
        {
            "task_id": "task_custom",
            "tenant_id": "tenant_a",
            "project_name": "自定义项目",
            "task_type": "scheme_generation",
            "user_query": "生成定制报告",
            "source_materials": [{"material_type": "policy", "doc_ids": ["doc_1"]}],
            "generation_requirements": {
                "required_sections": ["现状", "风险"],
                "citation_required_sections": ["风险"],
            },
            "output_schema": {
                "document_title": "自定义报告",
                "required_sections": ["现状", "风险"],
            },
            "metadata": {"request_source": "api"},
        }
    )

    assert item.tenant_id == "tenant_a"
    assert item.generation_requirements.required_sections == ["现状", "风险"]
    assert item.output_schema.required_sections == ["现状", "风险"]
    assert item.output_schema.document_title == "自定义报告"


# 阅读注释（函数）：处理 测试 truncation detects Token limit and unclosed JSON 相关逻辑。
def test_truncation_detects_token_limit_and_unclosed_json() -> None:
    """处理 测试 truncation detects Token limit and unclosed JSON 相关逻辑。

    返回:
        None

    阅读提示:
        主要直接调用：detect_truncation。
    """
    token_limited = detect_truncation("正文尚未结束，", "length", min_chars=10)
    invalid_json = detect_truncation('{"name": "unfinished"', "stop")

    assert token_limited.truncated is True
    assert "finish_reason indicates token limit" in token_limited.reasons
    assert invalid_json.truncated is True
    assert invalid_json.json_closed is False


# 阅读注释（类）：封装 failing Agent，负责接收状态、调用工具或服务并返回统一 Agent 结果。
class _FailingAgent(BaseAgent):
    """封装 failing Agent，负责接收状态、调用工具或服务并返回统一 Agent 结果。"""
    agent_name = "FailingAgent"
    agent_type = "sub_agent"

    # 阅读注释（函数）：执行 _FailingAgent 的主流程。
    def run(self, shared_state: SharedStateSchema) -> AgentResultSchema:
        """执行 _FailingAgent 的主流程。

        参数:
            shared_state: shared 状态，具体约束请结合类型标注和调用方确认。

        返回:
            AgentResultSchema

        阅读提示:
            主要直接调用：ErrorSchema, AgentResultSchema。
        """
        error = ErrorSchema(
            error_code="LOW_LEVEL_FAILURE",
            error_type="RuntimeError",
            message="底层工具失败",
            user_visible_message="处理失败，请稍后重试。",
            retryable=True,
            failed_node="tool_x",
            created_at=NOW,
        )
        return AgentResultSchema(
            result_id="result_failed",
            task_id=shared_state.task_id,
            run_id=shared_state.run_id,
            agent_name=self.agent_name,
            agent_type=self.agent_type,
            status=ExecutionStatus.RETRYABLE_FAILED,
            result_type="failure",
            error=error,
            error_message=error.message,
        )


# 阅读注释（函数）：处理 测试 工作流 propagates structured failure and stops 相关逻辑。
def test_workflow_propagates_structured_failure_and_stops() -> None:
    """处理 测试 工作流 propagates structured failure and stops 相关逻辑。

    返回:
        None

    阅读提示:
        主要直接调用：AgentRegistry, registry.register, _FailingAgent, WorkflowDefinitionSchema, WorkflowStepSchema, GraphStateSchema, ContextBundleSchema, UserContextSchema。
    """
    registry = AgentRegistry()
    registry.register(_FailingAgent())
    workflow = WorkflowDefinitionSchema(
        workflow_id="wf",
        workflow_name="wf",
        task_type="test",
        workflow_version="v1",
        steps=[
            WorkflowStepSchema(
                step_id="s1",
                step_name="first",
                step_type="agent",
                target_name="FailingAgent",
                order=1,
            ),
            WorkflowStepSchema(
                step_id="s2",
                step_name="second",
                step_type="agent",
                target_name="FailingAgent",
                order=2,
            ),
        ],
        created_at=NOW,
    )
    state = GraphStateSchema(
        task_id="task_1",
        run_id="run_1",
        task_type="test",
        user_input="test",
        context_bundle=ContextBundleSchema(
            user=UserContextSchema(user_query="test"),
            task=TaskContextSchema(task_id="task_1", run_id="run_1", task_type="test"),
        ),
        created_at=NOW,
    )

    execution = NativeWorkflowEngine(registry).execute(workflow, state)
    results = execution.node_results

    assert len(results) == 1
    assert state.status == ExecutionStatus.RETRYABLE_FAILED
    assert state.errors[-1].error_code == "LOW_LEVEL_FAILURE"
    assert state.workflow_step_states["s1"].error is not None
    assert "s2" not in state.workflow_step_states


# 阅读注释（函数）：处理 测试 hard gate rejects unverified 引用 绑定关系 相关逻辑。
def test_hard_gate_rejects_unverified_citation_binding() -> None:
    """处理 测试 hard gate rejects unverified 引用 绑定关系 相关逻辑。

    返回:
        None

    阅读提示:
        主要直接调用：CitationBindingSchema, SchemeSectionSchema, TruncationCheckSchema, SectionEvalSchema, SchemeDraftSchema, evaluate_scheme_draft, join。
    """
    binding = CitationBindingSchema(
        binding_id="binding_unverified",
        citation_id="C1",
        target_document_id="document_1",
        target_section_id="section_1",
        target_paragraph_id="paragraph_1",
        target_claim_id="claim_1",
        source_document_id="doc_1",
        source_chunk_id="chunk_1",
        claim_text="系统采用防火墙。",
        quote_text="知识库支持知识检索。",
    )
    section = SchemeSectionSchema(
        section_id="section_1",
        section_title="安全设计",
        section_order=1,
        content="系统采用防火墙。[C1]",
        status=ExecutionStatus.SUCCESS,
        citation_ids=["C1"],
        citation_bindings=[binding],
        truncation=TruncationCheckSchema(truncated=False),
        eval_result=SectionEvalSchema(passed=True),
    )
    draft = SchemeDraftSchema(
        draft_id="draft_1",
        document_id="document_1",
        task_id="task_1",
        run_id="run_1",
        title="方案",
        full_text=section.content,
        sections=[section],
        required_sections=["安全设计"],
        citation_bindings=[binding],
        created_at=NOW,
    )

    result = evaluate_scheme_draft(
        draft,
        citation_required=True,
        citation_required_sections=["安全设计"],
        retrieved_chunk_ids=["chunk_1"],
        evidence_sufficient=True,
    )

    assert result.passed is False
    assert "Claim-Evidence" in "；".join(result.failures)
    assert result.metadata["unverified_binding_ids"] == ["binding_unverified"]


# 阅读注释（函数）：处理 测试 production 项目 输入 rejects 演示 defaults 相关逻辑。
def test_production_project_input_rejects_demo_defaults() -> None:
    """处理 测试 production 项目 输入 rejects 演示 defaults 相关逻辑。

    返回:
        None

    阅读提示:
        主要直接调用：resolve, Path, importlib.util.spec_from_file_location, importlib.util.module_from_spec, spec.loader.exec_module, module.build_project_input, str, AssertionError。
    """
    import importlib.util
    from pathlib import Path

    script = Path(__file__).resolve().parents[2] / "scripts" / "run_demo.py"
    spec = importlib.util.spec_from_file_location("stage1_run_demo", script)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    try:
        module.build_project_input(
            task_id="task_prod",
            user_input="生成报告",
            raw_project_input={
                "task_id": "task_prod",
                "task_type": "scheme_generation",
                "user_query": "生成报告",
            },
            allow_demo_defaults=False,
        )
    except ValueError as exc:
        assert "required_sections" in str(exc)
    else:
        raise AssertionError("production input must not receive demo sections")

    item = module.build_project_input(
        task_id="task_prod",
        user_input="生成报告",
        raw_project_input={
            "task_id": "task_prod",
            "task_type": "scheme_generation",
            "user_query": "生成报告",
            "generation_requirements": {"required_sections": ["自定义章节"]},
            "output_schema": {"document_title": "自定义报告"},
        },
        allow_demo_defaults=False,
    )
    assert item.generation_requirements.required_sections == ["自定义章节"]
    assert item.output_schema.required_sections == ["自定义章节"]
    assert item.output_schema.document_title == "自定义报告"


# 阅读注释（函数）：处理 测试 项目 fact boundary rejects invented resources 相关逻辑。
def test_project_fact_boundary_rejects_invented_resources() -> None:
    """处理 测试 项目 fact boundary rejects invented resources 相关逻辑。

    返回:
        None

    阅读提示:
        主要直接调用：ProjectInputSchema.model_validate, SchemeWriterAgent._project_fact_violations, len, all。
    """
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
    violations = SectionAdvisoryService._project_fact_violations(
        "项目将采购两台GPU服务器，并组建5人技术团队。",
        item,
        [],
    )

    assert len(violations) >= 1
    assert all(
        entry["reason"] == "project_specific_fact_not_supported"
        for entry in violations
    )


# 阅读注释（函数）：处理 测试 项目 fact boundary accepts qualified or supported facts 相关逻辑。
def test_project_fact_boundary_accepts_qualified_or_supported_facts() -> None:
    """处理 测试 项目 fact boundary accepts qualified or supported facts 相关逻辑。

    返回:
        None

    阅读提示:
        主要直接调用：ProjectInputSchema.model_validate, CitationSchema, SchemeWriterAgent._project_fact_violations。
    """
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

    assert SectionAdvisoryService._project_fact_violations(content, item, [evidence]) == []


# 阅读注释（函数）：处理 测试 任务 and RAG filters preserve source material 标识集合 相关逻辑。
def test_task_and_rag_filters_preserve_source_material_ids() -> None:
    """处理 测试 任务 and RAG filters preserve source material 标识集合 相关逻辑。

    返回:
        None

    阅读提示:
        主要直接调用：resolve, Path, importlib.util.spec_from_file_location, importlib.util.module_from_spec, spec.loader.exec_module, module.build_task, ExecutorStub, SchemeWriterAgent。
    """
    import importlib.util
    from pathlib import Path
    from types import SimpleNamespace

    from apps.enterprise_document.agents.scheme_writer_agent import SchemeWriterAgent

    script = Path(__file__).resolve().parents[2] / "scripts" / "run_demo.py"
    spec = importlib.util.spec_from_file_location("stage1_run_demo_sources", script)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    payload = {
        "task_id": "task_sources",
        "task_type": "scheme_generation",
        "user_query": "根据指定资料生成报告",
        "source_materials": [
            {
                "material_type": "knowledge_base",
                "file_ids": ["file_1"],
                "doc_ids": ["doc_1", "doc_2"],
                "metadata": {"kb_id": "kb_1"},
            }
        ],
        "generation_requirements": {"required_sections": ["正文"]},
        "output_schema": {"required_sections": ["正文"]},
    }
    task = module.build_task(
        task_id="task_sources",
        run_id="run_sources",
        user_input=payload["user_query"],
        created_at=NOW,
        project_input=payload,
        allow_demo_defaults=False,
    )
    assert task.file_ids == ["file_1"]
    assert task.doc_ids == ["doc_1", "doc_2"]
    assert task.kb_ids == ["kb_1"]

    # 阅读注释（类）：封装 executor stub，集中封装相关状态、依赖和行为。
    class ExecutorStub:
        """封装 executor stub，集中封装相关状态、依赖和行为。"""
        # 阅读注释（函数）：初始化 ExecutorStub，保存运行所需的依赖、配置或状态。
        def __init__(self):
            """初始化 ExecutorStub，保存运行所需的依赖、配置或状态。

            返回:
                未显式标注；请结合调用方和实际返回语句理解。
            """
            self.call = None

        # 阅读注释（函数）：执行 ExecutorStub。
        def retrieve(self, call):
            """执行 ExecutorStub。

            参数:
                call: call，具体约束请结合类型标注和调用方确认。

            返回:
                未显式标注；请结合调用方和实际返回语句理解。

            阅读提示:
                主要直接调用：SimpleNamespace。
            """
            self.call = call
            return SimpleNamespace(model_dump=lambda: {}, success=False)

    executor = ExecutorStub()
    agent = SchemeWriterAgent(rag_service=executor)
    state = SimpleNamespace(
        run_id="run_sources",
        task_id="task_sources",
        task={"kb_ids": ["kb_1"]},
        updated_at=None,
        created_at=NOW,
        tool_results={},
    )
    project_input = ProjectInputSchema.model_validate(payload)
    agent.evidence_service._call_rag_tool(state, project_input)

    assert executor.call.filters["doc_ids"] == ["doc_1", "doc_2"]
    assert executor.call.filters["file_ids"] == ["file_1"]
    assert executor.call.kb_ids == ["kb_1"]


# 阅读注释（函数）：处理 测试 文档 计划 is derived from 项目 输入 without fixed sections 相关逻辑。
def test_document_plan_is_derived_from_project_input_without_fixed_sections() -> None:
    """处理 测试 文档 计划 is derived from 项目 输入 without fixed sections 相关逻辑。

    返回:
        None

    阅读提示:
        主要直接调用：ProjectInputSchema.model_validate, SchemeWriterAgent._build_document_plan。
    """
    from apps.enterprise_document.agents.scheme_writer_agent import SchemeWriterAgent

    item = ProjectInputSchema.model_validate(
        {
            "task_id": "task_plan",
            "task_type": "scheme_generation",
            "user_query": "生成自定义报告",
            "generation_requirements": {
                "required_sections": ["现状", "结论"],
                "citation_required_sections": ["结论"],
            },
            "output_schema": {
                "document_title": "自定义报告",
                "required_sections": ["现状", "结论"],
            },
        }
    )
    plan = DocumentPlanningService._build_document_plan(
        run_id="run_plan",
        document_id="document_plan",
        project_input=item,
        required_sections=item.generation_requirements.required_sections,
        created_at=NOW,
    )

    assert [entry.section_title for entry in plan.sections] == ["现状", "结论"]
    assert [entry.citation_required for entry in plan.sections] == [False, True]
    assert plan.planning_source == "project_input"


# 阅读注释（函数）：处理 测试 项目 输入 rejects inconsistent 章节 contracts 相关逻辑。
def test_project_input_rejects_inconsistent_section_contracts() -> None:
    """处理 测试 项目 输入 rejects inconsistent 章节 contracts 相关逻辑。

    返回:
        None

    阅读提示:
        主要直接调用：ProjectInputSchema.model_validate, str, AssertionError。
    """
    try:
        ProjectInputSchema.model_validate(
            {
                "task_id": "task_bad_sections",
                "task_type": "scheme_generation",
                "user_query": "生成报告",
                "generation_requirements": {"required_sections": ["A", "B"]},
                "output_schema": {"required_sections": ["A", "C"]},
            }
        )
    except ValueError as exc:
        assert "must match" in str(exc)
    else:
        raise AssertionError("inconsistent section contracts must fail")


# 阅读注释（函数）：处理 测试 truncated 章节 uses compact full retry without continuation 相关逻辑。
def test_truncated_section_uses_compact_full_retry_without_continuation() -> None:
    """处理 测试 truncated 章节 uses compact full retry without continuation 相关逻辑。

    返回:
        None

    阅读提示:
        主要直接调用：ProjectInputSchema.model_validate, SimpleNamespace, _generate_section, RetryAgent, RAGContextSchema。
    """
    from types import SimpleNamespace

    from apps.enterprise_document.agents.scheme_writer_agent import SchemeWriterAgent
    from schemas.model import ModelResponseSchema
    from schemas.rag import RAGContextSchema

    # 阅读注释（类）：封装 retry Agent，负责接收状态、调用工具或服务并返回统一 Agent 结果。
    class RetryAgent(SchemeWriterAgent):
        """封装 retry Agent，负责接收状态、调用工具或服务并返回统一 Agent 结果。"""
        # 阅读注释（函数）：初始化 RetryAgent，保存运行所需的依赖、配置或状态。
        def __init__(self):
            """初始化 RetryAgent，保存运行所需的依赖、配置或状态。

            返回:
                未显式标注；请结合调用方和实际返回语句理解。

            阅读提示:
                主要直接调用：__init__, super, ModelResponseSchema。
            """
            super().__init__()
            self.responses = [
                ModelResponseSchema(
                    model_call_id="initial",
                    task_id="task_retry",
                    run_id="run_retry",
                    model_name="stub",
                    success=True,
                    content="这是一个尚未完成的章节，",
                    finish_reason="length",
                    created_at=NOW,
                ),
                ModelResponseSchema(
                    model_call_id="retry",
                    task_id="task_retry",
                    run_id="run_retry",
                    model_name="stub",
                    success=True,
                    content="本章节已经完整重新生成，并以完整句子结束。",
                    finish_reason="stop",
                    created_at=NOW,
                ),
            ]

        # 阅读注释（函数）：处理 call 模型 相关逻辑。
        def _call_model(self, *args, **kwargs):  # type: ignore[override]
            """处理 call 模型 相关逻辑。

            参数:
                *args: 额外位置参数。
                **kwargs: 额外关键字参数。

            返回:
                未显式标注；请结合调用方和实际返回语句理解。

            阅读提示:
                主要直接调用：self.responses.pop。
            """
            return self.responses.pop(0)

    item = ProjectInputSchema.model_validate(
        {
            "task_id": "task_retry",
            "task_type": "scheme_generation",
            "user_query": "生成完整章节",
            "generation_requirements": {
                "required_sections": ["正文"],
                "min_section_chars": 10,
                "max_section_retries": 1,
                "citation_required_sections": [],
            },
            "output_schema": {"required_sections": ["正文"]},
        }
    )
    state = SimpleNamespace(run_id="run_retry", task_id="task_retry")
    section = RetryAgent().section_generation_service._generate_section(
        state,
        document_id="document_retry",
        project_input=item,
        section_title="正文",
        section_order=1,
        rag_context=RAGContextSchema(context_text="证据", max_context_chars=6000),
        citations=[],
        structured_facts=[],
        previous_sections=[],
    )

    assert section.status == ExecutionStatus.SUCCESS
    assert section.truncation.truncated is False
    assert section.content == "本章节已经完整重新生成，并以完整句子结束。"
    assert section.extra["continuation_model_call_id"] is None
    assert section.extra["truncation_retry_model_call_ids"] == ["retry"]


# 阅读注释（函数）：处理 测试 项目 fact boundary ignores structural enumeration 相关逻辑。
def test_project_fact_boundary_ignores_structural_enumeration() -> None:
    """处理 测试 项目 fact boundary ignores structural enumeration 相关逻辑。

    返回:
        None

    阅读提示:
        主要直接调用：ProjectInputSchema.model_validate, SchemeWriterAgent._project_fact_violations。
    """
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
    assert SectionAdvisoryService._project_fact_violations(
        "本章节从以下四个方面展开说明。",
        item,
        [],
    ) == []


# 阅读注释（函数）：处理 测试 stage1 minimal gate does not block domain content 相关逻辑。
def test_stage1_minimal_gate_does_not_block_domain_content() -> None:
    """处理 测试 stage1 minimal gate does not block domain content 相关逻辑。

    返回:
        None

    阅读提示:
        主要直接调用：ProjectInputSchema.model_validate, _generate_section, InventingAgent, SimpleNamespace, RAGContextSchema。
    """
    from types import SimpleNamespace

    from apps.enterprise_document.agents.scheme_writer_agent import SchemeWriterAgent
    from schemas.model import ModelResponseSchema
    from schemas.rag import RAGContextSchema

    # 阅读注释（类）：封装 inventing Agent，负责接收状态、调用工具或服务并返回统一 Agent 结果。
    class InventingAgent(SchemeWriterAgent):
        """封装 inventing Agent，负责接收状态、调用工具或服务并返回统一 Agent 结果。"""
        # 阅读注释（函数）：处理 call 模型 相关逻辑。
        def _call_model(self, *args, **kwargs):  # type: ignore[override]
            """处理 call 模型 相关逻辑。

            参数:
                *args: 额外位置参数。
                **kwargs: 额外关键字参数。

            返回:
                未显式标注；请结合调用方和实际返回语句理解。

            阅读提示:
                主要直接调用：ModelResponseSchema。
            """
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
    section = InventingAgent().section_generation_service._generate_section(
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



# 阅读注释（函数）：处理 测试 项目 fact boundary ignores generic technical design terms 相关逻辑。
def test_project_fact_boundary_ignores_generic_technical_design_terms() -> None:
    """处理 测试 项目 fact boundary ignores generic technical design terms 相关逻辑。

    返回:
        None

    阅读提示:
        主要直接调用：ProjectInputSchema.model_validate, SchemeWriterAgent._project_fact_violations。
    """
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

    assert SectionAdvisoryService._project_fact_violations(content, item, []) == []


# 阅读注释（函数）：处理 测试 项目 fact boundary accepts resource recommendation but rejects commitment 相关逻辑。
def test_project_fact_boundary_accepts_resource_recommendation_but_rejects_commitment() -> None:
    """处理 测试 项目 fact boundary accepts resource recommendation but rejects commitment 相关逻辑。

    返回:
        None

    阅读提示:
        主要直接调用：ProjectInputSchema.model_validate, SchemeWriterAgent._project_fact_violations。
    """
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

    assert SectionAdvisoryService._project_fact_violations(
        "建议根据实际并发量测算服务器数量，GPU型号需项目方确认。",
        item,
        [],
    ) == []
    assert SectionAdvisoryService._project_fact_violations(
        "本项目将配置两台GPU服务器。",
        item,
        [],
    )

# 阅读注释（函数）：处理 测试 deterministic 查询 expansion has no fixed business scenario 相关逻辑。
def test_deterministic_query_expansion_has_no_fixed_business_scenario() -> None:
    """处理 测试 deterministic 查询 expansion has no fixed business scenario 相关逻辑。

    返回:
        None

    阅读提示:
        主要直接调用：QueryExpander, expander._deterministic_rewrite_queries, join, all。
    """
    from rag.query.query_expander import QueryExpander

    expander = QueryExpander(use_llm=False)
    rewrites = expander._deterministic_rewrite_queries("分析设备故障", 5)
    combined = "\n".join(rewrites)

    assert "招投标" not in combined
    assert "功能点估算" not in combined
    assert "RAG Agent" not in combined
    assert all("分析设备故障" in item for item in rewrites)


# 阅读注释（函数）：处理 测试 RAG 质量 judge has no fixed business noise terms 相关逻辑。
def test_rag_quality_judge_has_no_fixed_business_noise_terms() -> None:
    """处理 测试 RAG 质量 judge has no fixed business noise terms 相关逻辑。

    返回:
        None

    阅读提示:
        主要直接调用：CRAGJudge, judge._deterministic_judge_chunk。
    """
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

# 阅读注释（函数）：处理 测试 production entry does not import 演示 module 相关逻辑。
def test_production_entry_does_not_import_demo_module() -> None:
    """处理 测试 production entry does not import 演示 module 相关逻辑。

    返回:
        None

    阅读提示:
        主要直接调用：resolve, Path, read_text。
    """
    from pathlib import Path

    project_root = Path(__file__).resolve().parents[2]
    pipeline_source = (project_root / "scripts" / "run_pipeline.py").read_text(
        encoding="utf-8"
    )
    assert "from run_demo import" not in pipeline_source
    assert "import run_demo" not in pipeline_source
    assert "from mainline_runtime import run_mainline" in pipeline_source



# 阅读注释（函数）：处理 测试 项目 fact boundary distinguishes goal training and resource commitment 相关逻辑。
def test_project_fact_boundary_distinguishes_goal_training_and_resource_commitment() -> None:
    """处理 测试 项目 fact boundary distinguishes goal training and resource commitment 相关逻辑。

    返回:
        None

    阅读提示:
        主要直接调用：ProjectInputSchema.model_validate, SchemeWriterAgent._project_fact_violations, len。
    """
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

    assert SectionAdvisoryService._project_fact_violations(allowed, item, []) == []
    violations = SectionAdvisoryService._project_fact_violations(rejected, item, [])
    assert len(violations) == 2


# 阅读注释（函数）：处理 测试 legacy scope keyword gate is disabled 相关逻辑。
def test_legacy_scope_keyword_gate_is_disabled() -> None:
    """处理 测试 legacy scope keyword gate is disabled 相关逻辑。

    返回:
        None

    阅读提示:
        主要直接调用：SchemeWriterAgent._section_scope_violations。
    """
    from apps.enterprise_document.agents.scheme_writer_agent import SchemeWriterAgent

    content = (
        "本项目拟建设企业级RAG-Agent系统。\n"
        "技术选型采用React、Golang和Kubernetes。\n"
        "培训计划：组织相关人员进行系统使用培训。"
    )

    # Scope is now evaluated semantically against the dynamic section plan;
    # chapter-name keyword blacklists no longer produce hard failures.
    assert SectionPromptService._section_scope_violations(content, "项目概述") == []


# 阅读注释（函数）：处理 测试 resource contract degrades to sizing principles without inputs 相关逻辑。
def test_resource_contract_degrades_to_sizing_principles_without_inputs() -> None:
    """处理 测试 resource contract degrades to sizing principles without inputs 相关逻辑。

    返回:
        None

    阅读提示:
        主要直接调用：ProjectInputSchema.model_validate, SchemeWriterAgent._section_generation_contract。
    """
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

    contract = SectionPromptService._section_generation_contract("资源配置", item)
    assert "测算维度" in contract
    assert "采购" in contract
    assert "确定承诺" in contract
    assert "当前章节标题" in contract


# 阅读注释（函数）：处理 测试 stage1 minimal gate does not invoke semantic 改写 相关逻辑。
def test_stage1_minimal_gate_does_not_invoke_semantic_rewrite() -> None:
    """处理 测试 stage1 minimal gate does not invoke semantic 改写 相关逻辑。

    返回:
        None

    阅读提示:
        主要直接调用：ProjectInputSchema.model_validate, _generate_section, ValidationRewriteAgent, SimpleNamespace, RAGContextSchema。
    """
    from types import SimpleNamespace

    from apps.enterprise_document.agents.scheme_writer_agent import SchemeWriterAgent
    from schemas.model import ModelResponseSchema
    from schemas.rag import RAGContextSchema

    # 阅读注释（类）：封装 validation 改写 Agent，负责接收状态、调用工具或服务并返回统一 Agent 结果。
    class ValidationRewriteAgent(SchemeWriterAgent):
        """封装 validation 改写 Agent，负责接收状态、调用工具或服务并返回统一 Agent 结果。"""
        # 阅读注释（函数）：初始化 ValidationRewriteAgent，保存运行所需的依赖、配置或状态。
        def __init__(self):
            """初始化 ValidationRewriteAgent，保存运行所需的依赖、配置或状态。

            返回:
                未显式标注；请结合调用方和实际返回语句理解。

            阅读提示:
                主要直接调用：__init__, super, ModelResponseSchema。
            """
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

        # 阅读注释（函数）：处理 call 模型 相关逻辑。
        def _call_model(self, *args, **kwargs):  # type: ignore[override]
            """处理 call 模型 相关逻辑。

            参数:
                *args: 额外位置参数。
                **kwargs: 额外关键字参数。

            返回:
                未显式标注；请结合调用方和实际返回语句理解。

            阅读提示:
                主要直接调用：self.responses.pop。
            """
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
    section = ValidationRewriteAgent().section_generation_service._generate_section(
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


# 阅读注释（函数）：处理 测试 overlong 章节 uses dedicated compression pass 相关逻辑。
def test_overlong_section_uses_dedicated_compression_pass() -> None:
    """处理 测试 overlong 章节 uses dedicated compression pass 相关逻辑。

    返回:
        None

    阅读提示:
        主要直接调用：ProjectInputSchema.model_validate, _generate_section, CompressionAgent, SimpleNamespace, RAGContextSchema, len。
    """
    from types import SimpleNamespace

    from apps.enterprise_document.agents.scheme_writer_agent import SchemeWriterAgent
    from schemas.model import ModelResponseSchema
    from schemas.rag import RAGContextSchema

    # 阅读注释（类）：封装 compression Agent，负责接收状态、调用工具或服务并返回统一 Agent 结果。
    class CompressionAgent(SchemeWriterAgent):
        """封装 compression Agent，负责接收状态、调用工具或服务并返回统一 Agent 结果。"""
        # 阅读注释（函数）：初始化 CompressionAgent，保存运行所需的依赖、配置或状态。
        def __init__(self):
            """初始化 CompressionAgent，保存运行所需的依赖、配置或状态。

            返回:
                未显式标注；请结合调用方和实际返回语句理解。

            阅读提示:
                主要直接调用：__init__, super, ModelResponseSchema。
            """
            super().__init__()
            self.responses = [
                ModelResponseSchema(
                    model_call_id="initial_long",
                    task_id="task_compress",
                    run_id="run_compress",
                    model_name="stub",
                    success=True,
                    content=("技术方案说明" * 320) + "。",
                    finish_reason="stop",
                    created_at=NOW,
                ),
                ModelResponseSchema(
                    model_call_id="compressed",
                    task_id="task_compress",
                    run_id="run_compress",
                    model_name="stub",
                    success=True,
                    content="技术方案包括总体架构、核心组件、数据流和接口机制，并以项目输入及证据为依据。",
                    finish_reason="stop",
                    created_at=NOW,
                ),
            ]

        # 阅读注释（函数）：处理 call 模型 相关逻辑。
        def _call_model(self, *args, **kwargs):  # type: ignore[override]
            """处理 call 模型 相关逻辑。

            参数:
                *args: 额外位置参数。
                **kwargs: 额外关键字参数。

            返回:
                未显式标注；请结合调用方和实际返回语句理解。

            阅读提示:
                主要直接调用：self.responses.pop。
            """
            return self.responses.pop(0)

    item = ProjectInputSchema.model_validate(
        {
            "task_id": "task_compress",
            "task_type": "scheme_generation",
            "user_query": "生成技术方案",
            "generation_requirements": {
                "required_sections": ["技术方案"],
                "citation_required_sections": [],
                "min_section_chars": 10,
                "max_section_retries": 1,
                "max_tokens_per_section": 1024,
            },
            "output_schema": {"required_sections": ["技术方案"]},
        }
    )
    section = CompressionAgent().section_generation_service._generate_section(
        SimpleNamespace(run_id="run_compress", task_id="task_compress"),
        document_id="document_compress",
        project_input=item,
        section_title="技术方案",
        section_order=1,
        rag_context=RAGContextSchema(context_text="", max_context_chars=6000),
        citations=[],
        structured_facts=[],
        previous_sections=[],
    )

    assert section.status == ExecutionStatus.SUCCESS
    assert section.extra["compression_model_call_id"] == "compressed"
    assert len(section.content) <= section.extra["max_section_chars"]


# 阅读注释（函数）：处理 测试 semantic scope issue is partial not failed 相关逻辑。
def test_semantic_scope_issue_is_partial_not_failed() -> None:
    """处理 测试 semantic scope issue is partial not failed 相关逻辑。

    返回:
        None

    阅读提示:
        主要直接调用：ProjectInputSchema.model_validate, _generate_section, _Agent, SimpleNamespace, RAGContextSchema。
    """
    from types import SimpleNamespace

    from apps.enterprise_document.agents.scheme_writer_agent import SchemeWriterAgent
    from apps.enterprise_document.schemas.scheme_writer_schema import (
        SemanticGateIssueSchema,
        SemanticGateResultSchema,
    )
    from schemas.model import ModelResponseSchema
    from schemas.rag import RAGContextSchema

    # 阅读注释（类）：封装 scope judge，集中封装相关状态、依赖和行为。
    class _ScopeJudge:
        """封装 scope judge，集中封装相关状态、依赖和行为。"""
        # 阅读注释（函数）：处理 judge 相关逻辑。
        def judge(self, **kwargs):
            """处理 judge 相关逻辑。

            参数:
                **kwargs: 额外关键字参数。

            返回:
                未显式标注；请结合调用方和实际返回语句理解。

            阅读提示:
                主要直接调用：SemanticGateResultSchema, SemanticGateIssueSchema。
            """
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

    # 阅读注释（类）：封装 Agent，负责接收状态、调用工具或服务并返回统一 Agent 结果。
    class _Agent(SchemeWriterAgent):
        """封装 Agent，负责接收状态、调用工具或服务并返回统一 Agent 结果。"""
        # 阅读注释（函数）：初始化 _Agent，保存运行所需的依赖、配置或状态。
        def __init__(self):
            """初始化 _Agent，保存运行所需的依赖、配置或状态。

            返回:
                未显式标注；请结合调用方和实际返回语句理解。

            阅读提示:
                主要直接调用：__init__, super, _ScopeJudge。
            """
            super().__init__(enable_semantic_gate=True)
            self.section_generation_service.semantic_judge = _ScopeJudge()

        # 阅读注释（函数）：处理 call 模型 相关逻辑。
        def _call_model(self, *args, **kwargs):  # type: ignore[override]
            """处理 call 模型 相关逻辑。

            参数:
                *args: 额外位置参数。
                **kwargs: 额外关键字参数。

            返回:
                未显式标注；请结合调用方和实际返回语句理解。

            阅读提示:
                主要直接调用：ModelResponseSchema。
            """
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
    section = _Agent().section_generation_service._generate_section(
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


# 阅读注释（函数）：处理 测试 semantic judge cannot hide explicit unsupported quantity 相关逻辑。
def test_semantic_judge_cannot_hide_explicit_unsupported_quantity() -> None:
    """处理 测试 semantic judge cannot hide explicit unsupported quantity 相关逻辑。

    返回:
        None

    阅读提示:
        主要直接调用：SemanticSectionJudge._merge_deterministic_candidates, len。
    """
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


# 阅读注释（函数）：处理 测试 hard gate accepts partial sections and returns warnings 相关逻辑。
def test_hard_gate_accepts_partial_sections_and_returns_warnings() -> None:
    """处理 测试 hard gate accepts partial sections and returns warnings 相关逻辑。

    返回:
        None

    阅读提示:
        主要直接调用：SchemeSectionSchema, TruncationCheckSchema, SectionEvalSchema, SchemeDraftSchema, evaluate_scheme_draft。
    """
    section = SchemeSectionSchema(
        section_id="section_partial",
        section_title="说明",
        section_order=1,
        content="正文完整，但存在轻微语义警告。",
        status=ExecutionStatus.PARTIAL_SUCCESS,
        truncation=TruncationCheckSchema(truncated=False),
        eval_result=SectionEvalSchema(
            passed=True,
            warnings=["semantic:section_scope_drift"],
        ),
    )
    draft = SchemeDraftSchema(
        draft_id="draft_partial",
        document_id="document_partial",
        task_id="task_partial",
        run_id="run_partial",
        title="报告",
        full_text=section.content,
        sections=[section],
        required_sections=["说明"],
        created_at=NOW,
    )

    result = evaluate_scheme_draft(
        draft,
        citation_required=False,
        retrieved_chunk_ids=[],
    )

    assert result.passed is True
    assert result.failures == []
    assert result.warnings
    assert result.metadata["partial_sections"] == ["说明"]


# 阅读注释（函数）：处理 测试 semantic judge downgrades scope hard failure to soft 相关逻辑。
def test_semantic_judge_downgrades_scope_hard_failure_to_soft() -> None:
    """处理 测试 semantic judge downgrades scope hard failure to soft 相关逻辑。

    返回:
        None

    阅读提示:
        主要直接调用：SemanticSectionJudge, judge._normalize_issue。
    """
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


# 阅读注释（函数）：处理 测试 semantic judge invalid JSON uses conservative fallback 相关逻辑。
def test_semantic_judge_invalid_json_uses_conservative_fallback() -> None:
    """处理 测试 semantic judge invalid JSON uses conservative fallback 相关逻辑。

    返回:
        None

    阅读提示:
        主要直接调用：ModelGateway, gateway.register_client, _InvalidJsonClient, SemanticSectionJudge, ProjectInputSchema.model_validate, judge.judge。
    """
    from contracts.base_client import BaseLLMClient
    from apps.enterprise_document.services.semantic_section_judge import (
        SemanticSectionJudge,
    )
    from model_gateway.model_gateway import ModelGateway
    from schemas.model import ModelRequestSchema, ModelResponseSchema

    # 阅读注释（类）：封装 invalid JSON 客户端，集中封装相关状态、依赖和行为。
    class _InvalidJsonClient(BaseLLMClient):
        """封装 invalid JSON 客户端，集中封装相关状态、依赖和行为。"""
        model_name = "invalid_json_model"

        # 阅读注释（函数）：生成 _InvalidJsonClient。
        def generate(self, request: ModelRequestSchema) -> ModelResponseSchema:
            """生成 _InvalidJsonClient。

            参数:
                request: 当前请求对象。

            返回:
                ModelResponseSchema

            阅读提示:
                主要直接调用：ModelResponseSchema。
            """
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


# 阅读注释（函数）：处理 测试 truncated compact retry can recover complete prefix 相关逻辑。
def test_truncated_compact_retry_can_recover_complete_prefix() -> None:
    """处理 测试 truncated compact retry can recover complete prefix 相关逻辑。

    返回:
        None

    阅读提示:
        主要直接调用：ProjectInputSchema.model_validate, _generate_section, _Agent, SimpleNamespace, RAGContextSchema。
    """
    from types import SimpleNamespace

    from apps.enterprise_document.agents.scheme_writer_agent import SchemeWriterAgent
    from schemas.model import ModelResponseSchema
    from schemas.rag import RAGContextSchema

    # 阅读注释（类）：封装 Agent，负责接收状态、调用工具或服务并返回统一 Agent 结果。
    class _Agent(SchemeWriterAgent):
        """封装 Agent，负责接收状态、调用工具或服务并返回统一 Agent 结果。"""
        # 阅读注释（函数）：初始化 _Agent，保存运行所需的依赖、配置或状态。
        def __init__(self):
            """初始化 _Agent，保存运行所需的依赖、配置或状态。

            返回:
                未显式标注；请结合调用方和实际返回语句理解。

            阅读提示:
                主要直接调用：__init__, super, ModelResponseSchema。
            """
            super().__init__()
            self.responses = [
                ModelResponseSchema(
                    model_call_id="initial_length",
                    task_id="task_safe_trim",
                    run_id="run_safe_trim",
                    model_name="stub",
                    success=True,
                    content="初稿达到模型输出上限，",
                    finish_reason="length",
                    created_at=NOW,
                ),
                ModelResponseSchema(
                    model_call_id="compact_length",
                    task_id="task_safe_trim",
                    run_id="run_safe_trim",
                    model_name="stub",
                    success=True,
                    content="第一项内容完整。第二项内容也完整。第三项仍未完成，",
                    finish_reason="length",
                    created_at=NOW,
                ),
            ]

        # 阅读注释（函数）：处理 call 模型 相关逻辑。
        def _call_model(self, *args, **kwargs):  # type: ignore[override]
            """处理 call 模型 相关逻辑。

            参数:
                *args: 额外位置参数。
                **kwargs: 额外关键字参数。

            返回:
                未显式标注；请结合调用方和实际返回语句理解。

            阅读提示:
                主要直接调用：self.responses.pop。
            """
            return self.responses.pop(0)

    item = ProjectInputSchema.model_validate(
        {
            "task_id": "task_safe_trim",
            "task_type": "scheme_generation",
            "user_query": "生成正文",
            "generation_requirements": {
                "required_sections": ["正文"],
                "citation_required_sections": [],
                "min_section_chars": 10,
                "max_section_retries": 1,
            },
            "output_schema": {"required_sections": ["正文"]},
        }
    )
    section = _Agent().section_generation_service._generate_section(
        SimpleNamespace(run_id="run_safe_trim", task_id="task_safe_trim"),
        document_id="document_safe_trim",
        project_input=item,
        section_title="正文",
        section_order=1,
        rag_context=RAGContextSchema(context_text="", max_context_chars=6000),
        citations=[],
        structured_facts=[],
        previous_sections=[],
    )

    assert section.status == ExecutionStatus.PARTIAL_SUCCESS
    assert section.truncation.truncated is False
    assert section.content == "第一项内容完整。第二项内容也完整。"
    assert section.extra["truncation_recovery_strategy"] == "complete_sentence_prefix"
    assert "truncation_recovered:complete_sentence_prefix" in section.eval_result.warnings


# 阅读注释（函数）：处理 测试 semantic hard issue is advisory only in stage1 相关逻辑。
def test_semantic_hard_issue_is_advisory_only_in_stage1() -> None:
    """处理 测试 semantic hard issue is advisory only in stage1 相关逻辑。

    返回:
        None

    阅读提示:
        主要直接调用：ProjectInputSchema.model_validate, _generate_section, _Agent, SimpleNamespace, RAGContextSchema。
    """
    from types import SimpleNamespace

    from apps.enterprise_document.agents.scheme_writer_agent import SchemeWriterAgent
    from apps.enterprise_document.schemas.scheme_writer_schema import (
        SemanticGateIssueSchema,
        SemanticGateResultSchema,
    )
    from schemas.model import ModelResponseSchema
    from schemas.rag import RAGContextSchema

    # 阅读注释（类）：封装 judge，集中封装相关状态、依赖和行为。
    class _Judge:
        """封装 judge，集中封装相关状态、依赖和行为。"""
        # 阅读注释（函数）：处理 judge 相关逻辑。
        def judge(self, **kwargs):
            """处理 judge 相关逻辑。

            参数:
                **kwargs: 额外关键字参数。

            返回:
                未显式标注；请结合调用方和实际返回语句理解。

            阅读提示:
                主要直接调用：SemanticGateResultSchema, SemanticGateIssueSchema。
            """
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

    # 阅读注释（类）：封装 Agent，负责接收状态、调用工具或服务并返回统一 Agent 结果。
    class _Agent(SchemeWriterAgent):
        """封装 Agent，负责接收状态、调用工具或服务并返回统一 Agent 结果。"""
        # 阅读注释（函数）：初始化 _Agent，保存运行所需的依赖、配置或状态。
        def __init__(self):
            """初始化 _Agent，保存运行所需的依赖、配置或状态。

            返回:
                未显式标注；请结合调用方和实际返回语句理解。

            阅读提示:
                主要直接调用：__init__, super, _Judge。
            """
            super().__init__(enable_semantic_gate=True)
            self.section_generation_service.semantic_judge = _Judge()

        # 阅读注释（函数）：处理 call 模型 相关逻辑。
        def _call_model(self, *args, **kwargs):  # type: ignore[override]
            """处理 call 模型 相关逻辑。

            参数:
                *args: 额外位置参数。
                **kwargs: 额外关键字参数。

            返回:
                未显式标注；请结合调用方和实际返回语句理解。

            阅读提示:
                主要直接调用：ModelResponseSchema。
            """
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
    section = _Agent().section_generation_service._generate_section(
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


# 阅读注释（函数）：处理 测试 演示 partial success never fabricates sub Agent failure 相关逻辑。
def test_demo_partial_success_never_fabricates_sub_agent_failure() -> None:
    """处理 测试 演示 partial success never fabricates sub Agent failure 相关逻辑。

    返回:
        None

    阅读提示:
        主要直接调用：resolve, Path, importlib.util.spec_from_file_location, importlib.util.module_from_spec, spec.loader.exec_module, module._effective_runtime_error。
    """
    import importlib.util
    from pathlib import Path

    script = Path(__file__).resolve().parents[2] / "scripts" / "run_demo.py"
    spec = importlib.util.spec_from_file_location("stage1_run_demo_status_contract", script)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    summary = {
        "status": "partial_success",
        "supervisor_result": {
            "error": None,
            "result": {
                "sub_agent_results": [
                    {
                        "agent_name": "ProjectInputNormalizerAgent",
                        "status": ExecutionStatus.SUCCESS,
                        "error": None,
                    },
                    {
                        "agent_name": "SchemeWriterAgent",
                        "status": ExecutionStatus.PARTIAL_SUCCESS,
                        "error": None,
                    },
                ]
            },
        },
    }

    assert module._effective_runtime_error(summary) == {}


# 阅读注释（函数）：处理 测试 演示 failed 状态 can still recover real sub Agent 错误 相关逻辑。
def test_demo_failed_status_can_still_recover_real_sub_agent_error() -> None:
    """处理 测试 演示 failed 状态 can still recover real sub Agent 错误 相关逻辑。

    返回:
        None

    阅读提示:
        主要直接调用：resolve, Path, importlib.util.spec_from_file_location, importlib.util.module_from_spec, spec.loader.exec_module, module._effective_runtime_error。
    """
    import importlib.util
    from pathlib import Path

    script = Path(__file__).resolve().parents[2] / "scripts" / "run_demo.py"
    spec = importlib.util.spec_from_file_location("stage1_run_demo_failed_contract", script)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    summary = {
        "status": "failed",
        "supervisor_result": {
            "error": None,
            "result": {
                "sub_agent_results": [
                    {
                        "agent_name": "ProjectInputNormalizerAgent",
                        "status": ExecutionStatus.SUCCESS,
                        "error": None,
                    },
                    {
                        "agent_name": "SchemeWriterAgent",
                        "status": ExecutionStatus.FAILED,
                        "error": {
                            "error_code": "DOCUMENT_HARD_GATE_FAILED",
                            "message": "hard gate failed",
                            "failed_node": "document_hard_gate",
                        },
                    },
                ]
            },
        },
    }

    error = module._effective_runtime_error(summary)
    assert error["error_code"] == "DOCUMENT_HARD_GATE_FAILED"
    assert error["failed_node"] == "document_hard_gate"
