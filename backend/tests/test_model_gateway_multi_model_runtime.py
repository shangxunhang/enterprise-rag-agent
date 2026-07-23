"""Acceptance contracts for the role-based multi-model runtime (Stages 1-6)."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Callable

import pytest
from pydantic import ValidationError

from context_manager.manager import ContextBudgetExceededError, LLMContextManager
from contracts.base_client import BaseLLMClient
from apps.enterprise_document.quality.budget import (
    WorkflowBudget,
    activate_workflow_budget,
)
from apps.enterprise_document.quality.model_adapter import (
    reserve_current_workflow_budget,
)
from core.runtime.execution_control import (
    WorkflowExecutionCancelled,
    WorkflowExecutionControl,
    activate_execution_control,
)
from model_gateway.call_boundary import ModelCallBoundary, infer_model_role
from model_gateway.model_contract import (
    ModelProfile,
    ModelRole,
    ModelRoutingConfig,
    ResidencyPolicy,
    RoutingPolicy,
)
from model_gateway.model_gateway import ModelGateway
from model_gateway.model_router import ModelRouter, ModelRoutingError
from model_gateway.provider_factory import ModelProviderFactory
from rag.query.query_expander import QueryExpander
from schemas.model import ModelRequestSchema, ModelResponseSchema, TokenUsageSchema


NOW = datetime.now(timezone.utc).isoformat()


class _StubClient(BaseLLMClient):
    def __init__(
        self,
        model_name: str,
        factory: Callable[[ModelRequestSchema], ModelResponseSchema],
    ) -> None:
        self.model_name = model_name
        self.factory = factory
        self.calls: list[ModelRequestSchema] = []
        self.release_count = 0

    def generate(self, request: ModelRequestSchema) -> ModelResponseSchema:
        self.calls.append(request)
        return self.factory(request)

    def release(self) -> None:
        self.release_count += 1


def _success(
    model_name: str,
    *,
    content: str = "ok",
    prompt_tokens: int = 10,
    completion_tokens: int = 5,
) -> Callable[[ModelRequestSchema], ModelResponseSchema]:
    def build(request: ModelRequestSchema) -> ModelResponseSchema:
        return ModelResponseSchema(
            model_call_id=request.model_call_id,
            task_id=request.task_id,
            run_id=request.run_id,
            model_name=model_name,
            success=True,
            content=content,
            latency_ms=7,
            created_at=request.created_at,
            token_usage=TokenUsageSchema(
                prompt_tokens=prompt_tokens,
                completion_tokens=completion_tokens,
                total_tokens=prompt_tokens + completion_tokens,
            ),
        )

    return build


def _unavailable(model_name: str) -> Callable[[ModelRequestSchema], ModelResponseSchema]:
    def build(request: ModelRequestSchema) -> ModelResponseSchema:
        return ModelResponseSchema(
            model_call_id=request.model_call_id,
            task_id=request.task_id,
            run_id=request.run_id,
            model_name=model_name,
            success=False,
            content="",
            error_message="provider unavailable",
            latency_ms=3,
            created_at=request.created_at,
        )

    return build


def _profile(
    profile_id: str,
    model_name: str,
    *,
    residency: ResidencyPolicy = ResidencyPolicy.PRIMARY,
    enabled: bool = True,
    context_window: int = 32768,
    max_output_tokens: int = 2048,
    input_cost: float | None = None,
    output_cost: float | None = None,
) -> ModelProfile:
    return ModelProfile(
        profile_id=profile_id,
        model_name=model_name,
        provider="test",
        enabled=enabled,
        residency_policy=residency,
        context_window=context_window,
        max_output_tokens=max_output_tokens,
        input_cost_per_million=input_cost,
        output_cost_per_million=output_cost,
    )


def _request(
    *,
    role: str | None = ModelRole.SECTION_GENERATION.value,
    model_name: str | None = None,
) -> ModelRequestSchema:
    return ModelRequestSchema(
        model_call_id="call-1",
        task_id="task-1",
        run_id="run-1",
        model_role=role,
        model_name=model_name,
        caller_agent="test",
        prompt="hello",
        max_tokens=512,
        created_at=NOW,
    )


def _gateway(
    profiles: list[ModelProfile],
    candidates: list[str],
) -> ModelGateway:
    router = ModelRouter(
        profiles[0].model_name,
        profiles=profiles,
        policies=[
            RoutingPolicy(
                role=ModelRole.SECTION_GENERATION,
                candidates=candidates,
            )
        ],
    )
    return ModelGateway(default_model_name=profiles[0].model_name, router=router)


def test_role_resolves_to_primary_profile() -> None:
    profiles = [_profile("p1", "m1"), _profile("p2", "m2")]
    router = ModelRouter(
        "m1",
        profiles=profiles,
        policies=[
            RoutingPolicy(
                role=ModelRole.SECTION_GENERATION,
                candidates=["p2", "p1"],
            )
        ],
    )

    plan = router.plan(_request())

    assert [item.profile_id for item in plan] == ["p2", "p1"]
    assert plan[0].model_name == "m2"


@pytest.mark.parametrize(
    ("purpose", "expected_role"),
    [
        ("agent_section_self_rag_check", ModelRole.RETRIEVAL_JUDGE),
        ("agent_section_local_rewrite", ModelRole.REPAIR),
        ("scheme_section_validation_rewrite", ModelRole.REPAIR),
        ("scheme_section_compression", ModelRole.REPAIR),
    ],
)
def test_quality_call_purpose_maps_to_the_intended_model_role(
    purpose: str,
    expected_role: ModelRole,
) -> None:
    assert infer_model_role(purpose) is expected_role


def test_disabled_profile_is_rejected_and_never_built() -> None:
    disabled = _profile(
        "disabled-7b",
        "local-7b",
        residency=ResidencyPolicy.DISABLED,
        enabled=False,
    )
    router = ModelRouter(
        "local-7b",
        profiles=[disabled],
        policies=[
            RoutingPolicy(
                role=ModelRole.SECTION_GENERATION,
                candidates=["disabled-7b"],
            )
        ],
    )

    with pytest.raises(ModelRoutingError, match="disabled"):
        router.plan(_request(model_name="local-7b"))
    assert ModelProviderFactory().build(disabled, settings=object()) is None


def test_explicit_model_override_bypasses_role_primary_for_tests() -> None:
    profiles = [_profile("p1", "m1"), _profile("p2", "m2")]
    gateway = _gateway(profiles, ["p1", "p2"])
    first = _StubClient("m1", _success("m1"))
    second = _StubClient("m2", _success("m2", content="override"))
    gateway.register_client(first)
    gateway.register_client(second)

    response = gateway.generate(_request(model_name="m2"))

    assert response.content == "override"
    assert len(first.calls) == 0
    assert len(second.calls) == 1
    assert response.metadata["selected_profile"] == "p2"


def test_unknown_role_fails_fast() -> None:
    router = ModelRouter(
        "m1",
        profiles=[_profile("p1", "m1")],
        policies=[RoutingPolicy(role=ModelRole.GENERAL, candidates=["p1"])],
    )

    with pytest.raises(ModelRoutingError, match="unknown model role"):
        router.plan(_request(role="section_genration_typo"))


def test_strict_gateway_config_rejects_typo() -> None:
    with pytest.raises(ValidationError):
        ModelProfile(
            profile_id="p1",
            model_name="m1",
            provider="test",
            context_widow=8192,  # type: ignore[call-arg]
        )

    with pytest.raises(ValidationError):
        ModelRoutingConfig.model_validate(
            {
                "profiles": [
                    {"profile_id": "p1", "model_name": "m1", "provider": "test"}
                ],
                "policies": [
                    {"role": "general", "candidates": ["p1"], "fallbak": True}
                ],
            }
        )


def test_routing_policy_can_disable_availability_fallback() -> None:
    profiles = [_profile("p1", "m1"), _profile("p2", "m2")]
    router = ModelRouter(
        "m1",
        profiles=profiles,
        policies=[
            RoutingPolicy(
                role=ModelRole.SECTION_GENERATION,
                candidates=["p1", "p2"],
                availability_fallback=False,
            )
        ],
    )

    plan = router.plan(_request())

    assert [item.profile_id for item in plan] == ["p1"]


def test_primary_success_does_not_fallback() -> None:
    profiles = [_profile("p1", "m1"), _profile("p2", "m2")]
    gateway = _gateway(profiles, ["p1", "p2"])
    first = _StubClient("m1", _success("m1"))
    second = _StubClient("m2", _success("m2"))
    gateway.register_client(first)
    gateway.register_client(second)

    response = gateway.generate(_request())

    assert response.success is True
    assert len(first.calls) == 1
    assert len(second.calls) == 0
    assert response.metadata["availability_fallback_used"] is False


def test_reused_call_id_still_counts_each_logical_invocation() -> None:
    gateway = _gateway([_profile("p1", "m1")], ["p1"])
    gateway.register_client(_StubClient("m1", _success("m1")))

    gateway.generate(_request())
    gateway.generate(_request())

    usage = gateway.usage_snapshot()
    assert usage["logical_calls"] == 2
    assert usage["unique_logical_call_ids"] == 1
    assert usage["provider_attempts"] == 2


def test_primary_unavailable_falls_back_and_usage_is_aggregated() -> None:
    profiles = [
        _profile("p1", "m1"),
        _profile(
            "p2",
            "m2",
            input_cost=1.0,
            output_cost=2.0,
        ),
    ]
    gateway = _gateway(profiles, ["p1", "p2"])
    first = _StubClient("m1", _unavailable("m1"))
    second = _StubClient(
        "m2",
        _success("m2", prompt_tokens=100, completion_tokens=20),
    )
    gateway.register_client(first)
    gateway.register_client(second)

    response = gateway.generate(_request())
    usage = gateway.usage_snapshot()

    assert response.success is True
    assert response.model_name == "m2"
    assert response.metadata["availability_fallback_used"] is True
    assert response.metadata["availability_fallback_from"] == ["p1"]
    assert len(first.calls) == len(second.calls) == 1
    assert usage["calls_by_model"] == {"m1": 1, "m2": 1}
    assert usage["logical_calls"] == 1
    assert usage["provider_attempts"] == 2
    assert usage["budget_semantics"] == "logical_model_call_v1"
    assert usage["availability_fallback_count"] == 1
    assert usage["failures"] == 1
    assert usage["prompt_tokens"] == 100
    assert usage["completion_tokens"] == 20
    assert usage["total_tokens"] == 120
    assert usage["latency_ms"] == 10
    assert usage["cost_if_available"] == pytest.approx(0.00014)


def test_cancellation_after_provider_return_records_attempt_without_fallback() -> None:
    profiles = [_profile("p1", "m1"), _profile("p2", "m2")]
    gateway = _gateway(profiles, ["p1", "p2"])
    control = WorkflowExecutionControl.with_timeout(
        execution_id="execution-1",
        timeout_seconds=10,
    )

    def cancel_then_return(request: ModelRequestSchema) -> ModelResponseSchema:
        control.cancel("deadline_exceeded")
        return _success(
            "m1",
            prompt_tokens=11,
            completion_tokens=7,
        )(request)

    first = _StubClient("m1", cancel_then_return)
    second = _StubClient("m2", _success("m2"))
    gateway.register_client(first)
    gateway.register_client(second)

    with activate_execution_control(control):
        with pytest.raises(WorkflowExecutionCancelled):
            gateway.generate(_request())

    usage = gateway.usage_snapshot()
    assert len(first.calls) == 1
    assert len(second.calls) == 0
    assert usage["logical_calls"] == 1
    assert usage["provider_attempts"] == 1
    assert usage["calls_by_model"] == {"m1": 1}
    assert usage["prompt_tokens"] == 11
    assert usage["completion_tokens"] == 7
    assert usage["total_tokens"] == 18
    assert usage["cancelled_attempts"] == 1
    assert usage["incomplete_usage_attempts"] == 0
    assert usage["failures"] == 1
    assert usage["availability_fallback_count"] == 0


def test_workflow_budget_counts_one_logical_call_across_provider_fallback() -> None:
    profiles = [_profile("p1", "m1"), _profile("p2", "m2")]
    gateway = _gateway(profiles, ["p1", "p2"])
    gateway.register_client(_StubClient("m1", _unavailable("m1")))
    gateway.register_client(_StubClient("m2", _success("m2")))
    budget = WorkflowBudget(
        max_retrieval_rounds=1,
        max_rewrite_rounds=1,
        max_total_llm_calls=1,
        max_total_tokens=1024,
    )
    boundary = ModelCallBoundary(
        model_gateway=gateway,
        model_role=ModelRole.SECTION_GENERATION,
        runtime_context={"task_id": "task-1", "workflow_run_id": "run-1"},
        budget_hook=reserve_current_workflow_budget,
    )

    with activate_workflow_budget(budget):
        response = boundary.generate_response(
            "hello",
            model_call_id="logical-call-1",
            max_new_tokens=128,
        )

    assert response.success is True
    assert budget.llm_calls == 1
    assert budget.snapshot()["logical_model_calls"] == 1
    assert gateway.usage_snapshot()["logical_calls"] == 1
    assert gateway.usage_snapshot()["provider_attempts"] == 2


def test_successful_low_quality_response_never_gateway_fallbacks() -> None:
    profiles = [_profile("p1", "m1"), _profile("p2", "m2")]
    gateway = _gateway(profiles, ["p1", "p2"])
    first = _StubClient("m1", _success("m1", content="low-quality-but-valid-response"))
    second = _StubClient("m2", _success("m2", content="better"))
    gateway.register_client(first)
    gateway.register_client(second)

    response = gateway.generate(_request())

    assert response.content == "low-quality-but-valid-response"
    assert len(first.calls) == 1
    assert len(second.calls) == 0
    assert gateway.usage_snapshot()["availability_fallback_count"] == 0


def test_on_demand_profile_is_released_after_call() -> None:
    profile = _profile(
        "on-demand",
        "m1",
        residency=ResidencyPolicy.ON_DEMAND,
    )
    gateway = _gateway([profile], ["on-demand"])
    client = _StubClient("m1", _success("m1"))
    gateway.register_client(client)

    response = gateway.generate(_request())

    assert response.success is True
    assert client.release_count == 1


def test_fallback_safe_capabilities_use_smallest_candidate_window() -> None:
    profiles = [
        _profile("p1", "m1", context_window=32768, max_output_tokens=4096),
        _profile("p2", "m2", context_window=8192, max_output_tokens=1024),
    ]
    gateway = _gateway(profiles, ["p1", "p2"])

    capabilities = gateway.routing_capabilities(
        model_role=ModelRole.SECTION_GENERATION.value
    )

    assert capabilities["candidate_profiles"] == ["p1", "p2"]
    assert capabilities["safe_context_window"] == 8192
    assert capabilities["safe_max_output_tokens"] == 1024
    assert capabilities["primary_context_window"] == 32768


def test_passthrough_rejects_content_over_selected_model_token_capacity() -> None:
    with pytest.raises(ContextBudgetExceededError, match="selected-model token capacity"):
        LLMContextManager().build_passthrough(
            task_id="task-1",
            run_id="run-1",
            call_purpose="repair",
            content="x" * 5000,
            max_input_tokens=512,
            reserved_output_tokens=128,
        )


def test_rag_query_rewrite_preserves_runtime_lineage_through_call_boundary() -> None:
    recorded: list[ModelRequestSchema] = []

    class _RecordingGateway:
        def generate(self, request: ModelRequestSchema) -> ModelResponseSchema:
            recorded.append(request)
            return ModelResponseSchema(
                model_call_id=request.model_call_id,
                task_id=request.task_id,
                run_id=request.run_id,
                model_name="m1",
                success=True,
                content="政务云安全合规",
                created_at=request.created_at,
            )

    expander = QueryExpander(
        llm_generator=ModelCallBoundary(model_gateway=_RecordingGateway()),
        use_llm=True,
        fallback_to_deterministic=False,
    )
    rewrites, _ = expander.rewrite_queries(
        query="政务云建设方案",
        num_rewrites=1,
        runtime_context={
            "task_id": "task-rag",
            "workflow_run_id": "run-workflow",
            "section_id": "section-1",
            "rag_run_id": "rag-run-1",
            "retrieval_trace_id": "trace-rag-1",
            "retrieval_scope": "section",
        },
    )

    assert rewrites == ["政务云安全合规"]
    request = recorded[0]
    assert request.model_role == ModelRole.QUERY_REWRITE.value
    assert request.task_id == "task-rag"
    assert request.run_id == "run-workflow"
    assert request.extra["section_id"] == "section-1"
    assert request.extra["rag_run_id"] == "rag-run-1"
    assert request.extra["retrieval_trace_id"] == "trace-rag-1"
    assert request.extra["retrieval_scope"] == "section"


def test_rag_deterministic_fallback_never_swallows_cancellation() -> None:
    class _CancelledGenerator:
        def generate(self, *_args, **_kwargs) -> str:
            raise WorkflowExecutionCancelled("cancel query rewrite")

    expander = QueryExpander(
        llm_generator=_CancelledGenerator(),
        use_llm=True,
        fallback_to_deterministic=True,
    )

    with pytest.raises(WorkflowExecutionCancelled):
        expander.rewrite_queries(query="政务云建设方案", num_rewrites=1)


def test_quality_escalation_is_accounted_separately_from_availability_fallback() -> None:
    profile = _profile("p1", "m1")
    gateway = _gateway([profile], ["p1"])
    gateway.register_client(_StubClient("m1", _success("m1")))

    gateway.generate(_request())
    gateway.record_quality_escalation()
    usage = gateway.usage_snapshot()

    assert usage["quality_escalation_count"] == 1
    assert usage["availability_fallback_count"] == 0
