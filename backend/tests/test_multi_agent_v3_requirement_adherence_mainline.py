"""346 actual Factory Requirement Adherence production/replay acceptance."""
from __future__ import annotations

from contextlib import ExitStack
from dataclasses import replace
import json
import os
from pathlib import Path
from unittest.mock import patch

import pytest

from application.mainline_service import MainlineApplicationService
from apps.enterprise_document.schemas.project_input_schema import ProjectInputSchema
from apps.enterprise_document.services.agent_rag_v1.post_write_composition import TargetPostWriteComposition
from apps.enterprise_document.services.agent_rag_v1.post_write_requirement_adherence import (
    REQUIREMENT_ADHERENCE_CONTRACT,
)
from core.config import get_settings
from model_gateway.fake_llm_client import FakeLLMClient

import test_multi_agent_v3_workflow_recovery as workflow_tests


TEXT = "本方案围绕用户授权范围说明风险、实施边界和交付要求。"


def project(task_id: str) -> ProjectInputSchema:
    return ProjectInputSchema(
        task_id=task_id,
        task_type="scheme_generation",
        user_query="生成风险控制方案，不得把暂定工期写成确定承诺。",
        business_goal="说明主要风险和应对措施。",
        policy_requirements=["不得虚构已完成建设。", "必须说明交付边界。"],
        manual_boundaries=[
            {"item": "设备数量最终核定", "handled_by": "human", "description": "正文不得声称模型已完成人工核定"},
        ],
        generation_requirements={
            "required_sections": ["Wave13 验证章节"],
            "need_citation": False,
            "citation_required_sections": [],
            "need_human_review": False,
            "min_section_chars": 80,
        },
        output_schema={
            "document_title": "风险控制方案",
            "required_sections": ["Wave13 验证章节"],
            "required_fields": ["风险", "措施"],
        },
    )


def _requirement_batch(request):
    for message in request.messages:
        text = str(message.get("content", ""))
        if not text.startswith("CONTROL_REF=requirement-batch://"):
            continue
        payload = json.loads(text.split("\n", 1)[1])
        if "requirements" in payload:
            return payload
    raise AssertionError("actual Requirement batch is absent from managed model input")


def _artifact_unit_refs(request):
    refs = []
    for message in request.messages:
        text = str(message.get("content", ""))
        start = text.find("{")
        if start < 0:
            continue
        try:
            payload, _end = json.JSONDecoder().raw_decode(text[start:])
        except (ValueError, TypeError):
            continue
        stack = [payload]
        while stack:
            value = stack.pop()
            if isinstance(value, dict):
                if isinstance(value.get("artifact_units"), list):
                    refs.extend(str(item["unit_ref"]) for item in value["artifact_units"] if isinstance(item, dict) and item.get("unit_ref"))
                stack.extend(value.values())
            elif isinstance(value, list):
                stack.extend(value)
    return tuple(sorted(set(refs)))


def _run(root: Path, observation: str):
    task_id = "task-rel346-" + observation.lower()
    exact_project = workflow_tests._w13_current_new_task_project_input(project(task_id))
    calls = []
    captures = []
    original_generate = FakeLLMClient.generate
    original_complete = TargetPostWriteComposition._complete_validation

    def provider(client, request):
        calls.append(request.model_copy(deep=True))
        response = original_generate(client, request)
        if request.extra.get("call_purpose") == "scheme_section_generation":
            return response.model_copy(update={"content": TEXT * 6}, deep=True)
        if request.extra.get("response_contract") == REQUIREMENT_ADHERENCE_CONTRACT:
            if observation == "BAD_JSON":
                return response.model_copy(update={"content": "{bad"}, deep=True)
            batch = _requirement_batch(request)
            refs = _artifact_unit_refs(request)
            content = json.dumps({"results": [
                {
                    "requirement_id": item["requirement_id"],
                    "observation": observation,
                    "response_refs": refs,
                    "reason_codes": [] if observation != "VIOLATED" else ["TEST_REQUIREMENT_VIOLATED"],
                }
                for item in batch["requirements"]
            ]}, ensure_ascii=False)
            return response.model_copy(update={"content": content}, deep=True)
        return response

    def complete(composition, **kwargs):
        result = original_complete(composition, **kwargs)
        completion = composition.validation_store.resolve_completion(result[0])
        item = next(row for row in completion.items if row.expected_validation_item_ref.startswith("relevancy:item:"))
        facts = tuple(composition.validation_store.resolve_fact(ref) for ref in item.fact_refs)
        captures.append((dict(kwargs), completion, item, facts, result[1]))
        before = len(calls)
        with patch.object(FakeLLMClient, "generate", side_effect=AssertionError("committed Relevancy replay called model")):
            assert original_complete(composition, **kwargs) == result
        assert len(calls) == before
        return result

    settings = replace(
        get_settings(),
        data_root=root,
        default_model_name="fake_llm",
        supervisor_model_name="fake_llm",
        enable_llm_routing=False,
        trace_enabled=True,
        data_capture_enabled=True,
        gate_enforcement_mode="strict",
        gate_recovery_rounds_limit=0,
        disk_soft_free_ratio=0.003,
        disk_soft_free_gib=0.03,
        disk_hard_free_ratio=0.002,
        disk_hard_free_gib=0.02,
        disk_critical_free_ratio=0.001,
        disk_critical_free_gib=0.01,
    )
    factory = workflow_tests._W13InspectingFactory()
    environment = {
        "USE_REAL_RAG_TOOL": "false",
        "FAKE_RAG_SCENARIO": "default",
        "FAKE_LLM_SCENARIO": "default",
        "ENABLE_AGENT_SELF_RAG": "false",
        "ENABLE_SEMANTIC_GATE": "false",
    }
    with ExitStack() as stack:
        stack.enter_context(patch.dict(os.environ, environment))
        stack.enter_context(patch.object(workflow_tests, "_w13_single_section_project_input", return_value=exact_project))
        stack.enter_context(patch.object(FakeLLMClient, "generate", provider))
        stack.enter_context(patch.object(TargetPostWriteComposition, "_complete_validation", complete))
        summary = MainlineApplicationService(supervisor_factory=factory).run(
            project_root=Path(__file__).resolve().parents[2],
            task_id=task_id,
            run_id="run-rel346-" + observation.lower(),
            user_input=exact_project.user_query,
            output_root=root,
            clean_existing=True,
            settings=settings,
            project_input=exact_project.model_dump(mode="json"),
            allow_demo_defaults=False,
            enable_agent_self_rag=False,
        )
    engine = factory.engines[0]
    try:
        top = workflow_tests._latest_w13_top_level_payload(engine)
    except AssertionError as exc:
        raise AssertionError({"summary": summary, "checkpoint": str(engine.checkpointer.path)}) from exc
    if top.terminal_error is not None:
        raise AssertionError(top.terminal_error.model_dump(mode="json"))
    return exact_project, engine, calls, captures, summary, top


@pytest.mark.parametrize(
    ("observation", "expected"),
    [("SATISFIED", "PASS"), ("VIOLATED", "FAIL"), ("INDETERMINATE", "INDETERMINATE")],
)
def test_actual_factory_requirement_adherence_section_document(tmp_path, observation, expected):
    project_input, engine, calls, captures, summary, top = _run(tmp_path, observation)
    assert len(captures) == 2
    assert {row[0]["scope"].value for row in captures} == {"SECTION", "DOCUMENT"}
    semantic_calls = [request for request in calls if request.extra.get("response_contract") == REQUIREMENT_ADHERENCE_CONTRACT]
    assert len(semantic_calls) == 2
    for request in semantic_calls:
        assert request.extra["llm_context_summary"]["managed"] is True
        batch = _requirement_batch(request)
        assert len(batch["requirements"]) == 4
        assert {item["requirement_kind"] for item in batch["requirements"]} == {
            "TASK_INTENT", "POLICY_REQUIREMENT", "MANUAL_BOUNDARY"
        }
        assert _artifact_unit_refs(request)
    for _kwargs, _completion, item, facts, _gate in captures:
        assert item.state.value == expected
        assert len(facts) == 4
        assert {fact.family.value for fact in facts} == {"REQUIREMENT_ADHERENCE"}
        assert {fact.semantic_state.value for fact in facts} == {expected if expected != "INDETERMINATE" else "INDETERMINATE"}
    requirement_ref = engine.stage_service.target_post_write_composition.entry_store.resolve_exact(
        captures[0][0]["entry_ref"]
    ).identity.requirement_ref
    snapshot = engine.stage_service.target_post_write_composition.requirement_store.resolve_exact(requirement_ref)
    assert snapshot.semantic_contract_version == "post-write-requirements-v2"
    semantic_requirements = tuple(
        item for item in snapshot.requirements
        if item.requirement_kind in {"TASK_INTENT", "POLICY_REQUIREMENT", "MANUAL_BOUNDARY"}
    )
    coverage_requirements = tuple(
        item for item in snapshot.requirements
        if item.requirement_kind in {
            "STRUCTURAL_PART_PRESENCE", "FIELD_OR_SLOT_PRESENCE", "CITATION_PRESENCE"
        }
    )
    procedure_requirements = tuple(
        item for item in snapshot.requirements
        if item.requirement_kind == "consistency_procedure"
    )
    assert len(semantic_requirements) == 4
    assert len(procedure_requirements) == 1
    assert (
        procedure_requirements[0].constraints["consistency_procedure"]
        ["document_global_applicability"]
        == "APPLICABLE"
    )
    assert procedure_requirements[0].constraints["consistency_procedure"][
        "applicability_reason"
    ] == "PRODUCTION_DOCUMENT_GLOBAL_CONSISTENCY_REQUIRED"
    selectors = procedure_requirements[0].constraints["consistency_procedure"][
        "document_global_selectors"
    ]
    assert [item["target_ref"] for item in selectors] == [
        "consistency-obligation://document-global/all-included-assertions/v1"
    ]
    assert len(snapshot.requirements) == (
        len(semantic_requirements)
        + len(coverage_requirements)
        + len(procedure_requirements)
    )
    assert coverage_requirements
    task = next(item for item in snapshot.requirements if item.requirement_id == "task-request")
    assert task.statement == project_input.user_query
    assert task.constraints["required_fields"] == ["风险", "措施"]
    assert task.constraints["business_goal"] == project_input.business_goal
    assert summary["status"] in {"partial_success", "success"}
    assert top.target_release_publication is not None


def test_technical_failure_receipt_is_exact_and_tampering_is_rejected(tmp_path):
    from apps.enterprise_document.services.agent_rag_v1.post_write_requirement_adherence import (
        RequirementAdherenceCompositionError,
    )

    _project, engine, _calls, captures, summary, top = _run(tmp_path, "BAD_JSON")
    assert top.terminal_error is None
    assert summary["status"] == "partial_success"
    composition = engine.stage_service.target_post_write_composition
    kwargs, completion, item, facts, _gate = captures[0]
    assert item.state.value == "TECHNICAL_FAILURE" and not facts
    owner = composition.requirement_adherence
    original_get_json = owner.store.get_json

    def tampered(ref, **identity):
        payload = original_get_json(ref, **identity)
        if str(ref).endswith("/result") and payload.get("outcome") == "failed":
            payload = dict(payload)
            failure = dict(payload["failure"])
            failure["message"] = "tampered failure reason"
            payload["failure"] = failure
        return payload

    with patch.object(owner.store, "get_json", side_effect=tampered):
        with pytest.raises(RequirementAdherenceCompositionError, match="technical Relevancy receipt differs"):
            owner.complete_item(
                validation_run_id=completion.validation_run_id,
                subject_hash=completion.subject_hash,
                artifact_ref=kwargs["artifact_ref"],
                scope=kwargs["scope"],
                target_ref=kwargs["target_ref"],
                requirement_ref=kwargs["requirement_ref"],
                source_sections=kwargs["source_sections"],
            )


def test_generic_fake_never_manufactures_requirement_pass(tmp_path):
    task_id = "task-rel346-generic"
    exact_project = workflow_tests._w13_current_new_task_project_input(workflow_tests._w13_single_section_project_input(task_id))
    settings = replace(
        get_settings(), data_root=tmp_path, default_model_name="fake_llm", supervisor_model_name="fake_llm",
        enable_llm_routing=False, trace_enabled=True, data_capture_enabled=True,
        disk_soft_free_ratio=0.003, disk_soft_free_gib=0.03,
        disk_hard_free_ratio=0.002, disk_hard_free_gib=0.02,
        disk_critical_free_ratio=0.001, disk_critical_free_gib=0.01,
    )
    factory = workflow_tests._W13InspectingFactory()
    with patch.dict(os.environ, {
        "USE_REAL_RAG_TOOL": "false", "FAKE_RAG_SCENARIO": "default", "FAKE_LLM_SCENARIO": "default",
        "ENABLE_AGENT_SELF_RAG": "false", "ENABLE_SEMANTIC_GATE": "false",
    }):
        summary = MainlineApplicationService(supervisor_factory=factory).run(
            project_root=Path(__file__).resolve().parents[2], task_id=task_id, run_id="run-rel346-generic",
            user_input=exact_project.user_query, output_root=tmp_path, clean_existing=True, settings=settings,
            project_input=exact_project.model_dump(mode="json"), allow_demo_defaults=False, enable_agent_self_rag=False,
        )
    engine = factory.engines[0]
    top = workflow_tests._latest_w13_top_level_payload(engine)
    assert top.terminal_error is None
    composition = engine.stage_service.target_post_write_composition
    controls = composition.control_store.list_for_validation_run if hasattr(composition.control_store, "list_for_validation_run") else None
    # Resolve the final completion through the exact final Control rather than a latest Requirement read.
    final_control = composition.control_store.resolve_exact(top.target_post_write_control_decision_ref)
    final_gate = composition.gate_store.resolve_exact(final_control.delivery_gate_decision_ref)
    completion = composition.validation_store.resolve_completion(
        final_gate.gate_input_manifest.validation_completion_ref
    )
    item = next(row for row in completion.items if row.expected_validation_item_ref.startswith("relevancy:item:"))
    assert item.state.value == "INDETERMINATE"
    facts = [composition.validation_store.resolve_fact(ref) for ref in item.fact_refs]
    assert facts and {fact.semantic_state.value for fact in facts} == {"INDETERMINATE"}
    assert summary["status"] == "partial_success"
