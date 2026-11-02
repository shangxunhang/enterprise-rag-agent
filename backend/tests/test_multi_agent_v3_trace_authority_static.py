"""Static negative-authority fences for Step 3.3 typed Trace correlation."""

from __future__ import annotations

import ast
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
TRACE_CORE = (
    ROOT / "schemas" / "trace.py",
    ROOT / "observability" / "trace_context.py",
    ROOT / "data_capture" / "run_trace_recorder.py",
    ROOT / "observability" / "sqlite_trace_sink.py",
)
TARGET_PRODUCTION_SURFACES = (
    ROOT / "apps" / "enterprise_document" / "workflows" / "top_level_business_graph.py",
    ROOT / "apps" / "enterprise_document" / "workflows" / "section_execution_graph.py",
    ROOT / "apps" / "enterprise_document" / "workflows" / "section_parallel_execution_graph.py",
    ROOT / "apps" / "enterprise_document" / "services" / "scheme_writer" / "stage_service.py",
)


def _source(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def _call_names(path: Path) -> set[str]:
    tree = ast.parse(_source(path))
    names: set[str] = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        if isinstance(node.func, ast.Attribute):
            names.add(node.func.attr)
        elif isinstance(node.func, ast.Name):
            names.add(node.func.id)
    return names


def test_trace_core_has_no_canonical_authority_mutator_calls() -> None:
    forbidden = {
        "transition_run",
        "transition_attempt",
        "create_attempt",
        "create_retry_successor",
        "authorize_retry",
        "reserve",
        "settle",
        "release_budget",
        "promote",
        "promote_head",
        "advance_head",
        "commit_version",
        "commit_result",
        "mark_writer_dispatched",
        "authorize_writer",
        "decide_route",
        "publish_release",
    }
    observed: set[str] = set()
    for path in TRACE_CORE:
        observed.update(_call_names(path))
    assert forbidden.isdisjoint(observed)


def test_trace_core_does_not_import_canonical_owner_services() -> None:
    forbidden_fragments = (
        "lifecycle_runtime",
        "budget_manager",
        "artifact_runtime",
        "fact_snapshot_runtime",
        "research_runtime",
        "paragraph_runtime",
        "claim_verification_runtime",
        "memory_runtime",
        "pre_write_route_decision_store",
        "side_effects",
    )
    joined = "\n".join(_source(path) for path in TRACE_CORE)
    for fragment in forbidden_fragments:
        assert fragment not in joined


def test_sif02_semantic_fingerprint_validation_does_not_read_trace_metadata() -> None:
    path = ROOT / "core" / "runtime" / "side_effects.py"
    tree = ast.parse(_source(path))
    target = next(
        node
        for node in tree.body
        if isinstance(node, ast.FunctionDef)
        and node.name == "validate_sif02_semantic_fingerprint"
    )
    segment = ast.get_source_segment(_source(path), target) or ""
    for forbidden in (
        "trace_id",
        "span_id",
        "parent_span_id",
        "agent_invocation_id",
        "execution_id",
        "retrieval_trace_id",
        "failure_origin",
    ):
        assert forbidden not in segment


def test_trace_schema_has_no_semantic_fingerprint_field() -> None:
    source = _source(ROOT / "schemas" / "trace.py")
    tree = ast.parse(source)
    trace_classes = {
        node.name: node
        for node in tree.body
        if isinstance(node, ast.ClassDef)
        and node.name in {"TraceCorrelationSchema", "RunTraceEventSchema"}
    }
    declared_names: set[str] = set()
    for node in trace_classes.values():
        for child in node.body:
            if isinstance(child, ast.AnnAssign) and isinstance(child.target, ast.Name):
                declared_names.add(child.target.id)
    assert "semantic_fingerprint" not in declared_names


def test_target_production_does_not_consume_legacy_runtime_trace_as_authority() -> None:
    joined = "\n".join(_source(path) for path in TARGET_PRODUCTION_SURFACES)
    assert "RuntimeTraceSchema" not in joined
    assert "runtime.agent_rag_v1.workflow_orchestrator" not in joined
    for control_field in (
        "trace.recovery_performed",
        "trace.writer_revisions",
        "trace.router_actions",
        "trace.attempt_records",
        "trace.research_rounds",
        "trace.verifier_rounds",
        "trace.retrieval_tool_calls",
    ):
        assert control_field not in joined
