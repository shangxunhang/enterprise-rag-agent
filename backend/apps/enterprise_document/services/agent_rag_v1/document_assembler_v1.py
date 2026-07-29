"""所有 Task 终态都生成实际文档 Artifact。"""

from __future__ import annotations

from apps.enterprise_document.schemas.agent_rag_v1 import (
    AssembledDocumentSchema,
    GenerationReportSchema,
    MultiAgentSharedStateSchema,
    SectionStatus,
)

from .artifact_store import FileArtifactStore


class MultiAgentDocumentAssembler:
    PARTIAL_NOTICE = "本章节部分内容未生成，原因见生成报告。"
    FAILED_NOTICE = "本章节未生成，原因见生成报告。"

    def __init__(self, artifact_store: FileArtifactStore) -> None:
        self.artifact_store = artifact_store

    def assemble(
        self,
        *,
        state: MultiAgentSharedStateSchema,
        report: GenerationReportSchema,
        generation_report_ref: str,
        document_title: str,
        attempt_id: str,
    ) -> AssembledDocumentSchema:
        if (
            report.task_id != state.state_meta.task_id
            or report.run_id != state.state_meta.run_id
        ):
            raise ValueError("generation report identity mismatch")
        included = [
            section
            for section in sorted(
                state.section_states.values(),
                key=lambda item: (item.section_order, item.section_id),
            )
            if section.execution.status != SectionStatus.SKIPPED
        ]
        omitted = [
            section.section_id
            for section in state.section_states.values()
            if section.execution.status == SectionStatus.SKIPPED
        ]
        lines = [f"# {document_title}", "", "## 目录", ""]
        for index, section in enumerate(included, start=1):
            lines.append(f"{index}. {section.section_title}")
        lines.append(f"{len(included) + 1}. 生成报告附录")

        for index, section in enumerate(included, start=1):
            lines.extend(["", f"## {index}. {section.section_title}", ""])
            verified_ids = [
                paragraph_id
                for paragraph_id in section.verification.accepted_paragraph_ids
                if paragraph_id in section.writing.paragraph_refs
            ]
            for paragraph_id in verified_ids:
                content_ref = section.writing.paragraph_refs[paragraph_id]
                content = self.artifact_store.get_bytes(
                    content_ref,
                    task_id=state.state_meta.task_id,
                ).decode("utf-8")
                lines.extend([content.strip(), ""])
            if section.execution.status == SectionStatus.PARTIALLY_ACCEPTED:
                lines.append(
                    f"{section.section_title}：{self.PARTIAL_NOTICE}"
                )
            elif section.execution.status in {
                SectionStatus.FAILED,
                SectionStatus.BLOCKED,
                SectionStatus.WAITING_HUMAN,
                SectionStatus.PENDING,
                SectionStatus.PLANNED,
                SectionStatus.RESEARCHING,
                SectionStatus.EVIDENCE_READY,
                SectionStatus.GENERATING,
                SectionStatus.PAUSED_FOR_PRECHECK,
                SectionStatus.GENERATED,
                SectionStatus.VERIFYING,
            }:
                lines.append(
                    f"{section.section_title}：{self.FAILED_NOTICE}"
                )
            elif (
                section.execution.status == SectionStatus.ACCEPTED
                and not verified_ids
            ):
                lines.append(
                    f"{section.section_title}：{self.FAILED_NOTICE}"
                )

        lines.extend(["", f"## {len(included) + 1}. 生成报告附录", ""])
        if report.partial_success_reasons:
            for reason in report.partial_success_reasons:
                lines.extend(
                    [
                        f"- 章节：{reason.section_id}",
                        f"  - 阶段：{reason.stage}",
                        f"  - 结果：{reason.what_happened}",
                        f"  - 原因：{reason.stop_reason}",
                        (
                            "  - 可恢复：是"
                            if reason.recoverable
                            else "  - 可恢复：否"
                        ),
                        f"  - 恢复建议：{reason.recovery_action}",
                    ]
                )
        else:
            lines.append("- 所有适用章节均已完整生成并通过验证。")

        content = ("\n".join(lines).rstrip() + "\n").encode("utf-8")
        reference = self.artifact_store.put_bytes(
            content,
            artifact_type="assembled_document",
            schema_version="construction_scheme_markdown_v1",
            created_by="document_assembler",
            task_id=state.state_meta.task_id,
            attempt_id=attempt_id,
            input_snapshot_version=state.state_meta.input_snapshot_version,
            artifact_ref=(
                f"artifact://{state.state_meta.task_id}/assembled/"
                f"{state.state_meta.run_id}"
            ),
            content_encoding="markdown-utf8",
        )
        return AssembledDocumentSchema(
            task_id=state.state_meta.task_id,
            run_id=state.state_meta.run_id,
            task_status=report.task_status,
            document_title=document_title,
            document_artifact_ref=reference.artifact_ref,
            content_hash=reference.content_hash,
            included_section_ids=[section.section_id for section in included],
            omitted_section_ids=sorted(omitted),
            generation_report_ref=generation_report_ref,
        )
