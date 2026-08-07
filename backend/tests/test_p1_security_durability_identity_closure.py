from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest
from pydantic import ValidationError

from application.run_workspace import RunWorkspace
from apps.enterprise_document.schemas.gate_decision import (
    ClaimRiskLevel,
    DeliveryMode,
    GateDecisionSchema,
)
from apps.enterprise_document.schemas.scheme_writer_schema import (
    SchemeDraftSchema,
    SchemeSectionSchema,
)
from apps.enterprise_document.schemas.writer_governance import WriterGateBindingSchema
from apps.enterprise_document.services.document_gate import evaluate_scheme_draft
from apps.enterprise_document.services.scheme_writer.writer_governance import (
    WriterClaimAuditor,
)
from core.runtime.execution_control import (
    WorkflowExecutionControl,
    activate_execution_control,
)
from core.runtime.side_effects import JsonlSideEffectCommitStore
from data_capture.data_capture_recorder import JsonlDataCaptureRecorder
from rag.config.static_retrieval import ComponentConfig
from rag.data_capture.jsonl_writer import JsonlWriter
from rag.data_capture.rag_run_capture import RagRunCapture
from rag.mapping.request_mapper import RAGRequestMapper
from rag.schema.evidence_assessment_v2 import EvidenceStatus
from rag.offline.builder import OfflineIndexBuilder
from rag.offline.config import (
    EmbeddingBuildConfig,
    IndexStorageConfig,
    OfflineIndexBuildConfig,
    OutputConfig,
    SourceDatasetConfig,
)
from rag.offline.lifecycle import IndexLifecycleManager
from rag.offline.manifest import (
    ArtifactRecord,
    IndexManifest,
    sha256_file,
)
from schemas.citation import CitationBindingSchema, CitationSchema
from schemas.rag import RAGToolInputSchema, RetrievalAccessScopeSchema
from schemas.status import ExecutionStatus
from schemas.task import TaskSchema
from task.task_manager import JsonlTaskManager


NOW = "2026-07-24T00:00:00+00:00"


def _ledger_terminal(path: Path, side_effect_id: str) -> str:
    events = [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    matching = [
        event for event in events if event["side_effect_id"] == side_effect_id
    ]
    assert matching
    return str(matching[-1]["event_type"])


def test_rag_extra_raw_filter_cannot_broaden_authorized_scope() -> None:
    malicious = 'tenant_id == "tenantA") or (tenant_id == "tenantB"'
    request = RAGToolInputSchema(
        task_id="task",
        run_id="run",
        agent_name="agent",
        query="scope attack",
        access_scope=RetrievalAccessScopeSchema(
            tenant_id="tenantA",
            authorized_kb_ids=["kbA"],
        ),
        extra={"filter_expr": malicious},
    )

    invocation = RAGRequestMapper().map(request)

    assert invocation.payload["filter_expr"] == (
        'tenant_id == "tenantA" and kb_id in ["kbA"]'
    )
    assert "tenantB" not in invocation.payload["filter_expr"]
    assert "filter_expr" not in invocation.request_data
    assert invocation.request_data["extra"]["filter_expr"] == malicious


def test_rag_structured_narrowing_intersects_authorized_documents() -> None:
    request = RAGToolInputSchema(
        task_id="task",
        run_id="run",
        agent_name="agent",
        query="narrow",
        access_scope=RetrievalAccessScopeSchema(
            tenant_id="tenantA",
            authorized_kb_ids=["kbA"],
            allowed_doc_ids=["doc1", "doc2"],
        ),
        filters={"doc_ids": ["doc1"]},
    )

    invocation = RAGRequestMapper().map(request)

    assert 'doc_id in ["doc1"]' in invocation.payload["filter_expr"]
    assert "doc2" not in invocation.payload["filter_expr"]
    assert invocation.payload["keyword_scope"]["doc_ids"] == ["doc1"]


def test_rag_narrowing_contract_rejects_raw_filter_field() -> None:
    with pytest.raises(ValidationError, match="filter_expr"):
        RAGToolInputSchema(
            task_id="task",
            run_id="run",
            agent_name="agent",
            query="invalid filter",
            filters={"filter_expr": 'tenant_id == "tenantB"'},
        )


def test_rag_caller_fields_cannot_manufacture_authorization_scope() -> None:
    request = RAGToolInputSchema(
        task_id="task",
        run_id="run",
        agent_name="agent",
        query="manufacture scope",
        kb_ids=["kbB"],
        filters={"tenant_id": "tenantB"},
    )
    with pytest.raises(ValueError, match="trusted application boundary"):
        RAGRequestMapper().map(request)


def test_jsonl_writer_recovers_torn_tail_before_idempotent_append(
    tmp_path: Path,
) -> None:
    path = tmp_path / "records.jsonl"
    path.write_bytes(b'{"capture_id":"effect-1"')

    JsonlWriter(path).write_once(
        {"capture_id": "effect-1", "value": 1},
        id_field="capture_id",
    )

    records = [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").splitlines()
    ]
    assert records == [{"capture_id": "effect-1", "value": 1}]


def test_data_capture_side_effect_recovers_torn_tail_before_commit(
    tmp_path: Path,
) -> None:
    ledger_path = tmp_path / "side-effects.jsonl"
    control = WorkflowExecutionControl.with_timeout(
        execution_id="attempt",
        timeout_seconds=2,
        commit_scope_id="task:step",
        side_effect_store=JsonlSideEffectCommitStore(ledger_path),
    )
    recorder = JsonlDataCaptureRecorder(tmp_path / "captures")
    with activate_execution_control(control):
        record = recorder.record(
            capture_type="eval_samples",
            capture_stage="candidate",
            task_id="task",
            run_id="run",
        )
    path = (
        tmp_path
        / "captures"
        / "eval_samples"
        / "run_eval_samples.jsonl"
    )
    path.write_bytes(b'{"record_id":"torn"')

    summary = control.finalize_external_commits(attempt_succeeded=True)

    records = [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").splitlines()
    ]
    side_effect_id = record.metadata["execution_attempt"]["side_effect_id"]
    assert summary["committed"] == 1
    assert [item["record_id"] for item in records] == [record.record_id]
    assert _ledger_terminal(ledger_path, side_effect_id) == "committed"


def test_rag_capture_side_effect_recovers_torn_tail_before_commit(
    tmp_path: Path,
) -> None:
    ledger_path = tmp_path / "side-effects.jsonl"
    output = tmp_path / "rag-runs.jsonl"
    output.write_bytes(b'{"capture_id":"torn"')
    control = WorkflowExecutionControl.with_timeout(
        execution_id="attempt",
        timeout_seconds=2,
        commit_scope_id="task:rag",
        side_effect_store=JsonlSideEffectCommitStore(ledger_path),
    )
    with activate_execution_control(control):
        staged = RagRunCapture(output).capture(
            {"run_id": "rag-run", "query": "hybrid"}
        )

    summary = control.finalize_external_commits(attempt_succeeded=True)

    records = [
        json.loads(line)
        for line in output.read_text(encoding="utf-8").splitlines()
    ]
    assert summary["committed"] == 1
    assert [item["capture_id"] for item in records] == [staged["capture_id"]]
    assert _ledger_terminal(ledger_path, staged["capture_id"]) == "committed"


def test_torn_recovery_plus_ack_retry_keeps_rag_record_exactly_once(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    ledger_path = tmp_path / "side-effects.jsonl"
    ledger = JsonlSideEffectCommitStore(ledger_path)
    original_mark = ledger.mark_committed
    calls = 0

    def fail_first_ack(claim) -> bool:
        nonlocal calls
        calls += 1
        if calls == 1:
            raise OSError("injected acknowledgement failure")
        return original_mark(claim)

    monkeypatch.setattr(ledger, "mark_committed", fail_first_ack)
    output = tmp_path / "rag-runs.jsonl"
    output.write_bytes(b'{"capture_id":"torn"')
    capture = RagRunCapture(output)

    def stage(execution_id: str) -> WorkflowExecutionControl:
        control = WorkflowExecutionControl.with_timeout(
            execution_id=execution_id,
            timeout_seconds=2,
            commit_scope_id="stable:rag",
            side_effect_store=ledger,
        )
        with activate_execution_control(control):
            capture.capture({"run_id": "run", "query": "query"})
        return control

    with pytest.raises(OSError, match="acknowledgement failure"):
        stage("first").finalize_external_commits(attempt_succeeded=True)
    summary = stage("retry").finalize_external_commits(attempt_succeeded=True)

    records = [
        json.loads(line)
        for line in output.read_text(encoding="utf-8").splitlines()
    ]
    committed_ids = [
        json.loads(line)["side_effect_id"]
        for line in ledger_path.read_text(encoding="utf-8").splitlines()
        if json.loads(line)["event_type"] == "committed"
    ]
    assert summary["committed"] == 1
    assert len(records) == 1
    assert len(committed_ids) == 1
    assert _ledger_terminal(ledger_path, committed_ids[0]) == "committed"


def test_jsonl_writer_fails_closed_on_committed_middle_corruption(
    tmp_path: Path,
) -> None:
    path = tmp_path / "records.jsonl"
    path.write_text(
        '{"capture_id":"one"}\n{"broken":\n{"capture_id":"two"}\n',
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="invalid committed JSONL record"):
        JsonlWriter(path).write_once(
            {"capture_id": "three"},
            id_field="capture_id",
        )


def test_jsonl_writer_fails_closed_on_duplicate_committed_identity(
    tmp_path: Path,
) -> None:
    path = tmp_path / "records.jsonl"
    path.write_text(
        '{"capture_id":"same","value":1}\n'
        '{"capture_id":"same","value":2}\n',
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="duplicate logical record"):
        JsonlWriter(path).write_once(
            {"capture_id": "same", "value": 3},
            id_field="capture_id",
        )


def _task(**updates) -> TaskSchema:
    payload = {
        "task_id": "task-identity",
        "run_id": "run-identity",
        "task_type": "scheme_generation",
        "user_input": "request A",
        "project_input": {"project": {"name": "A", "region": "north"}},
        "generation_requirements": {
            "citation": {"required": True, "minimum": 1}
        },
        "created_at": NOW,
    }
    payload.update(updates)
    return TaskSchema(**payload)


def test_duplicate_identical_task_request_is_idempotent(tmp_path: Path) -> None:
    manager = JsonlTaskManager(tmp_path)
    first = manager.create_task(_task())
    repeated = manager.create_task(_task(created_at="2027-01-01T00:00:00+00:00"))
    assert repeated.revision == first.revision
    assert repeated.canonical_task_request_sha256


@pytest.mark.parametrize(
    "update",
    [
        {"user_input": "request B"},
        {"project_input": {"project": {"name": "B", "region": "north"}}},
        {
            "generation_requirements": {
                "citation": {"required": True, "minimum": 2}
            }
        },
    ],
)
def test_duplicate_task_semantic_mismatch_is_rejected(
    tmp_path: Path,
    update: dict,
) -> None:
    manager = JsonlTaskManager(tmp_path)
    manager.create_task(_task())
    with pytest.raises(ValueError, match="immutable request mismatch"):
        manager.create_task(_task(**update))


def test_duplicate_task_mismatch_is_rejected_after_restart(
    tmp_path: Path,
) -> None:
    JsonlTaskManager(tmp_path).create_task(_task())
    restarted = JsonlTaskManager(tmp_path)
    with pytest.raises(ValueError, match="immutable request mismatch"):
        restarted.create_task(_task(user_input="request B"))


def test_task_request_hash_is_insensitive_to_dict_key_order(
    tmp_path: Path,
) -> None:
    manager = JsonlTaskManager(tmp_path)
    first = _task(
        project_input={"project": {"name": "A", "region": "north"}}
    )
    reordered = _task(
        project_input={"project": {"region": "north", "name": "A"}}
    )
    assert first.canonical_request_sha256() == reordered.canonical_request_sha256()
    assert manager.create_task(first).revision == manager.create_task(reordered).revision


def _citation(citation_id: str = "C1", chunk_id: str = "chunk1") -> CitationSchema:
    return CitationSchema(
        citation_id=citation_id,
        source_type="rag",
        doc_id="doc1",
        chunk_id=chunk_id,
        quote_text="canonical evidence",
    )


def _binding(
    citation_id: str = "C1",
    chunk_id: str = "chunk1",
) -> CitationBindingSchema:
    return CitationBindingSchema(
        binding_id="binding1",
        citation_id=citation_id,
        target_document_id="document1",
        target_section_id="section1",
        target_paragraph_id="paragraph1",
        target_claim_id="claim1",
        source_document_id="doc1",
        source_chunk_id=chunk_id,
        claim_text="verified claim",
        metadata={"grounding_verified": True},
    )


def _citation_draft(binding: CitationBindingSchema) -> SchemeDraftSchema:
    content = f"verified claim [{binding.citation_id}]"
    writer_binding = WriterGateBindingSchema(
        section_id="section1",
        section_title="Section",
        section_order=1,
        document_id="document1",
        run_id="run1",
        gate_decision=GateDecisionSchema(
            policy_id="p1_citation_lineage_fixture_v1",
            evidence_status=EvidenceStatus.SUFFICIENT,
            delivery_mode=DeliveryMode.GROUNDED,
            claim_risk_level=ClaimRiskLevel.LOW,
            allowed_claim_types=["general_method", "style_guidance"],
            forbidden_claim_types=[],
            required_qualifiers=[],
            covered_topics=["Section"],
            missing_topics=[],
            human_review_required=False,
            reason_codes=["p1_citation_lineage_fixture"],
        ),
        generation_allowed=True,
        evidence_contract_sha256="a" * 64,
        prompt_evidence_contract_sha256="b" * 64,
    )
    audit = WriterClaimAuditor().audit(content, binding=writer_binding)
    section = SchemeSectionSchema(
        section_id="section1",
        section_title="Section",
        section_order=1,
        content=content,
        status=ExecutionStatus.SUCCESS,
        citation_bindings=[binding],
        extra={
            "writer_gate_binding": writer_binding.model_dump(
                mode="json", exclude_none=True
            ),
            "writer_execution_status": writer_binding.execution_status.value,
            "claim_audit": audit.model_dump(mode="json", exclude_none=True),
            "required_qualifiers_applied": [],
            "formal_deliverable_eligible": audit.passed,
            "need_human_review": False,
        },
    )
    return SchemeDraftSchema(
        draft_id="draft1",
        document_id="document1",
        task_id="task1",
        run_id="run1",
        title="Document",
        full_text=section.content,
        sections=[section],
        required_sections=["Section"],
        citation_bindings=[binding],
        created_at=NOW,
    )


def _evaluate_citation(
    binding: CitationBindingSchema,
    citations: list[CitationSchema],
):
    return evaluate_scheme_draft(
        _citation_draft(binding),
        citation_required=True,
        citation_required_sections=["Section"],
        retrieved_chunk_ids=[binding.source_chunk_id or ""],
        canonical_citations=citations,
    )


def test_hard_gate_rejects_binding_to_unknown_canonical_citation() -> None:
    result = _evaluate_citation(_binding(citation_id="C999"), [])
    assert result.passed is False
    assert result.metadata["unknown_binding_citation_ids"] == ["C999"]


def test_hard_gate_rejects_binding_citation_source_lineage_mismatch() -> None:
    result = _evaluate_citation(
        _binding(chunk_id="other-chunk"),
        [_citation(chunk_id="canonical-chunk")],
    )
    assert result.passed is False
    assert result.metadata["binding_lineage_mismatches"] == ["binding1"]


def test_hard_gate_accepts_complete_marker_binding_citation_lineage() -> None:
    result = _evaluate_citation(_binding(), [_citation()])
    assert result.passed is True
    assert result.checks["citation_referential_integrity"] is True


def test_hard_gate_rejects_duplicate_canonical_citation_ids() -> None:
    result = _evaluate_citation(_binding(), [_citation(), _citation()])
    assert result.passed is False
    assert result.metadata["duplicate_citation_ids"] == ["C1"]


_INDEX_RECORDS = [
    {
        "unit_id": "d1_u1",
        "doc_id": "d1",
        "source_type": "offline",
        "source_uri": "test://d1",
        "source_name": "D1",
        "source_format": "md",
        "title": "D1",
        "section": "Overview",
        "section_level": 1,
        "page_start": 1,
        "page_end": 1,
        "unit_type": "paragraph",
        "unit_order": 1,
        "text": "Enterprise knowledge base retrieval evidence.",
        "language": "en",
        "quality_score": 0.9,
        "quality_flags": [],
        "cleaning_version": "clean_v1",
        "extra": {},
    }
]


def _index_config(
    tmp_path: Path,
    pointer_path: Path,
    *,
    backend: str = "artifacts_only",
) -> OfflineIndexBuildConfig:
    source = tmp_path / "units.jsonl"
    source.write_text(
        "".join(json.dumps(item) + "\n" for item in _INDEX_RECORDS),
        encoding="utf-8",
    )
    return OfflineIndexBuildConfig(
        build_id="p1-index-build",
        dataset_version="dataset-v1",
        source=SourceDatasetConfig(path=str(source)),
        chunker=ComponentConfig(
            name="fixed_parent_child",
            version="v1",
            params={
                "parent_chunk_size": 80,
                "parent_chunk_overlap": 10,
                "child_chunk_size": 40,
                "child_chunk_overlap": 5,
                "unit": "char",
            },
        ),
        embedding=EmbeddingBuildConfig(
            mode="hash",
            name="hash_embedding",
            version="v1",
            dim=32,
        ),
        index=IndexStorageConfig(backend=backend),
        outputs=OutputConfig(
            root_dir=str(tmp_path / "indexes"),
            active_pointer=str(pointer_path),
        ),
        deterministic_created_at=NOW,
    )


def _make_lifecycle_eligible_manifest(result) -> None:
    manifest_path = Path(result.manifest_path)
    manifest = IndexManifest.model_validate_json(
        manifest_path.read_text(encoding="utf-8")
    )
    fake_db = manifest_path.parent / "milvus.db"
    fake_db.write_bytes(b"test-only-milvus-artifact")
    artifacts = dict(manifest.artifacts)
    artifacts["milvus_lite"] = ArtifactRecord(
        path=str(fake_db),
        sha256=sha256_file(fake_db),
        record_count=result.child_chunk_count,
    )
    index = {**manifest.index, "backend": "milvus_lite"}
    manifest.model_copy(
        update={"artifacts": artifacts, "index": index}
    ).write(manifest_path)


class _SuccessfulVerifier:
    def verify(self, *args, **kwargs):
        del args, kwargs
        return SimpleNamespace(status="success", metrics={})


def test_offline_builder_cannot_mutate_existing_active_pointer(
    tmp_path: Path,
) -> None:
    pointer_path = tmp_path / "active_index.json"
    original = b'{"owner":"IndexLifecycleManager","version":"old"}'
    pointer_path.write_bytes(original)

    OfflineIndexBuilder(project_root=tmp_path).build(
        _index_config(tmp_path, pointer_path)
    )

    assert pointer_path.read_bytes() == original


def test_builder_activation_flag_fails_closed_even_for_milvus() -> None:
    with pytest.raises(ValidationError, match="IndexLifecycleManager"):
        OutputConfig(update_active_pointer=True)


def test_index_lifecycle_manager_remains_only_legal_activation_owner(
    tmp_path: Path,
) -> None:
    pointer_path = tmp_path / "active_index.json"
    result = OfflineIndexBuilder(project_root=tmp_path).build(
        _index_config(tmp_path, pointer_path)
    )
    _make_lifecycle_eligible_manifest(result)
    manager = IndexLifecycleManager(
        project_root=tmp_path,
        indexes_root=tmp_path / "indexes",
        pointer_path=pointer_path,
        registry_path=tmp_path / "registry.json",
        history_path=tmp_path / "history.jsonl",
        lock_path=tmp_path / "lifecycle.lock",
        verifier_factory=_SuccessfulVerifier,
    )
    manager.register(result.manifest_path)

    activation = manager.activate(
        result.index_version,
        verify_milvus=False,
        self_retrieval_samples=0,
    )

    pointer = json.loads(pointer_path.read_text(encoding="utf-8"))
    assert activation.active_index_version == result.index_version
    assert pointer["index_version"] == result.index_version


def test_failed_lifecycle_verification_preserves_previous_pointer(
    tmp_path: Path,
) -> None:
    pointer_path = tmp_path / "active_index.json"
    result = OfflineIndexBuilder(project_root=tmp_path).build(
        _index_config(tmp_path, pointer_path)
    )
    _make_lifecycle_eligible_manifest(result)

    class FailingVerifier:
        def verify(self, *args, **kwargs):
            del args, kwargs
            raise RuntimeError("injected verification failure")

    manager = IndexLifecycleManager(
        project_root=tmp_path,
        indexes_root=tmp_path / "indexes",
        pointer_path=pointer_path,
        registry_path=tmp_path / "registry.json",
        history_path=tmp_path / "history.jsonl",
        lock_path=tmp_path / "lifecycle.lock",
        verifier_factory=_SuccessfulVerifier,
    )
    manager.register(result.manifest_path)
    manager.activate(
        result.index_version,
        verify_milvus=False,
        self_retrieval_samples=0,
    )
    before = pointer_path.read_bytes()

    failing_manager = IndexLifecycleManager(
        project_root=tmp_path,
        indexes_root=tmp_path / "indexes",
        pointer_path=pointer_path,
        registry_path=tmp_path / "registry.json",
        history_path=tmp_path / "history.jsonl",
        lock_path=tmp_path / "lifecycle.lock",
        verifier_factory=FailingVerifier,
    )
    with pytest.raises(RuntimeError, match="verification failure"):
        failing_manager.activate(
            result.index_version,
            verify_milvus=False,
            self_retrieval_samples=0,
        )
    assert pointer_path.read_bytes() == before


def test_workspace_clean_cannot_bypass_task_immutable_identity(
    tmp_path: Path,
) -> None:
    workspace = RunWorkspace(
        output_root=tmp_path,
        task_id="task-identity",
        run_id="run-identity",
    )
    manager = JsonlTaskManager(workspace.tasks_dir)
    manager.create_task(_task())

    workspace.clean()

    with pytest.raises(ValueError, match="immutable request mismatch"):
        JsonlTaskManager(workspace.tasks_dir).create_task(
            _task(user_input="request B", run_id="run-retry")
        )


def test_workspace_clean_preserves_same_task_identity(
    tmp_path: Path,
) -> None:
    workspace = RunWorkspace(
        output_root=tmp_path,
        task_id="task-identity",
        run_id="run-identity",
    )
    manager = JsonlTaskManager(workspace.tasks_dir)
    created = manager.create_task(_task())

    workspace.clean()

    repeated = JsonlTaskManager(workspace.tasks_dir).create_task(
        _task(created_at="2027-01-01T00:00:00+00:00")
    )
    assert repeated.canonical_task_request_sha256 == (
        created.canonical_task_request_sha256
    )
    assert repeated.revision == created.revision


def test_workspace_clean_removes_run_artifacts_but_preserves_task_truth(
    tmp_path: Path,
) -> None:
    workspace = RunWorkspace(
        output_root=tmp_path,
        task_id="task-identity",
        run_id="run-identity",
    )
    JsonlTaskManager(workspace.tasks_dir).create_task(_task())
    run_artifacts = [
        workspace.trace_path,
        workspace.model_usage_path,
        workspace.raw_capture_path,
        workspace.sft_capture_path,
        workspace.eval_capture_path,
    ]
    for path in run_artifacts:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("stale", encoding="utf-8")

    workspace.clean()

    assert all(not path.exists() for path in run_artifacts)
    assert workspace.task_state_path.exists()
