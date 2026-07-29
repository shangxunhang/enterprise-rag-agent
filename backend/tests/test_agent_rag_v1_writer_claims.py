"""Architecture Spec v1.0 Phase 5 Writer 与 Claim Pipeline 验收。"""

from __future__ import annotations

from datetime import datetime, timezone

from apps.enterprise_document.schemas.agent_rag_v1 import (
    AgentExecutionContextSchema,
    AgentRole,
    CandidateEvidenceItemSchema,
    ClaimLifecycleStatus,
    ClaimVerificationStatus,
    DraftSnapshotSchema,
    ExcelDeviceFactSchema,
    FactSnapshotSchema,
    GeneratedParagraphInputSchema,
    ParagraphArtifactSchema,
    ParagraphChangeSchema,
    ParagraphChangeType,
    WriterRevisionResultSchema,
)
from apps.enterprise_document.services.agent_rag_v1 import (
    ClaimPipeline,
    DraftDiffService,
    EvidenceSanitizer,
    FileArtifactStore,
    IncrementalClaimPipeline,
    JsonlWriterProgressEventLog,
    ParagraphIdentityService,
    ParagraphPrecheckService,
    StreamingWriterService,
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
                aliases=["计算节点"],
                model="S-01",
                specification="2U",
                quantity=8,
                unit="台",
                unit_price=10000,
                total_amount=80000,
                source_ref="excel://sheet1/server",
            )
        ],
    )


def _writer_context() -> AgentExecutionContextSchema:
    return AgentExecutionContextSchema(
        invocation_id="writer_stream_1",
        logical_operation_id="writer_stream",
        task_id="task_1",
        run_id="run_1",
        section_id="compute",
        agent_role=AgentRole.WRITER,
        execution_mode="technical_generate",
        execution_id="exec_compute",
        attempt_id="attempt_compute",
        input_snapshot_version="snapshot_1",
        task_context_ref="artifact://task-context/1",
        source_snapshot_ref="artifact://source-snapshot/1",
        section_state_ref="artifact://section-state/compute",
        authorized_artifact_refs=["artifact://facts/1"],
    )


def test_vendor_evidence_is_project_adapted_and_excel_validated() -> None:
    result = EvidenceSanitizer().sanitize(
        candidates=[
            CandidateEvidenceItemSchema(
                evidence_id="vendor_generic",
                source_ref="vendor://guide/1",
                source_kind="vendor_material",
                content="旧城项目采用分层部署，可按业务规模扩展。",
                mentioned_project_names=["旧城项目"],
                general_technical_content=True,
            ),
            CandidateEvidenceItemSchema(
                evidence_id="vendor_old_model",
                source_ref="vendor://catalog/old",
                source_kind="vendor_material",
                content="服务器建议采用 OLD-9 型号。",
                device_name="服务器",
                claimed_model="OLD-9",
            ),
        ],
        fact_snapshot=_facts(),
        expected_snapshot_version="snapshot_1",
        current_project_name="新城项目",
        old_project_names=["旧城项目"],
    )

    assert [item.evidence_id for item in result.accepted] == ["vendor_generic"]
    assert "旧城项目" not in result.accepted[0].sanitized_content
    assert "本项目" in result.accepted[0].sanitized_content
    assert result.accepted[0].project_adapted is True
    assert result.accepted[0].validation_reason_codes == [
        "project_name_adapted",
        "general_technical_content_validated",
    ]
    assert [item.evidence_id for item in result.rejected] == ["vendor_old_model"]
    assert result.rejected[0].reason_code == "excel_model_conflict"


def _paragraph_artifact(
    store: FileArtifactStore,
    *,
    paragraph_id: str,
    version: int,
    order: int,
    content: str,
) -> ParagraphArtifactSchema:
    reference = store.put_bytes(
        content.encode("utf-8"),
        artifact_type="paragraph",
        schema_version="paragraph_text_v1",
        created_by="writer",
        task_id="task_1",
        section_id="compute",
        attempt_id=f"attempt_{version}",
        input_snapshot_version="snapshot_1",
        artifact_ref=(
            f"artifact://task_1/paragraph/compute/{paragraph_id}/v{version}"
        ),
        content_encoding="text-utf8",
    )
    return ParagraphArtifactSchema(
        paragraph_id=paragraph_id,
        paragraph_version=version,
        order=order,
        role="body",
        content_ref=reference.artifact_ref,
        content_hash=reference.content_hash,
    )


def test_excel_quantity_is_absolute_authority() -> None:
    precheck = ParagraphPrecheckService(ClaimPipeline())

    correct = precheck.check(
        section_id="compute",
        paragraph_id="p1",
        content="本项目部署服务器 8 台，型号为 S-01。",
        fact_snapshot=_facts(),
    )
    conflict = precheck.check(
        section_id="compute",
        paragraph_id="p2",
        content="历史方案建议部署服务器 6 台，型号为 S-01。",
        fact_snapshot=_facts(),
    )

    quantity = next(
        claim for claim in correct.claims if claim.claim_type == "quantity"
    )
    assert quantity.verification_status == ClaimVerificationStatus.VERIFIED
    assert quantity.source_bindings[0].source_ref == "excel://sheet1/server"
    assert correct.blocking is False

    wrong_quantity = next(
        claim for claim in conflict.claims if claim.claim_type == "quantity"
    )
    assert wrong_quantity.normalized_value == 6
    assert wrong_quantity.verification_status == ClaimVerificationStatus.CONFLICTED
    assert conflict.blocking is True
    assert any(
        issue.issue_code == "EXCEL_QUANTITY_CONFLICT"
        for issue in conflict.issues
    )


def test_precheck_rejects_device_missing_from_excel() -> None:
    result = ParagraphPrecheckService(ClaimPipeline()).check(
        section_id="compute",
        paragraph_id="p1",
        content="本项目新增交换机 2 台用于网络接入。",
        fact_snapshot=_facts(),
    )

    assert result.blocking is True
    assert any(issue.issue_code == "DEVICE_NOT_IN_EXCEL" for issue in result.issues)


def test_streaming_writer_stops_before_next_paragraph_on_hard_error(
    tmp_path,
) -> None:
    store = FileArtifactStore(tmp_path / "artifacts")
    progress_path = tmp_path / "writer-progress.jsonl"
    service = StreamingWriterService(
        artifact_store=store,
        precheck=ParagraphPrecheckService(ClaimPipeline()),
        identity=ParagraphIdentityService(),
        progress_log=JsonlWriterProgressEventLog(progress_path),
        clock=lambda: NOW,
    )
    consumed = []

    def paragraphs():
        consumed.append("p1")
        yield GeneratedParagraphInputSchema(
            paragraph_id="p1",
            order=1,
            role="body",
            content="本项目部署服务器 6 台。",
        )
        consumed.append("p2")
        yield GeneratedParagraphInputSchema(
            paragraph_id="p2",
            order=2,
            role="body",
            content="后续内容不应生成。",
        )

    outcome = service.generate(
        context=_writer_context(),
        section_id="compute",
        draft_version=1,
        paragraphs=paragraphs(),
        fact_snapshot=_facts(),
        expected_paragraph_ids=["p1", "p2"],
    )

    assert consumed == ["p1"]
    assert outcome.blocked_paragraph_id == "p1"
    assert outcome.writer_result.generation_complete is False
    assert [item.paragraph_id for item in outcome.writer_result.paragraphs] == ["p1"]
    assert len(outcome.paragraph_events) == 1
    assert outcome.section_completed_event is None
    persisted_after_pause = JsonlWriterProgressEventLog(
        progress_path
    ).list_events("task_1", "run_1", "compute")
    assert [event.event_id for event in persisted_after_pause] == [
        "writer_stream_1:paragraph:p1:v1"
    ]

    resumed = service.resume_after_precheck(
        context=_writer_context(),
        section_id="compute",
        draft_version=2,
        blocked_outcome=outcome,
        corrected_and_remaining=[
            GeneratedParagraphInputSchema(
                paragraph_id="p1",
                paragraph_version=2,
                order=1,
                role="body",
                content="本项目部署服务器 8 台。",
            ),
            GeneratedParagraphInputSchema(
                paragraph_id="p2",
                order=2,
                role="body",
                content="平台采用分层部署并支持弹性扩展。",
            ),
        ],
        fact_snapshot=_facts(),
        expected_paragraph_ids=["p1", "p2"],
    )

    assert resumed.blocked_paragraph_id is None
    assert resumed.writer_result.generation_complete is True
    assert resumed.section_completed_event is not None
    assert [
        paragraph.paragraph_id
        for paragraph in resumed.writer_result.paragraphs
    ] == ["p1", "p2"]
    assert resumed.writer_result.paragraphs[0].paragraph_version == 2
    persisted_after_resume = JsonlWriterProgressEventLog(
        progress_path
    ).list_events("task_1", "run_1", "compute")
    assert [event.event_id for event in persisted_after_resume] == [
        "writer_stream_1:paragraph:p1:v1",
        "writer_stream_1:paragraph:p1:v2",
        "writer_stream_1:paragraph:p2:v1",
        "writer_stream_1:section_generation_completed",
    ]


def test_streaming_writer_persists_each_paragraph_and_explicit_completion(
    tmp_path,
) -> None:
    store = FileArtifactStore(tmp_path / "artifacts")
    service = StreamingWriterService(
        artifact_store=store,
        precheck=ParagraphPrecheckService(ClaimPipeline()),
        clock=lambda: NOW,
    )
    outcome = service.generate(
        context=_writer_context(),
        section_id="compute",
        draft_version=1,
        paragraphs=[
            GeneratedParagraphInputSchema(
                paragraph_id="p1",
                order=1,
                role="body",
                content="本项目部署服务器 8 台，型号为 S-01。",
            ),
            GeneratedParagraphInputSchema(
                paragraph_id="p2",
                order=2,
                role="body",
                content="计算节点用于承载项目业务工作负载。",
            ),
        ],
        fact_snapshot=_facts(),
        expected_paragraph_ids=["p1", "p2"],
    )

    assert outcome.writer_result.generation_complete is True
    assert outcome.section_completed_event is not None
    assert outcome.section_completed_event.expected_paragraph_ids == ["p1", "p2"]
    assert len(outcome.paragraph_events) == 2
    assert store.get_bytes(
        outcome.writer_result.paragraphs[0].content_ref,
        task_id="task_1",
    ).decode("utf-8").startswith("本项目部署服务器 8 台")


def test_draft_diff_detects_undeclared_writer_change(tmp_path) -> None:
    store = FileArtifactStore(tmp_path / "artifacts")
    old_p1 = _paragraph_artifact(
        store,
        paragraph_id="p1",
        version=1,
        order=1,
        content="项目背景原文。",
    )
    old_p3 = _paragraph_artifact(
        store,
        paragraph_id="p3",
        version=1,
        order=3,
        content="服务器配置为 8 台。",
    )
    new_p1 = _paragraph_artifact(
        store,
        paragraph_id="p1",
        version=2,
        order=1,
        content="项目背景被越界修改。",
    )
    new_p3 = _paragraph_artifact(
        store,
        paragraph_id="p3",
        version=2,
        order=3,
        content="服务器配置为 8 台，型号 S-01。",
    )
    declared = WriterRevisionResultSchema(
        section_id="compute",
        base_draft_version=1,
        new_draft_version=2,
        change_manifest=[
            ParagraphChangeSchema(
                paragraph_id="p3",
                change_type=ParagraphChangeType.MODIFIED,
                old_content_hash=old_p3.content_hash,
                new_content_hash=new_p3.content_hash,
                reason_code="verifier_requested",
                verifier_issue_ids=["issue_1"],
            )
        ],
    )

    result = DraftDiffService(store).compare(
        task_id="task_1",
        before=DraftSnapshotSchema(
            section_id="compute",
            draft_version=1,
            paragraphs=[old_p1, old_p3],
        ),
        after=DraftSnapshotSchema(
            section_id="compute",
            draft_version=2,
            paragraphs=[new_p1, new_p3],
        ),
        declared=declared,
    )

    assert result.undeclared_paragraph_ids == ["p1"]
    assert result.declaration_mismatches == []
    assert {
        item.paragraph_id: item.change_type for item in result.actual_changes
    } == {
        "p1": ParagraphChangeType.MODIFIED,
        "p3": ParagraphChangeType.MODIFIED,
    }


def test_incremental_claim_pipeline_reuses_unchanged_paragraphs() -> None:
    pipeline = ClaimPipeline()
    facts = _facts()
    previous = [
        *pipeline.extract(
            section_id="compute",
            paragraph_id="p1",
            content="服务器 8 台。",
            fact_snapshot=facts,
        ),
        *pipeline.extract(
            section_id="compute",
            paragraph_id="p2",
            content="服务器型号为 S-01。",
            fact_snapshot=facts,
        ),
        *pipeline.extract(
            section_id="compute",
            paragraph_id="p3",
            content="计算节点数量为 8 台。",
            fact_snapshot=facts,
        ),
    ]
    result = IncrementalClaimPipeline(pipeline).update(
        section_id="compute",
        current_paragraphs={
            "p1": "服务器 6 台。",
            "p2": "服务器型号为 S-01。",
            "p3": "计算节点数量为 8 台。",
        },
        previous_claims=previous,
        added_paragraph_ids=set(),
        modified_paragraph_ids={"p1"},
        deleted_paragraph_ids=set(),
        fact_snapshot=facts,
    )

    p1_old = [
        claim
        for claim in result.claims
        if claim.paragraph_id == "p1"
        and claim.lifecycle_status == ClaimLifecycleStatus.INVALIDATED
    ]
    p1_new = [
        claim
        for claim in result.claims
        if claim.paragraph_id == "p1"
        and claim.lifecycle_status == ClaimLifecycleStatus.ACTIVE
    ]
    assert result.full_section_reextract is False
    assert p1_old
    assert p1_new
    assert all(
        claim.claim_id in result.reused_claim_ids
        for claim in previous
        if claim.paragraph_id in {"p2", "p3"}
    )


def test_incremental_claim_pipeline_falls_back_above_threshold() -> None:
    pipeline = ClaimPipeline()
    facts = _facts()
    previous = pipeline.extract(
        section_id="compute",
        paragraph_id="p1",
        content="服务器 8 台。",
        fact_snapshot=facts,
    )
    result = IncrementalClaimPipeline(pipeline).update(
        section_id="compute",
        current_paragraphs={
            "p1": "服务器 8 台，型号为 S-01。",
            "p2": "服务器 8 台。",
        },
        previous_claims=previous,
        added_paragraph_ids={"p2"},
        modified_paragraph_ids={"p1"},
        deleted_paragraph_ids=set(),
        fact_snapshot=facts,
    )

    assert result.full_section_reextract is True
    assert result.invalidated_claim_ids


def test_paragraph_identity_preserve_merge_and_split_rules() -> None:
    ids = iter(["new_merge", "new_split_1", "new_split_2"])
    service = ParagraphIdentityService(id_factory=lambda: next(ids))

    assert service.preserve("p1") == "p1"
    merged, superseded = service.merge(
        ["p1", "p2"],
        reserved_ids={"p1", "p2"},
    )
    split, split_superseded = service.split(
        merged,
        parts=2,
        reserved_ids={"p1", "p2", merged},
    )

    assert merged == "new_merge"
    assert superseded == ["p1", "p2"]
    assert split == ["new_split_1", "new_split_2"]
    assert split_superseded == merged
