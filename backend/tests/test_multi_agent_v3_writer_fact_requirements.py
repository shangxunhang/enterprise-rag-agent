"""338 R1-03: typed requirement transport and exact Fact lookup.

The live test supplies authored requirements at the Planner candidate boundary.
It does not claim that the default Planner generates these requirements yet.
"""
from __future__ import annotations

from contextlib import ExitStack
from dataclasses import replace
import json
import os
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest
from pydantic import ValidationError

from application.mainline_service import MainlineApplicationService
from apps.enterprise_document.schemas.agent_rag_v1.facts import (
    CanonicalFactRecordSchema, FactSnapshotContentSchema, FactSourceBindingSchema,
)
from apps.enterprise_document.schemas.project_input_schema import ProjectInputSchema
from apps.enterprise_document.schemas.scheme_writer.planning import (
    DocumentPlanSchema, PlannedFactRequirementSchema, SectionPlanSchema,
)
from apps.enterprise_document.services.agent_rag_v1.workflow_runtime import (
    WorkflowCutoverIntegrityError,
)
from apps.enterprise_document.services.agent_rag_v1.writer_context import (
    TargetWriterContextComposer, TargetWriterContextIntegrityError,
)
from apps.enterprise_document.services.project_input_normalization.fact_extractor import (
    StructuredFactExtractor,
)
from apps.enterprise_document.services.scheme_writer.document_planning_service import (
    DocumentPlanningService,
)
from apps.enterprise_document.services.scheme_writer.writer_invocation import (
    _restore_context_package_semantic_snapshot,
)
from bootstrap.supervisor_factory import SupervisorFactory
from core.config import get_settings
from core.hashing import canonical_sha256
from model_gateway.fake_llm_client import FakeLLMClient
from schemas.context import ContextSourceFamily
import test_multi_agent_v3_workflow_recovery as workflow_tests


_RUN = "run-w13"
_TITLE = "已知项目条件"
_SOURCE = FactSourceBindingSchema(
    source_type="project_input", source_ref="project-input://task/task-w13",
    source_version_ref="task-request://sha256/exact-test-input",
)


def _requirement(key="hardware_resources", *, importance="required"):
    definitions = {
        "hardware_resources": "hardware_resource",
        "organization_scale": "project_scale",
        "business_goal": "business_goal",
        "manual_boundaries": "manual_boundary",
        "missing_information": "missing_information",
    }
    return PlannedFactRequirementSchema(
        requirement_id="req-" + key, fact_key="project_input." + key,
        fact_type=definitions[key], importance=importance,
        purpose="只使用本章明确声明的项目输入。",
    )


def _plan_with_requirements(requirements):
    raw = workflow_tests._w13_document_plan().model_dump(mode="json")
    raw["sections"][0]["fact_requirements"] = [
        item.model_dump(mode="json") for item in requirements
    ]
    return DocumentPlanSchema.model_validate(raw)


def _fact(suffix, fact_type, value, *, run_id=_RUN, source=_SOURCE):
    return CanonicalFactRecordSchema(
        fact_id=f"fact_{run_id}_{suffix}", fact_type=fact_type,
        normalized_value=value, source_bindings=[source], confidence=1.0,
    )


def _snapshot(*facts, version="project-input-normalization-v1", source=_SOURCE):
    return FactSnapshotContentSchema(
        normalization_contract_version=version, source_bindings=[source], facts=list(facts),
    )


def _project(snapshot, requirements, *, run_id=_RUN, project_input_version="project_input_v1"):
    return TargetWriterContextComposer._relevant_fact_payload(
        snapshot, SimpleNamespace(fact_requirements=tuple(requirements)), run_id=run_id,
        project_input_version=project_input_version,
    )


def test_fact_requirement_is_typed_and_survives_compatibility_round_trip():
    required = (_requirement(), _requirement("organization_scale"))
    plan = _plan_with_requirements(reversed(required))
    actual = getattr(plan.sections[0], "fact_requirements", ())
    assert actual == tuple(sorted(required, key=lambda item: item.requirement_id))
    assert DocumentPlanSchema.model_validate_json(plan.model_dump_json()) == plan
    assert getattr(plan.sections[1], "fact_requirements", ()) == ()


@pytest.mark.parametrize("kind", ("duplicate_key", "duplicate_id", "value", "empty_key"))
def test_compatibility_plan_does_not_silently_drop_invalid_fact_requirements(kind):
    first = _requirement().model_dump(mode="json")
    second = _requirement("organization_scale").model_dump(mode="json")
    if kind == "duplicate_key":
        second["fact_key"] = first["fact_key"]
    elif kind == "duplicate_id":
        second["requirement_id"] = first["requirement_id"]
    elif kind == "value":
        first["normalized_value"] = "model-invented 999 servers"
    else:
        first["fact_key"] = " "
    raw = workflow_tests._w13_document_plan().model_dump(mode="json")
    raw["sections"][0]["fact_requirements"] = [first, second]
    with pytest.raises(ValidationError):
        DocumentPlanSchema.model_validate(raw)


def test_metadata_cannot_create_a_canonical_fact_requirement(tmp_path):
    *_, plan_runtime, runtime, _ = workflow_tests._w13_runtime(tmp_path)
    plan = workflow_tests._w13_document_plan()
    plan.sections[0].metadata["fact_requirements"] = [_requirement().model_dump(mode="json")]
    result = runtime.ensure_section_plan(
        task_id="task-w13", run_id=_RUN, document_plan=plan, structured_facts=[],
    )
    raw, _ = plan_runtime.artifact_runtime.read_version(result.plan_version_ref)
    assert raw["sections"][0]["fact_requirements"] == []


def test_missing_fact_requirement_is_committed_before_matching_fact_exists(tmp_path):
    *_, plan_runtime, runtime, _ = workflow_tests._w13_runtime(tmp_path)
    requirement = _requirement()
    result = runtime.ensure_section_plan(
        task_id="task-w13", run_id=_RUN,
        document_plan=_plan_with_requirements([requirement]), structured_facts=[],
    )
    raw, _ = plan_runtime.artifact_runtime.read_version(result.plan_version_ref)
    assert raw["sections"][0]["fact_requirements"] == [requirement.model_dump(mode="json")]
    assert raw["planning_contract_version"] == "workflow-cutover-section-plan-v2"


def test_requirement_order_replays_but_changed_scope_does_not_overwrite_plan(tmp_path):
    _, _, artifact, _, _, runtime, _ = workflow_tests._w13_runtime(tmp_path)
    requirements = [_requirement(), _requirement("organization_scale")]
    first = runtime.ensure_section_plan(
        task_id="task-w13", run_id=_RUN,
        document_plan=_plan_with_requirements(requirements), structured_facts=[],
    )
    before = artifact.ledger_path.read_bytes()
    again = runtime.ensure_section_plan(
        task_id="task-w13", run_id=_RUN,
        document_plan=_plan_with_requirements(reversed(requirements)), structured_facts=[],
    )
    assert again == first
    assert artifact.ledger_path.read_bytes() == before
    with pytest.raises(WorkflowCutoverIntegrityError, match="SectionPlan differs"):
        runtime.ensure_section_plan(
            task_id="task-w13", run_id=_RUN,
            document_plan=_plan_with_requirements(requirements[:1]), structured_facts=[],
        )
    assert artifact.ledger_path.read_bytes() == before


def test_exact_domain_key_selects_one_fact_not_all_facts_of_same_type():
    expected = _fact("hardware", "hardware_resource", "2台服务器X。")
    other = _fact("unrelated", "hardware_resource", "不属于该需求的999台设备。")
    payload = _project(_snapshot(expected, other), [_requirement()])
    assert payload["facts"] == [expected.model_dump(mode="json")]
    assert payload["unresolved_fact_requirements"] == []


@pytest.mark.parametrize("importance", ("required", "optional"))
def test_missing_key_is_not_satisfied_by_another_fact_of_same_type(importance):
    requirement = _requirement(importance=importance)
    other = _fact("unrelated", "hardware_resource", "其他设备。")
    payload = _project(_snapshot(other), [requirement])
    assert payload["facts"] == []
    assert payload["unresolved_fact_requirements"] == [requirement.model_dump(mode="json")]


def test_known_missing_hardware_marker_is_not_a_present_hardware_value():
    marker = _fact("hardware", "hardware_resource", "当前未提供硬件资源信息。")
    payload = _project(_snapshot(marker), [_requirement()])
    assert payload["facts"] == []
    assert payload["unresolved_fact_requirements"] == [_requirement().model_dump(mode="json")]


def test_wrong_run_fact_is_not_selected_by_type():
    other = _fact("hardware", "hardware_resource", "其他运行的设备。", run_id="other-run")
    payload = _project(_snapshot(other), [_requirement()])
    assert payload["facts"] == []
    assert payload["unresolved_fact_requirements"]


@pytest.mark.parametrize("kind", ("unknown_key", "raw_id", "wrong_requirement_type", "wrong_record_type", "unknown_normalization", "wrong_source"))
def test_unknown_or_incompatible_mapping_fails_as_integrity_not_business_absence(kind):
    requirement = _requirement()
    fact = _fact("hardware", "hardware_resource", "2台服务器X。")
    version = "project-input-normalization-v1"
    source = _SOURCE
    if kind == "unknown_key":
        requirement = requirement.model_copy(update={"fact_key": "project_input.unknown"})
    elif kind == "raw_id":
        requirement = requirement.model_copy(update={"fact_key": fact.fact_id})
    elif kind == "wrong_requirement_type":
        requirement = requirement.model_copy(update={"fact_type": "project_scale"})
    elif kind == "wrong_record_type":
        fact = fact.model_copy(update={"fact_type": "project_scale"})
    elif kind == "unknown_normalization":
        version = "unregistered-normalizer-v9"
    else:
        source = _SOURCE.model_copy(update={"source_type": "generated"})
        fact = fact.model_copy(update={"source_bindings": [source]})
    with pytest.raises(TargetWriterContextIntegrityError):
        _project(_snapshot(fact, version=version, source=source), [requirement])


def test_fact_normalization_must_match_the_bound_project_input_version() -> None:
    requirement = _requirement("business_goal")
    fact = _fact("business_goal", "business_goal", "经调用方明确声明的项目目标。")

    v2_snapshot = _snapshot(fact, version="project-input-normalization-v2")
    assert _project(
        v2_snapshot,
        [requirement],
        project_input_version="project_input_v2",
    )["facts"] == [fact.model_dump(mode="json")]
    with pytest.raises(TargetWriterContextIntegrityError, match="unsupported Fact normalization"):
        _project(
            v2_snapshot,
            [requirement],
            project_input_version="project_input_v1",
        )
    with pytest.raises(TargetWriterContextIntegrityError, match="normalization contract"):
        _project(
            _snapshot(fact, version="project-input-normalization-v1"),
            [requirement],
            project_input_version="project_input_v2",
        )


def test_empty_requirement_set_never_means_all_project_facts():
    fact = _fact("hardware", "hardware_resource", "2台服务器X。")
    assert _project(_snapshot(fact), []) == {
        "fact_requirements": [], "facts": [], "unresolved_fact_requirements": [],
    }


def _project_input(task_id):
    return ProjectInputSchema(
        schema_version="project_input_v3",
        task_id=task_id, task_type="scheme_generation",
        user_query="只写已确认硬件；组织规模未提供，不得补造人数。",
        business_goal="准确复述已确认条件。",
        hardware_resources=[{
            "resource_type": "server", "device_model": "服务器X",
            "server_count": 2, "cards_per_server": 8,
        }],
        generation_requirements={
            "required_sections": [_TITLE], "need_citation": False,
            "need_human_review": False,
        },
        output_schema={"document_title": "事实映射验收", "required_sections": [_TITLE]},
    )


def test_legacy_plan_without_fact_requirements_still_parses():
    section = SectionPlanSchema(
        section_id="legacy-section", section_title="Legacy", section_order=1,
    )
    assert section.fact_requirements == ()


def test_all_existing_normalizer_fields_resolve_without_changing_values():
    raw = _project_input("task-w13").model_dump(mode="json")
    raw.update(
        total_staff=120,
        manual_boundaries=[{"item": "投资测算", "handled_by": "human"}],
        missing_information=["验收日期"],
    )
    project = ProjectInputSchema.model_validate(raw)
    rows = StructuredFactExtractor().extract(
        SimpleNamespace(task_id="task-w13", run_id=_RUN), project,
        "2026-09-08T00:00:00Z",
    )
    assert [(item.fact_id, item.fact_type, item.content) for item in rows] == [
        ("fact_run-w13_business_goal", "business_goal", "准确复述已确认条件。"),
        ("fact_run-w13_organization_scale", "project_scale", "总人数120人。"),
        ("fact_run-w13_hardware", "hardware_resource", "2台服务器X，每台8张卡。"),
        ("fact_run-w13_manual_boundary", "manual_boundary", "投资测算由human处理"),
        ("fact_run-w13_missing_information", "missing_information", "当前待补充信息：验收日期"),
    ]
    snapshot = _snapshot(*[
        CanonicalFactRecordSchema(
            fact_id=item.fact_id, fact_type=item.fact_type, normalized_value=item.content,
            source_bindings=[_SOURCE], confidence=item.confidence,
        ) for item in rows
    ])
    fields = ("business_goal", "organization_scale", "hardware_resources", "manual_boundaries", "missing_information")
    for field, original in zip(fields, rows):
        payload = _project(snapshot, [_requirement(field)])
        assert [item["fact_id"] for item in payload["facts"]] == [original.fact_id]
        assert payload["facts"][0]["normalized_value"] == original.content
        assert payload["unresolved_fact_requirements"] == []


def test_old_committed_plan_is_not_silently_upgraded_or_overwritten(tmp_path):
    _, _, artifact, _, _, runtime, _ = workflow_tests._w13_runtime(tmp_path)
    with patch(
        "apps.enterprise_document.services.agent_rag_v1.workflow_runtime.PLANNING_CONTRACT_VERSION",
        "workflow-cutover-section-plan-v1",
    ):
        first = runtime.ensure_section_plan(
            task_id="task-w13", run_id=_RUN,
            document_plan=workflow_tests._w13_document_plan(), structured_facts=[],
        )
    before = artifact.ledger_path.read_bytes()
    with pytest.raises(WorkflowCutoverIntegrityError, match="SectionPlan differs"):
        runtime.ensure_section_plan(
            task_id="task-w13", run_id=_RUN,
            document_plan=workflow_tests._w13_document_plan(), structured_facts=[],
        )
    assert artifact.ledger_path.read_bytes() == before
    raw, ref = artifact.read_version(first.plan_version_ref)
    assert ref == first.plan_version_ref
    assert raw["planning_contract_version"] == "workflow-cutover-section-plan-v1"


def test_shared_fact_definition_preserves_existing_normalizer_outputs():
    project = _project_input("task-w13")
    state = SimpleNamespace(task_id="task-w13", run_id=_RUN)
    rows = StructuredFactExtractor().extract(state, project, "2026-09-08T00:00:00Z")
    assert [(item.fact_id, item.fact_type, item.content) for item in rows] == [
        ("fact_run-w13_business_goal", "business_goal", "准确复述已确认条件。"),
        ("fact_run-w13_hardware", "hardware_resource", "2台服务器X，每台8张卡。"),
    ]
    assert all(item.source_type == "project_input" and item.source_ids == ["task-w13"] for item in rows)


@pytest.fixture(scope="module", params=("normal", "fallback"))
def fact_mainline(tmp_path_factory, request):
    root = tmp_path_factory.mktemp("r103-facts-" + request.param)
    task_id = "task-r103-" + request.param
    run_id = "run-r103-" + request.param
    project = _project_input(task_id)
    calls = []
    engines = []
    build_plan = DocumentPlanningService.build_document_plan
    generate = FakeLLMClient.generate
    requirements = [_requirement(), _requirement("organization_scale")]

    def authored_candidate(**kwargs):
        # Test-authored Planner candidate, not production automatic fact planning.
        plan = build_plan(**kwargs)
        raw = plan.model_dump(mode="json")
        raw["sections"][0]["fact_requirements"] = [item.model_dump(mode="json") for item in requirements]
        return DocumentPlanSchema.model_validate(raw)

    def provider(client, model_request):
        if model_request.extra.get("call_purpose") == "scheme_section_generation":
            calls.append(model_request.model_copy(deep=True))
        response = generate(client, model_request)
        if request.param == "fallback" and model_request.extra.get("response_contract") == "criterion_support_evaluator_v1":
            payload = json.loads(response.content)
            payload.update(support_state="not_supported", evidence_ids=[], conflicts=[])
            return response.model_copy(update={"content": json.dumps(payload)}, deep=True)
        return response

    class InspectingFactory(SupervisorFactory):
        def build(self, **kwargs):
            supervisor = super().build(**kwargs)
            engines.append(supervisor.workflow_engine)
            return supervisor

    settings = replace(
        get_settings(), data_root=root, default_model_name="fake_llm",
        supervisor_model_name="fake_llm", enable_llm_routing=False,
        trace_enabled=True, data_capture_enabled=True,
        gate_enforcement_mode="strict", gate_recovery_rounds_limit=0,
    )
    environment = {
        "USE_REAL_RAG_TOOL": "false", "FAKE_RAG_SCENARIO": "default",
        "FAKE_LLM_SCENARIO": "default", "ENABLE_AGENT_SELF_RAG": "false",
        "ENABLE_SEMANTIC_GATE": "false",
    }
    with ExitStack() as stack:
        stack.enter_context(patch.dict(os.environ, environment))
        stack.enter_context(patch.object(DocumentPlanningService, "build_document_plan", staticmethod(authored_candidate)))
        stack.enter_context(patch.object(FakeLLMClient, "generate", provider))
        summary = MainlineApplicationService(supervisor_factory=InspectingFactory()).run(
            project_root=Path(__file__).resolve().parents[2], user_input=project.user_query,
            run_id=run_id, task_id=task_id, output_root=root, clean_existing=True,
            settings=settings, project_input=project.model_dump(mode="json"),
            allow_demo_defaults=False, enable_agent_self_rag=False,
        )
    assert len(calls) == 1
    engine = engines[0]
    section = workflow_tests._latest_w13_section_payload(engine)
    services = engine.section_execution_graph.coordinator.require_pre_write_services()
    invocation = services.writer_invocation.execution_store.resolve_writer_generation_invocation(
        section.result.writer_generation_invocation_ref
    )
    package = _restore_context_package_semantic_snapshot(invocation.unified_writer_input_snapshot["target_context_package"])
    return run_id, request.param, calls[0], package, invocation, summary, requirements


def test_authored_requirements_reach_real_writer_with_exact_facts_and_missing_list(fact_mainline):
    run_id, route, request, package, invocation, summary, requirements = fact_mainline
    families = {item.candidate_id: item.source_binding.source_family for item in package.candidate_decisions}
    fact_payload = next(item.materialized_payload for item in package.selected_materializations if families[item.candidate_id] is ContextSourceFamily.FACT_SNAPSHOT)
    plan_payload = next(item.materialized_payload for item in package.selected_materializations if families[item.candidate_id] is ContextSourceFamily.SECTION_PLAN)
    expected = [item.model_dump(mode="json") for item in requirements]
    assert plan_payload["section"]["fact_requirements"] == expected
    assert fact_payload["fact_requirements"] == expected
    assert [item["fact_id"] for item in fact_payload["facts"]] == [f"fact_{run_id}_hardware"]
    assert fact_payload["facts"][0]["normalized_value"] == "2台服务器X，每台8张卡。"
    assert fact_payload["unresolved_fact_requirements"] == [requirements[1].model_dump(mode="json")]
    text = "\n".join(item["content"] for item in request.messages)
    assert "2台服务器X，每台8张卡。" in text
    assert "project_input.organization_scale" in text
    assert '"writer_admission_decision"' not in text
    assert '"route_decision_ref"' not in text
    actual = {"input_kind": "MESSAGES", "messages": request.messages}
    assert actual == package.model_input.final_input_material.inline_final_input_payload
    assert canonical_sha256(actual) == package.model_input.final_model_input_sha256
    assert package.policy_binding.representation_policy_version == "v4"
    assert invocation.generation_class.value == ("normal_write" if route == "normal" else "fallback_write")
    assert summary["status"] == "partial_success", "Fact transport is not semantic-quality PASS"
