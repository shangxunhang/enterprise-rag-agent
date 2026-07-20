# =============================================================================
# 中文阅读说明：自动化测试模块，用于验证主链、边界条件和回归行为。
# 主要定义：_FailingAgent、test_truncation_detects_token_limit_and_unclosed_json、test_workflow_propagates_structured_failure_and_stops、test_hard_gate_rejects_unverified_citation_binding、test_hard_gate_accepts_partial_sections_and_returns_warnings。建议先从公开入口函数开始，再沿调用关系向下阅读。
# =============================================================================
"""Stage-1 regression tests split by responsibility."""

from __future__ import annotations

from agent.agent_registry import AgentRegistry
from agent.base_agent import BaseAgent
from agent.runtime.graph_state import GraphStateSchema
from agent.runtime.langgraph_workflow_engine import LangGraphWorkflowEngine
from agent.runtime.shared_state_schema import SharedStateSchema
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

    execution = LangGraphWorkflowEngine(registry).execute(workflow, state)
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
