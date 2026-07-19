# =============================================================================
# 中文阅读说明：跨模块数据 Schema 定义模块。
# 主要定义：UserContextSchema、TaskContextSchema、ConversationContextSchema、BusinessContextSchema、EvidenceContextSchema、GenerationContextSchema、WorkflowStepStateSchema、RuntimeContextSchema、ContextBundleSchema、ContextItemSchema等。建议先从公开入口函数开始，再沿调用关系向下阅读。
# =============================================================================
"""Typed context schemas shared by the current workflow.

This is the base context contract. The later Context Manager phase can add
compression, prioritisation and token budgeting without changing callers.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

import hashlib

from pydantic import Field, model_validator

from .common import ErrorSchema, SchemaBase
from .status import ExecutionStatus


# 阅读注释（类）：封装 user 上下文 Schema，定义跨模块传递的数据结构与字段约束。
class UserContextSchema(SchemaBase):
    """封装 user 上下文 Schema，定义跨模块传递的数据结构与字段约束。"""
    user_id: Optional[str] = None
    tenant_id: str = "default"
    session_id: Optional[str] = None
    user_query: str
    metadata: Dict[str, Any] = Field(default_factory=dict)


# 阅读注释（类）：封装 任务 上下文 Schema，定义跨模块传递的数据结构与字段约束。
class TaskContextSchema(SchemaBase):
    """封装 任务 上下文 Schema，定义跨模块传递的数据结构与字段约束。"""
    task_id: str
    run_id: str
    task_type: str
    project_name: Optional[str] = None
    generation_requirements: Dict[str, Any] = Field(default_factory=dict)
    output_schema: Dict[str, Any] = Field(default_factory=dict)
    metadata: Dict[str, Any] = Field(default_factory=dict)


# 阅读注释（类）：封装 conversation 上下文 Schema，定义跨模块传递的数据结构与字段约束。
class ConversationContextSchema(SchemaBase):
    """封装 conversation 上下文 Schema，定义跨模块传递的数据结构与字段约束。"""
    messages: List[Dict[str, Any]] = Field(default_factory=list)
    summary: Optional[str] = None
    max_messages: int = 20
    metadata: Dict[str, Any] = Field(default_factory=dict)


# 阅读注释（类）：封装 business 上下文 Schema，定义跨模块传递的数据结构与字段约束。
class BusinessContextSchema(SchemaBase):
    """封装 business 上下文 Schema，定义跨模块传递的数据结构与字段约束。"""
    project_input: Dict[str, Any] = Field(default_factory=dict)
    source_materials: List[Dict[str, Any]] = Field(default_factory=list)
    missing_information: List[str] = Field(default_factory=list)
    conflicting_information: List[str] = Field(default_factory=list)
    manual_boundaries: List[Dict[str, Any]] = Field(default_factory=list)
    metadata: Dict[str, Any] = Field(default_factory=dict)


# 阅读注释（类）：封装 证据 上下文 Schema，定义跨模块传递的数据结构与字段约束。
class EvidenceContextSchema(SchemaBase):
    """封装 证据 上下文 Schema，定义跨模块传递的数据结构与字段约束。"""
    query: Optional[str] = None
    # Step 12 canonical structured truth. The fields below are compatibility
    # projections consumed by the current generation services.
    contract: Dict[str, Any] = Field(default_factory=dict)
    context_text: str = ""
    retrieved_chunks: List[Dict[str, Any]] = Field(default_factory=list)
    citations: List[Dict[str, Any]] = Field(default_factory=list)
    used_doc_ids: List[str] = Field(default_factory=list)
    evidence_available: bool = False
    assessment_status: str = "not_assessed"
    evidence_sufficient: Optional[bool] = None
    metadata: Dict[str, Any] = Field(default_factory=dict)


# 阅读注释（类）：封装 生成 上下文 Schema，定义跨模块传递的数据结构与字段约束。
class GenerationContextSchema(SchemaBase):
    """封装 生成 上下文 Schema，定义跨模块传递的数据结构与字段约束。"""
    document_id: Optional[str] = None
    document_title: Optional[str] = None
    required_sections: List[str] = Field(default_factory=list)
    current_section_id: Optional[str] = None
    current_section_title: Optional[str] = None
    generated_section_ids: List[str] = Field(default_factory=list)
    token_budget: Optional[int] = None
    metadata: Dict[str, Any] = Field(default_factory=dict)


# 阅读注释（类）：封装 工作流 step 状态 Schema，定义跨模块传递的数据结构与字段约束。
class WorkflowStepStateSchema(SchemaBase):
    """封装 工作流 step 状态 Schema，定义跨模块传递的数据结构与字段约束。"""
    step_id: str
    step_name: str
    target_name: str
    status: ExecutionStatus = ExecutionStatus.PENDING
    started_at: Optional[str] = None
    finished_at: Optional[str] = None
    attempt: int = 0
    error: Optional[ErrorSchema] = None
    metadata: Dict[str, Any] = Field(default_factory=dict)


# 阅读注释（类）：封装 运行时 上下文 Schema，定义跨模块传递的数据结构与字段约束。
class RuntimeContextSchema(SchemaBase):
    """封装 运行时 上下文 Schema，定义跨模块传递的数据结构与字段约束。"""
    status: ExecutionStatus = ExecutionStatus.PENDING
    current_step: Optional[str] = None
    workflow_step_states: Dict[str, WorkflowStepStateSchema] = Field(default_factory=dict)
    retry_count: int = 0
    errors: List[ErrorSchema] = Field(default_factory=list)
    metadata: Dict[str, Any] = Field(default_factory=dict)


# 阅读注释（类）：封装 上下文 bundle Schema，定义跨模块传递的数据结构与字段约束。
class ContextBundleSchema(SchemaBase):
    """封装 上下文 bundle Schema，定义跨模块传递的数据结构与字段约束。"""
    schema_version: str = "context_bundle_v1"

    user: UserContextSchema
    task: TaskContextSchema
    conversation: ConversationContextSchema = Field(
        default_factory=ConversationContextSchema
    )
    business: BusinessContextSchema = Field(default_factory=BusinessContextSchema)
    evidence: EvidenceContextSchema = Field(default_factory=EvidenceContextSchema)
    generation: GenerationContextSchema = Field(default_factory=GenerationContextSchema)
    runtime: RuntimeContextSchema = Field(default_factory=RuntimeContextSchema)


# 阅读注释（类）：封装 上下文 数据项 Schema，定义跨模块传递的数据结构与字段约束。
class ContextItemSchema(SchemaBase):
    """One candidate block considered for a single model invocation."""

    schema_version: str = "context_item_v1"

    item_id: str
    source_type: str
    title: str
    content: str

    priority: int = 50
    required: bool = False
    truncate_allowed: bool = True
    citation_ids: List[str] = Field(default_factory=list)
    metadata: Dict[str, Any] = Field(default_factory=dict)


# 阅读注释（类）：封装 上下文 预算 Schema，定义跨模块传递的数据结构与字段约束。
class ContextBudgetSchema(SchemaBase):
    """Deterministic input budget for one model call.

    ``estimated_*_tokens`` are tokenizer-independent planning estimates. The
    model gateway remains the source of truth for actual token usage.
    """

    schema_version: str = "context_budget_v1"

    max_context_chars: int
    max_input_tokens: int
    reserved_output_tokens: int = 0
    safety_margin_tokens: int = 0

    used_context_chars: int = 0
    estimated_input_tokens: int = 0
    remaining_context_chars: int = 0
    remaining_input_tokens: int = 0


# 阅读注释（类）：封装 上下文 decision Schema，定义跨模块传递的数据结构与字段约束。
class ContextDecisionSchema(SchemaBase):
    """Why one context candidate was retained, truncated or omitted."""

    schema_version: str = "context_decision_v1"

    item_id: str
    source_type: str
    action: str  # selected | truncated | dropped
    reason: str
    priority: int
    required: bool

    chars_before: int
    chars_after: int
    estimated_tokens_before: int
    estimated_tokens_after: int

    metadata: Dict[str, Any] = Field(default_factory=dict)


# 阅读注释（类）：封装 上下文 build 请求 Schema，定义跨模块传递的数据结构与字段约束。
class ContextBuildRequestSchema(SchemaBase):
    """Complete request for building one bounded LLM context package."""

    schema_version: str = "context_build_request_v1"

    task_id: str
    run_id: str
    call_purpose: str

    section_id: Optional[str] = None
    section_title: Optional[str] = None

    items: List[ContextItemSchema] = Field(default_factory=list)

    max_context_chars: int = 6000
    max_input_tokens: int = 8192
    reserved_output_tokens: int = 1024
    safety_margin_tokens: int = 256

    lineage: Dict[str, Any] = Field(default_factory=dict)
    metadata: Dict[str, Any] = Field(default_factory=dict)


    # 阅读注释（函数）：校验 请求。
    @model_validator(mode="after")
    def validate_request(self) -> "ContextBuildRequestSchema":
        """校验 请求。

        返回:
            'ContextBuildRequestSchema'

        阅读提示:
            主要直接调用：len, set, ValueError, model_validator。
        """
        item_ids = [item.item_id for item in self.items]
        if len(item_ids) != len(set(item_ids)):
            raise ValueError("context item_id values must be unique")
        if self.max_context_chars <= 0 or self.max_input_tokens <= 0:
            raise ValueError("context budgets must be positive")
        if self.reserved_output_tokens < 0 or self.safety_margin_tokens < 0:
            raise ValueError("reserved token budgets must be non-negative")
        if self.max_input_tokens <= (
            self.reserved_output_tokens + self.safety_margin_tokens
        ):
            raise ValueError("max_input_tokens must exceed reserved budgets")
        return self


# 阅读注释（类）：封装 llmcontext package Schema，定义跨模块传递的数据结构与字段约束。
class LLMContextPackageSchema(SchemaBase):
    """Canonical context consumed by one prompt/model call.

    Structured items and decisions are the source of truth. ``rendered_context``
    is a deterministic projection for the current text-prompt runtime.
    """

    schema_version: str = "llm_context_package_v1"

    package_id: str
    task_id: str
    run_id: str
    call_purpose: str

    section_id: Optional[str] = None
    section_title: Optional[str] = None

    selected_items: List[ContextItemSchema] = Field(default_factory=list)
    decisions: List[ContextDecisionSchema] = Field(default_factory=list)

    rendered_context: str = ""
    context_sha256: str
    budget: ContextBudgetSchema

    lineage: Dict[str, Any] = Field(default_factory=dict)
    warnings: List[str] = Field(default_factory=list)
    metadata: Dict[str, Any] = Field(default_factory=dict)

    # 阅读注释（函数）：校验 package。
    @model_validator(mode="after")
    def validate_package(self) -> "LLMContextPackageSchema":
        """校验 package。

        返回:
            'LLMContextPackageSchema'

        阅读提示:
            主要直接调用：len, set, ValueError, hexdigest, hashlib.sha256, self.rendered_context.encode, self.metadata.get, model_validator。
        """
        selected_ids = [item.item_id for item in self.selected_items]
        if len(selected_ids) != len(set(selected_ids)):
            raise ValueError("selected context item_id values must be unique")
        decision_ids = [item.item_id for item in self.decisions]
        if len(decision_ids) != len(set(decision_ids)):
            raise ValueError("context decisions must be unique by item_id")
        actual_hash = hashlib.sha256(
            self.rendered_context.encode("utf-8")
        ).hexdigest()
        if actual_hash != self.context_sha256:
            raise ValueError("context_sha256 does not match rendered_context")
        if self.budget.used_context_chars != len(self.rendered_context):
            raise ValueError("used_context_chars must match rendered_context length")
        if self.metadata.get("budget_enforced", True):
            usable_tokens = (
                self.budget.max_input_tokens
                - self.budget.reserved_output_tokens
                - self.budget.safety_margin_tokens
            )
            if self.budget.used_context_chars > self.budget.max_context_chars:
                raise ValueError("rendered context exceeds max_context_chars")
            if self.budget.estimated_input_tokens > usable_tokens:
                raise ValueError("estimated context tokens exceed input budget")
        return self
