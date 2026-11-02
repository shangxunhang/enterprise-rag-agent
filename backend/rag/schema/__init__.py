"""框架无关的 RAG 运行信封与兼容 Schema 构造器包。"""

from .candidate import CandidateSet, RetrievalRequest
from .prompt_evidence_candidate_v1 import (
    PromptEvidenceCandidateV1Schema,
    build_prompt_evidence_candidate_v1,
)
from schemas.evidence_assessment import (
    CoverageStrategy,
    EvidenceAssessmentV2Schema,
    EvidenceConflictSchema,
    EvidenceRelevance,
    EvidenceSetAssessmentSchema,
    EvidenceStatus,
)
from .retrieval_candidate_v2 import (
    RetrievalCandidateV2Schema,
    build_retrieval_candidate_v2,
)
from .structured_evidence import (
    FactStatus,
    StructuredEvidenceSchema,
    build_structured_evidence,
)

__all__ = [
    "CandidateSet",
    "CoverageStrategy",
    "EvidenceAssessmentV2Schema",
    "EvidenceConflictSchema",
    "EvidenceRelevance",
    "EvidenceSetAssessmentSchema",
    "EvidenceStatus",
    "FactStatus",
    "PromptEvidenceCandidateV1Schema",
    "RetrievalCandidateV2Schema",
    "RetrievalRequest",
    "StructuredEvidenceSchema",
    "build_prompt_evidence_candidate_v1",
    "build_retrieval_candidate_v2",
    "build_structured_evidence",
]
