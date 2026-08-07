"""Stage F atomic, immutable, and truly idempotent release publication."""

from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import tempfile
from typing import Any

from pydantic import ValidationError

from apps.enterprise_document.schemas.agent_rag_v1 import (
    GenerationReportSchema,
    VersionDecisionSchema,
)
from apps.enterprise_document.schemas.release_publication import (
    PublishedReleaseSchema,
    ReleaseEligibilityProofSchema,
    ReleaseManifestSchema,
)
from apps.enterprise_document.schemas.scheme_writer.document import SchemeDraftSchema
from apps.enterprise_document.schemas.scheme_writer.evaluation import HardGateResultSchema
from apps.enterprise_document.services.agent_rag_v1.artifact_store import (
    ArtifactIntegrityError,
    FileArtifactStore,
)
from apps.enterprise_document.services.writer_artifact_integrity import (
    validate_writer_section_artifact,
)
from core.persistence.interprocess_lock import InterProcessFileLock
from observability.trace_summary import canonical_sha256
from schemas.status import ExecutionStatus


class ReleasePublicationError(ValueError):
    """Base error for Stage F publication failures."""


class ReleaseEligibilityError(ReleasePublicationError):
    """The current artifact does not possess a valid Stage E release credential."""


class ReleaseConflictError(ReleasePublicationError):
    """The same release identity already points to a different request."""


class ReleaseVerificationError(ReleasePublicationError):
    """Committed release content or identity could not be verified."""


_STAGE_E_RELEASE_CHECKS = (
    "writer_gate_bound",
    "writer_binding_identity_fields_present",
    "writer_binding_section_identity_valid",
    "writer_execution_status_consistent",
    "writer_generation_allowed",
    "writer_human_review_clear",
    "writer_claim_audit_present",
    "writer_claim_audit_schema_valid",
    "writer_audit_section_identity_valid",
    "writer_audit_content_hash_valid",
    "writer_audit_binding_hash_valid",
    "writer_evidence_lineage_fields_present",
    "writer_audit_evidence_lineage_valid",
    "writer_audit_claim_boundary_valid",
    "writer_audit_claim_spans_valid",
    "writer_classifier_policy_valid",
    "writer_claim_audit_recomputed_valid",
    "writer_claim_audits_passed",
    "writer_required_qualifiers_applied",
    "writer_section_status_deliverable",
    "writer_formal_eligibility_recomputed",
    "writer_formal_deliverable_eligible",
)
_RELEASE_POLICY = "atomic_manifest_commit_v1"
_DOCUMENT_SCHEMA_VERSION = "release_document_payload_v1"


def _canonical_bytes(value: Any) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    ).encode("utf-8")


def _sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _aware_datetime(value: str | datetime | None) -> datetime:
    if value is None:
        return datetime.now(timezone.utc)
    if isinstance(value, datetime):
        result = value
    else:
        normalized = str(value).strip().replace("Z", "+00:00")
        result = datetime.fromisoformat(normalized)
    if result.tzinfo is None:
        raise ReleaseEligibilityError("release timestamp must be timezone-aware")
    return result


def _default_release_root() -> Path:
    configured = str(os.getenv("RAG_AGENT_RELEASE_ROOT") or "").strip()
    if configured:
        return Path(configured).expanduser().resolve()
    base = Path(tempfile.gettempdir()).resolve() / "rag-agent-release-store"
    pytest_test = str(os.getenv("PYTEST_CURRENT_TEST") or "").strip()
    if pytest_test:
        namespace = hashlib.sha256(pytest_test.encode("utf-8")).hexdigest()[:16]
        return base / f"pytest-{os.getpid()}-{namespace}"
    return base


class ReleasePublicationService:
    """Publish one exact Stage E-approved version using a manifest commit marker."""

    def __init__(
        self,
        artifact_store: FileArtifactStore,
        *,
        publisher: str = "SchemeGenerationStageService",
    ) -> None:
        self.artifact_store = artifact_store
        self.publisher = str(publisher).strip()
        if not self.publisher:
            raise ValueError("release publisher cannot be empty")

    @classmethod
    def from_environment(cls) -> "ReleasePublicationService":
        return cls(FileArtifactStore(_default_release_root()))

    @staticmethod
    def _stable_identity(
        *,
        task_id: str,
        document_id: str,
        document_version: str,
    ) -> tuple[str, str]:
        identity = {
            "task_id": task_id,
            "document_id": document_id,
            "document_version": document_version,
        }
        idempotency_key = canonical_sha256(identity)
        return f"release_{idempotency_key[:24]}", idempotency_key

    @staticmethod
    def _manifest_projection(manifest: ReleaseManifestSchema) -> dict[str, Any]:
        payload = manifest.model_dump(mode="json", exclude_none=True)
        payload.pop("manifest_payload_sha256", None)
        return payload

    @staticmethod
    def _manifest_ref(idempotency_key: str) -> str:
        return f"release://{idempotency_key}/manifest"

    @staticmethod
    def _document_ref(idempotency_key: str, payload_sha256: str) -> str:
        return f"release-blob://{idempotency_key}/document/{payload_sha256}"

    @staticmethod
    def _eligibility_ref(idempotency_key: str, proof_sha256: str) -> str:
        return f"release-blob://{idempotency_key}/eligibility/{proof_sha256}"

    def _lock_path(self, idempotency_key: str) -> Path:
        return self.artifact_store.root / ".release-locks" / f"{idempotency_key}.lock"

    def _request_sha256(
        self,
        *,
        task_id: str,
        run_id: str,
        document_id: str,
        draft_id: str,
        document_version: str,
        output_format: str,
        draft_sha256: str,
        hard_gate_sha256: str,
        generation_report_sha256: str,
        version_decision_sha256: str,
        publisher: str | None = None,
        release_policy: str = _RELEASE_POLICY,
    ) -> str:
        return canonical_sha256(
            {
                "task_id": task_id,
                "run_id": run_id,
                "document_id": document_id,
                "draft_id": draft_id,
                "document_version": document_version,
                "output_format": output_format,
                "draft_sha256": draft_sha256,
                "hard_gate_sha256": hard_gate_sha256,
                "generation_report_sha256": generation_report_sha256,
                "version_decision_sha256": version_decision_sha256,
                "publisher": publisher or self.publisher,
                "release_policy": release_policy,
            }
        )

    def build_eligibility_proof(
        self,
        *,
        task_id: str,
        run_id: str,
        document_version: str,
        draft: SchemeDraftSchema,
        hard_gate: HardGateResultSchema,
        generation_report: GenerationReportSchema,
        version_decision: VersionDecisionSchema,
        output_status: ExecutionStatus,
        release_request_sha256: str,
        verified_at: str | datetime | None = None,
    ) -> ReleaseEligibilityProofSchema:
        if not hard_gate.passed:
            raise ReleaseEligibilityError("document hard gate must pass before release")
        if output_status not in {
            ExecutionStatus.SUCCESS,
            ExecutionStatus.PARTIAL_SUCCESS,
        }:
            raise ReleaseEligibilityError("failed workflow output cannot be released")
        failed_stage_e_checks = [
            check_name
            for check_name in _STAGE_E_RELEASE_CHECKS
            if hard_gate.checks.get(check_name) is not True
        ]
        if failed_stage_e_checks:
            raise ReleaseEligibilityError(
                "Stage E release checks are incomplete or failed: "
                + ", ".join(failed_stage_e_checks)
            )
        if draft.task_id != task_id or draft.run_id != run_id:
            raise ReleaseEligibilityError("draft task/run identity mismatch")
        if not str(document_version).strip():
            raise ReleaseEligibilityError("document_version is required for release")
        if version_decision.next_version != document_version:
            raise ReleaseEligibilityError("version decision does not match release version")
        if generation_report.task_id != task_id or generation_report.run_id != run_id:
            raise ReleaseEligibilityError("generation report task/run identity mismatch")
        if generation_report.document_version != document_version:
            raise ReleaseEligibilityError("generation report version mismatch")

        section_artifact_sha256: dict[str, str] = {}
        binding_sha256: dict[str, str] = {}
        claim_audit_sha256: dict[str, str] = {}
        for section in draft.sections:
            result = validate_writer_section_artifact(
                section,
                document_id=draft.document_id,
                run_id=draft.run_id,
            )
            if not result.recomputed_formal_deliverable_eligible:
                raise ReleaseEligibilityError(
                    f"section is not formally deliverable: {section.section_id}; "
                    + ", ".join(result.failure_codes)
                )
            if result.binding is None or result.audit is None:
                raise ReleaseEligibilityError(
                    f"section release proof is incomplete: {section.section_id}"
                )
            section_artifact_sha256[section.section_id] = canonical_sha256(
                section.model_dump(mode="json", exclude_none=True)
            )
            binding_sha256[section.section_id] = str(
                result.metadata["expected_writer_gate_binding_sha256"]
            )
            claim_audit_sha256[section.section_id] = canonical_sha256(
                result.audit.model_dump(mode="json", exclude_none=True)
            )

        return ReleaseEligibilityProofSchema(
            task_id=task_id,
            run_id=run_id,
            document_id=draft.document_id,
            draft_id=draft.draft_id,
            document_version=document_version,
            release_request_sha256=release_request_sha256,
            draft_sha256=canonical_sha256(
                draft.model_dump(mode="json", exclude_none=True)
            ),
            hard_gate_sha256=canonical_sha256(
                hard_gate.model_dump(mode="json", exclude_none=True)
            ),
            generation_report_sha256=canonical_sha256(
                generation_report.model_dump(mode="json", exclude_none=True)
            ),
            version_decision_sha256=canonical_sha256(
                version_decision.model_dump(mode="json", exclude_none=True)
            ),
            section_artifact_sha256=section_artifact_sha256,
            writer_gate_binding_sha256=binding_sha256,
            claim_audit_sha256=claim_audit_sha256,
            verified_at=_aware_datetime(verified_at),
            metadata={
                "stage_e_release_checks": list(_STAGE_E_RELEASE_CHECKS),
                "section_count": len(draft.sections),
            },
        )

    def _build_manifest(
        self,
        *,
        release_id: str,
        idempotency_key: str,
        request_sha256: str,
        task_id: str,
        run_id: str,
        draft: SchemeDraftSchema,
        document_version: str,
        document_artifact: Any,
        eligibility_artifact: Any,
        document_sha256: str,
        proof_sha256: str,
        published_at: datetime,
    ) -> ReleaseManifestSchema:
        manifest = ReleaseManifestSchema.model_validate(
            {
                "schema_version": "release_manifest_v1",
                "release_id": release_id,
                "idempotency_key": idempotency_key,
                "release_request_sha256": request_sha256,
                "task_id": task_id,
                "run_id": run_id,
                "document_id": draft.document_id,
                "draft_id": draft.draft_id,
                "document_version": document_version,
                "release_status": "published",
                "document_artifact": document_artifact.model_dump(mode="json"),
                "eligibility_artifact": eligibility_artifact.model_dump(mode="json"),
                "release_payload_sha256": document_sha256,
                "eligibility_proof_sha256": proof_sha256,
                "manifest_payload_sha256": "0" * 64,
                "publisher": self.publisher,
                "published_at": published_at.isoformat(),
                "immutable": True,
                "metadata": {
                    "stage": "Stage F.1",
                    "release_policy": _RELEASE_POLICY,
                },
            }
        )
        return manifest.model_copy(
            update={
                "manifest_payload_sha256": canonical_sha256(
                    self._manifest_projection(manifest)
                )
            },
            deep=True,
        )

    def _verify_candidate_payloads(
        self,
        manifest: ReleaseManifestSchema,
        document_payload: Any,
        proof_payload: Any,
    ) -> None:
        if not isinstance(document_payload, dict):
            raise ReleaseVerificationError("released document payload must be an object")
        if _sha256_bytes(_canonical_bytes(document_payload)) != manifest.release_payload_sha256:
            raise ReleaseVerificationError("released document payload hash mismatch")
        if _sha256_bytes(_canonical_bytes(proof_payload)) != manifest.eligibility_proof_sha256:
            raise ReleaseVerificationError("release eligibility proof hash mismatch")
        try:
            proof = ReleaseEligibilityProofSchema.model_validate(proof_payload)
            stored_draft = SchemeDraftSchema.model_validate(document_payload["draft"])
        except (ValidationError, KeyError, TypeError, ValueError) as exc:
            raise ReleaseVerificationError(str(exc)) from exc

        expected_document_ref = self._document_ref(
            manifest.idempotency_key,
            manifest.release_payload_sha256,
        )
        expected_proof_ref = self._eligibility_ref(
            manifest.idempotency_key,
            manifest.eligibility_proof_sha256,
        )
        if manifest.document_artifact.artifact_ref != expected_document_ref:
            raise ReleaseVerificationError("document artifact ref identity mismatch")
        if manifest.eligibility_artifact.artifact_ref != expected_proof_ref:
            raise ReleaseVerificationError("eligibility artifact ref identity mismatch")
        if document_payload.get("schema_version") != _DOCUMENT_SCHEMA_VERSION:
            raise ReleaseVerificationError("released document schema mismatch")
        identities = (
            ("task_id", manifest.task_id),
            ("run_id", manifest.run_id),
            ("document_id", manifest.document_id),
            ("draft_id", manifest.draft_id),
            ("document_version", manifest.document_version),
        )
        for field_name, expected in identities:
            if document_payload.get(field_name) != expected:
                raise ReleaseVerificationError(
                    f"released document {field_name} identity mismatch"
                )
            if getattr(proof, field_name) != expected:
                raise ReleaseVerificationError(
                    f"eligibility proof {field_name} identity mismatch"
                )
        if stored_draft.task_id != manifest.task_id:
            raise ReleaseVerificationError("draft task identity mismatch")
        if stored_draft.run_id != manifest.run_id:
            raise ReleaseVerificationError("draft run identity mismatch")
        if stored_draft.document_id != manifest.document_id:
            raise ReleaseVerificationError("draft document identity mismatch")
        if stored_draft.draft_id != manifest.draft_id:
            raise ReleaseVerificationError("draft identity mismatch")
        if proof.draft_sha256 != canonical_sha256(document_payload["draft"]):
            raise ReleaseVerificationError("eligibility proof draft hash mismatch")
        if document_payload.get("release_request_sha256") != manifest.release_request_sha256:
            raise ReleaseVerificationError("document request fingerprint mismatch")
        if proof.release_request_sha256 != manifest.release_request_sha256:
            raise ReleaseVerificationError("eligibility request fingerprint mismatch")

        section_sha256: dict[str, str] = {}
        binding_sha256: dict[str, str] = {}
        claim_audit_sha256: dict[str, str] = {}
        for section in stored_draft.sections:
            try:
                result = validate_writer_section_artifact(
                    section,
                    document_id=stored_draft.document_id,
                    run_id=stored_draft.run_id,
                )
            except ValueError as exc:
                raise ReleaseVerificationError(str(exc)) from exc
            if not result.recomputed_formal_deliverable_eligible:
                raise ReleaseVerificationError(
                    f"stored section is not formally deliverable: {section.section_id}"
                )
            if result.binding is None or result.audit is None:
                raise ReleaseVerificationError(
                    f"stored section release proof is incomplete: {section.section_id}"
                )
            section_sha256[section.section_id] = canonical_sha256(
                section.model_dump(mode="json", exclude_none=True)
            )
            binding_sha256[section.section_id] = str(
                result.metadata["expected_writer_gate_binding_sha256"]
            )
            claim_audit_sha256[section.section_id] = canonical_sha256(
                result.audit.model_dump(mode="json", exclude_none=True)
            )
        if proof.section_artifact_sha256 != section_sha256:
            raise ReleaseVerificationError("section artifact hash set mismatch")
        if proof.writer_gate_binding_sha256 != binding_sha256:
            raise ReleaseVerificationError("writer binding hash set mismatch")
        if proof.claim_audit_sha256 != claim_audit_sha256:
            raise ReleaseVerificationError("claim audit hash set mismatch")

        release_policy = str(manifest.metadata.get("release_policy") or "")
        if release_policy != _RELEASE_POLICY:
            raise ReleaseVerificationError("release policy identity mismatch")
        expected_request_sha256 = self._request_sha256(
            task_id=manifest.task_id,
            run_id=manifest.run_id,
            document_id=manifest.document_id,
            draft_id=manifest.draft_id,
            document_version=manifest.document_version,
            output_format=str(document_payload.get("output_format") or ""),
            draft_sha256=proof.draft_sha256,
            hard_gate_sha256=proof.hard_gate_sha256,
            generation_report_sha256=proof.generation_report_sha256,
            version_decision_sha256=proof.version_decision_sha256,
            publisher=manifest.publisher,
            release_policy=release_policy,
        )
        if expected_request_sha256 != manifest.release_request_sha256:
            raise ReleaseVerificationError("release request fingerprint mismatch")

    def _read_committed(
        self,
        *,
        manifest_ref: str,
        task_id: str,
        document_version: str,
    ) -> PublishedReleaseSchema | None:
        try:
            reference, payload = self.artifact_store.get_json_with_reference(
                manifest_ref,
                task_id=task_id,
                input_snapshot_version=document_version,
            )
        except FileNotFoundError:
            return None
        except ArtifactIntegrityError as exc:
            raise ReleaseVerificationError(str(exc)) from exc
        try:
            manifest = ReleaseManifestSchema.model_validate(payload)
            publication = PublishedReleaseSchema(
                manifest=manifest,
                manifest_artifact=reference,
                verified_after_write=True,
                idempotent_replay=False,
            )
        except (ValidationError, ValueError) as exc:
            raise ReleaseVerificationError(str(exc)) from exc
        self.verify(publication)
        return publication

    def publish(
        self,
        *,
        task_id: str,
        run_id: str,
        document_version: str,
        draft: SchemeDraftSchema,
        output_format: str,
        hard_gate: HardGateResultSchema,
        generation_report: GenerationReportSchema,
        version_decision: VersionDecisionSchema,
        output_status: ExecutionStatus,
        published_at: str | datetime | None = None,
    ) -> PublishedReleaseSchema:
        normalized_format = str(output_format).strip() or "markdown"
        draft_payload = draft.model_dump(mode="json", exclude_none=True)
        hard_gate_payload = hard_gate.model_dump(mode="json", exclude_none=True)
        generation_report_payload = generation_report.model_dump(
            mode="json",
            exclude_none=True,
        )
        version_decision_payload = version_decision.model_dump(
            mode="json",
            exclude_none=True,
        )
        draft_sha256 = canonical_sha256(draft_payload)
        hard_gate_sha256 = canonical_sha256(hard_gate_payload)
        generation_report_sha256 = canonical_sha256(generation_report_payload)
        version_decision_sha256 = canonical_sha256(version_decision_payload)
        request_sha256 = self._request_sha256(
            task_id=task_id,
            run_id=run_id,
            document_id=draft.document_id,
            draft_id=draft.draft_id,
            document_version=document_version,
            output_format=normalized_format,
            draft_sha256=draft_sha256,
            hard_gate_sha256=hard_gate_sha256,
            generation_report_sha256=generation_report_sha256,
            version_decision_sha256=version_decision_sha256,
        )
        release_id, idempotency_key = self._stable_identity(
            task_id=task_id,
            document_id=draft.document_id,
            document_version=document_version,
        )
        manifest_ref = self._manifest_ref(idempotency_key)

        with InterProcessFileLock(self._lock_path(idempotency_key)):
            existing = self._read_committed(
                manifest_ref=manifest_ref,
                task_id=task_id,
                document_version=document_version,
            )
            if existing is not None:
                if existing.manifest.release_request_sha256 == request_sha256:
                    return existing.model_copy(
                        update={"idempotent_replay": True},
                        deep=True,
                    )
                raise ReleaseConflictError(
                    "same release identity already contains different content or request"
                )

            committed_at = _aware_datetime(published_at)
            proof = self.build_eligibility_proof(
                task_id=task_id,
                run_id=run_id,
                document_version=document_version,
                draft=draft,
                hard_gate=hard_gate,
                generation_report=generation_report,
                version_decision=version_decision,
                output_status=output_status,
                release_request_sha256=request_sha256,
                verified_at=committed_at,
            )
            if proof.draft_sha256 != draft_sha256:
                raise ReleaseVerificationError("request draft hash changed during publish")
            if proof.hard_gate_sha256 != hard_gate_sha256:
                raise ReleaseVerificationError("request hard gate hash changed during publish")
            if proof.generation_report_sha256 != generation_report_sha256:
                raise ReleaseVerificationError(
                    "request generation report hash changed during publish"
                )
            if proof.version_decision_sha256 != version_decision_sha256:
                raise ReleaseVerificationError(
                    "request version decision hash changed during publish"
                )

            document_payload = {
                "schema_version": _DOCUMENT_SCHEMA_VERSION,
                "release_request_sha256": request_sha256,
                "task_id": task_id,
                "run_id": run_id,
                "document_id": draft.document_id,
                "draft_id": draft.draft_id,
                "document_version": document_version,
                "output_format": normalized_format,
                "draft": draft_payload,
            }
            proof_payload = proof.model_dump(mode="json", exclude_none=True)
            document_sha256 = _sha256_bytes(_canonical_bytes(document_payload))
            proof_sha256 = _sha256_bytes(_canonical_bytes(proof_payload))
            document_ref = self._document_ref(idempotency_key, document_sha256)
            proof_ref = self._eligibility_ref(idempotency_key, proof_sha256)
            try:
                document_artifact = self.artifact_store.put_json(
                    document_payload,
                    artifact_type="released_document",
                    schema_version=_DOCUMENT_SCHEMA_VERSION,
                    created_by=self.publisher,
                    task_id=task_id,
                    attempt_id=run_id,
                    input_snapshot_version=document_version,
                    artifact_ref=document_ref,
                )
                eligibility_artifact = self.artifact_store.put_json(
                    proof_payload,
                    artifact_type="release_eligibility_proof",
                    schema_version=proof.schema_version,
                    created_by=self.publisher,
                    task_id=task_id,
                    attempt_id=run_id,
                    input_snapshot_version=document_version,
                    artifact_ref=proof_ref,
                )
            except ArtifactIntegrityError as exc:
                raise ReleaseConflictError(str(exc)) from exc

            manifest = self._build_manifest(
                release_id=release_id,
                idempotency_key=idempotency_key,
                request_sha256=request_sha256,
                task_id=task_id,
                run_id=run_id,
                draft=draft,
                document_version=document_version,
                document_artifact=document_artifact,
                eligibility_artifact=eligibility_artifact,
                document_sha256=document_sha256,
                proof_sha256=proof_sha256,
                published_at=committed_at,
            )
            self._verify_candidate_payloads(
                manifest,
                document_payload,
                proof_payload,
            )
            try:
                manifest_artifact = self.artifact_store.put_json(
                    manifest.model_dump(mode="json", exclude_none=True),
                    artifact_type="release_manifest",
                    schema_version=manifest.schema_version,
                    created_by=self.publisher,
                    task_id=task_id,
                    attempt_id=run_id,
                    input_snapshot_version=document_version,
                    artifact_ref=manifest_ref,
                )
            except ArtifactIntegrityError as exc:
                raise ReleaseConflictError(str(exc)) from exc

            publication = PublishedReleaseSchema(
                manifest=manifest,
                manifest_artifact=manifest_artifact,
                verified_after_write=True,
                idempotent_replay=False,
            )
            self.verify(publication)
            return publication

    def verify(self, publication: PublishedReleaseSchema) -> bool:
        manifest = publication.manifest
        expected_release_id, expected_idempotency_key = self._stable_identity(
            task_id=manifest.task_id,
            document_id=manifest.document_id,
            document_version=manifest.document_version,
        )
        if manifest.release_id != expected_release_id:
            raise ReleaseVerificationError("release id identity mismatch")
        if manifest.idempotency_key != expected_idempotency_key:
            raise ReleaseVerificationError("release idempotency key mismatch")
        expected_manifest_ref = self._manifest_ref(expected_idempotency_key)
        if publication.manifest_artifact.artifact_ref != expected_manifest_ref:
            raise ReleaseVerificationError("canonical manifest ref identity mismatch")
        try:
            stored_manifest_ref, stored_manifest_payload = (
                self.artifact_store.get_json_with_reference(
                    expected_manifest_ref,
                    task_id=manifest.task_id,
                    attempt_id=manifest.run_id,
                    input_snapshot_version=manifest.document_version,
                )
            )
            stored_document_ref, stored_document = (
                self.artifact_store.get_json_with_reference(
                    manifest.document_artifact.artifact_ref,
                    task_id=manifest.task_id,
                    attempt_id=manifest.run_id,
                    input_snapshot_version=manifest.document_version,
                )
            )
            stored_proof_ref, stored_proof = (
                self.artifact_store.get_json_with_reference(
                    manifest.eligibility_artifact.artifact_ref,
                    task_id=manifest.task_id,
                    attempt_id=manifest.run_id,
                    input_snapshot_version=manifest.document_version,
                )
            )
        except (FileNotFoundError, ArtifactIntegrityError) as exc:
            raise ReleaseVerificationError(str(exc)) from exc
        if stored_manifest_ref != publication.manifest_artifact:
            raise ReleaseVerificationError("manifest artifact reference differs from store")
        if stored_document_ref != manifest.document_artifact:
            raise ReleaseVerificationError("document artifact reference differs from manifest")
        if stored_proof_ref != manifest.eligibility_artifact:
            raise ReleaseVerificationError("eligibility artifact reference differs from manifest")
        try:
            stored_manifest = ReleaseManifestSchema.model_validate(
                stored_manifest_payload
            )
        except (ValidationError, ValueError) as exc:
            raise ReleaseVerificationError(str(exc)) from exc
        if stored_manifest != manifest:
            raise ReleaseVerificationError("stored release manifest differs from credential")
        expected_manifest_hash = canonical_sha256(
            self._manifest_projection(stored_manifest)
        )
        if expected_manifest_hash != stored_manifest.manifest_payload_sha256:
            raise ReleaseVerificationError("release manifest payload hash mismatch")
        self._verify_candidate_payloads(
            stored_manifest,
            stored_document,
            stored_proof,
        )
        return True


__all__ = [
    "ReleaseConflictError",
    "ReleaseEligibilityError",
    "ReleasePublicationError",
    "ReleasePublicationService",
    "ReleaseVerificationError",
]
