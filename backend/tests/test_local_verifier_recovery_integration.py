"""A–J acceptance against actual durable production owners and a Fake Provider.

Fault wrappers interrupt original operations; they never replace authorizations,
source currentness decisions, provenance guards or canonical commits.
"""
from __future__ import annotations

import base64
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from dataclasses import fields, is_dataclass
import hashlib
import json
import multiprocessing
import os
from pathlib import Path
import traceback

import pytest

from core.hashing import canonical_sha256
from apps.enterprise_document.schemas.agent_rag_v1.verification import (
    ClaimBindingFactsRecordSchema, VerifierClaimInputSchema, VerifierSemanticResultCandidateSchema,
    build_verifier_original_root, VERIFIER_LOCAL_CORRECTION_KEY, VERIFIER_LOCAL_REGISTRATION_KEY,
)
from apps.enterprise_document.schemas.agent_rag_v1.claim_support import ClaimEvidenceSupportRecordSchema
from apps.enterprise_document.services.agent_rag_v1.claim_verification_runtime import ClaimVerificationRuntime, ClaimBindingError, ClaimHeadConflict
from apps.enterprise_document.services.agent_rag_v1.claim_verifier_composition import TargetClaimVerifierComposition
from backend.tests.local_verifier_recovery_helpers import (
    create_fixture, reopen_fixture, legal_reply, provider_rows, verify,
)
from backend.tests.historical_fixture_locator import resolve_attempt4_fixture


@pytest.fixture(autouse=True)
def _retain_exact_node_evidence_identity(request, tmp_path):
    # New acceptance evidence only: this binds every collected node to its
    # actual isolated directory and process, including read-only history nodes.
    (tmp_path / "node-identity.json").write_text(json.dumps({
        "nodeid": request.node.nodeid, "pid": os.getpid(),
        "fixture_root": str(tmp_path)}, sort_keys=True) + "\n", encoding="utf-8")


def _rows(path):
    return [json.loads(row) for row in Path(path).read_text(encoding="utf-8").splitlines()]


def _events(fx, kind):
    return [row for row in _rows(fx.runtime.ledger_path) if row["event_type"] == kind]


def _artifacts(fx, kind):
    refs = [ref for ref in fx.composer.store.list_references() if ref.artifact_type == kind]
    return [(ref, fx.composer.store.get_json(ref.artifact_ref, task_id=ref.task_id, run_id=ref.run_id)) for ref in refs]


def _illegal_reply():
    return legal_reply("VERIFIED", ["@all_evidence"])


def _snapshot(fx):
    return {"m6": fx.runtime.ledger_path.read_bytes(), "budget": (fx.root / "budget.jsonl").read_bytes(),
        "artifacts": {ref.artifact_ref: ref.content_hash for ref in fx.composer.store.list_references()},
        "provider": len(provider_rows(fx))}


def _dump_record(value):
    if hasattr(value, "model_dump"):
        return value.model_dump(mode="json")
    if is_dataclass(value):
        return {field.name: _dump_record(getattr(value, field.name)) for field in fields(value)}
    if isinstance(value, (tuple, list)):
        return [_dump_record(item) for item in value]
    return value


def _retain_actual_refusal(fx, error, *, domain, stage, kind):
    chain, current, seen = [], error, set()
    while current is not None and id(current) not in seen:
        seen.add(id(current))
        chain.append({"exception_type": type(current).__name__, "message": str(current),
            "error_code": getattr(current, "error_code", None),
            "frames": [{"file": frame.filename, "line": frame.lineno, "function": frame.name}
                for frame in traceback.extract_tb(current.__traceback__)]})
        current = current.__cause__
    (fx.root / "actual-owner-refusal.json").write_text(json.dumps({
        "domain": domain, "stage": stage, "injection_kind": kind,
        "native_injection_completed": True, "actual_exception_chain": chain,
        "provider_calls": len(provider_rows(fx)),
        "formal_commits": len(_events(fx, "verification_committed")),
        "claim_ledger_sha256": hashlib.sha256(fx.runtime.ledger_path.read_bytes()).hexdigest()},
        ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _history():
    root = resolve_attempt4_fixture()
    hashes = {str(path): hashlib.sha256(path.read_bytes()).hexdigest() for path in root.iterdir() if path.is_file()}
    def envelope(name):
        raw = json.loads((root / name).read_bytes())
        content = base64.b64decode(raw["content_base64"], validate=True)
        assert raw["reference"]["content_hash"] == "sha256:" + hashlib.sha256(content).hexdigest()
        return json.loads(content)
    original_input = envelope("input_exact_artifact_envelope.json")
    original_candidate = envelope("candidate_exact_artifact_envelope.json")
    invocation = VerifierClaimInputSchema.model_validate(original_input["input_material"]["invocation"])
    candidate = VerifierSemanticResultCandidateSchema.model_validate(original_candidate["candidate"])
    binding = ClaimBindingFactsRecordSchema.model_validate(json.loads((root / "m6_binding_facts_exact_event.jsonl").read_bytes())["record"])
    support = ClaimEvidenceSupportRecordSchema.model_validate(json.loads((root / "shared_support_exact_event.jsonl").read_bytes())["record"])
    return root, hashes, invocation, candidate, binding, support, original_candidate


def test_A_attempt4_original_members_still_rejected_readonly():
    root, hashes, invocation, candidate, binding, support, receipt = _history()
    parsed, _ = TargetClaimVerifierComposition.parse_candidate(receipt["reply_entry"]["raw_reply"],
        invocation=invocation, occurrence=candidate.verification_occurrence_ref, receipt_ref=candidate.semantic_rationale_ref)
    assert parsed == candidate
    assert len(candidate.positive_basis_refs) == 2
    with pytest.raises(ClaimBindingError) as error:
        ClaimVerificationRuntime.validate_v2_provenance(binding, support, candidate)
    assert getattr(error.value, "rule_code", None) == "EVIDENCE_POSITIVE_BASIS_NOT_ADMITTED"
    assert {str(path): hashlib.sha256(path.read_bytes()).hexdigest() for path in root.iterdir() if path.is_file()} == hashes


def test_negative_historical_attempt4_root_is_not_prospectively_registered():
    _, _, invocation, _, _, _, _ = _history()
    assert VERIFIER_LOCAL_REGISTRATION_KEY not in invocation.deterministic_validation_facts
    with pytest.raises(ValueError):
        build_verifier_original_root(invocation)


def test_B_C_J_full_parent_chain_single_commit_native_budget_and_exact_replay(tmp_path):
    fx = create_fixture(tmp_path, [_illegal_reply(), legal_reply("VERIFIED", ["@supporting"])])
    before_budget = fx.budget.canonical_state()
    committed = verify(fx)
    assert committed[0].verification_result.verdict.value == "VERIFIED"
    assert committed[0].verification_revision.verification_revision == 1
    assert committed[0].resulting_claim_head.head_revision == 1
    assert len(_events(fx, "verification_committed")) == 1
    rejected, grants = _events(fx, "verifier_candidate_rejected"), _events(fx, "verifier_correction_authorized")
    assert len(rejected) == len(grants) == 1
    assert _events(fx, "verifier_correction_terminated") == []
    grant, rejection = grants[0]["payload"], rejected[0]["payload"]
    assert grant["root"] == rejection["root"]
    assert grant["rejection_ref"] == rejection["rejection_ref"]
    assert grant["rejection_sha256"] == rejection["rejection_sha256"]
    assert grant["correction_ordinal"] == 1
    assert grant["feedback_sha256"] == hashlib.sha256(grant["feedback"].encode()).hexdigest()
    inputs = _artifacts(fx, "canonical_verifier_input")
    requests = _artifacts(fx, "canonical_verifier_request")
    responses = _artifacts(fx, "canonical_verifier_response")
    candidates = _artifacts(fx, "canonical_verifier_candidate")
    assert len(inputs) == len(requests) == len(responses) == len(candidates) == 2
    initial = next(raw for _, raw in inputs if VERIFIER_LOCAL_REGISTRATION_KEY in raw["input_material"]["invocation"]["deterministic_validation_facts"])
    child = next(raw for _, raw in inputs if VERIFIER_LOCAL_CORRECTION_KEY in raw["input_material"]["invocation"]["deterministic_validation_facts"])
    binding = child["input_material"]["invocation"]["deterministic_validation_facts"][VERIFIER_LOCAL_CORRECTION_KEY]
    assert binding["authorization"] == grant
    assert child["input_fingerprint"] != initial["input_fingerprint"]
    def strings(value):
        if isinstance(value, str):
            yield value
        elif isinstance(value, dict):
            for item in value.values():
                yield from strings(item)
        elif isinstance(value, list):
            for item in value:
                yield from strings(item)
    context_escaped_feedback = json.dumps(grant["feedback"], ensure_ascii=False)[1:-1]
    assert any(grant["feedback"] in value or context_escaped_feedback in value for value in strings(child["context_package"]))
    assert len(next(raw for _, raw in candidates if raw["candidate"]["verifier_semantic_input_sha256"] == initial["input_fingerprint"])["candidate"]["positive_basis_refs"]) == 2
    calls = provider_rows(fx)
    assert len(calls) == 2
    assert len({row["request"]["model_call_id"] for row in calls}) == 2
    assert calls[1]["request"]["extra"]["verifier_correction_ordinal"] == 1
    assert calls[1]["request"]["extra"]["verifier_original_root_ref"] == grant["root"]["original_root_ref"]
    persisted_requests = {_request_from_receipt(raw).model_call_id: (ref, _request_from_receipt(raw).model_dump(mode="json"))
        for ref, raw in requests}
    wire_receipts = []
    for row in calls:
        wire = row["request"]
        ref, persisted = persisted_requests[wire["model_call_id"]]
        for field in ("messages", "prompt", "system_prompt", "task_id", "run_id", "model_call_id"):
            assert wire[field] == persisted[field]
        assert 0 < wire["max_tokens"] <= persisted["max_tokens"]
        assert wire["extra"]["model_request_fingerprint"]
        assert wire["extra"]["provider_attempt_id"]
        assert wire["extra"]["provider_attempt_ordinal"] == 1
        wire_receipts.append({"request_ref": ref.artifact_ref, "model_call_id": wire["model_call_id"],
            "provider_attempt_id": wire["extra"]["provider_attempt_id"],
            "provider_attempt_ordinal": wire["extra"]["provider_attempt_ordinal"],
            "recorded_cap": persisted["max_tokens"], "effective_wire_cap": wire["max_tokens"]})
    assert len({item["provider_attempt_id"] for item in wire_receipts}) == 2
    escaped_feedback = json.dumps(grant["feedback"], ensure_ascii=False)[1:-1]
    assert any(grant["feedback"] in message["content"] or escaped_feedback in message["content"]
        for message in calls[1]["request"]["messages"])
    state = fx.budget.canonical_state()
    assert state.used.logical_model_calls - before_budget.used.logical_model_calls == 2
    assert state.used.model_tokens_total - before_budget.used.model_tokens_total == 46
    tokens = [item for item in state.reservations if item.reservation_ref.reservation_kind == "model_provider_attempt_tokens"]
    assert len(tokens) == 2 and all(item.status == "consumed" for item in tokens)
    for index, item in enumerate(wire_receipts):
        billed = next(token for token in tokens if token.reservation_ref.owner_occurrence_ref == item["provider_attempt_id"])
        assert billed.reservation_ref.operation_ref == item["model_call_id"]
        assert billed.actual.model_tokens_total == 22 + index * 2
        item.update(reservation_id=billed.reservation_ref.reservation_id, actual_tokens=billed.actual.model_tokens_total)
    (fx.root / "actual-wire-budget-linkage.json").write_text(json.dumps(wire_receipts, ensure_ascii=False,
        indent=2, sort_keys=True) + "\n", encoding="utf-8")
    # Reopen every physical owner before measuring healthy replay; any normal
    # SQLite reopen bookkeeping is not attributed to semantic replay.
    reopened = reopen_fixture(tmp_path)
    before = _snapshot(reopened)
    assert verify(reopened) == committed
    assert _snapshot(reopened) == before


@pytest.mark.parametrize("verdict", ["UNSUPPORTED", "CONFLICTED", "REJECTED"])
@pytest.mark.parametrize("initial", [True, False], ids=["initial", "correction"])
def test_E_legal_negative_commits_normally_without_retry(tmp_path, verdict, initial):
    fx = create_fixture(tmp_path, ([legal_reply(verdict)] if initial else [_illegal_reply(), legal_reply(verdict)]))
    committed = verify(fx)
    assert committed[0].verification_result.verdict.value == verdict
    assert len(provider_rows(fx)) == (1 if initial else 2)
    assert len(_events(fx, "verifier_correction_authorized")) == (0 if initial else 1)
    assert len(_events(fx, "verification_committed")) == 1
    before = _snapshot(fx)
    assert verify(reopen_fixture(tmp_path)) == committed
    assert _snapshot(fx) == before


def test_D_second_violation_permanent_termination_restart_and_concurrency(tmp_path):
    fx = create_fixture(tmp_path, [_illegal_reply(), _illegal_reply()])
    with pytest.raises(Exception):
        verify(fx)
    assert len(provider_rows(fx)) == 2
    assert len(_events(fx, "verifier_candidate_rejected")) == 2
    assert len(_events(fx, "verifier_correction_authorized")) == 1
    assert len(_events(fx, "verifier_correction_terminated")) == 1
    assert _events(fx, "verification_committed") == []
    before = _snapshot(fx)
    def repeated(_):
        reopened = reopen_fixture(tmp_path)
        with pytest.raises(Exception):
            verify(reopened)
    with ThreadPoolExecutor(max_workers=3) as pool:
        list(pool.map(repeated, range(3)))
    assert _snapshot(fx) == before


@pytest.mark.parametrize("unknown", ["UNKNOWN", "INTERRUPT"])
@pytest.mark.parametrize("initial", [True, False], ids=["initial", "correction"])
def test_F_unknown_no_blind_redispatch_retains_native_budget(tmp_path, unknown, initial):
    fx = create_fixture(tmp_path, ([unknown] if initial else [_illegal_reply(), unknown]))
    with pytest.raises(BaseException):
        verify(fx)
    assert len(provider_rows(fx)) == (1 if initial else 2)
    state = fx.budget.canonical_state()
    assert state.used.logical_model_calls == (1 if initial else 2)
    assert state.used.model_tokens_total > 0 or state.active_reserved.model_tokens_total > 0
    before_calls = len(provider_rows(fx))
    with pytest.raises(Exception):
        verify(reopen_fixture(tmp_path))
    assert len(provider_rows(fx)) == before_calls
    assert _events(fx, "verification_committed") == []
    assert len(_events(fx, "verifier_correction_authorized")) <= 1


def test_J_success_with_unknown_usage_settles_conservatively(tmp_path):
    unknown = {**legal_reply(), "_usage_unknown": True}
    fx = create_fixture(tmp_path, [_illegal_reply(), unknown])
    verify(fx)
    state = fx.budget.canonical_state()
    assert len(provider_rows(fx)) == state.used.logical_model_calls == 2
    assert state.used.model_tokens_total > 46
    assert state.active_reserved.model_tokens_total == 0
    before = _snapshot(fx)
    verify(reopen_fixture(tmp_path))
    assert _snapshot(fx) == before


def test_J_real_budget_router_full_original_child_and_reopened_existing_only_replay(tmp_path):
    from apps.enterprise_document.services.agent_rag_v1.model_tool_budget_runtime import CanonicalInvocationBudgetRouter
    fx = create_fixture(tmp_path, [_illegal_reply(), legal_reply()], budget_mode="router")
    assert isinstance(fx.gateway.budget_runtime, CanonicalInvocationBudgetRouter)
    committed = verify(fx)
    assert committed[0].verification_result.verdict.value == "UNSUPPORTED"
    assert len(provider_rows(fx)) == 2
    assert fx.budget.canonical_state().used.logical_model_calls == 2
    assert fx.budget.canonical_state().used.model_tokens_total == 46
    assert fx.budget_lookups["existing"], "actual composition must resolve the durable Router owner"
    assert sum(row["event_type"] == "account_created" for row in _rows(fx.root / "budget.jsonl")) == 1
    reopened = reopen_fixture(tmp_path)
    before = _snapshot(reopened)
    assert verify(reopened) == committed
    assert reopened.budget_lookups["provider"] == [], "healthy replay must not use a creating Budget provider"
    assert reopened.budget_lookups["existing"]
    assert _snapshot(reopened) == before
    (fx.root / "router-replay-proof.json").write_text(json.dumps({"account_created": 1,
        "initial_child_logical_calls": 2, "actual_tokens": 46,
        "replay_creating_provider_calls": len(reopened.budget_lookups["provider"]),
        "replay_existing_only_reads": len(reopened.budget_lookups["existing"]),
        "replay_owner_bytes_unchanged": True}, sort_keys=True) + "\n", encoding="utf-8")


@pytest.mark.parametrize("missing", ["ledger-absent", "account-absent"])
def test_J_real_budget_router_missing_existing_account_never_bootstraps(tmp_path, missing):
    fx = create_fixture(tmp_path, [_illegal_reply(), legal_reply()], budget_mode="router")
    verify(fx)
    ledger = fx.root / "budget.jsonl"
    original = ledger.read_bytes()
    provider_before = len(fx.budget_lookups["provider"])
    if missing == "ledger-absent":
        (fx.root / "fault-budget-router-original.jsonl").write_bytes(original)
        ledger.unlink()
        task_id, run_id = fx.budget.task_id, fx.budget.run_id
    else:
        task_id, run_id = "task-no-existing-budget-account", "run-no-existing-budget-account"
    with pytest.raises(Exception):
        fx.gateway.budget_runtime.resolve_existing_budget_manager(task_id, run_id)
    assert len(fx.budget_lookups["provider"]) == provider_before
    assert fx.budget_lookups["existing"][-1] == (task_id, run_id)
    assert len(provider_rows(fx)) == 2
    if missing == "ledger-absent":
        assert not ledger.exists(), "existing-only resolution must not create even an empty ledger"
        with pytest.raises(Exception):
            reopen_fixture(tmp_path)
        assert not ledger.exists()
        ledger.write_bytes(original)
    else:
        assert ledger.read_bytes() == original
    assert sum(row["event_type"] == "account_created" for row in _rows(ledger)) == 1
    (fx.root / "router-missing-account-proof.json").write_text(json.dumps({"missing": missing,
        "creating_provider_called": False, "new_ledger_or_account_created": False,
        "provider_calls_unchanged": True}, sort_keys=True) + "\n", encoding="utf-8")


def test_original_and_child_provider_io_hold_no_m6_or_lifecycle_file_lock(tmp_path):
    fx = create_fixture(tmp_path, [_illegal_reply(), legal_reply()])
    observations = []
    def inspect_os_locks(_request, ordinal):
        for owner, path in [("M6", fx.runtime.lock_path), ("Lifecycle", fx.lifecycle.lock_path)]:
            with Path(path).open("r+b", buffering=0) as handle:
                handle.seek(0)
                if os.name == "nt":
                    import msvcrt
                    msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
                    handle.seek(0)
                    msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
                else:
                    import fcntl
                    fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                    fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
            observations.append((ordinal, owner))
    fx.client.before_response = inspect_os_locks
    verify(fx)
    assert observations == [(0, "M6"), (0, "Lifecycle"), (1, "M6"), (1, "Lifecycle")]
    (fx.root / "actual-os-lock-provider-probe.json").write_text(json.dumps({"pid": os.getpid(),
        "provider_pids": [row["pid"] for row in provider_rows(fx)], "observations": observations},
        sort_keys=True) + "\n", encoding="utf-8")


@pytest.mark.parametrize("failure", ["UNAVAILABLE", "malformed-json", "wrong-authority"])
def test_negatives_provider_failure_and_nonwhite_guard_do_not_correct(tmp_path, failure):
    reply = {"malformed-json": "not json", "wrong-authority": legal_reply("VERIFIED", ["evidence-item://not-authorized"])}.get(failure, failure)
    fx = create_fixture(tmp_path, [reply])
    with pytest.raises(Exception):
        verify(fx)
    assert len(provider_rows(fx)) == 1
    assert _events(fx, "verifier_correction_authorized") == []
    assert _events(fx, "verification_committed") == []


def _multiprocess_worker(root, barrier, ordinal, result_path):
    try:
        fx = reopen_fixture(root)
        fx.composer.verifier_max_output_tokens = 1024 if ordinal % 2 == 0 else 512
        context = fx.context.model_copy(update={"authorization_ref": f"lvr-race-auth-{ordinal}"})
        barrier.wait(timeout=30)
        result = verify(fx, context=context)
        payload = {"status": "committed", "result": _dump_record(result[0])}
    except BaseException as exc:
        payload = {"status": "rejected", "type": type(exc).__name__, "message": str(exc), "traceback": traceback.format_exc()}
    payload["pid"] = os.getpid()
    Path(result_path).write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def test_G_real_windows_spawn_initial_registration_auth_race_slot_and_restart(tmp_path):
    fx = create_fixture(tmp_path, [_illegal_reply(), legal_reply()])
    ctx = multiprocessing.get_context("spawn")
    barrier = ctx.Barrier(4)
    result_paths = [tmp_path / f"worker-{ordinal}.json" for ordinal in range(4)]
    workers = [ctx.Process(target=_multiprocess_worker, args=(str(tmp_path), barrier, ordinal, str(path))) for ordinal, path in enumerate(result_paths)]
    for worker in workers:
        worker.start()
    try:
        for worker in workers:
            worker.join(timeout=90)
            assert worker.exitcode == 0
    finally:
        for worker in workers:
            if worker.is_alive():
                worker.terminate()
                worker.join(timeout=10)
    results = [json.loads(path.read_text(encoding="utf-8")) for path in result_paths]
    assert len({worker.pid for worker in workers}) == 4
    assert [row["pid"] for row in results] == [worker.pid for worker in workers]
    (fx.root / "actual-spawn-pids.json").write_text(json.dumps({"parent_pid": os.getpid(),
        "workers": [{"pid": worker.pid, "exit_code": worker.exitcode, "outcome": row["status"]}
            for worker, row in zip(workers, results)]}, sort_keys=True) + "\n", encoding="utf-8")
    assert any(row["status"] == "committed" for row in results), results
    assert len(provider_rows(fx)) == 2
    assert len(_events(fx, "verifier_correction_authorized")) == 1
    assert len(_events(fx, "verification_committed")) == 1
    inputs = _artifacts(fx, "canonical_verifier_input")
    assert sum(VERIFIER_LOCAL_REGISTRATION_KEY in raw["input_material"]["invocation"]["deterministic_validation_facts"] for _, raw in inputs) == 1
    winner = next(row for row in results if row["status"] == "committed")
    reopened = reopen_fixture(tmp_path)
    reopened.composer.verifier_max_output_tokens = winner["result"]["verification_result"]["deterministic_validation_facts"][
        reopened.composer.OUTPUT_CAP_BINDING_KEY]["effective_max_output_tokens"]
    original_context = reopened.context.model_copy(update={"authorization_ref": winner["result"]["verification_result"]["verification_authorization_ref"]})
    before = _snapshot(reopened)
    assert _dump_record(verify(reopened, context=original_context)[0]) == winner["result"]
    assert _snapshot(reopened) == before


def _replace_artifact_current(fx, version, marker):
    """Use real version commit/promotion CAS to invalidate an exact old source."""
    from core.runtime.side_effects import LogicalOperationRef
    from apps.enterprise_document.schemas.agent_rag_v1.facts import FactSnapshotContentSchema
    from apps.enterprise_document.schemas.agent_rag_v1.paragraphs import ParagraphCandidateSchema
    from apps.enterprise_document.schemas.agent_rag_v1.research import EvidencePackageContentSchema
    from apps.enterprise_document.services.agent_rag_v1 import EvidencePackageCommitOperations
    raw, _ = fx.artifact.read_version(version)
    changed = deepcopy(raw)
    if version.logical_ref.artifact_type == "paragraph":
        changed["text"] += " New canonical paragraph revision."
    elif version.logical_ref.artifact_type == "evidence_package":
        changed["selections"] = changed["selections"][:1]
    else:
        changed["facts"][0]["normalized_value"] = {"value": 2}
    lineage = fx.context.active_lineage
    def operation(kind):
        return LogicalOperationRef(logical_operation_id=f"lvr-stale-{marker}-{kind}", operation_kind=kind,
            run_id=lineage.run_id, operation_scope_ref=lineage.attempt_scope_ref,
            attempt_scope_ref=lineage.attempt_scope_ref, attempt_id=lineage.attempt_id)
    head = fx.artifact.resolve_active(version.logical_ref)
    if version.logical_ref.artifact_type == "fact_snapshot":
        content = FactSnapshotContentSchema.model_validate(changed)
        successor = fx.fact.commit_snapshot(logical_ref=version.logical_ref, content=content,
            operation_ref=operation("ARTIFACT_VERSION_COMMIT"), commit_guard_context=lineage)
        fx.fact.promote_snapshot(logical_ref=version.logical_ref, target_version_ref=successor,
            expected_head_revision=head.head_revision, expected_active_version_id=version.artifact_version_id,
            operation_ref=operation("ARTIFACT_ACTIVE_PROMOTION"), commit_guard_context=lineage)
    elif version.logical_ref.artifact_type == "evidence_package":
        content = EvidencePackageContentSchema.model_validate(changed)
        plan = fx.plan.resolve_current_plan_binding(run_id=lineage.run_id,
            document_scope_ref=content.section_plan_source.plan_version_ref.logical_ref.domain_scope_ref, section_id=fx.claim.section_id)
        successor = fx.research.commit_evidence_package(candidate_content=content, logical_ref=version.logical_ref,
            plan_binding=plan, expected_head_revision=head.head_revision, expected_active_version_id=version.artifact_version_id,
            operations=EvidencePackageCommitOperations(version_commit=operation("ARTIFACT_VERSION_COMMIT"),
                active_promotion=operation("ARTIFACT_ACTIVE_PROMOTION")), commit_guard_context=lineage)
    else:
        # The Paragraph authorization callback is the controlled upstream test
        # seam. All native REVISE identity/base/source/lineage/CAS rules execute.
        revise = fx.paragraph.revise_operation(run_id=lineage.run_id, section_id=fx.claim.section_id,
            write_instance_id=lineage.attempt_id, target_logical_ref=version.logical_ref, base_version_ref=version,
            authorization_ref=f"lvr-stale-paragraph-repair-authorized://{marker}")
        candidate = ParagraphCandidateSchema(**{key: value for key, value in changed.items() if key != "schema_version"}, operation_ref=revise)
        successor = fx.paragraph.commit_paragraph_version(candidate=candidate, active_lineage=lineage)
        fx.paragraph.promote_paragraph_active(candidate=candidate, target_version_ref=successor,
            expected_head_revision=head.head_revision, expected_active_version_id=version.artifact_version_id,
            active_lineage=lineage)
    promoted = fx.artifact.resolve_active(version.logical_ref)
    assert promoted.active_version_ref == successor and promoted.head_revision == head.head_revision + 1


def _damage_immutable_record(fx, domain):
    """Immutable domains have no mutable semantic head; damage exact bytes.

    The original fresh fixture bytes remain in a fault-before file, and the
    actual strict owner loader must reject rather than reinterpret those bytes.
    """
    path = fx.support_composition.runtime.ledger_path if domain == "Support" else fx.runtime.ledger_path
    original = path.read_bytes()
    (fx.root / f"fault-{domain}-before.jsonl").write_bytes(original)
    rows = _rows(path)
    for row in rows:
        if domain == "Claim" and row["event_type"] == "claim_records_committed":
            row["claim_records"][0]["normalized_value"] = "changed immutable Claim material"
            break
        if domain == "Binding" and row["event_type"] == "binding_facts_committed":
            row["record"]["source_set_hash"] = "a" * 64
            break
        if domain == "Support" and row["event_type"] == "support_committed":
            row["record"]["supporting_refs"] = list(reversed(row["record"]["evidence_target_set_ref"]["evidence_member_refs"]))
            break
    else:
        raise AssertionError(f"missing actual {domain} owner record")
    path.write_text("".join(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n" for row in rows), encoding="utf-8")
    assert path.read_bytes() != original


def _replace_writer_project(fx):
    from apps.enterprise_document.schemas.pre_write_writer import WriterGenerationInvocationSchema, WriterExecutionResultSchema
    from core.runtime.pre_write_execution_store import SQLitePreWriteExecutionStore
    input_, project = fx.composer.build_input(fx.claim, fx.binding, fx.support, fx.context)
    old = fx.writer.resolve_writer_generation_invocation(input_.verifier_project_input_basis.writer_generation_invocation_ref)
    data = old.model_dump(mode="json")
    data["unified_writer_input_snapshot"]["project_input"]["total_staff"] = project.total_staff + 1
    new = WriterGenerationInvocationSchema.model_validate(data)
    key = input_.verifier_project_input_basis.writer_generation_invocation_ref.logical_execution_key
    result_data = fx.writer.get_writer_execution(key).result.model_dump(mode="json")
    result_data["artifact"]["input"]["writer_generation_invocation"] = new.model_dump(mode="json")
    store = SQLitePreWriteExecutionStore(fx.root / "writer-changed-project.sqlite3")
    digest = canonical_sha256(new.model_dump(mode="json"))
    store.reserve_writer_execution(key, semantic_fingerprint=digest)
    store.mark_writer_dispatched(key, semantic_fingerprint=digest)
    store.persist_writer_result(key, semantic_fingerprint=digest, result=WriterExecutionResultSchema.model_validate(result_data))
    fx.composer.writer_execution_store = store
    assert store.get_writer_execution(key).semantic_fingerprint == digest
    assert digest != input_.verifier_project_input_basis.writer_generation_invocation_ref.invocation_sha256


def _produce_real_initial_head(fx, *, publish=True):
    """A competing native M6 producer, with complete actual Provider evidence.

    This fresh compatible producer uses an explicitly UNREGISTERED typed input
    and independent authorization. Its separate
    Artifact namespace cannot become a second eligible local original root. It
    does not ask composition to bypass its changed-initial registration fence.
    """
    from apps.enterprise_document.agents.agent_rag_v1.verifier import VerifierAgent
    original_artifacts = {ref.artifact_ref: ref.content_hash for ref in fx.composer.store.list_references()}
    context = fx.context.model_copy(update={"authorization_ref": "lvr-real-competing-initial-producer"})
    unregistered, _ = fx.composer.build_input(fx.claim, fx.binding, fx.support, context, _recorded_recovery_facts={})
    assert VERIFIER_LOCAL_REGISTRATION_KEY not in unregistered.deterministic_validation_facts
    invocation, material = fx.composer._material(fx.claim, fx.binding, fx.support, context, _recorded_invocation=unregistered)
    key = canonical_sha256({"contract": "verifier-physical-occurrence-v2",
        "verifier_semantic_input_sha256": invocation.verifier_semantic_input_sha256})
    occurrence = "claim-verification-v2://sha256/" + key
    prefix = f"artifact://lvr-independent-head-producer/{key}"
    identity = {"task_id": fx.lifecycle.load_run_identity(context.active_lineage.run_id).task_id,
        "run_id": context.active_lineage.run_id, "section_id": fx.claim.section_id}
    def actual_currentness():
        assert fx.composer._material(fx.claim, fx.binding, fx.support, context, _recorded_invocation=invocation)[1] == material
    package, limit = fx.composer._package(material, occurrence=occurrence, invocation=invocation, validate=actual_currentness)
    request = fx.composer._model_request(invocation, package, occurrence=occurrence,
        output_limit=limit, task_id=identity["task_id"], section_id=fx.claim.section_id)
    def put(value, suffix, type_, schema):
        return fx.composer.store.put_json(value, artifact_ref=prefix + suffix, artifact_type=type_,
            schema_version=schema, created_by="real-competing-m6-producer-test-fixture", **identity)
    put(request.model_dump(mode="json"), "/request", "canonical_verifier_request", "canonical_verifier_request_v1")
    put({"schema_version": "canonical_verifier_input_v2", "input_material": material,
        "input_fingerprint": invocation.verifier_semantic_input_sha256, "context_package": package.model_dump(mode="json"),
        "request_ref": prefix + "/request", "request": request.model_dump(mode="json")},
        "/input", "canonical_verifier_input", "canonical_verifier_input_v2")
    actual_currentness()
    response = fx.gateway.generate(request)
    assert response.success
    tokens = [item for item in fx.budget.canonical_state().reservations
        if item.reservation_ref.reservation_kind == "model_provider_attempt_tokens"
        and item.reservation_ref.operation_ref == request.model_call_id]
    assert len(tokens) == 1 and tokens[0].status == "consumed"
    put({"schema_version": "canonical_verifier_response_v1", "input_fingerprint": invocation.verifier_semantic_input_sha256,
        "request_ref": prefix + "/request", "request_sha256": canonical_sha256(request.model_dump(mode="json")),
        "response": response.model_dump(mode="json"), "budget_settlement_confirmed": True},
        "/response", "canonical_verifier_response", "canonical_verifier_response_v1")
    entry = fx.composer._initial_reply_entry(response.content)
    normalized, metadata = fx.composer.normalize_reply_reference_order(response.content)
    entry.update(metadata)
    entry.update(normalized_reply=normalized, normalization_status="completed")
    parsed = []
    def evaluate_actual(actual):
        pair = fx.composer._parse_candidate_strict(normalized, invocation=actual, occurrence=occurrence,
            receipt_ref=prefix + "/candidate")
        parsed.append(pair)
        return pair[0]
    candidate = VerifierAgent.evaluate_canonical_claim(invocation, evaluator=evaluate_actual)
    semantic = parsed[0][1]
    put({"schema_version": "canonical_verifier_candidate_v2", "input_fingerprint": invocation.verifier_semantic_input_sha256,
        "outcome": "completed", "candidate": candidate.model_dump(mode="json"), "semantic_result": semantic,
        "reply_entry": entry, "reply_entry_available": True},
        "/candidate", "canonical_verifier_candidate", "canonical_verifier_candidate_v2")
    actual_currentness()
    commit_args = (fx.claim.claim_ref, fx.binding.binding_facts_ref,
        fx.support.support_ref, candidate, None, context)
    result = fx.runtime.commit_verification(*commit_args) if publish else None
    if publish:
        assert fx.runtime.resolve_claim_head(fx.claim.claim_ref) == result.resulting_claim_head
    current_artifacts = {ref.artifact_ref: ref.content_hash for ref in fx.composer.store.list_references()}
    assert all(current_artifacts[ref] == digest for ref, digest in original_artifacts.items())
    (fx.root / "independent-producer-receipts.json").write_text(json.dumps({
        "artifact_prefix": prefix, "physical_occurrence": occurrence,
        "model_call_id": request.model_call_id, "actual_provider_pid": provider_rows(fx)[-1]["pid"],
        "registered_local_original": False, "published_head": publish,
        "retained_prior_artifact_hashes": original_artifacts}, sort_keys=True) + "\n", encoding="utf-8")
    return result if publish else commit_args


def _exercise_real_expected_head_checkpoint(fx, monkeypatch, stage):
    if stage == "grant":
        from apps.enterprise_document.schemas.agent_rag_v1.verification import build_verifier_candidate_rejection
        proposals = []
        actual_record = fx.runtime.record_verifier_candidate_rejection
        def competing_before_rejection(root, rejected_input, candidate, chain, context):
            with pytest.raises(ClaimBindingError) as violation:
                fx.runtime.validate_v2_provenance(fx.binding, fx.support, candidate)
            request = fx.composer.store.get_json(chain.request_ref, task_id=provider_rows(fx)[0]["request"]["task_id"],
                run_id=context.active_lineage.run_id)
            response = fx.composer.store.get_json(chain.response_ref, task_id=request["task_id"], run_id=request["run_id"])["response"]
            proposals.append(build_verifier_candidate_rejection(root=root, correction_ordinal=0,
                rejected_input=rejected_input, candidate=candidate, request=request, response=response,
                artifact_chain=chain, violation=violation.value.violation))
            _produce_real_initial_head(fx)
            return actual_record(root, rejected_input, candidate, chain, context)
        monkeypatch.setattr(fx.runtime, "record_verifier_candidate_rejection", competing_before_rejection)
        with pytest.raises(ClaimHeadConflict):
            verify(fx)
        assert len(proposals) == 1
        assert fx.runtime.resolve_claim_head(fx.claim.claim_ref) is not None
        with pytest.raises(ClaimHeadConflict):
            fx.runtime.authorize_verifier_correction(proposals[0], fx.context)
    else:
        captured = []
        actual_commit = fx.runtime.commit_verification
        def candidate_durable_before_commit(*args, **kwargs):
            if VERIFIER_LOCAL_CORRECTION_KEY in args[3].deterministic_validation_facts:
                captured.append((args, kwargs))
                raise KeyboardInterrupt("actual child candidate durable before formal commit")
            return actual_commit(*args, **kwargs)
        with monkeypatch.context() as scope:
            scope.setattr(fx.runtime, "commit_verification", candidate_durable_before_commit)
            with pytest.raises(KeyboardInterrupt):
                verify(fx)
        assert len(captured) == 1
        args, kwargs = captured[0]
        candidate = args[3]
        assert any(raw.get("candidate") == candidate.model_dump(mode="json")
            for _, raw in _artifacts(fx, "canonical_verifier_candidate"))
        assert len(_artifacts(fx, "canonical_verifier_response")) == 2
        competing_error = None
        concurrent_replay = False
        if stage == "commit":
            # The original permanent-root gate excludes another initial
            # producer before it can publish a competing head. Its actual
            # Gateway/Context/Budget/Artifact path is retained; no gate is
            # replaced merely to create an otherwise unreachable head race.
            assert fx.runtime.resolve_claim_head(fx.claim.claim_ref) is None
            competing_args = _produce_real_initial_head(fx, publish=False)
            before_competing_commit = _snapshot(fx)
            with pytest.raises(ClaimBindingError) as blocked:
                actual_commit(*competing_args)
            assert str(blocked.value) == "Verifier initial root cannot change registration/auth/profile"
            assert fx.runtime.resolve_claim_head(fx.claim.claim_ref) is None
            assert _events(fx, "verification_committed") == []
            assert _snapshot(fx) == before_competing_commit
            competing_error = {"exception_type": type(blocked.value).__name__,
                "message": str(blocked.value), "actual_head_exists_after_refusal": False,
                "refusal_checkpoint": "permanent_original_root_guard"}
            # Two actual owner calls race the same durable, valid child. The
            # winner commits once; the other must replay the exact operation.
            from threading import Barrier
            barrier = Barrier(2)
            def commit_same_candidate(_):
                barrier.wait(timeout=30)
                return actual_commit(*args, **kwargs)
            with ThreadPoolExecutor(max_workers=2) as pool:
                receipts = list(pool.map(commit_same_candidate, range(2)))
            assert receipts[0] == receipts[1]
            receipt = receipts[0]
            concurrent_replay = True
        else:
            receipt = actual_commit(*args, **kwargs)
        assert fx.runtime.resolve_claim_head(fx.claim.claim_ref) == receipt.resulting_claim_head
        before = _snapshot(fx)
        child = fx.runtime.verifier_input_from_candidate(candidate)
        if stage == "commit":
            # Original operation replay is healthy, even after its own head.
            assert actual_commit(*args, **kwargs) == receipt
            assert _snapshot(fx) == before
            # An adversarial changed occurrence is a different operation, not
            # a second legal Provider result. The real permanent head fence
            # must reject it with no owner append or extra Provider.
            changed = candidate.model_copy(update={"verification_occurrence_ref":
                candidate.verification_occurrence_ref + "/adversarial-new-operation"})
            probe_args = (*args[:3], changed, *args[4:])
            with pytest.raises(ClaimHeadConflict):
                actual_commit(*probe_args, **kwargs)
        with pytest.raises(ClaimHeadConflict):
            fx.runtime.validate_verifier_correction_dispatch(child, fx.context)
        assert _snapshot(fx) == before
    expected_calls = 3 if stage == "commit" else 2
    assert len(provider_rows(fx)) == expected_calls
    assert len(_events(fx, "verification_committed")) == 1
    assert fx.runtime.resolve_claim_head(fx.claim.claim_ref).head_revision == 1
    (fx.root / "expected-head-checkpoint.json").write_text(json.dumps({"stage": stage,
        "actual_head_exists": True, "provider_calls": expected_calls, "formal_commits": 1,
        "commit_exact_operation_replay": stage == "commit",
        "changed_occurrence_adversarial_probe": stage == "commit",
        "competing_publication_refusal": competing_error if stage == "commit" else None,
        "concurrent_same_operation_winner_replay": concurrent_replay if stage == "commit" else False,
        "primary_provider_calls": 1 if stage == "grant" else 2,
        "independent_adversarial_provider_calls": 1 if stage in {"grant", "commit"} else 0,
        "provider_calls_after_commit_probes": 0}, sort_keys=True) + "\n", encoding="utf-8")


def _inject_stale(fx, domain):
    from apps.enterprise_document.schemas.agent_rag_v1.lifecycle import RunLifecycleStatus, AttemptLifecycleStatus
    if domain == "Claim":
        # Claim currentness is derived from its producing Paragraph, not a new
        # Claim owner/head. A real Paragraph promotion invalidates the old Claim.
        _replace_artifact_current(fx, fx.claim.paragraph_version_ref, "claim-producing-paragraph")
    elif domain == "Binding":
        paragraph = fx.paragraph.read_paragraph(fx.claim.paragraph_version_ref)
        _replace_artifact_current(fx, paragraph.source_binding.fact_snapshot_version_ref, "binding-exact-fact-source")
    elif domain == "Support":
        paragraph = fx.paragraph.read_paragraph(fx.claim.paragraph_version_ref)
        _replace_artifact_current(fx, paragraph.source_binding.evidence_package_version_ref, "support-exact-evidence-source")
    elif domain == "Paragraph":
        _replace_artifact_current(fx, fx.claim.paragraph_version_ref, "paragraph")
    elif domain == "WriterProjectInput":
        _replace_writer_project(fx)
    elif domain == "snapshot":
        lifecycle = fx.lifecycle
        opened = lifecycle.open_attempt(fx.context.active_lineage.run_id, fx.context.active_lineage.attempt_scope_ref,
            "new-input-snapshot-after-original-verifier", attempt_id="lvr-currentness-new-snapshot-attempt")
        lifecycle.activate_attempt(opened.identity.attempt_id, opened.lifecycle_revision)
        newer = lifecycle.resolve_active_lineage(fx.context.active_lineage.run_id, fx.context.active_lineage.attempt_scope_ref)
        assert newer.attempt_id == opened.identity.attempt_id
        assert newer.input_snapshot_version != fx.context.active_lineage.input_snapshot_version
    elif domain == "Run":
        run = fx.lifecycle.load_run(fx.context.active_lineage.run_id)
        fx.lifecycle.transition_run(run.identity.run_id, run.lifecycle_revision, RunLifecycleStatus.FAILED)
        assert fx.lifecycle.load_run(run.identity.run_id).status is RunLifecycleStatus.FAILED
    elif domain == "Attempt":
        attempt = fx.lifecycle.load_attempt(fx.context.active_lineage.attempt_id)
        fx.lifecycle.transition_attempt(attempt.identity.attempt_id, attempt.lifecycle_revision, AttemptLifecycleStatus.COMPLETED)
        assert fx.lifecycle.load_attempt(attempt.identity.attempt_id).status is AttemptLifecycleStatus.COMPLETED
    elif domain == "FactSnapshot":
        paragraph = fx.paragraph.read_paragraph(fx.claim.paragraph_version_ref)
        _replace_artifact_current(fx, paragraph.source_binding.fact_snapshot_version_ref, "fact")
    else:
        raise AssertionError(domain)


@pytest.mark.parametrize("stage", ["grant", "dispatch", "commit"])
@pytest.mark.parametrize("domain", ["Claim", "Paragraph", "Binding", "Support", "WriterProjectInput", "snapshot", "expected_head"])
def test_H_three_currentness_points_seven_exact_domains(tmp_path, monkeypatch, stage, domain):
    script = [_illegal_reply(), legal_reply()]
    if domain == "expected_head" and stage == "commit":
        script.append(legal_reply())
    fx = create_fixture(tmp_path, script)
    if domain == "expected_head":
        _exercise_real_expected_head_checkpoint(fx, monkeypatch, stage)
        return
    fired = []
    completed = []
    name = {"grant": "authorize_verifier_correction", "dispatch": "validate_verifier_correction_dispatch", "commit": "commit_verification"}[stage]
    original = getattr(fx.runtime, name)
    def guarded(*args, **kwargs):
        is_child = stage != "commit" or VERIFIER_LOCAL_CORRECTION_KEY in args[3].deterministic_validation_facts
        if is_child and not fired:
            fired.append(True)
            _inject_stale(fx, domain)
            completed.append(True)
        return original(*args, **kwargs)
    monkeypatch.setattr(fx.runtime, name, guarded)
    with pytest.raises(Exception) as refusal:
        verify(fx)
    assert fired == [True], "injection must reach the actual requested phase"
    assert completed == [True], "the native stale mutation must actually complete"
    assert len(_events(fx, "verification_committed")) == 0
    assert len(_events(fx, "verifier_correction_authorized")) <= 1
    assert len(provider_rows(fx)) <= (2 if stage == "commit" else 1)
    _retain_actual_refusal(fx, refusal.value, domain=domain, stage=stage, kind="native_derived_currentness")


@pytest.mark.parametrize("domain", ["Run", "Attempt", "FactSnapshot"])
def test_H_additional_lifecycle_and_fact_snapshot_dispatch_fences(tmp_path, monkeypatch, domain):
    fx = create_fixture(tmp_path, [_illegal_reply(), legal_reply()])
    original = fx.runtime.validate_verifier_correction_dispatch
    fired = []
    completed = []
    def guarded(*args, **kwargs):
        if not fired:
            fired.append(True)
            _inject_stale(fx, domain)
            completed.append(True)
        return original(*args, **kwargs)
    monkeypatch.setattr(fx.runtime, "validate_verifier_correction_dispatch", guarded)
    with pytest.raises(Exception) as refusal:
        verify(fx)
    assert fired
    assert completed == [True]
    assert len(provider_rows(fx)) == 1
    assert _events(fx, "verification_committed") == []
    _retain_actual_refusal(fx, refusal.value, domain=domain, stage="dispatch", kind="native_lifecycle_or_fact_currentness")


@pytest.mark.parametrize("stage", ["grant", "dispatch", "commit"])
@pytest.mark.parametrize("domain", ["Claim", "Binding", "Support"])
def test_negative_immutable_owner_corruption_is_distinct_from_stale(tmp_path, monkeypatch, stage, domain):
    fx = create_fixture(tmp_path, [_illegal_reply(), legal_reply()])
    name = {"grant": "authorize_verifier_correction", "dispatch": "validate_verifier_correction_dispatch", "commit": "commit_verification"}[stage]
    original = getattr(fx.runtime, name)
    fired = []
    completed = []
    def guarded(*args, **kwargs):
        is_child = stage != "commit" or VERIFIER_LOCAL_CORRECTION_KEY in args[3].deterministic_validation_facts
        if is_child and not fired:
            fired.append(True)
            _damage_immutable_record(fx, domain)
            completed.append(True)
        return original(*args, **kwargs)
    monkeypatch.setattr(fx.runtime, name, guarded)
    with pytest.raises(Exception) as refusal:
        verify(fx)
    assert fired
    assert completed == [True]
    assert _events(fx, "verification_committed") == []
    assert len(provider_rows(fx)) <= (2 if stage == "commit" else 1)
    _retain_actual_refusal(fx, refusal.value, domain=domain, stage=stage, kind="exact_immutable_corruption")


@pytest.mark.parametrize("cut", ["rejection_persisted_grant_missing", "grant_persisted_reservation_missing",
    "child_intent_response_missing", "dispatched_unknown", "gateway_settled_response_missing",
    "response_complete_validation_interrupted", "candidate_persisted_commit_interrupted", "commit_persisted_return_interrupted"])
def test_I_durable_actual_owner_cut_resume_or_fail_closed(tmp_path, monkeypatch, cut):
    fx = create_fixture(tmp_path, [_illegal_reply(), "INTERRUPT" if cut == "dispatched_unknown" else legal_reply()])
    fired = []
    if cut in {"rejection_persisted_grant_missing", "grant_persisted_reservation_missing"}:
        name = "record_verifier_candidate_rejection" if cut.startswith("rejection") else "authorize_verifier_correction"
        original = getattr(fx.runtime, name)
        def interrupted(*args, **kwargs):
            result = original(*args, **kwargs)
            fired.append(True)
            raise KeyboardInterrupt(cut)
        monkeypatch.setattr(fx.runtime, name, interrupted)
    elif cut in {"child_intent_response_missing", "gateway_settled_response_missing", "response_complete_validation_interrupted"}:
        original = fx.composer.store.put_json
        def interrupted(value, **kwargs):
            type_ = kwargs.get("artifact_type")
            child = len(_events(fx, "verifier_correction_authorized")) == 1
            selected = (type_ == "canonical_verifier_input" if cut == "child_intent_response_missing" else type_ == "canonical_verifier_response")
            if child and selected:
                if cut != "gateway_settled_response_missing":
                    result = original(value, **kwargs)
                fired.append(True)
                raise KeyboardInterrupt(cut)
            return original(value, **kwargs)
        monkeypatch.setattr(fx.composer.store, "put_json", interrupted)
    elif cut in {"candidate_persisted_commit_interrupted", "commit_persisted_return_interrupted"}:
        original = fx.runtime.commit_verification
        def interrupted(*args, **kwargs):
            if VERIFIER_LOCAL_CORRECTION_KEY in args[3].deterministic_validation_facts:
                if cut == "commit_persisted_return_interrupted":
                    original(*args, **kwargs)
                fired.append(True)
                raise KeyboardInterrupt(cut)
            return original(*args, **kwargs)
        monkeypatch.setattr(fx.runtime, "commit_verification", interrupted)
    with pytest.raises(KeyboardInterrupt):
        verify(fx)
    assert fired or cut == "dispatched_unknown"
    before_calls = len(provider_rows(fx))
    resumed = reopen_fixture(tmp_path)
    unknown = cut in {"child_intent_response_missing", "dispatched_unknown", "gateway_settled_response_missing"}
    if unknown:
        with pytest.raises(Exception):
            verify(resumed)
        assert len(provider_rows(fx)) == before_calls
        assert _events(fx, "verification_committed") == []
    else:
        result = verify(resumed)
        assert result[0].verification_result.verdict.value == "UNSUPPORTED"
        assert len(provider_rows(fx)) == 2
        assert len(_events(fx, "verification_committed")) == 1
    assert len(_events(fx, "verifier_correction_authorized")) == 1
    # A second reopen cannot reset quota or fabricate an additional candidate.
    before = len(provider_rows(fx))
    if unknown:
        with pytest.raises(Exception):
            verify(reopen_fixture(tmp_path))
    else:
        verify(reopen_fixture(tmp_path))
    assert len(provider_rows(fx)) == before


def test_I_response_durable_budget_settlement_unknown_fails_closed(tmp_path, monkeypatch):
    fx = create_fixture(tmp_path, [_illegal_reply(), legal_reply()])
    before_settlement = []
    original_settle = fx.adapter.settle_provider_attempt
    def capture_then_settle(*args, **kwargs):
        if len(provider_rows(fx)) == 2:
            before_settlement.append((fx.root / "budget.jsonl").read_bytes())
        return original_settle(*args, **kwargs)
    monkeypatch.setattr(fx.adapter, "settle_provider_attempt", capture_then_settle)
    original_put = fx.composer.store.put_json
    def persist_then_interrupt(value, **kwargs):
        result = original_put(value, **kwargs)
        if kwargs.get("artifact_type") == "canonical_verifier_response" and len(provider_rows(fx)) == 2:
            raise KeyboardInterrupt("durable response cut before candidate validation")
        return result
    monkeypatch.setattr(fx.composer.store, "put_json", persist_then_interrupt)
    with pytest.raises(KeyboardInterrupt):
        verify(fx)
    assert len(before_settlement) == 1
    # This is an abnormal cross-owner durable-state cut, not a normally reachable
    # Gateway ordering: settlement always precedes composition response delivery.
    # Retain the full settled ledger and restore the actual earlier owner prefix
    # to represent an unreconciled token reservation alongside the exact response.
    (fx.root / "fault-budget-settled-suffix-preserved.jsonl").write_bytes((fx.root / "budget.jsonl").read_bytes())
    (fx.root / "budget.jsonl").write_bytes(before_settlement[0])
    reopened = reopen_fixture(tmp_path)
    assert reopened.budget.canonical_state().active_reserved.model_tokens_total > 0
    assert len(_artifacts(reopened, "canonical_verifier_response")) == 2
    with pytest.raises(Exception):
        verify(reopened)
    assert len(provider_rows(fx)) == 2
    assert _events(fx, "verification_committed") == []
    assert reopened.budget.canonical_state().used.logical_model_calls == 2


def _request_from_receipt(value):
    from schemas.model import ModelRequestSchema
    raw = value.get("request", value.get("model_request", value))
    return ModelRequestSchema.model_validate(raw)


def test_I_orphan_reservation_without_grant_no_dispatch_and_native_unreconciled_fence(tmp_path, monkeypatch):
    from apps.enterprise_document.schemas.agent_rag_v1.verification import (
        VerifierCandidateRejectedV1, build_verifier_correction_authorization, build_verifier_recovery_feedback,
    )
    fx = create_fixture(tmp_path, [_illegal_reply(), legal_reply()])
    original_reject = fx.runtime.record_verifier_candidate_rejection
    def persist_then_interrupt(*args, **kwargs):
        original_reject(*args, **kwargs)
        raise KeyboardInterrupt("actual original rejection durable before grant")
    monkeypatch.setattr(fx.runtime, "record_verifier_candidate_rejection", persist_then_interrupt)
    with pytest.raises(KeyboardInterrupt):
        verify(fx)
    rejection = VerifierCandidateRejectedV1.model_validate(_events(fx, "verifier_candidate_rejected")[0]["payload"])
    initial = _artifacts(fx, "canonical_verifier_input")[0][1]
    # Pure construction obtains the one deterministic prospective child identity
    # without authorizing it, persisting an intent or modifying any owner rows.
    feedback = build_verifier_recovery_feedback(rejection.violation, initial["input_material"], rejection.request["messages"])
    proposal = build_verifier_correction_authorization(root=rejection.root, rejection_ref=rejection.rejection_ref,
        rejection_sha256=rejection.rejection_sha256, feedback=feedback)
    child = fx.composer._build_correction_input(proposal)
    key = canonical_sha256({"contract": "verifier-physical-occurrence-v2",
        "verifier_semantic_input_sha256": child.verifier_semantic_input_sha256})
    occurrence = "claim-verification-v2://sha256/" + key
    material = deepcopy(initial["input_material"])
    material["invocation"] = child.model_dump(mode="json")
    def actual_sources_current():
        fx.composer.validate_verification_sources(fx.binding, fx.support, fx.context.active_lineage)
    package, limit = fx.composer._package(material, occurrence=occurrence, invocation=child, validate=actual_sources_current)
    request = fx.composer._model_request(child, package, occurrence=occurrence, output_limit=limit,
        task_id=rejection.request["task_id"], section_id=fx.claim.section_id)
    logical = fx.adapter.reserve_model_logical_call(request)
    fx.adapter.settle_model_logical_call(logical)
    request = request.model_copy(update={"extra": {**request.extra, "provider_attempt_id": "lvr-orphan-provider-attempt"}})
    fx.adapter.reserve_provider_attempt(request)
    assert _events(fx, "verifier_correction_authorized") == []
    assert len(_artifacts(fx, "canonical_verifier_input")) == 1
    reopened = reopen_fixture(tmp_path)
    authorize_calls = []
    actual_authorize = reopened.runtime.authorize_verifier_correction
    def observe_authorize(*args, **kwargs):
        authorize_calls.append(True)
        return actual_authorize(*args, **kwargs)
    monkeypatch.setattr(reopened.runtime, "authorize_verifier_correction", observe_authorize)
    with pytest.raises(Exception):
        reopened.runtime.validate_verifier_correction_dispatch(child, fx.context)
    with pytest.raises(Exception):
        verify(reopened)
    assert authorize_calls == []
    assert _events(reopened, "verifier_correction_authorized") == []
    assert _events(reopened, "verifier_correction_terminated")[0]["payload"]["reason_code"] == "ORPHAN_RESERVATION"
    state = reopened.budget.canonical_state()
    assert state.active_reserved.model_tokens_total > 0 and state.used.logical_model_calls == 2
    with pytest.raises(RuntimeError, match="unreconciled"):
        reopened.adapter.reserve_provider_attempt(request.model_copy(update={"extra": {**request.extra,
            "provider_attempt_id": "lvr-orphan-new-provider-attempt"}}))
    assert len(provider_rows(fx)) == 1
    assert _events(fx, "verification_committed") == []


def test_J_known_not_dispatched_budget_denial_uses_original_release_rule(tmp_path):
    fx = create_fixture(tmp_path, [legal_reply()], budget_tokens=1)
    with pytest.raises(Exception):
        verify(fx)
    assert provider_rows(fx) == []
    state = fx.budget.canonical_state()
    assert state.used.logical_model_calls == state.active_reserved.logical_model_calls == 0
    assert state.active_reserved.model_tokens_total == 0
    logical = [item for item in state.reservations if item.reservation_ref.reservation_kind == "logical_model_call"]
    assert len(logical) == 1 and logical[0].status == "released"
    assert _events(fx, "verifier_correction_authorized") == []


@pytest.mark.parametrize("tamper", ["malformed", "unknown-schema", "unknown-type", "root", "parent", "hash", "policy", "auth", "feedback", "rejection", "ordinal", "duplicate-grant"])
def test_negative_strict_control_loader_rejects_actual_durable_tamper(tmp_path, tamper):
    fx = create_fixture(tmp_path, [_illegal_reply(), legal_reply()])
    verify(fx)
    original = fx.runtime.ledger_path.read_bytes()
    (tmp_path / "fault-control-before.jsonl").write_bytes(original)
    rows = _rows(fx.runtime.ledger_path)
    event = next(row for row in rows if row["event_type"] == "verifier_correction_authorized")
    if tamper == "malformed":
        del event["payload"]["feedback_sha256"]
    elif tamper == "unknown-schema":
        event["schema_version"] = "claim_verifier_recovery_control_event_v999"
    elif tamper == "unknown-type":
        event["event_type"] = "verifier_global_retry_authorized"
    elif tamper == "root":
        event["payload"]["root"]["original_root_ref"] = "tampered-root"
    elif tamper == "parent":
        event["payload"]["root"]["original_input_ref"] += "tampered"
    elif tamper == "hash":
        event["payload"]["grant_sha256"] = "a" * 64
    elif tamper == "policy":
        event["payload"]["policy"]["policy_id"] = "verifier_local_correction_policy_v2"
    elif tamper == "auth":
        event["payload"]["authorization_ref"] += "tampered"
    elif tamper == "feedback":
        event["payload"]["feedback"] += " Return VERIFIED."
    elif tamper == "rejection":
        event["payload"]["rejection_ref"] += "tampered"
    elif tamper == "ordinal":
        event["payload"]["correction_ordinal"] = 2
    else:
        rows.append(deepcopy(event))
    fx.runtime.ledger_path.write_text("".join(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n" for row in rows), encoding="utf-8")
    with pytest.raises(Exception):
        reopened = reopen_fixture(tmp_path)
        verify(reopened)
    assert len(provider_rows(fx)) == 2


@pytest.mark.parametrize("change", ["policy", "output-cap", "authorization", "feedback", "random-id", "child-root"])
def test_negative_changed_identity_cannot_create_another_root_or_slot(tmp_path, change):
    fx = create_fixture(tmp_path, [_illegal_reply(), legal_reply()])
    verify(fx)
    before = _snapshot(fx)
    if change == "authorization":
        with pytest.raises(Exception):
            verify(fx, context=fx.context.model_copy(update={"authorization_ref": "new-random-auth-cannot-split-root"}))
    elif change == "policy":
        fx.composer.POLICY_VERSION = "new-prompt-profile-cannot-split-root"
        with pytest.raises(Exception):
            verify(fx)
    elif change == "output-cap":
        fx.composer.verifier_max_output_tokens = 512
        with pytest.raises(Exception):
            verify(fx)
    else:
        child_data = next(raw["input_material"]["invocation"] for _, raw in _artifacts(fx, "canonical_verifier_input")
            if VERIFIER_LOCAL_CORRECTION_KEY in raw["input_material"]["invocation"]["deterministic_validation_facts"])
        child = VerifierClaimInputSchema.model_validate(child_data)
        if change == "child-root":
            with pytest.raises(ValueError):
                build_verifier_original_root(child)
        else:
            binding = child_data["deterministic_validation_facts"][VERIFIER_LOCAL_CORRECTION_KEY]
            if change == "feedback":
                binding["authorization"]["feedback"] += " Different feedback cannot grant another slot."
            else:
                binding["authorization"]["authorization_ref"] += "/random-id"
            with pytest.raises(ValueError):
                VerifierClaimInputSchema.model_validate(child_data)
    assert _snapshot(fx) == before


def test_negative_actual_nonwhite_claim_binding_guard_cannot_trigger_correction(tmp_path):
    fx = create_fixture(tmp_path, [_illegal_reply()], support_relation="CONTRADICTED")
    with pytest.raises(ClaimBindingError) as error:
        verify(fx)
    assert getattr(error.value, "rule_code", None) != "EVIDENCE_POSITIVE_BASIS_NOT_ADMITTED"
    assert len(provider_rows(fx)) == 1
    assert _events(fx, "verifier_correction_authorized") == []
    assert _events(fx, "verification_committed") == []


def test_negative_terminated_all_dispatch_and_grant_entries_remain_closed(tmp_path):
    from apps.enterprise_document.schemas.agent_rag_v1.verification import VerifierCandidateRejectedV1
    fx = create_fixture(tmp_path, [_illegal_reply(), _illegal_reply()])
    with pytest.raises(Exception):
        verify(fx)
    first = VerifierCandidateRejectedV1.model_validate(_events(fx, "verifier_candidate_rejected")[0]["payload"])
    child_data = next(raw["input_material"]["invocation"] for _, raw in _artifacts(fx, "canonical_verifier_input")
        if VERIFIER_LOCAL_CORRECTION_KEY in raw["input_material"]["invocation"]["deterministic_validation_facts"])
    child = VerifierClaimInputSchema.model_validate(child_data)
    reopened = reopen_fixture(tmp_path)
    before = _snapshot(reopened)
    for call in [lambda: verify(reopened), lambda: reopened.runtime.authorize_verifier_correction(first, fx.context),
            lambda: reopened.runtime.validate_verifier_correction_dispatch(child, fx.context)]:
        with pytest.raises(Exception):
            call()
    assert _snapshot(reopened) == before


def test_negative_existing_formal_result_head_cannot_authorize_or_dispatch(tmp_path):
    from apps.enterprise_document.schemas.agent_rag_v1.verification import VerifierCandidateRejectedV1
    fx = create_fixture(tmp_path, [_illegal_reply(), legal_reply()])
    verify(fx)
    first = VerifierCandidateRejectedV1.model_validate(_events(fx, "verifier_candidate_rejected")[0]["payload"])
    child_data = next(raw["input_material"]["invocation"] for _, raw in _artifacts(fx, "canonical_verifier_input")
        if VERIFIER_LOCAL_CORRECTION_KEY in raw["input_material"]["invocation"]["deterministic_validation_facts"])
    child = VerifierClaimInputSchema.model_validate(child_data)
    before = _snapshot(fx)
    for call in [lambda: fx.runtime.authorize_verifier_correction(first, fx.context),
            lambda: fx.runtime.validate_verifier_correction_dispatch(child, fx.context)]:
        with pytest.raises(Exception):
            call()
    assert _snapshot(fx) == before


def test_negative_initial_stale_is_not_candidate_contract_recovery(tmp_path):
    fx = create_fixture(tmp_path, [_illegal_reply(), legal_reply()])
    _inject_stale(fx, "Paragraph")
    with pytest.raises(Exception):
        verify(fx)
    assert provider_rows(fx) == []
    assert _events(fx, "verifier_candidate_rejected") == []
    assert _events(fx, "verifier_correction_authorized") == []
    assert _events(fx, "verification_committed") == []


def _process_crash_worker(root, cut):
    fx = reopen_fixture(root)
    if cut == "grant_persisted":
        original = fx.runtime.authorize_verifier_correction
        def crash(*args, **kwargs):
            original(*args, **kwargs)
            os._exit(91)
        fx.runtime.authorize_verifier_correction = crash
    else:
        original = fx.composer.store.put_json
        def crash(value, **kwargs):
            if kwargs.get("artifact_type") == "canonical_verifier_response" and len(provider_rows(fx)) == 2:
                os._exit(91)
            return original(value, **kwargs)
        fx.composer.store.put_json = crash
    verify(fx)
    os._exit(92)


@pytest.mark.parametrize("cut", ["grant_persisted", "gateway_settled_response_missing"])
def test_I_windows_spawn_hard_process_exit_reopens_durable_owner(tmp_path, cut):
    fx = create_fixture(tmp_path, [_illegal_reply(), legal_reply()])
    ctx = multiprocessing.get_context("spawn")
    worker = ctx.Process(target=_process_crash_worker, args=(str(tmp_path), cut))
    worker.start()
    try:
        worker.join(timeout=90)
        assert worker.exitcode == 91
    finally:
        if worker.is_alive():
            worker.terminate()
            worker.join(timeout=10)
    (fx.root / "actual-hard-exit-process.json").write_text(json.dumps({"cut": cut, "worker_pid": worker.pid,
        "parent_pid": os.getpid(), "actual_exit_code": worker.exitcode,
        "provider_pids_at_exit": [row["pid"] for row in provider_rows(fx)]}, sort_keys=True) + "\n", encoding="utf-8")
    reopened = reopen_fixture(tmp_path)
    assert len(_events(reopened, "verifier_correction_authorized")) == 1
    if cut == "grant_persisted":
        assert len(provider_rows(fx)) == 1
        verify(reopened)
        assert len(provider_rows(fx)) == 2
        assert len(_events(reopened, "verification_committed")) == 1
    else:
        assert reopened.budget.canonical_state().used.logical_model_calls == 2
        assert reopened.budget.canonical_state().active_reserved.model_tokens_total == 0
        assert len(_artifacts(reopened, "canonical_verifier_response")) == 1
        with pytest.raises(Exception):
            verify(reopened)
        assert len(provider_rows(fx)) == 2
        assert _events(reopened, "verification_committed") == []
