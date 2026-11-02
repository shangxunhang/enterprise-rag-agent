"""Historical publication scope and internal integrity, from real I8 authority."""
from unittest.mock import patch
from contextlib import contextmanager
import base64
import hashlib
import json
import pytest
from apps.enterprise_document.schemas.post_write.release import PublicationReuseState as Reuse
from apps.enterprise_document.services.post_write.release import PostWriteReleaseError, PostWriteReleaseIntegrityError
from apps.enterprise_document.schemas.post_write.release import PostWriteReleaseManifestSchema
from backend.tests.bca014_i9_fixtures import terminal_case, publish, changed_request, manifests, make_target
from core.hashing import canonical_sha256


@contextmanager
def rehashed_manifest(post, first, **changes):
    """Corrupt only a temporary committed envelope, retaining valid self-hashes."""
    path = post.release_store._path_for_ref(first.manifest_artifact.artifact_ref)
    before = path.read_bytes()
    payload = first.manifest.model_dump(mode='json')
    payload.update(changes)
    payload.pop('manifest_payload_sha256')
    payload['manifest_payload_sha256'] = canonical_sha256(payload)
    tampered = PostWriteReleaseManifestSchema.model_validate(payload)
    content = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(',', ':')).encode()
    envelope = json.loads(before)
    envelope['content_base64'] = base64.b64encode(content).decode()
    envelope['reference']['content_hash'] = 'sha256:' + hashlib.sha256(content).hexdigest()
    envelope['reference']['size_bytes'] = len(content)
    try:
        path.write_text(json.dumps(envelope), encoding='utf8')
        # Prove physical envelope and Release schema are both valid before replay.
        _, stored = post.release_store.get_json_with_reference(
            first.manifest_artifact.artifact_ref, task_id=tampered.task_id, run_id=tampered.run_id)
        assert PostWriteReleaseManifestSchema.model_validate(stored) == tampered
        yield tampered
    finally:
        path.write_bytes(before)


@pytest.fixture(scope='module')
def published(tmp_path_factory):
    case = terminal_case(tmp_path_factory.mktemp('i9-scope'), orphan=True)
    return case, publish(case)


@pytest.mark.parametrize('field,value', [('run_id','foreign-run'), ('task_id','foreign-task'),
    ('document_logical_id','foreign-document')])
def test_foreign_scope_cannot_restore_same_physical_identity(published, field, value):
    case, first = published
    post, _, result, request = case
    foreign = changed_request(post, request, **{field:value})
    assert post.release_service.publication_identity(foreign) == post.release_service.publication_identity(request)
    # Deliberately allow cross-task lookup to prove the guard, rather than store
    # partitioning alone, enforces scope. The real same-task run/doc cases need no seam.
    get = post.release_store.get_json_with_reference
    def lookup(ref, **kw):
        return get(ref, **{**kw, 'task_id':request.task_id})
    with patch.object(post.release_store, 'get_json_with_reference', side_effect=lookup), \
            patch.object(post.release_service.input_guard, 'validate_new_publication', side_effect=AssertionError('fresh eligibility')):
        with pytest.raises(PostWriteReleaseError):
            post.release_service.publish(request=foreign,
                run_completion_assessment_ref=result.run_completion_assessment_ref,
                run_lifecycle_revision=result.terminal_run.lifecycle_revision,
                active_document_binding_supplier=lambda: (_ for _ in ()).throw(AssertionError('supplier')))
    assert len(manifests(post)) == 1


@pytest.mark.parametrize('field,value', [('renderer_contract_version','foreign-renderer'),
    ('output_format','foreign-format'), ('quality_state_passthrough','PASS'),
    ('run_lifecycle_revision',999)])
def test_schema_valid_manifest_cannot_disagree_with_durable_proof(published,field,value):
    case,first=published;post=case[0]
    path=post.release_store._path_for_ref(first.manifest_artifact.artifact_ref)
    before=path.read_bytes()
    payload=first.manifest.model_dump(mode='json');payload[field]=value
    payload.pop('manifest_payload_sha256')
    payload['manifest_payload_sha256']=canonical_sha256(payload)
    # Some identity changes fail schema directly; non-identity semantic changes
    # remain schema-valid and must still be checked against the immutable proof.
    try:
        envelope=json.loads(before)
        content=json.dumps(payload,ensure_ascii=False,sort_keys=True,separators=(',',':')).encode()
        envelope['content_base64']=base64.b64encode(content).decode()
        envelope['reference']['content_hash']='sha256:'+hashlib.sha256(content).hexdigest()
        envelope['reference']['size_bytes']=len(content)
        path.write_text(json.dumps(envelope),encoding='utf8')
        with pytest.raises(PostWriteReleaseError):publish(case)
    finally:path.write_bytes(before)
    assert publish(case).manifest == first.manifest


def test_exact_and_same_output_replay_preserve_original_authority(published):
    case, first = published
    post, _, result, request = case
    orphan_ref = post._i9_orphan_control
    assert post.control_store.resolve_exact(orphan_ref).as_ref()==orphan_ref
    incoming = changed_request(post, request, post_write_control_decision_ref=orphan_ref)
    with patch.object(post.release_service.release_commit_fence, 'hold', side_effect=AssertionError('fresh fence')), \
            patch.object(post.currentness_resolver, 'prove', side_effect=AssertionError('fresh currentness')), \
            patch.object(post.release_service.input_guard, 'validate_new_publication', side_effect=AssertionError('fresh eligibility')):
        for req, state in ((request, Reuse.EXACT_REQUEST_REPLAY), (incoming, Reuse.ALREADY_PUBLISHED_SAME_OUTPUT)):
            replay = post.release_service.publish(request=req,
                run_completion_assessment_ref=result.run_completion_assessment_ref,
                run_lifecycle_revision=result.terminal_run.lifecycle_revision,
                active_document_binding_supplier=lambda: (_ for _ in ()).throw(AssertionError('supplier')))
            assert replay.reuse_state is state
            assert replay.manifest == first.manifest
    assert len(manifests(post)) == 1


def test_schema_valid_logical_scope_tamper_rejects_matching_request(published):
    case, first = published
    post, _, result, request = case
    assert first.reuse_state is Reuse.NEW_COMMIT
    incoming = changed_request(post, request, document_logical_id='document-logical:TAMPERED')
    assert post.release_service.publication_identity(incoming) == post.release_service.publication_identity(request)
    with rehashed_manifest(post, first, document_logical_id=incoming.document_logical_id), \
            patch.object(post.currentness_resolver, 'prove', side_effect=AssertionError('currentness')), \
            patch.object(post.release_service.release_commit_fence, 'hold', side_effect=AssertionError('fence')):
        before = {p: p.read_bytes() for p in post.release_store.root.rglob('*') if p.is_file()}
        with pytest.raises(PostWriteReleaseIntegrityError, match='Assembly'):
            post.release_service.publish(request=incoming,
                run_completion_assessment_ref=result.run_completion_assessment_ref,
                run_lifecycle_revision=result.terminal_run.lifecycle_revision,
                active_document_binding_supplier=lambda: (_ for _ in ()).throw(AssertionError('supplier')))
        assert before == {p: p.read_bytes() for p in post.release_store.root.rglob('*') if p.is_file()}
    assert publish(case).manifest == first.manifest


@pytest.mark.parametrize('mode', ['unreadable', 'same-document-wrong-ref'])
def test_historical_assembly_resolution_fails_closed(published, mode):
    case, first = published
    post = case[0]
    store = post.currentness_resolver.document_binding_store
    assembly = store.resolve_assembly_manifest(first.manifest.assembly_input_manifest_ref)
    assert assembly.document_logical_id == first.manifest.document_logical_id
    wrong = assembly.model_copy(update={'manifest_id': 'assembly-manifest://foreign'})
    kwargs = {'side_effect': RuntimeError('missing immutable Assembly')} if mode == 'unreadable' else {'return_value': wrong}
    with patch.object(store, 'resolve_assembly_manifest', **kwargs):
        with pytest.raises(PostWriteReleaseIntegrityError, match='Assembly'):
            publish(case)
    assert publish(case).manifest == first.manifest


def test_rehashed_manifest_rejects_another_existing_v2_assembly_ref(tmp_path):
    post, target_request = make_target(tmp_path)
    complete = post._commit_and_terminalize_run
    alternates = []
    def prepare(**kwargs):
        original = post.binding_store.resolve_assembly_manifest(
            kwargs['final_document'].active_projection.assembly_input_manifest_ref)
        builder = post.manifest_builder
        # Persist actual V2 alternatives under the ordinary RUNNING write guard.
        for logical_id in ('document-logical:foreign', original.document_logical_id):
            entries = tuple(builder.missing_entry(key, 'FOREIGN_ASSEMBLY') for key in original.required_section_keys)
            ref = builder.build_and_persist(document_logical_id=logical_id,
                required_section_keys=original.required_section_keys, ordered_section_entries=entries)
            assert post.binding_store.resolve_assembly_manifest(ref).as_ref() == ref
            assert ref != original.as_ref()
            alternates.append(ref)
        return complete(**kwargs)
    with patch.object(post, '_commit_and_terminalize_run', side_effect=prepare):
        result = post.complete_target_post_write(**target_request)
    first = result.published_release
    manifest = first.manifest
    request = post.release_service.build_request(**{field: getattr(manifest, field) for field in (
        'release_contract_version', 'task_id', 'run_id', 'document_logical_id',
        'document_artifact_ref', 'post_write_control_decision_ref', 'output_format',
        'validated_output_binding_sha256', 'publication_target_id')})
    for ref in alternates:
        with rehashed_manifest(post, first, assembly_input_manifest_ref=ref.model_dump(mode='json')):
            with pytest.raises(PostWriteReleaseIntegrityError):
                post.release_service._restore_existing(request=request, key=manifest.idempotency_key)
    assert post.release_service._restore_existing(request=request, key=manifest.idempotency_key).manifest == manifest


@pytest.mark.parametrize('part', ['manifest','proof','payload'])
def test_real_target_historical_corruption_fails_closed(published, part):
    case, first = published
    post = case[0]
    ref = {'manifest':first.manifest_artifact, 'proof':first.manifest.release_input_proof_artifact,
        'payload':first.manifest.release_payload_artifact}[part]
    path = post.release_store._path_for_ref(ref.artifact_ref)
    before = path.read_bytes()
    try:
        path.write_bytes(b'{broken' if part != 'payload' else b'tampered output')
        with pytest.raises(PostWriteReleaseError):
            publish(case)
    finally:
        path.write_bytes(before)
    assert len(manifests(post)) == 1
