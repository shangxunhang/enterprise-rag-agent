"""Native Windows process cuts; OWNERCONTROL_NOT_FULLGRAPH verification draft.

Uses the existing pending Support native fixture, existing-only cold owners and
the same finite 24000 baseline. This is not Graph/Task/checkpoint evidence.
"""
from pathlib import Path
import hashlib
import importlib.abc
import importlib.machinery
import json
import os
import socket
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[2]
if __name__ == "__main__":
    assert ROOT == Path(r"D:\MyCode\rag-agent-shared")
    sys.dont_write_bytecode = True
    sys.path[:] = [p for p in sys.path if "rag-agent" not in str(p).lower() or "site-packages" in str(p).lower()]
    sys.path[:0] = [str(ROOT / "backend"), str(ROOT / "backend/tests"), str(ROOT)]
    names = {p.name for p in (ROOT / "backend").iterdir() if p.is_dir()} | {"backend"}
    class SourceGuard(importlib.abc.MetaPathFinder):
        def find_spec(self, fullname, path=None, target=None):
            if fullname.split(".")[0] not in names:
                return None
            spec = importlib.machinery.PathFinder.find_spec(fullname, path)
            if spec is not None:
                locations = list(spec.submodule_search_locations or ())
                if spec.origin not in (None, "built-in", "frozen"):
                    locations.append(spec.origin)
                if any(not Path(p).resolve().is_relative_to(ROOT) for p in locations):
                    raise ImportError("Foreign Support owner source: " + fullname + " " + repr(locations))
            return spec
    sys.meta_path.insert(0, SourceGuard())
    def deny_network(*_args, **_kwargs):
        raise RuntimeError("Native Support process-cut network is not authorized")
    socket.create_connection = socket.socket.connect = socket.socket.connect_ex = deny_network

import pytest
from apps.enterprise_document.schemas.agent_rag_v1.claim_support import ClaimSupportSubjectRefSchema, EvidenceTargetSetRefSchema
from apps.enterprise_document.services.agent_rag_v1.claim_support_composition import TargetClaimSupportError, SUPPORT_SEMANTIC_CONTRACT
from local_verifier_recovery_helpers import reopen_fixture
from test_fpbca_budget_document_support import _fresh, _refuse, _authorize, _invoke

SOURCE_PATHS = (
    "backend/core/persistence/interprocess_lock.py",
    "backend/apps/enterprise_document/services/agent_rag_v1/claim_support_composition.py",
    "backend/apps/enterprise_document/services/agent_rag_v1/claim_support_runtime.py",
    "backend/apps/enterprise_document/services/agent_rag_v1/model_tool_budget_runtime.py",
    "backend/model_gateway/model_gateway.py",
    "backend/apps/enterprise_document/services/agent_rag_v1/budget_manager.py",
    "backend/apps/enterprise_document/services/agent_rag_v1/lifecycle_runtime.py",
    "backend/apps/enterprise_document/services/agent_rag_v1/claim_verification_runtime.py",
    "backend/apps/enterprise_document/services/agent_rag_v1/post_write_citation_grounding.py",
    "backend/apps/enterprise_document/services/agent_rag_v1/post_write_citation_projection.py",
    "backend/apps/enterprise_document/services/post_write/shared_claim_support.py",
    "backend/apps/enterprise_document/schemas/agent_rag_v1/budgets.py",
    "backend/apps/enterprise_document/schemas/agent_rag_v1/lifecycle.py",
    "backend/apps/enterprise_document/schemas/agent_rag_v1/claim_support.py",
    "backend/tests/local_verifier_recovery_helpers.py",
    "backend/tests/test_fpbca_budget_document_support.py",
    "backend/tests/test_fpbca_budget_document_support_native_process_cuts.py",
)

def _json_once(path, value):
    with path.open("x", encoding="utf-8") as stream:
        json.dump(value, stream, ensure_ascii=False, indent=2)
        stream.flush()
        os.fsync(stream.fileno())

def _physical(root, request, model_name):
    with (root / "native-support-invoker-entry.jsonl").open("a", encoding="utf-8") as stream:
        stream.write(json.dumps({"pid":os.getpid(), "model_name":model_name,
            "request":request.model_dump(mode="json")}, ensure_ascii=False) + "\n")
        stream.flush()
        os.fsync(stream.fileno())

def _entries(root):
    path = root / "native-support-invoker-entry.jsonl"
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()] if path.exists() else []

def _open_case(root):
    case = json.loads((root / "support-process-case.json").read_text(encoding="utf-8"))
    for path, expected in case["source_epoch"].items():
        assert hashlib.sha256((ROOT/path).read_bytes()).hexdigest() == expected
    fx = reopen_fixture(root, script=case["script"])
    fx.support_composition.model_gateway = fx.gateway
    fx.subject = ClaimSupportSubjectRefSchema.model_validate(case["subject"])
    fx.target = EvidenceTargetSetRefSchema.model_validate(case["target"])
    fx.identity, fx.document_context, fx.semantic_key = case["identity"], case["document_context"], case["semantic_key"]
    fx.input_ref, fx.result_ref = case["input_ref"], case["result_ref"]
    fx.context = fx.context.model_copy(update={"active_lineage":fx.lifecycle.resolve_active_lineage(
        fx.identity["run_id"], "section:"+fx.identity["section_id"])}, deep=True)
    return fx, case

def _support_events(fx):
    rows = [json.loads(line) for line in (fx.root/"claim_support.jsonl").read_text(encoding="utf-8").splitlines()]
    return [row for row in rows if row.get("event_type") == "support_committed"
        and row["record"]["evidence_target_set_ref"] == fx.target.model_dump(mode="json")]

def _worker(root, cut):
    fx, case = _open_case(root)
    owner = fx.support_composition
    put, reserve, invoke, append = owner.store.put_json, fx.budget.reserve, fx.gateway.invoker.invoke, owner.runtime.log.append_serialized
    def exit_at(boundary):
        native = fx.lifecycle.budget_preimage(fx.identity["run_id"])
        _json_once(root/"actual-support-process-cut.json", {"scope":"OWNERCONTROL_NOT_FULLGRAPH",
            "pid":os.getpid(), "cut":boundary, "budget":fx.budget.canonical_state().model_dump(mode="json"),
            "run":native.run.model_dump(mode="json"),
            "children":[row.model_dump(mode="json",round_trip=True) for row in native.current_children],
            "child_preimage_sha256":native.child_preimage_sha256, "physical":_entries(root),
            "support_events":_support_events(fx)})
        os._exit(73)
    def write(value, **kwargs):
        reference = put(value, **kwargs)
        ref = kwargs.get("artifact_ref", "")
        if "/admission/" in ref and ref.endswith("/"+cut):
            exit_at(cut)
        return reference
    def reservation(reference, *args, **kwargs):
        reservation_id = reserve(reference, *args, **kwargs)
        if cut == "token_reserved" and reference.reservation_kind == "model_provider_attempt_tokens":
            exit_at(cut)
        return reservation_id
    def measured(request, model_name):
        _physical(root, request, model_name)
        if cut == "invoker_entered":
            exit_at(cut)
        return invoke(request, model_name)
    def commit_line(line):
        result = append(line)
        event = json.loads(line)
        if cut == "support_committed" and event.get("event_type") == "support_committed" and event["record"]["evidence_target_set_ref"] == fx.target.model_dump(mode="json"):
            exit_at(cut)
        return result
    owner.store.put_json, fx.budget.reserve, fx.gateway.invoker.invoke = write, reservation, measured
    owner.runtime.log.append_serialized = commit_line
    candidate = _invoke(fx, case["binding"])
    owner.runtime.commit_support(candidate, fx.context)
    raise AssertionError("Selected actual Support process boundary was not reached")

@pytest.mark.parametrize("cut", ["ack", "input", "token_reserved", "invoker_entered", "candidate", "support_committed"])
def test_native_support_process_exit_never_duplicates_dispatch_or_settlement(tmp_path, cut):
    fx = _fresh(tmp_path); refusal = _refuse(fx); binding = _authorize(fx, refusal)
    original_input = fx.support_composition.store.get_bytes(fx.input_ref, **fx.identity)
    original_result = fx.support_composition.store.get_bytes(fx.result_ref, **fx.identity)
    _json_once(tmp_path/"support-process-case.json", {"scope":"OWNERCONTROL_NOT_FULLGRAPH",
        "subject":fx.subject.model_dump(mode="json"), "target":fx.target.model_dump(mode="json"),
        "identity":fx.identity, "document_context":fx.document_context, "semantic_key":fx.semantic_key,
        "input_ref":fx.input_ref, "result_ref":fx.result_ref, "refusal":refusal,
        "binding":binding, "script":list(fx.client.script),
        "source_epoch":{name:hashlib.sha256((ROOT/name).read_bytes()).hexdigest() for name in SOURCE_PATHS}})
    try:
        child = subprocess.run([sys.executable,"-I","-B",str(Path(__file__).resolve()),"worker",str(tmp_path),cut],
            capture_output=True,text=True,timeout=40)
    except subprocess.TimeoutExpired as error:
        # subprocess.run stops and waits for only this exact fresh test child.
        # Exit/lock release alone supplies no no-effect or production authority.
        hashes = {str(path.relative_to(tmp_path)):hashlib.sha256(path.read_bytes()).hexdigest()
            for path in sorted(tmp_path.rglob("*")) if path.is_file() and not path.name.endswith(".lock")}
        _json_once(tmp_path/"actual-support-test-time-budget.json", {
            "scope":"OWNERCONTROL_NOT_FULLGRAPH", "cut":cut,
            "status":"TEST_TIME_BUDGET_EXHAUSTED", "campaign_status":"E2E_INCOMPLETE",
            "production_failure":False, "canonical_mutation_by_harness":False,
            "native_effects":"UNKNOWN", "source_epoch":json.loads((tmp_path/"support-process-case.json").read_text(encoding="utf-8"))["source_epoch"],
            "physical_entry_journal":"native-support-invoker-entry.jsonl (opaque on timeout; UNKNOWN)", "raw_fresh_owner_file_sha256":hashes,
            "stdout_timeout_diagnostic_repr":repr(error.stdout), "stderr_timeout_diagnostic_repr":repr(error.stderr),
            "sqlite_snapshot":"FILE_HASHES_ONLY_NO_CROSS_FILE_TRANSACTION_OR_REPAIR"})
        pytest.fail("TEST_TIME_BUDGET_EXHAUSTED / E2E_INCOMPLETE; production native effects UNKNOWN")
    _json_once(tmp_path/"actual-support-process-exit.json", {"returncode":child.returncode,
        "stdout":child.stdout,"stderr":child.stderr,"positive_process_exit":child.returncode == 73})
    assert child.returncode == 73, child.stderr
    cold, case = _open_case(tmp_path); owner = cold.support_composition
    before = cold.budget.canonical_state()
    slot = owner.read_budget_document_admission(binding, identity=cold.identity)
    native_cut = json.loads((tmp_path/"actual-support-process-cut.json").read_text(encoding="utf-8"))
    entries_before = _entries(tmp_path)
    assert len(entries_before) == (1 if cut in {"invoker_entered","candidate","support_committed"} else 0)
    invoke = cold.gateway.invoker.invoke
    def measured(request, model_name):
        _physical(tmp_path, request, model_name)
        return invoke(request, model_name)
    cold.gateway.invoker.invoke = measured
    if cut in {"input","token_reserved","invoker_entered"}:
        assert slot["phase"] == "INDETERMINATE"
        with pytest.raises(TargetClaimSupportError, match="intent exists without result"):
            _invoke(cold,binding)
        assert cold.budget.canonical_state() == before and _entries(tmp_path) == entries_before
        assert _support_events(cold) == []
        certainty = None
    else:
        assert slot["phase"] == ("PRE_CITATION_INTENT_SAFE" if cut == "ack" else "COMPLETED")
        candidate = _invoke(cold,binding)
        record = owner.runtime.commit_support(candidate,cold.context)
        settled = cold.budget.canonical_state()
        certainty = owner.resolve_certainty_observation(support_record=record,operation_context=cold.context)
        assert certainty == owner.resolve_certainty_observation_from_validated_snapshot(
            support_record=record,claim_record=cold.claim,active_lineage=cold.context.active_lineage,task_id=cold.identity["task_id"])
        assert _invoke(cold,binding) == candidate
        assert owner.runtime.commit_support(candidate,cold.context) == record
        assert len(_entries(tmp_path)) == 1 and cold.budget.canonical_state() == settled
        assert len(_support_events(cold)) == 1
        if cut != "ack":
            assert settled == before
    native = cold.lifecycle.budget_preimage(cold.identity["run_id"])
    _json_once(tmp_path/"native-support-process-observation.json", {"scope":"OWNERCONTROL_NOT_FULLGRAPH",
        "cut":cut,"actual_process_cut":native_cut,"native_slot":slot,
        "budget_before_cold":before.model_dump(mode="json"),"budget_after_cold":cold.budget.canonical_state().model_dump(mode="json"),
        "physical":_entries(tmp_path),"support_events":_support_events(cold),
        "certainty":certainty.model_dump(mode="json") if certainty else None,
        "run":native.run.model_dump(mode="json"),"children":[row.model_dump(mode="json",round_trip=True) for row in native.current_children],
        "original_input_unchanged":owner.store.get_bytes(cold.input_ref,**cold.identity) == original_input,
        "original_failure_unchanged":owner.store.get_bytes(cold.result_ref,**cold.identity) == original_result})
    assert owner.store.get_bytes(cold.input_ref,**cold.identity) == original_input
    assert owner.store.get_bytes(cold.result_ref,**cold.identity) == original_result
    assert cold.budget.canonical_state().limits.model_tokens_total == 24000

if __name__ == "__main__":
    assert sys.argv[1] == "worker"
    _worker(Path(sys.argv[2]),sys.argv[3])
