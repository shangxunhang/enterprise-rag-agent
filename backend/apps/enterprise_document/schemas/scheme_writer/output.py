"""方案生成用例的顶层输出及草稿生命周期约束。"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

from pydantic import ConfigDict, Field, model_validator

from apps.enterprise_document.schemas.release_publication import PublishedReleaseSchema
from apps.enterprise_document.schemas.scheme_writer.document import SchemeDraftSchema
from apps.enterprise_document.schemas.scheme_writer.evaluation import HardGateResultSchema
from apps.enterprise_document.schemas.scheme_writer.planning import DocumentPlanSchema
from apps.enterprise_document.schemas.scheme_writer.evidence import SectionEvidenceBundleSchema
from schemas.citation import CitationSchema
from schemas.common import ErrorSchema, SchemaBase, WarningSchema
from schemas.rag import RAGContextSchema, RetrievedChunkSchema
from schemas.status import ExecutionStatus


class RejectedDraftArtifactSchema(SchemaBase):
    """硬门禁拒绝后仅供诊断或人工复核的草稿包装。"""

    model_config = ConfigDict(extra="forbid")

    artifact_type: str = "rejected_draft"
    draft: SchemeDraftSchema
    rejection_code: str
    rejection_reasons: List[str] = Field(default_factory=list)
    hard_gate: HardGateResultSchema


class DeliverableFinalResultSchema(SchemaBase):
    """通过文档硬门禁后唯一允许作为业务结果暴露的交付物。"""

    model_config = ConfigDict(extra="forbid")

    artifact_type: str = "deliverable_final_result"
    draft: SchemeDraftSchema
    output_format: str = "markdown"
    release_publication: Optional[PublishedReleaseSchema] = None


class SchemeWriterOutputSchema(SchemaBase):
    """汇总规划、草稿、证据、门禁和最终产物，并强制状态与产物一致。"""
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
        """禁止把被拒草稿与可交付结果同时暴露，并校验状态和草稿引用关系。"""
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
            publication = self.deliverable_final_result.release_publication
            if publication is not None:
                manifest = publication.manifest
                if manifest.task_id != self.task_id or manifest.run_id != self.run_id:
                    raise ValueError("release publication task/run identity mismatch")
                if manifest.document_id != self.generated_draft.document_id:
                    raise ValueError("release publication document identity mismatch")
                if manifest.draft_id != self.generated_draft.draft_id:
                    raise ValueError("release publication draft identity mismatch")
                if self.need_human_review:
                    raise ValueError("published release cannot require human review")

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
