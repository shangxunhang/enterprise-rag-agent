"""四个业务 Agent 的最小强类型调用输入。"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from pydantic import Field

from ._base import ContractSchema
from .enums import SectionStrategy, VerifierExecutionMode


class PlannerInvocationInputSchema(ContractSchema):
    schema_version: str = "planner_invocation_input_v1"
    template_schema_ref: str
    source_inventory_ref: str
    fact_snapshot_ref: str
    current_plan_ref: Optional[str] = None
    target_section_ids: List[str] = Field(default_factory=list)
    constraints: Dict[str, Any] = Field(default_factory=dict)


class ResearchInvocationInputSchema(ContractSchema):
    schema_version: str = "research_invocation_input_v1"
    section_id: str
    section_task_ref: str
    fact_snapshot_ref: str
    source_inventory_ref: str
    evidence_topics: List[str] = Field(default_factory=list)
    prior_evidence_package_ref: Optional[str] = None


class WriterInvocationInputSchema(ContractSchema):
    schema_version: str = "writer_invocation_input_v1"
    section_id: str
    strategy: SectionStrategy
    fact_snapshot_ref: str
    evidence_package_ref: str
    base_draft_ref: Optional[str] = None
    verifier_issue_refs: List[str] = Field(default_factory=list)
    generation_cursor: Optional[str] = None


class VerifierInvocationInputSchema(ContractSchema):
    schema_version: str = "verifier_invocation_input_v1"
    execution_mode: VerifierExecutionMode
    target_artifact_ref: str
    fact_snapshot_ref: str
    section_id: Optional[str] = None
    claim_ids: List[str] = Field(default_factory=list)
    binding_refs: List[str] = Field(default_factory=list)
    verified_section_summary_refs: List[str] = Field(default_factory=list)
