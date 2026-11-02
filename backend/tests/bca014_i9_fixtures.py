"""Real I8 terminal authority followed by the unchanged V1 publisher."""
from contextlib import ExitStack
from unittest.mock import patch

from apps.enterprise_document.schemas.project_input_schema import ProjectInputSchema
from apps.enterprise_document.services.agent_rag_v1.post_write_composition import TargetPostWriteComposition
from backend.tests.bca014_i7_fixtures import make_section


def make_target(tmp_path, **kwargs):
    fx = make_section(tmp_path, **kwargs)
    post = fx['post_write']
    source = fx['arguments']['source_sections'][0]
    invocation = post.writer_execution_store.resolve_writer_generation_invocation(source.writer_generation_invocation_ref)
    subject = invocation.writer_subject_ref
    request = dict(task_id=post.lifecycle_service.load_run_identity(subject.run_id).task_id,
        run_id=subject.run_id, document_id=subject.document_id,
        project_input=ProjectInputSchema.model_validate(invocation.model_dump(mode='json')['unified_writer_input_snapshot']['project_input']),
        section_execution_results=(source,))
    return post, request


def terminal_case(tmp_path, *, orphan=False, **kwargs):
    post, request = make_target(tmp_path, **kwargs)
    complete = post._commit_and_terminalize_run
    def capture(**kw):
        if orphan:
            post._i9_orphan_control = add_orphan_control(post, kw['final_document'])
        return complete(**kw)
    with patch.object(post, '_publish_target_release', return_value=None), \
            patch.object(post, '_commit_and_terminalize_run', side_effect=capture):
        result = post.complete_target_post_write(**request)
    release_request = post.release_service.build_request(
        release_contract_version=post.release_service.release_contract_version,
        task_id=request['task_id'], run_id=request['run_id'],
        document_logical_id=f"document-logical:{request['run_id']}:{request['document_id']}",
        document_artifact_ref=result.final_document.artifact_state_ref,
        post_write_control_decision_ref=result.final_document.control_decision_ref,
        output_format=result.final_document.output_binding.output_format,
        validated_output_binding_sha256=result.final_document.output_binding.binding_sha256,
        publication_target_id=f"target://{request['task_id']}/{request['document_id']}/primary")
    return post, request, result, release_request


def add_orphan_control(post, final):
    control = post.control_store.resolve_exact(final.control_decision_ref)
    policy = post.correction_registry.register(policy_id='i9-orphan-policy',policy_version='v1',rules=())
    return post.control_service.decide(delivery_gate_decision_ref=final.delivery_gate_decision_ref,
        artifact_state_ref=final.artifact_state_ref,correction_policy_ref=policy,
        fresh_currentness_proof=final.currentness_proof,
        artifact_usability_integrity_proof=control.artifact_usability_integrity_proof,
        correction_lineage_projection=control.correction_lineage_projection,
        generation_class=None,assembly_input_manifest_ref=final.active_projection.assembly_input_manifest_ref,
        created_at_epoch=0.0)


def publish(case, **kwargs):
    post, _, result, request = case
    return post.release_service.publish(**dict(
        request=request, run_completion_assessment_ref=result.run_completion_assessment_ref,
        run_lifecycle_revision=result.terminal_run.lifecycle_revision,
        active_document_binding_supplier=lambda: result.final_document.active_projection,
        **kwargs))


def changed_request(post, request, **changes):
    payload = {key:getattr(request, key) for key in type(request).model_fields
        if key not in {'schema_version','request_sha256'}}
    payload.update(changes)
    return post.release_service.build_request(**payload)


def manifests(post):
    return tuple(ref for ref in post.release_store.list_references()
        if ref.artifact_type == 'post_write_release_manifest')


def recreate(post):
    # Simulate process restart: replace the old lifecycle owner with a fresh
    # canonical owner reading the same ledger. The old composition is retired.
    from apps.enterprise_document.services.agent_rag_v1.lifecycle_runtime import RunAttemptLifecycleService
    lifecycle = RunAttemptLifecycleService(post.lifecycle_service.path)
    new = TargetPostWriteComposition.create(runs_dir=post.runs_dir,
        research_runtime=post.research_runtime, writer_execution_store=post.writer_execution_store,
        budget_manager_provider=post.budget_manager_provider,
        lifecycle_service=lifecycle, require_run_commit_guard=True)
    for name in ('claim_quality_projection', 'faithfulness_fth03', 'citation_grounding',
            'requirement_adherence', 'coverage_composition', 'consistency_composition', 'memory_composition'):
        if hasattr(post, name):
            setattr(new, name, getattr(post, name))
    return new


def forbid_cognition(post):
    from model_gateway.fake_llm_client import FakeLLMClient
    stack = ExitStack()
    stack.enter_context(patch.object(FakeLLMClient, 'generate', side_effect=AssertionError('I9 cognition')))
    validate = post._complete_validation
    def replay_only(**kw):
        assert kw.get('committed_gate_ref') is not None, 'I9 fresh validation'
        return validate(**kw)
    stack.enter_context(patch.object(post, '_complete_validation', side_effect=replay_only))
    for owner, name in ((post, 'materialize_sections_and_document'),
            (post.database, 'normal_write_transaction')):
        stack.enter_context(patch.object(owner, name, side_effect=AssertionError('I9 normal mutation')))
    return stack
