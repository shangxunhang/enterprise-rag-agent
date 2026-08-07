"""Frozen projection of evidence that is actually visible in the prompt.

A retrieval candidate may contain a full parent chunk.  A prompt evidence
candidate instead binds the exact selected/truncated text emitted by the Context
Packer, so final governance cannot count text that the model never received.
"""

from __future__ import annotations

import re
import unicodedata
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, model_validator

from context_manager.token_estimator import DeterministicTokenEstimator
from rag.schema.retrieval_candidate_v2 import (
    RetrievalCandidateV2Schema,
    build_retrieval_candidate_v2,
)
from rag.schema.structured_evidence import StructuredEvidenceSchema


NonEmptyStr = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1)]


class PromptEvidenceCandidateV1Schema(BaseModel):
    model_config = ConfigDict(extra="forbid", validate_assignment=True)

    schema_version: Literal["prompt_evidence_candidate_v1"] = (
        "prompt_evidence_candidate_v1"
    )
    candidate_id: NonEmptyStr
    child_chunk_id: NonEmptyStr
    parent_chunk_id: NonEmptyStr
    document_id: NonEmptyStr

    prompt_visible_text: str
    prompt_visible_char_count: int = Field(ge=0)
    prompt_visible_token_count: int = Field(ge=0)

    citation_ids: list[NonEmptyStr] = Field(default_factory=list)
    context_rank: int = Field(ge=1)

    context_truncated: bool = False
    truncated_source_id: str | None = None
    truncation_reason: str | None = None

    document_profile: dict[str, Any]
    structured_evidence: StructuredEvidenceSchema
    retrieval_candidate: RetrievalCandidateV2Schema

    profile_sha256: NonEmptyStr
    source_sha256: NonEmptyStr
    fact_visible_in_prompt: bool | None = None

    @model_validator(mode="after")
    def _projection_is_consistent(self) -> "PromptEvidenceCandidateV1Schema":
        if len(self.citation_ids) != len(set(self.citation_ids)):
            raise ValueError("citation_ids must be unique")
        if self.prompt_visible_char_count != len(self.prompt_visible_text):
            raise ValueError("prompt_visible_char_count does not match text")
        estimated = DeterministicTokenEstimator.estimate(self.prompt_visible_text)
        if self.prompt_visible_token_count != estimated:
            raise ValueError("prompt_visible_token_count does not match text")
        candidate = self.retrieval_candidate
        expected = {
            "candidate_id": candidate.candidate_id,
            "child_chunk_id": candidate.child_chunk_id,
            "parent_chunk_id": candidate.parent_chunk_id,
            "document_id": candidate.document_id,
            "profile_sha256": candidate.profile_sha256,
            "source_sha256": candidate.source_sha256,
        }
        for field_name, value in expected.items():
            if getattr(self, field_name) != value:
                raise ValueError(
                    f"prompt evidence {field_name} does not match retrieval candidate"
                )
        if self.document_profile != candidate.document_profile:
            raise ValueError(
                "prompt evidence document_profile does not match retrieval candidate"
            )
        if self.structured_evidence != candidate.structured_evidence:
            raise ValueError(
                "prompt evidence structured_evidence does not match retrieval candidate"
            )
        if self.context_truncated and not self.truncated_source_id:
            raise ValueError("truncated prompt evidence requires truncated_source_id")
        return self


def _normalize_visible(value: Any) -> str:
    text = unicodedata.normalize("NFKC", str(value or ""))
    return re.sub(r"\s+", "", text).strip()


def _fact_visible(
    candidate: RetrievalCandidateV2Schema,
    prompt_visible_text: str,
) -> bool | None:
    fact = candidate.structured_evidence.fact
    if fact is None:
        return None
    visible = _normalize_visible(prompt_visible_text)

    # Stage C may encode a structured table row as a JSON value while retaining
    # the exact human-readable evidence in source_span.text.  Final governance
    # should accept that fact only when the actual source span is visible; it
    # must not require the internal JSON serialization to appear in the prompt.
    source_span = fact.source_span if isinstance(fact.source_span, dict) else {}
    source_text = _normalize_visible(source_span.get("text"))
    if source_text and source_text in visible:
        return True

    value = _normalize_visible(fact.value)
    if not value or value not in visible:
        return False
    predicate = _normalize_visible(fact.predicate)
    return not predicate or predicate in visible


def build_prompt_evidence_candidate_v1(
    result: dict[str, Any],
    *,
    allow_legacy_fallback: bool = False,
) -> PromptEvidenceCandidateV1Schema:
    """Build a prompt candidate from one authoritative selected result.

    Strict mode requires ``prompt_visible_text`` to be explicitly published by
    the Context Packer.  Compatibility mode may use ``context_text``/``text``,
    but never the full ``parent_text`` field.
    """
    metadata = result.get("metadata")
    metadata = metadata if isinstance(metadata, dict) else {}
    raw_candidate = metadata.get("retrieval_candidate_v2")
    if isinstance(raw_candidate, RetrievalCandidateV2Schema):
        candidate = raw_candidate
    elif isinstance(raw_candidate, dict):
        candidate = RetrievalCandidateV2Schema.model_validate(raw_candidate)
    else:
        candidate = build_retrieval_candidate_v2(result)

    if "prompt_visible_text" in result:
        prompt_text = str(result.get("prompt_visible_text") or "")
    elif allow_legacy_fallback:
        prompt_text = str(result.get("context_text") or result.get("text") or "")
    else:
        raise ValueError(
            "selected result missing explicit prompt_visible_text contract"
        )

    citation_ids = metadata.get("prompt_citation_ids")
    citation_ids = citation_ids if isinstance(citation_ids, list) else []
    context_rank = int(result.get("context_rank") or result.get("rank") or 1)
    truncated = bool(metadata.get("context_truncated", False))
    truncated_source_id = (
        str(
            metadata.get("truncated_source_id")
            or result.get("parent_chunk_id")
            or result.get("child_chunk_id")
            or ""
        ).strip()
        or None
    )
    truncation_reason = (
        str(metadata.get("truncation_reason") or "").strip() or None
    )
    return PromptEvidenceCandidateV1Schema(
        candidate_id=candidate.candidate_id,
        child_chunk_id=candidate.child_chunk_id,
        parent_chunk_id=candidate.parent_chunk_id,
        document_id=candidate.document_id,
        prompt_visible_text=prompt_text,
        prompt_visible_char_count=len(prompt_text),
        prompt_visible_token_count=DeterministicTokenEstimator.estimate(
            prompt_text
        ),
        citation_ids=list(dict.fromkeys(str(item) for item in citation_ids if str(item))),
        context_rank=max(1, context_rank),
        context_truncated=truncated,
        truncated_source_id=truncated_source_id if truncated else None,
        truncation_reason=truncation_reason if truncated else None,
        document_profile=dict(candidate.document_profile),
        structured_evidence=candidate.structured_evidence,
        retrieval_candidate=candidate,
        profile_sha256=candidate.profile_sha256,
        source_sha256=candidate.source_sha256,
        fact_visible_in_prompt=_fact_visible(candidate, prompt_text),
    )


__all__ = [
    "PromptEvidenceCandidateV1Schema",
    "build_prompt_evidence_candidate_v1",
]
