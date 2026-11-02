"""Draft only until D2: native Support commit response loss and journal boundaries."""
from hashlib import sha256
import json
from unittest.mock import patch
import pytest

from core.runtime.side_effects import LogicalOperationRef, CommittedResultResolutionStatus
from apps.enterprise_document.schemas.agent_rag_v1.lifecycle import RunLifecycleStatus
from apps.enterprise_document.schemas.agent_rag_v1.claim_support import ClaimEvidenceSupportRecordSchema
from apps.enterprise_document.services.agent_rag_v1.claim_support_runtime import ClaimSupportIntegrityError
from backend.tests import local_verifier_recovery_helpers as native
from backend.tests.test_fpbca_bcd_support_crash_cuts import _fresh, _reopen, _evaluate, _evaluate_commit, NativeCut


def _record(path, value):
    with path.open('x', encoding='utf-8') as handle:
        json.dump(value, handle, sort_keys=True, indent=2)


def test_support_native_commit_response_loss_cancelled_cold_read_never_ack_or_dispatch(tmp_path):
    fx, target, reply = _fresh(tmp_path / 'native')
    outcome = _evaluate(fx, target)
    owner = fx.support_composition.runtime
    before = owner.ledger_path.read_bytes()
    producer_events = []
    original = owner.log.append_serialized
    def append_then_lose(serialized):
        producer_events.append(json.loads(serialized))
        original(serialized)
        raise NativeCut('support_commit_durable_response_lost')
    with patch.object(owner.log, 'append_serialized', append_then_lose):
        with pytest.raises(NativeCut):
            owner.commit_support(outcome.candidate, fx.context)
    assert len(producer_events) == 1
    event = producer_events[0]
    expected = ClaimEvidenceSupportRecordSchema.model_validate(event['record'])
    assert expected.support_ref.support_semantic_fingerprint == owner.support_semantic_fingerprint(outcome.candidate)
    committed = owner.ledger_path.read_bytes()
    assert committed.startswith(before) and len(committed.splitlines()) == len(before.splitlines()) + 1
    run = fx.lifecycle.load_run(fx.context.active_lineage.run_id)
    fx.lifecycle.transition_run(run.identity.run_id, run.lifecycle_revision, RunLifecycleStatus.CANCELLED)
    cold = _reopen(fx, reply)
    coordination_path = cold.support_composition.runtime.coordination_store.log.path
    coordination_before = coordination_path.read_bytes() if coordination_path.exists() else b''
    calls = len(native.provider_rows(cold))
    forbidden_calls = []
    def forbidden(*args, **kwargs):
        forbidden_calls.append({'operation': 'forbidden_owner_mutation_or_gateway'})
        raise AssertionError('passive native committed read cannot ACK/evaluate/dispatch/commit')
    runtime = cold.support_composition.runtime
    with patch.object(runtime, '_ack_side_effect', forbidden), patch.object(runtime, '_reconcile', forbidden), \
         patch.object(runtime, 'evaluate_support_candidate', forbidden), patch.object(runtime, 'commit_support', forbidden), \
         patch.object(cold.gateway.invoker, 'invoke', forbidden):
        assert runtime.resolve_support(expected.support_ref) == expected
        resolution = runtime.resolve_committed_result(LogicalOperationRef(**event['operation_ref']), event['semantic_fingerprint'])
        assert resolution.resolution is CommittedResultResolutionStatus.COMMITTED_SAME
        assert resolution.committed_result_refs == (event['result_ref'],)
    assert forbidden_calls == []
    assert runtime.ledger_path.read_bytes() == committed
    assert (coordination_path.read_bytes() if coordination_path.exists() else b'') == coordination_before
    assert len(native.provider_rows(cold)) == calls == 1
    _record(tmp_path / 'support-native-commit-loss.json', {'cut': 'durable_append_before_response_ACK',
        'producer_event': event, 'journal_before_sha256': sha256(before).hexdigest(),
        'journal_committed_sha256': sha256(committed).hexdigest(), 'provider_count': calls,
        'cold_mutation_boundary_entries': len(forbidden_calls), 'cold_ACK_boundary_entries': len(forbidden_calls), 'cancelled': True})


@pytest.mark.parametrize('tail', [b'{"torn":', b'{"bad":}\n', b'\xff\n'])
def test_support_native_torn_suffix_and_complete_corruption(tmp_path, tail):
    fx, target, reply = _fresh(tmp_path / 'native')
    expected = _evaluate_commit(fx, target)
    owner = fx.support_composition.runtime
    prefix = owner.ledger_path.read_bytes()
    with owner.ledger_path.open('ab') as handle:
        handle.write(tail)
    transport = owner.ledger_path.read_bytes()
    if not tail.endswith(b'\n'):
        cold = _reopen(fx, reply)
        assert cold.support_composition.runtime.resolve_support(expected.support_ref) == expected
        assert owner.ledger_path.read_bytes() == prefix
        disposition = 'TORN_SUFFIX_REMOVED_EXACT_COMMITTED_PREFIX'
    else:
        with pytest.raises((ClaimSupportIntegrityError, ValueError)):
            cold = _reopen(fx, reply)
            cold.support_composition.runtime.resolve_support(expected.support_ref)
        assert owner.ledger_path.read_bytes() == transport
        disposition = 'COMPLETE_FRAME_REJECTED_WITHOUT_REPAIR'
    assert len(native.provider_rows(fx)) == 1
    _record(tmp_path / 'support-native-tail.json', {'tail_hex': tail.hex(), 'disposition': disposition,
        'prefix_sha256': sha256(prefix).hexdigest(), 'transport_before_sha256': sha256(transport).hexdigest(),
        'transport_after_sha256': sha256(owner.ledger_path.read_bytes()).hexdigest(), 'semantic_appends': 0})
