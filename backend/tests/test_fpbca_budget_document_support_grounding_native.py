"""Fresh local Fake DOCUMENT_SUPPORT Grounding commit boundary control.

TXT ONLY. Root may export this independent module after all active jobs close.
This does not change the existing 98 cases, original BT03 funding, or production.
The original finite baseline is 24000 tokens. Exactly two explicit grants are
authored: the original association's exact current allowance and a finite 20000
token allowance for the new pending Support continuation. No third grant exists.
Native marker/terminal observations are required; absence never becomes PASS.
"""
from __future__ import annotations

import hashlib
import json
import os
from unittest.mock import patch
from uuid import uuid4

from model_gateway.fake_llm_client import FakeLLMClient
from schemas.model import TokenUsageSchema
from tools.fake_rag_tool import FakeRAGTool
from test_fpbca_bcd_budget_topup_application import (
    _NativeApplicationCase,
    _bt03_dedicated_source_run,
    _bt03_model_semantic_producer,
    _bt03_three_source_producer,
    _native_grounding_commit_observer,
    _native_grounding_commit_oracles,
    _native_observation_wire,
    _native_old_owner_operation_rows,
    _native_pending_command,
    _native_wait,
    _offline,
    _prephysical_charge_hook,
)


def _seal_native_observation(path, body):
    payload = (json.dumps(_native_observation_wire(body), ensure_ascii=False,
        sort_keys=True, indent=2) + "\n").encode("utf-8")
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("xb") as stream:
        stream.write(payload)
        stream.flush()
        os.fsync(stream.fileno())
    return {"path":str(path), "sha256":hashlib.sha256(payload).hexdigest()}


def _read_completed_document_carriers(case, refusal):
    wire = refusal.model_dump(mode="json")
    identity = wire["document_identity"]
    rows = []
    for ref_field, hash_field in (
        ("document_input_ref", "document_input_sha256"),
        ("document_result_ref", "document_result_sha256"),
    ):
        reference, body = case.store.get_json_with_reference(wire[ref_field], **identity)
        raw = case.store._path_for_ref(wire[ref_field]).read_bytes()
        rows.append({"ref_field":ref_field, "expected_sha256":wire[hash_field],
            "reference":reference.model_dump(mode="json"), "body":body,
            "raw_file_sha256":hashlib.sha256(raw).hexdigest()})
    ack_ref = wire["document_input_ref"].removesuffix("/input") + "/ack"
    reference, body = case.store.get_json_with_reference(ack_ref, **identity)
    raw = case.store._path_for_ref(ack_ref).read_bytes()
    rows.append({"ref_field":"actual_completed_association_ack",
        "reference":reference.model_dump(mode="json"), "body":body,
        "raw_file_sha256":hashlib.sha256(raw).hexdigest()})
    return rows


def test_native_pending_document_support_grounding_marker_and_terminal_first_priority(tmp_path):
    charges = []
    reported_allowance = []
    exact_association_allowance = []
    upstream = []
    validity = []
    grounding = []
    initial_charge = _prephysical_charge_hook(charges, initial=True)

    def configure(engine, case):
        initial_charge(engine, case)
        invoker = engine.stage_service.target_post_write_composition.citation_grounding.gateway.invoker
        original = invoker.invoke

        def report_first_association_allowance(request, model_name):
            response = original(request, model_name)
            if (request.extra.get("call_purpose") == "citation_association"
                    and "/admission/" in request.extra.get("logical_operation_id", "")
                    and not reported_allowance):
                assert len(exact_association_allowance) == 1
                amount = exact_association_allowance[0]
                response = response.model_copy(update={"token_usage":TokenUsageSchema(
                    prompt_tokens=amount, completion_tokens=0, total_tokens=amount)}, deep=True)
                reported_allowance.append({
                    "scope":"EXPLICIT_LOCAL_FAKE_PROVIDER_REPORTED_EXACT_FIRST_ASSOCIATION_ALLOWANCE",
                    "request":request.model_dump(mode="json"),
                    "response":response.model_dump(mode="json")})
            return response

        invoker.invoke = report_first_association_allowance
        _native_grounding_commit_observer(engine, case, grounding)

    with patch.object(FakeRAGTool, "run", _bt03_three_source_producer(FakeRAGTool.run, upstream)), \
            patch.object(FakeLLMClient, "generate", _bt03_model_semantic_producer(
                FakeLLMClient.generate, validity, tmp_path / "native_validity_oracle")):
        case = _NativeApplicationCase(tmp_path / "native", engine_hook=configure,
            run_builder=_bt03_dedicated_source_run, expected_current_child_count=1,
            native_run_id="btg_" + uuid4().hex[:12])
        packet1, pause1, state1, required1 = _native_wait(case)
        _seal_native_observation(tmp_path / "support_grounding_first_native_hold.json", {
            "scope":"FRESH_LOCAL_FAKE_NATIVE_APP_NOT_BUSINESS_ACCEPTANCE",
            "wait":packet1, "pause":pause1, "budget":state1, "before":case.before,
            "test_owned_native_resource_charges":charges, "required_tokens":required1})
        assert state1.limits.model_tokens_total == 24000
        assert len(packet1.current_children) == 1
        assert state1.remaining.model_tokens_total == 0 and len(charges) == 1
        allowance = max(1, required1 - state1.remaining.model_tokens_total)
        exact_association_allowance.append(required1)
        command1 = _native_pending_command(case, approval_id="grounding-association-finite",
            authorization_id="grounding-association-explicit", tokens=allowance)
        first = case.approve(command1)["budget_acknowledgement"]
        first_result = case.resume(first)
        packet2, pause2, state2, required2 = _native_wait(case)
        after_first = case.capture()
        support_denial_path = case.store._path_for_ref(packet2.refusal.native_denial_ref)
        support_denial_before = support_denial_path.read_bytes()
        document_before = _read_completed_document_carriers(case, packet2.refusal)
        _seal_native_observation(tmp_path / "support_grounding_pending_native_hold.json", {
            "wait":packet2, "pause":pause2, "budget":state2, "first_command":command1,
            "first_ack":first, "first_result":first_result, "after_first":after_first,
            "required_tokens":required2, "reported_allowance":reported_allowance,
            "original_support_denial_raw_sha256":hashlib.sha256(support_denial_before).hexdigest(),
            "completed_document_carriers":document_before})
        assert packet2.refusal.operation_kind == "DOCUMENT_SUPPORT"
        assert packet2.current_children == packet1.current_children
        assert first_result["canonical_budget_state"]["run"]["status"] == "paused"
        assert state2.remaining.model_tokens_total == 0 and len(reported_allowance) == 1
        new_after_first = after_first["physical_requests"][case.initial_physical_count:]
        assert len(new_after_first) == 1 and new_after_first[0]["caller_agent"] == "CitationClaimAssociation"

        # Only this independent new fixture authors a larger second finite grant.
        # Baseline and the original BT03 exact second grant are never changed.
        command2 = _native_pending_command(case, approval_id="grounding-support-finite-20000",
            authorization_id="grounding-support-explicit-20000", tokens=20000)
        _seal_native_observation(tmp_path / "support_grounding_second_explicit_command.json", {
            "command":command2, "baseline":state2.limits, "effective_before":state2.effective_limits,
            "scope":"EXPLICIT_FINITE_NEW_FIXTURE_ONLY_NO_AUTOMATIC_THIRD_GRANT"})
        second = case.approve(command2)["budget_acknowledgement"]
        try:
            second_result = case.resume(second)
        except Exception as exc:
            _seal_native_observation(tmp_path / "support_grounding_native_resume_error.json", {
                "status":"NATIVE_RESUME_ERROR_NOT_GAP_PASS", "error_type":type(exc).__name__,
                "error":str(exc), "second_ack":second, "grounding":grounding,
                "actual_native_state":case.capture()})
            raise
        after = case.capture()
        document_after = _read_completed_document_carriers(case, packet2.refusal)
        support_denial_after = support_denial_path.read_bytes()
        later = None
        if after["run"]["status"] == "paused":
            later_packet, later_pause, later_state, later_required = _native_wait(case)
            later = {"status":"UNREACHED_FINITE_PENDING_NO_THIRD_GRANT",
                "wait":later_packet, "pause":later_pause, "budget":later_state,
                "required_tokens":later_required}
        old_rows = _native_old_owner_operation_rows(case.before, after)
        facts = {
            "scope":"FRESH_LOCAL_FAKE_NATIVE_DOCUMENT_SUPPORT_GROUNDING_NOT_BUSINESS_ACCEPTANCE",
            "first_wait":packet1, "second_wait":packet2, "first_command":command1,
            "second_command":command2, "first_ack":first, "second_ack":second,
            "first_result":first_result, "second_result":second_result,
            "before":case.before, "after_first":after_first, "after":after,
            "test_owned_native_resource_charges":charges, "reported_allowance":reported_allowance,
            "native_upstream_sources":upstream, "native_authored_verifier_oracle":validity,
            "old_completed_owner_operations":old_rows, "grounding":grounding,
            "old_support_denial_raw_sha256_before":hashlib.sha256(support_denial_before).hexdigest(),
            "old_support_denial_raw_sha256_after":hashlib.sha256(support_denial_after).hexdigest(),
            "completed_document_carriers_before":document_before,
            "completed_document_carriers_after":document_after,
            "later_finite_pending":later,
        }
        _seal_native_observation(tmp_path / "support_grounding_native_observation.json", facts)
        case.receipt(["BT-17", "BT-21"], "support_grounding_terminal_priority",
            acknowledgements=[first, second], observations=_native_observation_wire(facts))
        assert after["budget"]["limits"] == state1.limits.model_dump(mode="json")
        assert after["budget"]["limits"]["model_tokens_total"] == 24000
        assert len(after["budget"]["grant_history"]) == 2
        assert command2.delta.model_tokens_total == 20000
        assert document_after == document_before
        assert support_denial_after == support_denial_before
        assert old_rows and len(old_rows) == len(case.before["physical_requests"])
        assert all(not row["new_matching_physical_entries"]
            and not row["changed_native_accounting_reservation_ids"] for row in old_rows)
        new_calls = after["physical_requests"][case.initial_physical_count:]
        assert sum(row["caller_agent"] == "CitationClaimAssociation" for row in new_calls) == 1
        assert any(row["caller_agent"] == "ClaimSupportEvaluator" for row in new_calls)
        for row in document_before[:2]:
            assert row["reference"]["content_hash"] == "sha256:" + row["expected_sha256"]
        # This supplemental positive control cannot pass on UNREACHED.
        # Actual callback observations prove the marker and terminal priority;
        # no forced production terminal or synthetic completion is introduced.
        result = _native_grounding_commit_oracles(grounding, "DOCUMENT_SUPPORT",
            root=case.root, allow_unreached=False)
        assert result["gap_pass"] is True
