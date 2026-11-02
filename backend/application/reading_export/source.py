"""Read one exact published Target Markdown release without opening business stores.

The release files prove content identity and the bindings saved by the publisher.
The caller supplies the exact terminal receipt and owns its provenance and local
administrative authorization. This reader never establishes checkpoint provenance.
"""

from __future__ import annotations

import base64
from dataclasses import dataclass
from hashlib import sha256
import json
from pathlib import Path
import re
from typing import Any, Mapping

from application.terminal_outcome_schema import TerminalOutcomeResultV1
from application.terminal_status_view import project_terminal_status
from apps.enterprise_document.schemas.artifact_reference import ArtifactReferenceSchema
from apps.enterprise_document.schemas.post_write.currentness import (
    ActiveDocumentBindingProjectionSchema,
)
from apps.enterprise_document.schemas.post_write.foundation import ArtifactScope
from apps.enterprise_document.schemas.post_write.release import (
    PostWriteReleaseInputProofSchema,
    PostWriteReleaseManifestSchema,
)
from apps.enterprise_document.services.agent_rag_v1.post_write_citation_projection import (
    DocumentCitationProjection,
)
from apps.enterprise_document.services.agent_rag_v1.post_write_composition import (
    TargetPostWriteComposition,
)
from core.hashing import canonical_sha256


_RELEASE_ID = re.compile(r"post-write-release://sha256/([0-9a-f]{64})\Z")
_MARKDOWN_RENDERER = "target-markdown-renderer-v1"
_MARKDOWN_ADAPTER = "target-markdown-visible-v1"
TERMINAL_OUTCOME_SOURCE = "caller_supplied_exact_receipt"


class ReadingSourceError(ValueError):
    """One exact release cannot be verified for a reading export."""


class TerminalOutcomeReceiptRequired(ReadingSourceError):
    """The caller did not supply the exact final terminal receipt."""


class UnsupportedReadingExportFormat(ReadingSourceError):
    """The published output is outside the V1 Markdown reading contract."""


class ReadingSourceIntegrityError(ReadingSourceError):
    """A publication envelope, binding, or terminal receipt is inconsistent."""


@dataclass(frozen=True)
class ReleaseReadPackage:
    manifest: PostWriteReleaseManifestSchema
    proof: PostWriteReleaseInputProofSchema
    payload_bytes: bytes
    visible_payload: dict[str, Any]
    citation_projection: dict[str, Any]
    status_view: dict[str, str | bool | None]
    release_manifest_artifact: ArtifactReferenceSchema
    source_paths: tuple[Path, Path, Path]  # manifest, proof, payload
    terminal_outcome_result_id: str
    terminal_outcome_source: str = TERMINAL_OUTCOME_SOURCE


def _unique_pairs(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ReadingSourceIntegrityError("duplicate publication JSON key")
        result[key] = value
    return result


def _reject_constant(value: str) -> None:
    raise ReadingSourceIntegrityError(f"invalid publication JSON constant: {value}")


def _json_object(content: bytes, *, label: str) -> dict[str, Any]:
    try:
        value = json.loads(
            content.decode("utf-8"),
            object_pairs_hook=_unique_pairs,
            parse_constant=_reject_constant,
        )
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ReadingSourceIntegrityError(f"{label} is not UTF-8 JSON") from exc
    if not isinstance(value, dict):
        raise ReadingSourceIntegrityError(f"{label} must be a JSON object")
    return value


def _envelope_path(root: Path, artifact_ref: str) -> Path:
    digest = sha256(artifact_ref.encode("utf-8")).hexdigest()
    return root / digest[:2] / f"{digest}.json"


def _read_envelope(
    root: Path,
    *,
    artifact_ref: str,
    artifact_type: str,
    schema_version: str,
    content_encoding: str,
    expected_reference: ArtifactReferenceSchema | None = None,
    task_id: str | None = None,
    run_id: str | None = None,
) -> tuple[ArtifactReferenceSchema, bytes, Path]:
    path = _envelope_path(root, artifact_ref)
    if not path.is_file():
        raise ReadingSourceIntegrityError(f"publication artifact missing: {artifact_ref}")
    envelope = _json_object(path.read_bytes(), label="publication envelope")
    if set(envelope) != {"reference", "content_encoding", "content_base64"}:
        raise ReadingSourceIntegrityError("publication envelope fields differ")
    if envelope["content_encoding"] != content_encoding:
        raise ReadingSourceIntegrityError("publication content encoding differs")
    if not isinstance(envelope["content_base64"], str):
        raise ReadingSourceIntegrityError("publication content_base64 must be text")
    raw_reference = envelope["reference"]
    if not isinstance(raw_reference, dict) or (
        raw_reference.get("artifact_ref") != artifact_ref
        or raw_reference.get("artifact_type") != artifact_type
        or raw_reference.get("schema_version") != schema_version
        or (task_id is not None and raw_reference.get("task_id") != task_id)
        or (run_id is not None and raw_reference.get("run_id") != run_id)
        or (expected_reference is not None
            and raw_reference != expected_reference.model_dump(mode="json"))
    ):
        raise ReadingSourceIntegrityError("publication envelope raw reference differs")
    try:
        reference = ArtifactReferenceSchema.model_validate(raw_reference)
        content = base64.b64decode(envelope["content_base64"], validate=True)
    except Exception as exc:
        raise ReadingSourceIntegrityError("publication envelope reference/base64 invalid") from exc
    if (
        reference.artifact_ref != artifact_ref
        or reference.artifact_type != artifact_type
        or reference.schema_version != schema_version
        or (task_id is not None and reference.task_id != task_id)
        or (run_id is not None and reference.run_id != run_id)
        or (expected_reference is not None and reference != expected_reference)
        or raw_reference.get("content_hash") != "sha256:" + sha256(content).hexdigest()
        or type(raw_reference.get("size_bytes")) is not int
        or raw_reference["size_bytes"] != len(content)
        or reference.content_hash != "sha256:" + sha256(content).hexdigest()
        or reference.size_bytes != len(content)
    ):
        raise ReadingSourceIntegrityError("publication envelope identity/hash differs")
    return reference, content, path


def _validate_release_cross_bindings(
    manifest: PostWriteReleaseManifestSchema,
    proof: PostWriteReleaseInputProofSchema,
    *,
    key: str,
    manifest_artifact: ArtifactReferenceSchema,
    payload_bytes: bytes,
) -> None:
    payload_sha256 = sha256(payload_bytes).hexdigest()
    currentness = proof.release_time_currentness_proof
    active_projection = currentness.active_projection
    expected_identity = currentness.expected_active_entry_identity
    if (
        not isinstance(active_projection, ActiveDocumentBindingProjectionSchema)
        or active_projection.run_id != manifest.run_id
        or active_projection.artifact_state_ref != manifest.document_artifact_ref
        or expected_identity.run_id != manifest.run_id
        or expected_identity.document_id != active_projection.document_id
        or expected_identity.artifact_state_ref != manifest.document_artifact_ref
    ):
        raise ReadingSourceIntegrityError("release active Document identity differs")
    if (
        manifest.release_id != f"post-write-release://sha256/{key}"
        or manifest.idempotency_key != key
        or manifest_artifact.task_id != manifest.task_id
        or manifest_artifact.run_id != manifest.run_id
        or manifest_artifact.created_by != manifest.publisher
        or manifest_artifact.input_snapshot_version
        != str(manifest.document_artifact_ref.artifact_version)
        or manifest.document_artifact_ref.artifact_scope is not ArtifactScope.DOCUMENT
        or manifest.release_payload_sha256 != payload_sha256
        or manifest.release_payload_artifact.artifact_ref
        != f"target-release://{key}/payload/{payload_sha256}"
        or manifest.release_input_proof_artifact.artifact_ref
        != f"target-release://{key}/proof/{proof.proof_sha256}"
    ):
        raise ReadingSourceIntegrityError("release manifest identity differs")
    if (
        proof.proof_sha256 != manifest.release_input_proof_sha256
        or proof.release_contract_version != manifest.release_contract_version
        or proof.release_request_sha256 != manifest.request_sha256
        or proof.run_id != manifest.run_id
        or proof.run_completion_assessment_ref != manifest.run_completion_assessment_ref
        or proof.run_lifecycle_revision != manifest.run_lifecycle_revision
        or proof.document_artifact_ref != manifest.document_artifact_ref
        or proof.post_write_control_decision_ref
        != manifest.post_write_control_decision_ref
        or proof.enforcement_action != manifest.enforcement_action_passthrough
        or proof.quality_state_passthrough != manifest.quality_state_passthrough
        or proof.assembly_input_manifest_ref != manifest.assembly_input_manifest_ref
        or proof.blocking_fact_refs != manifest.blocking_fact_refs
        or proof.unresolved_reason_refs != manifest.unresolved_reason_refs
        or proof.release_payload_artifact_ref != manifest.release_payload_artifact
        or proof.release_payload_sha256 != manifest.release_payload_sha256
        or proof.validated_output_binding_sha256
        != manifest.validated_output_binding_sha256
        or proof.output_format != manifest.output_format
        or proof.renderer_contract_version != manifest.renderer_contract_version
    ):
        raise ReadingSourceIntegrityError("release manifest/proof binding differs")
    for reference in (
        manifest.release_input_proof_artifact,
        manifest.release_payload_artifact,
    ):
        if (
            reference.task_id != manifest.task_id
            or reference.run_id != manifest.run_id
            or reference.created_by != manifest.publisher
            or reference.input_snapshot_version
            != str(manifest.document_artifact_ref.artifact_version)
        ):
            raise ReadingSourceIntegrityError("release child artifact identity differs")


def _validate_visible_conformance(
    manifest: PostWriteReleaseManifestSchema,
    proof: PostWriteReleaseInputProofSchema,
    visible_payload: dict[str, Any],
) -> None:
    if visible_payload.get("document_logical_id") != manifest.document_logical_id:
        raise ReadingSourceIntegrityError("published Document logical identity differs")
    conformance = proof.output_conformance_proof
    ref = conformance.visible_projection_ref
    if (
        conformance.adapter_contract_version != _MARKDOWN_ADAPTER
        or ref.projection_schema_version != "artifact_visible_projection_v1"
        or ref.projection_contract_version != "artifact_visible_projection_v1"
    ):
        raise UnsupportedReadingExportFormat("unsupported Markdown visible projection version")
    projection_sha256 = canonical_sha256(
        {
            "schema_version": ref.projection_schema_version,
            "projection_contract_version": ref.projection_contract_version,
            "artifact_scope": ArtifactScope.DOCUMENT.value,
            "logical_target_ref": manifest.document_logical_id,
            "visible_payload": visible_payload,
        }
    )
    if (
        ref.projection_id != f"artifact-visible://sha256/{projection_sha256}"
        or ref.projection_sha256 != projection_sha256
        or conformance.visible_projection_sha256 != projection_sha256
        or conformance.extracted_visible_projection_sha256 != projection_sha256
    ):
        raise ReadingSourceIntegrityError("published Markdown visible projection differs")


def _validate_terminal(
    terminal_outcome: TerminalOutcomeResultV1 | Mapping[str, Any] | None,
    manifest: PostWriteReleaseManifestSchema,
) -> tuple[TerminalOutcomeResultV1, dict[str, str | bool | None]]:
    if terminal_outcome is None:
        raise TerminalOutcomeReceiptRequired("TERMINAL_OUTCOME_RECEIPT_REQUIRED")
    raw: Any = (
        terminal_outcome.model_dump(mode="json")
        if isinstance(terminal_outcome, TerminalOutcomeResultV1)
        else terminal_outcome
    )
    try:
        outcome = TerminalOutcomeResultV1.model_validate(raw)
    except Exception as exc:
        raise ReadingSourceIntegrityError("TerminalOutcome receipt schema/result_id invalid") from exc
    if (
        outcome.task_id != manifest.task_id
        or outcome.run_id != manifest.run_id
        or outcome.publication_exists is not True
        or outcome.publication_ref != manifest.release_id
        or outcome.control_action != manifest.enforcement_action_passthrough
        or outcome.quality_state != manifest.quality_state_passthrough
    ):
        raise ReadingSourceIntegrityError("TerminalOutcome receipt/release binding differs")
    status_view = project_terminal_status(
        outcome,
        source_mode="verified_target",
        expected_task_id=manifest.task_id,
        expected_run_id=manifest.run_id,
    )
    return outcome, status_view


def load_published_markdown(
    *,
    target_release_root: Path,
    release_id: str,
    terminal_outcome: TerminalOutcomeResultV1 | Mapping[str, Any] | None,
) -> ReleaseReadPackage:
    """Read a published Markdown payload and bind it to a supplied final receipt.

    Only deterministic artifact envelope paths under ``target_release_root`` are
    read. No store/service constructor, SQLite connection, or recovery is used.
    """
    match = _RELEASE_ID.fullmatch(release_id) if isinstance(release_id, str) else None
    if match is None:
        raise ReadingSourceIntegrityError("release_id format invalid")
    if terminal_outcome is None:
        raise TerminalOutcomeReceiptRequired("TERMINAL_OUTCOME_RECEIPT_REQUIRED")
    key = match.group(1)
    root = Path(target_release_root)
    manifest_artifact, manifest_bytes, manifest_path = _read_envelope(
        root,
        artifact_ref=f"target-release://{key}/manifest",
        artifact_type="post_write_release_manifest",
        schema_version="post_write_release_manifest_v1",
        content_encoding="json-utf8",
    )
    try:
        manifest = PostWriteReleaseManifestSchema.model_validate(
            _json_object(manifest_bytes, label="release manifest")
        )
    except Exception as exc:
        raise ReadingSourceIntegrityError("release manifest schema/hash invalid") from exc
    if manifest.release_id != release_id or manifest.idempotency_key != key:
        raise ReadingSourceIntegrityError("release_id/manifest identity differs")
    if (
        manifest.output_format != "markdown"
        or manifest.renderer_contract_version != _MARKDOWN_RENDERER
    ):
        raise UnsupportedReadingExportFormat("UNSUPPORTED_READING_EXPORT_FORMAT")
    proof_artifact, proof_bytes, proof_path = _read_envelope(
        root,
        artifact_ref=manifest.release_input_proof_artifact.artifact_ref,
        artifact_type="post_write_release_input_proof",
        schema_version="post_write_release_input_proof_v1",
        content_encoding="json-utf8",
        expected_reference=manifest.release_input_proof_artifact,
        task_id=manifest.task_id,
        run_id=manifest.run_id,
    )
    try:
        proof = PostWriteReleaseInputProofSchema.model_validate(
            _json_object(proof_bytes, label="release input proof")
        )
    except Exception as exc:
        raise ReadingSourceIntegrityError("release input proof schema/hash invalid") from exc
    payload_artifact, payload_bytes, payload_path = _read_envelope(
        root,
        artifact_ref=manifest.release_payload_artifact.artifact_ref,
        artifact_type="post_write_release_payload",
        schema_version="post_write_release_payload_v1",
        content_encoding="binary",
        expected_reference=manifest.release_payload_artifact,
        task_id=manifest.task_id,
        run_id=manifest.run_id,
    )
    # The stored references are also compared in full, including their hashes,
    # version, publisher, snapshot version and immutable reference metadata.
    if proof_artifact != manifest.release_input_proof_artifact or payload_artifact != manifest.release_payload_artifact:
        raise ReadingSourceIntegrityError("release child artifact reference differs")
    _validate_release_cross_bindings(
        manifest, proof, key=key,
        manifest_artifact=manifest_artifact, payload_bytes=payload_bytes,
    )
    try:
        visible_payload = TargetPostWriteComposition._extract_markdown_visible_payload(
            payload_bytes
        )
    except Exception as exc:
        raise ReadingSourceIntegrityError("published Markdown extraction failed") from exc
    _validate_visible_conformance(manifest, proof, visible_payload)
    citation_data = visible_payload.get("citation_projection")
    try:
        citation = DocumentCitationProjection.model_validate(citation_data)
    except Exception as exc:
        raise ReadingSourceIntegrityError("final Document citation projection invalid") from exc
    if (
        citation.catalog.scope != "DOCUMENT"
        or any(catalog.scope != "SECTION" for catalog in citation.source_catalogs)
        or not isinstance(citation_data, dict)
    ):
        raise ReadingSourceIntegrityError("final Document citation catalog scope invalid")
    outcome, status_view = _validate_terminal(terminal_outcome, manifest)
    return ReleaseReadPackage(
        manifest=manifest,
        proof=proof,
        payload_bytes=payload_bytes,
        visible_payload=visible_payload,
        citation_projection=citation_data,
        status_view=status_view,
        release_manifest_artifact=manifest_artifact,
        source_paths=(manifest_path, proof_path, payload_path),
        terminal_outcome_result_id=outcome.result_id,
    )


__all__ = [
    "ReadingSourceError",
    "ReadingSourceIntegrityError",
    "ReleaseReadPackage",
    "TERMINAL_OUTCOME_SOURCE",
    "TerminalOutcomeReceiptRequired",
    "UnsupportedReadingExportFormat",
    "load_published_markdown",
]
