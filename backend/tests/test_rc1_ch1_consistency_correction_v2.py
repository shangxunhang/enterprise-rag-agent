"""RC1 correction compatibility through complete, offline production receipts.

All inputs in this module are newly generated Fake fixtures.  They are mechanism
evidence, never substitutes for the independent read-only R03 native replay.
"""
from __future__ import annotations

from contextlib import ExitStack
from copy import deepcopy
from dataclasses import dataclass
from hashlib import sha256
import json
import socket
from unittest.mock import patch
import uuid

import pytest
from pydantic import ValidationError

from apps.enterprise_document.services.agent_rag_v1 import post_write_consistency_composition as consistency
from core.hashing import canonical_sha256
from model_gateway.fake_llm_client import FakeLLMClient

import test_multi_agent_v3_consistency_mainline as mainline


V1 = "consistency-protocol-correction-v1"
V2 = "consistency-protocol-correction-v2"
CONTRACTS = {
    "ordinary": consistency.CONSISTENCY_RELATION_CONTRACT,
    "shared": consistency.CONSISTENCY_SHARED_RELATION_CONTRACT,
}
CASE_KEYS = ("v1-success", "v1-failure", "v2-success", "v2-failure")


def _forbidden(*_args, **_kwargs):
    raise AssertionError("offline correction verification attempted a network call or write")


def _control(request):
    for message in request.messages:
        content = str(message.get("content", ""))
        if content.startswith("CONTROL_REF=consistency-"):
            return json.loads(content.split("\n", 1)[1])
    raise AssertionError("semantic request omitted its production control")


class _FaultGateway:
    """Inject a wire-order fault after the existing production Fake oracle."""

    def __init__(self, gateway, *, always_bad, requests):
        self.gateway = gateway
        self.always_bad = always_bad
        self.requests = requests

    def __getattr__(self, name):
        return getattr(self.gateway, name)

    def generate(self, request):
        self.requests.append(request.model_copy(deep=True))
        response = self.gateway.generate(request)
        assert request.extra.get("response_contract") in CONTRACTS.values()
        child = "protocol_correction_feedback" in _control(request)
        if child and not self.always_bad:
            return response
        payload = json.loads(response.content)
        works = payload["work_results"] if "work_results" in payload else [payload]
        row = next(row for work in works for row in work["results"])
        members = row["member_assertion_refs"]
        assert len(members) >= 2 and members == sorted(set(members))
        row["member_assertion_refs"] = list(reversed(members))
        return response.model_copy(update={
            "content": json.dumps(payload, ensure_ascii=False)
        }, deep=True)


@dataclass
class _GeneratedCase:
    policy: str
    failed: bool
    engine: object
    captures: list
    requests: list


@pytest.fixture(scope="module")
def generated_cases(tmp_path_factory):
    """Four isolated Fake runs, each with two ordinary and one shared chain."""
    cases = {}
    profile = consistency._instruction_profile
    protocol_generate = consistency.TargetPostWriteConsistency._protocol_generate
    for case_key in CASE_KEYS:
        policy = V1 if case_key.startswith("v1-") else V2
        failed = case_key.endswith("-failure")
        requests = []

        def pinned_profile(*, correction_policy_version=policy, **kwargs):
            return profile(correction_policy_version=correction_policy_version, **kwargs)

        def generate(composer, *args, **kwargs):
            gateway = _FaultGateway(composer.gateway, always_bad=failed, requests=requests)
            with patch.object(composer, "gateway", gateway):
                return protocol_generate(composer, *args, **kwargs)

        with ExitStack() as stack:
            stack.enter_context(patch.object(socket.socket, "connect", _forbidden))
            stack.enter_context(patch.object(socket.socket, "connect_ex", _forbidden))
            stack.enter_context(patch.object(consistency, "_instruction_profile", pinned_profile))
            stack.enter_context(patch.object(consistency.TargetPostWriteConsistency, "_protocol_generate", generate))
            engine, _calls, captures, _summary, top = mainline._run(
                tmp_path_factory.mktemp("rc1-correction-" + case_key), "NO_MATERIAL",
                sections=2, repeats=1,
                body_padding="本节按既定工作流程逐步展开说明。" * 12,
                identity_suffix="-rc1-correction-" + uuid.uuid4().hex,
            )
        assert top.terminal_error is None
        assert len(requests) == 6, "three physical groups must each stop after one correction"
        cases[case_key] = _GeneratedCase(policy, failed, engine, captures, requests)
    return cases


@pytest.fixture(autouse=True)
def reject_network(monkeypatch):
    monkeypatch.setattr(socket.socket, "connect", _forbidden)
    monkeypatch.setattr(socket.socket, "connect_ex", _forbidden)


class _ReadOnlyStore:
    def __init__(self, records, identity):
        self.records = deepcopy(records)
        self.identity = identity
        self.reads = []

    def get_json(self, ref, **identity):
        assert identity == self.identity
        self.reads.append(ref)
        return deepcopy(self.records[ref])

    put_json = staticmethod(_forbidden)


def _receipt(case, route):
    request = next(request for request in case.requests
                   if request.extra["response_contract"] == CONTRACTS[route]
                   and "protocol_correction_feedback" not in _control(request))
    shared = route == "shared"
    prefix = "model_call_consistency_shared_" if shared else "model_call_consistency_"
    key = request.model_call_id.removeprefix(prefix)
    base = "artifact://artifact-consistency" + ("-shared" if shared else "") + "/" + key
    intent_ref, result_ref = base + "/input", base + "/result"
    identity = {"task_id": request.task_id, "run_id": request.run_id,
                "section_id": request.extra["section_id"]}
    store = case.engine.stage_service.target_post_write_composition.consistency_composition.store
    intent = store.get_json(intent_ref, **identity)
    result = store.get_json(result_ref, **identity)
    chain = result["protocol_correction"]
    refs = {intent_ref, result_ref, chain["first_response_ref"], chain["protocol_error_ref"],
            chain["child_input_ref"], chain["child_response_ref"],
            chain["child_input_ref"].removesuffix("/input") + "/result"}
    assert None not in refs and len(refs) == 7
    records = {ref: store.get_json(ref, **identity) for ref in refs}
    return intent_ref, result_ref, identity, records


def _verify(intent_ref, result_ref, identity, records):
    composer = object.__new__(consistency.TargetPostWriteConsistency)
    composer.store = _ReadOnlyStore(records, identity)
    composer.gateway = type("NoProvider", (), {"generate": staticmethod(_forbidden)})()
    before = deepcopy(composer.store.records)
    selected = composer._verify_protocol_receipt(
        intent_ref, records[intent_ref], records[result_ref], identity
    )
    assert composer.store.records == before
    return selected, composer.store.reads


@pytest.mark.parametrize("case_key", CASE_KEYS)
@pytest.mark.parametrize("route", CONTRACTS)
def test_complete_fake_production_receipt_and_read_only_replay(generated_cases, case_key, route):
    case = generated_cases[case_key]
    intent_ref, result_ref, identity, records = _receipt(case, route)
    intent, result = records[intent_ref], records[result_ref]
    chain = result["protocol_correction"]
    child_ref = chain["child_input_ref"]
    child = records[child_ref]
    first = records[chain["first_response_ref"]]
    last = records[chain["child_response_ref"]]
    material = intent["input_material"]
    feedback = child["input_material"]["protocol_correction_feedback"]
    expected_instruction = (consistency._PROTOCOL_CORRECTION_INSTRUCTION if case.policy == V1
                            else consistency._PROTOCOL_CORRECTION_INSTRUCTION_V2)
    assert material["instruction_profile"]["protocol_correction_policy_version"] == case.policy
    assert chain["policy_version"] == feedback["policy_version"] == case.policy
    assert feedback["correction_ordinal"] == 1 and feedback["instruction"] == expected_instruction
    assert feedback["diagnostic"] == records[chain["protocol_error_ref"]]
    assert feedback["protocol_error_sha256"] == canonical_sha256(feedback["diagnostic"])
    assert feedback["parent_input_hash"] == intent["input_hash"] == canonical_sha256(material)
    assert child["input_hash"] == canonical_sha256(child["input_material"])
    for generation in (first, last):
        raw = generation["response"]["content"]
        assert generation["raw_reply_sha256"] == sha256(raw.encode("utf-8")).hexdigest()
        assert generation["response_sha256"] == canonical_sha256(generation["response"])
        assert generation["canonical_reply_sha256"] == canonical_sha256(generation["canonical_reply"])
        normalized = consistency.TargetPostWriteConsistency._normalize_reason_order(
            json.loads(raw), canonical_sets=True
        )
        assert generation["canonical_reply"] == normalized
    parser = (consistency.TargetPostWriteConsistency._parse_shared_reply if route == "shared"
              else consistency.TargetPostWriteConsistency._parse_reply)
    with pytest.raises(ValidationError, match="members must be canonical"):
        parser(first["response"]["content"], material)
    if case.failed:
        assert result["outcome"] == "failed"
        expected_failure_kind = ("CONSISTENCY_SHARED_TECHNICAL_FAILURE" if route == "shared"
                                 else "CONSISTENCY_TECHNICAL_FAILURE")
        assert result["failure"]["kind"] == expected_failure_kind
        with pytest.raises(ValidationError, match="members must be canonical"):
            parser(last["response"]["content"], material)
        assert all(item.state.value == "TECHNICAL_FAILURE" for _kwargs, item, _facts, _outcome in case.captures)
        assert all(not facts for _kwargs, _item, facts, _outcome in case.captures)
    else:
        assert result["outcome"] == "completed"
        parser(last["response"]["content"], material)
    assert records[child_ref.removesuffix("/input") + "/result"]["outcome"] == result["outcome"]
    selected, reads = _verify(intent_ref, result_ref, identity, records)
    assert selected == chain["selected_input_ref"] == child_ref
    assert set(reads) == set(records) - {intent_ref, result_ref}
    groups = [request for request in case.requests if request.extra["response_contract"] == CONTRACTS[route]]
    assert len(groups) == (2 if route == "shared" else 4)
    assert len({request.model_call_id for request in groups}) == len(groups)
    assert sum("protocol_correction_feedback" in _control(request) for request in groups) == len(groups) // 2


@pytest.mark.parametrize("case_key", CASE_KEYS)
@pytest.mark.parametrize("route", CONTRACTS)
@pytest.mark.parametrize("fault", ("raw", "canonical", "feedback", "instruction", "child", "selected"))
def test_complete_receipt_rejects_each_hash_or_binding_tamper(generated_cases, case_key, route, fault):
    intent_ref, result_ref, identity, records = _receipt(generated_cases[case_key], route)
    chain = records[result_ref]["protocol_correction"]
    child = records[chain["child_input_ref"]]
    if fault == "raw":
        records[chain["first_response_ref"]]["raw_reply_sha256"] = "0" * 64
    elif fault == "canonical":
        records[chain["child_response_ref"]]["canonical_reply_sha256"] = "0" * 64
    elif fault == "feedback":
        child["input_material"]["protocol_correction_feedback"]["protocol_error_sha256"] = "0" * 64
    elif fault == "instruction":
        child["input_material"]["protocol_correction_feedback"]["instruction"] += " tampered"
    elif fault == "child":
        child["input_hash"] = "0" * 64
    else:
        chain["selected_input_ref"] = intent_ref
    with pytest.raises(consistency.ConsistencyCompositionError):
        _verify(intent_ref, result_ref, identity, records)


@pytest.mark.parametrize("case_key", CASE_KEYS)
@pytest.mark.parametrize("route", CONTRACTS)
def test_complete_receipt_unknown_policy_fails_closed(generated_cases, case_key, route):
    intent_ref, result_ref, identity, records = _receipt(generated_cases[case_key], route)
    records[intent_ref]["input_material"]["instruction_profile"]["protocol_correction_policy_version"] = "unknown-correction-policy"
    records[result_ref]["protocol_correction"]["policy_version"] = "unknown-correction-policy"
    with pytest.raises(consistency.ConsistencyCompositionError, match="unknown Consistency protocol correction policy"):
        _verify(intent_ref, result_ref, identity, records)


@pytest.mark.parametrize("outcome", ("success", "failure"))
def test_v1_outer_committed_execution_replays_under_v2_without_dispatch_or_changes(generated_cases, outcome):
    case = generated_cases["v1-" + outcome]
    assert consistency._instruction_profile()["protocol_correction_policy_version"] == V2
    original = case.engine.stage_service.target_post_write_composition
    reopened = mainline.citation_tests._reopen(case.engine)
    before = mainline.citation_tests._durable_state(case.engine)
    old_records = []
    with ExitStack() as stack:
        stack.enter_context(patch.object(FakeLLMClient, "generate", _forbidden))
        stack.enter_context(patch.object(reopened.consistency_composition.gateway, "generate", _forbidden))
        stack.enter_context(patch.object(reopened.consistency_composition.store, "put_json", _forbidden))
        for kwargs, old_item, _facts, outcome_refs in case.captures:
            old_record = original.validation_store.find_validator_execution_for_item(
                validation_run_id=outcome_refs[0].validation_run_id,
                validator_id=consistency.TargetPostWriteConsistency.VALIDATOR_ID,
                expected_item_ref=old_item.expected_validation_item_ref,
            )
            assert old_record is not None
            replayed = reopened.consistency_composition.complete_item(
                validation_run_id=old_record.validation_run_id,
                subject_hash=old_record.subject_hash,
                artifact_ref=kwargs["artifact_ref"], scope=kwargs["scope"],
                target_ref=kwargs["target_ref"], quality_contract_ref=kwargs["quality_contract_ref"],
                source_sections=kwargs["source_sections"],
                assembly_input_manifest_ref=kwargs.get("assembly_input_manifest_ref"),
            )
            assert replayed == old_item
            old_records.append((old_item, old_record))
    for old_item, old_record in old_records:
        assert reopened.validation_store.find_validator_execution_for_item(
            validation_run_id=old_record.validation_run_id,
            validator_id=consistency.TargetPostWriteConsistency.VALIDATOR_ID,
            expected_item_ref=old_item.expected_validation_item_ref,
        ) == old_record
    assert mainline.citation_tests._durable_state(case.engine) == before
