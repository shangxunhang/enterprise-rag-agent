"""INT352-04: natural canonical capacity denial survives committed terminal read.

Additive integration coverage, outside the frozen 350/351/352 Target sets.
The existing eight-Section Claim + PostgreSQL workload supplies all 44 real calls.
"""
from contextlib import ExitStack
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import sqlite3
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from agent.runtime.business_node_execution_runner import WorkflowNodeExecutionOutcome
from application.mainline_service import MainlineApplicationService
from application.terminal_outcome_projector import public_terminal_result
from apps.enterprise_document.services.agent_rag_v1.budget_manager import (
    CanonicalBudgetCapacityExceeded, CanonicalBudgetDeadlineExceeded, CanonicalBudgetDraining,
)
from apps.enterprise_document.services.agent_rag_v1.memory_composition import TargetDurableMemoryComposition
from apps.enterprise_document.services.agent_rag_v1.memory_runtime import DurableMemoryRuntime
from apps.enterprise_document.services.agent_rag_v1.post_write_composition import TargetPostWriteComposition
from apps.enterprise_document.services.agent_rag_v1.section_dependency_runtime import SectionDependencyRuntime
from apps.enterprise_document.services.scheme_writer.runtime_support import SchemeWriterRuntimeSupport
from apps.enterprise_document.workflows.top_level_business_graph import EnterpriseDocumentWorkflowEngine
from core.persistence.crash_safe_jsonl import CrashSafeJsonlEventLog, CrashSafeJsonlRecordSink
from core.retry_policy import RetryDecisionPolicy
from core.runtime.execution_control import WorkflowExecutionControl
from model_gateway.fake_llm_client import FakeLLMClient
import test_step7_combined_integration as combined
from test_step7_durable_memory_postgres import pg_store


def _durable_snapshot(root, store):
    files, databases = {}, {}
    for path in sorted(root.rglob('*')):
        if not path.is_file() or path.suffix == '.lock' or path.name.endswith(('-wal', '-shm')):
            continue
        if path.read_bytes()[:16] == b'SQLite format 3\x00':
            with sqlite3.connect(path.as_uri() + '?mode=ro', uri=True) as db:
                content = '\n'.join(db.iterdump()).encode()
            databases[str(path.relative_to(root))] = hashlib.sha256(content).hexdigest()
        else:
            files[str(path.relative_to(root))] = hashlib.sha256(path.read_bytes()).hexdigest()
    tables = {}
    with store._transaction() as cursor:
        cursor.execute('SELECT tablename FROM pg_tables WHERE schemaname=current_schema() ORDER BY tablename')
        names = [row[0] for row in cursor.fetchall()]
        for name in names:
            assert name.replace('_', '').isalnum()
            cursor.execute('SELECT row_to_json(t)::text FROM "' + name + '" t')
            tables[name] = sorted(row[0] for row in cursor.fetchall())
    return files, databases, tables


TERMINAL_READ_GUARDS = (
    (FakeLLMClient, 'generate'),
    (EnterpriseDocumentWorkflowEngine, 'recover'),
    (EnterpriseDocumentWorkflowEngine, '_run_stage'),
    (TargetPostWriteComposition, 'complete_target_post_write'),
    (TargetDurableMemoryComposition, 'reconcile_run'),
    (DurableMemoryRuntime, 'project_source'),
    (SectionDependencyRuntime, 'complete_source'),
    (CrashSafeJsonlEventLog, 'append_serialized'),
    (CrashSafeJsonlEventLog, 'append_many_serialized'),
    (CrashSafeJsonlRecordSink, 'append_record'),
    (CrashSafeJsonlRecordSink, 'append_record_once'),
    (CrashSafeJsonlEventLog, '_truncate_unlocked'),
)


def test_natural_44_call_exhaustion_committed_terminal_read(tmp_path, monkeypatch, pg_store):
    observed = {}
    original_run = MainlineApplicationService.run
    original_build = combined.budget_mainline._InspectingFactory.build

    def capture_run(service, **kwargs):
        observed['request'] = kwargs
        observed['summary'] = original_run(service, **kwargs)
        return observed['summary']

    def capture_build(factory, **kwargs):
        supervisor = original_build(factory, **kwargs)
        observed['engine'] = supervisor.workflow_engine
        return supervisor

    monkeypatch.setattr(MainlineApplicationService, 'run', capture_run)
    monkeypatch.setattr(combined.budget_mainline._InspectingFactory, 'build', capture_build)
    combined.test_combined_dependency_memory_eight_section_budget(tmp_path, monkeypatch, pg_store, 2)
    summary = observed['summary']
    engine = observed['engine']
    top = combined.cross.w13._latest_w13_top_level_payload(engine)
    state = engine.budget_runtime(top.task_id, top.run_id).canonical_state()
    reservations = [row for row in state.reservations
                    if row.reservation_ref.reservation_kind == 'logical_model_call' and row.status == 'consumed']
    assert len(reservations) == len({row.reservation_ref.operation_ref for row in reservations}) == 44
    assert state.used.logical_model_calls == state.limits.logical_model_calls == 44
    assert state.used.true_retry_attempts == state.active_reserved.logical_model_calls == 0
    assert (state.deadline_at - state.started_at).total_seconds() == 120
    trace = [json.loads(line) for line in (tmp_path / 'runs' / (top.run_id + '_trace.jsonl')).read_text(encoding='utf-8').splitlines()]
    ordinals = {row['correlation']['provider_attempt_ordinal'] for row in trace
                if row.get('correlation', {}).get('provider_attempt_ordinal') is not None}
    assert ordinals == {1}
    terminal_event = next(row for row in reversed(trace) if row['event_type'] == 'run_finished')
    elapsed = (datetime.fromisoformat(terminal_event['finished_at'].replace('Z', '+00:00')) - state.started_at).total_seconds()
    assert elapsed < 120
    outcome = summary['terminal_outcome']
    assert top.terminal_outcome.model_dump(mode='json') == outcome
    assert top.terminal_agent_result.result['terminal_outcome'] == outcome

    before = _durable_snapshot(tmp_path, pg_store)
    with ExitStack() as stack:
        stack.enter_context(patch.dict(os.environ, {'USE_REAL_RAG_TOOL': 'false'}))
        for owner, name in TERMINAL_READ_GUARDS:
            stack.enter_context(patch.object(owner, name, autospec=True,
                side_effect=AssertionError('terminal read invoked ' + owner.__name__ + '.' + name)))
        for _ in range(2):
            replay = MainlineApplicationService().read_terminal_result(
                project_root=Path(__file__).resolve().parents[2], task_id=top.task_id, run_id=top.run_id,
                output_root=tmp_path, settings=observed['request']['settings'],
                allow_demo_defaults=True, enable_agent_self_rag=False)
            assert public_terminal_result(replay) == public_terminal_result(summary)
            assert _durable_snapshot(tmp_path, pg_store) == before

    # Persist the actual observation before semantic assertions, including red runs.
    (tmp_path / 'resource-terminal-evidence.json').write_text(json.dumps(dict(
        terminal=outcome, canonical_error=top.terminal_error.model_dump(mode='json'),
        calls=44, unique_identities=44, retries=0, elapsed=elapsed, headroom=120-elapsed,
        terminal_reads=2, zero_write=True, no_redispatch=True), indent=2) + '\n', encoding='utf-8')
    assert top.terminal_error.failure_category.value == 'resource_exhausted'
    assert top.terminal_error.details['failure_origin'] == 'canonical_budget'
    assert outcome['status'] == outcome['failure_category'] == 'resource_exhausted'
    assert outcome['failure_origin'] == 'canonical_budget'
    assert outcome['status'] not in {'TECHNICAL_FAILURE', 'BLOCK'}
    assert outcome['control_action'] is None and not outcome['publication_exists']
    assert replay['target_release_publication'] is None


@pytest.mark.parametrize('exc,category,origin', [
    (CanonicalBudgetCapacityExceeded('logical_model_calls', limit=44, remaining=0, required=1), 'resource_exhausted', 'canonical_budget'),
    (CanonicalBudgetDeadlineExceeded(datetime(2026, 9, 13, tzinfo=timezone.utc)), 'resource_exhausted', 'canonical_budget'),
    (CanonicalBudgetDraining(), 'resource_exhausted', 'canonical_budget'),
    (RuntimeError('canonical budget capacity exceeded: logical_model_calls required=1 remaining=0 limit=44'), 'unknown', None),
    (ValueError('invalid structured output'), 'validation', None),
], ids=['capacity', 'deadline', 'draining', 'untyped-lookalike', 'technical-validation'])
def test_workflow_budget_type_preserved_without_message_guessing(exc, category, origin):
    engine = object.__new__(EnterpriseDocumentWorkflowEngine)
    engine.stage_service = SimpleNamespace(runtime_support=SchemeWriterRuntimeSupport())
    engine.retry_policy = RetryDecisionPolicy()
    worker = WorkflowNodeExecutionOutcome(kind='error', started_at='', finished_at='', latency_ms=0,
        execution_control=WorkflowExecutionControl(execution_id='typed-failure-regression', deadline_monotonic=0),
        exception=exc)
    error = engine._error_from_outcome(node_name='execute_sections', outcome=worker)
    assert error.error_type == type(exc).__name__ and error.message == str(exc)
    assert error.failure_category.value == category
    assert error.details.get('failure_origin') == origin
    assert error.retryable is False
