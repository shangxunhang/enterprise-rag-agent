"""Canonical Claim-domain extraction over exact committed Paragraphs.

This is a semantic producer, not a Business Agent, Gate, support evaluator or
second Claim store. The canonical M6 runtime validates and commits all results.
"""
from __future__ import annotations

from datetime import datetime, timezone
import json
from threading import RLock
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from apps.enterprise_document.schemas.agent_rag_v1.claims import (
    ArtifactClaimCoverageUnitSchema, ClaimCoverageAccountingSchema,
    ClaimCoverageAccountingState, ClaimExtractionOrigin,
)
from apps.enterprise_document.schemas.agent_rag_v1.paragraphs import ParagraphBindingSchema
from apps.enterprise_document.schemas.agent_rag_v1.verification import ClaimOperationContextSchema
from core.hashing import canonical_sha256
from core.persistence.interprocess_lock import InterProcessFileLock
from core.retry_policy import classify_failure
from core.runtime.execution_control import checkpoint_current_execution
from core.token_estimator import DeterministicTokenEstimator
from model_gateway.model_contract import ModelRole
from schemas.claim import ClaimType
from schemas.context import (
    ContextInstructionMaterialSchema, ContextInvocationSubjectSchema, ContextPackageSchema,
    ContextPolicyBindingSchema, ContextPriorityTier, ContextRepresentationLevel,
    ContextRepresentationOptionSchema, ContextRolePolicySchema, ContextRoleSourceRuleSchema,
    ContextRoleVisibility, ContextSourceBindingSchema, ContextSourceCandidateSchema,
    ContextSourceFamily, ContextSourceStatusBindingSchema, InstructionBindingSchema,
    ModelContextCapabilityBindingSchema,
)
from schemas.model import ModelRequestSchema
from schemas.failure import FailureCategory, FailureOrigin
from .budget_manager import CanonicalBudgetCapacityExceeded, CanonicalBudgetDeadlineExceeded
from .claim_pipeline import TargetClaimMaterializer
from .context_runtime import ApplicationContextRuntime, ContextSourceProviderRegistration

EXTRACTION_CONTRACT = "canonical_claim_extraction_v1"
COVERAGE_CONTRACT = "claim-coverage-v1"


class ClaimExtractionError(RuntimeError):
    """Technical extraction failure; never means no factual claims or Gate FAIL."""

    def __init__(self, message: str, *, error_code="CLAIM_EXTRACTION_FAILED",
                 failure_category=FailureCategory.VALIDATION, failure_origin=FailureOrigin.UNKNOWN):
        super().__init__(message)
        self.error_code = error_code
        self.failure_category = FailureCategory(failure_category)
        self.failure_origin = FailureOrigin(failure_origin)


class _StrictExtraction(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class ExtractedClaimQuote(_StrictExtraction):
    quote: str = Field(min_length=1)
    occurrence_index: int = Field(ge=0, strict=True)
    claim_type: ClaimType


class ExtractedCoverageUnit(_StrictExtraction):
    unit_id: str = Field(min_length=1)
    accounting_state: ClaimCoverageAccountingState
    claims: tuple[ExtractedClaimQuote, ...]

    @model_validator(mode="after")
    def _state_is_coherent(self):
        if self.accounting_state is ClaimCoverageAccountingState.FACTUAL_CLAIMS_MATERIALIZED and not self.claims:
            raise ValueError("materialized unit requires factual Claim candidates")
        if self.accounting_state is ClaimCoverageAccountingState.NO_APPLICABLE_FACTUAL_CLAIM and self.claims:
            raise ValueError("no-factual-claim unit cannot contain Claim candidates")
        ids = [(item.quote, item.occurrence_index, item.claim_type) for item in self.claims]
        if len(ids) != len(set(ids)):
            raise ValueError("duplicate Claim occurrence in extraction output")
        return self


class ClaimExtractionProposal(_StrictExtraction):
    units: tuple[ExtractedCoverageUnit, ...] = Field(min_length=1)


class ClaimExtractionResultReceipt(_StrictExtraction):
    schema_version: Literal["claim_extraction_result_v1"] = "claim_extraction_result_v1"
    input_fingerprint: str
    request_ref: str
    model_call_id: str
    outcome: Literal["completed", "failed"]
    proposal: ClaimExtractionProposal | None = None
    failure_type: str | None = None
    failure_message: str | None = None
    failure_category: FailureCategory | None = None
    failure_origin: FailureOrigin | None = None
    token_usage: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def _exclusive_outcome(self):
        if self.outcome == "completed" and (self.proposal is None or self.failure_type is not None
                                             or self.failure_category is not None or self.failure_origin is not None):
            raise ValueError("completed extraction receipt requires proposal only")
        if self.outcome == "failed" and (self.proposal is not None or not self.failure_type or not self.failure_message
                                          or self.failure_category is None or self.failure_origin is None):
            raise ValueError("failed extraction receipt requires an exact technical failure")
        return self


def _strict_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate Claim extraction JSON key")
        result[key] = value
    return result


def exact_quote_offsets(text: str, quote: str, occurrence_index: int) -> tuple[int, int]:
    if (not quote.strip() or not isinstance(occurrence_index, int) or isinstance(occurrence_index, bool)
            or occurrence_index < 0 or occurrence_index >= len(text)):
        raise ValueError("invalid Claim quote or occurrence index")
    start = -1
    for _ in range(occurrence_index + 1):
        start = text.find(quote, start + 1)
        if start < 0:
            raise ValueError("Claim quote occurrence is absent from exact Paragraph text")
    return start, start + len(quote)


class TargetClaimExtractionRuntime:
    """Compose one lossless, bounded Section extraction and canonical Claim commit."""

    def __init__(self, *, claim_runtime, model_gateway):
        self.claim_runtime = claim_runtime
        self.paragraph_runtime = claim_runtime.paragraph_runtime
        self.model_gateway = model_gateway
        self.store = self.paragraph_runtime.artifact_runtime.artifact_store
        self.materializer = TargetClaimMaterializer()
        self.estimator = DeterministicTokenEstimator()
        self._lock = RLock()

    @staticmethod
    def _json(value):
        return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))

    def _units(self, bindings, context):
        self.claim_runtime.validate_operation_context(context)
        if not bindings:
            raise ClaimExtractionError("Claim extraction requires committed Paragraph membership")
        manifest_ref = bindings[0].manifest_ref
        manifest = self.paragraph_runtime.load_manifest(manifest_ref)
        if (any(item.manifest_ref != manifest_ref for item in bindings)
                or tuple(item.version_ref for item in bindings) != tuple(manifest.members)):
            raise ClaimExtractionError("Section extraction must cover the exact complete current Paragraph manifest")
        rows = []
        for binding in bindings:
            exact = ParagraphBindingSchema.model_validate(binding)
            ref = exact.version_ref
            if (ref.logical_ref.run_id != context.active_lineage.run_id
                    or ref.logical_ref.domain_scope_ref != context.active_lineage.attempt_scope_ref):
                raise ClaimExtractionError("Claim extraction Paragraph belongs to another scope")
            content = self.paragraph_runtime.validate_paragraph_binding_current(exact)
            if not content.text:
                raise ClaimExtractionError("Claim extraction cannot silently omit an empty Paragraph")
            rows.append({"unit_id": "claim-unit:" + canonical_sha256(ref.model_dump(mode="json")),
                         "paragraph_version_ref": ref.model_dump(mode="json"), "text": content.text})
        if len({item["unit_id"] for item in rows}) != len(rows):
            raise ClaimExtractionError("duplicate Paragraph in Claim extraction scope")
        return rows

    def validate_proposal(self, value, units):
        if isinstance(value, str):
            value = json.loads(value, object_pairs_hook=_strict_object)
        proposal = ClaimExtractionProposal.model_validate(value)
        actual = [item.unit_id for item in proposal.units]
        by_id = {item["unit_id"]: item for item in units}
        if len(actual) != len(set(actual)) or set(actual) != set(by_id):
            raise ValueError("Claim extraction must account for every exact coverage unit once")
        for unit in proposal.units:
            for claim in unit.claims:
                exact_quote_offsets(by_id[unit.unit_id]["text"], claim.quote, claim.occurrence_index)
        return proposal

    def _package(self, *, units, bindings, context, occurrence):
        lineage = context.active_lineage
        capability = dict(self.model_gateway.routing_capabilities(model_role=ModelRole.GENERAL.value))
        output_limit = min(4096, int(capability["safe_max_output_tokens"]))
        cap_hash = canonical_sha256({**capability, "effective_max_output_tokens": output_limit})
        estimator_hash = canonical_sha256({"estimator": "deterministic_mixed_text", "version": "v1"})
        capability_binding = ModelContextCapabilityBindingSchema(
            model_capability_ref="model-gateway-capability://sha256/" + cap_hash,
            capability_schema_id="model_gateway_routing_capability_v1", capability_schema_version="v1",
            capability_semantic_fingerprint=cap_hash, model_context_window=int(capability["safe_context_window"]),
            safe_max_output_tokens=output_limit, token_estimator_id="deterministic_mixed_text",
            token_estimator_version="v1", token_estimator_semantic_fingerprint=estimator_hash,
        )
        family = ContextSourceFamily.PARAGRAPH_ARTIFACT
        payload = {"coverage_units": units}
        current = {"state": "CURRENT"}
        source = ContextSourceBindingSchema(
            source_family=family,
            exact_source_ref={"paragraph_bindings": [item.model_dump(mode="json") for item in bindings]},
            source_schema_id="claim_materialization_paragraph_scope_v1", source_schema_version="v1",
            source_semantic_fingerprint=canonical_sha256(payload), authority_owner="SIF-06:ParagraphRuntime",
            run_id=lineage.run_id, scope_ref=lineage.attempt_scope_ref,
            currentness_binding=ContextSourceStatusBindingSchema(
                status_schema_id="claim_materialization_currentness_v1", status_schema_version="v1",
                resolver_id="claim-materialization-current-paragraphs", resolver_policy_version="v1",
                status_payload=current, status_payload_sha256=canonical_sha256(current),
                status_semantic_fingerprint=canonical_sha256(current),
            ),
        )
        candidate = ContextSourceCandidateSchema(
            candidate_id="claim-exact-section-paragraphs", source_binding=source, title="Exact coverage units",
            representations=(ContextRepresentationOptionSchema(
                representation_level=ContextRepresentationLevel.EXACT,
                projection_schema_id="claim_materialization_units_v1", projection_schema_version="v1",
                representation_policy_id="claim-exact-paragraph-text", representation_policy_version="v1",
                materialized_payload=payload, materialized_payload_sha256=canonical_sha256(payload),
                protected_field_evidence=("coverage_units",),
            ),),
        )
        runtime = ApplicationContextRuntime(estimator=self.estimator)
        def resolve(binding):
            restored = tuple(ParagraphBindingSchema.model_validate(item)
                             for item in binding.exact_source_ref["paragraph_bindings"])
            if self._units(restored, context) != units:
                raise ClaimExtractionError("Claim scope changed during Context assembly")
            return binding
        runtime.register_source_provider(ContextSourceProviderRegistration(
            source_family=family, provider_id="claim-exact-paragraphs", provider_version="v1",
            candidate_reader=lambda _subject: (candidate,), source_binding_resolver=resolve,
            hard_eligibility_validator=lambda binding, subject: (
                binding.run_id == subject.run_id and binding.scope_ref == subject.consumer_scope_ref,
                () if binding.run_id == subject.run_id and binding.scope_ref == subject.consumer_scope_ref else ("CLAIM_SCOPE_MISMATCH",),
            ),
        ))
        role_policy = ContextRolePolicySchema(
            role_policy_id="claim-domain-materialization", role_policy_version="v1",
            agent_role="CLAIM_MATERIALIZATION", execution_mode="EXTRACT", context_purpose="CLAIM_EXISTENCE",
            source_rules=(ContextRoleSourceRuleSchema(
                source_family=family, visibility=ContextRoleVisibility.MUST, mandatory=True,
                priority_tier=ContextPriorityTier.P0_MANDATORY_AUTHORITY,
                allowed_representation_levels=(ContextRepresentationLevel.EXACT,),
                minimum_representation_floor=ContextRepresentationLevel.EXACT,
            ),),
        )
        policy = ContextPolicyBindingSchema(
            source_catalog_id=runtime.SOURCE_CATALOG_ID, source_catalog_version=runtime.SOURCE_CATALOG_VERSION,
            role_policy_id=role_policy.role_policy_id, role_policy_version="v1",
            cross_section_policy_id="claim-same-section-only", cross_section_policy_version="v1",
            consumer_authorization_policy_id="claim-exact-scope", consumer_authorization_policy_version="v1",
            selection_policy_id="sif10-hierarchical-selection", selection_policy_version="v1",
            budget_policy_id="sif10-context-budget", budget_policy_version="v1",
            representation_policy_id="claim-exact-paragraph-text", representation_policy_version="v1",
        )
        system = "Account for factual assertions in the exact supplied text. Do not decide support, verification, repair or release."
        role = """You are the Claim Materializer. A request may contain multiple coverage units; each unit is one complete, immutable paragraph. Process every unit independently and account for every supplied unit exactly once.

Your only task is to identify and classify applicable factual assertions. Do not determine truth, source authority, requirement satisfaction, verification, repair or release. Do not rewrite facts. Apply the following steps IN ORDER to each coverage unit, then output only the existing JSON.

1. FACTUAL APPLICABILITY
Inspect the entire paragraph clause by clause. Decide whether each clause asserts a factual proposition independently eligible for evidence-based verification, rather than merely stating how this document is or will be prepared.
Exclude clauses that solely instruct, describe or commit to this document's drafting method, editorial convention, evidence-use rule, formatting, or later-section preparation, even when expressed as present-tense declarative descriptions of the document's own drafting basis or as future promises. These belong to RequirementAdherence; they are not independently asserted external-policy or project facts. Do not extract such content merely because it sounds like a policy_requirement or general_method, or because its author could be checked for compliance.
Include independently verifiable assertions of current project state or business goal, missing project information, completed checking/procurement/actions or status, concrete quantities or parameters, engineering guarantees, factual causal relationships or scope generalizations, and statements describing provisions of an actual external law, standard or policy. A documented completed action is different from a bare promise to follow a rule. A reference document's actual historical provenance is factual content, not itself a drafting instruction.
A proposed outcome is not an accomplished fact; however a concrete assertion of planning status, conditional project parameters, or quantified proposed design may still be applicable. Preserve the planned, conditional or uncertain status. Future tense alone never exempts an engineering guarantee or factual claim.
In mixed sentences, exclude only clauses that are purely drafting instructions or self-descriptions of this document's preparation; preserve every independently factual clause and all qualifiers. An engineering dependency or causal assertion about how missing/uncertain project inputs affect resource sizing, configuration values, feasibility or risks IS factual even when the same sentence continues with a rule for future writing. Extract that dependency; exclude only separable editorial instructions. Do not use a ClaimType label as a reason to skip applicability assessment.

2. CLAIM EXTRACTION
For every applicable factual assertion, extract an exact nonempty substring (quote) of the original paragraph that preserves the subject, quantity, time, negation, conditions, uncertainty and causal qualification. Never paraphrase, infer missing values, strengthen, soften or rewrite a proposition. Use occurrence_index as the zero-based occurrence of that exact quote from the beginning of its coverage unit. CRITICAL: occurrence_index is the occurrence number of that SAME FULL quote in its own paragraph, NOT the position of a claim in the claims array. If that full quote appears once, occurrence_index MUST be 0 even for the second, third or later different Claim. Two different unique quotes both require occurrence_index=0; never enumerate distinct Claims as 0,1,2.
Split distinct factual assertions when they can be separated without changing their meaning. Otherwise quote enough exact original wording to preserve the coupled factual assertion. Do not silently omit any plausible factual clause.

3. CLAIMTYPE CLASSIFICATION
Classify each extracted Claim using only the supplied public ClaimType taxonomy; do not invent or rename values. Classify only AFTER factual applicability and extraction. A fitting type never makes an otherwise procedural drafting statement factual.
If a potential factual assertion cannot be assigned any legitimate public type, do not invent one or falsely assert complete coverage; set that unit's Coverage to INDETERMINATE, retaining any other extracted Claims that can be represented legally.

4. COVERAGE CHECK
Recheck the complete paragraph, including mixed sentences, completed states, source provenance, project missing facts, generalizations, engineering guarantees, factual causal links, conditions and quantities.
FACTUAL_CLAIMS_MATERIALIZED: applicable factual accounting is complete and at least one valid Claim is present.
NO_APPLICABLE_FACTUAL_CLAIM: no applicable factual assertion exists in that entire unit.
INDETERMINATE: factual applicability or complete accounting cannot be established. Already legally extracted Claims may remain, but this does not certify full coverage.
When uncertain, do not silently discard potentially factual content: retain an exact, legally typed Claim when possible or mark the unit INDETERMINATE. Never claim complete coverage unless it is complete.

OUTPUT: Return only the existing JSON format with unchanged field names, ClaimType enum values and Coverage states; exactly one unit for every supplied unit. No reasoning, extra fields, additional explanation, invented facts or truth verdicts."""
        template = (
            'Return JSON only: {"units":[{"unit_id":"supplied id","accounting_state":"one of the three states",'
            '"claims":[{"quote":"exact text","occurrence_index":0,"claim_type":"public value"}]}]}. '
            'Every unit must occur exactly once. No extra keys, explanation or Markdown.'
        )
        task = self._json({"claim_types": [item.value for item in ClaimType], "normalization": "literal-claim-text-v1"})
        instruction = ContextInstructionMaterialSchema(system_instruction=system, role_instruction=role,
                        prompt_template=template, task_output_contract=task, dynamic_control_payloads=())
        instruction_binding = InstructionBindingSchema(
            system_instruction_ref="instruction://claim-domain/system/v1", system_instruction_version="v1",
            system_instruction_sha256=canonical_sha256(system), role_instruction_ref="instruction://claim-domain/role/v3",
            role_instruction_version="v3", role_instruction_sha256=canonical_sha256(role),
            prompt_template_ref="template://claim-domain/extract/v1", prompt_template_version="v1",
            prompt_template_sha256=canonical_sha256(template),
            task_output_contract_ref="task-contract://claim-domain/" + canonical_sha256(task),
            task_output_contract_sha256=canonical_sha256(task), dynamic_control_bindings=(),
        )
        package = runtime.materialize_context_package(
            invocation_subject=ContextInvocationSubjectSchema(
                run_id=lineage.run_id, attempt_id=lineage.attempt_id, invocation_id=occurrence,
                logical_operation_id=occurrence, agent_role="CLAIM_MATERIALIZATION", execution_mode="EXTRACT",
                context_purpose="CLAIM_EXISTENCE", consumer_scope_ref=lineage.attempt_scope_ref,
            ),
            role_policy=role_policy, policy_binding=policy, model_capability_binding=capability_binding,
            instruction_binding=instruction_binding, instruction_material=instruction,
            reserved_instruction_tokens=max(1, self.estimator.estimate(self._json(instruction.model_dump(mode="json")))),
            reserved_output_tokens=output_limit, safety_margin_tokens=64,
        )
        return runtime.validate_pre_model_call_current(package, {
            "source_catalog_version": runtime.SOURCE_CATALOG_VERSION, "role_policy_version": "v1",
            "cross_section_policy_version": "v1", "consumer_authorization_policy_version": "v1",
            "selection_policy_version": "v1", "budget_policy_version": "v1", "representation_policy_version": "v1",
            "model_capability_fingerprint": cap_hash, "token_estimator_fingerprint": estimator_hash,
        }), output_limit

    def _read(self, ref, identity):
        try:
            return self.store.get_json(ref, **identity)
        except FileNotFoundError:
            return None

    def extract_and_commit(self, *, task_id, bindings, operation_context):
        context = ClaimOperationContextSchema.model_validate(operation_context)
        bindings = tuple(ParagraphBindingSchema.model_validate(item) for item in bindings)
        units = self._units(bindings, context)
        lineage = context.active_lineage
        if self.claim_runtime.lifecycle_service.load_run_identity(lineage.run_id).task_id != task_id:
            raise ClaimExtractionError("Claim extraction Task differs from Run identity")
        material = {"contract": EXTRACTION_CONTRACT, "paragraph_bindings": [item.model_dump(mode="json") for item in bindings],
                    "units": units, "attempt_id": lineage.attempt_id,
                    "input_snapshot_version": lineage.input_snapshot_version, "authorization_ref": context.authorization_ref}
        fingerprint = canonical_sha256(material)
        occurrence = "claim-extraction://sha256/" + fingerprint
        request_ref = f"artifact://claim-extraction/{fingerprint}/input"
        result_ref = f"artifact://claim-extraction/{fingerprint}/result"
        identity = dict(task_id=task_id, run_id=lineage.run_id, attempt_id=lineage.attempt_id,
                        input_snapshot_version=lineage.input_snapshot_version, section_id=lineage.attempt_scope_ref.removeprefix("section:"))
        with self._lock, InterProcessFileLock(self.store.root / "claim_extraction_locks" / (fingerprint + ".lock")):
            checkpoint_current_execution()
            prior = self._read(request_ref, identity)
            result = self._read(result_ref, identity)
            if prior is not None:
                if (prior.get("schema_version") != "claim_extraction_input_v1" or prior.get("input_fingerprint") != fingerprint
                        or prior.get("input_material") != material):
                    raise ClaimExtractionError("Claim extraction receipt input is inconsistent")
                historical = ContextPackageSchema.model_validate(prior["context_package"])
                if historical.invocation_subject.invocation_id != occurrence:
                    raise ClaimExtractionError("Claim extraction Context belongs to another occurrence")
            if result is not None:
                receipt = ClaimExtractionResultReceipt.model_validate(result)
                if (prior is None or receipt.input_fingerprint != fingerprint or receipt.request_ref != request_ref
                        or receipt.model_call_id != prior.get("model_call_id")):
                    raise ClaimExtractionError("Claim extraction result receipt has invalid lineage")
                if receipt.outcome == "failed":
                    raise ClaimExtractionError(receipt.failure_message, error_code=receipt.failure_type,
                                               failure_category=receipt.failure_category, failure_origin=receipt.failure_origin)
                proposal = self.validate_proposal(receipt.proposal, units)
            else:
                if prior is not None:
                    raise ClaimExtractionError("indeterminate Claim extraction: intent exists without result; no automatic redispatch",
                                               error_code="CLAIM_EXTRACTION_INDETERMINATE", failure_category=FailureCategory.UNKNOWN)
                package, output_limit = self._package(units=units, bindings=bindings, context=context, occurrence=occurrence)
                final_input = package.model_input.final_input_material.inline_final_input_payload
                if not isinstance(final_input, dict) or final_input.get("input_kind") != "MESSAGES":
                    raise ClaimExtractionError("Claim extraction requires exact model messages")
                request = ModelRequestSchema(
                    model_call_id="model_call_claim_" + fingerprint, task_id=task_id, run_id=lineage.run_id,
                    caller_agent="ClaimMaterializer", model_role=ModelRole.GENERAL.value, prompt="", messages=final_input["messages"],
                    temperature=0.0, max_tokens=output_limit, created_at=datetime.now(timezone.utc).isoformat(),
                    extra={"call_purpose": "claim_materialization", "response_contract": EXTRACTION_CONTRACT,
                           "attempt_scope_ref": lineage.attempt_scope_ref, "attempt_id": lineage.attempt_id,
                           "section_id": identity["section_id"], "logical_operation_id": occurrence,
                           "input_snapshot_version": lineage.input_snapshot_version,
                           "prompt_id": EXTRACTION_CONTRACT, "prompt_version": package.instruction_binding.role_instruction_version,
                           "llm_context_summary": {"managed": True, "package_id": package.package_ref.context_package_id,
                                                   "context_sha256": package.model_input.final_model_input_sha256}},
                )
                self.store.put_json(
                    {"schema_version": "claim_extraction_input_v1", "input_fingerprint": fingerprint,
                     "input_material": material, "context_package": package.model_dump(mode="json"), "model_call_id": request.model_call_id},
                    artifact_type="claim_extraction_input", schema_version="claim_extraction_input_v1",
                    created_by="claim_materialization_runtime", artifact_ref=request_ref, **identity,
                )
                if self._units(bindings, context) != units:
                    raise ClaimExtractionError("Claim extraction scope changed before model dispatch")
                checkpoint_current_execution()
                try:
                    response = self.model_gateway.generate(request)
                    if (response.model_call_id, response.task_id, response.run_id) != (request.model_call_id, task_id, lineage.run_id):
                        raise ClaimExtractionError("Claim model returned mismatched invocation identity")
                    if not response.success:
                        error = response.error
                        raise ClaimExtractionError(
                            error.message if error else (response.error_message or "Claim extraction model failed"),
                            error_code=error.error_code if error else "CLAIM_EXTRACTION_MODEL_FAILED",
                            failure_category=error.failure_category if error else FailureCategory.UNKNOWN,
                            failure_origin=response.failure_origin,
                        )
                    proposal = self.validate_proposal(response.content, units)
                except Exception as exc:
                    is_budget = isinstance(exc, (CanonicalBudgetCapacityExceeded, CanonicalBudgetDeadlineExceeded))
                    failure = ClaimExtractionResultReceipt(
                        input_fingerprint=fingerprint, request_ref=request_ref, model_call_id=request.model_call_id,
                        outcome="failed", failure_type=getattr(exc, "error_code", type(exc).__name__),
                        failure_message=str(exc) or type(exc).__name__,
                        failure_category=FailureCategory.RESOURCE_EXHAUSTED if is_budget else classify_failure(exc),
                        failure_origin=FailureOrigin.CANONICAL_BUDGET if is_budget else getattr(exc, "failure_origin", FailureOrigin.UNKNOWN),
                    )
                    self.store.put_json(failure.model_dump(mode="json"), artifact_type="claim_extraction_result",
                                        schema_version="claim_extraction_result_v1", created_by="claim_materialization_runtime",
                                        artifact_ref=result_ref, **identity)
                    raise
                if self._units(bindings, context) != units:
                    raise ClaimExtractionError("Claim extraction scope changed during model execution")
                checkpoint_current_execution()
                receipt = ClaimExtractionResultReceipt(
                    input_fingerprint=fingerprint, request_ref=request_ref, model_call_id=request.model_call_id,
                    outcome="completed", proposal=proposal, token_usage=response.token_usage.model_dump(mode="json"),
                )
                self.store.put_json(receipt.model_dump(mode="json"), artifact_type="claim_extraction_result",
                                    schema_version="claim_extraction_result_v1", created_by="claim_materialization_runtime",
                                    artifact_ref=result_ref, **identity)
            if self._units(bindings, context) != units:
                raise ClaimExtractionError("Claim scope changed before canonical materialization")
            candidates, accounting = self.materialize_proposal(
                proposal=proposal, units=units, bindings=bindings, occurrence=occurrence, capture_ref=result_ref,
                section_id=identity["section_id"], run_id=lineage.run_id,
            )
            return self.claim_runtime.commit_claim_records(candidates, accounting, context)

    def materialize_proposal(self, *, proposal, units, bindings, occurrence, capture_ref, section_id, run_id):
        outputs = {item.unit_id: item for item in proposal.units}
        candidates, coverage_units = [], []
        for unit, binding in zip(units, bindings):
            output = outputs[unit["unit_id"]]
            unit_claims = []
            for claim in output.claims:
                start, end = exact_quote_offsets(unit["text"], claim.quote, claim.occurrence_index)
                unit_claims.append(self.materializer.build_candidate(
                    run_id=run_id, section_id=section_id, paragraph_version_ref=binding.version_ref,
                    paragraph_text=unit["text"], claim_type=claim.claim_type, start_offset=start, end_offset=end,
                    normalized_value=claim.quote, unit=None, extraction_origin=ClaimExtractionOrigin.SEMANTIC_EXTRACTOR,
                    extraction_occurrence_ref=occurrence, span_locator_policy_id="exact-quote-occurrence",
                    span_locator_policy_version="v1", normalization_policy_id="literal-claim-text", normalization_policy_version="v1",
                    claim_identity_policy_id="canonical-claim-occurrence", claim_identity_policy_version="v1",
                    candidate_capture_ref=capture_ref,
                ))
            candidates.extend(unit_claims)
            coverage_units.append(ArtifactClaimCoverageUnitSchema(
                paragraph_version_ref=binding.version_ref, coverage_unit_id=unit["unit_id"],
                start_offset=0, end_offset=len(unit["text"]), accounting_state=output.accounting_state,
                claim_occurrence_refs=tuple(item.claim_occurrence_ref for item in unit_claims),
                accounting_reason_ref=capture_ref,
            ))
        states = {item.accounting_state for item in coverage_units}
        state = (ClaimCoverageAccountingState.INDETERMINATE if ClaimCoverageAccountingState.INDETERMINATE in states
                 else ClaimCoverageAccountingState.FACTUAL_CLAIMS_MATERIALIZED if ClaimCoverageAccountingState.FACTUAL_CLAIMS_MATERIALIZED in states
                 else ClaimCoverageAccountingState.NO_APPLICABLE_FACTUAL_CLAIM)
        accounting = ClaimCoverageAccountingSchema(
            coverage_scope_ref=f"section:{section_id}", paragraph_version_refs=tuple(item.version_ref for item in bindings),
            coverage_units=tuple(coverage_units), accounting_state=state, extraction_occurrence_ref=occurrence,
            coverage_semantic_contract_version=COVERAGE_CONTRACT,
        )
        return tuple(candidates), accounting
