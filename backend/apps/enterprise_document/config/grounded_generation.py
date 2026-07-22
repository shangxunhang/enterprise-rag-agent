"""Configuration for the generation-quality loop owned by the use case."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class GenerationPluginConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str
    version: str = "v1"
    enabled: bool = True
    params: dict[str, Any] = Field(default_factory=dict)

    @field_validator("name", "version")
    @classmethod
    def not_blank(cls, value: str) -> str:
        value = str(value or "").strip()
        if not value:
            raise ValueError("generation plugin name/version cannot be blank")
        return value


class GroundedGenerationPolicyConfig(BaseModel):
    """Bounded post-generation checking, repair and retrieval feedback."""

    model_config = ConfigDict(extra="forbid")

    schema_version: Literal["grounded_generation_policy_v1"] = (
        "grounded_generation_policy_v1"
    )
    policy_id: str
    policy_version: str = "v1"
    generation_checker: GenerationPluginConfig
    repair_strategy: GenerationPluginConfig
    max_retrieval_rounds: int = Field(default=1, ge=0, le=3)
    max_rewrite_rounds: int = Field(default=1, ge=0, le=3)
    max_total_llm_calls: int = Field(default=35, ge=1, le=64)
    max_total_tokens: int = Field(default=24000, ge=256)
    human_review_on_exhaustion: bool = True
    budget_scope: Literal["section"] = "section"

    @model_validator(mode="after")
    def require_enabled_plugins(self) -> "GroundedGenerationPolicyConfig":
        if not self.generation_checker.enabled:
            raise ValueError(
                "grounded generation policy requires enabled generation_checker"
            )
        if not self.repair_strategy.enabled:
            raise ValueError(
                "grounded generation policy requires enabled repair_strategy"
            )
        return self


class GroundedGenerationPolicyLoader:
    def load(self, path: str | Path) -> GroundedGenerationPolicyConfig:
        path = Path(path).expanduser().resolve()
        if not path.is_file():
            raise FileNotFoundError(f"generation policy not found: {path}")
        raw = path.read_text(encoding="utf-8")
        payload = json.loads(raw) if path.suffix.lower() == ".json" else yaml.safe_load(raw)
        if not isinstance(payload, dict):
            raise ValueError("generation policy root must be an object")
        return GroundedGenerationPolicyConfig.model_validate(payload)
