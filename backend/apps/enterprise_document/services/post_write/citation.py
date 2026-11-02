"""Citation Integrity and Grounding projections for Target Post-write V1.

Citation Integrity is deterministic structural/source-binding truth. Citation
Grounding consumes the same canonical CLAIM_EVIDENCE_SUPPORT authority used by
Faithfulness, but over citation-specific exact Evidence target sets.
"""

from __future__ import annotations

from typing import Iterable

from apps.enterprise_document.schemas.agent_rag_v1.claim_support import (
    ClaimEvidenceSupportRecordSchema,
    EvidenceTargetSetRefSchema,
    SupportRelation,
)
from apps.enterprise_document.schemas.agent_rag_v1.verification import (
    ClaimOperationContextSchema,
)
from apps.enterprise_document.schemas.post_write.foundation import (
    ValidationFactFamily,
    ValidationFactSchema,
    ValidationSemanticState,
)
from apps.enterprise_document.schemas.post_write.quality import (
    ArtifactClaimTargetSchema,
    CitationClaimAssociationSchema,
    CitationAssociationEvidenceSchema, CitationGroundingUncertaintyPayloadSchema,
    CitationClaimAssociationState,
    CitationGroundingGroupKind,
    CitationGroundingPayloadSchema,
    CitationGroundingRelation,
    CitationGroundingTargetSchema,
    CitationIntegrityPayloadSchema,
    CitationOccurrenceBindingState,
    CitationOccurrenceTargetSchema,
    CitationTargetRefSchema,
)
from apps.enterprise_document.services.post_write.shared_claim_support import (
    ClaimTargetSupportSubjectAdapter,
    SharedClaimEvidenceSupportEvaluator,
)
from core.hashing import canonical_sha256


class CitationQualityIntegrityError(ValueError):
    """Citation target/binding/support integrity failure."""


def _canonical_strings(values: Iterable[str]) -> tuple[str, ...]:
    return tuple(sorted({str(item).strip() for item in values if str(item).strip()}))


def _validation_fact(
    *,
    family: ValidationFactFamily,
    contract_version: str,
    validation_run_id: str,
    subject_hash: str,
    producer_validator_id: str,
    producer_validator_version: str,
    target_ref: str,
    semantic_state: ValidationSemanticState,
    payload: dict,
) -> ValidationFactSchema:
    identity = {
        "family": family.value,
        "validation_run_id": validation_run_id,
        "subject_hash": subject_hash,
        "target_ref": target_ref,
        "contract_version": contract_version,
        "payload": payload,
    }
    fact_id = (
        "validation-fact:"
        + family.value.lower().replace("_", "-")
        + ":"
        + canonical_sha256(identity)
    )
    base = {
        "schema_version": "validation_fact_v1",
        "contract_version": contract_version,
        "fact_id": fact_id,
        "validation_run_id": validation_run_id,
        "subject_hash": subject_hash,
        "producer_validator_id": producer_validator_id,
        "producer_validator_version": producer_validator_version,
        "family": family,
        "target_ref": target_ref,
        "semantic_state": semantic_state,
        "payload": payload,
    }
    return ValidationFactSchema(
        **base,
        fact_sha256=canonical_sha256(base),
    )


class CitationIntegrityValidator:
    VALIDATOR_ID = "post_write_citation_integrity"
    VALIDATOR_VERSION = "v1"
    FACT_CONTRACT_VERSION = "citation_integrity_fact_v1"

    _STATE = {
        CitationOccurrenceBindingState.VALID: ValidationSemanticState.PASS,
        CitationOccurrenceBindingState.INVALID: ValidationSemanticState.FAIL,
        CitationOccurrenceBindingState.INDETERMINATE: (
            ValidationSemanticState.INDETERMINATE
        ),
    }

    @classmethod
    def project(
        cls,
        *,
        validation_run_id: str,
        subject_hash: str,
        occurrence: CitationOccurrenceTargetSchema,
        binding_state: CitationOccurrenceBindingState,
        citation_target_ref: CitationTargetRefSchema | None,
        reason_codes: Iterable[str] = (),
    ) -> ValidationFactSchema:
        occurrence = CitationOccurrenceTargetSchema.model_validate(occurrence)
        if occurrence.validation_subject_hash != subject_hash:
            raise CitationQualityIntegrityError(
                "Citation occurrence belongs to a different ValidationSubject"
            )
        payload = CitationIntegrityPayloadSchema(
            occurrence_ref=occurrence.occurrence_ref,
            binding_state=binding_state,
            citation_target_ref=citation_target_ref,
            reason_codes=_canonical_strings(reason_codes),
        )
        return _validation_fact(
            family=ValidationFactFamily.CITATION_INTEGRITY,
            contract_version=cls.FACT_CONTRACT_VERSION,
            validation_run_id=validation_run_id,
            subject_hash=subject_hash,
            producer_validator_id=cls.VALIDATOR_ID,
            producer_validator_version=cls.VALIDATOR_VERSION,
            target_ref=occurrence.occurrence_ref,
            semantic_state=cls._STATE[binding_state],
            payload=payload.model_dump(mode="json"),
        )


    @classmethod
    def project_rule(
        cls, *, validation_run_id: str, subject_hash: str,
        occurrence: CitationOccurrenceTargetSchema, rule_id: str,
        binding_state: CitationOccurrenceBindingState,
        citation_target_ref: CitationTargetRefSchema | None = None,
        reason_codes: Iterable[str] = (), alias_catalog_ref: str,
    ) -> ValidationFactSchema:
        """Project one frozen structural rule, without conflating syntax/source.

        The older combined project() contract stays unchanged. A syntactically
        valid occurrence can lack a source; only source-resolution VALID requires
        a usable CitationTargetRef.
        """
        if rule_id not in {"PW-R-CIT-I-01", "PW-R-CIT-I-02", "PW-R-CIT-I-03"}:
            raise CitationQualityIntegrityError("unknown Citation Integrity rule")
        occurrence = CitationOccurrenceTargetSchema.model_validate(occurrence)
        state = CitationOccurrenceBindingState(binding_state)
        if occurrence.validation_subject_hash != subject_hash:
            raise CitationQualityIntegrityError("Citation rule belongs to another ValidationSubject")
        if rule_id != "PW-R-CIT-I-01" and state is CitationOccurrenceBindingState.VALID and citation_target_ref is None:
            raise CitationQualityIntegrityError("source-rule VALID requires an exact Citation target")
        if not str(alias_catalog_ref).strip():
            raise CitationQualityIntegrityError("Citation rule requires an exact alias catalog")
        payload = {
            "schema_version": "citation_integrity_rule_payload_v1", "rule_id": rule_id,
            "occurrence_ref": occurrence.occurrence_ref, "binding_state": state.value,
            "citation_target_ref": citation_target_ref.model_dump(mode="json") if citation_target_ref else None,
            "alias_catalog_ref": alias_catalog_ref, "reason_codes": list(_canonical_strings(reason_codes)),
        }
        return _validation_fact(
            family=ValidationFactFamily.CITATION_INTEGRITY, contract_version="citation_integrity_rule_fact_v1",
            validation_run_id=validation_run_id, subject_hash=subject_hash,
            producer_validator_id=cls.VALIDATOR_ID, producer_validator_version="v2",
            target_ref=occurrence.occurrence_ref, semantic_state=cls._STATE[state], payload=payload,
        )


class CitationEvidenceTargetResolver:
    """Materializes an exact citation-specific Evidence target; never widens."""

    def __init__(self, support_adapter: SharedClaimEvidenceSupportEvaluator) -> None:
        self.support_adapter = support_adapter

    def resolve(
        self,
        *,
        claim_target: ArtifactClaimTargetSchema,
        citation_target_ref: CitationTargetRefSchema,
        selected_evidence_refs: Iterable[str],
        target_set_contract_version: str,
    ) -> EvidenceTargetSetRefSchema:
        citation = CitationTargetRefSchema.model_validate(citation_target_ref)
        selected = set(_canonical_strings(selected_evidence_refs))
        if citation.exact_evidence_ref not in selected:
            raise CitationQualityIntegrityError(
                "citation-specific Evidence target is outside exact selected Evidence domain"
            )
        return self.support_adapter.materialize_evidence_target_set(
            claim_target=claim_target,
            evidence_member_refs=(citation.exact_evidence_ref,),
            target_set_contract_version=target_set_contract_version,
        )


class CitationGroundingTargetMaterializer:
    """Builds one exact Claim + citation group target after semantic association."""

    def __init__(self, support_adapter: SharedClaimEvidenceSupportEvaluator) -> None:
        self.support_adapter = support_adapter

    def materialize(
        self,
        *,
        claim_target: ArtifactClaimTargetSchema,
        associations: Iterable[CitationClaimAssociationSchema],
        occurrences: Iterable[CitationOccurrenceTargetSchema],
        citation_targets: Iterable[CitationTargetRefSchema],
        individual_evidence_target_sets: Iterable[EvidenceTargetSetRefSchema],
        joint_target_set_contract_version: str,
    ) -> CitationGroundingTargetSchema:
        claim = ArtifactClaimTargetSchema.model_validate(claim_target)
        association_items = tuple(
            CitationClaimAssociationSchema.model_validate(item)
            for item in associations
        )
        occurrence_items = tuple(
            CitationOccurrenceTargetSchema.model_validate(item)
            for item in occurrences
        )
        citation_items = tuple(
            CitationTargetRefSchema.model_validate(item)
            for item in citation_targets
        )
        individual_sets = tuple(
            EvidenceTargetSetRefSchema.model_validate(item)
            for item in individual_evidence_target_sets
        )
        if not occurrence_items:
            raise CitationQualityIntegrityError(
                "CitationGroundingTarget requires at least one occurrence"
            )
        if not (
            len(association_items)
            == len(occurrence_items)
            == len(citation_items)
            == len(individual_sets)
        ):
            raise CitationQualityIntegrityError(
                "citation association/occurrence/target/Evidence cardinality mismatch"
            )

        association_by_occurrence = {
            item.occurrence_ref: item for item in association_items
        }
        citation_by_occurrence = {
            item.occurrence_ref: citation
            for item, citation in zip(occurrence_items, citation_items)
        }
        evidence_by_occurrence = {
            item.occurrence_ref: target
            for item, target in zip(occurrence_items, individual_sets)
        }
        if (
            len(association_by_occurrence) != len(occurrence_items)
            or len(citation_by_occurrence) != len(occurrence_items)
            or len(evidence_by_occurrence) != len(occurrence_items)
        ):
            raise CitationQualityIntegrityError(
                "citation grounding group contains duplicate occurrence identity"
            )

        expected_subject = ClaimTargetSupportSubjectAdapter.adapt(claim)
        ordered_occurrences = tuple(
            sorted(occurrence_items, key=lambda item: item.occurrence_ref)
        )
        ordered_associations = []
        ordered_citations = []
        ordered_sets = []
        union_members: set[str] = set()

        for occurrence in ordered_occurrences:
            if (
                occurrence.artifact_state_ref != claim.artifact_state_ref
                or occurrence.validation_subject_hash
                != claim.validation_subject_hash
            ):
                raise CitationQualityIntegrityError(
                    "citation occurrence belongs to a different Artifact/ValidationSubject"
                )
            association = association_by_occurrence.get(occurrence.occurrence_ref)
            if association is None:
                raise CitationQualityIntegrityError("missing exact citation association")
            if association.artifact_state_ref != claim.artifact_state_ref:
                raise CitationQualityIntegrityError(
                    "citation association belongs to a different Artifact"
                )
            if association.association_state is not CitationClaimAssociationState.ASSOCIATED:
                raise CitationQualityIntegrityError(
                    "only ASSOCIATED citations may materialize a GroundingTarget"
                )
            if claim.claim_target_id not in association.associated_claim_target_ids:
                raise CitationQualityIntegrityError(
                    "citation association does not bind the requested ClaimTarget"
                )
            target_set = evidence_by_occurrence[occurrence.occurrence_ref]
            if target_set.claim_support_subject_ref != expected_subject:
                raise CitationQualityIntegrityError(
                    "citation Evidence target set belongs to a different Claim subject"
                )
            citation = citation_by_occurrence[occurrence.occurrence_ref]
            if citation.exact_evidence_ref not in target_set.evidence_member_refs:
                raise CitationQualityIntegrityError(
                    "citation exact Evidence ref is absent from its individual target set"
                )
            union_members.update(target_set.evidence_member_refs)
            ordered_associations.append(association)
            ordered_citations.append(citation)
            ordered_sets.append(target_set)

        joint = self.support_adapter.materialize_evidence_target_set(
            claim_target=claim,
            evidence_member_refs=tuple(sorted(union_members)),
            target_set_contract_version=joint_target_set_contract_version,
        )
        group_kind = (
            CitationGroundingGroupKind.SINGLE
            if len(ordered_occurrences) == 1
            else CitationGroundingGroupKind.JOINT
        )
        base = {
            "artifact_state_ref": claim.artifact_state_ref.model_dump(mode="json"),
            "validation_subject_hash": claim.validation_subject_hash,
            "claim_target_id": claim.claim_target_id,
            "group_kind": group_kind.value,
            "association_refs": tuple(sorted(
                item.association_ref for item in ordered_associations
            )),
            "occurrence_refs": tuple(
                item.occurrence_ref for item in ordered_occurrences
            ),
            "citation_target_refs": tuple(
                item.model_dump(mode="json") for item in ordered_citations
            ),
            "individual_evidence_target_set_refs": tuple(
                item.model_dump(mode="json") for item in ordered_sets
            ),
            "joint_evidence_target_set_ref": joint.model_dump(mode="json"),
        }
        target_hash = canonical_sha256(base)
        return CitationGroundingTargetSchema(
            grounding_target_ref=(
                "citation-grounding-target://sha256/" + target_hash
            ),
            artifact_state_ref=claim.artifact_state_ref,
            validation_subject_hash=claim.validation_subject_hash,
            claim_target_id=claim.claim_target_id,
            group_kind=group_kind,
            association_refs=tuple(sorted(
                item.association_ref for item in ordered_associations
            )),
            occurrence_refs=tuple(
                item.occurrence_ref for item in ordered_occurrences
            ),
            citation_target_refs=tuple(ordered_citations),
            individual_evidence_target_set_refs=tuple(ordered_sets),
            joint_evidence_target_set_ref=joint,
            target_hash=target_hash,
        )


class CitationGroundingProjector:
    VALIDATOR_ID = "post_write_citation_grounding"
    VALIDATOR_VERSION = "v2"
    FACT_CONTRACT_VERSION = "citation_grounding_fact_v2"

    _RELATION = {
        SupportRelation.SUPPORTED: CitationGroundingRelation.GROUNDED,
        SupportRelation.PARTIALLY_SUPPORTED: (
            CitationGroundingRelation.PARTIALLY_GROUNDED
        ),
        SupportRelation.NOT_SUPPORTED: CitationGroundingRelation.NOT_GROUNDED,
        SupportRelation.CONTRADICTED: CitationGroundingRelation.CONTRADICTED,
        SupportRelation.INDETERMINATE: CitationGroundingRelation.INDETERMINATE,
    }
    _STATE = {
        SupportRelation.SUPPORTED: ValidationSemanticState.PASS,
        SupportRelation.PARTIALLY_SUPPORTED: ValidationSemanticState.FAIL,
        SupportRelation.NOT_SUPPORTED: ValidationSemanticState.FAIL,
        SupportRelation.CONTRADICTED: ValidationSemanticState.FAIL,
        SupportRelation.INDETERMINATE: ValidationSemanticState.INDETERMINATE,
    }

    @classmethod
    def project(
        cls,
        *,
        validation_run_id: str,
        subject_hash: str,
        grounding_target: CitationGroundingTargetSchema,
        joint_support_record: ClaimEvidenceSupportRecordSchema,
        member_support_records: Iterable[ClaimEvidenceSupportRecordSchema] = (),
    ) -> ValidationFactSchema:
        target = CitationGroundingTargetSchema.model_validate(grounding_target)
        if target.validation_subject_hash != subject_hash:
            raise CitationQualityIntegrityError(
                "CitationGroundingTarget belongs to a different ValidationSubject"
            )
        support = ClaimEvidenceSupportRecordSchema.model_validate(
            joint_support_record
        )
        members = tuple(
            ClaimEvidenceSupportRecordSchema.model_validate(item)
            for item in member_support_records
        )
        if support.evidence_target_set_ref != target.joint_evidence_target_set_ref:
            raise CitationQualityIntegrityError(
                "Citation Grounding joint support binds a different Evidence target set"
            )

        member_by_target = {
            item.evidence_target_set_ref.evidence_target_set_id: item
            for item in members
        }
        expected_member_target_ids = {
            item.evidence_target_set_id
            for item in target.individual_evidence_target_set_refs
        }
        if not set(member_by_target) <= expected_member_target_ids:
            raise CitationQualityIntegrityError(
                "Citation Grounding contains support for an unrelated Evidence target set"
            )
        for target_set in target.individual_evidence_target_set_refs:
            if (
                target_set.evidence_target_set_id
                not in member_by_target
                and target_set != target.joint_evidence_target_set_ref
            ):
                raise CitationQualityIntegrityError(
                    "Citation Grounding missing individual support accounting"
                )

        observation_codes: set[str] = set()
        for record in members:
            if record.relation is SupportRelation.PARTIALLY_SUPPORTED:
                observation_codes.add(
                    "CITED_SOURCE_PARTIALLY_SUPPORTS_ASSOCIATED_CLAIM"
                )
            elif record.relation is SupportRelation.NOT_SUPPORTED:
                observation_codes.add(
                    "VALID_CITATION_SOURCE_NOT_SUPPORTING_ASSOCIATED_CLAIM"
                )
            elif record.relation is SupportRelation.CONTRADICTED:
                observation_codes.add(
                    "CITED_SOURCE_CONTRADICTS_ASSOCIATED_CLAIM"
                )

        payload = CitationGroundingPayloadSchema(
            schema_version="citation_grounding_payload_v2",
            grounding_target_ref=target.grounding_target_ref,
            claim_target_id=target.claim_target_id,
            occurrence_refs=target.occurrence_refs,
            citation_target_refs=tuple(
                item.citation_target_ref for item in target.citation_target_refs
            ),
            joint_support_ref=support.support_ref,
            member_support_refs=tuple(
                item.support_ref
                for item in sorted(
                    members,
                    key=lambda item: item.support_ref.claim_evidence_support_id,
                )
            ),
            grounding_relation=cls._RELATION[support.relation],
            observation_codes=tuple(sorted(observation_codes)),
            related_refs=tuple(
                sorted(
                    {
                        support.support_ref.claim_evidence_support_id,
                        *(
                            item.support_ref.claim_evidence_support_id
                            for item in members
                        ),
                    }
                )
            ),
        )
        return _validation_fact(
            family=ValidationFactFamily.CITATION_GROUNDING,
            contract_version=cls.FACT_CONTRACT_VERSION,
            validation_run_id=validation_run_id,
            subject_hash=subject_hash,
            producer_validator_id=cls.VALIDATOR_ID,
            producer_validator_version=cls.VALIDATOR_VERSION,
            target_ref=target.grounding_target_ref,
            semantic_state=cls._STATE[support.relation],
            payload=payload.model_dump(mode="json"),
        )


class CitationGroundingValidator:
    """Evaluates individual + joint exact targets through shared support authority."""

    def __init__(
        self,
        *,
        support_adapter: SharedClaimEvidenceSupportEvaluator,
    ) -> None:
        self.support_adapter = support_adapter

    def evaluate(
        self,
        *,
        validation_run_id: str,
        subject_hash: str,
        claim_target: ArtifactClaimTargetSchema,
        grounding_target: CitationGroundingTargetSchema,
        support_semantic_contract_version: str,
        operation_context: ClaimOperationContextSchema,
    ) -> tuple[
        tuple[ClaimEvidenceSupportRecordSchema, ...],
        ClaimEvidenceSupportRecordSchema,
        ValidationFactSchema,
    ]:
        claim = ArtifactClaimTargetSchema.model_validate(claim_target)
        target = CitationGroundingTargetSchema.model_validate(grounding_target)
        if (
            claim.validation_subject_hash != subject_hash
            or target.validation_subject_hash != subject_hash
        ):
            raise CitationQualityIntegrityError(
                "Citation Grounding inputs belong to a different ValidationSubject"
            )
        if (
            target.claim_target_id != claim.claim_target_id
            or target.artifact_state_ref != claim.artifact_state_ref
            or target.validation_subject_hash != claim.validation_subject_hash
        ):
            raise CitationQualityIntegrityError(
                "CitationGroundingTarget belongs to a different Claim/subject"
            )

        member_records_by_id: dict[str, ClaimEvidenceSupportRecordSchema] = {}
        for target_set in target.individual_evidence_target_set_refs:
            record = self.support_adapter.evaluate_and_commit(
                claim_target=claim,
                evidence_target_set_ref=target_set,
                support_semantic_contract_version=support_semantic_contract_version,
                operation_context=operation_context,
            )
            member_records_by_id[
                record.support_ref.claim_evidence_support_id
            ] = record

        joint = self.support_adapter.evaluate_and_commit(
            claim_target=claim,
            evidence_target_set_ref=target.joint_evidence_target_set_ref,
            support_semantic_contract_version=support_semantic_contract_version,
            operation_context=operation_context,
        )
        member_records = tuple(
            sorted(
                member_records_by_id.values(),
                key=lambda item: item.support_ref.claim_evidence_support_id,
            )
        )
        fact = CitationGroundingProjector.project(
            validation_run_id=validation_run_id,
            subject_hash=subject_hash,
            grounding_target=target,
            joint_support_record=joint,
            member_support_records=member_records,
        )
        return member_records, joint, fact


__all__ = [
    "CitationEvidenceTargetResolver",
    "CitationGroundingProjector",
    "CitationGroundingTargetMaterializer",
    "CitationGroundingValidator",
    "CitationIntegrityValidator",
    "CitationQualityIntegrityError",
]


class CitationGroundingUncertaintyProjector:
    """Read committed observations; no model, support evaluation or applicability guess."""
    VALIDATOR_ID = "post_write_citation_grounding"
    VALIDATOR_VERSION = "v3"
    FACT_CONTRACT_VERSION = "citation_grounding_uncertainty_fact_v1"

    @classmethod
    def project(cls, *, store, intermediate_runtime, validation_run_id, subject_hash,
                artifact_state_ref, occurrence, association_evidence_ref=None,
                prerequisite_fact_ref=None, prerequisite_occurrence_ref=None, input_receipt_resolver=None):
        from apps.enterprise_document.schemas.post_write.foundation import ValidationFactRefSchema, ValidationIntermediateRefSchema
        occurrence = CitationOccurrenceTargetSchema.model_validate(occurrence)
        if association_evidence_ref is not None:
            association_evidence_ref = ValidationIntermediateRefSchema.model_validate(association_evidence_ref)
        if prerequisite_fact_ref is not None:
            prerequisite_fact_ref = ValidationFactRefSchema.model_validate(prerequisite_fact_ref)
        if prerequisite_occurrence_ref is not None:
            prerequisite_occurrence_ref = ValidationIntermediateRefSchema.model_validate(prerequisite_occurrence_ref)
        run = store.resolve_run_exact(validation_run_id)
        from .persistence import PostWriteEntryStore
        entry = PostWriteEntryStore(store.database).resolve_exact(run.subject_key.post_write_entry_ref)
        if entry.identity.artifact_state_ref != artifact_state_ref:
            raise CitationQualityIntegrityError("uncertainty Artifact differs from committed ValidationSubject Entry")
        if (run.subject_key.subject_hash != subject_hash
                or occurrence.validation_subject_hash != subject_hash
                or occurrence.artifact_state_ref != artifact_state_ref):
            raise CitationQualityIntegrityError("uncertainty Run/subject/Artifact/occurrence mismatch")
        if (association_evidence_ref is None) == (prerequisite_fact_ref is None):
            raise CitationQualityIntegrityError("uncertainty requires exactly one committed semantic basis")
        if association_evidence_ref is not None:
            evidence = intermediate_runtime.resolve_typed(association_evidence_ref, CitationAssociationEvidenceSchema)
            if (evidence.validation_run_id != validation_run_id or evidence.subject_hash != subject_hash
                    or evidence.occurrence != occurrence
                    or evidence.association.association_state.value != "INDETERMINATE"):
                raise CitationQualityIntegrityError("association uncertainty basis is not exact INDETERMINATE")
            if input_receipt_resolver is None:
                raise CitationQualityIntegrityError("association uncertainty requires exact committed input receipt resolver")
            receipt = input_receipt_resolver(evidence.input_receipt_ref)
            material = receipt["input_material"]
            if (canonical_sha256(material) != evidence.input_hash or receipt["input_hash"] != evidence.input_hash
                    or material["validation_run_id"] != validation_run_id or material["subject_hash"] != subject_hash
                    or material["artifact_state_ref"] != artifact_state_ref.model_dump(mode="json")
                    or material["policy_id"] != evidence.policy_id or material["policy_version"] != evidence.policy_version):
                raise CitationQualityIntegrityError("association uncertainty input receipt binding differs")
            observed = [row for row in material["occurrences"] if row["target"]["occurrence_ref"] == occurrence.occurrence_ref]
            if len(observed) != 1 or observed[0]["target"] != occurrence.model_dump(mode="json"):
                raise CitationQualityIntegrityError("association uncertainty occurrence not in committed input")
            candidates = tuple(sorted(row["claim_target_id"] for row in material["candidate_claim_targets"]))
            if evidence.association.candidate_claim_target_ids != candidates:
                raise CitationQualityIntegrityError("association uncertainty canonical candidate set differs")
            result = input_receipt_resolver(evidence.input_receipt_ref.removesuffix("/input") + "/result")
            rows = result.get("candidate", {}).get("associations", [])
            selected = [row for row in rows if row.get("occurrence_ref") == occurrence.occurrence_ref]
            if (result.get("input_hash") != evidence.input_hash or result.get("outcome") != "completed"
                    or len(selected) != 1 or selected[0].get("state") != "INDETERMINATE"
                    or selected[0].get("associated_claim_target_ids") != []):
                raise CitationQualityIntegrityError("association uncertainty has no matching completed semantic receipt")
            kind, policy_id, policy_version, basis_hash = "ASSOCIATION_INDETERMINATE", evidence.policy_id, evidence.policy_version, evidence.evidence_hash
        else:
            if prerequisite_occurrence_ref is None or prerequisite_occurrence_ref.validation_run_id != validation_run_id:
                raise CitationQualityIntegrityError("prerequisite requires committed exact occurrence basis")
            committed_occurrence = intermediate_runtime.resolve_typed(prerequisite_occurrence_ref, CitationOccurrenceTargetSchema)
            if committed_occurrence != occurrence:
                raise CitationQualityIntegrityError("prerequisite occurrence differs from committed basis")
            fact = store.resolve_fact(prerequisite_fact_ref)
            if (fact.validation_run_id != validation_run_id or fact.subject_hash != subject_hash
                    or fact.family is not ValidationFactFamily.CITATION_INTEGRITY
                    or fact.semantic_state is not ValidationSemanticState.INDETERMINATE
                    or fact.producer_validator_id != CitationIntegrityValidator.VALIDATOR_ID
                    or fact.target_ref != occurrence.occurrence_ref
                    or fact.payload.get("occurrence_ref") != occurrence.occurrence_ref
                    or fact.payload.get("binding_state") != "INDETERMINATE"):
                raise CitationQualityIntegrityError("prerequisite uncertainty basis is not exact semantic INDETERMINATE")
            if fact.contract_version == CitationIntegrityValidator.FACT_CONTRACT_VERSION:
                typed = CitationIntegrityPayloadSchema.model_validate(fact.payload)
                expected = CitationIntegrityValidator.project(validation_run_id=validation_run_id, subject_hash=subject_hash,
                    occurrence=occurrence, binding_state=typed.binding_state, citation_target_ref=typed.citation_target_ref,
                    reason_codes=typed.reason_codes)
            elif fact.contract_version == "citation_integrity_rule_fact_v1":
                payload = fact.payload
                expected = CitationIntegrityValidator.project_rule(validation_run_id=validation_run_id,subject_hash=subject_hash,
                    occurrence=occurrence,rule_id=payload["rule_id"],binding_state=payload["binding_state"],
                    citation_target_ref=CitationTargetRefSchema.model_validate(payload["citation_target_ref"]) if payload["citation_target_ref"] else None,
                    reason_codes=payload["reason_codes"],alias_catalog_ref=payload["alias_catalog_ref"])
            else:
                raise CitationQualityIntegrityError("unsupported prerequisite Fact contract")
            if fact != expected:
                raise CitationQualityIntegrityError("prerequisite Fact differs from canonical semantic projection")
            kind, policy_id, policy_version, basis_hash = "PREREQUISITE_INDETERMINATE", fact.producer_validator_id, fact.producer_validator_version, fact.fact_sha256
        payload = CitationGroundingUncertaintyPayloadSchema(uncertainty_kind=kind,
            validation_run_id=validation_run_id, subject_hash=subject_hash, artifact_state_ref=artifact_state_ref,
            occurrence=occurrence, association_evidence_ref=association_evidence_ref,
            prerequisite_fact_ref=prerequisite_fact_ref, prerequisite_occurrence_ref=prerequisite_occurrence_ref, policy_id=policy_id, policy_version=policy_version, basis_hash=basis_hash)
        return _validation_fact(family=ValidationFactFamily.CITATION_GROUNDING, contract_version=cls.FACT_CONTRACT_VERSION,
            validation_run_id=validation_run_id, subject_hash=subject_hash, producer_validator_id=cls.VALIDATOR_ID,
            producer_validator_version=cls.VALIDATOR_VERSION,
            target_ref="citation-grounding-uncertainty://sha256/" + canonical_sha256(payload.model_dump(mode="json")),
            semantic_state=ValidationSemanticState.INDETERMINATE, payload=payload.model_dump(mode="json"))
