# =============================================================================
# 中文阅读说明：跨模块数据 Schema 定义模块。
# 主要定义：PromptTemplateSchema、PromptRenderResultSchema。建议先从公开入口函数开始，再沿调用关系向下阅读。
# =============================================================================
"""Prompt schemas."""

from __future__ import annotations

from typing import Any, Dict, List, Optional
from pydantic import Field

from .common import SchemaBase


# 阅读注释（类）：封装 提示词 template Schema，定义跨模块传递的数据结构与字段约束。
class PromptTemplateSchema(SchemaBase):
    """Prompt template metadata and content."""

    schema_version: str = "prompt_template_v1"

    prompt_id: str
    prompt_name: str
    prompt_version: str

    task_type: Optional[str] = None
    scenario: Optional[str] = None

    template_path: str
    template_text: str

    variables: List[str] = Field(default_factory=list)

    metadata: Dict[str, Any] = Field(default_factory=dict)
    extra: Dict[str, Any] = Field(default_factory=dict)


# 阅读注释（类）：封装 提示词 render 结果 Schema，定义跨模块传递的数据结构与字段约束。
class PromptRenderResultSchema(SchemaBase):
    """Rendered prompt result."""

    schema_version: str = "prompt_render_result_v1"

    prompt_id: str
    prompt_name: str
    prompt_version: str

    rendered_text: str
    variables: Dict[str, Any] = Field(default_factory=dict)

    metadata: Dict[str, Any] = Field(default_factory=dict)
    extra: Dict[str, Any] = Field(default_factory=dict)