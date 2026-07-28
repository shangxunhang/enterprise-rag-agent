"""方案生成链使用的规划、执行、评测、草稿和交付物 Schema 汇总。"""

from .generation import SchemeGenerationOptionsSchema, SchemeWriterInputSchema
from .planning import DocumentPlanSchema, SectionPlanSchema
from .evaluation import (
    HardGateResultSchema,
    SectionEvalSchema,
    SemanticGateIssueSchema,
    SemanticGateResultSchema,
    TruncationCheckSchema,
)
from .document import SchemeDraftSchema, SchemeSectionSchema
from .output import (
    DeliverableFinalResultSchema,
    RejectedDraftArtifactSchema,
    SchemeWriterOutputSchema,
)
from .evidence import SectionEvidenceBundleSchema
from .section_execution import (
    ParallelSectionExecutionEnvelopeSchema,
    ParallelSectionStateDeltaSchema,
    SectionExecutionRequestSchema,
    SectionExecutionResultSchema,
)
from .document_assembly import (
    DocumentAssemblyRequestSchema,
    DocumentAssemblyResultSchema,
    SectionEvidenceRequirementSchema,
)

__all__ = [
    "SchemeGenerationOptionsSchema",
    "SchemeWriterInputSchema",
    "SectionPlanSchema",
    "DocumentPlanSchema",
    "TruncationCheckSchema",
    "SemanticGateIssueSchema",
    "SemanticGateResultSchema",
    "SectionEvalSchema",
    "SchemeSectionSchema",
    "HardGateResultSchema",
    "SchemeDraftSchema",
    "SchemeWriterOutputSchema",
    "RejectedDraftArtifactSchema",
    "DeliverableFinalResultSchema",
    "SectionEvidenceBundleSchema",
    "SectionExecutionRequestSchema",
    "SectionExecutionResultSchema",
    "ParallelSectionStateDeltaSchema",
    "ParallelSectionExecutionEnvelopeSchema",
    "DocumentAssemblyRequestSchema",
    "DocumentAssemblyResultSchema",
    "SectionEvidenceRequirementSchema",
]
