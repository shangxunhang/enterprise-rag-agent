"""模板顺序、标题、目录、占位语和报告附录的确定性装配校验。"""

from __future__ import annotations

from apps.enterprise_document.schemas.agent_rag_v1 import (
    AssembledDocumentSchema,
    AssemblyValidationResultSchema,
    GenerationReportSchema,
    MultiAgentSharedStateSchema,
    SectionStatus,
)
from apps.enterprise_document.schemas.scheme_writer.document_assembly import (
    DocumentAssemblyResultSchema,
)

from .artifact_store import FileArtifactStore
from .document_assembler_v1 import MultiAgentDocumentAssembler


class DocumentAssemblyValidator:
    def __init__(self, artifact_store: FileArtifactStore | None = None) -> None:
        self.artifact_store = artifact_store

    def validate_mainline(
        self,
        *,
        assembly: DocumentAssemblyResultSchema,
        required_sections: list[str],
    ) -> AssemblyValidationResultSchema:
        """Validate the canonical in-memory production assembly.

        No file artifact is introduced: the existing ``DocumentAssembler``
        remains the only assembly authority and its ``SchemeDraft`` is checked
        directly before the final Hard Gate.
        """

        draft = assembly.draft
        failures: list[str] = []
        section_ids = [item.section_id for item in draft.sections]
        unique_section_ids = len(section_ids) == len(set(section_ids))
        if not unique_section_ids:
            failures.append("duplicate_section_id")
        ordered = [
            item.section_id
            for item in sorted(
                draft.sections,
                key=lambda value: (value.section_order, value.section_id),
            )
        ]
        section_order = section_ids == ordered
        if not section_order:
            failures.append("section_order_mismatch")
        titles = [item.section_title for item in draft.sections]
        required_present = all(title in titles for title in required_sections)
        if not required_present:
            failures.append("required_section_missing")
        missing_consistent = draft.missing_sections == [
            title for title in required_sections if title not in set(titles)
        ]
        if not missing_consistent:
            failures.append("missing_section_projection_mismatch")
        full_text_order = True
        positions: list[int] = []
        for index, section in enumerate(draft.sections, start=1):
            positions.append(
                draft.full_text.find(f"{index}、{section.section_title}")
            )
        if positions and (
            any(position < 0 for position in positions)
            or positions != sorted(positions)
        ):
            full_text_order = False
            failures.append("full_text_section_order_mismatch")
        known_citation_ids = {item.citation_id for item in assembly.citations}
        valid_section_ids = set(section_ids)
        binding_targets_valid = all(
            binding.target_section_id in valid_section_ids
            and binding.citation_id in known_citation_ids
            for binding in assembly.citation_bindings
        )
        if not binding_targets_valid:
            failures.append("citation_binding_target_invalid")
        evidence_projection_consistent = all(
            item.section_title in set(required_sections)
            for item in assembly.section_evidence_requirements
        )
        if not evidence_projection_consistent:
            failures.append("section_evidence_projection_invalid")
        checks = {
            "draft_nonempty": bool(draft.full_text.strip()),
            "section_ids_unique": unique_section_ids,
            "section_order": section_order,
            "required_sections_present": required_present,
            "missing_sections_consistent": missing_consistent,
            "full_text_section_order": full_text_order,
            "citation_binding_targets_valid": binding_targets_valid,
            "section_evidence_projection_consistent": (
                evidence_projection_consistent
            ),
        }
        if not checks["draft_nonempty"]:
            failures.append("document_draft_empty")
        return AssemblyValidationResultSchema(
            passed=all(checks.values()),
            checks=checks,
            failures=list(dict.fromkeys(failures)),
        )

    def validate(
        self,
        *,
        state: MultiAgentSharedStateSchema,
        assembled: AssembledDocumentSchema,
        report: GenerationReportSchema,
    ) -> AssemblyValidationResultSchema:
        if self.artifact_store is None:
            raise ValueError("standalone assembly validation requires artifact_store")
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
