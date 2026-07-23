"""Lifecycle, concurrency and cancellation contracts for local model runtime."""

from __future__ import annotations

from contextlib import nullcontext
from datetime import datetime, timezone
from types import SimpleNamespace
import sys
import threading

import pytest

from contracts.base_client import BaseLLMClient
from core.runtime.execution_control import (
    WorkflowExecutionCancelled,
    WorkflowExecutionControl,
    activate_execution_control,
)
from model_gateway.local_hf_runtime import LocalHuggingFaceRuntime
from model_gateway.model_contract import ModelProfile, ModelRole, RoutingPolicy
from model_gateway.model_gateway import ModelGateway
from model_gateway.model_invoker import ModelInvoker
from model_gateway.model_registry import ModelRegistry
from model_gateway.model_router import ModelRouter
from schemas.model import ModelRequestSchema, ModelResponseSchema


def _request(model_call_id: str = "call-1") -> ModelRequestSchema:
    return ModelRequestSchema(
        model_call_id=model_call_id,
        task_id="task-1",
        run_id="run-1",
        model_role=ModelRole.SECTION_GENERATION.value,
        prompt="hello",
        created_at=datetime.now(timezone.utc).isoformat(),
    )


class _BlockingClient(BaseLLMClient):
    model_name = "m1"

    def __init__(self) -> None:
        self.started = threading.Event()
        self.proceed = threading.Event()
        self.release_count = 0

    def generate(self, request: ModelRequestSchema) -> ModelResponseSchema:
        self.started.set()
        if not self.proceed.wait(timeout=2):
            raise TimeoutError("test client was not released")
        return ModelResponseSchema(
            model_call_id=request.model_call_id,
            task_id=request.task_id,
            run_id=request.run_id,
            model_name=self.model_name,
            success=True,
            content="ok",
            created_at=request.created_at,
        )

    def release(self) -> None:
        self.release_count += 1


def _gateway(client: BaseLLMClient) -> ModelGateway:
    profile = ModelProfile(
        profile_id="p1",
        model_name=client.model_name,
        provider="test",
    )
    gateway = ModelGateway(
        default_model_name=client.model_name,
        router=ModelRouter(
            client.model_name,
            profiles=[profile],
            policies=[
                RoutingPolicy(
                    role=ModelRole.SECTION_GENERATION,
                    candidates=[profile.profile_id],
                )
            ],
        ),
    )
    gateway.register_client(client)
    return gateway


def test_gateway_close_waits_for_active_call_and_is_idempotent() -> None:
    client = _BlockingClient()
    gateway = _gateway(client)
    responses: list[ModelResponseSchema] = []
    call_thread = threading.Thread(
        target=lambda: responses.append(gateway.generate(_request())),
    )
    call_thread.start()
    assert client.started.wait(timeout=1)

    close_done = threading.Event()

    def close_gateway() -> None:
        gateway.close()
        close_done.set()

    close_thread = threading.Thread(target=close_gateway)
    close_thread.start()
    assert not close_done.wait(timeout=0.05)

    client.proceed.set()
    call_thread.join(timeout=1)
    close_thread.join(timeout=1)

    assert not call_thread.is_alive()
    assert not close_thread.is_alive()
    assert responses[0].success is True
    assert client.release_count == 1
    assert gateway.usage_snapshot()["logical_calls"] == 1

    gateway.close()
    assert client.release_count == 1
    with pytest.raises(RuntimeError, match="ModelGateway is closed"):
        gateway.generate(_request("call-after-close"))


def _fake_torch_module() -> SimpleNamespace:
    return SimpleNamespace(
        cuda=SimpleNamespace(
            is_available=lambda: False,
            empty_cache=lambda: None,
        ),
        float16=object(),
        float32=object(),
        no_grad=lambda: nullcontext(),
    )


def test_local_runtime_loads_once_under_concurrent_access(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    counts = {"tokenizer": 0, "model": 0}
    count_lock = threading.Lock()

    class FakeTokenizerFactory:
        @classmethod
        def from_pretrained(cls, *_args, **_kwargs):
            with count_lock:
                counts["tokenizer"] += 1
            return object()

    class FakeModel:
        hf_device_map = None

        def to(self, _device: str) -> None:
            return None

        def eval(self) -> None:
            return None

    class FakeModelFactory:
        @classmethod
        def from_pretrained(cls, *_args, **_kwargs):
            with count_lock:
                counts["model"] += 1
            return FakeModel()

    monkeypatch.setitem(sys.modules, "torch", _fake_torch_module())
    monkeypatch.setitem(
        sys.modules,
        "transformers",
        SimpleNamespace(
            AutoTokenizer=FakeTokenizerFactory,
            AutoModelForCausalLM=FakeModelFactory,
        ),
    )
    runtime = LocalHuggingFaceRuntime(tmp_path, device="cpu")
    barrier = threading.Barrier(6)
    errors: list[BaseException] = []

    def load() -> None:
        try:
            barrier.wait(timeout=1)
            runtime.ensure_loaded()
        except BaseException as exc:  # pragma: no cover - assertion aid
            errors.append(exc)

    workers = [threading.Thread(target=load) for _ in range(6)]
    for worker in workers:
        worker.start()
    for worker in workers:
        worker.join(timeout=1)

    assert errors == []
    assert all(not worker.is_alive() for worker in workers)
    assert counts == {"tokenizer": 1, "model": 1}
    assert runtime.is_loaded is True


class _FakeTensor:
    def to(self, _device: str):
        return self


class _FakeTokenizer:
    def __call__(self, _text: str, *, return_tensors: str):
        assert return_tensors == "pt"
        return {"input_ids": _FakeTensor()}


class _BlockingModel:
    def __init__(
        self,
        *,
        started: threading.Event,
        proceed: threading.Event,
        cancel: WorkflowExecutionControl | None = None,
    ) -> None:
        self.started = started
        self.proceed = proceed
        self.cancel = cancel

    def parameters(self):
        return iter([SimpleNamespace(device="cpu")])

    def generate(self, **_kwargs):
        self.started.set()
        if self.cancel is not None:
            self.cancel.cancel("test_cancelled_during_native_generate")
        if not self.proceed.wait(timeout=2):
            raise TimeoutError("test model was not released")
        return "output"


def test_local_runtime_unload_waits_for_generation(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setitem(sys.modules, "torch", _fake_torch_module())
    started = threading.Event()
    proceed = threading.Event()
    runtime = LocalHuggingFaceRuntime(tmp_path, device="cpu")
    runtime.tokenizer = _FakeTokenizer()
    runtime.model = _BlockingModel(started=started, proceed=proceed)

    generation_done = threading.Event()
    generation_thread = threading.Thread(
        target=lambda: (
            runtime.generate("prompt", {}),
            generation_done.set(),
        )
    )
    generation_thread.start()
    assert started.wait(timeout=1)

    unload_done = threading.Event()

    def unload() -> None:
        runtime.unload()
        unload_done.set()

    unload_thread = threading.Thread(target=unload)
    unload_thread.start()
    assert not unload_done.wait(timeout=0.05)

    proceed.set()
    generation_thread.join(timeout=1)
    unload_thread.join(timeout=1)
    assert generation_done.is_set()
    assert unload_done.is_set()
    assert runtime.is_loaded is False


def test_native_generate_defers_post_call_cancellation_to_gateway(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setitem(sys.modules, "torch", _fake_torch_module())
    control = WorkflowExecutionControl.with_timeout(
        execution_id="execution-1",
        timeout_seconds=10,
    )
    started = threading.Event()
    proceed = threading.Event()
    proceed.set()
    runtime = LocalHuggingFaceRuntime(tmp_path, device="cpu")
    runtime.tokenizer = _FakeTokenizer()
    runtime.model = _BlockingModel(
        started=started,
        proceed=proceed,
        cancel=control,
    )

    with activate_execution_control(control):
        _, output_ids, model_device = runtime.generate("prompt", {})

    assert output_ids == "output"
    assert model_device == "cpu"
    assert control.is_cancelled is True

    class CancelledClient(BaseLLMClient):
        model_name = "cancelled-model"

        def generate(self, request: ModelRequestSchema) -> ModelResponseSchema:
            raise WorkflowExecutionCancelled(request.model_call_id)

    registry = ModelRegistry()
    registry.register(CancelledClient())
    with pytest.raises(WorkflowExecutionCancelled):
        ModelInvoker(registry).invoke(_request(), "cancelled-model")

    gateway = _gateway(CancelledClient())
    with pytest.raises(WorkflowExecutionCancelled):
        gateway.generate(_request("cancelled-gateway-call"))
    usage = gateway.usage_snapshot()
    assert usage["logical_calls"] == 1
    assert usage["provider_attempts"] == 1
    assert usage["calls_by_model"] == {"cancelled-model": 1}
    assert usage["failures"] == 1
    assert usage["availability_fallback_count"] == 0
