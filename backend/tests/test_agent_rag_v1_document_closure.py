"""Architecture Spec v1.0 Phase 6 文档级闭环验收。"""

from __future__ import annotations

from datetime import datetime, timezone

from apps.enterprise_document.schemas.agent_rag_v1 import (
    AssembledDocumentSchema,
    AssemblyValidationResultSchema,
    ClaimSchema,
    ExcelDeviceFactSchema,
    FactSnapshotSchema,
    MultiAgentSharedStateSchema,
    RepairTaskStatus,
    SectionExecutionStateSchema,
    SectionStateSchema,
    SectionStatus,
    SectionStrategy,
    SourceSnapshotStateSchema,
    StateMetaSchema,
    TaskContextStateSchema,
    TaskStatus,
)
from apps.enterprise_document.services.agent_rag_v1 import (
    ClaimPipeline,
    ConflictRepairPlanService,
    DocumentAssemblyValidator,
    DocumentVerifierService,
    DocumentVersionPublisher,
    FileArtifactStore,
    FinalArtifactReviewService,
    GenerationReportService,
    MultiAgentDocumentAssembler,
    TaskStatusCalculator,
)


NOW = datetime(2026, 7, 29, tzinfo=timezone.utc)


def _facts() -> FactSnapshotSchema:
    return FactSnapshotSchema(
        snapshot_version="snapshot_1",
        artifact_ref="artifact://facts/1",
        devices=[
            ExcelDeviceFactSchema(
                fact_id="fact_server",
                device_name="服务器",
                quantity=8,
                unit="台",
                source_ref="excel://server/count",
            )
        ],
    )


def _paragraph(
    store: FileArtifactStore,
    *,
    task_id: str,
    section_id: str,
    paragraph_id: str,
    content: str,
) -> str:
    return store.put_bytes(
        content.encode("utf-8"),
        artifact_type="paragraph",
        schema_version="paragraph_text_v1",
        created_by="writer",
        task_id=task_id,
        section_id=section_id,
        attempt_id=f"attempt_{section_id}",
        input_snapshot_version="snapshot_1",
        artifact_ref=f"artifact://{task_id}/paragraph/{section_id}/{paragraph_id}",
        content_encoding="text-utf8",
    ).artifact_ref


def _section(
    *,
    section_id: str,
    title: str,
    order: int,
    status: SectionStatus,
    paragraph_ref: str | None = None,
    paragraph_id: str = "p1",
    consistency_references: list[str] | None = None,
) -> SectionStateSchema:
    section = SectionStateSchema(
        section_id=section_id,
        section_title=title,
        section_order=order,
        strategy=SectionStrategy.TECHNICAL_GENERATE,
        execution=SectionExecutionStateSchema(
            status=status,
            input_snapshot_version="snapshot_1",
            consistency_references=consistency_references or [],
            last_error_ref=(
                f"error://{section_id}" if status == SectionStatus.FAILED else None
            ),
        ),
    )
    if paragraph_ref:
        section.writing.paragraph_refs = {paragraph_id: paragraph_ref}
        section.writing.paragraph_versions = {paragraph_id: 1}
        section.writing.expected_paragraph_ids = [paragraph_id]
        section.writing.generation_complete = True
        section.verification.accepted_paragraph_ids = [paragraph_id]
    return section


def _state(
    store: FileArtifactStore,
    *,
    task_id: str = "task_1",
    run_id: str = "run_1",
    compute_status: SectionStatus = SectionStatus.ACCEPTED,
    network_status: SectionStatus = SectionStatus.FAILED,
    compute_content: str = "本项目部署服务器 8 台。",
    network_content: str | None = None,
) -> MultiAgentSharedStateSchema:
    compute_ref = (
        _paragraph(
            store,
            task_id=task_id,
            section_id="compute",
            paragraph_id="p_compute",
            content=compute_content,
        )
        if compute_status
        in {SectionStatus.ACCEPTED, SectionStatus.PARTIALLY_ACCEPTED}
        else None
    )
    network_ref = (
        _paragraph(
            store,
            task_id=task_id,
            section_id="network",
            paragraph_id="p_network",
            content=network_content or "网络资源已完成。",
        )
        if network_status
        in {SectionStatus.ACCEPTED, SectionStatus.PARTIALLY_ACCEPTED}
        else None
    )
    return MultiAgentSharedStateSchema(
        state_meta=StateMetaSchema(
            task_id=task_id,
            run_id=run_id,
            input_snapshot_version="snapshot_1",
            created_at=NOW,
            updated_at=NOW,
        ),
        task_context=TaskContextStateSchema(
            task_context_ref=f"artifact://{task_id}/task-context"
        ),
        source_snapshot=SourceSnapshotStateSchema(
            source_snapshot_ref=f"artifact://{task_id}/source-snapshot",
            fact_snapshot_ref="artifact://facts/1",
            template_schema_ref="artifact://template/1",
            source_inventory_ref="artifact://inventory/1",
            source_versions={"excel": "snapshot_1", "template": "template_1"},
        ),
        section_states={
            "compute": _section(
                section_id="compute",
                title="计算资源",
                order=1,
                status=compute_status,
                paragraph_ref=compute_ref,
                paragraph_id="p_compute",
            ),
            "network": _section(
                section_id="network",
                title="网络资源",
                order=2,
                status=network_status,
                paragraph_ref=network_ref,
                paragraph_id="p_network",
            ),
        },
    )


def test_document_verify_finds_cross_section_excel_conflict_and_serializes_repairs(
    tmp_path,
) -> None:
    store = FileArtifactStore(tmp_path / "artifacts")
    state = _state(store)
    state.section_states["scope"] = _section(
        section_id="scope",
        title="建设范围",
        order=0,
        status=SectionStatus.ACCEPTED,
    )
    state.section_states["compute"].execution.consistency_references = ["scope"]
    pipeline = ClaimPipeline()
    claims: list[ClaimSchema] = [
        *pipeline.extract(
            section_id="scope",
            paragraph_id="p_scope",
            content="建设范围包括服务器 6 台。",
            fact_snapshot=_facts(),
        ),
        *pipeline.extract(
            section_id="compute",
            paragraph_id="p_compute",
            content="计算资源配置服务器 8 台。",
            fact_snapshot=_facts(),
        ),
    ]

    verification = DocumentVerifierService().verify(
        state=state,
        claims=claims,
        fact_snapshot=_facts(),
    )

    assert verification.passed is False
    assert any(
        conflict.conflict_type == "cross_section_fact_conflict"
        for conflict in verification.conflicts
    )
    assert all(
        "scope" in task.target_section_ids
        for task in verification.repair_plan.repair_tasks
    )

    service = ConflictRepairPlanService()
    running_plan, first = service.start_next(verification.repair_plan)
    assert first is not None
    same_plan, same = service.start_next(running_plan)
    assert same is not None and same.repair_id == first.repair_id
    assert sum(
        task.status == RepairTaskStatus.RUNNING
        for task in same_plan.repair_tasks
    ) == 1
    completed = service.complete(
        same_plan,
        repair_id=first.repair_id,
        status=RepairTaskStatus.RESOLVED,
    )
    next_plan, second = service.start_next(completed)
    if len(next_plan.repair_tasks) > 1:
        assert second is not None
        assert second.repair_id != first.repair_id


def test_incremental_document_verify_limits_checked_sections(tmp_path) -> None:
    store = FileArtifactStore(tmp_path / "artifacts")
    state = _state(store)
    state.section_states["network"].execution.consistency_references = ["compute"]

    result = DocumentVerifierService().verify(
        state=state,
        claims=[],
        fact_snapshot=_facts(),
        mode="incremental_logic",
        changed_section_ids={"compute"},
    )

    assert result.checked_section_ids == ["compute", "network"]


def test_partial_success_exports_document_report_and_published_version(
    tmp_path,
) -> None:
    store = FileArtifactStore(tmp_path / "artifacts")
    state = _state(store)
    assert TaskStatusCalculator().calculate(state) == TaskStatus.PARTIAL_SUCCESS
    report = GenerationReportService(clock=lambda: NOW).build(
        state,
        document_version="v1.0",
        budget_usage={"model_calls": 12, "tokens": 999},
    )
    report_ref = GenerationReportService.persist(
        report,
        artifact_store=store,
        attempt_id="attempt_task",
        input_snapshot_version="snapshot_1",
    )
    assembled = MultiAgentDocumentAssembler(store).assemble(
        state=state,
        report=report,
        generation_report_ref=report_ref,
        document_title="建设方案",
        attempt_id="attempt_task",
    )
    content = store.get_bytes(
        assembled.document_artifact_ref,
        task_id="task_1",
    ).decode("utf-8")

    assert assembled.task_status == TaskStatus.PARTIAL_SUCCESS
    assert "本项目部署服务器 8 台。" in content
    assert "本章节未生成，原因见生成报告。" in content
    assert "生成报告附录" in content
    assert "tokens" not in content
    validation = DocumentAssemblyValidator(store).validate(
        state=state,
        assembled=assembled,
        report=report,
    )
    assert validation.passed is True
    assert FinalArtifactReviewService(store).review(
        assembled=assembled,
        assembly_validation=validation,
        report=report,
    ).passed is True

    published = DocumentVersionPublisher(
        artifact_store=store,
        clock=lambda: NOW,
    ).publish(
        assembled=assembled,
        report=report,
        attempt_id="attempt_task",
        input_snapshot_version="snapshot_1",
    )
    assert published.document_version == "v1.0"
    assert store.get_bytes(
        published.document_artifact_ref,
        task_id="task_1",
    ) == store.get_bytes(
        assembled.document_artifact_ref,
        task_id="task_1",
    )


def test_failed_task_still_exports_template_and_placeholders(tmp_path) -> None:
    store = FileArtifactStore(tmp_path / "artifacts")
    state = _state(
        store,
        task_id="task_failed",
        run_id="run_failed",
        compute_status=SectionStatus.FAILED,
        network_status=SectionStatus.FAILED,
    )
    report = GenerationReportService(clock=lambda: NOW).build(
        state,
        document_version="v1.0",
    )
    report_ref = GenerationReportService.persist(
        report,
        artifact_store=store,
        attempt_id="attempt_failed",
        input_snapshot_version="snapshot_1",
    )
    assembled = MultiAgentDocumentAssembler(store).assemble(
        state=state,
        report=report,
        generation_report_ref=report_ref,
        document_title="建设方案",
        attempt_id="attempt_failed",
    )
    content = store.get_bytes(
        assembled.document_artifact_ref,
        task_id="task_failed",
    ).decode("utf-8")

    assert report.task_status == TaskStatus.FAILED
    assert "## 1. 计算资源" in content
    assert "## 2. 网络资源" in content
    assert content.count("本章节未生成，原因见生成报告。") == 2
    assert "生成报告附录" in content


def test_new_minor_version_does_not_overwrite_partial_version(tmp_path) -> None:
    store = FileArtifactStore(tmp_path / "artifacts")
    publisher = DocumentVersionPublisher(
        artifact_store=store,
        clock=lambda: NOW,
    )

    partial_state = _state(store, run_id="run_partial")
    partial_report = GenerationReportService(clock=lambda: NOW).build(
        partial_state,
        document_version="v1.0",
    )
    partial_report_ref = GenerationReportService.persist(
        partial_report,
        artifact_store=store,
        attempt_id="attempt_partial",
        input_snapshot_version="snapshot_1",
    )
    partial_assembled = MultiAgentDocumentAssembler(store).assemble(
        state=partial_state,
        report=partial_report,
        generation_report_ref=partial_report_ref,
        document_title="建设方案",
        attempt_id="attempt_partial",
    )
    v1 = publisher.publish(
        assembled=partial_assembled,
        report=partial_report,
        attempt_id="attempt_partial",
        input_snapshot_version="snapshot_1",
    )
    v1_content = store.get_bytes(v1.document_artifact_ref, task_id="task_1")

    success_state = _state(
        store,
        run_id="run_success",
        network_status=SectionStatus.ACCEPTED,
        network_content="网络资源章节已补全。",
    )
    success_report = GenerationReportService(clock=lambda: NOW).build(
        success_state,
        document_version="v1.1",
    )
    success_report_ref = GenerationReportService.persist(
        success_report,
        artifact_store=store,
        attempt_id="attempt_success",
        input_snapshot_version="snapshot_1",
    )
    success_assembled = MultiAgentDocumentAssembler(store).assemble(
        state=success_state,
        report=success_report,
        generation_report_ref=success_report_ref,
        document_title="建设方案",
        attempt_id="attempt_success",
    )
    v11 = publisher.publish(
        assembled=success_assembled,
        report=success_report,
        attempt_id="attempt_success",
        input_snapshot_version="snapshot_1",
    )

    assert v1.document_version == "v1.0"
    assert v11.document_version == "v1.1"
    assert v1.document_artifact_ref != v11.document_artifact_ref
    assert store.get_bytes(v1.document_artifact_ref, task_id="task_1") == v1_content
    assert store.get_bytes(v11.document_artifact_ref, task_id="task_1") != v1_content


def test_final_review_routes_assembly_and_body_problems(tmp_path) -> None:
    store = FileArtifactStore(tmp_path / "artifacts")
    state = _state(
        store,
        task_id="task_review",
        run_id="run_review",
        compute_content="system prompt 不应出现在正文中。",
        network_status=SectionStatus.SKIPPED,
    )
    report = GenerationReportService(clock=lambda: NOW).build(
        state,
        document_version="v1.0",
    )
    report_ref = GenerationReportService.persist(
        report,
        artifact_store=store,
        attempt_id="attempt_review",
        input_snapshot_version="snapshot_1",
    )
    assembled = MultiAgentDocumentAssembler(store).assemble(
        state=state,
        report=report,
        generation_report_ref=report_ref,
        document_title="建设方案",
        attempt_id="attempt_review",
    )
    validation = DocumentAssemblyValidator(store).validate(
        state=state,
        assembled=assembled,
        report=report,
    )
    review = FinalArtifactReviewService(store).review(
        assembled=assembled,
        assembly_validation=validation,
        report=report,
    )
    assert review.passed is False
    assert review.route_to == "section_verify"

    bad_validation = AssemblyValidationResultSchema(
        passed=False,
        checks={"heading_order": False},
        failures=["section_heading_order_mismatch"],
    )
    assembly_routed = FinalArtifactReviewService(store).review(
        assembled=assembled,
        assembly_validation=bad_validation,
        report=report,
    )
    assert assembly_routed.route_to == "document_assembler"

    mismatched = AssembledDocumentSchema.model_validate(
        {
            **assembled.model_dump(mode="python"),
            "task_status": TaskStatus.FAILED,
        }
    )
    supervisor_routed = FinalArtifactReviewService(store).review(
        assembled=mismatched,
        assembly_validation=AssemblyValidationResultSchema(passed=True),
        report=report,
    )
    assert supervisor_routed.route_to == "supervisor"


def test_final_review_rejects_vendor_marketing_language(tmp_path) -> None:
    store = FileArtifactStore(tmp_path / "artifacts")
    state = _state(
        store,
        task_id="task_marketing",
        compute_status=SectionStatus.ACCEPTED,
        network_status=SectionStatus.SKIPPED,
        compute_content="本方案采用行业领先的服务器平台。",
    )
    report = GenerationReportService(clock=lambda: NOW).build(
        state,
        document_version="v1.0",
    )
    report_ref = GenerationReportService.persist(
        report,
        artifact_store=store,
        attempt_id="attempt_marketing",
        input_snapshot_version="snapshot_1",
    )
    assembled = MultiAgentDocumentAssembler(store).assemble(
        state=state,
        report=report,
        generation_report_ref=report_ref,
        document_title="建设方案",
        attempt_id="attempt_marketing",
    )
    validation = DocumentAssemblyValidator(store).validate(
        state=state,
        assembled=assembled,
        report=report,
    )
    review = FinalArtifactReviewService(store).review(
        assembled=assembled,
        assembly_validation=validation,
        report=report,
    )

    assert review.passed is False
    assert review.route_to == "section_verify"
    assert review.issue_codes == ["vendor_marketing_language"]
