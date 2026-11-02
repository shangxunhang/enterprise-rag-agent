"""P2-B verification for required Gate V2 Trace projection and SQLite durability."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
import json
import sqlite3
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from apps.enterprise_document.schemas.pre_write_admission import (
    WriterAdmissionAssessmentState,
    WriterAdmissionDecisionRefSchema,
    WriterAdmissionFactAssessmentRefSchema,
    WriterAdmissionOutcome,
)
from apps.enterprise_document.schemas.pre_write_binding import (
    BindingApplicabilityState,
    WriterGateBindingRefSchema,
    WriterSubjectRefSchema,
)
from apps.enterprise_document.schemas.pre_write_control import (
    EnforcementAction,
    EnforcementBasis,
    PreWriteRoute,
    PreWriteRouteDecisionRefSchema,
)
from apps.enterprise_document.schemas.pre_write_writer import (
    WriterGenerationClass,
    WriterLogicalExecutionKey,
)
import observability.pre_write_trace as pre_write_trace_module
import observability.sqlite_trace_sink as sqlite_trace_sink_module
from observability.pre_write_trace import (
    PRE_WRITE_ADMISSION_DECISION_EVENT,
    PRE_WRITE_BINDING_APPLICABILITY_EVENT,
    PRE_WRITE_BINDING_COMMITTED_EVENT,
    PRE_WRITE_ENFORCEMENT_EVENT,
    PRE_WRITE_EVIDENCE_PROMOTION_EVENT,
    PRE_WRITE_RECOVERY_EVENT,
    PRE_WRITE_ROUTE_DECISION_EVENT,
    PRE_WRITE_WRITER_EXECUTION_EVENT,
    PreWriteTraceEmitter,
    REQUIRED_PRE_WRITE_EVENT_TYPES,
)
from contracts.evidence import evidence_bundle_sha256
from core.hashing import canonical_sha256
from core.runtime.pre_write_execution_store import SQLitePreWriteExecutionStore
from apps.enterprise_document.services.scheme_writer.pre_write_recovery import (
    PreWriteRecoveryController,
)
from observability.sqlite_trace_sink import (
    SQLiteTraceSink,
    TRACE_PERSISTENCE_RECOVERED_EVENT,
    TracePersistenceFailureDiagnostic,
    TraceSinkHealth,
)
from backend.tests import test_gate_v2_pre_write_e2e as p4_e2e


class _RecordingSink:
    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    def record(self, **kwargs: Any) -> dict[str, Any]:
        self.calls.append(kwargs)
        return kwargs


class _TraceP4WriterInvocation(p4_e2e._P4WriterInvocation):
    @staticmethod
    def _build(route_decision, *, evidence_bundle, section_title, **kwargs):
        prepared = p4_e2e._P4WriterInvocation._build(
            route_decision,
            evidence_bundle=evidence_bundle,
            section_title=section_title,
            **kwargs,
        )
        invocation_payload = prepared.invocation.model_dump(mode="json")
        invocation_payload["unified_writer_input_snapshot"] = {
            "section_title": section_title,
            "target_prompt_lock": {
                "context_package_id": "p5-trace-context",
                "context_sha256": "c" * 64,
            },
        }
        invocation = prepared.invocation.__class__.model_validate(invocation_payload)
        return SimpleNamespace(
            invocation=invocation,
            logical_execution_key=prepared.logical_execution_key,
            semantic_fingerprint=canonical_sha256(invocation.model_dump(mode="json")),
        )


def _attach_trace(coordinator, emitter: PreWriteTraceEmitter) -> None:
    coordinator._pre_write_services = replace(
        coordinator.require_pre_write_services(),
        trace_emitter=emitter,
    )


def _run_real_target_occurrence_chain(
    tmp_path: Path,
    *,
    trace_sink: SQLiteTraceSink,
    trace_enabled: bool,
):
    writer = _TraceP4WriterInvocation()
    evidence_service = p4_e2e._P4RecoveryEvidenceService(
        p4_e2e._p4_bundle(selected=True)
    )
    query_builder = p4_e2e._P4RecoveryQueryBuilder()
    controller = PreWriteRecoveryController(
        execution_store=SQLitePreWriteExecutionStore(
            tmp_path / "execution_control.sqlite3"
        )
    )
    coordinator = p4_e2e._p4_coordinator(
        writer=writer,
        recovery_rounds_limit=2,
        recovery_controller=controller,
        evidence_service=evidence_service,
        query_builder=query_builder,
    )
    _attach_trace(
        coordinator,
        PreWriteTraceEmitter(trace_sink, trace_enabled=trace_enabled),
    )
    graph, state = p4_e2e._p4_run_to_route(
        coordinator,
        p4_e2e._p4_bundle(selected=False),
    )
    for method_name in (
        "_dispatch_pre_write_recovery",
        "_execute_pre_write_recovery",
        "_complete_pre_write_recovery_dispatch",
        "_evaluate_pre_write_enforcement",
        "_commit_pre_write_route",
        "_invoke_pre_write_writer",
    ):
        state = p4_e2e._p4_apply(graph, state, method_name)
    return graph.codec.restore_payload(state), writer, evidence_service, query_builder


def _sqlite_event_types(path: Path) -> list[str]:
    with sqlite3.connect(str(path)) as connection:
        return [
            str(row[0])
            for row in connection.execute(
                "SELECT event_type FROM trace_events ORDER BY event_sequence"
            ).fetchall()
        ]


def _subject_ref(section_id: str = "section-1") -> WriterSubjectRefSchema:
    return WriterSubjectRefSchema(
        run_id="run-1",
        document_id="doc-1",
        section_id=section_id,
    )


def _fact_ref(
    assessment_id: str = "assessment-1",
    *,
    evidence_hash: str = "a" * 64,
) -> WriterAdmissionFactAssessmentRefSchema:
    return WriterAdmissionFactAssessmentRefSchema(
        assessment_id=assessment_id,
        requirements_schema_version="evidence_requirements_snapshot_v1",
        requirements_contract_version="writer_evidence_requirements_v1",
        requirements_sha256="d" * 64,
        evidence_bundle_schema_version="rag_evidence_contract_v1",
        evidence_bundle_sha256=evidence_hash,
    )


def _decision_ref(decision_id: str = "decision-1") -> WriterAdmissionDecisionRefSchema:
    return WriterAdmissionDecisionRefSchema(decision_id=decision_id)


def _binding_ref(binding_id: str = "binding-1") -> WriterGateBindingRefSchema:
    return WriterGateBindingRefSchema(binding_id=binding_id)


def _route_ref(route_id: str = "route-1") -> PreWriteRouteDecisionRefSchema:
    return PreWriteRouteDecisionRefSchema(route_decision_id=route_id)


def _emit_all_required(emitter: PreWriteTraceEmitter) -> None:
    subject_ref = _subject_ref()
    common = {
        "task_id": "task-1",
        "run_id": "run-1",
        "writer_subject_ref": subject_ref,
        "write_instance_id": "write-1",
    }
    emitter.emit_admission_decision(
        **common,
        active_evidence_bundle_sha256="a" * 64,
        fact_assessment_ref=_fact_ref(),
        decision_ref=_decision_ref(),
        assessment_lifecycle_state=WriterAdmissionAssessmentState.COMPLETED,
        admission_outcome=WriterAdmissionOutcome.ALLOW,
    )
    emitter.emit_binding_committed(
        **common,
        decision_ref=_decision_ref(),
        binding_ref=_binding_ref(),
    )
    emitter.emit_binding_applicability(
        **common,
        binding_ref=_binding_ref(),
        binding_applicability_state=BindingApplicabilityState.APPLICABLE,
        resolved_decision_ref=_decision_ref(),
    )
    emitter.emit_enforcement(
        **common,
        decision_ref=_decision_ref(),
        binding_ref=_binding_ref(),
        binding_applicability_state=BindingApplicabilityState.APPLICABLE,
        effective_enforcement_mode="strict",
        enforcement_basis=EnforcementBasis.BUSINESS_ADMISSION,
        action=EnforcementAction.CONTINUE,
        would_block_in_strict=False,
        runtime_config_contract_version="gate_v2_runtime_config_v1",
    )
    emitter.emit_recovery(
        **common,
        decision_ref=_decision_ref(),
        binding_ref=_binding_ref(),
        fact_assessment_ref=_fact_ref(),
        requirements_sha256="b" * 64,
        recovery_state_revision=1,
        recovery_rounds_used=1,
        recovery_rounds_limit=2,
        consecutive_no_progress_rounds=0,
        no_progress_rounds_limit=1,
        recovery_config_contract_version="gate_recovery_config_v1",
        logical_dispatch_occurrence="recovery-occurrence-1",
        phase="start",
    )
    emitter.emit_evidence_promotion(
        **common,
        previous_active_evidence_ref={
            "evidence_bundle_schema_version": "rag_evidence_contract_v1",
            "evidence_bundle_sha256": "a" * 64,
        },
        new_active_evidence_ref={
            "evidence_bundle_schema_version": "rag_evidence_contract_v1",
            "evidence_bundle_sha256": "b" * 64,
        },
        new_fact_assessment_ref=_fact_ref("assessment-2", evidence_hash="b" * 64),
        new_decision_ref=_decision_ref("decision-2"),
        new_binding_ref=_binding_ref("binding-2"),
        recovery_state_revision=2,
    )
    emitter.emit_route_decision(
        **common,
        route_decision_ref=_route_ref(),
        decision_ref=_decision_ref("decision-2"),
        binding_ref=_binding_ref("binding-2"),
        selected_route=PreWriteRoute.NORMAL,
        recovery_state_revision=2,
    )
    emitter.emit_writer_execution(
        **common,
        route_decision_ref=_route_ref(),
        generation_class=WriterGenerationClass.NORMAL_WRITE,
        evidence_bundle_sha256="b" * 64,
        context_package_id="context-1",
        context_sha256="c" * 64,
        writer_logical_execution_key=WriterLogicalExecutionKey(
            writer_subject_ref=subject_ref,
            write_instance_id="write-1",
            route_decision_ref=_route_ref(),
        ),
        phase="start",
    )


def test_required_event_taxonomy_is_exact_and_trace_enabled_cannot_suppress_it() -> None:
    assert REQUIRED_PRE_WRITE_EVENT_TYPES == {
        PRE_WRITE_ADMISSION_DECISION_EVENT,
        PRE_WRITE_BINDING_COMMITTED_EVENT,
        PRE_WRITE_BINDING_APPLICABILITY_EVENT,
        PRE_WRITE_ENFORCEMENT_EVENT,
        PRE_WRITE_RECOVERY_EVENT,
        PRE_WRITE_EVIDENCE_PROMOTION_EVENT,
        PRE_WRITE_ROUTE_DECISION_EVENT,
        PRE_WRITE_WRITER_EXECUTION_EVENT,
    }
    sink = _RecordingSink()
    emitter = PreWriteTraceEmitter(sink, trace_enabled=False)
    _emit_all_required(emitter)
    assert [call["event_type"] for call in sink.calls] == [
        PRE_WRITE_ADMISSION_DECISION_EVENT,
        PRE_WRITE_BINDING_COMMITTED_EVENT,
        PRE_WRITE_BINDING_APPLICABILITY_EVENT,
        PRE_WRITE_ENFORCEMENT_EVENT,
        PRE_WRITE_RECOVERY_EVENT,
        PRE_WRITE_EVIDENCE_PROMOTION_EVENT,
        PRE_WRITE_ROUTE_DECISION_EVENT,
        PRE_WRITE_WRITER_EXECUTION_EVENT,
    ]
    assert all(call["payload"]["write_instance_id"] == "write-1" for call in sink.calls)
    assert emitter.emit_optional_diagnostic(
        task_id="task-1",
        run_id="run-1",
        event_type="optional_debug_v1",
        payload={"debug": True},
    ) is None
    assert len(sink.calls) == 8


def test_emitter_projects_only_supplied_truth_and_rejects_unbounded_programming_payload() -> None:
    sink = _RecordingSink()
    emitter = PreWriteTraceEmitter(sink)
    emitter.emit_route_decision(
        task_id="task-1",
        run_id="run-1",
        writer_subject_ref=_subject_ref(),
        write_instance_id="write-1",
        route_decision_ref=_route_ref("route-provided"),
        decision_ref=_decision_ref("decision-provided"),
        binding_ref=_binding_ref("binding-provided"),
        selected_route=PreWriteRoute.FALLBACK,
    )
    payload = sink.calls[0]["payload"]
    assert payload["selected_route"] == "fallback"
    assert payload["route_decision_ref"] == _route_ref("route-provided").model_dump(
        mode="json"
    )
    assert "admission" not in payload

    with pytest.raises(ValueError, match="bounded projection"):
        emitter.emit_route_decision(
            task_id="task-1",
            run_id="run-1",
            writer_subject_ref=_subject_ref("x" * (33 * 1024)),
            write_instance_id="write-1",
            route_decision_ref=_route_ref(),
            decision_ref=_decision_ref(),
            binding_ref=_binding_ref(),
            selected_route=PreWriteRoute.NORMAL,
        )


def test_sqlite_trace_schema_indexes_and_event_sequence_are_transactional_under_concurrency(
    tmp_path: Path,
) -> None:
    sink = SQLiteTraceSink(tmp_path / "run_trace.sqlite3", retry_backoff_seconds=0)

    def write(index: int) -> None:
        outcome = sink.record(
            task_id="task-1",
            run_id="run-concurrent",
            event_type="test_event",
            component_type="test",
            component_name="writer",
            payload={"write_instance_id": "write-1", "index": index},
        )
        assert not isinstance(outcome, TracePersistenceFailureDiagnostic)

    with ThreadPoolExecutor(max_workers=8) as pool:
        list(pool.map(write, range(40)))

    with sqlite3.connect(sink.database_path) as connection:
        rows = connection.execute(
            "SELECT event_sequence, event_id FROM trace_events "
            "WHERE run_id = ? ORDER BY event_sequence",
            ("run-concurrent",),
        ).fetchall()
        indexes = {
            row[1]
            for row in connection.execute("PRAGMA index_list(trace_events)").fetchall()
        }
        columns = {
            row[1]
            for row in connection.execute("PRAGMA table_info(trace_events)").fetchall()
        }
    assert [row[0] for row in rows] == list(range(1, 41))
    assert len({row[1] for row in rows}) == 40
    assert {
        "run_id",
        "task_id",
        "write_instance_id",
        "event_type",
        "event_sequence",
        "created_at",
    } <= columns
    assert {
        "idx_trace_events_run_type",
        "idx_trace_events_task",
        "idx_trace_events_write_instance",
        "idx_trace_events_created_at",
    } <= indexes


def test_sqlite_trace_busy_failure_buffers_then_recovers_without_jsonl_fallback(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    sink = SQLiteTraceSink(
        tmp_path / "run_trace.sqlite3",
        max_write_attempts=2,
        retry_backoff_seconds=0,
        pending_capacity=4,
    )
    real_persist = sink._persist_pending
    remaining_failures = 2

    def fail_then_recover(pending):
        nonlocal remaining_failures
        if remaining_failures:
            remaining_failures -= 1
            raise sqlite3.OperationalError("database is locked")
        return real_persist(pending)

    monkeypatch.setattr(sink, "_persist_pending", fail_then_recover)
    first = sink.record(
        task_id="task-1",
        run_id="run-1",
        event_type="required-test",
        component_type="gate",
        component_name="test",
        payload={"write_instance_id": "write-1"},
    )
    assert isinstance(first, TracePersistenceFailureDiagnostic)
    assert first.failure_kind == "SQLITE_BUSY"
    assert sink.health is TraceSinkHealth.DEGRADED
    assert sink.pending_count == 1

    second = sink.record(
        task_id="task-1",
        run_id="run-1",
        event_type="required-test-2",
        component_type="gate",
        component_name="test",
        payload={"write_instance_id": "write-1"},
    )
    assert not isinstance(second, TracePersistenceFailureDiagnostic)
    assert sink.health is TraceSinkHealth.HEALTHY
    assert sink.pending_count == 0
    assert list(tmp_path.glob("*.jsonl")) == []
    with sqlite3.connect(sink.database_path) as connection:
        rows = connection.execute(
            "SELECT event_id, event_type, event_json FROM trace_events ORDER BY event_sequence"
        ).fetchall()
    assert len(rows) == 3
    assert [row[0] for row in rows].count(first.event_id) == 1
    recovery_rows = [row for row in rows if row[1] == TRACE_PERSISTENCE_RECOVERED_EVENT]
    assert len(recovery_rows) == 1
    recovery_payload = json.loads(recovery_rows[0][2])["payload"]
    assert recovery_payload["recovered_failed_event_id"] == first.event_id
    assert recovery_payload["failure_kind"] == "SQLITE_BUSY"


@pytest.mark.parametrize(
    ("message", "failure_kind"),
    [
        ("database or disk is full", "SQLITE_FULL"),
        ("disk I/O error", "SQLITE_IOERR"),
    ],
)
def test_nonretryable_trace_storage_failures_degrade_without_raising_or_side_effect_retry(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    message: str,
    failure_kind: str,
) -> None:
    sink = SQLiteTraceSink(
        tmp_path / f"{failure_kind}.sqlite3",
        max_write_attempts=1,
        retry_backoff_seconds=0,
    )
    business_side_effect_calls = 0

    def fail_storage(pending):
        del pending
        raise sqlite3.OperationalError(message)

    monkeypatch.setattr(sink, "_persist_pending", fail_storage)
    outcome = sink.record(
        task_id="task-1",
        run_id="run-1",
        event_type="required-test",
        component_type="gate",
        component_name="test",
        payload={"write_instance_id": "write-1"},
    )
    assert isinstance(outcome, TracePersistenceFailureDiagnostic)
    assert outcome.failure_kind == failure_kind
    assert sink.health is TraceSinkHealth.DEGRADED
    assert sink.pending_count == 1
    assert business_side_effect_calls == 0
    assert list(tmp_path.glob("*.jsonl")) == []


def test_trace_pending_buffer_is_bounded_and_tracks_drops(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    sink = SQLiteTraceSink(
        tmp_path / "run_trace.sqlite3",
        max_write_attempts=1,
        pending_capacity=2,
    )

    def fail_storage(pending):
        del pending
        raise sqlite3.OperationalError("database or disk is full")

    monkeypatch.setattr(sink, "_persist_pending", fail_storage)
    for index in range(5):
        sink.record(
            task_id="task-1",
            run_id="run-1",
            event_type=f"required-{index}",
            component_type="gate",
            component_name="test",
        )
    assert sink.pending_count == 2
    assert sink.dropped_event_count == 3
    assert sink.health is TraceSinkHealth.DEGRADED


def test_required_trace_modules_have_no_business_execution_or_jsonl_fallback_dependency() -> None:
    emitter_source = Path(pre_write_trace_module.__file__).read_text(encoding="utf-8")
    sink_source = Path(sqlite_trace_sink_module.__file__).read_text(encoding="utf-8")
    forbidden = (
        "apps.enterprise_document.services",
        "model_gateway",
        "EvidenceService",
        "PreWriteRecoveryController",
        "PreWriteRouter",
        "PreWriteWriterInvocationService",
    )
    assert all(token not in emitter_source for token in forbidden)
    assert all(token not in sink_source for token in forbidden)
    assert "JsonlRunTraceRecorder" not in sink_source
    assert "JsonlSideEffectCommitStore" not in sink_source


def test_trace_programming_schema_defect_is_not_laundered_into_storage_degradation(
    tmp_path: Path,
) -> None:
    sink = SQLiteTraceSink(tmp_path / "run_trace.sqlite3")
    caught: BaseException | None = None
    try:
        sink.record(
            task_id="task-1",
            run_id="run-1",
            event_type="bad-programming-event",
            component_type="gate",
            component_name="test",
            payload={"unsupported": object()},
        )
    except BaseException as exc:  # the exact serializer exception is Pydantic-version specific
        caught = exc
    assert caught is not None
    assert not isinstance(caught, sqlite3.Error)
    assert sink.health is TraceSinkHealth.HEALTHY
    assert sink.pending_count == 0


def _business_control_snapshot(payload) -> dict[str, Any]:
    return {
        "evidence_sha256": evidence_bundle_sha256(payload.active_evidence_bundle),
        "assessment": payload.active_writer_admission_fact_assessment.model_dump(mode="json"),
        "decision": payload.active_writer_admission_decision.model_dump(mode="json"),
        "binding": payload.active_writer_gate_binding.model_dump(mode="json"),
        "enforcement": payload.current_enforcement_result.model_dump(mode="json"),
        "recovery": payload.recovery_runtime_state.model_dump(mode="json"),
        "route": payload.current_pre_write_route_decision.model_dump(mode="json"),
        "section": payload.section.model_dump(mode="json"),
    }


def test_real_target_occurrence_emits_all_required_trace_events_even_when_trace_disabled(
    tmp_path: Path,
) -> None:
    trace_path = tmp_path / "run_trace.sqlite3"
    sink = SQLiteTraceSink(trace_path, retry_backoff_seconds=0)

    payload, writer, evidence_service, query_builder = _run_real_target_occurrence_chain(
        tmp_path,
        trace_sink=sink,
        trace_enabled=False,
    )

    event_types = _sqlite_event_types(trace_path)
    assert REQUIRED_PRE_WRITE_EVENT_TYPES <= set(event_types)
    assert PRE_WRITE_RECOVERY_EVENT in event_types
    assert PRE_WRITE_EVIDENCE_PROMOTION_EVENT in event_types
    assert event_types.count(PRE_WRITE_WRITER_EXECUTION_EVENT) == 2
    assert writer.routes == [PreWriteRoute.NORMAL]
    assert evidence_service.calls == 1
    assert query_builder.calls == 1
    assert payload.section is not None
    assert payload.section.content == "canonical Writer artifact"


def test_real_target_trace_storage_degradation_does_not_change_business_control_or_redispatch(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    healthy_dir = tmp_path / "healthy"
    degraded_dir = tmp_path / "degraded"
    healthy_dir.mkdir()
    degraded_dir.mkdir()

    healthy_sink = SQLiteTraceSink(
        healthy_dir / "run_trace.sqlite3",
        max_write_attempts=1,
        retry_backoff_seconds=0,
    )
    healthy_payload, healthy_writer, healthy_evidence, healthy_query = (
        _run_real_target_occurrence_chain(
            healthy_dir,
            trace_sink=healthy_sink,
            trace_enabled=True,
        )
    )

    degraded_sink = SQLiteTraceSink(
        degraded_dir / "run_trace.sqlite3",
        max_write_attempts=1,
        retry_backoff_seconds=0,
    )

    def fail_storage(pending):
        del pending
        raise sqlite3.OperationalError("database or disk is full")

    monkeypatch.setattr(degraded_sink, "_persist_pending", fail_storage)
    degraded_payload, degraded_writer, degraded_evidence, degraded_query = (
        _run_real_target_occurrence_chain(
            degraded_dir,
            trace_sink=degraded_sink,
            trace_enabled=True,
        )
    )

    assert degraded_sink.health is TraceSinkHealth.DEGRADED
    assert degraded_sink.pending_count > 0
    assert _business_control_snapshot(degraded_payload) == _business_control_snapshot(
        healthy_payload
    )
    assert healthy_evidence.calls == degraded_evidence.calls == 1
    assert healthy_query.calls == degraded_query.calls == 1
    assert healthy_writer.routes == degraded_writer.routes == [PreWriteRoute.NORMAL]
