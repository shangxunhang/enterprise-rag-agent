"""Initial canonical Claim verification over existing committed Claim/Support owners.

The Agent returns a candidate. This composition validates and submits it to the
existing M6 runtime; it does not rewrite claims, control Workflow or publish.
"""
from __future__ import annotations

from datetime import datetime, timezone
from contextlib import ExitStack
from copy import deepcopy
import hashlib
import json
import re
from threading import RLock
from types import SimpleNamespace
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from apps.enterprise_document.agents.agent_rag_v1.verifier import VerifierAgent
from apps.enterprise_document.schemas.agent_rag_v1.verification import (
    ClaimOperationContextSchema, VerificationVerdict, VerifierClaimInputSchema,
    VerifierProjectInputBasisV1, project_input_basis_ref, authority_universe_sha256,
    build_verifier_semantic_input_sha256, VERIFIER_POLICY_ID, VERIFIER_POLICY_VERSION,
    VERIFIER_RESPONSE_CONTRACT, _canonical_refs,
    VerifierSemanticIssueCandidateSchema, VerifierSemanticResultCandidateSchema,
    VerificationResultRecordSchema, ClaimBindingFactSchema, ClaimBindingRelation,
    VERIFIER_LOCAL_REGISTRATION_KEY, VERIFIER_LOCAL_CORRECTION_KEY,
    VerifierLocalCorrectionBindingV1, VerifierRecoveryArtifactChainV1,
    build_verifier_local_correction_registration, build_verifier_registration_scope_sha256,
    build_verifier_original_root, build_verifier_correction_binding,
    build_verifier_correction_authorization, build_verifier_recovery_feedback,
    validate_verifier_local_recovery_facts,
)
from apps.enterprise_document.schemas.agent_rag_v1.claim_support import SupportRelation
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
from schemas.model import ModelRequestSchema, ModelResponseSchema
from .budget_manager import CanonicalBudgetCapacityExceeded, CanonicalBudgetDeadlineExceeded, CanonicalBudgetDraining
from .claim_support_runtime import ClaimSupportRuntime
from .claim_support_composition import TARGET_SET_CONTRACT, SUPPORT_SEMANTIC_CONTRACT, _strict_object
from .context_runtime import ApplicationContextRuntime, ContextSourceProviderRegistration
from .claim_verification_runtime import VerifierCandidateContractViolationError, ClaimCurrentnessError
from .model_tool_budget_runtime import CanonicalInvocationBudgetAdapter

from apps.enterprise_document.schemas.pre_write_writer import (
    WriterLogicalExecutionKey, WriterGenerationInvocationRefSchema, WriterExecutionStatus,
)
from apps.enterprise_document.schemas.project_input_schema import ProjectInputSchema
from core.runtime.pre_write_execution_store import WriterExecutionState


_CURRENT_OUTPUT_CAP = object()
_CURRENT_REPLY_REF_ORDER = object()
_CURRENT_LOCAL_RECOVERY = object()


class TargetClaimVerifierError(RuntimeError):
    def __init__(self, message, *, error_code="CLAIM_VERIFIER_FAILED", failure_category=FailureCategory.VALIDATION,
                 failure_origin=FailureOrigin.UNKNOWN):
        super().__init__(message)
        self.error_code = error_code
        self.failure_category = FailureCategory(failure_category)
        self.failure_origin = FailureOrigin(failure_origin)


class VerifierModelIssue(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    issue_code: str = Field(min_length=1, max_length=100)
    message: str = Field(min_length=1, max_length=1200)
    severity: Literal["MAJOR", "MINOR"]
    source_refs: tuple[str, ...]

    @model_validator(mode="after")
    def _refs(self):
        _canonical_refs(self.source_refs)
        return self


class VerifierModelReply(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    verdict: VerificationVerdict
    positive_basis_refs: tuple[str, ...]
    rationale: str = Field(min_length=1, max_length=2000)
    issues: tuple[VerifierModelIssue, ...] = Field(max_length=8)


    @model_validator(mode="after")
    def _provenance(self):
        _canonical_refs(self.positive_basis_refs)
        if len({(i.issue_code, i.message, i.severity, i.source_refs) for i in self.issues}) != len(self.issues):
            raise ValueError("Verifier issues must be unique")
        return self


def admissible_positive_basis_refs(binding, support, project_input_basis_ref):
    """Display existing mechanical admission; never decide semantic sufficiency."""
    kinds = {}
    for source in binding.exact_authorized_source_refs:
        kinds.setdefault(source.exact_source_ref, set()).add(source.source_kind)
    result = {ref for ref, values in kinds.items() if values == {"FACT"}}
    admissible_evidence = {fact.exact_source_ref for fact in binding.facts
        if fact.source_kind == "EVIDENCE" and fact.relation.value in {"SUPPORTS", "CONTEXT_ONLY"}} - {
        fact.exact_source_ref for fact in binding.facts if fact.relation.value == "NO_MATERIAL_SUPPORT"}
    evidence = set()
    if support.relation not in {SupportRelation.NOT_SUPPORTED, SupportRelation.INDETERMINATE}:
        evidence = {ref for ref, values in kinds.items() if values == {"EVIDENCE"}
            and ref in support.supporting_refs and ref in admissible_evidence}
        result.update(evidence)
    result.add(project_input_basis_ref)
    # Match the existing guard even for a conflicting Evidence/PI exact ref.
    if kinds.get(project_input_basis_ref) == {"EVIDENCE"} and project_input_basis_ref not in evidence:
        result.discard(project_input_basis_ref)
    return tuple(sorted(result))


class TargetClaimVerifierComposition:
    POLICY_ID = VERIFIER_POLICY_ID
    POLICY_VERSION = VERIFIER_POLICY_VERSION
    INSTRUCTION_VERSION = "v2-positive-provenance-v1"
    STRICT_DECISION_INSTRUCTION_VERSION = "v3-claim-complete-support-v1"
    STRICT_SEMANTIC_INSTRUCTION_VERSION = "v4-evidence-semantics-v2"
    STRICT_DECISION_PROFILE_KEY = "verifier_decision_profile"
    STRICT_DECISION_PROFILE = "claim-complete-support-v1"
    STRICT_SEMANTIC_PROFILE = "claim-semantic-v4"
    FACT_PRESENTATION_CONTRACT = "canonical-fact-exact-ref-material-v1"
    OUTPUT_CAP_BINDING_KEY = "verifier_output_cap_v1"
    REPLY_REF_ORDER_BINDING_KEY = "verifier_reply_ref_order_v1"
    REPLY_REF_ORDER_VERSION = "verifier-ref-order-v1"

    @staticmethod
    def _instructions(*, strict_decision=False):
        system = "You are the target-scoped VerifierAgent. Produce a candidate Claim validity verdict; never modify source facts, support, Workflow or release."
        role = (
            "Consume the committed shared support result; do not rerun Claim/Evidence support or invent alternative evidence. "
            "Assess the exact Claim in its current Paragraph, including subject, conditions and interpretation. "
            "Use FACT, supporting Evidence or the exact Writer ProjectInput basis as explicitly declared positive authority. "
            "allowed_authority_refs is the membership upper bound for provenance, not permission to use every member as positive_basis_refs. "
            "admissible_positive_basis_refs displays only existing mechanical positive-source admission; membership does not prove the Claim correct or require VERIFIED. "
            "Each FACT wrapper pairs its exact_source_ref with unchanged authoritative material; cite that exact ref without guessing identifiers. "
            "FACT_SNAPSHOT and EVIDENCE_PACKAGE are scope/container refs, never positive_basis_refs; they remain eligible issue.source_refs when present in allowed_authority_refs. "
            "ProjectInput instructions/output requirements are not automatically project facts. "
            "SUPPORTED/PARTIALLY_SUPPORTED allow only Evidence refs in supporting_refs; NOT_SUPPORTED forbids Evidence positive basis, but FACT/ProjectInput may independently support a Claim. "
            "CONTRADICTED or INDETERMINATE and Binding CONTRADICTS forbid VERIFIED. Support alone is not a verdict. "
            "Non-VERIFIED results may retain mechanically admissible mixed positive basis, including supporting Evidence with CONTRADICTED when its existing Binding restrictions permit it. "
            "INDETERMINATE forbids Evidence positive basis; do not remove admissible FACT/ProjectInput merely because VERIFIED is forbidden. "
            "CONFLICTED denotes explicit source conflict; UNSUPPORTED denotes insufficient or indeterminate authorized support, not proof of falsity; "
            "REJECTED denotes a claim invalid under this exact target, distinct from support or conflict. "
            "Preserve the canonical Evidence relation and unresolved contradiction fences. Report concrete issues and preserve uncertainty. "
            "Do not request new sources or decide repairs in this initial verification call."
        )
        template = "Return only JSON: verdict (VERIFIED/CONFLICTED/UNSUPPORTED/REJECTED), rationale (concise), positive_basis_refs (sorted unique exact refs from admissible_positive_basis_refs; nonempty for VERIFIED; admission alone is not semantic proof), issues (issue_code,message,severity MAJOR/MINOR,source_refs sorted unique exact refs from allowed_authority_refs)."
        if strict_decision:
            role += (
                " For this new-task strict decision profile, the task field "
                "admissible_verdicts is a deterministic projection of the "
                "existing M6 admission rules, not new support evidence. "
                "The verdict MUST be one of admissible_verdicts; never select "
                "VERIFIED when that list excludes it, even if some FACT or "
                "Writer ProjectInput rows support part of the Claim. "
                "Judge every material clause of the whole Claim, including "
                "causality, scope, negation, time and uncertainty. "
                "An authorized list of missing project inputs supports the "
                "fact that inputs are missing; it does not independently prove "
                "a stronger causal prediction about downstream outcomes. "
                "If the whole Claim is not substantiated and no explicit "
                "contradiction is established, choose UNSUPPORTED. "
                "Do not rewrite the Claim or turn a negative judgment into "
                "VERIFIED to satisfy a generation goal."
            )
            template += (
                " For admissible_verdicts use the exact task field; do not "
                "invent a fifth verdict or treat positive_basis_refs as "
                "independent authorization of VERIFIED."
            )
        return system, role, template

    @classmethod
    def _instructions_v4(cls):
        """Prospective, opt-in semantic clarification; keep v2/v3 prompt bytes frozen."""
        system, _, template = cls._instructions(strict_decision=True)
        role = (
            "Consume the committed Shared Support result; do not rerun support analysis, invent evidence, "
            "or alter Claim, sources, Support, Workflow, or release. "
            "Assess the exact Claim in its current Paragraph. Verify every material factual assertion and "
            "preserve its subject, scope, conditions, negation, time, causality, and certainty. "
            "Distinguish facts from inferences, conditional analyses, purposes, requirements, commitments, "
            "outcomes, and attributed historical references. Do not treat a purely document-drafting "
            "requirement as an independently verified real-world fact; assess factual assertions in mixed Claims. "
            "Use only FACT, supporting Evidence, or the exact Writer ProjectInput basis as explicitly declared "
            "positive authority, and only for propositions they actually support. A source's relevance or "
            "availability is not proof. A conservative inference may be supported when its premises are "
            "authorized, the reasoning adds no unsupported premise, and its scope and certainty do not exceed "
            "those premises. Do not use model memory, general plausibility, or unstated domain knowledge as evidence. "
            "Read ProjectInput according to its actual content and role. When the exact Writer ProjectInput "
            "basis explicitly identifies the project of this document by structured project_name, "
            "project_type or current_project_id, those fields are positive authority for the corresponding "
            "task-defined project identity or type Claim. A duplicate FACT row or supporting Evidence is "
            "not required for that limited Claim, even if Shared Support is NOT_SUPPORTED. "
            "This establishes a declared project descriptor only, not independent external verification, "
            "formal approval, implementation, or any other unstated project facts. A document-generation "
            "request without explicit identity fields cannot independently establish project identity. "
            "An explicitly scoped absence may support only that information was not supplied or found within "
            "that checked input scope; it does not prove the corresponding real-world entity or resource is absent. "
            "Historical sources may support accurately attributed historical facts or bounded reference lessons. "
            "They do not, without further support, establish current-project facts, universal practice, or "
            "guaranteed outcomes. Preserve the source's attribution, time, conditions, and applicability. "
            "Each FACT wrapper pairs its exact_source_ref with unchanged authoritative material; cite that "
            "exact ref and never guess identifiers. allowed_authority_refs is the provenance membership limit, "
            "not permission to use every member as positive_basis_refs. admissible_positive_basis_refs records "
            "mechanical positive-source admission; membership does not prove the Claim. "
            "FACT_SNAPSHOT and EVIDENCE_PACKAGE are container refs, never positive_basis_refs; they may be "
            "issue.source_refs when present in allowed_authority_refs. SUPPORTED/PARTIALLY_SUPPORTED permits "
            "Evidence positive basis only from supporting_refs. NOT_SUPPORTED forbids Evidence positive basis, "
            "but FACT or ProjectInput may independently support the Claim. "
            "CONTRADICTED, INDETERMINATE, and Binding CONTRADICTS forbid VERIFIED. "
            "INDETERMINATE forbids Evidence positive basis, but does not independently disqualify semantically "
            "supporting, mechanically admissible FACT/ProjectInput basis. "
            "For non-VERIFIED verdicts, mechanically admissible positive basis may be retained when it "
            "semantically supports a material part of the Claim, including mixed sources and supporting "
            "Evidence with CONTRADICTED when existing Binding restrictions permit it. "
            "Never include a positive_basis_ref solely because it is mechanically admissible. "
            "Preserve canonical Evidence relations and unresolved contradiction fences. "
            "CONFLICTED means an explicit source conflict; UNSUPPORTED means authorized support is insufficient "
            "or indeterminate, not that the Claim is false; REJECTED means the Claim is invalid under this exact "
            "target, not merely unsupported or conflicting. Report concrete issues, preserve uncertainty, "
            "use only exact admissible refs, and do not request new sources or decide repairs in this verification call. "
            "Use the task's admissible_verdicts exactly as the mechanically permitted candidate set: return one "
            "listed verdict and never return VERIFIED if it is absent. Its inclusion does not establish support "
            "or require VERIFIED. "
            "Return VERIFIED only when every material assertion in the whole Claim is sufficiently supported "
            "by semantically matching, authorized positive basis, with all existing M6, Binding, Shared Support, "
            "and source-admission restrictions satisfied. Do not upgrade partial support, inference, historical "
            "examples, or plausible outcomes by dropping conditions, broadening source scope, strengthening "
            "certainty or causality, or adding external knowledge. "
            "If support is insufficient and there is no explicit source conflict or basis to reject the Claim "
            "as invalid under this exact target, choose UNSUPPORTED. Do not rewrite the Claim or relax "
            "verification to meet a generation goal."
        )
        return system, role, template

    @classmethod
    def _strict_decision_from_project(cls, project):
        if hasattr(project, "model_dump"):
            project = project.model_dump(mode="json")
        if not isinstance(project, dict):
            raise TargetClaimVerifierError("Verifier ProjectInput profile is not a typed mapping")
        writing = project.get("generation_requirements") or {}
        extra = writing.get("extra") or {}
        if not isinstance(extra, dict):
            raise TargetClaimVerifierError("Verifier generation profile extras are invalid")
        requested = extra.get(cls.STRICT_DECISION_PROFILE_KEY)
        if requested is None:
            return False
        if (project.get("schema_version") != "project_input_v3"
                or requested not in {cls.STRICT_DECISION_PROFILE, cls.STRICT_SEMANTIC_PROFILE}):
            raise TargetClaimVerifierError("Unsupported prospective Verifier decision profile")
        return True

    @classmethod
    def _semantic_v4_from_project(cls, project):
        if hasattr(project, "model_dump"):
            project = project.model_dump(mode="json")
        return (cls._strict_decision_from_project(project)
                and (project.get("generation_requirements") or {}).get("extra", {}).get(
                    cls.STRICT_DECISION_PROFILE_KEY) == cls.STRICT_SEMANTIC_PROFILE)

    @staticmethod
    def _admissible_verdicts_from_m6_inputs(*, support_relation, binding_facts, positive_refs):
        """Model guidance only: mirrors existing VERIFIED prohibitions; M6 still owns admission."""
        positive_allowed = bool(positive_refs) and support_relation not in {
            SupportRelation.CONTRADICTED, SupportRelation.INDETERMINATE
        } and not any(fact.relation is ClaimBindingRelation.CONTRADICTS for fact in binding_facts)
        return (("VERIFIED",) if positive_allowed else ()) + (
            "UNSUPPORTED", "CONFLICTED", "REJECTED"
        )

    @classmethod
    def prompt_profile_sha256(cls, *, strict_decision=False, semantic_v4=False):
        if semantic_v4 and not strict_decision:
            raise ValueError("Verifier semantic v4 requires strict decision")
        system, role, template = (cls._instructions_v4() if semantic_v4
            else cls._instructions(strict_decision=True) if strict_decision
            else cls._instructions())
        version = (cls.STRICT_SEMANTIC_INSTRUCTION_VERSION if semantic_v4
            else cls.STRICT_DECISION_INSTRUCTION_VERSION if strict_decision
            else cls.INSTRUCTION_VERSION)
        return canonical_sha256({"instruction_version": version,
            "system_instruction_sha256": canonical_sha256(system), "role_instruction_sha256": canonical_sha256(role),
            "prompt_template_sha256": canonical_sha256(template),
            "fact_presentation_contract": cls.FACT_PRESENTATION_CONTRACT,
            "positive_admission_presentation_contract": "existing-v2-positive-provenance-display-v1"})

    @classmethod
    def reply_ref_order_profile_sha256(cls):
        return canonical_sha256({"normalizer_version": cls.REPLY_REF_ORDER_VERSION,
            "external_contract": "unordered-reference-lists; complete strict JSON and list[str] required",
            "paths": ["positive_basis_refs", "issues[*].source_refs"],
            "operation": "sort-only-preserve-strings-members-and-multiplicity",
            "internal_contract": "existing-sorted-unique-validation-and-provenance-guards",
            "other_fields": "unchanged-including-issue-order"})

    @classmethod
    def reply_ref_order_binding(cls):
        return {"normalizer_version": cls.REPLY_REF_ORDER_VERSION,
            "normalizer_profile_sha256": cls.reply_ref_order_profile_sha256()}

    @classmethod
    def _validated_reply_ref_order_binding(cls, binding):
        if not isinstance(binding, dict) or binding != cls.reply_ref_order_binding():
            raise TargetClaimVerifierError("Verifier reply-entry binding is unknown or invalid")
        return dict(binding)

    @classmethod
    def _binding_only_historical_input(cls, invocation):
        """Reconstruct the old hash for reads/probes; never upgrade or dispatch it."""
        data = invocation.model_dump(mode="json")
        data["deterministic_validation_facts"].pop("verifier_prompt_profile_sha256", None)
        data["deterministic_validation_facts"].pop(cls.OUTPUT_CAP_BINDING_KEY, None)
        data["deterministic_validation_facts"].pop(cls.REPLY_REF_ORDER_BINDING_KEY, None)
        data["deterministic_validation_facts"].pop(VERIFIER_LOCAL_REGISTRATION_KEY, None)
        data["deterministic_validation_facts"].pop(VERIFIER_LOCAL_CORRECTION_KEY, None)
        data["verifier_semantic_input_sha256"] = build_verifier_semantic_input_sha256(data)
        return VerifierClaimInputSchema.model_validate(data)

    def __init__(self, *, claim_runtime, support_composition, model_gateway, writer_execution_store,
                 verifier_max_output_tokens=1024):
        if type(verifier_max_output_tokens) is not int or verifier_max_output_tokens <= 0:
            raise TargetClaimVerifierError("Verifier output cap must be a positive integer")
        if writer_execution_store is None:
            raise TargetClaimVerifierError("Verifier requires canonical Writer execution owner")
        self.writer_execution_store = writer_execution_store
        self.claim_runtime = claim_runtime
        self.support = support_composition
        self.sources = support_composition.sources
        self.model_gateway = model_gateway
        self.verifier_max_output_tokens = verifier_max_output_tokens
        self.store = claim_runtime.paragraph_runtime.artifact_runtime.artifact_store
        self.estimator = DeterministicTokenEstimator()
        self._lock = RLock()

    @staticmethod
    def _validated_output_cap_binding(binding):
        if (not isinstance(binding, dict) or set(binding) != {"effective_max_output_tokens"}
                or type(binding["effective_max_output_tokens"]) is not int
                or binding["effective_max_output_tokens"] <= 0):
            raise TargetClaimVerifierError("Verifier output cap binding must contain one positive integer")
        return dict(binding)

    def _effective_output_cap_for(self, capability):
        safe_max = capability.get("safe_max_output_tokens")
        if type(safe_max) is not int or safe_max <= 0:
            raise TargetClaimVerifierError("Verifier requires a positive integer Gateway output capability")
        return min(self.verifier_max_output_tokens, safe_max)

    def effective_output_cap(self):
        if self.model_gateway is None:
            raise TargetClaimVerifierError("Verifier cognition requires a configured ModelGateway")
        capability = dict(self.model_gateway.routing_capabilities(model_role=ModelRole.GENERAL.value))
        return self._effective_output_cap_for(capability)

    @staticmethod
    def _json(value):
        return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))

    def validate_verification_sources(self, binding, support, lineage):
        """Called inside the M6 lock: never re-enter Claim or shared Support stores."""
        candidate = self.claim_runtime._binding_record_candidate(binding)
        rows = self.sources._materialize_sources(candidate, lineage)
        self._validate_resolved_sources(candidate, binding, support, rows)

    def validate_read_sources(self, binding, support, lineage):
        """Validate existing source facts for a read-only downstream consumer."""
        candidate = self.claim_runtime._binding_record_candidate(binding)
        rows = self.sources.read_current_sources(candidate, lineage)
        self._validate_resolved_sources(candidate, binding, support, rows)

    def _validate_resolved_sources(self, candidate, binding, support, rows):
        expected = self.sources._candidate_from_sources(candidate, rows)
        if candidate != expected:
            raise TargetClaimVerifierError("Verifier source bindings changed")
        if (support.support_semantic_contract_version != SUPPORT_SEMANTIC_CONTRACT
                or support.claim_support_subject_ref.subject_ref != binding.claim_ref.claim_id):
            raise TargetClaimVerifierError("Verifier support targets another Claim or contract")
        target = support.evidence_target_set_ref
        allowed = {row.source_ref.exact_source_ref for row in rows if row.source_ref.source_kind == "EVIDENCE"}
        if set(target.evidence_member_refs) != allowed:
            raise TargetClaimVerifierError("initial Verifier support target is not the full authorized Evidence set")
        exact = ClaimSupportRuntime.materialize_evidence_target_set(target.claim_support_subject_ref,
                    target.evidence_member_refs, TARGET_SET_CONTRACT)
        if target != exact:
            raise TargetClaimVerifierError("Verifier support target identity changed")

    def resolve_project_input_basis(self, record, lineage):
        """Only the Claim-producing Paragraph's exact durable Writer is authority."""
        paragraph = self.claim_runtime.paragraph_runtime.read_paragraph(record.paragraph_version_ref)
        source = paragraph.source_binding
        subject = source.writer_subject_ref
        if (subject.run_id != record.claim_occurrence_ref.run_id
                or subject.section_id != record.claim_occurrence_ref.section_id
                or lineage.run_id != subject.run_id
                or lineage.attempt_scope_ref != f"section:{subject.section_id}"
                or source.write_instance_id != lineage.attempt_id):
            raise TargetClaimVerifierError("Verifier Writer lineage differs from Claim occurrence")
        key = WriterLogicalExecutionKey(writer_subject_ref=subject,
            write_instance_id=source.write_instance_id, route_decision_ref=source.route_decision_ref)
        execution = self.writer_execution_store.get_writer_execution(key)
        if (execution is None or execution.state is not WriterExecutionState.SUCCEEDED
                or execution.result is None or execution.result.status is not WriterExecutionStatus.SUCCESS
                or execution.result.logical_execution_key != key):
            raise TargetClaimVerifierError("Verifier requires exact successful durable Writer execution")
        ref = WriterGenerationInvocationRefSchema(logical_execution_key=key,
            invocation_sha256=execution.semantic_fingerprint)
        invocation = self.writer_execution_store.resolve_writer_generation_invocation(ref)
        if (invocation.writer_subject_ref != subject or invocation.write_instance_id != source.write_instance_id
                or invocation.route_decision_ref != source.route_decision_ref):
            raise TargetClaimVerifierError("Verifier Writer invocation lineage mismatch")
        raw = invocation.model_dump(mode="json")["unified_writer_input_snapshot"]["project_input"]
        project = ProjectInputSchema.model_validate(raw)
        digest = canonical_sha256(project.model_dump(mode="json"))
        return VerifierProjectInputBasisV1(writer_generation_invocation_ref=ref,
            project_input_sha256=digest, project_input_basis_ref=project_input_basis_ref(ref, digest)), project

    def build_input(self, record, binding, support, context, *, _recorded_output_cap=_CURRENT_OUTPUT_CAP,
                    _recorded_reply_ref_order=_CURRENT_REPLY_REF_ORDER,
                    _recorded_recovery_facts=_CURRENT_LOCAL_RECOVERY):
        basis, project = self.resolve_project_input_basis(record, context.active_lineage)
        strict_decision = self._strict_decision_from_project(project)
        semantic_v4 = self._semantic_v4_from_project(project) if strict_decision else False
        deterministic_facts = {"binding_semantic_fingerprint": binding.binding_semantic_fingerprint,
            "verifier_prompt_profile_sha256": self.prompt_profile_sha256(
                strict_decision=strict_decision, semantic_v4=semantic_v4)}
        if _recorded_output_cap is _CURRENT_OUTPUT_CAP:
            # Owner-only projections may omit a Gateway, but cannot be packaged
            # or dispatched. Real canonical assembly always supplies its Gateway.
            if self.model_gateway is not None:
                deterministic_facts[self.OUTPUT_CAP_BINDING_KEY] = {
                    "effective_max_output_tokens": self.effective_output_cap()}
        elif _recorded_output_cap is not None:
            deterministic_facts[self.OUTPUT_CAP_BINDING_KEY] = self._validated_output_cap_binding(_recorded_output_cap)
        if _recorded_reply_ref_order is _CURRENT_REPLY_REF_ORDER:
            deterministic_facts[self.REPLY_REF_ORDER_BINDING_KEY] = self.reply_ref_order_binding()
        elif _recorded_reply_ref_order is not None:
            deterministic_facts[self.REPLY_REF_ORDER_BINDING_KEY] = self._validated_reply_ref_order_binding(_recorded_reply_ref_order)
        data = dict(claim_ref=record.claim_ref, claim_occurrence_ref=record.claim_occurrence_ref,
            paragraph_version_ref=record.paragraph_version_ref, binding_facts_ref=binding.binding_facts_ref,
            claim_evidence_support_ref=support.support_ref, exact_relevant_source_refs=binding.exact_authorized_source_refs,
            active_lineage=context.active_lineage, verification_authorization_ref=context.authorization_ref,
            deterministic_validation_facts=deterministic_facts,
            verifier_project_input_basis=basis,
            authority_universe_sha256=authority_universe_sha256(binding.binding_facts_ref, basis.project_input_basis_ref),
            allowed_authority_refs=tuple(sorted({i.exact_source_ref for i in binding.exact_authorized_source_refs}
                | {basis.project_input_basis_ref})), verifier_policy_id=self.POLICY_ID,
            verifier_policy_version=self.POLICY_VERSION, response_contract=VERIFIER_RESPONSE_CONTRACT)
        json_data = {key: value.model_dump(mode="json") if hasattr(value, "model_dump") else value for key, value in data.items()}
        if _recorded_recovery_facts is _CURRENT_LOCAL_RECOVERY:
            deterministic_facts[VERIFIER_LOCAL_REGISTRATION_KEY] = (
                build_verifier_local_correction_registration(json_data).model_dump(mode="json"))
        elif _recorded_recovery_facts:
            if set(_recorded_recovery_facts) - {VERIFIER_LOCAL_REGISTRATION_KEY, VERIFIER_LOCAL_CORRECTION_KEY}:
                raise TargetClaimVerifierError("unknown recorded Verifier local recovery fact")
            deterministic_facts.update(deepcopy(_recorded_recovery_facts))
        return VerifierClaimInputSchema(**data,
            verifier_semantic_input_sha256=build_verifier_semantic_input_sha256(json_data)), project

    def validate_v2_identity(self, record, binding, support, candidate, lineage):
        # Called under M6's lock: only foreign owners are read, never Claim/Support.
        context = ClaimOperationContextSchema(active_lineage=lineage,
            authorization_ref=candidate.verification_authorization_ref, operation_purpose="verification-currentness")
        if isinstance(candidate, VerifierSemanticResultCandidateSchema):
            actual = VerifierClaimInputSchema.model_validate({key: value for key, value in candidate.model_dump(mode="json").items()
                if key in VerifierClaimInputSchema.model_fields and key != "schema_version"})
        elif isinstance(candidate, VerifierClaimInputSchema):
            # Dispatch validates the real typed input, before any response exists.
            actual = candidate
        else:
            actual = self.input_from_result(candidate, binding)
        facts = actual.deterministic_validation_facts
        child = VERIFIER_LOCAL_CORRECTION_KEY in facts
        recorded_result = isinstance(candidate, VerificationResultRecordSchema)
        recovery = {key: facts[key] for key in (
            VERIFIER_LOCAL_REGISTRATION_KEY, VERIFIER_LOCAL_CORRECTION_KEY) if key in facts}
        if recorded_result or child:
            # Immutable owner Results retain recorded profiles. A child binds its
            # exact original profiles plus grant; neither acquires a new policy.
            expected, _ = self.build_input(record, binding, support, context,
                _recorded_output_cap=facts.get(self.OUTPUT_CAP_BINDING_KEY),
                _recorded_reply_ref_order=facts.get(self.REPLY_REF_ORDER_BINDING_KEY),
                _recorded_recovery_facts=recovery)
        else:
            # As before recovery, fresh Candidates must match today's profiles.
            # Explicit absence preserves an old input without registering it.
            expected, _ = self.build_input(record, binding, support, context,
                _recorded_recovery_facts=recovery)
        if recorded_result:
            if facts == {"binding_semantic_fingerprint": binding.binding_semantic_fingerprint}:
                expected = self._binding_only_historical_input(expected)
            elif "verifier_prompt_profile_sha256" not in facts:
                data = expected.model_dump(mode="json")
                data["deterministic_validation_facts"].pop("verifier_prompt_profile_sha256", None)
                data["verifier_semantic_input_sha256"] = build_verifier_semantic_input_sha256(data)
                expected = VerifierClaimInputSchema.model_validate(data)
        if actual.exact_relevant_source_refs != binding.exact_authorized_source_refs:
            raise TargetClaimVerifierError("Verifier derived Binding projection differs from canonical owner")
        if expected.verifier_semantic_input_sha256 != actual.verifier_semantic_input_sha256:
            raise TargetClaimVerifierError("Verifier V2 semantic input is stale")
        if expected.verifier_project_input_basis != actual.verifier_project_input_basis:
            raise TargetClaimVerifierError("Verifier ProjectInput basis is stale")
        formal = isinstance(candidate, (VerifierSemanticResultCandidateSchema, VerificationResultRecordSchema))
        _, _, physical_prefix = self._physical(actual)
        if (formal and not child and VERIFIER_LOCAL_REGISTRATION_KEY in facts
                and self._read(physical_prefix + "/request", self._artifact_identity(actual)) is not None):
            # Original eligible guards/grants also read configured native billing.
            # Only old no-Budget initial component paths retain compatibility.
            self._complete_response(actual, require_canonical_budget=False)
        if child and formal:
            # Foreign-owner reads only: never call a public M6 resolver here.
            _, _, response = self._complete_response(actual, require_canonical_budget=True)
            _, occurrence, prefix = self._physical(actual)
            receipt = self._exact_artifact(prefix + "/candidate", actual, "canonical_verifier_candidate", "canonical_verifier_candidate_v2")
            if (not response.success or response.finish_reason == "length"
                    or receipt.get("outcome") != "completed"
                    or receipt.get("input_fingerprint") != actual.verifier_semantic_input_sha256):
                raise TargetClaimVerifierError("Verifier child formal commit lacks a complete durable response/candidate",
                    error_code="VERIFIER_DURABLE_RESPONSE_INVALID")
            parsed, semantics = self.parse_candidate(response.content, invocation=actual, occurrence=occurrence, receipt_ref=prefix + "/candidate")
            if receipt.get("candidate") != parsed.model_dump(mode="json") or receipt.get("semantic_result") != semantics:
                raise TargetClaimVerifierError("Verifier child candidate differs from its exact durable raw judgment",
                    error_code="VERIFIER_DURABLE_RESPONSE_INVALID")
            if isinstance(candidate, VerifierSemanticResultCandidateSchema):
                if candidate != parsed:
                    raise TargetClaimVerifierError("Verifier child formal candidate differs from its durable judgment",
                        error_code="VERIFIER_DURABLE_RESPONSE_INVALID")
            elif (candidate.verdict, candidate.positive_basis_refs, candidate.semantic_rationale_hash,
                    candidate.semantic_rationale_ref, candidate.verification_occurrence_ref) != (
                    parsed.recommended_verdict, parsed.positive_basis_refs, parsed.semantic_rationale_hash,
                    parsed.semantic_rationale_ref, parsed.verification_occurrence_ref):
                raise TargetClaimVerifierError("Verifier child formal result differs from its durable judgment",
                    error_code="VERIFIER_DURABLE_RESPONSE_INVALID")

    @staticmethod
    def input_from_result(result, binding):
        data = result.model_dump(mode="json")
        data["verifier_project_input_basis"] = VerifierProjectInputBasisV1(
            writer_generation_invocation_ref=result.writer_generation_invocation_ref,
            project_input_sha256=result.project_input_sha256,
            project_input_basis_ref=result.project_input_basis_ref).model_dump(mode="json")
        data["exact_relevant_source_refs"] = [i.model_dump(mode="json") for i in binding.exact_authorized_source_refs]
        data["allowed_authority_refs"] = sorted({i.exact_source_ref for i in binding.exact_authorized_source_refs}
            | {result.project_input_basis_ref})
        return VerifierClaimInputSchema.model_validate({key: value for key, value in data.items()
            if key in VerifierClaimInputSchema.model_fields and key != "schema_version"})

    def _material(self, claim, binding, support, context, *, _recorded_invocation=None):
        self.claim_runtime.validate_operation_context(context)
        if self.claim_runtime.resolve_claim(claim.claim_ref) != claim:
            raise TargetClaimVerifierError("Verifier Claim differs from canonical record")
        if self.claim_runtime.resolve_binding_facts(binding.binding_facts_ref) != binding:
            raise TargetClaimVerifierError("Verifier Binding differs from canonical record")
        if self.support.runtime.resolve_support(support.support_ref) != support:
            raise TargetClaimVerifierError("Verifier shared Support differs from canonical record")
        self.validate_verification_sources(binding, support, context.active_lineage)
        rows = self.sources.materialize_sources(claim, context.active_lineage)
        paragraph, exact = self.claim_runtime.paragraph_runtime.artifact_runtime.read_version(claim.paragraph_version_ref)
        if exact != claim.paragraph_version_ref:
            raise TargetClaimVerifierError("Verifier Paragraph source identity changed")
        if _recorded_invocation is None:
            invocation, project = self.build_input(claim, binding, support, context)
        else:
            facts = _recorded_invocation.deterministic_validation_facts
            invocation, project = self.build_input(claim, binding, support, context,
                _recorded_output_cap=facts.get(self.OUTPUT_CAP_BINDING_KEY),
                _recorded_reply_ref_order=facts.get(self.REPLY_REF_ORDER_BINDING_KEY),
                _recorded_recovery_facts={key: facts[key] for key in (
                    VERIFIER_LOCAL_REGISTRATION_KEY, VERIFIER_LOCAL_CORRECTION_KEY) if key in facts})
            if facts == {"binding_semantic_fingerprint": binding.binding_semantic_fingerprint}:
                invocation = self._binding_only_historical_input(invocation)
        material = {
            "invocation": invocation.model_dump(mode="json"),
            "claim": claim.model_dump(mode="json"),
            "paragraph_text": paragraph["text"],
            "project_input": project.model_dump(mode="json"),
            "binding_facts": [item.model_dump(mode="json") for item in binding.facts],
            "shared_support": {"support_ref": support.support_ref.model_dump(mode="json"),
                "relation": support.relation.value, "supporting_refs": list(support.supporting_refs),
                "contradicting_refs": list(support.contradicting_refs)},
            "facts": [{"exact_source_ref": row.source_ref.exact_source_ref, "material": row.material}
                      for row in rows if row.source_ref.source_kind == "FACT"],
            "evidence": [{"exact_source_ref": row.source_ref.exact_source_ref, **row.material}
                         for row in rows if row.source_ref.source_kind == "EVIDENCE"],
            "policy_id": self.POLICY_ID, "policy_version": self.POLICY_VERSION,
            "admissible_positive_basis_refs": list(admissible_positive_basis_refs(
                binding, support, invocation.verifier_project_input_basis.project_input_basis_ref)),
        }
        return invocation, material

    def _package(self, material, *, occurrence, invocation, validate):
        strict_decision = self._strict_decision_from_project(material["project_input"])
        semantic_v4 = self._semantic_v4_from_project(material["project_input"]) if strict_decision else False
        if invocation.deterministic_validation_facts.get("verifier_prompt_profile_sha256") != self.prompt_profile_sha256(
                strict_decision=strict_decision, semantic_v4=semantic_v4):
            raise TargetClaimVerifierError("Verifier invocation does not bind the current prompt profile")
        self._validated_reply_ref_order_binding(
            invocation.deterministic_validation_facts.get(self.REPLY_REF_ORDER_BINDING_KEY))
        if self.model_gateway is None:
            raise TargetClaimVerifierError("Verifier cognition requires a configured ModelGateway")
        binding = self._validated_output_cap_binding(
            invocation.deterministic_validation_facts.get(self.OUTPUT_CAP_BINDING_KEY))
        capability = dict(self.model_gateway.routing_capabilities(model_role=ModelRole.GENERAL.value))
        output_limit = self._effective_output_cap_for(capability)
        if binding["effective_max_output_tokens"] != output_limit:
            raise TargetClaimVerifierError("Verifier invocation output cap differs from current Gateway/profile")
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
        lineage = invocation.active_lineage
        # Claim/Binding/Support are explicit target control, not uncommitted prose history.
        payloads = {
            ContextSourceFamily.PARAGRAPH_ARTIFACT: {"paragraph_text": material["paragraph_text"], "claim": material["claim"]},
            ContextSourceFamily.FACT_SNAPSHOT: {"relevant_facts": material["facts"]},
            ContextSourceFamily.EVIDENCE_PACKAGE: {"authorized_evidence": material["evidence"]},
            ContextSourceFamily.WORKFLOW_EXECUTION_METADATA: {"project_input": material["project_input"],
                "verifier_project_input_basis": invocation.verifier_project_input_basis.model_dump(mode="json")},
        }
        def resolve(source):
            validate()
            return source
        for family, payload in payloads.items():
            status = {"state": "CURRENT"}
            source = ContextSourceBindingSchema(
                source_family=family, exact_source_ref={"claim_ref": material["claim"]["claim_ref"],
                  "paragraph_version_ref": material["claim"]["paragraph_version_ref"],
                  "binding_facts_ref": material["invocation"]["binding_facts_ref"],
                  "verifier_project_input_basis": invocation.verifier_project_input_basis.model_dump(mode="json")},
                source_schema_id=("verifier_writer_project_input_v1" if family is ContextSourceFamily.WORKFLOW_EXECUTION_METADATA
                    else "canonical_verifier_source_v2"), source_schema_version=("v1" if family is ContextSourceFamily.WORKFLOW_EXECUTION_METADATA else "v2"),
                source_semantic_fingerprint=canonical_sha256(payload), authority_owner=("exact-WriterGenerationInvocation" if family is ContextSourceFamily.WORKFLOW_EXECUTION_METADATA
                    else "existing-Claim-Paragraph-Fact-Evidence-owners"),
                run_id=lineage.run_id, scope_ref=lineage.attempt_scope_ref,
                currentness_binding=ContextSourceStatusBindingSchema(
                    status_schema_id="canonical_verifier_currentness_v2", status_schema_version="v2",
                    resolver_id="canonical-verifier-exact-sources", resolver_policy_version="v2",
                    status_payload=status, status_payload_sha256=canonical_sha256(status), status_semantic_fingerprint=canonical_sha256(status),
                ),
            )
            candidate = ContextSourceCandidateSchema(
                candidate_id="verifier-source-" + family.value, source_binding=source, title=family.value,
                representations=(ContextRepresentationOptionSchema(
                    representation_level=ContextRepresentationLevel.EXACT,
                    projection_schema_id=("verifier_writer_project_input_v1" if family is ContextSourceFamily.WORKFLOW_EXECUTION_METADATA else "canonical_verifier_source_v2"),
                    projection_schema_version=("v1" if family is ContextSourceFamily.WORKFLOW_EXECUTION_METADATA else "v2"),
                    representation_policy_id="canonical-verifier-exact-sources", representation_policy_version="v2",
                    materialized_payload=payload, materialized_payload_sha256=canonical_sha256(payload),
                    protected_field_evidence=tuple(sorted(payload)),
                ),),
            )
            runtime.register_source_provider(ContextSourceProviderRegistration(
                source_family=family, provider_id="canonical-verifier-" + family.value, provider_version="v2",
                candidate_reader=lambda _subject, item=candidate: (item,), source_binding_resolver=resolve,
                hard_eligibility_validator=lambda source, subject: (
                    source.run_id == subject.run_id and source.scope_ref == subject.consumer_scope_ref,
                    () if source.run_id == subject.run_id and source.scope_ref == subject.consumer_scope_ref else ("VERIFIER_SCOPE_MISMATCH",),
                ),
            ))
        rules = tuple(ContextRoleSourceRuleSchema(source_family=family, visibility=ContextRoleVisibility.MUST,
                    mandatory=True, priority_tier=ContextPriorityTier.P0_MANDATORY_AUTHORITY,
                    allowed_representation_levels=(ContextRepresentationLevel.EXACT,),
                    minimum_representation_floor=ContextRepresentationLevel.EXACT) for family in payloads)
        policy = ContextPolicyBindingSchema(
            source_catalog_id=runtime.SOURCE_CATALOG_ID, source_catalog_version=runtime.SOURCE_CATALOG_VERSION,
            role_policy_id="canonical-verifier-initial", role_policy_version="v2",
            cross_section_policy_id="canonical-verifier-exact-scope", cross_section_policy_version="v2",
            consumer_authorization_policy_id="canonical-verifier-current-sources", consumer_authorization_policy_version="v2",
            selection_policy_id="sif10-hierarchical-selection", selection_policy_version="v1",
            budget_policy_id="sif10-context-budget", budget_policy_version="v1",
            representation_policy_id="canonical-verifier-exact-sources", representation_policy_version="v2",
        )
        system, role, template = (self._instructions_v4() if semantic_v4
            else self._instructions(strict_decision=True) if strict_decision
            else self._instructions())
        instruction_version = (self.STRICT_SEMANTIC_INSTRUCTION_VERSION if semantic_v4
            else self.STRICT_DECISION_INSTRUCTION_VERSION if strict_decision
            else self.INSTRUCTION_VERSION)
        projected = admissible_positive_basis_refs(SimpleNamespace(
            exact_authorized_source_refs=invocation.exact_relevant_source_refs,
            facts=tuple(ClaimBindingFactSchema.model_validate(item) for item in material["binding_facts"])),
            SimpleNamespace(relation=SupportRelation(material["shared_support"]["relation"]),
                supporting_refs=tuple(material["shared_support"]["supporting_refs"])),
            invocation.verifier_project_input_basis.project_input_basis_ref)
        if ("admissible_positive_basis_refs" in material
                and tuple(material["admissible_positive_basis_refs"]) != projected):
            raise TargetClaimVerifierError("positive admission display differs from authoritative inputs")
        task_material = {"verification_target": material["claim"]["claim_ref"],
                          "shared_support": material["shared_support"], "binding_facts": material["binding_facts"],
                          "allowed_authority_refs": invocation.allowed_authority_refs,
                          "admissible_positive_basis_refs": projected}
        if strict_decision:
            task_material["admissible_verdicts"] = list(self._admissible_verdicts_from_m6_inputs(
                support_relation=SupportRelation(material["shared_support"]["relation"]),
                binding_facts=tuple(ClaimBindingFactSchema.model_validate(item) for item in material["binding_facts"]),
                positive_refs=projected,
            ))
        if VERIFIER_LOCAL_CORRECTION_KEY in invocation.deterministic_validation_facts:
            correction = VerifierLocalCorrectionBindingV1.model_validate(
                invocation.deterministic_validation_facts[VERIFIER_LOCAL_CORRECTION_KEY])
            task_material["local_correction_feedback"] = correction.authorization.feedback
            task_material["local_correction_feedback_sha256"] = correction.authorization.feedback_sha256
        task = self._json(task_material)
        instructions = ContextInstructionMaterialSchema(system_instruction=system, role_instruction=role,
                            prompt_template=template, task_output_contract=task, dynamic_control_payloads=())
        lock = InstructionBindingSchema(
            system_instruction_ref="instruction://canonical-verifier/system/v2", system_instruction_version="v2", system_instruction_sha256=canonical_sha256(system),
            role_instruction_ref="instruction://canonical-verifier/role/" + instruction_version,
            role_instruction_version=instruction_version, role_instruction_sha256=canonical_sha256(role),
            prompt_template_ref="template://canonical-verifier/" + instruction_version,
            prompt_template_version=instruction_version, prompt_template_sha256=canonical_sha256(template),
            task_output_contract_ref="contract://canonical-verifier/" + canonical_sha256(task), task_output_contract_sha256=canonical_sha256(task), dynamic_control_bindings=(),
        )
        package = runtime.materialize_context_package(
            invocation_subject=ContextInvocationSubjectSchema(run_id=lineage.run_id, attempt_id=lineage.attempt_id,
                invocation_id=occurrence, logical_operation_id=occurrence, agent_role="VERIFIER", execution_mode="INITIAL_CLAIM_VERIFY",
                context_purpose="CLAIM_VALIDITY", consumer_scope_ref=lineage.attempt_scope_ref),
            role_policy=ContextRolePolicySchema(role_policy_id=policy.role_policy_id, role_policy_version="v2", agent_role="VERIFIER",
                execution_mode="INITIAL_CLAIM_VERIFY", context_purpose="CLAIM_VALIDITY", source_rules=rules),
            policy_binding=policy, model_capability_binding=cap, instruction_binding=lock, instruction_material=instructions,
            reserved_instruction_tokens=max(1, self.estimator.estimate(self._json(instructions.model_dump(mode="json")))),
            reserved_output_tokens=output_limit, safety_margin_tokens=64,
        )
        runtime.validate_pre_model_call_current(package, {
            "source_catalog_version": runtime.SOURCE_CATALOG_VERSION, "role_policy_version": "v2", "cross_section_policy_version": "v2",
            "consumer_authorization_policy_version": "v2", "selection_policy_version": "v1", "budget_policy_version": "v1",
            "representation_policy_version": "v2", "model_capability_fingerprint": cap_hash, "token_estimator_fingerprint": token_hash,
        })
        return package, output_limit

    @classmethod
    def _decode_external_reply(cls, raw):
        if isinstance(raw, str):
            def reject_constant(_value):
                raise ValueError("Verifier reply must contain valid JSON constants")
            decoded = json.loads(raw, object_pairs_hook=_strict_object, parse_constant=reject_constant)
        else:
            decoded = deepcopy(raw)
        if type(decoded) is not dict:
            raise ValueError("Verifier reply must be a JSON object")
        return decoded

    @staticmethod
    def _reply_reference_paths(decoded):
        if type(decoded.get("positive_basis_refs")) is not list or type(decoded.get("issues")) is not list:
            raise ValueError("Verifier reference fields and issues must be JSON arrays")
        paths = [("positive_basis_refs", decoded["positive_basis_refs"])]
        for index, issue in enumerate(decoded["issues"]):
            if type(issue) is not dict or type(issue.get("source_refs")) is not list:
                raise ValueError("Verifier issue source_refs must be a JSON array")
            paths.append((f"issues[{index}].source_refs", issue["source_refs"]))
        if any(type(ref) is not str for _path, refs in paths for ref in refs):
            raise ValueError("Verifier reference arrays must contain strings without coercion")
        return paths

    @classmethod
    def _raw_reply_encoding_and_hash(cls, raw):
        if isinstance(raw, str):
            return "utf8", hashlib.sha256(raw.encode("utf-8")).hexdigest()
        return "canonical-json-object", hashlib.sha256(cls._json(raw).encode("utf-8")).hexdigest()

    @classmethod
    def normalize_reply_reference_order(cls, raw):
        """Sort two external list fields only; existing validators retain authority.

        Members, multiplicity and all other fields are preserved. Duplicate refs
        and issues remain for the original strict validators to reject.
        """
        decoded = cls._decode_external_reply(raw)
        paths = cls._reply_reference_paths(decoded)
        normalized = deepcopy(decoded)
        normalized["positive_basis_refs"] = sorted(decoded["positive_basis_refs"])
        for index, issue in enumerate(decoded["issues"]):
            normalized["issues"][index]["source_refs"] = sorted(issue["source_refs"])
        encoding, raw_hash = cls._raw_reply_encoding_and_hash(raw)
        return normalized, {**cls.reply_ref_order_binding(), "raw_encoding": encoding,
            "raw_reply_sha256": raw_hash, "parsed_reply_sha256": canonical_sha256(decoded),
            "normalized_reply_sha256": canonical_sha256(normalized),
            "changed_ref_paths": [path for path, refs in paths if refs != sorted(refs)]}

    @classmethod
    def _initial_reply_entry(cls, raw):
        encoding, raw_hash = cls._raw_reply_encoding_and_hash(raw)
        entry = {**cls.reply_ref_order_binding(), "raw_encoding": encoding,
            "raw_reply_sha256": raw_hash, "raw_reply": deepcopy(raw),
            "parsed_reply_sha256": None, "normalized_reply": None,
            "normalized_reply_sha256": None, "changed_ref_paths": [],
            "normalization_status": "not_completed"}
        try:
            entry["parsed_reply_sha256"] = canonical_sha256(cls._decode_external_reply(raw))
        except Exception:
            pass
        return entry

    @classmethod
    def _validate_reply_entry_receipt(cls, entry, *, invocation):
        """Verify bound evidence without upgrading or reinterpreting old replies."""
        binding = cls._validated_reply_ref_order_binding(
            invocation.deterministic_validation_facts.get(cls.REPLY_REF_ORDER_BINDING_KEY))
        fields = {"normalizer_version", "normalizer_profile_sha256", "raw_encoding", "raw_reply_sha256",
            "raw_reply", "parsed_reply_sha256", "normalized_reply", "normalized_reply_sha256",
            "changed_ref_paths", "normalization_status"}
        if (not isinstance(entry, dict) or set(entry) not in (fields, fields | {"normalization_error_type"})
                or {key: entry[key] for key in binding} != binding):
            raise TargetClaimVerifierError("Verifier reply-entry receipt is missing or has another version")
        encoding, raw_hash = cls._raw_reply_encoding_and_hash(entry["raw_reply"])
        if (entry["raw_encoding"], entry["raw_reply_sha256"]) != (encoding, raw_hash):
            raise TargetClaimVerifierError("Verifier raw reply hash differs from receipt")
        try:
            decoded = cls._decode_external_reply(entry["raw_reply"])
        except Exception:
            decoded = None
        parsed_hash = canonical_sha256(decoded) if decoded is not None else None
        if entry["parsed_reply_sha256"] != parsed_hash:
            raise TargetClaimVerifierError("Verifier parsed raw reply hash differs from receipt")
        if entry["normalization_status"] == "not_completed":
            if (entry["normalized_reply"] is not None or entry["normalized_reply_sha256"] is not None
                    or entry["changed_ref_paths"] != [] or type(entry.get("normalization_error_type")) is not str):
                raise TargetClaimVerifierError("Verifier failed reply-entry metadata is inconsistent")
            if decoded is not None and entry["normalization_error_type"] != "VerifierProviderResponseFailure":
                try:
                    cls._reply_reference_paths(decoded)
                except ValueError:
                    pass
                else:
                    raise TargetClaimVerifierError("Verifier failed normalization contradicts valid reference arrays")
            return None
        if entry["normalization_status"] != "completed" or "normalization_error_type" in entry:
            raise TargetClaimVerifierError("Verifier reply-entry normalization status is invalid")
        normalized = entry["normalized_reply"]
        if type(normalized) is not dict or decoded is None:
            raise TargetClaimVerifierError("Verifier completed normalization lacks JSON objects")
        if entry["normalized_reply_sha256"] != canonical_sha256(normalized):
            raise TargetClaimVerifierError("Verifier normalized reply hash differs from receipt")
        original_paths = cls._reply_reference_paths(decoded)
        normalized_paths = cls._reply_reference_paths(normalized)
        if (len(original_paths) != len(normalized_paths)
                or any(path != next_path or sorted(refs) != next_refs
                    for (path, refs), (next_path, next_refs) in zip(original_paths, normalized_paths))
                or entry["changed_ref_paths"] != [path for path, refs in original_paths if refs != sorted(refs)]):
            raise TargetClaimVerifierError("Verifier normalization changed reference members or paths")
        original_order = deepcopy(normalized)
        original_order["positive_basis_refs"] = decoded["positive_basis_refs"]
        for index, issue in enumerate(decoded["issues"]):
            original_order["issues"][index]["source_refs"] = issue["source_refs"]
        if original_order != decoded:
            raise TargetClaimVerifierError("Verifier normalization changed other reply fields")
        return normalized

    @classmethod
    def _recorded_reply_entry(cls, result, *, invocation):
        if cls.REPLY_REF_ORDER_BINDING_KEY not in invocation.deterministic_validation_facts:
            if "reply_entry" in result or "reply_entry_available" in result:
                raise TargetClaimVerifierError("historical strict Verifier receipt cannot acquire new reply-entry metadata")
            return None
        cls._validated_reply_ref_order_binding(invocation.deterministic_validation_facts[cls.REPLY_REF_ORDER_BINDING_KEY])
        if "reply_entry" not in result or type(result.get("reply_entry_available")) is not bool:
            raise TargetClaimVerifierError("Verifier receipt is missing its bound reply-entry evidence")
        entry = result["reply_entry"]
        if result["reply_entry_available"] is False:
            if entry is not None or result.get("outcome") != "failed":
                raise TargetClaimVerifierError("Verifier receipt without a response cannot contain reply-entry semantics")
            return None
        normalized = cls._validate_reply_entry_receipt(entry, invocation=invocation)
        if result.get("outcome") == "completed" and normalized is None:
            raise TargetClaimVerifierError("Verifier completed receipt requires completed reply-entry evidence")
        return normalized

    @classmethod
    def _parse_candidate_strict(cls, raw, *, invocation, occurrence, receipt_ref):
        reply = VerifierModelReply.model_validate(json.loads(raw, object_pairs_hook=_strict_object) if isinstance(raw, str) else raw)
        if not reply.rationale.strip() or any(not item.message.strip() or not item.issue_code.strip() for item in reply.issues):
            raise ValueError("Verifier explanation cannot be blank")
        issues = tuple(VerifierSemanticIssueCandidateSchema(
            issue_slot=f"claim-issue-{index}", issue_code=item.issue_code, severity=item.severity,
            semantic_category="claim_validity", message_hash=canonical_sha256(item.message), rationale_ref=receipt_ref,
            source_refs=item.source_refs,
        ) for index, item in enumerate(reply.issues))
        candidate = VerifierSemanticResultCandidateSchema(
            **invocation.model_dump(exclude={"schema_version"}), positive_basis_refs=reply.positive_basis_refs,
            recommended_verdict=reply.verdict,
            semantic_rationale_hash=canonical_sha256(reply.rationale), semantic_rationale_ref=receipt_ref, issue_candidates=issues,
            verification_occurrence_ref=occurrence,
        )
        return candidate, reply.model_dump(mode="json")

    @classmethod
    def parse_candidate(cls, raw, *, invocation, occurrence, receipt_ref):
        if cls.REPLY_REF_ORDER_BINDING_KEY in invocation.deterministic_validation_facts:
            cls._validated_reply_ref_order_binding(invocation.deterministic_validation_facts[cls.REPLY_REF_ORDER_BINDING_KEY])
            raw, _metadata = cls.normalize_reply_reference_order(raw)
        # An absent binding is the exact historical strict parser, never today's
        # normalizer applied retroactively to a saved success or failure.
        return cls._parse_candidate_strict(raw, invocation=invocation, occurrence=occurrence, receipt_ref=receipt_ref)

    def _read(self, ref, identity):
        try:
            return self.store.get_json(ref, **identity)
        except FileNotFoundError:
            return None

    @staticmethod
    def _physical(invocation):
        key = canonical_sha256({"contract": "verifier-physical-occurrence-v2",
            "verifier_semantic_input_sha256": invocation.verifier_semantic_input_sha256})
        return key, "claim-verification-v2://sha256/" + key, f"artifact://canonical-verifier-v2/{key}"

    def _artifact_identity(self, invocation):
        return {"task_id": self.claim_runtime.lifecycle_service.load_run_identity(invocation.active_lineage.run_id).task_id,
            "run_id": invocation.active_lineage.run_id, "section_id": invocation.claim_occurrence_ref.section_id}

    def _exact_artifact(self, ref, invocation, artifact_type, schema_version):
        reference, payload = self.store.get_json_with_reference(ref, **self._artifact_identity(invocation))
        if (reference.artifact_type, reference.schema_version) != (artifact_type, schema_version):
            raise TargetClaimVerifierError("Verifier immutable Artifact metadata differs from its exact physical identity")
        return payload

    @staticmethod
    def _build_correction_input(authorization):
        """Pure prospective identity; this never appends a grant or reserves Budget."""
        data = authorization.root.original_input.model_dump(mode="json")
        facts = data["deterministic_validation_facts"]
        facts.pop(VERIFIER_LOCAL_REGISTRATION_KEY)
        facts[VERIFIER_LOCAL_CORRECTION_KEY] = build_verifier_correction_binding(authorization).model_dump(mode="json")
        data["verifier_semantic_input_sha256"] = build_verifier_semantic_input_sha256(data)
        return VerifierClaimInputSchema.model_validate(data)

    @classmethod
    def _bound_instruction_version(cls, invocation):
        """Read the version already committed to this exact Verifier input."""
        profile_fingerprint = invocation.deterministic_validation_facts.get("verifier_prompt_profile_sha256")
        if profile_fingerprint == cls.prompt_profile_sha256():
            return cls.INSTRUCTION_VERSION
        if profile_fingerprint == cls.prompt_profile_sha256(strict_decision=True):
            return cls.STRICT_DECISION_INSTRUCTION_VERSION
        if profile_fingerprint == cls.prompt_profile_sha256(strict_decision=True, semantic_v4=True):
            return cls.STRICT_SEMANTIC_INSTRUCTION_VERSION
        raise TargetClaimVerifierError("Verifier request has unrecognized immutable instruction profile")

    def _model_request(self, invocation, package, *, occurrence, output_limit, task_id, section_id):
        key, _, _ = self._physical(invocation)
        lineage = invocation.active_lineage
        instruction_version = self._bound_instruction_version(invocation)
        request = ModelRequestSchema(model_call_id="model_call_verifier_" + key, task_id=task_id, run_id=lineage.run_id,
            caller_agent="VerifierAgent", model_role=ModelRole.GENERAL.value, prompt="",
            messages=package.model_input.final_input_material.inline_final_input_payload["messages"],
            max_tokens=output_limit, temperature=0.0, created_at=datetime.now(timezone.utc).isoformat(),
            extra={"call_purpose": "claim_verification", "response_contract": VERIFIER_RESPONSE_CONTRACT,
                self.REPLY_REF_ORDER_BINDING_KEY: invocation.deterministic_validation_facts[self.REPLY_REF_ORDER_BINDING_KEY],
                "section_id": section_id, "attempt_id": lineage.attempt_id,
                "attempt_scope_ref": lineage.attempt_scope_ref, "logical_operation_id": occurrence,
                "input_snapshot_version": lineage.input_snapshot_version,
                "prompt_id": VERIFIER_RESPONSE_CONTRACT, "prompt_version": instruction_version,
                "llm_context_summary": {"managed": True, "package_id": package.package_ref.context_package_id,
                    "context_sha256": package.model_input.final_model_input_sha256}})
        if VERIFIER_LOCAL_CORRECTION_KEY in invocation.deterministic_validation_facts:
            binding = VerifierLocalCorrectionBindingV1.model_validate(invocation.deterministic_validation_facts[VERIFIER_LOCAL_CORRECTION_KEY])
            request.extra.update(verifier_correction_ordinal=1, verifier_original_root_ref=binding.authorization.root.original_root_ref)
        return request

    def _budget_owner(self, request, *, required):
        runtime = getattr(self.model_gateway, "budget_runtime", None)
        manager = getattr(runtime, "budget_manager", None)
        if manager is None and callable(getattr(runtime, "resolve_existing_budget_manager", None)):
            # The public existing-only seam must not bootstrap missing billing.
            manager = runtime.resolve_existing_budget_manager(request.task_id, request.run_id)
        elif manager is None and callable(getattr(runtime, "provider", None)):
            raise TargetClaimVerifierError("Verifier Router lacks existing-only canonical Budget read seam",
                error_code="VERIFIER_BUDGET_SETTLEMENT_UNKNOWN", failure_category=FailureCategory.UNKNOWN)
        if manager is None:
            if required:
                raise TargetClaimVerifierError("Verifier correction requires canonical Budget owner",
                    error_code="VERIFIER_BUDGET_SETTLEMENT_UNKNOWN", failure_category=FailureCategory.UNKNOWN)
            return None
        if (not getattr(manager, "_canonical_mode", False)
                or (str(manager.task_id), str(manager.run_id)) != (request.task_id, request.run_id)):
            raise TargetClaimVerifierError("Verifier response Budget owner differs from exact Task/Run",
                error_code="VERIFIER_BUDGET_SETTLEMENT_UNKNOWN", failure_category=FailureCategory.UNKNOWN)
        return manager

    def _validate_response_settlement(self, request, response, *, required):
        """Read the native owner; a response wrapper flag never proves settlement."""
        try:
            if (response.model_call_id, response.task_id, response.run_id) != (request.model_call_id, request.task_id, request.run_id):
                raise ValueError("response invocation differs from request")
            manager = self._budget_owner(request, required=required)
            if manager is None:
                return True  # Existing owner-only/component initial paths remain compatible.
            metadata = response.metadata
            logical_id = metadata["canonical_budget_logical_reservation_id"]
            if "canonical_budget_token_reservation_id" not in metadata:
                # Preserve the original logical lookup before the missing-key
                # error, including its integrity/recovery and error precedence.
                manager.resolve_reservation(logical_id)
                metadata["canonical_budget_token_reservation_id"]
            state, (logical, tokens) = manager.read_state_with_reservations(
                (logical_id, metadata["canonical_budget_token_reservation_id"]))
            scope = str(request.extra.get("section_id") or f"run:{request.run_id}")
            attempt = str(request.extra["attempt_id"]) if request.extra.get("attempt_id") else None
            for reservation, kind in ((logical, "logical_model_call"), (tokens, "model_provider_attempt_tokens")):
                ref = reservation.reservation_ref
                if ((ref.task_id, ref.run_id, ref.scope_ref, ref.attempt_id, ref.reservation_kind)
                        != (request.task_id, request.run_id, scope, attempt, kind)
                        or reservation.status != "consumed" or reservation.terminal_budget_revision is None):
                    raise ValueError("native reservation identity or consumed status differs")
            if (logical.reservation_ref.owner_occurrence_ref != request.model_call_id
                    or logical.reservation_ref.operation_ref != str(request.extra.get("logical_operation_id") or request.model_call_id)
                    or tokens.reservation_ref.operation_ref != request.model_call_id):
                raise ValueError("native reservation does not bind exact logical/physical occurrence")
            index = metadata.get("model_candidate_index")
            if (type(index) is not int or index < 0
                    or re.fullmatch(re.escape(f"provider_attempt_{request.model_call_id}_{index + 1}_") + r"[0-9a-f]{12}",
                        tokens.reservation_ref.owner_occurrence_ref) is None):
                raise ValueError("native token reservation differs from exact Gateway physical Provider ordinal")
            logical_requested, token_requested = logical.requested.model_dump(), tokens.requested.model_dump()
            caller_allowance = CanonicalInvocationBudgetAdapter.provider_token_allowance(request)
            minimum_allowance = CanonicalInvocationBudgetAdapter.provider_token_allowance(request.model_copy(update={"max_tokens": 1}))
            requested_tokens = token_requested.pop("model_tokens_total")
            if (logical_requested.pop("logical_model_calls") != 1 or any(logical_requested.values())
                    or not minimum_allowance <= requested_tokens <= caller_allowance or any(token_requested.values())):
                raise ValueError("native requested allowance differs from exact frozen request")
            if any(item.status == "active" and item.reservation_ref.reservation_kind == "model_provider_attempt_tokens"
                    and item.reservation_ref.operation_ref == request.model_call_id for item in state.reservations):
                raise ValueError("unreconciled native Provider attempt is still held")
            actual_tokens = (max(0, int(response.token_usage.total_tokens))
                if metadata.get("usage_complete") is not False and response.token_usage.total_tokens is not None
                else max(int(tokens.requested.model_tokens_total), max(0,
                    int(response.token_usage.prompt_tokens or 0) + int(response.token_usage.completion_tokens or 0))))
            logical_actual, token_actual = logical.actual.model_dump(), tokens.actual.model_dump()
            if (logical_actual.pop("logical_model_calls") != 1 or any(logical_actual.values())
                    or token_actual.pop("model_tokens_total") != actual_tokens or any(token_actual.values())):
                raise ValueError("native actual usage differs from exact response settlement")
            return True
        except Exception as exc:
            raise TargetClaimVerifierError("Verifier response native Budget settlement is unknown: " + str(exc),
                error_code="VERIFIER_BUDGET_SETTLEMENT_UNKNOWN", failure_category=FailureCategory.UNKNOWN) from exc

    def _complete_response(self, invocation, *, require_canonical_budget=False):
        """Foreign-owner read used both by replay and M6's identity callback."""
        key, occurrence, prefix = self._physical(invocation)
        prior = self._exact_artifact(prefix + "/input", invocation, "canonical_verifier_input", "canonical_verifier_input_v2")
        raw_request = self._exact_artifact(prefix + "/request", invocation, "canonical_verifier_request", "canonical_verifier_request_v1")
        wrapper = self._exact_artifact(prefix + "/response", invocation, "canonical_verifier_response", "canonical_verifier_response_v1")
        if (not isinstance(prior, dict) or prior.get("schema_version") != "canonical_verifier_input_v2"
                or prior.get("input_fingerprint") != invocation.verifier_semantic_input_sha256
                or prior.get("input_material", {}).get("invocation") != invocation.model_dump(mode="json")
                or prior.get("request_ref") != prefix + "/request" or prior.get("request") != raw_request):
            raise TargetClaimVerifierError("Verifier response lacks its exact immutable input/request chain")
        if (not isinstance(wrapper, dict)
                or set(wrapper) != {"schema_version", "input_fingerprint", "request_ref", "request_sha256", "response", "budget_settlement_confirmed"}
                or wrapper["schema_version"] != "canonical_verifier_response_v1"
                or wrapper["input_fingerprint"] != invocation.verifier_semantic_input_sha256
                or wrapper["request_ref"] != prefix + "/request"
                or wrapper["request_sha256"] != canonical_sha256(raw_request)
                or wrapper["budget_settlement_confirmed"] is not True):
            raise TargetClaimVerifierError("Verifier durable response chain is incomplete or unsettled",
                error_code="VERIFIER_INDETERMINATE_EXECUTION", failure_category=FailureCategory.UNKNOWN)
        request, response = ModelRequestSchema.model_validate(raw_request), ModelResponseSchema.model_validate(wrapper["response"])
        package = ContextPackageSchema.model_validate(prior["context_package"])
        expected_extra = {"section_id": invocation.claim_occurrence_ref.section_id,
            "attempt_id": invocation.active_lineage.attempt_id, "attempt_scope_ref": invocation.active_lineage.attempt_scope_ref,
            "logical_operation_id": occurrence, "input_snapshot_version": invocation.active_lineage.input_snapshot_version,
            "call_purpose": "claim_verification", "response_contract": VERIFIER_RESPONSE_CONTRACT,
            "prompt_id": VERIFIER_RESPONSE_CONTRACT, "prompt_version": self._bound_instruction_version(invocation),
            self.REPLY_REF_ORDER_BINDING_KEY: invocation.deterministic_validation_facts[self.REPLY_REF_ORDER_BINDING_KEY]}
        if VERIFIER_LOCAL_CORRECTION_KEY in invocation.deterministic_validation_facts:
            child_binding = VerifierLocalCorrectionBindingV1.model_validate(invocation.deterministic_validation_facts[VERIFIER_LOCAL_CORRECTION_KEY])
            expected_extra.update(verifier_correction_ordinal=1, verifier_original_root_ref=child_binding.authorization.root.original_root_ref)
        if (request.model_call_id != "model_call_verifier_" + key
                or request.caller_agent != "VerifierAgent" or request.model_role != ModelRole.GENERAL.value
                or request.model_name is not None or request.prompt != "" or request.system_prompt is not None or request.temperature != 0.0
                or (request.task_id, request.run_id) != (self._artifact_identity(invocation)["task_id"], invocation.active_lineage.run_id)
                or any(request.extra.get(name) != value for name, value in expected_extra.items())
                or package.invocation_subject.invocation_id != occurrence
                or request.messages != package.model_input.final_input_material.inline_final_input_payload["messages"]
                or request.max_tokens != invocation.deterministic_validation_facts[self.OUTPUT_CAP_BINDING_KEY]["effective_max_output_tokens"]
                or request.extra.get("llm_context_summary") != {"managed": True,
                    "package_id": package.package_ref.context_package_id,
                    "context_sha256": package.model_input.final_model_input_sha256}):
            raise TargetClaimVerifierError("Verifier saved request differs from bound Context/occurrence")
        self._validate_response_settlement(request, response, required=require_canonical_budget)
        return prior, request, response

    def _rejection_chain(self, invocation, candidate):
        _, occurrence, prefix = self._physical(invocation)
        _, request, response = self._complete_response(invocation, require_canonical_budget=True)
        return VerifierRecoveryArtifactChainV1(input_ref=prefix + "/input",
            input_sha256=canonical_sha256(invocation.model_dump(mode="json")),
            request_ref=prefix + "/request", request_sha256=canonical_sha256(request.model_dump(mode="json")),
            response_ref=prefix + "/response", response_sha256=canonical_sha256(response.model_dump(mode="json")),
            candidate_ref=prefix + "/candidate", candidate_sha256=canonical_sha256(candidate.model_dump(mode="json")),
            physical_occurrence=occurrence)

    def _orphan_child_budget(self, authorization):
        child = self._build_correction_input(authorization)
        key, occurrence, _ = self._physical(child)
        identity = self._artifact_identity(child)
        probe = SimpleNamespace(task_id=identity["task_id"], run_id=identity["run_id"])
        manager = self._budget_owner(probe, required=True)
        return any((item.reservation_ref.reservation_kind == "logical_model_call"
                    and item.reservation_ref.owner_occurrence_ref == "model_call_verifier_" + key
                    and item.reservation_ref.operation_ref == occurrence)
                or (item.reservation_ref.reservation_kind == "model_provider_attempt_tokens"
                    and item.reservation_ref.operation_ref == "model_call_verifier_" + key)
            for item in manager.canonical_state().reservations)

    def _reject_changed_historical_occurrence(self, invocation, identity):
        """Probe immutable, exact-owner intents; never replay a changed profile.

        The existing artifact owner's integrity reads and this composition's
        existing physical-occurrence fence apply. No global/latest authority or
        new recovery path is introduced.
        """
        base_hash = self._binding_only_historical_input(invocation).verifier_semantic_input_sha256
        for reference in self.store.list_references():
            if (not reference.artifact_ref.startswith("artifact://canonical-verifier-v2/")
                    or not reference.artifact_ref.endswith("/input")):
                continue
            receipt = self.store.get_json(reference.artifact_ref, task_id=reference.task_id,
                run_id=reference.run_id, section_id=reference.section_id)
            recorded = VerifierClaimInputSchema.model_validate(receipt.get("input_material", {}).get("invocation"))
            if build_verifier_registration_scope_sha256(recorded) != build_verifier_registration_scope_sha256(invocation):
                continue
            self._exact_artifact(reference.artifact_ref, invocation, "canonical_verifier_input", "canonical_verifier_input_v2")
            if (reference.schema_version != "canonical_verifier_input_v2"
                    or not isinstance(receipt, dict)
                    or receipt.get("schema_version") != "canonical_verifier_input_v2"):
                raise TargetClaimVerifierError("historical Verifier input receipt requires V2")
            material = receipt.get("input_material")
            if not isinstance(material, dict):
                raise TargetClaimVerifierError("historical Verifier receipt lacks typed invocation material")
            stored = VerifierClaimInputSchema.model_validate(material.get("invocation"))
            if self.OUTPUT_CAP_BINDING_KEY in stored.deterministic_validation_facts:
                self._validated_output_cap_binding(stored.deterministic_validation_facts[self.OUTPUT_CAP_BINDING_KEY])
            if self.REPLY_REF_ORDER_BINDING_KEY in stored.deterministic_validation_facts:
                self._validated_reply_ref_order_binding(stored.deterministic_validation_facts[self.REPLY_REF_ORDER_BINDING_KEY])
            stored_key = canonical_sha256({"contract": "verifier-physical-occurrence-v2",
                "verifier_semantic_input_sha256": stored.verifier_semantic_input_sha256})
            if (receipt.get("input_fingerprint") != stored.verifier_semantic_input_sha256
                    or reference.artifact_ref != f"artifact://canonical-verifier-v2/{stored_key}/input"
                    or stored.claim_occurrence_ref.run_id != identity["run_id"]
                    or stored.claim_occurrence_ref.section_id != identity["section_id"]):
                raise TargetClaimVerifierError("historical Verifier receipt contradicts its typed identity")
            if (stored.claim_ref != invocation.claim_ref
                    or stored.claim_occurrence_ref != invocation.claim_occurrence_ref
                    or stored.paragraph_version_ref != invocation.paragraph_version_ref
                    or (stored.active_lineage.run_id, stored.active_lineage.attempt_id,
                        stored.active_lineage.attempt_scope_ref, stored.active_lineage.input_snapshot_version)
                    != (invocation.active_lineage.run_id, invocation.active_lineage.attempt_id,
                        invocation.active_lineage.attempt_scope_ref, invocation.active_lineage.input_snapshot_version)):
                continue
            if (self._binding_only_historical_input(stored).verifier_semantic_input_sha256 == base_hash
                    and stored.verifier_semantic_input_sha256 != invocation.verifier_semantic_input_sha256):
                raise TargetClaimVerifierError(
                    "historical Verifier occurrence exists; changed prompt/output cap/reply entry cannot redispatch its Claim/Attempt",
                    error_code="VERIFIER_HISTORICAL_PROFILE_OCCURRENCE", failure_category=FailureCategory.UNKNOWN)

    def _select_initial(self, invocation, identity):
        """Called inside the stable registration scope fence, before intent publication."""
        scope = build_verifier_registration_scope_sha256(invocation)
        selected = None
        for reference in self.store.list_references():
            if (not reference.artifact_ref.startswith("artifact://canonical-verifier-v2/")
                    or not reference.artifact_ref.endswith("/input")):
                continue
            receipt = self.store.get_json(reference.artifact_ref, task_id=reference.task_id,
                run_id=reference.run_id, section_id=reference.section_id)
            if not isinstance(receipt, dict) or receipt.get("schema_version") != "canonical_verifier_input_v2":
                raise TargetClaimVerifierError("historical Verifier input receipt requires V2")
            stored = VerifierClaimInputSchema.model_validate(receipt.get("input_material", {}).get("invocation"))
            if build_verifier_registration_scope_sha256(stored) != scope:
                continue
            self._exact_artifact(reference.artifact_ref, invocation, "canonical_verifier_input", "canonical_verifier_input_v2")
            _, _, prefix = self._physical(stored)
            if reference.artifact_ref != prefix + "/input" or receipt.get("input_fingerprint") != stored.verifier_semantic_input_sha256:
                raise TargetClaimVerifierError("Verifier existing initial intent contradicts its physical identity")
            if VERIFIER_LOCAL_CORRECTION_KEY in stored.deterministic_validation_facts:
                continue
            if selected is not None and selected != stored:
                raise TargetClaimVerifierError("Verifier registration scope already has multiple initial roots")
            selected = stored
        if selected is None:
            return invocation
        if VERIFIER_LOCAL_REGISTRATION_KEY in selected.deterministic_validation_facts:
            if selected != invocation:
                raise TargetClaimVerifierError("Verifier registration scope already binds a different initial input",
                    error_code="VERIFIER_REGISTERED_SCOPE_COLLISION", failure_category=FailureCategory.UNKNOWN)
        else:
            # Rebuild recorded absence through the original identity; never add
            # today's registration or instruction-entry facts to historical bytes.
            data = invocation.model_dump(mode="json")
            facts = data["deterministic_validation_facts"]
            facts.pop(VERIFIER_LOCAL_REGISTRATION_KEY, None)
            for key in (self.OUTPUT_CAP_BINDING_KEY, self.REPLY_REF_ORDER_BINDING_KEY, "verifier_prompt_profile_sha256"):
                if key not in selected.deterministic_validation_facts:
                    facts.pop(key, None)
            data["verifier_semantic_input_sha256"] = build_verifier_semantic_input_sha256(data)
            if VerifierClaimInputSchema.model_validate(data) != selected:
                raise TargetClaimVerifierError("historical Verifier initial scope cannot acquire changed identity",
                    error_code="VERIFIER_HISTORICAL_PROFILE_OCCURRENCE", failure_category=FailureCategory.UNKNOWN)
        return selected

    def _prepare_execution(self, invocation, material, claim, binding, support, context):
        key, occurrence, prefix = self._physical(invocation)
        identity = self._artifact_identity(invocation)
        prior, result = self._read(prefix + "/input", identity), self._read(prefix + "/candidate", identity)
        def validate():
            rebuilt = (self._material(claim, binding, support, context)
                if VERIFIER_LOCAL_REGISTRATION_KEY in invocation.deterministic_validation_facts
                else self._material(claim, binding, support, context, _recorded_invocation=invocation))
            if rebuilt[1] != material:
                raise TargetClaimVerifierError("Verifier input changed during execution")
        if prior is not None:
            if prior.get("schema_version") != "canonical_verifier_input_v2":
                raise TargetClaimVerifierError("Verifier input receipt requires V2")
            if (prior.get("input_material") != material
                    or prior.get("input_fingerprint") != invocation.verifier_semantic_input_sha256):
                raise TargetClaimVerifierError("Verifier receipt input identity mismatch")
            package = ContextPackageSchema.model_validate(prior["context_package"])
            if package.invocation_subject.invocation_id != occurrence:
                raise TargetClaimVerifierError("Verifier saved Context targets another occurrence")
            request = ModelRequestSchema.model_validate(prior["request"]) if "request" in prior else None
            if result is None and self._read(prefix + "/response", identity) is None:
                raise TargetClaimVerifierError("Verifier intent exists without durable response; no automatic redispatch",
                    error_code="VERIFIER_INDETERMINATE_EXECUTION", failure_category=FailureCategory.UNKNOWN)
            return prior, result, request, validate, False
        if result is not None or self._read(prefix + "/request", identity) is not None or self._read(prefix + "/response", identity) is not None:
            raise TargetClaimVerifierError("Verifier immutable execution chain lacks its original intent",
                error_code="VERIFIER_INDETERMINATE_EXECUTION", failure_category=FailureCategory.UNKNOWN)
        if self.claim_runtime.resolve_claim_head(claim.claim_ref) is not None:
            raise TargetClaimVerifierError("existing Claim revision requires explicit re-verification authorization")
        child = VERIFIER_LOCAL_CORRECTION_KEY in invocation.deterministic_validation_facts
        if child:
            self._budget_owner(SimpleNamespace(**{name: identity[name] for name in ("task_id", "run_id")}), required=True)
            self.claim_runtime.validate_verifier_correction_dispatch(invocation, context)
        else:
            self._reject_changed_historical_occurrence(invocation, identity)
        package, output_limit = self._package(material, occurrence=occurrence, invocation=invocation, validate=validate)
        request = self._model_request(invocation, package, occurrence=occurrence, output_limit=output_limit,
            task_id=identity["task_id"], section_id=claim.section_id)
        raw_request = request.model_dump(mode="json")
        prior = {"schema_version": "canonical_verifier_input_v2", "input_material": material,
            "input_fingerprint": invocation.verifier_semantic_input_sha256, "context_package": package.model_dump(mode="json"),
            "request_ref": prefix + "/request", "request": raw_request}
        self.store.put_json(prior, artifact_ref=prefix + "/input", artifact_type="canonical_verifier_input",
            schema_version="canonical_verifier_input_v2", created_by="canonical_verifier_composition", **identity)
        self.store.put_json(raw_request, artifact_ref=prefix + "/request", artifact_type="canonical_verifier_request",
            schema_version="canonical_verifier_request_v1", created_by="canonical_verifier_composition", **identity)
        return prior, None, request, validate, True

    def _execute_prepared(self, invocation, prepared, context):
        prior, result, request, validate, fresh = prepared
        _, occurrence, prefix = self._physical(invocation)
        fingerprint, result_ref = invocation.verifier_semantic_input_sha256, prefix + "/candidate"
        identity = self._artifact_identity(invocation)
        child = VERIFIER_LOCAL_CORRECTION_KEY in invocation.deterministic_validation_facts
        if result is not None:
            if result.get("schema_version") != "canonical_verifier_candidate_v2":
                raise TargetClaimVerifierError("Verifier candidate receipt requires V2")
            if result.get("input_fingerprint") != fingerprint:
                raise TargetClaimVerifierError("Verifier candidate lacks exact V2 input receipt")
            if result.get("outcome") == "failed":
                self._recorded_reply_entry(result, invocation=invocation)
                failure = result["failure"]
                raise TargetClaimVerifierError(failure["message"], error_code=failure["error_code"],
                    failure_category=failure["category"], failure_origin=failure["origin"])
            if result.get("outcome") != "completed":
                raise TargetClaimVerifierError("Verifier result outcome is invalid")
            normalized = self._recorded_reply_entry(result, invocation=invocation)
            if normalized is not None and VerifierModelReply.model_validate(normalized).model_dump(mode="json") != result["semantic_result"]:
                raise TargetClaimVerifierError("Verifier saved semantics differ from bound normalized reply")
            candidate, _ = self._parse_candidate_strict(result["semantic_result"], invocation=invocation,
                occurrence=occurrence, receipt_ref=result_ref)
            if candidate.model_dump(mode="json") != result["candidate"]:
                raise TargetClaimVerifierError("Verifier candidate contradicts saved semantics")
            if request is not None:
                _, _, response = self._complete_response(invocation, require_canonical_budget=child)
                exact_candidate, _ = self.parse_candidate(response.content, invocation=invocation, occurrence=occurrence, receipt_ref=result_ref)
                if exact_candidate != candidate:
                    raise TargetClaimVerifierError("Verifier candidate contradicts its durable raw response")
            validate()
            return candidate
        semantic_result = reply_entry = None
        def evaluate(_invocation):
            nonlocal semantic_result, reply_entry
            validate()
            checkpoint_current_execution()
            if fresh:
                if child:
                    self.claim_runtime.validate_verifier_correction_dispatch(invocation, context)
                response = self.model_gateway.generate(request)
                settlement_error = None
                try:
                    self._validate_response_settlement(request, response, required=child)
                except Exception as exc:
                    settlement_error = exc
                # Always preserve the full response before parser/currentness work.
                self.store.put_json({"schema_version": "canonical_verifier_response_v1", "input_fingerprint": fingerprint,
                    "request_ref": prefix + "/request", "request_sha256": canonical_sha256(request.model_dump(mode="json")),
                    "response": response.model_dump(mode="json"), "budget_settlement_confirmed": settlement_error is None},
                    artifact_ref=prefix + "/response", artifact_type="canonical_verifier_response",
                    schema_version="canonical_verifier_response_v1", created_by="canonical_verifier_composition", **identity)
                if settlement_error is not None:
                    raise settlement_error
            else:
                _, _, response = self._complete_response(invocation, require_canonical_budget=child)
            reply_entry = self._initial_reply_entry(response.content)
            if not response.success:
                reply_entry["normalization_error_type"] = "VerifierProviderResponseFailure"
                error = response.error
                raise TargetClaimVerifierError(error.message if error else (response.error_message or "Verifier provider failed"),
                    error_code=error.error_code if error else "VERIFIER_PROVIDER_FAILED",
                    failure_category=error.failure_category if error else FailureCategory.UNKNOWN, failure_origin=response.failure_origin)
            try:
                if self.REPLY_REF_ORDER_BINDING_KEY in invocation.deterministic_validation_facts:
                    normalized, entry_metadata = self.normalize_reply_reference_order(response.content)
                    reply_entry.update(entry_metadata)
                else:
                    normalized = self._decode_external_reply(response.content)
            except Exception as exc:
                reply_entry["normalization_error_type"] = type(exc).__name__
                raise
            reply_entry.update(normalized_reply=normalized, normalization_status="completed")
            candidate, semantic_result = self._parse_candidate_strict(normalized,
                invocation=invocation, occurrence=occurrence, receipt_ref=result_ref)
            validate()
            checkpoint_current_execution()
            return candidate
        try:
            candidate = VerifierAgent.evaluate_canonical_claim(invocation, evaluator=evaluate)
        except Exception as exc:
            budget = isinstance(exc, (CanonicalBudgetCapacityExceeded, CanonicalBudgetDeadlineExceeded, CanonicalBudgetDraining))
            category = FailureCategory.RESOURCE_EXHAUSTED if budget else classify_failure(exc)
            origin = FailureOrigin.CANONICAL_BUDGET if budget else getattr(exc, "failure_origin", FailureOrigin.UNKNOWN)
            failure = {"message": str(exc) or type(exc).__name__, "error_code": getattr(exc, "error_code", type(exc).__name__),
                "category": category.value, "origin": FailureOrigin(origin).value}
            self.store.put_json({"schema_version": "canonical_verifier_candidate_v2", "input_fingerprint": fingerprint,
                "outcome": "failed", "failure": failure, "reply_entry": reply_entry, "reply_entry_available": reply_entry is not None},
                artifact_ref=result_ref, artifact_type="canonical_verifier_candidate", schema_version="canonical_verifier_candidate_v2",
                created_by="canonical_verifier_composition", **identity)
            raise TargetClaimVerifierError(failure["message"], error_code=failure["error_code"], failure_category=category, failure_origin=origin) from exc
        self.store.put_json({"schema_version": "canonical_verifier_candidate_v2", "input_fingerprint": fingerprint,
            "outcome": "completed", "candidate": candidate.model_dump(mode="json"), "semantic_result": semantic_result,
            "reply_entry": reply_entry, "reply_entry_available": reply_entry is not None},
            artifact_ref=result_ref, artifact_type="canonical_verifier_candidate", schema_version="canonical_verifier_candidate_v2",
            created_by="canonical_verifier_composition", **identity)
        return candidate

    def _evaluate(self, claim, binding, support, context, *, _invocation=None, _root_fenced=False):
        invocation, material = (self._material(claim, binding, support, context) if _invocation is None
            else self._material(claim, binding, support, context, _recorded_invocation=_invocation))
        with self._lock, ExitStack() as root_fence:
            if _invocation is None:
                scope = build_verifier_registration_scope_sha256(invocation)
                with InterProcessFileLock(self.store.root / "claim_verifier_locks" / ("scope-" + scope + ".lock")):
                    selected = self._select_initial(invocation, self._artifact_identity(invocation))
                    if selected != invocation:
                        invocation, material = self._material(claim, binding, support, context, _recorded_invocation=selected)
                    key, _, _ = self._physical(invocation)
                    root_fence.enter_context(InterProcessFileLock(self.store.root / "claim_verifier_locks" / ("root-" + key + ".lock")))
                    if VERIFIER_LOCAL_REGISTRATION_KEY in invocation.deterministic_validation_facts:
                        # Only an existing intent can be resolved by M6 here.
                        _, _, prefix = self._physical(invocation)
                        if self._read(prefix + "/input", self._artifact_identity(invocation)) is not None:
                            self._require_active_recovery(build_verifier_original_root(invocation))
                    prepared = self._prepare_execution(invocation, material, claim, binding, support, context)
                # Scope fence is released before Provider I/O; root stays held.
            else:
                _, binding_fact = validate_verifier_local_recovery_facts(invocation)
                root = binding_fact.authorization.root
                key, _, _ = self._physical(root.original_input)
                if not _root_fenced:
                    root_fence.enter_context(InterProcessFileLock(self.store.root / "claim_verifier_locks" / ("root-" + key + ".lock")))
                self._require_active_recovery(root)
                prepared = self._prepare_execution(invocation, material, claim, binding, support, context)
            return self._execute_prepared(invocation, prepared, context)

    def _require_active_recovery(self, root):
        projection = self.claim_runtime.resolve_verifier_local_recovery(root)
        if projection["termination"] is not None:
            raise TargetClaimVerifierError("Verifier local correction is permanently terminated",
                error_code="VERIFIER_CORRECTION_TERMINATED", failure_category=FailureCategory.UNKNOWN)
        return projection

    def _terminate_failed_child(self, root, context, exc):
        cause = exc.__cause__ if isinstance(exc, ClaimCurrentnessError) and isinstance(exc.__cause__, TargetClaimVerifierError) else exc
        code = getattr(cause, "error_code", "")
        if getattr(cause, "failure_category", None) == FailureCategory.CANCELLED:
            reason = "CANCELLED"
        elif code in {"VERIFIER_INDETERMINATE_EXECUTION", "VERIFIER_BUDGET_SETTLEMENT_UNKNOWN"}:
            reason = "PROVIDER_OUTCOME_UNKNOWN"
        elif code == "VERIFIER_DURABLE_RESPONSE_INVALID":
            reason = "INVALID_RESPONSE"
        elif isinstance(exc, ClaimCurrentnessError):
            reason = "STALE"
        elif getattr(cause, "failure_category", None) == FailureCategory.UNKNOWN:
            reason = "PROVIDER_OUTCOME_UNKNOWN"
        elif getattr(cause, "failure_origin", None) == FailureOrigin.PROVIDER_RUNTIME:
            reason = "PROVIDER_FAILURE"
        else:
            reason = "INVALID_RESPONSE"
        try:
            self.claim_runtime.terminate_verifier_correction(root, reason, context)
        except Exception:
            # A cancelled/stale lifecycle can refuse its ordinary write fence;
            # the unresolved immutable execution still cannot redispatch.
            pass

    def _recover_candidate(self, candidate, claim, binding, support, context):
        invocation = self.claim_runtime.verifier_input_from_candidate(candidate)
        root = build_verifier_original_root(invocation)
        projection = self._require_active_recovery(root)
        rejection = next((item for item in projection["rejections"] if item.correction_ordinal == 0), None)
        grant = projection["authorization"]
        if rejection is None:
            raise TargetClaimVerifierError("Verifier correction lacks its original typed rejection")
        if grant is None:
            prior, request, _ = self._complete_response(invocation, require_canonical_budget=True)
            feedback = build_verifier_recovery_feedback(rejection.violation, prior["input_material"], request.messages)
            prospective = build_verifier_correction_authorization(root=root,
                rejection_ref=rejection.rejection_ref, rejection_sha256=rejection.rejection_sha256, feedback=feedback)
            if self._orphan_child_budget(prospective):
                self.claim_runtime.terminate_verifier_correction(root, "ORPHAN_RESERVATION", context)
                raise TargetClaimVerifierError("Verifier child Budget exists before its M6 correction grant",
                    error_code="VERIFIER_ORPHAN_RESERVATION", failure_category=FailureCategory.UNKNOWN)
            grant = self.claim_runtime.authorize_verifier_correction(rejection, context)
            if grant != prospective:
                raise TargetClaimVerifierError("Verifier M6 grant differs from deterministic original feedback")
        child_input = self._build_correction_input(grant)
        try:
            child = self._evaluate(claim, binding, support, context, _invocation=child_input, _root_fenced=True)
            return self.claim_runtime.commit_verification(claim.claim_ref, binding.binding_facts_ref,
                support.support_ref, child, None, context)
        except VerifierCandidateContractViolationError:
            second = self.claim_runtime.record_verifier_candidate_rejection(root, child_input, child,
                self._rejection_chain(child_input, child), context)
            self.claim_runtime.terminate_verifier_correction(root, "SECOND_CONTRACT_VIOLATION", context, rejection=second)
            raise TargetClaimVerifierError("Verifier correction returned a second non-admitted evidence basis",
                error_code="VERIFIER_CORRECTION_TERMINATED")
        except Exception as exc:
            self._terminate_failed_child(root, context, exc)
            raise

    def verify_claims(self, *, records, binding_records, support_records, operation_context):
        context = ClaimOperationContextSchema.model_validate(operation_context)
        if not (len(records) == len(binding_records) == len(support_records)):
            raise TargetClaimVerifierError("each Claim requires exactly one Binding and Support record")
        results = []
        for claim, binding, support in zip(records, binding_records, support_records):
            candidate = self._evaluate(claim, binding, support, context)
            invocation = self.claim_runtime.verifier_input_from_candidate(candidate)
            registered = VERIFIER_LOCAL_REGISTRATION_KEY in invocation.deterministic_validation_facts
            key, _, _ = self._physical(invocation)
            with self._lock, InterProcessFileLock(self.store.root / "claim_verifier_locks" / ("root-" + key + ".lock")):
                root = build_verifier_original_root(invocation) if registered else None
                projection = self._require_active_recovery(root) if root is not None else None
                if projection is not None and (projection["authorization"] is not None or projection["rejections"]):
                    results.append(self._recover_candidate(candidate, claim, binding, support, context))
                    continue
                try:
                    # Legal negative results commit directly through the unchanged M6 gate.
                    result = self.claim_runtime.commit_verification(claim.claim_ref, binding.binding_facts_ref,
                        support.support_ref, candidate, None, context)
                except VerifierCandidateContractViolationError:
                    if root is None:
                        raise  # Historical absence is never retrospective permission.
                    chain = self._rejection_chain(invocation, candidate)
                    self.claim_runtime.record_verifier_candidate_rejection(root, invocation, candidate, chain, context)
                    result = self._recover_candidate(candidate, claim, binding, support, context)
                results.append(result)
        return tuple(results)


def normalize_reply_reference_order(raw):
    """Verifier-specific pure entry normalization; never a generic answer repair."""
    return TargetClaimVerifierComposition.normalize_reply_reference_order(raw)


__all__ = ["TargetClaimVerifierComposition", "TargetClaimVerifierError", "VerifierModelReply", "VERIFIER_RESPONSE_CONTRACT", "admissible_positive_basis_refs", "normalize_reply_reference_order"]
