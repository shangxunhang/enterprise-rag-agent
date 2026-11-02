"""348 actual Factory Artifact Consistency and Option-A Completion acceptance."""
from __future__ import annotations

from contextlib import ExitStack
from dataclasses import replace
import json
import os
from pathlib import Path
from unittest.mock import patch

import pytest

from application.mainline_service import MainlineApplicationService
from apps.enterprise_document.services.agent_rag_v1.post_write_composition import TargetPostWriteComposition
from apps.enterprise_document.services.agent_rag_v1.post_write_consistency_composition import (
    CONSISTENCY_RELATION_CONTRACT,
    CONSISTENCY_SHARED_RELATION_CONTRACT,
    ConsistencyMainlineAccounting,
)
from apps.enterprise_document.schemas.post_write.quality import (
    ConsistencyDiscoveryPlanSchema,
    SemanticRelationBindingSchema,
)
from core.config import get_settings
from model_gateway.fake_llm_client import FakeLLMClient

import test_multi_agent_v3_claim_materialization_mainline as extraction_tests
import test_multi_agent_v3_citation_integrity_mainline as citation_tests
import test_multi_agent_v3_workflow_recovery as workflow_tests


CLAIM_A = "当前方案声明资源数量为100。"
CLAIM_B = "汇总结果显示资源数量为120。"
BODY = (CLAIM_A + CLAIM_B) * 4


def _control(request):
    for message in request.messages:
        text = str(message.get("content", ""))
        if text.startswith("CONTROL_REF=consistency-batch://"):
            return json.loads(text.split("\n", 1)[1])
    raise AssertionError("Consistency model input lacks exact required pair control")


def _required_pairs(request):
    return _control(request)["required_relation_member_sets"]


def _shared_control(request):
    for message in request.messages:
        text = str(message.get("content", ""))
        if text.startswith("CONTROL_REF=consistency-shared-batch://"):
            return json.loads(text.split("\n", 1)[1])
    raise AssertionError("shared Consistency input lacks exact logical work controls")


def _assertion_refs(request):
    refs = []
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
                rows = value.get("assertions")
                if isinstance(rows, list):
                    refs.extend(
                        str(item.get("assertion_ref") or "")
                        for item in rows
                        if isinstance(item, dict) and item.get("assertion_ref")
                    )
                stack.extend(value.values())
            elif isinstance(value, list):
                stack.extend(value)
    return tuple(sorted(set(refs)))


def _run(root: Path, scenario: str, *, sections=1, repeats=4, expand=False,
         identity_suffix="", fixed_ingress=None, global_formal=False,
         body_padding="", allow_terminal_error=False):
    task_id = "task-consistency348-" + scenario.lower() + identity_suffix
    run_id = "run-consistency348-" + scenario.lower() + identity_suffix
    project = (workflow_tests._w13_current_new_task_project_input(workflow_tests._w13_single_section_project_input(task_id)) if sections == 1
               else workflow_tests._w13_current_new_task_project_input(workflow_tests._w13_two_section_project_input(task_id)))
    calls = []
    captures = []
    original_generate = FakeLLMClient.generate
    original_complete = TargetPostWriteComposition._complete_validation

    def provider(client, request):
        calls.append(request.model_copy(deep=True))
        response = original_generate(client, request)
        purpose = request.extra.get("call_purpose")
        contract = request.extra.get("response_contract")
        if purpose == "scheme_section_generation":
            body = (CLAIM_A + CLAIM_B) * repeats + body_padding
            if sections > 1:
                body += "本节按下述顺序展开说明。" * 4
            return response.model_copy(update={"content": body}, deep=True)
        if contract == extraction_tests.CONTRACT:
            units = extraction_tests._task_contract(request)["coverage_units"]
            payload = {
                "units": [
                    {
                        "unit_id": unit["unit_id"],
                        "accounting_state": "FACTUAL_CLAIMS_MATERIALIZED",
                        "claims": [
                            {"quote": CLAIM_A, "occurrence_index": index, "claim_type": "general_method"}
                            for index in range(repeats)
                        ]
                        + [
                            {"quote": CLAIM_B, "occurrence_index": index, "claim_type": "general_method"}
                            for index in range(repeats)
                        ],
                    }
                    for unit in units
                ]
            }
            return response.model_copy(update={"content": json.dumps(payload, ensure_ascii=False)}, deep=True)
        if contract == CONSISTENCY_SHARED_RELATION_CONTRACT:
            if scenario == "BAD_JSON":
                return response.model_copy(update={"content": "{"}, deep=True)
            refs = _assertion_refs(request)
            work_results = []
            for control in _shared_control(request)["logical_works"]:
                global_call = control["validation_scope"] == "DOCUMENT_GLOBAL"
                pairs = list(control["required_relation_member_sets"])
                if global_call and global_formal and not pairs:
                    pairs = [list(refs[:2])]
                if global_call and scenario in {"GLOBAL_CONFLICT", "GLOBAL_UNRESOLVED"}:
                    assert len(refs) >= 2
                    pairs = [list(refs[:2])]
                rows = []
                for pair in pairs:
                    row = {
                        "member_assertion_refs": sorted(pair),
                        "semantic_relation_description": "资源数量陈述是否可同时成立",
                        "role_bindings": ["resource-quantity"],
                        "scope_condition_bindings": [],
                        "reason_codes": ["TEST_CONSISTENCY_RELATION"],
                        "formal_predicate": None,
                        "precision_tolerance": None,
                    }
                    if (
                        scenario == "NO_MATERIAL"
                        or (
                            scenario in {"GLOBAL_CONFLICT", "GLOBAL_UNRESOLVED"}
                            and not global_call
                        )
                    ):
                        row.update(
                            relation_materiality="NO_MATERIAL_RELATION",
                            relation=None,
                        )
                    elif scenario in {"UNRESOLVED_MATERIALITY", "GLOBAL_UNRESOLVED"}:
                        row.update(
                            relation_materiality="INDETERMINATE",
                            relation=None,
                            ambiguity_codes=["SCOPE_UNRESOLVED"],
                        )
                    else:
                        relation = {
                            "INCONSISTENT": "INCONSISTENT",
                            "INDETERMINATE": "INDETERMINATE",
                            "FORMAL_EQ": "CONSISTENT",
                            "CROSS_CONFLICT": "INCONSISTENT",
                            "GLOBAL_CONFLICT": "INCONSISTENT",
                        }[scenario]
                        row.update(
                            relation_materiality="MATERIAL_RELATION",
                            relation=relation,
                        )
                        if scenario == "FORMAL_EQ":
                            row["formal_predicate"] = "EQ"
                    rows.append(row)
                work_results.append(
                    {
                        "logical_work_ref": control["logical_work_ref"],
                        "discovery_complete": True,
                        "results": rows,
                    }
                )
            return response.model_copy(
                update={
                    "content": json.dumps(
                        {"work_results": work_results}, ensure_ascii=False
                    )
                },
                deep=True,
            )
        if contract == CONSISTENCY_RELATION_CONTRACT:
            if scenario == "BAD_JSON":
                return response.model_copy(update={"content": "{"}, deep=True)
            rows = []
            control = _control(request)
            global_call = control["validation_scope"] == "DOCUMENT_GLOBAL"
            pairs = list(_required_pairs(request))
            if global_call and global_formal and not pairs:
                refs = _assertion_refs(request)
                pairs = [list(refs[:2])]
            if global_call and scenario in {"GLOBAL_CONFLICT", "GLOBAL_UNRESOLVED"}:
                refs = _assertion_refs(request)
                assert len(refs) >= 2
                pairs = [list(refs[:2])]
            if expand and control["discovery_mode"] == "LARGE_N_HIGH_RECALL":
                members = sorted({ref for pair in pairs for ref in pair})
                extra = [members[0], members[-1]]
                assert extra not in pairs
                pairs.append(extra)
            for pair in pairs:
                row = {
                    "member_assertion_refs": sorted(pair),
                    "semantic_relation_description": "资源数量陈述是否可同时成立",
                    "role_bindings": ["resource-quantity"],
                    "scope_condition_bindings": [],
                    "reason_codes": ["TEST_CONSISTENCY_RELATION"],
                    "formal_predicate": None,
                    "precision_tolerance": None,
                }
                if (
                    scenario == "NO_MATERIAL"
                    or (
                        scenario in {"GLOBAL_CONFLICT", "GLOBAL_UNRESOLVED"}
                        and not global_call
                    )
                    or (
                        scenario == "CROSS_CONFLICT"
                        and control["validation_scope"] == "SECTION"
                    )
                ):
                    row.update(relation_materiality="NO_MATERIAL_RELATION", relation=None)
                elif scenario in {"UNRESOLVED_MATERIALITY", "GLOBAL_UNRESOLVED"}:
                    row.update(relation_materiality="INDETERMINATE", relation=None,
                               ambiguity_codes=["SCOPE_UNRESOLVED"])
                else:
                    relation = {
                        "INCONSISTENT": "INCONSISTENT",
                        "INDETERMINATE": "INDETERMINATE",
                        "FORMAL_EQ": "CONSISTENT",
                        "CROSS_CONFLICT": "INCONSISTENT",
                        "GLOBAL_CONFLICT": "INCONSISTENT",
                    }[scenario]
                    row.update(relation_materiality="MATERIAL_RELATION", relation=relation)
                    if scenario == "FORMAL_EQ":
                        row["formal_predicate"] = "EQ"
                rows.append(row)
            return response.model_copy(
                update={"content": json.dumps({"discovery_complete": True, "results": rows}, ensure_ascii=False)},
                deep=True,
            )
        return response

    def complete(composition, **kwargs):
        outcome = original_complete(composition, **kwargs)
        completion = composition.validation_store.resolve_completion(outcome[0])
        item = next(row for row in completion.items if row.expected_validation_item_ref.startswith("consistency:item:"))
        facts = tuple(composition.validation_store.resolve_fact(ref) for ref in item.fact_refs)
        captures.append((dict(kwargs), item, facts, outcome))
        before = len(calls)
        with patch.object(FakeLLMClient, "generate", side_effect=AssertionError("committed Consistency replay called model")):
            assert original_complete(composition, **kwargs) == outcome
        assert len(calls) == before
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
    factory = workflow_tests._W13InspectingFactory(
        None if sections == 1 else workflow_tests._W13TwoSectionNormalization()
    )
    with ExitStack() as stack:
        stack.enter_context(patch.dict(os.environ, {
            "USE_REAL_RAG_TOOL": "false",
            "FAKE_RAG_SCENARIO": "default",
            "FAKE_LLM_SCENARIO": "default",
            "ENABLE_AGENT_SELF_RAG": "false",
            "ENABLE_SEMANTIC_GATE": "false",
        }))
        stack.enter_context(patch.object(FakeLLMClient, "generate", provider))
        stack.enter_context(patch.object(TargetPostWriteComposition, "_complete_validation", complete))
        service = MainlineApplicationService(supervisor_factory=factory)
        arguments = dict(
            project_root=Path(__file__).resolve().parents[2],
            task_id=task_id,
            run_id=run_id,
            user_input=project.user_query,
            output_root=root,
            clean_existing=True,
            settings=settings,
            project_input=project.model_dump(mode="json"),
            allow_demo_defaults=False,
            enable_agent_self_rag=False,
        )
        if fixed_ingress is None:
            summary = service.run(**arguments)
        else:
            assert arguments["project_input"] == fixed_ingress.project
            assert root.resolve() == fixed_ingress.root.resolve()
            summary = fixed_ingress.run(service, task_id=task_id, run_id=run_id, settings=settings)
    engine = factory.engines[0]
    top = workflow_tests._latest_w13_top_level_payload(engine)
    if allow_terminal_error and top.terminal_error is not None:
        return engine, calls, captures, summary, top
    if top.terminal_error is not None:
        raise AssertionError(top.terminal_error.model_dump(mode="json"))
    assert len(captures) == sections + 1
    return engine, calls, captures, summary, top


def _document_consistency_accounting(engine, capture):
    kwargs, item, _facts, outcome = capture
    assert kwargs["scope"].value == "DOCUMENT"
    composition = engine.stage_service.target_post_write_composition
    record = composition.validation_store.find_validator_execution_for_item(
        validation_run_id=outcome[0].validation_run_id,
        validator_id="post_write_consistency",
        expected_item_ref=item.expected_validation_item_ref,
    )
    assert record is not None
    return composition.validation_intermediate_runtime.resolve_typed(
        record.intermediate_refs[-1], ConsistencyMainlineAccounting
    )


def test_no_material_relations_use_proof_backed_pass_without_fake_fact(tmp_path):
    engine, calls, captures, summary, top = _run(tmp_path, "NO_MATERIAL")
    semantic_calls = [call for call in calls if call.extra.get("response_contract") == CONSISTENCY_RELATION_CONTRACT]
    assert len(semantic_calls) == 2
    controls = [_control(call) for call in semantic_calls]
    assert {row["validation_scope"] for row in controls} == {
        "SECTION",
        "DOCUMENT_GLOBAL",
    }
    for _kwargs, item, facts, _outcome in captures:
        assert item.state.value == "PASS"
        assert not facts and not item.fact_refs
        assert len(item.positive_proof_refs) >= 3
        for proof in item.positive_proof_refs:
            assert engine.stage_service.target_post_write_composition.validation_store.resolve_intermediate(proof)
    composition = engine.stage_service.target_post_write_composition
    final_control = composition.control_store.resolve_exact(top.target_post_write_control_decision_ref)
    gate = composition.gate_store.resolve_exact(final_control.delivery_gate_decision_ref)
    condition = next(row for row in gate.quality_policy_projection.condition_results if row.dimension_id == "consistency")
    assert condition.state.value == "SATISFIED"
    assert condition.positive_proof_refs
    assert summary["status"] == "partial_success"


@pytest.mark.parametrize(("scenario", "expected"), [
    ("INCONSISTENT", "FAIL"),
    ("INDETERMINATE", "INDETERMINATE"),
])
def test_material_relation_projects_real_consistency_fact(tmp_path, scenario, expected):
    _engine, _calls, captures, _summary, _top = _run(tmp_path, scenario)
    for _kwargs, item, facts, _outcome in captures:
        if _kwargs["scope"].value == "DOCUMENT":
            assert item.state.value == "PASS" and not facts
            continue
        assert item.state.value == expected
        assert not item.positive_proof_refs
        assert facts
        assert {fact.family.value for fact in facts} == {"ARTIFACT_CONSISTENCY"}
        assert {fact.semantic_state.value for fact in facts} == {expected}


def test_formal_eq_text_uses_existing_semantic_model_relation(tmp_path):
    _engine, _calls, captures, _summary, _top = _run(tmp_path, "FORMAL_EQ")
    # Whole-sentence strings lack independent atomic comparability proof. Guard v2
    # rejects Exact admission and consumes the same model's CONSISTENT relation.
    for _kwargs, item, facts, _outcome in captures:
        if _kwargs["scope"].value == "DOCUMENT":
            assert item.state.value == "PASS" and not facts
            continue
        assert item.state.value == "PASS"
        assert facts and {fact.semantic_state.value for fact in facts} == {"PASS"}
        assert all("FORMAL_RELATION_FALSE" not in fact.payload["observation_codes"]
            for fact in facts)


def test_semantic_consistency_technical_failure_never_fabricates_fact(tmp_path):
    _engine, _calls, captures, _summary, _top = _run(tmp_path, "BAD_JSON")
    for _kwargs, item, facts, _outcome in captures:
        assert item.state.value == "TECHNICAL_FAILURE"
        assert not facts and not item.fact_refs and not item.positive_proof_refs
        assert not item.indeterminate_proof_refs
        assert item.technical_failure_ref["kind"] == "CONSISTENCY_TECHNICAL_FAILURE"


def test_terminal_replay_reuses_consistency_without_model_or_writes(tmp_path):
    engine, _calls, _captures, _summary, top = _run(tmp_path, "NO_MATERIAL")
    composition = citation_tests._reopen(engine)
    before = citation_tests._durable_state(engine)
    with patch.object(FakeLLMClient, "generate", side_effect=AssertionError("terminal Consistency replay called model")):
        replay = composition.complete_target_post_write(
            task_id=top.task_id,
            run_id=top.run_id,
            document_id=top.document_id,
            project_input=top.project_input,
            section_execution_results=tuple(top.section_execution_results),
        )
    assert replay.published_release.reuse_state.value == "EXACT_REQUEST_REPLAY"
    assert before == citation_tests._durable_state(engine)


def test_unresolved_materiality_preserves_proof_and_terminal_replay(tmp_path):
    engine, _calls, captures, _summary, top = _run(tmp_path, "UNRESOLVED_MATERIALITY")
    for _kwargs, item, facts, _outcome in captures:
        if _kwargs["scope"].value == "DOCUMENT":
            assert item.state.value == "PASS" and not facts
            continue
        assert item.state.value == "INDETERMINATE"
        assert item.indeterminate_proof_refs and not item.positive_proof_refs and not facts
    composition = citation_tests._reopen(engine)
    before = citation_tests._durable_state(engine)
    with patch.object(FakeLLMClient, "generate", side_effect=AssertionError("uncertainty replay redispatched")):
        composition.complete_target_post_write(
            task_id=top.task_id, run_id=top.run_id, document_id=top.document_id,
            project_input=top.project_input, section_execution_results=tuple(top.section_execution_results),
        )
    assert before == citation_tests._durable_state(engine)


@pytest.mark.parametrize(
    ("scenario", "expected"),
    [
        ("GLOBAL_CONFLICT", "FAIL"),
        ("GLOBAL_UNRESOLVED", "INDETERMINATE"),
    ],
)
def test_document_global_is_independent_and_uses_production_contract(
    tmp_path, scenario, expected
):
    engine, calls, captures, _summary, top = _run(tmp_path, scenario)
    section = next(row for row in captures if row[0]["scope"].value == "SECTION")
    document = next(row for row in captures if row[0]["scope"].value == "DOCUMENT")
    assert section[1].state.value == "PASS" and not section[2]
    assert document[1].state.value == expected
    if expected == "FAIL":
        assert document[2]
        assert {fact.semantic_state.value for fact in document[2]} == {"FAIL"}
    else:
        assert not document[2]
        assert document[1].indeterminate_proof_refs

    controls = [
        _control(call)
        for call in calls
        if call.extra.get("response_contract") == CONSISTENCY_RELATION_CONTRACT
    ]
    assert {row["validation_scope"] for row in controls} == {
        "SECTION",
        "DOCUMENT_GLOBAL",
    }
    accounting = _document_consistency_accounting(engine, document)
    assert accounting.document_global is not None
    assert accounting.document_global.state == expected
    composition = engine.stage_service.target_post_write_composition
    global_plan = composition.validation_intermediate_runtime.resolve_typed(
        __import__(
            "apps.enterprise_document.schemas.post_write.foundation",
            fromlist=["ValidationIntermediateRefSchema"],
        ).ValidationIntermediateRefSchema.model_validate(
            accounting.document_global.plan_ref
        ),
        ConsistencyDiscoveryPlanSchema,
    )
    assert global_plan.runtime_procedure_input_hash is not None
    assert global_plan.required_logical_obligation_refs == (
        "consistency-obligation://document-global/all-included-assertions/v1",
    )

    final_control = composition.control_store.resolve_exact(
        top.target_post_write_control_decision_ref
    )
    entry = composition.entry_store.resolve_exact(
        final_control.input_manifest.post_write_entry_ref
    )
    contract = composition.contract_store.resolve_exact(
        entry.identity.quality_delivery_contract_ref
    )
    assert contract.consistency_procedure_input is not None
    source = contract.consistency_procedure_input.applicability_source_refs[0]
    snapshot = composition.requirement_store.resolve_exact(source.requirement_ref)
    requirement = next(
        item for item in snapshot.requirements
        if item.requirement_id == source.requirement_id
    )
    assert requirement.requirement_kind == "consistency_procedure"
    assert requirement.statement.startswith("quality-config://")


def test_document_cross_section_conflict_is_independent_of_section_pass(tmp_path):
    _engine, calls, captures, _summary, _top = _run(tmp_path, "CROSS_CONFLICT", sections=2, repeats=2)
    for kwargs, item, facts, _outcome in captures:
        if kwargs["scope"].value == "SECTION":
            assert item.state.value == "PASS" and not facts
        else:
            assert item.state.value == "FAIL" and facts
    semantic_calls = [call for call in calls if call.extra.get("response_contract") == CONSISTENCY_RELATION_CONTRACT]
    shared_calls = [call for call in calls if call.extra.get("response_contract") == CONSISTENCY_SHARED_RELATION_CONTRACT]
    assert len(semantic_calls) == 2 and len(shared_calls) == 1
    assert [_control(call)["validation_scope"] for call in semantic_calls] == ["SECTION", "SECTION"]
    logical_works = _shared_control(shared_calls[0])["logical_works"]
    assert {row["validation_scope"] for row in logical_works} == {"DOCUMENT", "DOCUMENT_GLOBAL"}
    cross = next(row for row in logical_works if row["validation_scope"] == "DOCUMENT")
    assert len(cross["required_relation_member_sets"]) == 16


def test_ten_canonical_claims_use_large_n_and_semantic_expansion(tmp_path):
    from apps.enterprise_document.services.post_write import consistency as owner
    original = owner.combinations
    def bounded_combinations(values, arity):
        values = tuple(values)
        assert len(values) <= 8, "large-N secretly enumerated all pairs"
        return original(values, arity)
    with patch.object(owner, "combinations", bounded_combinations):
        engine, calls, captures, _summary, _top = _run(tmp_path, "NO_MATERIAL", repeats=5, expand=True)
    semantic = [call for call in calls if call.extra.get("response_contract") == CONSISTENCY_RELATION_CONTRACT]
    assert len(semantic) == 2
    section_call = next(call for call in semantic if _control(call)["validation_scope"] == "SECTION")
    global_call = next(call for call in semantic if _control(call)["validation_scope"] == "DOCUMENT_GLOBAL")
    control = _control(section_call)
    assert control["discovery_mode"] == "LARGE_N_HIGH_RECALL"
    assert len(control["required_relation_member_sets"]) == 9
    assert _control(global_call)["discovery_mode"] == "DOCUMENT_GLOBAL_BOUNDED_DISCOVERY"
    section = next(item for kwargs, item, _facts, _outcome in captures if kwargs["scope"].value == "SECTION")
    assert section.state.value == "PASS" and not section.fact_refs
    runtime = engine.stage_service.target_post_write_composition.validation_intermediate_runtime
    from apps.enterprise_document.schemas.post_write.quality import ConsistencyCandidateDiscoveryResultSchema
    payloads = [engine.stage_service.target_post_write_composition.validation_store.resolve_intermediate(ref)
                for ref in section.positive_proof_refs]
    discovery = ConsistencyCandidateDiscoveryResultSchema.model_validate(next(p for p in payloads if p["schema_version"] == "consistency_candidate_discovery_result_v1"))
    assert len(discovery.candidate_member_refs) == 10  # 9 forced seeds plus semantic expansion, not 45 all-pairs.
