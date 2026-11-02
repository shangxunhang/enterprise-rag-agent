# Python/site/pytest plugin startup precede this boundary.
from pathlib import Path
PROJECT_ROOT = Path(__file__).resolve().parent

import importlib.util as _source_util
import sys as _source_sys
from pathlib import Path as _SourcePath

_source_file = PROJECT_ROOT / 'source_bootstrap.py'
_source_name = 'source_bootstrap'
if _source_name in _source_sys.modules:
    _source_module = _source_sys.modules[_source_name]
    try:
        _source_values = vars(_source_module)
        _source_spec = _source_values.get('__spec__')
        if (
            _source_spec is None
            or _source_values.get('__path__') is not None
            or _source_spec.submodule_search_locations is not None
            or _SourcePath(_source_values['__file__']).resolve() != _source_file
            or _SourcePath(_source_spec.origin).resolve() != _source_file
            or not callable(_source_values.get('bootstrap_source_root'))
        ):
            raise ImportError('SOURCE_ROOT_REJECTED: cached trusted helper')
    except (KeyError, TypeError, ValueError, AttributeError, OSError) as _source_error:
        raise ImportError('SOURCE_ROOT_REJECTED: malformed cached trusted helper') from _source_error
else:
    _source_spec = _source_util.spec_from_file_location(_source_name, _source_file)
    if _source_spec is None or _source_spec.loader is None:
        raise ImportError('SOURCE_ROOT_REJECTED: unavailable trusted helper')
    _source_module = _source_util.module_from_spec(_source_spec)
    _source_sys.modules[_source_name] = _source_module
    # Preserve the cached module if execution/initialization fails; never evict.
    _source_spec.loader.exec_module(_source_module)
_source_contract = _source_module.bootstrap_source_root(PROJECT_ROOT)


def pytest_sessionfinish(session, exitstatus):
    _source_module.assert_loaded_project_origins()
