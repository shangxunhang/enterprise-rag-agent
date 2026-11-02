"""Independent fixtures for the Section Evidence-to-Citation association."""

from copy import deepcopy
from types import SimpleNamespace
from unittest.mock import Mock, patch

import pytest

from apps.enterprise_document.schemas.agent_rag_v1.research import CanonicalEvidenceBundleBindingSchema
from apps.enterprise_document.schemas.agent_rag_v1.research import EvidencePackageSelectionSchema
from apps.enterprise_document.services.agent_rag_v1.writer_evidence_projection import project_writer_evidence
from schemas.rag import RAGEvidenceAssessmentSchema, RAGEvidenceItemSchema
from schemas.citation import CitationSchema


_C1_REF = "b66f07a9f974b9360cf3516690b1dc2e3d267d16e8c4fafa171a0cd2eb88bdb7"
_C2_REF = "6009e9822ee6b51a779617e2b1edc3999847eab6c9cfc59572ef71c9a39ea4e6"
_E1_REF = "evidence-item://sha256/01632119855741c01fba6a2156847d535637ef64c501816a7b3d3d905034a91a"
_E2_REF = "evidence-item://sha256/7f6b2cbcb580fd04de6be0f8f8aa2b7730d9942b07ed0dfda36e3c5bf3a28c4f"
_C1_LOCATOR_REF = "citation-locator://sha256/f5b542576b5361e64dba2feb91278ddbf019bca25ab9f36691d72b2d77e729f5"
_C2_LOCATOR_REF = "citation-locator://sha256/bb6c989f48ee8d0b99645fe550ab3ce3938688802da6f865ccd50adf6fa121ea"


def _citation(alias: str, *, doc_id: str, chunk_id: str, citation_ref: str) -> CitationSchema:
    return CitationSchema(
        citation_id=alias, citation_ref=citation_ref, source_type="chunk",
        doc_id=doc_id, chunk_id=chunk_id,
    )


def _inputs(*, package_ids=("E1", "E2"), selected_ids=("E2", "E1"), citations=None, items=None):
    binding = CanonicalEvidenceBundleBindingSchema(
        evidence_bundle_handle="bundle://exact", evidence_bundle_sha256="sha256:" + "a" * 64,
    )
    package = SimpleNamespace(
        evidence_bundle_binding=binding,
        selections=tuple(SimpleNamespace(evidence_id=value) for value in package_ids),
    )
    bundle = SimpleNamespace(
        selected_evidence_ids=list(selected_ids),
        items=list(items if items is not None else (
            SimpleNamespace(evidence_id="E2", citation_ids=["C1"]),
            SimpleNamespace(evidence_id="E1", citation_ids=["C1"]),
            SimpleNamespace(evidence_id="E3", citation_ids=["C2"]),
        )),
        citations=list(citations if citations is not None else (
            _citation("C1", doc_id="doc-a", chunk_id="chunk-a", citation_ref=_C1_REF),
            _citation("C2", doc_id="doc-b", chunk_id="chunk-b", citation_ref=_C2_REF),
        )),
    )
    return package, bundle


def test_selected_association_preserves_exact_rows_and_multiplicity() -> None:
    from apps.enterprise_document.services.agent_rag_v1.citation_association import (
        associate_selected_citations, writer_allowed_aliases,
    )

    package, bundle = _inputs()
    before = deepcopy((package, bundle))
    rows = associate_selected_citations(package=package, bundle=bundle)
    assert [
        (row.alias, row.citation_ref, row.evidence_id, row.exact_evidence_ref,
         row.source_identity_consistent, row.citation_source_identity, row.source_locator_ref)
        for row in rows
    ] == [
        ("C1", _C1_REF, "E1", _E1_REF, True, _C1_REF, _C1_LOCATOR_REF),
        ("C1", _C1_REF, "E2", _E2_REF, True, _C1_REF, _C1_LOCATOR_REF),
    ]
    assert rows[0].source_locator == {
        "source_type": "chunk", "doc_id": "doc-a", "source_document_id": None,
        "file_id": None, "chunk_id": "chunk-a", "child_chunk_id": None,
        "parent_chunk_id": None, "table_id": None, "row_ids": [],
        "page_start": None, "page_end": None,
    }
    assert writer_allowed_aliases(rows) == (), "two exact sources remain ambiguous"
    assert (package, bundle) == before


def test_unique_selected_alias_is_recommended_and_empty_scope_stays_empty() -> None:
    from apps.enterprise_document.services.agent_rag_v1.citation_association import (
        associate_selected_citations, writer_allowed_aliases,
    )

    package, bundle = _inputs(
        package_ids=("E1",), selected_ids=("E1", "E2"),
        items=(SimpleNamespace(evidence_id="E1", citation_ids=["C1"]),
               SimpleNamespace(evidence_id="E2", citation_ids=["C2"])),
    )
    rows = associate_selected_citations(package=package, bundle=bundle)
    assert [(row.alias, row.citation_ref, row.evidence_id, row.exact_evidence_ref,
             row.source_locator_ref) for row in rows] == [
        ("C1", _C1_REF, "E1", _E1_REF, _C1_LOCATOR_REF),
    ]
    assert writer_allowed_aliases(rows) == ("C1",)
    package.selections = ()
    assert associate_selected_citations(package=package, bundle=bundle) == ()
    assert writer_allowed_aliases(()) == ()


def test_identity_mismatch_stays_visible_but_is_never_recommended() -> None:
    from apps.enterprise_document.services.agent_rag_v1.citation_association import (
        associate_selected_citations, writer_allowed_aliases,
    )

    package, bundle = _inputs(
        package_ids=("E1",), selected_ids=("E1",),
        citations=(_citation("C1", doc_id="doc-a", chunk_id="chunk-a", citation_ref="foreign-source"),),
        items=(SimpleNamespace(evidence_id="E1", citation_ids=["C1"]),),
    )
    rows = associate_selected_citations(package=package, bundle=bundle)
    assert [(row.alias, row.citation_ref, row.evidence_id, row.exact_evidence_ref,
             row.source_identity_consistent, row.citation_source_identity)
            for row in rows] == [
        ("C1", "foreign-source", "E1", _E1_REF, False, _C1_REF),
    ]
    assert writer_allowed_aliases(rows) == ()


@pytest.mark.parametrize("citation, ref_present, alias_well_formed, message", (
    (_citation("C1", doc_id="doc-a", chunk_id="chunk-a", citation_ref=""),
     False, True,
     "canonical Citation source identity is missing"),
    (_citation("C 1", doc_id="doc-a", chunk_id="chunk-a", citation_ref=_C1_REF),
     True, False,
     "canonical Citation alias is outside the bound syntax contract"),
))
def test_selected_invalid_canonical_citation_is_observable_before_original_post_write_failure(
    citation, ref_present, alias_well_formed, message,
) -> None:
    from core.hashing import canonical_sha256
    from schemas.rag import EvidenceBundleSchema, RAGContextSchema
    from apps.enterprise_document.services.agent_rag_v1.citation_association import (
        CitationProjectionIntegrityError, associate_selected_citations, writer_allowed_aliases,
    )

    package, bundle = _inputs(
        package_ids=("E1",), selected_ids=("E1",), citations=(citation,),
        items=(RAGEvidenceItemSchema(
            evidence_id="E1", disposition="selected", rank=1,
            matched_chunk_id="chunk-a", context_chunk_id="chunk-a", doc_id="doc-a",
            match_text="auditable source", context_text="visible source",
            citation_ids=[citation.citation_id],
        ),),
    )
    package.selections = (EvidencePackageSelectionSchema(evidence_id="E1", requirement_ids=("REQ1",)),)
    bundle.assessment = RAGEvidenceAssessmentSchema()
    # External canonical inputs retain the original bad alias/ref; the builder
    # must not normalize them before the observability and fail-closed checks.
    bundle = EvidenceBundleSchema(
        query="selected invalid canonical citation", items=bundle.items,
        selected_evidence_ids=bundle.selected_evidence_ids, citations=bundle.citations,
        context=RAGContextSchema(context_text="visible source",
                                used_context_chars=len("visible source"), context_item_count=1),
        assessment=bundle.assessment,
    )
    bundle_before = canonical_sha256(bundle.model_dump(mode="json", round_trip=True))

    rows = associate_selected_citations(package=package, bundle=bundle)
    assert len(rows) == 1
    assert (rows[0].alias, rows[0].citation_ref, rows[0].evidence_id,
            rows[0].exact_evidence_ref) == (
        citation.citation_id, citation.citation_ref, "E1", _E1_REF,
    )
    assert (rows[0].citation_ref_present, rows[0].alias_well_formed) == (
        ref_present, alias_well_formed,
    )
    assert writer_allowed_aliases(rows) == ()

    with pytest.raises(ValueError, match="canonical builder source/order mapping"):
        project_writer_evidence(
            binding=package.evidence_bundle_binding, package=package, bundle=bundle,
            context_text="visible source", citations=bundle.citations,
        )
    assert bundle_before == canonical_sha256(bundle.model_dump(mode="json", round_trip=True))

    with pytest.raises(CitationProjectionIntegrityError) as exc_info:
        _section_catalog_from_current_sources(package, bundle)
    assert str(exc_info.value) == message


def test_unassociated_canonical_citation_preserves_existing_skip_order() -> None:
    from apps.enterprise_document.services.agent_rag_v1.citation_association import associate_selected_citations

    package, bundle = _inputs(
        package_ids=(), selected_ids=("E1",),
        citations=(_citation("C 1", doc_id="doc-a", chunk_id="chunk-a", citation_ref=""),),
        items=(SimpleNamespace(evidence_id="E1", citation_ids=["C 1"]),),
    )
    assert associate_selected_citations(package=package, bundle=bundle) == ()


def _section_catalog_from_current_sources(package, bundle):
    import apps.enterprise_document.services.agent_rag_v1.post_write_citation_projection as projection

    subject = SimpleNamespace(run_id="run-1", section_id="section-1")
    evidence_ref = SimpleNamespace(
        writer_invocation_ref=SimpleNamespace(
            logical_execution_key=SimpleNamespace(writer_subject_ref=subject)),
        evidence_bundle_sha256="a" * 64,
        model_dump=lambda **_: {"ref": "evidence-1"},
    )
    artifact_ref = SimpleNamespace(model_dump=lambda **_: {"artifact_id": "artifact-1"})
    invocation = SimpleNamespace(canonical_evidence_input=SimpleNamespace(evidence_bundle_sha256="a" * 64))
    current_binding = object()
    research = SimpleNamespace(
        resolve_current_evidence_package_binding=Mock(return_value=current_binding),
        validate_evidence_package_binding_current=Mock(return_value=package),
    )
    post_write = SimpleNamespace(
        writer_execution_store=SimpleNamespace(resolve_writer_generation_invocation=Mock(return_value=invocation)),
        _resolve_section_evidence_snapshot=Mock(return_value=bundle),
        artifact_store=SimpleNamespace(resolve_exact=Mock(return_value=SimpleNamespace(artifact_ref=artifact_ref, canonical_payload_ref={}))),
        research_runtime=research,
    )
    with patch.object(projection, "evidence_bundle_sha256", return_value="a" * 64), patch.object(
        projection.ArtifactCanonicalPayloadMaterializer, "business_payload",
        return_value={"section_id": "section-1"},
    ):
        catalog = projection.TargetPostWriteCitationIntegrity(post_write).section_catalog(
            section_artifact_ref=artifact_ref, evidence_ref=evidence_ref,
        )
    return catalog, research, current_binding


def _facts_for_alias(catalog, alias: str):
    from apps.enterprise_document.schemas.post_write.quality import CitationOccurrenceTargetSchema
    from apps.enterprise_document.services.agent_rag_v1.post_write_citation_projection import TargetPostWriteCitationIntegrity
    from test_multi_agent_v3_post_write_semantic_quality import _artifact_ref, _subject_hash

    occurrence = CitationOccurrenceTargetSchema(
        occurrence_ref="citation-occurrence://" + alias,
        artifact_state_ref=_artifact_ref(), validation_subject_hash=_subject_hash(),
        citation_alias=alias, source_span_ref="artifact-span://" + alias,
        occurrence_hash="a" * 64,
    )
    snapshot = SimpleNamespace(catalog=catalog, occurrences=({
        "target": occurrence.model_dump(mode="json"), "well_formed": True,
        "span": {"section_id": "section-1"},
    },))
    return TargetPostWriteCitationIntegrity.facts(
        snapshot, validation_run_id="validation-run:" + alias, subject_hash=_subject_hash(),
    )


def test_post_write_rebuilds_v1_catalog_from_current_exact_sources() -> None:
    import apps.enterprise_document.services.agent_rag_v1.post_write_citation_projection as projection
    from apps.enterprise_document.services.agent_rag_v1.citation_association import CitationProjectionIntegrityError

    package, bundle = _inputs(
        package_ids=("E1",), selected_ids=("E1",),
        items=(SimpleNamespace(evidence_id="E1", citation_ids=["C1"]),),
        citations=(_citation("C1", doc_id="doc-a", chunk_id="chunk-a", citation_ref=_C1_REF),),
    )
    catalog, research, current_binding = _section_catalog_from_current_sources(package, bundle)
    assert catalog.schema_version == "post_write_citation_alias_catalog_v1"
    assert catalog.scope == "SECTION"
    source_ref = {
        "run_id": "run-1", "section_id": "section-1",
        "section_artifact_ref": {"artifact_id": "artifact-1"},
        "evidence_snapshot_ref": {"ref": "evidence-1"},
    }
    assert catalog.source_refs == (source_ref,)
    assert len(catalog.entries) == 1
    entry = catalog.entries[0]
    assert (entry.alias, entry.citation_ref, entry.exact_evidence_ref,
            entry.source_identity_consistent) == ("C1", _C1_REF, _E1_REF, True)
    assert entry.origin_refs == ({
        **source_ref,
        "exact_evidence_ref": _E1_REF,
        "source_locator_ref": _C1_LOCATOR_REF,
        "source_locator": {
            "source_type": "chunk", "doc_id": "doc-a", "source_document_id": None,
            "file_id": None, "chunk_id": "chunk-a", "child_chunk_id": None,
            "parent_chunk_id": None, "table_id": None, "row_ids": [],
            "page_start": None, "page_end": None,
        },
        "evidence_id": "E1", "citation_source_identity": _C1_REF,
    },)
    research.resolve_current_evidence_package_binding.assert_called_once_with(
        run_id="run-1", section_id="section-1",
    )
    research.validate_evidence_package_binding_current.assert_called_once_with(
        current_binding, require_source_fresh=True,
    )
    assert projection.CitationProjectionIntegrityError is CitationProjectionIntegrityError


def test_post_write_keeps_duplicate_selected_alias_ambiguous() -> None:
    from apps.enterprise_document.services.agent_rag_v1.citation_association import (
        associate_selected_citations, writer_allowed_aliases,
    )

    package, bundle = _inputs()
    catalog, _, _ = _section_catalog_from_current_sources(package, bundle)
    assert catalog.schema_version == "post_write_citation_alias_catalog_v1"
    assert [(entry.alias, entry.citation_ref, entry.exact_evidence_ref,
             entry.origin_refs[0]["evidence_id"]) for entry in catalog.entries] == [
        ("C1", _C1_REF, _E1_REF, "E1"),
        ("C1", _C1_REF, _E2_REF, "E2"),
    ]
    assert writer_allowed_aliases(associate_selected_citations(package=package, bundle=bundle)) == ()
    facts = _facts_for_alias(catalog, "C1")
    assert [(fact.payload["rule_id"], fact.semantic_state.value,
             fact.payload["reason_codes"]) for fact in facts] == [
        ("PW-R-CIT-I-01", "PASS", ["WELL_FORMED"]),
        ("PW-R-CIT-I-02", "FAIL", ["AMBIGUOUS_ALIAS"]),
    ]


def test_post_write_keeps_claimed_ref_with_mismatched_exact_source_invalid() -> None:
    from apps.enterprise_document.services.agent_rag_v1.citation_association import (
        associate_selected_citations, writer_allowed_aliases,
    )

    package, bundle = _inputs(
        package_ids=("E1",), selected_ids=("E1",),
        items=(SimpleNamespace(evidence_id="E1", citation_ids=["C1"]),),
        citations=(_citation("C1", doc_id="doc-b", chunk_id="chunk-b", citation_ref=_C1_REF),),
    )
    catalog, _, _ = _section_catalog_from_current_sources(package, bundle)
    assert catalog.schema_version == "post_write_citation_alias_catalog_v1"
    assert len(catalog.entries) == 1
    entry = catalog.entries[0]
    assert (entry.alias, entry.citation_ref, entry.exact_evidence_ref,
            entry.source_identity_consistent) == ("C1", _C1_REF, _E1_REF, False)
    assert entry.origin_refs[0]["citation_source_identity"] == _C2_REF
    assert entry.origin_refs[0]["source_locator_ref"] == _C2_LOCATOR_REF
    assert writer_allowed_aliases(associate_selected_citations(package=package, bundle=bundle)) == ()
    facts = _facts_for_alias(catalog, "C1")
    assert [(fact.payload["rule_id"], fact.semantic_state.value,
             fact.payload["reason_codes"]) for fact in facts] == [
        ("PW-R-CIT-I-01", "PASS", ["WELL_FORMED"]),
        ("PW-R-CIT-I-02", "PASS", ["RESOLVED_UNIQUE"]),
        ("PW-R-CIT-I-03", "FAIL", ["CITATION_SOURCE_IDENTITY_MISMATCH"]),
    ]


def test_internal_fact_marker_is_still_scanned_and_rejected_as_unknown_alias() -> None:
    from apps.enterprise_document.schemas.post_write.quality import CitationOccurrenceTargetSchema
    from apps.enterprise_document.services.agent_rag_v1.post_write_citation_projection import (
        CitationAliasCatalog, TargetPostWriteCitationIntegrity, scan_citation_markers,
    )
    from test_multi_agent_v3_post_write_semantic_quality import _artifact_ref, _subject_hash

    markers = scan_citation_markers("项目事实[fact_run_xxx_hardware]，另有[C999]和[C 1]")
    assert [(item.alias, item.well_formed) for item in markers] == [
        ("fact_run_xxx_hardware", True), ("C999", True), ("C 1", False),
    ]
    catalog = CitationAliasCatalog.build(
        scope="SECTION", source_refs=({"section_id": "section-1"},), entries=(),
    )
    occurrence = CitationOccurrenceTargetSchema(
        occurrence_ref="citation-occurrence://fact-marker",
        artifact_state_ref=_artifact_ref(), validation_subject_hash=_subject_hash(),
        citation_alias="fact_run_xxx_hardware", source_span_ref="artifact-span://fact-marker",
        occurrence_hash="a" * 64,
    )
    snapshot = SimpleNamespace(catalog=catalog, occurrences=({
        "target": occurrence.model_dump(mode="json"), "well_formed": True,
        "span": {"section_id": "section-1"},
    },))
    facts = TargetPostWriteCitationIntegrity.facts(
        snapshot, validation_run_id="validation-run:fact-marker", subject_hash=_subject_hash(),
    )
    assert [(fact.payload["rule_id"], fact.semantic_state.value) for fact in facts] == [
        ("PW-R-CIT-I-01", "PASS"), ("PW-R-CIT-I-02", "FAIL"),
    ]
