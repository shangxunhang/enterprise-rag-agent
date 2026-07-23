"""In-process aggregate usage ledger for model calls.

WorkflowBudget remains a safety fuse.  This ledger consumes actual provider
responses/latency and owns accounting/operational aggregates instead.
"""

from __future__ import annotations

from collections import defaultdict
from threading import Lock
from typing import Any

from model_gateway.model_contract import ModelSelection
from schemas.model import ModelResponseSchema


class ModelUsageLedger:
    def __init__(self) -> None:
        self._lock = Lock()
        self._logical_calls = 0
        self._logical_call_ids: set[str] = set()
        self._calls_by_model: dict[str, int] = defaultdict(int)
        self._calls_by_profile: dict[str, int] = defaultdict(int)
        self._calls_by_provider: dict[str, int] = defaultdict(int)
        self._prompt_tokens = 0
        self._completion_tokens = 0
        self._total_tokens = 0
        self._latency_ms = 0
        self._failures = 0
        self._cancelled_attempts = 0
        self._incomplete_usage_attempts = 0
        self._availability_fallback_count = 0
        self._quality_escalation_count = 0
        self._cost = 0.0
        self._cost_available = False

    def record_logical_call(self, model_call_id: str) -> None:
        """Count one business model call, independently of provider fallback.

        Every boundary invocation is one logical call, even if a diagnostic or
        recovery caller deliberately reuses the same ``model_call_id``.
        """
        normalized = str(model_call_id or "").strip()
        with self._lock:
            self._logical_calls += 1
            if normalized:
                self._logical_call_ids.add(normalized)

    def record_attempt(
        self,
        *,
        selection: ModelSelection,
        response: ModelResponseSchema,
    ) -> None:
        usage = response.token_usage
        prompt_tokens = int(usage.prompt_tokens or 0)
        completion_tokens = int(usage.completion_tokens or 0)
        total_tokens = int(
            usage.total_tokens
            if usage.total_tokens is not None
            else prompt_tokens + completion_tokens
        )
        input_rate = selection.profile.input_cost_per_million
        output_rate = selection.profile.output_cost_per_million
        incremental_cost = 0.0
        cost_available = input_rate is not None or output_rate is not None
        response_metadata = dict(response.metadata or {})
        cancelled = bool(response_metadata.get("execution_cancelled")) or (
            response.finish_reason == "cancelled"
        )
        usage_complete = response_metadata.get("usage_complete") is not False
        if input_rate is not None:
            incremental_cost += prompt_tokens / 1_000_000 * float(input_rate)
        if output_rate is not None:
            incremental_cost += completion_tokens / 1_000_000 * float(output_rate)

        with self._lock:
            self._calls_by_model[selection.model_name] += 1
            self._calls_by_profile[selection.profile_id] += 1
            self._calls_by_provider[selection.provider] += 1
            self._prompt_tokens += prompt_tokens
            self._completion_tokens += completion_tokens
            self._total_tokens += total_tokens
            self._latency_ms += int(response.latency_ms or 0)
            if not response.success:
                self._failures += 1
            if cancelled:
                self._cancelled_attempts += 1
            if not usage_complete:
                self._incomplete_usage_attempts += 1
            if cost_available:
                self._cost_available = True
                self._cost += incremental_cost

    def record_availability_fallback(self) -> None:
        with self._lock:
            self._availability_fallback_count += 1

    def record_quality_escalation(self) -> None:
        with self._lock:
            self._quality_escalation_count += 1

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            provider_attempts = sum(self._calls_by_model.values())
            return {
                "budget_semantics": "logical_model_call_v1",
                "token_usage_semantics": "actual_provider_response_v1",
                "logical_calls": self._logical_calls,
                "unique_logical_call_ids": len(self._logical_call_ids),
                "provider_attempts": provider_attempts,
                "calls_by_model": dict(self._calls_by_model),
                "calls_by_profile": dict(self._calls_by_profile),
                "calls_by_provider": dict(self._calls_by_provider),
                "prompt_tokens": self._prompt_tokens,
                "completion_tokens": self._completion_tokens,
                "total_tokens": self._total_tokens,
                "latency_ms": self._latency_ms,
                "failures": self._failures,
                "cancelled_attempts": self._cancelled_attempts,
                "incomplete_usage_attempts": self._incomplete_usage_attempts,
                "availability_fallback_count": self._availability_fallback_count,
                "quality_escalation_count": self._quality_escalation_count,
                "cost_if_available": self._cost if self._cost_available else None,
            }
