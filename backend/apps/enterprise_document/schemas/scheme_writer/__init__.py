# =============================================================================
# 中文阅读说明：企业文档生成业务模块，负责方案规划、检索、章节生成、引用和验收。
# 主要定义：以常量、Schema 导入或注册配置为主。建议先从公开入口函数开始，再沿调用关系向下阅读。
# =============================================================================
"""Focused scheme-writer schema modules."""
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
from .output import SchemeWriterOutputSchema
from .evidence import SectionEvidenceBundleSchema
from .section_execution import SectionExecutionRequestSchema, SectionExecutionResultSchema
from .document_assembly import DocumentAssemblyRequestSchema, DocumentAssemblyResultSchema

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
    "SectionEvidenceBundleSchema",
    "SectionExecutionRequestSchema",
    "SectionExecutionResultSchema",
    "DocumentAssemblyRequestSchema",
    "DocumentAssemblyResultSchema",
]
