"""V2 payload clean cut with V1 control skeleton and exact active currentness."""
import json
from unittest.mock import patch

import pytest

from apps.enterprise_document.schemas.agent_rag_v1.verification import (
    VerifierSemanticResultCandidateSchema, build_verifier_semantic_input_sha256,
)
from apps.enterprise_document.services.agent_rag_v1.claim_verification_runtime import (
    ClaimVerificationIntegrityError, ClaimIdentityCollision, ClaimCurrentnessError,
)
from core.runtime.pre_write_execution_store import SQLitePreWriteExecutionStore
from backend.tests.test_bca014_i5_verifier_project_input_basis import fixture, replace_writer
from backend.tests.test_bca014_i5_verifier_v2_semantics import parse
from backend.tests.test_bca014_finalizing_read_current import canonical_section


def commit(fx, candidate=None, head=None):
    candidate = candidate or parse(fx, positive=[fx.invocation.verifier_project_input_basis.project_input_basis_ref])
    return fx.runtime.commit_verification(fx.claim.claim_ref, fx.binding.binding_facts_ref,
        fx.support.support_ref, candidate, head, fx.context)


def test_fresh_v2_replay_and_v1_control_skeleton(tmp_path):
    fx = fixture(tmp_path)
    first = commit(fx)
    before = fx.runtime.ledger_path.read_bytes()
    assert commit(fx) == first
    assert fx.runtime.ledger_path.read_bytes() == before
    result = first.verification_result
    assert result.schema_version == "verification_result_record_v2"
    assert fx.runtime.resolve_verification_result(result.verification_result_ref) == result
    assert result.verification_result_ref.schema_version == "verification_result_ref_v1"
    assert first.verification_revision.schema_version == "claim_verification_revision_record_v1"
    assert first.verification_revision.revision_ref.schema_version == "claim_verification_revision_ref_v1"
    assert first.resulting_claim_head.schema_version == "claim_current_head_v1"
    assert first.receipt.schema_version == "claim_verification_commit_receipt_v1"
    assert fx.claim.schema_version == "claim_record_v1"
    assert fx.binding.schema_version == "claim_binding_facts_record_v1"
    data = json.loads(before.decode().splitlines()[-1])
    assert data["schema_version"] == "claim_verification_event_v1"
    for payload in (data["verification_result"], data["verifier_candidate"]):
        assert "project_input" not in payload
        assert "unified_writer_input_snapshot" not in json.dumps(payload)
    assert result.binding_source_set_hash == fx.binding.source_set_hash
    assert fx.runtime.resolve_active_verified_claim(fx.claim.claim_ref).active_verified_claim


@pytest.mark.parametrize("version", ["verification_result_record_v1", "verification_result_record_v999", None])
def test_active_old_unknown_or_missing_result_version_fails_closed(tmp_path, version):
    fx = fixture(tmp_path)
    commit(fx)
    events = [json.loads(line) for line in fx.runtime.ledger_path.read_text().splitlines()]
    if version is None:
        del events[-1]["verification_result"]["schema_version"]
    else:
        events[-1]["verification_result"]["schema_version"] = version
    fx.runtime.ledger_path.write_text("\n".join(json.dumps(i) for i in events) + "\n", encoding="utf-8")
    with pytest.raises(ClaimVerificationIntegrityError, match="requires Result V2"):
        fx.runtime.resolve_claim_head(fx.claim.claim_ref)


def test_mixed_result_history_fails_closed(tmp_path):
    fx = fixture(tmp_path)
    first = commit(fx)
    # Append a second legal V2 verification before introducing an old-version
    # history fixture. This is a corrupt-ledger test, not a currentness test.
    fx.context = fx.context.model_copy(update={"authorization_ref": "verification://i5-second"})
    fx.invocation, _ = fx.composer.build_input(fx.claim, fx.binding, fx.support, fx.context)
    commit(fx, head=first.resulting_claim_head)
    events = [json.loads(line) for line in fx.runtime.ledger_path.read_text().splitlines()]
    results = [i for i in events if i["event_type"] == "verification_committed"]
    assert len(results) == 2
    results[0]["verification_result"]["schema_version"] = "verification_result_record_v1"
    fx.runtime.ledger_path.write_text("\n".join(json.dumps(i) for i in events) + "\n", encoding="utf-8")
    with pytest.raises(ClaimVerificationIntegrityError, match="requires Result V2"):
        fx.runtime.resolve_claim_head(fx.claim.claim_ref)


def test_same_operation_changed_semantic_input_conflicts(tmp_path):
    fx = fixture(tmp_path)
    first_candidate = parse(fx, positive=[fx.invocation.verifier_project_input_basis.project_input_basis_ref])
    operation = fx.runtime._verification_operation(claim_record=fx.claim, binding_record=fx.binding,
        support_record=fx.support, verifier_candidate=first_candidate, context=fx.context)
    commit(fx, first_candidate)
    data = first_candidate.model_dump(mode="json")
    data["response_contract"] = "different-response-contract"
    data["verifier_semantic_input_sha256"] = build_verifier_semantic_input_sha256(data)
    candidate = VerifierSemanticResultCandidateSchema.model_validate(data)
    assert fx.runtime._verification_operation(claim_record=fx.claim, binding_record=fx.binding,
        support_record=fx.support, verifier_candidate=candidate, context=fx.context) != operation
    with patch.object(fx.runtime, "_verification_operation", return_value=operation), pytest.raises(ClaimIdentityCollision):
        commit(fx, candidate)


@pytest.mark.parametrize("owner", ["writer", "project", "policy", "binding", "support", "paragraph"])
def test_active_verification_rechecks_current_owners(tmp_path, owner):
    fx = fixture(tmp_path)
    commit(fx)
    assert fx.runtime.resolve_active_verified_claim(fx.claim.claim_ref).active_verified_claim
    immutable = fx.runtime.ledger_path.read_bytes()
    if owner == "writer":
        fx.composer.writer_execution_store = SQLitePreWriteExecutionStore(tmp_path / "new-owner.db")
    elif owner == "project":
        fx.composer.writer_execution_store = replace_writer(fx, tmp_path / "new-owner.db",
            project=fx.project.model_copy(update={"total_staff": 13}))
    elif owner == "policy":
        fx.composer.POLICY_VERSION = "v2-current-owner-changed"
    elif owner == "support":
        fx.state["current"] = False
    elif owner == "binding":
        fx.runtime.binding_source_validator = lambda *args: (_ for _ in ()).throw(ValueError("active binding source stale"))
    else:
        from apps.enterprise_document.schemas.agent_rag_v1.paragraphs import ParagraphCandidateSchema
        paragraph = fx.runtime.paragraph_runtime
        base = paragraph.read_paragraph(fx.claim.paragraph_version_ref)
        operation = paragraph.revise_operation(run_id=fx.context.active_lineage.run_id,
            section_id=fx.claim.section_id, write_instance_id=fx.context.active_lineage.attempt_id,
            target_logical_ref=fx.claim.paragraph_version_ref.logical_ref,
            base_version_ref=fx.claim.paragraph_version_ref, authorization_ref="paragraph-repair:i5-currentness")
        candidate = ParagraphCandidateSchema(content_contract_version=base.content_contract_version,
            operation_ref=operation, source_binding=base.source_binding, role=base.role, text="Current paragraph replaced.")
        version = paragraph.commit_paragraph_version(candidate=candidate, active_lineage=fx.context.active_lineage)
        head = paragraph.artifact_runtime.resolve_active(fx.claim.paragraph_version_ref.logical_ref)
        paragraph.promote_paragraph_active(candidate=candidate, target_version_ref=version,
            expected_head_revision=head.head_revision, expected_active_version_id=fx.claim.paragraph_version_ref.artifact_version_id,
            active_lineage=fx.context.active_lineage)
    view = fx.runtime.resolve_active_verified_claim(fx.claim.claim_ref)
    assert not view.verification_current
    assert not view.active_verified_claim
    assert fx.runtime.ledger_path.read_bytes() == immutable


@pytest.mark.parametrize("spoof", ["basis", "authority", "semantic", "writer", "pi-hash"])
def test_commit_rejects_spoofed_v2_input(tmp_path, spoof):
    fx = fixture(tmp_path)
    candidate = parse(fx, positive=[fx.invocation.verifier_project_input_basis.project_input_basis_ref])
    data = candidate.model_dump(mode="json")
    if spoof in {"authority", "semantic"}:
        data["authority_universe_sha256" if spoof == "authority" else "verifier_semantic_input_sha256"] = "f" * 64
    else:
        basis = data["verifier_project_input_basis"]
        if spoof == "writer":
            basis["writer_generation_invocation_ref"]["invocation_sha256"] = "f" * 64
        else:
            basis["project_input_basis_ref" if spoof == "basis" else "project_input_sha256"] = "f" * 64
    before = fx.runtime.ledger_path.read_bytes()
    with pytest.raises((ValueError, ClaimCurrentnessError)):
        commit(fx, data)
    assert fx.runtime.ledger_path.read_bytes() == before


def test_target_startup_uses_one_writer_store_and_real_project_input(canonical_section):
    fx = canonical_section
    pw = fx["post_write"]
    projection = pw.claim_quality_projection
    assert projection.verifier.writer_execution_store is pw.writer_execution_store
    snapshot = projection.resolve_sources(post_write=pw,
        artifact_ref=fx["arguments"]["artifact_ref"], scope=fx["arguments"]["scope"],
        source_sections=fx["arguments"]["source_sections"], assembly_input_manifest_ref=None)
    for section in snapshot.sections:
        for claim, binding, support, result in zip(section.claim_records, section.binding_records,
                section.support_records, section.verification_records):
            lineage = projection.claim_runtime.lifecycle_service.resolve_read_lineage(
                claim.claim_occurrence_ref.run_id, "section:" + claim.section_id,
                expected_attempt_id=result.active_lineage.attempt_id)
            projection.verifier.validate_v2_identity(claim, binding, support, result, lineage)
            basis, project = projection.verifier.resolve_project_input_basis(claim, lineage)
            assert basis.project_input_sha256 == result.project_input_sha256
            assert project.task_id == "task-i2"


def test_derived_binding_projection_cannot_override_owner_source_kinds(tmp_path):
    fx = fixture(tmp_path)
    candidate = parse(fx, positive=[fx.invocation.verifier_project_input_basis.project_input_basis_ref])
    data = candidate.model_dump(mode="json")
    data["exact_relevant_source_refs"][0]["source_kind"] = "FACT"
    # Derived copies do not own semantic-input identity, but they must still
    # exactly equal the canonical Binding projection at commit.
    assert build_verifier_semantic_input_sha256(data) == candidate.verifier_semantic_input_sha256
    altered = VerifierSemanticResultCandidateSchema.model_validate(data)
    before = fx.runtime.ledger_path.read_bytes()
    with pytest.raises(ClaimCurrentnessError):
        commit(fx, altered)
    assert fx.runtime.ledger_path.read_bytes() == before
