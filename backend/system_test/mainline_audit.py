"""Static and artifact-backed audit for the pre-LangGraph mainline."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, Optional

import yaml


def _load_json(path: Path) -> Optional[Dict[str, Any]]:
    if not path.is_file():
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None
    return payload if isinstance(payload, dict) else None


def audit_mainline(project_root: str | Path) -> Dict[str, Any]:
    root = Path(project_root).expanduser().resolve()
    profile_path = root / "backend/rag/profiles/hybrid_v1.yaml"
    use_case_path = (
        root
        / "backend/apps/enterprise_document/services/scheme_writer/use_case.py"
    )
    section_service_path = (
        root
        / "backend/apps/enterprise_document/services/scheme_writer/section_generation_service.py"
    )
    workflow_path = (
        root / "backend/apps/enterprise_document/workflows/scheme_generation.py"
    )
    step15_report_path = (
        root / "data/processed/indexes/step_15_acceptance_report.json"
    )

    profile: Dict[str, Any] = {}
    if profile_path.is_file():
        loaded = yaml.safe_load(profile_path.read_text(encoding="utf-8"))
        profile = loaded if isinstance(loaded, dict) else {}

    use_case_text = use_case_path.read_text(encoding="utf-8") if use_case_path.is_file() else ""
    section_text = (
        section_service_path.read_text(encoding="utf-8")
        if section_service_path.is_file()
        else ""
    )
    workflow_text = workflow_path.read_text(encoding="utf-8") if workflow_path.is_file() else ""
    step15_report = _load_json(step15_report_path) or {}

    generation_checker = dict(profile.get("generation_checker") or {})
    repair_strategy = dict(profile.get("repair_strategy") or {})
    evidence_grader = dict(profile.get("evidence_grader") or {})
    graph_summary = dict(step15_report.get("graph_summary") or {})
    completed_nodes = list(graph_summary.get("completed_node_ids") or [])

    findings = [
        {
            "id": "generation_checker_noop",
            "severity": "high" if generation_checker.get("name") == "noop_generation" else "info",
            "present": generation_checker.get("name") == "noop_generation",
            "evidence": generation_checker,
            "meaning": "生成质量检查器当前不做语义支持性判断。",
        },
        {
            "id": "repair_strategy_noop",
            "severity": "high" if repair_strategy.get("name") == "noop_repair" else "info",
            "present": repair_strategy.get("name") == "noop_repair",
            "evidence": repair_strategy,
            "meaning": "RAG Pipeline内的RepairStrategy当前不执行真正修复。",
        },
        {
            "id": "evidence_grader_noop",
            "severity": "medium" if evidence_grader.get("name") == "noop_evidence" else "info",
            "present": evidence_grader.get("name") == "noop_evidence",
            "evidence": evidence_grader,
            "meaning": "Evidence Contract记录证据，但未自动判断章节证据是否充分。",
        },
        {
            "id": "semantic_gate_advisory",
            "severity": "medium",
            "present": "advisory_only" in section_text,
            "evidence": {
                "advisory_only_occurrences": section_text.count("advisory_only"),
            },
            "meaning": "Semantic Gate当前以建议性模式运行或包含该兼容路径。",
        },
        {
            "id": "scheme_writer_coarse_node",
            "severity": "medium",
            "present": len(completed_nodes) <= 2 and use_case_text.count("_generate_section(") >= 2,
            "evidence": {
                "completed_node_ids": completed_nodes,
                "use_case_line_count": len(use_case_text.splitlines()),
                "rag_call_sites": use_case_text.count("_call_rag_tool("),
                "section_generation_call_sites": use_case_text.count("_generate_section("),
                "workflow_step_literals": workflow_text.count("step_id="),
            },
            "meaning": "SchemeWriter仍是包含检索、生成、修复和门禁的粗粒度节点。",
        },
        {
            "id": "section_aware_retrieval_present",
            "severity": "info",
            "present": "enable_section_aware_retrieval" in use_case_text,
            "evidence": {
                "corrective_branch_present": "CorrectiveSectionRAG" in use_case_text,
            },
            "meaning": "章节级检索及补充检索分支已进入主链。",
        },
    ]

    active_risks = [
        item for item in findings if item.get("present") and item.get("severity") in {"high", "medium"}
    ]
    return {
        "schema_version": "step_16_mainline_audit_report_v1",
        "project_root": str(root),
        "profile_path": str(profile_path),
        "workflow_path": str(workflow_path),
        "step15_report_path": str(step15_report_path),
        "findings": findings,
        "summary": {
            "finding_count": len(findings),
            "active_risk_count": len(active_risks),
            "high_risk_count": sum(1 for item in active_risks if item["severity"] == "high"),
            "medium_risk_count": sum(1 for item in active_risks if item["severity"] == "medium"),
            "generation_checker": generation_checker.get("name"),
            "repair_strategy": repair_strategy.get("name"),
            "evidence_grader": evidence_grader.get("name"),
            "graph_completed_nodes": completed_nodes,
        },
    }
