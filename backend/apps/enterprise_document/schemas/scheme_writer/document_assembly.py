# =============================================================================
# 中文阅读说明：文档级确定性聚合的输入/输出契约。
# 本模块只定义章节结果聚合为完整文档时的跨层数据边界。
# =============================================================================
"""Contracts for deterministic document assembly."""

from __future__ import annotations

from typing import Any, Dict, List, Literal, Set

from pydantic import Field

from schemas.citation import CitationBindingSchema, CitationSchema
from schemas.common import SchemaBase
from schemas.rag import RetrievedChunkSchema

from .document import SchemeDraftSchema, SchemeSectionSchema
from .evidence import SectionEvidenceBundleSchema


class DocumentAssemblyRequestSchema(SchemaBase):
    """Inputs required to deterministically assemble one complete document."""

    schema_version: str = "document_assembly_request_v1"

    task_id: str
    run_id: str
    document_id: str
    document_title: str

    required_sections: List[str] = Field(default_factory=list)
    sections: List[SchemeSectionSchema] = Field(default_factory=list)

    retrieved_chunks: List[RetrievedChunkSchema] = Field(default_factory=list)
    citations: List[CitationSchema] = Field(default_factory=list)
    section_evidence: List[SectionEvidenceBundleSchema] = Field(default_factory=list)

    document_evidence_available: bool = False
    document_assessment_status: str = "not_assessed"
    document_evidence_assessment: Dict[str, Any] = Field(default_factory=dict)
    citation_required_sections: List[str] = Field(default_factory=list)

    created_at: str
    updated_at: str


class SectionEvidenceRequirementSchema(SchemaBase):
    """One section's independently evaluated evidence requirement."""

    section_title: str
    assessment_status: Literal[
        "sufficient",
        "insufficient",
        "not_assessed",
        "blocked",
    ]
    generation_blocked: bool = False
    generation_block_reason: str | None = None
    requirement_satisfied: bool = False


class DocumentAssemblyResultSchema(SchemaBase):
    """Pure aggregation result consumed by the document hard gate and output layer."""

    schema_version: str = "document_assembly_result_v1"

    draft: SchemeDraftSchema
    retrieved_chunks: List[RetrievedChunkSchema] = Field(default_factory=list)
    citations: List[CitationSchema] = Field(default_factory=list)
    citation_bindings: List[CitationBindingSchema] = Field(default_factory=list)
    known_chunk_ids: Set[str] = Field(default_factory=set)
    evidence_available: bool = False
    semantic_evidence_sufficient: bool = False
    evidence_requirements_satisfied: bool = False
    section_evidence_requirements: List[SectionEvidenceRequirementSchema] = Field(
        default_factory=list
    )
