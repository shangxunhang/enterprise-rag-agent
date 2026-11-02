"""Initial Planner fact requirements over existing Context/Gateway/persistence.

The model returns a proposal, never facts or a committed Plan. Immutable input
and candidate receipts are invocation evidence; SectionPlanRuntime remains the
only owner of the accepted Plan. One request covers all declared sections.
"""
from __future__ import annotations

from datetime import datetime, timezone
import json
from threading import RLock
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from apps.enterprise_document.agents.agent_rag_v1.base import AgentReasonerFailure
from apps.enterprise_document.schemas.agent_rag_v1 import (
    AgentExecutionContextSchema, WorkflowErrorCategory, WorkflowErrorSchema,
    WorkflowErrorScope, WorkflowErrorSource, WorkflowRetryability,
)
from apps.enterprise_document.schemas.agent_rag_v1.facts import FactSnapshotBindingSchema
from apps.enterprise_document.schemas.agent_rag_v1.lifecycle import (
    ActiveExecutionLineageSchema, AttemptLifecycleStatus, RunLifecycleStatus,
)
from apps.enterprise_document.schemas.project_input_schema import ProjectInputSchema
from apps.enterprise_document.schemas.scheme_writer.planning import (
    DocumentPlanSchema, PlannedFactRequirementSchema,
)
from apps.enterprise_document.services.project_input_normalization.fact_contract import (
    PROJECT_INPUT_FACT_FIELDS, project_input_fact_field,
)
from core.hashing import canonical_sha256
from core.persistence.interprocess_lock import InterProcessFileLock
from core.runtime.execution_control import checkpoint_current_execution
from core.token_estimator import DeterministicTokenEstimator
from model_gateway.model_contract import ModelRole
from schemas.context import (
    ContextInstructionMaterialSchema, ContextInvocationSubjectSchema,
    ContextPackageSchema, ContextPolicyBindingSchema, ContextPriorityTier,
    ContextRepresentationLevel, ContextRepresentationOptionSchema,
    ContextRolePolicySchema, ContextRoleSourceRuleSchema, ContextRoleVisibility,
    ContextSourceBindingSchema, ContextSourceCandidateSchema, ContextSourceFamily,
    ContextSourceStatusBindingSchema, InstructionBindingSchema,
    ModelContextCapabilityBindingSchema,
)
from schemas.failure import FailureOrigin
from schemas.model import ModelRequestSchema

from .budget_manager import CanonicalBudgetCapacityExceeded, CanonicalBudgetDeadlineExceeded
from .context_runtime import ApplicationContextRuntime, ContextSourceProviderRegistration
from .workflow_runtime import TargetWorkflowRuntime, WorkflowCutoverIntegrityError

RESPONSE_CONTRACT = "planner_fact_requirements_v1"
_FACT_DESCRIPTIONS = (
    "The current project's stated business goal.",
    "Confirmed staffing, department counts and organizational scale.",
    "Confirmed hardware/resources; an absence notice is not a hardware value.",
    "Work explicitly reserved for humans or external professional tools.",
    "Known missing information; this is a limitation, not an inferred value.",
)


class _StrictProposal(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class PlannerFactChoice(_StrictProposal):
    fact_key: str = Field(min_length=1)
    importance: Literal["required", "optional"]
    purpose: str | None = Field(default=None, min_length=1, max_length=300)


class PlannerSectionFactChoices(_StrictProposal):
    section_id: str = Field(min_length=1)
    fact_requirements: tuple[PlannerFactChoice, ...]


class PlannerFactChoices(_StrictProposal):
    sections: tuple[PlannerSectionFactChoices, ...] = Field(min_length=1)


class PlannerInvocationResultReceipt(_StrictProposal):
    schema_version: Literal["planner_candidate_receipt_v1"] = "planner_candidate_receipt_v1"
    input_fingerprint: str
    request_ref: str
    model_call_id: str
    outcome: Literal["completed", "failed"]
    proposal: PlannerFactChoices | None = None
    error: WorkflowErrorSchema | None = None
    token_usage: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def _one_outcome(self):
        if self.outcome == "completed" and (self.proposal is None or self.error is not None):
            raise ValueError("completed Planner receipt requires only a proposal")
        if self.outcome == "failed" and (self.error is None or self.proposal is not None):
            raise ValueError("failed Planner receipt requires only its exact error")
        return self


def _unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"duplicate Planner JSON key: {key}")
        result[key] = value
    return result


def validate_planner_fact_choices(value: Any, plan: DocumentPlanSchema) -> PlannerFactChoices:
    if isinstance(value, str):
        value = json.loads(value, object_pairs_hook=_unique_object)
    proposal = PlannerFactChoices.model_validate(value)
    actual = [item.section_id for item in proposal.sections]
    expected = {item.section_id for item in plan.sections}
    if len(actual) != len(set(actual)) or set(actual) != expected:
        raise ValueError("Planner proposal must account for every declared section exactly once")
    for section in proposal.sections:
        keys = [item.fact_key for item in section.fact_requirements]
        if len(keys) != len(set(keys)):
            raise ValueError("duplicate Planner fact requirement key")
        for key in keys:
            project_input_fact_field(key)
    return proposal


class TargetPlannerFactRequirements:
    """Application-owned initial-planning capability, not a new Plan authority."""

    def __init__(self, *, workflow_runtime: TargetWorkflowRuntime, model_gateway: Any) -> None:
        self.workflow = workflow_runtime
        self.model_gateway = model_gateway
        self.store = workflow_runtime.section_plan_runtime.artifact_runtime.artifact_store
        self.estimator = DeterministicTokenEstimator()
        self._lock = RLock()

    @staticmethod
    def _json(value: Any) -> str:
        return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))

    @staticmethod
    def _failure(context, code, message, *, budget=False, origin=FailureOrigin.UNKNOWN,
                 category=WorkflowErrorCategory.UNRECOVERABLE_SYSTEM_ERROR):
        return AgentReasonerFailure(WorkflowErrorSchema(
            error_id=f"planner-error:{context.invocation_id}:{code}",
            source=WorkflowErrorSource.AGENT,
            category=(WorkflowErrorCategory.BUDGET_EXHAUSTED if budget else category),
            scope=WorkflowErrorScope.TASK,
            retryability=WorkflowRetryability.NOT_RETRYABLE,
            error_code=code, message=message, failure_origin=origin,
        ))

    def _sources(self, context, project_input, plan):
        if context.task_id != project_input.task_id:
            raise WorkflowCutoverIntegrityError("Planner task identity mismatch")
        scope = f"planning:{plan.document_id}"
        lineage = self.workflow.initial_planning_lineage(context.run_id, plan.document_id)
        if (lineage.attempt_id != context.attempt_id
                or lineage.input_snapshot_version != context.input_snapshot_version
                or lineage.run_status is not RunLifecycleStatus.RUNNING
                or lineage.attempt_status not in {AttemptLifecycleStatus.RUNNING, AttemptLifecycleStatus.COMPLETED}):
            raise WorkflowCutoverIntegrityError("Planner lineage is stale or not applicable")
        logical = self.workflow.fact_snapshot_runtime.logical_ref_for_scope(
            run_id=context.run_id, domain_scope_ref=f"project:{context.task_id}",
        )
        binding = self.workflow.fact_snapshot_runtime.resolve_current_binding(logical)
        if binding is None:
            raise WorkflowCutoverIntegrityError("Planner requires a committed current FactSnapshot")
        expected_snapshot = self.workflow.initial_plan_input_snapshot_version(
            document_id=plan.document_id, project_input=project_input,
            required_sections=list(project_input.generation_requirements.required_sections), fact_binding=binding,
        )
        if context.input_snapshot_version != expected_snapshot:
            raise WorkflowCutoverIntegrityError("Planner inputs no longer match the initial snapshot")
        if [item.section_title for item in plan.sections] != list(project_input.generation_requirements.required_sections):
            raise WorkflowCutoverIntegrityError("Planner skeleton differs from declared section scope")
        content = self.workflow.fact_snapshot_runtime.read_binding(binding, require_current=True)
        return binding, content, lineage

    @staticmethod
    def _task_material(project, plan):
        return {
            "task": {
                "task_type": project.task_type, "project_name": project.project_name,
                "user_query": project.user_query,
                "policy_requirements": list(project.policy_requirements),
                "manual_boundaries": [item.model_dump(mode="json") for item in project.manual_boundaries],
                "missing_information": list(project.missing_information),
                "conflicting_information": list(project.conflicting_information),
            },
            "document_title": plan.document_title,
            "section_contracts": [
                {"section_id": item.section_id, "section_title": item.section_title,
                 "section_order": item.section_order, "citation_required": item.citation_required,
                 "authored_fact_requirements": [req.model_dump(mode="json") for req in item.fact_requirements]}
                for item in plan.sections
            ],
            "fact_field_catalog": [
                {"fact_key": field.fact_key, "fact_type": field.fact_type, "description": description}
                for field, description in zip(PROJECT_INPUT_FACT_FIELDS, _FACT_DESCRIPTIONS)
            ],
        }

    @staticmethod
    def _status(family):
        payload = {"state": "CURRENT"}
        return ContextSourceStatusBindingSchema(
            status_schema_id=f"planner_{family.value.lower()}_currentness_v1",
            status_schema_version="v1", resolver_id=f"initial-planner:{family.value}",
            resolver_policy_version="v1", status_payload=payload,
            status_payload_sha256=canonical_sha256(payload), status_semantic_fingerprint=canonical_sha256(payload),
        )

    def _build_package(self, context, task_material, fact_binding, facts, lineage):
        capability = dict(self.model_gateway.routing_capabilities(model_role=ModelRole.GENERAL.value))
        output_limit = min(4096, int(capability["safe_max_output_tokens"]))
        capability_material = {**capability, "effective_max_output_tokens": output_limit}
        capability_hash = canonical_sha256(capability_material)
        model_capability = ModelContextCapabilityBindingSchema(
            model_capability_ref="model-gateway-capability://sha256/" + capability_hash,
            capability_schema_id="model_gateway_routing_capability_v1", capability_schema_version="v1",
            capability_semantic_fingerprint=capability_hash,
            model_context_window=int(capability["safe_context_window"]), safe_max_output_tokens=output_limit,
            token_estimator_id="deterministic_mixed_text", token_estimator_version="v1",
            token_estimator_semantic_fingerprint=canonical_sha256({"estimator": "deterministic_mixed_text", "version": "v1"}),
        )
        runtime = ApplicationContextRuntime(estimator=self.estimator)
        candidates = {}
        source_rows = (
            (ContextSourceFamily.FACT_SNAPSHOT, {"binding": fact_binding.model_dump(mode="json")},
             facts.model_dump(mode="json"), "fact_snapshot_binding_v1", "SIF-04:FactSnapshotRuntime"),
            (ContextSourceFamily.WORKFLOW_EXECUTION_METADATA, {"lineage": lineage.model_dump(mode="json")},
             lineage.model_dump(mode="json"), "active_execution_lineage_v1", "SIF-01:RunAttemptLifecycleService"),
        )

        def resolve(binding):
            if binding.source_family is ContextSourceFamily.FACT_SNAPSHOT:
                self.workflow.fact_snapshot_runtime.read_binding(
                    FactSnapshotBindingSchema.model_validate(binding.exact_source_ref["binding"]), require_current=True,
                )
            elif binding.source_family is ContextSourceFamily.WORKFLOW_EXECUTION_METADATA:
                expected = ActiveExecutionLineageSchema.model_validate(binding.exact_source_ref["lineage"])
                current = self.workflow.lifecycle_service.resolve_active_lineage(expected.run_id, expected.attempt_scope_ref)
                if current != expected:
                    raise WorkflowCutoverIntegrityError("Planner execution source changed before dispatch")
            else:
                raise WorkflowCutoverIntegrityError("Forbidden initial Planner source family")
            return binding

        for family, exact_ref, material, schema_id, owner in source_rows:
            binding = ContextSourceBindingSchema(
                source_family=family, exact_source_ref=exact_ref, source_schema_id=schema_id,
                source_schema_version="v1", source_semantic_fingerprint=canonical_sha256(material),
                authority_owner=owner, run_id=context.run_id, scope_ref=lineage.attempt_scope_ref,
                currentness_binding=self._status(family),
            )
            candidates[family] = (ContextSourceCandidateSchema(
                candidate_id="planner-" + family.value.lower(), source_binding=binding, title=family.value,
                representations=(ContextRepresentationOptionSchema(
                    representation_level=ContextRepresentationLevel.EXACT,
                    projection_schema_id="planner_" + family.value.lower() + "_v1", projection_schema_version="v1",
                    representation_policy_id="initial-planner-exact-input", representation_policy_version="v1",
                    materialized_payload=material, materialized_payload_sha256=canonical_sha256(material),
                    protected_field_evidence=("facts",) if family is ContextSourceFamily.FACT_SNAPSHOT else ("attempt_id",),
                ),),
            ),)
            runtime.register_source_provider(ContextSourceProviderRegistration(
                source_family=family, provider_id="initial-planner-" + family.value.lower(), provider_version="v1",
                candidate_reader=lambda _subject, f=family: candidates[f], source_binding_resolver=resolve,
                hard_eligibility_validator=lambda source, subject: (
                    source.run_id == subject.run_id and source.scope_ref == subject.consumer_scope_ref,
                    () if source.run_id == subject.run_id and source.scope_ref == subject.consumer_scope_ref else ("PLANNER_SOURCE_SCOPE_MISMATCH",),
                ),
            ))
        role = ContextRolePolicySchema(
            role_policy_id="initial-planner-fact-requirements", role_policy_version="v1",
            agent_role="PLANNER", execution_mode="INITIAL_PLAN", context_purpose="INITIAL_PLAN_FACT_REQUIREMENTS",
            source_rules=tuple(ContextRoleSourceRuleSchema(
                source_family=family, visibility=ContextRoleVisibility.MUST, mandatory=True,
                priority_tier=ContextPriorityTier.P0_MANDATORY_AUTHORITY,
                allowed_representation_levels=(ContextRepresentationLevel.EXACT,),
                minimum_representation_floor=ContextRepresentationLevel.EXACT,
            ) for family, *_ in source_rows),
        )
        policy = ContextPolicyBindingSchema(
            source_catalog_id=runtime.SOURCE_CATALOG_ID, source_catalog_version=runtime.SOURCE_CATALOG_VERSION,
            role_policy_id=role.role_policy_id, role_policy_version="v1",
            cross_section_policy_id="initial-planner-no-cross-section", cross_section_policy_version="v1",
            consumer_authorization_policy_id="initial-planner-scope", consumer_authorization_policy_version="v1",
            selection_policy_id="sif10-hierarchical-selection", selection_policy_version="v1",
            budget_policy_id="sif10-context-budget", budget_policy_version="v1",
            representation_policy_id="initial-planner-exact-input", representation_policy_version="v1",
        )
        system = "You are the initial Planner. Return only a typed planning proposal, never facts or a Gate decision."
        role_text = (
            "For each declared section select needed fact keys from fact_field_catalog using the task and current facts. "
            "Treat user_query as the document task. Treat a project business goal only as a current FactSnapshot fact; "
            "never infer a goal from user_query or duplicate a task instruction as a project fact. "
            "A needed field may be absent: absence does not remove its requirement. Do not attach every field to every section. "
            "Do not infer values, create keys, alter section identity/order, or decide generation/release eligibility. "
            "Preserve authored requirements. Use required when the field is necessary for the section, optional otherwise. "
            "Purpose is a short business use, not private reasoning. No extra keys or Markdown."
        )
        template = (
            'Return {"sections":[{"section_id":"exact supplied id","fact_requirements":'
            '[{"fact_key":"catalog key","importance":"required or optional","purpose":"brief use"}]}]}. '
            'Every section must appear exactly once; explicitly use [] where no field is needed.'
        )
        task = self._json(task_material)
        instructions = ContextInstructionMaterialSchema(
            system_instruction=system, role_instruction=role_text, prompt_template=template,
            task_output_contract=task, dynamic_control_payloads=(),
        )
        instruction_binding = InstructionBindingSchema(
            system_instruction_ref="instruction://initial-planner/system/v1", system_instruction_version="v1",
            system_instruction_sha256=canonical_sha256(system),
            role_instruction_ref="instruction://initial-planner/role/v2", role_instruction_version="v2",
            role_instruction_sha256=canonical_sha256(role_text),
            prompt_template_ref="template://initial-planner/fact-requirements/v2", prompt_template_version="v2",
            prompt_template_sha256=canonical_sha256(template),
            task_output_contract_ref="task-contract://initial-planner/" + canonical_sha256(task),
            task_output_contract_sha256=canonical_sha256(task), dynamic_control_bindings=(),
        )
        package = runtime.materialize_context_package(
            invocation_subject=ContextInvocationSubjectSchema(
                run_id=context.run_id, attempt_id=context.attempt_id, invocation_id=context.invocation_id,
                logical_operation_id=context.logical_operation_id, agent_role="PLANNER", execution_mode="INITIAL_PLAN",
                context_purpose="INITIAL_PLAN_FACT_REQUIREMENTS", consumer_scope_ref=lineage.attempt_scope_ref,
            ),
            role_policy=role, policy_binding=policy, model_capability_binding=model_capability,
            instruction_binding=instruction_binding, instruction_material=instructions,
            reserved_instruction_tokens=max(1, self.estimator.estimate(self._json(instructions.model_dump(mode="json")))),
            reserved_output_tokens=output_limit, safety_margin_tokens=64,
        )
        versions = {
            "source_catalog_version": runtime.SOURCE_CATALOG_VERSION, "role_policy_version": "v1",
            "cross_section_policy_version": "v1", "consumer_authorization_policy_version": "v1",
            "selection_policy_version": "v1", "budget_policy_version": "v1", "representation_policy_version": "v1",
            "model_capability_fingerprint": capability_hash,
            "token_estimator_fingerprint": model_capability.token_estimator_semantic_fingerprint,
        }
        return runtime.validate_pre_model_call_current(package, versions), output_limit

    def _read_optional(self, reference, identity):
        try:
            return self.store.get_json(reference, **identity)
        except FileNotFoundError:
            return None

    def enrich(self, *, context: AgentExecutionContextSchema, project_input: ProjectInputSchema,
               document_plan: DocumentPlanSchema) -> DocumentPlanSchema:
        plan = DocumentPlanSchema.model_validate(document_plan.model_dump(mode="json"))
        fact_binding, facts, lineage = self._sources(context, project_input, plan)
        task_material = self._task_material(project_input, plan)
        input_material = {
            "contract": RESPONSE_CONTRACT, "input_snapshot_version": context.input_snapshot_version,
            "task": task_material, "fact_snapshot_binding": fact_binding.model_dump(mode="json"),
        }
        fingerprint = canonical_sha256(input_material)
        key = canonical_sha256({"run_id": context.run_id, "attempt_id": context.attempt_id,
                                "logical_operation_id": context.logical_operation_id, "contract": RESPONSE_CONTRACT})
        request_ref = f"artifact://planner-fact-requirements/{key}/input"
        result_ref = f"artifact://planner-fact-requirements/{key}/candidate"
        identity = dict(task_id=context.task_id, run_id=context.run_id, attempt_id=context.attempt_id,
                        input_snapshot_version=context.input_snapshot_version)
        with self._lock, InterProcessFileLock(self.store.root / "planner_locks" / (key + ".lock")):
            checkpoint_current_execution()
            prior_input = self._read_optional(request_ref, identity)
            prior_result = self._read_optional(result_ref, identity)
            if prior_input is not None:
                if (prior_input.get("schema_version") != "planner_invocation_input_v1"
                        or prior_input.get("input_fingerprint") != fingerprint
                        or prior_input.get("model_call_id") != "model_call_planner_" + key
                        or prior_input.get("input_material") != input_material):
                    raise WorkflowCutoverIntegrityError("same Planner invocation has conflicting input")
                historical = ContextPackageSchema.model_validate(prior_input["context_package"])
                if (historical.invocation_subject.run_id != context.run_id
                        or historical.invocation_subject.attempt_id != context.attempt_id
                        or historical.invocation_subject.invocation_id != context.invocation_id
                        or historical.invocation_subject.logical_operation_id != context.logical_operation_id
                        or historical.invocation_subject.agent_role != "PLANNER"
                        or historical.invocation_subject.execution_mode != "INITIAL_PLAN"
                        or historical.invocation_subject.consumer_scope_ref != lineage.attempt_scope_ref):
                    raise WorkflowCutoverIntegrityError("Planner receipt contains another invocation's Context")
                sources = {item.source_binding.source_family: item.source_binding
                           for item in historical.candidate_decisions}
                if (set(sources) != {ContextSourceFamily.FACT_SNAPSHOT, ContextSourceFamily.WORKFLOW_EXECUTION_METADATA}
                        or sources[ContextSourceFamily.FACT_SNAPSHOT].exact_source_ref
                        != {"binding": fact_binding.model_dump(mode="json")}):
                    raise WorkflowCutoverIntegrityError("Planner receipt contains an incompatible source set")
            if prior_result is not None:
                if (prior_input is None or prior_result.get("input_fingerprint") != fingerprint
                        or prior_result.get("schema_version") != "planner_candidate_receipt_v1"
                        or prior_result.get("model_call_id") != prior_input.get("model_call_id")
                        or prior_result.get("request_ref") != request_ref):
                    raise WorkflowCutoverIntegrityError("Planner candidate receipt has invalid input lineage")
                receipt = PlannerInvocationResultReceipt.model_validate(prior_result)
                if receipt.error is not None:
                    raise AgentReasonerFailure(receipt.error)
                proposal = validate_planner_fact_choices(receipt.proposal, plan)
            else:
                if prior_input is not None:
                    raise self._failure(context, "PLANNER_EXECUTION_INDETERMINATE",
                                        "Planner input intent exists without a durable candidate; automatic redispatch is forbidden")
                self.workflow.lifecycle_service.assert_attempt_committable(
                    run_id=context.run_id, attempt_scope_ref=lineage.attempt_scope_ref,
                    attempt_id=context.attempt_id, input_snapshot_version=context.input_snapshot_version,
                )
                package, max_tokens = self._build_package(context, task_material, fact_binding, facts, lineage)
                final_input = package.model_input.final_input_material.inline_final_input_payload
                if not isinstance(final_input, dict) or final_input.get("input_kind") != "MESSAGES":
                    raise WorkflowCutoverIntegrityError("Planner requires exact inline model messages")
                req = ModelRequestSchema(
                    model_call_id="model_call_planner_" + key, task_id=context.task_id, run_id=context.run_id,
                    caller_agent="PlannerAgent", model_role=ModelRole.GENERAL.value, prompt="",
                    messages=final_input["messages"], temperature=0.0, max_tokens=max_tokens,
                    created_at=datetime.now(timezone.utc).isoformat(),
                    extra={"call_purpose": "planner_fact_requirements", "response_contract": RESPONSE_CONTRACT,
                           "logical_operation_id": context.logical_operation_id, "attempt_id": context.attempt_id,
                           "attempt_scope_ref": lineage.attempt_scope_ref,
                           "agent_invocation_id": context.invocation_id, "execution_id": context.execution_id,
                           "input_snapshot_version": context.input_snapshot_version,
                           "prompt_id": RESPONSE_CONTRACT, "prompt_version": "v1",
                           "llm_context_summary": {"managed": True, "package_id": package.package_ref.context_package_id,
                               "context_sha256": package.model_input.final_model_input_sha256}},
                )
                checkpoint_current_execution()
                self.store.put_json(
                    {"schema_version": "planner_invocation_input_v1", "input_fingerprint": fingerprint,
                     "input_material": input_material, "context_package": package.model_dump(mode="json"),
                     "model_call_id": req.model_call_id},
                    artifact_type="planner_invocation_input", schema_version="planner_invocation_input_v1",
                    created_by="initial_planner_runtime", artifact_ref=request_ref, **identity,
                )
                # Intent persistence is not permission to dispatch with changed sources.
                current_fact, _, _ = self._sources(context, project_input, plan)
                if current_fact != fact_binding:
                    raise WorkflowCutoverIntegrityError("Planner FactSnapshot changed before model dispatch")
                self.workflow.lifecycle_service.assert_attempt_committable(
                    run_id=context.run_id, attempt_scope_ref=lineage.attempt_scope_ref,
                    attempt_id=context.attempt_id, input_snapshot_version=context.input_snapshot_version,
                )
                checkpoint_current_execution()
                def persist_failure(failure):
                    checkpoint_current_execution()
                    failed = PlannerInvocationResultReceipt(
                        input_fingerprint=fingerprint, request_ref=request_ref,
                        model_call_id=req.model_call_id, outcome="failed", error=failure.workflow_error,
                    )
                    self.store.put_json(
                        failed.model_dump(mode="json"), artifact_type="planner_candidate_result",
                        schema_version="planner_candidate_receipt_v1", created_by="initial_planner_runtime",
                        artifact_ref=result_ref, **identity,
                    )
                    return failure

                try:
                    response = self.model_gateway.generate(req)
                except (CanonicalBudgetCapacityExceeded, CanonicalBudgetDeadlineExceeded) as exc:
                    raise persist_failure(self._failure(context, type(exc).__name__, str(exc), budget=True,
                                                        origin=FailureOrigin.CANONICAL_BUDGET)) from exc
                except Exception as exc:
                    raise persist_failure(self._failure(context, "PLANNER_MODEL_EXECUTION_FAILED", str(exc))) from exc
                if (response.model_call_id, response.task_id, response.run_id) != (req.model_call_id, req.task_id, req.run_id):
                    raise persist_failure(self._failure(context, "PLANNER_MODEL_IDENTITY_MISMATCH",
                                                        "Planner Model response identity mismatch"))
                if not response.success:
                    error = response.error
                    raise persist_failure(self._failure(
                        context, error.error_code if error else "PLANNER_MODEL_FAILED",
                        error.message if error else (response.error_message or "Planner model failed"),
                        origin=response.failure_origin, category=WorkflowErrorCategory.TRANSIENT_INFRASTRUCTURE,
                    ))
                try:
                    proposal = validate_planner_fact_choices(response.content, plan)
                    self.apply_choices(plan, proposal)
                except ValueError as exc:
                    raise persist_failure(self._failure(context, "PLANNER_FACT_PROPOSAL_INVALID", str(exc),
                                                        category=WorkflowErrorCategory.AGENT_OUTPUT_INVALID)) from exc
                current_fact, _, _ = self._sources(context, project_input, plan)
                if current_fact != fact_binding:
                    raise WorkflowCutoverIntegrityError("Planner FactSnapshot changed during model execution")
                self.workflow.lifecycle_service.assert_attempt_committable(
                    run_id=context.run_id, attempt_scope_ref=lineage.attempt_scope_ref,
                    attempt_id=context.attempt_id, input_snapshot_version=context.input_snapshot_version,
                )
                checkpoint_current_execution()
                self.store.put_json(
                    PlannerInvocationResultReceipt(
                        input_fingerprint=fingerprint, request_ref=request_ref, proposal=proposal,
                        model_call_id=req.model_call_id, outcome="completed",
                        token_usage=response.token_usage.model_dump(mode="json"),
                    ).model_dump(mode="json"),
                    artifact_type="planner_candidate_result", schema_version="planner_candidate_receipt_v1",
                    created_by="initial_planner_runtime", artifact_ref=result_ref, **identity,
                )
        return self.apply_choices(plan, proposal, request_ref=request_ref, result_ref=result_ref)

    @staticmethod
    def apply_choices(plan, proposal, *, request_ref="", result_ref=""):
        proposed = {item.section_id: item for item in proposal.sections}
        sections = []
        for section in plan.sections:
            requirements = {item.fact_key: item for item in section.fact_requirements}
            for choice in proposed[section.section_id].fact_requirements:
                field = project_input_fact_field(choice.fact_key)
                if choice.fact_key in requirements:
                    existing = requirements[choice.fact_key]
                    if existing.fact_type != field.fact_type or existing.importance != choice.importance:
                        raise ValueError("Planner cannot reinterpret an authored fact requirement")
                    continue
                requirements[choice.fact_key] = PlannedFactRequirementSchema(
                    requirement_id="planned-fact:" + canonical_sha256({"section_id": section.section_id, "fact_key": choice.fact_key}),
                    fact_key=choice.fact_key, fact_type=field.fact_type, importance=choice.importance, purpose=choice.purpose,
                )
            raw = section.model_dump(mode="json")
            raw["fact_requirements"] = [item.model_dump(mode="json") for item in requirements.values()]
            sections.append(raw)
        raw_plan = plan.model_dump(mode="json")
        raw_plan["sections"] = sections
        raw_plan["metadata"] = {**plan.metadata, "fact_requirements_response_contract": RESPONSE_CONTRACT,
                                "planner_input_ref": request_ref, "planner_candidate_ref": result_ref}
        return DocumentPlanSchema.model_validate(raw_plan)
