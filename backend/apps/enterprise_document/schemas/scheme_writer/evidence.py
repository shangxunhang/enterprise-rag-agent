"""Section lifecycle carrier for final accepted canonical Evidence."""
from __future__ import annotations

from typing import Any, Dict, List, Optional

from pydantic import Field

from rag.evidence.contract import RAGEvidenceContractReader
from schemas.citation import CitationSchema
from schemas.common import SchemaBase
from schemas.rag import EvidenceBundleSchema, RAGContextSchema, RetrievedChunkSchema


class SectionEvidenceBundleSchema(SchemaBase):
    """Persist only the final accepted Evidence Bundle plus workflow-owned state.

    Evidence facts are never duplicated into section checkpoint fields.  The
    convenience properties below are one-way deterministic projections from the
    canonical ``evidence_bundle`` and are intentionally absent from ``model_fields``
    and serialized checkpoint state.
    """

    schema_version: str = "section_evidence_bundle_v2"
    section_id: str
    section_title: str
    evidence_bundle: Optional[EvidenceBundleSchema] = None
    tool_call_ids: List[str] = Field(default_factory=list)
    recovery_count: int = 0
    evidence_contract_sha256: Optional[str] = None
    writer_gate_binding: Optional[Dict[str, Any]] = None
    metadata: Dict[str, Any] = Field(default_factory=dict)

    @property
    def retrieval_scope(self) -> str:
        return self.evidence_bundle.retrieval_scope if self.evidence_bundle is not None else "document"

    @property
    def query(self) -> str:
        return self.evidence_bundle.query if self.evidence_bundle is not None else ""

    @property
    def rag_context(self) -> RAGContextSchema:
        if self.evidence_bundle is None:
            return RAGContextSchema()
        context, _, _ = RAGEvidenceContractReader.projections(self.evidence_bundle)
        return context

    @property
    def retrieved_chunks(self) -> List[RetrievedChunkSchema]:
        if self.evidence_bundle is None:
            return []
        _, chunks, _ = RAGEvidenceContractReader.projections(self.evidence_bundle)
        return list(chunks)

    @property
    def citations(self) -> List[CitationSchema]:
        if self.evidence_bundle is None:
            return []
        _, _, citations = RAGEvidenceContractReader.projections(self.evidence_bundle)
        return list(citations)

    @property
    def generation_allowed(self) -> Optional[bool]:
        if not isinstance(self.writer_gate_binding, dict):
            return None
        value = self.writer_gate_binding.get("generation_allowed")
        return bool(value) if value is not None else None
