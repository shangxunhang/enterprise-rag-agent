"""Reusable business stages for scheme generation.

The explicit LangGraph production path and the legacy use-case facade both
delegate to this service. There is one implementation of planning, retrieval,
section execution, assembly, hard-gate evaluation, artifact publication and
terminal result construction.
"""

from __future__ import annotations

from apps.enterprise_document.schemas.scheme_generation_graph import BudgetDocumentRefusalSchema
from apps.enterprise_document.schemas.artifact_reference import ArtifactReferenceSchema
from apps.enterprise_document.services.agent_rag_v1.post_write_citation_grounding import CitationGroundingError
from apps.enterprise_document.services.agent_rag_v1.budget_manager import CanonicalBudgetIntegrityError
from apps.enterprise_document.services.agent_rag_v1.artifact_store import ArtifactIntegrityError

from core.runtime.execution_control import checkpoint_current_execution
from core.diagnostics import current_diagnostics, capture_event

from application.terminal_outcome_projector import diagnostic, project_terminal, project_later_failure, safe_identifier, seal_terminal
from application.terminal_status_view import project_terminal_status
from apps.enterprise_document.services.agent_rag_v1.terminal_outcome_transport import (
    TargetPostWriteInterrupted, TerminalOutcomeStageFailure, declared_error, project_post_write,
)

from dataclasses import dataclass
from typing import Any, NoReturn, TYPE_CHECKING

from core.hashing import canonical_sha256
from agent.runtime.graph_state_ops import (
    GraphStateDiffer,
    StateWriteContractViolation,
)
from agent.runtime.shared_state_schema import SharedStateSchema
from agent.runtime.state_access import SharedStateWriter
from agent.runtime.workflow_stream_context import emit_workflow_custom_event
from apps.enterprise_document.quality.budget import (
    WorkflowBudget,
    activate_workflow_budget,
)
from apps.enterprise_document.schemas.agent_rag_v1 import (
    ChangeSetSchema,
    DocumentVerificationResultSchema,
    RepairTaskStatus,
)
from apps.enterprise_document.schemas.scheme_generation_graph import (
    SchemeGenerationGraphPayloadSchema,
)
from apps.enterprise_document.schemas.agent_rag_v1.lifecycle import (
    RunLifecycleRecordSchema, RunLifecycleStatus,
)
from apps.enterprise_document.services.agent_rag_v1.lifecycle_runtime import (
    LifecycleCompositeTransition, LifecycleRevisionConflict, LifecycleIntegrityError,
)
from apps.enterprise_document.schemas.section_execution_graph import (
    SectionGraphExecutionRequestSchema,
)
from apps.enterprise_document.schemas.scheme_writer_schema import (
    DeliverableFinalResultSchema,
    DocumentAssemblyRequestSchema,
    HardGateResultSchema,
    ParallelSectionExecutionEnvelopeSchema,
    ParallelSectionStateDeltaSchema,
    PlanProjectionStatus,
    RejectedDraftArtifactSchema,
    SchemeGenerationOptionsSchema,
    SchemeWriterInputSchema,
    SchemeWriterOutputSchema,
    SectionExecutionRequestSchema,
    SectionExecutionResultSchema,
    SectionPlanSchema,
)
from apps.enterprise_document.workflows.section_parallel_execution_graph import (
    ParallelSectionBudgetAllocator,
    SectionParallelExecutionGraph,
)
from apps.enterprise_document.services.document_gate import evaluate_scheme_draft
from apps.enterprise_document.services.gate_enforcement import GateFailureKind
from apps.enterprise_document.services.release_publication import (
    ReleasePublicationService,
)
from apps.enterprise_document.services.agent_rag_v1.assembly_validator import (
    DocumentAssemblyValidator,
)
from apps.enterprise_document.services.agent_rag_v1.conflict_repair import (
    ConflictRepairPlanService,
)
from apps.enterprise_document.services.agent_rag_v1.document_verifier import (
    DocumentVerifierService,
)
from apps.enterprise_document.services.agent_rag_v1.final_artifact_review import (
    FinalArtifactReviewService,
)
from apps.enterprise_document.services.agent_rag_v1.generation_report import (
    GenerationReportService,
)
from apps.enterprise_document.services.agent_rag_v1.version_policy import (
    VersionPolicyService,
)
from core.retry_policy import RetryDecisionPolicy, classify_failure
from core.runtime.recovery import current_durable_recovery
from core.runtime.timing import MonotonicTimer, elapsed_ms
from contracts.evidence import evidence_bundle_sha256, project_evidence_bundle
from schemas.agent import AgentResultSchema
from schemas.common import ErrorSchema, ErrorSourceSchema, WarningSchema
from schemas.failure import FailureCategory
from schemas.rag import EvidenceBundleSchema
from schemas.status import ExecutionStatus

from .agent_integration import MainlinePlannerAdapter, MainlinePlannerFailure
from .agent_integration.section_agent_adapter import MainlineSectionAgentFailure
from .capture_service import SchemeCaptureService
from .citation_service import CitationService
from .document_assembler import DocumentAssembler
from .document_citation_registry import DocumentCitationRegistry
from .document_planning_service import DocumentPlanningService
from .evidence_service import SchemeEvidenceService
from .input_service import SchemeInputService
from .runtime_support import SchemeWriterRuntimeSupport
from .section_execution_coordinator import SectionExecutionCoordinator, PreWriteWriterExecutionFailed

# Prospective, explicitly frozen input-only behavior; the original Gate,
# dependency acceptance and formal Release authorities are unchanged.
PWA004_REVIEW_POLICY_KEY = "verifier_rejection_policy"
PWA004_REVIEW_POLICY = "review_draft_continue_v1"
PWA004_REJECTION_KEY = "pwa004_verifier_rejections"
PWA004_SUMMARY_KEY = "pwa004_review_draft"

if TYPE_CHECKING:
    from apps.enterprise_document.services.agent_rag_v1.post_write_composition import (
        TargetPostWriteComposition,
    )
    from apps.enterprise_document.services.agent_rag_v1.workflow_runtime import (
        CanonicalPlanningProjection,
        TargetWorkflowRuntime,
    )
    from apps.enterprise_document.workflows.section_execution_graph import (
        SectionExecutionGraph,
    )


# Exception transport must allow LangGraph to restore __traceback__.
@dataclass
class BudgetDocumentAuthorizationWait(RuntimeError):
    """Verified immutable first-dispatch refusal; neither terminal nor trigger."""
    def __init__(self, *, refusal, materialization_ref, error):
        self.refusal = BudgetDocumentRefusalSchema.model_validate(refusal)
        self.materialization_ref = ArtifactReferenceSchema.model_validate(materialization_ref)
        self.error = error
        super().__init__(error.message)


@dataclass
class SchemeGenerationStageFailure(Exception):
    """Structured technical failure routed to the graph failure node."""

    error: ErrorSchema

    def __str__(self) -> str:
        return self.error.message


class SchemeGenerationStageService:
    """Own the canonical implementation of every top-level business stage."""

    def __init__(
        self,
        *,
        input_service: SchemeInputService,
        evidence_service: SchemeEvidenceService,
        section_execution_coordinator: SectionExecutionCoordinator,
        document_assembler: DocumentAssembler,
        document_planning_service: DocumentPlanningService,
        capture_service: SchemeCaptureService,
        runtime_support: SchemeWriterRuntimeSupport,
        agent_name: str,
        agent_type: str,
        rag_service_name: str,
        section_retrieval_default_enabled: bool,
        enable_semantic_gate: bool,
        section_max_concurrency: int = 1,
        release_publication_service: ReleasePublicationService | None = None,
    ) -> None:
        self.input_service = input_service
        self.evidence_service = evidence_service
        self.section_execution_coordinator = section_execution_coordinator
        self.gate_enforcement_controller = (
            section_execution_coordinator.gate_enforcement_controller
        )
        self.document_assembler = document_assembler
        self.document_planning_service = document_planning_service
        self.capture_service = capture_service
        self.runtime_support = runtime_support
        self.agent_name = agent_name
        self.agent_type = agent_type
        self.rag_service_name = rag_service_name
        self.section_retrieval_default_enabled = bool(
            section_retrieval_default_enabled
        )
        self.enable_semantic_gate = enable_semantic_gate
        if not 1 <= int(section_max_concurrency) <= 32:
            raise ValueError("section_max_concurrency must be between 1 and 32")
        self.section_max_concurrency = int(section_max_concurrency)
        self.state_writer = SharedStateWriter()
        self.section_execution_graph: SectionExecutionGraph | None = None
        self.target_workflow_runtime: TargetWorkflowRuntime | None = None
        self.target_post_write_composition: TargetPostWriteComposition | None = None
        self.section_parallel_execution_graph = SectionParallelExecutionGraph()
        self.planner_adapter: MainlinePlannerAdapter | None = None
        self.document_verifier = DocumentVerifierService()
        self.conflict_repair_service = ConflictRepairPlanService()
        self.document_assembly_validator = DocumentAssemblyValidator()
        self.final_artifact_review_service = FinalArtifactReviewService()
        self.generation_report_service = GenerationReportService()
        self.version_policy_service = VersionPolicyService()
        self.release_publication_service = (
            release_publication_service
            or ReleasePublicationService.from_environment()
        )

    def attach_planner_adapter(
        self,
        adapter: MainlinePlannerAdapter,
    ) -> None:
        """Install the production PlannerAgent adapter at the composition root."""

        self.planner_adapter = adapter

    def attach_section_execution_graph(
        self,
        graph: "SectionExecutionGraph",
    ) -> None:
        """Install the singular durable production Section subgraph."""

        current = self.section_execution_graph
        if current is graph:
            return
        if current is not None and current.coordinator is not graph.coordinator:
            raise RuntimeError(
                "SectionExecutionGraph rebind cannot change the owning coordinator"
            )
        # A restarted WorkflowEngine may replace only the recovery/checkpoint
        # wrapper. Domain authority remains in the shared coordinator/runtimes.
        self.section_execution_graph = graph

    def attach_target_post_write_composition(
        self,
        composition: "TargetPostWriteComposition",
    ) -> None:
        """Attach the singular Wave-13 Target Post-write owner composition."""

        current = self.target_post_write_composition
        if current is composition:
            return
        if current is not None:
            raise RuntimeError("TargetPostWriteComposition is already attached")
        self.target_post_write_composition = composition

    def attach_target_workflow_runtime(
        self,
        runtime: "TargetWorkflowRuntime",
    ) -> None:
        """Attach the Wave-13 canonical Workflow composition owner."""

        if self.target_workflow_runtime is not None and self.target_workflow_runtime is not runtime:
            raise RuntimeError("TargetWorkflowRuntime is already attached")
        self.target_workflow_runtime = runtime

    @staticmethod
    def _hard_gate_failure_policy(
        hard_gate,
        *,
        evidence_requirements_satisfied: bool,
    ) -> tuple[FailureCategory, bool]:
        citation_contract_failed = any(
            not bool(hard_gate.checks.get(check_name, True))
            for check_name in (
                "citation_marker_binding_consistent",
                "citation_marker_binding_bijection",
                "citation_marker_occurrences_verified",
                "citation_orphan_markers_absent",
                "citation_orphan_bindings_absent",
                "citation_targets_valid",
                "citation_grounding_verified",
            )
        )
        if not evidence_requirements_satisfied:
            return FailureCategory.EVIDENCE_INSUFFICIENT, False
        if citation_contract_failed:
            return FailureCategory.VALIDATION, False
        return FailureCategory.BUSINESS_QUALITY, True

    @staticmethod
    def _evidence_assessment_from_bundle(bundle: EvidenceBundleSchema) -> dict:
        assessment = bundle.assessment.model_dump(mode="json", exclude_none=True)
        raw_status = assessment.get("status") or "not_assessed"
        if hasattr(raw_status, "value"):
            raw_status = raw_status.value
        assessment["status"] = str(raw_status).strip().lower() or "not_assessed"
        details = assessment.get("details")
        assessment["details"] = dict(details) if isinstance(details, dict) else {}
        return assessment

    @staticmethod
    def _assembly_chunks_from_canonical_bundles(
        payload: SchemeGenerationGraphPayloadSchema,
    ) -> list:
        """Project and deduplicate chunks from document + final section Bundles on demand."""

        bundles = []
        if payload.document_evidence_bundle is not None:
            bundles.append(payload.document_evidence_bundle)
        bundles.extend(
            item.evidence_bundle
            for item in payload.section_evidence
            if item.evidence_bundle is not None
        )
        chunks = []
        seen: set[tuple[str, str, str]] = set()
        for bundle in bundles:
            _, projected_chunks, _ = project_evidence_bundle(bundle)
            for chunk in projected_chunks:
                key = (
                    chunk.doc_id,
                    chunk.matched_chunk_id,
                    chunk.context_chunk_id,
                )
                if key in seen:
                    continue
                seen.add(key)
                chunks.append(chunk)
        return chunks

    def _require(
        self,
        value,
        *,
        field_name: str,
        node_name: str,
    ):
        if value is None:
            raise ValueError(
                f"{node_name} requires initialized payload field {field_name}"
            )
        return value

    def _target_status_source_mode(
        self,
        shared_state: SharedStateSchema,
        payload: SchemeGenerationGraphPayloadSchema,
        *,
        node_name: str,
        require_completed_target: bool = False,
    ) -> str:
        """Verify exact Target owners before selecting a status source mode."""
        outcome = payload.terminal_outcome
        target_evidence = any(value is not None for value in (
            payload.target_document_artifact_ref,
            payload.target_post_write_control_decision_ref,
            payload.target_run_completion_assessment_ref,
            payload.target_release_publication,
            # A completed Target outcome retains these owner facts even if its
            # payload refs disappear. They require verification, not trust.
            outcome.control_action if outcome is not None else None,
            outcome.quality_state if outcome is not None else None,
            outcome.availability_state if outcome is not None else None,
            outcome.publication_ref if outcome is not None else None,
        )) or (outcome is not None and outcome.publication_exists)
        if self.target_post_write_composition is None:
            if target_evidence:
                raise ValueError(f"{node_name} Target provenance requires attached composition")
            return "legacy_or_early"
        if not require_completed_target and not target_evidence:
            return "legacy_or_early"
        outcome = self._require(
            outcome,
            field_name="terminal_outcome for Target status",
            node_name=node_name,
        )
        self._require(
            payload.target_post_write_control_decision_ref,
            field_name="Target control decision ref",
            node_name=node_name,
        )
        self._require(
            payload.target_run_completion_assessment_ref,
            field_name="Target run completion assessment ref",
            node_name=node_name,
        )
        if (payload.task_id, payload.run_id) != (shared_state.task_id, shared_state.run_id):
            raise ValueError(f"{node_name} Target payload differs from current Run")
        publication = payload.target_release_publication
        if (publication is not None) != outcome.publication_exists:
            raise ValueError(f"{node_name} Target publication differs from terminal outcome")
        if publication is not None and publication.manifest.release_id != outcome.publication_ref:
            raise ValueError(f"{node_name} Target publication ref differs from terminal outcome")
        return "verified_target"

    def _target_status_view(
        self,
        shared_state: SharedStateSchema,
        payload: SchemeGenerationGraphPayloadSchema,
        *,
        node_name: str,
    ) -> dict[str, str | bool | None] | None:
        """Use the exact completed Target outcome for compatibility fields."""
        source_mode = self._target_status_source_mode(
            shared_state, payload, node_name=node_name,
            require_completed_target=True,
        )
        if source_mode == "legacy_or_early":
            return None
        outcome = self._require(
            payload.terminal_outcome,
            field_name="terminal_outcome for Target status",
            node_name=node_name,
        )
        return project_terminal_status(
            outcome,
            source_mode=source_mode,
            expected_task_id=shared_state.task_id,
            expected_run_id=shared_state.run_id,
            expected_phase="evaluate_document_gate",
        )

    def prepare_document_plan(
        self,
        shared_state: SharedStateSchema,
        payload: SchemeGenerationGraphPayloadSchema,
    ) -> SchemeGenerationGraphPayloadSchema:
        """Read normalized input and obtain a canonical plan through PlannerAgent."""

        project_input, table_analysis, structured_facts = (
            self.input_service.read_inputs(shared_state)
        )
        required_sections = list(
            project_input.generation_requirements.required_sections
            or project_input.output_schema.required_sections
        )
        if not required_sections:
            raise ValueError("required_sections is empty in ProjectInput")
        options = SchemeGenerationOptionsSchema(
            need_citation=project_input.generation_requirements.need_citation,
            citation_required_sections=list(
                project_input.generation_requirements.citation_required_sections
            ),
            need_human_review=project_input.generation_requirements.need_human_review,
            max_context_chars=project_input.generation_requirements.max_context_chars,
            max_section_retries=(
                project_input.generation_requirements.max_section_retries
            ),
            max_tokens_per_section=(
                project_input.generation_requirements.max_tokens_per_section
            ),
            min_section_chars=project_input.generation_requirements.min_section_chars,
        )
        required_evidence_sections = list(
            options.citation_required_sections
            or (required_sections if options.need_citation else [])
        )
        document_id = f"document_{shared_state.run_id}"
        started_at = payload.started_at or self.runtime_support.now_iso()
        plan_state = payload.plan_state
        planning_lineage = None
        planner_source_kwargs = {}
        if self.target_workflow_runtime is not None:
            fact_binding, planning_lineage = self.target_workflow_runtime.ensure_initial_plan_prerequisites(
                task_id=shared_state.task_id, run_id=shared_state.run_id,
                document_id=document_id, project_input=project_input,
                required_sections=required_sections, structured_facts=structured_facts,
            )
            planner_source_kwargs = {
                "active_execution_lineage": planning_lineage,
                "canonical_fact_snapshot_ref": fact_binding.version_ref.content_ref,
            }
        if self.planner_adapter is not None:
            try:
                planner_outcome = self.planner_adapter.plan(
                    task_id=shared_state.task_id,
                    run_id=shared_state.run_id,
                    project_input=project_input,
                    required_sections=required_sections,
                    document_id=document_id,
                    created_at=started_at,
                    plan_revision=max(1, plan_state.plan_revision or 1),
                    current_plan_ref=plan_state.active_plan_revision_id,
                    **planner_source_kwargs,
                )
            except MainlinePlannerFailure as exc:
                self.state_writer.add_error(shared_state, exc.error)
                raise SchemeGenerationStageFailure(exc.error) from exc
            document_plan = planner_outcome.document_plan
            plan_state = plan_state.model_copy(
                update={
                    "status": PlanProjectionStatus.READY,
                    "canonical_plan_id": document_plan.plan_id,
                    "plan_revision": planner_outcome.plan_revision,
                    "active_plan_revision_id": document_plan.plan_id,
                    "input_snapshot_version": (
                        planner_outcome.input_snapshot_version
                    ),
                    "section_order": [
                        section.section_id for section in document_plan.sections
                    ],
                    "metadata": {
                        **dict(plan_state.metadata),
                        **dict(planner_outcome.metadata),
                        "planner_agent_attached": True,
                    },
                },
                deep=True,
            )
        else:
            # Legacy/unit-test compatibility only. SupervisorFactory always
            # installs the PlannerAgent adapter on the production mainline.
            document_plan = self.document_planning_service.build_document_plan(
                run_id=shared_state.run_id,
                document_id=document_id,
                project_input=project_input,
                required_sections=required_sections,
                created_at=started_at,
            )
            plan_state = plan_state.model_copy(
                update={
                    "metadata": {
                        **dict(plan_state.metadata),
                        "planner_agent_attached": False,
                        "planning_fallback": "legacy_compatibility",
                    }
                },
                deep=True,
            )
        if self.target_workflow_runtime is not None and planning_lineage is not None:
            planning_projection = self.target_workflow_runtime.ensure_section_plan(
                task_id=shared_state.task_id, run_id=shared_state.run_id,
                document_plan=document_plan, structured_facts=structured_facts,
                project_input_version=project_input.schema_version,
            )
            self.target_workflow_runtime.complete_initial_plan(
                lineage=planning_lineage, projection=planning_projection,
            )
        print(
            f"[DocumentPlan] sections={len(document_plan.sections)} "
            f"source={document_plan.planning_source}",
            flush=True,
        )
        scheme_input = SchemeWriterInputSchema(
            task_id=shared_state.task_id,
            run_id=shared_state.run_id,
            user_input=project_input.user_query,
            requirements=project_input.generation_requirements.model_dump(),
            project_input=project_input,
            table_analysis=table_analysis,
            structured_facts=structured_facts,
            kb_ids=(shared_state.task or {}).get("kb_ids") or [],
            template_id=(shared_state.task or {}).get("template_id"),
            required_sections=required_sections,
            generation_options=options,
        )
        return payload.model_copy(
            update={
                "started_at": started_at,
                "project_input": project_input,
                "table_analysis": table_analysis,
                "structured_facts": list(structured_facts),
                "required_sections": required_sections,
                "required_evidence_sections": required_evidence_sections,
                "generation_options": options,
                "document_id": document_id,
                "document_plan": document_plan,
                "plan_state": plan_state,
                "scheme_input": scheme_input,
            },
            deep=True,
        )

    def retrieve_document_evidence(
        self,
        shared_state: SharedStateSchema,
        payload: SchemeGenerationGraphPayloadSchema,
    ) -> SchemeGenerationGraphPayloadSchema:
        """Run only document-level retrieval and publish its canonical evidence."""

        project_input = self._require(
            payload.project_input,
            field_name="project_input",
            node_name="retrieve_document_evidence",
        )
        print(f"[RAG] START query={project_input.user_query[:80]!r}", flush=True)
        timer = MonotonicTimer()
        rag_started = timer.now()
        document_budget_config = {
            "max_retrieval_rounds": 4,
            "max_rewrite_rounds": 2,
            "max_total_llm_calls": 16,
            "max_total_tokens": 32000,
            "human_review_on_exhaustion": True,
            **dict(
                project_input.generation_requirements.extra.get(
                    "document_rag_budget",
                    {},
                )
                or {}
            ),
        }
        document_rag_budget = WorkflowBudget.from_policy_metadata(
            document_budget_config
        )
        if self.target_workflow_runtime is not None:
            # Wave-13 Target: legacy WorkflowBudget is telemetry only and cannot
            # authorize/deny canonical Research execution.
            rag_result = self.evidence_service.retrieve(
                shared_state,
                project_input,
            )
        else:
            with activate_workflow_budget(document_rag_budget):
                rag_result = self.evidence_service.retrieve(
                    shared_state,
                    project_input,
                )
        print(
            f"[RAG] END   success={bool(rag_result and rag_result.success)} "
            f"latency_ms={elapsed_ms(timer, rag_started)}",
            flush=True,
        )
        rag_context, chunks, citations, rag_output = (
            self.evidence_service.extract_rag_output(shared_state, rag_result)
        )
        rag_output.setdefault("extra", {})["document_rag_model_budget"] = (
            document_rag_budget.snapshot()
        )
        citation_registry = DocumentCitationRegistry()
        rag_context, chunks, citations, rag_output = citation_registry.remap_bundle(
            context=rag_context,
            chunks=chunks,
            citations=citations,
            normalized=rag_output,
            scope="document",
            query=project_input.user_query,
        )
        print(
            f"[RAG] NORMALIZED chunks={len(chunks)} citations={len(citations)} "
            f"citation_ids={[item.citation_id for item in citations]}",
            flush=True,
        )
        if rag_result is None or not rag_result.success:
            node_name = "retrieve_document_evidence"
            underlying = rag_result.error if rag_result else None
            if underlying is not None:
                source = (
                    underlying.source.model_copy(
                        update={"step_name": node_name},
                        deep=True,
                    )
                    if underlying.source is not None
                    else ErrorSourceSchema(
                        component=self.rag_service_name,
                        agent_name=self.agent_name,
                        step_name=node_name,
                    )
                )
                error = underlying.model_copy(
                    update={
                        "failed_node": node_name,
                        "source": source,
                    },
                    deep=True,
                )
            else:
                error = self.runtime_support.error(
                    "RAG_TOOL_FAILED",
                    rag_result.error_message if rag_result else "RAG tool is not configured",
                    node=node_name,
                    retryable=True,
                    failure_category=FailureCategory.AVAILABILITY,
                    requires_human=True,
                    user_message="知识检索失败，文档生成任务已停止。",
                )
            self.state_writer.add_error(shared_state, error)
            raise SchemeGenerationStageFailure(error)

        section_retrieval_enabled = bool(
            project_input.generation_requirements.extra.get(
                "enable_section_aware_retrieval",
                self.section_retrieval_default_enabled,
            )
        )
        corrective_retrieval_enabled = bool(
            project_input.generation_requirements.extra.get(
                "enable_corrective_section_retrieval",
                section_retrieval_enabled,
            )
        )
        return payload.model_copy(
            update={
                "document_evidence_bundle": rag_result,
                "document_tool_call_ids": [rag_result.retrieval_trace_id],
                "section_retrieval_enabled": section_retrieval_enabled,
                "corrective_retrieval_enabled": corrective_retrieval_enabled,
                "all_citations": list(citations),
                "all_rag_outputs": [
                    SchemeEvidenceService.retrieval_history_projection(
                        rag_output,
                        scope="document",
                        evidence_bundle=(
                            rag_result
                            if isinstance(rag_result, EvidenceBundleSchema)
                            else None
                        ),
                    )
                ],
            },
            deep=True,
        )

    def initialize_generation(
        self,
        shared_state: SharedStateSchema,
        payload: SchemeGenerationGraphPayloadSchema,
    ) -> SchemeGenerationGraphPayloadSchema:
        """Initialize deterministic generation context without external calls."""
        # 获取项目输入
        project_input = self._require(
            payload.project_input,
            field_name="project_input",
            node_name="initialize_generation",
        )
        # 获取文档ID
        document_id = self._require(
            payload.document_id,
            field_name="document_id",
            node_name="initialize_generation",
        )
        # 初始化生成状态
        self.state_writer.initialize_generation(
            shared_state,
            document_id=document_id,
            document_title=project_input.output_schema.document_title,
            required_sections=list(payload.required_sections),
        )
        return payload.model_copy(
            update={
                "sections": [],
                "section_evidence": [],
            },
            deep=True,
        )

    @classmethod
    def _remap_citation_payload(
        cls,
        value: Any,
        *,
        id_map: dict[str, str],
        registry: DocumentCitationRegistry,
    ) -> Any:
        """Remap diagnostic RAG payloads during deterministic ordered fan-in."""

        if isinstance(value, list):
            return [
                cls._remap_citation_payload(
                    item,
                    id_map=id_map,
                    registry=registry,
                )
                for item in value
            ]
        if not isinstance(value, dict):
            return value
        remapped: dict[str, Any] = {}
        for key, item in value.items():
            if key == "citation_id" and isinstance(item, str):
                remapped[key] = id_map.get(item, item)
            elif key == "citation_ids" and isinstance(item, list):
                remapped[key] = [
                    id_map.get(str(citation_id), str(citation_id))
                    for citation_id in item
                ]
            elif key in {
                "content",
                "context_text",
                "model_output",
                "prompt",
            } and isinstance(item, str):
                remapped[key] = registry.replace_markers(item, id_map)
            else:
                remapped[key] = cls._remap_citation_payload(
                    item,
                    id_map=id_map,
                    registry=registry,
                )
        return remapped

    def _merge_parallel_section_result(
        self,
        result: SectionExecutionResultSchema,
        *,
        document_id: str,
        citation_registry: DocumentCitationRegistry,
    ) -> SectionExecutionResultSchema:
        """Merge an isolated section result in deterministic document order."""

        canonical_bundle = result.evidence.evidence_bundle
        if canonical_bundle is None:
            raise ValueError("parallel section result requires canonical evidence_bundle")
        projection_context, projection_chunks, projection_citations = (
            project_evidence_bundle(canonical_bundle)
        )
        projection_scope = canonical_bundle.retrieval_scope
        projection_query = canonical_bundle.query

        # A parallel branch may carry diagnostic/rejected candidate citations in
        # its local registry. Commit only identities present in the final accepted
        # canonical Bundle, so rejected candidates cannot reserve document labels.
        accepted_refs = {
            citation_registry._identity(item) for item in projection_citations
        }
        local_citations = [
            item
            for item in result.citation_registry
            if citation_registry._identity(item) in accepted_refs
        ]
        if current_diagnostics().enabled:
            try:
                cap = current_diagnostics().config.max_pool_items
                capture_event('P-FANIN', {
                    'phase':'before', 'section_id':result.section.section_id,
                    'section_order':result.section.section_order,
                    'accepted_bundle_sha256':evidence_bundle_sha256(canonical_bundle),
                    'local_registry_count':len(local_citations),
                    'local_registry':[{ 'citation_id':item.citation_id,
                        'source_identity':citation_registry._identity(item)}
                        for item in local_citations[:cap]],
                    'before':{key:getattr(result.section,key) for key in ('content','model_output','prompt')},
                    'before_bindings':[item.model_dump(mode='json') for item in result.section.citation_bindings[:cap]],
                    'completeness':'COMPLETE' if len(local_citations)<=cap else 'CAPTURE_INCOMPLETE',
                },unit_id='B05',operation_id=result.section.section_id,representation_kind='DISPLAY_PROJECTION')
            except Exception:
                # An optional observer can never replace registration failure.
                current_diagnostics().emit({'event_type':'P-FANIN_CAPTURE_ERROR','completeness':'CAPTURE_INCOMPLETE'})
        committed_citations, id_map = citation_registry.register(
            local_citations,
            scope=f"section:{result.section.section_id}",
            query=projection_query,
        )
        id_map.update(
            {
                source.citation_id: target.citation_id
                for source, target in zip(local_citations, committed_citations)
            }
        )
        _context, _chunks, _evidence_citations, projection_output = (
            citation_registry.remap_bundle(
                context=projection_context,
                chunks=projection_chunks,
                citations=projection_citations,
                normalized={},
                scope=projection_scope,
                query=projection_query,
            )
        )
        id_map.update(
            dict((projection_output.get("extra") or {}).get("citation_id_map") or {})
        )
        content = citation_registry.replace_markers(
            result.section.content,
            id_map,
        )
        model_output = citation_registry.replace_markers(
            result.section.model_output,
            id_map,
        )
        prompt = citation_registry.replace_markers(
            result.section.prompt,
            id_map,
        )
        rebuilt_bindings = CitationService.bind_citations(
            document_id,
            result.section.section_id,
            content,
            citation_registry.all(),
        )
        original_by_index = {
            binding.target_marker_index: binding
            for binding in result.section.citation_bindings
            if binding.target_marker_index is not None
        }
        rebuilt_bindings = [
            binding.model_copy(
                update={
                    "metadata": dict(
                        original_by_index.get(
                            binding.target_marker_index,
                            binding,
                        ).metadata
                    )
                }
            )
            for binding in rebuilt_bindings
        ]
        section = result.section.model_copy(
            update={
                "content": content,
                "model_output": model_output,
                "prompt": prompt,
                "citation_ids": list(
                    dict.fromkeys(
                        binding.citation_id for binding in rebuilt_bindings
                    )
                ),
                "citation_bindings": rebuilt_bindings,
                "extra": {
                    **dict(result.section.extra),
                    "parallel_execution": True,
                    "citation_fan_in_order": result.section.section_order,
                },
            },
            deep=True,
        )
        evidence = result.evidence.model_copy(
            update={
                "evidence_contract_sha256": (
                    evidence_bundle_sha256(result.evidence.evidence_bundle)
                    if result.evidence.evidence_bundle is not None
                    else result.evidence.evidence_contract_sha256
                ),
                "metadata": {
                    **dict(result.evidence.metadata),
                    "parallel_execution": True,
                },
            },
            deep=True,
        )
        rag_outputs = [
            self._remap_citation_payload(
                item,
                id_map=id_map,
                registry=citation_registry,
            )
            for item in result.rag_outputs
        ]
        if current_diagnostics().enabled:
            try:
                cap = current_diagnostics().config.max_pool_items
                capture_event('P-FANIN', {
                    'phase':'after','section_id':result.section.section_id,
                    'section_order':result.section.section_order,'id_map':id_map,
                    'global_registry_count':len(committed_citations),
                    'global_registry':[{ 'citation_id':item.citation_id,
                        'source_identity':citation_registry._identity(item)}
                        for item in committed_citations[:cap]],
                    'after':{key:getattr(section,key) for key in ('content','model_output','prompt')},
                    'after_bindings':[item.model_dump(mode='json') for item in rebuilt_bindings[:cap]],
                    'completeness':'COMPLETE' if len(committed_citations)<=cap else 'CAPTURE_INCOMPLETE',
                },unit_id='B05',operation_id=result.section.section_id,representation_kind='DISPLAY_PROJECTION')
            except Exception:
                current_diagnostics().emit({'event_type':'P-FANIN_CAPTURE_ERROR','completeness':'CAPTURE_INCOMPLETE'})
        return result.model_copy(
            update={
                "section": section,
                "evidence": evidence,
                "rag_outputs": rag_outputs,
                "citation_registry": citation_registry.all(),
            },
            deep=True,
        )

    @staticmethod
    def _parallel_state_delta(
        before: SharedStateSchema,
        after: SharedStateSchema,
        *,
        section_id: str,
    ) -> ParallelSectionStateDeltaSchema:
        """Extract only additive, section-scoped writes from one branch."""

        def appended(before_items: list, after_items: list, path: str) -> list:
            if after_items[: len(before_items)] != before_items:
                raise StateWriteContractViolation(
                    changed_paths=[path],
                    allowed_paths=[f"{path} (append-only)"],
                )
            return list(after_items[len(before_items) :])

        generated_ids = appended(
            before.context_bundle.generation.generated_section_ids,
            after.context_bundle.generation.generated_section_ids,
            "context_bundle.generation.generated_section_ids",
        )
        warnings = appended(before.warnings, after.warnings, "warnings")
        errors = appended(before.errors, after.errors, "errors")
        runtime_errors = appended(
            before.context_bundle.runtime.errors,
            after.context_bundle.runtime.errors,
            "context_bundle.runtime.errors",
        )

        generated_outputs: dict[str, Any] = {}
        for key, value in before.generated_outputs.items():
            if (
                key not in after.generated_outputs
                or after.generated_outputs[key] != value
            ):
                raise StateWriteContractViolation(
                    changed_paths=[f"generated_outputs.{key}"],
                    allowed_paths=["generated_outputs.<new-section-call-id>"],
                )
        for key, value in after.generated_outputs.items():
            if key not in before.generated_outputs:
                generated_outputs[key] = value

        metadata_updates: dict[str, dict[str, Any]] = {}
        for scope, before_metadata, after_metadata in (
            (
                "generation",
                before.context_bundle.generation.metadata,
                after.context_bundle.generation.metadata,
            ),
            (
                "runtime",
                before.context_bundle.runtime.metadata,
                after.context_bundle.runtime.metadata,
            ),
        ):
            for key, value in before_metadata.items():
                if key not in after_metadata or after_metadata[key] != value:
                    raise StateWriteContractViolation(
                        changed_paths=[
                            f"context_bundle.{scope}.metadata.{key}"
                        ],
                        allowed_paths=[
                            f"context_bundle.{scope}.metadata.{section_id}"
                        ],
                    )
            new_values = {
                key: value
                for key, value in after_metadata.items()
                if key not in before_metadata
            }
            invalid = [
                key
                for key in new_values
                if key != section_id and not key.startswith(f"{section_id}:")
            ]
            if invalid:
                raise StateWriteContractViolation(
                    changed_paths=[
                        f"context_bundle.{scope}.metadata.{key}"
                        for key in invalid
                    ],
                    allowed_paths=[
                        f"context_bundle.{scope}.metadata.{section_id}"
                    ],
                )
            if new_values:
                metadata_updates[scope] = new_values

        retry_delta = (
            after.context_bundle.runtime.retry_count
            - before.context_bundle.runtime.retry_count
        )
        if retry_delta < 0:
            raise StateWriteContractViolation(
                changed_paths=["context_bundle.runtime.retry_count"],
                allowed_paths=["context_bundle.runtime.retry_count (+delta)"],
            )

        delta = ParallelSectionStateDeltaSchema(
            generated_section_ids=generated_ids,
            generated_outputs=generated_outputs,
            warnings=warnings,
            errors=errors,
            runtime_errors=runtime_errors,
            metadata_updates=metadata_updates,
            retry_count_delta=retry_delta,
        )
        expected = before.model_copy(deep=True)
        expected.context_bundle.generation.generated_section_ids.extend(
            delta.generated_section_ids
        )
        expected.generated_outputs.update(delta.generated_outputs)
        expected.warnings.extend(delta.warnings)
        expected.errors.extend(delta.errors)
        expected.context_bundle.runtime.errors.extend(delta.runtime_errors)
        expected.context_bundle.runtime.retry_count += delta.retry_count_delta
        expected.context_bundle.generation.metadata.update(
            delta.metadata_updates.get("generation", {})
        )
        expected.context_bundle.runtime.metadata.update(
            delta.metadata_updates.get("runtime", {})
        )
        if expected.model_dump(mode="python") != after.model_dump(mode="python"):
            changed_paths = GraphStateDiffer().changed_paths(
                expected.model_dump(mode="python"),
                after.model_dump(mode="python"),
            )
            raise StateWriteContractViolation(
                changed_paths=changed_paths,
                allowed_paths=[
                    "context_bundle.generation.generated_section_ids",
                    "generated_outputs.<new-section-call-id>",
                    "warnings",
                    "errors",
                    "context_bundle.runtime.errors",
                    "context_bundle.generation.metadata.<section-id>",
                    "context_bundle.runtime.metadata.<section-id>",
                    "context_bundle.runtime.retry_count",
                ],
            )
        return delta

    @staticmethod
    def _apply_parallel_state_delta(
        shared_state: SharedStateSchema,
        delta: ParallelSectionStateDeltaSchema,
    ) -> None:
        """Commit one validated delta in deterministic fan-in order."""

        generated_ids = (
            shared_state.context_bundle.generation.generated_section_ids
        )
        for section_id in delta.generated_section_ids:
            if section_id not in generated_ids:
                generated_ids.append(section_id)
        for key, value in delta.generated_outputs.items():
            if (
                key in shared_state.generated_outputs
                and shared_state.generated_outputs[key] != value
            ):
                raise StateWriteContractViolation(
                    changed_paths=[f"generated_outputs.{key}"],
                    allowed_paths=["generated_outputs.<unique-section-call-id>"],
                )
            shared_state.generated_outputs[key] = value
        shared_state.warnings.extend(delta.warnings)
        shared_state.errors.extend(delta.errors)
        shared_state.context_bundle.runtime.errors.extend(delta.runtime_errors)
        shared_state.context_bundle.runtime.retry_count += delta.retry_count_delta
        for scope, updates in delta.metadata_updates.items():
            target = (
                shared_state.context_bundle.generation.metadata
                if scope == "generation"
                else shared_state.context_bundle.runtime.metadata
            )
            for key, value in updates.items():
                if key in target and target[key] != value:
                    raise StateWriteContractViolation(
                        changed_paths=[
                            f"context_bundle.{scope}.metadata.{key}"
                        ],
                        allowed_paths=["conflict-free section metadata"],
                    )
                target[key] = value

    @staticmethod
    def _pwa004_opted_in(project_input) -> bool:
        extra = project_input.generation_requirements.extra or {}
        value = extra.get(PWA004_REVIEW_POLICY_KEY)
        if value is not None and value != PWA004_REVIEW_POLICY:
            raise ValueError("Unsupported prospective Verifier rejection policy")
        return value == PWA004_REVIEW_POLICY

    def _pwa004_rejection_explanation(self, *, payload, section_result, row):
        """Read the original refusal while its exact Writer lineage is current.

        This derivative is separate from the native rejection/recovery receipt.
        No model, mutation, failed-Run currentness exception or Release authority.
        """
        from apps.enterprise_document.schemas.agent_rag_v1.verification import (
            ClaimOperationContextSchema, VerifierSemanticResultCandidateSchema,
        )
        from apps.enterprise_document.services.agent_rag_v1.claim_verification_runtime import (
            VerifierSupportVerdictAdmissionError,
        )

        lineage = section_result.active_execution_lineage
        try:
            graph = self.section_execution_graph
            verifier = graph.coordinator.claim_verifier_composition
            runtime = graph.claim_verification_runtime
            section = section_result.section
            if (verifier.claim_runtime is not runtime or lineage is None
                    or (row["section_id"], row["section_title"]) !=
                       (section.section_id, section.section_title)
                    or lineage.run_id != payload.run_id
                    or lineage.attempt_scope_ref != "section:" + section.section_id
                    or lineage.attempt_id != row["attempt_id"]
                    or lineage.input_snapshot_version != row["input_snapshot_version"]
                    or runtime.lifecycle_service.load_run_identity(payload.run_id).task_id != payload.task_id):
                raise ValueError("PWA004 explanation differs from native Section/Attempt")
            fingerprint = row["verifier_semantic_input_sha256"]
            if (not isinstance(fingerprint, str) or len(fingerprint) != 64
                    or any(c not in "0123456789abcdef" for c in fingerprint)):
                raise ValueError("PWA004 explanation fingerprint is invalid")
            physical = canonical_sha256({"contract": "verifier-physical-occurrence-v2",
                "verifier_semantic_input_sha256": fingerprint})
            prefix = "artifact://canonical-verifier-v2/" + physical
            if row["candidate_ref"] != prefix + "/candidate":
                raise ValueError("PWA004 explanation Candidate identity differs")
            reference, stored = verifier.store.get_json_with_reference(
                row["candidate_ref"], task_id=payload.task_id, run_id=payload.run_id,
                section_id=section.section_id)
            if ((reference.artifact_type, reference.schema_version) !=
                    ("canonical_verifier_candidate", "canonical_verifier_candidate_v2")
                    or stored.get("outcome") != "completed"):
                raise ValueError("PWA004 explanation requires the exact durable Candidate")
            candidate = VerifierSemanticResultCandidateSchema.model_validate(stored["candidate"])
            invocation = runtime.verifier_input_from_candidate(candidate)
            if (candidate.active_lineage != lineage
                    or verifier._physical(invocation)[2] != prefix
                    or candidate.recommended_verdict.value != "VERIFIED"
                    or canonical_sha256(candidate.model_dump(mode="json")) != row["candidate_sha256"]
                    or section_result.writer_execution_ref is None
                    or section_result.writer_generation_invocation_ref !=
                       invocation.verifier_project_input_basis.writer_generation_invocation_ref
                    or section_result.writer_execution_ref.logical_execution_key !=
                       section_result.writer_generation_invocation_ref.logical_execution_key
                    or section_result.writer_execution_ref.logical_execution_key.writer_subject_ref.model_dump(mode="json") !=
                       {"run_id": payload.run_id, "document_id": payload.document_id,
                        "section_id": section.section_id}):
                raise ValueError("PWA004 explanation differs from exact Writer/Candidate")
            claim = runtime.resolve_claim(candidate.claim_ref)
            binding = runtime.resolve_binding_facts(candidate.binding_facts_ref)
            support = verifier.support.runtime.resolve_support(candidate.claim_evidence_support_ref)
            if runtime.resolve_claim_head(claim.claim_ref) is not None:
                raise ValueError("PWA004 rejected Claim acquired a formal current head")
            verifier.validate_read_sources(binding, support, lineage)
            verifier.validate_v2_identity(claim, binding, support, candidate, lineage)
            try:
                runtime.validate_v2_provenance(binding, support, candidate)
            except VerifierSupportVerdictAdmissionError as refusal:
                native = graph._pwa004_rejection_receipt(refusal, expected_claim=claim,
                    expected_binding=binding, expected_support=support, expected_lineage=lineage)
            else:
                raise ValueError("PWA004 explanation Candidate is not the original refusal")
            if native != {key: value for key, value in row.items()
                          if key not in {"section_id", "section_title"}}:
                raise ValueError("PWA004 explanation would change the native rejection")
            input_ref = prefix + "/input"
            input_reference, receipt = verifier.store.get_json_with_reference(
                input_ref, task_id=payload.task_id, run_id=payload.run_id,
                section_id=section.section_id)
            if ((input_reference.artifact_type, input_reference.schema_version) !=
                    ("canonical_verifier_input", "canonical_verifier_input_v2")
                    or receipt.get("schema_version") != "canonical_verifier_input_v2"
                    or receipt.get("input_fingerprint") != fingerprint):
                raise ValueError("PWA004 explanation input identity differs")
            context = ClaimOperationContextSchema(active_lineage=lineage,
                authorization_ref=candidate.verification_authorization_ref,
                operation_purpose="pwa004-review-read")
            # Reuse the original Owner's selected/eligible/Writer-visible sources.
            # This is intentionally unavailable once the Run/Attempt has failed.
            expected_invocation, material = verifier._material(
                claim, binding, support, context, _recorded_invocation=invocation)
            if (expected_invocation != invocation or receipt.get("input_material") != material):
                raise ValueError("PWA004 explanation input differs from its canonical visible sources")
            if (material["paragraph_text"] not in section.content
                    or material["paragraph_text"][claim.text_span.start_offset:claim.text_span.end_offset]
                       != claim.text_span.text):
                raise ValueError("PWA004 explanation Claim location differs from Writer body")
            evidence_keys = ("exact_source_ref", "evidence_id", "doc_id", "title",
                             "context_text", "context_text_sha256")
            explanation = dict(row, task_id=payload.task_id, run_id=payload.run_id,
                input_ref=input_ref, input_content_hash=input_reference.content_hash,
                claim_text=claim.text_span.text, paragraph_text=material["paragraph_text"],
                start_offset=claim.text_span.start_offset, end_offset=claim.text_span.end_offset,
                text_span_hash=claim.text_span_hash,
                binding_facts=material["binding_facts"],
                shared_support={key: material["shared_support"][key]
                    for key in ("relation", "supporting_refs", "contradicting_refs")},
                evidence=[{key: item[key] for key in evidence_keys} for item in material["evidence"]],
                candidate_verdict=candidate.recommended_verdict.value,
                verification_result_committed=False,
                explanation=("原生 Candidate 提出了 VERIFIED；SharedSupport 为 " + support.relation.value
                    + "，M6 规则 VERIFIER_VERIFIED_FORBIDDEN_BY_SHARED_SUPPORT 拒绝该候选。"
                    "该拒绝不证明陈述为假，也不构成正式质量 FAIL。"),
                limitations=["未产生该候选的正式 VerificationResult。",
                    "证据可见或被引用不等于已证明陈述成立。",
                    "本说明不授予正式发布或人工签发权限。"])
            return dict(explanation, explanation_sha256=canonical_sha256(explanation))
        except Exception as exc:
            # Trusted Section lineage attributes failure; external Error.details
            # never grants an artifact read or supplies explanatory authority.
            raise SchemeGenerationStageFailure(ErrorSchema(
                error_code="PWA004_REVIEW_EXPLANATION_INVALID",
                error_type=type(exc).__name__,
                message="PWA004 rejected Candidate explanation could not be verified.",
                failed_node="execute_sections", failure_category=FailureCategory.VALIDATION,
                retryable=False, details={
                    "c1_triggering_attempt_id": lineage.attempt_id if lineage is not None else None,
                    "explanation_cause_type": type(exc).__name__,
                })) from exc

    def execute_sections(
        self,
        shared_state: SharedStateSchema,
        payload: SchemeGenerationGraphPayloadSchema,
    ) -> SchemeGenerationGraphPayloadSchema:
        """Execute sections serially or through declared safe parallel batches."""

        checkpoint_current_execution()
        project_input = self._require(
            payload.project_input,
            field_name="project_input",
            node_name="execute_sections",
        )
        document_plan = self._require(
            payload.document_plan,
            field_name="document_plan",
            node_name="execute_sections",
        )
        document_id = self._require(
            payload.document_id,
            field_name="document_id",
            node_name="execute_sections",
        )
        document_bundle = self._require(
            payload.document_evidence_bundle,
            field_name="document_evidence_bundle",
            node_name="execute_sections",
        )
        pwa004 = self._pwa004_opted_in(project_input)
        if pwa004 and (self.target_workflow_runtime is None
                       or self.section_execution_graph is None
                       or self.section_max_concurrency != 1):
            raise RuntimeError("PWA004 requires canonical serial Section execution")
        rag_context, document_chunks, canonical_document_citations = (
            project_evidence_bundle(document_bundle)
        )
        citation_registry = DocumentCitationRegistry.from_citations(
            payload.all_citations
        )
        rag_context, document_chunks, document_citations, _ = (
            citation_registry.remap_bundle(
                context=rag_context,
                chunks=document_chunks,
                citations=canonical_document_citations,
                normalized={},
                scope="document",
                query=document_bundle.query,
            )
        )
        sections = []
        section_evidence_bundles = []
        all_rag_outputs = list(payload.all_rag_outputs)
        print(
            f"[SchemeWriter] 开始逐章节生成，共 {len(document_plan.sections)} 章",
            flush=True,
        )
        ordered_plans = sorted(
            document_plan.sections,
            key=lambda item: (item.section_order, item.section_id),
        )
        section_count = len(ordered_plans)
        canonical_planning: CanonicalPlanningProjection | None = None
        if self.target_workflow_runtime is not None:
            if self.section_execution_graph is None:
                raise RuntimeError(
                    "Target Workflow requires one durable attached SectionExecutionGraph"
                )
            checkpoint_current_execution()
            canonical_planning = self.target_workflow_runtime.ensure_section_plan(
                task_id=payload.task_id,
                run_id=payload.run_id,
                document_plan=document_plan,
                structured_facts=list(payload.structured_facts),
                project_input_version=project_input.schema_version,
            )
        base_section_budget = WorkflowBudget.from_policy_metadata(
            self.section_execution_coordinator.generation_quality_metadata
        )
        parallel_budget_config = dict(
            project_input.generation_requirements.extra.get(
                "parallel_section_budget",
                {},
            )
            or {}
        )
        parallel_llm_slice = max(
            1,
            int(
                parallel_budget_config.get(
                    "llm_calls_per_section",
                    base_section_budget.max_total_llm_calls,
                )
            ),
        )
        parallel_token_slice = max(
            256,
            int(
                parallel_budget_config.get(
                    "tokens_per_section",
                    base_section_budget.max_total_tokens,
                )
            ),
        )
        parallel_budget = (
            None
            if self.target_workflow_runtime is not None
            else ParallelSectionBudgetAllocator(
                max_total_llm_calls=max(
                    1,
                    int(
                        parallel_budget_config.get(
                            "max_total_llm_calls",
                            parallel_llm_slice * max(section_count, 1),
                        )
                    ),
                ),
                max_total_tokens=max(
                    256,
                    int(
                        parallel_budget_config.get(
                            "max_total_tokens",
                            parallel_token_slice * max(section_count, 1),
                        )
                    ),
                ),
            )
        )
        section_position = {
            item.section_id: index
            for index, item in enumerate(ordered_plans, start=1)
        }
        completed_ids: set[str] = set()
        results_by_id: dict[str, SectionExecutionResultSchema] = {}
        remaining = {item.section_id: item for item in ordered_plans}
        pwa004_rejected_ids: set[str] = set()
        pwa004_blocked: dict[str, dict[str, Any]] = {}

        def completed_sections() -> list:
            if not pwa004:
                return [results_by_id[item.section_id].section for item in ordered_plans
                        if item.section_id in results_by_id]
            # Source membership, not presence of Writer bytes or success flags,
            # grants context consumption. VERIFYING is not ACCEPTED.
            candidates = []
            dependencies = self.target_workflow_runtime.section_dependencies
            for item in ordered_plans:
                result = results_by_id.get(item.section_id)
                if result is None or result.canonical_section_source_binding is None:
                    continue
                source = dependencies.validate_source(result.canonical_section_source_binding)
                if source.workflow_status == "ACCEPTED":
                    candidates.append(result.section)
            return candidates

        def run_one(
            section_plan: SectionPlanSchema,
            *,
            state: SharedStateSchema,
            registry: DocumentCitationRegistry,
            previous_sections: list,
            emit_events: bool,
            parallel_isolated: bool,
            budget_policy_override: dict[str, Any],
        ) -> SectionExecutionResultSchema:
            checkpoint_current_execution()
            section_index = section_position[section_plan.section_id]
            if emit_events:
                emit_workflow_custom_event(
                    "section_started",
                    node_name="execute_sections",
                    section_id=section_plan.section_id,
                    progress=(section_index - 1) / max(section_count, 1),
                    payload={
                        "section_title": section_plan.section_title,
                        "section_index": section_index,
                        "section_count": section_count,
                    },
                )
            canonical_plan_binding = (
                canonical_planning.binding_for_section(section_plan.section_id)
                if canonical_planning is not None
                else None
            )
            request_data = {
                "document_id": document_id,
                "project_input": project_input,
                "section_plan": section_plan,
                "structured_facts": list(payload.structured_facts),
                "previous_sections": list(previous_sections),
                "document_evidence_bundle": payload.document_evidence_bundle,
                "document_tool_call_ids": list(payload.document_tool_call_ids),
                "section_retrieval_enabled": payload.section_retrieval_enabled,
                "corrective_retrieval_enabled": (
                    payload.corrective_retrieval_enabled
                ),
                "parallel_isolated": parallel_isolated,
                "budget_policy_override": dict(budget_policy_override),
                "canonical_plan_binding": canonical_plan_binding,
            }
            if self.section_execution_graph is not None:
                recovery = current_durable_recovery()
                section_result = (
                    self.section_execution_graph.recover_or_reuse(
                        workflow_namespace=payload.workflow_namespace,
                        task_id=payload.task_id,
                        run_id=payload.run_id,
                        document_id=document_id,
                        section_id=section_plan.section_id,
                        project_input=project_input,
                        section_plan=section_plan,
                        shared_state=state,
                        citation_registry=registry,
                        expected_canonical_plan_binding=canonical_plan_binding,
                    )
                    if recovery is not None
                    else None
                )
                if section_result is None:
                    checkpoint_current_execution()
                    if self.target_workflow_runtime is not None:
                        if canonical_plan_binding is None:
                            raise RuntimeError("Target Section execution requires canonical Plan")
                        lineage, authorization = (
                            self.target_workflow_runtime
                            .ensure_section_execution_prerequisites(
                                canonical_plan_binding
                            )
                        )
                        request_data["active_execution_lineage"] = lineage
                        request_data["research_authorization_binding"] = authorization
                    checkpoint_current_execution()
                    try:
                        section_result = self.section_execution_graph.execute(
                            SectionGraphExecutionRequestSchema(**request_data),
                            shared_state=state,
                            citation_registry=registry,
                            workflow_namespace=payload.workflow_namespace,
                        )
                    except Exception as exc:
                        lineage = request_data.get("active_execution_lineage")
                        if lineage is not None:
                            # Preserve the exact dispatch identity for C1; sibling
                            # truth is still derived under the lifecycle owner's lock.
                            exc.c1_triggering_attempt_id = lineage.attempt_id
                        raise
            else:
                if self.target_workflow_runtime is not None:
                    raise RuntimeError(
                        "Target Workflow cannot fall back to ephemeral Section graph"
                    )
                section_result = self.section_execution_coordinator.execute(
                    SectionExecutionRequestSchema(
                        shared_state=state,
                        **request_data,
                        document_rag_context=rag_context,
                        document_retrieved_chunks=list(document_chunks),
                        document_citations=list(document_citations),
                        document_rag_output={"extra": dict(document_bundle.extra or {})},
                    ),
                    citation_registry=registry,
                )
            checkpoint_current_execution()
            if emit_events:
                emit_workflow_custom_event(
                    "section_generated",
                    node_name="execute_sections",
                    section_id=section_plan.section_id,
                    status=section_result.section.status.value,
                    progress=section_index / max(section_count, 1),
                    payload={
                        "section_title": section_plan.section_title,
                        "content_chars": len(section_result.section.content),
                    },
                )
                emit_workflow_custom_event(
                    "section_completed",
                    node_name="execute_sections",
                    section_id=section_plan.section_id,
                    status=section_result.section.status.value,
                    progress=section_index / max(section_count, 1),
                    payload={
                        "section_title": section_plan.section_title,
                        "section_index": section_index,
                        "section_count": section_count,
                    },
                )
            return section_result

        dependencies = (self.target_workflow_runtime.section_dependencies
                        if self.target_workflow_runtime is not None else None)

        def dependency_ready(item):
            # ensure_section_plan deterministically lowers hard_dependencies from
            # this exact current DocumentPlan entry before execution. With no
            # dependency edge there is no upstream source/currentness to assess.
            if not item.depends_on:
                return True
            if dependencies is None:
                return set(item.depends_on) <= completed_ids
            assessment = dependencies.assess_dependencies(canonical_planning.binding_for_section(item.section_id))
            return assessment.aggregate == "ALL_SATISFIED"

        while remaining:
            checkpoint_current_execution()
            if pwa004 and pwa004_rejected_ids:
                # Remove only the explicit, transitive hard-dependency closure
                # of a known review-only Section. Never make a fake SKIP Attempt.
                changed = True
                while changed:
                    changed = False
                    for item in ordered_plans:
                        if item.section_id not in remaining:
                            continue
                        upstream = sorted(set(item.depends_on) &
                                          (pwa004_rejected_ids | set(pwa004_blocked)))
                        if upstream:
                            pwa004_blocked[item.section_id] = {
                                "section_id": item.section_id,
                                "section_title": item.section_title,
                                "blocked_by": upstream,
                                "reason_code": "PWA004_HARD_DEPENDENCY_UNVERIFIED",
                            }
                            remaining.pop(item.section_id)
                            changed = True
            if not remaining:
                break
            ready = [
                item
                for item in ordered_plans
                if item.section_id in remaining
                and dependency_ready(item)
            ]
            if not ready:
                if dependencies is not None:
                    # Preserve typed WAIT/UNSATISFIABLE/INTEGRITY result; no dependent
                    # Research/Writer or new Attempt is dispatched/terminalized here.
                    first_waiting = next(item for item in ordered_plans if item.section_id in remaining)
                    dependencies.require_dependencies(canonical_planning.binding_for_section(first_waiting.section_id))
                raise ValueError(
                    "no section is ready; dependency graph is inconsistent"
                )
            first = ready[0]
            parallel_safe = (
                self.section_max_concurrency > 1
                and first.can_run_in_parallel
                and not first.requires_global_context
                and not first.depends_on
            )
            batch = [first]
            if parallel_safe:
                batch = [
                    item
                    for item in ready
                    if item.can_run_in_parallel
                    and not item.requires_global_context
                    and not item.depends_on
                    and item.execution_group == first.execution_group
                ][: self.section_max_concurrency]

            if len(batch) > 1:
                previous = completed_sections()
                base_citations = citation_registry.all()
                budget_reservations = (
                    {
                        item.section_id: {}
                        for item in batch
                    }
                    if parallel_budget is None
                    else parallel_budget.reserve_batch(
                        [item.section_id for item in batch],
                        llm_calls_per_section=parallel_llm_slice,
                        tokens_per_section=parallel_token_slice,
                    )
                )
                for section_plan in batch:
                    emit_workflow_custom_event(
                        "section_started",
                        node_name="execute_sections",
                        section_id=section_plan.section_id,
                        progress=(
                            (section_position[section_plan.section_id] - 1)
                            / max(section_count, 1)
                        ),
                        payload={
                            "section_title": section_plan.section_title,
                            "section_index": section_position[
                                section_plan.section_id
                            ],
                            "section_count": section_count,
                            "parallel_batch": True,
                        },
                    )

                def execute_isolated(
                    section_plan: SectionPlanSchema,
                ) -> ParallelSectionExecutionEnvelopeSchema:
                    local_state = shared_state.model_copy(deep=True)
                    before_state = local_state.model_copy(deep=True)
                    local_registry = DocumentCitationRegistry.from_citations(
                        base_citations
                    )
                    result = run_one(
                        section_plan,
                        state=local_state,
                        registry=local_registry,
                        previous_sections=previous,
                        emit_events=False,
                        parallel_isolated=True,
                        budget_policy_override=budget_reservations[
                            section_plan.section_id
                        ],
                    )
                    return ParallelSectionExecutionEnvelopeSchema(
                        section_id=section_plan.section_id,
                        result=result,
                        state_delta=self._parallel_state_delta(
                            before_state,
                            local_state,
                            section_id=section_plan.section_id,
                        ),
                    )

                try:
                    batch_results = (
                        self.section_parallel_execution_graph.execute_batch(
                            batch,
                            max_concurrency=self.section_max_concurrency,
                            execute=execute_isolated,
                        )
                    )
                except BaseException as exc:
                    if classify_failure(exc) == FailureCategory.CANCELLED:
                        for section_plan in batch:
                            emit_workflow_custom_event(
                                "section_cancelled",
                                node_name="execute_sections",
                                section_id=section_plan.section_id,
                                status=ExecutionStatus.CANCELLED.value,
                                payload={
                                    "section_title": section_plan.section_title,
                                    "parallel_batch": True,
                                },
                            )
                    raise
                for envelope in batch_results:
                    merged = self._merge_parallel_section_result(
                        envelope.result,
                        document_id=document_id,
                        citation_registry=citation_registry,
                    )
                    self._apply_parallel_state_delta(
                        shared_state,
                        envelope.state_delta,
                    )
                    results_by_id[merged.section.section_id] = merged
                    completed_ids.add(merged.section.section_id)
                    remaining.pop(merged.section.section_id)
                    self.state_writer.set_current_section(
                        shared_state,
                        section_id=merged.section.section_id,
                        section_title=merged.section.section_title,
                    )
                    emit_workflow_custom_event(
                        "section_generated",
                        node_name="execute_sections",
                        section_id=merged.section.section_id,
                        status=merged.section.status.value,
                        progress=(
                            section_position[merged.section.section_id]
                            / max(section_count, 1)
                        ),
                        payload={
                            "section_title": merged.section.section_title,
                            "content_chars": len(merged.section.content),
                            "parallel_batch": True,
                        },
                    )
                    emit_workflow_custom_event(
                        "section_completed",
                        node_name="execute_sections",
                        section_id=merged.section.section_id,
                        status=merged.section.status.value,
                        progress=(
                            section_position[merged.section.section_id]
                            / max(section_count, 1)
                        ),
                        payload={
                            "section_title": merged.section.section_title,
                            "section_index": section_position[
                                merged.section.section_id
                            ],
                            "section_count": section_count,
                            "parallel_batch": True,
                        },
                    )
            else:
                result = run_one(
                    first,
                    state=shared_state,
                    registry=citation_registry,
                    previous_sections=completed_sections(),
                    emit_events=True,
                    parallel_isolated=False,
                    budget_policy_override={},
                )
                results_by_id[first.section_id] = result
                completed_ids.add(first.section_id)
                remaining.pop(first.section_id)
                if pwa004 and (result.section.extra or {}).get(PWA004_REJECTION_KEY):
                    if result.canonical_section_source_binding is not None:
                        raise RuntimeError("PWA004 rejected Section must not claim a complete source")
                    pwa004_rejected_ids.add(first.section_id)

        checkpoint_current_execution()
        for section_plan in ordered_plans:
            section_result = results_by_id.get(section_plan.section_id)
            if section_result is None:
                continue  # Only known dependency-blocked Sections may be absent.
            sections.append(section_result.section)
            section_evidence_bundles.append(section_result.evidence)
            all_rag_outputs.extend(section_result.rag_outputs)
        shared_state.context_bundle.generation.metadata[
            "parallel_section_budget"
        ] = (
            parallel_budget.snapshot()
            if parallel_budget is not None
            else {
                "compatibility_allocator_active": False,
                "authority": "canonical_budget",
            }
        )
        section_states = dict(payload.section_states)
        for section_id, section_result in results_by_id.items():
            if section_result.section_state is not None:
                section_states[section_id] = section_result.section_state
        updates: dict[str, Any] = {
            "sections": sections,
            "section_states": section_states,
            "section_evidence": section_evidence_bundles,
            "section_execution_results": [
                results_by_id[item.section_id] for item in ordered_plans
                if item.section_id in results_by_id
            ],
            "all_citations": citation_registry.all(),
            "all_rag_outputs": all_rag_outputs,
        }
        if pwa004 and pwa004_rejected_ids:
            rejections = []
            for item in ordered_plans:
                result = results_by_id.get(item.section_id)
                if result is not None:
                    rejections.extend(dict(row, section_id=item.section_id,
                                           section_title=item.section_title)
                                      for row in (result.section.extra or {}).get(PWA004_REJECTION_KEY, []))
            if not rejections:
                raise RuntimeError("PWA004 missing rejection details for deferred terminal")
            explanations = [
                self._pwa004_rejection_explanation(payload=payload,
                    section_result=results_by_id[row["section_id"]], row=row)
                for row in rejections
            ]
            # Only the top-level derivative gains these fields. Native Section
            # checkpoints and their exact rejection/recovery receipts stay intact.
            updates["sections"] = [
                section.model_copy(update={"extra": {
                    **(section.extra or {}),
                    "pwa004_rejection_explanations": [
                        item for item in explanations if item["section_id"] == section.section_id
                    ],
                }}, deep=True) if section.section_id in pwa004_rejected_ids else section
                for section in sections
            ]
            summary = {
                "rejections": rejections,
                "rejection_explanations": explanations,
                "review_only_section_ids": sorted(pwa004_rejected_ids),
                "hard_dependency_blocked": [
                    pwa004_blocked[item.section_id] for item in ordered_plans
                    if item.section_id in pwa004_blocked
                ],
                "generated_non_review_only_section_ids": [
                    item.section_id for item in ordered_plans
                    if item.section_id in results_by_id and item.section_id not in pwa004_rejected_ids
                ],
                "accepted_section_ids": [
                    item.section_id for item in ordered_plans
                    if item.section_id in results_by_id
                    and results_by_id[item.section_id].canonical_section_source_binding is not None
                    and results_by_id[item.section_id].canonical_section_source_binding.workflow_status == "ACCEPTED"
                ],
                "formal_post_write_assessed": False,
                "formal_release_authorized": False,
            }
            validation = payload.document_validation.model_copy(
                update={"metadata": {
                    **payload.document_validation.metadata,
                    PWA004_SUMMARY_KEY: summary,
                }}, deep=True)
            updates["document_validation"] = validation
        return payload.model_copy(update=updates, deep=True)

    def document_verify(
        self,
        shared_state: SharedStateSchema,
        payload: SchemeGenerationGraphPayloadSchema,
    ) -> SchemeGenerationGraphPayloadSchema:
        """Run full cross-section verification over canonical production state."""

        del shared_state
        project_input = self._require(
            payload.project_input,
            field_name="project_input",
            node_name="document_verify",
        )
        verification = self.document_verifier.verify_mainline(
            sections=list(payload.sections),
            section_states=dict(payload.section_states),
            project_input=project_input,
            mode="full_logic",
        )
        return payload.model_copy(
            update={
                "document_verification": verification,
                "conflict_repair_plan": verification.repair_plan,
            },
            deep=True,
        )

    def repair_document_conflicts(
        self,
        shared_state: SharedStateSchema,
        payload: SchemeGenerationGraphPayloadSchema,
    ) -> SchemeGenerationGraphPayloadSchema:
        """Execute the repair plan strictly serially and fail closed when unresolved."""

        del shared_state
        verification = self._require(
            payload.document_verification,
            field_name="document_verification",
            node_name="repair_document_conflicts",
        )
        plan = payload.conflict_repair_plan or verification.repair_plan
        valid_sections = set(payload.section_states)

        def resolve(task):
            if not task.target_section_ids:
                return RepairTaskStatus.OBSOLETE
            if set(task.target_section_ids) - valid_sections:
                return RepairTaskStatus.FAILED
            # Round 7 owns the serial routing and audit trail, but does not
            # fabricate a successful rewrite. A future successful Section resume
            # may return RESOLVED; until then unresolved business conflicts block.
            return RepairTaskStatus.BLOCKED

        resolved_plan, history = self.conflict_repair_service.execute_serially(
            plan,
            resolver=resolve,
        )
        return payload.model_copy(
            update={
                "conflict_repair_plan": resolved_plan,
                "conflict_repair_history": history,
            },
            deep=True,
        )

    def incremental_document_reverify(
        self,
        shared_state: SharedStateSchema,
        payload: SchemeGenerationGraphPayloadSchema,
    ) -> SchemeGenerationGraphPayloadSchema:
        """Re-check only Sections affected by the serial repair plan."""

        if (payload.document_validation.metadata.get(PWA004_SUMMARY_KEY) or {}).get("rejections"):
            # The review-only path never claims to have repaired missing
            # canonical VerificationResults or hard-dependency-skipped scope.
            # Retain the true document_verify/conflict observations, with no
            # synthetic incremental PASS or cross-run repair dispatch.
            return payload
        del shared_state
        project_input = self._require(
            payload.project_input,
            field_name="project_input",
            node_name="incremental_document_reverify",
        )
        changed_section_ids = {
            section_id
            for item in payload.conflict_repair_history
            for section_id in list(item.get("target_section_ids") or [])
        }
        if changed_section_ids:
            verification = self.document_verifier.verify_mainline(
                sections=list(payload.sections),
                section_states=dict(payload.section_states),
                project_input=project_input,
                mode="incremental_logic",
                changed_section_ids=changed_section_ids,
            )
        else:
            verification = DocumentVerificationResultSchema(
                mode="incremental_logic",
                checked_section_ids=[],
                conflicts=[],
                repair_plan=self.document_verifier._repair_plan([]),
                passed=True,
            )
        return payload.model_copy(
            update={"incremental_document_verification": verification},
            deep=True,
        )

    def assemble_document(
        self,
        shared_state: SharedStateSchema,
        payload: SchemeGenerationGraphPayloadSchema,
    ) -> SchemeGenerationGraphPayloadSchema:
        """Deterministically aggregate sections and evidence into one draft."""

        project_input = self._require(
            payload.project_input,
            field_name="project_input",
            node_name="assemble_document",
        )
        document_id = self._require(
            payload.document_id,
            field_name="document_id",
            node_name="assemble_document",
        )
        started_at = self._require(
            payload.started_at,
            field_name="started_at",
            node_name="assemble_document",
        )
        document_bundle = self._require(
            payload.document_evidence_bundle,
            field_name="document_evidence_bundle",
            node_name="assemble_document",
        )
        assembly = self.document_assembler.assemble(
            DocumentAssemblyRequestSchema(
                task_id=shared_state.task_id,
                run_id=shared_state.run_id,
                document_id=document_id,
                document_title=(
                    project_input.output_schema.document_title
                    or "项目建设方案"
                ),
                required_sections=list(payload.required_sections),
                sections=list(payload.sections),
                retrieved_chunks=self._assembly_chunks_from_canonical_bundles(payload),
                citations=list(payload.all_citations),
                section_evidence=list(payload.section_evidence),
                document_evidence_bundle=document_bundle,
                citation_required_sections=list(
                    payload.required_evidence_sections
                ),
                created_at=started_at,
                updated_at=self.runtime_support.now_iso(),
            )
        )
        review = payload.document_validation.metadata.get(PWA004_SUMMARY_KEY)
        draft = assembly.draft
        if isinstance(review, dict) and review.get("rejections"):
            # Only a local, non-authoritative draft; preserve the original
            # assembled text and required/missing scope, with audit references.
            draft = draft.model_copy(update={
                "extra": {**dict(draft.extra or {}), PWA004_SUMMARY_KEY: review},
                "summary": ("未验收草稿：已保存 Writer 正文，存在拒绝的 Verifier "
                            "Candidate；请根据审核清单核对。无正式 DOCUMENT/Release。"),
            }, deep=True)
            assembly = assembly.model_copy(update={"draft": draft}, deep=True)
        return payload.model_copy(
            update={
                "assembly": assembly,
                "generated_draft": draft,
                "all_citations": list(assembly.citations),
            },
            deep=True,
        )

    def validate_document_assembly(
        self,
        shared_state: SharedStateSchema,
        payload: SchemeGenerationGraphPayloadSchema,
    ) -> SchemeGenerationGraphPayloadSchema:
        """Run deterministic validation over the canonical assembled draft."""

        del shared_state
        assembly = self._require(
            payload.assembly,
            field_name="assembly",
            node_name="validate_document_assembly",
        )
        validation = self.document_assembly_validator.validate_mainline(
            assembly=assembly,
            required_sections=list(payload.required_sections),
        )
        return payload.model_copy(
            update={"assembly_validation": validation},
            deep=True,
        )

    def final_artifact_review(
        self,
        shared_state: SharedStateSchema,
        payload: SchemeGenerationGraphPayloadSchema,
    ) -> SchemeGenerationGraphPayloadSchema:
        """Review the user-visible assembled draft before the final Hard Gate."""

        if (payload.document_validation.metadata.get(PWA004_SUMMARY_KEY) or {}).get("rejections"):
            # This is a non-authoritative local draft; do not project an
            # official final-artifact assessment for missing Claim Results.
            return payload
        del shared_state
        project_input = self._require(
            payload.project_input,
            field_name="project_input",
            node_name="final_artifact_review",
        )
        assembly = self._require(
            payload.assembly,
            field_name="assembly",
            node_name="final_artifact_review",
        )
        validation = self._require(
            payload.assembly_validation,
            field_name="assembly_validation",
            node_name="final_artifact_review",
        )
        old_names = list(
            project_input.metadata.get("old_project_names") or []
        )
        review = self.final_artifact_review_service.review_mainline(
            draft=assembly.draft,
            assembly_validation=validation,
            old_project_names=[str(item) for item in old_names],
        )
        return payload.model_copy(
            update={"final_artifact_review": review},
            deep=True,
        )

    def decide_document_version(
        self,
        shared_state: SharedStateSchema,
        payload: SchemeGenerationGraphPayloadSchema,
    ) -> SchemeGenerationGraphPayloadSchema:
        """Apply deterministic immutable version policy without publishing early."""

        del shared_state
        project_input = self._require(
            payload.project_input,
            field_name="project_input",
            node_name="decide_document_version",
        )
        previous_version = str(
            project_input.metadata.get("previous_document_version") or ""
        ).strip() or None
        raw_change_set = project_input.metadata.get("change_set") or {}
        change_set = ChangeSetSchema.model_validate(raw_change_set)
        decision = self.version_policy_service.decide(
            previous_version=previous_version,
            changes=change_set,
        )
        return payload.model_copy(
            update={
                "version_decision": decision,
                "document_version": decision.next_version,
            },
            deep=True,
        )


    @staticmethod
    def _document_failure_fingerprint(error):
        # Diagnostics/acknowledgements cannot change the original operation.
        return canonical_sha256(error.model_dump(mode="json", exclude={"details": {
            "lifecycle_composite_transition", "bc33_document_failure_intent",
            "r02_failure_diagnostics",
        }}))

    def _document_failure_intent(self, *, payload, before, sections, error):
        """Bind a native complete Section return set to one fixed execution.

        This runs only at the protected Target call boundary. Error.details is
        never evidence of origin. No lifecycle write is performed here.
        """
        from apps.enterprise_document.schemas.agent_rag_v1.lifecycle import AttemptLifecycleStatus
        from apps.enterprise_document.services.agent_rag_v1.post_write_composition import ArtifactCanonicalPayloadMaterializer
        from apps.enterprise_document.services.agent_rag_v1.run_finalization_currentness import (
            read_postwrite_finalization, validate_request,
        )
        post = self.target_post_write_composition
        lifecycle = self.target_workflow_runtime.lifecycle_service
        if (error.failure_category is FailureCategory.CANCELLED
                or error.details.get("c1_triggering_attempt_id") is not None):
            return None
        if before is None or (before.run.identity.task_id, before.run.identity.run_id) != (
                payload.task_id, payload.run_id):
            raise ValueError("Document failure lacks its original Run preimage")
        sources = tuple(payload.section_execution_results)
        ids = tuple(s.section.section_id for s in sources)
        orders = tuple(s.section.section_order for s in sources)
        if (not ids or len(set(ids)) != len(ids) or orders != tuple(sorted(set(orders)))
                or (payload.document_plan is not None
                    and ids != tuple(s.section_id for s in payload.document_plan.sections))):
            raise ValueError("Document failure Section set/order is not exact")
        # A native FINALIZING entry resumes only Document processing. Its
        # barrier must prove the supplied request before an unwrapped error can
        # be classified; a RUNNING Section-loop error still needs the full set.
        current = lifecycle.budget_preimage(payload.run_id)
        fixed = before
        if before.run.status is RunLifecycleStatus.FINALIZING:
            if current != before:
                raise ValueError("Document finalization execution changed")
            values = read_postwrite_finalization(post, before.run.run_finalization_barrier)
            validate_request(post, values, task_id=payload.task_id, run_id=payload.run_id,
                document_id=payload.document_id, project_input=payload.project_input,
                source_sections=sources)
            completed_ids = tuple(key for key, _ in values.sections)
        else:
            completed_ids = tuple(item.section_id for item in sections)
            if before.run.status is not RunLifecycleStatus.RUNNING or completed_ids != ids:
                raise ValueError("Document failure occurred before all Sections returned")
            for source, completed in zip(sources, sections):
                entry = post.entry_store.resolve_exact(completed.post_write_entry_ref)
                control = post.control_store.resolve_exact(completed.control_decision_ref)
                artifact = post.artifact_service.resolve_exact(completed.artifact_state_ref)
                identity = entry.identity
                content = ArtifactCanonicalPayloadMaterializer.business_payload(artifact.canonical_payload_ref)
                if ((identity.run_id, identity.document_id, identity.section_id) != (
                        payload.run_id, payload.document_id, source.section.section_id)
                        or identity.writer_execution_ref != source.writer_execution_ref
                        or identity.writer_invocation_ref != source.writer_generation_invocation_ref
                        or identity.artifact_state_ref != completed.artifact_state_ref
                        or control.input_manifest.post_write_entry_ref != completed.post_write_entry_ref
                        or control.delivery_gate_decision_ref != completed.delivery_gate_decision_ref
                        or control.fresh_currentness_proof != completed.currentness_proof
                        or content.get("content") != source.section.content
                        or content.get("section_title") != source.section.section_title):
                    raise ValueError("Document completed Section differs from canonical owners")
            if current.run.status is RunLifecycleStatus.FINALIZING:
                if (current.run.identity != before.run.identity
                        or current.run.lifecycle_revision != before.run.lifecycle_revision + 1
                        or current.current_children != before.current_children
                        or current.run.run_finalization_barrier is None):
                    raise ValueError("Document FINALIZING is not the original one-hop execution")
                values = read_postwrite_finalization(post, current.run.run_finalization_barrier)
                validate_request(post, values, task_id=payload.task_id, run_id=payload.run_id,
                    document_id=payload.document_id, project_input=payload.project_input,
                    source_sections=sources)
                if tuple(key for key, _ in values.sections) != ids:
                    raise ValueError("Document barrier selects another Section execution")
                fixed = current
            elif current != before:
                # A stale invocation never refreshes its CAS authority.
                raise ValueError("Document failure execution preimage changed")
        if completed_ids != ids:
            raise ValueError("Document completion membership differs")
        children = {row.identity.attempt_id: row for row in before.current_children}
        for source in sources:
            lineage = source.active_execution_lineage
            if lineage is None or source.writer_generation_invocation_ref is None:
                raise ValueError("Document source lacks exact Attempt/Writer lineage")
            attempt = children.get(lineage.attempt_id) or lifecycle.load_attempt(lineage.attempt_id)
            invocation = post.writer_execution_store.resolve_writer_generation_invocation(
                source.writer_generation_invocation_ref)
            if (lineage.run_id != payload.run_id
                    or lineage.attempt_scope_ref != "section:" + source.section.section_id
                    or (attempt.identity.run_id, attempt.identity.attempt_scope_ref,
                        attempt.identity.input_snapshot_version) != (
                        payload.run_id, lineage.attempt_scope_ref, lineage.input_snapshot_version)
                    or (attempt.identity.attempt_id in children
                        and attempt.lifecycle_revision != lineage.attempt_lifecycle_revision)
                    or (attempt.identity.attempt_id not in children
                        and attempt.status is not AttemptLifecycleStatus.COMPLETED)
                    or invocation.write_instance_id != lineage.attempt_id
                    or (invocation.writer_subject_ref.run_id,
                        invocation.writer_subject_ref.document_id,
                        invocation.writer_subject_ref.section_id) != (
                        payload.run_id, payload.document_id, source.section.section_id)):
                raise ValueError("Document source belongs to another Attempt execution")
        fingerprint = self._document_failure_fingerprint(error)
        return {
            "kind": "DOCUMENT_NONCANCEL_FAILURE", "task_id": payload.task_id,
            "run_id": payload.run_id, "document_id": payload.document_id,
            "before_run": fixed.run.model_dump(mode="json", round_trip=True),
            "expected_attempt_revisions": {
                row.identity.attempt_id: row.lifecycle_revision for row in fixed.current_children},
            "source_sha256": canonical_sha256([s.model_dump(mode="json") for s in sources]),
            "completed_section_ids": list(ids), "failure_sha256": fingerprint,
            "reason_ref": "document_failure:" + fingerprint,
        }

    def evaluate_document_gate(
        self,
        shared_state: SharedStateSchema,
        payload: SchemeGenerationGraphPayloadSchema,
    ) -> SchemeGenerationGraphPayloadSchema:
        """Run Target Post-write authorization or the unattached legacy gate."""

        review = payload.document_validation.metadata.get(PWA004_SUMMARY_KEY)
        if isinstance(review, dict) and review.get("rejections"):
            # Defer failure until all independent Sections were attempted and
            # the unverified Writer draft was assembled. Never feed missing
            # canonical Results into DOCUMENT/Gate/Control/Release.
            first = review["rejections"][0]
            trigger = first.get("attempt_id")
            if not isinstance(trigger, str) or not trigger:
                raise ValueError("PWA004 terminal lacks exact triggering Attempt")
            error = self.runtime_support.error(
                "VERIFIER_ADMISSION_INCOMPLETE",
                "Verifier Candidate was refused by M6; a review-only draft was retained.",
                node="evaluate_document_gate", retryable=False,
                failure_category=FailureCategory.VALIDATION, requires_human=True,
                user_message="部分陈述未完成正式验证；已保留可审核草稿，未正式发布。",
            ).model_copy(update={"details": {
                "c1_triggering_attempt_id": trigger,
                "pwa004_rejection_count": len(review["rejections"]),
                "pwa004_rejection_refs": [row["candidate_ref"] for row in review["rejections"]],
            }}, deep=True)
            raise SchemeGenerationStageFailure(error)

        if self.target_post_write_composition is not None:
            project_input = self._require(
                payload.project_input,
                field_name="project_input",
                node_name="evaluate_document_gate",
            )
            document_id = self._require(
                payload.document_id,
                field_name="document_id",
                node_name="evaluate_document_gate",
            )
            if not payload.section_execution_results:
                raise RuntimeError(
                    "Target Post-write requires exact Section execution results"
                )
            # Freeze the complete execution before the protected call. A read
            # failure cannot be repaired with a newer preimage after an error.
            failure_preimage = None
            try:
                if self.target_workflow_runtime is not None:
                    failure_preimage = self.target_workflow_runtime.lifecycle_service.budget_preimage(payload.run_id)
            except Exception:
                pass
            try:
                completion = self.target_post_write_composition.complete_target_post_write(
                    task_id=payload.task_id,
                    run_id=payload.run_id,
                    document_id=document_id,
                    project_input=project_input,
                    section_execution_results=tuple(payload.section_execution_results),
                    **({"budget_continuation": payload.budget_document_continuation,
                        "budget_materialization_ref": payload.budget_materialization_ref}
                       if payload.budget_document_continuation is not None else {}),
                )
            except Exception as caught:
                exc = (caught if isinstance(caught, TargetPostWriteInterrupted)
                       else TargetPostWriteInterrupted(caught, ()))
                refusal = getattr(exc.cause, "budget_failure_receipt", None)
                captured = getattr(exc.cause, "budget_materialization_ref", None)
                if refusal is not None and captured is not None:
                    try:
                        if not isinstance(refusal,dict):
                            raise ValueError("DOCUMENT budget refusal is not a native packet")
                        identity = {name: refusal[name] for name in ("task_id", "run_id", "section_id")}
                        verified = self.target_post_write_composition.citation_grounding.resolve_budget_refusal(
                            receipt_ref=refusal["native_denial_ref"],
                            expected_sha256=refusal["native_denial_sha256"], identity=identity)
                        original_refusal = BudgetDocumentRefusalSchema.model_validate(refusal)
                        verified_refusal = BudgetDocumentRefusalSchema.model_validate(verified)
                        if verified_refusal != original_refusal:
                            raise ValueError("DOCUMENT budget refusal differs from actual native receipt")
                        verified_materialization = ArtifactReferenceSchema.model_validate(captured)
                        if (verified_refusal.task_id != payload.task_id
                                or verified_refusal.run_id != payload.run_id
                                or verified_materialization.task_id != payload.task_id
                                or verified_materialization.run_id != payload.run_id):
                            raise ValueError("DOCUMENT budget refusal/materialization belongs to another Run")
                    except (CitationGroundingError, CanonicalBudgetIntegrityError, ArtifactIntegrityError,
                            ValueError, KeyError, FileNotFoundError):
                        # Invalid/unproven carrier is not upgraded. Continue the
                        # existing original diagnostic/technical owner path below.
                        pass
                    else:
                        error = declared_error(exc.cause, self.runtime_support,
                                               node="evaluate_document_gate")
                        raise BudgetDocumentAuthorizationWait(refusal=verified_refusal,
                            materialization_ref=verified_materialization, error=error) from exc
                error = declared_error(exc.cause, self.runtime_support, node="evaluate_document_gate")
                # Strip any externally supplied routing marker/receipt before
                # deriving an intent from this native invocation.
                details = {k: v for k, v in error.details.items() if k not in {
                    "bc33_document_failure_intent", "lifecycle_composite_transition",
                    "r02_failure_diagnostics",
                }}
                trigger = getattr(exc.cause, "c1_triggering_attempt_id", None)
                if trigger is not None and details.get("c1_triggering_attempt_id") is None:
                    details["c1_triggering_attempt_id"] = trigger
                error = error.model_copy(update={"details": details}, deep=True)
                intent = None
                diagnostics = []
                if error.details.get("c1_triggering_attempt_id") is None:
                    try:
                        intent = self._document_failure_intent(payload=payload,
                            before=failure_preimage, sections=exc.completed_sections, error=error)
                    except Exception as proof_error:
                        diagnostics.append({"operation": "document_failure_provenance",
                            "error_type": type(proof_error).__name__, "message": str(proof_error)})
                if intent is not None:
                    error = error.model_copy(update={"details": {
                        **error.details, "bc33_document_failure_intent": intent,
                    }}, deep=True)
                try:
                    outcome = project_post_write(self.target_post_write_composition,
                        payload=payload, sections=exc.completed_sections, completion=exc.completion, error=error)
                except Exception as projection_error:
                    diagnostics.append({"operation": "post_write_failure_projection",
                        "error_type": type(projection_error).__name__, "message": str(projection_error)})
                    outcome = project_terminal(task_id=payload.task_id, run_id=payload.run_id,
                        execution_status=ExecutionStatus.FAILED, phase="evaluate_document_gate",
                        error=error, unfinished=("document_validation_and_release",))
                if diagnostics:
                    error = error.model_copy(update={"details": {
                        **error.details, "r02_failure_diagnostics": diagnostics,
                    }}, deep=True)
                data = outcome.model_dump(mode="python", exclude={"result_id"})
                data["diagnostics"] = (*outcome.diagnostics, diagnostic(
                    "R02_DOCUMENT_FAILURE_INTENT" if intent is not None else "R02_DOCUMENT_FAILURE_UNPROVEN",
                    intent if intent is not None else diagnostics,
                    "Document failure provenance was checked at the native Stage boundary."))
                outcome = seal_terminal(data)
                raise TerminalOutcomeStageFailure(error, outcome, completion=exc.completion) from caught
            recovery_errors = ()
            if current_durable_recovery() is not None and self.target_workflow_runtime is not None:
                history = self.target_workflow_runtime.task_manager.get_task(payload.task_id)
                recovery_errors = tuple(event.error for event in history.events if event.error is not None)
            terminal_outcome = project_post_write(self.target_post_write_composition,
                payload=payload, sections=completion.materialization.section_results, completion=completion,
                recovery_errors=recovery_errors)
            if terminal_outcome.control_action.value == "BLOCK":
                return payload.model_copy(update={
                    "terminal_outcome": terminal_outcome,
                    "output_status": terminal_outcome.execution_status,
                    "artifact_route": "blocked",
                    "target_document_artifact_ref": completion.final_document.artifact_state_ref,
                    "target_post_write_control_decision_ref": completion.final_document.control_decision_ref,
                    "target_run_completion_assessment_ref": completion.run_completion_assessment_ref,
                })
            control = self.target_post_write_composition.control_store.resolve_exact(
                completion.final_document.control_decision_ref
            )
            action = control.enforcement_action.value
            if action not in {"OUTPUT", "OUTPUT_BEST_EFFORT"}:
                raise RuntimeError(
                    "Target Release returned an ineligible Control action"
                )
            quality_passed = action == "OUTPUT"
            unresolved = list(control.unresolved_reason_refs)
            compatibility_gate = HardGateResultSchema(
                passed=quality_passed,
                failures=[] if quality_passed else unresolved,
                warnings=[] if quality_passed else unresolved,
                checks={
                    "target_release_authorized": completion.published_release is not None,
                    "target_quality_passed": quality_passed,
                    "target_currentness_proven": True,
                    "target_output_conforming": True,
                    "target_run_completion_success": True,
                },
                metadata={
                    "authority": "compatibility_projection_only",
                    "target_control_action": action,
                    "target_control_decision_ref": (
                        completion.final_document.control_decision_ref.model_dump(
                            mode="json"
                        )
                    ),
                    "target_run_completion_assessment_ref": (
                        completion.run_completion_assessment_ref.model_dump(
                            mode="json"
                        )
                    ),
                    "target_release_id": (
                        completion.published_release.manifest.release_id
                        if completion.published_release is not None else None
                    ),
                    "target_release_mode": (
                        completion.published_release.manifest.release_mode.value
                        if completion.published_release is not None else None
                    ),
                    "target_publication_failure": completion.publication_failure,
                },
            )
            output_warnings = []
            if not quality_passed:
                output_warnings.append(
                    WarningSchema(
                        warning_code="TARGET_POST_WRITE_BEST_EFFORT",
                        message=(
                            "Target Post-write permits BEST_EFFORT review output; "
                            "legacy HardGate is a non-authoritative projection only."
                        ),
                        source=ErrorSourceSchema(
                            component=self.agent_name,
                            agent_name=self.agent_name,
                            step_name="evaluate_document_gate",
                        ),
                        details={
                            "unresolved_reason_refs": unresolved,
                            "release_id": (
                                completion.published_release.manifest.release_id
                                if completion.published_release is not None else None
                            ),
                        },
                        created_at=self.runtime_support.now_iso(),
                    )
                )
            if completion.publication_failure is not None:
                output_warnings.append(
                    WarningSchema(
                        warning_code="TARGET_RELEASE_PUBLICATION_FAILED",
                        message=(
                            "Formal publication failed after the Document was "
                            "committed. The review draft remains a separate output."
                        ),
                        source=ErrorSourceSchema(
                            component=self.agent_name,
                            agent_name=self.agent_name,
                            step_name="evaluate_document_gate",
                        ),
                        details=dict(completion.publication_failure),
                        created_at=self.runtime_support.now_iso(),
                    )
                )
            return payload.model_copy(
                update={
                    "hard_gate": compatibility_gate,
                    "terminal_outcome": terminal_outcome,
                    "output_status": (
                        ExecutionStatus.PARTIAL_SUCCESS
                        if completion.publication_failure is not None
                        else ExecutionStatus.SUCCESS
                        if quality_passed
                        else ExecutionStatus.PARTIAL_SUCCESS
                    ),
                    "output_error": None,
                    "output_warnings": output_warnings,
                    "artifact_route": "deliverable",
                    "target_document_artifact_ref": (
                        completion.final_document.artifact_state_ref
                    ),
                    "target_post_write_control_decision_ref": (
                        completion.final_document.control_decision_ref
                    ),
                    "target_run_completion_assessment_ref": (
                        completion.run_completion_assessment_ref
                    ),
                    "target_release_publication": completion.published_release,
                },
                deep=True,
            )

        project_input = self._require(
            payload.project_input,
            field_name="project_input",
            node_name="evaluate_document_gate",
        )
        options = self._require(
            payload.generation_options,
            field_name="generation_options",
            node_name="evaluate_document_gate",
        )
        assembly = self._require(
            payload.assembly,
            field_name="assembly",
            node_name="evaluate_document_gate",
        )
        hard_gate = evaluate_scheme_draft(
            assembly.draft,
            citation_required=options.need_citation,
            citation_required_sections=list(payload.required_evidence_sections),
            retrieved_chunk_ids=set(assembly.known_chunk_ids),
            project_input=project_input,
            canonical_citations=list(assembly.citations),
            tool_failed=False,
            key_fields_valid=bool(
                project_input.task_id
                and project_input.task_type
                and project_input.user_query
                and payload.required_sections
            ),
            output_schema_valid=bool(
                assembly.draft.full_text and assembly.draft.sections
            ),
            evidence_sufficient=assembly.evidence_requirements_satisfied,
            workflow_complete=True,
        )
        verification = (
            payload.incremental_document_verification
            or payload.document_verification
        )
        repair_tasks = list(
            (payload.conflict_repair_plan.repair_tasks)
            if payload.conflict_repair_plan is not None
            else []
        )
        repairs_resolved = all(
            task.status
            in {
                RepairTaskStatus.RESOLVED,
                RepairTaskStatus.OBSOLETE,
            }
            for task in repair_tasks
        )
        closure_checks = {
            "document_verification_passed": bool(
                verification is not None and verification.passed
            ),
            "conflict_repairs_resolved": repairs_resolved,
            "assembly_validation_passed": bool(
                payload.assembly_validation
                and payload.assembly_validation.passed
            ),
            "final_artifact_review_passed": bool(
                payload.final_artifact_review
                and payload.final_artifact_review.passed
            ),
            "document_version_decided": bool(payload.document_version),
        }
        closure_failures: list[str] = []
        if verification is None or not verification.passed:
            closure_failures.append("document_verification_failed")
        if not repairs_resolved:
            closure_failures.append("document_conflict_repairs_unresolved")
        if payload.assembly_validation is None or not payload.assembly_validation.passed:
            closure_failures.extend(
                list(payload.assembly_validation.failures)
                if payload.assembly_validation is not None
                else ["assembly_validation_missing"]
            )
        if payload.final_artifact_review is None or not payload.final_artifact_review.passed:
            closure_failures.extend(
                list(payload.final_artifact_review.issue_codes)
                if payload.final_artifact_review is not None
                else ["final_artifact_review_missing"]
            )
        if not payload.document_version:
            closure_failures.append("document_version_missing")
        if closure_failures:
            hard_gate = hard_gate.model_copy(
                update={
                    "passed": False,
                    "failures": list(
                        dict.fromkeys(
                            [*hard_gate.failures, *closure_failures]
                        )
                    ),
                    "checks": {
                        **dict(hard_gate.checks),
                        **closure_checks,
                    },
                    "metadata": {
                        **dict(hard_gate.metadata),
                        "document_closure_round": "round7",
                        "conflict_repair_history": list(
                            payload.conflict_repair_history
                        ),
                    },
                },
                deep=True,
            )
        else:
            hard_gate = hard_gate.model_copy(
                update={
                    "checks": {
                        **dict(hard_gate.checks),
                        **closure_checks,
                    },
                    "metadata": {
                        **dict(hard_gate.metadata),
                        "document_closure_round": "round7",
                    },
                },
                deep=True,
            )
        enforcement = self.gate_enforcement_controller.resolve(
            gate_passed=hard_gate.passed,
            failure_kind=GateFailureKind.BUSINESS,
        )
        hard_gate = hard_gate.model_copy(
            update={
                "metadata": {
                    **dict(hard_gate.metadata),
                    **enforcement.as_metadata(gate_id="G40/G41"),
                }
            },
            deep=True,
        )
        print(
            f"[HardGate] passed={hard_gate.passed} "
            f"enforcement={enforcement.action.value} "
            f"mode={enforcement.mode.value} "
            f"failures={hard_gate.failures} warnings={hard_gate.warnings}",
            flush=True,
        )
        has_partial_sections = any(
            section.status == ExecutionStatus.PARTIAL_SUCCESS
            for section in payload.sections
        )
        if not hard_gate.passed:
            status = ExecutionStatus.FAILED
        elif has_partial_sections or hard_gate.warnings:
            status = ExecutionStatus.PARTIAL_SUCCESS
        else:
            status = ExecutionStatus.SUCCESS
        output_error = None
        if not hard_gate.passed and enforcement.should_block:
            failure_category, retryable = self._hard_gate_failure_policy(
                hard_gate,
                evidence_requirements_satisfied=(
                    assembly.evidence_requirements_satisfied
                ),
            )
            output_error = self.runtime_support.error(
                "DOCUMENT_HARD_GATE_FAILED",
                "; ".join(hard_gate.failures),
                node="evaluate_document_gate",
                retryable=retryable,
                failure_category=failure_category,
                requires_human=True,
                user_message="文档未通过强制质量校验，不能标记为成功。",
            )
            self.state_writer.add_error(shared_state, output_error)
        output_warnings = [
            WarningSchema(
                warning_code="DOCUMENT_SOFT_GATE_WARNING",
                message=message,
                source=ErrorSourceSchema(
                    component=self.agent_name,
                    agent_name=self.agent_name,
                    step_name="evaluate_document_gate",
                ),
                created_at=self.runtime_support.now_iso(),
            )
            for message in hard_gate.warnings
        ]
        if not hard_gate.passed and enforcement.should_continue:
            output_warnings.append(
                WarningSchema(
                    warning_code="DOCUMENT_GATE_SHADOW_CONTINUE",
                    message="文档硬门禁真实失败；Shadow 模式仅取消阻断权，草稿继续保留用于链路闭环与评测。",
                    source=ErrorSourceSchema(
                        component=self.agent_name,
                        agent_name=self.agent_name,
                        step_name="evaluate_document_gate",
                    ),
                    details=enforcement.as_metadata(gate_id="G40/G41"),
                    created_at=self.runtime_support.now_iso(),
                )
            )
        return payload.model_copy(
            update={
                "hard_gate": hard_gate,
                "output_status": status,
                "output_error": output_error,
                "output_warnings": output_warnings,
                "artifact_route": (
                    "deliverable" if hard_gate.passed else "rejected"
                ),
            },
            deep=True,
        )

    def build_generation_report(
        self,
        shared_state: SharedStateSchema,
        payload: SchemeGenerationGraphPayloadSchema,
    ) -> SchemeGenerationGraphPayloadSchema:
        """Persist a typed business report in the existing top-level checkpoint."""

        hard_gate = self._require(
            payload.hard_gate,
            field_name="hard_gate",
            node_name="build_generation_report",
        )
        document_version = self._require(
            payload.document_version,
            field_name="document_version",
            node_name="build_generation_report",
        )
        source_versions: dict[str, str] = {}
        if payload.document_evidence_bundle is not None:
            lineage = payload.document_evidence_bundle.lineage.model_dump(
                mode="json", exclude_none=True
            )
            for key in (
                "index_version",
                "dataset_version",
                "embedding_version",
                "static_retrieval_spec_version",
            ):
                value = str(lineage.get(key) or "").strip()
                if value:
                    source_versions[key] = value
        budget_usage = {
            section.section_id: dict(
                (section.extra or {}).get("workflow_budget") or {}
            )
            for section in payload.sections
        }
        report = self.generation_report_service.build_mainline(
            task_id=shared_state.task_id,
            run_id=shared_state.run_id,
            sections=list(payload.sections),
            section_states=dict(payload.section_states),
            document_version=document_version,
            hard_gate=hard_gate,
            source_versions=source_versions,
            budget_usage={"sections": budget_usage},
        )
        return payload.model_copy(
            update={"generation_report": report},
            deep=True,
        )

    def _persist_output(
        self,
        shared_state: SharedStateSchema,
        payload: SchemeGenerationGraphPayloadSchema,
        *,
        rejected_artifact: RejectedDraftArtifactSchema | None,
        deliverable_final_result: DeliverableFinalResultSchema | None,
    ) -> SchemeGenerationGraphPayloadSchema:
        status_view = self._target_status_view(
            shared_state, payload, node_name="persist_artifact"
        )
        project_input = self._require(
            payload.project_input,
            field_name="project_input",
            node_name="persist_artifact",
        )
        scheme_input = self._require(
            payload.scheme_input,
            field_name="scheme_input",
            node_name="persist_artifact",
        )
        document_plan = self._require(
            payload.document_plan,
            field_name="document_plan",
            node_name="persist_artifact",
        )
        assembly = self._require(
            payload.assembly,
            field_name="assembly",
            node_name="persist_artifact",
        )
        hard_gate = self._require(
            payload.hard_gate,
            field_name="hard_gate",
            node_name="persist_artifact",
        )
        status = (
            ExecutionStatus(status_view["execution_status"])
            if status_view is not None
            else self._require(
                payload.output_status,
                field_name="output_status",
                node_name="persist_artifact",
            )
        )
        document_bundle = self._require(
            payload.document_evidence_bundle,
            field_name="document_evidence_bundle",
            node_name="persist_artifact",
        )
        output_rag_context, _, _ = project_evidence_bundle(document_bundle)
        document_rag_summary = next(
            (
                dict(item)
                for item in payload.all_rag_outputs
                if str(item.get("scope") or item.get("retrieval_scope") or "")
                == "document"
            ),
            {},
        )
        section_evidence_summaries = []
        for item in payload.section_evidence:
            bundle = item.evidence_bundle
            raw_status = bundle.assessment.status if bundle is not None else None
            section_evidence_summaries.append(
                {
                    "section_id": item.section_id,
                    "section_title": item.section_title,
                    "evidence_contract_sha256": item.evidence_contract_sha256,
                    "status": (
                        raw_status.value
                        if raw_status is not None and hasattr(raw_status, "value")
                        else str(raw_status or "not_assessed")
                    ),
                    "citation_count": len(bundle.citations) if bundle is not None else 0,
                    "selected_evidence_count": (
                        len(bundle.selected_evidence_ids) if bundle is not None else 0
                    ),
                    "tool_call_ids": list(item.tool_call_ids),
                    "recovery_count": item.recovery_count,
                }
            )
        status_fields = (
            {
                "execution_status": (
                    "success"
                    if hard_gate.passed or self.gate_enforcement_controller.is_shadow
                    else "blocked"
                ),
                "business_quality_status": "passed" if hard_gate.passed else "failed",
                "gate_enforcement_status": (
                    "continued"
                    if hard_gate.passed or self.gate_enforcement_controller.is_shadow
                    else "blocked"
                ),
                "release_status": (
                    "published" if payload.release_publication is not None else
                    "shadow_only"
                    if self.gate_enforcement_controller.is_shadow and not hard_gate.passed
                    else "rejected"
                ),
            }
            if status_view is None else status_view
        )
        output = SchemeWriterOutputSchema(
            task_id=shared_state.task_id,
            run_id=shared_state.run_id,
            status=status,
            document_plan=document_plan,
            generated_draft=assembly.draft,
            rejected_artifact=rejected_artifact,
            deliverable_final_result=deliverable_final_result,
            rag_context=output_rag_context,
            retrieved_chunks=list(assembly.retrieved_chunks),
            citations=list(assembly.citations),
            section_evidence=section_evidence_summaries,
            hard_gate=hard_gate,
            warnings=list(payload.output_warnings),
            error=payload.output_error,
            need_human_review=(deliverable_final_result is None),
            extra={
                **status_fields,
                "gate_enforcement_mode": self.gate_enforcement_controller.mode.value,
                "would_block_in_strict": not hard_gate.passed,
                "final_artifact_generated": bool(assembly.draft.full_text),
                "scheme_writer_input": scheme_input.model_dump(),
                "document_plan": document_plan.model_dump(),
                "rag_tool_output": document_rag_summary,
                "rag_tool_outputs": list(payload.all_rag_outputs),
                "section_evidence": section_evidence_summaries,
                "section_aware_retrieval_enabled": (
                    payload.section_retrieval_enabled
                ),
                "corrective_section_retrieval_enabled": (
                    payload.corrective_retrieval_enabled
                ),
                "section_count": len(payload.sections),
                "citation_binding_count": len(assembly.citation_bindings),
                "partial_section_count": sum(
                    1
                    for section in payload.sections
                    if section.status == ExecutionStatus.PARTIAL_SUCCESS
                ),
                "semantic_gate_enabled": self.enable_semantic_gate,
                "semantic_evidence_sufficient": (
                    assembly.semantic_evidence_sufficient
                ),
                "evidence_requirements_satisfied": (
                    assembly.evidence_requirements_satisfied
                ),
                "section_evidence_requirements": [
                    item.model_dump()
                    for item in assembly.section_evidence_requirements
                ],
                "release_publication": (
                    payload.release_publication.model_dump(mode="json")
                    if payload.release_publication is not None
                    else None
                ),
                "target_release_publication": (
                    payload.target_release_publication.model_dump(mode="json")
                    if payload.target_release_publication is not None
                    else None
                ),
                "target_post_write_control_decision_ref": (
                    payload.target_post_write_control_decision_ref.model_dump(
                        mode="json"
                    )
                    if payload.target_post_write_control_decision_ref is not None
                    else None
                ),
                "target_run_completion_assessment_ref": (
                    payload.target_run_completion_assessment_ref.model_dump(
                        mode="json"
                    )
                    if payload.target_run_completion_assessment_ref is not None
                    else None
                ),
                "document_closure": {
                    "document_verification": (
                        payload.document_verification.model_dump(mode="json")
                        if payload.document_verification is not None
                        else None
                    ),
                    "incremental_document_verification": (
                        payload.incremental_document_verification.model_dump(
                            mode="json"
                        )
                        if payload.incremental_document_verification is not None
                        else None
                    ),
                    "conflict_repair_history": list(
                        payload.conflict_repair_history
                    ),
                    "assembly_validation": (
                        payload.assembly_validation.model_dump(mode="json")
                        if payload.assembly_validation is not None
                        else None
                    ),
                    "final_artifact_review": (
                        payload.final_artifact_review.model_dump(mode="json")
                        if payload.final_artifact_review is not None
                        else None
                    ),
                    "document_version": payload.document_version,
                    "version_decision": (
                        payload.version_decision.model_dump(mode="json")
                        if payload.version_decision is not None
                        else None
                    ),
                    "generation_report": (
                        payload.generation_report.model_dump(mode="json")
                        if payload.generation_report is not None
                        else None
                    ),
                },
            },
        )
        enriched_rag_output = {
            "document": document_rag_summary,
            "section_evidence": section_evidence_summaries,
            "rag_tool_outputs": list(payload.all_rag_outputs),
        }
        self.state_writer.set_scheme_outputs(
            shared_state,
            scheme_writer_input=scheme_input.model_dump(mode="json"),
            scheme_writer_output=output.model_dump(mode="json"),
            rag_tool_output=enriched_rag_output,
        )
        self.state_writer.set_scheme_artifacts(
            shared_state,
            generated_draft=assembly.draft.model_dump(),
            rejected_artifact=(
                rejected_artifact.model_dump(mode="json")
                if rejected_artifact is not None
                else None
            ),
            deliverable_final_result=(
                deliverable_final_result.model_dump(mode="json")
                if deliverable_final_result is not None
                else None
            ),
        )
        if not payload.capture_completed:
            self.capture_service.capture(
                shared_state,
                scheme_input,
                output,
                enriched_rag_output,
            )
        return payload.model_copy(
            update={
                "scheme_writer_output": output,
                "generated_draft": assembly.draft,
                "rejected_artifact": rejected_artifact,
                "deliverable_final_result": deliverable_final_result,
                "release_publication": payload.release_publication,
                "capture_completed": True,
            },
            deep=True,
        )

    def persist_deliverable(
        self,
        shared_state: SharedStateSchema,
        payload: SchemeGenerationGraphPayloadSchema,
    ) -> SchemeGenerationGraphPayloadSchema:
        """Publish the only deliverable lifecycle after a passed hard gate."""

        # An attached Target completion has already checked the publication
        # owner. A missing Target outcome must never enter legacy publication.
        self._target_status_view(shared_state, payload, node_name="persist_deliverable")

        project_input = self._require(
            payload.project_input,
            field_name="project_input",
            node_name="persist_deliverable",
        )
        assembly = self._require(
            payload.assembly,
            field_name="assembly",
            node_name="persist_deliverable",
        )
        hard_gate = self._require(
            payload.hard_gate,
            field_name="hard_gate",
            node_name="persist_deliverable",
        )
        output_status = self._require(
            payload.output_status,
            field_name="output_status",
            node_name="persist_deliverable",
        )
        if self.target_post_write_composition is not None:
            # Target publication is already durable and verified.  The legacy
            # VersionDecision/GenerationReport/Release surfaces are compatibility
            # only and are not required on the Target live path.
            release_publication = None
            published_payload = payload
        else:
            generation_report = self._require(
                payload.generation_report,
                field_name="generation_report",
                node_name="persist_deliverable",
            )
            version_decision = self._require(
                payload.version_decision,
                field_name="version_decision",
                node_name="persist_deliverable",
            )
            document_version = self._require(
                payload.document_version,
                field_name="document_version",
                node_name="persist_deliverable",
            )
            release_publication = self.release_publication_service.publish(
                task_id=shared_state.task_id,
                run_id=shared_state.run_id,
                document_version=document_version,
                draft=assembly.draft,
                output_format=project_input.output_schema.output_format,
                hard_gate=hard_gate,
                generation_report=generation_report,
                version_decision=version_decision,
                output_status=output_status,
            )
            published_payload = payload.model_copy(
                update={"release_publication": release_publication},
                deep=True,
            )
        deliverable_final_result = DeliverableFinalResultSchema(
            draft=assembly.draft,
            output_format=project_input.output_schema.output_format,
            release_publication=release_publication,
        )
        return self._persist_output(
            shared_state,
            published_payload,
            rejected_artifact=None,
            deliverable_final_result=deliverable_final_result,
        )

    def persist_rejected(
        self,
        shared_state: SharedStateSchema,
        payload: SchemeGenerationGraphPayloadSchema,
    ) -> SchemeGenerationGraphPayloadSchema:
        """Publish a rejected draft without exposing a business deliverable."""

        assembly = self._require(
            payload.assembly,
            field_name="assembly",
            node_name="persist_rejected",
        )
        hard_gate = self._require(
            payload.hard_gate,
            field_name="hard_gate",
            node_name="persist_rejected",
        )
        rejected_artifact = RejectedDraftArtifactSchema(
            draft=assembly.draft,
            rejection_code="DOCUMENT_HARD_GATE_FAILED",
            rejection_reasons=list(hard_gate.failures),
            hard_gate=hard_gate,
        )
        return self._persist_output(
            shared_state,
            payload,
            rejected_artifact=rejected_artifact,
            deliverable_final_result=None,
        )

    def finalize_workflow_result(
        self,
        shared_state: SharedStateSchema,
        payload: SchemeGenerationGraphPayloadSchema,
    ) -> SchemeGenerationGraphPayloadSchema:
        """Build the one terminal AgentResult for a business-complete workflow."""

        status_view = self._target_status_view(
            shared_state, payload, node_name="finalize_workflow_result"
        )
        if payload.artifact_route == "blocked" and payload.terminal_outcome is not None:
            outcome = payload.terminal_outcome
            result = AgentResultSchema(result_id=f"result_{shared_state.run_id}_scheme",
                task_id=shared_state.task_id, run_id=shared_state.run_id,
                agent_name=self.agent_name, agent_type=self.agent_type,
                status=outcome.execution_status, result_type="scheme_writer_output",
                result={"terminal_outcome": outcome.model_dump(mode="json"),
                    "generated_draft": None, "rejected_artifact": None,
                    "deliverable_final_result": None, "release_publication": None,
                    "target_release_publication": None},
                started_at=payload.started_at, finished_at=self.runtime_support.now_iso(),
                metadata={**status_view, "gate_enforcement_action": status_view["control_action"]}
                    if status_view is not None else {})
            shared_state.status = outcome.execution_status
            return payload.model_copy(update={"terminal_agent_result": result, "terminal_error": None})

        output = self._require(
            payload.scheme_writer_output,
            field_name="scheme_writer_output",
            node_name="finalize_workflow_result",
        )
        assembly = self._require(
            payload.assembly,
            field_name="assembly",
            node_name="finalize_workflow_result",
        )
        if status_view is not None:
            execution_status = ExecutionStatus(status_view["execution_status"])
            terminal_error = payload.output_error
            status_fields = {
                **status_view,
                "gate_enforcement_action": status_view["control_action"],
            }
        else:
            target_published = payload.target_release_publication is not None
            shadow_continued = bool(
                not target_published
                and payload.hard_gate is not None
                and not payload.hard_gate.passed
                and self.gate_enforcement_controller.is_shadow
            )
            execution_status = (
                output.status
                if target_published
                else (ExecutionStatus.SUCCESS if shadow_continued else output.status)
            )
            terminal_error = (
                None if target_published or shadow_continued else payload.output_error
            )
            status_fields = {
                "execution_status": execution_status.value,
                "business_quality_status": (
                    "passed"
                    if payload.hard_gate and payload.hard_gate.passed
                    else "failed"
                ),
                "gate_enforcement_action": (
                    payload.target_release_publication.manifest.enforcement_action_passthrough.value
                    if payload.target_release_publication is not None
                    else (
                        "continue" if shadow_continued else (
                            "continue"
                            if payload.hard_gate and payload.hard_gate.passed
                            else "block"
                        )
                    )
                ),
                "release_status": (
                    "published"
                    if payload.target_release_publication is not None
                    else (
                        "shadow_only"
                        if shadow_continued
                        else (
                            "published"
                            if payload.release_publication is not None
                            else "rejected"
                        )
                    )
                ),
            }
        review_draft_reference = None
        review_draft_issue = None
        if status_view is not None and payload.target_document_artifact_ref is not None:
            from apps.enterprise_document.services.agent_rag_v1.post_write_review_draft import (
                ReviewDraftUnavailable,
                TargetReviewDraftLocalExporter,
                TargetReviewDraftReader,
            )
            try:
                verified_draft = TargetReviewDraftReader(
                    self.target_post_write_composition
                ).read(
                    document_artifact_ref=payload.target_document_artifact_ref,
                    control_decision_ref=payload.target_post_write_control_decision_ref,
                    task_id=shared_state.task_id,
                    run_id=shared_state.run_id,
                )
                review_draft_reference = {
                    **verified_draft.as_result_reference(),
                    **TargetReviewDraftLocalExporter(
                        self.target_post_write_composition.runs_dir
                    ).export(
                        draft=verified_draft,
                        task_id=shared_state.task_id,
                        run_id=shared_state.run_id,
                    ),
                }
            except ReviewDraftUnavailable as exc:
                # A partial/stale/withheld draft is not presented as complete.
                # Preserve the existing terminal Run and its exact quality truth.
                review_draft_issue = str(exc)
        result = AgentResultSchema(
            result_id=f"result_{shared_state.run_id}_scheme",
            task_id=shared_state.task_id,
            run_id=shared_state.run_id,
            agent_name=self.agent_name,
            agent_type=self.agent_type,
            status=execution_status,
            result_type="scheme_writer_output",
            result={
                "terminal_outcome": payload.terminal_outcome.model_dump(mode="json") if payload.terminal_outcome else None,
                "scheme_writer_output": output.model_dump(mode="json"),
                "generated_draft": (
                    payload.generated_draft.model_dump()
                    if payload.generated_draft is not None
                    else None
                ),
                "rejected_artifact": (
                    payload.rejected_artifact.model_dump()
                    if payload.rejected_artifact is not None
                    else None
                ),
                "deliverable_final_result": (
                    payload.deliverable_final_result.model_dump(mode="json")
                    if payload.deliverable_final_result is not None
                    else None
                ),
                "release_publication": (
                    payload.release_publication.model_dump(mode="json")
                    if payload.release_publication is not None
                    else None
                ),
                "target_release_publication": (
                    payload.target_release_publication.model_dump(mode="json")
                    if payload.target_release_publication is not None
                    else None
                ),
                # Read-only review access: not a new Release, Gate or quality truth.
                "target_review_draft": review_draft_reference,
            },
            citations=list(assembly.citations),
            warnings=list(payload.output_warnings),
            error=terminal_error,
            error_message=(
                terminal_error.message if terminal_error is not None else None
            ),
            started_at=payload.started_at,
            finished_at=self.runtime_support.now_iso(),
            need_human_review=output.need_human_review,
            metadata={
                "output_schema": output.schema_version,
                "hard_gate_passed": bool(
                    payload.hard_gate and payload.hard_gate.passed
                ),
                **status_fields,
                "gate_enforcement_mode": self.gate_enforcement_controller.mode.value,
                "would_block_in_strict": bool(
                    payload.hard_gate and not payload.hard_gate.passed
                ),
                "final_artifact_generated": bool(assembly.draft.full_text),
                "review_draft_available": review_draft_reference is not None,
                "review_draft_unavailable_reason": review_draft_issue,
                "section_count": len(payload.sections),
                "citation_binding_count": len(assembly.citation_bindings),
                "semantic_evidence_sufficient": (
                    assembly.semantic_evidence_sufficient
                ),
                "evidence_requirements_satisfied": (
                    assembly.evidence_requirements_satisfied
                ),
                "section_evidence_requirements": [
                    item.model_dump()
                    for item in assembly.section_evidence_requirements
                ],
                "release_id": (
                    payload.target_release_publication.manifest.release_id
                    if payload.target_release_publication is not None
                    else (
                        payload.release_publication.manifest.release_id
                        if payload.release_publication is not None
                        else None
                    )
                ),
                "release_manifest_ref": (
                    payload.target_release_publication.manifest_artifact.artifact_ref
                    if payload.target_release_publication is not None
                    else (
                        payload.release_publication.manifest_artifact.artifact_ref
                        if payload.release_publication is not None
                        else None
                    )
                ),
                "release_verified_after_write": bool(
                    (
                        payload.target_release_publication
                        and payload.target_release_publication.verified_after_write
                    )
                    or (
                        payload.release_publication
                        and payload.release_publication.verified_after_write
                    )
                ),
            },
        )
        shared_state.status = result.status
        self.state_writer.set_final_result(shared_state, result.result)
        return payload.model_copy(
            update={
                "terminal_agent_result": result,
                "terminal_error": result.error,
            },
            deep=True,
        )

    def _terminalize_cancelled_run(
        self,
        *,
        run_id: str,
        failed_node: str,
    ):
        """Close the canonical Run for an explicit terminal cancellation."""

        runtime = self.target_workflow_runtime
        if runtime is None:
            return None
        lifecycle = runtime.lifecycle_service
        try:
            current = lifecycle.load_run_lifecycle(run_id)
        except KeyError:
            # Compatibility/isolated flows may not own a canonical SIF-01 Run.
            # Never manufacture lifecycle truth merely to project cancellation.
            return None
        if current.status is RunLifecycleStatus.CANCELLED:
            return current
        if current.status in {
            RunLifecycleStatus.SUCCESS,
            RunLifecycleStatus.PARTIAL_SUCCESS,
            RunLifecycleStatus.FAILED,
        }:
            raise RuntimeError(
                "cancelled workflow conflicts with an already terminal canonical Run"
            )
        try:
            return lifecycle.transition_run(
                run_id,
                current.lifecycle_revision,
                RunLifecycleStatus.CANCELLED,
                reason_ref=f"cancelled:{safe_identifier(failed_node)}",
            )
        except LifecycleRevisionConflict:
            current = lifecycle.load_run_lifecycle(run_id)
            if current.status is not RunLifecycleStatus.CANCELLED:
                raise
            return current

    def _terminalize_noncancel_failed_run(self, *, payload, error, failed_node):
        runtime = self.target_workflow_runtime
        if runtime is None:
            return None
        lifecycle = runtime.lifecycle_service
        intent = error.details.get("bc33_document_failure_intent")
        if intent is not None:
            # The checkpoint retains this exact native intent and its sealed
            # Stage diagnostic; a bare Error.details marker grants no authority.
            before = RunLifecycleRecordSchema.model_validate(intent["before_run"])
            expected = {
                "kind", "task_id", "run_id", "document_id", "before_run",
                "expected_attempt_revisions", "source_sha256", "completed_section_ids",
                "failure_sha256", "reason_ref",
            }
            if (set(intent) != expected or failed_node != "evaluate_document_gate"
                    or intent["kind"] != "DOCUMENT_NONCANCEL_FAILURE"
                    or (intent["task_id"], intent["run_id"], intent["document_id"]) != (
                        payload.task_id, payload.run_id, payload.document_id)
                    or (before.identity.task_id, before.identity.run_id) != (payload.task_id, payload.run_id)
                    or error.details.get("c1_triggering_attempt_id") is not None
                    or intent["failure_sha256"] != self._document_failure_fingerprint(error)
                    or intent["reason_ref"] != "document_failure:" + intent["failure_sha256"]
                    or intent["source_sha256"] != canonical_sha256([
                        s.model_dump(mode="json") for s in payload.section_execution_results])
                    or intent["completed_section_ids"] != [
                        s.section.section_id for s in payload.section_execution_results]
                    or payload.terminal_outcome is None
                    or not any(item.code == "R02_DOCUMENT_FAILURE_INTENT"
                        and item.diagnostic_ref == "sha256:" + canonical_sha256(intent)
                        for item in payload.terminal_outcome.diagnostics)):
                raise LifecycleIntegrityError("Document failure lacks exact native execution provenance")
            return lifecycle.terminalize_document_failure(
                payload.run_id, before.lifecycle_revision, RunLifecycleStatus.FAILED,
                intent["reason_ref"], expected_attempt_revisions=intent["expected_attempt_revisions"],
                operation_key=canonical_sha256(intent),
            )
        if (payload.terminal_outcome is not None and any(
                item.code == "R02_DOCUMENT_FAILURE_UNPROVEN"
                for item in payload.terminal_outcome.diagnostics)
                and error.details.get("c1_triggering_attempt_id") is None):
            raise LifecycleIntegrityError("Document/Section failure provenance is unproven")
        try:
            current = lifecycle.load_run_lifecycle(payload.run_id)
        except KeyError:
            return None
        if current.status in {RunLifecycleStatus.SUCCESS, RunLifecycleStatus.PARTIAL_SUCCESS}:
            # A late outer error projects the exact already sealed canonical Run.
            # Its committed success cannot authorize a new failure transition.
            return current
        reason = (f"noncancel_failure:{safe_identifier(failed_node)}:"
                  f"{canonical_sha256(error.model_dump(mode='json', exclude={'details': {'lifecycle_composite_transition', 'r02_failure_diagnostics'}}))}")
        target = (RunLifecycleStatus.RETRYABLE_FAILED
                  if error.retryable and current.status is RunLifecycleStatus.RUNNING
                  else RunLifecycleStatus.FAILED)
        revision = current.lifecycle_revision
        if current.status in {RunLifecycleStatus.FAILED, RunLifecycleStatus.RETRYABLE_FAILED}:
            # Only the exact prior composite request can replay this acknowledgement.
            # A legacy v2 failure or a changed reason cannot authorize a new mutation.
            target = current.status
            revision -= 1
        return lifecycle.terminalize_noncancel_failure(
            payload.run_id, revision, target, reason,
            triggering_attempt_id=error.details.get("c1_triggering_attempt_id"),
        )

    @staticmethod
    def _bind_cancelled_run_status(outcome, run_record):
        """Reseal an existing terminal projection with canonical cancellation."""

        data = outcome.model_dump(mode="python", exclude={"result_id"})
        data["execution_status"] = ExecutionStatus.CANCELLED
        data["run_status"] = run_record.status
        data["status"] = FailureCategory.CANCELLED
        return seal_terminal(data)

    def finalize_workflow_failure(
        self,
        shared_state: SharedStateSchema,
        payload: SchemeGenerationGraphPayloadSchema,
    ) -> SchemeGenerationGraphPayloadSchema:
        """Build a technical-failure result without fabricating an artifact route."""

        error = payload.terminal_error or self.runtime_support.error(
            "SCHEME_WRITER_FAILED",
            "scheme generation graph terminated without a structured error",
            node=payload.failed_business_node or "finalize_workflow_failure",
            failure_category=FailureCategory.UNKNOWN,
            requires_human=True,
            user_message="方案生成主链路发生错误。",
        )
        cancelled = error.failure_category is FailureCategory.CANCELLED
        status = (
            ExecutionStatus.CANCELLED
            if cancelled
            else (
                ExecutionStatus.RETRYABLE_FAILED
                if error.retryable
                else ExecutionStatus.FAILED
            )
        )
        failed_node = payload.failed_business_node or "workflow"
        # Preserve the first error even when cleanup or its presentation fails.
        raw_diagnostics = error.details.get("r02_failure_diagnostics")
        cleanup_diagnostics = [
            dict(item) for item in raw_diagnostics
            if isinstance(item, dict) and set(item) == {"operation", "error_type", "message"}
            and all(isinstance(item[key], str) for key in item)
        ] if isinstance(raw_diagnostics, list) else []
        error = error.model_copy(update={"details": {
            k: v for k, v in error.details.items() if k != "lifecycle_composite_transition"
        }}, deep=True)
        try:
            source_mode = self._target_status_source_mode(
                shared_state, payload, node_name="Target failure status",
            )
        except Exception as source_error:
            if cancelled:
                raise
            cleanup_diagnostics.append({"operation": "failure_status_projection",
                "error_type": type(source_error).__name__, "message": str(source_error)})
            source_mode = "legacy_or_early"
        lifecycle_result = None
        if not cancelled:
            try:
                lifecycle_result = self._terminalize_noncancel_failed_run(
                    payload=payload, error=error, failed_node=failed_node,
                )
            except Exception as cleanup_error:
                cleanup_diagnostics.append({"operation": "lifecycle_cleanup",
                    "error_type": type(cleanup_error).__name__, "message": str(cleanup_error)})
        transition = (lifecycle_result
                      if isinstance(lifecycle_result, LifecycleCompositeTransition) else None)
        terminal_run = (
            self._terminalize_cancelled_run(
                run_id=payload.run_id,
                failed_node=failed_node,
            )
            if cancelled
            else (lifecycle_result if isinstance(lifecycle_result, RunLifecycleRecordSchema)
                  else transition.resulting_run if transition is not None else None)
        )
        if not cancelled and self.target_workflow_runtime is not None:
            try:
                # A replay acknowledgement is historical; read actual state even
                # if append/fsync raised after the transaction committed.
                terminal_run = self.target_workflow_runtime.lifecycle_service.load_run_lifecycle(payload.run_id)
            except Exception as read_error:
                terminal_run = None
                cleanup_diagnostics.append({"operation": "canonical_run_read",
                    "error_type": type(read_error).__name__, "message": str(read_error)})
        lifecycle_binding = None
        if transition is not None:
            # Transport the owner result through existing channels. Input details
            # never supply lifecycle or retry authority, including on replay.
            lifecycle_binding = {
                "event_ref": transition.event_ref,
                "event_sha256": transition.event_sha256,
                "operation_key": transition.operation_key,
                "resulting_run": transition.resulting_run.model_dump(mode="json", round_trip=True),
                "resulting_attempts": [row.model_dump(mode="json", round_trip=True)
                                       for row in transition.resulting_attempts],
            }
            error = error.model_copy(update={"details": {
                **error.details, "lifecycle_composite_transition": lifecycle_binding,
            }}, deep=True)
        if cleanup_diagnostics:
            error = error.model_copy(update={"details": {
                **error.details, "r02_failure_diagnostics": cleanup_diagnostics,
            }}, deep=True)
        retained_outcome = payload.terminal_outcome
        if any(d["operation"] == "failure_status_projection" for d in cleanup_diagnostics):
            retained_outcome = None
        outcome = retained_outcome or project_terminal(
            task_id=payload.task_id,
            run_id=payload.run_id,
            execution_status=status,
            run_status=terminal_run.status if terminal_run is not None else None,
            phase=failed_node,
            error=error,
            completed=tuple(payload.executed_nodes),
            unfinished=(failed_node,),
        )
        if retained_outcome is None and payload.terminal_outcome is not None:
            # A safe presentation fallback must not lose the native routing
            # binding needed by the same checkpoint on replay.
            bindings = tuple(item for item in payload.terminal_outcome.diagnostics
                if item.code in {"R02_DOCUMENT_FAILURE_INTENT", "R02_DOCUMENT_FAILURE_UNPROVEN"})
            if bindings:
                data = outcome.model_dump(mode="python", exclude={"result_id"})
                data["diagnostics"] = (*outcome.diagnostics, *bindings)
                outcome = seal_terminal(data)
        if cancelled and terminal_run is not None and payload.terminal_outcome is not None:
            outcome = self._bind_cancelled_run_status(outcome, terminal_run)
        if payload.terminal_outcome is not None and (
            outcome.failure_category is None or outcome.phase != (payload.failed_business_node or "workflow")
        ):
            outcome = project_later_failure(outcome, error=error, phase=payload.failed_business_node or "workflow")
        canonical_status = terminal_run.status if terminal_run is not None else None
        if not cancelled and outcome.run_status is not canonical_status:
            data = outcome.model_dump(mode="python", exclude={"result_id"})
            data["run_status"] = canonical_status
            outcome = seal_terminal(data)
        if cleanup_diagnostics:
            data = outcome.model_dump(mode="python", exclude={"result_id"})
            data["diagnostics"] = (*(
                item for item in outcome.diagnostics if item.code != "R02_FAILURE_CLEANUP_DIAGNOSTIC"
            ), diagnostic("R02_FAILURE_CLEANUP_DIAGNOSTIC", cleanup_diagnostics,
                "The original failure is retained; cleanup or diagnostic reads could not complete."))
            outcome = seal_terminal(data)
        if lifecycle_binding is not None:
            data = outcome.model_dump(mode="python", exclude={"result_id"})
            data["artifacts"] = outcome.artifacts
            data["diagnostics"] = (*(
                item for item in outcome.diagnostics
                if item.code != "LIFECYCLE_COMPOSITE_TRANSITION"
            ), diagnostic("LIFECYCLE_COMPOSITE_TRANSITION", lifecycle_binding,
                "The exact committed lifecycle transition is retained."))
            outcome = seal_terminal(data)
        status_view = project_terminal_status(
            outcome,
            source_mode=source_mode,
            expected_task_id=shared_state.task_id,
            expected_run_id=shared_state.run_id,
            expected_phase=safe_identifier(payload.failed_business_node or "workflow"),
        )
        status = ExecutionStatus(status_view["execution_status"])
        if error not in shared_state.errors:
            self.state_writer.add_error(shared_state, error)
        self.state_writer.set_scheme_artifacts(
            shared_state,
            generated_draft=(
                payload.generated_draft.model_dump()
                if payload.generated_draft is not None
                else {}
            ),
            rejected_artifact=None,
            deliverable_final_result=None,
        )
        result = AgentResultSchema(
            result_id=f"result_{shared_state.run_id}_scheme_failed",
            task_id=shared_state.task_id,
            run_id=shared_state.run_id,
            agent_name=self.agent_name,
            agent_type=self.agent_type,
            status=status,
            result_type="scheme_writer_output",
            result={"terminal_outcome": outcome.model_dump(mode="json"),
                **({"r02_failure_diagnostics": cleanup_diagnostics} if cleanup_diagnostics else {}),
                **({"lifecycle_composite_transition": lifecycle_binding} if lifecycle_binding is not None else {}),
                **({
                    "generated_draft": payload.generated_draft.model_dump(mode="json"),
                    PWA004_SUMMARY_KEY: payload.document_validation.metadata[PWA004_SUMMARY_KEY],
                } if (payload.generated_draft is not None
                      and PWA004_SUMMARY_KEY in payload.document_validation.metadata) else {}),
                "target_release_publication": payload.target_release_publication.model_dump(mode="json")
                    if payload.target_release_publication is not None else None},
            error=error,
            error_message=error.message,
            started_at=payload.started_at,
            finished_at=self.runtime_support.now_iso(),
            need_human_review=True,
            metadata={
                "failed_business_node": payload.failed_business_node,
                "technical_failure": True,
                **({"r02_failure_diagnostics": cleanup_diagnostics} if cleanup_diagnostics else {}),                **({"lifecycle_composite_transition": lifecycle_binding} if lifecycle_binding is not None else {}),
                **status_view,
                "gate_enforcement_action": status_view["control_action"],
            },
        )
        shared_state.status = status
        return payload.model_copy(
            update={
                "output_status": status,
                "terminal_agent_result": result,
                "terminal_outcome": outcome,
                "terminal_error": error,
                "deliverable_final_result": None,
                "rejected_artifact": None,
                "artifact_route": "technical_failure",
            },
            deep=True,
        )

    def mark_technical_failure(
        self,
        payload: SchemeGenerationGraphPayloadSchema,
        *,
        node_name: str,
        exc: Exception,
    ) -> SchemeGenerationGraphPayloadSchema:
        """Normalize one node exception into the structured failure payload."""

        if isinstance(exc, TerminalOutcomeStageFailure):
            error = exc.error
            trigger = getattr(exc, "c1_triggering_attempt_id", None)
            if trigger is not None:
                error = error.model_copy(update={"details": {
                    **error.details, "c1_triggering_attempt_id": trigger,
                }}, deep=True)
            return payload.model_copy(update={"terminal_error": error, "terminal_outcome": exc.terminal_outcome,
                "failed_business_node": node_name, "artifact_route": "technical_failure"})
        if isinstance(exc, PreWriteWriterExecutionFailed):
            error = ErrorSchema.model_validate(exc.result.model_dump(mode="json")["error"])
        elif isinstance(
            exc,
            (SchemeGenerationStageFailure, MainlineSectionAgentFailure, MainlinePlannerFailure),
        ):
            # These adapters already carry the canonical structured failure.
            # Rewrapping would discard category, retryability and source details.
            error = exc.error
        else:
            failure_category = classify_failure(exc)
            retryable = RetryDecisionPolicy().should_retry(failure_category)
            error = self.runtime_support.error(
                "SCHEME_GENERATION_STAGE_FAILED",
                exc if isinstance(exc, Exception) else str(exc),
                node=node_name,
                retryable=retryable,
                failure_category=failure_category,
                requires_human=True,
                user_message="方案生成顶层业务节点执行失败。",
            )
        trigger = getattr(exc, "c1_triggering_attempt_id", None)
        if trigger is not None:
            error = error.model_copy(update={"details": {
                **error.details, "c1_triggering_attempt_id": trigger,
            }}, deep=True)
        return payload.model_copy(
            update={
                "terminal_error": error,
                "failed_business_node": node_name,
                "artifact_route": "technical_failure",
            },
            deep=True,
        )

    def _raise_failure(self, error: ErrorSchema) -> NoReturn:
        raise SchemeGenerationStageFailure(error)

    def run_compat(self, shared_state: SharedStateSchema) -> AgentResultSchema:
        """Thin legacy facade that calls the same stages in production order."""

        payload = SchemeGenerationGraphPayloadSchema(
            task_id=shared_state.task_id,
            run_id=shared_state.run_id,
            started_at=self.runtime_support.now_iso(),
        )
        try:
            payload = self.prepare_document_plan(shared_state, payload)
            payload = self.retrieve_document_evidence(shared_state, payload)
            payload = self.initialize_generation(shared_state, payload)
            payload = self.execute_sections(shared_state, payload)
            payload = self.assemble_document(shared_state, payload)
            payload = self.evaluate_document_gate(shared_state, payload)
            if payload.artifact_route == "deliverable":
                payload = self.persist_deliverable(shared_state, payload)
            else:
                payload = self.persist_rejected(shared_state, payload)
            payload = self.finalize_workflow_result(shared_state, payload)
        except Exception as exc:
            payload = self.mark_technical_failure(
                payload,
                node_name=shared_state.current_step or self.agent_name,
                exc=exc,
            )
            payload = self.finalize_workflow_failure(shared_state, payload)
        if payload.terminal_agent_result is None:
            error = self.runtime_support.error(
                "SCHEME_WRITER_FAILED",
                "scheme generation compatibility facade produced no result",
                node=self.agent_name,
                failure_category=FailureCategory.UNKNOWN,
            )
            self._raise_failure(error)
        return payload.terminal_agent_result
