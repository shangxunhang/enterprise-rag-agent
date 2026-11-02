"""Stage F callers on the current native Target publication contract.

The exact old V1 fixture is archived. Gate, Control, Assessment, lifecycle and
publisher remain production owners. The document usability oracle isolates
BLOCK and does not certify business quality.
"""
from __future__ import annotations

import hashlib
import os
from pathlib import Path
from unittest.mock import patch

from apps.enterprise_document.schemas.post_write import ArtifactScope, EnforcementAction
from apps.enterprise_document.schemas.post_write.release import PostWriteReleaseInputProofSchema
from core.hashing import canonical_sha256
from bca014_i10_capacity_fixtures import explicit_fake_startup_policy
from test_phase7_document_closure_mainline import _InspectingFactory, _latest_top_payload, _run_fake
from test_multi_agent_v3_target_release_summary import target_run


def test_success_mainline_publishes_verified_release_manifest(target_run) -> None:
    _, summary, engine, source, publication, payload = target_run
    manifest = publication.manifest
    assert summary['status'] == 'partial_success'
    assert summary['target_release_publication'] == source['result']['target_release_publication']
    assert summary['release_publication'] is None
    assert publication.verified_after_write is True
    assert publication.reuse_state.value == 'NEW_COMMIT'
    assert manifest.immutable is True
    assert manifest.release_payload_artifact.content_hash == 'sha256:' + manifest.release_payload_sha256
    store = engine.stage_service.target_post_write_composition.release_store
    identity = dict(task_id=manifest.task_id, run_id=manifest.run_id)
    assert store.get_json(publication.manifest_artifact.artifact_ref, **identity) == manifest.model_dump(mode='json')
    assert len(list(store.root.rglob('*.json'))) == 3
    for ref in (publication.manifest_artifact, manifest.release_payload_artifact,
                manifest.release_input_proof_artifact):
        stored, content = store.get_bytes_with_reference(ref.artifact_ref, **identity)
        assert stored == ref
        assert stored.content_hash == 'sha256:' + hashlib.sha256(content).hexdigest()
    proof = PostWriteReleaseInputProofSchema.model_validate(
        store.get_json(manifest.release_input_proof_artifact.artifact_ref, **identity))
    assert proof.proof_sha256 == manifest.release_input_proof_sha256
    assert proof.document_artifact_ref == manifest.document_artifact_ref
    assert payload['artifact_route'] == 'deliverable'
    assert payload['target_release_publication'] == publication.model_dump(mode='json')
    assert payload['target_run_completion_assessment_ref'] == manifest.run_completion_assessment_ref.model_dump(mode='json')
    assert payload['terminal_agent_result']['metadata']['release_verified_after_write'] is True
    terminal = engine.lifecycle_service.load_run_lifecycle(manifest.run_id)
    assert terminal.status.value == 'success'
    assert terminal.run_completion_assessment_ref == manifest.run_completion_assessment_ref
    assert manifest.release_mode.value == 'BEST_EFFORT'
    assert manifest.quality_state_passthrough.value == 'UNRESOLVED'


def test_rejected_mainline_does_not_publish_release_artifacts(tmp_path: Path) -> None:
    run_id = 'stage_f_rejected_no_publication'
    profile, default_path, original = explicit_fake_startup_policy(tmp_path, run_id)
    build = _InspectingFactory.build

    def with_document_block(self, **kwargs):
        supervisor = build(self, **kwargs)
        post = supervisor.workflow_engine.stage_service.target_post_write_composition
        resolve = post.usability_resolver.resolve

        def withheld(**kw):
            proof = resolve(**kw)
            if kw['artifact_state_ref'].artifact_scope is ArtifactScope.DOCUMENT:
                value = proof.model_dump(mode='json')
                value.update(state='INTEGRITY_INVALID', artifact_record_hash=None,
                             reason_codes=['STAGE_F_CONTROLLED_WITHHELD_USABILITY'])
                value.pop('proof_sha256')
                return type(proof)(**value, proof_sha256=canonical_sha256(value))
            return proof

        post.usability_resolver.resolve = withheld

        def forbid_publication(**kwargs):
            raise AssertionError('BLOCK must never call the publisher')

        post.release_service.publish = forbid_publication
        return supervisor

    summary, factory = _run_fake(tmp_path, run_id=run_id, patchers=(
        patch.dict(os.environ, {'GROUNDED_GENERATION_POLICY_FILE': str(profile)}),
        patch.object(_InspectingFactory, 'build', with_document_block),
    ))
    assert default_path.read_bytes() == original
    engine = factory.engines[0]
    payload = _latest_top_payload(Path(engine.checkpointer.path))
    post = engine.stage_service.target_post_write_composition
    assert summary['deliverable_final_result'] is None
    assert summary['release_publication'] is None
    assert summary['target_release_publication'] is None
    assert payload['target_release_publication'] is None
    assert payload.get('deliverable_final_result') is None
    assert payload['artifact_route'] == 'blocked'
    assert payload['terminal_error'] is None
    assert payload['terminal_outcome']['status'] == 'BLOCK'
    assert payload['terminal_outcome']['publication_exists'] is False
    assert not list(post.release_store.root.rglob('*.json'))
    control = post.control_store.resolve_exact(payload['target_post_write_control_decision_ref'])
    assert control.enforcement_action is EnforcementAction.BLOCK
    terminal = engine.lifecycle_service.load_run_lifecycle(run_id)
    assert terminal.status.value == 'success'
    assert terminal.run_completion_assessment_ref is not None
    budget = engine.budget_runtime(summary['task_id'], run_id).canonical_state()
    assert budget.limits.logical_model_calls == 44
    assert budget.limits.model_tokens_total == 64000
    assert budget.used.logical_model_calls == 43
    assert all(value == 0 for value in budget.active_reserved.model_dump().values())
