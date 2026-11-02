"""Writer 逐段生成、修订、Diff 与 Precheck 的强类型协议。"""

from __future__ import annotations

from typing import List, Optional

from pydantic import Field, field_validator

from ._base import ContractSchema
from .claims import ClaimSchema
from .enums import ParagraphChangeType
from .events import (
    ParagraphGeneratedEventSchema,
    SectionGenerationCompletedEventSchema,
)
from .payloads import (
    ParagraphArtifactSchema,
    VerifierIssueSchema,
    WriterResultPayloadSchema,
)


class GeneratedParagraphInputSchema(ContractSchema):
    paragraph_id: Optional[str] = None
    paragraph_version: int = Field(default=1, ge=1)
    order: int = Field(ge=0)
    role: str
    content: str

    @field_validator("content")
    @classmethod
    def _non_empty_content(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("generated paragraph content cannot be empty")
        return value


class ParagraphPrecheckResultSchema(ContractSchema):
    schema_version: str = "paragraph_precheck_result_v1"
    section_id: str
    paragraph_id: str
    paragraph_version: int = Field(default=1, ge=1)
    passed: bool
    blocking: bool
    claims: List[ClaimSchema] = Field(default_factory=list)
    issues: List[VerifierIssueSchema] = Field(default_factory=list)


class ParagraphChangeSchema(ContractSchema):
    paragraph_id: str
    change_type: ParagraphChangeType
    old_content_hash: Optional[str] = None
    new_content_hash: Optional[str] = None
    reason_code: str
    verifier_issue_ids: List[str] = Field(default_factory=list)


class WriterRevisionResultSchema(ContractSchema):
    schema_version: str = "writer_revision_result_v1"
    section_id: str
    base_draft_version: int = Field(ge=1)
    new_draft_version: int = Field(ge=1)
    change_manifest: List[ParagraphChangeSchema] = Field(default_factory=list)


class DraftSnapshotSchema(ContractSchema):
    schema_version: str = "draft_snapshot_v1"
    section_id: str
    draft_version: int = Field(ge=1)
    paragraphs: List[ParagraphArtifactSchema] = Field(default_factory=list)


class DraftDiffResultSchema(ContractSchema):
    schema_version: str = "draft_diff_result_v1"
    actual_changes: List[ParagraphChangeSchema] = Field(default_factory=list)
    undeclared_paragraph_ids: List[str] = Field(default_factory=list)
    declaration_mismatches: List[str] = Field(default_factory=list)


class StreamingWriterOutcomeSchema(ContractSchema):
    schema_version: str = "streaming_writer_outcome_v1"
    writer_result: WriterResultPayloadSchema
    paragraph_events: List[ParagraphGeneratedEventSchema] = Field(
        default_factory=list
    )
    section_completed_event: Optional[SectionGenerationCompletedEventSchema] = None
    precheck_results: List[ParagraphPrecheckResultSchema] = Field(
        default_factory=list
    )
    blocked_paragraph_id: Optional[str] = None


class IncrementalClaimResultSchema(ContractSchema):
    schema_version: str = "incremental_claim_result_v1"
    claims: List[ClaimSchema] = Field(default_factory=list)
    reused_claim_ids: List[str] = Field(default_factory=list)
    invalidated_claim_ids: List[str] = Field(default_factory=list)
    removed_claim_ids: List[str] = Field(default_factory=list)
    full_section_reextract: bool = False
