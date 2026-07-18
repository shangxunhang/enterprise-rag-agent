"""Hard-failure rules shared by runtime and offline evaluation."""

from __future__ import annotations

from typing import Any, Dict, Iterable

from apps.enterprise_document.schemas.scheme_writer_schema import (
    HardGateResultSchema,
    SchemeDraftSchema,
)
from schemas.status import ExecutionStatus


_REFUSAL_TERMS = ("证据不足", "无法确认", "待补充", "需项目方确认", "无法得出")


def evaluate_scheme_draft(
    draft: SchemeDraftSchema,
    *,
    citation_required: bool,
    retrieved_chunk_ids: Iterable[str],
    citation_required_sections: Iterable[str] = (),
    tool_failed: bool = False,
    key_fields_valid: bool = True,
    output_schema_valid: bool = True,
    evidence_sufficient: bool = True,
    workflow_complete: bool = True,
) -> HardGateResultSchema:
    checks: Dict[str, bool] = {}
    failures: list[str] = []
    warnings: list[str] = []

    section_titles = {section.section_title for section in draft.sections}
    checks["required_sections_present"] = all(
        section in section_titles for section in draft.required_sections
    ) and not draft.missing_sections
    if not checks["required_sections_present"]:
        failures.append("主章节缺失")

    checks["not_truncated"] = not draft.truncation_detected and all(
        not section.truncation.truncated for section in draft.sections
    )
    if not checks["not_truncated"]:
        failures.append("输出被截断")

    checks["tool_success"] = not tool_failed
    if not checks["tool_success"]:
        failures.append("工具执行失败")

    checks["key_fields_valid"] = key_fields_valid
    if not checks["key_fields_valid"]:
        failures.append("关键字段为空")

    checks["output_schema_valid"] = output_schema_valid
    if not checks["output_schema_valid"]:
        failures.append("输出结构不合法")

    binding_count = len(draft.citation_bindings)
    checks["citations_present"] = (not citation_required) or binding_count > 0
    if not checks["citations_present"]:
        failures.append("必须引用但无有效引用绑定")

    required_citation_titles = {
        str(title).strip() for title in citation_required_sections if str(title).strip()
    }
    section_by_title = {section.section_title: section for section in draft.sections}
    missing_citation_sections = sorted(
        title
        for title in required_citation_titles
        if title not in section_by_title
        or not section_by_title[title].citation_bindings
    )
    checks["citation_required_sections_bound"] = not missing_citation_sections
    if missing_citation_sections:
        failures.append(
            "必需引用章节缺少有效引用：" + "、".join(missing_citation_sections)
        )

    known_chunks = {item for item in retrieved_chunk_ids if item}
    binding_sources_valid = all(
        (not binding.source_chunk_id) or binding.source_chunk_id in known_chunks
        for binding in draft.citation_bindings
    )
    section_ids = {section.section_id for section in draft.sections}
    binding_targets_valid = all(
        binding.target_section_id in section_ids for binding in draft.citation_bindings
    )
    checks["citation_targets_valid"] = binding_sources_valid and binding_targets_valid
    if not checks["citation_targets_valid"]:
        failures.append("引用指向不存在的证据或章节")

    unverified_bindings = [
        binding.binding_id
        for binding in draft.citation_bindings
        if not bool((binding.metadata or {}).get("grounding_verified"))
    ]
    checks["citation_grounding_verified"] = not unverified_bindings
    if unverified_bindings:
        failures.append("存在未通过 Claim-Evidence 校验的引用")

    refused_or_qualified = any(term in draft.full_text for term in _REFUSAL_TERMS)
    checks["evidence_boundary_respected"] = evidence_sufficient or refused_or_qualified
    if not checks["evidence_boundary_respected"]:
        failures.append("证据不足却生成确定性结论")

    checks["workflow_complete"] = workflow_complete
    if not checks["workflow_complete"]:
        failures.append("Workflow 节点未完整结束")

    checks["section_status_valid"] = all(
        section.status in {ExecutionStatus.SUCCESS, ExecutionStatus.PARTIAL_SUCCESS}
        for section in draft.sections
    )
    if not checks["section_status_valid"]:
        failures.append("存在失败章节")

    partial_sections = [
        section.section_title
        for section in draft.sections
        if section.status == ExecutionStatus.PARTIAL_SUCCESS
    ]
    if partial_sections:
        warnings.append("存在带警告章节：" + "、".join(partial_sections))
    for section in draft.sections:
        if section.eval_result is not None:
            warnings.extend(
                f"{section.section_title}:{item}"
                for item in section.eval_result.warnings
            )
    warnings = list(dict.fromkeys(warnings))

    return HardGateResultSchema(
        passed=not failures,
        failures=failures,
        warnings=warnings,
        checks=checks,
        metadata={
            "citation_binding_count": binding_count,
            "missing_citation_sections": missing_citation_sections,
            "unverified_binding_ids": unverified_bindings,
            "partial_sections": partial_sections,
        },
    )


def extract_runtime_hard_failures(run_summary: Dict[str, Any]) -> list[str]:
    """Read embedded hard-gate failures from run_demo output."""

    failures: list[str] = []
    scheme = run_summary.get("scheme_writer_output") or {}
    hard_gate = scheme.get("hard_gate") or {}
    failures.extend(hard_gate.get("failures") or [])

    status = str(run_summary.get("status") or "")
    if status in {ExecutionStatus.FAILED.value, ExecutionStatus.RETRYABLE_FAILED.value}:
        if not failures:
            failures.append("主链路状态为失败")
    return list(dict.fromkeys(failures))
