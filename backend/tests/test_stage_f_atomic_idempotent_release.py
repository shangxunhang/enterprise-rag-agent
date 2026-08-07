from __future__ import annotations

import base64
import hashlib
import json
from pathlib import Path
from typing import Any

import pytest

from apps.enterprise_document.services.agent_rag_v1.artifact_store import (
    FileArtifactStore,
)
from apps.enterprise_document.services.release_publication import (
    ReleasePublicationService,
    ReleaseVerificationError,
)
from schemas.status import ExecutionStatus
from test_stage_f_release_publication import VERSION, _artifact, _report, _version


class FaultInjectionStore(FileArtifactStore):
    def __init__(self, root: Path, failure: str) -> None:
        super().__init__(root)
        self.failure = failure
        self.triggered = False

    def put_json(self, value: Any, **kwargs: Any):
        artifact_type = str(kwargs.get("artifact_type") or "")
        target = {
            "released_document": "document",
            "release_eligibility_proof": "eligibility",
            "release_manifest": "manifest_before",
        }.get(artifact_type)
        if not self.triggered and self.failure == target:
            self.triggered = True
            raise OSError(f"fault before {artifact_type}")
        reference = super().put_json(value, **kwargs)
        if (
            not self.triggered
            and self.failure == "manifest_after"
            and artifact_type == "release_manifest"
        ):
            self.triggered = True
            raise OSError("response lost after manifest commit")
        return reference


def _publish_default(
    service: ReleasePublicationService,
    *,
    artifact=None,
    output_format: str = "markdown",
):
    draft, hard_gate = artifact or _artifact()
    return service.publish(
        task_id=draft.task_id,
        run_id=draft.run_id,
        document_version=VERSION,
        draft=draft,
        output_format=output_format,
        hard_gate=hard_gate,
        generation_report=_report(),
        version_decision=_version(),
        output_status=ExecutionStatus.SUCCESS,
    )


def _manifest_ref(service: ReleasePublicationService, artifact) -> str:
    draft, _ = artifact
    _, key = service._stable_identity(  # noqa: SLF001 - contract probe
        task_id=draft.task_id,
        document_id=draft.document_id,
        document_version=VERSION,
    )
    return service._manifest_ref(key)  # noqa: SLF001 - contract probe


def _assert_manifest_absent(
    store: FileArtifactStore,
    service: ReleasePublicationService,
    artifact,
) -> None:
    draft, _ = artifact
    with pytest.raises(FileNotFoundError):
        store.get_json(
            _manifest_ref(service, artifact),
            task_id=draft.task_id,
            input_snapshot_version=VERSION,
        )


def _rewrite_payload(
    store: FileArtifactStore,
    artifact_ref: str,
    mutate,
) -> None:
    target = store._path_for_ref(artifact_ref)  # noqa: SLF001 - corruption probe
    envelope = json.loads(target.read_text(encoding="utf-8"))
    content = base64.b64decode(envelope["content_base64"])
    payload = json.loads(content.decode("utf-8"))
    mutate(payload)
    rewritten = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    envelope["content_base64"] = base64.b64encode(rewritten).decode("ascii")
    envelope["reference"]["content_hash"] = (
        "sha256:" + hashlib.sha256(rewritten).hexdigest()
    )
    envelope["reference"]["size_bytes"] = len(rewritten)
    target.write_text(
        json.dumps(
            envelope,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        + "\n",
        encoding="utf-8",
    )


def test_default_timestamp_exact_replay_is_true_idempotency(tmp_path: Path) -> None:
    root = tmp_path / "release-store"
    artifact = _artifact()
    first = _publish_default(ReleasePublicationService(FileArtifactStore(root)), artifact=artifact)
    second = _publish_default(ReleasePublicationService(FileArtifactStore(root)), artifact=artifact)

    assert first.manifest == second.manifest
    assert first.manifest_artifact == second.manifest_artifact
    assert first.manifest.published_at == second.manifest.published_at
    assert first.manifest.release_request_sha256 == second.manifest.release_request_sha256
    assert second.idempotent_replay is True


def test_new_service_and_store_recover_committed_release(tmp_path: Path) -> None:
    root = tmp_path / "release-store"
    artifact = _artifact()
    first_service = ReleasePublicationService(FileArtifactStore(root))
    first = _publish_default(first_service, artifact=artifact)

    restarted_service = ReleasePublicationService(FileArtifactStore(root))
    recovered = _publish_default(restarted_service, artifact=artifact)

    assert recovered.manifest == first.manifest
    assert recovered.manifest_artifact == first.manifest_artifact
    assert recovered.idempotent_replay is True
    assert restarted_service.verify(recovered) is True


@pytest.mark.parametrize("failure", ["document", "eligibility", "manifest_before"])
def test_precommit_failure_is_not_published_and_retry_succeeds(
    tmp_path: Path,
    failure: str,
) -> None:
    root = tmp_path / failure
    artifact = _artifact()
    fault_store = FaultInjectionStore(root, failure)
    fault_service = ReleasePublicationService(fault_store)

    with pytest.raises(OSError, match="fault before"):
        _publish_default(fault_service, artifact=artifact)

    _assert_manifest_absent(fault_store, fault_service, artifact)
    retry_service = ReleasePublicationService(FileArtifactStore(root))
    recovered = _publish_default(retry_service, artifact=artifact)

    assert recovered.idempotent_replay is False
    assert retry_service.verify(recovered) is True


def test_postcommit_response_loss_recovers_existing_release(tmp_path: Path) -> None:
    root = tmp_path / "release-store"
    artifact = _artifact()
    fault_service = ReleasePublicationService(
        FaultInjectionStore(root, "manifest_after")
    )

    with pytest.raises(OSError, match="response lost"):
        _publish_default(fault_service, artifact=artifact)

    restarted_service = ReleasePublicationService(FileArtifactStore(root))
    recovered = _publish_default(restarted_service, artifact=artifact)

    assert recovered.idempotent_replay is True
    assert restarted_service.verify(recovered) is True
    assert len(list(root.rglob("*.json"))) == 3


@pytest.mark.parametrize(
    "target_name",
    [
        "manifest_envelope",
        "manifest_payload",
        "document_payload",
        "eligibility_payload",
        "release_request_sha256",
    ],
)
def test_committed_release_tampering_is_never_overwritten(
    tmp_path: Path,
    target_name: str,
) -> None:
    root = tmp_path / target_name
    artifact = _artifact()
    store = FileArtifactStore(root)
    publication = _publish_default(ReleasePublicationService(store), artifact=artifact)

    manifest_ref = publication.manifest_artifact.artifact_ref
    document_ref = publication.manifest.document_artifact.artifact_ref
    proof_ref = publication.manifest.eligibility_artifact.artifact_ref
    if target_name == "manifest_envelope":
        target = store._path_for_ref(manifest_ref)  # noqa: SLF001
        envelope = json.loads(target.read_text(encoding="utf-8"))
        envelope["content_base64"] = "AAAA"
        target.write_text(json.dumps(envelope), encoding="utf-8")
    elif target_name == "manifest_payload":
        _rewrite_payload(
            store,
            manifest_ref,
            lambda payload: payload["metadata"].update({"tampered": True}),
        )
    elif target_name == "document_payload":
        _rewrite_payload(
            store,
            document_ref,
            lambda payload: payload.update({"output_format": "tampered"}),
        )
    elif target_name == "eligibility_payload":
        _rewrite_payload(
            store,
            proof_ref,
            lambda payload: payload["metadata"].update({"tampered": True}),
        )
    else:
        _rewrite_payload(
            store,
            manifest_ref,
            lambda payload: payload.update(
                {"release_request_sha256": "f" * 64}
            ),
        )

    restarted_service = ReleasePublicationService(FileArtifactStore(root))
    with pytest.raises(ReleaseVerificationError):
        _publish_default(restarted_service, artifact=artifact)
