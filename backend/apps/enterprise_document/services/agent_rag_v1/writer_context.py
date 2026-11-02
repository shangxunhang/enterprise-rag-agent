"""Wave-13 production composition for SIF-10 Writer Context.

This module is deliberately a composer, not a new truth owner.  It resolves the
existing canonical Plan / FactSnapshot / EvidencePackage / Lifecycle owners,
registers them as SIF-10 Context providers, and materializes one immutable
ContextPackage whose exact final model input is embedded in the Writer
invocation.

Legacy LLMContextPackage remains a technical compatibility/recovery surface and
must never be used by this composer as semantic authority.
"""

from __future__ import annotations

import json
import re
from core.diagnostics import current_diagnostics
from typing import Any, Iterable
from apps.enterprise_document.schemas.agent_rag_v1.artifacts import ArtifactVersionRefSchema

from apps.enterprise_document.schemas.agent_rag_v1.facts import (
    FactSnapshotBindingSchema,
    FactSnapshotContentSchema,
)
from apps.enterprise_document.schemas.agent_rag_v1.lifecycle import (
    ActiveExecutionLineageSchema,
)
from apps.enterprise_document.schemas.agent_rag_v1.memory import DomainMemoryType
from apps.enterprise_document.schemas.agent_rag_v1.research import (
    EvidencePackageBindingSchema,
)
from apps.enterprise_document.schemas.pre_write_admission import (
    WriterAdmissionDecisionSchema,
)
from apps.enterprise_document.schemas.pre_write_binding import (
    WriterGateBindingSchema,
)
from apps.enterprise_document.schemas.pre_write_control import (
    PreWriteRouteDecisionSchema,
)
from apps.enterprise_document.schemas.project_input_schema import ProjectInputSchema
from apps.enterprise_document.schemas.scheme_writer.planning import (
    SectionPlanBindingSchema,
    SectionPlanContentSchema,
)
from apps.enterprise_document.services.agent_rag_v1.fact_snapshot_runtime import (
    FactSnapshotRuntime,
)
from apps.enterprise_document.services.agent_rag_v1.lifecycle_runtime import (
    RunAttemptLifecycleService,
)
from apps.enterprise_document.services.agent_rag_v1.plan_runtime import (
    SectionPlanRuntime,
)
from apps.enterprise_document.services.agent_rag_v1.research_runtime import (
    ResearchRuntime,
)
from apps.enterprise_document.services.project_input_normalization.fact_contract import (
    project_input_fact_normalization_version,
    project_input_fact_field,
)
from apps.enterprise_document.services.project_input_normalization.input_contract import (
    requested_target_section_chars,
)
from contracts.evidence import project_evidence_bundle
from core.hashing import canonical_sha256
from core.token_estimator import DeterministicTokenEstimator
from model_gateway.model_contract import ModelRole
from schemas.context import (
    ContextInstructionMaterialSchema,
    ContextInvocationSubjectSchema,
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
    ContextSourceStatusBindingSchema,
    InstructionBindingSchema,
    ModelContextCapabilityBindingSchema,
)

from .context_runtime import (
    ApplicationContextRuntime,
    ContextSourceProviderRegistration,
)
from .writer_evidence_projection import (
    PROJECTION_SCHEMA_ID, PROJECTION_SCHEMA_VERSION, PROTECTED_FIELDS,
    project_writer_evidence,
)


class TargetWriterContextIntegrityError(RuntimeError):
    """Canonical Writer Context cannot be resolved or revalidated."""


class TargetWriterContextComposer:
    """Compose one exact SIF-10 ContextPackage for an initial Writer call."""

    ROLE_POLICY_ID = "target-writer-normal-context"
    ROLE_POLICY_VERSION = "v2"
    CROSS_SECTION_POLICY_ID = "sif10-cross-section-policy-b"
    CROSS_SECTION_POLICY_VERSION = "v1"
    CONSUMER_AUTH_POLICY_ID = "sif10-consumer-authorization"
    CONSUMER_AUTH_POLICY_VERSION = "v1"
    SELECTION_POLICY_ID = "sif10-hierarchical-selection"
    SELECTION_POLICY_VERSION = "v2"
    BUDGET_POLICY_ID = "sif10-context-budget"
    BUDGET_POLICY_VERSION = "v2"
    REPRESENTATION_POLICY_ID = "sif10-deterministic-representation"
    REPRESENTATION_POLICY_VERSION = "v4"
    MODEL_CAPABILITY_SCHEMA_ID = "model_gateway_routing_capability_v1"
    MODEL_CAPABILITY_SCHEMA_VERSION = "v2"
    TOKEN_ESTIMATOR_ID = "deterministic_mixed_text"
    TOKEN_ESTIMATOR_VERSION = "v1"

    def __init__(
        self,
        *,
        fact_snapshot_runtime: FactSnapshotRuntime,
        section_plan_runtime: SectionPlanRuntime,
        research_runtime: ResearchRuntime,
        lifecycle_service: RunAttemptLifecycleService,
        model_gateway: Any,
    ) -> None:
        self.fact_snapshot_runtime = fact_snapshot_runtime
        self.section_plan_runtime = section_plan_runtime
        self.research_runtime = research_runtime
        self.lifecycle_service = lifecycle_service
        self.model_gateway = model_gateway
        self.cross_section_provider = None
        self.estimator = DeterministicTokenEstimator()
        self.memory_composition = None

    @staticmethod
    def _canonical_json(value: Any) -> str:
        return json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )

    @classmethod
    def _status(
        cls,
        *,
        family: ContextSourceFamily,
        state: str = "CURRENT",
    ) -> ContextSourceStatusBindingSchema:
        payload = {"state": state}
        return ContextSourceStatusBindingSchema(
            status_schema_id=f"{family.value.lower()}_context_currentness_v1",
            status_schema_version="v1",
            resolver_id=f"target-writer-context:{family.value.lower()}",
            resolver_policy_version="v1",
            status_payload=payload,
            status_payload_sha256=canonical_sha256(payload),
            status_semantic_fingerprint=canonical_sha256(payload),
        )

    @staticmethod
    def _writer_memory_type(artifact_type: str) -> DomainMemoryType:
        if artifact_type == "fact_snapshot":
            return DomainMemoryType.FACT
        if artifact_type == "evidence_package":
            return DomainMemoryType.EVIDENCE
        raise TargetWriterContextIntegrityError(
            "Writer Memory locator source is not an authorized Fact/Evidence artifact"
        )

    @classmethod
    def _representation(
        cls,
        level: ContextRepresentationLevel,
        *,
        schema_id: str,
        payload: Any,
        protected_fields: Iterable[str] = (),
        schema_version: str = "v1",
    ) -> ContextRepresentationOptionSchema:
        return ContextRepresentationOptionSchema(
            representation_level=level,
            projection_schema_id=schema_id,
            projection_schema_version=schema_version,
            representation_policy_id=cls.REPRESENTATION_POLICY_ID,
            representation_policy_version=cls.REPRESENTATION_POLICY_VERSION,
            materialized_payload=payload,
            materialized_payload_sha256=canonical_sha256(payload),
            protected_field_evidence=tuple(sorted(set(protected_fields))),
        )

    @classmethod
    def _source_binding(
        cls,
        *,
        family: ContextSourceFamily,
        exact_source_ref: dict[str, Any],
        source_schema_id: str,
        source_schema_version: str,
        semantic_material: Any,
        run_id: str,
        scope_ref: str,
        authority_owner: str,
    ) -> ContextSourceBindingSchema:
        return ContextSourceBindingSchema(
            source_family=family,
            exact_source_ref=exact_source_ref,
            source_schema_id=source_schema_id,
            source_schema_version=source_schema_version,
            source_semantic_fingerprint=canonical_sha256(semantic_material),
            authority_owner=authority_owner,
            run_id=run_id,
            scope_ref=scope_ref,
            currentness_binding=cls._status(family=family),
        )

    def _resolve_binding_current(
        self,
        binding: ContextSourceBindingSchema,
    ) -> ContextSourceBindingSchema:
        family = binding.source_family
        exact = dict(binding.exact_source_ref)
        try:
            if family is ContextSourceFamily.SECTION_PLAN:
                plan_binding = SectionPlanBindingSchema.model_validate(exact["binding"])
                self.section_plan_runtime.validate_plan_binding_current(plan_binding)
            elif family is ContextSourceFamily.FACT_SNAPSHOT:
                fact_binding = FactSnapshotBindingSchema.model_validate(exact["binding"])
                self.fact_snapshot_runtime.validate_binding_current(fact_binding)
                self.fact_snapshot_runtime.read_binding(
                    fact_binding,
                    require_current=True,
                )
            elif family is ContextSourceFamily.EVIDENCE_PACKAGE:
                package_binding = EvidencePackageBindingSchema.model_validate(
                    exact["binding"]
                )
                self.research_runtime.validate_evidence_package_binding_current(
                    package_binding,
                    require_source_fresh=True,
                )
            elif family is ContextSourceFamily.DURABLE_RUN_MEMORY:
                if self.memory_composition is None or not self.memory_composition.required:
                    raise TargetWriterContextIntegrityError("Memory capability is not active")
                from apps.enterprise_document.schemas.agent_rag_v1.memory import DurableMemoryRefSchema
                ref = DurableMemoryRefSchema.model_validate(exact["memory_ref"])
                source = ArtifactVersionRefSchema.model_validate(exact["source_ref"])
                if (source.logical_ref.run_id != binding.run_id
                        or (source.logical_ref.artifact_type == "evidence_package"
                            and source.logical_ref.domain_scope_ref != binding.scope_ref)):
                    raise TargetWriterContextIntegrityError("Memory canonical source Run/Section mismatch")
                memory_type = self._writer_memory_type(source.logical_ref.artifact_type)
                records = self.memory_composition.recall(
                    run_id=binding.run_id,
                    scope_ref=source.logical_ref.domain_scope_ref,
                    memory_types=(memory_type,),
                    exact_source_refs=(exact["source_ref"],),
                )
                record = next((item for item in records if item.memory_ref == ref), None)
                if (record is None or record.source_binding.primary_source.exact_source_ref != exact["source_ref"]
                        or source.logical_ref.artifact_type not in ("fact_snapshot", "evidence_package")):
                    raise TargetWriterContextIntegrityError("Memory locator is stale or unauthorized")
                # Revalidate the original source authorization carried by the
                # current Writer, including its exact Plan and Section.
                self._resolve_binding_current(ContextSourceBindingSchema.model_validate(exact["canonical_binding"]))
                canonical = ContextSourceBindingSchema.model_validate(exact["canonical_binding"])
                if canonical.run_id != binding.run_id or canonical.scope_ref != binding.scope_ref:
                    raise TargetWriterContextIntegrityError("Memory consumer scope mismatch")
                canonical_ref = canonical.exact_source_ref["binding"]["version_ref"]
                if canonical_ref != exact["source_ref"]:
                    raise TargetWriterContextIntegrityError("Memory is not the authorized canonical source")
            elif family is ContextSourceFamily.WORKFLOW_EXECUTION_METADATA:
                lineage = ActiveExecutionLineageSchema.model_validate(exact["lineage"])
                current = self.lifecycle_service.resolve_active_lineage(
                    lineage.run_id,
                    lineage.attempt_scope_ref,
                )
                if current != lineage:
                    raise TargetWriterContextIntegrityError(
                        "Workflow execution metadata Attempt lineage is stale"
                    )
            else:
                raise TargetWriterContextIntegrityError(
                    f"unsupported Writer Context family: {family.value}"
                )
        except TargetWriterContextIntegrityError:
            raise
        except Exception as exc:
            raise TargetWriterContextIntegrityError(
                f"Writer Context source is stale or unresolved: {family.value}"
            ) from exc
        return binding.model_copy(
            update={"currentness_binding": self._status(family=family)}
        )

    @staticmethod
    def _hard_eligible(
        binding: ContextSourceBindingSchema,
        subject: ContextInvocationSubjectSchema,
    ) -> tuple[bool, tuple[str, ...]]:
        if binding.run_id != subject.run_id:
            return False, ("CROSS_RUN_SOURCE_INJECTION",)
        if binding.source_family is ContextSourceFamily.DURABLE_RUN_MEMORY and binding.scope_ref != subject.consumer_scope_ref:
            return False, ("CROSS_SCOPE_MEMORY_INJECTION",)
        if binding.currentness_binding.status_payload.get("state") != "CURRENT":
            return False, ("SOURCE_NOT_CURRENT",)
        return True, ()

    def _runtime(
        self,
        candidates: dict[
            ContextSourceFamily,
            tuple[ContextSourceCandidateSchema, ...],
        ] | None = None,
    ) -> ApplicationContextRuntime:
        candidate_map = candidates or {}
        runtime = ApplicationContextRuntime(estimator=self.estimator)
        for family in (
            ContextSourceFamily.FACT_SNAPSHOT,
            ContextSourceFamily.SECTION_PLAN,
            ContextSourceFamily.EVIDENCE_PACKAGE,
            ContextSourceFamily.WORKFLOW_EXECUTION_METADATA,
            ContextSourceFamily.DURABLE_RUN_MEMORY,
        ):
            runtime.register_source_provider(
                ContextSourceProviderRegistration(
                    source_family=family,
                    provider_id=f"target-writer-provider:{family.value.lower()}",
                    provider_version="v1",
                    candidate_reader=lambda _subject, _family=family: candidate_map.get(
                        _family,
                        (),
                    ),
                    source_binding_resolver=self._resolve_binding_current,
                    hard_eligibility_validator=self._hard_eligible,
                )
            )
        if self.cross_section_provider is not None:
            provider = self.cross_section_provider
            family = ContextSourceFamily.ACTIVE_VERIFIED_CLAIM
            runtime.register_source_provider(ContextSourceProviderRegistration(
                source_family=family, provider_id="writer-authorized-canonical-claim", provider_version="v1",
                candidate_reader=lambda _subject: candidate_map.get(family, ()),
                source_binding_resolver=provider.resolve_binding_current,
                hard_eligibility_validator=provider.hard_eligible_after_resolution,
                consumer_authorization_resolver=provider.authorize,
            ))
        return runtime

    @classmethod
    def role_policy(cls, *, require_cross_section=False) -> ContextRolePolicySchema:
        exact = (ContextRepresentationLevel.EXACT,)
        return ContextRolePolicySchema(
            role_policy_id=cls.ROLE_POLICY_ID,
            role_policy_version=cls.ROLE_POLICY_VERSION,
            agent_role="WRITER",
            execution_mode="NORMAL",
            context_purpose="SECTION_GENERATION",
            source_rules=(
                ContextRoleSourceRuleSchema(
                    source_family=ContextSourceFamily.ACTIVE_VERIFIED_CLAIM,
                    visibility=ContextRoleVisibility.CONDITIONAL,
                    mandatory=require_cross_section,
                    priority_tier=ContextPriorityTier.P1_OPERATION_CRITICAL,
                    allowed_representation_levels=exact,
                    minimum_representation_floor=ContextRepresentationLevel.EXACT,
                    conditional_authorization_ref=cls.CONSUMER_AUTH_POLICY_ID,
                ),
                ContextRoleSourceRuleSchema(
                    source_family=ContextSourceFamily.SECTION_PLAN,
                    visibility=ContextRoleVisibility.MUST,
                    mandatory=True,
                    priority_tier=ContextPriorityTier.P0_MANDATORY_AUTHORITY,
                    allowed_representation_levels=exact,
                    minimum_representation_floor=ContextRepresentationLevel.EXACT,
                ),
                ContextRoleSourceRuleSchema(
                    source_family=ContextSourceFamily.FACT_SNAPSHOT,
                    visibility=ContextRoleVisibility.MUST,
                    mandatory=True,
                    priority_tier=ContextPriorityTier.P0_MANDATORY_AUTHORITY,
                    allowed_representation_levels=exact,
                    minimum_representation_floor=ContextRepresentationLevel.EXACT,
                ),
                ContextRoleSourceRuleSchema(
                    source_family=ContextSourceFamily.EVIDENCE_PACKAGE,
                    visibility=ContextRoleVisibility.MUST,
                    mandatory=True,
                    priority_tier=ContextPriorityTier.P0_MANDATORY_AUTHORITY,
                    allowed_representation_levels=(
                        ContextRepresentationLevel.EXACT,
                        ContextRepresentationLevel.STRUCTURED_COMPACT,
                    ),
                    minimum_representation_floor=(
                        ContextRepresentationLevel.STRUCTURED_COMPACT
                    ),
                ),
                ContextRoleSourceRuleSchema(
                    source_family=ContextSourceFamily.WORKFLOW_EXECUTION_METADATA,
                    visibility=ContextRoleVisibility.MUST,
                    mandatory=True,
                    priority_tier=ContextPriorityTier.P1_OPERATION_CRITICAL,
                    allowed_representation_levels=exact,
                    minimum_representation_floor=ContextRepresentationLevel.EXACT,
                ),
            ),
        )

    @classmethod
    def policy_binding(cls) -> ContextPolicyBindingSchema:
        return ContextPolicyBindingSchema(
            source_catalog_id=ApplicationContextRuntime.SOURCE_CATALOG_ID,
            source_catalog_version=ApplicationContextRuntime.SOURCE_CATALOG_VERSION,
            role_policy_id=cls.ROLE_POLICY_ID,
            role_policy_version=cls.ROLE_POLICY_VERSION,
            cross_section_policy_id=cls.CROSS_SECTION_POLICY_ID,
            cross_section_policy_version=cls.CROSS_SECTION_POLICY_VERSION,
            consumer_authorization_policy_id=cls.CONSUMER_AUTH_POLICY_ID,
            consumer_authorization_policy_version=cls.CONSUMER_AUTH_POLICY_VERSION,
            selection_policy_id=cls.SELECTION_POLICY_ID,
            selection_policy_version=cls.SELECTION_POLICY_VERSION,
            budget_policy_id=cls.BUDGET_POLICY_ID,
            budget_policy_version=cls.BUDGET_POLICY_VERSION,
            representation_policy_id=cls.REPRESENTATION_POLICY_ID,
            representation_policy_version=cls.REPRESENTATION_POLICY_VERSION,
        )

    def _model_capability(
        self,
        *,
        project_input: ProjectInputSchema,
    ) -> ModelContextCapabilityBindingSchema:
        if self.model_gateway is None:
            raise TargetWriterContextIntegrityError(
                "Target Writer Context requires ModelGateway capability authority"
            )
        resolver = getattr(self.model_gateway, "routing_capabilities", None)
        if not callable(resolver):
            raise TargetWriterContextIntegrityError(
                "ModelGateway does not expose routing_capabilities"
            )
        capability = dict(
            resolver(model_role=ModelRole.SECTION_GENERATION.value)
        )
        configured_limit = project_input.generation_requirements.extra.get(
            "max_input_context_tokens"
        )
        safe_window = int(capability["safe_context_window"])
        model_window = (
            min(safe_window, int(configured_limit))
            if configured_limit is not None
            else safe_window
        )
        safe_output = min(
            int(capability["safe_max_output_tokens"]),
            int(project_input.generation_requirements.max_tokens_per_section),
        )
        capability_material = {
            "model_role": ModelRole.SECTION_GENERATION.value,
            "candidate_profiles": list(capability.get("candidate_profiles") or ()),
            "candidate_models": list(capability.get("candidate_models") or ()),
            "candidate_capabilities": list(capability.get("candidate_capabilities") or ()),
            "safe_context_window": safe_window,
            "safe_max_output_tokens": int(capability["safe_max_output_tokens"]),
            "effective_context_window": model_window,
            "effective_safe_output_tokens": safe_output,
        }
        return ModelContextCapabilityBindingSchema(
            model_capability_ref=(
                "model-gateway-capability://sha256/"
                + canonical_sha256(capability_material)
            ),
            capability_schema_id=self.MODEL_CAPABILITY_SCHEMA_ID,
            capability_schema_version=self.MODEL_CAPABILITY_SCHEMA_VERSION,
            capability_semantic_fingerprint=canonical_sha256(capability_material),
            model_context_window=model_window,
            safe_max_output_tokens=safe_output,
            token_estimator_id=self.TOKEN_ESTIMATOR_ID,
            token_estimator_version=self.TOKEN_ESTIMATOR_VERSION,
            token_estimator_semantic_fingerprint=canonical_sha256(
                {
                    "estimator": self.TOKEN_ESTIMATOR_ID,
                    "version": self.TOKEN_ESTIMATOR_VERSION,
                }
            ),
        )

    @staticmethod
    def _section_from_plan(
        plan: SectionPlanContentSchema,
        section_id: str,
    ):
        matches = tuple(item for item in plan.sections if item.section_id == section_id)
        if len(matches) != 1:
            raise TargetWriterContextIntegrityError(
                "Writer Context Section does not resolve exactly once in current Plan"
            )
        return matches[0]

    @staticmethod
    def _relevant_fact_payload(
        content: FactSnapshotContentSchema,
        section,
        *,
        run_id: str,
        project_input_version: str,
    ) -> dict[str, Any]:
        requirements = tuple(section.fact_requirements)
        matched = []
        unresolved = []
        expected_normalization_version = project_input_fact_normalization_version(
            project_input_version
        )
        if requirements and (
            content.normalization_contract_version
            != expected_normalization_version
        ):
            raise TargetWriterContextIntegrityError(
                "unsupported Fact normalization contract for Writer requirements"
            )
        by_id = {fact.fact_id: fact for fact in content.facts}
        for requirement in requirements:
            # Domain keys name requirements even when their value is absent. Never
            # replace a missing key with another fact merely sharing its type.
            try:
                field = project_input_fact_field(requirement.fact_key)
                fact_id = field.record_id(run_id)
            except ValueError as exc:
                raise TargetWriterContextIntegrityError(str(exc)) from exc
            if requirement.fact_type != field.fact_type:
                raise TargetWriterContextIntegrityError(
                    "Writer fact requirement type conflicts with its registered key"
                )
            fact = by_id.get(fact_id)
            if fact is None:
                unresolved.append(requirement)
                continue
            if (
                fact.fact_type != field.fact_type
                or not isinstance(fact.normalized_value, str)
                or not fact.normalized_value.strip()
                or any(
                    source.source_type != "project_input"
                    or not source.source_ref.startswith("project-input://task/")
                    for source in fact.source_bindings
                )
            ):
                raise TargetWriterContextIntegrityError(
                    "Writer fact record conflicts with the ProjectInput normalization binding"
                )
            if fact.normalized_value == field.unavailable_value:
                unresolved.append(requirement)
                continue
            matched.append(fact)
        unique = {item.fact_id: item for item in matched}
        return {
            "fact_requirements": [
                item.model_dump(mode="json") for item in requirements
            ],
            "facts": [
                unique[key].model_dump(mode="json")
                for key in sorted(unique)
            ],
            "unresolved_fact_requirements": [
                item.model_dump(mode="json") for item in unresolved
            ],
        }

    def _source_candidates(
        self,
        *,
        project_input_version: str,
        plan_binding: SectionPlanBindingSchema,
        evidence_package_binding: EvidencePackageBindingSchema,
        lineage: ActiveExecutionLineageSchema,
        route_decision: PreWriteRouteDecisionSchema,
        decision: WriterAdmissionDecisionSchema,
    ) -> tuple[
        dict[ContextSourceFamily, tuple[ContextSourceCandidateSchema, ...]],
        Any,
    ]:
        plan_binding = self.section_plan_runtime.validate_plan_binding_current(
            plan_binding
        )
        raw_plan, canonical_plan_ref = (
            self.section_plan_runtime.artifact_runtime.read_version(
                plan_binding.plan_version_ref
            )
        )
        if canonical_plan_ref != plan_binding.plan_version_ref:
            raise TargetWriterContextIntegrityError(
                "Plan resolver returned a different immutable version"
            )
        plan = SectionPlanContentSchema.model_validate(raw_plan)
        section = self._section_from_plan(plan, plan_binding.section_id)

        fact_head = self.section_plan_runtime.artifact_runtime.resolve_active(
            plan.fact_snapshot_version_ref.logical_ref
        )
        if (
            fact_head is None
            or fact_head.active_version_ref != plan.fact_snapshot_version_ref
        ):
            raise TargetWriterContextIntegrityError(
                "Plan-bound FactSnapshot is not canonical current head"
            )
        fact_binding = FactSnapshotBindingSchema(
            version_ref=plan.fact_snapshot_version_ref,
            resolved_head_revision=fact_head.head_revision,
        )
        fact_content = self.fact_snapshot_runtime.read_binding(
            fact_binding,
            require_current=True,
        )
        relevant_fact_payload = self._relevant_fact_payload(
            fact_content,
            section,
            run_id=plan_binding.plan_version_ref.logical_ref.run_id,
            project_input_version=project_input_version,
        )

        package_content = (
            self.research_runtime.validate_evidence_package_binding_current(
                evidence_package_binding,
                require_source_fresh=True,
            )
        )
        if (
            package_content.section_plan_source.plan_version_ref
            != plan_binding.plan_version_ref
            or package_content.section_plan_source.section_id
            != plan_binding.section_id
        ):
            raise TargetWriterContextIntegrityError(
                "EvidencePackage does not bind current Writer SectionPlan"
            )
        bundle = self.research_runtime.resolve_evidence_bundle(
            package_content.evidence_bundle_binding
        )
        requirement_snapshot, requirement_ids = (
            self.research_runtime.resolve_research_requirements(plan_binding)
        )
        rag_context, _, citations = project_evidence_bundle(bundle)

        scope_ref = f"section:{plan_binding.section_id}"
        run_id = plan_binding.plan_version_ref.logical_ref.run_id

        plan_binding_source = self._source_binding(
            family=ContextSourceFamily.SECTION_PLAN,
            exact_source_ref={"binding": plan_binding.model_dump(mode="json")},
            source_schema_id="section_plan_binding_v1",
            source_schema_version="v1",
            semantic_material=plan_binding.plan_version_ref.model_dump(mode="json"),
            run_id=run_id,
            scope_ref=scope_ref,
            authority_owner="SIF-05:SectionPlanRuntime",
        )
        fact_binding_source = self._source_binding(
            family=ContextSourceFamily.FACT_SNAPSHOT,
            exact_source_ref={"binding": fact_binding.model_dump(mode="json")},
            source_schema_id="fact_snapshot_binding_v1",
            source_schema_version="v1",
            semantic_material=fact_binding.version_ref.model_dump(mode="json"),
            run_id=run_id,
            scope_ref=scope_ref,
            authority_owner="SIF-04:FactSnapshotRuntime",
        )
        package_binding_source = self._source_binding(
            family=ContextSourceFamily.EVIDENCE_PACKAGE,
            exact_source_ref={
                "binding": evidence_package_binding.model_dump(mode="json")
            },
            source_schema_id="evidence_package_binding_v1",
            source_schema_version="v1",
            semantic_material=evidence_package_binding.version_ref.model_dump(
                mode="json"
            ),
            run_id=run_id,
            scope_ref=scope_ref,
            authority_owner="SIF-05/SIF-08:ResearchRuntime",
        )
        workflow_source = self._source_binding(
            family=ContextSourceFamily.WORKFLOW_EXECUTION_METADATA,
            exact_source_ref={"lineage": lineage.model_dump(mode="json")},
            source_schema_id="active_execution_lineage_v1",
            source_schema_version="v1",
            semantic_material=lineage.model_dump(mode="json"),
            run_id=run_id,
            scope_ref=scope_ref,
            authority_owner="SIF-01:RunAttemptLifecycleService",
        )

        candidates = {
            ContextSourceFamily.SECTION_PLAN: (
                ContextSourceCandidateSchema(
                    candidate_id="writer-current-section-plan",
                    source_binding=plan_binding_source,
                    title="Current SectionPlan",
                    representations=(
                        self._representation(
                            ContextRepresentationLevel.EXACT,
                            schema_id="writer_current_section_plan_v1",
                            payload={
                                "binding": plan_binding.model_dump(mode="json"),
                                "section": section.model_dump(mode="json"),
                            },
                            protected_fields=("section_id", "strategy"),
                        ),
                    ),
                ),
            ),
            ContextSourceFamily.FACT_SNAPSHOT: (
                ContextSourceCandidateSchema(
                    candidate_id="writer-current-fact-snapshot",
                    source_binding=fact_binding_source,
                    title="Current FactSnapshot relevant subset",
                    representations=(
                        self._representation(
                            ContextRepresentationLevel.EXACT,
                            schema_id="writer_relevant_fact_subset_v1",
                            payload={
                                "binding": fact_binding.model_dump(mode="json"),
                                **relevant_fact_payload,
                            },
                            protected_fields=("facts",),
                        ),
                    ),
                ),
            ),
            ContextSourceFamily.EVIDENCE_PACKAGE: (
                ContextSourceCandidateSchema(
                    candidate_id="writer-current-evidence-package",
                    source_binding=package_binding_source,
                    title="Current EvidencePackage",
                    representations=(
                        self._representation(
                            ContextRepresentationLevel.STRUCTURED_COMPACT,
                            schema_id=PROJECTION_SCHEMA_ID,
                            schema_version=PROJECTION_SCHEMA_VERSION,
                            payload=project_writer_evidence(
                                binding=evidence_package_binding,
                                package=package_content,
                                bundle=bundle,
                                context_text=rag_context.context_text,
                                citations=citations,
                                requirement_snapshot=requirement_snapshot,
                                requirement_ids=requirement_ids,
                            ),
                            protected_fields=PROTECTED_FIELDS,
                        ),
                    ),
                ),
            ),
            ContextSourceFamily.WORKFLOW_EXECUTION_METADATA: (
                ContextSourceCandidateSchema(
                    candidate_id="writer-current-workflow-metadata",
                    source_binding=workflow_source,
                    title="Current Workflow execution metadata",
                    representations=(
                        self._representation(
                            ContextRepresentationLevel.EXACT,
                            schema_id="writer_workflow_metadata_v1",
                            payload={
                                "active_execution_lineage": lineage.model_dump(
                                    mode="json"
                                ),
                            },
                            protected_fields=("active_execution_lineage",),
                        ),
                    ),
                ),
            ),
        }
        if section.hard_dependencies:
            if self.cross_section_provider is None:
                raise TargetWriterContextIntegrityError("Writer dependency source provider is missing")
            candidates[ContextSourceFamily.ACTIVE_VERIFIED_CLAIM] = self.cross_section_provider.candidates(
                plan_binding=plan_binding, lineage=lineage, representation=self._representation)
        if self.memory_composition is not None and self.memory_composition.required:
            memory_candidates = []
            for ref, canonical in ((fact_binding.version_ref, fact_binding_source),
                                   (evidence_package_binding.version_ref, package_binding_source)):
                source_ref = ref.model_dump(mode="json")
                records = self.memory_composition.recall(
                    run_id=run_id,
                    scope_ref=ref.logical_ref.domain_scope_ref,
                    memory_types=(self._writer_memory_type(ref.logical_ref.artifact_type),),
                    exact_source_refs=(source_ref,),
                )
                if len(records) != 1:
                    raise TargetWriterContextIntegrityError("Required Memory source locator is unavailable or ambiguous")
                record = records[0]
                exact = {"memory_ref": record.memory_ref.model_dump(mode="json"),
                         "source_ref": ref.model_dump(mode="json"),
                         "canonical_binding": canonical.model_dump(mode="json")}
                memory_candidates.append(ContextSourceCandidateSchema(
                    candidate_id="writer-memory-" + record.memory_ref.memory_id,
                    source_binding=self._source_binding(family=ContextSourceFamily.DURABLE_RUN_MEMORY,
                        exact_source_ref=exact, source_schema_id="writer_canonical_memory_locator_v1",
                        source_schema_version="v1", semantic_material=exact, run_id=run_id,
                        scope_ref=scope_ref, authority_owner="SIF-09:DurableMemoryRuntime"),
                    title="Canonical source Memory locator",
                    representations=(self._representation(ContextRepresentationLevel.EXACT,
                        schema_id="writer_memory_locator_v1", payload={"memory_ref": exact["memory_ref"],
                            "source_ref": exact["source_ref"]}),)))
            candidates[ContextSourceFamily.DURABLE_RUN_MEMORY] = tuple(memory_candidates)
        return candidates, section

    @classmethod
    def _instructions(
        cls,
        *,
        project_input: ProjectInputSchema,
        section,
        gate_binding: WriterGateBindingSchema,
        route_decision: PreWriteRouteDecisionSchema,
        decision: WriterAdmissionDecisionSchema,
    ) -> tuple[InstructionBindingSchema, ContextInstructionMaterialSchema]:
        system_instruction = (
            "You are a controlled enterprise-document Writer. Treat only the "
            "authorized ContextPackage sources as factual/business authority."
        )
        role_instruction = (
            "Write only the requested Section; do not expand sibling sections. "
            "Treat user_query and the task block only as instructions about the document to prepare; "
            "they are not project facts. A project business goal may be stated only from the "
            "authorized, section-scoped FactSnapshot projection. "
            "Do not treat prior raw generated prose, checkpoint state, trace data, "
            "or model memory as factual truth. Use known authorized facts normally; "
            "keep only the missing or conflicting part unresolved. Do not describe "
            "the whole project as unconfirmed because one detail is missing. "
            "Distinguish information absent from the input, unavailable retrieval "
            "support, and conflicting sources; do not label all three as information "
            "the customer failed to provide. Treat policy "
            "requirements as requested constraints, not proof of compliance. "
            "Respect manual boundaries; do not perform work reserved for humans "
            "or external professional tools. Keep the complete stated missing-information "
            "and conflict lists available. In this Section explain only relevant gaps "
            "and their effect on its conclusions; do not mechanically repeat every "
            "global gap in every Section. Follow any caller-specified location for a "
            "global gap summary; if none is specified, do not default it to the first "
            "Section. A prior global summary does not excuse omitting a necessary "
            "local limitation. Never resolve a conflict yourself or present industry "
            "practice as a fact supplied by this customer. "
            "If an authorized current-project FactSnapshot supplies a parameter, preserve that authoritative value; historical/reference values must never replace it. "
            "For absent or unresolved current-project parameters, selected useful historical/reference Evidence may support a clearly qualified proposal, estimate, planning assumption or suggested design. Preserve its provisional/reference wording; never describe it as confirmed, customer-approved or completed. "
            "If neither an authorized current Fact nor useful selected reference Evidence supplies a basis, keep the parameter unresolved/to be confirmed. "
            "Use Section body citation markers only from the current EvidencePackage "
            "citation_usage.allowed_aliases. Internal Fact, Claim, Run, or Artifact IDs "
            "are not Evidence citation aliases and must not be written as body citations. "
            "Express authorized project Facts naturally from the section-scoped FactSnapshot "
            "without displaying internal IDs; do not use an unrelated Evidence citation "
            "in place of a project Fact's source. An allowed alias alone does not show "
            "that its source supports the cited statement. When no alias is allowed, "
            "do not invent one; continue to respect the task's citation requirements."
        )
        writing = project_input.generation_requirements
        # Opt in with the new V3 business citation style. Keep the existing v5
        # instruction byte-for-byte for historical/default task inputs.
        exact_bracket_citations = (
            writing.citation_style == "citation_id_ascii_brackets_v2"
        )
        role_instruction_version = "v6" if exact_bracket_citations else "v5"
        if exact_bracket_citations:
            role_instruction += (
                " For every source-supported citation in the Section body, write "
                "the allowed alias as an exact ASCII square-bracket marker [C1] "
                "or [C2] immediately after its supported statement. "
                "For multiple genuinely supporting sources write [C1][C2]; "
                "do not use Chinese parentheses such as （C1、C2）, bare C1/C2, "
                "or substitute an internal Fact/Claim ID. "
                "When citation_required is true, include at least one canonical "
                "citation marker only when a current allowed Evidence source "
                "actually supports the corresponding statement. "
                "If no selected source supports a statement, do not fabricate a "
                "citation or factual claim; preserve the unresolved limitation. "
                "An allowed alias does not itself establish support."
            )
            if section.citation_required:
                role_instruction_version = "v7"
                role_instruction += (
                    " Before drafting a citation-required Section, inspect the selected "
                    "Evidence text, its relevance to this Section and its permitted uses. "
                    "When it supports a relevant statement, include that statement and "
                    "its allowed citation rather than leaving the citation obligation unused. "
                    "If the useful source is a historical/reference project, write a concise, "
                    "explicitly qualified historical case or reference method within this "
                    "Section's scope. Preserve whether the source describes a plan, an "
                    "expected benefit or an achieved result; do not upgrade one into another "
                    "or generalize one case into a universal rule. Put the exact allowed "
                    "[C#] marker immediately after that reference statement, separately "
                    "from current-project facts. Historical/reference Evidence cannot "
                    "establish the current project's quantity, organization, existing "
                    "conditions, procurement, installation, deployment or operating status; "
                    "these remain bound to authorized current-project Facts. "
                    "Do not force an irrelevant reference into the Section. If no selected "
                    "allowed source supports a relevant statement within its permitted "
                    "uses, leave the citation obligation unresolved and state the support "
                    "limitation; never invent a claim, source or marker to satisfy a count."
                )
        requested_target_chars = requested_target_section_chars(project_input)
        prompt_template = (
            "Generate the requested section using the task and writing requirements, "
            "current SectionPlan, relevant FactSnapshot projection, and current "
            "EvidencePackage, including its citation_usage and source constraints."
        )
        if requested_target_chars is not None:
            prompt_template += (
                " Treat requested target_section_chars as an advisory length goal "
                "for this initial Section, never as a hard limit or a source of facts."
            )
        # Project only authored task/writing semantics. Structured project facts
        # must still arrive through the scoped canonical FactSnapshot provider;
        # metadata, external-material locators and Gate decisions are not a shortcut.
        task_output_payload = {
                "task": {
                    "task_type": project_input.task_type,
                    "project_name": project_input.project_name,
                    "user_query": project_input.user_query,
                    "policy_requirements": list(project_input.policy_requirements),
                    "manual_boundaries": [
                        item.model_dump(mode="json")
                        for item in project_input.manual_boundaries
                    ],
                    "missing_information": list(project_input.missing_information),
                    "conflicting_information": list(
                        project_input.conflicting_information
                    ),
                },
                "writing_requirements": {
                    "language": writing.language,
                    "tone": writing.tone,
                    "citation_style": writing.citation_style,
                    "min_section_chars": writing.min_section_chars,
                },
                "document_title": project_input.output_schema.document_title,
                "section_id": section.section_id,
                "section_title": section.section_title,
                "section_order": section.section_order,
                "sibling_section_titles": [
                    title for title in writing.required_sections
                    if title != section.section_title
                ],
                "citation_required": section.citation_required,
                "citation_policy": {
                    "allowed_alias_source": "current_evidence_package.citation_usage.allowed_aliases",
                    "internal_ids_as_body_citations": False,
                },
                "output": "section_body_only",
        }
        if requested_target_chars is not None:
            task_output_payload["target_section_chars"] = requested_target_chars
        # Reuse the authored ProjectInput writing requirements; only the active
        # Section's method reaches this immutable Task Contract, never FACT/Evidence.
        section_methods = writing.extra.get("section_writing_methods", {})
        if not isinstance(section_methods, dict):
            raise TargetWriterContextIntegrityError("section_writing_methods must be a mapping")
        if section.section_title in section_methods:
            method = section_methods[section.section_title]
            if not isinstance(method, str) or not method.strip() or len(method) > 3000:
                raise TargetWriterContextIntegrityError("section writing method must be concise text")
            task_output_payload["section_writing_method"] = method.strip()
            prompt_template += (
                " Apply section_writing_method to this Section only as writing guidance, "
                "never as factual authority or permission to override Evidence/Fact constraints."
            )
        task_output_contract = cls._canonical_json(task_output_payload)
        prompt_template_sha256 = canonical_sha256(prompt_template)
        # The signature remains compatible with the invocation builder. Its exact
        # Gate/Route objects stay in runtime integrity/provenance, never in model
        # instructions (Pre-write 46 sections 8.2 and 10.2).
        binding = InstructionBindingSchema(
            system_instruction_ref="instruction://target-writer/system/v1",
            system_instruction_version="v1",
            system_instruction_sha256=canonical_sha256(system_instruction),
            role_instruction_ref=(
                "instruction://target-writer/role/" + role_instruction_version
            ),
            role_instruction_version=role_instruction_version,
            role_instruction_sha256=canonical_sha256(role_instruction),
            prompt_template_ref=(
                "template://target-writer/section/v5/" + prompt_template_sha256
            ),
            prompt_template_version="v5",
            prompt_template_sha256=prompt_template_sha256,
            task_output_contract_ref=(
                "output-contract://target-writer/section/v5/"
                + canonical_sha256(task_output_contract)
            ),
            task_output_contract_sha256=canonical_sha256(task_output_contract),
            dynamic_control_bindings=(),
        )
        material = ContextInstructionMaterialSchema(
            system_instruction=system_instruction,
            role_instruction=role_instruction,
            prompt_template=prompt_template,
            task_output_contract=task_output_contract,
            dynamic_control_payloads=(),
        )
        return binding, material

    def build_package(
        self,
        *,
        project_input: ProjectInputSchema,
        plan_binding: SectionPlanBindingSchema,
        evidence_package_binding: EvidencePackageBindingSchema,
        gate_binding: WriterGateBindingSchema,
        route_decision: PreWriteRouteDecisionSchema,
        decision: WriterAdmissionDecisionSchema,
        write_instance_id: str,
    ) -> ContextPackageSchema:
        plan_binding = SectionPlanBindingSchema.model_validate(plan_binding)
        evidence_package_binding = EvidencePackageBindingSchema.model_validate(
            evidence_package_binding
        )
        gate_binding = WriterGateBindingSchema.model_validate(gate_binding)
        route_decision = PreWriteRouteDecisionSchema.model_validate(route_decision)
        decision = WriterAdmissionDecisionSchema.model_validate(decision)

        run_id = plan_binding.plan_version_ref.logical_ref.run_id
        section_id = plan_binding.section_id
        scope_ref = f"section:{section_id}"
        lineage = self.lifecycle_service.resolve_active_lineage(run_id, scope_ref)
        if lineage.attempt_id != str(write_instance_id or "").strip():
            raise TargetWriterContextIntegrityError(
                "Writer Context write_instance_id is not active canonical Attempt"
            )

        candidates, section = self._source_candidates(
            project_input_version=project_input.schema_version,
            plan_binding=plan_binding,
            evidence_package_binding=evidence_package_binding,
            lineage=lineage,
            route_decision=route_decision,
            decision=decision,
        )
        if (
            gate_binding.writer_subject_ref.run_id != run_id
            or gate_binding.writer_subject_ref.section_id != section_id
            or route_decision.writer_subject_ref != gate_binding.writer_subject_ref
        ):
            raise TargetWriterContextIntegrityError(
                "Writer Gate/Route subject does not match canonical Context scope"
            )

        model_call_id = f"model_call_{run_id}_{section_id}"
        subject = ContextInvocationSubjectSchema(
            run_id=run_id,
            attempt_id=lineage.attempt_id,
            invocation_id=model_call_id,
            logical_operation_id=(
                "writer-model-call:"
                + canonical_sha256(
                    {
                        "run_id": run_id,
                        "section_id": section_id,
                        "attempt_id": lineage.attempt_id,
                        "route_decision_id": route_decision.route_decision_id,
                    }
                )
            ),
            agent_role="WRITER",
            execution_mode="NORMAL",
            context_purpose="SECTION_GENERATION",
            consumer_scope_ref=scope_ref,
        )
        instruction_binding, instruction_material = self._instructions(
            project_input=project_input,
            section=section,
            gate_binding=gate_binding,
            route_decision=route_decision,
            decision=decision,
        )
        capability = self._model_capability(project_input=project_input)
        instruction_tokens = max(
            1,
            self.estimator.estimate(
                self._canonical_json(
                    instruction_material.model_dump(mode="json")
                )
            ),
        )
        runtime = self._runtime(candidates)
        role_policy = self.role_policy(
            require_cross_section=bool(candidates.get(ContextSourceFamily.ACTIVE_VERIFIED_CLAIM)))
        policy_binding = self.policy_binding()
        if self.memory_composition is not None and self.memory_composition.required:
            role_policy = role_policy.model_copy(update={"role_policy_version": "v2-memory-locators",
                "source_rules": role_policy.source_rules + (
                ContextRoleSourceRuleSchema(source_family=ContextSourceFamily.DURABLE_RUN_MEMORY,
                    visibility=ContextRoleVisibility.MAY, mandatory=False,
                    priority_tier=ContextPriorityTier.P3_SUPPORTING,
                    allowed_representation_levels=(ContextRepresentationLevel.EXACT,),
                    minimum_representation_floor=ContextRepresentationLevel.EXACT),)})
            policy_binding = policy_binding.model_copy(update={"role_policy_version": "v2-memory-locators"})
        return runtime.materialize_context_package(
            invocation_subject=subject,
            role_policy=role_policy,
            policy_binding=policy_binding,
            model_capability_binding=capability,
            instruction_binding=instruction_binding,
            instruction_material=instruction_material,
            reserved_instruction_tokens=instruction_tokens,
            reserved_output_tokens=capability.safe_max_output_tokens,
            safety_margin_tokens=64,
        )

    def _active_versions(
        self,
        package: ContextPackageSchema,
        *,
        project_input: ProjectInputSchema,
    ) -> dict[str, str]:
        capability = self._model_capability(project_input=project_input)
        return {
            "source_catalog_version": ApplicationContextRuntime.SOURCE_CATALOG_VERSION,
            "role_policy_version": ("v2-memory-locators" if self.memory_composition is not None
                and self.memory_composition.required else self.ROLE_POLICY_VERSION),
            "cross_section_policy_version": self.CROSS_SECTION_POLICY_VERSION,
            "consumer_authorization_policy_version": (
                self.CONSUMER_AUTH_POLICY_VERSION
            ),
            "selection_policy_version": self.SELECTION_POLICY_VERSION,
            "budget_policy_version": self.BUDGET_POLICY_VERSION,
            "representation_policy_version": self.REPRESENTATION_POLICY_VERSION,
            "model_capability_fingerprint": (
                capability.capability_semantic_fingerprint
            ),
            "token_estimator_fingerprint": (
                capability.token_estimator_semantic_fingerprint
            ),
        }

    def validate_pre_model_call(
        self,
        package: ContextPackageSchema,
        *,
        project_input: ProjectInputSchema,
    ) -> ContextPackageSchema:
        package = ContextPackageSchema.model_validate(package)
        current = self._runtime().validate_pre_model_call_current(
            package,
            self._active_versions(package, project_input=project_input),
        )
        # The immutable package's generic currentness keys do not name the
        # Evidence compact shape. Apply the exact current representation guard
        # before returning a reusable package or exposing locked messages.
        self.locked_citation_aliases(current)
        return current

    def final_messages(
        self,
        package: ContextPackageSchema,
        *,
        project_input: ProjectInputSchema,
    ) -> list[dict[str, str]]:
        current = self.validate_pre_model_call(
            package,
            project_input=project_input,
        )
        material = current.model_input.final_input_material
        payload = material.inline_final_input_payload
        if (
            not isinstance(payload, dict)
            or payload.get("input_kind") != "MESSAGES"
        ):
            raise TargetWriterContextIntegrityError(
                "Target Writer Context final input is not inline messages"
            )
        messages = payload.get("messages")
        if not isinstance(messages, list) or not messages:
            raise TargetWriterContextIntegrityError(
                "Target Writer Context final messages are missing"
            )
        normalized: list[dict[str, str]] = []
        for item in messages:
            if not isinstance(item, dict):
                raise TargetWriterContextIntegrityError(
                    "Target Writer Context message is malformed"
                )
            role = str(item.get("role") or "").strip()
            content = str(item.get("content") or "")
            if not role:
                raise TargetWriterContextIntegrityError(
                    "Target Writer Context message role is missing"
                )
            normalized.append({"role": role, "content": content})
        observer = current_diagnostics()
        if observer.enabled:
            observer.emit({"event_type": "P-CONTEXT-LOCKED", "unit_id": "D04",
                "run_id": current.package_ref.run_id,
                "attempt_id": current.package_ref.attempt_id,
                "invocation_id": current.package_ref.invocation_id,
                "context_package_id": current.package_ref.context_package_id,
                "context_fingerprint": current.context_semantic_fingerprint,
                "final_input_hash": material.final_model_input_sha256,
                "messages": normalized,
                "representation_kind": "WRITER_LOCKED_INPUT",
                "currentness_checked": True})
        return normalized

    @staticmethod
    def locked_citation_aliases(package: ContextPackageSchema) -> tuple[str, ...]:
        """Read the usage view already bound into a validated Writer ContextPackage."""
        current = ContextPackageSchema.model_validate(package)
        evidence = [
            item for item in current.selected_materializations
            if item.candidate_id == "writer-current-evidence-package"
        ]
        if (
            len(evidence) != 1
            or evidence[0].projection_schema_id != PROJECTION_SCHEMA_ID
            or evidence[0].projection_schema_version != PROJECTION_SCHEMA_VERSION
            or "citation_usage" not in evidence[0].protected_field_evidence
        ):
            raise TargetWriterContextIntegrityError(
                "Writer citation usage is absent from the locked Evidence projection"
            )
        payload = evidence[0].materialized_payload
        if isinstance(payload, dict) and "evidence_bundle" in payload:
            payload = payload["evidence_bundle"]
        usage = payload.get("citation_usage") if isinstance(payload, dict) else None
        if not isinstance(usage, dict) or usage.get("scope") != "current_section":
            raise TargetWriterContextIntegrityError("Writer citation usage scope is invalid")
        aliases = usage.get("allowed_aliases")
        if (
            not isinstance(aliases, list)
            or not all(isinstance(alias, str) and re.fullmatch(r"C[1-9][0-9]*", alias) for alias in aliases)
            or aliases != sorted(set(aliases))
        ):
            raise TargetWriterContextIntegrityError("Writer citation usage aliases are invalid")
        # Generation/source proof belongs to the canonical projector. Here the
        # immutable usage and rows must remain one view; no raw-row fallback.
        citation_rows = payload.get("citations")
        sources = payload.get("sources")
        if (not isinstance(citation_rows, list)
                or not all(isinstance(row, dict) for row in citation_rows)
                or [row.get("citation_id") for row in citation_rows] != aliases
                or not isinstance(sources, list)):
            raise TargetWriterContextIntegrityError("Writer citation rows disagree with locked aliases")
        for row in sources:
            if (not isinstance(row, dict)
                    or not isinstance(row.get("evidence_id"), str)
                    or not re.fullmatch(r"E[1-9][0-9]*", row["evidence_id"])
                    or not isinstance(row.get("citation_ids"), list)
                    or any(alias not in aliases for alias in row["citation_ids"])):
                raise TargetWriterContextIntegrityError("Writer source aliases disagree with locked usage")
        return tuple(aliases)


__all__ = [
    "TargetWriterContextComposer",
    "TargetWriterContextIntegrityError",
]
