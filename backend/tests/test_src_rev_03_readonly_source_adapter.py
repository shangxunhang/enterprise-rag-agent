"""SRCREV-03 Option A: independently built file publication/terminal fixtures."""

from __future__ import annotations

import base64
from copy import deepcopy
from dataclasses import dataclass
from datetime import datetime, timezone
from hashlib import sha256
import json
from pathlib import Path
import sqlite3
from typing import Mapping

import pytest

from application.reading_export.source import (
    ReadingSourceIntegrityError,
    TerminalOutcomeReceiptRequired,
    UnsupportedReadingExportFormat,
    load_published_markdown,
)
from application.terminal_outcome_schema import TerminalOutcomeResultV1
from apps.enterprise_document.schemas.agent_rag_v1.lifecycle import (
    RunCompletionAssessmentRefSchema,
)
from apps.enterprise_document.schemas.artifact_reference import ArtifactReferenceSchema
from apps.enterprise_document.schemas.post_write.control import (
    ArtifactUsabilityIntegrityProofSchema,
)
from apps.enterprise_document.schemas.post_write.currentness import (
    ActiveDocumentBindingProjectionSchema,
    CurrentnessProofSchema,
)
from apps.enterprise_document.schemas.post_write.document import (
    AssemblyInputManifestRefSchema,
    DocumentEvidenceSnapshotRefSchema,
)
from apps.enterprise_document.schemas.post_write.entry import (
    PostWriteEntryIdentitySchema,
    PostWriteEvidenceBindingRef,
)
from apps.enterprise_document.schemas.post_write.foundation import (
    ArtifactOutputConformanceProofSchema,
    ArtifactStateRefSchema,
    ArtifactVisibleProjectionRefSchema,
    PostWriteControlDecisionRefSchema,
    PostWriteEntryRefSchema,
    QualityDeliveryContractRefSchema,
)
from apps.enterprise_document.schemas.post_write.release import (
    PostWriteReleaseInputProofSchema,
    PostWriteReleaseManifestSchema,
)
from apps.enterprise_document.schemas.post_write.requirement import RequirementRefSchema
from apps.enterprise_document.services.agent_rag_v1.post_write_citation_projection import (
    CitationAliasCatalog,
    CitationAliasEntry,
    DocumentCitationProjection,
)
from apps.enterprise_document.services.agent_rag_v1.post_write_composition import (
    TargetPostWriteComposition,
)
from core.hashing import canonical_sha256


TASK_ID = "task-src03-fixture"
RUN_ID = "run-src03-fixture"
DOCUMENT_ID = "document-src03-fixture"
DOCUMENT_LOGICAL_ID = f"document-logical:{RUN_ID}:{DOCUMENT_ID}"
PUBLISHER = "src03-fixture-publisher"
H = "a" * 64


@dataclass(frozen=True)
class ReleaseFixture:
    release_root: Path
    release_id: str
    terminal_outcome: TerminalOutcomeResultV1
    manifest: PostWriteReleaseManifestSchema
    proof: PostWriteReleaseInputProofSchema
    payload_bytes: bytes
    manifest_path: Path
    proof_path: Path
    payload_path: Path


def _hashed(schema, field: str, **values):
    placeholder = schema.model_construct(**values, **{field: "0" * 64})
    raw = placeholder.model_dump(mode="json", warnings=False)
    raw.pop(field)
    return schema(**values, **{field: canonical_sha256(raw)})


def _terminal(**values) -> TerminalOutcomeResultV1:
    placeholder = TerminalOutcomeResultV1.model_construct(
        **values, result_id="sha256:" + "0" * 64
    )
    raw = placeholder.model_dump(mode="json", exclude={"result_id"}, warnings=False)
    return TerminalOutcomeResultV1(
        **values, result_id="sha256:" + canonical_sha256(raw)
    )


def _envelope_path(root: Path, artifact_ref: str) -> Path:
    digest = sha256(artifact_ref.encode("utf-8")).hexdigest()
    return root / digest[:2] / f"{digest}.json"


def _put_envelope(
    root: Path,
    artifact_ref: str,
    content: bytes,
    artifact_type: str,
    schema_version: str,
    *,
    encoding: str,
    snapshot_version: str = "1",
) -> tuple[ArtifactReferenceSchema, Path]:
    reference = ArtifactReferenceSchema(
        artifact_ref=artifact_ref,
        artifact_type=artifact_type,
        content_hash="sha256:" + sha256(content).hexdigest(),
        schema_version=schema_version,
        size_bytes=len(content),
        created_by=PUBLISHER,
        task_id=TASK_ID,
        run_id=RUN_ID,
        input_snapshot_version=snapshot_version,
    )
    path = _envelope_path(root, artifact_ref)
    path.parent.mkdir(parents=True, exist_ok=True)
    envelope = {
        "reference": reference.model_dump(mode="json"),
        "content_encoding": encoding,
        "content_base64": base64.b64encode(content).decode("ascii"),
    }
    path.write_text(
        json.dumps(envelope, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n",
        encoding="utf-8",
    )
    return reference, path


def _json_bytes(value) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")


def build_release_fixture(
    tmp_path: Path,
    *,
    sections: tuple[tuple[str, str, str, str], ...] | None = None,
    control_action: str = "OUTPUT",
    quality_state: str = "PASS",
    output_format: str = "markdown",
    terminal_overrides: Mapping[str, object] | None = None,
    missing_section_keys: tuple[str, ...] = (),
    withheld_section_keys: tuple[str, ...] = (),
    partial: bool = False,
    document_logical_id: str = DOCUMENT_LOGICAL_ID,
) -> ReleaseFixture:
    """Write one valid Markdown release chain; no database or publisher exists."""
    if control_action not in {"OUTPUT", "OUTPUT_BEST_EFFORT"}:
        raise ValueError("fixture control_action must be publishable")
    if output_format not in {"markdown", "json"}:
        raise ValueError("fixture output_format must be markdown or json")
    sections = sections or (("one", "section-1", "Scope", "## Scope\n\nExact body [C1]."),)
    release_root = tmp_path / "target_release"
    release_root.mkdir(parents=True, exist_ok=True)
    artifact_ref = ArtifactStateRefSchema(
        artifact_id="artifact-src03-document", artifact_version=1,
        artifact_hash=H, artifact_scope="DOCUMENT",
    )
    control_ref = PostWriteControlDecisionRefSchema(
        control_decision_id="control-src03", schema_version="post_write_control_decision_v1",
        control_contract_version="post_write_control_v1", control_decision_hash="b" * 64,
    )
    completion_ref = RunCompletionAssessmentRefSchema(
        assessment_id="assessment-src03", run_id=RUN_ID, aggregate_result="SUCCESS",
        assessment_contract_version="run_completion_assessment_v1",
        semantic_fingerprint="c" * 64, assessment_sha256="d" * 64,
    )
    assembly_ref = AssemblyInputManifestRefSchema(
        # New synthetic file-publication fixtures use the current Assembly cut.
        # They do not rewrite a saved historical manifest or certify a native Run.
        manifest_id="assembly-src03", manifest_schema_version="assembly_input_manifest_v2",
        assembly_contract_version="document_assembly_v2", full_manifest_sha256="e" * 64,
    )
    origins = tuple({"section_id": section_id, "exact_evidence_ref": "evidence-src03",
                     "source_locator": {"page_start": index, "page_end": index}}
                    for index, (_, section_id, _, _) in enumerate(sections, start=3))
    source_catalogs = tuple(CitationAliasCatalog.build(
        scope="SECTION", source_refs=({"section_id": section_id},),
        entries=(CitationAliasEntry(
            alias="C1", citation_ref="citation-src03", exact_evidence_ref="evidence-src03",
            source_identity_consistent=True, origin_refs=(origin,),
        ),),
    ) for (_, section_id, _, _), origin in zip(sections, origins))
    document_catalog = CitationAliasCatalog.build(
        scope="DOCUMENT",
        source_refs=tuple({"section_key": section_key, "section_catalog_hash": source.catalog_hash,
                           "origins": list(source.source_refs)}
                          for (section_key, _, _, _), source in zip(sections, source_catalogs)),
        entries=(CitationAliasEntry(
            alias="C1", citation_ref="citation-src03", exact_evidence_ref="evidence-src03",
            source_identity_consistent=True, origin_refs=origins,
        ),),
    )
    source_visible = {
        "payload_kind": "document",
        "document_logical_id": document_logical_id,
        "included_sections": [{
            "section_key": section_key,
            "visible_payload": {
                "payload_kind": "section", "section_id": section_id,
                "section_title": section_title, "content": content,
            },
        } for section_key, section_id, section_title, content in sections],
        "missing_section_keys": list(missing_section_keys),
        "withheld_section_keys": list(withheld_section_keys),
        "partial": partial,
    }
    citation_base = {
        "schema_version": "document_citation_projection_v1",
        "policy_version": "document-citation-display-v1",
        "source_payload_sha256": canonical_sha256(source_visible),
        "source_catalogs": [catalog.model_dump(mode="json") for catalog in source_catalogs],
        "section_alias_maps": [{"section_key": section_key, "mapping": {"C1": "C1"}}
                               for section_key, _, _, _ in sections],
        "catalog": document_catalog.model_dump(mode="json"),
    }
    citation = DocumentCitationProjection.model_validate({
        **citation_base, "projection_hash": canonical_sha256(citation_base),
    })
    visible = {**source_visible, "citation_projection": citation.model_dump(mode="json")}
    payload_bytes = (
        TargetPostWriteComposition._render_markdown_visible_payload(visible)
        if output_format == "markdown" else _json_bytes(visible)
    )
    payload_sha = sha256(payload_bytes).hexdigest()
    output_binding_sha = "f" * 64
    renderer_contract_version = (
        "target-markdown-renderer-v1" if output_format == "markdown"
        else "target-json-renderer-v1"
    )
    adapter_contract_version = (
        "target-markdown-visible-v1" if output_format == "markdown"
        else "json-visible-v1"
    )
    identity = {
        "release_contract_version": "target-post-write-release-v1",
        "document_artifact_ref": artifact_ref.model_dump(mode="json"),
        "output_format": output_format,
        "validated_output_binding_sha256": output_binding_sha,
        "publication_target_id": "target://src03-fixture",
    }
    key = canonical_sha256(identity)
    release_id = "post-write-release://sha256/" + key
    payload_ref, payload_path = _put_envelope(
        release_root, f"target-release://{key}/payload/{payload_sha}", payload_bytes,
        "post_write_release_payload", "post_write_release_payload_v1", encoding="binary",
    )
    visible_hash = canonical_sha256({
        "schema_version": "artifact_visible_projection_v1",
        "projection_contract_version": "artifact_visible_projection_v1",
        "artifact_scope": "DOCUMENT", "logical_target_ref": document_logical_id,
        "visible_payload": visible,
    })
    visible_ref = ArtifactVisibleProjectionRefSchema(
        projection_id="artifact-visible://sha256/" + visible_hash,
        projection_schema_version="artifact_visible_projection_v1",
        projection_contract_version="artifact_visible_projection_v1",
        projection_sha256=visible_hash,
    )
    conformance = _hashed(
        ArtifactOutputConformanceProofSchema, "proof_sha256",
        conformance_contract_version="output_conformance_v1",
        adapter_contract_version=adapter_contract_version,
        artifact_id=artifact_ref.artifact_id, artifact_hash=artifact_ref.artifact_hash,
        visible_projection_ref=visible_ref, visible_projection_sha256=visible_hash,
        validated_output_binding_sha256=output_binding_sha,
        output_format=output_format, renderer_contract_version=renderer_contract_version,
        output_payload_sha256=payload_sha, extracted_visible_projection_sha256=visible_hash,
        state="CONFORMING", reason_codes=(),
    )
    evidence_ref = PostWriteEvidenceBindingRef(
        binding_kind="DOCUMENT_PROJECTION",
        document_projection_ref=DocumentEvidenceSnapshotRefSchema(
            projection_id="document-evidence-src03",
            projection_schema_version="document_evidence_snapshot_projection_v1",
            projection_contract_version="document-evidence-v1", projection_sha256="1" * 64,
        ),
    )
    requirement_ref = RequirementRefSchema(
        requirement_snapshot_id="requirement-src03",
        requirement_semantic_contract_version="requirement-v1", requirement_hash="2" * 64,
    )
    quality_ref = QualityDeliveryContractRefSchema(
        contract_id="quality-src03", contract_schema_version="quality_delivery_contract_v1",
        contract_version="quality-v1", contract_hash="3" * 64,
    )
    active = ActiveDocumentBindingProjectionSchema(
        run_id=RUN_ID, document_id=DOCUMENT_ID,
        assembly_input_manifest_ref=assembly_ref, artifact_state_ref=artifact_ref,
        evidence_binding=evidence_ref, requirement_ref=requirement_ref,
        quality_delivery_contract_ref=quality_ref, assembly_owner_section_selections=(),
    )
    entry_identity = PostWriteEntryIdentitySchema(
        entry_scope="DOCUMENT", run_id=RUN_ID, document_id=DOCUMENT_ID,
        assembly_input_manifest_ref=assembly_ref, artifact_state_ref=artifact_ref,
        evidence_binding=evidence_ref, requirement_ref=requirement_ref,
        quality_delivery_contract_ref=quality_ref,
        entry_id="entry-src03", entry_sha256="4" * 64,
    )
    entry_ref = PostWriteEntryRefSchema(
        entry_id="entry-src03", entry_schema_version="document_post_write_entry_v1",
        entry_contract_version="post_write_entry_binding_v1", entry_sha256="4" * 64,
    )
    currentness = CurrentnessProofSchema(
        comparison_mode="EXPECTED_BINDING_IDENTITY", historical_entry_ref=entry_ref,
        active_projection=active, expected_active_entry_identity=entry_identity,
        currentness_state="CURRENT", match_state="EXACT_MATCH", proof_sha256="5" * 64,
    )
    usability = _hashed(
        ArtifactUsabilityIntegrityProofSchema, "proof_sha256",
        artifact_state_ref=artifact_ref, currentness_proof_sha256=currentness.proof_sha256,
        artifact_record_hash="6" * 64, state="TRUSTED_USABLE", reason_codes=(),
    )
    proof = _hashed(
        PostWriteReleaseInputProofSchema, "proof_sha256",
        release_contract_version="target-post-write-release-v1",
        release_request_sha256="7" * 64,
        run_id=RUN_ID, run_completion_assessment_ref=completion_ref,
        resolved_run_completion_assessment_sha256=completion_ref.assessment_sha256,
        run_lifecycle_revision=1, resolved_run_lifecycle_record_sha256="8" * 64,
        run_lifecycle_status="success", document_artifact_ref=artifact_ref,
        post_write_control_decision_ref=control_ref,
        resolved_control_decision_hash=control_ref.control_decision_hash,
        enforcement_action=control_action, release_time_currentness_proof=currentness,
        release_time_artifact_usability_proof=usability, output_conformance_proof=conformance,
        release_payload_artifact_ref=payload_ref, release_payload_sha256=payload_sha,
        validated_output_binding_sha256=output_binding_sha,
        output_format=output_format, renderer_contract_version=renderer_contract_version,
        quality_state_passthrough=quality_state, assembly_input_manifest_ref=assembly_ref,
        blocking_fact_refs=(), unresolved_reason_refs=(),
        verified_at=datetime(2026, 9, 27, tzinfo=timezone.utc),
    )
    proof_ref, proof_path = _put_envelope(
        release_root, f"target-release://{key}/proof/{proof.proof_sha256}",
        _json_bytes(proof.model_dump(mode="json")),
        "post_write_release_input_proof", proof.schema_version, encoding="json-utf8",
    )
    manifest = _hashed(
        PostWriteReleaseManifestSchema, "manifest_payload_sha256",
        release_id=release_id, idempotency_key=key,
        release_contract_version="target-post-write-release-v1", request_sha256="7" * 64,
        task_id=TASK_ID, run_id=RUN_ID, document_logical_id=document_logical_id,
        document_artifact_ref=artifact_ref, post_write_control_decision_ref=control_ref,
        run_completion_assessment_ref=completion_ref, run_lifecycle_revision=1,
        release_mode="BEST_EFFORT" if control_action == "OUTPUT_BEST_EFFORT" else "STANDARD",
        enforcement_action_passthrough=control_action,
        quality_state_passthrough=quality_state, assembly_input_manifest_ref=assembly_ref,
        blocking_fact_refs=(), unresolved_reason_refs=(),
        output_format=output_format, renderer_contract_version=renderer_contract_version,
        validated_output_binding_sha256=output_binding_sha,
        publication_target_id="target://src03-fixture",
        release_payload_artifact=payload_ref, release_payload_sha256=payload_sha,
        release_input_proof_artifact=proof_ref, release_input_proof_sha256=proof.proof_sha256,
        publisher=PUBLISHER, published_at=datetime(2026, 9, 27, tzinfo=timezone.utc),
    )
    _, manifest_path = _put_envelope(
        release_root, f"target-release://{key}/manifest",
        _json_bytes(manifest.model_dump(mode="json")),
        "post_write_release_manifest", manifest.schema_version, encoding="json-utf8",
    )
    outcome_values = {
        "task_id": TASK_ID, "run_id": RUN_ID,
        "status": "partial_success" if control_action == "OUTPUT_BEST_EFFORT" else "success",
        "execution_status": "partial_success" if control_action == "OUTPUT_BEST_EFFORT" else "success",
        "run_status": "success", "control_action": control_action,
        "quality_state": quality_state, "phase": "persist_deliverable",
        "artifact_exists": True, "publication_exists": True, "publication_ref": release_id,
        "is_partial": control_action == "OUTPUT_BEST_EFFORT",
        "is_best_effort": control_action == "OUTPUT_BEST_EFFORT",
    }
    outcome_values.update(terminal_overrides or {})
    outcome = _terminal(**outcome_values)
    return ReleaseFixture(
        release_root, release_id, outcome, manifest, proof, payload_bytes,
        manifest_path, proof_path, payload_path,
    )


def _tree_hashes(root: Path) -> tuple[tuple[str, int, str], ...]:
    return tuple(sorted(
        (str(path.relative_to(root)), path.stat().st_size, sha256(path.read_bytes()).hexdigest())
        for path in root.rglob("*") if path.is_file()
    ))


def _rewrite_envelope(path: Path, *, reference=None, content=None, encoding=None) -> None:
    envelope = json.loads(path.read_text(encoding="utf-8"))
    if content is not None:
        envelope["content_base64"] = base64.b64encode(content).decode("ascii")
        envelope["reference"]["content_hash"] = "sha256:" + sha256(content).hexdigest()
        envelope["reference"]["size_bytes"] = len(content)
    if reference is not None:
        reference(envelope["reference"])
    if encoding is not None:
        envelope["content_encoding"] = encoding
    path.write_text(json.dumps(envelope, ensure_ascii=False, sort_keys=True) + "\n", encoding="utf-8")


def _rehash(raw: dict, field: str) -> None:
    raw[field] = canonical_sha256({key: value for key, value in raw.items() if key != field})


def _replace_proof_chain(
    fixture: ReleaseFixture,
    proof_raw: dict,
    *,
    manifest_changes: dict | None = None,
) -> None:
    """Keep all envelope/self hashes valid while changing one lineage field."""
    _rehash(proof_raw, "proof_sha256")
    proof = PostWriteReleaseInputProofSchema.model_validate(proof_raw)
    key = fixture.manifest.idempotency_key
    new_proof_ref, _ = _put_envelope(
        fixture.release_root,
        f"target-release://{key}/proof/{proof.proof_sha256}",
        _json_bytes(proof.model_dump(mode="json")),
        "post_write_release_input_proof", proof.schema_version, encoding="json-utf8",
    )
    manifest = fixture.manifest.model_dump(mode="json")
    manifest["release_input_proof_artifact"] = new_proof_ref.model_dump(mode="json")
    manifest["release_input_proof_sha256"] = proof.proof_sha256
    manifest.update(manifest_changes or {})
    _rehash(manifest, "manifest_payload_sha256")
    PostWriteReleaseManifestSchema.model_validate(manifest)
    _rewrite_envelope(fixture.manifest_path, content=_json_bytes(manifest))


def _replace_visible_payload_chain(fixture: ReleaseFixture, visible: dict) -> None:
    """Rebuild payload, conformance, proof and manifest after a visible edit."""
    citation = visible["citation_projection"]
    citation["source_payload_sha256"] = canonical_sha256({
        key: value for key, value in visible.items() if key != "citation_projection"
    })
    _rehash(citation, "projection_hash")
    DocumentCitationProjection.model_validate(citation)
    payload_bytes = TargetPostWriteComposition._render_markdown_visible_payload(visible)
    payload_sha = sha256(payload_bytes).hexdigest()
    key = fixture.manifest.idempotency_key
    payload_ref, _ = _put_envelope(
        fixture.release_root, f"target-release://{key}/payload/{payload_sha}",
        payload_bytes, "post_write_release_payload", "post_write_release_payload_v1",
        encoding="binary",
    )
    projection_sha = canonical_sha256({
        "schema_version": "artifact_visible_projection_v1",
        "projection_contract_version": "artifact_visible_projection_v1",
        "artifact_scope": "DOCUMENT",
        "logical_target_ref": fixture.manifest.document_logical_id,
        "visible_payload": visible,
    })
    proof = fixture.proof.model_dump(mode="json")
    proof["release_payload_artifact_ref"] = payload_ref.model_dump(mode="json")
    proof["release_payload_sha256"] = payload_sha
    conformance = proof["output_conformance_proof"]
    conformance["output_payload_sha256"] = payload_sha
    conformance["visible_projection_sha256"] = projection_sha
    conformance["extracted_visible_projection_sha256"] = projection_sha
    conformance["visible_projection_ref"]["projection_sha256"] = projection_sha
    conformance["visible_projection_ref"]["projection_id"] = (
        "artifact-visible://sha256/" + projection_sha
    )
    _rehash(conformance, "proof_sha256")
    _replace_proof_chain(
        fixture, proof,
        manifest_changes={
            "release_payload_artifact": payload_ref.model_dump(mode="json"),
            "release_payload_sha256": payload_sha,
        },
    )


def _mutate_terminal(outcome: TerminalOutcomeResultV1, **changes) -> dict:
    raw = outcome.model_dump(mode="json")
    raw.update(changes)
    raw.pop("result_id")
    return _terminal(**raw).model_dump(mode="json")


def test_valid_release_chain_and_zero_side_effects(tmp_path, monkeypatch):
    fixture = build_release_fixture(tmp_path)
    assert DOCUMENT_ID != DOCUMENT_LOGICAL_ID
    assert fixture.manifest.document_logical_id == DOCUMENT_LOGICAL_ID
    assert fixture.proof.release_time_currentness_proof.active_projection.document_id == DOCUMENT_ID
    before = _tree_hashes(fixture.release_root)
    def forbidden(*_args, **_kwargs):
        raise AssertionError("SRCREV-03 tried to open SQLite or construct a business owner")
    monkeypatch.setattr(sqlite3, "connect", forbidden)
    from apps.enterprise_document.services.agent_rag_v1.artifact_store import FileArtifactStore
    from apps.enterprise_document.services.post_write.persistence import PostWriteSqliteDatabase
    from apps.enterprise_document.services.post_write.release import PostWriteReleasePublicationService
    from application.mainline_service import MainlineApplicationService
    from agent.supervisor_agent import SupervisorAgent
    from bootstrap.supervisor_factory import SupervisorFactory
    monkeypatch.setattr(FileArtifactStore, "__init__", forbidden)
    monkeypatch.setattr(PostWriteSqliteDatabase, "__init__", forbidden)
    monkeypatch.setattr(PostWriteReleasePublicationService, "__init__", forbidden)
    monkeypatch.setattr(MainlineApplicationService, "__init__", forbidden)
    monkeypatch.setattr(SupervisorAgent, "__init__", forbidden)
    monkeypatch.setattr(SupervisorFactory, "__init__", forbidden)
    monkeypatch.setattr(TargetPostWriteComposition, "__init__", forbidden)
    result = load_published_markdown(
        target_release_root=fixture.release_root,
        release_id=fixture.release_id,
        terminal_outcome=fixture.terminal_outcome,
    )
    assert result.payload_bytes == fixture.payload_bytes
    assert result.manifest == fixture.manifest
    assert result.proof == fixture.proof
    assert result.visible_payload["document_logical_id"] == DOCUMENT_LOGICAL_ID
    assert result.citation_projection["projection_hash"] == result.visible_payload["citation_projection"]["projection_hash"]
    assert result.status_view["release_status"] == "published"
    assert result.source_paths == (fixture.manifest_path, fixture.proof_path, fixture.payload_path)
    assert _tree_hashes(fixture.release_root) == before
    assert all(not path.name.endswith((".lock", ".tmp", "-wal", "-shm", "-journal")) for path in fixture.release_root.rglob("*"))


@pytest.mark.parametrize("release_id", ["wrong", "post-write-release://sha256/" + "0" * 64])
def test_wrong_release_id_fails(tmp_path, release_id):
    fixture = build_release_fixture(tmp_path)
    with pytest.raises(ReadingSourceIntegrityError):
        load_published_markdown(target_release_root=fixture.release_root, release_id=release_id,
                                terminal_outcome=fixture.terminal_outcome)


@pytest.mark.parametrize("case", ["invalid_base64", "wrong_encoding", "whitespace_ref"])
def test_envelope_wire_contract_fails_closed(tmp_path, case):
    fixture = build_release_fixture(tmp_path)
    envelope = json.loads(fixture.manifest_path.read_text(encoding="utf-8"))
    if case == "invalid_base64":
        envelope["content_base64"] += "!"
    elif case == "wrong_encoding":
        envelope["content_encoding"] = "binary"
    else:
        envelope["reference"]["artifact_ref"] = " " + envelope["reference"]["artifact_ref"]
    fixture.manifest_path.write_text(json.dumps(envelope) + "\n", encoding="utf-8")
    with pytest.raises(ReadingSourceIntegrityError):
        load_published_markdown(target_release_root=fixture.release_root,
                                release_id=fixture.release_id,
                                terminal_outcome=fixture.terminal_outcome)


@pytest.mark.parametrize("case", [
    "manifest_ref", "manifest_hash", "proof_hash", "payload_hash", "task", "run",
    "artifact", "control", "run_completion", "renderer", "format", "citation",
])
def test_corrupt_release_chain_fails_closed(tmp_path, case):
    fixture = build_release_fixture(tmp_path, output_format="json" if case == "format" else "markdown")
    if case == "manifest_ref":
        _rewrite_envelope(fixture.manifest_path, reference=lambda ref: ref.update(artifact_ref="wrong"))
    elif case == "payload_hash":
        _rewrite_envelope(fixture.payload_path, content=fixture.payload_bytes + b"x")
    elif case in {"manifest_hash", "renderer"}:
        manifest = fixture.manifest.model_dump(mode="json")
        if case == "manifest_hash":
            manifest["publisher"] = "corrupt"
        elif case == "renderer":
            manifest["renderer_contract_version"] = "unsupported-renderer"
            manifest["manifest_payload_sha256"] = canonical_sha256({k: v for k, v in manifest.items() if k != "manifest_payload_sha256"})
        _rewrite_envelope(fixture.manifest_path, content=_json_bytes(manifest))
    elif case == "task":
        _rewrite_envelope(fixture.proof_path,
                          reference=lambda ref: ref.update(task_id="other-task"))
        manifest = fixture.manifest.model_dump(mode="json")
        manifest["release_input_proof_artifact"]["task_id"] = "other-task"
        _rehash(manifest, "manifest_payload_sha256")
        PostWriteReleaseManifestSchema.model_validate(manifest)
        _rewrite_envelope(fixture.manifest_path, content=_json_bytes(manifest))
    elif case in {"run", "artifact", "control", "run_completion"}:
        proof = fixture.proof.model_dump(mode="json")
        if case == "run":
            proof["run_id"] = "other-run"
            proof["run_completion_assessment_ref"]["run_id"] = "other-run"
        elif case == "artifact":
            wrong_artifact = deepcopy(proof["document_artifact_ref"])
            wrong_artifact["artifact_hash"] = "0" * 64
            proof["document_artifact_ref"] = wrong_artifact
            proof["release_time_currentness_proof"]["active_projection"]["artifact_state_ref"] = wrong_artifact
            proof["release_time_artifact_usability_proof"]["artifact_state_ref"] = wrong_artifact
            _rehash(proof["release_time_artifact_usability_proof"], "proof_sha256")
            proof["output_conformance_proof"]["artifact_hash"] = "0" * 64
            _rehash(proof["output_conformance_proof"], "proof_sha256")
        elif case == "control":
            proof["post_write_control_decision_ref"]["control_decision_hash"] = "0" * 64
            proof["resolved_control_decision_hash"] = "0" * 64
        else:
            proof["run_completion_assessment_ref"]["assessment_id"] = "other-assessment"
        _replace_proof_chain(fixture, proof)
    elif case == "format":
        pass  # The fixture itself is a valid published JSON chain.
    elif case == "proof_hash":
        proof = fixture.proof.model_dump(mode="json")
        proof["proof_sha256"] = "0" * 64
        _rewrite_envelope(fixture.proof_path, content=_json_bytes(proof))
    else:
        visible = TargetPostWriteComposition._extract_markdown_visible_payload(fixture.payload_bytes)
        visible["citation_projection"]["projection_hash"] = "0" * 64
        corrupted_payload = TargetPostWriteComposition._render_markdown_visible_payload(visible)
        corrupted_sha = sha256(corrupted_payload).hexdigest()
        key = fixture.manifest.idempotency_key
        payload_ref, _ = _put_envelope(
            fixture.release_root, f"target-release://{key}/payload/{corrupted_sha}",
            corrupted_payload, "post_write_release_payload", "post_write_release_payload_v1",
            encoding="binary",
        )
        projection_sha = canonical_sha256({
            "schema_version": "artifact_visible_projection_v1",
            "projection_contract_version": "artifact_visible_projection_v1",
            "artifact_scope": "DOCUMENT", "logical_target_ref": DOCUMENT_LOGICAL_ID,
            "visible_payload": visible,
        })
        proof = fixture.proof.model_dump(mode="json")
        proof["release_payload_artifact_ref"] = payload_ref.model_dump(mode="json")
        proof["release_payload_sha256"] = corrupted_sha
        conformance = proof["output_conformance_proof"]
        conformance["output_payload_sha256"] = corrupted_sha
        conformance["visible_projection_sha256"] = projection_sha
        conformance["extracted_visible_projection_sha256"] = projection_sha
        conformance["visible_projection_ref"]["projection_sha256"] = projection_sha
        conformance["visible_projection_ref"]["projection_id"] = "artifact-visible://sha256/" + projection_sha
        _rehash(conformance, "proof_sha256")
        _replace_proof_chain(
            fixture, proof,
            manifest_changes={"release_payload_artifact": payload_ref.model_dump(mode="json"),
                              "release_payload_sha256": corrupted_sha},
        )
    error = UnsupportedReadingExportFormat if case in {"renderer", "format"} else ReadingSourceIntegrityError
    expected_message = {
        "task": "publication envelope raw reference differs",
        "run": "release manifest/proof binding differs",
        "artifact": "release active Document identity differs",
        "control": "release manifest/proof binding differs",
        "run_completion": "release manifest/proof binding differs",
        "citation": "final Document citation projection invalid",
    }.get(case)
    with pytest.raises(error, match=expected_message):
        load_published_markdown(target_release_root=fixture.release_root,
                                release_id=fixture.release_id,
                                terminal_outcome=fixture.terminal_outcome)


@pytest.mark.parametrize("field, wrong_value", [
    ("run_id", "other-run"),
    ("document_id", "other-raw-document"),
])
def test_active_projection_identity_mismatch_fails_with_valid_proof_chain(
    tmp_path, field, wrong_value
):
    fixture = build_release_fixture(tmp_path)
    proof = fixture.proof.model_dump(mode="json")
    active = proof["release_time_currentness_proof"]["active_projection"]
    assert active[field] != wrong_value
    active[field] = wrong_value
    _replace_proof_chain(fixture, proof)
    with pytest.raises(ReadingSourceIntegrityError):
        load_published_markdown(target_release_root=fixture.release_root,
                                release_id=fixture.release_id,
                                terminal_outcome=fixture.terminal_outcome)


@pytest.mark.parametrize("case", ["missing", "mismatch"])
def test_visible_logical_document_identity_fails_with_valid_payload_chain(
    tmp_path, case
):
    fixture = build_release_fixture(tmp_path)
    visible = TargetPostWriteComposition._extract_markdown_visible_payload(
        fixture.payload_bytes
    )
    if case == "missing":
        visible.pop("document_logical_id")
    else:
        visible["document_logical_id"] = "document-logical:other-run:other-document"
    _replace_visible_payload_chain(fixture, visible)
    with pytest.raises(ReadingSourceIntegrityError,
                       match="published Document logical identity differs"):
        load_published_markdown(target_release_root=fixture.release_root,
                                release_id=fixture.release_id,
                                terminal_outcome=fixture.terminal_outcome)


@pytest.mark.parametrize("case", [
    "result_id", "task", "run", "publication", "control", "quality", "missing",
])
def test_terminal_receipt_fails_closed(tmp_path, case):
    fixture = build_release_fixture(tmp_path)
    if case == "missing":
        receipt = None
    elif case == "result_id":
        receipt = fixture.terminal_outcome.model_dump(mode="json")
        receipt["result_id"] = "sha256:" + "0" * 64
    else:
        changes = {
            "task": {"task_id": "other-task"}, "run": {"run_id": "other-run"},
            "publication": {"publication_ref": "post-write-release://sha256/" + "0" * 64},
            "control": {"control_action": "OUTPUT_BEST_EFFORT", "is_best_effort": True,
                        "is_partial": True},
            "quality": {"quality_state": "FAIL"},
        }
        receipt = _mutate_terminal(fixture.terminal_outcome, **changes[case])
    expected = TerminalOutcomeReceiptRequired if case == "missing" else ReadingSourceIntegrityError
    with pytest.raises(expected):
        load_published_markdown(target_release_root=fixture.release_root,
                                release_id=fixture.release_id, terminal_outcome=receipt)


def test_copied_model_with_bad_result_id_is_revalidated(tmp_path):
    fixture = build_release_fixture(tmp_path)
    bad_model = fixture.terminal_outcome.model_copy(update={"result_id": "sha256:" + "0" * 64})
    with pytest.raises(ReadingSourceIntegrityError):
        load_published_markdown(target_release_root=fixture.release_root,
                                release_id=fixture.release_id, terminal_outcome=bad_model)


def test_later_execution_failure_is_preserved(tmp_path):
    fixture = build_release_fixture(tmp_path)
    receipt = _mutate_terminal(fixture.terminal_outcome,
                               status="failed", execution_status="failed",
                               phase="persist_deliverable")
    result = load_published_markdown(target_release_root=fixture.release_root,
                                     release_id=fixture.release_id, terminal_outcome=receipt)
    assert result.status_view["execution_status"] == "failed"
    assert result.status_view["run_status"] == "success"
    assert result.status_view["publication_exists"] is True


def test_missing_publication_fails(tmp_path):
    fixture = build_release_fixture(tmp_path)
    receipt = _mutate_terminal(fixture.terminal_outcome,
                               publication_exists=False, publication_ref=None)
    with pytest.raises(ReadingSourceIntegrityError):
        load_published_markdown(target_release_root=fixture.release_root,
                                release_id=fixture.release_id, terminal_outcome=receipt)


def test_two_sections_fail_best_effort_fixture_is_accepted(tmp_path):
    sections = (
        ("one", "section-1", "Scope", "## Scope\n\n| A | B |\n|---|---|\n| 1 | 2 | [C1]"),
        ("two", "section-2", "Design", "```txt\n[C1]\n```\nUnknown [C9], internal [fact_x]."),
    )
    fixture = build_release_fixture(
        tmp_path, sections=sections, control_action="OUTPUT_BEST_EFFORT",
        quality_state="FAIL",
    )
    result = load_published_markdown(target_release_root=fixture.release_root,
                                     release_id=fixture.release_id,
                                     terminal_outcome=fixture.terminal_outcome)
    assert [section["visible_payload"]["content"]
            for section in result.visible_payload["included_sections"]] == [
                section[3] for section in sections
            ]
    assert result.status_view["business_quality_status"] == "failed"
    assert result.status_view["control_action"] == "OUTPUT_BEST_EFFORT"


def test_partial_missing_withheld_metadata_remains_in_published_payload(tmp_path):
    fixture = build_release_fixture(
        tmp_path, missing_section_keys=("not-generated",),
        withheld_section_keys=("not-released",), partial=True,
    )
    result = load_published_markdown(target_release_root=fixture.release_root,
                                     release_id=fixture.release_id,
                                     terminal_outcome=fixture.terminal_outcome)
    assert result.visible_payload["missing_section_keys"] == ["not-generated"]
    assert result.visible_payload["withheld_section_keys"] == ["not-released"]
    assert result.visible_payload["partial"] is True
