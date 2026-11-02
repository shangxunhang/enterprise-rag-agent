"""Explicit verification-only pytest plugin; no native owner or archive calls.

Root gates this separate new face before a fresh Source epoch. Actual exact
pytest node/fixture identities are transported by two unchanged Recorder hooks.
"""
import ast
import hashlib
import json
import os
from pathlib import Path
import stat
from types import SimpleNamespace

ROOT = Path(r"D:\MyCode\rag-agent-shared")
RECORDER = ROOT / "docs/fpbca_budget_topup_implementation_20261008/final_campaign_evidence_recorder.py"
RECORDER_SHA = "1a40f18c030534011521118d70ed6e8e90f53ff557293eb3065ead53c89ab85d"
EXPECTED_AST = {'digest': '76d8d324f495a51f83a6b18390dc06d039df7af6cdb0475bf4d0f20fd66ad23c', 'create_bytes': 'e21906dc0c4eba9ee5fdc6746d2960e15ff99124999a61a108dd3ae4968ab37c', 'create_json': '1f773bb986fccbaf2b539f4585523875d2fddf80144a789071cd6e4a2c1a5265', 'selected': 'b13a574938154b9ddc54e55b37b6291919cac8268413d0488898bc759432de42', 'FinalEvidenceRecorder.pytest_collection_finish': '2734960fed6b5bb05a18a3f83c5fbaa34d5c41b00202685d40d11792aeacdf04', 'FinalEvidenceRecorder.pytest_runtest_call': '0712c7168194fb985cb34677bafe4a817ddb3c410ef1968d25270397da015781'}
_STATE = None
_CALLBACKS = None
_GATE = None
_STAGES = {}

def _safe(value, file=True):
    p = Path(value)
    assert p.is_absolute() and "." not in p.parts and ".." not in p.parts
    for ancestor in reversed((p, *p.parents)):
        if ancestor.exists():
            native = ancestor.lstat()
            assert not stat.S_ISLNK(native.st_mode)
            assert not getattr(native, "st_file_attributes", 0) & 0x400
        else:
            assert not file and ancestor == p, str(ancestor)
    assert p.resolve() == p
    if file:
        assert p.is_file()
    return p

def _hash(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()

def _ref(reference):
    p = _safe(reference["path"])
    assert _hash(p) == reference["sha256"], str(p)
    return p

def _load_callbacks(recorder):
    tree = ast.parse(recorder.read_text(encoding="utf-8"))
    names = {"digest", "create_bytes", "create_json", "selected"}
    functions = [n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name in names]
    owner = next(n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == "FinalEvidenceRecorder")
    callbacks = [n for n in owner.body if isinstance(n, ast.FunctionDef) and n.name in
        {"pytest_collection_finish", "pytest_runtest_call"}]
    assert len(functions) == 4 and len(callbacks) == 2
    for node in functions + callbacks:
        key = ("FinalEvidenceRecorder." if node in callbacks else "") + node.name
        assert hashlib.sha256(ast.dump(node, include_attributes=False).encode()).hexdigest() == EXPECTED_AST[key]
    namespace = {"hashlib": hashlib, "json": json, "os": os, "Path": Path}
    exec(compile(ast.Module(body=functions + callbacks, type_ignores=[]), str(recorder), "exec"), namespace)
    return namespace

def pytest_configure(config):
    global _STATE, _CALLBACKS, _GATE
    gate_path = _safe(os.environ["FPBCA_CURRENT_CASE_MAPPING_GATE"])
    assert _hash(gate_path) == os.environ["FPBCA_CURRENT_CASE_MAPPING_GATE_SHA256"]
    gate = json.loads(gate_path.read_text(encoding="utf-8-sig"))
    assert gate["schema_version"] == "fpbca_current_verification_case_mapping_root_gate_v1"
    assert gate["approved_explicit_verification_mapping_face"] is True
    assert gate["native_owner_SQL_archive_or_recovery_calls_allowed"] is False
    assert gate["plugin"] == {"path": str(Path(__file__).resolve()), "sha256": _hash(Path(__file__))}
    assert gate["recorder"] == {"path": str(RECORDER), "sha256": RECORDER_SHA}
    recorder = _ref(gate["recorder"])
    contract_path, authority_path = _ref(gate["contracts"]), _ref(gate["authority"])
    contracts = json.loads(contract_path.read_text(encoding="utf-8-sig"))
    authority = json.loads(authority_path.read_text(encoding="utf-8-sig"))
    rows = gate["rows"]
    assert rows and len(rows) == len(set(rows)) and set(rows) <= set(contracts)
    assert set(rows) <= set(authority["per_row_dependency_manifest"])
    for row in rows:
        dependencies = authority["per_row_dependency_manifest"][row]
        for reference in (gate["plugin"], gate["recorder"], gate["contracts"]):
            assert any(r["path"] == reference["path"] and r["sha256"] == reference["sha256"] for r in dependencies)
    assert list(config.args) == gate["selectors"] and gate["selectors"]
    assert len(gate["selectors"]) == len(set(gate["selectors"]))
    base = _safe(str(Path(config.option.basetemp)), file=False)
    assert base == _safe(gate["basetemp"], file=False)
    assert base == Path(r"E:\rag-agent-evaluation\FPBCA_BCD_LOCAL_TEMP") / gate["label"]
    assert not base.exists(), "NEVER_REUSE_OR_REMOVE_HISTORICAL_BASETEMP"
    execution = _safe(gate["execution_root"], file=False)
    assert execution.parent.is_dir() and execution.is_relative_to(Path(r"E:\rag-agent-evaluation"))
    assert not execution.exists(), "MAPPING_IDENTITY_CREATE_ONCE"
    execution.mkdir(exist_ok=False)
    _CALLBACKS = _load_callbacks(recorder)
    _STATE = SimpleNamespace(rows=tuple(rows), contracts=contracts, basetemp=base,
        execution=execution, collected=[], collected_case_scopes={}, node_roots={})
    _GATE = {**gate, "root_gate": {"path": str(gate_path), "sha256": _hash(gate_path)}}

def pytest_collection_finish(session):
    _CALLBACKS["pytest_collection_finish"](_STATE, session)

def pytest_runtest_call(item):
    _CALLBACKS["pytest_runtest_call"](_STATE, item)

def pytest_runtest_logreport(report):
    event = {"stage": report.when, "outcome": report.outcome, "duration_seconds": report.duration}
    _STAGES.setdefault(report.nodeid, {}).setdefault(report.when, []).append(event)

def pytest_sessionfinish(session, exitstatus):
    assert _STATE is not None
    assert set(_STATE.node_roots) <= set(_STATE.collected)
    roots = {}
    for nodeid, root in _STATE.node_roots.items():
        _safe(str(root), file=False)
        assert root.is_dir() and root.is_relative_to(_STATE.basetemp)
        roots[nodeid] = str(root)
    payload = {"schema_version": "fpbca_exact_current_verification_case_mapping_v1",
        "label": _GATE["label"], "root_gate": _GATE["root_gate"],
        "plugin": _GATE["plugin"], "recorder": _GATE["recorder"],
        "contracts": _GATE["contracts"], "authority": _GATE["authority"],
        "selectors": _GATE["selectors"], "basetemp": str(_STATE.basetemp),
        "collected_exact_nodeids": _STATE.collected,
        "collected_case_scopes": _STATE.collected_case_scopes,
        "node_roots": roots, "stage_reports": _STAGES, "actual_session_exitstatus": int(exitstatus),
        "missing_roots": [n for n in _STATE.collected if n not in roots],
        "selected_original_function_AST_sha256": EXPECTED_AST,
        "scope": "ACTUAL_PYTEST_NODE_CALLSPEC_AND_TMP_PATH_ONLY; NO_NATIVE_VERDICT",
        "native_owner_SQL_archive_recovery_calls": 0,
        "unobserved_parameter_or_root": "ABSENT_UNMEASURED_NO_GUESS"}
    _CALLBACKS["create_json"](_STATE.execution / "case-mapping.json", payload)
