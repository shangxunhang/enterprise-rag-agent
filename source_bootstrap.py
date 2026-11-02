"""Managed source import metadata validation for supported CPython 3.10.

One managed source boundary for the approved CPython 3.10 entry surfaces.
This validates import metadata; it does not sandbox finder/loader method bodies,
manual loader execution, interpreter startup, or plain unmanaged interpreters.
"""
from __future__ import annotations

if __name__ != "source_bootstrap":
    raise ImportError("SOURCE_ROOT_REJECTED: helper must use canonical module name")

import _imp
import importlib
import importlib._bootstrap as _bootstrap
import importlib.util as _util
import os
import re
from pathlib import Path
import sys


_HELPER_FILE = Path(__file__).resolve()
_HELPER_ROOT = _HELPER_FILE.parent
_ROOT = None
_PROJECT_TOPS = frozenset()
_INITIALIZED = False
_INITIALIZATION_ERROR = None
_ORIGINAL_ACTUAL_FIND_SPEC = None
_ORIGINAL_PUBLIC_FIND_SPEC = None
_REQUIRED_NAMES = frozenset({
    "source_bootstrap", "application", "bootstrap", "core", "schemas",
    "agent", "mainline_runtime",
})
_DEPENDENCY_PARTS = frozenset({".venv", "venv", "site-packages", "dist-packages"})


def _reject(message):
    raise ImportError("SOURCE_ROOT_REJECTED: " + message)


def _path(value, label):
    if not isinstance(value, (str, os.PathLike)) or not str(value):
        _reject(label + " has no filesystem location")
    if str(value) in {"built-in", "builtin", "frozen", "namespace"}:
        _reject(label + " is not a filesystem source")
    try:
        return Path(value).resolve()
    except (OSError, TypeError, ValueError) as exc:
        raise ImportError("SOURCE_ROOT_REJECTED: invalid " + label) from exc


def _key(path):
    return os.path.normcase(str(path))


def _within(path, root):
    return path == root or root in path.parents


def _dependency_location(path):
    return any(part.lower() in _DEPENDENCY_PARTS for part in path.parts)


def _owned_path(value, label, *, file=False, directory=False):
    path = _path(value, label)
    if _ROOT is None or not _within(path, _ROOT) or _dependency_location(path):
        _reject(label + " is outside the owned source root: " + str(path))
    if file and not path.is_file():
        _reject(label + " is not an existing source file: " + str(path))
    if directory and not path.is_dir():
        _reject(label + " is not an existing source directory: " + str(path))
    return path


def _locations(values, label):
    if values is None:
        return None
    if isinstance(values, (str, bytes, os.PathLike)):
        _reject(label + " must contain source locations")
    try:
        materialized = tuple(values)
    except (TypeError, ValueError, OSError) as exc:
        raise ImportError("SOURCE_ROOT_REJECTED: invalid " + label) from exc
    if not materialized:
        _reject(label + " has no namespace/package source proof")
    for value in materialized:
        _owned_path(value, label, directory=True)
    return materialized


_RAG_CHECKOUT_FILE_MARKERS = (
    "backend/apps/enterprise_document/__init__.py",
    "scripts/mainline_runtime.py",
    "backend/core/config.py",
    "backend/bootstrap/supervisor_factory.py",
    "backend/schemas/task.py",
)


def _is_rag_checkout(candidate):
    if not candidate.is_dir():
        return False
    if not all((candidate / marker).is_file() for marker in _RAG_CHECKOUT_FILE_MARKERS):
        return False
    try:
        metadata = (candidate / "pyproject.toml").read_text(encoding="utf-8-sig")
    except (OSError, UnicodeError):
        return False
    # Bounded recognition of this repo's existing project marker; not a general
    # TOML parser or a public identity contract. All source markers above apply.
    section = re.search(
        r"(?ms)^[ \t]*\[project\][ \t]*(?:#[^\r\n]*)?\r?\n(.*?)(?=^[ \t]*\[|\Z)",
        metadata,
    )
    return section is not None and re.search(
        r'''(?m)^[ \t]*name[ \t]*=[ \t]*(["'])enterprise-rag-agent\1[ \t]*(?:#[^\r\n]*)?$''',
        section.group(1),
    ) is not None


def _recognized_checkout(path):
    """Recognize only rag-agent source aliases; never select the owned root."""
    if _dependency_location(path):
        return None
    for candidate in (path, *path.parents):
        if _is_rag_checkout(candidate):
            return candidate
    return None


def _foreign_checkout_location(value):
    if value is None:
        return False
    try:
        candidate = _path(value, "source alias")
    except ImportError:
        return False
    checkout = _recognized_checkout(candidate)
    return checkout is not None and checkout != _ROOT


def _is_project_name(fullname):
    return isinstance(fullname, str) and fullname.partition(".")[0] in _PROJECT_TOPS


def _module_values(module, name):
    if module is None:
        _reject("cached project module is None: " + name)
    try:
        return vars(module)
    except TypeError as exc:
        raise ImportError("SOURCE_ROOT_REJECTED: malformed cached " + name) from exc


def _has_foreign_alias(values):
    carriers = [values.get("__file__")]
    spec = values.get("__spec__")
    if spec is not None:
        carriers.append(getattr(spec, "origin", None))
    for locations in (
        values.get("__path__"),
        getattr(spec, "submodule_search_locations", None) if spec is not None else None,
    ):
        if locations is not None and not isinstance(locations, (str, bytes)):
            try:
                carriers.extend(tuple(locations))
            except (TypeError, ValueError, OSError):
                continue
    return any(_foreign_checkout_location(value) for value in carriers)


def _validate_project_spec(fullname, spec, parent_path=None):
    if spec is None:
        if fullname in _REQUIRED_NAMES:
            _reject("required project spec is absent: " + fullname)
        return  # Preserve ordinary absent-submodule resolution semantics.
    if getattr(spec, "name", None) != fullname:
        _reject(fullname + " adopted spec name mismatch")
    origin = getattr(spec, "origin", None)
    locations = _locations(
        getattr(spec, "submodule_search_locations", None), fullname + " spec locations"
    )
    if origin is None:
        if locations is None:
            _reject(fullname + " lacks legitimate namespace source proof")
    else:
        _owned_path(origin, fullname + " spec origin", file=True)
    loader = getattr(spec, "loader", None)
    filename = getattr(loader, "get_filename", None) if loader is not None else None
    if callable(filename):
        try:
            location = filename(fullname)
        except Exception as exc:
            raise ImportError(
                "SOURCE_ROOT_REJECTED: loader filename unavailable: " + fullname
            ) from exc
        _owned_path(location, fullname + " loader filename", file=True)
    if parent_path is not None:
        _locations(parent_path, fullname + " parent locations")


def _validate_cached_module(name, module):
    values = _module_values(module, name)
    file_value = values.get("__file__")
    if file_value is not None:
        _owned_path(file_value, name + " cached file", file=True)
    # Materialize namespace locations before any sys.path normalization.
    module_locations = _locations(values.get("__path__"), name + " cached path")
    spec = values.get("__spec__")
    if spec is None:
        _reject(name + " cached module lacks a source spec")
    _validate_project_spec(name, spec)
    if file_value is None and module_locations is None:
        _reject(name + " cached module lacks file/namespace source proof")


def _validate_cached_project_modules():
    for name, module in tuple(sys.modules.items()):
        if _is_project_name(name):
            _validate_cached_module(name, module)
        elif module is not None:
            try:
                values = vars(module)
            except TypeError:
                continue
            if _has_foreign_alias(values):
                _validate_cached_module(name, module)


def _validate_cached_chain(fullname):
    components = fullname.split(".")
    for count in range(1, len(components) + 1):
        name = ".".join(components[:count])
        if name in sys.modules:
            _validate_cached_module(name, sys.modules[name])


def _validate_parent_locations(fullname, path):
    if path is not None:
        _locations(path, fullname + " requested parent path")


def _spec_has_foreign_alias(spec):
    if spec is None:
        return False
    return _has_foreign_alias({"__spec__": spec})


def _guarded_actual_find_spec(fullname, path=None, target=None):
    project = _is_project_name(fullname)
    if project:
        _validate_cached_chain(fullname)
        _validate_parent_locations(fullname, path)
    spec = _ORIGINAL_ACTUAL_FIND_SPEC(fullname, path, target)
    if project or _spec_has_foreign_alias(spec):
        _validate_project_spec(fullname, spec, parent_path=path)
        _validate_cached_chain(fullname)
    return spec


def _guarded_public_find_spec(name, package=None):
    fullname = _util.resolve_name(name, package) if name.startswith(".") else name
    project = _is_project_name(fullname)
    if project:
        _validate_cached_chain(fullname)
    spec = _ORIGINAL_PUBLIC_FIND_SPEC(name, package)
    if project or _spec_has_foreign_alias(spec):
        _validate_project_spec(fullname, spec)
        _validate_cached_chain(fullname)
    return spec


def _discover_source_names(root):
    names = {"backend", "scripts", "source_bootstrap", "conftest"}
    for parent in (root / "backend", root / "scripts", root / "backend" / "tests"):
        for child in parent.iterdir():
            if child.is_dir() and parent == root / "backend":
                if child.name.isidentifier() and not child.name.startswith("__"):
                    names.add(child.name)
            elif child.is_file() and child.suffix == ".py":
                if child.stem.isidentifier() and child.stem != "__init__":
                    names.add(child.stem)
    return frozenset(names)


def _normalize_source_paths(root):
    owned = (root / "backend", root / "scripts", root)
    retained = []
    affected = set(owned)
    seen = {_key(path) for path in owned}
    for value in sys.path:
        if not isinstance(value, str):
            retained.append(value)
            continue
        path = Path(value or os.getcwd()).resolve()
        checkout = _recognized_checkout(path)
        if checkout is not None and path in {
            checkout, checkout / "backend", checkout / "scripts"
        }:
            affected.add(path)
            if checkout != root:
                continue
        key = _key(path)
        if key in seen:
            affected.add(path)
            continue
        seen.add(key)
        retained.append(value)
    sys.path[:] = [str(path) for path in owned] + retained
    for value in tuple(sys.path_importer_cache):
        if isinstance(value, str):
            path = Path(value or os.getcwd()).resolve()
            if path in affected:
                sys.path_importer_cache.pop(value, None)
    importlib.invalidate_caches()


def _assert_installed_hooks():
    if (
        _bootstrap._find_spec is not _guarded_actual_find_spec
        or _util._find_spec is not _guarded_actual_find_spec
        or _util.find_spec is not _guarded_public_find_spec
    ):
        _reject("installed source hook was replaced")


def bootstrap_source_root(trusted_root=None):
    global _ROOT, _PROJECT_TOPS, _INITIALIZED, _INITIALIZATION_ERROR
    global _ORIGINAL_ACTUAL_FIND_SPEC, _ORIGINAL_PUBLIC_FIND_SPEC
    _imp.acquire_lock()
    try:
        if _INITIALIZATION_ERROR is not None:
            _reject("prior helper initialization failed: " + _INITIALIZATION_ERROR)
        if sys.implementation.name != "cpython" or sys.version_info[:2] != (3, 10):
            _reject("unsupported actual-spec runtime; requires validated CPython 3.10")
        root = _HELPER_ROOT
        if trusted_root is not None and _path(trusted_root, "trusted root") != root:
            _reject("trusted root disagrees with helper file")
        inherited = os.environ.get("RAG_AGENT_SOURCE_ROOT")
        if inherited and _path(inherited, "inherited root") != root:
            _reject("inherited source root cannot retarget helper")
        if _ROOT is not None and _ROOT != root:
            _reject("source root is already bound")
        if _INITIALIZED:
            _assert_installed_hooks()
            _validate_cached_project_modules()
            _normalize_source_paths(root)
            _validate_cached_project_modules()
        else:
            _ROOT = root
            _PROJECT_TOPS = _discover_source_names(root)
            _validate_cached_project_modules()
            _normalize_source_paths(root)
            _ORIGINAL_ACTUAL_FIND_SPEC = _bootstrap._find_spec
            _ORIGINAL_PUBLIC_FIND_SPEC = _util.find_spec
            if _util._find_spec is not _ORIGINAL_ACTUAL_FIND_SPEC:
                _reject("stdlib util resolver alias was replaced before bootstrap")
            _bootstrap._find_spec = _guarded_actual_find_spec
            _util._find_spec = _guarded_actual_find_spec
            _util.find_spec = _guarded_public_find_spec
            for name in sorted(_REQUIRED_NAMES):
                _guarded_actual_find_spec(name)
            _validate_cached_project_modules()
            _INITIALIZED = True
        os.environ["RAG_AGENT_SOURCE_ROOT"] = str(root)
        return {"source_root": str(root), "runtime": "CPython3.10", "managed": True}
    except Exception as exc:
        if not _INITIALIZED and _INITIALIZATION_ERROR is None:
            _INITIALIZATION_ERROR = type(exc).__name__ + ": " + str(exc)
        raise
    finally:
        _imp.release_lock()


def assert_loaded_project_origins():
    if not _INITIALIZED:
        _reject("source helper has not initialized successfully")
    _imp.acquire_lock()
    try:
        _assert_installed_hooks()
        _validate_cached_project_modules()
        return {"source_root": str(_ROOT), "loaded_origins_checked": True}
    finally:
        _imp.release_lock()


def prepare_child_environment():
    assert_loaded_project_origins()
    return {**os.environ, "RAG_AGENT_SOURCE_ROOT": str(_ROOT)}
