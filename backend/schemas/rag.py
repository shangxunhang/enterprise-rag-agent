# =============================================================================
# 中文阅读说明：跨模块数据 Schema 定义模块。
# 主要定义：RAGToolInputSchema、RetrievedChunkSchema、RAGContextSchema、RAGTraceSchema、EvidenceDisposition、EvidenceAssessmentStatus、RAGEvidenceLineageSchema、RAGEvidenceItemSchema、RAGEvidenceAssessmentSchema、RAGEvidenceContractSchema等。建议先从公开入口函数开始，再沿调用关系向下阅读。
# =============================================================================
"""RAGTool boundary schemas.

These schemas are the Agent-facing contract of RAGTool. They intentionally do
not expose the full internal rag-template schemas such as VectorIndexRecord.
"""

from __future__ import annotations

from enum import Enum
from typing import Any, Dict, List, Optional

from pydantic import Field, model_validator

from schemas.citation import CitationSchema
from schemas.common import ErrorSchema, SchemaBase, WarningSchema
from schemas.status import ExecutionStatus


class RetrievalAccessScopeSchema(SchemaBase):
    """System-authorized retrieval boundary enforced by every retriever.

    This is not an authentication/RBAC model.  It is the effective retrieval
    scope produced by the application/security boundary.  Agents may narrow
    this scope, but must never broaden it.
    """

    schema_version: str = "retrieval_access_scope_v1"
    tenant_id: str
    authorized_kb_ids: List[str] = Field(default_factory=list)
    allowed_file_ids: List[str] = Field(default_factory=list)
    allowed_doc_ids: List[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_scope(self) -> "RetrievalAccessScopeSchema":
        self.tenant_id = str(self.tenant_id or "").strip()
        self.authorized_kb_ids = list(
            dict.fromkeys(str(item).strip() for item in self.authorized_kb_ids if str(item).strip())
        )
        self.allowed_file_ids = list(
            dict.fromkeys(str(item).strip() for item in self.allowed_file_ids if str(item).strip())
        )
        self.allowed_doc_ids = list(
            dict.fromkeys(str(item).strip() for item in self.allowed_doc_ids if str(item).strip())
        )
        if not self.tenant_id:
            raise ValueError("retrieval access scope requires tenant_id")
        if not self.authorized_kb_ids:
            raise ValueError("retrieval access scope requires at least one authorized_kb_id")
        return self


# 阅读注释（类）：封装 ragtool 输入 Schema，定义跨模块传递的数据结构与字段约束。
class RAGToolInputSchema(SchemaBase):
    """封装 ragtool 输入 Schema，定义跨模块传递的数据结构与字段约束。"""
    schema_version: str = "rag_tool_input_v1"

    task_id: str
    run_id: str
    agent_name: str

    query: str
    rewritten_queries: List[str] = Field(default_factory=list)

    kb_ids: List[str] = Field(default_factory=list)
    access_scope: Optional[RetrievalAccessScopeSchema] = None

    # Legacy caller-provided narrowing filters.  Security-sensitive mandatory
    # tenant/KB boundaries live in access_scope and cannot be overridden here.
    filters: Dict[str, Any] = Field(default_factory=dict)

    need_citation: bool = True

    max_context_chars: Optional[int] = 6000
    max_context_items: Optional[int] = 3

    extra: Dict[str, Any] = Field(default_factory=dict)


# 阅读注释（类）：封装 retrieved 文本块 Schema，定义跨模块传递的数据结构与字段约束。
class RetrievedChunkSchema(SchemaBase):
    """封装 retrieved 文本块 Schema，定义跨模块传递的数据结构与字段约束。"""
    schema_version: str = "retrieved_chunk_v1"

    rank: int

    score: Optional[float] = None
    score_type: str = "unknown"  # dense_distance | dense_similarity | keyword_score | rrf_score | weighted_score | unknown

    rerank_score: Optional[float] = None
    rerank_score_type: Optional[str] = None  # cross_encoder_logit | normalized_score | unknown | null

    matched_chunk_id: str
    context_chunk_id: str

    child_chunk_id: Optional[str] = None
    parent_chunk_id: Optional[str] = None
    doc_id: str

    matched_granularity: str = "child"  # child | parent | chunk | document
    context_granularity: str = "parent"  # parent | chunk | document

    match_text: str
    context_text: str

    title: Optional[str] = None
    section: Optional[str] = None
    section_level: Optional[int] = None
    page_start: Optional[int] = None
    page_end: Optional[int] = None

    retrieval_sources: List[str] = Field(default_factory=list)

    metadata: Dict[str, Any] = Field(default_factory=dict)
    extra: Dict[str, Any] = Field(default_factory=dict)


# 阅读注释（类）：封装 ragcontext Schema，定义跨模块传递的数据结构与字段约束。
class RAGContextSchema(SchemaBase):
    """封装 ragcontext Schema，定义跨模块传递的数据结构与字段约束。"""
    schema_version: str = "rag_context_v1"

    context_text: str

    used_context_chunk_ids: List[str] = Field(default_factory=list)
    matched_chunk_ids: List[str] = Field(default_factory=list)
    used_doc_ids: List[str] = Field(default_factory=list)

    max_context_chars: int = 6000
    used_context_chars: int = 0
    context_item_count: int = 0
    token_budget: Optional[int] = None
    tokens_used: int = 0
    truncated_item_ids: List[str] = Field(default_factory=list)

    context_format: str = "markdown"  # plain_text | markdown | structured

    extra: Dict[str, Any] = Field(default_factory=dict)


# 阅读注释（类）：封装 ragtrace Schema，定义跨模块传递的数据结构与字段约束。
class RAGTraceSchema(SchemaBase):
    """封装 ragtrace Schema，定义跨模块传递的数据结构与字段约束。"""
    schema_version: str = "rag_trace_v1"

    retrieval_mode: str

    query: str
    rewritten_queries: List[str] = Field(default_factory=list)

    embedding_model: Optional[str] = None
    embedding_version: Optional[str] = None

    reranker_model: Optional[str] = None
    reranker_version: Optional[str] = None

    index_name: Optional[str] = None
    index_version: Optional[str] = None
    vector_db: Optional[str] = None  # milvus | faiss | elasticsearch | other | null

    dense_top_k: Optional[int] = None
    keyword_top_k: Optional[int] = None
    candidate_top_k: Optional[int] = None
    rerank_top_k: Optional[int] = None

    max_context_chars: Optional[int] = None

    retrieved_count: int = 0
    reranked_count: int = 0
    context_item_count: int = 0

    latency_ms: Optional[int] = None

    extra: Dict[str, Any] = Field(default_factory=dict)


# 阅读注释（类）：封装 证据 disposition，集中封装相关状态、依赖和行为。
class EvidenceDisposition(str, Enum):
    """Whether a retrieved evidence item entered the final LLM context."""

    SELECTED = "selected"
    DROPPED = "dropped"


# 阅读注释（类）：封装 证据 assessment 状态，集中封装相关状态、依赖和行为。
class EvidenceAssessmentStatus(str, Enum):
    """Semantic sufficiency is not inferred merely from retrieval presence."""

    NOT_ASSESSED = "not_assessed"
    SUFFICIENT = "sufficient"
    PARTIAL = "partial"
    INSUFFICIENT = "insufficient"


# 阅读注释（类）：封装 ragevidence lineage Schema，定义跨模块传递的数据结构与字段约束。
class RAGEvidenceLineageSchema(SchemaBase):
    """Versions that produced the evidence bundle."""

    schema_version: str = "rag_evidence_lineage_v1"

    index_name: Optional[str] = None
    index_version: Optional[str] = None
    dataset_version: Optional[str] = None
    vector_db: Optional[str] = None

    embedding_model: Optional[str] = None
    embedding_version: Optional[str] = None
    embedding_dim: Optional[int] = None

    reranker_model: Optional[str] = None
    reranker_version: Optional[str] = None

    retrieval_plan_id: Optional[str] = None
    static_retrieval_spec_id: Optional[str] = None
    static_retrieval_spec_version: Optional[str] = None
    static_retrieval_spec_hash: Optional[str] = None

    extra: Dict[str, Any] = Field(default_factory=dict)


# 阅读注释（类）：封装 ragevidence 数据项 Schema，定义跨模块传递的数据结构与字段约束。
class RAGEvidenceItemSchema(SchemaBase):
    """One normalized evidence item.

    ``match_text`` is the child-level evidence span used for grounding.
    ``context_text`` is the parent-level text available for generation.
    """

    schema_version: str = "rag_evidence_item_v1"

    evidence_id: str
    disposition: EvidenceDisposition
    rank: int
    pre_context_rank: Optional[int] = None

    matched_chunk_id: str
    context_chunk_id: str
    child_chunk_id: Optional[str] = None
    parent_chunk_id: Optional[str] = None
    doc_id: str

    match_text: str
    context_text: str

    title: Optional[str] = None
    section: Optional[str] = None
    page_start: Optional[int] = None
    page_end: Optional[int] = None

    score: Optional[float] = None
    score_type: str = "unknown"
    rerank_score: Optional[float] = None
    rerank_score_type: Optional[str] = None
    retrieval_sources: List[str] = Field(default_factory=list)

    citation_ids: List[str] = Field(default_factory=list)
    drop_reason: Optional[str] = None

    metadata: Dict[str, Any] = Field(default_factory=dict)
    extra: Dict[str, Any] = Field(default_factory=dict)


# 阅读注释（类）：封装 ragevidence assessment Schema，定义跨模块传递的数据结构与字段约束。
class RAGEvidenceAssessmentSchema(SchemaBase):
    """Evidence availability and optional semantic sufficiency judgment."""

    schema_version: str = "rag_evidence_assessment_v1"

    status: EvidenceAssessmentStatus = EvidenceAssessmentStatus.NOT_ASSESSED
    evidence_available: bool = False
    selected_evidence_count: int = 0
    dropped_evidence_count: int = 0
    citation_count: int = 0

    judge_name: Optional[str] = None
    judge_version: Optional[str] = None
    score: Optional[float] = None
    reason_codes: List[str] = Field(default_factory=list)
    details: Dict[str, Any] = Field(default_factory=dict)


# 阅读注释（类）：封装 ragevidence contract Schema，定义跨模块传递的数据结构与字段约束。
class EvidenceBundleSchema(SchemaBase):
    """Canonical evidence package crossing the RAG/Agent boundary.

    The structured items are the source of truth. ``context`` and the legacy
    fields on ``RAGToolOutputSchema`` are deterministic projections.
    """

    schema_version: str = "rag_evidence_contract_v1"

    query: str
    rewritten_queries: List[str] = Field(default_factory=list)

    task_id: Optional[str] = None
    run_id: Optional[str] = None
    status: ExecutionStatus = ExecutionStatus.SUCCESS
    retrieval_trace_id: Optional[str] = None

    items: List[RAGEvidenceItemSchema] = Field(default_factory=list)
    selected_evidence_ids: List[str] = Field(default_factory=list)
    dropped_evidence_ids: List[str] = Field(default_factory=list)

    citations: List[CitationSchema] = Field(default_factory=list)
    context: RAGContextSchema
    lineage: RAGEvidenceLineageSchema = Field(default_factory=RAGEvidenceLineageSchema)
    assessment: RAGEvidenceAssessmentSchema = Field(default_factory=RAGEvidenceAssessmentSchema)

    correction_trace: List[Dict[str, Any]] = Field(default_factory=list)
    budget_usage: Dict[str, Any] = Field(default_factory=dict)
    trace: Optional[RAGTraceSchema] = None
    warnings: List[WarningSchema] = Field(default_factory=list)
    error: Optional[ErrorSchema] = None

    extra: Dict[str, Any] = Field(default_factory=dict)

    # 阅读注释（函数）：校验 contract。
    @model_validator(mode="after")
    def validate_contract(self) -> "EvidenceBundleSchema":
        """校验 contract。

        返回:
            'RAGEvidenceContractSchema'

        阅读提示:
            主要直接调用：len, ValueError, set, sorted, bool, model_validator。
        """
        item_by_id = {item.evidence_id: item for item in self.items}
        if len(item_by_id) != len(self.items):
            raise ValueError("evidence_id values must be unique")

        selected = set(self.selected_evidence_ids)
        dropped = set(self.dropped_evidence_ids)
        if selected & dropped:
            raise ValueError("selected and dropped evidence ids must be disjoint")
        unknown = (selected | dropped) - set(item_by_id)
        if unknown:
            raise ValueError(f"evidence id references are missing from items: {sorted(unknown)}")

        for evidence_id in selected:
            if item_by_id[evidence_id].disposition != EvidenceDisposition.SELECTED:
                raise ValueError(f"selected evidence has wrong disposition: {evidence_id}")
        for evidence_id in dropped:
            if item_by_id[evidence_id].disposition != EvidenceDisposition.DROPPED:
                raise ValueError(f"dropped evidence has wrong disposition: {evidence_id}")

        citation_ids = [item.citation_id for item in self.citations]
        if len(set(citation_ids)) != len(citation_ids):
            raise ValueError("citation_id values must be unique")
        citation_id_set = set(citation_ids)
        for item in self.items:
            missing = set(item.citation_ids) - citation_id_set
            if missing:
                raise ValueError(
                    f"evidence item references unknown citations: {item.evidence_id} -> {sorted(missing)}"
                )
            if item.disposition == EvidenceDisposition.DROPPED and item.citation_ids:
                raise ValueError("dropped evidence must not expose prompt citation ids")

        if self.context.used_context_chars != len(self.context.context_text):
            raise ValueError("context used_context_chars must equal rendered text length")
        if self.context.used_context_chars > self.context.max_context_chars:
            raise ValueError("rendered context exceeds max_context_chars")
        if (
            self.context.token_budget is not None
            and self.context.tokens_used > self.context.token_budget
        ):
            raise ValueError("rendered context exceeds token_budget")
        if self.context.context_item_count != len(self.selected_evidence_ids):
            raise ValueError("context item count must match selected evidence count")

        self.assessment.evidence_available = bool(self.selected_evidence_ids)
        self.assessment.selected_evidence_count = len(self.selected_evidence_ids)
        self.assessment.dropped_evidence_count = len(self.dropped_evidence_ids)
        self.assessment.citation_count = len(self.citations)
        return self

    @property
    def success(self) -> bool:
        return self.status in {
            ExecutionStatus.SUCCESS,
            ExecutionStatus.PARTIAL_SUCCESS,
        }

    @property
    def error_message(self) -> Optional[str]:
        return self.error.message if self.error is not None else None


# Temporary import alias for callers that still use the Step-12 name.  There is
# one runtime schema, not two competing evidence contracts.
RAGEvidenceContractSchema = EvidenceBundleSchema


# 阅读注释（类）：封装 ragtool 输出 Schema，定义跨模块传递的数据结构与字段约束。
class RAGToolOutputSchema(SchemaBase):
    """封装 ragtool 输出 Schema，定义跨模块传递的数据结构与字段约束。"""
    schema_version: str = "rag_tool_output_v1"

    task_id: str
    run_id: str

    status: ExecutionStatus

    query: str
    rewritten_queries: List[str] = Field(default_factory=list)

    # Step 12 canonical contract. The fields below remain compatibility
    # projections for current Agent/business consumers.
    evidence: Optional[EvidenceBundleSchema] = None

    retrieved_chunks: List[RetrievedChunkSchema] = Field(default_factory=list)
    context: Optional[RAGContextSchema] = None

    citations: List[CitationSchema] = Field(default_factory=list)
    trace: Optional[RAGTraceSchema] = None

    # Optional answer for rag_qa workflow. For scheme_generation use retrieve_only and leave answer empty/null.
    answer: Optional[str] = None

    warnings: List[WarningSchema] = Field(default_factory=list)
    error: Optional[ErrorSchema] = None

    extra: Dict[str, Any] = Field(default_factory=dict)

    # 阅读注释（函数）：处理 项目 证据 contract 相关逻辑。
    @model_validator(mode="after")
    def project_evidence_contract(self) -> "RAGToolOutputSchema":
        """处理 项目 证据 contract 相关逻辑。

        返回:
            'RAGToolOutputSchema'

        阅读提示:
            主要直接调用：list, model_validator。
        """
        if self.evidence is None:
            return self
        if not self.context:
            self.context = self.evidence.context
        if not self.citations:
            self.citations = list(self.evidence.citations)
        return self
