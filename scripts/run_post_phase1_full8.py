"""One-shot Post-Phase1 Full8 launcher. Importing this file starts no business.

The approved contract and a separate, SHA-bound one-run authorization are both
required by the command line. Offline tests use only isolated files and HTTPX
MockTransport. HTTP permits are neither successful requests nor billed amounts.
"""
from __future__ import annotations

import argparse
import asyncio
from contextlib import contextmanager
import ctypes
from ctypes import wintypes
import datetime as dt
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import re
import stat
import subprocess
import sys
import threading
import time
import traceback
import types
import uuid

ROOT = Path("D:/MyCode/rag-agent-shared").resolve()
WORKER = ROOT / "docs/post_r13_business_improvements_audits/POST_BCA014_REAL_BUSINESS_E2E_20261003_161008_c0090159/real_business_worker.py"
WORKER_SHA256 = "01c3a164b126eb24af74d02f4c0c1acf9164ea7edbe3234596c6b777c80efce0"
HISTORICAL_ROOTS = (Path("E:/RC1_FULL8_REAL_E2E_20261020_R01"), Path("E:/RC1_FULL8_RAG_20261020_R01"))
HISTORICAL_IDS = {"task-rc1-full8-real-e2e-20261020-r01", "rc1-full8-real-e2e-20261020-r01"}
SCHEMA = "post-phase1-full8-launch-v1"
STRICT_V3_PROJECT_PROJECTION = "r03-strict-v3-project-projection-v1"
STRICT_V3_PROFILE_PATH = "generation_requirements.extra.verifier_decision_profile"
STRICT_V3_PROFILE = "claim-complete-support-v1"
PWA004_REJECTION_POLICY_PATH = "generation_requirements.extra.verifier_rejection_policy"
PWA004_REJECTION_POLICY = "review_draft_continue_v1"
DESIGN_RECEIPT = ROOT / "docs/post_phase1_full8_cost_control_simplification_20261021_r01/04_MECHANICAL_RECEIPT.json"
DESIGN_RECEIPT_SHA256 = "084c31aeb404d75208b6d6dff81f9c7c51eba38bea0188e2014d05bb31ec123d"
COMPATIBILITY_RECEIPT = ROOT / "docs/srcrev11_impl006_demo_compatibility_fix_20261021_r01/03_MECHANICAL_RECEIPT.json"
COMPATIBILITY_RECEIPT_SHA256 = "42532f3b40bd498ea1bda0f66921b45d108dacdd1d1770e346d3485117fc81a8"
# The only accepted update to the frozen design's dependency baseline. All
# other entries retain their original hashes; this is not blanket drift waiver.
COMPATIBILITY_SOURCE_SHA256 = {
    ROOT / "scripts/run_demo.py": "5ae3af206e65242f596199922bcf05317a31bf24c671bb0d1c9d569f722931db",
    ROOT / "backend/tests/test_mainline_closure_acceptance.py": "f52de898762fc66e59790f749ca6d1a5f08c4c88a66dd1a0693778b794e942a8",
}
# PP1F8-RESUME-001 / two-blocker-minimal-fix-v1. Exact approved bytes only;
# historical receipts retain their original hashes and all other dependencies.
TWO_BLOCKER_SOURCE_SHA256 = {
    ROOT / "backend/model_gateway/model_gateway.py": "8d4c90cc2331ae429b4ed3362f43b2273aef1adeb0ebf3a049ec58b53b09f7ae",
    ROOT / "backend/tests/test_multi_agent_v3_model_tool_runtime.py": "0a9e0aa5136820ad67fb4abd6808f6a5f45a0a17fc5864260d0ab69ddccd0e9a",
}
# DSP-008: only this approved config-source implementation supersedes its
# historical design pin. Every other critical source retains its exact pin.
DOTENV_POLICY_SOURCE_SHA256 = {
    ROOT / "backend/core/config.py": "8d80e9c8cd9d167ad4e6b9f77b169dd5f9cbaa5e34a653416556d22105aba5b1",
}
ORIGINAL_PROJECT_SHA256 = "5b04f17357a4a1df7e1d4d54c0b1197a33dfa4f2a896ac4cf3cdbd48344db527"
ORIGINAL_POLICY_SHA256 = "d7e54ea70cbf3ab79749c59c349c4de7cb3bc6e3ceb963b4936b3a363b4589db"
ORIGINAL_PLAN = ROOT / "docs/rc1_full8_real_e2e_acceptance_20261020_r01/evidence/PLAN.json"
ORIGINAL_PLAN_SHA256 = "c9df6e1c07b53c5d052e940a45b4e2bafa3bdc9fd5d5e53c7b22e83283102b92"
ORIGINAL_RAG_MANIFEST = HISTORICAL_ROOTS[1] / "data/processed/indexes/idx_type_aware_parent_child_v2_m3e_base_local_m3e_base_profiled_v2_fd73dfd6ca2d/index_manifest.json"
ORIGINAL_RAG_MANIFEST_SHA256 = "a272498bed6e76913ef2c5557b7770389f20f41ea0b8c9fb19c05bfcdd8b1355"
ORIGINAL_RAG_ROOT = HISTORICAL_ROOTS[1]
RAG_SOURCE_MANIFEST = ROOT / "docs/rc1_full8_real_e2e_acceptance_20261020_r01/evidence/RAG_SOURCE_MANIFEST.json"
RAG_SOURCE_MANIFEST_SHA256 = "cc085d6218a288ce411008ec16251ae5d2a83bc277448da459daf9c960554cb9"
RAG_RESOURCE_MANIFEST = ROOT / "docs/rc1_full8_real_e2e_final_authorization_20261021_r01/freeze_snapshot_01a15e1d/01_FINAL_FREEZE_MANIFEST.json"
RAG_RESOURCE_MANIFEST_SHA256 = "a0cf6314ef04cc875c6791c1b74e2f2d062cb47e45af7cdf94bcd4204c1f39c3"
G07_SHARED_SOURCE = ROOT / "docs/rc1_full8_r02_g07_closure_20261021_r01/evidence/dependency_current.json"
G07_SHARED_SOURCE_SHA256 = "364fd20c07cd11ff9d041e60f669c4b97437ae5ab684ee03837b93b5eda60e9f"
CANONICAL = {"logical_model_calls": 1200, "logical_tool_calls": 160, "model_tokens_total": 8_000_000}
SECTIONS = ["项目背景", "建设目标", "总体架构", "计算资源建设方案", "存储资源建设方案", "网络资源建设方案", "安全设计", "实施与验收建议"]
CACHE_VARIABLES = ("HF_HOME", "HF_HUB_CACHE", "HF_ASSETS_CACHE", "TRANSFORMERS_CACHE", "HF_MODULES_CACHE", "SENTENCE_TRANSFORMERS_HOME", "TORCH_HOME", "XDG_CACHE_HOME", "TORCH_EXTENSIONS_DIR", "TORCHINDUCTOR_CACHE_DIR")
DOTENV_PATH = ROOT / ".env"
CONFIG_SOURCE_POLICY_ENV = "RAG_CONFIG_SOURCE_POLICY"
CONFIG_SOURCE_POLICY = "process_env_defaults_v1"
ENVIRONMENT_BASELINE_RELATIVE = Path("dotenv_source_policy_v1/EFFECTIVE_ENVIRONMENT_BASELINE.json")
ENVIRONMENT_SOURCE_FILES = (
    ROOT / "scripts/run_post_phase1_full8.py", WORKER,
    ROOT / "backend/core/config.py", ROOT / "backend/bootstrap/runtime_options.py",
    ROOT / "backend/prompt_manager/prompt_manager.py", ROOT / "backend/bootstrap/supervisor_factory.py",
    ROOT / "backend/bootstrap/rag_service_factory.py", ROOT / "backend/bootstrap/model_factory.py",
    ROOT / "backend/rag/configs/RAGConfig.py", ROOT / "backend/rag/runtime/retrieval_runtime.py",
)
ENVIRONMENT_INPUT_FILES = (
    ROOT / "prompts/scheme/scheme_section_generation_v2.txt", ROOT / "config/model_capabilities.json",
    ROOT / "backend/rag/config/static_retrieval_v1.yaml", ROOT / "backend/rag/config/intent_policy_v1.yaml",
    ROOT / "backend/rag/config/retrieval_gate_policy_v1.yaml",
)
# Only process loading/user-location inputs and the selected route's credential
# survive from the parent. Their nonsecret values are additionally SHA-bound in
# the prepared baseline. Unknown parent keys never reach the Worker.
_OS_ENVIRONMENT = ("SYSTEMROOT", "WINDIR", "SYSTEMDRIVE", "COMSPEC", "PATH", "PATHEXT", "USERPROFILE", "HOMEDRIVE", "HOMEPATH", "APPDATA", "LOCALAPPDATA")
_CONFIGURATION_ENVIRONMENT = set("""
APP_NAME APP_ENV DATA_ROOT PROMPT_ROOT DEFAULT_MODEL_NAME DEFAULT_SCHEME_PROMPT_ID
TRACE_ENABLED DATA_CAPTURE_ENABLED MIN_EVAL_OUTPUT_CHARS MAX_RECOVERY_ATTEMPTS
GATE_ENFORCEMENT_MODE GATE_RECOVERY_ROUNDS_LIMIT GATE_NO_PROGRESS_ROUNDS_LIMIT ENABLE_LLM_ROUTING
SUPERVISOR_MODEL_NAME LOCAL_QWEN_MODEL_NAME LOCAL_QWEN_MODEL_PATH LOCAL_QWEN_DEVICE LOCAL_QWEN_MAX_NEW_TOKENS
LOCAL_QWEN_1_5B_MODEL_NAME LOCAL_QWEN_1_5B_MODEL_PATH LOCAL_QWEN_3B_MODEL_NAME LOCAL_QWEN_3B_MODEL_PATH
LOCAL_QWEN_7B_MODEL_NAME LOCAL_QWEN_7B_MODEL_PATH MEMORY_DATABASE_URL MEMORY_DATABASE_POOL_MIN_SIZE
MEMORY_DATABASE_POOL_MAX_SIZE MEMORY_DATABASE_STATEMENT_TIMEOUT_MS MEMORY_DATABASE_SCHEMA_VERSION
RAW_MODEL_RETENTION_DAYS RAW_MODEL_DIAGNOSTIC_RETENTION_DAYS RAW_TOOL_RETENTION_DAYS RAW_TOOL_DIAGNOSTIC_RETENTION_DAYS
OLD_ARTIFACT_RETENTION_DAYS CLAIM_VERIFICATION_HISTORY_RETENTION_DAYS MEMORY_HISTORICAL_RETENTION_DAYS
MEMORY_INELIGIBLE_QUARANTINE_DAYS CONTEXT_REPLAY_RETENTION_DAYS CONTEXT_REPLAY_DIAGNOSTIC_RETENTION_DAYS
DISK_SOFT_FREE_RATIO DISK_SOFT_FREE_GIB DISK_HARD_FREE_RATIO DISK_HARD_FREE_GIB DISK_CRITICAL_FREE_RATIO DISK_CRITICAL_FREE_GIB
DEEPSEEK_MODEL_NAME DEEPSEEK_PROVIDER_MODEL_NAME DEEPSEEK_BASE_URL DEEPSEEK_THINKING_ENABLED DEEPSEEK_MAX_OUTPUT_TOKENS
SECTION_GENERATION_MODEL_PROFILE MODEL_CAPABILITIES_PATH USE_REAL_RAG_TOOL RAG_PROJECT_ROOT ENABLE_AGENT_SELF_RAG
ENABLE_SEMANTIC_GATE SEMANTIC_GATE_MODEL_NAME RAG_STATIC_RETRIEVAL_SPEC_FILE RAG_INTENT_POLICY_FILE
RAG_RETRIEVAL_GATE_POLICY_FILE GROUNDED_GENERATION_POLICY_FILE SECTION_MAX_CONCURRENCY
EXECUTE_SECTIONS_TIMEOUT_SECONDS EVALUATE_DOCUMENT_GATE_TIMEOUT_SECONDS VERIFIER_MAX_OUTPUT_TOKENS
""".split())
_NETWORK_ENVIRONMENT = set("HTTP_PROXY HTTPS_PROXY ALL_PROXY NO_PROXY OPENAI_PROXY SSL_CERT_FILE SSL_CERT_DIR REQUESTS_CA_BUNDLE CURL_CA_BUNDLE OPENAI_BASE_URL".split())
_NATIVE_ENVIRONMENT = set("""
PYTHONPATH PYTHONHOME ARROW_HOME ARROW_DEFAULT_MEMORY_POOL HF_ENDPOINT HF_DATASETS_CACHE HF_XET_CACHE
HF_HUB_ENABLE_HF_TRANSFER HF_XET_HIGH_PERFORMANCE PYTORCH_PRETRAINED_BERT_CACHE PYTORCH_TRANSFORMERS_CACHE
TIKTOKEN_CACHE_DIR DATA_GYM_CACHE_DIR NUMBA_CACHE_DIR TRITON_CACHE_DIR CUDA_CACHE_PATH CUDA_CACHE_DISABLE MPLCONFIGDIR
LD_LIBRARY_PATH LD_PRELOAD DYLD_LIBRARY_PATH DYLD_INSERT_LIBRARIES
LANGCHAIN_OPENAI_TCP_KEEPALIVE LANGCHAIN_OPENAI_TCP_KEEPCNT LANGCHAIN_OPENAI_TCP_KEEPIDLE
LANGCHAIN_OPENAI_TCP_KEEPINTVL LANGCHAIN_OPENAI_TCP_USER_TIMEOUT_MS
""".split())
_SECRET_NAME = re.compile(r"(^|_)(API_KEY|SECRET|PASSWORD|DSN|TOKEN)($|_)")
sys.dont_write_bytecode = True


class LaunchContractError(ValueError):
    pass


def utc_now():
    return dt.datetime.now(dt.timezone.utc).isoformat()


def sha256_file(path):
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _read_json(path):
    return json.loads(Path(path).read_text(encoding="utf-8-sig"))


def _write_exclusive(path, value):
    with Path(path).open("x", encoding="utf-8", newline="\n") as handle:
        json.dump(value, handle, ensure_ascii=False, indent=2)
        handle.write("\n")
        handle.flush()
        os.fsync(handle.fileno())


def _append(path, value):
    with Path(path).open("a", encoding="utf-8", newline="\n") as handle:
        handle.write(json.dumps(value, ensure_ascii=False) + "\n")
        handle.flush()
        os.fsync(handle.fileno())


def _require(condition, message):
    if not condition:
        raise LaunchContractError(message)


def _positive_integer(value, label):
    _require(type(value) is int and value > 0, label + " must be a positive integer")
    return value


def _reject_placeholders(value):
    if isinstance(value, dict):
        for key, item in value.items():
            _reject_placeholders(key)
            _reject_placeholders(item)
    elif isinstance(value, list):
        for item in value:
            _reject_placeholders(item)
    elif isinstance(value, str):
        _require(not re.search(r"<[^>]*>|\$\{|\b(?:TODO|TBD|PLACEHOLDER)\b|待填写|待批准", value, re.I), "Unresolved launch placeholder")


def _ordinary_path(path, *, directory=None, hardlinks=False):
    path = Path(path).absolute()
    for candidate in [*reversed(path.parents), path]:
        if not candidate.exists() and not candidate.is_symlink():
            continue
        info = candidate.lstat()
        _require(not stat.S_ISLNK(info.st_mode) and not (getattr(info, "st_file_attributes", 0) & 0x400), "Reparse/symlink path refused")
    _require(path.exists(), "Required prepared path is absent")
    info = path.stat()
    if directory is True:
        _require(stat.S_ISDIR(info.st_mode), "Expected ordinary directory")
    if directory is False:
        _require(stat.S_ISREG(info.st_mode), "Expected ordinary file")
    if stat.S_ISREG(info.st_mode) and not hardlinks:
        _require(info.st_nlink == 1, "Hard-linked mutable input/artifact refused")
    return path.resolve()


def _under(path, root):
    return Path(path).resolve().is_relative_to(Path(root).resolve())


def _disjoint(first, second):
    first, second = Path(first).resolve(), Path(second).resolve()
    return not (first.is_relative_to(second) or second.is_relative_to(first))


def _require_protected_directory(path, *, require_protected=True):
    """Same trusted-principal/write-mask policy used by Writer Review Copy."""
    _require(os.name == "nt", "Protected Windows directory cannot be verified on this platform")
    import win32api
    import win32con
    import win32security
    token = win32security.OpenProcessToken(win32api.GetCurrentProcess(), win32con.TOKEN_QUERY)
    try:
        user = win32security.GetTokenInformation(token, win32security.TokenUser)
        sid = user[0] if isinstance(user, tuple) else user
        trusted = {win32security.ConvertSidToStringSid(sid), "S-1-5-18", "S-1-5-32-544"}
    finally:
        token.Close()
    sd = win32security.GetNamedSecurityInfo(str(path), win32security.SE_FILE_OBJECT, win32security.OWNER_SECURITY_INFORMATION | win32security.DACL_SECURITY_INFORMATION)
    _require(win32security.ConvertSidToStringSid(sd.GetSecurityDescriptorOwner()) in trusted, "Untrusted prepared-root owner")
    if require_protected:
        _require(sd.GetSecurityDescriptorControl()[0] & 0x1000, "Prepared-root DACL is not protected")
    acl = sd.GetSecurityDescriptorDacl()
    _require(acl is not None, "NULL DACL refused")
    mask = 0x0002 | 0x0004 | 0x0010 | 0x0100 | 0x0040 | 0x10000 | 0x40000 | 0x80000 | 0x40000000 | 0x10000000
    for index in range(acl.GetAceCount()):
        ace = acl.GetAce(index)
        if ace[0][0] in (0, 5) and ace[1] & mask:
            _require(win32security.ConvertSidToStringSid(ace[-1]) in trusted, "Untrusted write permission on prepared root")


def validate_layout_and_identity(contract, *, check_acl=True):
    _require(Path(contract.get("project_root", ROOT)).resolve() == ROOT, "Wrong production checkout ROOT")
    for name in ("task_id", "run_id"):
        identity = contract.get(name)
        _require(isinstance(identity, str) and re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,159}", identity), "Invalid new Task/Run identity")
        _require(identity not in HISTORICAL_IDS, "Historical R01 identity refused")
    roots = {name: _ordinary_path(contract[name], directory=True) for name in ("prep_root", "output_root", "rag_root")}
    for name, root in roots.items():
        _require(all(_disjoint(root, old) for old in HISTORICAL_ROOTS), "Historical root overlap")
        _require(_disjoint(root, ROOT), "Prepared root overlaps source checkout")
        for other, other_root in roots.items():
            if name != other:
                _require(_disjoint(root, other_root), "PREP/OUTPUT/RAG roots overlap")
    _require(not any(roots["output_root"].iterdir()), "OUTPUT must be an empty ordinary directory")
    if check_acl:
        for name in ("prep_root", "output_root", "rag_root"):
            _require_protected_directory(roots[name])
    for relative in ("inputs", "audit", "tmp", "cache"):
        path = _ordinary_path(roots["prep_root"] / relative, directory=True)
        _require(_under(path, roots["prep_root"]), "PREP subdirectory escape")
    return {name: str(path) for name, path in roots.items()}


def validate_input_projection(original, projected, allowed_changes):
    """Only exact dot paths named in the approved contract may differ."""
    original = _read_json(original) if isinstance(original, (str, Path)) else original
    projected = _read_json(projected) if isinstance(projected, (str, Path)) else projected
    allowed = set(allowed_changes)
    _require(allowed <= {"task_id", "run_id", "canonical_budget.policy_ref"}, "Unapproved business projection path")
    def remove(value):
        result = json.loads(json.dumps(value, ensure_ascii=False))
        for name in allowed:
            parts = name.split(".")
            parent = result
            for part in parts[:-1]:
                parent = parent.get(part, {})
            if isinstance(parent, dict):
                parent.pop(parts[-1], None)
        return result
    _require(remove(original) == remove(projected), "Business input, facts, chapter order, permission or numeric budget changed")
    return {"business_payload_sha256": hashlib.sha256(json.dumps(remove(projected), sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode()).hexdigest(), "allowed_changes": sorted(allowed)}


def _validate_strict_v3_project_projection(original_path, projected, allowed_changes, *, task_id, run_id):
    """One prospective V3 addition to the pinned R01 payload; no general allowlist."""
    expected = {"task_id", STRICT_V3_PROFILE_PATH}
    prospective_review = (isinstance(allowed_changes, list)
                          and PWA004_REJECTION_POLICY_PATH in allowed_changes)
    if prospective_review:
        expected.add(PWA004_REJECTION_POLICY_PATH)
    _require(isinstance(allowed_changes, list) and len(allowed_changes) == len(expected)
             and all(isinstance(path, str) for path in allowed_changes)
             and set(allowed_changes) == expected,
             "Strict V3 requires exactly the approved ProjectInput projection paths")
    _require(isinstance(original_path, (str, Path))
             and sha256_file(original_path) == ORIGINAL_PROJECT_SHA256,
             "Original business input provenance differs")
    original = _read_json(original_path)
    _require(isinstance(original, dict) and isinstance(projected, dict)
             and original.get("schema_version") == projected.get("schema_version") == "project_input_v3",
             "Strict V3 requires original and projected ProjectInput V3")
    historical_ids = HISTORICAL_IDS | {
        "task-rc1-full8-post-phase1-20261021-r02", "rc1-full8-post-phase1-20261021-r02"}
    _require(all(isinstance(identity, str) and identity not in historical_ids
                 for identity in (task_id, run_id))
             and projected.get("task_id") == task_id and task_id != original.get("task_id"),
             "Strict V3 requires new bound Task/Run identities")
    old_generation, new_generation = (value.get("generation_requirements") for value in (original, projected))
    _require(isinstance(old_generation, dict) and isinstance(new_generation, dict),
             "Strict V3 generation requirements must be objects")
    old_extra, new_extra = old_generation.get("extra"), new_generation.get("extra")
    key = "verifier_decision_profile"
    _require(isinstance(old_extra, dict) and isinstance(new_extra, dict) and key not in old_extra,
             "Strict V3 requires an originally absent profile key")
    _require(new_extra.get(key) == STRICT_V3_PROFILE,
             "Strict V3 requires the exact claim-complete-support-v1 profile")
    if prospective_review:
        _require(PWA004_REJECTION_POLICY_PATH not in old_extra
                 and "verifier_rejection_policy" not in old_extra
                 and new_extra.get("verifier_rejection_policy") == PWA004_REJECTION_POLICY,
                 "PWA004 requires the exact prospective review-draft policy")
    else:
        _require("verifier_rejection_policy" not in new_extra,
                 "Unapproved Verifier rejection policy in historical Strict V3 input")
    # Compare raw JSON, before Schema default expansion. Serialization preserves
    # JSON scalar types (e.g. true versus 1), unlike Python dict equality.
    def business_bytes(value, *, remove_profile=False):
        result = json.loads(json.dumps(value, ensure_ascii=False))
        result.pop("task_id", None)
        if remove_profile:
            result["generation_requirements"]["extra"].pop(key)
            if prospective_review:
                result["generation_requirements"]["extra"].pop("verifier_rejection_policy")
        return json.dumps(result, sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    business = business_bytes(projected, remove_profile=True)
    _require(business_bytes(original) == business,
             "Business input, facts, chapter order, permission or numeric budget changed")
    return {"business_payload_sha256": hashlib.sha256(business).hexdigest(),
            "allowed_changes": sorted(allowed_changes), "project_projection_profile": STRICT_V3_PROJECT_PROJECTION}


def _verify_manifest(manifest, *, source=False):
    _require(isinstance(manifest, dict) and manifest, "Nonempty SHA manifest required")
    for name, expected in manifest.items():
        path = Path(name)
        if not path.is_absolute():
            _require(source, "Input SHA paths must be absolute")
            path = ROOT / path
        _require(os.path.normcase(os.path.abspath(path)) != os.path.normcase(os.path.abspath(DOTENV_PATH)),
                 "Shared dotenv cannot be a frozen source/input")
        _ordinary_path(path, directory=False, hardlinks=source)
        _require(re.fullmatch(r"[0-9a-f]{64}", str(expected)) is not None and sha256_file(path) == expected, "Frozen source/input SHA drift")


def _require_input_binding(contract, path):
    path = Path(path).resolve()
    bound = {Path(name).resolve(): digest for name, digest in contract["input_sha256"].items()}
    _require(path in bound and sha256_file(path) == bound[path], "Used input is absent from exact frozen input manifest")


def validate_rag_manifest(contract):
    rag = Path(contract["rag_root"]).resolve()
    manifest_path = _ordinary_path(contract["rag_manifest_path"], directory=False)
    pointer_path = _ordinary_path(contract["rag_pointer_path"], directory=False)
    _require(_under(manifest_path, rag) and _under(pointer_path, rag), "RAG manifest/pointer escape")
    _require(pointer_path == rag / "data/processed/indexes/active_index.json", "RAG pointer differs from actual runtime path")
    capture = rag / "data/processed/runs/retrieval_runs.jsonl"
    try:
        capture.lstat()
    except FileNotFoundError:
        pass
    else:
        raise LaunchContractError("New RAG retrieval log must not contain historical state or links")
    for parent in capture.parents:
        if parent.exists(): _ordinary_path(parent, directory=True)
        if parent == rag: break
    _require_input_binding(contract, manifest_path)
    _require_input_binding(contract, pointer_path)
    pointer, manifest = _read_json(pointer_path), _read_json(manifest_path)
    pointer_manifest = pointer.get("manifest_path")
    _require(isinstance(pointer_manifest, str), "Active pointer lacks manifest_path")
    _require(".." not in Path(pointer_manifest).parts and "~" not in Path(pointer_manifest).parts, "RAG pointer traversal refused")
    bound = Path(pointer_manifest)
    bound = bound if bound.is_absolute() else pointer_path.parent / bound
    _require(bound.resolve() == manifest_path and pointer.get("manifest_sha256") == sha256_file(manifest_path), "Active pointer manifest identity/SHA mismatch")
    _require(set(pointer) == {"schema_version", "index_version", "manifest_path", "manifest_sha256"}
             and pointer.get("schema_version") == "active_rag_index_pointer_v1", "Unsupported RAG pointer schema/field")
    _require(pointer.get("index_version") == manifest.get("index_version"), "RAG pointer/index version mismatch")
    artifacts = manifest.get("artifacts")
    _require(isinstance(artifacts, dict) and bool(artifacts), "RAG manifest has no named artifacts")

    def files_in(directory):
        # Inspect each directory before descending, including Windows junctions.
        files, pending = set(), [directory]
        while pending:
            parent = _ordinary_path(pending.pop(), directory=True)
            for child in parent.iterdir():
                child = _ordinary_path(child)
                if child.is_dir(): pending.append(child)
                else:
                    _ordinary_path(child, directory=False)
                    _require_input_binding(contract, child)
                    files.add(child)
        return files

    directory_files = {}
    for name, artifact in artifacts.items():
        _require(isinstance(artifact, dict) and isinstance(artifact.get("path"), str), "Malformed RAG artifact")
        raw = Path(artifact["path"])
        _require(".." not in raw.parts and "~" not in raw.parts, "RAG relative traversal refused")
        path = raw if raw.is_absolute() else manifest_path.parent / raw
        _require(_under(path, rag), "RAG absolute artifact escapes new copy")
        path = _ordinary_path(path)
        if path.is_dir():
            directory_files[name] = (path, files_in(path))
        else:
            _ordinary_path(path, directory=False)
            _require_input_binding(contract, path)
            _require(sha256_file(path) == artifact["sha256"], "RAG artifact SHA drift")

    # Source authority is the frozen original, independent of the new manifest's
    # own input SHA. Only artifact locations may change; data/model/provenance
    # and all nested metadata retain their exact original values.
    source = _ordinary_path(ORIGINAL_RAG_MANIFEST, directory=False)
    _require(sha256_file(source) == ORIGINAL_RAG_MANIFEST_SHA256, "Original RAG provenance SHA drift")
    original = _read_json(source)
    _require(manifest.get("schema_version") == original.get("schema_version") == "rag_index_manifest_v1", "Unsupported RAG manifest schema")
    fields = {"schema_version", "index_version", "build_id", "dataset_version", "config_hash", "source_hash", "source", "chunker", "embedding", "index", "document_count", "parent_chunk_count", "child_chunk_count", "artifacts", "created_at", "reproducibility_hash", "notes"}
    _require(set(manifest) <= fields and set(original) <= fields, "Unknown RAG manifest field")
    def payload(value):
        result = json.loads(json.dumps(value))
        for item in result["artifacts"].values(): item.pop("path", None)
        return result
    _require(payload(manifest) == payload(original), "RAG source identity or non-path payload changed")
    # Milvus' artifact SHA is semantic, so a byte-identical copy also needs the
    # original independent per-file source manifest, including lock/metadata.
    source_files = _ordinary_path(RAG_SOURCE_MANIFEST, directory=False)
    _require(sha256_file(source_files) == RAG_SOURCE_MANIFEST_SHA256, "Original RAG file manifest SHA drift")
    frozen_files = _read_json(source_files)
    for name, (directory, members) in directory_files.items():
        old = Path(original["artifacts"][name]["path"])
        _require(_under(old, ORIGINAL_RAG_ROOT), "Original directory artifact lacks source-root binding")
        prefix = old.resolve().relative_to(Path(ORIGINAL_RAG_ROOT).resolve()).as_posix() + "/"
        expected = {key[len(prefix):]: item["sha256"] for key, item in frozen_files.items() if key.startswith(prefix)}
        actual = {item.relative_to(directory).as_posix(): sha256_file(item) for item in members}
        _require(bool(expected) and actual == expected, "RAG directory copy differs from exact source bytes")

    # These exact v1 fields are written by the offline builder and never
    # dereferenced by ActiveIndexResolver/ParentChildRuntimeFactory. BM25 reads
    # the resolved child_chunks artifact, not index.bm25_records_path.
    provenance = {("source", "path"), ("source", "document_profiles_path"),
                  ("source", "structured_assets_root"), ("index", "output_dir"),
                  ("index", "bm25_records_path"), ("index", "profiled_cleaned_text_units_path")}
    def inspect(value, location=()):
        if isinstance(value, dict):
            for name, child in value.items():
                inspect(child, (*location, name))
        elif isinstance(value, list):
            for index, child in enumerate(value): inspect(child, (*location, index))
        elif isinstance(value, str):
            key = str(location[-1]) if location else ""
            is_path = Path(value).is_absolute() or key == "path" or key.endswith(("_file", "_path", "_dir", "_root"))
            if is_path:
                known = location in provenance or location == ("embedding", "model") or (
                    len(location) == 3 and location[0] == "artifacts" and location[2] == "path")
                _require(known, "Unclassified nested RAG path refused")
                _require(".." not in Path(value).parts and "~" not in Path(value).parts, "RAG path traversal refused")
    inspect(manifest)

    # The exact model directory is selected by the frozen source; no parent
    # directory grants access. The caller's already SHA-bound input manifest
    # must enumerate every ordinary file, with no missing or extra member.
    embedding = manifest["embedding"]
    _require(embedding.get("mode") == "model" and Path(embedding.get("model", "")).is_absolute(), "Expected frozen local embedding model")
    model = _ordinary_path(embedding["model"], directory=True)
    _require(all(_disjoint(model, root) for root in (rag, Path(contract["prep_root"]), Path(contract["output_root"]), *HISTORICAL_ROOTS)), "Shared model overlaps mutable Run root")
    model_files = files_in(model)
    bound_model_files = {Path(name).resolve() for name in contract["input_sha256"] if _under(name, model)}
    _require(bool(model_files) and model_files == bound_model_files, "Shared model exact frozen file set differs")
    resource_manifest = _ordinary_path(RAG_RESOURCE_MANIFEST, directory=False)
    _require(sha256_file(resource_manifest) == RAG_RESOURCE_MANIFEST_SHA256, "Frozen RAG resource manifest SHA drift")
    resource_files = _read_json(resource_manifest)["files"]
    expected_model = {Path(item["path"]).resolve(): item["sha256"] for item in resource_files
                      if item.get("source") == "local_embedding_or_reranker_model_resource" and _under(item["path"], model)}
    _require({item: sha256_file(item) for item in model_files} == expected_model,
             "Shared model bytes/file set differ from approved source")
    return {"manifest_sha256": sha256_file(manifest_path), "pointer_sha256": sha256_file(pointer_path)}


def validate_shared_dependencies(contract, *, deadline=None, check_acl=True):
    """The user's exact 59-file finite-integrity choice; no OS immutability claim.

    The earlier observation is not allowed to authorize new sources: its exact
    bytes and every member are independently pinned to the original freeze.
    Only the two exact model directories are enumerated, never their parents.
    """
    def in_time():
        if deadline is not None and time.monotonic() >= deadline:
            raise TimeoutError("Shared dependency integrity deadline exhausted")
    in_time()
    source = _ordinary_path(G07_SHARED_SOURCE, directory=False)
    _require(sha256_file(source) == G07_SHARED_SOURCE_SHA256, "G07 shared source identity drift")
    frozen = _read_json(source)
    entries = frozen.get("files")
    _require(isinstance(entries, list) and len(entries) == 59, "G07 requires the exact 59 shared files")
    _require(sum(item.get("role") == "embedding" for item in entries) == 26
             and sum(item.get("role") == "reranker" for item in entries) == 28,
             "G07 exact model member counts changed")
    paths = [Path(item["path"]).absolute() for item in entries]
    _require(all(Path(item["path"]).is_absolute() and ".." not in Path(item["path"]).parts for item in entries)
             and len({str(path).casefold() for path in paths}) == 59, "G07 duplicate or noncanonical shared path")
    authority = _ordinary_path(RAG_RESOURCE_MANIFEST, directory=False)
    _require(sha256_file(authority) == RAG_RESOURCE_MANIFEST_SHA256, "G07 independent resource authority drift")
    resources = _read_json(authority)["files"]
    independent = {}
    for item in resources:
        key = Path(item["path"]).absolute()
        if key in paths:
            identity = (item["sha256"], item.get("size", item.get("bytes")))
            _require(key not in independent or independent[key] == identity, "Ambiguous G07 source authority")
            independent[key] = identity
    bound = {Path(name).resolve(): digest for name, digest in contract["input_sha256"].items()}
    for role in ("embedding", "reranker"):
        root = _ordinary_path(frozen["roots"][role], directory=True)
        expected = {path.resolve() for path, item in zip(paths, entries) if item["role"] == role}
        _require(all(_under(path, root) for path in expected), "G07 shared model root binding differs")
        observed, pending = set(), [root]
        while pending:
            in_time()
            parent = _ordinary_path(pending.pop(), directory=True)
            for candidate in parent.iterdir():
                candidate = _ordinary_path(candidate)
                if candidate.is_dir():
                    pending.append(candidate)
                else:
                    observed.add(_ordinary_path(candidate, directory=False))
        _require(observed == expected, "G07 shared model member set drift")
    observed_files = []
    for path, expected in zip(paths, entries):
        in_time()
        path = _ordinary_path(path, directory=False)
        identity = (expected["sha256"], expected["bytes"])
        _require(independent.get(path) == identity and bound.get(path) == expected["sha256"],
                 "G07 shared file lacks independent source and input SHA binding")
        digest = sha256_file(path)
        _require(path.stat().st_size == expected["bytes"] and digest == expected["sha256"], "G07 shared file bytes drift")
        observed_files.append({"path": str(path), "bytes": expected["bytes"], "sha256": digest, "role": expected["role"]})
        in_time()
    acl_observation = []
    if check_acl:
        import win32security
        for entry in frozen.get("acl_objects", []):
            in_time()
            path = _ordinary_path(entry["path"])
            _require(any(path == member or path in member.parents for member in paths), "Unknown G07 ACL observation path")
            sd = win32security.GetNamedSecurityInfo(str(path), win32security.SE_FILE_OBJECT,
                win32security.OWNER_SECURITY_INFORMATION | win32security.GROUP_SECURITY_INFORMATION | win32security.DACL_SECURITY_INFORMATION)
            sddl = win32security.ConvertSecurityDescriptorToStringSecurityDescriptor(
                sd, win32security.SDDL_REVISION_1, win32security.OWNER_SECURITY_INFORMATION | win32security.GROUP_SECURITY_INFORMATION | win32security.DACL_SECURITY_INFORMATION)
            if "sddl_sha256" in entry:
                if hashlib.sha256(sddl.encode()).hexdigest() != entry["sddl_sha256"]:
                    # Get-Acl and GetNamedSecurityInfo may serialize an all-
                    # allow DACL in a different order. Compare exact grants,
                    # owner/group and protection; never reorder a real ACL.
                    reference = frozen["acl_classes"][entry["acl_class"]]["sddl"]
                    _require(hashlib.sha256(reference.encode()).hexdigest() == entry["sddl_sha256"], "G07 ACL source identity drift")
                    expected_sd = win32security.ConvertStringSecurityDescriptorToSecurityDescriptor(reference, 1)
                    def allow_signature(value):
                        dacl = value.GetSecurityDescriptorDacl()
                        _require(dacl is not None, "G07 NULL DACL refused")
                        aces = [dacl.GetAce(i) for i in range(dacl.GetAceCount())]
                        _require(all(ace[0][0] == 0 for ace in aces), "G07 non-allow ACL serialization drift")
                        return (win32security.ConvertSidToStringSid(value.GetSecurityDescriptorOwner()),
                            win32security.ConvertSidToStringSid(value.GetSecurityDescriptorGroup()),
                            value.GetSecurityDescriptorControl()[0] & 0x1000,
                            sorted((ace[0][1], ace[1], win32security.ConvertSidToStringSid(ace[-1])) for ace in aces))
                    _require(allow_signature(sd) == allow_signature(expected_sd), "G07 shared ACL observation drift")
            acl_observation.append({"path": str(path), "sddl": sddl})
    return {"status": "PASSED", "risk": "ACCEPTED_FINITE_INTEGRITY_RISK", "os_enforced_read_only": False,
            "source_manifest_sha256": G07_SHARED_SOURCE_SHA256, "resource_authority_sha256": RAG_RESOURCE_MANIFEST_SHA256,
            "files": observed_files, "acl_observation": acl_observation,
            "acl_semantics": "Observed only; same-privilege/AU/Admin writes remain possible", "utc": utc_now()}


def postcheck_shared_dependencies(contract, before, *, deadline=None, tree_reaped=True):
    """Supervisor evidence, parallel to the original business/stop assessment."""
    if not tree_reaped:
        return {"status": "INTEGRITY_POSTCHECK_UNVERIFIED", "reason": "Owned process tree was not verified stopped", "utc": utc_now()}
    try:
        after = validate_shared_dependencies(contract, deadline=deadline)
        _require(before.get("status") == "PASSED" and before.get("files") == after["files"]
                 and before.get("source_manifest_sha256") == after["source_manifest_sha256"], "G07 pre/post identity differs")
        return {"status": "PASSED", "snapshot": after, "runtime_immutability_proven": False,
                "mid_run_change_and_restore_detected": "UNVERIFIED_BY_PRE_POST_HASHES", "utc": utc_now()}
    except BaseException as exc:
        return {"status": "INTEGRITY_FAIL" if isinstance(exc, LaunchContractError) else "INTEGRITY_POSTCHECK_UNVERIFIED",
                "error_type": type(exc).__name__, "message": "Shared dependency integrity could not be established; no retry or repair",
                "utc": utc_now()}


def load_and_validate_launch_contract(contract_path, authorization_path=None, *, require_authorization=True, _worker_stage=False):
    if _worker_stage:
        install_dotenv_read_guard()
    _require_config_source_policy(worker_stage=_worker_stage)
    contract_path = _ordinary_path(contract_path, directory=False)
    contract = _read_json(contract_path)
    _reject_placeholders(contract)
    _require(contract.get("schema_version") == SCHEMA, "Unsupported launch contract")
    # Parent performs Windows ACL API checks. The suspended child's entry must
    # remain stdlib-only until the original Worker's H7 startup has completed.
    validate_layout_and_identity(contract, check_acl=not _worker_stage)
    _positive_integer(contract.get("physical_attempt_limit"), "Physical attempt limit N")
    for name in ("observation_seconds", "forensic_seconds", "reap_seconds"):
        value = contract.get("watchdog", {}).get(name)
        _require(type(value) in (int, float) and 0 < value < float("inf"), "Finite positive watchdog allocation required")
    _verify_manifest(contract.get("source_sha256"), source=True)
    _verify_manifest(contract.get("input_sha256"))
    # Preserve all 48 dependencies, accepting only exact receipt-bound
    # compatibility updates and this version's two approved fixes. run_demo
    # remains additionally pinned outside the older 48-file list.
    _require(sha256_file(DESIGN_RECEIPT) == DESIGN_RECEIPT_SHA256, "Frozen design receipt drift")
    _require(sha256_file(COMPATIBILITY_RECEIPT) == COMPATIBILITY_RECEIPT_SHA256, "Compatibility receipt drift")
    approved_sources = {**COMPATIBILITY_SOURCE_SHA256, **TWO_BLOCKER_SOURCE_SHA256,
                        **DOTENV_POLICY_SOURCE_SHA256}
    for entry in _read_json(DESIGN_RECEIPT)["target_source_sha256"]:
        expected = approved_sources.get(Path(entry["path"]).resolve(), entry["sha256"])
        _require(sha256_file(entry["path"]) == expected, "Critical production/SDK source drift")
    _verify_manifest({str(path): digest for path, digest in approved_sources.items()}, source=True)
    _require(sha256_file(WORKER) == WORKER_SHA256, "Pinned Worker drift")
    _require(any(Path(path if Path(path).is_absolute() else ROOT / path).resolve() == WORKER for path in contract["source_sha256"]), "Worker absent from source manifest")
    _require(any(Path(path if Path(path).is_absolute() else ROOT / path).resolve() == ROOT / "scripts/run_post_phase1_full8.py" for path in contract["source_sha256"]), "Launcher absent from source manifest")
    plan_path = _ordinary_path(contract["plan_path"], directory=False)
    _require(_under(plan_path, Path(contract["prep_root"]) / "inputs") and sha256_file(plan_path) == contract["plan_sha256"], "PLAN identity mismatch")
    plan = _read_json(plan_path)
    _require(sha256_file(ORIGINAL_PLAN) == ORIGINAL_PLAN_SHA256, "Original PLAN provenance drift")
    approved_plan = _read_json(ORIGINAL_PLAN)
    for name in ("task_id", "run_id", "project_root", "output_root", "rag_root"):
        _require(plan.get(name) == contract.get(name), "PLAN identity/path binding differs")
    _require(plan.get("caller_approved_conversion") is True and plan.get("enable_agent_self_rag") is True and plan.get("durable_memory_required") is False, "Formal Full8 startup switches changed")
    _require(plan.get("scope") == approved_plan["scope"], "Trusted caller scope permission changed")
    for name in ("citation_instruction_role_version", "citation_style", "max_tokens_per_section", "caller_approved_citation_role_v6", "caller_approved_fresh_v3_output_cap", "external_publication_authorized", "automatic_rerun"):
        _require(plan.get(name) == approved_plan.get(name), "Formal business/citation/publication setting changed")
    configured = plan.get("environment", {})
    approved_environment = approved_plan["environment"]
    _require(isinstance(configured, dict), "PLAN environment must be an object")
    _require(set(configured) <= set(approved_environment) | set(CACHE_VARIABLES) | {"TEMP", "TMP", "HF_DATASETS_OFFLINE", "LANGCHAIN_TRACING", "LANGCHAIN_TRACING_V2", "LANGSMITH_TRACING", "RAG_PROJECT_ROOT"}, "Unapproved PLAN environment field")
    _require(all(configured.get(name) == value for name, value in approved_environment.items() if name != "DATA_ROOT"), "Formal model/route/Gate/environment setting changed")
    validate_environment_preflight(contract, worker_stage=_worker_stage)
    validate_shared_dependencies(contract, check_acl=not _worker_stage)
    _require(Path(plan["catalog_root"]).resolve() == ROOT, "Unapproved catalog ROOT")
    project_path = plan.get("project_input_path", plan.get("project_input"))
    _require(isinstance(project_path, str) and _under(project_path, Path(contract["prep_root"]) / "inputs"), "Frozen project input must be inside PREP inputs")
    policy_path = _ordinary_path(plan["profile"], directory=False)
    _require(_under(policy_path, Path(contract["prep_root"]) / "inputs"), "Frozen policy must be inside PREP inputs")
    _require_input_binding(contract, project_path)
    _require_input_binding(contract, policy_path)
    project, policy = _read_json(project_path), _read_json(policy_path)
    _require(project.get("task_id") == contract["task_id"], "Project Task identity differs")
    _require(plan.get("scope", {}).get("tenant_id") == project.get("tenant_id"), "Trusted scope tenant mismatch")
    limits = policy.get("canonical_budget") or policy.get("metadata", {}).get("canonical_budget", {})
    _require(all(limits.get(key) == value for key, value in CANONICAL.items()), "Canonical Budget values changed")
    projection = contract["projection"]
    for name, expected in (("original_project", ORIGINAL_PROJECT_SHA256), ("original_policy", ORIGINAL_POLICY_SHA256)):
        original = _ordinary_path(projection[name], directory=False)
        _require(_under(original, Path(contract["prep_root"]) / "inputs"), "Original input copy must be inside PREP inputs")
        _require_input_binding(contract, original)
        _require(sha256_file(original) == expected, "Original business input provenance differs")
    if "project_projection_profile" not in projection:
        validate_input_projection(projection["original_project"], project, projection["allowed_project_paths"])
    else:
        _require(projection["project_projection_profile"] == STRICT_V3_PROJECT_PROJECTION,
                 "Unsupported ProjectInput projection profile")
        _require(projection["allowed_policy_paths"] == ["canonical_budget.policy_ref"],
                 "Strict V3 Policy projection remains identity-only")
        # The pinned Worker prefers project_input when both aliases are present.
        # Keep its selected input identical to the path validated above.
        worker_project = plan.get("project_input", project_path)
        _require(isinstance(worker_project, str)
                 and Path(worker_project).resolve() == Path(project_path).resolve(),
                 "Strict V3 Worker project input binding differs")
        _validate_strict_v3_project_projection(projection["original_project"], project,
            projection["allowed_project_paths"], task_id=contract["task_id"], run_id=contract["run_id"])
        strict_policy = _read_json(projection["original_policy"])
        strict_policy["canonical_budget"]["policy_ref"] = limits.get("policy_ref")
        _require(json.dumps(strict_policy, sort_keys=True, ensure_ascii=False)
                 == json.dumps(policy, sort_keys=True, ensure_ascii=False),
                 "Strict V3 Policy changed outside the identity-only reference")
    validate_input_projection(projection["original_policy"], policy, projection["allowed_policy_paths"])
    if "canonical_budget.policy_ref" in projection["allowed_policy_paths"]:
        old_ref = _read_json(projection["original_policy"])["canonical_budget"]["policy_ref"]
        new_ref = old_ref.replace("rc1-full8-real-e2e-20261020-r01", contract["run_id"])
        _require(new_ref != old_ref and limits.get("policy_ref") == new_ref, "Policy reference is not an identity-only Run projection")
    # The entire business payload is compared above, including native chapter
    # schema. Require the eight formal titles without inventing a new schema.
    project_text = json.dumps(project, ensure_ascii=False)
    _require(all(title in project_text for title in SECTIONS), "Formal eight chapters are missing")
    _require(plan.get("user_input") == project.get("user_query"), "Business query differs from frozen project")
    validate_rag_manifest(contract)
    _require(_ordinary_path(ROOT / "vector_store", directory=True).is_dir(), "Existing shared vector_store prerequisite missing")
    request = contract["request_contract"]
    _require(request.get("base_url") == "https://api.deepseek.com" and request.get("path") == "/chat/completions" and request.get("model") == "deepseek-v4-flash", "Unapproved Provider/model/endpoint")
    _positive_integer(request.get("max_tokens"), "Frozen output limit")
    _require(request["max_tokens"] == 65536, "Formal profile output limit changed")
    if require_authorization:
        _require(authorization_path is not None, "Separate one-run authorization is required")
        authorization = _read_json(_ordinary_path(authorization_path, directory=False))
        _reject_placeholders(authorization)
        _require(authorization.get("authorized") is True and authorization.get("one_run_only") is True, "No one-run approval")
        _require(authorization.get("contract_sha256") == sha256_file(contract_path), "Authorization is not bound to exact contract SHA")
        _require(all(authorization.get(name) == contract.get(name) for name in ("task_id", "run_id", "physical_attempt_limit")), "Authorization identity/N differs")
        _require(authorization.get("accept_billing_hard_cap_unverified") is True and authorization.get("accept_e1_limitations") is True, "Explicit remaining-risk/E1 acceptance absent")
    contract["_contract_path"] = str(contract_path)
    contract["_contract_sha256"] = sha256_file(contract_path)
    return contract


def _require_config_source_policy(*, worker_stage=False):
    # The launcher parent imports no application config. The owned child must
    # select process-only sources before either supported config module alias.
    selected = os.environ.get(CONFIG_SOURCE_POLICY_ENV)
    _require(selected is None and not worker_stage or selected == CONFIG_SOURCE_POLICY,
             "Full8 requires the process-only configuration source policy")
    if worker_stage:
        _require(not any(name in sys.modules for name in ("core.config", "backend.core.config")),
                 "Application configuration was imported before the approved H7/config boundary")


def install_dotenv_read_guard():
    """Deny the exact shared dotenv read, including later file appearance.

    Process-only configuration never needs a dotenv read or replacement.
    It is installed only in the owned Worker before any production import.
    """
    blocked = os.path.normcase(os.path.abspath(DOTENV_PATH))
    def audit(event, arguments):
        if event == "open" and arguments and isinstance(arguments[0], (str, bytes, os.PathLike)):
            candidate = os.path.normcase(os.path.abspath(os.fsdecode(arguments[0])))
            _require(candidate != blocked, "Shared dotenv read refused")
    sys.addaudithook(audit)


def _environment_baseline(contract):
    path = _ordinary_path(Path(contract["prep_root"]) / ENVIRONMENT_BASELINE_RELATIVE, directory=False)
    _require_input_binding(contract, path)
    baseline = _read_json(path)
    _require(isinstance(baseline, dict) and set(baseline) == {
        "environment", "credential_presence", "source_sha256", "input_sha256", "app_settings", "runtime_options"
    }, "Prepared environment baseline fields differ")
    _require(all(isinstance(baseline[name], dict) for name in baseline), "Prepared environment baseline must contain objects")
    _require(set(baseline["credential_presence"]) == {"DEEPSEEK_API_KEY"}
             and baseline["credential_presence"]["DEEPSEEK_API_KEY"] is True, "Selected process credential presence is required")
    for name, value in baseline["environment"].items():
        _require(isinstance(name, str) and not _SECRET_NAME.search(name) and isinstance(value, str), "Secret/invalid environment baseline field")
        _require(not re.search(r"[A-Za-z][A-Za-z0-9+.-]*://[^/\s]*@", value), "Credential-bearing baseline URL refused")
    plan = _read_json(contract["plan_path"])
    expected_sources = {path.resolve() for path in ENVIRONMENT_SOURCE_FILES}
    expected_inputs = {path.resolve() for path in ENVIRONMENT_INPUT_FILES} | {Path(plan["profile"]).resolve()}
    for name, expected, outer_name in (("source_sha256", expected_sources, "source_sha256"),
                                       ("input_sha256", expected_inputs, "input_sha256")):
        actual = {Path(key if Path(key).is_absolute() else ROOT / key).resolve(): value for key, value in baseline[name].items()}
        _require(len(actual) == len(baseline[name]) and set(actual) == expected, "Effective environment consumer/member set differs")
        outer = {Path(key if Path(key).is_absolute() else ROOT / key).resolve(): value for key, value in contract[outer_name].items()}
        _require(all(outer.get(path) == digest for path, digest in actual.items()), "Environment consumer is not bound by existing source/input manifest")
        _verify_manifest({str(path): digest for path, digest in actual.items()}, source=name == "source_sha256")
    # The baseline is an expectation, not permission to choose new consumers.
    settings, options = baseline["app_settings"], baseline["runtime_options"]
    _require(settings.get("prompt_root") == str(ROOT / "prompts")
             and settings.get("default_scheme_prompt_id") == "scheme_generation_v1"
             and settings.get("model_capabilities_path") == str(ROOT / "config/model_capabilities.json"),
             "Prepared Prompt/capability selection differs from frozen source defaults")
    selected = {
        "rag_static_retrieval_spec_file": str(ROOT / "backend/rag/config/static_retrieval_v1.yaml"),
        "rag_intent_policy_file": str(ROOT / "backend/rag/config/intent_policy_v1.yaml"),
        "rag_retrieval_gate_policy_file": str(ROOT / "backend/rag/config/retrieval_gate_policy_v1.yaml"),
        "grounded_generation_policy_file": str(Path(plan["profile"]).resolve()),
        "rag_project_root": str(Path(contract["rag_root"]).resolve()),
        "section_max_concurrency": 1, "enable_semantic_gate": False, "use_real_rag": True,
        "enable_agent_self_rag": True, "durable_memory_required": False,
    }
    _require(all(type(options.get(name)) is type(value) and options[name] == value for name, value in selected.items()),
             "Prepared RAG/Gate/concurrency selection differs")
    return baseline, path


def _validate_environment_paths(contract, environment, *, worker_stage):
    prep = _ordinary_path(contract["prep_root"], directory=True)
    directories = {prep, _ordinary_path(prep / "tmp", directory=True), _ordinary_path(prep / "cache", directory=True)}
    for name in CACHE_VARIABLES:
        path = _ordinary_path(environment[name], directory=True)
        _require(_under(path, prep / "cache"), "Cache environment path escapes PREP")
        directories.add(path)
    if not worker_stage:
        _require_protected_directory(prep)
        for path in directories - {prep}:
            for candidate in (path, *path.parents):
                if candidate == prep:
                    break
                _require_protected_directory(candidate, require_protected=False)
    _require(bool(environment.get("SYSTEMROOT")) and bool(environment.get("PATH")), "Required Windows loader environment is absent")
    system_root = _ordinary_path(environment["SYSTEMROOT"], directory=True)
    if environment.get("WINDIR"):
        _require(Path(environment["WINDIR"]).resolve() == system_root, "Windows loader roots disagree")
    trusted = (system_root, Path(sys.prefix).resolve(), Path(sys.base_prefix).resolve())
    for item in environment["PATH"].split(os.pathsep):
        _require(bool(item) and Path(item).is_absolute(), "Empty/relative loader search entry refused")
        path = _ordinary_path(item, directory=True)
        _require(any(_under(path, root) for root in trusted), "Unapproved loader search directory")
    if environment.get("COMSPEC"):
        path = _ordinary_path(environment["COMSPEC"], directory=False, hardlinks=True)
        _require(_under(path, system_root), "Unapproved command processor source")
    for name in ("USERPROFILE", "APPDATA", "LOCALAPPDATA"):
        if environment.get(name):
            _ordinary_path(environment[name], directory=True)


def validate_environment_preflight(contract, environment=None, *, worker_stage=False):
    """Bind the actual child mapping before resume; import no application/SDK."""
    if worker_stage:
        install_dotenv_read_guard()
    _require_config_source_policy(worker_stage=worker_stage)
    baseline, path = _environment_baseline(contract)
    actual = build_child_environment(contract) if environment is None else dict(environment)
    names = {name.upper() for name in actual}
    _require(len(names) == len(actual), "Case-ambiguous environment refused")
    actual = {name.upper(): value for name, value in actual.items()}
    # Supplied environments are checked against the builder too; a baseline
    # cannot authorize an extra Proxy, Prompt, native key or credential.
    _require(actual == build_child_environment(contract, base_environment=actual), "Supplied child environment differs from approved construction")
    nonsecret = {name: value for name, value in actual.items() if name != "DEEPSEEK_API_KEY"}
    presence = {"DEEPSEEK_API_KEY": bool(actual.get("DEEPSEEK_API_KEY"))}
    _require(nonsecret == baseline["environment"] and presence == baseline["credential_presence"], "Actual child environment differs from exact prepared baseline")
    _validate_environment_paths(contract, actual, worker_stage=worker_stage)
    _require(actual.get(CONFIG_SOURCE_POLICY_ENV) == CONFIG_SOURCE_POLICY, "Child configuration source policy differs")
    _require(presence["DEEPSEEK_API_KEY"], "Local Provider credential missing")
    return {"baseline_path": str(path), "baseline_sha256": sha256_file(path), "environment": nonsecret,
            "credential_presence": presence, "dotenv": "PROCESS_ENV_DEFAULTS_V1_READ_DENIED_IN_WORKER",
            "consumer_sources": "EXACT_SOURCE_AND_INPUT_SHA_BOUND", "application_config": "NOT_IMPORTED",
            "native_model_execution": "NOT_RUN", "os_read_only_guarantee": False}


def _post_h7_environment(environment):
    """Recognize only the delta made by the unchanged, pinned H7 helper."""
    import ast
    _require(sha256_file(WORKER) == WORKER_SHA256, "Pinned Worker source drift")
    assignments = {}
    for node in ast.parse(WORKER.read_text(encoding="utf-8")).body:
        if isinstance(node, ast.Assign) and len(node.targets) == 1 and isinstance(node.targets[0], ast.Name):
            assignments[node.targets[0].id] = node.value
    paths = {}
    for name in ("NATIVE_HELPER", "NATIVE_MANIFEST"):
        value, digest = assignments.get(name), assignments.get(name + "_SHA256")
        _require(isinstance(value, ast.Call) and isinstance(value.func, ast.Name) and value.func.id == "Path"
                 and len(value.args) == 1 and isinstance(value.args[0], ast.Constant) and isinstance(value.args[0].value, str)
                 and isinstance(digest, ast.Constant) and re.fullmatch(r"[0-9a-f]{64}", str(digest.value)), "Pinned H7 constant layout differs")
        path = _ordinary_path(value.args[0].value, directory=False)
        _require(sha256_file(path) == digest.value, "Pinned H7 helper/manifest source drift")
        paths[name] = path
    overlay = _ordinary_path(_read_json(paths["NATIVE_MANIFEST"])["overlay_root"], directory=True)
    actual = dict(environment)
    _require(actual.get("PYTHONPATH") == str(overlay), "Post-H7 loader delta differs from exact approved overlay")
    actual.pop("PYTHONPATH")
    return actual, {"PYTHONPATH": str(overlay), "source": str(paths["NATIVE_MANIFEST"]),
                    "source_sha256": sha256_file(paths["NATIVE_MANIFEST"]), "extra_search_entries_allowed": False}


def runtime_configuration_precheck(contract):
    """Resolve actual config only in provision, after the original H7 call."""
    before_h7, delta = _post_h7_environment(os.environ)
    validate_environment_preflight(contract, environment=before_h7, worker_stage=True)
    baseline, path = _environment_baseline(contract)
    from core.config import CONFIG_SOURCE_POLICY as actual_policy, get_settings
    _require(actual_policy == CONFIG_SOURCE_POLICY, "Actual configuration source policy differs")
    from bootstrap.runtime_options import RuntimeOptions
    from dataclasses import asdict
    settings = get_settings(reload=True)
    _require(get_settings() is settings, "Configuration cache did not retain the validated snapshot")
    options = RuntimeOptions.resolve(settings, ROOT, enable_agent_self_rag=True, durable_memory_required=False)
    observed_settings = settings.as_dict()
    observed_options = json.loads(json.dumps(asdict(options), default=str))
    canonical = lambda value: json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    _require(canonical(observed_settings) == canonical(baseline["app_settings"]), "Actual application settings differ from exact environment baseline")
    _require(canonical(observed_options) == canonical(baseline["runtime_options"]), "Actual RuntimeOptions differ from exact environment baseline")
    return {"baseline_path": str(path), "baseline_sha256": sha256_file(path),
            "post_h7_environment_delta": delta, "config_source_policy": actual_policy,
            "application_config": "ACTUAL_GET_SETTINGS_RELOAD_AND_CACHE_VERIFIED",
            "runtime_options": "ACTUAL_RESOLVE_VERIFIED", "settings": observed_settings, "options": observed_options,
            "credential_values_persisted": False, "actual_native_model_execution": "NOT_ASSESSED_BY_CONFIG_CHECK"}


def build_child_environment(contract, base_environment=None):
    source = os.environ if base_environment is None else base_environment
    # Windows environment names are case insensitive. Refuse an ambiguous
    # supplied mapping instead of choosing whichever spelling was iterated last.
    names = {str(name).upper(): name for name in source}
    _require(len(names) == len(source), "Case-ambiguous parent environment refused")
    if CONFIG_SOURCE_POLICY_ENV in names:
        _require(source[names[CONFIG_SOURCE_POLICY_ENV]] == CONFIG_SOURCE_POLICY,
                 "Parent configuration source policy differs")
    environment = {name: str(source[names[name]]) for name in (*_OS_ENVIRONMENT, "DEEPSEEK_API_KEY") if name in names}
    plan = _read_json(contract["plan_path"]) if "plan_path" in contract else {}
    configured = plan.get("environment", {})
    _require(isinstance(configured, dict), "PLAN environment must be an object")
    if configured:
        _require(sha256_file(ORIGINAL_PLAN) == ORIGINAL_PLAN_SHA256, "Original PLAN provenance drift")
        approved = _read_json(ORIGINAL_PLAN)["environment"]
        allowed = set(approved) | set(CACHE_VARIABLES) | {"TEMP", "TMP", "HF_DATASETS_OFFLINE", "LANGCHAIN_TRACING", "LANGCHAIN_TRACING_V2", "LANGSMITH_TRACING", "RAG_PROJECT_ROOT"}
        _require(set(configured) <= allowed, "Unapproved PLAN environment field")
        _require(all(str(value) == str(approved[name]) for name, value in configured.items()
                     if name in approved and name != "DATA_ROOT"), "Formal model/route/Gate/environment setting changed")
    prep = Path(contract["prep_root"]).resolve()
    fixed = {variable: str(prep / "cache" / variable.lower()) for variable in CACHE_VARIABLES}
    fixed.update(RAG_CONFIG_SOURCE_POLICY=CONFIG_SOURCE_POLICY, TMPDIR=str(prep / "tmp"), TEMP=str(prep / "tmp"), TMP=str(prep / "tmp"),
                 PYTHONDONTWRITEBYTECODE="1", HF_HUB_OFFLINE="1", TRANSFORMERS_OFFLINE="1", HF_DATASETS_OFFLINE="1",
                 LANGCHAIN_TRACING="false", LANGCHAIN_TRACING_V2="false", LANGSMITH_TRACING="false",
                 USE_REAL_RAG_TOOL="true", RAG_PROJECT_ROOT=str(Path(contract["rag_root"]).resolve()))
    if "output_root" in contract:
        fixed["DATA_ROOT"] = str(Path(contract["output_root"]).resolve())
    if "profile" in plan:
        fixed["GROUNDED_GENERATION_POLICY_FILE"] = str(Path(plan["profile"]).resolve())
    for name in _NETWORK_ENVIRONMENT | _NATIVE_ENVIRONMENT | _CONFIGURATION_ENVIRONMENT:
        if name in names and name not in fixed:
            value = str(source[names[name]])
            _require(not value or (name in configured and value == str(configured[name])), "Unapproved parent configuration/network/native override")
    for name, value in configured.items():
        _require(re.fullmatch(r"[A-Z][A-Z0-9_]*", name) is not None and not _SECRET_NAME.search(name), "Secret/invalid PLAN environment field")
        _require(not re.search(r"[A-Za-z][A-Za-z0-9+.-]*://[^/\s]*@", str(value)), "Credential-bearing PLAN URL refused")
        # The old Worker reapplies its PLAN environment after import. Refuse a
        # conflicting value instead of relying on our inherited override.
        if name in fixed:
            _require(str(value) == fixed[name], "PLAN overrides isolated child environment")
        environment[name] = str(value)
    environment.update(fixed)
    # Provider variables come from an existing process credential only. Never
    # read, print or rewrite the shared .env to obtain a secret.
    return environment


@contextmanager
def diagnostic_redaction(environment=None, *, include_application=False, guard_streams=True):
    """Redact owned diagnostics without modifying credentials or HTTP payloads.

    The application seam is entered only from provision, after native H7. The
    outer stream guard is stdlib-only and also covers early native failures.
    """
    from urllib.parse import unquote, urlsplit
    environment = os.environ if environment is None else environment
    secrets = {str(environment.get(name, "")) for name in ("DEEPSEEK_API_KEY", "OPENAI_API_KEY")}
    for name in ("HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "OPENAI_PROXY", "http_proxy", "https_proxy", "all_proxy"):
        value = environment.get(name, "")
        try:
            url = urlsplit(value)
            if url.password:
                secrets.update((url.password, unquote(url.password), value, url.netloc))
        except ValueError:
            # Malformed proxy construction is still refused by the SDK. Keep
            # its whole raw value out of diagnostics even on that error path.
            if "@" in value:
                secrets.add(value)
    secrets.discard("")
    ordered = sorted(secrets, key=len, reverse=True)
    def redact(value):
        result = str(value)
        for secret in ordered:
            result = result.replace(secret, "<redacted>")
        return result
    class Stream:
        def __init__(self, stream):
            self.stream = stream
        def write(self, value):
            self.stream.write(redact(value))
            return len(value)
        def __getattr__(self, name):
            return getattr(self.stream, name)
    before = sys.stdout, sys.stderr
    module = original = None
    if include_application:
        from core import secret_redaction
        module, original = secret_redaction, secret_redaction.redact_sensitive_text
        def application_redact(value, *, secrets=()):
            return original(value, secrets=(*secrets, *ordered))
        module.redact_sensitive_text = application_redact
    if guard_streams:
        sys.stdout, sys.stderr = Stream(sys.stdout), Stream(sys.stderr)
    try:
        yield
    finally:
        if guard_streams:
            sys.stdout, sys.stderr = before
        if module is not None:
            module.redact_sensitive_text = original


def _cancellation(reason):
    from core.runtime.execution_control import WorkflowExecutionCancelled
    return WorkflowExecutionCancelled(reason)


class SingleRunAttemptLimiter:
    def __init__(self, task_id, run_id, limit, *, audit_path=None, deadline=None, request_contract=None):
        _positive_integer(limit, "Physical attempt limit N")
        _require(task_id not in HISTORICAL_IDS and run_id not in HISTORICAL_IDS and bool(task_id) and bool(run_id), "New fixed Task/Run required")
        self.task_id, self.run_id, self.limit = task_id, run_id, limit
        self.used, self.stopped, self.reason, self.sequence = 0, False, None, 0
        self._lock = threading.RLock()
        self.audit_path = Path(audit_path) if audit_path else None
        self.deadline = deadline
        self.request_contract = dict(request_contract or {"base_url": "https://api.deepseek.com", "path": "/chat/completions", "model": "deepseek-v4-flash", "max_tokens": 65536})

    def stop(self, reason):
        with self._lock:
            if not self.stopped:
                self.stopped, self.reason = True, str(reason)
            return self.snapshot()

    def snapshot(self):
        with self._lock:
            return {"task_id": self.task_id, "run_id": self.run_id, "limit": self.limit, "used": self.used, "stopped": self.stopped, "reason": self.reason, "sequence": self.sequence, "meaning": "pre-transport HTTP permits; not successful billing events", "restart_supported": False}

    def _validate_request(self, request):
        expected, url = self.request_contract, request.url
        _require(request.method == "POST", "Only approved POST allowed")
        _require(url.scheme == "https" and url.host == "api.deepseek.com" and url.port in (None, 443) and url.path == expected["path"] and not url.query and not url.fragment and not url.userinfo, "Final Provider URL differs")
        payload = json.loads(request.content)
        _require(isinstance(payload, dict), "JSON request object required")
        _require(payload.get("model") == expected["model"], "Final model differs")
        _require(payload.get("thinking") == {"type": "disabled"}, "Thinking contract differs")
        _require(payload.get("stream", False) is False and type(payload.get("n", 1)) is int and payload.get("n", 1) == 1, "Streaming/multiple results refused")
        _require(type(payload.get("max_tokens")) is int and 0 < payload["max_tokens"] <= expected["max_tokens"], "Final output parameter differs")
        _require("max_completion_tokens" not in payload and not payload.get("tools") and not payload.get("functions") and not payload.get("web_search_options"), "Unapproved output/tool/paid service field")
        allowed = {"model", "messages", "temperature", "max_tokens", "thinking", "stream", "n", "stop", "top_p", "frequency_penalty", "presence_penalty", "response_format", "seed", "logprobs", "top_logprobs"}
        _require(set(payload) <= allowed and isinstance(payload.get("messages"), list), "Unknown final request shape")

    def before_send(self, request):
        with self._lock:
            if self.deadline is not None and time.monotonic() >= self.deadline:
                self.stop("worker dispatch deadline reached")
            if self.stopped:
                raise _cancellation(self.reason or "Physical dispatch stopped")
            try:
                self._validate_request(request)
            except Exception:
                self.stop("Final HTTP request contract violation")
                raise _cancellation(self.reason) from None
            if self.used >= self.limit:
                self.stop("Physical HTTP permit limit reached")
                raise _cancellation(self.reason)
            self.used += 1
            self.sequence += 1
            if self.audit_path is not None:
                try:
                    _append(self.audit_path, {"event": "http_permit", "task_id": self.task_id, "run_id": self.run_id, "ordinal": self.used, "sequence": self.sequence, "utc": utc_now(), "monotonic": time.monotonic(), "endpoint": "https://api.deepseek.com" + self.request_contract["path"]})
                except BaseException:
                    self.stop("Physical permit audit write failed")
                    raise _cancellation(self.reason) from None
            if self.used == self.limit:
                self.stop("Physical HTTP permit limit reached")

    async def async_before_send(self, request):
        self.stop("Asynchronous Provider sending is not authorized")
        raise _cancellation(self.reason)


class OwnedHttpClients:
    """Temporary DeepSeek construction seam, with two owned HTTPX pools."""
    def __init__(self, limiter, *, transport=None, async_transport=None):
        self.limiter = limiter
        self.transport, self.async_transport = transport, async_transport
        self.sync_client = self.async_client = None
        self.sdk_roots, self.chat_models = [], []
        self._original = self._module = None
        self._lock = threading.RLock()
        self._construction_count = 0
        self.closure = None
        self.network_shape = None

    def _create_httpx(self, kwargs):
        import httpx
        import openai
        from langchain_openai.chat_models import _client_utils as utils
        _require(not os.environ.get("OPENAI_PROXY") and not kwargs.get("openai_proxy"), "openai_proxy conflicts with owned clients; original network semantics cannot be bypassed")
        _require(kwargs.get("max_retries") == 0 and float(kwargs.get("timeout", 0)) == 300.0, "Factory SDK retries/timeout drift")
        _require(not any(kwargs.get(name) for name in ("http_client", "http_async_client", "client", "async_client", "root_client", "root_async_client")), "Second/unowned client injection refused")
        original_proxy_bypass = utils._should_bypass_socket_options_for_proxy_env(http_socket_options=None, http_client=None, http_async_client=None, openai_proxy=None)
        options = () if original_proxy_bypass else utils._resolve_socket_options(None)
        self.network_shape = {"env_proxy_bypass": original_proxy_bypass, "socket_options": [list(option) for option in options], "sdk_retries": 0, "transport_retries": 0, "follow_redirects": False, "timeout": 300.0, "trust_env": True, "max_connections": 1000, "max_keepalive_connections": 100, "keepalive_expiry": 5.0}
        if self.transport is None:
            self.sync_client = utils._build_sync_httpx_client(kwargs.get("base_url"), kwargs["timeout"], options)
        else:
            self.sync_client = openai.DefaultHttpxClient(base_url=kwargs["base_url"], timeout=kwargs["timeout"], transport=self.transport)
        if self.async_transport is None:
            self.async_client = utils._build_async_httpx_client(kwargs.get("base_url"), kwargs["timeout"], options)
        else:
            self.async_client = openai.DefaultAsyncHttpxClient(base_url=kwargs["base_url"], timeout=kwargs["timeout"], transport=self.async_transport)
        for client, hook in ((self.sync_client, self.limiter.before_send), (self.async_client, self.limiter.async_before_send)):
            client.follow_redirects = False
            client.event_hooks = {"request": [hook], "response": []}
            # Verify actual pools, including env-proxy mounts, rather than only
            # echoing the desired retry constant in a receipt.
            for transport in [client._transport, *[item for item in client._mounts.values() if item is not None]]:
                if isinstance(transport, httpx.MockTransport):
                    continue
                pool = getattr(transport, "_pool", None)
                _require(pool is not None and getattr(pool, "_retries", None) == 0, "Unverified HTTP transport retries")

    def construct_chat_model(self, **kwargs):
        with self._lock:
            _require(self._original is not None, "Owned construction seam is not installed")
            if self._construction_count:
                self.limiter.stop("Second Provider client construction refused")
                raise LaunchContractError("Exactly one approved DeepSeek client is allowed")
            self._construction_count += 1
            instance = None
            try:
                _require(kwargs.get("model") == self.limiter.request_contract["model"] and kwargs.get("base_url") == self.limiter.request_contract["base_url"], "Factory model/URL drift")
                _require(kwargs.get("extra_body") == {"thinking": {"type": "disabled"}}, "Factory thinking drift")
                self._create_httpx(kwargs)
                # Retain a partial Pydantic instance if async construction fails
                # after the sync SDK root has already been created.
                instance = self._original.__new__(self._original)
                self._original.__init__(instance, **dict(kwargs, http_client=self.sync_client, http_async_client=self.async_client))
                self.chat_models.append(instance)
                self.sdk_roots.extend([instance.root_client, instance.root_async_client])
                _require(instance.root_client._client is self.sync_client and instance.root_async_client._client is self.async_client, "SDK client ownership mismatch")
                _require(instance.max_retries == 0 and instance.root_client.max_retries == 0 and instance.root_async_client.max_retries == 0, "SDK retry drift")
                instance.cache = False
                return instance
            except BaseException:
                if instance is not None:
                    for name in ("root_client", "root_async_client"):
                        try:
                            root = getattr(instance, name, None)
                        except Exception:
                            root = None
                        if root is not None and root not in self.sdk_roots:
                            self.sdk_roots.append(root)
                self.limiter.stop("Owned Provider construction failed")
                self.close(idle=True)
                raise

    @contextmanager
    def install(self):
        from model_gateway.integrations.langchain import deepseek_chat_model
        _require(self._module is None, "Construction seam already installed")
        self._module = deepseek_chat_model
        self._original = deepseek_chat_model.DeepSeekChatOpenAI
        _require(isinstance(self._original, type) and self._original.__name__ == "DeepSeekChatOpenAI", "DeepSeek SDK constructor symbol drift")
        deepseek_chat_model.DeepSeekChatOpenAI = self.construct_chat_model
        try:
            yield self
        finally:
            deepseek_chat_model.DeepSeekChatOpenAI = self._original
            self._module = None

    def close(self, *, idle=True):
        if self.closure is not None and self.closure.get("closed"):
            return self.closure
        if not idle:
            self.limiter.stop("Provider did not become idle; graceful closure unverified")
            self.closure = {"graceful": False, "closed": False, "errors": ["active calls remain"], "forced_job_reap_required": True}
            return self.closure
        errors = []
        asynchronous = []
        sdk_closes = []
        for root in self.sdk_roots:
            close = getattr(root, "close", None)
            if close is None:
                errors.append("owned SDK root has no close method")
                continue
            if asyncio.iscoroutinefunction(close):
                asynchronous.append(close)
            else:
                record = {"kind": "sync", "called": True, "completed": False}
                sdk_closes.append(record)
                try:
                    close()
                    record["completed"] = True
                except BaseException as exc:
                    errors.append(type(exc).__name__)
        if self.sync_client is not None:
            try:
                self.sync_client.close()
            except BaseException as exc:
                errors.append(type(exc).__name__)
        async def close_async():
            for close in asynchronous:
                record = {"kind": "async", "called": True, "awaited": True, "completed": False}
                sdk_closes.append(record)
                try:
                    await close()
                    record["completed"] = True
                except BaseException as exc:
                    errors.append(type(exc).__name__)
            if self.async_client is not None:
                try:
                    await self.async_client.aclose()
                except BaseException as exc:
                    errors.append(type(exc).__name__)
        try:
            asyncio.get_running_loop()
        except RuntimeError:
            try:
                asyncio.run(close_async())
            except BaseException as exc:
                errors.append(type(exc).__name__)
        else:
            errors.append("active event loop prevents synchronous async-resource closure")
        closed = not errors and (self.sync_client is None or self.sync_client.is_closed) and (self.async_client is None or self.async_client.is_closed)
        self.closure = {"graceful": closed, "closed": closed, "errors": errors, "sync_sdk_roots": len(self.sdk_roots) - len(asynchronous), "async_sdk_roots": len(asynchronous), "sdk_close_calls_executed": len(sdk_closes) == len(self.sdk_roots) and all(item["completed"] for item in sdk_closes), "sdk_close_evidence": sdk_closes, "sync_httpx_close_called": self.sync_client is not None, "async_httpx_close_awaited": self.async_client is not None and not any("active event loop" in item for item in errors)}
        return self.closure


def load_pinned_worker(contract):
    _require(sha256_file(WORKER) == WORKER_SHA256, "Pinned Worker source drift")
    spec = importlib.util.spec_from_file_location("post_phase1_pinned_full8_worker", WORKER)
    _require(spec is not None and spec.loader is not None, "Pinned Worker loader unavailable")
    worker = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(worker)
    _require(worker.ROOT == ROOT and callable(worker.main), "Worker ROOT/main drift")
    worker.AUDIT = Path(contract["prep_root"]) / "audit"
    return worker


def _process_identity(pid):
    _require(os.name == "nt", "Windows process ownership required")
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    kernel.OpenProcess.restype = wintypes.HANDLE
    handle = kernel.OpenProcess(0x1000, False, int(pid))
    if not handle:
        raise OSError(ctypes.get_last_error(), "Cannot inspect owned process")
    try:
        values = [wintypes.FILETIME() for _ in range(4)]
        kernel.GetProcessTimes.argtypes = [wintypes.HANDLE, *([ctypes.POINTER(wintypes.FILETIME)] * 4)]
        if not kernel.GetProcessTimes(handle, *[ctypes.byref(item) for item in values]):
            raise OSError(ctypes.get_last_error(), "Cannot inspect process creation time")
        return {"pid": int(pid), "creation_time": (values[0].dwHighDateTime << 32) | values[0].dwLowDateTime}
    finally:
        kernel.CloseHandle.argtypes = [wintypes.HANDLE]
        kernel.CloseHandle(handle)


def exclusive_launch_intent(audit_root, task_id, run_id, nonce=None):
    intent = {"schema": SCHEMA, "task_id": task_id, "run_id": run_id, "nonce": nonce or uuid.uuid4().hex, "supervisor": _process_identity(os.getpid()), "utc": utc_now(), "monotonic": time.monotonic(), "one_worker_only": True, "restart_supported": False}
    _write_exclusive(Path(audit_root) / "LAUNCH_INTENT.json", intent)
    return intent


def validate_worker_binding(binding, nonce):
    _require(binding.get("nonce") == nonce and bool(nonce), "Worker nonce mismatch")
    _require(binding.get("worker") == _process_identity(os.getpid()), "Worker PID/creation time mismatch")
    _require(binding.get("supervisor") == _process_identity(os.getppid()), "Supervisor PID/creation time mismatch")
    current = time.monotonic()
    _require(binding["clock_start"] <= current < binding["deadline"], "Worker monotonic clock/deadline mismatch")
    return binding


class WindowsJob:
    """Supervisor-only, non-inherited kill-on-close Job for one owned tree."""
    def __init__(self):
        _require(os.name == "nt", "Windows Job is mandatory")
        import win32api
        import win32con
        import win32job
        import win32process
        self.api, self.con, self.job, self.process = win32api, win32con, win32job, win32process
        self.handle = win32job.CreateJobObject(None, "")
        try:
            win32api.SetHandleInformation(self.handle, win32con.HANDLE_FLAG_INHERIT, 0)
            info = win32job.QueryInformationJobObject(self.handle, win32job.JobObjectExtendedLimitInformation)
            info["BasicLimitInformation"]["LimitFlags"] = win32job.JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
            win32job.SetInformationJobObject(self.handle, win32job.JobObjectExtendedLimitInformation, info)
        except BaseException:
            self.handle.Close()
            raise
        self.process_handle = self.thread_handle = None
        self.pid, self.events, self.closed = None, [], False

    def start(self, argv, *, cwd=None, env=None, stdout=None, stderr=None, before_resume=None):
        _require(self.pid is None and not self.closed, "Only one owned worker may be created")
        startup = self.process.STARTUPINFO()
        changed = []
        null_input = None
        try:
            if stdout is not None or stderr is not None:
                import msvcrt
                startup.dwFlags |= self.con.STARTF_USESTDHANDLES
                for name, stream in (("hStdOutput", stdout), ("hStdError", stderr)):
                    if stream is not None:
                        fd = stream.fileno()
                        changed.append((fd, os.get_inheritable(fd)))
                        os.set_inheritable(fd, True)
                        setattr(startup, name, msvcrt.get_osfhandle(fd))
                # win32con on this installation omits STD_INPUT_HANDLE (-10).
                input_handle = self.api.GetStdHandle(-10)
                if input_handle is None or int(input_handle) in (0, -1):
                    null_input = open(os.devnull, "rb")
                    fd = null_input.fileno()
                    changed.append((fd, os.get_inheritable(fd)))
                    os.set_inheritable(fd, True)
                    input_handle = msvcrt.get_osfhandle(fd)
                startup.hStdInput = input_handle
            handles = self.process.CreateProcess(None, subprocess.list2cmdline([str(item) for item in argv]), None, None, bool(changed), self.con.CREATE_SUSPENDED | self.con.CREATE_NO_WINDOW, env, str(cwd) if cwd else None, startup)
        finally:
            for fd, previous in changed:
                os.set_inheritable(fd, previous)
            if null_input is not None:
                null_input.close()
        self.process_handle, self.thread_handle, self.pid, _ = handles
        self.events.append("created_suspended")
        try:
            self.job.AssignProcessToJobObject(self.handle, self.process_handle)
            self.events.append("assigned_to_job")
            identity = _process_identity(self.pid)
            if before_resume:
                before_resume(identity)
            self.process.ResumeThread(self.thread_handle)
            self.events.append("resumed")
            self.thread_handle.Close()
            self.thread_handle = None
            return identity
        except BaseException:
            # This handle was returned by our own suspended CreateProcess.
            # It has never executed application code and cannot be another PID.
            self.process.TerminateProcess(self.process_handle, 125)
            self.events.append("assignment_failed_no_resume")
            self.wait(timeout=5)
            self.close()
            raise

    def poll(self):
        if self.process_handle is None:
            return None
        code = self.process.GetExitCodeProcess(self.process_handle)
        return None if code == 259 else code

    def wait(self, timeout=None):
        import win32event
        millis = win32event.INFINITE if timeout is None else max(0, int(timeout * 1000))
        if win32event.WaitForSingleObject(self.process_handle, millis) == win32event.WAIT_TIMEOUT:
            raise subprocess.TimeoutExpired("owned worker", timeout)
        return self.poll()

    def process_ids(self):
        _require(not self.closed, "Closed Job cannot prove the owned tree is empty")
        kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel.QueryInformationJobObject.argtypes = [wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD, ctypes.POINTER(wintypes.DWORD)]
        # 4096 process slots, limited to this Job, never system-wide kill logic.
        buffer = ctypes.create_string_buffer(8 + ctypes.sizeof(ctypes.c_size_t) * 4096)
        length = wintypes.DWORD()
        if not kernel.QueryInformationJobObject(int(self.handle), 3, buffer, len(buffer), ctypes.byref(length)):
            raise OSError(ctypes.get_last_error(), "Cannot inspect owned Job tree")
        count = ctypes.c_uint32.from_buffer(buffer, 4).value
        _require(count <= 4096, "Owned Job query capacity exceeded")
        return list((ctypes.c_size_t * count).from_buffer(buffer, 8))

    def wait_tree_empty(self, timeout):
        """Query the owned Job until no descendants remain, within one bound."""
        deadline = time.monotonic() + max(0, timeout)
        while True:
            pids = self.process_ids()
            if not pids:
                self.events.append("owned_job_tree_empty_verified")
                return []
            if time.monotonic() >= deadline:
                raise subprocess.TimeoutExpired("owned Job descendants", timeout)
            time.sleep(min(0.02, max(0, deadline - time.monotonic())))

    def terminate(self, exit_code=124):
        if not self.closed:
            self.job.TerminateJobObject(self.handle, exit_code)
            self.events.append("owned_job_terminated")

    def close(self):
        if self.closed:
            return
        self.closed = True
        if self.handle is not None:
            self.handle.Close()
        if self.thread_handle is not None:
            self.thread_handle.Close()
            self.thread_handle = None
        if self.process_handle is not None:
            self.process_handle.Close()
            self.process_handle = None
        self.events.append("noninherited_job_handle_closed")

    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.close()


def passive_sample(pid, *, expected_creation_time=None, ledger_paths=()):
    started = time.monotonic()
    result = {"utc": utc_now(), "monotonic": started, "pid": pid, "scope": "owned process OS counters and file metadata", "status": "UNKNOWN", "internal_budget_attribution": "UNATTRIBUTED"}
    try:
        identity = _process_identity(pid)
        _require(expected_creation_time is None or identity["creation_time"] == expected_creation_time, "PID reuse")
        import psutil
        process = psutil.Process(pid)
        result.update(identity=identity, cpu=process.cpu_times()._asdict(), memory=process.memory_info()._asdict(), io=process.io_counters()._asdict(), status="OBSERVED")
    except BaseException as exc:
        result["error_type"] = type(exc).__name__
    result["ledger_metadata"] = []
    for path in ledger_paths:
        try:
            info = Path(path).stat()
            result["ledger_metadata"].append({"path": str(path), "bytes": info.st_size, "mtime_ns": info.st_mtime_ns, "status": "OBSERVED"})
        except OSError as exc:
            result["ledger_metadata"].append({"path": str(path), "status": "UNKNOWN", "error_type": type(exc).__name__})
    result["sampler_wall_seconds"] = time.monotonic() - started
    result["sampler_io_bytes"] = "UNVERIFIED"
    return result


def passive_progress(output_root, run_id):
    """Read at most one bounded trace tail; never open a business Owner."""
    path = Path(output_root) / "runs" / (run_id + "_trace.jsonl")
    result = {"path": str(path), "status": "UNKNOWN", "sampler_io_bytes": "UNVERIFIED", "internal_budget_attribution": "UNATTRIBUTED"}
    try:
        with path.open("rb") as handle:
            handle.seek(0, os.SEEK_END)
            size = handle.tell()
            handle.seek(max(0, size - 65536))
            raw = handle.read(65536)
        result["sampler_io_bytes"] = len(raw)
        result["partial_newline"] = bool(raw and not raw.endswith(b"\n"))
        lines = raw.splitlines()
        if size > len(raw) and lines:
            lines.pop(0)  # The first tail fragment may begin in mid-record.
        if result["partial_newline"] and lines:
            lines.pop()
        if lines:
            last = json.loads(lines[-1])
            result["last_complete_event"] = {name: last[name] for name in ("event_type", "timestamp", "component", "span_id", "parent_span_id") if name in last}
            result["status"] = "UNKNOWN" if result["partial_newline"] else "OBSERVED"
    except (OSError, ValueError, TypeError) as exc:
        result["error_type"] = type(exc).__name__
    return result


def optional_sample_write(audit, filename, producer, failures):
    """Only the new external sampler is optional; required audit is separate."""
    try:
        _append(Path(audit) / filename, producer())
    except Exception as exc:
        failures.append({"utc": utc_now(), "monotonic": time.monotonic(), "sample": filename, "status": "UNKNOWN", "error_type": type(exc).__name__})


def classify_terminal(exit_code, summary=None, *, forced=False):
    summary = summary or {}
    result = {"worker_exit_code": exit_code, "forced_exit": forced, "native_summary_status": summary.get("status", "UNVERIFIED"), "native_terminal_outcome": summary.get("terminal_outcome"), "technical_terminal": "UNVERIFIED", "business_quality": "UNVERIFIED", "control": "UNVERIFIED", "release": "UNVERIFIED", "human_approval": "UNVERIFIED", "human_approval_granted": False, "billing_hard_cap": "BILLING_HARD_CAP_UNVERIFIED", "exit_zero_is_business_pass": False}
    if not summary:
        return result
    try:
        # Invoked with a native summary only in the post-H7 worker. Supervisor
        # calls without a summary and never imports application/native modules.
        _require(sha256_file(ROOT / "scripts/run_demo.py") == COMPATIBILITY_SOURCE_SHA256[ROOT / "scripts/run_demo.py"], "Verified release consumer drift")
        import run_demo
        package = run_demo._read_target_release(summary)
        if package is not None:
            manifest = package.manifest
            result.update(technical_terminal=manifest.run_completion_assessment_ref.aggregate_result.value,
                          business_quality=manifest.quality_state_passthrough.value,
                          control=manifest.enforcement_action_passthrough.value,
                          release=manifest.release_mode.value,
                          release_id=manifest.release_id,
                          status_view=dict(package.status_view),
                          release_evidence="verified persisted manifest/proof/payload and supplied terminal receipt")
    except Exception as exc:
        result["release_verification_error"] = type(exc).__name__
    return result


def sampling_evidence(*, begin=None, end=None, partial_newline=False, access_error=False):
    return {"status": "UNKNOWN" if access_error or partial_newline else "OBSERVED", "right_censored": begin is not None and end is None, "duration_seconds": end - begin if begin is not None and end is not None else None, "internal_reads": "UNVERIFIED", "exclusive_budget_cost": "UNATTRIBUTED"}


def _run_worker_body(contract, binding, nonce):
    validate_worker_binding(binding, nonce)
    audit = Path(contract["prep_root"]) / "audit"
    # Recheck after suspended creation; validation still precedes H7/apps.
    _verify_manifest(contract["source_sha256"], source=True)
    _verify_manifest(contract["input_sha256"])
    validate_environment_preflight(contract, environment=os.environ, worker_stage=True)
    validate_shared_dependencies(contract, check_acl=False)
    worker = load_pinned_worker(contract)
    source_manifest = {str(Path(name if Path(name).is_absolute() else ROOT / name).resolve().relative_to(ROOT)).replace("\\", "/"): digest for name, digest in contract["source_sha256"].items() if _under(Path(name) if Path(name).is_absolute() else ROOT / name, ROOT)}
    prepared_manifest = audit / "SOURCE_MANIFEST_BEFORE.json"
    if prepared_manifest.exists():
        _require(_read_json(prepared_manifest) == source_manifest, "Prepared Worker source manifest differs from approved source binding")
    else:
        _write_exclusive(prepared_manifest, source_manifest)
    limiter = SingleRunAttemptLimiter(contract["task_id"], contract["run_id"], contract["physical_attempt_limit"], audit_path=audit / "HTTP_PERMITS.jsonl", deadline=binding["dispatch_deadline"], request_contract=contract["request_contract"])
    clients = OwnedHttpClients(limiter)
    previous = sys.modules.get("credential_bootstrap")
    credential = types.ModuleType("credential_bootstrap")
    installed = None
    redaction = None
    installed_entered = redaction_entered = False
    early_error = None
    exit_code = 1
    def provision():
        nonlocal installed, redaction, installed_entered, redaction_entered
        _require(bool(os.environ.get("DEEPSEEK_API_KEY")), "Local Provider credential missing")
        configuration = runtime_configuration_precheck(contract)
        _write_exclusive(audit / "EFFECTIVE_ENVIRONMENT_CHECK.json", configuration)
        # The old Worker has already executed H7 before this seam. Only now
        # may the SDK, HTTPX, application-dependent cancellation be imported.
        redaction = diagnostic_redaction(include_application=True, guard_streams=False)
        redaction.__enter__()
        redaction_entered = True
        installed = clients.install()
        installed.__enter__()
        installed_entered = True
        return {"present": True, "persisted_by_runner": False, "run_controls_installed": True}
    credential.provision = provision
    sys.modules["credential_bootstrap"] = credential
    try:
        with diagnostic_redaction():
            exit_code = int(worker.main(contract["plan_path"]))
    except BaseException as exc:
        early_error = {"type": type(exc).__name__, "message": "Worker failed; raw exception text suppressed to preserve credentials"}
        exit_code = 1
    finally:
        limiter.stop("Worker has returned or failed")
        for context, entered in ((installed, installed_entered), (redaction, redaction_entered)):
            if entered:
                try:
                    context.__exit__(None, None, None)
                except BaseException as exc:
                    early_error = {"type": type(exc).__name__, "message": "Owned startup seam restoration failed"}
                    exit_code = 1
        if previous is None:
            sys.modules.pop("credential_bootstrap", None)
        else:
            sys.modules["credential_bootstrap"] = previous
        execution_path = Path(contract["output_root"]) / "execution.json"
        try:
            execution = _read_json(execution_path) if execution_path.is_file() else {}
        except (OSError, ValueError, TypeError) as exc:
            execution = {}
            early_error = {"type": type(exc).__name__, "message": "Native resource evidence unreadable; no graceful close claim"}
            exit_code = 1
        resources = execution.get("resources", [])
        idle = bool(resources) and all(item.get("active_workers") == 0 and item.get("gateway_active_calls") == 0 and item.get("gateway_lifecycle_state") == "closed" and not item.get("close_error") for item in resources)
        if not clients.sdk_roots:
            idle = True
        closure = clients.close(idle=idle)
        if not closure["closed"]:
            exit_code = 1
        summary_path = Path(contract["output_root"]) / "summary.json"
        try:
            summary = _read_json(summary_path) if summary_path.is_file() else {}
            assessment = classify_terminal(exit_code, summary)
        except (OSError, ValueError, TypeError):
            assessment = classify_terminal(exit_code)
        _write_exclusive(audit / "WORKER_EXIT.json", {"utc": utc_now(), "exit_code": exit_code, "early_error": early_error, "limiter": limiter.snapshot(), "owned_resources": closure, "network_shape": clients.network_shape, "assessment": assessment, "automatic_rerun": False, "native_terminal_modified": False})
    return exit_code


def run_worker(contract, binding, nonce):
    """Cover source, H7, credentials and application failures with raw exit data."""
    try:
        return _run_worker_body(contract, binding, nonce)
    except BaseException as exc:
        audit = Path(contract["prep_root"]) / "audit"
        _write_exclusive(audit / "WORKER_EARLY_ERROR.json", {"utc": utc_now(), "exit_code": 1, "error_type": type(exc).__name__, "message": "Early Worker failure; exception text suppressed to preserve credentials", "native_terminal_modified": False, "automatic_rerun": False})
        return 1


def supervise(contract, authorization_path):
    audit = Path(contract["prep_root"]) / "audit"
    # These checks precede launch intent, owned process creation and HTTP
    # permits. Reuse the exact same environment for suspended creation/resume.
    worker_environment = build_child_environment(contract)
    validate_environment_preflight(contract, environment=worker_environment)
    integrity_before = validate_shared_dependencies(contract)
    _write_exclusive(audit / "DEPENDENCY_INTEGRITY_PRECHECK.json", integrity_before)
    intent = exclusive_launch_intent(audit, contract["task_id"], contract["run_id"])
    start = time.monotonic()
    watchdog = contract["watchdog"]
    dispatch_deadline = start + watchdog["observation_seconds"]
    deadline = dispatch_deadline + watchdog["forensic_seconds"] + watchdog["reap_seconds"]
    binding = {**intent, "clock_start": start, "dispatch_deadline": dispatch_deadline, "deadline": deadline}
    argv = [sys.executable, "-I", "-B", str(Path(__file__).resolve()), "--contract", contract["_contract_path"], "--authorization", str(authorization_path), "--internal-worker", "--nonce", intent["nonce"]]
    _write_exclusive(audit / "LAUNCH_COMMAND.json", {"argv": argv, "cwd": str(ROOT), "contract_sha256": contract["_contract_sha256"], "cache_environment": {key: value for key, value in worker_environment.items() if key in CACHE_VARIABLES or key in ("TMPDIR", "TEMP", "TMP", "HF_HUB_OFFLINE", "TRANSFORMERS_OFFLINE", "PYTHONDONTWRITEBYTECODE")}, "deadline": deadline, "dispatch_deadline": dispatch_deadline})
    forced, exit_code, error = False, None, None
    owned, remaining, reap_error = None, "UNVERIFIED", None
    job_close_error = None
    sampling_failures = []
    try:
        owned = WindowsJob()
        def before_resume(identity):
            validate_environment_preflight(contract, environment=worker_environment)
            validate_shared_dependencies(contract)
            binding["worker"] = identity
            _write_exclusive(audit / "WORKER_BINDING.json", binding)
        with (audit / "WORKER_STDOUT.log").open("xb") as stdout, (audit / "WORKER_STDERR.log").open("xb") as stderr:
            identity = owned.start(argv, cwd=ROOT, env=worker_environment, stdout=stdout, stderr=stderr, before_resume=before_resume)
            next_os, next_metadata, next_usage = start, start, start
            while owned.poll() is None:
                now = time.monotonic()
                if now >= dispatch_deadline:
                    forced = True
                    # Capture metadata only; no Owner/recovery, no native-state
                    # mutation, no copying an actively-written SQLite database.
                    _write_exclusive(audit / "WATCHDOG_STOP.json", {"utc": utc_now(), "monotonic": now, "dispatch_deadline": dispatch_deadline, "deadline": deadline, "owned_pids": owned.process_ids(), "last_sample": passive_sample(identity["pid"], expected_creation_time=identity["creation_time"]), "last_progress": passive_progress(contract["output_root"], contract["run_id"]), "native_terminal": "UNVERIFIED", "right_censored": True})
                    owned.terminate(124)
                    exit_code = owned.wait(timeout=max(0.001, deadline - time.monotonic()))
                    break
                if now >= next_os:
                    optional_sample_write(audit, "OS_SAMPLES.jsonl", lambda: passive_sample(identity["pid"], expected_creation_time=identity["creation_time"]), sampling_failures)
                    next_os = now + 30
                if now >= next_metadata:
                    optional_sample_write(audit, "LEDGER_SAMPLES.jsonl", lambda: {"process": passive_sample(identity["pid"], expected_creation_time=identity["creation_time"], ledger_paths=(Path(contract["output_root"]) / "runs/canonical_budget.jsonl",)), "progress": passive_progress(contract["output_root"], contract["run_id"])}, sampling_failures)
                    next_metadata = now + 120
                if now >= next_usage:
                    optional_sample_write(audit, "USAGE_METADATA.jsonl", lambda: {"utc": utc_now(), "monotonic": now, "free_disk_bytes": __import__("shutil").disk_usage(contract["output_root"]).free, "usage_content": "deferred until writer stops"}, sampling_failures)
                    next_usage = now + 300
                time.sleep(min(0.25, max(0.001, dispatch_deadline - now)))
            if exit_code is None:
                exit_code = owned.poll()
            # Even normal parent exit must leave no owned descendants alive.
            remaining = owned.process_ids()
            if remaining:
                forced = True
                owned.terminate(126)
    except BaseException as exc:
        error = {"type": type(exc).__name__, "message": "Supervisor exception; secret-bearing text suppressed"}
        forced = True
        if owned is not None:
            owned.terminate(125)
        if owned is not None and owned.process_handle is not None:
            try:
                exit_code = owned.wait(timeout=max(0.001, deadline - time.monotonic()))
            except BaseException:
                pass
    finally:
        events = []
        if owned is not None:
            try:
                remaining = owned.process_ids()
                if remaining:
                    forced = True
                    owned.terminate(125 if error else 126)
                remaining = owned.wait_tree_empty(timeout=max(0, deadline - time.monotonic()))
            except BaseException as exc:
                reap_error = type(exc).__name__
            finally:
                try:
                    owned.close()
                except BaseException as exc:
                    job_close_error = type(exc).__name__
                events = list(owned.events)
        assessment = classify_terminal(exit_code, forced=forced)
        worker_exit = audit / "WORKER_EXIT.json"
        if worker_exit.is_file() and not forced:
            try:
                assessment = _read_json(worker_exit).get("assessment", assessment)
            except (OSError, ValueError, TypeError):
                pass
        result_code = int(exit_code) if exit_code is not None and not error and not reap_error and not (forced and exit_code == 0) else 1
        try:
            integrity = postcheck_shared_dependencies(contract, integrity_before, deadline=deadline,
                tree_reaped=remaining == [] and reap_error is None and job_close_error is None)
        except BaseException as exc:
            integrity = {"status": "INTEGRITY_POSTCHECK_UNVERIFIED", "error_type": type(exc).__name__,
                         "message": "Independent integrity check failed; original stop/assessment retained"}
        try:
            _write_exclusive(audit / "DEPENDENCY_INTEGRITY_POSTCHECK.json", integrity)
        except BaseException as exc:
            integrity = {"status": "INTEGRITY_POSTCHECK_UNVERIFIED", "receipt_error_type": type(exc).__name__,
                         "observed_check": integrity, "message": "Independent integrity receipt could not be persisted"}
        # Do not overwrite an original failure/assessment with a secondary
        # integrity result. A business zero alone is never trusted success.
        if result_code == 0 and integrity["status"] != "PASSED":
            result_code = 1
        _write_exclusive(audit / "SUPERVISOR_EXIT.json", {"utc": utc_now(), "exit_code": exit_code, "supervisor_return_code": result_code, "forced": forced, "error": error, "job_events": events, "owned_job_pids_before_close": remaining, "tree_reap_verified": remaining == [] and reap_error is None, "tree_reap_error": reap_error, "job_close_error": job_close_error, "deadline": deadline, "finished_monotonic": time.monotonic(), "assessment": assessment, "dependency_integrity": integrity, "integrity_allows_existing_assessment": integrity["status"] == "PASSED", "run_outcome_not_certified_by_integrity_check": True, "optional_sampling_failures": sampling_failures, "automatic_rerun": False, "graceful_sdk_close": "UNVERIFIED" if forced else "see WORKER_EXIT.json"})
    return result_code


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--contract", required=True)
    parser.add_argument("--authorization", required=True)
    parser.add_argument("--internal-worker", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument("--nonce", help=argparse.SUPPRESS)
    args = parser.parse_args(argv)
    try:
        contract = load_and_validate_launch_contract(args.contract, args.authorization, _worker_stage=args.internal_worker)
        if args.internal_worker:
            binding = _read_json(Path(contract["prep_root"]) / "audit/WORKER_BINDING.json")
            return run_worker(contract, binding, args.nonce)
        _require(args.nonce is None, "Internal nonce supplied to supervisor")
        return supervise(contract, args.authorization)
    except BaseException as exc:
        # No traceback/payload/environment: even a malformed contract or proxy
        # constructor error must not publish credentials to the console.
        print(json.dumps({"event": "LAUNCH_REFUSED", "error_type": type(exc).__name__, "business_started": False, "message": "Launch contract or owned startup failed; preserve existing evidence"}), file=sys.stderr, flush=True)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
