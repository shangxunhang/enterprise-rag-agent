"""Target Post-write physical execution configuration.

Semantic QualityDeliveryContract remains separate. These profiles own only
bounded execution mechanics/procedure versions.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, model_validator

from core.hashing import canonical_sha256


class _Config(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class PostWriteTechnicalRetryProfile(_Config):
    profile_id: str
    profile_version: str
    max_attempts: int = Field(default=2, ge=1, le=8)
    retryable_failure_categories: tuple[str, ...] = (
        "TIMEOUT",
        "TRANSIENT_PROVIDER",
    )


class FaithfulnessExecutionProfile(_Config):
    profile_id: str
    profile_version: str
    max_window_claims: int = Field(default=16, ge=1)
    max_window_evidence_members: int = Field(default=32, ge=1)
    model_context_budget: int = Field(default=12000, ge=256)


class ConsistencyExecutionProfile(_Config):
    profile_id: str
    profile_version: str
    small_n_binary_member_limit: int = Field(default=8, ge=2)
    max_binary_batch_size: int = Field(default=32, ge=1)
    composite_max_arity: int = Field(default=3, ge=2, le=8)
    composite_candidate_budget: int = Field(default=24, ge=1)
    large_n_candidate_source_policy: str = "high-recall-union-v1"
    bridge_policy: str = "cross-partition-required-v1"
    second_pass_trigger_policy: str = "ambiguity-or-unresolved-v1"
    model_context_budget: int = Field(default=12000, ge=256)
    procedure_contract_version: str = "consistency-procedure-v1"
    execution_profile_hash: str

    @model_validator(mode="after")
    def _hash_integrity(self) -> "ConsistencyExecutionProfile":
        payload = self.model_dump(mode="json")
        payload.pop("execution_profile_hash", None)
        if canonical_sha256(payload) != self.execution_profile_hash:
            raise ValueError("ConsistencyExecutionProfile hash mismatch")
        return self

    @classmethod
    def build(cls, **kwargs) -> "ConsistencyExecutionProfile":
        payload = dict(kwargs)
        payload.setdefault("profile_id", "consistency-default")
        payload.setdefault("profile_version", "v1")
        payload.setdefault("small_n_binary_member_limit", 8)
        payload.setdefault("max_binary_batch_size", 32)
        payload.setdefault("composite_max_arity", 3)
        payload.setdefault("composite_candidate_budget", 24)
        payload.setdefault(
            "large_n_candidate_source_policy",
            "high-recall-union-v1",
        )
        payload.setdefault(
            "bridge_policy",
            "cross-partition-required-v1",
        )
        payload.setdefault(
            "second_pass_trigger_policy",
            "ambiguity-or-unresolved-v1",
        )
        payload.setdefault("model_context_budget", 12000)
        payload.setdefault(
            "procedure_contract_version",
            "consistency-procedure-v1",
        )
        return cls(
            **payload,
            execution_profile_hash=canonical_sha256(payload),
        )


class PostWriteExecutionConfig(_Config):
    schema_version: Literal["post_write_execution_config_v1"] = (
        "post_write_execution_config_v1"
    )
    config_id: str
    config_version: str
    technical_retry: PostWriteTechnicalRetryProfile
    faithfulness: FaithfulnessExecutionProfile
    consistency: ConsistencyExecutionProfile


def load_post_write_config(path: str | Path) -> PostWriteExecutionConfig:
    resolved = Path(path).expanduser().resolve()
    if not resolved.is_file():
        raise FileNotFoundError(f"Post-write config not found: {resolved}")
    raw = resolved.read_text(encoding="utf-8")
    payload = (
        json.loads(raw)
        if resolved.suffix.lower() == ".json"
        else yaml.safe_load(raw)
    )
    if not isinstance(payload, dict):
        raise ValueError("Post-write config root must be an object")
    consistency = dict(payload.get("consistency") or {})
    if "execution_profile_hash" not in consistency:
        built = ConsistencyExecutionProfile.build(**consistency)
        payload = dict(payload)
        payload["consistency"] = built.model_dump(mode="json")
    return PostWriteExecutionConfig.model_validate(payload)


__all__ = [
    "ConsistencyExecutionProfile",
    "FaithfulnessExecutionProfile",
    "PostWriteExecutionConfig",
    "PostWriteTechnicalRetryProfile",
    "load_post_write_config",
]
