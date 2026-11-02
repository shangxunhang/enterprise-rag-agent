"""347 actual production Coverage / Completeness acceptance."""
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
from apps.enterprise_document.services.agent_rag_v1.post_write_composition import (
    TargetPostWriteComposition,
)
from apps.enterprise_document.services.agent_rag_v1.post_write_coverage_composition import (
    COVERAGE_SEMANTIC_CONTRACT,
    CoverageCompositionError,
)
from core.config import get_settings
from model_gateway.fake_llm_client import FakeLLMClient

import test_multi_agent_v3_citation_integrity_mainline as citation_tests
import test_multi_agent_v3_claim_materialization_mainline as extraction_tests
import test_multi_agent_v3_workflow_recovery as workflow_tests


TEXT = "本方案说明项目风险及治理措施，并保留明确的交付边界。"


def _project(
    task_id: str,
    *,
    required_fields: tuple[str, ...] = (),
    citation_required: bool = False,
) -> ProjectInputSchema:
    source = workflow_tests._w13_current_new_task_project_input(workflow_tests._w13_single_section_project_input(task_id)).model_dump(
        mode="json"
    )
    source["output_schema"]["required_fields"] = list(required_fields)
    if citation_required:
        source["generation_requirements"]["need_citation"] = True
        source["generation_requirements"]["citation_required_sections"] = list(
            source["generation_requirements"]["required_sections"]
        )
    return ProjectInputSchema.model_validate(source)


def _coverage_control(request):
    for message in request.messages:
        text = str(message.get("content", ""))
        if text.startswith("CONTROL_REF=coverage-semantic://"):
            return json.loads(text.split("\n", 1)[1])
    raise AssertionError("managed Coverage call lacks exact coverage-semantic control")


def _artifact_unit_refs(request):
    values = []
    for message in request.messages:
        text = str(message.get("content", ""))
        position = text.find("{")
        if position < 0:
            continue
        try:
            payload, _end = json.JSONDecoder().raw_decode(text[position:])
        except (ValueError, TypeError):
            continue
        stack = [payload]
        while stack:
            value = stack.pop()
            if isinstance(value, dict):
                rows = value.get("artifact_units")
                if isinstance(rows, list):
                    values.extend(
                        str(item["unit_ref"])
                        for item in rows
                        if isinstance(item, dict) and item.get("unit_ref")
                    )
                stack.extend(value.values())
            elif isinstance(value, list):
                stack.extend(value)
    return tuple(sorted(set(values)))


def _run(
    root: Path,
    *,
    semantic_observation: str | None = None,
    required_fields: tuple[str, ...] = (),
    citation_required: bool = False,
    citation_token: str = "",
):
    task_id = "task-cov347-" + (
        semantic_observation.lower() if semantic_observation else "structural"
    ) + ("-citation" if citation_required else "")
    project = _project(
        task_id,
        required_fields=required_fields,
        citation_required=citation_required,
    )
    calls = []
    captures = []
    original_generate = FakeLLMClient.generate
    original_complete = TargetPostWriteComposition._complete_validation

    def provider(client, request):
        calls.append(request.model_copy(deep=True))
        response = original_generate(client, request)
        if request.extra.get("call_purpose") == "scheme_section_generation":
            return response.model_copy(
                update={"content": (TEXT + citation_token) * 5},
                deep=True,
            )
        if request.extra.get("response_contract") == COVERAGE_SEMANTIC_CONTRACT:
            assert semantic_observation is not None
            control = _coverage_control(request)
            refs = _artifact_unit_refs(request)
            matches = refs[:1] if semantic_observation == "MATCH_OBSERVED" else ()
            return response.model_copy(
                update={
                    "content": json.dumps(
                        {
                            "results": [
                                {
                                    "requirement_id": item["requirement_id"],
                                    "observation": semantic_observation,
                                    "matched_unit_refs": list(matches),
                                    "reason_codes": [],
                                }
                                for item in control["semantic_requirements"]
                            ]
                        },
                        ensure_ascii=False,
                    )
                },
                deep=True,
            )
        return response

    def complete(composition, **kwargs):
        before = len(calls)
        outcome = original_complete(composition, **kwargs)
        completion = composition.validation_store.resolve_completion(outcome[0])
        coverage = next(
            item
            for item in completion.items
            if item.expected_validation_item_ref.startswith("coverage:item:")
        )
        facts = tuple(
            composition.validation_store.resolve_fact(ref) for ref in coverage.fact_refs
        )
        captures.append((dict(kwargs), coverage, facts, outcome))
        first_added = [
            req
            for req in calls[before:]
            if req.extra.get("response_contract") == COVERAGE_SEMANTIC_CONTRACT
        ]
        expected_calls = 1 if required_fields and kwargs["scope"].value == "DOCUMENT" else 0
        assert len(first_added) == expected_calls
        before_replay = len(calls)
        with patch.object(
            FakeLLMClient,
            "generate",
            side_effect=AssertionError("committed Coverage replay called model"),
        ):
            assert original_complete(composition, **kwargs) == outcome
        assert len(calls) == before_replay
        return outcome

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
    )
    factory = workflow_tests._W13InspectingFactory()
    with ExitStack() as stack:
        stack.enter_context(
            patch.dict(
                os.environ,
                {
                    "USE_REAL_RAG_TOOL": "false",
                    "FAKE_RAG_SCENARIO": "default",
                    "FAKE_LLM_SCENARIO": "default",
                    "ENABLE_AGENT_SELF_RAG": "false",
                    "ENABLE_SEMANTIC_GATE": "false",
                },
            )
        )
        stack.enter_context(
            patch.object(
                workflow_tests,
                "_w13_single_section_project_input",
                return_value=project,
            )
        )
        stack.enter_context(patch.object(FakeLLMClient, "generate", provider))
        stack.enter_context(
            patch.object(TargetPostWriteComposition, "_complete_validation", complete)
        )
        summary = MainlineApplicationService(supervisor_factory=factory).run(
            project_root=Path(__file__).resolve().parents[2],
            task_id=task_id,
            run_id="run-" + task_id,
            user_input=project.user_query,
            output_root=root,
            clean_existing=True,
            settings=settings,
            project_input=project.model_dump(mode="json"),
            allow_demo_defaults=False,
            enable_agent_self_rag=False,
        )
    engine = factory.engines[0]
    top = workflow_tests._latest_w13_top_level_payload(engine)
    if top.terminal_error is not None:
        raise AssertionError(top.terminal_error.model_dump(mode="json"))
    assert len(captures) == 2
    return project, engine, calls, captures, summary, top


def test_default_factory_section_and_document_structural_coverage_are_live(tmp_path):
    project, engine, calls, captures, summary, top = _run(tmp_path)
    assert {item[0]["scope"].value for item in captures} == {"SECTION", "DOCUMENT"}
    assert not [
        request for request in calls
        if request.extra.get("response_contract") == COVERAGE_SEMANTIC_CONTRACT
    ]
    for _kwargs, item, facts, _outcome in captures:
        assert item.state.value == "PASS"
        assert len(facts) == 1
        assert facts[0].family.value == "COVERAGE_COMPLETENESS"
        assert facts[0].semantic_state.value == "PASS"
        assert facts[0].payload["coverage_relation"] == "PRESENT"
    assert summary["status"] == "partial_success"
    assert top.target_release_publication is not None


@pytest.mark.parametrize(
    ("observation", "expected"),
    [
        ("MATCH_OBSERVED", "PASS"),
        ("NO_MATCH_AFTER_COMPLETE_DOMAIN", "FAIL"),
        ("INDETERMINATE", "INDETERMINATE"),
    ],
)
def test_document_required_fields_use_semantic_presence_not_keywords(
    tmp_path, observation, expected
):
    _project_input, _engine, calls, captures, _summary, _top = _run(
        tmp_path,
        semantic_observation=observation,
        required_fields=("风险治理闭环", "职责边界"),
    )
    section = next(item for item in captures if item[0]["scope"].value == "SECTION")
    document = next(item for item in captures if item[0]["scope"].value == "DOCUMENT")
    # Field/slot targets are Document-only. Section still has its exact structural
    # Section-presence target and does not borrow the Document semantic result.
    assert section[1].state.value == "PASS"
    assert len(section[2]) == 1
    assert document[1].state.value == expected
    field_facts = [
        fact
        for fact in document[2]
        if fact.payload["coverage_target_ref"]
        and fact.payload["observation_codes"] == []
    ]
    assert len(field_facts) == 2
    assert {fact.semantic_state.value for fact in field_facts} == {expected}
    semantic_calls = [
        request for request in calls
        if request.extra.get("response_contract") == COVERAGE_SEMANTIC_CONTRACT
    ]
    assert len(semantic_calls) == 1
    assert len(_coverage_control(semantic_calls[0])["semantic_requirements"]) == 2


def test_explicit_citation_presence_missing_is_coverage_fail(tmp_path):
    _project_input, _engine, calls, captures, _summary, _top = _run(
        tmp_path,
        citation_required=True,
        citation_token="",
    )
    for _kwargs, item, facts, _outcome in captures:
        assert item.state.value == "FAIL"
        citation_facts = [
            fact
            for fact in facts
            if "EXACT_CITATION_OCCURRENCE_PRESENCE_ACCOUNTING"
            in fact.payload["observation_codes"]
        ]
        assert len(citation_facts) == 1
        assert citation_facts[0].payload["coverage_relation"] == "MISSING"
    assert not [
        request for request in calls
        if request.extra.get("response_contract") == COVERAGE_SEMANTIC_CONTRACT
    ]


def test_semantic_coverage_technical_failure_never_fabricates_field_fact(tmp_path):
    _project_input, engine, calls, captures, summary, top = _run(
        tmp_path,
        semantic_observation="BAD_JSON",
        required_fields=("风险治理闭环",),
    )
    assert top.terminal_error is None
    assert summary["status"] == "partial_success"
    section = next(item for item in captures if item[0]["scope"].value == "SECTION")
    document = next(item for item in captures if item[0]["scope"].value == "DOCUMENT")
    assert section[1].state.value == "PASS"
    assert document[1].state.value == "TECHNICAL_FAILURE"
    assert document[1].technical_failure_ref is not None
    field_facts = [
        fact for fact in document[2]
        if fact.payload.get("observation_codes") == []
    ]
    assert field_facts == []
    semantic_calls = [
        request for request in calls
        if request.extra.get("response_contract") == COVERAGE_SEMANTIC_CONTRACT
    ]
    assert len(semantic_calls) == 1
    composition = engine.stage_service.target_post_write_composition
    record = composition.validation_store.find_validator_execution_for_item(
        validation_run_id=document[1].fact_refs[0].validation_run_id
        if document[1].fact_refs
        else composition.gate_store.resolve_exact(document[3][1]).validation_run_id,
        validator_id="post_write_coverage",
        expected_item_ref=document[1].expected_validation_item_ref,
    )
    assert record is not None
    assert record.expected_item_accounting[0].execution_state.value == "TECHNICAL_FAILURE"


def test_malformed_existing_citation_counts_as_present_only_for_coverage(tmp_path):
    _project_input, engine, _calls, captures, _summary, top = _run(
        tmp_path,
        citation_required=True,
        citation_token="[C 1]",
    )
    composition = engine.stage_service.target_post_write_composition
    for _kwargs, item, facts, _outcome in captures:
        citation_facts = [
            fact
            for fact in facts
            if "EXACT_CITATION_OCCURRENCE_PRESENCE_ACCOUNTING"
            in fact.payload["observation_codes"]
        ]
        assert len(citation_facts) == 1
        assert citation_facts[0].semantic_state.value == "PASS"
        assert citation_facts[0].payload["coverage_relation"] == "PRESENT"
    final_control = composition.control_store.resolve_exact(
        top.target_post_write_control_decision_ref
    )
    final_gate = composition.gate_store.resolve_exact(
        final_control.delivery_gate_decision_ref
    )
    completion = composition.validation_store.resolve_completion(
        final_gate.gate_input_manifest.validation_completion_ref
    )
    integrity_item = next(
        item
        for item in completion.items
        if item.expected_validation_item_ref.startswith("citation:integrity:")
    )
    assert integrity_item.state.value == "FAIL"


@pytest.mark.parametrize("section_count", (4, 8))
def test_eight_section_required_field_stays_within_default_44_call_budget(tmp_path, section_count):
    from application.project_input_factory import ProjectInputFactory
    from test_phase7_document_closure_mainline import _run_fake

    original_build = ProjectInputFactory.build
    original_generate = FakeLLMClient.generate
    calls = []

    def build_with_field(self, *args, **kwargs):
        value = original_build(self, *args, **kwargs)
        titles=value.generation_requirements.required_sections[:section_count]
        generation=value.generation_requirements.model_copy(update={"required_sections":titles,
            "citation_required_sections":[title for title in value.generation_requirements.citation_required_sections if title in titles]},deep=True)
        output = value.output_schema.model_copy(
            update={"required_fields": ["风险治理闭环"], "required_sections":titles}, deep=True
        )
        return value.model_copy(update={"output_schema": output,"generation_requirements":generation}, deep=True)

    def provider(client, request):
        calls.append(request.model_copy(deep=True))
        response = original_generate(client, request)
        if request.extra.get("call_purpose") == "scheme_section_generation":
            return response.model_copy(update={"content": TEXT * 6}, deep=True)
        if request.extra.get("response_contract") == extraction_tests.CONTRACT:
            units = extraction_tests._task_contract(request)["coverage_units"]
            payload = {
                "units": [
                    {
                        "unit_id": unit["unit_id"],
                        "accounting_state": "FACTUAL_CLAIMS_MATERIALIZED",
                        "claims": [
                            {
                                "quote": TEXT,
                                "occurrence_index": 0,
                                "claim_type": "general_method",
                            }
                        ],
                    }
                    for unit in units
                ]
            }
            return response.model_copy(
                update={"content": json.dumps(payload, ensure_ascii=False)}, deep=True
            )
        return response

    summary, factory = _run_fake(
        tmp_path,
        run_id="coverage-default-budget347",
        patchers=(
            patch.object(ProjectInputFactory, "build", build_with_field),
            patch.object(FakeLLMClient, "generate", provider),
        ),
    )
    engine = factory.engines[0]
    top = workflow_tests._latest_w13_top_level_payload(engine)
    budget = engine.budget_runtime(top.task_id, top.run_id).canonical_state()
    coverage_calls = [
        request for request in calls
        if request.extra.get("response_contract") == COVERAGE_SEMANTIC_CONTRACT
    ]
    assert len(top.document_plan.sections) == section_count
    assert budget.limits.logical_model_calls == 44
    assert all(value==0 for value in budget.active_reserved.model_dump().values())
    if section_count==8:
        # User-approved complete 8×1 capacity refusal; preserve the workload.
        assert top.terminal_error is not None
        assert "logical_model_calls" in top.terminal_error.message and "limit=44" in top.terminal_error.message
        assert summary.get("target_release_publication") is None
        assert top.target_run_completion_assessment_ref is None
        assert engine.lifecycle_service.load_run(top.run_id).status.value!="success"
        assert budget.used.logical_model_calls==44
        assert not coverage_calls
        return
    assert top.terminal_error is None
    assert summary.get("target_release_publication") is not None
    assert engine.lifecycle_service.load_run(top.run_id).status.value=="success"
    assert len(coverage_calls) == 1
    # 348 shares one physical call across the independent Document CROSS_SECTION
    # and DOCUMENT_GLOBAL logical Consistency consumers; no dedicated Document
    # Consistency call remains in this workload.
    assert sum(request.extra.get("call_purpose") == "artifact_consistency_shared" for request in calls) == 1
    assert sum(request.extra.get("call_purpose") == "artifact_consistency" for request in calls) == 0
    assert budget.used.logical_model_calls == 28
    assert budget.active_reserved.logical_model_calls == 0


def test_terminal_replay_reuses_exact_coverage_and_corruption_fails_closed(tmp_path):
    _project_input, engine, _calls, captures, _summary, top = _run(tmp_path)
    composition = engine.stage_service.target_post_write_composition
    reopened = citation_tests._reopen(engine)
    before = citation_tests._durable_state(engine)
    with patch.object(
        FakeLLMClient,
        "generate",
        side_effect=AssertionError("terminal Coverage replay called model"),
    ):
        replay = reopened.complete_target_post_write(
            task_id=top.task_id,
            run_id=top.run_id,
            document_id=top.document_id,
            project_input=top.project_input,
            section_execution_results=tuple(top.section_execution_results),
        )
    assert replay.published_release.reuse_state.value == "EXACT_REQUEST_REPLAY"
    assert before == citation_tests._durable_state(engine)

    final_capture = next(item for item in captures if item[0]["scope"].value == "DOCUMENT")
    validation_run_id = final_capture[1].fact_refs[0].validation_run_id
    record = composition.validation_store.find_validator_execution_for_item(
        validation_run_id=validation_run_id,
        validator_id="post_write_coverage",
        expected_item_ref=final_capture[1].expected_validation_item_ref,
    )
    assert record is not None
    with composition.database.write_transaction(immediate=True) as connection:
        assert connection.execute(
            "UPDATE validator_execution_records SET record_json=? WHERE validator_execution_ref=?",
            ("{}", record.validator_execution_ref),
        ).rowcount == 1
    damaged = citation_tests._durable_state(engine)
    document_kwargs = final_capture[0]
    # Close CIT-344-NOTE-01 on the original compatibility helper itself: a
    # corrupt persisted execution is not equivalent to "missing" and must not
    # fall through into target/fact re-commit.
    with pytest.raises((ValueError, RuntimeError, KeyError)):
        composition._document_structural_coverage_item(
            validation_run_id=validation_run_id,
            subject_hash=composition.validation_store.resolve_completion(
                final_capture[3][0]
            ).subject_hash,
            artifact_ref=document_kwargs["artifact_ref"],
            document_id=document_kwargs["target_ref"],
            requirement_ref=document_kwargs["requirement_ref"],
            assembly_input_manifest_ref=document_kwargs["assembly_input_manifest_ref"],
            committed_only=True,
        )
    assert damaged == citation_tests._durable_state(engine)

    with patch.object(
        FakeLLMClient,
        "generate",
        side_effect=AssertionError("corrupt Coverage replay redispatched model"),
    ):
        with pytest.raises((ValueError, RuntimeError, KeyError)):
            reopened.complete_target_post_write(
                task_id=top.task_id,
                run_id=top.run_id,
                document_id=top.document_id,
                project_input=top.project_input,
                section_execution_results=tuple(top.section_execution_results),
            )
    assert damaged == citation_tests._durable_state(engine)
