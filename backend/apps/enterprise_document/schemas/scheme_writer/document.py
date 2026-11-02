"""生成章节与文档草稿的结构化协议。

章节保留输入、Prompt、原始模型输出、清洗后正文、引用绑定和评测结果；草稿只是这些
章节的聚合，尚不代表通过文档硬门禁的业务交付物。
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

from pydantic import Field

from apps.enterprise_document.schemas.scheme_writer.evaluation import SectionEvalSchema, TruncationCheckSchema
from schemas.citation import CitationBindingSchema
from schemas.common import ErrorSchema, SchemaBase, WarningSchema
from schemas.status import ExecutionStatus


class SchemeSectionSchema(SchemaBase):
    """一个章节从模型输入到评测完成的完整运行产物。"""
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
    """按计划顺序组装的文档草稿；即使被硬门禁拒绝也应保留用于诊断。"""
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
