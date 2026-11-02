"""四个业务 Agent 的最小强类型调用输入。"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from pydantic import Field

from apps.enterprise_document.schemas.project_input_schema import ProjectInputSchema
from apps.enterprise_document.schemas.scheme_writer.document import SchemeSectionSchema

from ._base import ContractSchema
from .enums import SectionStrategy, VerifierExecutionMode


class PlannerInvocationInputSchema(ContractSchema):
    schema_version: str = "planner_invocation_input_v2"
    template_schema_ref: str
    source_inventory_ref: str
    fact_snapshot_ref: str
    current_plan_ref: Optional[str] = None
    target_section_ids: List[str] = Field(default_factory=list)
    constraints: Dict[str, Any] = Field(default_factory=dict)

    # Production-mainline planning inputs. Standalone callers may omit them and
    # continue using artifact-backed planning.
    project_input: Optional[ProjectInputSchema] = None
    document_id: Optional[str] = None
    required_sections: List[str] = Field(default_factory=list)
    created_at: Optional[str] = None
    plan_revision: int = Field(default=1, ge=1)
    planning_source: str = "planner_agent"


class ResearchInvocationInputSchema(ContractSchema):
    schema_version: str = "research_invocation_input_v2"
    section_id: str
    section_task_ref: str
    fact_snapshot_ref: str
    source_inventory_ref: str
    evidence_topics: List[str] = Field(default_factory=list)
    prior_evidence_package_ref: Optional[str] = None

    # Round-4 mainline projection inputs. They are snapshots of canonical
    # evidence already produced by the production retrieval node; the fake
    # reasoner must not invent evidence, citations or tool calls.
    research_mode: str = "initial"
    retrieval_scope: str = "document"
    research_round: int = Field(default=0, ge=0)
    query: str = ""
    section_title: str = ""
    call_suffix: str = ""
    citation_required: bool = False
    evidence_assessment: Dict[str, Any] = Field(default_factory=dict)
    accepted_evidence_ids: List[str] = Field(default_factory=list)
    rejected_evidence_ids: List[str] = Field(default_factory=list)
    evidence_contract_sha256: Optional[str] = None
    tool_call_ids: List[str] = Field(default_factory=list)
    retrieval_succeeded: bool = True


class WriterInvocationInputSchema(ContractSchema):
    schema_version: str = "writer_invocation_input_v2"
    section_id: str
    strategy: SectionStrategy
    fact_snapshot_ref: str
    evidence_package_ref: str
    base_draft_ref: Optional[str] = None
    verifier_issue_refs: List[str] = Field(default_factory=list)
    generation_cursor: Optional[str] = None

    # Round-4 keeps the production SectionGenerationService authoritative. The
    # fake Writer reasoner receives its canonical output and emits a typed Agent
    # payload without creating a second draft artifact.
    section_snapshot: Optional[SchemeSectionSchema] = None
    generation_attempt: int = Field(default=1, ge=1)
    citation_required: bool = False
    generation_check: Dict[str, Any] = Field(default_factory=dict)


class VerifierInvocationInputSchema(ContractSchema):
    schema_version: str = "verifier_invocation_input_v2"
    execution_mode: VerifierExecutionMode
    target_artifact_ref: str
    fact_snapshot_ref: str
    section_id: Optional[str] = None
    claim_ids: List[str] = Field(default_factory=list)
    binding_refs: List[str] = Field(default_factory=list)
    verified_section_summary_refs: List[str] = Field(default_factory=list)

    # Canonical Section evaluation facts consumed by the Round-4 fake reasoner.
    section_snapshot: Optional[SchemeSectionSchema] = None
    generation_attempt: int = Field(default=1, ge=1)
    generation_check: Dict[str, Any] = Field(default_factory=dict)
    citation_required: bool = False
    evidence_blocked: bool = False
    corrective_retrieval_enabled: bool = True
