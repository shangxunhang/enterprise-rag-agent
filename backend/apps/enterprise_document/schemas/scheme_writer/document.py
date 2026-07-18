"""Generated section and document schemas."""
from __future__ import annotations

from typing import Any, Dict, List, Optional

from pydantic import Field

from apps.enterprise_document.schemas.scheme_writer.evaluation import SectionEvalSchema, TruncationCheckSchema
from schemas.citation import CitationBindingSchema
from schemas.common import ErrorSchema, SchemaBase, WarningSchema
from schemas.status import ExecutionStatus


class SchemeSectionSchema(SchemaBase):
    schema_version: str = "scheme_section_v2"
    section_id: str
    section_title: str
    section_level: int = 1
    section_order: int
    input: Dict[str, Any] = Field(default_factory=dict)
    prompt: str = ""
    model_output: str = ""
    content: str = ""
    status: ExecutionStatus = ExecutionStatus.PENDING
    error: Optional[ErrorSchema] = None
    citation_ids: List[str] = Field(default_factory=list)
    citation_bindings: List[CitationBindingSchema] = Field(default_factory=list)
    source_fact_ids: List[str] = Field(default_factory=list)
    truncation: TruncationCheckSchema = Field(default_factory=TruncationCheckSchema)
    eval_result: Optional[SectionEvalSchema] = None
    warnings: List[WarningSchema] = Field(default_factory=list)
    started_at: Optional[str] = None
    finished_at: Optional[str] = None
    extra: Dict[str, Any] = Field(default_factory=dict)


class SchemeDraftSchema(SchemaBase):
    schema_version: str = "scheme_draft_v2"
    draft_id: str
    document_id: str
    task_id: str
    run_id: str
    title: str
    full_text: str = ""
    sections: List[SchemeSectionSchema] = Field(default_factory=list)
    required_sections: List[str] = Field(default_factory=list)
    missing_sections: List[str] = Field(default_factory=list)
    citation_bindings: List[CitationBindingSchema] = Field(default_factory=list)
    truncation_detected: bool = False
    summary: Optional[str] = None
    created_at: str
    updated_at: Optional[str] = None
    extra: Dict[str, Any] = Field(default_factory=dict)
