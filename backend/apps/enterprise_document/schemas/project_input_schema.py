"""企业文档主链唯一允许进入的规范化项目输入契约。

该文件把项目事实、材料索引、人工边界、生成要求和输出结构放在一个严格对象中。生产
入口不得在缺失 ProjectInput 时由 Agent 自行编造；demo 所需的可缺省字段由文件末尾的
独立 Seed Schema 承担，避免污染正式业务契约。
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from pydantic import ConfigDict, Field, create_model, model_validator
from pydantic.fields import PydanticUndefined

from schemas.common import SchemaBase


class DepartmentGroupSchema(SchemaBase):
    """一类部门的数量和人员规模事实，用于描述组织结构而非逐部门建模。"""
    model_config = ConfigDict(extra="forbid", strict=True, arbitrary_types_allowed=True)

    group_name: str
    department_count: int
    approximate_staff_per_department: Optional[int] = None
    max_staff_per_department: Optional[int] = None
    description: Optional[str] = None


class HardwareResourceSchema(SchemaBase):
    """已确认硬件资源及其访问方式；未知配置应留空而不是由生成模型推断。"""
    model_config = ConfigDict(extra="forbid", strict=True, arbitrary_types_allowed=True)

    resource_type: str
    device_model: str
    server_count: int
    cards_per_server: Optional[int] = None
    total_cards: Optional[int] = None
    access_mode: Optional[str] = None
    purpose: Optional[str] = None
    description: Optional[str] = None


class SourceMaterialSchema(SchemaBase):
    """业务材料描述及其文件、文档和知识库定位元数据。"""
    model_config = ConfigDict(extra="forbid", strict=True, arbitrary_types_allowed=True)

    material_type: str
    material_name: Optional[str] = None
    status: str = "unknown"
    description: Optional[str] = None
    file_ids: List[str] = Field(default_factory=list)
    doc_ids: List[str] = Field(default_factory=list)
    metadata: Dict[str, Any] = Field(default_factory=dict)


class ManualBoundarySchema(SchemaBase):
    """明确必须由人工或外部专业工具处理、不能交给文本模型推断的事项。"""
    model_config = ConfigDict(extra="forbid", strict=True, arbitrary_types_allowed=True)

    item: str
    handled_by: str = "human"
    description: Optional[str] = None


class GenerationRequirementsSchema(SchemaBase):
    """控制章节集合、引用要求、重试次数、上下文预算和语言风格。"""
    model_config = ConfigDict(extra="forbid", strict=True, arbitrary_types_allowed=True)

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


class OutputSchemaDefinition(SchemaBase):
    """定义最终文档标题、格式、章节和必备字段，不包含具体正文。"""
    model_config = ConfigDict(extra="forbid", strict=True, arbitrary_types_allowed=True)

    document_title: Optional[str] = None
    output_format: str = "markdown"
    required_sections: List[str] = Field(default_factory=list)
    required_fields: List[str] = Field(default_factory=list)
    heading_style: str = "numbered_chinese"
    extra: Dict[str, Any] = Field(default_factory=dict)


class ProjectInputSchema(SchemaBase):
    """Single business input object entering the main workflow.

    The first group contains the universal entry fields. The remaining fields
    are enterprise-document domain extensions. No agent is allowed to invent a
    fallback ProjectInput when this object is missing.
    """

    model_config = ConfigDict(extra="forbid", strict=True, arbitrary_types_allowed=True)

    schema_version: str = "project_input_v1"

    task_id: str
    tenant_id: str = "default"
    project_name: Optional[str] = None
    current_project_id: Optional[str] = None
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

    @model_validator(mode="after")
    def align_generation_and_output_sections(self) -> "ProjectInputSchema":
        """对齐生成章节与输出章节，并补齐安全的派生默认值。

        两处章节声明同时存在时必须顺序完全一致；只提供一处时可单向复制。引用必需章节
        必须是总体章节集合的子集。这里只补业务目标和文档标题等可确定派生值，不补人员、
        硬件、预算或客户现状等事实字段。
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
        if not generation_sections:
            raise ValueError(
                "ProjectInput requires at least one required_sections entry"
            )

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


def _build_demo_seed_schema():
    """Build the explicit strict contract accepted by the CLI demo adapter."""

    seed_fields: dict[str, tuple[Any, Any]] = {}
    for field_name, field_info in ProjectInputSchema.model_fields.items():
        default = (
            None
            if field_info.default is PydanticUndefined
            else field_info.default
        )
        seed_fields[field_name] = (field_info.annotation, default)
    return create_model(
        "ProjectInputDemoSeedV1Schema",
        __config__=ConfigDict(
            extra="forbid",
            strict=True,
            arbitrary_types_allowed=True,
        ),
        **seed_fields,
    )


ProjectInputDemoSeedV1Schema = _build_demo_seed_schema()
