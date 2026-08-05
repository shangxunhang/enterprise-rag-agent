"""Frozen Stage D retrieval-candidate contract.

A Retrieval Candidate is the online projection of one Stage C child/parent
lineage.  Its scalar identity, claim permissions and hashes must agree with the
canonical Document Profile; mixed hit/profile objects are rejected.
"""

from __future__ import annotations

from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, model_validator

from rag.catalog.document_profile import (
    AuthorityLevel,
    ClaimType,
    DocumentType,
    EvidenceRole,
    ProjectRelation,
)
from rag.schema.structured_evidence import (
    StructuredEvidenceSchema,
    build_structured_evidence,
)


NonEmptyStr = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1)]


class _FrozenContract(BaseModel):
    model_config = ConfigDict(extra="forbid", validate_assignment=True)


class RetrievalCandidateV2Schema(_FrozenContract):
    schema_version: Literal["retrieval_candidate_v2"] = "retrieval_candidate_v2"
    candidate_id: NonEmptyStr
    child_chunk_id: NonEmptyStr
    parent_chunk_id: NonEmptyStr
    document_id: NonEmptyStr
    tenant_id: NonEmptyStr
    kb_id: NonEmptyStr
    file_id: str | None = None

    document_type: DocumentType
    evidence_role: EvidenceRole
    project_relation: ProjectRelation
    project_id: str | None = None
    authority_level: AuthorityLevel
    allowed_claim_types: list[ClaimType] = Field(default_factory=list)
    forbidden_claim_types: list[ClaimType] = Field(default_factory=list)

    profile_sha256: NonEmptyStr
    source_sha256: NonEmptyStr
    document_profile: dict[str, Any]
    type_aware_chunking: dict[str, Any] = Field(default_factory=dict)
    structured_evidence: StructuredEvidenceSchema

    rank: int = Field(ge=1)
    retrieval_score: float | None = None
    rerank_score: float | None = None
    retrieval_sources: list[NonEmptyStr] = Field(default_factory=list)
    match_text: str = ""
    context_text: str = ""
    metadata: dict[str, Any] = Field(default_factory=dict)

    @staticmethod
    def _profile_relation(profile: dict[str, Any]) -> tuple[str | None, str | None]:
        raw = profile.get("project_relation")
        if isinstance(raw, dict):
            relation = raw.get("relation")
            project_id = raw.get("project_id")
        else:
            relation = raw
            project_id = profile.get("project_id")
        return (
            str(relation).strip() if relation is not None else None,
            str(project_id).strip() if project_id not in (None, "") else None,
        )

    @model_validator(mode="after")
    def _candidate_is_consistent(self) -> "RetrievalCandidateV2Schema":
        for field_name in (
            "allowed_claim_types",
            "forbidden_claim_types",
            "retrieval_sources",
        ):
            values = getattr(self, field_name)
            if len(values) != len(set(values)):
                raise ValueError(f"{field_name} must be unique")
        overlap = set(self.allowed_claim_types) & set(self.forbidden_claim_types)
        if overlap:
            raise ValueError(
                "candidate claim types cannot be both allowed and forbidden: "
                + ", ".join(sorted(item.value for item in overlap))
            )

        profile = self.document_profile
        required = {
            "document_id",
            "document_type",
            "evidence_role",
            "kb_id",
            "tenant_id",
            "project_relation",
            "authority",
            "content_capabilities",
            "profile_sha256",
            "source_sha256",
        }
        missing = sorted(required - set(profile))
        if missing:
            raise ValueError(
                "document_profile missing required Stage D fields: "
                + ", ".join(missing)
            )
        authority = profile.get("authority")
        capabilities = profile.get("content_capabilities")
        if not isinstance(authority, dict):
            raise ValueError("document_profile.authority must be an object")
        if not isinstance(capabilities, dict):
            raise ValueError(
                "document_profile.content_capabilities must be an object"
            )

        profile_relation, profile_project_id = self._profile_relation(profile)
        expected_scalars = {
            "document_id": str(profile.get("document_id") or ""),
            "tenant_id": str(profile.get("tenant_id") or ""),
            "kb_id": str(profile.get("kb_id") or ""),
            "document_type": str(profile.get("document_type") or ""),
            "evidence_role": str(profile.get("evidence_role") or ""),
            "project_relation": str(profile_relation or ""),
            "authority_level": str(authority.get("level") or ""),
            "profile_sha256": str(profile.get("profile_sha256") or ""),
            "source_sha256": str(profile.get("source_sha256") or ""),
        }
        actual_scalars = {
            "document_id": self.document_id,
            "tenant_id": self.tenant_id,
            "kb_id": self.kb_id,
            "document_type": self.document_type.value,
            "evidence_role": self.evidence_role.value,
            "project_relation": self.project_relation.value,
            "authority_level": self.authority_level.value,
            "profile_sha256": self.profile_sha256,
            "source_sha256": self.source_sha256,
        }
        for field_name, expected in expected_scalars.items():
            if actual_scalars[field_name] != expected:
                raise ValueError(
                    f"candidate {field_name} does not match document_profile"
                )
        if self.project_id != profile_project_id:
            raise ValueError("candidate project_id does not match document_profile")

        expected_allowed = {
            ClaimType(item)
            for item in capabilities.get("allowed_claim_types") or []
        }
        expected_forbidden = {
            ClaimType(item)
            for item in capabilities.get("forbidden_claim_types") or []
        }
        if set(self.allowed_claim_types) != expected_allowed:
            raise ValueError(
                "candidate allowed_claim_types do not match document_profile"
            )
        if set(self.forbidden_claim_types) != expected_forbidden:
            raise ValueError(
                "candidate forbidden_claim_types do not match document_profile"
            )
        return self


def _profile_from_result(result: dict[str, Any]) -> dict[str, Any]:
    metadata = result.get("metadata")
    metadata = metadata if isinstance(metadata, dict) else {}
    extra = result.get("extra")
    extra = extra if isinstance(extra, dict) else {}
    child = result.get("child_chunk")
    child = child if isinstance(child, dict) else {}
    child_extra = child.get("extra")
    child_extra = child_extra if isinstance(child_extra, dict) else {}
    for candidate in (
        metadata.get("document_profile"),
        extra.get("document_profile"),
        result.get("document_profile"),
        child.get("document_profile"),
        child_extra.get("document_profile"),
    ):
        if isinstance(candidate, dict):
            return dict(candidate)
    return {}


def _type_aware_from_result(result: dict[str, Any]) -> dict[str, Any]:
    metadata = result.get("metadata")
    metadata = metadata if isinstance(metadata, dict) else {}
    extra = result.get("extra")
    extra = extra if isinstance(extra, dict) else {}
    child = result.get("child_chunk")
    child = child if isinstance(child, dict) else {}
    child_extra = child.get("extra")
    child_extra = child_extra if isinstance(child_extra, dict) else {}
    for candidate in (
        metadata.get("type_aware_chunking"),
        extra.get("type_aware_chunking"),
        result.get("type_aware_chunking"),
        child.get("type_aware_chunking"),
        child_extra.get("type_aware_chunking"),
    ):
        if isinstance(candidate, dict):
            return dict(candidate)
    return {}


def _explicit_or_profile(
    result: dict[str, Any],
    field_name: str,
    profile_value: Any,
) -> Any:
    if field_name in result and result.get(field_name) not in (None, ""):
        return result.get(field_name)
    child = result.get("child_chunk")
    if isinstance(child, dict) and field_name in child and child.get(field_name) not in (None, ""):
        return child.get(field_name)
    return profile_value


def build_retrieval_candidate_v2(result: dict[str, Any]) -> RetrievalCandidateV2Schema:
    """Validate one enriched retrieval result as the Stage D candidate truth."""
    profile = _profile_from_result(result)
    capabilities = profile.get("content_capabilities")
    capabilities = capabilities if isinstance(capabilities, dict) else {}
    authority = profile.get("authority")
    authority = authority if isinstance(authority, dict) else {}
    project_relation = profile.get("project_relation")
    if isinstance(project_relation, dict):
        profile_project_id = project_relation.get("project_id")
        profile_relation_value = project_relation.get("relation")
    else:
        profile_project_id = profile.get("project_id")
        profile_relation_value = project_relation

    child_id = str(
        result.get("child_chunk_id") or result.get("chunk_id") or ""
    ).strip()
    parent_id = str(result.get("parent_chunk_id") or "").strip()
    document_id = str(
        result.get("doc_id") or profile.get("document_id") or ""
    ).strip()
    raw_allowed = _explicit_or_profile(
        result,
        "allowed_claim_types",
        capabilities.get("allowed_claim_types") or [],
    )
    raw_forbidden = _explicit_or_profile(
        result,
        "forbidden_claim_types",
        capabilities.get("forbidden_claim_types") or [],
    )
    project_id = _explicit_or_profile(
        result,
        "project_id",
        profile_project_id,
    )
    return RetrievalCandidateV2Schema(
        candidate_id=child_id,
        child_chunk_id=child_id,
        parent_chunk_id=parent_id,
        document_id=document_id,
        tenant_id=str(
            _explicit_or_profile(result, "tenant_id", profile.get("tenant_id"))
            or ""
        ).strip(),
        kb_id=str(
            _explicit_or_profile(result, "kb_id", profile.get("kb_id")) or ""
        ).strip(),
        file_id=str(result.get("file_id") or "").strip() or None,
        document_type=_explicit_or_profile(
            result,
            "document_type",
            profile.get("document_type"),
        ),
        evidence_role=_explicit_or_profile(
            result,
            "evidence_role",
            profile.get("evidence_role"),
        ),
        project_relation=_explicit_or_profile(
            result,
            "project_relation",
            profile_relation_value,
        ),
        project_id=(
            str(project_id).strip() if project_id not in (None, "") else None
        ),
        authority_level=_explicit_or_profile(
            result,
            "authority_level",
            authority.get("level"),
        ),
        allowed_claim_types=list(raw_allowed or []),
        forbidden_claim_types=list(raw_forbidden or []),
        profile_sha256=str(profile.get("profile_sha256") or "").strip(),
        source_sha256=str(profile.get("source_sha256") or "").strip(),
        document_profile=profile,
        type_aware_chunking=_type_aware_from_result(result),
        structured_evidence=build_structured_evidence(result),
        rank=max(1, int(result.get("rank") or 1)),
        retrieval_score=(
            float(result["score"]) if result.get("score") is not None else None
        ),
        rerank_score=(
            float(result["rerank_score"])
            if result.get("rerank_score") is not None
            else None
        ),
        retrieval_sources=list(
            dict.fromkeys(
                str(item)
                for item in (
                    (result.get("metadata") or {}).get("retrieval_sources")
                    or result.get("retrieval_sources")
                    or []
                )
                if str(item).strip()
            )
        ),
        match_text=str(
            result.get("child_text") or result.get("match_text") or ""
        ),
        context_text=str(
            result.get("parent_text")
            or result.get("context_text")
            or result.get("text")
            or ""
        ),
        metadata={
            "section": result.get("section"),
            "title": result.get("title"),
            "page_start": result.get("page_start"),
            "page_end": result.get("page_end"),
            "matched_child_chunk_ids": list(
                (result.get("metadata") or {}).get("matched_child_chunk_ids") or []
            ),
            "crag_final_judgement": (
                (result.get("metadata") or {}).get("crag_final_judgement")
            ),
            "crag_final_assessment_sufficient": (
                (result.get("metadata") or {}).get(
                    "crag_final_assessment_sufficient"
                )
            ),
        },
    )


__all__ = ["RetrievalCandidateV2Schema", "build_retrieval_candidate_v2"]
