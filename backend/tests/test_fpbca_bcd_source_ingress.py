"""DRAFT ONLY: proposed backend/tests/test_fpbca_bcd_source_ingress.py.

Not imported, collected or executed. Keep the original four RED nodes unchanged.
Only source imports/CLI help, inert metadata attacks and authored local children.
No business runtime method, Provider, network client, canonical commit or campaign.
Future harness supplies isolated E: temp and its existing offline controls.
"""
from pathlib import Path
import json
import os
import subprocess
import sys

import pytest


ROOT = Path(__file__).resolve().parents[2]
ENTRIES = ["run_demo.py", "workflow_control.py", "mainline_runtime.py", "run_pipeline.py"]

_RAG_CHECKOUT_FILE_MARKERS = (
    "backend/apps/enterprise_document/__init__.py",
    "scripts/mainline_runtime.py",
    "backend/core/config.py",
    "backend/bootstrap/supervisor_factory.py",
    "backend/schemas/task.py",
)

_SOURCE_PROBE_TRACKING_OFF = {
    "LANGSMITH_TEST_TRACKING": "false",
    "LANGCHAIN_TEST_TRACKING": "false",
    "LANGSMITH_TRACING_V2": "false",
    "LANGCHAIN_TRACING_V2": "false",
    "LANGSMITH_TRACING": "false",
    "LANGCHAIN_TRACING": "false",
}


def _source_probe_child_environment(*, ordinary_pytest=False, updates=None):
    env = dict(os.environ)
    if updates:
        env.update(updates)
    env.update(_SOURCE_PROBE_TRACKING_OFF)
    if ordinary_pytest:
        env.pop("PYTEST_DISABLE_PLUGIN_AUTOLOAD", None)
    return env


def _bootstrap_code():
    return """
import importlib.util as _util
import sys
from pathlib import Path
_ROOT = Path(ROOT_PATH).resolve()
_helper_file=_ROOT/'source_bootstrap.py'
if 'source_bootstrap' in sys.modules:
    _module=sys.modules['source_bootstrap']
    try:
        _values=vars(_module);_spec=_values.get('__spec__')
        if (_spec is None or _values.get('__path__') is not None
            or _spec.submodule_search_locations is not None
            or Path(_values['__file__']).resolve()!=_helper_file
            or Path(_spec.origin).resolve()!=_helper_file
            or not callable(_values.get('bootstrap_source_root'))):
            raise ImportError('SOURCE_ROOT_REJECTED: cached child helper')
    except (KeyError,TypeError,ValueError,AttributeError,OSError) as exc:
        raise ImportError('SOURCE_ROOT_REJECTED: malformed cached child helper') from exc
else:
    _spec = _util.spec_from_file_location('source_bootstrap',_helper_file)
    if _spec is None or _spec.loader is None:
        raise ImportError('SOURCE_ROOT_REJECTED: unavailable child helper')
    _module = _util.module_from_spec(_spec)
    sys.modules['source_bootstrap'] = _module
    _spec.loader.exec_module(_module)
_module.bootstrap_source_root(_ROOT)
"""


def _child(tmp_path, body, *, bootstrap=True, env_updates=None, ordinary_pytest=False):
    code = "ROOT_PATH = " + repr(str(ROOT)) + "\n"
    if bootstrap:
        code += _bootstrap_code()
    else:
        code += "import sys\nfrom pathlib import Path\n_ROOT=Path(ROOT_PATH).resolve()\n"
    code += body
    env = _source_probe_child_environment(
        ordinary_pytest=ordinary_pytest, updates=env_updates,
    )
    # No source path allowance comes from PYTHONPATH under this real -I child.
    process = subprocess.run(
        [sys.executable, "-I", "-B", "-c", code], cwd=ROOT,
        env=env, capture_output=True, text=True, timeout=45,
    )
    return process


def _receipt(process):
    assert process.returncode == 0, process.stdout + process.stderr
    lines = [line for line in process.stdout.splitlines() if line.startswith("SOURCE_RECEIPT=")]
    assert len(lines) == 1, process.stdout + process.stderr
    return json.loads(lines[0].partition("=")[2])


def _rejection(process):
    assert process.returncode != 0, process.stdout + process.stderr
    assert "SOURCE_ROOT_REJECTED" in process.stdout + process.stderr


@pytest.mark.parametrize("entry", ENTRIES)
def test_owned_entry_import_is_managed_before_project_imports(tmp_path, entry):
    body = "import runpy, json, os\n"
    body += "runpy.run_path(str(_ROOT/'scripts'/" + repr(entry) + "), run_name='fpbca_source_import')\n"
    body += "_module.assert_loaded_project_origins()\nimport pydantic, anyio\n"
    body += "print('SOURCE_RECEIPT='+json.dumps({'root':str(_ROOT),'pid':os.getpid(),'dependencies':[pydantic.__file__,anyio.__file__]}))\n"
    receipt = _receipt(_child(tmp_path, body))
    assert Path(receipt["root"]).resolve() == ROOT
    assert receipt["pid"] != os.getpid()
    assert len(receipt["dependencies"]) == 2


@pytest.mark.parametrize("entry", ENTRIES)
def test_owned_entry_actual_isolated_cli_help_uses_trusted_file(tmp_path, entry):
    process = subprocess.run(
        [sys.executable, "-I", "-B", str(ROOT/"scripts"/entry), "--help"],
        cwd=ROOT, env=_source_probe_child_environment(), capture_output=True, text=True, timeout=45,
    )
    assert process.returncode == 0, process.stdout + process.stderr
    # mainline_runtime is a reusable facade without an argparse command.
    if entry != "mainline_runtime.py":
        assert "usage:" in process.stdout.lower()


@pytest.mark.parametrize("carrier", ["spec_origin", "module_path", "spec_locations", "mixed_namespace", "foreign_checkout_alias"])
def test_initial_cache_carriers_reject_without_eviction(tmp_path, carrier):
    foreign = tmp_path/"foreign"
    (foreign/"backend"/"core").mkdir(parents=True)
    (foreign/"scripts").mkdir()
    (foreign/"pyproject.toml").write_text("[project]\nname='inert-foreign'\n", encoding="utf-8")
    marker = tmp_path/"foreign-executed"
    source = foreign/"backend"/"core"/"config.py"
    source.write_text("raise RuntimeError('not executed')\n", encoding="utf-8")
    if carrier == "foreign_checkout_alias":
        for relative in _RAG_CHECKOUT_FILE_MARKERS:
            marker_path = foreign/relative
            marker_path.parent.mkdir(parents=True, exist_ok=True)
            if not marker_path.exists():
                marker_path.write_text("# inert rag-checkout fixture marker\n", encoding="utf-8")
        (foreign/"pyproject.toml").write_text(
            '[project]\nname="enterprise-rag-agent"\n', encoding="utf-8",
        )
    body = """
import importlib.machinery as machinery, types
foreign=Path(FOREIGN); source=Path(SOURCE)
name='foreign_checkout_alias' if CARRIER=='foreign_checkout_alias' else 'core'
owned=_ROOT/'backend/core/config.py'
m=types.ModuleType(name);m.__file__=str(owned)
s=machinery.ModuleSpec(name,loader=None,origin=str(owned),is_package=True)
s.submodule_search_locations=[str(_ROOT/'backend/core')]
m.__path__=[str(_ROOT/'backend/core')];m.__spec__=s
if CARRIER=='spec_origin':s.origin=str(source)
elif CARRIER=='module_path':m.__path__=[str(source.parent)]
elif CARRIER=='spec_locations':s.submodule_search_locations=[str(source.parent)]
elif CARRIER=='mixed_namespace':
    m.__file__=None;s.origin=None
    m.__path__=[str(_ROOT/'backend/core'),str(source.parent)]
    s.submodule_search_locations=list(m.__path__)
else:m.__file__=str(source);s.origin=str(source)
sys.modules[name]=m
try:
    BOOTSTRAP
except ImportError as exc:
    assert 'SOURCE_ROOT_REJECTED' in str(exc)
    assert sys.modules[name] is m
    import json
    print('SOURCE_RECEIPT='+json.dumps({'rejected':True,'cache_retained':True}))
else:raise AssertionError('foreign cached carrier accepted')
"""
    body = body.replace("BOOTSTRAP", _bootstrap_code().strip().replace("\n", "\n    "))
    prefix = "FOREIGN="+repr(str(foreign))+"\nSOURCE="+repr(str(source))+"\nCARRIER="+repr(carrier)+"\n"
    assert _receipt(_child(tmp_path, prefix+body, bootstrap=False))["cache_retained"]
    assert not marker.exists()


@pytest.mark.parametrize("resolution", ["ordinary_import", "public_find_spec"])
@pytest.mark.parametrize("attack", ["foreign_origin", "foreign_loader_filename", "mixed_namespace"])
def test_later_prepended_finder_actual_adopted_spec_rejected(tmp_path, resolution, attack):
    foreign = tmp_path/"foreign"; foreign.mkdir()
    marker = tmp_path/"FOREIGN_LOADER_EXECUTED"
    source = foreign/"probe.py"; source.write_text("# inert file\n", encoding="utf-8")
    body = """
import importlib, importlib.machinery as machinery, json
name='core._fpbca_d3_probe'
foreign=Path(FOREIGN);marker=Path(MARKER);source=foreign/'probe.py'
owned=_ROOT/'backend/core/config.py'
assert machinery.PathFinder.find_spec(name,[str(_ROOT/'backend/core')]) is None
class Loader:
    def create_module(self,spec):return None
    def get_filename(self,fullname):return str(source)
    def exec_module(self,module):marker.write_text('executed');raise AssertionError('foreign loader ran')
class Finder:
    def find_spec(self,fullname,path=None,target=None):
        if fullname!=name:return None
        if ATTACK=='mixed_namespace':
            spec=machinery.ModuleSpec(fullname,loader=None,is_package=True)
            spec.submodule_search_locations=[str(_ROOT/'backend/core'),str(foreign)]
            return spec
        return machinery.ModuleSpec(fullname,Loader(),origin=str(owned if ATTACK=='foreign_loader_filename' else source))
sys.meta_path.insert(0,Finder())
try:
    if RESOLUTION=='ordinary_import':importlib.import_module(name)
    else:_util.find_spec(name)
except ImportError as exc:
    assert 'SOURCE_ROOT_REJECTED' in str(exc)
else:raise AssertionError('foreign adopted spec accepted')
assert not marker.exists()
print('SOURCE_RECEIPT='+json.dumps({'rejected':True,'loader_executed':False,'pathfinder_missing':True}))
"""
    prefix = "FOREIGN="+repr(str(foreign))+"\nMARKER="+repr(str(marker))+"\nATTACK="+repr(attack)+"\nRESOLUTION="+repr(resolution)+"\n"
    assert _receipt(_child(tmp_path, prefix+body))["loader_executed"] is False


def test_public_relative_find_spec_uses_the_same_actual_guard(tmp_path):
    foreign = tmp_path/"foreign.py"; foreign.write_text("# inert\n", encoding="utf-8")
    body = """
import importlib.machinery as machinery,json
class Finder:
    def find_spec(self,fullname,path=None,target=None):
        if fullname=='core._fpbca_relative':return machinery.ModuleSpec(fullname,loader=None,origin=FOREIGN)
        return None
sys.meta_path.insert(0,Finder())
try:_util.find_spec('._fpbca_relative','core')
except ImportError as exc:assert 'SOURCE_ROOT_REJECTED' in str(exc)
else:raise AssertionError('relative spec accepted')
print('SOURCE_RECEIPT='+json.dumps({'rejected':True}))
"""
    assert _receipt(_child(tmp_path, "FOREIGN="+repr(str(foreign))+"\n"+body))["rejected"]


def test_managed_reusable_mainline_later_finder_cannot_execute_foreign_body(tmp_path):
    foreign = tmp_path/"mainline_runtime.py"
    marker = tmp_path/"FOREIGN_MAINLINE_BODY"
    foreign.write_text("# test-owned foreign file\n",encoding="utf-8")
    body = """
import importlib,importlib.machinery as machinery,json
class Loader:
    def create_module(self,spec):return None
    def get_filename(self,fullname):return FOREIGN
    def exec_module(self,module):Path(MARKER).write_text('executed');raise AssertionError('foreign mainline executed')
class Finder:
    def find_spec(self,fullname,path=None,target=None):
        if fullname=='mainline_runtime':return machinery.ModuleSpec(fullname,Loader(),origin=FOREIGN)
        return None
sys.meta_path.insert(0,Finder())
try:importlib.import_module('mainline_runtime')
except ImportError as exc:assert 'SOURCE_ROOT_REJECTED' in str(exc)
else:raise AssertionError('foreign reusable mainline accepted')
assert not Path(MARKER).exists()
print('SOURCE_RECEIPT='+json.dumps({'foreign_body_executed':False}))
"""
    prefix = "FOREIGN="+repr(str(foreign))+"\nMARKER="+repr(str(marker))+"\n"
    assert _receipt(_child(tmp_path,prefix+body))["foreign_body_executed"] is False


def test_owned_namespace_spec_and_same_root_reinitialization_preserve_hooks(tmp_path):
    body = """
import importlib,importlib._bootstrap as b,importlib.machinery as machinery,json
first=(b._find_spec,_util._find_spec,_util.find_spec)
_module.bootstrap_source_root(_ROOT)
assert first==(b._find_spec,_util._find_spec,_util.find_spec)
class Finder:
    def find_spec(self,fullname,path=None,target=None):
        if fullname=='core._fpbca_owned_namespace':
            s=machinery.ModuleSpec(fullname,loader=None,is_package=True)
            s.submodule_search_locations=[str(_ROOT/'backend/core')];return s
        return None
sys.meta_path.insert(0,Finder())
n=importlib.import_module('core._fpbca_owned_namespace')
assert list(n.__path__)==[str(_ROOT/'backend/core')]
_module.assert_loaded_project_origins()
import pydantic,anyio
print('SOURCE_RECEIPT='+json.dumps({'same_hooks':True,'owned_namespace':True,'dependency_files':[pydantic.__file__,anyio.__file__]}))
"""
    receipt = _receipt(_child(tmp_path, body))
    assert receipt["same_hooks"] and receipt["owned_namespace"]


def test_cached_public_spec_and_post_origin_assertion_reject_retained_contamination(tmp_path):
    foreign = tmp_path/"foreign.py"; foreign.write_text("# inert\n", encoding="utf-8")
    body = """
import core,types,importlib.machinery as machinery,json
name='core._fpbca_cached_probe';m=types.ModuleType(name)
m.__file__=FOREIGN;m.__spec__=machinery.ModuleSpec(name,loader=None,origin=FOREIGN)
sys.modules[name]=m
for check in (lambda:_util.find_spec(name),_module.assert_loaded_project_origins):
    try:check()
    except ImportError as exc:assert 'SOURCE_ROOT_REJECTED' in str(exc)
    else:raise AssertionError('cached foreign spec accepted')
assert sys.modules[name] is m
print('SOURCE_RECEIPT='+json.dumps({'cached_object_retained':True,'public_rejected':True,'postcheck_rejected':True}))
"""
    # A later manual sys.modules injection is inspected by public/post checks;
    # no claim that ordinary cached-import fast paths call _find_spec.
    assert _receipt(_child(tmp_path, "FOREIGN="+repr(str(foreign))+"\n"+body))["postcheck_rejected"]


@pytest.mark.parametrize("attack", ["retarget", "hook_replacement", "helper_alias"])
def test_same_process_helper_cannot_retarget_repair_or_duplicate_hooks(tmp_path, attack):
    body = """
import importlib._bootstrap as b,json,os
first=(b._find_spec,_util._find_spec,_util.find_spec)
try:
    if ATTACK=='retarget':
        os.environ['RAG_AGENT_SOURCE_ROOT']=FOREIGN
        _module.bootstrap_source_root(_ROOT)
    elif ATTACK=='hook_replacement':
        b._find_spec=_module._ORIGINAL_ACTUAL_FIND_SPEC
        _module.bootstrap_source_root(_ROOT)
    else:
        alias_spec=_util.spec_from_file_location('fpbca_helper_alias',_ROOT/'source_bootstrap.py')
        alias=_util.module_from_spec(alias_spec);sys.modules['fpbca_helper_alias']=alias
        alias_spec.loader.exec_module(alias)
except ImportError as exc:assert 'SOURCE_ROOT_REJECTED' in str(exc)
else:raise AssertionError('invalid helper entry accepted')
if ATTACK!='hook_replacement':assert first==(b._find_spec,_util._find_spec,_util.find_spec)
else:assert b._find_spec is _module._ORIGINAL_ACTUAL_FIND_SPEC
print('SOURCE_RECEIPT='+json.dumps({'rejected':True,'no_repair_or_rewrap':True}))
"""
    prefix = "ATTACK="+repr(attack)+"\nFOREIGN="+repr(str(tmp_path/"foreign-root"))+"\n"
    assert _receipt(_child(tmp_path, prefix+body))["no_repair_or_rewrap"]


def test_actual_entry_rejects_cached_foreign_helper_before_calling_it(tmp_path):
    marker = tmp_path/"HELPER_BODY_EXECUTED"
    body = """
import types,importlib.machinery as machinery,runpy,json
m=types.ModuleType('source_bootstrap');m.__file__=FOREIGN
m.__spec__=machinery.ModuleSpec('source_bootstrap',loader=None,origin=FOREIGN)
def forbidden(root):Path(MARKER).write_text('executed');raise AssertionError('foreign helper called')
m.bootstrap_source_root=forbidden;sys.modules['source_bootstrap']=m
try:runpy.run_path(str(_ROOT/'scripts/mainline_runtime.py'),run_name='fpbca_probe')
except ImportError as exc:assert 'SOURCE_ROOT_REJECTED' in str(exc)
else:raise AssertionError('cached foreign helper accepted')
assert sys.modules['source_bootstrap'] is m and not Path(MARKER).exists()
print('SOURCE_RECEIPT='+json.dumps({'helper_not_called':True,'cache_retained':True}))
"""
    prefix = "FOREIGN="+repr(str(tmp_path/"foreign_helper.py"))+"\nMARKER="+repr(str(marker))+"\n"
    assert _receipt(_child(tmp_path, prefix+body, bootstrap=False))["helper_not_called"]


def test_ordinary_pytest_child_probe(pytestconfig):
    if os.environ.get("FPBCA_D3_CHILD_PROBE") != "1":
        pytest.skip("D3_CHILD_ONLY_UNMARKED: leaf requires separate actual ordinary-pytest child receipt; this SKIP is not PASS")
    helper = sys.modules["source_bootstrap"]
    helper.assert_loaded_project_origins()
    import core.config
    import pydantic, anyio
    import importlib.metadata
    plugins = [name for name, plugin in pytestconfig.pluginmanager.list_name_plugin() if plugin is not None]
    installed = [ep.name for ep in importlib.metadata.entry_points().select(group="pytest11")]
    receipt = {"root":str(ROOT),"pid":os.getpid(),"startup_plugins":plugins,
               "installed_pytest11":installed,
               "dependency_files":[pydantic.__file__,anyio.__file__],"owned_core":core.config.__file__}
    from langsmith import utils as _ls_utils
    receipt["langsmith_test_tracking_disabled"] = _ls_utils.test_tracking_is_disabled()
    receipt["langsmith_tracing_enabled"] = _ls_utils.tracing_is_enabled()
    receipt["tracking_off_env"] = {
        key: os.environ.get(key) for key in _SOURCE_PROBE_TRACKING_OFF
    }
    receipt["scope"] = "D3_ACTUAL_ORDINARY_PYTEST_CHILD_LEAF"
    Path(os.environ["FPBCA_D3_CHILD_RECEIPT"]).write_text(json.dumps(receipt),encoding="utf-8")
    assert receipt["langsmith_test_tracking_disabled"] is True
    assert receipt["langsmith_tracing_enabled"] is False
    assert receipt["tracking_off_env"] == _SOURCE_PROBE_TRACKING_OFF


def test_actual_ordinary_pytest_command_records_its_startup_and_source_boundary(tmp_path):
    receipt_file = tmp_path/"ordinary.json"
    env = _source_probe_child_environment(
        ordinary_pytest=True,
        updates={"FPBCA_D3_CHILD_PROBE":"1", "FPBCA_D3_CHILD_RECEIPT":str(receipt_file)},
    )
    selector = str(Path(__file__).resolve())+"::test_ordinary_pytest_child_probe"
    process = subprocess.run(
        [sys.executable,"-I","-B","-m","pytest",selector,"-q","--basetemp",str(tmp_path/"ordinary-temp")],
        cwd=ROOT,env=env,capture_output=True,text=True,timeout=60,
    )
    assert process.returncode == 0, process.stdout + process.stderr
    receipt = json.loads(receipt_file.read_text(encoding="utf-8"))
    assert Path(receipt["root"]).resolve() == ROOT
    assert receipt["pid"] != os.getpid() and receipt["startup_plugins"]
    assert Path(receipt["owned_core"]).resolve().is_relative_to(ROOT)
    assert set(receipt["installed_pytest11"]).issubset(receipt["startup_plugins"])
    assert receipt["langsmith_test_tracking_disabled"] is True
    assert receipt["langsmith_tracing_enabled"] is False
    assert receipt["tracking_off_env"] == _SOURCE_PROBE_TRACKING_OFF
    assert receipt["scope"] == "D3_ACTUAL_ORDINARY_PYTEST_CHILD_LEAF"


def test_pytest_main_rejects_inert_preexisting_cache_at_conftest_boundary(tmp_path):
    receipt_file = tmp_path/"must-not-enter-leaf.json"
    foreign = tmp_path/"foreign";foreign.mkdir()
    body = """
import types,importlib.machinery as machinery,os
m=types.ModuleType('core');m.__file__=str(Path(FOREIGN)/'core.py')
m.__path__=[FOREIGN];m.__spec__=machinery.ModuleSpec('core',loader=None,origin=m.__file__,is_package=True)
m.__spec__.submodule_search_locations=[FOREIGN];sys.modules['core']=m
import pytest
raise SystemExit(pytest.main([SELECTOR,'-q','--basetemp',BASETEMP]))
"""
    prefix = "FOREIGN="+repr(str(foreign))+"\nSELECTOR="+repr(str(Path(__file__).resolve())+"::test_ordinary_pytest_child_probe")+"\nBASETEMP="+repr(str(tmp_path/"negative-pytest-temp"))+"\n"
    env = {"FPBCA_D3_CHILD_PROBE":"1","FPBCA_D3_CHILD_RECEIPT":str(receipt_file)}
    _rejection(_child(tmp_path, prefix+body, bootstrap=False, env_updates=env, ordinary_pytest=True))
    assert not receipt_file.exists()
    # This proves conftest failure before test body. It does not assert zero
    # project code executed by interpreter/site/plugin startup before conftest.


def test_real_windows_spawn_child_rebootstraps_and_records_its_own_root(tmp_path):
    if os.name != "nt":
        pytest.skip("Windows spawn proof requires the supported Windows host")
    driver = tmp_path/"spawn_source_driver.py"
    receipt_file = tmp_path/"spawn.json"
    # Driver source is also a separate draft reviewed with this file. Insert the
    # literal trusted ROOT and RECEIPT path, never let inherited env select root.
    driver_text = SPAWN_DRIVER_SOURCE_TEMPLATE
    driver_text = driver_text.replace("__ROOT_LITERAL__",repr(str(ROOT)))
    driver_text = driver_text.replace("__RECEIPT_LITERAL__",repr(str(receipt_file)))
    driver.write_text(driver_text,encoding="utf-8")
    process = subprocess.run([sys.executable,"-I","-B",str(driver)],cwd=ROOT,
                             env=_source_probe_child_environment(),capture_output=True,text=True,timeout=60)
    assert process.returncode == 0, process.stdout + process.stderr
    receipt = json.loads(receipt_file.read_text(encoding="utf-8"))
    assert receipt["child_pid"] != receipt["parent_pid"]
    assert receipt["child_root"] == receipt["parent_root"] == str(ROOT)
    assert receipt["module_top_bootstrap"] and receipt["target_rebootstrap"]


def test_managed_child_inherited_root_cannot_retarget_trusted_file(tmp_path):
    process = _child(tmp_path,"raise AssertionError('retargeted child accepted')\n",
                     env_updates={"RAG_AGENT_SOURCE_ROOT":str(tmp_path/"foreign-root")})
    _rejection(process)


def test_generic_third_party_repo_and_cached_alias_are_not_reclassified_as_rag_source(tmp_path):
    foreign = tmp_path/"third-party-library"
    (foreign/"backend").mkdir(parents=True)
    (foreign/"scripts").mkdir()
    (foreign/"pyproject.toml").write_text(
        '[project]\nname="independent-third-party-library"\n', encoding="utf-8",
    )
    source = foreign/"backend"/"library.py"
    source.write_text("# inert third-party library file\n", encoding="utf-8")
    body = """
import types,importlib.machinery as machinery,json
foreign=Path(FOREIGN);source=Path(SOURCE)
name='third_party_library_alias'
assert not _module._is_project_name(name)
m=types.ModuleType(name);m.__file__=str(source)
m.__spec__=machinery.ModuleSpec(name,loader=None,origin=str(source))
sys.modules[name]=m
sys.path.insert(0,str(foreign/'backend'))
assert _module._recognized_checkout(source) is None
_module.bootstrap_source_root(_ROOT)
_module.assert_loaded_project_origins()
assert sys.modules[name] is m
assert str(foreign/'backend') in sys.path
import pydantic,anyio
print('SOURCE_RECEIPT='+json.dumps({
    'third_party_cache_retained':True,'third_party_path_retained':True,
    'not_rag_checkout':True,'dependency_files':[pydantic.__file__,anyio.__file__],
}))
"""
    prefix = "FOREIGN="+repr(str(foreign))+"\nSOURCE="+repr(str(source))+"\n"
    receipt = _receipt(_child(tmp_path,prefix+body))
    assert receipt["third_party_cache_retained"] and receipt["third_party_path_retained"]
    assert receipt["not_rag_checkout"] and len(receipt["dependency_files"]) == 2

# Exact companion driver template; placeholders are replaced with reviewed
# literals above, never executed/imported as .py.txt by this draft preparation.
SPAWN_DRIVER_SOURCE_TEMPLATE = r'''
from pathlib import Path
import importlib.util as _util
import json
import os
import sys

_ROOT = Path(__ROOT_LITERAL__).resolve()
_RECEIPT = Path(__RECEIPT_LITERAL__)
_helper_file=_ROOT/'source_bootstrap.py'
if 'source_bootstrap' in sys.modules:
    _source_module=sys.modules['source_bootstrap']
    try:
        _values=vars(_source_module);_source_spec=_values.get('__spec__')
        if (_source_spec is None or _values.get('__path__') is not None
            or _source_spec.submodule_search_locations is not None
            or Path(_values['__file__']).resolve()!=_helper_file
            or Path(_source_spec.origin).resolve()!=_helper_file
            or not callable(_values.get('bootstrap_source_root'))):
            raise ImportError('SOURCE_ROOT_REJECTED: cached spawn helper')
    except (KeyError,TypeError,ValueError,AttributeError,OSError) as exc:
        raise ImportError('SOURCE_ROOT_REJECTED: malformed cached spawn helper') from exc
else:
    _source_spec=_util.spec_from_file_location('source_bootstrap',_helper_file)
    if _source_spec is None or _source_spec.loader is None:
        raise ImportError('SOURCE_ROOT_REJECTED: unavailable spawn helper')
    _source_module=_util.module_from_spec(_source_spec)
    sys.modules['source_bootstrap']=_source_module
    _source_spec.loader.exec_module(_source_module)
# This module top is executed again as __mp_main__ in the real spawn child.
_MODULE_TOP_RECEIPT = _source_module.bootstrap_source_root(_ROOT)
_MODULE_TOP_PID = os.getpid()


def child_target(parent_pid, parent_root):
    target_receipt = _source_module.bootstrap_source_root(_ROOT)
    import core.hashing
    import pydantic, anyio
    _source_module.assert_loaded_project_origins()
    _RECEIPT.write_text(json.dumps({
        'child_pid':os.getpid(),'parent_pid':parent_pid,
        'child_root':target_receipt['source_root'],'parent_root':parent_root,
        'module_top_bootstrap':_MODULE_TOP_PID==os.getpid() and _MODULE_TOP_RECEIPT['source_root']==str(_ROOT),
        'target_rebootstrap':target_receipt['source_root']==str(_ROOT),
        'core_file':core.hashing.__file__,
        'dependency_files':[pydantic.__file__,anyio.__file__],
    }),encoding='utf-8')


if __name__ == '__main__':
    import multiprocessing
    child=multiprocessing.get_context('spawn').Process(
        target=child_target,args=(os.getpid(),_MODULE_TOP_RECEIPT['source_root']),
    )
    child.start()
    child.join(45)
    if child.is_alive():
        child.terminate()
        child.join(5)
        raise RuntimeError('INCOMPLETE: test-owned spawn watchdog')
    if child.exitcode != 0:
        raise RuntimeError('spawn child failed: '+str(child.exitcode))
'''
