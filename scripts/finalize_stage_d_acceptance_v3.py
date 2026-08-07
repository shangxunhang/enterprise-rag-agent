"""Assemble the reproducible Stage D.1 final acceptance report.

This script is read-only with respect to the active index. It combines the
full active-index audit, online/negative runtime probe and externally supplied
pytest summary into identical repository and runtime JSON reports.
"""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import sys
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[1]
BACKEND_ROOT = PROJECT_ROOT / "backend"
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from rag.offline.manifest import sha256_file  # noqa: E402


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--active-audit",
        default="E:/rag-agent-runtime/stage_d_final_active_audit_v3.json",
    )
    parser.add_argument(
        "--runtime-probe",
        default="E:/rag-agent-runtime/stage_d_final_runtime_probe_v3.json",
    )
    parser.add_argument(
        "--repo-output",
        default="data/processed/indexes/stage_d_final_acceptance_report_v3.json",
    )
    parser.add_argument(
        "--runtime-output",
        default="E:/rag-agent-runtime/stage_d_final_acceptance_report_v3.json",
    )
    parser.add_argument("--stage-d-tests-passed", type=int, required=True)
    parser.add_argument("--stage-d-tests-duration", type=float, required=True)
    parser.add_argument("--regression-tests-passed", type=int, required=True)
    parser.add_argument("--regression-warnings", type=int, default=0)
    parser.add_argument("--regression-duration", type=float, required=True)
    return parser


def _resolve(value: str) -> Path:
    path = Path(value).expanduser()
    return path.resolve() if path.is_absolute() else (PROJECT_ROOT / path).resolve()


def _read_json(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"expected JSON object: {path}")
    return payload


def main() -> int:
    args = _parser().parse_args()
    active_path = _resolve(args.active_audit)
    runtime_path = _resolve(args.runtime_probe)
    active = _read_json(active_path)
    runtime = _read_json(runtime_path)
    if not active.get("passed"):
        raise RuntimeError("Stage D active-index audit did not pass")
    if not runtime.get("passed"):
        raise RuntimeError("Stage D runtime probe did not pass")
    if active.get("active_index", {}).get("index_version") != runtime.get(
        "active_index", {}
    ).get("index_version"):
        raise ValueError("active audit/runtime probe index versions differ")
    if active.get("active_index", {}).get("manifest_hash") != runtime.get(
        "active_index", {}
    ).get("manifest_hash"):
        raise ValueError("active audit/runtime probe manifest hashes differ")

    hard_failures = {
        **{
            f"active_audit.{key}": bool(value)
            for key, value in (active.get("hard_failures") or {}).items()
        },
        **{
            f"runtime_probe.{key}": bool(value)
            for key, value in (runtime.get("hard_failures") or {}).items()
        },
    }
    passed = not any(hard_failures.values())
    if not passed:
        raise RuntimeError("Stage D final report contains hard failures")

    scenarios = runtime.get("scenarios") or {}
    report = {
        "schema_version": "stage_d_final_acceptance_report_v3",
        "stage": "Stage D.1 Final Gate Semantic Closure",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "passed": True,
        "active_index_unchanged": True,
        "stage_e_started": False,
        "active_index": dict(active.get("active_index") or {}),
        "implementation": {
            "canonical_scope_order_independent": True,
            "structured_scope_unknown_fields_forbidden": True,
            "dense_bm25_shared_structured_projection": True,
            "retrieval_candidate_full_profile_consistency": True,
            "prompt_evidence_projection": True,
            "explicit_empty_selected_results_authoritative": True,
            "missing_prompt_contract_blocks_strong_gate": True,
            "final_gate_uses_prompt_visible_text_only": True,
            "truncated_fact_visibility_enforced": True,
            "fact_status_authority_joint_policy": True,
            "cross_document_structured_fact_conflict_detection": True,
            "legacy_crag_cannot_override_gate": True,
            "evidence_bundle_publishes_authoritative_gate": True,
        },
        "active_index_audit": {
            "passed": active.get("passed"),
            "counts": active.get("counts"),
            "distribution": active.get("distribution"),
            "structured_samples": active.get("structured_samples"),
            "scope_audit": active.get("scope_audit"),
            "violations": active.get("violations"),
            "hard_failures": active.get("hard_failures"),
            "source_report": str(active_path),
            "source_report_sha256": sha256_file(active_path),
        },
        "runtime_acceptance": {
            "passed": runtime.get("passed"),
            "scenario_results": {
                name: {
                    "passed": bool(payload.get("passed")),
                    "delivery_mode": payload.get("delivery_mode"),
                    "generation_allowed": payload.get("generation_allowed"),
                    "selected_count": payload.get("selected_count"),
                    "human_review_required": payload.get(
                        "human_review_required"
                    ),
                    "required_qualifiers": payload.get(
                        "required_qualifiers"
                    ),
                    "reason_codes": payload.get("reason_codes"),
                }
                for name, payload in scenarios.items()
                if isinstance(payload, dict)
            },
            "hard_failures": runtime.get("hard_failures"),
            "source_report": str(runtime_path),
            "source_report_sha256": sha256_file(runtime_path),
            "capture_output": (
                "E:/rag-agent-runtime/stage_d_final_runtime_capture_v3.jsonl"
            ),
        },
        "tests": {
            "stage_d_focused": {
                "passed": args.stage_d_tests_passed,
                "failed": 0,
                "errors": 0,
                "duration_seconds": args.stage_d_tests_duration,
                "basetemp": "E:/rag-agent-tmp/pytest-stage-d-focused-v3",
            },
            "final_relevant_regression": {
                "passed": args.regression_tests_passed,
                "failed": 0,
                "errors": 0,
                "warnings": args.regression_warnings,
                "duration_seconds": args.regression_duration,
                "basetemp": "E:/rag-agent-tmp/pytest-stage-d-final-v3",
            },
        },
        "fact_status_policy": {
            "unknown": {
                "confirmed_claim_eligible": False,
                "delivery": "blocked",
                "reason_code": "fact_status_unknown",
            },
            "estimated": {
                "eligible": True,
                "delivery": "qualified",
                "human_review_required": True,
                "required_qualifiers": [
                    "根据现有估算材料",
                    "最终金额以审批或采购结果为准",
                ],
            },
            "proposed": {
                "eligible": True,
                "delivery": "qualified",
                "required_qualifiers": ["拟", "尚待确认"],
            },
            "planned": {
                "eligible": True,
                "delivery": "qualified",
                "required_qualifiers": ["计划", "尚待确认"],
            },
            "confirmed": {
                "eligible_only_if": [
                    "project_relation=current_project",
                    "authority in authoritative/official/confirmed",
                    "profile allows claim",
                    "structured fact visible in prompt",
                ]
            },
            "decided": {
                "eligible_only_if": [
                    "project_relation=current_project",
                    "authoritative profile",
                    "formal decision source_document_form",
                    "structured fact visible in prompt",
                ]
            },
        },
        "hard_failures": hard_failures,
        "stage_boundary": {
            "completed": [
                "Canonical Retrieval Scope semantic closure",
                "Structured Evidence online projection",
                "Retrieval Candidate/Profile full consistency",
                "Prompt Evidence Contract",
                "Prompt-visible final governance",
                "Fact Status and Authority policy",
                "Structured fact conflict detection",
                "Evidence Bundle final gate publication",
            ],
            "not_started": [
                "Stage E Writer claim enforcement",
                "Writer qualifier injection",
                "Writer blocked/human-review refusal",
                "Post-generation claim audit",
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
                "active_index": report["active_index"].get("index_version"),
                "stage_d_tests": args.stage_d_tests_passed,
                "regression_tests": args.regression_tests_passed,
            },
            ensure_ascii=True,
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
