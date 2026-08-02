"""Retirement acceptance for the former Standalone scenario suite."""

from __future__ import annotations

from pathlib import Path


MATRIX = {
    "success": "test_production_artifact_lifecycle_and_capture_are_exactly_once",
    "research-more": "test_verifier_research_more_drives_real_section_route",
    "rewrite": "test_mainline_rewrite_scenario_creates_new_generation_attempt",
    "paragraph-repair": "test_mainline_paragraph_repair_replaces_conflicting_version",
    "replan": "test_mainline_replan_supersedes_attempt_and_rejects_old_result",
    "partial-success": "test_mainline_partial_success_publishes_deliverable",
    "failed": "test_explicit_insufficient_evidence_stops_before_writer_and_verifier",
    "budget-exhausted": "test_task_budget_draining_finishes_first_section_and_starts_no_more_agents",
    "human-review": "test_stale_human_review_decision_keeps_durable_interrupt_paused",
    "crash-recovery": "test_section_crash_resume_reuses_committed_paragraph_without_duplication",
}


def test_every_standalone_scenario_has_a_mainline_e2e_owner() -> None:
    root = Path(__file__).resolve().parents[1]
    sources = "\n".join(
        path.read_text(encoding="utf-8")
        for path in (
            root / "tests/test_phase2_top_level_business_graph.py",
            root / "tests/test_phase4_section_agents_mainline.py",
            root / "tests/test_phase8_recovery_budget_retirement.py",
        )
    )
    assert set(MATRIX) == {
        "success",
        "research-more",
        "rewrite",
        "paragraph-repair",
        "replan",
        "partial-success",
        "failed",
        "budget-exhausted",
        "human-review",
        "crash-recovery",
    }
    for test_name in MATRIX.values():
        assert f"def {test_name}" in sources


def test_retirement_document_matches_the_executable_matrix() -> None:
    root = Path(__file__).resolve().parents[2]
    document = (
        root / "docs/AGENT_RAG_STANDALONE_RETIREMENT_MATRIX_20260801.md"
    ).read_text(encoding="utf-8")
    for scenario, test_name in MATRIX.items():
        assert scenario in document
        assert test_name in document


def test_archived_cli_no_longer_imports_standalone_runtime() -> None:
    root = Path(__file__).resolve().parents[2]
    script = (root / "scripts/run_agent_rag_v1_standalone.py").read_text(
        encoding="utf-8"
    )
    assert "retired in Round 8" in script
    assert "run_scenario" not in script
    assert "MultiAgentApplicationService" not in script
    assert "RuntimeFactory" not in script
