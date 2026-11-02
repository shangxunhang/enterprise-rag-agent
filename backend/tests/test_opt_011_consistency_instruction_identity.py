"""OPT-011 instruction, request, and immutable Consistency identity checks.

The integration case captures actual ModelRequests through the existing fake
mainline harness. Replay cases use isolated read-only artifact stand-ins.
"""

from copy import deepcopy
import json
from pathlib import Path
import subprocess
import sys
from threading import RLock
from types import ModuleType
from unittest.mock import patch

import pytest

from apps.enterprise_document.services.agent_rag_v1 import (
    post_write_consistency_composition as consistency,
)
from apps.enterprise_document.services.post_write.execution import (
    ValidatorInputEnvelopeBuilder,
)
from core.hashing import canonical_sha256
from model_gateway.model_contract import ModelRole
from model_gateway.fake_llm_client import FakeLLMClient

import test_multi_agent_v3_consistency_mainline as mainline


LEGACY_HEAD = "411e1e75958ffb9550039eefbba2d22c2399f149"
LEGACY_MODULE_PATH = (
    "backend/apps/enterprise_document/services/agent_rag_v1/"
    "post_write_consistency_composition.py"
)


def _model_fields_and_required(model):
    return set(model.model_fields), {
        name for name, field in model.model_fields.items() if field.is_required()
    }


def test_cons_01_schema_descriptor_tracks_existing_pydantic_models():
    descriptors, _examples, contracts = consistency._instruction_components()
    ordinary = descriptors["ordinary"]
    shared = descriptors["shared"]
    row = ordinary["$defs"]["ConsistencyRelationReplyRow"]
    shared_row = shared["$defs"]["ConsistencyRelationReplyRow"]
    work = shared["$defs"]["ConsistencySharedWorkReply"]

    for descriptor, model in (
        (ordinary, consistency.ConsistencyRelationReply),
        (shared, consistency.ConsistencySharedReply),
        (row, consistency.ConsistencyRelationReplyRow),
        (work, consistency.ConsistencySharedWorkReply),
    ):
        fields, required = _model_fields_and_required(model)
        assert set(descriptor["properties"]) == fields
        assert set(descriptor["required"]) == required
        assert descriptor["additionalProperties"] is False
    assert row == shared_row

    fields = row["properties"]
    assert fields["member_assertion_refs"] == {"items": {"type": "string"}, "type": "array"}
    for name in ("role_bindings", "scope_condition_bindings", "reason_codes", "ambiguity_codes"):
        assert fields[name] == {"items": {"type": "string"}, "type": "array"}
    assert set(fields["relation_materiality"]["enum"]) == {
        "MATERIAL_RELATION", "NO_MATERIAL_RELATION", "INDETERMINATE"
    }
    assert set(fields["relation"]["anyOf"][0]["enum"]) == {
        "CONSISTENT", "INCONSISTENT", "INDETERMINATE"
    }
    assert fields["relation"]["anyOf"][1] == {"type": "null"}
    assert set(fields["formal_predicate"]["anyOf"][0]["enum"]) == {
        "EQ", "NE", "LT", "LE", "GT", "GE"
    }
    assert fields["formal_predicate"]["anyOf"][1] == {"type": "null"}
    assert fields["precision_tolerance"]["anyOf"] == [
        {"minimum": 0, "type": "number"}, {"type": "null"}
    ]
    assert ordinary["properties"]["results"]["type"] == "array"
    assert shared["properties"]["work_results"]["type"] == "array"

    for variant, contract in contracts.items():
        advertised = json.loads(contract)
        assert advertised["reply_schema_descriptor"] == descriptors[variant]
        assert advertised["contract"] == (
            consistency.CONSISTENCY_RELATION_CONTRACT
            if variant == "ordinary"
            else consistency.CONSISTENCY_SHARED_RELATION_CONTRACT
        )


def _example_material(members):
    return {
        "discovery_mode": "SMALL_N_EXHAUSTIVE_MEMBERS",
        "validation_scope": "SECTION",
        "required_relation_member_sets": [list(members)],
        "semantic_candidate_budget": 1,
        "assertions": [{"assertion_ref": ref} for ref in members],
        "assertion_context": {ref: {"section_id": "example-section"} for ref in members},
        "profile": {"composite_max_arity": 2},
    }


def test_cons_02_and_03_advertised_examples_and_rule_scope_are_valid():
    _descriptors, examples, _contracts = consistency._instruction_components()
    replies = examples["ordinary"]["replies"]
    assert len(replies) == 3
    assert {reply["results"][0]["relation_materiality"] for reply in replies} == {
        "MATERIAL_RELATION", "NO_MATERIAL_RELATION", "INDETERMINATE"
    }
    for reply in replies:
        row = reply["results"][0]
        members = row["member_assertion_refs"]
        assert members == sorted(set(members))
        consistency.ConsistencyRelationReply.model_validate(reply)
        parsed = consistency.TargetPostWriteConsistency._parse_reply(
            json.dumps(reply), _example_material(members)
        )
        assert len(parsed) == 1
        assert all(isinstance(value, str) for name in (
            "role_bindings", "scope_condition_bindings", "reason_codes", "ambiguity_codes"
        ) for value in row[name])
        assert all("|" not in str(value) for value in row.values())
    assert any(
        row[name]
        for reply in replies
        for row in reply["results"]
        for name in ("role_bindings", "scope_condition_bindings", "reason_codes", "ambiguity_codes")
    )

    shared = examples["shared"]["reply"]
    validated_shared = consistency.ConsistencySharedReply.model_validate(shared)
    assert {row["relation_materiality"] for work in shared["work_results"]
            for row in work["results"]} == {
        "MATERIAL_RELATION", "NO_MATERIAL_RELATION", "INDETERMINATE"
    }
    material = {
        "logical_works": [
            {
                "logical_work_ref": work["logical_work_ref"],
                "material": _example_material(work["results"][0]["member_assertion_refs"]),
            }
            for work in shared["work_results"]
        ]
    }
    parsed_shared = consistency.TargetPostWriteConsistency._parse_shared_reply(shared, material)
    assert len(parsed_shared) == 2
    for work in validated_shared.work_results:
        assert parsed_shared[work.logical_work_ref] == work.results

    ordinary_rule = consistency._ORDINARY_TEMPLATE
    shared_rule = consistency._SHARED_TEMPLATE
    for rule in (ordinary_rule, shared_rule):
        for exact_clause in (
            "JSON string array of at least two", "canonical ordered-unique",
            "case-sensitive, not natural-number sorting", "relation_materiality=MATERIAL_RELATION",
            "relation=INDETERMINATE is valid", "lhs=member_assertion_refs[0]",
            "rhs=member_assertion_refs[1]", "zero candidates with discovery_complete=true",
            "semantic_candidate_budget", "composite_max_arity",
        ):
            assert exact_clause in rule
        assert "one member group may appear only once" not in rule
    assert "exactly one result for every supplied logical_work_ref" in shared_rule
    assert "Each logical_work's own" in shared_rule or "each logical_work's own" in shared_rule


def test_cons_02_and_13_static_profile_is_stable_and_excludes_final_input_hash(monkeypatch):
    profile = consistency._instruction_profile()
    assert profile == consistency._instruction_profile()
    assert profile["schema_version"] == "consistency-instruction-profile-v2"
    assert profile["dynamic_control_projection_version"] == "v2"
    assert profile["ordinary_prompt_version"] == profile["shared_prompt_version"] == "v2"
    assert profile["profile_hash"] == canonical_sha256({
        key: value for key, value in profile.items()
        if key not in {"profile_hash", "config_ref"}
    })
    assert profile["config_ref"] == (
        "consistency-instruction-profile://sha256/" + profile["profile_hash"]
    )
    for key in (
        "common_reply_rules", "ordinary_role", "shared_role", "ordinary_template",
        "shared_template", "reply_schema_descriptor", "valid_examples",
        "ordinary_output_contract", "shared_output_contract",
    ):
        assert len(profile[key]["sha256"]) == 64
    serialized = json.dumps(profile)
    assert "final_model_input_sha256" not in serialized
    assert "final_messages" not in serialized
    assert "model_call_id" not in serialized
    monkeypatch.setattr(
        consistency, "_ORDINARY_TEMPLATE",
        consistency._ORDINARY_TEMPLATE + " OPT-011 test-only instruction change.",
    )
    changed = consistency._instruction_profile()
    assert changed["ordinary_template"]["sha256"] != profile["ordinary_template"]["sha256"]
    assert changed["profile_hash"] != profile["profile_hash"]
    assert changed["config_ref"] != profile["config_ref"]
    assert "final_model_input_sha256" not in json.dumps(changed)


@pytest.fixture(scope="module")
def captured_mainline(tmp_path_factory):
    # Two ordinary section requests and one shared Document/Global request.
    return mainline._run(
        tmp_path_factory.mktemp("opt011-instruction-mainline"),
        "CROSS_CONFLICT", sections=2, repeats=2,
    )


def _semantic_calls(captured):
    _engine, calls, _captures, _summary, _top = captured
    return [
        call for call in calls
        if call.extra.get("response_contract") in {
            consistency.CONSISTENCY_RELATION_CONTRACT,
            consistency.CONSISTENCY_SHARED_RELATION_CONTRACT,
        }
    ]


def _persisted_intent(engine, request):
    shared = request.extra["response_contract"] == consistency.CONSISTENCY_SHARED_RELATION_CONTRACT
    prefix = "model_call_consistency_shared_" if shared else "model_call_consistency_"
    key = request.model_call_id.removeprefix(prefix)
    assert len(key) == 64 and key != request.model_call_id
    ref = "artifact://artifact-consistency" + ("-shared" if shared else "") + "/" + key + "/input"
    store = engine.stage_service.target_post_write_composition.consistency_composition.store
    intent = store.get_json(
        ref, task_id=request.task_id, run_id=request.run_id,
        section_id=request.extra["section_id"],
    )
    return key, ref, intent


def test_cons_12_actual_ordinary_and_shared_requests_bind_locked_instruction(captured_mainline):
    engine, _calls, _captures, _summary, _top = captured_mainline
    semantic = _semantic_calls(captured_mainline)
    ordinary = [call for call in semantic if call.extra["response_contract"] == consistency.CONSISTENCY_RELATION_CONTRACT]
    shared = [call for call in semantic if call.extra["response_contract"] == consistency.CONSISTENCY_SHARED_RELATION_CONTRACT]
    assert len(ordinary) == 2 and len(shared) == 1
    profile = consistency._instruction_profile()
    for request in semantic:
        key, _ref, intent = _persisted_intent(engine, request)
        material = intent["input_material"]
        descriptors, examples, contracts = consistency._instruction_components(
            material["profile"]["composite_max_arity"]
        )
        package = intent["context_package"]
        model_input = package["model_input"]
        final_payload = model_input["final_input_material"]["inline_final_input_payload"]
        binding = package["instruction_binding"]
        variant = "shared" if request in shared else "ordinary"
        assert intent["input_hash"] == key == canonical_sha256(material)
        # Gateway routing enriches the observed request after immutable intent
        # persistence (notably model_name and routing metadata).
        saved_request = intent["request"]
        for field in ("model_call_id", "task_id", "run_id", "messages", "max_tokens", "temperature"):
            assert saved_request[field] == getattr(request, field)
        for field in (
            "response_contract", "prompt_version", "instruction_profile_ref",
            "instruction_profile_sha256", "logical_operation_id",
        ):
            assert saved_request["extra"][field] == request.extra[field]
        assert material["instruction_profile"] == profile
        assert request.extra["instruction_profile_ref"] == profile["config_ref"]
        assert request.extra["instruction_profile_sha256"] == profile["profile_hash"]
        assert request.extra["prompt_version"] == profile[f"{variant}_prompt_version"]
        assert request.messages == final_payload["messages"]
        assert canonical_sha256(final_payload) == model_input["final_model_input_sha256"]
        assert request.extra["llm_context_summary"]["context_sha256"] == model_input["final_model_input_sha256"]
        assert binding["prompt_template_version"] == "v2"
        assert binding["dynamic_control_bindings"][0]["control_version"] == "v2"
        assert binding["system_instruction_version"] == "v1"
        assert binding["role_instruction_version"] == "v1"
        assert binding["prompt_template_sha256"] == profile[f"{variant}_template"]["sha256"]
        assert binding["role_instruction_sha256"] == profile[f"{variant}_role"]["sha256"]

        contract_messages = [
            json.loads(message["content"]) for message in request.messages
            if str(message["content"]).startswith('{"contract":')
        ]
        assert len(contract_messages) == 1
        contract = contract_messages[0]
        assert contract["contract"] == request.extra["response_contract"]
        assert contract["reply_schema_descriptor"] == descriptors[variant]
        assert contract["examples"] == examples[variant]
        assert binding["task_output_contract_sha256"] == canonical_sha256(contracts[variant])
        assert any("canonical ordered-unique" in str(message["content"]) for message in request.messages)

        control = mainline._shared_control(request) if variant == "shared" else mainline._control(request)
        assert control["instruction_profile_ref"] == profile["config_ref"]
        expected_works = (
            [(work["logical_work_ref"], work["material"]) for work in material["logical_works"]]
            if variant == "shared" else [(None, material)]
        )
        actual_works = control["logical_works"] if variant == "shared" else [control]
        assert len(actual_works) == len(expected_works)
        for actual, (logical_ref, expected) in zip(actual_works, expected_works):
            if logical_ref is not None:
                assert actual["logical_work_ref"] == logical_ref
            for field in (
                "validation_scope", "discovery_mode", "required_relation_member_sets",
                "semantic_candidate_budget",
            ):
                assert actual[field] == expected[field]
            assert actual["required_logical_obligation_refs"] == expected.get(
                "required_logical_obligation_refs", []
            )
            assert actual["composite_max_arity"] == expected["profile"]["composite_max_arity"]
            assert "assertions" not in actual
        assert binding["dynamic_control_bindings"][0]["control_sha256"] == canonical_sha256(
            next(str(message["content"]).split("\n", 1)[1] for message in request.messages
                 if str(message["content"]).startswith("CONTROL_REF="))
        )


def test_cons_13_current_material_inner_and_outer_identity_changes_with_profile(captured_mainline):
    engine, _calls, captures, _summary, _top = captured_mainline
    request = next(call for call in _semantic_calls(captured_mainline)
                   if call.extra["response_contract"] == consistency.CONSISTENCY_RELATION_CONTRACT)
    key, _ref, intent = _persisted_intent(engine, request)
    material = intent["input_material"]
    changed = deepcopy(material)
    changed_profile = changed["instruction_profile"]
    changed_profile["common_reply_rules"]["sha256"] = "0" * 64
    static = {name: value for name, value in changed_profile.items()
              if name not in {"profile_hash", "config_ref"}}
    changed_profile["profile_hash"] = canonical_sha256(static)
    changed_profile["config_ref"] = "consistency-instruction-profile://sha256/" + changed_profile["profile_hash"]
    changed_key = canonical_sha256(changed)
    assert changed_key != key
    assert consistency.TargetPostWriteConsistency._logical_work_ref(changed) != (
        consistency.TargetPostWriteConsistency._logical_work_ref(material)
    )
    assert "artifact://artifact-consistency/" + changed_key + "/input" != _ref
    assert "model_call_consistency_" + changed_key != request.model_call_id
    assert "consistency-call://sha256/" + changed_key != request.extra["logical_operation_id"]
    assert "final_model_input_sha256" not in json.dumps(material["instruction_profile"])

    section_capture = next(row for row in captures if row[0]["scope"].value == "SECTION")
    kwargs, item, _facts, outcome = section_capture
    composition = engine.stage_service.target_post_write_composition
    record = composition.validation_store.find_validator_execution_for_item(
        validation_run_id=outcome[0].validation_run_id,
        validator_id=consistency.TargetPostWriteConsistency.VALIDATOR_ID,
        expected_item_ref=item.expected_validation_item_ref,
    )
    assert record is not None
    # Rebuild from the exact captured validator input; config is execution
    # identity, while the exact Claim provenance remains business authority.
    builder = ValidatorInputEnvelopeBuilder(validation_view_resolver=composition.validation_view_resolver)
    envelope_args = dict(
        validation_run_id=record.validation_run_id,
        subject_hash=record.subject_hash,
        artifact_state_ref=kwargs["artifact_ref"],
        stage_plan=consistency.TargetPostWriteConsistency.stage_plan(),
        applicable_expected_validation_item_refs=(item.expected_validation_item_ref,),
        exact_authority_refs=("post-write-claim-source://sha256/" + material["source_snapshot_hash"],),
        execution_contract_version="validator-execution-v1",
    )
    envelope = builder.build(
        **envelope_args,
        execution_config_refs=(material["instruction_profile"]["config_ref"],),
    )
    legacy = builder.build(**envelope_args)
    changed_envelope = builder.build(
        **envelope_args, execution_config_refs=(changed_profile["config_ref"],)
    )
    assert envelope.input_hash == record.input_hash
    assert record.validator_execution_ref == "validator-execution:consistency-mainline:" + canonical_sha256({
        "input_hash": envelope.input_hash, "item_ref": item.expected_validation_item_ref,
    })
    assert envelope.execution_config_refs == (material["instruction_profile"]["config_ref"],)
    assert envelope.exact_authority_refs == envelope_args["exact_authority_refs"]
    assert legacy.input_hash != envelope.input_hash != changed_envelope.input_hash
    assert legacy.execution_config_refs == ()

    shared_request = next(call for call in _semantic_calls(captured_mainline)
                          if call.extra["response_contract"] == consistency.CONSISTENCY_SHARED_RELATION_CONTRACT)
    shared_key, _shared_ref, shared_intent = _persisted_intent(engine, shared_request)
    shared_material = shared_intent["input_material"]
    altered_works = [deepcopy(work["material"]) for work in shared_material["logical_works"]]
    for work in altered_works:
        work["instruction_profile"] = deepcopy(changed_profile)
    altered_shared = consistency.TargetPostWriteConsistency._shared_material(altered_works)
    assert canonical_sha256(altered_shared) != shared_key
    assert set(work["logical_work_ref"] for work in altered_shared["logical_works"]) != {
        work["logical_work_ref"] for work in shared_material["logical_works"]
    }
    assert "model_call_consistency_shared_" + canonical_sha256(altered_shared) != shared_request.model_call_id
    assert "consistency-shared-call://sha256/" + canonical_sha256(altered_shared) != (
        shared_request.extra["logical_operation_id"]
    )


def test_cons_13_shared_work_rejects_mixed_instruction_profiles():
    first = _synthetic_material(
        required=("example-assertion:10", "example-assertion:2"), profiled=True
    )
    second = _synthetic_material(
        required=("example-assertion:10", "example-assertion:3"),
        target="item-2", profiled=True,
    )
    consistency.TargetPostWriteConsistency._shared_material((first, second))
    changed = deepcopy(second)
    changed["instruction_profile"]["profile_hash"] = "0" * 64
    with pytest.raises(consistency.ConsistencyCompositionError, match="compatible domain"):
        consistency.TargetPostWriteConsistency._shared_material((first, changed))
    legacy = consistency.TargetPostWriteConsistency._legacy_material(second)
    with pytest.raises(consistency.ConsistencyCompositionError, match="mix instruction profiles"):
        consistency.TargetPostWriteConsistency._shared_material((first, legacy))


def test_cons_14_limits_route_attempts_and_physical_call_count_are_unchanged(captured_mainline):
    engine, _calls, _captures, _summary, _top = captured_mainline
    semantic = _semantic_calls(captured_mainline)
    assert len(semantic) == len({call.model_call_id for call in semantic}) == 3
    stage = consistency.TargetPostWriteConsistency.stage_plan()
    assert stage.validator_version == "v6"
    assert stage.stage_plan_version == "v1"
    assert len(stage.stages) == 1 and stage.stages[0].max_attempts == 1
    assert consistency.TargetPostWriteConsistency.MAX_OUTPUT_TOKENS == 16384
    assert consistency.TargetPostWriteConsistency.SHARED_MAX_OUTPUT_TOKENS == 65536
    for request in semantic:
        assert request.model_role == ModelRole.GENERAL.value
        assert request.temperature == 0.0
        ceiling = (
            consistency.TargetPostWriteConsistency.SHARED_MAX_OUTPUT_TOKENS
            if request.extra["response_contract"] == consistency.CONSISTENCY_SHARED_RELATION_CONTRACT
            else consistency.TargetPostWriteConsistency.MAX_OUTPUT_TOKENS
        )
        assert 0 < request.max_tokens <= ceiling
        _key, _ref, intent = _persisted_intent(engine, request)
        package = intent["context_package"]
        assert package["budget_binding"]["reserved_output_tokens"] == request.max_tokens
        assert package["budget_binding"]["reserved_instruction_tokens"] > 0
        assert package["budget_binding"]["estimated_final_input_tokens"] <= (
            package["budget_binding"]["model_context_window"] - request.max_tokens
        )


def test_cons_13_old_committed_outer_execution_replays_read_only(tmp_path):
    """Generate an actual V1 committed receipt with pinned old code, then read it with V2."""
    repository = Path(__file__).resolve().parents[2]
    legacy_source = subprocess.run(
        ["git", "show", f"{LEGACY_HEAD}:{LEGACY_MODULE_PATH}"],
        cwd=repository,
        check=True,
        capture_output=True,
        text=True,
        encoding="utf-8",
    ).stdout
    module_name = (
        "apps.enterprise_document.services.agent_rag_v1."
        "_opt011_pinned_legacy_consistency"
    )
    legacy_module = ModuleType(module_name)
    legacy_module.__package__ = "apps.enterprise_document.services.agent_rag_v1"
    legacy_module.__file__ = consistency.__file__
    with patch.dict(sys.modules, {module_name: legacy_module}):
        exec(compile(legacy_source, legacy_module.__file__, "exec"), legacy_module.__dict__)
        with patch.object(
            consistency, "TargetPostWriteConsistency", legacy_module.TargetPostWriteConsistency
        ):
            engine, _calls, captures, _summary, _top = mainline._run(
                tmp_path / "legacy-outer", "NO_MATERIAL"
            )

    kwargs, old_item, _facts, outcome = next(
        row for row in captures if row[0]["scope"].value == "SECTION"
    )
    original = engine.stage_service.target_post_write_composition
    assert type(original.consistency_composition) is legacy_module.TargetPostWriteConsistency
    old_record = original.validation_store.find_validator_execution_for_item(
        validation_run_id=outcome[0].validation_run_id,
        validator_id=legacy_module.TargetPostWriteConsistency.VALIDATOR_ID,
        expected_item_ref=old_item.expected_validation_item_ref,
    )
    assert old_record is not None
    old_accounting = original.validation_intermediate_runtime.resolve_typed(
        old_record.intermediate_refs[-1], legacy_module.ConsistencyMainlineAccounting
    )
    assert old_accounting.semantic_input_receipt_ref is not None
    old_identity, _lineage = original.consistency_composition._identity(kwargs["source_sections"])
    old_intent = original.consistency_composition.store.get_json(
        old_accounting.semantic_input_receipt_ref, **old_identity
    )
    assert "instruction_profile" not in old_intent["input_material"]
    assert old_accounting.input_hash == canonical_sha256(old_intent["input_material"])

    builder = ValidatorInputEnvelopeBuilder(
        validation_view_resolver=original.validation_view_resolver
    )
    envelope_args = dict(
        validation_run_id=old_record.validation_run_id,
        subject_hash=old_record.subject_hash,
        artifact_state_ref=kwargs["artifact_ref"],
        stage_plan=legacy_module.TargetPostWriteConsistency.stage_plan(),
        applicable_expected_validation_item_refs=(old_item.expected_validation_item_ref,),
        exact_authority_refs=(
            "post-write-claim-source://sha256/" + old_accounting.source_snapshot_hash,
        ),
        execution_contract_version="validator-execution-v1",
    )
    legacy_envelope = builder.build(**envelope_args, execution_config_refs=())
    current_envelope = builder.build(
        **envelope_args,
        execution_config_refs=(consistency._instruction_profile()["config_ref"],),
    )
    assert legacy_envelope.execution_config_refs == ()
    assert legacy_envelope.input_hash == old_record.input_hash
    assert old_record.validator_execution_ref == (
        "validator-execution:consistency-mainline:"
        + canonical_sha256({
            "input_hash": legacy_envelope.input_hash,
            "item_ref": old_item.expected_validation_item_ref,
        })
    )
    assert current_envelope.input_hash != legacy_envelope.input_hash
    reopened = mainline.citation_tests._reopen(engine)
    assert type(reopened.consistency_composition) is consistency.TargetPostWriteConsistency
    before = mainline.citation_tests._durable_state(engine)
    with patch.object(
        FakeLLMClient, "generate",
        side_effect=AssertionError("old committed Consistency replay dispatched Provider"),
    ):
        replayed = reopened.consistency_composition.complete_item(
            validation_run_id=old_record.validation_run_id,
            subject_hash=old_record.subject_hash,
            artifact_ref=kwargs["artifact_ref"],
            scope=kwargs["scope"],
            target_ref=kwargs["target_ref"],
            quality_contract_ref=kwargs["quality_contract_ref"],
            source_sections=kwargs["source_sections"],
            assembly_input_manifest_ref=kwargs.get("assembly_input_manifest_ref"),
        )
    after = mainline.citation_tests._durable_state(engine)
    record_after = reopened.validation_store.find_validator_execution_for_item(
        validation_run_id=outcome[0].validation_run_id,
        validator_id=consistency.TargetPostWriteConsistency.VALIDATOR_ID,
        expected_item_ref=old_item.expected_validation_item_ref,
    )
    assert replayed == old_item
    assert record_after == old_record
    assert after == before


class _ReadOnlyArtifactStore:
    def __init__(self, root, records):
        self.root = root
        self.records = deepcopy(records)
        self.writes = 0

    def get_json(self, ref, **_identity):
        if ref not in self.records:
            raise FileNotFoundError(ref)
        return deepcopy(self.records[ref])

    def put_json(self, *_args, **_kwargs):
        self.writes += 1
        raise AssertionError("historical replay must not write an intent or result")


def _synthetic_material(*, required, scope="SECTION", target="item-1", profiled=False):
    refs = ("example-assertion:10", "example-assertion:2", "example-assertion:3")
    material = {
        "contract": consistency.CONSISTENCY_RELATION_CONTRACT,
        "policy_id": consistency.CONSISTENCY_POLICY_ID,
        "policy_version": consistency.CONSISTENCY_POLICY_VERSION,
        "validation_run_id": "validation-example",
        "subject_hash": "a" * 64,
        "validation_scope": scope,
        "target_ref": target,
        "artifact_state_ref": {"artifact_hash": "b" * 64},
        "source_snapshot_hash": "c" * 64,
        "assertions": [{"assertion_ref": ref} for ref in refs],
        "assertion_context": {ref: {"section_id": "section-a"} for ref in refs},
        "all_assertion_refs": list(refs),
        "discovery_mode": "SMALL_N_EXHAUSTIVE_MEMBERS",
        "required_relation_member_sets": [list(required)],
        "semantic_candidate_budget": 1,
        "profile": {"composite_max_arity": 3},
    }
    if profiled:
        material["instruction_profile"] = consistency._instruction_profile()
    return material


def _replay_composer(tmp_path, records):
    composition = object.__new__(consistency.TargetPostWriteConsistency)
    composition.store = _ReadOnlyArtifactStore(tmp_path, records)
    composition._lock = RLock()
    return composition


@pytest.mark.parametrize("shared", [False, True])
def test_cons_13_legacy_exact_completed_result_replays_without_provider_or_write(tmp_path, shared):
    first = _synthetic_material(required=("example-assertion:10", "example-assertion:2"))
    materials = (first, _synthetic_material(
        required=("example-assertion:10", "example-assertion:3"), target="item-2"
    )) if shared else (first,)
    if shared:
        saved = consistency.TargetPostWriteConsistency._shared_material(materials)
        candidate = {"work_results": [
            {"logical_work_ref": work["logical_work_ref"], "discovery_complete": True,
             "results": [{
                 "member_assertion_refs": work["material"]["required_relation_member_sets"][0],
                 "relation_materiality": "NO_MATERIAL_RELATION", "relation": None,
                 "semantic_relation_description": "Independent example relation",
             }]}
            for work in saved["logical_works"]
        ]}
        prefix = "artifact://artifact-consistency-shared/"
    else:
        saved = first
        candidate = {"discovery_complete": True, "results": [{
            "member_assertion_refs": list(materials[0]["required_relation_member_sets"][0]),
            "relation_materiality": "NO_MATERIAL_RELATION", "relation": None,
            "semantic_relation_description": "Independent example relation",
        }]}
        prefix = "artifact://artifact-consistency/"
    key = canonical_sha256(saved)
    intent_ref = prefix + key + "/input"
    result_ref = prefix + key + "/result"
    composer = _replay_composer(tmp_path, {
        intent_ref: {"input_hash": key, "input_material": saved},
        result_ref: {"input_hash": key, "outcome": "completed", "candidate": candidate},
    })
    validations = []
    validate = lambda: validations.append("current")
    if shared:
        parsed, returned_ref, failure = composer._evaluate_shared(
            materials, {}, None, validate
        )
        assert set(parsed) == {work["logical_work_ref"] for work in saved["logical_works"]}
    else:
        parsed, returned_ref, failure = composer._evaluate(first, {}, None, validate)
        assert len(parsed) == 1
    assert returned_ref == intent_ref and failure is None
    assert validations == ["current"]
    assert composer.store.writes == 0
    assert composer.store.records[intent_ref]["input_material"] == saved
    current_materials = tuple({**material, "instruction_profile": consistency._instruction_profile()}
                              for material in materials)
    with pytest.raises(consistency.ConsistencyCompositionError, match="legacy.*cannot continue"):
        if shared:
            composer._evaluate_shared(current_materials, {}, None, lambda: None)
        else:
            composer._evaluate(current_materials[0], {}, None, lambda: None)
    assert composer.store.writes == 0


@pytest.mark.parametrize("shared", [False, True])
@pytest.mark.parametrize("intent_exists", [False, True])
def test_cons_13_legacy_unfinished_or_unprofiled_new_dispatch_fails_closed(
    tmp_path, shared, intent_exists
):
    old = _synthetic_material(required=("example-assertion:10", "example-assertion:2"))
    new = _synthetic_material(required=("example-assertion:10", "example-assertion:2"), profiled=True)
    if shared:
        old_materials = (old, _synthetic_material(
            required=("example-assertion:10", "example-assertion:3"), target="item-2"
        ))
        new_materials = (new, _synthetic_material(
            required=("example-assertion:10", "example-assertion:3"), target="item-2", profiled=True
        ))
        old_saved = consistency.TargetPostWriteConsistency._shared_material(old_materials)
        prefix = "artifact://artifact-consistency-shared/"
    else:
        old_materials, new_materials = (old,), (new,)
        old_saved = old
        prefix = "artifact://artifact-consistency/"
    key = canonical_sha256(old_saved)
    intent_ref = prefix + key + "/input"
    records = {intent_ref: {"input_hash": key, "input_material": old_saved}} if intent_exists else {}
    composer = _replay_composer(tmp_path, records)
    evaluate = composer._evaluate_shared if shared else composer._evaluate
    arguments = lambda values: (values if shared else values[0], {}, None, lambda: None)
    if intent_exists:
        with pytest.raises(consistency.ConsistencyCompositionError, match="intent without result"):
            evaluate(*arguments(old_materials))
        with pytest.raises(consistency.ConsistencyCompositionError, match="legacy.*cannot continue"):
            evaluate(*arguments(new_materials))
    else:
        with pytest.raises(consistency.ConsistencyCompositionError, match="requires the current instruction profile"):
            evaluate(*arguments(old_materials))
    assert composer.store.writes == 0
