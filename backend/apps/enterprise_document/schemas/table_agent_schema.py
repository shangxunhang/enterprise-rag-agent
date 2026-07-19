# =============================================================================
# 中文阅读说明：企业文档生成业务模块，负责方案规划、检索、章节生成、引用和验收。
# 主要定义：StructuredFactSchema、TableAgentInputSchema、TableAnalysisSchema、TableAgentOutputSchema。建议先从公开入口函数开始，再沿调用关系向下阅读。
# =============================================================================
"""TableAgent input/output schemas."""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from pydantic import Field

from schemas.citation import CitationSchema
from schemas.common import ErrorSchema, SchemaBase, WarningSchema
from schemas.status import ExecutionStatus
from .project_input_schema import ProjectInputSchema


# 阅读注释（类）：封装 structured fact Schema，定义跨模块传递的数据结构与字段约束。
class StructuredFactSchema(SchemaBase):
    """封装 structured fact Schema，定义跨模块传递的数据结构与字段约束。"""
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


# 阅读注释（类）：封装 table Agent 输入 Schema，定义跨模块传递的数据结构与字段约束。
class TableAgentInputSchema(SchemaBase):
    """封装 table Agent 输入 Schema，定义跨模块传递的数据结构与字段约束。"""
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


# 阅读注释（类）：封装 table analysis Schema，定义跨模块传递的数据结构与字段约束。
class TableAnalysisSchema(SchemaBase):
    """封装 table analysis Schema，定义跨模块传递的数据结构与字段约束。"""
    table_summary: Optional[str] = None
    device_summary: Optional[str] = None
    project_scale: Dict[str, Any] = Field(default_factory=dict)
    device_list: List[Dict[str, Any]] = Field(default_factory=list)
    resource_config: List[Dict[str, Any]] = Field(default_factory=list)
    estimate_summary: Dict[str, Any] = Field(default_factory=dict)
    risk_items: List[Dict[str, Any]] = Field(default_factory=list)
    extra: Dict[str, Any] = Field(default_factory=dict)


# 阅读注释（类）：封装 table Agent 输出 Schema，定义跨模块传递的数据结构与字段约束。
class TableAgentOutputSchema(SchemaBase):
    """封装 table Agent 输出 Schema，定义跨模块传递的数据结构与字段约束。"""
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
