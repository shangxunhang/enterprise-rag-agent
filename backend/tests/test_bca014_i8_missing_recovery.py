"""Genuine MISSING has no evaluated Control, with full required coverage."""
from unittest.mock import patch
from apps.enterprise_document.schemas.project_input_schema import ProjectInputSchema
from apps.enterprise_document.schemas.post_write import AssemblySelectionState
from apps.enterprise_document.schemas.agent_rag_v1.lifecycle import RunLifecycleStatus as RS
from backend.tests.bca014_i8_fixtures import make_sections
from backend.tests.test_bca014_i8_finalization_recovery import no_normal_writes


def test_missing_required_section_resume_exact_manifest_without_completion(tmp_path,monkeypatch):
    fx=make_sections(tmp_path);post=fx['post_write'];source=fx['sources'][0]
    invocation=post.writer_execution_store.resolve_writer_generation_invocation(source.writer_generation_invocation_ref)
    subject=invocation.writer_subject_ref
    request=dict(task_id=post.lifecycle_service.load_run_identity(subject.run_id).task_id,run_id=subject.run_id,
        document_id=subject.document_id,project_input=ProjectInputSchema.model_validate(
            invocation.model_dump(mode='json')['unified_writer_input_snapshot']['project_input']),
        section_execution_results=(source,))
    monkeypatch.setattr(post,'_publish_target_release',lambda **k:None)
    # The stock Target fixture enumerates supplied results as required keys.
    # Exercise the real Assembly owner with a Plan-required unevaluated key.
    materialize=post.document_lifecycle.materialize
    missing_key=fx['sources'][1].section.section_id
    def materialize_required(**kwargs):
        return materialize(**{**kwargs,'required_section_keys':(source.section.section_id,missing_key),
            'ordered_section_entries':(*kwargs['ordered_section_entries'],post.manifest_builder.missing_entry(missing_key))})
    monkeypatch.setattr(post.document_lifecycle,'materialize',materialize_required)
    import pytest
    with patch.object(post,'_finish_finalization',side_effect=OSError('crash-C-missing')):
        with pytest.raises(Exception):post.complete_target_post_write(**request)
    assert post.lifecycle_service.load_run_lifecycle(subject.run_id).status is RS.FINALIZING
    with no_normal_writes(post):result=post.complete_target_post_write(**request)
    manifest=post.binding_store.resolve_assembly_manifest(result.final_document.active_projection.assembly_input_manifest_ref)
    assert len(manifest.required_section_keys)==2
    missing=tuple(e for e in manifest.ordered_section_entries if e.selection_state is AssemblySelectionState.MISSING)
    assert len(missing)==1 and missing[0].selected_section_control_decision_ref is None
    evidence=post.binding_store.resolve_document_evidence_snapshot(result.final_document.active_projection.evidence_binding.document_projection_ref)
    assert tuple(s.logical_section_id for s in evidence.source_bindings)==(source.section.section_id,)
    assessment=post.assessment_store.resolve_exact(result.run_completion_assessment_ref)
    assert tuple(e.source_scope_ref for e in assessment.section_completion_refs)==('section:'+source.section.section_id,)
    assert result.terminal_run.status is RS.SUCCESS
    # SUCCESS replay also recovers MISSING from Assembly, with no fabricated completion.
    assert post.complete_target_post_write(**request).run_completion_assessment_ref==result.run_completion_assessment_ref
