# =============================================================================
# 中文阅读说明：跨模块数据 Schema 定义模块。
# 主要定义：CitationSchema、CitationBindingSchema。建议先从公开入口函数开始，再沿调用关系向下阅读。
# =============================================================================
"""Citation source and target binding schemas."""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from pydantic import Field

from .common import SchemaBase


# 阅读注释（类）：封装 引用 Schema，定义跨模块传递的数据结构与字段约束。
class CitationSchema(SchemaBase):
    """封装 引用 Schema，定义跨模块传递的数据结构与字段约束。"""
    schema_version: str = "citation_v2"
    citation_id: str

    source_type: str
    doc_id: Optional[str] = None
    file_id: Optional[str] = None
    source_document_id: Optional[str] = None

    parent_chunk_id: Optional[str] = None
    child_chunk_id: Optional[str] = None
    chunk_id: Optional[str] = None

    table_id: Optional[str] = None
    row_ids: List[str] = Field(default_factory=list)

    title: Optional[str] = None
    section: Optional[str] = None
    page_start: Optional[int] = None
    page_end: Optional[int] = None

    quote_text: Optional[str] = None
    summary: Optional[str] = None
    confidence: Optional[float] = None

    extra: Dict[str, Any] = Field(default_factory=dict)


# 阅读注释（类）：封装 引用 绑定关系 Schema，定义跨模块传递的数据结构与字段约束。
class CitationBindingSchema(SchemaBase):
    """Bind one source citation to a concrete target claim location."""

    schema_version: str = "citation_binding_v1"

    binding_id: str
    citation_id: str

    target_document_id: str
    target_section_id: str
    target_paragraph_id: str
    target_claim_id: str

    source_document_id: Optional[str] = None
    source_chunk_id: Optional[str] = None
    source_parent_chunk_id: Optional[str] = None

    claim_text: str
    quote_text: Optional[str] = None
    confidence: Optional[float] = None

    metadata: Dict[str, Any] = Field(default_factory=dict)
