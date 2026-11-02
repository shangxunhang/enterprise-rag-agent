from __future__ import annotations

from datetime import datetime, timezone

import pytest

from apps.enterprise_document.services.agent_rag_v1.context_runtime import (
    ApplicationContextRuntime,
    ContextBudgetExceeded,
    ContextPostFingerprintMutation,
    ContextRoleNotAuthorized,
    ContextSourceProviderRegistration,
    ContextSourceStale,
    InMemoryContextPackageStore,
)
from core.hashing import canonical_sha256
from schemas.context import (
    AuthorizationEvidenceBindingSchema,
    ConsumerAuthorizationDecisionRefSchema,
    ConsumerAuthorizationDecisionSchema,
    ConsumerAuthorizationDecisionState,
    ConsumerInvocationRefSchema,
    ContextInstructionMaterialSchema,
    ContextInvocationSubjectSchema,
    ContextPackageReplayState,
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
    ContextSourceStatusBindingSchema,
    DynamicControlBindingSchema,
    InstructionBindingSchema,
    IntrinsicActiveVerifiedClaimAssessmentBindingSchema,
    ModelContextCapabilityBindingSchema,
)


def _subject(
    *,
    run_id: str = "run-1",
    attempt_id: str = "attempt-1",
    invocation_id: str = "invocation-1",
    role: str = "WRITER",
    mode: str = "NORMAL",
    purpose: str = "SECTION_GENERATION",
    scope: str = "section:S1",
) -> ContextInvocationSubjectSchema:
    return ContextInvocationSubjectSchema(
        run_id=run_id,
        attempt_id=attempt_id,
        invocation_id=invocation_id,
        logical_operation_id="logical-op-1",
        agent_role=role,
        execution_mode=mode,
        context_purpose=purpose,
        consumer_scope_ref=scope,
    )


def _status(
    state: str,
    *,
    assessed_at: str = "2026-09-05T00:00:00Z",
) -> ContextSourceStatusBindingSchema:
    payload = {
        "state": state,
        "assessed_at": assessed_at,
    }
    return ContextSourceStatusBindingSchema(
        status_schema_id="test_status_v1",
        status_schema_version="v1",
        resolver_id="test_status_resolver",
        resolver_policy_version="v1",
        status_payload=payload,
        status_payload_sha256=canonical_sha256(payload),
        status_semantic_fingerprint=canonical_sha256({"state": state}),
    )


def _binding(
    family: ContextSourceFamily,
    *,
    ref: str,
    state: str = "CURRENT",
    run_id: str = "run-1",
    scope: str = "section:S1",
    assessed_at: str = "2026-09-05T00:00:00Z",
) -> ContextSourceBindingSchema:
    exact_ref = {
        "family": family.value,
        "id": ref,
    }
    return ContextSourceBindingSchema(
        source_family=family,
        exact_source_ref=exact_ref,
        source_schema_id=f"{family.value.lower()}_v1",
        source_schema_version="v1",
        source_semantic_fingerprint=canonical_sha256(
            {
                "family": family.value,
                "ref": exact_ref,
                "source_version": 1,
            }
        ),
        authority_owner=f"authority:{family.value}",
        run_id=run_id,
        scope_ref=scope,
        currentness_binding=_status(state, assessed_at=assessed_at),
    )


def _representation(
    level: ContextRepresentationLevel,
    payload,
) -> ContextRepresentationOptionSchema:
    return ContextRepresentationOptionSchema(
        representation_level=level,
        projection_schema_id=f"projection_{level.value.lower()}_v1",
        projection_schema_version="v1",
        representation_policy_id="representation-policy",
        representation_policy_version="v1",
        materialized_payload=payload,
        materialized_payload_sha256=canonical_sha256(payload),
        protected_field_evidence=(),
    )


def _candidate(
    family: ContextSourceFamily,
    *,
    candidate_id: str,
    ref: str,
    state: str = "CURRENT",
    run_id: str = "run-1",
    scope: str = "section:S1",
    assessed_at: str = "2026-09-05T00:00:00Z",
    exact_payload=None,
    compact_payload=None,
    reference_payload=None,
    relevance: dict | None = None,
    conflict_group_ref: str | None = None,
) -> ContextSourceCandidateSchema:
    reps = [
        _representation(
            ContextRepresentationLevel.EXACT,
            exact_payload
            if exact_payload is not None
            else {"candidate_id": candidate_id, "value": "exact"},
        )
    ]
    if compact_payload is not None:
        reps.append(
            _representation(
                ContextRepresentationLevel.STRUCTURED_COMPACT,
                compact_payload,
            )
        )
    if reference_payload is not None:
        reps.append(
            _representation(
                ContextRepresentationLevel.REFERENCE_ONLY,
                reference_payload,
            )
        )
    return ContextSourceCandidateSchema(
        candidate_id=candidate_id,
        source_binding=_binding(
            family,
            ref=ref,
            state=state,
            run_id=run_id,
            scope=scope,
            assessed_at=assessed_at,
        ),
        title=f"{family.value}:{candidate_id}",
        representations=tuple(reps),
        relevance_evidence=relevance,
        conflict_group_ref=conflict_group_ref,
    )


def _rule(
    family: ContextSourceFamily,
    *,
    visibility: ContextRoleVisibility = ContextRoleVisibility.MAY,
    mandatory: bool = False,
    tier: ContextPriorityTier = ContextPriorityTier.P2_RELEVANT_CURRENT,
    allowed: tuple[ContextRepresentationLevel, ...] = (
        ContextRepresentationLevel.EXACT,
    ),
    floor: ContextRepresentationLevel = ContextRepresentationLevel.EXACT,
    conditional_authorization_ref: str | None = None,
) -> ContextRoleSourceRuleSchema:
    return ContextRoleSourceRuleSchema(
        source_family=family,
        visibility=visibility,
        mandatory=mandatory,
        priority_tier=tier,
        allowed_representation_levels=allowed,
        minimum_representation_floor=floor,
        conditional_authorization_ref=conditional_authorization_ref,
    )


def _policy(
    *rules: ContextRoleSourceRuleSchema,
    role: str = "WRITER",
    mode: str = "NORMAL",
    purpose: str = "SECTION_GENERATION",
) -> ContextRolePolicySchema:
    return ContextRolePolicySchema(
        role_policy_id="writer-context-policy",
        role_policy_version="v1",
        agent_role=role,
        execution_mode=mode,
        context_purpose=purpose,
        source_rules=tuple(rules),
    )


def _policy_binding(
    *,
    role_policy_version: str = "v1",
) -> ContextPolicyBindingSchema:
    return ContextPolicyBindingSchema(
        source_catalog_id=ApplicationContextRuntime.SOURCE_CATALOG_ID,
        source_catalog_version=ApplicationContextRuntime.SOURCE_CATALOG_VERSION,
        role_policy_id="writer-context-policy",
        role_policy_version=role_policy_version,
        cross_section_policy_id="cross-section-policy",
        cross_section_policy_version="v1",
        consumer_authorization_policy_id="consumer-auth-policy",
        consumer_authorization_policy_version="v1",
        selection_policy_id="selection-policy",
        selection_policy_version="v1",
        budget_policy_id="budget-policy",
        budget_policy_version="v1",
        representation_policy_id="representation-policy",
        representation_policy_version="v1",
    )


def _capability(
    *,
    window: int = 4096,
) -> ModelContextCapabilityBindingSchema:
    return ModelContextCapabilityBindingSchema(
        model_capability_ref="model-capability:qwen",
        capability_schema_id="model_context_capability_v1",
        capability_schema_version="v1",
        capability_semantic_fingerprint=canonical_sha256(
            {"model": "qwen", "window": window}
        ),
        model_context_window=window,
        safe_max_output_tokens=min(512, window // 2),
        token_estimator_id="deterministic_mixed_text",
        token_estimator_version="v1",
        token_estimator_semantic_fingerprint=canonical_sha256(
            {"estimator": "deterministic_mixed_text", "version": "v1"}
        ),
    )


def _instructions(
    *,
    control_payload: str | None = None,
) -> tuple[InstructionBindingSchema, ContextInstructionMaterialSchema]:
    system = "You are a controlled writer."
    role = "Write only from authorized context."
    template = "Generate the requested section."
    controls = ()
    control_material = ()
    if control_payload is not None:
        controls = (
            DynamicControlBindingSchema(
                control_ref="writer-gate://1",
                control_version="v1",
                control_sha256=canonical_sha256(control_payload),
            ),
        )
        control_material = (("writer-gate://1", control_payload),)
    binding = InstructionBindingSchema(
        system_instruction_ref="instruction://system",
        system_instruction_version="v1",
        system_instruction_sha256=canonical_sha256(system),
        role_instruction_ref="instruction://writer",
        role_instruction_version="v1",
        role_instruction_sha256=canonical_sha256(role),
        prompt_template_ref="template://section",
        prompt_template_version="v1",
        prompt_template_sha256=canonical_sha256(template),
        dynamic_control_bindings=controls,
    )
    material = ContextInstructionMaterialSchema(
        system_instruction=system,
        role_instruction=role,
        prompt_template=template,
        dynamic_control_payloads=control_material,
    )
    return binding, material


def _active_versions(package) -> dict[str, str]:
    return {
        "source_catalog_version": package.policy_binding.source_catalog_version,
        "role_policy_version": package.policy_binding.role_policy_version,
        "cross_section_policy_version": (
            package.policy_binding.cross_section_policy_version
        ),
        "consumer_authorization_policy_version": (
            package.policy_binding.consumer_authorization_policy_version
        ),
        "selection_policy_version": (
            package.policy_binding.selection_policy_version
        ),
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


def _register(
    runtime: ApplicationContextRuntime,
    family: ContextSourceFamily,
    state: dict,
    *,
    consumer_authorization_resolver=None,
) -> None:
    def reader(_subject):
        return tuple(
            item
            for item in state.get("candidates", ())
            if item.source_binding.source_family is family
        )

    def resolver(binding):
        current_state = state.get("status", {}).get(
            binding.exact_source_ref["id"],
            binding.currentness_binding.status_payload["state"],
        )
        assessed_at = state.get("assessed_at", {}).get(
            binding.exact_source_ref["id"],
            binding.currentness_binding.status_payload.get("assessed_at"),
        )
        return binding.model_copy(
            update={
                "currentness_binding": _status(
                    current_state,
                    assessed_at=assessed_at,
                )
            }
        )

    def eligibility(binding, _subject):
        source_state = binding.currentness_binding.status_payload["state"]
        if family is ContextSourceFamily.DURABLE_RUN_MEMORY:
            if source_state == "ACTIVE_RECALL":
                return True, ()
            if source_state == "HISTORICAL_ONLY":
                return False, ("MEMORY_HISTORICAL_ONLY",)
            return False, ("MEMORY_INELIGIBLE",)
        if source_state == "CURRENT":
            return True, ()
        return False, ("SOURCE_NOT_CURRENT",)

    runtime.register_source_provider(
        ContextSourceProviderRegistration(
            source_family=family,
            provider_id=f"provider:{family.value}",
            provider_version="v1",
            candidate_reader=reader,
            source_binding_resolver=resolver,
            hard_eligibility_validator=eligibility,
            consumer_authorization_resolver=consumer_authorization_resolver,
        )
    )


def _materialize(
    runtime: ApplicationContextRuntime,
    *,
    policy: ContextRolePolicySchema,
    subject: ContextInvocationSubjectSchema | None = None,
    capability: ModelContextCapabilityBindingSchema | None = None,
    control_payload: str | None = None,
    reserved_instruction_tokens: int = 64,
    reserved_output_tokens: int = 128,
    safety_margin_tokens: int = 32,
):
    instruction_binding, instruction_material = _instructions(
        control_payload=control_payload
    )
    return runtime.materialize_context_package(
        invocation_subject=subject or _subject(),
        role_policy=policy,
        policy_binding=_policy_binding(
            role_policy_version=policy.role_policy_version
        ),
        model_capability_binding=capability or _capability(),
        instruction_binding=instruction_binding,
        instruction_material=instruction_material,
        reserved_instruction_tokens=reserved_instruction_tokens,
        reserved_output_tokens=reserved_output_tokens,
        safety_margin_tokens=safety_margin_tokens,
    )


def _authorized_claim_decision(candidate, subject, rule):
    claim_ref = candidate.source_binding.exact_source_ref
    invocation_ref = ConsumerInvocationRefSchema(
        run_id=subject.run_id,
        attempt_id=subject.attempt_id,
        invocation_id=subject.invocation_id,
    )
    assessment_preimage = {
        "claim_ref": claim_ref,
        "assessed_verification_revision_ref": {"revision": 1},
        "claim_head_revision": 1,
        "intrinsic_dimension_results": {
            "CLAIM_HEAD_CURRENT": "PASS",
            "VERDICT_VERIFIED": "PASS",
        },
        "intrinsic_result": "ACTIVE_VERIFIED",
        "intrinsic_source_set_sha256": canonical_sha256({"sources": ["E1"]}),
        "intrinsic_resolver_id": "active-verified-claim-resolver",
        "intrinsic_resolver_policy_version": "v1",
    }
    assessment = IntrinsicActiveVerifiedClaimAssessmentBindingSchema(
        **assessment_preimage,
        assessment_semantic_fingerprint=canonical_sha256(assessment_preimage),
    )

    evidence_preimage = {
        "authorization_path": "EXPLICIT_DEPENDENCY",
        "owning_authority_ref": {"section_plan_id": "plan:S1"},
        "owning_authority_schema_id": "section_plan_v1",
        "owning_authority_schema_version": "v1",
        "owning_authority_semantic_fingerprint": canonical_sha256(
            {"plan": "S1", "revision": 1}
        ),
        "matched_dependency_ref": {"dependency_id": "dep:S0->S1"},
        "matched_consistency_subject": None,
        "matched_consistency_domain": None,
    }
    evidence = AuthorizationEvidenceBindingSchema(
        **evidence_preimage,
        evidence_binding_sha256=canonical_sha256(evidence_preimage),
    )
    decision_id_preimage = {
        "contract": "consumer_authorization_decision_v1",
        "consumer_invocation_ref": invocation_ref.model_dump(mode="json"),
        "claim_ref": claim_ref,
        "consumer_role": subject.agent_role,
        "consumer_scope_ref": subject.consumer_scope_ref,
        "context_purpose": subject.context_purpose,
        "intrinsic_claim_assessment_fingerprint": (
            assessment.assessment_semantic_fingerprint
        ),
        "authorization_evidence_sha256": evidence.evidence_binding_sha256,
        "policy_id": rule.conditional_authorization_ref,
        "policy_version": "v1",
    }
    decision_id = "cauth_" + canonical_sha256(decision_id_preimage)
    semantic_preimage = {
        **decision_id_preimage,
        "role_authorized": True,
        "scope_authorized": True,
        "purpose_authorized": True,
        "cross_section_authorized": True,
        "decision": ConsumerAuthorizationDecisionState.AUTHORIZED.value,
        "reason_codes": (),
    }
    return ConsumerAuthorizationDecisionSchema(
        decision_ref=ConsumerAuthorizationDecisionRefSchema(
            consumer_invocation_ref=invocation_ref,
            claim_ref=claim_ref,
            decision_id=decision_id,
        ),
        consumer_invocation_ref=invocation_ref,
        consumer_role=subject.agent_role,
        consumer_scope_ref=subject.consumer_scope_ref,
        context_purpose=subject.context_purpose,
        intrinsic_claim_assessment=assessment,
        authorization_evidence_binding=evidence,
        role_authorized=True,
        scope_authorized=True,
        purpose_authorized=True,
        cross_section_authorized=True,
        policy_id=rule.conditional_authorization_ref,
        policy_version="v1",
        decision=ConsumerAuthorizationDecisionState.AUTHORIZED,
        reason_codes=(),
        decision_semantic_fingerprint=canonical_sha256(semantic_preimage),
    )


def test_build_context_candidates_is_typed_deterministic_and_not_authorization() -> None:
    runtime = ApplicationContextRuntime()
    state = {
        "candidates": (
            _candidate(
                ContextSourceFamily.EVIDENCE_PACKAGE,
                candidate_id="evidence-b",
                ref="evidence-package:b",
            ),
            _candidate(
                ContextSourceFamily.FACT_SNAPSHOT,
                candidate_id="fact-a",
                ref="facts:a",
            ),
        )
    }
    _register(runtime, ContextSourceFamily.EVIDENCE_PACKAGE, state)
    _register(runtime, ContextSourceFamily.FACT_SNAPSHOT, state)

    candidates = runtime.build_context_candidates(_subject())

    assert [item.candidate_id for item in candidates] == ["fact-a", "evidence-b"]
    # Candidate collection itself does not promote Role Policy or hard eligibility.
    assert all(not hasattr(item, "hard_eligibility") for item in candidates)


def test_high_relevance_forbidden_source_is_rejected_before_ranking() -> None:
    runtime = ApplicationContextRuntime()
    state = {
        "candidates": (
            _candidate(
                ContextSourceFamily.DURABLE_RUN_MEMORY,
                candidate_id="memory-high-relevance",
                ref="memory:1",
                state="ACTIVE_RECALL",
                relevance={"score": 9999},
            ),
        )
    }
    _register(runtime, ContextSourceFamily.DURABLE_RUN_MEMORY, state)
    resolved = runtime.resolve_authorized_sources(
        _subject(),
        _policy(
            _rule(
                ContextSourceFamily.DURABLE_RUN_MEMORY,
                visibility=ContextRoleVisibility.FORBIDDEN,
                tier=ContextPriorityTier.P0_MANDATORY_AUTHORITY,
            )
        ),
    )

    assert resolved.eligible_candidates == ()
    assert resolved.decisions[0].hard_eligibility.value == "REJECTED"
    assert resolved.decisions[0].hard_reason_codes == ("ROLE_SOURCE_FORBIDDEN",)
    assert resolved.decisions[0].mandatory is False


def test_required_but_stale_source_cannot_be_rescued_by_required_flag() -> None:
    runtime = ApplicationContextRuntime()
    state = {
        "candidates": (
            _candidate(
                ContextSourceFamily.FACT_SNAPSHOT,
                candidate_id="fact-stale",
                ref="facts:1",
                state="STALE",
            ),
        )
    }
    _register(runtime, ContextSourceFamily.FACT_SNAPSHOT, state)

    with pytest.raises(ContextRoleNotAuthorized, match="mandatory authorized"):
        runtime.resolve_authorized_sources(
            _subject(),
            _policy(
                _rule(
                    ContextSourceFamily.FACT_SNAPSHOT,
                    visibility=ContextRoleVisibility.MUST,
                    mandatory=True,
                    tier=ContextPriorityTier.P0_MANDATORY_AUTHORITY,
                )
            ),
        )


def test_cross_run_source_is_rejected() -> None:
    runtime = ApplicationContextRuntime()
    state = {
        "candidates": (
            _candidate(
                ContextSourceFamily.FACT_SNAPSHOT,
                candidate_id="foreign-run",
                ref="facts:other",
                run_id="run-other",
            ),
        )
    }
    _register(runtime, ContextSourceFamily.FACT_SNAPSHOT, state)
    resolved = runtime.resolve_authorized_sources(
        _subject(),
        _policy(_rule(ContextSourceFamily.FACT_SNAPSHOT)),
    )

    assert resolved.eligible_candidates == ()
    assert resolved.decisions[0].hard_reason_codes == (
        "CROSS_RUN_SOURCE_INJECTION",
    )


@pytest.mark.parametrize(
    ("state_name", "eligible", "reason"),
    [
        ("ACTIVE_RECALL", True, None),
        ("HISTORICAL_ONLY", False, "MEMORY_HISTORICAL_ONLY"),
        ("INELIGIBLE", False, "MEMORY_INELIGIBLE"),
    ],
)
def test_memory_freshness_is_hard_eligibility(
    state_name: str,
    eligible: bool,
    reason: str | None,
) -> None:
    runtime = ApplicationContextRuntime()
    state = {
        "candidates": (
            _candidate(
                ContextSourceFamily.DURABLE_RUN_MEMORY,
                candidate_id="memory-1",
                ref="memory:1",
                state=state_name,
            ),
        )
    }
    _register(runtime, ContextSourceFamily.DURABLE_RUN_MEMORY, state)
    resolved = runtime.resolve_authorized_sources(
        _subject(),
        _policy(_rule(ContextSourceFamily.DURABLE_RUN_MEMORY)),
    )

    assert bool(resolved.eligible_candidates) is eligible
    if reason is not None:
        assert reason in resolved.decisions[0].hard_reason_codes


def test_text_similar_but_distinct_exact_sources_are_not_semantically_deduped() -> None:
    runtime = ApplicationContextRuntime()
    same_payload = {"statement": "same visible text"}
    state = {
        "candidates": (
            _candidate(
                ContextSourceFamily.EVIDENCE_PACKAGE,
                candidate_id="evidence-a",
                ref="evidence-package:a",
                exact_payload=same_payload,
            ),
            _candidate(
                ContextSourceFamily.EVIDENCE_PACKAGE,
                candidate_id="evidence-b",
                ref="evidence-package:b",
                exact_payload=same_payload,
            ),
        )
    }
    _register(runtime, ContextSourceFamily.EVIDENCE_PACKAGE, state)
    resolved = runtime.resolve_authorized_sources(
        _subject(),
        _policy(_rule(ContextSourceFamily.EVIDENCE_PACKAGE)),
    )

    assert [item.candidate_id for item in resolved.eligible_candidates] == [
        "evidence-a",
        "evidence-b",
    ]


def test_historical_memory_may_enter_only_under_explicit_historical_purpose() -> None:
    runtime = ApplicationContextRuntime()
    candidate = _candidate(
        ContextSourceFamily.DURABLE_RUN_MEMORY,
        candidate_id="memory-history",
        ref="memory:history",
        state="HISTORICAL_ONLY",
    )

    def reader(_subject):
        return (candidate,)

    def resolver(binding):
        return binding

    def eligibility(binding, subject):
        if (
            binding.currentness_binding.status_payload["state"] == "HISTORICAL_ONLY"
            and subject.context_purpose == "HISTORICAL_AUDIT"
        ):
            return True, ()
        return False, ("MEMORY_HISTORICAL_ONLY",)

    runtime.register_source_provider(
        ContextSourceProviderRegistration(
            source_family=ContextSourceFamily.DURABLE_RUN_MEMORY,
            provider_id="memory-provider",
            provider_version="v1",
            candidate_reader=reader,
            source_binding_resolver=resolver,
            hard_eligibility_validator=eligibility,
        )
    )

    normal = runtime.resolve_authorized_sources(
        _subject(),
        _policy(_rule(ContextSourceFamily.DURABLE_RUN_MEMORY)),
    )
    assert normal.eligible_candidates == ()

    historical_subject = _subject(purpose="HISTORICAL_AUDIT")
    historical_policy = _policy(
        _rule(ContextSourceFamily.DURABLE_RUN_MEMORY),
        purpose="HISTORICAL_AUDIT",
    )
    historical = runtime.resolve_authorized_sources(
        historical_subject,
        historical_policy,
    )
    assert [item.candidate_id for item in historical.eligible_candidates] == [
        "memory-history"
    ]


def test_deterministic_package_ignores_audit_only_status_timestamp() -> None:
    store = InMemoryContextPackageStore()
    runtime = ApplicationContextRuntime(
        package_store=store,
        clock=lambda: datetime(2026, 9, 5, tzinfo=timezone.utc),
    )
    state = {
        "candidates": (
            _candidate(
                ContextSourceFamily.FACT_SNAPSHOT,
                candidate_id="fact-1",
                ref="facts:1",
                exact_payload={"server_count": 16},
            ),
            _candidate(
                ContextSourceFamily.EVIDENCE_PACKAGE,
                candidate_id="evidence-1",
                ref="evidence-package:1",
                exact_payload={"evidence_ids": ["E1"]},
            ),
        ),
        "assessed_at": {},
    }
    _register(runtime, ContextSourceFamily.EVIDENCE_PACKAGE, state)
    _register(runtime, ContextSourceFamily.FACT_SNAPSHOT, state)
    policy = _policy(
        _rule(
            ContextSourceFamily.FACT_SNAPSHOT,
            mandatory=True,
            tier=ContextPriorityTier.P0_MANDATORY_AUTHORITY,
        ),
        _rule(
            ContextSourceFamily.EVIDENCE_PACKAGE,
            tier=ContextPriorityTier.P1_OPERATION_CRITICAL,
        ),
    )

    first = _materialize(runtime, policy=policy)
    state["assessed_at"]["facts:1"] = "2026-09-05T00:10:00Z"
    state["assessed_at"]["evidence-package:1"] = "2026-09-05T00:10:00Z"
    second = _materialize(runtime, policy=policy)

    assert first.package_ref == second.package_ref
    assert first.context_semantic_fingerprint == second.context_semantic_fingerprint
    assert (
        first.model_input.final_model_input_sha256
        == second.model_input.final_model_input_sha256
    )
    assert [item.candidate_id for item in first.selected_materializations] == [
        "fact-1",
        "evidence-1",
    ]


def test_optional_low_priority_is_dropped_before_mandatory_representation_floor() -> None:
    runtime = ApplicationContextRuntime()
    huge_optional = "optional " * 1200
    evidence_exact = {"text": "evidence " * 500, "ids": ["E1"]}
    evidence_compact = {"ids": ["E1"], "support": "SUPPORTED"}
    shared_state = {
        "candidates": (
            _candidate(
                ContextSourceFamily.EVIDENCE_PACKAGE,
                candidate_id="evidence-required",
                ref="evidence-package:1",
                exact_payload=evidence_exact,
                compact_payload=evidence_compact,
            ),
            _candidate(
                ContextSourceFamily.DURABLE_RUN_MEMORY,
                candidate_id="memory-optional",
                ref="memory:1",
                state="ACTIVE_RECALL",
                exact_payload=huge_optional,
            ),
        )
    }
    _register(runtime, ContextSourceFamily.EVIDENCE_PACKAGE, shared_state)
    _register(runtime, ContextSourceFamily.DURABLE_RUN_MEMORY, shared_state)
    policy = _policy(
        _rule(
            ContextSourceFamily.EVIDENCE_PACKAGE,
            visibility=ContextRoleVisibility.MUST,
            mandatory=True,
            tier=ContextPriorityTier.P0_MANDATORY_AUTHORITY,
            allowed=(
                ContextRepresentationLevel.EXACT,
                ContextRepresentationLevel.STRUCTURED_COMPACT,
            ),
            floor=ContextRepresentationLevel.STRUCTURED_COMPACT,
        ),
        _rule(
            ContextSourceFamily.DURABLE_RUN_MEMORY,
            tier=ContextPriorityTier.P4_HISTORICAL_OPTIONAL,
        ),
    )

    package = _materialize(
        runtime,
        policy=policy,
        capability=_capability(window=900),
        reserved_instruction_tokens=50,
        reserved_output_tokens=100,
        safety_margin_tokens=20,
    )

    actions = package.budget_binding.degradation_actions
    assert "DROP_OPTIONAL:memory-optional" in actions
    selected = {item.candidate_id: item for item in package.selected_materializations}
    assert "memory-optional" not in selected
    assert "evidence-required" in selected


def test_mandatory_exact_fact_overflow_fails_closed() -> None:
    runtime = ApplicationContextRuntime()
    state = {
        "candidates": (
            _candidate(
                ContextSourceFamily.FACT_SNAPSHOT,
                candidate_id="fact-required",
                ref="facts:1",
                exact_payload={"critical_fact": "X" * 10000},
            ),
        )
    }
    _register(runtime, ContextSourceFamily.FACT_SNAPSHOT, state)
    policy = _policy(
        _rule(
            ContextSourceFamily.FACT_SNAPSHOT,
            visibility=ContextRoleVisibility.MUST,
            mandatory=True,
            tier=ContextPriorityTier.P0_MANDATORY_AUTHORITY,
        )
    )

    with pytest.raises(ContextBudgetExceeded, match="mandatory representation floor"):
        _materialize(
            runtime,
            policy=policy,
            capability=_capability(window=512),
            reserved_instruction_tokens=64,
            reserved_output_tokens=128,
            safety_margin_tokens=32,
        )


def test_conflicting_authorized_sources_cannot_be_silently_rank_dropped() -> None:
    runtime = ApplicationContextRuntime()
    state = {
        "candidates": (
            _candidate(
                ContextSourceFamily.EVIDENCE_PACKAGE,
                candidate_id="conflict-a",
                ref="evidence-package:a",
                exact_payload={"claim": "A" * 4000},
                conflict_group_ref="conflict:1",
            ),
            _candidate(
                ContextSourceFamily.EVIDENCE_PACKAGE,
                candidate_id="conflict-b",
                ref="evidence-package:b",
                exact_payload={"claim": "B" * 4000},
                conflict_group_ref="conflict:1",
            ),
        )
    }
    _register(runtime, ContextSourceFamily.EVIDENCE_PACKAGE, state)

    with pytest.raises(ContextBudgetExceeded, match="mandatory representation floor"):
        _materialize(
            runtime,
            policy=_policy(_rule(ContextSourceFamily.EVIDENCE_PACKAGE)),
            capability=_capability(window=512),
            reserved_instruction_tokens=64,
            reserved_output_tokens=128,
            safety_margin_tokens=32,
        )


def test_conditional_active_verified_claim_requires_exact_consumer_authorization() -> None:
    runtime = ApplicationContextRuntime()
    state = {
        "candidates": (
            _candidate(
                ContextSourceFamily.ACTIVE_VERIFIED_CLAIM,
                candidate_id="claim-1",
                ref="claim:1",
                exact_payload={"claim": "verified fact"},
            ),
        )
    }
    _register(
        runtime,
        ContextSourceFamily.ACTIVE_VERIFIED_CLAIM,
        state,
        consumer_authorization_resolver=_authorized_claim_decision,
    )
    policy = _policy(
        _rule(
            ContextSourceFamily.ACTIVE_VERIFIED_CLAIM,
            visibility=ContextRoleVisibility.CONDITIONAL,
            tier=ContextPriorityTier.P2_RELEVANT_CURRENT,
            conditional_authorization_ref="consumer-auth-policy",
        )
    )

    package = _materialize(runtime, policy=policy)

    assert len(package.consumer_authorization_decisions) == 1
    decision = package.consumer_authorization_decisions[0]
    assert decision.decision is ConsumerAuthorizationDecisionState.AUTHORIZED
    assert decision.decision_ref.decision_id.startswith("cauth_")


def test_dynamic_control_is_inside_final_input_fingerprint() -> None:
    runtime = ApplicationContextRuntime()
    state = {
        "candidates": (
            _candidate(
                ContextSourceFamily.FACT_SNAPSHOT,
                candidate_id="fact-1",
                ref="facts:1",
                exact_payload={"server_count": 16},
            ),
        )
    }
    _register(runtime, ContextSourceFamily.FACT_SNAPSHOT, state)
    policy = _policy(_rule(ContextSourceFamily.FACT_SNAPSHOT))

    first = _materialize(
        runtime,
        policy=policy,
        control_payload="WRITE_ALLOWED",
    )
    second = _materialize(
        runtime,
        policy=policy,
        control_payload="WRITE_WITH_CITATIONS_ONLY",
    )

    assert (
        first.model_input.final_model_input_sha256
        != second.model_input.final_model_input_sha256
    )
    assert first.package_ref != second.package_ref


def test_unbound_dynamic_control_is_rejected_before_fingerprint() -> None:
    runtime = ApplicationContextRuntime()
    state = {
        "candidates": (
            _candidate(
                ContextSourceFamily.FACT_SNAPSHOT,
                candidate_id="fact-1",
                ref="facts:1",
            ),
        )
    }
    _register(runtime, ContextSourceFamily.FACT_SNAPSHOT, state)
    binding, _material = _instructions(control_payload=None)
    rogue_material = ContextInstructionMaterialSchema(
        system_instruction="You are a controlled writer.",
        role_instruction="Write only from authorized context.",
        prompt_template="Generate the requested section.",
        dynamic_control_payloads=(("writer-gate://rogue", "ALLOW"),),
    )

    with pytest.raises(ContextPostFingerprintMutation, match="set mismatch"):
        runtime.materialize_context_package(
            invocation_subject=_subject(),
            role_policy=_policy(_rule(ContextSourceFamily.FACT_SNAPSHOT)),
            policy_binding=_policy_binding(),
            model_capability_binding=_capability(),
            instruction_binding=binding,
            instruction_material=rogue_material,
            reserved_instruction_tokens=64,
            reserved_output_tokens=128,
            safety_margin_tokens=32,
        )


def test_replay_reuse_and_policy_or_source_change_require_rebuild() -> None:
    store = InMemoryContextPackageStore()
    runtime = ApplicationContextRuntime(package_store=store)
    state = {
        "candidates": (
            _candidate(
                ContextSourceFamily.FACT_SNAPSHOT,
                candidate_id="fact-1",
                ref="facts:1",
            ),
        ),
        "status": {},
    }
    _register(runtime, ContextSourceFamily.FACT_SNAPSHOT, state)
    package = _materialize(
        runtime,
        policy=_policy(_rule(ContextSourceFamily.FACT_SNAPSHOT)),
    )
    versions = _active_versions(package)

    reusable = runtime.rebuild_for_replay(package.package_ref, versions)
    assert reusable.state is ContextPackageReplayState.CURRENTLY_REUSABLE

    changed_policy = dict(versions)
    changed_policy["role_policy_version"] = "v2"
    policy_rebuild = runtime.rebuild_for_replay(
        package.package_ref,
        changed_policy,
    )
    assert policy_rebuild.state is ContextPackageReplayState.REBUILD_REQUIRED
    assert "POLICY_OR_CAPABILITY_BINDING_CHANGED" in policy_rebuild.reason_codes

    state["status"]["facts:1"] = "STALE"
    source_rebuild = runtime.rebuild_for_replay(package.package_ref, versions)
    assert source_rebuild.state is ContextPackageReplayState.REBUILD_REQUIRED
    assert any(
        code.startswith("SOURCE_STATUS_CHANGED:")
        for code in source_rebuild.reason_codes
    )


def test_pre_model_call_currentness_guard_closes_toctou_window() -> None:
    runtime = ApplicationContextRuntime()
    state = {
        "candidates": (
            _candidate(
                ContextSourceFamily.FACT_SNAPSHOT,
                candidate_id="fact-1",
                ref="facts:1",
            ),
        ),
        "status": {},
    }
    _register(runtime, ContextSourceFamily.FACT_SNAPSHOT, state)
    package = _materialize(
        runtime,
        policy=_policy(_rule(ContextSourceFamily.FACT_SNAPSHOT)),
    )
    versions = _active_versions(package)

    runtime.validate_pre_model_call_current(package, versions)
    state["status"]["facts:1"] = "STALE"
    with pytest.raises(ContextSourceStale, match="pre-model-call"):
        runtime.validate_pre_model_call_current(package, versions)


def test_corrupt_persisted_package_is_integrity_invalid() -> None:
    store = InMemoryContextPackageStore()
    runtime = ApplicationContextRuntime(package_store=store)
    state = {
        "candidates": (
            _candidate(
                ContextSourceFamily.FACT_SNAPSHOT,
                candidate_id="fact-1",
                ref="facts:1",
            ),
        )
    }
    _register(runtime, ContextSourceFamily.FACT_SNAPSHOT, state)
    package = _materialize(
        runtime,
        policy=_policy(_rule(ContextSourceFamily.FACT_SNAPSHOT)),
    )
    corrupt = package.model_copy(
        update={"context_semantic_fingerprint": "0" * 64}
    )
    store._items[package.package_ref.context_package_id] = corrupt

    result = runtime.rebuild_for_replay(
        package.package_ref,
        _active_versions(package),
    )
    assert result.state is ContextPackageReplayState.INTEGRITY_INVALID
    assert result.reason_codes == ("CONTEXT_PACKAGE_INTEGRITY_INVALID",)


def test_missing_persisted_package_requires_rebuild_not_truth_reconstruction() -> None:
    runtime = ApplicationContextRuntime(
        package_store=InMemoryContextPackageStore()
    )
    ref = {
        "run_id": "run-1",
        "attempt_id": "attempt-1",
        "invocation_id": "invocation-1",
        "context_package_id": "ctxpkg_missing",
    }
    from schemas.context import ContextPackageRefSchema

    result = runtime.rebuild_for_replay(
        ContextPackageRefSchema(**ref),
        {},
    )
    assert result.state is ContextPackageReplayState.REBUILD_REQUIRED
    assert result.reason_codes == ("CONTEXT_PACKAGE_MISSING",)
