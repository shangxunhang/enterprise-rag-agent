"""Frozen V2 positive/issue provenance and Support relation admission matrix."""
import pytest

from core.hashing import canonical_sha256
from apps.enterprise_document.schemas.agent_rag_v1.claim_support import SupportRelation
from apps.enterprise_document.schemas.agent_rag_v1.verification import (
    ClaimAuthorizedSourceRefSchema, ClaimBindingFactsCandidateSchema,
    ClaimBindingRelation, VerificationVerdict,
)
from apps.enterprise_document.services.agent_rag_v1.claim_verifier_composition import TargetClaimVerifierComposition
from apps.enterprise_document.services.agent_rag_v1.claim_verification_runtime import ClaimBindingError
from backend.tests.test_bca014_i5_verifier_project_input_basis import fixture
from backend.tests import test_multi_agent_v3_claim_verification_runtime as owners


def enriched_binding(fx):
    old = fx.runtime._binding_record_candidate(fx.binding)
    sources = list(old.exact_authorized_source_refs)
    facts = list(old.facts)
    for kind in ("FACT", "FACT_SNAPSHOT", "EVIDENCE_PACKAGE"):
        ref = kind.lower() + ":i5"
        sources.append(ClaimAuthorizedSourceRefSchema(source_kind=kind, exact_source_ref=ref))
        facts.append(facts[0].model_copy(update={"source_kind": kind, "exact_source_ref": ref}))
    sources.sort(key=lambda i: (i.source_kind, i.exact_source_ref))
    facts.sort(key=lambda i: (i.source_kind, i.exact_source_ref, i.relation.value, i.support_kind.value, i.source_scope_ref or ""))
    data = old.model_dump(mode="json")
    data.update(exact_authorized_source_refs=[i.model_dump(mode="json") for i in sources],
        facts=[i.model_dump(mode="json") for i in facts], source_set_hash=canonical_sha256([i.model_dump(mode="json") for i in sources]))
    candidate = ClaimBindingFactsCandidateSchema.model_validate(data)
    candidate = candidate.model_copy(update={"candidate_semantic_fingerprint": fx.runtime.binding_candidate_semantic_fingerprint(candidate)})
    fx.binding = fx.runtime.commit_binding_facts(candidate, owners._context(fx.env,
        authorization_ref="binding:i5-enriched", purpose="test-binding"))
    fx.invocation, _ = fx.composer.build_input(fx.claim, fx.binding, fx.support, fx.context)
    return fx


def parse(fx, *, positive=(), verdict="VERIFIED", issues=()):
    return TargetClaimVerifierComposition.parse_candidate({"verdict": verdict,
        "positive_basis_refs": list(positive), "rationale": "Controlled semantic judgment",
        "issues": list(issues)}, invocation=fx.invocation, occurrence="verification://i5",
        receipt_ref="artifact://i5-response")[0]


@pytest.mark.parametrize("relation,proof,allowed", [
    ("SUPPORTED", "evidence", True), ("SUPPORTED", "other-evidence", False),
    ("PARTIALLY_SUPPORTED", "evidence-fact", True), ("PARTIALLY_SUPPORTED", "evidence-project", True),
    ("NOT_SUPPORTED", "fact", True), ("NOT_SUPPORTED", "project", True),
    ("NOT_SUPPORTED", "evidence", False), ("CONTRADICTED", "project", False),
    ("INDETERMINATE", "project", False), ("INDETERMINATE", "evidence", False),
])
def test_support_relation_matrix(tmp_path, relation, proof, allowed):
    fx = enriched_binding(fixture(tmp_path, support_relation=SupportRelation(relation)))
    refs = []
    if "evidence" in proof:
        refs.append("evidence:C1")
    if proof == "other-evidence":
        # Same legal Binding authority, but NOT the Support supporting subset.
        fx.support = fx.support.model_copy(update={"supporting_refs": ("evidence:another",)})
    if "fact" in proof:
        refs.append("fact:i5")
    if "project" in proof:
        refs.append(fx.invocation.verifier_project_input_basis.project_input_basis_ref)
    candidate = parse(fx, positive=sorted(refs))
    if allowed:
        fx.runtime.validate_v2_provenance(fx.binding, fx.support, candidate)
        result = fx.runtime.commit_verification(fx.claim.claim_ref, fx.binding.binding_facts_ref,
            fx.support.support_ref, candidate, None, fx.context)
        assert result.verification_result.verdict is VerificationVerdict.VERIFIED
    else:
        before = fx.runtime.ledger_path.read_bytes()
        with pytest.raises(ClaimBindingError):
            fx.runtime.validate_v2_provenance(fx.binding, fx.support, candidate)
        assert fx.runtime.ledger_path.read_bytes() == before


def test_binding_contradiction_cannot_be_bypassed_by_project_input(tmp_path):
    fx = fixture(tmp_path, binding_relation=ClaimBindingRelation.CONTRADICTS)
    candidate = parse(fx, positive=[fx.invocation.verifier_project_input_basis.project_input_basis_ref])
    with pytest.raises(ClaimBindingError, match="contradiction"):
        fx.runtime.commit_verification(fx.claim.claim_ref, fx.binding.binding_facts_ref,
            fx.support.support_ref, candidate, None, fx.context)


@pytest.mark.parametrize("verdict", ["CONFLICTED", "UNSUPPORTED", "REJECTED"])
@pytest.mark.parametrize("with_positive", [False, True])
def test_non_verified_mixed_positive_basis_is_preserved(tmp_path, verdict, with_positive):
    fx = fixture(tmp_path)
    refs = [fx.invocation.verifier_project_input_basis.project_input_basis_ref] if with_positive else []
    candidate = parse(fx, positive=refs, verdict=verdict)
    result = fx.runtime.commit_verification(fx.claim.claim_ref, fx.binding.binding_facts_ref,
        fx.support.support_ref, candidate, None, fx.context)
    assert result.verification_result.positive_basis_refs == tuple(refs)


@pytest.mark.parametrize("bad", ["empty", "duplicate", "unsorted", "unknown", "support", "package", "snapshot", "type"])
def test_invalid_positive_provenance_is_rejected(tmp_path, bad):
    fx = enriched_binding(fixture(tmp_path))
    pi = fx.invocation.verifier_project_input_basis.project_input_basis_ref
    refs = {"empty": [], "duplicate": [pi, pi], "unsorted": [pi, "fact:i5"],
        "unknown": ["unknown"], "support": [fx.support.support_ref.claim_evidence_support_id],
        "package": ["evidence_package:i5"], "snapshot": ["fact_snapshot:i5"], "type": "wrong-type"}[bad]
    with pytest.raises((ValueError, ClaimBindingError)):
        candidate = parse(fx, positive=refs)
        fx.runtime.validate_v2_provenance(fx.binding, fx.support, candidate)


@pytest.mark.parametrize("ref,allowed", [("project", True), ("fact:i5", True), ("evidence:C1", True),
    ("support", False), ("unknown", False)])
def test_issue_provenance_is_explicit_and_unmodified(tmp_path, ref, allowed):
    fx = enriched_binding(fixture(tmp_path))
    if ref == "project":
        ref = fx.invocation.verifier_project_input_basis.project_input_basis_ref
    elif ref == "support":
        ref = fx.support.support_ref.claim_evidence_support_id
    issue = {"issue_code": "I5", "message": "Explicit issue basis", "severity": "MAJOR", "source_refs": [ref]}
    if not allowed:
        with pytest.raises(ValueError):
            parse(fx, verdict="UNSUPPORTED", issues=[issue])
    else:
        candidate = parse(fx, verdict="UNSUPPORTED", issues=[issue])
        assert candidate.issue_candidates[0].source_refs == (ref,)
        result = fx.runtime.commit_verification(fx.claim.claim_ref, fx.binding.binding_facts_ref,
            fx.support.support_ref, candidate, None, fx.context)
        assert result.issue_records[0].source_refs == (ref,)


@pytest.mark.parametrize("defect", ["missing-source", "duplicate-source", "unsorted-source", "duplicate-issue", "extra-field"])
def test_parser_never_repairs_provider_provenance(tmp_path, defect):
    fx = fixture(tmp_path)
    issue = {"issue_code": "I5", "message": "Explicit provenance", "severity": "MAJOR", "source_refs": []}
    if defect == "missing-source":
        del issue["source_refs"]
    elif defect == "duplicate-source":
        issue["source_refs"] = ["evidence:C1", "evidence:C1"]
    elif defect == "unsorted-source":
        issue["source_refs"] = [fx.invocation.verifier_project_input_basis.project_input_basis_ref, "evidence:C1"]
    elif defect == "extra-field":
        issue["invented"] = "forbidden"
    with pytest.raises(ValueError):
        parse(fx, verdict="UNSUPPORTED", issues=[issue, issue] if defect == "duplicate-issue" else [issue])


def test_empty_issue_provenance_is_not_auto_filled(tmp_path):
    fx = fixture(tmp_path)
    candidate = parse(fx, verdict="UNSUPPORTED", issues=[{"issue_code": "I5", "message": "No refs declared",
        "severity": "MINOR", "source_refs": []}])
    assert candidate.issue_candidates[0].source_refs == ()


def test_contradicted_mixed_non_verified_evidence_preserves_positive_subset(tmp_path):
    fx = fixture(tmp_path)
    # A mechanical matrix probe: CONTRADICTED only adds the VERIFIED hard fence.
    # A supporting Evidence subset can still be declared on a mixed result.
    support = fx.support.model_copy(update={"relation": SupportRelation.CONTRADICTED,
        "contradicting_refs": ("evidence:other",)})
    candidate = parse(fx, verdict="CONFLICTED", positive=["evidence:C1"])
    fx.runtime.validate_v2_provenance(fx.binding, support, candidate)
