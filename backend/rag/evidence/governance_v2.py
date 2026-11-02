"""Stage D deterministic evidence eligibility and coverage assessment.

C-RAG first decides whether retrieved evidence is relevant to the current
section.  This module consumes that decision and answers the narrower claim-
eligibility question from canonical Document Profiles, Structured Evidence and
the exact prompt-visible text.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
import hashlib
import json
import re
import unicodedata
from typing import Any, Iterable

from pydantic import BaseModel, ConfigDict, Field, model_validator

from schemas.claim import (
    CURRENT_PROJECT_CLAIM_TYPES,
    TEMPLATE_ONLY_CLAIM_TYPES,
    ClaimType,
)
from schemas.evidence_assessment import (
    CoverageStrategy,
    EvidenceAssessmentV2Schema,
    EvidenceConflictSchema,
    EvidenceRelevance,
    EvidenceSetAssessmentSchema,
)
from schemas.evidence_common import EvidenceStatus
from schemas.evidence_source import (
    AuthorityLevel,
    DocumentType,
    EvidenceRole,
    ProjectRelation,
)
from schemas.rag import (
    EvidenceRequirementsSchema,
    RAGEvidenceAssessmentSchema,
)
from rag.evidence.eligibility_policy import (
    EVIDENCE_USE_ELIGIBILITY_POLICY_ID,
    EVIDENCE_USE_ELIGIBILITY_POLICY_VERSION,
)
from rag.retriever.bm25_child_retriever import default_keyword_tokenize
from rag.schema.prompt_evidence_candidate_v1 import (
    PromptEvidenceCandidateV1Schema,
)
from rag.schema.retrieval_candidate_v2 import (
    RetrievalCandidateV2Schema,
    build_retrieval_candidate_v2,
)
from rag.schema.structured_evidence import FactStatus


class RAGEvidenceRequirementsPolicySchema(EvidenceRequirementsSchema):
    """RAG-internal policy wrapper around the public requirements envelope."""

    model_config = ConfigDict(extra="forbid", validate_assignment=True)

    policy_id: str = "conservative_section_policy_v1"
    # Legacy ignored data remains readable/serializable but does not drive retrieval.
    requested_claim_types: list[ClaimType] = Field(default_factory=list)
    coverage_strategy: CoverageStrategy = CoverageStrategy.TOPIC_UNION
    metadata: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def _unique_values(self) -> "RAGEvidenceRequirementsPolicySchema":
        if len(self.requested_claim_types) != len(set(self.requested_claim_types)):
            raise ValueError("requested_claim_types must be unique")
        return self


@dataclass(frozen=True)
class FactClaimPolicyResult:
    eligible: bool
    certainty_level: str
    required_qualifiers: tuple[str, ...] = ()
    reason_codes: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {
            "eligible": self.eligible,
            "certainty_level": self.certainty_level,
            "required_qualifiers": list(self.required_qualifiers),
            "reason_codes": list(self.reason_codes),
        }


@dataclass(frozen=True)
class EvidenceGovernanceV2Result:
    requirements: RAGEvidenceRequirementsPolicySchema
    candidates: tuple[RetrievalCandidateV2Schema, ...]
    item_assessments: tuple[EvidenceAssessmentV2Schema, ...]
    set_assessment: EvidenceSetAssessmentSchema
    public_assessment: RAGEvidenceAssessmentSchema
    eligible_candidate_ids: tuple[str, ...]
    rejected_candidate_ids: tuple[str, ...]
    candidate_contract_errors: tuple[dict[str, Any], ...] = ()
    prompt_evidence_candidates: tuple[PromptEvidenceCandidateV1Schema, ...] = ()
    assessment_view: str = "retrieval"
    prompt_evidence_source: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": "evidence_governance_v2",
            "assessment_view": self.assessment_view,
            "prompt_evidence_source": self.prompt_evidence_source,
            "requirements": self.requirements.model_dump(mode="json"),
            "retrieval_candidates": [
                item.model_dump(mode="json", exclude_none=True)
                for item in self.candidates
            ],
            "prompt_evidence_candidates": [
                item.model_dump(mode="json", exclude_none=True)
                for item in self.prompt_evidence_candidates
            ],
            "item_assessments": [
                item.model_dump(mode="json", exclude_none=True)
                for item in self.item_assessments
            ],
            "set_assessment": self.set_assessment.model_dump(
                mode="json", exclude_none=True
            ),
            "public_assessment": self.public_assessment.model_dump(
                mode="json", exclude_none=True
            ),
            "eligible_candidate_ids": list(self.eligible_candidate_ids),
            "rejected_candidate_ids": list(self.rejected_candidate_ids),
            "candidate_contract_errors": [
                dict(item) for item in self.candidate_contract_errors
            ],
        }


@dataclass(frozen=True)
class _AssessmentView:
    candidate: RetrievalCandidateV2Schema
    semantic_text: str
    topic_text: str
    fact_visible_in_prompt: bool | None
    prompt_candidate: PromptEvidenceCandidateV1Schema | None = None


def _stable_unique(values: Iterable[Any]) -> list[str]:
    return list(
        dict.fromkeys(
            str(value).strip() for value in values if str(value or "").strip()
        )
    )


def _slug(value: str, fallback: str) -> str:
    normalized = re.sub(r"[^0-9A-Za-z_\-\u4e00-\u9fff]+", "_", value).strip("_")
    return normalized or fallback


def resolve_evidence_requirements_v2(
    *,
    query: str,
    runtime_context: dict[str, Any] | None,
) -> RAGEvidenceRequirementsPolicySchema:
    context = dict(runtime_context or {})
    explicit = context.get("evidence_requirements")
    explicit = dict(explicit) if isinstance(explicit, dict) else {}
    section_id = str(
        explicit.get("section_id")
        or context.get("section_id")
        or context.get("retrieval_scope")
        or "document"
    ).strip()
    section_title = str(
        explicit.get("section_title")
        or context.get("section_title")
        or section_id
    ).strip()
    section_type = str(
        explicit.get("section_type")
        or context.get("section_type")
        or section_id
    ).strip()
    raw_requirements = explicit.get("requirements")
    if isinstance(raw_requirements, list) and raw_requirements:
        public_requirements = EvidenceRequirementsSchema.model_validate(
            {
                "schema_version": explicit.get(
                    "schema_version", "evidence_requirements_v2"
                ),
                "section_id": _slug(section_id, "document"),
                "section_title": section_title or None,
                "section_type": _slug(section_type, "document"),
                "requirements": raw_requirements,
            }
        )
    else:
        topics = _stable_unique(
            explicit.get("required_topics") or [section_title or query]
        )
        public_requirements = EvidenceRequirementsSchema.from_topics(
            section_id=_slug(section_id, "document"),
            section_title=section_title or None,
            section_type=_slug(section_type, "document"),
            topics=topics,
        )
    raw_claims = explicit.get("requested_claim_types") or []
    claims = [ClaimType(item) for item in raw_claims]
    strategy = explicit.get("coverage_strategy")
    coverage_strategy = (
        CoverageStrategy(strategy)
        if strategy
        else CoverageStrategy.TOPIC_UNION
    )
    return RAGEvidenceRequirementsPolicySchema(
        **public_requirements.model_dump(exclude={"required_topics"}),
        policy_id=str(
            explicit.get("policy_id") or "candidate_capability_union_v1"
        ),
        requested_claim_types=list(dict.fromkeys(claims)),
        coverage_strategy=coverage_strategy,
        metadata={
            "resolution": "candidate_capability_union",
            "claim_resolution_mode": "candidate_capability_union",
            "requested_claim_types_ignored": True,
            "section_title": section_title,
            "explicit_requirements": bool(explicit),
            "prompt_evidence_source": context.get("prompt_evidence_source"),
            "prompt_evidence_contract_missing": bool(
                context.get("prompt_evidence_contract_missing", False)
            ),
        },
    )


def _term_overlap(required: str, text: str) -> float:
    required_tokens = set(default_keyword_tokenize(required))
    text_tokens = set(default_keyword_tokenize(text))
    if not required_tokens or not text_tokens:
        return 0.0
    return len(required_tokens & text_tokens) / len(required_tokens)


def _bounded_score(candidate: RetrievalCandidateV2Schema, query: str, text: str) -> float:
    overlap = _term_overlap(query, text)
    score = candidate.rerank_score
    if score is None or not 0.0 <= score <= 1.0:
        score = candidate.retrieval_score
    if score is None or not 0.0 <= score <= 1.0:
        score = 1.0 / max(candidate.rank, 1)
    return max(0.0, min(1.0, max(overlap, float(score))))


def resolve_fact_claim_eligibility(
    *,
    claim_type: ClaimType,
    fact_status: FactStatus,
    authority_level: AuthorityLevel,
    document_type: DocumentType,
    source_document_form: str | None,
    project_relation: ProjectRelation,
) -> FactClaimPolicyResult:
    """Resolve certainty policy without bypassing profile/project authority."""
    if claim_type not in CURRENT_PROJECT_CLAIM_TYPES:
        return FactClaimPolicyResult(True, "not_applicable")
    if project_relation is not ProjectRelation.CURRENT_PROJECT:
        return FactClaimPolicyResult(
            False,
            "wrong_project",
            reason_codes=("current_project_claim_requires_current_project_relation",),
        )

    authoritative = authority_level in {
        AuthorityLevel.AUTHORITATIVE,
        AuthorityLevel.OFFICIAL,
        AuthorityLevel.CONFIRMED,
    }
    if fact_status is FactStatus.UNKNOWN:
        return FactClaimPolicyResult(
            False,
            "unconfirmed",
            required_qualifiers=("该信息尚未确认",),
            reason_codes=("fact_status_unknown",),
        )
    if fact_status is FactStatus.ESTIMATED:
        return FactClaimPolicyResult(
            True,
            "estimate_only",
            required_qualifiers=(
                "根据现有估算材料",
                "最终金额以审批或采购结果为准",
            ),
            reason_codes=("fact_status_estimated",),
        )
    if fact_status is FactStatus.PROPOSED:
        return FactClaimPolicyResult(
            True,
            "proposal_only",
            required_qualifiers=("拟", "尚待确认"),
            reason_codes=("fact_status_proposed",),
        )
    if fact_status is FactStatus.PLANNED:
        return FactClaimPolicyResult(
            True,
            "plan_only",
            required_qualifiers=("计划", "尚待确认"),
            reason_codes=("fact_status_planned",),
        )
    if fact_status is FactStatus.CONFIRMED:
        if not authoritative:
            return FactClaimPolicyResult(
                False,
                "confirmed_but_not_authoritative",
                reason_codes=("confirmed_fact_requires_authoritative_document",),
            )
        return FactClaimPolicyResult(
            True,
            "confirmed",
            reason_codes=("fact_status_confirmed",),
        )

    form = str(source_document_form or "").strip().lower()
    decision_markers = (
        "decision",
        "approval",
        "procurement_result",
        "tender_result",
        "contract",
        "meeting_minutes",
        "official_notice",
        "决策",
        "批复",
        "成交",
        "中标",
        "合同",
        "会议纪要",
    )
    formal_decision = bool(form) and any(marker in form for marker in decision_markers)
    if fact_status is FactStatus.DECIDED and authoritative and formal_decision:
        return FactClaimPolicyResult(
            True,
            "decided",
            reason_codes=("fact_status_decided",),
        )
    return FactClaimPolicyResult(
        False,
        "decision_authority_insufficient",
        reason_codes=("decided_fact_requires_formal_authoritative_source",),
    )


def _claim_structure_compatible(
    claim: ClaimType,
    view: _AssessmentView,
) -> bool:
    candidate = view.candidate
    structured = candidate.structured_evidence
    fact = structured.fact
    table = structured.table
    policy = structured.policy
    template = structured.template
    text = view.topic_text

    if claim is ClaimType.POLICY_REQUIREMENT:
        return (
            candidate.evidence_role is EvidenceRole.POLICY_STANDARD
            and policy is not None
            and not policy.is_table_of_contents
        )
    if claim in TEMPLATE_ONLY_CLAIM_TYPES:
        return candidate.evidence_role is EvidenceRole.TEMPLATE_STYLE and template is not None
    if claim is ClaimType.PROCUREMENT_REQUIREMENT:
        return candidate.evidence_role is EvidenceRole.PROCUREMENT_REQUIREMENT
    if claim is ClaimType.TECHNICAL_SPECIFICATION:
        return candidate.evidence_role is EvidenceRole.TECHNICAL_REFERENCE

    if fact is not None and view.fact_visible_in_prompt is False:
        return False
    fact_category = str(fact.fact_category or "").lower() if fact else ""
    if claim is ClaimType.CURRENT_PROJECT_COST:
        return bool(
            (fact and fact_category == "cost")
            or (table and table.calculation_record)
            or any(token in text for token in ("投资", "预算", "概算", "费用", "金额"))
        )
    if claim is ClaimType.CURRENT_PROJECT_QUANTITY:
        return bool(
            (fact and fact_category in {"quantity", "configuration"})
            or any(token in text for token in ("数量", "台", "套", "规模", "容量"))
        )
    if claim is ClaimType.CURRENT_PROJECT_SCHEDULE:
        return bool(
            (fact and fact_category == "schedule")
            or any(token in text for token in ("工期", "进度", "周期", "计划时间", "上线时间"))
        )
    if claim is ClaimType.CURRENT_PROJECT_CONFIGURATION:
        return bool(
            (fact and fact_category in {"configuration", "quantity"})
            or any(token in text for token in ("配置", "设备", "资源", "规格", "参数"))
        )
    if claim is ClaimType.CURRENT_PROJECT_FACT:
        return bool(text.strip()) and (
            fact is None or view.fact_visible_in_prompt is not False
        )
    return True


def _item_assessment(
    view: _AssessmentView,
    requirements: RAGEvidenceRequirementsPolicySchema,
    query: str,
) -> EvidenceAssessmentV2Schema:
    candidate = view.candidate
    topic_scores = {
        topic: _term_overlap(topic, view.topic_text)
        for topic in requirements.required_topics
    }
    supported_topics = [
        topic for topic, score in topic_scores.items() if score >= 0.30
    ]
    semantic_relevance = max(
        [_bounded_score(candidate, query, view.semantic_text), *topic_scores.values()],
        default=0.0,
    )
    if any(score >= 0.60 for score in topic_scores.values()) or semantic_relevance >= 0.7:
        relevance = EvidenceRelevance.RELEVANT
    elif supported_topics or semantic_relevance >= 0.25:
        relevance = EvidenceRelevance.PARTIAL
    else:
        relevance = EvidenceRelevance.IRRELEVANT

    crag_judgement = candidate.metadata.get("crag_final_judgement")
    crag_judgement = crag_judgement if isinstance(crag_judgement, dict) else {}
    crag_relevance = str(crag_judgement.get("relevance") or "").strip().lower()
    governance_relevance_before_crag = relevance
    if crag_relevance == "irrelevant":
        relevance = EvidenceRelevance.IRRELEVANT
        supported_topics = []

    allowed = set(candidate.allowed_claim_types)
    forbidden = set(candidate.forbidden_claim_types)
    supported_claims = allowed - forbidden
    removed_claims: set[ClaimType] = set()
    reason_codes: list[str] = []
    qualifiers: list[str] = []
    fact_policies: dict[str, Any] = {}

    current_claims = supported_claims & CURRENT_PROJECT_CLAIM_TYPES
    if current_claims and candidate.project_relation is not ProjectRelation.CURRENT_PROJECT:
        supported_claims -= current_claims
        removed_claims.update(current_claims)
        reason_codes.append(
            "current_project_claim_requires_current_project_relation"
        )

    for claim in list(supported_claims):
        if not _claim_structure_compatible(claim, view):
            supported_claims.remove(claim)
            removed_claims.add(claim)
            reason_codes.append(f"structured_evidence_incompatible:{claim.value}")
            if view.prompt_candidate is not None and view.fact_visible_in_prompt is False:
                reason_codes.append("structured_fact_not_visible_in_prompt")
            continue
        fact = candidate.structured_evidence.fact
        if fact is not None and claim in CURRENT_PROJECT_CLAIM_TYPES:
            policy = resolve_fact_claim_eligibility(
                claim_type=claim,
                fact_status=fact.fact_status,
                authority_level=candidate.authority_level,
                document_type=candidate.document_type,
                source_document_form=str(
                    candidate.document_profile.get("source_document_form") or ""
                )
                or None,
                project_relation=candidate.project_relation,
            )
            fact_policies[claim.value] = policy.to_dict()
            reason_codes.extend(policy.reason_codes)
            qualifiers.extend(policy.required_qualifiers)
            if not policy.eligible:
                supported_claims.remove(claim)
                removed_claims.add(claim)

    if allowed and not supported_claims:
        reason_codes.append("document_profile_has_no_eligible_claim_capability")
    if crag_relevance == "irrelevant":
        reason_codes.append("rejected_by_final_crag_irrelevant")
    elif relevance is EvidenceRelevance.IRRELEVANT:
        reason_codes.append("semantic_relevance_below_threshold")
    eligibility = bool(supported_claims) and relevance is not EvidenceRelevance.IRRELEVANT
    if eligibility:
        reason_codes.append("eligible_by_profile_structure_status_and_relevance")

    return EvidenceAssessmentV2Schema(
        evidence_id=candidate.candidate_id,
        document_id=candidate.document_id,
        eligibility_semantic_policy_id=EVIDENCE_USE_ELIGIBILITY_POLICY_ID,
        eligibility_semantic_policy_version=EVIDENCE_USE_ELIGIBILITY_POLICY_VERSION,
        document_type=candidate.document_type,
        evidence_role=candidate.evidence_role,
        project_relation=candidate.project_relation,
        authority_level=candidate.authority_level,
        semantic_relevance=round(semantic_relevance, 6),
        eligibility=eligibility,
        relevance=relevance,
        supported_topics=(
            supported_topics
            if relevance is not EvidenceRelevance.IRRELEVANT
            else []
        ),
        supported_claim_types=(
            sorted(supported_claims, key=lambda item: item.value)
            if eligibility
            else []
        ),
        forbidden_claim_types=sorted(
            forbidden | removed_claims,
            key=lambda item: item.value,
        ),
        reason_codes=_stable_unique(reason_codes),
        metadata={
            "topic_scores": {
                key: round(value, 6) for key, value in topic_scores.items()
            },
            "profile_sha256": candidate.profile_sha256,
            "source_sha256": candidate.source_sha256,
            "rank": candidate.rank,
            "assessment_view": (
                "prompt" if view.prompt_candidate is not None else "retrieval"
            ),
            "fact_visible_in_prompt": view.fact_visible_in_prompt,
            "required_qualifiers": _stable_unique(qualifiers),
            "fact_claim_policies": fact_policies,
            "claim_resolution_mode": "candidate_capability_union",
            "crag_final_judgement": crag_judgement or None,
            "governance_relevance_before_crag": (
                governance_relevance_before_crag.value
            ),
            "structured_evidence": candidate.structured_evidence.model_dump(
                mode="json", exclude_none=True
            ),
        },
    )


def _normalize_predicate(value: str) -> str:
    text = unicodedata.normalize("NFKC", str(value or ""))
    text = re.sub(r"[\s:：,，;；。]+", "", text)
    return text.strip().lower()


def _normalize_fact_value(value: Any, unit: str | None) -> str:
    raw = unicodedata.normalize("NFKC", str(value or "")).strip()
    raw = re.sub(r"\s+", "", raw)
    normalized_unit = unicodedata.normalize("NFKC", str(unit or "")).strip()
    qualified_markers = ("约", "大约", "预计", "估算", "不超过", "以上", "以下", "至少", "至多", ">", "<", "~", "～")
    if any(marker in raw for marker in qualified_markers):
        return f"text:{raw}|unit:{normalized_unit}"

    match = re.fullmatch(r"([+-]?\d+(?:\.\d+)?)(亿元|万元|元)?", raw)
    if match:
        number_text, inline_unit = match.groups()
        effective_unit = inline_unit or normalized_unit
        multipliers = {
            "元": Decimal("1"),
            "万元": Decimal("10000"),
            "亿元": Decimal("100000000"),
        }
        if effective_unit in multipliers:
            try:
                base = Decimal(number_text) * multipliers[effective_unit]
            except InvalidOperation:
                pass
            else:
                return f"money_yuan:{base.normalize()}"
    return f"text:{raw}|unit:{normalized_unit}"


def _structured_fact(
    candidate: RetrievalCandidateV2Schema,
) -> tuple[str, str] | None:
    fact = candidate.structured_evidence.fact
    if fact is None or not fact.predicate or fact.value in (None, ""):
        return None
    predicate = _normalize_predicate(fact.predicate)
    if not predicate:
        return None
    return predicate, _normalize_fact_value(fact.value, fact.unit)


def _conflicts(
    candidates: list[RetrievalCandidateV2Schema],
    assessments: list[EvidenceAssessmentV2Schema],
    requirements: RAGEvidenceRequirementsPolicySchema,
) -> list[EvidenceConflictSchema]:
    candidate_by_id = {item.candidate_id: item for item in candidates}
    groups: dict[tuple[ClaimType, str], list[tuple[RetrievalCandidateV2Schema, str]]] = {}
    for assessment in assessments:
        if not assessment.eligibility:
            continue
        candidate = candidate_by_id.get(assessment.evidence_id)
        if candidate is None:
            continue
        fact = _structured_fact(candidate)
        if fact is None:
            continue
        predicate, value = fact
        for claim in assessment.supported_claim_types:
            groups.setdefault((claim, predicate), []).append((candidate, value))

    conflicts: list[EvidenceConflictSchema] = []
    for (claim, predicate), rows in sorted(
        groups.items(), key=lambda item: (item[0][0].value, item[0][1])
    ):
        values = {value for _, value in rows}
        documents = {candidate.document_id for candidate, _ in rows}
        if len(values) < 2 or len(documents) < 2:
            continue
        evidence_ids = _stable_unique(
            candidate.candidate_id for candidate, _ in rows
        )
        digest = hashlib.sha256(
            f"{requirements.section_id}:{claim.value}:{predicate}:{'|'.join(sorted(values))}".encode(
                "utf-8"
            )
        ).hexdigest()[:12]
        conflicts.append(
            EvidenceConflictSchema(
                conflict_id=f"conflict_{digest}",
                topic=predicate,
                evidence_ids=evidence_ids,
                claim_type=claim,
                description="不同文档对同一规范化结构化事实给出不同值",
                candidate_values={
                    candidate.candidate_id: value for candidate, value in rows
                },
            )
        )
    return conflicts


def _set_assessment(
    candidates: list[RetrievalCandidateV2Schema],
    assessments: list[EvidenceAssessmentV2Schema],
    requirements: RAGEvidenceRequirementsPolicySchema,
) -> EvidenceSetAssessmentSchema:
    eligible = [item for item in assessments if item.eligibility]
    covered = [
        topic
        for topic in requirements.required_topics
        if any(topic in item.supported_topics for item in eligible)
    ]
    missing = [
        topic for topic in requirements.required_topics if topic not in covered
    ]
    conflicts = _conflicts(candidates, assessments, requirements)
    ratio = len(covered) / len(requirements.required_topics)
    if conflicts:
        status = EvidenceStatus.CONFLICTED
    elif not eligible or not covered:
        status = EvidenceStatus.INSUFFICIENT
        ratio = 0.0
        covered = []
        missing = list(requirements.required_topics)
    elif not missing:
        status = EvidenceStatus.SUFFICIENT
        ratio = 1.0
    else:
        status = EvidenceStatus.INSUFFICIENT
    supporting = eligible if covered else []
    evidence_ids = [item.evidence_id for item in supporting]
    evidence_id_set = set(evidence_ids)
    document_ids = {
        item.document_id
        for item in candidates
        if item.candidate_id in evidence_id_set
    }
    return EvidenceSetAssessmentSchema(
        assessment_id=f"assessment_{requirements.section_id}",
        section_id=requirements.section_id,
        section_type=requirements.section_type,
        strategy=requirements.coverage_strategy,
        status=status,
        coverage_ratio=round(ratio, 6),
        required_topics=list(requirements.required_topics),
        covered_topics=covered,
        missing_topics=missing,
        direct_evidence_count=sum(
            1
            for item in supporting
            if item.relevance is EvidenceRelevance.RELEVANT
        ),
        partial_evidence_count=sum(
            1
            for item in supporting
            if item.relevance is EvidenceRelevance.PARTIAL
        ),
        distinct_document_count=len(document_ids),
        evidence_ids=evidence_ids,
        conflicts=conflicts,
        reason_codes=(
            ["evidence_conflict_detected"]
            if conflicts
            else (
                ["coverage_partial"]
                if status is EvidenceStatus.INSUFFICIENT and covered
                else ["coverage_absent"]
                if status is EvidenceStatus.INSUFFICIENT
                else ["coverage_sufficient"]
            )
        ),
        metadata={
            "claim_resolution_mode": "candidate_capability_union",
            "requested_claim_types_ignored": True,
            "legacy_coverage_shape": (
                "partial" if covered and missing else "absent" if not covered else None
            ),
        },
    )


@dataclass(frozen=True)
class _SetEvidenceFacts:
    """Internal carrier for one-way projection into public evidence facts."""

    supported_claim_types: frozenset[ClaimType]
    forbidden_claim_types: frozenset[ClaimType]
    authoritative_evidence_present: bool
    historical_or_reference_evidence_present: bool
    required_qualifiers: tuple[str, ...]
    prompt_evidence_source: str | None
    prompt_evidence_contract_missing: bool
    reason_codes: tuple[str, ...]


def _set_evidence_facts(
    requirements: RAGEvidenceRequirementsPolicySchema,
    candidates: list[RetrievalCandidateV2Schema],
    assessments: list[EvidenceAssessmentV2Schema],
    evidence_set: EvidenceSetAssessmentSchema,
) -> _SetEvidenceFacts:
    eligible = [item for item in assessments if item.eligibility]
    supported = set().union(
        *(set(item.supported_claim_types) for item in eligible),
        set(),
    )
    forbidden = set().union(
        *(set(item.forbidden_claim_types) for item in assessments),
        set(),
    ) - supported
    candidate_by_id = {item.candidate_id: item for item in candidates}
    authoritative = any(
        candidate_by_id[item.evidence_id].authority_level
        in {
            AuthorityLevel.AUTHORITATIVE,
            AuthorityLevel.OFFICIAL,
            AuthorityLevel.CONFIRMED,
        }
        for item in eligible
        if item.evidence_id in candidate_by_id
    )
    historical_or_reference = any(
        candidate_by_id[item.evidence_id].evidence_role
        in {EvidenceRole.HISTORICAL_CASE, EvidenceRole.TECHNICAL_REFERENCE}
        or candidate_by_id[item.evidence_id].authority_level
        in {
            AuthorityLevel.REFERENCE,
            AuthorityLevel.DRAFT,
            AuthorityLevel.UNKNOWN,
        }
        for item in eligible
        if item.evidence_id in candidate_by_id
    )
    qualifiers = _stable_unique(
        qualifier
        for item in eligible
        for qualifier in item.metadata.get("required_qualifiers", [])
    )
    reason_codes = _stable_unique(
        [
            *evidence_set.reason_codes,
            *(
                reason
                for item in assessments
                for reason in item.reason_codes
            ),
        ]
    )
    return _SetEvidenceFacts(
        supported_claim_types=frozenset(supported),
        forbidden_claim_types=frozenset(forbidden),
        authoritative_evidence_present=authoritative,
        historical_or_reference_evidence_present=historical_or_reference,
        required_qualifiers=tuple(qualifiers),
        prompt_evidence_source=(
            str(requirements.metadata.get("prompt_evidence_source") or "").strip()
            or None
        ),
        prompt_evidence_contract_missing=bool(
            requirements.metadata.get("prompt_evidence_contract_missing")
        ),
        reason_codes=tuple(reason_codes),
    )


def _public_assessment(
    *,
    candidates: list[RetrievalCandidateV2Schema],
    evidence_set: EvidenceSetAssessmentSchema,
    facts: _SetEvidenceFacts,
) -> RAGEvidenceAssessmentSchema:
    """Project internal assessment state into the public evidence-fact contract."""

    return RAGEvidenceAssessmentSchema(
        assessment_state="completed",
        status=evidence_set.status,
        evidence_available=bool(candidates),
        selected_evidence_count=len(candidates),
        judge_name="document_profile_gate_v2",
        judge_version="stage_d_v1",
        score=evidence_set.coverage_ratio,
        coverage_ratio=evidence_set.coverage_ratio,
        required_topics=list(evidence_set.required_topics),
        covered_topics=list(evidence_set.covered_topics),
        missing_topics=list(evidence_set.missing_topics),
        conflicts=[
            item.model_dump(mode="json", exclude_none=True)
            for item in evidence_set.conflicts
        ],
        supported_claim_types=sorted(
            facts.supported_claim_types, key=lambda item: item.value
        ),
        forbidden_claim_types=sorted(
            facts.forbidden_claim_types, key=lambda item: item.value
        ),
        authoritative_evidence_present=facts.authoritative_evidence_present,
        historical_or_reference_evidence_present=(
            facts.historical_or_reference_evidence_present
        ),
        required_qualifiers=list(facts.required_qualifiers),
        prompt_evidence_source=facts.prompt_evidence_source,
        prompt_evidence_contract_missing=(
            facts.prompt_evidence_contract_missing
        ),
        reason_codes=list(facts.reason_codes),
        details={"projection_source": "evidence_governance_v2"},
    )


class OnlineEvidenceGovernanceV2:
    """Evaluate retrieval or prompt-visible candidates without mutating rank."""

    @staticmethod
    def _finish(
        *,
        query: str,
        views: list[_AssessmentView],
        runtime_context: dict[str, Any] | None,
        rejected_ids: list[str],
        candidate_contract_errors: list[dict[str, Any]],
        assessment_view: str,
        prompt_evidence_source: str | None,
    ) -> EvidenceGovernanceV2Result:
        requirements = resolve_evidence_requirements_v2(
            query=query,
            runtime_context={
                **dict(runtime_context or {}),
                "prompt_evidence_source": prompt_evidence_source,
            },
        )
        candidates = [item.candidate for item in views]
        assessments = [
            _item_assessment(item, requirements, query) for item in views
        ]
        evidence_set = _set_assessment(candidates, assessments, requirements)
        evidence_facts = _set_evidence_facts(
            requirements, candidates, assessments, evidence_set
        )
        public_assessment = _public_assessment(
            candidates=candidates,
            evidence_set=evidence_set,
            facts=evidence_facts,
        )
        eligible_ids = tuple(
            item.evidence_id for item in assessments if item.eligibility
        )
        rejected_ids.extend(
            item.evidence_id for item in assessments if not item.eligibility
        )
        return EvidenceGovernanceV2Result(
            requirements=requirements,
            candidates=tuple(candidates),
            prompt_evidence_candidates=tuple(
                item.prompt_candidate
                for item in views
                if item.prompt_candidate is not None
            ),
            item_assessments=tuple(assessments),
            set_assessment=evidence_set,
            public_assessment=public_assessment,
            eligible_candidate_ids=eligible_ids,
            rejected_candidate_ids=tuple(_stable_unique(rejected_ids)),
            candidate_contract_errors=tuple(candidate_contract_errors),
            assessment_view=assessment_view,
            prompt_evidence_source=prompt_evidence_source,
        )

    def evaluate(
        self,
        *,
        query: str,
        results: list[dict[str, Any]],
        runtime_context: dict[str, Any] | None = None,
    ) -> EvidenceGovernanceV2Result:
        views: list[_AssessmentView] = []
        rejected_ids: list[str] = []
        candidate_contract_errors: list[dict[str, Any]] = []
        for rank, raw in enumerate(results, start=1):
            record = dict(raw)
            record.setdefault("rank", rank)
            try:
                candidate = build_retrieval_candidate_v2(record)
            except Exception as exc:
                candidate_id = str(
                    record.get("child_chunk_id")
                    or record.get("chunk_id")
                    or f"unprofiled_{rank}"
                )
                rejected_ids.append(candidate_id)
                candidate_contract_errors.append(
                    {
                        "candidate_id": candidate_id,
                        "error_type": type(exc).__name__,
                        "message": str(exc),
                    }
                )
                continue
            views.append(
                _AssessmentView(
                    candidate=candidate,
                    semantic_text=(
                        f"{candidate.match_text}\n{candidate.context_text}"
                    ),
                    topic_text=(
                        f"{candidate.match_text}\n{candidate.context_text}"
                    ),
                    fact_visible_in_prompt=True,
                )
            )
        return self._finish(
            query=query,
            views=views,
            runtime_context=runtime_context,
            rejected_ids=rejected_ids,
            candidate_contract_errors=candidate_contract_errors,
            assessment_view="retrieval",
            prompt_evidence_source=None,
        )

    def evaluate_prompt(
        self,
        *,
        query: str,
        prompt_candidates: list[PromptEvidenceCandidateV1Schema],
        runtime_context: dict[str, Any] | None = None,
        prompt_evidence_source: str = "context_pack_selected_results",
    ) -> EvidenceGovernanceV2Result:
        views = [
            _AssessmentView(
                candidate=item.retrieval_candidate,
                semantic_text=(
                    f"{item.retrieval_candidate.match_text}\n"
                    f"{item.prompt_visible_text}"
                ),
                topic_text=item.prompt_visible_text,
                fact_visible_in_prompt=item.fact_visible_in_prompt,
                prompt_candidate=item,
            )
            for item in prompt_candidates
        ]
        return self._finish(
            query=query,
            views=views,
            runtime_context=runtime_context,
            rejected_ids=[],
            candidate_contract_errors=[],
            assessment_view="prompt",
            prompt_evidence_source=prompt_evidence_source,
        )


# Import compatibility only; the public item/envelope authority lives in schemas.rag.
EvidenceRequirementsV2Schema = RAGEvidenceRequirementsPolicySchema


__all__ = [
    "EvidenceGovernanceV2Result",
    "EvidenceRequirementsV2Schema",
    "FactClaimPolicyResult",
    "OnlineEvidenceGovernanceV2",
    "RAGEvidenceRequirementsPolicySchema",
    "resolve_evidence_requirements_v2",
    "resolve_fact_claim_eligibility",
]
