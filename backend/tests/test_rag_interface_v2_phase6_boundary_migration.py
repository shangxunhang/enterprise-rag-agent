from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from application.mainline_service import MainlineApplicationService
from apps.enterprise_document.quality.self_rag_judge import SelfRAGJudge
from application.retrieval_access_policy import narrow_retrieval_access_scope
from bootstrap.document_catalog_adapter import DocumentProfileCatalogAdapter
from contracts.evidence import (
    build_evidence_bundle,
    evidence_bundle_sha256,
    project_evidence_bundle,
)
from core.hashing import canonical_sha256
from core.token_estimator import DeterministicTokenEstimator
from observability.trace_summary import canonical_sha256 as trace_canonical_sha256
from rag.catalog.document_profile_registry import DocumentProfileRegistry
from schemas.citation import CitationSchema, prompt_visible_citation_text
from schemas.document_catalog import DocumentAccessDescriptorSchema
from schemas.rag import RetrievalAccessScopeSchema, RetrievedChunkSchema


def _chunk(marker: str, *, rank: int = 1) -> RetrievedChunkSchema:
    return RetrievedChunkSchema(
        rank=rank,
        matched_chunk_id=f"child-{marker}",
        context_chunk_id=f"parent-{marker}",
        child_chunk_id=f"child-{marker}",
        parent_chunk_id=f"parent-{marker}",
        doc_id=f"doc-{marker}",
        match_text=f"match {marker}",
        context_text=f"context {marker}",
        title=f"Title {marker}",
    )


def _citation(marker: str, *, extra: dict | None = None) -> CitationSchema:
    return CitationSchema(
        citation_id=f"source-{marker}",
        source_type="document",
        source_document_id=f"doc-{marker}",
        doc_id=f"doc-{marker}",
        child_chunk_id=f"child-{marker}",
        parent_chunk_id=f"parent-{marker}",
        quote_text=f"raw grounding {marker}",
        extra=dict(extra or {}),
    )


def test_phase6_evidence_public_operations_preserve_selected_projection_and_hash() -> None:
    selected = _chunk("selected")
    dropped = _chunk("dropped", rank=2)
    bundle = build_evidence_bundle(
        query="q",
        rewritten_queries=["q2"],
        selected_chunks=[selected],
        dropped_chunks=[dropped],
        citations=[_citation("selected")],
        trace=None,
        max_context_chars=1000,
        extra={"phase6": True},
    )

    context, chunks, citations = project_evidence_bundle(bundle)
    assert [item.doc_id for item in chunks] == ["doc-selected"]
    assert "context selected" in context.context_text
    assert "context dropped" not in context.context_text
    assert len(citations) == 1

    payload = bundle.model_dump(mode="json", round_trip=True)
    old_semantics_hash = hashlib.sha256(
        json.dumps(
            payload,
            ensure_ascii=False,
            sort_keys=True,
            default=str,
        ).encode("utf-8")
    ).hexdigest()
    assert evidence_bundle_sha256(bundle) == old_semantics_hash
    assert canonical_sha256(payload) == old_semantics_hash
    assert trace_canonical_sha256 is canonical_sha256


def test_phase6_prompt_visible_accessor_never_falls_back_to_raw_quote() -> None:
    raw_only = _citation("raw")
    assert prompt_visible_citation_text(raw_only) == ""

    direct = _citation("direct", extra={"prompt_visible_text": " governed direct "})
    assert prompt_visible_citation_text(direct) == "governed direct"

    nested = _citation(
        "nested",
        extra={
            "prompt_evidence_candidate_v1": {
                "prompt_visible_text": " governed nested ",
            }
        },
    )
    assert prompt_visible_citation_text(nested) == "governed nested"
    assert "prompt_visible_text" not in CitationSchema.model_fields


def _descriptor(
    document_id: str,
    *,
    tenant_id: str = "tenant-a",
    kb_id: str = "historical_case_kb",
    indexable: bool = True,
    project_relation: str = "other_project",
    project_id: str | None = "other-project",
) -> DocumentAccessDescriptorSchema:
    return DocumentAccessDescriptorSchema(
        document_id=document_id,
        tenant_id=tenant_id,
        kb_id=kb_id,
        indexable=indexable,
        project_relation=project_relation,
        project_id=project_id,
    )


class _FakeDocumentCatalog:
    def __init__(self, descriptors: list[DocumentAccessDescriptorSchema]) -> None:
        self.descriptors = list(descriptors)
        self.project_roots: list[Path] = []

    def list_access_descriptors(
        self,
        *,
        project_root: Path,
    ) -> list[DocumentAccessDescriptorSchema]:
        self.project_roots.append(project_root)
        return list(self.descriptors)


def _authorization_scope(*, allowed_doc_ids: list[str] | None = None) -> RetrievalAccessScopeSchema:
    return RetrievalAccessScopeSchema(
        tenant_id="tenant-a",
        authorized_kb_ids=[
            "project_fact_kb",
            "historical_case_kb",
            "policy_standard_kb",
            "template_style_kb",
            "technical_reference_kb",
        ],
        allowed_doc_ids=list(allowed_doc_ids or []),
    )


def test_phase6_application_authorization_preserves_frozen_reference_semantics() -> None:
    descriptors = [
        _descriptor(
            "current-match",
            kb_id="project_fact_kb",
            project_relation="current_project",
            project_id="project-a",
        ),
        _descriptor(
            "current-other",
            kb_id="project_fact_kb",
            project_relation="current_project",
            project_id="project-b",
        ),
        _descriptor("historical-other"),
        _descriptor(
            "policy-general",
            kb_id="policy_standard_kb",
            project_relation="general",
            project_id=None,
        ),
        _descriptor(
            "template-reference",
            kb_id="template_style_kb",
            project_relation="general",
            project_id=None,
        ),
        _descriptor(
            "technical-reference",
            kb_id="technical_reference_kb",
            project_relation="general",
            project_id=None,
        ),
        _descriptor("unauthorized-kb", kb_id="private-kb"),
        _descriptor("other-tenant", tenant_id="tenant-b"),
        _descriptor("not-indexable", indexable=False),
    ]

    narrowed = narrow_retrieval_access_scope(
        descriptors=descriptors,
        current_project_id="project-a",
        retrieval_access_scope=_authorization_scope(),
    )

    assert narrowed.allowed_doc_ids == [
        "current-match",
        "historical-other",
        "policy-general",
        "template-reference",
        "technical-reference",
    ]


def test_phase6_existing_whitelist_only_narrows_and_empty_intersection_fails_closed() -> None:
    descriptors = [
        _descriptor("historical-other"),
        _descriptor(
            "current-other",
            kb_id="project_fact_kb",
            project_relation="current_project",
            project_id="project-b",
        ),
    ]
    narrowed = narrow_retrieval_access_scope(
        descriptors=descriptors,
        current_project_id="project-a",
        retrieval_access_scope=_authorization_scope(
            allowed_doc_ids=["current-other", "historical-other"],
        ),
    )
    assert narrowed.allowed_doc_ids == ["historical-other"]

    with pytest.raises(ValueError, match="resolved to no authorized documents"):
        narrow_retrieval_access_scope(
            descriptors=descriptors,
            current_project_id="project-a",
            retrieval_access_scope=_authorization_scope(
                allowed_doc_ids=["current-other"],
            ),
        )


def test_phase6_mainline_requires_catalog_for_secure_project_scope_and_accepts_fake_port() -> None:
    scope = _authorization_scope()
    with pytest.raises(ValueError, match="document_catalog is required"):
        MainlineApplicationService()._bind_current_project_retrieval_scope(
            project_root=Path("."),
            current_project_id="project-a",
            retrieval_access_scope=scope,
        )

    fake = _FakeDocumentCatalog([_descriptor("historical-other")])
    service = MainlineApplicationService(document_catalog=fake)
    narrowed = service._bind_current_project_retrieval_scope(
        project_root=Path("."),
        current_project_id="project-a",
        retrieval_access_scope=scope,
    )
    assert narrowed.allowed_doc_ids == ["historical-other"]
    assert fake.project_roots == [Path(".")]


def test_phase6_real_catalog_old_and_new_effective_document_sets_match() -> None:
    root = Path(__file__).resolve().parents[2]
    catalog_path = root / "data/catalog/document_profiles_v1.jsonl"
    registry = DocumentProfileRegistry.from_jsonl(catalog_path)
    scope = RetrievalAccessScopeSchema(
        tenant_id="default",
        authorized_kb_ids=[
            "historical_case_kb",
            "policy_standard_kb",
            "project_fact_kb",
            "technical_reference_kb",
            "template_style_kb",
        ],
    )
    old_registry_ids = registry.allowed_document_ids_for_current_project(
        "gov_cloud_framework",
        authorized_kb_ids=scope.authorized_kb_ids,
    )
    # The old final RAG boundary also enforced the trusted tenant.  Model that
    # final effective set here so the comparison is final-set parity, not just
    # parity with the Registry's incomplete pre-enforcement selection.
    old_final_ids = [
        document_id
        for document_id in old_registry_ids
        if registry.get(document_id).tenant_id == scope.tenant_id
    ]

    descriptors = DocumentProfileCatalogAdapter().list_access_descriptors(
        project_root=root,
    )
    new_scope = narrow_retrieval_access_scope(
        descriptors=descriptors,
        current_project_id="gov_cloud_framework",
        retrieval_access_scope=scope,
    )
    assert new_scope.allowed_doc_ids == old_final_ids


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("", 0),
        ("中文测试", 4),
        ("abcdefgh", 2),
        ("中文 abcdefgh", 5),
        ("...   ", 3),
    ],
)
def test_phase6_neutral_token_estimator_preserves_characterization(
    text: str,
    expected: int,
) -> None:
    assert DeterministicTokenEstimator.estimate(text) == expected


def test_phase6_generation_quality_owner_preserves_checker_metadata() -> None:
    result = SelfRAGJudge(use_llm=False).check_answer(
        query="政务云安全方案",
        answer="政务云安全方案采用分区防护并强化身份认证",
        context="政务云安全方案采用分区防护并强化身份认证",
        citations=[{"citation_id": "C1"}],
    )
    assert result.metadata["checker"] == "SelfRAGJudge"
    assert result.method == "deterministic_fallback"
