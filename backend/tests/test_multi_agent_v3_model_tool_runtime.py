from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import time
from types import SimpleNamespace
from typing import Callable

import pytest

from contracts.base_client import BaseLLMClient
from apps.enterprise_document.schemas.agent_rag_v1 import (
    AgentExecutionContextSchema,
    AgentRole,
    CanonicalBudgetVectorSchema,
    ToolHandlerOutputSchema,
    ToolResultStatus,
)
from apps.enterprise_document.services.agent_rag_v1 import (
    BudgetManager,
    CanonicalBudgetCapacityExceeded,
    CanonicalInvocationBudgetAdapter,
    InMemoryToolExecutionStore,
    ToolGateway,
)
from model_gateway.failure_policy import AvailabilityFailurePolicy
from model_gateway.model_contract import (
    ModelProfile,
    ModelRole,
    RoutingPolicy,
)
from model_gateway.model_gateway import ModelGateway
from model_gateway.model_router import ModelRouter
from schemas.failure import FailureCategory, FailureOrigin
from schemas.model import (
    ModelRequestSchema,
    ModelResponseSchema,
    TokenUsageSchema,
)


NOW = datetime(2026, 9, 3, 15, 0, tzinfo=timezone.utc)


def _cleanup_exception_snapshot(exc, seen=None):
    if exc is None:
        return None
    seen = set() if seen is None else seen
    if id(exc) in seen:
        return {"cycle": type(exc).__name__}
    seen.add(id(exc))
    return {
        "type": type(exc).__name__, "message": str(exc),
        "native_denial": getattr(exc, "native_denial", None).model_dump(mode="json")
        if getattr(exc, "native_denial", None) is not None else None,
        "pre_physical_phase_proof": getattr(exc, "pre_physical_phase_proof", None).model_dump(mode="json")
        if getattr(exc, "pre_physical_phase_proof", None) is not None else None,
        "cause": _cleanup_exception_snapshot(exc.__cause__, seen),
        "context": _cleanup_exception_snapshot(exc.__context__, seen),
    }


def _cleanup_observation(tmp_path, case, manager, client, error, before, **extra):
    """Keep red and green native observations before any expected-contract assertion."""
    data = dict(case=case, observed_at_ns=time.time_ns(),
        source_sha256=hashlib.sha256(Path(__file__).parents[1].joinpath(
            "model_gateway/model_gateway.py").read_bytes()).hexdigest(),
        sandbox=str(tmp_path), before=before.model_dump(mode="json"),
        after=manager.canonical_state().model_dump(mode="json"),
        provider_sends=len(client.calls), exception=_cleanup_exception_snapshot(error), **extra)
    path = tmp_path / ("gateway_cleanup_" + case + "_observation.json")
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    # The runner retains sandbox paths; emit the full native receipt into raw logs too.
    print("GATEWAY_CLEANUP_OBSERVATION " + json.dumps(data, ensure_ascii=False))
    return data


def _caught_gateway_error(gateway, request):
    try:
        gateway.generate(request)
    except BaseException as exc:
        return exc
    raise AssertionError("expected native gateway rejection")


def _limits(**updates: int) -> CanonicalBudgetVectorSchema:
    values = {
        "logical_model_calls": 4,
        "logical_tool_calls": 4,
        "model_tokens_total": 20000,
        "research_rounds": 2,
        "repair_attempts": 2,
        "generation_attempts": 4,
        "replan_attempts": 2,
        "true_retry_attempts": 2,
    }
    values.update(updates)
    return CanonicalBudgetVectorSchema(**values)


def _manager(tmp_path, *, limits=None) -> BudgetManager:
    return BudgetManager.canonical(
        task_id="task_1",
        run_id="run_1",
        limits=limits or _limits(),
        max_elapsed_seconds=120,
        storage_path=tmp_path / "budget.jsonl",
        policy_ref="budget-policy://step4-model-tool",
        policy_version="v1",
        clock=lambda: NOW,
    )


class _StubClient(BaseLLMClient):
    def __init__(
        self,
        model_name: str,
        factory: Callable[[ModelRequestSchema], ModelResponseSchema],
    ) -> None:
        self.model_name = model_name
        self.factory = factory
        self.calls: list[ModelRequestSchema] = []

    def generate(self, request: ModelRequestSchema) -> ModelResponseSchema:
        self.calls.append(request)
        return self.factory(request)


def _profile(profile_id: str, model_name: str) -> ModelProfile:
    return ModelProfile(
        profile_id=profile_id,
        model_name=model_name,
        provider="test-provider",
        context_window=8192,
        max_output_tokens=1024,
    )


def _request() -> ModelRequestSchema:
    return ModelRequestSchema(
        model_call_id="model-op-1",
        task_id="task_1",
        run_id="run_1",
        model_role=ModelRole.SECTION_GENERATION.value,
        caller_agent="WriterAgent",
        prompt="hello",
        max_tokens=100,
        created_at=NOW.isoformat(),
        extra={
            "section_id": "section-1",
            "attempt_id": "attempt-1",
            "logical_operation_id": "writer-op-1",
            "generation_params": {"top_p": 0.9, "do_sample": False},
        },
    )


def _gateway(tmp_path, *, limits=None):
    profiles = [_profile("p1", "m1"), _profile("p2", "m2")]
    router = ModelRouter(
        "m1",
        profiles=profiles,
        policies=[
            RoutingPolicy(
                role=ModelRole.SECTION_GENERATION,
                candidates=["p1", "p2"],
            )
        ],
    )
    manager = _manager(tmp_path, limits=limits)
    budget = CanonicalInvocationBudgetAdapter(manager)
    gateway = ModelGateway(
        default_model_name="m1",
        router=router,
        budget_runtime=budget,
    )
    return gateway, manager


def _unavailable(request: ModelRequestSchema) -> ModelResponseSchema:
    return ModelResponseSchema(
        model_call_id=request.model_call_id,
        task_id=request.task_id,
        run_id=request.run_id,
        model_name="m1",
        success=False,
        error_message="provider unavailable",
        failure_origin=FailureOrigin.PROVIDER_RUNTIME,
        created_at=request.created_at,
    )


def _success(request: ModelRequestSchema) -> ModelResponseSchema:
    return ModelResponseSchema(
        model_call_id=request.model_call_id,
        task_id=request.task_id,
        run_id=request.run_id,
        model_name="m2",
        success=True,
        content="ok",
        created_at=request.created_at,
        token_usage=TokenUsageSchema(
            prompt_tokens=8,
            completion_tokens=4,
            total_tokens=12,
        ),
    )


@pytest.mark.parametrize("denial", ["capacity", "unreconciled", "draining"])
@pytest.mark.parametrize("runtime_kind", ["adapter", "router"])
def test_consumed_logical_cleanup_preserves_native_rejection(tmp_path, denial, runtime_kind):
    from apps.enterprise_document.services.agent_rag_v1.model_tool_budget_runtime import CanonicalInvocationBudgetRouter
    from apps.enterprise_document.services.agent_rag_v1.budget_manager import CanonicalBudgetDraining
    gateway, manager = _gateway(tmp_path, limits=_limits(model_tokens_total=110 if denial == "capacity" else 20000))
    if runtime_kind == "router":
        gateway.budget_runtime = CanonicalInvocationBudgetRouter(lambda *_: manager,
            existing_manager_provider=lambda *_: manager)
    client = _StubClient("m1", _success)
    gateway.register_client(client)
    gateway.generate(_request())
    if denial == "unreconciled":
        gateway.budget_runtime.reserve_provider_attempt(_request().model_copy(update={
            "extra": {**_request().extra, "provider_attempt_id": "unsettled-original"}}, deep=True))
    elif denial == "draining":
        manager.start_draining(expected_revision=manager.canonical_state().revision)
    before = manager.canonical_state()
    original_errors = []
    releases = []
    original_release = gateway.budget_runtime.release_reservation
    def observe_release(reservation_id):
        releases.append(reservation_id)
        return original_release(reservation_id)
    gateway.budget_runtime.release_reservation = observe_release
    original_admit = gateway.budget_runtime.reserve_provider_attempt
    def observe_admission(request):
        try:
            return original_admit(request)
        except Exception as exc:
            original_errors.append(exc)
            raise
    gateway.budget_runtime.reserve_provider_attempt = observe_admission
    error = _caught_gateway_error(gateway, _request())
    data = _cleanup_observation(tmp_path, f"consumed-{runtime_kind}-{denial}", manager,
        client, error, before, admission_exception=_cleanup_exception_snapshot(original_errors[0]),
        release_invocations=releases)
    assert error is original_errors[0]
    if denial == "capacity":
        assert isinstance(error, CanonicalBudgetCapacityExceeded)
        assert error.native_denial.denied_dimension == "model_tokens_total"
    elif denial == "draining":
        assert isinstance(error, CanonicalBudgetDraining)
    else:
        assert type(error) is RuntimeError
        assert "unreconciled active provider-token reservation" in str(error)
    assert data["before"] == data["after"]
    assert releases == []
    assert len(client.calls) == 1
    assert not hasattr(error, "pre_physical_phase_proof")
    assert gateway._active_calls == 0


def test_fresh_logical_denial_retains_exact_release_and_native_proof(tmp_path):
    gateway, manager = _gateway(tmp_path, limits=_limits(model_tokens_total=1))
    client = _StubClient("m1", _success)
    gateway.register_client(client)
    before = manager.canonical_state()
    error = _caught_gateway_error(gateway, _request())
    _cleanup_observation(tmp_path, "fresh-token-denial", manager, client, error, before)
    assert isinstance(error, CanonicalBudgetCapacityExceeded)
    proof = gateway.budget_runtime.validate_pre_physical_token_rejection_proof(error.pre_physical_phase_proof)
    logical = manager.resolve_reservation(proof.logical_reservation_id)
    assert logical.status == "released" and logical.actual.is_zero()
    assert proof.gateway_call_closed and not proof.physical_dispatch_started
    assert not client.calls and manager.canonical_state().used.logical_model_calls == 0
    # A released logical is terminal; retry cannot create a cognition or proof.
    second = _caught_gateway_error(gateway, _request())
    assert isinstance(second, CanonicalBudgetCapacityExceeded)
    assert not hasattr(second, "pre_physical_phase_proof")
    assert manager.resolve_reservation(proof.logical_reservation_id) == logical
    assert not client.calls


@pytest.mark.parametrize("reused", [False, True])
def test_observer_start_cleanup_uses_canonical_logical_state(tmp_path, reused):
    gateway, manager = _gateway(tmp_path)
    client = _StubClient("m1", _success)
    gateway.register_client(client)
    if reused:
        gateway.generate(_request())
    original = RuntimeError("test observer could not persist its start")
    def fail_start(*args, **kwargs):
        raise original
    gateway.observer.start = fail_start
    before = manager.canonical_state()
    error = _caught_gateway_error(gateway, _request())
    data = _cleanup_observation(tmp_path, f"observer-start-reused-{reused}", manager, client, error, before)
    assert error is original
    logical = [x for x in manager.canonical_state().reservations if x.reservation_ref.reservation_kind == "logical_model_call"]
    assert logical[0].status == ("consumed" if reused else "released")
    assert all(x.status != "active" for x in manager.canonical_state().reservations)
    assert data["provider_sends"] == int(reused)
    assert not hasattr(error, "pre_physical_phase_proof")


@pytest.mark.parametrize("kind", ["cancelled", "base_exception", "settlement_unknown"])
def test_consumed_logical_after_interruption_keeps_real_settlement(tmp_path, kind):
    from core.runtime.execution_control import WorkflowExecutionCancelled
    class NativeAbort(BaseException):
        pass
    original = WorkflowExecutionCancelled("test-cancel") if kind == "cancelled" else NativeAbort("test-native-abort")
    gateway, manager = _gateway(tmp_path)
    def interrupted(request):
        if kind == "settlement_unknown":
            return _success(request)
        raise original
    client = _StubClient("m1", interrupted)
    gateway.register_client(client)
    if kind == "settlement_unknown":
        def fail_settlement(*args):
            raise RuntimeError("test settlement unavailable after physical completion")
        gateway.budget_runtime.settle_provider_attempt = fail_settlement
    error = _caught_gateway_error(gateway, _request())
    before = manager.canonical_state()
    _cleanup_observation(tmp_path, f"physical-interruption-{kind}", manager, client, error, before)
    logical = next(x for x in before.reservations if x.reservation_ref.reservation_kind == "logical_model_call")
    tokens = next(x for x in before.reservations if x.reservation_ref.reservation_kind == "model_provider_attempt_tokens")
    assert logical.status == "consumed"
    if kind == "cancelled":
        assert error is original and tokens.status == "consumed"
        assert tokens.actual.model_tokens_total >= tokens.requested.model_tokens_total
    else:
        assert tokens.status == "active" and tokens.actual.is_zero()
        second = _caught_gateway_error(gateway, _request())
        _cleanup_observation(tmp_path, f"physical-interruption-retry-{kind}", manager, client, second, before)
        assert type(second) is RuntimeError
        assert "unreconciled active provider-token reservation" in str(second)
        assert manager.canonical_state() == before
    assert len(client.calls) == 1 and gateway._active_calls == 0
    assert not hasattr(error, "pre_physical_phase_proof")


@pytest.mark.parametrize("stage", ["token", "observer"])
def test_predispatch_base_exception_retains_unknown_active_reservations(tmp_path, stage):
    class NativeAbort(BaseException):
        pass
    gateway, manager = _gateway(tmp_path)
    client = _StubClient("m1", _success)
    gateway.register_client(client)
    original = NativeAbort("test prephysical abort remains control flow")
    def abort(*args, **kwargs):
        raise original
    if stage == "token":
        gateway.budget_runtime.reserve_provider_attempt = abort
    else:
        gateway.observer.start = abort
    before = manager.canonical_state()
    error = _caught_gateway_error(gateway, _request())
    _cleanup_observation(tmp_path, f"predispatch-base-exception-{stage}", manager, client, error, before)
    assert error is original and not client.calls
    state = manager.canonical_state()
    assert all(x.status == "active" for x in state.reservations)
    assert len(state.reservations) == (1 if stage == "token" else 2)
    assert state.used.is_zero() and not hasattr(error, "pre_physical_phase_proof")
    assert gateway._active_calls == 0


def test_cleanup_corruption_still_fails_closed_without_no_effect_proof(tmp_path):
    from apps.enterprise_document.services.agent_rag_v1.budget_manager import CanonicalBudgetIntegrityError
    gateway, manager = _gateway(tmp_path, limits=_limits(model_tokens_total=1))
    client = _StubClient("m1", _success)
    gateway.register_client(client)
    original = CanonicalBudgetIntegrityError("test canonical release history is contradictory")
    def corrupt_release(*args, **kwargs):
        raise original
    gateway.budget_runtime.release_reservation = corrupt_release
    before = manager.canonical_state()
    error = _caught_gateway_error(gateway, _request())
    _cleanup_observation(tmp_path, "cleanup-real-corruption", manager, client, error, before)
    assert error is original and isinstance(error.__context__, CanonicalBudgetCapacityExceeded)
    assert not hasattr(error, "pre_physical_phase_proof") and not client.calls
    assert manager.canonical_state().reservations[0].status == "active"


@pytest.mark.parametrize("tamper", ["owner", "reservation_kind"])
def test_logical_cleanup_rejects_wrong_canonical_owner_or_identity(tmp_path, tamper):
    gateway, manager = _gateway(tmp_path)
    adapter = gateway.budget_runtime
    logical_id = adapter.reserve_model_logical_call(_request())
    reservation_id = logical_id
    if tamper == "owner":
        adapter.budget_manager = SimpleNamespace(_canonical_mode=True, task_id="another-task", run_id="run_1")
    else:
        reservation_id = adapter.reserve_provider_attempt(_request().model_copy(update={
            "extra": {**_request().extra, "provider_attempt_id": "wrong-kind"}}, deep=True))
    before = manager.canonical_state()
    with pytest.raises(RuntimeError, match="differs from request"):
        gateway._release_active_logical_reservation(_request(), reservation_id)
    assert manager.canonical_state() == before
    assert manager.resolve_reservation(logical_id).status == "active"


@pytest.mark.parametrize("kind", ["resource", "technical"])
def test_gateway_native_error_preserves_writer_terminal_partial_artifact(tmp_path, kind):
    from application.terminal_outcome_projector import opaque_ref
    from schemas.common import ErrorSchema
    from apps.enterprise_document.schemas.pre_write_binding import WriterSubjectRefSchema
    from apps.enterprise_document.schemas.pre_write_control import PreWriteRouteDecisionRefSchema
    from apps.enterprise_document.schemas.pre_write_writer import WriterGenerationClass, WriterLogicalExecutionKey
    from apps.enterprise_document.schemas.post_write.foundation import AvailabilityState, OverallQualityState
    from apps.enterprise_document.services.scheme_writer.writer_invocation import PreWriteWriterInvocationService
    from apps.enterprise_document.services.agent_rag_v1.terminal_outcome_transport import project_post_write
    gateway, manager = _gateway(tmp_path, limits=_limits(model_tokens_total=110))
    client = _StubClient("m1", _success)
    gateway.register_client(client)
    gateway.generate(_request())
    before = manager.canonical_state()
    error = _caught_gateway_error(gateway, _request()) if kind == "resource" else RuntimeError("test independent technical fault")
    subject = WriterSubjectRefSchema(run_id="run_1", document_id="document_1", section_id="section-2")
    route = PreWriteRouteDecisionRefSchema(route_decision_id="route-2")
    provenance = SimpleNamespace(writer_subject_ref=subject, write_instance_id="write-2", route_decision_ref=route,
        generation_class=WriterGenerationClass.NORMAL_WRITE,
        canonical_evidence_input=SimpleNamespace(evidence_bundle_sha256="a" * 64))
    prepared = SimpleNamespace(logical_execution_key=WriterLogicalExecutionKey(
        writer_subject_ref=subject, write_instance_id="write-2", route_decision_ref=route),
        invocation=provenance, target_prompt=SimpleNamespace(technical_recovery_context=SimpleNamespace(
            prompt_id="dummy-prompt", prompt_version="v1", context_package_id="context-2", context_sha256="b" * 64)))
    writer = object.__new__(PreWriteWriterInvocationService)
    writer_result = writer._exception_result(prepared, error)
    content = "离线自建事实：服务器 12 台，存储容量 96 TB。"
    artifact_path = tmp_path / "owner_approved_partial_artifact.json"
    artifact_path.write_text(json.dumps({"content": content}, ensure_ascii=False), encoding="utf-8")
    before_sha = hashlib.sha256(artifact_path.read_bytes()).hexdigest()
    section = SimpleNamespace(section_id="section-1", control_decision_ref="section-control-1",
        artifact_state_ref={"artifact_sha256": before_sha}, validation_completion_ref="validation-1")
    control = SimpleNamespace(input_manifest=SimpleNamespace(generation_class=WriterGenerationClass.NORMAL_WRITE),
        quality_state_passthrough=OverallQualityState.PASS,
        availability_projection=SimpleNamespace(state=AvailabilityState.AVAILABLE))
    reads = []
    def resolve_control(ref):
        assert ref == section.control_decision_ref
        reads.append(["control", ref])
        return control
    def resolve_artifact(ref):
        assert ref == section.artifact_state_ref
        assert hashlib.sha256(artifact_path.read_bytes()).hexdigest() == before_sha
        reads.append(["artifact", ref])
        return SimpleNamespace(canonical_payload_ref=json.loads(artifact_path.read_text(encoding="utf-8")))
    def resolve_validation(ref):
        assert ref == section.validation_completion_ref
        reads.append(["validation", ref])
        return SimpleNamespace(items=())
    composition = SimpleNamespace(control_store=SimpleNamespace(resolve_exact=resolve_control),
        section_eligibility=SimpleNamespace(resolve=lambda **kwargs: SimpleNamespace(candidate=section)),
        artifact_service=SimpleNamespace(resolve_exact=resolve_artifact),
        validation_store=SimpleNamespace(resolve_completion=resolve_validation), lifecycle_service=None)
    payload = SimpleNamespace(task_id="task_1", run_id="run_1", section_execution_results=(
        SimpleNamespace(section=SimpleNamespace(section_id="section-1")),
        SimpleNamespace(section=SimpleNamespace(section_id="section-2"))))
    # The real graph boundary rehydrates the immutable Writer result's frozen JSON.
    typed_error = ErrorSchema.model_validate(writer_result.model_dump(mode="json")["error"])
    terminal = project_post_write(composition, payload=payload, sections=(section,), error=typed_error)
    _cleanup_observation(tmp_path, f"writer-terminal-partial-{kind}", manager, client, error, before,
        writer_result=writer_result.model_dump(mode="json"), terminal=terminal.model_dump(mode="json"),
        source_artifact_sha256=before_sha, exact_owner_reads=reads)
    assert hashlib.sha256(artifact_path.read_bytes()).hexdigest() == before_sha
    assert json.loads(artifact_path.read_text(encoding="utf-8"))["content"] == content
    assert writer_result.generation_provenance["evidence_bundle_sha256"] == "a" * 64
    assert writer_result.logical_execution_key == prepared.logical_execution_key
    assert terminal.completed_scope_refs == ("section-1",)
    assert terminal.unfinished_scope_refs == ("section-2", "document_validation_and_release")
    assert not terminal.publication_exists and terminal.control_action is None
    assert terminal.requires_human is False  # Native writer fact is transported, not invented.
    if kind == "resource":
        assert writer_result.failure_kind.value == "runtime_resource_failure"
        assert typed_error.failure_category is FailureCategory.RESOURCE_EXHAUSTED
        assert terminal.failure_origin is FailureOrigin.CANONICAL_BUDGET
        assert terminal.status.value == "resource_exhausted" and terminal.is_partial
        assert terminal.execution_status.value == "partial_success"
        assert len(terminal.artifacts) == 1
        assert terminal.artifacts[0].artifact_ref == opaque_ref(section.artifact_state_ref)
        assert terminal.artifacts[0].content == content
        assert terminal.artifacts[0].quality_state is OverallQualityState.PASS
    else:
        assert writer_result.failure_kind.value == "model_invocation_failure"
        assert typed_error.failure_category is FailureCategory.UNKNOWN
        assert terminal.status.value == "TECHNICAL_FAILURE" and not terminal.artifacts


def test_model_fallback_uses_one_logical_call_and_two_token_reservations(tmp_path) -> None:
    gateway, manager = _gateway(tmp_path)
    first = _StubClient("m1", _unavailable)
    second = _StubClient("m2", _success)
    gateway.register_client(first)
    gateway.register_client(second)

    response = gateway.generate(_request())

    assert response.success is True
    assert len(first.calls) == 1
    assert len(second.calls) == 1
    assert first.calls[0].model_call_id == second.calls[0].model_call_id == "model-op-1"
    assert (
        first.calls[0].extra["provider_attempt_id"]
        != second.calls[0].extra["provider_attempt_id"]
    )

    state = manager.canonical_state()
    assert state.used.logical_model_calls == 1
    token_reservations = [
        item
        for item in state.reservations
        if item.reservation_ref.reservation_kind == "model_provider_attempt_tokens"
    ]
    logical_reservations = [
        item
        for item in state.reservations
        if item.reservation_ref.reservation_kind == "logical_model_call"
    ]
    assert len(logical_reservations) == 1
    assert len(token_reservations) == 2
    assert all(item.status == "consumed" for item in token_reservations)
    assert state.used.model_tokens_total >= 12

    usage = gateway.usage_snapshot()
    assert usage["logical_calls"] == 1
    assert usage["provider_attempts"] == 2
    assert usage["availability_fallback_count"] == 1


def test_token_budget_denial_starts_no_provider_and_releases_logical_reservation(
    tmp_path,
) -> None:
    gateway, manager = _gateway(
        tmp_path,
        limits=_limits(model_tokens_total=1),
    )
    first = _StubClient("m1", _success)
    second = _StubClient("m2", _success)
    gateway.register_client(first)
    gateway.register_client(second)

    with pytest.raises(CanonicalBudgetCapacityExceeded):
        gateway.generate(_request())

    assert first.calls == []
    assert second.calls == []
    state = manager.canonical_state()
    assert state.used.logical_model_calls == 0
    assert state.active_reserved.logical_model_calls == 0
    logical = [
        item
        for item in state.reservations
        if item.reservation_ref.reservation_kind == "logical_model_call"
    ]
    assert len(logical) == 1
    assert logical[0].status == "released"


def test_same_model_call_id_with_same_material_request_reuses_logical_budget(
    tmp_path,
) -> None:
    gateway, manager = _gateway(tmp_path)
    client = _StubClient("m1", _success)
    gateway.register_client(client)
    gateway.register_client(_StubClient("m2", _success))

    first = gateway.generate(_request())
    second = gateway.generate(_request())

    assert first.success is True
    assert second.success is True
    assert len(client.calls) == 2
    state = manager.canonical_state()
    assert state.used.logical_model_calls == 1
    logical = [
        item
        for item in state.reservations
        if item.reservation_ref.reservation_kind == "logical_model_call"
    ]
    assert len(logical) == 1
    assert logical[0].status == "consumed"


def test_same_model_call_id_with_changed_material_request_fails_closed(tmp_path) -> None:
    gateway, _manager_instance = _gateway(tmp_path)
    client = _StubClient("m1", _success)
    gateway.register_client(client)
    gateway.register_client(_StubClient("m2", _success))

    gateway.generate(_request())
    changed = _request().model_copy(update={"prompt": "materially changed prompt"})

    with pytest.raises(ValueError, match="materially different request fingerprint"):
        gateway.generate(changed)


def test_same_model_call_id_with_changed_sampling_parameters_fails_closed(
    tmp_path,
) -> None:
    gateway, _manager_instance = _gateway(tmp_path)
    client = _StubClient("m1", _success)
    gateway.register_client(client)
    gateway.register_client(_StubClient("m2", _success))

    gateway.generate(_request())
    changed = _request().model_copy(
        update={
            "extra": {
                **dict(_request().extra),
                "generation_params": {"top_p": 0.7, "do_sample": True},
            }
        },
        deep=True,
    )

    with pytest.raises(ValueError, match="materially different request fingerprint"):
        gateway.generate(changed)


def test_trace_and_physical_attempt_fields_are_excluded_from_model_fingerprint() -> None:
    baseline = _request()
    changed = baseline.model_copy(
        update={
            "extra": {
                **dict(baseline.extra),
                "trace_id": "trace-changed",
                "span_id": "span-changed",
                "provider_attempt_id": "provider-attempt-changed",
                "provider_attempt_ordinal": 99,
                "availability_fallback_from": ["p1", "p2"],
            }
        },
        deep=True,
    )

    assert ModelGateway._request_fingerprint(baseline) == ModelGateway._request_fingerprint(
        changed
    )


def test_incomplete_provider_usage_settles_conservative_allowance(tmp_path) -> None:
    manager = _manager(tmp_path)
    adapter = CanonicalInvocationBudgetAdapter(manager)
    request = _request().model_copy(
        update={
            "extra": {
                **dict(_request().extra),
                "provider_attempt_id": "provider-attempt-incomplete-usage",
            }
        },
        deep=True,
    )
    reservation_id = adapter.reserve_provider_attempt(request)
    allowance = manager.resolve_reservation(reservation_id).requested.model_tokens_total
    response = ModelResponseSchema(
        model_call_id=request.model_call_id,
        task_id=request.task_id,
        run_id=request.run_id,
        model_name="m1",
        success=False,
        error_message="provider outcome uncertain",
        failure_origin=FailureOrigin.EXECUTION_LIFECYCLE,
        created_at=request.created_at,
        token_usage=TokenUsageSchema(prompt_tokens=3),
        metadata={"usage_complete": False},
    )

    adapter.settle_provider_attempt(reservation_id, request, response)

    terminal = manager.resolve_reservation(reservation_id)
    assert terminal.status == "consumed"
    assert terminal.actual.model_tokens_total >= allowance
    assert manager.canonical_state().used.model_tokens_total >= allowance


def test_unreconciled_provider_token_reservation_blocks_reexecution(tmp_path) -> None:
    gateway, manager = _gateway(tmp_path)
    adapter = gateway.budget_runtime
    assert isinstance(adapter, CanonicalInvocationBudgetAdapter)
    request = _request().model_copy(
        update={
            "extra": {
                **dict(_request().extra),
                "provider_attempt_id": "provider_attempt_crashed_before_settlement",
            }
        },
        deep=True,
    )
    stale_reservation_id = adapter.reserve_provider_attempt(request)
    assert manager.resolve_reservation(stale_reservation_id).status == "active"

    first = _StubClient("m1", _success)
    second = _StubClient("m2", _success)
    gateway.register_client(first)
    gateway.register_client(second)

    with pytest.raises(RuntimeError, match="unreconciled active provider-token reservation"):
        gateway.generate(_request())

    assert first.calls == []
    assert second.calls == []


def test_availability_policy_rejects_budget_or_unknown_origin() -> None:
    policy = AvailabilityFailurePolicy()
    selection = ModelRouter(
        "m1",
        profiles=[_profile("p1", "m1")],
        policies=[
            RoutingPolicy(
                role=ModelRole.SECTION_GENERATION,
                candidates=["p1"],
            )
        ],
    ).plan(_request())[0]

    for origin in (FailureOrigin.CANONICAL_BUDGET, FailureOrigin.UNKNOWN):
        response = ModelResponseSchema(
            model_call_id="model-op-1",
            task_id="task_1",
            run_id="run_1",
            model_name="m1",
            success=False,
            error_message="resource exhausted",
            failure_origin=origin,
            created_at=NOW.isoformat(),
        )
        assert policy.is_availability_failure(response, selection) is False


def test_gateway_unknown_failure_origin_does_not_fallback(tmp_path) -> None:
    gateway, manager = _gateway(tmp_path)
    first = _StubClient(
        "m1",
        lambda request: ModelResponseSchema(
            model_call_id=request.model_call_id,
            task_id=request.task_id,
            run_id=request.run_id,
            model_name="m1",
            success=False,
            error_message="ambiguous failure",
            failure_origin=FailureOrigin.UNKNOWN,
            created_at=request.created_at,
        ),
    )
    second = _StubClient("m2", _success)
    gateway.register_client(first)
    gateway.register_client(second)

    response = gateway.generate(_request())

    assert response.success is False
    assert response.failure_origin is FailureOrigin.UNKNOWN
    assert len(first.calls) == 1
    assert second.calls == []
    assert gateway.usage_snapshot()["availability_fallback_count"] == 0
    token_reservations = [
        item
        for item in manager.canonical_state().reservations
        if item.reservation_ref.reservation_kind == "model_provider_attempt_tokens"
    ]
    assert len(token_reservations) == 1
    assert token_reservations[0].status == "consumed"


def _tool_context(*, authorized: bool = True) -> AgentExecutionContextSchema:
    return AgentExecutionContextSchema(
        invocation_id="research-invocation-1",
        logical_operation_id="research-round-1",
        task_id="task_1",
        run_id="run_1",
        section_id="section-1",
        agent_role=AgentRole.RESEARCH,
        execution_mode="research",
        execution_id="execution-1",
        attempt_id="attempt-1",
        input_snapshot_version="snapshot-1",
        expected_domain_versions={},
        task_context_ref="artifact://task-context/1",
        source_snapshot_ref="artifact://source-snapshot/1",
        execution_plan_ref="artifact://plan/1",
        section_state_ref="artifact://section/1",
        authorized_artifact_refs=[],
        authorized_tool_names=(["internal.search"] if authorized else []),
        writable_state_paths=[],
    )


def test_tool_retry_reuses_one_logical_budget_and_emits_distinct_physical_attempts(
    tmp_path,
) -> None:
    manager = _manager(tmp_path)
    adapter = CanonicalInvocationBudgetAdapter(manager)
    calls = []

    def handler(invocation):
        calls.append(invocation)
        if len(calls) == 1:
            raise TimeoutError("temporary tool timeout")
        return ToolHandlerOutputSchema(
            status=ToolResultStatus.SUCCESS,
            data_type="search_result",
            data={"ids": ["e1"]},
            source_refs=["source://1"],
        )

    gateway = ToolGateway(
        execution_store=InMemoryToolExecutionStore(),
        budget_runtime=adapter,
        authorization_validator=lambda context, registration, arguments: None,
        currentness_validator=lambda context: None,
    )
    gateway.register(
        name="internal.search",
        version="v1",
        handler=handler,
        max_retries=1,
    )

    result = gateway.invoke(
        _tool_context(),
        call_id="display-call-1",
        tool_operation_id="tool-operation-1",
        tool_name="internal.search",
        arguments={"query": "x"},
    )

    assert result.status is ToolResultStatus.SUCCESS
    assert result.tool_operation_id == "tool-operation-1"
    assert len(result.physical_attempt_refs) == 2
    assert len(set(result.physical_attempt_refs)) == 2
    assert [item.tool_operation_id for item in calls] == [
        "tool-operation-1",
        "tool-operation-1",
    ]
    assert calls[0].tool_attempt_id != calls[1].tool_attempt_id

    state = manager.canonical_state()
    assert state.used.logical_tool_calls == 1
    tool_reservations = [
        item
        for item in state.reservations
        if item.reservation_ref.reservation_kind == "logical_tool_call"
    ]
    assert len(tool_reservations) == 1
    assert tool_reservations[0].status == "consumed"
    assert result.budget_reservation_ref == tool_reservations[0].reservation_ref
    assert result.budget_settlement is not None


def test_tool_result_reuse_rechecks_authorization_before_operational_store(tmp_path) -> None:
    manager = _manager(tmp_path)
    calls = []
    store = InMemoryToolExecutionStore()
    gateway = ToolGateway(
        execution_store=store,
        budget_runtime=CanonicalInvocationBudgetAdapter(manager),
        authorization_validator=lambda context, registration, arguments: None,
        currentness_validator=lambda context: None,
    )
    gateway.register(
        name="internal.search",
        version="v1",
        max_retries=0,
        handler=lambda invocation: (
            calls.append(invocation)
            or ToolHandlerOutputSchema(
                status=ToolResultStatus.SUCCESS,
                data_type="search_result",
                data={"ids": ["e1"]},
            )
        ),
    )
    allowed = gateway.invoke(
        _tool_context(authorized=True),
        call_id="display-1",
        tool_operation_id="tool-operation-replay",
        tool_name="internal.search",
        arguments={"query": "x"},
    )
    assert allowed.status is ToolResultStatus.SUCCESS

    denied = gateway.invoke(
        _tool_context(authorized=False),
        call_id="display-2",
        tool_operation_id="tool-operation-replay",
        tool_name="internal.search",
        arguments={"query": "x"},
    )
    assert denied.status is ToolResultStatus.REJECTED
    assert denied.error is not None
    assert denied.error.error_code == "TOOL_PERMISSION_DENIED"
    assert len(calls) == 1
    assert store.get("tool-operation-replay") == allowed
    assert manager.canonical_state().used.logical_tool_calls == 1


def test_tool_result_reuse_rechecks_invocation_grant_before_operational_store(
    tmp_path,
) -> None:
    manager = _manager(tmp_path)
    calls = []
    grant = {"allowed": True}

    def validate_grant(
        context: AgentExecutionContextSchema,
        registration,
        arguments,
    ) -> None:
        assert context.run_id == "run_1"
        assert registration.name == "internal.search"
        assert arguments == {"query": "x"}
        if not grant["allowed"]:
            raise PermissionError("grant revoked")

    store = InMemoryToolExecutionStore()
    gateway = ToolGateway(
        execution_store=store,
        budget_runtime=CanonicalInvocationBudgetAdapter(manager),
        authorization_validator=validate_grant,
        currentness_validator=lambda context: None,
    )
    gateway.register(
        name="internal.search",
        version="v1",
        max_retries=0,
        handler=lambda invocation: (
            calls.append(invocation)
            or ToolHandlerOutputSchema(
                status=ToolResultStatus.SUCCESS,
                data_type="search_result",
                data={"ids": ["e1"]},
            )
        ),
    )

    allowed = gateway.invoke(
        _tool_context(authorized=True),
        call_id="display-grant-1",
        tool_operation_id="tool-operation-grant",
        tool_name="internal.search",
        arguments={"query": "x"},
    )
    assert allowed.status is ToolResultStatus.SUCCESS

    grant["allowed"] = False
    rejected = gateway.invoke(
        _tool_context(authorized=True),
        call_id="display-grant-2",
        tool_operation_id="tool-operation-grant",
        tool_name="internal.search",
        arguments={"query": "x"},
    )

    assert rejected.status is ToolResultStatus.REJECTED
    assert rejected.error is not None
    assert rejected.error.error_code == "TOOL_CAPABILITY_GRANT_REJECTED"
    assert len(calls) == 1
    assert store.get("tool-operation-grant") == allowed
    assert manager.canonical_state().used.logical_tool_calls == 1


def test_tool_result_reuse_rechecks_currentness_before_operational_store(
    tmp_path,
) -> None:
    manager = _manager(tmp_path)
    calls = []
    current = {"allowed": True}

    def validate_currentness(context: AgentExecutionContextSchema) -> None:
        assert context.run_id == "run_1"
        if not current["allowed"]:
            raise RuntimeError("stale attempt")

    store = InMemoryToolExecutionStore()
    gateway = ToolGateway(
        execution_store=store,
        budget_runtime=CanonicalInvocationBudgetAdapter(manager),
        authorization_validator=lambda context, registration, arguments: None,
        currentness_validator=validate_currentness,
    )
    gateway.register(
        name="internal.search",
        version="v1",
        max_retries=0,
        handler=lambda invocation: (
            calls.append(invocation)
            or ToolHandlerOutputSchema(
                status=ToolResultStatus.SUCCESS,
                data_type="search_result",
                data={"ids": ["e1"]},
            )
        ),
    )

    allowed = gateway.invoke(
        _tool_context(authorized=True),
        call_id="display-current-1",
        tool_operation_id="tool-operation-currentness",
        tool_name="internal.search",
        arguments={"query": "x"},
    )
    assert allowed.status is ToolResultStatus.SUCCESS

    current["allowed"] = False
    stale = gateway.invoke(
        _tool_context(authorized=True),
        call_id="display-current-2",
        tool_operation_id="tool-operation-currentness",
        tool_name="internal.search",
        arguments={"query": "x"},
    )

    assert stale.status is ToolResultStatus.STALE
    assert stale.error is not None
    assert stale.error.error_code == "TOOL_CURRENTNESS_REJECTED"
    assert len(calls) == 1
    assert store.get("tool-operation-currentness") == allowed
    assert manager.canonical_state().used.logical_tool_calls == 1


def test_target_tool_operation_without_capability_grant_guard_fails_closed(
    tmp_path,
) -> None:
    manager = _manager(tmp_path)
    calls = []
    store = InMemoryToolExecutionStore()
    gateway = ToolGateway(
        execution_store=store,
        budget_runtime=CanonicalInvocationBudgetAdapter(manager),
        currentness_validator=lambda context: None,
    )
    gateway.register(
        name="internal.search",
        version="v1",
        max_retries=0,
        handler=lambda invocation: calls.append(invocation),
    )

    rejected = gateway.invoke(
        _tool_context(authorized=True),
        call_id="display-no-grant",
        tool_operation_id="tool-operation-no-grant",
        tool_name="internal.search",
        arguments={"query": "x"},
    )

    assert rejected.status is ToolResultStatus.REJECTED
    assert rejected.error is not None
    assert rejected.error.error_code == "TOOL_CAPABILITY_GRANT_REQUIRED"
    assert calls == []
    with pytest.raises(KeyError):
        store.get("tool-operation-no-grant")
    assert manager.canonical_state().used.logical_tool_calls == 0


def test_target_tool_operation_without_currentness_guard_fails_closed(tmp_path) -> None:
    manager = _manager(tmp_path)
    calls = []
    store = InMemoryToolExecutionStore()
    gateway = ToolGateway(
        execution_store=store,
        budget_runtime=CanonicalInvocationBudgetAdapter(manager),
        authorization_validator=lambda context, registration, arguments: None,
    )
    gateway.register(
        name="internal.search",
        version="v1",
        max_retries=0,
        handler=lambda invocation: calls.append(invocation),
    )

    stale = gateway.invoke(
        _tool_context(authorized=True),
        call_id="display-no-guard",
        tool_operation_id="tool-operation-no-guard",
        tool_name="internal.search",
        arguments={"query": "x"},
    )

    assert stale.status is ToolResultStatus.STALE
    assert stale.error is not None
    assert stale.error.error_code == "TOOL_CURRENTNESS_GUARD_REQUIRED"
    assert calls == []
    with pytest.raises(KeyError):
        store.get("tool-operation-no-guard")
    assert manager.canonical_state().used.logical_tool_calls == 0
