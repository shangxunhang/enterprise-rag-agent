"""Composition and validation of the Adaptive Profile router."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from rag.registry.default_registrations import build_default_component_registry
from rag.routing.policy import AdaptiveProfileDecision
from rag.routing.schema import (
    AdaptiveProfileRouterConfig,
    AdaptiveProfileRouterConfigLoader,
)


class AdaptiveProfileRouterRuntime:
    def __init__(
        self,
        *,
        config_file: str | Path,
        project_root: str | Path,
        loader: AdaptiveProfileRouterConfigLoader | None = None,
    ) -> None:
        self.project_root = Path(project_root).expanduser().resolve()
        self.loader = loader or AdaptiveProfileRouterConfigLoader()
        self.config_file = self.loader.resolve_path(
            config_file, project_root=self.project_root
        )
        self.config: AdaptiveProfileRouterConfig = self.loader.load(
            self.config_file, project_root=self.project_root
        )
        self.targets = self.loader.validate_profiles(
            self.config, project_root=self.project_root
        )
        registry = build_default_component_registry()
        self.policy = registry.build(
            category="profile_router",
            config=self.config.policy,
            build_context={
                "router_id": self.config.router_id,
                "router_version": self.config.router_version,
                "default_profile_id": self.config.default_profile_id,
                "profile_ids": list(self.targets),
                "rules": self.config.rules,
            },
        )

    @property
    def default_profile_id(self) -> str:
        return self.config.default_profile_id

    @property
    def agent_quality_profile_id(self) -> str:
        return self.config.agent_quality_profile_id

    def profile_path(self, profile_id: str) -> Path:
        try:
            return Path(self.targets[profile_id]["path"])
        except KeyError as exc:
            raise ValueError(f"unknown routed profile: {profile_id}") from exc

    def route(self, payload: dict[str, Any]) -> AdaptiveProfileDecision:
        extra = dict(payload.get("extra_metadata") or {})
        request_context = dict(extra.get("request_context") or {})
        document_context = dict(extra.get("document_context") or {})
        request_context = {
            **request_context,
            **document_context,
            **extra,
            "need_citation": bool(payload.get("need_citation", True)),
        }
        requested_profile_id = (
            payload.get("requested_profile_id")
            or extra.get("requested_profile_id")
            or request_context.get("requested_profile_id")
        )
        decision = self.policy.route(
            query=str(payload.get("query") or ""),
            request_context=request_context,
            requested_profile_id=(
                str(requested_profile_id).strip() if requested_profile_id else None
            ),
        )
        target = self.targets[decision.selected_profile_id]
        decision.route_config_file = str(self.config_file)
        decision.route_config_hash = self.config.config_hash()
        decision.selected_profile_path = target["path"]
        decision.selected_profile_hash = target["hash"]
        decision.selected_profile_version = target["profile_version"]
        decision.metadata = {
            **dict(decision.metadata),
            "policy": self.policy.plugin_metadata.to_dict(),
            "agent_quality_profile_id": self.agent_quality_profile_id,
            "quality_scores_used_for_routing": False,
        }
        return decision

    def validation_report(self) -> dict[str, Any]:
        return {
            "status": "success",
            "schema_version": self.config.schema_version,
            "router_id": self.config.router_id,
            "router_version": self.config.router_version,
            "config_file": str(self.config_file),
            "config_hash": self.config.config_hash(),
            "default_profile_id": self.default_profile_id,
            "agent_quality_profile_id": self.agent_quality_profile_id,
            "policy": self.policy.plugin_metadata.to_dict(),
            "profile_count": len(self.targets),
            "profiles": list(self.targets.values()),
            "rule_count": len(self.config.rules),
            "rules": [
                {
                    "rule_id": item.rule_id,
                    "priority": item.priority,
                    "profile_id": item.profile_id,
                    "reason": item.reason,
                }
                for item in sorted(
                    self.config.rules,
                    key=lambda rule: (-int(rule.priority), rule.rule_id),
                )
            ],
        }
