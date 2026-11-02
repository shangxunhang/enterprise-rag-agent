"""SRCREV-02 initial writing messages consume only the V3 advisory target."""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from application.project_input_factory import ProjectInputFactory
from apps.enterprise_document.schemas.project_input_schema import ProjectInputSchema
from apps.enterprise_document.services.agent_rag_v1.context_runtime import (
    ApplicationContextRuntime,
)
from apps.enterprise_document.services.agent_rag_v1.writer_context import (
    TargetWriterContextComposer,
)
from apps.enterprise_document.services.scheme_writer.model_service import SectionModelService
from apps.enterprise_document.services.scheme_writer.prompt_service import (
    SectionPromptService,
    TargetPromptContextIntegrityError,
)
from apps.enterprise_document.services.scheme_writer.runtime_support import (
    SchemeWriterRuntimeSupport,
)
from context_manager import LLMContextManager, SectionGenerationContextPolicy
from core.hashing import canonical_sha256
from prompt_manager.prompt_manager import PromptManager
from schemas.model import ModelResponseSchema
from schemas.rag import RAGContextSchema

import test_gate_v2_pre_write_prompt_prep as prompt_prep


def _project(*, target: int | None, max_tokens: int = 1024) -> ProjectInputSchema:
    raw = prompt_prep._project_input().model_dump(mode="json")
    raw["schema_version"] = "project_input_v3"
    writing = raw["generation_requirements"]
    writing["max_tokens_per_section"] = max_tokens
    writing.pop("target_section_chars", None)
    if target is not None:
        writing["target_section_chars"] = target
    return ProjectInputSchema.model_validate(raw)


def _prompt_service(*, managed_template: bool = False, legacy_template: bool = False):
    manager = (
        PromptManager(prompt_root=Path(__file__).resolve().parents[2] / "prompts")
        if managed_template or legacy_template else None
    )
    return SectionPromptService(
        context_manager=LLMContextManager(),
        context_policy=SectionGenerationContextPolicy(),
        prompt_manager=manager,
        prompt_id=(
            "scheme_section_generation_v1" if legacy_template
            else "scheme_section_generation_v2"
        ),
        model_gateway=None,
    )


def _target_prompt(project: ProjectInputSchema, *, managed_template: bool = False):
    return _prompt_service(managed_template=managed_template).render_target_initial_section_prompt(
        prompt_prep._shared_state(),
        project,
        section_id=prompt_prep.SECTION_ID,
        section_title="技术方案",
        section_order=1,
        evidence_bundle=prompt_prep._completed_bundle(),
        structured_facts=[],
        previous_sections=[],
    )


def _ordinary_prompt(project: ProjectInputSchema, *, managed_template: bool = False):
    return _prompt_service(managed_template=managed_template).render_section_prompt(
        prompt_prep._shared_state(),
        project,
        prompt_prep.SECTION_ID,
        "技术方案",
        1,
        RAGContextSchema(context_text="", used_context_chars=0, context_item_count=0),
        [],
        [],
    )


@pytest.mark.parametrize("managed_template", (False, True))
def test_target_and_ordinary_initial_prompts_omit_unspecified_goal(managed_template: bool) -> None:
    project = _project(target=None)
    target = _target_prompt(project, managed_template=managed_template)
    ordinary = _ordinary_prompt(project, managed_template=managed_template)
    for result in (target.prompt_result, ordinary):
        assert "建议目标篇幅" not in result.rendered_text
        assert "正文控制在" not in result.rendered_text
        assert "target_section_chars" not in result.variables
        assert "None 个" not in result.rendered_text
    for context in (
        target.context_package.rendered_context,
        ordinary.extra["llm_context_package"]["rendered_context"],
    ):
        assert "建议目标篇幅" not in context
        assert "目标篇幅：不超过" not in context


@pytest.mark.parametrize("managed_template", (False, True))
def test_target_and_ordinary_initial_prompts_use_explicit_soft_goal(managed_template: bool) -> None:
    first = _project(target=1000)
    second = _project(target=3000)
    target_first = _target_prompt(first, managed_template=managed_template)
    target_second = _target_prompt(second, managed_template=managed_template)
    ordinary_first = _ordinary_prompt(first, managed_template=managed_template)
    ordinary_second = _ordinary_prompt(second, managed_template=managed_template)
    for result in (target_second.prompt_result, ordinary_second):
        assert "建议目标篇幅：约 3000 个章节正文字符" in result.rendered_text
        assert "正文控制在 3000" not in result.rendered_text
        assert result.variables["target_section_chars"] == 3000
    assert target_first.context_package.context_sha256 != target_second.context_package.context_sha256
    assert ordinary_first.extra["llm_context_package"]["context_sha256"] != ordinary_second.extra["llm_context_package"]["context_sha256"]
    assert second.generation_requirements.max_tokens_per_section == first.generation_requirements.max_tokens_per_section


def test_legacy_hard_template_fails_without_fabricating_a_target() -> None:
    service = _prompt_service(legacy_template=True)
    with pytest.raises(TargetPromptContextIntegrityError, match="legacy hard target"):
        service.render_section_prompt(
            prompt_prep._shared_state(), _project(target=None),
            prompt_prep.SECTION_ID, "技术方案", 1,
            RAGContextSchema(context_text="", used_context_chars=0, context_item_count=0),
            [], [],
        )


def test_writer_instruction_identity_and_final_messages_track_only_requested_goal() -> None:
    section = SimpleNamespace(
        section_id="section-src-rev-02",
        section_title="技术方案",
        section_order=1,
        citation_required=False,
    )
    results = {}
    for label, target, max_tokens in (
        ("none", None, 1024),
        ("short", 1000, 1024),
        ("long", 3000, 1024),
        ("more_tokens", 3000, 2048),
    ):
        binding, material = TargetWriterContextComposer._instructions(
            project_input=_project(target=target, max_tokens=max_tokens),
            section=section,
            gate_binding=None,
            route_decision=None,
            decision=None,
        )
        contract = json.loads(material.task_output_contract)
        final_messages = ApplicationContextRuntime._render_final_prompt(
            instruction_material=material,
            rendered_context="same authorized Section context",
        )
        results[label] = (binding, material, contract, final_messages)
        assert binding.system_instruction_version == "v1"
        assert binding.role_instruction_version == "v5"
        assert binding.prompt_template_version == "v5"
        assert "/v5/" in binding.task_output_contract_ref
        assert binding.role_instruction_sha256 == canonical_sha256(material.role_instruction)
        assert binding.prompt_template_sha256 == canonical_sha256(material.prompt_template)
        assert binding.prompt_template_ref == (
            f"template://target-writer/section/v5/{binding.prompt_template_sha256}"
        )
        assert binding.task_output_contract_sha256 == canonical_sha256(material.task_output_contract)
        assert "authorized, section-scoped FactSnapshot" in material.role_instruction
        assert "citation_usage.allowed_aliases" in material.role_instruction
        assert "do not mechanically repeat every global gap" in material.role_instruction
        assert "do not default it to the first" in material.role_instruction
        assert "business_goal" not in contract["task"]

    none_binding, none_material, none_contract, none_messages = results["none"]
    short_binding, short_material, short_contract, short_messages = results["short"]
    long_binding, long_material, long_contract, long_messages = results["long"]
    more_binding, more_material, more_contract, _ = results["more_tokens"]

    assert "target_section_chars" not in none_contract
    assert "target_section_chars" not in none_material.prompt_template
    none_final_text = json.dumps(none_messages, ensure_ascii=False)
    assert "target_section_chars" not in none_final_text
    assert "advisory length goal" not in none_final_text

    assert short_contract["target_section_chars"] == 1000
    assert long_contract["target_section_chars"] == 3000
    assert more_contract["target_section_chars"] == 3000

    assert none_material.prompt_template != short_material.prompt_template
    assert none_binding.prompt_template_ref != short_binding.prompt_template_ref
    assert none_binding.prompt_template_sha256 != short_binding.prompt_template_sha256
    assert short_material.prompt_template == long_material.prompt_template
    assert short_binding.prompt_template_ref == long_binding.prompt_template_ref
    assert short_binding.prompt_template_sha256 == long_binding.prompt_template_sha256

    assert short_binding.task_output_contract_ref != long_binding.task_output_contract_ref
    assert short_binding.task_output_contract_sha256 != long_binding.task_output_contract_sha256
    assert long_binding.task_output_contract_ref == more_binding.task_output_contract_ref
    assert long_binding.task_output_contract_sha256 == more_binding.task_output_contract_sha256
    assert long_material.prompt_template == more_material.prompt_template
    assert canonical_sha256(short_messages) != canonical_sha256(long_messages)


def test_ordinary_initial_model_request_contains_the_locked_soft_prompt() -> None:
    captured = []

    class FakeGateway:
        def generate(self, request):
            captured.append(request)
            return ModelResponseSchema(
                model_call_id=request.model_call_id,
                task_id=request.task_id,
                run_id=request.run_id,
                model_name="fake_section",
                success=True,
                content="模拟章节正文。",
                finish_reason="stop",
                created_at=request.created_at,
            )

    service = _prompt_service()
    model_service = SectionModelService(
        model_gateway=FakeGateway(),
        model_name="fake_section",
        agent_name="SchemeWriterAgent",
        context_manager=service.context_manager,
        prompt_service=service,
        runtime_support=SchemeWriterRuntimeSupport(),
    )
    for target in (None, 3000):
        project = _project(target=target)
        render = _ordinary_prompt(project)
        model_service.call_model(
            prompt_prep._shared_state(),
            prompt=render.rendered_text,
            section_id=prompt_prep.SECTION_ID,
            section_title="技术方案",
            project_input=project,
            available_citation_ids=[],
            context_package=render.extra["llm_context_package"],
            prompt_id=render.prompt_id,
            prompt_version=render.prompt_version,
        )
    assert len(captured) == 2
    assert "建议目标篇幅" not in captured[0].prompt
    assert "建议目标篇幅：约 3000 个章节正文字符" in captured[1].prompt
    assert captured[0].max_tokens == captured[1].max_tokens == 1024
    assert captured[0].extra["prompt_version"] == captured[1].extra["prompt_version"] == "v3"


@pytest.mark.parametrize("variant", ("normal", "missing"))
def test_frozen_pair_expected_data_stays_out_of_actual_section_model_request(variant: str) -> None:
    """Exercise one ordinary section without starting the eight-section workflow."""
    fixture_dir = Path(__file__).parent / "fixtures" / "src_rev_02_business_acceptance"
    raw = json.loads((fixture_dir / f"{variant}_project_input.json").read_text(encoding="utf-8"))
    expected = json.loads((fixture_dir / "expected" / f"{variant}_expected.json").read_text(encoding="utf-8"))
    project = ProjectInputFactory().build(
        task_id=raw["task_id"],
        user_input=raw["user_query"],
        raw_project_input=raw,
        allow_demo_defaults=False,
    )
    state = SimpleNamespace(
        task_id=raw["task_id"],
        run_id=f"run_{raw['task_id']}",
        generated_outputs={},
    )
    captured = []

    class FakeGateway:
        def generate(self, request):
            captured.append(request)
            return ModelResponseSchema(
                model_call_id=request.model_call_id,
                task_id=request.task_id,
                run_id=request.run_id,
                model_name="fake_section",
                success=True,
                content="模拟章节正文。",
                finish_reason="stop",
                created_at=request.created_at,
            )

    service = _prompt_service(managed_template=True)
    render = service.render_section_prompt(
        state,
        project,
        "section-sr02-compute",
        "计算资源建设方案",
        4,
        RAGContextSchema(context_text="", used_context_chars=0, context_item_count=0),
        [],
        [],
    )
    SectionModelService(
        model_gateway=FakeGateway(),
        model_name="fake_section",
        agent_name="SchemeWriterAgent",
        context_manager=service.context_manager,
        prompt_service=service,
        runtime_support=SchemeWriterRuntimeSupport(),
    ).call_model(
        state,
        prompt=render.rendered_text,
        section_id="section-sr02-compute",
        section_title="计算资源建设方案",
        project_input=project,
        available_citation_ids=[],
        context_package=render.extra["llm_context_package"],
        prompt_id=render.prompt_id,
        prompt_version=render.prompt_version,
    )
    assert len(captured) == 1
    context = json.dumps(render.extra["llm_context_package"], ensure_ascii=False, sort_keys=True)
    request = json.dumps(captured[0].model_dump(mode="json"), ensure_ascii=False, sort_keys=True)
    for value in (
        expected["source_policy"]["forbidden_model_input_marker"],
        expected["source_policy"]["forbidden_model_input_path_fragment"],
    ):
        assert value not in context
        assert value not in request
    assert "建议目标篇幅：约 3000 个章节正文字符" in captured[0].prompt
    assert captured[0].max_tokens == 1024
    if variant == "missing":
        assert "双链路政务专网接入" not in context
        assert "双链路政务专网接入" not in request
    else:
        assert "双链路政务专网接入" in context
        assert "双链路政务专网接入" in request
