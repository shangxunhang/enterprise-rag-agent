"""SIF-11 source/assessment projections. Canonical truth stays with source owners."""
from __future__ import annotations

from typing import Literal
from pydantic import BaseModel, ConfigDict, Field, model_validator
from core.hashing import canonical_sha256
from apps.enterprise_document.schemas.agent_rag_v1.claims import ClaimCoverageProofRefSchema
from apps.enterprise_document.schemas.agent_rag_v1.paragraphs import SectionParagraphManifestSchema
from apps.enterprise_document.schemas.agent_rag_v1.verification import VerificationResultRefSchema
from apps.enterprise_document.schemas.pre_write_writer import WriterExecutionRefSchema, WriterGenerationInvocationRefSchema
from apps.enterprise_document.schemas.scheme_writer.planning import SectionPlanBindingSchema


class SectionSourceBindingSchema(BaseModel):
    """Immutable replay evidence; ACCEPTED must be re-derived on every current read."""
    model_config = ConfigDict(extra="forbid", frozen=True)
    schema_version: Literal["section_source_binding_v1"] = "section_source_binding_v1"
    source_ref: str = Field(min_length=1)
    source_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    plan_binding: SectionPlanBindingSchema
    producer_attempt_id: str = Field(min_length=1)
    producer_execution_status: Literal["success", "partial_success", "failed", "retryable_failed", "cancelled", "skipped"]
    input_snapshot_version: str = Field(min_length=1)
    paragraph_manifest: SectionParagraphManifestSchema
    writer_execution_ref: WriterExecutionRefSchema
    writer_invocation_ref: WriterGenerationInvocationRefSchema
    coverage_proof_refs: tuple[ClaimCoverageProofRefSchema, ...]
    verification_refs: tuple[VerificationResultRefSchema, ...]
    workflow_status: Literal["ACCEPTED", "VERIFYING"]
    acceptance_policy: Literal["sif11-complete-current-claim-scope-v1"] = "sif11-complete-current-claim-scope-v1"

    def semantic_payload(self):
        return self.model_dump(mode="json", exclude={"source_ref", "source_sha256"})

    @model_validator(mode="after")
    def _identity(self):
        manifest = self.paragraph_manifest.manifest_ref
        plan = self.plan_binding
        key = self.writer_execution_ref.logical_execution_key
        if (manifest.run_id != plan.plan_version_ref.logical_ref.run_id
                or manifest.section_id != plan.section_id
                or manifest.attempt_id != self.producer_attempt_id
                or manifest.write_instance_id != self.producer_attempt_id
                or key.writer_subject_ref.run_id != manifest.run_id
                or key.writer_subject_ref.section_id != manifest.section_id
                or key.write_instance_id != self.producer_attempt_id
                or self.writer_invocation_ref.logical_execution_key != key):
            raise ValueError("Section source Run/Section/Attempt/Writer identity mismatch")
        if canonical_sha256(self.semantic_payload()) != self.source_sha256:
            raise ValueError("Section source fingerprint mismatch")
        return self


class SectionDependencyEdgeSchema(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    upstream_section_id: str = Field(min_length=1)
    result: Literal["SATISFIED", "WAITING", "UNSATISFIABLE", "INTEGRITY_INVALID"]
    reason: str = Field(min_length=1)
    source_binding: SectionSourceBindingSchema | None = None


class SectionDependencyAssessmentSchema(BaseModel):
    """Derived current observation, never a mutable dependency head/store."""
    model_config = ConfigDict(extra="forbid", frozen=True)
    schema_version: Literal["section_dependency_assessment_v1"] = "section_dependency_assessment_v1"
    consumer_plan_binding: SectionPlanBindingSchema
    edges: tuple[SectionDependencyEdgeSchema, ...]
    aggregate: Literal["ALL_SATISFIED", "WAITING", "UNSATISFIABLE", "INTEGRITY_INVALID"]
    policy: Literal["sif11-accepted-only-dependency-v1"] = "sif11-accepted-only-dependency-v1"

    @property
    def fingerprint(self):
        return canonical_sha256(self.model_dump(mode="json"))

    @model_validator(mode="after")
    def _aggregate(self):
        ids = [edge.upstream_section_id for edge in self.edges]
        if len(ids) != len(set(ids)):
            raise ValueError("duplicate dependency edge")
        results = {edge.result for edge in self.edges}
        expected = next((value for value in ("INTEGRITY_INVALID", "UNSATISFIABLE", "WAITING")
                         if value in results), "ALL_SATISFIED")
        if self.aggregate != expected:
            raise ValueError("dependency aggregate is not exact")
        for edge in self.edges:
            if edge.result == "SATISFIED" and (edge.source_binding is None
                    or edge.source_binding.workflow_status != "ACCEPTED"
                    or edge.source_binding.plan_binding.section_id != edge.upstream_section_id):
                raise ValueError("satisfied dependency lacks accepted exact source")
        return self
