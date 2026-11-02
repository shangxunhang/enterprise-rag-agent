"""Immutable Claim byte integrity and legacy guard-only compatibility.

Fresh canonical owners produce every Claim. Corruption is injected only into
the test's new ledger; original bytes are retained beside it. No currentness,
authorizer, provenance or commit guard is replaced.
"""
import json
from types import SimpleNamespace

import pytest

from apps.enterprise_document.schemas.agent_rag_v1.claims import ClaimRecordSchema
from apps.enterprise_document.schemas.agent_rag_v1.claim_support import SupportRelation
from apps.enterprise_document.schemas.agent_rag_v1.verification import (
    ClaimBindingRelation, VerificationVerdict,
)
from apps.enterprise_document.services.agent_rag_v1.claim_verification_runtime import (
    ClaimBindingError, ClaimVerificationIntegrityError, ClaimVerificationRuntime,
    VerifierCandidateContractViolationError,
)
from backend.tests import test_multi_agent_v3_claim_verification_runtime as owners


def _reopen(runtime, paragraph):
    return ClaimVerificationRuntime(
        paragraph_runtime=paragraph,
        binding_source_validator=runtime.binding_source_validator,
        ledger_path=runtime.ledger_path,
    )


def test_valid_immutable_claim_reopen_and_exact_commit_replay_preserve_bytes(tmp_path):
    env, paragraph, runtime, version, _manifest, candidate, first, *_ = owners._commit_claim(tmp_path)
    before = runtime.ledger_path.read_bytes()
    reopened = _reopen(runtime, paragraph)
    assert reopened.resolve_claim(first.records[0].claim_ref) == first.records[0]
    replay = reopened.commit_claim_records(
        (candidate,), owners._coverage(version, candidate), owners._context(env),
    )
    assert replay == first
    assert runtime.ledger_path.read_bytes() == before


@pytest.mark.parametrize("material", ["normalized_value", "text_span"])
@pytest.mark.parametrize("reader", ["existing-owner", "reopened-owner"])
def test_immutable_claim_loader_recomputes_actual_material_hash(tmp_path, material, reader):
    _env, paragraph, runtime, _version, _manifest, _candidate, committed, *_ = owners._commit_claim(tmp_path)
    path = runtime.ledger_path
    before = path.read_bytes()
    (tmp_path / "claim-integrity-before.jsonl").write_bytes(before)
    rows = [json.loads(line) for line in before.decode("utf-8").splitlines()]
    event = next(row for row in rows if row["event_type"] == "claim_records_committed")
    record = event["claim_records"][0]
    if material == "normalized_value":
        record["normalized_value"] = {"corrupted": "actual immutable Claim material"}
    else:
        text = record["text_span"]["text"]
        record["text_span"]["text"] = "X" + text[1:]
    # The supplied hash, ClaimRef and occurrence remain mutually consistent;
    # the unchanged schema alone cannot establish actual material integrity.
    parsed = ClaimRecordSchema.model_validate(record)
    assert parsed.claim_ref == committed.records[0].claim_ref
    damaged = ("\n".join(json.dumps(row, ensure_ascii=False, sort_keys=True)
        for row in rows) + "\n").encode("utf-8")
    assert damaged != before
    path.write_bytes(damaged)
    owner = runtime if reader == "existing-owner" else _reopen(runtime, paragraph)
    expected = "normalized_value_hash" if material == "normalized_value" else "text_span_hash"
    with pytest.raises(ClaimVerificationIntegrityError, match=expected):
        owner.resolve_claim(committed.records[0].claim_ref)
    assert path.read_bytes() == damaged


def _partial_guard_material(*, missing_support=False, no_material_support=False):
    evidence = "evidence:legacy-guard-only"
    project = "writer-project-input-basis:legacy-guard-only"
    binding = SimpleNamespace(
        exact_authorized_source_refs=(SimpleNamespace(exact_source_ref=evidence, source_kind="EVIDENCE"),),
        facts=(SimpleNamespace(exact_source_ref=evidence, source_kind="EVIDENCE",
            relation=ClaimBindingRelation.NO_MATERIAL_SUPPORT if no_material_support else ClaimBindingRelation.SUPPORTS),),
    )
    support = SimpleNamespace(relation=SupportRelation.SUPPORTED,
        supporting_refs=() if missing_support else (evidence,))
    candidate = SimpleNamespace(
        verifier_project_input_basis=SimpleNamespace(project_input_basis_ref=project),
        allowed_authority_refs=(evidence, project), positive_basis_refs=(evidence,),
        recommended_verdict=VerificationVerdict.UNSUPPORTED, issue_candidates=(),
    )
    return binding, support, candidate


@pytest.mark.parametrize("defect", ["shared-support", "binding-observation"])
def test_partial_legacy_guard_candidate_keeps_generic_intersection_refusal(defect):
    binding, support, candidate = _partial_guard_material(
        missing_support=defect == "shared-support",
        no_material_support=defect == "binding-observation",
    )
    with pytest.raises(ClaimBindingError) as caught:
        ClaimVerificationRuntime.validate_v2_provenance(binding, support, candidate)
    assert type(caught.value) is ClaimBindingError
    assert not isinstance(caught.value, VerifierCandidateContractViolationError)
    assert str(caught.value) == "Evidence positive basis requires shared supporting refs with admissible Binding observations"


def test_partial_legacy_guard_candidate_retains_legal_evidence_admission():
    ClaimVerificationRuntime.validate_v2_provenance(*_partial_guard_material())
