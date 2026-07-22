# =============================================================================
# 中文阅读说明：单章节完整执行生命周期的输入/输出契约。
# 本模块只定义跨层数据边界，不实现检索、生成、恢复或状态写入逻辑。
# =============================================================================
"""Contracts for executing one section inside scheme generation."""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from pydantic import Field

from agent.runtime.shared_state_schema import SharedStateSchema
from apps.enterprise_document.schemas.project_input_schema import ProjectInputSchema
from apps.enterprise_document.schemas.table_agent_schema import StructuredFactSchema
from schemas.citation import CitationSchema
from schemas.common import ErrorSchema, SchemaBase, WarningSchema
from schemas.rag import RAGContextSchema, RetrievedChunkSchema

from .document import SchemeSectionSchema
from .evidence import SectionEvidenceBundleSchema
from .planning import SectionPlanSchema


class SectionExecutionRequestSchema(SchemaBase):
    """Inputs required to execute one section lifecycle.

    ``shared_state`` intentionally remains the existing working state during the
    first extraction step so model-output capture and other current side effects
    keep one source of truth. ``DocumentCitationRegistry`` is deliberately not
    owned by this schema; the document-level use case must inject the shared
    registry separately when it calls the future coordinator.
    """

    schema_version: str = "section_execution_request_v1"

    shared_state: SharedStateSchema
    document_id: str
    project_input: ProjectInputSchema
    section_plan: SectionPlanSchema

    structured_facts: List[StructuredFactSchema] = Field(default_factory=list)
    previous_sections: List[SchemeSectionSchema] = Field(default_factory=list)

    document_rag_context: RAGContextSchema
    document_retrieved_chunks: List[RetrievedChunkSchema] = Field(default_factory=list)
    document_citations: List[CitationSchema] = Field(default_factory=list)
    document_evidence_assessment: Dict[str, Any] = Field(default_factory=dict)
    document_tool_call_ids: List[str] = Field(default_factory=list)

    section_retrieval_enabled: bool = True
    corrective_retrieval_enabled: bool = True


class SectionExecutionResultSchema(SchemaBase):
    """Business result and complete retrieval artifacts for one section.

    ``evidence`` is the final active evidence used by the section. In contrast,
    ``retrieved_chunks`` and ``rag_outputs`` retain every retrieval call made by
    this section lifecycle, in original call order, so document assembly,
    capture and lineage do not lose historical recovery evidence.
    """

    schema_version: str = "section_execution_result_v1"

    section: SchemeSectionSchema
    evidence: SectionEvidenceBundleSchema

    retrieved_chunks: List[RetrievedChunkSchema] = Field(default_factory=list)
    rag_outputs: List[Dict[str, Any]] = Field(default_factory=list)
    budget_usage: Dict[str, Any] = Field(default_factory=dict)
    need_human_review: bool = False

    warnings: List[WarningSchema] = Field(default_factory=list)
    error: Optional[ErrorSchema] = None
