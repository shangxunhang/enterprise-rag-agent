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


class UserContextSchema(SchemaBase):
    user_id: Optional[str] = None
    tenant_id: str = "default"
    session_id: Optional[str] = None
    user_query: str
    metadata: Dict[str, Any] = Field(default_factory=dict)


class TaskContextSchema(SchemaBase):
    task_id: str
    run_id: str
    task_type: str
    project_name: Optional[str] = None
    generation_requirements: Dict[str, Any] = Field(default_factory=dict)
    output_schema: Dict[str, Any] = Field(default_factory=dict)
    metadata: Dict[str, Any] = Field(default_factory=dict)


class ConversationContextSchema(SchemaBase):
    messages: List[Dict[str, Any]] = Field(default_factory=list)
    summary: Optional[str] = None
    max_messages: int = 20
    metadata: Dict[str, Any] = Field(default_factory=dict)


class BusinessContextSchema(SchemaBase):
    project_input: Dict[str, Any] = Field(default_factory=dict)
    source_materials: List[Dict[str, Any]] = Field(default_factory=list)
    missing_information: List[str] = Field(default_factory=list)
    conflicting_information: List[str] = Field(default_factory=list)
    manual_boundaries: List[Dict[str, Any]] = Field(default_factory=list)
    metadata: Dict[str, Any] = Field(default_factory=dict)


class EvidenceContextSchema(SchemaBase):
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


class GenerationContextSchema(SchemaBase):
    document_id: Optional[str] = None
    document_title: Optional[str] = None
    required_sections: List[str] = Field(default_factory=list)
    current_section_id: Optional[str] = None
    current_section_title: Optional[str] = None
    generated_section_ids: List[str] = Field(default_factory=list)
    token_budget: Optional[int] = None
    metadata: Dict[str, Any] = Field(default_factory=dict)


class WorkflowStepStateSchema(SchemaBase):
    step_id: str
    step_name: str
    target_name: str
    status: ExecutionStatus = ExecutionStatus.PENDING
    started_at: Optional[str] = None
    finished_at: Optional[str] = None
    attempt: int = 0
    error: Optional[ErrorSchema] = None
    metadata: Dict[str, Any] = Field(default_factory=dict)


class RuntimeContextSchema(SchemaBase):
    status: ExecutionStatus = ExecutionStatus.PENDING
    current_step: Optional[str] = None
    workflow_step_states: Dict[str, WorkflowStepStateSchema] = Field(default_factory=dict)
    retry_count: int = 0
    errors: List[ErrorSchema] = Field(default_factory=list)
    metadata: Dict[str, Any] = Field(default_factory=dict)


class ContextBundleSchema(SchemaBase):
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


    @model_validator(mode="after")
    def validate_request(self) -> "ContextBuildRequestSchema":
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

    @model_validator(mode="after")
    def validate_package(self) -> "LLMContextPackageSchema":
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
