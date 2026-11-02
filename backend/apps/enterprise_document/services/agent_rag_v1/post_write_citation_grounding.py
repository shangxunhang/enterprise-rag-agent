"""345 application composition: committed citations -> canonical Claims -> shared Support.

Association owns no Claim identity or support truth. Model calls are intent-first,
Gateway/Budget/Context governed; committed replay never dispatches cognition.
"""
from __future__ import annotations

from datetime import datetime, timezone
import json
from threading import RLock
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator
from apps.enterprise_document.schemas.agent_rag_v1.verification import ClaimOperationContextSchema
from apps.enterprise_document.schemas.post_write.foundation import (
    ExpectedItemExecutionAccountingSchema, ExpectedItemExecutionState, StageDescriptorSchema, StagePlanSchema,
    ValidationCompletionItemSchema, ValidationCompletionItemState, ValidationIntermediateKind,
    ValidatorExecutionRecordSchema, ValidationRunState,
)
from apps.enterprise_document.schemas.post_write.quality import (
    ArtifactClaimTargetSchema, CitationAssociationEvidenceSchema, CitationClaimAssociationSchema,
    CitationOccurrenceTargetSchema, CitationTargetRefSchema, CitationGroundingTargetSchema,
    CitationGroundingPayloadSchema, CitationGroundingUncertaintyPayloadSchema,
    UpstreamSIF07ClaimAuthorityResolutionSchema,
)
from apps.enterprise_document.services.post_write.artifact_state import ArtifactCanonicalPayloadMaterializer
from apps.enterprise_document.services.post_write.citation import (
    CitationEvidenceTargetResolver, CitationGroundingTargetMaterializer, CitationGroundingValidator,
    CitationGroundingUncertaintyProjector, CitationGroundingProjector,
)
from apps.enterprise_document.services.post_write.shared_claim_support import (
    ArtifactClaimTargetMaterializer, SharedClaimEvidenceSupportEvaluator,
)
from apps.enterprise_document.services.post_write.execution import ValidatorInputEnvelopeBuilder
from core.hashing import canonical_sha256
from core.retry_policy import classify_failure
from schemas.failure import FailureCategory, FailureOrigin
from core.persistence.interprocess_lock import InterProcessFileLock
from core.runtime.execution_control import checkpoint_current_execution
from core.token_estimator import DeterministicTokenEstimator
from model_gateway.model_contract import ModelRole
from schemas.model import ModelRequestSchema
from schemas.context import (
    ContextInstructionMaterialSchema, ContextInvocationSubjectSchema, ContextPolicyBindingSchema,
    ContextPriorityTier, ContextRepresentationLevel, ContextRepresentationOptionSchema,
    ContextRolePolicySchema, ContextRoleSourceRuleSchema, ContextRoleVisibility,
    ContextSourceBindingSchema, ContextSourceCandidateSchema, ContextSourceFamily,
    ContextSourceStatusBindingSchema, InstructionBindingSchema, ModelContextCapabilityBindingSchema,
)
from .context_runtime import ApplicationContextRuntime, ContextSourceProviderRegistration
from .claim_support_composition import TARGET_SET_CONTRACT, SUPPORT_SEMANTIC_CONTRACT
from .budget_manager import CanonicalBudgetCapacityExceeded, CanonicalBudgetDeadlineExceeded, CanonicalBudgetDraining

ASSOCIATION_CONTRACT = "citation_claim_association_v1"
POLICY_ID = "citation-claim-association"
POLICY_VERSION = "v1"
ASSOCIATION_REPRESENTATION_VERSION = "v2"


class CitationGroundingError(ValueError):
    """Exact Grounding inputs or durable execution cannot be trusted."""
    def __init__(self, message, *, error_code="CITATION_GROUNDING_FAILED",
                 failure_category=FailureCategory.VALIDATION, failure_origin=FailureOrigin.UNKNOWN):
        super().__init__(message)
        self.error_code = error_code
        self.failure_category = FailureCategory(failure_category)
        self.failure_origin = FailureOrigin(failure_origin)


class AssociationRow(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    occurrence_ref: str
    state: Literal["ASSOCIATED", "NO_APPLICABLE_CLAIM", "INDETERMINATE"]
    associated_claim_target_ids: tuple[str, ...]

    @model_validator(mode="after")
    def _shape(self):
        ids = self.associated_claim_target_ids
        if len(ids) != len(set(ids)) or not self.occurrence_ref:
            raise ValueError("duplicate Claim IDs or missing occurrence identity")
        if (self.state == "ASSOCIATED") != bool(ids):
            raise ValueError("association state and Claim IDs disagree")
        return self


class AssociationReply(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    associations: tuple[AssociationRow, ...]


class CitationBudgetContinuation(BaseModel):
    """Exact references only; authority is resolved by the existing owners."""
    model_config = ConfigDict(extra="forbid", frozen=True)
    schema_version: Literal["document_budget_continuation_v1"] = "document_budget_continuation_v1"
    predecessor_result_ref: str = Field(min_length=1)
    predecessor_result_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    accepted_grant_ref: str = Field(min_length=1)
    accepted_grant_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    resume_event_ref: str = Field(min_length=1)
    resume_event_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")


class GroundingAccounting(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    schema_version: Literal["citation_grounding_accounting_v1"] = "citation_grounding_accounting_v1"
    input_hash: str
    artifact_state_ref: dict
    subject_hash: str
    occurrence_outcomes: tuple[dict, ...]
    association_refs: tuple[dict, ...]
    groups: tuple[dict, ...]
    fact_refs: tuple[dict, ...]
    state: Literal["PASS", "FAIL", "NOT_APPLICABLE", "INDETERMINATE", "TECHNICAL_FAILURE"]
    failure: dict | None = None
    input_receipt_ref: str | None = None


def _strict_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate association JSON key")
        result[key] = value
    return result


class TargetPostWriteCitationGrounding:
    # Finite real-workload calibration; routing capability remains the bound.
    MAX_OUTPUT_TOKENS = 8192
    CLAIM_PROJECTION_CONTRACT_VERSION = "claim-projection-v1"
    VALIDATOR_ID = "post_write_citation_grounding"
    VERSION = "v3"

    def __init__(self, *, post_write, claim_projection, support_composition, model_gateway):
        if claim_projection.support is not support_composition:
            raise CitationGroundingError("Grounding must use the existing canonical shared Support owner")
        self.post_write, self.claim_projection, self.support = post_write, claim_projection, support_composition
        self.gateway = model_gateway
        self.adapter = SharedClaimEvidenceSupportEvaluator(support_composition.runtime)
        self.store = support_composition.store
        self._lock = RLock()
        self.estimator = DeterministicTokenEstimator()

    @staticmethod
    def stage_plan():
        stage = StageDescriptorSchema(stage_ref="citation-grounding-stage:v1", stage_version="v1", sequence_index=0,
            stage_input_schema_ref="schema://citation-grounding-input/v1", stage_output_schema_ref="schema://citation-grounding-accounting/v1",
            execution_policy_ref="policy://citation-grounding/v1", execution_policy_version="v1", timeout_seconds=120, max_attempts=1)
        base = {"schema_version": "validator_stage_plan_v1", "stage_plan_ref": "stage-plan:citation-grounding:v1",
            "validator_id": TargetPostWriteCitationGrounding.VALIDATOR_ID, "validator_version": "v3", "stage_plan_version": "v1", "stages": [stage.model_dump(mode="json")]}
        return StagePlanSchema(**base, stage_plan_hash=canonical_sha256(base))

    def _inputs(self, arguments):
        pw = self.post_write
        citation, prerequisite_refs, facts = pw.citation_integrity.read_committed_inputs(**arguments)
        claim_sources = self.claim_projection.resolve_sources(post_write=pw,
            artifact_ref=arguments["artifact_ref"], scope=arguments["scope"], source_sections=arguments["source_sections"],
            assembly_input_manifest_ref=arguments["assembly_input_manifest_ref"],
            citation_validation_run_id=arguments["validation_run_id"], citation_subject_hash=arguments["subject_hash"])
        records = {record.claim_ref.claim_id: record for section in claim_sources.sections for record in section.claim_records}
        targets = ArtifactClaimTargetMaterializer.materialize(
            authority_resolution=UpstreamSIF07ClaimAuthorityResolutionSchema(state="UPSTREAM_SIF07_CLAIM_AUTHORITY_PRESENT",
                claim_refs=tuple(records[key].claim_ref for key in sorted(records)), resolution_reason_ref="claim-authority://committed-343"),
            artifact_state_ref=arguments["artifact_ref"], validation_subject_hash=arguments["subject_hash"],
            semantic_version=self.CLAIM_PROJECTION_CONTRACT_VERSION, resolved_claim_records=records) if records else ()
        artifact = pw.artifact_store.resolve_exact(arguments["artifact_ref"])
        visible = ArtifactCanonicalPayloadMaterializer.business_payload(artifact.canonical_payload_ref)
        complete = {section.section_id: all(row.accounting_state.value != "INDETERMINATE" for row in section.coverage_records)
                    for section in claim_sources.sections}
        material = {"contract": ASSOCIATION_CONTRACT, "policy_id": POLICY_ID, "policy_version": POLICY_VERSION,
            "association_representation_version": ASSOCIATION_REPRESENTATION_VERSION,
            "validation_run_id": arguments["validation_run_id"], "subject_hash": arguments["subject_hash"],
            "artifact_state_ref": arguments["artifact_ref"].model_dump(mode="json"),
            "visible_artifact": visible, "occurrences": list(citation.occurrences),
            "candidate_claim_targets": [row.model_dump(mode="json") for row in targets],
            "claim_source_hash": canonical_sha256(claim_sources.model_dump(mode="json")),
            "citation_source_hash": canonical_sha256(citation.model_dump(mode="json")),
            "prerequisite_refs": [ref.model_dump(mode="json") for ref in prerequisite_refs],
            "no_applicable_allowed": {row["target"]["occurrence_ref"]: row["span"]["field"] == "content" and complete[row["span"]["section_id"]]
                                      for row in citation.occurrences}}
        return material, citation, claim_sources, targets, facts

    def _identity(self, source_sections):
        lineages = [self.claim_projection.claim_runtime.lifecycle_service.resolve_active_lineage(
            row.active_execution_lineage.run_id, row.active_execution_lineage.attempt_scope_ref) for row in source_sections]
        lineage = sorted(lineages, key=lambda row: row.attempt_scope_ref)[0]
        identity = {"task_id": self.claim_projection.claim_runtime.lifecycle_service.load_run_identity(lineage.run_id).task_id,
                    "run_id": lineage.run_id, "section_id": lineage.attempt_scope_ref.removeprefix("section:")}
        return identity, lineage

    @staticmethod
    def _model_material(material):
        """Exact semantic projection; full provenance stays in the input receipt.

        Citation association consumes visible structure, occurrences and the full
        canonical Claim domain. Catalog origins and prerequisite Fact envelopes
        are runtime bindings, not additional authored text or support evidence.
        """
        visible = material["visible_artifact"]

        def section_payload(payload):
            return {key: payload[key] for key in ("payload_kind", "section_id", "section_title", "content")}

        if visible["payload_kind"] == "section":
            model_visible = section_payload(visible)
        elif visible["payload_kind"] == "document":
            model_visible = {key: visible[key] for key in (
                "payload_kind", "document_logical_id", "missing_section_keys", "withheld_section_keys", "partial")}
            model_visible["included_sections"] = [
                {"section_key": row["section_key"], "visible_payload": section_payload(row["visible_payload"])}
                for row in visible["included_sections"]
            ]
        else:
            raise CitationGroundingError("unsupported visible Artifact kind for citation association")
        result = {key: value for key, value in material.items() if key != "prerequisite_refs"}
        result["visible_artifact"] = model_visible
        result["source_material_sha256"] = canonical_sha256(material)
        return result

    def _package(self, material, lineage, occurrence):
        capability = dict(self.gateway.routing_capabilities(model_role=ModelRole.GENERAL.value))
        output_limit = min(self.MAX_OUTPUT_TOKENS, int(capability["safe_max_output_tokens"]))
        cap_hash = canonical_sha256({**capability, "effective_max_output_tokens": output_limit})
        token_hash = canonical_sha256({"estimator": "deterministic_mixed_text", "version": "v1"})
        cap = ModelContextCapabilityBindingSchema(model_capability_ref="model-gateway-capability://sha256/"+cap_hash,
            capability_schema_id="model_gateway_routing_capability_v1", capability_schema_version="v1",
            capability_semantic_fingerprint=cap_hash, model_context_window=int(capability["safe_context_window"]),
            safe_max_output_tokens=output_limit, token_estimator_id="deterministic_mixed_text", token_estimator_version="v1", token_estimator_semantic_fingerprint=token_hash)
        runtime = ApplicationContextRuntime(estimator=self.estimator)
        family = ContextSourceFamily.PARAGRAPH_ARTIFACT
        payload = {"citation_association_input": self._model_material(material)}
        stamp = {"state": "CURRENT"}
        source = ContextSourceBindingSchema(source_family=family, exact_source_ref={"artifact_state_ref": material["artifact_state_ref"],
            "claim_source_hash": material["claim_source_hash"], "citation_source_hash": material["citation_source_hash"],
            "association_input_sha256": canonical_sha256(material), "association_representation_version": ASSOCIATION_REPRESENTATION_VERSION},
            source_schema_id="citation_association_source_v2", source_schema_version="v2", source_semantic_fingerprint=canonical_sha256(payload),
            authority_owner="POST_WRITE/SIF-07", run_id=lineage.run_id, scope_ref=lineage.attempt_scope_ref,
            currentness_binding=ContextSourceStatusBindingSchema(status_schema_id="citation_association_currentness_v1", status_schema_version="v1",
                resolver_id=POLICY_ID, resolver_policy_version="v1", status_payload=stamp, status_payload_sha256=canonical_sha256(stamp), status_semantic_fingerprint=canonical_sha256(stamp)))
        candidate = ContextSourceCandidateSchema(candidate_id="citation-association-sources", source_binding=source, title="Exact citation association sources",
            representations=(ContextRepresentationOptionSchema(representation_level=ContextRepresentationLevel.EXACT,
                projection_schema_id="citation_association_source_v2", projection_schema_version="v2",
                representation_policy_id=POLICY_ID, representation_policy_version=ASSOCIATION_REPRESENTATION_VERSION, materialized_payload=payload,
                materialized_payload_sha256=canonical_sha256(payload), protected_field_evidence=tuple(payload)),))
        runtime.register_source_provider(ContextSourceProviderRegistration(source_family=family, provider_id=POLICY_ID, provider_version="v1",
            candidate_reader=lambda _subject: (candidate,), source_binding_resolver=lambda value: value,
            hard_eligibility_validator=lambda value, subject: (value.run_id == subject.run_id and value.scope_ref == subject.consumer_scope_ref, ())))
        policy = ContextPolicyBindingSchema(source_catalog_id=runtime.SOURCE_CATALOG_ID, source_catalog_version=runtime.SOURCE_CATALOG_VERSION,
            role_policy_id=POLICY_ID, role_policy_version="v1", cross_section_policy_id="citation-exact-artifact", cross_section_policy_version="v1",
            consumer_authorization_policy_id="canonical-claim-candidates", consumer_authorization_policy_version="v1",
            selection_policy_id="sif10-hierarchical-selection", selection_policy_version="v1", budget_policy_id="sif10-context-budget", budget_policy_version="v1",
            representation_policy_id=POLICY_ID, representation_policy_version=ASSOCIATION_REPRESENTATION_VERSION)
        system = "Associate each cited occurrence with existing canonical factual Claims using the exact Artifact meaning. Never create Claims or decide support, verification, Gate, repair or release."
        role = "Consider the full supplied structure and canonical candidate set. A citation may attach to multiple atomic Claims; many citations may attach to one Claim. Adjacency or lexical similarity alone is not semantic association. Use INDETERMINATE when attachment cannot be reliably resolved or the applicable Claim domain is uncertain. NO_APPLICABLE_CLAIM requires a genuine semantic absence and no_applicable_allowed=true."
        template = 'Return only JSON {"associations":[{"occurrence_ref":"exact input id","state":"ASSOCIATED|NO_APPLICABLE_CLAIM|INDETERMINATE","associated_claim_target_ids":[]}]}. Exactly one row per eligible_occurrence_refs. Only ASSOCIATED has nonempty candidate Claim IDs. No extra keys.'
        task = json.dumps({"contract": ASSOCIATION_CONTRACT, "eligible_occurrence_refs": material["eligible_occurrence_refs"]}, sort_keys=True)
        instructions = ContextInstructionMaterialSchema(system_instruction=system, role_instruction=role, prompt_template=template, task_output_contract=task, dynamic_control_payloads=())
        binding = InstructionBindingSchema(system_instruction_ref="instruction://citation-association/system/v1", system_instruction_version="v1", system_instruction_sha256=canonical_sha256(system),
            role_instruction_ref="instruction://citation-association/role/v1", role_instruction_version="v1", role_instruction_sha256=canonical_sha256(role),
            prompt_template_ref="template://citation-association/v1", prompt_template_version="v1", prompt_template_sha256=canonical_sha256(template),
            task_output_contract_ref="contract://citation-association/"+canonical_sha256(task), task_output_contract_sha256=canonical_sha256(task), dynamic_control_bindings=())
        rule = ContextRoleSourceRuleSchema(source_family=family, visibility=ContextRoleVisibility.MUST, mandatory=True,
            priority_tier=ContextPriorityTier.P0_MANDATORY_AUTHORITY, allowed_representation_levels=(ContextRepresentationLevel.EXACT,), minimum_representation_floor=ContextRepresentationLevel.EXACT)
        package = runtime.materialize_context_package(invocation_subject=ContextInvocationSubjectSchema(run_id=lineage.run_id, attempt_id=lineage.attempt_id,
            invocation_id=occurrence, logical_operation_id=occurrence, agent_role="CITATION_ASSOCIATION", execution_mode="EVALUATE", context_purpose="CITATION_CLAIM_ASSOCIATION", consumer_scope_ref=lineage.attempt_scope_ref),
            role_policy=ContextRolePolicySchema(role_policy_id=POLICY_ID, role_policy_version="v1", agent_role="CITATION_ASSOCIATION", execution_mode="EVALUATE", context_purpose="CITATION_CLAIM_ASSOCIATION", source_rules=(rule,)),
            policy_binding=policy, model_capability_binding=cap, instruction_binding=binding, instruction_material=instructions,
            reserved_instruction_tokens=max(1,self.estimator.estimate(json.dumps(instructions.model_dump(mode="json")))), reserved_output_tokens=output_limit, safety_margin_tokens=64)
        return runtime.validate_pre_model_call_current(package, {"source_catalog_version":runtime.SOURCE_CATALOG_VERSION,"role_policy_version":"v1","cross_section_policy_version":"v1","consumer_authorization_policy_version":"v1","selection_policy_version":"v1","budget_policy_version":"v1","representation_policy_version":ASSOCIATION_REPRESENTATION_VERSION,"model_capability_fingerprint":cap_hash,"token_estimator_fingerprint":token_hash}), output_limit

    @staticmethod
    def parse_reply(raw, material):
        reply = AssociationReply.model_validate(json.loads(raw, object_pairs_hook=_strict_object) if isinstance(raw,str) else raw)
        actual = [row.occurrence_ref for row in reply.associations]
        if len(actual) != len(set(actual)) or set(actual) != set(material["eligible_occurrence_refs"]):
            raise CitationGroundingError("association reply omitted, duplicated or added occurrence")
        candidates = {row["claim_target_id"] for row in material["candidate_claim_targets"]}
        for row in reply.associations:
            if not set(row.associated_claim_target_ids) <= candidates:
                raise CitationGroundingError("association reply Claim escapes canonical candidates")
            if row.state == "NO_APPLICABLE_CLAIM" and not material["no_applicable_allowed"][row.occurrence_ref]:
                raise CitationGroundingError("association cannot exclude uncovered or uncertain Claim domain")
        return tuple(sorted(reply.associations,key=lambda row:row.occurrence_ref))

    def _budget_failure_error(self, result, intent_ref, receipt_ref, identity):
        failure = result["failure"]
        error = CitationGroundingError(failure["message"], error_code=failure["error_code"],
            failure_category=failure["category"], failure_origin=failure["origin"])
        input_envelope, native_input = self.store.get_json_with_reference(intent_ref, **identity)
        result_envelope, native_result = self.store.get_json_with_reference(receipt_ref, **identity)
        if (native_result != result or native_input.get("input_hash") != result.get("input_hash")
                or canonical_sha256(native_input.get("input_material")) != result.get("input_hash")):
            raise CitationGroundingError("Budget failure receipt lacks exact immutable input/result binding")
        error.budget_failure_receipt = {
            "schema_version": "citation_budget_refusal_v1",
            "operation_kind": "CITATION_ASSOCIATION",
            "task_id": identity["task_id"], "run_id": identity["run_id"], "section_id": identity["section_id"],
            "input_ref": intent_ref, "input_sha256": input_envelope.content_hash.removeprefix("sha256:"),
            "native_denial_ref": receipt_ref, "native_denial_sha256": result_envelope.content_hash.removeprefix("sha256:"),
            "phase_proof_ref": receipt_ref,
            "phase_proof_sha256": failure.get("phase_proof", {}).get("proof_sha256"),
            "semantic_input_sha256": result["input_hash"],
            "semantic_operation_ref": "citation-association-call://sha256/" + result["input_hash"],
            "artifact_state_ref": native_input["input_material"]["artifact_state_ref"],
            "artifact_version": native_input["input_material"]["artifact_state_ref"]["artifact_version"],
        }
        return error

    @staticmethod
    def normalize_budget_refusal(value):
        raw = dict(value.model_dump(mode="json") if hasattr(value,"model_dump") else value)
        kind = raw.setdefault("operation_kind","CITATION_ASSOCIATION")
        if kind == "CITATION_ASSOCIATION":
            for name in ("document_identity","document_input_ref","document_input_sha256","document_result_ref",
                         "document_result_sha256","association_budget_continuation","pending_support"):
                if raw.get(name) is not None:
                    raise CitationGroundingError("association refusal contains Support-only binding")
                raw.pop(name,None)
        elif kind != "DOCUMENT_SUPPORT":
            raise CitationGroundingError("unknown DOCUMENT Budget operation kind")
        return raw


    def resolve_budget_refusal(self, *, receipt_ref, expected_sha256, identity):
        """Read and verify one immutable first-physical TOKEN refusal; never mint proof."""
        if receipt_ref.startswith("artifact://claim-support/") and receipt_ref.endswith("/candidate"):
            return self.support.resolve_budget_refusal(receipt_ref=receipt_ref,
                expected_sha256=expected_sha256, identity=identity)
        result_envelope, result = self.store.get_json_with_reference(receipt_ref, **identity)
        if result_envelope.content_hash != "sha256:" + expected_sha256:
            raise CitationGroundingError("Budget refusal result exact content hash differs")
        if (not receipt_ref.endswith("/result") or result.get("outcome") != "failed"
                or result.get("failure", {}).get("kind") != "CANONICAL_BUDGET_FAILURE"):
            raise CitationGroundingError("not an immutable canonical Budget refusal result")
        intent_ref = receipt_ref.removesuffix("/result") + "/input"
        _, intent = self.store.get_json_with_reference(intent_ref, **identity)
        material = intent.get("input_material")
        if (not isinstance(material, dict) or canonical_sha256(material) != intent.get("input_hash")
                or result.get("input_hash") != intent.get("input_hash")
                or material.get("visible_artifact", {}).get("payload_kind") != "document"):
            raise CitationGroundingError("Budget continuation requires exact unchanged DOCUMENT input")
        proof = result["failure"].get("phase_proof")
        validator = getattr(self.gateway.budget_runtime, "validate_pre_physical_token_rejection_proof", None)
        if proof is None or not callable(validator):
            raise CitationGroundingError("Budget refusal lacks positive closed first-dispatch native proof")
        proof = validator(proof)
        from model_gateway.model_gateway import ModelGateway
        request = ModelRequestSchema.model_validate(intent["request"])
        if (proof.routed_request.model_call_id != request.model_call_id
                or proof.routed_request.task_id != request.task_id or proof.routed_request.run_id != request.run_id
                or proof.routed_request.extra.get("model_request_fingerprint") != ModelGateway._request_fingerprint(request)
                or proof.native_denial.model_dump(mode="json") != result["failure"].get("native_denial")):
            raise CitationGroundingError("Budget phase proof differs from exact original native request")
        # The phase proof establishes only this closed Gateway occurrence. Engine
        # worker closure and full lifecycle Child preimage are checked by their owners.
        return self._budget_failure_error(result, intent_ref, receipt_ref, identity).budget_failure_receipt

    def _existing_budget_manager(self, identity):
        runtime = self.gateway.budget_runtime
        resolver = getattr(runtime, "resolve_existing_budget_manager", None)
        if callable(resolver):
            return resolver(identity["task_id"], identity["run_id"])
        manager = getattr(runtime, "budget_manager", None)
        if (manager is None or not getattr(manager, "_canonical_mode", False)
                or manager.task_id != identity["task_id"] or manager.run_id != identity["run_id"]):
            raise CitationGroundingError("DOCUMENT continuation has no exact existing Budget owner")
        manager.canonical_state()
        return manager

    def resolve_budget_continuation(self, binding, material, identity, *, live=False):
        raw = binding.model_dump(mode="json") if hasattr(binding, "model_dump") else binding
        bound = CitationBudgetContinuation.model_validate(raw)
        if bound.predecessor_result_ref.startswith("artifact://claim-support/") and bound.predecessor_result_ref.endswith("/candidate"):
            refusal = self.support.resolve_budget_refusal(receipt_ref=bound.predecessor_result_ref,
                expected_sha256=bound.predecessor_result_sha256, identity=identity)
            self.resolve_completed_document_association(refusal, material=material, identity=refusal["document_identity"])
            from apps.enterprise_document.schemas.agent_rag_v1.claim_support import ClaimSupportSubjectRefSchema, EvidenceTargetSetRefSchema
            pending = refusal["pending_support"]
            lineage = self.claim_projection.claim_runtime.lifecycle_service.resolve_active_lineage(identity["run_id"],"section:"+identity["section_id"])
            context = ClaimOperationContextSchema(active_lineage=lineage, authorization_ref="native-document-support-continuation", operation_purpose="CITATION_GROUNDING")
            return self.support.resolve_budget_continuation(bound, ClaimSupportSubjectRefSchema.model_validate(pending["subject"]),
                EvidenceTargetSetRefSchema.model_validate(pending["target"]), pending["contract"], context, live=live)
        manager = self._existing_budget_manager(identity)
        grant = manager.resolve_topup_grant(bound.accepted_grant_ref, bound.accepted_grant_sha256)
        life = self.claim_projection.claim_runtime.lifecycle_service
        resume = life.get_budget_transition(bound.resume_event_ref, bound.resume_event_sha256)
        request = grant.request
        run_identity = life.load_run_identity(identity["run_id"])
        key = canonical_sha256(material)
        if (resume.kind != "RESUME" or resume.grant_ref != grant.grant_ref or resume.grant_sha256 != grant.grant_sha256
                or resume.wait_event_ref != request.wait_event_ref or resume.wait_event_sha256 != request.wait_event_sha256
                or resume.child_preimage_sha256 != request.child_preimage_sha256
                or request.task_id != identity["task_id"] or request.run_id != identity["run_id"]
                or request.native_denial_ref != bound.predecessor_result_ref
                or request.native_denial_sha256 != bound.predecessor_result_sha256
                or request.phase_proof_ref != bound.predecessor_result_ref
                or request.canonical_request_ref != "run-identity:" + run_identity.run_id
                or request.canonical_request_sha256 != run_identity.canonical_request_sha256
                or run_identity.task_id != identity["task_id"]
                or request.semantic_input_sha256 != key or resume.semantic_input_sha256 != key
                or resume.semantic_operation_ref != request.semantic_operation_ref
                or resume.artifact_version != request.artifact_version
                or str(material["artifact_state_ref"]["artifact_version"]) != request.artifact_version):
            raise CitationGroundingError("DOCUMENT continuation native grant/resume/input differs")
        refusal = self.resolve_budget_refusal(receipt_ref=bound.predecessor_result_ref,
            expected_sha256=bound.predecessor_result_sha256, identity=identity)
        if (refusal["semantic_input_sha256"] != key or refusal["semantic_operation_ref"] != request.semantic_operation_ref
                or refusal["phase_proof_sha256"] != request.phase_proof_sha256):
            raise CitationGroundingError("DOCUMENT continuation predecessor phase differs")
        _, predecessor = self.store.get_json_with_reference(refusal["input_ref"], **identity)
        if predecessor["input_material"] != material:
            raise CitationGroundingError("DOCUMENT continuation semantic material changed")
        if live:
            current = life.budget_preimage(identity["run_id"])
            if (current.run != resume.resulting_run or current.current_children != resume.current_children
                    or current.child_preimage_sha256 != resume.child_preimage_sha256):
                raise CitationGroundingError("DOCUMENT continuation current Run/Children changed")
            life.assert_run_committable(identity["run_id"])
        admission_id = canonical_sha256({"semantic_input_sha256": key,
            "predecessor_result_ref": bound.predecessor_result_ref,
            "predecessor_result_sha256": bound.predecessor_result_sha256,
            "grant_ref": grant.grant_ref, "grant_sha256": grant.grant_sha256,
            "resume_occurrence_id": resume.resume_occurrence_id})
        return bound, admission_id, predecessor

    @staticmethod
    def _admission_context(raw):
        # Invocation/package identifiers change; governed source, instructions,
        # capabilities and the actual model messages do not.
        result = {key: raw.get(key) for key in (
            "policy_binding", "instruction_binding", "budget_binding", "context",
            "candidate_decisions", "selected_materializations")}
        # Only invocation-derived identifiers differ between lawful admissions.
        # Every native authorization/source/assessment decision remains exact.
        from copy import deepcopy
        decisions = deepcopy(raw.get("consumer_authorization_decisions", []))
        for decision in decisions:
            for target in (decision.get("consumer_invocation_ref", {}),
                    decision.get("decision_ref", {}).get("consumer_invocation_ref", {})):
                target.pop("invocation_id", None)
            decision.get("decision_ref", {}).pop("decision_id", None)
            decision.pop("decision_semantic_fingerprint", None)
        result["consumer_authorization_decisions"] = decisions
        return result

    @staticmethod
    def _admission_ack(bound, admission_id, key):
        return {"schema_version":"citation_admission_ack_v1", "input_hash":key,
            "admission_id":admission_id, "budget_continuation":bound.model_dump(mode="json")}

    def _validate_admission_ack(self, bound, admission_id, key, intent_ref, identity, *, required):
        ref = intent_ref.removesuffix("/input") + "/ack"
        try:
            native, ack = self.store.get_json_with_reference(ref, **identity)
        except FileNotFoundError:
            if required:
                raise CitationGroundingError("DOCUMENT admission intent/result has no native acknowledgement")
            return None, None
        if ack != self._admission_ack(bound, admission_id, key):
            raise CitationGroundingError("DOCUMENT admission acknowledgement differs from exact native binding")
        return native, ack

    def _validate_admission_input(self, bound, admission_id, key, intent, material, predecessor, identity):
        request = ModelRequestSchema.model_validate(intent.get("request"))
        context = intent.get("context_package", {})
        if context.get("schema_version") == "context_package_v1":
            from schemas.context import ContextPackageSchema
            ContextPackageSchema.model_validate(context)
        if (intent.get("input_material") != material or intent.get("input_hash") != key
                or intent.get("budget_continuation") != bound.model_dump(mode="json")
                or self._admission_context(context) != self._admission_context(predecessor["context_package"])
                or request.messages != predecessor["request"]["messages"]
                or request.max_tokens != predecessor["request"]["max_tokens"]
                or request.task_id != identity["task_id"] or request.run_id != identity["run_id"]
                or request.model_call_id != "model_call_citation_association_"+key+"_"+admission_id
                or request.extra.get("logical_operation_id") != "citation-association-call://sha256/"+key+"/admission/"+admission_id):
            raise CitationGroundingError("DOCUMENT admission immutable input/source/occurrence differs")

    def read_budget_document_admission(self, binding, *, identity, include_grounding_commit=False):
        """Passive exact slot protocol read; callers separately prove worker/cursor closure."""
        raw = binding.model_dump(mode="json") if hasattr(binding, "model_dump") else binding
        parsed = CitationBudgetContinuation.model_validate(raw)
        if parsed.predecessor_result_ref.startswith("artifact://claim-support/") and parsed.predecessor_result_ref.endswith("/candidate"):
            slot = self.support.read_budget_document_admission(parsed, identity=identity)
            if not include_grounding_commit:
                return slot
            refusal = self.resolve_budget_refusal(receipt_ref=parsed.predecessor_result_ref,
                expected_sha256=parsed.predecessor_result_sha256, identity=identity)
            native_input, original = self.store.get_json_with_reference(
                refusal["document_input_ref"], **refusal["document_identity"])
            material = original["input_material"]
            if (native_input.content_hash != "sha256:" + refusal["document_input_sha256"]
                    or original.get("input_hash") != canonical_sha256(material)):
                raise CitationGroundingError("Grounding classification DOCUMENT native input differs")
            self.resolve_completed_document_association(refusal, material=material,
                identity=refusal["document_identity"])
            return self._classify_budget_grounding_commit(slot, material)
        _, original = self.store.get_json_with_reference(
            parsed.predecessor_result_ref.removesuffix("/result") + "/input", **identity)
        material = original["input_material"]
        bound, admission_id, predecessor = self.resolve_budget_continuation(parsed, material, identity)
        key = canonical_sha256(material)
        intent_ref = f"artifact://citation-association/{key}/admission/{admission_id}/input"
        result_ref = intent_ref.removesuffix("/input") + "/result"
        def read(ref):
            try:return self.store.get_json(ref, **identity)
            except FileNotFoundError:return None
        with self._lock, InterProcessFileLock(self.store.root / "citation_association_locks" / (key+".lock")):
            intent, result = read(intent_ref), read(result_ref)
            ack_ref, ack = self._validate_admission_ack(bound, admission_id, key, intent_ref, identity,
                required=intent is not None or result is not None)
            if intent is not None:
                self._validate_admission_input(bound, admission_id, key, intent, material, predecessor, identity)
            if result is not None and (intent is None or result.get("input_hash") != key):
                raise CitationGroundingError("DOCUMENT admission result lacks exact immutable input")
            phase = "PRE_CITATION_INTENT_SAFE" if intent is None else "INDETERMINATE"
            if result is not None:
                if result.get("outcome") == "completed":
                    self.parse_reply(result["candidate"], material)
                    phase = "COMPLETED"
                elif result.get("outcome") != "failed":
                    raise CitationGroundingError("DOCUMENT admission result outcome differs")
            slot = {"phase":phase, "semantic_input_sha256":key, "input_sha256":key,
                "admission_id":admission_id, "input_ref":intent_ref, "result_ref":result_ref,
                "ack_ref":intent_ref.removesuffix("/input")+"/ack",
                "ack_sha256":ack_ref.content_hash.removeprefix("sha256:") if ack_ref else None,
                "ack":ack, "intent":intent, "result":result}
            return self._classify_budget_grounding_commit(slot, material) if include_grounding_commit else slot

    def _classify_budget_grounding_commit(self, slot, material):
        """Read existing Grounding commits; never dispatch or repair them."""
        from apps.enterprise_document.schemas.post_write.foundation import (
            ArtifactScope, ArtifactStateRefSchema, ValidationFactRefSchema,
        )
        if material.get("visible_artifact", {}).get("payload_kind") != "document":
            raise CitationGroundingError("Grounding execution classification requires exact DOCUMENT input")
        artifact_ref = ArtifactStateRefSchema.model_validate(material["artifact_state_ref"])
        if artifact_ref.artifact_scope is not ArtifactScope.DOCUMENT:
            raise CitationGroundingError("Grounding execution classification has another Artifact scope")
        validation_run_id, subject_hash = material["validation_run_id"], material["subject_hash"]
        item_ref = "citation:item:" + artifact_ref.artifact_hash
        pw = self.post_write
        with pw.database.read_transaction() as connection:
            run = pw.validation_store.resolve_run_exact(validation_run_id, connection=connection)
            if run.subject_key.subject_hash != subject_hash:
                raise CitationGroundingError("Grounding execution classification subject differs")
            record = pw.validation_store.find_validator_execution_for_item(
                validation_run_id=validation_run_id, validator_id=self.VALIDATOR_ID,
                expected_item_ref=item_ref, connection=connection)
            # The committed terminal wins over its retained historical marker.
            if record is not None:
                plan = self.stage_plan()
                input_hash = canonical_sha256(material)
                provenance = ("citation-grounding-input://sha256/" + input_hash,)
                envelope = ValidatorInputEnvelopeBuilder(validation_view_resolver=pw.validation_view_resolver).build(
                    validation_run_id=validation_run_id, subject_hash=subject_hash, artifact_state_ref=artifact_ref,
                    stage_plan=plan, applicable_expected_validation_item_refs=(item_ref,),
                    exact_authority_refs=provenance, connection=connection)
                execution_ref = "validator-execution:citation-grounding:" + canonical_sha256(
                    {"input_hash":envelope.input_hash, "item_ref":item_ref})
                if (record.validation_run_id != validation_run_id or record.validator_execution_ref != execution_ref
                        or record.input_hash != envelope.input_hash or record.validator_id != self.VALIDATOR_ID
                        or record.validator_version != self.VERSION or record.subject_hash != subject_hash
                        or record.stage_plan_ref != plan.stage_plan_ref or record.stage_plan_version != plan.stage_plan_version
                        or len(record.intermediate_refs) != 1 or len(record.expected_item_accounting) != 1):
                    raise CitationGroundingError("committed Grounding execution identity/version differs")
                snapshot = pw.validation_intermediate_runtime.resolve_typed(
                    record.intermediate_refs[0], GroundingAccounting, connection=connection)
                refs = tuple(ValidationFactRefSchema.model_validate(ref) for ref in snapshot.fact_refs)
                accounting = record.expected_item_accounting[0]
                if (snapshot.input_hash != input_hash or snapshot.artifact_state_ref != artifact_ref.model_dump(mode="json")
                        or snapshot.subject_hash != subject_hash or refs != record.produced_fact_refs
                        or accounting.expected_validation_item_ref != item_ref or accounting.fact_refs != refs
                        or accounting.provenance_refs != provenance):
                    raise CitationGroundingError("committed Grounding input/Fact/accounting binding differs")
                return slot
            pending = connection.execute(
                "SELECT event_id FROM validation_execution_events WHERE validation_run_id=? "
                "AND event_kind='INTERMEDIATE_COMMITTED' AND json_extract(event_json,'$.payload.target_ref')=?",
                (validation_run_id, item_ref)).fetchone()
            if pending is not None or run.execution_state is ValidationRunState.COMPLETED:
                return {**slot, "native_admission_phase":slot["phase"], "phase":"INDETERMINATE",
                    "grounding_commit":{"validation_run_id":validation_run_id, "expected_item_ref":item_ref,
                        "intermediate_event_id":pending["event_id"] if pending is not None else None,
                        "validator_execution_ref":None, "reason":"INCOMPLETE_GROUNDING_COMMIT"}}
        # This is only the existing item-marker read, not general no-effect proof.
        return slot

    def _associate(self, material, identity, lineage, validate_current, *, budget_continuation=None):
        key = canonical_sha256(material)
        intent_ref, receipt_ref = f"artifact://citation-association/{key}/input", f"artifact://citation-association/{key}/result"
        bound = admission_id = predecessor = None
        if budget_continuation is not None:
            bound, admission_id, predecessor = self.resolve_budget_continuation(budget_continuation, material, identity)
            intent_ref = f"artifact://citation-association/{key}/admission/{admission_id}/input"
            receipt_ref = intent_ref.removesuffix("/input") + "/result"
        def read(ref):
            try:return self.store.get_json(ref, **identity)
            except FileNotFoundError:return None
        with self._lock, InterProcessFileLock(self.store.root / "citation_association_locks" / (key+".lock")):
            prior, result = read(intent_ref), read(receipt_ref)
            if bound is not None:
                self._validate_admission_ack(bound, admission_id, key, intent_ref, identity,
                    required=prior is not None or result is not None)
                if prior is not None:
                    self._validate_admission_input(bound, admission_id, key, prior, material, predecessor, identity)
            if prior is not None and (prior.get("input_material") != material or prior.get("input_hash") != key):
                raise CitationGroundingError("association immutable input receipt differs")
            if prior is not None and prior.get("budget_continuation") != (bound.model_dump(mode="json") if bound else None):
                raise CitationGroundingError("association immutable admission binding differs")
            if result is not None:
                if prior is None or result.get("input_hash") != key:
                    raise CitationGroundingError("association receipt has no matching intent")
                if result["outcome"] == "failed":
                    if result["failure"]["kind"] == "CANONICAL_BUDGET_FAILURE":
                        raise self._budget_failure_error(result, intent_ref, receipt_ref, identity)
                    return None, intent_ref, result["failure"]
                if result["outcome"] != "completed":
                    raise CitationGroundingError("invalid association receipt outcome")
                validate_current()
                return self.parse_reply(result["candidate"],material), intent_ref, None
            if prior is not None:
                raise CitationGroundingError("association intent without result: uncertain external execution, no redispatch")
            if bound is not None:
                self.resolve_budget_continuation(bound, material, identity, live=True)
            validate_current()
            self.claim_projection.claim_runtime.lifecycle_service.assert_attempt_committable(run_id=lineage.run_id,
                attempt_scope_ref=lineage.attempt_scope_ref, attempt_id=lineage.attempt_id, input_snapshot_version=lineage.input_snapshot_version)
            occurrence = "citation-association-call://sha256/"+key+("/admission/"+admission_id if admission_id else "")
            package, limit = self._package(material,lineage,occurrence)
            if predecessor is not None and (self._admission_context(package.model_dump(mode="json")) != self._admission_context(predecessor["context_package"])
                    or package.model_input.final_input_material.inline_final_input_payload["messages"] != predecessor["request"]["messages"]
                    or limit != predecessor["request"]["max_tokens"]):
                raise CitationGroundingError("DOCUMENT continuation context source/capability/messages changed")
            request = ModelRequestSchema(model_call_id="model_call_citation_association_"+key+("_"+admission_id if admission_id else ""), task_id=identity["task_id"], run_id=identity["run_id"],
                caller_agent="CitationClaimAssociation", model_role=ModelRole.GENERAL.value, prompt="", messages=package.model_input.final_input_material.inline_final_input_payload["messages"],
                max_tokens=limit, temperature=0.0, created_at=datetime.now(timezone.utc).isoformat(),
                extra={"call_purpose":"citation_association","response_contract":ASSOCIATION_CONTRACT,"section_id":identity["section_id"],
                    "association_representation_version":ASSOCIATION_REPRESENTATION_VERSION,
                    "attempt_scope_ref":lineage.attempt_scope_ref,"attempt_id":lineage.attempt_id,"input_snapshot_version":lineage.input_snapshot_version,
                    "logical_operation_id":occurrence,"prompt_id":ASSOCIATION_CONTRACT,"prompt_version":"v1",
                    "llm_context_summary":{"managed":True,"package_id":package.package_ref.context_package_id,"context_sha256":package.model_input.final_model_input_sha256}})
            input_receipt={"schema_version":"citation_association_input_v1","input_material":material,"input_hash":key,"context_package":package.model_dump(mode="json"),"request":request.model_dump(mode="json")}
            if bound is not None:
                input_receipt["budget_continuation"]=bound.model_dump(mode="json")
                self.store.put_json(self._admission_ack(bound, admission_id, key),
                    artifact_ref=intent_ref.removesuffix("/input")+"/ack",artifact_type="citation_admission_ack",schema_version="citation_admission_ack_v1",created_by=POLICY_ID,**identity)
            self.store.put_json(input_receipt,
                artifact_ref=intent_ref,artifact_type="citation_association_input",schema_version="citation_association_input_v1",created_by=POLICY_ID,**identity)
            try:
                checkpoint_current_execution()
                validate_current()
                response = self.gateway.generate(request)
                if (response.model_call_id,response.task_id,response.run_id)!=(request.model_call_id,request.task_id,request.run_id):
                    raise CitationGroundingError("association provider invocation identity mismatch")
                if not response.success:
                    error = response.error
                    raise CitationGroundingError(error.message if error else (response.error_message or "association provider failed"),
                        error_code=error.error_code if error else "CITATION_ASSOCIATION_PROVIDER_FAILED",
                        failure_category=error.failure_category if error else FailureCategory.UNKNOWN,
                        failure_origin=response.failure_origin)
                rows = self.parse_reply(response.content,material)
                validate_current()
                result={"input_hash":key,"outcome":"completed","candidate":{"associations":[row.model_dump(mode="json") for row in rows]}}
            except Exception as exc:
                budget = isinstance(exc, (CanonicalBudgetCapacityExceeded, CanonicalBudgetDeadlineExceeded, CanonicalBudgetDraining))
                origin = FailureOrigin.CANONICAL_BUDGET if budget else FailureOrigin(getattr(exc, "failure_origin", FailureOrigin.UNKNOWN))
                category = FailureCategory.RESOURCE_EXHAUSTED if budget else classify_failure(exc)
                failure = {"schema_version":"citation_grounding_failure_v1",
                    "kind":"CANONICAL_BUDGET_FAILURE" if origin is FailureOrigin.CANONICAL_BUDGET else "ASSOCIATION_TECHNICAL_FAILURE",
                    "error_type":type(exc).__name__, "error_code":getattr(exc,"error_code",type(exc).__name__),
                    "message":str(exc), "category":category.value, "origin":origin.value}
                native_denial = getattr(exc, "native_denial", None)
                phase_proof = getattr(exc, "pre_physical_phase_proof", None)
                if native_denial is not None:
                    failure["native_denial"] = native_denial.model_dump(mode="json")
                if phase_proof is not None:
                    failure["phase_proof"] = phase_proof.model_dump(mode="json")
                if getattr(exc, "phase_proof_validation_error", None) is not None:
                    failure["phase_proof_validation_error"] = exc.phase_proof_validation_error
                result = {"input_hash":key,"outcome":"failed","failure":failure}
                rows = None
                if origin is FailureOrigin.CANONICAL_BUDGET:
                    self.store.put_json(result,artifact_ref=receipt_ref,artifact_type="citation_association_result",
                        schema_version="citation_association_result_v1",created_by=POLICY_ID,**identity)
                    raise self._budget_failure_error(result, intent_ref, receipt_ref, identity) from exc
            self.store.put_json(result,artifact_ref=receipt_ref,artifact_type="citation_association_result",schema_version="citation_association_result_v1",created_by=POLICY_ID,**identity)
            return rows,intent_ref,result.get("failure")

    @staticmethod
    def association_evidence(row, material, intent_ref):
        input_hash=canonical_sha256(material)
        occurrence=next(item["target"] for item in material["occurrences"] if item["target"]["occurrence_ref"]==row.occurrence_ref)
        base={"schema_version":"citation_claim_association_v1","artifact_state_ref":material["artifact_state_ref"],"occurrence_ref":row.occurrence_ref,
            "candidate_claim_target_ids":tuple(sorted(item["claim_target_id"] for item in material["candidate_claim_targets"])),
            "associated_claim_target_ids":tuple(sorted(row.associated_claim_target_ids)),"association_state":row.state,"materializer_id":POLICY_ID,"materializer_version":POLICY_VERSION}
        digest=canonical_sha256({"association":base,"input_hash":input_hash})
        association=CitationClaimAssociationSchema(**base,association_ref="citation-association://sha256/"+digest,association_hash=digest)
        evidence={"schema_version":"citation_association_evidence_v1","validation_run_id":material["validation_run_id"],"subject_hash":material["subject_hash"],
            "occurrence":occurrence,"association":association.model_dump(mode="json"),"input_hash":input_hash,"input_receipt_ref":intent_ref,"policy_id":POLICY_ID,"policy_version":POLICY_VERSION}
        return CitationAssociationEvidenceSchema(**evidence,evidence_hash=canonical_sha256(evidence))

    def resolve_completed_document_association(self, context, *, material, identity):
        """Read an exact completed pair; this entry never invokes _associate."""
        if context.get("document_identity") != identity:
            raise CitationGroundingError("completed DOCUMENT association native identity differs")
        input_ref, result_ref = context["document_input_ref"], context["document_result_ref"]
        native_input, intent = self.store.get_json_with_reference(input_ref, **identity)
        native_result, result = self.store.get_json_with_reference(result_ref, **identity)
        if (not input_ref.startswith("artifact://citation-association/") or not input_ref.endswith("/input")
                or result_ref != input_ref.removesuffix("/input")+"/result"
                or native_input.content_hash != "sha256:"+context["document_input_sha256"]
                or native_result.content_hash != "sha256:"+context["document_result_sha256"]
                or intent.get("schema_version") != "citation_association_input_v1"
                or intent.get("input_material") != material or intent.get("input_hash") != canonical_sha256(material)
                or native_result.schema_version != "citation_association_result_v1"
                or result.get("input_hash") != intent["input_hash"] or result.get("outcome") != "completed"):
            raise CitationGroundingError("completed DOCUMENT association pair/source differs")
        binding = context.get("association_budget_continuation")
        if binding is not None:
            bound, admission_id, predecessor = self.resolve_budget_continuation(binding, material, identity)
            if input_ref != f"artifact://citation-association/{intent['input_hash']}/admission/{admission_id}/input":
                raise CitationGroundingError("completed association uses another admission")
            self._validate_admission_ack(bound, admission_id, intent["input_hash"], input_ref, identity, required=True)
            self._validate_admission_input(bound, admission_id, intent["input_hash"], intent, material, predecessor, identity)
        elif intent.get("budget_continuation") is not None or "/admission/" in input_ref:
            raise CitationGroundingError("completed association lost its native grant/ACK binding")
        return self.parse_reply(result["candidate"],material), input_ref, None

    def complete_item(self, *, validation_run_id, subject_hash, artifact_ref, scope, source_sections,
                      evidence_snapshot_ref=None, assembly_input_manifest_ref=None, budget_continuation=None):
        from apps.enterprise_document.schemas.post_write.foundation import ValidationFactRefSchema, ValidationIntermediateRefSchema
        pw=self.post_write
        args=dict(validation_run_id=validation_run_id,subject_hash=subject_hash,artifact_ref=artifact_ref,scope=scope,
            source_sections=source_sections,evidence_snapshot_ref=evidence_snapshot_ref,assembly_input_manifest_ref=assembly_input_manifest_ref)
        material,citation,sources,targets,prerequisites=self._inputs(args)
        rules={row["target"]["occurrence_ref"]:[] for row in citation.occurrences}
        for fact in prerequisites:
            rules[fact.target_ref].append(fact)
        eligible=[]; outcomes=[]; uncertainty=[]
        for row in citation.occurrences:
            occurrence=row["target"]["occurrence_ref"]
            observed=rules[occurrence]
            if not observed or len({fact.payload["rule_id"] for fact in observed})!=len(observed):
                raise CitationGroundingError("missing or duplicate committed Integrity prerequisite")
            if any(fact.payload["binding_state"]=="INVALID" for fact in observed):
                outcomes.append({"occurrence_ref":occurrence,"state":"NOT_APPLICABLE","reason":"INTEGRITY_INVALID"})
            elif any(fact.payload["binding_state"]=="INDETERMINATE" for fact in observed):
                fact=next(f for f in observed if f.payload["binding_state"]=="INDETERMINATE")
                uncertainty.append((row, next(ref for ref in material["prerequisite_refs"] if ref["fact_id"]==fact.fact_id)))
                outcomes.append({"occurrence_ref":occurrence,"state":"INDETERMINATE","reason":"PREREQUISITE_INDETERMINATE"})
            elif {fact.payload["rule_id"] for fact in observed}!={"PW-R-CIT-I-01","PW-R-CIT-I-02","PW-R-CIT-I-03"}:
                raise CitationGroundingError("Grounding requires all exact valid Integrity rules")
            else:
                eligible.append(occurrence)
        material["eligible_occurrence_refs"]=sorted(eligible)
        input_hash=canonical_sha256(material)
        item_ref="citation:item:"+artifact_ref.artifact_hash
        plan=self.stage_plan()
        provenance=("citation-grounding-input://sha256/"+input_hash,)
        envelope=ValidatorInputEnvelopeBuilder(validation_view_resolver=pw.validation_view_resolver).build(
            validation_run_id=validation_run_id,subject_hash=subject_hash,artifact_state_ref=artifact_ref,
            stage_plan=plan,applicable_expected_validation_item_refs=(item_ref,),exact_authority_refs=provenance)
        execution_ref="validator-execution:citation-grounding:"+canonical_sha256({"input_hash":envelope.input_hash,"item_ref":item_ref})
        record=pw.validation_store.find_validator_execution_for_item(validation_run_id=validation_run_id,validator_id=self.VALIDATOR_ID,expected_item_ref=item_ref)
        identity,lineage=self._identity(source_sections)
        if record is not None:
            if (record.validator_execution_ref!=execution_ref or record.input_hash!=envelope.input_hash
                    or record.validator_version!=self.VERSION or record.subject_hash!=subject_hash
                    or record.stage_plan_ref!=plan.stage_plan_ref or record.stage_plan_version!=plan.stage_plan_version
                    or len(record.intermediate_refs)!=1 or len(record.expected_item_accounting)!=1):
                raise CitationGroundingError("committed Grounding execution identity/version differs")
            snapshot=pw.validation_intermediate_runtime.resolve_typed(record.intermediate_refs[0],GroundingAccounting)
            if snapshot.input_hash!=input_hash or snapshot.artifact_state_ref!=artifact_ref.model_dump(mode="json") or snapshot.subject_hash!=subject_hash:
                raise CitationGroundingError("committed Grounding input binding differs")
            refs=tuple(ValidationFactRefSchema.model_validate(ref) for ref in snapshot.fact_refs)
            if refs!=record.produced_fact_refs or record.expected_item_accounting[0].fact_refs!=refs or record.expected_item_accounting[0].provenance_refs!=provenance:
                raise CitationGroundingError("committed Grounding Fact/accounting mismatch")
            self._verify_accounting(snapshot,material,identity,targets,prerequisites)
            return ValidationCompletionItemSchema(expected_validation_item_ref=item_ref,required=True,state=snapshot.state,
                fact_refs=refs,technical_failure_ref=snapshot.failure)
        run=pw.validation_store.resolve_run_exact(validation_run_id)
        if run.execution_state is ValidationRunState.COMPLETED or self.claim_projection.is_terminal_replay(source_sections):
            raise CitationGroundingError("terminal Run requires committed Grounding execution; no upgrade/redispatch")
        with pw.database.read_transaction() as connection:
            pending=connection.execute("SELECT event_id FROM validation_execution_events WHERE validation_run_id=? AND event_kind='INTERMEDIATE_COMMITTED' AND json_extract(event_json,'$.payload.target_ref')=?",(validation_run_id,item_ref)).fetchone()
        if pending:
            raise CitationGroundingError("incomplete Grounding commit requires exact recovery; no redispatch")
        def validate_current():
            current_material, *_ = self._inputs(args)
            current_material["eligible_occurrence_refs"] = material["eligible_occurrence_refs"]
            if current_material != material:
                raise CitationGroundingError("association sources changed from frozen input")
        support_binding = support_refusal = None
        association_binding = budget_continuation
        if budget_continuation is not None:
            parsed = CitationBudgetContinuation.model_validate(budget_continuation.model_dump(mode="json") if hasattr(budget_continuation,"model_dump") else budget_continuation)
            if parsed.predecessor_result_ref.startswith("artifact://claim-support/") and parsed.predecessor_result_ref.endswith("/candidate"):
                native_candidate,_ = self.store.get_json_with_reference(parsed.predecessor_result_ref,
                    task_id=identity["task_id"],run_id=identity["run_id"])
                predecessor_input = self.store.get_json(parsed.predecessor_result_ref.removesuffix("/candidate")+"/input",
                    task_id=identity["task_id"],run_id=identity["run_id"],section_id=native_candidate.section_id)
                # The Support may belong to a different current Section than
                # the stable DOCUMENT association anchor.
                pending_target = predecessor_input["input_material"]["target"]
                pending_claim = self.claim_projection.claim_runtime.resolve_claim_by_id(pending_target["claim_support_subject_ref"]["subject_ref"])
                support_identity = {**identity,"section_id":pending_claim.section_id}
                if native_candidate.section_id != pending_claim.section_id:
                    raise CitationGroundingError("Support refusal carrier differs from its native Claim Section")
                support_refusal = self.support.resolve_budget_refusal(receipt_ref=parsed.predecessor_result_ref,
                    expected_sha256=parsed.predecessor_result_sha256,identity=support_identity)
                support_binding = parsed
                association_binding = support_refusal["association_budget_continuation"]
        if support_refusal is not None:
            validate_current()
            rows,intent,failure=self.resolve_completed_document_association(support_refusal,material=material,identity=identity)
        else:
            rows,intent,failure=self._associate(material,identity,lineage,validate_current,**({"budget_continuation":association_binding} if association_binding is not None else {})) if eligible else ((),None,None)
        association_refs=[];associations={};produced=[];groups=[]
        if failure is not None:
            outcomes.extend({"occurrence_ref":ref,"state":"TECHNICAL_FAILURE","reason":"ASSOCIATION_TECHNICAL_FAILURE"} for ref in eligible)
        else:
            for row in rows:
                evidence=self.association_evidence(row,material,intent)
                ref=pw.validation_intermediate_runtime.commit(validation_run_id=validation_run_id,producer_ref=POLICY_ID,
                    target_ref=evidence.association.association_ref,typed_intermediate=evidence,
                    intermediate_kind=ValidationIntermediateKind.MODEL_DERIVATION,occurred_at_epoch=0.0)
                association_refs.append(ref.model_dump(mode="json"));associations[row.occurrence_ref]=evidence.association
                outcomes.append({"occurrence_ref":row.occurrence_ref,"state":row.state,"association_ref":evidence.association.association_ref})
                if row.state=="INDETERMINATE":
                    produced.append(CitationGroundingUncertaintyProjector.project(store=pw.validation_store,intermediate_runtime=pw.validation_intermediate_runtime,
                        validation_run_id=validation_run_id,subject_hash=subject_hash,artifact_state_ref=artifact_ref,occurrence=evidence.occurrence,
                        association_evidence_ref=ref,input_receipt_resolver=lambda value:self.store.get_json(value,**identity)))
        for row,ref in uncertainty:
            occurrence_basis = pw.validation_intermediate_runtime.commit(validation_run_id=validation_run_id,
                producer_ref=self.VALIDATOR_ID, target_ref=row["target"]["occurrence_ref"],
                typed_intermediate=CitationOccurrenceTargetSchema.model_validate(row["target"]),
                intermediate_kind=ValidationIntermediateKind.TARGET_ACCOUNTING,occurred_at_epoch=0.0)
            produced.append(CitationGroundingUncertaintyProjector.project(store=pw.validation_store,intermediate_runtime=pw.validation_intermediate_runtime,
                validation_run_id=validation_run_id,subject_hash=subject_hash,artifact_state_ref=artifact_ref,
                occurrence=row["target"],prerequisite_fact_ref=ValidationFactRefSchema.model_validate(ref),
                prerequisite_occurrence_ref=occurrence_basis))
        source_bindings={binding.claim_ref.claim_id:binding for section in sources.sections for binding in section.binding_records}
        section_sources={row.section.section_id:row for row in source_sections}
        for claim in targets:
            members=[row for row in citation.occurrences if row["target"]["occurrence_ref"] in associations
                and claim.claim_target_id in associations[row["target"]["occurrence_ref"]].associated_claim_target_ids]
            if not members:continue
            try:
                binding=source_bindings[claim.source_claim_ref.claim_id]
                allowed=tuple(ref.exact_source_ref for ref in binding.exact_authorized_source_refs if ref.source_kind=="EVIDENCE")
                occurrence_targets=[];citation_targets=[];target_sets=[];claim_associations=[]
                claim_record=self.claim_projection.claim_runtime.resolve_claim(claim.source_claim_ref)
                for member in members:
                    occurrence=CitationOccurrenceTargetSchema.model_validate(member["target"])
                    entries=citation.catalog.groups()[occurrence.citation_alias]
                    if len(entries)!=1:raise CitationGroundingError("association citation alias is not exact unique")
                    origins=[origin for origin in entries[0].origin_refs if origin["section_id"]==claim_record.section_id and origin["exact_evidence_ref"] in allowed]
                    if len(origins)!=1:raise CitationGroundingError("cited source has no unique Claim-authorized origin")
                    origin=origins[0]
                    writer_ref = origin["evidence_snapshot_ref"]["writer_invocation_ref"]
                    if writer_ref != section_sources[claim_record.section_id].writer_generation_invocation_ref.model_dump(mode="json"):
                        raise CitationGroundingError("cited source Writer origin differs")
                    base={"citation_ref":entries[0].citation_ref,"exact_origin":origin,"artifact_state_ref":material["artifact_state_ref"]}
                    digest=canonical_sha256(base)
                    citation_target=CitationTargetRefSchema(citation_target_ref="citation-target://sha256/"+digest,exact_evidence_ref=origin["exact_evidence_ref"],target_hash=digest)
                    individual=CitationEvidenceTargetResolver(self.adapter).resolve(claim_target=claim,citation_target_ref=citation_target,
                        selected_evidence_refs=allowed,target_set_contract_version=TARGET_SET_CONTRACT)
                    occurrence_targets.append(occurrence);citation_targets.append(citation_target);target_sets.append(individual);claim_associations.append(associations[occurrence.occurrence_ref])
                group=CitationGroundingTargetMaterializer(self.adapter).materialize(claim_target=claim,associations=claim_associations,
                    occurrences=occurrence_targets,citation_targets=citation_targets,individual_evidence_target_sets=target_sets,joint_target_set_contract_version=TARGET_SET_CONTRACT)
                current=self.claim_projection.claim_runtime.lifecycle_service.resolve_active_lineage(lineage.run_id,"section:"+claim_record.section_id)
                context=ClaimOperationContextSchema(active_lineage=current,authorization_ref="citation-grounding-input://sha256/"+input_hash,operation_purpose="CITATION_GROUNDING")
                adapter = self.adapter
                if str(getattr(scope,"value",scope)) == "DOCUMENT":
                    input_native,_ = self.store.get_json_with_reference(intent,**identity)
                    result_native,_ = self.store.get_json_with_reference(intent.removesuffix("/input")+"/result",**identity)
                    document_context = {"schema_version":"document_support_budget_context_v1", "document_identity":identity,
                        "document_input_ref":intent,"document_input_sha256":input_native.content_hash.removeprefix("sha256:"),
                        "document_result_ref":result_native.artifact_ref,"document_result_sha256":result_native.content_hash.removeprefix("sha256:"),
                        "association_budget_continuation":association_binding.model_dump(mode="json") if hasattr(association_binding,"model_dump") else association_binding,
                        "citation_source_snapshot":citation.model_dump(mode="json"),"grounding_target":group.model_dump(mode="json")}
                    def scoped_support(subject,target,contract,operation_context):
                        pending = support_refusal["pending_support"] if support_refusal is not None else None
                        exact = {"subject":subject.model_dump(mode="json"),"target":target.model_dump(mode="json"),"contract":contract}
                        validate_current()
                        if pw.artifact_store.resolve_current(artifact_ref.artifact_id).artifact_ref != artifact_ref:
                            raise CitationGroundingError("DOCUMENT Support native Artifact head changed before evaluation")
                        try:
                            return self.support.evaluate(subject,target,contract,operation_context,document_budget_context=document_context,
                                **({"budget_continuation":support_binding} if pending == exact else {}))
                        finally:
                            validate_current()
                            if pw.artifact_store.resolve_current(artifact_ref.artifact_id).artifact_ref != artifact_ref:
                                raise CitationGroundingError("DOCUMENT Support native Artifact head changed after evaluation")
                    adapter = SharedClaimEvidenceSupportEvaluator(self.support.runtime,support_evaluator=scoped_support)
                individual,joint,fact=CitationGroundingValidator(support_adapter=adapter).evaluate(validation_run_id=validation_run_id,subject_hash=subject_hash,
                    claim_target=claim,grounding_target=group,support_semantic_contract_version=SUPPORT_SEMANTIC_CONTRACT,operation_context=context)
                groups.append({"target":group.model_dump(mode="json"),"member_support_refs":[row.support_ref.model_dump(mode="json") for row in individual],"joint_support_ref":joint.support_ref.model_dump(mode="json")})
                produced.append(fact)
            except (CanonicalBudgetCapacityExceeded,CanonicalBudgetDeadlineExceeded,CanonicalBudgetDraining):
                raise
            except Exception as exc:
                if getattr(exc, "failure_origin", None) == FailureOrigin.CANONICAL_BUDGET:
                    raise
                failure={"category":classify_failure(exc).value,"origin":FailureOrigin(getattr(exc,"failure_origin",FailureOrigin.UNKNOWN)).value,
                    "schema_version":"citation_grounding_failure_v1","kind":"TARGET_OR_SUPPORT_TECHNICAL_FAILURE","claim_target_id":claim.claim_target_id,"message":str(exc),"error_type":type(exc).__name__}
                groups.append({"claim_target_id":claim.claim_target_id,"failure":failure})
        fact_refs=tuple(pw.validation_store.put_fact_if_absent_or_same(fact) for fact in produced)
        states={fact.semantic_state.value for fact in produced}
        state="TECHNICAL_FAILURE" if failure else "FAIL" if "FAIL" in states else "INDETERMINATE" if "INDETERMINATE" in states else "PASS" if produced else "NOT_APPLICABLE"
        snapshot=GroundingAccounting(input_hash=input_hash,artifact_state_ref=material["artifact_state_ref"],subject_hash=subject_hash,
            occurrence_outcomes=tuple(sorted(outcomes,key=lambda row:row["occurrence_ref"])),association_refs=tuple(association_refs),groups=tuple(groups),
            fact_refs=tuple(ref.model_dump(mode="json") for ref in fact_refs),state=state,failure=failure,input_receipt_ref=intent)
        self._verify_accounting(snapshot,material,identity,targets,prerequisites)
        checkpoint_current_execution()
        intermediate=pw.validation_intermediate_runtime.commit(validation_run_id=validation_run_id,producer_ref=self.VALIDATOR_ID,
            target_ref=item_ref,typed_intermediate=snapshot,intermediate_kind=ValidationIntermediateKind.TARGET_ACCOUNTING,occurred_at_epoch=0.0)
        with pw.database.read_transaction() as connection:sequence=pw.validation_store._next_event_sequence(connection,validation_run_id)
        event=pw.validation_store._event(validation_run_id=validation_run_id,event_id="validator-terminal:citation-grounding:"+canonical_sha256(execution_ref),
            sequence=sequence,event_kind="VALIDATOR_TECHNICAL_FAILURE" if failure else "VALIDATOR_COMPLETED",payload={"validator_execution_ref":execution_ref},occurred_at_epoch=0.0)
        accounting=ExpectedItemExecutionAccountingSchema(expected_validation_item_ref=item_ref,execution_state="TECHNICAL_FAILURE" if failure else "COMPLETED",
            fact_refs=fact_refs,technical_failure_ref=failure,provenance_refs=provenance)
        base={"schema_version":"validator_execution_record_v1","validator_execution_ref":execution_ref,"validation_run_id":validation_run_id,"subject_hash":subject_hash,
            "validator_id":self.VALIDATOR_ID,"validator_version":self.VERSION,"stage_plan_ref":plan.stage_plan_ref,"stage_plan_version":plan.stage_plan_version,
            "input_hash":envelope.input_hash,"terminal_event_id":event.event_id,"terminal_execution_state":"TECHNICAL_FAILURE" if failure else "COMPLETED",
            "expected_item_accounting":[accounting.model_dump(mode="json")],"produced_fact_refs":[ref.model_dump(mode="json") for ref in fact_refs],"intermediate_refs":[intermediate.model_dump(mode="json")]}
        pw.validation_store.finalize_validator_execution(event,ValidatorExecutionRecordSchema(**base,record_sha256=canonical_sha256(base)),stage_plan=plan,input_envelope=envelope)
        return ValidationCompletionItemSchema(expected_validation_item_ref=item_ref,required=True,state=state,fact_refs=fact_refs,technical_failure_ref=failure)

    def _verify_accounting(self,snapshot,material,identity,targets,prerequisites):
        from apps.enterprise_document.schemas.post_write.foundation import ValidationFactRefSchema,ValidationIntermediateRefSchema
        from apps.enterprise_document.schemas.agent_rag_v1.claim_support import ClaimEvidenceSupportRefSchema
        pw=self.post_write
        occurrences={row["target"]["occurrence_ref"]:row["target"] for row in material["occurrences"]}
        if len(snapshot.occurrence_outcomes)!=len(occurrences) or {row["occurrence_ref"] for row in snapshot.occurrence_outcomes}!=set(occurrences):
            raise CitationGroundingError("Grounding occurrence accounting is incomplete/duplicated")
        associations={}
        receipt=None
        if snapshot.input_receipt_ref:
            receipt=self.store.get_json(snapshot.input_receipt_ref,**identity)
            result=self.store.get_json(snapshot.input_receipt_ref.removesuffix("/input")+"/result",**identity)
            if receipt["input_hash"]!=canonical_sha256(material) or receipt["input_material"]!=material or result["input_hash"]!=receipt["input_hash"]:
                raise CitationGroundingError("Grounding association receipts differ from frozen input")
            if "/admission/" in snapshot.input_receipt_ref:
                bound, admission_id, predecessor = self.resolve_budget_continuation(receipt.get("budget_continuation"), material, identity)
                if snapshot.input_receipt_ref != f"artifact://citation-association/{snapshot.input_hash}/admission/{admission_id}/input":
                    raise CitationGroundingError("Grounding admission reference differs from native grant/resume")
                self._validate_admission_ack(bound, admission_id, snapshot.input_hash,
                    snapshot.input_receipt_ref, identity, required=True)
                self._validate_admission_input(bound, admission_id, snapshot.input_hash,
                    receipt, material, predecessor, identity)
        for raw in snapshot.association_refs:
            ref=ValidationIntermediateRefSchema.model_validate(raw)
            evidence=pw.validation_intermediate_runtime.resolve_typed(ref,CitationAssociationEvidenceSchema)
            if (ref.validation_run_id!=material["validation_run_id"] or evidence.input_hash!=snapshot.input_hash or evidence.subject_hash!=snapshot.subject_hash
                    or evidence.occurrence.model_dump(mode="json")!=occurrences.get(evidence.occurrence.occurrence_ref)):
                raise CitationGroundingError("Grounding committed association binding differs")
            if evidence.occurrence.occurrence_ref in associations:raise CitationGroundingError("duplicate committed association")
            if receipt is None or evidence.input_receipt_ref!=snapshot.input_receipt_ref:
                raise CitationGroundingError("committed association lacks its exact input receipt")
            candidate_rows=result.get("candidate",{}).get("associations",[])
            selected=[row for row in candidate_rows if row["occurrence_ref"]==evidence.occurrence.occurrence_ref]
            if (result.get("outcome")!="completed" or len(selected)!=1
                    or selected[0]["state"]!=evidence.association.association_state.value
                    or tuple(sorted(selected[0]["associated_claim_target_ids"]))!=evidence.association.associated_claim_target_ids
                    or evidence.association.candidate_claim_target_ids!=tuple(sorted(row.claim_target_id for row in targets))):
                raise CitationGroundingError("association differs from committed semantic candidate")
            associations[evidence.occurrence.occurrence_ref]=evidence.association
        expected_associations=set(material["eligible_occurrence_refs"])
        if snapshot.failure and snapshot.failure["kind"]=="ASSOCIATION_TECHNICAL_FAILURE":expected_associations=set()
        if set(associations)!=expected_associations:
            raise CitationGroundingError("Grounding association accounting is incomplete")
        for row in snapshot.occurrence_outcomes:
            if row["occurrence_ref"] in associations:
                association=associations[row["occurrence_ref"]]
                if row["state"]!=association.association_state.value or row.get("association_ref")!=association.association_ref:
                    raise CitationGroundingError("Grounding occurrence/association state differs")
        expected_claims={claim for association in associations.values() for claim in association.associated_claim_target_ids}
        actual_claims=[row["target"]["claim_target_id"] if "target" in row else row["claim_target_id"] for row in snapshot.groups]
        if set(actual_claims)!=expected_claims or len(actual_claims)!=len(set(actual_claims)):
            raise CitationGroundingError("Grounding Claim group accounting is incomplete")
        facts=[pw.validation_store.resolve_fact(ValidationFactRefSchema.model_validate(ref)) for ref in snapshot.fact_refs]
        for fact in facts:
            if fact.validation_run_id!=material["validation_run_id"] or fact.subject_hash!=snapshot.subject_hash or fact.family.value!="CITATION_GROUNDING":
                raise CitationGroundingError("Grounding Fact Run/subject/family mismatch")
            if fact.contract_version==CitationGroundingUncertaintyProjector.FACT_CONTRACT_VERSION:
                payload=CitationGroundingUncertaintyPayloadSchema.model_validate(fact.payload)
                if fact.semantic_state.value!="INDETERMINATE" or payload.artifact_state_ref.model_dump(mode="json")!=snapshot.artifact_state_ref:
                    raise CitationGroundingError("uncertainty Fact state/Artifact mismatch")
                projected = CitationGroundingUncertaintyProjector.project(store=pw.validation_store,
                    intermediate_runtime=pw.validation_intermediate_runtime, validation_run_id=material["validation_run_id"],
                    subject_hash=snapshot.subject_hash, artifact_state_ref=payload.artifact_state_ref, occurrence=payload.occurrence,
                    association_evidence_ref=payload.association_evidence_ref, prerequisite_fact_ref=payload.prerequisite_fact_ref,
                    prerequisite_occurrence_ref=payload.prerequisite_occurrence_ref,
                    input_receipt_resolver=lambda ref:self.store.get_json(ref,**identity))
                if projected != fact:
                    raise CitationGroundingError("committed uncertainty Fact differs from exact semantic basis")
            else:
                payload=CitationGroundingPayloadSchema.model_validate(fact.payload)
                if payload.grounding_target_ref not in {row["target"]["grounding_target_ref"] for row in snapshot.groups if "target" in row}:
                    raise CitationGroundingError("Grounding Fact target not accounted")
        expected_fact_targets={row["target"]["grounding_target_ref"] for row in snapshot.groups if "target" in row}
        uncertain_occurrences={row["occurrence_ref"] for row in snapshot.occurrence_outcomes if row["state"]=="INDETERMINATE"}
        uncertainty_facts=[fact for fact in facts if fact.contract_version==CitationGroundingUncertaintyProjector.FACT_CONTRACT_VERSION]
        if (len(uncertainty_facts)!=len(uncertain_occurrences)
                or {fact.payload["occurrence"]["occurrence_ref"] for fact in uncertainty_facts}!=uncertain_occurrences
                or len(facts)!=len(expected_fact_targets)+len(uncertain_occurrences)):
            raise CitationGroundingError("Grounding expected Fact coverage incomplete")
        for row in snapshot.groups:
            if "target" not in row:continue
            group=CitationGroundingTargetSchema.model_validate(row["target"])
            expected_members={ref for ref,association in associations.items() if group.claim_target_id in association.associated_claim_target_ids}
            if set(group.occurrence_refs)!=expected_members or set(group.association_refs)!={associations[ref].association_ref for ref in expected_members}:
                raise CitationGroundingError("Grounding group association membership mismatch")
            joint=self.support.runtime.resolve_support(ClaimEvidenceSupportRefSchema.model_validate(row["joint_support_ref"]))
            if joint.evidence_target_set_ref!=group.joint_evidence_target_set_ref:
                raise CitationGroundingError("committed joint Support differs")
            members=[self.support.runtime.resolve_support(ClaimEvidenceSupportRefSchema.model_validate(ref)) for ref in row["member_support_refs"]]
            if {item.evidence_target_set_ref.evidence_target_set_id for item in members}!={item.evidence_target_set_id for item in group.individual_evidence_target_set_refs}:
                raise CitationGroundingError("committed individual Support incomplete")
            projected = CitationGroundingProjector.project(validation_run_id=material["validation_run_id"],
                subject_hash=snapshot.subject_hash,grounding_target=group,joint_support_record=joint,member_support_records=members)
            matching = [fact for fact in facts if fact.target_ref == group.grounding_target_ref]
            if matching != [projected]:
                raise CitationGroundingError("committed Grounding Fact differs from exact shared Support")
        states={fact.semantic_state.value for fact in facts}
        expected="TECHNICAL_FAILURE" if snapshot.failure else "FAIL" if "FAIL" in states else "INDETERMINATE" if "INDETERMINATE" in states else "PASS" if facts else "NOT_APPLICABLE"
        if snapshot.state!=expected:raise CitationGroundingError("Grounding completion state differs from accounted Facts")
