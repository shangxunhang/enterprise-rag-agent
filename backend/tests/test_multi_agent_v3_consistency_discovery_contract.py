"""348 exact procedural/scope fences; candidate mechanics are not semantic truth."""
from unittest.mock import patch

import pytest

from apps.enterprise_document.services.agent_rag_v1.post_write_consistency_composition import (
    ConsistencyCompositionError, ConsistencyRelationReply, TargetPostWriteConsistency,
)
from apps.enterprise_document.services.post_write import consistency as owner
from apps.enterprise_document.config.post_write import ConsistencyExecutionProfile
from core.hashing import canonical_sha256
import test_post_write_consistency as fixtures


def _domain(tmp_path, count=10, scope="SECTION"):
    fx = fixtures._consistency_fixture(tmp_path)
    assertions = owner.ConsistencyAssertionMaterializer().materialize(
        artifact_state_ref=fx["artifact_ref"], validation_subject_hash=fx["run"].subject_key.subject_hash,
        assertions=[dict(source_unit_ref=f"canonical-claim:{i}", canonical_text=f"Canonical assertion {i}", typed_value=i) for i in range(count)],
    )
    sections = {a.assertion_ref: ("section-a" if scope == "SECTION" or i % 2 == 0 else "section-b")
                for i, a in enumerate(assertions)}
    args = dict(validation_run_id=fx["run"].validation_run_id, artifact_state_ref=fx["artifact_ref"],
                validation_subject_hash=fx["run"].subject_key.subject_hash,
                assertions=assertions, validation_scope=scope, assertion_sections=sections)
    planner = owner.ConsistencyDiscoveryPlanner(profile=fx["profile"])
    seeds = planner.structural_members(assertion_refs=tuple(sections), validation_scope=scope,
                                      assertion_sections=sections, exhaustive=count <= 8)
    return fx, planner, args, seeds


def _rehash(model, ref_field, hash_field, prefix, **updates):
    raw = model.model_dump(mode="json") | updates
    raw.pop(ref_field)
    raw.pop(hash_field)
    digest = canonical_sha256(raw)
    return type(model).model_validate(raw | {ref_field: prefix + digest, hash_field: digest})


@pytest.mark.parametrize("scope", ["SECTION", "DOCUMENT"])
def test_large_structural_seeds_do_not_enumerate_universal_pairs(scope):
    refs = tuple(f"a{i:05d}" for i in range(5000))
    sections = {ref: ("s0" if scope == "SECTION" else f"s{i % 7}") for i, ref in enumerate(refs)}
    with patch.object(owner, "combinations", side_effect=AssertionError("universal pair enumeration")):
        seeds = owner.ConsistencyDiscoveryPlanner.structural_members(
            assertion_refs=refs, validation_scope=scope, assertion_sections=sections, exhaustive=False,
        )
    assert len(seeds) <= len(refs)
    assert {ref for pair in seeds for ref in pair} == set(refs)
    assert all(owner.ConsistencyDiscoveryPlanner.eligible(pair, validation_scope=scope, assertion_sections=sections) for pair in seeds)


def test_document_small_n_only_compares_distinct_included_sections(tmp_path):
    fx, planner, args, seeds = _domain(tmp_path, count=8, scope="DOCUMENT")
    assert len(seeds) == 16
    plan = planner.plan(**args)
    assert len(plan.required_relation_keys) == 16
    assert all(args["assertion_sections"][a] != args["assertion_sections"][b] for a, b in seeds)


@pytest.mark.parametrize("fault", ["omitted_force", "wrong_profile", "missing_external_input"])
def test_self_consistent_but_nonconforming_plan_cannot_pass(tmp_path, fault):
    fx, planner, args, seeds = _domain(tmp_path)
    # Add an explicit relation outside the linear seed set.
    forced = (args["assertions"][0].assertion_ref, args["assertions"][-1].assertion_ref)
    plan = planner.plan(**args, required_relation_members=(forced,))
    candidate = {planner.relation_key(pair): pair for pair in (*seeds, forced)}
    if fault == "omitted_force":
        key = planner.relation_key(forced)
        candidate.pop(key)
        plan = _rehash(plan, "plan_ref", "plan_hash", "consistency-discovery-plan://sha256/",
                       required_relation_keys=sorted(candidate))
    discovery = owner.ConsistencyCandidateDiscoveryExecutor().execute(
        plan=plan, candidate_relations=candidate, complete_required_plan_accounting=True,
        discovery_profile_version=fx["profile"].profile_version,
    )
    kwargs = dict(profile=fx["profile"], validation_scope="SECTION", assertion_sections=args["assertion_sections"], required_relation_members=(forced,))
    if fault == "wrong_profile":
        raw = fx["profile"].model_dump()
        raw.pop("execution_profile_hash")
        kwargs["profile"] = ConsistencyExecutionProfile.build(**(raw | {"profile_version": "other"}))
    elif fault == "missing_external_input":
        kwargs = {}
    conformance = owner.ConsistencyPlanConformanceGuard().evaluate(plan=plan, discovery=discovery, **kwargs)
    assert conformance.state.value == "NONCONFORMING"


def _reply_material(tmp_path):
    fx, planner, args, seeds = _domain(tmp_path)
    material = dict(discovery_mode="LARGE_N_HIGH_RECALL", validation_scope="SECTION",
                    required_relation_member_sets=seeds, semantic_candidate_budget=24,
                    assertions=[a.model_dump(mode="json") for a in args["assertions"]],
                    assertion_context={ref: dict(section_id=section, claim_type="unrelated-observed-type") for ref, section in args["assertion_sections"].items()},
                    profile=fx["profile"].model_dump(mode="json"))
    rows = [dict(member_assertion_refs=pair, relation_materiality="NO_MATERIAL_RELATION", relation=None,
                 semantic_relation_description="No material relation for this candidate") for pair in seeds]
    return material, rows


@pytest.mark.parametrize("fault", ["foreign", "malformed", "omitted_force", "arity", "incomplete"])
def test_candidate_reply_scope_and_accounting_fail_closed(tmp_path, fault):
    material, rows = _reply_material(tmp_path)
    if fault == "foreign":
        rows[0]["member_assertion_refs"] = ["foreign-a", "foreign-b"]
    elif fault == "malformed":
        rows[0]["member_assertion_refs"] = [rows[0]["member_assertion_refs"][0]]
    elif fault == "omitted_force":
        rows.pop()
    elif fault == "arity":
        rows.append(rows[0] | {"member_assertion_refs": sorted(a["assertion_ref"] for a in material["assertions"][:4])})
    with pytest.raises(ValueError):
        TargetPostWriteConsistency._parse_reply(dict(discovery_complete=fault != "incomplete", results=rows), material)


def test_low_type_similarity_does_not_drop_uncertain_semantic_expansion(tmp_path):
    material, rows = _reply_material(tmp_path)
    refs = sorted(a["assertion_ref"] for a in material["assertions"])
    added = (refs[0], refs[-1])
    rows.append(dict(member_assertion_refs=added, relation_materiality="INDETERMINATE", relation=None,
                     semantic_relation_description="Different observed types; relation scope unresolved", ambiguity_codes=["UNCERTAIN_SCOPE"]))
    parsed = TargetPostWriteConsistency._parse_reply(dict(discovery_complete=True, results=rows), material)
    assert len(parsed) == 10
    assert next(row for row in parsed if row.member_assertion_refs == added).relation_materiality == "INDETERMINATE"


def test_distinct_semantic_relations_may_reuse_one_member_set():
    row = dict(member_assertion_refs=["a", "b"], relation_materiality="MATERIAL_RELATION", relation="INDETERMINATE", semantic_relation_description="same reporting period")
    reply = ConsistencyRelationReply.model_validate(dict(discovery_complete=True, results=[row, row | {"semantic_relation_description": "same unit basis"}]))
    assert len(reply.results) == 2
    with pytest.raises(ValueError, match="repeats a semantic relation binding"):
        ConsistencyRelationReply.model_validate(dict(discovery_complete=True, results=[row, row]))


def test_logical_relation_identity_excludes_physical_route_but_preserves_meaning(tmp_path):
    fx = fixtures._consistency_fixture(tmp_path)
    first = fx["binding"]
    routed = _rehash(first, "binding_ref", "binding_hash", "semantic-relation-binding://sha256/",
                     physical_model_call_ref="large-n-physical-call", provenance_refs=["different-batch"])
    different = _rehash(first, "binding_ref", "binding_hash", "semantic-relation-binding://sha256/",
                        semantic_relation_description="A different semantic relation on the same members")
    assert routed.binding_ref != first.binding_ref
    assert owner.ConsistencyProjector.logical_relation_key(first) == owner.ConsistencyProjector.logical_relation_key(routed)
    assert owner.ConsistencyProjector.logical_relation_key(first) != owner.ConsistencyProjector.logical_relation_key(different)
