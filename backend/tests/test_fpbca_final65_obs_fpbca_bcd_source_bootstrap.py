"""TXT-only new native observation controls; old source/tests stay unchanged.

Original assertions and native owner calls are copied exactly. Only observational
capture is added; no new business owner/store or semantic operation is added.
"""
import importlib as _fpbca_importlib
from pathlib import Path as _FPBCA_Path
import pytest as _fpbca_pytest
from fpbca_final65_native_capture import NativeCapture as _FPBCA_NativeCapture

_fpbca_base = _fpbca_importlib.import_module('test_fpbca_bcd_source_bootstrap')
for _fpbca_name, _fpbca_value in vars(_fpbca_base).items():
    if not _fpbca_name.startswith('__') and not _fpbca_name.startswith('test_'):
        globals().setdefault(_fpbca_name, _fpbca_value)
_FPBCA_DECLARATION = {'hooks': [{'async': False, 'end_line': 78, 'line': 28, 'path': 'backend/model_gateway/model_invoker.py', 'proof_scope': 'ACTUAL_DEF_AST_BINDING_ONLY_NOT_NATIVE_EXECUTION_PROOF', 'sha256': '888618af70072418f93d1fc637b34dbc12a8f720b73d874fdcc0b125a18870c9', 'symbol': 'ModelInvoker.invoke', 'module': 'model_gateway.model_invoker', 'owner': False, 'consumer': False, 'plain_method': True}], 'explicit_local_names': ['actual_request', 'after', 'bad_evidence', 'bad_requirement', 'before', 'bundle', 'calls', 'candidate', 'child', 'chosen', 'chunking', 'content', 'content_v2', 'current', 'destination', 'engine', 'entry', 'env', 'evidence', 'expected', 'fact_v2', 'factory', 'failure', 'frame', 'frozen', 'gate', 'head', 'header', 'invocation', 'kwargs', 'manifest', 'marker', 'material', 'observations', 'old_lineage', 'opened', 'original_hash', 'pack', 'package', 'packer', 'packs', 'parent', 'payload', 'process', 'projection', 'prompt', 'proof', 'provider_calls', 'publication', 'raw', 'record', 'request', 'result', 'retrieval_v2', 'role', 'route', 'row', 'rows', 'run', 'selected', 'source', 'state', 'structured', 'summary', 'support', 'support_payload', 'terminal', 'trace', 'unit', 'verifier', 'versions', 'view', 'writer'], 'pure_projection_rows': ['V08', 'V09', 'V10', 'V11', 'V12', 'V13', 'V14', 'V15', 'V18', 'V25'], 'row_native_symbols': {'V20': []}, 'row_exact_final_consumer_symbols': {'V20': []}, 'generic_fields': ['exact_producer_input_material', 'positive_exact_owner_result', 'negative_mutation_and_rejection', 'final_consumer_observation', 'actual_control_revisions', 'physical_dispatch_count', 'semantic_commit_delta'], 'D5_source_decision': {'path': 'D:\\MyCode\\rag-agent-shared\\docs\\fpbca_bcd_closure_design_v0_2_20261007\\03_D_VERIFICATION_DESIGN_V0_2.md', 'role': 'FROZEN_D1_D2_D5_DISPATCH_MUTATION_ACCOUNTING_WHERE_RELEVANT', 'sha256': '86c300eb36233b96858168d33aa2d69167de1c93c18a115b1fc4dc387b9aa085'}, 'original_module_source': {'path': 'D:\\MyCode\\rag-agent-shared\\backend\\tests\\test_fpbca_bcd_source_bootstrap.py', 'sha256': '2973d604e7ed521be3bff8802f2da81207a7d1d1774a91a1dcdedd715eb28414'}}

@_fpbca_pytest.fixture(scope='function', autouse=True)
def _fpbca_native_case_capture():
    capture = _FPBCA_NativeCapture(_FPBCA_DECLARATION).install(native_namespaces=(globals(), vars(_fpbca_base)))
    try:
        yield capture
    finally:
        capture.close()

@pytest.mark.parametrize('entry', ['run_demo.py', 'workflow_control.py', 'mainline_runtime.py', 'run_pipeline.py'])
def test_native_observed_cached_foreign_package_rejected_before_any_project_loader(tmp_path, entry, _fpbca_native_case_capture):
    try:
        foreign = tmp_path / 'foreign'
        core = foreign / 'core'
        core.mkdir(parents=True)
        marker = tmp_path / 'FOREIGN_EXECUTED'
        (core / 'config.py').write_text('from pathlib import Path\nPath(' + repr(str(marker)) + ').write_text("executed")\nraise RuntimeError("FOREIGN_LOADER")', encoding='utf-8')
        (core / 'hashing.py').write_text('from pathlib import Path\nPath(' + repr(str(marker)) + ').write_text("executed")\nraise RuntimeError("FOREIGN_LOADER")', encoding='utf-8')
        code = "import runpy,sys,types\nfrom pathlib import Path\nforeign=Path(F);m=types.ModuleType('core');m.__file__=str(foreign/'core/__init__.py');m.__path__=[str(foreign/'core')];sys.modules['core']=m\nsys.path[:0]=[str(R/'scripts'),str(R/'backend'),str(foreign)]\ntry:runpy.run_path(str(R/'scripts'/ENTRY),run_name='fpbca_test_import')\nexcept BaseException as exc:print(type(exc).__name__,str(exc))\n"
        code = 'from pathlib import Path\nR=Path(' + repr(str(ROOT)) + ');F=' + repr(str(foreign)) + ';ENTRY=' + repr(entry) + '\n' + code
        process = subprocess.run([sys.executable, '-I', '-B', '-c', code], capture_output=True, text=True, timeout=45)
        assert process.returncode == 0, process.stderr
        assert not marker.exists(), process.stdout
        assert 'source' in process.stdout.lower() or 'foreign' in process.stdout.lower(), process.stdout
    finally:
        _fpbca_native_case_capture.emit(root=tmp_path, rows=['V20'], local_values=locals(), selector='backend/tests/test_fpbca_final65_obs_fpbca_bcd_source_bootstrap.py::test_native_observed_cached_foreign_package_rejected_before_any_project_loader', native_source={'path': 'D:\\MyCode\\rag-agent-shared\\backend\\tests\\test_fpbca_bcd_source_bootstrap.py', 'sha256': '2973d604e7ed521be3bff8802f2da81207a7d1d1774a91a1dcdedd715eb28414', 'function': 'test_cached_foreign_package_rejected_before_any_project_loader', 'line': 7, 'native_operation': 'EXACT_ORIGINAL_NATIVE_TEST_FLOW', 'native_refs': []}, native_local_names=[])
