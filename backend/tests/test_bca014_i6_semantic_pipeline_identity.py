"""Composition contract sensitivity without changing production versions."""
from copy import deepcopy
from types import SimpleNamespace
from unittest.mock import patch
import subprocess, sys, json, os
import pytest
from core.hashing import canonical_sha256
from apps.enterprise_document.schemas.post_write.foundation import ValidationScope
from apps.enterprise_document.services.agent_rag_v1.post_write_composition import TargetPostWriteComposition
from apps.enterprise_document.services.agent_rag_v1.post_write_pipeline_identity import (
    pipeline_identity_material, semantic_pipeline_sha256,
    requirement, integrity, coverage, claims, certainty, consistency, grounding,
)


def composition():
    return SimpleNamespace(REQUIRED_DIMENSIONS=TargetPostWriteComposition.REQUIRED_DIMENSIONS,
        requirement_adherence=requirement.TargetPostWriteRequirementAdherence,
        citation_integrity=integrity.TargetPostWriteCitationIntegrity,
        coverage_composition=coverage.TargetPostWriteCoverageComposition,
        claim_quality_projection=claims.TargetPostWriteClaimProjection,
        faithfulness_fth03=certainty.TargetPostWriteFaithfulnessFTH03,
        consistency_composition=consistency.TargetPostWriteConsistency,
        citation_grounding=grounding.TargetPostWriteCitationGrounding,
        _coverage_stage_plan=TargetPostWriteComposition._coverage_stage_plan)


def material():
    return pipeline_identity_material(composition(),ValidationScope.SECTION)


def test_live_inventory_and_stable_calls():
    one = material()
    assert one == material()
    assert len(one["composition_topology"]) == 7
    assert {node["role"] for node in one["composition_topology"]} == {"requirement_adherence","citation_integrity","coverage_composition","claim_quality_projection","faithfulness_fth03","consistency_composition","citation_grounding"}
    for node in one["composition_topology"]:
        assert set(node["validator_identity"]) == {"validator_id","validator_version","stage_plan_ref","stage_plan_version","stage_plan_hash"}
        assert node["contracts"] and node["stage_contracts"]
    assert one["required_dimensions"] is TargetPostWriteComposition.REQUIRED_DIMENSIONS


@pytest.mark.parametrize("field",["validator_version","stage_plan_ref","stage_plan_version","stage_plan_hash"])
def test_live_stage_plan_changes_hash(field):
    original = claims.TargetPostWriteClaimProjection.stage_plan()
    changed = original.model_copy(update={field: "f"*64 if field=="stage_plan_hash" else "fixture-next"})
    before = semantic_pipeline_sha256(material())
    with patch.object(claims.TargetPostWriteClaimProjection,"stage_plan",return_value=changed):
        assert semantic_pipeline_sha256(material()) != before


@pytest.mark.parametrize("target,field",[(claims.FaithfulnessProjector,"FACT_CONTRACT_VERSION"),
    (claims.TargetPostWriteClaimProjection,"CLAIM_PROJECTION_CONTRACT_VERSION"),
    (claims.TargetPostWriteClaimProjection,"BODY_FACT_CONTRACT_VERSION")])
def test_live_fact_projection_contract_changes_hash(target,field):
    before = semantic_pipeline_sha256(material())
    with patch.object(target,field,"i6-fixture-next"):
        assert semantic_pipeline_sha256(material()) != before


@pytest.mark.parametrize("change",["add","remove","required_set","required_order","order","dependency","completion_order"])
def test_set_and_topology_changes_hash(change):
    one=material(); two=deepcopy(one)
    if change=="add": two["composition_topology"] += (dict(two["composition_topology"][0],role="fixture-extra"),)
    elif change=="remove": two["composition_topology"] = two["composition_topology"][1:]
    elif change=="required_set": two["required_dimensions"] += ("fixture-extra",)
    elif change=="required_order": two["required_dimensions"] = tuple(reversed(two["required_dimensions"]))
    elif change=="order": two["composition_topology"] = tuple(reversed(two["composition_topology"]))
    elif change=="completion_order": two["completion_order"] = tuple(reversed(two["completion_order"]))
    else: two["composition_topology"][0]["depends_on"] = ("citation_integrity",)
    assert semantic_pipeline_sha256(one) != semantic_pipeline_sha256(two)


def test_runtime_composition_presence_changes_and_structural_fallback():
    full=composition(); old=semantic_pipeline_sha256(pipeline_identity_material(full,ValidationScope.DOCUMENT))
    full.coverage_composition=None
    fallback=pipeline_identity_material(full,ValidationScope.DOCUMENT)
    assert any(n["role"]=="structural_coverage" for n in fallback["composition_topology"])
    assert semantic_pipeline_sha256(fallback)!=old
    full.requirement_adherence=None
    assert semantic_pipeline_sha256(pipeline_identity_material(full,ValidationScope.DOCUMENT))!=semantic_pipeline_sha256(fallback)


def test_cross_process_determinism():
    command="from backend.tests.test_bca014_i6_semantic_pipeline_identity import material; from core.hashing import canonical_sha256; print(canonical_sha256(material()))"
    hashes=[subprocess.check_output([sys.executable,"-c",command],env=os.environ,text=True).strip() for _ in range(2)]
    assert hashes == [canonical_sha256(material())]*2
