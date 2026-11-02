"""SIF-10 application-local Context Runtime.

This module is an authorization/materialization runtime over registered canonical
source providers. It never owns Fact/Evidence/Artifact/Claim/Memory truth and it
never treats checkpoint/trace/legacy ContextBundle bytes as semantic authority.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import json
from typing import Any, Callable, Iterable, Mapping, Protocol

from context_manager.manager import ContextBudgetExceededError, LLMContextManager
from core.hashing import canonical_sha256
from core.diagnostics import current_diagnostics
from core.token_estimator import DeterministicTokenEstimator
from core.model_input_accounting import estimate_model_input
from schemas.context import (
    ConsumerAuthorizationDecisionSchema,
    ConsumerAuthorizationDecisionState,
    ContextBudgetBindingSchema,
    ContextBuildRequestSchema,
    ContextCandidateDecisionSchema,
    ContextFinalInputKind,
    ContextHardEligibility,
    ContextInstructionMaterialSchema,
    ContextInvocationSubjectSchema,
    ContextItemSchema,
    ContextMaterializationSchema,
    ContextPackageRefSchema,
    ContextPackageReplayState,
    ContextPackageSchema,
    ContextPolicyBindingSchema,
    ContextPriorityTier,
    ContextRepresentationLevel,
    ContextRepresentationOptionSchema,
    ContextRolePolicySchema,
    ContextRoleSourceRuleSchema,
    ContextRoleVisibility,
    ContextSourceBindingSchema,
    ContextSourceCandidateSchema,
    ContextSourceFamily,
    FinalModelInputMaterialSchema,
    InstructionBindingSchema,
    ModelContextCapabilityBindingSchema,
    ModelInvocationInputEnvelopeSchema,
)


class ContextRuntimeError(RuntimeError):
    """Base SIF-10 runtime failure."""


class ContextSourceRefInvalid(ContextRuntimeError):
    pass


class ContextSourceScopeMismatch(ContextRuntimeError):
    pass


class ContextSourceStale(ContextRuntimeError):
    pass


class ContextPolicyUnresolved(ContextRuntimeError):
    pass


class ContextRoleNotAuthorized(ContextRuntimeError):
    pass


class ContextConsumerAuthorizationRejected(ContextRuntimeError):
    pass


class ContextProjectionSchemaInvalid(ContextRuntimeError):
    pass


class ContextProjectionHashMismatch(ContextRuntimeError):
    pass


class ContextRepresentationNotAuthorized(ContextRuntimeError):
    pass


class ContextPackageIntegrityInvalid(ContextRuntimeError):
    pass


class ContextPackageMissing(ContextRuntimeError):
    pass


class ContextPackageConflict(ContextRuntimeError):
    pass


class ContextFinalInputHashMismatch(ContextRuntimeError):
    pass


class ContextPostFingerprintMutation(ContextRuntimeError):
    pass


class ContextBudgetExceeded(ContextRuntimeError):
    # A Context planning failure is not canonical execution-resource exhaustion.
    failure_category = "validation"


EligibilityValidator = Callable[
    [ContextSourceBindingSchema, ContextInvocationSubjectSchema],
    tuple[bool, tuple[str, ...]],
]
CandidateReader = Callable[
    [ContextInvocationSubjectSchema],
    tuple[ContextSourceCandidateSchema, ...],
]
SourceBindingResolver = Callable[
    [ContextSourceBindingSchema],
    ContextSourceBindingSchema,
]
ConsumerAuthorizationResolver = Callable[
    [
        ContextSourceCandidateSchema,
        ContextInvocationSubjectSchema,
        ContextRoleSourceRuleSchema,
    ],
    ConsumerAuthorizationDecisionSchema,
]


@dataclass(frozen=True)
class ContextSourceProviderRegistration:
    source_family: ContextSourceFamily
    provider_id: str
    provider_version: str
    candidate_reader: CandidateReader
    source_binding_resolver: SourceBindingResolver
    hard_eligibility_validator: EligibilityValidator
    consumer_authorization_resolver: ConsumerAuthorizationResolver | None = None


@dataclass(frozen=True)
class ResolvedContextCandidates:
    decisions: tuple[ContextCandidateDecisionSchema, ...]
    eligible_candidates: tuple[ContextSourceCandidateSchema, ...]
    consumer_authorizations: tuple[ConsumerAuthorizationDecisionSchema, ...]


@dataclass(frozen=True)
class ContextReplayResult:
    state: ContextPackageReplayState
    package: ContextPackageSchema | None
    reason_codes: tuple[str, ...]


class ContextPackageStore(Protocol):
    def put_if_absent_or_same(
        self,
        package: ContextPackageSchema,
    ) -> ContextPackageSchema: ...

    def resolve(
        self,
        package_ref: ContextPackageRefSchema,
    ) -> ContextPackageSchema | None: ...


class InMemoryContextPackageStore:
    """Noncanonical reference store for immutable replay/audit packages."""

    def __init__(self) -> None:
        self._items: dict[str, ContextPackageSchema] = {}

    @staticmethod
    def _same_semantics(
        left: ContextPackageSchema,
        right: ContextPackageSchema,
    ) -> bool:
        # Source-status payloads may contain audit-only observation timestamps.
        # ContextPackage semantic fingerprint intentionally excludes those fields
        # via ContextSourceBinding.semantic_identity(). Equal canonical package
        # identity/fingerprint therefore means equal replay semantics even when
        # a fresh build carries newer audit observation bytes.
        return (
            left.package_ref == right.package_ref
            and left.context_semantic_fingerprint
            == right.context_semantic_fingerprint
            and left.model_input.final_model_input_sha256
            == right.model_input.final_model_input_sha256
        )

    def put_if_absent_or_same(
        self,
        package: ContextPackageSchema,
    ) -> ContextPackageSchema:
        incoming = ContextPackageSchema.model_validate(package)
        key = incoming.package_ref.context_package_id
        existing = self._items.get(key)
        if existing is None:
            self._items[key] = incoming
            return incoming
        if not self._same_semantics(existing, incoming):
            raise ContextPackageConflict(
                "context_package_id resolves conflicting immutable package bytes"
            )
        return existing

    def resolve(
        self,
        package_ref: ContextPackageRefSchema,
    ) -> ContextPackageSchema | None:
        ref = ContextPackageRefSchema.model_validate(package_ref)
        package = self._items.get(ref.context_package_id)
        if package is None:
            return None
        if package.package_ref != ref:
            raise ContextPackageIntegrityInvalid(
                "context_package_id resolves a different ContextPackageRef"
            )
        return ContextPackageSchema.model_validate(package)


_TIER_ORDER = {
    ContextPriorityTier.P0_MANDATORY_AUTHORITY: 0,
    ContextPriorityTier.P1_OPERATION_CRITICAL: 1,
    ContextPriorityTier.P2_RELEVANT_CURRENT: 2,
    ContextPriorityTier.P3_SUPPORTING: 3,
    ContextPriorityTier.P4_HISTORICAL_OPTIONAL: 4,
}

_FAMILY_ORDER = {
    ContextSourceFamily.FACT_SNAPSHOT: 0,
    ContextSourceFamily.SECTION_PLAN: 1,
    ContextSourceFamily.EVIDENCE_PACKAGE: 2,
    ContextSourceFamily.PARAGRAPH_ARTIFACT: 3,
    ContextSourceFamily.CLAIM_RECORD: 4,
    ContextSourceFamily.CLAIM_BINDING_FACTS: 5,
    ContextSourceFamily.ACTIVE_VERIFIED_CLAIM: 6,
    ContextSourceFamily.VERIFIER_ISSUE: 7,
    ContextSourceFamily.DURABLE_RUN_MEMORY: 8,
    ContextSourceFamily.WORKFLOW_EXECUTION_METADATA: 9,
}

_REPRESENTATION_ORDER = {
    ContextRepresentationLevel.EXACT: 0,
    ContextRepresentationLevel.STRUCTURED_COMPACT: 1,
    ContextRepresentationLevel.REFERENCE_ONLY: 2,
}


def _canonical_codes(values: Iterable[str]) -> tuple[str, ...]:
    return tuple(sorted({str(item).strip() for item in values if str(item).strip()}))


def _payload_text(value: Any) -> str:
    if isinstance(value, str):
        return value
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    )


class ApplicationContextRuntime:
    """Target SIF-10 one-invocation Context authority executor."""

    SOURCE_CATALOG_ID = "multi_agent_v3_context_source_catalog"
    SOURCE_CATALOG_VERSION = "sif10-v1"

    def __init__(
        self,
        *,
        context_manager: LLMContextManager | None = None,
        package_store: ContextPackageStore | None = None,
        estimator: DeterministicTokenEstimator | None = None,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self.context_manager = context_manager or LLMContextManager()
        self.package_store = package_store
        self.estimator = estimator or DeterministicTokenEstimator()
        self.clock = clock or (lambda: datetime.now(timezone.utc))
        self._providers: dict[
            ContextSourceFamily,
            ContextSourceProviderRegistration,
        ] = {}

    def register_source_provider(
        self,
        registration: ContextSourceProviderRegistration,
    ) -> None:
        if registration.source_family in self._providers:
            existing = self._providers[registration.source_family]
            if existing != registration:
                raise ValueError(
                    f"Context source provider already registered: "
                    f"{registration.source_family.value}"
                )
            return
        if not str(registration.provider_id or "").strip():
            raise ValueError("Context provider_id is required")
        if not str(registration.provider_version or "").strip():
            raise ValueError("Context provider_version is required")
        for callback in (
            registration.candidate_reader,
            registration.source_binding_resolver,
            registration.hard_eligibility_validator,
        ):
            if not callable(callback):
                raise TypeError("Context provider callbacks must be callable")
        if (
            registration.consumer_authorization_resolver is not None
            and not callable(registration.consumer_authorization_resolver)
        ):
            raise TypeError("consumer authorization resolver must be callable")
        self._providers[registration.source_family] = registration

    def build_context_candidates(
        self,
        invocation_subject: ContextInvocationSubjectSchema,
    ) -> tuple[ContextSourceCandidateSchema, ...]:
        """Read typed candidates from registered providers without authorizing them.

        This is intentionally not a shortcut to Context selection. The returned
        candidates are still subject to source revalidation, Role Policy, scope,
        consumer authorization, ranking and budget in resolve_authorized_sources.
        """
        subject = ContextInvocationSubjectSchema.model_validate(invocation_subject)
        candidates: list[ContextSourceCandidateSchema] = []
        candidate_ids: set[str] = set()
        for family in sorted(self._providers, key=lambda item: _FAMILY_ORDER[item]):
            registration = self._providers[family]
            try:
                raw_items = tuple(registration.candidate_reader(subject))
            except Exception as exc:
                raise ContextSourceRefInvalid(
                    f"Context provider candidate read failed: {family.value}"
                ) from exc
            for raw_item in raw_items:
                candidate = ContextSourceCandidateSchema.model_validate(raw_item)
                if candidate.source_binding.source_family is not family:
                    raise ContextSourceRefInvalid(
                        "Context provider returned another source family"
                    )
                if candidate.candidate_id in candidate_ids:
                    raise ContextSourceRefInvalid(
                        f"duplicate Context candidate_id: {candidate.candidate_id}"
                    )
                candidate_ids.add(candidate.candidate_id)
                candidates.append(candidate)
        return tuple(
            sorted(
                candidates,
                key=lambda item: (
                    _FAMILY_ORDER[item.source_binding.source_family],
                    item.source_binding.scope_ref,
                    item.candidate_id,
                ),
            )
        )

    @staticmethod
    def _policy_rule_map(
        role_policy: ContextRolePolicySchema,
    ) -> dict[ContextSourceFamily, ContextRoleSourceRuleSchema]:
        return {item.source_family: item for item in role_policy.source_rules}

    @staticmethod
    def _validate_role_policy(
        subject: ContextInvocationSubjectSchema,
        role_policy: ContextRolePolicySchema,
    ) -> None:
        if (
            role_policy.agent_role != subject.agent_role
            or role_policy.execution_mode != subject.execution_mode
            or role_policy.context_purpose != subject.context_purpose
        ):
            raise ContextPolicyUnresolved(
                "Role Policy does not bind the exact invocation role/mode/purpose"
            )

    @staticmethod
    def _binding_source_identity(
        binding: ContextSourceBindingSchema,
    ) -> dict[str, Any]:
        identity = binding.semantic_identity()
        identity.pop("currentness_semantic_fingerprint", None)
        identity.pop("currentness_resolver_id", None)
        identity.pop("currentness_resolver_policy_version", None)
        identity.pop("lifecycle_semantic_fingerprint", None)
        identity.pop("lifecycle_resolver_id", None)
        identity.pop("lifecycle_resolver_policy_version", None)
        return identity

    def revalidate_source_currentness(
        self,
        source_binding: ContextSourceBindingSchema,
    ) -> ContextSourceBindingSchema:
        binding = ContextSourceBindingSchema.model_validate(source_binding)
        registration = self._providers.get(binding.source_family)
        if registration is None:
            raise ContextSourceRefInvalid(
                f"unregistered Context source family: {binding.source_family.value}"
            )
        try:
            resolved = ContextSourceBindingSchema.model_validate(
                registration.source_binding_resolver(binding)
            )
        except ContextRuntimeError:
            raise
        except Exception as exc:
            raise ContextSourceRefInvalid(
                "registered Context source resolver failed"
            ) from exc
        if resolved.source_family is not binding.source_family:
            raise ContextSourceRefInvalid(
                "Context source resolver changed source family"
            )
        if self._binding_source_identity(resolved) != self._binding_source_identity(binding):
            raise ContextSourceStale(
                "exact Context source semantic identity changed during revalidation"
            )
        return resolved

    @staticmethod
    def _decision_sort_key(
        decision: ContextCandidateDecisionSchema,
    ) -> tuple[int, int, int, str, str]:
        binding = decision.source_binding
        return (
            0 if decision.mandatory else 1,
            _TIER_ORDER[decision.priority_tier],
            _FAMILY_ORDER[binding.source_family],
            binding.scope_ref,
            decision.candidate_id,
        )

    @staticmethod
    def _candidate_sort_key(
        candidate: ContextSourceCandidateSchema,
        decision: ContextCandidateDecisionSchema,
    ) -> tuple[int, int, int, str, str]:
        return ApplicationContextRuntime._decision_sort_key(decision)

    @staticmethod
    def _authorized_consumer_decision(
        decision: ConsumerAuthorizationDecisionSchema,
        *,
        candidate: ContextSourceCandidateSchema,
        subject: ContextInvocationSubjectSchema,
    ) -> None:
        if decision.decision is not ConsumerAuthorizationDecisionState.AUTHORIZED:
            raise ContextConsumerAuthorizationRejected(
                "consumer authorization decision is REJECTED"
            )
        invocation = decision.consumer_invocation_ref
        if (
            invocation.run_id != subject.run_id
            or invocation.attempt_id != subject.attempt_id
            or invocation.invocation_id != subject.invocation_id
            or decision.consumer_role != subject.agent_role
            or decision.consumer_scope_ref != subject.consumer_scope_ref
            or decision.context_purpose != subject.context_purpose
        ):
            raise ContextConsumerAuthorizationRejected(
                "consumer authorization decision binds another invocation"
            )
        claim_ref = decision.decision_ref.claim_ref
        source_ref = candidate.source_binding.exact_source_ref
        if canonical_sha256(claim_ref) != canonical_sha256(source_ref):
            raise ContextConsumerAuthorizationRejected(
                "consumer authorization decision binds a different Claim source"
            )

    def resolve_authorized_sources(
        self,
        invocation_subject: ContextInvocationSubjectSchema,
        role_policy: ContextRolePolicySchema,
    ) -> ResolvedContextCandidates:
        subject = ContextInvocationSubjectSchema.model_validate(invocation_subject)
        policy = ContextRolePolicySchema.model_validate(role_policy)
        self._validate_role_policy(subject, policy)
        rules = self._policy_rule_map(policy)

        decisions: list[ContextCandidateDecisionSchema] = []
        eligible: list[ContextSourceCandidateSchema] = []
        consumer_decisions: list[ConsumerAuthorizationDecisionSchema] = []
        candidate_ids: set[str] = set()
        semantic_dedup_keys: set[str] = set()

        for family in sorted(self._providers, key=lambda item: _FAMILY_ORDER[item]):
            registration = self._providers[family]
            try:
                candidates = tuple(registration.candidate_reader(subject))
            except Exception as exc:
                raise ContextSourceRefInvalid(
                    f"Context provider candidate read failed: {family.value}"
                ) from exc

            for raw_candidate in candidates:
                candidate = ContextSourceCandidateSchema.model_validate(raw_candidate)
                if candidate.source_binding.source_family is not family:
                    raise ContextSourceRefInvalid(
                        "Context provider returned another source family"
                    )
                if candidate.candidate_id in candidate_ids:
                    raise ContextSourceRefInvalid(
                        f"duplicate Context candidate_id: {candidate.candidate_id}"
                    )
                candidate_ids.add(candidate.candidate_id)

                resolved_binding = self.revalidate_source_currentness(
                    candidate.source_binding
                )
                candidate = candidate.model_copy(
                    update={"source_binding": resolved_binding}
                )
                if resolved_binding.run_id != subject.run_id:
                    reasons = ("CROSS_RUN_SOURCE_INJECTION",)
                    rule = rules.get(family)
                    decisions.append(
                        ContextCandidateDecisionSchema(
                            candidate_id=candidate.candidate_id,
                            source_binding=resolved_binding,
                            role_visibility=(
                                rule.visibility
                                if rule is not None
                                else ContextRoleVisibility.FORBIDDEN
                            ),
                            hard_eligibility=ContextHardEligibility.REJECTED,
                            hard_reason_codes=reasons,
                            mandatory=False,
                            priority_tier=(
                                rule.priority_tier
                                if rule is not None
                                else ContextPriorityTier.P4_HISTORICAL_OPTIONAL
                            ),
                            conflict_group_ref=candidate.conflict_group_ref,
                            dedup_group_ref=candidate.dedup_group_ref,
                        )
                    )
                    continue

                rule = rules.get(family)
                if rule is None or rule.visibility is ContextRoleVisibility.FORBIDDEN:
                    decisions.append(
                        ContextCandidateDecisionSchema(
                            candidate_id=candidate.candidate_id,
                            source_binding=resolved_binding,
                            role_visibility=ContextRoleVisibility.FORBIDDEN,
                            hard_eligibility=ContextHardEligibility.REJECTED,
                            hard_reason_codes=("ROLE_SOURCE_FORBIDDEN",),
                            mandatory=False,
                            priority_tier=(
                                rule.priority_tier
                                if rule is not None
                                else ContextPriorityTier.P4_HISTORICAL_OPTIONAL
                            ),
                            conflict_group_ref=candidate.conflict_group_ref,
                            dedup_group_ref=candidate.dedup_group_ref,
                        )
                    )
                    continue

                try:
                    source_eligible, reason_codes = (
                        registration.hard_eligibility_validator(
                            resolved_binding,
                            subject,
                        )
                    )
                except Exception as exc:
                    raise ContextSourceStale(
                        f"Context source eligibility validation failed: {family.value}"
                    ) from exc
                reason_codes = _canonical_codes(reason_codes)
                if not source_eligible:
                    decisions.append(
                        ContextCandidateDecisionSchema(
                            candidate_id=candidate.candidate_id,
                            source_binding=resolved_binding,
                            role_visibility=rule.visibility,
                            hard_eligibility=ContextHardEligibility.REJECTED,
                            hard_reason_codes=reason_codes or (
                                "SOURCE_NOT_HARD_ELIGIBLE",
                            ),
                            mandatory=False,
                            priority_tier=rule.priority_tier,
                            conflict_group_ref=candidate.conflict_group_ref,
                            dedup_group_ref=candidate.dedup_group_ref,
                        )
                    )
                    continue

                consumer_decision = None
                if rule.visibility is ContextRoleVisibility.CONDITIONAL:
                    if registration.consumer_authorization_resolver is None:
                        decisions.append(
                            ContextCandidateDecisionSchema(
                                candidate_id=candidate.candidate_id,
                                source_binding=resolved_binding,
                                role_visibility=rule.visibility,
                                hard_eligibility=ContextHardEligibility.REJECTED,
                                hard_reason_codes=("CONDITIONAL_AUTH_RESOLVER_MISSING",),
                                mandatory=False,
                                priority_tier=rule.priority_tier,
                                conflict_group_ref=candidate.conflict_group_ref,
                                dedup_group_ref=candidate.dedup_group_ref,
                            )
                        )
                        continue
                    try:
                        consumer_decision = (
                            registration.consumer_authorization_resolver(
                                candidate,
                                subject,
                                rule,
                            )
                        )
                        consumer_decision = (
                            ConsumerAuthorizationDecisionSchema.model_validate(
                                consumer_decision
                            )
                        )
                        self._authorized_consumer_decision(
                            consumer_decision,
                            candidate=candidate,
                            subject=subject,
                        )
                    except ContextConsumerAuthorizationRejected:
                        decisions.append(
                            ContextCandidateDecisionSchema(
                                candidate_id=candidate.candidate_id,
                                source_binding=resolved_binding,
                                role_visibility=rule.visibility,
                                hard_eligibility=ContextHardEligibility.REJECTED,
                                hard_reason_codes=("CONSUMER_AUTH_REJECTED",),
                                mandatory=False,
                                priority_tier=rule.priority_tier,
                                conflict_group_ref=candidate.conflict_group_ref,
                                dedup_group_ref=candidate.dedup_group_ref,
                            )
                        )
                        continue

                semantic_dedup_key = canonical_sha256(
                    {
                        "source_binding": resolved_binding.semantic_identity(),
                        "representation_contracts": [
                            {
                                "level": item.representation_level.value,
                                "projection_schema_id": item.projection_schema_id,
                                "projection_schema_version": item.projection_schema_version,
                                "representation_policy_id": item.representation_policy_id,
                                "representation_policy_version": item.representation_policy_version,
                                "payload_hash": item.materialized_payload_sha256,
                            }
                            for item in candidate.representations
                        ],
                    }
                )
                if semantic_dedup_key in semantic_dedup_keys:
                    decisions.append(
                        ContextCandidateDecisionSchema(
                            candidate_id=candidate.candidate_id,
                            source_binding=resolved_binding,
                            role_visibility=rule.visibility,
                            hard_eligibility=ContextHardEligibility.REJECTED,
                            hard_reason_codes=("EXACT_DUPLICATE_SOURCE_PROJECTION",),
                            mandatory=False,
                            priority_tier=rule.priority_tier,
                            conflict_group_ref=candidate.conflict_group_ref,
                            dedup_group_ref=(
                                candidate.dedup_group_ref
                                or "dedup_" + semantic_dedup_key
                            ),
                        )
                    )
                    continue
                semantic_dedup_keys.add(semantic_dedup_key)

                decision = ContextCandidateDecisionSchema(
                    candidate_id=candidate.candidate_id,
                    source_binding=resolved_binding,
                    role_visibility=rule.visibility,
                    hard_eligibility=ContextHardEligibility.ELIGIBLE,
                    hard_reason_codes=(),
                    mandatory=rule.mandatory,
                    priority_tier=rule.priority_tier,
                    relevance_strategy_id=(
                        "registered_deterministic_relevance_v1"
                        if candidate.relevance_evidence is not None
                        else None
                    ),
                    relevance_strategy_version=(
                        "v1" if candidate.relevance_evidence is not None else None
                    ),
                    relevance_evidence=candidate.relevance_evidence,
                    conflict_group_ref=candidate.conflict_group_ref,
                    dedup_group_ref=candidate.dedup_group_ref,
                )
                decisions.append(decision)
                eligible.append(candidate)
                if consumer_decision is not None:
                    consumer_decisions.append(consumer_decision)

        decision_by_id = {item.candidate_id: item for item in decisions}

        # A Role-Policy mandatory source family is an invocation requirement,
        # not a ranking preference. Missing provider rows, stale rows, rejected
        # rows, or consumer-auth rejection therefore fail the invocation rather
        # than silently producing a package without the mandatory authority.
        eligible_families = {
            item.source_binding.source_family
            for item in decisions
            if item.hard_eligibility is ContextHardEligibility.ELIGIBLE
        }
        missing_mandatory = tuple(
            sorted(
                (
                    rule.source_family.value
                    for rule in policy.source_rules
                    if rule.mandatory
                    and rule.source_family not in eligible_families
                )
            )
        )
        if missing_mandatory:
            raise ContextRoleNotAuthorized(
                "mandatory authorized Context source unavailable: "
                + ",".join(missing_mandatory)
            )

        ordered_decisions = tuple(
            sorted(decisions, key=self._decision_sort_key)
        )
        ordered_candidates = tuple(
            sorted(
                eligible,
                key=lambda item: self._candidate_sort_key(
                    item,
                    decision_by_id[item.candidate_id],
                ),
            )
        )
        ordered_consumer = tuple(
            sorted(
                consumer_decisions,
                key=lambda item: item.decision_ref.decision_id,
            )
        )
        return ResolvedContextCandidates(
            decisions=ordered_decisions,
            eligible_candidates=ordered_candidates,
            consumer_authorizations=ordered_consumer,
        )

    @staticmethod
    def _representation_map(
        candidate: ContextSourceCandidateSchema,
    ) -> dict[ContextRepresentationLevel, ContextRepresentationOptionSchema]:
        return {item.representation_level: item for item in candidate.representations}

    @staticmethod
    def _rule_for_candidate(
        candidate: ContextSourceCandidateSchema,
        role_policy: ContextRolePolicySchema,
    ) -> ContextRoleSourceRuleSchema:
        for rule in role_policy.source_rules:
            if rule.source_family is candidate.source_binding.source_family:
                return rule
        raise ContextPolicyUnresolved(
            "eligible candidate lost its exact Role Policy source rule"
        )

    @staticmethod
    def _levels_high_to_low(
        rule: ContextRoleSourceRuleSchema,
        candidate: ContextSourceCandidateSchema,
    ) -> tuple[ContextRepresentationLevel, ...]:
        available = ApplicationContextRuntime._representation_map(candidate)
        levels = tuple(
            item
            for item in sorted(
                rule.allowed_representation_levels,
                key=lambda level: _REPRESENTATION_ORDER[level],
            )
            if item in available
        )
        if not levels:
            raise ContextRepresentationNotAuthorized(
                f"no registered representation exists for {candidate.candidate_id}"
            )
        floor_index = _REPRESENTATION_ORDER[rule.minimum_representation_floor]
        levels = tuple(
            item
            for item in levels
            if _REPRESENTATION_ORDER[item] <= floor_index
        )
        if rule.minimum_representation_floor not in levels:
            raise ContextRepresentationNotAuthorized(
                "mandatory representation floor is unavailable"
            )
        return levels

    @staticmethod
    def _instruction_binding_matches_material(
        binding: InstructionBindingSchema,
        material: ContextInstructionMaterialSchema,
    ) -> None:
        checks = (
            (
                binding.system_instruction_sha256,
                canonical_sha256(material.system_instruction),
                "system instruction",
            ),
            (
                binding.role_instruction_sha256,
                canonical_sha256(material.role_instruction),
                "role instruction",
            ),
            (
                binding.prompt_template_sha256,
                canonical_sha256(material.prompt_template),
                "prompt template",
            ),
        )
        for expected, actual, label in checks:
            if expected != actual:
                raise ContextFinalInputHashMismatch(f"{label} hash mismatch")
        if binding.task_output_contract_ref is None:
            if material.task_output_contract is not None:
                raise ContextPostFingerprintMutation(
                    "task output contract material exists without exact binding"
                )
        else:
            if material.task_output_contract is None:
                raise ContextFinalInputHashMismatch(
                    "task output contract binding has no exact material"
                )
            if binding.task_output_contract_sha256 != canonical_sha256(
                material.task_output_contract
            ):
                raise ContextFinalInputHashMismatch(
                    "task output contract hash mismatch"
                )

        bound_controls = {
            item.control_ref: item for item in binding.dynamic_control_bindings
        }
        payload_controls = dict(material.dynamic_control_payloads)
        if set(bound_controls) != set(payload_controls):
            raise ContextPostFingerprintMutation(
                "dynamic control material/binding set mismatch"
            )
        for ref, item in bound_controls.items():
            if item.control_sha256 != canonical_sha256(payload_controls[ref]):
                raise ContextFinalInputHashMismatch(
                    f"dynamic control payload hash mismatch: {ref}"
                )

    @staticmethod
    def _render_final_prompt(
        *,
        instruction_material: ContextInstructionMaterialSchema,
        rendered_context: str,
    ) -> dict[str, Any]:
        return {
            "input_kind": ContextFinalInputKind.MESSAGES.value,
            "messages": [
                {
                    "role": "system",
                    "content": instruction_material.system_instruction,
                },
                {
                    "role": "system",
                    "content": instruction_material.role_instruction,
                },
                {
                    "role": "system",
                    "content": instruction_material.prompt_template,
                },
                *(
                    [
                        {
                            "role": "system",
                            "content": instruction_material.task_output_contract,
                        }
                    ]
                    if instruction_material.task_output_contract is not None
                    else []
                ),
                *[
                    {
                        "role": "system",
                        # ModelGateway's stable message contract is role/content.
                        # Keep the control identity model-visible inside content so
                        # ContextPackage final-input hashing is byte-semantic with
                        # every supported Gateway adapter; do not rely on a dropped
                        # OpenAI-style `name` extension.
                        "content": f"CONTROL_REF={ref}\n{payload}",
                    }
                    for ref, payload in instruction_material.dynamic_control_payloads
                ],
                {
                    "role": "user",
                    "content": rendered_context,
                },
            ],
        }

    def _build_materializations(
        self,
        *,
        resolved: ResolvedContextCandidates,
        role_policy: ContextRolePolicySchema,
        capability: ModelContextCapabilityBindingSchema,
        reserved_instruction_tokens: int,
        reserved_output_tokens: int,
        safety_margin_tokens: int,
        instruction_material: ContextInstructionMaterialSchema,
    ) -> tuple[
        tuple[ContextMaterializationSchema, ...],
        str,
        int,
        tuple[str, ...],
    ]:
        policy = ContextRolePolicySchema.model_validate(role_policy)
        decision_by_id = {item.candidate_id: item for item in resolved.decisions}
        selected_levels: dict[str, int] = {}
        level_options: dict[str, tuple[ContextRepresentationLevel, ...]] = {}
        candidates = tuple(resolved.eligible_candidates)
        for candidate in candidates:
            rule = self._rule_for_candidate(candidate, policy)
            level_options[candidate.candidate_id] = self._levels_high_to_low(
                rule,
                candidate,
            )
            selected_levels[candidate.candidate_id] = 0

        degradation_actions: list[str] = []
        conflict_counts: dict[str, int] = {}
        for candidate in candidates:
            if candidate.conflict_group_ref is not None:
                conflict_counts[candidate.conflict_group_ref] = (
                    conflict_counts.get(candidate.conflict_group_ref, 0) + 1
                )
        conflict_protected_ids = {
            candidate.candidate_id
            for candidate in candidates
            if candidate.conflict_group_ref is not None
            and conflict_counts.get(candidate.conflict_group_ref, 0) > 1
        }
        max_iterations = max(
            8,
            sum(len(value) for value in level_options.values())
            + len(candidates)
            + 4,
        )

        for _ in range(max_iterations):
            items: list[ContextItemSchema] = []
            materializations: list[ContextMaterializationSchema] = []
            for candidate in candidates:
                if selected_levels[candidate.candidate_id] < 0:
                    continue
                levels = level_options[candidate.candidate_id]
                level = levels[selected_levels[candidate.candidate_id]]
                option = self._representation_map(candidate)[level]
                decision = decision_by_id[candidate.candidate_id]
                payload_text = _payload_text(option.materialized_payload)
                items.append(
                    ContextItemSchema(
                        item_id=candidate.candidate_id,
                        source_type=candidate.source_binding.source_family.value,
                        title=candidate.title,
                        content=payload_text,
                        priority=100 - _TIER_ORDER[decision.priority_tier] * 10,
                        required=(
                            decision.mandatory
                            or candidate.candidate_id in conflict_protected_ids
                        ),
                        truncate_allowed=False,
                        metadata={
                            "target_context_v1": True,
                            "priority_tier": decision.priority_tier.value,
                            "representation_level": level.value,
                        },
                    )
                )
                materializations.append(
                    ContextMaterializationSchema(
                        candidate_id=candidate.candidate_id,
                        source_binding_ref=candidate.source_binding.binding_id(),
                        representation_level=level,
                        projection_schema_id=option.projection_schema_id,
                        projection_schema_version=option.projection_schema_version,
                        representation_policy_id=option.representation_policy_id,
                        representation_policy_version=option.representation_policy_version,
                        materialized_payload=option.materialized_payload,
                        materialized_payload_sha256=option.materialized_payload_sha256,
                        estimated_tokens=self.estimator.estimate(payload_text),
                        protected_field_evidence=option.protected_field_evidence,
                    )
                )

            usable_input = (
                capability.model_context_window
                - reserved_instruction_tokens
                - reserved_output_tokens
                - safety_margin_tokens
            )
            if usable_input <= 0:
                raise ContextBudgetExceeded(
                    "CONTEXT_BUDGET_EXCEEDED: no usable context token budget"
                )
            request = ContextBuildRequestSchema(
                task_id=resolved.decisions[0].source_binding.run_id if resolved.decisions else "context",
                run_id=(
                    resolved.decisions[0].source_binding.run_id
                    if resolved.decisions
                    else "context"
                ),
                call_purpose="target_context_materialization",
                items=items,
                max_context_chars=max(256, usable_input * 6),
                max_input_tokens=capability.model_context_window,
                reserved_output_tokens=(
                    reserved_instruction_tokens
                    + reserved_output_tokens
                    + safety_margin_tokens
                ),
                safety_margin_tokens=0,
                metadata={"target_context_v1": True},
            )
            try:
                package = self.context_manager.build(
                    request,
                    ordering_key=lambda item: self._decision_sort_key(
                        decision_by_id[item.item_id]
                    ),
                )
                # Admission covers the exact final messages, including JSON-string
                # escaping and instructions, on every controlled degradation step.
                final_tokens = estimate_model_input(
                    self._render_final_prompt(
                        instruction_material=instruction_material,
                        rendered_context=package.rendered_context,
                    ),
                    estimator=self.estimator,
                )
                if (final_tokens + reserved_output_tokens + safety_margin_tokens
                        > capability.model_context_window):
                    raise ContextBudgetExceededError(
                        "final serialized model input cannot fit"
                    )
                selected_ids = {item.item_id for item in package.selected_items}
                ordered_materializations = tuple(
                    item
                    for item in materializations
                    if item.candidate_id in selected_ids
                )
                return (
                    ordered_materializations,
                    package.rendered_context,
                    package.budget.estimated_input_tokens,
                    tuple(degradation_actions),
                )
            except ContextBudgetExceededError:
                optional = [
                    candidate
                    for candidate in reversed(candidates)
                    if selected_levels[candidate.candidate_id] >= 0
                    and not decision_by_id[candidate.candidate_id].mandatory
                    and candidate.candidate_id not in conflict_protected_ids
                ]
                if optional:
                    victim = optional[0]
                    selected_levels[victim.candidate_id] = -1
                    degradation_actions.append(
                        f"DROP_OPTIONAL:{victim.candidate_id}"
                    )
                    continue

                downgrade_candidates = [
                    candidate
                    for candidate in reversed(candidates)
                    if selected_levels[candidate.candidate_id] >= 0
                    and selected_levels[candidate.candidate_id] + 1
                    < len(level_options[candidate.candidate_id])
                ]
                if downgrade_candidates:
                    victim = downgrade_candidates[0]
                    selected_levels[victim.candidate_id] += 1
                    new_level = level_options[victim.candidate_id][
                        selected_levels[victim.candidate_id]
                    ]
                    degradation_actions.append(
                        f"DOWNGRADE:{victim.candidate_id}:{new_level.value}"
                    )
                    continue
                raise ContextBudgetExceeded(
                    "CONTEXT_BUDGET_EXCEEDED: mandatory representation floor cannot fit"
                )

        raise ContextBudgetExceeded(
            "CONTEXT_BUDGET_EXCEEDED: deterministic degradation did not converge"
        )

    def materialize_context_package(
        self,
        *,
        invocation_subject: ContextInvocationSubjectSchema,
        role_policy: ContextRolePolicySchema,
        policy_binding: ContextPolicyBindingSchema,
        model_capability_binding: ModelContextCapabilityBindingSchema,
        instruction_binding: InstructionBindingSchema,
        instruction_material: ContextInstructionMaterialSchema,
        reserved_instruction_tokens: int,
        reserved_output_tokens: int,
        safety_margin_tokens: int,
    ) -> ContextPackageSchema:
        subject = ContextInvocationSubjectSchema.model_validate(invocation_subject)
        policy = ContextRolePolicySchema.model_validate(role_policy)
        binding = ContextPolicyBindingSchema.model_validate(policy_binding)
        capability = ModelContextCapabilityBindingSchema.model_validate(
            model_capability_binding
        )
        instruction = InstructionBindingSchema.model_validate(instruction_binding)
        material = ContextInstructionMaterialSchema.model_validate(
            instruction_material
        )
        self._validate_role_policy(subject, policy)
        if (
            binding.source_catalog_id != self.SOURCE_CATALOG_ID
            or binding.source_catalog_version != self.SOURCE_CATALOG_VERSION
            or binding.role_policy_id != policy.role_policy_id
            or binding.role_policy_version != policy.role_policy_version
        ):
            raise ContextPolicyUnresolved(
                "ContextPolicyBinding does not match active source catalog/Role Policy"
            )
        self._instruction_binding_matches_material(instruction, material)

        resolved = self.resolve_authorized_sources(subject, policy)
        for consumer_decision in resolved.consumer_authorizations:
            if (
                consumer_decision.policy_id
                != binding.consumer_authorization_policy_id
                or consumer_decision.policy_version
                != binding.consumer_authorization_policy_version
            ):
                raise ContextPolicyUnresolved(
                    "consumer authorization decision policy binding mismatch"
                )
        (
            selected_materializations,
            rendered_context,
            estimated_context_tokens,
            degradation_actions,
        ) = self._build_materializations(
            resolved=resolved,
            role_policy=policy,
            capability=capability,
            reserved_instruction_tokens=reserved_instruction_tokens,
            reserved_output_tokens=reserved_output_tokens,
            safety_margin_tokens=safety_margin_tokens,
            instruction_material=material,
        )

        final_payload = self._render_final_prompt(
            instruction_material=material,
            rendered_context=rendered_context,
        )
        final_hash = canonical_sha256(final_payload)
        rendered_hash = canonical_sha256(rendered_context)
        estimated_final_tokens = estimate_model_input(final_payload, estimator=self.estimator)
        usable_context_tokens = (
            capability.model_context_window
            - reserved_instruction_tokens
            - reserved_output_tokens
            - safety_margin_tokens
        )
        budget = ContextBudgetBindingSchema(
            model_capability_binding=capability,
            model_context_window=capability.model_context_window,
            reserved_instruction_tokens=reserved_instruction_tokens,
            reserved_output_tokens=reserved_output_tokens,
            safety_margin_tokens=safety_margin_tokens,
            usable_context_tokens=usable_context_tokens,
            estimated_selected_tokens=estimated_context_tokens,
            estimated_final_input_tokens=estimated_final_tokens,
            overflow_policy_id=binding.budget_policy_id,
            overflow_policy_version=binding.budget_policy_version,
            degradation_actions=_canonical_codes(degradation_actions),
        )
        final_input_material = FinalModelInputMaterialSchema(
            input_kind=ContextFinalInputKind.MESSAGES,
            input_schema_id="canonical_messages_v1",
            input_schema_version="v1",
            canonicalization_policy_id="canonical_json",
            canonicalization_policy_version="v1",
            inline_final_input_payload=final_payload,
            immutable_final_input_content_ref=None,
            final_model_input_sha256=final_hash,
        )
        model_input = ModelInvocationInputEnvelopeSchema(
            invocation_subject=subject,
            instruction_binding=instruction,
            ordered_context_materializations=selected_materializations,
            rendered_context_sha256=rendered_hash,
            final_messages_or_prompt_sha256=final_hash,
            final_input_material=final_input_material,
            final_model_input_sha256=final_hash,
            budget_binding=budget,
        )

        # Build the exact semantic preimage without audit-only created_at.
        semantic_preimage = {
            "schema_version": "context_package_v1",
            "invocation_subject": subject.model_dump(mode="json"),
            "policy_binding": binding.model_dump(mode="json"),
            "candidate_decisions": [
                item.semantic_identity() for item in resolved.decisions
            ],
            "selected_materializations": [
                item.model_dump(mode="json")
                for item in selected_materializations
            ],
            "consumer_authorization_decisions": [
                item.model_dump(mode="json")
                for item in resolved.consumer_authorizations
            ],
            "instruction_binding": instruction.model_dump(mode="json"),
            "budget_binding": budget.model_dump(mode="json"),
            "model_input": model_input.model_dump(mode="json"),
        }
        fingerprint = canonical_sha256(semantic_preimage)
        package_id = "ctxpkg_" + canonical_sha256(
            {
                "contract": "context_package_v1",
                "invocation_subject": subject.model_dump(mode="json"),
                "context_semantic_fingerprint": fingerprint,
            }
        )
        package = ContextPackageSchema(
            package_ref=ContextPackageRefSchema(
                run_id=subject.run_id,
                attempt_id=subject.attempt_id,
                invocation_id=subject.invocation_id,
                context_package_id=package_id,
            ),
            invocation_subject=subject,
            policy_binding=binding,
            candidate_decisions=resolved.decisions,
            selected_materializations=selected_materializations,
            consumer_authorization_decisions=resolved.consumer_authorizations,
            instruction_binding=instruction,
            budget_binding=budget,
            model_input=model_input,
            context_semantic_fingerprint=fingerprint,
            created_at=self.clock(),
        )
        if self.package_store is not None:
            package = self.package_store.put_if_absent_or_same(package)
        observer = current_diagnostics()
        if observer.enabled:
            # The finite current invocation is the capture boundary.  Candidate
            # payloads are not deep-copied before the session's item/byte caps.
            selected_ids = {item.candidate_id for item in selected_materializations}
            observer.emit_pool({"diagnostic_key": "P-CONTEXT-CANDIDATES",
                "unit_id": "D04", "run_id": subject.run_id,
                "invocation_id": subject.invocation_id,
                "pool_id": package_id + ":candidates",
                "declared_count": len(resolved.decisions)}, ({
                    "candidate_id": item.candidate_id,
                    "ref": item.source_binding.exact_source_ref,
                    "hash": item.source_binding.source_semantic_fingerprint,
                    "run_id": item.source_binding.run_id,
                    "scope": item.source_binding.scope_ref,
                    "state": item.source_binding.currentness_binding.status_payload,
                    "selected": item.candidate_id in selected_ids,
                    "hard_eligibility": item.hard_eligibility.value,
                    "drop_reason": (list(item.hard_reason_codes) or
                        ([] if item.candidate_id in selected_ids else ["NOT_SELECTED_BY_CONTEXT_POLICY"])),
                } for item in resolved.decisions))
            observer.emit_pool({"diagnostic_key": "P-CONTEXT-SELECTED",
                "unit_id": "D04", "run_id": subject.run_id,
                "invocation_id": subject.invocation_id,
                "pool_id": package_id + ":selected",
                "declared_count": len(selected_materializations)}, ({
                    "candidate_id": item.candidate_id,
                    "source_binding_ref": item.source_binding_ref,
                    "representation": item.representation_level.value,
                    "representation_hash": item.materialized_payload_sha256,
                    "protected_fields": list(item.protected_field_evidence),
                    "payload": item.materialized_payload,
                } for item in selected_materializations))
            observer.emit({"event_type": "P-CONTEXT", "unit_id": "D04",
                "run_id": subject.run_id, "attempt_id": subject.attempt_id,
                "invocation_id": subject.invocation_id,
                "context_package_ref": {"run_id": package.package_ref.run_id,
                    "attempt_id": package.package_ref.attempt_id,
                    "invocation_id": package.package_ref.invocation_id,
                    "context_package_id": package.package_ref.context_package_id},
                "context_fingerprint": fingerprint,
                "final_input_hash": final_hash,
                "final_input_material": final_payload,
                "representation_kind": "CONTEXT_FINAL_INPUT_MATERIAL",
                "estimated_final_input_tokens": estimated_final_tokens})
        return package

    @staticmethod
    def _active_policy_versions_match(
        package: ContextPackageSchema,
        active_policy_versions: Mapping[str, str],
    ) -> bool:
        expected = {
            "source_catalog_version": package.policy_binding.source_catalog_version,
            "role_policy_version": package.policy_binding.role_policy_version,
            "cross_section_policy_version": (
                package.policy_binding.cross_section_policy_version
            ),
            "consumer_authorization_policy_version": (
                package.policy_binding.consumer_authorization_policy_version
            ),
            "selection_policy_version": package.policy_binding.selection_policy_version,
            "budget_policy_version": package.policy_binding.budget_policy_version,
            "representation_policy_version": (
                package.policy_binding.representation_policy_version
            ),
            "model_capability_fingerprint": (
                package.budget_binding.model_capability_binding.capability_semantic_fingerprint
            ),
            "token_estimator_fingerprint": (
                package.budget_binding.model_capability_binding.token_estimator_semantic_fingerprint
            ),
        }
        return all(active_policy_versions.get(key) == value for key, value in expected.items())

    def _current_reuse_reasons(
        self,
        package: ContextPackageSchema,
        active_policy_versions: Mapping[str, str],
    ) -> tuple[str, ...]:
        reasons: list[str] = []
        if not self._active_policy_versions_match(package, active_policy_versions):
            reasons.append("POLICY_OR_CAPABILITY_BINDING_CHANGED")
        for decision in package.candidate_decisions:
            if decision.hard_eligibility is not ContextHardEligibility.ELIGIBLE:
                continue
            try:
                current = self.revalidate_source_currentness(decision.source_binding)
            except ContextRuntimeError:
                reasons.append(
                    f"SOURCE_REVALIDATION_FAILED:{decision.candidate_id}"
                )
                continue
            if current.semantic_identity() != decision.source_binding.semantic_identity():
                reasons.append(
                    f"SOURCE_STATUS_CHANGED:{decision.candidate_id}"
                )
        return _canonical_codes(reasons)

    def rebuild_for_replay(
        self,
        package_ref: ContextPackageRefSchema,
        active_policy_versions: Mapping[str, str],
    ) -> ContextReplayResult:
        ref = ContextPackageRefSchema.model_validate(package_ref)
        if self.package_store is None:
            return ContextReplayResult(
                state=ContextPackageReplayState.REBUILD_REQUIRED,
                package=None,
                reason_codes=("PACKAGE_STORE_NOT_CONFIGURED",),
            )
        try:
            package = self.package_store.resolve(ref)
        except Exception as exc:
            return ContextReplayResult(
                state=ContextPackageReplayState.INTEGRITY_INVALID,
                package=None,
                reason_codes=("CONTEXT_PACKAGE_INTEGRITY_INVALID",),
            )
        if package is None:
            return ContextReplayResult(
                state=ContextPackageReplayState.REBUILD_REQUIRED,
                package=None,
                reason_codes=("CONTEXT_PACKAGE_MISSING",),
            )
        try:
            package = ContextPackageSchema.model_validate(package)
        except Exception:
            return ContextReplayResult(
                state=ContextPackageReplayState.INTEGRITY_INVALID,
                package=None,
                reason_codes=("CONTEXT_PACKAGE_INTEGRITY_INVALID",),
            )
        reasons = self._current_reuse_reasons(package, active_policy_versions)
        if reasons:
            return ContextReplayResult(
                state=ContextPackageReplayState.REBUILD_REQUIRED,
                package=package,
                reason_codes=reasons,
            )
        return ContextReplayResult(
            state=ContextPackageReplayState.CURRENTLY_REUSABLE,
            package=package,
            reason_codes=("EXACT_BINDINGS_CURRENT",),
        )

    def validate_pre_model_call_current(
        self,
        package: ContextPackageSchema,
        active_policy_versions: Mapping[str, str],
    ) -> ContextPackageSchema:
        candidate = ContextPackageSchema.model_validate(package)
        reasons = self._current_reuse_reasons(
            candidate,
            active_policy_versions,
        )
        if reasons:
            raise ContextSourceStale(
                "pre-model-call Context currentness guard rejected package: "
                + ",".join(reasons)
            )
        return candidate


__all__ = [
    "ApplicationContextRuntime",
    "ContextBudgetExceeded",
    "ContextConsumerAuthorizationRejected",
    "ContextFinalInputHashMismatch",
    "ContextPackageConflict",
    "ContextPackageIntegrityInvalid",
    "ContextPackageMissing",
    "ContextPackageStore",
    "ContextPolicyUnresolved",
    "ContextPostFingerprintMutation",
    "ContextProjectionHashMismatch",
    "ContextProjectionSchemaInvalid",
    "ContextReplayResult",
    "ContextRepresentationNotAuthorized",
    "ContextRoleNotAuthorized",
    "ContextRuntimeError",
    "ContextSourceProviderRegistration",
    "ContextSourceRefInvalid",
    "ContextSourceScopeMismatch",
    "ContextSourceStale",
    "InMemoryContextPackageStore",
    "ResolvedContextCandidates",
]
