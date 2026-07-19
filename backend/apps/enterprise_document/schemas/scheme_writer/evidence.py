# =============================================================================
# 中文阅读说明：企业文档生成业务模块，负责方案规划、检索、章节生成、引用和验收。
# 主要定义：SectionEvidenceBundleSchema。建议先从公开入口函数开始，再沿调用关系向下阅读。
# =============================================================================
"""Section-scoped evidence records for long-document generation."""
from __future__ import annotations

from typing import Any, Dict, List, Optional

from pydantic import Field

from schemas.citation import CitationSchema
from schemas.common import SchemaBase
from schemas.rag import RAGContextSchema, RetrievedChunkSchema


# 阅读注释（类）：封装 章节 证据 bundle Schema，定义跨模块传递的数据结构与字段约束。
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
