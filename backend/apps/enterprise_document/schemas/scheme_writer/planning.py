"""Document and section planning schemas."""
from __future__ import annotations

from typing import Any, Dict, List

from pydantic import Field

from schemas.common import SchemaBase


class SectionPlanSchema(SchemaBase):
    schema_version: str = "section_plan_v1"
    section_id: str
    section_title: str
    section_order: int
    citation_required: bool = False
    metadata: Dict[str, Any] = Field(default_factory=dict)


class DocumentPlanSchema(SchemaBase):
    schema_version: str = "document_plan_v1"
    plan_id: str
    document_id: str
    document_title: str
    sections: List[SectionPlanSchema] = Field(default_factory=list)
    planning_source: str = "project_input"
    created_at: str
    metadata: Dict[str, Any] = Field(default_factory=dict)
