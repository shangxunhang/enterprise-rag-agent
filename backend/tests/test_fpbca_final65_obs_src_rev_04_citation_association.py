"""TXT-only new native observation controls; old source/tests stay unchanged.

Original assertions and native owner calls are copied exactly. Only observational
capture is added; no new business owner/store or semantic operation is added.
"""
import importlib as _fpbca_importlib
from pathlib import Path as _FPBCA_Path
import pytest as _fpbca_pytest
from fpbca_final65_native_capture import NativeCapture as _FPBCA_NativeCapture

_fpbca_base = _fpbca_importlib.import_module('test_src_rev_04_citation_association')
for _fpbca_name, _fpbca_value in vars(_fpbca_base).items():
    if not _fpbca_name.startswith('__') and not _fpbca_name.startswith('test_'):
        globals().setdefault(_fpbca_name, _fpbca_value)
_FPBCA_DECLARATION = {'hooks': [{'async': False, 'end_line': 78, 'line': 28, 'path': 'backend/model_gateway/model_invoker.py', 'proof_scope': 'ACTUAL_DEF_AST_BINDING_ONLY_NOT_NATIVE_EXECUTION_PROOF', 'sha256': '888618af70072418f93d1fc637b34dbc12a8f720b73d874fdcc0b125a18870c9', 'symbol': 'ModelInvoker.invoke', 'module': 'model_gateway.model_invoker', 'owner': False, 'consumer': False, 'plain_method': True}, {'async': False, 'end_line': 85, 'line': 45, 'path': 'backend/apps/enterprise_document/services/agent_rag_v1/citation_association.py', 'proof_scope': 'ACTUAL_DEF_AST_BINDING_ONLY_NOT_NATIVE_EXECUTION_PROOF', 'sha256': '887aa487d07e7f6652e723026337e58e6f77888658f7c0be6e0db44a9d34735c', 'symbol': 'associate_selected_citations', 'module': 'apps.enterprise_document.services.agent_rag_v1.citation_association', 'owner': True, 'consumer': False, 'plain_method': False}, {'async': False, 'end_line': 97, 'line': 88, 'path': 'backend/apps/enterprise_document/services/agent_rag_v1/citation_association.py', 'proof_scope': 'ACTUAL_DEF_AST_BINDING_ONLY_NOT_NATIVE_EXECUTION_PROOF', 'sha256': '887aa487d07e7f6652e723026337e58e6f77888658f7c0be6e0db44a9d34735c', 'symbol': 'writer_allowed_aliases', 'module': 'apps.enterprise_document.services.agent_rag_v1.citation_association', 'owner': True, 'consumer': False, 'plain_method': False}], 'explicit_local_names': ['actual_request', 'after', 'bad_evidence', 'bad_requirement', 'before', 'bundle', 'calls', 'candidate', 'child', 'chosen', 'chunking', 'content', 'content_v2', 'current', 'destination', 'engine', 'entry', 'env', 'evidence', 'expected', 'fact_v2', 'factory', 'failure', 'frame', 'frozen', 'gate', 'head', 'header', 'invocation', 'kwargs', 'manifest', 'marker', 'material', 'observations', 'old_lineage', 'opened', 'original_hash', 'pack', 'package', 'packer', 'packs', 'parent', 'payload', 'process', 'projection', 'prompt', 'proof', 'provider_calls', 'publication', 'raw', 'record', 'request', 'result', 'retrieval_v2', 'role', 'route', 'row', 'rows', 'run', 'selected', 'source', 'state', 'structured', 'summary', 'support', 'support_payload', 'terminal', 'trace', 'unit', 'verifier', 'versions', 'view', 'writer'], 'pure_projection_rows': ['V08', 'V09', 'V10', 'V11', 'V12', 'V13', 'V14', 'V15', 'V18', 'V25'], 'row_native_symbols': {'V07': ['associate_selected_citations', 'writer_allowed_aliases']}, 'row_exact_final_consumer_symbols': {'V07': []}, 'generic_fields': ['exact_producer_input_material', 'positive_exact_owner_result', 'negative_mutation_and_rejection', 'final_consumer_observation', 'actual_control_revisions', 'physical_dispatch_count', 'semantic_commit_delta'], 'D5_source_decision': {'path': 'D:\\MyCode\\rag-agent-shared\\docs\\fpbca_bcd_closure_design_v0_2_20261007\\03_D_VERIFICATION_DESIGN_V0_2.md', 'role': 'FROZEN_D1_D2_D5_DISPATCH_MUTATION_ACCOUNTING_WHERE_RELEVANT', 'sha256': '86c300eb36233b96858168d33aa2d69167de1c93c18a115b1fc4dc387b9aa085'}, 'original_module_source': {'path': 'D:\\MyCode\\rag-agent-shared\\backend\\tests\\test_src_rev_04_citation_association.py', 'sha256': 'd71451beff6bf768e683609675738a562d15d02bd1ffbb303cbf09ca794542b5'}}

@_fpbca_pytest.fixture(scope='function', autouse=True)
def _fpbca_native_case_capture():
    capture = _FPBCA_NativeCapture(_FPBCA_DECLARATION).install(native_namespaces=(globals(), vars(_fpbca_base)))
    try:
        yield capture
    finally:
        capture.close()

def test_native_observed_identity_mismatch_stays_visible_but_is_never_recommended(tmp_path, _fpbca_native_case_capture) -> None:
    try:
        from apps.enterprise_document.services.agent_rag_v1.citation_association import associate_selected_citations, writer_allowed_aliases
        package, bundle = _inputs(package_ids=('E1',), selected_ids=('E1',), citations=(_citation('C1', doc_id='doc-a', chunk_id='chunk-a', citation_ref='foreign-source'),), items=(SimpleNamespace(evidence_id='E1', citation_ids=['C1']),))
        rows = associate_selected_citations(package=package, bundle=bundle)
        assert [(row.alias, row.citation_ref, row.evidence_id, row.exact_evidence_ref, row.source_identity_consistent, row.citation_source_identity) for row in rows] == [('C1', 'foreign-source', 'E1', _E1_REF, False, _C1_REF)]
        assert writer_allowed_aliases(rows) == ()
    finally:
        _fpbca_native_case_capture.emit(root=tmp_path, rows=['V07'], local_values=locals(), selector='backend/tests/test_fpbca_final65_obs_src_rev_04_citation_association.py::test_native_observed_identity_mismatch_stays_visible_but_is_never_recommended', native_source={'path': 'D:\\MyCode\\rag-agent-shared\\backend\\tests\\test_src_rev_04_citation_association.py', 'sha256': 'd71451beff6bf768e683609675738a562d15d02bd1ffbb303cbf09ca794542b5', 'function': 'test_identity_mismatch_stays_visible_but_is_never_recommended', 'line': 101, 'native_operation': 'EXACT_ORIGINAL_NATIVE_TEST_FLOW', 'native_refs': []}, native_local_names=['rows'])

def test_native_observed_post_write_keeps_duplicate_selected_alias_ambiguous(tmp_path, _fpbca_native_case_capture) -> None:
    try:
        from apps.enterprise_document.services.agent_rag_v1.citation_association import associate_selected_citations, writer_allowed_aliases
        package, bundle = _inputs()
        catalog, _, _ = _section_catalog_from_current_sources(package, bundle)
        assert catalog.schema_version == 'post_write_citation_alias_catalog_v1'
        assert [(entry.alias, entry.citation_ref, entry.exact_evidence_ref, entry.origin_refs[0]['evidence_id']) for entry in catalog.entries] == [('C1', _C1_REF, _E1_REF, 'E1'), ('C1', _C1_REF, _E2_REF, 'E2')]
        assert writer_allowed_aliases(associate_selected_citations(package=package, bundle=bundle)) == ()
        facts = _facts_for_alias(catalog, 'C1')
        assert [(fact.payload['rule_id'], fact.semantic_state.value, fact.payload['reason_codes']) for fact in facts] == [('PW-R-CIT-I-01', 'PASS', ['WELL_FORMED']), ('PW-R-CIT-I-02', 'FAIL', ['AMBIGUOUS_ALIAS'])]
    finally:
        _fpbca_native_case_capture.emit(root=tmp_path, rows=['V07'], local_values=locals(), selector='backend/tests/test_fpbca_final65_obs_src_rev_04_citation_association.py::test_native_observed_post_write_keeps_duplicate_selected_alias_ambiguous', native_source={'path': 'D:\\MyCode\\rag-agent-shared\\backend\\tests\\test_src_rev_04_citation_association.py', 'sha256': 'd71451beff6bf768e683609675738a562d15d02bd1ffbb303cbf09ca794542b5', 'function': 'test_post_write_keeps_duplicate_selected_alias_ambiguous', 'line': 289, 'native_operation': 'EXACT_ORIGINAL_NATIVE_TEST_FLOW', 'native_refs': []}, native_local_names=[])

def test_native_observed_selected_association_preserves_exact_rows_and_multiplicity(tmp_path, _fpbca_native_case_capture) -> None:
    try:
        from apps.enterprise_document.services.agent_rag_v1.citation_association import associate_selected_citations, writer_allowed_aliases
        package, bundle = _inputs()
        before = deepcopy((package, bundle))
        rows = associate_selected_citations(package=package, bundle=bundle)
        assert [(row.alias, row.citation_ref, row.evidence_id, row.exact_evidence_ref, row.source_identity_consistent, row.citation_source_identity, row.source_locator_ref) for row in rows] == [('C1', _C1_REF, 'E1', _E1_REF, True, _C1_REF, _C1_LOCATOR_REF), ('C1', _C1_REF, 'E2', _E2_REF, True, _C1_REF, _C1_LOCATOR_REF)]
        assert rows[0].source_locator == {'source_type': 'chunk', 'doc_id': 'doc-a', 'source_document_id': None, 'file_id': None, 'chunk_id': 'chunk-a', 'child_chunk_id': None, 'parent_chunk_id': None, 'table_id': None, 'row_ids': [], 'page_start': None, 'page_end': None}
        assert writer_allowed_aliases(rows) == (), 'two exact sources remain ambiguous'
        assert (package, bundle) == before
    finally:
        _fpbca_native_case_capture.emit(root=tmp_path, rows=['V07'], local_values=locals(), selector='backend/tests/test_fpbca_final65_obs_src_rev_04_citation_association.py::test_native_observed_selected_association_preserves_exact_rows_and_multiplicity', native_source={'path': 'D:\\MyCode\\rag-agent-shared\\backend\\tests\\test_src_rev_04_citation_association.py', 'sha256': 'd71451beff6bf768e683609675738a562d15d02bd1ffbb303cbf09ca794542b5', 'function': 'test_selected_association_preserves_exact_rows_and_multiplicity', 'line': 54, 'native_operation': 'EXACT_ORIGINAL_NATIVE_TEST_FLOW', 'native_refs': []}, native_local_names=['rows'])
