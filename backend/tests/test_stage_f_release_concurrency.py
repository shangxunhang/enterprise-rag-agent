from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from threading import Barrier

import pytest

from apps.enterprise_document.services.agent_rag_v1.artifact_store import (
    FileArtifactStore,
)
from apps.enterprise_document.services.release_publication import (
    ReleaseConflictError,
    ReleasePublicationService,
)
from test_stage_f_atomic_idempotent_release import _publish_default
from test_stage_f_release_publication import _artifact


def _run_concurrently(calls):
    barrier = Barrier(len(calls))

    def invoke(call):
        barrier.wait()
        return call()

    with ThreadPoolExecutor(max_workers=len(calls)) as executor:
        futures = [executor.submit(invoke, call) for call in calls]
        results = []
        errors = []
        for future in futures:
            try:
                results.append(future.result())
            except Exception as exc:  # noqa: BLE001 - concurrency outcome capture
                errors.append(exc)
    return results, errors


def test_same_content_concurrent_publish_returns_one_release(tmp_path: Path) -> None:
    root = tmp_path / "release-store"
    artifact = _artifact()
    first_service = ReleasePublicationService(FileArtifactStore(root))
    second_service = ReleasePublicationService(FileArtifactStore(root))

    results, errors = _run_concurrently(
        [
            lambda: _publish_default(first_service, artifact=artifact),
            lambda: _publish_default(second_service, artifact=artifact),
        ]
    )

    assert errors == []
    assert len(results) == 2
    assert results[0].manifest == results[1].manifest
    assert results[0].manifest_artifact == results[1].manifest_artifact
    assert results[0].manifest.published_at == results[1].manifest.published_at
    assert sum(result.idempotent_replay for result in results) == 1
    assert len(list(root.rglob("*.json"))) == 3
    assert first_service.verify(results[0]) is True
    assert second_service.verify(results[1]) is True


def test_different_content_concurrent_publish_has_one_clean_winner(
    tmp_path: Path,
) -> None:
    root = tmp_path / "release-store"
    artifacts = {
        "a": _artifact("采用分阶段实施方法，形成闭环治理流程。"),
        "b": _artifact("采用迭代实施方法，形成持续治理闭环。"),
    }
    services = {
        "a": ReleasePublicationService(FileArtifactStore(root)),
        "b": ReleasePublicationService(FileArtifactStore(root)),
    }
    barrier = Barrier(2)

    def invoke(label: str):
        barrier.wait()
        try:
            return label, _publish_default(
                services[label],
                artifact=artifacts[label],
            ), None
        except Exception as exc:  # noqa: BLE001 - expected competing outcome
            return label, None, exc

    with ThreadPoolExecutor(max_workers=2) as executor:
        outcomes = list(executor.map(invoke, ["a", "b"]))

    successes = [(label, result) for label, result, error in outcomes if error is None]
    conflicts = [(label, error) for label, result, error in outcomes if error is not None]
    assert len(successes) == 1
    assert len(conflicts) == 1
    assert isinstance(conflicts[0][1], ReleaseConflictError)

    winner_label, winner = successes[0]
    loser_label, _ = conflicts[0]
    verifier = ReleasePublicationService(FileArtifactStore(root))
    assert verifier.verify(winner) is True
    assert len(list(root.rglob("*.json"))) == 3

    document = verifier.artifact_store.get_json(
        winner.manifest.document_artifact.artifact_ref,
        task_id=winner.manifest.task_id,
        attempt_id=winner.manifest.run_id,
        input_snapshot_version=winner.manifest.document_version,
    )
    proof = verifier.artifact_store.get_json(
        winner.manifest.eligibility_artifact.artifact_ref,
        task_id=winner.manifest.task_id,
        attempt_id=winner.manifest.run_id,
        input_snapshot_version=winner.manifest.document_version,
    )
    assert document["release_request_sha256"] == winner.manifest.release_request_sha256
    assert proof["release_request_sha256"] == winner.manifest.release_request_sha256

    winner_replay = _publish_default(
        ReleasePublicationService(FileArtifactStore(root)),
        artifact=artifacts[winner_label],
    )
    assert winner_replay.idempotent_replay is True
    assert winner_replay.manifest == winner.manifest

    with pytest.raises(ReleaseConflictError):
        _publish_default(
            ReleasePublicationService(FileArtifactStore(root)),
            artifact=artifacts[loser_label],
        )
    assert verifier.verify(winner) is True
