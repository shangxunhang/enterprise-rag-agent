"""Intent and correction-budget policies kept separate from static topology."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Literal, TypeVar

import yaml
from pydantic import BaseModel, ConfigDict, Field


class IntentPolicyConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: Literal["intent_policy_v1"] = "intent_policy_v1"
    policy_id: str = "adaptive_intent_v1"
    short_query_max_chars: int = Field(default=16, ge=1, le=256)


class RetrievalGatePolicyConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: Literal["retrieval_gate_policy_v1"] = "retrieval_gate_policy_v1"
    policy_id: str = "evidence_correction_gate_v1"
    correction_budget: int = Field(default=1, ge=0, le=3)


PolicyT = TypeVar("PolicyT", bound=BaseModel)


class RetrievalPolicyLoader:
    def load(self, path: str | Path, schema: type[PolicyT]) -> PolicyT:
        resolved = Path(path).expanduser().resolve()
        if not resolved.is_file():
            raise FileNotFoundError(f"retrieval policy not found: {resolved}")
        raw = resolved.read_text(encoding="utf-8")
        payload = json.loads(raw) if resolved.suffix.lower() == ".json" else yaml.safe_load(raw)
        if not isinstance(payload, dict):
            raise ValueError("retrieval policy root must be an object")
        return schema.model_validate(payload)
