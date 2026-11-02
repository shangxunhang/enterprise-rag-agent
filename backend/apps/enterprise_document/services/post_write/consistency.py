"""Target Artifact Consistency validator pipeline."""

from __future__ import annotations

from itertools import combinations
from typing import Callable, Iterable, Mapping

from apps.enterprise_document.config.post_write import ConsistencyExecutionProfile
from apps.enterprise_document.schemas.post_write.foundation import (
    ArtifactStateRefSchema,
    ValidationFactFamily,
    ValidationFactSchema,
    ValidationIntermediateKind,
    ValidationIntermediateRefSchema,
    ValidationSemanticState,
)
from apps.enterprise_document.schemas.post_write.quality import (
    ArtifactConsistencyPayloadSchema,
    ArtifactConsistencyRelation,
    ConsistencyAssertionSchema,
    ConsistencyCandidateDiscoveryResultSchema,
    ConsistencyDiscoveryPlanSchema,
    ConsistencyPlanConformanceResultSchema,
    ConsistencyPlanConformanceState,
    ConsistencyRelationMateriality,
    ConsistencyRelationShape,
    ExactConsistencyEvaluationResultSchema,
    FormalExecutabilityResultSchema,
    FormalExecutabilityState,
    FormalRelationCandidateSchema,
    SemanticConsistencyEvaluationResultSchema,
    SemanticRelationBindingSchema,
)
from core.hashing import canonical_sha256

from .execution import ValidationIntermediateRuntime


class ConsistencyIntegrityError(ValueError):
    pass


def _canon(values: Iterable[str]) -> tuple[str, ...]:
    return tuple(sorted({str(value).strip() for value in values if str(value).strip()}))


def _hashed_model(schema_cls, *, ref_field: str, hash_field: str, prefix: str, **kwargs):
    placeholder = schema_cls.model_construct(
        **kwargs,
        **{ref_field: "pending", hash_field: "0" * 64},
    )
    payload = placeholder.model_dump(mode="json")
    payload.pop(ref_field, None)
    payload.pop(hash_field, None)
    digest = canonical_sha256(payload)
    return schema_cls(
        **kwargs,
        **{ref_field: prefix + digest, hash_field: digest},
    )


def _commit(
    runtime: ValidationIntermediateRuntime | None,
    *,
    validation_run_id: str,
    producer_ref: str,
    target_ref: str,
    value,
    kind: ValidationIntermediateKind,
) -> ValidationIntermediateRefSchema:
    if runtime is None:
        raise ConsistencyIntegrityError(
            "Consistency intermediate commit requires ValidationIntermediateRuntime"
        )
    return runtime.commit(
        validation_run_id=validation_run_id,
        producer_ref=producer_ref,
        target_ref=target_ref,
        typed_intermediate=value,
        intermediate_kind=kind,
    )


class ConsistencyAssertionMaterializer:
    PRODUCER_REF = "post-write-consistency-assertion-materializer:v1"

    def __init__(
        self,
        intermediate_runtime: ValidationIntermediateRuntime | None = None,
    ) -> None:
        self.intermediate_runtime = intermediate_runtime

    def materialize(
        self,
        *,
        artifact_state_ref: ArtifactStateRefSchema,
        validation_subject_hash: str,
        assertions: Iterable[Mapping],
    ) -> tuple[ConsistencyAssertionSchema, ...]:
        materialized = []
        for raw in assertions:
            item = dict(raw)
            kwargs = {
                "artifact_state_ref": artifact_state_ref,
                "validation_subject_hash": str(validation_subject_hash).strip(),
                "source_unit_ref": str(item["source_unit_ref"]).strip(),
                "canonical_text": str(item["canonical_text"]).strip(),
                "typed_value": item.get("typed_value"),
                "value_unit": (
                    str(item["value_unit"]).strip()
                    if item.get("value_unit") is not None
                    else None
                ),
            }
            materialized.append(
                _hashed_model(
                    ConsistencyAssertionSchema,
                    ref_field="assertion_ref",
                    hash_field="assertion_hash",
                    prefix="consistency-assertion://sha256/",
                    **kwargs,
                )
            )
        refs = [item.assertion_ref for item in materialized]
        if len(refs) != len(set(refs)):
            raise ConsistencyIntegrityError(
                "Consistency assertions collapse to duplicate exact identity"
            )
        return tuple(sorted(materialized, key=lambda item: item.assertion_ref))

    def commit(
        self,
        *,
        validation_run_id: str,
        assertion: ConsistencyAssertionSchema,
    ) -> ValidationIntermediateRefSchema:
        exact = ConsistencyAssertionSchema.model_validate(assertion)
        return _commit(
            self.intermediate_runtime,
            validation_run_id=validation_run_id,
            producer_ref=self.PRODUCER_REF,
            target_ref=exact.assertion_ref,
            value=exact,
            kind=ValidationIntermediateKind.TARGET_ACCOUNTING,
        )


class ConsistencyDiscoveryPlanner:
    PRODUCER_REF = "post-write-consistency-discovery-planner:v1"

    def __init__(
        self,
        *,
        profile: ConsistencyExecutionProfile,
        intermediate_runtime: ValidationIntermediateRuntime | None = None,
    ) -> None:
        self.profile = ConsistencyExecutionProfile.model_validate(profile)
        self.intermediate_runtime = intermediate_runtime

    @staticmethod
    def relation_key(member_refs: Iterable[str]) -> str:
        members = _canon(member_refs)
        if len(members) < 2:
            raise ConsistencyIntegrityError(
                "Consistency relation requires at least two members"
            )
        return "consistency-relation-key://sha256/" + canonical_sha256(
            {"member_assertion_refs": members}
        )

    @staticmethod
    def eligible(members, *, validation_scope, assertion_sections):
        refs = tuple(members)
        if len(refs) < 2 or len(set(refs)) != len(refs) or not set(refs) <= set(assertion_sections):
            return False
        sections = {assertion_sections[ref] for ref in refs}
        if validation_scope == "SECTION":
            return len(sections) == 1
        if validation_scope in {"DOCUMENT", "CROSS_SECTION"}:
            return len(sections) == len(refs) if len(refs) == 2 else len(sections) > 1
        if validation_scope == "DOCUMENT_GLOBAL":
            return True  # Only externally bound logical selector work enters this domain.
        return False

    @classmethod
    def structural_members(cls, *, assertion_refs, validation_scope, assertion_sections, exhaustive):
        """Exact eligible binary domain or linear seeds; never semantic comparability."""
        refs = tuple(sorted(assertion_refs))
        if set(refs) != set(assertion_sections) or any(not value for value in assertion_sections.values()):
            raise ConsistencyIntegrityError("Consistency assertion Section manifest differs")
        if validation_scope not in {"SECTION", "DOCUMENT", "CROSS_SECTION", "DOCUMENT_GLOBAL"}:
            raise ConsistencyIntegrityError("unsupported Consistency relation scope")
        if validation_scope == "SECTION" and len(set(assertion_sections.values())) > 1:
            raise ConsistencyIntegrityError("SECTION assertion domain crosses Sections")
        if validation_scope == "DOCUMENT_GLOBAL":
            return ()  # Never infer global obligations from cross-section/all-pairs work.
        if exhaustive:
            return tuple(pair for pair in combinations(refs, 2)
                         if cls.eligible(pair, validation_scope=validation_scope, assertion_sections=assertion_sections))
        if validation_scope == "SECTION":
            return tuple(zip(refs, refs[1:]))
        by_section = {}
        for ref in refs:
            by_section.setdefault(assertion_sections[ref], []).append(ref)
        sections = sorted(by_section)
        if len(sections) < 2:
            return ()
        # Every member participates in a cross-Section seed, without enumerating
        # the product of Section domains. Full-domain semantic expansion follows.
        seeds = set()
        first, second = by_section[sections[0]][0], by_section[sections[1]][0]
        for ref in refs:
            anchor = second if assertion_sections[ref] == sections[0] else first
            seeds.add(tuple(sorted((ref, anchor))))
        return tuple(sorted(seeds))

    def plan(
        self,
        *,
        validation_run_id: str,
        artifact_state_ref: ArtifactStateRefSchema,
        validation_subject_hash: str,
        assertions: Iterable[ConsistencyAssertionSchema],
        required_relation_members: Iterable[Iterable[str]] = (),
        validation_scope: str | None = None,
        assertion_sections: Mapping[str, str] | None = None,
        runtime_procedure_input=None,
    ) -> ConsistencyDiscoveryPlanSchema:
        items = tuple(
            sorted(
                (
                    ConsistencyAssertionSchema.model_validate(item)
                    for item in assertions
                ),
                key=lambda item: item.assertion_ref,
            )
        )
        if any(
            item.artifact_state_ref != artifact_state_ref
            or item.validation_subject_hash != validation_subject_hash
            for item in items
        ):
            raise ConsistencyIntegrityError(
                "Consistency plan assertions belong to another subject"
            )
        assertion_refs = tuple(item.assertion_ref for item in items)
        supplied = tuple(tuple(members) for members in required_relation_members)
        scoped = validation_scope is not None
        if validation_scope == "DOCUMENT_GLOBAL" and runtime_procedure_input is None:
            raise ConsistencyIntegrityError("DOCUMENT_GLOBAL requires exact runtime procedure input")
        if runtime_procedure_input is not None:
            from .consistency_procedure import ConsistencyProcedureLoweringSchema
            lowering = ConsistencyProcedureLoweringSchema.model_validate(runtime_procedure_input)
            if (validation_scope != "DOCUMENT_GLOBAL" or lowering.document_global_applicability != "APPLICABLE"
                    or lowering.validation_run_id != validation_run_id or lowering.artifact_state_ref != artifact_state_ref
                    or lowering.validation_subject_hash != validation_subject_hash or lowering.assertion_refs != assertion_refs
                    or lowering.assertion_sections != dict(assertion_sections or {})):
                raise ConsistencyIntegrityError("global plan differs from exact runtime lowering")
            supplied = tuple(sorted(set(supplied) | set(lowering.required_relation_members())))
            if not lowering.required_logical_obligations():
                raise ConsistencyIntegrityError("applicable global procedure lacks exact logical obligation")
            if any(len(members) > self.profile.composite_max_arity for members in supplied):
                raise ConsistencyIntegrityError("required global relation-member work exceeds bounded profile")
        if scoped:
            sections = dict(assertion_sections or {})
            structural = self.structural_members(
                assertion_refs=assertion_refs, validation_scope=validation_scope,
                assertion_sections=sections,
                exhaustive=len(items) <= self.profile.small_n_binary_member_limit,
            )
            if any(not self.eligible(members, validation_scope=validation_scope, assertion_sections=sections) for members in supplied):
                raise ConsistencyIntegrityError("Consistency required member escapes exact scope")
            supplied = tuple(sorted(set(structural) | set(supplied)))
        if len(items) <= self.profile.small_n_binary_member_limit:
            required_keys = tuple(
                sorted(
                    self.relation_key(pair)
                    for pair in (supplied if scoped else combinations(assertion_refs, 2))
                )
            )
            mode = "SMALL_N_EXHAUSTIVE_MEMBERS"
        else:
            required_keys = tuple(
                sorted(
                    {
                        self.relation_key(members)
                        for members in supplied
                    }
                )
            )
            if not required_keys and not scoped:
                raise ConsistencyIntegrityError(
                    "large-N Consistency plan requires exact force/bridge obligations"
                )
            mode = "LARGE_N_HIGH_RECALL"
        kwargs = {
            "validation_run_id": validation_run_id,
            "artifact_state_ref": artifact_state_ref,
            "validation_subject_hash": validation_subject_hash,
            "assertion_refs": assertion_refs,
            "required_relation_keys": required_keys,
            "discovery_mode": mode,
            "profile_id": self.profile.profile_id,
            "profile_version": self.profile.profile_version,
            "execution_profile_hash": self.profile.execution_profile_hash,
            "procedure_contract_version": (
                self.profile.procedure_contract_version
            ),
        }
        if scoped:
            kwargs["procedure_input_hash"] = canonical_sha256({
                "validation_scope": validation_scope, "assertion_sections": sections,
                "required_relation_members": supplied,
                "execution_profile_hash": self.profile.execution_profile_hash,
            })
        if runtime_procedure_input is not None:
            kwargs["runtime_procedure_input_hash"] = lowering.runtime_input_hash
            kwargs["required_logical_obligation_refs"] = lowering.required_logical_obligations()
        return _hashed_model(
            ConsistencyDiscoveryPlanSchema,
            ref_field="plan_ref",
            hash_field="plan_hash",
            prefix="consistency-discovery-plan://sha256/",
            **kwargs,
        )

    def commit(
        self,
        plan: ConsistencyDiscoveryPlanSchema,
    ) -> ValidationIntermediateRefSchema:
        exact = ConsistencyDiscoveryPlanSchema.model_validate(plan)
        return _commit(
            self.intermediate_runtime,
            validation_run_id=exact.validation_run_id,
            producer_ref=self.PRODUCER_REF,
            target_ref=exact.plan_ref,
            value=exact,
            kind=ValidationIntermediateKind.TARGET_ACCOUNTING,
        )


class ConsistencyCandidateDiscoveryExecutor:
    PRODUCER_REF = "post-write-consistency-candidate-discovery:v1"

    def __init__(
        self,
        intermediate_runtime: ValidationIntermediateRuntime | None = None,
    ) -> None:
        self.intermediate_runtime = intermediate_runtime

    def execute(
        self,
        *,
        plan: ConsistencyDiscoveryPlanSchema,
        candidate_relations: Mapping[str, Iterable[str]],
        complete_required_plan_accounting: bool,
        discovery_profile_version: str,
        provenance_refs: Iterable[str] = (),
        accounted_logical_obligation_refs: Iterable[str] = (),
    ) -> ConsistencyCandidateDiscoveryResultSchema:
        exact = ConsistencyDiscoveryPlanSchema.model_validate(plan)
        logical_refs = _canon(accounted_logical_obligation_refs)
        if not set(logical_refs) <= set(exact.required_logical_obligation_refs):
            raise ConsistencyIntegrityError("discovery accounts for a foreign logical obligation")
        known = set(exact.assertion_refs)
        normalized: dict[str, tuple[str, ...]] = {}
        for key, refs in candidate_relations.items():
            members = _canon(refs)
            if len(members) < 2 or not set(members) <= known:
                raise ConsistencyIntegrityError(
                    "Consistency candidate relation has invalid member domain"
                )
            canonical_key = ConsistencyDiscoveryPlanner.relation_key(members)
            if str(key) != canonical_key:
                raise ConsistencyIntegrityError(
                    "Consistency candidate relation key/member mismatch"
                )
            normalized[canonical_key] = members
        keys = tuple(sorted(normalized))
        members = tuple(normalized[key] for key in keys)
        kwargs = {
            "plan_ref": exact.plan_ref,
            "candidate_relation_keys": keys,
            "candidate_member_refs": members,
            "complete_required_plan_accounting": bool(
                complete_required_plan_accounting
            ),
            "discovery_profile_version": str(
                discovery_profile_version
            ).strip(),
            "provenance_refs": _canon(provenance_refs),
            **({"accounted_logical_obligation_refs": logical_refs} if logical_refs else {}),
        }
        return _hashed_model(
            ConsistencyCandidateDiscoveryResultSchema,
            ref_field="discovery_ref",
            hash_field="discovery_hash",
            prefix="consistency-discovery://sha256/",
            **kwargs,
        )

    def commit(
        self,
        *,
        validation_run_id: str,
        result: ConsistencyCandidateDiscoveryResultSchema,
    ) -> ValidationIntermediateRefSchema:
        exact = ConsistencyCandidateDiscoveryResultSchema.model_validate(result)
        return _commit(
            self.intermediate_runtime,
            validation_run_id=validation_run_id,
            producer_ref=self.PRODUCER_REF,
            target_ref=exact.discovery_ref,
            value=exact,
            kind=ValidationIntermediateKind.TARGET_ACCOUNTING,
        )


class ConsistencyPlanConformanceGuard:
    PRODUCER_REF = "post-write-consistency-plan-conformance:v1"

    def __init__(
        self,
        intermediate_runtime: ValidationIntermediateRuntime | None = None,
    ) -> None:
        self.intermediate_runtime = intermediate_runtime

    def evaluate(
        self,
        *,
        plan: ConsistencyDiscoveryPlanSchema,
        discovery: ConsistencyCandidateDiscoveryResultSchema,
        profile: ConsistencyExecutionProfile | None = None,
        validation_scope: str | None = None,
        assertion_sections: Mapping[str, str] | None = None,
        required_relation_members: Iterable[Iterable[str]] = (),
        runtime_procedure_input=None,
    ) -> ConsistencyPlanConformanceResultSchema:
        exact_plan = ConsistencyDiscoveryPlanSchema.model_validate(plan)
        exact_discovery = ConsistencyCandidateDiscoveryResultSchema.model_validate(
            discovery
        )
        if exact_discovery.plan_ref != exact_plan.plan_ref:
            raise ConsistencyIntegrityError(
                "Consistency discovery belongs to another plan"
            )
        required = set(exact_plan.required_relation_keys)
        accounted = set(exact_discovery.candidate_relation_keys)
        missing = tuple(sorted(required - accounted))
        procedure_valid = not (exact_plan.required_logical_obligation_refs or exact_discovery.accounted_logical_obligation_refs)
        if exact_plan.runtime_procedure_input_hash is not None or validation_scope == "DOCUMENT_GLOBAL":
            from .consistency_procedure import ConsistencyProcedureLoweringSchema
            if runtime_procedure_input is None:
                procedure_valid = False
            else:
                lowering = ConsistencyProcedureLoweringSchema.model_validate(runtime_procedure_input)
                procedure_valid = (
                    validation_scope == "DOCUMENT_GLOBAL" and lowering.document_global_applicability == "APPLICABLE"
                    and exact_plan.runtime_procedure_input_hash == lowering.runtime_input_hash
                    and exact_plan.validation_run_id == lowering.validation_run_id
                    and exact_plan.artifact_state_ref == lowering.artifact_state_ref
                    and exact_plan.validation_subject_hash == lowering.validation_subject_hash
                    and exact_plan.assertion_refs == lowering.assertion_refs
                    and dict(assertion_sections or {}) == lowering.assertion_sections
                    and exact_plan.procedure_input_hash is not None
                    and {ConsistencyDiscoveryPlanner.relation_key(members) for members in lowering.required_relation_members()} <= required
                    and exact_plan.required_logical_obligation_refs == lowering.required_logical_obligations()
                    and exact_discovery.accounted_logical_obligation_refs == lowering.required_logical_obligations()
                )
                required_relation_members = tuple(sorted(set(required_relation_members) | set(lowering.required_relation_members())))
        if exact_plan.procedure_input_hash is not None:
            if profile is None or validation_scope is None or assertion_sections is None:
                procedure_valid = False
            else:
                exact_profile = ConsistencyExecutionProfile.model_validate(profile)
                structural = ConsistencyDiscoveryPlanner.structural_members(
                    assertion_refs=exact_plan.assertion_refs, validation_scope=validation_scope,
                    assertion_sections=assertion_sections,
                    exhaustive=len(exact_plan.assertion_refs) <= exact_profile.small_n_binary_member_limit,
                )
                supplied = tuple(sorted(set(structural) | {tuple(members) for members in required_relation_members}))
                expected_keys = {ConsistencyDiscoveryPlanner.relation_key(members) for members in supplied}
                procedure_valid = procedure_valid and (
                    all(ConsistencyDiscoveryPlanner.eligible(members, validation_scope=validation_scope, assertion_sections=assertion_sections) for members in supplied)
                    and required == expected_keys
                    and (exact_plan.profile_id, exact_plan.profile_version, exact_plan.execution_profile_hash, exact_plan.procedure_contract_version)
                    == (exact_profile.profile_id, exact_profile.profile_version, exact_profile.execution_profile_hash, exact_profile.procedure_contract_version)
                    and exact_plan.discovery_mode == ("SMALL_N_EXHAUSTIVE_MEMBERS" if len(exact_plan.assertion_refs) <= exact_profile.small_n_binary_member_limit else "LARGE_N_HIGH_RECALL")
                    and exact_plan.procedure_input_hash == canonical_sha256({
                        "validation_scope": validation_scope, "assertion_sections": dict(assertion_sections),
                        "required_relation_members": supplied, "execution_profile_hash": exact_profile.execution_profile_hash,
                    })
                )
        state = (
            ConsistencyPlanConformanceState.CONFORMING
            if exact_discovery.complete_required_plan_accounting
            and not missing and procedure_valid
            else ConsistencyPlanConformanceState.NONCONFORMING
        )
        kwargs = {
            "plan_ref": exact_plan.plan_ref,
            "discovery_ref": exact_discovery.discovery_ref,
            "required_relation_keys": exact_plan.required_relation_keys,
            "accounted_relation_keys": exact_discovery.candidate_relation_keys,
            "state": state,
            "missing_relation_keys": missing,
        }
        return _hashed_model(
            ConsistencyPlanConformanceResultSchema,
            ref_field="result_ref",
            hash_field="result_hash",
            prefix="consistency-plan-conformance://sha256/",
            **kwargs,
        )

    def commit(
        self,
        *,
        validation_run_id: str,
        result: ConsistencyPlanConformanceResultSchema,
    ) -> ValidationIntermediateRefSchema:
        exact = ConsistencyPlanConformanceResultSchema.model_validate(result)
        return _commit(
            self.intermediate_runtime,
            validation_run_id=validation_run_id,
            producer_ref=self.PRODUCER_REF,
            target_ref=exact.result_ref,
            value=exact,
            kind=ValidationIntermediateKind.TARGET_ACCOUNTING,
        )


class SemanticConsistencyEvaluator:
    PRODUCER_REF = "post-write-semantic-consistency:v1"

    def __init__(
        self,
        *,
        semantic_evaluator: Callable[[tuple[ConsistencyAssertionSchema, ...]], Mapping],
        intermediate_runtime: ValidationIntermediateRuntime | None = None,
    ) -> None:
        self.semantic_evaluator = semantic_evaluator
        self.intermediate_runtime = intermediate_runtime

    def bind(
        self,
        *,
        validation_run_id: str,
        validation_scope: str,
        scope_subject_ref: str,
        assertions: Iterable[ConsistencyAssertionSchema],
        semantic_contract_version: str = "semantic-relation-binding-v1",
        source_context_refs: Iterable[str] = (),
        logical_consumer_binding_refs: Iterable[str] = (),
        provenance_refs: Iterable[str] = (),
    ) -> SemanticRelationBindingSchema:
        items = tuple(
            sorted(
                (
                    ConsistencyAssertionSchema.model_validate(item)
                    for item in assertions
                ),
                key=lambda item: item.assertion_ref,
            )
        )
        if len(items) < 2:
            raise ConsistencyIntegrityError(
                "semantic Consistency binding requires >=2 assertions"
            )
        artifact_refs = {item.artifact_state_ref for item in items}
        subjects = {item.validation_subject_hash for item in items}
        if len(artifact_refs) != 1 or len(subjects) != 1:
            raise ConsistencyIntegrityError(
                "semantic Consistency assertions cross Artifact/subject"
            )
        raw = dict(self.semantic_evaluator(items))
        materiality = ConsistencyRelationMateriality(
            raw.get(
                "relation_materiality",
                ConsistencyRelationMateriality.INDETERMINATE,
            )
        )
        formal = raw.get("formal_relation_candidate")
        if formal is not None:
            formal = FormalRelationCandidateSchema.model_validate(formal)
            if (
                formal.lhs_assertion_ref not in {item.assertion_ref for item in items}
                or formal.rhs_assertion_ref
                not in {item.assertion_ref for item in items}
            ):
                raise ConsistencyIntegrityError(
                    "formal relation candidate escapes semantic member binding"
                )
        kwargs = {
            "semantic_contract_version": str(
                semantic_contract_version
            ).strip(),
            "validation_run_id": validation_run_id,
            "artifact_state_ref": items[0].artifact_state_ref,
            "validation_scope": str(validation_scope).strip(),
            "scope_subject_ref": str(scope_subject_ref).strip(),
            "member_assertion_refs": tuple(
                item.assertion_ref for item in items
            ),
            "source_context_refs": _canon(source_context_refs),
            "semantic_relation_description": str(
                raw.get("semantic_relation_description")
                or "semantic relation unresolved"
            ).strip(),
            "role_bindings": _canon(raw.get("role_bindings", ())),
            "scope_condition_bindings": _canon(
                raw.get("scope_condition_bindings", ())
            ),
            "relation_shape": (
                ConsistencyRelationShape.BINARY
                if len(items) == 2
                else ConsistencyRelationShape.COMPOSITE
            ),
            "relation_materiality": materiality,
            "formal_relation_candidate": formal,
            "ambiguity_codes": _canon(raw.get("ambiguity_codes", ())),
            "reason_codes": _canon(raw.get("reason_codes", ())),
            "physical_model_call_ref": raw.get("physical_model_call_ref"),
            "logical_consumer_binding_refs": _canon(
                logical_consumer_binding_refs
            ),
            "provenance_refs": _canon(provenance_refs),
        }
        return _hashed_model(
            SemanticRelationBindingSchema,
            ref_field="binding_ref",
            hash_field="binding_hash",
            prefix="semantic-relation-binding://sha256/",
            **kwargs,
        )

    def evaluate(
        self,
        *,
        binding: SemanticRelationBindingSchema,
        relation: ArtifactConsistencyRelation,
        observation_codes: Iterable[str] = (),
    ) -> SemanticConsistencyEvaluationResultSchema:
        exact = SemanticRelationBindingSchema.model_validate(binding)
        kwargs = {
            "binding_ref": exact.binding_ref,
            "relation": relation,
            "observation_codes": _canon(observation_codes),
        }
        return _hashed_model(
            SemanticConsistencyEvaluationResultSchema,
            ref_field="evaluation_ref",
            hash_field="evaluation_hash",
            prefix="semantic-consistency-evaluation://sha256/",
            **kwargs,
        )

    def commit_binding(
        self,
        binding: SemanticRelationBindingSchema,
    ) -> ValidationIntermediateRefSchema:
        exact = SemanticRelationBindingSchema.model_validate(binding)
        return _commit(
            self.intermediate_runtime,
            validation_run_id=exact.validation_run_id,
            producer_ref=self.PRODUCER_REF,
            target_ref=exact.binding_ref,
            value=exact,
            kind=ValidationIntermediateKind.MODEL_DERIVATION,
        )

    def commit_evaluation(
        self,
        *,
        validation_run_id: str,
        result: SemanticConsistencyEvaluationResultSchema,
    ) -> ValidationIntermediateRefSchema:
        exact = SemanticConsistencyEvaluationResultSchema.model_validate(result)
        return _commit(
            self.intermediate_runtime,
            validation_run_id=validation_run_id,
            producer_ref=self.PRODUCER_REF,
            target_ref=exact.evaluation_ref,
            value=exact,
            kind=ValidationIntermediateKind.MODEL_DERIVATION,
        )


class FormalExecutabilityGuard:
    PRODUCER_REF = "post-write-formal-executability:v2"

    def __init__(
        self,
        intermediate_runtime: ValidationIntermediateRuntime | None = None,
    ) -> None:
        self.intermediate_runtime = intermediate_runtime

    def evaluate(
        self,
        *,
        binding: SemanticRelationBindingSchema,
        assertions_by_ref: Mapping[str, ConsistencyAssertionSchema],
    ) -> FormalExecutabilityResultSchema:
        exact = SemanticRelationBindingSchema.model_validate(binding)
        candidate = exact.formal_relation_candidate
        state = FormalExecutabilityState.NOT_EXECUTABLE
        reasons: tuple[str, ...] = ("NO_FORMAL_CANDIDATE",)
        candidate_ref = None
        if candidate is not None:
            candidate_ref = candidate.formal_candidate_ref
            lhs = assertions_by_ref.get(candidate.lhs_assertion_ref)
            rhs = assertions_by_ref.get(candidate.rhs_assertion_ref)
            if lhs is None or rhs is None:
                state = FormalExecutabilityState.TECHNICAL_FAILURE
                reasons = ("FORMAL_OPERAND_REF_UNRESOLVED",)
            elif lhs.typed_value is None or rhs.typed_value is None:
                state = FormalExecutabilityState.NOT_EXECUTABLE
                reasons = ("FORMAL_TYPED_OPERAND_UNAVAILABLE",)
            elif (
                lhs.value_unit is not None
                and rhs.value_unit is not None
                and lhs.value_unit != rhs.value_unit
            ):
                state = FormalExecutabilityState.NOT_EXECUTABLE
                reasons = ("FORMAL_UNIT_CONVERSION_UNAVAILABLE",)
            elif type(lhs.typed_value) is not type(rhs.typed_value) and not (
                isinstance(lhs.typed_value, (int, float))
                and isinstance(rhs.typed_value, (int, float))
            ):
                state = FormalExecutabilityState.NOT_EXECUTABLE
                reasons = ("FORMAL_OPERAND_TYPE_MISMATCH",)
            elif isinstance(lhs.typed_value, str) and isinstance(rhs.typed_value, str):
                state = FormalExecutabilityState.NOT_EXECUTABLE
                reasons = ("FORMAL_TEXT_OPERAND_NOT_PROVEN_COMPARABLE",)
            else:
                state = FormalExecutabilityState.EXECUTABLE
                reasons = ()
        kwargs = {
            "binding_ref": exact.binding_ref,
            "formal_candidate_ref": candidate_ref,
            "state": state,
            "reason_codes": reasons,
        }
        return _hashed_model(
            FormalExecutabilityResultSchema,
            ref_field="result_ref",
            hash_field="result_hash",
            prefix="formal-executability://sha256/",
            **kwargs,
        )

    def commit(
        self,
        *,
        validation_run_id: str,
        result: FormalExecutabilityResultSchema,
    ) -> ValidationIntermediateRefSchema:
        exact = FormalExecutabilityResultSchema.model_validate(result)
        return _commit(
            self.intermediate_runtime,
            validation_run_id=validation_run_id,
            producer_ref=self.PRODUCER_REF,
            target_ref=exact.result_ref,
            value=exact,
            kind=ValidationIntermediateKind.TARGET_ACCOUNTING,
        )


class ExactConsistencyEvaluator:
    PRODUCER_REF = "post-write-exact-consistency:v1"

    def __init__(
        self,
        intermediate_runtime: ValidationIntermediateRuntime | None = None,
    ) -> None:
        self.intermediate_runtime = intermediate_runtime

    @staticmethod
    def _compare(lhs, rhs, predicate: str, tolerance: float | None) -> bool:
        if predicate == "EQ":
            if tolerance is not None and isinstance(lhs, (int, float)) and isinstance(
                rhs, (int, float)
            ):
                return abs(float(lhs) - float(rhs)) <= tolerance
            return lhs == rhs
        if predicate == "NE":
            return lhs != rhs
        if predicate == "LT":
            return lhs < rhs
        if predicate == "LE":
            return lhs <= rhs
        if predicate == "GT":
            return lhs > rhs
        if predicate == "GE":
            return lhs >= rhs
        raise ConsistencyIntegrityError("unsupported formal predicate")

    def evaluate(
        self,
        *,
        binding: SemanticRelationBindingSchema,
        executability: FormalExecutabilityResultSchema,
        assertions_by_ref: Mapping[str, ConsistencyAssertionSchema],
    ) -> ExactConsistencyEvaluationResultSchema:
        exact = SemanticRelationBindingSchema.model_validate(binding)
        guard = FormalExecutabilityResultSchema.model_validate(executability)
        if (
            guard.binding_ref != exact.binding_ref
            or guard.state is not FormalExecutabilityState.EXECUTABLE
            or exact.formal_relation_candidate is None
            or guard.formal_candidate_ref
            != exact.formal_relation_candidate.formal_candidate_ref
        ):
            raise ConsistencyIntegrityError(
                "ExactConsistencyEvaluator requires exact EXECUTABLE formal binding"
            )
        candidate = exact.formal_relation_candidate
        lhs = assertions_by_ref[candidate.lhs_assertion_ref].typed_value
        rhs = assertions_by_ref[candidate.rhs_assertion_ref].typed_value
        is_true = self._compare(
            lhs,
            rhs,
            candidate.predicate,
            candidate.precision_tolerance,
        )
        relation = (
            ArtifactConsistencyRelation.CONSISTENT
            if is_true
            else ArtifactConsistencyRelation.INCONSISTENT
        )
        kwargs = {
            "binding_ref": exact.binding_ref,
            "formal_candidate_ref": candidate.formal_candidate_ref,
            "relation": relation,
            "reason_codes": (
                ("FORMAL_RELATION_TRUE",)
                if is_true
                else ("FORMAL_RELATION_FALSE",)
            ),
        }
        return _hashed_model(
            ExactConsistencyEvaluationResultSchema,
            ref_field="evaluation_ref",
            hash_field="evaluation_hash",
            prefix="exact-consistency-evaluation://sha256/",
            **kwargs,
        )

    def commit(
        self,
        *,
        validation_run_id: str,
        result: ExactConsistencyEvaluationResultSchema,
    ) -> ValidationIntermediateRefSchema:
        exact = ExactConsistencyEvaluationResultSchema.model_validate(result)
        return _commit(
            self.intermediate_runtime,
            validation_run_id=validation_run_id,
            producer_ref=self.PRODUCER_REF,
            target_ref=exact.evaluation_ref,
            value=exact,
            kind=ValidationIntermediateKind.TARGET_ACCOUNTING,
        )


class ConsistencyProjector:
    VALIDATOR_ID = "post_write_consistency"
    VALIDATOR_VERSION = "v2"
    FACT_CONTRACT_VERSION = "artifact_consistency_fact_v1"

    _STATE = {
        ArtifactConsistencyRelation.CONSISTENT: ValidationSemanticState.PASS,
        ArtifactConsistencyRelation.INCONSISTENT: ValidationSemanticState.FAIL,
        ArtifactConsistencyRelation.INDETERMINATE: (
            ValidationSemanticState.INDETERMINATE
        ),
    }

    @staticmethod
    def logical_relation_key(binding: SemanticRelationBindingSchema) -> str:
        """Semantic target identity excludes model-call, plan and batch provenance."""
        exact = SemanticRelationBindingSchema.model_validate(binding)
        material = exact.model_dump(mode="json")
        fields = ("artifact_state_ref", "validation_scope", "scope_subject_ref",
                  "member_assertion_refs", "semantic_contract_version", "relation_shape",
                  "semantic_relation_description", "role_bindings", "scope_condition_bindings",
                  "formal_relation_candidate")
        return "consistency-logical-relation://sha256/" + canonical_sha256({key: material[key] for key in fields})

    @classmethod
    def project(
        cls,
        *,
        validation_run_id: str,
        subject_hash: str,
        plan: ConsistencyDiscoveryPlanSchema,
        conformance: ConsistencyPlanConformanceResultSchema,
        binding: SemanticRelationBindingSchema,
        evaluation: (
            ExactConsistencyEvaluationResultSchema
            | SemanticConsistencyEvaluationResultSchema
        ),
    ) -> ValidationFactSchema:
        exact_plan = ConsistencyDiscoveryPlanSchema.model_validate(plan)
        exact_conformance = ConsistencyPlanConformanceResultSchema.model_validate(
            conformance
        )
        exact_binding = SemanticRelationBindingSchema.model_validate(binding)
        if (
            exact_plan.validation_run_id != validation_run_id
            or exact_plan.validation_subject_hash != subject_hash
            or exact_binding.validation_run_id != validation_run_id
            or exact_binding.artifact_state_ref != exact_plan.artifact_state_ref
            or exact_conformance.plan_ref != exact_plan.plan_ref
        ):
            raise ConsistencyIntegrityError(
                "Consistency projector exact subject/plan binding mismatch"
            )
        if (
            exact_conformance.state
            is not ConsistencyPlanConformanceState.CONFORMING
        ):
            raise ConsistencyIntegrityError(
                "Consistency cannot project semantic PASS/FAIL without plan conformance"
            )
        if (
            exact_binding.relation_materiality
            is not ConsistencyRelationMateriality.MATERIAL_RELATION
        ):
            raise ConsistencyIntegrityError(
                "only MATERIAL_RELATION binding projects ARTIFACT_CONSISTENCY Fact"
            )
        relation = evaluation.relation
        if evaluation.binding_ref != exact_binding.binding_ref:
            raise ConsistencyIntegrityError(
                "Consistency evaluation belongs to another semantic binding"
            )
        payload = ArtifactConsistencyPayloadSchema(
            plan_ref=exact_plan.plan_ref,
            plan_conformance_result_ref=exact_conformance.result_ref,
            semantic_relation_binding_ref=exact_binding.binding_ref,
            evaluation_ref=evaluation.evaluation_ref,
            relation=relation,
            observation_codes=(
                evaluation.observation_codes
                if isinstance(
                    evaluation,
                    SemanticConsistencyEvaluationResultSchema,
                )
                else evaluation.reason_codes
            ),
            related_refs=tuple(
                sorted(
                    {
                        exact_plan.plan_ref,
                        exact_conformance.result_ref,
                        exact_binding.binding_ref,
                        evaluation.evaluation_ref,
                    }
                )
            ),
        )
        identity = {
            "family": ValidationFactFamily.ARTIFACT_CONSISTENCY.value,
            "validation_run_id": validation_run_id,
            "subject_hash": subject_hash,
            "target_ref": cls.logical_relation_key(exact_binding),
            "contract_version": cls.FACT_CONTRACT_VERSION,
            "payload": payload.model_dump(mode="json"),
        }
        fact_id = "validation-fact:artifact-consistency:" + canonical_sha256(
            identity
        )
        base = {
            "schema_version": "validation_fact_v1",
            "contract_version": cls.FACT_CONTRACT_VERSION,
            "fact_id": fact_id,
            "validation_run_id": validation_run_id,
            "subject_hash": subject_hash,
            "producer_validator_id": cls.VALIDATOR_ID,
            "producer_validator_version": cls.VALIDATOR_VERSION,
            "family": ValidationFactFamily.ARTIFACT_CONSISTENCY,
            "target_ref": cls.logical_relation_key(exact_binding),
            "semantic_state": cls._STATE[relation],
            "payload": payload.model_dump(mode="json"),
        }
        return ValidationFactSchema(
            **base,
            fact_sha256=canonical_sha256(base),
        )


__all__ = [
    "ConsistencyAssertionMaterializer",
    "ConsistencyCandidateDiscoveryExecutor",
    "ConsistencyDiscoveryPlanner",
    "ConsistencyIntegrityError",
    "ConsistencyPlanConformanceGuard",
    "ConsistencyProjector",
    "ExactConsistencyEvaluator",
    "FormalExecutabilityGuard",
    "SemanticConsistencyEvaluator",
]
