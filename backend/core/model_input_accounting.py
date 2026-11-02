"""Canonical, deterministic planning accounting; never provider-tokenizer usage."""
from __future__ import annotations

import json
from typing import Any, Mapping, Sequence

from core.token_estimator import DeterministicTokenEstimator


def canonical_input_json(payload: Mapping[str, Any]) -> str:
    return json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def estimate_model_input(payload: Mapping[str, Any], *, estimator=None) -> int:
    return (estimator or DeterministicTokenEstimator()).estimate(canonical_input_json(payload))


def request_input_payload(*, messages: Sequence[Mapping[str, str]], prompt: str,
                          system_prompt: str | None = None) -> dict[str, Any]:
    """Mirror the provider adapter's minimal role/content contract and precedence."""
    if messages:
        aliases = {"human": "user", "ai": "assistant"}
        normalized = []
        for item in messages:
            role = str(item.get("role") or "").strip().lower()
            role = aliases.get(role, role)
            if role not in {"system", "user", "assistant"}:
                raise ValueError("Unsupported model message role for context accounting")
            normalized.append({"role": role, "content": str(item.get("content") or "")})
    else:
        normalized = ([{"role": "system", "content": system_prompt}] if system_prompt else [])
        normalized.append({"role": "user", "content": prompt})
    return {"input_kind": "MESSAGES", "messages": normalized}


class ModelContextBudgetExceeded(ValueError):
    """Local profile/input validation; distinct from canonical execution Budget."""
    failure_category = "validation"

    def __init__(self, *, profile_id: str, input_tokens: int, output_tokens: int, window: int):
        self.profile_id = profile_id
        self.estimated_input_tokens = input_tokens
        self.reserved_output_tokens = output_tokens
        self.context_window = window
        super().__init__(
            f"MODEL_CONTEXT_BUDGET_EXCEEDED: profile={profile_id} "
            f"input={input_tokens} output={output_tokens} window={window}"
        )
