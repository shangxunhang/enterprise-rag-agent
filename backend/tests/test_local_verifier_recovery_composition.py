"""Focused composition checks with real durable owners and native billing."""
from __future__ import annotations

import json

import pytest

from apps.enterprise_document.schemas.agent_rag_v1.verification import (
    VERIFIER_LOCAL_REGISTRATION_KEY, VerifierSemanticResultCandidateSchema,
    build_verifier_original_root, build_verifier_correction_authorization, build_verifier_recovery_feedback,
)
from apps.enterprise_document.services.agent_rag_v1.claim_verifier_composition import TargetClaimVerifierError
from apps.enterprise_document.services.agent_rag_v1.claim_verification_runtime import ClaimCurrentnessError
from backend.tests.local_verifier_recovery_helpers import create_fixture, reopen_fixture, legal_reply, provider_rows, verify


def _events(fx, name):
    return [json.loads(line) for line in fx.runtime.ledger_path.read_text(encoding="utf-8").splitlines()
        if json.loads(line)["event_type"] == name]


def _artifact_refs(fx, kind):
    return [ref for ref in fx.composer.store.list_references() if ref.artifact_type == kind]


def _cut(monkeypatch, fx, kind, ordinal=1):
    original, count = fx.composer.store.put_json, 0
    def put(payload, **kwargs):
        nonlocal count
        result = original(payload, **kwargs)
        if kwargs["artifact_type"] == kind:
            count += 1
            if count == ordinal:
                raise KeyboardInterrupt("after actual owner durable Artifact")
        return result
    monkeypatch.setattr(fx.composer.store, "put_json", put)


@pytest.mark.parametrize("verdict", ["UNSUPPORTED", "CONFLICTED", "REJECTED"])
def test_legal_negative_commits_without_local_control(tmp_path, verdict):
    fx = create_fixture(tmp_path, [legal_reply(verdict)])
    receipt = verify(fx)[0]
    assert receipt.verification_result.verdict.value == verdict
    assert receipt.resulting_claim_head.head_revision == 1
    assert len(provider_rows(fx)) == 1
    assert not _events(fx, "verifier_correction_authorized")
    assert not _events(fx, "verifier_candidate_rejected")
    assert len(_artifact_refs(fx, "canonical_verifier_response")) == 1


@pytest.mark.parametrize("verdict,refs", [("UNSUPPORTED", []), ("VERIFIED", ["@supporting"])])
def test_real_typed_rejection_one_child_and_exact_restart_replay(tmp_path, verdict, refs):
    fx = create_fixture(tmp_path, [legal_reply("VERIFIED", ["@all_evidence"]), legal_reply(verdict, refs)])
    first = verify(fx)
    assert first[0].verification_result.verdict.value == verdict
    assert len(provider_rows(fx)) == 2
    assert len(_events(fx, "verifier_candidate_rejected")) == 1
    assert len(_events(fx, "verifier_correction_authorized")) == 1
    assert len(_events(fx, "verification_committed")) == 1
    assert not _events(fx, "verifier_correction_terminated")
    m6, budget = fx.runtime.ledger_path.read_bytes(), (fx.root / "budget.jsonl").read_bytes()
    restarted = reopen_fixture(tmp_path)
    assert verify(restarted) == first
    assert len(provider_rows(restarted)) == 2
    assert restarted.runtime.ledger_path.read_bytes() == m6
    assert (fx.root / "budget.jsonl").read_bytes() == budget


def test_second_violation_permanently_terminates(tmp_path):
    illegal = legal_reply("VERIFIED", ["@all_evidence"])
    fx = create_fixture(tmp_path, [illegal, illegal])
    with pytest.raises(TargetClaimVerifierError, match="second non-admitted"):
        verify(fx)
    assert len(provider_rows(fx)) == 2
    assert len(_events(fx, "verifier_candidate_rejected")) == 2
    assert len(_events(fx, "verifier_correction_authorized")) == 1
    assert len(_events(fx, "verifier_correction_terminated")) == 1
    assert fx.runtime.resolve_claim_head(fx.claim.claim_ref) is None
    with pytest.raises(TargetClaimVerifierError, match="permanently terminated"):
        verify(reopen_fixture(tmp_path))
    assert len(provider_rows(fx)) == 2


@pytest.mark.parametrize("kind,ordinal,extra_calls", [
    ("canonical_verifier_response", 1, 1),
    ("canonical_verifier_response", 2, 0),
    ("canonical_verifier_candidate", 2, 0),
])
def test_durable_complete_chain_continues_without_repeating_provider(tmp_path, monkeypatch, kind, ordinal, extra_calls):
    fx = create_fixture(tmp_path, [legal_reply("VERIFIED", ["@all_evidence"]), legal_reply()])
    _cut(monkeypatch, fx, kind, ordinal)
    with pytest.raises(KeyboardInterrupt):
        verify(fx)
    count = len(provider_rows(fx))
    committed = verify(reopen_fixture(tmp_path))
    assert committed[0].verification_result.verdict.value == "UNSUPPORTED"
    assert len(provider_rows(fx)) == count + extra_calls == 2
    assert len(_events(fx, "verification_committed")) == 1


@pytest.mark.parametrize("ordinal", [1, 2])
def test_intent_without_response_fails_closed_even_before_budget(tmp_path, monkeypatch, ordinal):
    fx = create_fixture(tmp_path, [legal_reply("VERIFIED", ["@all_evidence"]), legal_reply()])
    _cut(monkeypatch, fx, "canonical_verifier_input", ordinal)
    with pytest.raises(KeyboardInterrupt):
        verify(fx)
    calls = len(provider_rows(fx))
    with pytest.raises(TargetClaimVerifierError, match="without durable response"):
        verify(reopen_fixture(tmp_path))
    assert len(provider_rows(fx)) == calls == ordinal - 1
    assert fx.runtime.resolve_claim_head(fx.claim.claim_ref) is None


def test_registered_scope_excludes_generic_authorization_from_quota(tmp_path):
    fx = create_fixture(tmp_path, [legal_reply()])
    verify(fx)
    changed = fx.context.model_copy(update={"authorization_ref": "a-different-generic-authorization"})
    with pytest.raises(TargetClaimVerifierError, match="scope already binds"):
        verify(fx, context=changed)
    assert len(provider_rows(fx)) == 1
    assert len(_artifact_refs(fx, "canonical_verifier_input")) == 1


def test_recorded_absence_identity_rebuild_does_not_register(tmp_path):
    fx = create_fixture(tmp_path, [legal_reply()])
    legacy, _ = fx.composer.build_input(fx.claim, fx.binding, fx.support, fx.context, _recorded_recovery_facts={})
    assert VERIFIER_LOCAL_REGISTRATION_KEY not in legacy.deterministic_validation_facts
    fx.composer.validate_v2_identity(fx.claim, fx.binding, fx.support, legacy, fx.context.active_lineage)
    assert not provider_rows(fx)


def test_registered_input_precedes_request_and_changed_authorization_cannot_escape_cut(tmp_path, monkeypatch):
    fx = create_fixture(tmp_path, [legal_reply()])
    _cut(monkeypatch, fx, "canonical_verifier_input")
    with pytest.raises(KeyboardInterrupt):
        verify(fx)
    assert len(_artifact_refs(fx, "canonical_verifier_input")) == 1
    assert not _artifact_refs(fx, "canonical_verifier_request")
    changed = fx.context.model_copy(update={"authorization_ref": "different-after-registration-cut"})
    with pytest.raises(TargetClaimVerifierError, match="scope already binds"):
        verify(reopen_fixture(tmp_path), context=changed)
    assert not provider_rows(fx)
    assert len(_artifact_refs(fx, "canonical_verifier_input")) == 1


def test_damaged_metadata_cannot_hide_historical_absent_registration_scope(tmp_path):
    fx = create_fixture(tmp_path, [legal_reply()])
    legacy, _ = fx.composer.build_input(fx.claim, fx.binding, fx.support, fx.context, _recorded_recovery_facts={})
    _, material = fx.composer._material(fx.claim, fx.binding, fx.support, fx.context, _recorded_invocation=legacy)
    _, occurrence, prefix = fx.composer._physical(legacy)
    package, cap = fx.composer._package(material, occurrence=occurrence, invocation=legacy,
        validate=lambda: fx.composer.validate_verification_sources(fx.binding, fx.support, fx.context.active_lineage))
    identity = fx.composer._artifact_identity(legacy)
    request = fx.composer._model_request(legacy, package, occurrence=occurrence, output_limit=cap,
        task_id=identity["task_id"], section_id=fx.claim.section_id).model_dump(mode="json")
    fx.composer.store.put_json({"schema_version": "canonical_verifier_input_v2", "input_material": material,
        "input_fingerprint": legacy.verifier_semantic_input_sha256, "context_package": package.model_dump(mode="json"),
        "request_ref": prefix + "/request", "request": request}, artifact_ref=prefix + "/input",
        artifact_type="canonical_verifier_input", schema_version="canonical_verifier_input_v2", created_by="historical-component-fixture", **identity)
    path = fx.composer.store._path_for_ref(prefix + "/input")
    envelope = json.loads(path.read_bytes())
    envelope["reference"]["artifact_type"] = "damaged-other-type"
    path.write_text(json.dumps(envelope), encoding="utf-8")
    changed = fx.context.model_copy(update={"authorization_ref": "different-after-metadata-damage"})
    with pytest.raises(TargetClaimVerifierError, match="metadata differs"):
        verify(reopen_fixture(tmp_path), context=changed)
    assert not provider_rows(fx)
    assert len([ref for ref in fx.composer.store.list_references() if ref.artifact_ref.endswith("/input")]) == 1


def test_child_formal_commit_must_match_durable_raw_judgment(tmp_path, monkeypatch):
    fx = create_fixture(tmp_path, [legal_reply("VERIFIED", ["@all_evidence"]), legal_reply()])
    _cut(monkeypatch, fx, "canonical_verifier_candidate", 2)
    with pytest.raises(KeyboardInterrupt):
        verify(fx)
    candidates = [fx.composer.store.get_json(item.artifact_ref, task_id=item.task_id, run_id=item.run_id,
        section_id=item.section_id)["candidate"] for item in _artifact_refs(fx, "canonical_verifier_candidate")]
    original = VerifierSemanticResultCandidateSchema.model_validate(next(item for item in candidates
        if VERIFIER_LOCAL_REGISTRATION_KEY not in item["deterministic_validation_facts"]))
    forged = original.model_copy(update={"semantic_rationale_hash": "f" * 64})
    with pytest.raises(ClaimCurrentnessError) as rejected:
        fx.runtime.commit_verification(fx.claim.claim_ref, fx.binding.binding_facts_ref, fx.support.support_ref,
            forged, None, fx.context)
    assert isinstance(rejected.value.__cause__, TargetClaimVerifierError)
    assert rejected.value.__cause__.error_code == "VERIFIER_DURABLE_RESPONSE_INVALID"
    assert fx.runtime.resolve_claim_head(fx.claim.claim_ref) is None
    assert len(provider_rows(fx)) == 2


def test_child_length_indicator_cannot_formally_commit_complete_json(tmp_path, monkeypatch):
    fx = create_fixture(tmp_path, [legal_reply("VERIFIED", ["@all_evidence"]), legal_reply()])
    original, count = fx.client.generate, 0
    def generate(request):
        nonlocal count
        response = original(request)
        count += 1
        return response.model_copy(update={"finish_reason": "length"}) if count == 2 else response
    monkeypatch.setattr(fx.client, "generate", generate)
    with pytest.raises(ClaimCurrentnessError) as rejected:
        verify(fx)
    assert isinstance(rejected.value.__cause__, TargetClaimVerifierError)
    assert rejected.value.__cause__.error_code == "VERIFIER_DURABLE_RESPONSE_INVALID"
    assert len(provider_rows(fx)) == 2
    assert fx.runtime.resolve_claim_head(fx.claim.claim_ref) is None
    assert len(_events(fx, "verifier_correction_terminated")) == 1


def test_budget_proof_rejects_different_physical_provider_owner(tmp_path):
    fx = create_fixture(tmp_path, [legal_reply()])
    verify(fx)
    invocation, _ = fx.composer.build_input(fx.claim, fx.binding, fx.support, fx.context)
    _, request, response = fx.composer._complete_response(invocation, require_canonical_budget=True)
    wrong = request.model_copy(deep=True)
    wrong.extra["provider_attempt_id"] = "different-physical-provider"
    reservation = fx.adapter.reserve_provider_attempt(wrong)
    fx.adapter.settle_provider_attempt(reservation, wrong, response)
    altered = response.model_copy(deep=True)
    altered.metadata["canonical_budget_token_reservation_id"] = reservation
    with pytest.raises(TargetClaimVerifierError, match="physical Provider ordinal"):
        fx.composer._validate_response_settlement(request, altered, required=True)
    assert len(provider_rows(fx)) == 1


def test_native_orphan_child_cannot_receive_late_grant(tmp_path, monkeypatch):
    fx = create_fixture(tmp_path, [legal_reply("VERIFIED", ["@all_evidence"]), legal_reply()])
    def cut_before_authorization(*_args, **_kwargs):
        raise KeyboardInterrupt("original rejection durable, no grant")
    monkeypatch.setattr(fx.runtime, "authorize_verifier_correction", cut_before_authorization)
    with pytest.raises(KeyboardInterrupt):
        verify(fx)
    original = fx.composer.build_input(fx.claim, fx.binding, fx.support, fx.context)[0]
    root = build_verifier_original_root(original)
    rejection = fx.runtime.resolve_verifier_local_recovery(root)["rejections"][0]
    prior, request, _ = fx.composer._complete_response(original, require_canonical_budget=True)
    prospective = build_verifier_correction_authorization(root=root, rejection_ref=rejection.rejection_ref,
        rejection_sha256=rejection.rejection_sha256,
        feedback=build_verifier_recovery_feedback(rejection.violation, prior["input_material"], request.messages))
    child = fx.composer._build_correction_input(prospective)
    _, material = fx.composer._material(fx.claim, fx.binding, fx.support, fx.context, _recorded_invocation=child)
    _, occurrence, _ = fx.composer._physical(child)
    package, cap = fx.composer._package(material, occurrence=occurrence, invocation=child,
        validate=lambda: fx.composer.validate_verification_sources(fx.binding, fx.support, fx.context.active_lineage))
    identity = fx.composer._artifact_identity(child)
    child_request = fx.composer._model_request(child, package, occurrence=occurrence, output_limit=cap,
        task_id=identity["task_id"], section_id=fx.claim.section_id)
    fx.adapter.reserve_model_logical_call(child_request)
    child_request.extra["provider_attempt_id"] = f"provider_attempt_{child_request.model_call_id}_1_aaaaaaaaaaaa"
    fx.adapter.reserve_provider_attempt(child_request)
    before = (fx.root / "budget.jsonl").read_bytes()
    with pytest.raises(TargetClaimVerifierError, match="before its M6 correction grant"):
        verify(reopen_fixture(tmp_path))
    assert (fx.root / "budget.jsonl").read_bytes() == before
    assert not _events(fx, "verifier_correction_authorized")
    assert _events(fx, "verifier_correction_terminated")[0]["payload"]["reason_code"] == "ORPHAN_RESERVATION"
    assert len(provider_rows(fx)) == 1


@pytest.mark.parametrize("entry", ["record", "authorize"])
def test_direct_original_controls_cannot_trust_wrapper_with_unreconciled_native_budget(tmp_path, monkeypatch, entry):
    fx = create_fixture(tmp_path, [legal_reply("VERIFIED", ["@all_evidence"])])
    original_settle, before = fx.adapter.settle_provider_attempt, []
    def settle(*args, **kwargs):
        before.append((fx.root / "budget.jsonl").read_bytes())
        return original_settle(*args, **kwargs)
    monkeypatch.setattr(fx.adapter, "settle_provider_attempt", settle)
    candidate = fx.composer._evaluate(fx.claim, fx.binding, fx.support, fx.context)
    invocation = fx.runtime.verifier_input_from_candidate(candidate)
    root = build_verifier_original_root(invocation)
    chain = fx.composer._rejection_chain(invocation, candidate)
    rejection = None
    if entry == "authorize":
        rejection = fx.runtime.record_verifier_candidate_rejection(root, invocation, candidate, chain, fx.context)
    assert len(before) == 1
    # Preserve an actual journal checkpoint, then reopen the independent owners:
    # response/candidate stay durable while the native token settlement is unknown.
    (fx.root / "settlement-before-cut.copy").write_bytes(before[0])
    (fx.root / "budget.jsonl").write_bytes(before[0])
    reopened = reopen_fixture(tmp_path)
    with pytest.raises(ClaimCurrentnessError) as rejected:
        if entry == "record":
            reopened.runtime.record_verifier_candidate_rejection(root, invocation, candidate, chain, reopened.context)
        else:
            reopened.runtime.authorize_verifier_correction(rejection, reopened.context)
    assert isinstance(rejected.value.__cause__, TargetClaimVerifierError)
    assert rejected.value.__cause__.error_code == "VERIFIER_BUDGET_SETTLEMENT_UNKNOWN"
    assert not _events(fx, "verifier_correction_authorized")
    assert len(_events(fx, "verifier_candidate_rejected")) == (1 if entry == "authorize" else 0)
    assert (fx.root / "budget.jsonl").read_bytes() == before[0]
    assert len(provider_rows(fx)) == 1


def test_native_allowance_preserves_gateway_output_cap_clamp(tmp_path):
    fx = create_fixture(tmp_path, [legal_reply()])
    verify(fx)
    invocation, _ = fx.composer.build_input(fx.claim, fx.binding, fx.support, fx.context)
    _, request, response = fx.composer._complete_response(invocation, require_canonical_budget=True)
    routed = request.model_copy(deep=True)
    routed.max_tokens = request.max_tokens // 2
    routed.extra["provider_attempt_id"] = f"provider_attempt_{request.model_call_id}_1_bbbbbbbbbbbb"
    token_ref = fx.adapter.reserve_provider_attempt(routed)
    fx.adapter.settle_provider_attempt(token_ref, routed, response)
    proof = response.model_copy(deep=True)
    proof.metadata["canonical_budget_token_reservation_id"] = token_ref
    assert fx.composer._validate_response_settlement(request, proof, required=True) is True
    assert len(provider_rows(fx)) == 1


# Phase 1 differential controls use the exact pre-change working-tree method.
# These small native accounts never dispatch a Provider. Defensive-view tests
# explicitly bypass only the owner loader's return value; they do not describe
# malformed records as legally committed native Budget history.
@pytest.fixture(scope="module")
def phase1_settlement_a():
    import hashlib
    from pathlib import Path
    from apps.enterprise_document.services.agent_rag_v1 import claim_verifier_composition as module

    source_path = Path(__file__).resolve().parents[2] / (
        "docs/rc1_full8_budget_phase1_read_consolidation_implementation_20261021_r01/"
        "raw/settlement_oracle_a.py")
    source = source_path.read_bytes()
    assert hashlib.sha256(source).hexdigest() == (
        "16fa62707d029edfb455fb70611abdc05ef26260cdb73216e26fb50716390bcd")
    namespace = dict(vars(module))
    exec(compile(source, str(source_path), "exec"), namespace)
    return namespace["_validate_response_settlement"]


def _phase1_settlement_fixture(tmp_path, *, logical_status="consumed", token_status="consumed",
                               routed_max_tokens=64, usage=(17, 5, 22), usage_complete=True,
                               extra=None):
    from datetime import datetime, timezone
    from types import SimpleNamespace
    from apps.enterprise_document.schemas.agent_rag_v1.budgets import CanonicalBudgetVectorSchema
    from apps.enterprise_document.services.agent_rag_v1.budget_manager import BudgetManager
    from apps.enterprise_document.services.agent_rag_v1.claim_verifier_composition import TargetClaimVerifierComposition
    from apps.enterprise_document.services.agent_rag_v1.model_tool_budget_runtime import CanonicalInvocationBudgetAdapter
    from schemas.model import ModelRequestSchema, ModelResponseSchema, TokenUsageSchema

    now = datetime(2026, 10, 21, tzinfo=timezone.utc)
    manager = BudgetManager.canonical(task_id="phase1-task", run_id="phase1-run",
        limits=CanonicalBudgetVectorSchema(logical_model_calls=16, logical_tool_calls=4,
            model_tokens_total=10000, research_rounds=2, repair_attempts=2,
            generation_attempts=4, replan_attempts=2, true_retry_attempts=2),
        max_elapsed_seconds=3600, storage_path=tmp_path / "budget.jsonl",
        policy_ref="budget-policy://phase1-settlement-native", policy_version="v1", clock=lambda: now)
    adapter = CanonicalInvocationBudgetAdapter(manager)
    request = ModelRequestSchema(model_call_id="phase1-call", task_id=manager.task_id,
        run_id=manager.run_id, prompt="p", max_tokens=64, created_at=now.isoformat(),
        extra=extra if extra is not None else {"section_id": "phase1-section", "attempt_id": "phase1-attempt",
            "logical_operation_id": "phase1-logical"})
    routed = request.model_copy(deep=True)
    routed.max_tokens = routed_max_tokens
    routed.extra["provider_attempt_id"] = f"provider_attempt_{request.model_call_id}_1_aaaaaaaaaaaa"
    logical_id = adapter.reserve_model_logical_call(request)
    token_id = adapter.reserve_provider_attempt(routed)
    response = ModelResponseSchema(model_call_id=request.model_call_id, task_id=request.task_id,
        run_id=request.run_id, model_name="phase1-native-no-provider", success=True,
        created_at=now.isoformat(), token_usage=TokenUsageSchema(
            prompt_tokens=usage[0], completion_tokens=usage[1], total_tokens=usage[2]),
        metadata={"canonical_budget_logical_reservation_id": logical_id,
            "canonical_budget_token_reservation_id": token_id, "model_candidate_index": 0,
            "usage_complete": usage_complete})
    if logical_status == "consumed":
        adapter.settle_model_logical_call(logical_id)
    elif logical_status == "released":
        adapter.release_reservation(logical_id)
    else:
        assert logical_status == "active"
    if token_status == "consumed":
        adapter.settle_provider_attempt(token_id, routed, response)
    elif token_status == "released":
        adapter.release_reservation(token_id)
    else:
        assert token_status == "active"
    composer = TargetClaimVerifierComposition.__new__(TargetClaimVerifierComposition)
    composer.model_gateway = SimpleNamespace(budget_runtime=adapter)
    return SimpleNamespace(manager=manager, adapter=adapter, request=request, routed=routed,
        response=response, composer=composer, logical_id=logical_id, token_id=token_id,
        ledger=tmp_path / "budget.jsonl")


def _phase1_exception_signature(exc):
    return (type(exc).__module__, type(exc).__qualname__, exc.args, str(exc),
        getattr(exc, "error_code", None), getattr(exc, "failure_category", None),
        getattr(exc, "failure_origin", None), exc.__suppress_context__,
        _phase1_exception_signature(exc.__cause__) if exc.__cause__ is not None else None)


def _phase1_outcome(method, fx, *, required):
    try:
        return ("return", method(fx.composer, fx.request, fx.response, required=required)), None
    except Exception as exc:
        return ("error", _phase1_exception_signature(exc)), exc


def _phase1_assert_settlement_equivalent(fx, phase1_settlement_a, *, required=True,
                                        cause_type=None, cause_args=None, state_check=True):
    from apps.enterprise_document.services.agent_rag_v1.claim_verifier_composition import TargetClaimVerifierComposition
    from schemas.failure import FailureCategory

    before_bytes = fx.ledger.read_bytes()
    before_state = fx.manager.canonical_state().model_dump(mode="json") if state_check else None
    a, a_error = _phase1_outcome(phase1_settlement_a, fx, required=required)
    b, b_error = _phase1_outcome(TargetClaimVerifierComposition._validate_response_settlement, fx, required=required)
    assert b == a
    if cause_type is None:
        assert a == ("return", True)
    else:
        for error in (a_error, b_error):
            assert type(error) is TargetClaimVerifierError
            assert error.error_code == "VERIFIER_BUDGET_SETTLEMENT_UNKNOWN"
            assert error.failure_category == FailureCategory.UNKNOWN
            assert type(error.__cause__) is cause_type
            if cause_args is not None:
                assert error.__cause__.args == cause_args
    assert fx.ledger.read_bytes() == before_bytes
    if state_check:
        assert fx.manager.canonical_state().model_dump(mode="json") == before_state
    return a_error, b_error


def _phase1_defensive_view(monkeypatch, fx, changes):
    """Synthetic defensive-view injection, never a valid committed-history claim."""
    state = fx.manager.canonical_state()
    replacements = {}
    for role, path, value in changes:
        record_id = fx.logical_id if role == "logical" else fx.token_id
        record = replacements.get(record_id) or next(row for row in state.reservations
            if row.reservation_ref.reservation_id == record_id)
        if "." in path:
            parent, field = path.split(".", 1)
            record = record.model_copy(update={parent: getattr(record, parent).model_copy(update={field: value})})
        else:
            record = record.model_copy(update={path: value})
        replacements[record_id] = record
    view = state.model_copy(update={"reservations": [replacements.get(row.reservation_ref.reservation_id, row)
        for row in state.reservations]}, deep=True)
    monkeypatch.setattr(fx.manager, "_canonical_current_unlocked", lambda: view.model_copy(deep=True))
    return view


def test_phase1_V01_native_consumed_full_observation_and_different_terminal_revisions(tmp_path, phase1_settlement_a):
    fx = _phase1_settlement_fixture(tmp_path)
    before = fx.manager.canonical_state()
    logical = fx.manager.resolve_reservation(fx.logical_id)
    tokens = fx.manager.resolve_reservation(fx.token_id)
    observed, selected = fx.manager.read_state_with_reservations((fx.logical_id, fx.token_id))
    assert observed.model_dump(mode="json") == before.model_dump(mode="json")
    assert [row.model_dump(mode="json") for row in selected] == [
        logical.model_dump(mode="json"), tokens.model_dump(mode="json")]
    assert logical.terminal_budget_revision != tokens.terminal_budget_revision
    assert logical.terminal_budget_revision < observed.revision
    _phase1_assert_settlement_equivalent(fx, phase1_settlement_a)


@pytest.mark.parametrize("extra", [{}, {"section_id": "", "attempt_id": "", "logical_operation_id": ""},
    {"section_id": 0, "attempt_id": 0, "logical_operation_id": 0},
    {"section_id": 7, "attempt_id": 42, "logical_operation_id": 9}])
def test_phase1_V01_native_scope_attempt_operation_original_truthy_fallbacks(tmp_path, phase1_settlement_a, extra):
    fx = _phase1_settlement_fixture(tmp_path, extra=extra)
    fx.response.metadata["canonical_budget_logical_reservation_id"] = f" {fx.logical_id}\t"
    fx.response.metadata["canonical_budget_token_reservation_id"] = f"\t{fx.token_id} "
    _phase1_assert_settlement_equivalent(fx, phase1_settlement_a)


@pytest.mark.parametrize("missing", [("logical",), ("token",), ("logical", "token")])
def test_phase1_V02_native_missing_records_priority(tmp_path, phase1_settlement_a, missing):
    fx = _phase1_settlement_fixture(tmp_path)
    for role in missing:
        fx.response.metadata[f"canonical_budget_{role}_reservation_id"] = f"missing-{role}"
    _phase1_assert_settlement_equivalent(fx, phase1_settlement_a,
        cause_type=KeyError, cause_args=(f"missing-{missing[0]}",))


@pytest.mark.parametrize("duplicate_role", ["logical", "token"])
def test_phase1_V02_native_duplicate_metadata_ids_retain_identity_rejection(tmp_path, phase1_settlement_a, duplicate_role):
    fx = _phase1_settlement_fixture(tmp_path)
    duplicate_id = fx.logical_id if duplicate_role == "logical" else fx.token_id
    fx.response.metadata.update(canonical_budget_logical_reservation_id=duplicate_id,
        canonical_budget_token_reservation_id=duplicate_id)
    _phase1_assert_settlement_equivalent(fx, phase1_settlement_a, cause_type=ValueError,
        cause_args=("native reservation identity or consumed status differs",))


@pytest.mark.parametrize("role", ["logical", "token"])
@pytest.mark.parametrize("field,value", [("task_id", "foreign-task"), ("run_id", "foreign-run"),
    ("scope_ref", "foreign-scope"), ("attempt_id", "foreign-attempt"), ("reservation_kind", "foreign-kind")])
def test_phase1_V03_synthetic_identity_predicates_individually(tmp_path, monkeypatch, phase1_settlement_a, role, field, value):
    fx = _phase1_settlement_fixture(tmp_path)
    _phase1_defensive_view(monkeypatch, fx, [(role, f"reservation_ref.{field}", value)])
    _phase1_assert_settlement_equivalent(fx, phase1_settlement_a, cause_type=ValueError,
        cause_args=("native reservation identity or consumed status differs",))


@pytest.mark.parametrize("role", ["logical", "token"])
@pytest.mark.parametrize("status", ["active", "released"])
def test_phase1_V04_native_unconsumed_status(tmp_path, phase1_settlement_a, role, status):
    fx = _phase1_settlement_fixture(tmp_path, **{f"{role}_status": status})
    _phase1_assert_settlement_equivalent(fx, phase1_settlement_a, cause_type=ValueError,
        cause_args=("native reservation identity or consumed status differs",))


@pytest.mark.parametrize("role", ["logical", "token"])
def test_phase1_V05_synthetic_consumed_missing_terminal_revision(tmp_path, monkeypatch, phase1_settlement_a, role):
    fx = _phase1_settlement_fixture(tmp_path)
    _phase1_defensive_view(monkeypatch, fx, [(role, "terminal_budget_revision", None)])
    _phase1_assert_settlement_equivalent(fx, phase1_settlement_a, cause_type=ValueError,
        cause_args=("native reservation identity or consumed status differs",))


@pytest.mark.parametrize("role,field", [("logical", "owner_occurrence_ref"),
    ("logical", "operation_ref"), ("token", "operation_ref")])
def test_phase1_V06_synthetic_exact_occurrence_predicates(tmp_path, monkeypatch, phase1_settlement_a, role, field):
    fx = _phase1_settlement_fixture(tmp_path)
    _phase1_defensive_view(monkeypatch, fx, [(role, f"reservation_ref.{field}", "different-occurrence")])
    _phase1_assert_settlement_equivalent(fx, phase1_settlement_a, cause_type=ValueError,
        cause_args=("native reservation does not bind exact logical/physical occurrence",))


@pytest.mark.parametrize("index", [-1, False, True, "0", None])
def test_phase1_V07_candidate_index_strict_type(tmp_path, phase1_settlement_a, index):
    fx = _phase1_settlement_fixture(tmp_path)
    fx.response.metadata["model_candidate_index"] = index
    _phase1_assert_settlement_equivalent(fx, phase1_settlement_a, cause_type=ValueError,
        cause_args=("native token reservation differs from exact Gateway physical Provider ordinal",))


@pytest.mark.parametrize("suffix", ["2_aaaaaaaaaaaa", "1_AAAAAAAAAAAA", "1_aaaaaaaaaaa",
    "1_aaaaaaaaaaaaa", "1_aaaaaaaaaaag", "1_aaaaaaaaaaaa_extra"])
def test_phase1_V07_synthetic_provider_ordinal_and_exact_hex_suffix(tmp_path, monkeypatch, phase1_settlement_a, suffix):
    fx = _phase1_settlement_fixture(tmp_path)
    _phase1_defensive_view(monkeypatch, fx, [("token", "reservation_ref.owner_occurrence_ref",
        f"provider_attempt_{fx.request.model_call_id}_{suffix}")])
    _phase1_assert_settlement_equivalent(fx, phase1_settlement_a, cause_type=ValueError,
        cause_args=("native token reservation differs from exact Gateway physical Provider ordinal",))


_PHASE1_OTHER_DIMENSIONS = ("logical_tool_calls", "research_rounds", "repair_attempts",
    "generation_attempts", "replan_attempts", "true_retry_attempts")


@pytest.mark.parametrize("field,value", [("logical_model_calls", 0), ("logical_model_calls", 2),
    ("model_tokens_total", 1)] + [(field, 1) for field in _PHASE1_OTHER_DIMENSIONS])
def test_phase1_V08_synthetic_logical_requested_exact_vector(tmp_path, monkeypatch, phase1_settlement_a, field, value):
    fx = _phase1_settlement_fixture(tmp_path)
    _phase1_defensive_view(monkeypatch, fx, [("logical", f"requested.{field}", value)])
    _phase1_assert_settlement_equivalent(fx, phase1_settlement_a, cause_type=ValueError,
        cause_args=("native requested allowance differs from exact frozen request",))


@pytest.mark.parametrize("field,value", [("model_tokens_total", 1), ("model_tokens_total", 66),
    ("logical_model_calls", 1)] + [(field, 1) for field in _PHASE1_OTHER_DIMENSIONS])
def test_phase1_V09_synthetic_token_requested_outside_exact_contract(tmp_path, monkeypatch, phase1_settlement_a, field, value):
    fx = _phase1_settlement_fixture(tmp_path)
    _phase1_defensive_view(monkeypatch, fx, [("token", f"requested.{field}", value)])
    _phase1_assert_settlement_equivalent(fx, phase1_settlement_a, cause_type=ValueError,
        cause_args=("native requested allowance differs from exact frozen request",))


@pytest.mark.parametrize("routed_max_tokens,requested", [(1, 2), (32, 33), (64, 65)])
def test_phase1_V09_native_minimum_maximum_and_gateway_clamp(tmp_path, phase1_settlement_a, routed_max_tokens, requested):
    fx = _phase1_settlement_fixture(tmp_path, routed_max_tokens=routed_max_tokens)
    assert fx.manager.resolve_reservation(fx.token_id).requested.model_tokens_total == requested
    _phase1_assert_settlement_equivalent(fx, phase1_settlement_a)


@pytest.mark.parametrize("same_operation", [True, False])
def test_phase1_V10_native_active_provider_scan_uses_exact_operation(tmp_path, phase1_settlement_a, same_operation):
    fx = _phase1_settlement_fixture(tmp_path)
    active = fx.request.model_copy(deep=True)
    if not same_operation:
        active.model_call_id = "another-operation"
    active.extra["provider_attempt_id"] = f"provider_attempt_{active.model_call_id}_2_bbbbbbbbbbbb"
    active_id = fx.adapter.reserve_provider_attempt(active)
    assert fx.manager.resolve_reservation(active_id).status == "active"
    _phase1_assert_settlement_equivalent(fx, phase1_settlement_a,
        cause_type=ValueError if same_operation else None,
        cause_args=("unreconciled native Provider attempt is still held",) if same_operation else None)


@pytest.mark.parametrize("field", ["model_call_id", "task_id", "run_id"])
def test_phase1_V11_response_identity_precedes_owner_lookup(tmp_path, monkeypatch, phase1_settlement_a, field):
    fx = _phase1_settlement_fixture(tmp_path)
    setattr(fx.response, field, "another-identity")
    def forbidden(*_args, **_kwargs):
        raise AssertionError("owner must not be resolved before response identity")
    monkeypatch.setattr(fx.composer, "_budget_owner", forbidden)
    _phase1_assert_settlement_equivalent(fx, phase1_settlement_a, cause_type=ValueError,
        cause_args=("response invocation differs from request",))


@pytest.mark.parametrize("role,field,value", [("logical", "logical_model_calls", 0),
    ("logical", "logical_model_calls", 2), ("logical", "model_tokens_total", 1),
    ("token", "model_tokens_total", 23), ("token", "logical_model_calls", 1)]
    + [(role, field, 1) for role in ("logical", "token") for field in _PHASE1_OTHER_DIMENSIONS])
def test_phase1_V12_synthetic_actual_exact_vectors(tmp_path, monkeypatch, phase1_settlement_a, role, field, value):
    fx = _phase1_settlement_fixture(tmp_path)
    _phase1_defensive_view(monkeypatch, fx, [(role, f"actual.{field}", value)])
    _phase1_assert_settlement_equivalent(fx, phase1_settlement_a, cause_type=ValueError,
        cause_args=("native actual usage differs from exact response settlement",))


@pytest.mark.parametrize("total,expected", [(0, 0), (-7, 0), (22, 22), (180, 180)])
def test_phase1_V13_native_complete_usage_preserves_zero_clamp_and_full_total(tmp_path, phase1_settlement_a, total, expected):
    fx = _phase1_settlement_fixture(tmp_path, usage=(17, 5, total))
    assert fx.manager.resolve_reservation(fx.token_id).actual.model_tokens_total == expected
    _phase1_assert_settlement_equivalent(fx, phase1_settlement_a)


@pytest.mark.parametrize("complete,total,prompt,completion,expected", [
    (False, 22, 0, 0, 65), (False, 22, 40, 25, 65), (False, 22, 70, 20, 90),
    (True, None, None, None, 65), (True, None, -20, 5, 65), (True, None, 70, 20, 90),
    (False, None, None, 7, 65), (None, 22, None, None, 22)])
def test_phase1_V14_native_incomplete_usage_and_lower_bound_fallback(tmp_path, phase1_settlement_a,
        complete, total, prompt, completion, expected):
    fx = _phase1_settlement_fixture(tmp_path, usage=(prompt, completion, total), usage_complete=complete)
    assert fx.manager.resolve_reservation(fx.token_id).actual.model_tokens_total == expected
    _phase1_assert_settlement_equivalent(fx, phase1_settlement_a)


def test_phase1_V15_native_provider_token_overrun_retains_honest_actual(tmp_path, phase1_settlement_a):
    fx = _phase1_settlement_fixture(tmp_path, usage=(100, 80, 180))
    token = fx.manager.resolve_reservation(fx.token_id)
    assert token.requested.model_tokens_total == 65
    assert token.actual.model_tokens_total == 180
    assert fx.manager.canonical_state().used.model_tokens_total == 180
    _phase1_assert_settlement_equivalent(fx, phase1_settlement_a)


@pytest.mark.parametrize("case,expected_type,expected_args", [
    ("logical_key_missing", KeyError, ("canonical_budget_logical_reservation_id",)),
    ("both_keys_missing", KeyError, ("canonical_budget_logical_reservation_id",)),
    ("token_key_missing", KeyError, ("canonical_budget_token_reservation_id",)),
    ("logical_blank", ValueError, ("reservation_id is required",)),
    ("token_blank", ValueError, ("reservation_id is required",)),
    ("both_blank", ValueError, ("reservation_id is required",)),
    ("logical_missing_token_key_missing", KeyError, ("missing-logical",)),
    ("logical_blank_token_key_missing", ValueError, ("reservation_id is required",)),
    ("logical_missing_token_blank", KeyError, ("missing-logical",))])
def test_phase1_V16_V17_V18_native_metadata_and_multiple_fault_priority(tmp_path, phase1_settlement_a,
        case, expected_type, expected_args):
    fx = _phase1_settlement_fixture(tmp_path)
    metadata = fx.response.metadata
    if case in {"logical_key_missing", "both_keys_missing"}:
        metadata.pop("canonical_budget_logical_reservation_id")
    if case in {"token_key_missing", "both_keys_missing", "logical_missing_token_key_missing",
                "logical_blank_token_key_missing"}:
        metadata.pop("canonical_budget_token_reservation_id")
    if case in {"logical_blank", "both_blank", "logical_blank_token_key_missing"}:
        metadata["canonical_budget_logical_reservation_id"] = "  "
    if case in {"token_blank", "both_blank", "logical_missing_token_blank"}:
        metadata["canonical_budget_token_reservation_id"] = "\t"
    if case in {"logical_missing_token_key_missing", "logical_missing_token_blank"}:
        metadata["canonical_budget_logical_reservation_id"] = "missing-logical"
    _phase1_assert_settlement_equivalent(fx, phase1_settlement_a,
        cause_type=expected_type, cause_args=expected_args)


@pytest.mark.parametrize("token_fault", ["blank", "missing_key"])
def test_phase1_V18_native_committed_bad_ledger_precedes_later_metadata_fault(tmp_path, phase1_settlement_a, token_fault):
    from apps.enterprise_document.services.agent_rag_v1.budget_manager import CanonicalBudgetIntegrityError
    fx = _phase1_settlement_fixture(tmp_path)
    with fx.ledger.open("ab") as handle:
        handle.write(b"bad committed JSON\n")
    if token_fault == "blank":
        fx.response.metadata["canonical_budget_token_reservation_id"] = " "
    else:
        fx.response.metadata.pop("canonical_budget_token_reservation_id")
    errors = _phase1_assert_settlement_equivalent(fx, phase1_settlement_a,
        cause_type=CanonicalBudgetIntegrityError, state_check=False)
    assert type(errors[0].__cause__.__cause__) is json.JSONDecodeError


def test_phase1_V19_synthetic_invalid_logical_identity_does_not_precede_token_lookup(tmp_path, monkeypatch, phase1_settlement_a):
    fx = _phase1_settlement_fixture(tmp_path)
    _phase1_defensive_view(monkeypatch, fx, [("logical", "reservation_ref.task_id", "foreign-task")])
    fx.response.metadata["canonical_budget_token_reservation_id"] = "missing-token"
    _phase1_assert_settlement_equivalent(fx, phase1_settlement_a,
        cause_type=KeyError, cause_args=("missing-token",))


@pytest.mark.parametrize("required", [True, False])
@pytest.mark.parametrize("runtime_kind", ["adapter", "router", "absent", "provider_without_seam",
    "router_without_existing_provider", "foreign_task", "foreign_run", "noncanonical"])
def test_phase1_V20_owner_branches_and_original_double_wrapping(tmp_path, phase1_settlement_a, required, runtime_kind):
    from types import SimpleNamespace
    from apps.enterprise_document.services.agent_rag_v1.model_tool_budget_runtime import CanonicalInvocationBudgetRouter
    fx = _phase1_settlement_fixture(tmp_path)
    lookups = {"admission": 0, "existing": 0}
    def forbidden_admission(*_args):
        lookups["admission"] += 1
        raise AssertionError("settlement must not call admission provider")
    def existing(*_args):
        lookups["existing"] += 1
        return fx.manager
    cause_type, cause_args = None, None
    if runtime_kind == "router":
        fx.composer.model_gateway.budget_runtime = CanonicalInvocationBudgetRouter(
            forbidden_admission, existing_manager_provider=existing)
    elif runtime_kind == "absent":
        fx.composer.model_gateway.budget_runtime = None
        if required:
            cause_type = TargetClaimVerifierError
            cause_args = ("Verifier correction requires canonical Budget owner",)
    elif runtime_kind == "provider_without_seam":
        fx.composer.model_gateway.budget_runtime = SimpleNamespace(provider=forbidden_admission)
        cause_type = TargetClaimVerifierError
        cause_args = ("Verifier Router lacks existing-only canonical Budget read seam",)
    elif runtime_kind == "router_without_existing_provider":
        fx.composer.model_gateway.budget_runtime = CanonicalInvocationBudgetRouter(forbidden_admission)
        cause_type, cause_args = RuntimeError, ("existing canonical Budget provider is not configured",)
    elif runtime_kind in {"foreign_task", "foreign_run", "noncanonical"}:
        foreign = SimpleNamespace(_canonical_mode=runtime_kind != "noncanonical",
            task_id="foreign-task" if runtime_kind == "foreign_task" else fx.request.task_id,
            run_id="foreign-run" if runtime_kind == "foreign_run" else fx.request.run_id)
        fx.composer.model_gateway.budget_runtime = SimpleNamespace(budget_manager=foreign)
        cause_type = TargetClaimVerifierError
        cause_args = ("Verifier response Budget owner differs from exact Task/Run",)
    errors = _phase1_assert_settlement_equivalent(fx, phase1_settlement_a,
        required=required, cause_type=cause_type, cause_args=cause_args)
    assert lookups == {"admission": 0, "existing": 2 if runtime_kind == "router" else 0}
    if cause_type is TargetClaimVerifierError:
        for error in errors:
            assert error.__cause__.error_code == "VERIFIER_BUDGET_SETTLEMENT_UNKNOWN"
            assert error.__cause__.__cause__ is None


@pytest.mark.parametrize("fault_point", ["lookup", "lock", "read"])
def test_phase1_V21_lookup_lock_and_file_io_failure_chain(tmp_path, monkeypatch, phase1_settlement_a, fault_point):
    from apps.enterprise_document.services.agent_rag_v1 import budget_manager as module
    fx = _phase1_settlement_fixture(tmp_path)
    fault_type = LookupError if fault_point == "lookup" else OSError
    args = ("injected native lookup failure",) if fault_point == "lookup" else (5, f"injected native {fault_point} failure")
    def fail(*_args, **_kwargs):
        raise fault_type(*args)
    if fault_point == "lookup":
        monkeypatch.setattr(fx.manager, "_canonical_current_unlocked", fail)
    elif fault_point == "lock":
        monkeypatch.setattr(module.InterProcessFileLock, "__enter__", fail)
    else:
        monkeypatch.setattr(fx.manager._canonical_log, "read_complete_lines", fail)
    _phase1_assert_settlement_equivalent(fx, phase1_settlement_a,
        cause_type=fault_type, cause_args=args, state_check=False)


@pytest.mark.parametrize("case,role,path,value", [("V03", "logical", "reservation_ref.task_id", "foreign-task"),
    ("V05", "token", "terminal_budget_revision", None),
    ("V05-invalid", "token", "terminal_budget_revision", -1),
    ("V08", "logical", "requested.logical_model_calls", 2),
    ("V12", "logical", "actual.logical_model_calls", 2)])
def test_phase1_native_committed_corruption_is_separate_from_defensive_views(tmp_path, phase1_settlement_a, case, role, path, value):
    from apps.enterprise_document.services.agent_rag_v1.budget_manager import CanonicalBudgetIntegrityError
    from core.persistence.crash_safe_jsonl import canonical_json_sha256
    fx = _phase1_settlement_fixture(tmp_path)
    lines = fx.ledger.read_bytes().splitlines()
    event = json.loads(lines[-1])
    reservation_id = fx.logical_id if role == "logical" else fx.token_id
    record = next(row for row in event["state"]["reservations"]
        if row["reservation_ref"]["reservation_id"] == reservation_id)
    target = record
    fields = path.split(".")
    for field in fields[:-1]:
        target = target[field]
    target[fields[-1]] = value
    if "event_sha256" in event:
        event["event_sha256"] = canonical_json_sha256({key: value for key, value in event.items() if key != "event_sha256"})
    lines[-1] = json.dumps(event, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    fx.ledger.write_bytes(b"\n".join(lines) + b"\n")
    _phase1_assert_settlement_equivalent(fx, phase1_settlement_a,
        cause_type=CanonicalBudgetIntegrityError, state_check=False)
