"""Frozen A3 route/occurrence counterexamples; no Provider or graph recovery."""

from __future__ import annotations

from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace

import pytest
from langgraph.checkpoint.base import empty_checkpoint

from agent.runtime.graph_state import GraphStateSchema
from agent.runtime.langgraph_state import LangGraphRuntimeStateCodec
from agent.runtime.official_checkpoint_saver import create_sqlite_checkpoint_resource
from agent.runtime.workflow_schema import WorkflowDefinitionSchema
from apps.enterprise_document.schemas.scheme_generation_graph import (
    SchemeGenerationGraphPayloadSchema,
    SchemeGenerationGraphStateCodec,
)
from apps.enterprise_document.workflows.top_level_business_graph import (
    EnterpriseDocumentWorkflowEngine,
)
from schemas.common import ErrorSchema
from schemas.context import ContextBundleSchema, TaskContextSchema, UserContextSchema


TASK = "task-A3-v2.1-focused"
RUN = "run-A3-v2.1-focused"
NOW = "2026-10-07T00:00:00+00:00"
WORKFLOW = WorkflowDefinitionSchema(
    workflow_id="A3-v2.1-focused", workflow_name="A3 focused",
    task_type="scheme_generation", workflow_version="1", steps=[], created_at=NOW,
)
NS = "A3-v2.1-focused:1"


class _Stage:
    def __init__(self, target: bool):
        self.target_post_write_composition = object() if target else None

    def __getattr__(self, _name):
        return lambda *_args, **_kwargs: None


def _config(checkpoint_id: str, *, run: str = RUN, namespace: str = NS):
    return {"configurable": {"thread_id": run, "checkpoint_ns": namespace,
                              "checkpoint_id": checkpoint_id}}


def checkpoint_row(
    checkpoint_id: str, node: str, route: str | None, parent_id: str | None,
    revision: int, *, failed: str | None = None, task: str = TASK,
    run: str = RUN, namespace: str = NS,
):
    graph = GraphStateSchema(
        task_id=task, run_id=run, task_type="scheme_generation", user_input="A3",
        context_bundle=ContextBundleSchema(
            user=UserContextSchema(user_query="A3"),
            task=TaskContextSchema(task_id=task, run_id=run, task_type="scheme_generation"),
        ), created_at=NOW, graph_revision=revision, current_node_id=node,
    )
    error = ErrorSchema(
        error_code="A3_TEST_STAGE_FAILURE", error_type="RuntimeError",
        message="A3 focused failure", recoverable=True, failed_node=failed,
        created_at=NOW,
    ) if failed else None
    outputs = [{"node_id": "normalize_project_input", "status": "success"}
               for _ in range(max(0, revision - 1))]
    if revision:
        outputs.append({"node_id": node, "status": "failed" if failed == node else "success",
                        "error": error.model_dump(mode="json") if failed == node else None})
    payload = SchemeGenerationGraphPayloadSchema(
        task_id=task, run_id=run, workflow_namespace=namespace, started_at=NOW,
        business_revision=revision, executed_nodes=[x["node_id"] for x in outputs],
        failed_business_node=failed, terminal_error=error,
    )
    codec = LangGraphRuntimeStateCodec()
    state = codec.build_initial_state(WORKFLOW, graph)
    state.update(SchemeGenerationGraphStateCodec().build_initial_update(payload))
    state.update(current_business_node=node, business_route=route,
                 transition_count=revision, node_outputs=outputs)
    state["workflow_namespace"] = namespace
    return SimpleNamespace(
        config=_config(checkpoint_id, run=run, namespace=namespace),
        checkpoint={"channel_values": state},
        parent_config=_config(parent_id, run=run, namespace=namespace) if parent_id else None,
        metadata={"source": "loop", "step": revision}, pending_writes=[],
    )


class _Rows:
    def __init__(self, rows, latest):
        self.rows = {row.config["configurable"]["checkpoint_id"]: row for row in rows}
        self.latest = latest
        self.history_reads = 0

    def get_tuple(self, config):
        identity = config.get("configurable", {})
        return self.rows.get(identity.get("checkpoint_id") or self.latest)

    def list(self, _config):
        self.history_reads += 1
        return iter(reversed(list(self.rows.values())))


def engine_from_rows(rows, latest: str, *, target: bool = True):
    """Native-parent/typed-state fixture reusable by existing method tests."""
    by_id = {row.config["configurable"]["checkpoint_id"]: row for row in rows}
    done = set()

    def preserve_parent_commit_prefix(row, visiting):
        checkpoint_id = row.config["configurable"]["checkpoint_id"]
        if checkpoint_id in done or checkpoint_id in visiting:
            return
        visiting = {*visiting, checkpoint_id}
        parent_id = (row.parent_config or {}).get("configurable", {}).get("checkpoint_id")
        parent = by_id.get(parent_id)
        if parent is not None:
            preserve_parent_commit_prefix(parent, visiting)
            values = row.checkpoint["channel_values"]
            parent_values = parent.checkpoint["channel_values"]
            if values["transition_count"] == parent_values["transition_count"] + 1:
                values["node_outputs"] = [*deepcopy(parent_values["node_outputs"]),
                                          values["node_outputs"][-1]]
                values["scheme_generation_payload"]["executed_nodes"] = [
                    output["node_id"] for output in values["node_outputs"]]
        done.add(checkpoint_id)

    for row in rows:
        preserve_parent_commit_prefix(row, set())
    engine = object.__new__(EnterpriseDocumentWorkflowEngine)
    engine.stage_service = _Stage(target)
    engine.runtime_state_codec = LangGraphRuntimeStateCodec()
    engine.business_state_codec = SchemeGenerationGraphStateCodec()
    engine.checkpointer = _Rows(rows, latest)
    engine._langgraph_checkpointer = None
    engine.inspect = lambda _workflow, run_id: {
        "interrupts": [], "checkpoint_config": _config(latest),
        "graph_state": {"task_id": TASK, "run_id": run_id}, "next_nodes": [],
    }
    return engine, WORKFLOW


def _plan(rows, latest="F", *, target=True):
    engine, workflow = engine_from_rows(rows, latest, target=target)
    return engine.recovery_plan(workflow, run_id=RUN)


def test_ce001_missing_deliverable_persistence_rejects_actual_gate():
    rows = [checkpoint_row("G", "evaluate_document_gate", "deliverable", None, 1),
            checkpoint_row("F", "finalize_workflow_result", "technical_failure", "MISSING-P", 3,
                           failed="finalize_workflow_result")]
    outputs = [deepcopy(rows[0].checkpoint["channel_values"]["node_outputs"][-1]),
               {"node_id": "persist_deliverable", "status": "success", "error": None},
               rows[1].checkpoint["channel_values"]["node_outputs"][-1]]
    rows[1].checkpoint["channel_values"]["node_outputs"] = outputs
    rows[1].checkpoint["channel_values"]["scheme_generation_payload"]["executed_nodes"] = [
        output["node_id"] for output in outputs]
    with pytest.raises(ValueError):
        _plan(rows)


def test_ce002_failure_finalizer_uses_exact_retrieval_technical_parent():
    rows = [checkpoint_row("P", "retrieve_document_evidence", "technical_failure", None, 1),
            checkpoint_row("F", "finalize_workflow_failure", "technical_failure", "P", 2,
                           failed="finalize_workflow_failure")]
    plan = _plan(rows)
    assert plan["predecessor"] == "retrieve_document_evidence"
    assert plan["checkpoint_config"]["configurable"]["checkpoint_id"] == "P"


@pytest.mark.parametrize("target,source,raw,destination", [
    (True, "final_artifact_review", "continue", "evaluate_document_gate"),
    (False, "final_artifact_review", "continue", "decide_document_version"),
    (False, "decide_document_version", "continue", "evaluate_document_gate"),
    (False, "evaluate_document_gate", "continue", "build_generation_report"),
    (False, "evaluate_document_gate", "deliverable", "build_generation_report"),
    (False, "evaluate_document_gate", "rejected", "build_generation_report"),
    (False, "evaluate_document_gate", "blocked", "build_generation_report"),
    (True, "initialize_generation", "continue", "execute_sections"),
    (False, "initialize_generation", "continue", "execute_sections"),
    (True, "evaluate_document_gate", "deliverable", "persist_deliverable"),
    (True, "evaluate_document_gate", "rejected", "persist_rejected"),
    (True, "evaluate_document_gate", "blocked", "finalize_workflow_result"),
    (True, "evaluate_document_gate", "technical_failure", "finalize_workflow_failure"),
    (False, "build_generation_report", "deliverable", "persist_deliverable"),
    (False, "build_generation_report", "rejected", "persist_rejected"),
    (False, "build_generation_report", "blocked", "finalize_workflow_result"),
    (False, "build_generation_report", "technical_failure", "finalize_workflow_failure"),
    (True, "persist_deliverable", "continue", "finalize_workflow_result"),
    (True, "persist_rejected", "continue", "finalize_workflow_result"),
    (True, "persist_deliverable", "technical_failure", "finalize_workflow_failure"),
    (True, "persist_rejected", "technical_failure", "finalize_workflow_failure"),
])
def test_exact_legal_native_parent_route(target, source, raw, destination):
    rows = [checkpoint_row("P", source, raw, None, 1),
            checkpoint_row("F", destination, "technical_failure", "P", 2, failed=destination)]
    assert _plan(rows, target=target)["predecessor"] == source


@pytest.mark.parametrize("field,value", [("task_id", "FOREIGN"), ("run_id", "FOREIGN"),
                                         ("workflow_namespace", "FOREIGN")])
def test_wrong_parent_identity_rejects(field, value):
    parent = checkpoint_row("P", "initialize_generation", "continue", None, 1)
    parent.checkpoint["channel_values"][field] = value
    failure = checkpoint_row("F", "execute_sections", "technical_failure", "P", 2,
                             failed="execute_sections")
    with pytest.raises(ValueError):
        _plan([parent, failure])


def test_wrong_parent_config_namespace_rejects_before_rebinding():
    parent = checkpoint_row("P", "initialize_generation", "continue", None, 1)
    parent.config["configurable"]["checkpoint_ns"] = "FOREIGN"
    failure = checkpoint_row("F", "execute_sections", "technical_failure", "P", 2,
                             failed="execute_sections")
    with pytest.raises(ValueError):
        _plan([parent, failure])


def test_broken_parent_never_searches_older_allowed_history():
    older = checkpoint_row("OLD", "initialize_generation", "continue", None, 1)
    failure = checkpoint_row("F", "execute_sections", "technical_failure", "MISSING", 2,
                             failed="execute_sections")
    with pytest.raises(ValueError):
        _plan([older, failure])


def test_nearest_wrong_branch_stops_before_older_legal_owner():
    older = checkpoint_row("OLD", "persist_deliverable", "continue", None, 1)
    gate = checkpoint_row("G", "evaluate_document_gate", "deliverable", "OLD", 2)
    failure = checkpoint_row("F", "finalize_workflow_result", "technical_failure", "G", 3,
                             failed="finalize_workflow_result")
    with pytest.raises(ValueError):
        _plan([older, gate, failure])


def test_same_business_commit_physical_copy_is_not_previous_occurrence():
    parent = checkpoint_row("P", "initialize_generation", "continue", None, 1)
    failure = checkpoint_row("F", "execute_sections", "technical_failure", "P", 2,
                             failed="execute_sections")
    engine_from_rows([parent, failure], "F")
    copy = deepcopy(failure)
    copy.config = _config("COPY")
    copy.parent_config = _config("F")
    copy.metadata = {"source": "update", "step": 3}
    assert _plan([parent, failure, copy], "COPY")["predecessor"] == "initialize_generation"


def test_distinct_same_node_commit_is_not_skipped_by_name():
    parent = checkpoint_row("P", "initialize_generation", "continue", None, 1)
    failure = checkpoint_row("F", "execute_sections", "technical_failure", "P", 2,
                             failed="execute_sections")
    repeated = checkpoint_row("R", "execute_sections", "technical_failure", "F", 3,
                              failed="execute_sections")
    with pytest.raises(ValueError):
        _plan([parent, failure, repeated], "R")


def test_same_named_older_branch_is_not_selected_over_exact_current_parent():
    old_parent = checkpoint_row("OLD-P", "initialize_generation", "continue", None, 1)
    old_failure = checkpoint_row("OLD-F", "execute_sections", "technical_failure", "OLD-P", 2,
                                 failed="execute_sections")
    current_parent = checkpoint_row("CURRENT-P", "initialize_generation", "continue", None, 1)
    current_failure = checkpoint_row("CURRENT-F", "execute_sections", "technical_failure", "CURRENT-P", 2,
                                     failed="execute_sections")
    plan = _plan([current_parent, current_failure, old_parent, old_failure], "CURRENT-F")
    assert plan["checkpoint_config"]["configurable"]["checkpoint_id"] == "CURRENT-P"


def test_prior_occurrence_physical_copy_selects_original_business_commit(tmp_path):
    parent = checkpoint_row("01-business", "initialize_generation", "continue", None, 1)
    copy = deepcopy(parent)
    copy.config = _config("02-physical-copy")
    copy.parent_config = parent.config
    copy.metadata = {"source": "update", "step": 2}
    failure = checkpoint_row("03-failed", "execute_sections", "technical_failure", "02-physical-copy", 2,
                             failed="execute_sections")
    rows = [parent, copy, failure]
    engine, workflow = engine_from_rows(rows, "03-failed")
    resource = create_sqlite_checkpoint_resource(tmp_path / "prior-copy.sqlite3")
    try:
        for row in rows:
            checkpoint = empty_checkpoint()
            checkpoint["id"] = row.config["configurable"]["checkpoint_id"]
            checkpoint["channel_values"] = row.checkpoint["channel_values"]
            resource.saver.put(row.parent_config or {"configurable": {"thread_id": RUN, "checkpoint_ns": NS}},
                               checkpoint, row.metadata, {})
        engine.checkpointer = resource.saver
        plan = engine.recovery_plan(workflow, run_id=RUN)
        assert plan["checkpoint_config"]["configurable"]["checkpoint_id"] == "01-business"
    finally:
        resource.close()


def test_parent_cycle_rejects_without_history_fallback():
    parent = checkpoint_row("P", "initialize_generation", "continue", "P", 1)
    failure = checkpoint_row("F", "execute_sections", "technical_failure", "F", 2,
                             failed="execute_sections")
    with pytest.raises(ValueError):
        _plan([parent, failure])


def test_parent_walk_bound_rejects(monkeypatch):
    import apps.enterprise_document.workflows.top_level_business_graph as graph_module
    monkeypatch.setattr(graph_module, "_RECOVERY_PARENT_CHAIN_LIMIT", 1, raising=False)
    rows = [checkpoint_row("P", "initialize_generation", "continue", None, 1),
            checkpoint_row("F", "execute_sections", "technical_failure", "P", 2,
                           failed="execute_sections")]
    with pytest.raises(ValueError):
        _plan(rows)


def test_recovery_reads_exact_parents_not_history_or_node_only_map():
    rows = [checkpoint_row("P", "initialize_generation", "continue", None, 1),
            checkpoint_row("F", "execute_sections", "technical_failure", "P", 2,
                           failed="execute_sections")]
    engine, workflow = engine_from_rows(rows, "F")
    engine.checkpoint_history = lambda *_a, **_k: pytest.fail("whole history scan")
    engine._recovery_predecessors = lambda *_a, **_k: pytest.fail("node-only authority")
    assert engine.recovery_plan(workflow, run_id=RUN)["predecessor"] == "initialize_generation"


@pytest.mark.parametrize("target", [True, False])
def test_full_topology_matches_compiled_conditional_and_terminal_edges(target):
    engine, workflow = engine_from_rows([], "NONE", target=target)
    edges = engine._active_route_edges()
    conditional = {(e.source, e.route, e.destination) for e in edges if e.router_kind is not None}
    unconditional = {(e.source, e.destination) for e in edges if e.router_kind is None}
    spine = ["normalize_project_input", "plan_document", "retrieve_document_evidence",
             "initialize_generation", "execute_sections", "document_verify",
             "repair_document_conflicts", "incremental_document_reverify", "assemble_document",
             "validate_document_assembly", "final_artifact_review"]
    spine.extend(["evaluate_document_gate"] if target else
                 ["decide_document_version", "evaluate_document_gate", "build_generation_report"])
    expected = {(source, "continue", destination) for source, destination in zip(spine, spine[1:])}
    expected.update((source, "technical_failure", "finalize_workflow_failure") for source in spine[:-1])
    expected.update((spine[-1], route, destination) for route, destination in (
        ("deliverable", "persist_deliverable"), ("rejected", "persist_rejected"),
        ("blocked", "finalize_workflow_result"), ("technical_failure", "finalize_workflow_failure")))
    expected.update((source, route, destination) for source in ("persist_deliverable", "persist_rejected")
                    for route, destination in (("continue", "finalize_workflow_result"),
                                               ("technical_failure", "finalize_workflow_failure")))
    assert conditional == expected
    assert unconditional == {("__start__", "normalize_project_input"),
                              ("finalize_workflow_result", "__end__"),
                              ("finalize_workflow_failure", "__end__")}
    actual = engine.build_graph(workflow).get_graph().edges
    assert conditional == {(e.source, e.data, e.target) for e in actual if e.conditional}
    assert unconditional == {(e.source, e.target) for e in actual if not e.conditional}
    assert len(conditional) == (30 if target else 34)
    assert sum(e.destination == "finalize_workflow_failure" for e in edges) == (14 if target else 16)
    assert len({e.router_kind for e in edges if e.source == "evaluate_document_gate"}) == 1


@pytest.mark.parametrize("target", [True, False])
@pytest.mark.parametrize("raw", [None, "continue", "deliverable", "rejected", "blocked",
                                "technical_failure", "UNKNOWN"])
def test_same_raw_effective_route_uses_unique_active_router_kind(target, raw):
    engine, _ = engine_from_rows([], "NONE", target=target)
    expected = (raw if raw in ("deliverable", "rejected", "blocked", "technical_failure")
                else "technical_failure") if target else (
                    "technical_failure" if raw == "technical_failure" else "continue")
    assert engine._effective_route_label("evaluate_document_gate", {"business_route": raw}) == expected


@pytest.mark.parametrize("target,source,route,destination", [
    (True, "initialize_generation", "continue", "execute_sections"),
    (False, "evaluate_document_gate", "deliverable", "build_generation_report"),
    (False, "evaluate_document_gate", "rejected", "build_generation_report"),
    (False, "evaluate_document_gate", "blocked", "build_generation_report"),
])
def test_native_sqlite_parent_config_proves_focused_occurrence(tmp_path: Path, target, source, route, destination):
    parent = checkpoint_row("01-parent", source, route, None, 1)
    failure = checkpoint_row("02-failed", destination, "technical_failure", "01-parent", 2,
                             failed=destination)
    resource = create_sqlite_checkpoint_resource(tmp_path / "native-focused.sqlite3")
    try:
        engine, workflow = engine_from_rows([parent, failure], "02-failed", target=target)
        for row in (parent, failure):
            checkpoint = empty_checkpoint()
            checkpoint["id"] = row.config["configurable"]["checkpoint_id"]
            checkpoint["channel_values"] = row.checkpoint["channel_values"]
            config = row.parent_config or {"configurable": {"thread_id": RUN, "checkpoint_ns": NS}}
            resource.saver.put(config, checkpoint, row.metadata, {})
        engine.checkpointer = resource.saver
        native = resource.saver.get_tuple(_config("02-failed"))
        assert native.parent_config == _config("01-parent")
        plan = engine.recovery_plan(workflow, run_id=RUN)
        assert plan["predecessor"] == source
        assert plan["checkpoint_config"]["configurable"]["checkpoint_id"] == "01-parent"
        assert plan["checkpoint_config"]["configurable"]["workflow_namespace"] == NS
    finally:
        resource.close()


ORDINARY_SOURCES = (
    "normalize_project_input", "plan_document", "retrieve_document_evidence",
    "initialize_generation", "execute_sections", "document_verify",
    "repair_document_conflicts", "incremental_document_reverify", "assemble_document",
    "validate_document_assembly", "final_artifact_review",
    "persist_deliverable", "persist_rejected",
)


@pytest.mark.parametrize("target", [True, False])
@pytest.mark.parametrize("source", ORDINARY_SOURCES)
def test_every_ordinary_technical_failure_parent_is_accepted(target, source):
    rows = [checkpoint_row("P", source, "technical_failure", None, 1),
            checkpoint_row("F", "finalize_workflow_failure", "technical_failure", "P", 2,
                           failed="finalize_workflow_failure")]
    assert _plan(rows, target=target)["predecessor"] == source


@pytest.mark.parametrize("source", ["decide_document_version", "evaluate_document_gate"])
def test_legacy_extra_stage_technical_failure_is_accepted(source):
    rows = [checkpoint_row("P", source, "technical_failure", None, 1),
            checkpoint_row("F", "finalize_workflow_failure", "technical_failure", "P", 2,
                           failed="finalize_workflow_failure")]
    assert _plan(rows, target=False)["predecessor"] == source


@pytest.mark.parametrize("route,persistence", [("deliverable", "persist_deliverable"),
                                             ("rejected", "persist_rejected")])
def test_intact_persistence_branch_selects_persistence_not_gate(route, persistence):
    rows = [checkpoint_row("G", "evaluate_document_gate", route, None, 1),
            checkpoint_row("P", persistence, "continue", "G", 2),
            checkpoint_row("F", "finalize_workflow_result", "technical_failure", "P", 3,
                           failed="finalize_workflow_result")]
    plan = _plan(rows)
    assert plan["predecessor"] == persistence
    assert plan["checkpoint_config"]["configurable"]["checkpoint_id"] == "P"


def test_rejected_branch_missing_persistence_never_accepts_gate():
    rows = [checkpoint_row("G", "evaluate_document_gate", "rejected", None, 1),
            checkpoint_row("F", "finalize_workflow_result", "technical_failure", "G", 2,
                           failed="finalize_workflow_result")]
    with pytest.raises(ValueError, match="matching effective edge"):
        _plan(rows)


def test_missing_current_failed_occurrence_rejects():
    rows = [checkpoint_row("P", "initialize_generation", "continue", None, 1),
            checkpoint_row("F", "finalize_workflow_failure", "continue", "P", 2,
                           failed="execute_sections")]
    with pytest.raises(ValueError, match="current failed business occurrence"):
        _plan(rows)


def test_normalize_first_failure_has_no_business_predecessor():
    with pytest.raises(ValueError):
        _plan([checkpoint_row("F", "normalize_project_input", "technical_failure", None, 1,
                             failed="normalize_project_input")])


@pytest.mark.parametrize("field,value", [("transition_count", 3), ("node_outputs", []),
                                        ("current_business_node", "STALE")])
def test_current_commit_proof_mismatch_rejects(field, value):
    rows = [checkpoint_row("P", "initialize_generation", "continue", None, 1),
            checkpoint_row("F", "execute_sections", "technical_failure", "P", 2,
                           failed="execute_sections")]
    engine, workflow = engine_from_rows(rows, "F")
    rows[-1].checkpoint["channel_values"][field] = value
    with pytest.raises(ValueError, match="commit proof"):
        engine.recovery_plan(workflow, run_id=RUN)


def test_stale_latest_differs_from_inspected_checkpoint_rejects():
    rows = [checkpoint_row("P", "initialize_generation", "continue", None, 1),
            checkpoint_row("F", "execute_sections", "technical_failure", "P", 2,
                           failed="execute_sections")]
    engine, workflow = engine_from_rows(rows, "P")
    engine.checkpointer.latest = "F"
    with pytest.raises(ValueError, match="changed during inspection"):
        engine.recovery_plan(workflow, run_id=RUN)


@pytest.mark.parametrize("field,value", [("thread_id", "FOREIGN"), ("workflow_namespace", "FOREIGN"),
                                         ("checkpoint_id", "MISSING")])
def test_wrong_parent_link_scope_or_missing_id_rejects(field, value):
    rows = [checkpoint_row("P", "initialize_generation", "continue", None, 1),
            checkpoint_row("F", "execute_sections", "technical_failure", "P", 2,
                           failed="execute_sections")]
    engine, workflow = engine_from_rows(rows, "F")
    rows[-1].parent_config["configurable"][field] = value
    with pytest.raises(ValueError):
        engine.recovery_plan(workflow, run_id=RUN)


@pytest.mark.parametrize("field,value", [("workflow_id", "FOREIGN"), ("workflow_version", "FOREIGN")])
def test_wrong_parent_runtime_workflow_rejects(field, value):
    rows = [checkpoint_row("P", "initialize_generation", "continue", None, 1),
            checkpoint_row("F", "execute_sections", "technical_failure", "P", 2,
                           failed="execute_sections")]
    engine, workflow = engine_from_rows(rows, "F")
    rows[0].checkpoint["channel_values"][field] = value
    with pytest.raises(ValueError):
        engine.recovery_plan(workflow, run_id=RUN)


def test_physical_copy_with_changed_raw_route_rejects():
    rows = [checkpoint_row("P", "initialize_generation", "continue", None, 1),
            checkpoint_row("F", "execute_sections", "technical_failure", "P", 2,
                           failed="execute_sections")]
    engine_from_rows(rows, "F")
    copy = deepcopy(rows[-1])
    copy.config = _config("COPY")
    copy.parent_config = _config("F")
    copy.checkpoint["channel_values"]["business_route"] = "continue"
    with pytest.raises(ValueError, match="physical checkpoint copy"):
        _plan([*rows, copy], "COPY")


def test_valid_unfinished_checkpoint_keeps_existing_recovery_mode():
    rows = [checkpoint_row("P", "initialize_generation", "continue", None, 1)]
    engine, workflow = engine_from_rows(rows, "P")
    original = engine.inspect
    engine.inspect = lambda workflow, run_id: {**original(workflow, run_id), "next_nodes": ["execute_sections"]}
    plan = engine.recovery_plan(workflow, run_id=RUN)
    assert plan["reason"] == "unfinished_top_level_checkpoint"
    assert plan["checkpoint_config"]["configurable"]["checkpoint_id"] == "P"


@pytest.mark.parametrize("raw", [None, "", "continue", "deliverable", "rejected", "blocked",
                                "technical_failure", "UNKNOWN", 0, 1, False, True, [], {},
                                {"route": "deliverable"}])
@pytest.mark.parametrize("target", [True, False])
def test_all_sources_raw_normalization_matches_frozen_runtime(target, raw):
    engine, _ = engine_from_rows([], "NONE", target=target)
    edges = engine._active_route_edges()
    sources = {edge.source for edge in edges if edge.router_kind is not None}
    assert len(sources) == (14 if target else 16)
    for source in sources:
        kind = {edge.router_kind for edge in edges if edge.source == source}.pop()
        state = {"business_route": raw}
        expected = (str(raw or "") if str(raw or "") in
                    ("deliverable", "rejected", "blocked", "technical_failure")
                    else "technical_failure") if kind == "gate" else (
                        "technical_failure" if raw == "technical_failure" else "continue")
        assert engine._effective_route_label(source, state) == expected
