"""Task entry schema.

A Task is the runtime envelope. ProjectInput is carried as a validated payload
inside the envelope and must be provided by the caller or explicitly built by
an API/CLI adapter before the workflow starts.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from pydantic import Field

from .common import SchemaBase
from .status import ExecutionStatus


class TaskOptionsSchema(SchemaBase):
    need_table_analysis: bool = True
    need_rag: bool = True
    need_citation: bool = True
    need_word_export: bool = False
    need_human_review: bool = True
    retrieval_mode: str = "hybrid"
    max_context_chars: Optional[int] = 6000
    extra: Dict[str, Any] = Field(default_factory=dict)


class TaskSchema(SchemaBase):
    schema_version: str = "task_v2"

    task_id: str
    run_id: str
    tenant_id: str = "default"

    task_type: str
    task_name: Optional[str] = None
    project_name: Optional[str] = None

    user_id: Optional[str] = None
    session_id: Optional[str] = None

    user_input: str
    project_input: Dict[str, Any]
    source_materials: List[Dict[str, Any]] = Field(default_factory=list)
    generation_requirements: Dict[str, Any] = Field(default_factory=dict)
    output_schema: Dict[str, Any] = Field(default_factory=dict)
    metadata: Dict[str, Any] = Field(default_factory=dict)

    file_ids: List[str] = Field(default_factory=list)
    doc_ids: List[str] = Field(default_factory=list)
    kb_ids: List[str] = Field(default_factory=list)
    table_refs: List[str] = Field(default_factory=list)
    template_id: Optional[str] = None

    priority: str = "normal"
    status: ExecutionStatus = ExecutionStatus.PENDING

    options: TaskOptionsSchema = Field(default_factory=TaskOptionsSchema)

    created_at: str
    updated_at: Optional[str] = None

    extra: Dict[str, Any] = Field(default_factory=dict)
