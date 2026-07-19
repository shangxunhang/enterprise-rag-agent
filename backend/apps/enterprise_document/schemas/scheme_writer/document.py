# =============================================================================
# 中文阅读说明：企业文档生成业务模块，负责方案规划、检索、章节生成、引用和验收。
# 主要定义：SchemeSectionSchema、SchemeDraftSchema。建议先从公开入口函数开始，再沿调用关系向下阅读。
# =============================================================================
"""Generated section and document schemas."""
from __future__ import annotations

from typing import Any, Dict, List, Optional

from pydantic import Field

from apps.enterprise_document.schemas.scheme_writer.evaluation import SectionEvalSchema, TruncationCheckSchema
from schemas.citation import CitationBindingSchema
from schemas.common import ErrorSchema, SchemaBase, WarningSchema
from schemas.status import ExecutionStatus


# 阅读注释（类）：封装 scheme 章节 Schema，定义跨模块传递的数据结构与字段约束。
class SchemeSectionSchema(SchemaBase):
    """封装 scheme 章节 Schema，定义跨模块传递的数据结构与字段约束。"""
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


# 阅读注释（类）：封装 scheme draft Schema，定义跨模块传递的数据结构与字段约束。
class SchemeDraftSchema(SchemaBase):
    """封装 scheme draft Schema，定义跨模块传递的数据结构与字段约束。"""
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
