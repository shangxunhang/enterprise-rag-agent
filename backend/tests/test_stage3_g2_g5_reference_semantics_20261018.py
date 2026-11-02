"""Synthetic evaluator-only regressions; these fixtures are not reviewed Gold."""
from copy import deepcopy
from dataclasses import replace
from hashlib import sha256
import json

import pytest

from eval.full_chain.contracts import EvaluationEvidenceRef, StageObservation, canonical_hash
from eval.full_chain.reference_metrics import evaluate_references
from eval.full_chain.references import ReferenceView, load_references
from eval.full_chain.snapshot import SnapshotError, SnapshotView


_UNSET = object()


def _candidate(identifier):
    return {'child_chunk_id': identifier, 'chunk_id': identifier}


def _fixture(family, transport, *, reference_id='synthetic-reference', results=None,
             gold=_UNSET, top_k=2, scope='DOCUMENT', requirements=None, citations=None):
    """Bind only small, explicit synthetic observations; no runtime or model call."""
    if family == 'G2':
        labels = {'relevant_allowed': ['E1', 'E2', 'E3'], 'relevant_forbidden': ['FORBIDDEN']}
        if gold is not _UNSET:
            if gold is None:
                labels.pop('relevant_allowed')
            else:
                labels['relevant_allowed'] = gold
        payload = {'grain': 'child', 'top_k': top_k,
                   'results': [_candidate('E1'), _candidate('E2')] if results is None else results}
        paths = {key: key for key in ('results', 'grain', 'top_k')}
        unit = 'C12'
    else:
        labels = {'requirements': ['R1', 'R2'], 'expected_citation_refs': ['C1', 'C2']}
        payload = {'requirements': ['R1', 'R2'] if requirements is None else requirements,
                   'citation_refs': ['C1', 'C2'] if citations is None else citations}
        paths = {key: key for key in ('requirements', 'citation_refs')}
        unit = 'E08'
    ref = {'reference_id': reference_id, 'family': family,
           'source_sha256': canonical_hash(['synthetic-source', reference_id]),
           'reference_kind': 'SYNTHETIC_MECHANISM',
           'effective_review_status': 'SYNTHETIC_MECHANISM', 'labels': labels}
    stage = None
    row = None
    if transport == 'native':
        if family == 'G5':
            payload['source_scope'] = scope
        raw_hash = canonical_hash(payload)
        stage = StageObservation(unit, 'synthetic-subject', operation_id='synthetic:' + reference_id,
            scope=scope, measurement_state='MEASURED', payload=payload,
            evidence_refs=(EvaluationEvidenceRef('synthetic-memory:' + reference_id,
                raw_sha256=raw_hash, integrity='VALID', completeness='COMPLETE'),))
        ref['match_rules'] = {'native_observation': {'unit_id': unit,
            'operation_id': stage.operation_id, 'source_raw_sha256': raw_hash,
            'scope': scope, 'field_paths': paths}}
    else:
        row = {'reference_id': reference_id, 'source_sha256': ref['source_sha256'],
               'input_completeness': 'COMPLETE', **payload}
        if family == 'G5':
            row['scope'] = scope
    return ref, row, stage


def _evaluate(ref, row, stage):
    snapshot = SnapshotView.from_records({'reference_observations': [row] if row is not None else []})
    return evaluate_references(snapshot, ReferenceView((ref,)), subject_id='synthetic-subject',
                               stages=(stage,) if stage is not None else ())


def _metric(batch, metric_id, *, unit_id=None):
    return next(metric for metric in batch.metrics
                if metric.metric_id == metric_id and (unit_id is None or metric.unit_id == unit_id))


def _assert_unmeasured(batch):
    assert batch.metrics
    assert all(metric.measurement_state == 'UNMEASURED' for metric in batch.metrics), batch.metrics
    assert all(metric.assertion_result is None for metric in batch.metrics)
    assert not batch.findings


@pytest.mark.parametrize('transport', ['native', 'direct'])
@pytest.mark.parametrize('selected,expected_recall,expected_mrr,expected_ndcg,passes', [
    (['E1', 'E2'], 2 / 3, 1.0, 1.0, True),
    (['E4', 'E2'], 1 / 3, 0.5, 0.38685280723454163, False),
])
def test_g2_frozen_k_ceiling_preserves_raw_ranked_values(
        transport, selected, expected_recall, expected_mrr, expected_ndcg, passes):
    batch = _evaluate(*_fixture('G2', transport, results=[_candidate(item) for item in selected]))
    recall = _metric(batch, 'recall_at_k')
    assert recall.value == expected_recall
    assert recall.assertion_result is passes
    assert recall.measurement_state == 'MEASURED'
    assert recall.calculator_ref == 'eval.rag.strategy.metrics.recall_at_k'
    assert _metric(batch, 'mrr').value == expected_mrr
    assert _metric(batch, 'ndcg_at_k').value == pytest.approx(expected_ndcg)
    assert _metric(batch, 'hit_at_k').value == 1.0
    assert _metric(batch, 'forbidden_use_count').value == 0
    assert bool([finding for finding in batch.findings
                 if finding.code == 'REFERENCE_QUALITY_FAILURE'
                 and finding.message == 'G2:recall_at_k']) is (not passes)


@pytest.mark.parametrize('transport', ['native', 'direct'])
@pytest.mark.parametrize('gold,selected,top_k', [
    (['E1'], ['E1'], 2),
    (['E1', 'E2'], ['E1', 'E2'], 2),
    (['E1', 'E2', 'E3'], ['E1', 'E2', 'E3'], 4),
])
def test_g2_gold_count_at_or_below_k_requires_complete_recall(transport, gold, selected, top_k):
    batch = _evaluate(*_fixture('G2', transport, gold=gold, top_k=top_k,
                               results=[_candidate(item) for item in selected]))
    recall = _metric(batch, 'recall_at_k')
    assert recall.value == 1.0 and recall.assertion_result is True
    assert not batch.findings


@pytest.mark.parametrize('transport', ['native', 'direct'])
def test_g2_duplicate_gold_is_a_set_and_duplicate_candidates_consume_k(transport):
    good = _evaluate(*_fixture('G2', transport, gold=['E1', 'E1', 'E2', 'E3']))
    assert _metric(good, 'recall_at_k').value == 2 / 3
    assert _metric(good, 'recall_at_k').assertion_result is True
    duplicate = _evaluate(*_fixture('G2', transport,
        results=[_candidate('E1'), _candidate('E1'), _candidate('E2')]))
    assert _metric(duplicate, 'recall_at_k').value == 1 / 3
    assert _metric(duplicate, 'recall_at_k').assertion_result is False
    assert _metric(duplicate, 'ndcg_at_k').value < 1.0
    assert any(finding.code == 'REFERENCE_QUALITY_FAILURE' for finding in duplicate.findings)


@pytest.mark.parametrize('transport', ['native', 'direct'])
@pytest.mark.parametrize('gold,state', [([], 'N/A'), (None, 'UNMEASURED')])
def test_g2_empty_or_missing_reference_never_becomes_recall_pass(transport, gold, state):
    batch = _evaluate(*_fixture('G2', transport, gold=gold))
    recall = _metric(batch, 'recall_at_k')
    assert recall.measurement_state == state
    assert recall.value is None and recall.assertion_result is None


@pytest.mark.parametrize('transport', ['native', 'direct'])
@pytest.mark.parametrize('gold', [[''], ['E1', None], [4], 'E1'])
def test_g2_invalid_gold_identity_is_unmeasured(transport, gold):
    _assert_unmeasured(_evaluate(*_fixture('G2', transport, gold=gold)))


@pytest.mark.parametrize('transport', ['native', 'direct'])
@pytest.mark.parametrize('top_k', [True, False, 0, -1, 2.0, '2', None])
def test_g2_invalid_k_is_unmeasured_without_a_quality_pass(transport, top_k):
    _assert_unmeasured(_evaluate(*_fixture('G2', transport, top_k=top_k)))


@pytest.mark.parametrize('transport', ['native', 'direct'])
@pytest.mark.parametrize('results', [[{}], [{'child_chunk_id': ''}], [{'child_chunk_id': 4}], [None]])
def test_g2_missing_candidate_identity_is_unmeasured(transport, results):
    _assert_unmeasured(_evaluate(*_fixture('G2', transport, results=results)))


@pytest.mark.parametrize('transport', ['native', 'direct'])
def test_g2_forbidden_child_identity_still_fails(transport):
    batch = _evaluate(*_fixture('G2', transport,
        results=[{'child_chunk_id': 'FORBIDDEN'}, _candidate('E2')]))
    forbidden = _metric(batch, 'forbidden_use_count')
    assert forbidden.value == 1 and forbidden.assertion_result is False
    assert _metric(batch, 'recall_at_k').value == 1 / 3
    assert any(finding.message == 'G2:forbidden_use_count' for finding in batch.findings)


@pytest.mark.parametrize('transport', ['native', 'direct'])
@pytest.mark.parametrize('variant', ['source_mismatch', 'incomplete', 'provisional'])
def test_g2_reference_authority_and_completeness_still_reject(transport, variant):
    ref, row, stage = _fixture('G2', transport)
    if variant == 'provisional':
        ref['effective_review_status'] = 'PROVISIONAL'
    elif transport == 'native':
        if variant == 'source_mismatch':
            ref['match_rules']['native_observation']['source_raw_sha256'] = 'wrong-hash'
        else:
            stage = replace(stage, evidence_refs=(replace(stage.evidence_refs[0], completeness='PARTIAL'),))
    elif variant == 'source_mismatch':
        row['source_sha256'] = 'wrong-hash'
    else:
        row['input_completeness'] = 'PARTIAL'
    _assert_unmeasured(_evaluate(ref, row, stage))


@pytest.mark.parametrize('variant', ['wrong_operation', 'ambiguous', 'invalid_evidence'])
def test_g2_exact_native_binding_and_integrity_still_required(variant):
    ref, row, stage = _fixture('G2', 'native')
    if variant == 'wrong_operation':
        ref['match_rules']['native_observation']['operation_id'] = 'other-operation'
    elif variant == 'invalid_evidence':
        stage = replace(stage, evidence_refs=(replace(stage.evidence_refs[0], integrity='INVALID'),))
    if variant == 'ambiguous':
        batch = evaluate_references(SnapshotView.from_records({}), ReferenceView((ref,)), stages=(stage, stage))
    else:
        batch = _evaluate(ref, row, stage)
    _assert_unmeasured(batch)


@pytest.mark.parametrize('transport,scope', [('native', 'SECTION'), ('native', 'DOCUMENT'), ('direct', 'DOCUMENT')])
def test_g5_proven_scope_is_used_by_requirement_and_citation_metrics(transport, scope):
    batch = _evaluate(*_fixture('G5', transport, scope=scope))
    assert len(batch.metrics) == 8
    assert {metric.scope for metric in batch.metrics} == {scope}
    for unit in ('E08', 'E05'):
        assert _metric(batch, 'recall', unit_id=unit).value == 1.0
        assert _metric(batch, 'recall', unit_id=unit).denominator == 2
        assert _metric(batch, 'recall', unit_id=unit).assertion_result is True
    assert not batch.findings


@pytest.mark.parametrize('scope', ['SECTION', 'DOCUMENT'])
def test_g5_explicit_stage_scope_does_not_require_an_extra_payload_field(scope):
    ref, row, stage = _fixture('G5', 'native', scope=scope)
    payload = dict(stage.payload)
    payload.pop('source_scope')
    stage = replace(stage, payload=payload)
    batch = _evaluate(ref, row, stage)
    assert {metric.scope for metric in batch.metrics} == {scope}
    assert _metric(batch, 'recall', unit_id='E08').assertion_result is True


@pytest.mark.parametrize('variant', ['missing', 'invalid_stage', 'invalid_payload', 'payload_conflict',
                                    'binding_conflict', 'invalid_binding'])
def test_g5_missing_invalid_or_conflicting_native_scope_is_unmeasured(variant):
    ref, row, stage = _fixture('G5', 'native', scope='SECTION')
    binding = ref['match_rules']['native_observation']
    if variant in {'missing', 'invalid_stage'}:
        payload = dict(stage.payload)
        payload.pop('source_scope')
        binding.pop('scope')
        stage = replace(stage, scope='unknown' if variant == 'missing' else 'SECTION_NOT_A_SCOPE', payload=payload)
    elif variant in {'invalid_payload', 'payload_conflict'}:
        stage = replace(stage, payload={**stage.payload,
            'source_scope': 'INVALID' if variant == 'invalid_payload' else 'DOCUMENT'})
    else:
        binding['scope'] = 'DOCUMENT' if variant == 'binding_conflict' else 'INVALID'
    _assert_unmeasured(_evaluate(ref, row, stage))


@pytest.mark.parametrize('scope', [None, 'unknown', 'INVALID'])
def test_g5_direct_scope_absence_or_invalid_value_is_unmeasured(scope):
    ref, row, stage = _fixture('G5', 'direct')
    if scope is None:
        row.pop('scope')
    else:
        row['scope'] = scope
    _assert_unmeasured(_evaluate(ref, row, stage))


@pytest.mark.parametrize('signal', ['source_scope', 'binding_scope'])
def test_g5_direct_scope_conflicts_are_unmeasured(signal):
    ref, row, stage = _fixture('G5', 'direct', scope='DOCUMENT')
    if signal == 'source_scope':
        row['source_scope'] = 'SECTION'
    else:
        ref['match_rules'] = {'native_observation': {'scope': 'SECTION'}}
    _assert_unmeasured(_evaluate(ref, row, stage))


def test_g5_explicit_null_native_binding_is_legal_absence_for_direct_document():
    ref, row, stage = _fixture('G5', 'direct', scope='DOCUMENT')
    ref['match_rules'] = {'native_observation': None}
    batch = _evaluate(ref, row, stage)
    assert {metric.scope for metric in batch.metrics} == {'DOCUMENT'}
    assert all(metric.measurement_state == 'MEASURED' for metric in batch.metrics)
    assert _metric(batch, 'recall', unit_id='E08').assertion_result is True
    assert _metric(batch, 'recall', unit_id='E05').assertion_result is True
    assert not batch.findings


@pytest.mark.parametrize('binding', ['invalid-binding', [], 7, {'scope': None}])
def test_g5_direct_malformed_binding_or_explicit_null_binding_scope_is_unmeasured(binding):
    ref, row, stage = _fixture('G5', 'direct', scope='DOCUMENT')
    ref['match_rules'] = {'native_observation': binding}
    _assert_unmeasured(_evaluate(ref, row, stage))


@pytest.mark.parametrize('transport', ['native', 'direct'])
@pytest.mark.parametrize('negative', ['requirements', 'citations'])
def test_g5_missing_requirement_and_wrong_citation_still_fail_with_the_proven_scope(transport, negative):
    scope = 'SECTION' if transport == 'native' else 'DOCUMENT'
    args = {'requirements': ['R1']} if negative == 'requirements' else {'citations': ['C1', 'WRONG']}
    batch = _evaluate(*_fixture('G5', transport, scope=scope, **args))
    assert {metric.scope for metric in batch.metrics} == {scope}
    affected = 'E08' if negative == 'requirements' else 'E05'
    unaffected = 'E05' if affected == 'E08' else 'E08'
    assert _metric(batch, 'recall', unit_id=affected).value == 0.5
    assert _metric(batch, 'recall', unit_id=affected).assertion_result is False
    assert _metric(batch, 'recall', unit_id=unaffected).value == 1.0
    if negative == 'citations':
        assert _metric(batch, 'false_positive_count', unit_id='E05').value == 1
        assert _metric(batch, 'false_positive_count', unit_id='E05').assertion_result is False
    assert any(finding.code == 'REFERENCE_QUALITY_FAILURE' and finding.unit_id == affected
               for finding in batch.findings)


@pytest.mark.parametrize('transport', ['native', 'direct'])
def test_g5_duplicate_citation_is_still_a_false_positive(transport):
    scope = 'SECTION' if transport == 'native' else 'DOCUMENT'
    batch = _evaluate(*_fixture('G5', transport, scope=scope, citations=['C1', 'C1', 'C2']))
    assert _metric(batch, 'recall', unit_id='E05').value == 1.0
    precision = _metric(batch, 'precision', unit_id='E05')
    assert precision.value == 2 / 3 and precision.assertion_result is False
    duplicate = _metric(batch, 'false_positive_count', unit_id='E05')
    assert duplicate.value == 1 and duplicate.assertion_result is False
    assert duplicate.scope == scope
    assert any(finding.message == 'G5:false_positive_count' for finding in batch.findings)


@pytest.mark.parametrize('transport', ['native', 'direct'])
@pytest.mark.parametrize('variant', ['source_mismatch', 'incomplete', 'provisional'])
def test_g5_proven_scope_cannot_bypass_reference_authority_or_completeness(transport, variant):
    ref, row, stage = _fixture('G5', transport)
    if variant == 'provisional':
        ref['effective_review_status'] = 'PROVISIONAL'
    elif transport == 'native':
        if variant == 'source_mismatch':
            ref['match_rules']['native_observation']['source_raw_sha256'] = 'wrong-hash'
        else:
            stage = replace(stage, evidence_refs=(replace(stage.evidence_refs[0], completeness='PARTIAL'),))
    elif variant == 'source_mismatch':
        row['source_sha256'] = 'wrong-hash'
    else:
        row['input_completeness'] = 'PARTIAL'
    _assert_unmeasured(_evaluate(ref, row, stage))


@pytest.mark.parametrize('variant', ['no_evidence', 'invalid_evidence'])
def test_g5_native_scope_requires_valid_evidence(variant):
    ref, row, stage = _fixture('G5', 'native', scope='SECTION')
    evidence = () if variant == 'no_evidence' else (replace(stage.evidence_refs[0], integrity='INVALID'),)
    _assert_unmeasured(_evaluate(ref, row, replace(stage, evidence_refs=evidence)))


def test_g5_section_and_document_keep_independent_denominators():
    section, _, section_stage = _fixture('G5', 'native', reference_id='synthetic-section', scope='SECTION')
    document, _, document_stage = _fixture('G5', 'native', reference_id='synthetic-document', scope='DOCUMENT')
    document['labels']['requirements'] = ['R1', 'R2', 'R3']
    refs_before = deepcopy((section, document))
    batch = evaluate_references(SnapshotView.from_records({}), ReferenceView((section, document)),
                                stages=(section_stage, document_stage))
    by_scope = {metric.scope: metric for metric in batch.metrics
                if metric.metric_id == 'recall' and metric.unit_id == 'E08'}
    assert set(by_scope) == {'SECTION', 'DOCUMENT'}
    assert (by_scope['SECTION'].numerator, by_scope['SECTION'].denominator) == (2, 2)
    assert by_scope['SECTION'].value == 1.0 and by_scope['SECTION'].assertion_result is True
    assert (by_scope['DOCUMENT'].numerator, by_scope['DOCUMENT'].denominator) == (2, 3)
    assert by_scope['DOCUMENT'].value == 2 / 3 and by_scope['DOCUMENT'].assertion_result is False
    assert (section, document) == refs_before


@pytest.mark.parametrize('variant,reason', [
    ('file_hash', 'REFERENCE_FILE_HASH_MISMATCH'),
    ('source_hash', 'REFERENCE_SOURCE_HASH_MISMATCH'),
    ('duplicate', 'DUPLICATE_REFERENCE_ID'),
    ('outside_root', 'OUTSIDE_ALLOWED_ROOT'),
])
def test_reference_loader_still_rejects_tampering_and_unauthorized_paths(tmp_path, variant, reason):
    root = tmp_path / 'allowed'
    root.mkdir()
    source = 'Synthetic loader boundary fixture; no independent human review.'
    row = {'reference_id': 'synthetic-loader', 'family': 'G2', 'source': source,
           'source_sha256': sha256(source.encode()).hexdigest(),
           'labels': {'relevant_allowed': ['E1']}}
    if variant == 'source_hash':
        row['source_sha256'] = '0' * 64
    raw = (json.dumps(row) + '\n').encode()
    if variant == 'duplicate':
        raw += raw
    labels = tmp_path / 'outside.jsonl' if variant == 'outside_root' else root / 'labels.jsonl'
    labels.write_bytes(raw)
    manifest = {'schema_version': 'full_chain_reference_manifest_v1',
                'trusted_reviewers': {}, 'files': [{
                    'path': str(labels),
                    'sha256': '0' * 64 if variant == 'file_hash' else sha256(raw).hexdigest()}]}
    path = root / 'manifest.json'
    path.write_text(json.dumps(manifest), encoding='utf-8')
    with pytest.raises((ValueError, SnapshotError), match=reason):
        load_references(path, [root])
