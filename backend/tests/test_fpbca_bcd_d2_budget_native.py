"""Draft only until D2: existing canonical Budget append-durable/response-loss cuts."""
from hashlib import sha256
import json
import pytest

from apps.enterprise_document.services.agent_rag_v1 import BudgetManager, CanonicalBudgetIntegrityError
from apps.enterprise_document.schemas.agent_rag_v1 import CanonicalBudgetVectorSchema
from backend.tests.test_multi_agent_v3_budget_runtime import _manager, _ref, _limits, NOW


class LocalResponseLost(BaseException):
    pass


def _cold(path):
    return BudgetManager.canonical(task_id='task_1', run_id='run_1', limits=_limits(),
        max_elapsed_seconds=60, storage_path=path, policy_ref='budget-policy://tests/canonical-v1',
        policy_version='v1', clock=lambda: NOW, require_existing_account=True)


def _record(tmp_path, name, value):
    with (tmp_path / (name + '.json')).open('x', encoding='utf-8') as handle:
        json.dump(value, handle, sort_keys=True, indent=2)


@pytest.mark.parametrize('operation', ['reserve', 'consume', 'release'])
def test_actual_budget_journal_commit_response_lost_reopen_exact_once(tmp_path, monkeypatch, operation):
    owner = _manager(tmp_path)
    requested = CanonicalBudgetVectorSchema(logical_model_calls=1, model_tokens_total=20)
    actual = CanonicalBudgetVectorSchema(logical_model_calls=1, model_tokens_total=12)
    if operation != 'reserve':
        owner.reserve(_ref('cut'), requested, expected_revision=0)
    before = owner.canonical_state()
    before_bytes = owner.storage_path.read_bytes()
    original = owner._canonical_log.append_serialized
    committed = []
    def append_then_lose_response(serialized):
        original(serialized)
        committed.append(sha256(owner.storage_path.read_bytes()).hexdigest())
        raise LocalResponseLost(operation)
    with monkeypatch.context() as patch:
        patch.setattr(owner._canonical_log, 'append_serialized', append_then_lose_response)
        with pytest.raises(LocalResponseLost, match=operation):
            if operation == 'reserve':
                owner.reserve(_ref('cut'), requested, expected_revision=before.revision)
            elif operation == 'consume':
                owner.consume('cut', actual, expected_revision=before.revision)
            else:
                owner.release('cut', expected_revision=before.revision)
    cut_bytes = owner.storage_path.read_bytes()
    assert cut_bytes.startswith(before_bytes) and len(committed) == 1
    reopened = _cold(owner.storage_path)
    state = reopened.canonical_state()
    assert state.revision == before.revision + 1
    assert state.limits == before.limits and state.policy_ref == before.policy_ref
    reservation = reopened.resolve_reservation('cut')
    assert reservation.reservation_ref == _ref('cut')
    replay_appends = []
    original_replay_append = reopened._canonical_log.append_serialized
    def measured_replay_append(serialized):
        replay_appends.append(serialized)
        return original_replay_append(serialized)
    with monkeypatch.context() as patch:
        patch.setattr(reopened._canonical_log, 'append_serialized', measured_replay_append)
        if operation == 'reserve':
            assert reservation.status == 'active' and state.active_reserved == requested
            assert reopened.reserve(_ref('cut'), requested, expected_revision=before.revision) == reservation
        elif operation == 'consume':
            assert reservation.status == 'consumed' and state.used == actual
            assert state.active_reserved.is_zero()
            replay = reopened.consume('cut', actual, expected_revision=before.revision)
            assert replay.terminal_budget_revision == state.revision
        else:
            assert reservation.status == 'released' and state.used.is_zero() and state.active_reserved.is_zero()
            replay = reopened.release('cut', expected_revision=before.revision)
            assert replay.terminal_budget_revision == state.revision
    assert replay_appends == []
    assert owner.storage_path.read_bytes() == cut_bytes and reopened.canonical_state() == state
    _record(tmp_path, 'budget-native-' + operation, {'operation': operation,
        'before': before.model_dump(mode='json'), 'after': state.model_dump(mode='json'),
        'reservation': reservation.model_dump(mode='json'),
        'before_journal_sha256': sha256(before_bytes).hexdigest(),
        'committed_journal_sha256': sha256(cut_bytes).hexdigest(),
        'actual_native_append_count': len(committed), 'replay_append_count': len(replay_appends),
        'proof_scope': 'native local accounting owner; not Provider or business completion'})


@pytest.mark.parametrize('tail', [b'{"uncommitted":', b'{"invalid":}\n', b'\xff\n'])
def test_budget_native_torn_suffix_vs_committed_corruption(tmp_path, tail):
    owner = _manager(tmp_path)
    state = owner.canonical_state()
    committed = owner.storage_path.read_bytes()
    with owner.storage_path.open('ab') as handle:
        handle.write(tail)
    transport_before = owner.storage_path.read_bytes()
    if not tail.endswith(b'\n'):
        assert _cold(owner.storage_path).canonical_state() == state
        assert owner.storage_path.read_bytes() == committed
        disposition = 'TORN_UNCOMMITTED_SUFFIX_REMOVED_COMMITTED_PREFIX_PRESERVED'
    else:
        with pytest.raises((CanonicalBudgetIntegrityError, ValueError)):
            _cold(owner.storage_path)
        assert owner.storage_path.read_bytes() == transport_before
        disposition = 'COMPLETE_FRAME_CORRUPTION_REJECTED_NO_REPAIR'
    _record(tmp_path, 'budget-native-tail', {'tail_hex': tail.hex(), 'disposition': disposition,
        'committed_prefix_sha256': sha256(committed).hexdigest(),
        'before_transport_sha256': sha256(transport_before).hexdigest(),
        'after_transport_sha256': sha256(owner.storage_path.read_bytes()).hexdigest(),
        'semantic_appends': 0})
