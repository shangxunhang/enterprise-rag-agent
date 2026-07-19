# =============================================================================
# 中文阅读说明：企业文档生成业务模块，负责方案规划、检索、章节生成、引用和验收。
# 主要定义：SectionPlanSchema、DocumentPlanSchema。建议先从公开入口函数开始，再沿调用关系向下阅读。
# =============================================================================
"""Document and section planning schemas."""
from __future__ import annotations

from typing import Any, Dict, List

from pydantic import Field

from schemas.common import SchemaBase


# 阅读注释（类）：封装 章节 计划 Schema，定义跨模块传递的数据结构与字段约束。
class SectionPlanSchema(SchemaBase):
    """封装 章节 计划 Schema，定义跨模块传递的数据结构与字段约束。"""
    schema_version: str = "section_plan_v1"
    section_id: str
    section_title: str
    section_order: int
    citation_required: bool = False
    metadata: Dict[str, Any] = Field(default_factory=dict)


# 阅读注释（类）：封装 文档 计划 Schema，定义跨模块传递的数据结构与字段约束。
class DocumentPlanSchema(SchemaBase):
    """封装 文档 计划 Schema，定义跨模块传递的数据结构与字段约束。"""
    schema_version: str = "document_plan_v1"
    plan_id: str
    document_id: str
    document_title: str
    sections: List[SectionPlanSchema] = Field(default_factory=list)
    planning_source: str = "project_input"
    created_at: str
    metadata: Dict[str, Any] = Field(default_factory=dict)
