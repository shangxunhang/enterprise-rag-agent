# =============================================================================
# 中文阅读说明：企业文档生成业务模块，负责方案规划、检索、章节生成、引用和验收。
# 主要定义：SchemeWriterOutputSchema。建议先从公开入口函数开始，再沿调用关系向下阅读。
# =============================================================================
"""Top-level scheme writer output contract."""
from __future__ import annotations

from typing import Any, Dict, List, Optional

from pydantic import ConfigDict, Field, model_validator

from apps.enterprise_document.schemas.scheme_writer.document import SchemeDraftSchema
from apps.enterprise_document.schemas.scheme_writer.evaluation import HardGateResultSchema
from apps.enterprise_document.schemas.scheme_writer.planning import DocumentPlanSchema
from apps.enterprise_document.schemas.scheme_writer.evidence import SectionEvidenceBundleSchema
from schemas.citation import CitationSchema
from schemas.common import ErrorSchema, SchemaBase, WarningSchema
from schemas.rag import RAGContextSchema, RetrievedChunkSchema
from schemas.status import ExecutionStatus


class RejectedDraftArtifactSchema(SchemaBase):
    """A generated draft retained only for diagnostics/review after rejection."""

    model_config = ConfigDict(extra="forbid")

    artifact_type: str = "rejected_draft"
    draft: SchemeDraftSchema
    rejection_code: str
    rejection_reasons: List[str] = Field(default_factory=list)
    hard_gate: HardGateResultSchema


class DeliverableFinalResultSchema(SchemaBase):
    """The only business deliverable exposed after the document hard gate."""

    model_config = ConfigDict(extra="forbid")

    artifact_type: str = "deliverable_final_result"
    draft: SchemeDraftSchema
    output_format: str = "markdown"


# 阅读注释（类）：封装 scheme writer 输出 Schema，定义跨模块传递的数据结构与字段约束。
class SchemeWriterOutputSchema(SchemaBase):
    """封装 scheme writer 输出 Schema，定义跨模块传递的数据结构与字段约束。"""
    model_config = ConfigDict(extra="forbid")

    schema_version: str = "scheme_writer_output_v2"
    task_id: str
    run_id: str
    status: ExecutionStatus
    document_plan: Optional[DocumentPlanSchema] = None
    generated_draft: Optional[SchemeDraftSchema] = None
    rejected_artifact: Optional[RejectedDraftArtifactSchema] = None
    deliverable_final_result: Optional[DeliverableFinalResultSchema] = None
    rag_context: Optional[RAGContextSchema] = None
    retrieved_chunks: List[RetrievedChunkSchema] = Field(default_factory=list)
    citations: List[CitationSchema] = Field(default_factory=list)
    section_evidence: List[SectionEvidenceBundleSchema] = Field(default_factory=list)
    hard_gate: Optional[HardGateResultSchema] = None
    warnings: List[WarningSchema] = Field(default_factory=list)
    error: Optional[ErrorSchema] = None
    need_human_review: bool = True
    extra: Dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def validate_artifact_lifecycle(self) -> "SchemeWriterOutputSchema":
        if self.rejected_artifact is not None and self.deliverable_final_result is not None:
            raise ValueError("rejected artifact and deliverable are mutually exclusive")
        if self.rejected_artifact is not None:
            if self.generated_draft is None:
                raise ValueError("rejected artifact requires generated_draft")
            if self.rejected_artifact.draft.draft_id != self.generated_draft.draft_id:
                raise ValueError("rejected artifact must reference generated_draft")
        if self.deliverable_final_result is not None:
            if self.generated_draft is None:
                raise ValueError("deliverable requires generated_draft")
            if (
                self.deliverable_final_result.draft.draft_id
                != self.generated_draft.draft_id
            ):
                raise ValueError("deliverable must reference generated_draft")

        successful = self.status in {
            ExecutionStatus.SUCCESS,
            ExecutionStatus.PARTIAL_SUCCESS,
        }
        if successful and self.deliverable_final_result is None:
            raise ValueError("successful scheme output requires a deliverable")
        if not successful and self.deliverable_final_result is not None:
            raise ValueError("failed scheme output cannot expose a deliverable")
        if (
            self.generated_draft is not None
            and not successful
            and self.rejected_artifact is None
        ):
            raise ValueError("failed generated draft requires rejected_artifact")
        return self
