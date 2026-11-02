"""AO-007: offline native owners, preserved receipts, and legal local failures.

All mutable owners are created beneath pytest's fresh tmp_path.  These tests
reuse the ordinary Fake mainline helper, never the historical Guard evidence
writer or its E: admission root.  Model failures are fixed failed responses;
local Ref failures call the real Guard with a test-only missing operand map.
"""
from __future__ import annotations

from contextlib import ExitStack
from copy import deepcopy
import socket
from unittest.mock import patch
import uuid

import pytest

from application.terminal_outcome_projector import opaque_ref
from apps.enterprise_document.schemas.post_write.foundation import ValidationIntermediateRefSchema
from apps.enterprise_document.schemas.post_write.quality import (
    ConsistencyPlanConformanceResultSchema,
    FormalExecutabilityResultSchema,
)
from apps.enterprise_document.services.agent_rag_v1.post_write_consistency_composition import (
    CONSISTENCY_RELATION_CONTRACT,
    CONSISTENCY_SHARED_RELATION_CONTRACT,
    ConsistencyCompositionError,
    TargetPostWriteConsistency,
)
from apps.enterprise_document.services.post_write import consistency as formal
from apps.enterprise_document.services.post_write.persistence import PostWritePersistenceIntegrityError
from model_gateway.fake_llm_client import FakeLLMClient
from schemas.common import ErrorSchema
from schemas.failure import FailureCategory, FailureOrigin

import test_multi_agent_v3_consistency_mainline as native


SEMANTIC_CONTRACTS = {CONSISTENCY_RELATION_CONTRACT, CONSISTENCY_SHARED_RELATION_CONTRACT}
REJECTED = (ConsistencyCompositionError, PostWritePersistenceIntegrityError, ValueError, FileNotFoundError)


def _run_failure(root, *, scope, sections, cause="guard"):
    """Capture exact verification inputs without replacing the verifier."""
    calls, guards, verifications = [], [], []
    original_generate = FakeLLMClient.generate
    original_guard = formal.FormalExecutabilityGuard.evaluate
    original_discover = formal.ConsistencyCandidateDiscoveryExecutor.execute
    original_verify = TargetPostWriteConsistency._verify_accounting

    def fake(client, request):
        calls.append(request.model_copy(deep=True))
        response = original_generate(client, request)
        contract = request.extra.get("response_contract")
        selected = False
        if contract == CONSISTENCY_SHARED_RELATION_CONTRACT:
            selected = sections == 2 and scope in {"DOCUMENT", "DOCUMENT_GLOBAL"}
        elif contract == CONSISTENCY_RELATION_CONTRACT:
            selected = native._control(request)["validation_scope"] == scope
        if cause == "provider" and selected:
            error = ErrorSchema(error_code="AO007_FIXED_PROVIDER_UNAVAILABLE", error_type="FixedProviderFailure",
                message="Fixed offline model physical-call failure", failure_category=FailureCategory.AVAILABILITY,
                retryable=False, requires_human=True)
            return response.model_copy(update={"success": False, "content": "", "error": error,
                "error_message": error.message, "failure_origin": FailureOrigin.PROVIDER_RUNTIME}, deep=True)
        return response

    def guard(owner, *, binding, assertions_by_ref):
        before = len(calls)
        result = original_guard(owner, binding=binding,
            assertions_by_ref={} if cause == "guard" and binding.validation_scope == scope else assertions_by_ref)
        assert len(calls) == before
        guards.append((binding, result))
        return result

    def discover(owner, **arguments):
        # The fixed one-section fixture has required local members only for
        # SECTION.  Global plans identify themselves with the runtime input.
        plan = arguments["plan"]
        if cause == "plan" and scope == "SECTION" and plan.runtime_procedure_input_hash is None and plan.required_relation_keys:
            arguments = {**arguments, "complete_required_plan_accounting": False}
        return original_discover(owner, **arguments)

    def verify(owner, accounting, material, identity, **arguments):
        result = original_verify(owner, accounting, material, identity, **arguments)
        verifications.append((owner, deepcopy(accounting.model_dump(mode="json")),
            deepcopy(material), deepcopy(identity), deepcopy(arguments)))
        return result

    with ExitStack() as stack:
        stack.enter_context(patch.object(socket.socket, "connect", side_effect=AssertionError("AO-007 Fake attempted network")))
        stack.enter_context(patch.object(FakeLLMClient, "generate", fake))
        stack.enter_context(patch.object(formal.FormalExecutabilityGuard, "evaluate", guard))
        stack.enter_context(patch.object(formal.ConsistencyCandidateDiscoveryExecutor, "execute", discover))
        stack.enter_context(patch.object(TargetPostWriteConsistency, "_verify_accounting", verify))
        outcome = native._run(root, "FORMAL_EQ", sections=sections, repeats=1,
            body_padding="本节按既定工作流程逐步展开说明。" * 12,
            global_formal=True, identity_suffix="-ao007-" + uuid.uuid4().hex)
    assert len([request for request in calls if request.extra.get("response_contract") in SEMANTIC_CONTRACTS]) == sections + 1
    # The imported helper replays every committed Completion with Fake.generate
    # disabled, proving the same execution identity does not redispatch.
    assert len(verifications) >= 2 * (sections + 1)
    return (*outcome, calls, guards, verifications)


def _failed_item(outcome, scope):
    engine, _calls, captures, _summary, top = outcome[:5]
    expected_scope = "DOCUMENT" if scope == "DOCUMENT_GLOBAL" else scope
    capture = next(row for row in captures if row[0]["scope"].value == expected_scope)
    arguments, item, facts, completion_outcome = capture
    assert item.state.value == "TECHNICAL_FAILURE"
    assert not facts and not item.fact_refs and not item.positive_proof_refs and not item.indeterminate_proof_refs
    composition = engine.stage_service.target_post_write_composition
    assert composition.validation_store.resolve_run_exact(completion_outcome[0].validation_run_id).execution_state.value == "COMPLETED"
    # A legal Gate may account an unresolved technical item.  It must preserve
    # that unresolved state, rather than infer consistency from absent facts.
    gate = composition.gate_store.resolve_exact(completion_outcome[1])
    assert any("COMPLETION_TECHNICAL_FAILURE:" + item.expected_validation_item_ref in reason
        for reason in gate.unresolved_reason_refs)
    assert top.terminal_error is None
    return item, capture


def _failure_verification(outcome, scope):
    material_scope = "DOCUMENT" if scope == "DOCUMENT_GLOBAL" else scope
    return next(row for row in outcome[-1] if row[2]["validation_scope"] == material_scope and row[1]["failure"] is not None)


def _verify_saved(saved, accounting=None):
    owner, original_accounting, material, identity, arguments = saved
    return owner._verify_accounting(accounting if accounting is not None else original_accounting,
        material, identity, **arguments)


def _failure_field(accounting, field, value):
    """Keep overall/global failure aliasing valid while corrupting the cause."""
    changed = deepcopy(accounting)
    changed["failure"][field] = value
    if (changed.get("document_global") or {}).get("failure") is not None:
        changed["document_global"]["failure"][field] = value
    return changed


def _assert_rejected_tampering(saved, *, scope):
    owner, accounting, _material, identity, _arguments = saved
    before = native.citation_tests._durable_state_from_composition(owner.post_write)
    with patch.object(FakeLLMClient, "generate", side_effect=AssertionError("receipt verification dispatched model")):
        for field, value in (("source_result_ref", "formal-executability://sha256/" + "0" * 64),
                             ("binding_ref", "semantic-relation-binding://sha256/" + "0" * 64),
                             ("error_code", "AO007_TAMPERED_LOCAL_FAILURE")):
            with pytest.raises(REJECTED):
                _verify_saved(saved, _failure_field(accounting, field, value))

        bad_source = deepcopy(accounting["failure"]["source_ref"])
        bad_source["event_id"] = "ao007-not-the-committed-guard"
        with pytest.raises(REJECTED):
            _verify_saved(saved, _failure_field(accounting, "source_ref", bad_source))

        missing_guard = deepcopy(accounting)
        target = missing_guard["document_global"] if scope == "DOCUMENT_GLOBAL" else missing_guard
        target["executability_refs"] = []
        with pytest.raises(REJECTED):
            _verify_saved(saved, missing_guard)

        original_intermediate = owner.post_write.validation_store.resolve_intermediate
        guard_source = ValidationIntermediateRefSchema.model_validate(accounting["failure"]["source_ref"])
        def different_guard(ref, **arguments):
            value = deepcopy(original_intermediate(ref, **arguments))
            if ref == guard_source:
                value["binding_ref"] = "semantic-relation-binding://sha256/" + "0" * 64
            return value
        with patch.object(owner.post_write.validation_store, "resolve_intermediate", different_guard):
            with pytest.raises(REJECTED):
                _verify_saved(saved)

        receipt_ref = (accounting["document_global"] if scope == "DOCUMENT_GLOBAL" else accounting)["semantic_input_receipt_ref"]
        result_ref = receipt_ref.removesuffix("/input") + "/result"
        original_get = owner.store.get_json
        original_result = original_get(result_ref, **identity)
        assert original_result["outcome"] == "completed" and "candidate" in original_result
        for part in ("input", "candidate", "generation"):
            def tamper(ref, _part=part, **keys):
                value = deepcopy(original_get(ref, **keys))
                if _part == "input" and ref == receipt_ref:
                    value["input_hash"] = "0" * 64
                elif _part == "candidate" and ref == result_ref:
                    candidate = value["candidate"]
                    rows = candidate["work_results"][0]["results"] if "work_results" in candidate else candidate["results"]
                    rows[0]["relation"] = "INCONSISTENT"
                elif _part == "generation" and ref.endswith("/generation"):
                    value["response"]["content"] = "AO007 changed raw physical response"
                return value
            with patch.object(owner.store, "get_json", tamper):
                with pytest.raises(REJECTED):
                    _verify_saved(saved)
    after = native.citation_tests._durable_state_from_composition(owner.post_write)
    assert before == after


@pytest.mark.parametrize(("scope", "sections"), [
    ("SECTION", 1), ("DOCUMENT_GLOBAL", 1), ("DOCUMENT", 2), ("DOCUMENT_GLOBAL", 2),
], ids=["dedicated-section", "dedicated-global", "shared-document", "shared-global"])
def test_local_ref_failure_preserves_completed_physical_receipt_and_identity(tmp_path, scope, sections):
    outcome = _run_failure(tmp_path, scope=scope, sections=sections)
    item, _capture = _failed_item(outcome, scope)
    failure = item.technical_failure_ref
    assert failure["kind"] == ("GLOBAL_FORMAL_EXECUTABILITY_TECHNICAL_FAILURE" if scope == "DOCUMENT_GLOBAL" else "FORMAL_EXECUTABILITY_TECHNICAL_FAILURE")
    assert failure["error_code"] == "FORMAL_OPERAND_REF_UNRESOLVED"
    assert tuple(failure["reason_codes"]) == ("FORMAL_OPERAND_REF_UNRESOLVED",)
    assert failure["origin"] == FailureOrigin.POST_WRITE.value
    assert failure["category"] == FailureCategory.VALIDATION.value
    saved = _failure_verification(outcome, scope)
    owner, accounting, _material, identity, _arguments = saved
    evidence = owner.post_write.validation_intermediate_runtime.resolve_typed(
        ValidationIntermediateRefSchema.model_validate(failure["source_ref"]), FormalExecutabilityResultSchema)
    assert evidence.state.value == "TECHNICAL_FAILURE"
    assert failure["source_result_ref"] == evidence.result_ref
    assert failure["binding_ref"] == evidence.binding_ref
    assert tuple(failure["reason_codes"]) == evidence.reason_codes
    target = accounting["document_global"] if scope == "DOCUMENT_GLOBAL" else accounting
    result = owner.store.get_json(target["semantic_input_receipt_ref"].removesuffix("/input") + "/result", **identity)
    assert result["outcome"] == "completed" and result.get("failure") is None
    assert result["candidate"]
    assert any(d.code == failure["error_code"] and d.failure_origin is FailureOrigin.POST_WRITE
        and d.diagnostic_ref == opaque_ref(failure) for d in outcome[4].terminal_outcome.diagnostics)
    _assert_rejected_tampering(saved, scope=scope)


@pytest.mark.parametrize(("scope", "sections"), [("SECTION", 1), ("DOCUMENT", 2)], ids=["dedicated", "shared"])
def test_failed_model_response_keeps_physical_failure_receipt(tmp_path, scope, sections):
    outcome = _run_failure(tmp_path, scope=scope, sections=sections, cause="provider")
    item, _capture = _failed_item(outcome, scope)
    failure = item.technical_failure_ref
    assert failure["kind"] == ("CONSISTENCY_SHARED_TECHNICAL_FAILURE" if sections == 2 else "CONSISTENCY_TECHNICAL_FAILURE")
    assert failure["error_code"] == "AO007_FIXED_PROVIDER_UNAVAILABLE"
    assert failure["origin"] == FailureOrigin.PROVIDER_RUNTIME.value
    assert failure["category"] == FailureCategory.AVAILABILITY.value
    saved = _failure_verification(outcome, scope)
    owner, accounting, _material, identity, _arguments = saved
    receipt_ref = accounting["semantic_input_receipt_ref"]
    result_ref = receipt_ref.removesuffix("/input") + "/result"
    result = owner.store.get_json(result_ref, **identity)
    assert result["outcome"] == "failed" and result["failure"] == failure
    packet = owner.store.get_json(receipt_ref.removesuffix("/input") + "/generation", **identity)
    assert packet["response"]["success"] is False
    assert packet["response"]["failure_origin"] == FailureOrigin.PROVIDER_RUNTIME.value
    assert packet["response"]["error"]["error_code"] == failure["error_code"]
    assert any(d.diagnostic_ref == opaque_ref(failure) and d.failure_origin is FailureOrigin.PROVIDER_RUNTIME
        for d in outcome[4].terminal_outcome.diagnostics)
    original_get = owner.store.get_json
    def different_failure(ref, **keys):
        value = deepcopy(original_get(ref, **keys))
        if ref == result_ref:
            value["failure"]["error_code"] = "AO007_CHANGED_PHYSICAL_FAILURE"
        return value
    with patch.object(owner.store, "get_json", different_failure), patch.object(FakeLLMClient, "generate", side_effect=AssertionError("failure verification dispatched model")):
        with pytest.raises(REJECTED):
            _verify_saved(saved)


def test_local_plan_failure_keeps_completed_model_and_conformance_source(tmp_path):
    outcome = _run_failure(tmp_path, scope="SECTION", sections=1, cause="plan")
    item, _capture = _failed_item(outcome, "SECTION")
    failure = item.technical_failure_ref
    assert failure["kind"] == failure["error_code"] == "CONSISTENCY_PLAN_NONCONFORMING"
    assert failure["origin"] == FailureOrigin.POST_WRITE.value
    saved = _failure_verification(outcome, "SECTION")
    owner, accounting, _material, identity, _arguments = saved
    evidence = owner.post_write.validation_intermediate_runtime.resolve_typed(
        ValidationIntermediateRefSchema.model_validate(failure["source_ref"]), ConsistencyPlanConformanceResultSchema)
    assert evidence.state.value == "NONCONFORMING"
    assert failure["source_result_ref"] == evidence.result_ref
    assert failure["source_ref"] == accounting["conformance_ref"]
    result = owner.store.get_json(accounting["semantic_input_receipt_ref"].removesuffix("/input") + "/result", **identity)
    assert result["outcome"] == "completed" and result.get("failure") is None
    with patch.object(FakeLLMClient, "generate", side_effect=AssertionError("plan verification dispatched model")):
        with pytest.raises(REJECTED):
            _verify_saved(saved, _failure_field(accounting, "source_result_ref", "consistency-plan-conformance://sha256/" + "0" * 64))


"""AO-007 global applicability early-return evidence checks; no runtime I/O."""
from types import SimpleNamespace
from unittest.mock import Mock, patch

import pytest

from apps.enterprise_document.schemas.post_write.foundation import (
    ArtifactScope,
    ArtifactStateRefSchema,
    QualityDeliveryContractRefSchema,
    ValidationIntermediateKind,
    ValidationIntermediateRefSchema,
)
from apps.enterprise_document.services.agent_rag_v1.post_write_consistency_composition import (
    ConsistencyCompositionError,
    ConsistencyGlobalProcedureAccounting,
    TargetPostWriteConsistency,
)
from apps.enterprise_document.services.post_write.consistency_procedure import ConsistencyProcedureInputResolver


def _assert_global_early_return_requires_no_failure(applicability):
    run_id = "validation-run:ao007-unit-global"
    artifact_ref = ArtifactStateRefSchema(artifact_id="document-artifact:ao007-unit-global",
        artifact_version=1, artifact_hash="a" * 64, artifact_scope=ArtifactScope.DOCUMENT)
    lowering_ref = ValidationIntermediateRefSchema(validation_run_id=run_id,
        event_id="intermediate:ao007-unit-global-lowering",
        intermediate_kind=ValidationIntermediateKind.TARGET_ACCOUNTING,
        intermediate_sha256="b" * 64)
    contract_ref = QualityDeliveryContractRefSchema(contract_id="quality-contract:ao007-unit-global",
        contract_schema_version="quality_delivery_contract_v1", contract_version="v1", contract_hash="c" * 64)
    material = {"assertions": [], "assertion_context": {},
        "artifact_state_ref": artifact_ref.model_dump(mode="json"), "validation_run_id": run_id,
        "subject_hash": "d" * 64, "source_snapshot_hash": "e" * 64}
    state = "INDETERMINATE" if applicability == "UNRESOLVED" else "NOT_APPLICABLE"
    proofs = (lowering_ref.model_dump(mode="json"),) if applicability == "UNRESOLVED" else ()
    exact_global = ConsistencyGlobalProcedureAccounting(lowering_ref=lowering_ref.model_dump(mode="json"),
        state=state, indeterminate_proof_refs=proofs)

    # The resolver's owner replay is the sole mocked authority.  This fixture
    # does not construct a model gateway, database, or intermediate store.
    runtime = Mock()
    owner = object.__new__(TargetPostWriteConsistency)
    owner.post_write = SimpleNamespace(validation_intermediate_runtime=runtime,
        contract_store=Mock(), requirement_store=Mock())
    lowering = SimpleNamespace(document_global_applicability=applicability)
    with patch.object(ConsistencyProcedureInputResolver, "replay", return_value=lowering) as replay:
        result = owner._verify_document_global_accounting(exact_global,
            material=material, identity={}, quality_contract_ref=contract_ref)
        assert result == (state, (), (lowering_ref,) if applicability == "UNRESOLVED" else ())
        replay.assert_called_once_with(lowering_ref, contract_ref=contract_ref,
            artifact_state_ref=artifact_ref, validation_run_id=run_id, validation_subject_hash=material["subject_hash"])

        # Applicability-only outcomes cannot authenticate a technical cause.
        # Even a nonempty post_write-shaped source packet must be refused.
        unbound = exact_global.model_copy(update={"failure": {
            "schema_version": "artifact_consistency_failure_v1",
            "kind": "GLOBAL_FORMAL_EXECUTABILITY_TECHNICAL_FAILURE",
            "error_code": "AO007_UNBOUND_GLOBAL_FAILURE", "error_type": "FormalExecutabilityError",
            "message": "Unbound local technical failure", "category": "validation", "origin": "post_write",
            "source_ref": lowering_ref.model_dump(mode="json"),
            "source_result_ref": "formal-executability://sha256/" + "f" * 64,
            "binding_ref": "semantic-relation-binding://sha256/" + "f" * 64,
        }})
        with pytest.raises(ConsistencyCompositionError, match="global.*accounting differs"):
            owner._verify_document_global_accounting(unbound,
                material=material, identity={}, quality_contract_ref=contract_ref)
    assert runtime.mock_calls == []
    assert owner.post_write.contract_store.mock_calls == []
    assert owner.post_write.requirement_store.mock_calls == []


def test_unresolved_global_applicability_refuses_unbound_technical_failure():
    _assert_global_early_return_requires_no_failure("UNRESOLVED")


def test_not_applicable_global_refuses_unbound_technical_failure():
    _assert_global_early_return_requires_no_failure("NOT_APPLICABLE")


def test_small_fixed_protocol_failure_keeps_failed_semantic_receipt(tmp_path):
    # Preserve the old 8-assertion BAD_JSON fixture and its budget rejection;
    # use two assertions here to exercise parser-failure accounting itself.
    with patch.object(socket.socket, "connect", side_effect=AssertionError("offline protocol case attempted network")):
        engine, calls, captures, summary, top = native._run(tmp_path, "BAD_JSON", repeats=1,
            body_padding="本节按既定工作流程逐步展开说明。" * 12,
            identity_suffix="-ao007-protocol-" + uuid.uuid4().hex)
    assert len(captures) == 2 and top.terminal_error is None
    for arguments, item, facts, completion_refs in captures:
        assert item.state.value == "TECHNICAL_FAILURE"
        assert not facts and not item.fact_refs and not item.positive_proof_refs and not item.indeterminate_proof_refs
        failure = item.technical_failure_ref
        assert failure["kind"] == "CONSISTENCY_TECHNICAL_FAILURE"
        assert failure["kind"] != "CANONICAL_BUDGET_FAILURE"
        assert failure["origin"] != FailureOrigin.PROVIDER_RUNTIME.value
    semantic_calls = [request for request in calls if request.extra.get("response_contract") in SEMANTIC_CONTRACTS]
    assert len(semantic_calls) == 4  # SECTION and GLOBAL, one existing correction each.
