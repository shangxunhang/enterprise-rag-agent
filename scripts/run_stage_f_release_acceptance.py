"""Run final Stage F atomic release acceptance against real file stores."""

from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor
import json
from pathlib import Path
import shutil
import sys
from threading import Barrier
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[1]
BACKEND_ROOT = PROJECT_ROOT / "backend"
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from apps.enterprise_document.schemas.agent_rag_v1 import (  # noqa: E402
    GenerationReportSchema,
    TaskStatus,
    VersionDecisionSchema,
)
from apps.enterprise_document.schemas.gate_decision import (  # noqa: E402
    ClaimRiskLevel,
    DeliveryMode,
    GateDecisionSchema,
)
from apps.enterprise_document.schemas.scheme_writer import (  # noqa: E402
    SchemeDraftSchema,
    SchemeSectionSchema,
    SectionEvalSchema,
)
from apps.enterprise_document.schemas.writer_governance import (  # noqa: E402
    WriterGateBindingSchema,
)
from apps.enterprise_document.services.agent_rag_v1.artifact_store import (  # noqa: E402
    FileArtifactStore,
)
from apps.enterprise_document.services.document_gate import (  # noqa: E402
    evaluate_scheme_draft,
)
from apps.enterprise_document.services.release_publication import (  # noqa: E402
    ReleaseConflictError,
    ReleaseEligibilityError,
    ReleasePublicationService,
    ReleaseVerificationError,
)
from apps.enterprise_document.services.scheme_writer.writer_governance import (  # noqa: E402
    WriterClaimAuditor,
)
from rag.offline.manifest import sha256_file  # noqa: E402
from rag.offline.resolver import ActiveIndexResolver  # noqa: E402
from rag.schema.evidence_assessment_v2 import EvidenceStatus  # noqa: E402
from schemas.status import ExecutionStatus  # noqa: E402


TASK_ID = "task_stage_f_runtime"
RUN_ID = "run_stage_f_runtime"
DOCUMENT_ID = "document_stage_f_runtime"
SECTION_ID = "section_run_stage_f_runtime_001"
SECTION_TITLE = "实施方案"
FIXTURE_TIME = "2026-08-04T11:30:00+00:00"


class FaultInjectionStore(FileArtifactStore):
    """One-shot failure probe around immutable artifact writes."""

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


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--output",
        default="E:/rag-agent-runtime/stage_f_release_runtime_acceptance_final.json",
    )
    parser.add_argument(
        "--store-root",
        default="E:/rag-agent-runtime/stage_f_release_store_final",
    )
    parser.add_argument(
        "--tamper-store-root",
        default="E:/rag-agent-runtime/stage_f_release_tamper_store_final",
    )
    return parser


def _binding(*, run_id: str = RUN_ID) -> WriterGateBindingSchema:
    return WriterGateBindingSchema(
        section_id=SECTION_ID if run_id == RUN_ID else f"section_{run_id}_001",
        section_title=SECTION_TITLE,
        section_order=1,
        document_id=DOCUMENT_ID,
        run_id=run_id,
        gate_decision=GateDecisionSchema(
            policy_id="stage_f_runtime_grounded_v1",
            evidence_status=EvidenceStatus.SUFFICIENT,
            delivery_mode=DeliveryMode.GROUNDED,
            claim_risk_level=ClaimRiskLevel.LOW,
            allowed_claim_types=["general_method"],
            forbidden_claim_types=[],
            required_qualifiers=[],
            covered_topics=["实施方法"],
            missing_topics=[],
            human_review_required=False,
            reason_codes=["stage_f_runtime_control"],
        ),
        generation_allowed=True,
        evidence_contract_sha256="a" * 64,
        prompt_evidence_source="stage_f_runtime_selected_evidence",
        prompt_evidence_contract_sha256="b" * 64,
    )


def _artifact(
    *,
    content: str,
    run_id: str = RUN_ID,
    document_version: str = "v1.0",
) -> tuple[
    SchemeDraftSchema,
    Any,
    GenerationReportSchema,
    VersionDecisionSchema,
]:
    binding = _binding(run_id=run_id)
    audit = WriterClaimAuditor().audit(
        content,
        binding=binding,
        audit_created_at=FIXTURE_TIME,
    )
    section = SchemeSectionSchema(
        section_id=binding.section_id,
        section_title=SECTION_TITLE,
        section_order=1,
        content=content,
        model_output=content,
        status=ExecutionStatus.SUCCESS,
        eval_result=SectionEvalSchema(
            passed=True,
            checks={"claim_audit_passed": True},
        ),
        started_at=FIXTURE_TIME,
        finished_at=FIXTURE_TIME,
        extra={
            "writer_gate_binding": binding.model_dump(mode="json", exclude_none=True),
            "writer_execution_status": binding.execution_status.value,
            "claim_audit": audit.model_dump(mode="json", exclude_none=True),
            "required_qualifiers_applied": [],
            "formal_deliverable_eligible": True,
            "need_human_review": False,
        },
    )
    draft = SchemeDraftSchema(
        draft_id=f"draft_{run_id}_stage_f",
        document_id=DOCUMENT_ID,
        task_id=TASK_ID,
        run_id=run_id,
        title="Stage F Runtime Release",
        full_text=content,
        sections=[section],
        required_sections=[SECTION_TITLE],
        created_at=FIXTURE_TIME,
        updated_at=FIXTURE_TIME,
    )
    hard_gate = evaluate_scheme_draft(
        draft,
        citation_required=False,
        retrieved_chunk_ids=[],
        evidence_sufficient=True,
        workflow_complete=True,
    )
    report = GenerationReportSchema(
        task_id=TASK_ID,
        run_id=run_id,
        task_status=TaskStatus.SUCCESS,
        document_version=document_version,
        generated_at=FIXTURE_TIME,
    )
    previous = None if document_version == "v1.0" else "v1.0"
    decision = VersionDecisionSchema(
        previous_version=previous,
        next_version=document_version,
        change_level="initial" if previous is None else "minor",
        reason_codes=["stage_f_runtime_release"],
    )
    return draft, hard_gate, report, decision


def _publish(
    service: ReleasePublicationService,
    artifact,
    *,
    document_version: str,
    output_status: ExecutionStatus = ExecutionStatus.SUCCESS,
):
    draft, hard_gate, report, decision = artifact
    return service.publish(
        task_id=TASK_ID,
        run_id=draft.run_id,
        document_version=document_version,
        draft=draft,
        output_format="markdown",
        hard_gate=hard_gate,
        generation_report=report,
        version_decision=decision,
        output_status=output_status,
    )


def _manifest_ref(service: ReleasePublicationService, artifact, version: str) -> str:
    draft = artifact[0]
    _, key = service._stable_identity(  # noqa: SLF001 - acceptance probe
        task_id=TASK_ID,
        document_id=draft.document_id,
        document_version=version,
    )
    return service._manifest_ref(key)  # noqa: SLF001 - acceptance probe


def _manifest_exists(
    store: FileArtifactStore,
    service: ReleasePublicationService,
    artifact,
    version: str,
) -> bool:
    try:
        store.get_json(
            _manifest_ref(service, artifact, version),
            task_id=TASK_ID,
            input_snapshot_version=version,
        )
        return True
    except FileNotFoundError:
        return False


def _fault_scenario(root: Path, failure: str) -> dict[str, Any]:
    artifact = _artifact(
        content="采用分阶段实施方法，形成闭环治理流程。",
    )
    store = FaultInjectionStore(root, failure)
    service = ReleasePublicationService(store)
    failed = False
    message = ""
    try:
        _publish(service, artifact, document_version="v1.0")
    except OSError as exc:
        failed = True
        message = str(exc)
    committed_after_failure = _manifest_exists(store, service, artifact, "v1.0")
    retry_service = ReleasePublicationService(FileArtifactStore(root))
    retry = _publish(retry_service, artifact, document_version="v1.0")
    return {
        "failure_observed": failed,
        "failure_message": message,
        "committed_after_failure": committed_after_failure,
        "retry_succeeded": retry_service.verify(retry),
        "retry_idempotent_replay": retry.idempotent_replay,
        "manifest": retry.manifest.model_dump(mode="json"),
        "artifact_file_count": len(list(root.rglob("*.json"))),
    }


def _postcommit_loss(root: Path) -> dict[str, Any]:
    artifact = _artifact(
        content="采用分阶段实施方法，形成闭环治理流程。",
    )
    service = ReleasePublicationService(FaultInjectionStore(root, "manifest_after"))
    failed = False
    message = ""
    try:
        _publish(service, artifact, document_version="v1.0")
    except OSError as exc:
        failed = True
        message = str(exc)
    restarted = ReleasePublicationService(FileArtifactStore(root))
    recovered = _publish(restarted, artifact, document_version="v1.0")
    return {
        "response_loss_observed": failed,
        "message": message,
        "recovered": restarted.verify(recovered),
        "idempotent_replay": recovered.idempotent_replay,
        "manifest": recovered.manifest.model_dump(mode="json"),
        "artifact_file_count": len(list(root.rglob("*.json"))),
    }


def _concurrent_same(root: Path) -> dict[str, Any]:
    artifact = _artifact(
        content="采用分阶段实施方法，形成闭环治理流程。",
    )
    services = [
        ReleasePublicationService(FileArtifactStore(root)),
        ReleasePublicationService(FileArtifactStore(root)),
    ]
    barrier = Barrier(2)

    def invoke(service: ReleasePublicationService):
        barrier.wait()
        return _publish(service, artifact, document_version="v1.0")

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(invoke, services))
    return {
        "both_succeeded": len(results) == 2,
        "same_manifest": results[0].manifest == results[1].manifest,
        "same_manifest_artifact": (
            results[0].manifest_artifact == results[1].manifest_artifact
        ),
        "same_published_at": (
            results[0].manifest.published_at == results[1].manifest.published_at
        ),
        "idempotent_replay_count": sum(item.idempotent_replay for item in results),
        "artifact_file_count": len(list(root.rglob("*.json"))),
        "verified": all(service.verify(result) for service, result in zip(services, results)),
        "manifest": results[0].manifest.model_dump(mode="json"),
    }


def _concurrent_different(root: Path) -> dict[str, Any]:
    artifacts = {
        "a": _artifact(content="采用分阶段实施方法，形成闭环治理流程。"),
        "b": _artifact(content="采用迭代实施方法，形成持续治理闭环。"),
    }
    services = {
        key: ReleasePublicationService(FileArtifactStore(root)) for key in artifacts
    }
    barrier = Barrier(2)

    def invoke(label: str):
        barrier.wait()
        try:
            return label, _publish(
                services[label],
                artifacts[label],
                document_version="v1.0",
            ), None
        except Exception as exc:  # noqa: BLE001 - acceptance outcome capture
            return label, None, exc

    with ThreadPoolExecutor(max_workers=2) as executor:
        outcomes = list(executor.map(invoke, ["a", "b"]))
    successes = [(label, result) for label, result, error in outcomes if error is None]
    conflicts = [(label, error) for label, result, error in outcomes if error is not None]
    winner_label, winner = successes[0] if len(successes) == 1 else (None, None)
    loser_label = conflicts[0][0] if len(conflicts) == 1 else None
    verifier = ReleasePublicationService(FileArtifactStore(root))
    mixed = True
    winner_retry_ok = False
    loser_retry_conflict = False
    if winner is not None:
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
        mixed = not (
            document.get("release_request_sha256")
            == proof.get("release_request_sha256")
            == winner.manifest.release_request_sha256
        )
        winner_retry = _publish(
            ReleasePublicationService(FileArtifactStore(root)),
            artifacts[winner_label],
            document_version="v1.0",
        )
        winner_retry_ok = winner_retry.idempotent_replay and verifier.verify(winner_retry)
        try:
            _publish(
                ReleasePublicationService(FileArtifactStore(root)),
                artifacts[loser_label],
                document_version="v1.0",
            )
        except ReleaseConflictError:
            loser_retry_conflict = True
    return {
        "success_count": len(successes),
        "conflict_count": sum(
            isinstance(error, ReleaseConflictError) for _, error in conflicts
        ),
        "winner": winner_label,
        "loser": loser_label,
        "mixed_release": mixed,
        "winner_retry_ok": winner_retry_ok,
        "loser_retry_conflict": loser_retry_conflict,
        "artifact_file_count": len(list(root.rglob("*.json"))),
        "winner_manifest": (
            winner.manifest.model_dump(mode="json") if winner is not None else None
        ),
    }


def _tamper(root: Path, target_name: str) -> dict[str, Any]:
    artifact = _artifact(
        content="采用分阶段实施方法，形成闭环治理流程。",
        run_id=f"run_stage_f_tamper_{target_name}",
    )
    store = FileArtifactStore(root)
    service = ReleasePublicationService(store)
    publication = _publish(service, artifact, document_version="v1.0")
    target_ref = {
        "manifest": publication.manifest_artifact.artifact_ref,
        "document": publication.manifest.document_artifact.artifact_ref,
        "eligibility": publication.manifest.eligibility_artifact.artifact_ref,
    }[target_name]
    target = store._path_for_ref(target_ref)  # noqa: SLF001 - corruption probe
    envelope = json.loads(target.read_text(encoding="utf-8"))
    envelope["content_base64"] = "AAAA"
    target.write_text(json.dumps(envelope), encoding="utf-8")
    detected = False
    message = ""
    try:
        ReleasePublicationService(FileArtifactStore(root)).verify(publication)
    except ReleaseVerificationError as exc:
        detected = True
        message = str(exc)
    return {"detected": detected, "message": message, "artifact_ref": target_ref}


def main() -> int:
    args = _parser().parse_args()
    pointer = PROJECT_ROOT / "data/processed/indexes/active_index.json"
    resolver = ActiveIndexResolver(verify_manifest_hash=True, verify_artifacts=False)
    active_before = resolver.resolve(pointer)

    store_root = Path(args.store_root).expanduser().resolve()
    tamper_root = Path(args.tamper_store_root).expanduser().resolve()
    for root in (store_root, tamper_root):
        if root.exists():
            shutil.rmtree(root)
        root.mkdir(parents=True, exist_ok=True)

    base_root = store_root / "base"
    base_artifact = _artifact(content="采用分阶段实施方法，形成闭环治理流程。")
    first_service = ReleasePublicationService(FileArtifactStore(base_root))
    first = _publish(first_service, base_artifact, document_version="v1.0")
    replay = _publish(first_service, base_artifact, document_version="v1.0")
    restarted_service = ReleasePublicationService(FileArtifactStore(base_root))
    restarted_replay = _publish(
        restarted_service,
        base_artifact,
        document_version="v1.0",
    )

    conflict_rejected = False
    try:
        _publish(
            restarted_service,
            _artifact(content="采用迭代实施方法，形成持续治理闭环。"),
            document_version="v1.0",
        )
    except ReleaseConflictError:
        conflict_rejected = True

    cross_run_rejected = False
    try:
        _publish(
            restarted_service,
            _artifact(
                content="采用分阶段实施方法，形成闭环治理流程。",
                run_id="run_stage_f_runtime_replay",
            ),
            document_version="v1.0",
        )
    except ReleaseConflictError:
        cross_run_rejected = True

    version_bump = _publish(
        restarted_service,
        _artifact(
            content="采用迭代实施方法，形成持续治理闭环。",
            document_version="v1.1",
        ),
        document_version="v1.1",
    )

    failed_gate_rejected = False
    rejected_artifact = _artifact(
        content="采用分阶段实施方法，形成闭环治理流程。",
        document_version="v2.0",
    )
    rejected_artifact = (
        rejected_artifact[0],
        rejected_artifact[1].model_copy(
            update={"passed": False, "failures": ["forced_failure"]},
            deep=True,
        ),
        rejected_artifact[2],
        rejected_artifact[3],
    )
    rejected_root = store_root / "rejected"
    rejected_service = ReleasePublicationService(FileArtifactStore(rejected_root))
    try:
        _publish(
            rejected_service,
            rejected_artifact,
            document_version="v2.0",
            output_status=ExecutionStatus.FAILED,
        )
    except ReleaseEligibilityError:
        failed_gate_rejected = True
    rejected_manifest_exists = _manifest_exists(
        rejected_service.artifact_store,
        rejected_service,
        rejected_artifact,
        "v2.0",
    )

    document_failure = _fault_scenario(store_root / "fault-document", "document")
    eligibility_failure = _fault_scenario(
        store_root / "fault-eligibility",
        "eligibility",
    )
    manifest_failure = _fault_scenario(
        store_root / "fault-manifest",
        "manifest_before",
    )
    postcommit_loss = _postcommit_loss(store_root / "postcommit-loss")
    concurrent_same = _concurrent_same(store_root / "concurrent-same")
    concurrent_different = _concurrent_different(
        store_root / "concurrent-different"
    )
    tampered_manifest = _tamper(tamper_root / "manifest", "manifest")
    tampered_document = _tamper(tamper_root / "document", "document")
    tampered_eligibility = _tamper(tamper_root / "eligibility", "eligibility")

    active_after = resolver.resolve(pointer)
    hard_failures = {
        "valid_release_not_verified": not (
            first.verified_after_write and first_service.verify(first)
        ),
        "exact_replay_not_idempotent": not (
            replay.idempotent_replay and replay.manifest == first.manifest
        ),
        "default_timestamp_exact_replay_not_idempotent": not (
            replay.manifest == first.manifest
            and replay.manifest.published_at == first.manifest.published_at
        ),
        "different_service_replay_not_idempotent": not (
            restarted_replay.idempotent_replay
            and restarted_replay.manifest == first.manifest
        ),
        "same_version_different_content_not_rejected": not conflict_rejected,
        "cross_run_same_version_replay_not_rejected": not cross_run_rejected,
        "new_version_release_failed": not restarted_service.verify(version_bump),
        "failed_hard_gate_release_not_rejected": not failed_gate_rejected,
        "document_blob_failure_created_committed_release": document_failure[
            "committed_after_failure"
        ],
        "eligibility_blob_failure_created_committed_release": eligibility_failure[
            "committed_after_failure"
        ],
        "manifest_failure_created_committed_release": manifest_failure[
            "committed_after_failure"
        ],
        "retry_after_document_blob_failure_failed": not document_failure[
            "retry_succeeded"
        ],
        "retry_after_eligibility_blob_failure_failed": not eligibility_failure[
            "retry_succeeded"
        ],
        "retry_after_manifest_failure_failed": not manifest_failure[
            "retry_succeeded"
        ],
        "post_commit_response_loss_not_recovered": not (
            postcommit_loss["response_loss_observed"]
            and postcommit_loss["recovered"]
            and postcommit_loss["idempotent_replay"]
        ),
        "concurrent_same_content_not_same_manifest": not (
            concurrent_same["both_succeeded"]
            and concurrent_same["same_manifest"]
            and concurrent_same["same_manifest_artifact"]
            and concurrent_same["same_published_at"]
            and concurrent_same["verified"]
        ),
        "concurrent_same_content_created_multiple_canonical_manifests": (
            concurrent_same["artifact_file_count"] != 3
        ),
        "concurrent_different_content_not_exactly_one_winner": not (
            concurrent_different["success_count"] == 1
            and concurrent_different["conflict_count"] == 1
        ),
        "concurrent_mixed_release_detected": concurrent_different["mixed_release"],
        "concurrent_conflict_poisoned_version": not (
            concurrent_different["winner_retry_ok"]
            and concurrent_different["loser_retry_conflict"]
        ),
        "orphan_blob_treated_as_published": eligibility_failure[
            "committed_after_failure"
        ],
        "tampered_manifest_not_detected": not tampered_manifest["detected"],
        "tampered_document_not_detected": not tampered_document["detected"],
        "tampered_eligibility_not_detected": not tampered_eligibility["detected"],
        "release_artifacts_not_immutable": not all(
            [
                first.manifest_artifact.immutable,
                first.manifest.document_artifact.immutable,
                first.manifest.eligibility_artifact.immutable,
            ]
        ),
        "rejected_path_published_release": rejected_manifest_exists,
        "active_pointer_or_manifest_changed": not (
            active_before["index_version"] == active_after["index_version"]
            and active_before["pointer_hash"] == active_after["pointer_hash"]
            and active_before["manifest_hash"] == active_after["manifest_hash"]
        ),
    }
    report = {
        "schema_version": "stage_f_release_runtime_acceptance_final",
        "stage": "Stage F.1 Atomic Commit & True Idempotency",
        "passed": not any(hard_failures.values()),
        "active_index": {
            "index_version": active_after["index_version"],
            "pointer_path": active_after["pointer_path"],
            "pointer_hash": active_after["pointer_hash"],
            "manifest_path": active_after["manifest_path"],
            "manifest_hash": active_after["manifest_hash"],
        },
        "release_store": {
            "root": str(store_root),
            "artifact_file_count": len(list(store_root.rglob("*.json"))),
            "canonical_manifest_ref": first.manifest_artifact.artifact_ref,
            "release_id": first.manifest.release_id,
            "release_request_sha256": first.manifest.release_request_sha256,
            "document_blob_ref": first.manifest.document_artifact.artifact_ref,
            "eligibility_blob_ref": first.manifest.eligibility_artifact.artifact_ref,
        },
        "scenarios": {
            "valid_release": first.model_dump(mode="json"),
            "default_timestamp_exact_replay": replay.model_dump(mode="json"),
            "different_service_restart_replay": restarted_replay.model_dump(mode="json"),
            "same_version_different_content_conflict": conflict_rejected,
            "cross_run_same_version_conflict": cross_run_rejected,
            "new_version_release": version_bump.model_dump(mode="json"),
            "failed_hard_gate": {
                "rejected": failed_gate_rejected,
                "manifest_exists": rejected_manifest_exists,
            },
            "document_blob_failure": document_failure,
            "eligibility_blob_failure": eligibility_failure,
            "manifest_precommit_failure": manifest_failure,
            "postcommit_response_loss": postcommit_loss,
            "concurrent_same_content": concurrent_same,
            "concurrent_different_content": concurrent_different,
            "tampered_manifest": tampered_manifest,
            "tampered_document": tampered_document,
            "tampered_eligibility": tampered_eligibility,
        },
        "hard_failures": hard_failures,
    }
    output = Path(args.output).expanduser().resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(
        json.dumps(
            {
                "passed": report["passed"],
                "output": str(output),
                "output_sha256": sha256_file(output),
                "hard_failure_count": sum(hard_failures.values()),
                "release_id": first.manifest.release_id,
                "active_index": active_after["index_version"],
            },
            ensure_ascii=True,
            indent=2,
        )
    )
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
