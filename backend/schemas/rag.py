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


class RAGToolInputSchema(SchemaBase):
    schema_version: str = "rag_tool_input_v1"

    task_id: str
    run_id: str
    agent_name: str

    query: str
    rewritten_queries: List[str] = Field(default_factory=list)

    kb_ids: List[str] = Field(default_factory=list)

    retrieval_mode: str = "hybrid"  # dense | keyword | hybrid

    top_k: int = 10
    dense_top_k: Optional[int] = 10
    keyword_top_k: Optional[int] = 10
    candidate_top_k: Optional[int] = 10
    rerank_top_k: Optional[int] = 5

    filters: Dict[str, Any] = Field(default_factory=dict)

    need_context: bool = True
    need_citation: bool = True

    max_context_chars: Optional[int] = 6000
    max_context_items: Optional[int] = 3

    score_threshold: Optional[float] = None

    # retrieve_only: return chunks/context only; qa_generate: RAGTool generates answer internally.
    mode: str = "retrieve_only"

    extra: Dict[str, Any] = Field(default_factory=dict)


class RetrievedChunkSchema(SchemaBase):
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


class RAGContextSchema(SchemaBase):
    schema_version: str = "rag_context_v1"

    context_text: str

    used_context_chunk_ids: List[str] = Field(default_factory=list)
    matched_chunk_ids: List[str] = Field(default_factory=list)
    used_doc_ids: List[str] = Field(default_factory=list)

    max_context_chars: int = 6000
    used_context_chars: int = 0
    context_item_count: int = 0

    context_format: str = "markdown"  # plain_text | markdown | structured

    extra: Dict[str, Any] = Field(default_factory=dict)


class RAGTraceSchema(SchemaBase):
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


class EvidenceDisposition(str, Enum):
    """Whether a retrieved evidence item entered the final LLM context."""

    SELECTED = "selected"
    DROPPED = "dropped"


class EvidenceAssessmentStatus(str, Enum):
    """Semantic sufficiency is not inferred merely from retrieval presence."""

    NOT_ASSESSED = "not_assessed"
    SUFFICIENT = "sufficient"
    PARTIAL = "partial"
    INSUFFICIENT = "insufficient"


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

    retrieval_strategy: Optional[str] = None
    pipeline_profile_id: Optional[str] = None
    pipeline_profile_version: Optional[str] = None
    pipeline_config_hash: Optional[str] = None

    extra: Dict[str, Any] = Field(default_factory=dict)


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


class RAGEvidenceContractSchema(SchemaBase):
    """Canonical evidence package crossing the RAG/Agent boundary.

    The structured items are the source of truth. ``context`` and the legacy
    fields on ``RAGToolOutputSchema`` are deterministic projections.
    """

    schema_version: str = "rag_evidence_contract_v1"

    query: str
    rewritten_queries: List[str] = Field(default_factory=list)

    items: List[RAGEvidenceItemSchema] = Field(default_factory=list)
    selected_evidence_ids: List[str] = Field(default_factory=list)
    dropped_evidence_ids: List[str] = Field(default_factory=list)

    citations: List[CitationSchema] = Field(default_factory=list)
    context: RAGContextSchema
    lineage: RAGEvidenceLineageSchema = Field(default_factory=RAGEvidenceLineageSchema)
    assessment: RAGEvidenceAssessmentSchema = Field(default_factory=RAGEvidenceAssessmentSchema)

    extra: Dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def validate_contract(self) -> "RAGEvidenceContractSchema":
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
        if self.context.context_item_count != len(self.selected_evidence_ids):
            raise ValueError("context item count must match selected evidence count")

        self.assessment.evidence_available = bool(self.selected_evidence_ids)
        self.assessment.selected_evidence_count = len(self.selected_evidence_ids)
        self.assessment.dropped_evidence_count = len(self.dropped_evidence_ids)
        self.assessment.citation_count = len(self.citations)
        return self


class RAGToolOutputSchema(SchemaBase):
    schema_version: str = "rag_tool_output_v1"

    task_id: str
    run_id: str

    status: ExecutionStatus

    query: str
    rewritten_queries: List[str] = Field(default_factory=list)

    # Step 12 canonical contract. The fields below remain compatibility
    # projections for current Agent/business consumers.
    evidence: Optional[RAGEvidenceContractSchema] = None

    retrieved_chunks: List[RetrievedChunkSchema] = Field(default_factory=list)
    context: Optional[RAGContextSchema] = None

    citations: List[CitationSchema] = Field(default_factory=list)
    trace: Optional[RAGTraceSchema] = None

    # Optional answer for rag_qa workflow. For scheme_generation use retrieve_only and leave answer empty/null.
    answer: Optional[str] = None

    warnings: List[WarningSchema] = Field(default_factory=list)
    error: Optional[ErrorSchema] = None

    extra: Dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def project_evidence_contract(self) -> "RAGToolOutputSchema":
        if self.evidence is None:
            return self
        if not self.context:
            self.context = self.evidence.context
        if not self.citations:
            self.citations = list(self.evidence.citations)
        return self
