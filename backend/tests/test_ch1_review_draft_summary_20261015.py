"""Check review reference survives the existing continuation/public projection."""
from __future__ import annotations

from types import SimpleNamespace

from application.mainline_service import MainlineApplicationService
from schemas.agent import AgentResultSchema
from schemas.status import ExecutionStatus


def test_continuation_summary_exposes_review_without_public_release():
    review = {
        "output_format": "markdown",
        "quality_state": "FAIL",
        "formal_quality_eligible": False,
        "formal_release_authorized": False,
        "validated_output_ref": {"artifact_ref": "validated-output:test"},
    }
    result = AgentResultSchema(
        result_id="result-run-1",
        task_id="task-1",
        run_id="run-1",
        agent_name="SchemeWriterAgent",
        agent_type="sub_agent",
        status=ExecutionStatus.PARTIAL_SUCCESS,
        result_type="scheme_writer_output",
        result={
            "target_review_draft": review,
            "target_release_publication": None,
            "generated_draft": {"document_id": "draft-1"},
        },
    )
    summary = MainlineApplicationService()._continuation_summary(
        workspace=SimpleNamespace(paths=lambda: {}),
        task_manager=SimpleNamespace(
            get_task=lambda task_id: SimpleNamespace(
                model_dump=lambda mode="json": {"task_id": task_id}
            )
        ),
        result=result,
    )
    assert summary["target_review_draft"] == review
    assert summary["target_release_publication"] is None
    assert summary["generated_draft"] == {"document_id": "draft-1"}
    # The public status-only projection still does not expose Artifact refs.
    public = MainlineApplicationService.public_result(summary)
    assert "target_review_draft" not in public
    assert "terminal_outcome" in public
