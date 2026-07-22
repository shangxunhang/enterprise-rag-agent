"""Strict model-routing contracts owned by ``model_gateway``.

These contracts deliberately live inside the gateway package instead of the
business schemas.  Business code expresses *what kind of model call* it needs
through ``ModelRole``; the composition root owns the concrete profiles and
routing policy.
"""

from __future__ import annotations

from enum import Enum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class StrictGatewayModel(BaseModel):
    """Gateway-owned configuration contracts reject unknown fields."""

    model_config = ConfigDict(extra="forbid", frozen=True)


class ModelRole(str, Enum):
    """Stable business-facing roles for LLM calls."""

    GENERAL = "general"
    SUPERVISOR_ROUTING = "supervisor_routing"
    QUERY_REWRITE = "query_rewrite"
    HYDE = "hyde"
    RETRIEVAL_JUDGE = "retrieval_judge"
    CORRECTIVE_PLANNER = "corrective_planner"
    SECTION_GENERATION = "section_generation"
    SEMANTIC_GATE = "semantic_gate"
    REPAIR = "repair"


class ResidencyPolicy(str, Enum):
    """Minimal model lifecycle policy.

    ``primary`` and ``resident`` are lazy-loaded and retained after first use.
    ``on_demand`` is released after each invocation.  ``remote`` has no local
    residency lifecycle.  ``disabled`` can never be selected or loaded.
    """

    PRIMARY = "primary"
    RESIDENT = "resident"
    ON_DEMAND = "on_demand"
    REMOTE = "remote"
    DISABLED = "disabled"


class ModelProfile(StrictGatewayModel):
    """One concrete model/provider capability profile."""

    profile_id: str
    model_name: str
    provider: str
    provider_model_name: str | None = None
    local_path: str | None = None
    enabled: bool = True
    residency_policy: ResidencyPolicy = ResidencyPolicy.PRIMARY

    context_window: int = Field(default=32768, ge=256)
    max_output_tokens: int = Field(default=2048, ge=1)
    timeout_seconds: float = Field(default=120.0, gt=0)

    input_cost_per_million: float | None = Field(default=None, ge=0)
    output_cost_per_million: float | None = Field(default=None, ge=0)
    metadata: dict[str, Any] = Field(default_factory=dict)

    @field_validator("profile_id", "model_name", "provider")
    @classmethod
    def _non_empty(cls, value: str) -> str:
        normalized = str(value or "").strip()
        if not normalized:
            raise ValueError("model profile identifiers cannot be empty")
        return normalized

    @model_validator(mode="after")
    def _validate_residency(self) -> "ModelProfile":
        if self.residency_policy == ResidencyPolicy.REMOTE and self.local_path:
            raise ValueError("remote model profile cannot declare local_path")
        return self

    @property
    def selectable(self) -> bool:
        return bool(self.enabled and self.residency_policy != ResidencyPolicy.DISABLED)


class RoutingPolicy(StrictGatewayModel):
    """Ordered candidate profiles for one ``ModelRole``."""

    role: ModelRole
    candidates: list[str] = Field(min_length=1)
    availability_fallback: bool = True

    @field_validator("candidates")
    @classmethod
    def _normalize_candidates(cls, value: list[str]) -> list[str]:
        normalized = [str(item or "").strip() for item in value]
        if any(not item for item in normalized):
            raise ValueError("routing policy candidates cannot contain empty ids")
        if len(normalized) != len(set(normalized)):
            raise ValueError("routing policy candidates must be unique and ordered")
        return normalized


class ModelRoutingConfig(StrictGatewayModel):
    """Strict aggregate config used by composition and tests."""

    profiles: list[ModelProfile]
    policies: list[RoutingPolicy]

    @model_validator(mode="after")
    def _validate_references(self) -> "ModelRoutingConfig":
        profile_ids = [item.profile_id for item in self.profiles]
        if len(profile_ids) != len(set(profile_ids)):
            raise ValueError("duplicate model profile_id")
        model_names = [item.model_name for item in self.profiles]
        if len(model_names) != len(set(model_names)):
            raise ValueError("duplicate model_name across profiles")
        roles = [item.role for item in self.policies]
        if len(roles) != len(set(roles)):
            raise ValueError("duplicate routing policy role")
        known = set(profile_ids)
        unknown = [
            candidate
            for policy in self.policies
            for candidate in policy.candidates
            if candidate not in known
        ]
        if unknown:
            raise ValueError(
                "routing policy references unknown profiles: "
                + ", ".join(sorted(set(unknown)))
            )
        return self


class ModelSelection(StrictGatewayModel):
    """Resolved candidate returned by ``ModelRouter``."""

    role: ModelRole | None = None
    profile: ModelProfile
    candidate_index: int = Field(default=0, ge=0)
    explicit_override: bool = False

    @property
    def profile_id(self) -> str:
        return self.profile.profile_id

    @property
    def model_name(self) -> str:
        return self.profile.model_name

    @property
    def provider(self) -> str:
        return self.profile.provider
