"""338 R1: actual Writer inputs, not a waiver for missing semantic validation."""
from __future__ import annotations

from contextlib import ExitStack
from dataclasses import dataclass, replace
import json
import os
from pathlib import Path
from typing import Any
from unittest.mock import patch

import pytest
from pydantic import ValidationError

from application.mainline_service import MainlineApplicationService
from apps.enterprise_document.schemas.project_input_schema import ProjectInputSchema
from apps.enterprise_document.services.project_input_normalization.input_contract import (
    normalize_new_project_input,
)
from apps.enterprise_document.schemas.scheme_writer.planning import (
    SectionPlanEntrySchema,
    SectionStrategyCode,
)
from apps.enterprise_document.services.agent_rag_v1.context_runtime import (
    ApplicationContextRuntime,
    ContextSourceStale,
)
from apps.enterprise_document.services.agent_rag_v1.writer_context import (
    TargetWriterContextComposer,
)
from apps.enterprise_document.services.scheme_writer.writer_invocation import (
    _restore_context_package_semantic_snapshot,
)
from core.config import get_settings
from core.hashing import canonical_sha256
from model_gateway.fake_llm_client import FakeLLMClient
from schemas.context import ContextPackageSchema, ContextSourceFamily
import test_multi_agent_v3_workflow_recovery as workflow_tests


_SECTION_TITLE = "Wave13 验证章节"
_HIDDEN_SENTINEL = "R1_NON_SEMANTIC_METADATA_MUST_NOT_REACH_WRITER"


def _project_input(task_id: str) -> ProjectInputSchema:
    raw = workflow_tests_original_input(task_id).model_dump(mode="json")
    raw["schema_version"] = "project_input_v3"
    raw.update(
        {
            "user_query": "请核对现有资料并撰写方案；计划中的双路供电不得写成已建成。",
            "business_goal": "说明已知条件与待确认事项，不作无依据的建设承诺。",
            "project_name": "R1受控输入项目",
            "policy_requirements": ["核验适用条款，不能把提出的合规要求写成已合规。"],
            "manual_boundaries": [
                {"item": "投资测算", "handled_by": "human", "description": "由专业人员确认。"}
            ],
            "missing_information": ["双路供电的验收日期待确认。"],
            "conflicting_information": ["两份材料记载的建设状态不同。"],
            "metadata": {"internal": _HIDDEN_SENTINEL},
            "extra": {"internal": _HIDDEN_SENTINEL},
        }
    )
    raw["generation_requirements"]["tone"] = "客观、克制"
    raw["generation_requirements"]["extra"] = {"internal": _HIDDEN_SENTINEL}
    raw["output_schema"]["extra"] = {"internal": _HIDDEN_SENTINEL}
    return normalize_new_project_input(raw)


# Keep the original helper while the real-factory fixture supplies a richer task.
workflow_tests_original_input = workflow_tests._w13_single_section_project_input


@dataclass(frozen=True)
class _ControlProbe:
    """Dependency probe for the pure projection; never a Gate business fixture."""

    marker: str

    def model_dump(self, **_kwargs: Any) -> dict[str, str]:
        return {"control_marker": self.marker}


def _instruction_pair(project_input: ProjectInputSchema, marker: str = "control-a"):
    section = SectionPlanEntrySchema(
        section_id="section-r1",
        section_title=_SECTION_TITLE,
        section_order=1,
        applicability="applicable",
        strategy=SectionStrategyCode.TECHNICAL_GENERATE,
        citation_required=False,
    )
    return TargetWriterContextComposer._instructions(
        project_input=project_input,
        section=section,
        gate_binding=_ControlProbe(marker + "-binding"),
        route_decision=_ControlProbe(marker + "-route"),
        decision=_ControlProbe(marker + "-admission"),
    )


@pytest.mark.parametrize(
    "field",
    (
        "user_query",
        "project_name",
        "policy_requirements",
        "manual_boundaries",
        "missing_information",
        "conflicting_information",
    ),
)
def test_r1_task_semantics_are_explicitly_projected(field: str) -> None:
    project = _project_input("task-r1-unit")
    binding, material = _instruction_pair(project)
    contract = json.loads(material.task_output_contract)
    expected = project.model_dump(mode="json")[field]
    assert contract.get("task", {}).get(field) == expected
    assert binding.task_output_contract_sha256 == canonical_sha256(material.task_output_contract)
    assert "business_goal" not in contract.get("task", {})


def test_r1_writer_instruction_identity_tracks_goal_source_boundary() -> None:
    binding, material = _instruction_pair(_project_input("task-r1-goal-boundary"))
    contract = json.loads(material.task_output_contract)
    assert "business_goal" not in contract["task"]
    assert "authorized, section-scoped FactSnapshot" in material.role_instruction
    assert binding.role_instruction_ref.endswith("/v5")
    assert binding.prompt_template_ref == (
        f"template://target-writer/section/v5/{canonical_sha256(material.prompt_template)}"
    )
    assert "/v5/" in binding.task_output_contract_ref


def test_src_rev_04_writer_instruction_separates_fact_from_evidence_citations() -> None:
    binding, material = _instruction_pair(_project_input("task-cita-instruction"))
    assert "authorized, section-scoped FactSnapshot" in material.role_instruction
    assert "user_query and the task block only as instructions" in material.role_instruction
    assert "citation_usage.allowed_aliases" in material.role_instruction
    assert "Fact, Claim, Run, or Artifact IDs" in material.role_instruction
    assert "Evidence citation" in material.role_instruction
    assert "source supports the cited statement" in material.role_instruction
    assert binding.role_instruction_ref.endswith("/v5")
    assert binding.prompt_template_ref == (
        f"template://target-writer/section/v5/{canonical_sha256(material.prompt_template)}"
    )
    assert "/v5/" in binding.task_output_contract_ref


def test_r1_language_tone_and_section_boundaries_are_not_lost() -> None:
    raw = _project_input("task-r1-scope").model_dump(mode="json")
    sections = [_SECTION_TITLE, "投资测算章节"]
    raw["generation_requirements"]["required_sections"] = sections
    raw["output_schema"]["required_sections"] = sections
    project = ProjectInputSchema.model_validate(raw)
    _, material = _instruction_pair(project)
    contract = json.loads(material.task_output_contract)
    writing = contract.get("writing_requirements", {})
    assert writing.get("language") == project.generation_requirements.language
    assert writing.get("tone") == project.generation_requirements.tone
    assert writing.get("citation_style") == project.generation_requirements.citation_style
    assert contract.get("sibling_section_titles") == ["投资测算章节"]
    assert contract["section_title"] == _SECTION_TITLE
    assert contract["output"] == "section_body_only"


def test_r1_query_changes_locked_task_and_final_model_input_hash() -> None:
    project = _project_input("task-r1-diff")
    changed = project.model_copy(update={"user_query": "只核查现状，不提出建设建议。"}, deep=True)
    first_binding, first = _instruction_pair(project)
    second_binding, second = _instruction_pair(changed)
    assert first_binding.task_output_contract_sha256 != second_binding.task_output_contract_sha256
    first_input = ApplicationContextRuntime._render_final_prompt(
        instruction_material=first, rendered_context="same exact authorized evidence"
    )
    second_input = ApplicationContextRuntime._render_final_prompt(
        instruction_material=second, rendered_context="same exact authorized evidence"
    )
    assert canonical_sha256(first_input) != canonical_sha256(second_input)


def test_r1_gate_control_changes_do_not_change_writer_instructions() -> None:
    project = _project_input("task-r1-control")
    first_binding, first = _instruction_pair(project, "control-a")
    second_binding, second = _instruction_pair(project, "control-b")
    assert first_binding == second_binding
    assert first == second
    assert first_binding.dynamic_control_bindings == ()
    assert first.dynamic_control_payloads == ()


def test_r1_task_projection_is_not_a_full_project_input_dump() -> None:
    _, material = _instruction_pair(_project_input("task-r1-no-extra"))
    payload = json.loads(material.task_output_contract)
    assert _HIDDEN_SENTINEL not in material.task_output_contract
    assert "metadata" not in payload.get("task", {})
    assert "extra" not in payload.get("task", {})
    assert "source_materials" not in payload.get("task", {})
    assert "hardware_resources" not in payload.get("task", {})
    assert "need_human_review" not in material.task_output_contract


@pytest.fixture(scope="module", params=("normal", "fallback", "target3000"))
def r1_mainline(request, tmp_path_factory):
    """Capture the provider after real normal/fallback routing, never seed decisions."""
    mode = request.param
    root = tmp_path_factory.mktemp(f"r1-writer-input-{mode}")
    task_id = f"task-r1-live-input-{mode}"
    run_id = f"run-r1-live-input-{mode}"
    project = _project_input(task_id)
    if mode == "target3000":
        raw = project.model_dump(mode="json")
        raw["generation_requirements"]["target_section_chars"] = 3000
        project = ProjectInputSchema.model_validate(raw)
    requests = []
    predispatch_packages = []
    original_generate = FakeLLMClient.generate
    original_validate = TargetWriterContextComposer.validate_pre_model_call

    def capture_provider(client, request):
        if request.extra.get("call_purpose") == "scheme_section_generation":
            requests.append(request.model_copy(deep=True))
        response = original_generate(client, request)
        if (
            mode == "fallback"
            and request.extra.get("response_contract") == "criterion_support_evaluator_v1"
        ):
            # Change only the test provider response. Real assessor/policy/router
            # must derive DENY -> exhausted recovery -> FALLBACK_WRITE themselves.
            response = response.model_copy(update={"content": json.dumps({
                "support_state": "not_supported",
                "evidence_ids": [],
                "conflicts": [],
                "reason_codes": ["r1_test_provider_support_absent"],
            })}, deep=True)
        return response

    def capture_currentness(composer, package, *, project_input):
        # A policy change must reject this real, still-current package before dispatch.
        versions = composer._active_versions(package, project_input=project_input)
        wrong_versions = {**versions, "representation_policy_version": "r1-other-version"}
        with pytest.raises(ContextSourceStale, match="POLICY_OR_CAPABILITY_BINDING_CHANGED"):
            composer._runtime().validate_pre_model_call_current(package, wrong_versions)
        current = original_validate(composer, package, project_input=project_input)
        predispatch_packages.append(current.model_copy(deep=True))
        return current

    settings = replace(
        get_settings(), data_root=root, default_model_name="fake_llm",
        supervisor_model_name="fake_llm", enable_llm_routing=False,
        trace_enabled=True, data_capture_enabled=True,
        # Explicit supported test settings: no extra research is authorized.
        # This isolates Writer input parity, not the recovery-retrieval loop.
        gate_enforcement_mode="strict", gate_recovery_rounds_limit=0,
    )
    environment = {
        "USE_REAL_RAG_TOOL": "false", "FAKE_RAG_SCENARIO": "default",
        "FAKE_LLM_SCENARIO": "default", "ENABLE_AGENT_SELF_RAG": "false",
        "ENABLE_SEMANTIC_GATE": "false",
    }
    factory = workflow_tests._W13InspectingFactory()
    with ExitStack() as stack:
        stack.enter_context(patch.dict(os.environ, environment))
        stack.enter_context(patch.object(workflow_tests, "_w13_single_section_project_input", lambda _task_id: project))
        stack.enter_context(patch.object(FakeLLMClient, "generate", capture_provider))
        stack.enter_context(patch.object(TargetWriterContextComposer, "validate_pre_model_call", capture_currentness))
        summary = MainlineApplicationService(supervisor_factory=factory).run(
            project_root=Path(__file__).resolve().parents[2],
            user_input=project.user_query, run_id=run_id, task_id=task_id,
            output_root=root, clean_existing=True, settings=settings,
            project_input=project.model_dump(mode="json"), allow_demo_defaults=False,
            enable_agent_self_rag=False,
        )
    assert len(requests) == 1, summary
    assert predispatch_packages
    engine = factory.engines[0]
    section_payload = workflow_tests._latest_w13_section_payload(engine)
    top = workflow_tests._latest_w13_top_level_payload(engine)
    services = engine.section_execution_graph.coordinator.require_pre_write_services()
    result = section_payload.result
    assert result is not None
    invocation = services.writer_invocation.execution_store.resolve_writer_generation_invocation(
        result.writer_generation_invocation_ref
    )
    package = _restore_context_package_semantic_snapshot(
        invocation.unified_writer_input_snapshot["target_context_package"]
    )
    return project, requests[0], package, services, result, invocation, summary, top


def test_r1_actual_provider_receives_locked_task(r1_mainline) -> None:
    project, request, _, _, _, _, _, _ = r1_mainline
    text = "\n".join(item["content"] for item in request.messages)
    for value in (
        project.user_query, project.project_name,
        project.policy_requirements[0], project.manual_boundaries[0].item,
        project.missing_information[0], project.conflicting_information[0],
        project.generation_requirements.tone,
    ):
        assert value in text
    assert _HIDDEN_SENTINEL not in text


def test_src_rev_02_actual_locked_messages_use_only_requested_soft_target(r1_mainline) -> None:
    project, request, package, _, _, _, _, _ = r1_mainline
    text = "\n".join(item["content"] for item in request.messages)
    assert package.model_input.final_input_material.inline_final_input_payload == {
        "input_kind": "MESSAGES", "messages": request.messages,
    }
    contract = json.loads(request.messages[3]["content"])
    if project.task_id.endswith("target3000"):
        assert contract["target_section_chars"] == 3000
        assert '"target_section_chars":3000' in text
    else:
        assert "target_section_chars" not in contract
        assert '"target_section_chars":' not in text
    assert "do not mechanically repeat every global gap" in text
    assert "citation_usage.allowed_aliases" in text
    assert "authorized, section-scoped FactSnapshot" in text


def test_r1_actual_provider_is_control_free_and_byte_bound(r1_mainline) -> None:
    _, request, package, _, _, _, _, _ = r1_mainline
    text = "\n".join(item["content"] for item in request.messages)
    for forbidden in (
        '"writer_gate_binding"', '"writer_admission_decision"',
        '"pre_write_route_decision"', '"writer_admission_decision_ref"',
        '"route_decision_ref"', '"selected_route"', '"generation_class"',
        "CONTROL_REF=", "NORMAL_WRITE", "FALLBACK_WRITE",
    ):
        assert forbidden not in text
    expected = package.model_input.final_input_material.inline_final_input_payload
    assert expected == {"input_kind": "MESSAGES", "messages": request.messages}
    assert canonical_sha256(expected) == package.model_input.final_model_input_sha256
    assert package.policy_binding.representation_policy_version == "v4"
    assert package.instruction_binding.dynamic_control_bindings == ()


def test_r1_controls_remain_durable_and_quality_is_not_upgraded(r1_mainline) -> None:
    _, _, _, services, result, invocation, summary, top = r1_mainline
    stored_result = services.writer_invocation.execution_store.resolve_writer_execution(
        result.writer_execution_ref
    )
    route = services.route_decision_store.resolve_exact(invocation.route_decision_ref)
    assert stored_result is not None
    assert route.route_decision_id == invocation.route_decision_ref.route_decision_id
    assert route.writer_subject_ref == invocation.writer_subject_ref
    assert route.write_instance_id == invocation.write_instance_id
    assert route.decision_ref is not None
    expected = "fallback" if invocation.writer_subject_ref.run_id.endswith("fallback") else "normal"
    assert route.selected_route.value == expected
    assert invocation.generation_class.value == f"{expected}_write"
    assert stored_result.generation_provenance.generation_class == f"{expected}_write"
    assert summary["status"] == "partial_success"
    publication = top.target_release_publication
    assert publication is not None
    assert publication.manifest.quality_state_passthrough.value == "UNRESOLVED"
    assert publication.manifest.release_mode.value == "BEST_EFFORT"


def test_r1_evidence_semantics_survive_control_removal(r1_mainline) -> None:
    _, _, package, _, _, invocation, _, _ = r1_mainline
    families = {
        item.candidate_id: item.source_binding.source_family
        for item in package.candidate_decisions
    }
    evidence = [
        item for item in package.selected_materializations
        if families[item.candidate_id] is ContextSourceFamily.EVIDENCE_PACKAGE
    ]
    assert len(evidence) == 1
    payload = evidence[0].materialized_payload
    projected = (
        payload["evidence_bundle"]["assessment"]
        if "evidence_bundle" in payload else payload["assessment"]
    )
    locked_evidence = invocation.canonical_evidence_input.model_dump(mode="json")
    # V4 retains needed typed state and exact canonical references. Public
    # conflicts may contain raw values, so a full public dump is unsafe.
    canonical_assessment = locked_evidence["assessment"]
    for key in (
        "assessment_state", "status", "evidence_available",
        "selected_evidence_count", "dropped_evidence_count", "citation_count",
        "coverage_ratio", "supported_claim_types", "forbidden_claim_types",
        "authoritative_evidence_present", "historical_or_reference_evidence_present",
        "prompt_evidence_contract_missing",
    ):
        assert projected[key] == canonical_assessment[key]
    assert projected["assessment_ref"] == {
        "bundle_json_pointer": "/assessment",
        "sha256": canonical_sha256(canonical_assessment),
    }
    assert len(projected["conflicts"]) == len(canonical_assessment["conflicts"])
    for index, (safe, raw) in enumerate(zip(projected["conflicts"], canonical_assessment["conflicts"])):
        assert not {"topic", "description", "candidate_values"}.intersection(safe)
        assert "evidence_ids" not in safe  # V2.2: exact conflict_ref owns raw participants.
        assert safe["claim_type"] == raw["claim_type"]
        assert safe["conflict_ref"] == {
            "bundle_json_pointer": f"/assessment/conflicts/{index}",
            "sha256": canonical_sha256(raw),
        }
    assert set(projected["required_topics"]).issubset(canonical_assessment["required_topics"])
    assert set(projected["covered_topics"]).issubset(canonical_assessment["covered_topics"])
    assert set(projected["missing_topics"]).issubset(canonical_assessment["missing_topics"])
    assert "details" in locked_evidence["assessment"]
    assert "details" not in projected
    assert evidence[0].projection_schema_id == "writer_evidence_package_compact_v5"
    assert evidence[0].projection_schema_version == "v5"
    # R1 does not pretend to have fixed the separately scoped Fact mapping.
    facts = [
        item for item in package.selected_materializations
        if families[item.candidate_id] is ContextSourceFamily.FACT_SNAPSHOT
    ]
    assert len(facts) == 1
    assert facts[0].materialized_payload["fact_requirements"] == []
    assert facts[0].materialized_payload["facts"] == []


def test_src_rev_04_actual_model_request_has_bound_citation_usage(r1_mainline) -> None:
    _, request, package, _, _, _, _, _ = r1_mainline
    families = {
        item.candidate_id: item.source_binding.source_family
        for item in package.candidate_decisions
    }
    evidence = [
        item for item in package.selected_materializations
        if families[item.candidate_id] is ContextSourceFamily.EVIDENCE_PACKAGE
    ]
    assert len(evidence) == 1
    payload = evidence[0].materialized_payload
    projected = payload.get("evidence_bundle", payload)
    usage = projected["citation_usage"]
    assert usage["scope"] == "current_section"
    assert usage["allowed_aliases"]
    assert request.extra["available_citation_ids"] == usage["allowed_aliases"]
    message_text = "\n".join(item["content"] for item in request.messages)
    assert "citation_usage.allowed_aliases" in message_text
    assert all(line in message_text for line in projected["context_text"].splitlines())
    for alias in usage["allowed_aliases"]:
        assert any(item["citation_id"] == alias for item in projected["citations"])
        assert any(alias in item["citation_ids"] for item in projected["sources"])
        assert alias in message_text
    assert request.messages == package.model_input.final_input_material.inline_final_input_payload["messages"]


def test_r1_tampered_final_input_still_fails_package_integrity(r1_mainline) -> None:
    _, _, package, _, _, _, _, _ = r1_mainline
    raw = package.model_dump(mode="json")
    raw["model_input"]["final_input_material"]["inline_final_input_payload"]["messages"][-1]["content"] += " tampered"
    with pytest.raises(ValidationError):
        ContextPackageSchema.model_validate(raw)


def test_src_rev_04_tampered_citation_usage_fails_locked_context_integrity(r1_mainline) -> None:
    _, _, package, _, _, _, _, _ = r1_mainline
    raw = package.model_dump(mode="json")
    evidence = next(
        item for item in raw["selected_materializations"]
        if item["candidate_id"] == "writer-current-evidence-package"
    )
    payload = evidence["materialized_payload"]
    projected = payload.get("evidence_bundle", payload)
    projected["citation_usage"]["allowed_aliases"].append("C999")
    with pytest.raises(ValidationError, match="materialized payload hash mismatch"):
        ContextPackageSchema.model_validate(raw)
