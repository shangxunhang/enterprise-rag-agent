# =============================================================================
# 中文阅读说明：自动化测试模块，用于验证主链、边界条件和回归行为。
# 主要定义：_StaticGenerator、test_profiles_require_explicit_repair_strategy、test_pipeline_schema_rejects_missing_or_disabled_repair_strategy、test_v4_profile_is_rejected_after_repair_migration、test_registry_builds_repair_plugins、test_noop_repair_is_explicit_pass_through、test_local_rewrite_uses_configured_generator、_ContextPacker、_PromptBuilder、_AnswerGenerator等。建议先从公开入口函数开始，再沿调用关系向下阅读。
# =============================================================================
from __future__ import annotations

from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace

import pytest
from pydantic import ValidationError

from apps.enterprise_document.agents.scheme_writer_agent import SchemeWriterAgent
from apps.enterprise_document.config.grounded_generation import (
    GroundedGenerationPolicyConfig,
    GroundedGenerationPolicyLoader,
)
from apps.enterprise_document.quality.plugins import (
    LocalRewriteRepairStrategyPlugin,
    NoOpRepairStrategyPlugin,
)
from apps.enterprise_document.quality.ports import RepairOutput
from apps.enterprise_document.quality.registry import (
    build_generation_plugin_registry,
)
from apps.enterprise_document.schemas.project_input_schema import ProjectInputSchema
from schemas.citation import CitationSchema
from schemas.model import ModelResponseSchema
from schemas.rag import RAGContextSchema
from schemas.status import ExecutionStatus

PROJECT_ROOT = Path(__file__).resolve().parents[2]
NOW = "2026-07-16T00:00:00+00:00"


# 阅读注释（类）：封装 static generator，集中封装相关状态、依赖和行为。
class _StaticGenerator:
    """封装 static generator，集中封装相关状态、依赖和行为。"""
    # 阅读注释（函数）：初始化 _StaticGenerator，保存运行所需的依赖、配置或状态。
    def __init__(self, text: str) -> None:
        """初始化 _StaticGenerator，保存运行所需的依赖、配置或状态。

        参数:
            text: 待处理文本。

        返回:
            None
        """
        self.text = text
        self.calls: list[dict] = []

    # 阅读注释（函数）：生成 _StaticGenerator。
    def generate(self, prompt: str, **kwargs):
        """生成 _StaticGenerator。

        参数:
            prompt: 提示词，具体约束请结合类型标注和调用方确认。
            **kwargs: 额外关键字参数。

        返回:
            未显式标注；请结合调用方和实际返回语句理解。

        阅读提示:
            主要直接调用：self.calls.append。
        """
        self.calls.append({"prompt": prompt, "kwargs": kwargs})
        return self.text


# 阅读注释（函数）：处理 测试 profiles require explicit 修复 strategy 相关逻辑。
def test_profiles_require_explicit_repair_strategy() -> None:
    """处理 测试 profiles require explicit 修复 strategy 相关逻辑。

    返回:
        None

    阅读提示:
        主要直接调用：load, PipelineConfigLoader。
    """
    policy = GroundedGenerationPolicyLoader().load(
        PROJECT_ROOT
        / "backend/apps/enterprise_document/config/grounded_generation_v1.yaml"
    )

    assert policy.schema_version == "grounded_generation_policy_v1"
    assert policy.generation_checker.name == "self_rag_lite"
    assert policy.repair_strategy.name == "local_rewrite"


# 阅读注释（函数）：处理 测试 pipeline Schema rejects missing or disabled 修复 strategy 相关逻辑。
def test_pipeline_schema_rejects_missing_or_disabled_repair_strategy() -> None:
    """处理 测试 pipeline Schema rejects missing or disabled 修复 strategy 相关逻辑。

    返回:
        None

    阅读提示:
        主要直接调用：model_dump, load, PipelineConfigLoader, deepcopy, missing.pop, pytest.raises, OnlineRAGPipelineConfig.model_validate。
    """
    payload = GroundedGenerationPolicyLoader().load(
        PROJECT_ROOT
        / "backend/apps/enterprise_document/config/grounded_generation_v1.yaml"
    ).model_dump(mode="json")

    missing = deepcopy(payload)
    missing.pop("repair_strategy")
    with pytest.raises(ValidationError):
        GroundedGenerationPolicyConfig.model_validate(missing)

    disabled = deepcopy(payload)
    disabled["repair_strategy"]["enabled"] = False
    with pytest.raises(ValidationError, match="requires enabled repair_strategy"):
        GroundedGenerationPolicyConfig.model_validate(disabled)


# 阅读注释（函数）：处理 测试 v4 策略配置 is rejected after 修复 migration 相关逻辑。
def test_old_generation_policy_schema_is_rejected() -> None:
    """处理 测试 v4 策略配置 is rejected after 修复 migration 相关逻辑。

    返回:
        None

    阅读提示:
        主要直接调用：model_dump, load, PipelineConfigLoader, pytest.raises, OnlineRAGPipelineConfig.model_validate。
    """
    payload = GroundedGenerationPolicyLoader().load(
        PROJECT_ROOT
        / "backend/apps/enterprise_document/config/grounded_generation_v1.yaml"
    ).model_dump(mode="json")
    payload["schema_version"] = "online_rag_pipeline_config_v4"

    with pytest.raises(ValidationError):
        GroundedGenerationPolicyConfig.model_validate(payload)


# 阅读注释（函数）：处理 测试 注册表 builds 修复 plugins 相关逻辑。
def test_registry_builds_repair_plugins() -> None:
    """处理 测试 注册表 builds 修复 plugins 相关逻辑。

    返回:
        None

    阅读提示:
        主要直接调用：build_default_component_registry, load, PipelineConfigLoader, registry.build, isinstance。
    """
    registry = build_generation_plugin_registry()
    policy = GroundedGenerationPolicyLoader().load(
        PROJECT_ROOT
        / "backend/apps/enterprise_document/config/grounded_generation_v1.yaml"
    )

    plugin = registry.build(
        category="repair_strategy",
        config=policy.repair_strategy,
        build_context={
            "enable_quality_llm": False,
            "quality_llm_generator": None,
        },
    )

    assert isinstance(plugin, LocalRewriteRepairStrategyPlugin)
    assert plugin.plugin_metadata.name == "local_rewrite"


# 阅读注释（函数）：处理 测试 noop 修复 is explicit pass through 相关逻辑。
def test_noop_repair_is_explicit_pass_through() -> None:
    """处理 测试 noop 修复 is explicit pass through 相关逻辑。

    返回:
        None

    阅读提示:
        主要直接调用：NoOpRepairStrategyPlugin, plugin.repair。
    """
    plugin = NoOpRepairStrategyPlugin()
    output = plugin.repair(
        query="q",
        answer="original",
        context="ctx",
        citations=[],
        citation_bindings=[],
        check_result={"need_rewrite": True},
    )

    assert output.answer == "original"
    assert output.repaired is False
    assert output.report == {"enabled": False, "action": "noop"}


# 阅读注释（函数）：处理 测试 本地 改写 uses configured generator 相关逻辑。
def test_local_rewrite_uses_configured_generator() -> None:
    """处理 测试 本地 改写 uses configured generator 相关逻辑。

    返回:
        None

    阅读提示:
        主要直接调用：_StaticGenerator, LocalRewriteRepairStrategyPlugin, plugin.repair, len。
    """
    generator = _StaticGenerator("修订后的章节正文。")
    plugin = LocalRewriteRepairStrategyPlugin(
        build_context={
            "enable_quality_llm": True,
            "quality_llm_generator": generator,
        },
        use_llm=True,
    )

    output = plugin.repair(
        query="生成安全设计章节",
        answer="原章节。",
        context="证据说明系统采用JWT认证。",
        citations=[{"citation_id": "C1"}],
        citation_bindings=[{"citation_id": "C1"}],
        check_result={
            "need_rewrite": True,
            "need_retrieve_more": False,
            "problems": ["存在无依据表述"],
            "unsupported_claims": [],
        },
    )

    assert output.repaired is True
    assert output.answer == "修订后的章节正文。"
    assert output.report["action"] == "local_rewrite"
    assert len(generator.calls) == 1


# 阅读注释（类）：封装 上下文 packer，集中封装相关状态、依赖和行为。
class _ContextPacker:
    """封装 上下文 packer，集中封装相关状态、依赖和行为。"""
    # 阅读注释（函数）：压缩并组装 _ContextPacker。
    def pack(self, results):
        """压缩并组装 _ContextPacker。

        参数:
            results: 待处理的结果集合。

        返回:
            未显式标注；请结合调用方和实际返回语句理解。

        阅读提示:
            主要直接调用：SimpleNamespace。
        """
        del results
        return SimpleNamespace(
            context="证据上下文",
            citations=[{"citation_id": "C1"}],
            to_dict=lambda: {},
        )


# 阅读注释（类）：封装 提示词 builder，集中封装相关状态、依赖和行为。
class _PromptBuilder:
    """封装 提示词 builder，集中封装相关状态、依赖和行为。"""
    # 阅读注释（函数）：构建 _PromptBuilder。
    def build(self, *, query, packed_context, citations):
        """构建 _PromptBuilder。

        参数:
            query: 当前检索或生成查询。
            packed_context: packed 上下文，具体约束请结合类型标注和调用方确认。
            citations: 引用信息集合。

        返回:
            未显式标注；请结合调用方和实际返回语句理解。

        阅读提示:
            主要直接调用：SimpleNamespace。
        """
        del query, packed_context, citations
        return SimpleNamespace(
            prompt="prompt",
            prompt_id="p",
            prompt_version="v1",
            to_dict=lambda: {},
        )


# 阅读注释（类）：封装 answer generator，集中封装相关状态、依赖和行为。
class _AnswerGenerator:
    """封装 answer generator，集中封装相关状态、依赖和行为。"""
    model_name = "fake"

    # 阅读注释（函数）：生成 _AnswerGenerator。
    def generate(self, prompt, **kwargs):
        """生成 _AnswerGenerator。

        参数:
            prompt: 提示词，具体约束请结合类型标注和调用方确认。
            **kwargs: 额外关键字参数。

        返回:
            未显式标注；请结合调用方和实际返回语句理解。
        """
        del prompt, kwargs
        return "原始答案"


# 阅读注释（类）：封装 改写 检查器，集中封装相关状态、依赖和行为。
class _RewriteChecker:
    """封装 改写 检查器，集中封装相关状态、依赖和行为。"""
    # 阅读注释（函数）：检查 _RewriteChecker。
    def check(self, **kwargs):
        """检查 _RewriteChecker。

        参数:
            **kwargs: 额外关键字参数。

        返回:
            未显式标注；请结合调用方和实际返回语句理解。
        """
        del kwargs
        return {
            "enabled": True,
            "is_supported": False,
            "need_rewrite": True,
            "need_retrieve_more": False,
            "score": 0.2,
        }

    # 阅读注释（函数）：处理 execution 元数据 相关逻辑。
    def execution_metadata(self):
        """处理 execution 元数据 相关逻辑。

        返回:
            未显式标注；请结合调用方和实际返回语句理解。
        """
        return {"enabled": True, "mode": "test"}


# 阅读注释（类）：封装 pipeline 修复，集中封装相关状态、依赖和行为。
class _PipelineRepair:
    """封装 pipeline 修复，集中封装相关状态、依赖和行为。"""
    # 阅读注释（函数）：修复 _PipelineRepair。
    def repair(self, **kwargs):
        """修复 _PipelineRepair。

        参数:
            **kwargs: 额外关键字参数。

        返回:
            未显式标注；请结合调用方和实际返回语句理解。

        阅读提示:
            主要直接调用：RepairOutput。
        """
        del kwargs
        return RepairOutput(
            answer="修复后的答案",
            repaired=True,
            report={"enabled": True, "action": "test_rewrite"},
        )

    # 阅读注释（函数）：处理 execution 元数据 相关逻辑。
    def execution_metadata(self):
        """处理 execution 元数据 相关逻辑。

        返回:
            未显式标注；请结合调用方和实际返回语句理解。
        """
        return {"enabled": True, "mode": "test_rewrite"}


# 阅读注释（函数）：处理 测试 standalone 生成 pipeline applies configured 修复 相关逻辑。
def legacy_standalone_generation_pipeline_applies_configured_repair() -> None:
    """处理 测试 standalone 生成 pipeline applies configured 修复 相关逻辑。

    返回:
        None

    阅读提示:
        主要直接调用：ParentChildGenerationPipeline, _ContextPacker, _PromptBuilder, _AnswerGenerator, _RewriteChecker, _PipelineRepair, pipeline.run。
    """
    pipeline = ParentChildGenerationPipeline(
        context_packer=_ContextPacker(),
        prompt_builder=_PromptBuilder(),
        llm_generator=_AnswerGenerator(),
        generation_checker=_RewriteChecker(),
        repair_strategy=_PipelineRepair(),
    )

    output = pipeline.run(
        "query",
        [],
        generate_answer=True,
        generation_params={},
    )

    assert output.answer == "修复后的答案"
    assert output.repair["repaired"] is True
    assert output.repair_strategy_metadata["mode"] == "test_rewrite"


# 阅读注释（类）：封装 章节 检查器，集中封装相关状态、依赖和行为。
class _SectionChecker:
    """封装 章节 检查器，集中封装相关状态、依赖和行为。"""
    # 阅读注释（函数）：初始化 _SectionChecker，保存运行所需的依赖、配置或状态。
    def __init__(self) -> None:
        """初始化 _SectionChecker，保存运行所需的依赖、配置或状态。

        返回:
            None
        """
        self.calls: list[dict] = []

    # 阅读注释（函数）：检查 _SectionChecker。
    def check(self, **kwargs):
        """检查 _SectionChecker。

        参数:
            **kwargs: 额外关键字参数。

        返回:
            未显式标注；请结合调用方和实际返回语句理解。

        阅读提示:
            主要直接调用：self.calls.append, len。
        """
        self.calls.append(kwargs)
        if len(self.calls) == 1:
            return {
                "enabled": True,
                "is_supported": False,
                "need_rewrite": True,
                "need_retrieve_more": False,
                "score": 0.3,
                "problems": ["需要局部修订"],
                "unsupported_claims": [],
            }
        return {
            "enabled": True,
            "is_supported": True,
            "need_rewrite": False,
            "need_retrieve_more": False,
            "score": 0.9,
            "problems": [],
            "unsupported_claims": [],
        }

    # 阅读注释（函数）：处理 execution 元数据 相关逻辑。
    def execution_metadata(self):
        """处理 execution 元数据 相关逻辑。

        返回:
            未显式标注；请结合调用方和实际返回语句理解。
        """
        return {"enabled": True, "mode": "section_test"}


# 阅读注释（类）：封装 章节 修复，集中封装相关状态、依赖和行为。
class _SectionRepair:
    """封装 章节 修复，集中封装相关状态、依赖和行为。"""
    # 阅读注释（函数）：初始化 _SectionRepair，保存运行所需的依赖、配置或状态。
    def __init__(self) -> None:
        """初始化 _SectionRepair，保存运行所需的依赖、配置或状态。

        返回:
            None
        """
        self.calls: list[dict] = []

    # 阅读注释（函数）：修复 _SectionRepair。
    def repair(self, **kwargs):
        """修复 _SectionRepair。

        参数:
            **kwargs: 额外关键字参数。

        返回:
            未显式标注；请结合调用方和实际返回语句理解。

        阅读提示:
            主要直接调用：self.calls.append, RepairOutput。
        """
        self.calls.append(kwargs)
        return RepairOutput(
            answer="安全设计采用JWT认证并执行输入参数校验。",
            repaired=True,
            report={"enabled": True, "action": "local_rewrite"},
        )

    # 阅读注释（函数）：处理 execution 元数据 相关逻辑。
    def execution_metadata(self):
        """处理 execution 元数据 相关逻辑。

        返回:
            未显式标注；请结合调用方和实际返回语句理解。
        """
        return {"enabled": True, "mode": "section_rewrite"}


# 阅读注释（函数）：处理 测试 Agent final 章节 runs 检查器 after 绑定关系 and rechecks 修复 相关逻辑。
def test_agent_final_section_runs_checker_after_binding_and_rechecks_repair() -> None:
    """处理 测试 Agent final 章节 runs 检查器 after 绑定关系 and rechecks 修复 相关逻辑。

    返回:
        None

    阅读提示:
        主要直接调用：_SectionChecker, _SectionRepair, ProjectInputSchema.model_validate, CitationSchema, SimpleNamespace, _generate_section, _Agent, RAGContextSchema。
    """
    checker = _SectionChecker()
    repair = _SectionRepair()

    # 阅读注释（类）：封装 Agent，负责接收状态、调用工具或服务并返回统一 Agent 结果。
    class _Agent(SchemeWriterAgent):
        """封装 Agent，负责接收状态、调用工具或服务并返回统一 Agent 结果。"""
        # 阅读注释（函数）：初始化 _Agent，保存运行所需的依赖、配置或状态。
        def __init__(self):
            """初始化 _Agent，保存运行所需的依赖、配置或状态。

            返回:
                未显式标注；请结合调用方和实际返回语句理解。

            阅读提示:
                主要直接调用：__init__, super。
            """
            super().__init__(
                generation_checker=checker,
                repair_strategy=repair,
                generation_quality_metadata={"policy_id": "self_rag_v1"},
            )

        # 阅读注释（函数）：处理 call 模型 相关逻辑。
        def call_model(self, *args, **kwargs):  # type: ignore[override]
            """处理 call 模型 相关逻辑。

            参数:
                *args: 额外位置参数。
                **kwargs: 额外关键字参数。

            返回:
                未显式标注；请结合调用方和实际返回语句理解。

            阅读提示:
                主要直接调用：ModelResponseSchema。
            """
            del args, kwargs
            return ModelResponseSchema(
                model_call_id="initial",
                task_id="task_quality",
                run_id="run_quality",
                model_name="stub",
                success=True,
                content="安全设计采用JWT认证。",
                finish_reason="stop",
                created_at=NOW,
            )

    project = ProjectInputSchema.model_validate(
        {
            "task_id": "task_quality",
            "task_type": "scheme_generation",
            "user_query": "生成企业系统建设方案",
            "generation_requirements": {
                "required_sections": ["安全设计"],
                "citation_required_sections": ["安全设计"],
                "min_section_chars": 5,
                "max_section_retries": 0,
            },
            "output_schema": {"required_sections": ["安全设计"]},
        }
    )
    citation = CitationSchema(
        citation_id="C1",
        source_type="document",
        doc_id="doc_security",
        chunk_id="child_security",
        quote_text="安全设计采用JWT认证并执行输入参数校验。",
    )
    state = SimpleNamespace(
        task_id="task_quality",
        run_id="run_quality",
        generated_outputs={},
    )

    section = _Agent().section_generation_service.generate_section(
        state,
        document_id="document_quality",
        project_input=project,
        section_title="安全设计",
        section_order=1,
        rag_context=RAGContextSchema(
            context_text="安全设计采用JWT认证并执行输入参数校验。",
            max_context_chars=6000,
        ),
        citations=[citation],
        structured_facts=[],
        previous_sections=[],
    )

    assert section.status == ExecutionStatus.SUCCESS
    assert section.extra["repair_accepted"] is True
    assert section.extra["generation_check"]["is_supported"] is True
    assert section.extra["generation_quality_pipeline"]["policy_id"] == "self_rag_v1"
    assert len(checker.calls) == 2
    assert len(checker.calls[0]["citation_bindings"]) >= 1
    assert len(checker.calls[1]["citation_bindings"]) >= 1
    assert "输入参数校验" in section.content
    assert section.citation_ids == ["C1"]


# 阅读注释（函数）：处理 测试 Agent 质量 工厂 is used by supervisor composition 相关逻辑。
def test_agent_quality_factory_is_used_by_supervisor_composition() -> None:
    """处理 测试 Agent 质量 工厂 is used by supervisor composition 相关逻辑。

    返回:
        None

    阅读提示:
        主要直接调用：read_text。
    """
    source = (
        PROJECT_ROOT / "backend/bootstrap/supervisor_factory.py"
    ).read_text(encoding="utf-8")
    section_source = (
        PROJECT_ROOT
        / "backend/apps/enterprise_document/services/scheme_writer/section_generation_service.py"
    ).read_text(encoding="utf-8")

    assert "AgentQualityFactory" in source
    assert "generation_checker=agent_quality.generation_checker" in source
    assert "repair_strategy=agent_quality.repair_strategy" in source
    assert "[GenerationChecker] START" in section_source
    assert "[RepairStrategy] START" in section_source
    assert "candidate_bindings" in section_source
