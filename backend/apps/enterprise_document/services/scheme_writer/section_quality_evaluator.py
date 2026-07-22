"""Evaluate final section quality without invoking retrieval or generation."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, List, Optional

from apps.enterprise_document.schemas.scheme_writer_schema import (
    SectionEvalSchema,
    SemanticGateIssueSchema,
    SemanticGateResultSchema,
    TruncationCheckSchema,
)
from schemas.common import ErrorSchema, ErrorSourceSchema, WarningSchema
from schemas.status import ExecutionStatus

from .runtime_support import SchemeWriterRuntimeSupport


@dataclass
class SectionQualityEvaluationResult:
    """Deterministic quality decision and the evidence used to explain it."""

    status: ExecutionStatus
    error: Optional[ErrorSchema]
    warnings: List[WarningSchema]
    eval_result: SectionEvalSchema
    semantic_hard_issues: List[SemanticGateIssueSchema]
    semantic_soft_issues: List[SemanticGateIssueSchema]
    semantic_warning_issues: List[SemanticGateIssueSchema]
    semantic_fact_issues: List[SemanticGateIssueSchema]
    semantic_scope_issues: List[SemanticGateIssueSchema]


class SectionQualityEvaluator:
    """Own hard/advisory checks, warning mapping and final section status."""

    _FACT_ISSUE_TYPES = {
        "unsupported_quantitative_claim",
        "unsupported_resource_commitment",
        "fabricated_project_fact",
        "evidence_contradiction",
        "missing_context_qualification",
    }
    _SCOPE_ISSUE_TYPES = {"section_scope_drift", "minor_scope_drift"}

    def __init__(self, *, runtime_support: SchemeWriterRuntimeSupport) -> None:
        self.runtime_support = runtime_support

    def evaluate(
        self,
        *,
        section_id: str,
        section_title: str,
        content: str,
        truncation: TruncationCheckSchema,
        max_section_chars: int,
        citation_ok: bool,
        semantic_gate: SemanticGateResultSchema,
        generation_check_result: Optional[Dict[str, Any]],
        repair_result: Optional[Dict[str, Any]],
        repair_accepted: bool,
        truncation_recovery_strategy: Optional[str],
        compression_fallback_strategy: Optional[str],
    ) -> SectionQualityEvaluationResult:
        """Convert final validation signals into one stable section decision."""

        semantic_hard_issues = [
            item for item in semantic_gate.issues if item.severity == "hard_failure"
        ]
        semantic_soft_issues = [
            item for item in semantic_gate.issues if item.severity == "soft_failure"
        ]
        semantic_warning_issues = [
            item for item in semantic_gate.issues if item.severity == "warning"
        ]
        semantic_fact_issues = [
            item
            for item in semantic_gate.issues
            if item.issue_type in self._FACT_ISSUE_TYPES
        ]
        semantic_scope_issues = [
            item
            for item in semantic_gate.issues
            if item.issue_type in self._SCOPE_ISSUE_TYPES
        ]

        hard_checks = {
            "model_success": True,
            "content_nonempty": bool(content.strip()),
            "not_truncated": not truncation.truncated,
            "citation_bound": citation_ok,
        }
        generation_supported = (
            generation_check_result is None
            or (
                bool(generation_check_result.get("is_supported"))
                and not bool(generation_check_result.get("need_rewrite"))
            )
        )
        generation_retrieval_sufficient = (
            generation_check_result is None
            or not bool(generation_check_result.get("need_retrieve_more"))
        )
        advisory_checks = {
            "section_length_within_limit": len(content) <= max_section_chars,
            "project_fact_boundary_respected": not semantic_fact_issues,
            "section_scope_respected": not semantic_scope_issues,
            "generation_checker_passed": generation_supported,
            "generation_retrieval_sufficient": generation_retrieval_sufficient,
        }
        checks = {**hard_checks, **advisory_checks}
        failures = [name for name, passed in hard_checks.items() if not passed]

        warning_names: list[str] = []
        if truncation_recovery_strategy:
            warning_names.append(
                f"truncation_recovered:{truncation_recovery_strategy}"
            )
        if compression_fallback_strategy:
            warning_names.append(
                f"compression_fallback:{compression_fallback_strategy}"
            )
        if not advisory_checks["section_length_within_limit"]:
            warning_names.append("section_length_exceeds_recommended_limit")
        if not advisory_checks["generation_checker_passed"]:
            warning_names.append("self_rag:generation_check_failed")
        if not advisory_checks["generation_retrieval_sufficient"]:
            warning_names.append("self_rag:retrieve_more_required")
        if repair_result is not None and repair_result.get("repaired") and not repair_accepted:
            warning_names.append("self_rag:repair_rejected")
        warning_names.extend(
            f"semantic:{item.issue_type}"
            for item in [
                *semantic_hard_issues,
                *semantic_soft_issues,
                *semantic_warning_issues,
            ]
        )
        warning_names = list(dict.fromkeys(warning_names))

        if failures:
            status = ExecutionStatus.FAILED
        elif warning_names:
            status = ExecutionStatus.PARTIAL_SUCCESS
        else:
            status = ExecutionStatus.SUCCESS

        if failures or warning_names:
            print(
                f"[SectionValidation] section={section_title} status={status.value} "
                f"hard_failures={failures} warnings={warning_names} "
                f"chars={len(content)} semantic_decision={semantic_gate.decision}",
                flush=True,
            )

        error = None
        if failures:
            error = self.runtime_support.error(
                "SECTION_HARD_GATE_FAILED",
                "; ".join(failures),
                node=section_id,
                retryable=True,
                user_message=f"‘{section_title}’章节存在不可放行的运行完整性问题。",
            )

        section_warnings = [
            WarningSchema(
                warning_code=self._warning_code(name),
                message=name,
                source=ErrorSourceSchema(
                    component="SchemeWriterAgent",
                    agent_name="SchemeWriterAgent",
                    step_name=section_id,
                ),
                details={
                    "section_title": section_title,
                    "semantic_gate": semantic_gate.model_dump(),
                    "generation_check": generation_check_result,
                    "repair_result": repair_result,
                },
                created_at=self.runtime_support.now_iso(),
            )
            for name in warning_names
        ]

        return SectionQualityEvaluationResult(
            status=status,
            error=error,
            warnings=section_warnings,
            eval_result=SectionEvalSchema(
                passed=not failures,
                checks=checks,
                failures=failures,
                warnings=warning_names,
                semantic_gate=semantic_gate,
            ),
            semantic_hard_issues=semantic_hard_issues,
            semantic_soft_issues=semantic_soft_issues,
            semantic_warning_issues=semantic_warning_issues,
            semantic_fact_issues=semantic_fact_issues,
            semantic_scope_issues=semantic_scope_issues,
        )

    @staticmethod
    def _warning_code(name: str) -> str:
        if name == "section_length_exceeds_recommended_limit":
            return "SECTION_LENGTH_RECOMMENDATION"
        if name.startswith("truncation_recovered:"):
            return "SECTION_TRUNCATION_RECOVERED"
        if name.startswith("compression_fallback:"):
            return "SECTION_COMPRESSION_FALLBACK"
        if name.startswith("self_rag:"):
            return "SECTION_SELF_RAG_WARNING"
        return "SECTION_SEMANTIC_WARNING"
