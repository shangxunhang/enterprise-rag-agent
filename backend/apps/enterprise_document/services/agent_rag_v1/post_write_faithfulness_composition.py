"""349 production FTH-03 projection over the shared support-call certainty receipt.

This composition does not evaluate Claim/Evidence support and never dispatches a
model call.  It validates the exact current Claim/Support/Evidence lineage, reads
the immutable v2 physical certainty observation produced by the existing shared
support call, and projects a new ValidationRun-bound FAITHFULNESS Fact.
"""
from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict

from apps.enterprise_document.schemas.agent_rag_v1.claims import (
    ClaimCoverageAccountingState,
)
from apps.enterprise_document.schemas.agent_rag_v1.claim_support import SupportRelation
from apps.enterprise_document.schemas.agent_rag_v1.verification import (
    ClaimBindingRelation,
    VerificationVerdict,
)
from apps.enterprise_document.schemas.post_write.foundation import (
    ExpectedItemExecutionAccountingSchema,
    ExpectedItemExecutionState,
    StageDescriptorSchema,
    StagePlanSchema,
    ValidationCompletionItemSchema,
    ValidationFactRefSchema,
    ValidationIntermediateKind,
    ValidationIntermediateRefSchema,
    ValidationRunState,
    ValidatorExecutionRecordSchema,
)
from apps.enterprise_document.schemas.post_write.quality import (
    FaithfulnessCertaintyRelation,
    UpstreamSIF07ClaimAuthorityResolutionSchema,
    UpstreamSIF07ClaimAuthorityState,
)
from apps.enterprise_document.schemas.post_write.requirement import RequirementRefSchema
from apps.enterprise_document.services.post_write.execution import ValidatorInputEnvelopeBuilder
from apps.enterprise_document.services.post_write.faithfulness import (
    FaithfulnessCertaintyProjector,
)
from apps.enterprise_document.services.post_write.shared_claim_support import (
    ArtifactClaimTargetMaterializer,
)
from .post_write_requirement_sources import normalize_requirement_entries
from core.hashing import canonical_sha256
from core.retry_policy import classify_failure
from core.runtime.execution_control import checkpoint_current_execution
from schemas.failure import FailureCategory, FailureOrigin


class FaithfulnessFTH03CompositionError(ValueError):
    """Exact FTH-03 source/accounting/replay integrity failure."""


class FaithfulnessFTH03Accounting(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    schema_version: Literal["faithfulness_fth03_accounting_v1"] = (
        "faithfulness_fth03_accounting_v1"
    )
    input_hash: str
    artifact_state_ref: dict[str, Any]
    subject_hash: str
    source_snapshot_hash: str
    requirement_ref: dict[str, Any]
    coverage_states: tuple[str, ...]
    claim_target_refs: tuple[dict[str, Any], ...]
    certainty_receipt_refs: tuple[str, ...]
    fact_refs: tuple[dict[str, Any], ...]
    state: Literal[
        "PASS",
        "FAIL",
        "INDETERMINATE",
        "NOT_APPLICABLE",
        "TECHNICAL_FAILURE",
    ]
    failure: dict[str, Any] | None = None


class TargetPostWriteFaithfulnessFTH03:
    """Complete the original required ``faithfulness:item:*`` FTH-03 item."""

    CLAIM_PROJECTION_CONTRACT_VERSION = "faithfulness-certainty-projection-v1"
    SOURCE_APPLICABILITY_CONTRACT_VERSION = "faithfulness-fth03-source-applicability-v1"
    REQUIREMENT_DELEGATION_CONTRACT_VERSION = "faithfulness-fth03-r1-requirement-delegation-v1"
    VALIDATOR_ID = FaithfulnessCertaintyProjector.VALIDATOR_ID
    VERSION = FaithfulnessCertaintyProjector.VALIDATOR_VERSION

    def __init__(self, *, post_write, claim_projection, support_composition) -> None:
        if claim_projection.support is not support_composition:
            raise FaithfulnessFTH03CompositionError(
                "FTH-03 must share the canonical ClaimSupport composition"
            )
        self.post_write = post_write
        self.claim_projection = claim_projection
        self.support = support_composition

    @classmethod
    def stage_plan(cls) -> StagePlanSchema:
        stage = StageDescriptorSchema(
            stage_ref="post-write-faithfulness-fth03-stage:v2",
            stage_version="v2",
            sequence_index=0,
            stage_input_schema_ref="schema://post-write/fth03-current-sources/v1",
            stage_output_schema_ref="schema://post-write/faithfulness-certainty-fact/v1",
            execution_policy_ref="policy://post-write/faithfulness-fth03-source-aware-receipt-projection/v2",
            execution_policy_version="v2",
            timeout_seconds=10.0,
            max_attempts=1,
            retryable_failure_categories=(),
        )
        base = {
            "schema_version": "validator_stage_plan_v1",
            "stage_plan_ref": "stage-plan:post-write-faithfulness-fth03:v2",
            "validator_id": cls.VALIDATOR_ID,
            "validator_version": cls.VERSION,
            "stage_plan_version": "v2",
            "stages": [stage.model_dump(mode="json")],
        }
        return StagePlanSchema(
            **base,
            stage_plan_hash=canonical_sha256(base),
        )

    def _snapshot(
        self,
        *,
        validation_run_id,
        subject_hash,
        artifact_ref,
        scope,
        source_sections,
        assembly_input_manifest_ref,
    ):
        return self.claim_projection.resolve_sources(
            post_write=self.post_write,
            artifact_ref=artifact_ref,
            scope=scope,
            source_sections=source_sections,
            assembly_input_manifest_ref=assembly_input_manifest_ref,
            citation_validation_run_id=validation_run_id,
            citation_subject_hash=subject_hash,
        )

    def _assert_committed_snapshot_current(
        self,
        *,
        snapshot,
        validation_run_id,
        subject_hash,
        artifact_ref,
        scope,
        source_sections,
    ) -> None:
        exact = self.claim_projection.resolve_committed_source_snapshot(
            post_write=self.post_write,
            validation_run_id=validation_run_id,
            subject_hash=subject_hash,
            artifact_ref=artifact_ref,
            scope=scope,
        )
        if exact != snapshot:
            raise FaithfulnessFTH03CompositionError(
                "shared-Faithfulness source snapshot changed before FTH-03 commit"
            )
        source_by_id = {item.section.section_id: item for item in source_sections}
        snapshot_by_id = {item.section_id: item for item in snapshot.sections}
        if set(source_by_id) != set(snapshot_by_id):
            raise FaithfulnessFTH03CompositionError(
                "FTH-03 Section source set differs from committed shared snapshot"
            )
        lifecycle = self.claim_projection.claim_runtime.lifecycle_service
        for section_id, frozen in snapshot_by_id.items():
            source = source_by_id[section_id]
            lineage = source.active_execution_lineage
            if lineage is None:
                raise FaithfulnessFTH03CompositionError(
                    "FTH-03 Section source lacks active execution lineage"
                )
            current = lifecycle.resolve_active_lineage(
                lineage.run_id,
                lineage.attempt_scope_ref,
            )
            if (
                current.attempt_id != lineage.attempt_id
                or current.input_snapshot_version != lineage.input_snapshot_version
                or current.attempt_lifecycle_revision != lineage.attempt_lifecycle_revision
                or current.attempt_status.value != "running"
                or current.run_status.value not in {"running", "success"}
            ):
                raise FaithfulnessFTH03CompositionError(
                    "FTH-03 active attempt changed from committed shared snapshot"
                )
            if (
                source.section.section_title != frozen.section_title
                or canonical_sha256(source.section.content) != frozen.body_sha256
                or source.writer_execution_ref != frozen.writer_execution_ref
                or source.writer_generation_invocation_ref != frozen.writer_invocation_ref
                or source.section_paragraph_manifest != frozen.paragraph_manifest
            ):
                raise FaithfulnessFTH03CompositionError(
                    "FTH-03 Section body/Writer/Paragraph identity changed"
                )

    @staticmethod
    def _coverage_states(snapshot) -> tuple[str, ...]:
        return tuple(
            sorted(
                {
                    record.accounting_state.value
                    for section in snapshot.sections
                    for record in section.coverage_records
                }
            )
        )

    @staticmethod
    def _targets(snapshot, *, subject_hash):
        targets = []
        supports = {}
        claims = {}
        for section in snapshot.sections:
            if not section.claim_records:
                continue
            authority = UpstreamSIF07ClaimAuthorityResolutionSchema(
                state=UpstreamSIF07ClaimAuthorityState.PRESENT,
                claim_refs=tuple(record.claim_ref for record in section.claim_records),
                resolution_reason_ref=(
                    "faithfulness-fth03-source://sha256/"
                    + canonical_sha256(section.model_dump(mode="json"))
                ),
            )
            materialized = ArtifactClaimTargetMaterializer.materialize(
                authority_resolution=authority,
                artifact_state_ref=snapshot.artifact_state_ref,
                validation_subject_hash=subject_hash,
                semantic_version=TargetPostWriteFaithfulnessFTH03.CLAIM_PROJECTION_CONTRACT_VERSION,
                resolved_claim_records={
                    record.claim_ref.claim_id: record for record in section.claim_records
                },
            )
            section_supports = {
                record.claim_support_subject_ref.subject_ref: record
                for record in section.support_records
            }
            section_claims = {
                record.claim_ref.claim_id: record for record in section.claim_records
            }
            if set(section_supports) != set(section_claims):
                raise FaithfulnessFTH03CompositionError(
                    "FTH-03 SupportRecord set differs from the complete Claim set"
                )
            for target in materialized:
                if target.source_claim_ref is None:
                    raise FaithfulnessFTH03CompositionError(
                        "FTH-03 production path requires canonical SIF-07 Claim targets"
                    )
                claim_id = target.source_claim_ref.claim_id
                targets.append(target)
                supports[target.claim_target_id] = section_supports[claim_id]
                claims[target.claim_target_id] = section_claims[claim_id]
        return tuple(sorted(targets, key=lambda item: item.claim_target_id)), supports, claims

    def _material(
        self,
        *,
        snapshot,
        source_hash,
        validation_run_id,
        subject_hash,
        artifact_ref,
        scope,
        target_ref,
        requirement_ref,
        claim_targets,
    ):
        return {
            "contract": "faithfulness-fth03-mainline-v2",
            "source_applicability_contract": self.SOURCE_APPLICABILITY_CONTRACT_VERSION,
            "requirement_delegation_contract": self.REQUIREMENT_DELEGATION_CONTRACT_VERSION,
            "validation_run_id": validation_run_id,
            "subject_hash": subject_hash,
            "artifact_state_ref": artifact_ref.model_dump(mode="json"),
            "validation_scope": scope.value,
            "target_ref": target_ref,
            "source_snapshot_hash": source_hash,
            "requirement_ref": requirement_ref.model_dump(mode="json"),
            "claim_targets": [item.model_dump(mode="json") for item in claim_targets],
            "coverage_states": list(self._coverage_states(snapshot)),
        }

    def _requirements_permit_source_exclusion(self, requirement, project, run_id, task_id):
        """R1 delegates complete TASK_INTENT to required Adherence, not to FTH03."""
        if (project.task_id != task_id
                or requirement.source_business_ref != f"project-input://{task_id}"
                or requirement.source_task_ref != f"task://{task_id}/run/{run_id}"):
            return False
        expected = {item.requirement_id: item for item in normalize_requirement_entries(project)}
        procedure = self.post_write.semantic_quality_config.requirement_entry()
        expected[procedure.requirement_id] = procedure
        actual = {item.requirement_id: item for item in requirement.requirements}
        if any(item.mandatory and actual.get(key) != item for key, item in expected.items()):
            return False
        delegated_kinds = {"TASK_INTENT", "STRUCTURAL_PART_PRESENCE",
                           "FIELD_OR_SLOT_PRESENCE", "CITATION_PRESENCE"}
        return all(not item.mandatory or (
            expected.get(item.requirement_id) == item
            and (item.requirement_kind in delegated_kinds or item == procedure)
        ) for item in requirement.requirements)

    def _current_source_records(self, snapshot, source_sections):
        runtime = self.claim_projection.claim_runtime
        sources = {item.section.section_id: item for item in source_sections}
        records = {}
        for section in snapshot.sections:
            source = sources[section.section_id]
            lineage = source.active_execution_lineage
            claims = {item.claim_ref.claim_id: item for item in section.claim_records}
            bindings = {item.claim_ref.claim_id: item for item in section.binding_records}
            results = {item.claim_ref.claim_id: item for item in section.verification_records}
            if (len(claims) != len(section.claim_records)
                    or len(bindings) != len(section.binding_records)
                    or len(results) != len(section.verification_records)
                    or set(claims) != set(bindings) or set(claims) != set(results)):
                raise FaithfulnessFTH03CompositionError("FTH-03 complete Claim/Binding/Result sets differ")
            current = runtime.resolve_current_generation_sources(
                run_id=lineage.run_id, section_id=section.section_id,
                paragraph_version_refs=section.paragraph_manifest.members)
            if ({row[0].claim_ref.claim_id: row[0] for row in current} != claims
                    or {row[3].claim_ref.claim_id: row[3] for row in current} != results):
                raise FaithfulnessFTH03CompositionError("FTH-03 snapshot differs from current Claim/head/Result set")
            for claim, head, revision, result in current:
                binding = bindings[claim.claim_ref.claim_id]
                if (runtime.resolve_claim_head(claim.claim_ref) != head
                        or head.head_updated_by_operation_id != result.committed_operation_id
                        or head.active_verification_revision_ref != revision.revision_ref
                        or revision.verification_result_ref != result.verification_result_ref
                        or binding.binding_facts_ref != result.binding_facts_ref
                        or binding.claim_ref != claim.claim_ref
                        or binding.paragraph_version_ref != claim.paragraph_version_ref
                        or result.paragraph_version_ref != claim.paragraph_version_ref):
                    raise FaithfulnessFTH03CompositionError("FTH-03 exact current source identity differs")
                records[claim.claim_ref.claim_id] = (binding, result)
        return records

    def _pi_fact_source_exclusion(self, *, claim, binding, result, support,
                                 lineage, requirement, task_id):
        """Consume the approved M6 whole-Claim witness; never infer source roles."""
        if result.verdict is not VerificationVerdict.VERIFIED or not result.positive_basis_refs:
            return False
        if (result.schema_version != "verification_result_record_v2"
                or result.claim_ref != claim.claim_ref
                or result.claim_evidence_support_ref != support.support_ref
                or result.binding_source_set_hash != binding.source_set_hash
                or any(getattr(result.active_lineage, key) != getattr(lineage, key)
                       for key in ("run_id", "attempt_id", "attempt_scope_ref", "input_snapshot_version"))):
            raise FaithfulnessFTH03CompositionError("FTH-03 M6 witness belongs to another exact source")
        kinds = {}
        for source in binding.exact_authorized_source_refs:
            kinds.setdefault(source.exact_source_ref, set()).add(source.source_kind)
        if any((ref in kinds if ref == result.project_input_basis_ref else kinds.get(ref) != {"FACT"})
               for ref in result.positive_basis_refs):
            return False
        if (support.relation in {SupportRelation.CONTRADICTED, SupportRelation.INDETERMINATE}
                or any(fact.relation is ClaimBindingRelation.CONTRADICTS for fact in binding.facts)):
            return False
        runtime = self.claim_projection.claim_runtime
        view = runtime.resolve_active_verified_claim(claim.claim_ref, strict_currentness=True)
        head = runtime.resolve_claim_head(claim.claim_ref)
        if (not view.active_verified_claim or head is None
                or head.head_updated_by_operation_id != result.committed_operation_id):
            raise FaithfulnessFTH03CompositionError("FTH-03 PI/FACT witness is not currently active")
        verifier = self.claim_projection.verifier
        basis, project = verifier.resolve_project_input_basis(claim, lineage)
        if (basis.project_input_basis_ref != result.project_input_basis_ref
                or basis.project_input_sha256 != result.project_input_sha256
                or basis.writer_generation_invocation_ref != result.writer_generation_invocation_ref
                or project.task_id != task_id):
            raise FaithfulnessFTH03CompositionError("FTH-03 exact Writer ProjectInput witness differs")
        if not verifier._strict_decision_from_project(project):
            return False
        profile = verifier.prompt_profile_sha256(strict_decision=True,
            semantic_v4=verifier._semantic_v4_from_project(project))
        if result.deterministic_validation_facts.get("verifier_prompt_profile_sha256") != profile:
            return False
        return self._requirements_permit_source_exclusion(requirement, project, lineage.run_id, task_id)

    def _evaluate_current(
        self,
        *,
        snapshot,
        source_hash,
        validation_run_id,
        subject_hash,
        artifact_ref,
        scope,
        target_ref,
        requirement_ref,
        source_sections,
        claim_targets,
        supports,
        claims,
    ):
        requirement = self.post_write.requirement_store.resolve_exact(requirement_ref)
        if requirement.requirement_hash != requirement_ref.requirement_hash:
            raise FaithfulnessFTH03CompositionError(
                "FTH-03 Requirement snapshot hash differs from exact RequirementRef"
            )
        # Current production has no typed FTH-03-specific Requirement selector.
        # The complete canonical Requirement snapshot is still bound to every Fact;
        # free-text policy Requirements are never keyword-inferred into applicability.
        applicable_requirement_ids: tuple[str, ...] = ()

        source_by_section = {
            item.section.section_id: item for item in source_sections
        }
        coverage_states = self._coverage_states(snapshot)
        coverage_indeterminate = (
            ClaimCoverageAccountingState.INDETERMINATE.value in coverage_states
        )
        facts = []
        receipt_refs = []
        observations = {}
        failure = None
        task_id = None
        if claim_targets:
            first_lineage = next(
                (
                    item.active_execution_lineage
                    for item in source_sections
                    if item.active_execution_lineage is not None
                ),
                None,
            )
            if first_lineage is None:
                raise FaithfulnessFTH03CompositionError(
                    "FTH-03 requires exact Run identity for current Claim targets"
                )
            task_id = self.claim_projection.claim_runtime.lifecycle_service.load_run_identity(
                first_lineage.run_id
            ).task_id

        try:
            for target in claim_targets:
                claim = claims[target.claim_target_id]
                source = source_by_section.get(claim.section_id)
                if source is None or source.active_execution_lineage is None:
                    raise FaithfulnessFTH03CompositionError(
                        "FTH-03 Claim has no exact current Section lineage"
                    )
                observation = self.support.resolve_certainty_observation_from_validated_snapshot(
                    support_record=supports[target.claim_target_id],
                    claim_record=claim,
                    active_lineage=source.active_execution_lineage,
                    task_id=task_id,
                )
                if observation is None:
                    failure = {
                        "schema_version": "faithfulness_fth03_failure_v1",
                        "kind": "MISSING_CURRENT_CERTAINTY_RECEIPT",
                        "error_type": "FaithfulnessFTH03MissingReceipt",
                        "error_code": "FTH03_CURRENT_CERTAINTY_RECEIPT_REQUIRED",
                        "message": (
                            "canonical Support is readable but has no current v2 FTH-03 certainty receipt; "
                            "automatic semantic redispatch is forbidden"
                        ),
                        "category": FailureCategory.VALIDATION.value,
                        "origin": FailureOrigin.UNKNOWN.value,
                    }
                    break
                receipt_refs.append(observation.physical_support_receipt_ref)
                observations[target.claim_target_id] = observation
            if failure is None:
                records = self._current_source_records(snapshot, source_sections)
                for target in claim_targets:
                    claim = claims[target.claim_target_id]
                    binding, result = records[claim.claim_ref.claim_id]
                    source = source_by_section[claim.section_id]
                    if self._pi_fact_source_exclusion(claim=claim, binding=binding, result=result,
                            support=supports[target.claim_target_id], lineage=source.active_execution_lineage,
                            requirement=requirement, task_id=task_id):
                        continue
                    observation = observations[target.claim_target_id]
                    facts.append(
                        FaithfulnessCertaintyProjector.project(
                            validation_run_id=validation_run_id,
                            subject_hash=subject_hash,
                            claim_target=target,
                            support_record=supports[target.claim_target_id],
                            requirement_ref=requirement_ref,
                            applicable_requirement_ids=applicable_requirement_ids,
                            certainty_relation=FaithfulnessCertaintyRelation(
                                observation.certainty_relation
                            ),
                            certainty_reason_codes=observation.reason_codes,
                            physical_support_receipt_ref=(
                                observation.physical_support_receipt_ref
                            ),
                            related_refs=(source_hash,),
                        )
                    )
        except Exception as exc:
            failure = {
                "schema_version": "faithfulness_fth03_failure_v1",
                "kind": "FTH03_SOURCE_OR_RECEIPT_TECHNICAL_FAILURE",
                "error_type": type(exc).__name__,
                "error_code": getattr(exc, "error_code", type(exc).__name__),
                "message": str(exc) or type(exc).__name__,
                "category": classify_failure(exc).value,
                "origin": FailureOrigin(
                    getattr(exc, "failure_origin", FailureOrigin.UNKNOWN)
                ).value,
            }

        if failure is not None:
            facts = []
            receipt_refs = []
            state = "TECHNICAL_FAILURE"
        else:
            semantic_states = {item.semantic_state.value for item in facts}
            if "FAIL" in semantic_states:
                state = "FAIL"
            elif coverage_indeterminate or "INDETERMINATE" in semantic_states:
                state = "INDETERMINATE"
            elif not claim_targets:
                state = "NOT_APPLICABLE"
            else:
                state = "PASS"
        return tuple(facts), tuple(sorted(set(receipt_refs))), state, failure

    def complete_item(
        self,
        *,
        validation_run_id,
        subject_hash,
        artifact_ref,
        scope,
        target_ref,
        requirement_ref,
        source_sections,
        assembly_input_manifest_ref=None,
    ) -> ValidationCompletionItemSchema:
        pw = self.post_write
        checkpoint_current_execution()
        exact_requirement_ref = RequirementRefSchema.model_validate(requirement_ref)
        snapshot = self.claim_projection.resolve_committed_source_snapshot(
            post_write=pw,
            validation_run_id=validation_run_id,
            subject_hash=subject_hash,
            artifact_ref=artifact_ref,
            scope=scope,
        )
        self._assert_committed_snapshot_current(
            snapshot=snapshot,
            validation_run_id=validation_run_id,
            subject_hash=subject_hash,
            artifact_ref=artifact_ref,
            scope=scope,
            source_sections=source_sections,
        )
        source_hash = canonical_sha256(snapshot.model_dump(mode="json"))
        claim_targets, supports, claims = self._targets(
            snapshot,
            subject_hash=subject_hash,
        )
        material = self._material(
            snapshot=snapshot,
            source_hash=source_hash,
            validation_run_id=validation_run_id,
            subject_hash=subject_hash,
            artifact_ref=artifact_ref,
            scope=scope,
            target_ref=target_ref,
            requirement_ref=exact_requirement_ref,
            claim_targets=claim_targets,
        )
        item_ref = "faithfulness:item:" + artifact_ref.artifact_hash
        plan = self.stage_plan()
        provenance = (
            "post-write-claim-source://sha256/" + source_hash,
            "post-write-requirement://sha256/" + exact_requirement_ref.requirement_hash,
        )
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
        execution_ref = (
            "validator-execution:faithfulness-fth03:"
            + canonical_sha256({"input_hash": envelope.input_hash, "item_ref": item_ref})
        )
        record = pw.validation_store.find_validator_execution_for_item(
            validation_run_id=validation_run_id,
            validator_id=self.VALIDATOR_ID,
            expected_item_ref=item_ref,
        )
        current_facts, current_receipts, current_state, current_failure = (
            self._evaluate_current(
                snapshot=snapshot,
                source_hash=source_hash,
                validation_run_id=validation_run_id,
                subject_hash=subject_hash,
                artifact_ref=artifact_ref,
                scope=scope,
                target_ref=target_ref,
                requirement_ref=exact_requirement_ref,
                source_sections=source_sections,
                claim_targets=claim_targets,
                supports=supports,
                claims=claims,
            )
        )
        input_hash = canonical_sha256(material)

        if record is not None:
            if (
                record.validator_execution_ref != execution_ref
                or record.input_hash != envelope.input_hash
                or record.validator_version != self.VERSION
                or record.validation_run_id != validation_run_id
                or record.subject_hash != subject_hash
                or record.validator_id != self.VALIDATOR_ID
                or record.stage_plan_ref != plan.stage_plan_ref
                or record.stage_plan_version != plan.stage_plan_version
                or len(record.intermediate_refs) != 1
                or len(record.expected_item_accounting) != 1
                or record.terminal_execution_state
                != ("TECHNICAL_FAILURE" if current_failure is not None else "COMPLETED")
            ):
                raise FaithfulnessFTH03CompositionError(
                    "committed FTH-03 execution identity/version differs"
                )
            accounting_ref = record.intermediate_refs[-1]
            accounting = pw.validation_intermediate_runtime.resolve_typed(
                accounting_ref,
                FaithfulnessFTH03Accounting,
            )
            if (
                accounting.input_hash != input_hash
                or accounting.source_snapshot_hash != source_hash
                or accounting.artifact_state_ref != artifact_ref.model_dump(mode="json")
                or accounting.subject_hash != subject_hash
                or accounting.requirement_ref
                != exact_requirement_ref.model_dump(mode="json")
                or accounting.coverage_states != self._coverage_states(snapshot)
                or accounting.claim_target_refs
                != tuple(item.model_dump(mode="json") for item in claim_targets)
                or accounting.certainty_receipt_refs != current_receipts
                or accounting.state != current_state
                or accounting.failure != current_failure
            ):
                raise FaithfulnessFTH03CompositionError(
                    "committed FTH-03 accounting differs from exact current sources"
                )
            fact_refs = tuple(
                ValidationFactRefSchema.model_validate(item)
                for item in accounting.fact_refs
            )
            if tuple(pw.validation_store.resolve_fact(ref) for ref in fact_refs) != current_facts:
                raise FaithfulnessFTH03CompositionError(
                    "committed FTH-03 Facts differ from exact certainty projection"
                )
            expected_proofs = (
                (accounting_ref,)
                if current_state == "INDETERMINATE" and not fact_refs
                else ()
            )
            positive_proofs = (
                (accounting_ref,)
                if current_state == "PASS" and len(fact_refs) < len(claim_targets)
                else ()
            )
            exact_accounting = record.expected_item_accounting[0]
            if (
                exact_accounting.fact_refs != fact_refs
                or exact_accounting.expected_validation_item_ref != item_ref
                or exact_accounting.execution_state.value != record.terminal_execution_state
                or exact_accounting.positive_proof_refs != positive_proofs
                or exact_accounting.indeterminate_proof_refs != expected_proofs
                or exact_accounting.technical_failure_ref != current_failure
            ):
                raise FaithfulnessFTH03CompositionError(
                    "committed FTH-03 execution accounting differs"
                )
            return ValidationCompletionItemSchema(
                expected_validation_item_ref=item_ref,
                required=True,
                state=current_state,
                fact_refs=fact_refs,
                positive_proof_refs=positive_proofs,
                indeterminate_proof_refs=expected_proofs,
                technical_failure_ref=current_failure,
            )

        run = pw.validation_store.resolve_run_exact(validation_run_id)
        if run.execution_state is ValidationRunState.COMPLETED:
            raise FaithfulnessFTH03CompositionError(
                "terminal ValidationRun is missing committed FTH-03 execution"
            )

        checkpoint_current_execution()
        self._assert_committed_snapshot_current(
            snapshot=snapshot,
            validation_run_id=validation_run_id,
            subject_hash=subject_hash,
            artifact_ref=artifact_ref,
            scope=scope,
            source_sections=source_sections,
        )

        fact_refs = tuple(
            pw.validation_store.put_fact_if_absent_or_same(fact)
            for fact in current_facts
        )
        accounting = FaithfulnessFTH03Accounting(
            input_hash=input_hash,
            artifact_state_ref=artifact_ref.model_dump(mode="json"),
            subject_hash=subject_hash,
            source_snapshot_hash=source_hash,
            requirement_ref=exact_requirement_ref.model_dump(mode="json"),
            coverage_states=self._coverage_states(snapshot),
            claim_target_refs=tuple(
                item.model_dump(mode="json") for item in claim_targets
            ),
            certainty_receipt_refs=current_receipts,
            fact_refs=tuple(item.model_dump(mode="json") for item in fact_refs),
            state=current_state,
            failure=current_failure,
        )
        accounting_ref = pw.validation_intermediate_runtime.commit(
            validation_run_id=validation_run_id,
            producer_ref=self.VALIDATOR_ID,
            target_ref=item_ref,
            typed_intermediate=accounting,
            intermediate_kind=ValidationIntermediateKind.TARGET_ACCOUNTING,
            occurred_at_epoch=0.0,
        )
        positive_proofs = (
            (accounting_ref,)
            if current_state == "PASS" and len(fact_refs) < len(claim_targets)
            else ()
        )
        indeterminate_proofs = (
            (accounting_ref,)
            if current_state == "INDETERMINATE" and not fact_refs
            else ()
        )
        with pw.database.read_transaction() as connection:
            sequence = pw.validation_store._next_event_sequence(
                connection,
                validation_run_id,
            )
        event = pw.validation_store._event(
            validation_run_id=validation_run_id,
            event_id=(
                "validator-terminal:faithfulness-fth03:"
                + canonical_sha256(execution_ref)
            ),
            sequence=sequence,
            event_kind=(
                "VALIDATOR_TECHNICAL_FAILURE"
                if current_failure is not None
                else "VALIDATOR_COMPLETED"
            ),
            payload={"validator_execution_ref": execution_ref},
            occurred_at_epoch=0.0,
        )
        execution_accounting = ExpectedItemExecutionAccountingSchema(
            expected_validation_item_ref=item_ref,
            execution_state=(
                ExpectedItemExecutionState.TECHNICAL_FAILURE
                if current_failure is not None
                else ExpectedItemExecutionState.COMPLETED
            ),
            fact_refs=fact_refs,
            positive_proof_refs=positive_proofs,
            indeterminate_proof_refs=indeterminate_proofs,
            technical_failure_ref=current_failure,
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
                "TECHNICAL_FAILURE"
                if current_failure is not None
                else "COMPLETED"
            ),
            "expected_item_accounting": [
                execution_accounting.model_dump(mode="json")
            ],
            "produced_fact_refs": [
                item.model_dump(mode="json") for item in fact_refs
            ],
            "intermediate_refs": [accounting_ref.model_dump(mode="json")],
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
            state=current_state,
            fact_refs=fact_refs,
            positive_proof_refs=positive_proofs,
            indeterminate_proof_refs=indeterminate_proofs,
            technical_failure_ref=current_failure,
        )


__all__ = [
    "FaithfulnessFTH03Accounting",
    "FaithfulnessFTH03CompositionError",
    "TargetPostWriteFaithfulnessFTH03",
]
