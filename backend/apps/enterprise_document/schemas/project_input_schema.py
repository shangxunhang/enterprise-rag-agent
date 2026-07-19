# =============================================================================
# 中文阅读说明：企业文档生成业务模块，负责方案规划、检索、章节生成、引用和验收。
# 主要定义：DepartmentGroupSchema、HardwareResourceSchema、SourceMaterialSchema、ManualBoundarySchema、GenerationRequirementsSchema、OutputSchemaDefinition、ProjectInputSchema。建议先从公开入口函数开始，再沿调用关系向下阅读。
# =============================================================================
"""Normalized project input contract for enterprise-document workflows."""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from pydantic import Field, model_validator

from schemas.common import SchemaBase


# 阅读注释（类）：封装 department group Schema，定义跨模块传递的数据结构与字段约束。
class DepartmentGroupSchema(SchemaBase):
    """封装 department group Schema，定义跨模块传递的数据结构与字段约束。"""
    group_name: str
    department_count: int
    approximate_staff_per_department: Optional[int] = None
    max_staff_per_department: Optional[int] = None
    description: Optional[str] = None


# 阅读注释（类）：封装 hardware resource Schema，定义跨模块传递的数据结构与字段约束。
class HardwareResourceSchema(SchemaBase):
    """封装 hardware resource Schema，定义跨模块传递的数据结构与字段约束。"""
    resource_type: str
    device_model: str
    server_count: int
    cards_per_server: Optional[int] = None
    total_cards: Optional[int] = None
    access_mode: Optional[str] = None
    purpose: Optional[str] = None
    description: Optional[str] = None


# 阅读注释（类）：封装 source material Schema，定义跨模块传递的数据结构与字段约束。
class SourceMaterialSchema(SchemaBase):
    """封装 source material Schema，定义跨模块传递的数据结构与字段约束。"""
    material_type: str
    material_name: Optional[str] = None
    status: str = "unknown"
    description: Optional[str] = None
    file_ids: List[str] = Field(default_factory=list)
    doc_ids: List[str] = Field(default_factory=list)
    metadata: Dict[str, Any] = Field(default_factory=dict)


# 阅读注释（类）：封装 manual boundary Schema，定义跨模块传递的数据结构与字段约束。
class ManualBoundarySchema(SchemaBase):
    """封装 manual boundary Schema，定义跨模块传递的数据结构与字段约束。"""
    item: str
    handled_by: str = "human"
    description: Optional[str] = None


# 阅读注释（类）：封装 生成 requirements Schema，定义跨模块传递的数据结构与字段约束。
class GenerationRequirementsSchema(SchemaBase):
    """封装 生成 requirements Schema，定义跨模块传递的数据结构与字段约束。"""
    required_sections: List[str] = Field(default_factory=list)
    need_citation: bool = True
    citation_required_sections: List[str] = Field(default_factory=list)
    need_human_review: bool = True
    min_section_chars: int = 80
    max_section_retries: int = 1
    max_context_chars: int = 6000
    max_tokens_per_section: int = 1024
    citation_style: str = "citation_id"
    language: str = "zh-CN"
    tone: str = "formal"
    extra: Dict[str, Any] = Field(default_factory=dict)


# 阅读注释（类）：封装 输出 Schema definition，集中封装相关状态、依赖和行为。
class OutputSchemaDefinition(SchemaBase):
    """封装 输出 Schema definition，集中封装相关状态、依赖和行为。"""
    document_title: Optional[str] = None
    output_format: str = "markdown"
    required_sections: List[str] = Field(default_factory=list)
    required_fields: List[str] = Field(default_factory=list)
    heading_style: str = "numbered_chinese"
    extra: Dict[str, Any] = Field(default_factory=dict)


# 阅读注释（类）：封装 项目 输入 Schema，定义跨模块传递的数据结构与字段约束。
class ProjectInputSchema(SchemaBase):
    """Single business input object entering the main workflow.

    The first group contains the universal entry fields. The remaining fields
    are enterprise-document domain extensions. No agent is allowed to invent a
    fallback ProjectInput when this object is missing.
    """

    schema_version: str = "project_input_v1"

    task_id: str
    tenant_id: str = "default"
    project_name: Optional[str] = None
    task_type: str
    user_query: str
    source_materials: List[SourceMaterialSchema] = Field(default_factory=list)
    generation_requirements: GenerationRequirementsSchema = Field(
        default_factory=GenerationRequirementsSchema
    )
    output_schema: OutputSchemaDefinition = Field(
        default_factory=OutputSchemaDefinition
    )
    metadata: Dict[str, Any] = Field(default_factory=dict)

    project_type: str = "unspecified"
    customer_type: Optional[str] = None
    business_goal: str = ""
    target_documents: List[str] = Field(default_factory=list)

    total_staff: Optional[int] = None
    functional_department_count: Optional[int] = None
    business_department_count: Optional[int] = None

    department_groups: List[DepartmentGroupSchema] = Field(default_factory=list)
    hardware_resources: List[HardwareResourceSchema] = Field(default_factory=list)

    target_templates: List[str] = Field(default_factory=list)
    policy_requirements: List[str] = Field(default_factory=list)
    manual_boundaries: List[ManualBoundarySchema] = Field(default_factory=list)

    missing_information: List[str] = Field(default_factory=list)
    conflicting_information: List[str] = Field(default_factory=list)
    extra: Dict[str, Any] = Field(default_factory=dict)

    # 阅读注释（函数）：处理 align 生成 and 输出 sections 相关逻辑。
    @model_validator(mode="after")
    def align_generation_and_output_sections(self) -> "ProjectInputSchema":
        """处理 align 生成 and 输出 sections 相关逻辑。

        返回:
            'ProjectInputSchema'

        阅读提示:
            主要直接调用：strip, str, len, set, ValueError, list, join, model_validator。
        """
        generation_sections = [
            str(item).strip()
            for item in self.generation_requirements.required_sections
            if str(item).strip()
        ]
        output_sections = [
            str(item).strip()
            for item in self.output_schema.required_sections
            if str(item).strip()
        ]
        if len(generation_sections) != len(set(generation_sections)):
            raise ValueError("generation_requirements.required_sections contains duplicates")
        if len(output_sections) != len(set(output_sections)):
            raise ValueError("output_schema.required_sections contains duplicates")

        if generation_sections and output_sections and generation_sections != output_sections:
            raise ValueError(
                "generation_requirements.required_sections and "
                "output_schema.required_sections must match"
            )
        if generation_sections and not output_sections:
            output_sections = list(generation_sections)
        elif output_sections and not generation_sections:
            generation_sections = list(output_sections)

        citation_sections = [
            str(item).strip()
            for item in self.generation_requirements.citation_required_sections
            if str(item).strip()
        ]
        unknown_citation_sections = [
            item for item in citation_sections if item not in generation_sections
        ]
        if unknown_citation_sections:
            raise ValueError(
                "citation_required_sections must be a subset of required_sections: "
                + ", ".join(unknown_citation_sections)
            )

        self.generation_requirements.required_sections = generation_sections
        self.generation_requirements.citation_required_sections = citation_sections
        self.output_schema.required_sections = output_sections

        if not self.business_goal:
            self.business_goal = self.user_query

        if not self.output_schema.document_title:
            self.output_schema.document_title = (
                f"{self.project_name}建设方案" if self.project_name else "项目建设方案"
            )

        return self
