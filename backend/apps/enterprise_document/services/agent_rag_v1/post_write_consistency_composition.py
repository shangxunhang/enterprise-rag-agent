"""348 application composition for canonical Artifact Consistency.

Factual comparison units are projected only from the already-current SIF-07
Claim authority consumed by the 343 Post-write Claim projection.  This module
owns orchestration, managed semantic relation binding, persistence/replay and
Completion accounting; the existing post_write.consistency owners remain the
only assertion/plan/binding/evaluation/Fact semantics.
"""
from __future__ import annotations

from datetime import datetime, timezone
from hashlib import sha256
import json
from pathlib import Path
from threading import RLock
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

from apps.enterprise_document.config.post_write import load_post_write_config
from apps.enterprise_document.schemas.post_write.foundation import (
    ArtifactStateRefSchema,
    ExpectedItemExecutionAccountingSchema,
    ExpectedItemExecutionState,
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
    ArtifactConsistencyRelation,
    ConsistencyAssertionSchema,
    ConsistencyCandidateDiscoveryResultSchema,
    ConsistencyDiscoveryPlanSchema,
    ConsistencyPlanConformanceResultSchema,
    ConsistencyPlanConformanceState,
    ConsistencyRelationMateriality,
    ExactConsistencyEvaluationResultSchema,
    FormalExecutabilityResultSchema,
    FormalExecutabilityState,
    FormalRelationCandidateSchema,
    SemanticConsistencyEvaluationResultSchema,
    SemanticRelationBindingSchema,
    UpstreamSIF07ClaimAuthorityResolutionSchema,
    UpstreamSIF07ClaimAuthorityState,
)
from apps.enterprise_document.services.post_write.consistency import (
    ConsistencyAssertionMaterializer,
    ConsistencyCandidateDiscoveryExecutor,
    ConsistencyDiscoveryPlanner,
    ConsistencyPlanConformanceGuard,
    ConsistencyProjector,
    ExactConsistencyEvaluator,
    FormalExecutabilityGuard,
    SemanticConsistencyEvaluator,
)
from apps.enterprise_document.services.post_write.consistency_procedure import (
    ConsistencyProcedureInputResolver,
    ConsistencyProcedureLoweringSchema,
)
from apps.enterprise_document.services.post_write.execution import ValidatorInputEnvelopeBuilder
from apps.enterprise_document.services.post_write.shared_claim_support import ArtifactClaimTargetMaterializer
from core.hashing import canonical_sha256
from core.persistence.interprocess_lock import InterProcessFileLock
from core.retry_policy import classify_failure
from core.runtime.execution_control import checkpoint_current_execution
from core.token_estimator import DeterministicTokenEstimator
from model_gateway.model_contract import ModelRole
from schemas.context import (
    ContextInstructionMaterialSchema,
    ContextPackageSchema,
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
from schemas.model import ModelRequestSchema, ModelResponseSchema

from .budget_manager import (
    CanonicalBudgetCapacityExceeded,
    CanonicalBudgetDeadlineExceeded,
    CanonicalBudgetDraining,
)
from .context_runtime import ApplicationContextRuntime, ContextSourceProviderRegistration

CONSISTENCY_RELATION_CONTRACT = "artifact_consistency_relation_batch_v1"
CONSISTENCY_SHARED_RELATION_CONTRACT = "artifact_consistency_shared_relation_batch_v1"
CONSISTENCY_POLICY_ID = "post-write-artifact-consistency"
CONSISTENCY_POLICY_VERSION = "v1"


class ConsistencyCompositionError(ValueError):
    def __init__(
        self,
        message: str,
        *,
        error_code: str = "ARTIFACT_CONSISTENCY_FAILED",
        failure_category: FailureCategory = FailureCategory.VALIDATION,
        failure_origin: FailureOrigin = FailureOrigin.UNKNOWN,
    ) -> None:
        super().__init__(message)
        self.error_code = error_code
        self.failure_category = FailureCategory(failure_category)
        self.failure_origin = FailureOrigin(failure_origin)


class ConsistencyRelationReplyRow(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    member_assertion_refs: tuple[str, ...]
    relation_materiality: Literal["MATERIAL_RELATION", "NO_MATERIAL_RELATION", "INDETERMINATE"]
    relation: Literal["CONSISTENT", "INCONSISTENT", "INDETERMINATE"] | None = None
    semantic_relation_description: str = Field(min_length=1)
    role_bindings: tuple[str, ...] = ()
    scope_condition_bindings: tuple[str, ...] = ()
    reason_codes: tuple[str, ...] = ()
    ambiguity_codes: tuple[str, ...] = ()
    formal_predicate: Literal["EQ", "NE", "LT", "LE", "GT", "GE"] | None = None
    precision_tolerance: float | None = Field(default=None, ge=0)

    @model_validator(mode="after")
    def _shape(self):
        members = tuple(sorted(set(self.member_assertion_refs)))
        if len(members) < 2 or members != self.member_assertion_refs:
            raise ValueError("Consistency relation members must be canonical ordered-unique")
        for name in ("role_bindings", "scope_condition_bindings", "reason_codes", "ambiguity_codes"):
            values = tuple(getattr(self, name))
            if values != tuple(sorted(set(values))):
                raise ValueError(f"{name} must be canonical ordered-unique")
        if self.relation_materiality == "MATERIAL_RELATION" and self.relation is None:
            raise ValueError("MATERIAL_RELATION requires a terminal relation")
        if self.relation_materiality != "MATERIAL_RELATION" and self.relation is not None:
            raise ValueError("non-material or unresolved binding cannot fabricate a consistency relation")
        if self.relation_materiality != "MATERIAL_RELATION" and self.formal_predicate is not None:
            raise ValueError("formal predicate requires MATERIAL_RELATION")
        if self.formal_predicate is not None and len(members) != 2:
            raise ValueError("formal predicate requires binary relation")
        return self


class ConsistencyRelationReply(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    discovery_complete: bool
    results: tuple[ConsistencyRelationReplyRow, ...]

    @model_validator(mode="after")
    def _unique(self):
        keys = [(item.member_assertion_refs, item.semantic_relation_description,
                 item.role_bindings, item.scope_condition_bindings,
                 item.formal_predicate, item.precision_tolerance) for item in self.results]
        if len(keys) != len(set(keys)):
            raise ValueError("Consistency reply repeats a semantic relation binding")
        return self


class ConsistencyGlobalProcedureAccounting(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    schema_version: Literal["consistency_global_procedure_accounting_v1"] = (
        "consistency_global_procedure_accounting_v1"
    )
    lowering_ref: dict[str, Any]
    plan_ref: dict[str, Any] | None = None
    discovery_ref: dict[str, Any] | None = None
    conformance_ref: dict[str, Any] | None = None
    binding_refs: tuple[dict[str, Any], ...] = ()
    executability_refs: tuple[dict[str, Any], ...] = ()
    evaluation_refs: tuple[dict[str, Any], ...] = ()
    fact_refs: tuple[dict[str, Any], ...] = ()
    positive_proof_refs: tuple[dict[str, Any], ...] = ()
    indeterminate_proof_refs: tuple[dict[str, Any], ...] = ()
    state: Literal["PASS", "FAIL", "INDETERMINATE", "TECHNICAL_FAILURE", "NOT_APPLICABLE"]
    failure: dict[str, Any] | None = None
    semantic_input_receipt_ref: str | None = None


class ConsistencySharedWorkReply(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    logical_work_ref: str = Field(min_length=1)
    discovery_complete: bool
    results: tuple[ConsistencyRelationReplyRow, ...]


class ConsistencySharedReply(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    work_results: tuple[ConsistencySharedWorkReply, ...]

    @model_validator(mode="after")
    def _unique_work(self):
        refs = [item.logical_work_ref for item in self.work_results]
        if refs != sorted(set(refs)):
            raise ValueError("shared Consistency work refs must be canonical ordered-unique")
        return self


_INSTRUCTION_PROFILE_VERSION = "consistency-instruction-profile-v2"
_DYNAMIC_CONTROL_PROJECTION_VERSION = "v2"
_PROMPT_VERSION = "v2"
_PROTOCOL_CORRECTION_VERSION = "consistency-protocol-correction-v1"
_LABEL_CANONICALIZATION_VERSION = "consistency-label-set-v1"
_PROTOCOL_CORRECTION_INSTRUCTION = "Regenerate once under the unchanged Source Universe and controls. Feedback is protocol data, not business facts or Evidence. Do not clean, truncate, mechanically split, or delete relations to manufacture PASS; perform bounded discovery and report completion truthfully."
_PROTOCOL_CORRECTION_VERSION_V2 = "consistency-protocol-correction-v2"
_PROTOCOL_CORRECTION_INSTRUCTION_V2 = (
    _PROTOCOL_CORRECTION_INSTRUCTION + " "
    "Use the member-order guidance only if diagnostic.errors is nonempty, every error code is exactly "
    "MEMBER_ORDER_OR_UNIQUENESS, and every logical work has measurable, clean required-member-set, "
    "candidate-budget, Source, Scope, Arity, duplicate-binding and discovery-completion accounting. "
    "Otherwise use the general bounded-discovery correction rules above: do not freeze relation counts "
    "or declare incomplete discovery complete. For the member-order case, output complete original "
    "Assertion IDs in ascending case-sensitive Python string lexicographic order, not natural-number "
    "or narrative order. Do not shorten, invent, replace, delete or deduplicate IDs. Describe relations "
    "using explicit business facts or Assertion identities; avoid first/second, former/latter and 前者/后者 "
    "positional references. Do not intentionally add, delete, split, merge or generalize relations merely "
    "to fix member order. Preserve the Source Universe, Scope, required member sets, composite Arity, "
    "candidate budget and discovery_complete rules. Do not mechanically change relation, "
    "relation_materiality, formal_predicate, precision_tolerance or the business judgment. A formal "
    "predicate must follow canonical lhs/rhs direction and explicit business obligations; never blindly "
    "retain or reverse a predicate. If allowed sources cannot support a relation, express uncertainty "
    "under the existing schema's applicable rules without manufacturing PASS. The previous complete "
    "reply is not supplied: exact candidate-set or row-count preservation is not guaranteed."
)


def _correction_instruction(policy):
    instructions = {
        _PROTOCOL_CORRECTION_VERSION: _PROTOCOL_CORRECTION_INSTRUCTION,
        _PROTOCOL_CORRECTION_VERSION_V2: _PROTOCOL_CORRECTION_INSTRUCTION_V2,
    }
    if policy not in instructions:
        raise ConsistencyCompositionError("unknown Consistency protocol correction policy")
    return instructions[policy]


_SYSTEM_INSTRUCTION = "Assess semantic consistency relations among exact canonical Claim assertions. Candidate discovery is not truth. Do not judge evidence support, citation validity, requirement adherence, coverage, repair or release."
_ORDINARY_ROLE_INSTRUCTION = "Assess exact canonical assertions only within the supplied validation scope. For required relation-member sets, decide whether a material consistency relation exists. For DOCUMENT_GLOBAL logical discovery, search the complete exact selector universe for bounded material relations without assuming that all members form one relation. If material, decide CONSISTENT, INCONSISTENT or INDETERMINATE. Preserve scope, qualifiers, quantities and conditions. Only propose a formal predicate when relation meaning is explicit; never invent tolerance."
_SHARED_ROLE_INSTRUCTION = "One physical call serves multiple exact logical Consistency consumers over the same canonical Document assertion domain. Evaluate each logical_work_ref independently using only its supplied validation scope and procedure controls. Never reuse one scope's semantic conclusion as another scope's result. Preserve qualifiers, quantities and conditions; never invent tolerance."
_COMMON_REPLY_RULES = (
    "Return only a JSON object matching the reply schema. Examples are schema teaching data, not current assertions or answers. "
    "member_assertion_refs is a JSON string array of at least two exact assertion IDs, canonical ordered-unique by Python lexicographic ordering of the complete original strings (case-sensitive, not natural-number sorting); never rewrite, shorten, or invent IDs. "
    "role_bindings, scope_condition_bindings, reason_codes, and ambiguity_codes are JSON string arrays, each canonical ordered-unique by the same ascending case-sensitive full-string order. Neither subject-first, source-first, nor narrative role order determines member_assertion_refs; keep narrative roles in the description and role_bindings, while constructing every ordered-unique array in its own lexical order. "
    "semantic_relation_description must identify the concrete compared property, constraint, dependency, or conflict and how the selected members participate. Shared provenance or reference-case classification alone is not a material comparison. If the existence of that concrete relation is unresolved, use relation_materiality=INDETERMINATE rather than a generic claim that the group is CONSISTENT. "
    "For relation_materiality=MATERIAL_RELATION, relation is one of CONSISTENT, INCONSISTENT, INDETERMINATE and is not null. "
    "For relation_materiality=NO_MATERIAL_RELATION or INDETERMINATE, relation and formal_predicate are JSON null; the latter materiality means the existence of a material relation is unresolved. "
    "MATERIAL_RELATION with relation=INDETERMINATE is valid when the relation exists but its consistency conclusion is unresolved. "
    "formal_predicate is optional and only applies to MATERIAL_RELATION with exactly two members: lhs=member_assertion_refs[0], rhs=member_assertion_refs[1] in that canonical direction; choose EQ, NE, LT, LE, GT, or GE for that direction. Never swap operands or invert a predicate after output. "
    "formal_predicate applies to the supplied typed_value of those exact lhs and rhs assertions, not to semantic similarity of their prose. Natural-language paraphrases with different string typed_value are not exact EQ; leave formal_predicate null for a semantic-only relation without an explicit exact typed-value predicate. Encode the explicit business obligation before evaluating the observed values: endpoints required to differ use NE even when their observed values are equal; a capacity required not below a bound uses GE when canonical lhs is that capacity and rhs is that bound. Use the predicate for the actual canonical direction, and never choose a different predicate merely because it makes the observed operands satisfy it. "
    "Use precision_tolerance only when supported by actual material; never invent tolerance. "
    "Distinct semantic relation bindings may share one member group; do not duplicate the same full binding of members, semantic_relation_description, role_bindings, scope_condition_bindings, formal_predicate, and precision_tolerance. "
    "discovery_complete must truthfully report whether the supplied discovery work was completed."
)
_LEGACY_COMMON_REPLY_RULES = _COMMON_REPLY_RULES
_COMMON_REPLY_RULES = _COMMON_REPLY_RULES.replace(
    'semantic_relation_description must identify the concrete compared property, constraint, dependency, or conflict and how the selected members participate. Shared provenance or reference-case classification alone is not a material comparison. If the existence of that concrete relation is unresolved, use relation_materiality=INDETERMINATE rather than a generic claim that the group is CONSISTENT. ',
    'Decide relation existence before consistency. semantic_relation_description must identify a concrete shared property and comparable scope, a specific overlap, or a constraint/dependency explicitly supported by the original assertions or supplied business obligation; explain why every selected member is needed. Same topic, same case, distinct-but-compatible identities, or absence of contradiction alone does not establish MATERIAL_RELATION. State only the extent supported: partial overlap is not full containment. Preserve unknown state, reference-case scope, and expected long-term qualifiers; never invent a necessary dependency or realized benefit. If no such relation exists use NO_MATERIAL_RELATION; if existence is unresolved use INDETERMINATE. Judge CONSISTENT/INCONSISTENT only after a material relation is established. ',
)
_COMMON_REPLY_RULES = _COMMON_REPLY_RULES.replace(
    'role_bindings, scope_condition_bindings, reason_codes, and ambiguity_codes are JSON string arrays, each canonical ordered-unique by the same ascending case-sensitive full-string order. Neither subject-first, source-first, nor narrative role order determines member_assertion_refs; keep narrative roles in the description and role_bindings, while constructing every ordered-unique array in its own lexical order. ',
    'role_bindings, scope_condition_bindings, reason_codes, and ambiguity_codes are unordered string sets; Python canonicalizes their exact strings before strict validation. Their array order never encodes member roles or predicate direction. Keep semantic roles and qualifiers explicit; member_assertion_refs and formal operands remain strict and are never repaired by label normalization. ',
)
_DISCOVERY_REPLY_RULES = (
    "SMALL_N_EXHAUSTIVE_MEMBERS requires exactly the supplied required_relation_member_sets, with no missing or extra member set. "
    "LARGE_N_HIGH_RECALL includes every force-included required set and may add candidate member sets only up to semantic_candidate_budget. "
    "DOCUMENT_GLOBAL_BOUNDED_DISCOVERY independently searches the complete supplied assertion universe for bounded candidates of arity 2 through composite_max_arity; zero candidates with discovery_complete=true is a completed no-candidate outcome. "
    "Apply the validation_scope, required_logical_obligation_refs, and composite_max_arity from this work's dynamic control. "
    "The all-included selector defines the Claim Universe to search, not a requirement that every assertion appear in output or in one relation. "
    "A Semantic Cluster sharing a topic, entity, provenance, or uncertainty label does not by itself establish a Material Relation. Discover a specific comparison, constraint, dependency, or conflict first; its Relation Members are only the exact assertions needed to express that independently justified Relation Candidate within this work's arity bound. "
    "Do not output topic-cluster or whole-document-summary relations. For a theme spanning more members than the bound, discover independently justified bounded relations from the original Universe; never retrofit an oversized binding by truncation, arbitrary subsets, or mechanical splitting. "
    "The reply descriptor presents minItems=2, uniqueItems=true, and maxItems from this work's composite_max_arity. Search completion is not a fixed example value: if the supplied bounded search or required accounting is unfinished, report discovery_complete=false; unresolved relation conclusions retain their appropriate INDETERMINATE state."
    " Before returning each work, self-check the complete original member IDs in canonical ordered-unique order, arity from 2 through that work's composite_max_arity, required member sets, candidate budget, and truthful discovery_complete. Establish canonical operands and bounded member groups while discovering the relation. This self-check does not authorize rewriting a completed binding, truncating or dropping members, splitting or deleting relations, or changing predicates to manufacture conformance."
)
_ORDINARY_TEMPLATE = _COMMON_REPLY_RULES + " " + _DISCOVERY_REPLY_RULES
_SHARED_TEMPLATE = (
    _COMMON_REPLY_RULES + " " + _DISCOVERY_REPLY_RULES + " "
    "work_results is a JSON array canonical ordered-unique by full logical_work_ref and must contain exactly one result for every supplied logical_work_ref, with no extra work. "
    "Apply each logical_work's own validation_scope, discovery_mode, required_relation_member_sets, semantic_candidate_budget, required_logical_obligation_refs, and composite_max_arity independently; a result from one scope does not account for another."
)
_EXAMPLE_MEMBERS = ("example-assertion:10", "example-assertion:2")
_EXAMPLE_ROWS = (
    {
        "member_assertion_refs": list(_EXAMPLE_MEMBERS),
        "relation_materiality": "MATERIAL_RELATION",
        "relation": "CONSISTENT",
        "semantic_relation_description": "The same stated resource total agrees.",
        "role_bindings": ["resource-total"],
        "scope_condition_bindings": [],
        "reason_codes": ["MATCHING_VALUE"],
        "ambiguity_codes": [],
        "formal_predicate": "EQ",
        "precision_tolerance": None,
    },
    {
        "member_assertion_refs": list(_EXAMPLE_MEMBERS),
        "relation_materiality": "NO_MATERIAL_RELATION",
        "relation": None,
        "semantic_relation_description": "The statements concern different quantities.",
        "role_bindings": [],
        "scope_condition_bindings": [],
        "reason_codes": [],
        "ambiguity_codes": [],
        "formal_predicate": None,
        "precision_tolerance": None,
    },
    {
        "member_assertion_refs": list(_EXAMPLE_MEMBERS),
        "relation_materiality": "INDETERMINATE",
        "relation": None,
        "semantic_relation_description": "The scopes cannot be compared from the supplied claims.",
        "role_bindings": [],
        "scope_condition_bindings": [],
        "reason_codes": [],
        "ambiguity_codes": ["SCOPE_UNRESOLVED"],
        "formal_predicate": None,
        "precision_tolerance": None,
    },
)


def _compact_reply_schema(value):
    """Project only wire-shape metadata from the existing Pydantic models."""
    if isinstance(value, list):
        return [_compact_reply_schema(item) for item in value]
    if isinstance(value, dict):
        return {
            key: _compact_reply_schema(item)
            for key, item in value.items()
            if key not in {"title", "default"}
        }
    return value


def _reply_schema_descriptors(composite_max_arity=None):
    descriptors = {
        "ordinary": _compact_reply_schema(ConsistencyRelationReply.model_json_schema()),
        "shared": _compact_reply_schema(ConsistencySharedReply.model_json_schema()),
    }
    if composite_max_arity is not None:
        for descriptor in descriptors.values():
            descriptor["$defs"]["ConsistencyRelationReplyRow"]["properties"]["member_assertion_refs"].update(
                minItems=2, maxItems=int(composite_max_arity), uniqueItems=True,
            )
    return descriptors


def _instruction_components(composite_max_arity=None):
    descriptors = _reply_schema_descriptors(composite_max_arity)
    examples = {
        "ordinary": {
            "label": "schema examples only; not current assertions",
            "replies": [
                {"discovery_complete": True, "results": [row]}
                for row in _EXAMPLE_ROWS
            ],
        },
        "shared": {
            "label": "schema example only; not current assertions or logical work",
            "reply": {
                "work_results": [
                    {
                        "logical_work_ref": "example-logical-work:1",
                        "discovery_complete": True,
                        "results": [_EXAMPLE_ROWS[0], _EXAMPLE_ROWS[1]],
                    },
                    {
                        "logical_work_ref": "example-logical-work:2",
                        "discovery_complete": True,
                        "results": [_EXAMPLE_ROWS[2]],
                    },
                ],
            },
        },
    }
    output_contracts = {
        "ordinary": json.dumps({
            "contract": CONSISTENCY_RELATION_CONTRACT,
            "reply_schema_descriptor": descriptors["ordinary"],
            "examples": examples["ordinary"],
        }, sort_keys=True, separators=(",", ":")),
        "shared": json.dumps({
            "contract": CONSISTENCY_SHARED_RELATION_CONTRACT,
            "reply_schema_descriptor": descriptors["shared"],
            "examples": examples["shared"],
        }, sort_keys=True, separators=(",", ":")),
    }
    return descriptors, examples, output_contracts


def _instruction_profile(*, protocol_correction=True, label_canonicalization=True,
                         correction_policy_version=_PROTOCOL_CORRECTION_VERSION_V2):
    """Finite static instruction identity; final messages never enter this hash."""
    descriptors, examples, output_contracts = _instruction_components()
    common = _COMMON_REPLY_RULES if label_canonicalization else _LEGACY_COMMON_REPLY_RULES
    ordinary = _ORDINARY_TEMPLATE if label_canonicalization else _ORDINARY_TEMPLATE.replace(_COMMON_REPLY_RULES, common, 1)
    shared = _SHARED_TEMPLATE if label_canonicalization else _SHARED_TEMPLATE.replace(_COMMON_REPLY_RULES, common, 1)
    static = {
        "schema_version": _INSTRUCTION_PROFILE_VERSION,
        "system_instruction": {"ref": "instruction://artifact-consistency/system/v1", "version": "v1", "sha256": canonical_sha256(_SYSTEM_INSTRUCTION)},
        "common_reply_rules": {"ref": "instruction://artifact-consistency/common-reply-rules/v2", "version": "v2", "sha256": canonical_sha256(common)},
        "ordinary_role": {"ref": "instruction://artifact-consistency/role/v1", "version": "v1", "sha256": canonical_sha256(_ORDINARY_ROLE_INSTRUCTION)},
        "shared_role": {"ref": "instruction://artifact-consistency/role/shared/v1", "version": "v1", "sha256": canonical_sha256(_SHARED_ROLE_INSTRUCTION)},
        "ordinary_template": {"ref": "template://artifact-consistency/v2", "version": "v2", "sha256": canonical_sha256(ordinary)},
        "shared_template": {"ref": "template://artifact-consistency/shared/v2", "version": "v2", "sha256": canonical_sha256(shared)},
        "reply_schema_descriptor": {"ref": "schema://artifact-consistency/reply-descriptor/pydantic-projection-v1", "version": "pydantic-projection-v1", "sha256": canonical_sha256(descriptors)},
        "valid_examples": {"ref": "examples://artifact-consistency/valid/v2", "version": "v2", "sha256": canonical_sha256(examples)},
        "ordinary_output_contract": {"ref": "contract://artifact-consistency/v2", "version": "v2", "sha256": canonical_sha256(output_contracts["ordinary"])},
        "shared_output_contract": {"ref": "contract://artifact-consistency/shared/v2", "version": "v2", "sha256": canonical_sha256(output_contracts["shared"])},
        "dynamic_control_projection_version": _DYNAMIC_CONTROL_PROJECTION_VERSION,
        "ordinary_prompt_version": _PROMPT_VERSION,
        "shared_prompt_version": _PROMPT_VERSION,
    }
    if label_canonicalization:
        static["reply_label_canonicalization_version"] = _LABEL_CANONICALIZATION_VERSION
    if protocol_correction:
        static["protocol_correction_policy_version"] = correction_policy_version
        static["protocol_correction_instruction_sha256"] = canonical_sha256(_correction_instruction(correction_policy_version))
    digest = canonical_sha256(static)
    return {
        **static,
        "profile_hash": digest,
        "config_ref": "consistency-instruction-profile://sha256/" + digest,
    }


def _previous_instruction_profiles():
    """Observed native identities, for replay/fencing only; never new dispatch."""
    v1 = _instruction_profile(correction_policy_version=_PROTOCOL_CORRECTION_VERSION)
    no_labels = _instruction_profile(label_canonicalization=False, correction_policy_version=_PROTOCOL_CORRECTION_VERSION)
    no_correction = _instruction_profile(protocol_correction=False, label_canonicalization=False)

    def identify(profile):
        static = {key: value for key, value in profile.items() if key not in {"profile_hash", "config_ref"}}
        digest = canonical_sha256(static)
        return {**static, "profile_hash": digest, "config_ref": "consistency-instruction-profile://sha256/" + digest}

    # Native inventory: docs/rc1_ch1_consistency_correction_v2_implementation_20261019_r01/12_PROFILE_INVENTORY_READONLY.json.
    before_instruction_sha = identify({key: value for key, value in no_labels.items()
                                      if key != "protocol_correction_instruction_sha256"})
    c2_early = identify({**no_correction, **{
        name: {**no_correction[name], "sha256": digest} for name, digest in (
            ("common_reply_rules", "a998a111964494bcd8bae11f09e7a466943e150fe0be013e529eb5ae3b86439f"),
            ("ordinary_template", "ccaf42a420542dc2404581a63d9ea47189bb074f75642968948bbc680bfa5dfd"),
            ("shared_template", "4aeb4661e35d60378f5cd513183fd1b4f3f76149a80bafad3c5b1d08c4e5cb2b"),
        )}})
    return v1, no_labels, no_correction, before_instruction_sha, c2_early


class ConsistencyMainlineAccounting(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    schema_version: Literal["consistency_mainline_accounting_v1"] = "consistency_mainline_accounting_v1"
    input_hash: str
    artifact_state_ref: dict[str, Any]
    subject_hash: str
    source_snapshot_hash: str
    assertion_refs: tuple[dict[str, Any], ...]
    plan_ref: dict[str, Any]
    discovery_ref: dict[str, Any]
    conformance_ref: dict[str, Any]
    binding_refs: tuple[dict[str, Any], ...]
    executability_refs: tuple[dict[str, Any], ...]
    evaluation_refs: tuple[dict[str, Any], ...]
    fact_refs: tuple[dict[str, Any], ...]
    positive_proof_refs: tuple[dict[str, Any], ...]
    indeterminate_proof_refs: tuple[dict[str, Any], ...] = Field(default=(), exclude_if=lambda value: not value)
    state: Literal["PASS", "FAIL", "INDETERMINATE", "TECHNICAL_FAILURE"]
    failure: dict[str, Any] | None = None
    semantic_input_receipt_ref: str | None = None
    document_global: ConsistencyGlobalProcedureAccounting | None = Field(
        default=None, exclude_if=lambda value: value is None
    )


class TargetPostWriteConsistency:
    CLAIM_PROJECTION_CONTRACT_VERSION = "consistency-claim-projection-v1"
    VALIDATOR_ID = ConsistencyProjector.VALIDATOR_ID
    VERSION = "v6"
    # Finite real-workload calibration; routing capability remains the bound.
    MAX_OUTPUT_TOKENS = 16384
    SHARED_MAX_OUTPUT_TOKENS = 65536

    def __init__(self, *, post_write, model_gateway) -> None:
        self.post_write = post_write
        self.gateway = model_gateway
        self.store = post_write.research_runtime.artifact_runtime.artifact_store
        self.estimator = DeterministicTokenEstimator()
        self._lock = RLock()
        config_path = Path(__file__).resolve().parents[2] / "config" / "post_write_v1.yaml"
        self.profile = load_post_write_config(config_path).consistency

    @staticmethod
    def stage_plan() -> StagePlanSchema:
        stage = StageDescriptorSchema(
            stage_ref="consistency-mainline-stage:v1",
            stage_version="v1",
            sequence_index=0,
            stage_input_schema_ref="schema://consistency-mainline-input/v1",
            stage_output_schema_ref="schema://consistency-mainline-accounting/v1",
            execution_policy_ref="policy://artifact-consistency/v1",
            execution_policy_version="v1",
            timeout_seconds=120,
            max_attempts=1,
        )
        base = {
            "schema_version": "validator_stage_plan_v1",
            "stage_plan_ref": "stage-plan:consistency-mainline:v1",
            "validator_id": TargetPostWriteConsistency.VALIDATOR_ID,
            "validator_version": TargetPostWriteConsistency.VERSION,
            "stage_plan_version": "v1",
            "stages": [stage.model_dump(mode="json")],
        }
        return StagePlanSchema(**base, stage_plan_hash=canonical_sha256(base))

    def _identity(self, source_sections):
        if not source_sections:
            raise ConsistencyCompositionError("Consistency requires exact execution lineage")
        lifecycle = self.post_write.lifecycle_service
        lineages = [
            lifecycle.resolve_active_lineage(
                row.active_execution_lineage.run_id,
                row.active_execution_lineage.attempt_scope_ref,
            )
            for row in source_sections
        ]
        lineage = sorted(lineages, key=lambda row: row.attempt_scope_ref)[0]
        return {
            "task_id": lifecycle.load_run_identity(lineage.run_id).task_id,
            "run_id": lineage.run_id,
            "section_id": lineage.attempt_scope_ref.removeprefix("section:"),
        }, lineage

    def _source_material(self, *, artifact_ref, subject_hash, scope, source_sections, assembly_input_manifest_ref, validation_run_id):
        projection = self.post_write.claim_quality_projection
        if projection is None:
            raise ConsistencyCompositionError("Consistency requires canonical Claim projection owner")
        snapshot = projection.resolve_sources(
            post_write=self.post_write,
            artifact_ref=artifact_ref,
            scope=scope,
            source_sections=source_sections,
            assembly_input_manifest_ref=assembly_input_manifest_ref,
            citation_validation_run_id=validation_run_id,
            citation_subject_hash=subject_hash,
        )
        assertion_material = []
        claim_targets = []
        claim_by_id = {}
        section_by_claim = {}
        for section in snapshot.sections:
            if not section.claim_records:
                continue
            authority = UpstreamSIF07ClaimAuthorityResolutionSchema(
                state=UpstreamSIF07ClaimAuthorityState.PRESENT,
                claim_refs=tuple(record.claim_ref for record in section.claim_records),
                resolution_reason_ref="consistency-source://" + section.body_sha256,
            )
            targets = ArtifactClaimTargetMaterializer.materialize(
                authority_resolution=authority,
                artifact_state_ref=artifact_ref,
                validation_subject_hash=subject_hash,
                semantic_version=self.CLAIM_PROJECTION_CONTRACT_VERSION,
                resolved_claim_records={record.claim_ref.claim_id: record for record in section.claim_records},
            )
            records = {record.claim_ref.claim_id: record for record in section.claim_records}
            for target in targets:
                record = records[target.source_claim_ref.claim_id]
                claim_targets.append(target)
                claim_by_id[target.claim_target_id] = record
                section_by_claim[target.claim_target_id] = section.section_id
                assertion_material.append({
                    "source_unit_ref": target.claim_target_id,
                    "canonical_text": target.canonical_claim_text,
                    "typed_value": record.normalized_value,
                    "value_unit": record.unit,
                })
        return snapshot, tuple(sorted(claim_targets, key=lambda item: item.claim_target_id)), tuple(assertion_material), claim_by_id, section_by_claim

    @staticmethod
    def _logical_consumer_ref(*, validation_scope: str, scope_subject_ref: str, members) -> str:
        relation_key = ConsistencyDiscoveryPlanner.relation_key(members)
        return "consistency-consumer://sha256/" + canonical_sha256({
            "validation_scope": validation_scope,
            "scope_subject_ref": scope_subject_ref,
            "relation_key": relation_key,
        })

    @staticmethod
    def _formal_candidate(row: ConsistencyRelationReplyRow, assertions_by_ref):
        if row.formal_predicate is None:
            return None
        lhs_ref, rhs_ref = row.member_assertion_refs
        kwargs = {
            "lhs_assertion_ref": lhs_ref,
            "predicate": row.formal_predicate,
            "rhs_assertion_ref": rhs_ref,
            "precision_tolerance": row.precision_tolerance,
            "formal_contract_version": "formal-consistency-v1",
        }
        placeholder = FormalRelationCandidateSchema.model_construct(
            formal_candidate_ref="pending",
            formal_candidate_hash="0" * 64,
            **kwargs,
        )
        payload = placeholder.model_dump(mode="json")
        payload.pop("formal_candidate_ref", None)
        payload.pop("formal_candidate_hash", None)
        digest = canonical_sha256(payload)
        return FormalRelationCandidateSchema(
            formal_candidate_ref="formal-relation-candidate://sha256/" + digest,
            formal_candidate_hash=digest,
            **kwargs,
        )

    @staticmethod
    def _logical_work_ref(material) -> str:
        return "consistency-logical-work://sha256/" + canonical_sha256(material)

    @classmethod
    def _shared_material(cls, materials):
        values = tuple(materials)
        if len(values) < 2:
            raise ConsistencyCompositionError(
                "shared Consistency call requires multiple logical consumers"
            )
        common_fields = (
            "policy_id",
            "policy_version",
            "validation_run_id",
            "subject_hash",
            "artifact_state_ref",
            "source_snapshot_hash",
            "assertions",
            "assertion_context",
            "all_assertion_refs",
            "profile",
        )
        first = values[0]
        has_instruction_profile = "instruction_profile" in first
        if any(("instruction_profile" in value) != has_instruction_profile for value in values):
            raise ConsistencyCompositionError(
                "shared Consistency consumers mix instruction profiles"
            )
        if has_instruction_profile:
            common_fields += ("instruction_profile",)
        for value in values[1:]:
            if any(value[field] != first[field] for field in common_fields):
                raise ConsistencyCompositionError(
                    "shared Consistency consumers do not have an exact compatible domain"
                )
        works = tuple(
            sorted(
                (
                    {
                        "logical_work_ref": cls._logical_work_ref(value),
                        "material": value,
                    }
                    for value in values
                ),
                key=lambda item: item["logical_work_ref"],
            )
        )
        refs = [item["logical_work_ref"] for item in works]
        if len(refs) != len(set(refs)):
            raise ConsistencyCompositionError(
                "shared Consistency logical work identities collide"
            )
        return {
            "contract": CONSISTENCY_SHARED_RELATION_CONTRACT,
            **{field: first[field] for field in common_fields},
            "logical_works": list(works),
        }

    @staticmethod
    def _require_current_instruction_profile(material):
        if material.get("instruction_profile") != _instruction_profile():
            raise ConsistencyCompositionError(
                "new Consistency dispatch requires the current instruction profile"
            )

    @staticmethod
    def _legacy_material(material):
        return {key: value for key, value in material.items() if key != "instruction_profile"}

    @staticmethod
    def _model_source(material):
        """Exact semantic projection; durable hashes/provenance remain in receipt."""
        shared = material.get("contract") == CONSISTENCY_SHARED_RELATION_CONTRACT
        return {
            **(
                {
                    "validation_scopes": [
                        item["material"]["validation_scope"]
                        for item in material["logical_works"]
                    ]
                }
                if shared
                else {"validation_scope": material["validation_scope"]}
            ),
            "assertions": [
                {key: assertion[key] for key in ("assertion_ref", "canonical_text", "typed_value", "value_unit")}
                | {key: material["assertion_context"][assertion["assertion_ref"]][key]
                   for key in ("section_id", "claim_type")}
                for assertion in material["assertions"]
            ],
        }

    def _package(self, material, lineage, occurrence):
        self._require_current_instruction_profile(material)
        capability = dict(self.gateway.routing_capabilities(model_role=ModelRole.GENERAL.value))
        output_ceiling = (
            self.SHARED_MAX_OUTPUT_TOKENS
            if material.get("contract") == CONSISTENCY_SHARED_RELATION_CONTRACT
            else self.MAX_OUTPUT_TOKENS
        )
        output_limit = min(output_ceiling, int(capability["safe_max_output_tokens"]))
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
        shared = material.get("contract") == CONSISTENCY_SHARED_RELATION_CONTRACT
        source_payload = self._model_source(material)
        stamp = {"state": "CURRENT"}
        source = ContextSourceBindingSchema(
            source_family=ContextSourceFamily.PARAGRAPH_ARTIFACT,
            exact_source_ref={"source_snapshot_hash": material["source_snapshot_hash"]},
            source_schema_id="consistency_claim_assertions_v1",
            source_schema_version="v1",
            source_semantic_fingerprint=canonical_sha256(source_payload),
            authority_owner="SIF07/POST_WRITE_CLAIM_PROJECTION",
            run_id=lineage.run_id,
            scope_ref=lineage.attempt_scope_ref,
            currentness_binding=ContextSourceStatusBindingSchema(
                status_schema_id="consistency_currentness_v1",
                status_schema_version="v1",
                resolver_id=CONSISTENCY_POLICY_ID,
                resolver_policy_version="v1",
                status_payload=stamp,
                status_payload_sha256=canonical_sha256(stamp),
                status_semantic_fingerprint=canonical_sha256(stamp),
            ),
        )
        candidate = ContextSourceCandidateSchema(
            candidate_id="consistency-claim-assertions",
            source_binding=source,
            title="Exact canonical Claim assertions",
            representations=(ContextRepresentationOptionSchema(
                representation_level=ContextRepresentationLevel.EXACT,
                projection_schema_id="consistency_claim_assertions_v1",
                projection_schema_version="v1",
                representation_policy_id=CONSISTENCY_POLICY_ID,
                representation_policy_version="v1",
                materialized_payload=source_payload,
                materialized_payload_sha256=canonical_sha256(source_payload),
                protected_field_evidence=tuple(sorted(source_payload)),
            ),),
        )
        runtime.register_source_provider(ContextSourceProviderRegistration(
            source_family=ContextSourceFamily.PARAGRAPH_ARTIFACT,
            provider_id=CONSISTENCY_POLICY_ID,
            provider_version="v1",
            candidate_reader=lambda _subject: (candidate,),
            source_binding_resolver=lambda value: value,
            hard_eligibility_validator=lambda value, subject: (
                value.run_id == subject.run_id and value.scope_ref == subject.consumer_scope_ref,
                () if value.run_id == subject.run_id and value.scope_ref == subject.consumer_scope_ref else ("CONSISTENCY_SOURCE_SCOPE_MISMATCH",),
            ),
        ))
        policy = ContextPolicyBindingSchema(
            source_catalog_id=runtime.SOURCE_CATALOG_ID,
            source_catalog_version=runtime.SOURCE_CATALOG_VERSION,
            role_policy_id=CONSISTENCY_POLICY_ID,
            role_policy_version="v1",
            cross_section_policy_id="consistency-exact-claim-domain",
            cross_section_policy_version="v1",
            consumer_authorization_policy_id="canonical-claim-targets",
            consumer_authorization_policy_version="v1",
            selection_policy_id="sif10-hierarchical-selection",
            selection_policy_version="v1",
            budget_policy_id="sif10-context-budget",
            budget_policy_version="v1",
            representation_policy_id=CONSISTENCY_POLICY_ID,
            representation_policy_version="v1",
        )
        system = _SYSTEM_INSTRUCTION
        _descriptors, _examples, output_contracts = _instruction_components(
            material["profile"]["composite_max_arity"]
        )
        if shared:
            role = _SHARED_ROLE_INSTRUCTION
            template = _SHARED_TEMPLATE
            control_payload = json.dumps(
                {
                    "instruction_profile_ref": material["instruction_profile"]["config_ref"],
                    "logical_works": [
                        {
                            "logical_work_ref": item["logical_work_ref"],
                            "validation_scope": item["material"]["validation_scope"],
                            "discovery_mode": item["material"]["discovery_mode"],
                            "required_relation_member_sets": item["material"]["required_relation_member_sets"],
                            "semantic_candidate_budget": item["material"]["semantic_candidate_budget"],
                            "required_logical_obligation_refs": item["material"].get(
                                "required_logical_obligation_refs", []
                            ),
                            "composite_max_arity": item["material"]["profile"]["composite_max_arity"],
                        }
                        for item in material["logical_works"]
                    ]
                },
                sort_keys=True,
                separators=(",", ":"),
            )
            control_ref = "consistency-shared-batch://sha256/" + canonical_sha256(control_payload)
            output_contract = output_contracts["shared"]
        else:
            role = _ORDINARY_ROLE_INSTRUCTION
            template = _ORDINARY_TEMPLATE
            control_payload = json.dumps(
                {
                    "instruction_profile_ref": material["instruction_profile"]["config_ref"],
                    "discovery_mode": material["discovery_mode"],
                    "validation_scope": material["validation_scope"],
                    "required_relation_member_sets": material["required_relation_member_sets"],
                    "semantic_candidate_budget": material["semantic_candidate_budget"],
                    "required_logical_obligation_refs": material.get(
                        "required_logical_obligation_refs", []
                    ),
                    "composite_max_arity": material["profile"]["composite_max_arity"],
                },
                sort_keys=True,
                separators=(",", ":"),
            )
            control_ref = "consistency-batch://sha256/" + canonical_sha256(control_payload)
            output_contract = output_contracts["ordinary"]
        if "protocol_correction_feedback" in material:
            control_payload = json.dumps({**json.loads(control_payload),
                "protocol_correction_feedback": material["protocol_correction_feedback"]}, sort_keys=True, separators=(",", ":"))
            control_ref = ("consistency-shared-batch://sha256/" if shared else "consistency-batch://sha256/") + canonical_sha256(control_payload)
        instructions = ContextInstructionMaterialSchema(
            system_instruction=system,
            role_instruction=role,
            prompt_template=template,
            task_output_contract=output_contract,
            dynamic_control_payloads=((control_ref, control_payload),),
        )
        instruction_variant = "shared/v2" if shared else "v2"
        binding = InstructionBindingSchema(
            system_instruction_ref="instruction://artifact-consistency/system/v1",
            system_instruction_version="v1",
            system_instruction_sha256=canonical_sha256(system),
            role_instruction_ref=f"instruction://artifact-consistency/role/{'shared/v1' if shared else 'v1'}",
            role_instruction_version="v1",
            role_instruction_sha256=canonical_sha256(role),
            prompt_template_ref=f"template://artifact-consistency/{instruction_variant}",
            prompt_template_version="v2",
            prompt_template_sha256=canonical_sha256(template),
            task_output_contract_ref=f"contract://artifact-consistency/{instruction_variant}",
            task_output_contract_sha256=canonical_sha256(output_contract),
            dynamic_control_bindings=(DynamicControlBindingSchema(
                control_ref=control_ref,
                control_version=_DYNAMIC_CONTROL_PROJECTION_VERSION,
                control_sha256=canonical_sha256(control_payload),
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
        context_purpose = (
            "CONSISTENCY_SHARED_RELATION_BINDING"
            if shared
            else "CONSISTENCY_RELATION_BINDING"
        )
        package = runtime.materialize_context_package(
            invocation_subject=ContextInvocationSubjectSchema(
                run_id=lineage.run_id,
                attempt_id=lineage.attempt_id,
                invocation_id=occurrence,
                logical_operation_id=occurrence,
                agent_role="ARTIFACT_CONSISTENCY",
                execution_mode="EVALUATE",
                context_purpose=context_purpose,
                consumer_scope_ref=lineage.attempt_scope_ref,
            ),
            role_policy=ContextRolePolicySchema(
                role_policy_id=CONSISTENCY_POLICY_ID,
                role_policy_version="v1",
                agent_role="ARTIFACT_CONSISTENCY",
                execution_mode="EVALUATE",
                context_purpose=context_purpose,
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

    @classmethod
    def _normalize_reason_order(cls, payload, *, canonical_sets=False):
        if isinstance(payload, dict) and isinstance(payload.get("work_results"), (list, tuple)):
            return {**payload, "work_results": [cls._normalize_reason_order(work, canonical_sets=canonical_sets)
                                                for work in payload["work_results"]]}
        if not isinstance(payload, dict) or not isinstance(payload.get("results"), (list, tuple)):
            return payload
        rows = []
        for row in payload["results"]:
            if isinstance(row, dict):
                fields = ("reason_codes", "role_bindings", "scope_condition_bindings", "ambiguity_codes") if canonical_sets else ("reason_codes", "role_bindings")
                for field in fields:
                    values = row.get(field)
                    if (
                        isinstance(values, (list, tuple))
                        and all(isinstance(value, str) for value in values)
                        and (all(value.strip() for value in values) if canonical_sets else len(values) == len(set(values)))
                    ):
                        # Exact set labels only. Preserve blanks/invalid types,
                        # members, predicates, descriptions and complete rows.
                        row = {**row, field: sorted(set(values)) if canonical_sets else sorted(values)}
            rows.append(row)
        return {**payload, "results": rows}

    @classmethod
    def _parse_reply(cls, raw, material):
        payload = json.loads(raw) if isinstance(raw, str) else raw
        version = material.get("instruction_profile", {}).get("reply_label_canonicalization_version")
        if version not in (None, _LABEL_CANONICALIZATION_VERSION):
            raise ConsistencyCompositionError("unknown Consistency label canonicalization version")
        payload = cls._normalize_reason_order(payload, canonical_sets=version is not None)
        reply = ConsistencyRelationReply.model_validate(payload)
        required = {tuple(item) for item in material["required_relation_member_sets"]}
        actual = {item.member_assertion_refs for item in reply.results}
        if not reply.discovery_complete:
            raise ConsistencyCompositionError("Consistency discovery did not establish terminal accounting")
        if material["discovery_mode"] == "SMALL_N_EXHAUSTIVE_MEMBERS":
            if actual != required:
                raise ConsistencyCompositionError("small-N Consistency reply omitted or added relation members")
        else:
            if not required <= actual:
                raise ConsistencyCompositionError("large-N Consistency reply omitted force-included relations")
            extras = actual - required
            if len(extras) > int(material["semantic_candidate_budget"]):
                raise ConsistencyCompositionError("large-N semantic candidate budget exceeded")
        allowed = {item["assertion_ref"] for item in material["assertions"]}
        if any(not set(item.member_assertion_refs) <= allowed for item in reply.results):
            raise ConsistencyCompositionError("Consistency reply escapes exact assertion domain")
        sections = {ref: context["section_id"] for ref, context in material["assertion_context"].items()}
        if any(not ConsistencyDiscoveryPlanner.eligible(item.member_assertion_refs,
                   validation_scope=material["validation_scope"], assertion_sections=sections)
               or len(item.member_assertion_refs) > material["profile"]["composite_max_arity"] for item in reply.results):
            raise ConsistencyCompositionError("Consistency reply escapes exact scope or composite bound")
        return tuple(sorted(reply.results, key=lambda item: item.member_assertion_refs))

    @classmethod
    def _parse_shared_reply(cls, raw, shared_material):
        payload = json.loads(raw) if isinstance(raw, str) else raw
        version = shared_material.get("instruction_profile", {}).get("reply_label_canonicalization_version")
        if version not in (None, _LABEL_CANONICALIZATION_VERSION):
            raise ConsistencyCompositionError("unknown Consistency label canonicalization version")
        payload = cls._normalize_reason_order(payload, canonical_sets=version is not None)
        reply = ConsistencySharedReply.model_validate(payload)
        expected = {
            item["logical_work_ref"]: item["material"]
            for item in shared_material["logical_works"]
        }
        actual = {item.logical_work_ref: item for item in reply.work_results}
        if set(actual) != set(expected):
            raise ConsistencyCompositionError(
                "shared Consistency reply does not exactly account every logical consumer"
            )
        parsed = {}
        for work_ref, work_reply in actual.items():
            parsed[work_ref] = cls._parse_reply(
                {
                    "discovery_complete": work_reply.discovery_complete,
                    "results": [
                        item.model_dump(mode="json") for item in work_reply.results
                    ],
                },
                expected[work_ref],
            )
        return parsed

    @classmethod
    def _member_order_only(cls, raw, material, diagnostic):
        """Conservative instruction precondition, never a reply repair or acceptance."""
        try:
            errors, counts = diagnostic["errors"], diagnostic["work_counts"]
            if not errors or any(error["code"] != "MEMBER_ORDER_OR_UNIQUENESS" for error in errors):
                return False
            payload = json.loads(raw)
            shared = material.get("contract") == CONSISTENCY_SHARED_RELATION_CONTRACT
            works = material["logical_works"] if shared else [{"material": material}]
            if not isinstance(counts, list) or len(counts) != len(works):
                return False
            if shared:
                if not isinstance(payload, dict) or set(payload) != {"work_results"}:
                    return False
                replies = payload["work_results"]
                if not isinstance(replies, list) or [row["logical_work_ref"] for row in replies] != [work["logical_work_ref"] for work in works]:
                    return False
            else:
                replies = [payload]
            for work, reply, count in zip(works, replies, counts):
                current = work["material"]
                keys = {"discovery_complete", "results"} | ({"logical_work_ref"} if shared else set())
                if not isinstance(reply, dict) or set(reply) != keys or reply["discovery_complete"] is not True or not isinstance(reply["results"], list):
                    return False
                if count["logical_work_ref"] != work.get("logical_work_ref") or count["missing_required_sets"] != []:
                    return False
                allowed = {item["assertion_ref"] for item in current["assertions"]}
                sections = {ref: item["section_id"] for ref, item in current["assertion_context"].items()}
                groups, bindings = set(), set()
                normalized = cls._normalize_reason_order({"results": reply["results"]}, canonical_sets=
                    current.get("instruction_profile", {}).get("reply_label_canonicalization_version") == _LABEL_CANONICALIZATION_VERSION)
                for row in normalized["results"]:
                    try:
                        ConsistencyRelationReplyRow.model_validate(row)
                    except ValidationError as error:
                        # The member check runs before other cross-field checks.
                        if any(item["type"] != "value_error" or "Consistency relation members must be canonical ordered-unique" not in item["msg"] for item in error.errors()):
                            return False
                    members = row["member_assertion_refs"]
                    group = frozenset(members)  # Identity comparison only; never write a member array.
                    if len(group) != len(members) or not 2 <= len(members) <= current["profile"]["composite_max_arity"] or not group <= allowed:
                        return False
                    if not ConsistencyDiscoveryPlanner.eligible(members, validation_scope=current["validation_scope"], assertion_sections=sections):
                        return False
                    labels = tuple(tuple(row.get(name, ())) for name in ("role_bindings", "scope_condition_bindings", "reason_codes", "ambiguity_codes"))
                    if any(values != tuple(sorted(set(values))) for values in labels):
                        return False
                    is_material = row["relation_materiality"] == "MATERIAL_RELATION"
                    predicate = row.get("formal_predicate")
                    if (is_material != (row.get("relation") is not None)
                            or (predicate is not None and (not is_material or len(members) != 2))):
                        return False
                    # Match the validated float's identity for duplicate census only.
                    tolerance = None if row.get("precision_tolerance") is None else float(row["precision_tolerance"])
                    binding = (group, row["semantic_relation_description"], labels[0], labels[1], predicate, tolerance)
                    if binding in bindings:
                        return False
                    groups.add(group)
                    bindings.add(binding)
                required = {frozenset(item) for item in current["required_relation_member_sets"]}
                candidates = len(groups - required)
                if (not required <= groups or (current["discovery_mode"] == "SMALL_N_EXHAUSTIVE_MEMBERS" and groups != required)
                        or candidates > current["semantic_candidate_budget"] or type(count["candidate_count"]) is not int
                        or count["candidate_count"] != candidates or count["semantic_candidate_budget"] != current["semantic_candidate_budget"]
                        or count["composite_max_arity"] != current["profile"]["composite_max_arity"]):
                    return False
            return True
        except (KeyError, TypeError, ValueError, AttributeError):
            return False

    @classmethod
    def _protocol_feedback(cls, raw, material):
        """Non-mutating protocol census, never a semantic or truth judgment."""
        shared = material.get("contract") == CONSISTENCY_SHARED_RELATION_CONTRACT
        parser = cls._parse_shared_reply if shared else cls._parse_reply
        try:
            parser(raw, material)
        except (json.JSONDecodeError, ValidationError, ConsistencyCompositionError) as exc:
            errors = []
            if isinstance(exc, ValidationError):
                errors = [{"path": ".".join(map(str, item["loc"])), "code": item["type"], "message": item["msg"]}
                          for item in exc.errors() if item["type"] != "value_error"]
            else:
                errors.append({"path": "/", "code": type(exc).__name__, "message": str(exc)})
        else:
            return None
        try:
            payload = json.loads(raw)
        except (json.JSONDecodeError, TypeError):
            return {"errors": errors, "work_counts": "UNMEASURABLE"}
        works = material["logical_works"] if shared else [{"material": material}]
        counts = []
        for index, work in enumerate(works):
            current = work["material"]
            prefix = "work_results[%d]." % index if shared else ""
            replies = payload.get("work_results", []) if shared and isinstance(payload, dict) else []
            matches = [item for item in replies if isinstance(item, dict) and item.get("logical_work_ref") == work["logical_work_ref"]] if shared and isinstance(replies, list) else []
            reply = matches[0] if len(matches) == 1 else None if shared else payload
            rows = reply.get("results") if isinstance(reply, dict) else None
            groups = set()
            measurable = isinstance(rows, list)
            allowed = {item["assertion_ref"] for item in current["assertions"]}
            sections = {ref: item["section_id"] for ref, item in current["assertion_context"].items()}
            for row_index, row in enumerate(rows or () if isinstance(rows, list) else ()):
                path = prefix + "results[%d]" % row_index
                if not isinstance(row, dict):
                    measurable = False
                    continue
                members = row.get("member_assertion_refs")
                if not isinstance(members, list) or not all(isinstance(ref, str) for ref in members):
                    measurable = False
                    continue
                groups.add(tuple(sorted(set(members))))
                for bad, code, details in (
                    (members != sorted(set(members)), "MEMBER_ORDER_OR_UNIQUENESS", {"expected_order": sorted(set(members))}),
                    (not 2 <= len(members) <= current["profile"]["composite_max_arity"], "ARITY", {}),
                    (not set(members) <= allowed, "SOURCE_DOMAIN", {"foreign_refs": sorted(set(members) - allowed)}),
                    (set(members) <= allowed and not ConsistencyDiscoveryPlanner.eligible(members, validation_scope=current["validation_scope"], assertion_sections=sections), "SCOPE", {}),
                ):
                    if bad:
                        errors.append({"path": path + ".member_assertion_refs", "code": code,
                                       "member_count": len(members), "maximum": current["profile"]["composite_max_arity"], **details})
                diagnostic = cls._normalize_reason_order({"results": [row]}, canonical_sets=
                    current.get("instruction_profile", {}).get("reply_label_canonicalization_version") == _LABEL_CANONICALIZATION_VERSION)["results"][0]
                try:
                    ConsistencyRelationReplyRow.model_validate(diagnostic)
                except ValidationError as row_error:
                    for item in row_error.errors():
                        if item["type"] == "value_error" and "relation members must be canonical" not in item["msg"]:
                            errors.append({"path": path, "code": "ROW_SCHEMA", "message": item["msg"]})
            required = {tuple(item) for item in current["required_relation_member_sets"]}
            missing = [list(item) for item in sorted(required - groups)] if measurable else "UNMEASURABLE"
            count = len(groups - required) if measurable else "UNMEASURABLE"
            counts.append({"logical_work_ref": work.get("logical_work_ref"), "candidate_count": count,
                           "semantic_candidate_budget": current["semantic_candidate_budget"],
                           "composite_max_arity": current["profile"]["composite_max_arity"], "missing_required_sets": missing})
            if measurable and count > current["semantic_candidate_budget"]:
                errors.append({"path": prefix + "results", "code": "CANDIDATE_BUDGET", "candidate_count": count,
                               "semantic_candidate_budget": current["semantic_candidate_budget"]})
            if missing and measurable:
                errors.append({"path": prefix + "results", "code": "REQUIRED_SETS_MISSING", "member_sets": missing})
        if not errors:
            errors.append({"path": "/", "code": "REPLY_SCHEMA_OR_DISCOVERY", "message": "Original strict Parser rejected this complete reply."})
        diagnostic = {"errors": errors, "work_counts": counts}
        if (material.get("instruction_profile", {}).get("protocol_correction_policy_version") == _PROTOCOL_CORRECTION_VERSION_V2
                and errors and all(error["code"] == "MEMBER_ORDER_OR_UNIQUENESS" for error in errors)
                and not cls._member_order_only(raw, material, diagnostic)):
            errors.append({"path": "/", "code": "REPLY_SCHEMA_OR_DISCOVERY",
                           "message": "Member-order-only precondition could not be established; use general bounded-discovery correction."})
        return diagnostic

    @staticmethod
    def _parsed_candidate(parsed, material):
        if material.get("contract") == CONSISTENCY_SHARED_RELATION_CONTRACT:
            return {"work_results": [{"logical_work_ref": item["logical_work_ref"], "discovery_complete": True,
                                      "results": [row.model_dump(mode="json") for row in parsed[item["logical_work_ref"]]]}
                                     for item in material["logical_works"]]}
        return {"discovery_complete": True, "results": [row.model_dump(mode="json") for row in parsed]}

    def _protocol_generate(self, material, identity, lineage, validate_current, request, intent_ref, chain):
        policy = material["instruction_profile"]["protocol_correction_policy_version"]
        instruction = _correction_instruction(policy)
        shared = material.get("contract") == CONSISTENCY_SHARED_RELATION_CONTRACT
        parser = self._parse_shared_reply if shared else self._parse_reply
        selected_material, selected_ref = material, intent_ref
        for ordinal in (0, 1):
            snapshot = request.model_dump(mode="json")
            response = self.gateway.generate(request)
            generation_ref = selected_ref.removesuffix("/input") + "/generation"
            response_payload = response.model_dump(mode="json")
            generation_payload = {"request": snapshot, "response": response_payload,
                                  "response_sha256": canonical_sha256(response_payload),
                                  "raw_reply_sha256": sha256((response.content or "").encode("utf-8")).hexdigest()}
            if material.get("instruction_profile", {}).get("reply_label_canonicalization_version") == _LABEL_CANONICALIZATION_VERSION:
                try:
                    canonical = self._normalize_reason_order(json.loads(response.content or ""), canonical_sets=True)
                    canonical_hash = canonical_sha256(canonical)
                except (json.JSONDecodeError, ValueError):
                    canonical, canonical_hash = None, canonical_sha256(None)
                generation_payload.update(canonical_reply=canonical, canonical_reply_sha256=canonical_hash)
            self.store.put_json(generation_payload,
                                artifact_ref=generation_ref, artifact_type="artifact_consistency_result",
                                schema_version="artifact_consistency_result_v1", created_by=CONSISTENCY_POLICY_ID, **identity)
            chain["child_response_ref" if ordinal else "first_response_ref"] = generation_ref
            if (response.model_call_id, response.task_id, response.run_id) != (request.model_call_id, request.task_id, request.run_id):
                raise ConsistencyCompositionError("Consistency provider identity mismatch")
            if not response.success or response.error is not None:
                error = response.error
                raise ConsistencyCompositionError(error.message if error else response.error_message or "Consistency provider failed",
                    error_code=error.error_code if error else "CONSISTENCY_PROVIDER_FAILED",
                    failure_category=error.failure_category if error else FailureCategory.UNKNOWN, failure_origin=response.failure_origin)
            validate_current()
            try:
                parsed = parser(response.content, material)
            except (json.JSONDecodeError, ValidationError, ConsistencyCompositionError):
                if ordinal or response.finish_reason != "stop" or response.metadata.get("usage_complete") is False or response.metadata.get("execution_cancelled"):
                    raise
                diagnostic = self._protocol_feedback(response.content, material)
                error_ref = intent_ref.removesuffix("/input") + "/protocol-error"
                self.store.put_json(diagnostic, artifact_ref=error_ref, artifact_type="artifact_consistency_result",
                                    schema_version="artifact_consistency_result_v1", created_by=CONSISTENCY_POLICY_ID, **identity)
                chain["protocol_error_ref"] = error_ref
                feedback = {"policy_version": policy, "correction_ordinal": 1,
                            "parent_input_ref": intent_ref, "parent_input_hash": canonical_sha256(material),
                            "first_response_ref": generation_ref, "first_response_sha256": canonical_sha256(response_payload),
                            "raw_reply_sha256": sha256((response.content or "").encode("utf-8")).hexdigest(),
                            "protocol_error_ref": error_ref, "protocol_error_sha256": canonical_sha256(diagnostic),
                            "diagnostic": diagnostic,
                            "instruction": instruction}
                validate_current()
                self.post_write.lifecycle_service.assert_attempt_committable(run_id=lineage.run_id,
                    attempt_scope_ref=lineage.attempt_scope_ref, attempt_id=lineage.attempt_id, input_snapshot_version=lineage.input_snapshot_version)
                selected_material = {**material, "protocol_correction_feedback": feedback}
                child_key = canonical_sha256(selected_material)
                selected_ref = intent_ref.rsplit("/", 2)[0] + "/" + child_key + "/input"
                occurrence = ("consistency-shared-call://sha256/" if shared else "consistency-call://sha256/") + child_key
                package, limit = self._package(selected_material, lineage, occurrence)
                request = request.model_copy(update={"model_call_id": ("model_call_consistency_shared_" if shared else "model_call_consistency_") + child_key,
                    "messages": package.model_input.final_input_material.inline_final_input_payload["messages"],
                    "max_tokens": limit, "created_at": datetime.now(timezone.utc).isoformat(),
                    "extra": {**snapshot["extra"], "logical_operation_id": occurrence,
                              "llm_context_summary": {"managed": True, "package_id": package.package_ref.context_package_id,
                                  "context_sha256": package.model_input.final_model_input_sha256}}})
                self.store.put_json({"input_hash": child_key, "input_material": selected_material,
                                     "context_package": package.model_dump(mode="json"), "request": request.model_dump(mode="json")},
                                    artifact_ref=selected_ref, artifact_type="artifact_consistency_input",
                                    schema_version="artifact_consistency_input_v1", created_by=CONSISTENCY_POLICY_ID, **identity)
                chain["child_input_ref"] = chain["selected_input_ref"] = selected_ref
                checkpoint_current_execution()
                validate_current()
            else:
                validate_current()
                return parsed

    def _verify_protocol_receipt(self, intent_ref, intent, result, identity):
        material = intent["input_material"]
        policy = material.get("instruction_profile", {}).get("protocol_correction_policy_version")
        chain = result.get("protocol_correction")
        if policy is None:
            if chain is not None:
                raise ConsistencyCompositionError("legacy Consistency cannot acquire a correction chain")
            return intent_ref
        def require(condition):
            if not condition:
                raise ConsistencyCompositionError("Consistency protocol correction proof differs")
        instruction = _correction_instruction(policy)
        require(isinstance(chain, dict) and chain.get("policy_version") == policy)
        require(set(chain) == {"policy_version", "first_response_ref", "protocol_error_ref",
                               "child_input_ref", "child_response_ref", "selected_input_ref"})
        shared = material.get("contract") == CONSISTENCY_SHARED_RELATION_CONTRACT
        parser = self._parse_shared_reply if shared else self._parse_reply
        parent_key = canonical_sha256(material)
        require(intent["input_hash"] == result["input_hash"] == parent_key
                and intent_ref.endswith("/" + parent_key + "/input"))
        parent_request, package = intent["request"], intent["context_package"]
        ContextPackageSchema.model_validate(package)
        final = package["model_input"]["final_input_material"]["inline_final_input_payload"]
        sources = package["selected_materializations"]
        subject = package["invocation_subject"]
        occurrence = ("consistency-shared-call://sha256/" if shared else "consistency-call://sha256/") + parent_key
        require(len(sources) == 1 and sources[0]["materialized_payload"] == self._model_source(material)
                and sources[0]["materialized_payload_sha256"] == canonical_sha256(self._model_source(material))
                and parent_request["messages"] == final["messages"]
                and parent_request["extra"]["llm_context_summary"]["context_sha256"] == canonical_sha256(final)
                and parent_request["extra"]["llm_context_summary"]["package_id"] == package["package_ref"]["context_package_id"]
                and parent_request["model_call_id"] == ("model_call_consistency_shared_" if shared else "model_call_consistency_") + parent_key
                and (parent_request["task_id"], parent_request["run_id"]) == (identity["task_id"], identity["run_id"])
                and subject["invocation_id"] == subject["logical_operation_id"] == parent_request["extra"]["logical_operation_id"] == occurrence
                and subject["run_id"] == identity["run_id"]
                and subject["attempt_id"] == parent_request["extra"]["attempt_id"]
                and subject["consumer_scope_ref"] == parent_request["extra"]["attempt_scope_ref"]
                and parent_request["max_tokens"] == package["budget_binding"]["reserved_output_tokens"]
                and parent_request["temperature"] == 0.0)
        for field, suffix in (("first_response_ref", "/generation"), ("protocol_error_ref", "/protocol-error")):
            require(chain.get(field) is None or chain[field] == intent_ref.removesuffix("/input") + suffix)
        def generation(ref, expected_request):
            value = self.store.get_json(ref, **identity)
            response = ModelResponseSchema.model_validate(value["response"])
            require(value["request"] == expected_request and value["response_sha256"] == canonical_sha256(value["response"])
                    and value["raw_reply_sha256"] == sha256((response.content or "").encode("utf-8")).hexdigest()
                    and (response.model_call_id, response.task_id, response.run_id) ==
                    (expected_request["model_call_id"], expected_request["task_id"], expected_request["run_id"]))
            if material.get("instruction_profile", {}).get("reply_label_canonicalization_version") == _LABEL_CANONICALIZATION_VERSION:
                try:
                    canonical = self._normalize_reason_order(json.loads(response.content or ""), canonical_sets=True)
                    canonical_hash = canonical_sha256(canonical)
                except (json.JSONDecodeError, ValueError):
                    canonical, canonical_hash = None, canonical_sha256(None)
                require("canonical_reply" in value and value["canonical_reply"] == canonical
                        and value.get("canonical_reply_sha256") == canonical_hash)
            return response
        first = generation(chain["first_response_ref"], intent["request"]) if chain.get("first_response_ref") else None
        selected_ref, selected_response = intent_ref, first
        if chain.get("child_input_ref"):
            require(first is not None and first.success and first.error is None and first.finish_reason == "stop"
                    and first.metadata.get("usage_complete") is not False and not first.metadata.get("execution_cancelled"))
            diagnostic = self._protocol_feedback(first.content, material)
            require(diagnostic is not None and self.store.get_json(chain["protocol_error_ref"], **identity) == diagnostic)
            child_ref = chain["child_input_ref"]
            child = self.store.get_json(child_ref, **identity)
            child_material = child["input_material"]
            feedback = child_material.get("protocol_correction_feedback")
            require(isinstance(feedback, dict) and {k: v for k, v in child_material.items() if k != "protocol_correction_feedback"} == material)
            require(feedback["policy_version"] == policy and feedback["correction_ordinal"] == 1
                    and feedback["parent_input_ref"] == intent_ref and feedback["parent_input_hash"] == canonical_sha256(material)
                    and feedback["first_response_ref"] == chain["first_response_ref"]
                    and feedback["first_response_sha256"] == canonical_sha256(first.model_dump(mode="json"))
                    and feedback["raw_reply_sha256"] == sha256((first.content or "").encode("utf-8")).hexdigest()
                    and feedback["protocol_error_ref"] == chain["protocol_error_ref"]
                    and feedback["protocol_error_sha256"] == canonical_sha256(diagnostic) and feedback["diagnostic"] == diagnostic
                    and feedback["instruction"] == instruction
                    and set(feedback) == {"policy_version", "correction_ordinal", "parent_input_ref", "parent_input_hash",
                                          "first_response_ref", "first_response_sha256", "raw_reply_sha256",
                                          "protocol_error_ref", "protocol_error_sha256", "diagnostic", "instruction"})
            child_key = canonical_sha256(child_material)
            require(child["input_hash"] == child_key and child_ref == intent_ref.rsplit("/", 2)[0] + "/" + child_key + "/input")
            child_request = child["request"]
            require(child_request["model_call_id"] == ("model_call_consistency_shared_" if shared else "model_call_consistency_") + child_key
                    and child_request["model_call_id"] != intent["request"]["model_call_id"]
                    and (child_request["task_id"], child_request["run_id"]) == (identity["task_id"], identity["run_id"]))
            ContextPackageSchema.model_validate(child["context_package"])
            selected_sources = child["context_package"]["selected_materializations"]
            require(len(selected_sources) == 1 and selected_sources[0]["materialized_payload"] == self._model_source(material)
                    and selected_sources[0]["materialized_payload_sha256"] == canonical_sha256(self._model_source(material)))
            final = child["context_package"]["model_input"]["final_input_material"]["inline_final_input_payload"]
            require(child_request["messages"] == final["messages"]
                    and child_request["extra"]["llm_context_summary"]["context_sha256"] == canonical_sha256(final)
                    and child["context_package"]["model_input"]["final_model_input_sha256"] == canonical_sha256(final))
            occurrence = ("consistency-shared-call://sha256/" if shared else "consistency-call://sha256/") + child_key
            subject = child["context_package"]["invocation_subject"]
            require(child_request["extra"]["logical_operation_id"] == occurrence
                    and subject["invocation_id"] == subject["logical_operation_id"] == occurrence
                    and subject["run_id"] == identity["run_id"]
                    and subject["attempt_id"] == child_request["extra"]["attempt_id"]
                    and subject["consumer_scope_ref"] == child_request["extra"]["attempt_scope_ref"]
                    and child_request["extra"]["llm_context_summary"]["package_id"] == child["context_package"]["package_ref"]["context_package_id"])
            require(child_request["max_tokens"] == child["context_package"]["budget_binding"]["reserved_output_tokens"]
                    and child_request["temperature"] == 0.0)
            for field in ("model_role", "caller_agent", "max_tokens", "temperature"):
                require(child_request[field] == intent["request"][field])
            for field in ("attempt_id", "attempt_scope_ref", "input_snapshot_version", "response_contract", "instruction_profile_ref", "instruction_profile_sha256"):
                require(child_request["extra"][field] == intent["request"]["extra"][field])
            messages = [dict(item) for item in intent["request"]["messages"]]
            for message in messages:
                if message["content"].startswith("CONTROL_REF="):
                    control = json.loads(message["content"].split("\n", 1)[1])
                    control["protocol_correction_feedback"] = feedback
                    control_text = json.dumps(control, sort_keys=True, separators=(",", ":"))
                    ref = ("consistency-shared-batch://sha256/" if shared else "consistency-batch://sha256/") + canonical_sha256(control_text)
                    message["content"] = "CONTROL_REF=" + ref + "\n" + control_text
            require(child_request["messages"] == messages)
            binding = child["context_package"]["instruction_binding"]
            require(len(binding["dynamic_control_bindings"]) == 1
                    and binding["dynamic_control_bindings"][0]["control_ref"] == ref
                    and binding["dynamic_control_bindings"][0]["control_sha256"] == canonical_sha256(control_text)
                    and binding["dynamic_control_bindings"][0]["control_version"] == _DYNAMIC_CONTROL_PROJECTION_VERSION)
            output = next(item["content"] for item in messages if item["content"].startswith('{"contract":'))
            require(binding["task_output_contract_sha256"] == canonical_sha256(output))
            child_result = self.store.get_json(child_ref.removesuffix("/input") + "/result", **identity)
            require(child_result["input_hash"] == child_key and child_result.get("outcome") == result.get("outcome")
                    and child_result.get("candidate") == result.get("candidate") and child_result.get("failure") == result.get("failure"))
            require(chain.get("child_response_ref") is None or chain["child_response_ref"] == child_ref.removesuffix("/input") + "/generation")
            selected_ref = child_ref
            selected_response = generation(chain["child_response_ref"], child_request) if chain.get("child_response_ref") else None
        else:
            require(chain.get("child_response_ref") is None)
            if chain.get("protocol_error_ref"):
                require(first is not None and self._protocol_feedback(first.content, material) == self.store.get_json(chain["protocol_error_ref"], **identity))
        require(chain.get("selected_input_ref") == selected_ref)
        if result.get("outcome") == "completed":
            require(selected_response is not None and selected_response.success and selected_response.error is None)
            parsed = parser(selected_response.content, material)
            require(result["candidate"] == self._parsed_candidate(parsed, material))
        else:
            require(result.get("outcome") == "failed" and isinstance(result.get("failure"), dict))
            failure = result["failure"]
            if selected_response is not None and (not selected_response.success or selected_response.error is not None):
                error = selected_response.error
                require(failure.get("error_type") == "ConsistencyCompositionError"
                        and failure.get("error_code") == (error.error_code if error else "CONSISTENCY_PROVIDER_FAILED")
                        and failure.get("message") == (error.message if error else selected_response.error_message or "Consistency provider failed")
                        and failure.get("category") == (error.failure_category.value if error else FailureCategory.UNKNOWN.value)
                        and failure.get("origin") == selected_response.failure_origin.value)
            elif failure.get("origin") == FailureOrigin.PROVIDER_RUNTIME.value:
                require(False)
        return selected_ref


    def _evaluate(self, material, identity, lineage, validate_current):
        if "protocol_correction_feedback" in material:
            raise ConsistencyCompositionError("correction Child can only dispatch through its Parent")
        key = canonical_sha256(material)
        intent_ref = f"artifact://artifact-consistency/{key}/input"
        result_ref = f"artifact://artifact-consistency/{key}/result"

        def read(ref):
            try:
                return self.store.get_json(ref, **identity)
            except FileNotFoundError:
                return None

        with self._lock, InterProcessFileLock(self.store.root / "consistency_locks" / (key + ".lock")):
            prior, result = read(intent_ref), read(result_ref)
            if prior is not None and (prior.get("input_hash") != key or prior.get("input_material") != material):
                raise ConsistencyCompositionError("Consistency immutable intent differs")
            if result is not None:
                if prior is None or result.get("input_hash") != key:
                    raise ConsistencyCompositionError("Consistency result has no exact intent")
                selected_ref = self._verify_protocol_receipt(intent_ref, prior, result, identity)
                if result.get("outcome") == "failed":
                    failure = result.get("failure") or {}
                    if failure.get("kind") == "CANONICAL_BUDGET_FAILURE":
                        raise ConsistencyCompositionError(
                            failure.get("message") or "canonical Budget failure",
                            error_code=failure.get("error_code") or "CONSISTENCY_BUDGET_FAILED",
                            failure_category=failure.get("category") or FailureCategory.RESOURCE_EXHAUSTED,
                            failure_origin=failure.get("origin") or FailureOrigin.CANONICAL_BUDGET,
                        )
                    return None, selected_ref, failure
                validate_current()
                return self._parse_reply(result["candidate"], material), selected_ref, None
            if prior is not None:
                raise ConsistencyCompositionError("Consistency intent without result; no automatic redispatch")
            self._require_current_instruction_profile(material)
            for previous_profile in _previous_instruction_profiles():
                previous_key = canonical_sha256({**material, "instruction_profile": previous_profile})
                if read(f"artifact://artifact-consistency/{previous_key}/input") is not None or read(f"artifact://artifact-consistency/{previous_key}/result") is not None:
                    raise ConsistencyCompositionError("historical Consistency work cannot redispatch under a new policy")
            legacy_key = canonical_sha256(self._legacy_material(material))
            if read(f"artifact://artifact-consistency/{legacy_key}/input") is not None or read(
                f"artifact://artifact-consistency/{legacy_key}/result"
            ) is not None:
                raise ConsistencyCompositionError(
                    "legacy Consistency work cannot continue under the current instruction profile"
                )
            validate_current()
            self.post_write.lifecycle_service.assert_attempt_committable(
                run_id=lineage.run_id,
                attempt_scope_ref=lineage.attempt_scope_ref,
                attempt_id=lineage.attempt_id,
                input_snapshot_version=lineage.input_snapshot_version,
            )
            occurrence = "consistency-call://sha256/" + key
            package, limit = self._package(material, lineage, occurrence)
            request = ModelRequestSchema(
                model_call_id="model_call_consistency_" + key,
                task_id=identity["task_id"],
                run_id=identity["run_id"],
                caller_agent="ArtifactConsistencyEvaluator",
                model_role=ModelRole.GENERAL.value,
                prompt="",
                messages=package.model_input.final_input_material.inline_final_input_payload["messages"],
                max_tokens=limit,
                temperature=0.0,
                created_at=datetime.now(timezone.utc).isoformat(),
                extra={
                    "call_purpose": "artifact_consistency",
                    "response_contract": CONSISTENCY_RELATION_CONTRACT,
                    "section_id": identity["section_id"],
                    "attempt_scope_ref": lineage.attempt_scope_ref,
                    "attempt_id": lineage.attempt_id,
                    "input_snapshot_version": lineage.input_snapshot_version,
                    "logical_operation_id": occurrence,
                    "prompt_id": CONSISTENCY_RELATION_CONTRACT,
                    "prompt_version": _PROMPT_VERSION,
                    "instruction_profile_ref": material["instruction_profile"]["config_ref"],
                    "instruction_profile_sha256": material["instruction_profile"]["profile_hash"],
                    "llm_context_summary": {"managed": True, "package_id": package.package_ref.context_package_id, "context_sha256": package.model_input.final_model_input_sha256},
                },
            )
            self.store.put_json(
                {"schema_version": "artifact_consistency_input_v1", "input_hash": key, "input_material": material, "context_package": package.model_dump(mode="json"), "request": request.model_dump(mode="json")},
                artifact_ref=intent_ref,
                artifact_type="artifact_consistency_input",
                schema_version="artifact_consistency_input_v1",
                created_by=CONSISTENCY_POLICY_ID,
                **identity,
            )
            chain = {"policy_version": material["instruction_profile"]["protocol_correction_policy_version"], "first_response_ref": None, "protocol_error_ref": None,
                     "child_input_ref": None, "child_response_ref": None, "selected_input_ref": intent_ref}
            try:
                checkpoint_current_execution()
                validate_current()
                rows = self._protocol_generate(material, identity, lineage, validate_current, request, intent_ref, chain)
                validate_current()
                result = {"schema_version": "artifact_consistency_result_v1", "input_hash": key, "outcome": "completed", "candidate": {"discovery_complete": True, "results": [item.model_dump(mode="json") for item in rows]}}
                failure = None
            except Exception as exc:
                budget = isinstance(exc, (CanonicalBudgetCapacityExceeded, CanonicalBudgetDeadlineExceeded, CanonicalBudgetDraining))
                category = FailureCategory.RESOURCE_EXHAUSTED if budget else classify_failure(exc)
                origin = FailureOrigin.CANONICAL_BUDGET if budget else FailureOrigin(getattr(exc, "failure_origin", FailureOrigin.UNKNOWN))
                failure = {"schema_version": "artifact_consistency_failure_v1", "kind": "CANONICAL_BUDGET_FAILURE" if budget else "CONSISTENCY_TECHNICAL_FAILURE", "error_type": type(exc).__name__, "error_code": getattr(exc, "error_code", type(exc).__name__), "message": str(exc), "category": category.value, "origin": origin.value}
                result = {"schema_version": "artifact_consistency_result_v1", "input_hash": key, "outcome": "failed", "failure": failure}
                rows = None
            result["protocol_correction"] = chain
            selected_ref = chain["selected_input_ref"]
            if chain["child_input_ref"] is not None:
                child = self.store.get_json(selected_ref, **identity)
                child_result = {**result, "input_hash": child["input_hash"]}
                child_result.pop("protocol_correction")
                self.store.put_json(child_result, artifact_ref=selected_ref.removesuffix("/input") + "/result",
                    artifact_type="artifact_consistency_result", schema_version="artifact_consistency_result_v1", created_by=CONSISTENCY_POLICY_ID, **identity)
            self.store.put_json(
                result,
                artifact_ref=result_ref,
                artifact_type="artifact_consistency_result",
                schema_version="artifact_consistency_result_v1",
                created_by=CONSISTENCY_POLICY_ID,
                **identity,
            )
            if failure and failure["kind"] == "CANONICAL_BUDGET_FAILURE":
                raise ConsistencyCompositionError(failure["message"], error_code=failure["error_code"], failure_category=failure["category"], failure_origin=failure["origin"])
            return rows, selected_ref, failure

    def _evaluate_shared(self, materials, identity, lineage, validate_current):
        materials = tuple(materials)
        if any("protocol_correction_feedback" in value for value in materials):
            raise ConsistencyCompositionError("correction Child can only dispatch through its Parent")
        shared_material = self._shared_material(materials)
        key = canonical_sha256(shared_material)
        intent_ref = f"artifact://artifact-consistency-shared/{key}/input"
        result_ref = f"artifact://artifact-consistency-shared/{key}/result"

        def read(ref):
            try:
                return self.store.get_json(ref, **identity)
            except FileNotFoundError:
                return None

        with self._lock, InterProcessFileLock(
            self.store.root / "consistency_shared_locks" / (key + ".lock")
        ):
            prior, result = read(intent_ref), read(result_ref)
            if prior is not None and (
                prior.get("input_hash") != key
                or prior.get("input_material") != shared_material
            ):
                raise ConsistencyCompositionError(
                    "shared Consistency immutable intent differs"
                )
            if result is not None:
                if prior is None or result.get("input_hash") != key:
                    raise ConsistencyCompositionError(
                        "shared Consistency result has no exact intent"
                    )
                selected_ref = self._verify_protocol_receipt(intent_ref, prior, result, identity)
                if result.get("outcome") == "failed":
                    failure = result.get("failure") or {}
                    if failure.get("kind") == "CANONICAL_BUDGET_FAILURE":
                        raise ConsistencyCompositionError(
                            failure.get("message") or "canonical Budget failure",
                            error_code=failure.get("error_code")
                            or "CONSISTENCY_BUDGET_FAILED",
                            failure_category=failure.get("category")
                            or FailureCategory.RESOURCE_EXHAUSTED,
                            failure_origin=failure.get("origin")
                            or FailureOrigin.CANONICAL_BUDGET,
                        )
                    return None, selected_ref, failure
                if result.get("outcome") != "completed":
                    raise ConsistencyCompositionError(
                        "shared Consistency result outcome is invalid"
                    )
                validate_current()
                return (
                    self._parse_shared_reply(
                        result.get("candidate"), shared_material
                    ),
                    selected_ref,
                    None,
                )
            if prior is not None:
                raise ConsistencyCompositionError(
                    "shared Consistency intent without result; no automatic redispatch"
                )
            for material in materials:
                self._require_current_instruction_profile(material)
            for previous_profile in _previous_instruction_profiles():
                previous_material = self._shared_material({**value, "instruction_profile": previous_profile} for value in materials)
                previous_key = canonical_sha256(previous_material)
                if read(f"artifact://artifact-consistency-shared/{previous_key}/input") is not None or read(f"artifact://artifact-consistency-shared/{previous_key}/result") is not None:
                    raise ConsistencyCompositionError("historical shared work cannot redispatch under a new policy")
            legacy_shared_material = self._shared_material(
                self._legacy_material(material) for material in materials
            )
            legacy_key = canonical_sha256(legacy_shared_material)
            if read(f"artifact://artifact-consistency-shared/{legacy_key}/input") is not None or read(
                f"artifact://artifact-consistency-shared/{legacy_key}/result"
            ) is not None:
                raise ConsistencyCompositionError(
                    "legacy shared Consistency work cannot continue under the current instruction profile"
                )

            # complete_item() already entered with an exact canonical Claim
            # source snapshot.  The full validate_current() retained after
            # immutable intent persistence is the authoritative pre-dispatch
            # fence, so another full source traversal here is duplicate I/O.
            self.post_write.lifecycle_service.assert_attempt_committable(
                run_id=lineage.run_id,
                attempt_scope_ref=lineage.attempt_scope_ref,
                attempt_id=lineage.attempt_id,
                input_snapshot_version=lineage.input_snapshot_version,
            )
            occurrence = "consistency-shared-call://sha256/" + key
            package, limit = self._package(
                shared_material, lineage, occurrence
            )
            request = ModelRequestSchema(
                model_call_id="model_call_consistency_shared_" + key,
                task_id=identity["task_id"],
                run_id=identity["run_id"],
                caller_agent="ArtifactConsistencySharedEvaluator",
                model_role=ModelRole.GENERAL.value,
                prompt="",
                messages=package.model_input.final_input_material.inline_final_input_payload[
                    "messages"
                ],
                max_tokens=limit,
                temperature=0.0,
                created_at=datetime.now(timezone.utc).isoformat(),
                extra={
                    "call_purpose": "artifact_consistency_shared",
                    "response_contract": CONSISTENCY_SHARED_RELATION_CONTRACT,
                    "section_id": identity["section_id"],
                    "attempt_scope_ref": lineage.attempt_scope_ref,
                    "attempt_id": lineage.attempt_id,
                    "input_snapshot_version": lineage.input_snapshot_version,
                    "logical_operation_id": occurrence,
                    "prompt_id": CONSISTENCY_SHARED_RELATION_CONTRACT,
                    "prompt_version": _PROMPT_VERSION,
                    "instruction_profile_ref": shared_material["instruction_profile"]["config_ref"],
                    "instruction_profile_sha256": shared_material["instruction_profile"]["profile_hash"],
                    "logical_consumer_refs": [
                        item["logical_work_ref"]
                        for item in shared_material["logical_works"]
                    ],
                    "llm_context_summary": {
                        "managed": True,
                        "package_id": package.package_ref.context_package_id,
                        "context_sha256": package.model_input.final_model_input_sha256,
                    },
                },
            )
            self.store.put_json(
                {
                    "schema_version": "artifact_consistency_shared_input_v1",
                    "input_hash": key,
                    "input_material": shared_material,
                    "context_package": package.model_dump(mode="json"),
                    "request": request.model_dump(mode="json"),
                },
                artifact_ref=intent_ref,
                artifact_type="artifact_consistency_shared_input",
                schema_version="artifact_consistency_shared_input_v1",
                created_by=CONSISTENCY_POLICY_ID,
                **identity,
            )
            chain = {"policy_version": shared_material["instruction_profile"]["protocol_correction_policy_version"], "first_response_ref": None, "protocol_error_ref": None,
                     "child_input_ref": None, "child_response_ref": None, "selected_input_ref": intent_ref}
            try:
                checkpoint_current_execution()
                validate_current()
                parsed = self._protocol_generate(shared_material, identity, lineage, validate_current, request, intent_ref, chain)
                validate_current()
                candidate = {
                    "work_results": [
                        {
                            "logical_work_ref": item["logical_work_ref"],
                            "discovery_complete": True,
                            "results": [
                                row.model_dump(mode="json")
                                for row in parsed[item["logical_work_ref"]]
                            ],
                        }
                        for item in shared_material["logical_works"]
                    ]
                }
                result = {
                    "schema_version": "artifact_consistency_shared_result_v1",
                    "input_hash": key,
                    "outcome": "completed",
                    "candidate": candidate,
                }
                failure = None
            except Exception as exc:
                budget = isinstance(
                    exc,
                    (
                        CanonicalBudgetCapacityExceeded,
                        CanonicalBudgetDeadlineExceeded,
                        CanonicalBudgetDraining,
                    ),
                )
                category = (
                    FailureCategory.RESOURCE_EXHAUSTED
                    if budget
                    else classify_failure(exc)
                )
                origin = (
                    FailureOrigin.CANONICAL_BUDGET
                    if budget
                    else FailureOrigin(
                        getattr(exc, "failure_origin", FailureOrigin.UNKNOWN)
                    )
                )
                failure = {
                    "schema_version": "artifact_consistency_failure_v1",
                    "kind": (
                        "CANONICAL_BUDGET_FAILURE"
                        if budget
                        else "CONSISTENCY_SHARED_TECHNICAL_FAILURE"
                    ),
                    "error_type": type(exc).__name__,
                    "error_code": getattr(
                        exc, "error_code", type(exc).__name__
                    ),
                    "message": str(exc),
                    "category": category.value,
                    "origin": origin.value,
                }
                result = {
                    "schema_version": "artifact_consistency_shared_result_v1",
                    "input_hash": key,
                    "outcome": "failed",
                    "failure": failure,
                }
                parsed = None
            result["protocol_correction"] = chain
            selected_ref = chain["selected_input_ref"]
            if chain["child_input_ref"] is not None:
                child = self.store.get_json(selected_ref, **identity)
                child_result = {**result, "input_hash": child["input_hash"]}
                child_result.pop("protocol_correction")
                self.store.put_json(child_result, artifact_ref=selected_ref.removesuffix("/input") + "/result",
                    artifact_type="artifact_consistency_result", schema_version="artifact_consistency_result_v1", created_by=CONSISTENCY_POLICY_ID, **identity)
            self.store.put_json(
                result,
                artifact_ref=result_ref,
                artifact_type="artifact_consistency_shared_result",
                schema_version="artifact_consistency_shared_result_v1",
                created_by=CONSISTENCY_POLICY_ID,
                **identity,
            )
            if failure and failure["kind"] == "CANONICAL_BUDGET_FAILURE":
                raise ConsistencyCompositionError(
                    failure["message"],
                    error_code=failure["error_code"],
                    failure_category=failure["category"],
                    failure_origin=failure["origin"],
                )
            return parsed, selected_ref, failure

    def _prepare_document_global(
        self,
        *,
        validation_run_id,
        subject_hash,
        artifact_ref,
        quality_contract_ref,
        assertions,
        assertion_sections,
        source_snapshot_hash,
        material,
    ):
        """Resolve/commit exact global procedure input before semantic dispatch."""
        pw = self.post_write

        def canonical_universe_reader(exact_artifact_ref, exact_subject_hash):
            if exact_artifact_ref != artifact_ref or exact_subject_hash != subject_hash:
                raise ConsistencyCompositionError(
                    "global Consistency universe request differs from exact subject"
                )
            return assertions, dict(assertion_sections), source_snapshot_hash

        resolver = ConsistencyProcedureInputResolver(
            contract_store=pw.contract_store,
            requirement_store=pw.requirement_store,
            canonical_universe_reader=canonical_universe_reader,
            intermediate_runtime=pw.validation_intermediate_runtime,
        )
        lowering = resolver.resolve(
            contract_ref=quality_contract_ref,
            artifact_state_ref=artifact_ref,
            validation_run_id=validation_run_id,
            validation_subject_hash=subject_hash,
        )
        lowering_ref = resolver.commit(lowering)
        global_material = None
        if lowering.document_global_applicability == "APPLICABLE":
            if len(lowering.global_work) != 1:
                raise ConsistencyCompositionError(
                    "production v1 requires one exact Document-global logical selector"
                )
            global_material = {
                **material,
                "validation_scope": "DOCUMENT_GLOBAL",
                "target_ref": lowering.global_work[0].selector.target_ref,
                "discovery_mode": "DOCUMENT_GLOBAL_BOUNDED_DISCOVERY",
                "required_relation_member_sets": [
                    list(item) for item in lowering.required_relation_members()
                ],
                "required_logical_obligation_refs": list(
                    lowering.required_logical_obligations()
                ),
                "runtime_procedure_input_hash": lowering.runtime_input_hash,
            }
        return {
            "lowering": lowering,
            "lowering_ref": lowering_ref,
            "global_material": global_material,
        }

    def _execute_document_global(
        self,
        *,
        validation_run_id,
        subject_hash,
        artifact_ref,
        target_ref,
        quality_contract_ref,
        assertions,
        assertion_by_ref,
        assertion_sections,
        section_by_claim,
        source_target_by_assertion,
        source_snapshot_hash,
        material,
        provenance,
        identity,
        lineage,
        validate_current,
        prepared=None,
        semantic_result=None,
    ):
        """Execute the independently accountable DOCUMENT_GLOBAL procedure."""
        pw = self.post_write
        prepared = prepared or self._prepare_document_global(
            validation_run_id=validation_run_id,
            subject_hash=subject_hash,
            artifact_ref=artifact_ref,
            quality_contract_ref=quality_contract_ref,
            assertions=assertions,
            assertion_sections=assertion_sections,
            source_snapshot_hash=source_snapshot_hash,
            material=material,
        )
        lowering = prepared["lowering"]
        lowering_ref = prepared["lowering_ref"]
        base_result = {
            "lowering_ref": lowering_ref,
            "plan_ref": None,
            "discovery_ref": None,
            "conformance_ref": None,
            "binding_refs": (),
            "executability_refs": (),
            "evaluation_refs": (),
            "facts": (),
            "positive_proofs": (),
            "indeterminate_proofs": (),
            "state": "NOT_APPLICABLE",
            "failure": None,
            "semantic_input_ref": None,
            "intermediate_refs": (lowering_ref,),
        }
        if lowering.document_global_applicability == "UNRESOLVED":
            return {
                **base_result,
                "state": "INDETERMINATE",
                "indeterminate_proofs": (lowering_ref,),
            }
        if lowering.document_global_applicability == "NOT_APPLICABLE":
            return base_result
        if lowering.document_global_applicability != "APPLICABLE":
            raise ConsistencyCompositionError(
                "unknown Document-global Consistency applicability state"
            )
        if len(lowering.global_work) != 1:
            raise ConsistencyCompositionError(
                "production v1 requires one exact Document-global logical selector"
            )

        required_members = lowering.required_relation_members()
        logical_obligations = lowering.required_logical_obligations()
        global_material = prepared["global_material"]
        rows = ()
        semantic_input_ref = None
        failure = None
        # With fewer than two exact assertions the global relation universe is
        # mechanically empty. The logical obligation is still independently
        # planned/accounted and may produce a positive procedural proof.
        if semantic_result is not None:
            rows, semantic_input_ref, failure = semantic_result
        elif len(assertions) >= 2:
            rows, semantic_input_ref, failure = self._evaluate(
                global_material,
                identity,
                lineage,
                validate_current,
            )

        planner = ConsistencyDiscoveryPlanner(
            profile=self.profile,
            intermediate_runtime=pw.validation_intermediate_runtime,
        )
        plan = planner.plan(
            validation_run_id=validation_run_id,
            artifact_state_ref=artifact_ref,
            validation_subject_hash=subject_hash,
            assertions=assertions,
            required_relation_members=required_members,
            validation_scope="DOCUMENT_GLOBAL",
            assertion_sections=assertion_sections,
            runtime_procedure_input=lowering,
        )
        plan_ref = planner.commit(plan)
        candidate_relations = {
            ConsistencyDiscoveryPlanner.relation_key(row.member_assertion_refs): (
                row.member_assertion_refs
            )
            for row in (rows or ())
        }
        discovery_executor = ConsistencyCandidateDiscoveryExecutor(
            pw.validation_intermediate_runtime
        )
        discovery = discovery_executor.execute(
            plan=plan,
            candidate_relations=candidate_relations,
            complete_required_plan_accounting=failure is None,
            discovery_profile_version=self.profile.profile_version,
            provenance_refs=provenance
            + ((semantic_input_ref,) if semantic_input_ref else ()),
            accounted_logical_obligation_refs=(
                logical_obligations if failure is None else ()
            ),
        )
        discovery_ref = discovery_executor.commit(
            validation_run_id=validation_run_id,
            result=discovery,
        )
        conformance_guard = ConsistencyPlanConformanceGuard(
            pw.validation_intermediate_runtime
        )
        conformance = conformance_guard.evaluate(
            plan=plan,
            discovery=discovery,
            profile=self.profile,
            validation_scope="DOCUMENT_GLOBAL",
            assertion_sections=assertion_sections,
            required_relation_members=required_members,
            runtime_procedure_input=lowering,
        )
        conformance_ref = conformance_guard.commit(
            validation_run_id=validation_run_id,
            result=conformance,
        )
        if (
            failure is None
            and conformance.state is not ConsistencyPlanConformanceState.CONFORMING
        ):
            failure = self._local_failure("CONSISTENCY_GLOBAL_PLAN_NONCONFORMING", conformance_ref, conformance)

        binding_refs = []
        unresolved_binding_refs = []
        executability_refs = []
        evaluation_refs = []
        facts = []
        if failure is None:
            for row in rows:
                members = row.member_assertion_refs
                formal_candidate = self._formal_candidate(row, assertion_by_ref)
                semantic = SemanticConsistencyEvaluator(
                    semantic_evaluator=(
                        lambda _assertions, row=row, formal_candidate=formal_candidate: {
                            "relation_materiality": row.relation_materiality,
                            "semantic_relation_description": row.semantic_relation_description,
                            "role_bindings": row.role_bindings,
                            "scope_condition_bindings": row.scope_condition_bindings,
                            "formal_relation_candidate": formal_candidate,
                            "reason_codes": row.reason_codes,
                            "ambiguity_codes": row.ambiguity_codes,
                            "physical_model_call_ref": semantic_input_ref,
                        }
                    ),
                    intermediate_runtime=pw.validation_intermediate_runtime,
                )
                relation_assertions = tuple(
                    assertion_by_ref[ref] for ref in members
                )
                binding = semantic.bind(
                    validation_run_id=validation_run_id,
                    validation_scope="DOCUMENT_GLOBAL",
                    scope_subject_ref=lowering.global_work[0].selector.target_ref,
                    assertions=relation_assertions,
                    source_context_refs=tuple(
                        sorted(
                            "section:"
                            + section_by_claim[source_target_by_assertion[ref]]
                            for ref in members
                        )
                    ),
                    logical_consumer_binding_refs=(
                        self._logical_consumer_ref(
                            validation_scope="DOCUMENT_GLOBAL",
                            scope_subject_ref=lowering.global_work[0].selector.target_ref,
                            members=members,
                        ),
                    ),
                    provenance_refs=provenance
                    + ((semantic_input_ref,) if semantic_input_ref else ()),
                )
                binding_ref = semantic.commit_binding(binding)
                binding_refs.append(binding_ref)
                if (
                    binding.relation_materiality
                    is ConsistencyRelationMateriality.NO_MATERIAL_RELATION
                ):
                    continue
                if (
                    binding.relation_materiality
                    is ConsistencyRelationMateriality.INDETERMINATE
                ):
                    unresolved_binding_refs.append(binding_ref)
                    continue
                formal_guard = FormalExecutabilityGuard(
                    pw.validation_intermediate_runtime
                )
                executability = formal_guard.evaluate(
                    binding=binding,
                    assertions_by_ref=assertion_by_ref,
                )
                executability_ref = formal_guard.commit(
                    validation_run_id=validation_run_id,
                    result=executability,
                )
                executability_refs.append(executability_ref)
                if (
                    executability.state
                    is FormalExecutabilityState.TECHNICAL_FAILURE
                ):
                    failure = self._local_failure("GLOBAL_FORMAL_EXECUTABILITY_TECHNICAL_FAILURE", executability_ref, executability)
                    break
                if executability.state is FormalExecutabilityState.EXECUTABLE:
                    exact = ExactConsistencyEvaluator(
                        pw.validation_intermediate_runtime
                    )
                    evaluation = exact.evaluate(
                        binding=binding,
                        executability=executability,
                        assertions_by_ref=assertion_by_ref,
                    )
                    evaluation_ref = exact.commit(
                        validation_run_id=validation_run_id,
                        result=evaluation,
                    )
                else:
                    evaluation = semantic.evaluate(
                        binding=binding,
                        relation=ArtifactConsistencyRelation(row.relation),
                        observation_codes=row.reason_codes,
                    )
                    evaluation_ref = semantic.commit_evaluation(
                        validation_run_id=validation_run_id,
                        result=evaluation,
                    )
                evaluation_refs.append(evaluation_ref)
                facts.append(
                    ConsistencyProjector.project(
                        validation_run_id=validation_run_id,
                        subject_hash=subject_hash,
                        plan=plan,
                        conformance=conformance,
                        binding=binding,
                        evaluation=evaluation,
                    )
                )

        if failure is not None:
            facts = []
        semantic_states = {item.semantic_state.value for item in facts}
        if failure is not None:
            state = "TECHNICAL_FAILURE"
        elif "FAIL" in semantic_states:
            state = "FAIL"
        elif "INDETERMINATE" in semantic_states or unresolved_binding_refs:
            state = "INDETERMINATE"
        else:
            state = "PASS"
        positive_proofs = (
            tuple(
                sorted(
                    (
                        lowering_ref,
                        plan_ref,
                        discovery_ref,
                        conformance_ref,
                        *binding_refs,
                    ),
                    key=lambda item: (item.validation_run_id, item.event_id),
                )
            )
            if state == "PASS"
            else ()
        )
        indeterminate_proofs = (
            tuple(
                sorted(
                    (
                        lowering_ref,
                        plan_ref,
                        discovery_ref,
                        conformance_ref,
                        *unresolved_binding_refs,
                    ),
                    key=lambda item: (item.validation_run_id, item.event_id),
                )
            )
            if state == "INDETERMINATE" and unresolved_binding_refs
            else ()
        )
        all_refs = tuple(
            (
                lowering_ref,
                plan_ref,
                discovery_ref,
                conformance_ref,
                *binding_refs,
                *executability_refs,
                *evaluation_refs,
            )
        )
        return {
            "lowering_ref": lowering_ref,
            "plan_ref": plan_ref,
            "discovery_ref": discovery_ref,
            "conformance_ref": conformance_ref,
            "binding_refs": tuple(binding_refs),
            "executability_refs": tuple(executability_refs),
            "evaluation_refs": tuple(evaluation_refs),
            "facts": tuple(facts),
            "positive_proofs": positive_proofs,
            "indeterminate_proofs": indeterminate_proofs,
            "state": state,
            "failure": failure,
            "semantic_input_ref": semantic_input_ref,
            "intermediate_refs": all_refs,
        }

    def complete_item(self, *, validation_run_id, subject_hash, artifact_ref, scope, target_ref, quality_contract_ref, source_sections, assembly_input_manifest_ref=None):
        pw = self.post_write
        snapshot, claim_targets, assertion_material, claim_by_id, section_by_claim = self._source_material(
            artifact_ref=artifact_ref,
            subject_hash=subject_hash,
            scope=scope,
            source_sections=source_sections,
            assembly_input_manifest_ref=assembly_input_manifest_ref,
            validation_run_id=validation_run_id,
        )
        source_snapshot_hash = canonical_sha256(snapshot.model_dump(mode="json"))
        assertion_materializer = ConsistencyAssertionMaterializer(pw.validation_intermediate_runtime)
        assertions = assertion_materializer.materialize(
            artifact_state_ref=artifact_ref,
            validation_subject_hash=subject_hash,
            assertions=assertion_material,
        )
        assertion_by_ref = {item.assertion_ref: item for item in assertions}
        source_target_by_assertion = {
            assertion.assertion_ref: assertion.source_unit_ref for assertion in assertions
        }
        assertion_context = {
            assertion.assertion_ref: {
                "claim_target_id": assertion.source_unit_ref,
                "section_id": section_by_claim[assertion.source_unit_ref],
                "claim_type": claim_by_id[assertion.source_unit_ref].claim_type,
            }
            for assertion in assertions
        }
        small = len(assertions) <= self.profile.small_n_binary_member_limit
        discovery_mode = "SMALL_N_EXHAUSTIVE_MEMBERS" if small else "LARGE_N_HIGH_RECALL"
        assertion_sections = {ref: value["section_id"] for ref, value in assertion_context.items()}
        required_pairs = ConsistencyDiscoveryPlanner.structural_members(
            assertion_refs=tuple(assertion_by_ref), validation_scope=scope.value,
            assertion_sections=assertion_sections, exhaustive=small,
        )
        material = {
            "contract": CONSISTENCY_RELATION_CONTRACT,
            "policy_id": CONSISTENCY_POLICY_ID,
            "policy_version": CONSISTENCY_POLICY_VERSION,
            "validation_run_id": validation_run_id,
            "subject_hash": subject_hash,
            "validation_scope": scope.value,
            "target_ref": target_ref,
            "artifact_state_ref": artifact_ref.model_dump(mode="json"),
            "source_snapshot_hash": source_snapshot_hash,
            "assertions": [item.model_dump(mode="json") for item in assertions],
            "assertion_context": assertion_context,
            "all_assertion_refs": [item.assertion_ref for item in assertions],
            "discovery_mode": discovery_mode,
            "required_relation_member_sets": [list(item) for item in required_pairs],
            "semantic_candidate_budget": self.profile.composite_candidate_budget,
            "profile": self.profile.model_dump(mode="json"),
            "instruction_profile": _instruction_profile(),
        }
        item_ref = "consistency:item:" + artifact_ref.artifact_hash
        plan_stage = self.stage_plan()
        provenance = ("post-write-claim-source://sha256/" + source_snapshot_hash,)
        envelope_builder = ValidatorInputEnvelopeBuilder(validation_view_resolver=pw.validation_view_resolver)
        envelope_kwargs = dict(
            validation_run_id=validation_run_id,
            subject_hash=subject_hash,
            artifact_state_ref=artifact_ref,
            stage_plan=plan_stage,
            applicable_expected_validation_item_refs=(item_ref,),
            exact_authority_refs=provenance,
            execution_contract_version="validator-execution-v1",
        )
        envelope = envelope_builder.build(
            **envelope_kwargs,
            execution_config_refs=(material["instruction_profile"]["config_ref"],),
        )
        execution_ref = "validator-execution:consistency-mainline:" + canonical_sha256({"input_hash": envelope.input_hash, "item_ref": item_ref})
        record = pw.validation_store.find_validator_execution_for_item(validation_run_id=validation_run_id, validator_id=self.VALIDATOR_ID, expected_item_ref=item_ref)
        identity, lineage = self._identity(source_sections)
        if record is not None:
            legacy_material = self._legacy_material(material)
            legacy_envelope = envelope_builder.build(**envelope_kwargs)
            legacy_execution_ref = "validator-execution:consistency-mainline:" + canonical_sha256({
                "input_hash": legacy_envelope.input_hash, "item_ref": item_ref,
            })
            if record.validator_version != self.VERSION or len(record.intermediate_refs) < 4:
                raise ConsistencyCompositionError("committed Consistency execution identity/version differs")
            if record.validator_execution_ref == execution_ref and record.input_hash == envelope.input_hash:
                replay_material = material
            elif record.validator_execution_ref == legacy_execution_ref and record.input_hash == legacy_envelope.input_hash:
                replay_material = legacy_material
            else:
                for previous_profile in _previous_instruction_profiles():
                    previous_envelope = envelope_builder.build(**envelope_kwargs, execution_config_refs=(previous_profile["config_ref"],))
                    previous_execution = "validator-execution:consistency-mainline:" + canonical_sha256({"input_hash": previous_envelope.input_hash, "item_ref": item_ref})
                    if record.validator_execution_ref == previous_execution and record.input_hash == previous_envelope.input_hash:
                        replay_material = {**material, "instruction_profile": previous_profile}
                        break
                else:
                    raise ConsistencyCompositionError("committed Consistency execution identity/version differs")
            accounting = pw.validation_intermediate_runtime.resolve_typed(record.intermediate_refs[-1], ConsistencyMainlineAccounting)
            self._verify_accounting(
                accounting,
                replay_material,
                identity,
                quality_contract_ref=quality_contract_ref,
            )
            refs = tuple(ValidationFactRefSchema.model_validate(item) for item in accounting.fact_refs)
            proofs = tuple(ValidationIntermediateRefSchema.model_validate(item) for item in accounting.positive_proof_refs)
            indeterminate_proofs = tuple(ValidationIntermediateRefSchema.model_validate(item) for item in accounting.indeterminate_proof_refs)
            execution_accounting = record.expected_item_accounting[0]
            if refs != record.produced_fact_refs or execution_accounting.fact_refs != refs:
                raise ConsistencyCompositionError("committed Consistency Fact accounting differs")
            if execution_accounting.positive_proof_refs != proofs:
                raise ConsistencyCompositionError("committed Consistency positive proof accounting differs")
            if execution_accounting.indeterminate_proof_refs != indeterminate_proofs:
                raise ConsistencyCompositionError("committed Consistency indeterminate proof accounting differs")
            if execution_accounting.technical_failure_ref != accounting.failure:
                raise ConsistencyCompositionError("committed Consistency failure accounting differs")
            return ValidationCompletionItemSchema(
                expected_validation_item_ref=item_ref,
                required=True,
                state=accounting.state,
                fact_refs=refs,
                positive_proof_refs=proofs if accounting.state == "PASS" else (),
                indeterminate_proof_refs=indeterminate_proofs,
                technical_failure_ref=accounting.failure,
            )

        run = pw.validation_store.resolve_run_exact(validation_run_id)
        if run.execution_state is ValidationRunState.COMPLETED:
            raise ConsistencyCompositionError("terminal ValidationRun is missing committed Consistency execution")

        def validate_current():
            current, *_ = self._source_material(
                artifact_ref=artifact_ref,
                subject_hash=subject_hash,
                scope=scope,
                source_sections=source_sections,
                assembly_input_manifest_ref=assembly_input_manifest_ref,
                validation_run_id=validation_run_id,
            )
            if canonical_sha256(current.model_dump(mode="json")) != source_snapshot_hash:
                raise ConsistencyCompositionError("Consistency canonical Claim sources changed from frozen input")

        assertion_refs = tuple(assertion_materializer.commit(validation_run_id=validation_run_id, assertion=item) for item in assertions)
        document_global_prepared = None
        shared_global_result = None
        if scope.value == "DOCUMENT":
            document_global_prepared = self._prepare_document_global(
                validation_run_id=validation_run_id,
                subject_hash=subject_hash,
                artifact_ref=artifact_ref,
                quality_contract_ref=quality_contract_ref,
                assertions=assertions,
                assertion_sections=assertion_sections,
                source_snapshot_hash=source_snapshot_hash,
                material=material,
            )

        rows = ()
        semantic_input_ref = None
        failure = None
        global_material = (
            document_global_prepared["global_material"]
            if document_global_prepared is not None
            else None
        )
        if required_pairs and global_material is not None and len(assertions) >= 2:
            shared_rows, semantic_input_ref, failure = self._evaluate_shared(
                (material, global_material),
                identity,
                lineage,
                validate_current,
            )
            cross_ref = self._logical_work_ref(material)
            global_ref = self._logical_work_ref(global_material)
            rows = tuple(shared_rows.get(cross_ref, ())) if shared_rows is not None else ()
            shared_global_result = (
                tuple(shared_rows.get(global_ref, ())) if shared_rows is not None else (),
                semantic_input_ref,
                failure,
            )
        elif required_pairs:
            rows, semantic_input_ref, failure = self._evaluate(material, identity, lineage, validate_current)
        if discovery_mode == "LARGE_N_HIGH_RECALL" and failure is None:
            required_pairs = tuple(sorted({row.member_assertion_refs for row in rows}))
        planner = ConsistencyDiscoveryPlanner(profile=self.profile, intermediate_runtime=pw.validation_intermediate_runtime)
        plan = planner.plan(
            validation_run_id=validation_run_id,
            artifact_state_ref=artifact_ref,
            validation_subject_hash=subject_hash,
            assertions=assertions,
            required_relation_members=required_pairs,
            validation_scope=scope.value,
            assertion_sections=assertion_sections,
        )
        plan_ref = planner.commit(plan)
        candidate_relations = {
            ConsistencyDiscoveryPlanner.relation_key(row.member_assertion_refs): row.member_assertion_refs
            for row in (rows or ())
        }
        discovery_executor = ConsistencyCandidateDiscoveryExecutor(pw.validation_intermediate_runtime)
        discovery = discovery_executor.execute(
            plan=plan,
            candidate_relations=candidate_relations,
            complete_required_plan_accounting=failure is None,
            discovery_profile_version=self.profile.profile_version,
            provenance_refs=provenance + ((semantic_input_ref,) if semantic_input_ref else ()),
        )
        discovery_ref = discovery_executor.commit(validation_run_id=validation_run_id, result=discovery)
        conformance_guard = ConsistencyPlanConformanceGuard(pw.validation_intermediate_runtime)
        conformance = conformance_guard.evaluate(
            plan=plan, discovery=discovery, profile=self.profile,
            validation_scope=scope.value, assertion_sections=assertion_sections,
            required_relation_members=required_pairs,
        )
        conformance_ref = conformance_guard.commit(validation_run_id=validation_run_id, result=conformance)

        binding_refs = []
        unresolved_binding_refs = []
        executability_refs = []
        evaluation_refs = []
        facts = []
        if failure is None and conformance.state is not ConsistencyPlanConformanceState.CONFORMING:
            failure = self._local_failure("CONSISTENCY_PLAN_NONCONFORMING", conformance_ref, conformance)
        if failure is None:
            for row in rows:
                members = row.member_assertion_refs
                formal_candidate = self._formal_candidate(row, assertion_by_ref)
                semantic = SemanticConsistencyEvaluator(
                    semantic_evaluator=lambda _assertions, row=row, formal_candidate=formal_candidate: {
                        "relation_materiality": row.relation_materiality,
                        "semantic_relation_description": row.semantic_relation_description,
                        "role_bindings": row.role_bindings,
                        "scope_condition_bindings": row.scope_condition_bindings,
                        "formal_relation_candidate": formal_candidate,
                        "reason_codes": row.reason_codes,
                        "ambiguity_codes": row.ambiguity_codes,
                        "physical_model_call_ref": semantic_input_ref,
                    },
                    intermediate_runtime=pw.validation_intermediate_runtime,
                )
                relation_assertions = tuple(assertion_by_ref[ref] for ref in members)
                binding = semantic.bind(
                    validation_run_id=validation_run_id,
                    validation_scope=scope.value,
                    scope_subject_ref=target_ref,
                    assertions=relation_assertions,
                    source_context_refs=tuple(sorted("section:" + section_by_claim[source_target_by_assertion[ref]] for ref in members)),
                    logical_consumer_binding_refs=(
                        self._logical_consumer_ref(
                            validation_scope=scope.value,
                            scope_subject_ref=target_ref,
                            members=members,
                        ),
                    ),
                    provenance_refs=provenance + ((semantic_input_ref,) if semantic_input_ref else ()),
                )
                binding_ref = semantic.commit_binding(binding)
                binding_refs.append(binding_ref)
                if binding.relation_materiality is ConsistencyRelationMateriality.NO_MATERIAL_RELATION:
                    continue
                if binding.relation_materiality is ConsistencyRelationMateriality.INDETERMINATE:
                    unresolved_binding_refs.append(binding_ref)
                    continue
                formal_guard = FormalExecutabilityGuard(pw.validation_intermediate_runtime)
                executability = formal_guard.evaluate(binding=binding, assertions_by_ref=assertion_by_ref)
                executability_ref = formal_guard.commit(validation_run_id=validation_run_id, result=executability)
                executability_refs.append(executability_ref)
                if executability.state is FormalExecutabilityState.TECHNICAL_FAILURE:
                    failure = self._local_failure("FORMAL_EXECUTABILITY_TECHNICAL_FAILURE", executability_ref, executability)
                    break
                if executability.state is FormalExecutabilityState.EXECUTABLE:
                    exact = ExactConsistencyEvaluator(pw.validation_intermediate_runtime)
                    evaluation = exact.evaluate(binding=binding, executability=executability, assertions_by_ref=assertion_by_ref)
                    evaluation_ref = exact.commit(validation_run_id=validation_run_id, result=evaluation)
                else:
                    evaluation = semantic.evaluate(binding=binding, relation=ArtifactConsistencyRelation(row.relation), observation_codes=row.reason_codes)
                    evaluation_ref = semantic.commit_evaluation(validation_run_id=validation_run_id, result=evaluation)
                evaluation_refs.append(evaluation_ref)
                facts.append(ConsistencyProjector.project(validation_run_id=validation_run_id, subject_hash=subject_hash, plan=plan, conformance=conformance, binding=binding, evaluation=evaluation))

        document_global = None
        if scope.value == "DOCUMENT":
            document_global = self._execute_document_global(
                validation_run_id=validation_run_id,
                subject_hash=subject_hash,
                artifact_ref=artifact_ref,
                target_ref=target_ref,
                quality_contract_ref=quality_contract_ref,
                assertions=assertions,
                assertion_by_ref=assertion_by_ref,
                assertion_sections=assertion_sections,
                section_by_claim=section_by_claim,
                source_target_by_assertion=source_target_by_assertion,
                source_snapshot_hash=source_snapshot_hash,
                material=material,
                provenance=provenance,
                identity=identity,
                lineage=lineage,
                validate_current=validate_current,
                prepared=document_global_prepared,
                semantic_result=shared_global_result,
            )
            if document_global["failure"] is not None:
                failure = document_global["failure"]
            else:
                facts.extend(document_global["facts"])

        if failure is not None:
            facts = []
        fact_ref_by_id = {}
        persisted_fact_refs = []
        for item in facts:
            ref = pw.validation_store.put_fact_if_absent_or_same(item)
            fact_ref_by_id[item.fact_id] = ref
            persisted_fact_refs.append(ref)
        fact_refs = tuple(persisted_fact_refs)
        semantic_states = {item.semantic_state.value for item in facts}
        global_state = document_global["state"] if document_global is not None else None
        if failure is not None:
            state = "TECHNICAL_FAILURE"
        elif "FAIL" in semantic_states:
            state = "FAIL"
        elif (
            "INDETERMINATE" in semantic_states
            or unresolved_binding_refs
            or global_state == "INDETERMINATE"
        ):
            state = "INDETERMINATE"
        else:
            state = "PASS"

        cross_positive = tuple(
            sorted(
                (plan_ref, discovery_ref, conformance_ref, *binding_refs),
                key=lambda item: (item.validation_run_id, item.event_id),
            )
        )
        global_positive = (
            document_global["positive_proofs"]
            if document_global is not None
            else ()
        )
        positive_proofs = (
            tuple(
                sorted(
                    set((*cross_positive, *global_positive)),
                    key=lambda item: (item.validation_run_id, item.event_id),
                )
            )
            if state == "PASS"
            else ()
        )
        cross_indeterminate = tuple(
            sorted(
                (plan_ref, discovery_ref, conformance_ref, *unresolved_binding_refs),
                key=lambda item: (item.validation_run_id, item.event_id),
            )
        ) if unresolved_binding_refs else ()
        global_indeterminate = (
            document_global["indeterminate_proofs"]
            if document_global is not None
            else ()
        )
        indeterminate_proofs = (
            tuple(
                sorted(
                    set((*cross_indeterminate, *global_indeterminate)),
                    key=lambda item: (item.validation_run_id, item.event_id),
                )
            )
            if state == "INDETERMINATE"
            else ()
        )

        document_global_accounting = None
        if document_global is not None:
            global_fact_refs = tuple(
                fact_ref_by_id[item.fact_id]
                for item in document_global["facts"]
                if item.fact_id in fact_ref_by_id
            )
            document_global_accounting = ConsistencyGlobalProcedureAccounting(
                lowering_ref=document_global["lowering_ref"].model_dump(mode="json"),
                plan_ref=(
                    document_global["plan_ref"].model_dump(mode="json")
                    if document_global["plan_ref"] is not None
                    else None
                ),
                discovery_ref=(
                    document_global["discovery_ref"].model_dump(mode="json")
                    if document_global["discovery_ref"] is not None
                    else None
                ),
                conformance_ref=(
                    document_global["conformance_ref"].model_dump(mode="json")
                    if document_global["conformance_ref"] is not None
                    else None
                ),
                binding_refs=tuple(
                    item.model_dump(mode="json")
                    for item in document_global["binding_refs"]
                ),
                executability_refs=tuple(
                    item.model_dump(mode="json")
                    for item in document_global["executability_refs"]
                ),
                evaluation_refs=tuple(
                    item.model_dump(mode="json")
                    for item in document_global["evaluation_refs"]
                ),
                fact_refs=tuple(
                    item.model_dump(mode="json") for item in global_fact_refs
                ),
                positive_proof_refs=tuple(
                    item.model_dump(mode="json")
                    for item in document_global["positive_proofs"]
                ),
                indeterminate_proof_refs=tuple(
                    item.model_dump(mode="json")
                    for item in document_global["indeterminate_proofs"]
                ),
                state=document_global["state"],
                failure=document_global["failure"],
                semantic_input_receipt_ref=document_global["semantic_input_ref"],
            )

        accounting = ConsistencyMainlineAccounting(
            input_hash=canonical_sha256(material),
            artifact_state_ref=artifact_ref.model_dump(mode="json"),
            subject_hash=subject_hash,
            source_snapshot_hash=source_snapshot_hash,
            assertion_refs=tuple(item.model_dump(mode="json") for item in assertion_refs),
            plan_ref=plan_ref.model_dump(mode="json"),
            discovery_ref=discovery_ref.model_dump(mode="json"),
            conformance_ref=conformance_ref.model_dump(mode="json"),
            binding_refs=tuple(item.model_dump(mode="json") for item in binding_refs),
            executability_refs=tuple(item.model_dump(mode="json") for item in executability_refs),
            evaluation_refs=tuple(item.model_dump(mode="json") for item in evaluation_refs),
            fact_refs=tuple(item.model_dump(mode="json") for item in fact_refs),
            positive_proof_refs=tuple(item.model_dump(mode="json") for item in positive_proofs),
            indeterminate_proof_refs=tuple(item.model_dump(mode="json") for item in indeterminate_proofs),
            state=state,
            failure=failure,
            semantic_input_receipt_ref=semantic_input_ref,
            document_global=document_global_accounting,
        )
        intermediate = pw.validation_intermediate_runtime.commit(
            validation_run_id=validation_run_id,
            producer_ref=self.VALIDATOR_ID,
            target_ref=item_ref,
            typed_intermediate=accounting,
            intermediate_kind=ValidationIntermediateKind.TARGET_ACCOUNTING,
            occurred_at_epoch=0.0,
        )
        global_intermediate_refs = (
            document_global["intermediate_refs"] if document_global is not None else ()
        )
        all_intermediate_refs = tuple(
            (
                *assertion_refs,
                plan_ref,
                discovery_ref,
                conformance_ref,
                *binding_refs,
                *executability_refs,
                *evaluation_refs,
                *global_intermediate_refs,
                intermediate,
            )
        )
        with pw.database.read_transaction() as connection:
            sequence = pw.validation_store._next_event_sequence(connection, validation_run_id)
        event = pw.validation_store._event(
            validation_run_id=validation_run_id,
            event_id="validator-terminal:consistency-mainline:" + canonical_sha256(execution_ref),
            sequence=sequence,
            event_kind="VALIDATOR_TECHNICAL_FAILURE" if failure else "VALIDATOR_COMPLETED",
            payload={"validator_execution_ref": execution_ref},
            occurred_at_epoch=0.0,
        )
        expected_accounting = ExpectedItemExecutionAccountingSchema(
            expected_validation_item_ref=item_ref,
            execution_state=ExpectedItemExecutionState.TECHNICAL_FAILURE if failure else ExpectedItemExecutionState.COMPLETED,
            fact_refs=fact_refs,
            positive_proof_refs=positive_proofs,
            indeterminate_proof_refs=indeterminate_proofs,
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
            "stage_plan_ref": plan_stage.stage_plan_ref,
            "stage_plan_version": plan_stage.stage_plan_version,
            "input_hash": envelope.input_hash,
            "terminal_event_id": event.event_id,
            "terminal_execution_state": "TECHNICAL_FAILURE" if failure else "COMPLETED",
            "expected_item_accounting": [expected_accounting.model_dump(mode="json")],
            "produced_fact_refs": [item.model_dump(mode="json") for item in fact_refs],
            "intermediate_refs": [item.model_dump(mode="json") for item in all_intermediate_refs],
        }
        pw.validation_store.finalize_validator_execution(
            event,
            ValidatorExecutionRecordSchema(**base, record_sha256=canonical_sha256(base)),
            stage_plan=plan_stage,
            input_envelope=envelope,
        )
        self._verify_accounting(
            accounting,
            material,
            identity,
            quality_contract_ref=quality_contract_ref,
        )
        return ValidationCompletionItemSchema(
            expected_validation_item_ref=item_ref,
            required=True,
            state=state,
            fact_refs=fact_refs,
            positive_proof_refs=positive_proofs,
            indeterminate_proof_refs=indeterminate_proofs,
            technical_failure_ref=failure,
        )

    @staticmethod
    def _local_failure(kind, source_ref, result):
        formal = isinstance(result, FormalExecutabilityResultSchema)
        return {
            "schema_version": "artifact_consistency_failure_v1",
            "kind": kind,
            "error_type": "FormalExecutabilityError" if formal else "ConsistencyPlanConformanceError",
            "error_code": result.reason_codes[0] if formal and result.reason_codes else kind,
            "message": "formal Consistency operands cannot be resolved" if formal else "Consistency plan is not conforming",
            "category": FailureCategory.VALIDATION.value,
            "origin": FailureOrigin.POST_WRITE.value,
            "source_ref": source_ref.model_dump(mode="json"),
            "source_result_ref": result.result_ref,
            **({"binding_ref": result.binding_ref, "reason_codes": list(result.reason_codes)} if formal else {}),
        }

    def _verify_procedure_failure(self, exact, *, material, identity, conformance):
        """Read physical outcome and local owner evidence independently."""
        runtime = self.post_write.validation_intermediate_runtime
        rows, physical_failure = (), None
        if exact.semantic_input_receipt_ref is not None:
            rows, physical_failure = self._verify_semantic_receipt(
                exact.semantic_input_receipt_ref, material=material, identity=identity,
            )
        elif material["required_relation_member_sets"] or (
            material["validation_scope"] == "DOCUMENT_GLOBAL" and len(material["assertions"]) >= 2
        ):
            raise ConsistencyCompositionError("Consistency procedure lacks its physical receipt")
        if physical_failure is not None:
            if exact.binding_refs or exact.executability_refs or exact.evaluation_refs:
                raise ConsistencyCompositionError("failed physical Consistency call carries local evaluation")
            return physical_failure

        global_scope = material["validation_scope"] == "DOCUMENT_GLOBAL"
        if conformance.state is not ConsistencyPlanConformanceState.CONFORMING:
            if exact.binding_refs or exact.executability_refs or exact.evaluation_refs:
                raise ConsistencyCompositionError("nonconforming Consistency plan carries evaluation")
            return self._local_failure(
                "CONSISTENCY_GLOBAL_PLAN_NONCONFORMING" if global_scope else "CONSISTENCY_PLAN_NONCONFORMING",
                ValidationIntermediateRefSchema.model_validate(exact.conformance_ref), conformance,
            )

        assertions = {item["assertion_ref"]: ConsistencyAssertionSchema.model_validate(item)
                      for item in material["assertions"]}
        expected_bindings = []
        for row in rows:
            candidate = self._formal_candidate(row, assertions)
            semantic = SemanticConsistencyEvaluator(semantic_evaluator=lambda _items, row=row, candidate=candidate: {
                "relation_materiality": row.relation_materiality,
                "semantic_relation_description": row.semantic_relation_description,
                "role_bindings": row.role_bindings, "scope_condition_bindings": row.scope_condition_bindings,
                "formal_relation_candidate": candidate, "reason_codes": row.reason_codes,
                "ambiguity_codes": row.ambiguity_codes, "physical_model_call_ref": exact.semantic_input_receipt_ref,
            })
            expected_bindings.append(semantic.bind(
                validation_run_id=material["validation_run_id"], validation_scope=material["validation_scope"],
                scope_subject_ref=material["target_ref"],
                assertions=tuple(assertions[ref] for ref in row.member_assertion_refs),
                source_context_refs=tuple("section:" + material["assertion_context"][ref]["section_id"]
                                          for ref in row.member_assertion_refs),
                logical_consumer_binding_refs=(self._logical_consumer_ref(
                    validation_scope=material["validation_scope"], scope_subject_ref=material["target_ref"],
                    members=row.member_assertion_refs),),
                provenance_refs=("post-write-claim-source://sha256/" + material["source_snapshot_hash"],
                                 exact.semantic_input_receipt_ref),
            ))
        bindings = []
        for raw in exact.binding_refs:
            ref = ValidationIntermediateRefSchema.model_validate(raw)
            if ref.validation_run_id != material["validation_run_id"]:
                raise ConsistencyCompositionError("Consistency binding belongs to another run")
            bindings.append(runtime.resolve_typed(ref, SemanticRelationBindingSchema))
        if bindings != expected_bindings[:len(bindings)]:
            raise ConsistencyCompositionError("Consistency binding differs from physical candidate")
        material_bindings = [item for item in bindings
                             if item.relation_materiality is ConsistencyRelationMateriality.MATERIAL_RELATION]
        if len(exact.executability_refs) != len(material_bindings):
            raise ConsistencyCompositionError("Consistency Guard manifest differs from material bindings")
        local_failure = None
        evaluated_bindings = []
        for raw, binding in zip(exact.executability_refs, material_bindings):
            ref = ValidationIntermediateRefSchema.model_validate(raw)
            guard = runtime.resolve_typed(ref, FormalExecutabilityResultSchema)
            candidate_ref = (binding.formal_relation_candidate.formal_candidate_ref
                             if binding.formal_relation_candidate else None)
            if (ref.validation_run_id != material["validation_run_id"] or guard.binding_ref != binding.binding_ref
                    or guard.formal_candidate_ref != candidate_ref or local_failure is not None):
                raise ConsistencyCompositionError("Consistency Guard binding/candidate differs")
            if guard.state is FormalExecutabilityState.TECHNICAL_FAILURE:
                if not guard.reason_codes or binding != bindings[-1]:
                    raise ConsistencyCompositionError("Consistency failed Guard lacks exact stopping evidence")
                local_failure = self._local_failure(
                    "GLOBAL_FORMAL_EXECUTABILITY_TECHNICAL_FAILURE" if global_scope else "FORMAL_EXECUTABILITY_TECHNICAL_FAILURE",
                    ref, guard,
                )
            else:
                evaluated_bindings.append((binding, guard))
        if len(exact.evaluation_refs) != len(evaluated_bindings):
            raise ConsistencyCompositionError("Consistency evaluation manifest differs from completed Guards")
        for raw, (binding, guard) in zip(exact.evaluation_refs, evaluated_bindings):
            ref = ValidationIntermediateRefSchema.model_validate(raw)
            evaluation = runtime.resolve_typed(ref, ExactConsistencyEvaluationResultSchema
                if guard.state is FormalExecutabilityState.EXECUTABLE else SemanticConsistencyEvaluationResultSchema)
            if (ref.validation_run_id != material["validation_run_id"] or evaluation.binding_ref != binding.binding_ref
                    or (guard.state is FormalExecutabilityState.EXECUTABLE
                        and evaluation.formal_candidate_ref != guard.formal_candidate_ref)):
                raise ConsistencyCompositionError("Consistency evaluation binding differs")
        if local_failure is None and bindings != expected_bindings:
            raise ConsistencyCompositionError("Consistency binding manifest omitted physical candidate")
        return local_failure

    def _verify_semantic_receipt(
        self,
        receipt_ref,
        *,
        material,
        identity,
    ):
        """Verify and return the physical outcome, independent of local failures."""
        receipt = self.store.get_json(receipt_ref, **identity)
        result = self.store.get_json(
            receipt_ref.removesuffix("/input") + "/result", **identity
        )
        receipt_material = receipt.get("input_material")
        feedback = receipt_material.get("protocol_correction_feedback") if isinstance(receipt_material, dict) else None
        if feedback is not None:
            parent_ref = feedback["parent_input_ref"]
            parent = self.store.get_json(parent_ref, **identity)
            parent_result = self.store.get_json(parent_ref.removesuffix("/input") + "/result", **identity)
            if self._verify_protocol_receipt(parent_ref, parent, parent_result, identity) != receipt_ref:
                raise ConsistencyCompositionError("Consistency correction is not the Parent-selected receipt")
        else:
            if self._verify_protocol_receipt(receipt_ref, receipt, result, identity) != receipt_ref:
                raise ConsistencyCompositionError("Consistency Parent did not select this receipt")
        if (
            receipt.get("input_hash") != canonical_sha256(receipt_material)
            or result.get("input_hash") != receipt.get("input_hash")
        ):
            raise ConsistencyCompositionError(
                "Consistency semantic receipt hash/binding differs"
            )
        if feedback is not None:
            receipt_material = {key: value for key, value in receipt_material.items() if key != "protocol_correction_feedback"}
        shared = receipt_material != material
        if shared:
            if not isinstance(receipt_material, dict) or receipt_material.get("contract") != CONSISTENCY_SHARED_RELATION_CONTRACT:
                raise ConsistencyCompositionError("Consistency semantic receipt does not contain exact logical work")
            matching = [item for item in receipt_material.get("logical_works", ()) if item.get("material") == material]
            if len(matching) != 1 or matching[0].get("logical_work_ref") != self._logical_work_ref(material):
                raise ConsistencyCompositionError("shared Consistency receipt logical work differs")
        if result.get("outcome") == "failed":
            failure = result.get("failure")
            if not isinstance(failure, dict) or not failure:
                raise ConsistencyCompositionError("Consistency failed physical receipt lacks failure")
            allowed_kind = ("CONSISTENCY_SHARED_TECHNICAL_FAILURE" if shared else "CONSISTENCY_TECHNICAL_FAILURE")
            if failure.get("kind") not in {allowed_kind, "CANONICAL_BUDGET_FAILURE"}:
                raise ConsistencyCompositionError("Consistency physical failure carries a local cause")
            return (), failure
        if result.get("outcome") != "completed" or result.get("failure") is not None:
            raise ConsistencyCompositionError("Consistency physical result is not completed")
        if shared:
            parsed = self._parse_shared_reply(result.get("candidate"), receipt_material)
            logical_ref = self._logical_work_ref(material)
            if logical_ref not in parsed:
                raise ConsistencyCompositionError("shared Consistency result omitted exact logical work")
            return tuple(parsed[logical_ref]), None
        return self._parse_reply(result.get("candidate"), material), None

    def _verify_document_global_accounting(
        self,
        exact_global: ConsistencyGlobalProcedureAccounting,
        *,
        material,
        identity,
        quality_contract_ref,
    ):
        pw = self.post_write
        runtime = pw.validation_intermediate_runtime
        assertions = tuple(
            ConsistencyAssertionSchema.model_validate(item)
            for item in material["assertions"]
        )
        assertion_sections = {
            ref: value["section_id"]
            for ref, value in material["assertion_context"].items()
        }

        def canonical_universe_reader(exact_artifact_ref, exact_subject_hash):
            if (
                exact_artifact_ref.model_dump(mode="json")
                != material["artifact_state_ref"]
                or exact_subject_hash != material["subject_hash"]
            ):
                raise ConsistencyCompositionError(
                    "replayed global Consistency universe differs from exact subject"
                )
            return assertions, dict(assertion_sections), material["source_snapshot_hash"]

        resolver = ConsistencyProcedureInputResolver(
            contract_store=pw.contract_store,
            requirement_store=pw.requirement_store,
            canonical_universe_reader=canonical_universe_reader,
            intermediate_runtime=runtime,
        )
        lowering_ref = ValidationIntermediateRefSchema.model_validate(
            exact_global.lowering_ref
        )
        lowering = resolver.replay(
            lowering_ref,
            contract_ref=quality_contract_ref,
            artifact_state_ref=(
                assertions[0].artifact_state_ref
                if assertions
                else ArtifactStateRefSchema.model_validate(
                    material["artifact_state_ref"]
                )
            ),
            validation_run_id=material["validation_run_id"],
            validation_subject_hash=material["subject_hash"],
        )
        if lowering.document_global_applicability == "UNRESOLVED":
            if (
                exact_global.state != "INDETERMINATE"
                or exact_global.failure is not None
                or exact_global.plan_ref is not None
                or exact_global.fact_refs
                or exact_global.positive_proof_refs
                or tuple(
                    ValidationIntermediateRefSchema.model_validate(item)
                    for item in exact_global.indeterminate_proof_refs
                )
                != (lowering_ref,)
            ):
                raise ConsistencyCompositionError(
                    "unresolved global applicability accounting differs"
                )
            return "INDETERMINATE", (), (lowering_ref,)
        if lowering.document_global_applicability == "NOT_APPLICABLE":
            if (
                exact_global.state != "NOT_APPLICABLE"
                or exact_global.failure is not None
                or exact_global.plan_ref is not None
                or exact_global.fact_refs
                or exact_global.positive_proof_refs
                or exact_global.indeterminate_proof_refs
            ):
                raise ConsistencyCompositionError(
                    "explicit global NOT_APPLICABLE accounting differs"
                )
            return "NOT_APPLICABLE", (), ()
        if lowering.document_global_applicability != "APPLICABLE":
            raise ConsistencyCompositionError(
                "replayed global applicability state is unsupported"
            )
        if len(lowering.global_work) != 1:
            raise ConsistencyCompositionError(
                "production global replay requires one exact logical selector"
            )

        if (
            exact_global.plan_ref is None
            or exact_global.discovery_ref is None
            or exact_global.conformance_ref is None
        ):
            raise ConsistencyCompositionError(
                "applicable global accounting lacks plan/discovery/conformance"
            )
        plan_ref = ValidationIntermediateRefSchema.model_validate(
            exact_global.plan_ref
        )
        discovery_ref = ValidationIntermediateRefSchema.model_validate(
            exact_global.discovery_ref
        )
        conformance_ref = ValidationIntermediateRefSchema.model_validate(
            exact_global.conformance_ref
        )
        plan = runtime.resolve_typed(plan_ref, ConsistencyDiscoveryPlanSchema)
        discovery = runtime.resolve_typed(
            discovery_ref, ConsistencyCandidateDiscoveryResultSchema
        )
        conformance = runtime.resolve_typed(
            conformance_ref, ConsistencyPlanConformanceResultSchema
        )
        expected_conformance = ConsistencyPlanConformanceGuard().evaluate(
            plan=plan,
            discovery=discovery,
            profile=self.profile,
            validation_scope="DOCUMENT_GLOBAL",
            assertion_sections=assertion_sections,
            required_relation_members=lowering.required_relation_members(),
            runtime_procedure_input=lowering,
        )
        if conformance != expected_conformance:
            raise ConsistencyCompositionError(
                "global plan/discovery/conformance replay differs"
            )
        if (
            exact_global.failure is None
            and conformance.state is not ConsistencyPlanConformanceState.CONFORMING
        ):
            raise ConsistencyCompositionError(
                "successful global Consistency lacks conforming procedure accounting"
            )

        binding_refs = tuple(
            ValidationIntermediateRefSchema.model_validate(item)
            for item in exact_global.binding_refs
        )
        bindings = tuple(
            runtime.resolve_typed(item, SemanticRelationBindingSchema)
            for item in binding_refs
        )
        if any(
            item.validation_scope != "DOCUMENT_GLOBAL"
            or item.validation_run_id != material["validation_run_id"]
            or item.artifact_state_ref.model_dump(mode="json")
            != material["artifact_state_ref"]
            or item.scope_subject_ref
            != lowering.global_work[0].selector.target_ref
            for item in bindings
        ):
            raise ConsistencyCompositionError(
                "global semantic binding scope/subject differs"
            )
        unresolved_refs = tuple(
            ref
            for ref, binding in zip(binding_refs, bindings)
            if binding.relation_materiality
            is ConsistencyRelationMateriality.INDETERMINATE
        )
        fact_refs = tuple(
            ValidationFactRefSchema.model_validate(item)
            for item in exact_global.fact_refs
        )
        facts = tuple(pw.validation_store.resolve_fact(item) for item in fact_refs)
        binding_ids = {item.binding_ref for item in bindings}
        evaluation_refs = {
            ValidationIntermediateRefSchema.model_validate(item).event_id
            for item in exact_global.evaluation_refs
        }
        if any(
            fact.family.value != "ARTIFACT_CONSISTENCY"
            or fact.payload.get("plan_ref") != plan.plan_ref
            or fact.payload.get("plan_conformance_result_ref")
            != conformance.result_ref
            or fact.payload.get("semantic_relation_binding_ref") not in binding_ids
            or not fact.payload.get("evaluation_ref")
            for fact in facts
        ):
            raise ConsistencyCompositionError(
                "global Consistency Fact binding differs"
            )
        # Ensure every recorded evaluation ref still resolves; the Fact payload
        # is already self-hashed and names the exact evaluation identity.
        for raw in exact_global.evaluation_refs:
            ref = ValidationIntermediateRefSchema.model_validate(raw)
            pw.validation_store.resolve_intermediate(ref)
        for raw in exact_global.executability_refs:
            ref = ValidationIntermediateRefSchema.model_validate(raw)
            pw.validation_store.resolve_intermediate(ref)

        if exact_global.failure is not None and fact_refs:
            raise ConsistencyCompositionError("global Consistency technical failure carries facts")
        states = {item.semantic_state.value for item in facts}
        expected_state = (
            "TECHNICAL_FAILURE"
            if exact_global.failure is not None
            else "FAIL"
            if "FAIL" in states
            else "INDETERMINATE"
            if "INDETERMINATE" in states or unresolved_refs
            else "PASS"
        )
        if exact_global.state != expected_state:
            raise ConsistencyCompositionError(
                "global completion state differs from exact facts/procedure"
            )
        positive = tuple(
            ValidationIntermediateRefSchema.model_validate(item)
            for item in exact_global.positive_proof_refs
        )
        indeterminate = tuple(
            ValidationIntermediateRefSchema.model_validate(item)
            for item in exact_global.indeterminate_proof_refs
        )
        if expected_state == "PASS":
            expected_positive = {
                lowering_ref,
                plan_ref,
                discovery_ref,
                conformance_ref,
                *binding_refs,
            }
            if set(positive) != expected_positive or indeterminate:
                raise ConsistencyCompositionError(
                    "global PASS proof set differs"
                )
            for proof in positive:
                pw.validation_store.resolve_intermediate(proof)
        elif expected_state == "INDETERMINATE" and unresolved_refs:
            expected_indeterminate = {
                lowering_ref,
                plan_ref,
                discovery_ref,
                conformance_ref,
                *unresolved_refs,
            }
            if set(indeterminate) != expected_indeterminate or positive:
                raise ConsistencyCompositionError(
                    "global INDETERMINATE proof set differs"
                )
            for proof in indeterminate:
                pw.validation_store.resolve_intermediate(proof)
        elif positive or indeterminate:
            raise ConsistencyCompositionError(
                "global proof set does not match terminal state"
            )

        if lowering.document_global_applicability == "APPLICABLE":
            expected_material = {
                **material,
                "validation_scope": "DOCUMENT_GLOBAL",
                "target_ref": lowering.global_work[0].selector.target_ref,
                "discovery_mode": "DOCUMENT_GLOBAL_BOUNDED_DISCOVERY",
                "required_relation_member_sets": [
                    list(item) for item in lowering.required_relation_members()
                ],
                "required_logical_obligation_refs": list(
                    lowering.required_logical_obligations()
                ),
                "runtime_procedure_input_hash": lowering.runtime_input_hash,
            }
            expected_failure = self._verify_procedure_failure(
                exact_global, material=expected_material, identity=identity, conformance=conformance,
            )
            if exact_global.failure != expected_failure:
                raise ConsistencyCompositionError("global Consistency failure source differs")
        return expected_state, positive, indeterminate

    def _verify_accounting(
        self,
        accounting,
        material,
        identity,
        *,
        quality_contract_ref,
    ):
        exact = ConsistencyMainlineAccounting.model_validate(accounting)
        if exact.input_hash != canonical_sha256(material) or exact.artifact_state_ref != material["artifact_state_ref"] or exact.subject_hash != material["subject_hash"] or exact.source_snapshot_hash != material["source_snapshot_hash"]:
            raise ConsistencyCompositionError("Consistency accounting input binding differs")
        runtime = self.post_write.validation_intermediate_runtime
        plan = runtime.resolve_typed(ValidationIntermediateRefSchema.model_validate(exact.plan_ref), ConsistencyDiscoveryPlanSchema)
        discovery = runtime.resolve_typed(ValidationIntermediateRefSchema.model_validate(exact.discovery_ref), ConsistencyCandidateDiscoveryResultSchema)
        conformance = runtime.resolve_typed(ValidationIntermediateRefSchema.model_validate(exact.conformance_ref), ConsistencyPlanConformanceResultSchema)
        if plan.validation_run_id != material["validation_run_id"] or plan.validation_subject_hash != material["subject_hash"] or discovery.plan_ref != plan.plan_ref or conformance.plan_ref != plan.plan_ref or conformance.discovery_ref != discovery.discovery_ref:
            raise ConsistencyCompositionError("Consistency plan/discovery/conformance chain differs")
        facts = [
            self.post_write.validation_store.resolve_fact(
                ValidationFactRefSchema.model_validate(raw)
            )
            for raw in exact.fact_refs
        ]
        states = {item.semantic_state.value for item in facts}
        unresolved = tuple(
            ValidationIntermediateRefSchema.model_validate(raw)
            for raw in exact.binding_refs
            if runtime.resolve_typed(
                ValidationIntermediateRefSchema.model_validate(raw),
                SemanticRelationBindingSchema,
            ).relation_materiality
            is ConsistencyRelationMateriality.INDETERMINATE
        )

        global_state = None
        global_positive = ()
        global_indeterminate = ()
        if material["validation_scope"] == "DOCUMENT":
            if exact.document_global is None:
                raise ConsistencyCompositionError(
                    "Document Consistency accounting lacks independent global procedure"
                )
            (
                global_state,
                global_positive,
                global_indeterminate,
            ) = self._verify_document_global_accounting(
                exact.document_global,
                material=material,
                identity=identity,
                quality_contract_ref=quality_contract_ref,
            )
            global_fact_ids = {
                ValidationFactRefSchema.model_validate(raw).fact_id
                for raw in exact.document_global.fact_refs
            }
            if not global_fact_ids <= {item.fact_id for item in facts}:
                raise ConsistencyCompositionError(
                    "global Consistency facts escape the validator fact manifest"
                )
            if (
                global_state == "TECHNICAL_FAILURE"
                and exact.failure != exact.document_global.failure
            ):
                raise ConsistencyCompositionError(
                    "global technical failure differs from overall Consistency failure"
                )
        elif exact.document_global is not None:
            raise ConsistencyCompositionError(
                "Section Consistency cannot carry Document-global accounting"
            )

        expected_state = (
            "TECHNICAL_FAILURE"
            if exact.failure is not None
            else "FAIL"
            if "FAIL" in states
            else "INDETERMINATE"
            if (
                "INDETERMINATE" in states
                or unresolved
                or global_state == "INDETERMINATE"
            )
            else "PASS"
        )
        if exact.state != expected_state:
            raise ConsistencyCompositionError(
                "Consistency completion state differs from exact Facts/procedure"
            )
        proofs = tuple(
            ValidationIntermediateRefSchema.model_validate(raw)
            for raw in exact.positive_proof_refs
        )
        cross_positive = {
            ValidationIntermediateRefSchema.model_validate(exact.plan_ref),
            ValidationIntermediateRefSchema.model_validate(exact.discovery_ref),
            ValidationIntermediateRefSchema.model_validate(exact.conformance_ref),
            *(
                ValidationIntermediateRefSchema.model_validate(raw)
                for raw in exact.binding_refs
            ),
        }
        if exact.state == "PASS":
            expected_positive = set(cross_positive) | set(global_positive)
            if (
                set(proofs) != expected_positive
                or conformance.state
                is not ConsistencyPlanConformanceState.CONFORMING
            ):
                raise ConsistencyCompositionError(
                    "Consistency PASS lacks exact conforming procedural proofs"
                )
            for proof in proofs:
                self.post_write.validation_store.resolve_intermediate(proof)
        elif proofs:
            raise ConsistencyCompositionError(
                "non-PASS Consistency cannot carry positive proof"
            )
        indeterminate = tuple(
            ValidationIntermediateRefSchema.model_validate(raw)
            for raw in exact.indeterminate_proof_refs
        )
        cross_indeterminate = (
            {
                ValidationIntermediateRefSchema.model_validate(exact.plan_ref),
                ValidationIntermediateRefSchema.model_validate(exact.discovery_ref),
                ValidationIntermediateRefSchema.model_validate(exact.conformance_ref),
                *unresolved,
            }
            if unresolved
            else set()
        )
        expected_indeterminate = cross_indeterminate | set(global_indeterminate)
        if exact.state == "INDETERMINATE" and expected_indeterminate:
            if (
                set(indeterminate) != expected_indeterminate
                or conformance.state
                is not ConsistencyPlanConformanceState.CONFORMING
            ):
                raise ConsistencyCompositionError(
                    "Consistency uncertainty lacks exact conforming procedure proofs"
                )
            for proof in indeterminate:
                self.post_write.validation_store.resolve_intermediate(proof)
        elif indeterminate:
            raise ConsistencyCompositionError(
                "indeterminate proof does not match unresolved binding"
            )
        cross_failure = self._verify_procedure_failure(
            exact, material=material, identity=identity, conformance=conformance,
        )
        expected_failure = (exact.document_global.failure
                            if exact.document_global is not None and exact.document_global.failure is not None
                            else cross_failure)
        if exact.failure != expected_failure:
            raise ConsistencyCompositionError("Consistency failure source differs from physical/local evidence")
        if exact.failure is not None and (exact.fact_refs or exact.positive_proof_refs or exact.indeterminate_proof_refs):
            raise ConsistencyCompositionError("Consistency technical failure carries semantic facts/proofs")


__all__ = [
    "CONSISTENCY_RELATION_CONTRACT",
    "ConsistencyCompositionError",
    "TargetPostWriteConsistency",
]
