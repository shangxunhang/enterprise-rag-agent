"""First-chapter professional writing method through the existing Writer task contract.

No new agent, schema, authority, context family or model provider.
The old v5/v6 Writer role/template bytes remain unchanged without opt-in.
"""
import json
from pathlib import Path

import pytest

from apps.enterprise_document.schemas.project_input_schema import ProjectInputSchema
from apps.enterprise_document.schemas.scheme_writer.planning import (
    SectionPlanEntrySchema,
    SectionStrategyCode,
)
from apps.enterprise_document.services.agent_rag_v1.context_runtime import ApplicationContextRuntime
from apps.enterprise_document.services.agent_rag_v1.writer_context import (
    TargetWriterContextComposer,
    TargetWriterContextIntegrityError,
)


METHOD_FILE = (
    Path(__file__).resolve().parents[2]
    / "docs/post_r13_business_improvements_audits"
    / "CH1_WRITER_MINIMAL_FIX_20261012_V1/01_CH1_METHOD_CONFIG.json"
)


def project(methods=None, *, raw_extra=None):
    extra = dict(raw_extra if raw_extra is not None else {})
    if methods is not None:
        extra["section_writing_methods"] = methods
    return ProjectInputSchema.model_validate({
        "schema_version": "project_input_v3",
        "task_id": "task-ch1-writer-method-test",
        "task_type": "scheme_generation",
        "user_query": "编制政务云建设方案，覆盖背景和建设目标。",
        "project_name": "政务云建设项目",
        "project_type": "government_cloud",
        "business_goal": "在证据边界内生成方案并标记待确认项。",
        "hardware_resources": [],
        "missing_information": ["项目现状", "用户规模", "设备品牌型号"],
        "generation_requirements": {
            "required_sections": ["项目背景", "建设目标"],
            "citation_style": "citation_id_ascii_brackets_v2",
            "extra": extra,
        },
        "output_schema": {
            "document_title": "政务云建设方案",
            "required_sections": ["项目背景", "建设目标"],
        },
    })


def section(title="项目背景"):
    return SectionPlanEntrySchema(
        section_id="ch1-method-test",
        section_title=title,
        section_order=1 if title == "项目背景" else 2,
        applicability="applicable",
        strategy=SectionStrategyCode.TECHNICAL_GENERATE,
        citation_required=False,
    )


def instructions(p, title="项目背景"):
    return TargetWriterContextComposer._instructions(
        project_input=p, section=section(title),
        gate_binding=None, route_decision=None, decision=None,
    )


def method():
    return json.loads(METHOD_FILE.read_text(encoding="utf-8"))["section_writing_methods"]["项目背景"]


def test_ch1_method_is_loaded_only_as_task_writing_instruction():
    text = method()
    assert 200 < len(text) < 3000
    legacy_binding, legacy = instructions(project())
    new_binding, authored = instructions(project({"项目背景": text}))
    legacy_task = json.loads(legacy.task_output_contract)
    task = json.loads(authored.task_output_contract)
    assert "section_writing_method" not in legacy_task
    assert task["section_writing_method"] == text
    assert task["section_title"] == "项目背景"
    assert "section_writing_method" in authored.prompt_template
    assert "never as factual authority" in authored.prompt_template
    assert authored.system_instruction == legacy.system_instruction
    assert authored.role_instruction == legacy.role_instruction
    assert new_binding.role_instruction_version == legacy_binding.role_instruction_version == "v6"
    assert new_binding.role_instruction_sha256 == legacy_binding.role_instruction_sha256
    assert new_binding.prompt_template_sha256 != legacy_binding.prompt_template_sha256
    assert new_binding.task_output_contract_sha256 != legacy_binding.task_output_contract_sha256
    assert new_binding.prompt_template_version == legacy_binding.prompt_template_version
    assert "section_writing_method" not in json.dumps(legacy_task)
    assert "hardware_resources" not in task


def test_actual_sif10_five_message_input_contains_only_current_method():
    project_with_method = project({"项目背景": method(), "建设目标": "目标只陈述可核查的建设指标。"})
    _, ch1 = instructions(project_with_method, title="项目背景")
    _, ch2 = instructions(project_with_method, title="建设目标")
    for material, expected_title in ((ch1, "项目背景"), (ch2, "建设目标")):
        physical = ApplicationContextRuntime._render_final_prompt(
            instruction_material=material, rendered_context="EXACT_AUTHORIZED_CONTEXT"
        )
        messages = physical["messages"]
        assert len(messages) == 5
        assert [m["role"] for m in messages] == ["system"] * 4 + ["user"]
        assert messages[-1]["content"] == "EXACT_AUTHORIZED_CONTEXT"
        task = json.loads(messages[3]["content"])
        assert task["section_title"] == expected_title
        assert task["section_writing_method"] == project_with_method.generation_requirements.extra[
            "section_writing_methods"
        ][expected_title]
        other = "建设目标" if expected_title == "项目背景" else "项目背景"
        assert project_with_method.generation_requirements.extra["section_writing_methods"][other] not in messages[3]["content"]


def test_no_method_or_other_section_does_not_change_former_physical_instructions():
    base = project()
    p = project({"项目背景": method()})
    for title in ("项目背景", "建设目标"):
        _, plain = instructions(base, title=title)
        assert "section_writing_method" not in plain.task_output_contract
        assert "section_writing_method" not in plain.prompt_template
    _, plain_second = instructions(base, "建设目标")
    _, p_second = instructions(p, "建设目标")
    assert plain_second == p_second
    _, base_first = instructions(base, "项目背景")
    _, first_again = instructions(project(), "项目背景")
    assert base_first == first_again


@pytest.mark.parametrize("bad", [None, [], 123, "", " ", "x" * 3001])
def test_invalid_current_section_method_fails_closed(bad):
    with pytest.raises(TargetWriterContextIntegrityError, match="section writing method"):
        instructions(project({"项目背景": bad}))


def test_invalid_method_map_fails_closed():
    with pytest.raises(TargetWriterContextIntegrityError, match="mapping"):
        instructions(project(raw_extra={"section_writing_methods": ["not-a-map"]}))


def test_method_is_task_guidance_not_a_fact_source_or_authority_upgrade():
    _, material = instructions(project({"项目背景": method()}))
    task = json.loads(material.task_output_contract)
    assert "section_writing_method" in task
    assert "FACT" not in task and "EVIDENCE" not in task
    assert "user_query" in task["task"]
    assert "business_goal" not in task["task"]
    assert task["task"]["project_name"] == "政务云建设项目"
    assert "admissible_verdicts" not in material.task_output_contract
