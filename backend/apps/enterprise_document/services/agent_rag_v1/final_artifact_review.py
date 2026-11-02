"""最终用户成品的轻量审阅与问题分流。"""

from __future__ import annotations

from apps.enterprise_document.schemas.agent_rag_v1 import (
    AssembledDocumentSchema,
    AssemblyValidationResultSchema,
    FinalArtifactReviewResultSchema,
    GenerationReportSchema,
)
from apps.enterprise_document.schemas.scheme_writer.document import SchemeDraftSchema

from .artifact_store import FileArtifactStore


class FinalArtifactReviewService:
    _PROMPT_LEAK_MARKERS = (
        "system prompt",
        "assistant:",
        "作为一个AI",
        "内部指令",
    )
    _MARKETING_MARKERS = (
        "行业领先",
        "业界领先",
        "遥遥领先",
        "最佳选择",
        "首选品牌",
    )

    def __init__(self, artifact_store: FileArtifactStore | None = None) -> None:
        self.artifact_store = artifact_store

    def review_mainline(
        self,
        *,
        draft: SchemeDraftSchema,
        assembly_validation: AssemblyValidationResultSchema,
        old_project_names: list[str] | None = None,
    ) -> FinalArtifactReviewResultSchema:
        """Review the canonical production draft without publishing a side artifact."""

        if not assembly_validation.passed:
            return FinalArtifactReviewResultSchema(
                passed=False,
                issue_codes=list(assembly_validation.failures),
                route_to="document_assembler",
            )
        content = draft.full_text
        issues: list[str] = []
        lowered = content.casefold()
        if any(
            marker.casefold() in lowered
            for marker in self._PROMPT_LEAK_MARKERS
        ):
            issues.append("prompt_or_internal_information_leak")
        if any(name and name in content for name in old_project_names or []):
            issues.append("old_project_name_residue")
        if any(marker in content for marker in self._MARKETING_MARKERS):
            issues.append("vendor_marketing_language")
        body_lines = [
            line.strip()
            for line in content.splitlines()
            if line.strip()
            and not line.startswith("#")
            and not line.startswith("-")
        ]
        if any(
            left == right
            for left, right in zip(body_lines, body_lines[1:])
        ):
            issues.append("adjacent_duplicate_content")
        if draft.missing_sections:
            issues.append("required_section_missing")
        if issues:
            return FinalArtifactReviewResultSchema(
                passed=False,
                issue_codes=sorted(set(issues)),
                route_to=(
                    "document_assembler"
                    if "required_section_missing" in issues
                    else "section_verify"
                ),
                affected_section_ids=[
                    section.section_id for section in draft.sections
                ],
            )
        return FinalArtifactReviewResultSchema(passed=True)

    def review(
        self,
        *,
        assembled: AssembledDocumentSchema,
        assembly_validation: AssemblyValidationResultSchema,
        report: GenerationReportSchema,
        old_project_names: list[str] | None = None,
    ) -> FinalArtifactReviewResultSchema:
        if self.artifact_store is None:
            raise ValueError("standalone final review requires artifact_store")
        if not assembly_validation.passed:
            return FinalArtifactReviewResultSchema(
                passed=False,
                issue_codes=assembly_validation.failures,
                route_to="document_assembler",
            )
        if assembled.task_status != report.task_status:
            return FinalArtifactReviewResultSchema(
                passed=False,
                issue_codes=["task_status_mismatch"],
                route_to="supervisor",
            )
        content = self.artifact_store.get_bytes(
            assembled.document_artifact_ref,
            task_id=assembled.task_id,
        ).decode("utf-8")
        issues: list[str] = []
        lowered = content.casefold()
        if any(
            marker.casefold() in lowered
            for marker in self._PROMPT_LEAK_MARKERS
        ):
            issues.append("prompt_or_internal_information_leak")
        if any(name and name in content for name in old_project_names or []):
            issues.append("old_project_name_residue")
        if any(marker in content for marker in self._MARKETING_MARKERS):
            issues.append("vendor_marketing_language")
        body_lines = [
            line.strip()
            for line in content.splitlines()
            if line.strip()
            and not line.startswith("#")
            and not line.startswith("-")
        ]
        if any(
            left == right
            for left, right in zip(body_lines, body_lines[1:])
        ):
            issues.append("adjacent_duplicate_content")
        if issues:
            return FinalArtifactReviewResultSchema(
                passed=False,
                issue_codes=sorted(set(issues)),
                route_to="section_verify",
                affected_section_ids=assembled.included_section_ids,
            )
        return FinalArtifactReviewResultSchema(passed=True)
