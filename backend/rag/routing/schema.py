"""Schemas for configuration-driven Adaptive Profile routing."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any, Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from rag.config.pipeline_config import ComponentConfig, PipelineConfigLoader


class ProfileTargetConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    profile_id: str
    profile_file: str

    @field_validator("profile_id", "profile_file")
    @classmethod
    def _not_blank(cls, value: str) -> str:
        normalized = str(value or "").strip()
        if not normalized:
            raise ValueError("profile target fields cannot be blank")
        return normalized


class RoutingConditionConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    field: str
    operator: Literal[
        "eq",
        "ne",
        "truthy",
        "falsy",
        "gte",
        "lte",
        "in",
        "contains_any",
    ] = "eq"
    value: Any = None

    @field_validator("field")
    @classmethod
    def _field_not_blank(cls, value: str) -> str:
        normalized = str(value or "").strip()
        if not normalized:
            raise ValueError("routing condition field cannot be blank")
        return normalized


class RoutingRuleConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    rule_id: str
    priority: int = 0
    profile_id: str
    reason: str
    all_conditions: list[RoutingConditionConfig] = Field(default_factory=list)
    any_conditions: list[RoutingConditionConfig] = Field(default_factory=list)

    @field_validator("rule_id", "profile_id", "reason")
    @classmethod
    def _not_blank(cls, value: str) -> str:
        normalized = str(value or "").strip()
        if not normalized:
            raise ValueError("routing rule id/profile/reason cannot be blank")
        return normalized

    @model_validator(mode="after")
    def _require_conditions(self) -> "RoutingRuleConfig":
        if not self.all_conditions and not self.any_conditions:
            raise ValueError("routing rule must contain at least one condition")
        return self


class AdaptiveProfileRouterConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: Literal["adaptive_profile_router_config_v1"] = (
        "adaptive_profile_router_config_v1"
    )
    router_id: str
    router_version: str = "v1"
    policy: ComponentConfig
    default_profile_id: str
    agent_quality_profile_id: str
    profiles: list[ProfileTargetConfig]
    rules: list[RoutingRuleConfig]

    @field_validator(
        "router_id", "router_version", "default_profile_id", "agent_quality_profile_id"
    )
    @classmethod
    def _not_blank(cls, value: str) -> str:
        normalized = str(value or "").strip()
        if not normalized:
            raise ValueError("router identifiers cannot be blank")
        return normalized

    @model_validator(mode="after")
    def _validate_references(self) -> "AdaptiveProfileRouterConfig":
        profile_ids = [item.profile_id for item in self.profiles]
        if len(profile_ids) != len(set(profile_ids)):
            raise ValueError("adaptive router contains duplicate profile targets")
        if self.default_profile_id not in profile_ids:
            raise ValueError("default_profile_id must reference a configured profile")
        if self.agent_quality_profile_id not in profile_ids:
            raise ValueError("agent_quality_profile_id must reference a configured profile")
        unknown = sorted({item.profile_id for item in self.rules} - set(profile_ids))
        if unknown:
            raise ValueError(f"routing rules reference unknown profiles: {unknown}")
        rule_ids = [item.rule_id for item in self.rules]
        if len(rule_ids) != len(set(rule_ids)):
            raise ValueError("adaptive router contains duplicate rule ids")
        return self

    def canonical_dict(self) -> dict[str, Any]:
        return self.model_dump(mode="json", exclude_none=True)

    def config_hash(self) -> str:
        payload = json.dumps(
            self.canonical_dict(),
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        return hashlib.sha256(payload).hexdigest()


class AdaptiveProfileRouterConfigLoader:
    def __init__(self, pipeline_loader: PipelineConfigLoader | None = None) -> None:
        self.pipeline_loader = pipeline_loader or PipelineConfigLoader()

    def resolve_path(
        self,
        path: str | Path,
        *,
        project_root: str | Path | None = None,
    ) -> Path:
        return self.pipeline_loader.resolve_path(path, project_root=project_root)

    def load(
        self,
        path: str | Path,
        *,
        project_root: str | Path | None = None,
    ) -> AdaptiveProfileRouterConfig:
        config_path = self.resolve_path(path, project_root=project_root)
        if not config_path.is_file():
            raise FileNotFoundError(f"Adaptive router config not found: {config_path}")
        raw_text = config_path.read_text(encoding="utf-8")
        suffix = config_path.suffix.lower()
        if suffix in {".yaml", ".yml"}:
            payload = yaml.safe_load(raw_text)
        elif suffix == ".json":
            payload = json.loads(raw_text)
        else:
            raise ValueError(f"Unsupported adaptive router config format: {suffix!r}")
        if not isinstance(payload, dict):
            raise ValueError("Adaptive router config root must be an object")
        return AdaptiveProfileRouterConfig.model_validate(payload)

    def validate_profiles(
        self,
        config: AdaptiveProfileRouterConfig,
        *,
        project_root: str | Path,
    ) -> dict[str, dict[str, Any]]:
        root = Path(project_root).expanduser().resolve()
        validated: dict[str, dict[str, Any]] = {}
        for target in config.profiles:
            path = self.resolve_path(target.profile_file, project_root=root)
            pipeline = self.pipeline_loader.load(path, project_root=root)
            if pipeline.profile_id != target.profile_id:
                raise ValueError(
                    "adaptive router profile id mismatch: "
                    f"declared={target.profile_id!r}, actual={pipeline.profile_id!r}, "
                    f"path={path}"
                )
            validated[target.profile_id] = {
                "profile_id": pipeline.profile_id,
                "profile_version": pipeline.profile_version,
                "path": str(path),
                "hash": pipeline.config_hash(),
                "schema_version": pipeline.schema_version,
            }
        return validated


def peek_config_schema_version(path: str | Path) -> str:
    config_path = Path(path).expanduser().resolve()
    if not config_path.is_file():
        return ""
    try:
        if config_path.suffix.lower() in {".yaml", ".yml"}:
            payload = yaml.safe_load(config_path.read_text(encoding="utf-8"))
        elif config_path.suffix.lower() == ".json":
            payload = json.loads(config_path.read_text(encoding="utf-8"))
        else:
            return ""
    except Exception:
        return ""
    return str(payload.get("schema_version") or "") if isinstance(payload, dict) else ""
