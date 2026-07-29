from __future__ import annotations

import json
from pathlib import Path

import pytest

from apps.enterprise_document.runtime.agent_rag_v1 import (
    MultiAgentApplicationService,
    MultiAgentProjectInputSchema,
)


pytestmark = pytest.mark.standalone_e2e


def _run(tmp_path: Path, scenario: str):
    return MultiAgentApplicationService().run(
        MultiAgentProjectInputSchema(
            scenario=scenario,
            output_dir=str(tmp_path),
            task_id=f"e2e-{scenario}",
            run_id="run-001",
        )
    )


def _assert_published(result) -> None:
    assert Path(result.document_artifact_path).is_file()
    assert Path(result.generation_report_path).is_file()
    assert Path(result.checkpoint_path).is_file()
    assert Path(result.event_log_path).is_file()
    assert result.final_state.final_result.published is True
    assert result.assembly_validation_passed is True
    assert result.final_artifact_review_passed is True
    assert result.markdown_only is True


def test_success_schedules_four_agents_and_closes_document(
    tmp_path: Path,
) -> None:
    result = _run(tmp_path, "success")

    assert result.task_status.value == "success"
    assert [item.status.value for item in result.section_results] == [
        "accepted",
        "accepted",
    ]
    assert {
        invocation.split(":", 1)[0]
        for invocation in result.trace.agent_invocations
    } == {"planner", "research", "writer", "verifier"}
    assert result.trace.section_batches == [["compute", "storage"]]
    assert result.trace.state_commit_count >= 20
    _assert_published(result)


def test_research_more_routes_second_retrieval_round(tmp_path: Path) -> None:
    result = _run(tmp_path, "research-more")

    assert result.task_status.value == "success"
    assert result.trace.research_rounds["compute"] == 2
    assert "research_more" in result.trace.router_actions
    _assert_published(result)


def test_rewrite_creates_second_draft_and_accepts(tmp_path: Path) -> None:
    result = _run(tmp_path, "rewrite")

    assert result.task_status.value == "success"
    assert result.trace.writer_revisions["compute"] == 2
    assert result.trace.verifier_rounds["compute"] == 2
    assert "rewrite" in result.trace.router_actions
    assert (
        result.final_state.section_states["compute"].writing.draft_version == 2
    )
    _assert_published(result)


def test_paragraph_hard_fact_is_repaired_locally(tmp_path: Path) -> None:
    result = _run(tmp_path, "paragraph-repair")
    events = [
        json.loads(line)
        for line in Path(result.writer_event_log_path)
        .read_text(encoding="utf-8")
        .splitlines()
    ]
    compute_p001_versions = [
        event["paragraph"]["paragraph_version"]
        for event in events
        if event.get("section_id") == "compute"
        and event.get("paragraph", {}).get("paragraph_id") == "p001"
    ]

    assert result.task_status.value == "success"
    assert compute_p001_versions == [1, 2]
    assert result.trace.writer_revisions["compute"] == 2
    assert "rewrite" in result.trace.router_actions
    _assert_published(result)


def test_replan_creates_revision_attempt_and_rejects_stale_write(
    tmp_path: Path,
) -> None:
    result = _run(tmp_path, "replan")
    compute_attempts = [
        record
        for record in result.trace.attempt_records
        if record.section_id == "compute"
    ]

    assert result.task_status.value == "success"
    assert len(result.trace.plan_revisions) == 2
    assert [record.plan_revision for record in compute_attempts] == [1, 2]
    assert [record.status for record in compute_attempts] == [
        "superseded",
        "completed",
    ]
    assert result.trace.stale_write_rejected is True
    _assert_published(result)


@pytest.mark.parametrize(
    (
        "scenario",
        "expected_status",
        "section_statuses",
        "expected_router_actions",
    ),
    [
        (
            "partial-success",
            "partial_success",
            ["partially_accepted", "failed"],
            {"partial_accept", "reject"},
        ),
        ("failed", "failed", ["failed", "failed"], {"reject"}),
        (
            "human-review",
            "partial_success",
            ["accepted", "waiting_human"],
            {"accept", "wait_section_human"},
        ),
    ],
)
def test_degraded_scenarios_export_real_artifacts(
    tmp_path: Path,
    scenario: str,
    expected_status: str,
    section_statuses: list[str],
    expected_router_actions: set[str],
) -> None:
    result = _run(tmp_path, scenario)
    markdown = Path(result.document_artifact_path).read_text(encoding="utf-8")

    assert result.task_status.value == expected_status
    assert [item.status.value for item in result.section_results] == (
        section_statuses
    )
    assert expected_router_actions <= set(result.trace.router_actions)
    assert "生成报告附录" in markdown
    assert "原因见生成报告" in markdown
    _assert_published(result)


def test_budget_exhaustion_drains_without_starting_storage_agent(
    tmp_path: Path,
) -> None:
    result = _run(tmp_path, "budget-exhausted")

    assert result.task_status.value == "partial_success"
    assert result.final_state.runtime_control.draining is True
    assert "start_draining" in result.trace.router_actions
    assert not any(
        ":storage:" in invocation
        for invocation in result.trace.agent_invocations
    )
    assert result.final_state.section_states["storage"].execution.status.value == (
        "failed"
    )
    _assert_published(result)
