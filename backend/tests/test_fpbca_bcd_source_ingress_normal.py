"""TXT ONLY: bounded normal-interpreter D3 ingress controls; never run here.

Proposed target: backend/tests/test_fpbca_bcd_source_ingress_normal.py.
Preserve original four RED cases, native33 and its embedded spawn driver.
Each new child runs the actual owned CLI or the unchanged driver with a
test-owned stdlib observation prelude inserted before its existing bootstrap.
No business engine, managed Run recovery, global venv, or source repair.
"""
from pathlib import Path
import hashlib
import json
import os
import subprocess
import sys
import socket

import pytest


ROOT = Path(__file__).resolve().parents[2]
CAPABILITY_PROBE_SOURCE = (Path(sys.executable).resolve().parents[1]/
    "Lib/site-packages/urllib3/util/connection.py").resolve()
CAPABILITY_PROBE_SOURCE_SHA256 = "2633bbdb69731e5ccb5cf4e4afd65605d86c7979cc5633126f50c92d5ad74a74"
ENTRIES = ("run_demo.py", "workflow_control.py", "mainline_runtime.py", "run_pipeline.py")
MARKERS = (
    "backend/apps/enterprise_document/__init__.py", "scripts/mainline_runtime.py",
    "backend/core/config.py", "backend/bootstrap/supervisor_factory.py", "backend/schemas/task.py",
)
TRACKING_OFF = {
    "LANGSMITH_TEST_TRACKING": "false", "LANGCHAIN_TEST_TRACKING": "false",
    "LANGSMITH_TRACING_V2": "false", "LANGCHAIN_TRACING_V2": "false",
    "LANGSMITH_TRACING": "false", "LANGCHAIN_TRACING": "false",
}


# Installed in EACH fresh normal interpreter, including real __mp_main__,
# before executing the trusted launcher/helper and any backend project loader.
# Python profile callbacks reject planned physical owner calls before their
# function body. Socket functions reject network I/O before their native call.
# These are local child test guards; they do not alter production bootstrap.
OBSERVATION_PRELUDE = r'''
import importlib._bootstrap as _d3_boot
import json as _d3_json
import hashlib as _d3_hashlib
import os as _d3_os
from pathlib import Path as _D3Path
import socket as _d3_socket
import sys as _d3_sys
import threading as _d3_threading

_D3_ROOT = _D3Path(__OWNED_ROOT_LITERAL__).resolve()
_D3_CAPABILITY_SOURCE = _D3Path(__CAPABILITY_SOURCE_LITERAL__).resolve()
_D3_CAPABILITY_SOURCE_SHA256 = __CAPABILITY_SHA_LITERAL__
_D3_AUDIT = _D3Path(__AUDIT_ROOT_LITERAL__)
_D3_AUDIT.mkdir(parents=True, exist_ok=True)
_D3_LOG = _D3_AUDIT / (str(_d3_os.getpid()) + '.jsonl')
_D3_MUTEX = _d3_threading.RLock()
_D3_TOPS = {'backend', 'scripts', 'conftest', 'mainline_runtime'}
for _d3_parent in (_D3_ROOT/'backend', _D3_ROOT/'scripts', _D3_ROOT/'backend/tests'):
    for _d3_item in _d3_parent.iterdir():
        if _d3_parent == _D3_ROOT/'backend' and _d3_item.is_dir():
            if _d3_item.name.isidentifier() and not _d3_item.name.startswith('__'):
                _D3_TOPS.add(_d3_item.name)
        elif _d3_item.is_file() and _d3_item.suffix == '.py':
            if _d3_item.stem.isidentifier() and _d3_item.stem != '__init__':
                _D3_TOPS.add(_d3_item.stem)
_D3_FORBIDDEN = {
    (_D3_ROOT/'backend/model_gateway/model_invoker.py', 'invoke'),
    (_D3_ROOT/'backend/model_gateway/fake_llm_client.py', 'generate'),
    (_D3_ROOT/'backend/model_gateway/local_qwen_client.py', 'generate'),
    (_D3_ROOT/'backend/model_gateway/integrations/langchain/chat_model_client.py', 'generate'),
    (_D3_ROOT/'backend/model_gateway/local_hf_runtime.py', 'generate'),
    (_D3_ROOT/'backend/model_gateway/local_hf_runtime.py', 'ensure_loaded'),
    (_D3_ROOT/'backend/model_gateway/local_hf_runtime.py', '_ensure_loaded_unlocked'),
    (_D3_ROOT/'backend/tools/fake_rag_tool.py', 'run'),
    (_D3_ROOT/'backend/rag/rag_engine/parent_child_rag_engine.py', 'run'),
    (_D3_ROOT/'backend/rag/application/parent_child_retrieval.py', 'run'),
}

def _d3_emit(event, **fields):
    row = {'event': event, 'pid': _d3_os.getpid(), **fields}
    with _D3_MUTEX:
        with _D3_LOG.open('a', encoding='utf-8') as stream:
            stream.write(_d3_json.dumps(row, ensure_ascii=False, sort_keys=True) + '\n')
            stream.flush()

class _D3BusinessInvocationForbidden(BaseException):
    pass

def _d3_forbid_network(*args, **kwargs):
    # The pinned installed urllib3 import probes IPv6 by attempting this bind.
    # Deny before native I/O using the Exception branch it already catches;
    # the real library then reports its own unavailable-capability result.
    # Every other socket call retains the original fatal refusal unchanged.
    caller = _d3_sys._getframe(1)
    parent = caller.f_back
    if (not kwargs and len(args) == 2
            and isinstance(args[0], _d3_socket.socket)
            and args[0].family == _d3_socket.AF_INET6
            and args[1] == ('::1', 0)
            and caller.f_code.co_name == '_has_ipv6'
            and caller.f_globals.get('__name__') == 'urllib3.util.connection'
            and caller.f_code is getattr(caller.f_globals.get('_has_ipv6'), '__code__', None)
            and _D3Path(caller.f_code.co_filename).resolve() == _D3_CAPABILITY_SOURCE
            and caller.f_lineno == 127
            and caller.f_locals.get('host') == '::1'
            and caller.f_locals.get('sock') is args[0]
            and parent is not None and parent.f_code.co_name == '<module>'
            and _D3Path(parent.f_code.co_filename).resolve() == _D3_CAPABILITY_SOURCE
            and parent.f_lineno == 137):
        try:
            source_sha256 = _d3_hashlib.sha256(_D3_CAPABILITY_SOURCE.read_bytes()).hexdigest()
        except OSError:
            # An unreadable or changed source is unknown: use the original
            # fatal refusal below, never a swallowed ordinary probe error.
            source_sha256 = None
        if source_sha256 == _D3_CAPABILITY_SOURCE_SHA256:
            _d3_emit('CAPABILITY_PROBE_DENIED_BEFORE_NATIVE_IO',
                     guard='stdlib_socket_pre_project', source_path=str(_D3_CAPABILITY_SOURCE),
                     source_sha256=_D3_CAPABILITY_SOURCE_SHA256,
                     module=caller.f_globals['__name__'], function=caller.f_code.co_name,
                     line=caller.f_lineno, parent_function=parent.f_code.co_name,
                     parent_line=parent.f_lineno, operation='socket.bind',
                     family=int(args[0].family), bind_address=list(args[1]),
                     original_socket_bind_invoked=False,
                     disposition='DENIED_BEFORE_NATIVE_IO', exception='OSError',
                     profile_guard_current=_d3_sys.getprofile() is _d3_profile,
                     socket_guards_current=all(getattr(owner, attr) is _d3_forbid_network
                                              for owner, attr in _D3_SOCKET_PATCHES))
            raise OSError('D3_CAPABILITY_PROBE_DENIED_BEFORE_NATIVE_IO')
    _d3_emit('SOCKET_ATTEMPT_BLOCKED', guard='stdlib_socket_pre_project')
    raise _D3BusinessInvocationForbidden('D3_NETWORK_FORBIDDEN_BEFORE_NATIVE_IO')

_D3_SOCKET_PATCHES = (
    (_d3_socket.socket, 'connect'), (_d3_socket.socket, 'connect_ex'),
    (_d3_socket.socket, 'send'), (_d3_socket.socket, 'sendall'),
    (_d3_socket.socket, 'sendto'), (_d3_socket.socket, 'bind'),
    (_d3_socket.socket, 'listen'), (_d3_socket, 'create_connection'),
    (_d3_socket, 'getaddrinfo'), (_d3_socket, 'gethostbyname'),
    (_d3_socket, 'gethostbyname_ex'),
)
for _d3_owner, _d3_attr in _D3_SOCKET_PATCHES:
    setattr(_d3_owner, _d3_attr, _d3_forbid_network)

_D3_FORBIDDEN_NAMES = frozenset(name for _, name in _D3_FORBIDDEN)

def _d3_profile(frame, event, argument):
    if event == 'call' and frame.f_code.co_name in _D3_FORBIDDEN_NAMES:
        # Exact authored existing-owner source paths; no project import is
        # needed to install the guard before the first project loader.
        if (_D3Path(frame.f_code.co_filename).resolve(), frame.f_code.co_name) in _D3_FORBIDDEN:
            _d3_emit('PROVIDER_OR_RETRIEVAL_ATTEMPT_BLOCKED',
                     path=frame.f_code.co_filename, function=frame.f_code.co_name)
            raise _D3BusinessInvocationForbidden('D3_BUSINESS_INVOCATION_FORBIDDEN_BEFORE_BODY')

_d3_sys.setprofile(_d3_profile)
_d3_threading.setprofile(_d3_profile)
_D3_ORIGINAL_LOAD = _d3_boot._load_unlocked
_D3_FIRST = True

def _d3_observe_load(spec):
    global _D3_FIRST
    if spec.name.partition('.')[0] in _D3_TOPS:
        helper = _d3_sys.modules.get('source_bootstrap')
        initialized = helper is not None and bool(vars(helper).get('_INITIALIZED'))
        root = None if helper is None else vars(helper).get('_ROOT')
        _d3_emit('FIRST_PROJECT_LOADER' if _D3_FIRST else 'PROJECT_LOADER',
                 module=spec.name, origin=spec.origin,
                 locations=None if spec.submodule_search_locations is None else list(spec.submodule_search_locations),
                 native_bootstrap_initialized=initialized, native_source_root=str(root),
                 profile_guard_current=_d3_sys.getprofile() is _d3_profile,
                 socket_guards_current=all(getattr(owner, attr) is _d3_forbid_network for owner, attr in _D3_SOCKET_PATCHES))
        _D3_FIRST = False
        if not initialized or root != _D3_ROOT:
            raise AssertionError('D3_PRE_FIRST_PROJECT_LOADER_BOUNDARY_MISSING')
        if _d3_sys.getprofile() is not _d3_profile:
            raise AssertionError('D3_PROVIDER_PRELOAD_GUARD_REPLACED')
        if not all(getattr(owner, attr) is _d3_forbid_network for owner, attr in _D3_SOCKET_PATCHES):
            raise AssertionError('D3_SOCKET_PRELOAD_GUARD_REPLACED')
        helper._assert_installed_hooks()
        if spec.origin is not None:
            location = _D3Path(spec.origin).resolve()
            if location != _D3_ROOT and _D3_ROOT not in location.parents:
                raise AssertionError('D3_FOREIGN_LOADER_REACHED_OBSERVER')
        if spec.submodule_search_locations is not None:
            if not all(_D3_ROOT == _D3Path(value).resolve() or _D3_ROOT in _D3Path(value).resolve().parents
                       for value in spec.submodule_search_locations):
                raise AssertionError('D3_MIXED_NAMESPACE_REACHED_OBSERVER')
    return _D3_ORIGINAL_LOAD(spec)

_d3_boot._load_unlocked = _d3_observe_load
_d3_emit('GUARDS_INSTALLED_BEFORE_PROJECT_LOAD', initial_project_modules=sorted(
    name for name in _d3_sys.modules if name.partition('.')[0] in _D3_TOPS),
    threads=[thread.name for thread in _d3_threading.enumerate()],
    sys_path=list(_d3_sys.path), pythonpath=_d3_os.environ.get('PYTHONPATH'))
'''


def _foreign_checkout(tmp_path):
    foreign = tmp_path/'foreign-rag-checkout'
    marker = tmp_path/'FOREIGN_PROJECT_LOADER_EXECUTED'
    for relative in MARKERS:
        path = foreign/relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text('# authored inert recognized-checkout marker\n', encoding='utf-8')
    (foreign/'pyproject.toml').write_text('[project]\nname="enterprise-rag-agent"\n', encoding='utf-8')
    for relative in ('backend/core/__init__.py', 'backend/core/hash.py',
                     'backend/core/config.py', 'backend/core/hashing.py',
                     'backend/application/__init__.py', 'backend/application/mainline_service.py',
                     'scripts/mainline_runtime.py'):
        path = foreign/relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text('from pathlib import Path\nPath('+repr(str(marker))+').write_text("FOREIGN_LOADER_EXECUTED")\nraise RuntimeError("INERT_FOREIGN_PROJECT_BODY")\n', encoding='utf-8')
    return foreign, marker


def _environment(foreign, pollution):
    env = {**os.environ, **TRACKING_OFF}
    # These two fresh authored scopes are independent of inherited path order.
    env.pop('PYTHONPATH', None)
    if pollution:
        env['PYTHONPATH'] = os.pathsep.join((str(foreign/'backend'), str(foreign/'scripts'), str(foreign)))
    return env


def _prelude(audit_root):
    assert CAPABILITY_PROBE_SOURCE.is_file()
    assert hashlib.sha256(CAPABILITY_PROBE_SOURCE.read_bytes()).hexdigest()==CAPABILITY_PROBE_SOURCE_SHA256
    return OBSERVATION_PRELUDE.replace('__OWNED_ROOT_LITERAL__', repr(str(ROOT))).replace('__AUDIT_ROOT_LITERAL__', repr(str(audit_root))).replace('__CAPABILITY_SOURCE_LITERAL__', repr(str(CAPABILITY_PROBE_SOURCE))).replace('__CAPABILITY_SHA_LITERAL__', repr(CAPABILITY_PROBE_SOURCE_SHA256))


def _observations(audit_root):
    result = []
    for path in sorted(audit_root.glob('*.jsonl')):
        rows = [json.loads(line) for line in path.read_text(encoding='utf-8').splitlines()]
        result.append({'path':str(path), 'sha256':hashlib.sha256(path.read_bytes()).hexdigest(), 'rows':rows})
    return result


def _receipt(tmp_path, *, process, pollution, audit_root, native=None):
    payload = {'scope':'D3_NORMAL_SUBPROCESS_ACTUAL_ENTRY_NOT_BUSINESS_E2E',
        'parent_pid':os.getpid(), 'polluted_pythonpath':pollution,
        'returncode':process.returncode, 'stdout':process.stdout, 'stderr':process.stderr,
        'actual_process_observations':_observations(audit_root), 'native_spawn_receipt':native}
    path = tmp_path/'normal-ingress-observation.json'
    with path.open('x', encoding='utf-8') as stream:
        json.dump(payload, stream, ensure_ascii=False, indent=2)
    return payload


def _run_child(tmp_path, audit_root, *args, **kwargs):
    try:
        return subprocess.run(*args, **kwargs)
    except subprocess.TimeoutExpired as exc:
        # The stdlib kills/waits for its direct subprocess. Original spawn
        # driver separately owns child.join/terminate. No canonical owner is
        # touched; closure/effects are UNKNOWN until actual handles are read.
        observations = []
        observation_error = None
        try:
            observations = _observations(audit_root)
        except Exception as read_error:
            observation_error = type(read_error).__name__ + ': ' + str(read_error)
        def decode(value):
            return value.decode('utf-8', errors='replace') if isinstance(value, bytes) else value
        payload = {'status':'TEST_TIME_BUDGET_EXHAUSTED', 'e2e':'E2E_INCOMPLETE',
            'production_failure':'NO', 'test_native_process_closure':'UNKNOWN',
            'native_effect_disposition':'UNKNOWN', 'wall_clock_seconds':exc.timeout,
            'stdout':decode(exc.stdout), 'stderr':decode(exc.stderr),
            'actual_observations':observations, 'observation_read_error':observation_error}
        with (tmp_path/'test-time-budget-exhausted.json').open('x',encoding='utf-8') as stream:
            json.dump(payload,stream,ensure_ascii=False,indent=2)
        raise AssertionError('TEST_TIME_BUDGET_EXHAUSTED / E2E_INCOMPLETE / production_failure=NO / native_closure=UNKNOWN') from exc


def _assert_native_loader_rows(observations, *, target_pid=None):
    rows = [row for group in observations for row in group['rows'] if target_pid is None or row['pid']==target_pid]
    installs = [row for row in rows if row['event']=='GUARDS_INSTALLED_BEFORE_PROJECT_LOAD']
    first = [row for row in rows if row['event']=='FIRST_PROJECT_LOADER']
    assert len(installs)==1 and installs[0]['initial_project_modules']==[]
    assert len(first)==1 and first[0]['native_bootstrap_initialized'] is True
    assert first[0]['native_source_root']==str(ROOT)
    assert first[0]['profile_guard_current'] and first[0]['socket_guards_current']
    assert not [row for row in rows if row['event'] in {'SOCKET_ATTEMPT_BLOCKED','PROVIDER_OR_RETRIEVAL_ATTEMPT_BLOCKED'}]
    for row in rows:
        if row['event']!='CAPABILITY_PROBE_DENIED_BEFORE_NATIVE_IO':
            continue
        assert row['guard']=='stdlib_socket_pre_project'
        assert Path(row['source_path']).resolve()==CAPABILITY_PROBE_SOURCE
        assert row['source_sha256']==CAPABILITY_PROBE_SOURCE_SHA256
        assert row['module']=='urllib3.util.connection' and row['function']=='_has_ipv6'
        assert row['line']==127 and row['parent_function']=='<module>' and row['parent_line']==137
        assert row['operation']=='socket.bind' and row['family']==int(socket.AF_INET6)
        assert row['bind_address']==['::1',0]
        assert row['original_socket_bind_invoked'] is False
        assert row['disposition']=='DENIED_BEFORE_NATIVE_IO' and row['exception']=='OSError'
        assert row['profile_guard_current'] and row['socket_guards_current']
    for row in rows:
        if row['event'] not in {'FIRST_PROJECT_LOADER','PROJECT_LOADER'}:
            continue
        if row['origin'] is not None:
            assert Path(row['origin']).resolve().is_relative_to(ROOT)
        for location in row['locations'] or ():
            assert Path(location).resolve().is_relative_to(ROOT)


@pytest.mark.parametrize('entry', ENTRIES)
@pytest.mark.parametrize('pollution', (False, True), ids=('normal-clean','normal-polluted-pythonpath'))
def test_normal_owned_cli_exact_boundary_before_first_project_loader(tmp_path, entry, pollution):
    foreign, marker = _foreign_checkout(tmp_path)
    audit = tmp_path/'audit'
    code = _prelude(audit) + '\nimport runpy, sys\n'
    code += 'sys.argv=['+repr(str(ROOT/'scripts'/entry))+', "--help"]\n'
    code += 'try:\n    runpy.run_path('+repr(str(ROOT/'scripts'/entry))+', run_name="__main__")\n'
    code += 'except SystemExit as exc:\n    if exc.code not in (None,0): raise\n'
    code += '_helper=sys.modules["source_bootstrap"]\n_helper.assert_loaded_project_origins()\n'
    process = _run_child(tmp_path, audit, [sys.executable,'-B','-c',code], cwd=tmp_path,
        env=_environment(foreign,pollution), capture_output=True,text=True,timeout=75)
    payload = _receipt(tmp_path,process=process,pollution=pollution,audit_root=audit)
    assert process.returncode==0,process.stdout+process.stderr
    assert not marker.exists()
    assert entry=='mainline_runtime.py' or 'usage:' in process.stdout.lower()
    _assert_native_loader_rows(payload['actual_process_observations'])


@pytest.mark.parametrize('pollution', (False,True), ids=('normal-spawn-clean','normal-spawn-polluted-pythonpath'))
def test_normal_real_spawn_reuses_original_driver_boundary(tmp_path, pollution):
    from test_fpbca_bcd_source_ingress import SPAWN_DRIVER_SOURCE_TEMPLATE
    foreign, marker = _foreign_checkout(tmp_path)
    audit = tmp_path/'audit'
    native_receipt = tmp_path/'spawn.json'
    driver_text = SPAWN_DRIVER_SOURCE_TEMPLATE
    anchor = "_helper_file=_ROOT/'source_bootstrap.py'"
    assert driver_text.count(anchor)==1
    # Only a child test observation prelude is inserted. Original driver bytes
    # remain literal around this insertion; its real get_context('spawn'),
    # target, module-top helper and target rebootstrap are unchanged.
    driver_text = driver_text.replace(anchor,_prelude(audit)+'\n'+anchor,1)
    driver_text = driver_text.replace('__ROOT_LITERAL__',repr(str(ROOT))).replace('__RECEIPT_LITERAL__',repr(str(native_receipt)))
    driver = tmp_path/'normal_spawn_source_driver.py'
    driver.write_text(driver_text,encoding='utf-8')
    process = _run_child(tmp_path, audit, [sys.executable,'-B',str(driver)],cwd=tmp_path,
        env=_environment(foreign,pollution),capture_output=True,text=True,timeout=75)
    native = json.loads(native_receipt.read_text(encoding='utf-8')) if native_receipt.exists() else None
    payload = _receipt(tmp_path,process=process,pollution=pollution,audit_root=audit,native=native)
    assert process.returncode==0,process.stdout+process.stderr
    assert not marker.exists() and native is not None
    assert native['child_pid']!=native['parent_pid']!=os.getpid()
    assert native['child_root']==native['parent_root']==str(ROOT)
    assert native['module_top_bootstrap'] and native['target_rebootstrap']
    _assert_native_loader_rows(payload['actual_process_observations'],target_pid=native['child_pid'])
    for group in payload['actual_process_observations']:
        assert not [row for row in group['rows'] if row['event'] in {'SOCKET_ATTEMPT_BLOCKED','PROVIDER_OR_RETRIEVAL_ATTEMPT_BLOCKED'}]
