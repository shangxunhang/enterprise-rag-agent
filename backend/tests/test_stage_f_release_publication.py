from __future__ import annotations

import json
from pathlib import Path

import pytest

from apps.enterprise_document.schemas.agent_rag_v1 import (
    GenerationReportSchema,
    TaskStatus,
    VersionDecisionSchema,
)
from apps.enterprise_document.services.agent_rag_v1.artifact_store import (
    FileArtifactStore,
)
from apps.enterprise_document.services.release_publication import (
    ReleaseConflictError,
    ReleaseEligibilityError,
    ReleasePublicationService,
    ReleaseVerificationError,
)
from schemas.status import ExecutionStatus
from stage_e_integrity_test_fixtures import evaluate, make_binding, make_draft, make_section


PUBLISHED_AT = "2026-08-04T10:00:00+00:00"
VERSION = "v1.0"


def _report() -> GenerationReportSchema:
    return GenerationReportSchema(
        task_id="task_stage_e_integrity",
        run_id="run_stage_e_integrity",
        task_status=TaskStatus.SUCCESS,
        document_version=VERSION,
        generated_at=PUBLISHED_AT,
    )


def _version() -> VersionDecisionSchema:
    return VersionDecisionSchema(
        previous_version=None,
        next_version=VERSION,
        change_level="initial",
        reason_codes=["initial_release"],
    )


def _artifact(content: str = "采用分阶段实施方法，形成闭环治理流程。"):
    binding = make_binding(allowed=["general_method"])
    section = make_section(content=content, binding=binding)
    draft = make_draft(section)
    hard_gate = evaluate(section)
    assert hard_gate.passed is True
    return draft, hard_gate


def _publish(
    service: ReleasePublicationService,
    *,
    content: str | None = None,
    artifact=None,
):
    draft, hard_gate = artifact or _artifact(
        content or "采用分阶段实施方法，形成闭环治理流程。"
    )
    return service.publish(
        task_id=draft.task_id,
        run_id=draft.run_id,
        document_version=VERSION,
        draft=draft,
        output_format="markdown",
        hard_gate=hard_gate,
        generation_report=_report(),
        version_decision=_version(),
        output_status=ExecutionStatus.SUCCESS,
        published_at=PUBLISHED_AT,
    )


def test_valid_release_is_immutable_and_verified(tmp_path: Path) -> None:
    service = ReleasePublicationService(FileArtifactStore(tmp_path / "artifacts"))

    publication = _publish(service)

    assert publication.verified_after_write is True
    assert publication.idempotent_replay is False
    assert publication.manifest.release_status == "published"
    assert publication.manifest.immutable is True
    assert publication.manifest.document_version == VERSION
    assert service.verify(publication) is True
    assert publication.manifest_artifact.immutable is True
    assert publication.manifest.document_artifact.immutable is True
    assert publication.manifest.eligibility_artifact.immutable is True


def test_same_release_identity_and_content_is_idempotent(tmp_path: Path) -> None:
    service = ReleasePublicationService(FileArtifactStore(tmp_path / "artifacts"))

    artifact = _artifact()
    first = _publish(service, artifact=artifact)
    second = _publish(service, artifact=artifact)

    assert first.manifest == second.manifest
    assert first.manifest_artifact == second.manifest_artifact
    assert second.idempotent_replay is True


def test_same_release_identity_with_different_content_is_rejected(tmp_path: Path) -> None:
    service = ReleasePublicationService(FileArtifactStore(tmp_path / "artifacts"))
    _publish(service)

    with pytest.raises(ReleaseConflictError, match="different content"):
        _publish(
            service,
            content="采用迭代实施方法，形成持续治理闭环。",
        )


def test_failed_hard_gate_cannot_publish(tmp_path: Path) -> None:
    service = ReleasePublicationService(FileArtifactStore(tmp_path / "artifacts"))
    draft, hard_gate = _artifact()
    failed_gate = hard_gate.model_copy(
        update={
            "passed": False,
            "failures": ["forced_failure"],
        },
        deep=True,
    )

    with pytest.raises(ReleaseEligibilityError, match="hard gate must pass"):
        service.publish(
            task_id=draft.task_id,
            run_id=draft.run_id,
            document_version=VERSION,
            draft=draft,
            output_format="markdown",
            hard_gate=failed_gate,
            generation_report=_report(),
            version_decision=_version(),
            output_status=ExecutionStatus.FAILED,
            published_at=PUBLISHED_AT,
        )


def test_missing_stage_e_release_check_cannot_publish(tmp_path: Path) -> None:
    service = ReleasePublicationService(FileArtifactStore(tmp_path / "artifacts"))
    draft, hard_gate = _artifact()
    incomplete_gate = hard_gate.model_copy(
        update={
            "checks": {
                **dict(hard_gate.checks),
                "writer_evidence_lineage_fields_present": False,
            }
        },
        deep=True,
    )

    with pytest.raises(ReleaseEligibilityError, match="Stage E release checks"):
        service.publish(
            task_id=draft.task_id,
            run_id=draft.run_id,
            document_version=VERSION,
            draft=draft,
            output_format="markdown",
            hard_gate=incomplete_gate,
            generation_report=_report(),
            version_decision=_version(),
            output_status=ExecutionStatus.SUCCESS,
            published_at=PUBLISHED_AT,
        )


def test_tampered_release_envelope_fails_readback_verification(tmp_path: Path) -> None:
    store = FileArtifactStore(tmp_path / "artifacts")
    service = ReleasePublicationService(store)
    publication = _publish(service)
    target = store._path_for_ref(  # noqa: SLF001 - intentional corruption probe
        publication.manifest.document_artifact.artifact_ref
    )
    envelope = json.loads(target.read_text(encoding="utf-8"))
    envelope["content_base64"] = "AAAA"
    target.write_text(json.dumps(envelope), encoding="utf-8")

    with pytest.raises(ReleaseVerificationError, match="hash mismatch|size mismatch"):
        service.verify(publication)
