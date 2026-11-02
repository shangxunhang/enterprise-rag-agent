from __future__ import annotations

import json
from pathlib import Path

import pytest

from build_document_profiles_v1 import _profiles_jsonl, build_catalog
from rag.catalog import (
    ClaimType,
    DocumentProfileCatalogAuditSchema,
    DocumentProfileRegistry,
    DocumentProfileSeedSchema,
    DocumentType,
    EvidenceRole,
    ProjectRelation,
    classify_manifest_record,
)
from rag.catalog.document_profile import CURRENT_PROJECT_CLAIM_TYPES


ROOT = Path(__file__).resolve().parents[2]
MANIFEST_PATH = ROOT / "data" / "parsed" / "document_full_v1" / "document_manifest.jsonl"
SEED_PATH = ROOT / "data" / "catalog" / "document_profile_seeds_v1.jsonl"
PROFILE_PATH = ROOT / "data" / "catalog" / "document_profiles_v1.jsonl"
AUDIT_PATH = ROOT / "data" / "catalog" / "document_profile_catalog_audit_v1.json"


def _jsonl(path: Path) -> list[dict[str, object]]:
    return [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def test_stage_b_catalog_is_deterministically_generated_from_manifest_and_seeds() -> None:
    profiles, audit = build_catalog(MANIFEST_PATH, SEED_PATH)
    assert PROFILE_PATH.read_text(encoding="utf-8") == _profiles_jsonl(profiles)
    persisted_audit = DocumentProfileCatalogAuditSchema.model_validate_json(
        AUDIT_PATH.read_text(encoding="utf-8")
    )
    assert persisted_audit == audit
    assert audit.passed is True


def test_stage_b_catalog_has_exact_manifest_coverage() -> None:
    manifest_ids = {str(row["doc_id"]) for row in _jsonl(MANIFEST_PATH)}
    registry = DocumentProfileRegistry.from_jsonl(PROFILE_PATH)
    profile_ids = {profile.document_id for profile in registry.all()}

    assert len(manifest_ids) == 31
    assert len(registry) == 31
    assert profile_ids == manifest_ids
    assert "doc_59f7609343f4286266dddf49" not in profile_ids


def test_stage_b_audit_is_persisted_and_passes() -> None:
    audit = DocumentProfileCatalogAuditSchema.model_validate_json(
        AUDIT_PATH.read_text(encoding="utf-8")
    )
    assert audit.passed is True
    assert audit.manifest_document_count == 31
    assert audit.profile_document_count == 31
    assert audit.reviewed_document_count == 31
    assert audit.unknown_document_count == 0
    assert audit.indexable_document_count == 30
    assert audit.non_indexable_document_count == 1
    assert audit.missing_profile_document_ids == []
    assert audit.orphan_profile_document_ids == []
    assert audit.source_hash_mismatch_document_ids == []


def test_stage_b_document_type_distribution_is_frozen() -> None:
    audit = DocumentProfileCatalogAuditSchema.model_validate_json(
        AUDIT_PATH.read_text(encoding="utf-8")
    )
    assert audit.document_type_counts == {
        "current_project_decision": 1,
        "current_project_requirement": 3,
        "document_template": 4,
        "historical_construction_scheme": 7,
        "historical_feasibility_study": 8,
        "policy_standard": 6,
        "technical_whitepaper": 1,
        "tender_requirement": 1,
    }
    assert audit.kb_counts == {
        "historical_case_kb": 15,
        "policy_standard_kb": 6,
        "project_fact_kb": 5,
        "technical_reference_kb": 1,
        "template_style_kb": 4,
    }


def test_gold_reference_is_profiled_but_is_the_only_non_indexable_document() -> None:
    profiles = DocumentProfileRegistry.from_jsonl(PROFILE_PATH).all()
    non_indexable = [
        profile for profile in profiles if profile.metadata["should_index"] is False
    ]

    assert [profile.document_id for profile in non_indexable] == [
        "doc_a8144d5d4e2b2478bbdfc1cd"
    ]
    gold = non_indexable[0]
    assert gold.metadata["is_gold_reference"] is True
    assert gold.metadata["catalog_disposition"] == "profiled_but_not_indexed"
    assert gold.authority.version == "0702"


def test_current_project_scope_is_limited_to_local_project() -> None:
    profiles = DocumentProfileRegistry.from_jsonl(PROFILE_PATH).all()
    current_profiles = [
        profile
        for profile in profiles
        if profile.project_relation.relation is ProjectRelation.CURRENT_PROJECT
    ]

    assert len(current_profiles) == 5
    assert {profile.project_relation.project_id for profile in current_profiles} == {
        _local_historical_project_id()
    }
    assert {profile.kb_id for profile in current_profiles} == {"project_fact_kb"}


def test_runtime_whitelist_keeps_matching_project_sources_and_all_reference_roles() -> None:
    registry = DocumentProfileRegistry.from_jsonl(PROFILE_PATH)
    allowed = registry.allowed_document_ids_for_current_project(
        _local_historical_project_id()
    )
    profiles = {profile.document_id: profile for profile in registry.all()}

    assert len(allowed) == 30
    project_fact_ids = {
        document_id
        for document_id in allowed
        if profiles[document_id].kb_id == "project_fact_kb"
    }
    assert project_fact_ids == {
        "doc_41a218696b126f8270238a45",
        "doc_5bd4faa071c0d2cb316c1add",
        "doc_9237648465447a2b4a396140",
        "doc_da8cd497ed9aa16c5cd1dd16",
    }
    assert "doc_a8144d5d4e2b2478bbdfc1cd" not in allowed


def test_runtime_whitelist_blocks_other_project_sources_without_blocking_references() -> None:
    registry = DocumentProfileRegistry.from_jsonl(PROFILE_PATH)
    allowed = registry.allowed_document_ids_for_current_project(
        "gov_cloud_framework"
    )
    profiles = {profile.document_id: profile for profile in registry.all()}

    assert len(allowed) == 26
    assert all(profiles[document_id].kb_id != "project_fact_kb" for document_id in allowed)
    assert any(
        profiles[document_id].evidence_role is EvidenceRole.HISTORICAL_CASE
        for document_id in allowed
    )
    assert any(
        profiles[document_id].evidence_role is EvidenceRole.POLICY_STANDARD
        for document_id in allowed
    )
    assert any(
        profiles[document_id].evidence_role is EvidenceRole.TEMPLATE_STYLE
        for document_id in allowed
    )
    assert any(
        profiles[document_id].evidence_role is EvidenceRole.TECHNICAL_REFERENCE
        for document_id in allowed
    )


def test_historical_cases_cannot_support_current_project_claims() -> None:
    profiles = DocumentProfileRegistry.from_jsonl(PROFILE_PATH).all()
    historical = [
        profile for profile in profiles if profile.evidence_role is EvidenceRole.HISTORICAL_CASE
    ]

    assert len(historical) == 15
    for profile in historical:
        assert profile.project_relation.relation is ProjectRelation.OTHER_PROJECT
        assert not (
            set(profile.content_capabilities.allowed_claim_types)
            & CURRENT_PROJECT_CLAIM_TYPES
        )
        assert CURRENT_PROJECT_CLAIM_TYPES <= set(
            profile.content_capabilities.forbidden_claim_types
        )


def test_templates_are_style_only_and_use_template_kb() -> None:
    profiles = DocumentProfileRegistry.from_jsonl(PROFILE_PATH).all()
    templates = [
        profile for profile in profiles if profile.document_type is DocumentType.DOCUMENT_TEMPLATE
    ]

    assert len(templates) == 4
    for profile in templates:
        assert profile.kb_id == "template_style_kb"
        assert profile.evidence_role is EvidenceRole.TEMPLATE_STYLE
        assert set(profile.content_capabilities.allowed_claim_types) == {
            ClaimType.TEMPLATE_STRUCTURE,
            ClaimType.STYLE_GUIDANCE,
        }
        assert ClaimType.CURRENT_PROJECT_FACT in set(
            profile.content_capabilities.forbidden_claim_types
        )


def test_policy_profiles_are_general_and_policy_only() -> None:
    profiles = DocumentProfileRegistry.from_jsonl(PROFILE_PATH).all()
    policies = [
        profile for profile in profiles if profile.document_type is DocumentType.POLICY_STANDARD
    ]

    assert len(policies) == 6
    for profile in policies:
        assert profile.project_relation.relation is ProjectRelation.GENERAL
        assert profile.evidence_role is EvidenceRole.POLICY_STANDARD
        assert profile.kb_id == "policy_standard_kb"
        assert profile.content_capabilities.allowed_claim_types == [
            ClaimType.POLICY_REQUIREMENT
        ]


def test_every_profile_is_reviewed_and_hash_bound() -> None:
    manifest_by_id = {
        str(row["doc_id"]): row for row in _jsonl(MANIFEST_PATH)
    }
    profiles = DocumentProfileRegistry.from_jsonl(PROFILE_PATH).all()

    for profile in profiles:
        assert profile.classification.reviewed is True
        assert profile.classification.reviewer == (
            "devspace_stage_b_catalog_review_20260803"
        )
        assert profile.metadata["review_scope"] in {
            "manifest_filename_and_parsed_structure",
            "manifest_governance_filename_and_parsed_structure",
        }
        assert profile.metadata["source_sha256"] == manifest_by_id[
            profile.document_id
        ]["source_sha256"]


def test_manifest_hash_change_invalidates_seed() -> None:
    manifest = dict(_jsonl(MANIFEST_PATH)[0])
    seed = DocumentProfileSeedSchema.model_validate(_jsonl(SEED_PATH)[0])
    manifest["source_sha256"] = "0" * 64

    with pytest.raises(ValueError, match="source hash mismatch"):
        classify_manifest_record(manifest, seed)


def test_taxonomy_fallbacks_are_explicitly_disclosed() -> None:
    registry = DocumentProfileRegistry.from_jsonl(PROFILE_PATH)

    current_feasibility = registry.get("doc_da8cd497ed9aa16c5cd1dd16")
    assert current_feasibility.metadata["source_document_form"] == "feasibility_study"
    assert current_feasibility.metadata["taxonomy_mapping"] == (
        "current_project_requirement_due_to_v1_taxonomy"
    )

    planning_work_plan = registry.get("doc_fd9ad9a09bc12d07298005a3")
    assert planning_work_plan.metadata["taxonomy_mapping"] == (
        "historical_construction_scheme_due_to_v1_taxonomy"
    )

    co2_sheet = registry.get("doc_584ac18ec1c01c6a64ffed5b")
    assert co2_sheet.parsing.parser_strategy.value == "structured_table"
    assert co2_sheet.metadata["source_document_form"] == "supplementary_calculation"


def _local_historical_project_id() -> str:
    """Original historical identity is private; never synthesize a replacement."""
    import os
    import pytest
    value = os.environ.get("RAG_LOCAL_HISTORICAL_PROJECT_ID")
    if not value:
        pytest.skip("requires local archive identity: RAG_LOCAL_HISTORICAL_PROJECT_ID")
    return value
