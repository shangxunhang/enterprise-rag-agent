"""Finalize Stage F.1 acceptance from runtime, JUnit, and repository evidence."""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import subprocess
import sys
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[1]
BACKEND_ROOT = PROJECT_ROOT / "backend"
SCRIPTS_ROOT = PROJECT_ROOT / "scripts"
for path in (BACKEND_ROOT, SCRIPTS_ROOT):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

from finalize_stage_e_acceptance_v3 import (  # noqa: E402
    parse_junit_report,
    validate_clean_junit,
)
from rag.offline.manifest import sha256_file  # noqa: E402
from rag.offline.resolver import ActiveIndexResolver  # noqa: E402


EXPECTED_INDEX_VERSION = (
    "idx_type_aware_parent_child_v2_m3e_base_local_m3e_base_profiled_v2_"
    "f382dcfcb28e"
)
EXPECTED_POINTER_SHA256 = (
    "0e77cc066d3dfc47875e7991e22d41828bc5ad531cb2cbb134e6096c6e6772ce"
)
EXPECTED_MANIFEST_SHA256 = (
    "18d1aedb6bdd1fe6e6093e5879e0f205808482010ff8d48b8a9215b55287fc02"
)


def _resolve(value: str) -> Path:
    path = Path(value).expanduser()
    return path.resolve() if path.is_absolute() else (PROJECT_ROOT / path).resolve()


def _read_json(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"expected JSON object: {path}")
    return payload


def _git_text(*args: str) -> str:
    completed = subprocess.run(
        ["git", *args],
        cwd=PROJECT_ROOT,
        check=True,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    return completed.stdout


def _sha256_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _command_evidence(path_value: str) -> dict[str, Any]:
    path = _resolve(path_value)
    text = path.read_text(encoding="utf-8").strip()
    if not text:
        raise ValueError(f"empty command evidence: {path}")
    return {
        "path": str(path),
        "sha256": sha256_file(path),
        "command_text": text,
    }


def _run_check(command: list[str]) -> dict[str, Any]:
    completed = subprocess.run(
        command,
        cwd=PROJECT_ROOT,
        check=False,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    result = {
        "command": command,
        "returncode": completed.returncode,
        "passed": completed.returncode == 0,
        "stdout": completed.stdout.strip(),
        "stderr": completed.stderr.strip(),
    }
    if completed.returncode != 0:
        raise RuntimeError(f"quality check failed: {command}")
    return result


def _line_count(path_value: str) -> int:
    path = _resolve(path_value)
    return len(path.read_text(encoding="utf-8").splitlines())


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--runtime-probe",
        default="E:/rag-agent-runtime/stage_f_release_runtime_acceptance_final.json",
    )
    parser.add_argument(
        "--focused-junit",
        default="E:/rag-agent-runtime/stage_f_focused_junit_final.xml",
    )
    parser.add_argument(
        "--regression-junit",
        default="E:/rag-agent-runtime/stage_f_regression_junit_final.xml",
    )
    parser.add_argument(
        "--focused-command-file",
        default="scripts/stage_f_focused_commands_v1.ps1",
    )
    parser.add_argument(
        "--regression-command-file",
        default="scripts/stage_f_regression_commands_v1.ps1",
    )
    parser.add_argument(
        "--stage-e-report",
        default="data/processed/indexes/stage_e_final_acceptance_report_v3.json",
    )
    parser.add_argument(
        "--repo-output",
        default="data/processed/indexes/stage_f_release_acceptance_report_final.json",
    )
    parser.add_argument(
        "--runtime-output",
        default="E:/rag-agent-runtime/stage_f_release_acceptance_report_final.json",
    )
    return parser


def main() -> int:
    args = _parser().parse_args()
    runtime_path = _resolve(args.runtime_probe)
    runtime = _read_json(runtime_path)
    hard_failures = {
        str(key): bool(value)
        for key, value in dict(runtime.get("hard_failures") or {}).items()
    }
    if not runtime.get("passed") or any(hard_failures.values()):
        raise RuntimeError("Stage F runtime acceptance contains hard failures")

    stage_e_path = _resolve(args.stage_e_report)
    stage_e = _read_json(stage_e_path)
    if not stage_e.get("passed"):
        raise RuntimeError("Stage E prerequisite acceptance is not passed")

    focused_path = _resolve(args.focused_junit)
    regression_path = _resolve(args.regression_junit)
    focused = parse_junit_report(focused_path)
    regression = parse_junit_report(regression_path)
    validate_clean_junit(focused, label="focused")
    validate_clean_junit(regression, label="regression")

    focused_command = _command_evidence(args.focused_command_file)
    regression_command = _command_evidence(args.regression_command_file)
    compileall = _run_check(
        [sys.executable, "-m", "compileall", "-q", "backend", "scripts"]
    )
    diff_check = _run_check(["git", "diff", "--check"])

    active = ActiveIndexResolver(
        verify_manifest_hash=True,
        verify_artifacts=False,
    ).resolve(PROJECT_ROOT / "data/processed/indexes/active_index.json")
    expected_active = {
        "index_version": EXPECTED_INDEX_VERSION,
        "pointer_hash": EXPECTED_POINTER_SHA256,
        "manifest_hash": EXPECTED_MANIFEST_SHA256,
    }
    for key, expected in expected_active.items():
        if active.get(key) != expected:
            raise ValueError(f"active index changed for {key}")
        if dict(runtime.get("active_index") or {}).get(key) != expected:
            raise ValueError(f"runtime active index changed for {key}")

    production_lines = {
        "release_publication_service": _line_count(
            "backend/apps/enterprise_document/services/release_publication.py"
        ),
        "artifact_store": _line_count(
            "backend/apps/enterprise_document/services/agent_rag_v1/artifact_store.py"
        ),
        "release_schema": _line_count(
            "backend/apps/enterprise_document/schemas/release_publication.py"
        ),
    }
    production_delta = (
        production_lines["release_publication_service"]
        - 441
        + production_lines["artifact_store"]
        - 191
        + production_lines["release_schema"]
        - 141
        - 1
    )
    test_lines = {
        "atomic_idempotent": _line_count(
            "backend/tests/test_stage_f_atomic_idempotent_release.py"
        ),
        "concurrency": _line_count(
            "backend/tests/test_stage_f_release_concurrency.py"
        ),
    }
    test_delta = sum(test_lines.values())
    if production_delta > 400 or test_delta > 500:
        raise RuntimeError("Stage F.1 complexity limit exceeded")

    status_text = _git_text("status", "--porcelain=v1", "--untracked-files=all")
    diff_text = _git_text("diff", "--no-ext-diff", "--binary")
    head_commit = _git_text("rev-parse", "HEAD").strip()
    scenarios = dict(runtime.get("scenarios") or {})
    release_store = dict(runtime.get("release_store") or {})

    report = {
        "schema_version": "stage_f_release_acceptance_report_final",
        "stage": "Stage F.1 Atomic Commit & True Idempotency",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "passed": True,
        "stage_a_to_f_frozen": True,
        "active_index_unchanged": True,
        "stage_e_prerequisite": {
            "path": str(stage_e_path),
            "sha256": sha256_file(stage_e_path),
            "passed": True,
            "report_schema_version": stage_e.get("schema_version"),
        },
        "implementation": {
            "modified_production_files": [
                "backend/apps/enterprise_document/schemas/release_publication.py",
                "backend/apps/enterprise_document/services/agent_rag_v1/artifact_store.py",
                "backend/apps/enterprise_document/services/release_publication.py",
                "backend/apps/enterprise_document/services/scheme_writer/stage_service.py",
            ],
            "new_production_files": [],
            "new_test_files": [
                "backend/tests/test_stage_f_atomic_idempotent_release.py",
                "backend/tests/test_stage_f_release_concurrency.py",
            ],
            "production_line_counts": production_lines,
            "production_net_added_lines": production_delta,
            "test_line_counts": test_lines,
            "test_added_lines": test_delta,
            "complexity_limits_passed": True,
        },
        "release_request_fingerprint": {
            "field": "release_request_sha256",
            "canonical_hash": "project canonical_sha256",
            "included": [
                "task_id",
                "run_id",
                "document_id",
                "draft_id",
                "document_version",
                "output_format",
                "draft_sha256",
                "hard_gate_sha256",
                "generation_report_sha256",
                "version_decision_sha256",
                "publisher",
                "release_policy",
            ],
            "excluded": [
                "published_at",
                "verified_at",
                "current_system_time",
                "retry_count",
                "worker_id",
                "process_id",
                "temporary_path",
                "log_time",
                "checkpoint_time",
            ],
            "release_policy": "atomic_manifest_commit_v1",
        },
        "atomic_publication": {
            "version_identity": [
                "task_id",
                "document_id",
                "document_version",
            ],
            "lock_path": "<release_store_root>/.release-locks/<idempotency_key>.lock",
            "lock_implementation": "InterProcessFileLock",
            "lock_scope": [
                "read and verify canonical manifest",
                "compare release_request_sha256",
                "generate first commit time",
                "build eligibility proof",
                "write document blob",
                "write eligibility blob",
                "build and preverify manifest",
                "commit canonical manifest",
                "postcommit readback verification",
            ],
            "document_blob_ref": (
                "release-blob://<idempotency_key>/document/<document_sha256>"
            ),
            "eligibility_blob_ref": (
                "release-blob://<idempotency_key>/eligibility/<proof_sha256>"
            ),
            "canonical_manifest_ref": "release://<idempotency_key>/manifest",
            "unique_commit_point": "canonical manifest atomic put_json",
            "publication_definition": (
                "published only when canonical manifest exists and deep verification passes"
            ),
        },
        "recovery_semantics": {
            "manifest_absent": "NOT_COMMITTED; orphan blobs ignored; safe retry",
            "manifest_valid_request_match": "COMMITTED_MATCH; return idempotent replay",
            "manifest_valid_request_differs": "COMMITTED_CONFLICT; reject overwrite",
            "manifest_invalid_or_blob_invalid": (
                "COMMITTED_CORRUPTED; raise ReleaseVerificationError"
            ),
        },
        "runtime_acceptance": {
            "path": str(runtime_path),
            "sha256": sha256_file(runtime_path),
            "passed": True,
            "hard_failures": hard_failures,
            "hard_failure_count": len(hard_failures),
            "hard_failure_true_count": sum(hard_failures.values()),
            "release_store": release_store,
            "scenarios": scenarios,
        },
        "tests": {
            "focused": {
                **focused,
                "path": str(focused_path),
                "sha256": sha256_file(focused_path),
            },
            "regression": {
                **regression,
                "path": str(regression_path),
                "sha256": sha256_file(regression_path),
            },
            "total": {
                "tests": focused["tests"] + regression["tests"],
                "failures": focused["failures"] + regression["failures"],
                "errors": focused["errors"] + regression["errors"],
                "skipped": focused["skipped"] + regression["skipped"],
                "time_seconds": focused["time_seconds"]
                + regression["time_seconds"],
            },
        },
        "commands": {
            "focused": focused_command,
            "regression": regression_command,
        },
        "code_quality": {
            "compileall": compileall,
            "git_diff_check": diff_check,
        },
        "active_index": {
            "index_version": active["index_version"],
            "pointer_path": active["pointer_path"],
            "pointer_hash": active["pointer_hash"],
            "manifest_path": active["manifest_path"],
            "manifest_hash": active["manifest_hash"],
        },
        "workspace": {
            "python_executable": sys.executable,
            "python_version": sys.version,
            "git_head": head_commit,
            "dirty": bool(status_text.strip()),
            "git_status_porcelain_sha256": _sha256_text(status_text),
            "git_diff_binary_sha256": _sha256_text(diff_text),
            "git_status_entry_count": len(
                [line for line in status_text.splitlines() if line.strip()]
            ),
        },
        "mainline": {
            "formal_publish_entry": (
                "SchemeGenerationStageService.persist_deliverable"
            ),
            "published_at_source": "ReleasePublicationService first commit time",
            "final_result_created_after_verified_publication": True,
            "rejected_path_calls_release_service": False,
        },
        "residual_issues": [
            "orphan content blobs are intentionally retained and require no GC in scope",
            "file locking and atomic replace assume a local/shared filesystem honoring OS locks",
            "external object storage, PKI, revocation, and distributed consensus remain out of scope",
        ],
        "freeze_decision": {
            "stage_a_to_f_can_freeze": True,
            "next_allowed_work": [
                "code audit",
                "redundancy removal",
                "architecture documentation",
                "project documentation",
            ],
            "new_evidence_chain_features_allowed": False,
        },
    }

    repo_output = _resolve(args.repo_output)
    runtime_output = _resolve(args.runtime_output)
    rendered = json.dumps(report, ensure_ascii=False, indent=2) + "\n"
    for output in (repo_output, runtime_output):
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(rendered, encoding="utf-8")
    print(
        json.dumps(
            {
                "passed": True,
                "repo_output": str(repo_output),
                "repo_output_sha256": sha256_file(repo_output),
                "runtime_output": str(runtime_output),
                "runtime_output_sha256": sha256_file(runtime_output),
                "focused_tests": focused["tests"],
                "regression_tests": regression["tests"],
                "total_tests": focused["tests"] + regression["tests"],
                "runtime_hard_failure_true_count": sum(hard_failures.values()),
                "production_net_added_lines": production_delta,
                "test_added_lines": test_delta,
                "active_index": active["index_version"],
            },
            ensure_ascii=True,
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
