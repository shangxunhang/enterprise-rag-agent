from __future__ import annotations

import ast
import hashlib
from pathlib import Path
from typing import get_args, get_origin

import pytest
from pydantic import ValidationError

from apps.enterprise_document.services.scheme_writer.evidence_service import (
    SchemeEvidenceService,
)
from contracts.evidence import build_evidence_bundle, evidence_bundle_sha256
from rag.catalog import document_profile as catalog_profile
from rag.evidence.eligibility_policy import (
    EVIDENCE_USE_ELIGIBILITY_POLICY_ID,
    EVIDENCE_USE_ELIGIBILITY_POLICY_VERSION,
    resolve_evidence_use_eligibility_policy,
)
from rag.mapping.result_mapper import RAGResultMapper
from rag.schema import evidence_assessment_v2 as compatibility_assessment
from schemas.claim import ClaimType
from schemas.evidence_assessment import (
    EvidenceAssessmentV2Schema,
    EvidenceRelevance,
)
from schemas.evidence_common import EvidenceStatus
from schemas.evidence_source import (
    AuthorityLevel,
    DocumentType,
    EvidenceRole,
    ProjectRelation,
)
from schemas.rag import (
    EvidenceBundleSchema,
    RAGEvidenceAssessmentSchema,
    RetrievedChunkSchema,
)
from schemas.citation import CitationSchema


def _chunk(*, candidate_id: str = "candidate-1") -> RetrievedChunkSchema:
    return RetrievedChunkSchema(
        rank=1,
        matched_chunk_id="child-1",
        context_chunk_id="parent-1",
        child_chunk_id="child-1",
        parent_chunk_id="parent-1",
        doc_id="doc-1",
        match_text="current project fact",
        context_text="current project fact",
        title="Evidence",
        metadata={
            "prompt_evidence_candidate_v1": {
                "candidate_id": candidate_id,
            },
            # Contradictory legacy metadata must never become Target authority.
            "evidence_assessment_v2": {
                "evidence_id": "legacy-id",
                "eligibility": False,
            },
        },
    )


def _citation() -> CitationSchema:
    return CitationSchema(
        citation_id="source-1",
        source_type="document",
        source_document_id="doc-1",
        doc_id="doc-1",
        child_chunk_id="child-1",
        parent_chunk_id="parent-1",
        quote_text="current project fact",
        title="Evidence",
    )


def _pending_bundle() -> EvidenceBundleSchema:
    return build_evidence_bundle(
        query="current project fact",
        rewritten_queries=[],
        selected_chunks=[_chunk()],
        dropped_chunks=[],
        citations=[_citation()],
        trace=None,
        max_context_chars=6000,
    )


def _assessment(
    *,
    evidence_id: str = "E1",
    policy_id: str = EVIDENCE_USE_ELIGIBILITY_POLICY_ID,
    policy_version: str = EVIDENCE_USE_ELIGIBILITY_POLICY_VERSION,
    eligibility: bool = True,
) -> EvidenceAssessmentV2Schema:
    return EvidenceAssessmentV2Schema(
        evidence_id=evidence_id,
        document_id="doc-1",
        document_type=DocumentType.CURRENT_PROJECT_REQUIREMENT,
        evidence_role=EvidenceRole.PROJECT_FACT,
        project_relation=ProjectRelation.CURRENT_PROJECT,
        authority_level=AuthorityLevel.AUTHORITATIVE,
        eligibility_semantic_policy_id=policy_id,
        eligibility_semantic_policy_version=policy_version,
        semantic_relevance=1.0 if eligibility else 0.0,
        eligibility=eligibility,
        relevance=(
            EvidenceRelevance.RELEVANT
            if eligibility
            else EvidenceRelevance.IRRELEVANT
        ),
        supported_topics=["current project fact"] if eligibility else [],
        supported_claim_types=(
            [ClaimType.CURRENT_PROJECT_FACT] if eligibility else []
        ),
        reason_codes=["eligible"] if eligibility else ["ineligible"],
    )


def _completed_bundle(
    *,
    item_assessments: list[EvidenceAssessmentV2Schema],
) -> EvidenceBundleSchema:
    pending = _pending_bundle()
    completed = pending.model_copy(
        update={
            "item_assessments": item_assessments,
            "assessment": RAGEvidenceAssessmentSchema(
                assessment_state="completed",
                status=EvidenceStatus.SUFFICIENT,
                coverage_ratio=1.0,
                required_topics=["current project fact"],
                covered_topics=["current project fact"],
                reason_codes=["coverage_sufficient"],
            ),
        },
        deep=True,
    )
    return EvidenceBundleSchema.model_validate(
        completed.model_dump(mode="python", round_trip=True)
    )


def test_public_schema_modules_are_the_single_canonical_authority() -> None:
    from schemas.rag import EvidenceStatus as RagEvidenceStatus

    assert RagEvidenceStatus is EvidenceStatus
    assert catalog_profile.DocumentType is DocumentType
    assert catalog_profile.EvidenceRole is EvidenceRole
    assert catalog_profile.ProjectRelation is ProjectRelation
    assert catalog_profile.AuthorityLevel is AuthorityLevel
    assert (
        compatibility_assessment.EvidenceAssessmentV2Schema
        is EvidenceAssessmentV2Schema
    )


def test_item_assessment_requires_explicit_semantic_policy_binding() -> None:
    payload = _assessment().model_dump(mode="python")
    payload.pop("eligibility_semantic_policy_id")
    payload.pop("eligibility_semantic_policy_version")

    with pytest.raises(ValidationError):
        EvidenceAssessmentV2Schema.model_validate(payload)


def test_target_v1_policy_resolver_accepts_only_exact_frozen_pair() -> None:
    assert resolve_evidence_use_eligibility_policy(
        EVIDENCE_USE_ELIGIBILITY_POLICY_ID,
        EVIDENCE_USE_ELIGIBILITY_POLICY_VERSION,
    ) == (
        EVIDENCE_USE_ELIGIBILITY_POLICY_ID,
        EVIDENCE_USE_ELIGIBILITY_POLICY_VERSION,
    )

    with pytest.raises(ValueError, match="unknown evidence-use eligibility"):
        resolve_evidence_use_eligibility_policy(
            EVIDENCE_USE_ELIGIBILITY_POLICY_ID,
            "v999",
        )


def test_completed_bundle_requires_exact_selected_item_assessment() -> None:
    with pytest.raises(
        ValidationError,
        match="missing selected item assessments",
    ):
        _completed_bundle(item_assessments=[])


def test_bundle_rejects_item_assessment_for_unknown_evidence_id() -> None:
    with pytest.raises(
        ValidationError,
        match="unknown evidence ids",
    ):
        _completed_bundle(item_assessments=[_assessment(evidence_id="E404")])


def test_bundle_rejects_duplicate_item_assessment_identity() -> None:
    with pytest.raises(
        ValidationError,
        match="item assessment evidence_id values must be unique",
    ):
        _completed_bundle(item_assessments=[_assessment(), _assessment()])


def test_target_v1_bundle_rejects_mixed_semantic_policy_pairs() -> None:
    pending = _pending_bundle()
    second_item = pending.items[0].model_copy(
        update={
            "evidence_id": "E2",
            "matched_chunk_id": "child-2",
            "context_chunk_id": "parent-2",
            "child_chunk_id": "child-2",
            "parent_chunk_id": "parent-2",
        },
        deep=True,
    )
    pending = pending.model_copy(
        update={
            "items": [pending.items[0], second_item],
            "selected_evidence_ids": ["E1", "E2"],
            "context": pending.context.model_copy(
                update={"context_item_count": 2},
                deep=True,
            ),
        },
        deep=True,
    )
    completed = pending.model_copy(
        update={
            "item_assessments": [
                _assessment(evidence_id="E1"),
                _assessment(evidence_id="E2", policy_version="v2"),
            ],
            "assessment": RAGEvidenceAssessmentSchema(
                assessment_state="completed",
                status=EvidenceStatus.SUFFICIENT,
                coverage_ratio=1.0,
                required_topics=["current project fact"],
                covered_topics=["current project fact"],
            ),
        },
        deep=True,
    )

    with pytest.raises(ValidationError, match="cannot mix eligibility semantic policies"):
        EvidenceBundleSchema.model_validate(
            completed.model_dump(mode="python", round_trip=True)
        )


def test_scheme_evidence_service_consumes_typed_assessment_not_legacy_metadata() -> None:
    bundle = _completed_bundle(item_assessments=[_assessment(eligibility=True)])

    resolved = SchemeEvidenceService.resolve_item_assessment(bundle, "E1")

    assert resolved.eligibility is True
    assert bundle.items[0].metadata["evidence_assessment_v2"]["eligibility"] is False


def test_scheme_evidence_service_fails_closed_on_unknown_policy_binding() -> None:
    bundle = _completed_bundle(
        item_assessments=[_assessment(policy_version="unknown-version")]
    )

    with pytest.raises(ValueError, match="unknown evidence-use eligibility"):
        SchemeEvidenceService.resolve_item_assessment(bundle, "E1")


def test_mapper_rebinds_governance_candidate_id_to_canonical_bundle_evidence_id() -> None:
    bundle = _pending_bundle()
    candidate_assessment = _assessment(evidence_id="candidate-1")

    rebound = RAGResultMapper._canonical_item_assessments(
        bundle,
        (candidate_assessment,),
    )

    assert len(rebound) == 1
    assert rebound[0].evidence_id == "E1"
    assert rebound[0].document_id == "doc-1"


def test_mapper_fails_closed_when_governance_candidate_has_no_bundle_identity() -> None:
    bundle = _pending_bundle()

    with pytest.raises(ValueError, match="cannot resolve canonical Bundle Evidence ID"):
        RAGResultMapper._canonical_item_assessments(
            bundle,
            (_assessment(evidence_id="candidate-missing"),),
        )


def test_policy_binding_participates_in_bundle_integrity_hash() -> None:
    v1 = _completed_bundle(item_assessments=[_assessment()])
    v2 = _completed_bundle(
        item_assessments=[_assessment(policy_version="v2")]
    )

    assert evidence_bundle_sha256(v1) != evidence_bundle_sha256(v2)


def test_pending_internal_bundle_may_exist_before_governance_materialization() -> None:
    bundle = _pending_bundle()

    assert bundle.assessment.assessment_state == "pending"
    assert bundle.item_assessments == []


def test_public_dependency_graph_has_no_schema_rag_to_internal_assessment_import() -> None:
    source = Path("backend/schemas/rag.py").read_text(encoding="utf-8")
    compatibility = Path(
        "backend/rag/schema/evidence_assessment_v2.py"
    ).read_text(encoding="utf-8")

    assert "rag.schema.evidence_assessment_v2" not in source
    assert "from schemas.evidence_assessment import" in source
    assert "from schemas.evidence_assessment import" in compatibility


def test_document_profile_module_does_not_redefine_public_source_enums() -> None:
    source = Path("backend/rag/catalog/document_profile.py").read_text(
        encoding="utf-8"
    )

    for symbol in (
        "DocumentType",
        "EvidenceRole",
        "ProjectRelation",
        "AuthorityLevel",
    ):
        assert f"class {symbol}(" not in source


def _production_python_sources() -> dict[Path, str]:
    backend = Path("backend")
    return {
        path: path.read_text(encoding="utf-8")
        for path in backend.rglob("*.py")
        if "tests" not in path.parts and "__pycache__" not in path.parts
    }


def _class_definition_locations(symbol: str) -> list[str]:
    locations: list[str] = []
    for path, source in _production_python_sources().items():
        tree = ast.parse(source)
        for node in ast.walk(tree):
            if isinstance(node, ast.ClassDef) and node.name == symbol:
                locations.append(path.as_posix())
    return sorted(locations)


def test_static_canonical_definition_counts_are_exactly_one() -> None:
    expected = {
        "EvidenceStatus": "backend/schemas/evidence_common.py",
        "DocumentType": "backend/schemas/evidence_source.py",
        "EvidenceRole": "backend/schemas/evidence_source.py",
        "ProjectRelation": "backend/schemas/evidence_source.py",
        "AuthorityLevel": "backend/schemas/evidence_source.py",
        "EvidenceAssessmentV2Schema": "backend/schemas/evidence_assessment.py",
    }
    for symbol, canonical_path in expected.items():
        assert _class_definition_locations(symbol) == [canonical_path]


def test_compatibility_assessment_module_is_reexport_only_and_public_graph_is_acyclic() -> None:
    compatibility_path = Path("backend/rag/schema/evidence_assessment_v2.py")
    compatibility_tree = ast.parse(compatibility_path.read_text(encoding="utf-8"))
    assert not any(
        isinstance(node, (ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef))
        for node in ast.walk(compatibility_tree)
    )
    imported_modules = {
        node.module
        for node in compatibility_tree.body
        if isinstance(node, ast.ImportFrom)
    }
    assert imported_modules <= {"__future__", "schemas.evidence_assessment"}

    rag_source = Path("backend/schemas/rag.py").read_text(encoding="utf-8")
    assessment_source = Path("backend/schemas/evidence_assessment.py").read_text(
        encoding="utf-8"
    )
    assert "rag.schema.evidence_assessment_v2" not in rag_source
    assert "from schemas.evidence_assessment import" in rag_source
    assert "from schemas.rag import" not in assessment_source
    assert "import schemas.rag" not in assessment_source


def test_bundle_item_assessment_field_is_typed_canonical_schema() -> None:
    annotation = EvidenceBundleSchema.model_fields["item_assessments"].annotation
    assert get_origin(annotation) is list
    assert get_args(annotation) == (EvidenceAssessmentV2Schema,)


def test_static_producer_mapper_service_authority_roles_are_fenced() -> None:
    governance = Path("backend/rag/evidence/governance_v2.py").read_text(
        encoding="utf-8"
    )
    mapper = Path("backend/rag/mapping/result_mapper.py").read_text(encoding="utf-8")
    rag_service = Path("backend/rag/services/rag_service.py").read_text(
        encoding="utf-8"
    )
    application = Path(
        "backend/apps/enterprise_document/services/scheme_writer/evidence_service.py"
    ).read_text(encoding="utf-8")

    assert "EvidenceAssessmentV2Schema(" in governance
    assert "eligibility=eligibility" in governance
    assert "item_assessments" in governance

    assert "def _canonical_item_assessments(" in mapper
    assert 'model_copy(update={"evidence_id": evidence_id}' in mapper
    assert "eligibility=" not in mapper
    assert "resolve_fact_claim_eligibility" not in mapper

    assert "EvidenceBundleSchema.model_validate(" in rag_service
    assert "bundle.item_assessments" in application
    assert "resolve_evidence_use_eligibility_policy(" in application
    assert 'metadata.get("evidence_assessment_v2")' not in application


def test_static_target_eligibility_evaluator_ownership_is_rag_governance_only() -> None:
    sources = _production_python_sources()
    governance_path = Path("backend/rag/evidence/governance_v2.py")
    fake_fixture_path = Path("backend/tools/fake_rag_tool.py")
    evaluator_tokens = (
        "def resolve_fact_claim_eligibility(",
        "def _item_assessment(",
        "eligibility_semantic_policy_id=EVIDENCE_USE_ELIGIBILITY_POLICY_ID",
    )
    owners = {
        path.as_posix()
        for path, source in sources.items()
        if any(token in source for token in evaluator_tokens)
    }
    assert owners == {
        governance_path.as_posix(),
        fake_fixture_path.as_posix(),
    }
    assert "def resolve_fact_claim_eligibility(" not in sources[fake_fixture_path]
    assert "def _item_assessment(" not in sources[fake_fixture_path]

    gate_paths = [
        path
        for path in sources
        if "gate" in path.name.lower() or "writer_governance" in path.name.lower()
    ]
    agent_paths = [path for path in sources if "agent" in path.parts]
    post_write_paths = [
        path for path in sources if "post_write" in path.as_posix().lower()
    ]
    forbidden_target_tokens = (
        "resolve_fact_claim_eligibility(",
        "def _item_assessment(",
        "eligibility_semantic_policy_id=EVIDENCE_USE_ELIGIBILITY_POLICY_ID",
        "eligibility_semantic_policy_id=",
    )
    for path in [*gate_paths, *agent_paths, *post_write_paths]:
        source = sources[path]
        assert not any(token in source for token in forbidden_target_tokens), path

    evidence_gate = Path(
        "backend/apps/enterprise_document/services/scheme_writer/evidence_gate_policy.py"
    ).read_text(encoding="utf-8")
    writer_governance = Path(
        "backend/apps/enterprise_document/services/scheme_writer/writer_governance.py"
    ).read_text(encoding="utf-8")
    assert "item_assessments" not in evidence_gate
    assert "eligibility_semantic_policy" not in evidence_gate
    assert "resolve_item_assessment" not in evidence_gate
    assert "EvidenceGatePolicy().evaluate(evidence_bundle.assessment)" in writer_governance


def test_legacy_metadata_is_not_target_eligibility_authority() -> None:
    mapper = Path("backend/rag/mapping/result_mapper.py").read_text(encoding="utf-8")
    application = Path(
        "backend/apps/enterprise_document/services/scheme_writer/evidence_service.py"
    ).read_text(encoding="utf-8")
    assert 'metadata.get("evidence_assessment_v2")' not in mapper
    assert 'metadata.get("evidence_assessment_v2")' not in application
    assert "final_governance.item_assessments" in mapper
    assert "bundle.item_assessments" in application


@pytest.mark.parametrize(
    ("policy_id", "policy_version"),
    [
        ("unknown_policy", EVIDENCE_USE_ELIGIBILITY_POLICY_VERSION),
        (EVIDENCE_USE_ELIGIBILITY_POLICY_ID, "unknown_version"),
    ],
)
def test_application_consumption_rejects_unknown_policy_id_or_version(
    policy_id: str,
    policy_version: str,
) -> None:
    bundle = _completed_bundle(
        item_assessments=[
            _assessment(policy_id=policy_id, policy_version=policy_version)
        ]
    )
    with pytest.raises(ValueError, match="unknown evidence-use eligibility"):
        SchemeEvidenceService.resolve_item_assessment(bundle, "E1")


def test_v1_semantic_policy_ast_fingerprint_requires_versioned_change() -> None:
    source = Path("backend/rag/evidence/governance_v2.py").read_text(encoding="utf-8")
    tree = ast.parse(source)
    semantic_functions = {
        "resolve_fact_claim_eligibility",
        "_claim_structure_compatible",
        "_item_assessment",
    }
    nodes = sorted(
        (
            node
            for node in tree.body
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
            and node.name in semantic_functions
        ),
        key=lambda node: node.name,
    )
    assert {node.name for node in nodes} == semantic_functions
    normalized = "\n".join(
        ast.dump(node, include_attributes=False) for node in nodes
    )
    fingerprint = hashlib.sha256(normalized.encode("utf-8")).hexdigest()
    frozen = {
        (
            "rag_evidence_use_eligibility",
            "v1",
        ): "6c2f2c8db332bc2bb00d0d622921dabf857fab78614a50c7fecea7b484631252"
    }
    assert frozen[
        (
            EVIDENCE_USE_ELIGIBILITY_POLICY_ID,
            EVIDENCE_USE_ELIGIBILITY_POLICY_VERSION,
        )
    ] == fingerprint
