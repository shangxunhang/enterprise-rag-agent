"""Deterministic historical-project filtering and vendor-evidence validation."""

from __future__ import annotations

import re
from typing import Iterable

from apps.enterprise_document.schemas.agent_rag_v1 import (
    CandidateEvidenceItemSchema,
    EvidenceSanitizationResultSchema,
    FactSnapshotSchema,
    RejectedCandidateEvidenceSchema,
    ValidatedEvidenceItemSchema,
)


class EvidenceSanitizer:
    def sanitize(
        self,
        *,
        candidates: Iterable[CandidateEvidenceItemSchema],
        fact_snapshot: FactSnapshotSchema,
        expected_snapshot_version: str,
        current_project_name: str,
        old_project_names: Iterable[str] = (),
    ) -> EvidenceSanitizationResultSchema:
        if fact_snapshot.snapshot_version != expected_snapshot_version:
            raise ValueError("evidence validation FactSnapshot is stale")
        explicitly_old = {
            name.strip()
            for name in old_project_names
            if name.strip() and name.strip() != current_project_name
        }
        accepted: list[ValidatedEvidenceItemSchema] = []
        rejected: list[RejectedCandidateEvidenceSchema] = []
        seen: set[str] = set()
        for candidate in candidates:
            if candidate.evidence_id in seen:
                raise ValueError(
                    f"duplicate candidate evidence id: {candidate.evidence_id}"
                )
            seen.add(candidate.evidence_id)
            conflict = self._model_conflict(candidate, fact_snapshot)
            if conflict:
                rejected.append(
                    RejectedCandidateEvidenceSchema(
                        evidence_id=candidate.evidence_id,
                        source_ref=candidate.source_ref,
                        validation_status="conflicted",
                        reason_code=conflict,
                    )
                )
                continue

            old_names = explicitly_old | {
                name.strip()
                for name in candidate.mentioned_project_names
                if name.strip() and name.strip() != current_project_name
            }
            sanitized = candidate.content
            adapted = False
            for old_name in sorted(old_names, key=len, reverse=True):
                if old_name in sanitized:
                    sanitized = sanitized.replace(old_name, "本项目")
                    adapted = True
            sanitized = re.sub(r"[ \t]{2,}", " ", sanitized).strip()
            if not sanitized:
                rejected.append(
                    RejectedCandidateEvidenceSchema(
                        evidence_id=candidate.evidence_id,
                        source_ref=candidate.source_ref,
                        validation_status="rejected",
                        reason_code="empty_after_project_sanitization",
                    )
                )
                continue
            reason_codes = ["project_name_adapted"] if adapted else []
            if candidate.general_technical_content:
                reason_codes.append("general_technical_content_validated")
            accepted.append(
                ValidatedEvidenceItemSchema(
                    evidence_id=candidate.evidence_id,
                    source_ref=candidate.source_ref,
                    sanitized_content=sanitized,
                    project_adapted=adapted,
                    validation_reason_codes=reason_codes,
                )
            )
        return EvidenceSanitizationResultSchema(
            accepted=accepted,
            rejected=rejected,
        )

    @staticmethod
    def _model_conflict(
        candidate: CandidateEvidenceItemSchema,
        facts: FactSnapshotSchema,
    ) -> str | None:
        if not candidate.claimed_model:
            return None
        assert candidate.device_name is not None
        normalized_device = candidate.device_name.strip().casefold()
        matches = [
            fact
            for fact in facts.devices
            if normalized_device
            in {
                fact.device_name.strip().casefold(),
                *(alias.strip().casefold() for alias in fact.aliases),
            }
        ]
        if not matches:
            return "device_not_in_fact_snapshot"
        authoritative_models = {
            fact.model.strip().casefold()
            for fact in matches
            if fact.model and fact.model.strip()
        }
        if (
            authoritative_models
            and candidate.claimed_model.strip().casefold()
            not in authoritative_models
        ):
            return "excel_model_conflict"
        return None
