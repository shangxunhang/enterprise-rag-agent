"""Target Writer admission requirement, fact-assessment, and policy services.

This module owns the three Step-2 producer boundaries only:

* authored Profile/Task requirements -> immutable canonical requirements snapshot;
* canonical requirements + canonical EvidenceBundle -> immutable semantic facts;
* canonical facts + the fixed admission contract -> immutable admission Decision.

It deliberately does not import the legacy EvidenceGatePolicy, ClaimType policy, Writer
model identity, routing, Recovery, or enforcement mechanisms.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import json
from typing import Callable, Literal, Protocol, Sequence
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from apps.enterprise_document.schemas.pre_write_admission import (
    CriterionEvidenceFactSchema,
    CriterionEvidenceSupportState,
    EvidenceConflictFactSchema,
    EvidenceCriterionSchema,
    EvidenceRequirementSchema,
    EvidenceRequirementsSnapshotSchema,
    RequirementEvidenceFactSchema,
    WriterAdmissionAssessmentState,
    WriterAdmissionContractSchema,
    WriterAdmissionDecisionSchema,
    WriterAdmissionFactAssessmentRefSchema,
    WriterAdmissionFactAssessmentSchema,
    WriterAdmissionOutcome,
    WriterAdmissionResolutionState,
)
from contracts.evidence import evidence_bundle_sha256
from core.hashing import canonical_sha256
from model_gateway.model_gateway import ModelGateway
from schemas.model import ModelRequestSchema
from schemas.rag import EvidenceDisposition, RAGEvidenceContractSchema, RAGEvidenceItemSchema


ArtifactIdFactory = Callable[[], str]

SCHEME_GENERATION_MINIMAL_PROFILE_REF = "profile:scheme-generation-minimal:v1"
SCHEME_GENERATION_MINIMAL_TASK_REF = "task-contract:scheme-generation-minimal:v1"
SCHEME_GENERATION_MINIMAL_REQUIREMENT_ID = "req_factual_grounding_v1"
SCHEME_GENERATION_MINIMAL_CRITERION_ID = "crit_project_fact_present_v1"
SCHEME_GENERATION_MINIMAL_CRITERION_TEXT = (
    "已选 Evidence 中至少有一条包含明确、具体、项目相关的事实陈述，"
    "能够作为方案章节事实性内容的证据依据。"
)


def _uuid4_id() -> str:
    return str(uuid4())


def _stable_unique(values: Sequence[str]) -> tuple[str, ...]:
    return tuple(dict.fromkeys(str(value).strip() for value in values if str(value).strip()))


@dataclass(frozen=True)
class CriterionConflictAssessment:
    """Non-canonical evaluator output used to materialize one canonical conflict fact."""

    evidence_ids: tuple[str, ...]
    reason_codes: tuple[str, ...] = ()


@dataclass(frozen=True)
class CriterionAssessmentResult:
    """Non-canonical semantic-evaluator result for one already-frozen criterion.

    The result is intentionally narrow.  It cannot add/remove requirements or criteria and
    cannot carry Admission, routing, Recovery, Writer, model, or ClaimType truth.
    """

    support_state: CriterionEvidenceSupportState
    evidence_ids: tuple[str, ...] = ()
    conflicts: tuple[CriterionConflictAssessment, ...] = ()
    reason_codes: tuple[str, ...] = ()


class CriterionSupportEvaluator(Protocol):
    """Mechanism port used inside the canonical WriterAdmissionFactAssessor boundary."""

    def evaluate(
        self,
        *,
        requirement: EvidenceRequirementSchema,
        criterion: EvidenceCriterionSchema,
        selected_evidence: tuple[RAGEvidenceItemSchema, ...],
    ) -> CriterionAssessmentResult:
        """Assess one frozen criterion against only Writer-visible selected evidence."""

        ...


class CriterionSupportTechnicalError(RuntimeError):
    """Technical evaluator failure that must never be converted to Gate business truth."""


class _LLMConflictPayload(BaseModel):
    model_config = ConfigDict(extra="forbid")

    evidence_ids: tuple[str, ...] = Field(min_length=2)
    reason_codes: tuple[str, ...] = ()


class _LLMCriterionAssessmentPayload(BaseModel):
    """Strict mechanism DTO for one model response; not a canonical Gate schema."""

    model_config = ConfigDict(extra="forbid")

    support_state: Literal["supported", "not_supported", "indeterminate"]
    evidence_ids: tuple[str, ...] = ()
    conflicts: tuple[_LLMConflictPayload, ...] = ()
    reason_codes: tuple[str, ...] = ()


class LLMCriterionSupportEvaluator:
    """Production semantic evaluator using only one criterion and selected Evidence.

    The LLM emits a strictly validated mechanism payload.  It cannot emit an
    Admission Decision, route, enforcement action, generation class, or Writer
    authorization.  Any provider/parse/reference defect raises a technical error
    before a canonical FactAssessment can be produced.
    """

    OUTPUT_CONTRACT = "criterion_support_evaluator_v1"

    def __init__(self, *, model_gateway: ModelGateway, model_name: str,
                 require_run_identity: bool = False) -> None:
        if model_gateway is None:
            raise ValueError("model_gateway is required")
        normalized_model_name = str(model_name or "").strip()
        if not normalized_model_name:
            raise ValueError("model_name is required")
        self.model_gateway = model_gateway
        self.model_name = normalized_model_name
        self.require_run_identity = require_run_identity

    @staticmethod
    def _prompt(
        *,
        requirement: EvidenceRequirementSchema,
        criterion: EvidenceCriterionSchema,
        selected_evidence: tuple[RAGEvidenceItemSchema, ...],
    ) -> str:
        evidence_payload = [
            {
                "evidence_id": item.evidence_id,
                "match_text": item.match_text,
                "context_text": item.context_text,
                "doc_id": item.doc_id,
                "title": item.title,
                "section": item.section,
                "page_start": item.page_start,
                "page_end": item.page_end,
            }
            for item in selected_evidence
        ]
        input_payload = {
            "requirement": requirement.model_dump(mode="json"),
            "criterion": criterion.model_dump(mode="json"),
            "selected_evidence": evidence_payload,
        }
        return (
            "Evaluate whether the frozen criterion is semantically supported by ONLY the "
            "selected evidence below. Do not add, remove, or rewrite requirements or criteria. "
            "Return exactly one JSON object with keys support_state, evidence_ids, conflicts, "
            "reason_codes. support_state must be supported, not_supported, or indeterminate. "
            "Use only supplied evidence_id values. supported requires at least one evidence_id. "
            "Each conflict must contain at least two supplied evidence_id values. Do not return "
            "any Gate decision, route, enforcement, generation, or Writer authorization field.\n"
            + json.dumps(input_payload, ensure_ascii=False, separators=(",", ":"))
        )

    def evaluate(
        self,
        *,
        requirement: EvidenceRequirementSchema,
        criterion: EvidenceCriterionSchema,
        selected_evidence: tuple[RAGEvidenceItemSchema, ...],
        task_id: str | None = None,
        run_id: str | None = None,
    ) -> CriterionAssessmentResult:
        if not selected_evidence:
            raise CriterionSupportTechnicalError(
                "LLM evaluator must not be called with zero selected Evidence"
            )
        if self.require_run_identity and (not task_id or not run_id):
            raise CriterionSupportTechnicalError("Target criterion evaluation requires exact Task/Run identity")
        selected_ids = {item.evidence_id for item in selected_evidence}
        request = ModelRequestSchema(
            model_call_id=f"model_call_pre_write_criterion_{uuid4()}",
            task_id=task_id or "gate_v2_pre_write_semantic_assessment",
            run_id=run_id or f"criterion_assessment_{uuid4()}",
            model_name=self.model_name,
            caller_agent="WriterAdmissionFactAssessor",
            prompt=self._prompt(
                requirement=requirement,
                criterion=criterion,
                selected_evidence=selected_evidence,
            ),
            temperature=0.0,
            max_tokens=768,
            created_at=datetime.now(timezone.utc).isoformat(),
            extra={"response_contract": self.OUTPUT_CONTRACT},
        )
        try:
            response = self.model_gateway.generate(request)
        except Exception as exc:
            raise CriterionSupportTechnicalError(
                "criterion support provider invocation failed"
            ) from exc
        if not response.success:
            raise CriterionSupportTechnicalError(
                "criterion support provider returned technical failure"
            )
        try:
            payload = _LLMCriterionAssessmentPayload.model_validate_json(
                response.content
            )
        except (ValidationError, ValueError, TypeError) as exc:
            raise CriterionSupportTechnicalError(
                "criterion support output failed strict structured validation"
            ) from exc

        evidence_ids = _stable_unique(payload.evidence_ids)
        if not set(evidence_ids) <= selected_ids:
            raise CriterionSupportTechnicalError(
                "criterion support output references unknown selected Evidence"
            )
        if payload.support_state == "supported" and not evidence_ids:
            raise CriterionSupportTechnicalError(
                "SUPPORTED criterion requires at least one selected Evidence ref"
            )

        conflicts: list[CriterionConflictAssessment] = []
        for conflict in payload.conflicts:
            conflict_ids = _stable_unique(conflict.evidence_ids)
            if len(conflict_ids) < 2:
                raise CriterionSupportTechnicalError(
                    "criterion conflict requires at least two selected Evidence refs"
                )
            if not set(conflict_ids) <= selected_ids:
                raise CriterionSupportTechnicalError(
                    "criterion conflict references unknown selected Evidence"
                )
            conflicts.append(
                CriterionConflictAssessment(
                    evidence_ids=conflict_ids,
                    reason_codes=_stable_unique(conflict.reason_codes),
                )
            )

        return CriterionAssessmentResult(
            support_state=CriterionEvidenceSupportState(payload.support_state),
            evidence_ids=evidence_ids,
            conflicts=tuple(conflicts),
            reason_codes=_stable_unique(payload.reason_codes),
        )


@dataclass(frozen=True)
class BusinessProfileTemplate:
    """Minimal static/versioned L2 fixture consumed by the Step-2 materializer."""

    profile_ref: str
    requirements: tuple[EvidenceRequirementSchema, ...]


@dataclass(frozen=True)
class TaskContractTemplate:
    """Minimal static/lightweight L3 fixture containing task-specific requirements."""

    task_ref: str
    requirements: tuple[EvidenceRequirementSchema, ...] = ()


def scheme_generation_minimal_profile() -> BusinessProfileTemplate:
    """Return the deployment-fixed intentionally minimal V1 Business Profile."""

    return BusinessProfileTemplate(
        profile_ref=SCHEME_GENERATION_MINIMAL_PROFILE_REF,
        requirements=(
            EvidenceRequirementSchema(
                requirement_id=SCHEME_GENERATION_MINIMAL_REQUIREMENT_ID,
                criteria=(
                    EvidenceCriterionSchema(
                        criterion_id=SCHEME_GENERATION_MINIMAL_CRITERION_ID,
                        criterion=SCHEME_GENERATION_MINIMAL_CRITERION_TEXT,
                    ),
                ),
            ),
        ),
    )


def scheme_generation_minimal_task_contract() -> TaskContractTemplate:
    """Return the sole V1 Task Contract; it adds zero requirements by design."""

    return TaskContractTemplate(
        task_ref=SCHEME_GENERATION_MINIMAL_TASK_REF,
        requirements=(),
    )


class EvidenceRequirementsMaterializer:
    """Materialize frozen L2/L3 authored requirements and their exact content hash.

    V1 composition is intentionally small: stable Profile requirements are followed by
    task-specific requirements in declared order.  The canonical Snapshot validator
    rejects duplicate Requirement/Criterion identities, so L3 cannot silently override
    or redefine L2 truth.  No query/title fallback or dynamic rule engine is involved.
    """

    @classmethod
    def materialize_profile_task(
        cls,
        *,
        profile: BusinessProfileTemplate,
        task_contract: TaskContractTemplate,
    ) -> EvidenceRequirementsSnapshotSchema:
        return cls.materialize(
            source_profile_ref=profile.profile_ref,
            source_task_ref=task_contract.task_ref,
            requirements=(*profile.requirements, *task_contract.requirements),
        )

    @staticmethod
    def materialize(
        *,
        source_profile_ref: str,
        source_task_ref: str,
        requirements: Sequence[EvidenceRequirementSchema],
    ) -> EvidenceRequirementsSnapshotSchema:
        canonical_requirements = tuple(requirements)
        payload = {
            "schema_version": "evidence_requirements_snapshot_v1",
            "contract_version": "evidence_requirement_semantics_v1",
            "source_profile_ref": source_profile_ref,
            "source_task_ref": source_task_ref,
            "requirements": [
                requirement.model_dump(mode="json", round_trip=True)
                for requirement in canonical_requirements
            ],
        }
        requirements_sha256 = canonical_sha256(payload)
        return EvidenceRequirementsSnapshotSchema(
            requirements_sha256=requirements_sha256,
            source_profile_ref=source_profile_ref,
            source_task_ref=source_task_ref,
            requirements=canonical_requirements,
        )


class WriterAdmissionFactAssessor:
    """Sole canonical producer boundary for Writer Admission semantic facts."""

    def __init__(
        self,
        *,
        evaluator: CriterionSupportEvaluator | None = None,
        assessment_id_factory: ArtifactIdFactory | None = None,
    ) -> None:
        self._evaluator = evaluator
        self._assessment_id_factory = assessment_id_factory or _uuid4_id

    @staticmethod
    def _selected_evidence(
        evidence_bundle: RAGEvidenceContractSchema,
    ) -> tuple[RAGEvidenceItemSchema, ...]:
        selected_ids = tuple(evidence_bundle.selected_evidence_ids)
        if len(selected_ids) != len(set(selected_ids)):
            raise ValueError("selected_evidence_ids must be duplicate-free")
        item_by_id = {item.evidence_id: item for item in evidence_bundle.items}
        if len(item_by_id) != len(evidence_bundle.items):
            raise ValueError("EvidenceBundle evidence_id values must be unique")
        selected: list[RAGEvidenceItemSchema] = []
        for evidence_id in selected_ids:
            item = item_by_id.get(evidence_id)
            if item is None:
                raise ValueError("selected Evidence reference cannot be resolved")
            if item.disposition is not EvidenceDisposition.SELECTED:
                raise ValueError("selected Evidence must have selected disposition")
            selected.append(item)
        return tuple(selected)

    @staticmethod
    def _assessment_base(
        *,
        requirements: EvidenceRequirementsSnapshotSchema,
        evidence_bundle: RAGEvidenceContractSchema,
    ) -> dict[str, str]:
        return {
            "requirements_schema_version": requirements.schema_version,
            "requirements_contract_version": requirements.contract_version,
            "requirements_sha256": requirements.requirements_sha256,
            "evidence_bundle_schema_version": evidence_bundle.schema_version,
            "evidence_bundle_sha256": evidence_bundle_sha256(evidence_bundle),
        }

    def pending(
        self,
        *,
        requirements: EvidenceRequirementsSnapshotSchema,
        evidence_bundle: RAGEvidenceContractSchema,
    ) -> WriterAdmissionFactAssessmentSchema:
        """Emit a new immutable PENDING artifact before semantic facts are available."""

        self._selected_evidence(evidence_bundle)
        return WriterAdmissionFactAssessmentSchema(
            assessment_id=self._assessment_id_factory(),
            assessment_state=WriterAdmissionAssessmentState.PENDING,
            **self._assessment_base(
                requirements=requirements,
                evidence_bundle=evidence_bundle,
            ),
        )

    def assess(
        self,
        *,
        requirements: EvidenceRequirementsSnapshotSchema,
        evidence_bundle: RAGEvidenceContractSchema,
        task_id: str | None = None,
        run_id: str | None = None,
    ) -> WriterAdmissionFactAssessmentSchema:
        """Produce one immutable COMPLETED assessment or raise a technical failure.

        A valid empty selected-Evidence domain is deterministic: every criterion is
        NOT_SUPPORTED.  Non-empty evidence requires a criterion semantic evaluator;
        evaluator failure is allowed to propagate and therefore creates no canonical
        business assessment artifact.
        """

        selected_evidence = self._selected_evidence(evidence_bundle)
        selected_ids = {item.evidence_id for item in selected_evidence}
        if selected_evidence and self._evaluator is None:
            raise RuntimeError(
                "criterion semantic evaluator is required for non-empty selected Evidence"
            )

        assessment_id = self._assessment_id_factory()
        conflict_facts: list[EvidenceConflictFactSchema] = []
        criterion_facts: list[CriterionEvidenceFactSchema] = []
        requirement_facts: list[RequirementEvidenceFactSchema] = []

        for requirement_index, requirement in enumerate(requirements.requirements, start=1):
            requirement_criterion_facts: list[CriterionEvidenceFactSchema] = []
            for criterion_index, criterion in enumerate(requirement.criteria, start=1):
                if not selected_evidence:
                    evaluation = CriterionAssessmentResult(
                        support_state=CriterionEvidenceSupportState.NOT_SUPPORTED,
                    )
                else:
                    assert self._evaluator is not None
                    evaluation = self._evaluator.evaluate(
                        requirement=requirement,
                        criterion=criterion,
                        selected_evidence=selected_evidence,
                        **({"task_id": task_id, "run_id": run_id}
                           if isinstance(self._evaluator, LLMCriterionSupportEvaluator) else {}),
                    )
                    if not isinstance(evaluation, CriterionAssessmentResult):
                        raise TypeError(
                            "criterion evaluator must return CriterionAssessmentResult"
                        )

                evidence_ids = _stable_unique(evaluation.evidence_ids)
                if not set(evidence_ids) <= selected_ids:
                    raise ValueError(
                        "criterion evidence references must belong to selected Evidence"
                    )
                if (
                    evaluation.support_state is CriterionEvidenceSupportState.SUPPORTED
                    and not evidence_ids
                ):
                    raise ValueError("SUPPORTED criterion requires selected Evidence refs")

                criterion_conflict_refs: list[str] = []
                for conflict_index, conflict in enumerate(evaluation.conflicts, start=1):
                    conflict_evidence_ids = _stable_unique(conflict.evidence_ids)
                    if len(conflict_evidence_ids) < 2:
                        raise ValueError("criterion conflict requires at least two Evidence refs")
                    if not set(conflict_evidence_ids) <= selected_ids:
                        raise ValueError(
                            "conflict Evidence references must belong to selected Evidence"
                        )
                    conflict_id = (
                        f"conflict_{assessment_id}_{requirement_index:04d}_"
                        f"{criterion_index:04d}_{conflict_index:04d}"
                    )
                    conflict_facts.append(
                        EvidenceConflictFactSchema(
                            conflict_id=conflict_id,
                            criterion_id=criterion.criterion_id,
                            evidence_ids=conflict_evidence_ids,
                            reason_codes=_stable_unique(conflict.reason_codes),
                        )
                    )
                    criterion_conflict_refs.append(conflict_id)

                criterion_fact = CriterionEvidenceFactSchema(
                    fact_id=(
                        f"criterion_fact_{assessment_id}_{requirement_index:04d}_"
                        f"{criterion_index:04d}"
                    ),
                    requirement_id=requirement.requirement_id,
                    criterion_id=criterion.criterion_id,
                    support_state=evaluation.support_state,
                    evidence_ids=evidence_ids,
                    conflict_refs=tuple(criterion_conflict_refs),
                    reason_codes=_stable_unique(evaluation.reason_codes),
                )
                criterion_facts.append(criterion_fact)
                requirement_criterion_facts.append(criterion_fact)

            support_states = tuple(
                fact.support_state for fact in requirement_criterion_facts
            )
            if CriterionEvidenceSupportState.INDETERMINATE in support_states:
                requirement_state = RequirementEvidenceFactSchema.SupportState.INDETERMINATE
            elif all(
                state is CriterionEvidenceSupportState.SUPPORTED
                for state in support_states
            ):
                requirement_state = RequirementEvidenceFactSchema.SupportState.SUPPORTED
            elif all(
                state is CriterionEvidenceSupportState.NOT_SUPPORTED
                for state in support_states
            ):
                requirement_state = RequirementEvidenceFactSchema.SupportState.NOT_SUPPORTED
            else:
                requirement_state = (
                    RequirementEvidenceFactSchema.SupportState.PARTIALLY_SUPPORTED
                )

            conflict_refs = tuple(
                dict.fromkeys(
                    conflict_ref
                    for fact in requirement_criterion_facts
                    for conflict_ref in fact.conflict_refs
                )
            )
            requirement_facts.append(
                RequirementEvidenceFactSchema(
                    fact_id=f"requirement_fact_{assessment_id}_{requirement_index:04d}",
                    requirement_id=requirement.requirement_id,
                    support_state=requirement_state,
                    criterion_fact_ids=tuple(
                        fact.fact_id for fact in requirement_criterion_facts
                    ),
                    conflict_refs=conflict_refs,
                    reason_codes=(),
                )
            )

        return WriterAdmissionFactAssessmentSchema(
            assessment_id=assessment_id,
            assessment_state=WriterAdmissionAssessmentState.COMPLETED,
            **self._assessment_base(
                requirements=requirements,
                evidence_bundle=evidence_bundle,
            ),
            conflict_facts=tuple(conflict_facts),
            criterion_facts=tuple(criterion_facts),
            requirement_facts=tuple(requirement_facts),
        )


class WriterAdmissionPolicy:
    """Sole canonical business producer of WriterAdmissionDecision truth."""

    def __init__(self, *, decision_id_factory: ArtifactIdFactory | None = None) -> None:
        self._decision_id_factory = decision_id_factory or _uuid4_id

    @staticmethod
    def _assessment_ref(
        assessment: WriterAdmissionFactAssessmentSchema,
    ) -> WriterAdmissionFactAssessmentRefSchema:
        return WriterAdmissionFactAssessmentRefSchema(
            assessment_id=assessment.assessment_id,
            assessment_schema_version=assessment.schema_version,
            assessment_contract_version=assessment.contract_version,
            requirements_schema_version=assessment.requirements_schema_version,
            requirements_contract_version=assessment.requirements_contract_version,
            requirements_sha256=assessment.requirements_sha256,
            evidence_bundle_schema_version=assessment.evidence_bundle_schema_version,
            evidence_bundle_sha256=assessment.evidence_bundle_sha256,
        )

    def evaluate(
        self,
        *,
        assessment: WriterAdmissionFactAssessmentSchema,
        contract: WriterAdmissionContractSchema,
        supersedes_decision_id: str | None = None,
    ) -> WriterAdmissionDecisionSchema:
        """Apply the frozen deterministic PENDING/ALLOW/DENY matrix.

        The method intentionally has no raw Evidence, Task, ClaimType, regex, score, model,
        or provider parameter.  Invalid/technical upstream operations therefore cannot be
        converted into a business Decision through this API.
        """

        # Accessing these fields makes the fixed system contract dependency explicit while
        # keeping business calculation closed to the frozen V1 semantics.
        if contract.required_support_state != "supported":
            raise ValueError("unsupported WriterAdmissionContract support semantics")
        if contract.unresolved_conflict_allowed is not False:
            raise ValueError("unsupported WriterAdmissionContract conflict semantics")

        assessment_ref = self._assessment_ref(assessment)
        decision_id = self._decision_id_factory()
        if assessment.assessment_state is WriterAdmissionAssessmentState.PENDING:
            return WriterAdmissionDecisionSchema(
                decision_id=decision_id,
                resolution_state=WriterAdmissionResolutionState.UNRESOLVED,
                admission_outcome=None,
                supersedes_decision_id=supersedes_decision_id,
                fact_assessment_ref=assessment_ref,
                reason_codes=("assessment_pending",),
            )

        requirement_facts = tuple(assessment.requirement_facts)
        criterion_by_id = {
            fact.criterion_id: fact for fact in assessment.criterion_facts
        }
        conflicted_requirement_ids = {
            criterion_by_id[conflict.criterion_id].requirement_id
            for conflict in assessment.conflict_facts
        }
        blocking_requirement_ids = tuple(
            fact.requirement_id
            for fact in requirement_facts
            if (
                fact.support_state is not RequirementEvidenceFactSchema.SupportState.SUPPORTED
                or fact.requirement_id in conflicted_requirement_ids
            )
        )
        indeterminate_requirement_ids = tuple(
            fact.requirement_id
            for fact in requirement_facts
            if fact.support_state
            is RequirementEvidenceFactSchema.SupportState.INDETERMINATE
        )
        conflict_refs = tuple(
            conflict.conflict_id for conflict in assessment.conflict_facts
        )

        if not blocking_requirement_ids and not conflict_refs:
            return WriterAdmissionDecisionSchema(
                decision_id=decision_id,
                resolution_state=WriterAdmissionResolutionState.RESOLVED,
                admission_outcome=WriterAdmissionOutcome.ALLOW,
                supersedes_decision_id=supersedes_decision_id,
                fact_assessment_ref=assessment_ref,
                reason_codes=("normal_admission_satisfied",),
            )

        reason_codes: list[str] = []
        if any(
            fact.support_state
            is not RequirementEvidenceFactSchema.SupportState.SUPPORTED
            for fact in requirement_facts
        ):
            reason_codes.append("requirement_not_fully_supported")
        if indeterminate_requirement_ids:
            reason_codes.append("requirement_indeterminate")
        if conflict_refs:
            reason_codes.append("unresolved_evidence_conflict")

        return WriterAdmissionDecisionSchema(
            decision_id=decision_id,
            resolution_state=WriterAdmissionResolutionState.RESOLVED,
            admission_outcome=WriterAdmissionOutcome.DENY,
            supersedes_decision_id=supersedes_decision_id,
            fact_assessment_ref=assessment_ref,
            blocking_requirement_ids=blocking_requirement_ids,
            indeterminate_requirement_ids=indeterminate_requirement_ids,
            conflict_refs=conflict_refs,
            reason_codes=tuple(reason_codes),
        )


__all__ = [
    "BusinessProfileTemplate",
    "CriterionAssessmentResult",
    "CriterionConflictAssessment",
    "CriterionSupportEvaluator",
    "CriterionSupportTechnicalError",
    "EvidenceRequirementsMaterializer",
    "LLMCriterionSupportEvaluator",
    "SCHEME_GENERATION_MINIMAL_CRITERION_ID",
    "SCHEME_GENERATION_MINIMAL_CRITERION_TEXT",
    "SCHEME_GENERATION_MINIMAL_PROFILE_REF",
    "SCHEME_GENERATION_MINIMAL_REQUIREMENT_ID",
    "SCHEME_GENERATION_MINIMAL_TASK_REF",
    "TaskContractTemplate",
    "WriterAdmissionFactAssessor",
    "WriterAdmissionPolicy",
    "scheme_generation_minimal_profile",
    "scheme_generation_minimal_task_contract",
]
