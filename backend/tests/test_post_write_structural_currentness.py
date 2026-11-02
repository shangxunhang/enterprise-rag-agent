from __future__ import annotations

import sqlite3

import pytest

from apps.enterprise_document.schemas.post_write import (
    ActiveComparisonMode,
    ActiveSectionBindingProjectionSchema,
    AssemblyInputManifestRefSchema,
    ArtifactScope,
    ArtifactStateRefSchema,
    DocumentEvidenceSnapshotRefSchema,
    DocumentPostWriteEntrySchema,
    EvidenceSnapshotRefSchema,
    InitialWriterArtifactOriginSchema,
    PostWriteEvidenceBindingKind,
    PostWriteEvidenceBindingRef,
    PostWriteRequirementEntrySchema,
    QualityDeliveryContractRefSchema,
)
from apps.enterprise_document.schemas.pre_write_binding import WriterSubjectRefSchema
from apps.enterprise_document.schemas.pre_write_control import PreWriteRouteDecisionRefSchema
from apps.enterprise_document.schemas.pre_write_writer import (
    WriterExecutionRefSchema,
    WriterGenerationClass,
    WriterGenerationInvocationRefSchema,
    WriterLogicalExecutionKey,
)
from apps.enterprise_document.services.post_write import (
    ArtifactStateService,
    ArtifactStateStore,
    CurrentnessIntegrityError,
    CurrentnessResolver,
    PostWriteConcurrencyError,
    PostWriteEntryIdentityBuilder,
    PostWriteEntryStore,
    PostWritePersistenceIntegrityError,
    PostWriteSqliteDatabase,
    RequirementMaterializer,
    RequirementStore,
    SectionPostWriteEntryAssembler,
)


SHA_A = "a" * 64
SHA_B = "b" * 64
SHA_C = "c" * 64
SHA_D = "d" * 64


def _writer_refs():
    subject = WriterSubjectRefSchema(
        run_id="run-1",
        document_id="doc-1",
        section_id="section-1",
    )
    route = PreWriteRouteDecisionRefSchema(route_decision_id="route-1")
    key = WriterLogicalExecutionKey(
        writer_subject_ref=subject,
        write_instance_id="write-1",
        route_decision_ref=route,
    )
    execution = WriterExecutionRefSchema(
        logical_execution_key=key,
        result_sha256=SHA_A,
    )
    invocation = WriterGenerationInvocationRefSchema(
        logical_execution_key=key,
        invocation_sha256=SHA_B,
    )
    return route, execution, invocation


def _db(tmp_path):
    return PostWriteSqliteDatabase(tmp_path / "post_write.sqlite3")


def _requirement_ref(database, *, statement="must preserve scope"):
    materializer = RequirementMaterializer(RequirementStore(database))
    return materializer.materialize(
        semantic_contract_version="post-write-requirements-v1",
        source_business_ref="business://profile/v1",
        source_task_ref="task://contract/v1",
        requirements=(
            PostWriteRequirementEntrySchema(
                requirement_id="req-1",
                requirement_kind="business",
                statement=statement,
            ),
        ),
    )


def _contract_ref(version="v1"):
    return QualityDeliveryContractRefSchema(
        contract_id="quality-contract",
        contract_schema_version="quality_delivery_contract_v1",
        contract_version=version,
        contract_hash=SHA_C if version == "v1" else SHA_D,
    )


def _section_fixture(tmp_path):
    database = _db(tmp_path)
    route, execution, invocation = _writer_refs()
    artifact_service = ArtifactStateService(
        store=ArtifactStateStore(database)
    )
    origin = InitialWriterArtifactOriginSchema(
        writer_execution_ref=execution,
        writer_invocation_ref=invocation,
    )
    artifact_ref = artifact_service.create_initial(
        artifact_id="section-artifact:run-1:doc-1:section-1",
        artifact_scope=ArtifactScope.SECTION,
        canonical_payload_ref={
            "payload_kind": "section",
            "text_sha256": SHA_A,
        },
        origin=origin,
    )
    requirement_ref = _requirement_ref(database)
    evidence_ref = EvidenceSnapshotRefSchema(
        evidence_bundle_schema_version="evidence_bundle_v2",
        evidence_bundle_sha256=SHA_B,
        writer_invocation_ref=invocation,
    )
    evidence_binding = PostWriteEvidenceBindingRef(
        binding_kind=PostWriteEvidenceBindingKind.SECTION_WRITER_SNAPSHOT,
        section_writer_snapshot_ref=evidence_ref,
    )
    builder = PostWriteEntryIdentityBuilder()
    store = PostWriteEntryStore(database)
    assembler = SectionPostWriteEntryAssembler(
        store=store,
        identity_builder=builder,
    )
    entry_ref = assembler.assemble_and_persist(
        run_id="run-1",
        document_id="doc-1",
        section_id="section-1",
        artifact_state_ref=artifact_ref,
        writer_execution_ref=execution,
        writer_invocation_ref=invocation,
        route_decision_ref=route,
        evidence_binding=evidence_binding,
        requirement_ref=requirement_ref,
        quality_delivery_contract_ref=_contract_ref(),
        generation_class=WriterGenerationClass.NORMAL_WRITE,
    )
    projection = ActiveSectionBindingProjectionSchema(
        run_id="run-1",
        document_id="doc-1",
        section_id="section-1",
        artifact_state_ref=artifact_ref,
        evidence_binding=evidence_binding,
        requirement_ref=requirement_ref,
        quality_delivery_contract_ref=_contract_ref(),
        writer_execution_ref=execution,
        writer_invocation_ref=invocation,
        route_decision_ref=route,
        generation_class=WriterGenerationClass.NORMAL_WRITE,
    )
    return (
        database,
        artifact_service,
        store,
        builder,
        entry_ref,
        projection,
        origin,
    )


def test_artifact_initial_commit_is_idempotent_and_no_head_table_exists(tmp_path) -> None:
    database, service, *_rest, origin = _section_fixture(tmp_path)
    first = service.create_initial(
        artifact_id="section-artifact:run-1:doc-1:section-1",
        artifact_scope=ArtifactScope.SECTION,
        canonical_payload_ref={
            "payload_kind": "section",
            "text_sha256": SHA_A,
        },
        origin=origin,
    )
    second = service.create_initial(
        artifact_id="section-artifact:run-1:doc-1:section-1",
        artifact_scope=ArtifactScope.SECTION,
        canonical_payload_ref={
            "payload_kind": "section",
            "text_sha256": SHA_A,
        },
        origin=origin,
    )
    assert first == second
    with database.read_transaction() as connection:
        tables = {
            row[0]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            )
        }
    assert "artifact_head" not in tables
    assert "artifact_heads" not in tables


def test_artifact_expected_parent_cas_and_same_hash_noop(tmp_path) -> None:
    _dbase, service, *_rest, origin = _section_fixture(tmp_path)
    parent = service.resolve_exact(
        service.create_initial(
            artifact_id="other-artifact",
            artifact_scope=ArtifactScope.SECTION,
            canonical_payload_ref={"text_sha256": SHA_A},
            origin=origin,
        )
    ).artifact_ref

    same = service.append_next_version(
        expected_parent_ref=parent,
        canonical_payload_ref={"text_sha256": SHA_A},
        origin=origin,
    )
    assert same == parent

    changed = service.append_next_version(
        expected_parent_ref=parent,
        canonical_payload_ref={"text_sha256": SHA_B},
        origin=origin,
    )
    assert changed.artifact_version == 2

    with pytest.raises(PostWriteConcurrencyError):
        service.append_next_version(
            expected_parent_ref=parent,
            canonical_payload_ref={"text_sha256": SHA_C},
            origin=origin,
        )


def test_requirement_materialization_is_deterministic_and_exact_resolvable(tmp_path) -> None:
    database = _db(tmp_path)
    first = _requirement_ref(database)
    second = _requirement_ref(database)
    assert first == second
    snapshot = RequirementStore(database).resolve_exact(first)
    assert snapshot.requirement_hash == first.requirement_hash
    assert snapshot.requirements[0].requirement_id == "req-1"


def test_requirement_identity_collision_fails_closed(tmp_path) -> None:
    database = _db(tmp_path)
    first = _requirement_ref(database)
    # Same source identity/contract produces same snapshot id. Changed requirement
    # semantics must not overwrite the historical canonical snapshot.
    with pytest.raises(PostWritePersistenceIntegrityError):
        _requirement_ref(database, statement="materially changed requirement")
    assert RequirementStore(database).resolve_exact(first).requirements[0].statement == (
        "must preserve scope"
    )


def test_entry_identity_builder_is_shared_with_expected_binding_currentness(tmp_path) -> None:
    database, _artifact, store, builder, entry_ref, projection, _origin = _section_fixture(
        tmp_path
    )
    proof = CurrentnessResolver(
        entry_store=store,
        identity_builder=builder,
    ).prove(
        historical_entry_ref=entry_ref,
        active_projection=projection,
        comparison_mode=ActiveComparisonMode.EXPECTED_BINDING_IDENTITY,
    )
    assert proof.currentness_state.value == "CURRENT"
    assert proof.expected_active_entry_identity.entry_id == entry_ref.entry_id
    assert proof.active_materialized_post_write_entry_ref is None


def test_section_entry_target_identity_must_match_writer_subject(tmp_path) -> None:
    _database, _artifact, _store, builder, _entry_ref, projection, _origin = (
        _section_fixture(tmp_path)
    )
    with pytest.raises(ValueError, match="target identity contradicts"):
        builder.build_section(
            run_id="run-other",
            document_id=projection.document_id,
            section_id=projection.section_id,
            artifact_state_ref=projection.artifact_state_ref,
            writer_execution_ref=projection.writer_execution_ref,
            writer_invocation_ref=projection.writer_invocation_ref,
            route_decision_ref=projection.route_decision_ref,
            evidence_binding=projection.evidence_binding,
            requirement_ref=projection.requirement_ref,
            quality_delivery_contract_ref=projection.quality_delivery_contract_ref,
            generation_class=projection.generation_class,
        )


def test_document_entry_manifest_must_match_identity(tmp_path) -> None:
    database = _db(tmp_path)
    manifest_ref = AssemblyInputManifestRefSchema(
        manifest_id="assembly-manifest://sha256/" + SHA_A,
        manifest_schema_version="assembly_input_manifest_v2",
        assembly_contract_version="document_assembly_v2",
        full_manifest_sha256=SHA_A,
    )
    other_manifest_ref = AssemblyInputManifestRefSchema(
        manifest_id="assembly-manifest://sha256/" + SHA_D,
        manifest_schema_version="assembly_input_manifest_v2",
        assembly_contract_version="document_assembly_v2",
        full_manifest_sha256=SHA_D,
    )
    document_evidence_ref = DocumentEvidenceSnapshotRefSchema(
        projection_id="document-evidence://sha256/" + SHA_B,
        projection_schema_version="document_evidence_snapshot_projection_v1",
        projection_contract_version="document_evidence_projection_v1",
        projection_sha256=SHA_B,
    )
    identity = PostWriteEntryIdentityBuilder().build_document(
        run_id="run-1",
        document_id="doc-1",
        assembly_input_manifest_ref=manifest_ref,
        artifact_state_ref=ArtifactStateRefSchema(
            artifact_id="document-artifact:run-1:doc-1",
            artifact_version=1,
            artifact_hash=SHA_A,
            artifact_scope=ArtifactScope.DOCUMENT,
        ),
        evidence_binding=PostWriteEvidenceBindingRef(
            binding_kind=PostWriteEvidenceBindingKind.DOCUMENT_PROJECTION,
            document_projection_ref=document_evidence_ref,
        ),
        requirement_ref=_requirement_ref(database),
        quality_delivery_contract_ref=_contract_ref(),
    )
    with pytest.raises(ValueError, match="assembly manifest contradicts"):
        DocumentPostWriteEntrySchema(
            identity=identity,
            assembly_input_manifest_ref=other_manifest_ref,
        )


def test_changed_active_binding_makes_historical_entry_stale_without_latest_query(tmp_path) -> None:
    database, _artifact, store, builder, entry_ref, projection, _origin = _section_fixture(
        tmp_path
    )
    changed_contract = _contract_ref("v2")
    changed_projection = projection.model_copy(
        update={"quality_delivery_contract_ref": changed_contract}
    )
    proof = CurrentnessResolver(
        entry_store=store,
        identity_builder=builder,
    ).prove(
        historical_entry_ref=entry_ref,
        active_projection=changed_projection,
        comparison_mode=ActiveComparisonMode.EXPECTED_BINDING_IDENTITY,
    )
    assert proof.currentness_state.value == "STALE"
    assert proof.match_state.value == "IDENTITY_MISMATCH"
    assert proof.expected_active_entry_identity.entry_id != entry_ref.entry_id


def test_materialized_entry_mode_detects_projection_contradiction(tmp_path) -> None:
    database, _artifact, store, builder, entry_ref, projection, _origin = _section_fixture(
        tmp_path
    )
    changed_projection = projection.model_copy(
        update={"quality_delivery_contract_ref": _contract_ref("v2")}
    )
    with pytest.raises(CurrentnessIntegrityError, match="contradicts"):
        CurrentnessResolver(
            entry_store=store,
            identity_builder=builder,
        ).prove(
            historical_entry_ref=entry_ref,
            active_projection=changed_projection,
            comparison_mode=ActiveComparisonMode.MATERIALIZED_ENTRY,
            active_materialized_post_write_entry_ref=entry_ref,
        )


def test_corrupt_artifact_row_metadata_fails_exact_resolution(tmp_path) -> None:
    database, service, _store, _builder, _entry_ref, projection, _origin = (
        _section_fixture(tmp_path)
    )
    connection = sqlite3.connect(database.path)
    try:
        connection.execute(
            "UPDATE artifact_states SET origin_json=? "
            "WHERE artifact_id=? AND artifact_version=?",
            (
                "{}",
                projection.artifact_state_ref.artifact_id,
                projection.artifact_state_ref.artifact_version,
            ),
        )
        connection.commit()
    finally:
        connection.close()

    with pytest.raises(PostWritePersistenceIntegrityError, match="row metadata"):
        service.resolve_exact(projection.artifact_state_ref)


def test_corrupt_requirement_row_hash_fails_exact_resolution(tmp_path) -> None:
    database = _db(tmp_path)
    ref = _requirement_ref(database)
    connection = sqlite3.connect(database.path)
    try:
        connection.execute(
            "UPDATE requirement_snapshots SET requirement_hash=? "
            "WHERE requirement_snapshot_id=?",
            (SHA_D, ref.requirement_snapshot_id),
        )
        connection.commit()
    finally:
        connection.close()

    with pytest.raises(PostWritePersistenceIntegrityError):
        RequirementStore(database).resolve_exact(ref)
