"""Role-based model selection policy owned by ModelGateway."""

from __future__ import annotations

from collections.abc import Iterable

from model_gateway.model_contract import (
    ModelProfile,
    ModelRole,
    ModelSelection,
    ResidencyPolicy,
    RoutingPolicy,
)
from schemas.model import ModelRequestSchema


class ModelRoutingError(ValueError):
    """Fail-fast routing/configuration error before provider invocation."""


class ModelRouter:
    """Resolve a request into an ordered candidate plan.

    Compatibility mode is intentionally retained: gateways constructed only
    with ``default_model_name`` still behave like the pre-role router.  Once
    profiles/policies are supplied, role-based routing becomes authoritative.
    """

    def __init__(
        self,
        default_model_name: str,
        *,
        profiles: Iterable[ModelProfile] | None = None,
        policies: Iterable[RoutingPolicy] | None = None,
    ) -> None:
        self.default_model_name = str(default_model_name or "").strip()
        if not self.default_model_name:
            raise ValueError("default_model_name cannot be empty")

        self._profiles_by_id: dict[str, ModelProfile] = {}
        self._profiles_by_model: dict[str, ModelProfile] = {}
        for profile in profiles or ():
            if profile.profile_id in self._profiles_by_id:
                raise ValueError(f"duplicate model profile: {profile.profile_id}")
            if profile.model_name in self._profiles_by_model:
                raise ValueError(f"duplicate model name: {profile.model_name}")
            self._profiles_by_id[profile.profile_id] = profile
            self._profiles_by_model[profile.model_name] = profile

        self._policies: dict[ModelRole, RoutingPolicy] = {}
        for policy in policies or ():
            if policy.role in self._policies:
                raise ValueError(f"duplicate model routing role: {policy.role.value}")
            unknown = [
                candidate
                for candidate in policy.candidates
                if candidate not in self._profiles_by_id
            ]
            if unknown:
                raise ValueError(
                    f"routing role {policy.role.value} references unknown profiles: "
                    + ", ".join(unknown)
                )
            self._policies[policy.role] = policy

    @property
    def profiles(self) -> tuple[ModelProfile, ...]:
        return tuple(self._profiles_by_id.values())

    @property
    def policies(self) -> tuple[RoutingPolicy, ...]:
        return tuple(self._policies.values())

    @staticmethod
    def _legacy_profile(model_name: str) -> ModelProfile:
        return ModelProfile(
            profile_id=f"legacy:{model_name}",
            model_name=model_name,
            provider="legacy_registry",
            enabled=True,
            residency_policy=ResidencyPolicy.PRIMARY,
            context_window=32768,
            max_output_tokens=8192,
            metadata={"compatibility_profile": True},
        )

    def _profile_for_override(self, override: str) -> ModelProfile:
        profile = self._profiles_by_id.get(override) or self._profiles_by_model.get(
            override
        )
        if profile is None:
            # Explicit overrides are intentionally open for tests/debugging and
            # externally registered clients.  A typo still fails deterministically
            # at registry lookup instead of silently changing the routed model.
            return self._legacy_profile(override)
        if not profile.selectable:
            raise ModelRoutingError(
                f"model profile is disabled and cannot be selected: {profile.profile_id}"
            )
        return profile

    def _role(self, raw_role: str | None) -> ModelRole | None:
        normalized = str(raw_role or "").strip()
        if not normalized:
            return None
        try:
            return ModelRole(normalized)
        except ValueError as exc:
            raise ModelRoutingError(f"unknown model role: {normalized}") from exc

    def plan(self, request: ModelRequestSchema) -> list[ModelSelection]:
        """Return the ordered provider candidate plan for one request."""

        override = str(request.model_name or "").strip()
        role = self._role(request.model_role)
        if override:
            return [
                ModelSelection(
                    role=role,
                    profile=self._profile_for_override(override),
                    candidate_index=0,
                    explicit_override=True,
                )
            ]

        if role is not None:
            policy = self._policies.get(role)
            if policy is None and not self._policies:
                # Compatibility gateway created with only default_model_name.
                # Recognized roles still resolve to that default; once any role
                # policies are configured, missing roles fail fast.
                profile = self._profiles_by_model.get(self.default_model_name)
                if profile is None:
                    profile = self._legacy_profile(self.default_model_name)
                if not profile.selectable:
                    raise ModelRoutingError(
                        "default model profile is disabled: " + profile.profile_id
                    )
                return [
                    ModelSelection(
                        role=role,
                        profile=profile,
                        candidate_index=0,
                        explicit_override=False,
                    )
                ]
            if policy is None:
                raise ModelRoutingError(f"no routing policy configured for role: {role.value}")
            selections: list[ModelSelection] = []
            candidate_ids = (
                policy.candidates
                if policy.availability_fallback
                else policy.candidates[:1]
            )
            for profile_id in candidate_ids:
                profile = self._profiles_by_id[profile_id]
                if not profile.selectable:
                    continue
                selections.append(
                    ModelSelection(
                        role=role,
                        profile=profile,
                        candidate_index=len(selections),
                        explicit_override=False,
                    )
                )
            if not selections:
                raise ModelRoutingError(
                    f"routing role has no enabled model candidates: {role.value}"
                )
            return selections

        # Backward compatibility for callers not migrated to roles yet.
        profile = self._profiles_by_model.get(self.default_model_name)
        if profile is not None:
            if not profile.selectable:
                raise ModelRoutingError(
                    "default model profile is disabled: " + profile.profile_id
                )
        else:
            profile = self._legacy_profile(self.default_model_name)
        return [
            ModelSelection(
                role=None,
                profile=profile,
                candidate_index=0,
                explicit_override=False,
            )
        ]

    def select(self, request: ModelRequestSchema) -> str:
        """Compatibility helper returning only the primary model name."""

        return self.plan(request)[0].model_name
