"""单章节完整执行生命周期及并行 fan-in 的跨层契约。

本模块只描述输入快照、业务结果和允许合并的增量，不实现检索、生成、恢复或共享状态
写入。并行分支必须通过显式 Delta 回收，不能把隔离状态对象直接互相覆盖。
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from pydantic import ConfigDict, Field, field_validator

from agent.runtime.shared_state_schema import SharedStateSchema
from apps.enterprise_document.schemas.project_input_schema import ProjectInputSchema
from apps.enterprise_document.schemas.table_agent_schema import StructuredFactSchema
from schemas.citation import CitationSchema
from schemas.common import ErrorSchema, SchemaBase, WarningSchema
from schemas.rag import EvidenceBundleSchema, RAGContextSchema, RetrievedChunkSchema

from .document import SchemeSectionSchema
from .evidence import SectionEvidenceBundleSchema
from .planning import SectionPlanSchema
from .state_projection import SectionStateSchema


class SectionExecutionRequestSchema(SchemaBase):
    """执行一个章节生命周期所需的项目、计划、前文和证据快照。

    ``shared_state`` intentionally remains the existing working state during the
    first extraction step so model-output capture and other current side effects
    keep one source of truth. ``DocumentCitationRegistry`` is deliberately not
    owned by this schema; the document-level use case must inject the shared
    registry separately when it calls the future coordinator.
    """

    schema_version: str = "section_execution_request_v1"

    shared_state: SharedStateSchema
    document_id: str
    project_input: ProjectInputSchema
    section_plan: SectionPlanSchema

    structured_facts: List[StructuredFactSchema] = Field(default_factory=list)
    previous_sections: List[SchemeSectionSchema] = Field(default_factory=list)

    document_evidence_bundle: Optional[EvidenceBundleSchema] = None
    document_rag_context: RAGContextSchema
    document_retrieved_chunks: List[RetrievedChunkSchema] = Field(default_factory=list)
    document_citations: List[CitationSchema] = Field(default_factory=list)
    document_rag_output: Dict[str, Any] = Field(default_factory=dict)
    document_tool_call_ids: List[str] = Field(default_factory=list)

    section_retrieval_enabled: bool = True
    corrective_retrieval_enabled: bool = True
    parallel_isolated: bool = False
    budget_policy_override: Dict[str, Any] = Field(default_factory=dict)


class SectionExecutionResultSchema(SchemaBase):
    """一个章节的业务结果以及整个生命周期产生的全部检索产物。

    ``evidence`` is the final active evidence used by the section. The legacy
    ``retrieved_chunks`` field retains compatibility history, while ``rag_outputs``
    stores only lightweight retrieval trace/ref projections in call order. Phase 4B
    must never persist historical full EvidenceBundle copies in this result.
    """

    schema_version: str = "section_execution_result_v1"

    section: SchemeSectionSchema
    evidence: SectionEvidenceBundleSchema

    retrieved_chunks: List[RetrievedChunkSchema] = Field(default_factory=list)
    rag_outputs: List[Dict[str, Any]] = Field(default_factory=list)
    citation_registry: List[CitationSchema] = Field(default_factory=list)
    budget_usage: Dict[str, Any] = Field(default_factory=dict)
    need_human_review: bool = False
    section_state: Optional[SectionStateSchema] = None

    warnings: List[WarningSchema] = Field(default_factory=list)
    error: Optional[ErrorSchema] = None


class ParallelSectionStateDeltaSchema(SchemaBase):
    """隔离章节分支在 fan-in 时唯一允许提交的加法型 SharedState 更新。"""

    model_config = ConfigDict(extra="forbid")

    schema_version: str = "parallel_section_state_delta_v1"
    generated_section_ids: List[str] = Field(default_factory=list)
    generated_outputs: Dict[str, Any] = Field(default_factory=dict)
    warnings: List[WarningSchema] = Field(default_factory=list)
    errors: List[ErrorSchema] = Field(default_factory=list)
    runtime_errors: List[ErrorSchema] = Field(default_factory=list)
    metadata_updates: Dict[str, Dict[str, Any]] = Field(default_factory=dict)
    retry_count_delta: int = Field(default=0, ge=0)

    @field_validator("metadata_updates")
    @classmethod
    def _section_scoped_metadata_only(
        cls,
        value: Dict[str, Dict[str, Any]],
    ) -> Dict[str, Dict[str, Any]]:
        """并行分支只能更新 generation/runtime 元数据，禁止改写其他上下文域。"""
        unsupported = set(value) - {"generation", "runtime"}
        if unsupported:
            raise ValueError(
                "parallel metadata updates support only generation/runtime: "
                + ", ".join(sorted(unsupported))
            )
        return value


class ParallelSectionExecutionEnvelopeSchema(SchemaBase):
    """一个隔离章节分支的确定性 fan-in 包：业务结果与状态增量成对返回。"""

    model_config = ConfigDict(extra="forbid")

    schema_version: str = "parallel_section_execution_envelope_v1"
    section_id: str
    result: SectionExecutionResultSchema
    state_delta: ParallelSectionStateDeltaSchema
