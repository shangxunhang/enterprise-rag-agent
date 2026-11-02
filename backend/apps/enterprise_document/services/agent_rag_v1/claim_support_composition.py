"""Production composition for the existing shared Claim/Evidence support owner.

Only RAG Evidence items enter the support target. Fact scope anchors are not
Evidence, and a support result does not create a Verification verdict or Gate PASS.
"""
from __future__ import annotations

from datetime import datetime, timezone
import json
from threading import RLock
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, model_validator

from apps.enterprise_document.schemas.agent_rag_v1.claim_support import (
    ClaimEvidenceSupportCandidateSchema, ClaimEvidenceSupportRecordSchema, ClaimSupportAuthorityKind,
    ClaimSupportSubjectRefSchema, EvidenceTargetSetRefSchema, SupportRelation,
)
from apps.enterprise_document.schemas.agent_rag_v1.verification import ClaimOperationContextSchema
from core.hashing import canonical_sha256
from core.persistence.interprocess_lock import InterProcessFileLock
from core.retry_policy import classify_failure
from core.runtime.execution_control import checkpoint_current_execution
from core.token_estimator import DeterministicTokenEstimator
from model_gateway.model_contract import ModelRole
from schemas.context import (
    ContextInstructionMaterialSchema, ContextInvocationSubjectSchema, ContextPackageSchema,
    ContextPolicyBindingSchema, ContextPriorityTier, ContextRepresentationLevel,
    ContextRepresentationOptionSchema, ContextRolePolicySchema, ContextRoleSourceRuleSchema,
    ContextRoleVisibility, ContextSourceBindingSchema, ContextSourceCandidateSchema,
    ContextSourceFamily, ContextSourceStatusBindingSchema, InstructionBindingSchema,
    ModelContextCapabilityBindingSchema,
)
from schemas.failure import FailureCategory, FailureOrigin
from schemas.model import ModelRequestSchema
from .budget_manager import CanonicalBudgetCapacityExceeded, CanonicalBudgetDeadlineExceeded, CanonicalBudgetDraining
from .claim_support_runtime import ClaimSupportRuntime, ClaimSupportTechnicalFailure
from .context_runtime import ApplicationContextRuntime, ContextSourceProviderRegistration

LEGACY_SUPPORT_RESPONSE_CONTRACT = "canonical_claim_evidence_support_v1"
SUPPORT_RESPONSE_CONTRACT = "canonical_claim_evidence_support_v2"
SUPPORT_SEMANTIC_CONTRACT = "claim-evidence-support-v1"
TARGET_SET_CONTRACT = "claim-authorized-evidence-v1"


class TargetClaimSupportError(ClaimSupportTechnicalFailure):
    def __init__(self, message: str, *, error_code="CLAIM_SUPPORT_FAILED",
                 failure_category=FailureCategory.VALIDATION, failure_origin=FailureOrigin.UNKNOWN):
        super().__init__(message)
        self.error_code = error_code
        self.failure_category = FailureCategory(failure_category)
        self.failure_origin = FailureOrigin(failure_origin)


class SupportCertaintyObservationSchema(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    schema_version: Literal["faithfulness_certainty_physical_observation_v1"] = (
        "faithfulness_certainty_physical_observation_v1"
    )
    certainty_relation: Literal["ALIGNED", "ESCALATED", "INDETERMINATE"]
    reason_codes: tuple[str, ...] = ()
    physical_support_receipt_ref: str
    input_fingerprint: str

    @model_validator(mode="after")
    def _canonical(self):
        if self.reason_codes != tuple(sorted(set(self.reason_codes))):
            raise ValueError("certainty receipt reason_codes must be canonical ordered-unique")
        if not self.physical_support_receipt_ref.strip() or not self.input_fingerprint.strip():
            raise ValueError("certainty receipt requires exact physical receipt and fingerprint")
        return self


class SupportModelReply(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    relation: SupportRelation
    supporting_refs: tuple[str, ...]
    contradicting_refs: tuple[str, ...]
    certainty_relation: Literal["ALIGNED", "ESCALATED", "INDETERMINATE"] | None = None
    certainty_reason_codes: tuple[str, ...] = ()

    @model_validator(mode="after")
    def _coherent_relation(self):
        if self.relation in {SupportRelation.SUPPORTED, SupportRelation.PARTIALLY_SUPPORTED}:
            if not self.supporting_refs or self.contradicting_refs:
                raise ValueError("positive support requires supporting refs and no contradictory evidence")
        if self.relation is SupportRelation.NOT_SUPPORTED and (self.supporting_refs or self.contradicting_refs):
            raise ValueError("NOT_SUPPORTED cannot assert supporting or contradicting evidence")
        if self.certainty_reason_codes != tuple(sorted(set(self.certainty_reason_codes))):
            raise ValueError("certainty_reason_codes must be canonical ordered-unique")
        if self.certainty_relation is None and self.certainty_reason_codes:
            raise ValueError("certainty reason codes require a certainty relation")
        return self


def _strict_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate support JSON key")
        result[key] = value
    return result


class TargetClaimSupportComposition:
    POLICY_ID = "claim-evidence-semantic-support"
    POLICY_VERSION = "v1"

    def __init__(self, *, claim_runtime, source_composition, model_gateway):
        self.claim_runtime = claim_runtime
        self.sources = source_composition
        self.model_gateway = model_gateway
        self.store = claim_runtime.paragraph_runtime.artifact_runtime.artifact_store
        self.estimator = DeterministicTokenEstimator()
        self._lock = RLock()
        self.runtime = ClaimSupportRuntime(
            claim_runtime=claim_runtime, evidence_target_validator=self.validate_target,
            support_evaluator=self.evaluate,
            ledger_path=claim_runtime.ledger_path.with_name("claim_support.jsonl"),
            coordination_store=claim_runtime.coordination_store,
        )

    @staticmethod
    def _json(value):
        return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))

    def _scope(self, target, context):
        target = EvidenceTargetSetRefSchema.model_validate(target)
        context = ClaimOperationContextSchema.model_validate(context)
        self.claim_runtime.validate_operation_context(context)
        if target.claim_support_subject_ref.authority_kind is not ClaimSupportAuthorityKind.SIF07_CLAIM:
            raise TargetClaimSupportError("production support requires a canonical SIF-07 Claim")
        if target.target_set_contract_version != TARGET_SET_CONTRACT:
            raise TargetClaimSupportError("unsupported Claim Evidence target contract")
        claim = self.claim_runtime.resolve_claim_by_id(target.claim_support_subject_ref.subject_ref)
        rows = self.sources.materialize_sources(claim, context.active_lineage)
        candidate = self.sources._candidate_from_sources(claim, rows)
        ref = self.claim_runtime.binding_ref_for_candidate(candidate)
        binding = self.claim_runtime.resolve_binding_facts(ref)
        evidence = {row.source_ref.exact_source_ref: row for row in rows if row.source_ref.source_kind == "EVIDENCE"}
        if not set(target.evidence_member_refs) <= set(evidence):
            raise TargetClaimSupportError("support target escapes the Claim-authorized RAG Evidence set")
        expected = ClaimSupportRuntime.materialize_evidence_target_set(
            target.claim_support_subject_ref, target.evidence_member_refs, TARGET_SET_CONTRACT,
        )
        if target != expected:
            raise TargetClaimSupportError("support target identity is not deterministic")
        return claim, binding, tuple(evidence[ref] for ref in target.evidence_member_refs)

    def _read_scope(self, target, context):
        """Read the exact current Claim/Evidence domain without making it writable."""
        target = EvidenceTargetSetRefSchema.model_validate(target)
        context = ClaimOperationContextSchema.model_validate(context)
        if target.claim_support_subject_ref.authority_kind is not ClaimSupportAuthorityKind.SIF07_CLAIM:
            raise TargetClaimSupportError("production support requires a canonical SIF-07 Claim")
        if target.target_set_contract_version != TARGET_SET_CONTRACT:
            raise TargetClaimSupportError("unsupported Claim Evidence target contract")
        claim = self.claim_runtime.resolve_claim_by_id(
            target.claim_support_subject_ref.subject_ref
        )
        rows = self.sources.read_current_sources(claim, context.active_lineage)
        candidate = self.sources._candidate_from_sources(claim, rows)
        ref = self.claim_runtime.binding_ref_for_candidate(candidate)
        binding = self.claim_runtime.resolve_binding_facts(ref)
        evidence = {
            row.source_ref.exact_source_ref: row
            for row in rows
            if row.source_ref.source_kind == "EVIDENCE"
        }
        if not set(target.evidence_member_refs) <= set(evidence):
            raise TargetClaimSupportError(
                "support target escapes the Claim-authorized RAG Evidence set"
            )
        expected = ClaimSupportRuntime.materialize_evidence_target_set(
            target.claim_support_subject_ref,
            target.evidence_member_refs,
            TARGET_SET_CONTRACT,
        )
        if target != expected:
            raise TargetClaimSupportError("support target identity is not deterministic")
        return claim, binding, tuple(evidence[ref] for ref in target.evidence_member_refs)

    def validate_target(self, target, context):
        self._scope(target, context)

    @staticmethod
    def _reply_model(raw) -> SupportModelReply:
        if isinstance(raw, str):
            raw = json.loads(raw, object_pairs_hook=_strict_object)
        if isinstance(raw, dict):
            codes = raw.get("certainty_reason_codes")
            if (
                isinstance(codes, (list, tuple))
                and all(isinstance(code, str) for code in codes)
                and len(codes) == len(set(codes))
            ):
                # Normalize only wire order; duplicates and invalid types still
                # reach the strict validator without adding or removing codes.
                raw = {**raw, "certainty_reason_codes": sorted(codes)}
        return SupportModelReply.model_validate(raw)

    @classmethod
    def _candidate_from_reply(cls, reply, *, subject, target, occurrence):
        exact = SupportModelReply.model_validate(reply)
        # Sorting is deterministic normalization; duplicate refs remain invalid.
        return ClaimEvidenceSupportCandidateSchema(
            claim_support_subject_ref=subject, evidence_target_set_ref=target,
            support_semantic_contract_version=SUPPORT_SEMANTIC_CONTRACT,
            relation=exact.relation, supporting_refs=tuple(sorted(exact.supporting_refs)),
            contradicting_refs=tuple(sorted(exact.contradicting_refs)),
            evaluation_occurrence_ref=occurrence,
            evaluator_policy_id=cls.POLICY_ID, evaluator_policy_version=cls.POLICY_VERSION,
        )

    @classmethod
    def parse_reply(cls, raw, *, subject, target, occurrence):
        """Historical support-only parser retained for direct callers/tests."""
        reply = cls._reply_model(raw)
        return cls._candidate_from_reply(
            reply, subject=subject, target=target, occurrence=occurrence
        )

    @classmethod
    def parse_current_reply(cls, raw, *, subject, target, occurrence):
        """Current physical response requires an independent FTH-03 observation."""
        reply = cls._reply_model(raw)
        if reply.certainty_relation is None:
            raise ValueError("current support response lacks FTH-03 certainty observation")
        candidate = cls._candidate_from_reply(
            reply, subject=subject, target=target, occurrence=occurrence
        )
        certainty = {
            "schema_version": "faithfulness_certainty_physical_observation_v1",
            "certainty_relation": reply.certainty_relation,
            "reason_codes": list(reply.certainty_reason_codes),
        }
        return candidate, certainty

    def _input_material(self, claim, binding, evidence, target):
        return {
            "contract": SUPPORT_RESPONSE_CONTRACT,
            "claim_ref": claim.claim_ref.model_dump(mode="json"),
            "binding_facts_ref": binding.binding_facts_ref.model_dump(mode="json"),
            "target": target.model_dump(mode="json"),
            "claim_text": claim.text_span.text,
            "normalized_value": claim.normalized_value,
            "evidence": [{"exact_source_ref": row.source_ref.exact_source_ref, **row.material} for row in evidence],
            "policy_id": self.POLICY_ID, "policy_version": self.POLICY_VERSION,
        }

    def _package(self, material, *, target, context, occurrence):
        lineage = context.active_lineage
        capability = dict(self.model_gateway.routing_capabilities(model_role=ModelRole.GENERAL.value))
        output_limit = min(768, int(capability["safe_max_output_tokens"]))
        cap_hash = canonical_sha256({**capability, "effective_max_output_tokens": output_limit})
        token_hash = canonical_sha256({"estimator": "deterministic_mixed_text", "version": "v1"})
        cap = ModelContextCapabilityBindingSchema(
            model_capability_ref="model-gateway-capability://sha256/" + cap_hash,
            capability_schema_id="model_gateway_routing_capability_v1", capability_schema_version="v1",
            capability_semantic_fingerprint=cap_hash, model_context_window=int(capability["safe_context_window"]),
            safe_max_output_tokens=output_limit, token_estimator_id="deterministic_mixed_text",
            token_estimator_version="v1", token_estimator_semantic_fingerprint=token_hash,
        )
        runtime = ApplicationContextRuntime(estimator=self.estimator)
        payloads = {
            ContextSourceFamily.PARAGRAPH_ARTIFACT: {
                "claim_target": {"claim_id": target.claim_support_subject_ref.subject_ref,
                                 "text": material["claim_text"], "normalized_value": material["normalized_value"],
                                 "claim_type": material["claim_ref"]["claim_occurrence_ref"]["claim_type"]},
            },
            ContextSourceFamily.EVIDENCE_PACKAGE: {
                "support_evidence": material["evidence"],
            },
        }
        def resolve(source):
            claim, binding, evidence = self._scope(target, context)
            if self._input_material(claim, binding, evidence, target) != material:
                raise TargetClaimSupportError("support sources changed during Context validation")
            return source
        for family, payload in payloads.items():
            stamp = {"state": "CURRENT"}
            source = ContextSourceBindingSchema(
                source_family=family,
                exact_source_ref={"claim_ref": material["claim_ref"], "binding_facts_ref": material["binding_facts_ref"],
                                  "target": material["target"]},
                source_schema_id="claim_support_source_projection_v1", source_schema_version="v1",
                source_semantic_fingerprint=canonical_sha256(payload),
                authority_owner="SIF-06/SIF-07" if family is ContextSourceFamily.PARAGRAPH_ARTIFACT else "RAG/SIF-08",
                run_id=lineage.run_id, scope_ref=lineage.attempt_scope_ref,
                currentness_binding=ContextSourceStatusBindingSchema(
                    status_schema_id="claim_support_source_currentness_v1", status_schema_version="v1",
                    resolver_id="claim-support-exact-sources", resolver_policy_version="v1",
                    status_payload=stamp, status_payload_sha256=canonical_sha256(stamp), status_semantic_fingerprint=canonical_sha256(stamp),
                ),
            )
            candidate = ContextSourceCandidateSchema(
                candidate_id="support-source-" + family.value, source_binding=source, title=family.value,
                representations=(ContextRepresentationOptionSchema(
                    representation_level=ContextRepresentationLevel.EXACT,
                    projection_schema_id="claim_support_source_projection_v1", projection_schema_version="v1",
                    representation_policy_id="claim-support-exact-sources", representation_policy_version="v1",
                    materialized_payload=payload, materialized_payload_sha256=canonical_sha256(payload),
                    protected_field_evidence=tuple(payload),
                ),),
            )
            runtime.register_source_provider(ContextSourceProviderRegistration(
                source_family=family, provider_id="claim-support-" + family.value, provider_version="v1",
                candidate_reader=lambda _subject, selected=candidate: (selected,), source_binding_resolver=resolve,
                hard_eligibility_validator=lambda source, subject: (
                    source.run_id == subject.run_id and source.scope_ref == subject.consumer_scope_ref,
                    () if source.run_id == subject.run_id and source.scope_ref == subject.consumer_scope_ref else ("SUPPORT_SOURCE_SCOPE_MISMATCH",),
                ),
            ))
        rules = tuple(ContextRoleSourceRuleSchema(
            source_family=family, visibility=ContextRoleVisibility.MUST, mandatory=True,
            priority_tier=ContextPriorityTier.P0_MANDATORY_AUTHORITY,
            allowed_representation_levels=(ContextRepresentationLevel.EXACT,),
            minimum_representation_floor=ContextRepresentationLevel.EXACT,
        ) for family in payloads)
        policy = ContextPolicyBindingSchema(
            source_catalog_id=runtime.SOURCE_CATALOG_ID, source_catalog_version=runtime.SOURCE_CATALOG_VERSION,
            role_policy_id="claim-support-exact-target", role_policy_version="v1",
            cross_section_policy_id="claim-support-same-scope", cross_section_policy_version="v1",
            consumer_authorization_policy_id="claim-authorized-evidence", consumer_authorization_policy_version="v1",
            selection_policy_id="sif10-hierarchical-selection", selection_policy_version="v1",
            budget_policy_id="sif10-context-budget", budget_policy_version="v1",
            representation_policy_id="claim-support-exact-sources", representation_policy_version="v1",
        )
        system = "Judge support for the exact factual claim using only the supplied authorized Evidence. In the same physical call, independently classify whether the Claim expresses more certainty/qualification than that exact Evidence supports. Do not decide verification, rewriting or release."
        role = (
            "Support and certainty are separate logical outputs. Preserve the claim's subject, quantity, time, negation, conditions and qualifiers. Related text or a citation alone is not support. "
            "Plans do not prove completed construction; another project's facts do not prove the current project's facts. "
            "Historical/reference Evidence cannot establish a CONFIRMED current-project fact. It may support a clearly qualified proposal, estimate or reference-derived design only when the exact Claim preserves provisional/reference conditions. "
            "A Claim that turns reference material into an unqualified confirmed/current/completed fact must be ESCALATED or otherwise non-positive; ambiguous certainty remains INDETERMINATE. "
            "SUPPORTED means the full material claim content is supported without contradictory Evidence; PARTIALLY_SUPPORTED means only part. "
            "CONTRADICTED requires explicit conflicting Evidence; NOT_SUPPORTED means no sufficient material support, not proof of the opposite. "
            "For certainty_relation use ALIGNED only when the Claim's certainty/qualification does not exceed the exact Evidence; ESCALATED only when the Claim is materially more certain/less qualified than the Evidence; otherwise INDETERMINATE. "
            "certainty_relation must not rewrite or substitute the support relation. Use reason codes, not prose explanations. "
            "Never invent missing facts or pick a conflict winner. Supporting and contradicting refs must refer only to exact_source_ref values in support_evidence."
        )
        template = 'Return only JSON with relation, supporting_refs, contradicting_refs, certainty_relation, certainty_reason_codes. certainty_relation must be ALIGNED, ESCALATED or INDETERMINATE. No explanation, extra keys, Markdown or verification verdict.'
        task = self._json({"allowed_relations": [item.value for item in SupportRelation],
                           "allowed_certainty_relations": ["ALIGNED", "ESCALATED", "INDETERMINATE"],
                           "support_target_id": target.evidence_target_set_id})
        instruction = ContextInstructionMaterialSchema(system_instruction=system, role_instruction=role,
                         prompt_template=template, task_output_contract=task, dynamic_control_payloads=())
        binding = InstructionBindingSchema(
            system_instruction_ref="instruction://claim-support/system/v2", system_instruction_version="v2", system_instruction_sha256=canonical_sha256(system),
            role_instruction_ref="instruction://claim-support/role/v2", role_instruction_version="v2", role_instruction_sha256=canonical_sha256(role),
            prompt_template_ref="template://claim-support/v2", prompt_template_version="v2", prompt_template_sha256=canonical_sha256(template),
            task_output_contract_ref="task-contract://claim-support/" + canonical_sha256(task), task_output_contract_sha256=canonical_sha256(task),
            dynamic_control_bindings=(),
        )
        package = runtime.materialize_context_package(
            invocation_subject=ContextInvocationSubjectSchema(
                run_id=lineage.run_id, attempt_id=lineage.attempt_id, invocation_id=occurrence,
                logical_operation_id=occurrence, agent_role="CLAIM_SUPPORT", execution_mode="EVALUATE",
                context_purpose="CLAIM_EVIDENCE_SUPPORT", consumer_scope_ref=lineage.attempt_scope_ref,
            ),
            role_policy=ContextRolePolicySchema(role_policy_id=policy.role_policy_id, role_policy_version="v1",
                agent_role="CLAIM_SUPPORT", execution_mode="EVALUATE", context_purpose="CLAIM_EVIDENCE_SUPPORT", source_rules=rules),
            policy_binding=policy, model_capability_binding=cap, instruction_binding=binding, instruction_material=instruction,
            reserved_instruction_tokens=max(1, self.estimator.estimate(self._json(instruction.model_dump(mode="json")))),
            reserved_output_tokens=output_limit, safety_margin_tokens=64,
        )
        return runtime.validate_pre_model_call_current(package, {
            "source_catalog_version": runtime.SOURCE_CATALOG_VERSION, "role_policy_version": "v1", "cross_section_policy_version": "v1",
            "consumer_authorization_policy_version": "v1", "selection_policy_version": "v1", "budget_policy_version": "v1",
            "representation_policy_version": "v1", "model_capability_fingerprint": cap_hash, "token_estimator_fingerprint": token_hash,
        }), output_limit

    def _read(self, ref, identity):
        try:
            return self.store.get_json(ref, **identity)
        except FileNotFoundError:
            return None

    def _existing_budget_manager(self, identity):
        runtime = self.model_gateway.budget_runtime
        resolver = getattr(runtime, "resolve_existing_budget_manager", None)
        manager = (resolver(identity["task_id"], identity["run_id"]) if callable(resolver)
                   else getattr(runtime, "budget_manager", None))
        if (manager is None or not getattr(manager, "_canonical_mode", False)
                or manager.task_id != identity["task_id"] or manager.run_id != identity["run_id"]):
            raise TargetClaimSupportError("DOCUMENT Support has no exact existing Budget owner")
        manager.canonical_state()
        return manager

    def _document_context(self, raw, *, subject, target, contract, material, identity):
        """Bind the native completed DOCUMENT pair and regenerate Support eligibility."""
        from apps.enterprise_document.schemas.post_write.foundation import ArtifactStateRefSchema, ArtifactScope
        from apps.enterprise_document.schemas.post_write.quality import ArtifactClaimTargetSchema, CitationOccurrenceTargetSchema, CitationTargetRefSchema
        from apps.enterprise_document.services.post_write.shared_claim_support import ArtifactClaimTargetMaterializer, ClaimTargetSupportSubjectAdapter, SharedClaimEvidenceSupportEvaluator
        from apps.enterprise_document.services.post_write.citation import CitationGroundingTargetMaterializer
        from .post_write_citation_projection import CitationIntegritySnapshot
        from .post_write_citation_grounding import TargetPostWriteCitationGrounding
        required = {"schema_version", "document_identity", "document_input_ref", "document_input_sha256",
                    "document_result_ref", "document_result_sha256", "association_budget_continuation",
                    "citation_source_snapshot", "grounding_target"}
        if not isinstance(raw, dict) or set(raw) != required or raw["schema_version"] != "document_support_budget_context_v1":
            raise TargetClaimSupportError("DOCUMENT Support context has invalid exact fields")
        doc_identity = raw["document_identity"]
        if (not isinstance(doc_identity, dict) or set(doc_identity) != {"task_id", "run_id", "section_id"}
                or any(not isinstance(v, str) or not v.strip() for v in doc_identity.values())
                or doc_identity["task_id"] != identity["task_id"] or doc_identity["run_id"] != identity["run_id"]):
            raise TargetClaimSupportError("DOCUMENT Support crosses native association identity")
        input_ref, result_ref = raw["document_input_ref"], raw["document_result_ref"]
        if (not isinstance(input_ref, str) or not input_ref.startswith("artifact://citation-association/")
                or not input_ref.endswith("/input") or result_ref != input_ref.removesuffix("/input") + "/result"):
            raise TargetClaimSupportError("DOCUMENT Support association pair is not exact")
        native_input, intent = self.store.get_json_with_reference(input_ref, **doc_identity)
        native_result, result = self.store.get_json_with_reference(result_ref, **doc_identity)
        if (native_input.content_hash != "sha256:" + raw["document_input_sha256"]
                or native_result.content_hash != "sha256:" + raw["document_result_sha256"]):
            raise TargetClaimSupportError("DOCUMENT Support association carrier hash differs")
        doc = intent.get("input_material")
        if (intent.get("schema_version") != "citation_association_input_v1" or not isinstance(doc, dict)
                or canonical_sha256(doc) != intent.get("input_hash")
                or doc.get("visible_artifact", {}).get("payload_kind") != "document"
                or native_result.schema_version != "citation_association_result_v1"
                or result.get("input_hash") != intent["input_hash"] or result.get("outcome") != "completed"):
            raise TargetClaimSupportError("DOCUMENT Support requires a completed exact association")
        artifact = ArtifactStateRefSchema.model_validate(doc["artifact_state_ref"])
        if artifact.artifact_scope is not ArtifactScope.DOCUMENT:
            raise TargetClaimSupportError("DOCUMENT Support cannot use a Section proxy")
        association_binding = raw["association_budget_continuation"]
        if association_binding is not None:
            # These methods read only foreign-owner receipts. They never enter
            # an engine, acquire the citation semantic lock or create an owner.
            from types import SimpleNamespace
            reader = SimpleNamespace(store=self.store, gateway=self.model_gateway,
                claim_projection=SimpleNamespace(claim_runtime=self.claim_runtime))
            for name in ("_existing_budget_manager", "resolve_budget_refusal", "_budget_failure_error",
                         "resolve_budget_continuation", "_validate_admission_ack", "_validate_admission_input"):
                setattr(reader, name, getattr(TargetPostWriteCitationGrounding, name).__get__(reader))
            reader._admission_ack = TargetPostWriteCitationGrounding._admission_ack
            reader._admission_context = TargetPostWriteCitationGrounding._admission_context
            bound, admission_id, predecessor = reader.resolve_budget_continuation(association_binding, doc, doc_identity)
            if input_ref != f"artifact://citation-association/{intent['input_hash']}/admission/{admission_id}/input":
                raise TargetClaimSupportError("DOCUMENT Support completed association admission differs")
            reader._validate_admission_ack(bound, admission_id, intent["input_hash"], input_ref, doc_identity, required=True)
            reader._validate_admission_input(bound, admission_id, intent["input_hash"], intent, doc, predecessor, doc_identity)
        elif intent.get("budget_continuation") is not None or "/admission/" in input_ref:
            raise TargetClaimSupportError("DOCUMENT Support association is missing its original grant binding")
        snapshot = CitationIntegritySnapshot.model_validate(raw["citation_source_snapshot"])
        if (snapshot.artifact_state_ref != doc["artifact_state_ref"]
                or snapshot.validation_subject_hash != doc.get("subject_hash") or snapshot.catalog.scope != "DOCUMENT"
                or canonical_sha256(snapshot.model_dump(mode="json")) != doc.get("citation_source_hash")
                or list(snapshot.occurrences) != doc.get("occurrences")):
            raise TargetClaimSupportError("DOCUMENT Support citation source snapshot differs")
        if contract != SUPPORT_SEMANTIC_CONTRACT or target.claim_support_subject_ref != subject:
            raise TargetClaimSupportError("DOCUMENT Support has another semantic tuple")
        claim = self.claim_runtime.resolve_claim_by_id(subject.subject_ref)
        binding = self.claim_runtime.resolve_binding_facts(material["binding_facts_ref"])
        if claim.claim_ref.model_dump(mode="json") != material["claim_ref"] or binding.claim_ref != claim.claim_ref:
            raise TargetClaimSupportError("DOCUMENT Support immutable Claim/Binding differs")
        candidates = tuple(ArtifactClaimTargetSchema.model_validate(row) for row in doc["candidate_claim_targets"])
        matched = tuple(row for row in candidates if ClaimTargetSupportSubjectAdapter.adapt(row) == subject)
        if len(matched) != 1:
            raise TargetClaimSupportError("DOCUMENT Support Claim is not in exact native candidate domain")
        claim_target = matched[0]
        expected = ArtifactClaimTargetMaterializer._sif07_target(record=claim, artifact_state_ref=artifact,
            validation_subject_hash=doc["subject_hash"], semantic_version=claim_target.semantic_version)
        if claim_target != expected:
            raise TargetClaimSupportError("DOCUMENT Support Claim projection differs from canonical Claim")
        allowed = {row.exact_source_ref for row in binding.exact_authorized_source_refs if row.source_kind == "EVIDENCE"}
        associations = TargetPostWriteCitationGrounding.parse_reply(result["candidate"], doc)
        selected = tuple(row for row in associations if claim_target.claim_target_id in row.associated_claim_target_ids)
        occurrence_rows = {row["target"]["occurrence_ref"]: row for row in doc["occurrences"]}
        association_values, occurrences, citations, individual = [], [], [], []
        for row in selected:
            evidence = TargetPostWriteCitationGrounding.association_evidence(row, doc, input_ref)
            occurrence = CitationOccurrenceTargetSchema.model_validate(evidence.occurrence)
            entries = snapshot.catalog.groups()[occurrence.citation_alias]
            if len(entries) != 1:
                raise TargetClaimSupportError("DOCUMENT Support citation alias is not exact unique")
            origins = tuple(origin for origin in entries[0].origin_refs
                            if origin["section_id"] == claim.section_id and origin["exact_evidence_ref"] in allowed)
            if len(origins) != 1 or row.occurrence_ref not in occurrence_rows:
                raise TargetClaimSupportError("DOCUMENT Support citation is outside Claim-authorized sources")
            base = {"citation_ref":entries[0].citation_ref, "exact_origin":origins[0], "artifact_state_ref":doc["artifact_state_ref"]}
            digest = canonical_sha256(base)
            citation = CitationTargetRefSchema(citation_target_ref="citation-target://sha256/"+digest,
                exact_evidence_ref=origins[0]["exact_evidence_ref"], target_hash=digest)
            singleton = self.runtime.materialize_evidence_target_set(subject, (citation.exact_evidence_ref,), TARGET_SET_CONTRACT)
            association_values.append(evidence.association); occurrences.append(occurrence)
            citations.append(citation); individual.append(singleton)
        group = CitationGroundingTargetMaterializer(SharedClaimEvidenceSupportEvaluator(self.runtime)).materialize(
            claim_target=claim_target, associations=association_values, occurrences=occurrences,
            citation_targets=citations, individual_evidence_target_sets=individual,
            joint_target_set_contract_version=TARGET_SET_CONTRACT)
        if (group.model_dump(mode="json") != raw["grounding_target"]
                or target not in (*group.individual_evidence_target_set_refs, group.joint_evidence_target_set_ref)):
            raise TargetClaimSupportError("DOCUMENT Support pending tuple is not exact unfinished group eligibility")
        return raw, doc

    @staticmethod
    def _has_exact_failed_input_binding(result, input_ref, native_input):
        # Old failed candidates remain replayable, but cannot acquire authority
        # from a new DOCUMENT header attached to their original semantic input.
        if "input_ref" not in result and "input_sha256" not in result:
            return False
        if (result.get("input_ref") != input_ref
                or native_input.content_hash != "sha256:" + str(result.get("input_sha256"))):
            raise TargetClaimSupportError("DOCUMENT Support failed candidate original input SHA differs")
        return True

    def _budget_failure_error(self, result, input_ref, result_ref, identity):
        failure = result["failure"]
        error = TargetClaimSupportError(failure["message"], error_code=failure["error_code"],
            failure_category=failure["category"], failure_origin=failure["origin"])
        native_input, intent = self.store.get_json_with_reference(input_ref, **identity)
        native_result, exact_result = self.store.get_json_with_reference(result_ref, **identity)
        material = intent.get("input_material")
        if (exact_result != result or not isinstance(material, dict)
                or canonical_sha256(material) != intent.get("input_fingerprint")
                or result.get("input_fingerprint") != intent["input_fingerprint"]):
            raise TargetClaimSupportError("DOCUMENT Support refusal lacks exact native input/candidate binding")
        doc_context = intent.get("document_budget_context")
        if (not isinstance(doc_context, dict)
                or not self._has_exact_failed_input_binding(result, input_ref, native_input)):
            return error
        doc_identity = doc_context["document_identity"]
        doc = self.store.get_json(doc_context["document_input_ref"], **doc_identity)["input_material"]
        target = EvidenceTargetSetRefSchema.model_validate(material["target"])
        subject = target.claim_support_subject_ref
        pending = {"subject":subject.model_dump(mode="json"), "target":target.model_dump(mode="json"), "contract":SUPPORT_SEMANTIC_CONTRACT}
        key = canonical_sha256(pending)
        error.budget_failure_receipt = {"schema_version":"citation_budget_refusal_v1", "operation_kind":"DOCUMENT_SUPPORT",
            **identity, "input_ref":input_ref, "input_sha256":native_input.content_hash.removeprefix("sha256:"),
            "native_denial_ref":result_ref, "native_denial_sha256":native_result.content_hash.removeprefix("sha256:"),
            "phase_proof_ref":result_ref, "phase_proof_sha256":failure.get("phase_proof", {}).get("proof_sha256"),
            "semantic_input_sha256":intent["input_fingerprint"], "semantic_operation_ref":"claim-support-evaluation://sha256/"+key,
            "artifact_state_ref":doc["artifact_state_ref"], "artifact_version":doc["artifact_state_ref"]["artifact_version"],
            "pending_support":pending,
            **{name:doc_context[name] for name in ("document_identity", "document_input_ref", "document_input_sha256",
                "document_result_ref", "document_result_sha256", "association_budget_continuation")}}
        return error

    def resolve_budget_refusal(self, *, receipt_ref, expected_sha256, identity):
        return self._resolve_budget_refusal(receipt_ref=receipt_ref, expected_sha256=expected_sha256, identity=identity)

    def _resolve_budget_refusal(self, *, receipt_ref, expected_sha256, identity, validated_snapshot=False):
        native, result = self.store.get_json_with_reference(receipt_ref, **identity)
        if (native.content_hash != "sha256:"+expected_sha256 or not receipt_ref.startswith("artifact://claim-support/")
                or not receipt_ref.endswith("/candidate") or result.get("outcome") != "failed"
                or result.get("failure", {}).get("kind") != "CANONICAL_BUDGET_FAILURE"):
            raise TargetClaimSupportError("not an exact DOCUMENT Support Budget refusal")
        input_ref = receipt_ref.removesuffix("/candidate")+"/input"
        native_input, intent = self.store.get_json_with_reference(input_ref, **identity)
        if not self._has_exact_failed_input_binding(result, input_ref, native_input):
            raise TargetClaimSupportError("DOCUMENT Support refusal lacks immutable original input SHA binding")
        material = intent.get("input_material")
        if (not isinstance(material, dict) or canonical_sha256(material) != intent.get("input_fingerprint")
                or result.get("input_fingerprint") != intent["input_fingerprint"]):
            raise TargetClaimSupportError("DOCUMENT Support refusal semantic input differs")
        target = EvidenceTargetSetRefSchema.model_validate(material["target"])
        if not validated_snapshot:
            self._document_context(intent.get("document_budget_context"), subject=target.claim_support_subject_ref,
                target=target, contract=SUPPORT_SEMANTIC_CONTRACT, material=material, identity=identity)
        else:
            # The enclosing native snapshot already validated Claim/Binding and
            # full group sources under M6. Only foreign immutable carriers here.
            doc_context = intent.get("document_budget_context")
            if not isinstance(doc_context, dict):
                raise TargetClaimSupportError("winning Support lacks its DOCUMENT source carrier")
            for kind in ("input", "result"):
                ref, _ = self.store.get_json_with_reference(doc_context["document_"+kind+"_ref"], **doc_context["document_identity"])
                if ref.content_hash != "sha256:"+doc_context["document_"+kind+"_sha256"]:
                    raise TargetClaimSupportError("winning Support DOCUMENT carrier hash differs")
        proof = result["failure"].get("phase_proof")
        validator = getattr(self.model_gateway.budget_runtime, "validate_pre_physical_token_rejection_proof", None)
        if proof is None or not callable(validator):
            raise TargetClaimSupportError("DOCUMENT Support refusal lacks positive closed first-dispatch proof")
        proof = validator(proof)
        request = ModelRequestSchema.model_validate(intent["request"])
        from model_gateway.model_gateway import ModelGateway
        if (proof.routed_request.model_call_id != request.model_call_id or proof.routed_request.task_id != request.task_id
                or proof.routed_request.run_id != request.run_id
                or proof.routed_request.extra.get("model_request_fingerprint") != ModelGateway._request_fingerprint(request)
                or proof.native_denial.model_dump(mode="json") != result["failure"].get("native_denial")):
            raise TargetClaimSupportError("DOCUMENT Support phase proof differs from native request")
        return self._budget_failure_error(result, input_ref, receipt_ref, identity).budget_failure_receipt

    def resolve_budget_continuation(self, binding, subject, target, contract, context, *, live=False, _validated_snapshot=False):
        from .post_write_citation_grounding import CitationBudgetContinuation
        bound = CitationBudgetContinuation.model_validate(binding.model_dump(mode="json") if hasattr(binding,"model_dump") else binding)
        identity = {"task_id":self.claim_runtime.lifecycle_service.load_run_identity(context.active_lineage.run_id).task_id,
                    "run_id":context.active_lineage.run_id, "section_id":context.active_lineage.attempt_scope_ref.removeprefix("section:")}
        refusal = self._resolve_budget_refusal(receipt_ref=bound.predecessor_result_ref,
            expected_sha256=bound.predecessor_result_sha256, identity=identity, validated_snapshot=_validated_snapshot)
        pending = {"subject":subject.model_dump(mode="json"), "target":target.model_dump(mode="json"), "contract":contract}
        if refusal["pending_support"] != pending:
            raise TargetClaimSupportError("DOCUMENT Support continuation has another pending tuple")
        predecessor = self.store.get_json(refusal["input_ref"], **identity)
        manager = self._existing_budget_manager(identity)
        grant = manager.resolve_topup_grant(bound.accepted_grant_ref, bound.accepted_grant_sha256)
        life = self.claim_runtime.lifecycle_service
        resume = life.get_budget_transition(bound.resume_event_ref, bound.resume_event_sha256)
        request = grant.request; native_run = life.load_run_identity(identity["run_id"])
        if (resume.kind != "RESUME" or resume.grant_ref != grant.grant_ref or resume.grant_sha256 != grant.grant_sha256
                or resume.wait_event_ref != request.wait_event_ref or resume.wait_event_sha256 != request.wait_event_sha256
                or resume.child_preimage_sha256 != request.child_preimage_sha256
                or request.task_id != identity["task_id"] or request.run_id != identity["run_id"]
                or request.canonical_request_ref != "run-identity:"+identity["run_id"]
                or request.canonical_request_sha256 != native_run.canonical_request_sha256
                or request.native_denial_ref != bound.predecessor_result_ref or request.native_denial_sha256 != bound.predecessor_result_sha256
                or request.phase_proof_ref != bound.predecessor_result_ref or request.phase_proof_sha256 != refusal["phase_proof_sha256"]
                or request.semantic_input_sha256 != refusal["semantic_input_sha256"] or resume.semantic_input_sha256 != request.semantic_input_sha256
                or request.semantic_operation_ref != refusal["semantic_operation_ref"] or resume.semantic_operation_ref != request.semantic_operation_ref
                or request.artifact_version != str(refusal["artifact_version"]) or resume.artifact_version != request.artifact_version):
            raise TargetClaimSupportError("DOCUMENT Support native grant/resume/predecessor differs")
        if live:
            current = life.budget_preimage(identity["run_id"])
            if current.run != resume.resulting_run or current.current_children != resume.current_children or current.child_preimage_sha256 != resume.child_preimage_sha256:
                raise TargetClaimSupportError("DOCUMENT Support current Run/Children changed")
            life.assert_run_committable(identity["run_id"])
        admission_id = canonical_sha256({"semantic_input_sha256":refusal["semantic_input_sha256"],
            "predecessor_result_ref":bound.predecessor_result_ref, "predecessor_result_sha256":bound.predecessor_result_sha256,
            "grant_ref":grant.grant_ref, "grant_sha256":grant.grant_sha256, "resume_occurrence_id":resume.resume_occurrence_id})
        return bound, admission_id, predecessor

    @staticmethod
    def _admission_ack(bound, admission_id, fingerprint):
        return {"schema_version":"document_support_admission_ack_v1", "input_fingerprint":fingerprint,
                "admission_id":admission_id, "budget_continuation":bound.model_dump(mode="json")}

    def _validate_admission(self, *, bound, admission_id, fingerprint, input_ref, identity, prior, required):
        ack = self._read(input_ref.removesuffix("/input")+"/ack", identity)
        if ack is None and not required:
            return
        if ack != self._admission_ack(bound, admission_id, fingerprint):
            raise TargetClaimSupportError("DOCUMENT Support admission acknowledgement differs")
        if prior is not None:
            request = ModelRequestSchema.model_validate(prior.get("request"))
            package = ContextPackageSchema.model_validate(prior.get("context_package"))
            key = input_ref.removeprefix("artifact://claim-support/").split("/")[0]
            predecessor = self.store.get_json(bound.predecessor_result_ref.removesuffix("/candidate")+"/input", **identity)
            from .post_write_citation_grounding import TargetPostWriteCitationGrounding
            if (prior.get("budget_continuation") != bound.model_dump(mode="json")
                    or prior.get("input_fingerprint") != fingerprint
                    or prior.get("input_material") != predecessor["input_material"]
                    or prior.get("document_budget_context") != predecessor["document_budget_context"]
                    or request.task_id != identity["task_id"] or request.run_id != identity["run_id"]
                    or request.extra.get("section_id") != identity["section_id"]
                    or request.messages != predecessor["request"]["messages"] or request.max_tokens != predecessor["request"]["max_tokens"]
                    or package.invocation_subject.invocation_id != "claim-support-evaluation://sha256/"+key+"/admission/"+admission_id
                    or TargetPostWriteCitationGrounding._admission_context(package.model_dump(mode="json"))
                        != TargetPostWriteCitationGrounding._admission_context(predecessor["context_package"])
                    or request.model_call_id != "model_call_support_"+key+"_"+admission_id
                    or request.extra.get("logical_operation_id") != "claim-support-evaluation://sha256/"+key+"/admission/"+admission_id):
                raise TargetClaimSupportError("DOCUMENT Support native admission input identity differs")

    def _winning_receipt_pair(self, support, identity):
        key = canonical_sha256({"subject":support.claim_support_subject_ref.model_dump(mode="json"),
            "target":support.evidence_target_set_ref.model_dump(mode="json"), "contract":support.support_semantic_contract_version})
        occurrence = "claim-support-evaluation://sha256/"+key
        ref = "artifact://claim-support/"+key
        if support.evaluation_occurrence_ref == occurrence:
            return occurrence, ref+"/input", ref+"/candidate"
        prefix = occurrence+"/admission/"
        if not support.evaluation_occurrence_ref.startswith(prefix):
            return occurrence, ref+"/input", ref+"/candidate"
        admission_id = support.evaluation_occurrence_ref.removeprefix(prefix)
        if len(admission_id) != 64 or any(c not in "0123456789abcdef" for c in admission_id):
            raise TargetClaimSupportError("Support winning admission occurrence is invalid")
        input_ref, result_ref = ref+"/admission/"+admission_id+"/input", ref+"/admission/"+admission_id+"/candidate"
        prior = self.store.get_json(input_ref, **identity)
        life = self.claim_runtime.lifecycle_service
        # Native recorded lineage, not a public canonical Support/Claim source
        # read, keeps the validated-snapshot certainty path free of lock reentry.
        request = ModelRequestSchema.model_validate(prior["request"])
        from apps.enterprise_document.schemas.agent_rag_v1.lifecycle import ActiveExecutionLineageSchema
        resume = life.get_budget_transition(prior["budget_continuation"]["resume_event_ref"], prior["budget_continuation"]["resume_event_sha256"])
        child = next((row for row in resume.current_children if row.identity.attempt_id == request.extra["attempt_id"]), None)
        if child is None:
            raise TargetClaimSupportError("Support winning admission recorded Child is missing")
        lineage = ActiveExecutionLineageSchema(run_id=identity["run_id"], attempt_scope_ref=child.identity.attempt_scope_ref,
            attempt_id=child.identity.attempt_id, input_snapshot_version=child.identity.input_snapshot_version,
            attempt_lifecycle_revision=child.lifecycle_revision, attempt_status=child.status,
            run_lifecycle_revision=resume.resulting_run.lifecycle_revision, run_status=resume.resulting_run.status)
        context = ClaimOperationContextSchema(active_lineage=lineage, authorization_ref="native-support-winning-receipt", operation_purpose="CITATION_GROUNDING")
        bound, expected_id, predecessor = self.resolve_budget_continuation(prior["budget_continuation"],
            support.claim_support_subject_ref, support.evidence_target_set_ref, support.support_semantic_contract_version,
            context, _validated_snapshot=True)
        if expected_id != admission_id or prior.get("input_material") != predecessor["input_material"] or prior.get("document_budget_context") != predecessor["document_budget_context"]:
            raise TargetClaimSupportError("Support winning receipt semantic predecessor differs")
        self._validate_admission(bound=bound, admission_id=admission_id, fingerprint=prior["input_fingerprint"],
            input_ref=input_ref, identity=identity, prior=prior, required=True)
        return support.evaluation_occurrence_ref, input_ref, result_ref

    def read_budget_document_admission(self, binding, *, identity):
        from .post_write_citation_grounding import CitationBudgetContinuation
        bound = CitationBudgetContinuation.model_validate(binding.model_dump(mode="json") if hasattr(binding,"model_dump") else binding)
        refusal = self.resolve_budget_refusal(receipt_ref=bound.predecessor_result_ref,
            expected_sha256=bound.predecessor_result_sha256, identity=identity)
        pending = refusal["pending_support"]
        subject = ClaimSupportSubjectRefSchema.model_validate(pending["subject"])
        target = EvidenceTargetSetRefSchema.model_validate(pending["target"])
        lineage = self.claim_runtime.lifecycle_service.resolve_active_lineage(identity["run_id"], "section:"+identity["section_id"])
        context = ClaimOperationContextSchema(active_lineage=lineage, authorization_ref="native-support-slot-read", operation_purpose="CITATION_GROUNDING")
        bound, admission_id, predecessor = self.resolve_budget_continuation(bound, subject, target, pending["contract"], context)
        key = refusal["semantic_operation_ref"].removeprefix("claim-support-evaluation://sha256/")
        input_ref = f"artifact://claim-support/{key}/admission/{admission_id}/input"
        result_ref = input_ref.removesuffix("/input")+"/candidate"
        with self._lock, InterProcessFileLock(self.store.root / "claim_support_locks" / (key+".lock")):
            prior, result = self._read(input_ref, identity), self._read(result_ref, identity)
            self._validate_admission(bound=bound, admission_id=admission_id, fingerprint=refusal["semantic_input_sha256"],
                input_ref=input_ref, identity=identity, prior=prior, required=prior is not None or result is not None)
            if prior is not None and (prior.get("input_material") != predecessor["input_material"]
                    or prior.get("document_budget_context") != predecessor["document_budget_context"]):
                raise TargetClaimSupportError("Support admission slot semantic material changed")
            phase = "PRE_CITATION_INTENT_SAFE" if prior is None and result is None else "INDETERMINATE"
            if result is not None:
                if prior is None or result.get("input_fingerprint") != refusal["semantic_input_sha256"]:
                    raise TargetClaimSupportError("Support admission result has no exact native input")
                if result.get("outcome") == "completed":
                    candidate = ClaimEvidenceSupportCandidateSchema.model_validate(result["candidate"])
                    if (candidate.claim_support_subject_ref != subject or candidate.evidence_target_set_ref != target
                            or candidate.support_semantic_contract_version != pending["contract"]
                            or candidate.evaluation_occurrence_ref != refusal["semantic_operation_ref"]+"/admission/"+admission_id):
                        raise TargetClaimSupportError("Support completed admission candidate identity differs")
                    phase = "COMPLETED"
            ack_ref = input_ref.removesuffix("/input")+"/ack"
            ack = self._read(ack_ref, identity)
            ack_hash = self.store.get_json_with_reference(ack_ref, **identity)[0].content_hash.removeprefix("sha256:") if ack is not None else None
            return {"phase":phase, "admission_id":admission_id, "input_ref":input_ref, "result_ref":result_ref,
                "input_sha256":self.store.get_json_with_reference(input_ref, **identity)[0].content_hash.removeprefix("sha256:") if prior is not None else None,
                "ack_ref":ack_ref, "ack_sha256":ack_hash, "ack":ack, "intent":prior, "result":result}

    def resolve_certainty_observation(
        self,
        *,
        support_record: ClaimEvidenceSupportRecordSchema,
        operation_context: ClaimOperationContextSchema,
        read_only: bool = False,
    ) -> SupportCertaintyObservationSchema | None:
        """Resolve current v2 certainty output without semantic re-evaluation.

        Initial execution keeps the normal committable Claim guard. Terminal
        replay may use the existing read-only Claim source authority, which
        accepts exact SUCCESS-run lineage but creates no new support/Claim state.
        """
        support = ClaimEvidenceSupportRecordSchema.model_validate(support_record)
        context = ClaimOperationContextSchema.model_validate(operation_context)
        if not read_only:
            self.claim_runtime.validate_operation_context(context)
        if self.runtime.resolve_support(support.support_ref) != support:
            raise TargetClaimSupportError("certainty consumer SupportRecord is not canonical current support")
        target = support.evidence_target_set_ref
        subject = support.claim_support_subject_ref
        claim, binding, evidence = (
            self._read_scope(target, context)
            if read_only
            else self._scope(target, context)
        )
        key = canonical_sha256({
            "subject": subject.model_dump(mode="json"),
            "target": target.model_dump(mode="json"),
            "contract": support.support_semantic_contract_version,
        })
        occurrence = "claim-support-evaluation://sha256/" + key
        request_ref = f"artifact://claim-support/{key}/input"
        result_ref = f"artifact://claim-support/{key}/candidate"
        identity = {
            "task_id": self.claim_runtime.lifecycle_service.load_run_identity(
                context.active_lineage.run_id
            ).task_id,
            "run_id": context.active_lineage.run_id,
            "section_id": claim.section_id,
        }
        occurrence, request_ref, result_ref = self._winning_receipt_pair(support, identity)
        prior = self._read(request_ref, identity)
        result = self._read(result_ref, identity)
        if result is None:
            return None
        if result.get("schema_version") == "claim_support_candidate_v1":
            return None
        if result.get("schema_version") != "claim_support_candidate_v2":
            raise TargetClaimSupportError("certainty receipt has unsupported support-result schema")
        material = self._input_material(claim, binding, evidence, target)
        fingerprint = canonical_sha256(material)
        if (
            prior is None
            or prior.get("input_material") != material
            or prior.get("input_fingerprint") != fingerprint
            or result.get("input_fingerprint") != fingerprint
            or result.get("outcome") != "completed"
        ):
            raise TargetClaimSupportError("certainty receipt differs from exact current Claim/Evidence sources")
        candidate = ClaimEvidenceSupportCandidateSchema.model_validate(result.get("candidate"))
        candidate_signature = (
            candidate.claim_support_subject_ref,
            candidate.evidence_target_set_ref,
            candidate.support_semantic_contract_version,
            candidate.relation,
            candidate.supporting_refs,
            candidate.contradicting_refs,
            candidate.evaluator_policy_id,
            candidate.evaluator_policy_version,
            candidate.evaluation_occurrence_ref,
        )
        support_signature = (
            support.claim_support_subject_ref,
            support.evidence_target_set_ref,
            support.support_semantic_contract_version,
            support.relation,
            support.supporting_refs,
            support.contradicting_refs,
            support.evaluator_policy_id,
            support.evaluator_policy_version,
            support.evaluation_occurrence_ref,
        )
        if candidate_signature != support_signature or candidate.evaluation_occurrence_ref != occurrence:
            raise TargetClaimSupportError("certainty receipt candidate differs from canonical SupportRecord")
        certainty = dict(result.get("certainty_observation") or {})
        return SupportCertaintyObservationSchema.model_validate({
            **certainty,
            "physical_support_receipt_ref": result_ref,
            "input_fingerprint": fingerprint,
        })

    def resolve_certainty_observation_from_validated_snapshot(
        self,
        *,
        support_record: ClaimEvidenceSupportRecordSchema,
        claim_record,
        active_lineage,
        task_id: str,
    ) -> SupportCertaintyObservationSchema | None:
        """Read v2 certainty after the enclosing projection validated sources.

        This path deliberately does not re-run Claim/Evidence source resolution.
        The caller must have produced an exact current Post-write source snapshot
        and must repeat that snapshot before commit.  This reader only verifies
        immutable support input/result receipts against the canonical Claim and
        SupportRecord identities.
        """
        support = ClaimEvidenceSupportRecordSchema.model_validate(support_record)
        claim = claim_record
        lineage = active_lineage
        if (
            lineage.run_id != claim.claim_occurrence_ref.run_id
            or lineage.attempt_scope_ref != f"section:{claim.section_id}"
        ):
            raise TargetClaimSupportError(
                "validated-snapshot certainty lineage differs from Claim scope"
            )
        target = support.evidence_target_set_ref
        subject = support.claim_support_subject_ref
        if subject.subject_ref != claim.claim_ref.claim_id:
            raise TargetClaimSupportError(
                "validated-snapshot certainty SupportRecord targets another Claim"
            )
        key = canonical_sha256({
            "subject": subject.model_dump(mode="json"),
            "target": target.model_dump(mode="json"),
            "contract": support.support_semantic_contract_version,
        })
        occurrence = "claim-support-evaluation://sha256/" + key
        request_ref = f"artifact://claim-support/{key}/input"
        result_ref = f"artifact://claim-support/{key}/candidate"
        exact_task_id = str(task_id or "").strip()
        if not exact_task_id:
            raise TargetClaimSupportError(
                "validated-snapshot certainty requires exact task identity"
            )
        identity = {
            "task_id": exact_task_id,
            "run_id": lineage.run_id,
            "section_id": claim.section_id,
        }
        occurrence, request_ref, result_ref = self._winning_receipt_pair(support, identity)
        prior = self._read(request_ref, identity)
        result = self._read(result_ref, identity)
        if result is None:
            return None
        if result.get("schema_version") == "claim_support_candidate_v1":
            return None
        if result.get("schema_version") != "claim_support_candidate_v2":
            raise TargetClaimSupportError(
                "validated-snapshot certainty receipt has unsupported schema"
            )
        material = prior.get("input_material") if isinstance(prior, dict) else None
        if not isinstance(material, dict):
            raise TargetClaimSupportError(
                "validated-snapshot certainty receipt lacks exact input material"
            )
        fingerprint = canonical_sha256(material)
        if (
            prior.get("schema_version") != "claim_support_input_v2"
            or prior.get("input_fingerprint") != fingerprint
            or result.get("input_fingerprint") != fingerprint
            or result.get("outcome") != "completed"
            or material.get("contract") != SUPPORT_RESPONSE_CONTRACT
            or material.get("claim_ref") != claim.claim_ref.model_dump(mode="json")
            or material.get("target") != target.model_dump(mode="json")
            or material.get("policy_id") != self.POLICY_ID
            or material.get("policy_version") != self.POLICY_VERSION
        ):
            raise TargetClaimSupportError(
                "validated-snapshot certainty receipt differs from exact canonical identities"
            )
        candidate = ClaimEvidenceSupportCandidateSchema.model_validate(
            result.get("candidate")
        )
        candidate_signature = (
            candidate.claim_support_subject_ref,
            candidate.evidence_target_set_ref,
            candidate.support_semantic_contract_version,
            candidate.relation,
            candidate.supporting_refs,
            candidate.contradicting_refs,
            candidate.evaluator_policy_id,
            candidate.evaluator_policy_version,
            candidate.evaluation_occurrence_ref,
        )
        support_signature = (
            support.claim_support_subject_ref,
            support.evidence_target_set_ref,
            support.support_semantic_contract_version,
            support.relation,
            support.supporting_refs,
            support.contradicting_refs,
            support.evaluator_policy_id,
            support.evaluator_policy_version,
            support.evaluation_occurrence_ref,
        )
        if (
            candidate_signature != support_signature
            or candidate.evaluation_occurrence_ref != occurrence
        ):
            raise TargetClaimSupportError(
                "validated-snapshot certainty candidate differs from canonical SupportRecord"
            )
        certainty = dict(result.get("certainty_observation") or {})
        return SupportCertaintyObservationSchema.model_validate({
            **certainty,
            "physical_support_receipt_ref": result_ref,
            "input_fingerprint": fingerprint,
        })

    def evaluate(self, subject, target, contract, context, *, document_budget_context=None, budget_continuation=None):
        if contract != SUPPORT_SEMANTIC_CONTRACT or subject != target.claim_support_subject_ref:
            raise TargetClaimSupportError("unsupported shared support identity or contract")
        claim, binding, evidence = self._scope(target, context)
        key = canonical_sha256({"subject": subject.model_dump(mode="json"), "target": target.model_dump(mode="json"), "contract": contract})
        occurrence = "claim-support-evaluation://sha256/" + key
        material = self._input_material(claim, binding, evidence, target)
        fingerprint = canonical_sha256(material)
        request_ref = f"artifact://claim-support/{key}/input"
        result_ref = f"artifact://claim-support/{key}/candidate"
        identity = {"task_id": self.claim_runtime.lifecycle_service.load_run_identity(context.active_lineage.run_id).task_id,
                    "run_id": context.active_lineage.run_id, "section_id": claim.section_id}
        bound = admission_id = predecessor = None
        if document_budget_context is not None:
            self._document_context(document_budget_context, subject=subject, target=target, contract=contract,
                material=material, identity=identity)
        if budget_continuation is not None:
            if document_budget_context is None:
                raise TargetClaimSupportError("new Support admission requires exact DOCUMENT caller context")
            bound, admission_id, predecessor = self.resolve_budget_continuation(budget_continuation, subject, target, contract, context)
            if predecessor["input_material"] != material or predecessor["document_budget_context"] != document_budget_context:
                raise TargetClaimSupportError("Support admission semantic/document material changed")
            occurrence += "/admission/"+admission_id
            request_ref = f"artifact://claim-support/{key}/admission/{admission_id}/input"
            result_ref = request_ref.removesuffix("/input")+"/candidate"
        with self._lock, InterProcessFileLock(self.store.root / "claim_support_locks" / (key + ".lock")):
            checkpoint_current_execution()
            prior, result = self._read(request_ref, identity), self._read(result_ref, identity)
            if bound is not None:
                self._validate_admission(bound=bound, admission_id=admission_id, fingerprint=fingerprint,
                    input_ref=request_ref, identity=identity, prior=prior, required=prior is not None or result is not None)
            if prior is not None and document_budget_context is not None and prior.get("document_budget_context") != document_budget_context:
                raise TargetClaimSupportError("Support input lacks the exact DOCUMENT caller binding")
            if prior is not None:
                if (prior.get("schema_version") not in {"claim_support_input_v1", "claim_support_input_v2"}
                        or prior.get("input_material") != material
                        or prior.get("input_fingerprint") != fingerprint):
                    raise TargetClaimSupportError("support input receipt does not match exact sources")
                if prior.get("context_package") is None:
                    if prior.get("execution_mode") != "DETERMINISTIC_EMPTY_EVIDENCE":
                        raise TargetClaimSupportError("support input receipt lacks Context without deterministic empty-domain proof")
                else:
                    package = ContextPackageSchema.model_validate(prior["context_package"])
                    if package.invocation_subject.invocation_id != occurrence:
                        raise TargetClaimSupportError("support Context receipt has another occurrence")
            if result is not None:
                if (prior is None
                        or result.get("schema_version") not in {"claim_support_candidate_v1", "claim_support_candidate_v2"}
                        or result.get("input_fingerprint") != fingerprint):
                    raise TargetClaimSupportError("support candidate has no matching input receipt")
                if result.get("outcome") == "failed":
                    failure = result["failure"]
                    if failure.get("kind") == "CANONICAL_BUDGET_FAILURE" and prior.get("document_budget_context") is not None:
                        raise self._budget_failure_error(result, request_ref, result_ref, identity)
                    raise TargetClaimSupportError(failure["message"], error_code=failure["error_code"],
                                                 failure_category=failure["category"], failure_origin=failure["origin"])
                if result.get("outcome") != "completed":
                    raise TargetClaimSupportError("support candidate outcome is invalid")
                candidate = ClaimEvidenceSupportCandidateSchema.model_validate(result["candidate"])
                if candidate.claim_support_subject_ref != subject or candidate.evidence_target_set_ref != target or candidate.support_semantic_contract_version != contract:
                    raise TargetClaimSupportError("support candidate receipt identity mismatch")
                if bound is not None and candidate.evaluation_occurrence_ref != occurrence:
                    raise TargetClaimSupportError("Support completed admission physical occurrence differs")
                self.validate_target(target, context)
                return candidate
            if prior is not None:
                raise TargetClaimSupportError("support intent exists without result; no automatic redispatch",
                                             error_code="CLAIM_SUPPORT_INDETERMINATE_EXECUTION", failure_category=FailureCategory.UNKNOWN)
            if bound is not None:
                self.resolve_budget_continuation(bound, subject, target, contract, context, live=True)
            if not evidence:
                candidate = self.parse_reply(
                    {"relation": "NOT_SUPPORTED", "supporting_refs": [], "contradicting_refs": []},
                    subject=subject, target=target, occurrence=occurrence,
                )
                certainty = {
                    "schema_version": "faithfulness_certainty_physical_observation_v1",
                    "certainty_relation": "INDETERMINATE",
                    "reason_codes": ["EMPTY_EVIDENCE_CERTAINTY_UNRESOLVED"],
                }
                self.store.put_json(
                    {"schema_version": "claim_support_input_v2", "input_material": material,
                     "input_fingerprint": fingerprint, "context_package": None,
                     "execution_mode": "DETERMINISTIC_EMPTY_EVIDENCE"},
                    artifact_ref=request_ref, artifact_type="claim_support_input",
                    schema_version="claim_support_input_v2",
                    created_by="claim_support_composition", **identity,
                )
                self.store.put_json(
                    {"schema_version": "claim_support_candidate_v2", "input_fingerprint": fingerprint,
                     "outcome": "completed", "candidate": candidate.model_dump(mode="json"),
                     "certainty_observation": certainty, "token_usage": {}},
                    artifact_ref=result_ref, artifact_type="claim_support_candidate",
                    schema_version="claim_support_candidate_v2",
                    created_by="claim_support_composition", **identity,
                )
                return candidate
            package, output_limit = self._package(material, target=target, context=context, occurrence=occurrence)
            final = package.model_input.final_input_material.inline_final_input_payload
            if predecessor is not None:
                from .post_write_citation_grounding import TargetPostWriteCitationGrounding
                if (TargetPostWriteCitationGrounding._admission_context(package.model_dump(mode="json"))
                        != TargetPostWriteCitationGrounding._admission_context(predecessor["context_package"])
                        or final["messages"] != predecessor["request"]["messages"]
                        or output_limit != predecessor["request"]["max_tokens"]):
                    raise TargetClaimSupportError("Support continuation governed Context/messages/capability changed")
            request = ModelRequestSchema(
                model_call_id="model_call_support_" + key + ("_"+admission_id if admission_id else ""), task_id=identity["task_id"], run_id=identity["run_id"],
                caller_agent="ClaimSupportEvaluator", model_role=ModelRole.GENERAL.value, prompt="", messages=final["messages"],
                max_tokens=output_limit, temperature=0.0, created_at=datetime.now(timezone.utc).isoformat(),
                extra={"call_purpose": "claim_support", "response_contract": SUPPORT_RESPONSE_CONTRACT,
                       "section_id": claim.section_id, "attempt_scope_ref": context.active_lineage.attempt_scope_ref,
                       "attempt_id": context.active_lineage.attempt_id, "logical_operation_id": occurrence,
                       "input_snapshot_version": context.active_lineage.input_snapshot_version,
                       "prompt_id": SUPPORT_RESPONSE_CONTRACT, "prompt_version": "v1",
                       "llm_context_summary": {"managed": True, "package_id": package.package_ref.context_package_id,
                                               "context_sha256": package.model_input.final_model_input_sha256}},
            )
            intent = {"schema_version": "claim_support_input_v2", "input_material": material,
                      "input_fingerprint": fingerprint, "context_package": package.model_dump(mode="json")}
            if document_budget_context is not None:
                intent.update(request=request.model_dump(mode="json"), document_budget_context=document_budget_context)
            if bound is not None:
                intent["budget_continuation"] = bound.model_dump(mode="json")
                self.store.put_json(self._admission_ack(bound, admission_id, fingerprint),
                    artifact_ref=request_ref.removesuffix("/input")+"/ack", artifact_type="document_support_admission_ack",
                    schema_version="document_support_admission_ack_v1", created_by="claim_support_composition", **identity)
            native_input = self.store.put_json(intent,
                                artifact_ref=request_ref, artifact_type="claim_support_input", schema_version="claim_support_input_v2",
                                created_by="claim_support_composition", **identity)
            try:
                self.validate_target(target, context)
                checkpoint_current_execution()
                response = self.model_gateway.generate(request)
                if (response.model_call_id, response.task_id, response.run_id) != (request.model_call_id, request.task_id, request.run_id):
                    raise TargetClaimSupportError("support provider returned a different invocation identity")
                if not response.success:
                    error = response.error
                    raise TargetClaimSupportError(error.message if error else (response.error_message or "support provider failed"),
                         error_code=error.error_code if error else "CLAIM_SUPPORT_PROVIDER_FAILED",
                         failure_category=error.failure_category if error else FailureCategory.UNKNOWN, failure_origin=response.failure_origin)
                candidate, certainty = self.parse_current_reply(
                    response.content, subject=subject, target=target, occurrence=occurrence
                )
                self.validate_target(target, context)
                checkpoint_current_execution()
            except Exception as exc:
                budget = isinstance(exc, (CanonicalBudgetCapacityExceeded, CanonicalBudgetDeadlineExceeded, CanonicalBudgetDraining))
                category = FailureCategory.RESOURCE_EXHAUSTED if budget else classify_failure(exc)
                origin = FailureOrigin.CANONICAL_BUDGET if budget else getattr(exc, "failure_origin", FailureOrigin.UNKNOWN)
                failure = {"message": str(exc) or type(exc).__name__, "error_code": getattr(exc, "error_code", type(exc).__name__),
                           "category": category.value, "origin": FailureOrigin(origin).value}
                if document_budget_context is not None and origin is FailureOrigin.CANONICAL_BUDGET:
                    failure["kind"] = "CANONICAL_BUDGET_FAILURE"
                    denial, proof = getattr(exc,"native_denial",None), getattr(exc,"pre_physical_phase_proof",None)
                    if denial is not None:failure["native_denial"] = denial.model_dump(mode="json")
                    if proof is not None:failure["phase_proof"] = proof.model_dump(mode="json")
                failed_result = {"schema_version": "claim_support_candidate_v2", "input_fingerprint": fingerprint,
                                 "outcome": "failed", "failure": failure}
                if document_budget_context is not None:
                    failed_result.update(input_ref=request_ref,
                        input_sha256=native_input.content_hash.removeprefix("sha256:"))
                self.store.put_json(failed_result, artifact_ref=result_ref,
                                     artifact_type="claim_support_candidate", schema_version="claim_support_candidate_v2",
                                     created_by="claim_support_composition", **identity)
                if failure.get("kind") == "CANONICAL_BUDGET_FAILURE":
                    raise self._budget_failure_error(self.store.get_json(result_ref,**identity),request_ref,result_ref,identity) from exc
                raise TargetClaimSupportError(failure["message"], error_code=failure["error_code"],
                                             failure_category=category, failure_origin=origin) from exc
            self.store.put_json({"schema_version": "claim_support_candidate_v2", "input_fingerprint": fingerprint,
                                "outcome": "completed", "candidate": candidate.model_dump(mode="json"),
                                "certainty_observation": certainty,
                                "token_usage": response.token_usage.model_dump(mode="json")}, artifact_ref=result_ref,
                                artifact_type="claim_support_candidate", schema_version="claim_support_candidate_v2",
                                created_by="claim_support_composition", **identity)
            return candidate

    def support_bindings(self, *, records, binding_records, operation_context):
        context = ClaimOperationContextSchema.model_validate(operation_context)
        if len(records) != len(binding_records):
            raise TargetClaimSupportError("each Claim requires exactly one committed BindingFacts record")
        results = []
        for claim, binding in zip(records, binding_records):
            if binding.claim_ref != claim.claim_ref:
                raise TargetClaimSupportError("support composition crossed Claim bindings")
            subject = ClaimSupportSubjectRefSchema(authority_kind=ClaimSupportAuthorityKind.SIF07_CLAIM,
                                                  subject_ref=claim.claim_ref.claim_id)
            members = tuple(item.exact_source_ref for item in binding.exact_authorized_source_refs if item.source_kind == "EVIDENCE")
            target = self.runtime.materialize_evidence_target_set(subject, members, TARGET_SET_CONTRACT)
            self.validate_target(target, context)
            outcome = self.runtime.evaluate_support_candidate(subject, target, SUPPORT_SEMANTIC_CONTRACT, context)
            results.append(outcome.existing_record if outcome.existing_record is not None else self.runtime.commit_support(outcome.candidate, context))
        return tuple(results)


__all__ = ["TargetClaimSupportComposition", "TargetClaimSupportError", "SupportModelReply", "SUPPORT_RESPONSE_CONTRACT", "SUPPORT_SEMANTIC_CONTRACT", "TARGET_SET_CONTRACT"]
