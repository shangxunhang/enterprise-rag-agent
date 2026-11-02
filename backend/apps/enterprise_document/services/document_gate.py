"""运行时与离线评测共用的文档级硬失败规则。

该门禁只消费已经装配完成的草稿和结构化运行事实，统一检查必需章节、截断、工具状态、
输出结构、引用出现次数与绑定一致性、Chunk 血缘、证据覆盖和高风险项目事实。它不会
调用模型，也不会修复正文；失败草稿可保留诊断，但不得升级为业务交付物。
"""

from __future__ import annotations

import json
import re
from collections import Counter
from typing import Any, Dict, Iterable

from apps.enterprise_document.schemas.scheme_writer_schema import (
    HardGateResultSchema,
    SchemeDraftSchema,
)
from apps.enterprise_document.schemas.project_input_schema import ProjectInputSchema
from apps.enterprise_document.services.project_input_normalization.input_contract import (
    read_saved_project_input,
)
from apps.enterprise_document.services.writer_artifact_integrity import (
    validate_writer_section_artifact,
)
from apps.enterprise_document.services.scheme_writer.citation_integrity_service import (
    CitationOccurrenceIntegrityGate,
)
from apps.enterprise_document.services.scheme_writer.high_risk_claim_service import (
    HighRiskClaimGate,
)
from apps.enterprise_document.services.scheme_writer.trusted_fact_ledger import (
    TrustedFactLedgerBuilder,
)
from schemas.citation import CitationSchema
from schemas.status import ExecutionStatus


_CITATION_PATTERN = re.compile(r"\[([A-Za-z0-9_.:\-]+)\]")
_REFUSAL_TERMS = ("证据不足", "无法确认", "待补充", "需项目方确认", "无法得出")


def evaluate_scheme_draft(
    draft: SchemeDraftSchema,
    *,
    citation_required: bool,
    retrieved_chunk_ids: Iterable[str],
    project_input: ProjectInputSchema | None = None,
    canonical_citations: Iterable[CitationSchema] = (),
    citation_required_sections: Iterable[str] = (),
    tool_failed: bool = False,
    key_fields_valid: bool = True,
    output_schema_valid: bool = True,
    evidence_sufficient: bool = True,
    workflow_complete: bool = True,
) -> HardGateResultSchema:
    """对完整草稿执行确定性文档硬门禁。

    引用检查不仅要求存在标记，还要求每次可见 marker 与唯一 Binding 对应、Binding 指向
    canonical Citation，且来源 Chunk 属于已检索集合。项目高风险事实会用最终正文重新
    计算，不能仅信任章节阶段记录的旧结果。明确因证据不足而 blocked 的章节可作为安全
    拒绝说明存在，但普通未评估或证据不足章节不能被文档级判断替代。
    """
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

    visible_marker_counts = Counter(
        match.group(1) for match in _CITATION_PATTERN.finditer(draft.full_text or "")
    )
    binding_counts = Counter(
        binding.citation_id for binding in draft.citation_bindings if binding.citation_id
    )
    binding_id_counts = Counter(
        binding.binding_id for binding in draft.citation_bindings if binding.binding_id
    )
    duplicate_binding_ids = sorted(
        binding_id for binding_id, count in binding_id_counts.items() if count != 1
    )
    invalid_binding_ids = [
        binding.binding_id or "<blank>"
        for binding in draft.citation_bindings
        if not str(binding.binding_id or "").strip()
        or not str(binding.citation_id or "").strip()
    ]
    canonical_citation_list = list(canonical_citations)
    canonical_citation_id_counts = Counter(
        citation.citation_id
        for citation in canonical_citation_list
        if citation.citation_id
    )
    duplicate_citation_ids = sorted(
        citation_id
        for citation_id, count in canonical_citation_id_counts.items()
        if count != 1
    )
    citations_by_id = {
        citation.citation_id: citation
        for citation in canonical_citation_list
        if citation.citation_id
    }
    unknown_binding_citation_ids = sorted(
        {
            binding.citation_id
            for binding in draft.citation_bindings
            if binding.citation_id not in citations_by_id
        }
    )
    binding_lineage_mismatches: list[str] = []
    for binding in draft.citation_bindings:
        citation = citations_by_id.get(binding.citation_id)
        if citation is None:
            continue
        expected_chunk_id = (
            citation.chunk_id
            or citation.child_chunk_id
            or citation.parent_chunk_id
        )
        expected_document_id = citation.source_document_id or citation.doc_id
        lineage_matches = (
            bool(expected_chunk_id)
            and binding.source_chunk_id == expected_chunk_id
            and binding.source_document_id == expected_document_id
            and binding.source_parent_chunk_id == citation.parent_chunk_id
        )
        if not lineage_matches:
            binding_lineage_mismatches.append(binding.binding_id or "<blank>")

    checks["citation_referential_integrity"] = not (
        duplicate_citation_ids
        or unknown_binding_citation_ids
        or binding_lineage_mismatches
    )
    if duplicate_citation_ids:
        failures.append(
            "存在重复 canonical citation_id：" + "、".join(duplicate_citation_ids)
        )
    if unknown_binding_citation_ids:
        failures.append(
            "引用绑定指向不存在的 canonical citation："
            + "、".join(unknown_binding_citation_ids)
        )
    if binding_lineage_mismatches:
        failures.append(
            "引用绑定与 canonical citation 来源谱系不一致："
            + "、".join(binding_lineage_mismatches)
        )

    citation_ids = set(visible_marker_counts) | set(binding_counts)
    marker_binding_count_mismatches = {
        citation_id: {
            "marker_occurrences": visible_marker_counts[citation_id],
            "binding_count": binding_counts[citation_id],
        }
        for citation_id in sorted(citation_ids)
        if visible_marker_counts[citation_id] != binding_counts[citation_id]
    }
    orphan_marker_ids = sorted(
        citation_id
        for citation_id in citation_ids
        if visible_marker_counts[citation_id] > binding_counts[citation_id]
    )
    binding_without_marker_ids = sorted(
        citation_id
        for citation_id in citation_ids
        if binding_counts[citation_id] > visible_marker_counts[citation_id]
    )

    section_marker_binding_mismatches: dict[str, Any] = {}
    section_binding_payload_mismatches: dict[str, Any] = {}
    aggregate_bindings_by_section = {
        section.section_id: Counter(
            binding.citation_id
            for binding in draft.citation_bindings
            if binding.target_section_id == section.section_id and binding.citation_id
        )
        for section in draft.sections
    }
    for section in draft.sections:
        section_markers = Counter(
            match.group(1) for match in _CITATION_PATTERN.finditer(section.content or "")
        )
        section_declared_bindings = Counter(
            binding.citation_id
            for binding in section.citation_bindings
            if binding.citation_id
        )
        section_aggregate_bindings = aggregate_bindings_by_section[section.section_id]
        section_binding_payloads = Counter(
            json.dumps(
                binding.model_dump(mode="json"),
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            )
            for binding in section.citation_bindings
        )
        aggregate_binding_payloads = Counter(
            json.dumps(
                binding.model_dump(mode="json"),
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            )
            for binding in draft.citation_bindings
            if binding.target_section_id == section.section_id
        )
        if section_binding_payloads != aggregate_binding_payloads:
            section_binding_payload_mismatches[section.section_id] = {
                "section_binding_count": sum(section_binding_payloads.values()),
                "document_binding_count": sum(
                    aggregate_binding_payloads.values()
                ),
            }
        if not (
            section_markers
            == section_declared_bindings
            == section_aggregate_bindings
        ):
            section_marker_binding_mismatches[section.section_id] = {
                "marker_occurrences": dict(section_markers),
                "section_binding_counts": dict(section_declared_bindings),
                "document_binding_counts": dict(section_aggregate_bindings),
            }

    occurrence_integrity = CitationOccurrenceIntegrityGate().evaluate(
        sections=draft.sections,
        bindings=draft.citation_bindings,
        full_text=draft.full_text,
    )
    checks.update(occurrence_integrity.checks)

    checks["citation_marker_binding_consistent"] = not (
        marker_binding_count_mismatches
        or duplicate_binding_ids
        or invalid_binding_ids
        or section_marker_binding_mismatches
        or section_binding_payload_mismatches
    ) and occurrence_integrity.passed
    if marker_binding_count_mismatches:
        failures.append("引用标记与绑定数量不是一一对应")
    if duplicate_binding_ids:
        failures.append("存在重复 citation binding_id：" + "、".join(duplicate_binding_ids))
    if invalid_binding_ids:
        failures.append("存在空 binding_id 或 citation_id 的引用绑定")
    if section_marker_binding_mismatches:
        failures.append("章节级引用标记与绑定不一致")
    if section_binding_payload_mismatches:
        failures.append("章节级与文档级引用绑定内容不一致")
    if not checks["citation_marker_binding_bijection"]:
        failures.append("引用标记 occurrence 与绑定不是一一对应")
    if not checks["citation_marker_occurrences_verified"]:
        failures.append("存在未通过 grounding 校验的引用 occurrence")
    if not checks["citation_orphan_markers_absent"]:
        failures.append("最终正文存在 orphan citation marker")
    if not checks["citation_orphan_bindings_absent"]:
        failures.append("存在未指向真实 marker occurrence 的引用绑定")

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
        bool(binding.source_chunk_id) and binding.source_chunk_id in known_chunks
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

    recorded_unsupported_project_facts = [
        {
            "section_id": section.section_id,
            "section_title": section.section_title,
            **dict(violation),
        }
        for section in draft.sections
        for violation in list(
            (section.extra or {}).get("mandatory_project_fact_violations")
            or []
        )
    ]
    inferred_project_inputs: dict[str, ProjectInputSchema] = {}
    project_input_parse_failures: list[str] = []
    for section in draft.sections:
        if project_input is not None:
            inferred_project_inputs[section.section_id] = project_input
            continue
        payload = dict(section.input or {}).get("project_input")
        if payload is None:
            continue
        try:
            inferred_project_inputs[section.section_id] = (
                read_saved_project_input(payload)
            )
        except Exception:
            project_input_parse_failures.append(section.section_id)

    ledger_builder = TrustedFactLedgerBuilder()
    trusted_facts = ledger_builder.build_many(
        inferred_project_inputs.values()
    )
    high_risk_gate = HighRiskClaimGate(ledger_builder=ledger_builder)
    final_unsupported_project_facts: list[dict[str, Any]] = []
    supported_section_fact_occurrences: Counter[
        tuple[str, str, int | float | str, str]
    ] = Counter()
    for section in draft.sections:
        section_result = high_risk_gate.evaluate(
            str(section.content or ""),
            project_input=inferred_project_inputs.get(section.section_id),
            bindings=section.citation_bindings,
            target_section_id=section.section_id,
        )
        section_violation_counts = Counter(
            (
                str(violation.get("fact_type") or ""),
                str(violation.get("subject") or ""),
                violation.get("value"),
                str(violation.get("unit") or ""),
            )
            for violation in section_result.violations
        )
        for claim in section_result.claims:
            if section_violation_counts[claim.identity] > 0:
                section_violation_counts[claim.identity] -= 1
                continue
            supported_section_fact_occurrences[claim.identity] += 1
        final_unsupported_project_facts.extend(
            {
                "section_id": section.section_id,
                "section_title": section.section_title,
                **dict(violation),
            }
            for violation in section_result.violations
        )

    document_result = high_risk_gate.evaluate(
        str(draft.full_text or ""),
        trusted_facts=trusted_facts,
        bindings=(),
    )
    remaining_supported_occurrences = supported_section_fact_occurrences.copy()
    for violation in document_result.violations:
        identity = (
            str(violation.get("fact_type") or ""),
            str(violation.get("subject") or ""),
            violation.get("value"),
            str(violation.get("unit") or ""),
        )
        if remaining_supported_occurrences[identity] > 0:
            remaining_supported_occurrences[identity] -= 1
            continue
        final_unsupported_project_facts.append(
            {
                "section_id": "<document>",
                "section_title": draft.title,
                **dict(violation),
            }
        )

    unsupported_project_facts: list[dict[str, Any]] = []
    seen_unsupported_facts: set[str] = set()
    for violation in [
        *recorded_unsupported_project_facts,
        *final_unsupported_project_facts,
    ]:
        identity = json.dumps(
            {
                key: violation.get(key)
                for key in (
                    "section_id",
                    "reason",
                    "fact_type",
                    "subject",
                    "value",
                    "unit",
                    "span_start",
                    "span_end",
                )
            },
            ensure_ascii=False,
            sort_keys=True,
        )
        if identity in seen_unsupported_facts:
            continue
        seen_unsupported_facts.add(identity)
        unsupported_project_facts.append(violation)
    checks["high_risk_claims_grounded"] = not unsupported_project_facts
    if unsupported_project_facts:
        failures.append("存在未获项目输入或已验证证据支撑的高风险项目事实")

    # Stage E.1: recompute writer integrity from the current artifact. Historical
    # ``claim_audit.passed`` and ``formal_deliverable_eligible`` booleans are only
    # upstream assertions; they are never accepted as the final fact source.
    substantive_writer_sections = [
        section for section in draft.sections if str(section.content or "").strip()
    ]
    writer_integrity_results = {
        section.section_id: validate_writer_section_artifact(
            section,
            document_id=draft.document_id,
            run_id=draft.run_id,
        )
        for section in substantive_writer_sections
    }
    writer_status_by_section: dict[str, str] = {}
    for section in substantive_writer_sections:
        result = writer_integrity_results[section.section_id]
        writer_status_by_section[section.section_id] = (
            "not_assessed"
            if "legacy_writer_artifact_incomplete" in result.failure_codes
            else (
                result.binding.execution_status.value
                if result.binding is not None
                else "not_assessed"
            )
        )

    writer_check_names = (
        "writer_gate_bound",
        "writer_binding_identity_fields_present",
        "writer_binding_section_identity_valid",
        "writer_execution_status_consistent",
        "writer_generation_allowed",
        "writer_human_review_clear",
        "writer_claim_audit_present",
        "writer_claim_audit_schema_valid",
        "writer_audit_section_identity_valid",
        "writer_audit_content_hash_valid",
        "writer_audit_binding_hash_valid",
        "writer_evidence_lineage_fields_present",
        "writer_audit_evidence_lineage_valid",
        "writer_audit_claim_boundary_valid",
        "writer_audit_claim_spans_valid",
        "writer_classifier_policy_valid",
        "writer_claim_audit_recomputed_valid",
        "writer_claim_audits_passed",
        "writer_required_qualifiers_applied",
        "writer_section_status_deliverable",
        "writer_formal_eligibility_recomputed",
    )
    for check_name in writer_check_names:
        checks[check_name] = all(
            result.checks.get(check_name, False)
            for result in writer_integrity_results.values()
        )

    writer_failure_messages = {
        "writer_gate_bound": "存在未绑定最终证据门禁的章节",
        "writer_binding_identity_fields_present": "Writer Binding 缺少正式发布所需身份字段",
        "writer_binding_section_identity_valid": "Writer Binding 与当前章节、文档或运行身份不一致",
        "writer_execution_status_consistent": "章节 Writer 执行状态与最终门禁不一致",
        "writer_generation_allowed": "存在被阻断或不允许生成的章节",
        "writer_human_review_clear": "文档仍包含待人工复核章节",
        "writer_claim_audit_present": "存在缺失 Claim Audit 的章节",
        "writer_claim_audit_schema_valid": "存在 Claim Audit Schema 无效的章节",
        "writer_audit_section_identity_valid": "Claim Audit 与当前章节身份不一致",
        "writer_audit_content_hash_valid": "writer_claim_audit_stale：正文与审计 Hash 不一致",
        "writer_audit_binding_hash_valid": "Claim Audit 与当前 Writer Binding Hash 不一致",
        "writer_evidence_lineage_fields_present": "Writer Binding 或 Claim Audit 缺少 Evidence Contract 血缘字段",
        "writer_audit_evidence_lineage_valid": "Claim Audit Evidence Contract 血缘不一致",
        "writer_audit_claim_boundary_valid": "Claim Audit 与当前 Gate Claim 边界不一致",
        "writer_audit_claim_spans_valid": "Generated Claim Span 与当前正文不一致",
        "writer_classifier_policy_valid": "Claim Classifier 策略身份无效或不受支持",
        "writer_claim_audit_recomputed_valid": "当前正文重新执行 Claim Audit 的结果不一致",
        "writer_claim_audits_passed": "存在未通过的章节 Claim Audit",
        "writer_required_qualifiers_applied": "限定交付章节未完整保留最终门禁限定语",
        "writer_section_status_deliverable": "存在不可交付状态的章节",
        "writer_formal_eligibility_recomputed": "writer_formal_eligibility_mismatch：上游布尔值与 Hard Gate 重算不一致",
    }
    for check_name, message in writer_failure_messages.items():
        if not checks[check_name]:
            failures.append(message)

    writer_failure_code_section_ids: dict[str, list[str]] = {}
    writer_integrity_by_section: dict[str, dict[str, Any]] = {}
    for section_id, result in writer_integrity_results.items():
        writer_integrity_by_section[section_id] = {
            "checks": dict(result.checks),
            "failure_codes": list(result.failure_codes),
            "upstream_formal_deliverable_eligible": (
                result.upstream_formal_deliverable_eligible
            ),
            "recomputed_formal_deliverable_eligible": (
                result.recomputed_formal_deliverable_eligible
            ),
            "metadata": dict(result.metadata),
        }
        for code in result.failure_codes:
            writer_failure_code_section_ids.setdefault(code, []).append(section_id)

    writer_gate_missing_section_ids = writer_failure_code_section_ids.get(
        "writer_gate_missing", []
    )
    writer_gate_invalid_section_ids = writer_failure_code_section_ids.get(
        "writer_gate_invalid", []
    )
    writer_binding_identity_fields_missing_section_ids = (
        writer_failure_code_section_ids.get(
            "writer_binding_identity_fields_missing", []
        )
    )
    writer_binding_identity_mismatch_section_ids = (
        writer_failure_code_section_ids.get("writer_binding_identity_mismatch", [])
    )
    writer_evidence_lineage_fields_missing_section_ids = (
        writer_failure_code_section_ids.get(
            "writer_evidence_lineage_fields_missing", []
        )
    )
    legacy_writer_artifact_incomplete_section_ids = (
        writer_failure_code_section_ids.get(
            "legacy_writer_artifact_incomplete", []
        )
    )
    writer_audit_identity_mismatch_section_ids = (
        writer_failure_code_section_ids.get("writer_audit_identity_mismatch", [])
    )
    writer_stale_claim_audit_section_ids = writer_failure_code_section_ids.get(
        "writer_claim_audit_stale", []
    )
    writer_status_mismatch_section_ids = writer_failure_code_section_ids.get(
        "writer_execution_status_mismatch", []
    )
    writer_blocked_section_ids = writer_failure_code_section_ids.get(
        "writer_generation_not_allowed", []
    )
    writer_human_review_section_ids = writer_failure_code_section_ids.get(
        "writer_human_review_required", []
    )
    writer_claim_audit_missing_section_ids = writer_failure_code_section_ids.get(
        "writer_claim_audit_missing", []
    )
    writer_claim_audit_invalid_section_ids = writer_failure_code_section_ids.get(
        "writer_claim_audit_invalid", []
    )
    writer_claim_audit_failed_section_ids = writer_failure_code_section_ids.get(
        "writer_claim_audit_failed", []
    )
    writer_qualifier_failure_section_ids = writer_failure_code_section_ids.get(
        "writer_required_qualifier_missing", []
    )
    writer_formal_eligibility_mismatch_section_ids = (
        writer_failure_code_section_ids.get(
            "writer_formal_eligibility_mismatch", []
        )
    )
    writer_formal_ineligible_section_ids = [
        section_id
        for section_id, result in writer_integrity_results.items()
        if not result.recomputed_formal_deliverable_eligible
    ]
    checks["writer_formal_deliverable_eligible"] = not (
        writer_formal_ineligible_section_ids
    )
    if not checks["writer_formal_deliverable_eligible"]:
        failures.append("存在经 Document Hard Gate 重算后不具备正式交付资格的章节")

    # Do not infer a safe no-evidence response from prose keywords.  A phrase
    # such as “证据不足，但本项目确定建设1000个节点” must not let the unsupported
    # claim hitchhike through the gate.  When evidence is insufficient, only
    # sections explicitly produced by the fail-closed generation branch count
    # as respecting the evidence boundary.
    substantive_sections = [
        section for section in draft.sections if str(section.content or "").strip()
    ]
    evidence_blocked_section_ids = [
        section.section_id
        for section in substantive_sections
        if bool((section.extra or {}).get("generation_blocked"))
        and (
            str(
                (section.extra or {}).get("generation_block_reason") or ""
            )
            == "evidence_insufficient"
            or str(
                (section.extra or {}).get("generation_block_reason") or ""
            ).startswith("writer_")
            or str(
                (section.extra or {}).get("writer_execution_status") or ""
            )
            in {"blocked", "human_review", "not_assessed"}
        )
    ]
    explicitly_blocked_for_no_evidence = bool(substantive_sections) and (
        len(evidence_blocked_section_ids) == len(substantive_sections)
    )
    checks["evidence_boundary_respected"] = (
        evidence_sufficient or explicitly_blocked_for_no_evidence
    )
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
            "unsupported_project_facts": unsupported_project_facts,
            "orphan_citation_marker_ids": orphan_marker_ids,
            "binding_without_marker_ids": binding_without_marker_ids,
            "marker_binding_count_mismatches": marker_binding_count_mismatches,
            "duplicate_binding_ids": duplicate_binding_ids,
            "invalid_binding_ids": invalid_binding_ids,
            "duplicate_citation_ids": duplicate_citation_ids,
            "unknown_binding_citation_ids": unknown_binding_citation_ids,
            "binding_lineage_mismatches": binding_lineage_mismatches,
            "section_marker_binding_mismatches": section_marker_binding_mismatches,
            "section_binding_payload_mismatches": section_binding_payload_mismatches,
            **occurrence_integrity.metadata,
            "project_input_parse_failure_section_ids": project_input_parse_failures,
            "recorded_unsupported_project_facts": (
                recorded_unsupported_project_facts
            ),
            "final_unsupported_project_facts": final_unsupported_project_facts,
            "partial_sections": partial_sections,
            "writer_status_by_section": writer_status_by_section,
            "writer_gate_missing_section_ids": writer_gate_missing_section_ids,
            "writer_gate_invalid_section_ids": writer_gate_invalid_section_ids,
            "writer_binding_identity_fields_missing_section_ids": (
                writer_binding_identity_fields_missing_section_ids
            ),
            "writer_binding_identity_mismatch_section_ids": (
                writer_binding_identity_mismatch_section_ids
            ),
            "writer_audit_identity_mismatch_section_ids": (
                writer_audit_identity_mismatch_section_ids
            ),
            "writer_evidence_lineage_fields_missing_section_ids": (
                writer_evidence_lineage_fields_missing_section_ids
            ),
            "legacy_writer_artifact_incomplete_section_ids": (
                legacy_writer_artifact_incomplete_section_ids
            ),
            "writer_stale_claim_audit_section_ids": (
                writer_stale_claim_audit_section_ids
            ),
            "writer_status_mismatch_section_ids": writer_status_mismatch_section_ids,
            "writer_blocked_section_ids": writer_blocked_section_ids,
            "writer_human_review_section_ids": writer_human_review_section_ids,
            "writer_claim_audit_missing_section_ids": (
                writer_claim_audit_missing_section_ids
            ),
            "writer_claim_audit_invalid_section_ids": (
                writer_claim_audit_invalid_section_ids
            ),
            "writer_claim_audit_failed_section_ids": (
                writer_claim_audit_failed_section_ids
            ),
            "writer_qualifier_failure_section_ids": (
                writer_qualifier_failure_section_ids
            ),
            "writer_formal_eligibility_mismatch_section_ids": (
                writer_formal_eligibility_mismatch_section_ids
            ),
            "writer_formal_ineligible_section_ids": (
                writer_formal_ineligible_section_ids
            ),
            "writer_failure_code_section_ids": writer_failure_code_section_ids,
            "writer_integrity_by_section": writer_integrity_by_section,
            "evidence_blocked_section_ids": evidence_blocked_section_ids,
            "explicitly_blocked_for_no_evidence": explicitly_blocked_for_no_evidence,
        },
    )


def extract_runtime_hard_failures(run_summary: Dict[str, Any]) -> list[str]:
    """从主链摘要提取嵌入的硬门禁失败；缺失明细时补充主链失败状态。"""

    failures: list[str] = []
    scheme = run_summary.get("scheme_writer_output") or {}
    hard_gate = scheme.get("hard_gate") or {}
    failures.extend(hard_gate.get("failures") or [])

    status = str(run_summary.get("status") or "")
    if status in {ExecutionStatus.FAILED.value, ExecutionStatus.RETRYABLE_FAILED.value}:
        if not failures:
            failures.append("主链路状态为失败")
    return list(dict.fromkeys(failures))
