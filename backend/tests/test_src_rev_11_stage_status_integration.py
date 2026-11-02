"""SRCREV-11: one completed Target outcome drives both StageService result views."""

from __future__ import annotations

from hashlib import sha256
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from application.mainline_service import MainlineApplicationService
from application.terminal_outcome_projector import project_terminal
from apps.enterprise_document.schemas.scheme_generation_graph import SchemeGenerationGraphPayloadSchema
from apps.enterprise_document.services.post_write.release import PostWriteReleasePublicationService
from model_gateway.fake_llm_client import FakeLLMClient
from test_multi_agent_v3_final_outcome_transport import actual_path
from test_multi_agent_v3_workflow_recovery import _latest_w13_top_level_payload
from system_test.mainline_closure import run_fake_mainline_scenario
from schemas.common import ErrorSchema
from schemas.failure import FailureCategory


@pytest.mark.parametrize(
    "actual_path",
    ["success", "validation_incomplete"],
    indirect=True,
)
def test_target_extra_and_metadata_use_same_outcome_axes(actual_path) -> None:
    scenario, _, summary, engine, _, _ = actual_path
    top = _latest_w13_top_level_payload(engine)
    outcome = top.terminal_outcome
    output = top.scheme_writer_output
    result = top.terminal_agent_result
    assert outcome is not None
    assert output is not None
    assert result is not None
    assert engine.stage_service.target_post_write_composition is not None

    expected = {
        "success": {
            "execution_status": "success",
            "business_quality_status": "passed",
            "control_action": "OUTPUT",
            "gate_enforcement_status": "continued",
            "release_status": "published",
        },
        "validation_incomplete": {
            "execution_status": "partial_success",
            "business_quality_status": "unresolved",
            "control_action": "OUTPUT_BEST_EFFORT",
            "gate_enforcement_status": "continued",
            "release_status": "published",
        },
    }[scenario]
    expected_version = "terminal-status-view-v1"
    assert outcome.model_dump(mode="json") == result.result["terminal_outcome"]
    assert outcome.model_dump(mode="json") == summary["terminal_outcome"]
    assert result.status.value == expected["execution_status"]
    assert output.status.value == expected["execution_status"]
    assert summary["status"] == expected["execution_status"]

    for key, value in expected.items():
        assert output.extra[key] == value
        assert result.metadata[key] == value
    assert result.metadata["gate_enforcement_action"] == expected["control_action"]
    assert output.extra["status_projection_version"] == expected_version
    assert result.metadata["status_projection_version"] == expected_version
    assert outcome.publication_exists is True
    assert outcome.publication_ref is not None


@pytest.mark.parametrize("actual_path", ["block"], indirect=True)
def test_target_block_shortcut_keeps_execution_and_control_distinct(actual_path) -> None:
    _, _, summary, engine, _, _ = actual_path
    top = _latest_w13_top_level_payload(engine)
    outcome = top.terminal_outcome
    result = top.terminal_agent_result
    assert outcome is not None
    assert result is not None
    assert top.scheme_writer_output is None
    assert outcome.model_dump(mode="json") == result.result["terminal_outcome"]
    assert outcome.execution_status.value == result.status.value == "success"
    assert outcome.status.value == "BLOCK"
    assert outcome.control_action.value == "BLOCK"
    assert outcome.publication_exists is False
    assert outcome.publication_ref is None
    assert summary["terminal_outcome"] == outcome.model_dump(mode="json")
    assert result.metadata["status_projection_version"] == "terminal-status-view-v1"
    assert result.metadata["execution_status"] == "success"
    assert result.metadata["control_action"] == "BLOCK"
    assert result.metadata["gate_enforcement_action"] == "BLOCK"
    assert result.metadata["gate_enforcement_status"] == "blocked"
    assert result.metadata["release_status"] == "not_published"


def test_strict_quality_fail_best_effort_publication_keeps_axes(tmp_path) -> None:
    summary = run_fake_mainline_scenario(
        tmp_path / "quality_fail_best_effort",
        run_id="src11_quality_fail_best_effort",
        rag_scenario="business_gate_failure",
        llm_scenario="force_business_gate_failure",
        enable_corrective_section_retrieval=True,
    )
    outcome = summary["terminal_outcome"]
    extra = summary["scheme_writer_output"]["extra"]
    expected = {
        "execution_status": "partial_success",
        "business_quality_status": "failed",
        "control_action": "OUTPUT_BEST_EFFORT",
        "gate_enforcement_status": "continued",
        "release_status": "published",
    }
    assert summary["status"] == "partial_success"
    assert outcome["quality_state"] == "FAIL"
    assert outcome["control_action"] == "OUTPUT_BEST_EFFORT"
    assert outcome["publication_exists"] is True
    assert outcome["publication_ref"] is not None
    for key, value in expected.items():
        assert extra[key] == value
    assert extra["status_projection_version"] == "terminal-status-view-v1"


@pytest.mark.parametrize("actual_path", ["success"], indirect=True)
def test_missing_target_outcome_fails_before_legacy_publisher(actual_path) -> None:
    _, _, _, engine, _, _ = actual_path
    top = _latest_w13_top_level_payload(engine)
    assert top.terminal_outcome is not None
    assert top.target_release_publication is not None
    assert top.target_post_write_control_decision_ref is not None
    assert top.target_run_completion_assessment_ref is not None
    payload = top.model_copy(update={"terminal_outcome": None})
    service = engine.stage_service
    shared_state = SimpleNamespace(task_id=top.task_id, run_id=top.run_id)

    with (
        patch.object(
            service.release_publication_service,
            "publish",
            side_effect=AssertionError("legacy Publisher fallback"),
        ) as publish,
        patch.object(
            service,
            "_persist_output",
            side_effect=AssertionError("missing Target outcome reached persistence"),
        ) as persist,
    ):
        with pytest.raises(ValueError, match="Target status|terminal_outcome"):
            service.persist_deliverable(shared_state, payload)
    publish.assert_not_called()
    persist.assert_not_called()

    error = ErrorSchema(
        error_code="CONTROLLED_LATER_FAILURE",
        error_type="ControlledFailure",
        message="Controlled failure after Target completion",
        failure_category=FailureCategory.VALIDATION,
    )
    for invalid_payload in (
        payload,
        top.model_copy(update={"target_release_publication": None}),
    ):
        invalid_payload = invalid_payload.model_copy(update={
            "terminal_error": error,
            "failed_business_node": "persist_deliverable",
        })
        with (
            patch.object(service.state_writer, "add_error") as add_error,
            patch.object(service.state_writer, "set_scheme_artifacts") as write_artifacts,
        ):
            with pytest.raises(ValueError, match="Target failure"):
                service.finalize_workflow_failure(shared_state, invalid_payload)
        add_error.assert_not_called()
        write_artifacts.assert_not_called()

    with patch.object(service, "target_post_write_composition", None):
        with patch.object(
            service.release_publication_service,
            "publish",
            side_effect=AssertionError("Target provenance entered legacy Publisher"),
        ) as publish:
            with pytest.raises(ValueError, match="Target provenance"):
                service.persist_deliverable(shared_state, top)
        publish.assert_not_called()


@pytest.mark.parametrize("actual_path", ["success"], indirect=True)
def test_completed_target_failure_cannot_downgrade_when_owner_refs_disappear(actual_path) -> None:
    _, _, _, engine, _, _ = actual_path
    top = _latest_w13_top_level_payload(engine)
    service = engine.stage_service
    assert service.target_post_write_composition is not None
    assert top.terminal_outcome is not None
    assert top.terminal_outcome.control_action is not None
    assert top.target_document_artifact_ref is not None
    assert top.target_post_write_control_decision_ref is not None
    assert top.target_run_completion_assessment_ref is not None
    assert top.target_release_publication is not None
    error = ErrorSchema(
        error_code="CONTROLLED_LATER_FAILURE",
        error_type="ControlledFailure",
        message="Controlled failure after Target completion",
        failure_category=FailureCategory.VALIDATION,
    )
    payload = top.model_copy(update={
        "target_document_artifact_ref": None,
        "target_post_write_control_decision_ref": None,
        "target_run_completion_assessment_ref": None,
        "target_release_publication": None,
        "terminal_error": error,
        "failed_business_node": "persist_deliverable",
    })
    assert payload.terminal_outcome is top.terminal_outcome
    shared_state = SimpleNamespace(task_id=top.task_id, run_id=top.run_id, errors=[])
    with (
        patch.object(service.state_writer, "add_error") as add_error,
        patch.object(service.state_writer, "set_scheme_artifacts") as write_artifacts,
    ):
        with pytest.raises(ValueError, match="Target failure status.*control decision ref"):
            service.finalize_workflow_failure(shared_state, payload)
    add_error.assert_not_called()
    write_artifacts.assert_not_called()


@pytest.mark.parametrize("actual_path", ["success"], indirect=True)
def test_pre_target_failure_keeps_legacy_or_early_status_mode(actual_path) -> None:
    _, _, _, engine, _, _ = actual_path
    service = engine.stage_service
    assert service.target_post_write_composition is not None
    error = ErrorSchema(
        error_code="CONTROLLED_EARLY_FAILURE",
        error_type="ControlledFailure",
        message="Controlled failure before Target completion",
        failure_category=FailureCategory.VALIDATION,
    )
    payload = SchemeGenerationGraphPayloadSchema(
        task_id="src11-early-task",
        run_id="src11-early-run",
        terminal_error=error,
        failed_business_node="prepare_document_plan",
    )
    assert payload.terminal_outcome is None
    assert payload.target_post_write_control_decision_ref is None
    assert payload.target_run_completion_assessment_ref is None
    shared_state = SimpleNamespace(task_id=payload.task_id, run_id=payload.run_id, errors=[])
    with (
        patch(
            "apps.enterprise_document.services.scheme_writer.stage_service.project_terminal",
            wraps=project_terminal,
        ) as early_projection,
        patch.object(service.state_writer, "add_error") as add_error,
        patch.object(service.state_writer, "set_scheme_artifacts") as write_artifacts,
    ):
        result = service.finalize_workflow_failure(shared_state, payload)
    early_projection.assert_called_once()
    add_error.assert_called_once()
    write_artifacts.assert_called_once()
    assert result.terminal_outcome is not None
    assert result.terminal_outcome.control_action is None
    assert result.terminal_agent_result is not None
    assert result.terminal_agent_result.metadata["source_mode"] == "legacy_or_early"
    assert result.terminal_agent_result.metadata["release_status"] is None


@pytest.mark.parametrize("actual_path", ["success"], indirect=True)
def test_terminal_read_has_no_provider_publisher_or_business_file_writes(actual_path) -> None:
    _, root, summary, _, calls, settings = actual_path

    def snapshot() -> dict[str, str]:
        return {
            path.relative_to(root).as_posix(): sha256(path.read_bytes()).hexdigest()
            for path in root.rglob("*") if path.is_file()
        }

    before_files = snapshot()
    before_calls = len(calls)
    with (
        patch.object(FakeLLMClient, "generate", side_effect=AssertionError("redispatch")) as provider,
        patch.object(PostWriteReleasePublicationService, "publish", side_effect=AssertionError("republish")) as publisher,
    ):
        replay = MainlineApplicationService().read_terminal_result(
            project_root=Path(__file__).resolve().parents[2],
            task_id=summary["task_id"],
            run_id=summary["run_id"],
            output_root=root,
            settings=settings,
            allow_demo_defaults=True,
            enable_agent_self_rag=False,
        )
    assert replay["terminal_outcome"] == summary["terminal_outcome"]
    assert len(calls) == before_calls
    assert snapshot() == before_files
    provider.assert_not_called()
    publisher.assert_not_called()
