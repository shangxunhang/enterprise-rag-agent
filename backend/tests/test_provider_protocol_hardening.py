"""Deterministic OpenAI-compatible protocol tests over the production adapter."""

from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
from threading import Thread
import time
from typing import Iterator

from langchain_core.callbacks import BaseCallbackHandler
from langchain_openai import ChatOpenAI
import pytest

from data_capture.run_trace_recorder import JsonlRunTraceRecorder
from model_gateway.integrations.langchain import (
    LangChainChatModelClient,
    LangChainModelGatewayFacade,
    build_model_gateway_runnable,
)
from model_gateway.model_gateway import ModelGateway
from schemas.failure import FailureCategory
from schemas.model import ModelRequestSchema


_SECRET = "sk-protocol-secret-123456789"
_MARKER = "REAL_PROVIDER_ACCEPTANCE_OK"


class _CallbackLifecycle(BaseCallbackHandler):
    def __init__(self) -> None:
        self.starts = 0
        self.ends = 0
        self.errors = 0

    def on_llm_start(self, *args, **kwargs) -> None:
        self.starts += 1

    def on_llm_end(self, *args, **kwargs) -> None:
        self.ends += 1

    def on_llm_error(self, *args, **kwargs) -> None:
        self.errors += 1


class _OpenAIProtocolHandler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, format: str, *args) -> None:
        # Never write request headers, credentials or intentionally induced
        # provider errors to stdout/stderr.
        return

    def _write_json(self, status: int, payload: dict) -> None:
        body = json.dumps(payload).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        try:
            self.wfile.write(body)
        except (BrokenPipeError, ConnectionResetError):
            # Expected when the timeout fixture closes its connection.
            pass

    def do_POST(self) -> None:
        content_length = int(self.headers.get("Content-Length", "0"))
        request_body = self.rfile.read(content_length)
        request = json.loads(request_body)
        mode = str(request.get("model") or "")

        if mode == "protocol-timeout":
            time.sleep(0.25)
            self._write_json(200, self._success_payload(mode))
            return
        if mode == "protocol-invalid-json":
            body = b"{invalid-json"
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        if mode == "protocol-missing-choices":
            self._write_json(
                200,
                {
                    "id": "chatcmpl-missing-choices",
                    "object": "chat.completion",
                    "created": 1785024000,
                    "model": mode,
                },
            )
            return

        status_by_mode = {
            "protocol-401": 401,
            "protocol-429": 429,
            "protocol-500": 500,
        }
        status = status_by_mode.get(mode)
        if status is not None:
            authorization = self.headers.get("Authorization", "")
            self._write_json(
                status,
                {
                    "error": {
                        "message": (
                            f"local protocol failure; Authorization: "
                            f"{authorization}"
                        ),
                        "type": "local_protocol_error",
                        "code": f"local_{status}",
                    }
                },
            )
            return

        self._write_json(200, self._success_payload(mode))

    @staticmethod
    def _success_payload(model: str) -> dict:
        return {
            "id": "chatcmpl-local-protocol",
            "object": "chat.completion",
            "created": 1785024000,
            "model": model,
            "choices": [
                {
                    "index": 0,
                    "message": {
                        "role": "assistant",
                        "content": _MARKER,
                    },
                    "finish_reason": "stop",
                }
            ],
            "usage": {
                "prompt_tokens": 5,
                "completion_tokens": 3,
                "total_tokens": 8,
            },
            "system_fingerprint": "local-protocol-fixture",
        }


@contextmanager
def _openai_protocol_server() -> Iterator[str]:
    server = ThreadingHTTPServer(("127.0.0.1", 0), _OpenAIProtocolHandler)
    worker = Thread(target=server.serve_forever, daemon=True)
    worker.start()
    try:
        host, port = server.server_address
        yield f"http://{host}:{port}/v1"
    finally:
        server.shutdown()
        server.server_close()
        worker.join(timeout=2.0)


def _request(model_name: str) -> ModelRequestSchema:
    return ModelRequestSchema(
        model_call_id=f"call-{model_name}",
        task_id="task-provider-protocol",
        run_id=f"run-{model_name}",
        model_name=model_name,
        caller_agent="ProviderProtocolHardeningTest",
        prompt=f"Return exactly {_MARKER}",
        temperature=0.0,
        max_tokens=32,
        created_at=datetime.now(timezone.utc).isoformat(),
        extra={"call_purpose": "local_openai_protocol_acceptance"},
    )


def _runtime(
    *,
    base_url: str,
    model_name: str,
    callback: _CallbackLifecycle,
    trace_root: Path,
) -> tuple[LangChainModelGatewayFacade, ModelGateway]:
    chat_model = ChatOpenAI(
        model=model_name,
        api_key=_SECRET,
        base_url=base_url,
        max_retries=0,
        timeout=0.05 if model_name == "protocol-timeout" else 2.0,
        callbacks=[callback],
    )
    recorder = JsonlRunTraceRecorder(trace_root)
    gateway = ModelGateway(
        default_model_name=model_name,
        run_trace_recorder=recorder,
    )
    gateway.register_client(
        LangChainChatModelClient(
            model_name=model_name,
            chat_model=chat_model,
            provider_name="local_openai_protocol",
        )
    )
    return (
        LangChainModelGatewayFacade(
            delegate=gateway,
            runnable=build_model_gateway_runnable(gateway),
        ),
        gateway,
    )


def _trace_text(trace_root: Path, run_id: str) -> str:
    return (trace_root / f"{run_id}_trace.jsonl").read_text(encoding="utf-8")


def test_local_openai_protocol_success_preserves_marker_usage_and_metadata(
    tmp_path,
) -> None:
    callback = _CallbackLifecycle()
    with _openai_protocol_server() as base_url:
        facade, gateway = _runtime(
            base_url=base_url,
            model_name="protocol-normal",
            callback=callback,
            trace_root=tmp_path,
        )
        try:
            responses = list(facade.stream(_request("protocol-normal")))
            usage = gateway.usage_snapshot()
        finally:
            facade.close()

    assert len(responses) == 1
    response = responses[0]
    assert response.success is True
    assert _MARKER in response.content
    assert response.token_usage.prompt_tokens == 5
    assert response.token_usage.completion_tokens == 3
    assert response.token_usage.total_tokens == 8
    assert set(response.raw_output) >= {
        "provider",
        "response_metadata",
        "additional_kwargs",
        "langchain_message_type",
    }
    assert response.raw_output["provider"] == "local_openai_protocol"
    assert (
        response.raw_output["response_metadata"]["system_fingerprint"]
        == "local-protocol-fixture"
    )
    assert facade.stream_semantics == "canonical_final_response"
    assert facade.supports_token_streaming is False
    assert facade.supports_native_async is False
    assert usage["logical_calls"] == 1
    assert usage["provider_attempts"] == 1
    assert callback.starts == 1
    assert callback.ends == 1
    assert callback.errors == 0

    trace = _trace_text(tmp_path, "run-protocol-normal")
    assert trace.count('"event_type": "model_started"') == 1
    assert trace.count('"event_type": "model_finished"') == 1
    assert _SECRET not in trace
    assert "Authorization: Bearer" not in trace


@pytest.mark.parametrize(
    ("model_name", "expected_category", "retryable"),
    [
        ("protocol-401", FailureCategory.CONFIGURATION, False),
        ("protocol-429", FailureCategory.TRANSIENT, True),
        ("protocol-500", FailureCategory.AVAILABILITY, True),
        ("protocol-timeout", FailureCategory.TIMEOUT, True),
        ("protocol-invalid-json", FailureCategory.VALIDATION, False),
        ("protocol-missing-choices", FailureCategory.VALIDATION, False),
    ],
)
def test_local_openai_protocol_errors_are_normalized_closed_and_redacted(
    tmp_path,
    capsys,
    model_name,
    expected_category,
    retryable,
) -> None:
    callback = _CallbackLifecycle()
    with _openai_protocol_server() as base_url:
        facade, gateway = _runtime(
            base_url=base_url,
            model_name=model_name,
            callback=callback,
            trace_root=tmp_path,
        )
        try:
            response = facade.invoke(_request(model_name))
            usage = gateway.usage_snapshot()
        finally:
            facade.close()

    assert response.success is False
    assert response.error is not None
    assert response.error.failure_category == expected_category
    assert response.error.retryable is retryable
    assert response.finish_reason == "error"
    assert usage["logical_calls"] == 1
    assert usage["provider_attempts"] == 1
    assert usage["failures"] == 1
    assert callback.starts == 1
    assert callback.ends == 0
    assert callback.errors == 1

    serialized = response.model_dump_json()
    trace = _trace_text(tmp_path, f"run-{model_name}")
    captured = capsys.readouterr()
    all_diagnostics = "\n".join(
        (serialized, trace, captured.out, captured.err)
    )
    assert _SECRET not in all_diagnostics
    assert "Authorization: Bearer" not in all_diagnostics
    assert trace.count('"event_type": "model_started"') == 1
    assert trace.count('"event_type": "model_finished"') == 1
    assert '"phase": "error"' in trace
