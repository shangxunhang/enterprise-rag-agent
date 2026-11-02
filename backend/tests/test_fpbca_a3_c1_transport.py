"""C1 acknowledgement compatibility preserves exact A3 occurrence authority."""
from __future__ import annotations

from copy import deepcopy

import pytest

from backend.tests.test_fpbca_a3_v2_1_recovery import (
    RUN, _config, checkpoint_row, engine_from_rows,
)


def _transport():
    # An input dictionary is deliberately not a native lifecycle or retry proof.
    return {"event_ref": "forged-or-real-transport-is-not-authority",
            "event_sha256": "f" * 64, "operation_key": "untrusted-transport-key",
            "resulting_run": {"run_id": RUN, "status": "running"},
            "resulting_attempts": [{"attempt_id": "invented", "status": "running"}]}


def _chain():
    predecessor = checkpoint_row("P", "initialize_generation", "continue", None, 1)
    failure = checkpoint_row("F", "execute_sections", "technical_failure", "P", 2,
                             failed="execute_sections")
    terminal = checkpoint_row("T", "finalize_workflow_failure", "technical_failure", "F", 3,
                              failed="execute_sections")
    failure_values = failure.checkpoint["channel_values"]
    primary = failure_values["scheme_generation_payload"]["terminal_error"]
    primary["details"] = {"c1_triggering_attempt_id": "exact-trigger-attempt",
                          "business_detail": "must-remain-exact"}
    failure_values["node_outputs"][-1]["error"] = deepcopy(primary)
    terminal_primary = deepcopy(primary)
    terminal_primary["details"]["lifecycle_composite_transition"] = _transport()
    terminal.checkpoint["channel_values"]["scheme_generation_payload"]["terminal_error"] = terminal_primary
    return [predecessor, failure, terminal]


def _plan(rows, latest="T"):
    engine, workflow = engine_from_rows(rows, latest)
    return engine.recovery_plan(workflow, run_id=RUN)


def test_later_c1_transport_preserves_exact_primary_occurrence_and_predecessor():
    rows = _chain()
    engine, workflow = engine_from_rows(rows, "T")
    before = deepcopy([row.checkpoint for row in rows])
    plan = engine.recovery_plan(workflow, run_id=RUN)
    assert plan["predecessor"] == "initialize_generation"
    assert plan["checkpoint_config"]["configurable"]["checkpoint_id"] == "P"
    assert plan["failed_business_node"] == "execute_sections"
    assert "starts_new_call" not in plan and "retry_authorized" not in plan
    assert [row.checkpoint for row in rows] == before


@pytest.mark.parametrize("change", ["message", "error_code", "error_type", "failure_category",
                                    "trigger", "other_details"])
def test_transport_does_not_mask_changed_primary_error_or_trigger(change):
    rows = _chain()
    error = rows[-1].checkpoint["channel_values"]["scheme_generation_payload"]["terminal_error"]
    if change == "message":
        error["message"] = error["error_message"] = "a different primary failure"
    elif change == "trigger":
        error["details"]["c1_triggering_attempt_id"] = "different-attempt"
    elif change == "other_details":
        error["details"]["business_detail"] = "changed"
    else:
        error[change] = {"error_code": "OTHER_PRIMARY_CODE", "error_type": "OtherError",
                         "failure_category": "timeout"}[change]
    with pytest.raises(ValueError, match="current failed business occurrence cannot be proven"):
        _plan(rows)


@pytest.mark.parametrize("missing_proof", ["failed_output", "parent", "identity", "active_edge"])
def test_fake_c1_transport_cannot_supply_missing_native_occurrence_proof(missing_proof):
    rows = _chain()
    predecessor, failure, _terminal = rows
    if missing_proof == "failed_output":
        failure.checkpoint["channel_values"]["node_outputs"][-1]["error"] = None
    elif missing_proof == "parent":
        failure.parent_config = _config("MISSING")
    elif missing_proof == "identity":
        failure.config["configurable"]["checkpoint_ns"] = "FOREIGN"
    else:
        predecessor.checkpoint["channel_values"]["business_route"] = "technical_failure"
    with pytest.raises(ValueError):
        _plan(rows)


def test_same_count_physical_copy_transport_drift_keeps_full_signature_rejection():
    rows = _chain()
    engine_from_rows(rows, "T")
    physical = deepcopy(rows[-1])
    physical.config = _config("COPY")
    physical.parent_config = _config("T")
    physical.metadata = {"source": "update", "step": 4}
    details = physical.checkpoint["channel_values"]["scheme_generation_payload"]["terminal_error"]["details"]
    details["lifecycle_composite_transition"]["event_sha256"] = "e" * 64
    with pytest.raises(ValueError, match="physical checkpoint copy changes business occurrence proof"):
        _plan([*rows, physical], "COPY")


def test_primary_comparison_is_nonmutating_and_excludes_only_one_nested_key():
    rows = _chain()
    engine, _workflow = engine_from_rows(rows, "T")
    error = rows[-1].checkpoint["channel_values"]["scheme_generation_payload"]["terminal_error"]
    before = deepcopy(error)
    primary = engine._recovery_primary_error(error)
    expected = deepcopy(error)
    expected["details"].pop("lifecycle_composite_transition")
    assert primary == expected and error == before
    assert primary["details"]["c1_triggering_attempt_id"] == "exact-trigger-attempt"
    assert primary["details"]["business_detail"] == "must-remain-exact"
    for malformed in (None, "error", {"details": "not-a-mapping"}):
        assert engine._recovery_primary_error(malformed) == malformed
