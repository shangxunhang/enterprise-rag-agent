"""TXT-only new native observation controls; old source/tests stay unchanged.

Original assertions and native owner calls are copied exactly. Only observational
capture is added; no new business owner/store or semantic operation is added.
"""
import importlib as _fpbca_importlib
from pathlib import Path as _FPBCA_Path
import pytest as _fpbca_pytest
from fpbca_final65_native_capture import NativeCapture as _FPBCA_NativeCapture

_fpbca_base = _fpbca_importlib.import_module('test_fpbca_bcd_cap_business')
for _fpbca_name, _fpbca_value in vars(_fpbca_base).items():
    if not _fpbca_name.startswith('__') and not _fpbca_name.startswith('test_'):
        globals().setdefault(_fpbca_name, _fpbca_value)
_FPBCA_DECLARATION = {'hooks': [{'async': False, 'end_line': 821, 'line': 786, 'path': 'backend/rag/context/context_packer.py', 'proof_scope': 'ACTUAL_DEF_AST_BINDING_ONLY_NOT_NATIVE_EXECUTION_PROOF', 'sha256': 'dc4401cce4d552e7e110a244d9a8255135ecb1943ad43cd31ff605ff9f355e05', 'symbol': 'ContextPacker.pack', 'module': 'rag.context.context_packer', 'owner': True, 'consumer': True, 'plain_method': True}, {'async': False, 'end_line': 78, 'line': 28, 'path': 'backend/model_gateway/model_invoker.py', 'proof_scope': 'ACTUAL_DEF_AST_BINDING_ONLY_NOT_NATIVE_EXECUTION_PROOF', 'sha256': '888618af70072418f93d1fc637b34dbc12a8f720b73d874fdcc0b125a18870c9', 'symbol': 'ModelInvoker.invoke', 'module': 'model_gateway.model_invoker', 'owner': False, 'consumer': False, 'plain_method': True}, {'async': False, 'end_line': 1148, 'line': 1099, 'path': 'backend/apps/enterprise_document/services/agent_rag_v1/writer_context.py', 'proof_scope': 'ACTUAL_DEF_AST_BINDING_ONLY_NOT_NATIVE_EXECUTION_PROOF', 'sha256': '36fa16c7ae1a36ccbd7e84679ffdf6a41b9ba474a2253cad9e02673987748b94', 'symbol': 'TargetWriterContextComposer.final_messages', 'module': 'apps.enterprise_document.services.agent_rag_v1.writer_context', 'owner': True, 'consumer': True, 'plain_method': True}], 'explicit_local_names': ['actual_request', 'after', 'bad_evidence', 'bad_requirement', 'before', 'bundle', 'calls', 'candidate', 'child', 'chosen', 'chunking', 'content', 'content_v2', 'current', 'destination', 'engine', 'entry', 'env', 'evidence', 'expected', 'fact_v2', 'factory', 'failure', 'frame', 'frozen', 'gate', 'head', 'header', 'invocation', 'kwargs', 'manifest', 'marker', 'material', 'observations', 'old_lineage', 'opened', 'original_hash', 'pack', 'package', 'packer', 'packs', 'parent', 'payload', 'process', 'projection', 'prompt', 'proof', 'provider_calls', 'publication', 'raw', 'record', 'request', 'result', 'retrieval_v2', 'role', 'route', 'row', 'rows', 'run', 'selected', 'source', 'state', 'structured', 'summary', 'support', 'support_payload', 'terminal', 'trace', 'unit', 'verifier', 'versions', 'view', 'writer'], 'pure_projection_rows': ['V08', 'V09', 'V10', 'V11', 'V12', 'V13', 'V14', 'V15', 'V18', 'V25'], 'row_native_symbols': {'V08': ['TargetWriterContextComposer.final_messages'], 'V16': [], 'V17': ['ContextPacker.pack', 'TargetWriterContextComposer.final_messages']}, 'row_exact_final_consumer_symbols': {'V08': ['ModelInvoker.invoke'], 'V16': [], 'V17': []}, 'generic_fields': ['exact_producer_input_material', 'positive_exact_owner_result', 'negative_mutation_and_rejection', 'final_consumer_observation', 'actual_control_revisions', 'physical_dispatch_count', 'semantic_commit_delta'], 'D5_source_decision': {'path': 'D:\\MyCode\\rag-agent-shared\\docs\\fpbca_bcd_closure_design_v0_2_20261007\\03_D_VERIFICATION_DESIGN_V0_2.md', 'role': 'FROZEN_D1_D2_D5_DISPATCH_MUTATION_ACCOUNTING_WHERE_RELEVANT', 'sha256': '86c300eb36233b96858168d33aa2d69167de1c93c18a115b1fc4dc387b9aa085'}, 'original_module_source': {'path': 'D:\\MyCode\\rag-agent-shared\\backend\\tests\\test_fpbca_bcd_cap_business.py', 'sha256': 'c30f34bec79500395071dc0d66e8472fd2c40ca41e3ee2ae134cb221f3e4513f'}}

@_fpbca_pytest.fixture(scope='function', autouse=True)
def _fpbca_native_case_capture():
    capture = _FPBCA_NativeCapture(_FPBCA_DECLARATION).install(native_namespaces=(globals(), vars(_fpbca_base)))
    try:
        yield capture
    finally:
        capture.close()

def test_native_observed_bs01_bs07_actual_normal_fallback_writer_messages_keep_qualified_proposal_instruction(r1_mainline, tmp_path, _fpbca_native_case_capture):
    try:
        'Real local route/provider input; the Fake response is not semantic proof.'
        _, request, package, services, _, invocation, _, _ = r1_mainline
        text = '\n'.join((item['content'] for item in request.messages))
        for instruction in ('preserve that authoritative value; historical/reference values must never replace it', 'clearly qualified proposal, estimate, planning assumption or suggested design', 'keep the parameter unresolved/to be confirmed', 'practice as a fact supplied by this customer'):
            assert instruction in text
        actual = {'input_kind': 'MESSAGES', 'messages': request.messages}
        assert package.model_input.final_input_material.inline_final_input_payload == actual
        assert canonical_sha256(actual) == package.model_input.final_model_input_sha256
        route = services.route_decision_store.resolve_exact(invocation.route_decision_ref)
        expected = 'fallback' if invocation.writer_subject_ref.run_id.endswith('fallback') else 'normal'
        assert route.selected_route.value == expected
        assert invocation.generation_class.value == f'{expected}_write'
    finally:
        _fpbca_native_case_capture.emit(root=tmp_path, rows=['V16', 'V17'], local_values=locals(), selector='backend/tests/test_fpbca_final65_obs_fpbca_bcd_cap_business.py::test_native_observed_bs01_bs07_actual_normal_fallback_writer_messages_keep_qualified_proposal_instruction', native_source={'path': 'D:\\MyCode\\rag-agent-shared\\backend\\tests\\test_fpbca_bcd_cap_business.py', 'sha256': 'c30f34bec79500395071dc0d66e8472fd2c40ca41e3ee2ae134cb221f3e4513f', 'function': 'test_bs01_bs07_actual_normal_fallback_writer_messages_keep_qualified_proposal_instruction', 'line': 74, 'native_operation': 'EXACT_ORIGINAL_NATIVE_TEST_FLOW', 'native_refs': []}, native_local_names=[])

def test_native_observed_bs01_bs07_writer_current_fact_precedence_and_reference_proposal_instructions(tmp_path, _fpbca_native_case_capture):
    try:
        _, material = _instruction_pair(_project_input('cap-controlled-task'))
        role = material.role_instruction
        assert 'authoritative' in role.lower() and 'historical' in role.lower()
        assert 'replace' in role.lower()
        assert 'qualified proposal' in role.lower()
        assert 'practice as a fact supplied by this customer' in role
    finally:
        _fpbca_native_case_capture.emit(root=tmp_path, rows=['V16', 'V17'], local_values=locals(), selector='backend/tests/test_fpbca_final65_obs_fpbca_bcd_cap_business.py::test_native_observed_bs01_bs07_writer_current_fact_precedence_and_reference_proposal_instructions', native_source={'path': 'D:\\MyCode\\rag-agent-shared\\backend\\tests\\test_fpbca_bcd_cap_business.py', 'sha256': 'c30f34bec79500395071dc0d66e8472fd2c40ca41e3ee2ae134cb221f3e4513f', 'function': 'test_bs01_bs07_writer_current_fact_precedence_and_reference_proposal_instructions', 'line': 65, 'native_operation': 'EXACT_ORIGINAL_NATIVE_TEST_FLOW', 'native_refs': []}, native_local_names=[])

def test_native_observed_bs02_bs11_reference_view_is_contextual_not_current_factual_eligibility(tmp_path, _fpbca_native_case_capture):
    try:
        bundle, package = reference_bundle()
        before = bundle.model_dump(mode='json')
        assert TargetClaimMaterializer.eligible_evidence_ids(bundle, ClaimType.CURRENT_PROJECT_QUANTITY) == ()
        assert TargetClaimRuntimeValidators.reference_proposal_evidence_ids(bundle, package, ClaimType.CURRENT_PROJECT_QUANTITY) == ('history',)
        assert TargetClaimRuntimeValidators.reference_proposal_evidence_ids(bundle, package, ClaimType.GENERAL_METHOD) == ()
        assert bundle.model_dump(mode='json') == before
    finally:
        _fpbca_native_case_capture.emit(root=tmp_path, rows=['V16', 'V17'], local_values=locals(), selector='backend/tests/test_fpbca_final65_obs_fpbca_bcd_cap_business.py::test_native_observed_bs02_bs11_reference_view_is_contextual_not_current_factual_eligibility', native_source={'path': 'D:\\MyCode\\rag-agent-shared\\backend\\tests\\test_fpbca_bcd_cap_business.py', 'sha256': 'c30f34bec79500395071dc0d66e8472fd2c40ca41e3ee2ae134cb221f3e4513f', 'function': 'test_bs02_bs11_reference_view_is_contextual_not_current_factual_eligibility', 'line': 94, 'native_operation': 'EXACT_ORIGINAL_NATIVE_TEST_FLOW', 'native_refs': []}, native_local_names=[])

@pytest.mark.parametrize('relation,expected', [('ALIGNED', 'pass'), ('ESCALATED', 'fail'), ('INDETERMINATE', 'indeterminate')])
def test_native_observed_bs03_bs12_existing_fth03_authored_certainty_outcome(tmp_path, relation, expected, _fpbca_native_case_capture):
    try:
        target, support = _target_support(tmp_path)
        fact = FaithfulnessCertaintyProjector.project(validation_run_id='cap-certainty-control', subject_hash=_subject_hash(), claim_target=target, support_record=support, requirement_ref=_requirement_ref(), applicable_requirement_ids=(), certainty_relation=relation, certainty_reason_codes=('AUTHORED_FAKE_CERTAINTY_CONTROL',), physical_support_receipt_ref='artifact://controlled-cap/support', related_refs=())
        assert fact.semantic_state.value.lower() == expected
    finally:
        _fpbca_native_case_capture.emit(root=tmp_path, rows=['V16', 'V17'], local_values=locals(), selector='backend/tests/test_fpbca_final65_obs_fpbca_bcd_cap_business.py::test_native_observed_bs03_bs12_existing_fth03_authored_certainty_outcome', native_source={'path': 'D:\\MyCode\\rag-agent-shared\\backend\\tests\\test_fpbca_bcd_cap_business.py', 'sha256': 'c30f34bec79500395071dc0d66e8472fd2c40ca41e3ee2ae134cb221f3e4513f', 'function': 'test_bs03_bs12_existing_fth03_authored_certainty_outcome', 'line': 222, 'native_operation': 'EXACT_ORIGINAL_NATIVE_TEST_FLOW', 'native_refs': []}, native_local_names=[])

@pytest.mark.parametrize('variant', ['dropped', 'empty', 'irrelevant', 'ineligible', 'current', 'unfinished'])
def test_native_observed_bs04_bs11_no_source_promotion(variant, tmp_path, _fpbca_native_case_capture):
    try:
        bundle, package = reference_bundle()
        if variant == 'dropped':
            package = SimpleNamespace(selections=())
        elif variant == 'empty':
            bundle = bundle.model_copy(update={'items': [bundle.items[0].model_copy(update={'context_text': ' '})]})
        elif variant in {'irrelevant', 'ineligible'}:
            assessment = bundle.item_assessments[0].model_copy(update={'eligibility': False, 'supported_claim_types': [], 'relevance': EvidenceRelevance.IRRELEVANT if variant == 'irrelevant' else EvidenceRelevance.PARTIAL})
            bundle = EvidenceBundleSchema.model_validate(bundle.model_copy(update={'item_assessments': [assessment]}).model_dump(mode='json'))
        elif variant == 'current':
            assessment = bundle.item_assessments[0].model_copy(update={'project_relation': ProjectRelation.CURRENT_PROJECT, 'evidence_role': EvidenceRole.PROJECT_FACT, 'authority_level': AuthorityLevel.AUTHORITATIVE})
            bundle = EvidenceBundleSchema.model_validate(bundle.model_copy(update={'item_assessments': [assessment]}).model_dump(mode='json'))
        else:
            bundle = bundle.model_copy(update={'assessment': bundle.assessment.model_copy(update={'assessment_state': 'not_assessed'})})
        if variant == 'unfinished':
            with pytest.raises(ValueError):
                TargetClaimRuntimeValidators.reference_proposal_evidence_ids(bundle, package, ClaimType.CURRENT_PROJECT_QUANTITY)
        else:
            assert TargetClaimRuntimeValidators.reference_proposal_evidence_ids(bundle, package, ClaimType.CURRENT_PROJECT_QUANTITY) == ()
    finally:
        _fpbca_native_case_capture.emit(root=tmp_path, rows=['V16', 'V17'], local_values=locals(), selector='backend/tests/test_fpbca_final65_obs_fpbca_bcd_cap_business.py::test_native_observed_bs04_bs11_no_source_promotion', native_source={'path': 'D:\\MyCode\\rag-agent-shared\\backend\\tests\\test_fpbca_bcd_cap_business.py', 'sha256': 'c30f34bec79500395071dc0d66e8472fd2c40ca41e3ee2ae134cb221f3e4513f', 'function': 'test_bs04_bs11_no_source_promotion', 'line': 104, 'native_operation': 'EXACT_ORIGINAL_NATIVE_TEST_FLOW', 'native_refs': []}, native_local_names=[])

@pytest.mark.parametrize('status', [FactStatus.ESTIMATED, FactStatus.PROPOSED, FactStatus.PLANNED])
def test_native_observed_bs05_existing_current_fact_qualifiers(status, tmp_path, _fpbca_native_case_capture):
    try:
        policy = resolve_fact_claim_eligibility(claim_type=ClaimType.CURRENT_PROJECT_QUANTITY, fact_status=status, authority_level=AuthorityLevel.AUTHORITATIVE, document_type=DocumentType.HISTORICAL_FEASIBILITY_STUDY, source_document_form=None, project_relation=ProjectRelation.CURRENT_PROJECT)
        assert policy.eligible and policy.required_qualifiers
    finally:
        _fpbca_native_case_capture.emit(root=tmp_path, rows=['V16', 'V17'], local_values=locals(), selector='backend/tests/test_fpbca_final65_obs_fpbca_bcd_cap_business.py::test_native_observed_bs05_existing_current_fact_qualifiers', native_source={'path': 'D:\\MyCode\\rag-agent-shared\\backend\\tests\\test_fpbca_bcd_cap_business.py', 'sha256': 'c30f34bec79500395071dc0d66e8472fd2c40ca41e3ee2ae134cb221f3e4513f', 'function': 'test_bs05_existing_current_fact_qualifiers', 'line': 127, 'native_operation': 'EXACT_ORIGINAL_NATIVE_TEST_FLOW', 'native_refs': []}, native_local_names=[])

def test_native_observed_bs06_governed_historical_body_and_labels_visible(tmp_path, _fpbca_native_case_capture):
    try:
        pack = ContextPacker(max_context_chars=6000).pack([historical_candidate()], token_budget=4000)
        assert HISTORY in pack.context and '历史16台服务器章节' in pack.context
        assert pack.selected_results[0]['metadata']['raw_fact_visibility'] == 'reference'
        assert '未向生成模型暴露' not in pack.context
    finally:
        _fpbca_native_case_capture.emit(root=tmp_path, rows=['V16', 'V17'], local_values=locals(), selector='backend/tests/test_fpbca_final65_obs_fpbca_bcd_cap_business.py::test_native_observed_bs06_governed_historical_body_and_labels_visible', native_source={'path': 'D:\\MyCode\\rag-agent-shared\\backend\\tests\\test_fpbca_bcd_cap_business.py', 'sha256': 'c30f34bec79500395071dc0d66e8472fd2c40ca41e3ee2ae134cb221f3e4513f', 'function': 'test_bs06_governed_historical_body_and_labels_visible', 'line': 135, 'native_operation': 'EXACT_ORIGINAL_NATIVE_TEST_FLOW', 'native_refs': []}, native_local_names=[])

@pytest.mark.parametrize('variant', ['ineligible', 'irrelevant', 'missing_eligibility'])
def test_native_observed_bs06_unusable_historical_body_stays_restricted(variant, tmp_path, _fpbca_native_case_capture):
    try:
        candidate = historical_candidate()
        assessment = candidate['metadata']['evidence_assessment_v2']
        if variant == 'ineligible':
            assessment['eligibility'] = False
        elif variant == 'irrelevant':
            assessment['relevance'] = 'irrelevant'
        else:
            assessment.pop('eligibility')
        pack = ContextPacker(max_context_chars=6000).pack([candidate], token_budget=4000)
        assert HISTORY not in pack.context
        assert 'GOVERNED_BODY_NONCE' not in pack.context
        assert pack.selected_results[0]['metadata']['raw_fact_visibility'] == 'restricted'
        assert pack.selected_results[0]['metadata']['prompt_projection_mode'] == 'historical_reference_restricted'
    finally:
        _fpbca_native_case_capture.emit(root=tmp_path, rows=['V16', 'V17'], local_values=locals(), selector='backend/tests/test_fpbca_final65_obs_fpbca_bcd_cap_business.py::test_native_observed_bs06_unusable_historical_body_stays_restricted', native_source={'path': 'D:\\MyCode\\rag-agent-shared\\backend\\tests\\test_fpbca_bcd_cap_business.py', 'sha256': 'c30f34bec79500395071dc0d66e8472fd2c40ca41e3ee2ae134cb221f3e4513f', 'function': 'test_bs06_unusable_historical_body_stays_restricted', 'line': 143, 'native_operation': 'EXACT_ORIGINAL_NATIVE_TEST_FLOW', 'native_refs': []}, native_local_names=[])

def test_native_observed_bs08_raw_assessment_metadata_does_not_bypass_visible_projection(tmp_path, _fpbca_native_case_capture):
    try:
        bundle, _ = reference_bundle()
        assessment = bundle.item_assessments[0].model_copy(update={'metadata': {'hidden_nonce': 'RAW_OUTER_NONCE'}})
        material, _ = TargetClaimRuntimeValidators._visible_evidence_material(bundle.items[0], assessment)
        assert 'RAW_OUTER_NONCE' not in str(material)
        assert material['context_text_sha256'] == canonical_sha256(HISTORY)
    finally:
        _fpbca_native_case_capture.emit(root=tmp_path, rows=['V08', 'V16', 'V17'], local_values=locals(), selector='backend/tests/test_fpbca_final65_obs_fpbca_bcd_cap_business.py::test_native_observed_bs08_raw_assessment_metadata_does_not_bypass_visible_projection', native_source={'path': 'D:\\MyCode\\rag-agent-shared\\backend\\tests\\test_fpbca_bcd_cap_business.py', 'sha256': 'c30f34bec79500395071dc0d66e8472fd2c40ca41e3ee2ae134cb221f3e4513f', 'function': 'test_bs08_raw_assessment_metadata_does_not_bypass_visible_projection', 'line': 161, 'native_operation': 'EXACT_ORIGINAL_NATIVE_TEST_FLOW', 'native_refs': []}, native_local_names=[])

def test_native_observed_bs09_same_projection_priority_membership_before_presentation(tmp_path, _fpbca_native_case_capture):
    try:
        rows = [historical_candidate(rank=i, chunk_id=f'child-{i}', child_chunk_id=f'child-{i}', parent_chunk_id=f'parent-{i}') for i in range(1, 5)]
        packs = [ContextPacker(max_context_chars=10000, max_items=3, packing_strategy=s).pack(rows, token_budget=4000) for s in ('default', 'lost_in_middle_aware')]
        assert [sorted((r['child_chunk_id'] for r in p.items)) for p in packs] == [['child-1', 'child-2', 'child-3']] * 2
    finally:
        _fpbca_native_case_capture.emit(root=tmp_path, rows=['V16', 'V17'], local_values=locals(), selector='backend/tests/test_fpbca_final65_obs_fpbca_bcd_cap_business.py::test_native_observed_bs09_same_projection_priority_membership_before_presentation', native_source={'path': 'D:\\MyCode\\rag-agent-shared\\backend\\tests\\test_fpbca_bcd_cap_business.py', 'sha256': 'c30f34bec79500395071dc0d66e8472fd2c40ca41e3ee2ae134cb221f3e4513f', 'function': 'test_bs09_same_projection_priority_membership_before_presentation', 'line': 169, 'native_operation': 'EXACT_ORIGINAL_NATIVE_TEST_FLOW', 'native_refs': []}, native_local_names=[])

def test_native_observed_bs10_native_contextual_sources_commit_read_reopen_support_verifier(monkeypatch, tmp_path, _fpbca_native_case_capture):
    try:
        'Actual native source reconstruction; existing authored support/Fake verifier.'
        original = native._completed_two_evidence_bundle

        def historical_native_bundle():
            bundle = original()
            assessments = [a.model_copy(update={'document_type': DocumentType.HISTORICAL_CONSTRUCTION_SCHEME, 'evidence_role': EvidenceRole.HISTORICAL_CASE, 'project_relation': ProjectRelation.OTHER_PROJECT, 'authority_level': AuthorityLevel.REFERENCE, 'supported_claim_types': [ClaimType.GENERAL_METHOD], 'forbidden_claim_types': sorted(CURRENT_PROJECT_CLAIM_TYPES, key=lambda t: t.value)}) for a in bundle.item_assessments]
            items = [i.model_copy(update={'context_text': native.TEXT}) for i in bundle.items]
            return EvidenceBundleSchema.model_validate(bundle.model_copy(update={'item_assessments': assessments, 'items': items}).model_dump(mode='json'))
        monkeypatch.setattr(native, '_completed_two_evidence_bundle', historical_native_bundle)
        fx = native.create_fixture(tmp_path / 'native', script=[native.legal_reply('VERIFIED', ['@supporting'])])
        rows = fx.sources.materialize_sources(fx.claim, fx.context.active_lineage)
        evidence = tuple((r for r in rows if r.source_ref.source_kind == 'EVIDENCE'))
        assert len(evidence) == 2
        assert all((r.fact.support_kind is ClaimBindingSupportKind.CONTEXTUAL_SUPPORT and r.fact.relation is ClaimBindingRelation.CONTEXT_ONLY for r in evidence))
        read_rows = fx.sources.read_current_sources(fx.claim, fx.context.active_lineage)
        assert read_rows == rows
        reopened = native.reopen_fixture(fx.root)
        assert reopened.sources.read_current_sources(reopened.claim, reopened.context.active_lineage) == rows
        target = fx.support.evidence_target_set_ref
        assert set(target.evidence_member_refs) == {r.source_ref.exact_source_ref for r in evidence}
        assert fx.support_composition._scope(target, fx.context)[2] == evidence
        fx.composer._validate_resolved_sources(fx.runtime._binding_record_candidate(fx.binding), fx.binding, fx.support, rows)
        native.verify(fx)
        assert len(native.provider_rows(fx)) == 1
    finally:
        _fpbca_native_case_capture.emit(root=tmp_path, rows=['V16', 'V17'], local_values=locals(), selector='backend/tests/test_fpbca_final65_obs_fpbca_bcd_cap_business.py::test_native_observed_bs10_native_contextual_sources_commit_read_reopen_support_verifier', native_source={'path': 'D:\\MyCode\\rag-agent-shared\\backend\\tests\\test_fpbca_bcd_cap_business.py', 'sha256': 'c30f34bec79500395071dc0d66e8472fd2c40ca41e3ee2ae134cb221f3e4513f', 'function': 'test_bs10_native_contextual_sources_commit_read_reopen_support_verifier', 'line': 175, 'native_operation': 'EXACT_ORIGINAL_NATIVE_TEST_FLOW', 'native_refs': []}, native_local_names=[])

def test_native_observed_bs12_support_prompt_uses_existing_one_call_qualified_proposal_semantics(tmp_path, _fpbca_native_case_capture):
    try:
        fx = native.create_fixture(tmp_path / 'support-prompt')
        support = fx.support_composition
        support.model_gateway = fx.gateway
        target = fx.support.evidence_target_set_ref
        claim, binding, evidence = support._scope(target, fx.context)
        material = support._input_material(claim, binding, evidence, target)
        package, _ = support._package(material, target=target, context=fx.context, occurrence='cap-support-instruction')
        projected = str(package.model_dump(mode='json'))
        assert 'CONFIRMED current-project fact' in projected
        assert 'qualified proposal' in projected
        assert 'ESCALATED' in projected and 'INDETERMINATE' in projected
        assert not native.provider_rows(fx)
    finally:
        _fpbca_native_case_capture.emit(root=tmp_path, rows=['V16', 'V17'], local_values=locals(), selector='backend/tests/test_fpbca_final65_obs_fpbca_bcd_cap_business.py::test_native_observed_bs12_support_prompt_uses_existing_one_call_qualified_proposal_semantics', native_source={'path': 'D:\\MyCode\\rag-agent-shared\\backend\\tests\\test_fpbca_bcd_cap_business.py', 'sha256': 'c30f34bec79500395071dc0d66e8472fd2c40ca41e3ee2ae134cb221f3e4513f', 'function': 'test_bs12_support_prompt_uses_existing_one_call_qualified_proposal_semantics', 'line': 206, 'native_operation': 'EXACT_ORIGINAL_NATIVE_TEST_FLOW', 'native_refs': []}, native_local_names=[])
