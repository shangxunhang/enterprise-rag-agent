from __future__ import annotations

import ast
from pathlib import Path

import pytest

from apps.enterprise_document.schemas.post_write import (
    ArtifactScope,
    DocumentAssemblyArtifactOriginSchema,
    OutputConformanceState,
)
from apps.enterprise_document.services.agent_rag_v1.artifact_store import (
    FileArtifactStore,
)
from apps.enterprise_document.services.post_write import (
    ArtifactCanonicalPayloadMaterializer,
    ArtifactOutputConformanceVerifier,
    ArtifactStateService,
    ArtifactStateStore,
    ArtifactValidationViewResolver,
    OutputConformanceRegistry,
    OutputConformanceTechnicalError,
    PostWriteSqliteDatabase,
    ValidatorInputEnvelopeBuilder,
)


from backend.tests.test_post_write_validation_foundation import _stage_plan


BACKEND_ROOT = Path(__file__).resolve().parents[1]
RELEASE_SCHEMA = (
    BACKEND_ROOT
    / "apps"
    / "enterprise_document"
    / "schemas"
    / "post_write"
    / "release.py"
)
RELEASE_SERVICE = (
    BACKEND_ROOT
    / "apps"
    / "enterprise_document"
    / "services"
    / "post_write"
    / "release.py"
)
PERSISTENCE = (
    BACKEND_ROOT
    / "apps"
    / "enterprise_document"
    / "services"
    / "post_write"
    / "persistence.py"
)


def _import_surface(path: Path) -> tuple[set[str], set[str]]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    modules: set[str] = set()
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            modules.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            modules.add(node.module or "")
            names.update(alias.name for alias in node.names)
    return modules, names


def test_target_release_imports_no_legacy_release_or_hard_gate_authority() -> None:
    modules, names = _import_surface(RELEASE_SERVICE)
    schema_modules, schema_names = _import_surface(RELEASE_SCHEMA)
    all_modules = modules | schema_modules
    all_names = names | schema_names

    assert "apps.enterprise_document.schemas.release_publication" not in all_modules
    assert "apps.enterprise_document.services.release_publication" not in all_modules
    forbidden_names = {
        "ReleaseEligibilityProofSchema",
        "ReleaseManifestSchema",
        "PublishedReleaseSchema",
        "ReleasePublicationService",
        "HardGateResultSchema",
        "GenerationReportSchema",
        "VersionDecisionSchema",
        "validate_writer_section_artifact",
    }
    assert not (forbidden_names & all_names)


def test_target_release_surface_is_physically_isolated_from_legacy_modules() -> None:
    assert RELEASE_SCHEMA.is_file()
    assert RELEASE_SERVICE.is_file()
    assert (
        BACKEND_ROOT
        / "apps"
        / "enterprise_document"
        / "schemas"
        / "release_publication.py"
    ).is_file()
    assert (
        BACKEND_ROOT
        / "apps"
        / "enterprise_document"
        / "services"
        / "release_publication.py"
    ).is_file()


def test_release_commit_fence_is_physical_only_not_release_authority() -> None:
    source = PERSISTENCE.read_text(encoding="utf-8")
    start = source.index("class PostWriteReleaseCommitFence")
    block = source[start : source.index("_TABLE_DDL", start)]
    assert "write_transaction(immediate=True)" in block
    assert "ReleaseDecision" not in block
    assert "active_head" not in block.lower()
    assert "manifest" not in block.lower()
    assert "currentness" not in block.lower()


def _verifier(tmp_path, adapter):
    store = FileArtifactStore(tmp_path / "output-store")
    registry = OutputConformanceRegistry()
    registry.register(
        output_format="json",
        adapter_contract_version="json-visible-v1",
        adapter=adapter,
    )
    return (
        store,
        ArtifactOutputConformanceVerifier(
            artifact_store=store,
            registry=registry,
        ),
    )


def test_output_conformance_mismatch_is_nonconforming_not_quality_fact(tmp_path) -> None:
    store, verifier = _verifier(
        tmp_path,
        lambda raw: __import__("json").loads(raw.decode("utf-8")),
    )
    projection = ArtifactValidationViewResolver.build_projection(
        artifact_scope=ArtifactScope.DOCUMENT,
        logical_target_ref="document:test",
        visible_payload={"text": "expected"},
    )
    output_ref = store.put_json(
        {"text": "different"},
        artifact_type="validated_document_output",
        schema_version="validated_document_output_v1",
        created_by="test",
        task_id="task",
        run_id="run",
    )
    binding = verifier.build_output_binding(
        output_format="json",
        renderer_contract_version="renderer-v1",
        output_artifact_ref=output_ref,
    )
    proof = verifier.prove_precommit(
        artifact_id="artifact:document:test",
        artifact_scope=ArtifactScope.DOCUMENT,
        canonical_payload_ref={"text": "expected"},
        visible_projection=projection,
        output_binding=binding,
    )

    assert proof.state is OutputConformanceState.NONCONFORMING
    assert proof.reason_codes == ("VISIBLE_PROJECTION_MISMATCH",)
    assert not hasattr(proof, "quality_state")
    assert not hasattr(proof, "validation_fact")


def test_output_conformance_adapter_failure_creates_no_semantic_nonconforming_proof(
    tmp_path,
) -> None:
    def explode(_raw):
        raise RuntimeError("extractor failed")

    store, verifier = _verifier(tmp_path, explode)
    projection = ArtifactValidationViewResolver.build_projection(
        artifact_scope=ArtifactScope.DOCUMENT,
        logical_target_ref="document:test",
        visible_payload={"text": "expected"},
    )
    output_ref = store.put_json(
        {"text": "expected"},
        artifact_type="validated_document_output",
        schema_version="validated_document_output_v1",
        created_by="test",
        task_id="task",
        run_id="run",
    )
    binding = verifier.build_output_binding(
        output_format="json",
        renderer_contract_version="renderer-v1",
        output_artifact_ref=output_ref,
    )

    with pytest.raises(
        OutputConformanceTechnicalError,
        match="extraction failed",
    ):
        verifier.prove_precommit(
            artifact_id="artifact:document:test",
            artifact_scope=ArtifactScope.DOCUMENT,
            canonical_payload_ref={"text": "expected"},
            visible_projection=projection,
            output_binding=binding,
        )


def test_release_visible_output_bytes_are_inside_artifact_semantic_hash_boundary(
    tmp_path,
) -> None:
    store, verifier = _verifier(
        tmp_path,
        lambda raw: __import__("json").loads(raw.decode("utf-8")),
    )
    ref_a = store.put_json(
        {"text": "A"},
        artifact_type="validated_document_output",
        schema_version="validated_document_output_v1",
        created_by="test",
        task_id="task",
        run_id="run",
        artifact_ref="output://A",
    )
    ref_b = store.put_json(
        {"text": "B"},
        artifact_type="validated_document_output",
        schema_version="validated_document_output_v1",
        created_by="test",
        task_id="task",
        run_id="run",
        artifact_ref="output://B",
    )
    binding_a = verifier.build_output_binding(
        output_format="json",
        renderer_contract_version="renderer-v1",
        output_artifact_ref=ref_a,
    )
    binding_b = verifier.build_output_binding(
        output_format="json",
        renderer_contract_version="renderer-v1",
        output_artifact_ref=ref_b,
    )
    base = {"logical_document": "same"}
    payload_a = (
        ArtifactCanonicalPayloadMaterializer.with_validated_output_semantics(
            base,
            (binding_a,),
        )
    )
    payload_b = (
        ArtifactCanonicalPayloadMaterializer.with_validated_output_semantics(
            base,
            (binding_b,),
        )
    )
    hash_a = ArtifactCanonicalPayloadMaterializer.artifact_hash(
        artifact_scope=ArtifactScope.DOCUMENT,
        canonical_payload_ref=payload_a,
    )
    hash_b = ArtifactCanonicalPayloadMaterializer.artifact_hash(
        artifact_scope=ArtifactScope.DOCUMENT,
        canonical_payload_ref=payload_b,
    )

    assert binding_a.output_payload_sha256 != binding_b.output_payload_sha256
    assert hash_a != hash_b


def test_target_release_module_contains_no_post_validation_renderer_or_model_path() -> None:
    source = RELEASE_SERVICE.read_text(encoding="utf-8").lower()
    forbidden = (
        "modelgateway",
        "toolgateway",
        "hardgateresult",
        "validate_writer_section_artifact",
        "markdown_to_docx",
        "render_document",
        "rerender",
        "llm",
    )
    for token in forbidden:
        assert token not in source


def test_no_run_completion_or_release_mutable_head_is_introduced() -> None:
    release_source = RELEASE_SERVICE.read_text(encoding="utf-8")
    schema_source = RELEASE_SCHEMA.read_text(encoding="utf-8")
    combined = release_source + schema_source
    assert "RunCompletionAssessmentHead" not in combined
    assert "PostWriteReleaseHead" not in combined
    assert "ReleaseDecisionStore" not in combined



def test_new_artifact_commit_materializes_exact_visible_projection_by_default(
    tmp_path,
) -> None:
    database = PostWriteSqliteDatabase(tmp_path / "post-write.sqlite3")
    service = ArtifactStateService(store=ArtifactStateStore(database))
    artifact_ref = service.create_initial(
        artifact_id="document-artifact:projection-default",
        artifact_scope=ArtifactScope.DOCUMENT,
        canonical_payload_ref={
            "payload_kind": "document",
            "text": "visible body",
        },
        origin=DocumentAssemblyArtifactOriginSchema(
            assembly_input_manifest_ref="assembly://projection-default"
        ),
    )
    record = service.resolve_exact(artifact_ref)

    assert record.visible_projection is not None
    assert record.visible_projection_ref == record.visible_projection.as_ref()
    assert (
        record.canonical_payload_ref["visible_projection_sha256"]
        == record.visible_projection.projection_sha256
    )
    assert record.visible_projection.visible_payload == {
        "payload_kind": "document",
        "text": "visible body",
    }


def test_validator_input_builder_binds_exact_committed_projection(
    tmp_path,
) -> None:
    database = PostWriteSqliteDatabase(tmp_path / "post-write.sqlite3")
    service = ArtifactStateService(store=ArtifactStateStore(database))
    artifact_ref = service.create_initial(
        artifact_id="section-artifact:validator-input",
        artifact_scope=ArtifactScope.SECTION,
        canonical_payload_ref={
            "payload_kind": "section",
            "text": "validator-visible",
        },
        origin=DocumentAssemblyArtifactOriginSchema(
            assembly_input_manifest_ref="assembly://validator-input"
        ),
    )
    resolver = ArtifactValidationViewResolver(service)
    projection = resolver.resolve(artifact_ref)
    plan = _stage_plan()
    envelope = ValidatorInputEnvelopeBuilder(
        validation_view_resolver=resolver
    ).build(
        validation_run_id="validation-run:projection",
        subject_hash="a" * 64,
        artifact_state_ref=artifact_ref,
        stage_plan=plan,
        applicable_expected_validation_item_refs=("item-1",),
        exact_authority_refs=("entry://projection",),
        execution_config_refs=("config://projection",),
    )

    assert envelope.artifact_state_ref == artifact_ref
    assert envelope.artifact_visible_projection_ref == projection.as_ref()
    assert (
        envelope.artifact_visible_projection_sha256
        == projection.projection_sha256
    )
    assert envelope.input_envelope_ref == (
        "validator-input://sha256/" + envelope.input_hash
    )


def test_target_validator_modules_do_not_read_raw_or_latest_document_authority() -> None:
    modules = (
        "faithfulness.py",
        "citation.py",
        "shared_claim_support.py",
    )
    service_root = (
        BACKEND_ROOT
        / "apps"
        / "enterprise_document"
        / "services"
        / "post_write"
    )
    forbidden = (
        "SchemeDraftSchema",
        "schemas.scheme_writer.document",
        ".rendered_output_ref",
        ".canonical_payload_ref",
        "latest_scheme",
        "latest_document",
    )
    for name in modules:
        source = (service_root / name).read_text(encoding="utf-8")
        for token in forbidden:
            assert token not in source, f"{name} bypasses visible projection via {token}"
