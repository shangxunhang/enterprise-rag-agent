"""346 application composition for canonical Requirement Adherence.

The application layer owns only exact input assembly, one managed physical model
call, persistence/replay and composition into existing Requirement intermediates
and Relevancy Facts. Requirement truth remains RequirementMaterializer/Store;
semantic Fact truth remains RelevancyValidator.
"""
from __future__ import annotations

from datetime import datetime, timezone
import json
from threading import RLock
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from apps.enterprise_document.schemas.agent_rag_v1.verification import ClaimOperationContextSchema
from apps.enterprise_document.schemas.post_write.foundation import (
    ExpectedItemExecutionAccountingSchema,
    StageDescriptorSchema,
    StagePlanSchema,
    ValidationCompletionItemSchema,
    ValidationCompletionItemState,
    ValidationFactRefSchema,
    ValidationIntermediateKind,
    ValidationIntermediateRefSchema,
    ValidationRunState,
    ValidatorExecutionRecordSchema,
)
from apps.enterprise_document.schemas.post_write.quality import (
    ArtifactResponseTargetSetRefSchema,
    RequirementAdherenceObservation,
    RequirementArtifactAssessmentSchema,
    RequirementDomainCompletenessState,
    SharedRequirementArtifactCandidateDomainSchema,
)
from apps.enterprise_document.schemas.post_write.requirement import RequirementRefSchema
from apps.enterprise_document.services.post_write.artifact_state import ArtifactCanonicalPayloadMaterializer
from apps.enterprise_document.services.post_write.execution import ValidatorInputEnvelopeBuilder
from apps.enterprise_document.services.post_write.relevancy import RelevancyValidator
from apps.enterprise_document.services.post_write.shared_requirement import (
    RequirementArtifactAssessmentProjector,
    RequirementBatchConsumer,
    RequirementBatchEvaluator,
    RelevancyTargetProjector,
    SharedRequirementArtifactCandidateDomainBuilder,
)
from core.hashing import canonical_sha256
from core.persistence.interprocess_lock import InterProcessFileLock
from core.retry_policy import classify_failure
from core.runtime.execution_control import checkpoint_current_execution
from core.token_estimator import DeterministicTokenEstimator
from model_gateway.model_contract import ModelRole
from schemas.context import (
    ContextInstructionMaterialSchema,
    ContextInvocationSubjectSchema,
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
    ModelContextCapabilityBindingSchema,
)
from schemas.failure import FailureCategory, FailureOrigin
from schemas.model import ModelRequestSchema

from .budget_manager import (
    CanonicalBudgetCapacityExceeded,
    CanonicalBudgetDeadlineExceeded,
    CanonicalBudgetDraining,
)
from .context_runtime import ApplicationContextRuntime, ContextSourceProviderRegistration

REQUIREMENT_ADHERENCE_CONTRACT = "requirement_adherence_batch_v1"
REQUIREMENT_ADHERENCE_POLICY_ID = "post-write-requirement-adherence"
REQUIREMENT_ADHERENCE_POLICY_VERSION = "v1"
ASSESSMENT_CONTRACT_VERSION = "requirement-artifact-assessment-v1"


class RequirementAdherenceCompositionError(ValueError):
    def __init__(
        self,
        message: str,
        *,
        error_code: str = "REQUIREMENT_ADHERENCE_FAILED",
        failure_category: FailureCategory = FailureCategory.VALIDATION,
        failure_origin: FailureOrigin = FailureOrigin.UNKNOWN,
    ) -> None:
        super().__init__(message)
        self.error_code = error_code
        self.failure_category = FailureCategory(failure_category)
        self.failure_origin = FailureOrigin(failure_origin)


class RequirementAdherenceRow(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    requirement_id: str = Field(min_length=1)
    observation: RequirementAdherenceObservation
    response_refs: tuple[str, ...] = ()
    reason_codes: tuple[str, ...] = ()

    @model_validator(mode="after")
    def _canonical(self):
        for name in ("response_refs", "reason_codes"):
            values = tuple(getattr(self, name))
            if values != tuple(sorted(set(values))):
                raise ValueError(f"{name} must be canonical ordered-unique")
        return self


class RequirementAdherenceReply(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    results: tuple[RequirementAdherenceRow, ...]

    @model_validator(mode="after")
    def _unique(self):
        ids = [item.requirement_id for item in self.results]
        if len(ids) != len(set(ids)):
            raise ValueError("Requirement Adherence reply contains duplicate requirement IDs")
        return self


class RequirementAdherenceAccounting(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    schema_version: Literal["requirement_adherence_accounting_v1"] = "requirement_adherence_accounting_v1"
    input_hash: str
    artifact_state_ref: dict[str, Any]
    requirement_ref: dict[str, Any]
    subject_hash: str
    domain_ref: dict[str, Any]
    target_refs: tuple[dict[str, Any], ...]
    assessment_refs: tuple[dict[str, Any], ...]
    fact_refs: tuple[dict[str, Any], ...]
    state: Literal["PASS", "FAIL", "INDETERMINATE", "TECHNICAL_FAILURE"]
    failure: dict[str, Any] | None = None
    input_receipt_ref: str | None = None


class TargetPostWriteRequirementAdherence:
    VALIDATOR_ID = "post_write_relevancy"
    VERSION = "v2"

    def __init__(self, *, post_write, model_gateway) -> None:
        self.post_write = post_write
        self.gateway = model_gateway
        self.store = post_write.research_runtime.artifact_runtime.artifact_store
        self.estimator = DeterministicTokenEstimator()
        self._lock = RLock()

    @staticmethod
    def stage_plan() -> StagePlanSchema:
        stage = StageDescriptorSchema(
            stage_ref="requirement-adherence-stage:v1",
            stage_version="v1",
            sequence_index=0,
            stage_input_schema_ref="schema://requirement-adherence-input/v1",
            stage_output_schema_ref="schema://requirement-adherence-accounting/v1",
            execution_policy_ref="policy://requirement-adherence/v1",
            execution_policy_version="v1",
            timeout_seconds=120,
            max_attempts=1,
        )
        base = {
            "schema_version": "validator_stage_plan_v1",
            "stage_plan_ref": "stage-plan:requirement-adherence:v1",
            "validator_id": TargetPostWriteRequirementAdherence.VALIDATOR_ID,
            "validator_version": TargetPostWriteRequirementAdherence.VERSION,
            "stage_plan_version": "v1",
            "stages": [stage.model_dump(mode="json")],
        }
        return StagePlanSchema(**base, stage_plan_hash=canonical_sha256(base))

    @staticmethod
    def _semantic_requirements(snapshot):
        allowed = {"TASK_INTENT", "POLICY_REQUIREMENT", "MANUAL_BOUNDARY"}
        values = tuple(item for item in snapshot.requirements if item.requirement_kind in allowed)
        if not any(item.requirement_id == "task-request" for item in values):
            raise RequirementAdherenceCompositionError("Requirement snapshot lacks canonical task intent")
        return tuple(sorted(values, key=lambda item: item.requirement_id))

    @staticmethod
    def _artifact_units(artifact_ref, visible: dict) -> tuple[dict[str, Any], ...]:
        def unit(*, selector: dict[str, Any], payload: dict[str, Any]) -> dict[str, Any]:
            content = {
                "payload_kind": payload.get("payload_kind"),
                "section_id": payload.get("section_id"),
                "section_title": payload.get("section_title"),
                "content": payload.get("content"),
            }
            digest = canonical_sha256({
                "artifact_state_ref": artifact_ref.model_dump(mode="json"),
                "selector": selector,
                "content": content,
            })
            return {"unit_ref": "requirement-artifact-unit://sha256/" + digest, "selector": selector, **content}

        if visible.get("payload_kind") == "section":
            return (unit(selector={"kind": "SECTION", "section_id": visible.get("section_id")}, payload=visible),)
        if visible.get("payload_kind") == "document":
            rows = visible.get("included_sections")
            if not isinstance(rows, list) or not rows:
                raise RequirementAdherenceCompositionError("Document Relevancy requires complete included Sections")
            units = []
            for index, row in enumerate(rows):
                payload = row.get("visible_payload") if isinstance(row, dict) else None
                if not isinstance(payload, dict):
                    raise RequirementAdherenceCompositionError("Document Relevancy Section projection is malformed")
                units.append(unit(selector={"kind": "DOCUMENT_SECTION", "index": index, "section_key": row.get("section_key")}, payload=payload))
            return tuple(units)
        raise RequirementAdherenceCompositionError("unsupported Artifact kind for Requirement Adherence")

    def _inputs(self, *, validation_run_id, subject_hash, artifact_ref, scope, target_ref, requirement_ref):
        snapshot = self.post_write.requirement_store.resolve_exact(requirement_ref)
        requirements = self._semantic_requirements(snapshot)
        artifact = self.post_write.artifact_store.resolve_exact(artifact_ref)
        visible = ArtifactCanonicalPayloadMaterializer.business_payload(artifact.canonical_payload_ref)
        units = self._artifact_units(artifact_ref, visible)
        material = {
            "contract": REQUIREMENT_ADHERENCE_CONTRACT,
            "policy_id": REQUIREMENT_ADHERENCE_POLICY_ID,
            "policy_version": REQUIREMENT_ADHERENCE_POLICY_VERSION,
            "validation_run_id": validation_run_id,
            "subject_hash": subject_hash,
            "validation_scope": scope.value,
            "target_ref": target_ref,
            "artifact_state_ref": artifact_ref.model_dump(mode="json"),
            "requirement_ref": requirement_ref.model_dump(mode="json"),
            "requirement_hash": snapshot.requirement_hash,
            "requirements": [item.model_dump(mode="json") for item in requirements],
            "artifact_units": list(units),
        }
        return material, snapshot, requirements, units

    def _identity(self, source_sections):
        if not source_sections:
            raise RequirementAdherenceCompositionError("Requirement Adherence requires exact execution lineage")
        lifecycle = self.post_write.lifecycle_service
        if lifecycle is None:
            raise RequirementAdherenceCompositionError("Requirement Adherence requires canonical lifecycle owner")
        lineages = [lifecycle.resolve_active_lineage(
            row.active_execution_lineage.run_id,
            row.active_execution_lineage.attempt_scope_ref,
        ) for row in source_sections]
        lineage = sorted(lineages, key=lambda row: row.attempt_scope_ref)[0]
        identity = {
            "task_id": lifecycle.load_run_identity(lineage.run_id).task_id,
            "run_id": lineage.run_id,
            "section_id": lineage.attempt_scope_ref.removeprefix("section:"),
        }
        return identity, lineage

    def _package(self, material, lineage, occurrence):
        capability = dict(self.gateway.routing_capabilities(model_role=ModelRole.GENERAL.value))
        output_limit = min(2048, int(capability["safe_max_output_tokens"]))
        cap_hash = canonical_sha256({**capability, "effective_max_output_tokens": output_limit})
        token_hash = canonical_sha256({"estimator": "deterministic_mixed_text", "version": "v1"})
        cap = ModelContextCapabilityBindingSchema(
            model_capability_ref="model-gateway-capability://sha256/" + cap_hash,
            capability_schema_id="model_gateway_routing_capability_v1",
            capability_schema_version="v1",
            capability_semantic_fingerprint=cap_hash,
            model_context_window=int(capability["safe_context_window"]),
            safe_max_output_tokens=output_limit,
            token_estimator_id="deterministic_mixed_text",
            token_estimator_version="v1",
            token_estimator_semantic_fingerprint=token_hash,
        )
        runtime = ApplicationContextRuntime(estimator=self.estimator)
        artifact_payload = {
            "artifact_state_ref": material["artifact_state_ref"],
            "artifact_units": material["artifact_units"],
        }
        stamp = {"state": "CURRENT"}
        source = ContextSourceBindingSchema(
            source_family=ContextSourceFamily.PARAGRAPH_ARTIFACT,
            exact_source_ref={
                "artifact_state_ref": material["artifact_state_ref"],
                "artifact_units_sha256": canonical_sha256(material["artifact_units"]),
            },
            source_schema_id="requirement_adherence_artifact_v1",
            source_schema_version="v1",
            source_semantic_fingerprint=canonical_sha256(artifact_payload),
            authority_owner="POST_WRITE/ARTIFACT",
            run_id=lineage.run_id,
            scope_ref=lineage.attempt_scope_ref,
            currentness_binding=ContextSourceStatusBindingSchema(
                status_schema_id="requirement_adherence_currentness_v1",
                status_schema_version="v1",
                resolver_id=REQUIREMENT_ADHERENCE_POLICY_ID,
                resolver_policy_version="v1",
                status_payload=stamp,
                status_payload_sha256=canonical_sha256(stamp),
                status_semantic_fingerprint=canonical_sha256(stamp),
            ),
        )
        candidate = ContextSourceCandidateSchema(
            candidate_id="requirement-adherence-artifact",
            source_binding=source,
            title="Exact Artifact domain",
            representations=(ContextRepresentationOptionSchema(
                representation_level=ContextRepresentationLevel.EXACT,
                projection_schema_id="requirement_adherence_artifact_v1",
                projection_schema_version="v1",
                representation_policy_id=REQUIREMENT_ADHERENCE_POLICY_ID,
                representation_policy_version="v1",
                materialized_payload=artifact_payload,
                materialized_payload_sha256=canonical_sha256(artifact_payload),
                protected_field_evidence=tuple(artifact_payload),
            ),),
        )
        runtime.register_source_provider(ContextSourceProviderRegistration(
            source_family=ContextSourceFamily.PARAGRAPH_ARTIFACT,
            provider_id=REQUIREMENT_ADHERENCE_POLICY_ID,
            provider_version="v1",
            candidate_reader=lambda _subject: (candidate,),
            source_binding_resolver=lambda value: value,
            hard_eligibility_validator=lambda value, subject: (
                value.run_id == subject.run_id and value.scope_ref == subject.consumer_scope_ref,
                () if value.run_id == subject.run_id and value.scope_ref == subject.consumer_scope_ref else ("REQUIREMENT_ARTIFACT_SCOPE_MISMATCH",),
            ),
        ))
        policy = ContextPolicyBindingSchema(
            source_catalog_id=runtime.SOURCE_CATALOG_ID,
            source_catalog_version=runtime.SOURCE_CATALOG_VERSION,
            role_policy_id=REQUIREMENT_ADHERENCE_POLICY_ID,
            role_policy_version="v1",
            cross_section_policy_id="requirement-exact-artifact-domain",
            cross_section_policy_version="v1",
            consumer_authorization_policy_id="canonical-requirement-ref",
            consumer_authorization_policy_version="v1",
            selection_policy_id="sif10-hierarchical-selection",
            selection_policy_version="v1",
            budget_policy_id="sif10-context-budget",
            budget_policy_version="v1",
            representation_policy_id=REQUIREMENT_ADHERENCE_POLICY_ID,
            representation_policy_version="v1",
        )
        system = "Evaluate whether the exact Artifact satisfies each supplied canonical Requirement. Do not decide Evidence support, citation correctness, coverage presence, correction or release."
        role = "Judge each requirement against the complete supplied Artifact domain. Preserve negation, scope, quantity, conditions and uncertainty. Composite requirements pass only when their complete authored meaning is satisfied. Use INDETERMINATE when semantics cannot be resolved. Never omit a requirement and never invent requirements or Artifact facts."
        template = 'Return only JSON {"results":[{"requirement_id":"exact id","observation":"SATISFIED|VIOLATED|INDETERMINATE","response_refs":["exact unit_ref"],"reason_codes":[]}]}. Exactly one result per supplied requirement. response_refs must be a subset of supplied Artifact unit refs. No extra keys.'
        output_contract = json.dumps({
            "contract": REQUIREMENT_ADHERENCE_CONTRACT,
            "allowed_observations": [item.value for item in RequirementAdherenceObservation],
        }, sort_keys=True)
        requirement_payload = json.dumps({
            "requirement_ref": material["requirement_ref"],
            "requirement_hash": material["requirement_hash"],
            "requirements": material["requirements"],
        }, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        control_ref = "requirement-batch://sha256/" + canonical_sha256(requirement_payload)
        instructions = ContextInstructionMaterialSchema(
            system_instruction=system,
            role_instruction=role,
            prompt_template=template,
            task_output_contract=output_contract,
            dynamic_control_payloads=((control_ref, requirement_payload),),
        )
        binding = InstructionBindingSchema(
            system_instruction_ref="instruction://requirement-adherence/system/v1",
            system_instruction_version="v1",
            system_instruction_sha256=canonical_sha256(system),
            role_instruction_ref="instruction://requirement-adherence/role/v1",
            role_instruction_version="v1",
            role_instruction_sha256=canonical_sha256(role),
            prompt_template_ref="template://requirement-adherence/v1",
            prompt_template_version="v1",
            prompt_template_sha256=canonical_sha256(template),
            task_output_contract_ref="contract://requirement-adherence/v1",
            task_output_contract_sha256=canonical_sha256(output_contract),
            dynamic_control_bindings=(DynamicControlBindingSchema(
                control_ref=control_ref,
                control_version="v1",
                control_sha256=canonical_sha256(requirement_payload),
            ),),
        )
        rule = ContextRoleSourceRuleSchema(
            source_family=ContextSourceFamily.PARAGRAPH_ARTIFACT,
            visibility=ContextRoleVisibility.MUST,
            mandatory=True,
            priority_tier=ContextPriorityTier.P0_MANDATORY_AUTHORITY,
            allowed_representation_levels=(ContextRepresentationLevel.EXACT,),
            minimum_representation_floor=ContextRepresentationLevel.EXACT,
        )
        package = runtime.materialize_context_package(
            invocation_subject=ContextInvocationSubjectSchema(
                run_id=lineage.run_id,
                attempt_id=lineage.attempt_id,
                invocation_id=occurrence,
                logical_operation_id=occurrence,
                agent_role="REQUIREMENT_ADHERENCE",
                execution_mode="EVALUATE",
                context_purpose="REQUIREMENT_ARTIFACT_ADHERENCE",
                consumer_scope_ref=lineage.attempt_scope_ref,
            ),
            role_policy=ContextRolePolicySchema(
                role_policy_id=REQUIREMENT_ADHERENCE_POLICY_ID,
                role_policy_version="v1",
                agent_role="REQUIREMENT_ADHERENCE",
                execution_mode="EVALUATE",
                context_purpose="REQUIREMENT_ARTIFACT_ADHERENCE",
                source_rules=(rule,),
            ),
            policy_binding=policy,
            model_capability_binding=cap,
            instruction_binding=binding,
            instruction_material=instructions,
            reserved_instruction_tokens=max(1, self.estimator.estimate(json.dumps(instructions.model_dump(mode="json")))),
            reserved_output_tokens=output_limit,
            safety_margin_tokens=64,
        )
        package = runtime.validate_pre_model_call_current(package, {
            "source_catalog_version": runtime.SOURCE_CATALOG_VERSION,
            "role_policy_version": "v1",
            "cross_section_policy_version": "v1",
            "consumer_authorization_policy_version": "v1",
            "selection_policy_version": "v1",
            "budget_policy_version": "v1",
            "representation_policy_version": "v1",
            "model_capability_fingerprint": cap_hash,
            "token_estimator_fingerprint": token_hash,
        })
        return package, output_limit

    @staticmethod
    def parse_reply(raw, material):
        payload = json.loads(raw) if isinstance(raw, str) else raw
        if isinstance(payload, dict) and isinstance(payload.get("results"), (list, tuple)):
            rows = []
            for row in payload["results"]:
                refs = row.get("response_refs") if isinstance(row, dict) else None
                if (
                    isinstance(refs, (list, tuple))
                    and all(isinstance(ref, str) for ref in refs)
                    and len(refs) == len(set(refs))
                ):
                    # Preserve the exact set; duplicates, types and domain
                    # membership remain the strict validators' responsibility.
                    row = {**row, "response_refs": sorted(refs)}
                rows.append(row)
            payload = {**payload, "results": rows}
        reply = RequirementAdherenceReply.model_validate(payload)
        expected = {item["requirement_id"] for item in material["requirements"]}
        actual = {item.requirement_id for item in reply.results}
        if actual != expected or len(reply.results) != len(expected):
            raise RequirementAdherenceCompositionError("Requirement Adherence reply omitted or added requirement")
        units = {item["unit_ref"] for item in material["artifact_units"]}
        for item in reply.results:
            if not set(item.response_refs) <= units:
                raise RequirementAdherenceCompositionError("Requirement Adherence response refs escape exact Artifact domain")
        return tuple(sorted(reply.results, key=lambda item: item.requirement_id))

    def _evaluate(self, material, identity, lineage, validate_current):
        key = canonical_sha256(material)
        intent_ref = f"artifact://requirement-adherence/{key}/input"
        result_ref = f"artifact://requirement-adherence/{key}/result"

        def read(ref):
            try:
                return self.store.get_json(ref, **identity)
            except FileNotFoundError:
                return None

        with self._lock, InterProcessFileLock(self.store.root / "requirement_adherence_locks" / (key + ".lock")):
            prior, result = read(intent_ref), read(result_ref)
            if prior is not None and (prior.get("input_hash") != key or prior.get("input_material") != material):
                raise RequirementAdherenceCompositionError("Requirement Adherence immutable intent differs")
            if result is not None:
                if prior is None or result.get("input_hash") != key:
                    raise RequirementAdherenceCompositionError("Requirement Adherence result has no exact intent")
                if result.get("outcome") == "failed":
                    failure = result.get("failure") or {}
                    if failure.get("kind") == "CANONICAL_BUDGET_FAILURE":
                        raise RequirementAdherenceCompositionError(
                            failure.get("message") or "canonical Budget failure",
                            error_code=failure.get("error_code") or "REQUIREMENT_BUDGET_FAILED",
                            failure_category=failure.get("category") or FailureCategory.RESOURCE_EXHAUSTED,
                            failure_origin=failure.get("origin") or FailureOrigin.CANONICAL_BUDGET,
                        )
                    return None, intent_ref, failure
                if result.get("outcome") != "completed":
                    raise RequirementAdherenceCompositionError("Requirement Adherence result outcome is invalid")
                validate_current()
                return self.parse_reply(result["candidate"], material), intent_ref, None
            if prior is not None:
                raise RequirementAdherenceCompositionError("Requirement Adherence intent without result; no automatic redispatch")

            validate_current()
            self.post_write.lifecycle_service.assert_attempt_committable(
                run_id=lineage.run_id,
                attempt_scope_ref=lineage.attempt_scope_ref,
                attempt_id=lineage.attempt_id,
                input_snapshot_version=lineage.input_snapshot_version,
            )
            occurrence = "requirement-adherence-call://sha256/" + key
            package, limit = self._package(material, lineage, occurrence)
            request = ModelRequestSchema(
                model_call_id="model_call_requirement_adherence_" + key,
                task_id=identity["task_id"],
                run_id=identity["run_id"],
                caller_agent="RequirementAdherenceEvaluator",
                model_role=ModelRole.GENERAL.value,
                prompt="",
                messages=package.model_input.final_input_material.inline_final_input_payload["messages"],
                max_tokens=limit,
                temperature=0.0,
                created_at=datetime.now(timezone.utc).isoformat(),
                extra={
                    "call_purpose": "requirement_adherence",
                    "response_contract": REQUIREMENT_ADHERENCE_CONTRACT,
                    "section_id": identity["section_id"],
                    "attempt_scope_ref": lineage.attempt_scope_ref,
                    "attempt_id": lineage.attempt_id,
                    "input_snapshot_version": lineage.input_snapshot_version,
                    "logical_operation_id": occurrence,
                    "prompt_id": REQUIREMENT_ADHERENCE_CONTRACT,
                    "prompt_version": "v1",
                    "llm_context_summary": {
                        "managed": True,
                        "package_id": package.package_ref.context_package_id,
                        "context_sha256": package.model_input.final_model_input_sha256,
                    },
                },
            )
            self.store.put_json(
                {
                    "schema_version": "requirement_adherence_input_v1",
                    "input_hash": key,
                    "input_material": material,
                    "context_package": package.model_dump(mode="json"),
                    "request": request.model_dump(mode="json"),
                },
                artifact_ref=intent_ref,
                artifact_type="requirement_adherence_input",
                schema_version="requirement_adherence_input_v1",
                created_by=REQUIREMENT_ADHERENCE_POLICY_ID,
                **identity,
            )
            try:
                checkpoint_current_execution()
                validate_current()
                response = self.gateway.generate(request)
                if (response.model_call_id, response.task_id, response.run_id) != (
                    request.model_call_id, request.task_id, request.run_id
                ):
                    raise RequirementAdherenceCompositionError("Requirement Adherence provider identity mismatch")
                if not response.success:
                    error = response.error
                    raise RequirementAdherenceCompositionError(
                        error.message if error else (response.error_message or "Requirement Adherence provider failed"),
                        error_code=error.error_code if error else "REQUIREMENT_ADHERENCE_PROVIDER_FAILED",
                        failure_category=error.failure_category if error else FailureCategory.UNKNOWN,
                        failure_origin=response.failure_origin,
                    )
                rows = self.parse_reply(response.content, material)
                validate_current()
                result = {
                    "schema_version": "requirement_adherence_result_v1",
                    "input_hash": key,
                    "outcome": "completed",
                    "candidate": {"results": [item.model_dump(mode="json") for item in rows]},
                }
                failure = None
            except Exception as exc:
                budget = isinstance(exc, (CanonicalBudgetCapacityExceeded, CanonicalBudgetDeadlineExceeded, CanonicalBudgetDraining))
                category = FailureCategory.RESOURCE_EXHAUSTED if budget else classify_failure(exc)
                origin = FailureOrigin.CANONICAL_BUDGET if budget else FailureOrigin(getattr(exc, "failure_origin", FailureOrigin.UNKNOWN))
                failure = {
                    "schema_version": "requirement_adherence_failure_v1",
                    "kind": "CANONICAL_BUDGET_FAILURE" if budget else "REQUIREMENT_ADHERENCE_TECHNICAL_FAILURE",
                    "error_type": type(exc).__name__,
                    "error_code": getattr(exc, "error_code", type(exc).__name__),
                    "message": str(exc),
                    "category": category.value,
                    "origin": origin.value,
                }
                result = {
                    "schema_version": "requirement_adherence_result_v1",
                    "input_hash": key,
                    "outcome": "failed",
                    "failure": failure,
                }
                rows = None
            self.store.put_json(
                result,
                artifact_ref=result_ref,
                artifact_type="requirement_adherence_result",
                schema_version="requirement_adherence_result_v1",
                created_by=REQUIREMENT_ADHERENCE_POLICY_ID,
                **identity,
            )
            if failure and failure["kind"] == "CANONICAL_BUDGET_FAILURE":
                raise RequirementAdherenceCompositionError(
                    failure["message"],
                    error_code=failure["error_code"],
                    failure_category=failure["category"],
                    failure_origin=failure["origin"],
                )
            return rows, intent_ref, failure

    def complete_item(
        self,
        *,
        validation_run_id,
        subject_hash,
        artifact_ref,
        scope,
        target_ref,
        requirement_ref: RequirementRefSchema,
        source_sections,
    ) -> ValidationCompletionItemSchema:
        pw = self.post_write
        material, snapshot, requirements, units = self._inputs(
            validation_run_id=validation_run_id,
            subject_hash=subject_hash,
            artifact_ref=artifact_ref,
            scope=scope,
            target_ref=target_ref,
            requirement_ref=requirement_ref,
        )
        item_ref = "relevancy:item:" + artifact_ref.artifact_hash
        input_hash = canonical_sha256(material)
        provenance = (
            "requirement-adherence-input://sha256/" + input_hash,
            requirement_ref.requirement_snapshot_id,
        )
        plan = self.stage_plan()
        envelope = ValidatorInputEnvelopeBuilder(validation_view_resolver=pw.validation_view_resolver).build(
            validation_run_id=validation_run_id,
            subject_hash=subject_hash,
            artifact_state_ref=artifact_ref,
            stage_plan=plan,
            applicable_expected_validation_item_refs=(item_ref,),
            exact_authority_refs=provenance,
            execution_contract_version="validator-execution-v1",
        )
        execution_ref = "validator-execution:requirement-adherence:" + canonical_sha256({
            "input_hash": envelope.input_hash,
            "item_ref": item_ref,
        })
        record = pw.validation_store.find_validator_execution_for_item(
            validation_run_id=validation_run_id,
            validator_id=self.VALIDATOR_ID,
            expected_item_ref=item_ref,
        )
        identity, lineage = self._identity(source_sections)
        if record is not None:
            if (
                record.validator_execution_ref != execution_ref
                or record.input_hash != envelope.input_hash
                or record.validator_version != self.VERSION
                or record.subject_hash != subject_hash
                or record.stage_plan_ref != plan.stage_plan_ref
                or record.stage_plan_version != plan.stage_plan_version
                or len(record.intermediate_refs) != 1
                or len(record.expected_item_accounting) != 1
            ):
                raise RequirementAdherenceCompositionError("committed Relevancy execution identity/version differs")
            accounting = pw.validation_intermediate_runtime.resolve_typed(
                record.intermediate_refs[0], RequirementAdherenceAccounting
            )
            self._verify_accounting(accounting, material, identity)
            refs = tuple(ValidationFactRefSchema.model_validate(item) for item in accounting.fact_refs)
            if refs != record.produced_fact_refs or record.expected_item_accounting[0].fact_refs != refs:
                raise RequirementAdherenceCompositionError("committed Relevancy Fact refs differ")
            return ValidationCompletionItemSchema(
                expected_validation_item_ref=item_ref,
                required=True,
                state=accounting.state,
                fact_refs=refs,
                technical_failure_ref=accounting.failure,
            )

        run = pw.validation_store.resolve_run_exact(validation_run_id)
        if run.execution_state is ValidationRunState.COMPLETED:
            raise RequirementAdherenceCompositionError("terminal ValidationRun is missing committed Relevancy execution")
        with pw.database.read_transaction() as connection:
            pending = connection.execute(
                "SELECT event_id FROM validation_execution_events WHERE validation_run_id=? "
                "AND event_kind='INTERMEDIATE_COMMITTED' AND json_extract(event_json,'$.payload.target_ref')=?",
                (validation_run_id, item_ref),
            ).fetchone()
        if pending:
            raise RequirementAdherenceCompositionError("incomplete Relevancy commit requires exact recovery; no redispatch")

        unit_refs = tuple(sorted(item["unit_ref"] for item in units))
        domain_builder = SharedRequirementArtifactCandidateDomainBuilder(pw.validation_intermediate_runtime)
        domain = domain_builder.build(
            artifact_state_ref=artifact_ref,
            validation_scope=scope.value,
            subject_selector_ref=target_ref,
            complete_applicable_domain_refs=unit_refs,
            prioritized_candidate_refs=unit_refs,
            consumer_requirements=tuple(item.requirement_id for item in requirements),
            completeness_state=RequirementDomainCompletenessState.COMPLETE,
            provenance_refs=provenance,
        )
        domain_ref = domain_builder.commit(validation_run_id=validation_run_id, domain=domain)
        target_projector = RelevancyTargetProjector(pw.validation_intermediate_runtime)
        targets = []
        target_refs = []
        consumers = []
        for requirement in requirements:
            target = target_projector.project(
                requirement_ref=requirement_ref,
                requirement=requirement,
                artifact_state_ref=artifact_ref,
                candidate_domain=domain,
                response_unit_refs=unit_refs,
            )
            target_ref_record = target_projector.commit(validation_run_id=validation_run_id, target_set=target)
            targets.append(target)
            target_refs.append(target_ref_record)
            consumers.append(RequirementBatchConsumer(
                requirement_ref=requirement_ref,
                requirement=requirement,
                artifact_state_ref=artifact_ref,
                candidate_domain=domain,
                relevancy_target=target,
            ))

        def validate_current():
            current, current_snapshot, current_requirements, current_units = self._inputs(
                validation_run_id=validation_run_id,
                subject_hash=subject_hash,
                artifact_ref=artifact_ref,
                scope=scope,
                target_ref=target_ref,
                requirement_ref=requirement_ref,
            )
            if current != material or current_snapshot != snapshot or current_requirements != requirements or current_units != units:
                raise RequirementAdherenceCompositionError("Requirement/Artifact sources changed from frozen input")

        rows, intent_ref, failure = self._evaluate(material, identity, lineage, validate_current)
        facts = []
        assessment_refs = []
        if failure is None:
            by_id = {item.requirement_id: item for item in rows}
            assessment_projector = RequirementArtifactAssessmentProjector(pw.validation_intermediate_runtime)
            physical_ref = "physical-model-call://" + canonical_sha256({"input_hash": input_hash, "intent_ref": intent_ref})
            evaluator = RequirementBatchEvaluator(
                projector=assessment_projector,
                evaluator=lambda consumer: {
                    "adherence_observation": by_id[consumer.requirement.requirement_id].observation,
                    "semantic_presence_observation": None,
                    "response_refs": by_id[consumer.requirement.requirement_id].response_refs,
                    "presence_refs": (),
                    "observed_count": None,
                    "reason_codes": by_id[consumer.requirement.requirement_id].reason_codes,
                    "physical_model_call_ref": physical_ref,
                    "logical_consumer_binding_refs": (
                        "requirement-adherence-consumer://" + consumer.requirement.requirement_id,
                    ),
                    "provenance_refs": provenance + (intent_ref,),
                },
            )
            assessments = evaluator.evaluate(
                validation_run_id=validation_run_id,
                consumers=tuple(consumers),
                assessment_contract_version=ASSESSMENT_CONTRACT_VERSION,
            )
            assessment_by_id = {item.requirement_id: item for item in assessments}
            for requirement, target in zip(requirements, targets):
                assessment = assessment_by_id[requirement.requirement_id]
                assessment_ref = assessment_projector.commit(assessment)
                assessment_refs.append(assessment_ref)
                facts.append(RelevancyValidator.project(
                    validation_run_id=validation_run_id,
                    subject_hash=subject_hash,
                    target_set=target,
                    assessment=assessment,
                ))

        fact_refs = tuple(pw.validation_store.put_fact_if_absent_or_same(item) for item in facts)
        states = {item.semantic_state.value for item in facts}
        state = (
            "TECHNICAL_FAILURE" if failure is not None
            else "FAIL" if "FAIL" in states
            else "INDETERMINATE" if "INDETERMINATE" in states
            else "PASS"
        )
        accounting = RequirementAdherenceAccounting(
            input_hash=input_hash,
            artifact_state_ref=artifact_ref.model_dump(mode="json"),
            requirement_ref=requirement_ref.model_dump(mode="json"),
            subject_hash=subject_hash,
            domain_ref=domain_ref.model_dump(mode="json"),
            target_refs=tuple(item.model_dump(mode="json") for item in target_refs),
            assessment_refs=tuple(item.model_dump(mode="json") for item in assessment_refs),
            fact_refs=tuple(item.model_dump(mode="json") for item in fact_refs),
            state=state,
            failure=failure,
            input_receipt_ref=intent_ref,
        )
        self._verify_accounting(accounting, material, identity)
        checkpoint_current_execution()
        intermediate = pw.validation_intermediate_runtime.commit(
            validation_run_id=validation_run_id,
            producer_ref=self.VALIDATOR_ID,
            target_ref=item_ref,
            typed_intermediate=accounting,
            intermediate_kind=ValidationIntermediateKind.TARGET_ACCOUNTING,
            occurred_at_epoch=0.0,
        )
        with pw.database.read_transaction() as connection:
            sequence = pw.validation_store._next_event_sequence(connection, validation_run_id)
        event = pw.validation_store._event(
            validation_run_id=validation_run_id,
            event_id="validator-terminal:requirement-adherence:" + canonical_sha256(execution_ref),
            sequence=sequence,
            event_kind="VALIDATOR_TECHNICAL_FAILURE" if failure else "VALIDATOR_COMPLETED",
            payload={"validator_execution_ref": execution_ref},
            occurred_at_epoch=0.0,
        )
        item_accounting = ExpectedItemExecutionAccountingSchema(
            expected_validation_item_ref=item_ref,
            execution_state="TECHNICAL_FAILURE" if failure else "COMPLETED",
            fact_refs=fact_refs,
            technical_failure_ref=failure,
            provenance_refs=provenance,
        )
        base = {
            "schema_version": "validator_execution_record_v1",
            "validator_execution_ref": execution_ref,
            "validation_run_id": validation_run_id,
            "subject_hash": subject_hash,
            "validator_id": self.VALIDATOR_ID,
            "validator_version": self.VERSION,
            "stage_plan_ref": plan.stage_plan_ref,
            "stage_plan_version": plan.stage_plan_version,
            "input_hash": envelope.input_hash,
            "terminal_event_id": event.event_id,
            "terminal_execution_state": "TECHNICAL_FAILURE" if failure else "COMPLETED",
            "expected_item_accounting": [item_accounting.model_dump(mode="json")],
            "produced_fact_refs": [item.model_dump(mode="json") for item in fact_refs],
            "intermediate_refs": [intermediate.model_dump(mode="json")],
        }
        pw.validation_store.finalize_validator_execution(
            event,
            ValidatorExecutionRecordSchema(**base, record_sha256=canonical_sha256(base)),
            stage_plan=plan,
            input_envelope=envelope,
        )
        return ValidationCompletionItemSchema(
            expected_validation_item_ref=item_ref,
            required=True,
            state=state,
            fact_refs=fact_refs,
            technical_failure_ref=failure,
        )

    def _verify_accounting(self, accounting, material, identity):
        pw = self.post_write
        exact = RequirementAdherenceAccounting.model_validate(accounting)
        if (
            exact.input_hash != canonical_sha256(material)
            or exact.artifact_state_ref != material["artifact_state_ref"]
            or exact.requirement_ref != material["requirement_ref"]
            or exact.subject_hash != material["subject_hash"]
        ):
            raise RequirementAdherenceCompositionError("Relevancy accounting input binding differs")
        domain_ref = ValidationIntermediateRefSchema.model_validate(exact.domain_ref)
        domain = pw.validation_intermediate_runtime.resolve_typed(
            domain_ref, SharedRequirementArtifactCandidateDomainSchema
        )
        expected_units = {item["unit_ref"] for item in material["artifact_units"]}
        expected_requirements = {item["requirement_id"] for item in material["requirements"]}
        if (
            domain.artifact_state_ref.model_dump(mode="json") != material["artifact_state_ref"]
            or set(domain.complete_applicable_domain_refs) != expected_units
            or set(domain.prioritized_candidate_refs) != expected_units
            or set(domain.consumer_requirements) != expected_requirements
            or domain.completeness_state is not RequirementDomainCompletenessState.COMPLETE
        ):
            raise RequirementAdherenceCompositionError("Relevancy committed complete domain differs")
        targets = [pw.validation_intermediate_runtime.resolve_typed(
            ValidationIntermediateRefSchema.model_validate(raw), ArtifactResponseTargetSetRefSchema
        ) for raw in exact.target_refs]
        if {item.requirement_id for item in targets} != expected_requirements or any(
            set(item.response_unit_refs) != expected_units or item.candidate_domain_ref != domain.candidate_domain_ref
            for item in targets
        ):
            raise RequirementAdherenceCompositionError("Relevancy committed target set differs")
        facts = [pw.validation_store.resolve_fact(ValidationFactRefSchema.model_validate(raw)) for raw in exact.fact_refs]
        if exact.failure is not None:
            if facts or exact.assessment_refs or exact.state != "TECHNICAL_FAILURE":
                raise RequirementAdherenceCompositionError("technical Relevancy accounting fabricated semantic results")
            if exact.input_receipt_ref is None:
                raise RequirementAdherenceCompositionError("technical Relevancy accounting lacks exact model intent")
            receipt = self.store.get_json(exact.input_receipt_ref, **identity)
            result = self.store.get_json(exact.input_receipt_ref.removesuffix("/input") + "/result", **identity)
            if (
                receipt.get("schema_version") != "requirement_adherence_input_v1"
                or receipt.get("input_hash") != exact.input_hash
                or receipt.get("input_material") != material
                or result.get("schema_version") != "requirement_adherence_result_v1"
                or result.get("outcome") != "failed"
                or result.get("failure") != exact.failure
            ):
                raise RequirementAdherenceCompositionError("technical Relevancy receipt differs")
            return
        if exact.input_receipt_ref is None:
            raise RequirementAdherenceCompositionError("semantic Relevancy accounting lacks exact model intent")
        receipt = self.store.get_json(exact.input_receipt_ref, **identity)
        result = self.store.get_json(exact.input_receipt_ref.removesuffix("/input") + "/result", **identity)
        if (
            receipt.get("schema_version") != "requirement_adherence_input_v1"
            or receipt.get("input_hash") != exact.input_hash
            or receipt.get("input_material") != material
            or result.get("schema_version") != "requirement_adherence_result_v1"
            or result.get("outcome") != "completed"
        ):
            raise RequirementAdherenceCompositionError("semantic Relevancy receipt differs")
        rows = self.parse_reply(result.get("candidate"), material)
        row_by_id = {item.requirement_id: item for item in rows}
        assessments = [pw.validation_intermediate_runtime.resolve_typed(
            ValidationIntermediateRefSchema.model_validate(raw), RequirementArtifactAssessmentSchema
        ) for raw in exact.assessment_refs]
        if {item.requirement_id for item in assessments} != expected_requirements:
            raise RequirementAdherenceCompositionError("Relevancy assessment set differs")
        target_by_id = {item.requirement_id: item for item in targets}
        fact_by_target = {item.target_ref: item for item in facts}
        for assessment in assessments:
            row = row_by_id[assessment.requirement_id]
            target = target_by_id[assessment.requirement_id]
            if (
                assessment.adherence_observation is not row.observation
                or set(assessment.response_refs) != set(row.response_refs)
                or assessment.relevancy_projection_ref != target.target_set_ref
                or assessment.relevancy_projection_hash != target.projection_hash
                or assessment.shared_candidate_domain_ref != domain.candidate_domain_ref
                or assessment.shared_candidate_domain_hash != domain.domain_hash
            ):
                raise RequirementAdherenceCompositionError("Relevancy assessment differs from committed model result")
            projected = RelevancyValidator.project(
                validation_run_id=material["validation_run_id"],
                subject_hash=material["subject_hash"],
                target_set=target,
                assessment=assessment,
            )
            if fact_by_target.get(projected.target_ref) != projected:
                raise RequirementAdherenceCompositionError("Relevancy Fact differs from exact assessment")
        states = {item.semantic_state.value for item in facts}
        expected_state = "FAIL" if "FAIL" in states else "INDETERMINATE" if "INDETERMINATE" in states else "PASS"
        if exact.state != expected_state:
            raise RequirementAdherenceCompositionError("Relevancy completion state differs from exact Facts")


__all__ = [
    "ASSESSMENT_CONTRACT_VERSION",
    "REQUIREMENT_ADHERENCE_CONTRACT",
    "RequirementAdherenceCompositionError",
    "TargetPostWriteRequirementAdherence",
]
