from __future__ import annotations

import json

import pytest

from apps.enterprise_document.services.scheme_writer.evidence_service import (
    SchemeEvidenceService,
)
from contracts.evidence import build_evidence_bundle, evidence_bundle_sha256
from rag.store.evidence_bundle_store import (
    EvidenceBundleSnapshotIntegrityError,
    EvidenceBundleSnapshotNotFound,
    EvidenceBundleSnapshotStore,
)
from schemas.citation import CitationSchema
from schemas.rag import RetrievedChunkSchema


def _bundle(*, query: str = "canonical evidence", run_id: str = "run-store-1"):
    chunk = RetrievedChunkSchema(
        rank=1,
        matched_chunk_id="child-1",
        context_chunk_id="parent-1",
        child_chunk_id="child-1",
        parent_chunk_id="parent-1",
        doc_id="doc-1",
        match_text="matched evidence",
        context_text="canonical evidence text",
        title="Evidence",
    )
    citation = CitationSchema(
        citation_id="source-local",
        source_type="document",
        source_document_id="doc-1",
        doc_id="doc-1",
        child_chunk_id="child-1",
        parent_chunk_id="parent-1",
        quote_text="canonical evidence text",
        title="Evidence",
    )
    bundle = build_evidence_bundle(
        query=query,
        rewritten_queries=[],
        selected_chunks=[chunk],
        dropped_chunks=[],
        citations=[citation],
        trace=None,
        max_context_chars=6000,
    )
    return bundle.model_copy(
        update={
            "task_id": "task-store-1",
            "run_id": run_id,
            "retrieval_trace_id": "trace-store-1",
            "retrieval_scope": "section",
        },
        deep=True,
    )


def test_snapshot_persist_resolve_and_restart_round_trip(tmp_path) -> None:
    bundle = _bundle()
    root = tmp_path / "rag-evidence-bundles"
    first_store = EvidenceBundleSnapshotStore(root)

    ref = first_store.persist(bundle)
    assert ref.handle.startswith("rag-evidence-bundle://v1/")
    assert ref.evidence_bundle_sha256 == "sha256:" + evidence_bundle_sha256(bundle)
    assert first_store.resolve(ref.handle) == bundle

    restarted_store = EvidenceBundleSnapshotStore(root)
    assert restarted_store.resolve(ref.handle) == bundle
    assert restarted_store.resolve_ref(ref.handle) == ref


def test_same_exact_bundle_replay_reuses_same_opaque_handle(tmp_path) -> None:
    store = EvidenceBundleSnapshotStore(tmp_path / "rag-evidence-bundles")
    bundle = _bundle()

    first = store.persist(bundle)
    replay = store.persist(bundle.model_copy(deep=True))

    assert replay == first
    assert store.resolve(first.handle) == bundle


def test_changed_canonical_bundle_gets_different_handle_and_hash(tmp_path) -> None:
    store = EvidenceBundleSnapshotStore(tmp_path / "rag-evidence-bundles")

    first = store.persist(_bundle(query="first"))
    second = store.persist(_bundle(query="second"))

    assert first.handle != second.handle
    assert first.evidence_bundle_sha256 != second.evidence_bundle_sha256


def test_missing_handle_fails_closed(tmp_path) -> None:
    store = EvidenceBundleSnapshotStore(tmp_path / "rag-evidence-bundles")
    missing = "rag-evidence-bundle://v1/" + "a" * 64

    with pytest.raises(EvidenceBundleSnapshotNotFound):
        store.resolve(missing)


def test_corrupt_snapshot_fails_closed_instead_of_reconstructing_truth(tmp_path) -> None:
    store = EvidenceBundleSnapshotStore(tmp_path / "rag-evidence-bundles")
    ref = store.persist(_bundle())
    path = store._path_for_handle(ref.handle)

    envelope = json.loads(path.read_text(encoding="utf-8"))
    envelope["evidence_bundle_sha256"] = "sha256:" + "b" * 64
    path.write_text(
        json.dumps(
            envelope,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        + "\n",
        encoding="utf-8",
    )

    with pytest.raises(
        EvidenceBundleSnapshotIntegrityError,
        match="canonical hash mismatch",
    ):
        store.resolve(ref.handle)


def test_scheme_evidence_service_forms_exact_resolver_backed_binding(tmp_path) -> None:
    bundle = _bundle()
    store = EvidenceBundleSnapshotStore(tmp_path / "rag-evidence-bundles")
    service = SchemeEvidenceService(
        rag_service=None,
        agent_name="test",
        evidence_bundle_store=store,
    )

    binding = service.canonical_bundle_binding(bundle)

    assert binding.evidence_bundle_sha256 == "sha256:" + evidence_bundle_sha256(bundle)
    assert store.resolve(binding.evidence_bundle_handle) == bundle


def test_invalid_handle_shape_is_not_treated_as_hash_or_trace_alias(tmp_path) -> None:
    store = EvidenceBundleSnapshotStore(tmp_path / "rag-evidence-bundles")

    for invalid in (
        "",
        "trace-store-1",
        evidence_bundle_sha256(_bundle()),
        "rag-evidence-bundle://v1/not-a-sha",
    ):
        with pytest.raises(EvidenceBundleSnapshotIntegrityError):
            store.resolve(invalid)
