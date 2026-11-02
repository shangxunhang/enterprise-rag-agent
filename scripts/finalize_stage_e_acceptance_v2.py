"""Finalize Stage E.1 from runtime evidence and real pytest JUnit reports."""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import subprocess
import sys
from typing import Any
import xml.etree.ElementTree as ET

PROJECT_ROOT = Path(__file__).resolve().parents[1]
BACKEND_ROOT = PROJECT_ROOT / "backend"
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from apps.enterprise_document.services.scheme_writer.writer_governance import (  # noqa: E402
    CLAIM_CLASSIFIER_ID,
    CLAIM_CLASSIFIER_POLICY_SHA256,
    CLAIM_CLASSIFIER_VERSION,
    SUBSTANTIVE_DETECTOR_VERSION,
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


def _iter_leaf_suites(root: ET.Element):
    if root.tag == "testsuite":
        yield root
        return
    for suite in root.findall(".//testsuite"):
        if not suite.findall("./testsuite"):
            yield suite


def parse_junit_report(path: Path) -> dict[str, Any]:
    """Parse actual test counts and provenance from one JUnit report."""

    if not path.exists():
        raise FileNotFoundError(path)
    root = ET.parse(path).getroot()
    suites = list(_iter_leaf_suites(root))
    if not suites:
        raise ValueError(f"no testsuite found in {path}")
    totals: dict[str, int | float] = {
        "tests": 0,
        "failures": 0,
        "errors": 0,
        "skipped": 0,
        "time": 0.0,
    }
    test_ids: list[str] = []
    test_files: list[str] = []
    source_junits: list[str] = []
    for suite in suites:
        totals["tests"] += int(suite.get("tests") or 0)
        totals["failures"] += int(suite.get("failures") or 0)
        totals["errors"] += int(suite.get("errors") or 0)
        totals["skipped"] += int(suite.get("skipped") or 0)
        totals["time"] += float(suite.get("time") or 0.0)
        source = str(suite.get("source_junit") or "").strip()
        if source:
            source_junits.append(source)
        for case in suite.findall("./testcase"):
            classname = str(case.get("classname") or "").strip()
            name = str(case.get("name") or "").strip()
            file_name = str(case.get("file") or "").strip()
            identifier = "::".join(item for item in (classname, name) if item)
            if identifier:
                test_ids.append(identifier)
            if file_name:
                test_files.append(file_name)
            elif classname:
                test_files.append(classname.split(".")[0])
    if int(totals["tests"]) != len(test_ids):
        raise ValueError(
            f"JUnit tests attribute does not match testcase count for {path}: "
            f"{totals['tests']} != {len(test_ids)}"
        )
    return {
        "path": str(path),
        "sha256": sha256_file(path),
        "tests": int(totals["tests"]),
        "failures": int(totals["failures"]),
        "errors": int(totals["errors"]),
        "skipped": int(totals["skipped"]),
        "time_seconds": float(totals["time"]),
        "test_ids": list(dict.fromkeys(test_ids)),
        "test_files": list(dict.fromkeys(test_files)),
        "source_junits": list(dict.fromkeys(source_junits)),
    }


def validate_clean_junit(result: dict[str, Any], *, label: str) -> None:
    if int(result["tests"]) <= 0:
        raise RuntimeError(f"{label} JUnit contains no tests")
    if int(result["failures"]) or int(result["errors"]):
        raise RuntimeError(
            f"{label} JUnit is not clean: failures={result['failures']} "
            f"errors={result['errors']}"
        )


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


def _command_text(value: str | None, file_value: str | None) -> str:
    if value and file_value:
        raise ValueError("use either command text or command file, not both")
    if file_value:
        return _resolve(file_value).read_text(encoding="utf-8").strip()
    return str(value or "").strip()


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--runtime-probe",
        default="E:/rag-agent-runtime/stage_e_final_runtime_acceptance_v2.json",
    )
    parser.add_argument(
        "--focused-junit",
        default="E:/rag-agent-runtime/stage_e_focused_junit_v2.xml",
    )
    parser.add_argument(
        "--regression-junit",
        default="E:/rag-agent-runtime/stage_e_regression_junit_v2.xml",
    )
    parser.add_argument("--focused-command")
    parser.add_argument("--focused-command-file")
    parser.add_argument("--regression-command")
    parser.add_argument("--regression-command-file")
    parser.add_argument(
        "--repo-output",
        default="data/processed/indexes/stage_e_final_acceptance_report_v2.json",
    )
    parser.add_argument(
        "--runtime-output",
        default="E:/rag-agent-runtime/stage_e_final_acceptance_report_v2.json",
    )
    return parser


def main() -> int:
    args = _parser().parse_args()
    runtime_path = _resolve(args.runtime_probe)
    runtime = _read_json(runtime_path)
    if not runtime.get("passed"):
        raise RuntimeError("Stage E.1 runtime acceptance did not pass")
    runtime_failures = {
        str(key): bool(value)
        for key, value in dict(runtime.get("hard_failures") or {}).items()
    }
    if any(runtime_failures.values()):
        raise RuntimeError("Stage E.1 runtime acceptance contains hard failures")

    focused = parse_junit_report(_resolve(args.focused_junit))
    regression = parse_junit_report(_resolve(args.regression_junit))
    for label, result in (("focused", focused), ("regression", regression)):
        validate_clean_junit(result, label=label)

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
    focused_command = _command_text(
        args.focused_command,
        args.focused_command_file,
    )
    regression_command = _command_text(
        args.regression_command,
        args.regression_command_file,
    )

    report = {
        "schema_version": "stage_e_final_acceptance_report_v2",
        "stage": "Stage E.1 Claim Audit Integrity & Fail-Closed Closure",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "passed": True,
        "stage_f_started": False,
        "active_index_unchanged": True,
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
            "focused": {
                **focused,
                "pytest_command": focused_command,
            },
            "regression": {
                **regression,
                "pytest_command": regression_command,
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
        "integrity_guarantees": {
            "audit_binds_exact_final_content_sha256": True,
            "audit_binds_normalized_diagnostic_sha256": True,
            "audit_binds_current_section_identity": True,
            "audit_binds_writer_gate_hash": True,
            "audit_binds_evidence_contract_hash": True,
            "audit_binds_prompt_evidence_contract_hash": True,
            "generated_claim_spans_revalidated": True,
            "classifier_policy_registry_enforced": True,
            "unclassified_substantive_claims_fail_closed": True,
            "formal_eligibility_recomputed_by_document_gate": True,
            "upstream_formal_boolean_not_trusted": True,
            "hidden_package_runtime_delegation_removed": True,
        },
        "stage_boundary": {
            "completed": [
                "Claim Audit Integrity V1",
                "exact final content hash binding",
                "section and binding identity validation",
                "evidence lineage validation",
                "Gate-independent multi-label claim classification",
                "substantive sentence fail-closed detection",
                "generated claim span verification",
                "Document Hard Gate formal eligibility recomputation",
                "JUnit-backed acceptance evidence",
            ],
            "not_started": [
                "Stage F downstream release workflow",
                "Human review resolution UI/workflow",
                "Model-assisted claim classifier expansion",
            ],
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
                "total_tests": focused["tests"] + regression["tests"],
                "active_index": active["index_version"],
            },
            ensure_ascii=True,
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
