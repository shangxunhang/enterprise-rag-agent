from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import sys
from types import SimpleNamespace

import pytest

from application.mainline_service import MainlineApplicationService
from apps.enterprise_document.schemas.project_input_schema import ProjectInputSchema
from apps.enterprise_document.services.scheme_writer.evidence_service import (
    SchemeEvidenceService,
)
from core.config import get_settings
from core.persistence.crash_safe_jsonl import canonical_json_sha256
from core.runtime.execution_control import (
    WorkflowExecutionControl,
    activate_execution_control,
)
from core.runtime.side_effects import (
    JsonlSideEffectCommitStore,
    SideEffectIdentityConflict,
)
from data_capture.data_capture_recorder import JsonlDataCaptureRecorder
from rag.data_capture.jsonl_writer import JsonlWriter
from rag.data_capture.rag_run_capture import (
    RagRunCapture,
    rag_run_semantic_projection,
)
from rag.mapping.request_mapper import RAGRequestMapper
from schemas.rag import RAGToolInputSchema, RetrievalAccessScopeSchema


class _FixedClock:
    def __init__(self, value: str) -> None:
        self.value = value

    def now_iso(self) -> str:
        return self.value


class _MappingRagService:
    def __init__(self) -> None:
        self.request = None
        self.invocation = None
        self.mapper = RAGRequestMapper()

    def retrieve(self, request):
        self.request = request
        self.invocation = self.mapper.map(request)
        return None


def _project_input(
    *,
    tenant_id: str = "tenantA",
    kb_ids: list[str] | None = None,
    doc_ids: list[str] | None = None,
    file_ids: list[str] | None = None,
) -> ProjectInputSchema:
    return ProjectInputSchema.model_validate(
        {
            "task_id": "task-auth",
            "tenant_id": tenant_id,
            "task_type": "scheme_generation",
            "user_query": "生成授权范围内的方案",
            "generation_requirements": {
                "required_sections": ["技术方案"],
            },
            "output_schema": {
                "required_sections": ["技术方案"],
            },
            "source_materials": [
                {
                    "material_type": "knowledge_base",
                    "doc_ids": list(doc_ids or []),
                    "file_ids": list(file_ids or []),
                    "metadata": {"kb_ids": list(kb_ids or [])},
                }
            ],
        }
    )


def _state(*, kb_ids: list[str] | None = None):
    return SimpleNamespace(
        task_id="task-auth",
        run_id="run-auth",
        task={"kb_ids": list(kb_ids or [])},
    )


def _service(
    scope: RetrievalAccessScopeSchema | None,
) -> tuple[SchemeEvidenceService, _MappingRagService]:
    rag = _MappingRagService()
    return (
        SchemeEvidenceService(
            rag_service=rag,
            agent_name="SchemeWriterAgent",
            retrieval_access_scope=scope,
        ),
        rag,
    )


def _read_jsonl(path: Path) -> list[dict]:
    return [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def test_production_mainline_rejects_business_scope_without_trusted_context(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("USE_REAL_RAG_TOOL", "true")
    project_input = _project_input(
        tenant_id="tenantB",
        kb_ids=["kbB"],
        doc_ids=["docB"],
        file_ids=["fileB"],
    ).model_dump()

    with pytest.raises(ValueError, match="trusted retrieval_access_scope"):
        MainlineApplicationService().run(
            project_root=Path(__file__).resolve().parents[2],
            user_input=project_input["user_query"],
            task_id="task-auth",
            run_id="run-auth",
            output_root=tmp_path,
            settings=get_settings(),
            project_input=project_input,
            allow_demo_defaults=False,
        )


def test_scheme_evidence_service_cannot_promote_project_input_into_authority() -> None:
    service, rag = _service(None)

    with pytest.raises(ValueError, match="trusted application boundary"):
        service.retrieve(
            _state(kb_ids=["kbB"]),
            _project_input(tenant_id="tenantB", kb_ids=["kbB"]),
        )

    assert rag.request is not None
    assert rag.request.access_scope is None


def test_trusted_tenant_conflict_fails_before_retrieval() -> None:
    service, rag = _service(
        RetrievalAccessScopeSchema(
            tenant_id="tenantA",
            authorized_kb_ids=["kbA"],
        )
    )

    with pytest.raises(ValueError, match="authenticated retrieval tenant"):
        service.retrieve(
            _state(kb_ids=["kbB"]),
            _project_input(tenant_id="tenantB", kb_ids=["kbB"]),
        )

    assert rag.request is None


def test_mapper_rejects_caller_tenant_narrowing_conflict() -> None:
    with pytest.raises(ValueError, match="requested tenant_id conflicts"):
        RAGRequestMapper().map(
            RAGToolInputSchema(
                task_id="task",
                run_id="run",
                agent_name="agent",
                query="scope",
                access_scope=RetrievalAccessScopeSchema(
                    tenant_id="tenantA",
                    authorized_kb_ids=["kbA"],
                ),
                filters={"tenant_id": "tenantB"},
            )
        )


def test_authorized_kb_request_is_only_narrowed_by_project_input() -> None:
    service, rag = _service(
        RetrievalAccessScopeSchema(
            tenant_id="tenantA",
            authorized_kb_ids=["kbA", "kbB"],
        )
    )

    service.retrieve(
        _state(kb_ids=["kbB"]),
        _project_input(kb_ids=["kbB"]),
    )

    assert rag.request.access_scope.authorized_kb_ids == ["kbA", "kbB"]
    assert rag.request.kb_ids == ["kbB"]
    assert rag.invocation.payload["keyword_scope"] == {
        "tenant_id": "tenantA",
        "kb_ids": ["kbB"],
        "file_ids": [],
        "doc_ids": [],
    }


def test_unauthorized_kb_request_fails_closed() -> None:
    service, _ = _service(
        RetrievalAccessScopeSchema(
            tenant_id="tenantA",
            authorized_kb_ids=["kbA"],
        )
    )

    with pytest.raises(ValueError, match="outside the authorized retrieval scope"):
        service.retrieve(
            _state(kb_ids=["kbB"]),
            _project_input(kb_ids=["kbB"]),
        )


@pytest.mark.parametrize(
    ("field", "authorized", "requested", "expected"),
    [
        ("doc", ["docA", "docB"], ["docB"], ["docB"]),
        ("file", ["fileA", "fileB"], ["fileB"], ["fileB"]),
    ],
)
def test_doc_and_file_business_filters_only_narrow_trusted_scope(
    field: str,
    authorized: list[str],
    requested: list[str],
    expected: list[str],
) -> None:
    scope = RetrievalAccessScopeSchema(
        tenant_id="tenantA",
        authorized_kb_ids=["kbA"],
        allowed_doc_ids=authorized if field == "doc" else [],
        allowed_file_ids=authorized if field == "file" else [],
    )
    service, rag = _service(scope)

    service.retrieve(
        _state(kb_ids=["kbA"]),
        _project_input(
            kb_ids=["kbA"],
            doc_ids=requested if field == "doc" else [],
            file_ids=requested if field == "file" else [],
        ),
    )

    assert rag.invocation.payload["keyword_scope"][f"{field}_ids"] == expected


@pytest.mark.parametrize("field", ["doc", "file"])
def test_unauthorized_doc_and_file_requests_fail_closed(field: str) -> None:
    scope = RetrievalAccessScopeSchema(
        tenant_id="tenantA",
        authorized_kb_ids=["kbA"],
        allowed_doc_ids=["docA"] if field == "doc" else [],
        allowed_file_ids=["fileA"] if field == "file" else [],
    )
    service, _ = _service(scope)

    with pytest.raises(ValueError, match="outside the authorized retrieval scope"):
        service.retrieve(
            _state(kb_ids=["kbA"]),
            _project_input(
                kb_ids=["kbA"],
                doc_ids=["docB"] if field == "doc" else [],
                file_ids=["fileB"] if field == "file" else [],
            ),
        )


def test_real_milvus_lite_applies_trusted_scope_to_returned_rows(
    tmp_path: Path,
) -> None:
    helper = Path(__file__).parent / "helpers" / "milvus_scope_probe.py"
    env = dict(os.environ)
    backend_root = str(Path(__file__).resolve().parents[1])
    env["PYTHONPATH"] = os.pathsep.join(
        item
        for item in (backend_root, env.get("PYTHONPATH", ""))
        if item
    )
    completed = subprocess.run(
        [sys.executable, str(helper), str(tmp_path)],
        check=False,
        capture_output=True,
        text=True,
        env=env,
        timeout=60,
    )

    assert completed.returncode == 0, completed.stderr
    result_line = next(
        line
        for line in completed.stdout.splitlines()
        if line.startswith("SCOPE_RESULT=")
    )
    assert json.loads(result_line.removeprefix("SCOPE_RESULT=")) == {
        "rows": [["tenantA", "kbA"]],
        "tenantB_rows": 0,
    }


def _control(
    *,
    ledger: JsonlSideEffectCommitStore,
    execution_id: str,
    commit_scope_id: str,
) -> WorkflowExecutionControl:
    return WorkflowExecutionControl.with_timeout(
        execution_id=execution_id,
        timeout_seconds=3,
        commit_scope_id=commit_scope_id,
        side_effect_store=ledger,
    )


def _data_capture_path(root: Path) -> Path:
    return root / "eval_samples" / "run_eval_samples.jsonl"


def _rag_fingerprint(record: dict) -> str:
    return canonical_json_sha256(rag_run_semantic_projection(record))


def _stage_rag_capture(
    *,
    ledger: JsonlSideEffectCommitStore,
    output: Path,
    execution_id: str,
    record: dict,
) -> WorkflowExecutionControl:
    control = _control(
        ledger=ledger,
        execution_id=execution_id,
        commit_scope_id="task:rag-semantic-projection",
    )
    with activate_execution_control(control):
        RagRunCapture(output).capture(record)
    return control


def test_same_data_capture_semantics_ignore_only_retry_metadata(
    tmp_path: Path,
) -> None:
    ledger_path = tmp_path / "ledger.jsonl"
    ledger = JsonlSideEffectCommitStore(ledger_path)
    capture_root = tmp_path / "captures"

    first = _control(
        ledger=ledger,
        execution_id="attempt-1",
        commit_scope_id="task:step",
    )
    with activate_execution_control(first):
        JsonlDataCaptureRecorder(
            capture_root,
            clock=_FixedClock("2026-07-24T00:00:00+00:00"),
        ).record(
            capture_type="eval_samples",
            task_id="task",
            run_id="run",
            model_output="stable business output",
            lineage_refs={"source": "docA"},
        )
    assert first.finalize_external_commits(attempt_succeeded=True)["committed"] == 1

    retry = _control(
        ledger=JsonlSideEffectCommitStore(ledger_path),
        execution_id="attempt-2",
        commit_scope_id="task:step",
    )
    with activate_execution_control(retry):
        JsonlDataCaptureRecorder(
            capture_root,
            clock=_FixedClock("2026-07-25T00:00:00+00:00"),
        ).record(
            capture_type="eval_samples",
            task_id="task",
            run_id="run",
            model_output="stable business output",
            lineage_refs={"source": "docA"},
        )
    summary = retry.finalize_external_commits(attempt_succeeded=True)

    assert summary["already_committed"] == 1
    assert len(_read_jsonl(_data_capture_path(capture_root))) == 1
    semantic_fingerprints = {
        event["semantic_fingerprint"]
        for event in _read_jsonl(ledger_path)
        if event["event_type"] in {"claimed", "committed"}
    }
    assert len(semantic_fingerprints) == 1
    assert next(iter(semantic_fingerprints))


def test_same_data_capture_id_with_different_business_payload_fails_closed(
    tmp_path: Path,
) -> None:
    ledger_path = tmp_path / "ledger.jsonl"
    ledger = JsonlSideEffectCommitStore(ledger_path)
    capture_root = tmp_path / "captures"
    first = _control(
        ledger=ledger,
        execution_id="attempt-1",
        commit_scope_id="task:step",
    )
    with activate_execution_control(first):
        JsonlDataCaptureRecorder(capture_root).record(
            capture_type="eval_samples",
            task_id="task",
            run_id="run",
            model_output="value=1",
            lineage_refs={"source": "docA"},
        )
    first.finalize_external_commits(attempt_succeeded=True)

    collision = _control(
        ledger=JsonlSideEffectCommitStore(ledger_path),
        execution_id="attempt-2",
        commit_scope_id="task:step",
    )
    with activate_execution_control(collision):
        JsonlDataCaptureRecorder(capture_root).record(
            capture_type="eval_samples",
            task_id="task",
            run_id="run",
            model_output="value=999",
            lineage_refs={"source": "docB"},
        )

    with pytest.raises(SideEffectIdentityConflict):
        collision.finalize_external_commits(attempt_succeeded=True)

    target = _read_jsonl(_data_capture_path(capture_root))
    assert len(target) == 1
    assert target[0]["model_output"] == "value=1"
    committed = [
        event
        for event in _read_jsonl(ledger_path)
        if event["event_type"] == "committed"
    ]
    assert len(committed) == 1


def _project_fact_input(
    task_id: str,
    *,
    metadata: dict | None = None,
    **project_fields,
) -> ProjectInputSchema:
    payload = {
        "task_id": task_id,
        "task_type": "scheme_generation",
        "user_query": "生成资源配置说明",
        "generation_requirements": {
            "required_sections": ["资源配置"],
            "citation_required_sections": [],
            "min_section_chars": 8,
            "max_section_retries": 0,
        },
        "output_schema": {"required_sections": ["资源配置"]},
        "metadata": dict(metadata or {}),
    }
    payload.update(project_fields)
    return ProjectInputSchema.model_validate(payload)


def _generate_project_fact_section(
    *,
    project_input: ProjectInputSchema,
    content: str,
    citations: list,
):
    from apps.enterprise_document.agents.scheme_writer_agent import (
        SchemeWriterAgent,
    )
    from schemas.model import ModelResponseSchema
    from schemas.rag import RAGContextSchema

    class _ProjectFactAgent(SchemeWriterAgent):
        def call_model(self, *args, **kwargs):  # type: ignore[override]
            del args, kwargs
            return ModelResponseSchema(
                model_call_id="project_fact_generation",
                task_id=project_input.task_id,
                run_id="run-project-fact",
                model_name="stub",
                success=True,
                content=content,
                finish_reason="stop",
                created_at="2026-07-25T00:00:00+00:00",
            )

    return _ProjectFactAgent(
        enable_semantic_gate=False
    ).section_generation_service.generate_section(
        SimpleNamespace(
            run_id="run-project-fact",
            task_id=project_input.task_id,
            generated_outputs={},
        ),
        document_id="document-project-fact",
        project_input=project_input,
        section_title="资源配置",
        section_order=1,
        rag_context=RAGContextSchema(context_text="", max_context_chars=6000),
        citations=citations,
        structured_facts=[],
        previous_sections=[],
    )


def _evaluate_project_fact_section(section, *, citations: list):
    from apps.enterprise_document.schemas.scheme_writer_schema import (
        SchemeDraftSchema,
    )
    from apps.enterprise_document.services.document_gate import (
        evaluate_scheme_draft,
    )

    draft = SchemeDraftSchema(
        draft_id="draft-project-fact",
        document_id="document-project-fact",
        task_id="task-project-fact",
        run_id="run-project-fact",
        title="资源配置报告",
        full_text=section.content,
        sections=[section],
        required_sections=["资源配置"],
        citation_bindings=list(section.citation_bindings),
        created_at="2026-07-25T00:00:00+00:00",
    )
    return evaluate_scheme_draft(
        draft,
        citation_required=False,
        retrieved_chunk_ids=[
            item.chunk_id
            for item in citations
            if str(item.chunk_id or "").strip()
        ],
        canonical_citations=citations,
    )


def test_optional_citation_does_not_allow_unsupported_high_risk_fact() -> None:
    section = _generate_project_fact_section(
        project_input=_project_fact_input("task-project-fact"),
        content="本项目部署4台服务器，并形成资源配置方案。",
        citations=[],
    )

    gate = _evaluate_project_fact_section(section, citations=[])
    violation = section.extra["mandatory_project_fact_violations"][0]

    assert violation["fact_type"] == "hardware_count"
    assert violation["subject"] == "server"
    assert violation["value"] == 4
    assert violation["unit"] == "count"
    assert violation["commitment"] == "affirmative"
    assert gate.checks["high_risk_claims_grounded"] is False
    assert gate.passed is False


def test_project_input_supported_high_risk_fact_remains_deliverable() -> None:
    section = _generate_project_fact_section(
        project_input=_project_fact_input(
            "task-project-fact",
            metadata={"server_count": 4},
        ),
        content="本项目部署4台服务器，并形成资源配置方案。",
        citations=[],
    )

    gate = _evaluate_project_fact_section(section, citations=[])

    assert section.extra["mandatory_project_fact_violations"] == []
    assert gate.checks["high_risk_claims_grounded"] is True
    assert gate.passed is True


def test_functional_department_count_cannot_support_server_count() -> None:
    section = _generate_project_fact_section(
        project_input=_project_fact_input(
            "task-project-fact",
            functional_department_count=4,
        ),
        content="本项目部署4台服务器，并形成资源配置方案。",
        citations=[],
    )

    gate = _evaluate_project_fact_section(section, citations=[])

    assert section.extra["mandatory_project_fact_violations"]
    assert gate.checks["high_risk_claims_grounded"] is False
    assert gate.passed is False


def test_unknown_metadata_count_cannot_support_server_count() -> None:
    section = _generate_project_fact_section(
        project_input=_project_fact_input(
            "task-project-fact",
            metadata={"unrelated_count": 4},
        ),
        content="本项目部署4台服务器，并形成资源配置方案。",
        citations=[],
    )

    gate = _evaluate_project_fact_section(section, citations=[])

    assert section.extra["mandatory_project_fact_violations"]
    assert gate.checks["high_risk_claims_grounded"] is False
    assert gate.passed is False


def test_gpu_count_cannot_support_server_count() -> None:
    section = _generate_project_fact_section(
        project_input=_project_fact_input(
            "task-project-fact",
            metadata={"gpu_count": 4},
        ),
        content="本项目部署4台服务器，并形成资源配置方案。",
        citations=[],
    )

    gate = _evaluate_project_fact_section(section, citations=[])

    assert section.extra["mandatory_project_fact_violations"]
    assert gate.checks["high_risk_claims_grounded"] is False
    assert gate.passed is False


def test_negated_project_input_cannot_support_affirmative_server_claim() -> None:
    section = _generate_project_fact_section(
        project_input=_project_fact_input(
            "task-project-fact",
            user_query="不要部署4台服务器，只生成通用资源配置说明。",
        ),
        content="本项目部署4台服务器，并形成资源配置方案。",
        citations=[],
    )

    gate = _evaluate_project_fact_section(section, citations=[])

    assert section.extra["mandatory_project_fact_violations"]
    assert gate.checks["high_risk_claims_grounded"] is False
    assert gate.passed is False


def test_interrogative_project_input_cannot_support_affirmative_server_claim() -> None:
    section = _generate_project_fact_section(
        project_input=_project_fact_input(
            "task-project-fact",
            user_query="是否需要部署4台服务器？",
        ),
        content="本项目部署4台服务器。",
        citations=[],
    )

    gate = _evaluate_project_fact_section(section, citations=[])

    assert section.extra["mandatory_project_fact_violations"]
    assert gate.checks["high_risk_claims_grounded"] is False
    assert gate.passed is False


def test_negated_generated_server_value_still_requires_support() -> None:
    section = _generate_project_fact_section(
        project_input=_project_fact_input("task-project-fact"),
        content="本项目不部署4台服务器。",
        citations=[],
    )

    gate = _evaluate_project_fact_section(section, citations=[])

    violations = section.extra["mandatory_project_fact_violations"]
    assert violations
    assert violations[0]["commitment"] == "negated"
    assert gate.checks["high_risk_claims_grounded"] is False
    assert gate.passed is False


def test_negation_cannot_cross_scope_boundary_to_exempt_server_fact() -> None:
    section = _generate_project_fact_section(
        project_input=_project_fact_input("task-project-fact"),
        content="不调整现有网络，但新增4台服务器。",
        citations=[],
    )

    gate = _evaluate_project_fact_section(section, citations=[])
    violation = section.extra["mandatory_project_fact_violations"][0]

    assert violation["subject"] == "server"
    assert violation["value"] == 4
    assert violation["commitment"] == "affirmative"
    assert gate.checks["high_risk_claims_grounded"] is False
    assert gate.passed is False


def test_grounding_verified_evidence_supports_high_risk_fact() -> None:
    from schemas.citation import CitationSchema

    citation = CitationSchema(
        citation_id="C-PROJECT-FACT",
        source_type="rag",
        source_document_id="source-project-fact",
        chunk_id="chunk-project-fact",
        quote_text="本项目部署4台服务器，并形成资源配置方案。",
    )
    section = _generate_project_fact_section(
        project_input=_project_fact_input("task-project-fact"),
        content="本项目部署4台服务器，并形成资源配置方案。",
        citations=[citation],
    )

    gate = _evaluate_project_fact_section(section, citations=[citation])

    assert section.citation_bindings
    assert all(
        binding.metadata.get("grounding_verified")
        for binding in section.citation_bindings
    )
    assert section.extra["mandatory_project_fact_violations"] == []
    assert gate.checks["high_risk_claims_grounded"] is True
    assert gate.passed is True


def test_unbound_citation_value_does_not_support_high_risk_fact() -> None:
    from schemas.citation import CitationSchema

    citation = CitationSchema(
        citation_id="C-UNBOUND",
        source_type="rag",
        source_document_id="source-unbound",
        chunk_id="chunk-unbound",
        quote_text="容量上限为4。",
    )
    section = _generate_project_fact_section(
        project_input=_project_fact_input("task-project-fact"),
        content="本项目部署4台服务器，并形成资源配置方案。",
        citations=[citation],
    )

    gate = _evaluate_project_fact_section(section, citations=[citation])

    assert section.citation_bindings == []
    assert section.extra["mandatory_project_fact_violations"]
    assert gate.checks["high_risk_claims_grounded"] is False
    assert gate.passed is False


def test_qualified_high_risk_fact_is_not_blocked() -> None:
    section = _generate_project_fact_section(
        project_input=_project_fact_input("task-project-fact"),
        content="服务器数量待项目方确认，确认后再形成资源配置方案。",
        citations=[],
    )

    gate = _evaluate_project_fact_section(section, citations=[])

    assert section.extra["mandatory_project_fact_violations"] == []
    assert gate.checks["high_risk_claims_grounded"] is True
    assert gate.passed is True


def test_unpunctuated_qualification_cannot_exempt_affirmative_server_count() -> None:
    section = _generate_project_fact_section(
        project_input=_project_fact_input("task-project-fact"),
        content="服务器品牌待项目方确认但本项目确定部署1000台服务器。",
        citations=[],
    )

    gate = _evaluate_project_fact_section(section, citations=[])
    violations = section.extra["mandatory_project_fact_violations"]

    assert violations
    assert any(
        item["subject"] == "server"
        and item["value"] == 1000
        and item["commitment"] == "affirmative"
        for item in violations
    )
    assert gate.checks["high_risk_claims_grounded"] is False
    assert gate.passed is False


def test_server_count_qualification_without_value_remains_allowed() -> None:
    section = _generate_project_fact_section(
        project_input=_project_fact_input("task-project-fact"),
        content="服务器数量待项目方确认后再确定部署规模。",
        citations=[],
    )

    gate = _evaluate_project_fact_section(section, citations=[])

    assert section.extra["mandatory_project_fact_violations"] == []
    assert gate.checks["high_risk_claims_grounded"] is True
    assert gate.passed is True


def test_qualification_for_brand_cannot_exempt_definite_server_count() -> None:
    section = _generate_project_fact_section(
        project_input=_project_fact_input("task-project-fact"),
        content="本项目确定部署1000台服务器，服务器品牌待项目方确认。",
        citations=[],
    )

    gate = _evaluate_project_fact_section(section, citations=[])
    violations = section.extra["mandatory_project_fact_violations"]

    assert violations
    assert any("1000台服务器" in item["claim"] for item in violations)
    assert gate.checks["high_risk_claims_grounded"] is False
    assert gate.passed is False


def test_contradictory_quantity_qualification_cannot_exempt_commitment() -> None:
    section = _generate_project_fact_section(
        project_input=_project_fact_input("task-project-fact"),
        content="本项目部署1000台服务器，具体数量待项目方确认。",
        citations=[],
    )

    gate = _evaluate_project_fact_section(section, citations=[])
    violations = section.extra["mandatory_project_fact_violations"]

    assert violations
    assert any("1000台服务器" in item["claim"] for item in violations)
    assert gate.checks["high_risk_claims_grounded"] is False
    assert gate.passed is False


def test_minor_semantic_advisory_remains_deliverable() -> None:
    from apps.enterprise_document.schemas.scheme_writer_schema import (
        SchemeSectionSchema,
        SectionEvalSchema,
        TruncationCheckSchema,
    )
    from schemas.status import ExecutionStatus

    section = SchemeSectionSchema(
        section_id="section-advisory",
        section_title="资源配置",
        section_order=1,
        content="本章节存在轻微范围偏移，但没有高风险项目事实。",
        status=ExecutionStatus.PARTIAL_SUCCESS,
        truncation=TruncationCheckSchema(truncated=False),
        eval_result=SectionEvalSchema(
            passed=True,
            warnings=["semantic:section_scope_drift"],
        ),
        extra={"mandatory_project_fact_violations": []},
    )

    gate = _evaluate_project_fact_section(section, citations=[])

    assert gate.checks["high_risk_claims_grounded"] is True
    assert gate.passed is True


def test_torn_tail_same_semantics_recovers_exactly_once(tmp_path: Path) -> None:
    path = tmp_path / "records.jsonl"
    path.write_bytes(
        b'{"capture_id":"effect","value":1}\n{"capture_id":"torn"'
    )

    JsonlWriter(path).write_once(
        {"capture_id": "effect", "value": 1},
        id_field="capture_id",
    )

    assert _read_jsonl(path) == [{"capture_id": "effect", "value": 1}]


def test_torn_tail_conflicting_semantics_recovers_then_fails_closed(
    tmp_path: Path,
) -> None:
    path = tmp_path / "records.jsonl"
    path.write_bytes(
        b'{"capture_id":"effect","value":1}\n{"capture_id":"torn"'
    )

    with pytest.raises(ValueError, match="semantic payload"):
        JsonlWriter(path).write_once(
            {"capture_id": "effect", "value": 999},
            id_field="capture_id",
        )

    assert _read_jsonl(path) == [{"capture_id": "effect", "value": 1}]


def test_ack_failure_retry_same_rag_semantics_commits_exactly_once(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    ledger = JsonlSideEffectCommitStore(tmp_path / "ledger.jsonl")
    original_mark = ledger.mark_committed
    mark_calls = 0

    def fail_first_ack(claim) -> bool:
        nonlocal mark_calls
        mark_calls += 1
        if mark_calls == 1:
            raise OSError("injected ack failure")
        return original_mark(claim)

    monkeypatch.setattr(ledger, "mark_committed", fail_first_ack)
    output = tmp_path / "rag.jsonl"

    def stage(execution_id: str, query: str) -> WorkflowExecutionControl:
        control = _control(
            ledger=ledger,
            execution_id=execution_id,
            commit_scope_id="task:rag",
        )
        with activate_execution_control(control):
            RagRunCapture(output).capture(
                {
                    "run_id": "run",
                    "query": query,
                    "created_at": execution_id,
                    "latency_ms": 1 if execution_id == "attempt-1" else 999,
                }
            )
        return control

    with pytest.raises(OSError, match="ack failure"):
        stage("attempt-1", "same query").finalize_external_commits(
            attempt_succeeded=True
        )
    summary = stage("attempt-2", "same query").finalize_external_commits(
        attempt_succeeded=True
    )

    assert summary["committed"] == 1
    assert len(_read_jsonl(output)) == 1


def test_ack_failure_target_a_retry_same_id_payload_b_fails_closed(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    ledger_path = tmp_path / "ledger.jsonl"
    ledger = JsonlSideEffectCommitStore(ledger_path)
    original_mark = ledger.mark_committed
    mark_calls = 0

    def fail_first_ack(claim) -> bool:
        nonlocal mark_calls
        mark_calls += 1
        if mark_calls == 1:
            raise OSError("injected ack failure")
        return original_mark(claim)

    monkeypatch.setattr(ledger, "mark_committed", fail_first_ack)
    output = tmp_path / "rag.jsonl"

    def stage(execution_id: str, query: str) -> WorkflowExecutionControl:
        control = _control(
            ledger=ledger,
            execution_id=execution_id,
            commit_scope_id="task:rag",
        )
        with activate_execution_control(control):
            RagRunCapture(output).capture({"run_id": "run", "query": query})
        return control

    with pytest.raises(OSError, match="ack failure"):
        stage("attempt-1", "payload A").finalize_external_commits(
            attempt_succeeded=True
        )
    with pytest.raises(ValueError, match="semantic payload"):
        stage("attempt-2", "payload B").finalize_external_commits(
            attempt_succeeded=True
        )

    target = _read_jsonl(output)
    assert len(target) == 1
    assert target[0]["query"] == "payload A"
    assert not any(
        event["event_type"] == "committed"
        for event in _read_jsonl(ledger_path)
    )


def test_nested_retrieval_timestamp_changes_fingerprint_and_committed_retry_conflicts(
    tmp_path: Path,
) -> None:
    ledger_path = tmp_path / "ledger.jsonl"
    ledger = JsonlSideEffectCommitStore(ledger_path)
    output = tmp_path / "rag.jsonl"
    record_a = {
        "run_id": "run",
        "query": "q",
        "retrieval_results": [
            {
                "doc_id": "d1",
                "metadata": {
                    "timestamp": "2025-01-01",
                    "source": "policy",
                },
            }
        ],
    }
    record_b = {
        **record_a,
        "retrieval_results": [
            {
                "doc_id": "d1",
                "metadata": {
                    "timestamp": "2026-01-01",
                    "source": "policy",
                },
            }
        ],
    }

    assert _rag_fingerprint(record_a) != _rag_fingerprint(record_b)
    first = _stage_rag_capture(
        ledger=ledger,
        output=output,
        execution_id="attempt-1",
        record=record_a,
    )
    assert first.finalize_external_commits(attempt_succeeded=True)["committed"] == 1

    retry = _stage_rag_capture(
        ledger=JsonlSideEffectCommitStore(ledger_path),
        output=output,
        execution_id="attempt-2",
        record=record_b,
    )
    with pytest.raises(SideEffectIdentityConflict):
        retry.finalize_external_commits(attempt_succeeded=True)

    target = _read_jsonl(output)
    assert len(target) == 1
    assert (
        target[0]["retrieval_results"][0]["metadata"]["timestamp"]
        == "2025-01-01"
    )
    committed = [
        event
        for event in _read_jsonl(ledger_path)
        if event["event_type"] == "committed"
    ]
    assert len(committed) == 1


def test_nested_evidence_created_at_is_semantic() -> None:
    record_a = {
        "run_id": "run",
        "query": "q",
        "evidence_quality": {
            "item_judgements": [
                {
                    "evidence_id": "parent-1",
                    "metadata": {"created_at": "2025-01-01"},
                }
            ]
        },
    }
    record_b = {
        **record_a,
        "evidence_quality": {
            "item_judgements": [
                {
                    "evidence_id": "parent-1",
                    "metadata": {"created_at": "2026-01-01"},
                }
            ]
        },
    }

    assert _rag_fingerprint(record_a) != _rag_fingerprint(record_b)
    assert (
        rag_run_semantic_projection(record_a)["evidence_quality"][
            "item_judgements"
        ][0]["metadata"]["created_at"]
        == "2025-01-01"
    )


def test_business_metadata_model_call_id_is_semantic() -> None:
    record_a = {
        "run_id": "run",
        "query": "q",
        "metadata": {
            "source": "policy",
            "model_call_id": "business-version-a",
        },
    }
    record_b = {
        **record_a,
        "metadata": {
            "source": "policy",
            "model_call_id": "business-version-b",
        },
    }

    assert _rag_fingerprint(record_a) != _rag_fingerprint(record_b)
    assert (
        rag_run_semantic_projection(record_a)["metadata"]["model_call_id"]
        == "business-version-a"
    )


def test_explicit_runtime_paths_do_not_change_rag_fingerprint() -> None:
    def record(suffix: str, latency_ms: int) -> dict:
        model_call = {
            "model_call_id": f"call-{suffix}",
            "provider_attempts": 1 if suffix == "a" else 3,
            "latency_ms": latency_ms,
            "selected_model": "judge-v1",
            "call_purpose": "rag_evidence_assessment",
            "success": True,
        }
        query_expansion = {
            "original_query": "q",
            "retrieval_queries": ["q"],
            "metadata": {
                "rag_fusion": {
                    "method": "llm",
                    "latency_ms": latency_ms,
                    "raw_output": "q",
                },
                "hyde": {
                    "method": "llm",
                    "latency_ms": latency_ms,
                    "raw_output": "document",
                },
            },
        }
        evidence_quality = {
            "latency_ms": latency_ms,
            "retrieval_confidence": 0.9,
            "initial_assessment": {
                "sufficient": True,
                "report": {"latency_ms": latency_ms, "method": "llm"},
            },
            "final_assessment": {
                "sufficient": True,
                "report": {"latency_ms": latency_ms, "method": "llm"},
            },
        }
        return {
            "run_id": "run",
            "query": "q",
            "created_at": f"created-{suffix}",
            "finished_at": f"finished-{suffix}",
            "latency_ms": latency_ms,
            "timestamp": f"runtime-{suffix}",
            "attempt": {
                "execution_id": f"attempt-{suffix}",
                "commit_scope_id": "task:rag",
            },
            "retrieval_results": [
                {
                    "doc_id": "d1",
                    "metadata": {
                        "timestamp": "business-time",
                        "latency_ms": 77,
                    },
                }
            ],
            "query_expansion": query_expansion,
            "evidence_quality": evidence_quality,
            "model_calls": [model_call],
            "metadata": {
                "query_expansion": query_expansion,
                "evidence_quality": evidence_quality,
                "model_calls": [model_call],
                "model_call_ids": [f"call-{suffix}"],
                "query_expansion_model_call_ids": [f"query-call-{suffix}"],
                "source_lineage": {"timestamp": "business-time"},
            },
        }

    record_a = record("a", 10)
    record_b = record("b", 999)

    assert _rag_fingerprint(record_a) == _rag_fingerprint(record_b)
    projection = rag_run_semantic_projection(record_a)
    assert projection["retrieval_results"][0]["metadata"]["latency_ms"] == 77
    assert projection["metadata"]["source_lineage"]["timestamp"] == "business-time"


def test_ack_failure_then_nested_business_timestamp_change_fails_closed(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    ledger_path = tmp_path / "ledger.jsonl"
    ledger = JsonlSideEffectCommitStore(ledger_path)
    original_mark = ledger.mark_committed
    mark_calls = 0

    def fail_first_ack(claim) -> bool:
        nonlocal mark_calls
        mark_calls += 1
        if mark_calls == 1:
            raise OSError("injected ack failure")
        return original_mark(claim)

    monkeypatch.setattr(ledger, "mark_committed", fail_first_ack)
    output = tmp_path / "rag.jsonl"

    def record(timestamp: str) -> dict:
        return {
            "run_id": "run",
            "query": "q",
            "retrieval_results": [
                {
                    "doc_id": "d1",
                    "metadata": {
                        "timestamp": timestamp,
                        "source": "policy",
                    },
                }
            ],
        }

    with pytest.raises(OSError, match="ack failure"):
        _stage_rag_capture(
            ledger=ledger,
            output=output,
            execution_id="attempt-1",
            record=record("2025-01-01"),
        ).finalize_external_commits(attempt_succeeded=True)

    with pytest.raises(ValueError, match="semantic payload"):
        _stage_rag_capture(
            ledger=ledger,
            output=output,
            execution_id="attempt-2",
            record=record("2026-01-01"),
        ).finalize_external_commits(attempt_succeeded=True)

    target = _read_jsonl(output)
    assert len(target) == 1
    assert (
        target[0]["retrieval_results"][0]["metadata"]["timestamp"]
        == "2025-01-01"
    )
    assert not any(
        event["event_type"] == "committed"
        for event in _read_jsonl(ledger_path)
    )


def test_ack_failure_then_runtime_only_changes_eventually_commit_exactly_once(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    ledger_path = tmp_path / "ledger.jsonl"
    ledger = JsonlSideEffectCommitStore(ledger_path)
    original_mark = ledger.mark_committed
    mark_calls = 0

    def fail_first_ack(claim) -> bool:
        nonlocal mark_calls
        mark_calls += 1
        if mark_calls == 1:
            raise OSError("injected ack failure")
        return original_mark(claim)

    monkeypatch.setattr(ledger, "mark_committed", fail_first_ack)
    output = tmp_path / "rag.jsonl"

    def record(suffix: str, latency_ms: int) -> dict:
        return {
            "run_id": "run",
            "query": "q",
            "created_at": f"created-{suffix}",
            "finished_at": f"finished-{suffix}",
            "latency_ms": latency_ms,
            "model_calls": [
                {
                    "model_call_id": f"call-{suffix}",
                    "provider_attempts": 1 if suffix == "a" else 2,
                    "latency_ms": latency_ms,
                    "selected_model": "retrieval-model",
                    "call_purpose": "rag_query_rewrite",
                    "success": True,
                }
            ],
            "retrieval_results": [
                {
                    "doc_id": "d1",
                    "metadata": {"timestamp": "business-time"},
                }
            ],
        }

    with pytest.raises(OSError, match="ack failure"):
        _stage_rag_capture(
            ledger=ledger,
            output=output,
            execution_id="attempt-1",
            record=record("a", 10),
        ).finalize_external_commits(attempt_succeeded=True)

    summary = _stage_rag_capture(
        ledger=ledger,
        output=output,
        execution_id="attempt-2",
        record=record("b", 999),
    ).finalize_external_commits(attempt_succeeded=True)

    assert summary["committed"] == 1
    assert len(_read_jsonl(output)) == 1
    committed = [
        event
        for event in _read_jsonl(ledger_path)
        if event["event_type"] == "committed"
    ]
    assert len(committed) == 1
