"""Run opt-in acceptance against one real OpenAI-compatible chat provider."""

from __future__ import annotations

import argparse
import asyncio
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import sys
from threading import Lock
from typing import Any


PROJECT_ROOT = Path(__file__).resolve().parents[1]
BACKEND_ROOT = PROJECT_ROOT / "backend"
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from langchain_core.callbacks import BaseCallbackHandler
from langchain_openai import ChatOpenAI

from contracts.base_client import BaseLLMClient
from data_capture.run_trace_recorder import JsonlRunTraceRecorder
from model_gateway.integrations.langchain.chat_model_client import (
    LangChainChatModelClient,
)
from model_gateway.integrations.langchain.runtime_facade import (
    LangChainModelGatewayFacade,
)
from model_gateway.integrations.langchain.runnable import (
    build_model_gateway_runnable,
)
from model_gateway.model_contract import (
    ModelProfile,
    ModelRole,
    ResidencyPolicy,
    RoutingPolicy,
)
from model_gateway.model_gateway import ModelGateway
from model_gateway.model_router import ModelRouter
from schemas.model import ModelRequestSchema, ModelResponseSchema


_ENABLED_VALUES = {"1", "true", "yes", "on"}
_RAW_PROVIDER_FIELDS = {
    "provider",
    "response_metadata",
    "additional_kwargs",
    "langchain_message_type",
}
_REASONING_FIELDS = {
    "reasoning",
    "reasoning_content",
    "reasoning_details",
    "reasoning_tokens",
}


class _UnavailableClient(BaseLLMClient):
    model_name = "acceptance_unavailable"

    def generate(self, request: ModelRequestSchema) -> ModelResponseSchema:
        raise ConnectionError("provider unavailable: acceptance fallback probe")


class _CountingCallback(BaseCallbackHandler):
    def __init__(self) -> None:
        self.starts = 0
        self.ends = 0
        self.errors = 0
        self._lock = Lock()

    def on_chain_start(self, *args: Any, **kwargs: Any) -> None:
        with self._lock:
            self.starts += 1

    def on_chain_end(self, *args: Any, **kwargs: Any) -> None:
        with self._lock:
            self.ends += 1

    def on_chain_error(self, *args: Any, **kwargs: Any) -> None:
        with self._lock:
            self.errors += 1


def _required_environment() -> dict[str, str]:
    if os.getenv("REAL_PROVIDER_ACCEPTANCE", "").strip().lower() not in (
        _ENABLED_VALUES
    ):
        raise RuntimeError(
            "real provider acceptance is disabled; set "
            "REAL_PROVIDER_ACCEPTANCE=1 explicitly"
        )
    required = ("MODEL_PROVIDER", "MODEL_API_KEY", "MODEL_BASE_URL", "MODEL_NAME")
    values = {name: os.getenv(name, "").strip() for name in required}
    missing = [name for name, value in values.items() if not value]
    if missing:
        raise RuntimeError(
            "missing real-provider configuration: " + ", ".join(missing)
        )
    if values["MODEL_PROVIDER"].lower() not in {
        "openai",
        "openai_compatible",
    }:
        raise RuntimeError(
            "MODEL_PROVIDER must be openai or openai_compatible"
        )
    return values


def _request(index: int) -> ModelRequestSchema:
    return ModelRequestSchema(
        model_call_id=f"real_provider_acceptance_{index}",
        task_id="task_real_provider_acceptance",
        run_id="run_real_provider_acceptance",
        model_role=ModelRole.GENERAL.value,
        caller_agent="RealProviderAcceptance",
        prompt=(
            "Reply with a short sentence containing the exact marker "
            "REAL_PROVIDER_ACCEPTANCE_OK."
        ),
        system_prompt="You are a concise connectivity acceptance assistant.",
        temperature=0.0,
        max_tokens=96,
        created_at=datetime.now(timezone.utc).isoformat(),
        extra={"call_purpose": "real_provider_acceptance"},
    )


def _provider_specific_fields_status(
    responses: list[ModelResponseSchema],
) -> str:
    """Verify the raw provider envelope and report optional reasoning support."""

    reasoning_returned = False
    for response in responses:
        raw_output = response.raw_output
        if not isinstance(raw_output, dict):
            return "unsupported"
        missing = _RAW_PROVIDER_FIELDS - set(raw_output)
        if missing:
            raise AssertionError(
                "raw provider fields were not preserved: "
                + ", ".join(sorted(missing))
            )
        for container_name in ("response_metadata", "additional_kwargs"):
            container = raw_output.get(container_name)
            if not isinstance(container, dict):
                raise AssertionError(
                    f"raw_output.{container_name} must be a mapping"
                )
            if _REASONING_FIELDS & set(container):
                reasoning_returned = True
    return "preserved" if reasoning_returned else "not_returned_by_provider"


def _assert_secret_absent(value: Any, *, secret: str) -> None:
    serialized = json.dumps(value, ensure_ascii=False, default=str)
    if secret and secret in serialized:
        raise AssertionError("acceptance output contains the configured API key")
    if "authorization:" in serialized.lower():
        raise AssertionError("acceptance output contains an Authorization header")


def _build_gateway(
    *,
    values: dict[str, str],
    trace_sink: JsonlRunTraceRecorder,
    timeout_seconds: float,
) -> ModelGateway:
    real_profile = ModelProfile(
        profile_id="acceptance_real",
        model_name="acceptance_real",
        provider="openai_compatible",
        provider_model_name=values["MODEL_NAME"],
        residency_policy=ResidencyPolicy.REMOTE,
        context_window=8192,
        max_output_tokens=512,
        timeout_seconds=timeout_seconds,
        metadata={"acceptance_only": True},
    )
    unavailable_profile = ModelProfile(
        profile_id="acceptance_unavailable",
        model_name="acceptance_unavailable",
        provider="acceptance_probe",
        residency_policy=ResidencyPolicy.REMOTE,
        context_window=8192,
        max_output_tokens=512,
        timeout_seconds=timeout_seconds,
    )
    router = ModelRouter(
        real_profile.model_name,
        profiles=[unavailable_profile, real_profile],
        policies=[
            RoutingPolicy(
                role=ModelRole.GENERAL,
                candidates=[
                    unavailable_profile.profile_id,
                    real_profile.profile_id,
                ],
                availability_fallback=True,
            )
        ],
    )
    gateway = ModelGateway(
        default_model_name=real_profile.model_name,
        run_trace_recorder=trace_sink,
        router=router,
    )
    gateway.register_client(_UnavailableClient())
    chat_model = ChatOpenAI(
        model=values["MODEL_NAME"],
        api_key=values["MODEL_API_KEY"],
        base_url=values["MODEL_BASE_URL"],
        timeout=timeout_seconds,
        max_retries=0,
    )
    gateway.register_client(
        LangChainChatModelClient(
            model_name=real_profile.model_name,
            chat_model=chat_model,
            provider_name=values["MODEL_PROVIDER"].lower(),
        )
    )
    return gateway


def run_acceptance(
    output_dir: Path,
    *,
    timeout_seconds: float = 30.0,
) -> dict[str, Any]:
    """Execute real sync/async/stream calls and assert canonical accounting."""

    values = _required_environment()
    output_dir.mkdir(parents=True, exist_ok=True)
    trace_sink = JsonlRunTraceRecorder(output_dir)
    gateway = _build_gateway(
        values=values,
        trace_sink=trace_sink,
        timeout_seconds=timeout_seconds,
    )
    facade = LangChainModelGatewayFacade(
        delegate=gateway,
        runnable=build_model_gateway_runnable(gateway),
    )
    callback = _CountingCallback()
    config = {
        "callbacks": [callback],
        "tags": ["real-provider-acceptance"],
        "metadata": {"acceptance_mode": "explicit_opt_in"},
    }
    try:
        sync_response = facade.invoke(_request(1), config=config)

        async def call_async() -> ModelResponseSchema:
            return await facade.ainvoke(_request(2), config=config)

        async_response = asyncio.run(call_async())
        stream_items = list(facade.stream(_request(3), config=config))
        if len(stream_items) != 1:
            raise AssertionError(
                "canonical gateway stream must emit one final ModelResponseSchema"
            )
        stream_response = stream_items[0]
        responses = [sync_response, async_response, stream_response]
        for response in responses:
            if not response.success:
                raise AssertionError(
                    f"real provider call failed: {response.error_message}"
                )
            if not response.content.strip():
                raise AssertionError("real provider returned empty content")
            if "REAL_PROVIDER_ACCEPTANCE_OK" not in response.content:
                raise AssertionError(
                    "real provider response did not preserve the acceptance marker"
                )
            if (
                response.token_usage.total_tokens is None
                or response.token_usage.total_tokens <= 0
            ):
                raise AssertionError(
                    "provider did not return positive token usage"
                )
            if response.metadata.get("provider") != "openai_compatible":
                raise AssertionError("gateway provider metadata was not preserved")
            if not response.metadata.get("availability_fallback_used"):
                raise AssertionError("availability fallback probe was not recorded")
        provider_specific_fields_status = _provider_specific_fields_status(
            responses
        )

        usage = gateway.usage_snapshot()
        if usage["logical_calls"] != 3 or usage["provider_attempts"] != 6:
            raise AssertionError("logical/provider attempt accounting mismatch")
        if callback.errors or callback.starts != callback.ends:
            raise AssertionError("LangChain callback lifecycle is incomplete")
        trace_path = (
            output_dir / "run_real_provider_acceptance_trace.jsonl"
        )
        trace_events = [
            json.loads(line)
            for line in trace_path.read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]
        event_types = [item.get("event_type") for item in trace_events]
        if event_types.count("model_started") != 6:
            raise AssertionError("trace does not contain every provider attempt")
        if event_types.count("model_finished") != 6:
            raise AssertionError("trace does not close every provider attempt")

        error_gateway = ModelGateway(
            default_model_name=_UnavailableClient.model_name
        )
        error_gateway.register_client(_UnavailableClient())
        normalized_error = error_gateway.generate(
            _request(99).model_copy(
                update={
                    "model_name": _UnavailableClient.model_name,
                    "model_role": None,
                }
            )
        )
        if normalized_error.success or normalized_error.error is None:
            raise AssertionError("provider error was not normalized")

        summary = {
            "schema_version": "real_provider_acceptance_v1",
            "provider": values["MODEL_PROVIDER"].lower(),
            "provider_model": values["MODEL_NAME"],
            "base_url_configured": True,
            "timeout_seconds": timeout_seconds,
            "max_retries": 0,
            "stream_semantics": "canonical_final_response",
            "cancellation_semantics": "cooperative_gateway_boundary",
            "responses": [
                {
                    "model_call_id": item.model_call_id,
                    "content_preview": item.content[:160],
                    "finish_reason": item.finish_reason,
                    "token_usage": item.token_usage.model_dump(mode="json"),
                    "provider_metadata": item.metadata,
                }
                for item in responses
            ],
            "usage": usage,
            "callback_counts": {
                "starts": callback.starts,
                "ends": callback.ends,
                "errors": callback.errors,
            },
            "trace_event_count": len(trace_events),
            "marker_verified": True,
            "fallback_verified": True,
            "normalized_error_verified": True,
            "provider_specific_fields_status": (
                provider_specific_fields_status
            ),
        }
        _assert_secret_absent(summary, secret=values["MODEL_API_KEY"])
        _assert_secret_absent(trace_events, secret=values["MODEL_API_KEY"])
        return summary
    finally:
        facade.close()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=PROJECT_ROOT / "data" / "real_provider_acceptance",
    )
    parser.add_argument("--timeout-seconds", type=float, default=30.0)
    args = parser.parse_args()
    summary = run_acceptance(
        args.output_dir.resolve(),
        timeout_seconds=args.timeout_seconds,
    )
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
