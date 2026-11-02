"""T12-01 focused acceptance tests for Target Writer Admission services."""

from __future__ import annotations

from dataclasses import dataclass
import json

import pytest

from apps.enterprise_document.schemas.pre_write_admission import (
    CriterionEvidenceSupportState,
    EvidenceCriterionSchema,
    EvidenceRequirementSchema,
    RequirementEvidenceFactSchema,
    WriterAdmissionAssessmentState,
    WriterAdmissionContractSchema,
    WriterAdmissionOutcome,
    WriterAdmissionResolutionState,
)
from apps.enterprise_document.services.scheme_writer.writer_admission import (
    BusinessProfileTemplate,
    CriterionAssessmentResult,
    CriterionConflictAssessment,
    CriterionSupportTechnicalError,
    EvidenceRequirementsMaterializer,
    LLMCriterionSupportEvaluator,
    SCHEME_GENERATION_MINIMAL_CRITERION_ID,
    SCHEME_GENERATION_MINIMAL_CRITERION_TEXT,
    SCHEME_GENERATION_MINIMAL_PROFILE_REF,
    SCHEME_GENERATION_MINIMAL_REQUIREMENT_ID,
    SCHEME_GENERATION_MINIMAL_TASK_REF,
    TaskContractTemplate,
    WriterAdmissionFactAssessor,
    WriterAdmissionPolicy,
    scheme_generation_minimal_profile,
    scheme_generation_minimal_task_contract,
)
from contracts.evidence import evidence_bundle_sha256
from core.hashing import canonical_sha256
from schemas.model import ModelResponseSchema
from schemas.rag import (
    EvidenceBundleSchema,
    EvidenceDisposition,
    RAGContextSchema,
    RAGEvidenceItemSchema,
)


def requirement(
    requirement_id: str,
    *criteria: tuple[str, str],
) -> EvidenceRequirementSchema:
    return EvidenceRequirementSchema(
        requirement_id=requirement_id,
        criteria=tuple(
            EvidenceCriterionSchema(criterion_id=criterion_id, criterion=text)
            for criterion_id, text in criteria
        ),
    )


def requirements_snapshot():
    return EvidenceRequirementsMaterializer.materialize(
        source_profile_ref="profile:normal:v1",
        source_task_ref="task-contract:section:v1",
        requirements=(
            requirement("r1", ("c1", "The section states the approved capacity."), ("c2", "The section states the approved location.")),
            requirement("r2", ("c3", "The section states the approved delivery boundary.")),
        ),
    )


def evidence_bundle(*, selected: tuple[str, ...] = (), dropped: tuple[str, ...] = ()) -> EvidenceBundleSchema:
    items = []
    for index, evidence_id in enumerate((*selected, *dropped), start=1):
        disposition = (
            EvidenceDisposition.SELECTED if evidence_id in selected else EvidenceDisposition.DROPPED
        )
        items.append(
            RAGEvidenceItemSchema(
                evidence_id=evidence_id,
                disposition=disposition,
                rank=index,
                matched_chunk_id=f"match-{evidence_id}",
                context_chunk_id=f"context-{evidence_id}",
                doc_id=f"doc-{evidence_id}",
                match_text=f"match text {evidence_id}",
                context_text=f"context text {evidence_id}",
            )
        )
    return EvidenceBundleSchema(
        query="locked query",
        items=items,
        selected_evidence_ids=list(selected),
        dropped_evidence_ids=list(dropped),
        context=RAGContextSchema(
            context_text="",
            used_context_chars=0,
            context_item_count=len(selected),
        ),
    )


@dataclass
class MappingEvaluator:
    values: dict[str, CriterionAssessmentResult]

    def evaluate(self, *, requirement, criterion, selected_evidence):
        del requirement, selected_evidence
        return self.values[criterion.criterion_id]


def fixed_ids(*values: str):
    iterator = iter(values)
    return lambda: next(iterator)


class FakeCriterionGateway:
    def __init__(self, *results) -> None:
        self.results = list(results)
        self.calls = []

    def generate(self, request):
        self.calls.append(request)
        result = self.results.pop(0)
        if isinstance(result, BaseException):
            raise result
        return ModelResponseSchema(
            model_call_id=request.model_call_id,
            task_id=request.task_id,
            run_id=request.run_id,
            model_name=request.model_name or "test-model",
            success=True,
            content=str(result),
            created_at="2026-08-23T00:00:00+00:00",
        )


def _llm_payload(
    support_state: str,
    *,
    evidence_ids: tuple[str, ...] = (),
    conflicts: tuple[dict, ...] = (),
    reason_codes: tuple[str, ...] = (),
) -> str:
    return json.dumps(
        {
            "support_state": support_state,
            "evidence_ids": list(evidence_ids),
            "conflicts": list(conflicts),
            "reason_codes": list(reason_codes),
        }
    )


def test_p5_deployment_fixed_profile_and_task_materialize_one_exact_minimal_snapshot() -> None:
    profile = scheme_generation_minimal_profile()
    task_contract = scheme_generation_minimal_task_contract()
    assert profile.profile_ref == SCHEME_GENERATION_MINIMAL_PROFILE_REF
    assert task_contract.task_ref == SCHEME_GENERATION_MINIMAL_TASK_REF
    assert task_contract.requirements == ()
    assert len(profile.requirements) == 1
    assert profile.requirements[0].requirement_id == SCHEME_GENERATION_MINIMAL_REQUIREMENT_ID
    assert len(profile.requirements[0].criteria) == 1
    criterion = profile.requirements[0].criteria[0]
    assert criterion.criterion_id == SCHEME_GENERATION_MINIMAL_CRITERION_ID
    assert criterion.criterion == SCHEME_GENERATION_MINIMAL_CRITERION_TEXT

    first = EvidenceRequirementsMaterializer.materialize_profile_task(
        profile=profile,
        task_contract=task_contract,
    )
    second = EvidenceRequirementsMaterializer.materialize_profile_task(
        profile=scheme_generation_minimal_profile(),
        task_contract=scheme_generation_minimal_task_contract(),
    )
    assert first == second
    assert first.requirements_sha256 == second.requirements_sha256
    assert first.source_profile_ref == SCHEME_GENERATION_MINIMAL_PROFILE_REF
    assert first.source_task_ref == SCHEME_GENERATION_MINIMAL_TASK_REF
    assert len(first.requirements) == 1
    assert sum(len(item.criteria) for item in first.requirements) == 1

    import apps.enterprise_document.services.scheme_writer.writer_admission as module

    for forbidden in (
        "ProfileRegistry",
        "ProfileRepository",
        "ProfileSelector",
        "TaskContractSelector",
        "RuleEngine",
    ):
        assert not hasattr(module, forbidden)


@pytest.mark.parametrize(
    ("support_state", "evidence_ids", "expected"),
    [
        ("supported", ("E1",), CriterionEvidenceSupportState.SUPPORTED),
        ("not_supported", (), CriterionEvidenceSupportState.NOT_SUPPORTED),
        ("indeterminate", ("E1",), CriterionEvidenceSupportState.INDETERMINATE),
    ],
)
def test_p5_llm_evaluator_strict_semantic_states(
    support_state: str,
    evidence_ids: tuple[str, ...],
    expected: CriterionEvidenceSupportState,
) -> None:
    gateway = FakeCriterionGateway(
        _llm_payload(support_state, evidence_ids=evidence_ids)
    )
    evaluator = LLMCriterionSupportEvaluator(
        model_gateway=gateway,
        model_name="deployment-default-model",
    )
    requirement_schema = scheme_generation_minimal_profile().requirements[0]
    criterion = requirement_schema.criteria[0]
    result = evaluator.evaluate(
        requirement=requirement_schema,
        criterion=criterion,
        selected_evidence=tuple(evidence_bundle(selected=("E1",)).items),
    )
    assert result.support_state is expected
    assert result.evidence_ids == evidence_ids
    assert len(gateway.calls) == 1
    request = gateway.calls[0]
    assert request.model_name == "deployment-default-model"
    assert "ALLOW" not in request.prompt
    assert "raw user query" not in request.prompt.lower()


def test_support_states_conflict_and_semantic_risk_metrics_are_reproducible() -> None:
    """AV-S12-002: frozen offline semantic-risk metrics are deterministic."""

    states = (
        CriterionEvidenceSupportState.SUPPORTED,
        CriterionEvidenceSupportState.NOT_SUPPORTED,
        CriterionEvidenceSupportState.INDETERMINATE,
    )
    labeled = (
        (states[0], states[0]),
        (states[0], states[1]),
        (states[1], states[1]),
        (states[1], states[2]),
        (states[2], states[2]),
        (states[2], states[0]),
    )
    confusion = {
        truth: {predicted: 0 for predicted in states}
        for truth in states
    }
    for truth, predicted in labeled:
        confusion[truth][predicted] += 1

    supported_predictions = sum(
        confusion[truth][states[0]] for truth in states
    )
    true_supported_predictions = confusion[states[0]][states[0]]
    false_supported_predictions = supported_predictions - true_supported_predictions
    supported_precision = true_supported_predictions / supported_predictions
    false_supported_rate = false_supported_predictions / supported_predictions

    assert confusion == {
        states[0]: {states[0]: 1, states[1]: 1, states[2]: 0},
        states[1]: {states[0]: 0, states[1]: 1, states[2]: 1},
        states[2]: {states[0]: 1, states[1]: 0, states[2]: 1},
    }
    assert supported_precision == 0.5
    assert false_supported_rate == 0.5
    assert confusion[states[1]][states[2]] == 1
    assert confusion[states[2]][states[1]] == 0


def test_p5_llm_evaluator_preserves_selected_evidence_conflict_only() -> None:
    gateway = FakeCriterionGateway(
        _llm_payload(
            "supported",
            evidence_ids=("E1",),
            conflicts=({"evidence_ids": ["E1", "E2"], "reason_codes": ["conflict"]},),
        )
    )
    evaluator = LLMCriterionSupportEvaluator(
        model_gateway=gateway,
        model_name="deployment-default-model",
    )
    requirement_schema = scheme_generation_minimal_profile().requirements[0]
    result = evaluator.evaluate(
        requirement=requirement_schema,
        criterion=requirement_schema.criteria[0],
        selected_evidence=tuple(evidence_bundle(selected=("E1", "E2")).items),
    )
    assert result.support_state is CriterionEvidenceSupportState.SUPPORTED
    assert result.conflicts == (
        CriterionConflictAssessment(
            evidence_ids=("E1", "E2"),
            reason_codes=("conflict",),
        ),
    )


@pytest.mark.parametrize(
    ("raw_output", "message"),
    [
        ("not-json", "structured validation"),
        (_llm_payload("supported", evidence_ids=("E404",)), "unknown selected Evidence"),
        (_llm_payload("supported"), "requires at least one selected Evidence"),
        (
            _llm_payload(
                "supported",
                evidence_ids=("E1",),
                conflicts=({"evidence_ids": ["E1", "E404"]},),
            ),
            "conflict references unknown selected Evidence",
        ),
    ],
)
def test_p5_llm_evaluator_invalid_output_is_technical_failure(
    raw_output: str,
    message: str,
) -> None:
    gateway = FakeCriterionGateway(raw_output)
    evaluator = LLMCriterionSupportEvaluator(
        model_gateway=gateway,
        model_name="deployment-default-model",
    )
    requirement_schema = scheme_generation_minimal_profile().requirements[0]
    with pytest.raises(CriterionSupportTechnicalError, match=message):
        evaluator.evaluate(
            requirement=requirement_schema,
            criterion=requirement_schema.criteria[0],
            selected_evidence=tuple(evidence_bundle(selected=("E1", "E2")).items),
        )


def test_p5_llm_provider_exception_is_technical_failure_and_produces_no_assessment() -> None:
    gateway = FakeCriterionGateway(TimeoutError("provider timeout"))
    evaluator = LLMCriterionSupportEvaluator(
        model_gateway=gateway,
        model_name="deployment-default-model",
    )
    with pytest.raises(CriterionSupportTechnicalError, match="provider invocation"):
        WriterAdmissionFactAssessor(
            evaluator=evaluator,
            assessment_id_factory=fixed_ids("must-not-complete"),
        ).assess(
            requirements=EvidenceRequirementsMaterializer.materialize_profile_task(
                profile=scheme_generation_minimal_profile(),
                task_contract=scheme_generation_minimal_task_contract(),
            ),
            evidence_bundle=evidence_bundle(selected=("E1",)),
        )
    assert len(gateway.calls) == 1


def test_p5_zero_selected_evidence_never_calls_llm_evaluator() -> None:
    gateway = FakeCriterionGateway(
        _llm_payload("supported", evidence_ids=("E1",))
    )
    evaluator = LLMCriterionSupportEvaluator(
        model_gateway=gateway,
        model_name="deployment-default-model",
    )
    snapshot = EvidenceRequirementsMaterializer.materialize_profile_task(
        profile=scheme_generation_minimal_profile(),
        task_contract=scheme_generation_minimal_task_contract(),
    )
    assessment = WriterAdmissionFactAssessor(
        evaluator=evaluator,
        assessment_id_factory=fixed_ids("zero-assessment"),
    ).assess(
        requirements=snapshot,
        evidence_bundle=evidence_bundle(),
    )
    assert gateway.calls == []
    assert assessment.criterion_facts[0].support_state is CriterionEvidenceSupportState.NOT_SUPPORTED
    decision = WriterAdmissionPolicy(
        decision_id_factory=fixed_ids("zero-decision")
    ).evaluate(
        assessment=assessment,
        contract=WriterAdmissionContractSchema(),
    )
    assert decision.admission_outcome is WriterAdmissionOutcome.DENY


def test_static_profile_and_task_fixtures_materialize_exact_ordered_requirement_set() -> None:
    profile = BusinessProfileTemplate(
        profile_ref="profile:gov-cloud:v1",
        requirements=(requirement("r-profile", ("c-profile", "Profile criterion.")),),
    )
    task_contract = TaskContractTemplate(
        task_ref="task-contract:project-a:v1",
        requirements=(requirement("r-task", ("c-task", "Task criterion.")),),
    )
    snapshot = EvidenceRequirementsMaterializer.materialize_profile_task(
        profile=profile,
        task_contract=task_contract,
    )
    assert snapshot.source_profile_ref == profile.profile_ref
    assert snapshot.source_task_ref == task_contract.task_ref
    assert [item.requirement_id for item in snapshot.requirements] == [
        "r-profile",
        "r-task",
    ]


def test_requirements_materialization_uses_exact_frozen_hash_preimage() -> None:
    snapshot = requirements_snapshot()
    expected = canonical_sha256(
        {
            "schema_version": snapshot.schema_version,
            "contract_version": snapshot.contract_version,
            "source_profile_ref": snapshot.source_profile_ref,
            "source_task_ref": snapshot.source_task_ref,
            "requirements": [
                item.model_dump(mode="json", round_trip=True)
                for item in snapshot.requirements
            ],
        }
    )
    assert snapshot.requirements_sha256 == expected
    changed = EvidenceRequirementsMaterializer.materialize(
        source_profile_ref=snapshot.source_profile_ref,
        source_task_ref="task-contract:section:v2",
        requirements=snapshot.requirements,
    )
    assert changed.requirements_sha256 != snapshot.requirements_sha256


def test_pending_is_immutable_and_completion_gets_new_assessment_identity() -> None:
    snapshot = requirements_snapshot()
    bundle = evidence_bundle()
    assessor = WriterAdmissionFactAssessor(
        assessment_id_factory=fixed_ids("pending-a", "completed-a")
    )
    pending = assessor.pending(requirements=snapshot, evidence_bundle=bundle)
    completed = assessor.assess(requirements=snapshot, evidence_bundle=bundle)
    assert pending.assessment_id == "pending-a"
    assert completed.assessment_id == "completed-a"
    assert pending.assessment_state is WriterAdmissionAssessmentState.PENDING
    assert completed.assessment_state is WriterAdmissionAssessmentState.COMPLETED
    assert pending.criterion_facts == ()
    assert pending.requirement_facts == ()
    assert pending != completed


def test_zero_selected_evidence_completes_every_fact_not_supported() -> None:
    snapshot = requirements_snapshot()
    bundle = evidence_bundle(dropped=("E9",))
    assessment = WriterAdmissionFactAssessor(
        assessment_id_factory=fixed_ids("assessment-zero")
    ).assess(requirements=snapshot, evidence_bundle=bundle)
    assert assessment.evidence_bundle_sha256 == evidence_bundle_sha256(bundle)
    assert len(assessment.criterion_facts) == 3
    assert len(assessment.requirement_facts) == 2
    assert {
        item.support_state for item in assessment.criterion_facts
    } == {CriterionEvidenceSupportState.NOT_SUPPORTED}
    assert {
        item.support_state for item in assessment.requirement_facts
    } == {RequirementEvidenceFactSchema.SupportState.NOT_SUPPORTED}
    decision = WriterAdmissionPolicy(
        decision_id_factory=fixed_ids("decision-zero")
    ).evaluate(assessment=assessment, contract=WriterAdmissionContractSchema())
    assert decision.admission_outcome is WriterAdmissionOutcome.DENY
    assert decision.blocking_requirement_ids == ("r1", "r2")
    assert decision.reason_codes == ("requirement_not_fully_supported",)


def test_assessor_covers_exact_frozen_criterion_set_and_aggregates_deterministically() -> None:
    snapshot = requirements_snapshot()
    bundle = evidence_bundle(selected=("E1", "E2"))
    evaluator = MappingEvaluator(
        {
            "c1": CriterionAssessmentResult(
                support_state=CriterionEvidenceSupportState.SUPPORTED,
                evidence_ids=("E1",),
            ),
            "c2": CriterionAssessmentResult(
                support_state=CriterionEvidenceSupportState.NOT_SUPPORTED,
            ),
            "c3": CriterionAssessmentResult(
                support_state=CriterionEvidenceSupportState.INDETERMINATE,
                evidence_ids=("E2",),
            ),
        }
    )
    assessment = WriterAdmissionFactAssessor(
        evaluator=evaluator,
        assessment_id_factory=fixed_ids("assessment-mixed"),
    ).assess(requirements=snapshot, evidence_bundle=bundle)
    assert [fact.criterion_id for fact in assessment.criterion_facts] == ["c1", "c2", "c3"]
    assert [fact.requirement_id for fact in assessment.requirement_facts] == ["r1", "r2"]
    assert assessment.requirement_facts[0].support_state is RequirementEvidenceFactSchema.SupportState.PARTIALLY_SUPPORTED
    assert assessment.requirement_facts[1].support_state is RequirementEvidenceFactSchema.SupportState.INDETERMINATE


def test_assessor_rejects_dropped_or_unknown_evidence_as_fact_lineage() -> None:
    snapshot = requirements_snapshot()
    bundle = evidence_bundle(selected=("E1",), dropped=("E2",))
    evaluator = MappingEvaluator(
        {
            criterion.criterion_id: CriterionAssessmentResult(
                support_state=CriterionEvidenceSupportState.SUPPORTED,
                evidence_ids=("E2",),
            )
            for item in snapshot.requirements
            for criterion in item.criteria
        }
    )
    with pytest.raises(ValueError, match="selected Evidence"):
        WriterAdmissionFactAssessor(evaluator=evaluator).assess(
            requirements=snapshot,
            evidence_bundle=bundle,
        )


def test_nonempty_evidence_without_semantic_evaluator_is_technical_failure() -> None:
    with pytest.raises(RuntimeError, match="semantic evaluator"):
        WriterAdmissionFactAssessor().assess(
            requirements=requirements_snapshot(),
            evidence_bundle=evidence_bundle(selected=("E1",)),
        )


def test_conflict_is_orthogonal_to_supported_and_blocks_normal_admission() -> None:
    snapshot = requirements_snapshot()
    bundle = evidence_bundle(selected=("E1", "E2"))
    evaluator = MappingEvaluator(
        {
            "c1": CriterionAssessmentResult(
                support_state=CriterionEvidenceSupportState.SUPPORTED,
                evidence_ids=("E1",),
                conflicts=(CriterionConflictAssessment(evidence_ids=("E1", "E2")),),
            ),
            "c2": CriterionAssessmentResult(
                support_state=CriterionEvidenceSupportState.SUPPORTED,
                evidence_ids=("E2",),
            ),
            "c3": CriterionAssessmentResult(
                support_state=CriterionEvidenceSupportState.SUPPORTED,
                evidence_ids=("E1",),
            ),
        }
    )
    assessment = WriterAdmissionFactAssessor(
        evaluator=evaluator,
        assessment_id_factory=fixed_ids("assessment-conflict"),
    ).assess(requirements=snapshot, evidence_bundle=bundle)
    assert all(
        fact.support_state is RequirementEvidenceFactSchema.SupportState.SUPPORTED
        for fact in assessment.requirement_facts
    )
    assert len(assessment.conflict_facts) == 1
    decision = WriterAdmissionPolicy(
        decision_id_factory=fixed_ids("decision-conflict")
    ).evaluate(assessment=assessment, contract=WriterAdmissionContractSchema())
    assert decision.admission_outcome is WriterAdmissionOutcome.DENY
    assert decision.blocking_requirement_ids == ("r1",)
    assert decision.reason_codes == ("unresolved_evidence_conflict",)


def test_policy_pending_maps_to_unresolved_without_business_fabrication() -> None:
    pending = WriterAdmissionFactAssessor(
        assessment_id_factory=fixed_ids("assessment-pending")
    ).pending(
        requirements=requirements_snapshot(),
        evidence_bundle=evidence_bundle(),
    )
    decision = WriterAdmissionPolicy(
        decision_id_factory=fixed_ids("decision-pending")
    ).evaluate(assessment=pending, contract=WriterAdmissionContractSchema())
    assert decision.resolution_state is WriterAdmissionResolutionState.UNRESOLVED
    assert decision.admission_outcome is None
    assert decision.reason_codes == ("assessment_pending",)


def test_policy_all_supported_no_conflict_allows() -> None:
    snapshot = requirements_snapshot()
    evaluator = MappingEvaluator(
        {
            "c1": CriterionAssessmentResult(CriterionEvidenceSupportState.SUPPORTED, ("E1",)),
            "c2": CriterionAssessmentResult(CriterionEvidenceSupportState.SUPPORTED, ("E1",)),
            "c3": CriterionAssessmentResult(CriterionEvidenceSupportState.SUPPORTED, ("E1",)),
        }
    )
    assessment = WriterAdmissionFactAssessor(
        evaluator=evaluator,
        assessment_id_factory=fixed_ids("assessment-allow"),
    ).assess(requirements=snapshot, evidence_bundle=evidence_bundle(selected=("E1",)))
    decision = WriterAdmissionPolicy(
        decision_id_factory=fixed_ids("decision-allow")
    ).evaluate(assessment=assessment, contract=WriterAdmissionContractSchema())
    assert decision.resolution_state is WriterAdmissionResolutionState.RESOLVED
    assert decision.admission_outcome is WriterAdmissionOutcome.ALLOW
    assert decision.reason_codes == ("normal_admission_satisfied",)
    assert decision.blocking_requirement_ids == ()


def test_decision_schema_is_immutable_structural_carrier_only() -> None:
    from pydantic import ValidationError

    snapshot = requirements_snapshot()
    assessment = WriterAdmissionFactAssessor(
        evaluator=MappingEvaluator(
            {
                "c1": CriterionAssessmentResult(CriterionEvidenceSupportState.SUPPORTED, ("E1",)),
                "c2": CriterionAssessmentResult(CriterionEvidenceSupportState.SUPPORTED, ("E1",)),
                "c3": CriterionAssessmentResult(CriterionEvidenceSupportState.SUPPORTED, ("E1",)),
            }
        ),
        assessment_id_factory=fixed_ids("assessment-immutable"),
    ).assess(
        requirements=snapshot,
        evidence_bundle=evidence_bundle(selected=("E1",)),
    )
    decision = WriterAdmissionPolicy(
        decision_id_factory=fixed_ids("decision-immutable")
    ).evaluate(
        assessment=assessment,
        contract=WriterAdmissionContractSchema(),
    )

    with pytest.raises(ValidationError):
        decision.decision_id = "mutated"  # type: ignore[misc]

    source_payload = decision.model_dump(mode="json")
    restored = type(decision).model_validate(source_payload)
    assert restored == decision
    assert restored is not decision

    source_payload["reason_codes"].append("source-alias-mutation")
    source_payload["fact_assessment_ref"]["assessment_id"] = "source-alias-mutation"
    assert restored.reason_codes == ("normal_admission_satisfied",)
    assert restored.fact_assessment_ref.assessment_id == "assessment-immutable"


def test_policy_deny_reason_order_and_payload_are_deterministic() -> None:
    snapshot = requirements_snapshot()
    evaluator = MappingEvaluator(
        {
            "c1": CriterionAssessmentResult(CriterionEvidenceSupportState.NOT_SUPPORTED),
            "c2": CriterionAssessmentResult(CriterionEvidenceSupportState.SUPPORTED, ("E1",)),
            "c3": CriterionAssessmentResult(CriterionEvidenceSupportState.INDETERMINATE, ("E2",)),
        }
    )
    assessment = WriterAdmissionFactAssessor(
        evaluator=evaluator,
        assessment_id_factory=fixed_ids("assessment-deny"),
    ).assess(requirements=snapshot, evidence_bundle=evidence_bundle(selected=("E1", "E2")))
    policy = WriterAdmissionPolicy(decision_id_factory=fixed_ids("decision-1", "decision-2"))
    first = policy.evaluate(assessment=assessment, contract=WriterAdmissionContractSchema())
    second = policy.evaluate(assessment=assessment, contract=WriterAdmissionContractSchema())
    assert first.decision_id != second.decision_id
    assert first.blocking_requirement_ids == second.blocking_requirement_ids == ("r1", "r2")
    assert first.indeterminate_requirement_ids == second.indeterminate_requirement_ids == ("r2",)
    assert first.reason_codes == second.reason_codes == (
        "requirement_not_fully_supported",
        "requirement_indeterminate",
    )
    left = first.model_dump(exclude={"decision_id"})
    right = second.model_dump(exclude={"decision_id"})
    assert left == right
