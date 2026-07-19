# =============================================================================
# 中文阅读说明：企业文档生成业务模块，负责方案规划、检索、章节生成、引用和验收。
# 主要定义：SchemeWriterOutputSchema。建议先从公开入口函数开始，再沿调用关系向下阅读。
# =============================================================================
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


# 阅读注释（类）：封装 scheme writer 输出 Schema，定义跨模块传递的数据结构与字段约束。
class SchemeWriterOutputSchema(SchemaBase):
    """封装 scheme writer 输出 Schema，定义跨模块传递的数据结构与字段约束。"""
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
