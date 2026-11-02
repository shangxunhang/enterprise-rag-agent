"""规则 Claim、语义 Claim、合并、权威绑定与增量重抽取。"""

from __future__ import annotations

from copy import deepcopy
from decimal import Decimal, InvalidOperation
import hashlib
import json
import re
from dataclasses import dataclass
from typing import Any, Callable, Iterable, Mapping

from apps.enterprise_document.schemas.agent_rag_v1 import (
    ClaimLifecycleStatus,
    ClaimSchema,
    ClaimSourceBindingSchema,
    ClaimSupportType,
    ClaimVerificationStatus,
    ExcelDeviceFactSchema,
    FactSnapshotSchema,
    IncrementalClaimResultSchema,
)
from apps.enterprise_document.schemas.agent_rag_v1.claims import (
    ArtifactClaimCoverageUnitSchema,
    ClaimCandidateSchema,
    ClaimCoverageAccountingSchema,
    ClaimCoverageAccountingState,
    ClaimExtractionOrigin,
    ClaimOccurrenceRefSchema,
    ClaimRecordSchema,
    ClaimTextSpanSchema,
)
from apps.enterprise_document.schemas.agent_rag_v1.verification import (
    ClaimAuthorizedSourceRefSchema,
)
from core.hashing import canonical_sha256
from schemas.claim import ClaimType
from schemas.rag import EvidenceBundleSchema


def _claim_id(
    *,
    section_id: str,
    paragraph_id: str,
    claim_type: str,
    start: int,
    end: int,
    normalized_value: Any,
) -> str:
    payload = json.dumps(
        {
            "section_id": section_id,
            "paragraph_id": paragraph_id,
            "claim_type": claim_type,
            "start": start,
            "end": end,
            "value": str(normalized_value),
        },
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return "claim_" + hashlib.sha256(payload.encode("utf-8")).hexdigest()[:24]


def _decimal(value: str) -> Decimal:
    try:
        return Decimal(value.replace(",", "").strip())
    except InvalidOperation as exc:
        raise ValueError(f"invalid numeric claim: {value}") from exc


class DeterministicClaimExtractor:
    """从 Excel 设备别名附近抽取设备、型号、规格、数量与金额。"""

    _QUANTITY = re.compile(
        r"(?P<value>\d+(?:\.\d+)?)\s*(?P<unit>台|套|个|块|颗|TB|GB)"
    )
    _MODEL = re.compile(
        r"型号(?:为|是|：|:)?\s*(?P<value>[A-Za-z0-9][A-Za-z0-9._/-]*)",
        re.IGNORECASE,
    )
    _SPEC = re.compile(
        r"规格(?:为|是|：|:)?\s*(?P<value>[^，。；;\s]{1,64})"
    )
    _AMOUNT = re.compile(
        r"(?:金额|总价|投资)(?:为|是|：|:)?\s*"
        r"(?P<value>\d+(?:\.\d+)?)\s*(?P<unit>万元|元)"
    )

    def extract(
        self,
        *,
        section_id: str,
        paragraph_id: str,
        content: str,
        fact_snapshot: FactSnapshotSchema,
    ) -> list[ClaimSchema]:
        claims: list[ClaimSchema] = []
        for fact in fact_snapshot.devices:
            match = self._device_match(content, fact)
            if match is None:
                continue
            claims.append(
                self._claim(
                    section_id=section_id,
                    paragraph_id=paragraph_id,
                    claim_type="device",
                    start=match.start(),
                    end=match.end(),
                    value=fact.device_name,
                    unit=None,
                    fact=fact,
                    authoritative_value=fact.device_name,
                )
            )
            window_end = min(len(content), match.end() + 120)
            window = content[match.start() : window_end]
            claims.extend(
                self._extract_nearby_fields(
                    section_id=section_id,
                    paragraph_id=paragraph_id,
                    content=content,
                    window=window,
                    offset=match.start(),
                    fact=fact,
                )
            )
        return claims

    @staticmethod
    def _device_match(
        content: str,
        fact: ExcelDeviceFactSchema,
    ) -> re.Match[str] | None:
        candidates = sorted(
            {fact.device_name, *fact.aliases},
            key=lambda item: (-len(item), item),
        )
        matches = [
            re.search(re.escape(candidate), content, re.IGNORECASE)
            for candidate in candidates
        ]
        actual = [match for match in matches if match is not None]
        return min(actual, key=lambda match: match.start()) if actual else None

    def _extract_nearby_fields(
        self,
        *,
        section_id: str,
        paragraph_id: str,
        content: str,
        window: str,
        offset: int,
        fact: ExcelDeviceFactSchema,
    ) -> list[ClaimSchema]:
        claims: list[ClaimSchema] = []
        quantity = self._QUANTITY.search(window)
        if quantity is not None:
            claims.append(
                self._claim(
                    section_id=section_id,
                    paragraph_id=paragraph_id,
                    claim_type="quantity",
                    start=offset + quantity.start("value"),
                    end=offset + quantity.end("unit"),
                    value=_decimal(quantity.group("value")),
                    unit=quantity.group("unit"),
                    fact=fact,
                    authoritative_value=fact.quantity,
                )
            )
        model = self._MODEL.search(window)
        if model is not None:
            claims.append(
                self._claim(
                    section_id=section_id,
                    paragraph_id=paragraph_id,
                    claim_type="model",
                    start=offset + model.start("value"),
                    end=offset + model.end("value"),
                    value=model.group("value"),
                    unit=None,
                    fact=fact,
                    authoritative_value=fact.model,
                )
            )
        elif fact.model:
            literal = re.search(re.escape(fact.model), window, re.IGNORECASE)
            if literal is not None:
                claims.append(
                    self._claim(
                        section_id=section_id,
                        paragraph_id=paragraph_id,
                        claim_type="model",
                        start=offset + literal.start(),
                        end=offset + literal.end(),
                        value=fact.model,
                        unit=None,
                        fact=fact,
                        authoritative_value=fact.model,
                    )
                )
        specification = self._SPEC.search(window)
        if specification is not None:
            claims.append(
                self._claim(
                    section_id=section_id,
                    paragraph_id=paragraph_id,
                    claim_type="specification",
                    start=offset + specification.start("value"),
                    end=offset + specification.end("value"),
                    value=specification.group("value"),
                    unit=None,
                    fact=fact,
                    authoritative_value=fact.specification,
                )
            )
        amount = self._AMOUNT.search(window)
        if amount is not None:
            value = _decimal(amount.group("value"))
            if amount.group("unit") == "万元":
                value *= Decimal("10000")
            claims.append(
                self._claim(
                    section_id=section_id,
                    paragraph_id=paragraph_id,
                    claim_type="amount",
                    start=offset + amount.start("value"),
                    end=offset + amount.end("unit"),
                    value=value,
                    unit="元",
                    fact=fact,
                    authoritative_value=fact.total_amount,
                )
            )
        del content
        return claims

    @staticmethod
    def _claim(
        *,
        section_id: str,
        paragraph_id: str,
        claim_type: str,
        start: int,
        end: int,
        value: Any,
        unit: str | None,
        fact: ExcelDeviceFactSchema,
        authoritative_value: Any,
    ) -> ClaimSchema:
        return ClaimSchema(
            claim_id=_claim_id(
                section_id=section_id,
                paragraph_id=paragraph_id,
                claim_type=claim_type,
                start=start,
                end=end,
                normalized_value=value,
            ),
            section_id=section_id,
            paragraph_id=paragraph_id,
            claim_type=claim_type,
            text_span_ref=f"text://{paragraph_id}#{start}:{end}",
            normalized_value=value,
            unit=unit,
            metadata={
                "candidate_fact_id": fact.fact_id,
                "authoritative_value": authoritative_value,
                "authoritative_unit": fact.unit,
                "source_ref": fact.source_ref,
            },
        )


SemanticExtractorBackend = Callable[
    [str, str, str],
    Iterable[ClaimSchema | Mapping[str, Any]],
]


class SemanticClaimExtractor:
    """语义抽取器只负责结构化输出，来源绑定仍由 Resolver 决定。"""

    def __init__(self, backend: SemanticExtractorBackend | None = None) -> None:
        self.backend = backend

    def extract(
        self,
        *,
        section_id: str,
        paragraph_id: str,
        content: str,
    ) -> list[ClaimSchema]:
        if self.backend is None:
            return []
        return [
            ClaimSchema.model_validate(item)
            for item in self.backend(section_id, paragraph_id, content)
        ]


class ClaimMerger:
    def merge(self, *claim_groups: Iterable[ClaimSchema]) -> list[ClaimSchema]:
        merged: dict[tuple[str, str, str], ClaimSchema] = {}
        for claim in (item for group in claim_groups for item in group):
            key = (
                claim.claim_type,
                claim.text_span_ref,
                json.dumps(
                    claim.normalized_value,
                    ensure_ascii=False,
                    sort_keys=True,
                    default=str,
                ),
            )
            existing = merged.get(key)
            if existing is None:
                merged[key] = claim.model_copy(deep=True)
                continue
            bindings = {
                (
                    binding.source_type,
                    binding.source_ref,
                    binding.support_type,
                ): binding
                for binding in [
                    *existing.source_bindings,
                    *claim.source_bindings,
                ]
            }
            existing.source_bindings = list(bindings.values())
        return sorted(merged.values(), key=lambda item: item.claim_id)


class ClaimBindingResolver:
    """Legacy Excel binding helper plus Target Eligibility validation seam.

    ``resolve`` is compatibility-only and may project legacy verification fields.
    Target M6 callers must use ``validate_target_authorized_sources`` and then
    commit BindingFacts through ClaimVerificationRuntime.
    """

    def resolve(
        self,
        claims: Iterable[ClaimSchema],
        fact_snapshot: FactSnapshotSchema,
    ) -> list[ClaimSchema]:
        facts = {fact.fact_id: fact for fact in fact_snapshot.devices}
        resolved: list[ClaimSchema] = []
        for item in claims:
            claim = item.model_copy(deep=True)
            fact_id = str(claim.metadata.get("candidate_fact_id") or "")
            fact = facts.get(fact_id)
            if fact is None:
                resolved.append(claim)
                continue
            authoritative = claim.metadata.get("authoritative_value")
            matches = self._equals(claim.normalized_value, authoritative)
            if claim.claim_type == "quantity" and fact.unit and claim.unit:
                matches = matches and claim.unit == fact.unit
            if matches:
                claim.source_bindings = [
                    ClaimSourceBindingSchema(
                        source_type="excel_fact",
                        source_ref=fact.source_ref,
                        support_type=ClaimSupportType.AUTHORITATIVE,
                    )
                ]
                claim.verification_status = ClaimVerificationStatus.VERIFIED
            else:
                claim.source_bindings = []
                claim.verification_status = ClaimVerificationStatus.CONFLICTED
            resolved.append(claim)
        return resolved

    def validate_target_authorized_sources(
        self,
        *,
        claim_record: ClaimRecordSchema,
        bundle: EvidenceBundleSchema,
        exact_authorized_source_refs: Iterable[ClaimAuthorizedSourceRefSchema],
        evidence_id_for_source_ref: Callable[[ClaimAuthorizedSourceRefSchema], str],
    ) -> tuple[ClaimAuthorizedSourceRefSchema, ...]:
        return validate_target_authorized_evidence_sources(
            claim_record=claim_record,
            bundle=bundle,
            exact_authorized_source_refs=exact_authorized_source_refs,
            evidence_id_for_source_ref=evidence_id_for_source_ref,
        )

    @staticmethod
    def _equals(left: Any, right: Any) -> bool:
        if right is None:
            return False
        if isinstance(left, Decimal) or isinstance(right, Decimal):
            try:
                return Decimal(str(left)) == Decimal(str(right))
            except InvalidOperation:
                return False
        return str(left).strip().casefold() == str(right).strip().casefold()


class ClaimPipeline:
    def __init__(
        self,
        *,
        deterministic: DeterministicClaimExtractor | None = None,
        semantic: SemanticClaimExtractor | None = None,
        merger: ClaimMerger | None = None,
        binding_resolver: ClaimBindingResolver | None = None,
    ) -> None:
        self.deterministic = deterministic or DeterministicClaimExtractor()
        self.semantic = semantic or SemanticClaimExtractor()
        self.merger = merger or ClaimMerger()
        self.binding_resolver = binding_resolver or ClaimBindingResolver()

    def extract(
        self,
        *,
        section_id: str,
        paragraph_id: str,
        content: str,
        fact_snapshot: FactSnapshotSchema,
    ) -> list[ClaimSchema]:
        deterministic = self.deterministic.extract(
            section_id=section_id,
            paragraph_id=paragraph_id,
            content=content,
            fact_snapshot=fact_snapshot,
        )
        semantic = self.semantic.extract(
            section_id=section_id,
            paragraph_id=paragraph_id,
            content=content,
        )
        return self.binding_resolver.resolve(
            self.merger.merge(deterministic, semantic),
            fact_snapshot,
        )


class IncrementalClaimPipeline:
    def __init__(
        self,
        claim_pipeline: ClaimPipeline,
        *,
        full_section_reextract_threshold: float = 0.4,
    ) -> None:
        if not 0 < full_section_reextract_threshold <= 1:
            raise ValueError("claim reextract threshold must be in (0, 1]")
        self.claim_pipeline = claim_pipeline
        self.threshold = full_section_reextract_threshold

    def update(
        self,
        *,
        section_id: str,
        current_paragraphs: Mapping[str, str],
        previous_claims: Iterable[ClaimSchema],
        added_paragraph_ids: set[str],
        modified_paragraph_ids: set[str],
        deleted_paragraph_ids: set[str],
        fact_snapshot: FactSnapshotSchema,
    ) -> IncrementalClaimResultSchema:
        previous = [claim.model_copy(deep=True) for claim in previous_claims]
        previous_paragraph_ids = {claim.paragraph_id for claim in previous}
        total = max(
            1,
            len(previous_paragraph_ids | set(current_paragraphs)),
        )
        changed_ratio = (
            len(
                added_paragraph_ids
                | modified_paragraph_ids
                | deleted_paragraph_ids
            )
            / total
        )
        full = changed_ratio > self.threshold
        if full:
            invalidated = [
                claim.claim_id
                for claim in previous
                if claim.paragraph_id in current_paragraphs
            ]
            removed = [
                claim.claim_id
                for claim in previous
                if claim.paragraph_id not in current_paragraphs
            ]
            historical = []
            for claim in previous:
                if claim.paragraph_id in current_paragraphs:
                    claim.lifecycle_status = ClaimLifecycleStatus.INVALIDATED
                else:
                    claim.lifecycle_status = ClaimLifecycleStatus.REMOVED
                historical.append(claim)
            new_claims = [
                claim
                for paragraph_id, content in current_paragraphs.items()
                for claim in self.claim_pipeline.extract(
                    section_id=section_id,
                    paragraph_id=paragraph_id,
                    content=content,
                    fact_snapshot=fact_snapshot,
                )
            ]
            return IncrementalClaimResultSchema(
                claims=[*historical, *new_claims],
                invalidated_claim_ids=invalidated,
                removed_claim_ids=removed,
                full_section_reextract=True,
            )

        reused: list[str] = []
        invalidated: list[str] = []
        removed: list[str] = []
        result: list[ClaimSchema] = []
        for claim in previous:
            if claim.paragraph_id in deleted_paragraph_ids:
                claim.lifecycle_status = ClaimLifecycleStatus.REMOVED
                removed.append(claim.claim_id)
            elif claim.paragraph_id in modified_paragraph_ids:
                claim.lifecycle_status = ClaimLifecycleStatus.INVALIDATED
                invalidated.append(claim.claim_id)
            else:
                reused.append(claim.claim_id)
            result.append(claim)
        changed_ids = added_paragraph_ids | modified_paragraph_ids
        result.extend(
            claim
            for paragraph_id in sorted(changed_ids)
            for claim in self.claim_pipeline.extract(
                section_id=section_id,
                paragraph_id=paragraph_id,
                content=current_paragraphs[paragraph_id],
                fact_snapshot=fact_snapshot,
            )
        )
        return IncrementalClaimResultSchema(
            claims=result,
            reused_claim_ids=reused,
            invalidated_claim_ids=invalidated,
            removed_claim_ids=removed,
            full_section_reextract=False,
        )


@dataclass(frozen=True)
class TargetClaimMaterializationResult:
    """Candidate/coverage input only; no support or verification truth."""

    candidates: tuple[ClaimCandidateSchema, ...]
    coverage_accounting: ClaimCoverageAccountingSchema


class TargetClaimMaterializer:
    """Lower explicit canonical Claim semantics onto exact Paragraph versions.

    The caller must provide canonical public ClaimType. This class deliberately
    does not map legacy extractor-local names (quantity/model/amount/...) into
    the public taxonomy because no frozen mapping authority exists.
    """

    def build_candidate(
        self,
        *,
        run_id: str,
        section_id: str,
        paragraph_version_ref,
        paragraph_text: str,
        claim_type: ClaimType,
        start_offset: int,
        end_offset: int,
        normalized_value: Any,
        unit: str | None,
        extraction_origin: ClaimExtractionOrigin,
        extraction_occurrence_ref: str,
        span_locator_policy_id: str,
        span_locator_policy_version: str,
        normalization_policy_id: str,
        normalization_policy_version: str,
        claim_identity_policy_id: str,
        claim_identity_policy_version: str,
        candidate_capture_ref: str | None = None,
    ) -> ClaimCandidateSchema:
        canonical_type = ClaimType(claim_type)
        text = str(paragraph_text)
        if start_offset < 0 or end_offset <= start_offset or end_offset > len(text):
            raise ValueError("Target Claim span escapes exact Paragraph bytes")
        span_text = text[start_offset:end_offset]
        if not span_text:
            raise ValueError("Target Claim span cannot be empty")
        text_span_hash = hashlib.sha256(span_text.encode("utf-8")).hexdigest()
        normalized_value_hash = canonical_sha256(normalized_value)
        occurrence = ClaimOccurrenceRefSchema(
            run_id=run_id,
            section_id=section_id,
            paragraph_version_ref=paragraph_version_ref,
            claim_type=canonical_type.value,
            start_offset=start_offset,
            end_offset=end_offset,
            text_span_hash=text_span_hash,
            span_locator_policy_id=span_locator_policy_id,
            span_locator_policy_version=span_locator_policy_version,
            normalized_value_hash=normalized_value_hash,
            normalization_policy_id=normalization_policy_id,
            normalization_policy_version=normalization_policy_version,
            claim_identity_policy_id=claim_identity_policy_id,
            claim_identity_policy_version=claim_identity_policy_version,
        )
        return ClaimCandidateSchema(
            claim_occurrence_ref=occurrence,
            paragraph_version_ref=paragraph_version_ref,
            section_id=section_id,
            claim_type=canonical_type.value,
            text_span=ClaimTextSpanSchema(
                start_offset=start_offset,
                end_offset=end_offset,
                text=span_text,
            ),
            text_span_hash=text_span_hash,
            normalized_value=normalized_value,
            normalized_value_hash=normalized_value_hash,
            unit=unit,
            extraction_origin=extraction_origin,
            extraction_occurrence_ref=extraction_occurrence_ref,
            candidate_capture_ref=candidate_capture_ref,
        )

    def build_whole_paragraph_coverage(
        self,
        *,
        paragraph_version_ref,
        paragraph_text: str,
        candidates: Iterable[ClaimCandidateSchema],
        extraction_occurrence_ref: str,
        coverage_scope_ref: str,
        coverage_semantic_contract_version: str,
        no_applicable_factual_claim_proven: bool = False,
    ) -> ClaimCoverageAccountingSchema:
        text = str(paragraph_text)
        if not text:
            raise ValueError(
                "Target Claim coverage requires non-empty exact Paragraph bytes"
            )
        candidate_tuple = tuple(
            sorted(
                (
                    ClaimCandidateSchema.model_validate(item)
                    for item in candidates
                ),
                key=lambda item: (
                    item.text_span.start_offset,
                    item.text_span.end_offset,
                    item.claim_type,
                    item.normalized_value_hash,
                ),
            )
        )
        for item in candidate_tuple:
            if item.paragraph_version_ref != paragraph_version_ref:
                raise ValueError(
                    "Target Claim coverage candidate belongs to another Paragraph"
                )
            if item.extraction_occurrence_ref != extraction_occurrence_ref:
                raise ValueError(
                    "Target Claim coverage/extraction occurrence mismatch"
                )
            if (
                item.text_span.end_offset > len(text)
                or text[
                    item.text_span.start_offset : item.text_span.end_offset
                ]
                != item.text_span.text
            ):
                raise ValueError(
                    "Target Claim coverage candidate span disagrees with Paragraph"
                )

        if candidate_tuple:
            state = ClaimCoverageAccountingState.FACTUAL_CLAIMS_MATERIALIZED
        elif no_applicable_factual_claim_proven:
            state = ClaimCoverageAccountingState.NO_APPLICABLE_FACTUAL_CLAIM
        else:
            state = ClaimCoverageAccountingState.INDETERMINATE

        unit = ArtifactClaimCoverageUnitSchema(
            paragraph_version_ref=paragraph_version_ref,
            coverage_unit_id=(
                "claim-coverage-unit://sha256/"
                + canonical_sha256(
                    {
                        "paragraph_version_ref": paragraph_version_ref.model_dump(
                            mode="json"
                        ),
                        "start_offset": 0,
                        "end_offset": len(text),
                        "extraction_occurrence_ref": extraction_occurrence_ref,
                        "coverage_semantic_contract_version": (
                            coverage_semantic_contract_version
                        ),
                    }
                )
            ),
            start_offset=0,
            end_offset=len(text),
            accounting_state=state,
            claim_occurrence_refs=tuple(
                item.claim_occurrence_ref for item in candidate_tuple
            ),
        )
        return ClaimCoverageAccountingSchema(
            coverage_scope_ref=coverage_scope_ref,
            paragraph_version_refs=(paragraph_version_ref,),
            coverage_units=(unit,),
            accounting_state=state,
            extraction_occurrence_ref=extraction_occurrence_ref,
            coverage_semantic_contract_version=coverage_semantic_contract_version,
        )

    def materialize(
        self,
        *,
        paragraph_version_ref,
        paragraph_text: str,
        candidates: Iterable[ClaimCandidateSchema],
        extraction_occurrence_ref: str,
        coverage_scope_ref: str,
        coverage_semantic_contract_version: str,
        no_applicable_factual_claim_proven: bool = False,
    ) -> TargetClaimMaterializationResult:
        candidate_tuple = tuple(candidates)
        return TargetClaimMaterializationResult(
            candidates=candidate_tuple,
            coverage_accounting=self.build_whole_paragraph_coverage(
                paragraph_version_ref=paragraph_version_ref,
                paragraph_text=paragraph_text,
                candidates=candidate_tuple,
                extraction_occurrence_ref=extraction_occurrence_ref,
                coverage_scope_ref=coverage_scope_ref,
                coverage_semantic_contract_version=(
                    coverage_semantic_contract_version
                ),
                no_applicable_factual_claim_proven=(
                    no_applicable_factual_claim_proven
                ),
            ),
        )

    @staticmethod
    def eligible_evidence_ids(
        bundle: EvidenceBundleSchema,
        claim_type: ClaimType,
    ) -> tuple[str, ...]:
        """Project typed RAG Eligibility truth; never recreate it from metadata."""

        contract = EvidenceBundleSchema.model_validate(bundle)
        canonical_type = ClaimType(claim_type)
        if contract.assessment.assessment_state != "completed":
            raise ValueError(
                "Target Claim Binding requires completed EvidenceBundle assessment"
            )
        selected = set(contract.selected_evidence_ids)
        assessments = {
            item.evidence_id: item for item in contract.item_assessments
        }
        if len(assessments) != len(contract.item_assessments):
            raise ValueError("EvidenceBundle item assessment identities are ambiguous")
        eligible: list[str] = []
        for evidence_id in sorted(selected):
            assessment = assessments.get(evidence_id)
            if assessment is None:
                raise ValueError(
                    "selected Evidence has no exact typed Eligibility assessment"
                )
            if (
                assessment.eligibility
                and canonical_type in assessment.supported_claim_types
            ):
                eligible.append(evidence_id)
        return tuple(eligible)


def _target_claim_type(record: ClaimRecordSchema) -> ClaimType:
    try:
        return ClaimType(record.claim_type)
    except ValueError as exc:
        raise ValueError(
            "Target ClaimRecord claim_type is outside canonical public ClaimType"
        ) from exc


def validate_target_authorized_evidence_sources(
    *,
    claim_record: ClaimRecordSchema,
    bundle: EvidenceBundleSchema,
    exact_authorized_source_refs: Iterable[ClaimAuthorizedSourceRefSchema],
    evidence_id_for_source_ref: Callable[[ClaimAuthorizedSourceRefSchema], str],
) -> tuple[ClaimAuthorizedSourceRefSchema, ...]:
    """Validate caller-owned exact source refs against RAG-owned Eligibility.

    M6 does not invent an Evidence source URI. The caller supplies exact source
    refs plus a resolver that maps each exact ref back to canonical Evidence ID.
    """

    sources = tuple(
        ClaimAuthorizedSourceRefSchema.model_validate(item)
        for item in exact_authorized_source_refs
    )
    eligible = set(
        TargetClaimMaterializer.eligible_evidence_ids(
            bundle,
            _target_claim_type(claim_record),
        )
    )
    seen: set[tuple[str, str]] = set()
    for source in sources:
        if source.source_kind != "EVIDENCE":
            raise ValueError(
                "Target Evidence eligibility validation only accepts EVIDENCE sources"
            )
        evidence_id = str(evidence_id_for_source_ref(source) or "").strip()
        if not evidence_id:
            raise ValueError("exact Evidence source ref did not resolve evidence_id")
        if evidence_id not in eligible:
            raise ValueError(
                "exact Evidence source is not Claim-eligible in canonical Bundle"
            )
        key = (source.source_kind, source.exact_source_ref)
        if key in seen:
            raise ValueError("exact authorized Evidence source refs must be unique")
        seen.add(key)
    return tuple(
        sorted(
            sources,
            key=lambda item: (
                item.source_kind,
                item.exact_source_ref,
                item.source_scope_ref or "",
            ),
        )
    )
