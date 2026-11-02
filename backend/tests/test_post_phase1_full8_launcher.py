"""Offline contracts for the dedicated Full8 launcher; no Provider network allowed.

All SDK sends terminate at HTTPX MockTransport. Canonical state lives exclusively
under pytest tmp_path. Existing production code and historical state are read only.
"""
from __future__ import annotations

import asyncio
import builtins
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
import copy
from datetime import datetime, timezone
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import socket
import shutil
import subprocess
import sys
import threading
import time
from types import SimpleNamespace
import textwrap
from unittest.mock import patch

import httpx
import pytest

ROOT = Path(__file__).resolve().parents[2]
LAUNCHER = ROOT / "scripts/run_post_phase1_full8.py"
HISTORICAL = ROOT / "docs/rc1_full8_real_e2e_acceptance_20261020_r01/evidence"
TASK, RUN = "task-offline-full8-contract", "run-offline-full8-contract"
NOW = datetime(2026, 10, 21, tzinfo=timezone.utc)
FAKE_KEY = "sk-full8-offline-fake-never-live"
PROXY_SECRET = "offline-proxy-password"


def _write(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")
    return path


def _sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def _protect(path):
    """Use the same protected owner-only filesystem premise as the launcher."""
    import win32api
    import win32con
    import win32security
    import ntsecuritycon
    token = win32security.OpenProcessToken(win32api.GetCurrentProcess(), win32con.TOKEN_QUERY)
    try:
        sid = win32security.GetTokenInformation(token, win32security.TokenUser)[0]
    finally:
        token.Close()
    acl = win32security.ACL()
    acl.AddAccessAllowedAceEx(win32security.ACL_REVISION, 3, ntsecuritycon.FILE_ALL_ACCESS, sid)
    win32security.SetNamedSecurityInfo(str(path), win32security.SE_FILE_OBJECT,
        win32security.DACL_SECURITY_INFORMATION | win32security.PROTECTED_DACL_SECURITY_INFORMATION,
        None, None, acl, None)


def _is_socketpair_control(sock, address):
    """Recognize only stdlib's exact self-created socketpair handshake."""
    code = getattr(getattr(socket, "_fallback_socketpair", None), "__code__", None)
    frame = sys._getframe(1)
    while frame is not None:
        if frame.f_code is code:
            values = frame.f_locals
            listener = values.get("lsock")
            return (values.get("csock") is sock and listener is not None
                and isinstance(address, tuple) and len(address) >= 2
                and address[0] in {"127.0.0.1", "::1"}
                and tuple(address[:2]) == tuple(listener.getsockname()[:2]))
        frame = frame.f_back
    return False


@pytest.fixture(autouse=True)
def no_network_or_real_credentials(monkeypatch, request):
    # A deterministic test parent is independent of host Prompt/proxy/native
    # overrides. Individual negative cases inject them explicitly afterward.
    current = sys.modules.get("post_phase1_full8_launcher_contract_test")
    controlled = set()
    for name in ("_CONFIGURATION_ENVIRONMENT", "_NETWORK_ENVIRONMENT", "_NATIVE_ENVIRONMENT"):
        controlled.update(getattr(current, name, ()))
    for name in list(os.environ):
        if name.upper() in controlled:
            monkeypatch.delenv(name, raising=False)
    windows = Path(os.environ.get("SystemRoot", r"C:\Windows"))
    loader = [Path(sys.executable).parent, Path(sys.prefix) / "Library/bin", windows / "System32", windows]
    monkeypatch.setenv("PATH", os.pathsep.join(str(p) for p in loader if p.is_dir()))
    original_connect = socket.socket.connect
    def denied(*args, **kwargs):
        sys.audit("socket.connect", None, args[0] if args else None)
        raise AssertionError("OFFLINE_CONTRACT_NETWORK_ATTEMPT")
    def connect(sock, address):
        if _is_socketpair_control(sock, address): return original_connect(sock, address)
        sys.audit("socket.connect", sock, address)
        raise AssertionError("OFFLINE_CONTRACT_NETWORK_ATTEMPT")
    def getaddrinfo(*args, **kwargs):
        sys.audit("socket.getaddrinfo", *args)
        raise AssertionError("OFFLINE_CONTRACT_NETWORK_ATTEMPT")
    monkeypatch.setattr(socket.socket, "connect", connect)
    monkeypatch.setattr(socket.socket, "connect_ex", denied)
    monkeypatch.setattr(socket.socket, "sendto", denied)
    monkeypatch.setattr(socket, "create_connection", denied)
    monkeypatch.setattr(socket, "getaddrinfo", getaddrinfo)
    monkeypatch.setenv("DEEPSEEK_API_KEY", FAKE_KEY)
    monkeypatch.setenv("OPENAI_API_KEY", FAKE_KEY)
    monkeypatch.delenv("OPENAI_PROXY", raising=False)
    original_read = Path.read_text
    def no_dotenv(path, *args, **kwargs):
        if path.name == ".env":
            return ""
        return original_read(path, *args, **kwargs)
    monkeypatch.setattr(Path, "read_text", no_dotenv)
    # These original cases deliberately call a Fake Worker in the pytest
    # process. Model the entry's real fresh-child premises without stubbing any
    # production validation: exact built environment, no pre-imported config.
    # The separate native/fresh tests exercise the actual process boundary.
    name = getattr(request.node, "originalname", "")
    case = getattr(getattr(request.node, "callspec", None), "params", {}).get("case")
    if current is not None and ((name == "test_early_error_and_watchdog_preserve_evidence"
            and case not in {"exit7", "watchdog"})
            or (name == "test_credentials_and_proxy_auth_are_redacted" and case == "worker_exception")):
        original_entry = current.run_worker
        def fresh_fake_entry(contract, binding, nonce):
            environment = current.build_child_environment(contract)
            cached = sys.modules.pop("core.config", None)
            try:
                with patch.dict(os.environ, environment, clear=True):
                    return original_entry(contract, binding, nonce)
            finally:
                if cached is not None: sys.modules["core.config"] = cached
                else: sys.modules.pop("core.config", None)
        monkeypatch.setattr(current, "run_worker", fresh_fake_entry)


@pytest.fixture(scope="module")
def launcher():
    # Only this evidence runner can choose the preserved pre-change module for
    # independent red executions. Production source identity remains untouched.
    source = Path(os.environ.get("G07_OPTION_A_TEST_LAUNCHER", str(LAUNCHER)))
    spec = importlib.util.spec_from_file_location("post_phase1_full8_launcher_contract_test", source)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _layout(tmp_path):
    prep, output, rag = (tmp_path / name for name in ("PREP", "OUTPUT", "RAG"))
    for path in (prep, output, rag):
        path.mkdir(parents=True)
        _protect(path)
    for name in ("inputs", "audit", "tmp", "cache"):
        (prep / name).mkdir()
    return {"project_root": str(ROOT), "task_id": TASK, "run_id": RUN,
            "prep_root": str(prep), "output_root": str(output), "rag_root": str(rag)}


def _base_contract(tmp_path):
    contract = _layout(tmp_path)
    prep, rag = Path(contract["prep_root"]), Path(contract["rag_root"])
    project = json.loads((HISTORICAL / "PROJECT_INPUT_V3.json").read_text(encoding="utf-8-sig"))
    policy = json.loads((HISTORICAL / "REAL_E2E_POLICY.json").read_text(encoding="utf-8-sig"))
    old_project, old_policy = prep / "inputs/original_project.json", prep / "inputs/original_policy.json"
    shutil.copyfile(HISTORICAL / "PROJECT_INPUT_V3.json", old_project)
    shutil.copyfile(HISTORICAL / "REAL_E2E_POLICY.json", old_policy)
    project["task_id"] = TASK
    policy["canonical_budget"]["policy_ref"] = policy["canonical_budget"]["policy_ref"].replace("rc1-full8-real-e2e-20261020-r01", RUN)
    project_path = _write(prep / "inputs/PROJECT_INPUT_V3.json", project)
    policy_path = _write(prep / "inputs/REAL_E2E_POLICY.json", policy)
    plan = json.loads((HISTORICAL / "PLAN.json").read_text(encoding="utf-8-sig"))
    plan.update(task_id=TASK, run_id=RUN, output_root=contract["output_root"],
        project_input_path=str(project_path), profile=str(policy_path), rag_root=str(rag))
    plan["environment"]["DATA_ROOT"] = contract["output_root"]
    plan_path = _write(prep / "inputs/PLAN.json", plan)
    artifact = _write(rag / "data/processed/entity.json", {"trusted": "offline-test-entity"})
    manifest = _write(rag / "manifest.json", {"artifacts": [{"path": str(artifact), "sha256": _sha(artifact)}]})
    pointer = _write(rag / "active.json", {"manifest_path": str(manifest), "manifest_sha256": _sha(manifest)})
    contract.update(schema_version="post-phase1-full8-launch-v1", plan_path=str(plan_path),
        plan_sha256=_sha(plan_path), source_sha256={str(LAUNCHER): _sha(LAUNCHER),
            str(ROOT / "docs/post_r13_business_improvements_audits/POST_BCA014_REAL_BUSINESS_E2E_20261003_161008_c0090159/real_business_worker.py"):
            _sha(ROOT / "docs/post_r13_business_improvements_audits/POST_BCA014_REAL_BUSINESS_E2E_20261003_161008_c0090159/real_business_worker.py")},
        input_sha256={str(p): _sha(p) for p in (project_path, policy_path, old_project, old_policy, manifest, pointer, artifact)},
        request_contract={"base_url": "https://api.deepseek.com", "model": "deepseek-v4-flash",
            "path": "/chat/completions", "max_tokens": 65536}, physical_attempt_limit=1200,
        watchdog={"observation_seconds": 30, "forensic_seconds": 2, "reap_seconds": 2},
        projection={"original_project": str(old_project), "project": str(project_path),
            "original_policy": str(old_policy), "policy": str(policy_path),
            "allowed_project_paths": ["task_id"], "allowed_policy_paths": ["canonical_budget.policy_ref"]},
        rag_manifest_path=str(manifest), rag_pointer_path=str(pointer))
    return contract, _write(prep / "inputs/launch_contract.json", contract)


def _contract(tmp_path):
    launcher = sys.modules["post_phase1_full8_launcher_contract_test"]
    contract, path, _, _ = _rag_contract(launcher, tmp_path)
    return contract, path


@pytest.mark.parametrize("change", ["approved", "old_gateway", "arbitrary_gateway", "other_critical", "missing_launcher", "launcher_drift"])
def test_two_blocker_source_identity_closure(launcher, tmp_path, monkeypatch, change):
    contract, path = _contract(tmp_path)
    gateway = ROOT / "backend/model_gateway/model_gateway.py"
    assert launcher.TWO_BLOCKER_SOURCE_SHA256[gateway] == _sha(gateway)
    if change in {"old_gateway", "arbitrary_gateway"}:
        expected = ("c822ca969d9cdd78ac58b3109d98ce035fc3f8d1fc5aac9b42826c20b9595d2f"
                    if change == "old_gateway" else "0" * 64)
        monkeypatch.setattr(launcher, "TWO_BLOCKER_SOURCE_SHA256", {
            **launcher.TWO_BLOCKER_SOURCE_SHA256, gateway: expected})
    elif change == "other_critical":
        entries = json.loads(launcher.DESIGN_RECEIPT.read_text(encoding="utf-8"))["target_source_sha256"]
        approved = (set(launcher.COMPATIBILITY_SOURCE_SHA256) | set(launcher.TWO_BLOCKER_SOURCE_SHA256)
                    | set(launcher.DOTENV_POLICY_SOURCE_SHA256))
        target = next(Path(entry["path"]).resolve() for entry in entries if Path(entry["path"]).resolve() not in approved)
        original_sha = launcher.sha256_file
        monkeypatch.setattr(launcher, "sha256_file", lambda p: "0" * 64 if Path(p).resolve() == target else original_sha(p))
    elif change == "missing_launcher":
        contract["source_sha256"].pop(str(LAUNCHER))
    elif change == "launcher_drift":
        contract["source_sha256"][str(LAUNCHER)] = "0" * 64
    _write(path, contract)
    if change == "approved":
        assert launcher.load_and_validate_launch_contract(path, require_authorization=False)["run_id"] == RUN
    else:
        with pytest.raises(launcher.LaunchContractError):
            launcher.load_and_validate_launch_contract(path, require_authorization=False)


@pytest.mark.parametrize("case", ["approved", "old_config", "arbitrary_config"])
def test_source_policy_config_pin_exact_mapping(launcher, tmp_path, monkeypatch, case):
    contract, path = _contract(tmp_path)
    config_path = ROOT / "backend/core/config.py"
    actual_sha = _sha(config_path)
    assert launcher.DOTENV_POLICY_SOURCE_SHA256 == {config_path: actual_sha}
    if case != "approved":
        before_config = (ROOT / "docs/rc1_full8_dotenv_source_policy_20261022_r01"
                         / "evidence/before_config.py")
        before_sha = _sha(before_config)
        assert before_sha == "cf3926896677c025cd91a2c336fc07b9c3f0e9e9f5c8e76da99ec016fd2877d7"
        assert before_sha != actual_sha
        expected = before_sha if case == "old_config" else "0" * 64
        monkeypatch.setattr(launcher, "DOTENV_POLICY_SOURCE_SHA256", {config_path: expected})
    if case == "approved":
        assert launcher.load_and_validate_launch_contract(path, require_authorization=False)["run_id"] == RUN
    else:
        with pytest.raises(launcher.LaunchContractError):
            launcher.load_and_validate_launch_contract(path, require_authorization=False)
    assert not list(Path(contract["output_root"]).iterdir())


def _rag_contract(launcher, tmp_path, monkeypatch=None):
    """Real v1 shape, byte-bound dummy index, and an exact shared model tree."""
    contract, contract_path = _base_contract(tmp_path)
    rag = Path(contract["rag_root"])
    original_path = Path("E:/RC1_FULL8_RAG_20261020_R01/data/processed/indexes/") / (
        "idx_type_aware_parent_child_v2_m3e_base_local_m3e_base_profiled_v2_fd73dfd6ca2d/index_manifest.json")
    original = json.loads(original_path.read_text(encoding="utf-8"))
    model = tmp_path / "SHARED_MODEL"
    model.mkdir()
    (model / "config.json").write_text('{"model_type":"bert"}', encoding="utf-8")
    (model / "weights.bin").write_bytes(b"offline-dummy-model-never-loaded")
    original["embedding"]["model"] = str(model)
    frozen = tmp_path / "FROZEN_INDEX"
    frozen.mkdir()
    projected = copy.deepcopy(original)
    for name, artifact in original["artifacts"].items():
        old = frozen / (name + (".db" if artifact["kind"] == "directory" else ".jsonl"))
        new = rag / "data/processed/indexes" / original["index_version"] / old.name
        if artifact["kind"] == "directory":
            old.mkdir()
            (old / "LOCK").write_bytes(b"")
            _write(old / "collections/offline/schema.json", {"offline": True})
            shutil.copytree(old, new)
        else:
            old.write_bytes(("{\"offline_artifact\":\"" + name + "\"}\n").encode())
            new.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(old, new)
            artifact["sha256"] = _sha(old)
        artifact["path"] = str(old)
        projected["artifacts"][name] = {**artifact, "path": str(new)}
    source = _write(tmp_path / "ORIGINAL_RAG_MANIFEST.json", original)
    def constant(name, value):
        if monkeypatch is None: setattr(launcher, name, value)
        else: monkeypatch.setattr(launcher, name, value, raising=False)
    constant("ORIGINAL_RAG_MANIFEST", source)
    constant("ORIGINAL_RAG_MANIFEST_SHA256", _sha(source))
    source_files = _write(tmp_path / "RAG_SOURCE_MANIFEST.json", {
        p.relative_to(frozen).as_posix(): {"bytes": p.stat().st_size, "sha256": _sha(p)}
        for p in frozen.rglob("*") if p.is_file()})
    constant("ORIGINAL_RAG_ROOT", frozen)
    constant("RAG_SOURCE_MANIFEST", source_files)
    constant("RAG_SOURCE_MANIFEST_SHA256", _sha(source_files))
    resources = _write(tmp_path / "RAG_RESOURCE_MANIFEST.json", {"files": [
        {"path": str(p), "sha256": _sha(p), "source": "local_embedding_or_reranker_model_resource"}
        for p in model.rglob("*") if p.is_file()]})
    constant("RAG_RESOURCE_MANIFEST", resources)
    constant("RAG_RESOURCE_MANIFEST_SHA256", _sha(resources))
    manifest = _write(rag / "data/processed/indexes" / original["index_version"] / "index_manifest.json", projected)
    pointer = _write(rag / "data/processed/indexes/active_index.json", {
        "schema_version": "active_rag_index_pointer_v1", "index_version": original["index_version"],
        "manifest_path": str(manifest), "manifest_sha256": _sha(manifest)})
    contract.update(rag_manifest_path=str(manifest), rag_pointer_path=str(pointer))
    for path in [source, manifest, pointer, *model.rglob("*"), *rag.rglob("*")]:
        if path.is_file(): contract["input_sha256"][str(path)] = _sha(path)
    _g07_shared_fixture(launcher, tmp_path, contract, model, constant)
    _g07_environment_fixture(launcher, tmp_path, contract, constant)
    _write(contract_path, contract)
    return contract, contract_path, source, model


def _g07_shared_fixture(launcher, tmp_path, contract, model, constant):
    """Independent dummy authorities, never signatures for real shared bytes."""
    for i in range(24):
        (model / ("member_%02d.bin" % i)).write_bytes(("embedding-%02d" % i).encode())
    reranker = tmp_path / "SHARED_RERANKER"
    reranker.mkdir()
    for i in range(28):
        (reranker / ("member_%02d.bin" % i)).write_bytes(("reranker-%02d" % i).encode())
    checkout = tmp_path / "SHARED_CHECKOUT"
    rows = []
    for role, root in (("embedding", model), ("reranker", reranker)):
        rows.extend({"role": role, "path": str(p), "bytes": p.stat().st_size,
            "sha256": _sha(p)} for p in sorted(root.rglob("*")) if p.is_file())
    for role, relative in (
        ("runtime_catalog", "data/catalog/document_profiles_v1.jsonl"),
        ("default_static_retrieval", "backend/rag/config/static_retrieval_v1.yaml"),
        ("default_intent_policy", "backend/rag/config/intent_policy_v1.yaml"),
        ("default_retrieval_gate_policy", "backend/rag/config/retrieval_gate_policy_v1.yaml"),
        ("default_generation_policy_overridden_by_worker", "backend/apps/enterprise_document/config/grounded_generation_v1.yaml")):
        p = _write(checkout / relative, {"dummy_role": role})
        rows.append({"role": role, "path": str(p), "bytes": p.stat().st_size, "sha256": _sha(p)})
    assert len(rows) == 59
    source = _write(tmp_path / "G07_SHARED_SOURCE.json", {"files": rows,
        "roots": {"embedding": str(model), "reranker": str(reranker), "checkout": str(checkout)},
        "acl_objects": [{"path": str(p)} for p in (model, reranker, checkout)]})
    constant("G07_SHARED_SOURCE", source)
    constant("G07_SHARED_SOURCE_SHA256", _sha(source))
    resources = _write(tmp_path / "RAG_RESOURCE_MANIFEST.json", {"files": [
        {**row, "source": "local_embedding_or_reranker_model_resource"
            if row["role"] in {"embedding", "reranker"} else "static_shared_dependency"}
        for row in rows]})
    constant("RAG_RESOURCE_MANIFEST", resources)
    constant("RAG_RESOURCE_MANIFEST_SHA256", _sha(resources))
    for row in rows:
        contract["input_sha256"][row["path"]] = row["sha256"]
    contract["input_sha256"].update({str(source): _sha(source), str(resources): _sha(resources)})


def _g07_environment_fixture(launcher, tmp_path, contract, constant):
    """Prepare a nonsecret expectation using real config readers and Fake dotenv.

    This models a future absent-dotenv process; it does not approve the current
    host's existing shared .env, loader PATH, or runtime resource environment.
    """
    if not hasattr(launcher, "ENVIRONMENT_BASELINE_RELATIVE"):
        return  # Preserved before-source is used only for independent red cases.
    constant("DOTENV_PATH", tmp_path / "ABSENT_DOTENV")
    environment = launcher.build_child_environment(contract)
    for name in (*launcher.CACHE_VARIABLES, "TMPDIR", "TEMP", "TMP"):
        Path(environment[name]).mkdir(parents=True, exist_ok=True)
    source = {str(p): _sha(p) for p in launcher.ENVIRONMENT_SOURCE_FILES}
    plan = json.loads(Path(contract["plan_path"]).read_text(encoding="utf-8"))
    inputs = {str(p): _sha(p) for p in (*launcher.ENVIRONMENT_INPUT_FILES, Path(plan["profile"]))}
    contract["source_sha256"].update(source)
    contract["input_sha256"].update(inputs)
    from core import config
    from bootstrap.runtime_options import RuntimeOptions
    from dataclasses import asdict
    previous_settings, previous_dotenv = config._SETTINGS, config._DOTENV_VALUES
    try:
        config._SETTINGS, config._DOTENV_VALUES = None, {}
        with patch.dict(os.environ, environment, clear=True):
            # This parent fixture may already hold legacy config. Only the
            # fresh child proves the production process policy/import boundary.
            os.environ[config.CONFIG_SOURCE_POLICY_ENV] = config.CONFIG_SOURCE_POLICY
            settings = config.get_settings(reload=True)
            options = RuntimeOptions.resolve(settings, ROOT, enable_agent_self_rag=True, durable_memory_required=False)
            baseline = {"environment": {k: v for k, v in environment.items() if k != "DEEPSEEK_API_KEY"},
                "credential_presence": {"DEEPSEEK_API_KEY": bool(environment.get("DEEPSEEK_API_KEY"))},
                "source_sha256": source, "input_sha256": inputs, "app_settings": settings.as_dict(),
                "runtime_options": json.loads(json.dumps(asdict(options), default=str))}
    finally:
        config._SETTINGS, config._DOTENV_VALUES = previous_settings, previous_dotenv
    baseline_path = _write(Path(contract["prep_root"]) / launcher.ENVIRONMENT_BASELINE_RELATIVE, baseline)
    contract["input_sha256"][str(baseline_path)] = _sha(baseline_path)


def test_rag_manifest_accepts_exact_shared_model_and_build_provenance(launcher, tmp_path, monkeypatch):
    contract, _, source, model = _rag_contract(launcher, tmp_path, monkeypatch)
    before = {str(p): _sha(p) for p in [source, *model.rglob("*")] if p.is_file()}
    result = launcher.validate_rag_manifest(contract)
    assert result["manifest_sha256"] == _sha(contract["rag_manifest_path"])
    assert before == {p: _sha(p) for p in before}


def test_rag_capture_dangling_symlink_refused(launcher, tmp_path, monkeypatch):
    contract, _, _, _ = _rag_contract(launcher, tmp_path, monkeypatch)
    capture = Path(contract["rag_root"]) / "data/processed/runs/retrieval_runs.jsonl"
    capture.parent.mkdir(parents=True)
    outside = tmp_path / "outside_rag_capture.jsonl"
    capture.symlink_to(outside)
    assert capture.is_symlink() and not capture.exists() and not outside.exists()
    try:
        launcher.validate_rag_manifest(contract)
    except launcher.LaunchContractError:
        assert not outside.exists()
    else:
        # The attack target is another file inside this test's owned sandbox.
        # Exercise the actual downstream writer, never a historical location.
        from rag.data_capture.jsonl_writer import JsonlWriter
        JsonlWriter(capture).write({"proof": "dangling_capture_followed", "run_id": RUN})
        observed = json.loads(outside.read_text(encoding="utf-8"))
        _write(tmp_path / "DANGLING_CAPTURE_REPRO.json", {
            "validator_accepted": True, "outside_rag_created": outside.is_file(),
            "outside_sha256": _sha(outside), "native_writer_record": observed,
            "all_paths_test_owned": True})
        pytest.fail("RAG validator admitted dangling capture; native writer created outside-RAG sandbox file")


def _rebind_rag(contract, manifest, pointer=None):
    _write(contract["rag_manifest_path"], manifest)
    if pointer is None:
        pointer = json.loads(Path(contract["rag_pointer_path"]).read_text(encoding="utf-8"))
        pointer["manifest_sha256"] = _sha(contract["rag_manifest_path"])
    _write(contract["rag_pointer_path"], pointer)
    for key in ("rag_manifest_path", "rag_pointer_path"):
        contract["input_sha256"][contract[key]] = _sha(contract[key])


@pytest.mark.parametrize("case", ["old_artifact", "external_artifact", "relative_escape", "different_drive",
    "artifact_sha", "artifact_bytes_and_input_rebound", "directory_bytes_and_input_rebound", "directory_extra",
    "model_unbound", "model_bytes_and_input_rebound", "model_extra", "model_bound_extra", "model_wrong_directory",
    "source_sha", "resource_sha", "file_manifest_sha", "provenance_changed", "index_path_changed",
    "manifest_schema", "pointer_schema", "pointer_identity", "pointer_sha", "pointer_extra",
    "nested_map", "nested_list", "artifact_nested_path", "metadata_path", "model_hardlink", "model_ancestor_junction",
    "runtime_pointer_mismatch", "old_retrieval_log"])
def test_rag_manifest_rejects_unapproved_paths_or_rebound_identity(launcher, tmp_path, monkeypatch, case):
    contract, _, source, model = _rag_contract(launcher, tmp_path, monkeypatch)
    manifest = json.loads(Path(contract["rag_manifest_path"]).read_text(encoding="utf-8"))
    original = json.loads(source.read_text(encoding="utf-8"))
    parent = manifest["artifacts"]["parent_chunks"]
    pointer = None
    external = _write(tmp_path / "outside.json", {"untouched": True})
    before = _sha(external)
    if case == "old_artifact": parent["path"] = original["artifacts"]["parent_chunks"]["path"]
    elif case == "external_artifact": parent["path"] = str(external)
    elif case == "different_drive": parent["path"] = "C:/outside-forbidden.json"
    elif case == "relative_escape": parent["path"] = "../../../../../../outside.json"
    elif case == "artifact_sha": parent["sha256"] = "0" * 64
    elif case == "artifact_bytes_and_input_rebound":
        Path(parent["path"]).write_bytes(b"rebound bytes")
        parent["sha256"] = contract["input_sha256"][parent["path"]] = _sha(parent["path"])
    elif case in {"directory_bytes_and_input_rebound", "directory_extra"}:
        directory = Path(manifest["artifacts"]["milvus_lite"]["path"])
        target = directory / ("LOCK" if case == "directory_bytes_and_input_rebound" else "unapproved")
        target.write_bytes(b"rebound directory byte")
        contract["input_sha256"][str(target)] = _sha(target)
    elif case == "model_unbound": contract["input_sha256"].pop(str(model / "weights.bin"))
    elif case == "model_bytes_and_input_rebound":
        (model / "weights.bin").write_bytes(b"changed-model")
        contract["input_sha256"][str(model / "weights.bin")] = _sha(model / "weights.bin")
    elif case in {"model_extra", "model_bound_extra"}:
        target = model / "unapproved.bin"
        target.write_bytes(b"new model file")
        if case == "model_bound_extra": contract["input_sha256"][str(target)] = _sha(target)
    elif case == "model_wrong_directory": manifest["embedding"]["model"] = str(tmp_path)
    elif case == "source_sha": source.write_text("{}", encoding="utf-8")
    elif case == "resource_sha": launcher.RAG_RESOURCE_MANIFEST.write_text("{}", encoding="utf-8")
    elif case == "file_manifest_sha": launcher.RAG_SOURCE_MANIFEST.write_text("{}", encoding="utf-8")
    elif case == "provenance_changed": manifest["source"]["path"] = str(external)
    elif case == "index_path_changed": manifest["index"]["bm25_records_path"] = str(external)
    elif case == "manifest_schema": manifest["schema_version"] = "unknown_v2"
    elif case.startswith("pointer_"):
        pointer = json.loads(Path(contract["rag_pointer_path"]).read_text(encoding="utf-8"))
        if case == "pointer_schema": pointer["schema_version"] = "unknown_v2"
        elif case == "pointer_identity": pointer["index_version"] += "-forged"
        elif case == "pointer_sha": pointer["manifest_sha256"] = "0" * 64
        else: pointer["metadata"] = [{"file_path": str(external)}]
    elif case == "nested_map": manifest["chunker"]["params"]["nested"] = {"model": str(external)}
    elif case == "nested_list": manifest["chunker"]["params"]["nested"] = [{"path": str(external)}]
    elif case == "artifact_nested_path": parent["metadata"] = {"nested": [{"path": str(external)}]}
    elif case == "metadata_path": manifest["metadata"] = {"allowed": [{"new_path": str(external)}]}
    elif case == "model_hardlink": os.link(model / "weights.bin", tmp_path / "weight-link.bin")
    elif case == "model_ancestor_junction":
        relocated = tmp_path / "relocated-dummy-model"
        model.rename(relocated)
        result = subprocess.run(["cmd.exe", "/d", "/c", "mklink", "/J", str(model), str(relocated)], capture_output=True, text=True)
        assert result.returncode == 0, result.stderr
    elif case == "runtime_pointer_mismatch":
        alternate = Path(contract["rag_root"]) / "unused-active.json"
        shutil.copyfile(contract["rag_pointer_path"], alternate)
        contract["rag_pointer_path"] = str(alternate)
        contract["input_sha256"][str(alternate)] = _sha(alternate)
    elif case == "old_retrieval_log":
        _write(Path(contract["rag_root"]) / "data/processed/runs/retrieval_runs.jsonl", {"old_run": "must-not-reuse"})
    _rebind_rag(contract, manifest, pointer)
    with pytest.raises(launcher.LaunchContractError): launcher.validate_rag_manifest(contract)
    assert _sha(external) == before


@pytest.mark.parametrize("location", ["source", "chunker_map", "chunker_list", "artifact", "embedding"])
def test_rag_manifest_rejects_frozen_but_unclassified_nested_paths(launcher, tmp_path, monkeypatch, location):
    contract, _, source, _ = _rag_contract(launcher, tmp_path, monkeypatch)
    original = json.loads(source.read_text(encoding="utf-8"))
    projected = json.loads(Path(contract["rag_manifest_path"]).read_text(encoding="utf-8"))
    for value in (original, projected):
        extra = {"unclassified_file": str(tmp_path / "unclassified.json")}
        if location == "source": value["source"]["extra"] = extra
        elif location == "chunker_map": value["chunker"]["params"]["extra"] = extra
        elif location == "chunker_list": value["chunker"]["params"]["extra"] = [extra]
        elif location == "artifact": value["artifacts"]["parent_chunks"]["extra"] = [extra]
        else: value["embedding"]["extra"] = extra
    _write(source, original)
    monkeypatch.setattr(launcher, "ORIGINAL_RAG_MANIFEST_SHA256", _sha(source))
    _rebind_rag(contract, projected)
    with pytest.raises(launcher.LaunchContractError, match="Unclassified nested"):
        launcher.validate_rag_manifest(contract)


def test_rag_manifest_propagates_shared_file_permission_failure(launcher, tmp_path, monkeypatch):
    contract, _, _, model = _rag_contract(launcher, tmp_path, monkeypatch)
    original = launcher.sha256_file
    def denied(path):
        if Path(path) == model / "weights.bin": raise PermissionError("offline mocked model read denied")
        return original(path)
    monkeypatch.setattr(launcher, "sha256_file", denied)
    with pytest.raises(PermissionError, match="mocked model read denied"):
        launcher.validate_rag_manifest(contract)


def test_rag_manifest_matches_actual_resolver_and_factory_read_paths(launcher, tmp_path, monkeypatch):
    from rag.offline.resolver import ActiveIndexResolver
    from rag.runtime.parent_child_runtime_factory import ParentChildRuntimeFactory
    from rag.runtime.retrieval_runtime import RetrievalRuntimeConfig
    from rag.retriever.bm25_child_retriever import load_jsonl_dicts
    contract, _, _, model = _rag_contract(launcher, tmp_path, monkeypatch)
    launcher.validate_rag_manifest(contract)
    paths = ActiveIndexResolver(verify_artifacts=True).resolve(contract["rag_pointer_path"])
    cfg = ParentChildRuntimeFactory().resolve_config(RetrievalRuntimeConfig(
        capture_output="data/processed/runs/retrieval_runs.jsonl"), Path(contract["rag_root"]))
    assert cfg.parent_file == paths["parent_file"] and cfg.child_file == paths["child_file"]
    assert cfg.db_file == paths["db_file"] and Path(cfg.embedding_model) == model
    assert Path(cfg.capture_output) == Path(contract["rag_root"]) / "data/processed/runs/retrieval_runs.jsonl"
    assert load_jsonl_dicts(cfg.child_file) == [{"offline_artifact": "child_chunks"}]
    assert all(Path(paths[key]).is_relative_to(Path(contract["rag_root"])) for key in ("parent_file", "child_file", "db_file"))


def test_rag_local_sdk_helpers_read_dummy_model_without_download_or_write(launcher, tmp_path, monkeypatch):
    contract, _ = _contract(tmp_path)
    body = '''
# Follow the pinned Worker's required H7-before-native import order. This
# invokes only the source-verified native helper, never Worker.main/credentials.
worker = launcher.load_pinned_worker(contract)
assert launcher.sha256_file(worker.NATIVE_HELPER) == worker.NATIVE_HELPER_SHA256
assert launcher.sha256_file(worker.NATIVE_MANIFEST) == worker.NATIVE_MANIFEST_SHA256
spec = importlib.util.spec_from_file_location("offline_rag_h7", worker.NATIVE_HELPER)
native = importlib.util.module_from_spec(spec)
spec.loader.exec_module(native)
native_identity = native.activate_native_overlay(worker.NATIVE_MANIFEST)
from sentence_transformers import util
from transformers.utils import hub
import io
from unittest.mock import patch
model = SANDBOX / "READ_ONLY_DUMMY_MODEL"
pooling = model / "1_Pooling"
pooling.mkdir(parents=True)
(model / "config.json").write_text('{"model_type":"bert"}', encoding="utf-8")
(pooling / "config.json").write_text('{"pooling_mode_mean_tokens":true}', encoding="utf-8")
before = {str(p): launcher.sha256_file(p) for p in model.rglob("*") if p.is_file()}
downloads, writes = [], []
def deny_download(*args, **kwargs):
    downloads.append(True)
    raise AssertionError("DUMMY_LOCAL_MODEL_MUST_NOT_DOWNLOAD")
def observe_open(original):
    def guarded(file, mode="r", *args, **kwargs):
        if not isinstance(file, int) and pathlib.Path(file).absolute().is_relative_to(model):
            if any(c in mode for c in "wax+"):
                writes.append(str(file))
                raise AssertionError("MODEL_WRITE_FORBIDDEN")
        return original(file, mode, *args, **kwargs)
    return guarded
with patch.object(util, "hf_hub_download", deny_download), patch.object(util, "snapshot_download", deny_download), patch.object(hub, "hf_hub_download", deny_download), patch.object(builtins, "open", observe_open(builtins.open)), patch.object(io, "open", observe_open(io.open)):
    assert pathlib.Path(util.load_file_path(str(model), "config.json", None, None)) == model / "config.json"
    assert pathlib.Path(util.load_dir_path(str(model), "1_Pooling", None, None)) == pooling
    assert pathlib.Path(hub.cached_file(str(model), "config.json")) == model / "config.json"
assert downloads == writes == []
assert before == {str(p): launcher.sha256_file(p) for p in model.rglob("*") if p.is_file()}
(SANDBOX / "SDK_HELPER_RECEIPT.json").write_text(json.dumps({"h7": native_identity, "downloads": downloads, "model_writes": writes, "model_file_sha256": before, "model_initializers_called": 0}), encoding="utf-8")
'''
    body = "contract = json.loads(" + repr(json.dumps(contract)) + ")\n" + body
    _fresh_python(launcher, tmp_path, body, contract=contract)
    receipt = json.loads((tmp_path / "SDK_HELPER_RECEIPT.json").read_text(encoding="utf-8"))
    assert receipt["downloads"] == receipt["model_writes"] == []
    assert receipt["model_initializers_called"] == 0


def _limiter(launcher, tmp_path, limit=3, **kwargs):
    return launcher.SingleRunAttemptLimiter(TASK, RUN, limit,
        audit_path=tmp_path / "permits.jsonl", request_contract={"base_url": "https://api.deepseek.com",
        "model": "deepseek-v4-flash", "path": "/chat/completions", "max_tokens": 65536}, **kwargs)


def _payload(**changes):
    payload = {"model": "deepseek-v4-flash", "messages": [{"role": "user", "content": "offline hello"}],
        "max_tokens": 100, "stream": False, "thinking": {"type": "disabled"}}
    payload.update(changes)
    return payload


def _response(request, *, usage="complete", status=200):
    if status != 200:
        return httpx.Response(status, json={"error": {"message": "offline provider error", "type": "server_error"}}, request=request)
    body = {"id": "offline-completion", "object": "chat.completion", "created": 1,
        "model": "deepseek-flash", "choices": [{"index": 0, "message": {"role": "assistant", "content": "offline answer"}, "finish_reason": "stop"}]}
    if usage != "missing":
        tokens = 100000 if usage == "overrun" else 12
        body["usage"] = {"prompt_tokens": tokens - 4, "completion_tokens": 4, "total_tokens": tokens}
    return httpx.Response(200, json=body, request=request)


class SendOracle:
    def __init__(self, mode="complete"):
        self.mode = mode
        self.requests = []
        self.lock = threading.Lock()
    def __call__(self, request):
        with self.lock:
            self.requests.append(request)
            ordinal = len(self.requests)
        mode = self.mode if ordinal == 1 else "complete"
        if mode == "connect":
            raise httpx.ConnectError("offline connect failed", request=request)
        if mode == "timeout":
            raise httpx.ReadTimeout("offline timeout", request=request)
        return _response(request, usage=mode, status=500 if mode == "http500" else 200)
    @property
    def sends(self):
        return len(self.requests)


def _request(call_id="same-logical", **updates):
    from model_gateway.model_contract import ModelRole
    from schemas.model import ModelRequestSchema
    values = dict(model_call_id=call_id, task_id=TASK, run_id=RUN,
        model_role=ModelRole.SECTION_GENERATION.value, caller_agent="OfflineContractWriter", prompt="hello",
        max_tokens=100, created_at=NOW.isoformat(), extra={"section_id": "offline-section", "attempt_id": "offline-attempt"})
    values.update(updates)
    return ModelRequestSchema(**values)


@contextmanager
def _real_gateway(launcher, tmp_path, oracle, *, limit=3, controlled=True, token_limit=1000000):
    from apps.enterprise_document.schemas.agent_rag_v1 import CanonicalBudgetVectorSchema
    from apps.enterprise_document.services.agent_rag_v1 import BudgetManager, CanonicalInvocationBudgetAdapter
    from model_gateway.model_contract import ModelProfile, ModelRole, ResidencyPolicy, RoutingPolicy
    from model_gateway.model_gateway import ModelGateway
    from model_gateway.model_router import ModelRouter
    from model_gateway.provider_factory import ModelProviderFactory
    import model_gateway.integrations.langchain.deepseek_chat_model as deepseek_module
    tmp_path.mkdir(parents=True, exist_ok=True)
    limiter = _limiter(launcher, tmp_path, limit)
    manager = BudgetManager.canonical(task_id=TASK, run_id=RUN,
        limits=CanonicalBudgetVectorSchema(logical_model_calls=20, logical_tool_calls=160,
            model_tokens_total=token_limit, research_rounds=1, repair_attempts=1,
            generation_attempts=2, replan_attempts=3, true_retry_attempts=1),
        max_elapsed_seconds=120, storage_path=tmp_path / "budget.jsonl",
        policy_ref="budget-policy://offline-full8", policy_version="v1", clock=lambda: NOW)
    profile = ModelProfile(profile_id="deepseek", model_name="deepseek_api", provider="openai_compatible",
        provider_model_name="deepseek-v4-flash", residency_policy=ResidencyPolicy.REMOTE,
        context_window=1000000, max_output_tokens=65536, timeout_seconds=300)
    policies = [RoutingPolicy(role=role, candidates=["deepseek"], availability_fallback=False) for role in ModelRole]
    gateway = ModelGateway(default_model_name="deepseek_api", router=ModelRouter("deepseek_api", profiles=[profile], policies=policies),
        budget_runtime=CanonicalInvocationBudgetAdapter(manager))
    clients = launcher.OwnedHttpClients(limiter, transport=httpx.MockTransport(oracle), async_transport=httpx.MockTransport(oracle))
    original = deepseek_module.DeepSeekChatOpenAI
    baseline_clients = []
    def plain_constructor(**kwargs):
        sync = httpx.Client(transport=httpx.MockTransport(oracle), follow_redirects=False)
        async_client = httpx.AsyncClient(transport=httpx.MockTransport(oracle), follow_redirects=False)
        model = original(**kwargs, http_client=sync, http_async_client=async_client, cache=False)
        baseline_clients.append((model, sync, async_client))
        return model
    try:
        if controlled:
            with clients.install():
                client = ModelProviderFactory().build(profile, settings=SimpleNamespace(deepseek_api_key=FAKE_KEY,
                    deepseek_base_url="https://api.deepseek.com", deepseek_thinking_enabled=False))
        else:
            deepseek_module.DeepSeekChatOpenAI = plain_constructor
            client = ModelProviderFactory().build(profile, settings=SimpleNamespace(deepseek_api_key=FAKE_KEY,
                deepseek_base_url="https://api.deepseek.com", deepseek_thinking_enabled=False))
            deepseek_module.DeepSeekChatOpenAI = original
        gateway.register_client(client)
        yield SimpleNamespace(gateway=gateway, manager=manager, limiter=limiter, clients=clients,
            model=client.chat_model, profile=profile, policies=policies)
    finally:
        deepseek_module.DeepSeekChatOpenAI = original
        gateway.close()
        if controlled:
            clients.close(idle=True)
        for model, sync, async_client in baseline_clients:
            model.root_client.close()
            asyncio.run(model.root_async_client.close())
            sync.close()
            asyncio.run(async_client.aclose())


def _budget_signature(manager):
    state = manager.canonical_state()
    return {"used": state.used.model_dump(), "remaining": state.remaining.model_dump(),
        "active_reserved": state.active_reserved.model_dump(), "draining": state.draining,
        "exhausted_dimensions": list(state.exhausted_dimensions),
        "reservations": [(r.reservation_ref.reservation_kind, r.status, r.requested.model_dump(),
            r.consumed.model_dump() if hasattr(r, "consumed") else None) for r in state.reservations]}


def _model_kwargs():
    return dict(model="deepseek-v4-flash", api_key=FAKE_KEY,
        base_url="https://api.deepseek.com", timeout=300.0, max_retries=0,
        extra_body={"thinking": {"type": "disabled"}})


def _network_signature(client):
    def context_signature(context):
        if context is None: return None
        certificates = sorted(hashlib.sha256(cert).hexdigest() for cert in context.get_ca_certs(binary_form=True))
        return (context.check_hostname, context.verify_mode, context.minimum_version,
            hashlib.sha256(json.dumps(certificates).encode()).hexdigest())
    items = [("default", client._transport)] + [(pattern.pattern, transport)
        for pattern, transport in client._mounts.items() if transport is not None]
    result = []
    for name, transport in items:
        pool = transport._pool
        headers = getattr(pool, "_proxy_headers", None)
        header_digest = hashlib.sha256(repr(headers).encode()).hexdigest() if headers is not None else None
        result.append((name, type(pool).__name__, getattr(pool, "_proxy_url", None),
            header_digest, context_signature(getattr(pool, "_ssl_context", None)),
            context_signature(getattr(pool, "_proxy_ssl_context", None)),
            getattr(pool, "_socket_options", None), pool._retries, pool._max_connections,
            pool._max_keepalive_connections, pool._keepalive_expiry))
    return {"timeout": client.timeout.as_dict(), "trust_env": client._trust_env, "pools": result}


def _fresh_python(launcher, tmp_path, body, *, contract=None, timeout=90, expected_exit=0, exact_environment=None, hide_shared_dotenv=True):
    """Fresh stdlib-first harness; socket and write guards precede imports."""
    sandbox = tmp_path.resolve()
    script = tmp_path / "fresh_contract_probe.py"
    preamble = '''
import builtins, importlib.util, json, os, pathlib, socket, sys, time, types
SANDBOX = pathlib.Path(__file__).resolve().parent
def socketpair_control(sock, address):
    code = getattr(getattr(socket, "_fallback_socketpair", None), "__code__", None)
    frame = sys._getframe(1)
    while frame is not None:
        if frame.f_code is code:
            values = frame.f_locals
            listener = values.get("lsock")
            return (values.get("csock") is sock and listener is not None and isinstance(address, tuple)
                and len(address) >= 2 and address[0] in {"127.0.0.1", "::1"}
                and tuple(address[:2]) == tuple(listener.getsockname()[:2]))
        frame = frame.f_back
    return False
def guard(event, args):
    if event in ("socket.connect", "socket.getaddrinfo", "socket.sendto"):
        if event == "socket.connect" and socketpair_control(args[0], args[1]):
            with (SANDBOX / "CONTROL_IPC.jsonl").open("a", encoding="utf-8") as stream:
                stream.write(json.dumps({"event": "stdlib_socketpair_control_ipc"}) + "\\n")
            return
        with (SANDBOX / "BLOCKED_NETWORK.jsonl").open("a", encoding="utf-8") as stream:
            stream.write(json.dumps({"event": event}) + "\\n")
        raise RuntimeError("OFFLINE_CONTRACT_NETWORK_ATTEMPT")
    if event == "open" and isinstance(args[0], (str, bytes, os.PathLike)):
        path = pathlib.Path(os.fsdecode(args[0])).absolute()
        mode, flags = args[1], args[2]
        writing = (isinstance(mode, str) and any(c in mode for c in "wax+")) or (isinstance(flags, int) and bool(flags & (os.O_WRONLY | os.O_RDWR | os.O_CREAT | os.O_TRUNC | os.O_APPEND)))
        if path.name == ".env" and not writing:
            raise RuntimeError("OFFLINE_DOTENV_READ_REFUSED")
        if writing and not path.resolve().is_relative_to(SANDBOX):
            raise RuntimeError("OFFLINE_WRITE_ESCAPE_REFUSED")
sys.addaudithook(guard)
sys.dont_write_bytecode = True
sys.path[:0] = [str(ROOT / "backend"), str(ROOT / "scripts")]
spec = importlib.util.spec_from_file_location("fresh_full8_launcher", ROOT / "scripts/run_post_phase1_full8.py")
launcher = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = launcher
spec.loader.exec_module(launcher)
'''
    preamble = preamble.replace('SANDBOX = pathlib.Path', 'ROOT = pathlib.Path(' + repr(str(ROOT)) + ')\nSANDBOX = pathlib.Path', 1)
    # Project only independent test authorities into the fresh module. The real
    # source files and source SHA verification are retained. No real shared
    # dependency or dotenv is rebound or modified on disk by this fixture.
    constants = ("ORIGINAL_RAG_MANIFEST", "ORIGINAL_RAG_MANIFEST_SHA256", "ORIGINAL_RAG_ROOT",
        "RAG_SOURCE_MANIFEST", "RAG_SOURCE_MANIFEST_SHA256", "RAG_RESOURCE_MANIFEST",
        "RAG_RESOURCE_MANIFEST_SHA256", "G07_SHARED_SOURCE", "G07_SHARED_SOURCE_SHA256", "DOTENV_PATH")
    for name in constants:
        if hasattr(launcher, name):
            value = getattr(launcher, name)
            expression = "pathlib.Path(" + repr(str(value)) + ")" if isinstance(value, Path) else repr(value)
            preamble += "\nlauncher." + name + " = " + expression + "\n"
    if hide_shared_dotenv:
        preamble += '''
_original_exists = pathlib.Path.exists
def _test_dotenv_absence(path):
    if path.absolute() == ROOT / ".env":
        return False
    return _original_exists(path)
pathlib.Path.exists = _test_dotenv_absence
'''
    preamble += '''
(SANDBOX / "FRESH_PROCESS_IDENTITY.json").write_text(json.dumps({
    "process": launcher._process_identity(os.getpid()), "parent_pid": os.getppid(),
    "guards_precede_production_imports": True}), encoding="utf-8")
'''
    script.write_text(textwrap.dedent(preamble) + "\n" + textwrap.dedent(body), encoding="utf-8")
    environment = dict(exact_environment) if exact_environment is not None else (
        launcher.build_child_environment(contract) if contract else dict(os.environ))
    environment.update(DEEPSEEK_API_KEY=FAKE_KEY, PYTHONDONTWRITEBYTECODE="1")
    if exact_environment is None and contract is None:
        environment.update(TMPDIR=str(tmp_path), TEMP=str(tmp_path), TMP=str(tmp_path))
    result = subprocess.run([sys.executable, "-I", "-B", "-X", "utf8", str(script)], cwd=tmp_path,
        env=environment, capture_output=True, text=True, encoding="utf-8", timeout=timeout)
    (tmp_path / "fresh_stdout.log").write_text(result.stdout, encoding="utf-8")
    (tmp_path / "fresh_stderr.log").write_text(result.stderr, encoding="utf-8")
    identity = json.loads((tmp_path / "FRESH_PROCESS_IDENTITY.json").read_text(encoding="utf-8"))
    _write(tmp_path / "FRESH_PROCESS_EXIT.json", {"identity": identity, "exit_code": result.returncode,
        "subprocess_wait_completed": True, "stdout_sha256": _sha(tmp_path / "fresh_stdout.log"),
        "stderr_sha256": _sha(tmp_path / "fresh_stderr.log"), "script_sha256": _sha(script),
        "environment_values_persisted": False})
    assert result.returncode == expected_exit, result.stdout + result.stderr
    return result


def _binding(launcher):
    start = time.monotonic()
    return {"worker": launcher._process_identity(os.getpid()),
        "supervisor": launcher._process_identity(os.getppid()), "nonce": "offline-bound-nonce",
        "clock_start": start - 1, "dispatch_deadline": start + 15, "deadline": start + 20}


def _wait_until(predicate, timeout=8):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return
        time.sleep(0.025)
    assert predicate(), "Owned dummy process did not reach its expected state"


def _dummy_program(tmp_path):
    script = tmp_path / "owned_dummy.py"
    script.write_text(textwrap.dedent('''
        import json, os, pathlib, socket, subprocess, sys, time
        base = pathlib.Path(__file__).resolve().parent
        def guard(event, args):
            if event in ("socket.connect", "socket.getaddrinfo", "socket.sendto"):
                raise RuntimeError("OFFLINE_DUMMY_NETWORK_REFUSED")
            if event == "open" and isinstance(args[0], (str, bytes, os.PathLike)):
                path = pathlib.Path(os.fsdecode(args[0])).absolute()
                mode, flags = args[1], args[2]
                writing = (isinstance(mode, str) and any(c in mode for c in "wax+")) or (isinstance(flags, int) and bool(flags & (os.O_WRONLY | os.O_RDWR | os.O_CREAT | os.O_TRUNC | os.O_APPEND)))
                if writing and not path.resolve().is_relative_to(base):
                    raise RuntimeError("OFFLINE_DUMMY_WRITE_ESCAPE")
        sys.addaudithook(guard)
        role = sys.argv[1]
        (base / (role + ".json")).write_text(json.dumps({"pid": os.getpid(), "ppid": os.getppid()}))
        print("owned dummy " + role, flush=True)
        print("owned dummy stderr " + role, file=sys.stderr, flush=True)
        if role in ("tree", "abnormal"):
            subprocess.Popen([sys.executable, "-I", "-B", __file__, "child"], creationflags=subprocess.CREATE_NO_WINDOW)
        elif role == "child":
            subprocess.Popen([sys.executable, "-I", "-B", __file__, "grandchild"], creationflags=subprocess.CREATE_NO_WINDOW)
        if role == "abnormal":
            limit = time.monotonic() + 5
            while not (base / "grandchild.json").exists() and time.monotonic() < limit:
                time.sleep(.01)
            os._exit(23)
        if role == "exit7":
            time.sleep(.15)
            (base / "partial.jsonl").write_bytes(b'{"last_progress":"dummy"}\\n{"partial":')
            raise SystemExit(7)
        time.sleep(60)
    '''), encoding="utf-8")
    return script


def _pid_gone(identity):
    import psutil
    try:
        process = psutil.Process(identity["pid"])
        return not process.is_running() or ("_created" in identity and process.create_time() != identity["_created"])
    except psutil.NoSuchProcess:
        return True


@pytest.mark.parametrize("source_kind", ["source", "worker", "helper", "plan", "valid_contract"])
def test_source_drift_refuses_before_application_import(launcher, tmp_path, source_kind, monkeypatch):
    contract, path, _, _ = _rag_contract(launcher, tmp_path, monkeypatch)
    imports_before = set(sys.modules)
    if source_kind == "valid_contract":
        result = launcher.load_and_validate_launch_contract(path, require_authorization=False)
        assert result["physical_attempt_limit"] == 1200
        assert not (Path(contract["output_root"]) / "runs").exists()
        return
    if source_kind in {"worker", "helper"}:
        target = launcher.WORKER if source_kind == "worker" else Path("E:/rag-agent-tmp/post-step7-real-provider-hardening-20260913-session/observer/native_overlay_startup.py")
        contract["source_sha256"][str(target)] = _sha(target)
        original_sha = launcher.sha256_file
        monkeypatch.setattr(launcher, "sha256_file", lambda candidate: "0" * 64 if Path(candidate).resolve() == target.resolve() else original_sha(candidate))
    elif source_kind == "plan":
        Path(contract["plan_path"]).write_text("{}", encoding="utf-8")
    else:
        sentinel = tmp_path / "source.py"
        sentinel.write_text("raise AssertionError('MUST_NOT_IMPORT')\n", encoding="utf-8")
        contract["source_sha256"][str(sentinel)] = _sha(sentinel)
        sentinel.write_text("raise AssertionError('DRIFTED')\n", encoding="utf-8")
    _write(path, contract)
    with pytest.raises(launcher.LaunchContractError, match="PLAN identity mismatch" if source_kind == "plan" else "Frozen source/input SHA drift"):
        launcher.load_and_validate_launch_contract(path, require_authorization=False)
    assert not any(name.startswith("application.") for name in set(sys.modules) - imports_before)
    assert not (Path(contract["output_root"]) / "runs").exists()


@pytest.mark.parametrize("case", ["valid", "old_task", "old_run", "placeholder", "wrong_root"])
def test_launch_identity_binding(launcher, tmp_path, case):
    contract = _layout(tmp_path)
    if case == "old_task": contract["task_id"] = "task-rc1-full8-real-e2e-20261020-r01"
    if case == "old_run": contract["run_id"] = "rc1-full8-real-e2e-20261020-r01"
    if case == "placeholder": contract["task_id"] = "<APPROVED_TASK>"
    if case == "wrong_root": contract["project_root"] = str(tmp_path)
    if case == "valid":
        launcher.validate_layout_and_identity(contract)
    else:
        with pytest.raises(launcher.LaunchContractError): launcher.validate_layout_and_identity(contract)
    assert list(Path(contract["output_root"]).iterdir()) == []


@pytest.mark.parametrize("nonempty", [False, True])
def test_empty_output_contract(launcher, tmp_path, nonempty):
    contract = _layout(tmp_path)
    _write(Path(contract["prep_root"]) / "inputs/frozen.json", {"offline": True})
    marker = Path(contract["output_root"]) / "do-not-overwrite.txt"
    if nonempty: marker.write_bytes(b"original")
    if nonempty:
        with pytest.raises(launcher.LaunchContractError): launcher.validate_layout_and_identity(contract)
        assert marker.read_bytes() == b"original"
    else:
        launcher.validate_layout_and_identity(contract)


@pytest.mark.parametrize("case", ["overlap", "symlink", "junction", "ancestor_reparse", "artifact_escape", "hardlink"])
def test_layout_reparse_and_rag_escape(launcher, tmp_path, case, monkeypatch):
    contract, path, _, _ = _rag_contract(launcher, tmp_path, monkeypatch)
    rag = Path(contract["rag_root"])
    outside = tmp_path / "outside"
    outside.mkdir()
    marker = outside / "preserve.txt"
    marker.write_bytes(b"original")
    if case == "overlap":
        nested = Path(contract["prep_root"]) / "nested"
        nested.mkdir()
        contract["rag_root"] = str(nested)
    elif case == "symlink":
        (rag / "link").symlink_to(outside, target_is_directory=True)
        contract["rag_root"] = str(rag / "link")
    elif case in ("junction", "ancestor_reparse"):
        junction = tmp_path / "junction"
        result = subprocess.run(["cmd.exe", "/d", "/c", "mklink", "/J", str(junction), str(rag)], capture_output=True, text=True)
        assert result.returncode == 0, result.stderr
        if case == "ancestor_reparse":
            (rag / "child").mkdir()
            contract["rag_root"] = str(junction / "child")
        else: contract["rag_root"] = str(junction)
    elif case == "artifact_escape":
        manifest = json.loads(Path(contract["rag_manifest_path"]).read_text(encoding="utf-8"))
        manifest["artifacts"]["parent_chunks"].update(path=str(marker), sha256=_sha(marker))
        _write(contract["rag_manifest_path"], manifest)
    elif case == "hardlink":
        os.link(marker, rag / "linked-entity.json")
        manifest = json.loads(Path(contract["rag_manifest_path"]).read_text(encoding="utf-8"))
        manifest["artifacts"]["parent_chunks"].update(path=str(rag / "linked-entity.json"), sha256=_sha(marker))
        _write(contract["rag_manifest_path"], manifest)
    _write(path, contract)
    if case in {"artifact_escape", "hardlink"}:
        pointer = json.loads(Path(contract["rag_pointer_path"]).read_text(encoding="utf-8"))
        pointer["manifest_sha256"] = _sha(contract["rag_manifest_path"])
        _write(contract["rag_pointer_path"], pointer)
        for key in ("rag_manifest_path", "rag_pointer_path"):
            contract["input_sha256"][str(Path(contract[key]))] = _sha(contract[key])
        with pytest.raises(launcher.LaunchContractError, match="escapes|Hard-linked"):
            launcher.validate_rag_manifest(contract)
    else:
        with pytest.raises(launcher.LaunchContractError, match="overlap|Reparse/symlink"):
            launcher.validate_layout_and_identity(contract)
    assert marker.read_bytes() == b"original"


@pytest.mark.parametrize("case", ["identity", "fact", "section_order", "permission", "budget", "original_tamper", "policy_authority"])
def test_input_projection_preserves_business_contract(launcher, tmp_path, case):
    if case in {"original_tamper", "policy_authority"}:
        contract, path = _contract(tmp_path)
        if case == "original_tamper":
            for key in ("original_project", "project"):
                location = Path(contract["projection"][key])
                value = json.loads(location.read_text(encoding="utf-8-sig"))
                value["business_goal"] = "both original and projected were tampered"
                _write(location, value)
                contract["input_sha256"][str(location)] = _sha(location)
        else:
            location = Path(contract["projection"]["policy"])
            value = json.loads(location.read_text(encoding="utf-8-sig"))
            value["canonical_budget"]["policy_ref"] = "unapproved-authority://different-policy"
            _write(location, value)
            contract["input_sha256"][str(location)] = _sha(location)
        _write(path, contract)
        with pytest.raises(launcher.LaunchContractError): launcher.load_and_validate_launch_contract(path, require_authorization=False)
        return
    project = json.loads((HISTORICAL / "PROJECT_INPUT_V3.json").read_text(encoding="utf-8-sig"))
    projected = copy.deepcopy(project)
    projected["task_id"] = TASK
    if case == "fact": projected["business_goal"] = "invented business fact"
    if case == "section_order": projected["generation_requirements"]["required_sections"].reverse()
    if case == "permission": projected["tenant_id"] = "unauthorized"
    if case == "budget": projected["generation_requirements"]["max_tokens_per_section"] += 1
    if case == "identity": launcher.validate_input_projection(project, projected, ["task_id"])
    else:
        with pytest.raises(launcher.LaunchContractError): launcher.validate_input_projection(project, projected, ["task_id"])


@pytest.mark.parametrize("case", ["route_and_second_client", "wrong_model", "wrong_endpoint", "remote_embedding", "sdk_async", "sdk_async_root"])
def test_provider_client_closure(launcher, tmp_path, case):
    from model_gateway.model_contract import ModelRole
    from model_gateway.provider_factory import ModelProviderFactory
    oracle = SendOracle()
    if case in {"wrong_model", "wrong_endpoint"}:
        clients = launcher.OwnedHttpClients(_limiter(launcher, tmp_path), transport=httpx.MockTransport(oracle))
        kwargs = _model_kwargs()
        kwargs["model" if case == "wrong_model" else "base_url"] = "unauthorized" if case == "wrong_model" else "https://unapproved.invalid"
        with clients.install(), pytest.raises(launcher.LaunchContractError): clients.construct_chat_model(**kwargs)
        assert oracle.sends == 0 and clients.limiter.snapshot()["stopped"]
        return
    with _real_gateway(launcher, tmp_path, oracle) as run:
        assert all(p.candidates == ["deepseek"] and not p.availability_fallback for p in run.policies)
        assert {p.role for p in run.policies} == set(ModelRole)
        assert run.model.cache is False
        assert run.model.root_client.max_retries == run.model.root_async_client.max_retries == 0
        if case == "remote_embedding":
            from core.runtime.execution_control import WorkflowExecutionCancelled
            with pytest.raises(WorkflowExecutionCancelled):
                run.model.root_client.embeddings.create(model="unapproved-embedding", input="offline")
        elif case == "sdk_async":
            from core.runtime.execution_control import WorkflowExecutionCancelled
            cancellations = []
            original_hook = run.clients.async_client.event_hooks["request"][0]
            async def observe_denial(request):
                try: return await original_hook(request)
                except WorkflowExecutionCancelled as exc:
                    cancellations.append(type(exc).__name__)
                    raise
            run.clients.async_client.event_hooks["request"] = [observe_denial]
            # Installed LangChain's gather(return_exceptions=True) misclassifies
            # BaseException WEC as a generation. Preserve that exact outer
            # defect while proving the real pre-transport stop still occurred.
            with pytest.raises(AttributeError, match="'WorkflowExecutionCancelled' object has no attribute 'generations'") as caught:
                asyncio.run(run.model.ainvoke("offline async denied"))
            assert cancellations == ["WorkflowExecutionCancelled"]
            assert run.limiter.snapshot()["stopped"] and run.limiter.snapshot()["used"] == 0
            _write(tmp_path / "SDK_ASYNC_DENIAL.json", {"outer_type": type(caught.value).__name__,
                "outer_message": str(caught.value), "pre_send_exceptions": cancellations,
                "handler_sends": oracle.sends, "limiter": run.limiter.snapshot()})
        elif case == "sdk_async_root":
            from core.runtime.execution_control import WorkflowExecutionCancelled
            parameters = _payload()
            thinking = parameters.pop("thinking")
            with pytest.raises(WorkflowExecutionCancelled):
                asyncio.run(run.model.root_async_client.chat.completions.create(
                    **parameters, extra_body={"thinking": thinking}))
            assert run.limiter.snapshot()["stopped"] and run.limiter.snapshot()["used"] == 0
        else:
            with run.clients.install(), pytest.raises(launcher.LaunchContractError):
                ModelProviderFactory().build(run.profile, settings=SimpleNamespace(deepseek_api_key=FAKE_KEY,
                    deepseek_base_url="https://api.deepseek.com", deepseek_thinking_enabled=False))
        assert oracle.sends == 0


@pytest.mark.parametrize("limit", [1, 3, 0, -1, True, 1.5, None])
def test_same_logical_id_consumes_each_http_permit(launcher, tmp_path, limit):
    from core.runtime.execution_control import WorkflowExecutionCancelled
    if type(limit) is not int or limit <= 0:
        with pytest.raises(launcher.LaunchContractError): _limiter(launcher, tmp_path, limit)
        return
    oracle = SendOracle()
    with _real_gateway(launcher, tmp_path, oracle, limit=limit) as run:
        for _ in range(limit): assert run.gateway.generate(_request()).success
        assert run.manager.canonical_state().used.logical_model_calls == 1
        assert run.gateway.usage_snapshot()["provider_attempts"] == limit
        assert oracle.sends == run.limiter.snapshot()["used"] == limit
        with pytest.raises(WorkflowExecutionCancelled): run.gateway.generate(_request())
        assert oracle.sends == limit
        assert run.limiter.snapshot()["used"] == limit
        with pytest.raises(ValueError): run.gateway.generate(_request(prompt="changed material fingerprint"))
        assert oracle.sends == limit


@pytest.mark.parametrize("case", ["last_permit", "inflight_stop", "stop_race", "deadline", "audit_failure"])
def test_last_permit_concurrency_and_inflight(launcher, tmp_path, case):
    from core.runtime.execution_control import WorkflowExecutionCancelled
    limiter = _limiter(launcher, tmp_path, 1)
    oracle = SendOracle()
    admitted = threading.Event()
    release = threading.Event()
    def hook(request):
        limiter.before_send(request)
        if case == "inflight_stop":
            admitted.set()
            assert release.wait(5)
    with httpx.Client(transport=httpx.MockTransport(oracle), event_hooks={"request": [hook]}, follow_redirects=False) as client:
        if case == "deadline":
            limiter.deadline = time.monotonic() - 1
            with pytest.raises(WorkflowExecutionCancelled): client.post("https://api.deepseek.com/chat/completions", json=_payload())
            assert oracle.sends == limiter.snapshot()["used"] == 0
            assert limiter.snapshot()["stopped"]
        elif case == "stop_race":
            barrier = threading.Barrier(2)
            def race_send():
                barrier.wait(5)
                try: return client.post("https://api.deepseek.com/chat/completions", json=_payload()).status_code
                except WorkflowExecutionCancelled: return "denied"
            def race_stop():
                barrier.wait(5)
                return limiter.stop("offline concurrent stop")
            with ThreadPoolExecutor(max_workers=2) as pool:
                sending, stopping = pool.submit(race_send), pool.submit(race_stop)
                assert sending.result(5) in {200, "denied"}
                assert stopping.result(5)["stopped"]
            with pytest.raises(WorkflowExecutionCancelled): client.post("https://api.deepseek.com/chat/completions", json=_payload())
            assert oracle.sends <= limiter.snapshot()["used"] <= 1
        elif case == "audit_failure":
            limiter = launcher.SingleRunAttemptLimiter(TASK, RUN, 1, audit_path=tmp_path,
                request_contract={"base_url": "https://api.deepseek.com", "model": "deepseek-v4-flash", "path": "/chat/completions", "max_tokens": 65536})
            with pytest.raises(WorkflowExecutionCancelled): client.post("https://api.deepseek.com/chat/completions", json=_payload())
            assert oracle.sends == 0 and limiter.snapshot()["stopped"]
            assert limiter.snapshot()["used"] == 1
        elif case == "inflight_stop":
            with ThreadPoolExecutor(max_workers=1) as pool:
                future = pool.submit(client.post, "https://api.deepseek.com/chat/completions", json=_payload())
                assert admitted.wait(5)
                limiter.stop("offline-explicit-stop")
                release.set()
                assert future.result(5).status_code == 200
            with pytest.raises(WorkflowExecutionCancelled): client.post("https://api.deepseek.com/chat/completions", json=_payload())
            assert oracle.sends == 1 == limiter.snapshot()["used"]
        else:
            barrier = threading.Barrier(8)
            def send():
                barrier.wait(5)
                try:
                    client.post("https://api.deepseek.com/chat/completions", json=_payload())
                    return "sent"
                except WorkflowExecutionCancelled: return "denied"
            with ThreadPoolExecutor(max_workers=8) as pool: results = list(pool.map(lambda _: send(), range(8)))
            assert results.count("sent") == 1 and results.count("denied") == 7
            assert oracle.sends == limiter.snapshot()["used"] == 1


@pytest.mark.parametrize("mode", ["complete", "http500", "connect", "timeout", "missing", "overrun", "overrun_drain", "explicit_draining"])
def test_provider_failures_preserve_budget_settlement(launcher, tmp_path, mode):
    signatures = []
    errors = []
    for controlled in (False, True):
        oracle = SendOracle("overrun" if mode == "overrun_drain" else mode)
        with _real_gateway(launcher, tmp_path / str(controlled), oracle, controlled=controlled,
                           token_limit=1000 if mode == "overrun_drain" else 1000000) as run:
            first = run.gateway.generate(_request())
            assert first.success is (mode not in {"http500", "connect", "timeout"})
            if controlled: assert not run.limiter.snapshot()["stopped"]
            assert oracle.sends == 1
            if mode == "overrun_drain":
                from apps.enterprise_document.services.agent_rag_v1 import CanonicalBudgetCapacityExceeded
                with pytest.raises(CanonicalBudgetCapacityExceeded) as caught:
                    run.gateway.generate(_request())
                assert caught.value.__context__ is None
                errors.append((type(caught.value).__name__, str(caught.value),
                    caught.value.__cause__, caught.value.__context__))
                assert oracle.sends == 1
                exhausted = run.manager.canonical_state()
                assert exhausted.remaining.model_tokens_total == 0
                assert "model_tokens_total" in exhausted.exhausted_dimensions
                assert exhausted.draining is False  # Draining is explicit in the original contract.
            elif mode == "explicit_draining":
                from apps.enterprise_document.services.agent_rag_v1 import CanonicalBudgetDraining
                run.manager.start_draining(expected_revision=run.manager.canonical_state().revision)
                with pytest.raises(CanonicalBudgetDraining) as caught:
                    run.gateway.generate(_request("new-logical-after-explicit-draining"))
                errors.append((type(caught.value).__name__, str(caught.value)))
                assert oracle.sends == 1 and run.manager.canonical_state().draining
            else:
                assert run.gateway.generate(_request()).success
                assert oracle.sends == 2
            state = run.manager.canonical_state()
            assert state.active_reserved.model_tokens_total == 0
            assert all(r.status == "consumed" for r in state.reservations)
            if mode in {"overrun", "overrun_drain"}: assert state.used.model_tokens_total >= 100000
            if mode == "missing": assert state.used.model_tokens_total > 12
            signatures.append(_budget_signature(run.manager))
            if controlled: assert oracle.sends == run.limiter.snapshot()["used"] <= 3
    assert signatures[0] == signatures[1]
    if errors:
        assert errors[0] == errors[1]
        _write(tmp_path / "UNCHANGED_ORIGINAL_ERROR.json", {"mode": mode,
            "baseline_error": errors[0], "controlled_error": errors[1], "new_handler_sends": 0})


@pytest.mark.parametrize("case", ["valid", "missing_n", "host", "port", "path", "query", "userinfo", "method", "model", "thinking", "n", "stream", "max_tokens", "server_tools", "async", "sdk_responses", "invalid_json", "unread_stream", "json_list", "unknown_field"])
def test_final_request_contract_and_async_denial(launcher, tmp_path, case):
    from core.runtime.execution_control import WorkflowExecutionCancelled
    limiter, oracle = _limiter(launcher, tmp_path), SendOracle()
    if case == "sdk_responses":
        with _real_gateway(launcher, tmp_path / "sdk_responses", oracle) as run:
            with pytest.raises(WorkflowExecutionCancelled): run.model.root_client.responses.create(model="deepseek-v4-flash", input="offline")
            assert oracle.sends == run.limiter.snapshot()["used"] == 0
            assert run.limiter.snapshot()["stopped"]
        return
    url, method, payload = "https://api.deepseek.com/chat/completions", "POST", _payload(n=1)
    if case in {"invalid_json", "unread_stream", "json_list"}:
        with httpx.Client(transport=httpx.MockTransport(oracle), event_hooks={"request": [limiter.before_send]}, follow_redirects=False) as client:
            content = iter([b"{unread}"]) if case == "unread_stream" else (b"[1,2,3]" if case == "json_list" else b"{broken")
            request = httpx.Request("POST", url, content=content)
            with pytest.raises(WorkflowExecutionCancelled): client.send(request)
        assert oracle.sends == limiter.snapshot()["used"] == 0
        assert limiter.snapshot()["stopped"]
        return
    if case == "missing_n": payload.pop("n")
    if case == "host": url = "https://unapproved.invalid/chat/completions"
    if case == "port": url = "https://api.deepseek.com:444/chat/completions"
    if case == "path": url = "https://api.deepseek.com/embeddings"
    if case == "query": url += "?forward=another-host"
    if case == "userinfo": url = "https://name:password@api.deepseek.com/chat/completions"
    if case == "method": method = "GET"
    if case == "model": payload["model"] = "unapproved-model"
    if case == "thinking": payload["thinking"] = {"type": "enabled"}
    if case == "n": payload["n"] = 2
    if case == "stream": payload["stream"] = True
    if case == "max_tokens": payload["max_tokens"] = 65537
    if case == "server_tools": payload["tools"] = [{"type": "web_search"}]
    if case == "unknown_field": payload["unapproved_paid_service"] = "extra"
    original = copy.deepcopy(payload)
    if case == "async":
        async def attempt():
            async with httpx.AsyncClient(transport=httpx.MockTransport(oracle), event_hooks={"request": [limiter.async_before_send]}) as client:
                await client.post(url, json=payload)
        with pytest.raises(WorkflowExecutionCancelled): asyncio.run(attempt())
        assert oracle.sends == 0
    else:
        with httpx.Client(transport=httpx.MockTransport(oracle), event_hooks={"request": [limiter.before_send]}, follow_redirects=False) as client:
            if case in {"valid", "missing_n"}:
                assert client.request(method, url, json=payload).status_code == 200
                assert json.loads(oracle.requests[0].content) == original
                assert oracle.sends == limiter.snapshot()["used"] == 1
            else:
                with pytest.raises(WorkflowExecutionCancelled): client.request(method, url, json=payload)
                assert oracle.sends == 0
    assert payload == original


@pytest.mark.parametrize("case", ["redirect307", "redirect308", "sdk_zero", "explicit_second", "proxy", "direct", "openai_proxy"])
def test_retry_redirect_and_proxy_contract(launcher, tmp_path, case, monkeypatch):
    if case.startswith("redirect"):
        seen = []
        def redirect(request):
            seen.append(request)
            return httpx.Response(int(case[-3:]), headers={"location": "https://api.deepseek.com/another"}, request=request)
        with _real_gateway(launcher, tmp_path, redirect) as run:
            result = run.gateway.generate(_request())
            assert not result.success
            assert len(seen) == run.limiter.snapshot()["used"] == 1
            assert run.clients.sync_client.follow_redirects is False
    elif case in {"proxy", "direct", "openai_proxy"}:
        for name in ("HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "http_proxy", "https_proxy", "all_proxy", "NO_PROXY", "no_proxy"):
            monkeypatch.delenv(name, raising=False)
        if case == "proxy":
            monkeypatch.setenv("HTTPS_PROXY", "http://offline-user:" + PROXY_SECRET + "@127.0.0.1:9")
        if case == "direct": monkeypatch.setenv("NO_PROXY", "*")
        if case == "openai_proxy": monkeypatch.setenv("OPENAI_PROXY", "http://offline-user:" + PROXY_SECRET + "@127.0.0.1:9")
        if case == "openai_proxy":
            clients = launcher.OwnedHttpClients(_limiter(launcher, tmp_path))
            try:
                with clients.install(), pytest.raises(launcher.LaunchContractError):
                    clients.construct_chat_model(**_model_kwargs())
                assert clients.limiter.snapshot()["stopped"]
            finally: clients.close(idle=True)
            return
        # The future Worker is fresh. Run each proxy environment in a fresh
        # process so the installed SDK's intentional process cache cannot carry
        # a previous test case's proxy mounts into the baseline oracle.
        body = '''
probe_spec = importlib.util.spec_from_file_location("full8_test_helpers", ROOT / "backend/tests/test_post_phase1_full8_launcher.py")
probe = importlib.util.module_from_spec(probe_spec)
sys.modules[probe_spec.name] = probe
probe_spec.loader.exec_module(probe)
from langchain_openai.chat_models import _client_utils as utils
from httpx._utils import get_environment_proxies
before = get_environment_proxies()
bypass = utils._should_bypass_socket_options_for_proxy_env(http_socket_options=None, http_client=None, http_async_client=None, openai_proxy=None)
options = () if bypass else utils._resolve_socket_options(None)
baseline = utils._get_default_httpx_client("https://api.deepseek.com", 300.0, options)
baseline_async = utils._get_default_async_httpx_client("https://api.deepseek.com", 300.0, options)
limiter = launcher.SingleRunAttemptLimiter(probe.TASK, probe.RUN, 2, audit_path=SANDBOX / "network_permits.jsonl")
clients = launcher.OwnedHttpClients(limiter)
try:
    with clients.install(): clients.construct_chat_model(**probe._model_kwargs())
    assert get_environment_proxies() == before
    assert probe._network_signature(clients.sync_client) == probe._network_signature(baseline)
    assert probe._network_signature(clients.async_client) == probe._network_signature(baseline_async)
    assert clients.sync_client is not baseline and clients.async_client is not baseline_async
    assert clients.sync_client.follow_redirects is False and clients.async_client.follow_redirects is False
    assert clients.close(idle=True)["closed"]
    assert not baseline.is_closed and not baseline_async.is_closed
    (SANDBOX / "network_equivalence.json").write_text(json.dumps({"equivalent": True, "fresh_cached_baseline": True, "shared_clients_closed": False, "handler_sends": 0}))
finally:
    clients.close(idle=True)
'''
        _fresh_python(launcher, tmp_path, body)
    else:
        oracle = SendOracle("http500" if case == "sdk_zero" else "complete")
        with _real_gateway(launcher, tmp_path, oracle) as run:
            assert run.model.root_client.max_retries == 0
            run.gateway.generate(_request())
            assert oracle.sends == 1
            if case == "explicit_second":
                run.gateway.generate(_request())
                assert oracle.sends == 2 == run.limiter.snapshot()["used"]


@pytest.mark.parametrize("case", ["local_stop", "budget_denial", "settlement_failure"])
def test_local_stop_preserves_canonical_cancellation(launcher, tmp_path, case, monkeypatch):
    from core.runtime.execution_control import WorkflowExecutionCancelled
    from apps.enterprise_document.services.agent_rag_v1 import CanonicalBudgetCapacityExceeded
    oracle = SendOracle()
    with _real_gateway(launcher, tmp_path, oracle, token_limit=1 if case == "budget_denial" else 1000000) as run:
        if case != "budget_denial": run.limiter.stop("offline stop")
        if case == "settlement_failure":
            original_error = RuntimeError("offline settlement failure")
            def fail(*args, **kwargs): raise original_error
            monkeypatch.setattr(run.gateway.budget_runtime, "settle_provider_attempt", fail)
            with pytest.raises(RuntimeError) as caught: run.gateway.generate(_request())
            assert caught.value is original_error
            assert isinstance(caught.value.__context__, WorkflowExecutionCancelled)
            assert run.manager.canonical_state().active_reserved.model_tokens_total > 0
        elif case == "budget_denial":
            with pytest.raises(CanonicalBudgetCapacityExceeded): run.gateway.generate(_request())
            state = run.manager.canonical_state()
            assert state.used.logical_model_calls == 0
            assert state.active_reserved.logical_model_calls == 0
        else:
            with pytest.raises(WorkflowExecutionCancelled): run.gateway.generate(_request())
            state = run.manager.canonical_state()
            assert state.used.logical_model_calls == 1
            assert state.used.model_tokens_total > 0
            assert state.active_reserved.model_tokens_total == 0
            assert run.gateway.usage_snapshot()["provider_attempts"] == 1
            with pytest.raises(WorkflowExecutionCancelled): run.gateway.generate(_request())
            assert run.limiter.snapshot()["used"] == 0
        assert oracle.sends == 0


@pytest.mark.parametrize("case", ["application_boundary", "credential_error"])
def test_native_startup_order_and_patch_restore(launcher, tmp_path, case):
    contract, path = _contract(tmp_path)
    payload = repr(json.dumps(contract))
    body = '''
contract = json.loads(CONTRACT)
events = []
assert not any(name in sys.modules for name in ("application.mainline_service", "pyarrow", "faiss", "torch"))
original_spec = importlib.util.spec_from_file_location
def observed_spec(name, location, *args, **kwargs):
    spec = original_spec(name, location, *args, **kwargs)
    if name == "post_bca014_h7_native_startup":
        original_exec = spec.loader.exec_module
        def observed_exec(module):
            original_exec(module)
            activate = module.activate_native_overlay
            def observed_activate(path):
                events.append("H7/native_begin")
                result = activate(path)
                assert "application.mainline_service" not in sys.modules
                events.append("H7/native_done")
                # Presence is now checked before resume. Exercise the original
                # post-H7 credential failure at its actual provision boundary.
                if CASE == "credential_error":
                    os.environ.pop("DEEPSEEK_API_KEY", None)
                return result
            module.activate_native_overlay = observed_activate
        spec.loader.exec_module = observed_exec
    return spec
importlib.util.spec_from_file_location = observed_spec
original_import = builtins.__import__
def observed_import(name, *args, **kwargs):
    if name == "model_gateway.integrations.langchain" and "H7/native_done" in events:
        events.append("credential/controls")
    if name == "application.mainline_service":
        assert events[:2] == ["H7/native_begin", "H7/native_done"]
        module = sys.modules["model_gateway.integrations.langchain.deepseek_chat_model"]
        assert getattr(module.DeepSeekChatOpenAI, "__self__", None) is not None
        events.append("application")
        raise RuntimeError("OFFLINE_APPLICATION_BOUNDARY")
    return original_import(name, *args, **kwargs)
builtins.__import__ = observed_import
start = time.monotonic()
binding = {"worker": launcher._process_identity(os.getpid()), "supervisor": launcher._process_identity(os.getppid()), "nonce": "fresh-offline", "clock_start": start-1, "dispatch_deadline": start+60, "deadline": start+65}
code = launcher.run_worker(contract, binding, "fresh-offline")
builtins.__import__ = original_import
importlib.util.spec_from_file_location = original_spec
assert code == 1
assert events[:2] == ["H7/native_begin", "H7/native_done"], events
if CASE == "application_boundary":
    assert events[-1] == "application", events
    from model_gateway.integrations.langchain import deepseek_chat_model
    assert isinstance(deepseek_chat_model.DeepSeekChatOpenAI, type)
    assert "credential_bootstrap" not in sys.modules
else:
    assert "application" not in events
    assert "model_gateway.integrations.langchain.deepseek_chat_model" not in sys.modules
(SANDBOX / "native_order.json").write_text(json.dumps({"events": events, "exit_code": code, "case": CASE}))
'''.replace("CONTRACT", payload).replace("CASE", repr(case))
    _fresh_python(launcher, tmp_path, body, contract=contract)
    observation = json.loads((tmp_path / "native_order.json").read_text(encoding="utf-8"))
    assert observation["events"][:2] == ["H7/native_begin", "H7/native_done"]
    assert not (Path(contract["output_root"]) / "runs").exists()


@pytest.mark.parametrize("case", ["fresh_import", "old_cache_not_migrated", "plan_cache_escape", "plan_temp_escape", "plan_rag_escape"])
def test_cache_environment_precedes_imports(launcher, tmp_path, case):
    contract, path = _contract(tmp_path)
    old_cache = tmp_path / "old_cache"
    old_cache.mkdir()
    marker = old_cache / "untouched.bin"
    marker.write_bytes(b"old-cache-identity")
    before = _sha(marker)
    plan = json.loads(Path(contract["plan_path"]).read_text(encoding="utf-8"))
    if case.startswith("plan_"):
        key = {"plan_cache_escape": "HF_HOME", "plan_temp_escape": "TEMP", "plan_rag_escape": "RAG_PROJECT_ROOT"}[case]
        plan["environment"][key] = str(old_cache)
        _write(contract["plan_path"], plan)
        with pytest.raises(launcher.LaunchContractError):
            launcher.build_child_environment(contract)
    else:
        environment = launcher.build_child_environment(contract, {"HF_HOME": str(old_cache), "TEMP": str(old_cache)})
        prep = Path(contract["prep_root"])
        for name in launcher.CACHE_VARIABLES:
            assert Path(environment[name]).is_relative_to(prep / "cache")
        assert environment["PYTHONDONTWRITEBYTECODE"] == "1"
        assert environment["HF_HUB_OFFLINE"] == environment["TRANSFORMERS_OFFLINE"] == "1"
        body = '''
assert "huggingface_hub.constants" not in sys.modules
from huggingface_hub import constants
assert pathlib.Path(constants.HF_HOME) == pathlib.Path(os.environ["HF_HOME"])
assert constants.HF_HUB_OFFLINE is True
assert sys.dont_write_bytecode is True
assert "application.mainline_service" not in sys.modules
expected = pathlib.Path(PREP) / "cache"
for name in launcher.CACHE_VARIABLES:
    assert pathlib.Path(os.environ[name]).is_relative_to(expected)
(SANDBOX / "cache_probe.json").write_text(json.dumps({"hf_home": str(constants.HF_HOME), "offline": constants.HF_HUB_OFFLINE}))
'''.replace("PREP", repr(contract["prep_root"]))
        _fresh_python(launcher, tmp_path, body, contract=contract)
        assert not list(prep.rglob("*.pyc"))
        assert not list((prep / "cache").rglob("untouched.bin"))
    assert _sha(marker) == before
    assert list(old_cache.iterdir()) == [marker]


@pytest.mark.parametrize("case", ["public_error", "worker_exception", "proxy_conflict", "secret_plan", "proxy_plan"])
def test_credentials_and_proxy_auth_are_redacted(launcher, tmp_path, case, monkeypatch, capsys):
    secret = FAKE_KEY + "/" + PROXY_SECRET
    contract, path = _contract(tmp_path)
    if case == "public_error":
        def fail(*args, **kwargs):
            raise RuntimeError(secret)
        monkeypatch.setattr(launcher, "load_and_validate_launch_contract", fail)
        assert launcher.main(["--contract", str(path), "--authorization", str(tmp_path / "approval.json")]) == 2
    elif case == "worker_exception":
        def fail(*args, **kwargs):
            raise RuntimeError(secret)
        monkeypatch.setattr(launcher, "load_pinned_worker", lambda _: SimpleNamespace(main=fail))
        assert launcher.run_worker(contract, _binding(launcher), "offline-bound-nonce") == 1
    elif case == "proxy_conflict":
        monkeypatch.setenv("OPENAI_PROXY", "http://offline-user:" + PROXY_SECRET + "@127.0.0.1:9")
        clients = launcher.OwnedHttpClients(_limiter(launcher, tmp_path), transport=httpx.MockTransport(SendOracle()))
        with clients.install(), pytest.raises(launcher.LaunchContractError) as caught:
            clients.construct_chat_model(**_model_kwargs())
        assert FAKE_KEY not in str(caught.value) and PROXY_SECRET not in str(caught.value)
    else:
        plan = json.loads(Path(contract["plan_path"]).read_text(encoding="utf-8"))
        key = "DEEPSEEK_API_KEY" if case == "secret_plan" else "HTTPS_PROXY"
        plan["environment"][key] = FAKE_KEY if case == "secret_plan" else "http://name:" + PROXY_SECRET + "@127.0.0.1:9"
        _write(contract["plan_path"], plan)
        with pytest.raises(launcher.LaunchContractError) as caught:
            launcher.build_child_environment(contract)
        assert FAKE_KEY not in str(caught.value) and PROXY_SECRET not in str(caught.value)
    output = capsys.readouterr()
    assert FAKE_KEY not in output.out + output.err and PROXY_SECRET not in output.out + output.err
    for artifact in (Path(contract["prep_root"]) / "audit").glob("*"):
        if artifact.is_file():
            text = artifact.read_text(encoding="utf-8")
            assert FAKE_KEY not in text and PROXY_SECRET not in text


@pytest.mark.parametrize("case", ["normal_idle", "async_constructor_failure", "construction_error", "not_idle", "active_loop", "close_error"])
def test_owned_sdk_httpx_resources_close(launcher, tmp_path, case, monkeypatch):
    import openai
    from model_gateway.integrations.langchain import deepseek_chat_model
    original_class = deepseek_chat_model.DeepSeekChatOpenAI
    calls = {"sync_sdk": 0, "async_sdk": 0, "sync_httpx": 0, "async_httpx": 0}
    clients = launcher.OwnedHttpClients(_limiter(launcher, tmp_path),
        transport=httpx.MockTransport(SendOracle()), async_transport=httpx.MockTransport(SendOracle()))
    shared = httpx.Client(transport=httpx.MockTransport(SendOracle()))
    old_sync, old_async = openai.OpenAI.close, openai.AsyncOpenAI.close
    old_httpx, old_httpx_async = httpx.Client.close, httpx.AsyncClient.aclose
    def sdk_close(self):
        if any(self is root for root in clients.sdk_roots):
            calls["sync_sdk"] += 1
            if case == "close_error":
                raise RuntimeError("offline SDK close failure")
        return old_sync(self)
    async def sdk_aclose(self):
        if any(self is root for root in clients.sdk_roots):
            calls["async_sdk"] += 1
        return await old_async(self)
    def httpx_close(self):
        if self is clients.sync_client: calls["sync_httpx"] += 1
        return old_httpx(self)
    async def httpx_aclose(self):
        if self is clients.async_client: calls["async_httpx"] += 1
        return await old_httpx_async(self)
    monkeypatch.setattr(openai.OpenAI, "close", sdk_close)
    monkeypatch.setattr(openai.AsyncOpenAI, "close", sdk_aclose)
    monkeypatch.setattr(httpx.Client, "close", httpx_close)
    monkeypatch.setattr(httpx.AsyncClient, "aclose", httpx_aclose)
    try:
        if case == "async_constructor_failure":
            def broken_async(*args, **kwargs): raise RuntimeError("offline async constructor failed")
            monkeypatch.setattr(openai.AsyncOpenAI, "__init__", broken_async)
        if case == "construction_error":
            original_init = original_class.__init__
            def late_failure(self, *args, **kwargs):
                original_init(self, *args, **kwargs)
                raise RuntimeError("offline post-construction failure")
            monkeypatch.setattr(original_class, "__init__", late_failure)
        with clients.install():
            if case in {"async_constructor_failure", "construction_error"}:
                with pytest.raises(RuntimeError): clients.construct_chat_model(**_model_kwargs())
            else:
                clients.construct_chat_model(**_model_kwargs())
        assert deepseek_chat_model.DeepSeekChatOpenAI is original_class
        if case == "not_idle":
            closure = clients.close(idle=False)
            assert closure["closed"] is False and closure["graceful"] is False
            assert calls == {name: 0 for name in calls}
        if case == "active_loop":
            async def active(): return clients.close(idle=True)
            assert asyncio.run(active())["closed"] is False
        closure = clients.close(idle=True)
        assert closure["closed"] is (case != "close_error")
        assert closure["graceful"] is (case != "close_error")
        assert clients.sync_client.is_closed and clients.async_client.is_closed
        assert calls["sync_sdk"] >= 1 and calls["sync_httpx"] >= 1 and calls["async_httpx"] >= 1
        if case != "async_constructor_failure": assert calls["async_sdk"] >= 1
        if case != "close_error":
            snapshot = dict(calls)
            assert clients.close(idle=True) == closure
            assert calls == snapshot
        assert not shared.is_closed
    finally:
        old_httpx(shared)
        if clients.sync_client and not clients.sync_client.is_closed: old_httpx(clients.sync_client)
        if clients.async_client and not clients.async_client.is_closed: asyncio.run(old_httpx_async(clients.async_client))


@pytest.mark.parametrize("case", ["owned_tree", "abnormal_parent", "assignment_failure", "supervisor_crash", "unique_intent", "binding_valid", "binding_nonce", "binding_pid", "binding_creation", "direct_internal"])
def test_job_ownership_and_unique_launch(launcher, tmp_path, case, monkeypatch):
    import psutil
    if case.startswith("binding_"):
        binding = _binding(launcher)
        nonce = "offline-bound-nonce"
        if case == "binding_nonce": nonce = "another-nonce"
        if case == "binding_pid": binding["worker"]["pid"] += 100000
        if case == "binding_creation": binding["worker"]["creation_time"] += 1
        if case == "binding_valid": assert launcher.validate_worker_binding(binding, nonce) == binding
        else:
            with pytest.raises(launcher.LaunchContractError): launcher.validate_worker_binding(binding, nonce)
        return
    if case == "unique_intent":
        audit = tmp_path / "audit"
        audit.mkdir()
        barrier = threading.Barrier(2)
        def compete(_):
            barrier.wait(5)
            try: return launcher.exclusive_launch_intent(audit, TASK, RUN)
            except FileExistsError: return None
        with ThreadPoolExecutor(max_workers=2) as pool: results = list(pool.map(compete, range(2)))
        assert sum(item is not None for item in results) == 1
        before = (audit / "LAUNCH_INTENT.json").read_bytes()
        with pytest.raises(FileExistsError): launcher.exclusive_launch_intent(audit, TASK, RUN)
        assert (audit / "LAUNCH_INTENT.json").read_bytes() == before
        return
    if case == "direct_internal":
        contract, path = _contract(tmp_path)
        monkeypatch.setattr(launcher, "load_and_validate_launch_contract", lambda *a, **k: contract)
        assert launcher.main(["--contract", str(path), "--authorization", str(tmp_path / "fake.json"), "--internal-worker", "--nonce", "unbound"]) == 2
        assert not (Path(contract["output_root"]) / "runs").exists()
        return
    script = _dummy_program(tmp_path)
    if case == "supervisor_crash":
        body = '''
job = launcher.WindowsJob()
job.start([sys.executable, "-I", "-B", str(SANDBOX / "owned_dummy.py"), "tree"], cwd=SANDBOX)
limit = time.monotonic() + 8
while not (SANDBOX / "grandchild.json").exists() and time.monotonic() < limit: time.sleep(.02)
assert (SANDBOX / "grandchild.json").exists()
os._exit(29)
'''
        # The crash is intentional; use the same guarded child preamble and
        # inspect its raw exit without converting it to a passing business run.
        _fresh_python(launcher, tmp_path, body, expected_exit=29)
        identities = [json.loads((tmp_path / (name + ".json")).read_text(encoding="utf-8")) for name in ("tree", "child", "grandchild")]
        for identity in identities: _wait_until(lambda item=identity: _pid_gone(item))
        return
    unrelated = subprocess.Popen([sys.executable, "-I", "-B", str(script), "unrelated"],
        cwd=tmp_path, creationflags=subprocess.CREATE_NO_WINDOW, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    owned = None
    identities = []
    try:
        owned = launcher.WindowsJob()
        assert not os.get_handle_inheritable(int(owned.handle))
        if case == "assignment_failure":
            def denied(*args): raise PermissionError("offline AssignProcessToJobObject denied")
            monkeypatch.setattr(owned.job, "AssignProcessToJobObject", denied)
            with pytest.raises(PermissionError):
                owned.start([sys.executable, "-I", "-B", str(script), "tree"], cwd=tmp_path)
            assert "resumed" not in owned.events and "assignment_failed_no_resume" in owned.events
            assert not (tmp_path / "tree.json").exists()
            _wait_until(lambda: not psutil.pid_exists(owned.pid))
        else:
            role = "abnormal" if case == "abnormal_parent" else "tree"
            def suspended(identity):
                assert not (tmp_path / (role + ".json")).exists()
                assert owned.events == ["created_suspended", "assigned_to_job"]
            owned.start([sys.executable, "-I", "-B", str(script), role], cwd=tmp_path, before_resume=suspended)
            _wait_until(lambda: (tmp_path / "grandchild.json").is_file())
            identities = [json.loads((tmp_path / (name + ".json")).read_text(encoding="utf-8")) for name in (role, "child", "grandchild")]
            if case == "abnormal_parent": assert owned.wait(5) == 23
            assert owned.events[:3] == ["created_suspended", "assigned_to_job", "resumed"]
            assert len(owned.process_ids()) >= 2
            owned.close()
            for identity in identities: _wait_until(lambda item=identity: _pid_gone(item))
        assert unrelated.poll() is None
    finally:
        if owned is not None: owned.close()
        unrelated.terminate()
        unrelated.wait(5)
        for identity in identities: _wait_until(lambda item=identity: _pid_gone(item))


@pytest.mark.parametrize("case", ["source", "h7", "credential", "worker_error", "raw_capture_failure", "exit7", "watchdog"])
def test_early_error_and_watchdog_preserve_evidence(launcher, tmp_path, case, monkeypatch):
    contract, path = _contract(tmp_path)
    audit = Path(contract["prep_root"]) / "audit"
    if case in {"exit7", "watchdog"}:
        script = _dummy_program(tmp_path)
        original_start = launcher.WindowsJob.start
        starts = []
        def dummy_start(self, argv, **kwargs):
            starts.append(list(argv))
            return original_start(self, [sys.executable, "-I", "-B", str(script), case if case == "exit7" else "tree"], **kwargs)
        monkeypatch.setattr(launcher.WindowsJob, "start", dummy_start)
        contract.update(_contract_path=str(path), _contract_sha256=_sha(path))
        contract["watchdog"] = {"observation_seconds": 2 if case == "watchdog" else 5, "forensic_seconds": 1, "reap_seconds": 3}
        started = time.monotonic()
        code = launcher.supervise(contract, tmp_path / "fake-authorization.json")
        elapsed = time.monotonic() - started
        receipt = json.loads((audit / "SUPERVISOR_EXIT.json").read_text(encoding="utf-8"))
        assert code == (7 if case == "exit7" else 124)
        assert receipt["exit_code"] == code and receipt["automatic_rerun"] is False
        assert len(starts) == 1
        assert elapsed < sum(contract["watchdog"].values()) + 2
        assert b"owned dummy" in (audit / "WORKER_STDOUT.log").read_bytes()
        assert b"owned dummy stderr" in (audit / "WORKER_STDERR.log").read_bytes()
        assert (audit / "WORKER_BINDING.json").is_file()
        if case == "exit7":
            assert (tmp_path / "partial.jsonl").read_bytes().endswith(b'{"partial":')
        else:
            stop = json.loads((audit / "WATCHDOG_STOP.json").read_text(encoding="utf-8"))
            assert stop["right_censored"] and stop["native_terminal"] == "UNVERIFIED"
            assert stop["dispatch_deadline"] <= stop["monotonic"] <= stop["deadline"]
            for name in ("tree", "child", "grandchild"):
                identity = json.loads((tmp_path / (name + ".json")).read_text(encoding="utf-8"))
                _wait_until(lambda item=identity: _pid_gone(item))
    else:
        calls = []
        if case == "source":
            contract["source_sha256"][str(LAUNCHER)] = "0" * 64
        def worker_main(plan):
            calls.append(plan)
            if case == "credential":
                os.environ.pop("DEEPSEEK_API_KEY", None)
                sys.modules["credential_bootstrap"].provision()
            if case == "raw_capture_failure": raise OSError("offline required raw capture failed")
            raise RuntimeError("offline " + case + " failure")
        def load(_):
            if case == "h7": raise RuntimeError("offline H7 failure")
            return SimpleNamespace(main=worker_main)
        monkeypatch.setattr(launcher, "load_pinned_worker", load)
        code = launcher.run_worker(contract, _binding(launcher), "offline-bound-nonce")
        assert code == 1
        assert len(calls) == (0 if case in {"source", "h7"} else 1)
        receipts = list(audit.glob("WORKER*ERROR.json")) + list(audit.glob("WORKER_EXIT.json"))
        assert receipts
        raw = json.loads(receipts[0].read_text(encoding="utf-8"))
        assert raw["automatic_rerun"] is False and raw["native_terminal_modified"] is False
        if case == "raw_capture_failure": assert raw["early_error"]["type"] == "OSError"
    assert not list(Path(contract["output_root"]).rglob("*terminal*"))


@pytest.fixture(scope="module")
def native_best_effort_summary(tmp_path_factory):
    from system_test.mainline_closure import run_fake_mainline_scenario
    return run_fake_mainline_scenario(tmp_path_factory.mktemp("launcher_fake_best_effort"),
        run_id="run_launcher_contract_fake_best_effort", rag_scenario="business_gate_failure",
        llm_scenario="force_business_gate_failure", enable_corrective_section_retrieval=True)


@pytest.mark.parametrize("case", ["exit_zero", "partial_unverified", "valid_best_effort", "valid_unresolved", "missing_receipt", "missing_publication", "forged_quality", "unverified_release", "forced_exit"])
def test_terminal_quality_release_are_separate(launcher, native_best_effort_summary, tmp_path, case):
    summary = copy.deepcopy(native_best_effort_summary)
    if case == "valid_unresolved":
        from system_test.mainline_closure import run_fake_mainline_scenario
        summary = run_fake_mainline_scenario(tmp_path / "native_unresolved",
            run_id="run_launcher_contract_fake_unresolved", rag_scenario="citation_collision",
            llm_scenario="always_grounded", enable_corrective_section_retrieval=True)
    if case == "exit_zero": summary = {}
    if case == "partial_unverified": summary = {"status": "partial_success", "terminal_outcome": {"quality_state": "UNRESOLVED"}}
    if case == "missing_receipt": summary["terminal_outcome"] = None
    if case == "missing_publication": summary["target_release_publication"] = None
    if case == "forged_quality": summary["terminal_outcome"]["quality_state"] = "PASS"
    if case == "unverified_release": summary["target_release_publication"]["verified_after_write"] = False
    verdict = launcher.classify_terminal(0, summary, forced=case == "forced_exit")
    assert verdict["worker_exit_code"] == 0 and verdict["exit_zero_is_business_pass"] is False
    assert verdict["billing_hard_cap"] == "BILLING_HARD_CAP_UNVERIFIED"
    assert verdict["human_approval"] in {"UNVERIFIED", "NOT_APPROVED", "UNAPPROVED"}
    if case in {"valid_best_effort", "valid_unresolved", "forced_exit"}:
        assert summary["status"] == "partial_success"
        assert verdict["native_summary_status"] == "partial_success"
        assert verdict["technical_terminal"] == "SUCCESS"
        assert verdict["business_quality"] == ("UNRESOLVED" if case == "valid_unresolved" else "FAIL")
        assert verdict["release"] == "BEST_EFFORT"
        assert verdict["control"] == "OUTPUT_BEST_EFFORT"
        assert verdict["forced_exit"] is (case == "forced_exit")
    else:
        assert verdict["business_quality"] == "UNVERIFIED"
        assert verdict["release"] == "UNVERIFIED"
        assert verdict["technical_terminal"] == "UNVERIFIED"


@pytest.mark.parametrize("case", ["enabled", "disabled", "sample_failure", "sample_write_failure", "disk_failure", "business_exception", "permit_audit_failure"])
def test_e1_sampling_is_passive(launcher, tmp_path, case, monkeypatch):
    contract, path = _contract(tmp_path)
    if case == "permit_audit_failure":
        from core.runtime.execution_control import WorkflowExecutionCancelled
        limiter = _limiter(launcher, tmp_path)
        oracle = SendOracle()
        def required_failure(*args, **kwargs): raise OSError("offline required permit audit failure")
        monkeypatch.setattr(launcher, "_append", required_failure)
        with httpx.Client(transport=httpx.MockTransport(oracle), event_hooks={"request": [limiter.before_send]}) as client:
            with pytest.raises(WorkflowExecutionCancelled): client.post("https://api.deepseek.com/chat/completions", json=_payload())
        assert oracle.sends == 0 and limiter.snapshot()["stopped"] and limiter.snapshot()["used"] == 1
        return
    script = _dummy_program(tmp_path)
    original_start = launcher.WindowsJob.start
    def start(self, argv, **kwargs):
        return original_start(self, [sys.executable, "-I", "-B", str(script), "exit7"], **kwargs)
    monkeypatch.setattr(launcher.WindowsJob, "start", start)
    contract.update(_contract_path=str(path), _contract_sha256=_sha(path))
    contract["watchdog"] = {"observation_seconds": 4, "forensic_seconds": 1, "reap_seconds": 2}
    if case == "disabled":
        monkeypatch.setattr(launcher, "optional_sample_write", lambda *args, **kwargs: None)
    if case == "sample_failure":
        monkeypatch.setattr(launcher, "passive_sample", lambda *a, **k: (_ for _ in ()).throw(PermissionError("offline sampler failure")))
    if case == "sample_write_failure":
        original_append = launcher._append
        def sample_write(path, value):
            if "SAMPLES" in Path(path).name or "METADATA" in Path(path).name: raise OSError("offline optional sample write failure")
            return original_append(path, value)
        monkeypatch.setattr(launcher, "_append", sample_write)
    if case == "disk_failure": monkeypatch.setattr(shutil, "disk_usage", lambda *a: (_ for _ in ()).throw(OSError("offline disk sample failed")))
    if case == "business_exception":
        # An OS sample is supplementary; the original dummy exit remains the
        # outcome even when the child exits with an exception-style code.
        monkeypatch.setattr(launcher, "passive_sample", lambda *a, **k: {"status": "UNKNOWN", "error_type": "AccessDenied"})
    assert launcher.supervise(contract, tmp_path / "offline-approval.json") == 7
    audit = Path(contract["prep_root"]) / "audit"
    receipt = json.loads((audit / "SUPERVISOR_EXIT.json").read_text(encoding="utf-8"))
    assert receipt["exit_code"] == 7 and receipt["error"] is None
    assert receipt["automatic_rerun"] is False
    assert (tmp_path / "partial.jsonl").read_bytes().endswith(b'{"partial":')
    if case in {"sample_failure", "sample_write_failure", "disk_failure"}:
        records = "\n".join(item.read_text(encoding="utf-8") for item in audit.iterdir() if item.is_file())
        assert "UNKNOWN" in records
    if case == "disabled": assert not list(audit.glob("*SAMPLES.jsonl"))


@pytest.mark.parametrize("case", ["pid_reuse", "access_denied", "missing_process", "metadata_only", "partial_newline", "missing_end", "complete_interval", "missing_ledger"])
def test_sampling_unknown_and_censored_evidence(launcher, tmp_path, case, monkeypatch):
    ledger = tmp_path / "large-budget.jsonl"
    ledger.write_bytes(b'{"large":"' + b"x" * 100000 + b'"}\n')
    original_read = Path.read_bytes
    def reject_contents(path):
        if path == ledger: raise AssertionError("Sampler must not load budget content")
        return original_read(path)
    monkeypatch.setattr(Path, "read_bytes", reject_contents)
    if case in {"partial_newline", "missing_end", "complete_interval"}:
        evidence = launcher.sampling_evidence(begin=10., end=12. if case == "complete_interval" else None,
            partial_newline=case == "partial_newline")
        assert evidence["right_censored"] is (case != "complete_interval")
        assert evidence["duration_seconds"] == (2. if case == "complete_interval" else None)
        if case == "partial_newline": assert evidence["status"] == "UNKNOWN"
        assert evidence["exclusive_budget_cost"] == "UNATTRIBUTED"
        return
    current = launcher._process_identity(os.getpid())
    if case == "access_denied":
        monkeypatch.setattr(launcher, "_process_identity", lambda pid: (_ for _ in ()).throw(PermissionError("offline access denied")))
    if case == "missing_process":
        monkeypatch.setattr(launcher, "_process_identity", lambda pid: (_ for _ in ()).throw(ProcessLookupError("offline process absent")))
    if case == "missing_ledger": ledger.unlink()
    sample = launcher.passive_sample(os.getpid(), expected_creation_time=current["creation_time"] + (1 if case == "pid_reuse" else 0), ledger_paths=[ledger])
    assert sample["status"] == ("UNKNOWN" if case in {"pid_reuse", "access_denied", "missing_process"} else "OBSERVED")
    assert sample["internal_budget_attribution"] == "UNATTRIBUTED"
    assert sample["sampler_wall_seconds"] >= 0
    assert sample["sampler_io_bytes"] == "UNVERIFIED"
    metadata = sample["ledger_metadata"][0]
    assert metadata["status"] == ("UNKNOWN" if case == "missing_ledger" else "OBSERVED")
    if case != "missing_ledger": assert metadata["bytes"] == ledger.stat().st_size



def test_g07_tmpdir_real_fresh_process_stays_in_prep(launcher, tmp_path):
    contract, _ = _contract(tmp_path)
    sibling = tmp_path / "outside-prep-temp"
    sibling.mkdir()
    environment = launcher.build_child_environment(contract, {
        "SystemRoot": os.environ.get("SystemRoot", r"C:\Windows"),
        "PATH": str(Path(sys.executable).parent), "TMPDIR": str(sibling),
        "TEMP": str(Path(contract["prep_root"]) / "tmp"), "TMP": str(Path(contract["prep_root"]) / "tmp")})
    body = '''
import tempfile
expected = pathlib.Path(EXPECTED)
sibling = pathlib.Path(SIBLING)
selected = pathlib.Path(tempfile.gettempdir())
fd, created = tempfile.mkstemp(prefix="g07_actual_")
os.close(fd)
receipt = {"selected": str(selected), "created": created, "expected": str(expected),
    "pid": os.getpid(), "real_tempfile": True, "shared_writes": 0}
(SANDBOX / "TMPDIR_ACTUAL.json").write_text(json.dumps(receipt), encoding="utf-8")
assert selected == expected, "TMPDIR escaped fixed TEMP/TMP inside the owned test sandbox"
assert pathlib.Path(created).parent == expected
assert list(sibling.iterdir()) == []
'''.replace("EXPECTED", repr(str(Path(contract["prep_root"]) / "tmp"))).replace("SIBLING", repr(str(sibling)))
    _fresh_python(launcher, tmp_path, body, contract=contract, exact_environment=environment)
    assert list(sibling.iterdir()) == []


def test_g07_shared_dependency_scope_accepts_finite_risk(launcher, tmp_path, monkeypatch):
    contract, _, _, _ = _rag_contract(launcher, tmp_path, monkeypatch)
    before = launcher.validate_shared_dependencies(contract)
    assert before["status"] == "PASSED"
    assert len(before["files"]) == 59
    assert before["risk"] == "ACCEPTED_FINITE_INTEGRITY_RISK"
    assert before["os_enforced_read_only"] is False
    assert {Path(item["path"]) for item in before["files"]} == {
        Path(item["path"]) for item in json.loads(launcher.G07_SHARED_SOURCE.read_text())["files"]}
    assert launcher.postcheck_shared_dependencies(contract, before)["status"] == "PASSED"


@pytest.mark.parametrize("case", ["missing", "extra_embedding", "extra_reranker", "embedding",
    "reranker", "catalog", "yaml", "rebound_input", "hardlink", "ancestor_junction",
    "source_pin", "resource_pin", "input_missing", "unknown_source"])
def test_g07_shared_dependency_preflight_rejects_drift(launcher, tmp_path, monkeypatch, case):
    contract, _, _, model = _rag_contract(launcher, tmp_path, monkeypatch)
    source = json.loads(launcher.G07_SHARED_SOURCE.read_text())
    row = next(item for item in source["files"] if item["role"] == {
        "reranker": "reranker", "catalog": "runtime_catalog", "yaml": "default_intent_policy"
    }.get(case, "embedding"))
    target = Path(row["path"])
    if case == "missing": target.unlink()
    elif case in {"extra_embedding", "extra_reranker"}:
        directory = Path(source["roots"]["embedding" if case == "extra_embedding" else "reranker"])
        extra = directory / "unapproved-model-member.bin"
        extra.write_bytes(b"new member never approved")
        contract["input_sha256"][str(extra)] = _sha(extra)
    elif case in {"embedding", "reranker", "catalog", "yaml", "rebound_input"}:
        target.write_bytes(b"changed shared bytes")
        if case == "rebound_input": contract["input_sha256"][str(target)] = _sha(target)
    elif case == "hardlink":
        alias = tmp_path / "hardlink-alias.bin"
        os.link(target, alias)
    elif case == "ancestor_junction":
        moved = tmp_path / "original-model-moved"
        model.rename(moved)
        import _winapi
        _winapi.CreateJunction(str(moved), str(model))
    elif case == "source_pin": launcher.G07_SHARED_SOURCE.write_text("{}", encoding="utf-8")
    elif case == "resource_pin": launcher.RAG_RESOURCE_MANIFEST.write_text("{}", encoding="utf-8")
    elif case == "input_missing": contract["input_sha256"].pop(str(target))
    elif case == "unknown_source":
        extra = _write(tmp_path / "UNKNOWN_SHARED.json", {"unknown": True})
        source["files"].append({"role": "unknown", "path": str(extra),
            "bytes": extra.stat().st_size, "sha256": _sha(extra)})
        _write(launcher.G07_SHARED_SOURCE, source)
        monkeypatch.setattr(launcher, "G07_SHARED_SOURCE_SHA256", _sha(launcher.G07_SHARED_SOURCE))
        contract["input_sha256"][str(extra)] = _sha(extra)
    with pytest.raises(launcher.LaunchContractError):
        launcher.validate_shared_dependencies(contract)
    assert not list(Path(contract["output_root"]).iterdir())


@pytest.mark.parametrize("case", ["unchanged", "content", "missing", "extra", "permission",
    "deadline", "tree_unreaped", "source_pin", "restored"])
def test_g07_shared_postcheck_separates_drift_and_unknown(launcher, tmp_path, monkeypatch, case):
    contract, _, _, model = _rag_contract(launcher, tmp_path, monkeypatch)
    before = launcher.validate_shared_dependencies(contract)
    target = model / "weights.bin"
    original = target.read_bytes()
    kwargs = {}
    if case == "content": target.write_bytes(b"post-run changed bytes")
    elif case == "missing": target.unlink()
    elif case == "extra": (model / "post-run-extra.bin").write_bytes(b"unapproved")
    elif case == "permission":
        old = launcher.sha256_file
        def unreadable(path):
            if Path(path).resolve() == target.resolve(): raise PermissionError("controlled read denial")
            return old(path)
        monkeypatch.setattr(launcher, "sha256_file", unreadable)
    elif case == "deadline": kwargs["deadline"] = time.monotonic() - 1
    elif case == "tree_unreaped": kwargs["tree_reaped"] = False
    elif case == "source_pin": launcher.G07_SHARED_SOURCE.write_text("{}", encoding="utf-8")
    elif case == "restored":
        target.write_bytes(b"changed-and-restored during simulated run")
        target.write_bytes(original)
    result = launcher.postcheck_shared_dependencies(contract, before, **kwargs)
    expected = ("INTEGRITY_POSTCHECK_UNVERIFIED" if case in {"permission", "deadline", "tree_unreaped"}
        else "PASSED" if case in {"unchanged", "restored"} else "INTEGRITY_FAIL")
    assert result["status"] == expected
    # Equal end hashes cannot detect the simulated intermediate mutation.
    if case == "restored":
        _write(tmp_path / "RESTORE_RESIDUAL.json", {"end_hash_matches": True,
            "intermediate_mutation_detectable_by_start_end_hash": False, "os_readonly_proved": False})


@pytest.mark.parametrize("case", ["normal", "exit7", "watchdog", "post_permission", "supervisor_error", "close_error", "postcheck_exception", "receipt_error"])
def test_g07_supervisor_always_records_independent_postcheck(launcher, tmp_path, monkeypatch, case):
    contract, path, _, model = _rag_contract(launcher, tmp_path, monkeypatch)
    contract.update(_contract_path=str(path), _contract_sha256=_sha(path))
    contract["watchdog"] = {"observation_seconds": .01 if case == "watchdog" else 2,
        "forensic_seconds": 1, "reap_seconds": 1}
    # Environment validation has separate real fresh-process cases. This
    # integration case isolates the real shared-integrity + finally control flow.
    monkeypatch.setattr(launcher, "validate_environment_preflight", lambda *a, **k: {"status": "PASSED"}, raising=False)
    state = {"exit": 7 if case in {"exit7", "close_error", "postcheck_exception", "receipt_error"} else 0,
        "terminated": False, "started": 0, "postcheck": False}
    target = model / "weights.bin"
    old_sha = launcher.sha256_file
    def observed_sha(p):
        if case == "post_permission" and state["postcheck"] and Path(p).resolve() == target.resolve():
            raise PermissionError("controlled postcheck only")
        return old_sha(p)
    monkeypatch.setattr(launcher, "sha256_file", observed_sha)
    if case == "postcheck_exception":
        monkeypatch.setattr(launcher, "postcheck_shared_dependencies",
            lambda *a, **k: (_ for _ in ()).throw(RuntimeError("controlled secondary postcheck failure")))
    if case == "receipt_error":
        original_write = launcher._write_exclusive
        def refused_receipt(path, value):
            if Path(path).name == "DEPENDENCY_INTEGRITY_POSTCHECK.json":
                raise OSError("controlled independent receipt failure")
            return original_write(path, value)
        monkeypatch.setattr(launcher, "_write_exclusive", refused_receipt)
    class FakeJob:
        process_handle = object()
        events = []
        def start(self, argv, *, before_resume, **kwargs):
            state["started"] += 1
            identity = launcher._process_identity(os.getpid())
            before_resume(identity)
            if case != "post_permission": target.write_bytes(b"simulated stopped worker changed dependency")
            if case == "supervisor_error": raise RuntimeError("original controlled supervisor failure")
            return identity
        def poll(self):
            state["postcheck"] = True
            return None if case == "watchdog" and not state["terminated"] else state["exit"]
        def process_ids(self): return []
        def terminate(self, code): state.update(terminated=True, exit=code)
        def wait(self, timeout): return state["exit"]
        def wait_tree_empty(self, timeout): return []
        def close(self):
            if case == "close_error": raise OSError("controlled Job close failure")
    monkeypatch.setattr(launcher, "WindowsJob", FakeJob)
    code = launcher.supervise(contract, tmp_path / "NO_AUTHORIZATION.json")
    audit = Path(contract["prep_root"]) / "audit"
    receipt_path = audit / "DEPENDENCY_INTEGRITY_POSTCHECK.json"
    final = json.loads((audit / "SUPERVISOR_EXIT.json").read_text())
    if case == "receipt_error":
        assert not receipt_path.exists()
        post = final["dependency_integrity"]
        assert post["receipt_error_type"] == "OSError"
    else:
        assert receipt_path.is_file(), "Supervisor exited without independent dependency postcheck"
        post = json.loads(receipt_path.read_text())
    assert post["status"] == ("INTEGRITY_POSTCHECK_UNVERIFIED" if case in {
        "post_permission", "close_error", "postcheck_exception", "receipt_error"} else "INTEGRITY_FAIL")
    assert final["automatic_rerun"] is False
    if case in {"exit7", "close_error", "postcheck_exception", "receipt_error"}:
        assert final["exit_code"] == code == 7
        if case == "close_error": assert final["job_close_error"] == "OSError"
    elif case == "watchdog": assert final["exit_code"] == code == 124 and final["forced"] is True
    elif case == "supervisor_error": assert final["error"]["type"] == "RuntimeError" and code != 0
    else: assert final["exit_code"] == 0 and code == 1
    assert state["started"] == 1
    assert not list(Path(contract["output_root"]).iterdir())


def test_g07_supervisor_preflight_drift_stops_before_job_resume(launcher, tmp_path, monkeypatch):
    contract, path, _, _ = _rag_contract(launcher, tmp_path, monkeypatch)
    contract.update(_contract_path=str(path), _contract_sha256=_sha(path))
    source = json.loads(launcher.G07_SHARED_SOURCE.read_text())
    target = Path(next(row["path"] for row in source["files"] if row["role"] == "reranker"))
    target.write_bytes(b"changed before any worker")
    contract["input_sha256"][str(target)] = _sha(target)  # independent source still wins
    starts = []
    monkeypatch.setattr(launcher.WindowsJob, "start", lambda *a, **k: starts.append(True))
    with pytest.raises(launcher.LaunchContractError):
        launcher.supervise(contract, tmp_path / "NO_AUTHORIZATION.json")
    assert starts == []
    assert not list(Path(contract["output_root"]).iterdir())



@pytest.mark.parametrize("name", ["PROMPT_ROOT", "DEFAULT_SCHEME_PROMPT_ID", "MODEL_CAPABILITIES_PATH",
    "RAG_STATIC_RETRIEVAL_SPEC_FILE", "RAG_INTENT_POLICY_FILE", "RAG_RETRIEVAL_GATE_POLICY_FILE",
    "SECTION_MAX_CONCURRENCY", "ENABLE_SEMANTIC_GATE", "DEEPSEEK_PROVIDER_MODEL_NAME",
    "HTTP_PROXY", "https_proxy", "ALL_PROXY", "SSL_CERT_FILE", "SSL_CERT_DIR",
    "REQUESTS_CA_BUNDLE", "CURL_CA_BUNDLE", "OPENAI_BASE_URL", "HF_ENDPOINT",
    "PYTHONPATH", "PYTHONHOME", "NUMBA_CACHE_DIR", "TRITON_CACHE_DIR", "CUDA_CACHE_PATH"])
def test_g07_parent_override_refused_before_effective_use(launcher, tmp_path, name):
    contract, _ = _contract(tmp_path)
    with pytest.raises(launcher.LaunchContractError) as caught:
        launcher.build_child_environment(contract, {name: str(tmp_path / "not-approved")})
    assert FAKE_KEY not in str(caught.value)
    assert not (tmp_path / "not-approved").exists()
    assert not list(Path(contract["output_root"]).iterdir())


def test_g07_environment_positive_exact_baseline_and_unknown_parent_not_inherited(launcher, tmp_path):
    contract, _ = _contract(tmp_path)
    environment = launcher.build_child_environment(contract)
    receipt = launcher.validate_environment_preflight(contract, environment)
    assert receipt["credential_presence"] == {"DEEPSEEK_API_KEY": True}
    assert "DEEPSEEK_API_KEY" not in receipt["environment"]
    assert FAKE_KEY not in json.dumps(receipt)
    pure = launcher.build_child_environment(contract, {"UNAPPROVED_UNKNOWN_DYNAMIC_PATH": str(tmp_path),
        "LANGSMITH_TRACING": "true", "LANGCHAIN_TRACING_V2": "true"})
    assert "UNAPPROVED_UNKNOWN_DYNAMIC_PATH" not in pure
    assert pure["LANGSMITH_TRACING"] == pure["LANGCHAIN_TRACING_V2"] == "false"
    assert pure["TMPDIR"] == pure["TEMP"] == pure["TMP"] == str(Path(contract["prep_root"]) / "tmp")


@pytest.mark.parametrize("case", ["unbound", "extra_field", "extra_consumer", "source_sha",
    "input_sha", "prompt", "concurrency", "environment", "credential", "cache_missing", "credential_type", "option_type"])
def test_g07_environment_baseline_cannot_self_authorize_drift(launcher, tmp_path, case):
    contract, _ = _contract(tmp_path)
    path = Path(contract["prep_root"]) / launcher.ENVIRONMENT_BASELINE_RELATIVE
    baseline = json.loads(path.read_text())
    if case == "unbound": contract["input_sha256"].pop(str(path))
    elif case == "extra_field": baseline["authorized"] = True
    elif case == "extra_consumer": baseline["source_sha256"][str(tmp_path / "unknown.py")] = "0" * 64
    elif case == "source_sha":
        name = next(iter(baseline["source_sha256"]))
        baseline["source_sha256"][name] = contract["source_sha256"][name] = "0" * 64
    elif case == "input_sha":
        name = next(iter(baseline["input_sha256"]))
        baseline["input_sha256"][name] = contract["input_sha256"][name] = "0" * 64
    elif case == "prompt": baseline["app_settings"]["prompt_root"] = str(tmp_path)
    elif case == "concurrency": baseline["runtime_options"]["section_max_concurrency"] = 5
    elif case == "environment": baseline["environment"]["PROMPT_ROOT"] = str(tmp_path)
    elif case == "credential": baseline["credential_presence"]["DEEPSEEK_API_KEY"] = False
    elif case == "credential_type": baseline["credential_presence"]["DEEPSEEK_API_KEY"] = 1
    elif case == "option_type": baseline["runtime_options"]["section_max_concurrency"] = True
    elif case == "cache_missing":
        (Path(contract["prep_root"]) / "cache" / launcher.CACHE_VARIABLES[0].lower()).rmdir()
    _write(path, baseline)
    if case != "unbound": contract["input_sha256"][str(path)] = _sha(path)
    with pytest.raises((launcher.LaunchContractError, OSError)):
        launcher.validate_environment_preflight(contract)
    assert not list(Path(contract["output_root"]).iterdir())


@pytest.mark.parametrize("case", ["regular", "empty", "dangling", "permission"])
def test_g07_any_shared_dotenv_object_refused_without_content_read(launcher, tmp_path, monkeypatch, case):
    contract, _ = _contract(tmp_path)
    path = launcher.DOTENV_PATH
    if case in {"regular", "empty"}:
        path.write_text("PROMPT_ROOT=never-selected" if case == "regular" else "", encoding="utf-8")
    elif case == "dangling": path.symlink_to(tmp_path / "absent-dotenv-target")
    else:
        old = Path.lstat
        def denied(candidate, *a, **k):
            if candidate == path: raise PermissionError("controlled lstat denial")
            return old(candidate, *a, **k)
        monkeypatch.setattr(Path, "lstat", denied)
    old_open = Path.open
    reads = []
    def observed(candidate, mode="r", *a, **k):
        if candidate == path and "r" in mode:
            reads.append(True)
            raise AssertionError("DOTENV_CONTENT_MUST_NOT_BE_READ")
        return old_open(candidate, mode, *a, **k)
    monkeypatch.setattr(Path, "open", observed)
    # The approved process policy permits any shared dotenv object without
    # consulting its metadata or content. The exact-path read guard remains.
    receipt = launcher.validate_environment_preflight(contract)
    assert receipt["environment"]["RAG_CONFIG_SOURCE_POLICY"] == "process_env_defaults_v1"
    assert reads == []
    assert not list(Path(contract["output_root"]).iterdir())


@pytest.mark.parametrize("case", ["actual_config", "cached_config", "dotenv_appears", "missing_delta", "wrong_delta", "extra_delta", "settings_type"])
def test_g07_fresh_configuration_boundary_and_dotenv_read_guard(launcher, tmp_path, case):
    contract, _ = _contract(tmp_path)
    if case == "settings_type":
        baseline_path = Path(contract["prep_root"]) / launcher.ENVIRONMENT_BASELINE_RELATIVE
        baseline = json.loads(baseline_path.read_text())
        baseline["app_settings"]["deepseek_api_key_configured"] = 1
        _write(baseline_path, baseline)
        contract["input_sha256"][str(baseline_path)] = _sha(baseline_path)
    body = '''
contract = json.loads(CONTRACT)
events = ["FakeH7/native_done"]
assert "core.config" not in sys.modules
import ast
assignments = {node.targets[0].id: node.value for node in ast.parse(launcher.WORKER.read_text(encoding="utf-8")).body
    if isinstance(node, ast.Assign) and len(node.targets) == 1 and isinstance(node.targets[0], ast.Name)}
native_manifest = pathlib.Path(ast.literal_eval(assignments["NATIVE_MANIFEST"].args[0]))
assert launcher.sha256_file(native_manifest) == ast.literal_eval(assignments["NATIVE_MANIFEST_SHA256"])
overlay = pathlib.Path(json.loads(native_manifest.read_text(encoding="utf-8"))["overlay_root"]).resolve()
if CASE not in {"dotenv_appears", "missing_delta"}:
    os.environ["PYTHONPATH"] = str(overlay)
if CASE == "wrong_delta": os.environ["PYTHONPATH"] = str(SANDBOX)
if CASE == "extra_delta": os.environ["PYTHONPATH"] += os.pathsep + str(SANDBOX)
if CASE in {"missing_delta", "wrong_delta", "extra_delta", "settings_type"}:
    try:
        launcher.runtime_configuration_precheck(contract)
    except launcher.LaunchContractError:
        pass
    else:
        raise AssertionError("Unknown or missing post-H7 loader delta was accepted")
    if CASE != "settings_type": assert "core.config" not in sys.modules
elif CASE == "cached_config":
    import core.config
    try:
        launcher.runtime_configuration_precheck(contract)
    except launcher.LaunchContractError:
        pass
    else:
        raise AssertionError("Pre-H7 cached configuration was accepted")
elif CASE == "dotenv_appears":
    launcher.validate_environment_preflight(contract, environment=os.environ, worker_stage=True)
    source = SANDBOX / "appearing-nonsecret-input"
    source.write_text("PROMPT_ROOT=never-read", encoding="utf-8")
    source.rename(launcher.DOTENV_PATH)
    try:
        launcher.DOTENV_PATH.read_text(encoding="utf-8")
    except launcher.LaunchContractError:
        pass
    else:
        raise AssertionError("New dotenv read escaped the pre-import guard")
else:
    result = launcher.runtime_configuration_precheck(contract)
    assert result["application_config"] == "ACTUAL_GET_SETTINGS_RELOAD_AND_CACHE_VERIFIED"
    assert result["credential_values_persisted"] is False
    assert result["post_h7_environment_delta"]["PYTHONPATH"] == str(overlay)
    from core.config import get_settings
    assert get_settings().as_dict() == result["settings"]
    assert result["options"]["section_max_concurrency"] == 1
    assert "model_gateway" not in sys.modules and "torch" not in sys.modules
    assert events == ["FakeH7/native_done"]
(SANDBOX / "CONFIGURATION_BOUNDARY.json").write_text(json.dumps({"case": CASE,
    "actual_config_readers": CASE == "actual_config", "fake_h7_only": True,
    "real_worker_calls": 0, "model_initializers": 0, "secret_values_persisted": False}))
'''.replace("CONTRACT", repr(json.dumps(contract))).replace("CASE", repr(case))
    _fresh_python(launcher, tmp_path, body, contract=contract)


def test_g07_fake_dotenv_same_key_empty_fallback_and_reload_semantics(launcher, tmp_path):
    contract, _ = _contract(tmp_path)
    body = '''
from unittest.mock import patch
virtual = {"text": "PROMPT_ROOT=" + str(SANDBOX / "dotenv-prompts"), "reads": 0}
original_read = pathlib.Path.read_text
original_exists = pathlib.Path.exists
def virtual_read(path, *a, **k):
    if path.absolute() == ROOT / ".env":
        virtual["reads"] += 1
        return virtual["text"]
    return original_read(path, *a, **k)
def virtual_exists(path):
    return True if path.absolute() == ROOT / ".env" else original_exists(path)
os.environ["PROMPT_ROOT"] = str(ROOT / "prompts")
os.environ["RAG_CONFIG_SOURCE_POLICY"] = "legacy_dotenv_v1"
with patch.object(pathlib.Path, "read_text", virtual_read), patch.object(pathlib.Path, "exists", virtual_exists):
    from core import config
    from bootstrap.runtime_options import RuntimeOptions
    first = config.get_settings(reload=True)
    assert first.prompt_root == ROOT / "prompts"  # same key OS wins Fake dotenv
    os.environ["PROMPT_ROOT"] = ""
    fallback = config.get_settings(reload=True)
    assert fallback.prompt_root == SANDBOX / "dotenv-prompts"
    virtual["text"] = "PROMPT_ROOT=" + str(SANDBOX / "changed-dotenv")
    assert config.get_settings() is fallback
    assert config.get_settings(reload=True).prompt_root == fallback.prompt_root
    os.environ["PROMPT_ROOT"] = str(ROOT / "prompts")
    reset = config.get_settings(reload=True)
    assert reset.prompt_root == ROOT / "prompts"
    assert virtual["reads"] == 1
    os.environ["RAG_INTENT_POLICY_FILE"] = str(SANDBOX / "first-intent.yaml")
    one = RuntimeOptions.resolve(reset, ROOT)
    os.environ["RAG_INTENT_POLICY_FILE"] = str(SANDBOX / "second-intent.yaml")
    two = RuntimeOptions.resolve(reset, ROOT)
    assert one.rag_intent_policy_file != two.rag_intent_policy_file
(SANDBOX / "FAKE_DOTENV_CONFIG_SEMANTICS.json").write_text(json.dumps({
    "same_key_os_wins": True, "empty_falls_back": True, "dotenv_parse_count": virtual["reads"],
    "settings_cache_and_reload_verified": True, "runtime_options_each_resolve_verified": True,
    "real_dotenv_content_reads": 0, "actual_environment_preflight_accepted_dotenv": False}))
'''
    _fresh_python(launcher, tmp_path, body, contract=contract)



@pytest.mark.parametrize("case", ["allow_reordered", "grant_changed", "owner_changed", "protected_changed", "deny_changed"])
def test_g07_acl_observation_compares_full_semantics_without_modification(launcher, tmp_path, monkeypatch, case):
    import re
    import win32security
    contract, _, _, model = _rag_contract(launcher, tmp_path, monkeypatch)
    target = model / "weights.bin"
    flags = (win32security.OWNER_SECURITY_INFORMATION | win32security.GROUP_SECURITY_INFORMATION
        | win32security.DACL_SECURITY_INFORMATION)
    descriptor = win32security.GetNamedSecurityInfo(str(target), win32security.SE_FILE_OBJECT, flags)
    original = win32security.ConvertSecurityDescriptorToStringSecurityDescriptor(descriptor,
        win32security.SDDL_REVISION_1, flags)
    dacl = original.index("D:")
    prefix = original[:dacl]
    header = original[dacl:original.index("(", dacl)]
    aces = re.findall(r"\([^()]*\)", original[dacl:])
    assert len(aces) > 1 and all(ace.startswith("(A;") for ace in aces)
    if case == "allow_reordered": expected = prefix + header + "".join(reversed(aces))
    elif case == "grant_changed":
        fields = aces[0][1:-1].split(";")
        fields[2] = "FR" if fields[2] != "FR" else "FW"
        expected = prefix + header + "(" + ";".join(fields) + ")" + "".join(aces[1:])
    elif case == "owner_changed":
        expected = re.sub(r"^O:[^G]+(?=G:)", "O:SY", original)
        assert expected != original
    elif case == "protected_changed":
        expected = prefix + (header.replace("P", "") if "P" in header else "D:P" + header[2:]) + "".join(aces)
    else: expected = original.replace("(A;", "(D;", 1)
    source = json.loads(launcher.G07_SHARED_SOURCE.read_text())
    source["acl_classes"] = {"TEST_CLASS": {"sddl": expected}}
    source["acl_objects"] = [{"path": str(target), "acl_class": "TEST_CLASS",
        "sddl_sha256": hashlib.sha256(expected.encode()).hexdigest()}]
    _write(launcher.G07_SHARED_SOURCE, source)
    monkeypatch.setattr(launcher, "G07_SHARED_SOURCE_SHA256", _sha(launcher.G07_SHARED_SOURCE))
    contract["input_sha256"][str(launcher.G07_SHARED_SOURCE)] = _sha(launcher.G07_SHARED_SOURCE)
    if case == "allow_reordered":
        assert launcher.validate_shared_dependencies(contract)["status"] == "PASSED"
    else:
        with pytest.raises(launcher.LaunchContractError): launcher.validate_shared_dependencies(contract)
    after = win32security.ConvertSecurityDescriptorToStringSecurityDescriptor(
        win32security.GetNamedSecurityInfo(str(target), win32security.SE_FILE_OBJECT, flags),
        win32security.SDDL_REVISION_1, flags)
    assert after == original  # Only the independent test source expectation changed.



@pytest.mark.parametrize("kind", ["input_sha256", "source_sha256"])
def test_g07_dotenv_rejected_before_generic_manifest_reads(launcher, tmp_path, monkeypatch, kind):
    contract, path = _contract(tmp_path)
    dotenv = launcher.DOTENV_PATH
    dotenv.write_text("PROMPT_ROOT=controlled-nonsecret-never-read", encoding="utf-8")
    contract[kind][str(dotenv)] = _sha(dotenv)
    _write(path, contract)
    reads = []
    original_open = Path.open
    def observed(candidate, mode="r", *a, **k):
        if candidate.absolute() == dotenv.absolute() and "r" in mode:
            reads.append(True)
        return original_open(candidate, mode, *a, **k)
    monkeypatch.setattr(Path, "open", observed)
    with pytest.raises(launcher.LaunchContractError):
        launcher.load_and_validate_launch_contract(path, require_authorization=False)
    assert reads == [], "Generic manifest SHA read the refused dotenv before environment preflight"
    assert not list(Path(contract["output_root"]).iterdir())


@pytest.mark.parametrize("policy", ["default", "legacy_dotenv_v1", "process_env_defaults_v1"])
def test_source_policy_fresh_import_reader_count_and_precedence(launcher, tmp_path, policy):
    contract, _ = _contract(tmp_path)
    body = '''
from unittest.mock import patch
selected = SELECTED_POLICY
if selected == "default":
    os.environ.pop("RAG_CONFIG_SOURCE_POLICY", None)
else:
    os.environ["RAG_CONFIG_SOURCE_POLICY"] = selected
expected = "legacy_dotenv_v1" if selected == "default" else selected
fake = {"reads": 0, "metadata": 0, "reader_calls": 0}
fake_prompt = SANDBOX / "fake-dotenv-prompts"
original_read, original_exists = pathlib.Path.read_text, pathlib.Path.exists
def virtual_read(path, *args, **kwargs):
    if path.absolute() == ROOT / ".env":
        fake["reads"] += 1
        return "PROMPT_ROOT=" + str(fake_prompt) + "\\nPOLICY_STRICT_EXAMPLE=fake-dotenv-value"
    return original_read(path, *args, **kwargs)
def virtual_exists(path):
    if path.absolute() == ROOT / ".env":
        fake["metadata"] += 1
        return True
    return original_exists(path)
def profile(frame, event, arg):
    if event == "call" and frame.f_code.co_name == "_read_dotenv":
        if pathlib.Path(frame.f_code.co_filename).resolve() == ROOT / "backend/core/config.py":
            fake["reader_calls"] += 1
os.environ["PROMPT_ROOT"] = str(ROOT / "prompts")
with patch.object(pathlib.Path, "read_text", virtual_read), patch.object(pathlib.Path, "exists", virtual_exists):
    sys.setprofile(profile)
    try:
        from core import config
        assert config.CONFIG_SOURCE_POLICY == expected
        assert config.CONFIG_SOURCE_POLICY_ENV == "RAG_CONFIG_SOURCE_POLICY"
        first = config.get_settings(reload=True)
        assert first.prompt_root == ROOT / "prompts"
        os.environ["PROMPT_ROOT"] = ""
        assert config.get_settings() is first
        second = config.get_settings(reload=True)
        assert second.prompt_root == (ROOT / "prompts" if expected == "process_env_defaults_v1" else fake_prompt)
        os.environ["POLICY_STRICT_EXAMPLE"] = ""
        assert config._get_explicit_env("POLICY_STRICT_EXAMPLE", "default") == ""
        os.environ.pop("POLICY_STRICT_EXAMPLE")
        assert config._get_explicit_env("POLICY_STRICT_EXAMPLE", "default") == (
            "default" if expected == "process_env_defaults_v1" else "fake-dotenv-value")
        assert fake["reader_calls"] == fake["reads"] == fake["metadata"] == (
            0 if expected == "process_env_defaults_v1" else 1)
        if expected == "process_env_defaults_v1":
            import importlib
            importlib.reload(config)
            assert config.CONFIG_SOURCE_POLICY == expected
            assert config._DOTENV_VALUES == {}
            config.get_settings(reload=True)
            assert fake["reader_calls"] == fake["reads"] == fake["metadata"] == 0
    finally:
        sys.setprofile(None)
(SANDBOX / "SOURCE_POLICY_IMPORT.json").write_text(json.dumps({
    "selected": selected, "effective": expected, **fake,
    "network_requests": 0, "real_dotenv_content_reads": 0}), encoding="utf-8")
'''.replace("SELECTED_POLICY", repr(policy))
    _fresh_python(launcher, tmp_path, body, contract=contract, hide_shared_dotenv=False)
    observed = json.loads((tmp_path / "SOURCE_POLICY_IMPORT.json").read_text())
    assert observed["reader_calls"] == (0 if policy == "process_env_defaults_v1" else 1)
    assert not list(Path(contract["output_root"]).iterdir())


@pytest.mark.parametrize("policy", ["", "unknown-policy", "PROCESS_ENV_DEFAULTS_V1", "process_env_defaults_v1 "])
def test_source_policy_unknown_first_import_fails_before_dotenv(launcher, tmp_path, policy):
    contract, _ = _contract(tmp_path)
    body = '''
os.environ["RAG_CONFIG_SOURCE_POLICY"] = SELECTED_POLICY
reader_calls = []
def profile(frame, event, arg):
    if event == "call" and frame.f_code.co_name == "_read_dotenv":
        reader_calls.append(True)
sys.setprofile(profile)
try:
    try:
        import core.config
    except ValueError:
        pass
    else:
        raise AssertionError("Unknown source policy was accepted")
finally:
    sys.setprofile(None)
assert reader_calls == []
assert "core.config" not in sys.modules
assert "application.mainline_service" not in sys.modules
(SANDBOX / "UNKNOWN_POLICY.json").write_text(json.dumps({
    "rejected_before_dotenv_reader": True, "real_provider_calls": 0}), encoding="utf-8")
'''.replace("SELECTED_POLICY", repr(policy))
    _fresh_python(launcher, tmp_path, body, contract=contract, hide_shared_dotenv=False)


@pytest.mark.parametrize("initial, changed", [
    ("legacy_dotenv_v1", "process_env_defaults_v1"),
    ("process_env_defaults_v1", "legacy_dotenv_v1"),
    ("process_env_defaults_v1", ""),
    ("process_env_defaults_v1", "unknown-policy"),
    ("process_env_defaults_v1", None),
    ("legacy_dotenv_v1", "unknown-policy"),
])
@pytest.mark.parametrize("reload_settings", [False, True])
def test_source_policy_drift_refused_even_for_cached_settings(launcher, tmp_path, initial, changed, reload_settings):
    contract, _ = _contract(tmp_path)
    body = '''
os.environ["RAG_CONFIG_SOURCE_POLICY"] = INITIAL_POLICY
from core import config
settings = config.get_settings(reload=True)
if CHANGED_POLICY is None:
    os.environ.pop("RAG_CONFIG_SOURCE_POLICY", None)
else:
    os.environ["RAG_CONFIG_SOURCE_POLICY"] = CHANGED_POLICY
try:
    config.get_settings(reload=RELOAD_SETTINGS)
except ValueError:
    pass
else:
    raise AssertionError("Changed import-time configuration authority was accepted")
assert config._SETTINGS is settings
assert "application.mainline_service" not in sys.modules
(SANDBOX / "SOURCE_POLICY_DRIFT.json").write_text(json.dumps({
    "cached_or_reload_drift_rejected": True, "reload": RELOAD_SETTINGS}), encoding="utf-8")
'''.replace("INITIAL_POLICY", repr(initial)).replace("CHANGED_POLICY", repr(changed)).replace("RELOAD_SETTINGS", repr(reload_settings))
    _fresh_python(launcher, tmp_path, body, contract=contract)


@pytest.mark.parametrize("policy", ["legacy_dotenv_v1", "unknown-policy", ""])
def test_source_policy_launcher_refuses_parent_override(launcher, tmp_path, policy):
    contract, _ = _contract(tmp_path)
    with pytest.raises(launcher.LaunchContractError):
        launcher.build_child_environment(contract, {"RAG_CONFIG_SOURCE_POLICY": policy})
    assert not list(Path(contract["output_root"]).iterdir())


@pytest.mark.parametrize("alias", ["core.config", "backend.core.config"])
def test_source_policy_worker_rejects_both_preimported_config_aliases(launcher, tmp_path, alias):
    contract, _ = _contract(tmp_path)
    body = '''
contract = json.loads(CONTRACT)
sys.path.insert(0, str(ROOT))
import importlib
config = importlib.import_module(CONFIG_ALIAS)
assert config.CONFIG_SOURCE_POLICY == "process_env_defaults_v1"
try:
    launcher.validate_environment_preflight(contract, environment=os.environ, worker_stage=True)
except launcher.LaunchContractError:
    pass
else:
    raise AssertionError("Preloaded config alias passed the fresh Worker boundary")
assert "application.mainline_service" not in sys.modules
assert "model_gateway" not in sys.modules
(SANDBOX / "PRELOADED_ALIAS.json").write_text(json.dumps({
    "alias": CONFIG_ALIAS, "preimport_rejected": True}), encoding="utf-8")
'''.replace("CONTRACT", repr(json.dumps(contract))).replace("CONFIG_ALIAS", repr(alias))
    _fresh_python(launcher, tmp_path, body, contract=contract, hide_shared_dotenv=False)


@pytest.mark.parametrize("credential", [None, ""])
def test_source_policy_missing_process_credentials_fail_before_config_import(launcher, tmp_path, credential):
    contract, _ = _contract(tmp_path)
    body = '''
contract = json.loads(CONTRACT)
if CREDENTIAL is None:
    os.environ.pop("DEEPSEEK_API_KEY", None)
else:
    os.environ["DEEPSEEK_API_KEY"] = CREDENTIAL
try:
    launcher.validate_environment_preflight(contract, environment=os.environ, worker_stage=True)
except launcher.LaunchContractError:
    pass
else:
    raise AssertionError("Missing required process credential was accepted")
assert "core.config" not in sys.modules and "backend.core.config" not in sys.modules
assert "application.mainline_service" not in sys.modules
(SANDBOX / "MISSING_PROCESS_CREDENTIAL.json").write_text(json.dumps({
    "missing_credential_rejected": True, "config_imports": 0}), encoding="utf-8")
'''.replace("CONTRACT", repr(json.dumps(contract))).replace("CREDENTIAL", repr(credential))
    _fresh_python(launcher, tmp_path, body, contract=contract, hide_shared_dotenv=False)


@pytest.mark.parametrize("reader", ["path", "builtin", "os_open"])
def test_source_policy_exact_dotenv_read_deny_covers_low_level_readers(launcher, tmp_path, reader):
    contract, _ = _contract(tmp_path)
    body = '''
contract = json.loads(CONTRACT)
# DOTENV_PATH is the controlled sandbox authority, never the shared .env.
launcher.DOTENV_PATH.write_text("controlled-nonsecret-never-read", encoding="utf-8")
launcher.validate_environment_preflight(contract, environment=os.environ, worker_stage=True)
try:
    if READER == "path":
        launcher.DOTENV_PATH.read_bytes()
    elif READER == "builtin":
        with builtins.open(launcher.DOTENV_PATH, "rb") as handle:
            raise AssertionError("Unexpected builtin read reached an open handle")
    else:
        handle = os.open(launcher.DOTENV_PATH, os.O_RDONLY)
        os.close(handle)
except launcher.LaunchContractError:
    pass
else:
    raise AssertionError("Exact-path dotenv read escaped the Worker guard")
assert "core.config" not in sys.modules and "backend.core.config" not in sys.modules
(SANDBOX / "EXACT_DOTENV_READ_DENY.json").write_text(json.dumps({
    "reader": READER, "read_denied": True, "real_dotenv_content_reads": 0}), encoding="utf-8")
'''.replace("CONTRACT", repr(json.dumps(contract))).replace("READER", repr(reader))
    _fresh_python(launcher, tmp_path, body, contract=contract, hide_shared_dotenv=False)


# R03 Strict V3: additive offline coverage of the narrow prospective projection.
# Existing test bodies/helpers above remain the exact pre-implementation bytes.
def _strict_v3_contract(launcher, tmp_path):
    contract, path = _contract(tmp_path)
    projection = contract["projection"]
    projection["project_projection_profile"] = launcher.STRICT_V3_PROJECT_PROJECTION
    projection["allowed_project_paths"] = ["task_id", launcher.STRICT_V3_PROFILE_PATH]
    project = json.loads(Path(projection["project"]).read_text(encoding="utf-8"))
    project["generation_requirements"]["extra"]["verifier_decision_profile"] = launcher.STRICT_V3_PROFILE
    _strict_v3_rebind_json(contract, projection["project"], project)
    _write(path, contract)
    return contract, path


def _strict_v3_rebind_json(contract, path, value):
    _write(path, value)
    contract["input_sha256"][str(path)] = _sha(path)


def _strict_v3_assert_no_execution(contract):
    assert not list(Path(contract["output_root"]).iterdir())
    assert not list(Path(contract["prep_root"]).rglob("LAUNCH_INTENT.json"))
    assert not list(Path(contract["prep_root"]).rglob("WORKER_BINDING.json"))


@pytest.mark.parametrize("case", ["task_only", "profile_without_marker", "strict_paths_without_marker"])
def test_r03_strict_v3_legacy_contract_requires_explicit_projection(launcher, tmp_path, case):
    contract, path = _contract(tmp_path)
    if case != "task_only":
        project_path = contract["projection"]["project"]
        project = json.loads(Path(project_path).read_text(encoding="utf-8"))
        project["generation_requirements"]["extra"]["verifier_decision_profile"] = launcher.STRICT_V3_PROFILE
        _strict_v3_rebind_json(contract, project_path, project)
        if case == "strict_paths_without_marker":
            contract["projection"]["allowed_project_paths"].append(launcher.STRICT_V3_PROFILE_PATH)
        _write(path, contract)
    if case == "task_only":
        assert launcher.load_and_validate_launch_contract(path, require_authorization=False)["task_id"] == TASK
    else:
        with pytest.raises(launcher.LaunchContractError):
            launcher.load_and_validate_launch_contract(path, require_authorization=False)
    _strict_v3_assert_no_execution(contract)


@pytest.mark.parametrize("reverse_paths", [False, True])
def test_r03_strict_v3_exact_projection_accepts_only_two_differences(launcher, tmp_path, reverse_paths):
    contract, path = _strict_v3_contract(launcher, tmp_path)
    projection = contract["projection"]
    if reverse_paths:
        projection["allowed_project_paths"].reverse()
        _write(path, contract)
    original = json.loads(Path(projection["original_project"]).read_text(encoding="utf-8-sig"))
    project = json.loads(Path(projection["project"]).read_text(encoding="utf-8"))
    assert _sha(projection["original_project"]) == launcher.ORIGINAL_PROJECT_SHA256
    assert original["task_id"] != project["task_id"] == TASK
    assert original["generation_requirements"]["extra"] == {}
    stripped = copy.deepcopy(project)
    stripped["task_id"] = original["task_id"]
    assert stripped["generation_requirements"]["extra"].pop("verifier_decision_profile") == "claim-complete-support-v1"
    assert json.dumps(stripped, sort_keys=True) == json.dumps(original, sort_keys=True)
    result = launcher.load_and_validate_launch_contract(path, require_authorization=False)
    assert result["projection"]["project_projection_profile"] == "r03-strict-v3-project-projection-v1"
    assert result["task_id"] == TASK and result["run_id"] == RUN
    assert result["source_sha256"][str(LAUNCHER)] == _sha(LAUNCHER)
    _strict_v3_assert_no_execution(contract)


@pytest.mark.parametrize("marker", [None, "", "unknown", "R03-STRICT-V3-PROJECT-PROJECTION-V1", True, [], {}])
def test_r03_strict_v3_unknown_or_null_projection_marker_rejected(launcher, tmp_path, marker):
    contract, path = _strict_v3_contract(launcher, tmp_path)
    contract["projection"]["project_projection_profile"] = marker
    _write(path, contract)
    with pytest.raises(launcher.LaunchContractError):
        launcher.load_and_validate_launch_contract(path, require_authorization=False)
    _strict_v3_assert_no_execution(contract)


@pytest.mark.parametrize("value", [
    "missing", "claim-semantic-v4", "unknown", None, "", True, 1, [], {},
    "claim-complete-support-v1 ", "CLAIM-COMPLETE-SUPPORT-V1",
])
def test_r03_strict_v3_profile_missing_or_nonexact_value_rejected(launcher, tmp_path, value):
    contract, path = _strict_v3_contract(launcher, tmp_path)
    project_path = contract["projection"]["project"]
    project = json.loads(Path(project_path).read_text(encoding="utf-8"))
    if value == "missing":
        project["generation_requirements"]["extra"].pop("verifier_decision_profile")
    else:
        project["generation_requirements"]["extra"]["verifier_decision_profile"] = value
    _strict_v3_rebind_json(contract, project_path, project)
    _write(path, contract)
    with pytest.raises(launcher.LaunchContractError):
        launcher.load_and_validate_launch_contract(path, require_authorization=False)
    _strict_v3_assert_no_execution(contract)


@pytest.mark.parametrize("paths", [
    [], ["task_id"], ["generation_requirements.extra.verifier_decision_profile"],
    ["task_id", "task_id"],
    ["task_id", "generation_requirements.extra.verifier_decision_profile", "task_id"],
    ["task_id", "user_query"],
    ["task_id", "generation_requirements.extra"],
    ["task_id", "generation_requirements.extra.verifier_decision_profile", "metadata"],
    ["task_id", None], "task_id", None, {},
])
def test_r03_strict_v3_contract_cannot_expand_or_malformed_project_paths(launcher, tmp_path, paths):
    contract, path = _strict_v3_contract(launcher, tmp_path)
    contract["projection"]["allowed_project_paths"] = paths
    _write(path, contract)
    with pytest.raises(launcher.LaunchContractError):
        launcher.load_and_validate_launch_contract(path, require_authorization=False)
    _strict_v3_assert_no_execution(contract)


@pytest.mark.parametrize("case", [
    "user_query", "business_goal", "scale", "hardware_count", "budget", "section_order",
    "tenant", "rag_source", "authorization_scope", "metadata", "baseline_run_removed",
    "manual_boundary", "source_reference", "extra_key", "other_extra", "output_format",
    "missing_information", "model_generation", "bool_integer", "integer_float", "schema",
    "generation_type", "extra_type", "default_field_added",
])
def test_r03_strict_v3_rebound_input_cannot_change_any_other_business_field(launcher, tmp_path, case):
    contract, path = _strict_v3_contract(launcher, tmp_path)
    project_path = contract["projection"]["project"]
    project = json.loads(Path(project_path).read_text(encoding="utf-8"))
    if case == "user_query": project["user_query"] += " changed"
    elif case == "business_goal": project["business_goal"] = "unapproved goal"
    elif case == "scale": project["total_staff"] = 2400
    elif case == "hardware_count": project["hardware_resources"] = [{"resource_type": "server", "quantity": 24}]
    elif case == "budget": project["extra"]["investment_amount"] = 18000000
    elif case == "section_order":
        project["generation_requirements"]["required_sections"].reverse()
        project["output_schema"]["required_sections"].reverse()
    elif case == "tenant": project["tenant_id"] = "other-tenant"
    elif case == "rag_source": project["source_materials"] = [{"kb_id": "unapproved_kb"}]
    elif case == "authorization_scope": project["extra"]["authorized_kb_ids"] = ["unapproved_kb"]
    elif case == "metadata": project["metadata"]["allow_demo_defaults"] = True
    elif case == "baseline_run_removed": project["metadata"].pop("baseline_run_id")
    elif case == "manual_boundary": project["manual_boundaries"] = ["unapproved boundary"]
    elif case == "source_reference": project["extra"]["source_ref"] = "source://unapproved"
    elif case == "extra_key": project["generation_requirements"]["extra"]["approved"] = True
    elif case == "other_extra": project["extra"]["approved"] = True
    elif case == "output_format": project["output_schema"]["output_format"] = "json"
    elif case == "missing_information": project["missing_information"].pop()
    elif case == "model_generation": project["generation_requirements"]["max_tokens_per_section"] += 1
    elif case == "bool_integer": project["generation_requirements"]["need_human_review"] = 1
    elif case == "integer_float": project["generation_requirements"]["max_section_retries"] = 1.0
    elif case == "schema": project["schema_version"] = "project_input_v2"
    elif case == "generation_type": project["generation_requirements"] = []
    elif case == "extra_type": project["generation_requirements"]["extra"] = None
    else: project["source_materials"] = []  # No schema-default expansion may add a third delta.
    _strict_v3_rebind_json(contract, project_path, project)
    _write(path, contract)
    assert contract["input_sha256"][project_path] == _sha(project_path)
    with pytest.raises(launcher.LaunchContractError):
        launcher.load_and_validate_launch_contract(path, require_authorization=False)
    _strict_v3_assert_no_execution(contract)


@pytest.mark.parametrize("case", ["original_bytes", "original_and_project", "original_manifest_sha", "source_pin"])
def test_r03_strict_v3_original_r01_provenance_cannot_be_forged(launcher, tmp_path, monkeypatch, case):
    contract, path = _strict_v3_contract(launcher, tmp_path)
    original_path = contract["projection"]["original_project"]
    if case == "original_bytes":
        original = Path(original_path).read_bytes()
        Path(original_path).write_bytes(original + b"\n")
        contract["input_sha256"][original_path] = _sha(original_path)
    elif case == "original_and_project":
        for name in ("original_project", "project"):
            target = contract["projection"][name]
            value = json.loads(Path(target).read_text(encoding="utf-8-sig"))
            value["business_goal"] = "forged in both copies"
            _strict_v3_rebind_json(contract, target, value)
    elif case == "original_manifest_sha": contract["input_sha256"][original_path] = "0" * 64
    else: monkeypatch.setattr(launcher, "ORIGINAL_PROJECT_SHA256", "0" * 64)
    _write(path, contract)
    with pytest.raises(launcher.LaunchContractError):
        launcher.load_and_validate_launch_contract(path, require_authorization=False)
    _strict_v3_assert_no_execution(contract)


@pytest.mark.parametrize("case", ["existing_key", "other_extra_changed", "schema", "extra_not_dict"])
def test_r03_strict_v3_original_key_absence_and_other_extra_are_defensive_guards(launcher, tmp_path, monkeypatch, case):
    # Independent test-only authority exercises branches unreachable from the
    # immutable R01 bytes; it does not replace or edit any historical authority.
    original = json.loads((HISTORICAL / "PROJECT_INPUT_V3.json").read_text(encoding="utf-8-sig"))
    if case == "existing_key":
        original["generation_requirements"]["extra"]["verifier_decision_profile"] = launcher.STRICT_V3_PROFILE
    elif case == "other_extra_changed": original["generation_requirements"]["extra"]["retained"] = "original"
    elif case == "schema": original["schema_version"] = "project_input_v2"
    else: original["generation_requirements"]["extra"] = []
    source = _write(tmp_path / "independent-original-project.json", original)
    monkeypatch.setattr(launcher, "ORIGINAL_PROJECT_SHA256", _sha(source))
    projected = copy.deepcopy(original)
    projected["task_id"] = TASK
    projected["schema_version"] = "project_input_v3"
    projected["generation_requirements"]["extra"] = {"verifier_decision_profile": launcher.STRICT_V3_PROFILE}
    if case == "other_extra_changed": projected["generation_requirements"]["extra"]["retained"] = "changed"
    with pytest.raises(launcher.LaunchContractError):
        launcher._validate_strict_v3_project_projection(source, projected,
            ["task_id", launcher.STRICT_V3_PROFILE_PATH], task_id=TASK, run_id=RUN)


@pytest.mark.parametrize("case", [
    "canonical_calls", "canonical_tokens", "elapsed", "generation_checker", "extra_key",
    "profile_in_policy", "policy_ref", "strict_path_in_policy_paths", "policy_paths_extra",
    "policy_paths_missing", "policy_paths_duplicate",
])
def test_r03_strict_v3_policy_remains_identity_only(launcher, tmp_path, case):
    contract, path = _strict_v3_contract(launcher, tmp_path)
    policy_path = contract["projection"]["policy"]
    policy = json.loads(Path(policy_path).read_text(encoding="utf-8"))
    if case == "canonical_calls": policy["canonical_budget"]["logical_model_calls"] += 1
    elif case == "canonical_tokens": policy["canonical_budget"]["model_tokens_total"] += 1
    elif case == "elapsed": policy["canonical_budget"]["max_elapsed_seconds"] += 1
    elif case == "generation_checker": policy["generation_checker"]["enabled"] = False
    elif case == "extra_key": policy["approved"] = True
    elif case == "profile_in_policy":
        policy["generation_requirements"] = {"extra": {"verifier_decision_profile": launcher.STRICT_V3_PROFILE}}
    elif case == "policy_ref": policy["canonical_budget"]["policy_ref"] = "budget-policy://unapproved"
    elif case == "strict_path_in_policy_paths": contract["projection"]["allowed_policy_paths"] = [launcher.STRICT_V3_PROFILE_PATH]
    elif case == "policy_paths_extra": contract["projection"]["allowed_policy_paths"].append("policy_version")
    elif case == "policy_paths_missing": contract["projection"]["allowed_policy_paths"] = []
    else: contract["projection"]["allowed_policy_paths"] *= 2
    _strict_v3_rebind_json(contract, policy_path, policy)
    _write(path, contract)
    with pytest.raises(launcher.LaunchContractError):
        launcher.load_and_validate_launch_contract(path, require_authorization=False)
    _strict_v3_assert_no_execution(contract)


@pytest.mark.parametrize("case", [
    "project_sha", "project_unbound", "launcher_sha", "launcher_unbound", "plan_sha",
    "plan_query", "project_task", "plan_task", "scope_tenant", "scope_kb",
    "rag_manifest", "rag_pointer", "output_nonempty", "path_overlap",
    "task_r02", "run_r02", "physical_budget", "watchdog",
])
def test_r03_strict_v3_existing_freeze_and_safety_guards_still_reject(launcher, tmp_path, case):
    contract, path = _strict_v3_contract(launcher, tmp_path)
    project_path = contract["projection"]["project"]
    project = json.loads(Path(project_path).read_text(encoding="utf-8"))
    plan_path = contract["plan_path"]
    plan = json.loads(Path(plan_path).read_text(encoding="utf-8"))
    if case == "project_sha": contract["input_sha256"][project_path] = "0" * 64
    elif case == "project_unbound": contract["input_sha256"].pop(project_path)
    elif case == "launcher_sha": contract["source_sha256"][str(LAUNCHER)] = "0" * 64
    elif case == "launcher_unbound": contract["source_sha256"].pop(str(LAUNCHER))
    elif case == "plan_sha": contract["plan_sha256"] = "0" * 64
    elif case == "plan_query": plan["user_input"] += " changed"
    elif case == "project_task": project["task_id"] = "task-unbound-projection"
    elif case == "plan_task": plan["task_id"] = "task-unbound-plan"
    elif case == "scope_tenant": plan["scope"]["tenant_id"] = "other-tenant"
    elif case == "scope_kb": plan["scope"]["authorized_kb_ids"].append("unapproved_kb")
    elif case == "rag_manifest":
        manifest = json.loads(Path(contract["rag_manifest_path"]).read_text(encoding="utf-8"))
        manifest["embedding"]["model"] = str(tmp_path)
        _rebind_rag(contract, manifest)
    elif case == "rag_pointer":
        pointer = json.loads(Path(contract["rag_pointer_path"]).read_text(encoding="utf-8"))
        pointer["manifest_sha256"] = "0" * 64
        _strict_v3_rebind_json(contract, contract["rag_pointer_path"], pointer)
    elif case == "output_nonempty": (Path(contract["output_root"]) / "preserve.bin").write_bytes(b"existing-output")
    elif case == "path_overlap": contract["rag_root"] = contract["prep_root"]
    elif case == "task_r02":
        contract["task_id"] = plan["task_id"] = project["task_id"] = "task-rc1-full8-post-phase1-20261021-r02"
    elif case == "run_r02":
        contract["run_id"] = plan["run_id"] = "rc1-full8-post-phase1-20261021-r02"
        policy_path = contract["projection"]["policy"]
        policy = json.loads(Path(policy_path).read_text(encoding="utf-8"))
        policy["canonical_budget"]["policy_ref"] = policy["canonical_budget"]["policy_ref"].replace(RUN, contract["run_id"])
        _strict_v3_rebind_json(contract, policy_path, policy)
    elif case == "physical_budget": contract["physical_attempt_limit"] = 0
    else: contract["watchdog"]["observation_seconds"] = 0
    if case in {"plan_query", "plan_task", "scope_tenant", "scope_kb", "task_r02", "run_r02"}:
        _write(plan_path, plan)
        contract["plan_sha256"] = _sha(plan_path)
    if case in {"project_task", "task_r02"}: _strict_v3_rebind_json(contract, project_path, project)
    _write(path, contract)
    with pytest.raises(launcher.LaunchContractError):
        launcher.load_and_validate_launch_contract(path, require_authorization=False)
    if case == "output_nonempty":
        assert (Path(contract["output_root"]) / "preserve.bin").read_bytes() == b"existing-output"
    else: _strict_v3_assert_no_execution(contract)


@pytest.mark.parametrize("case", ["valid_fixture", "missing", "unbound_sha", "task", "run", "n", "one_run", "remaining_risk"])
def test_r03_strict_v3_separate_authorization_remains_required_and_exact(launcher, tmp_path, case):
    contract, path = _strict_v3_contract(launcher, tmp_path)
    # This is a local offline test document, never formal run authorization.
    authorization = {"authorized": True, "one_run_only": True, "contract_sha256": _sha(path),
        "task_id": TASK, "run_id": RUN, "physical_attempt_limit": contract["physical_attempt_limit"],
        "accept_billing_hard_cap_unverified": True, "accept_e1_limitations": True}
    if case == "unbound_sha": authorization["contract_sha256"] = "0" * 64
    elif case in {"task", "run"}: authorization[case + "_id"] = "unbound-offline-identity"
    elif case == "n": authorization["physical_attempt_limit"] += 1
    elif case == "one_run": authorization["one_run_only"] = False
    elif case == "remaining_risk": authorization["accept_billing_hard_cap_unverified"] = False
    authorization_path = None if case == "missing" else _write(tmp_path / "OFFLINE_FAKE_AUTHORIZATION.json", authorization)
    if case == "valid_fixture":
        assert launcher.load_and_validate_launch_contract(path, authorization_path)["run_id"] == RUN
    else:
        with pytest.raises(launcher.LaunchContractError):
            launcher.load_and_validate_launch_contract(path, authorization_path)
    _strict_v3_assert_no_execution(contract)


def test_r03_strict_v3_type_sensitive_business_comparison_preserves_legacy_rule(launcher, tmp_path):
    original = json.loads((HISTORICAL / "PROJECT_INPUT_V3.json").read_text(encoding="utf-8-sig"))
    projected = copy.deepcopy(original)
    projected["task_id"] = TASK
    projected["generation_requirements"]["need_human_review"] = 1
    # Preserve the old function's existing Python-equality behavior unchanged.
    launcher.validate_input_projection(original, projected, ["task_id"])
    with pytest.raises(launcher.LaunchContractError, match="Unapproved business projection path"):
        launcher.validate_input_projection(original, projected,
            ["task_id", launcher.STRICT_V3_PROFILE_PATH])
    projected["generation_requirements"]["extra"]["verifier_decision_profile"] = launcher.STRICT_V3_PROFILE
    with pytest.raises(launcher.LaunchContractError):
        launcher._validate_strict_v3_project_projection(HISTORICAL / "PROJECT_INPUT_V3.json", projected,
            ["task_id", launcher.STRICT_V3_PROFILE_PATH], task_id=TASK, run_id=RUN)

@pytest.mark.parametrize("case", ["same_path", "conflicting_path", "embedded_object", "null"])
def test_r03_strict_v3_worker_project_input_alias_binds_validated_candidate(launcher, tmp_path, case):
    contract, path = _strict_v3_contract(launcher, tmp_path)
    plan_path = contract["plan_path"]
    plan = json.loads(Path(plan_path).read_text(encoding="utf-8"))
    if case == "same_path": plan["project_input"] = plan["project_input_path"]
    elif case == "conflicting_path":
        alternate = _write(Path(contract["prep_root"]) / "inputs/ALTERNATE_PROJECT.json",
            {"task_id": TASK, "unapproved_business": True})
        contract["input_sha256"][str(alternate)] = _sha(alternate)
        plan["project_input"] = str(alternate)
    elif case == "embedded_object":
        plan["project_input"] = json.loads(Path(plan["project_input_path"]).read_text(encoding="utf-8"))
    else: plan["project_input"] = None
    _write(plan_path, plan)
    contract["plan_sha256"] = _sha(plan_path)
    _write(path, contract)
    if case == "same_path":
        assert launcher.load_and_validate_launch_contract(path, require_authorization=False)["run_id"] == RUN
    else:
        with pytest.raises(launcher.LaunchContractError):
            launcher.load_and_validate_launch_contract(path, require_authorization=False)
    _strict_v3_assert_no_execution(contract)

@pytest.mark.parametrize("case", ["calls_float", "tokens_float", "review_integer", "retrieval_boolean"])
def test_r03_strict_v3_policy_json_types_are_exact_without_changing_legacy_equality(launcher, tmp_path, case):
    contract, path = _strict_v3_contract(launcher, tmp_path)
    original_path, policy_path = (contract["projection"][key] for key in ("original_policy", "policy"))
    original = json.loads(Path(original_path).read_text(encoding="utf-8-sig"))
    projected = json.loads(Path(policy_path).read_text(encoding="utf-8"))
    if case == "calls_float": projected["canonical_budget"]["logical_model_calls"] = 1200.0
    elif case == "tokens_float": projected["canonical_budget"]["model_tokens_total"] = 8000000.0
    elif case == "review_integer": projected["human_review_on_exhaustion"] = 1
    else: projected["max_retrieval_rounds"] = True
    launcher.validate_input_projection(original, projected, ["canonical_budget.policy_ref"])
    _strict_v3_rebind_json(contract, policy_path, projected)
    _write(path, contract)
    with pytest.raises(launcher.LaunchContractError):
        launcher.load_and_validate_launch_contract(path, require_authorization=False)
    _strict_v3_assert_no_execution(contract)
