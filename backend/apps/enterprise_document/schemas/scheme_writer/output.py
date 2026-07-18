"""Top-level scheme writer output contract."""
from __future__ import annotations

from typing import Any, Dict, List, Optional

from pydantic import Field

from apps.enterprise_document.schemas.scheme_writer.document import SchemeDraftSchema
from apps.enterprise_document.schemas.scheme_writer.evaluation import HardGateResultSchema
from apps.enterprise_document.schemas.scheme_writer.planning import DocumentPlanSchema
from apps.enterprise_document.schemas.scheme_writer.evidence import SectionEvidenceBundleSchema
from schemas.citation import CitationSchema
from schemas.common import ErrorSchema, SchemaBase, WarningSchema
from schemas.rag import RAGContextSchema, RetrievedChunkSchema
from schemas.status import ExecutionStatus


class SchemeWriterOutputSchema(SchemaBase):
    schema_version: str = "scheme_writer_output_v2"
    task_id: str
    run_id: str
    status: ExecutionStatus
    document_plan: Optional[DocumentPlanSchema] = None
    scheme_draft: Optional[SchemeDraftSchema] = None
    rag_context: Optional[RAGContextSchema] = None
    retrieved_chunks: List[RetrievedChunkSchema] = Field(default_factory=list)
    citations: List[CitationSchema] = Field(default_factory=list)
    section_evidence: List[SectionEvidenceBundleSchema] = Field(default_factory=list)
    hard_gate: Optional[HardGateResultSchema] = None
    warnings: List[WarningSchema] = Field(default_factory=list)
    error: Optional[ErrorSchema] = None
    need_human_review: bool = True
    extra: Dict[str, Any] = Field(default_factory=dict)
