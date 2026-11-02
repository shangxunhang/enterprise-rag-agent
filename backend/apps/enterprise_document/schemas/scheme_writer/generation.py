"""方案生成用例的输入与运行选项协议。"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

from pydantic import Field

from apps.enterprise_document.schemas.project_input_schema import ProjectInputSchema
from apps.enterprise_document.schemas.table_agent_schema import StructuredFactSchema, TableAnalysisSchema
from schemas.common import SchemaBase


class SchemeGenerationOptionsSchema(SchemaBase):
    """从 ProjectInput 投影出的引用、预算、重试和人工复核控制项。"""
    need_citation: bool = True
    citation_required_sections: List[str] = Field(default_factory=list)
    need_word_export: bool = False
    need_human_review: bool = True
    max_context_chars: Optional[int] = 6000
    max_section_retries: int = 1
    max_tokens_per_section: int = 1024
    min_section_chars: int = 80
    extra: Dict[str, Any] = Field(default_factory=dict)


class SchemeWriterInputSchema(SchemaBase):
    """方案生成用例消费的完整输入快照，包含项目事实和规范化阶段产物。"""
    schema_version: str = "scheme_writer_input_v2"
    task_id: str
    run_id: str
    user_input: str
    requirements: Dict[str, Any] = Field(default_factory=dict)
    project_input: ProjectInputSchema
    table_analysis: TableAnalysisSchema = Field(default_factory=TableAnalysisSchema)
    structured_facts: List[StructuredFactSchema] = Field(default_factory=list)
    kb_ids: List[str] = Field(default_factory=list)
    template_id: Optional[str] = None
    required_sections: List[str] = Field(default_factory=list)
    generation_options: SchemeGenerationOptionsSchema = Field(default_factory=SchemeGenerationOptionsSchema)
    extra: Dict[str, Any] = Field(default_factory=dict)
