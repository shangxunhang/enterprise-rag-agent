"""内部结构化 Generation Report 与用户可见恢复原因。"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Callable, Iterable, Mapping, TYPE_CHECKING

from apps.enterprise_document.schemas.agent_rag_v1 import (
    GenerationReportSchema,
    MultiAgentSharedStateSchema,
    PartialSuccessReasonSchema,
    SectionGenerationReportSchema,
    SectionStatus,
    TaskStatus,
)

from apps.enterprise_document.schemas.scheme_writer.document import SchemeSectionSchema
from apps.enterprise_document.schemas.scheme_writer.evaluation import HardGateResultSchema
from apps.enterprise_document.schemas.scheme_writer.state_projection import (
    SectionLifecycleStatus,
    SectionStateSchema as ProductionSectionStateSchema,
)
from schemas.status import ExecutionStatus

from .artifact_store import FileArtifactStore

if TYPE_CHECKING:
    from .task_status import TaskStatusCalculator


class GenerationReportService:
    def __init__(
        self,
        *,
        status_calculator: TaskStatusCalculator | None = None,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self.status_calculator = status_calculator
        self.clock = clock or (lambda: datetime.now(timezone.utc))

    def build_mainline(
        self,
        *,
        task_id: str,
        run_id: str,
        sections: Iterable[SchemeSectionSchema],
        section_states: Mapping[str, ProductionSectionStateSchema],
        document_version: str,
        hard_gate: HardGateResultSchema | None,
        source_versions: dict[str, str] | None = None,
        budget_usage: dict[str, Any] | None = None,
    ) -> GenerationReportSchema:
        """Build the business report from canonical production checkpoint data."""

        section_list = sorted(
            list(sections),
            key=lambda item: (item.section_order, item.section_id),
        )
        task_status = self._mainline_task_status(section_list, hard_gate)
        section_results: list[SectionGenerationReportSchema] = []
        partial_reasons: list[PartialSuccessReasonSchema] = []
        error_refs: list[str] = []
        for section in section_list:
            state = section_states.get(section.section_id)
            status = self._mainline_section_status(section, state)
            deliverable = []
            if state is not None:
                deliverable = sorted(
                    set(state.verification.accepted_paragraph_ids)
                    & set(state.writing.paragraph_refs)
                )
                if not deliverable and status == SectionStatus.ACCEPTED:
                    deliverable = sorted(state.writing.paragraph_refs)
            complete = status == SectionStatus.ACCEPTED
            stage = self._stage(status)
            reason = None
            if not complete:
                reason = (
                    state.attempt.last_error_ref
                    if state is not None
                    else section.error.error_id
                    if section.error is not None
                    else f"section_status={status.value}"
                )
            recoverable = status in {
                SectionStatus.WAITING_HUMAN,
                SectionStatus.BLOCKED,
                SectionStatus.FAILED,
                SectionStatus.PARTIALLY_ACCEPTED,
            }
            recovery_action = self._recovery_action(status)
            section_results.append(
                SectionGenerationReportSchema(
                    section_id=section.section_id,
                    section_title=section.section_title,
                    status=status,
                    deliverable_paragraph_ids=deliverable,
                    failure_stage=None if complete else stage,
                    reason=reason,
                    recoverable=recoverable,
                    recovery_action=recovery_action,
                )
            )
            if not complete and status != SectionStatus.SKIPPED:
                partial_reasons.append(
                    PartialSuccessReasonSchema(
                        section_id=section.section_id,
                        stage=stage,
                        what_happened=f"章节状态为 {status.value}",
                        stop_reason=reason or "章节未完整收敛",
                        recoverable=recoverable,
                        recovery_action=(
                            recovery_action or "人工复核后重新执行"
                        ),
                    )
                )
            if reason and str(reason).startswith("error"):
                error_refs.append(str(reason))
        return GenerationReportSchema(
            task_id=task_id,
            run_id=run_id,
            task_status=task_status,
            document_version=document_version,
            section_results=section_results,
            partial_success_reasons=partial_reasons,
            source_versions=dict(source_versions or {}),
            budget_usage=dict(budget_usage or {}),
            error_refs=sorted(set(error_refs)),
            generated_at=self.clock(),
        )

    @staticmethod
    def _mainline_task_status(
        sections: list[SchemeSectionSchema],
        hard_gate: HardGateResultSchema | None,
    ) -> TaskStatus:
        if hard_gate is not None and not hard_gate.passed:
            return TaskStatus.FAILED
        if not sections:
            return TaskStatus.FAILED
        successful = sum(
            section.status == ExecutionStatus.SUCCESS for section in sections
        )
        partial = sum(
            section.status == ExecutionStatus.PARTIAL_SUCCESS
            for section in sections
        )
        if successful == len(sections):
            return TaskStatus.SUCCESS
        if successful or partial:
            return TaskStatus.PARTIAL_SUCCESS
        return TaskStatus.FAILED

    @staticmethod
    def _mainline_section_status(
        section: SchemeSectionSchema,
        state: ProductionSectionStateSchema | None,
    ) -> SectionStatus:
        if state is not None:
            try:
                return SectionStatus(state.lifecycle_status.value)
            except ValueError:
                pass
        mapping = {
            ExecutionStatus.SUCCESS: SectionStatus.ACCEPTED,
            ExecutionStatus.PARTIAL_SUCCESS: SectionStatus.PARTIALLY_ACCEPTED,
            ExecutionStatus.FAILED: SectionStatus.FAILED,
            ExecutionStatus.RETRYABLE_FAILED: SectionStatus.FAILED,
            ExecutionStatus.CANCELLED: SectionStatus.FAILED,
            ExecutionStatus.SKIPPED: SectionStatus.SKIPPED,
        }
        return mapping.get(section.status, SectionStatus.GENERATED)

    def build(
        self,
        state: MultiAgentSharedStateSchema,
        *,
        document_version: str,
        budget_usage: dict[str, Any] | None = None,
    ) -> GenerationReportSchema:
        calculator = self.status_calculator
        if calculator is None:
            # Legacy Standalone-only dependency. Production build_mainline does
            # not import or instantiate the duplicate TaskStatusCalculator.
            from .task_status import TaskStatusCalculator

            calculator = TaskStatusCalculator()
        task_status = calculator.calculate(state)
        section_results: list[SectionGenerationReportSchema] = []
        partial_reasons: list[PartialSuccessReasonSchema] = []
        error_refs: list[str] = []
        for section in sorted(
            state.section_states.values(),
            key=lambda item: (item.section_order, item.section_id),
        ):
            deliverable = sorted(
                set(section.verification.accepted_paragraph_ids)
                & set(section.writing.paragraph_refs)
            )
            stage = self._stage(section.execution.status)
            complete = section.execution.status == SectionStatus.ACCEPTED
            reason = None if complete else (
                section.execution.last_error_ref
                or f"section_status={section.execution.status.value}"
            )
            recoverable = section.execution.status in {
                SectionStatus.WAITING_HUMAN,
                SectionStatus.BLOCKED,
                SectionStatus.FAILED,
                SectionStatus.PARTIALLY_ACCEPTED,
            }
            recovery_action = self._recovery_action(section.execution.status)
            section_results.append(
                SectionGenerationReportSchema(
                    section_id=section.section_id,
                    section_title=section.section_title,
                    status=section.execution.status,
                    deliverable_paragraph_ids=deliverable,
                    failure_stage=None if complete else stage,
                    reason=reason,
                    recoverable=recoverable,
                    recovery_action=recovery_action,
                )
            )
            if not complete and section.execution.status != SectionStatus.SKIPPED:
                partial_reasons.append(
                    PartialSuccessReasonSchema(
                        section_id=section.section_id,
                        stage=stage,
                        what_happened=(
                            f"章节状态为 {section.execution.status.value}"
                        ),
                        stop_reason=reason or "章节未完整收敛",
                        recoverable=recoverable,
                        recovery_action=recovery_action or "人工复核后重新执行",
                    )
                )
            if section.execution.last_error_ref:
                error_refs.append(section.execution.last_error_ref)
        return GenerationReportSchema(
            task_id=state.state_meta.task_id,
            run_id=state.state_meta.run_id,
            task_status=task_status,
            document_version=document_version,
            section_results=section_results,
            partial_success_reasons=partial_reasons,
            source_versions=state.source_snapshot.source_versions,
            budget_usage=dict(budget_usage or {}),
            error_refs=sorted(set(error_refs)),
            generated_at=self.clock(),
        )

    @staticmethod
    def persist(
        report: GenerationReportSchema,
        *,
        artifact_store: FileArtifactStore,
        attempt_id: str,
        input_snapshot_version: str,
    ) -> str:
        reference = artifact_store.put_json(
            report.model_dump(mode="json"),
            artifact_type="generation_report",
            schema_version=report.schema_version,
            created_by="supervisor",
            task_id=report.task_id,
            attempt_id=attempt_id,
            input_snapshot_version=input_snapshot_version,
            artifact_ref=(
                f"artifact://{report.task_id}/generation-report/"
                f"{report.document_version}"
            ),
        )
        return reference.artifact_ref

    @staticmethod
    def _stage(status: SectionStatus) -> str:
        if status in {SectionStatus.PENDING, SectionStatus.PLANNED}:
            return "planner"
        if status in {SectionStatus.RESEARCHING, SectionStatus.EVIDENCE_READY}:
            return "research"
        if status in {
            SectionStatus.GENERATING,
            SectionStatus.PAUSED_FOR_PRECHECK,
            SectionStatus.GENERATED,
        }:
            return "writer"
        if status in {
            SectionStatus.VERIFYING,
            SectionStatus.PARTIALLY_ACCEPTED,
            SectionStatus.ACCEPTED,
        }:
            return "verifier"
        return "workflow"

    @staticmethod
    def _recovery_action(status: SectionStatus) -> str | None:
        if status == SectionStatus.WAITING_HUMAN:
            return "提交人工答复后从 ResumeRouter 恢复"
        if status == SectionStatus.BLOCKED:
            return "解除硬依赖或补充输入后恢复"
        if status == SectionStatus.FAILED:
            return "根据错误引用从最小可恢复阶段重试"
        if status == SectionStatus.PARTIALLY_ACCEPTED:
            return "仅重跑未通过的 Paragraph 或验证步骤"
        return None
