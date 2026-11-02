"""Citation writing obligation: offline interfaces, never real-model acceptance.

The historical source-bound semantic oracle is recorded separately. These
portable fixtures prove instruction transport, source restrictions and unchanged
Target output; a Fake response is not evidence that a model followed a prompt.
"""
import json
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from apps.enterprise_document.schemas.scheme_writer.planning import (
    SectionPlanEntrySchema, SectionStrategyCode,
)
from apps.enterprise_document.schemas.project_input_schema import ProjectInputSchema
from apps.enterprise_document.services.agent_rag_v1.context_runtime import ApplicationContextRuntime
from apps.enterprise_document.services.agent_rag_v1.writer_context import TargetWriterContextComposer
from core.hashing import canonical_sha256
from rag.evidence.governance_v2 import OnlineEvidenceGovernanceV2

import test_gate_v2_pre_write_content_boundary as target
import test_multi_agent_v3_writer_input_boundary as native
import test_multi_agent_v3_research_runtime as research
import test_stage_d_evidence_governance_v2 as governance


def project(*, style="citation_id_ascii_brackets_v2"):
    raw = target._project_input().model_dump(mode="json")
    raw["schema_version"] = "project_input_v3"
    raw["generation_requirements"]["citation_style"] = style
    return ProjectInputSchema.model_validate(raw)


def instructions(*, required=True, style="citation_id_ascii_brackets_v2", title="来源使用说明"):
    section = SectionPlanEntrySchema(
        section_id="portable-reference-method", section_title=title, section_order=1,
        applicability="applicable", strategy=SectionStrategyCode.TECHNICAL_GENERATE,
        citation_required=required,
        evidence_requirements_source=research._requirements_ref() if required else None,
        evidence_requirement_ids=("req-1",) if required else (),
    )
    return TargetWriterContextComposer._instructions(
        project_input=project(style=style), section=section,
        gate_binding=None, route_decision=None, decision=None,
    )


@pytest.mark.parametrize("title", ["来源使用说明", "建设依据", "Methods"])
def test_required_reference_method_is_versioned_and_not_title_specific(title):
    binding, material = instructions(title=title)
    old_binding, old = instructions(required=False, title=title)
    assert binding.role_instruction_ref == "instruction://target-writer/role/v7"
    assert binding.role_instruction_sha256 == canonical_sha256(material.role_instruction)
    assert old_binding.role_instruction_version == "v6"
    assert binding.role_instruction_sha256 != old_binding.role_instruction_sha256
    assert material.role_instruction.startswith(old.role_instruction)
    assert material.prompt_template == old.prompt_template
    assert material.system_instruction == old.system_instruction
    assert "inspect the selected Evidence text" in material.role_instruction
    assert "within this Section's scope" in material.role_instruction
    assert "plan, an expected benefit or an achieved result" in material.role_instruction
    assert "do not upgrade one into another" in material.role_instruction
    assert "separately from current-project facts" in material.role_instruction
    assert "quantity, organization, existing conditions" in material.role_instruction
    assert "leave the citation obligation unresolved" in material.role_instruction
    assert "never invent a claim, source or marker" in material.role_instruction
    assert json.loads(material.task_output_contract)["citation_required"] is True


def test_legacy_style_and_optional_section_keep_existing_role_versions():
    binding, _ = instructions(style="default")
    optional, _ = instructions(required=False)
    assert binding.role_instruction_version == "v5"
    assert optional.role_instruction_version == "v6"


@pytest.mark.parametrize("text,aliases", [
    ("作为历史参考，该可研提出建设统一资源池，作为该案例的建设目标。[C1]", ("C1",)),
    ("本项目确定新建12台服务器；采购、安装、部署和投运状态尚待确认。", ("C1",)),
    ("没有相关来源支持参考陈述，引用论据仍未解决。", ()),
])
def test_single_target_call_preserves_frozen_body_and_never_inserts_citation(monkeypatch, text, aliases):
    # Synthetic fixed texts exercise the content boundary, not source entailment.
    _, material = instructions()
    p = project()
    monkeypatch.setattr(target, "_project_input", lambda: p)
    physical = ApplicationContextRuntime._render_final_prompt(
        instruction_material=material, rendered_context="SYNTHETIC_COMPONENT_CONTEXT",
    )
    result, calls, *_ = target._run_target_case(
        primary_content=text, primary_finish_reason="stop", recovery_content=None,
        advance_runtime_to_e2=False, canonical_messages=physical["messages"],
        canonical_allowed_aliases=aliases,
        canonical_context_package={"component_fixture": "not_a_native_package"},
    )
    assert len(calls) == 1
    assert result.content == text
    assert result.recovery is None
    assert calls[0]["messages"] == physical["messages"]
    assert calls[0]["available_citation_ids"] == list(aliases)
    assert [m["role"] for m in calls[0]["messages"]] == ["system"] * 4 + ["user"]
    assert calls[0]["messages"][1]["content"] == material.role_instruction


@pytest.mark.parametrize("current_claim", ["current_project_fact", "current_project_quantity", "current_project_configuration"])
def test_historical_same_12_quantity_cannot_become_current_project_authority(current_claim):
    # Even identical numbers do not transfer authority across projects.
    profile = governance._profile(
        document_id="another-project-history", document_type="historical_feasibility_study",
        evidence_role="historical_case", project_relation="other_project", authority_level="reference",
        allowed=["general_method", "reference_case", "typical_architecture"],
        forbidden=[current_claim], kb_id="historical_case_kb",
    )
    result = OnlineEvidenceGovernanceV2().evaluate(
        query="服务器资源池建设背景",
        results=[governance._result(
            child_id="another-project-child", document_id="another-project-history",
            text="该历史可研计划新增12台服务器，建设统一资源池。", profile=profile,
            fact_category="general",
        )],
        runtime_context=governance._context(topics=["资源池建设"], claims=[current_claim]),
    )
    decision = governance._gate(result)
    allowed = {item.value for item in decision.allowed_claim_types}
    assert current_claim not in allowed
    assert allowed <= {"general_method", "reference_case", "typical_architecture"}


def test_native_final_messages_reach_fake_model_with_new_role_and_exact_source_binding(tmp_path_factory):
    original_project = native._project_input
    original_final = TargetWriterContextComposer.final_messages
    dispatched_messages = []

    def capture_final(composer, package, *, project_input):
        messages = original_final(composer, package, project_input=project_input)
        dispatched_messages.append(messages)
        return messages

    def required_project(task_id):
        p = original_project(task_id)
        p.generation_requirements.citation_style = "citation_id_ascii_brackets_v2"
        p.generation_requirements.need_citation = True
        p.generation_requirements.citation_required_sections = list(p.generation_requirements.required_sections)
        return p

    # Existing native Fake fixture uses real Composer/Invocation/Artifact owners.
    # Its quality is deliberately not asserted PASS and its content is not Gold.
    with patch.object(native, "_project_input", required_project), patch.object(
        TargetWriterContextComposer, "final_messages", capture_final
    ):
        row = native.r1_mainline.__wrapped__(SimpleNamespace(param="normal"), tmp_path_factory)
    p, request, package, services, result, invocation, summary, top = row
    assert package.instruction_binding.role_instruction_version == "v7"
    composer = services.writer_invocation.builder.target_context_composer
    assert dispatched_messages == [request.messages]
    assert canonical_sha256(request.messages[1]["content"]) == package.instruction_binding.role_instruction_sha256
    assert "leave the citation obligation unresolved" in request.messages[1]["content"]
    aliases = composer.locked_citation_aliases(package)
    assert aliases
    assert invocation.canonical_evidence_input.evidence_bundle_sha256
    # Existing proof checks unique aliases, exact Evidence refs and source identity.
    native.test_src_rev_04_actual_model_request_has_bound_citation_usage(row)
    native.test_r1_actual_provider_is_control_free_and_byte_bound(row)
