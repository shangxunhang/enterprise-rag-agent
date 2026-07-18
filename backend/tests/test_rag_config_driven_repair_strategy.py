from __future__ import annotations

from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace

import pytest
from pydantic import ValidationError

from apps.enterprise_document.agents.scheme_writer_agent import SchemeWriterAgent
from apps.enterprise_document.schemas.project_input_schema import ProjectInputSchema
from rag.application.parent_child_generation import ParentChildGenerationPipeline
from rag.config.pipeline_config import (
    OnlineRAGPipelineConfig,
    PipelineConfigLoader,
)
from rag.plugins.repair_strategies import (
    LocalRewriteRepairStrategyPlugin,
    NoOpRepairStrategyPlugin,
)
from rag.ports.quality import RepairOutput
from rag.registry.default_registrations import build_default_component_registry
from schemas.citation import CitationSchema
from schemas.model import ModelResponseSchema
from schemas.rag import RAGContextSchema
from schemas.status import ExecutionStatus

PROJECT_ROOT = Path(__file__).resolve().parents[2]
NOW = "2026-07-16T00:00:00+00:00"


class _StaticGenerator:
    def __init__(self, text: str) -> None:
        self.text = text
        self.calls: list[dict] = []

    def generate(self, prompt: str, **kwargs):
        self.calls.append({"prompt": prompt, "kwargs": kwargs})
        return self.text


def test_profiles_require_explicit_repair_strategy() -> None:
    hybrid = PipelineConfigLoader().load(
        PROJECT_ROOT / "backend/rag/profiles/hybrid_v1.yaml"
    )
    self_rag = PipelineConfigLoader().load(
        PROJECT_ROOT / "backend/rag/profiles/self_rag_v1.yaml"
    )

    assert hybrid.schema_version == "online_rag_pipeline_config_v5"
    assert hybrid.repair_strategy.name == "noop_repair"
    assert self_rag.repair_strategy.name == "local_rewrite"


def test_pipeline_schema_rejects_missing_or_disabled_repair_strategy() -> None:
    payload = PipelineConfigLoader().load(
        PROJECT_ROOT / "backend/rag/profiles/hybrid_v1.yaml"
    ).model_dump(mode="json")

    missing = deepcopy(payload)
    missing.pop("repair_strategy")
    with pytest.raises(ValidationError):
        OnlineRAGPipelineConfig.model_validate(missing)

    disabled = deepcopy(payload)
    disabled["repair_strategy"]["enabled"] = False
    with pytest.raises(ValidationError, match="requires enabled repair_strategy"):
        OnlineRAGPipelineConfig.model_validate(disabled)


def test_v4_profile_is_rejected_after_repair_migration() -> None:
    payload = PipelineConfigLoader().load(
        PROJECT_ROOT / "backend/rag/profiles/hybrid_v1.yaml"
    ).model_dump(mode="json")
    payload["schema_version"] = "online_rag_pipeline_config_v4"

    with pytest.raises(ValidationError):
        OnlineRAGPipelineConfig.model_validate(payload)


def test_registry_builds_repair_plugins() -> None:
    registry = build_default_component_registry()
    profile = PipelineConfigLoader().load(
        PROJECT_ROOT / "backend/rag/profiles/self_rag_v1.yaml"
    )

    plugin = registry.build(
        category="repair_strategy",
        config=profile.repair_strategy,
        build_context={
            "enable_quality_llm": False,
            "quality_llm_generator": None,
        },
    )

    assert isinstance(plugin, LocalRewriteRepairStrategyPlugin)
    assert plugin.plugin_metadata.name == "local_rewrite"


def test_noop_repair_is_explicit_pass_through() -> None:
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


def test_local_rewrite_uses_configured_generator() -> None:
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


class _ContextPacker:
    def pack(self, results):
        del results
        return SimpleNamespace(
            context="证据上下文",
            citations=[{"citation_id": "C1"}],
            to_dict=lambda: {},
        )


class _PromptBuilder:
    def build(self, *, query, packed_context, citations):
        del query, packed_context, citations
        return SimpleNamespace(
            prompt="prompt",
            prompt_id="p",
            prompt_version="v1",
            to_dict=lambda: {},
        )


class _AnswerGenerator:
    model_name = "fake"

    def generate(self, prompt, **kwargs):
        del prompt, kwargs
        return "原始答案"


class _RewriteChecker:
    def check(self, **kwargs):
        del kwargs
        return {
            "enabled": True,
            "is_supported": False,
            "need_rewrite": True,
            "need_retrieve_more": False,
            "score": 0.2,
        }

    def execution_metadata(self):
        return {"enabled": True, "mode": "test"}


class _PipelineRepair:
    def repair(self, **kwargs):
        del kwargs
        return RepairOutput(
            answer="修复后的答案",
            repaired=True,
            report={"enabled": True, "action": "test_rewrite"},
        )

    def execution_metadata(self):
        return {"enabled": True, "mode": "test_rewrite"}


def test_standalone_generation_pipeline_applies_configured_repair() -> None:
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


class _SectionChecker:
    def __init__(self) -> None:
        self.calls: list[dict] = []

    def check(self, **kwargs):
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

    def execution_metadata(self):
        return {"enabled": True, "mode": "section_test"}


class _SectionRepair:
    def __init__(self) -> None:
        self.calls: list[dict] = []

    def repair(self, **kwargs):
        self.calls.append(kwargs)
        return RepairOutput(
            answer="安全设计采用JWT认证并执行输入参数校验。",
            repaired=True,
            report={"enabled": True, "action": "local_rewrite"},
        )

    def execution_metadata(self):
        return {"enabled": True, "mode": "section_rewrite"}


def test_agent_final_section_runs_checker_after_binding_and_rechecks_repair() -> None:
    checker = _SectionChecker()
    repair = _SectionRepair()

    class _Agent(SchemeWriterAgent):
        def __init__(self):
            super().__init__(
                generation_checker=checker,
                repair_strategy=repair,
                generation_quality_metadata={"profile_id": "self_rag_v1"},
            )

        def _call_model(self, *args, **kwargs):  # type: ignore[override]
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

    section = _Agent()._generate_section(
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
    assert section.extra["generation_quality_pipeline"]["profile_id"] == "self_rag_v1"
    assert len(checker.calls) == 2
    assert len(checker.calls[0]["citation_bindings"]) >= 1
    assert len(checker.calls[1]["citation_bindings"]) >= 1
    assert "输入参数校验" in section.content
    assert section.citation_ids == ["C1"]


def test_agent_quality_factory_is_used_by_supervisor_composition() -> None:
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
