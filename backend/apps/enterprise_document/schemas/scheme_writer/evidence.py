"""Section-scoped evidence records for long-document generation."""
from __future__ import annotations

from typing import Any, Dict, List, Optional

from pydantic import Field

from schemas.citation import CitationSchema
from schemas.common import SchemaBase
from schemas.rag import RAGContextSchema, RetrievedChunkSchema


class SectionEvidenceBundleSchema(SchemaBase):
    """Evidence actually used for one generated section.

    The document-level RAG result remains available for compatibility.  This
    bundle records section-aware retrieval and optional corrective retrieval so
    later observability, evaluation and Word assembly can explain each section.
    """

    schema_version: str = "section_evidence_bundle_v1"
    section_id: str
    section_title: str
    retrieval_scope: str = "document"  # document | section | recovery
    query: str
    tool_call_ids: List[str] = Field(default_factory=list)
    rag_context: RAGContextSchema
    retrieved_chunks: List[RetrievedChunkSchema] = Field(default_factory=list)
    citations: List[CitationSchema] = Field(default_factory=list)
    recovery_count: int = 0
    evidence_contract_sha256: Optional[str] = None
    metadata: Dict[str, Any] = Field(default_factory=dict)
