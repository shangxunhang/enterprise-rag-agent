"""Bounded Writer projection of explicitly authorized current canonical Claims."""
from __future__ import annotations

from apps.enterprise_document.schemas.agent_rag_v1.claims import ClaimRefSchema
from apps.enterprise_document.schemas.agent_rag_v1.section_dependency import SectionSourceBindingSchema
from apps.enterprise_document.schemas.scheme_writer.planning import SectionPlanBindingSchema
from core.hashing import canonical_sha256
from schemas.context import (
    AuthorizationEvidenceBindingSchema, ConsumerAuthorizationDecisionRefSchema,
    ConsumerAuthorizationDecisionSchema, ConsumerAuthorizationDecisionState, ConsumerInvocationRefSchema,
    ContextRepresentationLevel, ContextSourceBindingSchema, ContextSourceCandidateSchema,
    ContextSourceFamily, ContextSourceStatusBindingSchema, IntrinsicActiveVerifiedClaimAssessmentBindingSchema,
)
from .section_dependency_runtime import SectionDependencyIntegrityError


class CrossSectionClaimContextProvider:
    POLICY_ID = "sif10-consumer-authorization"
    POLICY_VERSION = "v1"

    def __init__(self, dependencies):
        self.dependencies = dependencies

    def _resolve(self, binding, subject=None):
        exact = binding.exact_source_ref
        if set(exact) != {"claim_ref", "accepted_source", "consumer_plan_binding", "consumer_attempt_id"}:
            raise SectionDependencyIntegrityError("cross-section binding is not an exact typed envelope")
        source = SectionSourceBindingSchema.model_validate(exact["accepted_source"])
        consumer = SectionPlanBindingSchema.model_validate(exact["consumer_plan_binding"])
        run_id = consumer.plan_version_ref.logical_ref.run_id
        if (binding.source_family is not ContextSourceFamily.ACTIVE_VERIFIED_CLAIM
                or binding.run_id != run_id or binding.scope_ref != f"section:{source.plan_binding.section_id}"):
            raise SectionDependencyIntegrityError("cross-section source envelope Run/scope mismatch")
        lineage = self.dependencies.lifecycle.resolve_active_lineage(run_id, f"section:{consumer.section_id}")
        # A completed producer may be read; consumer dispatch still needs an active Run/Attempt.
        from apps.enterprise_document.schemas.agent_rag_v1.lifecycle import AttemptLifecycleStatus, RunLifecycleStatus
        if (lineage.run_status is not RunLifecycleStatus.RUNNING
                or lineage.attempt_status is not AttemptLifecycleStatus.RUNNING):
            raise SectionDependencyIntegrityError("cross-section consumer is not dispatch-current")
        if lineage.attempt_id != exact["consumer_attempt_id"]:
            raise SectionDependencyIntegrityError("cross-section consumer Attempt is stale")
        if subject is not None and (subject.run_id != run_id or subject.attempt_id != lineage.attempt_id
                or subject.consumer_scope_ref != f"section:{consumer.section_id}" or subject.agent_role != "WRITER"
                or subject.execution_mode != "NORMAL" or subject.context_purpose != "SECTION_GENERATION"):
            raise SectionDependencyIntegrityError("cross-section consumer is unauthorized")
        assessment = self.dependencies.require_dependencies(consumer)
        matches = [edge for edge in assessment.edges if edge.source_binding == source]
        if len(matches) != 1:
            raise SectionDependencyIntegrityError("source is not the exact current authorized upstream dependency")
        claim, verification, view = self.dependencies.read_active_verified_source(
            ClaimRefSchema.model_validate(exact["claim_ref"]), source)
        semantic = self._material(claim, verification, source)
        if canonical_sha256(semantic) != binding.source_semantic_fingerprint:
            raise SectionDependencyIntegrityError("cross-section Claim material differs from canonical source")
        return source, consumer, claim, verification, view

    @staticmethod
    def _material(claim, verification, source):
        # No body reconstruction, Gate/control object, source runtime or unverified prose.
        def provenance(ref, id_field):
            return {"ref": getattr(ref, id_field), "exact_ref_sha256": canonical_sha256(ref.model_dump(mode="json"))}
        return {"verified_statement": claim.normalized_value, "claim_type": claim.claim_type,
                "origin_section_id": claim.section_id, "claim_ref": provenance(claim.claim_ref, "claim_id"),
                "support_ref": provenance(verification.claim_evidence_support_ref, "claim_evidence_support_id"),
                "verification_ref": provenance(verification.verification_result_ref, "verification_result_id"),
                "binding_facts_ref": provenance(verification.binding_facts_ref, "binding_facts_id"),
                "source_binding_ref": source.source_ref, "source_binding_sha256": source.source_sha256}

    @staticmethod
    def _status():
        value = {"state": "CURRENT"}
        return ContextSourceStatusBindingSchema(status_schema_id="sif11_cross_section_source_current_v1",
            status_schema_version="v1", resolver_id="sif11-current-canonical-claim", resolver_policy_version="v1",
            status_payload=value, status_payload_sha256=canonical_sha256(value),
            status_semantic_fingerprint=canonical_sha256(value))

    def candidates(self, *, plan_binding, lineage, representation):
        assessment = self.dependencies.require_dependencies(plan_binding)
        candidates, seen = [], set()
        for edge in assessment.edges:
            source = edge.source_binding
            for verification_ref in source.verification_refs:
                claim, result, _ = self.dependencies.read_active_verified_source(verification_ref.claim_ref, source)
                if claim.claim_ref in seen:
                    continue
                seen.add(claim.claim_ref)
                material = self._material(claim, result, source)
                binding = ContextSourceBindingSchema(source_family=ContextSourceFamily.ACTIVE_VERIFIED_CLAIM,
                    exact_source_ref={"claim_ref": claim.claim_ref.model_dump(mode="json"),
                        "accepted_source": source.model_dump(mode="json"),
                        "consumer_plan_binding": plan_binding.model_dump(mode="json"),
                        "consumer_attempt_id": lineage.attempt_id},
                    source_schema_id="sif11_authorized_claim_source_v1", source_schema_version="v1",
                    source_semantic_fingerprint=canonical_sha256(material), authority_owner="SIF-07",
                    run_id=lineage.run_id, scope_ref=f"section:{claim.section_id}", currentness_binding=self._status())
                candidates.append(ContextSourceCandidateSchema(candidate_id="writer-verified:" + claim.claim_ref.claim_id,
                    source_binding=binding, title="Authorized verified upstream statement",
                    representations=(representation(ContextRepresentationLevel.EXACT,
                        schema_id="writer_verified_statement_v1", payload=material,
                        protected_fields=tuple(material)),),
                    # Preserve the complete authorized set under budget pressure. The provider
                    # never chooses one supposed truth by a semantic-relevance score.
                    conflict_group_ref="authorized-upstream-claims:" + source.source_sha256))
        return tuple(candidates)

    def resolve_binding_current(self, binding):
        self._resolve(binding)
        return binding.model_copy(update={"currentness_binding": self._status()})

    def hard_eligible(self, binding, subject):
        self._resolve(binding, subject)
        return True, ()

    def hard_eligible_after_resolution(self, binding, subject):
        """Check consumer identity after the runtime just revalidated this binding.

        ApplicationContextRuntime always runs source_binding_resolver immediately
        before this registered hook. SIF-10 authorization below still performs its
        own full per-invocation currentness resolution, and pre-model validation
        independently revalidates the selected source again.
        """
        exact = binding.exact_source_ref
        if set(exact) != {"claim_ref", "accepted_source", "consumer_plan_binding", "consumer_attempt_id"}:
            raise SectionDependencyIntegrityError("cross-section binding is not an exact typed envelope")
        source = SectionSourceBindingSchema.model_validate(exact["accepted_source"])
        consumer = SectionPlanBindingSchema.model_validate(exact["consumer_plan_binding"])
        run_id = consumer.plan_version_ref.logical_ref.run_id
        if (binding.source_family is not ContextSourceFamily.ACTIVE_VERIFIED_CLAIM
                or binding.run_id != run_id or binding.scope_ref != f"section:{source.plan_binding.section_id}"):
            raise SectionDependencyIntegrityError("cross-section source envelope Run/scope mismatch")
        if (binding.currentness_binding.status_payload.get("state") != "CURRENT"
                or subject.run_id != run_id or subject.attempt_id != exact["consumer_attempt_id"]
                or subject.consumer_scope_ref != f"section:{consumer.section_id}"
                or subject.agent_role != "WRITER" or subject.execution_mode != "NORMAL"
                or subject.context_purpose != "SECTION_GENERATION"):
            raise SectionDependencyIntegrityError("cross-section consumer is unauthorized")
        return True, ()

    def authorize(self, candidate, subject, rule):
        source, consumer, claim, verification, view = self._resolve(candidate.source_binding, subject)
        # Generic SIF-10 binds the exact candidate source envelope, containing the
        # canonical Claim ref and its typed producer/consumer provenance.
        exact_ref = candidate.source_binding.exact_source_ref
        head = self.dependencies.claim_runtime.resolve_claim_head(claim.claim_ref)
        invocation = ConsumerInvocationRefSchema(run_id=subject.run_id, attempt_id=subject.attempt_id,
            invocation_id=subject.invocation_id)
        intrinsic_payload = dict(claim_ref=exact_ref,
            assessed_verification_revision_ref=head.active_verification_revision_ref.model_dump(mode="json"),
            claim_head_revision=head.head_revision, intrinsic_dimension_results=view.model_dump(mode="json"),
            intrinsic_result="ACTIVE_VERIFIED", intrinsic_source_set_sha256=verification.authority_universe_sha256,
            intrinsic_resolver_id="sif07-resolve-active-verified-claim", intrinsic_resolver_policy_version="v1")
        intrinsic = IntrinsicActiveVerifiedClaimAssessmentBindingSchema(**intrinsic_payload,
            assessment_semantic_fingerprint=canonical_sha256(intrinsic_payload))
        evidence_payload = dict(authorization_path="EXPLICIT_DEPENDENCY",
            owning_authority_ref=consumer.model_dump(mode="json"),
            owning_authority_schema_id="section_plan_binding_v1", owning_authority_schema_version="v1",
            owning_authority_semantic_fingerprint=canonical_sha256(consumer.model_dump(mode="json")),
            matched_dependency_ref={"upstream_section_id": source.plan_binding.section_id,
                "downstream_section_id": consumer.section_id, "source_ref": source.source_ref,
                "source_sha256": source.source_sha256}, matched_consistency_subject=None, matched_consistency_domain=None)
        evidence = AuthorizationEvidenceBindingSchema(**evidence_payload,
            evidence_binding_sha256=canonical_sha256(evidence_payload))
        identity = dict(contract="consumer_authorization_decision_v1",
            consumer_invocation_ref=invocation.model_dump(mode="json"), claim_ref=exact_ref,
            consumer_role=subject.agent_role, consumer_scope_ref=subject.consumer_scope_ref,
            context_purpose=subject.context_purpose,
            intrinsic_claim_assessment_fingerprint=intrinsic.assessment_semantic_fingerprint,
            authorization_evidence_sha256=evidence.evidence_binding_sha256,
            policy_id=self.POLICY_ID, policy_version=self.POLICY_VERSION)
        semantic = dict(**identity, role_authorized=True, scope_authorized=True, purpose_authorized=True,
            cross_section_authorized=True, decision="AUTHORIZED", reason_codes=())
        return ConsumerAuthorizationDecisionSchema(
            decision_ref=ConsumerAuthorizationDecisionRefSchema(consumer_invocation_ref=invocation,
                claim_ref=exact_ref, decision_id="cauth_" + canonical_sha256(identity)),
            consumer_invocation_ref=invocation, consumer_role=subject.agent_role,
            consumer_scope_ref=subject.consumer_scope_ref, context_purpose=subject.context_purpose,
            intrinsic_claim_assessment=intrinsic, authorization_evidence_binding=evidence,
            role_authorized=True, scope_authorized=True, purpose_authorized=True, cross_section_authorized=True,
            policy_id=self.POLICY_ID, policy_version=self.POLICY_VERSION,
            decision=ConsumerAuthorizationDecisionState.AUTHORIZED, reason_codes=(),
            decision_semantic_fingerprint=canonical_sha256(semantic))
