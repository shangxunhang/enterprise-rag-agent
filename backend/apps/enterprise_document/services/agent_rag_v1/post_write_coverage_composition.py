"""347 application composition for canonical Coverage / Completeness.

Coverage owns presence/completeness only.  Requirement truth remains the
RequirementMaterializer/Store; semantic satisfaction remains Relevancy; citation
validity remains Citation Integrity.  This layer composes existing Coverage and
shared Requirement owners into the production SECTION/DOCUMENT validation path.
"""
from __future__ import annotations

from datetime import datetime, timezone
import json
from threading import RLock
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from apps.enterprise_document.schemas.post_write.foundation import (
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
    ArtifactCoverageMatchTargetSetRefSchema,
    CoverageRelation,
    CoverageRequirementTargetSchema,
    RequirementArtifactAssessmentSchema,
    RequirementDomainCompletenessState,
    SemanticPresenceObservation,
    SharedRequirementArtifactCandidateDomainSchema,
)
from apps.enterprise_document.schemas.post_write.requirement import RequirementRefSchema
from apps.enterprise_document.services.post_write.artifact_state import (
    ArtifactCanonicalPayloadMaterializer,
)
from apps.enterprise_document.services.post_write.coverage import (
    CoverageProjector,
    CoverageRequirementTargetMaterializer,
    CoverageSemanticEvaluator,
    CoverageStructuralEvaluator,
)
from apps.enterprise_document.services.post_write.execution import (
    ValidatorInputEnvelopeBuilder,
)
from apps.enterprise_document.services.post_write.shared_requirement import (
    CoverageTargetProjector,
    RequirementArtifactAssessmentProjector,
    RequirementBatchConsumer,
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

COVERAGE_SEMANTIC_CONTRACT = "coverage_semantic_presence_batch_v1"
COVERAGE_POLICY_ID = "post-write-coverage-mainline"
COVERAGE_POLICY_VERSION = "v1"
COVERAGE_ASSESSMENT_CONTRACT = "requirement-artifact-assessment-v1"


class CoverageCompositionError(ValueError):
    def __init__(
        self,
        message: str,
        *,
        error_code: str = "COVERAGE_MAINLINE_FAILED",
        failure_category: FailureCategory = FailureCategory.VALIDATION,
        failure_origin: FailureOrigin = FailureOrigin.UNKNOWN,
    ) -> None:
        super().__init__(message)
        self.error_code = error_code
        self.failure_category = FailureCategory(failure_category)
        self.failure_origin = FailureOrigin(failure_origin)


class CoverageSemanticRow(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    requirement_id: str = Field(min_length=1)
    observation: SemanticPresenceObservation
    matched_unit_refs: tuple[str, ...] = ()
    reason_codes: tuple[str, ...] = ()

    @model_validator(mode="after")
    def _shape(self):
        for name in ("matched_unit_refs", "reason_codes"):
            values = tuple(getattr(self, name))
            if values != tuple(sorted(set(values))):
                raise ValueError(f"{name} must be canonical ordered-unique")
        if (
            self.observation is SemanticPresenceObservation.MATCH_OBSERVED
            and not self.matched_unit_refs
        ):
            raise ValueError("MATCH_OBSERVED requires at least one matched Artifact unit")
        if (
            self.observation is SemanticPresenceObservation.NO_MATCH_AFTER_COMPLETE_DOMAIN
            and self.matched_unit_refs
        ):
            raise ValueError("complete-domain no-match cannot carry matched Artifact units")
        return self


class CoverageSemanticReply(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    results: tuple[CoverageSemanticRow, ...]

    @model_validator(mode="after")
    def _unique(self):
        ids = [item.requirement_id for item in self.results]
        if len(ids) != len(set(ids)):
            raise ValueError("Coverage semantic reply contains duplicate requirement IDs")
        return self


class CoverageMainlineAccounting(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    schema_version: Literal["coverage_mainline_accounting_v1"] = (
        "coverage_mainline_accounting_v1"
    )
    input_hash: str
    artifact_state_ref: dict[str, Any]
    requirement_ref: dict[str, Any]
    subject_hash: str
    applicable_requirement_ids: tuple[str, ...]
    domain_ref: dict[str, Any] | None = None
    target_refs: tuple[dict[str, Any], ...] = ()
    match_refs: tuple[dict[str, Any], ...] = ()
    assessment_refs: tuple[dict[str, Any], ...] = ()
    fact_refs: tuple[dict[str, Any], ...] = ()
    state: Literal[
        "NOT_APPLICABLE", "PASS", "FAIL", "INDETERMINATE", "TECHNICAL_FAILURE"
    ]
    failure: dict[str, Any] | None = None
    semantic_input_receipt_ref: str | None = None

    @model_validator(mode="after")
    def _canonical(self):
        values = tuple(self.applicable_requirement_ids)
        if values != tuple(sorted(set(values))):
            raise ValueError("Coverage applicable requirement IDs must be ordered-unique")
        return self


class TargetPostWriteCoverageComposition:
    """Exact production Coverage composition over frozen Coverage owners."""

    VALIDATOR_ID = CoverageProjector.VALIDATOR_ID
    VERSION = "v2"
    _KINDS = {
        "STRUCTURAL_PART_PRESENCE",
        "FIELD_OR_SLOT_PRESENCE",
        "CITATION_PRESENCE",
    }

    def __init__(self, *, post_write, model_gateway) -> None:
        self.post_write = post_write
        self.gateway = model_gateway
        self.store = post_write.research_runtime.artifact_runtime.artifact_store
        self.estimator = DeterministicTokenEstimator()
        self._lock = RLock()

    @staticmethod
    def stage_plan() -> StagePlanSchema:
        stage = StageDescriptorSchema(
            stage_ref="coverage-mainline-stage:v1",
            stage_version="v1",
            sequence_index=0,
            stage_input_schema_ref="schema://coverage-mainline-input/v1",
            stage_output_schema_ref="schema://coverage-mainline-accounting/v1",
            execution_policy_ref="policy://coverage-mainline/v1",
            execution_policy_version="v1",
            timeout_seconds=120,
            max_attempts=1,
        )
        base = {
            "schema_version": "validator_stage_plan_v1",
            "stage_plan_ref": "stage-plan:coverage-mainline:v1",
            "validator_id": TargetPostWriteCoverageComposition.VALIDATOR_ID,
            "validator_version": TargetPostWriteCoverageComposition.VERSION,
            "stage_plan_version": "v1",
            "stages": [stage.model_dump(mode="json")],
        }
        return StagePlanSchema(**base, stage_plan_hash=canonical_sha256(base))

    @staticmethod
    def _artifact_units(artifact_ref, visible: dict[str, Any]) -> tuple[dict[str, Any], ...]:
        def make_unit(*, selector: dict[str, Any], payload: dict[str, Any]) -> dict[str, Any]:
            material = {
                "payload_kind": payload.get("payload_kind"),
                "section_id": payload.get("section_id"),
                "section_title": payload.get("section_title"),
                "content": payload.get("content"),
            }
            digest = canonical_sha256({
                "artifact_state_ref": artifact_ref.model_dump(mode="json"),
                "selector": selector,
                "content": material,
            })
            return {
                "unit_ref": "coverage-artifact-unit://sha256/" + digest,
                "selector": selector,
                **material,
            }

        if visible.get("payload_kind") == "section":
            return (
                make_unit(
                    selector={
                        "kind": "SECTION",
                        "section_id": visible.get("section_id"),
                    },
                    payload=visible,
                ),
            )
        if visible.get("payload_kind") == "document":
            rows = visible.get("included_sections")
            if not isinstance(rows, list) or not rows:
                raise CoverageCompositionError("Document Coverage requires included Sections")
            units = []
            for index, row in enumerate(rows):
                payload = row.get("visible_payload") if isinstance(row, dict) else None
                if not isinstance(payload, dict):
                    raise CoverageCompositionError("Document Coverage Section projection is malformed")
                units.append(make_unit(
                    selector={
                        "kind": "DOCUMENT_SECTION",
                        "index": index,
                        "section_key": row.get("section_key"),
                    },
                    payload=payload,
                ))
            return tuple(units)
        raise CoverageCompositionError("unsupported Artifact kind for Coverage")

    @classmethod
    def _coverage_requirements(cls, snapshot):
        return tuple(
            sorted(
                (item for item in snapshot.requirements if item.requirement_kind in cls._KINDS),
                key=lambda item: item.requirement_id,
            )
        )

    @classmethod
    def _verify_requirement_projection(cls, snapshot) -> tuple:
        task = [item for item in snapshot.requirements if item.requirement_id == "task-request"]
        if len(task) != 1:
            raise CoverageCompositionError("Coverage requires one canonical task-request")
        constraints = task[0].constraints
        required_sections = tuple(str(value).strip() for value in constraints.get("required_sections", ()))
        required_fields = tuple(str(value).strip() for value in constraints.get("required_fields", ()))
        citation_sections = tuple(
            str(value).strip() for value in constraints.get("citation_required_sections", ())
        )
        need_citation = bool(constraints.get("need_citation"))
        if (
            any(not value for value in (*required_sections, *required_fields, *citation_sections))
            or len(required_sections) != len(set(required_sections))
            or len(required_fields) != len(set(required_fields))
            or len(citation_sections) != len(set(citation_sections))
        ):
            raise CoverageCompositionError("canonical Coverage declarations are blank or duplicated")

        coverage = cls._coverage_requirements(snapshot)
        sections = sorted(
            (item for item in coverage if item.requirement_kind == "STRUCTURAL_PART_PRESENCE"),
            key=lambda item: int(item.constraints["source_index"]),
        )
        fields = sorted(
            (item for item in coverage if item.requirement_kind == "FIELD_OR_SLOT_PRESENCE"),
            key=lambda item: int(item.constraints["source_index"]),
        )
        citations = tuple(item for item in coverage if item.requirement_kind == "CITATION_PRESENCE")
        if tuple(item.constraints.get("section_title") for item in sections) != required_sections:
            raise CoverageCompositionError("canonical required-section Coverage entries are incomplete")
        if tuple(item.constraints.get("field_name") for item in fields) != required_fields:
            raise CoverageCompositionError("canonical required-field Coverage entries are incomplete")
        section_citations = sorted(
            (item for item in citations if not item.constraints.get("document_global")),
            key=lambda item: int(item.constraints["source_index"]),
        )
        globals_ = tuple(item for item in citations if item.constraints.get("document_global"))
        if citation_sections:
            if tuple(item.constraints.get("section_title") for item in section_citations) != citation_sections or globals_:
                raise CoverageCompositionError("canonical Section citation Coverage entries are incomplete")
        elif need_citation:
            if section_citations or len(globals_) != 1:
                raise CoverageCompositionError("canonical global citation Coverage entry is incomplete")
        elif citations:
            raise CoverageCompositionError("Coverage contains citation obligation without canonical source declaration")
        return coverage

    @staticmethod
    def _applicable(requirements, *, scope, units):
        if scope.value == "SECTION":
            if len(units) != 1:
                raise CoverageCompositionError("SECTION Coverage requires one exact Artifact unit")
            title = str(units[0].get("section_title") or "").strip()
            return tuple(
                item for item in requirements
                if (
                    item.requirement_kind == "STRUCTURAL_PART_PRESENCE"
                    and item.constraints.get("section_title") == title
                )
                or (
                    item.requirement_kind == "CITATION_PRESENCE"
                    and not item.constraints.get("document_global")
                    and item.constraints.get("section_title") == title
                )
            )
        if scope.value == "DOCUMENT":
            return tuple(requirements)
        raise CoverageCompositionError("unsupported Coverage validation scope")

    def _inputs(
        self,
        *,
        validation_run_id,
        subject_hash,
        artifact_ref,
        scope,
        target_ref,
        requirement_ref,
        source_sections,
        evidence_snapshot_ref=None,
        assembly_input_manifest_ref=None,
    ):
        snapshot = self.post_write.requirement_store.resolve_exact(requirement_ref)
        coverage_requirements = self._verify_requirement_projection(snapshot)
        artifact = self.post_write.artifact_store.resolve_exact(artifact_ref)
        visible = ArtifactCanonicalPayloadMaterializer.business_payload(
            artifact.canonical_payload_ref
        )
        units = self._artifact_units(artifact_ref, visible)
        applicable = self._applicable(coverage_requirements, scope=scope, units=units)

        citation_snapshot = None
        if any(item.requirement_kind == "CITATION_PRESENCE" for item in applicable):
            citation_snapshot = self.post_write.citation_integrity.read_committed_inputs(
                validation_run_id=validation_run_id,
                subject_hash=subject_hash,
                artifact_ref=artifact_ref,
                scope=scope,
                source_sections=source_sections,
                evidence_snapshot_ref=evidence_snapshot_ref,
                assembly_input_manifest_ref=assembly_input_manifest_ref,
            )[0]
        material = {
            "contract": "coverage-mainline-v1",
            "policy_id": COVERAGE_POLICY_ID,
            "policy_version": COVERAGE_POLICY_VERSION,
            "validation_run_id": validation_run_id,
            "subject_hash": subject_hash,
            "validation_scope": scope.value,
            "target_ref": target_ref,
            "artifact_state_ref": artifact_ref.model_dump(mode="json"),
            "requirement_ref": requirement_ref.model_dump(mode="json"),
            "requirement_hash": snapshot.requirement_hash,
            "applicable_requirements": [item.model_dump(mode="json") for item in applicable],
            "artifact_units": list(units),
            "citation_occurrences": (
                list(citation_snapshot.occurrences) if citation_snapshot is not None else []
            ),
            "citation_catalog_hash": (
                citation_snapshot.catalog.catalog_hash if citation_snapshot is not None else None
            ),
        }
        return material, snapshot, applicable, units, citation_snapshot

    def _identity(self, source_sections):
        if not source_sections:
            raise CoverageCompositionError("Coverage requires exact execution lineage")
        lifecycle = self.post_write.lifecycle_service
        if lifecycle is None:
            raise CoverageCompositionError("Coverage requires canonical lifecycle owner")
        lineages = [
            lifecycle.resolve_active_lineage(
                row.active_execution_lineage.run_id,
                row.active_execution_lineage.attempt_scope_ref,
            )
            for row in source_sections
        ]
        lineage = sorted(lineages, key=lambda row: row.attempt_scope_ref)[0]
        identity = {
            "task_id": lifecycle.load_run_identity(lineage.run_id).task_id,
            "run_id": lineage.run_id,
            "section_id": lineage.attempt_scope_ref.removeprefix("section:"),
        }
        return identity, lineage

    @staticmethod
    def _parse_semantic_reply(raw, material):
        payload = json.loads(raw) if isinstance(raw, str) else raw
        reply = CoverageSemanticReply.model_validate(payload)
        expected = {
            item["requirement_id"]
            for item in material["semantic_requirements"]
        }
        actual = {item.requirement_id for item in reply.results}
        if actual != expected or len(reply.results) != len(expected):
            raise CoverageCompositionError("Coverage semantic reply omitted or added requirement")
        units = {item["unit_ref"] for item in material["artifact_units"]}
        for item in reply.results:
            if not set(item.matched_unit_refs) <= units:
                raise CoverageCompositionError("Coverage semantic matches escape exact Artifact domain")
        return tuple(sorted(reply.results, key=lambda item: item.requirement_id))

    def _package_semantic(self, material, lineage, occurrence):
        capability = dict(
            self.gateway.routing_capabilities(model_role=ModelRole.GENERAL.value)
        )
        output_limit = min(2048, int(capability["safe_max_output_tokens"]))
        cap_hash = canonical_sha256({
            **capability,
            "effective_max_output_tokens": output_limit,
        })
        token_hash = canonical_sha256({
            "estimator": "deterministic_mixed_text",
            "version": "v1",
        })
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
            source_schema_id="coverage_semantic_artifact_v1",
            source_schema_version="v1",
            source_semantic_fingerprint=canonical_sha256(artifact_payload),
            authority_owner="POST_WRITE/ARTIFACT",
            run_id=lineage.run_id,
            scope_ref=lineage.attempt_scope_ref,
            currentness_binding=ContextSourceStatusBindingSchema(
                status_schema_id="coverage_semantic_currentness_v1",
                status_schema_version="v1",
                resolver_id=COVERAGE_POLICY_ID,
                resolver_policy_version="v1",
                status_payload=stamp,
                status_payload_sha256=canonical_sha256(stamp),
                status_semantic_fingerprint=canonical_sha256(stamp),
            ),
        )
        candidate = ContextSourceCandidateSchema(
            candidate_id="coverage-semantic-artifact",
            source_binding=source,
            title="Exact complete Artifact domain for Coverage",
            representations=(ContextRepresentationOptionSchema(
                representation_level=ContextRepresentationLevel.EXACT,
                projection_schema_id="coverage_semantic_artifact_v1",
                projection_schema_version="v1",
                representation_policy_id=COVERAGE_POLICY_ID,
                representation_policy_version="v1",
                materialized_payload=artifact_payload,
                materialized_payload_sha256=canonical_sha256(artifact_payload),
                protected_field_evidence=tuple(artifact_payload),
            ),),
        )
        runtime.register_source_provider(ContextSourceProviderRegistration(
            source_family=ContextSourceFamily.PARAGRAPH_ARTIFACT,
            provider_id=COVERAGE_POLICY_ID,
            provider_version="v1",
            candidate_reader=lambda _subject: (candidate,),
            source_binding_resolver=lambda value: value,
            hard_eligibility_validator=lambda value, subject: (
                value.run_id == subject.run_id
                and value.scope_ref == subject.consumer_scope_ref,
                ()
                if value.run_id == subject.run_id
                and value.scope_ref == subject.consumer_scope_ref
                else ("COVERAGE_ARTIFACT_SCOPE_MISMATCH",),
            ),
        ))
        policy = ContextPolicyBindingSchema(
            source_catalog_id=runtime.SOURCE_CATALOG_ID,
            source_catalog_version=runtime.SOURCE_CATALOG_VERSION,
            role_policy_id=COVERAGE_POLICY_ID,
            role_policy_version="v1",
            cross_section_policy_id="coverage-complete-artifact-domain",
            cross_section_policy_version="v1",
            consumer_authorization_policy_id="canonical-coverage-requirements",
            consumer_authorization_policy_version="v1",
            selection_policy_id="sif10-hierarchical-selection",
            selection_policy_version="v1",
            budget_policy_id="sif10-context-budget",
            budget_policy_version="v1",
            representation_policy_id=COVERAGE_POLICY_ID,
            representation_policy_version="v1",
        )
        system = (
            "Evaluate only whether each supplied canonical Coverage field/slot target is present "
            "somewhere in the complete exact Artifact domain. Do not judge adequacy, correctness, "
            "Evidence support, citations, Gate, correction or release."
        )
        role = (
            "Semantic presence may be expressed by paraphrase and must not depend on keyword hits. "
            "Use NO_MATCH_AFTER_COMPLETE_DOMAIN only after considering every supplied Artifact unit. "
            "Use INDETERMINATE when presence cannot be trusted. Never invent a match or a target."
        )
        template = (
            'Return only JSON {"results":[{"requirement_id":"exact id",'
            '"observation":"MATCH_OBSERVED|NO_MATCH_AFTER_COMPLETE_DOMAIN|INDETERMINATE",'
            '"matched_unit_refs":["exact unit_ref"],"reason_codes":[]}]}. '
            "Exactly one result per supplied requirement; matched refs must be from the supplied domain."
        )
        output_contract = json.dumps({
            "contract": COVERAGE_SEMANTIC_CONTRACT,
            "complete_domain": True,
        }, sort_keys=True)
        requirement_payload = json.dumps({
            "requirement_ref": material["requirement_ref"],
            "semantic_requirements": material["semantic_requirements"],
        }, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        control_ref = "coverage-semantic://sha256/" + canonical_sha256(requirement_payload)
        instructions = ContextInstructionMaterialSchema(
            system_instruction=system,
            role_instruction=role,
            prompt_template=template,
            task_output_contract=output_contract,
            dynamic_control_payloads=((control_ref, requirement_payload),),
        )
        binding = InstructionBindingSchema(
            system_instruction_ref="instruction://coverage-semantic/system/v1",
            system_instruction_version="v1",
            system_instruction_sha256=canonical_sha256(system),
            role_instruction_ref="instruction://coverage-semantic/role/v1",
            role_instruction_version="v1",
            role_instruction_sha256=canonical_sha256(role),
            prompt_template_ref="template://coverage-semantic/v1",
            prompt_template_version="v1",
            prompt_template_sha256=canonical_sha256(template),
            task_output_contract_ref="contract://coverage-semantic/v1",
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
                agent_role="COVERAGE_SEMANTIC_PRESENCE",
                execution_mode="EVALUATE",
                context_purpose="COVERAGE_FIELD_PRESENCE",
                consumer_scope_ref=lineage.attempt_scope_ref,
            ),
            role_policy=ContextRolePolicySchema(
                role_policy_id=COVERAGE_POLICY_ID,
                role_policy_version="v1",
                agent_role="COVERAGE_SEMANTIC_PRESENCE",
                execution_mode="EVALUATE",
                context_purpose="COVERAGE_FIELD_PRESENCE",
                source_rules=(rule,),
            ),
            policy_binding=policy,
            model_capability_binding=cap,
            instruction_binding=binding,
            instruction_material=instructions,
            reserved_instruction_tokens=max(
                1,
                self.estimator.estimate(
                    json.dumps(instructions.model_dump(mode="json"))
                ),
            ),
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

    def _evaluate_semantic(self, material, identity, lineage, validate_current):
        key = canonical_sha256(material)
        intent_ref = f"artifact://coverage-semantic/{key}/input"
        result_ref = f"artifact://coverage-semantic/{key}/result"

        def read(ref):
            try:
                return self.store.get_json(ref, **identity)
            except FileNotFoundError:
                return None

        with self._lock, InterProcessFileLock(
            self.store.root / "coverage_semantic_locks" / (key + ".lock")
        ):
            prior, result = read(intent_ref), read(result_ref)
            if prior is not None and (
                prior.get("input_hash") != key
                or prior.get("input_material") != material
            ):
                raise CoverageCompositionError("Coverage semantic immutable intent differs")
            if result is not None:
                if prior is None or result.get("input_hash") != key:
                    raise CoverageCompositionError("Coverage semantic result has no exact intent")
                if result.get("outcome") == "failed":
                    failure = result.get("failure") or {}
                    if failure.get("kind") == "CANONICAL_BUDGET_FAILURE":
                        raise CoverageCompositionError(
                            failure.get("message") or "canonical Budget failure",
                            error_code=failure.get("error_code") or "COVERAGE_BUDGET_FAILED",
                            failure_category=failure.get("category") or FailureCategory.RESOURCE_EXHAUSTED,
                            failure_origin=failure.get("origin") or FailureOrigin.CANONICAL_BUDGET,
                        )
                    return None, intent_ref, failure
                if result.get("outcome") != "completed":
                    raise CoverageCompositionError("Coverage semantic result outcome is invalid")
                validate_current()
                return self._parse_semantic_reply(result["candidate"], material), intent_ref, None
            if prior is not None:
                raise CoverageCompositionError(
                    "Coverage semantic intent without result; no automatic redispatch"
                )

            # complete_item() already entered with an exact source snapshot.
            # Keep the full currentness fence immediately before model dispatch
            # (after immutable intent persistence); a second full traversal here
            # only duplicates durable reads without narrowing the dispatch window.
            self.post_write.lifecycle_service.assert_attempt_committable(
                run_id=lineage.run_id,
                attempt_scope_ref=lineage.attempt_scope_ref,
                attempt_id=lineage.attempt_id,
                input_snapshot_version=lineage.input_snapshot_version,
            )
            occurrence = "coverage-semantic-call://sha256/" + key
            package, limit = self._package_semantic(material, lineage, occurrence)
            request = ModelRequestSchema(
                model_call_id="model_call_coverage_semantic_" + key,
                task_id=identity["task_id"],
                run_id=identity["run_id"],
                caller_agent="CoverageSemanticPresenceEvaluator",
                model_role=ModelRole.GENERAL.value,
                prompt="",
                messages=package.model_input.final_input_material.inline_final_input_payload[
                    "messages"
                ],
                max_tokens=limit,
                temperature=0.0,
                created_at=datetime.now(timezone.utc).isoformat(),
                extra={
                    "call_purpose": "coverage_semantic_presence",
                    "response_contract": COVERAGE_SEMANTIC_CONTRACT,
                    "section_id": identity["section_id"],
                    "attempt_scope_ref": lineage.attempt_scope_ref,
                    "attempt_id": lineage.attempt_id,
                    "input_snapshot_version": lineage.input_snapshot_version,
                    "logical_operation_id": occurrence,
                    "prompt_id": COVERAGE_SEMANTIC_CONTRACT,
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
                    "schema_version": "coverage_semantic_input_v1",
                    "input_hash": key,
                    "input_material": material,
                    "context_package": package.model_dump(mode="json"),
                    "request": request.model_dump(mode="json"),
                },
                artifact_ref=intent_ref,
                artifact_type="coverage_semantic_input",
                schema_version="coverage_semantic_input_v1",
                created_by=COVERAGE_POLICY_ID,
                **identity,
            )
            try:
                checkpoint_current_execution()
                validate_current()
                response = self.gateway.generate(request)
                if (response.model_call_id, response.task_id, response.run_id) != (
                    request.model_call_id,
                    request.task_id,
                    request.run_id,
                ):
                    raise CoverageCompositionError("Coverage semantic provider identity mismatch")
                if not response.success:
                    error = response.error
                    raise CoverageCompositionError(
                        error.message
                        if error
                        else (response.error_message or "Coverage semantic provider failed"),
                        error_code=(
                            error.error_code if error else "COVERAGE_SEMANTIC_PROVIDER_FAILED"
                        ),
                        failure_category=(
                            error.failure_category if error else FailureCategory.UNKNOWN
                        ),
                        failure_origin=response.failure_origin,
                    )
                rows = self._parse_semantic_reply(response.content, material)
                validate_current()
                result = {
                    "schema_version": "coverage_semantic_result_v1",
                    "input_hash": key,
                    "outcome": "completed",
                    "candidate": {
                        "results": [item.model_dump(mode="json") for item in rows]
                    },
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
                    FailureCategory.RESOURCE_EXHAUSTED if budget else classify_failure(exc)
                )
                origin = (
                    FailureOrigin.CANONICAL_BUDGET
                    if budget
                    else FailureOrigin(
                        getattr(exc, "failure_origin", FailureOrigin.UNKNOWN)
                    )
                )
                failure = {
                    "schema_version": "coverage_semantic_failure_v1",
                    "kind": (
                        "CANONICAL_BUDGET_FAILURE"
                        if budget
                        else "COVERAGE_SEMANTIC_TECHNICAL_FAILURE"
                    ),
                    "error_type": type(exc).__name__,
                    "error_code": getattr(exc, "error_code", type(exc).__name__),
                    "message": str(exc),
                    "category": category.value,
                    "origin": origin.value,
                }
                result = {
                    "schema_version": "coverage_semantic_result_v1",
                    "input_hash": key,
                    "outcome": "failed",
                    "failure": failure,
                }
                rows = None
            self.store.put_json(
                result,
                artifact_ref=result_ref,
                artifact_type="coverage_semantic_result",
                schema_version="coverage_semantic_result_v1",
                created_by=COVERAGE_POLICY_ID,
                **identity,
            )
            if failure and failure["kind"] == "CANONICAL_BUDGET_FAILURE":
                raise CoverageCompositionError(
                    failure["message"],
                    error_code=failure["error_code"],
                    failure_category=failure["category"],
                    failure_origin=failure["origin"],
                )
            return rows, intent_ref, failure

    @staticmethod
    def _target_spec(requirement):
        kind = requirement.requirement_kind
        if kind == "STRUCTURAL_PART_PRESENCE":
            title = str(requirement.constraints["section_title"])
            return {
                "target_kind": "STRUCTURAL_PART",
                "canonical_subject_selector": "section-title:" + title,
                "normalized_presence_constraint": "required_section_present",
                "required_count": 1,
            }
        if kind == "FIELD_OR_SLOT_PRESENCE":
            field_name = str(requirement.constraints["field_name"])
            return {
                "target_kind": "FIELD_OR_SLOT",
                "canonical_subject_selector": "required-field:" + field_name,
                "normalized_presence_constraint": "required_field_semantically_present",
                "required_count": 1,
            }
        if kind == "CITATION_PRESENCE":
            if requirement.constraints.get("document_global"):
                selector = "document:any-citation"
            else:
                selector = "citation-section:" + str(requirement.constraints["section_title"])
            return {
                "target_kind": "CITATION_PRESENCE_TARGET",
                "canonical_subject_selector": selector,
                "normalized_presence_constraint": "citation_occurrence_present",
                "required_count": 1,
            }
        raise CoverageCompositionError("unsupported canonical Coverage requirement kind")

    @staticmethod
    def _structural_matches(requirement, units, citation_occurrences):
        kind = (
            requirement.requirement_kind
            if hasattr(requirement, "requirement_kind")
            else requirement["requirement_kind"]
        )
        constraints = (
            requirement.constraints
            if hasattr(requirement, "constraints")
            else requirement["constraints"]
        )
        if kind == "STRUCTURAL_PART_PRESENCE":
            title = constraints["section_title"]
            return tuple(
                sorted(
                    item["unit_ref"]
                    for item in units
                    if str(item.get("section_title") or "").strip() == title
                )
            )
        if kind == "CITATION_PRESENCE":
            occurrence_sections = {
                str(item.get("span", {}).get("section_id") or "")
                for item in citation_occurrences
            }
            if constraints.get("document_global"):
                return tuple(
                    sorted(
                        item["unit_ref"]
                        for item in units
                        if str(
                            item.get("selector", {}).get("section_key")
                            or item.get("selector", {}).get("section_id")
                            or ""
                        )
                        in occurrence_sections
                    )
                )
            title = constraints["section_title"]
            return tuple(
                sorted(
                    item["unit_ref"]
                    for item in units
                    if str(item.get("section_title") or "").strip() == title
                    and str(
                        item.get("selector", {}).get("section_key")
                        or item.get("selector", {}).get("section_id")
                        or ""
                    )
                    in occurrence_sections
                )
            )
        raise CoverageCompositionError("semantic field target cannot use structural matcher")

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
        evidence_snapshot_ref=None,
        assembly_input_manifest_ref=None,
    ) -> ValidationCompletionItemSchema:
        pw = self.post_write
        args = dict(
            validation_run_id=validation_run_id,
            subject_hash=subject_hash,
            artifact_ref=artifact_ref,
            scope=scope,
            target_ref=target_ref,
            requirement_ref=requirement_ref,
            source_sections=source_sections,
            evidence_snapshot_ref=evidence_snapshot_ref,
            assembly_input_manifest_ref=assembly_input_manifest_ref,
        )
        material, snapshot, applicable, units, citation_snapshot = self._inputs(**args)
        item_ref = "coverage:item:" + artifact_ref.artifact_hash
        input_hash = canonical_sha256(material)
        provenance = (
            "coverage-mainline-input://sha256/" + input_hash,
            requirement_ref.requirement_snapshot_id,
        )
        plan = self.stage_plan()
        envelope = ValidatorInputEnvelopeBuilder(
            validation_view_resolver=pw.validation_view_resolver
        ).build(
            validation_run_id=validation_run_id,
            subject_hash=subject_hash,
            artifact_state_ref=artifact_ref,
            stage_plan=plan,
            applicable_expected_validation_item_refs=(item_ref,),
            exact_authority_refs=provenance,
            execution_contract_version="validator-execution-v1",
        )
        execution_ref = "validator-execution:coverage-mainline:" + canonical_sha256({
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
                raise CoverageCompositionError("committed Coverage execution identity/version differs")
            accounting = pw.validation_intermediate_runtime.resolve_typed(
                record.intermediate_refs[0], CoverageMainlineAccounting
            )
            self._verify_accounting(accounting, material, identity)
            refs = tuple(
                ValidationFactRefSchema.model_validate(item)
                for item in accounting.fact_refs
            )
            expected_accounting = record.expected_item_accounting[0]
            if (
                refs != record.produced_fact_refs
                or expected_accounting.fact_refs != refs
                or expected_accounting.provenance_refs != tuple(sorted(provenance))
                or (
                    accounting.failure is None
                    and expected_accounting.execution_state
                    is not ExpectedItemExecutionState.COMPLETED
                )
                or (
                    accounting.failure is not None
                    and expected_accounting.execution_state
                    is not ExpectedItemExecutionState.TECHNICAL_FAILURE
                )
                or expected_accounting.technical_failure_ref != accounting.failure
            ):
                raise CoverageCompositionError("committed Coverage execution accounting differs")
            return ValidationCompletionItemSchema(
                expected_validation_item_ref=item_ref,
                required=True,
                state=accounting.state,
                fact_refs=refs,
                technical_failure_ref=accounting.failure,
            )

        run = pw.validation_store.resolve_run_exact(validation_run_id)
        if run.execution_state is ValidationRunState.COMPLETED:
            raise CoverageCompositionError(
                "terminal ValidationRun is missing committed Coverage execution"
            )

        semantic_requirements = tuple(
            item for item in applicable
            if item.requirement_kind == "FIELD_OR_SLOT_PRESENCE"
        )
        semantic_material = {
            "contract": COVERAGE_SEMANTIC_CONTRACT,
            "policy_id": COVERAGE_POLICY_ID,
            "policy_version": COVERAGE_POLICY_VERSION,
            "validation_run_id": validation_run_id,
            "subject_hash": subject_hash,
            "artifact_state_ref": artifact_ref.model_dump(mode="json"),
            "requirement_ref": requirement_ref.model_dump(mode="json"),
            "semantic_requirements": [
                item.model_dump(mode="json") for item in semantic_requirements
            ],
            "artifact_units": list(units),
        }

        def validate_current():
            current, current_snapshot, current_applicable, current_units, current_citation = self._inputs(**args)
            if (
                current != material
                or current_snapshot != snapshot
                or current_applicable != applicable
                or current_units != units
                or current_citation != citation_snapshot
            ):
                raise CoverageCompositionError("Coverage Requirement/Artifact sources changed from frozen input")

        rows = ()
        semantic_intent_ref = None
        failure = None
        if semantic_requirements:
            rows, semantic_intent_ref, failure = self._evaluate_semantic(
                semantic_material,
                identity,
                lineage,
                validate_current,
            )

        applicable_ids = tuple(sorted(item.requirement_id for item in applicable))
        target_objects = []
        target_refs = []
        match_objects = []
        match_refs = []
        assessment_objects = []
        assessment_refs = []
        facts = []
        domain_ref = None

        if applicable:
            unit_refs = tuple(sorted(item["unit_ref"] for item in units))
            domain_builder = SharedRequirementArtifactCandidateDomainBuilder(
                pw.validation_intermediate_runtime
            )
            domain = domain_builder.build(
                artifact_state_ref=artifact_ref,
                validation_scope=scope.value,
                subject_selector_ref=target_ref,
                complete_applicable_domain_refs=unit_refs,
                prioritized_candidate_refs=unit_refs,
                consumer_requirements=applicable_ids,
                completeness_state=RequirementDomainCompletenessState.COMPLETE,
                provenance_refs=provenance,
            )
            domain_ref = domain_builder.commit(
                validation_run_id=validation_run_id,
                domain=domain,
            )
            materializer = CoverageRequirementTargetMaterializer(
                pw.validation_intermediate_runtime
            )
            match_projector = CoverageTargetProjector(
                pw.validation_intermediate_runtime
            )
            semantic_by_id = {
                item.requirement_id: item for item in (rows or ())
            }
            assessment_projector = RequirementArtifactAssessmentProjector(
                pw.validation_intermediate_runtime
            )
            for requirement in applicable:
                target = materializer.materialize(
                    requirement_ref=requirement_ref,
                    requirement=requirement,
                    artifact_state_ref=artifact_ref,
                    validation_scope=scope.value,
                    target_specs=(self._target_spec(requirement),),
                    provenance_refs=provenance,
                )[0]
                target_ref_record = materializer.commit(
                    validation_run_id=validation_run_id,
                    target=target,
                )
                target_objects.append(target)
                target_refs.append(target_ref_record)

                if requirement.requirement_kind == "FIELD_OR_SLOT_PRESENCE":
                    if failure is not None:
                        continue
                    row = semantic_by_id[requirement.requirement_id]
                    matched = row.matched_unit_refs
                    match = match_projector.project(
                        coverage_target=target,
                        candidate_domain=domain,
                        matched_unit_refs=matched,
                        match_state=row.observation.value,
                        complete_domain=True,
                        provenance_refs=provenance,
                    )
                    match_ref = match_projector.commit(
                        validation_run_id=validation_run_id,
                        target_set=match,
                    )
                    consumer = RequirementBatchConsumer(
                        requirement_ref=requirement_ref,
                        requirement=requirement,
                        artifact_state_ref=artifact_ref,
                        candidate_domain=domain,
                        coverage_target=target,
                        coverage_projection=match,
                    )
                    assessment = assessment_projector.project(
                        validation_run_id=validation_run_id,
                        consumer=consumer,
                        assessment_contract_version=COVERAGE_ASSESSMENT_CONTRACT,
                        adherence_observation=None,
                        semantic_presence_observation=row.observation,
                        response_refs=(),
                        presence_refs=matched,
                        observed_count=len(matched),
                        reason_codes=row.reason_codes,
                        physical_model_call_ref=(
                            "physical-model-call://"
                            + canonical_sha256({
                                "semantic_input": semantic_material,
                                "intent_ref": semantic_intent_ref,
                            })
                        ),
                        logical_consumer_binding_refs=(
                            "coverage-consumer://" + requirement.requirement_id,
                        ),
                        provenance_refs=provenance + (semantic_intent_ref,),
                    )
                    assessment_ref = assessment_projector.commit(assessment)
                    relation = CoverageSemanticEvaluator.evaluate(
                        target=target,
                        match_target_set=match,
                        assessment=assessment,
                    )
                    fact = CoverageProjector.project(
                        validation_run_id=validation_run_id,
                        subject_hash=subject_hash,
                        target=target,
                        match_target_set=match,
                        relation=relation,
                        observed_count=len(matched),
                        observation_codes=row.reason_codes,
                        related_refs=(assessment.assessment_ref,),
                    )
                    match_objects.append(match)
                    match_refs.append(match_ref)
                    assessment_objects.append(assessment)
                    assessment_refs.append(assessment_ref)
                    facts.append(fact)
                    continue

                matched = self._structural_matches(
                    requirement,
                    units,
                    material["citation_occurrences"],
                )
                match = match_projector.project(
                    coverage_target=target,
                    candidate_domain=domain,
                    matched_unit_refs=matched,
                    match_state="MATCH_OBSERVED" if matched else "NO_MATCH_OBSERVED",
                    complete_domain=True,
                    provenance_refs=provenance,
                )
                match_ref = match_projector.commit(
                    validation_run_id=validation_run_id,
                    target_set=match,
                )
                relation = CoverageStructuralEvaluator.evaluate(
                    target=target,
                    match_target_set=match,
                )
                fact = CoverageProjector.project(
                    validation_run_id=validation_run_id,
                    subject_hash=subject_hash,
                    target=target,
                    match_target_set=match,
                    relation=relation,
                    observed_count=len(matched),
                    observation_codes=(
                        "EXACT_STRUCTURAL_PRESENCE_ACCOUNTING"
                        if requirement.requirement_kind == "STRUCTURAL_PART_PRESENCE"
                        else "EXACT_CITATION_OCCURRENCE_PRESENCE_ACCOUNTING",
                    ),
                    related_refs=provenance,
                )
                match_objects.append(match)
                match_refs.append(match_ref)
                facts.append(fact)

        fact_refs = tuple(
            pw.validation_store.put_fact_if_absent_or_same(item) for item in facts
        )
        states = {item.semantic_state.value for item in facts}
        if failure is not None:
            state = "TECHNICAL_FAILURE"
        elif not applicable:
            state = "NOT_APPLICABLE"
        elif "FAIL" in states:
            state = "FAIL"
        elif "INDETERMINATE" in states:
            state = "INDETERMINATE"
        else:
            state = "PASS"
        accounting = CoverageMainlineAccounting(
            input_hash=input_hash,
            artifact_state_ref=artifact_ref.model_dump(mode="json"),
            requirement_ref=requirement_ref.model_dump(mode="json"),
            subject_hash=subject_hash,
            applicable_requirement_ids=applicable_ids,
            domain_ref=(domain_ref.model_dump(mode="json") if domain_ref else None),
            target_refs=tuple(item.model_dump(mode="json") for item in target_refs),
            match_refs=tuple(item.model_dump(mode="json") for item in match_refs),
            assessment_refs=tuple(
                item.model_dump(mode="json") for item in assessment_refs
            ),
            fact_refs=tuple(item.model_dump(mode="json") for item in fact_refs),
            state=state,
            failure=failure,
            semantic_input_receipt_ref=semantic_intent_ref,
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
            sequence = pw.validation_store._next_event_sequence(
                connection,
                validation_run_id,
            )
        event = pw.validation_store._event(
            validation_run_id=validation_run_id,
            event_id="validator-terminal:coverage-mainline:" + canonical_sha256(execution_ref),
            sequence=sequence,
            event_kind=(
                "VALIDATOR_TECHNICAL_FAILURE"
                if failure is not None
                else "VALIDATOR_COMPLETED"
            ),
            payload={"validator_execution_ref": execution_ref},
            occurred_at_epoch=0.0,
        )
        item_accounting = ExpectedItemExecutionAccountingSchema(
            expected_validation_item_ref=item_ref,
            execution_state=(
                ExpectedItemExecutionState.TECHNICAL_FAILURE
                if failure is not None
                else ExpectedItemExecutionState.COMPLETED
            ),
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
            "terminal_execution_state": (
                "TECHNICAL_FAILURE" if failure is not None else "COMPLETED"
            ),
            "expected_item_accounting": [item_accounting.model_dump(mode="json")],
            "produced_fact_refs": [item.model_dump(mode="json") for item in fact_refs],
            "intermediate_refs": [intermediate.model_dump(mode="json")],
        }
        pw.validation_store.finalize_validator_execution(
            event,
            ValidatorExecutionRecordSchema(
                **base,
                record_sha256=canonical_sha256(base),
            ),
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
        exact = CoverageMainlineAccounting.model_validate(accounting)
        applicable = {
            item["requirement_id"]: item for item in material["applicable_requirements"]
        }
        expected_ids = tuple(sorted(applicable))
        if (
            exact.input_hash != canonical_sha256(material)
            or exact.artifact_state_ref != material["artifact_state_ref"]
            or exact.requirement_ref != material["requirement_ref"]
            or exact.subject_hash != material["subject_hash"]
            or exact.applicable_requirement_ids != expected_ids
        ):
            raise CoverageCompositionError("Coverage accounting input binding differs")
        if not expected_ids:
            if (
                exact.state != "NOT_APPLICABLE"
                or exact.domain_ref is not None
                or exact.target_refs
                or exact.match_refs
                or exact.assessment_refs
                or exact.fact_refs
                or exact.failure is not None
                or exact.semantic_input_receipt_ref is not None
            ):
                raise CoverageCompositionError("Coverage NOT_APPLICABLE accounting is not empty")
            return

        if exact.domain_ref is None:
            raise CoverageCompositionError("applicable Coverage accounting lacks complete domain")
        domain = pw.validation_intermediate_runtime.resolve_typed(
            ValidationIntermediateRefSchema.model_validate(exact.domain_ref),
            SharedRequirementArtifactCandidateDomainSchema,
        )
        expected_units = {item["unit_ref"] for item in material["artifact_units"]}
        if (
            domain.artifact_state_ref.model_dump(mode="json")
            != material["artifact_state_ref"]
            or set(domain.complete_applicable_domain_refs) != expected_units
            or set(domain.prioritized_candidate_refs) != expected_units
            or set(domain.consumer_requirements) != set(expected_ids)
            or domain.completeness_state
            is not RequirementDomainCompletenessState.COMPLETE
        ):
            raise CoverageCompositionError("Coverage committed complete domain differs")

        targets = [
            pw.validation_intermediate_runtime.resolve_typed(
                ValidationIntermediateRefSchema.model_validate(raw),
                CoverageRequirementTargetSchema,
            )
            for raw in exact.target_refs
        ]
        if {item.requirement_id for item in targets} != set(expected_ids):
            raise CoverageCompositionError("Coverage committed target set is incomplete")
        target_by_id = {item.requirement_id: item for item in targets}
        match_sets = [
            pw.validation_intermediate_runtime.resolve_typed(
                ValidationIntermediateRefSchema.model_validate(raw),
                ArtifactCoverageMatchTargetSetRefSchema,
            )
            for raw in exact.match_refs
        ]
        match_by_target = {item.coverage_target_ref: item for item in match_sets}
        if len(match_by_target) != len(match_sets):
            raise CoverageCompositionError("Coverage committed match target is duplicated")
        assessments = [
            pw.validation_intermediate_runtime.resolve_typed(
                ValidationIntermediateRefSchema.model_validate(raw),
                RequirementArtifactAssessmentSchema,
            )
            for raw in exact.assessment_refs
        ]
        assessment_by_target = {
            item.coverage_target_ref: item for item in assessments
        }
        if len(assessment_by_target) != len(assessments):
            raise CoverageCompositionError("Coverage committed assessment target is duplicated")
        facts = [
            pw.validation_store.resolve_fact(ValidationFactRefSchema.model_validate(raw))
            for raw in exact.fact_refs
        ]
        fact_by_target = {item.target_ref: item for item in facts}
        if len(fact_by_target) != len(facts):
            raise CoverageCompositionError("Coverage committed Fact target is duplicated")

        semantic_ids = {
            requirement_id
            for requirement_id, item in applicable.items()
            if item["requirement_kind"] == "FIELD_OR_SLOT_PRESENCE"
        }
        structural_ids = set(expected_ids) - semantic_ids
        if exact.failure is not None:
            if exact.state != "TECHNICAL_FAILURE" or exact.semantic_input_receipt_ref is None:
                raise CoverageCompositionError("Coverage technical accounting lacks exact failure input")
            receipt = self.store.get_json(exact.semantic_input_receipt_ref, **identity)
            result = self.store.get_json(
                exact.semantic_input_receipt_ref.removesuffix("/input") + "/result",
                **identity,
            )
            semantic_material = receipt.get("input_material")
            if (
                receipt.get("input_hash") != canonical_sha256(semantic_material)
                or result.get("input_hash") != receipt.get("input_hash")
                or result.get("outcome") != "failed"
                or result.get("failure") != exact.failure
            ):
                raise CoverageCompositionError("Coverage technical failure receipt differs")
            if any(
                target_by_id[item].coverage_target_ref in fact_by_target
                or target_by_id[item].coverage_target_ref in match_by_target
                or target_by_id[item].coverage_target_ref in assessment_by_target
                for item in semantic_ids
            ):
                raise CoverageCompositionError("Coverage technical failure fabricated semantic field output")
        elif semantic_ids:
            if exact.semantic_input_receipt_ref is None:
                raise CoverageCompositionError("semantic Coverage lacks exact model intent")
            receipt = self.store.get_json(exact.semantic_input_receipt_ref, **identity)
            result = self.store.get_json(
                exact.semantic_input_receipt_ref.removesuffix("/input") + "/result",
                **identity,
            )
            semantic_material = receipt.get("input_material")
            if (
                receipt.get("input_hash") != canonical_sha256(semantic_material)
                or result.get("input_hash") != receipt.get("input_hash")
                or result.get("outcome") != "completed"
            ):
                raise CoverageCompositionError("semantic Coverage receipt differs")
            rows = self._parse_semantic_reply(result.get("candidate"), semantic_material)
            row_by_id = {item.requirement_id: item for item in rows}
            if set(row_by_id) != semantic_ids:
                raise CoverageCompositionError("semantic Coverage result set differs")
            for requirement_id in semantic_ids:
                target = target_by_id[requirement_id]
                match = match_by_target.get(target.coverage_target_ref)
                assessment = assessment_by_target.get(target.coverage_target_ref)
                if match is None or assessment is None:
                    raise CoverageCompositionError("semantic Coverage committed chain is incomplete")
                row = row_by_id[requirement_id]
                if (
                    set(match.matched_unit_refs) != set(row.matched_unit_refs)
                    or not match.complete_domain
                    or assessment.semantic_presence_observation is not row.observation
                    or set(assessment.presence_refs) != set(row.matched_unit_refs)
                    or assessment.coverage_projection_ref != match.match_target_set_ref
                ):
                    raise CoverageCompositionError("semantic Coverage assessment differs from model result")
                relation = CoverageSemanticEvaluator.evaluate(
                    target=target,
                    match_target_set=match,
                    assessment=assessment,
                )
                projected = CoverageProjector.project(
                    validation_run_id=material["validation_run_id"],
                    subject_hash=material["subject_hash"],
                    target=target,
                    match_target_set=match,
                    relation=relation,
                    observed_count=len(row.matched_unit_refs),
                    observation_codes=row.reason_codes,
                    related_refs=(assessment.assessment_ref,),
                )
                if fact_by_target.get(target.coverage_target_ref) != projected:
                    raise CoverageCompositionError("semantic Coverage Fact differs from exact assessment")
        elif exact.semantic_input_receipt_ref is not None:
            raise CoverageCompositionError("structural-only Coverage unexpectedly has model receipt")

        citation_occurrences = material["citation_occurrences"]
        for requirement_id in structural_ids:
            requirement = applicable[requirement_id]
            target = target_by_id[requirement_id]
            match = match_by_target.get(target.coverage_target_ref)
            if match is None:
                raise CoverageCompositionError("structural Coverage match set is missing")
            # Recompute from frozen typed Requirement payload rather than any Fact.
            matched = self._structural_matches(
                requirement,
                material["artifact_units"],
                citation_occurrences,
            )
            if set(match.matched_unit_refs) != set(matched) or not match.complete_domain:
                raise CoverageCompositionError("structural Coverage match differs from exact Artifact")
            relation = CoverageStructuralEvaluator.evaluate(
                target=target,
                match_target_set=match,
            )
            projected = CoverageProjector.project(
                validation_run_id=material["validation_run_id"],
                subject_hash=material["subject_hash"],
                target=target,
                match_target_set=match,
                relation=relation,
                observed_count=len(matched),
                observation_codes=(
                    "EXACT_STRUCTURAL_PRESENCE_ACCOUNTING"
                    if requirement["requirement_kind"] == "STRUCTURAL_PART_PRESENCE"
                    else "EXACT_CITATION_OCCURRENCE_PRESENCE_ACCOUNTING",
                ),
                related_refs=(
                    "coverage-mainline-input://sha256/" + canonical_sha256(material),
                    material["requirement_ref"]["requirement_snapshot_id"],
                ),
            )
            if fact_by_target.get(target.coverage_target_ref) != projected:
                raise CoverageCompositionError("structural Coverage Fact differs from exact Artifact")

        if exact.failure is None:
            if set(fact_by_target) != {
                target_by_id[item].coverage_target_ref for item in expected_ids
            }:
                raise CoverageCompositionError("Coverage positive expected-set Fact coverage is incomplete")
            states = {item.semantic_state.value for item in facts}
            expected_state = (
                "FAIL"
                if "FAIL" in states
                else "INDETERMINATE"
                if "INDETERMINATE" in states
                else "PASS"
            )
            if exact.state != expected_state:
                raise CoverageCompositionError("Coverage completion state differs from exact Facts")


__all__ = [
    "COVERAGE_SEMANTIC_CONTRACT",
    "CoverageCompositionError",
    "TargetPostWriteCoverageComposition",
]
