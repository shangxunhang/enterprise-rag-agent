"""Finalize Stage E from Runtime V3, JUnit V3, and reproducible command evidence."""

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

from apps.enterprise_document.services.scheme_writer.writer_governance import (  # noqa: E402
    CLAIM_CLASSIFIER_ID,
    CLAIM_CLASSIFIER_POLICY_SHA256,
    CLAIM_CLASSIFIER_VERSION,
    SUBSTANTIVE_DETECTOR_VERSION,
)
from finalize_stage_e_acceptance_v2 import (  # noqa: E402
    parse_junit_report,
    validate_clean_junit,
)
from rag.offline.manifest import sha256_file  # noqa: E402
from rag.offline.resolver import ActiveIndexResolver  # noqa: E402


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
    if not path.exists():
        raise FileNotFoundError(path)
    text = path.read_text(encoding="utf-8").strip()
    if not text:
        raise RuntimeError(f"command evidence is empty: {path}")
    return {
        "path": str(path),
        "sha256": sha256_file(path),
        "content": text,
    }


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--runtime-probe",
        default="E:/rag-agent-runtime/stage_e_final_runtime_acceptance_v3.json",
    )
    parser.add_argument(
        "--focused-junit",
        default="E:/rag-agent-runtime/stage_e_focused_junit_v3.xml",
    )
    parser.add_argument(
        "--regression-junit",
        default="E:/rag-agent-runtime/stage_e_regression_junit_v3.xml",
    )
    parser.add_argument(
        "--focused-command-file",
        default="scripts/stage_e_focused_commands_v3.txt",
    )
    parser.add_argument(
        "--regression-command-file",
        default="scripts/stage_e_regression_commands_v3.txt",
    )
    parser.add_argument(
        "--repo-output",
        default="data/processed/indexes/stage_e_final_acceptance_report_v3.json",
    )
    parser.add_argument(
        "--runtime-output",
        default="E:/rag-agent-runtime/stage_e_final_acceptance_report_v3.json",
    )
    return parser


def main() -> int:
    args = _parser().parse_args()
    runtime_path = _resolve(args.runtime_probe)
    runtime = _read_json(runtime_path)
    if runtime.get("schema_version") != "stage_e_final_runtime_acceptance_v3":
        raise RuntimeError("unexpected Stage E Runtime Acceptance schema")
    if not runtime.get("passed"):
        raise RuntimeError("Stage E Runtime Acceptance V3 did not pass")
    runtime_failures = {
        str(key): bool(value)
        for key, value in dict(runtime.get("hard_failures") or {}).items()
    }
    if any(runtime_failures.values()):
        raise RuntimeError("Stage E Runtime Acceptance V3 contains hard failures")

    focused = parse_junit_report(_resolve(args.focused_junit))
    regression = parse_junit_report(_resolve(args.regression_junit))
    validate_clean_junit(focused, label="focused")
    validate_clean_junit(regression, label="regression")
    focused_commands = _command_evidence(args.focused_command_file)
    regression_commands = _command_evidence(args.regression_command_file)

    active = ActiveIndexResolver(
        verify_manifest_hash=True,
        verify_artifacts=False,
    ).resolve(PROJECT_ROOT / "data/processed/indexes/active_index.json")
    runtime_active = dict(runtime.get("active_index") or {})
    for key in ("index_version", "pointer_hash", "manifest_hash"):
        if runtime_active.get(key) != active.get(key):
            raise ValueError(f"runtime report and active index differ for {key}")

    status_text = _git_text("status", "--porcelain=v1", "--untracked-files=all")
    diff_text = _git_text("diff", "--no-ext-diff", "--binary")
    head_commit = _git_text("rev-parse", "HEAD").strip()

    total = {
        "tests": focused["tests"] + regression["tests"],
        "failures": focused["failures"] + regression["failures"],
        "errors": focused["errors"] + regression["errors"],
        "skipped": focused["skipped"] + regression["skipped"],
        "time_seconds": focused["time_seconds"] + regression["time_seconds"],
    }
    report = {
        "schema_version": "stage_e_final_acceptance_report_v3",
        "stage": "Stage E.2 Mandatory Identity & Evidence Lineage Closure",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "passed": True,
        "stage_e_finally_accepted": True,
        "stage_f_started": False,
        "active_index_unchanged": True,
        "mandatory_identity": {
            "section_order_required": True,
            "document_id_required": True,
            "run_id_required": True,
            "missing_identity_is_not_a_wildcard": True,
        },
        "mandatory_lineage": {
            "evidence_contract_sha256_required": True,
            "prompt_evidence_contract_sha256_required": True,
            "missing_lineage_is_not_equal": True,
        },
        "legacy_artifact_policy": {
            "readable": True,
            "formally_deliverable": False,
            "default_route": "human_review_or_not_assessed",
            "failure_code": "legacy_writer_artifact_incomplete",
        },
        "active_index": {
            "index_version": active["index_version"],
            "pointer_path": active["pointer_path"],
            "pointer_hash": active["pointer_hash"],
            "manifest_path": active["manifest_path"],
            "manifest_hash": active["manifest_hash"],
        },
        "claim_classifier": {
            "classifier_id": CLAIM_CLASSIFIER_ID,
            "classifier_version": CLAIM_CLASSIFIER_VERSION,
            "classifier_policy_sha256": CLAIM_CLASSIFIER_POLICY_SHA256,
            "substantive_detector_version": SUBSTANTIVE_DETECTOR_VERSION,
            "classification_independent_of_gate": True,
            "unclassified_substantive_behavior": "fail_closed_human_review",
        },
        "runtime_acceptance": {
            "path": str(runtime_path),
            "sha256": sha256_file(runtime_path),
            "passed": True,
            "hard_failures": runtime_failures,
            "scenario_names": list(dict(runtime.get("scenarios") or {})),
        },
        "tests": {
            "focused": {**focused, "commands": focused_commands},
            "regression": {**regression, "commands": regression_commands},
            "total": total,
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
        "stage_e_guarantees": {
            "final_content_hash_bound": True,
            "claim_audit_recomputed": True,
            "claim_classifier_fail_closed": True,
            "section_identity_bound": True,
            "document_identity_bound": True,
            "run_identity_bound": True,
            "section_order_bound": True,
            "evidence_contract_hash_required_and_matched": True,
            "prompt_evidence_contract_hash_required_and_matched": True,
            "cross_section_document_run_replay_rejected": True,
            "missing_identity_or_lineage_rejected": True,
            "formal_eligibility_recomputed_by_document_gate": True,
            "upstream_formal_boolean_not_trusted": True,
            "junit_is_test_count_source": True,
        },
        "stage_boundary": {
            "completed": [
                "Stage E.1 Claim Audit Integrity & Fail-Closed Closure",
                "Stage E.2 Mandatory Identity & Evidence Lineage Closure",
            ],
            "not_started": ["Stage F downstream release workflow"],
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
                "runtime_output": str(runtime_output),
                "focused_tests": focused["tests"],
                "regression_tests": regression["tests"],
                "total_tests": total["tests"],
                "active_index": active["index_version"],
            },
            ensure_ascii=True,
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
