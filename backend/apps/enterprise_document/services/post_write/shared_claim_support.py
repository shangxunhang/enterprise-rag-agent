"""Post-write adapter into canonical M6 CLAIM_EVIDENCE_SUPPORT authority.

The adapter owns only Post-write ClaimTarget projection/fallback fencing and
conversion to the dependency-neutral ClaimSupportSubjectRef. It never owns or
re-adjudicates canonical support truth.
"""

from __future__ import annotations

from typing import Iterable, Mapping

from apps.enterprise_document.schemas.agent_rag_v1.claim_support import (
    ClaimEvidenceSupportRecordSchema,
    ClaimSupportAuthorityKind,
    ClaimSupportSubjectRefSchema,
    EvidenceTargetSetRefSchema,
)
from apps.enterprise_document.schemas.agent_rag_v1.claims import (
    ClaimRecordSchema,
    ClaimRefSchema,
)
from apps.enterprise_document.schemas.agent_rag_v1.verification import (
    ClaimOperationContextSchema,
)
from apps.enterprise_document.schemas.post_write.foundation import (
    ArtifactStateRefSchema,
)
from apps.enterprise_document.schemas.post_write.quality import (
    ArtifactClaimTargetSchema,
    ClaimTargetAuthorityKind,
    ForeignClaimTargetCandidateSchema,
    PostWriteForeignClaimTargetRefSchema,
    UpstreamSIF07ClaimAuthorityResolutionSchema,
    UpstreamSIF07ClaimAuthorityState,
)
from apps.enterprise_document.services.agent_rag_v1.claim_support_runtime import (
    ClaimSupportRuntime,
)
from core.hashing import canonical_sha256


class PostWriteClaimTargetError(ValueError):
    """Post-write ClaimTarget authority/materialization failure."""


class UpstreamClaimAuthorityUnresolved(PostWriteClaimTargetError):
    """SIF-07 applicability/current authority cannot be resolved exactly."""


class ForeignClaimFallbackForbidden(PostWriteClaimTargetError):
    """Foreign fallback was attempted outside explicit NOT_APPLICABLE state."""


class ClaimTargetIntegrityError(PostWriteClaimTargetError):
    """Resolved upstream/local target inputs contradict exact identity."""


def _claim_ref_key(ref: ClaimRefSchema) -> str:
    return ref.claim_id


class ArtifactClaimTargetMaterializer:
    """Projects exact SIF-07 ClaimRecords or a genuine foreign-local target set."""

    @staticmethod
    def _sif07_target(
        *,
        record: ClaimRecordSchema,
        artifact_state_ref: ArtifactStateRefSchema,
        validation_subject_hash: str,
        semantic_version: str,
    ) -> ArtifactClaimTargetSchema:
        record = ClaimRecordSchema.model_validate(record)
        base = {
            "authority_kind": ClaimTargetAuthorityKind.SIF07_PROJECTION.value,
            "artifact_state_ref": artifact_state_ref.model_dump(mode="json"),
            "validation_subject_hash": validation_subject_hash,
            "source_claim_ref": record.claim_ref.model_dump(mode="json"),
            "canonical_claim_text": record.text_span.text,
            "source_span_ref": (
                f"claim-span://{record.claim_ref.claim_id}/"
                f"{record.claim_occurrence_ref.start_offset}:"
                f"{record.claim_occurrence_ref.end_offset}"
            ),
            "semantic_version": semantic_version,
        }
        projection_hash = canonical_sha256(base)
        target_id = "post-write-claim-projection://sha256/" + projection_hash
        final = {
            **base,
            "claim_target_id": target_id,
        }
        return ArtifactClaimTargetSchema(
            claim_target_id=target_id,
            authority_kind=ClaimTargetAuthorityKind.SIF07_PROJECTION,
            artifact_state_ref=artifact_state_ref,
            validation_subject_hash=validation_subject_hash,
            source_claim_ref=record.claim_ref,
            foreign_local_ref=None,
            canonical_claim_text=record.text_span.text,
            source_span_ref=base["source_span_ref"],
            semantic_version=semantic_version,
            claim_target_sha256=canonical_sha256(final),
        )

    @staticmethod
    def _foreign_target(
        *,
        candidate: ForeignClaimTargetCandidateSchema,
        artifact_state_ref: ArtifactStateRefSchema,
        validation_subject_hash: str,
        semantic_version: str,
    ) -> ArtifactClaimTargetSchema:
        candidate = ForeignClaimTargetCandidateSchema.model_validate(candidate)
        foreign_identity = {
            "authority_kind": ClaimTargetAuthorityKind.POST_WRITE_FOREIGN_LOCAL.value,
            "artifact_state_ref": artifact_state_ref.model_dump(mode="json"),
            "validation_subject_hash": validation_subject_hash,
            "local_claim_occurrence_ref": candidate.local_claim_occurrence_ref,
            "local_semantic_contract_version": candidate.local_semantic_contract_version,
        }
        canonical_ref = (
            "post-write-foreign-claim://sha256/"
            + canonical_sha256(foreign_identity)
        )
        foreign_ref = PostWriteForeignClaimTargetRefSchema(
            claim_target_ref=canonical_ref,
            validation_subject_hash=validation_subject_hash,
            artifact_state_ref=artifact_state_ref,
            local_claim_occurrence_ref=candidate.local_claim_occurrence_ref,
            local_semantic_contract_version=candidate.local_semantic_contract_version,
        )
        base = {
            "claim_target_id": canonical_ref,
            "authority_kind": ClaimTargetAuthorityKind.POST_WRITE_FOREIGN_LOCAL.value,
            "artifact_state_ref": artifact_state_ref.model_dump(mode="json"),
            "validation_subject_hash": validation_subject_hash,
            "foreign_local_ref": foreign_ref.model_dump(mode="json"),
            "canonical_claim_text": candidate.canonical_claim_text,
            "source_span_ref": candidate.source_span_ref,
            "semantic_version": semantic_version,
        }
        return ArtifactClaimTargetSchema(
            claim_target_id=canonical_ref,
            authority_kind=ClaimTargetAuthorityKind.POST_WRITE_FOREIGN_LOCAL,
            artifact_state_ref=artifact_state_ref,
            validation_subject_hash=validation_subject_hash,
            source_claim_ref=None,
            foreign_local_ref=foreign_ref,
            canonical_claim_text=candidate.canonical_claim_text,
            source_span_ref=candidate.source_span_ref,
            semantic_version=semantic_version,
            claim_target_sha256=canonical_sha256(base),
        )

    @classmethod
    def materialize(
        cls,
        *,
        authority_resolution: UpstreamSIF07ClaimAuthorityResolutionSchema,
        artifact_state_ref: ArtifactStateRefSchema,
        validation_subject_hash: str,
        semantic_version: str,
        resolved_claim_records: Mapping[str, ClaimRecordSchema] | None = None,
        foreign_candidates: Iterable[ForeignClaimTargetCandidateSchema] = (),
    ) -> tuple[ArtifactClaimTargetSchema, ...]:
        resolution = UpstreamSIF07ClaimAuthorityResolutionSchema.model_validate(
            authority_resolution
        )
        artifact_ref = ArtifactStateRefSchema.model_validate(artifact_state_ref)
        semantic_version = str(semantic_version or "").strip()
        if not semantic_version:
            raise ValueError("ClaimTarget semantic_version must be non-empty")

        records = dict(resolved_claim_records or {})
        foreign = tuple(
            ForeignClaimTargetCandidateSchema.model_validate(item)
            for item in foreign_candidates
        )

        if resolution.state is UpstreamSIF07ClaimAuthorityState.UNRESOLVED:
            raise UpstreamClaimAuthorityUnresolved(
                "upstream SIF-07 Claim authority is UNRESOLVED; fallback forbidden"
            )

        if resolution.state is UpstreamSIF07ClaimAuthorityState.PRESENT:
            if foreign:
                raise ForeignClaimFallbackForbidden(
                    "foreign Claim fallback is forbidden when SIF-07 authority is PRESENT"
                )
            expected = tuple(ref.claim_id for ref in resolution.claim_refs)
            if set(records) != set(expected):
                raise ClaimTargetIntegrityError(
                    "PRESENT upstream Claim authority requires exact resolved ClaimRecord set"
                )
            targets = []
            for ref in resolution.claim_refs:
                record = ClaimRecordSchema.model_validate(records[ref.claim_id])
                if record.claim_ref != ref:
                    raise ClaimTargetIntegrityError(
                        "resolved ClaimRecord differs from exact authority resolution"
                    )
                targets.append(
                    cls._sif07_target(
                        record=record,
                        artifact_state_ref=artifact_ref,
                        validation_subject_hash=validation_subject_hash,
                        semantic_version=semantic_version,
                    )
                )
            return tuple(sorted(targets, key=lambda item: item.claim_target_id))

        if records:
            raise ClaimTargetIntegrityError(
                "NOT_APPLICABLE upstream state cannot carry SIF-07 ClaimRecords"
            )
        targets = tuple(
            cls._foreign_target(
                candidate=item,
                artifact_state_ref=artifact_ref,
                validation_subject_hash=validation_subject_hash,
                semantic_version=semantic_version,
            )
            for item in foreign
        )
        ids = [item.claim_target_id for item in targets]
        if len(ids) != len(set(ids)):
            raise ClaimTargetIntegrityError(
                "foreign ClaimTarget candidates collide on canonical identity"
            )
        return tuple(sorted(targets, key=lambda item: item.claim_target_id))


class ClaimTargetSupportSubjectAdapter:
    """Converts one Post-write ClaimTarget to the canonical shared tagged subject."""

    @staticmethod
    def adapt(
        target: ArtifactClaimTargetSchema,
    ) -> ClaimSupportSubjectRefSchema:
        target = ArtifactClaimTargetSchema.model_validate(target)
        if target.authority_kind is ClaimTargetAuthorityKind.SIF07_PROJECTION:
            if target.source_claim_ref is None:
                raise ClaimTargetIntegrityError("SIF-07 target lost source ClaimRef")
            return ClaimSupportSubjectRefSchema(
                authority_kind=ClaimSupportAuthorityKind.SIF07_CLAIM,
                subject_ref=target.source_claim_ref.claim_id,
            )
        if target.foreign_local_ref is None:
            raise ClaimTargetIntegrityError("foreign target lost canonical local ref")
        if not target.foreign_local_ref.claim_target_ref.startswith(
            "post-write-foreign-claim://sha256/"
        ):
            raise ClaimTargetIntegrityError("foreign ClaimTarget namespace is invalid")
        return ClaimSupportSubjectRefSchema(
            authority_kind=ClaimSupportAuthorityKind.POST_WRITE_FOREIGN_LOCAL,
            subject_ref=target.foreign_local_ref.claim_target_ref,
        )


class SharedClaimEvidenceSupportEvaluator:
    """Thin adapter/caller into the canonical M6 ClaimSupportRuntime."""

    def __init__(self, runtime: ClaimSupportRuntime, *, support_evaluator=None) -> None:
        self.runtime = runtime
        self.support_evaluator = support_evaluator

    def materialize_evidence_target_set(
        self,
        *,
        claim_target: ArtifactClaimTargetSchema,
        evidence_member_refs: Iterable[str],
        target_set_contract_version: str,
    ) -> EvidenceTargetSetRefSchema:
        subject = ClaimTargetSupportSubjectAdapter.adapt(claim_target)
        return self.runtime.materialize_evidence_target_set(
            subject,
            evidence_member_refs,
            target_set_contract_version,
        )

    def evaluate_and_commit(
        self,
        *,
        claim_target: ArtifactClaimTargetSchema,
        evidence_target_set_ref: EvidenceTargetSetRefSchema,
        support_semantic_contract_version: str,
        operation_context: ClaimOperationContextSchema,
    ) -> ClaimEvidenceSupportRecordSchema:
        subject = ClaimTargetSupportSubjectAdapter.adapt(claim_target)
        target = EvidenceTargetSetRefSchema.model_validate(evidence_target_set_ref)
        if target.claim_support_subject_ref != subject:
            raise ClaimTargetIntegrityError(
                "Post-write ClaimTarget and EvidenceTargetSet bind different support subjects"
            )
        outcome = self.runtime.evaluate_support_candidate(
            subject,
            target,
            support_semantic_contract_version,
            operation_context,
            **({"support_evaluator": self.support_evaluator} if self.support_evaluator is not None else {}),
        )
        if outcome.existing_record is not None:
            return outcome.existing_record
        if outcome.candidate is None:
            raise ClaimTargetIntegrityError(
                "canonical shared support runtime returned no record/candidate"
            )
        return self.runtime.commit_support(outcome.candidate, operation_context)


__all__ = [
    "ArtifactClaimTargetMaterializer",
    "ClaimTargetIntegrityError",
    "ClaimTargetSupportSubjectAdapter",
    "ForeignClaimFallbackForbidden",
    "PostWriteClaimTargetError",
    "SharedClaimEvidenceSupportEvaluator",
    "UpstreamClaimAuthorityUnresolved",
]
