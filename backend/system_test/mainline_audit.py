# =============================================================================
# 中文阅读说明：系统级验收与审计模块，用于验证完整运行闭环。
# 主要定义：_load_json、audit_mainline。建议先从公开入口函数开始，再沿调用关系向下阅读。
# =============================================================================
"""Static and artifact-backed audit for the pre-LangGraph mainline."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, Optional

import yaml


# 阅读注释（函数）：加载 JSON。
def _load_json(path: Path) -> Optional[Dict[str, Any]]:
    """加载 JSON。

    参数:
        path: 目标文件或目录路径。

    返回:
        Optional[Dict[str, Any]]

    阅读提示:
        主要直接调用：path.is_file, json.loads, path.read_text, isinstance。
    """
    if not path.is_file():
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None
    return payload if isinstance(payload, dict) else None


# 阅读注释（函数）：处理 audit 主链 相关逻辑。
def audit_mainline(project_root: str | Path) -> Dict[str, Any]:
    """处理 audit 主链 相关逻辑。

    参数:
        project_root: 项目 root，具体约束请结合类型标注和调用方确认。

    返回:
        Dict[str, Any]

    阅读提示:
        主要直接调用：resolve, expanduser, Path, profile_path.is_file, yaml.safe_load, profile_path.read_text, isinstance, use_case_path.is_file。
    """
    root = Path(project_root).expanduser().resolve()
    static_spec_path = root / "backend/rag/config/static_retrieval_v1.yaml"
    generation_policy_path = (
        root
        / "backend/apps/enterprise_document/config/grounded_generation_v1.yaml"
    )
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

    static_spec: Dict[str, Any] = {}
    if static_spec_path.is_file():
        loaded = yaml.safe_load(static_spec_path.read_text(encoding="utf-8"))
        static_spec = loaded if isinstance(loaded, dict) else {}
    generation_policy: Dict[str, Any] = {}
    if generation_policy_path.is_file():
        loaded = yaml.safe_load(generation_policy_path.read_text(encoding="utf-8"))
        generation_policy = loaded if isinstance(loaded, dict) else {}

    use_case_text = use_case_path.read_text(encoding="utf-8") if use_case_path.is_file() else ""
    section_text = (
        section_service_path.read_text(encoding="utf-8")
        if section_service_path.is_file()
        else ""
    )
    workflow_text = workflow_path.read_text(encoding="utf-8") if workflow_path.is_file() else ""
    step15_report = _load_json(step15_report_path) or {}

    generation_checker = dict(generation_policy.get("generation_checker") or {})
    repair_strategy = dict(generation_policy.get("repair_strategy") or {})
    evidence_assessor = dict(static_spec.get("evidence_assessor") or {})
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
            "meaning": "应用生成闭环的 RepairStrategy 当前不执行真正修复。",
        },
        {
            "id": "evidence_assessor_noop",
            "severity": "medium" if evidence_assessor.get("name") == "noop_evidence" else "info",
            "present": evidence_assessor.get("name") == "noop_evidence",
            "evidence": evidence_assessor,
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
        "static_retrieval_spec_path": str(static_spec_path),
        "generation_policy_path": str(generation_policy_path),
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
            "evidence_assessor": evidence_assessor.get("name"),
            "graph_completed_nodes": completed_nodes,
        },
    }
