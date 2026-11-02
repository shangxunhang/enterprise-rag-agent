"""Checkpoint-safe contracts for the single-section LangGraph subgraph."""

from __future__ import annotations

from copy import deepcopy
import json
from typing import Any, Mapping, TypedDict

from pydantic import ConfigDict, Field, model_validator

from agent.runtime.graph_state import GraphStateSchema
from apps.enterprise_document.schemas.agent_rag_v1.claims import (
    ClaimCoverageProofRefSchema,
    ClaimSchema,
)
from apps.enterprise_document.schemas.agent_rag_v1.lifecycle import (
    ActiveExecutionLineageSchema,
)
from apps.enterprise_document.schemas.agent_rag_v1.verification import (
    VerificationResultRefSchema,
)
from apps.enterprise_document.schemas.agent_rag_v1.research import (
    EvidencePackageBindingSchema,
    ResearchRoundAuthorizationBindingSchema,
    ResearchRoundBindingSchema,
)
from apps.enterprise_document.schemas.agent_rag_v1.paragraphs import (
    ParagraphBindingSchema,
    ParagraphCandidateSchema,
    SectionParagraphManifestSchema,
)
from apps.enterprise_document.schemas.agent_rag_v1.payloads import (
    ParagraphArtifactSchema,
    ResearchResultPayloadSchema,
    VerifierResultPayloadSchema,
    WriterResultPayloadSchema,
)
from apps.enterprise_document.schemas.agent_rag_v1.writer_pipeline import (
    GeneratedParagraphInputSchema,
    ParagraphPrecheckResultSchema,
)
from apps.enterprise_document.schemas.pre_write_admission import (
    EvidenceRequirementsSnapshotSchema,
    WriterAdmissionDecisionSchema,
    WriterAdmissionFactAssessmentSchema,
)
from apps.enterprise_document.schemas.pre_write_binding import (
    WriterGateBindingSchema,
)
from apps.enterprise_document.schemas.pre_write_control import (
    EnforcementResultSchema,
    PreWriteRouteDecisionSchema,
    RecoveryControlFactsSchema,
    RecoveryRuntimeStateSchema,
)
from apps.enterprise_document.schemas.pre_write_writer import (
    WriterExecutionRefSchema,
    WriterGenerationInvocationRefSchema,
    WriterGenerationInvocationSchema,
    WriterLogicalExecutionKey,
)
from apps.enterprise_document.schemas.project_input_schema import ProjectInputSchema
from apps.enterprise_document.schemas.scheme_writer.planning import (
    SectionPlanBindingSchema,
)
from apps.enterprise_document.schemas.scheme_writer_schema import (
    SchemeSectionSchema,
    SectionEvidenceBundleSchema,
    SectionExecutionResultSchema,
    SectionPlanSchema,
    SectionStateSchema,
)
from apps.enterprise_document.schemas.table_agent_schema import StructuredFactSchema
from apps.enterprise_document.schemas.writer_governance import (
    WriterGateBindingSchema as LegacyWriterGateBindingSchema,
)
from schemas.citation import CitationSchema
from schemas.common import ErrorSchema, SchemaBase, WarningSchema
from schemas.rag import EvidenceBundleSchema, RAGContextSchema, RetrievedChunkSchema


SECTION_EXECUTION_GRAPH_STATE_VERSION = 3


class SectionGraphExecutionRequestSchema(SchemaBase):
    """Production section-subgraph input without live runtime objects."""

    model_config = ConfigDict(extra="forbid")

    schema_version: str = "section_graph_execution_request_v1"
    document_id: str
    project_input: ProjectInputSchema
    section_plan: SectionPlanSchema
    structured_facts: list[StructuredFactSchema] = Field(default_factory=list)
    previous_sections: list[SchemeSectionSchema] = Field(default_factory=list)
    document_evidence_bundle: EvidenceBundleSchema | None = None
    document_tool_call_ids: list[str] = Field(default_factory=list)
    section_retrieval_enabled: bool = True
    corrective_retrieval_enabled: bool = True
    parallel_isolated: bool = False
    budget_policy_override: dict[str, Any] = Field(default_factory=dict)
    canonical_plan_binding: SectionPlanBindingSchema | None = None
    active_execution_lineage: ActiveExecutionLineageSchema | None = None
    research_authorization_binding: ResearchRoundAuthorizationBindingSchema | None = None
    active_evidence_package_binding: EvidencePackageBindingSchema | None = None
    current_research_round_binding: ResearchRoundBindingSchema | None = None


class SectionExecutionGraphPayloadSchema(SchemaBase):
    """Serializable section checkpoint payload.

    Version-3 checkpoint validation is deliberately behavior-neutral. Canonical
    Gate artifacts are persisted exactly as values already produced by their
    owning services; this schema never derives Admission, Binding, Recovery,
    Route, Writer authorization, or replacement canonical identities.

    Services and live registries never enter this payload. ``shared_state_payload``
    remains a JSON projection restored into an isolated ``GraphStateSchema``.
    """

    model_config = ConfigDict(extra="forbid")

    schema_version: str = "section_execution_graph_payload_v1"
    task_id: str
    run_id: str
    document_id: str
    project_input: ProjectInputSchema
    section_plan: SectionPlanSchema
    structured_facts: list[StructuredFactSchema] = Field(default_factory=list)
    previous_sections: list[SchemeSectionSchema] = Field(default_factory=list)

    shared_state_payload: dict[str, Any]
    document_evidence_bundle: EvidenceBundleSchema | None = None
    document_tool_call_ids: list[str] = Field(default_factory=list)
    section_retrieval_enabled: bool = True
    corrective_retrieval_enabled: bool = True
    parallel_isolated: bool = False
    budget_policy_override: dict[str, Any] = Field(default_factory=dict)

    # Canonical M3/M4 refs supplied by the owning Workflow/cutover layer.
    # Checkpoint persistence is projection-only and never creates currentness.
    canonical_plan_binding: SectionPlanBindingSchema | None = None
    active_execution_lineage: ActiveExecutionLineageSchema | None = None
    research_authorization_binding: ResearchRoundAuthorizationBindingSchema | None = None
    active_evidence_package_binding: EvidencePackageBindingSchema | None = None
    current_research_round_binding: ResearchRoundBindingSchema | None = None

    section_id: str | None = None
    section_title: str | None = None
    section_order: int | None = None
    total_sections: int = 0
    citation_required: bool = False
    section_state: SectionStateSchema | None = None

    citation_registry: list[CitationSchema] = Field(default_factory=list)

    # Target canonical checkpoint values. These are passive, already-produced
    # values; restore/model validation must not synthesize or mutate them.
    active_evidence_bundle: EvidenceBundleSchema | None = None
    active_evidence_requirements_snapshot: EvidenceRequirementsSnapshotSchema | None = None
    active_writer_admission_fact_assessment: WriterAdmissionFactAssessmentSchema | None = None
    active_writer_admission_decision: WriterAdmissionDecisionSchema | None = None
    active_writer_gate_binding: WriterGateBindingSchema | None = None
    recovery_runtime_state: RecoveryRuntimeStateSchema | None = None
    current_enforcement_result: EnforcementResultSchema | None = None
    current_recovery_control_facts: RecoveryControlFactsSchema | None = None
    current_pre_write_route_decision: PreWriteRouteDecisionSchema | None = None
    pending_writer_generation_invocation: WriterGenerationInvocationSchema | None = None
    writer_logical_execution_key: WriterLogicalExecutionKey | None = None
    writer_execution_ref: WriterExecutionRefSchema | None = None
    writer_generation_invocation_ref: WriterGenerationInvocationRefSchema | None = None
    # Technical occurrence projection only. Its exact execution-store lifecycle
    # belongs to the execution-control lane; this field carries no Gate authority.
    pending_recovery_dispatch: dict[str, Any] | None = None

    # Existing section execution/context state retained for orchestration and
    # bounded compatibility. None of these fields is Target Gate authority.
    active_context: RAGContextSchema | None = None
    active_chunks: list[RetrievedChunkSchema] = Field(default_factory=list)
    active_citations: list[CitationSchema] = Field(default_factory=list)
    active_query: str = ""
    active_scope: str = "document"
    active_writer_gate: LegacyWriterGateBindingSchema | None = None
    tool_call_ids: list[str] = Field(default_factory=list)
    retrieved_chunks: list[RetrievedChunkSchema] = Field(default_factory=list)
    rag_outputs: list[dict[str, Any]] = Field(default_factory=list)
    retrieval_metadata: dict[str, Any] = Field(default_factory=dict)

    budget_state: dict[str, Any] = Field(default_factory=dict)
    research_agent_payloads: list[ResearchResultPayloadSchema] = Field(default_factory=list)
    writer_agent_payloads: list[WriterResultPayloadSchema] = Field(default_factory=list)
    verifier_agent_payloads: list[VerifierResultPayloadSchema] = Field(default_factory=list)

    # B6 canonical Paragraph refs/snapshot. These are passive projections only;
    # ParagraphRuntime + ArtifactRuntime remain the owners of currentness.
    canonical_paragraph_candidates: list[ParagraphCandidateSchema] = Field(
        default_factory=list
    )
    paragraph_bindings: list[ParagraphBindingSchema] = Field(default_factory=list)
    section_paragraph_manifest: SectionParagraphManifestSchema | None = None
    canonical_claim_coverage_proof_refs: list[ClaimCoverageProofRefSchema] = Field(
        default_factory=list
    )
    canonical_claim_verification_refs: list[VerificationResultRefSchema] = Field(
        default_factory=list
    )

    # Legacy Paragraph/Claim compatibility projections retained until the
    # synchronized Workflow/Claim cutover. They cannot own Paragraph truth.
    paragraph_candidates: list[GeneratedParagraphInputSchema] = Field(default_factory=list)
    paragraph_artifacts: list[ParagraphArtifactSchema] = Field(default_factory=list)
    paragraph_precheck_results: list[ParagraphPrecheckResultSchema] = Field(default_factory=list)
    paragraph_claims: list[ClaimSchema] = Field(default_factory=list)
    paragraph_cursor: int = Field(default=0, ge=0)
    paragraph_blocked_id: str | None = None
    paragraph_pipeline_revision: int = Field(default=0, ge=0)

    # Compatibility/technical counters remain serializable until Graph cutover,
    # but they are not Target Recovery or business-decision truth.
    recovery_count: int = 0
    generation_attempt: int = 1
    generation_check: dict[str, Any] = Field(default_factory=dict)
    self_rag_retrieval_rounds: list[dict[str, Any]] = Field(default_factory=list)
    pending_self_rag_round: dict[str, Any] | None = None
    evidence_blocked: bool = False
    budget_exhaustion_resource: str | None = None
    budget_exhaustion_limit: int | None = None

    section: SchemeSectionSchema | None = None
    evidence: SectionEvidenceBundleSchema | None = None
    result: SectionExecutionResultSchema | None = None
    warnings: list[WarningSchema] = Field(default_factory=list)
    error: ErrorSchema | None = None

    route: str = "prepare"
    current_node: str = "not_started"
    executed_nodes: list[str] = Field(default_factory=list)
    transition_count: int = 0
    completed: bool = False

    @model_validator(mode="after")
    def _validate_paragraph_pipeline(self) -> "SectionExecutionGraphPayloadSchema":
        """Validate structural paragraph persistence without producing behavior."""

        canonical_logical_ids = [
            item.operation_ref.target_logical_ref.logical_artifact_id
            for item in self.canonical_paragraph_candidates
        ]
        if len(canonical_logical_ids) != len(set(canonical_logical_ids)):
            raise ValueError("canonical paragraph candidate logical ids must be unique")
        if self.canonical_paragraph_candidates and self.paragraph_cursor > len(
            self.canonical_paragraph_candidates
        ):
            raise ValueError("paragraph cursor exceeds canonical candidate count")

        if self.section_paragraph_manifest is not None:
            manifest = self.section_paragraph_manifest
            binding_versions = [item.version_ref for item in self.paragraph_bindings]
            if any(
                item.manifest_ref != manifest.manifest_ref
                or item.resolved_manifest_revision != manifest.manifest_revision
                for item in self.paragraph_bindings
            ):
                raise ValueError(
                    "paragraph binding projection disagrees with Manifest lifecycle"
                )
            if tuple(binding_versions) != manifest.members:
                raise ValueError(
                    "paragraph binding projection must match exact Manifest order/members"
                )

        candidate_ids = [str(item.paragraph_id or "").strip() for item in self.paragraph_candidates]
        candidate_orders = [item.order for item in self.paragraph_candidates]
        if any(not item for item in candidate_ids):
            raise ValueError("paragraph candidates require stable ids")
        if len(candidate_ids) != len(set(candidate_ids)):
            raise ValueError("paragraph candidate ids must be unique")
        if len(candidate_orders) != len(set(candidate_orders)):
            raise ValueError("paragraph candidate orders must be unique")
        artifact_ids = [item.paragraph_id for item in self.paragraph_artifacts]
        artifact_orders = [item.order for item in self.paragraph_artifacts]
        if len(artifact_ids) != len(set(artifact_ids)):
            raise ValueError("paragraph artifact ids must be unique")
        if len(artifact_orders) != len(set(artifact_orders)):
            raise ValueError("paragraph artifact orders must be unique")
        if set(artifact_ids) - set(candidate_ids):
            raise ValueError("committed paragraph is not in the active plan")
        if self.paragraph_cursor > len(self.paragraph_candidates):
            raise ValueError("paragraph cursor exceeds candidate count")
        for artifact in self.paragraph_artifacts:
            artifact.verify_content_integrity()
        precheck_ids = [item.paragraph_id for item in self.paragraph_precheck_results]
        if len(precheck_ids) != len(set(precheck_ids)):
            raise ValueError("paragraph precheck result ids must be unique")
        if set(precheck_ids) - set(artifact_ids):
            raise ValueError("paragraph precheck cannot precede artifact commit")
        claim_ids = [item.claim_id for item in self.paragraph_claims]
        if len(claim_ids) != len(set(claim_ids)):
            raise ValueError("paragraph claim ids must be unique")
        if self.paragraph_blocked_id is not None and self.paragraph_blocked_id not in artifact_ids:
            raise ValueError("blocked paragraph must already be committed")
        return self


class SectionExecutionGraphState(TypedDict, total=False):
    section_graph_state_version: int
    section_payload: dict[str, Any]
    current_section_node: str
    section_route: str


class SectionExecutionGraphStateCodec:
    """Serialize exact section v3 state without reconstructing business truth."""

    @staticmethod
    def dump_payload(payload: SectionExecutionGraphPayloadSchema) -> dict[str, Any]:
        normalized = SectionExecutionGraphPayloadSchema.model_validate(
            payload.model_dump(mode="python", round_trip=True)
        )
        value = normalized.model_dump(mode="json", round_trip=True)
        return json.loads(json.dumps(value, ensure_ascii=False))

    @staticmethod
    def dump_graph_state(state: GraphStateSchema) -> dict[str, Any]:
        value = state.model_dump(mode="json", round_trip=True)
        return json.loads(json.dumps(value, ensure_ascii=False))

    @staticmethod
    def restore_graph_state(payload: SectionExecutionGraphPayloadSchema) -> GraphStateSchema:
        state = GraphStateSchema.model_validate(deepcopy(payload.shared_state_payload))
        if state.task_id != payload.task_id:
            raise ValueError(
                "section graph task_id identity mismatch: "
                f"expected={payload.task_id!r} actual={state.task_id!r}"
            )
        if state.run_id != payload.run_id:
            raise ValueError(
                "section graph run_id identity mismatch: "
                f"expected={payload.run_id!r} actual={state.run_id!r}"
            )
        return state

    @staticmethod
    def restore_payload(runtime_state: Mapping[str, Any]) -> SectionExecutionGraphPayloadSchema:
        actual = runtime_state.get("section_graph_state_version")
        if type(actual) is not int or actual != SECTION_EXECUTION_GRAPH_STATE_VERSION:
            raise ValueError(
                "unsupported section graph state version: "
                f"expected={SECTION_EXECUTION_GRAPH_STATE_VERSION} actual={actual!r}"
            )
        raw = runtime_state.get("section_payload")
        if not isinstance(raw, Mapping):
            raise ValueError("section graph state requires section_payload")
        return SectionExecutionGraphPayloadSchema.model_validate(deepcopy(dict(raw)))

    def build_initial_state(self, payload: SectionExecutionGraphPayloadSchema) -> SectionExecutionGraphState:
        return {
            "section_graph_state_version": SECTION_EXECUTION_GRAPH_STATE_VERSION,
            "section_payload": self.dump_payload(payload),
            "current_section_node": "not_started",
            "section_route": "prepare",
        }

    @staticmethod
    def assert_json_serializable(state: Mapping[str, Any]) -> None:
        json.dumps(state, ensure_ascii=False)
