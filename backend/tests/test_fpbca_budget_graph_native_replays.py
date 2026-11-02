"""BT01: original 24000 configurations, fresh same-width IDs, passive old receipts.

The current provider allowance is UTF-8 input bytes plus routed max_tokens.
Historical source errors and fresh PAUSED behavior are checked separately.
"""
from __future__ import annotations

import base64
from dataclasses import replace
from difflib import SequenceMatcher
import hashlib
import json
import os
from pathlib import Path
import socket
import threading
import time
from unittest.mock import patch

import pytest
from apps.enterprise_document.services.agent_rag_v1.budget_manager import CanonicalBudgetCapacityExceeded
from apps.enterprise_document.services.agent_rag_v1.model_tool_budget_runtime import CanonicalInvocationBudgetAdapter
from apps.enterprise_document.services.scheme_writer.stage_service import SchemeGenerationStageService
from core.runtime.execution_control import current_execution_control
from schemas.model import ModelRequestSchema
from test_phase2_runtime_safety_closure import _execution, _run_production_fake

ROOT = Path(__file__).resolve().parents[2]
HISTORICAL_RECEIPT = ROOT / "docs/fpbca_bcd_unified_implementation_20261008/receipts/nonclock_n_source_blocker_02.json"
HISTORICAL_RECEIPT_SHA256 = "2672d69df4fd89436fe4a7c88960e6510a0dc167e397985f69d73932d0c0a01f"
ORIGINAL_FIXTURE = ROOT / "backend/tests/test_phase2_runtime_safety_closure.py"
ORIGINAL_FIXTURE_SHA256 = "a4e4d8cc326837d1fe1854e440bfdf7870b06ef20937198ccc9e4503bfa0f748"
CASES = (
    ("success", "phase2_runtime_success_records", "bt01aa_runtime_success_records", 19165, 18937),
    ("late_side_effect", "phase2_runtime_late_side_effect", "bt01aa_runtime_late_side_effect", 19209, 18935),
    ("retry_exactly_once", "phase2_runtime_retry_exactly_once", "bt01aa_runtime_retry_exactly_once", 19297, 18931),
)


def _sha(data):
    return hashlib.sha256(data).hexdigest()


def _json_once(path, value):
    with path.open("x", encoding="utf-8") as stream:
        json.dump(value, stream, ensure_ascii=False, indent=2)
        stream.flush()
        os.fsync(stream.fileno())


def _history(old_run):
    raw = HISTORICAL_RECEIPT.read_bytes()
    assert _sha(raw) == HISTORICAL_RECEIPT_SHA256
    receipt = json.loads(raw)
    cases = [item for item in receipt["cases"] if item["run_id"] == old_run]
    assert len(cases) == 1
    case = cases[0]
    bytes_before = {str(HISTORICAL_RECEIPT): _sha(raw)}
    values = {}
    for suffix in ("input", "result"):
        pins = [item for item in receipt["byte_preservation"]
                if item["original_path"] == case[f"native_{suffix}_path"]]
        assert len(pins) == 1
        pin = pins[0]
        # Read the hash-sealed copy, never reconstruct an owner on the old root.
        preserved = Path(pin["preserved_path"])
        data = preserved.read_bytes()
        assert _sha(data) == pin["sha256"] and len(data) == pin["size"]
        envelope = json.loads(data)
        content = base64.b64decode(envelope["content_base64"], validate=True)
        assert envelope["reference"] == case[f"native_{suffix}_ref"]
        assert envelope["reference"]["content_hash"] == "sha256:" + _sha(content)
        bytes_before[str(preserved)] = _sha(data)
        values[suffix] = json.loads(content)
    source_pins = {Path(item["original_path"]).relative_to(ROOT).as_posix(): item["sha256"]
        for item in receipt["byte_preservation"]
        if "/source/" in item["preserved_path"].replace("\\", "/")}
    return case, values, bytes_before, source_pins


def _parts(request):
    parts = {"prompt": len((request.get("prompt") or "").encode("utf-8")),
             "system_prompt": len((request.get("system_prompt") or "").encode("utf-8"))}
    for i, message in enumerate(request["messages"]):
        for field in ("role", "content"):
            parts[f"messages[{i}].{field}"] = len(str(message.get(field) or "").encode("utf-8"))
    return parts


def _changes(old, new, path="$"):
    if type(old) is not type(new):
        return [{"path": path, "old": old, "new": new}]
    if isinstance(old, dict):
        changes = []
        for key in sorted(set(old) | set(new)):
            if key not in old or key not in new:
                changes.append({"path": path + "." + key, "old_present": key in old,
                                "new_present": key in new, "old": old.get(key), "new": new.get(key)})
            else:
                changes.extend(_changes(old[key], new[key], path + "." + key))
        return changes
    if isinstance(old, list):
        changes = []
        for i in range(max(len(old), len(new))):
            if i >= len(old) or i >= len(new):
                changes.append({"path": f"{path}[{i}]", "old_present": i < len(old), "new_present": i < len(new),
                                "old": old[i] if i < len(old) else None, "new": new[i] if i < len(new) else None})
            else:
                changes.extend(_changes(old[i], new[i], f"{path}[{i}]"))
        return changes
    if old == new:
        return []
    if isinstance(old, str):
        edits = [{"opcode": kind, "old_offsets": [i,j], "new_offsets": [k,l],
                  "old_text": old[i:j], "new_text": new[k:l]}
                 for kind,i,j,k,l in SequenceMatcher(None, old, new, autojunk=False).get_opcodes() if kind != "equal"]
        return [{"path": path, "old_sha256": _sha(old.encode("utf-8")), "new_sha256": _sha(new.encode("utf-8")),
                 "old_utf8_bytes": len(old.encode("utf-8")), "new_utf8_bytes": len(new.encode("utf-8")), "exact_text_edits": edits}]
    return [{"path": path, "old": old, "new": new}]


def _observe(engine, sink, physical):
    pw = engine.stage_service.target_post_write_composition
    invoke = pw.citation_grounding.gateway.invoker.invoke
    lock = threading.Lock()
    def measured(request, model_name):
        with lock:
            physical.append({"request": request.model_copy(deep=True).model_dump(mode="json"), "model_name": model_name})
        return invoke(request, model_name)
    pw.citation_grounding.gateway.invoker.invoke = measured
    stage = engine._run_stage
    def observed_stage(runtime_state, **kwargs):
        if kwargs["node_name"] == "evaluate_document_gate":
            _, payload = engine._restore(runtime_state, kwargs["workflow"])
            native = pw.lifecycle_service.budget_preimage(payload.run_id)
            budget = engine.budget_runtime(payload.task_id, payload.run_id, require_existing_account=True).canonical_state()
            sink.append({"run": native.run.model_dump(mode="json"),
                         "children": [row.model_dump(mode="json",round_trip=True) for row in native.current_children],
                         "child_preimage_sha256": native.child_preimage_sha256,
                         "budget": budget.model_dump(mode="json"), "physical_count": len(physical)})
        return stage(runtime_state, **kwargs)
    engine._run_stage = observed_stage


@pytest.mark.parametrize("variant,old_run,fresh_run,old_required,old_remaining", CASES)
def test_bt01_original_native_configs_fresh_fixed24000_hold_without_document_dispatch(
        tmp_path, monkeypatch, variant, old_run, fresh_run, old_required, old_remaining):
    assert fresh_run != old_run and len(fresh_run.encode("utf-8")) == len(old_run.encode("utf-8"))
    assert _sha(ORIGINAL_FIXTURE.read_bytes()) == ORIGINAL_FIXTURE_SHA256
    history, historical, preserved_before, historical_sources = _history(old_run)
    assert (history["required"],history["remaining"],history["limit"]) == (old_required,old_remaining,24000)
    assert CanonicalInvocationBudgetAdapter.provider_token_allowance(ModelRequestSchema.model_validate(historical["input"]["request"])) == old_required
    old_error = CanonicalBudgetCapacityExceeded("model_tokens_total",limit=24000,remaining=old_remaining,required=old_required)
    assert str(old_error) == historical["result"]["failure"]["message"]
    assert "phase_proof" not in historical["result"]["failure"]
    def deny_network(*_args, **_kwargs):
        raise RuntimeError("BT01 real/network resources are not authorized")
    monkeypatch.setattr(socket,"create_connection",deny_network)
    monkeypatch.setattr(socket.socket,"connect",deny_network)
    monkeypatch.setattr(socket.socket,"connect_ex",deny_network)
    observed,physical,attempts,committed = [],[],[],[]
    original = SchemeGenerationStageService.prepare_document_plan
    def slow_stage(self, shared_state, payload):
        control = current_execution_control()
        assert control is not None
        control.defer_external_commit(lambda:committed.append("committed"),description="phase2-late-side-effect",
            side_effect_id=f"phase2-late-side-effect:{shared_state.run_id}:plan_document")
        time.sleep(0.15)
        return original(self,shared_state,payload)
    def flaky_stage(self, shared_state, payload):
        attempt = len(attempts)+1
        attempts.append(attempt)
        control = current_execution_control()
        assert control is not None
        control.defer_external_commit(lambda:committed.append(f"commit:{shared_state.run_id}"),description="phase2-retry-side-effect",
            side_effect_id=f"phase2-retry-side-effect:{shared_state.run_id}:plan_document")
        if attempt == 1:
            raise ConnectionError("temporarily unavailable")
        return original(self,shared_state,payload)
    def install(engine):
        _observe(engine,observed,physical)
        if variant == "late_side_effect":
            engine.node_contracts["plan_document"] = replace(engine.node_contracts["plan_document"],timeout_seconds=0.01)
        elif variant == "retry_exactly_once":
            engine.node_contracts["plan_document"] = replace(engine.node_contracts["plan_document"],max_retries=1)
    patchers = () if variant == "success" else (patch.object(SchemeGenerationStageService,"prepare_document_plan",
        slow_stage if variant == "late_side_effect" else flaky_stage),)
    fresh_root = tmp_path/"fresh-native"
    assert not fresh_root.exists()
    summary,factory = _run_production_fake(fresh_root,run_id=fresh_run,patchers=patchers,mutate_engine=install)
    # Preserve actual returned data before dereferencing the expected hold, so
    # a counterexample remains reviewable even when the hold is unreachable.
    _json_once(tmp_path/"bt01-native-run-returned.json",{
        "schema_version":"bt01_native_run_returned_v1","variant":variant,
        "old_run_id":old_run,"fresh_run_id":fresh_run,"summary":summary,
        "document_gate_observations":observed,"physical_entries":physical,
        "attempts":attempts,"committed":committed,
        "status":"ACTUAL_RETURNED_DATA_NO_DERIVED_VERDICT"})
    execution = _execution(summary)
    assert len(factory.engines) == 1 and len(observed) == 1
    engine = factory.engines[0]
    pw = engine.stage_service.target_post_write_composition
    before = observed[0]
    after = pw.lifecycle_service.budget_preimage(fresh_run)
    budget = engine.budget_runtime(after.run.identity.task_id,fresh_run,require_existing_account=True).canonical_state()
    refusal = execution["metadata"]["budget_wait"]["refusal"]
    identity = {key:refusal[key] for key in ("task_id","run_id","section_id")}
    citation = pw.citation_grounding
    verified = citation.resolve_budget_refusal(receipt_ref=refusal["native_denial_ref"],expected_sha256=refusal["native_denial_sha256"],identity=identity)
    assert citation.normalize_budget_refusal(verified) == citation.normalize_budget_refusal(refusal)
    input_env,native_input = citation.store.get_json_with_reference(refusal["input_ref"],**identity)
    result_env,native_result = citation.store.get_json_with_reference(refusal["native_denial_ref"],**identity)
    proof = citation.gateway.budget_runtime.validate_pre_physical_token_rejection_proof(native_result["failure"]["phase_proof"])
    denial = proof.native_denial
    actual_request = proof.routed_request.model_dump(mode="json")
    old_request = historical["input"]["request"]
    new_parts,old_parts = _parts(actual_request),_parts(old_request)
    assert CanonicalInvocationBudgetAdapter.provider_token_allowance(proof.routed_request) == denial.required
    assert denial.denied_dimension == "model_tokens_total" and denial.required > denial.remaining
    assert denial.baseline_limits.model_tokens_total == denial.effective_limits.model_tokens_total == 24000
    assert denial.required-old_required == sum(new_parts.values())-sum(old_parts.values())+actual_request["max_tokens"]-old_request["max_tokens"]
    native_after = {"run":after.run.model_dump(mode="json"),"children":[row.model_dump(mode="json",round_trip=True) for row in after.current_children],
                    "child_preimage_sha256":after.child_preimage_sha256,"budget":budget.model_dump(mode="json")}
    # This gate first materializes Section validations before the DOCUMENT
    # refusal. Preserve their native activity separately from any replay or
    # physical dispatch of the exact denied DOCUMENT operation.
    reservation_key = lambda row: json.dumps(row["reservation_ref"],sort_keys=True,separators=(",",":"))
    old_reservations = {reservation_key(row):row for row in before["budget"]["reservations"]}
    after_reservations = budget.model_dump(mode="json")["reservations"]
    current_reservations = {reservation_key(row):row for row in after_reservations}
    new_reservations = [row for row in after_reservations if reservation_key(row) not in old_reservations]
    old_operation_ids = {row["request"]["extra"].get("logical_operation_id") for row in physical[:before["physical_count"]]}
    new_gate_physical = physical[before["physical_count"]:]
    denied_operation_id = actual_request["extra"]["logical_operation_id"]
    preliminary_activity = {
        "physical_delta":len(new_gate_physical),
        "new_operation_rows":[{key:row["request"]["extra"].get(key)
            for key in ("logical_operation_id","call_purpose","section_id","attempt_id")}
            for row in new_gate_physical],
        "old_operation_replay_count":sum(row["request"]["extra"].get("logical_operation_id") in old_operation_ids
            for row in new_gate_physical),
        "old_reservations_unchanged":all(current_reservations.get(key)==row for key,row in old_reservations.items()),
        "new_native_reservations":new_reservations,
        "token_delta":budget.used.model_tokens_total-before["budget"]["used"]["model_tokens_total"],
        "new_native_accounted_tokens":sum(row["actual"]["model_tokens_total"] for row in new_reservations),
        "denied_document_operation_id":denied_operation_id,
        "denied_document_physical_count":sum(row["request"]["extra"].get("logical_operation_id")==denied_operation_id
            for row in physical),
        "native_release_budget_revision":proof.release_budget_revision,
        "actual_current_budget_revision":budget.revision,
        "measurement_scope":"Original gate materialization first calls; exact denied DOCUMENT operation never dispatched"}
    numeric = "IDENTICAL" if (denial.required,denial.remaining)==(old_required,old_remaining) else "DIFFERENT_ACTUAL_INPUT_RECORDED"
    receipt = {"schema_version":"bt01_native_three_variant_replay_v1","variant":variant,
        "scope":"ACTUAL_FACTORY_OFFLINE_FAKE_EIGHT_SECTIONS_BASELINE_24000","old_run_id":old_run,"fresh_run_id":fresh_run,
        "identity_utf8_width_preserved":True,"historical_receipt_sha256":HISTORICAL_RECEIPT_SHA256,
        "historical_required":old_required,"historical_remaining":old_remaining,"actual_required":denial.required,"actual_remaining":denial.remaining,
        "numeric_reproduction":numeric,"source_cause":{"owner_formula":"UTF8(prompt/system/messages.role+content) + routed max_tokens",
            "required_delta":denial.required-old_required,"input_utf8_delta":sum(new_parts.values())-sum(old_parts.values()),
            "max_tokens_delta":actual_request["max_tokens"]-old_request["max_tokens"],"old_parts_utf8":old_parts,"actual_parts_utf8":new_parts,
            "used_token_delta":budget.used.model_tokens_total-history["budget_snapshot"]["used"]["model_tokens_total"],
            "exact_request_changes":_changes(old_request,actual_request),
            "exact_material_changes":_changes(historical["input"]["input_material"],native_input["input_material"]),
            "historical_source_hashes":historical_sources,"actual_source_epoch":engine._budget_source_epoch()},
        "native_input_ref":input_env.model_dump(mode="json"),"native_failed_result_ref":result_env.model_dump(mode="json"),
        "native_failed_result":native_result,"before_document_gate":before,"after_hold":native_after,"physical_entries":physical,
        "physical_delta_at_document_gate":len(physical)-before["physical_count"],"preliminary_gate_activity":preliminary_activity,
        "attempts":attempts,"committed":committed,"execution":execution,
        "historical_error_source_compatibility":str(old_error),"historical_bytes_before":preserved_before,
        "status":"OBSERVED_NATIVE_FACTS_PENDING_ASSERTIONS"}
    _json_once(tmp_path/"bt01-native-replay.json",receipt)
    assert execution["status"] == "paused" and execution["error"] is None
    assert execution["metadata"]["pause_purpose"] == "BUDGET_AUTHORIZATION" and not execution["metadata"]["workflow_complete"]
    assert execution["metadata"]["human_review_required"] is False
    assert refusal.get("operation_kind","CITATION_ASSOCIATION") == "CITATION_ASSOCIATION"
    assert after.run.status.value == "paused" and after.run.finished_at is None
    assert before["run"]["status"] == "running" and before["run"]["finished_at"] is None
    assert after.run.lifecycle_revision == before["run"]["lifecycle_revision"]+1
    assert native_after["children"] == before["children"] and len(after.current_children) == 8
    assert native_after["child_preimage_sha256"] == before["child_preimage_sha256"]
    assert all(row.status.value == "running" for row in after.current_children)
    assert budget.limits.model_tokens_total == 24000 and not budget.grant_history
    assert len(old_reservations) == len(before["budget"]["reservations"])
    assert len(current_reservations) == len(after_reservations)
    assert preliminary_activity["old_reservations_unchanged"]
    assert preliminary_activity["old_operation_replay_count"] == 0
    new_operation_ids = [row["request"]["extra"].get("logical_operation_id") for row in new_gate_physical]
    assert all(new_operation_ids) and len(new_operation_ids) == len(set(new_operation_ids))
    assert preliminary_activity["token_delta"] == preliminary_activity["new_native_accounted_tokens"]
    assert budget.active_reserved.model_tokens_total == 0
    assert budget.used.model_tokens_total == denial.effective_limits.model_tokens_total-denial.remaining
    assert budget.revision == proof.release_budget_revision
    assert preliminary_activity["denied_document_physical_count"] == 0
    assert denied_operation_id == refusal["semantic_operation_ref"]
    assert all(row["model_name"] == "fake_llm" for row in physical)
    assert all(row["request"]["extra"].get("logical_operation_id") != refusal["semantic_operation_ref"] for row in physical)
    assert "evaluate_document_gate" not in execution["completed_node_ids"]
    assert "finalize_workflow_failure" not in execution["metadata"]["business_node_sequence"]
    assert after.run.run_completion_assessment_ref is None
    assert native_result["failure"]["native_denial"] == denial.model_dump(mode="json")
    assert proof.physical_dispatch_started is False and proof.gateway_call_closed is True
    if variant == "late_side_effect":
        assert committed == ["committed"] and engine.wait_for_idle(timeout_seconds=1.0)
    elif variant == "retry_exactly_once":
        assert attempts == [1,2] and committed == [f"commit:{fresh_run}"]
        plan = next(row for row in execution["node_outputs"] if row["node_id"] == "plan_document")
        assert plan["metadata"]["attempt_count"] == 2 and plan["metadata"]["retry_count"] == 1
        assert [row["retry"] for row in plan["metadata"]["attempt_history"]] == [True,False]
    for path,expected in preserved_before.items():
        assert _sha(Path(path).read_bytes()) == expected
    assert _sha(ORIGINAL_FIXTURE.read_bytes()) == ORIGINAL_FIXTURE_SHA256
    _json_once(tmp_path/"bt01-native-replay-verdict.json",{"schema_version":"bt01_native_replay_verdict_v1","variant":variant,
        "native_behavior":"PAUSED_EIGHT_CURRENT_CHILDREN_UNCHANGED_DENIED_DOCUMENT_OPERATION_PHYSICAL_ZERO",
        "historical_source_errors":"SOURCE_COMPATIBLE_PRESERVED_NO_NEW_PROOF","numeric_reproduction":numeric,
        "native_receipt_sha256":_sha((tmp_path/"bt01-native-replay.json").read_bytes()),
        "status":"LOCAL_IMPLEMENTATION_VALIDATION_PENDING_INDEPENDENT_REAUDIT"})
