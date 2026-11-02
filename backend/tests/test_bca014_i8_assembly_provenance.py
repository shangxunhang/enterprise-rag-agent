"""V2 assembly clean cut and independent evaluated completion provenance."""
import pytest
from pydantic import ValidationError
from core.hashing import canonical_sha256
from apps.enterprise_document.schemas.post_write import AssemblySelectionState
from apps.enterprise_document.schemas.post_write.document import AssemblySectionEntrySchema, AssemblyInputManifestSchema
from apps.enterprise_document.schemas.post_write.currentness import ActiveAssemblySectionSelectionSchema
from apps.enterprise_document.schemas.post_write.control import CorrectionLineageProjectionSchema
from apps.enterprise_document.services.post_write import (
    AssemblyInputManifestBuilder, DocumentBindingStore, PostWriteEntryStore,
    SectionAssemblyEligibilityResolver, active_selection_from_manifest_entry,
)
from backend.tests.test_post_write_correction_control import _fixture, _decide


def _entries(tmp_path, quality):
    fx = _fixture(tmp_path, quality=quality)
    first = _decide(fx)
    payload = fx['lineage'].model_dump(mode='json')
    payload.pop('projection_hash')
    payload['repair_budget_revision'] += 1
    second = _decide(fx, correction_lineage_projection=CorrectionLineageProjectionSchema(
        **payload, projection_hash=canonical_sha256(payload)))
    store = DocumentBindingStore(fx['database'])
    builder = AssemblyInputManifestBuilder(store=store)
    resolver = SectionAssemblyEligibilityResolver(control_store=fx['control_store'],
        entry_store=PostWriteEntryStore(fx['database']))
    entries = [builder.selection_entry(section_key='section-control-1',
        eligibility=resolver.resolve(section_key='section-control-1', control_decision_ref=d.as_ref()))
        for d in (first, second)]
    return fx, builder, store, (first, second), entries


@pytest.mark.parametrize('quality', ['PASS', 'FAIL'])
def test_selected_control_survives_reopen_and_changes_provenance_only(tmp_path, quality):
    fx, builder, store, controls, entries = _entries(tmp_path, quality)
    refs = [builder.build_and_persist(document_logical_id='document-logical:r:d',
        required_section_keys=('section-control-1',), ordered_section_entries=(e,)) for e in entries]
    manifests = [DocumentBindingStore(fx['database']).resolve_assembly_manifest(ref) for ref in refs]
    assert refs[0] != refs[1]
    assert manifests[0].assembly_membership_sha256 == manifests[1].assembly_membership_sha256
    assert manifests[0].assembly_control_provenance_sha256 != manifests[1].assembly_control_provenance_sha256
    assert entries[0].entry_binding_hash != entries[1].entry_binding_hash
    selections = [active_selection_from_manifest_entry(e) for e in entries]
    assert selections[0].selection_binding_hash != selections[1].selection_binding_hash
    for entry, control, manifest, selection in zip(entries, controls, manifests, selections):
        assert manifest.schema_version == 'assembly_input_manifest_v2'
        assert manifest.ordered_section_entries[0].selected_section_control_decision_ref == control.as_ref()
        assert selection.schema_version == 'active_assembly_section_selection_v3'
        assert selection.selected_section_control_decision_ref == control.as_ref()
        if quality == 'PASS':
            assert entry.section_assembly_candidate.section_control_decision_ref == control.as_ref()
            assert selection.control_decision_ref == control.as_ref()
        else:
            assert entry.selection_state is AssemblySelectionState.WITHHELD
            assert entry.section_assembly_candidate is None
            assert selection.control_decision_ref is None


def test_missing_has_no_evaluation_but_covers_required_key(tmp_path):
    _, builder, store, _, _ = _entries(tmp_path, 'PASS')
    entry = builder.missing_entry('missing')
    ref = builder.build_and_persist(document_logical_id='document-logical:r:d',
        required_section_keys=('missing',), ordered_section_entries=(entry,))
    manifest = store.resolve_assembly_manifest(ref)
    assert manifest.required_section_keys == ('missing',)
    assert entry.section_assembly_candidate is entry.selected_section_control_decision_ref is None
    selection = active_selection_from_manifest_entry(entry)
    assert selection.selected_section_control_decision_ref is selection.control_decision_ref is None
    from types import SimpleNamespace
    from apps.enterprise_document.services.agent_rag_v1.run_finalization_currentness import (
        assert_assessment_sections, evaluated_section_envelopes)
    assert evaluated_section_envelopes(manifest) == ()
    assert_assessment_sections(SimpleNamespace(section_completion_refs=()), manifest)
    assert_assessment_sections(SimpleNamespace(section_completion_refs=()), store.resolve_assembly_manifest(ref))


def test_included_and_withheld_assessment_envelopes_exact_missing_absent(tmp_path):
    from types import SimpleNamespace
    from apps.enterprise_document.services.agent_rag_v1.run_finalization_currentness import (
        evaluated_section_envelopes, assert_assessment_sections)
    from apps.enterprise_document.services.post_write.persistence import PostWritePersistenceIntegrityError
    _, builder, store, _, entries = _entries(tmp_path, 'PASS')
    evaluated = entries[0]
    ref = builder.build_and_persist(document_logical_id='document-logical:r:d',
        required_section_keys=('section-control-1','missing'),
        ordered_section_entries=(evaluated,builder.missing_entry('missing')))
    manifest=store.resolve_assembly_manifest(ref)
    envelopes=evaluated_section_envelopes(manifest)
    assert len(envelopes)==1 and envelopes[0].source_scope_ref=='section:section-control-1'
    assert envelopes[0].exact_ref_payload==evaluated.selected_section_control_decision_ref.model_dump(mode='json')
    assert_assessment_sections(SimpleNamespace(section_completion_refs=envelopes),manifest)
    for invalid in ((),envelopes+envelopes,(envelopes[0].model_copy(update={'source_scope_ref':'section:missing'}),)):
        with pytest.raises(PostWritePersistenceIntegrityError):
            assert_assessment_sections(SimpleNamespace(section_completion_refs=invalid),manifest)


@pytest.mark.parametrize('quality', ['PASS', 'FAIL'])
def test_evaluated_entry_requires_selected_control(tmp_path, quality):
    _, _, _, _, entries = _entries(tmp_path, quality)
    raw = entries[0].model_dump(mode='json')
    raw['selected_section_control_decision_ref'] = None
    with pytest.raises(ValidationError, match='requires selected Control'):
        AssemblySectionEntrySchema.model_validate(raw)


def test_old_versions_and_mixed_contract_fail_closed(tmp_path):
    _, builder, store, _, entries = _entries(tmp_path, 'PASS')
    entry = entries[0]
    raw = entry.model_dump(mode='json')
    raw['schema_version'] = 'assembly_section_entry_v1'
    with pytest.raises(ValidationError):
        AssemblySectionEntrySchema.model_validate(raw)
    raw = active_selection_from_manifest_entry(entry).model_dump(mode='json')
    raw['schema_version'] = 'active_assembly_section_selection_v2'
    with pytest.raises(ValidationError):
        ActiveAssemblySectionSelectionSchema.model_validate(raw)
    with pytest.raises(ValueError, match='document_assembly_v2'):
        AssemblyInputManifestBuilder(store=store, assembly_contract_version='document_assembly_v1')


def test_withheld_control_changes_real_currentness_without_evidence_membership_change(tmp_path):
    from backend.tests.test_post_write_section_document_lifecycle import _env,_selection_entry,_contract_ref
    from apps.enterprise_document.services.post_write import (
        SectionAssemblyEligibility,CurrentnessResolver,PostWriteEntryIdentityBuilder)
    from apps.enterprise_document.schemas.post_write import ActiveComparisonMode
    _,_,_,controls,_=_entries(tmp_path/'controls','FAIL')
    env=_env(tmp_path/'document');builder=env['manifest_builder']
    included=_selection_entry(env,env['section_a'])
    withheld=tuple(builder.selection_entry(section_key='section-control-1',eligibility=SectionAssemblyEligibility(
        selection_state=AssemblySelectionState.WITHHELD,candidate=None,reason_code='SECTION_ACTION_CORRECT',
        selected_section_control_decision_ref=control.as_ref())) for control in controls)
    historical=env['coordinator'].materialize(run_id='run-wave11',document_id='doc-wave11',
        document_logical_id='document-logical:withheld-alias',required_section_keys=('A','section-control-1'),
        ordered_section_entries=(included,withheld[0]),requirement_ref=env['requirement_ref'],
        quality_delivery_contract_ref=_contract_ref())
    other=builder.build_and_persist(document_logical_id='document-logical:withheld-alias',
        required_section_keys=('A','section-control-1'),ordered_section_entries=(included,withheld[1]))
    assert historical.manifest_ref!=other
    # The new completion provenance adds no WITHHELD Evidence to assembly.
    assert env['projector'].project_and_persist(other)==historical.document_evidence_ref
    evidence=env['binding_store'].resolve_document_evidence_snapshot(historical.document_evidence_ref)
    assert tuple(s.logical_section_id for s in evidence.source_bindings)==('A',)
    changed=historical.active_projection.model_copy(update={'assembly_owner_section_selections':(
        active_selection_from_manifest_entry(included),active_selection_from_manifest_entry(withheld[1]))})
    proof=CurrentnessResolver(entry_store=env['entry_store'],identity_builder=PostWriteEntryIdentityBuilder(),
        document_binding_store=env['binding_store']).prove(historical_entry_ref=historical.document_entry_ref,
            active_projection=changed,comparison_mode=ActiveComparisonMode.EXPECTED_BINDING_IDENTITY)
    assert proof.currentness_state.value=='STALE' and proof.match_state.value=='SELECTION_MISMATCH'
