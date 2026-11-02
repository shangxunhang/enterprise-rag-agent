"""Native Windows process cuts at Citation admission; owner scope, not full Graph.

The new fixture keeps its baseline 24000 and explicit immutable top-up. Its
Context package is the deliberately controlled owner fixture, so this evidence
does not substitute for native completed Section-source adoption acceptance.
"""
from pathlib import Path
from copy import deepcopy
from types import SimpleNamespace
from threading import RLock
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
    names = {p.name for p in (ROOT / "backend").iterdir() if p.is_dir()}
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
                    raise ImportError("Foreign native owner source: " + fullname + " " + repr(locations))
            return spec
    sys.meta_path.insert(0, SourceGuard())
    def deny_network(*_a, **_kw):
        raise RuntimeError("Native process-cut network is not authorized")
    socket.create_connection = socket.socket.connect = socket.socket.connect_ex = deny_network

import pytest
from apps.enterprise_document.services.agent_rag_v1.artifact_store import FileArtifactStore
from apps.enterprise_document.services.agent_rag_v1.lifecycle_runtime import RunAttemptLifecycleService
from apps.enterprise_document.services.agent_rag_v1.post_write_citation_grounding import TargetPostWriteCitationGrounding, CitationGroundingError
from core.hashing import canonical_sha256
from test_fpbca_budget_document_admission import _fixture, _authorize
from test_fpbca_budget_document_phase_proof import _manager, _gateway

def _json_once(path, value):
    with path.open("x", encoding="utf-8") as stream:
        json.dump(value, stream, ensure_ascii=False, indent=2)

def _physical(root, request):
    with (root / "native-invoker-entry.jsonl").open("a", encoding="utf-8") as stream:
        stream.write(json.dumps({"pid":os.getpid(), "request":request.model_dump(mode="json")}, ensure_ascii=False) + "\n")
        stream.flush()
        os.fsync(stream.fileno())

def _cold(root):
    case = json.loads((root / "case.json").read_text(encoding="utf-8"))
    budget = _manager(root)
    life = RunAttemptLifecycleService(root / "lifecycle.jsonl")
    gateway, clients, _ = _gateway(budget)
    old_generate = clients[0].generate
    def reply(request):
        response = old_generate(request)
        return response.model_copy(update={"content":json.dumps({"associations":[{"occurrence_ref":"occurrence://local", "state":"ASSOCIATED", "associated_claim_target_ids":["claim-local"]}]})})
    clients[0].generate = reply
    invoke = gateway.invoker.invoke
    def measured(request, model_name):
        _physical(root, request)
        return invoke(request, model_name)
    gateway.invoker.invoke = measured
    owner = object.__new__(TargetPostWriteCitationGrounding)
    owner.store = FileArtifactStore(root / "artifacts")
    owner._lock = RLock()
    owner.gateway = gateway
    owner.post_write = SimpleNamespace(budget_manager_provider=lambda *_:budget, lifecycle_service=life)
    owner.claim_projection = SimpleNamespace(claim_runtime=SimpleNamespace(lifecycle_service=life))
    def package(material, lineage, occurrence):
        raw = {"context":deepcopy(case["context"]), "model_input":{"final_input_material":{"inline_final_input_payload":{"messages":[{"role":"user", "content":"Exact unchanged DOCUMENT source"}]}}, "final_model_input_sha256":canonical_sha256("unchanged-messages")}}
        return SimpleNamespace(model_dump=lambda **_:deepcopy(raw), model_input=SimpleNamespace(final_input_material=SimpleNamespace(inline_final_input_payload=raw["model_input"]["final_input_material"]["inline_final_input_payload"]), final_model_input_sha256=raw["model_input"]["final_model_input_sha256"]), package_ref=SimpleNamespace(context_package_id=occurrence)),8192
    owner._package = package
    return owner, budget, life, case

def _worker(root, cut):
    owner, budget, life, case = _cold(root)
    put = owner.store.put_json
    reserve = budget.reserve
    invoke = owner.gateway.invoker.invoke
    def exit_at(boundary):
        _json_once(root / "actual-process-cut.json", {"pid":os.getpid(), "cut":boundary, "budget":budget.canonical_state().model_dump(mode="json"), "children":[r.model_dump(mode="json") for r in life.budget_preimage(budget.run_id).current_children]})
        os._exit(71)
    def write(value, **kwargs):
        result = put(value, **kwargs)
        reference = kwargs.get("artifact_ref", "")
        if "/admission/" in reference and reference.endswith("/" + cut):
            exit_at(cut)
        return result
    def reserve_cut(reference, *args, **kwargs):
        result = reserve(reference, *args, **kwargs)
        if cut == "token_reserved" and reference.reservation_kind == "model_provider_attempt_tokens":
            exit_at(cut)
        return result
    def invoke_cut(request, model_name):
        if cut == "invoker_entered":
            _physical(root, request)
            exit_at(cut)
        return invoke(request, model_name)
    owner.store.put_json = write
    budget.reserve = reserve_cut
    owner.gateway.invoker.invoke = invoke_cut
    lineage = life.resolve_active_lineage(budget.run_id, "section:s0")
    owner._associate(case["material"], case["identity"], lineage, lambda:None, budget_continuation=case["binding"])
    raise AssertionError("Actual selected native boundary was not reached")

@pytest.mark.parametrize("cut", ["ack", "input", "token_reserved", "invoker_entered", "result"])
def test_native_process_exit_never_duplicates_citation_physical_or_settlement(tmp_path, cut):
    state = _fixture(tmp_path)
    binding = _authorize(state)
    owner, budget, life, _, identity, _, material, refusal, context = state
    original = owner.store.get_bytes(refusal["native_denial_ref"], **identity)
    _json_once(tmp_path / "case.json", {"identity":identity, "material":material, "refusal":refusal, "context":context, "binding":binding})
    child = subprocess.run([sys.executable, "-I", "-B", str(Path(__file__).resolve()), "worker", str(tmp_path), cut], capture_output=True, text=True, timeout=40)
    _json_once(tmp_path / "actual-process-exit.json", {"returncode":child.returncode, "stdout":child.stdout, "stderr":child.stderr, "positive_process_exit":child.returncode == 71})
    assert child.returncode == 71, child.stderr
    reopened, manager, lifecycle, case = _cold(tmp_path)
    before = manager.canonical_state()
    native_slot = reopened.read_budget_document_admission(binding, identity=identity)
    entries = lambda: (tmp_path / "native-invoker-entry.jsonl").read_text(encoding="utf-8").splitlines() if (tmp_path / "native-invoker-entry.jsonl").exists() else []
    before_physical = len(entries())
    assert before_physical == (1 if cut in {"invoker_entered", "result"} else 0)
    lineage = lifecycle.resolve_active_lineage(manager.run_id, "section:s0")
    if cut in {"input", "token_reserved", "invoker_entered"}:
        assert native_slot["phase"] == "INDETERMINATE"
        with pytest.raises(CitationGroundingError, match="uncertain"):
            reopened._associate(material, identity, lineage, lambda:None, budget_continuation=binding)
        assert manager.canonical_state() == before and len(entries()) == before_physical
    else:
        assert native_slot["phase"] == ("PRE_CITATION_INTENT_SAFE" if cut == "ack" else "COMPLETED")
        reopened._associate(material, identity, lineage, lambda:None, budget_continuation=binding)
        settled = manager.canonical_state()
        reopened._associate(material, identity, lineage, lambda:None, budget_continuation=binding)
        assert manager.canonical_state() == settled and len(entries()) == 1
        if cut == "result":
            assert settled == before
    _json_once(tmp_path / "native-process-cut-observation.json", {"scope":"ACTUAL_WINDOWS_PROCESS_CUT_NATIVE_CITATION_BUDGET_LIFECYCLE_OWNER_NOT_GRAPH", "cut":cut, "slot":native_slot, "budget_before_cold":before.model_dump(mode="json"), "budget_after_cold":manager.canonical_state().model_dump(mode="json"), "physical_entries":entries(), "native_children":[r.model_dump(mode="json") for r in lifecycle.budget_preimage(manager.run_id).current_children], "original_refusal_unchanged":reopened.store.get_bytes(refusal["native_denial_ref"], **identity) == original})
    assert reopened.store.get_bytes(refusal["native_denial_ref"], **identity) == original
    assert manager.canonical_state().limits.model_tokens_total == 24000

if __name__ == "__main__":
    assert sys.argv[1] == "worker"
    _worker(Path(sys.argv[2]), sys.argv[3])
