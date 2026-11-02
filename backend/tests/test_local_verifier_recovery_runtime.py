"""Phase 2 M6 journal/slot proof on real isolated production owners.

The identity-validator seam temporarily rebuilds strict recorded nested facts
until Phase 3 wires the production composition resolver. Source currentness,
Gateway/Budget, parser, provenance, authorizer, M6 journal and commit stay real.
"""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
import base64
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys

import pytest

from core.hashing import canonical_sha256
from schemas.model import ModelRequestSchema
from apps.enterprise_document.schemas.agent_rag_v1.verification import (
    VERIFIER_LOCAL_REGISTRATION_KEY, VERIFIER_LOCAL_CORRECTION_KEY,
    VerifierClaimInputSchema, VerifierSemanticResultCandidateSchema,
    VerifierOriginalRootV1, VerifierRecoveryArtifactChainV1,
    VerifierLocalCorrectionBindingV1, VerifierCandidateRejectedV1,
    build_verifier_local_correction_registration, build_verifier_original_root,
    build_verifier_correction_binding, build_verifier_semantic_input_sha256,
    build_verifier_registration_scope_sha256,
    build_verifier_correction_authorization, build_verifier_recovery_control_event,
)
from apps.enterprise_document.services.agent_rag_v1.claim_verification_runtime import (
    ClaimBindingError, ClaimCurrentnessError, ClaimHeadConflict,
    ClaimVerificationIntegrityError, ClaimVerificationRuntime,
    VerifierCandidateContractViolationError,
)
from apps.enterprise_document.schemas.agent_rag_v1.lifecycle import RunLifecycleStatus
from backend.tests.local_verifier_recovery_helpers import (
    create_fixture, reopen_fixture, legal_reply, provider_rows,
)


def _phase2_identity(fx):
    """Configured read adapter: exact native Writer and strict recorded facts."""
    def validate(claim, binding, support, material, lineage):
        expected, _project = fx.composer.build_input(claim, binding, support,
            fx.context.model_copy(update={"active_lineage": lineage}))
        facts = dict(expected.deterministic_validation_facts)
        facts.pop(VERIFIER_LOCAL_REGISTRATION_KEY, None)
        facts.pop(VERIFIER_LOCAL_CORRECTION_KEY, None)
        actual = material.deterministic_validation_facts
        if VERIFIER_LOCAL_REGISTRATION_KEY in actual:
            facts[VERIFIER_LOCAL_REGISTRATION_KEY] = build_verifier_local_correction_registration(expected).model_dump(mode="json")
        if VERIFIER_LOCAL_CORRECTION_KEY in actual:
            recorded = VerifierLocalCorrectionBindingV1.model_validate(actual[VERIFIER_LOCAL_CORRECTION_KEY])
            facts[VERIFIER_LOCAL_CORRECTION_KEY] = recorded.model_dump(mode="json")
        data = expected.model_dump(mode="json")
        data["deterministic_validation_facts"] = facts
        data["verifier_semantic_input_sha256"] = build_verifier_semantic_input_sha256(data)
        rebuilt = VerifierClaimInputSchema.model_validate(data)
        if fx.runtime.verifier_input_from_candidate(material) != rebuilt:
            raise ValueError("Phase 2 exact native source/Writer/recorded identity mismatch")
    fx.runtime.verification_identity_validator = validate
    return fx


def _record_call(fx, invocation, material, reply):
    """Actual Gateway call, then immutable production-form receipts."""
    key = canonical_sha256({"contract": "verifier-physical-occurrence-v2",
        "verifier_semantic_input_sha256": invocation.verifier_semantic_input_sha256})
    occurrence = "claim-verification-v2://sha256/" + key
    prefix = f"artifact://canonical-verifier-v2/{key}"
    def validate_material():
        _current_input, current = fx.composer._material(fx.claim, fx.binding, fx.support, fx.context)
        current = {**current, "invocation": invocation.model_dump(mode="json")}
        if "correction_feedback" in material:
            current["correction_feedback"] = material["correction_feedback"]
        if current != material:
            raise ValueError("Phase 2 complete native model materials changed before dispatch")
        fx.runtime.verification_identity_validator(fx.claim, fx.binding, fx.support, invocation, fx.context.active_lineage)
    package, limit = fx.composer._package(material, occurrence=occurrence,
        invocation=invocation, validate=validate_material)
    identity = fx.runtime._verifier_artifact_identity(invocation)
    request = ModelRequestSchema(model_call_id="model_call_verifier_" + key,
        task_id=identity["task_id"], run_id=identity["run_id"], caller_agent="VerifierAgent",
        model_role="general", prompt="", max_tokens=limit, temperature=0,
        messages=package.model_input.final_input_material.inline_final_input_payload["messages"],
        created_at="2026-10-04T08:00:00+00:00", extra={"call_purpose": "claim_verification",
            "logical_operation_id": occurrence, "section_id": identity["section_id"],
            "attempt_id": invocation.active_lineage.attempt_id,
            "attempt_scope_ref": invocation.active_lineage.attempt_scope_ref,
            "input_snapshot_version": invocation.active_lineage.input_snapshot_version,
            "llm_context_summary": {"managed": True, "package_id": package.package_ref.context_package_id,
                "context_sha256": package.model_input.final_model_input_sha256}})
    request_data = request.model_dump(mode="json")
    store = fx.composer.store
    store.put_json(request_data, artifact_ref=prefix + "/request", artifact_type="canonical_verifier_request",
        schema_version="canonical_verifier_request_v1", created_by="phase2_real_gateway", **identity)
    store.put_json({"schema_version": "canonical_verifier_input_v2", "input_material": material,
        "input_fingerprint": invocation.verifier_semantic_input_sha256, "request_ref": prefix + "/request",
        "request": request_data, "context_package": package.model_dump(mode="json")},
        artifact_ref=prefix + "/input", artifact_type="canonical_verifier_input",
        schema_version="canonical_verifier_input_v2", created_by="phase2_real_gateway", **identity)
    fx.client.script.append(reply)
    fx.runtime.validate_operation_context(fx.context)
    validate_material()
    response = fx.gateway.generate(request)
    assert response.success
    response_data = response.model_dump(mode="json")
    store.put_json({"schema_version": "canonical_verifier_response_v1",
        "input_fingerprint": invocation.verifier_semantic_input_sha256, "request_ref": prefix + "/request",
        "request_sha256": canonical_sha256(request_data), "response": response_data,
        "budget_settlement_confirmed": True}, artifact_ref=prefix + "/response",
        artifact_type="canonical_verifier_response", schema_version="canonical_verifier_response_v1",
        created_by="phase2_real_gateway", **identity)
    normalized, entry_metadata = fx.composer.normalize_reply_reference_order(response.content)
    entry = fx.composer._initial_reply_entry(response.content)
    entry.update(entry_metadata)
    entry.update(normalized_reply=normalized, normalization_status="completed")
    candidate, semantic = fx.composer._parse_candidate_strict(normalized, invocation=invocation,
        occurrence=occurrence, receipt_ref=prefix + "/candidate")
    store.put_json({"schema_version": "canonical_verifier_candidate_v2",
        "input_fingerprint": invocation.verifier_semantic_input_sha256, "outcome": "completed",
        "candidate": candidate.model_dump(mode="json"), "semantic_result": semantic,
        "reply_entry": entry, "reply_entry_available": True}, artifact_ref=prefix + "/candidate",
        artifact_type="canonical_verifier_candidate", schema_version="canonical_verifier_candidate_v2",
        created_by="phase2_real_gateway", **identity)
    chain = VerifierRecoveryArtifactChainV1(input_ref=prefix + "/input",
        input_sha256=canonical_sha256(invocation.model_dump(mode="json")),
        request_ref=prefix + "/request", request_sha256=canonical_sha256(request_data),
        response_ref=prefix + "/response", response_sha256=canonical_sha256(response_data),
        candidate_ref=prefix + "/candidate", candidate_sha256=canonical_sha256(candidate.model_dump(mode="json")),
        physical_occurrence=occurrence)
    return candidate, chain


def prepared(root, *, verdict="VERIFIED", illegal=True):
    fx = _phase2_identity(create_fixture(root))
    base, material = fx.composer._material(fx.claim, fx.binding, fx.support, fx.context)
    data = base.model_dump(mode="json")
    facts = dict(data["deterministic_validation_facts"])
    facts[VERIFIER_LOCAL_REGISTRATION_KEY] = build_verifier_local_correction_registration(base).model_dump(mode="json")
    data["deterministic_validation_facts"] = facts
    data["verifier_semantic_input_sha256"] = build_verifier_semantic_input_sha256(data)
    invocation = VerifierClaimInputSchema.model_validate(data)
    material = {**material, "invocation": invocation.model_dump(mode="json")}
    refs = list(fx.support.evidence_target_set_ref.evidence_member_refs) if illegal else []
    candidate, chain = _record_call(fx, invocation, material, legal_reply(verdict, refs))
    root_value = build_verifier_original_root(invocation)
    state = {"root": root_value.model_dump(mode="json"), "invocation": invocation.model_dump(mode="json"),
        "candidate": candidate.model_dump(mode="json"), "chain": chain.model_dump(mode="json")}
    (Path(root) / "phase2-state.json").write_text(json.dumps(state, ensure_ascii=False), encoding="utf-8")
    return fx, root_value, invocation, candidate, chain


def _reopen_prepared(root):
    fx = _phase2_identity(reopen_fixture(root))
    state = json.loads((Path(root) / "phase2-state.json").read_text(encoding="utf-8"))
    return fx, VerifierOriginalRootV1.model_validate(state["root"]), VerifierClaimInputSchema.model_validate(state["invocation"]), \
        VerifierSemanticResultCandidateSchema.model_validate(state["candidate"]), VerifierRecoveryArtifactChainV1.model_validate(state["chain"])


def rejection(fx, root, invocation, candidate, chain):
    return fx.runtime.record_verifier_candidate_rejection(root, invocation, candidate, chain, fx.context)


def _child(fx, root, grant, *, illegal=True):
    data = root.original_input.model_dump(mode="json")
    facts = dict(data["deterministic_validation_facts"])
    facts.pop(VERIFIER_LOCAL_REGISTRATION_KEY)
    facts[VERIFIER_LOCAL_CORRECTION_KEY] = build_verifier_correction_binding(grant).model_dump(mode="json")
    data["deterministic_validation_facts"] = facts
    data["verifier_semantic_input_sha256"] = build_verifier_semantic_input_sha256(data)
    invocation = VerifierClaimInputSchema.model_validate(data)
    _base, material = fx.composer._material(fx.claim, fx.binding, fx.support, fx.context)
    material = {**material, "invocation": invocation.model_dump(mode="json"), "correction_feedback": grant.feedback}
    fx.runtime.validate_verifier_correction_dispatch(invocation, fx.context)
    reply = legal_reply("VERIFIED", list(fx.support.evidence_target_set_ref.evidence_member_refs)) if illegal else legal_reply()
    candidate, chain = _record_call(fx, invocation, material, reply)
    return invocation, candidate, chain


def test_unchanged_guard_returns_exact_typed_evidence(tmp_path):
    fx, root, invocation, candidate, chain = prepared(tmp_path)
    with pytest.raises(VerifierCandidateContractViolationError) as caught:
        fx.runtime.commit_verification(fx.claim.claim_ref, fx.binding.binding_facts_ref,
            fx.support.support_ref, candidate, None, fx.context)
    assert caught.value.rule_code == "EVIDENCE_POSITIVE_BASIS_NOT_ADMITTED"
    assert caught.value.violation.original_input_sha256 == chain.input_sha256
    assert set(caught.value.violation.violating_refs) == set(candidate.positive_basis_refs) - set(fx.support.supporting_refs)
    assert candidate.positive_basis_refs == fx.support.evidence_target_set_ref.evidence_member_refs
    assert fx.runtime.resolve_claim_head(fx.claim.claim_ref) is None


def test_m6_rejection_grant_reopen_quota_and_original_bytes(tmp_path):
    fx, root, invocation, candidate, chain = prepared(tmp_path)
    original = {ref: fx.composer.store.get_bytes(ref, **fx.runtime._verifier_artifact_identity(invocation))
        for ref in (chain.input_ref, chain.request_ref, chain.response_ref, chain.candidate_ref)}
    rejected = rejection(fx, root, invocation, candidate, chain)
    grant = fx.runtime.authorize_verifier_correction(rejected, fx.context)
    reopened, *_ = _reopen_prepared(tmp_path)
    before = reopened.runtime.ledger_path.read_bytes()
    assert reopened.runtime.authorize_verifier_correction(rejected, reopened.context) == grant
    assert reopened.runtime.ledger_path.read_bytes() == before
    projection = reopened.runtime.resolve_verifier_local_recovery(root)
    assert projection["authorization"] == grant and projection["rejections"] == (rejected,)
    assert projection["termination"] is None
    assert len({item["operation_id"] for item in map(json.loads, before.decode().splitlines()) if "operation_id" in item}) == 2
    assert original == {ref: fx.composer.store.get_bytes(ref, **fx.runtime._verifier_artifact_identity(invocation)) for ref in original}
    assert len(provider_rows(fx)) == 1


@pytest.mark.parametrize("verdict", ["UNSUPPORTED", "CONFLICTED", "REJECTED"])
def test_legal_negative_never_becomes_m6_rejection(tmp_path, verdict):
    fx, root, invocation, candidate, chain = prepared(tmp_path, verdict=verdict, illegal=False)
    before = fx.runtime.ledger_path.read_bytes()
    with pytest.raises(ClaimBindingError, match="not eligible"):
        rejection(fx, root, invocation, candidate, chain)
    assert fx.runtime.ledger_path.read_bytes() == before
    result = fx.runtime.commit_verification(fx.claim.claim_ref, fx.binding.binding_facts_ref,
        fx.support.support_ref, candidate, None, fx.context)
    assert result.verification_result.verdict.value == verdict
    with pytest.raises(ClaimHeadConflict):
        rejection(fx, root, invocation, candidate, chain)
    assert len(provider_rows(fx)) == 1


def test_second_rejection_termination_permanently_fences_all_mutation_entries(tmp_path):
    fx, root, invocation, candidate, chain = prepared(tmp_path)
    initial = rejection(fx, root, invocation, candidate, chain)
    grant = fx.runtime.authorize_verifier_correction(initial, fx.context)
    child_input, child_candidate, child_chain = _child(fx, root, grant)
    second = rejection(fx, root, child_input, child_candidate, child_chain)
    terminal = fx.runtime.terminate_verifier_correction(root, "SECOND_CONTRACT_VIOLATION", fx.context, second)
    reopened, *_ = _reopen_prepared(tmp_path)
    before = reopened.runtime.ledger_path.read_bytes()
    entries = [
        lambda: reopened.runtime.authorize_verifier_correction(initial, reopened.context),
        lambda: reopened.runtime.validate_verifier_correction_dispatch(child_input, reopened.context),
        lambda: rejection(reopened, root, invocation, candidate, chain),
        lambda: reopened.runtime.commit_verification(fx.claim.claim_ref, fx.binding.binding_facts_ref,
            fx.support.support_ref, candidate, None, reopened.context),
        lambda: reopened.runtime.commit_verification(fx.claim.claim_ref, fx.binding.binding_facts_ref,
            fx.support.support_ref, child_candidate, None, reopened.context),
    ]
    for entry in entries:
        with pytest.raises(ClaimBindingError, match="TERMINATED"):
            entry()
    assert reopened.runtime.resolve_verifier_local_recovery(root)["termination"] == terminal
    assert reopened.runtime.ledger_path.read_bytes() == before
    assert len(provider_rows(fx)) == 2


def test_corrected_candidate_formal_commit_and_exact_replay_once(tmp_path):
    fx, root, invocation, candidate, chain = prepared(tmp_path)
    initial = rejection(fx, root, invocation, candidate, chain)
    grant = fx.runtime.authorize_verifier_correction(initial, fx.context)
    child_input, child_candidate, child_chain = _child(fx, root, grant, illegal=False)
    assert fx.runtime.validate_verifier_correction_dispatch(child_input, fx.context) == grant
    first = fx.runtime.commit_verification(fx.claim.claim_ref, fx.binding.binding_facts_ref,
        fx.support.support_ref, child_candidate, None, fx.context)
    before = fx.runtime.ledger_path.read_bytes()
    reopened, *_ = _reopen_prepared(tmp_path)
    replay = reopened.runtime.commit_verification(fx.claim.claim_ref, fx.binding.binding_facts_ref,
        fx.support.support_ref, child_candidate, None, reopened.context)
    assert replay == first and reopened.runtime.ledger_path.read_bytes() == before
    assert first.resulting_claim_head.head_revision == 1
    assert sum(json.loads(line).get("event_type") == "verification_committed" for line in before.decode().splitlines()) == 1


def _grant_worker(root):
    fx, root_value, *_ = _reopen_prepared(root)
    initial = fx.runtime.resolve_verifier_local_recovery(root_value)["rejections"][0]
    grant = fx.runtime.authorize_verifier_correction(initial, fx.context)
    print(grant.authorization_ref)


def test_real_m6_cross_process_fixed_slot_only_one_append(tmp_path):
    fx, root, invocation, candidate, chain = prepared(tmp_path)
    rejection(fx, root, invocation, candidate, chain)
    script = "from backend.tests.test_local_verifier_recovery_runtime import _grant_worker; import sys; _grant_worker(sys.argv[1])"
    processes = [subprocess.Popen([sys.executable, "-c", script, str(tmp_path)], stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT, text=True, env=os.environ.copy()) for _ in range(2)]
    outputs = []
    for index, process in enumerate(processes):
        output, _ = process.communicate(timeout=120)
        (tmp_path / f"phase2-worker-{index}.log").write_text(output, encoding="utf-8")
        assert process.returncode == 0, output
        outputs.append(output.strip())
    assert outputs[0] == outputs[1]
    projection = fx.runtime.resolve_verifier_local_recovery(root)
    assert projection["authorization"] is not None
    rows = [json.loads(line) for line in fx.runtime.ledger_path.read_text(encoding="utf-8").splitlines()]
    assert sum(row.get("event_type") == "verifier_correction_authorized" for row in rows) == 1
    assert len(provider_rows(fx)) == 1


def test_real_m6_independent_thread_owners_share_permanent_slot(tmp_path):
    fx, root, invocation, candidate, chain = prepared(tmp_path)
    initial = rejection(fx, root, invocation, candidate, chain)
    def grant(_index):
        reopened, *_ = _reopen_prepared(tmp_path)
        return reopened.runtime.authorize_verifier_correction(initial, reopened.context)
    with ThreadPoolExecutor(max_workers=4) as pool:
        grants = tuple(pool.map(grant, range(4)))
    assert all(item == grants[0] for item in grants)
    rows = [json.loads(line) for line in fx.runtime.ledger_path.read_text(encoding="utf-8").splitlines()]
    assert sum(row.get("event_type") == "verifier_correction_authorized" for row in rows) == 1


@pytest.mark.parametrize("defect", ["unknown-schema", "unknown-type", "root", "parent", "hash", "policy", "auth", "feedback", "ordinal", "duplicate", "extra"])
def test_strict_control_loader_rejects_tamper_and_duplicate(tmp_path, defect):
    fx, root, invocation, candidate, chain = prepared(tmp_path)
    initial = rejection(fx, root, invocation, candidate, chain)
    fx.runtime.authorize_verifier_correction(initial, fx.context)
    lines = fx.runtime.ledger_path.read_text(encoding="utf-8").splitlines()
    event = json.loads(lines[-1])
    if defect == "unknown-schema":
        event["schema_version"] = "claim_verifier_recovery_control_event_v99"
    elif defect == "unknown-type":
        event["event_type"] = "verifier_correction_reactivated"
    elif defect == "root":
        event["payload"]["root"]["original_root_ref"] += "tamper"
    elif defect == "parent":
        event["payload"]["rejection_ref"] += "tamper"
    elif defect == "hash":
        event["payload"]["grant_sha256"] = "f" * 64
    elif defect == "policy":
        event["payload"]["policy"]["max_additional_calls"] = 2
    elif defect == "auth":
        event["payload"]["authorization_ref"] += "tamper"
    elif defect == "feedback":
        event["payload"]["feedback"] += "tamper"
    elif defect == "ordinal":
        event["payload"]["correction_ordinal"] = 2
    elif defect == "extra":
        event["random_id"] = "new-slot"
    if defect == "duplicate":
        lines.append(lines[-1])
    else:
        lines[-1] = json.dumps(event)
    corrupt_path = tmp_path / f"corrupt-{defect}.jsonl"
    corrupt_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    corrupt = ClaimVerificationRuntime(paragraph_runtime=fx.paragraph,
        binding_source_validator=fx.sources.validate_binding_sources,
        support_resolver=fx.support_composition.runtime.resolve_support,
        verification_source_validator=fx.composer.validate_verification_sources,
        ledger_path=corrupt_path)
    with pytest.raises(ClaimVerificationIntegrityError):
        corrupt.resolve_verifier_local_recovery(root)


@pytest.mark.parametrize("stage", ["grant", "dispatch"])
def test_native_lifecycle_fence_blocks_grant_and_dispatch(tmp_path, stage):
    fx, root, invocation, candidate, chain = prepared(tmp_path)
    initial = rejection(fx, root, invocation, candidate, chain)
    if stage == "dispatch":
        grant = fx.runtime.authorize_verifier_correction(initial, fx.context)
        data = root.original_input.model_dump(mode="json")
        data["deterministic_validation_facts"].pop(VERIFIER_LOCAL_REGISTRATION_KEY)
        data["deterministic_validation_facts"][VERIFIER_LOCAL_CORRECTION_KEY] = build_verifier_correction_binding(grant).model_dump(mode="json")
        data["verifier_semantic_input_sha256"] = build_verifier_semantic_input_sha256(data)
        child = VerifierClaimInputSchema.model_validate(data)
    current = fx.lifecycle.load_run_lifecycle(fx.context.active_lineage.run_id)
    fx.lifecycle.transition_run(current.identity.run_id, current.lifecycle_revision, RunLifecycleStatus.CANCELLED)
    before = fx.runtime.ledger_path.read_bytes()
    with pytest.raises(ValueError):
        if stage == "grant":
            fx.runtime.authorize_verifier_correction(initial, fx.context)
        else:
            fx.runtime.validate_verifier_correction_dispatch(child, fx.context)
    assert fx.runtime.ledger_path.read_bytes() == before
    assert len(provider_rows(fx)) == 1


def test_m6_prevents_repaired_original_candidate_commit(tmp_path):
    fx, root, invocation, candidate, chain = prepared(tmp_path)
    rejection(fx, root, invocation, candidate, chain)
    repaired = candidate.model_copy(update={"positive_basis_refs": fx.support.supporting_refs})
    with pytest.raises(ClaimBindingError, match="cannot be repaired"):
        fx.runtime.commit_verification(fx.claim.claim_ref, fx.binding.binding_facts_ref,
            fx.support.support_ref, repaired, None, fx.context)
    assert fx.runtime.resolve_claim_head(fx.claim.claim_ref) is None


def _rewrite_attack_fixture_artifact(fx, ref, mutate, *, metadata_type=None):
    """Deliberate durable corruption only in a new isolated attack fixture.

    Preserve the original envelope before injecting the attack. This never
    touches historical Attempts or the successful immutability proof fixture.
    """
    path = fx.composer.store._path_for_ref(ref)
    original = path.read_bytes()
    path.with_name(path.name + ".before-attack").write_bytes(original)
    envelope = json.loads(original)
    content = json.loads(base64.b64decode(envelope["content_base64"]))
    mutate(content)
    serialized = json.dumps(content, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    envelope["content_base64"] = base64.b64encode(serialized).decode("ascii")
    envelope["reference"]["content_hash"] = "sha256:" + hashlib.sha256(serialized).hexdigest()
    envelope["reference"]["size_bytes"] = len(serialized)
    if metadata_type:
        envelope["reference"]["artifact_type"] = metadata_type
    path.write_text(json.dumps(envelope, ensure_ascii=False, sort_keys=True) + "\n", encoding="utf-8")


@pytest.mark.parametrize("defect", ["messages", "output-cap", "context-summary", "metadata-type", "settlement-unconfirmed", "different-response"])
def test_m6_strict_durable_context_metadata_and_response_linkage(tmp_path, defect):
    fx, root, invocation, candidate, chain = prepared(tmp_path)
    identity = fx.runtime._verifier_artifact_identity(invocation)
    if defect in {"messages", "output-cap", "context-summary"}:
        request = fx.composer.store.get_json(chain.request_ref, **identity)
        if defect == "messages":
            request["messages"][0]["content"] += " tampered request instructions"
        elif defect == "output-cap":
            request["max_tokens"] += 1
        else:
            request["extra"]["llm_context_summary"]["context_sha256"] = "f" * 64
        _rewrite_attack_fixture_artifact(fx, chain.request_ref, lambda data: (data.clear(), data.update(request)))
        _rewrite_attack_fixture_artifact(fx, chain.input_ref, lambda data: data.update(request=request))
        _rewrite_attack_fixture_artifact(fx, chain.response_ref, lambda data: data.update(request_sha256=canonical_sha256(request)))
        chain = chain.model_copy(update={"request_sha256": canonical_sha256(request)})
    elif defect == "metadata-type":
        _rewrite_attack_fixture_artifact(fx, chain.input_ref, lambda data: None, metadata_type="hidden_initial_intent")
    elif defect == "settlement-unconfirmed":
        _rewrite_attack_fixture_artifact(fx, chain.response_ref, lambda data: data.update(budget_settlement_confirmed=False))
    else:
        response = fx.composer.store.get_json(chain.response_ref, **identity)["response"]
        response["content"] = json.dumps(legal_reply())
        _rewrite_attack_fixture_artifact(fx, chain.response_ref, lambda data: data.update(response=response))
        chain = chain.model_copy(update={"response_sha256": canonical_sha256(response)})
    before = fx.runtime.ledger_path.read_bytes()
    with pytest.raises(ClaimVerificationIntegrityError):
        rejection(fx, root, invocation, candidate, chain)
    assert fx.runtime.ledger_path.read_bytes() == before
    assert len(provider_rows(fx)) == 1


def test_loader_rejects_rehashed_feedback_without_a_second_slot(tmp_path):
    fx, root, invocation, candidate, chain = prepared(tmp_path)
    initial = rejection(fx, root, invocation, candidate, chain)
    grant = fx.runtime.authorize_verifier_correction(initial, fx.context)
    changed = build_verifier_correction_authorization(root=root, rejection_ref=initial.rejection_ref,
        rejection_sha256=initial.rejection_sha256, feedback=grant.feedback + "tampered feedback")
    event = build_verifier_recovery_control_event(changed)
    assert event.operation_id == build_verifier_recovery_control_event(grant).operation_id
    lines = fx.runtime.ledger_path.read_text(encoding="utf-8").splitlines()
    lines[-1] = json.dumps(event.model_dump(mode="json"))
    corrupt_path = tmp_path / "self-consistent-feedback-corruption.jsonl"
    corrupt_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    corrupt = ClaimVerificationRuntime(paragraph_runtime=fx.paragraph,
        binding_source_validator=fx.sources.validate_binding_sources,
        support_resolver=fx.support_composition.runtime.resolve_support,
        verification_source_validator=fx.composer.validate_verification_sources,
        ledger_path=corrupt_path)
    with pytest.raises(ClaimVerificationIntegrityError, match="feedback"):
        corrupt.resolve_verifier_local_recovery(root)


def test_m6_scope_arbitration_prevents_authorization_changed_root(tmp_path):
    fx, root, invocation, candidate, chain = prepared(tmp_path)
    initial = rejection(fx, root, invocation, candidate, chain)
    fx.runtime.authorize_verifier_correction(initial, fx.context)
    data = invocation.model_dump(mode="json")
    data["verification_authorization_ref"] += "/a-new-authorization"
    data["verifier_semantic_input_sha256"] = build_verifier_semantic_input_sha256(data)
    different = VerifierClaimInputSchema.model_validate(data)
    another_root = build_verifier_original_root(different)
    assert build_verifier_registration_scope_sha256(different) == build_verifier_registration_scope_sha256(invocation)
    before = fx.runtime.ledger_path.read_bytes()
    with pytest.raises(ClaimVerificationIntegrityError, match="different root"):
        rejection(fx, another_root, invocation, candidate, chain)
    assert fx.runtime.ledger_path.read_bytes() == before
