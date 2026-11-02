"""项目输入规范化阶段的表格分析、结构化事实和兼容输出协议。"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from pydantic import Field

from schemas.citation import CitationSchema
from schemas.common import ErrorSchema, SchemaBase, WarningSchema
from schemas.status import ExecutionStatus
from .project_input_schema import ProjectInputSchema


class StructuredFactSchema(SchemaBase):
    """带来源和置信度的单条业务事实，供检索过滤和章节生成引用。"""
    schema_version: str = "structured_fact_v1"

    fact_id: str
    task_id: str
    run_id: str

    fact_type: str  # project_scale | device_summary | resource_config | estimate_summary | risk_item | other
    content: str

    source_type: str  # table | database | document | user_input | generated
    source_ids: List[str] = Field(default_factory=list)

    confidence: Optional[float] = None

    created_at: str

    extra: Dict[str, Any] = Field(default_factory=dict)


class TableAgentInputSchema(SchemaBase):
    """旧 TableAgent 接口的输入快照；当前主链由 ProjectInput 规范化用例消费。"""
    schema_version: str = "table_agent_input_v1"

    task_id: str
    run_id: str

    user_input: str

    file_ids: List[str] = Field(default_factory=list)
    table_refs: List[str] = Field(default_factory=list)

    requirements: Dict[str, Any] = Field(default_factory=dict)

    goal: str = "提取建设方案所需表格信息。"

    required_facts: List[str] = Field(
        default_factory=lambda: [
            "project_scale",
            "device_list",
            "resource_config",
            "estimate_summary",
            "risk_items",
        ]
    )

    extra: Dict[str, Any] = Field(default_factory=dict)


class TableAnalysisSchema(SchemaBase):
    """表格解析后的设备、规模、资源、投资摘要和风险结构。"""
    table_summary: Optional[str] = None
    device_summary: Optional[str] = None
    project_scale: Dict[str, Any] = Field(default_factory=dict)
    device_list: List[Dict[str, Any]] = Field(default_factory=list)
    resource_config: List[Dict[str, Any]] = Field(default_factory=list)
    estimate_summary: Dict[str, Any] = Field(default_factory=dict)
    risk_items: List[Dict[str, Any]] = Field(default_factory=list)
    extra: Dict[str, Any] = Field(default_factory=dict)


class TableAgentOutputSchema(SchemaBase):
    """规范化阶段的统一输出；ProjectInput 是事实源，旧表格字段仅用于迁移兼容。"""
    schema_version: str = "table_agent_output_v1"

    task_id: str
    run_id: str

    status: ExecutionStatus

    # 新增：真正的项目输入契约
    project_input: Optional[ProjectInputSchema] = None

    # 旧字段暂时保留，用于兼容 SchemeWriter
    table_analysis: TableAnalysisSchema = Field(default_factory=TableAnalysisSchema)
    structured_facts: List[StructuredFactSchema] = Field(default_factory=list)

    citations: List[CitationSchema] = Field(default_factory=list)
    warnings: List[WarningSchema] = Field(default_factory=list)
    error: Optional[ErrorSchema] = None

    extra: Dict[str, Any] = Field(default_factory=dict)
