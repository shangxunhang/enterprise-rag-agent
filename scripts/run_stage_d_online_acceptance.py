"""Run Stage D.1 final online and negative acceptance scenarios."""

from __future__ import annotations

import argparse
from copy import deepcopy
import json
from pathlib import Path
import sys
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[1]
BACKEND_ROOT = PROJECT_ROOT / "backend"
SCRIPTS_ROOT = PROJECT_ROOT / "scripts"
for path in (BACKEND_ROOT, SCRIPTS_ROOT):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

from audit_stage_d_online_v2 import audit as audit_active_index  # noqa: E402
from rag.context.context_packer import ContextPacker  # noqa: E402
from rag.evidence.governance_v2 import OnlineEvidenceGovernanceV2  # noqa: E402
from rag.mapping.request_mapper import RAGRequestMapper  # noqa: E402
from rag.mapping.result_mapper import RAGResultMapper  # noqa: E402
from rag.offline.resolver import ActiveIndexResolver  # noqa: E402
from rag.runtime.retrieval_runtime import (  # noqa: E402
    RetrievalRuntime,
    RetrievalRuntimeConfig,
)
from rag.schema.prompt_evidence_candidate_v1 import (  # noqa: E402
    build_prompt_evidence_candidate_v1,
)
from rag.schema.retrieval_candidate_v2 import (  # noqa: E402
    build_retrieval_candidate_v2,
)
from rag.services.rag_service import RAGService  # noqa: E402
from schemas.rag import (  # noqa: E402
    RAGToolInputSchema,
    RetrievalAccessScopeSchema,
)
from schemas.status import ExecutionStatus  # noqa: E402
from test_support.stage_d_fixtures import (  # noqa: E402
    document_profile,
    evidence_requirements,
    prompt_selected,
    retrieval_result,
)


PROJECT_FACT_DOC = "doc_5bd4faa071c0d2cb316c1add"
ESTIMATED_QUERY = "大件运输许可数字化提升前期采购项目预算金额250000.00元合同包预算金额"
UNKNOWN_QUERY = "大件运输许可系统升级项目预算金额和投资估算"
PROJECT_FILTER = 'tenant_id == "default" and kb_id in ["project_fact_kb"]'


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--output",
        default="E:/rag-agent-runtime/stage_d_final_runtime_probe_v3.json",
    )
    parser.add_argument(
        "--capture-output",
        default="E:/rag-agent-runtime/stage_d_final_runtime_capture_v3.jsonl",
    )
    parser.add_argument(
        "--active-audit-output",
        default="E:/rag-agent-runtime/stage_d_final_active_audit_v3.json",
    )
    return parser


def _requirements(
    *,
    topics: list[str],
    max_items: int = 5,
    max_chars: int = 6000,
) -> dict[str, Any]:
    return {
        "retrieval_scope": "section",
        "section_id": "investment",
        "section_title": "投资估算",
        "section_type": "investment",
        "evidence_requirements": {
            "policy_id": "stage_d_current_cost_v1",
            "section_id": "investment",
            "section_title": "投资估算",
            "section_type": "investment",
            "required_topics": topics,
            "requested_claim_types": ["current_project_cost"],
            "claim_risk_level": "high",
            "coverage_strategy": "structured_fact_resolution",
            "allow_framework_only": False,
        },
        "context_requirements": {
            "model_context_window": 8192,
            "prompt_reserved_tokens": 1536,
            "section_token_budget": 4096,
            "max_evidence_items": max_items,
            "max_context_chars": max_chars,
        },
    }


def _bundle_summary(bundle: Any) -> dict[str, Any]:
    governance = dict(bundle.extra.get("evidence_governance_v2") or {})
    gate = dict(bundle.extra.get("gate_decision") or {})
    prompt_contract = dict(bundle.extra.get("prompt_evidence_contract") or {})
    legacy = dict(bundle.assessment.details or {}).get(
        "legacy_corrective_assessment"
    )
    legacy = legacy if isinstance(legacy, dict) else {}
    legacy_status = legacy.get("status")
    if hasattr(legacy_status, "value"):
        legacy_status = legacy_status.value
    return {
        "status": bundle.status.value,
        "assessment_status": bundle.assessment.status.value,
        "assessment_judge": bundle.assessment.judge_name,
        "selected_count": len(bundle.selected_evidence_ids),
        "dropped_count": len(bundle.dropped_evidence_ids),
        "citation_count": len(bundle.citations),
        "delivery_mode": gate.get("delivery_mode"),
        "generation_allowed": bool(bundle.extra.get("generation_allowed")),
        "human_review_required": bool(gate.get("human_review_required")),
        "allowed_claim_types": list(gate.get("allowed_claim_types") or []),
        "forbidden_claim_types": list(gate.get("forbidden_claim_types") or []),
        "required_qualifiers": list(gate.get("required_qualifiers") or []),
        "reason_codes": list(gate.get("reason_codes") or []),
        "set_assessment": governance.get("set_assessment"),
        "prompt_evidence_source": bundle.extra.get("prompt_evidence_source"),
        "prompt_evidence_contract": prompt_contract,
        "legacy_assessment_status": (
            str(legacy_status) if legacy_status is not None else None
        ),
    }


def _service_request(
    *,
    query: str,
    run_id: str,
    topics: list[str],
    doc_ids: list[str] | None = None,
    max_items: int = 5,
    max_chars: int = 6000,
) -> RAGToolInputSchema:
    return RAGToolInputSchema(
        task_id="stage-d-final-acceptance",
        run_id=run_id,
        agent_name="StageDFinalAcceptance",
        query=query,
        kb_ids=["project_fact_kb"],
        access_scope=RetrievalAccessScopeSchema(
            tenant_id="default",
            authorized_kb_ids=["project_fact_kb"],
            allowed_doc_ids=list(doc_ids or []),
        ),
        filters={"doc_ids": list(doc_ids or [])},
        need_citation=True,
        max_context_chars=max_chars,
        max_context_items=max_items,
        extra={
            "retrieval_trace_id": f"stage_d_final_{run_id}",
            "retrieval_scope": "section",
            "extra_metadata": _requirements(
                topics=topics,
                max_items=max_items,
                max_chars=max_chars,
            ),
        },
    )


def _map_synthetic(
    *,
    context_pack: dict[str, Any],
    results: list[dict[str, Any]],
    topics: list[str] | None = None,
) -> Any:
    runtime_context = evidence_requirements(topics=topics or ["预算金额"])
    request = RAGToolInputSchema(
        task_id="stage-d-negative",
        run_id="stage-d-negative-run",
        agent_name="StageDNegativeAcceptance",
        query="预算金额",
        access_scope=RetrievalAccessScopeSchema(
            tenant_id="default",
            authorized_kb_ids=["project_fact_kb"],
        ),
        max_context_chars=6000,
        max_context_items=5,
        extra={
            "retrieval_scope": "section",
            "extra_metadata": runtime_context,
        },
    )
    invocation = RAGRequestMapper().map(request)
    pre = OnlineEvidenceGovernanceV2().evaluate(
        query=request.query,
        results=results,
        runtime_context=runtime_context,
    )
    return RAGResultMapper().map(
        request=request,
        invocation=invocation,
        raw_result={
            "data": {
                "query_expansion": {"rewritten_queries": []},
                "retrieval_plan": {"plan_id": "stage_d_negative"},
                "evidence_quality": {
                    "final_assessment": {
                        "sufficient": True,
                        "confidence": 1.0,
                    }
                },
                "retrieval_results": results,
                "evidence_governance_v2": pre.to_dict(),
                "context_pack": context_pack,
            },
            "metadata": {},
        },
        latency_ms=1,
        rag_project_root=str(PROJECT_ROOT),
        service_name="stage_d_negative_acceptance",
    )


def _scenario_empty_prompt() -> dict[str, Any]:
    result = retrieval_result()
    bundle = _map_synthetic(
        results=[result],
        context_pack={
            "context": "",
            "rendered_text": "",
            "selected_results": [],
            "dropped_results": [result],
            "selected_count": 0,
            "dropped_count": 1,
            "max_context_chars": 6000,
            "token_budget": 1000,
            "tokens_used": 0,
        },
    )
    summary = _bundle_summary(bundle)
    summary["passed"] = (
        summary["selected_count"] == 0
        and summary["set_assessment"]["status"] == "absent"
        and summary["delivery_mode"] == "blocked"
        and not summary["generation_allowed"]
    )
    return summary


def _scenario_missing_prompt_contract() -> dict[str, Any]:
    result = retrieval_result()
    bundle = _map_synthetic(
        results=[result],
        context_pack={
            "context": "",
            "rendered_text": "",
            "dropped_results": [],
            "max_context_chars": 6000,
            "token_budget": 1000,
            "tokens_used": 0,
        },
    )
    summary = _bundle_summary(bundle)
    summary["passed"] = (
        summary["delivery_mode"] == "blocked"
        and not summary["generation_allowed"]
        and "prompt_evidence_contract_missing" in summary["reason_codes"]
        and summary["prompt_evidence_source"] == "missing_blocked"
    )
    return summary


def _scenario_truncated_topic() -> dict[str, Any]:
    full = "一般说明。" * 100 + "预算金额：250万元"
    result = retrieval_result(text=full, value="250万元", unit="万元")
    packer = ContextPacker(
        max_context_chars=640,
        max_items=1,
        text_field="parent_text",
        dedup_parent=True,
        include_metadata=True,
        packing_strategy="default",
    )
    pack = packer.pack([result], token_budget=128, max_items=1, char_budget=640)
    selected = pack.selected_results[0] if pack.selected_results else None
    bundle = _map_synthetic(
        results=[result],
        context_pack=pack.to_dict(),
    )
    summary = _bundle_summary(bundle)
    prompt_candidates = summary["prompt_evidence_contract"].get("candidates") or []
    summary.update(
        {
            "context_truncated": bool(
                selected
                and (selected.get("metadata") or {}).get("context_truncated")
            ),
            "fact_visible_in_prompt": (
                prompt_candidates[0].get("fact_visible_in_prompt")
                if prompt_candidates
                else None
            ),
            "prompt_visible_text": (
                prompt_candidates[0].get("prompt_visible_text")
                if prompt_candidates
                else None
            ),
        }
    )
    covered = list((summary["set_assessment"] or {}).get("covered_topics") or [])
    summary["passed"] = (
        summary["context_truncated"]
        and summary["fact_visible_in_prompt"] is False
        and "预算金额" not in covered
        and not summary["generation_allowed"]
    )
    return summary


def _scenario_prompt_context_inconsistent() -> dict[str, Any]:
    result = retrieval_result()
    selected = prompt_selected(result, prompt_text="预算金额：250万元")
    try:
        _map_synthetic(
            results=[result],
            context_pack={
                "context": "",
                "rendered_text": "",
                "selected_results": [selected],
                "dropped_results": [],
            },
        )
    except ValueError as exc:
        return {"passed": True, "rejected": True, "message": str(exc)}
    return {"passed": False, "rejected": False}


def _scenario_conflict() -> dict[str, Any]:
    first = document_profile(document_id="conflict-a", profile_char="a")
    second = document_profile(document_id="conflict-b", profile_char="b")
    governance = OnlineEvidenceGovernanceV2().evaluate(
        query="预算金额",
        results=[
            retrieval_result(
                child_id="conflict-child-a",
                profile=first,
                text="预算金额：250万元",
                value="250万元",
                unit="万元",
                fact_status="confirmed",
            ),
            retrieval_result(
                child_id="conflict-child-b",
                profile=second,
                text="预算金额：300万元",
                value="300万元",
                unit="万元",
                fact_status="confirmed",
                rank=2,
            ),
        ],
        runtime_context=evidence_requirements(),
    )
    payload = governance.to_dict()
    gate = payload["gate_decision"]
    return {
        "passed": (
            payload["set_assessment"]["status"] == "conflicted"
            and gate["delivery_mode"] == "human_review"
            and gate["allowed_claim_types"] == []
            and not payload["generation_allowed"]
        ),
        "set_assessment": payload["set_assessment"],
        "gate_decision": gate,
        "generation_allowed": payload["generation_allowed"],
    }


def _scenario_fact_status() -> dict[str, Any]:
    unknown = OnlineEvidenceGovernanceV2().evaluate(
        query="预算金额",
        results=[retrieval_result(fact_status="unknown")],
        runtime_context=evidence_requirements(),
    ).to_dict()
    estimated = OnlineEvidenceGovernanceV2().evaluate(
        query="预算金额",
        results=[retrieval_result(fact_status="estimated")],
        runtime_context=evidence_requirements(),
    ).to_dict()
    estimated_gate = estimated["gate_decision"]
    return {
        "passed": (
            unknown["gate_decision"]["delivery_mode"] == "blocked"
            and not unknown["generation_allowed"]
            and estimated_gate["delivery_mode"] == "qualified"
            and "根据现有估算材料"
            in estimated_gate["required_qualifiers"]
            and "最终金额以审批或采购结果为准"
            in estimated_gate["required_qualifiers"]
        ),
        "unknown": {
            "item_assessments": unknown["item_assessments"],
            "gate_decision": unknown["gate_decision"],
            "generation_allowed": unknown["generation_allowed"],
        },
        "estimated": {
            "item_assessments": estimated["item_assessments"],
            "gate_decision": estimated_gate,
            "generation_allowed": estimated["generation_allowed"],
        },
    }


def _scenario_candidate_mismatch() -> dict[str, Any]:
    result = retrieval_result()
    result["kb_id"] = "template_style_kb"
    try:
        build_retrieval_candidate_v2(result)
    except Exception as exc:
        return {"passed": True, "rejected": True, "message": str(exc)}
    return {"passed": False, "rejected": False}


def main() -> int:
    args = _parser().parse_args()
    active = ActiveIndexResolver(
        verify_manifest_hash=True,
        verify_artifacts=False,
    ).resolve(PROJECT_ROOT / "data/processed/indexes/active_index.json")
    active_audit = audit_active_index(
        PROJECT_ROOT / "data/processed/indexes/active_index.json",
        verify_artifacts=True,
    )
    active_audit_path = Path(args.active_audit_output).expanduser()
    active_audit_path.parent.mkdir(parents=True, exist_ok=True)
    active_audit_path.write_text(
        json.dumps(active_audit, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )

    runtime = RetrievalRuntime(
        RetrievalRuntimeConfig(
            capture_output=str(Path(args.capture_output).expanduser()),
            index_source_mode="manifest",
            active_index_pointer="data/processed/indexes/active_index.json",
            verify_active_index_manifest_hash=True,
            verify_active_index_artifacts=False,
        ),
        project_root=PROJECT_ROOT,
    )
    service = RAGService(PROJECT_ROOT, retrieval_runtime=runtime)
    try:
        estimated_bundle = service.retrieve(
            _service_request(
                query=ESTIMATED_QUERY,
                run_id="estimated-cost",
                topics=["预算金额"],
                doc_ids=[PROJECT_FACT_DOC],
            )
        )
        unknown_bundle = service.retrieve(
            _service_request(
                query=UNKNOWN_QUERY,
                run_id="unknown-cost",
                topics=["预算金额", "投资估算"],
            )
        )
    finally:
        service.close()

    estimated_summary = _bundle_summary(estimated_bundle)
    estimated_summary["query"] = ESTIMATED_QUERY
    estimated_summary["passed"] = (
        estimated_bundle.status is ExecutionStatus.SUCCESS
        and estimated_summary["delivery_mode"] == "qualified"
        and "根据现有估算材料"
        in estimated_summary["required_qualifiers"]
        and estimated_summary["prompt_evidence_contract"].get("candidate_count", 0)
        > 0
    )
    unknown_summary = _bundle_summary(unknown_bundle)
    unknown_summary["query"] = UNKNOWN_QUERY
    unknown_summary["passed"] = (
        unknown_bundle.status is ExecutionStatus.SUCCESS
        and unknown_summary["delivery_mode"] == "blocked"
        and not unknown_summary["generation_allowed"]
        and unknown_summary["selected_count"] == 0
    )

    scenarios = {
        "estimated_active_runtime": estimated_summary,
        "unknown_active_runtime_empty_prompt": unknown_summary,
        "explicit_empty_selected_results": _scenario_empty_prompt(),
        "missing_selected_results": _scenario_missing_prompt_contract(),
        "truncated_prompt_topic": _scenario_truncated_topic(),
        "structured_fact_conflict": _scenario_conflict(),
        "fact_status_policy": _scenario_fact_status(),
        "candidate_profile_mismatch": _scenario_candidate_mismatch(),
        "prompt_context_inconsistent": _scenario_prompt_context_inconsistent(),
    }

    active_failures = dict(active_audit.get("hard_failures") or {})
    hard_failures = {
        "empty_selected_results_fell_back": not scenarios[
            "explicit_empty_selected_results"
        ]["passed"],
        "missing_selected_results_not_blocked": not scenarios[
            "missing_selected_results"
        ]["passed"],
        "truncated_topic_counted_as_visible": not scenarios[
            "truncated_prompt_topic"
        ]["passed"],
        "structured_evidence_projection_missing": bool(
            active_failures.get("structured_evidence_projection_missing")
        ),
        "dense_bm25_structured_evidence_mismatch": bool(
            active_failures.get("dense_bm25_structured_evidence_mismatch")
        ),
        "candidate_profile_scalar_mismatch_not_rejected": not scenarios[
            "candidate_profile_mismatch"
        ]["passed"],
        "real_fact_conflict_not_detected": not scenarios[
            "structured_fact_conflict"
        ]["passed"],
        "unknown_fact_status_allowed_as_grounded": not scenarios[
            "fact_status_policy"
        ]["passed"],
        "estimated_fact_missing_qualifier": not (
            scenarios["fact_status_policy"]["passed"]
            and scenarios["estimated_active_runtime"]["passed"]
        ),
        "prompt_context_selected_records_inconsistent": not scenarios[
            "prompt_context_inconsistent"
        ]["passed"],
        "active_runtime_estimated_scenario_failed": not scenarios[
            "estimated_active_runtime"
        ]["passed"],
        "active_runtime_unknown_scenario_failed": not scenarios[
            "unknown_active_runtime_empty_prompt"
        ]["passed"],
        "active_index_audit_failed": not bool(active_audit.get("passed")),
    }
    passed = not any(hard_failures.values())
    report = {
        "schema_version": "stage_d_final_runtime_probe_v3",
        "passed": passed,
        "active_index": {
            "index_version": active["index_version"],
            "pointer_path": active["pointer_path"],
            "pointer_hash": active["pointer_hash"],
            "manifest_path": active["manifest_path"],
            "manifest_hash": active["manifest_hash"],
        },
        "scenarios": scenarios,
        "active_index_audit_summary": {
            "passed": active_audit.get("passed"),
            "counts": active_audit.get("counts"),
            "hard_failures": active_audit.get("hard_failures"),
            "report_path": str(active_audit_path),
        },
        "hard_failures": hard_failures,
    }
    output = Path(args.output).expanduser()
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(json.dumps(report, ensure_ascii=True, indent=2))
    return 0 if passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
