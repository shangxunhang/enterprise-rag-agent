"""Release consumes evaluated-only Assessment and full exact Assembly V2."""
from unittest.mock import patch
from apps.enterprise_document.schemas.project_input_schema import ProjectInputSchema
from apps.enterprise_document.schemas.post_write import AssemblySelectionState
from backend.tests.bca014_i8_fixtures import make_sections
from backend.tests.bca014_i9_fixtures import manifests, recreate, forbid_cognition


def test_missing_required_provenance_releases_and_replays_from_v2_manifest(tmp_path):
    fx=make_sections(tmp_path);post=fx['post_write'];source=fx['sources'][0]
    invocation=post.writer_execution_store.resolve_writer_generation_invocation(source.writer_generation_invocation_ref)
    subject=invocation.writer_subject_ref
    request=dict(task_id=post.lifecycle_service.load_run_identity(subject.run_id).task_id,run_id=subject.run_id,
        document_id=subject.document_id,project_input=ProjectInputSchema.model_validate(
            invocation.model_dump(mode='json')['unified_writer_input_snapshot']['project_input']),
        section_execution_results=(source,))
    materialize=post.document_lifecycle.materialize
    missing_key=fx['sources'][1].section.section_id
    def required(**kw):
        return materialize(**{**kw,'required_section_keys':(source.section.section_id,missing_key),
            'ordered_section_entries':(*kw['ordered_section_entries'],post.manifest_builder.missing_entry(missing_key))})
    with patch.object(post.document_lifecycle,'materialize',side_effect=required):
        result=post.complete_target_post_write(**request)
    assert result.published_release is not None
    manifest=result.published_release.manifest
    assembly=post.binding_store.resolve_assembly_manifest(manifest.assembly_input_manifest_ref)
    assert assembly.schema_version=='assembly_input_manifest_v2'
    assert assembly.assembly_contract_version=='document_assembly_v2'
    assert len(assembly.required_section_keys)==2
    missing=next(e for e in assembly.ordered_section_entries if e.selection_state is AssemblySelectionState.MISSING)
    assert missing.section_key==missing_key and missing.selected_section_control_decision_ref is None
    assessment=post.assessment_store.resolve_exact(result.run_completion_assessment_ref)
    assert len(assessment.section_completion_refs)==1
    new=recreate(post)
    with forbid_cognition(new):replay=new.complete_target_post_write(**request)
    assert replay.published_release.manifest==manifest
    assert replay.published_release.reuse_state.value=='EXACT_REQUEST_REPLAY'
    assert len(manifests(new))==1
