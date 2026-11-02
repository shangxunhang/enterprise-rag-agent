"""Checkpoint-safe state contracts for the enterprise document business graph."""

from __future__ import annotations

from typing import Literal
from pydantic import BaseModel, model_validator
from core.hashing import canonical_sha256
from apps.enterprise_document.schemas.artifact_reference import ArtifactReferenceSchema
from apps.enterprise_document.schemas.agent_rag_v1.lifecycle import RunLifecycleRecordSchema, AttemptLifecycleRecordSchema
from apps.enterprise_document.schemas.agent_rag_v1.claim_support import (
    ClaimSupportAuthorityKind, ClaimSupportSubjectRefSchema, EvidenceTargetSetRefSchema,
)

from application.terminal_outcome_schema import TerminalOutcomeResultV1

from copy import deepcopy
from dataclasses import dataclass
import json
from typing import Any, Mapping

from pydantic import ConfigDict, Field

from agent.runtime.langgraph_state import LangGraphRuntimeState
from apps.enterprise_document.schemas.agent_rag_v1.lifecycle import (
    RunCompletionAssessmentRefSchema,
)
from apps.enterprise_document.schemas.agent_rag_v1.document_closure import (
    AssemblyValidationResultSchema,
    ConflictRepairPlanSchema,
    DocumentVerificationResultSchema,
    FinalArtifactReviewResultSchema,
    GenerationReportSchema,
)
from apps.enterprise_document.schemas.agent_rag_v1.workflow import VersionDecisionSchema
from apps.enterprise_document.schemas.post_write.foundation import (
    ArtifactStateRefSchema,
    PostWriteControlDecisionRefSchema,
)
from apps.enterprise_document.schemas.post_write.release import (
    PostWritePublishedReleaseSchema,
)
from apps.enterprise_document.schemas.project_input_schema import ProjectInputSchema
from apps.enterprise_document.schemas.release_publication import PublishedReleaseSchema
from apps.enterprise_document.schemas.scheme_writer.section_execution import (
    SectionExecutionResultSchema,
)
from apps.enterprise_document.schemas.scheme_writer_schema import (
    DeliverableFinalResultSchema,
    DocumentAssemblyResultSchema,
    DocumentPlanSchema,
    DocumentValidationStateSchema,
    HardGateResultSchema,
    PlanStateSchema,
    RejectedDraftArtifactSchema,
    SchemeDraftSchema,
    SchemeGenerationOptionsSchema,
    SchemeSectionSchema,
    SchemeWriterInputSchema,
    SchemeWriterOutputSchema,
    SectionEvidenceBundleSchema,
    SectionStateSchema,
)
from apps.enterprise_document.schemas.table_agent_schema import (
    StructuredFactSchema,
    TableAnalysisSchema,
)
from schemas.agent import AgentResultSchema
from schemas.citation import CitationSchema
from schemas.common import ErrorSchema, SchemaBase, WarningSchema
from schemas.human_review import (
    HumanReviewDecisionSchema,
    HumanReviewRequestSchema,
    HumanReviewStatus,
)
from schemas.rag import EvidenceBundleSchema
from schemas.status import ExecutionStatus


SCHEME_GENERATION_GRAPH_STATE_VERSION = 3


@dataclass(frozen=True)
class BusinessNodeContract:
    """Explicit runtime authority for one top-level business node."""

    node_name: str
    timeout_seconds: float
    max_retries: int
    declared_read_paths: tuple[str, ...]
    declared_graph_write_paths: tuple[str, ...]
    declared_payload_write_paths: tuple[str, ...]
    commit_policy: str = "success_only"
    failure_graph_write_paths: tuple[str, ...] = ()
    failure_payload_write_paths: tuple[str, ...] = ()
    failure_commit_error_codes: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if not self.node_name.strip():
            raise ValueError("business node contract requires node_name")
        if self.timeout_seconds <= 0:
            raise ValueError("business node timeout_seconds must be positive")
        if self.max_retries < 0:
            raise ValueError("business node max_retries cannot be negative")
        if self.commit_policy not in {
            "success_only",
            "always",
            "allow_partial_on_failure",
        }:
            raise ValueError(
                f"unsupported business node commit_policy={self.commit_policy!r}"
            )


class BudgetDocumentContinuationSchema(BaseModel):
    """References only; Citation/Budget/Lifecycle independently reread native truth."""
    model_config = ConfigDict(extra="forbid", frozen=True)
    schema_version: Literal["document_budget_continuation_v1"] = "document_budget_continuation_v1"
    predecessor_result_ref: str = Field(min_length=1)
    predecessor_result_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    accepted_grant_ref: str = Field(min_length=1)
    accepted_grant_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    resume_event_ref: str = Field(min_length=1)
    resume_event_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")



class BudgetDocumentReceiptIdentitySchema(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    task_id: str = Field(min_length=1)
    run_id: str = Field(min_length=1)
    section_id: str = Field(min_length=1)


class BudgetDocumentPendingSupportSchema(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    subject: ClaimSupportSubjectRefSchema
    target: EvidenceTargetSetRefSchema
    contract: Literal["claim-evidence-support-v1"]

    @model_validator(mode="after")
    def _exact_support(self):
        if (self.subject.authority_kind is not ClaimSupportAuthorityKind.SIF07_CLAIM
                or self.target.claim_support_subject_ref != self.subject
                or self.target.target_set_contract_version != "claim-authorized-evidence-v1"):
            raise ValueError("DOCUMENT budget cursor is not exact native SIF07 Support")
        return self


class BudgetDocumentRefusalSchema(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    schema_version: Literal["citation_budget_refusal_v1"] = "citation_budget_refusal_v1"
    operation_kind: Literal["CITATION_ASSOCIATION", "DOCUMENT_SUPPORT"] = "CITATION_ASSOCIATION"
    task_id: str = Field(min_length=1)
    run_id: str = Field(min_length=1)
    section_id: str = Field(min_length=1)
    input_ref: str = Field(min_length=1)
    input_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    native_denial_ref: str = Field(min_length=1)
    native_denial_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    phase_proof_ref: str = Field(min_length=1)
    phase_proof_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    semantic_input_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    semantic_operation_ref: str = Field(min_length=1)
    artifact_state_ref: ArtifactStateRefSchema
    artifact_version: int = Field(ge=1)
    document_input_ref: str | None = Field(default=None, min_length=1)
    document_input_sha256: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    document_result_ref: str | None = Field(default=None, min_length=1)
    document_result_sha256: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    document_identity: BudgetDocumentReceiptIdentitySchema | None = None
    association_budget_continuation: BudgetDocumentContinuationSchema | None = None
    pending_support: BudgetDocumentPendingSupportSchema | None = None

    @model_validator(mode="after")
    def _pair(self):
        if (not self.input_ref.endswith("/input")
                or self.phase_proof_ref != self.native_denial_ref
                or self.artifact_version != self.artifact_state_ref.artifact_version
                or self.artifact_state_ref.artifact_scope.value != "DOCUMENT"):
            raise ValueError("Budget DOCUMENT refusal exact native pair/version differs")
        suffix = "/result" if self.operation_kind == "CITATION_ASSOCIATION" else "/candidate"
        if self.input_ref.removesuffix("/input") + suffix != self.native_denial_ref:
            raise ValueError("Budget DOCUMENT refusal kind/native result suffix differs")
        if self.operation_kind == "CITATION_ASSOCIATION":
            if (self.semantic_operation_ref != "citation-association-call://sha256/" + self.semantic_input_sha256
                    or any(value is not None for value in (
                        self.document_input_ref, self.document_input_sha256,
                        self.document_result_ref, self.document_result_sha256,
                        self.document_identity, self.association_budget_continuation, self.pending_support))):
                raise ValueError("Citation budget refusal carries another owner cursor")
        else:
            if (self.document_input_ref is None or self.document_input_sha256 is None
                    or self.document_result_ref is None or self.document_result_sha256 is None
                    or self.document_identity is None or self.pending_support is None
                    or not self.document_input_ref.endswith("/input")
                    or self.document_input_ref.removesuffix("/input") + "/result" != self.document_result_ref
                    or (self.document_identity.task_id, self.document_identity.run_id) != (self.task_id, self.run_id)):
                raise ValueError("DOCUMENT Support budget refusal lacks exact completed association binding")
            support_key = canonical_sha256({
                "subject": self.pending_support.subject.model_dump(mode="json"),
                "target": self.pending_support.target.model_dump(mode="json"),
                "contract": self.pending_support.contract,
            })
            if self.semantic_operation_ref != "claim-support-evaluation://sha256/" + support_key:
                raise ValueError("DOCUMENT Support budget cursor targets another semantic operation")
        return self


class BudgetDocumentCursorSchema(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    schema_version: Literal["budget_document_cursor_v1"] = "budget_document_cursor_v1"
    task_id: str = Field(min_length=1)
    run_id: str = Field(min_length=1)
    document_id: str = Field(min_length=1)
    canonical_request_ref: str = Field(min_length=1)
    canonical_request_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    project_input_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    input_snapshot_version: str = Field(min_length=1)
    workflow_id: str = Field(min_length=1)
    workflow_version: str = Field(min_length=1)
    workflow_namespace: str = Field(min_length=1)
    checkpoint_config: dict[str, Any]
    checkpoint_state_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    pending_node: Literal["evaluate_document_gate"] = "evaluate_document_gate"
    predecessor_business_node: Literal["final_artifact_review"] = "final_artifact_review"
    predecessor_effective_route: Literal["continue"] = "continue"
    predecessor_transition_count: int = Field(ge=1)
    predecessor_committed_node_refs: tuple[dict[str, Any], ...]
    source_sections: tuple[SectionExecutionResultSchema, ...]
    budget_materialization_ref: ArtifactReferenceSchema
    source_epoch: dict[str, str]
    source_epoch_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")

    @model_validator(mode="after")
    def _bindings(self):
        if (self.canonical_request_ref != "run-identity:" + self.run_id
                or self.budget_materialization_ref.task_id != self.task_id
                or self.budget_materialization_ref.run_id != self.run_id
                or not self.source_epoch
                or any(len(value) != 64 or any(ch not in "0123456789abcdef" for ch in value)
                       for value in self.source_epoch.values())
                or canonical_sha256(self.source_epoch) != self.source_epoch_sha256
                or len(self.predecessor_committed_node_refs) != self.predecessor_transition_count):
            raise ValueError("Budget DOCUMENT cursor native identity/source epoch differs")
        return self


class BudgetDocumentWaitPacketSchema(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    schema_version: Literal["document_budget_wait_packet_v1"] = "document_budget_wait_packet_v1"
    authorization_decision: Literal["PENDING_AUTHORIZATION"] = "PENDING_AUTHORIZATION"
    pause_purpose: Literal["BUDGET_AUTHORIZATION"] = "BUDGET_AUTHORIZATION"
    refusal: BudgetDocumentRefusalSchema
    cursor: BudgetDocumentCursorSchema
    run_preimage: RunLifecycleRecordSchema
    current_children: tuple[AttemptLifecycleRecordSchema, ...]
    child_preimage_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    runner_closed: Literal[True] = True
    runner_closure_ref: str = Field(min_length=1)
    runner_closure_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")

    @model_validator(mode="after")
    def _preimage(self):
        children = tuple(sorted(self.current_children, key=lambda row: (
            row.identity.attempt_scope_ref, row.identity.attempt_ordinal, row.identity.attempt_id)))
        if (children != self.current_children
                or len({row.identity.attempt_scope_ref for row in children}) != len(children)
                or canonical_sha256([row.model_dump(mode="json", round_trip=True) for row in children]) != self.child_preimage_sha256):
            raise ValueError("Budget wait complete native Child preimage differs")
        if (self.refusal.task_id, self.refusal.run_id) != (self.cursor.task_id, self.cursor.run_id):
            raise ValueError("Budget wait refusal and cursor Run differ")
        if (self.run_preimage.identity.task_id, self.run_preimage.identity.run_id) != (self.cursor.task_id, self.cursor.run_id):
            raise ValueError("Budget wait native Run differs")
        if (self.run_preimage.status.value != "running" or self.run_preimage.finished_at is not None
                or self.run_preimage.identity.canonical_request_sha256 != self.cursor.canonical_request_sha256):
            raise ValueError("Budget wait native request/Run preimage differs")
        if any(row.identity.run_id != self.cursor.run_id for row in children):
            raise ValueError("Budget wait Child belongs to another Run")
        return self




class SchemeGenerationGraphPayloadSchema(SchemaBase):
    """Strict top-level checkpoint payload.

    Version-3 validation is passive. Document/section state already produced by
    owning workflow components is persisted as-is; validation/restore never
    computes accept/reject/partial/rewrite/research-more, Admission, Route, or
    Writer authorization.
    """

    model_config = ConfigDict(extra="forbid")

    schema_version: str = "scheme_generation_graph_payload_v1"
    task_id: str
    run_id: str
    workflow_namespace: str = "scheme_generation:unversioned"
    started_at: str | None = None

    project_input: ProjectInputSchema | None = None
    table_analysis: TableAnalysisSchema | None = None
    structured_facts: list[StructuredFactSchema] = Field(default_factory=list)

    required_sections: list[str] = Field(default_factory=list)
    required_evidence_sections: list[str] = Field(default_factory=list)
    generation_options: SchemeGenerationOptionsSchema | None = None
    document_id: str | None = None
    document_plan: DocumentPlanSchema | None = None
    plan_state: PlanStateSchema = Field(default_factory=PlanStateSchema)
    # Exact section identities/state supplied by section/outer workflow owners.
    # The checkpoint never derives these values from ``sections``.
    section_states: dict[str, SectionStateSchema] = Field(default_factory=dict)
    scheme_input: SchemeWriterInputSchema | None = None

    # Sole document-lifecycle canonical Evidence carrier.
    document_evidence_bundle: EvidenceBundleSchema | None = None
    document_tool_call_ids: list[str] = Field(default_factory=list)

    section_retrieval_enabled: bool = False
    corrective_retrieval_enabled: bool = False
    sections: list[SchemeSectionSchema] = Field(default_factory=list)
    section_evidence: list[SectionEvidenceBundleSchema] = Field(default_factory=list)
    # Passive exact-owner projection only.  Durable truth remains in the
    # Plan/Lifecycle/Research/Writer/Paragraph/Claim owners referenced by each
    # result; checkpoint restore must never reconstruct those refs from sections.
    section_execution_results: list[SectionExecutionResultSchema] = Field(
        default_factory=list
    )
    all_citations: list[CitationSchema] = Field(default_factory=list)
    all_rag_outputs: list[dict[str, Any]] = Field(default_factory=list)

    document_verification: DocumentVerificationResultSchema | None = None
    conflict_repair_plan: ConflictRepairPlanSchema | None = None
    conflict_repair_history: list[dict[str, Any]] = Field(default_factory=list)
    incremental_document_verification: DocumentVerificationResultSchema | None = None
    assembly: DocumentAssemblyResultSchema | None = None
    assembly_validation: AssemblyValidationResultSchema | None = None
    final_artifact_review: FinalArtifactReviewResultSchema | None = None
    version_decision: VersionDecisionSchema | None = None
    document_version: str | None = None
    generation_report: GenerationReportSchema | None = None
    hard_gate: HardGateResultSchema | None = None
    # Persist only state supplied by its downstream owner; do not infer it from
    # hard_gate/eval/technical execution status during checkpoint validation.
    document_validation: DocumentValidationStateSchema = Field(
        default_factory=DocumentValidationStateSchema
    )
    output_status: ExecutionStatus | None = None
    output_error: ErrorSchema | None = None
    output_warnings: list[WarningSchema] = Field(default_factory=list)
    artifact_route: str = "pending"
    pause_purpose: Literal["BUDGET_AUTHORIZATION"] | None = None
    budget_wait_ref: str | None = None
    budget_wait_sha256: str | None = None
    budget_wait_event_ref: str | None = None
    budget_wait_event_sha256: str | None = None
    budget_materialization_ref: ArtifactReferenceSchema | None = None
    budget_document_continuation: BudgetDocumentContinuationSchema | None = None

    scheme_writer_output: SchemeWriterOutputSchema | None = None
    generated_draft: SchemeDraftSchema | None = None
    rejected_artifact: RejectedDraftArtifactSchema | None = None
    deliverable_final_result: DeliverableFinalResultSchema | None = None
    release_publication: PublishedReleaseSchema | None = None

    # Wave-13 passive Target projections. Durable authority remains in the
    # Post-write / Lifecycle / Release owners referenced by these exact refs.
    target_document_artifact_ref: ArtifactStateRefSchema | None = None
    target_post_write_control_decision_ref: (
        PostWriteControlDecisionRefSchema | None
    ) = None
    target_run_completion_assessment_ref: (
        RunCompletionAssessmentRefSchema | None
    ) = None
    target_release_publication: PostWritePublishedReleaseSchema | None = None

    terminal_outcome: TerminalOutcomeResultV1 | None = None
    terminal_agent_result: AgentResultSchema | None = None
    terminal_error: ErrorSchema | None = None
    failed_business_node: str | None = None
    executed_nodes: list[str] = Field(default_factory=list)
    business_revision: int = 0
    capture_completed: bool = False

    review_required: bool = False
    review_decision: str | None = None
    review_feedback: str | None = None
    review_requested_at: str | None = None
    review_resumed_at: str | None = None
    resume_count: int = 0
    human_review_required: bool = False
    human_review_recommended: bool = False
    human_review_status: HumanReviewStatus = HumanReviewStatus.NOT_REQUIRED
    human_review_request: HumanReviewRequestSchema | None = None
    human_review_decision: HumanReviewDecisionSchema | None = None


class SchemeGenerationGraphState(LangGraphRuntimeState, total=False):
    """LangGraph-owned envelope for the explicit enterprise document graph."""

    scheme_generation_state_version: int
    scheme_generation_payload: dict[str, Any]
    current_business_node: str
    business_route: str
    human_review_response: Any


class SchemeGenerationGraphStateCodec:
    """Serialize exact top-level v3 state without business reconstruction."""

    @staticmethod
    def dump_payload(payload: SchemeGenerationGraphPayloadSchema) -> dict[str, Any]:
        normalized = SchemeGenerationGraphPayloadSchema.model_validate(
            payload.model_dump(mode="python", round_trip=True)
        )
        serialized = normalized.model_dump(mode="json", round_trip=True)
        return json.loads(json.dumps(serialized, ensure_ascii=False))

    @staticmethod
    def restore_payload(runtime_state: Mapping[str, Any]) -> SchemeGenerationGraphPayloadSchema:
        actual_version = runtime_state.get("scheme_generation_state_version")
        if (
            type(actual_version) is not int
            or actual_version != SCHEME_GENERATION_GRAPH_STATE_VERSION
        ):
            raise ValueError(
                "unsupported scheme generation graph state version: "
                f"expected={SCHEME_GENERATION_GRAPH_STATE_VERSION} "
                f"actual={actual_version!r}"
            )
        raw_payload = runtime_state.get("scheme_generation_payload")
        if not isinstance(raw_payload, Mapping):
            raise ValueError(
                "scheme generation graph state requires scheme_generation_payload"
            )
        payload = SchemeGenerationGraphPayloadSchema.model_validate(
            deepcopy(dict(raw_payload))
        )
        runtime_task_id = runtime_state.get("task_id")
        runtime_run_id = runtime_state.get("run_id")
        if runtime_task_id != payload.task_id:
            raise ValueError(
                "scheme generation task_id identity mismatch: "
                f"expected={payload.task_id!r} actual={runtime_task_id!r}"
            )
        if runtime_run_id != payload.run_id:
            raise ValueError(
                "scheme generation run_id identity mismatch: "
                f"expected={payload.run_id!r} actual={runtime_run_id!r}"
            )
        return payload

    def build_initial_update(self, payload: SchemeGenerationGraphPayloadSchema) -> SchemeGenerationGraphState:
        return {
            "scheme_generation_state_version": SCHEME_GENERATION_GRAPH_STATE_VERSION,
            "scheme_generation_payload": self.dump_payload(payload),
            "current_business_node": "not_started",
            "business_route": "continue",
        }

    @staticmethod
    def assert_json_serializable(runtime_state: Mapping[str, Any]) -> None:
        json.dumps(runtime_state, ensure_ascii=False)
