# =============================================================================
# 中文阅读说明：自动化测试模块，用于验证主链、边界条件和回归行为。
# 主要定义：test_truncated_section_uses_compact_full_retry_without_continuation、test_overlong_section_uses_dedicated_compression_pass、test_truncated_compact_retry_can_recover_complete_prefix。建议先从公开入口函数开始，再沿调用关系向下阅读。
# =============================================================================
"""Stage-1 regression tests split by responsibility."""

from __future__ import annotations

from agent.agent_registry import AgentRegistry
from agent.base_agent import BaseAgent
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
        def call_model(self, *args, **kwargs):  # type: ignore[override]
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
    section = RetryAgent().section_generation_service.generate_section(
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
        def call_model(self, *args, **kwargs):  # type: ignore[override]
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
    section = CompressionAgent().section_generation_service.generate_section(
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
        def call_model(self, *args, **kwargs):  # type: ignore[override]
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
    section = _Agent().section_generation_service.generate_section(
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
