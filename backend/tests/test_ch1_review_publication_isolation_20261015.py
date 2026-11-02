"""Review draft must survive post-terminal Publisher error without false release.

Strictly offline: mocked publisher, real terminal outcome/status projection and
locally exported editable review copy. No Provider or durable Run is started.
"""
from __future__ import annotations

from hashlib import sha256
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from application.terminal_status_view import project_terminal_status
from apps.enterprise_document.schemas.agent_rag_v1.lifecycle import RunLifecycleStatus
from apps.enterprise_document.schemas.post_write.foundation import (
    AvailabilityState,
    EnforcementAction,
    OverallQualityState,
)
from apps.enterprise_document.services.agent_rag_v1.post_write_composition import (
    TargetPostWriteComposition,
)
from apps.enterprise_document.services.agent_rag_v1.post_write_review_draft import (
    TargetReviewDraftLocalExporter,
    TargetReviewDraftReader,
    ReviewDraftUnavailable,
)
from apps.enterprise_document.services.agent_rag_v1.terminal_outcome_transport import (
    project_post_write,
)
from test_ch1_review_draft_access_20261015 import _fixture


def _publisher_fixture(*, action=EnforcementAction.OUTPUT):
    control = SimpleNamespace(enforcement_action=action)
    composition = SimpleNamespace(
        control_store=SimpleNamespace(resolve_exact=Mock(return_value=control)),
        _publish_target_release=Mock(),
    )
    final_document = SimpleNamespace(control_decision_ref="control-1")
    arguments = dict(
        task_id="task-ch1",
        run_id="run-ch1",
        document_id="doc-ch1",
        final_document=final_document,
        assessment_ref="assessment-1",
        terminal_run=SimpleNamespace(status=RunLifecycleStatus.SUCCESS),
    )
    return composition, arguments, control


def test_publication_error_is_distinct_from_completed_artifact():
    composition, args, _ = _publisher_fixture()
    composition._publish_target_release.side_effect = RuntimeError(
        "publisher transport unavailable"
    )
    published, failure = TargetPostWriteComposition._attempt_review_safe_publication(
        composition, **args
    )
    assert published is None
    assert failure["error_code"] == "TARGET_RELEASE_PUBLICATION_FAILED"
    assert failure["error_type"] == "RuntimeError"
    assert "publisher transport unavailable" in failure["message"]
    composition._publish_target_release.assert_called_once()
    assert composition.control_store.resolve_exact.call_count == 1


def test_publication_success_passthrough_and_block_not_dispatched():
    composition, args, control = _publisher_fixture()
    exact = SimpleNamespace(manifest=SimpleNamespace(release_id="release-1"))
    composition._publish_target_release.return_value = exact
    published, failure = TargetPostWriteComposition._attempt_review_safe_publication(
        composition, **args
    )
    assert published is exact
    assert failure is None
    control.enforcement_action = EnforcementAction.BLOCK
    composition._publish_target_release.reset_mock()
    published, failure = TargetPostWriteComposition._attempt_review_safe_publication(
        composition, **args
    )
    assert published is None and failure is None
    composition._publish_target_release.assert_not_called()


def test_publication_error_survives_terminal_outcome_without_quality_upgrade():
    quality = OverallQualityState.PASS
    control = SimpleNamespace(
        enforcement_action=EnforcementAction.OUTPUT,
        quality_state_passthrough=quality,
        availability_projection=SimpleNamespace(state=AvailabilityState.AVAILABLE),
        control_reason_codes=(),
        blocking_fact_refs=(),
        unresolved_reason_refs=(),
    )
    completion = SimpleNamespace(
        final_document=SimpleNamespace(
            control_decision_ref="control-1",
            validation_completion_ref="validation-1",
        ),
        published_release=None,
        publication_failure={
            "error_code": "TARGET_RELEASE_PUBLICATION_FAILED",
            "error_type": "RuntimeError",
            "message": "synthetic publication outage",
        },
    )
    comp = SimpleNamespace(
        control_store=SimpleNamespace(resolve_exact=Mock(return_value=control)),
        validation_store=SimpleNamespace(
            resolve_completion=Mock(return_value=SimpleNamespace(items=()))
        ),
        lifecycle_service=SimpleNamespace(
            load_run_lifecycle=Mock(
                return_value=SimpleNamespace(status=RunLifecycleStatus.SUCCESS)
            )
        ),
    )
    payload = SimpleNamespace(
        task_id="task-ch1", run_id="run-ch1", section_execution_results=()
    )
    outcome = project_post_write(
        comp, payload=payload, sections=(), completion=completion
    )
    assert outcome.execution_status.value == "partial_success"
    assert outcome.quality_state is OverallQualityState.PASS
    assert outcome.run_status is RunLifecycleStatus.SUCCESS
    assert outcome.publication_exists is False
    assert "publication" in outcome.unfinished_scope_refs
    assert "TARGET_RELEASE_PUBLICATION_FAILED" in {
        item.code for item in outcome.diagnostics
    }
    status = project_terminal_status(
        outcome,
        source_mode="verified_target",
        expected_task_id="task-ch1",
        expected_run_id="run-ch1",
        expected_phase="evaluate_document_gate",
    )
    assert status["release_status"] == "publication_unverified"
    assert status["business_quality_status"] == "passed"
    assert status["execution_status"] == "partial_success"


def test_review_copy_is_real_editable_file_and_idempotent(tmp_path):
    service, request, _ = _fixture()
    draft = TargetReviewDraftReader(service).read(**request)
    exporter = TargetReviewDraftLocalExporter(tmp_path)
    first = exporter.export(
        draft=draft, task_id=request["task_id"], run_id=request["run_id"]
    )
    target = __import__("pathlib").Path(first["review_file_path"])
    assert target.is_file()
    assert target.suffix == ".md"
    assert "本项目计划新建服务器12台" in target.read_text(encoding="utf-8")
    assert sha256(target.read_bytes()).hexdigest() == first["review_file_sha256"]
    assert first["source_validated_output_sha256"] == draft.output_binding.output_payload_sha256
    assert first["delivery_kind"] == "HUMAN_REVIEW_COPY_NOT_FORMAL_RELEASE"
    second = exporter.export(
        draft=draft, task_id=request["task_id"], run_id=request["run_id"]
    )
    assert first == second


def test_review_copy_refuses_overwrite_of_human_edit(tmp_path):
    service, request, _ = _fixture()
    draft = TargetReviewDraftReader(service).read(**request)
    exporter = TargetReviewDraftLocalExporter(tmp_path)
    receipt = exporter.export(
        draft=draft, task_id=request["task_id"], run_id=request["run_id"]
    )
    __import__("pathlib").Path(receipt["review_file_path"]).write_text(
        "engineer human edits", encoding="utf-8"
    )
    with pytest.raises(ReviewDraftUnavailable, match="differs"):
        exporter.export(
            draft=draft, task_id=request["task_id"], run_id=request["run_id"]
        )


def test_review_copy_cross_run_does_not_export(tmp_path):
    service, request, _ = _fixture()
    draft = TargetReviewDraftReader(service).read(**request)
    with pytest.raises(ReviewDraftUnavailable, match="foreign"):
        TargetReviewDraftLocalExporter(tmp_path).export(
            draft=draft, task_id="other-task", run_id="other-run"
        )


def test_review_copy_json_preserves_validated_source_bytes(tmp_path):
    service, request, _ = _fixture()
    draft = TargetReviewDraftReader(service).read(**request)
    from dataclasses import replace
    payload = b'{"payload_kind":"document"}'
    binding = SimpleNamespace(
        output_format="json",
        output_artifact_ref=draft.output_binding.output_artifact_ref,
        output_payload_sha256=sha256(payload).hexdigest(),
    )
    json_copy = replace(draft, output_binding=binding, validated_output_bytes=payload)
    receipt = TargetReviewDraftLocalExporter(tmp_path).export(
        draft=json_copy, task_id=request["task_id"], run_id=request["run_id"]
    )
    assert receipt["review_file_path"].endswith(".json")
    assert __import__("pathlib").Path(receipt["review_file_path"]).read_bytes() == payload
