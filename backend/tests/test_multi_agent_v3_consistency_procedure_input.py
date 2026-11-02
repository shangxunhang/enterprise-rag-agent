"""348 approved stable procedure Contract and exact runtime lowering."""
import pytest
from types import SimpleNamespace
from unittest.mock import patch
from apps.enterprise_document.config.quality_delivery import load_semantic_quality_delivery_config
from apps.enterprise_document.schemas.post_write.gate import QualityDeliveryContractSchema
from apps.enterprise_document.schemas.post_write.consistency_procedure import (
    ConsistencyProcedureInputSchema, ConsistencyProcedureSourceRefSchema,
)
from apps.enterprise_document.schemas.post_write.foundation import ValidationTargetSelectorSchema, ValidationCompletionItemSchema
from apps.enterprise_document.schemas.post_write.requirement import PostWriteRequirementEntrySchema
from apps.enterprise_document.schemas.post_write.gate import RequiredDimensionConditionSchema
from apps.enterprise_document.services.post_write.policy import QualityDeliveryContractRegistry, QualityPolicyProjector
from apps.enterprise_document.services.post_write.persistence import QualityDeliveryContractStore, RequirementStore, PostWritePersistenceIntegrityError
from apps.enterprise_document.services.post_write.requirements import RequirementMaterializer
from apps.enterprise_document.services.post_write.consistency_procedure import ConsistencyProcedureInputResolver
from apps.enterprise_document.services.post_write.execution import ValidationCompletionBuilder
from core.hashing import canonical_sha256
import test_post_write_consistency as owner
import test_multi_agent_v3_consistency_discovery_contract as discovery_tests


def test_consistency_procedure_input_is_an_optional_typed_contract_field():
    field = QualityDeliveryContractSchema.model_fields.get("consistency_procedure_input")
    assert field is not None
    assert field.default is None


def _fixture(tmp_path, state="APPLICABLE", *, force=False, absent=False, source_kind="consistency_procedure", multiple=False):
    fx = owner._consistency_fixture(tmp_path)
    selector = ValidationTargetSelectorSchema(selector_kind="consistency_all_included_assertions",
        target_ref="consistency-obligation://declared-aggregate", selector_contract_version="consistency-logical-selector-v1")
    declaration = dict(document_global_applicability=state,
        document_global_selectors=[selector.model_dump(mode="json")] if state == "APPLICABLE" else [],
        applicability_reason="Explicit authoritative scope decision",
        force_include_obligation_refs=[selector.target_ref] if force else [],
        force_include_selectors=[selector.model_dump(mode="json")] if force else [])
    if multiple:
        declaration["document_global_selectors"].append(selector.model_copy(update={"target_ref": "consistency-obligation://other-global-meaning"}).model_dump(mode="json"))
    requirement_store = RequirementStore(fx["store"].database)
    source_ref = RequirementMaterializer(requirement_store).materialize(
        semantic_contract_version="explicit-consistency-source-v1", source_business_ref="business://explicit-global-policy",
        source_task_ref="task://stable-quality-policy",
        requirements=(PostWriteRequirementEntrySchema(requirement_id="global-scope", requirement_kind=source_kind,
            statement="Consider the declared aggregate relation over all current canonical assertions.",
            constraints={"consistency_procedure": declaration}),))
    source = ConsistencyProcedureSourceRefSchema(requirement_ref=source_ref, requirement_id="global-scope")
    procedure = ConsistencyProcedureInputSchema.build(**declaration,
        applicability_source_refs=(source,), force_include_source_refs=(source,) if force else ())
    contract_store = QualityDeliveryContractStore(fx["store"].database)
    registry = QualityDeliveryContractRegistry(contract_store)
    contract_ref = registry.register(contract_id="quality://procedure-test", contract_version="v1",
        required_dimensions=(RequiredDimensionConditionSchema(dimension_id="consistency"),),
        consistency_procedure_input=None if absent else procedure)
    universe = {"assertions": fx["assertions"], "source_hash": "a" * 64}
    def canonical_reader(artifact, subject):
        assert artifact == fx["artifact_ref"] and subject == fx["run"].subject_key.subject_hash
        return universe["assertions"], {a.assertion_ref: "included-section" for a in universe["assertions"]}, universe["source_hash"]
    resolver = ConsistencyProcedureInputResolver(contract_store=contract_store, requirement_store=requirement_store,
        canonical_universe_reader=canonical_reader, intermediate_runtime=fx["runtime"])
    kwargs = dict(contract_ref=contract_ref, artifact_state_ref=fx["artifact_ref"],
        validation_run_id=fx["run"].validation_run_id, validation_subject_hash=fx["run"].subject_key.subject_hash)
    fx.update(procedure=procedure, declaration=declaration, resolver=resolver, kwargs=kwargs, contract_store=contract_store,
              registry=registry, universe=universe, selector=selector)
    return fx


def _plan(fx, lowering):
    return owner.ConsistencyDiscoveryPlanner(profile=fx["profile"]).plan(
        validation_run_id=lowering.validation_run_id, artifact_state_ref=lowering.artifact_state_ref,
        validation_subject_hash=lowering.validation_subject_hash, assertions=fx["universe"]["assertions"],
        validation_scope="DOCUMENT_GLOBAL", assertion_sections=lowering.assertion_sections,
        runtime_procedure_input=lowering)


def test_applicable_exact_selector_drives_document_global_owner_procedure(tmp_path):
    fx = _fixture(tmp_path)
    lowering = fx["resolver"].resolve(**fx["kwargs"])
    plan = _plan(fx, lowering)
    assert lowering.required_relation_members() == ()
    assert plan.required_relation_keys == ()
    assert plan.required_logical_obligation_refs == lowering.required_logical_obligations()
    discovery = owner.ConsistencyCandidateDiscoveryExecutor().execute(
        plan=plan,
        candidate_relations={},
        complete_required_plan_accounting=True,
        discovery_profile_version=fx["profile"].profile_version,
        accounted_logical_obligation_refs=lowering.required_logical_obligations(),
    )
    conformance = owner.ConsistencyPlanConformanceGuard().evaluate(
        plan=plan,
        discovery=discovery,
        profile=fx["profile"],
        validation_scope="DOCUMENT_GLOBAL",
        assertion_sections=lowering.assertion_sections,
        runtime_procedure_input=lowering,
    )
    assert conformance.state.value == "CONFORMING"
    assert not fx["store"].list_facts_for_run(lowering.validation_run_id)


@pytest.mark.parametrize("state", ["APPLICABLE", "NOT_APPLICABLE"])
def test_determinate_applicability_requires_exact_source_and_selector(tmp_path, state):
    fx = _fixture(tmp_path, state)
    raw = fx["procedure"].model_dump(exclude={"procedure_input_hash"})
    raw["applicability_source_refs"] = []
    with pytest.raises(ValueError, match="source"):
        ConsistencyProcedureInputSchema.build(**raw)
    raw = fx["procedure"].model_dump(exclude={"procedure_input_hash"})
    raw["document_global_selectors"] = []
    if state == "APPLICABLE":
        with pytest.raises(ValueError, match="selectors"):
            ConsistencyProcedureInputSchema.build(**raw)
    else:
        raw["applicability_reason"] = None
        with pytest.raises(ValueError, match="reason"):
            ConsistencyProcedureInputSchema.build(**raw)


def test_explicit_authoritative_not_applicable_is_preserved(tmp_path):
    fx = _fixture(tmp_path, "NOT_APPLICABLE")
    lowered = fx["resolver"].resolve(**fx["kwargs"])
    assert lowered.document_global_applicability == "NOT_APPLICABLE"
    assert lowered.reason == fx["procedure"].applicability_reason and not lowered.global_work
    with pytest.raises(owner.ConsistencyIntegrityError):
        _plan(fx, lowered)


@pytest.mark.parametrize("absent", [False, True])
def test_unknown_or_missing_procedure_input_never_satisfies_global(tmp_path, absent):
    fx = _fixture(tmp_path, "UNRESOLVED", absent=absent)
    lowered = fx["resolver"].resolve(**fx["kwargs"])
    proof = fx["resolver"].commit(lowered)
    assert lowered.document_global_applicability == "UNRESOLVED"
    item = ValidationCompletionItemSchema(expected_validation_item_ref="consistency:item:document-global", required=True,
        state="INDETERMINATE", indeterminate_proof_refs=(proof,))
    completion = ValidationCompletionBuilder.build(validation_run_id=lowered.validation_run_id,
        subject_hash=lowered.validation_subject_hash, items=(item,))
    condition = QualityPolicyProjector._evaluate_condition(RequiredDimensionConditionSchema(dimension_id="consistency"), (), completion)
    assert condition.state.value == "UNRESOLVED" and not condition.positive_proof_refs


def test_legacy_contract_omits_empty_field_and_preserves_exact_hash_replay(tmp_path):
    fx = _fixture(tmp_path, absent=True)
    ref = fx["kwargs"]["contract_ref"]
    old = fx["contract_store"].resolve_exact(ref).model_dump(mode="json")
    assert "consistency_procedure_input" not in old
    for value in (None, {}):
        parsed = QualityDeliveryContractSchema.model_validate(old | {"consistency_procedure_input": value})
        assert parsed.model_dump(mode="json") == old
        assert canonical_sha256(parsed.model_dump(mode="json", exclude={"contract_hash"})) == ref.contract_hash
        assert fx["contract_store"].put_if_absent_or_same(parsed) == ref


@pytest.mark.parametrize("damage", ["selector", "source", "wrong_authority"])
def test_contract_selector_source_tamper_fails_closed(tmp_path, damage):
    fx = _fixture(tmp_path, source_kind="coverage" if damage == "wrong_authority" else "consistency_procedure")
    if damage == "wrong_authority":
        with pytest.raises(owner.ConsistencyIntegrityError, match="source declaration"):
            fx["resolver"].resolve(**fx["kwargs"])
    elif damage == "selector":
        raw = fx["procedure"].model_dump(mode="json")
        raw["document_global_selectors"][0]["target_ref"] = "consistency-obligation://tampered"
        with pytest.raises(ValueError, match="hash"):
            ConsistencyProcedureInputSchema.model_validate(raw)
    else:
        with fx["store"].database.write_transaction(immediate=True) as connection:
            connection.execute("UPDATE requirement_snapshots SET snapshot_json='{}'")
        with pytest.raises(PostWritePersistenceIntegrityError):
            fx["resolver"].resolve(**fx["kwargs"])


def test_runtime_member_change_preserves_contract_and_changes_plan(tmp_path):
    fx = _fixture(tmp_path)
    first = fx["resolver"].resolve(**fx["kwargs"])
    plan = _plan(fx, first)
    changed = owner.ConsistencyAssertionMaterializer().materialize(artifact_state_ref=fx["artifact_ref"],
        validation_subject_hash=first.validation_subject_hash,
        assertions=[dict(source_unit_ref="new-canonical-unit", canonical_text="Additional current canonical assertion")])
    fx["universe"]["assertions"] = tuple(sorted((*fx["assertions"], *changed), key=lambda a: a.assertion_ref))
    fx["universe"]["source_hash"] = "b" * 64
    second = fx["resolver"].resolve(**fx["kwargs"])
    assert first.contract_ref == second.contract_ref
    assert first.runtime_input_hash != second.runtime_input_hash
    assert plan.plan_hash != _plan(fx, second).plan_hash


@pytest.mark.parametrize("strip_mechanical_binding", [False, True])
def test_contract_force_include_omission_is_nonconforming(tmp_path, strip_mechanical_binding):
    fx = _fixture(tmp_path, force=True)
    lowering = fx["resolver"].resolve(**fx["kwargs"])
    assert lowering.force_include_work[0].member_assertion_refs == lowering.assertion_refs
    plan = _plan(fx, lowering)
    raw = plan.model_dump(mode="json", exclude={"plan_ref", "plan_hash"})
    raw["required_relation_keys"] = []
    if strip_mechanical_binding:
        raw.pop("procedure_input_hash")
    digest = canonical_sha256(raw)
    forged = type(plan).model_validate(raw | {"plan_ref": "consistency-discovery-plan://sha256/" + digest, "plan_hash": digest})
    discovery = owner.ConsistencyCandidateDiscoveryExecutor().execute(plan=forged, candidate_relations={},
        complete_required_plan_accounting=True, discovery_profile_version=fx["profile"].profile_version)
    result = owner.ConsistencyPlanConformanceGuard().evaluate(plan=forged, discovery=discovery,
        profile=fx["profile"], validation_scope="DOCUMENT_GLOBAL", assertion_sections=lowering.assertion_sections,
        runtime_procedure_input=lowering)
    assert result.state.value == "NONCONFORMING"


@pytest.mark.parametrize("damage", ["foreign", "stale"])
def test_foreign_or_stale_canonical_members_fail_closed(tmp_path, damage):
    fx = _fixture(tmp_path)
    lowered = fx["resolver"].resolve(**fx["kwargs"])
    ref = fx["resolver"].commit(lowered)
    if damage == "foreign":
        fx["universe"]["assertions"] = (fx["assertions"][0].model_copy(update={"validation_subject_hash": "f" * 64}),)
    else:
        fx["universe"]["source_hash"] = "c" * 64
    with pytest.raises(ValueError):
        fx["resolver"].replay(ref, **fx["kwargs"])


def test_cross_section_plan_cannot_satisfy_document_global_work(tmp_path):
    fx = _fixture(tmp_path)
    lowering = fx["resolver"].resolve(**fx["kwargs"])
    result = owner.ConsistencyPlanConformanceGuard().evaluate(plan=fx["plan"], discovery=fx["discovery"],
        profile=fx["profile"], validation_scope="DOCUMENT_GLOBAL", assertion_sections=lowering.assertion_sections,
        runtime_procedure_input=lowering)
    assert result.state.value == "NONCONFORMING"


def test_committed_lowering_replay_has_no_semantic_dispatch_or_proof_write(tmp_path):
    fx = _fixture(tmp_path)
    lowered = fx["resolver"].resolve(**fx["kwargs"])
    ref = fx["resolver"].commit(lowered)
    with fx["store"].database.read_transaction() as connection:
        before = tuple(connection.iterdump())
    with patch.object(owner.SemanticConsistencyEvaluator, "bind", side_effect=AssertionError("semantic redispatch")), \
         patch.object(fx["runtime"], "commit", side_effect=AssertionError("proof rewrite")):
        assert fx["resolver"].replay(ref, **fx["kwargs"]) == lowered
    with fx["store"].database.read_transaction() as connection:
        assert tuple(connection.iterdump()) == before


def test_current_production_contract_population_uses_versioned_semantic_config(tmp_path):
    from pathlib import Path
    from apps.enterprise_document.services.agent_rag_v1.post_write_composition import TargetPostWriteComposition
    fx = _fixture(tmp_path)
    config = load_semantic_quality_delivery_config(
        Path(__file__).resolve().parents[1]
        / "apps"
        / "enterprise_document"
        / "config"
        / "quality_delivery_v1.yaml"
    )
    requirement_ref = RequirementMaterializer(RequirementStore(fx["store"].database)).materialize(
        semantic_contract_version="production-quality-config-test-v1",
        source_business_ref=config.source_ref,
        source_task_ref="task://production-quality-config-test",
        requirements=(config.requirement_entry(),),
    )
    composition = SimpleNamespace(
        quality_registry=fx["registry"],
        REQUIRED_DIMENSIONS=("consistency",),
        semantic_quality_config=config,
    )
    ref = TargetPostWriteComposition._quality_contract_ref(
        composition, requirement_ref
    )
    contract = fx["contract_store"].resolve_exact(ref)
    assert contract.consistency_procedure_input == config.procedure_input(
        requirement_ref=requirement_ref
    )
    lowered = fx["resolver"].resolve(**(fx["kwargs"] | {"contract_ref": ref}))
    assert lowered.document_global_applicability == "APPLICABLE"
    assert lowered.reason == "PRODUCTION_DOCUMENT_GLOBAL_CONSISTENCY_REQUIRED"


@pytest.mark.parametrize("field,value", [("member_assertion_refs", ["claim://runtime"]),
    ("small_n_binary_member_limit", 8), ("batch_size", 32)])
def test_static_contract_forbids_runtime_members_and_physical_profile(tmp_path, field, value):
    fx = _fixture(tmp_path)
    raw = fx["procedure"].model_dump(exclude={"procedure_input_hash"})
    with pytest.raises(ValueError):
        ConsistencyProcedureInputSchema.build(**(raw | {field: value}))


def test_contract_cannot_rehash_away_an_authoritative_force_include(tmp_path):
    fx = _fixture(tmp_path, force=True)
    raw = fx["procedure"].model_dump(exclude={"procedure_input_hash"})
    raw.update(force_include_obligation_refs=[], force_include_selectors=[], force_include_source_refs=[])
    weaker = ConsistencyProcedureInputSchema.build(**raw)
    ref = fx["registry"].register(contract_id="quality://omitted-authority", contract_version="v1",
        required_dimensions=(RequiredDimensionConditionSchema(dimension_id="consistency"),), consistency_procedure_input=weaker)
    with pytest.raises(owner.ConsistencyIntegrityError, match="force-includes"):
        fx["resolver"].resolve(**(fx["kwargs"] | {"contract_ref": ref}))


@pytest.mark.parametrize("accounted", [0, 1, 2])
def test_shared_member_set_does_not_complete_distinct_logical_obligations(tmp_path, accounted):
    fx = _fixture(tmp_path, multiple=True)
    lowering = fx["resolver"].resolve(**fx["kwargs"])
    assert len(lowering.global_work) == 2
    assert lowering.required_relation_members() == ()
    plan = _plan(fx, lowering)
    members = lowering.required_relation_members()
    discovery = owner.ConsistencyCandidateDiscoveryExecutor().execute(plan=plan,
        candidate_relations={owner.ConsistencyDiscoveryPlanner.relation_key(m): m for m in members},
        complete_required_plan_accounting=True, discovery_profile_version=fx["profile"].profile_version,
        accounted_logical_obligation_refs=lowering.required_logical_obligations()[:accounted])
    conformance = owner.ConsistencyPlanConformanceGuard().evaluate(plan=plan, discovery=discovery,
        profile=fx["profile"], validation_scope="DOCUMENT_GLOBAL", assertion_sections=lowering.assertion_sections,
        runtime_procedure_input=lowering)
    assert conformance.state.value == ("CONFORMING" if accounted == 2 else "NONCONFORMING")
