"""Stateless I8 orchestration: drain one owner at a time, compare exact values.

No store, head, durable PASS receipt, or lifecycle ownership lives here.
PostWrite values are read through the I4 connection; external owner resolvers
run only after that connection has released its reservation.
"""
from dataclasses import dataclass

from apps.enterprise_document.schemas.post_write import (
    ActiveComparisonMode, ActiveDocumentBindingProjectionSchema,
    ArtifactScope, AssemblySelectionState, ValidationScope,
)
from apps.enterprise_document.schemas.post_write.foundation import EnforcementAction
from apps.enterprise_document.services.post_write.gate import GateInputIntegrityGuard
from apps.enterprise_document.services.post_write.document_lifecycle import active_selection_from_manifest_entry
from apps.enterprise_document.services.post_write.persistence import PostWritePersistenceIntegrityError
from core.hashing import canonical_sha256
from .post_write_pipeline_identity import pipeline_identity_material, semantic_pipeline_sha256, validation_run_identity
from .run_completion_runtime import exact_ref_envelope, RunCompletionAssessmentStaleError


class FinalizationStaleError(RunCompletionAssessmentStaleError):
    """A proven mismatch, eligible for explicit abort back to RUNNING."""


def require_current(condition, message):
    if not condition:
        raise FinalizationStaleError(message)


def require_exact(condition, message):
    if not condition:
        raise PostWritePersistenceIntegrityError(message)


def final_control_eligible(control):
    if control.enforcement_action is EnforcementAction.CORRECT:
        return False
    if control.enforcement_action not in {EnforcementAction.OUTPUT, EnforcementAction.OUTPUT_BEST_EFFORT, EnforcementAction.BLOCK}:
        return False
    if control.correction_projection.state.value == 'ELIGIBLE_AND_REQUESTED':
        return False
    dispatch = control.document_correction_dispatch_projection
    return dispatch is None or dispatch.correction_mode.value == 'NOT_ELIGIBLE'


def evaluated_section_envelopes(manifest):
    return tuple(exact_ref_envelope(source_authority='POST_WRITE_CONTROL',
        source_kind='SECTION_CONTROL_DECISION', source_scope_ref='section:'+entry.section_key,
        exact_ref=entry.selected_section_control_decision_ref)
        for entry in manifest.ordered_section_entries if entry.selection_state is not AssemblySelectionState.MISSING)


def assert_assessment_sections(record, manifest):
    expected = {item.source_scope_ref: item for item in evaluated_section_envelopes(manifest)}
    actual = {item.source_scope_ref: item for item in record.section_completion_refs}
    require_exact(len(actual) == len(record.section_completion_refs) and actual == expected,
        'Assessment Section completion set differs from exact evaluated manifest selections')


@dataclass(frozen=True)
class FinalizationChain:
    control: object
    entry: object
    artifact: object
    gate: object
    completion: object
    validation_run: object
    currentness: object
    claim_snapshot: object = None


@dataclass(frozen=True)
class FinalizationValues:
    manifest: object
    document: FinalizationChain
    sections: tuple
    base_entry_ref: object
    parent: object


def _read_chain(post, control_ref, connection, *, projection=None):
    control = post.control_store.resolve_exact(control_ref, connection=connection)
    entry = post.entry_store.resolve_exact(control.input_manifest.post_write_entry_ref, connection=connection)
    identity = entry.identity
    require_exact(identity.artifact_state_ref == control.input_manifest.artifact_state_ref,
        'Control/Entry Artifact exact binding differs')
    artifact = post.artifact_store.resolve_exact(identity.artifact_state_ref, connection=connection)
    require_current(post.artifact_store.resolve_current(artifact.artifact_ref.artifact_id,
        connection=connection).artifact_ref == artifact.artifact_ref, 'PostWrite Artifact head moved')
    post.requirement_store.resolve_exact(identity.requirement_ref, connection=connection)
    post.contract_store.resolve_exact(identity.quality_delivery_contract_ref, connection=connection)
    policy = post.correction_policy_store.resolve_exact(control.input_manifest.correction_policy_ref, connection=connection)
    require_current(control.control_contract_version == post.control_service.control_contract_version,
        'Control execution contract changed')
    require_exact(not policy.automatic_correction_enabled and not policy.rules,
        'Target correction policy differs from frozen no-inference policy')
    if projection is None:
        projection = control.fresh_currentness_proof.active_projection
    currentness = post.currentness_resolver.prove(
        historical_entry_ref=control.input_manifest.post_write_entry_ref, active_projection=projection,
        comparison_mode=control.fresh_currentness_proof.comparison_mode,
        active_materialized_post_write_entry_ref=control.fresh_currentness_proof.active_materialized_post_write_entry_ref,
        connection=connection)
    require_current(currentness == control.fresh_currentness_proof and currentness.currentness_state.value == 'CURRENT',
        'Control Entry/assembly currentness changed')
    require_current(post.usability_resolver.resolve(artifact_state_ref=artifact.artifact_ref,
        currentness_proof=currentness, connection=connection) == control.artifact_usability_integrity_proof,
        'Artifact usability projection changed')
    gate = post.gate_store.resolve_exact(control.delivery_gate_decision_ref, connection=connection)
    completion_ref = gate.gate_input_manifest.validation_completion_ref
    resolved = GateInputIntegrityGuard(validation_store=post.validation_store,
        entry_store=post.entry_store, contract_store=post.contract_store).resolve(
        validation_run_id=gate.validation_run_id, validation_completion_ref=completion_ref,
        quality_delivery_contract_ref=identity.quality_delivery_contract_ref,
        currentness_proof=currentness, connection=connection)
    require_exact(resolved.manifest == gate.gate_input_manifest and gate.validation_run_id == control.validation_run_id,
        'Control/Gate/Completion exact relation differs')
    completion = post.validation_store.resolve_completion(completion_ref, connection=connection)
    run = post.validation_store.resolve_run_exact(control.validation_run_id, connection=connection)
    # Completion exact resolution drains/validates recursive execution accounting.
    # Explicitly read every Fact, execution and intermediate on the fenced connection.
    for item in completion.items:
        for ref in item.fact_refs:
            post.validation_store.resolve_fact(ref, connection=connection)
    snapshot = None
    if post.claim_quality_projection is not None:
        from .post_write_claim_projection import PostWriteClaimSourceSnapshot
        execution = post.validation_store.find_validator_execution_for_item(
            validation_run_id=run.validation_run_id,
            expected_item_ref='faithfulness:shared:'+artifact.artifact_ref.artifact_hash, connection=connection)
        require_exact(execution is not None and len(execution.intermediate_refs) == 1,
            'required committed Claim source execution missing')
        snapshot = PostWriteClaimSourceSnapshot.model_validate(
            post.validation_store.resolve_intermediate(execution.intermediate_refs[0], connection=connection))
    return FinalizationChain(control, entry, artifact, gate, completion, run, currentness, snapshot)


def read_postwrite_finalization(post, barrier):
    """I4 owner drain. No external mutable resolver is called inside this fence."""
    from apps.enterprise_document.schemas.post_write.foundation import PostWriteControlDecisionRefSchema
    control_ref = PostWriteControlDecisionRefSchema.model_validate(barrier.final_control_decision_ref.exact_ref_payload)
    with post.database.finalization_read_transaction() as connection:
        control = post.control_store.resolve_exact(control_ref, connection=connection)
        require_exact(control.input_manifest.artifact_scope is ArtifactScope.DOCUMENT, 'barrier Control is not DOCUMENT')
        entry = post.entry_store.resolve_exact(control.input_manifest.post_write_entry_ref, connection=connection)
        require_exact(entry.identity.run_id == barrier.run_id, 'barrier Control belongs to another Run')
        require_exact(final_control_eligible(control), 'barrier Control has executable correction')
        manifest = post.binding_store.resolve_assembly_manifest(entry.identity.assembly_input_manifest_ref, connection=connection)
        require_exact(control.input_manifest.assembly_input_manifest_ref == entry.identity.assembly_input_manifest_ref,
            'Control/Entry assembly exact refs differ')
        active = ActiveDocumentBindingProjectionSchema(
            run_id=entry.identity.run_id, document_id=entry.identity.document_id,
            artifact_state_ref=entry.identity.artifact_state_ref,
            assembly_input_manifest_ref=entry.identity.assembly_input_manifest_ref,
            evidence_binding=entry.identity.evidence_binding, requirement_ref=entry.identity.requirement_ref,
            quality_delivery_contract_ref=entry.identity.quality_delivery_contract_ref,
            assembly_owner_section_selections=tuple(active_selection_from_manifest_entry(e) for e in manifest.ordered_section_entries))
        document = _read_chain(post, control_ref, connection, projection=active)
        post.binding_store.resolve_document_evidence_snapshot(entry.identity.evidence_binding.document_projection_ref, connection=connection)
        sections = []
        for e in manifest.ordered_section_entries:
            if e.selection_state is AssemblySelectionState.MISSING:
                continue
            chain = _read_chain(post, e.selected_section_control_decision_ref, connection)
            selected = post.manifest_builder.selection_entry(section_key=e.section_key,
                eligibility=post.section_eligibility.resolve(section_key=e.section_key,
                    control_decision_ref=e.selected_section_control_decision_ref, connection=connection))
            require_exact(selected == e, 'Assembly selection differs from exact selected Section Control')
            require_exact(chain.entry.identity.section_id == e.section_key
                and chain.entry.identity.run_id == barrier.run_id
                and chain.entry.identity.document_id == entry.identity.document_id
                and chain.entry.identity.requirement_ref == entry.identity.requirement_ref
                and chain.entry.identity.quality_delivery_contract_ref == entry.identity.quality_delivery_contract_ref,
                'selected Section Control scope differs')
            if e.section_assembly_candidate is not None:
                candidate = e.section_assembly_candidate
                require_exact(candidate.section_artifact_ref == chain.artifact.artifact_ref
                    and candidate.section_post_write_entry_ref == chain.control.input_manifest.post_write_entry_ref
                    and candidate.section_gate_decision_ref == chain.gate.as_ref(), 'selected candidate exact chain differs')
            sections.append((e.section_key, chain))
        parent_ref = document.artifact.parent_artifact_ref
        require_exact(parent_ref is not None, 'final Document has no assembly parent')
        parent = post.artifact_store.resolve_exact(parent_ref, connection=connection)
        require_exact(parent.origin == document.artifact.origin
            and document.artifact.artifact_ref.artifact_version == parent_ref.artifact_version+1,
            'final Document parent lineage differs')
        from .post_write_composition import ArtifactCanonicalPayloadMaterializer
        from apps.enterprise_document.schemas.post_write.entry import entry_ref_from_identity
        require_exact(ArtifactCanonicalPayloadMaterializer.business_payload(parent.canonical_payload_ref)
            == ArtifactCanonicalPayloadMaterializer.business_payload(document.artifact.canonical_payload_ref),
            'final Document business payload differs from assembly parent')
        base_identity = post.identity_builder.from_active_document_projection(
            active.model_copy(update={'artifact_state_ref': parent_ref}))
        base_entry_ref = entry_ref_from_identity(base_identity)
        require_exact(post.entry_store.resolve_exact(base_entry_ref, connection=connection).identity == base_identity,
            'assembly parent Entry identity differs')
    return FinalizationValues(manifest, document, tuple(sections), base_entry_ref, parent)


def validate_request(post, values, *, task_id, run_id, document_id, project_input, source_sections):
    """Supplied request is verification material, never finalization authority."""
    from application.task_factory import TaskFactory
    identity = post.lifecycle_service.load_run_identity(run_id)
    request = TaskFactory().build(task_id=task_id, run_id=run_id, user_input=project_input.user_query,
        created_at='finalization-request-verification', project_input=project_input.model_dump(mode='json'))
    memory = getattr(post, 'memory_composition', None)
    if memory is not None and memory.required:
        request = request.model_copy(update={'metadata': {**request.metadata, 'durable_memory_required': True}})
    require_exact(identity.task_id == task_id and request.canonical_request_sha256() == identity.canonical_request_sha256,
        'finalization request differs from immutable RunIdentity')
    require_exact(values.document.entry.identity.document_id == document_id
        and values.manifest.document_logical_id == f'document-logical:{run_id}:{document_id}',
        'finalization request Document differs')
    sources = {s.section.section_id: s for s in source_sections}
    require_exact(len(sources) == len(source_sections) and tuple(sources) == tuple(key for key, _ in values.sections),
        'finalization supplied source set/order differs from evaluated Sections')
    for key, chain in values.sections:
        source = sources[key]
        identity = chain.entry.identity
        require_exact(identity.writer_execution_ref == source.writer_execution_ref
            and identity.writer_invocation_ref == source.writer_generation_invocation_ref,
            'supplied Section Writer refs differ from selected Control')
        from .post_write_composition import ArtifactCanonicalPayloadMaterializer
        payload = ArtifactCanonicalPayloadMaterializer.business_payload(chain.artifact.canonical_payload_ref)
        require_exact(payload.get('content') == source.section.content
            and payload.get('section_title') == source.section.section_title,
            'supplied Section text differs from exact Artifact')
    return sources


def assert_finalization_current(post, values, *, task_id, run_id, project_input, sources):
    """Sequential owner reads after the PostWrite fence is released."""
    from apps.enterprise_document.schemas.agent_rag_v1.budgets import CanonicalBudgetStateSchema
    from apps.enterprise_document.services.post_write.control import CanonicalRepairBudgetProjectionAdapter
    from .post_write_claim_projection import PostWriteClaimProjectionError
    for key, chain in values.sections:
        source = sources[key]
        writer = post.writer_execution_store.resolve_writer_execution(source.writer_execution_ref)
        invocation = post.writer_execution_store.resolve_writer_generation_invocation(source.writer_generation_invocation_ref)
        subject = invocation.writer_subject_ref
        require_exact(subject.run_id == run_id and subject.document_id == chain.entry.identity.document_id
            and subject.section_id == key and invocation.route_decision_ref == chain.entry.identity.route_decision_ref
            and invocation.generation_class == chain.entry.identity.generation_class,
            'selected Writer subject/route/generation differs from Section Entry')
        require_exact(invocation.model_dump(mode='json')['unified_writer_input_snapshot']['project_input'] == project_input.model_dump(mode='json'),
            'supplied ProjectInput differs from selected Writer invocation')
        require_current(writer.artifact is not None and writer.artifact.content == source.section.content,
            'selected Writer result changed')
        package = post.research_runtime.validate_evidence_package_binding_current(source.active_evidence_package_binding)
        bundle = post.research_runtime.resolve_evidence_bundle(package.evidence_bundle_binding)
        from contracts.evidence import evidence_bundle_sha256
        require_exact(source.evidence.evidence_bundle is not None
            and evidence_bundle_sha256(source.evidence.evidence_bundle) == evidence_bundle_sha256(bundle),
            'supplied Evidence differs from selected package')
        if source.current_research_round_binding is not None:
            round_record = post.research_runtime.validate_round_binding_current(source.current_research_round_binding)
            require_exact(round_record.package_progress.resulting_evidence_package_version_ref
                == source.active_evidence_package_binding.version_ref
                and round_record.source_plan_version_ref == source.canonical_plan_binding.plan_version_ref
                and round_record.final_evidence_bundle_binding == package.evidence_bundle_binding,
                'Research round/Plan/package exact lineage differs')
        else:
            lineage = source.active_execution_lineage
            progress = post.research_runtime.resolve_progress(run_id=run_id, section_id=key,
                attempt_scope_ref=lineage.attempt_scope_ref, attempt_id=lineage.attempt_id)
            require_current(progress.last_committed_round_ref is None, 'Research lineage has an unbound committed round')
    parent = values.parent
    require_exact(post.citation_integrity.committed_assembly(artifact_id=parent.artifact_ref.artifact_id,
        artifact_version=parent.artifact_ref.artifact_version, manifest=values.manifest,
        expected_parent_ref=parent.parent_artifact_ref, record=parent,
        section_records={key: chain.artifact for key,chain in values.sections}) == parent.artifact_ref,
        'final Document parent differs from committed citation assembly')
    included = tuple(sources[e.section_key] for e in values.manifest.ordered_section_entries
        if e.selection_state is AssemblySelectionState.INCLUDED)
    chains = [(key, chain, ValidationScope.SECTION, (sources[key],)) for key, chain in values.sections]
    chains.append((values.document.entry.identity.document_id, values.document, ValidationScope.DOCUMENT, included))
    for target, chain, scope, selected_sources in chains:
        identity = chain.entry.identity
        if post.claim_quality_projection is not None:
            fresh = chain.claim_snapshot.model_copy(update={'sections': tuple(
                post.claim_quality_projection._section_sources(post, source) for source in selected_sources)})
            require_current(fresh == chain.claim_snapshot, 'committed Claim/Paragraph snapshot changed')
            generation = post.claim_quality_projection.build_verification_generation(snapshot=fresh,
                run_id=run_id, scope=scope, target_ref=target, strict_currentness=True)
        else:
            generation = post._verification_generation(run_id=run_id, artifact_ref=chain.artifact.artifact_ref,
                scope=scope, target_ref=target, source_sections=selected_sources,
                assembly_input_manifest_ref=identity.assembly_input_manifest_ref, strict_currentness=True)
        expected = validation_run_identity(subject_hash=chain.validation_run.subject_key.subject_hash,
            quality_contract_ref=identity.quality_delivery_contract_ref,
            semantic_pipeline_sha256=semantic_pipeline_sha256(pipeline_identity_material(post, scope)),
            verification_generation_sha256=generation.verification_generation_sha256)
        require_current(expected == chain.control.validation_run_id, 'Validation generation/pipeline changed')
        if post.claim_quality_projection is not None:
            # The generation is now known current. Reuse the existing producer
            # replay validator on an I4 connection, with cross-owner Result
            # checks already proved by the fresh Section snapshots above.
            with post.database.finalization_read_transaction() as connection:
                committed = post.claim_quality_projection.resolve_committed_source_snapshot(post_write=post,
                    validation_run_id=chain.control.validation_run_id,
                    subject_hash=chain.validation_run.subject_key.subject_hash,
                    artifact_ref=chain.artifact.artifact_ref, scope=scope,
                    connection=connection, validate_canonical_results=False)
            require_exact(committed == chain.claim_snapshot, 'fenced committed Claim snapshot changed')
    state = CanonicalBudgetStateSchema.model_validate(post.budget_manager_provider(task_id, run_id).canonical_state())
    require_exact(state.task_id == task_id and state.run_id == run_id, 'Budget belongs to another Run')
    require_current(state.active_reserved.repair_attempts == 0 and not any(
        item.reservation_ref.reservation_kind == 'PARAGRAPH_SEMANTIC_REPAIR' and item.status == 'active'
        for item in state.reservations), 'active repair reservation remains')
    lineage = CanonicalRepairBudgetProjectionAdapter.project(target_artifact_ref=values.document.artifact.artifact_ref,
        budget_state=state)
    require_current(lineage == values.document.control.correction_lineage_projection, 'canonical repair Budget projection changed')
    return values.document.currentness
