"""把独立章节结果确定性聚合为完整文档的输入/输出契约。

这里不执行检索、生成或门禁，只携带章节、文档级与章节级证据、引用注册结果及充分性
判断，供装配器和文档硬门禁使用。
"""

from __future__ import annotations

from typing import Any, Dict, List, Literal, Set

from pydantic import Field

from schemas.citation import CitationBindingSchema, CitationSchema
from schemas.common import SchemaBase
from schemas.rag import RetrievedChunkSchema

from .document import SchemeDraftSchema, SchemeSectionSchema
from .evidence import SectionEvidenceBundleSchema


class DocumentAssemblyRequestSchema(SchemaBase):
    """装配完整文档所需的章节、证据、引用要求和时间快照。"""

    schema_version: str = "document_assembly_request_v1"

    task_id: str
    run_id: str
    document_id: str
    document_title: str

    required_sections: List[str] = Field(default_factory=list)
    sections: List[SchemeSectionSchema] = Field(default_factory=list)

    retrieved_chunks: List[RetrievedChunkSchema] = Field(default_factory=list)
    citations: List[CitationSchema] = Field(default_factory=list)
    section_evidence: List[SectionEvidenceBundleSchema] = Field(default_factory=list)

    document_evidence_available: bool = False
    document_assessment_status: str = "not_assessed"
    document_evidence_assessment: Dict[str, Any] = Field(default_factory=dict)
    citation_required_sections: List[str] = Field(default_factory=list)

    created_at: str
    updated_at: str


class SectionEvidenceRequirementSchema(SchemaBase):
    """单个章节独立评估后的证据要求满足状态。"""

    section_title: str
    assessment_status: Literal[
        "sufficient",
        "insufficient",
        "not_assessed",
        "blocked",
    ]
    generation_blocked: bool = False
    generation_block_reason: str | None = None
    requirement_satisfied: bool = False


class DocumentAssemblyResultSchema(SchemaBase):
    """供文档硬门禁与输出层消费的纯聚合结果，不等同于最终交付物。"""

    schema_version: str = "document_assembly_result_v1"

    draft: SchemeDraftSchema
    retrieved_chunks: List[RetrievedChunkSchema] = Field(default_factory=list)
    citations: List[CitationSchema] = Field(default_factory=list)
    citation_bindings: List[CitationBindingSchema] = Field(default_factory=list)
    known_chunk_ids: Set[str] = Field(default_factory=set)
    evidence_available: bool = False
    semantic_evidence_sufficient: bool = False
    evidence_requirements_satisfied: bool = False
    section_evidence_requirements: List[SectionEvidenceRequirementSchema] = Field(
        default_factory=list
    )
