"""Wave7-only native Support cut controls; authored Fake responses, no business proof.

The historical helper seeds a different two-member Support identity.  These
controls use a fresh one-member identity through the actual composition/runtime,
and compare every mutation against the seed rather than deleting its journal.
"""
from concurrent.futures import ThreadPoolExecutor
from hashlib import sha256
import json
from threading import Barrier
from unittest.mock import patch

import pytest

from core.hashing import canonical_sha256
from apps.enterprise_document.schemas.agent_rag_v1.lifecycle import RunLifecycleStatus
from apps.enterprise_document.schemas.agent_rag_v1.claim_support import SupportRelation
from apps.enterprise_document.services.agent_rag_v1.lifecycle_runtime import LifecycleCurrentnessError
from apps.enterprise_document.services.agent_rag_v1.claim_support_composition import (
    SUPPORT_SEMANTIC_CONTRACT, TARGET_SET_CONTRACT, TargetClaimSupportError,
)
from apps.enterprise_document.services.agent_rag_v1.claim_support_runtime import (
    ClaimSupportCurrentnessError, ClaimSupportIdentityCollision,
)
from backend.tests import local_verifier_recovery_helpers as native


class NativeCut(BaseException):
    """Simulated process loss: bypass ordinary technical-failure persistence."""


def _fresh(root):
    fx = native.create_fixture(root)
    composition = fx.support_composition
    composition.model_gateway = fx.gateway
    target = composition.runtime.materialize_evidence_target_set(
        fx.support.claim_support_subject_ref,
        (fx.support.evidence_target_set_ref.evidence_member_refs[-1],),
        TARGET_SET_CONTRACT,
    )
    assert target != fx.support.evidence_target_set_ref
    reply = {"relation": "SUPPORTED", "supporting_refs": list(target.evidence_member_refs),
             "contradicting_refs": [], "certainty_relation": "ALIGNED",
             "certainty_reason_codes": ["AUTHORED_FAKE_SUPPORT_CUT"]}
    fx.client.script = [reply]
    assert composition.runtime.resolve_support_by_identity(
        target.claim_support_subject_ref, target, SUPPORT_SEMANTIC_CONTRACT) is None
    return fx, target, reply


def _reopen(fx, reply):
    reopened = native.reopen_fixture(fx.root, script=[reply])
    reopened.support_composition.model_gateway = reopened.gateway
    return reopened


def _evaluate(fx, target):
    return fx.support_composition.runtime.evaluate_support_candidate(
        target.claim_support_subject_ref, target, SUPPORT_SEMANTIC_CONTRACT, fx.context)


def _evaluate_commit(fx, target):
    outcome = _evaluate(fx, target)
    return outcome.existing_record if outcome.existing_record is not None else (
        fx.support_composition.runtime.commit_support(outcome.candidate, fx.context))


def _receipt_material(fx, target):
    composition = fx.support_composition
    key = canonical_sha256({"subject": target.claim_support_subject_ref.model_dump(mode="json"),
                           "target": target.model_dump(mode="json"), "contract": SUPPORT_SEMANTIC_CONTRACT})
    identity = {"task_id": fx.lifecycle.load_run_identity(fx.context.active_lineage.run_id).task_id,
                "run_id": fx.context.active_lineage.run_id, "section_id": fx.claim.section_id}
    input_ref, candidate_ref = (f"artifact://claim-support/{key}/{suffix}" for suffix in ("input", "candidate"))
    return input_ref, candidate_ref, composition._read(input_ref, identity), composition._read(candidate_ref, identity)


def _trace(fx, target, cut, before):
    input_ref, candidate_ref, input_receipt, candidate_receipt = _receipt_material(fx, target)
    if input_receipt is not None:
        assert input_receipt["input_fingerprint"] == canonical_sha256(input_receipt["input_material"])
    if candidate_receipt is not None:
        assert candidate_receipt["input_fingerprint"] == input_receipt["input_fingerprint"]
    support = fx.support_composition.runtime.resolve_support_by_identity(
        target.claim_support_subject_ref, target, SUPPORT_SEMANTIC_CONTRACT)
    data = {"classification": "NATIVE_OWNER_WITH_AUTHORED_FAKE_RESPONSE", "cut": cut,
            "target": target.model_dump(mode="json"), "input_ref": input_ref, "candidate_ref": candidate_ref,
            "input_fingerprint": input_receipt["input_fingerprint"] if input_receipt else None,
            "candidate_sha256": canonical_sha256(candidate_receipt) if candidate_receipt else None,
            "support_ref": support.support_ref.model_dump(mode="json") if support else None,
            "provider_count": len(native.provider_rows(fx)), "provider_rows": native.provider_rows(fx),
            "ledger_before_sha256": sha256(before).hexdigest(),
            "ledger_after_sha256": sha256(fx.support_composition.runtime.ledger_path.read_bytes()).hexdigest()}
    with (fx.root / ("support-cut-" + cut + ".json")).open("x", encoding="utf-8") as stream:
        json.dump(data, stream, ensure_ascii=False, indent=2)


def _cut(fx, target, cut):
    composition = fx.support_composition
    if cut == "provider_success_before_candidate":
        with patch.object(composition, "parse_current_reply", side_effect=NativeCut(cut)):
            with pytest.raises(NativeCut):
                _evaluate(fx, target)
        return
    selected_type = "claim_support_input" if cut == "input_before_model" else "claim_support_candidate"
    actual_put = composition.store.put_json
    def put_then_stop(value, **kwargs):
        result = actual_put(value, **kwargs)
        if kwargs.get("artifact_type") == selected_type:
            raise NativeCut(cut)
        return result
    with patch.object(composition.store, "put_json", put_then_stop):
        with pytest.raises(NativeCut):
            _evaluate(fx, target)


@pytest.mark.parametrize("cut,expected_calls", [("input_before_model", 0), ("provider_success_before_candidate", 1)])
def test_native_support_unresolved_input_reopen_never_redispatches(tmp_path, cut, expected_calls):
    fx, target, reply = _fresh(tmp_path / cut)
    before = fx.support_composition.runtime.ledger_path.read_bytes()
    _cut(fx, target, cut)
    reopened = _reopen(fx, reply)
    _, _, input_receipt, candidate = _receipt_material(reopened, target)
    assert input_receipt is not None and candidate is None
    with pytest.raises(TargetClaimSupportError) as failure:
        _evaluate(reopened, target)
    assert failure.value.error_code == "CLAIM_SUPPORT_INDETERMINATE_EXECUTION"
    assert len(native.provider_rows(reopened)) == expected_calls
    assert reopened.support_composition.runtime.ledger_path.read_bytes() == before
    _trace(reopened, target, cut, before)


def test_native_support_candidate_before_commit_exact_reopen_reconciliation(tmp_path):
    fx, target, reply = _fresh(tmp_path / "candidate")
    before = fx.support_composition.runtime.ledger_path.read_bytes()
    _cut(fx, target, "candidate_before_support_commit")
    assert fx.support_composition.runtime.ledger_path.read_bytes() == before
    reopened = _reopen(fx, reply)
    outcome = _evaluate(reopened, target)
    assert outcome.candidate is not None and outcome.existing_record is None
    _, _, input_receipt, candidate_receipt = _receipt_material(reopened, target)
    assert candidate_receipt["candidate"] == outcome.candidate.model_dump(mode="json")
    assert candidate_receipt["input_fingerprint"] == input_receipt["input_fingerprint"]
    record = reopened.support_composition.runtime.commit_support(outcome.candidate, reopened.context)
    assert record.support_ref.support_semantic_fingerprint == reopened.support_composition.runtime.support_semantic_fingerprint(outcome.candidate)
    committed = reopened.support_composition.runtime.ledger_path.read_bytes()
    again = _reopen(reopened, reply)
    assert _evaluate_commit(again, target) == record
    assert again.support_composition.runtime.ledger_path.read_bytes() == committed
    assert len(native.provider_rows(again)) == 1
    _trace(again, target, "candidate_before_support_commit", before)


def test_native_support_committed_read_and_certainty_are_exact_no_dispatch(tmp_path):
    fx, target, reply = _fresh(tmp_path / "committed")
    before = fx.support_composition.runtime.ledger_path.read_bytes()
    record = _evaluate_commit(fx, target)
    committed = fx.support_composition.runtime.ledger_path.read_bytes()
    reopened = _reopen(fx, reply)
    assert reopened.support_composition.runtime.resolve_support(record.support_ref) == record
    assert _evaluate_commit(reopened, target) == record
    observation = reopened.support_composition.resolve_certainty_observation(
        support_record=record, operation_context=reopened.context)
    assert observation.certainty_relation == "ALIGNED"
    assert observation.physical_support_receipt_ref == _receipt_material(reopened, target)[1]
    assert len(native.provider_rows(reopened)) == 1
    assert reopened.support_composition.runtime.ledger_path.read_bytes() == committed
    _trace(reopened, target, "committed_read", before)


@pytest.mark.parametrize("cut", ["input_before_model", "provider_success_before_candidate", "candidate_before_support_commit"])
def test_native_support_cancelled_cut_receipts_never_adopt_or_redispatch(tmp_path, cut):
    fx, target, reply = _fresh(tmp_path / cut)
    _cut(fx, target, cut)
    before = fx.support_composition.runtime.ledger_path.read_bytes()
    calls = len(native.provider_rows(fx))
    run = fx.lifecycle.load_run(fx.context.active_lineage.run_id)
    fx.lifecycle.transition_run(run.identity.run_id, run.lifecycle_revision, RunLifecycleStatus.CANCELLED)
    reopened = _reopen(fx, reply)
    with pytest.raises(LifecycleCurrentnessError):
        _evaluate(reopened, target)
    _, _, _, candidate = _receipt_material(reopened, target)
    if candidate is not None:
        from apps.enterprise_document.schemas.agent_rag_v1.claim_support import ClaimEvidenceSupportCandidateSchema
        with pytest.raises(LifecycleCurrentnessError):
            reopened.support_composition.runtime.commit_support(
                ClaimEvidenceSupportCandidateSchema.model_validate(candidate["candidate"]), reopened.context)
    assert len(native.provider_rows(reopened)) == calls
    assert reopened.support_composition.runtime.ledger_path.read_bytes() == before
    _trace(reopened, target, "cancelled_" + cut, before)


def test_native_support_candidate_currentness_failure_blocks_reconcile(tmp_path):
    fx, target, reply = _fresh(tmp_path / "currentness")
    _cut(fx, target, "candidate_before_support_commit")
    reopened = _reopen(fx, reply)
    before = reopened.support_composition.runtime.ledger_path.read_bytes()
    paragraph_ref = reopened.claim.paragraph_version_ref
    current = reopened.artifact.resolve_active
    def unavailable(logical_ref):
        return None if logical_ref == paragraph_ref.logical_ref else current(logical_ref)
    with patch.object(reopened.artifact, "resolve_active", unavailable):
        with pytest.raises(ClaimSupportCurrentnessError):
            _evaluate(reopened, target)
    assert reopened.support_composition.runtime.ledger_path.read_bytes() == before
    assert len(native.provider_rows(reopened)) == 1
    _trace(reopened, target, "currentness", before)


def test_native_support_same_identity_conflict_preserves_commit(tmp_path):
    fx, target, _ = _fresh(tmp_path / "conflict")
    outcome = _evaluate(fx, target)
    record = fx.support_composition.runtime.commit_support(outcome.candidate, fx.context)
    before = fx.support_composition.runtime.ledger_path.read_bytes()
    conflicting = outcome.candidate.model_copy(update={"relation": SupportRelation.NOT_SUPPORTED, "supporting_refs": ()})
    with pytest.raises(ClaimSupportIdentityCollision):
        fx.support_composition.runtime.commit_support(conflicting, fx.context)
    assert fx.support_composition.runtime.resolve_support(record.support_ref) == record
    assert fx.support_composition.runtime.ledger_path.read_bytes() == before
    assert len(native.provider_rows(fx)) == 1
    _trace(fx, target, "conflict", before)


def test_native_support_independent_owners_concurrent_single_dispatch_commit(tmp_path):
    fx, target, reply = _fresh(tmp_path / "concurrent")
    other = _reopen(fx, reply)
    before = fx.support_composition.runtime.ledger_path.read_bytes()
    barrier = Barrier(2, timeout=10)
    def run(owner):
        barrier.wait()
        return _evaluate_commit(owner, target)
    with ThreadPoolExecutor(max_workers=2) as pool:
        one, two = pool.submit(run, fx), pool.submit(run, other)
        result = one.result(timeout=30)
        assert two.result(timeout=30) == result
    assert len(native.provider_rows(fx)) == 1
    assert len(fx.support_composition.runtime.ledger_path.read_text(encoding="utf-8").splitlines()) == len(before.decode().splitlines()) + 1
    _trace(fx, target, "concurrent", before)
