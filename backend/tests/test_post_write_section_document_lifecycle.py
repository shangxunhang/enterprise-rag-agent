from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timedelta, timezone

import pytest

from contracts.evidence import build_evidence_bundle, evidence_bundle_sha256
from core.hashing import canonical_sha256
from schemas.citation import CitationSchema
from schemas.rag import EvidenceBundleSchema, RetrievedChunkSchema

from apps.enterprise_document.schemas.agent_rag_v1.budgets import (
    CanonicalBudgetStateSchema,
    CanonicalBudgetVectorSchema,
)
from apps.enterprise_document.schemas.post_write import (
    ActiveComparisonMode,
    ActiveDocumentBindingProjectionSchema,
    ArtifactCorrectionAuthorizationSchema,
    ArtifactScope,
    ArtifactStateRefSchema,
    AssemblyInputManifestRefSchema,
    AvailabilityState,
    AssemblyMechanicalRepairProjectionSchema,
    AssemblySelectionState,
    CorrectabilityState,
    CorrectionPolicyRefSchema,
    CorrectionPolicyRuleSchema,
    DeliveryGateDecisionRefSchema,
    DocumentCorrectionDispatchMode,
    DocumentCorrectionDispatchProjectionSchema,
    DocumentDefectOwnership,
    DocumentEvidenceSnapshotRefSchema,
    EnforcementAction,
    EvidenceSnapshotRefSchema,
    InitialWriterArtifactOriginSchema,
    OverallQualityState,
    PostWriteControlDecisionRefSchema,
    PostWriteEvidenceBindingKind,
    PostWriteEvidenceBindingRef,
    PostWriteRequirementEntrySchema,
    QualityDeliveryContractRefSchema,
    RequiredDimensionConditionSchema,
    SectionAssemblyCandidateSchema,
    ValidationCompletionItemSchema,
    ValidationCompletionItemState,
    ValidationFactFamily,
    ValidationScope,
    ValidationSemanticState,
    ValidationTargetSelectorSchema,
)
from apps.enterprise_document.schemas.pre_write_binding import WriterSubjectRefSchema
from apps.enterprise_document.schemas.pre_write_control import PreWriteRouteDecisionRefSchema
from apps.enterprise_document.schemas.pre_write_writer import (
    WriterExecutionRefSchema,
    WriterGenerationClass,
    WriterGenerationInvocationRefSchema,
    WriterLogicalExecutionKey,
)
from apps.enterprise_document.schemas.post_write.gate import (
    FactPolicyRuleSchema,
    QualityPolicyEffect,
)
from apps.enterprise_document.services.post_write import (
    ArtifactCorrectionAuthorizationBuilder,
    ArtifactUsabilityIntegrityResolver,
    ArtifactStateService,
    ArtifactStateStore,
    AssemblyInputManifestBuilder,
    CanonicalRepairBudgetProjectionAdapter,
    ControlDecisionStore,
    CorrectionPolicyRegistry,
    CorrectionPolicyResolver,
    CorrectionPolicyStore,
    CurrentnessResolver,
    DeliveryGateService,
    DocumentAssemblyError,
    DocumentBindingStore,
    DocumentCorrectionDispatchBuilder,
    DocumentDefectAttributionResolver,
    DocumentEvidenceSnapshotProjector,
    DocumentEvidenceSnapshotResolver,
    DocumentLifecycleCoordinator,
    DocumentPostWriteEntryAssembler,
    GateDecisionStore,
    GateInputIntegrityGuard,
    PostWriteControlInputIntegrityGuard,
    PostWriteControlService,
    PostWriteDocumentAssembler,
    PostWriteEntryIdentityBuilder,
    PostWriteEntryStore,
    PostWritePersistenceIntegrityError,
    PostWriteSqliteDatabase,
    QualityDeliveryContractRegistry,
    QualityDeliveryContractStore,
    RequirementMaterializer,
    RequirementStore,
    SectionAssemblyEligibility,
    SectionAssemblyEligibilityResolver,
    SectionPostWriteEntryAssembler,
    ValidationCompletionBuilder,
    ValidationRunFactory,
    ValidationRunLifecycleService,
    ValidationStore,
    ValidationSubjectKeyBuilder,
    active_selection_from_manifest_entry,
)

from backend.tests.test_post_write_correction_control import (
    _Clock as _W10Clock,
    _Ids as _W10Ids,
    _decide as _w10_decide,
    _fact as _w10_fact,
    _fixture as _w10_fixture,
)


SHA_A = "a" * 64
SHA_B = "b" * 64
SHA_C = "c" * 64
SHA_D = "d" * 64


def _hash_model(model_cls, hash_field: str, **kwargs):
    placeholder = model_cls.model_construct(**kwargs, **{hash_field: "0" * 64})
    payload = placeholder.model_dump(mode="json")
    payload.pop(hash_field, None)
    return model_cls(**kwargs, **{hash_field: canonical_sha256(payload)})


def _writer_refs(*, run_id: str, document_id: str, section_id: str, suffix: str):
    subject = WriterSubjectRefSchema(
        run_id=run_id,
        document_id=document_id,
        section_id=section_id,
    )
    route = PreWriteRouteDecisionRefSchema(route_decision_id=f"route:{suffix}")
    key = WriterLogicalExecutionKey(
        writer_subject_ref=subject,
        write_instance_id=f"write:{suffix}",
        route_decision_ref=route,
    )
    return (
        route,
        WriterExecutionRefSchema(
            logical_execution_key=key,
            result_sha256=canonical_sha256({"result": suffix}),
        ),
        WriterGenerationInvocationRefSchema(
            logical_execution_key=key,
            invocation_sha256=canonical_sha256({"invocation": suffix}),
        ),
    )


def _bundle(*, suffix: str, include_dropped: bool = False) -> EvidenceBundleSchema:
    selected = RetrievedChunkSchema(
        rank=1,
        matched_chunk_id=f"child:{suffix}:selected",
        context_chunk_id=f"parent:{suffix}:selected",
        child_chunk_id=f"child:{suffix}:selected",
        parent_chunk_id=f"parent:{suffix}:selected",
        doc_id=f"doc:{suffix}:selected",
        match_text=f"selected fact {suffix}",
        context_text=f"selected fact {suffix}",
        title=f"Selected {suffix}",
    )
    dropped = RetrievedChunkSchema(
        rank=2,
        matched_chunk_id=f"child:{suffix}:dropped",
        context_chunk_id=f"parent:{suffix}:dropped",
        child_chunk_id=f"child:{suffix}:dropped",
        parent_chunk_id=f"parent:{suffix}:dropped",
        doc_id=f"doc:{suffix}:dropped",
        match_text=f"dropped fact {suffix}",
        context_text=f"dropped fact {suffix}",
        title=f"Dropped {suffix}",
    )
    citation = CitationSchema(
        citation_id=f"citation:{suffix}",
        source_type="document",
        source_document_id=f"doc:{suffix}:selected",
        doc_id=f"doc:{suffix}:selected",
        child_chunk_id=f"child:{suffix}:selected",
        parent_chunk_id=f"parent:{suffix}:selected",
        quote_text=f"selected fact {suffix}",
        title=f"Selected {suffix}",
    )
    return build_evidence_bundle(
        query=f"query {suffix}",
        rewritten_queries=[],
        selected_chunks=[selected],
        dropped_chunks=[dropped] if include_dropped else [],
        citations=[citation],
        trace=None,
        max_context_chars=6000,
    )


def _gate_ref(suffix: str) -> DeliveryGateDecisionRefSchema:
    return DeliveryGateDecisionRefSchema(
        delivery_gate_decision_id=f"delivery-gate://{suffix}",
        schema_version="delivery_gate_decision_v1",
        gate_contract_version="delivery_gate_v1",
        decision_hash=canonical_sha256({"gate": suffix}),
    )


def _control_ref(suffix: str) -> PostWriteControlDecisionRefSchema:
    return PostWriteControlDecisionRefSchema(
        control_decision_id=f"post-write-control://{suffix}",
        schema_version="post_write_control_decision_v1",
        control_contract_version="post_write_control_v1",
        control_decision_hash=canonical_sha256({"control": suffix}),
    )


def _contract_ref(suffix: str = "main") -> QualityDeliveryContractRefSchema:
    return QualityDeliveryContractRefSchema(
        contract_id=f"quality-contract:{suffix}",
        contract_schema_version="quality_delivery_contract_v1",
        contract_version="v1",
        contract_hash=canonical_sha256({"quality-contract": suffix}),
    )


def _requirement_ref(database: PostWriteSqliteDatabase):
    return RequirementMaterializer(RequirementStore(database)).materialize(
        semantic_contract_version="post-write-requirements-v1",
        source_business_ref="business://wave11",
        source_task_ref="task://wave11",
        requirements=(
            PostWriteRequirementEntrySchema(
                requirement_id="req-wave11",
                requirement_kind="business",
                statement="assemble exact eligible sections",
            ),
        ),
    )


def _section(
    *,
    database: PostWriteSqliteDatabase,
    artifact_service: ArtifactStateService,
    entry_store: PostWriteEntryStore,
    requirement_ref,
    bundle: EvidenceBundleSchema,
    section_key: str,
    suffix: str,
    text: str,
    generation_class: WriterGenerationClass = WriterGenerationClass.NORMAL_WRITE,
    quality_state: OverallQualityState = OverallQualityState.PASS,
    action: EnforcementAction = EnforcementAction.OUTPUT,
    availability="AVAILABLE",
    parent_ref: ArtifactStateRefSchema | None = None,
):
    run_id = "run-wave11"
    document_id = "doc-wave11"
    route, execution, invocation = _writer_refs(
        run_id=run_id,
        document_id=document_id,
        section_id=section_key,
        suffix=suffix,
    )
    origin = InitialWriterArtifactOriginSchema(
        writer_execution_ref=execution,
        writer_invocation_ref=invocation,
    )
    payload = {
        "payload_kind": "section",
        "section_key": section_key,
        "text": text,
    }
    artifact_id = f"section-artifact:{run_id}:{document_id}:{section_key}"
    if parent_ref is None:
        artifact_ref = artifact_service.create_initial(
            artifact_id=artifact_id,
            artifact_scope=ArtifactScope.SECTION,
            canonical_payload_ref=payload,
            origin=origin,
        )
    else:
        artifact_ref = artifact_service.append_next_version(
            expected_parent_ref=parent_ref,
            canonical_payload_ref=payload,
            origin=origin,
        )
    evidence_ref = EvidenceSnapshotRefSchema(
        evidence_bundle_schema_version=bundle.schema_version,
        evidence_bundle_sha256=evidence_bundle_sha256(bundle),
        writer_invocation_ref=invocation,
    )
    evidence_binding = PostWriteEvidenceBindingRef(
        binding_kind=PostWriteEvidenceBindingKind.SECTION_WRITER_SNAPSHOT,
        section_writer_snapshot_ref=evidence_ref,
    )
    entry_ref = SectionPostWriteEntryAssembler(
        store=entry_store,
        identity_builder=PostWriteEntryIdentityBuilder(),
    ).assemble_and_persist(
        run_id=run_id,
        document_id=document_id,
        section_id=section_key,
        artifact_state_ref=artifact_ref,
        writer_execution_ref=execution,
        writer_invocation_ref=invocation,
        route_decision_ref=route,
        evidence_binding=evidence_binding,
        requirement_ref=requirement_ref,
        quality_delivery_contract_ref=_contract_ref(),
        generation_class=generation_class,
    )
    availability_state = {
        "AVAILABLE": AvailabilityState.AVAILABLE,
        "BEST_EFFORT": AvailabilityState.BEST_EFFORT,
    }[availability]
    kwargs = {
        "section_key": section_key,
        "section_artifact_ref": artifact_ref,
        "section_post_write_entry_ref": entry_ref,
        "section_evidence_snapshot_ref": evidence_ref,
        "section_gate_decision_ref": _gate_ref(suffix),
        "section_control_decision_ref": _control_ref(suffix),
        "generation_class": generation_class,
        "quality_state": quality_state,
        "availability_state": availability_state,
        "enforcement_action": action,
    }
    candidate = _hash_model(
        SectionAssemblyCandidateSchema,
        "candidate_hash",
        **kwargs,
    )
    return {
        "artifact_ref": artifact_ref,
        "entry_ref": entry_ref,
        "evidence_ref": evidence_ref,
        "candidate": candidate,
        "origin": origin,
    }


def _env(tmp_path):
    database = PostWriteSqliteDatabase(tmp_path / "post_write.sqlite3")
    artifact_store = ArtifactStateStore(database)
    artifact_service = ArtifactStateService(store=artifact_store)
    entry_store = PostWriteEntryStore(database)
    binding_store = DocumentBindingStore(database)
    requirement_ref = _requirement_ref(database)
    bundle_a = _bundle(suffix="shared", include_dropped=True)
    # Deliberately reuse the same canonical bundle across two Section snapshot refs
    # to prove source-qualified logical member preservation.
    bundle_b = bundle_a
    bundles = {
        evidence_bundle_sha256(bundle_a): bundle_a,
    }

    def resolve_bundle(ref: EvidenceSnapshotRefSchema) -> EvidenceBundleSchema:
        return bundles[ref.evidence_bundle_sha256].model_copy(deep=True)

    section_a = _section(
        database=database,
        artifact_service=artifact_service,
        entry_store=entry_store,
        requirement_ref=requirement_ref,
        bundle=bundle_a,
        section_key="A",
        suffix="A1",
        text="Alpha visible content",
    )
    section_b = _section(
        database=database,
        artifact_service=artifact_service,
        entry_store=entry_store,
        requirement_ref=requirement_ref,
        bundle=bundle_b,
        section_key="B",
        suffix="B1",
        text="Beta visible content",
        generation_class=WriterGenerationClass.FALLBACK_WRITE,
    )
    manifest_builder = AssemblyInputManifestBuilder(store=binding_store)
    projector = DocumentEvidenceSnapshotProjector(
        binding_store=binding_store,
        entry_store=entry_store,
        evidence_bundle_resolver=resolve_bundle,
    )
    evidence_resolver = DocumentEvidenceSnapshotResolver(
        binding_store=binding_store,
        evidence_bundle_resolver=resolve_bundle,
    )
    assembler = PostWriteDocumentAssembler(
        artifact_service=artifact_service,
        binding_store=binding_store,
    )
    coordinator = DocumentLifecycleCoordinator(
        manifest_builder=manifest_builder,
        evidence_projector=projector,
        document_assembler=assembler,
        entry_assembler=DocumentPostWriteEntryAssembler(
            store=entry_store,
            identity_builder=PostWriteEntryIdentityBuilder(),
        ),
    )
    return {
        "database": database,
        "artifact_store": artifact_store,
        "artifact_service": artifact_service,
        "entry_store": entry_store,
        "binding_store": binding_store,
        "requirement_ref": requirement_ref,
        "bundle": bundle_a,
        "resolve_bundle": resolve_bundle,
        "section_a": section_a,
        "section_b": section_b,
        "manifest_builder": manifest_builder,
        "projector": projector,
        "evidence_resolver": evidence_resolver,
        "assembler": assembler,
        "coordinator": coordinator,
    }


def _selection_entry(env, section, reason="SECTION_OUTPUT"):
    return env["manifest_builder"].selection_entry(
        section_key=section["candidate"].section_key,
        eligibility=SectionAssemblyEligibility(
            selection_state=AssemblySelectionState.INCLUDED,
            candidate=section["candidate"],
            reason_code=reason,
        ),
    )


def _state_entry(env, section_key, state, section=None):
    if state is AssemblySelectionState.INCLUDED:
        assert section is not None
        return _selection_entry(env, section)
    return env["manifest_builder"].selection_entry(
        section_key=section_key,
        eligibility=SectionAssemblyEligibility(
            selection_state=state,
            candidate=None,
            selected_section_control_decision_ref=(None if state is AssemblySelectionState.MISSING
                else (section or env['section_b'])['candidate'].section_control_decision_ref),
            reason_code=(
                "SECTION_MISSING"
                if state is AssemblySelectionState.MISSING
                else "SECTION_WITHHELD"
            ),
        ),
    )


def _partial_document(env):
    entries = (
        _selection_entry(env, env["section_a"]),
        env["manifest_builder"].missing_entry("B"),
    )
    result = env["coordinator"].materialize(
        run_id="run-wave11",
        document_id="doc-wave11",
        document_logical_id="document-logical:wave11",
        required_section_keys=("A", "B"),
        ordered_section_entries=entries,
        requirement_ref=env["requirement_ref"],
        quality_delivery_contract_ref=_contract_ref(),
    )
    return entries, result



def _document_gate_control_fixture(
    tmp_path,
    *,
    document_artifact_transform=None,
    validation_state=ValidationSemanticState.FAIL,
):
    env = _env(tmp_path)
    entries, document = _partial_document(env)
    if document_artifact_transform is not None:
        transformed_ref = document_artifact_transform(env, document)
        document = replace(
            document,
            document_artifact_ref=transformed_ref,
        )

    quality_contract_ref = QualityDeliveryContractRegistry(
        QualityDeliveryContractStore(env["database"])
    ).register(
        contract_id="quality-document-wave11",
        contract_version="v1",
        required_dimensions=(
            RequiredDimensionConditionSchema(
                dimension_id="faithfulness",
                fact_families=(ValidationFactFamily.FAITHFULNESS,),
            ),
        ),
        fact_policy_rules=(
            FactPolicyRuleSchema(
                policy_rule_ref="rule:block-document-faithfulness",
                fact_family=ValidationFactFamily.FAITHFULNESS,
                semantic_states=(ValidationSemanticState.FAIL,),
                effect=QualityPolicyEffect.BLOCKING,
                reason_code="DOCUMENT_FAITHFULNESS_FAIL_BLOCKS",
            ),
        ),
    )
    evidence_binding = document.active_projection.evidence_binding
    document_entry_ref = DocumentPostWriteEntryAssembler(
        store=env["entry_store"],
        identity_builder=PostWriteEntryIdentityBuilder(),
    ).assemble_and_persist(
        run_id="run-wave11",
        document_id="doc-wave11",
        assembly_input_manifest_ref=document.manifest_ref,
        artifact_state_ref=document.document_artifact_ref,
        evidence_binding=evidence_binding,
        requirement_ref=env["requirement_ref"],
        quality_delivery_contract_ref=quality_contract_ref,
    )
    active_projection = ActiveDocumentBindingProjectionSchema(
        run_id="run-wave11",
        document_id="doc-wave11",
        assembly_input_manifest_ref=document.manifest_ref,
        artifact_state_ref=document.document_artifact_ref,
        evidence_binding=evidence_binding,
        requirement_ref=env["requirement_ref"],
        quality_delivery_contract_ref=quality_contract_ref,
        assembly_owner_section_selections=(
            document.active_projection.assembly_owner_section_selections
        ),
    )
    currentness = CurrentnessResolver(
        entry_store=env["entry_store"],
        identity_builder=PostWriteEntryIdentityBuilder(),
        document_binding_store=env["binding_store"],
    ).prove(
        historical_entry_ref=document_entry_ref,
        active_projection=active_projection,
        comparison_mode=ActiveComparisonMode.EXPECTED_BINDING_IDENTITY,
    )

    subject_key = ValidationSubjectKeyBuilder.build(
        post_write_entry_ref=document_entry_ref,
        validation_scope=ValidationScope.DOCUMENT,
        target_selector=ValidationTargetSelectorSchema(
            selector_kind="document",
            target_ref="doc-wave11",
            selector_contract_version="selector-v1",
        ),
        subject_contract_version="subject-v1",
    )
    clock = _W10Clock()
    validation_store = ValidationStore(env["database"])
    lifecycle = ValidationRunLifecycleService(
        store=validation_store,
        clock=clock,
    )
    run = ValidationRunFactory(
        id_generator=_W10Ids(),
        clock=clock,
    ).create(
        subject_key=subject_key,
        creation_command_id="validation-create-document-wave11",
    )
    lifecycle.create(run)
    lifecycle.start(
        run.validation_run_id,
        expected_state_version=1,
        transition_command_id="validation-start-document-wave11",
    )
    fact = _w10_fact(
        run_id=run.validation_run_id,
        subject_hash=subject_key.subject_hash,
        state=validation_state,
    )
    fact_ref = validation_store.put_fact_if_absent_or_same(fact)
    completion = ValidationCompletionBuilder.build(
        validation_run_id=run.validation_run_id,
        subject_hash=subject_key.subject_hash,
        items=(
            ValidationCompletionItemSchema(
                expected_validation_item_ref="faithfulness:item-document-wave11",
                required=True,
                state=(
                    ValidationCompletionItemState.PASS
                    if validation_state is ValidationSemanticState.PASS
                    else (
                        ValidationCompletionItemState.FAIL
                        if validation_state is ValidationSemanticState.FAIL
                        else ValidationCompletionItemState.INDETERMINATE
                    )
                ),
                fact_refs=(fact_ref,),
            ),
        ),
    )
    completion_ref = validation_store.put_completion_if_absent_or_same(completion)
    lifecycle.complete(
        run.validation_run_id,
        expected_state_version=2,
        transition_command_id="validation-complete-document-wave11",
    )

    gate_store = GateDecisionStore(env["database"])
    gate = DeliveryGateService(
        input_guard=GateInputIntegrityGuard(
            validation_store=validation_store,
            entry_store=env["entry_store"],
            contract_store=QualityDeliveryContractStore(env["database"]),
        ),
        decision_store=gate_store,
        clock=lambda: 4000.0,
    ).evaluate(
        validation_run_id=run.validation_run_id,
        validation_completion_ref=completion_ref,
        quality_delivery_contract_ref=quality_contract_ref,
        currentness_proof=currentness,
    )
    expected_quality = {
        ValidationSemanticState.PASS: OverallQualityState.PASS,
        ValidationSemanticState.FAIL: OverallQualityState.FAIL,
        ValidationSemanticState.INDETERMINATE: OverallQualityState.UNRESOLVED,
    }[validation_state]
    assert gate.overall_quality_state is expected_quality

    policy_store = CorrectionPolicyStore(env["database"])
    dispatch = None
    if validation_state is ValidationSemanticState.FAIL:
        blocker = gate.quality_policy_projection.blocking_conditions[0]
        rules = (
            CorrectionPolicyRuleSchema(
                policy_rule_ref="document-correction-rule:faithfulness",
                blocking_condition_ref=blocker.condition_ref,
                correctability=CorrectabilityState.CORRECTABLE_ARTIFACT_SIDE,
                reason_code="DOCUMENT_REASSEMBLY_ALLOWED",
            ),
        )
    else:
        blocker = None
        rules = ()
    correction_policy_ref = CorrectionPolicyRegistry(policy_store).register(
        policy_id="document-correction-policy",
        policy_version="v1",
        rules=rules,
    )
    if blocker is not None:
        attribution = DocumentDefectAttributionResolver().attribute(
            document_gate_decision_ref=gate.as_ref(),
            blocking_condition_ref=blocker.condition_ref,
            ownership=DocumentDefectOwnership.ASSEMBLY_MECHANICAL,
            correction_reason_refs=("DOCUMENT_REASSEMBLY_ALLOWED",),
            exact_basis_refs=(document.manifest_ref.manifest_id,),
        )
        repair_identity = {
            "repair_kind": "REASSEMBLE_EXACT_MANIFEST",
            "correction_policy_ref": correction_policy_ref.model_dump(mode="json"),
            "correction_policy_rule_ref": "document-correction-rule:faithfulness",
            "exact_input_refs": [document.manifest_ref.manifest_id],
            "expected_reassembly_scope": "document:doc-wave11",
        }
        repair_kwargs = {
            "repair_id": (
                "assembly-mechanical-repair://sha256/"
                + canonical_sha256(repair_identity)
            ),
            "repair_kind": "REASSEMBLE_EXACT_MANIFEST",
            "correction_policy_ref": correction_policy_ref,
            "correction_policy_rule_ref": "document-correction-rule:faithfulness",
            "exact_input_refs": (document.manifest_ref.manifest_id,),
            "expected_reassembly_scope": "document:doc-wave11",
        }
        mechanical_repair = _hash_model(
            AssemblyMechanicalRepairProjectionSchema,
            "repair_projection_hash",
            **repair_kwargs,
        )
        dispatch = DocumentCorrectionDispatchBuilder.build(
            document_gate_decision_ref=gate.as_ref(),
            document_artifact_ref=document.document_artifact_ref,
            assembly_input_manifest_ref=document.manifest_ref,
            correction_mode=DocumentCorrectionDispatchMode.ASSEMBLY_MECHANICAL,
            defect_attributions=(attribution,),
            assembly_mechanical_repairs=(mechanical_repair,),
            dispatch_reason_codes=("DOCUMENT_REASSEMBLY_ALLOWED",),
        )

    now = datetime(2026, 9, 5, 12, 0, tzinfo=timezone.utc)
    budget_state = CanonicalBudgetStateSchema(
        task_id="task-wave11",
        run_id="run-wave11",
        policy_ref="budget-policy://document-wave11",
        policy_version="v1",
        limits=CanonicalBudgetVectorSchema(),
        used=CanonicalBudgetVectorSchema(),
        active_reserved=CanonicalBudgetVectorSchema(),
        remaining=CanonicalBudgetVectorSchema(),
        exhausted_dimensions=[],
        draining=False,
        revision=1,
        started_at=now,
        deadline_at=now + timedelta(hours=1),
        reservations=[],
    )
    lineage = CanonicalRepairBudgetProjectionAdapter.project(
        target_artifact_ref=document.document_artifact_ref,
        budget_state=budget_state,
    )
    usability = ArtifactUsabilityIntegrityResolver(
        env["artifact_store"]
    ).resolve(
        artifact_state_ref=document.document_artifact_ref,
        currentness_proof=currentness,
    )
    control_store = ControlDecisionStore(env["database"])
    service = PostWriteControlService(
        integrity_guard=PostWriteControlInputIntegrityGuard(
            gate_store=gate_store,
            correction_policy_resolver=CorrectionPolicyResolver(policy_store),
        ),
        control_store=control_store,
    )
    return {
        "env": env,
        "document": document,
        "document_entry_ref": document_entry_ref,
        "active_projection": active_projection,
        "currentness": currentness,
        "gate": gate,
        "policy_ref": correction_policy_ref,
        "dispatch": dispatch,
        "lineage": lineage,
        "usability": usability,
        "control_store": control_store,
        "service": service,
    }

def test_section_assembly_eligibility_uses_exact_wave10_control_decision(tmp_path) -> None:
    fx = _w10_fixture(tmp_path / "pass", quality="PASS")
    decision = _w10_decide(fx)
    resolver = SectionAssemblyEligibilityResolver(
        control_store=fx["control_store"],
        entry_store=PostWriteEntryStore(fx["database"]),
    )
    eligible = resolver.resolve(
        section_key="section-control-1",
        control_decision_ref=decision.as_ref(),
    )
    assert eligible.selection_state is AssemblySelectionState.INCLUDED
    assert eligible.candidate is not None
    assert eligible.candidate.enforcement_action is EnforcementAction.OUTPUT

    failing = _w10_fixture(tmp_path / "correct", quality="FAIL", repair_remaining=2)
    correct_decision = _w10_decide(failing)
    withheld = SectionAssemblyEligibilityResolver(
        control_store=failing["control_store"],
        entry_store=PostWriteEntryStore(failing["database"]),
    ).resolve(
        section_key="section-control-1",
        control_decision_ref=correct_decision.as_ref(),
    )
    assert withheld.selection_state is AssemblySelectionState.WITHHELD
    assert withheld.candidate is None
    assert "CORRECT" in withheld.reason_code


def test_output_best_effort_fallback_section_remains_assembly_eligible(tmp_path) -> None:
    fx = _w10_fixture(
        tmp_path,
        quality="FAIL",
        generation_class=WriterGenerationClass.FALLBACK_WRITE,
        repair_remaining=2,
    )
    decision = _w10_decide(fx)
    result = SectionAssemblyEligibilityResolver(
        control_store=fx["control_store"],
        entry_store=PostWriteEntryStore(fx["database"]),
    ).resolve(
        section_key="section-control-1",
        control_decision_ref=decision.as_ref(),
    )
    assert result.selection_state is AssemblySelectionState.INCLUDED
    assert result.candidate is not None
    assert result.candidate.generation_class is WriterGenerationClass.FALLBACK_WRITE
    assert result.candidate.enforcement_action is EnforcementAction.OUTPUT_BEST_EFFORT


def test_partial_manifest_is_complete_required_section_vector_and_idempotent(tmp_path) -> None:
    env = _env(tmp_path)
    entries = (
        _selection_entry(env, env["section_a"]),
        env["manifest_builder"].missing_entry("B"),
    )
    first = env["manifest_builder"].build_and_persist(
        document_logical_id="document-logical:wave11",
        required_section_keys=("A", "B"),
        ordered_section_entries=entries,
    )
    second = env["manifest_builder"].build_and_persist(
        document_logical_id="document-logical:wave11",
        required_section_keys=("A", "B"),
        ordered_section_entries=entries,
    )
    assert first == second
    manifest = env["binding_store"].resolve_assembly_manifest(first)
    assert tuple(item.selection_state for item in manifest.ordered_section_entries) == (
        AssemblySelectionState.INCLUDED,
        AssemblySelectionState.MISSING,
    )


def test_no_admissible_content_does_not_create_document_artifact(tmp_path) -> None:
    env = _env(tmp_path)
    entries = (
        env["manifest_builder"].missing_entry("A"),
        env["manifest_builder"].missing_entry("B"),
    )
    manifest_ref = env["manifest_builder"].build_and_persist(
        document_logical_id="document-logical:empty",
        required_section_keys=("A", "B"),
        ordered_section_entries=entries,
    )
    with pytest.raises(DocumentAssemblyError, match="no assembly-eligible"):
        env["assembler"].assemble(
            run_id="run-wave11",
            document_id="doc-empty",
            manifest_ref=manifest_ref,
        )


def test_mixed_normal_and_fallback_sections_assemble_without_document_generation_class(
    tmp_path,
) -> None:
    env = _env(tmp_path)
    entries = (
        _selection_entry(env, env["section_a"]),
        _selection_entry(env, env["section_b"], "SECTION_OUTPUT_BEST_EFFORT"),
    )
    result = env["coordinator"].materialize(
        run_id="run-wave11",
        document_id="doc-wave11",
        document_logical_id="document-logical:mixed",
        required_section_keys=("A", "B"),
        ordered_section_entries=entries,
        requirement_ref=env["requirement_ref"],
        quality_delivery_contract_ref=_contract_ref(),
    )
    entry = env["entry_store"].resolve_exact(result.document_entry_ref)
    assert entry.identity.entry_scope is ArtifactScope.DOCUMENT
    assert entry.identity.generation_class is None
    assert entry.identity.writer_execution_ref is None
    assert entry.identity.route_decision_ref is None


def test_document_evidence_projection_contains_selected_only_and_keeps_source_qualified_duplicates(
    tmp_path,
) -> None:
    env = _env(tmp_path)
    entries = (
        _selection_entry(env, env["section_a"]),
        _selection_entry(env, env["section_b"]),
    )
    manifest_ref = env["manifest_builder"].build_and_persist(
        document_logical_id="document-logical:evidence",
        required_section_keys=("A", "B"),
        ordered_section_entries=entries,
    )
    projection_ref = env["projector"].project_and_persist(manifest_ref)
    projection = env["binding_store"].resolve_document_evidence_snapshot(projection_ref)

    assert len(projection.source_bindings) == 2
    assert len(projection.selected_members) == 2
    assert [item.logical_section_id for item in projection.selected_members] == ["A", "B"]
    selected_id = env["bundle"].selected_evidence_ids[0]
    assert {item.evidence_id for item in projection.selected_members} == {selected_id}
    assert env["bundle"].dropped_evidence_ids
    assert not (
        set(env["bundle"].dropped_evidence_ids)
        & {item.evidence_id for item in projection.selected_members}
    )


def test_document_evidence_replay_resolves_persisted_projection_not_current_membership(
    tmp_path,
) -> None:
    env = _env(tmp_path)
    entries, result = _partial_document(env)
    view = env["evidence_resolver"].resolve(result.document_evidence_ref)
    assert len(view.members) == 1
    assert view.members[0].member_ref.logical_section_id == "A"
    assert view.members[0].item.evidence_id == env["bundle"].selected_evidence_ids[0]


def test_same_document_payload_changed_control_binding_reuses_artifact_but_changes_entry(
    tmp_path,
) -> None:
    env = _env(tmp_path)
    entries, first = _partial_document(env)

    old = env["section_a"]["candidate"]
    changed_candidate = _hash_model(
        SectionAssemblyCandidateSchema,
        "candidate_hash",
        section_key=old.section_key,
        section_artifact_ref=old.section_artifact_ref,
        section_post_write_entry_ref=old.section_post_write_entry_ref,
        section_evidence_snapshot_ref=old.section_evidence_snapshot_ref,
        section_gate_decision_ref=old.section_gate_decision_ref,
        section_control_decision_ref=_control_ref("A2"),
        generation_class=old.generation_class,
        quality_state=old.quality_state,
        availability_state=old.availability_state,
        enforcement_action=old.enforcement_action,
    )
    changed_entry = env["manifest_builder"].selection_entry(
        section_key="A",
        eligibility=SectionAssemblyEligibility(
            selection_state=AssemblySelectionState.INCLUDED,
            candidate=changed_candidate,
            reason_code="SECTION_OUTPUT",
        ),
    )
    second = env["coordinator"].materialize(
        run_id="run-wave11",
        document_id="doc-wave11",
        document_logical_id="document-logical:wave11",
        required_section_keys=("A", "B"),
        ordered_section_entries=(
            changed_entry,
            env["manifest_builder"].missing_entry("B"),
        ),
        requirement_ref=env["requirement_ref"],
        quality_delivery_contract_ref=_contract_ref(),
        expected_parent_ref=first.document_artifact_ref,
    )
    assert second.document_artifact_ref == first.document_artifact_ref
    assert second.manifest_ref != first.manifest_ref
    assert second.document_entry_ref != first.document_entry_ref


def test_changed_visible_section_payload_creates_next_document_artifact_version(tmp_path) -> None:
    env = _env(tmp_path)
    _entries, first = _partial_document(env)
    updated = _section(
        database=env["database"],
        artifact_service=env["artifact_service"],
        entry_store=env["entry_store"],
        requirement_ref=env["requirement_ref"],
        bundle=env["bundle"],
        section_key="A",
        suffix="A2-visible",
        text="Alpha changed visible content",
        parent_ref=env["section_a"]["artifact_ref"],
    )
    second = env["coordinator"].materialize(
        run_id="run-wave11",
        document_id="doc-wave11",
        document_logical_id="document-logical:wave11",
        required_section_keys=("A", "B"),
        ordered_section_entries=(
            _selection_entry(env, updated),
            env["manifest_builder"].missing_entry("B"),
        ),
        requirement_ref=env["requirement_ref"],
        quality_delivery_contract_ref=_contract_ref(),
        expected_parent_ref=first.document_artifact_ref,
    )
    assert second.document_artifact_ref.artifact_version == 2
    assert second.document_artifact_ref.artifact_hash != first.document_artifact_ref.artifact_hash


def test_exact_unchanged_document_selection_is_current(tmp_path) -> None:
    env = _env(tmp_path)
    _entries, result = _partial_document(env)
    proof = CurrentnessResolver(
        entry_store=env["entry_store"],
        identity_builder=PostWriteEntryIdentityBuilder(),
        document_binding_store=env["binding_store"],
    ).prove(
        historical_entry_ref=result.document_entry_ref,
        active_projection=result.active_projection,
        comparison_mode=ActiveComparisonMode.EXPECTED_BINDING_IDENTITY,
    )
    assert proof.currentness_state.value == "CURRENT"
    assert proof.match_state.value == "EXACT_MATCH"


def test_missing_to_included_transition_makes_old_partial_document_stale_before_reassembly(
    tmp_path,
) -> None:
    env = _env(tmp_path)
    _entries, result = _partial_document(env)
    fresh_b = active_selection_from_manifest_entry(
        _selection_entry(env, env["section_b"])
    )
    changed = result.active_projection.model_copy(
        update={
            "assembly_owner_section_selections": (
                result.active_projection.assembly_owner_section_selections[0],
                fresh_b,
            )
        }
    )
    proof = CurrentnessResolver(
        entry_store=env["entry_store"],
        identity_builder=PostWriteEntryIdentityBuilder(),
        document_binding_store=env["binding_store"],
    ).prove(
        historical_entry_ref=result.document_entry_ref,
        active_projection=changed,
        comparison_mode=ActiveComparisonMode.EXPECTED_BINDING_IDENTITY,
    )
    assert proof.currentness_state.value == "STALE"
    assert proof.match_state.value == "SELECTION_MISMATCH"


@pytest.mark.parametrize(
    ("historical_state", "fresh_state"),
    [
        (AssemblySelectionState.MISSING, AssemblySelectionState.INCLUDED),
        (AssemblySelectionState.WITHHELD, AssemblySelectionState.INCLUDED),
        (AssemblySelectionState.INCLUDED, AssemblySelectionState.WITHHELD),
        (AssemblySelectionState.INCLUDED, AssemblySelectionState.MISSING),
    ],
)
def test_complete_selection_membership_transitions_make_old_document_stale(
    tmp_path,
    historical_state,
    fresh_state,
) -> None:
    env = _env(tmp_path)
    historical_entries = (
        _selection_entry(env, env["section_a"]),
        _state_entry(
            env,
            "B",
            historical_state,
            env["section_b"] if historical_state is AssemblySelectionState.INCLUDED else None,
        ),
    )
    historical = env["coordinator"].materialize(
        run_id="run-wave11",
        document_id="doc-wave11",
        document_logical_id="document-logical:selection-transition",
        required_section_keys=("A", "B"),
        ordered_section_entries=historical_entries,
        requirement_ref=env["requirement_ref"],
        quality_delivery_contract_ref=_contract_ref(),
    )
    fresh_entry = _state_entry(
        env,
        "B",
        fresh_state,
        env["section_b"] if fresh_state is AssemblySelectionState.INCLUDED else None,
    )
    fresh_projection = historical.active_projection.model_copy(
        update={
            "assembly_owner_section_selections": (
                historical.active_projection.assembly_owner_section_selections[0],
                active_selection_from_manifest_entry(fresh_entry),
            )
        }
    )
    proof = CurrentnessResolver(
        entry_store=env["entry_store"],
        identity_builder=PostWriteEntryIdentityBuilder(),
        document_binding_store=env["binding_store"],
    ).prove(
        historical_entry_ref=historical.document_entry_ref,
        active_projection=fresh_projection,
        comparison_mode=ActiveComparisonMode.EXPECTED_BINDING_IDENTITY,
    )
    assert proof.currentness_state.value == "STALE"
    assert proof.match_state.value == "SELECTION_MISMATCH"


def test_incomplete_active_selection_is_indeterminate_never_current(tmp_path) -> None:
    env = _env(tmp_path)
    _entries, result = _partial_document(env)
    incomplete = result.active_projection.model_copy(
        update={
            "assembly_owner_section_selections": (
                result.active_projection.assembly_owner_section_selections[0],
            )
        }
    )
    proof = CurrentnessResolver(
        entry_store=env["entry_store"],
        identity_builder=PostWriteEntryIdentityBuilder(),
        document_binding_store=env["binding_store"],
    ).prove(
        historical_entry_ref=result.document_entry_ref,
        active_projection=incomplete,
        comparison_mode=ActiveComparisonMode.EXPECTED_BINDING_IDENTITY,
    )
    assert proof.currentness_state.value == "INDETERMINATE"
    assert proof.match_state.value == "SELECTION_UNRESOLVED"


def test_missing_or_withheld_selection_cannot_fabricate_included_lineage(tmp_path) -> None:
    env = _env(tmp_path)
    included = active_selection_from_manifest_entry(
        _selection_entry(env, env["section_a"])
    )
    payload = included.model_dump(mode="python")
    payload["selection_state"] = AssemblySelectionState.MISSING
    payload["selected_section_control_decision_ref"] = None
    with pytest.raises(ValueError, match="cannot fabricate"):
        type(included).model_validate(payload)


def test_document_defect_attribution_is_deterministic_action_routing_not_quality_truth(
    tmp_path,
) -> None:
    env = _env(tmp_path)
    _entries, document = _partial_document(env)
    resolver = DocumentDefectAttributionResolver()
    kwargs = {
        "document_gate_decision_ref": _gate_ref("document"),
        "blocking_condition_ref": "document-blocker:cross-section",
        "ownership": DocumentDefectOwnership.SECTION_CONTENT,
        "affected_section_artifact_refs": (env["section_a"]["artifact_ref"],),
        "correction_reason_refs": ("CROSS_SECTION_CONSISTENCY",),
        "exact_basis_refs": (document.manifest_ref.manifest_id,),
    }
    first = resolver.attribute(**kwargs)
    second = resolver.attribute(**kwargs)
    assert first == second
    assert first.ownership is DocumentDefectOwnership.SECTION_CONTENT
    assert not hasattr(first, "quality_state")
    assert not hasattr(first, "validation_fact")


def test_document_attributed_section_authorization_uses_target_section_generation_class(
    tmp_path,
) -> None:
    env = _env(tmp_path)
    _entries, document = _partial_document(env)
    attribution = DocumentDefectAttributionResolver().attribute(
        document_gate_decision_ref=_gate_ref("document"),
        blocking_condition_ref="document-blocker:cross-section",
        ownership=DocumentDefectOwnership.SECTION_CONTENT,
        affected_section_artifact_refs=(env["section_a"]["artifact_ref"],),
        correction_reason_refs=("CROSS_SECTION_CONSISTENCY",),
        exact_basis_refs=(document.manifest_ref.manifest_id,),
    )
    policy_ref = CorrectionPolicyRefSchema(
        policy_id="correction-policy",
        policy_schema_version="correction_policy_v1",
        policy_version="v1",
        policy_hash=SHA_C,
    )
    builder = ArtifactCorrectionAuthorizationBuilder()
    auth = builder.build_document_attribution(
        document_gate_decision_ref=_gate_ref("document"),
        document_artifact_ref=document.document_artifact_ref,
        assembly_input_manifest_ref=document.manifest_ref,
        attribution=attribution,
        target_candidate=env["section_a"]["candidate"],
        correction_policy_ref=policy_ref,
        target_budget_revision=4,
        target_repair_attempts_remaining=1,
    )
    assert auth is not None
    assert auth.target_generation_class is WriterGenerationClass.NORMAL_WRITE
    assert auth.document_defect_attribution_id == attribution.attribution_id

    fallback_attribution = DocumentDefectAttributionResolver().attribute(
        document_gate_decision_ref=_gate_ref("document-fallback"),
        blocking_condition_ref="document-blocker:fallback",
        ownership=DocumentDefectOwnership.SECTION_CONTENT,
        affected_section_artifact_refs=(env["section_b"]["artifact_ref"],),
    )
    assert builder.build_document_attribution(
        document_gate_decision_ref=_gate_ref("document-fallback"),
        document_artifact_ref=document.document_artifact_ref,
        assembly_input_manifest_ref=document.manifest_ref,
        attribution=fallback_attribution,
        target_candidate=env["section_b"]["candidate"],
        correction_policy_ref=policy_ref,
        target_budget_revision=4,
        target_repair_attempts_remaining=1,
    ) is None


def test_document_dispatch_projection_embeds_attribution_and_rejects_dangling_mismatch(
    tmp_path,
) -> None:
    env = _env(tmp_path)
    _entries, document = _partial_document(env)
    gate_ref = _gate_ref("document-dispatch")
    attribution = DocumentDefectAttributionResolver().attribute(
        document_gate_decision_ref=gate_ref,
        blocking_condition_ref="document-blocker:dispatch",
        ownership=DocumentDefectOwnership.SECTION_CONTENT,
        affected_section_artifact_refs=(env["section_a"]["artifact_ref"],),
    )
    policy_ref = CorrectionPolicyRefSchema(
        policy_id="correction-policy",
        policy_schema_version="correction_policy_v1",
        policy_version="v1",
        policy_hash=SHA_C,
    )
    authorization = ArtifactCorrectionAuthorizationBuilder().build_document_attribution(
        document_gate_decision_ref=gate_ref,
        document_artifact_ref=document.document_artifact_ref,
        assembly_input_manifest_ref=document.manifest_ref,
        attribution=attribution,
        target_candidate=env["section_a"]["candidate"],
        correction_policy_ref=policy_ref,
        target_budget_revision=7,
        target_repair_attempts_remaining=1,
    )
    assert authorization is not None
    dispatch = DocumentCorrectionDispatchBuilder.build(
        document_gate_decision_ref=gate_ref,
        document_artifact_ref=document.document_artifact_ref,
        assembly_input_manifest_ref=document.manifest_ref,
        correction_mode=DocumentCorrectionDispatchMode.SECTION_DISPATCH,
        defect_attributions=(attribution,),
        correction_authorizations=(authorization,),
        dispatch_reason_codes=("DOCUMENT_SECTION_DISPATCH",),
    )
    assert dispatch.correction_authorizations[0].authorization_id == authorization.authorization_id

    bad_auth = authorization.model_copy(
        update={"document_defect_attribution_hash": SHA_D}
    )
    with pytest.raises(ValueError, match="hash mismatch|attribution mismatch"):
        DocumentCorrectionDispatchBuilder.build(
            document_gate_decision_ref=gate_ref,
            document_artifact_ref=document.document_artifact_ref,
            assembly_input_manifest_ref=document.manifest_ref,
            correction_mode=DocumentCorrectionDispatchMode.SECTION_DISPATCH,
            defect_attributions=(attribution,),
            correction_authorizations=(bad_auth,),
        )


def test_assembly_mechanical_dispatch_contains_no_semantic_section_authorization(tmp_path) -> None:
    env = _env(tmp_path)
    _entries, document = _partial_document(env)
    gate_ref = _gate_ref("mechanical")
    policy_ref = CorrectionPolicyRefSchema(
        policy_id="correction-policy",
        policy_schema_version="correction_policy_v1",
        policy_version="v1",
        policy_hash=SHA_C,
    )
    identity = {
        "repair_kind": "REORDER_SECTIONS",
        "correction_policy_ref": policy_ref.model_dump(mode="json"),
        "correction_policy_rule_ref": "rule:reorder",
        "exact_input_refs": [document.manifest_ref.manifest_id],
        "expected_reassembly_scope": "document:doc-wave11",
    }
    repair_id = "assembly-mechanical-repair://sha256/" + canonical_sha256(identity)
    kwargs = {
        "repair_id": repair_id,
        "repair_kind": "REORDER_SECTIONS",
        "correction_policy_ref": policy_ref,
        "correction_policy_rule_ref": "rule:reorder",
        "exact_input_refs": (document.manifest_ref.manifest_id,),
        "expected_reassembly_scope": "document:doc-wave11",
    }
    repair = _hash_model(
        AssemblyMechanicalRepairProjectionSchema,
        "repair_projection_hash",
        **kwargs,
    )
    dispatch = DocumentCorrectionDispatchBuilder.build(
        document_gate_decision_ref=gate_ref,
        document_artifact_ref=document.document_artifact_ref,
        assembly_input_manifest_ref=document.manifest_ref,
        correction_mode=DocumentCorrectionDispatchMode.ASSEMBLY_MECHANICAL,
        assembly_mechanical_repairs=(repair,),
    )
    assert dispatch.correction_authorizations == ()
    assert len(dispatch.assembly_mechanical_repairs) == 1


def test_document_binding_store_same_identity_conflict_fails_closed(tmp_path) -> None:
    env = _env(tmp_path)
    entries = (
        _selection_entry(env, env["section_a"]),
        env["manifest_builder"].missing_entry("B"),
    )
    ref = env["manifest_builder"].build_and_persist(
        document_logical_id="document-logical:collision",
        required_section_keys=("A", "B"),
        ordered_section_entries=entries,
    )
    manifest = env["binding_store"].resolve_assembly_manifest(ref)
    corrupt = manifest.model_copy(
        update={
            "assembly_control_provenance_sha256": SHA_D,
        }
    )
    with env["database"].write_transaction(immediate=True) as connection:
        connection.execute(
            "UPDATE document_binding_values SET payload_json=? WHERE binding_id=?",
            (corrupt.model_dump_json(), ref.manifest_id),
        )
    with pytest.raises(PostWritePersistenceIntegrityError):
        env["binding_store"].resolve_assembly_manifest(ref)



def test_real_document_gate_to_control_mechanical_dispatch_yields_correct(tmp_path) -> None:
    fx = _document_gate_control_fixture(tmp_path)
    ref = fx["service"].decide(
        delivery_gate_decision_ref=fx["gate"].as_ref(),
        artifact_state_ref=fx["document"].document_artifact_ref,
        correction_policy_ref=fx["policy_ref"],
        fresh_currentness_proof=fx["currentness"],
        artifact_usability_integrity_proof=fx["usability"],
        correction_lineage_projection=fx["lineage"],
        generation_class=None,
        assembly_input_manifest_ref=fx["document"].manifest_ref,
        document_correction_dispatch_projection=fx["dispatch"],
        created_at_epoch=5000.0,
    )
    decision = fx["control_store"].resolve_exact(ref)

    assert decision.enforcement_action is EnforcementAction.CORRECT
    assert decision.input_manifest.artifact_scope is ArtifactScope.DOCUMENT
    assert not hasattr(decision.input_manifest, "generation_class")
    assert decision.correction_projection.authorization is None
    assert decision.correction_projection.state.value == "ELIGIBLE_AND_REQUESTED"
    assert decision.document_correction_dispatch_projection == fx["dispatch"]
    assert (
        decision.document_correction_dispatch_projection.correction_mode
        is DocumentCorrectionDispatchMode.ASSEMBLY_MECHANICAL
    )
    assert decision.availability_projection.state.value == "WITHHELD"


def test_document_gate_fail_without_exact_dispatch_remains_best_effort(tmp_path) -> None:
    fx = _document_gate_control_fixture(tmp_path)
    ref = fx["service"].decide(
        delivery_gate_decision_ref=fx["gate"].as_ref(),
        artifact_state_ref=fx["document"].document_artifact_ref,
        correction_policy_ref=fx["policy_ref"],
        fresh_currentness_proof=fx["currentness"],
        artifact_usability_integrity_proof=fx["usability"],
        correction_lineage_projection=fx["lineage"],
        generation_class=None,
        assembly_input_manifest_ref=fx["document"].manifest_ref,
        document_correction_dispatch_projection=None,
        created_at_epoch=5001.0,
    )
    decision = fx["control_store"].resolve_exact(ref)

    assert decision.enforcement_action is EnforcementAction.OUTPUT_BEST_EFFORT
    assert decision.document_correction_dispatch_projection is None
    assert decision.correction_projection.state.value == "NOT_ELIGIBLE"
    assert "DOCUMENT_ATTRIBUTION_REQUIRED" in decision.correction_projection.reason_codes
    assert decision.availability_projection.state.value == "BEST_EFFORT"
