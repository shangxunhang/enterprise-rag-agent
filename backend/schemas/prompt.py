"""Prompt schemas."""

from __future__ import annotations

from typing import Any, Dict, List, Optional
from pydantic import Field

from .common import SchemaBase


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