"""Fresh observation copies of unchanged original local native controls.
Original owner calls/assertions remain intact. No production implementation.
"""
import importlib as _fpbca_importlib
import pytest as _fpbca_pytest
from fpbca_final65_d2_native_capture import RemainingNativeCapture as _FPBCA_RemainingNativeCapture
_fpbca_base = _fpbca_importlib.import_module('test_fpbca_bcd_tool_native_cuts')
for _fpbca_name, _fpbca_value in vars(_fpbca_base).items():
    if not _fpbca_name.startswith('__') and not _fpbca_name.startswith('test_'):
        globals().setdefault(_fpbca_name, _fpbca_value)
_FPBCA_DECLARATION = {'hooks': [{'path': 'backend/apps/enterprise_document/services/agent_rag_v1/tool_store.py', 'sha256': 'f22eeee893c19ffb158abf07abab9a84ac291f5ce0067bb50d643fd7aef56ae4', 'module': 'apps.enterprise_document.services.agent_rag_v1.tool_store', 'symbol': 'JsonlToolExecutionStore.__init__', 'line': 63, 'kind': 'COLD_CONSTRUCTOR', 'plain_method': True, 'instance_attributes': [], 'owned_file_attributes': ['path'], 'planned_export': False}, {'path': 'backend/apps/enterprise_document/services/agent_rag_v1/tool_store.py', 'sha256': 'f22eeee893c19ffb158abf07abab9a84ac291f5ce0067bb50d643fd7aef56ae4', 'module': 'apps.enterprise_document.services.agent_rag_v1.tool_store', 'symbol': 'JsonlToolExecutionStore.put', 'line': 67, 'kind': 'OWNER', 'plain_method': True, 'instance_attributes': [], 'owned_file_attributes': ['path'], 'planned_export': False}, {'path': 'backend/apps/enterprise_document/services/agent_rag_v1/tool_store.py', 'sha256': 'f22eeee893c19ffb158abf07abab9a84ac291f5ce0067bb50d643fd7aef56ae4', 'module': 'apps.enterprise_document.services.agent_rag_v1.tool_store', 'symbol': 'JsonlToolExecutionStore.get', 'line': 83, 'kind': 'OWNER', 'plain_method': True, 'instance_attributes': [], 'owned_file_attributes': ['path'], 'planned_export': False}, {'path': 'backend/apps/enterprise_document/services/agent_rag_v1/tool_gateway.py', 'sha256': '8724544b36fbc487245ec6bd2cddb82f416363e8cdcb88f7755cb5cb0d2f5761', 'module': 'apps.enterprise_document.services.agent_rag_v1.tool_gateway', 'symbol': 'ToolGateway.invoke', 'line': 133, 'kind': 'CURRENTNESS', 'plain_method': True, 'instance_attributes': [], 'owned_file_attributes': [], 'planned_export': False}, {'path': 'backend/tests/test_fpbca_bcd_tool_native_cuts.py', 'sha256': '5fa1226c9e6f9a8a4e2111a95c47b358311015d0fc9f30d2380fca21f3a89554', 'module': 'test_fpbca_bcd_tool_native_cuts', 'symbol': '_Case.__init__', 'line': 148, 'kind': 'COLD_CONSTRUCTOR', 'plain_method': True, 'instance_attributes': [], 'owned_file_attributes': [], 'planned_export': True}, {'path': 'backend/tests/test_fpbca_bcd_tool_native_cuts.py', 'sha256': '5fa1226c9e6f9a8a4e2111a95c47b358311015d0fc9f30d2380fca21f3a89554', 'module': 'test_fpbca_bcd_tool_native_cuts', 'symbol': '_CountedBackend.retrieve', 'line': 130, 'kind': 'ACTUAL_DISPATCH', 'plain_method': True, 'instance_attributes': [], 'owned_file_attributes': [], 'planned_export': True}, {'path': 'backend/apps/enterprise_document/services/agent_rag_v1/budget_manager.py', 'sha256': '903dd686a16dce9350c105c564f72e1f78ef1316ba45fa1798560d58e5b119a6', 'module': 'apps.enterprise_document.services.agent_rag_v1.budget_manager', 'symbol': 'BudgetManager._append_canonical_state_unlocked', 'line': 743, 'kind': 'BUDGET_APPEND', 'plain_method': True, 'instance_attributes': ['task_id', 'run_id'], 'owned_file_attributes': [], 'planned_export': False}], 'original_source': {'path': 'D:\\MyCode\\rag-agent-shared\\backend\\tests\\test_fpbca_bcd_tool_native_cuts.py', 'sha256': '5fa1226c9e6f9a8a4e2111a95c47b358311015d0fc9f30d2380fca21f3a89554'}, 'row': 'D2/REC-Q', 'source_refresh': 'PREPARATION_ONLY_FINAL_C2_D3_EPOCH_REBUILD_ROOT_REVIEW_REQUIRED'}
@_fpbca_pytest.fixture(scope='function', autouse=True)
def _fpbca_d2_capture(tmp_path):
    capture = _FPBCA_RemainingNativeCapture(_FPBCA_DECLARATION, tmp_path).install(namespaces=(globals(), vars(_fpbca_base)))
    try:
        yield capture
    finally:
        capture.close()

@pytest.mark.parametrize('registration', [Q1, Q2], ids=['Q1', 'Q2'])
@pytest.mark.parametrize('cut', ['backend_before', 'native_put_before', 'native_put_durable_ack_lost'])
def test_native_observed_actual_tool_loss_cuts_cold_canceled_passive(tmp_path, monkeypatch, registration, cut, _fpbca_d2_capture):
    _fpbca_d2_capture.begin_flow()
    try:
        case = _Case(tmp_path / 'native', registration, cut)
        with pytest.raises(_LocalCut):
            case.invoke()
        before = case.semantic_files()
        proof = case.durable_proofs[0] if case.durable_proofs else None
        result = _canceled_passive(monkeypatch, case, case.original_origin, proof)
        after = case.semantic_files()
        _receipt(tmp_path, case, observation=result, before=before, after=after)
        assert after == before
        expected = RECOVERED if cut == 'native_put_durable_ack_lost' else INDETERMINATE
        assert result['disposition'] == expected
        assert result['disposition'] != 'PROVEN_NO_EFFECT'
        counts = case.counts()
        entered = 0 if cut == 'backend_before' else 1
        assert counts.get('backend_entries', 0) == entered
        assert counts.get('backend_returns', 0) == entered
        assert counts.get('tool_put_attempts', 0) == entered
        assert counts.get('tool_put_inserted', 0) == int(cut == 'native_put_durable_ack_lost')
        if registration == Q1:
            assert counts.get('snapshot_persist_returns', 0) == 2 * entered
            assert counts.get('raw_put_attempts', 0) == 0
            assert case.manager.canonical_state().used.logical_tool_calls == 1
        else:
            assert counts.get('raw_put_returns', 0) == entered
            assert case.manager is None
            if proof:
                assert json.loads(proof.source_json)['reference']['run_id'] is None
        if proof:
            assert result['result'] == json.loads(proof.row_json)
            assert result['original_status'] == result['result']['status']
    finally:
        _fpbca_d2_capture.safe_emit(row='D2/REC-Q', selector='backend/tests/test_fpbca_final65_rec_q_fpbca_bcd_tool_native_cuts.py::test_native_observed_actual_tool_loss_cuts_cold_canceled_passive', local_values=locals(), original_point={'path': 'D:\\MyCode\\rag-agent-shared\\backend\\tests\\test_fpbca_bcd_tool_native_cuts.py', 'sha256': '5fa1226c9e6f9a8a4e2111a95c47b358311015d0fc9f30d2380fca21f3a89554', 'function': 'test_actual_tool_loss_cuts_cold_canceled_passive', 'line': 647, 'native_operation': 'EXACT_UNCHANGED_ORIGINAL_NATIVE_CONTROL_FLOW', 'native_refs': []}, flow={'scope': 'ACTUAL_Q1_Q2_NATIVE_GATEWAY_AND_AUTHORED_KNOWLEDGE_BACKEND; CLASSIFIER_IS_TEST_OWNED_PASSIVE_OBSERVER_NOT_PRODUCTION_RECOVERY', 'controlled_boundary': 'EXACT_ORIGINAL_TOOL_NATIVE_BACKEND_PUT_OR_COMMITTED_ACK_BOUNDARY', 'cold_reopen': True, 'exact_replay': True, 'replay_symbols': ['ToolGateway.invoke'], 'concurrent': False, 'native_local_names': ['result', 'first', 'replay', 'proof', 'observation', 'outcomes', 'counts', 'counts_before', 'counts_after'], 'dispatch_scope': 'ACTUAL_Q1_Q2_COUNTED_KNOWLEDGE_BACKEND_RETRIEVE_ENTRIES_ONLY; PROJECTION_PERSIST_IS_SEPARATE', 'field_dispositions': {'actual_settlement_count': {'status': 'SOURCE_BOUND_N_A', 'reason': 'Q2_REGISTRATION_HAS_NO_CANONICAL_BUDGET_OWNER', 'evidence_scope': 'Q2_EXACT_NATIVE_KNOWLEDGE_HANDLER_ONLY; Q1_NATIVE_TOOL_TRANSITIONS_ARE_MEASURED_SEPARATELY', 'source_decision': {'path': 'D:\\MyCode\\rag-agent-shared\\docs\\fpbca_bcd_closure_design_v0_2_20261007\\03_D_VERIFICATION_DESIGN_V0_2.md', 'sha256': '86c300eb36233b96858168d33aa2d69167de1c93c18a115b1fc4dc387b9aa085', 'authority': 'D5_PHYSICAL_MUTATION_ACCOUNTING_WHERE_RELEVANT', 'section': 'D5 required native fields WHERE RELEVANT'}}}, 'cold_symbols': ['JsonlToolExecutionStore.__init__'], 'explicit_handler_counter': ['case', 'backend_requests'], 'terminal_reservation_kinds': ['logical_tool_call'], 'settlement_scope': 'ACTUAL_Q1_CANONICAL_LOGICAL_TOOL_ACTIVE_TO_CONSUMED_OR_RELEASED_TRANSITIONS; NOT_PROVIDER_TOKEN_SETTLEMENT', 'native_tool_passive_receipt': 'TOOL_NATIVE_EXACT_FINAL_RECEIPT_CUSTODY_V1'})

@pytest.mark.parametrize('registration', [Q1, Q2], ids=['Q1', 'Q2'])
@pytest.mark.parametrize('field', ['task_id', 'run_id', 'attempt_id', 'snapshot', 'operation', 'fingerprint', 'version', 'arguments', 'call_id', 'Round_or_legacy_binding'])
def test_native_observed_passive_original_producer_binding_drift_is_indeterminate(tmp_path, monkeypatch, registration, field, _fpbca_d2_capture):
    _fpbca_d2_capture.begin_flow()
    try:
        case = _Case(tmp_path / 'native', registration)
        case.invoke()
        proof = case.winner_proof()
        requested = copy.deepcopy(case.original_origin)
        if field in {'task_id', 'run_id', 'attempt_id'}:
            requested['context'][field] += '_other'
        elif field == 'snapshot':
            requested['context']['input_snapshot_version'] += '_other'
        elif field == 'operation':
            requested['operation_id'] += '_other'
        elif field == 'fingerprint':
            requested['fingerprint'] = 'a' * 64
        elif field == 'version':
            requested['registration'] += '_other'
        elif field == 'arguments':
            requested['arguments']['authored_wrong_argument'] = True
        elif field == 'call_id':
            requested['call_id'] += '_other'
        elif registration == Q1:
            requested['research_round_ref']['attempt_id'] += '_other'
        else:
            requested['binding_scope'] = 'UNPROVEN_TARGET_BINDING'
        before = case.semantic_files()
        result = _canceled_passive(monkeypatch, case, requested, proof)
        after = case.semantic_files()
        _receipt(tmp_path, case, observation={'attack': field, **result}, before=before, after=after)
        assert result['disposition'] == INDETERMINATE
        assert after == before
    finally:
        _fpbca_d2_capture.safe_emit(row='D2/REC-Q', selector='backend/tests/test_fpbca_final65_rec_q_fpbca_bcd_tool_native_cuts.py::test_native_observed_passive_original_producer_binding_drift_is_indeterminate', local_values=locals(), original_point={'path': 'D:\\MyCode\\rag-agent-shared\\backend\\tests\\test_fpbca_bcd_tool_native_cuts.py', 'sha256': '5fa1226c9e6f9a8a4e2111a95c47b358311015d0fc9f30d2380fca21f3a89554', 'function': 'test_passive_original_producer_binding_drift_is_indeterminate', 'line': 685, 'native_operation': 'EXACT_UNCHANGED_ORIGINAL_NATIVE_CONTROL_FLOW', 'native_refs': []}, flow={'scope': 'ACTUAL_Q1_Q2_NATIVE_GATEWAY_AND_AUTHORED_KNOWLEDGE_BACKEND; CLASSIFIER_IS_TEST_OWNED_PASSIVE_OBSERVER_NOT_PRODUCTION_RECOVERY', 'controlled_boundary': 'EXACT_ORIGINAL_TOOL_NATIVE_BACKEND_PUT_OR_COMMITTED_ACK_BOUNDARY', 'cold_reopen': True, 'exact_replay': True, 'replay_symbols': ['ToolGateway.invoke'], 'concurrent': False, 'native_local_names': ['result', 'first', 'replay', 'proof', 'observation', 'outcomes', 'counts', 'counts_before', 'counts_after'], 'dispatch_scope': 'ACTUAL_Q1_Q2_COUNTED_KNOWLEDGE_BACKEND_RETRIEVE_ENTRIES_ONLY; PROJECTION_PERSIST_IS_SEPARATE', 'field_dispositions': {'actual_settlement_count': {'status': 'SOURCE_BOUND_N_A', 'reason': 'Q2_REGISTRATION_HAS_NO_CANONICAL_BUDGET_OWNER', 'evidence_scope': 'Q2_EXACT_NATIVE_KNOWLEDGE_HANDLER_ONLY; Q1_NATIVE_TOOL_TRANSITIONS_ARE_MEASURED_SEPARATELY', 'source_decision': {'path': 'D:\\MyCode\\rag-agent-shared\\docs\\fpbca_bcd_closure_design_v0_2_20261007\\03_D_VERIFICATION_DESIGN_V0_2.md', 'sha256': '86c300eb36233b96858168d33aa2d69167de1c93c18a115b1fc4dc387b9aa085', 'authority': 'D5_PHYSICAL_MUTATION_ACCOUNTING_WHERE_RELEVANT', 'section': 'D5 required native fields WHERE RELEVANT'}}}, 'cold_symbols': ['JsonlToolExecutionStore.__init__'], 'explicit_handler_counter': ['case', 'backend_requests'], 'terminal_reservation_kinds': ['logical_tool_call'], 'settlement_scope': 'ACTUAL_Q1_CANONICAL_LOGICAL_TOOL_ACTIVE_TO_CONSUMED_OR_RELEASED_TRANSITIONS; NOT_PROVIDER_TOKEN_SETTLEMENT', 'native_tool_passive_receipt': 'TOOL_NATIVE_EXACT_FINAL_RECEIPT_CUSTODY_V1'})

@pytest.mark.parametrize('registration', [Q1, Q2], ids=['Q1', 'Q2'])
@pytest.mark.parametrize('field', ['call_id', 'fingerprint', 'version', 'snapshot', 'source_refs', 'raw_or_bundle_ref', 'data_ids', 'status', 'physical_refs', 'budget_refs', 'quality', 'data_type', 'error'])
def test_native_observed_passive_full_native_tuple_drift_is_indeterminate(tmp_path, monkeypatch, registration, field, _fpbca_d2_capture):
    _fpbca_d2_capture.begin_flow()
    try:
        case = _Case(tmp_path / 'native', registration)
        case.invoke()
        proof = case.winner_proof()
        row = json.loads(proof.row_json)
        if field == 'call_id':
            row['call_id'] += '_other'
        elif field == 'fingerprint':
            row['tool_semantic_fingerprint'] = 'b' * 64
        elif field == 'version':
            row['provenance']['tool_version'] += '_other'
        elif field == 'snapshot':
            row['provenance']['input_snapshot_version'] += '_other'
        elif field == 'source_refs':
            row['provenance']['source_refs'] = ['source://authored-wrong-proof']
        elif field == 'raw_or_bundle_ref':
            if registration == Q1:
                row['data']['evidence_bundle_handle'] += '_other'
            else:
                row['provenance']['raw_artifact_ref'] += '_other'
        elif field == 'data_ids':
            row['data']['accepted_evidence_ids' if registration == Q1 else 'evidence_ids'] = ['E_WRONG']
        elif field == 'status':
            row['status'] = ToolResultStatus.PARTIAL.value
        elif field == 'physical_refs':
            row['physical_attempt_refs'] = ['tool_attempt:authored-wrong-proof']
        elif field == 'budget_refs':
            if registration == Q1:
                row['budget_reservation_ref'] = None
                row['budget_settlement'] = None
            else:
                row['data']['unproven_budget_authority'] = True
        elif field == 'quality':
            row['quality']['warnings'] = ['authored drift']
        elif field == 'error':
            row['status'] = ToolResultStatus.PERMANENT_ERROR.value
            row['error']['error_code'] = 'D2_AUTHORED_ERROR_TUPLE_DRIFT'
        else:
            row['data_type'] += '_other'
        attacked = NormalizedToolResultSchema.model_validate(row)
        case.tool_log.write_bytes(_json_bytes(attacked.model_dump(mode='json')) + b'\n')
        before = case.semantic_files()
        result = _canceled_passive(monkeypatch, case, case.original_origin, proof)
        after = case.semantic_files()
        _receipt(tmp_path, case, observation={'attack': field, **result}, before=before, after=after)
        assert result['disposition'] == INDETERMINATE
        assert after == before
    finally:
        _fpbca_d2_capture.safe_emit(row='D2/REC-Q', selector='backend/tests/test_fpbca_final65_rec_q_fpbca_bcd_tool_native_cuts.py::test_native_observed_passive_full_native_tuple_drift_is_indeterminate', local_values=locals(), original_point={'path': 'D:\\MyCode\\rag-agent-shared\\backend\\tests\\test_fpbca_bcd_tool_native_cuts.py', 'sha256': '5fa1226c9e6f9a8a4e2111a95c47b358311015d0fc9f30d2380fca21f3a89554', 'function': 'test_passive_full_native_tuple_drift_is_indeterminate', 'line': 718, 'native_operation': 'EXACT_UNCHANGED_ORIGINAL_NATIVE_CONTROL_FLOW', 'native_refs': []}, flow={'scope': 'ACTUAL_Q1_Q2_NATIVE_GATEWAY_AND_AUTHORED_KNOWLEDGE_BACKEND; CLASSIFIER_IS_TEST_OWNED_PASSIVE_OBSERVER_NOT_PRODUCTION_RECOVERY', 'controlled_boundary': 'EXACT_ORIGINAL_TOOL_NATIVE_BACKEND_PUT_OR_COMMITTED_ACK_BOUNDARY', 'cold_reopen': True, 'exact_replay': True, 'replay_symbols': ['ToolGateway.invoke'], 'concurrent': False, 'native_local_names': ['result', 'first', 'replay', 'proof', 'observation', 'outcomes', 'counts', 'counts_before', 'counts_after'], 'dispatch_scope': 'ACTUAL_Q1_Q2_COUNTED_KNOWLEDGE_BACKEND_RETRIEVE_ENTRIES_ONLY; PROJECTION_PERSIST_IS_SEPARATE', 'field_dispositions': {'actual_settlement_count': {'status': 'SOURCE_BOUND_N_A', 'reason': 'Q2_REGISTRATION_HAS_NO_CANONICAL_BUDGET_OWNER', 'evidence_scope': 'Q2_EXACT_NATIVE_KNOWLEDGE_HANDLER_ONLY; Q1_NATIVE_TOOL_TRANSITIONS_ARE_MEASURED_SEPARATELY', 'source_decision': {'path': 'D:\\MyCode\\rag-agent-shared\\docs\\fpbca_bcd_closure_design_v0_2_20261007\\03_D_VERIFICATION_DESIGN_V0_2.md', 'sha256': '86c300eb36233b96858168d33aa2d69167de1c93c18a115b1fc4dc387b9aa085', 'authority': 'D5_PHYSICAL_MUTATION_ACCOUNTING_WHERE_RELEVANT', 'section': 'D5 required native fields WHERE RELEVANT'}}}, 'cold_symbols': ['JsonlToolExecutionStore.__init__'], 'explicit_handler_counter': ['case', 'backend_requests'], 'terminal_reservation_kinds': ['logical_tool_call'], 'settlement_scope': 'ACTUAL_Q1_CANONICAL_LOGICAL_TOOL_ACTIVE_TO_CONSUMED_OR_RELEASED_TRANSITIONS; NOT_PROVIDER_TOKEN_SETTLEMENT', 'native_tool_passive_receipt': 'TOOL_NATIVE_EXACT_FINAL_RECEIPT_CUSTODY_V1'})

@pytest.mark.parametrize('registration', [Q1, Q2], ids=['Q1', 'Q2'])
@pytest.mark.parametrize('attack', ['missing', 'complete_corruption'])
def test_native_observed_passive_source_absence_or_corruption_is_indeterminate(tmp_path, monkeypatch, registration, attack, _fpbca_d2_capture):
    _fpbca_d2_capture.begin_flow()
    try:
        case = _Case(tmp_path / 'native', registration)
        case.invoke()
        proof = case.winner_proof()
        source = json.loads(proof.source_json)
        path = case.snapshot_store._path_for_handle(source['handle']) if registration == Q1 else case.raw_store._path_for_ref(source['reference']['artifact_ref'])
        assert path.resolve().is_relative_to(case.root.resolve())
        original_bytes = path.read_bytes()
        if attack == 'missing':
            path.unlink()
        else:
            envelope = json.loads(original_bytes)
            if registration == Q1:
                envelope['bundle']['run_id'] += '_other'
            else:
                envelope['content_base64'] = base64.b64encode(b'authored corrupt bytes').decode('ascii')
            path.write_bytes(_json_bytes(envelope) + b'\n')
        before = case.semantic_files()
        result = _canceled_passive(monkeypatch, case, case.original_origin, proof)
        after = case.semantic_files()
        _receipt(tmp_path, case, observation={'attack': attack, 'original_source_hex': original_bytes.hex(), **result}, before=before, after=after)
        assert result['disposition'] == INDETERMINATE
        assert after == before
    finally:
        _fpbca_d2_capture.safe_emit(row='D2/REC-Q', selector='backend/tests/test_fpbca_final65_rec_q_fpbca_bcd_tool_native_cuts.py::test_native_observed_passive_source_absence_or_corruption_is_indeterminate', local_values=locals(), original_point={'path': 'D:\\MyCode\\rag-agent-shared\\backend\\tests\\test_fpbca_bcd_tool_native_cuts.py', 'sha256': '5fa1226c9e6f9a8a4e2111a95c47b358311015d0fc9f30d2380fca21f3a89554', 'function': 'test_passive_source_absence_or_corruption_is_indeterminate', 'line': 772, 'native_operation': 'EXACT_UNCHANGED_ORIGINAL_NATIVE_CONTROL_FLOW', 'native_refs': []}, flow={'scope': 'ACTUAL_Q1_Q2_NATIVE_GATEWAY_AND_AUTHORED_KNOWLEDGE_BACKEND; CLASSIFIER_IS_TEST_OWNED_PASSIVE_OBSERVER_NOT_PRODUCTION_RECOVERY', 'controlled_boundary': 'EXACT_ORIGINAL_TOOL_NATIVE_BACKEND_PUT_OR_COMMITTED_ACK_BOUNDARY', 'cold_reopen': True, 'exact_replay': True, 'replay_symbols': ['ToolGateway.invoke'], 'concurrent': False, 'native_local_names': ['result', 'first', 'replay', 'proof', 'observation', 'outcomes', 'counts', 'counts_before', 'counts_after'], 'dispatch_scope': 'ACTUAL_Q1_Q2_COUNTED_KNOWLEDGE_BACKEND_RETRIEVE_ENTRIES_ONLY; PROJECTION_PERSIST_IS_SEPARATE', 'field_dispositions': {'actual_settlement_count': {'status': 'SOURCE_BOUND_N_A', 'reason': 'Q2_REGISTRATION_HAS_NO_CANONICAL_BUDGET_OWNER', 'evidence_scope': 'Q2_EXACT_NATIVE_KNOWLEDGE_HANDLER_ONLY; Q1_NATIVE_TOOL_TRANSITIONS_ARE_MEASURED_SEPARATELY', 'source_decision': {'path': 'D:\\MyCode\\rag-agent-shared\\docs\\fpbca_bcd_closure_design_v0_2_20261007\\03_D_VERIFICATION_DESIGN_V0_2.md', 'sha256': '86c300eb36233b96858168d33aa2d69167de1c93c18a115b1fc4dc387b9aa085', 'authority': 'D5_PHYSICAL_MUTATION_ACCOUNTING_WHERE_RELEVANT', 'section': 'D5 required native fields WHERE RELEVANT'}}}, 'cold_symbols': ['JsonlToolExecutionStore.__init__'], 'explicit_handler_counter': ['case', 'backend_requests'], 'terminal_reservation_kinds': ['logical_tool_call'], 'settlement_scope': 'ACTUAL_Q1_CANONICAL_LOGICAL_TOOL_ACTIVE_TO_CONSUMED_OR_RELEASED_TRANSITIONS; NOT_PROVIDER_TOKEN_SETTLEMENT', 'native_tool_passive_receipt': 'TOOL_NATIVE_EXACT_FINAL_RECEIPT_CUSTODY_V1'})

@pytest.mark.parametrize('registration', [Q1, Q2], ids=['Q1', 'Q2'])
@pytest.mark.parametrize('frame', ['torn', 'complete_json_corrupt', 'complete_utf8_corrupt', 'duplicate_identity', 'invalid_schema'])
def test_native_observed_tool_native_torn_prefix_and_complete_frame_controls(tmp_path, monkeypatch, registration, frame, _fpbca_d2_capture):
    _fpbca_d2_capture.begin_flow()
    try:
        case = _Case(tmp_path / 'native', registration)
        case.invoke()
        proof = case.winner_proof()
        if frame == 'torn':
            case.tool_log.write_bytes(proof.native_prefix + b'{"uncommitted":"\xe4')
        elif frame == 'complete_json_corrupt':
            case.tool_log.write_bytes(proof.native_prefix + b'{"broken":\n')
        elif frame == 'complete_utf8_corrupt':
            case.tool_log.write_bytes(proof.native_prefix + b'{"bad":"\xff"}\n')
        elif frame == 'duplicate_identity':
            case.tool_log.write_bytes(proof.native_prefix + proof.row_json + b'\n')
        else:
            bad = json.loads(proof.row_json)
            del bad['provenance']
            case.tool_log.write_bytes(_json_bytes(bad) + b'\n')
        before = case.semantic_files()
        result = _canceled_passive(monkeypatch, case, case.original_origin, proof)
        after = case.semantic_files()
        _receipt(tmp_path, case, observation={'frame': frame, 'transport_tail_truncated': frame == 'torn', **result}, before=before, after=after)
        if frame == 'torn':
            assert result['disposition'] == RECOVERED
            assert after['normalized-tool.jsonl'] == proof.native_prefix
            assert before['normalized-tool.jsonl'].startswith(proof.native_prefix)
            assert {key: value for key, value in before.items() if key != 'normalized-tool.jsonl'} == {key: value for key, value in after.items() if key != 'normalized-tool.jsonl'}
        else:
            assert result['disposition'] == INDETERMINATE
            assert after == before
    finally:
        _fpbca_d2_capture.safe_emit(row='D2/REC-Q', selector='backend/tests/test_fpbca_final65_rec_q_fpbca_bcd_tool_native_cuts.py::test_native_observed_tool_native_torn_prefix_and_complete_frame_controls', local_values=locals(), original_point={'path': 'D:\\MyCode\\rag-agent-shared\\backend\\tests\\test_fpbca_bcd_tool_native_cuts.py', 'sha256': '5fa1226c9e6f9a8a4e2111a95c47b358311015d0fc9f30d2380fca21f3a89554', 'function': 'test_tool_native_torn_prefix_and_complete_frame_controls', 'line': 803, 'native_operation': 'EXACT_UNCHANGED_ORIGINAL_NATIVE_CONTROL_FLOW', 'native_refs': []}, flow={'scope': 'ACTUAL_Q1_Q2_NATIVE_GATEWAY_AND_AUTHORED_KNOWLEDGE_BACKEND; CLASSIFIER_IS_TEST_OWNED_PASSIVE_OBSERVER_NOT_PRODUCTION_RECOVERY', 'controlled_boundary': 'EXACT_ORIGINAL_TOOL_NATIVE_BACKEND_PUT_OR_COMMITTED_ACK_BOUNDARY', 'cold_reopen': True, 'exact_replay': True, 'replay_symbols': ['ToolGateway.invoke'], 'concurrent': False, 'native_local_names': ['result', 'first', 'replay', 'proof', 'observation', 'outcomes', 'counts', 'counts_before', 'counts_after'], 'dispatch_scope': 'ACTUAL_Q1_Q2_COUNTED_KNOWLEDGE_BACKEND_RETRIEVE_ENTRIES_ONLY; PROJECTION_PERSIST_IS_SEPARATE', 'field_dispositions': {'actual_settlement_count': {'status': 'SOURCE_BOUND_N_A', 'reason': 'Q2_REGISTRATION_HAS_NO_CANONICAL_BUDGET_OWNER', 'evidence_scope': 'Q2_EXACT_NATIVE_KNOWLEDGE_HANDLER_ONLY; Q1_NATIVE_TOOL_TRANSITIONS_ARE_MEASURED_SEPARATELY', 'source_decision': {'path': 'D:\\MyCode\\rag-agent-shared\\docs\\fpbca_bcd_closure_design_v0_2_20261007\\03_D_VERIFICATION_DESIGN_V0_2.md', 'sha256': '86c300eb36233b96858168d33aa2d69167de1c93c18a115b1fc4dc387b9aa085', 'authority': 'D5_PHYSICAL_MUTATION_ACCOUNTING_WHERE_RELEVANT', 'section': 'D5 required native fields WHERE RELEVANT'}}}, 'cold_symbols': ['JsonlToolExecutionStore.__init__'], 'explicit_handler_counter': ['case', 'backend_requests'], 'terminal_reservation_kinds': ['logical_tool_call'], 'settlement_scope': 'ACTUAL_Q1_CANONICAL_LOGICAL_TOOL_ACTIVE_TO_CONSUMED_OR_RELEASED_TRANSITIONS; NOT_PROVIDER_TOKEN_SETTLEMENT', 'native_tool_passive_receipt': 'TOOL_NATIVE_EXACT_FINAL_RECEIPT_CUSTODY_V1'})

def test_native_observed_q1_active_replay_counts_real_projection_persist_separately(tmp_path, monkeypatch, _fpbca_d2_capture):
    _fpbca_d2_capture.begin_flow()
    try:
        case = _Case(tmp_path / 'native', Q1)
        first = case.invoke()
        proof = case.winner_proof()
        before = case.semantic_files()
        counts_before = case.counts()
        alias = case.context.model_copy(update={'invocation_id': 'd2_active_replay_alias', 'execution_id': 'd2_active_replay_execution'}, deep=True)
        replay = case.invoke(alias)
        counts_after = case.counts()
        _receipt(tmp_path, case, observation={'active_counts_before': counts_before, 'active_counts_after': counts_after, 'first': first.model_dump(mode='json'), 'replay': replay.model_dump(mode='json')}, before=before, after=case.semantic_files(), phase='active_preassertions')
        assert replay == first
        assert counts_after['backend_entries'] == counts_before['backend_entries'] == 1
        assert counts_after['backend_returns'] == counts_before['backend_returns'] == 1
        assert counts_after['tool_put_attempts'] == counts_before['tool_put_attempts'] == 1
        assert counts_after['snapshot_persist_attempts'] == counts_before['snapshot_persist_attempts'] + 1
        assert counts_after['snapshot_persist_returns'] == counts_before['snapshot_persist_returns'] + 1
        assert counts_after.get('budget_reserve_attempts') == counts_before.get('budget_reserve_attempts')
        assert counts_after.get('budget_consume_attempts') == counts_before.get('budget_consume_attempts')
        assert case.manager.canonical_state().used.logical_tool_calls == 1
        assert case.semantic_files() == before
        passive = _canceled_passive(monkeypatch, case, case.original_origin, proof)
        assert passive['disposition'] == RECOVERED
        _receipt(tmp_path, case, observation={'active_replay_has_real_persist_call': True, 'active_counts_before': counts_before, 'active_counts_after': counts_after, 'passive': passive}, before=before, after=case.semantic_files())
    finally:
        _fpbca_d2_capture.safe_emit(row='D2/REC-Q', selector='backend/tests/test_fpbca_final65_rec_q_fpbca_bcd_tool_native_cuts.py::test_native_observed_q1_active_replay_counts_real_projection_persist_separately', local_values=locals(), original_point={'path': 'D:\\MyCode\\rag-agent-shared\\backend\\tests\\test_fpbca_bcd_tool_native_cuts.py', 'sha256': '5fa1226c9e6f9a8a4e2111a95c47b358311015d0fc9f30d2380fca21f3a89554', 'function': 'test_q1_active_replay_counts_real_projection_persist_separately', 'line': 837, 'native_operation': 'EXACT_UNCHANGED_ORIGINAL_NATIVE_CONTROL_FLOW', 'native_refs': []}, flow={'scope': 'ACTUAL_Q1_Q2_NATIVE_GATEWAY_AND_AUTHORED_KNOWLEDGE_BACKEND; CLASSIFIER_IS_TEST_OWNED_PASSIVE_OBSERVER_NOT_PRODUCTION_RECOVERY', 'controlled_boundary': 'EXACT_ORIGINAL_TOOL_NATIVE_BACKEND_PUT_OR_COMMITTED_ACK_BOUNDARY', 'cold_reopen': True, 'exact_replay': True, 'replay_symbols': ['ToolGateway.invoke'], 'concurrent': False, 'native_local_names': ['result', 'first', 'replay', 'proof', 'observation', 'outcomes', 'counts', 'counts_before', 'counts_after'], 'dispatch_scope': 'ACTUAL_Q1_Q2_COUNTED_KNOWLEDGE_BACKEND_RETRIEVE_ENTRIES_ONLY; PROJECTION_PERSIST_IS_SEPARATE', 'field_dispositions': {'actual_settlement_count': {'status': 'SOURCE_BOUND_N_A', 'reason': 'Q2_REGISTRATION_HAS_NO_CANONICAL_BUDGET_OWNER', 'evidence_scope': 'Q2_EXACT_NATIVE_KNOWLEDGE_HANDLER_ONLY; Q1_NATIVE_TOOL_TRANSITIONS_ARE_MEASURED_SEPARATELY', 'source_decision': {'path': 'D:\\MyCode\\rag-agent-shared\\docs\\fpbca_bcd_closure_design_v0_2_20261007\\03_D_VERIFICATION_DESIGN_V0_2.md', 'sha256': '86c300eb36233b96858168d33aa2d69167de1c93c18a115b1fc4dc387b9aa085', 'authority': 'D5_PHYSICAL_MUTATION_ACCOUNTING_WHERE_RELEVANT', 'section': 'D5 required native fields WHERE RELEVANT'}}}, 'cold_symbols': ['JsonlToolExecutionStore.__init__'], 'explicit_handler_counter': ['case', 'backend_requests'], 'terminal_reservation_kinds': ['logical_tool_call'], 'settlement_scope': 'ACTUAL_Q1_CANONICAL_LOGICAL_TOOL_ACTIVE_TO_CONSUMED_OR_RELEASED_TRANSITIONS; NOT_PROVIDER_TOKEN_SETTLEMENT', 'native_tool_passive_receipt': 'TOOL_NATIVE_EXACT_FINAL_RECEIPT_CUSTODY_V1'})

@pytest.mark.parametrize('backend_status', ['partial', 'empty'])
def test_native_observed_q2_native_non_success_disposition_is_retained(tmp_path, monkeypatch, backend_status, _fpbca_d2_capture):
    _fpbca_d2_capture.begin_flow()
    try:
        case = _Case(tmp_path / 'native', Q2, 'native_put_durable_ack_lost')
        case.backend.response = RetrievalResponseSchema(status=backend_status, evidence_ids=['E_D2_PARTIAL'] if backend_status == 'partial' else [], source_refs=['source://local/d2-partial'] if backend_status == 'partial' else [])
        with pytest.raises(_LocalCut):
            case.invoke()
        proof = case.winner_proof()
        before = case.semantic_files()
        result = _canceled_passive(monkeypatch, case, case.original_origin, proof)
        after = case.semantic_files()
        _receipt(tmp_path, case, observation={'backend_status': backend_status, **result}, before=before, after=after)
        assert result['disposition'] == RECOVERED
        assert result['original_status'] == backend_status
        assert result['result']['status'] == backend_status
        assert result['result']['status'] != ToolResultStatus.SUCCESS.value
        assert before == after
    finally:
        _fpbca_d2_capture.safe_emit(row='D2/REC-Q', selector='backend/tests/test_fpbca_final65_rec_q_fpbca_bcd_tool_native_cuts.py::test_native_observed_q2_native_non_success_disposition_is_retained', local_values=locals(), original_point={'path': 'D:\\MyCode\\rag-agent-shared\\backend\\tests\\test_fpbca_bcd_tool_native_cuts.py', 'sha256': '5fa1226c9e6f9a8a4e2111a95c47b358311015d0fc9f30d2380fca21f3a89554', 'function': 'test_q2_native_non_success_disposition_is_retained', 'line': 865, 'native_operation': 'EXACT_UNCHANGED_ORIGINAL_NATIVE_CONTROL_FLOW', 'native_refs': []}, flow={'scope': 'ACTUAL_Q1_Q2_NATIVE_GATEWAY_AND_AUTHORED_KNOWLEDGE_BACKEND; CLASSIFIER_IS_TEST_OWNED_PASSIVE_OBSERVER_NOT_PRODUCTION_RECOVERY', 'controlled_boundary': 'EXACT_ORIGINAL_TOOL_NATIVE_BACKEND_PUT_OR_COMMITTED_ACK_BOUNDARY', 'cold_reopen': True, 'exact_replay': True, 'replay_symbols': ['ToolGateway.invoke'], 'concurrent': False, 'native_local_names': ['result', 'first', 'replay', 'proof', 'observation', 'outcomes', 'counts', 'counts_before', 'counts_after'], 'dispatch_scope': 'ACTUAL_Q1_Q2_COUNTED_KNOWLEDGE_BACKEND_RETRIEVE_ENTRIES_ONLY; PROJECTION_PERSIST_IS_SEPARATE', 'field_dispositions': {'actual_settlement_count': {'status': 'SOURCE_BOUND_N_A', 'reason': 'Q2_REGISTRATION_HAS_NO_CANONICAL_BUDGET_OWNER', 'evidence_scope': 'Q2_EXACT_NATIVE_KNOWLEDGE_HANDLER_ONLY; Q1_NATIVE_TOOL_TRANSITIONS_ARE_MEASURED_SEPARATELY', 'source_decision': {'path': 'D:\\MyCode\\rag-agent-shared\\docs\\fpbca_bcd_closure_design_v0_2_20261007\\03_D_VERIFICATION_DESIGN_V0_2.md', 'sha256': '86c300eb36233b96858168d33aa2d69167de1c93c18a115b1fc4dc387b9aa085', 'authority': 'D5_PHYSICAL_MUTATION_ACCOUNTING_WHERE_RELEVANT', 'section': 'D5 required native fields WHERE RELEVANT'}}}, 'cold_symbols': ['JsonlToolExecutionStore.__init__'], 'explicit_handler_counter': ['case', 'backend_requests'], 'terminal_reservation_kinds': ['logical_tool_call'], 'settlement_scope': 'ACTUAL_Q1_CANONICAL_LOGICAL_TOOL_ACTIVE_TO_CONSUMED_OR_RELEASED_TRANSITIONS; NOT_PROVIDER_TOKEN_SETTLEMENT', 'native_tool_passive_receipt': 'TOOL_NATIVE_EXACT_FINAL_RECEIPT_CUSTODY_V1'})

@pytest.mark.parametrize('registration', [Q1, Q2], ids=['Q1', 'Q2'])
def test_native_observed_actual_two_gateway_native_miss_concurrent_observer_counts(tmp_path, monkeypatch, registration, _fpbca_d2_capture):
    _fpbca_d2_capture.begin_flow()
    try:
        case = _Case(tmp_path / 'native', registration)
        case.miss_barrier = threading.Barrier(2)
        outcomes: list[dict[str, Any]] = []
        outcome_lock = threading.Lock()

        def worker(index: int) -> None:
            context = case.context.model_copy(update={'invocation_id': f'd2_native_concurrent_{index}', 'execution_id': f'd2_native_worker_{index}'}, deep=True)
            try:
                returned = case.invoke(context)
                outcome = {'worker': index, 'return': returned.model_dump(mode='json')}
            except BaseException as exc:
                outcome = {'worker': index, 'exception': type(exc).__name__, 'message': str(exc)}
            with outcome_lock:
                outcomes.append(outcome)
        workers = [threading.Thread(target=worker, args=(index,), name=f'd2-local-tool-{index}') for index in (1, 2)]
        try:
            for thread in workers:
                thread.start()
            for thread in workers:
                thread.join(timeout=15)
        finally:
            case.miss_barrier.abort()
            for thread in workers:
                if thread.is_alive():
                    thread.join(timeout=5)
        worker_incomplete = any((thread.is_alive() for thread in workers))
        counts = case.counts()
        before = case.semantic_files()
        observation = {'worker_incomplete': worker_incomplete, 'worker_outcomes': outcomes, 'counts': counts, 'oracle': 'CONTROLLED_TWO_NATIVE_MISSES; NO_GLOBAL_AT_MOST_ONCE_DISPATCH_CLAIM'}
        _receipt(tmp_path, case, observation=observation, before=before, after=case.semantic_files(), phase='concurrent_preassertions')
        if worker_incomplete or len(outcomes) != 2:
            _receipt(tmp_path, case, observation={'disposition': 'INCOMPLETE', **observation}, before=before, after=case.semantic_files())
            pytest.fail('bounded harness observation incomplete; no canonical cancellation/retry')
        assert counts.get('native_initial_misses', 0) == 2
        assert counts.get('backend_entries', 0) == 2
        assert counts.get('backend_returns', 0) == 2
        assert counts.get('tool_put_attempts', 0) == 2
        rows = JsonlToolExecutionStore(case.tool_log).list()
        assert len(rows) == 1
        proof = case.winner_proof()
        returns = [item for item in outcomes if 'return' in item]
        errors = [item for item in outcomes if 'exception' in item]
        assert len(returns) == 1
        assert len(errors) == 1 and errors[0]['exception'] == 'ValueError'
        assert returns[0]['return'] == rows[0].model_dump(mode='json')
        assert len(rows[0].physical_attempt_refs) == 1
        if registration == Q1:
            assert case.manager.canonical_state().used.logical_tool_calls == 1
            assert counts.get('snapshot_persist_returns', 0) == 4
            assert counts.get('raw_put_attempts', 0) == 0
        else:
            assert case.manager is None
            assert counts.get('raw_put_returns', 0) == 2
        passive = _canceled_passive(monkeypatch, case, json.loads(proof.origin_json), proof)
        after = case.semantic_files()
        _receipt(tmp_path, case, observation={'passive': passive, **observation}, before=before, after=after)
        assert passive['disposition'] == RECOVERED
        assert after == before
    finally:
        _fpbca_d2_capture.safe_emit(row='D2/REC-Q', selector='backend/tests/test_fpbca_final65_rec_q_fpbca_bcd_tool_native_cuts.py::test_native_observed_actual_two_gateway_native_miss_concurrent_observer_counts', local_values=locals(), original_point={'path': 'D:\\MyCode\\rag-agent-shared\\backend\\tests\\test_fpbca_bcd_tool_native_cuts.py', 'sha256': '5fa1226c9e6f9a8a4e2111a95c47b358311015d0fc9f30d2380fca21f3a89554', 'function': 'test_actual_two_gateway_native_miss_concurrent_observer_counts', 'line': 889, 'native_operation': 'EXACT_UNCHANGED_ORIGINAL_NATIVE_CONTROL_FLOW', 'native_refs': []}, flow={'scope': 'ACTUAL_Q1_Q2_NATIVE_GATEWAY_AND_AUTHORED_KNOWLEDGE_BACKEND; CLASSIFIER_IS_TEST_OWNED_PASSIVE_OBSERVER_NOT_PRODUCTION_RECOVERY', 'controlled_boundary': 'EXACT_ORIGINAL_TOOL_NATIVE_BACKEND_PUT_OR_COMMITTED_ACK_BOUNDARY', 'cold_reopen': True, 'exact_replay': True, 'replay_symbols': ['ToolGateway.invoke'], 'concurrent': True, 'native_local_names': ['result', 'first', 'replay', 'proof', 'observation', 'outcomes', 'counts', 'counts_before', 'counts_after'], 'dispatch_scope': 'ACTUAL_Q1_Q2_COUNTED_KNOWLEDGE_BACKEND_RETRIEVE_ENTRIES_ONLY; PROJECTION_PERSIST_IS_SEPARATE', 'field_dispositions': {'actual_settlement_count': {'status': 'SOURCE_BOUND_N_A', 'reason': 'Q2_REGISTRATION_HAS_NO_CANONICAL_BUDGET_OWNER', 'evidence_scope': 'Q2_EXACT_NATIVE_KNOWLEDGE_HANDLER_ONLY; Q1_NATIVE_TOOL_TRANSITIONS_ARE_MEASURED_SEPARATELY', 'source_decision': {'path': 'D:\\MyCode\\rag-agent-shared\\docs\\fpbca_bcd_closure_design_v0_2_20261007\\03_D_VERIFICATION_DESIGN_V0_2.md', 'sha256': '86c300eb36233b96858168d33aa2d69167de1c93c18a115b1fc4dc387b9aa085', 'authority': 'D5_PHYSICAL_MUTATION_ACCOUNTING_WHERE_RELEVANT', 'section': 'D5 required native fields WHERE RELEVANT'}}}, 'cold_symbols': ['JsonlToolExecutionStore.__init__'], 'explicit_handler_counter': ['case', 'backend_requests'], 'terminal_reservation_kinds': ['logical_tool_call'], 'settlement_scope': 'ACTUAL_Q1_CANONICAL_LOGICAL_TOOL_ACTIVE_TO_CONSUMED_OR_RELEASED_TRANSITIONS; NOT_PROVIDER_TOKEN_SETTLEMENT', 'native_tool_passive_receipt': 'TOOL_NATIVE_EXACT_FINAL_RECEIPT_CUSTODY_V1'})
