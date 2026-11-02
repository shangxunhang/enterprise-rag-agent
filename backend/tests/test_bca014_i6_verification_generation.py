"""Scoped derived identity and canonical current Result V2 admission."""
from types import SimpleNamespace
from unittest.mock import patch
import pytest
from core.hashing import canonical_sha256
from apps.enterprise_document.schemas.post_write.verification_generation import (
    VerificationGenerationScopeRefV1 as Scope, VerificationGenerationEntryV1 as Entry,
    VerificationGenerationManifestV1 as Manifest,
)
from apps.enterprise_document.services.agent_rag_v1.post_write_claim_projection import (
    TargetPostWriteClaimProjection, PostWriteClaimProjectionError,
)
from apps.enterprise_document.schemas.agent_rag_v1.verification import ClaimLifecycleStatus
from backend.tests.test_bca014_i5_verifier_project_input_basis import fixture
from backend.tests.test_bca014_i5_verification_result_v2_ledger import commit
from backend.tests import test_multi_agent_v3_claim_verification_runtime as owners


def current(tmp_path, verdict="VERIFIED"):
    fx = fixture(tmp_path)
    from backend.tests.test_bca014_i5_verifier_v2_semantics import parse
    first = commit(fx, parse(fx, verdict=verdict, positive=[fx.invocation.verifier_project_input_basis.project_input_basis_ref]))
    projection = TargetPostWriteClaimProjection(claim_runtime=fx.runtime, verifier_composition=fx.composer)
    return fx, first, projection


def snapshot(fx, result):
    return SimpleNamespace(validation_scope=Scope(run_id="unused", validation_scope="SECTION", target_ref="unused").validation_scope,
        sections=(SimpleNamespace(section_id=fx.claim.section_id,
            paragraph_manifest=SimpleNamespace(members=(fx.claim.paragraph_version_ref,)),
            claim_records=(fx.claim,), verification_records=(result,)),))


def build(fx, projection, material):
    return projection.build_verification_generation(snapshot=material,
        run_id=fx.claim.claim_occurrence_ref.run_id, scope=material.validation_scope, target_ref=fx.claim.section_id)


@pytest.mark.parametrize("verdict", ["VERIFIED", "CONFLICTED", "UNSUPPORTED", "REJECTED"])
def test_all_current_verdicts_and_no_provider_or_write(tmp_path, verdict):
    fx, first, projection = current(tmp_path, verdict)
    before = fx.runtime.ledger_path.read_bytes()
    with patch.object(fx.runtime, "commit_verification", side_effect=AssertionError("no writes")), patch.object(fx.composer, "verify_claims", side_effect=AssertionError("no provider")):
        one = build(fx, projection, snapshot(fx, first.verification_result))
        assert one == build(fx, projection, snapshot(fx, first.verification_result))
    assert one.entries[0].claim_head_revision == first.resulting_claim_head.head_revision
    assert one.entries[0].verification_result_ref == first.verification_result.verification_result_ref
    assert fx.runtime.ledger_path.read_bytes() == before


def test_real_reverification_changes_generation_and_stale_result_rejected(tmp_path):
    fx, first, projection = current(tmp_path)
    old_snapshot = snapshot(fx, first.verification_result)
    one = build(fx, projection, old_snapshot)
    fx.context = fx.context.model_copy(update={"authorization_ref": "verification://i6-next"})
    fx.invocation, _ = fx.composer.build_input(fx.claim, fx.binding, fx.support, fx.context)
    second = commit(fx, head=first.resulting_claim_head)
    two = build(fx, projection, snapshot(fx, second.verification_result))
    assert one.verification_generation_sha256 != two.verification_generation_sha256
    assert two.entries[0].claim_head_revision == one.entries[0].claim_head_revision + 1
    with pytest.raises(PostWriteClaimProjectionError, match="complete current"):
        build(fx, projection, old_snapshot)


def test_inactive_stale_snapshot_and_missing_claim_fail_closed(tmp_path):
    fx, first, projection = current(tmp_path)
    material = snapshot(fx, first.verification_result)
    with patch.object(fx.runtime, "resolve_claim_head", return_value=first.resulting_claim_head.model_copy(update={"head_revision": 42})):
        with pytest.raises(PostWriteClaimProjectionError, match="not current"):
            build(fx, projection, material)
    material.sections[0].claim_records = ()
    material.sections[0].verification_records = ()
    with pytest.raises(PostWriteClaimProjectionError, match="complete current"):
        build(fx, projection, material)
    fx.runtime.transition_claim_lifecycle(fx.claim.claim_ref, first.resulting_claim_head.head_revision,
        ClaimLifecycleStatus.ACTIVE, ClaimLifecycleStatus.INVALIDATED, "i6-test-invalidated",
        owners._context(fx.env, authorization_ref="i6-lifecycle", purpose="claim-lifecycle"))
    assert not build(fx, projection, material).entries
    with pytest.raises(PostWriteClaimProjectionError, match="complete current"):
        build(fx, projection, snapshot(fx, first.verification_result))


def test_duplicate_snapshot_claim_rejected(tmp_path):
    fx, first, projection = current(tmp_path)
    material = snapshot(fx, first.verification_result)
    material.sections[0].claim_records *= 2
    with pytest.raises(PostWriteClaimProjectionError, match="duplicate"):
        build(fx, projection, material)


def test_i5_currentness_failure_rejected(tmp_path):
    fx, first, projection = current(tmp_path)
    fx.composer.POLICY_VERSION = "i6-stale-policy"
    with pytest.raises(PostWriteClaimProjectionError, match="not current"):
        build(fx, projection, snapshot(fx, first.verification_result))


def test_schema_canonical_order_set_result_and_aba(tmp_path):
    fx, first, projection = current(tmp_path)
    one = build(fx, projection, snapshot(fx, first.verification_result))
    scope = one.generation_scope_ref
    a = one.entries[0]
    # Exact Result V2 fixture; copies isolate derived identity sensitivity.
    other_ref = a.claim_ref.model_copy(update={"claim_id": a.claim_ref.claim_id + "-other"})
    b = Entry(claim_ref=other_ref, claim_head_revision=1,
        verification_result_ref=a.verification_result_ref.model_copy(update={"claim_ref": other_ref}))
    assert Manifest.build(scope, (a,b)) == Manifest.build(scope, (b,a))
    assert Manifest.build(scope, (a,b)).verification_generation_sha256 != one.verification_generation_sha256
    assert Manifest.build(scope).verification_generation_sha256 != one.verification_generation_sha256
    for modified in (a.model_copy(update={"claim_head_revision": a.claim_head_revision+1}),
            a.model_copy(update={"verification_result_ref": a.verification_result_ref.model_copy(update={"verification_result_id":"other-result"})})):
        assert Manifest.build(scope, (modified,)).verification_generation_sha256 != one.verification_generation_sha256
    with pytest.raises(ValueError, match="unique"):
        Manifest.build(scope, (a,a))
    data = Manifest.build(scope, (a,b)).model_dump(mode="json")
    data["entries"].reverse()
    data["verification_generation_sha256"] = canonical_sha256({k:v for k,v in data.items() if k != "verification_generation_sha256"})
    with pytest.raises(ValueError, match="sorted"):
        Manifest.model_validate(data)


@pytest.mark.parametrize("empty", [True, False])
def test_scope_identity_is_first_class(tmp_path, empty):
    fx, first, projection = current(tmp_path)
    one = build(fx, projection, snapshot(fx, first.verification_result))
    entries = () if empty else one.entries
    hashes = {Manifest.build(Scope(run_id=one.generation_scope_ref.run_id,
        validation_scope=scope, target_ref=target),entries).verification_generation_sha256
        for scope,target in (("SECTION","A"),("SECTION","B"),("DOCUMENT","A"),("DOCUMENT","B"))}
    assert len(hashes) == 4


def test_manifest_field_fence():
    assert set(Scope.model_fields) == {"run_id","validation_scope","target_ref"}
    assert set(Entry.model_fields) == {"claim_ref","claim_head_revision","verification_result_ref"}
    assert set(Manifest.model_fields) == {"generation_scope_ref","entries","generation_contract_version","verification_generation_sha256"}


def test_current_claim_without_head_cannot_be_silent_empty(tmp_path):
    fx=fixture(tmp_path)
    from apps.enterprise_document.services.agent_rag_v1.claim_verification_runtime import ClaimVerificationIntegrityError
    with pytest.raises(ClaimVerificationIntegrityError,match="no head"):
        fx.runtime.resolve_current_generation_sources(run_id=fx.claim.claim_occurrence_ref.run_id,
            section_id=fx.claim.section_id,paragraph_version_refs=(fx.claim.paragraph_version_ref,))


def test_legal_empty_canonical_claim_owner(tmp_path):
    from apps.enterprise_document.schemas.post_write.foundation import ValidationScope
    env, paragraphs, runtime, version, manifest, checks, state=owners._setup_claim_runtime(tmp_path)
    verifier=SimpleNamespace(claim_runtime=runtime,support=SimpleNamespace())
    projection=TargetPostWriteClaimProjection(claim_runtime=runtime,verifier_composition=verifier)
    material=SimpleNamespace(validation_scope=ValidationScope.SECTION,sections=(SimpleNamespace(
        section_id=manifest.section_id,paragraph_manifest=SimpleNamespace(members=(version,)),
        claim_records=(),verification_records=()),))
    empty=projection.build_verification_generation(snapshot=material,run_id=env["lineage"].run_id,
        scope=ValidationScope.SECTION,target_ref=manifest.section_id)
    assert empty.entries==()
    assert empty==projection.build_verification_generation(snapshot=material,run_id=env["lineage"].run_id,
        scope=ValidationScope.SECTION,target_ref=manifest.section_id)
