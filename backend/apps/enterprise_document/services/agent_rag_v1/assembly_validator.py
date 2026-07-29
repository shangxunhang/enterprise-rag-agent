"""模板顺序、标题、目录、占位语和报告附录的确定性装配校验。"""

from __future__ import annotations

from apps.enterprise_document.schemas.agent_rag_v1 import (
    AssembledDocumentSchema,
    AssemblyValidationResultSchema,
    GenerationReportSchema,
    MultiAgentSharedStateSchema,
    SectionStatus,
)

from .artifact_store import FileArtifactStore
from .document_assembler_v1 import MultiAgentDocumentAssembler


class DocumentAssemblyValidator:
    def __init__(self, artifact_store: FileArtifactStore) -> None:
        self.artifact_store = artifact_store

    def validate(
        self,
        *,
        state: MultiAgentSharedStateSchema,
        assembled: AssembledDocumentSchema,
        report: GenerationReportSchema,
    ) -> AssemblyValidationResultSchema:
        failures: list[str] = []
        try:
            content = self.artifact_store.get_bytes(
                assembled.document_artifact_ref,
                task_id=state.state_meta.task_id,
            ).decode("utf-8")
            readable = True
        except (UnicodeDecodeError, ValueError, FileNotFoundError):
            content = ""
            readable = False
            failures.append("document_file_unreadable")

        included = [
            section
            for section in sorted(
                state.section_states.values(),
                key=lambda item: (item.section_order, item.section_id),
            )
            if section.execution.status != SectionStatus.SKIPPED
        ]
        headings = [
            f"## {index}. {section.section_title}"
            for index, section in enumerate(included, start=1)
        ]
        positions = [content.find(heading) for heading in headings]
        heading_order = bool(
            all(position >= 0 for position in positions)
            and positions == sorted(positions)
        )
        if not heading_order:
            failures.append("section_heading_order_mismatch")

        toc_matches = all(
            f"{index}. {section.section_title}" in content
            for index, section in enumerate(included, start=1)
        )
        if not toc_matches:
            failures.append("toc_body_mismatch")

        skipped_removed = all(
            section.section_title not in content
            for section in state.section_states.values()
            if section.execution.status == SectionStatus.SKIPPED
        )
        if not skipped_removed:
            failures.append("skipped_section_present")

        placeholders_correct = True
        for index, section in enumerate(included, start=1):
            start = content.find(f"## {index}. {section.section_title}")
            next_heading = f"## {index + 1}."
            end = content.find(next_heading, start + 1)
            if end < 0:
                end = len(content)
            block = content[start:end] if start >= 0 else ""
            if (
                section.execution.status == SectionStatus.PARTIALLY_ACCEPTED
                and MultiAgentDocumentAssembler.PARTIAL_NOTICE not in block
            ):
                placeholders_correct = False
            if (
                section.execution.status
                in {
                    SectionStatus.FAILED,
                    SectionStatus.BLOCKED,
                    SectionStatus.WAITING_HUMAN,
                }
                and MultiAgentDocumentAssembler.FAILED_NOTICE not in block
            ):
                placeholders_correct = False
        if not placeholders_correct:
            failures.append("section_placeholder_mismatch")

        report_appendix = "生成报告附录" in content
        if not report_appendix:
            failures.append("generation_report_appendix_missing")
        report_consistent = (
            assembled.task_status == report.task_status
            and assembled.generation_report_ref
            == f"artifact://{report.task_id}/generation-report/{report.document_version}"
        )
        if not report_consistent:
            failures.append("generation_report_state_mismatch")
        checks = {
            "file_readable": readable,
            "heading_order": heading_order,
            "toc_matches_body": toc_matches,
            "skipped_sections_removed": skipped_removed,
            "placeholders_correct": placeholders_correct,
            "generation_report_appendix_present": report_appendix,
            "generation_report_state_consistent": report_consistent,
        }
        return AssemblyValidationResultSchema(
            passed=all(checks.values()),
            checks=checks,
            failures=failures,
        )
