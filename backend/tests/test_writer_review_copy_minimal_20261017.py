"""Isolated Native acceptance for the minimal, local Writer review copy."""

from __future__ import annotations

from pathlib import Path
import time

from langgraph.checkpoint.sqlite import SqliteSaver

from apps.enterprise_document.workflows.top_level_business_graph import (
    phase2_checkpoint_identity,
)
from test_phase2_runtime_safety_closure import _run_production_fake


def test_wc_n01_native_sqlite_exact_predecessor_visible_at_final_review_entry(tmp_path: Path):
    """Observe the real pre-stage entry without changing durability or retrying reads."""
    observations = []

    def configure(engine):
        original = engine._run_stage

        def observe(runtime_state, *, workflow, node_name, stage):
            if node_name == "final_artifact_review":
                graph, payload = engine._restore(runtime_state, workflow)
                row = engine.checkpointer.get_tuple(
                    phase2_checkpoint_identity(workflow, run_id=graph.run_id)
                )
                observations.append({
                    "row": row,
                    "graph": graph.model_copy(deep=True),
                    "payload": payload.model_copy(deep=True),
                    "workflow": workflow,
                    "observed_ns": time.monotonic_ns(),
                })
            return original(runtime_state, workflow=workflow, node_name=node_name, stage=stage)

        engine._run_stage = observe

    summary, factory = _run_production_fake(
        tmp_path / "native-runtime", run_id="writer-copy-native-c1-20261017", mutate_engine=configure
    )
    assert len(observations) == 1, summary
    observed = observations[0]
    row = observed["row"]
    assert row is not None
    engine = factory.engines[0]
    saver = engine.checkpointer
    assert isinstance(getattr(saver, "delegate", saver), SqliteSaver)
    values = row.checkpoint["channel_values"]
    assert values["current_business_node"] == "validate_document_assembly", values["current_business_node"]
    assert values["business_route"] == "continue"
    output = values["node_outputs"][-1]
    assert output["node_id"] == "validate_document_assembly"
    assert output["status"] == output["result"]["status"] == "success"
    committed_values, committed_payload, count, _ = engine._recovery_checkpoint_values(
        observed["workflow"], row, task_id=observed["graph"].task_id, run_id=observed["graph"].run_id
    )
    assert committed_payload.assembly_validation.passed is True
    assert committed_payload.model_dump(mode="json") == observed["payload"].model_dump(mode="json")
    assert count == committed_payload.business_revision
    pinned_id = row.config["configurable"]["checkpoint_id"]
    assert pinned_id
    pinned = saver.get_tuple(row.config)
    assert pinned.checkpoint == row.checkpoint
    assert committed_values == values
