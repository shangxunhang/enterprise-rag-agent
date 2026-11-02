"""LangGraph-owned single-section execution lifecycle.

This graph replaces the production ``SectionExecutionCoordinator.execute``
loop.  The old coordinator remains a compatibility implementation, while the
production path owns retrieval, evidence routing, generation retries,
corrective retrieval and finalization as explicit graph nodes.
"""

from __future__ import annotations

from core.runtime.execution_control import checkpoint_current_execution
from core.runtime.recovery import current_durable_recovery

import hashlib
import re
from dataclasses import dataclass
from typing import Any, Callable

from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.graph import END, START, StateGraph

from agent.runtime.graph_state import GraphStateSchema
from agent.runtime.langgraph_state import (
    LangGraphRuntimeStateCodec,
    WorkflowNamespaceCheckpointSaver,
)
from agent.runtime.workflow_stream_context import emit_workflow_custom_event
from agent.runtime.state_access import SharedStateWriter
from apps.enterprise_document.quality.budget import (
    TaskDrainingBudgetExceeded,
    WorkflowBudget,
    WorkflowBudgetExceeded,
    activate_workflow_budget,
    current_task_draining_budget,
)
from apps.enterprise_document.schemas.agent_rag_v1 import (
    EvidencePackageContentSchema,
    ParagraphCandidateSchema,
    ParagraphWriterSourceBindingSchema,
    EvidencePackageSelectionSchema,
    ResearchPackageProgressSchema,
    ResearchRecommendedAction,
    ResearchRoundAuthorizationBindingSchema,
    ResearchRoundBindingSchema,
    ResearchRoundExecutionOutcome,
    ResearchRoundRecordSchema,
    RunningInvocationRecoveryInputSchema,
    SectionPlanSourceRefSchema,
    VerifierAction,
)
from apps.enterprise_document.schemas.pre_write_admission import (
    EvidenceRequirementsSnapshotSchema,
    WriterAdmissionDecisionRefSchema,
    WriterAdmissionFactAssessmentRefSchema,
)
from apps.enterprise_document.schemas.pre_write_binding import (
    BindingApplicabilityResult,
    WriterGateBindingRefSchema,
    WriterSubjectRefSchema,
)
from apps.enterprise_document.schemas.pre_write_control import (
    PreWriteRoute,
    PreWriteRouteDecisionRefSchema,
    RecoveryControlSnapshotRefSchema,
)
from apps.enterprise_document.schemas.pre_write_writer import (
    WriterExecutionStatus,
    WriterGenerationInvocationSchema,
)
from apps.enterprise_document.schemas.section_execution_graph import (
    SectionExecutionGraphPayloadSchema,
    SectionExecutionGraphState,
    SectionExecutionGraphStateCodec,
    SectionGraphExecutionRequestSchema,
)
from apps.enterprise_document.schemas.scheme_writer.planning import (
    SectionPlanBindingSchema,
    SectionPlanContentSchema,
)
from apps.enterprise_document.schemas.scheme_writer.document import SchemeSectionSchema
from apps.enterprise_document.schemas.agent_rag_v1.payloads import (
    ParagraphArtifactSchema,
)
from apps.enterprise_document.schemas.agent_rag_v1.claims import (
    ClaimCoverageAccountingSchema,
    ClaimCoverageAccountingState,
)
from apps.enterprise_document.schemas.agent_rag_v1.verification import (
    ClaimOperationContextSchema,
    VerifierSemanticResultCandidateSchema,
)
from apps.enterprise_document.schemas.agent_rag_v1.writer_pipeline import (
    GeneratedParagraphInputSchema,
)
from apps.enterprise_document.schemas.scheme_writer_schema import (
    AttemptStateSchema,
    SectionEvalSchema,
    SectionEvidenceBundleSchema,
    SectionExecutionRequestSchema,
    SectionExecutionResultSchema,
    SectionLifecycleStatus,
    SectionStateSchema,
)
from apps.enterprise_document.services.output_validation import detect_truncation
from apps.enterprise_document.services.scheme_writer.evidence_projection import (
    bundle_first_assessment_projection,
)
from contracts.evidence import evidence_bundle_sha256, project_evidence_bundle
from core.hashing import canonical_sha256
from core.runtime.side_effects import LogicalOperationRef
from schemas.common import ErrorSourceSchema, WarningSchema
from schemas.rag import EvidenceBundleSchema
from schemas.status import ExecutionStatus

from ..runtime.agent_rag_v1.mainline_recovery import (
    MainlineAttemptGuard,
    MainlineReplanAttempt,
    MainlineRunningInvocationRecovery,
)
from ..services.agent_rag_v1.paragraph_runtime import (
    SECTION_PARAGRAPH_MANIFEST_SEAL,
    SECTION_PARAGRAPH_MANIFEST_UPDATE,
    ParagraphRuntime,
)
from ..services.agent_rag_v1.claim_pipeline import TargetClaimMaterializer
from ..services.agent_rag_v1.claim_verification_runtime import (
    ClaimVerificationRuntime, VerifierSupportVerdictAdmissionError,
)
from ..services.agent_rag_v1.running_invocation_recovery import RunningInvocationRecoveryService
from ..services.agent_rag_v1.research_runtime import (
    EvidencePackageCommitOperations,
    ResearchRoundNotFound,
)
from ..services.agent_rag_v1.streaming_writer import (
    CheckpointParagraphPipeline,
)
from ..services.scheme_writer.agent_integration import (
    MainlineResearchAgentFailure,
)
from ..services.scheme_writer.agent_integration.section_agent_ports import (
    MainlineResearchRetrieval,
)
from ..services.scheme_writer.document_citation_registry import (
    DocumentCitationRegistry,
)
from ..services.scheme_writer.evidence_service import SchemeEvidenceService
from ..services.scheme_writer.gate_recovery_research_authorization import (
    GateRecoveryResearchAuthorizationContext,
)
from ..services.scheme_writer.pre_write_recovery import (
    RecoveryChainSnapshot,
    RecoveryRoundReservation,
)
from ..services.scheme_writer.section_execution_coordinator import (
    PreWriteWriterExecutionFailed,
    SectionExecutionCoordinator,
)
from core.runtime.pre_write_execution_store import RecoveryDispatchClaim, RecoveryDispatchStatus


PWA004_REVIEW_POLICY_KEY = "verifier_rejection_policy"
PWA004_REVIEW_POLICY = "review_draft_continue_v1"
PWA004_REJECTION_KEY = "pwa004_verifier_rejections"


SECTION_GRAPH_NODE_NAMES = (
    "prepare_section",
    "retrieve_section_evidence",
    "evaluate_evidence_gate",
    "evaluate_pre_write_admission",
    "bind_pre_write_gate",
    "evaluate_pre_write_enforcement",
    "commit_pre_write_route",
    "dispatch_pre_write_recovery",
    "execute_pre_write_recovery",
    "complete_pre_write_recovery_dispatch",
    "invoke_pre_write_writer",
    "generate_section",
    "prepare_paragraph_pipeline",
    "persist_paragraph",
    "precheck_paragraph",
    "evaluate_generation",
    "retrieve_more",
    "corrective_retrieval",
    "finalize_section",
)


@dataclass(frozen=True)
class _CommittedGateResearchFailureProjection:
    """Invocation-local transport of canonical truth, never a dispatch authority."""

    authorization: ResearchRoundAuthorizationBindingSchema
    round_binding: ResearchRoundBindingSchema


class SectionExecutionGraph:
    """Execute one section through an explicit checkpoint-safe subgraph."""

    def __init__(
        self,
        *,
        coordinator: SectionExecutionCoordinator,
        checkpointer: BaseCheckpointSaver | None = None,
    ) -> None:
        self.coordinator = coordinator
        self.evidence_service = coordinator.evidence_service
        self.query_builder = coordinator.query_builder
        self.section_generation_service = coordinator.section_generation_service
        self.paragraph_pipeline = CheckpointParagraphPipeline()
        self.attempt_guard = MainlineAttemptGuard()
        paragraph_owner = getattr(coordinator, "paragraph_runtime", None)
        support_composition = getattr(coordinator, "claim_support_composition", None)
        writer_store = (coordinator.require_pre_write_services().writer_invocation.execution_store
                        if coordinator.pre_write_ready else None)
        self.running_invocation_recovery = MainlineRunningInvocationRecovery(
            RunningInvocationRecoveryService(writer_execution_store=writer_store,
                support_runtime=getattr(support_composition, "runtime", None),
                verification_runtime=getattr(coordinator, "claim_verification_runtime", None),
                lifecycle_service=getattr(paragraph_owner, "lifecycle_service", None)),
            lifecycle_service=getattr(paragraph_owner, "lifecycle_service", None))
        self.runtime_support = coordinator.runtime_support
        self.research_runtime = getattr(coordinator, "research_runtime", None)
        self.paragraph_runtime: ParagraphRuntime | None = getattr(
            coordinator, "paragraph_runtime", None
        )
        self.claim_verification_runtime: ClaimVerificationRuntime | None = getattr(
            coordinator,
            "claim_verification_runtime",
            None,
        )
        self.target_claim_materializer = TargetClaimMaterializer()
        self.generation_quality_metadata = dict(
            coordinator.generation_quality_metadata or {}
        )
        self.section_agent_adapter = getattr(
            coordinator,
            "section_agent_adapter",
            None,
        )
        self.codec = SectionExecutionGraphStateCodec()
        self.graph_state_codec = LangGraphRuntimeStateCodec()
        self.state_writer = SharedStateWriter()
        self.checkpointer = checkpointer
        self._langgraph_checkpointer = (
            WorkflowNamespaceCheckpointSaver(checkpointer)
            if checkpointer is not None
            and not isinstance(checkpointer, WorkflowNamespaceCheckpointSaver)
            else checkpointer
        )

    @property
    def _pre_write_ready(self) -> bool:
        return bool(getattr(self.coordinator, "pre_write_ready", False))

    @staticmethod
    def namespace(
        *,
        workflow_namespace: str,
        section_id: str,
    ) -> str:
        return f"{workflow_namespace}/sections/{section_id}"

    def checkpoint_config(
        self,
        *,
        workflow_namespace: str,
        run_id: str,
        section_id: str,
    ) -> dict[str, Any]:
        namespace = self.namespace(
            workflow_namespace=workflow_namespace,
            section_id=section_id,
        )
        return {
            "configurable": {
                "thread_id": run_id,
                "checkpoint_ns": namespace,
                "workflow_namespace": namespace,
            },
            "metadata": {
                "workflow_namespace": workflow_namespace,
                "section_id": section_id,
                "checkpoint_namespace": namespace,
                "graph_kind": "section_execution_subgraph",
            },
            "recursion_limit": 128,
        }

    def _restore(
        self,
        state: SectionExecutionGraphState,
    ) -> tuple[SectionExecutionGraphPayloadSchema, GraphStateSchema]:
        checkpoint_current_execution()
        payload = self.codec.restore_payload(state)
        shared_state = self.codec.restore_graph_state(payload)
        checkpoint_current_execution()
        return payload, shared_state

    def _update(
        self,
        payload: SectionExecutionGraphPayloadSchema,
        *,
        node_name: str,
        route: str,
        shared_state: GraphStateSchema | None = None,
        completed: bool | None = None,
    ) -> dict[str, Any]:
        checkpoint_current_execution()
        updates: dict[str, Any] = {
            "current_node": node_name,
            "route": route,
            "executed_nodes": [*payload.executed_nodes, node_name],
            "transition_count": payload.transition_count + 1,
        }
        if shared_state is not None:
            updates["shared_state_payload"] = self.codec.dump_graph_state(shared_state)
        if completed is not None:
            updates["completed"] = completed
        # Node state is restored fresh and serialized immediately. A shallow
        # replacement copy avoids Python deepcopy of contract-frozen JSON
        # containers while the codec preserves value isolation across nodes.
        updated = payload.model_copy(update=updates)
        state_update = {
            "section_payload": self.codec.dump_payload(updated),
            "current_section_node": node_name,
            "section_route": route,
        }
        checkpoint_current_execution()
        return state_update

    @staticmethod
    def _route(state: SectionExecutionGraphState) -> str:
        return str(state.get("section_route") or "finalize")

    @staticmethod
    def _assessment_from_bundle(bundle: EvidenceBundleSchema) -> dict[str, Any]:
        return bundle_first_assessment_projection(bundle)

    def _initialize_section_state(
        self,
        payload: SectionExecutionGraphPayloadSchema,
        *,
        section_id: str,
    ) -> SectionStateSchema:
        """Explicitly publish the canonical initial attempt before any Agent/Gate use."""

        current = payload.section_state
        lineage = payload.active_execution_lineage
        if current is not None:
            if current.section_id != section_id:
                raise ValueError("persisted SectionState identity is stale")
            if lineage is not None and (
                current.attempt.attempt_id != lineage.attempt_id
                or current.attempt.input_snapshot_version
                != lineage.input_snapshot_version
            ):
                raise ValueError(
                    "persisted SectionState Attempt projection is stale"
                )
            return current
        attempt_number = max(1, int(payload.generation_attempt or 1))
        if lineage is not None:
            if (
                lineage.run_id != payload.run_id
                or lineage.attempt_scope_ref != f"section:{section_id}"
                or not lineage.commit_eligible
            ):
                raise ValueError(
                    "Target SectionState requires current canonical Attempt lineage"
                )
            attempt = AttemptStateSchema(
                attempt_id=lineage.attempt_id,
                attempt_number=attempt_number,
                execution_id=f"{lineage.attempt_id}:execution",
                plan_revision=(
                    payload.canonical_plan_binding.resolved_head_revision
                    if payload.canonical_plan_binding is not None
                    else 1
                ),
                input_snapshot_version=lineage.input_snapshot_version,
            )
        else:
            attempt = AttemptStateSchema(
                attempt_id=f"{payload.run_id}:{section_id}:attempt:{attempt_number}",
                attempt_number=attempt_number,
                execution_id=(
                    f"{payload.run_id}:{section_id}:execution:{attempt_number}"
                ),
                plan_revision=1,
                input_snapshot_version=self.attempt_guard.snapshot_version(
                    payload.project_input,
                    payload.section_plan,
                ),
            )
        plan = payload.section_plan
        return SectionStateSchema(
            section_id=section_id,
            section_title=plan.section_title,
            section_order=plan.section_order,
            lifecycle_status=SectionLifecycleStatus.PLANNED,
            execution_group=plan.execution_group,
            can_run_in_parallel=plan.can_run_in_parallel,
            requires_global_context=plan.requires_global_context,
            hard_dependencies=list(plan.depends_on),
            attempt=attempt,
        )

    def _pre_write_identity(
        self,
        payload: SectionExecutionGraphPayloadSchema,
    ) -> tuple[WriterSubjectRefSchema, str]:
        if payload.section_state is None:
            raise ValueError(
                "Target pre-write execution requires persisted SectionState"
            )
        write_instance_id = str(
            payload.section_state.attempt.attempt_id or ""
        ).strip()
        if not write_instance_id:
            raise ValueError(
                "Target pre-write execution requires persisted attempt_id"
            )
        lineage = payload.active_execution_lineage
        if lineage is not None:
            if lineage.run_id != payload.run_id:
                raise ValueError(
                    "canonical pre-write Attempt lineage belongs to another Run"
                )
            if lineage.attempt_id != write_instance_id:
                raise ValueError(
                    "persisted write_instance_id disagrees with active canonical Attempt"
                )
            expected_scope = f"section:{str(payload.section_id or payload.section_plan.section_id).strip()}"
            if lineage.attempt_scope_ref != expected_scope:
                raise ValueError(
                    "canonical pre-write Attempt scope does not target current Section"
                )
        section_id = str(payload.section_id or payload.section_plan.section_id).strip()
        if section_id != payload.section_state.section_id:
            raise ValueError("SectionState identity is stale")
        subject = self.coordinator.pre_write_subject(
            run_id=payload.run_id,
            document_id=payload.document_id,
            section_id=section_id,
        )
        return subject, write_instance_id

    def _canonical_pre_write_sources(
        self,
        payload: SectionExecutionGraphPayloadSchema,
    ) -> tuple[EvidenceBundleSchema, EvidenceRequirementsSnapshotSchema | None]:
        """Resolve the exact current Research subject before Gate semantics run.

        Compatibility mode may still enter Gate from the existing Bundle plus
        supervisor-owned requirements. Canonical M4 mode is stricter: the exact
        Package, current ResearchRound, Bundle, Plan and Requirement source must
        agree before WriterAdmissionFactAssessor is allowed to observe them.
        """

        runtime = self.research_runtime
        package_binding = payload.active_evidence_package_binding
        if package_binding is None:
            if runtime is not None:
                raise ValueError(
                    "canonical pre-write readiness requires current EvidencePackage"
                )
            evidence_bundle = (
                payload.active_evidence_bundle or payload.document_evidence_bundle
            )
            if evidence_bundle is None:
                raise ValueError(
                    "pre-write Admission requires canonical EvidenceBundle"
                )
            return evidence_bundle, None

        if runtime is None:
            raise ValueError(
                "EvidencePackage binding requires attached ResearchRuntime"
            )
        plan_binding = payload.canonical_plan_binding
        lineage = payload.active_execution_lineage
        round_binding = payload.current_research_round_binding
        if plan_binding is None or lineage is None or round_binding is None:
            raise ValueError(
                "canonical pre-write readiness requires Plan, Attempt, and ResearchRound refs"
            )
        if lineage.run_id != payload.run_id:
            raise ValueError("canonical pre-write Attempt belongs to another Run")
        section_id = str(payload.section_id or payload.section_plan.section_id).strip()
        if plan_binding.section_id != section_id:
            raise ValueError("canonical pre-write Plan targets another Section")

        package = runtime.validate_evidence_package_binding_current(
            package_binding,
            require_source_fresh=True,
        )
        if (
            package.section_plan_source.plan_version_ref
            != plan_binding.plan_version_ref
            or package.section_plan_source.section_id != section_id
        ):
            raise ValueError(
                "current EvidencePackage does not bind the current SectionPlan"
            )
        evidence_bundle = runtime.resolve_evidence_bundle(
            package.evidence_bundle_binding
        )
        projected_bundle = (
            payload.active_evidence_bundle or payload.document_evidence_bundle
        )
        if projected_bundle is None:
            raise ValueError(
                "canonical pre-write readiness requires active EvidenceBundle projection"
            )
        if evidence_bundle != projected_bundle:
            raise ValueError(
                "active EvidenceBundle projection disagrees with current EvidencePackage"
            )
        expected_bundle_hash = "sha256:" + evidence_bundle_sha256(evidence_bundle)
        if (
            package.evidence_bundle_binding.evidence_bundle_sha256
            != expected_bundle_hash
        ):
            raise ValueError(
                "current EvidencePackage EvidenceBundle hash is inconsistent"
            )

        if (
            round_binding.round_ref.run_id != payload.run_id
            or round_binding.round_ref.section_id != section_id
            or round_binding.round_ref.attempt_scope_ref
            != lineage.attempt_scope_ref
            or round_binding.round_ref.attempt_id != lineage.attempt_id
        ):
            raise ValueError(
                "current ResearchRound does not belong to active canonical Attempt"
            )
        round_record = runtime.validate_round_binding_current(round_binding)
        if (
            round_record.execution_outcome
            is ResearchRoundExecutionOutcome.TECHNICAL_FAILURE
        ):
            raise ValueError(
                "TECHNICAL_FAILURE ResearchRound cannot authorize WriterAdmission"
            )
        if (
            round_record.source_plan_version_ref != plan_binding.plan_version_ref
            or round_record.final_evidence_bundle_binding
            != package.evidence_bundle_binding
            or round_record.package_progress.resulting_evidence_package_version_ref
            != package_binding.version_ref
        ):
            raise ValueError(
                "current ResearchRound does not resolve the current Package subject"
            )

        requirements, _selected_ids = runtime.resolve_research_requirements(
            plan_binding
        )
        if requirements is None:
            raise ValueError(
                "canonical pre-write readiness requires exact EvidenceRequirements snapshot"
            )
        return evidence_bundle, requirements

    def _pre_write_trace_emitter(self):
        if not self._pre_write_ready:
            return None
        return self.coordinator.require_pre_write_services().trace_emitter

    @staticmethod
    def _fact_assessment_ref(assessment) -> WriterAdmissionFactAssessmentRefSchema:
        return WriterAdmissionFactAssessmentRefSchema(
            assessment_id=assessment.assessment_id,
            assessment_schema_version=assessment.schema_version,
            assessment_contract_version=assessment.contract_version,
            requirements_schema_version=assessment.requirements_schema_version,
            requirements_contract_version=assessment.requirements_contract_version,
            requirements_sha256=assessment.requirements_sha256,
            evidence_bundle_schema_version=assessment.evidence_bundle_schema_version,
            evidence_bundle_sha256=assessment.evidence_bundle_sha256,
        )

    @staticmethod
    def _decision_ref(decision) -> WriterAdmissionDecisionRefSchema:
        return WriterAdmissionDecisionRefSchema(
            decision_id=decision.decision_id,
            decision_schema_version=decision.schema_version,
            decision_contract_version=decision.contract_version,
        )

    @staticmethod
    def _binding_ref(binding) -> WriterGateBindingRefSchema:
        return WriterGateBindingRefSchema(
            binding_id=binding.binding_id,
            binding_schema_version=binding.schema_version,
            binding_contract_version=binding.contract_version,
        )

    @staticmethod
    def _route_ref(route_decision) -> PreWriteRouteDecisionRefSchema:
        return PreWriteRouteDecisionRefSchema(
            route_decision_id=route_decision.route_decision_id,
            route_decision_schema_version=route_decision.schema_version,
            route_decision_contract_version=route_decision.contract_version,
        )

    def _canonical_paragraph_source_binding(
        self,
        payload: SectionExecutionGraphPayloadSchema,
    ) -> tuple[ParagraphWriterSourceBindingSchema, WriterGenerationInvocationSchema]:
        runtime = self.paragraph_runtime
        research_runtime = self.research_runtime
        if runtime is None or research_runtime is None:
            raise ValueError(
                "canonical Paragraph path requires ParagraphRuntime and ResearchRuntime"
            )
        lineage = payload.active_execution_lineage
        plan_binding = payload.canonical_plan_binding
        package_binding = payload.active_evidence_package_binding
        writer_key = payload.writer_logical_execution_key
        execution_ref = payload.writer_execution_ref
        invocation_ref = payload.writer_generation_invocation_ref
        gate_binding = payload.active_writer_gate_binding
        route_decision = payload.current_pre_write_route_decision
        if (
            lineage is None
            or plan_binding is None
            or package_binding is None
            or writer_key is None
            or execution_ref is None
            or invocation_ref is None
            or gate_binding is None
            or route_decision is None
        ):
            raise ValueError(
                "canonical Paragraph source requires exact Attempt/Plan/Package/Gate/Writer refs"
            )
        subject, write_instance_id = self._pre_write_identity(payload)
        if (
            writer_key.writer_subject_ref != subject
            or writer_key.write_instance_id != write_instance_id
            or execution_ref.logical_execution_key != writer_key
            or invocation_ref.logical_execution_key != writer_key
        ):
            raise ValueError("canonical Paragraph Writer exact refs disagree")

        result = self.coordinator.resolve_pre_write_writer_execution(execution_ref)
        invocation = self.coordinator.resolve_pre_write_writer_invocation(invocation_ref)
        if result.logical_execution_key != writer_key:
            raise ValueError("canonical Paragraph Writer execution key is stale")
        if (
            invocation.writer_subject_ref != subject
            or invocation.write_instance_id != write_instance_id
            or invocation.route_decision_ref != writer_key.route_decision_ref
        ):
            raise ValueError("canonical Paragraph Writer invocation lineage is stale")
        if self._route_ref(route_decision) != invocation.route_decision_ref:
            raise ValueError("canonical Paragraph route ref is stale")
        applicability = self._pre_write_applicability(payload)
        if applicability.binding != gate_binding:
            raise ValueError(
                "canonical Paragraph WriterGateBinding is not currently applicable"
            )
        route_store = self.coordinator.require_pre_write_services().route_decision_store
        persisted_route = route_store.resolve_exact(self._route_ref(route_decision))
        if persisted_route != route_decision:
            raise ValueError(
                "canonical Paragraph RouteDecision does not resolve exact persisted truth"
            )

        package = research_runtime.validate_evidence_package_binding_current(
            package_binding,
            require_source_fresh=True,
        )
        if (
            package.section_plan_source.plan_version_ref
            != plan_binding.plan_version_ref
            or package.section_plan_source.section_id != plan_binding.section_id
        ):
            raise ValueError(
                "canonical Paragraph Package does not bind current SectionPlan"
            )
        plan_runtime = research_runtime.section_plan_runtime
        plan_runtime.validate_plan_binding_current(plan_binding)
        raw_plan, canonical_plan_ref = plan_runtime.artifact_runtime.read_version(
            plan_binding.plan_version_ref
        )
        if canonical_plan_ref != plan_binding.plan_version_ref:
            raise ValueError("canonical Paragraph Plan resolver returned another version")
        plan_content = SectionPlanContentSchema.model_validate(raw_plan)
        fact_ref = plan_content.fact_snapshot_version_ref
        fact_binding = plan_runtime.fact_snapshot_runtime.resolve_current_binding(
            fact_ref.logical_ref
        )
        if fact_binding is None or fact_binding.version_ref != fact_ref:
            raise ValueError("canonical Paragraph FactSnapshot source is stale")

        target_context = invocation.unified_writer_input_snapshot.get(
            "target_context_package"
        )
        if target_context is not None:
            target_context_json = (
                target_context
                if isinstance(target_context, dict)
                else invocation.model_dump(mode="json")[
                    "unified_writer_input_snapshot"
                ].get("target_context_package")
            )
            target_context_json = dict(target_context_json or {})
            context_package_id = str(
                dict(target_context_json.get("package_ref") or {}).get(
                    "context_package_id"
                )
                or ""
            ).strip()
            context_sha256 = str(
                dict(target_context_json.get("model_input") or {}).get(
                    "final_model_input_sha256"
                )
                or ""
            ).strip()
        else:
            target_prompt_lock = dict(
                invocation.unified_writer_input_snapshot.get("target_prompt_lock") or {}
            )
            context_package_id = str(
                target_prompt_lock.get("context_package_id") or ""
            ).strip()
            context_sha256 = str(
                target_prompt_lock.get("context_sha256") or ""
            ).strip()
        if not context_package_id or not context_sha256:
            raise ValueError(
                "canonical Paragraph Writer invocation lacks managed Context identity"
            )
        return (
            ParagraphWriterSourceBindingSchema(
                writer_subject_ref=subject,
                write_instance_id=write_instance_id,
                route_decision_ref=invocation.route_decision_ref,
                writer_gate_binding_ref=self._binding_ref(gate_binding),
                generation_class=invocation.generation_class,
                section_plan_source=SectionPlanSourceRefSchema(
                    plan_version_ref=plan_binding.plan_version_ref,
                    section_id=plan_binding.section_id,
                ),
                fact_snapshot_version_ref=fact_ref,
                evidence_package_version_ref=package_binding.version_ref,
                context_package_id=context_package_id,
                context_sha256=context_sha256,
            ),
            invocation,
        )

    def _build_canonical_paragraph_candidates(
        self,
        payload: SectionExecutionGraphPayloadSchema,
        *,
        section,
    ) -> list[ParagraphCandidateSchema]:
        runtime = self.paragraph_runtime
        if runtime is None:
            raise ValueError("canonical Paragraph candidate builder requires runtime")
        source_binding, _invocation = self._canonical_paragraph_source_binding(payload)
        writer_key = payload.writer_logical_execution_key
        if writer_key is None:
            raise ValueError("canonical Paragraph candidate requires Writer key")
        authorization_ref = runtime.initial_writer_authorization_ref(writer_key)
        parts = self.paragraph_pipeline.split_candidate_text(section.content)
        candidates: list[ParagraphCandidateSchema] = []
        for index, text in enumerate(parts):
            occurrence = runtime.creation_occurrence(
                run_id=payload.run_id,
                section_id=str(payload.section_id),
                write_instance_id=source_binding.write_instance_id,
                authorization_kind="INITIAL_WRITER",
                authorization_ref=authorization_ref,
                creation_slot_ref=f"initial-writer-paragraph-slot:{index:06d}",
            )
            operation = runtime.create_operation(occurrence)
            candidates.append(
                ParagraphCandidateSchema(
                    content_contract_version="section_writer_paragraph_candidate_v1",
                    operation_ref=operation,
                    source_binding=source_binding,
                    role="body",
                    text=text,
                )
            )
        return candidates

    def _canonical_paragraph_projection(
        self,
        payload: SectionExecutionGraphPayloadSchema,
    ) -> tuple[Any, list, list[ParagraphArtifactSchema]]:
        runtime = self.paragraph_runtime
        lineage = payload.active_execution_lineage
        if runtime is None or lineage is None:
            raise ValueError("canonical Paragraph projection requires runtime/Attempt")
        _subject, write_instance_id = self._pre_write_identity(payload)
        manifest_ref = runtime.manifest_ref(
            run_id=payload.run_id,
            section_id=str(payload.section_id),
            active_lineage=lineage,
            write_instance_id=write_instance_id,
        )
        manifest = runtime.load_manifest(manifest_ref)
        bindings = []
        artifacts: list[ParagraphArtifactSchema] = []
        for order, member in enumerate(manifest.members):
            binding = runtime.resolve_paragraph_binding(
                logical_ref=member.logical_ref,
                manifest_ref=manifest_ref,
            )
            if binding is None:
                raise ValueError(
                    "canonical Paragraph Manifest member is not exact current binding"
                )
            content = runtime.validate_paragraph_binding_current(binding)
            bindings.append(binding)
            artifacts.append(
                ParagraphArtifactSchema(
                    paragraph_id=member.logical_ref.logical_artifact_id,
                    paragraph_version=binding.resolved_head_revision,
                    order=order,
                    role=content.role,
                    content_ref=member.content_ref,
                    content_hash=(
                        "sha256:"
                        + hashlib.sha256(content.text.encode("utf-8")).hexdigest()
                    ),
                    content=content.text,
                )
            )
        return manifest, bindings, artifacts

    @staticmethod
    def _recovery_control_ref(recovery_control) -> RecoveryControlSnapshotRefSchema:
        return RecoveryControlSnapshotRefSchema(
            schema_version=recovery_control.schema_version,
            contract_version=recovery_control.contract_version,
            recovery_state_revision=recovery_control.recovery_state_revision,
        )

    def _pre_write_chain(
        self,
        payload: SectionExecutionGraphPayloadSchema,
    ) -> RecoveryChainSnapshot:
        evidence_bundle = payload.active_evidence_bundle
        requirements = payload.active_evidence_requirements_snapshot
        assessment = payload.active_writer_admission_fact_assessment
        decision = payload.active_writer_admission_decision
        binding = payload.active_writer_gate_binding
        if (
            evidence_bundle is None
            or requirements is None
            or assessment is None
            or decision is None
            or binding is None
        ):
            raise ValueError("canonical pre-write control chain is incomplete")
        return self.coordinator.pre_write_recovery_chain(
            evidence_bundle=evidence_bundle,
            requirements=requirements,
            assessment=assessment,
            decision=decision,
            binding=binding,
        )

    def _pre_write_applicability(
        self,
        payload: SectionExecutionGraphPayloadSchema,
    ) -> BindingApplicabilityResult:
        subject, write_instance_id = self._pre_write_identity(payload)
        chain = self._pre_write_chain(payload)
        return self.coordinator.validate_pre_write_binding(
            writer_subject_ref=subject,
            write_instance_id=write_instance_id,
            decision=chain.decision,
            binding=chain.binding,
            evidence_bundle=chain.evidence_bundle,
            requirements=chain.requirements,
        )

    @staticmethod
    def _document_projection(
        payload: SectionExecutionGraphPayloadSchema,
    ) -> tuple:
        bundle = payload.document_evidence_bundle
        if bundle is None:
            raise ValueError("section graph requires canonical document_evidence_bundle")
        context, chunks, citations = project_evidence_bundle(bundle)
        registry = DocumentCitationRegistry.from_citations(payload.citation_registry)
        context, chunks, citations, normalized = registry.remap_bundle(
            context=context,
            chunks=chunks,
            citations=citations,
            normalized={"extra": dict(bundle.extra or {})},
            scope="document",
            query=bundle.query,
        )
        return context, chunks, citations, normalized

    @classmethod
    def _active_context_projection(
        cls,
        payload: SectionExecutionGraphPayloadSchema,
    ) -> RAGContextSchema:
        if payload.active_context is not None:
            return payload.active_context
        context, _, _, _ = cls._document_projection(payload)
        return context

    @staticmethod
    def _evidence_contract_sha(
        payload: SectionExecutionGraphPayloadSchema,
    ) -> str | None:
        bundle = payload.active_evidence_bundle or payload.document_evidence_bundle
        return evidence_bundle_sha256(bundle) if bundle is not None else None

    @staticmethod
    def _active_evidence_ids(
        payload: SectionExecutionGraphPayloadSchema,
    ) -> list[str]:
        bundle = payload.active_evidence_bundle or payload.document_evidence_bundle
        return list(bundle.selected_evidence_ids) if bundle is not None else []

    @staticmethod
    def _validate_research_evidence_projection(
        payload: SectionExecutionGraphPayloadSchema,
        *,
        outcome,
        invocation_accepted_evidence_ids: list[str],
        invocation_evidence_contract_sha256: str | None,
    ) -> None:
        """Reject Research Evidence ID/hash lineage drift at the Application boundary."""

        summary = outcome.payload.evidence_summary
        package_ids = list(summary.accepted_evidence_ids)
        state_ids = list(outcome.section_state.research.accepted_evidence_ids)
        package_rejected_ids = [
            item.evidence_id for item in summary.rejected_evidence
        ]
        state_rejected_ids = list(
            outcome.section_state.research.rejected_evidence_ids
        )
        if package_rejected_ids or state_rejected_ids:
            raise ValueError("Research rejected_evidence_ids must remain empty")

        retrieval = outcome.retrieval
        if retrieval is None:
            bundle = payload.active_evidence_bundle or payload.document_evidence_bundle
            expected_ids = (
                list(bundle.selected_evidence_ids) if bundle is not None else []
            )
            expected_sha = (
                evidence_bundle_sha256(bundle) if bundle is not None else None
            )
            if (
                invocation_accepted_evidence_ids != expected_ids
                or package_ids != expected_ids
                or state_ids != expected_ids
            ):
                raise ValueError(
                    "Research Evidence ID projection mismatch on precomputed path"
                )
            if (
                invocation_evidence_contract_sha256 != expected_sha
                or outcome.payload.evidence_contract_sha256 != expected_sha
                or outcome.section_state.research.evidence_contract_sha256
                != expected_sha
            ):
                raise ValueError(
                    "Research Evidence contract hash mismatch on precomputed path"
                )
            return

        if retrieval.success and retrieval.evidence_bundle is not None:
            bundle = retrieval.evidence_bundle
            expected_ids = list(bundle.selected_evidence_ids)
            expected_sha = evidence_bundle_sha256(bundle)
            if package_ids != expected_ids or state_ids != expected_ids:
                raise ValueError(
                    "Research Evidence ID projection mismatch on tool path"
                )
            if (
                outcome.payload.evidence_contract_sha256 != expected_sha
                or outcome.section_state.research.evidence_contract_sha256
                != expected_sha
            ):
                raise ValueError(
                    "Research Evidence contract hash mismatch on tool path"
                )
            return

        if not retrieval.success and retrieval.evidence_bundle is None:
            bundle = payload.active_evidence_bundle or payload.document_evidence_bundle
            expected_ids = (
                list(bundle.selected_evidence_ids) if bundle is not None else []
            )
            expected_sha = (
                evidence_bundle_sha256(bundle) if bundle is not None else None
            )
            if (
                invocation_accepted_evidence_ids != expected_ids
                or package_ids != expected_ids
                or state_ids != expected_ids
            ):
                raise ValueError(
                    "Research Evidence ID projection mismatch on failed tool path"
                )
            if (
                invocation_evidence_contract_sha256 != expected_sha
                or outcome.payload.evidence_contract_sha256 != expected_sha
                or outcome.section_state.research.evidence_contract_sha256
                != expected_sha
            ):
                raise ValueError(
                    "Research Evidence contract hash mismatch on failed tool path"
                )

    def _recovery_candidate_decision(
        self,
        *,
        current_writer_gate,
        candidate_writer_gate,
        candidate_context,
    ) -> tuple[bool, bool, bool, str | None]:
        """Decide whether a recovery candidate may replace current active Evidence.

        Retrieval success is only a technical fact. Application acceptance is a
        separate decision and must happen before any active Evidence field changes.
        This mirrors the frozen coordinator semantics without introducing ranking.
        """

        current_generation_permitted = bool(
            current_writer_gate is not None
            and not self.coordinator.writer_gate_blocks(current_writer_gate)
        )
        candidate_context_available = bool(
            candidate_context is not None
            and candidate_context.context_text.strip()
        )
        candidate_generation_permitted = bool(
            candidate_context_available
            and candidate_writer_gate is not None
            and not self.coordinator.writer_gate_blocks(candidate_writer_gate)
        )
        if candidate_generation_permitted:
            return True, current_generation_permitted, True, None
        if current_generation_permitted:
            rejection_reason = (
                "empty_recovery_context"
                if not candidate_context_available
                else "recovery_not_ordinary_generation_permitted"
            )
            return False, True, False, rejection_reason
        # Existing coordinator semantics: when both current and candidate are
        # non-generatable, the newest failed Evidence may remain the final active
        # Evidence if the recovery returned any meaningful context or Gate result.
        accept_failed_candidate = bool(
            candidate_context_available or candidate_writer_gate is not None
        )
        return (
            accept_failed_candidate,
            False,
            False,
            None if accept_failed_candidate else "empty_recovery_context",
        )

    @staticmethod
    def _append_agent_payload(
        existing: list,
        item,
        *,
        metadata_field: str,
    ) -> list:
        metadata = dict(getattr(item, metadata_field, {}) or {})
        invocation_id = str(metadata.get("invocation_id") or "").strip()
        if not invocation_id:
            return [*existing, item]
        retained = [
            value
            for value in existing
            if str(
                dict(getattr(value, metadata_field, {}) or {}).get(
                    "invocation_id"
                )
                or ""
            ).strip()
            != invocation_id
        ]
        return [*retained, item]

    def _validate_agent_result_identity(
        self,
        payload: SectionExecutionGraphPayloadSchema,
        *,
        section_state,
        metadata: dict[str, Any],
    ) -> dict[str, Any]:
        """Reject stale Agent output before it can mutate the Section payload."""

        section_id = str(payload.section_id or payload.section_plan.section_id)
        return self.attempt_guard.validate_result_metadata(
            run_id=payload.run_id,
            section_id=section_id,
            section_state=section_state,
            project_input=payload.project_input,
            section_plan=payload.section_plan,
            metadata=metadata,
        )

    @staticmethod
    def _research_package_operation(
        *,
        logical_ref,
        lineage,
        round_ref,
        package_fingerprint: str,
        operation_kind: str,
        suffix: str,
    ) -> LogicalOperationRef:
        operation_id = "research-package:" + suffix + ":" + canonical_sha256(
            {
                "round_ref": round_ref.model_dump(mode="json", round_trip=True),
                "package_fingerprint": package_fingerprint,
                "operation_kind": operation_kind,
            }
        )
        return LogicalOperationRef(
            logical_operation_id=operation_id,
            operation_kind=operation_kind,
            run_id=round_ref.run_id,
            operation_scope_ref=logical_ref.domain_scope_ref,
            attempt_scope_ref=lineage.attempt_scope_ref,
            attempt_id=lineage.attempt_id,
        )

    def _commit_canonical_research_result(
        self,
        payload: SectionExecutionGraphPayloadSchema,
        *,
        outcome=None,
        retrieval,
        authorization_context: object | None = None,
    ) -> dict[str, Any]:
        """Lower one exact accepted Research handoff into SIF-05/SIF-08 truth.

        The helper owns no Workflow route decision.  Wave-13/cutover must supply
        an already-current Plan binding, active Attempt lineage and exact external
        Research authorization.  If the canonical runtime is attached, absence of
        any of those refs fails closed rather than falling back to checkpoint state.
        """

        runtime = self.research_runtime
        if runtime is None:
            return {}
        plan_binding = payload.canonical_plan_binding
        lineage = payload.active_execution_lineage
        authorization = payload.research_authorization_binding
        bundle_binding = retrieval.canonical_bundle_binding
        missing = [
            name
            for name, value in (
                ("canonical_plan_binding", plan_binding),
                ("active_execution_lineage", lineage),
                ("research_authorization_binding", authorization),
                ("canonical_bundle_binding", bundle_binding),
            )
            if value is None
        ]
        if missing:
            raise RuntimeError(
                "canonical Research commit prerequisites are missing: "
                + ", ".join(missing)
            )
        assert plan_binding is not None
        assert lineage is not None
        assert authorization is not None
        assert bundle_binding is not None
        section_id = str(payload.section_id or payload.section_plan.section_id)
        if plan_binding.section_id != section_id:
            raise RuntimeError("canonical Plan binding targets a different Section")
        if lineage.run_id != payload.run_id:
            raise RuntimeError("canonical Attempt lineage targets a different Run")

        round_ref = runtime.derive_round_ref(
            run_id=payload.run_id,
            section_id=section_id,
            authorization_binding=authorization,
            active_attempt=lineage,
        )

        # Resolver-before-retry: a committed occurrence must never mint a second
        # Round or roll current progress backward after checkpoint loss.
        try:
            existing_round = runtime.resolve_round(round_ref)
        except ResearchRoundNotFound:
            existing_round = None
        if existing_round is not None:
            if existing_round.final_evidence_bundle_binding != bundle_binding:
                raise RuntimeError(
                    "same Research authorization replay produced different EvidenceBundle"
                )
            current_round = runtime.resolve_current_round_binding(
                run_id=round_ref.run_id,
                section_id=round_ref.section_id,
                attempt_scope_ref=round_ref.attempt_scope_ref,
                attempt_id=round_ref.attempt_id,
            )
            if current_round is None or current_round.round_ref != round_ref:
                raise RuntimeError(
                    "historical Research authorization replay cannot become current"
                )
            package_version = (
                existing_round.package_progress.resulting_evidence_package_version_ref
            )
            if package_version is None:
                raise RuntimeError(
                    "committed successful Research round has no EvidencePackage version"
                )
            package_binding = runtime.resolve_current_evidence_package_binding(
                run_id=payload.run_id,
                section_id=section_id,
            )
            if package_binding is None or package_binding.version_ref != package_version:
                raise RuntimeError(
                    "committed Research round no longer resolves its current Package"
                )
            memory = getattr(self.coordinator, "memory_composition", None)
            if memory is not None:
                memory.project_artifact(package_binding.version_ref)
                memory.project_round(round_ref)
            return {
                "active_evidence_package_binding": package_binding,
                "current_research_round_binding": current_round,
            }

        prior_package = payload.active_evidence_package_binding
        prior_version = None
        if prior_package is not None:
            runtime.validate_evidence_package_binding_current(
                prior_package,
                require_source_fresh=True,
            )
            prior_version = prior_package.version_ref

        accepted_source_ids = (
            outcome.payload.evidence_summary.accepted_evidence_ids
            if outcome is not None
            else (
                retrieval.evidence_bundle.selected_evidence_ids
                if retrieval.evidence_bundle is not None
                else ()
            )
        )
        accepted_ids = tuple(dict.fromkeys(accepted_source_ids))
        package_content = EvidencePackageContentSchema(
            projection_contract_version="research_evidence_package_projection_v1",
            section_plan_source=SectionPlanSourceRefSchema(
                plan_version_ref=plan_binding.plan_version_ref,
                section_id=section_id,
            ),
            evidence_bundle_binding=bundle_binding,
            selections=tuple(
                EvidencePackageSelectionSchema(evidence_id=evidence_id)
                for evidence_id in accepted_ids
            ),
        )
        package_logical = runtime.evidence_package_logical_ref(
            run_id=payload.run_id,
            section_id=section_id,
        )
        package_fingerprint = runtime.evidence_package_semantic_fingerprint(
            package_logical,
            package_content,
        )
        head = runtime.artifact_runtime.resolve_active(package_logical)
        expected_head_revision = head.head_revision if head is not None else 0
        expected_active_version_id = (
            head.active_version_ref.artifact_version_id
            if head is not None and head.active_version_ref is not None
            else None
        )
        package_version = runtime.commit_evidence_package(
            candidate_content=package_content,
            logical_ref=package_logical,
            plan_binding=plan_binding,
            expected_head_revision=expected_head_revision,
            expected_active_version_id=expected_active_version_id,
            operations=EvidencePackageCommitOperations(
                version_commit=self._research_package_operation(
                    logical_ref=package_logical,
                    lineage=lineage,
                    round_ref=round_ref,
                    package_fingerprint=package_fingerprint,
                    operation_kind="ARTIFACT_VERSION_COMMIT",
                    suffix="version",
                ),
                active_promotion=self._research_package_operation(
                    logical_ref=package_logical,
                    lineage=lineage,
                    round_ref=round_ref,
                    package_fingerprint=package_fingerprint,
                    operation_kind="ARTIFACT_ACTIVE_PROMOTION",
                    suffix="promotion",
                ),
            ),
            commit_guard_context=lineage,
        )
        package_binding = runtime.resolve_current_evidence_package_binding(
            run_id=payload.run_id,
            section_id=section_id,
        )
        if package_binding is None or package_binding.version_ref != package_version:
            raise RuntimeError("EvidencePackage commit did not resolve current binding")

        if prior_version is None:
            material_change = "NEW_PACKAGE"
        elif prior_version == package_version:
            material_change = "NO_MATERIAL_PACKAGE_CHANGE"
        else:
            material_change = "CHANGED_PACKAGE"
        progress = runtime.resolve_progress(
            run_id=round_ref.run_id,
            section_id=round_ref.section_id,
            attempt_scope_ref=round_ref.attempt_scope_ref,
            attempt_id=round_ref.attempt_id,
        )
        execution_outcome = (
            ResearchRoundExecutionOutcome.COMPLETED_WITH_TECHNICAL_DEGRADATION
            if (
                outcome is not None
                and outcome.payload.research_execution_status == "partial"
            )
            else ResearchRoundExecutionOutcome.COMPLETED
        )
        round_record = ResearchRoundRecordSchema(
            round_ref=round_ref,
            round_ordinal=progress.committed_round_count + 1,
            authorization_binding=authorization,
            prior_committed_round_ref=progress.last_committed_round_ref,
            source_plan_version_ref=plan_binding.plan_version_ref,
            execution_outcome=execution_outcome,
            final_evidence_bundle_binding=bundle_binding,
            package_progress=ResearchPackageProgressSchema(
                prior_evidence_package_version_ref=prior_version,
                resulting_evidence_package_version_ref=package_version,
                material_package_change=material_change,
            ),
            # The gateway may expose exact SIF-12 refs after its owning
            # cutover. Legacy call-id strings are deliberately not promoted here.
            tool_operation_refs=tuple(retrieval.tool_operation_refs),
            budget_receipt_refs=tuple(retrieval.budget_receipt_refs),
        )
        committed = runtime.commit_round(
            round_candidate=round_record,
            authorization_context=authorization_context,
            operation_ref=runtime.round_operation_ref(round_ref),
            source_plan_binding=plan_binding,
            resulting_package_binding=package_binding,
            commit_guard_context=lineage,
            expected_progress_revision=progress.progress_revision,
            expected_committed_round_count=progress.committed_round_count,
            expected_last_committed_round_ref=progress.last_committed_round_ref,
        )
        memory = getattr(self.coordinator, "memory_composition", None)
        if memory is not None:
            memory.project_artifact(package_binding.version_ref)
            memory.project_round(round_ref)
        return {
            "active_evidence_package_binding": package_binding,
            "current_research_round_binding": committed.binding,
        }

    def _commit_canonical_research_failure(
        self,
        payload: SectionExecutionGraphPayloadSchema,
        *,
        retrieval,
        authorization_context: object | None = None,
    ) -> dict[str, Any]:
        """Commit the exact authorized occurrence as TECHNICAL_FAILURE."""

        runtime = self.research_runtime
        if runtime is None:
            return {}
        plan_binding = payload.canonical_plan_binding
        lineage = payload.active_execution_lineage
        authorization = payload.research_authorization_binding
        missing = [
            name
            for name, value in (
                ("canonical_plan_binding", plan_binding),
                ("active_execution_lineage", lineage),
                ("research_authorization_binding", authorization),
            )
            if value is None
        ]
        if missing:
            raise RuntimeError(
                "canonical Research technical-failure prerequisites are missing: "
                + ", ".join(missing)
            )
        assert plan_binding is not None
        assert lineage is not None
        assert authorization is not None
        section_id = str(payload.section_id or payload.section_plan.section_id)
        if plan_binding.section_id != section_id:
            raise RuntimeError("canonical Plan binding targets a different Section")

        round_ref = runtime.derive_round_ref(
            run_id=payload.run_id,
            section_id=section_id,
            authorization_binding=authorization,
            active_attempt=lineage,
        )
        try:
            existing = runtime.resolve_round(round_ref)
        except ResearchRoundNotFound:
            existing = None
        if existing is not None:
            if existing.execution_outcome is not ResearchRoundExecutionOutcome.TECHNICAL_FAILURE:
                raise RuntimeError(
                    "same Research authorization is already committed with a "
                    "different execution outcome"
                )
            current = runtime.resolve_current_round_binding(
                run_id=round_ref.run_id,
                section_id=round_ref.section_id,
                attempt_scope_ref=round_ref.attempt_scope_ref,
                attempt_id=round_ref.attempt_id,
            )
            if current is None or current.round_ref != round_ref:
                raise RuntimeError(
                    "historical failed Research authorization replay cannot become current"
                )
            memory = getattr(self.coordinator, "memory_composition", None)
            if memory is not None:
                memory.project_round(round_ref)
            return {"current_research_round_binding": current}

        prior_package = payload.active_evidence_package_binding
        prior_version = None
        if prior_package is not None:
            runtime.validate_evidence_package_binding_current(
                prior_package,
                require_source_fresh=False,
            )
            prior_version = prior_package.version_ref

        progress = runtime.resolve_progress(
            run_id=round_ref.run_id,
            section_id=round_ref.section_id,
            attempt_scope_ref=round_ref.attempt_scope_ref,
            attempt_id=round_ref.attempt_id,
        )
        tool_refs = (
            tuple(retrieval.tool_operation_refs)
            if retrieval is not None
            else ()
        )
        budget_refs = (
            tuple(retrieval.budget_receipt_refs)
            if retrieval is not None
            else ()
        )
        record = ResearchRoundRecordSchema(
            round_ref=round_ref,
            round_ordinal=progress.committed_round_count + 1,
            authorization_binding=authorization,
            prior_committed_round_ref=progress.last_committed_round_ref,
            source_plan_version_ref=plan_binding.plan_version_ref,
            execution_outcome=ResearchRoundExecutionOutcome.TECHNICAL_FAILURE,
            final_evidence_bundle_binding=None,
            package_progress=ResearchPackageProgressSchema(
                prior_evidence_package_version_ref=prior_version,
                resulting_evidence_package_version_ref=None,
                material_package_change="UNAVAILABLE_DUE_TO_TECHNICAL_FAILURE",
            ),
            tool_operation_refs=tool_refs,
            budget_receipt_refs=budget_refs,
        )
        committed = runtime.commit_round(
            round_candidate=record,
            authorization_context=authorization_context,
            operation_ref=runtime.round_operation_ref(round_ref),
            source_plan_binding=plan_binding,
            resulting_package_binding=None,
            commit_guard_context=lineage,
            expected_progress_revision=progress.progress_revision,
            expected_committed_round_count=progress.committed_round_count,
            expected_last_committed_round_ref=progress.last_committed_round_ref,
        )
        memory = getattr(self.coordinator, "memory_composition", None)
        if memory is not None:
            memory.project_round(round_ref)
        return {"current_research_round_binding": committed.binding}

    def _invoke_research_agent(
        self,
        payload: SectionExecutionGraphPayloadSchema,
        *,
        node_name: str,
        research_mode: str,
        retrieval_succeeded: bool,
        retrieval_scope: str | None = None,
        shared_state: GraphStateSchema | None = None,
        query: str = "",
        call_suffix: str = "",
        citation_registry: list | None = None,
        recovery_candidate: bool = False,
    ) -> SectionExecutionGraphPayloadSchema:
        adapter = self.section_agent_adapter
        if adapter is None:
            return payload
        section_state = payload.section_state
        if section_state is None or payload.section_id is None:
            raise ValueError("ResearchAgent requires initialized section state")
        invocation_accepted_evidence_ids = self._active_evidence_ids(payload)
        invocation_evidence_contract_sha256 = self._evidence_contract_sha(payload)
        canonical_round_ref = None
        canonical_evidence_requirements = None
        canonical_evidence_requirement_ids: tuple[str, ...] = ()
        if self.research_runtime is not None:
            plan_binding = payload.canonical_plan_binding
            lineage = payload.active_execution_lineage
            authorization = payload.research_authorization_binding
            missing = [
                name
                for name, value in (
                    ("canonical_plan_binding", plan_binding),
                    ("active_execution_lineage", lineage),
                    ("research_authorization_binding", authorization),
                )
                if value is None
            ]
            if missing:
                raise RuntimeError(
                    "canonical Research start prerequisites are missing: "
                    + ", ".join(missing)
                )
            assert plan_binding is not None
            assert lineage is not None
            assert authorization is not None
            canonical_round_ref = self.research_runtime.authorize_round_ref(
                run_id=payload.run_id,
                section_id=payload.section_id,
                source_plan_binding=plan_binding,
                authorization_binding=authorization,
                active_attempt=lineage,
            )
            (
                canonical_evidence_requirements,
                canonical_evidence_requirement_ids,
            ) = self.research_runtime.resolve_research_requirements(plan_binding)
        try:
            outcome = adapter.research(
                node_name=node_name,
                task_id=payload.task_id,
                run_id=payload.run_id,
                project_input=payload.project_input,
                section_plan=payload.section_plan,
                section_state=section_state,
                section_id=payload.section_id,
                section_title=str(
                    payload.section_title or payload.section_plan.section_title
                ),
                retrieval_scope=str(retrieval_scope or payload.active_scope),
                research_mode=research_mode,
                research_round=max(1, payload.recovery_count + 1),
                citation_required=payload.citation_required,
                assessment=bundle_first_assessment_projection(
                    payload.active_evidence_bundle,
                ),
                accepted_evidence_ids=invocation_accepted_evidence_ids,
                rejected_evidence_ids=[],
                evidence_contract_sha256=invocation_evidence_contract_sha256,
                tool_call_ids=list(payload.tool_call_ids),
                retrieval_succeeded=retrieval_succeeded,
                prior_evidence_package_ref=(
                    payload.active_evidence_package_binding.version_ref.content_ref
                    if payload.active_evidence_package_binding is not None
                    else section_state.research.evidence_package_ref
                ),
                budget=dict(payload.budget_state),
                shared_state=shared_state,
                query=query,
                call_suffix=call_suffix,
                citation_registry=list(citation_registry or []),
                research_round_ref=canonical_round_ref,
                active_execution_lineage=payload.active_execution_lineage,
                canonical_evidence_requirements=canonical_evidence_requirements,
                canonical_evidence_requirement_ids=(
                    canonical_evidence_requirement_ids
                ),
            )
        except MainlineResearchAgentFailure as exc:
            # Canonical Research progress records the authorized occurrence's
            # terminal technical fact before the error is projected upstream.
            # No EvidenceBundle or resulting EvidencePackage is manufactured.
            self._commit_canonical_research_failure(
                payload,
                retrieval=exc.retrieval,
            )
            raise
        self._validate_research_evidence_projection(
            payload,
            outcome=outcome,
            invocation_accepted_evidence_ids=invocation_accepted_evidence_ids,
            invocation_evidence_contract_sha256=(
                invocation_evidence_contract_sha256
            ),
        )
        identity_audit = self._validate_agent_result_identity(
            payload,
            section_state=section_state,
            metadata=dict(outcome.payload.agent_metadata),
        )
        metadata = dict(payload.retrieval_metadata)
        metadata.update(
            {
                "research_agent_identity": identity_audit,
                "research_agent_action": (
                    outcome.payload.evidence_summary.recommended_action.value
                    if outcome.payload.evidence_summary.recommended_action is not None
                    else None
                ),
                "research_agent_status": (
                    outcome.payload.evidence_summary.research_status.value
                    if outcome.payload.evidence_summary.research_status is not None
                    else None
                ),
                "research_agent_invocation_id": (
                    outcome.payload.agent_metadata.get("invocation_id")
                ),
                "research_agent_fake_reasoner": bool(
                    outcome.payload.agent_metadata.get("fake_reasoner")
                ),
                "research_agent_tool_gateway": (
                    outcome.payload.agent_metadata.get("tool_gateway")
                ),
            }
        )
        updates: dict[str, Any] = {
            "section_state": outcome.section_state,
            "research_agent_payloads": self._append_agent_payload(
                list(payload.research_agent_payloads),
                outcome.payload,
                metadata_field="agent_metadata",
            ),
            "retrieval_metadata": metadata,
        }
        retrieval = outcome.retrieval
        if retrieval is not None:
            writer_gate = (
                None
                if self._pre_write_ready and not recovery_candidate
                else self.coordinator._writer_gate_binding(
                    dict(retrieval.normalized),
                    evidence_bundle=retrieval.evidence_bundle,
                    section_id=str(payload.section_id),
                    section_title=str(
                        payload.section_title or payload.section_plan.section_title
                    ),
                    section_order=(
                        int(payload.section_order)
                        if payload.section_order is not None
                        else None
                    ),
                    document_id=payload.document_id,
                    run_id=payload.run_id,
                )
            )
            tool_call_ids = list(payload.tool_call_ids)
            if retrieval.retrieval_trace_id:
                tool_call_ids.append(retrieval.retrieval_trace_id)
            metadata.update(
                {
                    "research_tool_retrieval_success": retrieval.success,
                    "research_tool_error": retrieval.error_message,
                    (
                        "recovery_candidate_writer_gate_bound"
                        if recovery_candidate
                        else "writer_gate_bound"
                    ): bool(writer_gate is not None),
                }
            )
            updates.update(
                {
                    "tool_call_ids": list(dict.fromkeys(tool_call_ids)),
                    "retrieval_metadata": metadata,
                }
            )
            if retrieval.success:
                # History is lightweight and may record rejected attempts.  Citation
                # registry/chunk projection commits are transactional: recovery
                # candidates are promoted only after Application acceptance below.
                updates["rag_outputs"] = [
                    *payload.rag_outputs,
                    SchemeEvidenceService.retrieval_history_projection(
                        retrieval.normalized,
                        scope=str(retrieval_scope or payload.active_scope),
                        evidence_bundle=retrieval.evidence_bundle,
                    ),
                ]
                if not recovery_candidate:
                    updates.update(
                        {
                            "citation_registry": list(retrieval.citation_registry),
                            "retrieved_chunks": [
                                *payload.retrieved_chunks,
                                *retrieval.chunks,
                            ],
                        }
                    )
                candidate_scope = str(retrieval_scope or payload.active_scope)
                if recovery_candidate:
                    (
                        candidate_accepted,
                        current_generation_permitted,
                        candidate_generation_permitted,
                        rejection_reason,
                    ) = self._recovery_candidate_decision(
                        current_writer_gate=payload.active_writer_gate,
                        candidate_writer_gate=writer_gate,
                        candidate_context=retrieval.context,
                    )
                    metadata.update(
                        {
                            "recovery_candidate_accepted": candidate_accepted,
                            "recovery_candidate_generation_permitted": (
                                candidate_generation_permitted
                            ),
                            "recovery_candidate_current_generation_permitted": (
                                current_generation_permitted
                            ),
                            "recovery_candidate_scope": candidate_scope,
                            "recovery_candidate_chunk_count": len(retrieval.chunks),
                            "recovery_candidate_citation_count": len(retrieval.citations),
                        }
                    )
                    acceptance_key = (
                        "corrective_recovery_accepted"
                        if research_mode == "corrective_retrieval"
                        else "self_rag_recovery_accepted"
                    )
                    rejection_key = (
                        "corrective_recovery_rejected"
                        if research_mode == "corrective_retrieval"
                        else "self_rag_recovery_rejected"
                    )
                    metadata[acceptance_key] = candidate_accepted
                    if not candidate_accepted:
                        metadata[rejection_key] = rejection_reason
                        metadata["recovery_candidate_rejection_reason"] = rejection_reason
                        metadata["recovery_candidate_retained_scope"] = payload.active_scope
                        metadata["recovery_candidate_retained_query"] = payload.active_query
                        metadata["recovery_candidate_section_state_retained"] = True
                        # A rejected candidate may remain in Research payload/trace,
                        # but it must not project its blocked/insufficient lifecycle
                        # into the accepted Section state.
                        updates["section_state"] = payload.section_state
                    if candidate_accepted:
                        metadata["writer_gate_bound"] = bool(writer_gate is not None)
                        updates.update(
                            {
                                "citation_registry": list(retrieval.citation_registry),
                                "retrieved_chunks": [
                                    *payload.retrieved_chunks,
                                    *retrieval.chunks,
                                ],
                                "active_evidence_bundle": retrieval.evidence_bundle,
                                "active_context": retrieval.context,
                                "active_chunks": list(retrieval.chunks),
                                "active_citations": list(retrieval.citations),
                                "active_writer_gate": writer_gate,
                                "active_query": query,
                                "active_scope": candidate_scope,
                            }
                        )
                elif retrieval.context is not None:
                    # A technically successful zero-result retrieval is a valid
                    # canonical empty Evidence handoff. Context text is a
                    # projection, not Evidence-currentness authority.
                    updates.update(
                        {
                            "active_evidence_bundle": retrieval.evidence_bundle,
                            "active_context": retrieval.context,
                            "active_chunks": list(retrieval.chunks),
                            "active_citations": list(retrieval.citations),
                            "active_writer_gate": writer_gate,
                            "active_query": query,
                            "active_scope": candidate_scope,
                        }
                    )

                canonical_accepted = (
                    not recovery_candidate
                    or bool(metadata.get("recovery_candidate_accepted"))
                )
                if canonical_accepted:
                    canonical_updates = self._commit_canonical_research_result(
                        payload,
                        outcome=outcome,
                        retrieval=retrieval,
                    )
                    updates.update(canonical_updates)
                    package_binding = canonical_updates.get(
                        "active_evidence_package_binding"
                    )
                    round_binding = canonical_updates.get(
                        "current_research_round_binding"
                    )
                    if package_binding is not None:
                        canonical_package_ref = package_binding.version_ref.content_ref
                        metadata["canonical_evidence_package_version_id"] = (
                            package_binding.version_ref.artifact_version_id
                        )
                        metadata["canonical_evidence_package_head_revision"] = (
                            package_binding.resolved_head_revision
                        )
                        projected_state = updates["section_state"]
                        projected_state = projected_state.model_copy(
                            update={
                                "research": projected_state.research.model_copy(
                                    update={
                                        "evidence_package_ref": canonical_package_ref,
                                    },
                                    deep=True,
                                )
                            },
                            deep=True,
                        )
                        updates["section_state"] = projected_state
                        projected_payloads = list(updates["research_agent_payloads"])
                        projected_payloads[-1] = projected_payloads[-1].model_copy(
                            update={"evidence_package_ref": canonical_package_ref},
                            deep=True,
                        )
                        updates["research_agent_payloads"] = projected_payloads
                    if round_binding is not None:
                        metadata["canonical_research_round_id"] = (
                            round_binding.round_ref.research_round_id
                        )
                        metadata["canonical_research_progress_revision"] = (
                            round_binding.resolved_progress_revision
                        )
                        progress = self.research_runtime.resolve_progress(
                            run_id=round_binding.round_ref.run_id,
                            section_id=round_binding.round_ref.section_id,
                            attempt_scope_ref=round_binding.round_ref.attempt_scope_ref,
                            attempt_id=round_binding.round_ref.attempt_id,
                        )
                        projected_state = updates["section_state"]
                        projected_state = projected_state.model_copy(
                            update={
                                "research": projected_state.research.model_copy(
                                    update={
                                        "research_rounds": progress.committed_round_count,
                                    },
                                    deep=True,
                                )
                            },
                            deep=True,
                        )
                        updates["section_state"] = projected_state
                        projected_payloads = list(updates["research_agent_payloads"])
                        projected_payloads[-1] = projected_payloads[-1].model_copy(
                            update={
                                "research_round": progress.committed_round_count,
                            },
                            deep=True,
                        )
                        updates["research_agent_payloads"] = projected_payloads
                    updates["retrieval_metadata"] = metadata
        return payload.model_copy(update=updates, deep=True)

    def _invoke_writer_agent(
        self,
        payload: SectionExecutionGraphPayloadSchema,
        *,
        node_name: str,
        section,
    ) -> SectionExecutionGraphPayloadSchema:
        adapter = self.section_agent_adapter
        if adapter is None:
            return payload
        section_state = payload.section_state
        if section_state is None:
            raise ValueError("WriterAgent requires initialized section state")
        evidence_ref = (
            payload.active_evidence_package_binding.version_ref.content_ref
            if payload.active_evidence_package_binding is not None
            else section_state.research.evidence_package_ref
            or f"state://section-evidence/{section.section_id}/{payload.active_scope}"
        )
        outcome = adapter.write(
            node_name=node_name,
            task_id=payload.task_id,
            run_id=payload.run_id,
            project_input=payload.project_input,
            section_plan=payload.section_plan,
            section_state=section_state,
            section=section,
            evidence_package_ref=evidence_ref,
            generation_attempt=payload.generation_attempt,
            citation_required=payload.citation_required,
            generation_check=dict(payload.generation_check),
            budget=dict(payload.budget_state),
        )
        identity_audit = self._validate_agent_result_identity(
            payload,
            section_state=section_state,
            metadata=dict(outcome.payload.writer_metadata),
        )
        projected_payload = self.paragraph_pipeline.writer_payload(
            section=section,
            artifacts=list(payload.paragraph_artifacts),
            draft_version=max(1, payload.generation_attempt),
            blocked_paragraph_id=payload.paragraph_blocked_id,
        )
        projected_payload = projected_payload.model_copy(
            update={
                "writer_metadata": {
                    **dict(outcome.payload.writer_metadata),
                    **dict(projected_payload.writer_metadata),
                    "agent_generation_complete": (
                        outcome.payload.generation_complete
                    ),
                    "fixed_projector": "round6_paragraph_checkpoint_projection",
                }
            },
            deep=True,
        )
        metadata = dict(payload.retrieval_metadata)
        metadata.update(
            {
                "writer_agent_identity": identity_audit,
                "writer_agent_status": (
                    projected_payload.writer_metadata.get("agent_status")
                ),
                "writer_agent_invocation_id": (
                    projected_payload.writer_metadata.get("invocation_id")
                ),
                "writer_agent_fake_reasoner": bool(
                    projected_payload.writer_metadata.get("fake_reasoner")
                ),
                "writer_agent_model_call_id": (
                    projected_payload.writer_metadata.get("model_call_id")
                ),
                "paragraph_pipeline_attached": True,
                "paragraph_artifact_count": len(payload.paragraph_artifacts),
            }
        )
        return payload.model_copy(
            update={
                "section_state": outcome.section_state,
                "writer_agent_payloads": self._append_agent_payload(
                    list(payload.writer_agent_payloads),
                    projected_payload,
                    metadata_field="writer_metadata",
                ),
                "retrieval_metadata": metadata,
                "budget_state": dict(outcome.budget_state),
            },
            deep=True,
        )

    def _invoke_verifier_agent(
        self,
        payload: SectionExecutionGraphPayloadSchema,
        *,
        node_name: str,
        section,
    ) -> SectionExecutionGraphPayloadSchema:
        adapter = self.section_agent_adapter
        if adapter is None:
            return payload
        section_state = payload.section_state
        if section_state is None:
            raise ValueError("VerifierAgent requires initialized section state")
        outcome = adapter.verify(
            node_name=node_name,
            task_id=payload.task_id,
            run_id=payload.run_id,
            project_input=payload.project_input,
            section_plan=payload.section_plan,
            section_state=section_state,
            section=section,
            generation_attempt=payload.generation_attempt,
            generation_check=dict(payload.generation_check),
            citation_required=payload.citation_required,
            evidence_blocked=payload.evidence_blocked,
            corrective_retrieval_enabled=payload.corrective_retrieval_enabled,
            budget=dict(payload.budget_state),
        )
        identity_audit = self._validate_agent_result_identity(
            payload,
            section_state=section_state,
            metadata=dict(outcome.payload.decision_metadata),
        )
        metadata = dict(payload.retrieval_metadata)
        metadata.update(
            {
                "verifier_agent_identity": identity_audit,
                "verifier_agent_action": outcome.payload.action.value,
                "verifier_agent_retrieval_route": (
                    outcome.payload.decision_metadata.get("retrieval_route")
                ),
                "verifier_agent_invocation_id": (
                    outcome.payload.decision_metadata.get("invocation_id")
                ),
                "verifier_agent_fake_reasoner": bool(
                    outcome.payload.decision_metadata.get("fake_reasoner")
                ),
                "verifier_agent_model_call_id": (
                    outcome.payload.decision_metadata.get("model_call_id")
                ),
            }
        )
        return payload.model_copy(
            update={
                "section_state": outcome.section_state,
                "verifier_agent_payloads": self._append_agent_payload(
                    list(payload.verifier_agent_payloads),
                    outcome.payload,
                    metadata_field="decision_metadata",
                ),
                "retrieval_metadata": metadata,
                "budget_state": dict(outcome.budget_state),
            },
            deep=True,
        )

    def _request(
        self,
        payload: SectionExecutionGraphPayloadSchema,
        shared_state: GraphStateSchema,
    ) -> SectionExecutionRequestSchema:
        document_context, document_chunks, document_citations, normalized = (
            self._document_projection(payload)
        )
        return SectionExecutionRequestSchema(
            shared_state=shared_state,
            document_id=payload.document_id,
            project_input=payload.project_input,
            section_plan=payload.section_plan,
            structured_facts=list(payload.structured_facts),
            previous_sections=list(payload.previous_sections),
            document_evidence_bundle=payload.document_evidence_bundle,
            document_rag_context=document_context,
            document_retrieved_chunks=list(document_chunks),
            document_citations=list(document_citations),
            document_rag_output=normalized,
            document_tool_call_ids=list(payload.document_tool_call_ids),
            section_retrieval_enabled=payload.section_retrieval_enabled,
            corrective_retrieval_enabled=payload.corrective_retrieval_enabled,
            parallel_isolated=payload.parallel_isolated,
            budget_policy_override=dict(payload.budget_policy_override),
        )

    def _prepare_section(
        self,
        state: SectionExecutionGraphState,
    ) -> dict[str, Any]:
        payload, shared_state = self._restore(state)
        plan = payload.section_plan
        section_id = f"section_{payload.run_id}_{plan.section_order:03d}"
        required = list(
            payload.project_input.generation_requirements.required_sections
            or payload.project_input.output_schema.required_sections
        )
        citation_required_titles = set(
            payload.project_input.generation_requirements.citation_required_sections
        )
        citation_required = bool(
            plan.citation_required or plan.section_title in citation_required_titles
        )
        if not payload.parallel_isolated:
            self.state_writer.set_current_section(
                shared_state,
                section_id=section_id,
                section_title=plan.section_title,
            )
        registry = DocumentCitationRegistry.from_citations(
            payload.citation_registry
        )
        budget = WorkflowBudget.from_policy_metadata(
            {
                **self.generation_quality_metadata,
                **dict(payload.budget_policy_override),
            }
        )
        task_budget = current_task_draining_budget()
        task_budget_snapshot = task_budget.snapshot() if task_budget else {}
        document_context, document_chunks, document_citations, document_output = (
            self._document_projection(payload)
        )
        document_writer_gate = (
            None
            if self._pre_write_ready
            else self.coordinator._writer_gate_binding(
                document_output,
                evidence_bundle=payload.document_evidence_bundle,
                section_id=section_id,
                section_title=plan.section_title,
                section_order=plan.section_order,
                document_id=payload.document_id,
                run_id=payload.run_id,
            )
        )
        section_state = self._initialize_section_state(
            payload,
            section_id=section_id,
        )
        updated = payload.model_copy(
            update={
                "section_id": section_id,
                "section_title": plan.section_title,
                "section_order": plan.section_order,
                "section_state": section_state,
                "total_sections": len(required),
                "citation_required": citation_required,
                "citation_registry": registry.all(),
                "active_evidence_bundle": payload.document_evidence_bundle,
                "active_context": document_context,
                "active_chunks": list(document_chunks),
                "active_citations": list(document_citations),
                "active_query": payload.project_input.user_query,
                "active_scope": "document",
                "active_writer_gate": document_writer_gate,
                "tool_call_ids": list(payload.document_tool_call_ids),
                "retrieval_metadata": {
                    "section_retrieval_enabled": (
                        payload.section_retrieval_enabled
                    ),
                    "citation_required": citation_required,
                    "writer_gate_bound": bool(document_writer_gate is not None),
                },
                "budget_state": {
                    **budget.snapshot(),
                    "task_budget": task_budget_snapshot,
                    "task_draining": bool(
                        task_budget_snapshot.get("draining", False)
                    ),
                },
            },
            deep=True,
        )
        # Target sections that do not need an additional Section retrieval still
        # require an exact ResearchRound/EvidencePackage before Pre-write Gate.
        # The already-durable document EvidenceBundle is the material source;
        # this zero-Tool Research occurrence performs no new cognition/retrieval.
        if (
            self.research_runtime is not None
            and not (payload.section_retrieval_enabled and citation_required)
        ):
            bundle = updated.active_evidence_bundle
            if bundle is None:
                raise ValueError(
                    "Target precomputed Research requires canonical document EvidenceBundle"
                )
            precomputed = MainlineResearchRetrieval(
                success=True,
                evidence_bundle=bundle,
                context=document_context,
                chunks=tuple(document_chunks),
                citations=tuple(document_citations),
                citation_registry=tuple(registry.all()),
                normalized=dict(document_output),
                assessment=self._assessment_from_bundle(bundle),
                retrieval_trace_id=(
                    str(bundle.retrieval_trace_id).strip()
                    if bundle.retrieval_trace_id
                    else None
                ),
                canonical_bundle_binding=(
                    self.evidence_service.canonical_bundle_binding(bundle)
                ),
            )
            canonical_updates = self._commit_canonical_research_result(
                updated,
                retrieval=precomputed,
            )
            updated = updated.model_copy(update=canonical_updates, deep=True)
        print(
            f"[Section {plan.section_order}/{len(required)}] START "
            f"{plan.section_title}",
            flush=True,
        )
        emit_workflow_custom_event(
            "section_started",
            node_name="prepare_section",
            section_id=section_id,
            status=ExecutionStatus.RUNNING.value,
            progress=(
                plan.section_order / len(required)
                if required
                else None
            ),
            payload={
                "section_title": plan.section_title,
                "section_order": plan.section_order,
                "total_sections": len(required),
            },
        )
        pre_write_entry = (
            "pre_write_admission"
            if self._pre_write_ready
            else "evidence_gate"
        )
        route = (
            "finalize"
            if payload.budget_exhaustion_resource is not None
            else "retrieve"
            if payload.section_retrieval_enabled and citation_required
            else pre_write_entry
        )
        return self._update(
            updated,
            node_name="prepare_section",
            route=route,
            shared_state=shared_state,
        )

    def _retrieve_section_evidence(
        self,
        state: SectionExecutionGraphState,
    ) -> dict[str, Any]:
        payload, shared_state = self._restore(state)
        title = str(payload.section_title or payload.section_plan.section_title)
        order = int(payload.section_order or payload.section_plan.section_order)
        section_id = str(payload.section_id)
        query = self.query_builder.build(
            payload.project_input,
            title,
            recovery=False,
        )
        print(
            f"[SectionRAG] START section={title} scope=section "
            f"query={query[:100]!r}",
            flush=True,
        )
        emit_workflow_custom_event(
            "retrieval_started",
            node_name="retrieve_section_evidence",
            section_id=section_id,
            payload={"scope": "section", "query": query},
        )
        if (
            self.section_agent_adapter is not None
            and self.section_agent_adapter.uses_research_tool_gateway
        ):
            updated = self._invoke_research_agent(
                payload,
                node_name="retrieve_section_evidence",
                research_mode="initial",
                retrieval_succeeded=False,
                retrieval_scope="section",
                shared_state=shared_state,
                query=query,
                call_suffix=f"section_{order:03d}",
                citation_registry=list(payload.citation_registry),
            )
            success = bool(
                updated.retrieval_metadata.get(
                    "research_tool_retrieval_success"
                )
            )
            metadata = dict(updated.retrieval_metadata)
            metadata.update(
                {
                    "section_retrieval_success": success,
                    "section_citation_count": len(updated.active_citations),
                }
            )
            updated = updated.model_copy(
                update={"retrieval_metadata": metadata},
                deep=True,
            )
            emit_workflow_custom_event(
                "retrieval_completed",
                node_name="retrieve_section_evidence",
                section_id=section_id,
                status=(
                    ExecutionStatus.SUCCESS.value
                    if success
                    else ExecutionStatus.FAILED.value
                ),
                payload={"scope": "section", "query": query},
            )
            print(
                f"[SectionRAG] END   section={title} success={success} "
                f"chunks={len(updated.active_chunks)} "
                f"citations={len(updated.active_citations)}",
                flush=True,
            )
            return self._update(
                updated,
                node_name="retrieve_section_evidence",
                route=(
                    "pre_write_admission"
                    if self._pre_write_ready
                    else "evidence_gate"
                ),
                shared_state=shared_state,
            )
        result = self.evidence_service.retrieve(
            shared_state,
            payload.project_input,
            query=query,
            scope="section",
            section_id=section_id,
            section_title=title,
            call_suffix=f"section_{order:03d}",
        )
        emit_workflow_custom_event(
            "retrieval_completed",
            node_name="retrieve_section_evidence",
            section_id=section_id,
            status=(
                ExecutionStatus.SUCCESS.value
                if result is not None and result.success
                else ExecutionStatus.FAILED.value
            ),
            payload={"scope": "section", "query": query},
        )
        tool_call_ids = list(payload.tool_call_ids)
        metadata = dict(payload.retrieval_metadata)
        registry = DocumentCitationRegistry.from_citations(
            payload.citation_registry
        )
        updates: dict[str, Any] = {}
        if result is not None:
            tool_call_ids.append(result.retrieval_trace_id)
        if result is not None and result.success:
            context, chunks, citations, normalized = (
                self.evidence_service.extract_rag_output(shared_state, result)
            )
            context, chunks, citations, normalized = registry.remap_bundle(
                context=context,
                chunks=chunks,
                citations=citations,
                normalized=normalized,
                scope="section",
                query=query,
            )
            assessment = self._assessment_from_bundle(result)
            writer_gate = (
                None
                if self._pre_write_ready
                else self.coordinator._writer_gate_binding(
                    normalized,
                    evidence_bundle=result,
                    section_id=section_id,
                    section_title=title,
                    section_order=int(payload.section_order or 0) or None,
                    document_id=payload.document_id,
                    run_id=payload.run_id,
                )
            )
            metadata.update(
                {
                    "section_retrieval_success": True,
                    "section_citation_count": len(citations),
                    "writer_gate_bound": bool(writer_gate is not None),
                }
            )
            updates.update(
                {
                    "citation_registry": registry.all(),
                    "retrieved_chunks": [*payload.retrieved_chunks, *chunks],
                    "rag_outputs": [
                        *payload.rag_outputs,
                        SchemeEvidenceService.retrieval_history_projection(
                            normalized,
                            scope="section",
                            evidence_bundle=result,
                        ),
                    ],
                }
            )
            # Successful canonical retrieval replaces the prior Evidence
            # even when its context/chunk/citation projections are empty.
            updates.update(
                {
                    "active_evidence_bundle": result,
                    "active_context": context,
                    "active_chunks": list(chunks),
                    "active_citations": list(citations),
                    "active_writer_gate": writer_gate,
                    "active_query": query,
                    "active_scope": "section",
                }
            )
            print(
                f"[SectionRAG] END   section={title} success=True "
                f"chunks={len(chunks)} citations={len(citations)}",
                flush=True,
            )
        else:
            metadata.update(
                {
                    "section_retrieval_success": False,
                    "section_retrieval_error": (
                        result.error_message if result else "not_configured"
                    ),
                }
            )
            print(
                f"[SectionRAG] END   section={title} success=False "
                "fallback=document_evidence",
                flush=True,
            )
        updates.update(
            {
                "tool_call_ids": list(dict.fromkeys(tool_call_ids)),
                "retrieval_metadata": metadata,
            }
        )
        updated = payload.model_copy(update=updates, deep=True)
        updated = self._invoke_research_agent(
            updated,
            node_name="retrieve_section_evidence",
            research_mode="initial",
            retrieval_succeeded=bool(result is not None and result.success),
        )
        return self._update(
            updated,
            node_name="retrieve_section_evidence",
            route=(
                "pre_write_admission"
                if self._pre_write_ready
                else "evidence_gate"
            ),
            shared_state=shared_state,
        )

    def _evaluate_evidence_gate(
        self,
        state: SectionExecutionGraphState,
    ) -> dict[str, Any]:
        payload, shared_state = self._restore(state)
        if self.section_agent_adapter is not None and not payload.research_agent_payloads:
            payload = self._invoke_research_agent(
                payload,
                node_name="evaluate_evidence_gate",
                research_mode="document_context",
                retrieval_succeeded=True,
            )
        research_action = (
            payload.research_agent_payloads[-1]
            .evidence_summary.recommended_action
            if payload.research_agent_payloads
            else None
        )
        writer_gate = payload.active_writer_gate
        writer_enforcement = (
            self.coordinator.resolve_writer_gate_enforcement(writer_gate)
        )
        research_enforcement = (
            self.coordinator.gate_enforcement_controller.resolve(
                gate_passed=(
                    research_action is not ResearchRecommendedAction.HUMAN_REVIEW
                )
            )
        )
        blocked = bool(
            writer_enforcement.should_block or research_enforcement.should_block
        )
        metadata = dict(payload.retrieval_metadata)
        metadata["gate_enforcement"] = {
            "writer_gate": writer_enforcement.as_metadata(gate_id="G06/G11"),
            "research_review": research_enforcement.as_metadata(
                gate_id="research_human_review"
            ),
        }
        updates: dict[str, Any] = {
            "evidence_blocked": blocked,
            "retrieval_metadata": metadata,
        }
        route = "generate"
        if blocked:
            title = str(payload.section_title)
            block_reason = (
                "writer_gate_missing"
                if writer_gate is None
                else (
                    "legacy_writer_artifact_incomplete"
                    if writer_gate.legacy_artifact_incomplete
                    else (
                        "writer_human_review_required"
                        if writer_gate.gate_decision.human_review_required
                        else "writer_generation_blocked"
                    )
                )
            )
            metadata.update(
                {
                    "normal_generation_blocked": True,
                    "generation_block_reason": block_reason,
                }
            )
            if writer_gate is not None:
                section = self.section_generation_service.build_gate_blocked_section(
                    shared_state,
                    project_input=payload.project_input,
                    section_title=title,
                    section_order=int(payload.section_order or 0),
                    rag_context=self._active_context_projection(payload),
                    citations=list(payload.active_citations),
                    writer_gate_binding=writer_gate,
                )
            else:
                section = self.section_generation_service.build_insufficient_evidence_section(
                    shared_state,
                    project_input=payload.project_input,
                    section_title=title,
                    section_order=int(payload.section_order or 0),
                    rag_context=self._active_context_projection(payload),
                    citations=list(payload.active_citations),
                    assessment={
                        **bundle_first_assessment_projection(
                            payload.active_evidence_bundle,
                        ),
                        "status": "not_assessed",
                        "reason": "final writer gate binding is missing",
                    },
                )
                section.extra = {
                    **dict(section.extra or {}),
                    "generation_block_reason": block_reason,
                    "writer_execution_status": "not_assessed",
                    "formal_deliverable_eligible": False,
                }
            updates.update(
                {
                    "section": section,
                    "generation_check": {},
                    "retrieval_metadata": metadata,
                }
            )
            route = "finalize"
            print(
                f"[WriterGate] BLOCK section={title} reason={block_reason}",
                flush=True,
            )
        updated = payload.model_copy(update=updates, deep=True)
        return self._update(
            updated,
            node_name="evaluate_evidence_gate",
            route=route,
            shared_state=shared_state,
        )

    def _evaluate_pre_write_admission(
        self,
        state: SectionExecutionGraphState,
    ) -> dict[str, Any]:
        """Publish canonical Requirements/FactAssessment/Decision by value."""

        payload, shared_state = self._restore(state)
        evidence_bundle, exact_requirements = self._canonical_pre_write_sources(
            payload
        )
        requirements, assessment, decision = (
            self.coordinator.evaluate_pre_write_admission(
                evidence_bundle,
                requirements=exact_requirements,
                task_id=shared_state.task_id,
                run_id=payload.run_id,
            )
        )
        subject, write_instance_id = self._pre_write_identity(payload)
        trace_emitter = self._pre_write_trace_emitter()
        if trace_emitter is not None:
            trace_emitter.emit_admission_decision(
                task_id=shared_state.task_id,
                run_id=payload.run_id,
                writer_subject_ref=subject,
                write_instance_id=write_instance_id,
                active_evidence_bundle_sha256=evidence_bundle_sha256(evidence_bundle),
                fact_assessment_ref=self._fact_assessment_ref(assessment),
                decision_ref=self._decision_ref(decision),
                assessment_lifecycle_state=assessment.assessment_state,
                admission_outcome=decision.admission_outcome,
            )
        updated = payload.model_copy(
            update={
                "active_evidence_bundle": evidence_bundle,
                "active_evidence_requirements_snapshot": requirements,
                "active_writer_admission_fact_assessment": assessment,
                "active_writer_admission_decision": decision,
                "active_writer_gate_binding": None,
                "recovery_runtime_state": None,
                "current_enforcement_result": None,
                "current_recovery_control_facts": None,
                "current_pre_write_route_decision": None,
                "pending_writer_generation_invocation": None,
                "writer_logical_execution_key": None,
                "writer_execution_ref": None,
                "writer_generation_invocation_ref": None,
            },
            deep=True,
        )
        return self._update(
            updated,
            node_name="evaluate_pre_write_admission",
            route="pre_write_binding",
            shared_state=shared_state,
        )

    def _bind_pre_write_gate(
        self,
        state: SectionExecutionGraphState,
    ) -> dict[str, Any]:
        """Publish the Binder-owned immutable Binding in the same transition."""

        payload, shared_state = self._restore(state)
        subject, write_instance_id = self._pre_write_identity(payload)
        evidence_bundle = payload.active_evidence_bundle
        requirements = payload.active_evidence_requirements_snapshot
        decision = payload.active_writer_admission_decision
        if evidence_bundle is None or requirements is None or decision is None:
            raise ValueError("pre-write Binding inputs are incomplete")
        binding, applicability = self.coordinator.bind_pre_write_gate(
            writer_subject_ref=subject,
            write_instance_id=write_instance_id,
            decision=decision,
            evidence_bundle=evidence_bundle,
            requirements=requirements,
        )
        if applicability.binding != binding or applicability.decision != decision:
            raise ValueError("new canonical Binding is not current/applicable")
        trace_emitter = self._pre_write_trace_emitter()
        if trace_emitter is not None:
            decision_ref = self._decision_ref(decision)
            binding_ref = self._binding_ref(binding)
            trace_emitter.emit_binding_committed(
                task_id=shared_state.task_id,
                run_id=payload.run_id,
                writer_subject_ref=subject,
                write_instance_id=write_instance_id,
                decision_ref=decision_ref,
                binding_ref=binding_ref,
            )
            trace_emitter.emit_binding_applicability(
                task_id=shared_state.task_id,
                run_id=payload.run_id,
                writer_subject_ref=subject,
                write_instance_id=write_instance_id,
                binding_ref=binding_ref,
                binding_applicability_state=applicability.state,
                resolved_decision_ref=decision_ref,
            )
        updated = payload.model_copy(
            update={"active_writer_gate_binding": binding},
            deep=True,
        )
        return self._update(
            updated,
            node_name="bind_pre_write_gate",
            route="pre_write_enforcement",
            shared_state=shared_state,
        )

    def _evaluate_pre_write_enforcement(
        self,
        state: SectionExecutionGraphState,
    ) -> dict[str, Any]:
        """Delegate typed Enforcement and RecoveryControl production."""

        payload, shared_state = self._restore(state)
        subject, write_instance_id = self._pre_write_identity(payload)
        chain = self._pre_write_chain(payload)
        applicability = self._pre_write_applicability(payload)
        enforcement = self.coordinator.evaluate_pre_write_enforcement(
            applicability=applicability,
            decision=chain.decision,
        )
        runtime_state = payload.recovery_runtime_state
        if runtime_state is None:
            runtime_state = self.coordinator.initialize_pre_write_recovery(
                writer_subject_ref=subject,
                write_instance_id=write_instance_id,
            )
        recovery_control = self.coordinator.evaluate_pre_write_recovery_control(
            chain=chain,
            applicability=applicability,
            runtime_state=runtime_state,
        )
        trace_emitter = self._pre_write_trace_emitter()
        if trace_emitter is not None:
            trace_emitter.emit_binding_applicability(
                task_id=shared_state.task_id,
                run_id=payload.run_id,
                writer_subject_ref=subject,
                write_instance_id=write_instance_id,
                binding_ref=self._binding_ref(chain.binding),
                binding_applicability_state=applicability.state,
                resolved_decision_ref=self._decision_ref(chain.decision),
            )
            trace_emitter.emit_enforcement(
                task_id=shared_state.task_id,
                run_id=payload.run_id,
                writer_subject_ref=subject,
                write_instance_id=write_instance_id,
                decision_ref=enforcement.decision_ref,
                binding_ref=enforcement.binding_ref,
                binding_applicability_state=applicability.state,
                effective_enforcement_mode=enforcement.mode,
                enforcement_basis=enforcement.enforcement_basis,
                action=enforcement.action,
                would_block_in_strict=enforcement.would_block_in_strict,
                runtime_config_contract_version="gate_v2_runtime_config_v1",
            )
        updated = payload.model_copy(
            update={
                "recovery_runtime_state": runtime_state,
                "current_enforcement_result": enforcement,
                "current_recovery_control_facts": recovery_control,
                "current_pre_write_route_decision": None,
                "pending_writer_generation_invocation": None,
                "writer_logical_execution_key": None,
                "writer_execution_ref": None,
                "writer_generation_invocation_ref": None,
            },
            deep=True,
        )
        return self._update(
            updated,
            node_name="evaluate_pre_write_enforcement",
            route="pre_write_route",
            shared_state=shared_state,
        )

    def _commit_pre_write_route(
        self,
        state: SectionExecutionGraphState,
    ) -> dict[str, Any]:
        """Produce or revalidate the sole current RouteDecision before dispatch."""

        payload, shared_state = self._restore(state)
        subject, write_instance_id = self._pre_write_identity(payload)
        chain = self._pre_write_chain(payload)
        applicability = self._pre_write_applicability(payload)
        enforcement = payload.current_enforcement_result
        recovery_control = payload.current_recovery_control_facts
        if enforcement is None or recovery_control is None:
            raise ValueError("pre-write Route inputs are incomplete")
        route_decision = self.coordinator.commit_pre_write_route(
            writer_subject_ref=subject,
            write_instance_id=write_instance_id,
            decision=chain.decision,
            binding=chain.binding,
            applicability=applicability,
            enforcement=enforcement,
            recovery_control=recovery_control,
            route_decision=payload.current_pre_write_route_decision,
        )
        trace_emitter = self._pre_write_trace_emitter()
        if trace_emitter is not None:
            trace_emitter.emit_route_decision(
                task_id=shared_state.task_id,
                run_id=payload.run_id,
                writer_subject_ref=subject,
                write_instance_id=write_instance_id,
                route_decision_ref=self._route_ref(route_decision),
                decision_ref=self._decision_ref(chain.decision),
                binding_ref=self._binding_ref(chain.binding),
                selected_route=route_decision.selected_route,
                recovery_state_revision=recovery_control.recovery_state_revision,
                recovery_control_facts_ref=self._recovery_control_ref(recovery_control),
            )
        updates: dict[str, Any] = {
            "current_pre_write_route_decision": route_decision,
        }
        if route_decision.selected_route is PreWriteRoute.RECOVERY:
            route = "pre_write_recovery_dispatch"
        else:
            prepared = self.coordinator.prepare_pre_write_writer(
                route_decision,
                writer_subject_ref=subject,
                write_instance_id=write_instance_id,
                decision=chain.decision,
                binding=chain.binding,
                applicability=applicability,
                enforcement=enforcement,
                recovery_control=recovery_control,
                shared_state=shared_state,
                project_input=payload.project_input,
                evidence_bundle=chain.evidence_bundle,
                section_title=str(payload.section_title),
                section_order=int(payload.section_order or 0),
                structured_facts=list(payload.structured_facts),
                previous_sections=list(payload.previous_sections),
                canonical_plan_binding=payload.canonical_plan_binding,
                active_evidence_package_binding=(
                    payload.active_evidence_package_binding
                ),
            )
            updates.update(
                {
                    "pending_writer_generation_invocation": prepared.invocation,
                    "writer_logical_execution_key": (
                        prepared.logical_execution_key
                    ),
                }
            )
            route = "pre_write_writer"
        updated = payload.model_copy(update=updates)
        return self._update(
            updated,
            node_name="commit_pre_write_route",
            route=route,
            shared_state=shared_state,
        )

    @staticmethod
    def _reservation_payload(
        reservation: RecoveryRoundReservation,
    ) -> dict[str, Any]:
        return {
            "target": reservation.target.model_dump(mode="json", round_trip=True),
            "occurrence_id": reservation.occurrence_id,
            "semantic_fingerprint": reservation.semantic_fingerprint,
            "state_before": reservation.state_before.model_dump(
                mode="json", round_trip=True
            ),
            "state_after_reservation": (
                reservation.state_after_reservation.model_dump(
                    mode="json", round_trip=True
                )
            ),
        }

    @staticmethod
    def _restore_reservation(value: dict[str, Any]) -> RecoveryRoundReservation:
        from apps.enterprise_document.schemas.pre_write_control import (
            RecoveryRuntimeStateSchema,
            RecoveryTargetProjection,
        )

        return RecoveryRoundReservation(
            target=RecoveryTargetProjection.model_validate(value["target"]),
            occurrence_id=str(value["occurrence_id"]),
            semantic_fingerprint=str(value["semantic_fingerprint"]),
            state_before=RecoveryRuntimeStateSchema.model_validate(
                value["state_before"]
            ),
            state_after_reservation=RecoveryRuntimeStateSchema.model_validate(
                value["state_after_reservation"]
            ),
        )

    @staticmethod
    def _claim_payload(claim: RecoveryDispatchClaim) -> dict[str, Any]:
        return {
            "status": claim.status.value,
            "occurrence_id": claim.occurrence_id,
            "semantic_fingerprint": claim.semantic_fingerprint,
            "owner_id": claim.owner_id,
            "claim_token": claim.claim_token,
            "result_ref": claim.result_ref,
        }

    @staticmethod
    def _restore_claim(value: dict[str, Any]) -> RecoveryDispatchClaim:
        return RecoveryDispatchClaim(
            status=RecoveryDispatchStatus(str(value["status"])),
            occurrence_id=str(value["occurrence_id"]),
            semantic_fingerprint=str(value["semantic_fingerprint"]),
            owner_id=str(value["owner_id"]),
            claim_token=(
                str(value["claim_token"])
                if value.get("claim_token") is not None
                else None
            ),
            result_ref=(
                str(value["result_ref"])
                if value.get("result_ref") is not None
                else None
            ),
        )

    def _dispatch_pre_write_recovery(
        self,
        state: SectionExecutionGraphState,
    ) -> dict[str, Any]:
        """Reserve one logical Recovery round before any physical dispatch."""

        payload, shared_state = self._restore(state)
        chain = self._pre_write_chain(payload)
        applicability = self._pre_write_applicability(payload)
        runtime_state = payload.recovery_runtime_state
        route_decision = payload.current_pre_write_route_decision
        enforcement = payload.current_enforcement_result
        recovery_control = payload.current_recovery_control_facts
        if (
            runtime_state is None
            or route_decision is None
            or enforcement is None
            or recovery_control is None
        ):
            raise ValueError("Recovery dispatch inputs are incomplete")
        if route_decision.selected_route is not PreWriteRoute.RECOVERY:
            raise ValueError("only RECOVERY Route may reserve Recovery dispatch")
        self.coordinator.commit_pre_write_route(
            writer_subject_ref=route_decision.writer_subject_ref,
            write_instance_id=route_decision.write_instance_id,
            decision=chain.decision,
            binding=chain.binding,
            applicability=applicability,
            enforcement=enforcement,
            recovery_control=recovery_control,
            route_decision=route_decision,
        )
        controller = self.coordinator.require_pre_write_services().recovery_controller
        reservation = controller.prepare_round_reservation(
            chain=chain,
            applicability=applicability,
            runtime_state=runtime_state,
        )
        trace_emitter = self._pre_write_trace_emitter()
        if trace_emitter is not None:
            reserved_state = reservation.state_after_reservation
            trace_emitter.emit_recovery(
                task_id=shared_state.task_id,
                run_id=payload.run_id,
                writer_subject_ref=reserved_state.writer_subject_ref,
                write_instance_id=reserved_state.write_instance_id,
                decision_ref=reservation.target.decision_ref,
                binding_ref=reservation.target.binding_ref,
                fact_assessment_ref=reservation.target.fact_assessment_ref,
                requirements_sha256=reservation.target.requirements_sha256,
                recovery_state_revision=reserved_state.recovery_state_revision,
                recovery_rounds_used=reserved_state.recovery_rounds_used,
                recovery_rounds_limit=reserved_state.recovery_rounds_limit,
                consecutive_no_progress_rounds=reserved_state.consecutive_no_progress_rounds,
                no_progress_rounds_limit=reserved_state.no_progress_rounds_limit,
                recovery_config_contract_version="gate_recovery_config_v1",
                logical_dispatch_occurrence=reservation.occurrence_id,
                phase="start",
                last_round_outcome=reserved_state.last_round_outcome,
            )
        pending = {
            "phase": "reserved",
            "reservation": self._reservation_payload(reservation),
        }
        updated = payload.model_copy(
            update={
                "recovery_runtime_state": reservation.state_after_reservation,
                "pending_recovery_dispatch": pending,
            },
            deep=True,
        )
        return self._update(
            updated,
            node_name="dispatch_pre_write_recovery",
            route="pre_write_recovery_execute",
            shared_state=shared_state,
        )

    def _execute_canonical_gate_recovery_research(
        self,
        payload: SectionExecutionGraphPayloadSchema,
        *,
        shared_state: GraphStateSchema,
        reservation: RecoveryRoundReservation,
        source_chain: RecoveryChainSnapshot,
        query: str,
        publish_committed_failure: Callable[[_CommittedGateResearchFailureProjection], None] | None = None,
    ):
        """Execute one Gate-owned Recovery dispatch through canonical M4 Research."""

        runtime = self.research_runtime
        if runtime is None:
            raise RuntimeError("canonical Gate recovery requires ResearchRuntime")
        adapter = self.section_agent_adapter
        if adapter is None:
            raise RuntimeError("canonical Gate recovery requires ResearchAgent adapter")
        route_decision = payload.current_pre_write_route_decision
        plan_binding = payload.canonical_plan_binding
        lineage = payload.active_execution_lineage
        section_state = payload.section_state
        if (
            route_decision is None
            or plan_binding is None
            or lineage is None
            or section_state is None
        ):
            raise ValueError(
                "canonical Gate recovery requires Route, Plan, Attempt, and SectionState"
            )
        if route_decision.selected_route is not PreWriteRoute.RECOVERY:
            raise ValueError("Gate recovery Research requires committed RECOVERY route")

        route_ref = self._route_ref(route_decision)
        canonical_route = (
            self.coordinator.require_pre_write_services()
            .route_decision_store.resolve_exact(route_ref)
        )
        if canonical_route != route_decision:
            raise ValueError(
                "Gate recovery Research route ref disagrees with canonical store"
            )

        authorization_context = GateRecoveryResearchAuthorizationContext(
            reservation=reservation, source_chain=source_chain,
        )
        authorization = ResearchRoundAuthorizationBindingSchema(
            authorization_ref=reservation.occurrence_id,
            authorization_kind="GATE_RECOVERY_RESEARCH",
            route_decision_ref=route_ref,
            gate_recovery_dispatch_occurrence_ref=reservation.occurrence_id,
            gate_recovery_state_revision=(
                reservation.state_after_reservation.recovery_state_revision
            ),
        )
        round_ref = runtime.authorize_round_ref(
            run_id=payload.run_id,
            section_id=str(payload.section_id),
            source_plan_binding=plan_binding,
            authorization_binding=authorization,
            active_attempt=lineage,
            authorization_context=authorization_context,
        )
        requirements, requirement_ids = runtime.resolve_research_requirements(
            plan_binding
        )

        # SIF-11 resolver-before-retry: the Gate dispatch occurrence is the
        # Research business identity. A committed same occurrence must be
        # reconstructed from Round/Package/Bundle truth without a second
        # ResearchAgent or RAG call.
        try:
            existing_round = runtime.resolve_round(round_ref)
        except ResearchRoundNotFound:
            existing_round = None
        if existing_round is not None:
            current_round = runtime.resolve_current_round_binding(
                run_id=round_ref.run_id,
                section_id=round_ref.section_id,
                attempt_scope_ref=round_ref.attempt_scope_ref,
                attempt_id=round_ref.attempt_id,
            )
            if current_round is None or current_round.round_ref != round_ref:
                raise RuntimeError(
                    "Gate recovery Research replay is historical rather than current"
                )
            runtime.validate_round_binding_current(current_round)
            if (
                existing_round.execution_outcome
                is ResearchRoundExecutionOutcome.TECHNICAL_FAILURE
            ):
                if publish_committed_failure is not None:
                    publish_committed_failure(_CommittedGateResearchFailureProjection(
                        authorization=existing_round.authorization_binding,
                        round_binding=current_round,
                    ))
                raise RuntimeError(
                    "Gate recovery Research occurrence already terminated technically"
                )
            package_version = (
                existing_round.package_progress.resulting_evidence_package_version_ref
            )
            if package_version is None:
                raise RuntimeError(
                    "committed Gate recovery Research has no EvidencePackage result"
                )
            package_binding = runtime.resolve_current_evidence_package_binding(
                run_id=round_ref.run_id,
                section_id=round_ref.section_id,
            )
            if (
                package_binding is None
                or package_binding.version_ref != package_version
            ):
                raise RuntimeError(
                    "committed Gate recovery Research Package is no longer current"
                )
            package_content = runtime.validate_evidence_package_binding_current(
                package_binding,
                require_source_fresh=True,
            )
            if (
                existing_round.final_evidence_bundle_binding
                != package_content.evidence_bundle_binding
            ):
                raise RuntimeError(
                    "committed Gate recovery Round/Package Bundle lineage disagrees"
                )
            bundle = runtime.resolve_evidence_bundle(
                package_content.evidence_bundle_binding
            )
            context, chunks, citations, normalized = (
                self.evidence_service.extract_rag_output(shared_state, bundle)
            )
            registry = DocumentCitationRegistry.from_citations(
                payload.citation_registry
            )
            context, chunks, citations, normalized = registry.remap_bundle(
                context=context,
                chunks=chunks,
                citations=citations,
                normalized=normalized,
                scope="section_recovery",
                query=query,
            )
            retrieval = MainlineResearchRetrieval(
                success=True,
                evidence_bundle=bundle,
                context=context,
                chunks=tuple(chunks),
                citations=tuple(citations),
                citation_registry=tuple(registry.all()),
                normalized=dict(normalized),
                assessment=bundle_first_assessment_projection(bundle),
                retrieval_trace_id=bundle.retrieval_trace_id,
                canonical_bundle_binding=(
                    package_content.evidence_bundle_binding
                ),
                tool_operation_refs=existing_round.tool_operation_refs,
                budget_receipt_refs=existing_round.budget_receipt_refs,
            )
            return (
                authorization,
                None,
                retrieval,
                requirements,
                {
                    "active_evidence_package_binding": package_binding,
                    "current_research_round_binding": current_round,
                },
            )

        progress = runtime.resolve_progress(
            run_id=round_ref.run_id,
            section_id=round_ref.section_id,
            attempt_scope_ref=round_ref.attempt_scope_ref,
            attempt_id=round_ref.attempt_id,
        )
        prior_bundle = payload.active_evidence_bundle or payload.document_evidence_bundle
        prior_ids = (
            list(prior_bundle.selected_evidence_ids)
            if prior_bundle is not None
            else []
        )
        prior_sha = (
            evidence_bundle_sha256(prior_bundle)
            if prior_bundle is not None
            else None
        )
        research_payload = payload.model_copy(
            update={"research_authorization_binding": authorization},
            deep=True,
        )
        try:
            outcome = adapter.research(
                node_name="execute_pre_write_recovery",
                task_id=payload.task_id,
                run_id=payload.run_id,
                project_input=payload.project_input,
                section_plan=payload.section_plan,
                section_state=section_state,
                section_id=str(payload.section_id),
                section_title=str(payload.section_title),
                retrieval_scope="section_recovery",
                research_mode="gate_recovery_research",
                research_round=progress.committed_round_count + 1,
                citation_required=payload.citation_required,
                assessment=bundle_first_assessment_projection(prior_bundle),
                accepted_evidence_ids=prior_ids,
                rejected_evidence_ids=[],
                evidence_contract_sha256=prior_sha,
                tool_call_ids=list(payload.tool_call_ids),
                retrieval_succeeded=True,
                prior_evidence_package_ref=(
                    payload.active_evidence_package_binding.version_ref.content_ref
                    if payload.active_evidence_package_binding is not None
                    else None
                ),
                budget=dict(payload.budget_state),
                shared_state=shared_state,
                query=query,
                call_suffix=(
                    "gate_recovery_"
                    + reservation.occurrence_id.replace(":", "_")
                ),
                citation_registry=list(payload.citation_registry),
                research_round_ref=round_ref,
                active_execution_lineage=lineage,
                canonical_evidence_requirements=requirements,
                canonical_evidence_requirement_ids=requirement_ids,
            )
        except MainlineResearchAgentFailure as exc:
            failure_updates = self._commit_canonical_research_failure(
                research_payload,
                retrieval=exc.retrieval,
                authorization_context=authorization_context,
            )
            if publish_committed_failure is not None:
                publish_committed_failure(_CommittedGateResearchFailureProjection(
                    authorization=authorization,
                    round_binding=failure_updates["current_research_round_binding"],
                ))
            raise

        self._validate_research_evidence_projection(
            research_payload,
            outcome=outcome,
            invocation_accepted_evidence_ids=prior_ids,
            invocation_evidence_contract_sha256=prior_sha,
        )
        retrieval = outcome.retrieval
        if (
            retrieval is None
            or not retrieval.success
            or retrieval.evidence_bundle is None
        ):
            raise RuntimeError(
                "canonical Gate recovery Research completed without valid Evidence handoff"
            )
        canonical_updates = self._commit_canonical_research_result(
            research_payload,
            outcome=outcome,
            retrieval=retrieval,
            authorization_context=authorization_context,
        )
        return (
            authorization,
            outcome,
            retrieval,
            requirements,
            canonical_updates,
        )

    def _execute_pre_write_recovery(
        self,
        state: SectionExecutionGraphState,
    ) -> dict[str, Any]:
        """Claim, retrieve, and atomically publish a coherent promoted chain."""

        payload, shared_state = self._restore(state)
        pending = payload.pending_recovery_dispatch
        if not isinstance(pending, dict) or pending.get("phase") != "reserved":
            raise ValueError("Recovery execution requires a durable reservation")
        reservation = self._restore_reservation(
            dict(pending["reservation"])
        )
        controller = self.coordinator.require_pre_write_services().recovery_controller
        owner_id = f"section-graph:{payload.run_id}:{payload.section_id}"
        claim = controller.claim_physical_dispatch(
            reservation,
            owner_id=owner_id,
        )
        if not claim.acquired:
            if claim.status is RecoveryDispatchStatus.COMPLETED:
                raise RuntimeError(
                    "Recovery result is durable but candidate checkpoint is unavailable"
                )
            raise RuntimeError("Recovery physical occurrence is already in progress")

        chain = self._pre_write_chain(payload)
        applicability = self._pre_write_applicability(payload)
        committed_failure: _CommittedGateResearchFailureProjection | None = None

        def publish_committed_failure(projection: _CommittedGateResearchFailureProjection) -> None:
            nonlocal committed_failure
            committed_failure = projection

        try:
            query = self.query_builder.build_recovery_target_query(
                reservation.target,
                chain.requirements,
                section_title=str(payload.section_title),
            )
            canonical_research_updates: dict[str, Any] = {}
            research_authorization = None
            research_outcome = None
            if self.research_runtime is not None:
                (
                    research_authorization,
                    research_outcome,
                    retrieval,
                    requirements,
                    canonical_research_updates,
                ) = self._execute_canonical_gate_recovery_research(
                    payload,
                    shared_state=shared_state,
                    reservation=reservation,
                    source_chain=chain,
                    query=query,
                    publish_committed_failure=publish_committed_failure,
                )
                result = retrieval.evidence_bundle
                context = retrieval.context
                chunks = list(retrieval.chunks)
                citations = list(retrieval.citations)
                normalized = dict(retrieval.normalized)
                registry = DocumentCitationRegistry.from_citations(
                    retrieval.citation_registry
                )
                if result is None or context is None:
                    raise RuntimeError(
                        "canonical Gate recovery Research returned incomplete retrieval"
                    )
            else:
                result = self.evidence_service.retrieve(
                    shared_state,
                    payload.project_input,
                    query=query,
                    scope="section_recovery",
                    section_id=str(payload.section_id),
                    section_title=str(payload.section_title),
                    call_suffix=(
                        f"pre_write_recovery_"
                        f"{reservation.state_after_reservation.recovery_rounds_used:03d}"
                    ),
                )
                if result is None or not result.success:
                    raise RuntimeError("pre-write Recovery retrieval failed")
                context, chunks, citations, normalized = (
                    self.evidence_service.extract_rag_output(shared_state, result)
                )
                registry = DocumentCitationRegistry.from_citations(
                    payload.citation_registry
                )
                context, chunks, citations, normalized = registry.remap_bundle(
                    context=context,
                    chunks=chunks,
                    citations=citations,
                    normalized=normalized,
                    scope="section_recovery",
                    query=query,
                )
                requirements = None

            requirements, assessment, decision = (
                self.coordinator.evaluate_pre_write_admission(
                    result,
                    requirements=requirements,
                    supersedes_decision_id=chain.decision.decision_id,
                    task_id=shared_state.task_id,
                    run_id=payload.run_id,
                )
            )
            subject, write_instance_id = self._pre_write_identity(payload)
            binding, transitioned_applicability = (
                self.coordinator.bind_pre_write_gate(
                    writer_subject_ref=subject,
                    write_instance_id=write_instance_id,
                    decision=decision,
                    evidence_bundle=result,
                    requirements=requirements,
                )
            )
            candidate_chain = self.coordinator.pre_write_recovery_chain(
                evidence_bundle=result,
                requirements=requirements,
                assessment=assessment,
                decision=decision,
                binding=binding,
            )
            proposal = controller.prepare_candidate_promotion(
                current_chain=chain,
                candidate_chain=candidate_chain,
                reservation=reservation,
            )
            promotion = controller.finalize_candidate_promotion(
                proposal=proposal,
                transitioned_applicability=transitioned_applicability,
            )
            if not promotion.committed:
                raise RuntimeError("Recovery candidate promotion did not commit")
        except Exception as exc:
            controller.release_physical_dispatch(
                claim,
                reason=type(exc).__name__,
            )
            failure = controller.complete_technical_failure(
                current_chain=chain,
                applicability=applicability,
                reservation=reservation,
            )
            trace_emitter = self._pre_write_trace_emitter()
            if trace_emitter is not None:
                failed_state = failure.runtime_state
                trace_emitter.emit_recovery(
                    task_id=shared_state.task_id,
                    run_id=payload.run_id,
                    writer_subject_ref=failed_state.writer_subject_ref,
                    write_instance_id=failed_state.write_instance_id,
                    decision_ref=self._decision_ref(failure.active_chain.decision),
                    binding_ref=self._binding_ref(failure.active_chain.binding),
                    fact_assessment_ref=self._fact_assessment_ref(
                        failure.active_chain.assessment
                    ),
                    requirements_sha256=(
                        failure.active_chain.requirements.requirements_sha256
                    ),
                    recovery_state_revision=failed_state.recovery_state_revision,
                    recovery_rounds_used=failed_state.recovery_rounds_used,
                    recovery_rounds_limit=failed_state.recovery_rounds_limit,
                    consecutive_no_progress_rounds=(
                        failed_state.consecutive_no_progress_rounds
                    ),
                    no_progress_rounds_limit=failed_state.no_progress_rounds_limit,
                    recovery_config_contract_version="gate_recovery_config_v1",
                    logical_dispatch_occurrence=reservation.occurrence_id,
                    phase="error",
                    last_round_outcome=failed_state.last_round_outcome,
                    exhaustion_state=(
                        failure.recovery_control_facts.exhaustion_reason
                    ),
                    technical_diagnostic={"exception_type": type(exc).__name__},
                )
            updated = payload.model_copy(
                update={
                    **({
                        "research_authorization_binding": committed_failure.authorization,
                        "current_research_round_binding": committed_failure.round_binding,
                    } if committed_failure is not None else {}),
                    "recovery_runtime_state": failure.runtime_state,
                    "current_recovery_control_facts": (
                        failure.recovery_control_facts
                    ),
                    "current_pre_write_route_decision": None,
                    "pending_recovery_dispatch": None,
                },
                deep=True,
            )
            return self._update(
                updated,
                node_name="execute_pre_write_recovery",
                route="pre_write_route",
                shared_state=shared_state,
            )

        trace_emitter = self._pre_write_trace_emitter()
        if trace_emitter is not None:
            active_chain = promotion.active_chain
            trace_emitter.emit_evidence_promotion(
                task_id=shared_state.task_id,
                run_id=payload.run_id,
                writer_subject_ref=active_chain.binding.writer_subject_ref,
                write_instance_id=active_chain.binding.write_instance_id,
                previous_active_evidence_ref={
                    "evidence_bundle_schema_version": chain.evidence_bundle.schema_version,
                    "evidence_bundle_sha256": evidence_bundle_sha256(chain.evidence_bundle),
                },
                new_active_evidence_ref={
                    "evidence_bundle_schema_version": active_chain.evidence_bundle.schema_version,
                    "evidence_bundle_sha256": evidence_bundle_sha256(
                        active_chain.evidence_bundle
                    ),
                },
                new_fact_assessment_ref=self._fact_assessment_ref(
                    active_chain.assessment
                ),
                new_decision_ref=self._decision_ref(active_chain.decision),
                new_binding_ref=self._binding_ref(active_chain.binding),
                recovery_state_revision=promotion.runtime_state.recovery_state_revision,
            )
            promoted_state = promotion.runtime_state
            recovery_facts = promotion.recovery_control_facts
            trace_emitter.emit_recovery(
                task_id=shared_state.task_id,
                run_id=payload.run_id,
                writer_subject_ref=promoted_state.writer_subject_ref,
                write_instance_id=promoted_state.write_instance_id,
                decision_ref=self._decision_ref(active_chain.decision),
                binding_ref=self._binding_ref(active_chain.binding),
                fact_assessment_ref=self._fact_assessment_ref(active_chain.assessment),
                requirements_sha256=active_chain.requirements.requirements_sha256,
                recovery_state_revision=promoted_state.recovery_state_revision,
                recovery_rounds_used=promoted_state.recovery_rounds_used,
                recovery_rounds_limit=promoted_state.recovery_rounds_limit,
                consecutive_no_progress_rounds=(
                    promoted_state.consecutive_no_progress_rounds
                ),
                no_progress_rounds_limit=promoted_state.no_progress_rounds_limit,
                recovery_config_contract_version="gate_recovery_config_v1",
                logical_dispatch_occurrence=reservation.occurrence_id,
                phase="end",
                last_round_outcome=promotion.round_outcome,
                exhaustion_state=(
                    recovery_facts.exhaustion_reason
                    if recovery_facts is not None
                    else None
                ),
            )

        tool_call_ids = list(payload.tool_call_ids)
        if result.retrieval_trace_id:
            tool_call_ids.append(result.retrieval_trace_id)
        recovery_updates: dict[str, Any] = {
            "citation_registry": registry.all(),
            "active_evidence_bundle": result,
            "active_evidence_requirements_snapshot": requirements,
            "active_writer_admission_fact_assessment": assessment,
            "active_writer_admission_decision": decision,
            "active_writer_gate_binding": binding,
            "recovery_runtime_state": promotion.runtime_state,
            "current_enforcement_result": None,
            "current_recovery_control_facts": (
                promotion.recovery_control_facts
            ),
            "current_pre_write_route_decision": None,
            "pending_writer_generation_invocation": None,
            "writer_logical_execution_key": None,
            "writer_execution_ref": None,
            "writer_generation_invocation_ref": None,
            "active_context": context,
            "active_chunks": list(chunks),
            "active_citations": list(citations),
            "active_query": query,
            "active_scope": "section_recovery",
            "tool_call_ids": list(dict.fromkeys(tool_call_ids)),
            "retrieved_chunks": [
                *payload.retrieved_chunks,
                *chunks,
            ],
            "rag_outputs": [
                *payload.rag_outputs,
                SchemeEvidenceService.retrieval_history_projection(
                    normalized,
                    scope="section_recovery",
                    evidence_bundle=result,
                ),
            ],
            "pending_recovery_dispatch": {
                "phase": "candidate_durable",
                "reservation": self._reservation_payload(reservation),
                "claim": self._claim_payload(claim),
                "result_ref": evidence_bundle_sha256(result),
            },
        }
        if self.research_runtime is not None:
            recovery_updates.update(canonical_research_updates)
            recovery_updates["research_authorization_binding"] = (
                research_authorization
            )
            if research_outcome is not None:
                recovery_updates["section_state"] = research_outcome.section_state
                recovery_updates["research_agent_payloads"] = (
                    self._append_agent_payload(
                        list(payload.research_agent_payloads),
                        research_outcome.payload,
                        metadata_field="agent_metadata",
                    )
                )
        updated = payload.model_copy(
            update=recovery_updates,
            deep=True,
        )
        return self._update(
            updated,
            node_name="execute_pre_write_recovery",
            route="pre_write_recovery_complete",
            shared_state=shared_state,
        )

    def _complete_pre_write_recovery_dispatch(
        self,
        state: SectionExecutionGraphState,
    ) -> dict[str, Any]:
        """Complete physical dedupe only after candidate checkpoint publication."""

        payload, shared_state = self._restore(state)
        pending = payload.pending_recovery_dispatch
        if (
            not isinstance(pending, dict)
            or pending.get("phase") != "candidate_durable"
        ):
            raise ValueError("Recovery completion requires a durable candidate")
        claim = self._restore_claim(dict(pending["claim"]))
        controller = self.coordinator.require_pre_write_services().recovery_controller
        controller.complete_physical_dispatch(
            claim,
            result_ref=str(pending["result_ref"]),
        )
        updated = payload.model_copy(
            update={"pending_recovery_dispatch": None},
            deep=True,
        )
        return self._update(
            updated,
            node_name="complete_pre_write_recovery_dispatch",
            route="pre_write_enforcement",
            shared_state=shared_state,
        )

    def _invoke_pre_write_writer(
        self,
        state: SectionExecutionGraphState,
    ) -> dict[str, Any]:
        """Invoke NORMAL/FALLBACK through the same canonical Writer boundary."""

        payload, shared_state = self._restore(state)
        route_decision = payload.current_pre_write_route_decision
        invocation = payload.pending_writer_generation_invocation
        logical_key = payload.writer_logical_execution_key
        if (
            route_decision is None
            or invocation is None
            or logical_key is None
        ):
            raise ValueError("durable Writer handoff is incomplete")
        if route_decision.selected_route is PreWriteRoute.RECOVERY:
            raise ValueError("RECOVERY route cannot invoke Writer")
        chain = self._pre_write_chain(payload)
        applicability = self._pre_write_applicability(payload)
        enforcement = payload.current_enforcement_result
        recovery_control = payload.current_recovery_control_facts
        if enforcement is None or recovery_control is None:
            raise ValueError("Writer currentness inputs are incomplete")
        subject, write_instance_id = self._pre_write_identity(payload)
        route_id = route_decision.route_decision_id
        expected_generation_class = (
            "fallback_write"
            if route_decision.selected_route is PreWriteRoute.FALLBACK
            else "normal_write"
        )
        if (
            invocation.writer_subject_ref != subject
            or invocation.write_instance_id != write_instance_id
            or invocation.route_decision_ref.route_decision_id != route_id
            or invocation.generation_class.value != expected_generation_class
            or logical_key.writer_subject_ref != subject
            or logical_key.write_instance_id != write_instance_id
            or logical_key.route_decision_ref != invocation.route_decision_ref
        ):
            raise ValueError("durable Writer handoff is stale or internally inconsistent")
        trace_emitter = self._pre_write_trace_emitter()
        if trace_emitter is not None:
            target_prompt_lock = dict(
                invocation.unified_writer_input_snapshot["target_prompt_lock"]
            )
            context_package_id = str(target_prompt_lock["context_package_id"])
            context_sha256 = str(target_prompt_lock["context_sha256"])
            trace_emitter.emit_writer_execution(
                task_id=shared_state.task_id,
                run_id=payload.run_id,
                writer_subject_ref=subject,
                write_instance_id=write_instance_id,
                route_decision_ref=invocation.route_decision_ref,
                generation_class=invocation.generation_class,
                evidence_bundle_sha256=(
                    invocation.canonical_evidence_input.evidence_bundle_sha256
                ),
                context_package_id=context_package_id,
                context_sha256=context_sha256,
                writer_logical_execution_key=logical_key,
                phase="start",
            )
        recovered = self.running_invocation_recovery.reconcile_writer(payload)
        try:
            if recovered["outcome"] == "RECOVERED_DURABLE_RESULT":
                # Pure source reads retain the normal consumer's currentness and
                # cancellation fences. They never revive an old worker/control.
                lifecycle = self.running_invocation_recovery.lifecycle_service
                if lifecycle is None:
                    raise ValueError("canonical Writer read handoff requires lifecycle owner")
                lifecycle.assert_attempt_committable(run_id=payload.run_id,
                    attempt_scope_ref=f"section:{payload.section_id}",
                    attempt_id=write_instance_id,
                    input_snapshot_version=payload.active_execution_lineage.input_snapshot_version)
                from ..services.scheme_writer.pre_write_router import PreWriteRouter
                writer_owner = self.coordinator.require_pre_write_services().writer_invocation
                snapshot = PreWriteRouter.authorization_snapshot_sha256(
                    writer_subject_ref=subject, write_instance_id=write_instance_id,
                    decision=chain.decision, binding=chain.binding, applicability=applicability,
                    enforcement=enforcement, recovery_control=recovery_control)
                committed_route = writer_owner.route_decision_store.resolve_exact_for_snapshot(
                    invocation.route_decision_ref, authorization_snapshot_sha256=snapshot)
                if committed_route != route_decision:
                    raise ValueError("recovered Writer Route differs from canonical current route")
                PreWriteRouter.validate_commit(committed_route, writer_subject_ref=subject,
                    write_instance_id=write_instance_id, decision=chain.decision, binding=chain.binding,
                    applicability=applicability, enforcement=enforcement, recovery_control=recovery_control)
                from ..services.scheme_writer.writer_invocation import _restore_context_package_semantic_snapshot
                composer = writer_owner.builder.target_context_composer
                raw_package = invocation.unified_writer_input_snapshot.get("target_context_package")
                if composer is None or raw_package is None:
                    raise ValueError("recovered Writer handoff requires its existing Context currentness owner")
                composer.validate_pre_model_call(_restore_context_package_semantic_snapshot(raw_package),
                                                project_input=payload.project_input)
                checkpoint_current_execution()
                result = recovered["result"]
                section = SchemeSectionSchema.model_validate(result.model_dump(mode="json")["artifact"])
            elif recovered["outcome"] == "DURABLE_FAILURE":
                raise PreWriteWriterExecutionFailed(recovered["result"])
            else:
                if current_durable_recovery() is not None:
                    raise ValueError("INDETERMINATE: unresolved native Writer cannot be redispatched")
                result, section = self.coordinator.invoke_pre_write_writer(
                    route_decision,
                    writer_subject_ref=subject,
                    write_instance_id=write_instance_id,
                    decision=chain.decision,
                    binding=chain.binding,
                    applicability=applicability,
                    enforcement=enforcement,
                    recovery_control=recovery_control,
                    shared_state=shared_state,
                    project_input=payload.project_input,
                    evidence_bundle=chain.evidence_bundle,
                    section_title=str(payload.section_title),
                    section_order=int(payload.section_order or 0),
                    structured_facts=list(payload.structured_facts),
                    previous_sections=list(payload.previous_sections),
                    canonical_plan_binding=payload.canonical_plan_binding,
                    active_evidence_package_binding=payload.active_evidence_package_binding,
                )
        except PreWriteWriterExecutionFailed as exc:
            # This bounded exact native read occurs before propagation to C1.
            # Preserve the original owner failure even if its proof is unavailable.
            failure_view = self.running_invocation_recovery.reconcile_writer(payload)
            exc.rec_reconciliation_outcome = failure_view["outcome"]
            # The canonical technical result is already durable in the execution
            # store. Do not create an Artifact, Gate DENY, Recovery, or Fallback.
            if trace_emitter is not None:
                failed_result = exc.result
                trace_emitter.emit_writer_execution(
                    task_id=shared_state.task_id,
                    run_id=payload.run_id,
                    writer_subject_ref=subject,
                    write_instance_id=write_instance_id,
                    route_decision_ref=invocation.route_decision_ref,
                    generation_class=invocation.generation_class,
                    evidence_bundle_sha256=(
                        invocation.canonical_evidence_input.evidence_bundle_sha256
                    ),
                    context_package_id=context_package_id,
                    context_sha256=context_sha256,
                    writer_logical_execution_key=logical_key,
                    phase="error",
                    writer_status=failed_result.status,
                    failure_kind=failed_result.failure_kind,
                    model_call_ids=tuple(
                        failed_result.technical_execution_metadata.get(
                            "model_call_ids", ()
                        )
                    ),
                )
            raise
        if result.status is not WriterExecutionStatus.SUCCESS:
            raise RuntimeError("successful Writer handoff returned non-success")
        if trace_emitter is not None:
            trace_emitter.emit_writer_execution(
                task_id=shared_state.task_id,
                run_id=payload.run_id,
                writer_subject_ref=subject,
                write_instance_id=write_instance_id,
                route_decision_ref=invocation.route_decision_ref,
                generation_class=invocation.generation_class,
                evidence_bundle_sha256=(
                    invocation.canonical_evidence_input.evidence_bundle_sha256
                ),
                context_package_id=context_package_id,
                context_sha256=context_sha256,
                writer_logical_execution_key=logical_key,
                phase="end",
                writer_status=result.status,
                artifact_ref={"section_id": section.section_id},
                model_call_ids=tuple(
                    result.technical_execution_metadata.get("model_call_ids", ())
                ),
            )
        writer_execution_ref, writer_generation_invocation_ref = (
            self.coordinator.resolve_pre_write_writer_refs(
                result=result,
                invocation=invocation,
            )
        )
        generation_check = dict(
            (section.extra or {}).get("generation_check") or {}
        )
        updated = payload.model_copy(
            update={
                "shared_state_payload": self.codec.dump_graph_state(shared_state),
                "section": section,
                "generation_check": generation_check,
                "pending_writer_generation_invocation": None,
                "writer_logical_execution_key": logical_key,
                "writer_execution_ref": writer_execution_ref,
                "writer_generation_invocation_ref": (
                    writer_generation_invocation_ref
                ),
            },
        )
        return self._update(
            updated,
            node_name="invoke_pre_write_writer",
            route="prepare_paragraph_pipeline",
            shared_state=shared_state,
        )

    def _generate_section(
        self,
        state: SectionExecutionGraphState,
    ) -> dict[str, Any]:
        payload, shared_state = self._restore(state)
        budget = WorkflowBudget.from_snapshot(payload.budget_state)
        request = self._request(payload, shared_state)
        if (
            self._pre_write_ready
            and payload.current_pre_write_route_decision is not None
            and payload.section is None
        ):
            raise RuntimeError(
                "initial Graph Writer occurrence must use invoke_pre_write_writer"
            )
        # This physical callsite is intentionally downstream-only once Target
        # pre-write state exists. Re-entry from retrieve-more/corrective retrieval
        # keeps the existing post-generation owner and never creates new Route truth.
        try:
            with activate_workflow_budget(budget):
                section = self.section_generation_service.generate_section(
                    shared_state,
                    document_id=payload.document_id,
                    project_input=payload.project_input,
                    section_title=str(payload.section_title),
                    section_order=int(payload.section_order or 0),
                    rag_context=self._active_context_projection(payload),
                    citations=list(payload.active_citations),
                    structured_facts=list(payload.structured_facts),
                    previous_sections=list(payload.previous_sections),
                    evidence_bundle=(
                        payload.active_evidence_bundle
                        or payload.document_evidence_bundle
                    ),
                    generation_attempt=payload.generation_attempt,
                    writer_gate_binding=payload.active_writer_gate,
                )
            exhaustion_resource = None
            exhaustion_limit = None
        except WorkflowBudgetExceeded as exc:
            section = self.coordinator._budget_fallback_section(
                request,
                section_id=str(payload.section_id),
                section_title=str(payload.section_title),
                section_order=int(payload.section_order or 0),
                fallback_section=payload.section,
            )
            exhaustion_resource = exc.resource
            exhaustion_limit = exc.limit
        generation_check = dict((section.extra or {}).get("generation_check") or {})
        emit_workflow_custom_event(
            "section_generated",
            node_name="generate_section",
            section_id=str(payload.section_id),
            status=section.status.value,
            payload={
                "section_title": payload.section_title,
                "section_order": payload.section_order,
                "generation_attempt": payload.generation_attempt,
            },
        )
        updated = payload.model_copy(
            update={
                "shared_state_payload": self.codec.dump_graph_state(shared_state),
                "section": section,
                "generation_check": generation_check,
                "budget_state": budget.snapshot(),
                "budget_exhaustion_resource": exhaustion_resource,
                "budget_exhaustion_limit": exhaustion_limit,
            },
            deep=True,
        )
        return self._update(
            updated,
            node_name="generate_section",
            route="prepare_paragraph_pipeline",
            shared_state=shared_state,
        )

    def _prepare_paragraph_pipeline(
        self,
        state: SectionExecutionGraphState,
    ) -> dict[str, Any]:
        payload, shared_state = self._restore(state)
        section = payload.section
        if section is None:
            raise ValueError("paragraph pipeline requires generated section")
        if self.paragraph_runtime is not None:
            if self.research_runtime is None:
                raise ValueError(
                    "canonical Paragraph graph path requires ResearchRuntime"
                )
            canonical_candidates = self._build_canonical_paragraph_candidates(
                payload,
                section=section,
            )
            if not canonical_candidates:
                raise ValueError("canonical Writer produced no Paragraph candidates")
            manifest, bindings, artifacts = self._canonical_paragraph_projection(
                payload
            )
            candidate_ids = [
                item.operation_ref.target_logical_ref.logical_artifact_id
                for item in canonical_candidates
            ]
            member_ids = [
                item.logical_ref.logical_artifact_id for item in manifest.members
            ]
            if member_ids != candidate_ids[: len(member_ids)]:
                raise ValueError(
                    "canonical Paragraph Manifest is not a prefix of initial Writer candidates"
                )
            if (
                manifest.generation_membership_state == "SEALED"
                and len(member_ids) != len(candidate_ids)
            ):
                raise ValueError(
                    "SEALED Paragraph Manifest cannot accept missing initial candidates"
                )

            artifact_versions = {
                item.paragraph_id: item.paragraph_version for item in artifacts
            }
            legacy_candidates = [
                GeneratedParagraphInputSchema(
                    paragraph_id=(
                        item.operation_ref.target_logical_ref.logical_artifact_id
                    ),
                    paragraph_version=artifact_versions.get(
                        item.operation_ref.target_logical_ref.logical_artifact_id,
                        1,
                    ),
                    order=index,
                    role=item.role,
                    content=item.text,
                )
                for index, item in enumerate(canonical_candidates)
            ]
            artifact_ids = {item.paragraph_id for item in artifacts}
            retained_prechecks = [
                item
                for item in payload.paragraph_precheck_results
                if item.paragraph_id in artifact_ids
                and any(
                    artifact.paragraph_id == item.paragraph_id
                    and artifact.paragraph_version == item.paragraph_version
                    for artifact in artifacts
                )
            ]
            retained_claims = [
                item
                for item in payload.paragraph_claims
                if item.paragraph_id in artifact_ids
            ]
            cursor = len(manifest.members)
            updated = payload.model_copy(
                update={
                    "canonical_paragraph_candidates": canonical_candidates,
                    "paragraph_candidates": legacy_candidates,
                    "paragraph_bindings": bindings,
                    "section_paragraph_manifest": manifest,
                    "paragraph_artifacts": artifacts,
                    "paragraph_precheck_results": retained_prechecks,
                    "paragraph_claims": retained_claims,
                    "paragraph_cursor": cursor,
                    "paragraph_blocked_id": None,
                    "paragraph_pipeline_revision": manifest.manifest_revision,
                },
                deep=True,
            )
            route = (
                "persist_paragraph"
                if cursor < len(canonical_candidates)
                else "evaluate_generation"
            )
            emit_workflow_custom_event(
                "paragraph_pipeline_planned",
                node_name="prepare_paragraph_pipeline",
                section_id=section.section_id,
                status="success",
                payload={
                    "candidate_count": len(canonical_candidates),
                    "retained_count": len(artifacts),
                    "cursor": cursor,
                    "artifact_authority": "paragraph_runtime",
                },
            )
            return self._update(
                updated,
                node_name="prepare_paragraph_pipeline",
                route=route,
                shared_state=shared_state,
            )

        candidates = self.paragraph_pipeline.plan(
            section_id=section.section_id,
            content=section.content,
            paragraph_version=max(1, payload.generation_attempt),
            prior_paragraphs=list(payload.paragraph_artifacts),
        )
        candidate_ids = {
            str(item.paragraph_id)
            for item in candidates
            if item.paragraph_id is not None
        }
        retained_artifacts = [
            item
            for item in payload.paragraph_artifacts
            if item.paragraph_id in candidate_ids
            and any(
                candidate.paragraph_id == item.paragraph_id
                and candidate.paragraph_version == item.paragraph_version
                and candidate.content.strip() == item.content
                for candidate in candidates
            )
        ]
        retained_ids = {item.paragraph_id for item in retained_artifacts}
        retained_prechecks = [
            item
            for item in payload.paragraph_precheck_results
            if item.paragraph_id in retained_ids
            and any(
                artifact.paragraph_id == item.paragraph_id
                and artifact.paragraph_version == item.paragraph_version
                for artifact in retained_artifacts
            )
        ]
        retained_claims = [
            item
            for item in payload.paragraph_claims
            if item.paragraph_id in retained_ids
        ]
        prechecked_versions = {
            (item.paragraph_id, item.paragraph_version)
            for item in retained_prechecks
        }
        cursor = len(candidates)
        for index, candidate in enumerate(candidates):
            if (
                str(candidate.paragraph_id),
                candidate.paragraph_version,
            ) not in prechecked_versions:
                cursor = index
                break
        updated = payload.model_copy(
            update={
                "paragraph_candidates": candidates,
                "paragraph_artifacts": retained_artifacts,
                "paragraph_precheck_results": retained_prechecks,
                "paragraph_claims": retained_claims,
                "paragraph_cursor": cursor,
                "paragraph_blocked_id": None,
                "paragraph_pipeline_revision": max(
                    payload.paragraph_pipeline_revision,
                    payload.generation_attempt,
                ),
            },
            deep=True,
        )
        route = "persist_paragraph" if cursor < len(candidates) else "evaluate_generation"
        if route == "evaluate_generation":
            updated = self._invoke_writer_agent(
                updated,
                node_name="prepare_paragraph_pipeline",
                section=section,
            )
        emit_workflow_custom_event(
            "paragraph_pipeline_planned",
            node_name="prepare_paragraph_pipeline",
            section_id=section.section_id,
            status="success",
            payload={
                "candidate_count": len(candidates),
                "retained_count": len(retained_artifacts),
                "cursor": cursor,
            },
        )
        return self._update(
            updated,
            node_name="prepare_paragraph_pipeline",
            route=route,
            shared_state=shared_state,
        )

    def _persist_paragraph(
        self,
        state: SectionExecutionGraphState,
    ) -> dict[str, Any]:
        payload, shared_state = self._restore(state)
        if self.paragraph_runtime is not None:
            runtime = self.paragraph_runtime
            candidates = payload.canonical_paragraph_candidates
            if payload.paragraph_cursor >= len(candidates):
                raise ValueError("canonical paragraph persist cursor has no candidate")
            lineage = payload.active_execution_lineage
            if lineage is None:
                raise ValueError("canonical Paragraph persist requires active Attempt")
            candidate = candidates[payload.paragraph_cursor]
            _subject, write_instance_id = self._pre_write_identity(payload)
            manifest_ref = runtime.manifest_ref(
                run_id=payload.run_id,
                section_id=str(payload.section_id),
                active_lineage=lineage,
                write_instance_id=write_instance_id,
            )
            manifest = runtime.load_manifest(manifest_ref)
            cursor = payload.paragraph_cursor
            candidate_logical = candidate.operation_ref.target_logical_ref

            if cursor < len(manifest.members):
                member = manifest.members[cursor]
                if member.logical_ref != candidate_logical:
                    raise ValueError(
                        "checkpoint cursor disagrees with canonical Manifest member"
                    )
                if member.semantic_fingerprint != runtime.semantic_fingerprint(candidate):
                    raise ValueError(
                        "canonical Manifest member semantics disagree with candidate"
                    )
                manifest, bindings, artifacts = self._canonical_paragraph_projection(
                    payload
                )
                updated = payload.model_copy(
                    update={
                        "paragraph_bindings": bindings,
                        "section_paragraph_manifest": manifest,
                        "paragraph_artifacts": artifacts,
                        "paragraph_cursor": cursor,
                        "paragraph_pipeline_revision": manifest.manifest_revision,
                    },
                    deep=True,
                )
                emit_workflow_custom_event(
                    "paragraph_committed",
                    node_name="persist_paragraph",
                    section_id=str(payload.section_id),
                    status="success",
                    payload={
                        "paragraph_id": member.logical_ref.logical_artifact_id,
                        "artifact_version_id": member.artifact_version_id,
                        "content_hash": member.content_hash,
                        "idempotent_replay": True,
                        "artifact_authority": "paragraph_runtime",
                    },
                )
                return self._update(
                    updated,
                    node_name="persist_paragraph",
                    route="precheck_paragraph",
                    shared_state=shared_state,
                )

            if cursor != len(manifest.members):
                raise ValueError(
                    "canonical Paragraph cursor cannot skip Manifest membership"
                )
            version_ref = runtime.commit_paragraph_version(
                candidate=candidate,
                active_lineage=lineage,
            )
            runtime.promote_paragraph_active(
                candidate=candidate,
                target_version_ref=version_ref,
                expected_head_revision=0,
                expected_active_version_id=None,
                active_lineage=lineage,
            )
            current = runtime.load_manifest(manifest_ref)
            if current.members != manifest.members:
                raise ValueError(
                    "Paragraph Manifest changed during candidate commit/promotion"
                )
            manifest_operation = runtime.manifest_operation(
                manifest_ref=manifest_ref,
                operation_kind=SECTION_PARAGRAPH_MANIFEST_UPDATE,
                authorization_ref=candidate.operation_ref.authorization_ref,
                stable_slot_ref=candidate.operation_ref.paragraph_operation_id,
            )
            runtime.update_manifest(
                operation=manifest_operation,
                expected_manifest_revision=current.manifest_revision,
                expected_generation_membership_state=(
                    current.generation_membership_state
                ),
                expected_members=current.members,
                resulting_members=(*current.members, version_ref),
                mutation_kind="APPEND",
                active_lineage=lineage,
            )
            manifest, bindings, artifacts = self._canonical_paragraph_projection(
                payload
            )
            updated = payload.model_copy(
                update={
                    "paragraph_bindings": bindings,
                    "section_paragraph_manifest": manifest,
                    "paragraph_artifacts": artifacts,
                    "paragraph_cursor": cursor,
                    "paragraph_pipeline_revision": manifest.manifest_revision,
                },
                deep=True,
            )
            emit_workflow_custom_event(
                "paragraph_committed",
                node_name="persist_paragraph",
                section_id=str(payload.section_id),
                status="success",
                payload={
                    "paragraph_id": version_ref.logical_ref.logical_artifact_id,
                    "artifact_version_id": version_ref.artifact_version_id,
                    "content_hash": version_ref.content_hash,
                    "idempotent_replay": False,
                    "artifact_authority": "paragraph_runtime",
                },
            )
            return self._update(
                updated,
                node_name="persist_paragraph",
                route="precheck_paragraph",
                shared_state=shared_state,
            )

        if payload.paragraph_cursor >= len(payload.paragraph_candidates):
            raise ValueError("paragraph persist cursor has no candidate")
        candidate = payload.paragraph_candidates[payload.paragraph_cursor]
        artifact = self.paragraph_pipeline.persist(
            task_id=payload.task_id,
            run_id=payload.run_id,
            section_id=str(payload.section_id),
            candidate=candidate,
        )
        artifacts = list(payload.paragraph_artifacts)
        existing_index = next(
            (
                index
                for index, item in enumerate(artifacts)
                if item.paragraph_id == artifact.paragraph_id
            ),
            None,
        )
        changed = False
        if existing_index is None:
            artifacts.append(artifact)
            changed = True
        else:
            existing = artifacts[existing_index]
            if existing.paragraph_version == artifact.paragraph_version:
                self.paragraph_pipeline.assert_idempotent_commit(existing, artifact)
            elif artifact.paragraph_version > existing.paragraph_version:
                artifacts[existing_index] = artifact
                changed = True
            else:
                raise ValueError("paragraph replay attempted an older version")
        artifacts.sort(key=lambda item: item.order)
        prechecks = [
            item
            for item in payload.paragraph_precheck_results
            if not (
                item.paragraph_id == artifact.paragraph_id
                and item.paragraph_version != artifact.paragraph_version
            )
        ]
        updated = payload.model_copy(
            update={
                "paragraph_artifacts": artifacts,
                "paragraph_precheck_results": prechecks,
                "paragraph_pipeline_revision": (
                    payload.paragraph_pipeline_revision + 1
                    if changed
                    else payload.paragraph_pipeline_revision
                ),
            },
            deep=True,
        )
        emit_workflow_custom_event(
            "paragraph_committed",
            node_name="persist_paragraph",
            section_id=str(payload.section_id),
            status="success",
            payload={
                "paragraph_id": artifact.paragraph_id,
                "paragraph_version": artifact.paragraph_version,
                "content_hash": artifact.content_hash,
                "idempotent_replay": not changed,
            },
        )
        return self._update(
            updated,
            node_name="persist_paragraph",
            route="precheck_paragraph",
            shared_state=shared_state,
        )

    def _paragraph_section_snapshot(
        self,
        payload: SectionExecutionGraphPayloadSchema,
        *,
        blocked_paragraph_id: str | None,
    ):
        section = payload.section
        if section is None:
            raise ValueError("paragraph snapshot requires generated section")
        canonical_manifest = None
        if self.paragraph_runtime is not None:
            canonical_manifest, _bindings, _artifacts = (
                self._canonical_paragraph_projection(payload)
            )
            content = self.paragraph_runtime.assemble_manifest_text(
                canonical_manifest.manifest_ref
            )
        else:
            excluded = {blocked_paragraph_id} if blocked_paragraph_id else set()
            content = self.paragraph_pipeline.assemble_content(
                payload.paragraph_artifacts,
                exclude_ids=excluded,
            )
        checks = dict(
            section.eval_result.checks if section.eval_result is not None else {}
        )
        failures = list(
            section.eval_result.failures if section.eval_result is not None else []
        )
        warnings = list(
            section.eval_result.warnings if section.eval_result is not None else []
        )
        checks["paragraph_precheck_passed"] = blocked_paragraph_id is None
        if blocked_paragraph_id is not None:
            failure = f"paragraph_precheck_blocked:{blocked_paragraph_id}"
            if failure not in failures:
                failures.append(failure)
        passed = bool(
            blocked_paragraph_id is None
            and (section.eval_result is None or section.eval_result.passed)
        )
        status = section.status
        if blocked_paragraph_id is not None:
            status = (
                ExecutionStatus.PARTIAL_SUCCESS
                if content
                else ExecutionStatus.FAILED
            )
        return section.model_copy(
            update={
                "content": content,
                "status": status,
                "eval_result": SectionEvalSchema(
                    passed=passed,
                    checks=checks,
                    failures=failures,
                    warnings=warnings,
                    semantic_gate=(
                        section.eval_result.semantic_gate
                        if section.eval_result is not None
                        else None
                    ),
                ),
                "extra": {
                    **dict(section.extra or {}),
                    "paragraph_pipeline": {
                        "artifact_authority": (
                            "paragraph_runtime"
                            if self.paragraph_runtime is not None
                            else "section_langgraph_checkpoint"
                        ),
                        "paragraph_count": (
                            len(canonical_manifest.members)
                            if canonical_manifest is not None
                            else len(payload.paragraph_artifacts)
                        ),
                        "precheck_count": len(
                            payload.paragraph_precheck_results
                        ),
                        "claim_count": len(payload.paragraph_claims),
                        "blocked_paragraph_id": blocked_paragraph_id,
                        "pipeline_revision": payload.paragraph_pipeline_revision,
                    },
                },
            },
            deep=True,
        )

    def _precheck_paragraph(
        self,
        state: SectionExecutionGraphState,
    ) -> dict[str, Any]:
        payload, shared_state = self._restore(state)
        if payload.paragraph_cursor >= len(payload.paragraph_candidates):
            raise ValueError("paragraph precheck cursor has no candidate")
        candidate = payload.paragraph_candidates[payload.paragraph_cursor]
        paragraph_id = str(candidate.paragraph_id)
        artifact = next(
            (
                item
                for item in payload.paragraph_artifacts
                if item.paragraph_id == paragraph_id
            ),
            None,
        )
        if artifact is None:
            raise ValueError("paragraph precheck requires committed artifact")
        snapshot_version = str(
            payload.section_state.attempt.input_snapshot_version
            if payload.section_state is not None
            and payload.section_state.attempt.input_snapshot_version
            else f"sha256:{payload.task_id}:{payload.run_id}"
        )
        fact_snapshot = self.paragraph_pipeline.fact_snapshot(
            task_id=payload.task_id,
            run_id=payload.run_id,
            section_id=str(payload.section_id),
            snapshot_version=snapshot_version,
            structured_facts=list(payload.structured_facts),
        )
        existing = next(
            (
                item
                for item in payload.paragraph_precheck_results
                if item.paragraph_id == paragraph_id
                and item.paragraph_version == artifact.paragraph_version
            ),
            None,
        )
        precheck = existing or self.paragraph_pipeline.precheck_paragraph(
            section_id=str(payload.section_id),
            artifact=artifact,
            fact_snapshot=fact_snapshot,
        )
        prechecks = [
            item
            for item in payload.paragraph_precheck_results
            if item.paragraph_id != paragraph_id
        ]
        prechecks.append(precheck)
        prechecks.sort(
            key=lambda item: next(
                artifact_item.order
                for artifact_item in payload.paragraph_artifacts
                if artifact_item.paragraph_id == item.paragraph_id
            )
        )
        claims = self.paragraph_pipeline.update_claims(
            section_id=str(payload.section_id),
            artifacts=list(payload.paragraph_artifacts),
            previous_claims=list(payload.paragraph_claims),
            changed_paragraph_ids={paragraph_id},
            fact_snapshot=fact_snapshot,
        )
        claims_by_id = {item.claim_id: item for item in claims}
        claims = sorted(claims_by_id.values(), key=lambda item: item.claim_id)
        canonical_paragraph_mode = self.paragraph_runtime is not None
        generation_check = dict(payload.generation_check)
        generation_check.update(
            {
                "paragraph_precheck_passed": (
                    True if canonical_paragraph_mode else not precheck.blocking
                ),
                "paragraph_precheck_blocking": (
                    False if canonical_paragraph_mode else precheck.blocking
                ),
                "paragraph_precheck_compat_blocking": precheck.blocking,
                "paragraph_precheck_issue_count": len(precheck.issues),
            }
        )
        updates: dict[str, Any] = {
            "paragraph_precheck_results": prechecks,
            "paragraph_claims": claims,
            "generation_check": generation_check,
            "paragraph_pipeline_revision": (
                payload.section_paragraph_manifest.manifest_revision
                if canonical_paragraph_mode
                and payload.section_paragraph_manifest is not None
                else payload.paragraph_pipeline_revision
                if existing is not None
                else payload.paragraph_pipeline_revision + 1
            ),
        }
        route = "persist_paragraph"
        if precheck.blocking and not canonical_paragraph_mode:
            updates["paragraph_blocked_id"] = paragraph_id
            blocked_payload = payload.model_copy(update=updates, deep=True)
            section = self._paragraph_section_snapshot(
                blocked_payload,
                blocked_paragraph_id=paragraph_id,
            )
            blocked_payload = blocked_payload.model_copy(
                update={"section": section},
                deep=True,
            )
            blocked_payload = self._invoke_writer_agent(
                blocked_payload,
                node_name="precheck_paragraph",
                section=section,
            )
            updated = blocked_payload
            route = "evaluate_generation"
        else:
            cursor = payload.paragraph_cursor + 1
            prechecked_versions = {
                (item.paragraph_id, item.paragraph_version)
                for item in prechecks
            }
            while cursor < len(payload.paragraph_candidates):
                next_candidate = payload.paragraph_candidates[cursor]
                if (
                    str(next_candidate.paragraph_id),
                    next_candidate.paragraph_version,
                ) not in prechecked_versions:
                    break
                cursor += 1
            updates["paragraph_cursor"] = cursor
            updated = payload.model_copy(update=updates, deep=True)
            if cursor >= len(payload.paragraph_candidates):
                section = self._paragraph_section_snapshot(
                    updated,
                    blocked_paragraph_id=None,
                )
                updated = updated.model_copy(update={"section": section}, deep=True)
                if not canonical_paragraph_mode:
                    updated = self._invoke_writer_agent(
                        updated,
                        node_name="precheck_paragraph",
                        section=section,
                    )
                route = "evaluate_generation"
        emit_workflow_custom_event(
            "paragraph_prechecked",
            node_name="precheck_paragraph",
            section_id=str(payload.section_id),
            status=(
                "compat_observation"
                if canonical_paragraph_mode and precheck.blocking
                else "blocked"
                if precheck.blocking
                else "success"
            ),
            payload={
                "paragraph_id": paragraph_id,
                "paragraph_version": artifact.paragraph_version,
                "blocking": precheck.blocking,
                "claim_count": len(precheck.claims),
                "issue_count": len(precheck.issues),
            },
        )
        return self._update(
            updated,
            node_name="precheck_paragraph",
            route=route,
            shared_state=shared_state,
        )

    @staticmethod
    def _pwa004_review_enabled(project_input) -> bool:
        """Only a new, explicitly opted-in ProjectInput can change failure scope."""
        extra = project_input.generation_requirements.extra or {}
        policy = extra.get(PWA004_REVIEW_POLICY_KEY)
        if policy is not None and policy != PWA004_REVIEW_POLICY:
            raise ValueError("Unsupported prospective Verifier rejection policy")
        return policy == PWA004_REVIEW_POLICY

    def _pwa004_rejection_receipt(self, error, *, expected_claim=None, expected_binding=None,
                                  expected_support=None, expected_lineage=None):
        """Resolve a durable initial Candidate; do not synthesize a Result/Issue."""
        verifier = getattr(self.coordinator, "claim_verifier_composition", None)
        runtime = self.claim_verification_runtime
        if verifier is None or runtime is None or verifier.claim_runtime is not runtime:
            raise RuntimeError("PWA004 requires the original exact Verifier owner")
        candidate = error.candidate
        invocation = runtime.verifier_input_from_candidate(candidate)
        if expected_lineage is not None and candidate.active_lineage != expected_lineage:
            raise RuntimeError("PWA004 Candidate lineage differs from current Attempt")
        if expected_claim is not None and candidate.claim_ref != expected_claim.claim_ref:
            raise RuntimeError("PWA004 Candidate belongs to another Claim")
        if expected_binding is not None and candidate.binding_facts_ref != expected_binding.binding_facts_ref:
            raise RuntimeError("PWA004 Candidate belongs to another Binding")
        if expected_support is not None and candidate.claim_evidence_support_ref != expected_support.support_ref:
            raise RuntimeError("PWA004 Candidate belongs to another Support")
        _key, _occurrence, prefix = verifier._physical(invocation)
        input_receipt = verifier._exact_artifact(
            prefix + "/input", invocation, "canonical_verifier_input", "canonical_verifier_input_v2")
        verifier._exact_artifact(
            prefix + "/request", invocation, "canonical_verifier_request", "canonical_verifier_request_v1")
        verifier._exact_artifact(prefix + "/response", invocation,
                                 "canonical_verifier_response", "canonical_verifier_response_v1")
        stored = verifier._exact_artifact(
            prefix + "/candidate", invocation, "canonical_verifier_candidate", "canonical_verifier_candidate_v2")
        if (stored.get("outcome") != "completed"
                or stored.get("candidate") != candidate.model_dump(mode="json")
                or input_receipt.get("input_fingerprint") != candidate.verifier_semantic_input_sha256):
            raise RuntimeError("PWA004 rejected Candidate is not an exact durable occurrence")
        return {
            "rule_code": error.rule_code,
            "claim_ref": candidate.claim_ref.model_dump(mode="json"),
            "claim_occurrence_ref": candidate.claim_occurrence_ref.model_dump(mode="json"),
            "paragraph_version_ref": candidate.paragraph_version_ref.model_dump(mode="json"),
            "binding_facts_ref": candidate.binding_facts_ref.model_dump(mode="json"),
            "support_ref": candidate.claim_evidence_support_ref.model_dump(mode="json"),
            "support_relation": error.support_relation,
            "candidate_ref": prefix + "/candidate",
            "candidate_sha256": canonical_sha256(candidate.model_dump(mode="json")),
            "verifier_semantic_input_sha256": candidate.verifier_semantic_input_sha256,
            "attempt_id": candidate.active_lineage.attempt_id,
            "input_snapshot_version": candidate.active_lineage.input_snapshot_version,
        }

    def _pwa004_validate_recovered_rejections(self, payload):
        """Checkpoint metadata is not proof; re-resolve exact current owners."""
        rows = (payload.retrieval_metadata or {}).get(PWA004_REJECTION_KEY)
        if not isinstance(rows, list) or not rows:
            raise RuntimeError("PWA004 recovery lacks exact refusal records")
        runtime = self.claim_verification_runtime
        if runtime is None:
            raise RuntimeError("PWA004 recovery lacks canonical Claim runtime")
        for row in rows:
            if not isinstance(row, dict) or row.get("rule_code") != VerifierSupportVerdictAdmissionError.rule_code:
                raise ValueError("PWA004 recovery rejection rule is unrecognized")
            invocation_fp = row.get("verifier_semantic_input_sha256")
            if not isinstance(invocation_fp, str) or len(invocation_fp) != 64:
                raise ValueError("PWA004 recovery Verifier input fingerprint missing")
            key = canonical_sha256({"contract": "verifier-physical-occurrence-v2",
                                    "verifier_semantic_input_sha256": invocation_fp})
            ref = f"artifact://canonical-verifier-v2/{key}/candidate"
            if row.get("candidate_ref") != ref:
                raise ValueError("PWA004 recovery Candidate artifact ref mismatch")
            verifier = self.coordinator.claim_verifier_composition
            artifact = verifier.store.get_json(
                ref, task_id=payload.task_id, run_id=payload.run_id,
                section_id=payload.section_id)
            if not isinstance(artifact, dict) or artifact.get("outcome") != "completed":
                raise ValueError("PWA004 recovery Candidate missing or incomplete")
            candidate = VerifierSemanticResultCandidateSchema.model_validate(artifact["candidate"])
            claim = runtime.resolve_claim(candidate.claim_ref)
            binding = runtime.resolve_binding_facts(candidate.binding_facts_ref)
            support = self.coordinator.claim_support_composition.runtime.resolve_support(
                candidate.claim_evidence_support_ref)
            verifier.validate_read_sources(binding, support, payload.active_execution_lineage)
            verifier.validate_v2_identity(claim, binding, support, candidate, payload.active_execution_lineage)
            if (candidate.active_lineage != payload.active_execution_lineage
                    or candidate.claim_occurrence_ref.section_id != payload.section_id):
                raise ValueError("PWA004 recovered Candidate has stale Attempt or Section")
            try:
                runtime.validate_v2_provenance(binding, support, candidate)
            except VerifierSupportVerdictAdmissionError as error:
                actual = self._pwa004_rejection_receipt(
                    error, expected_claim=claim, expected_binding=binding,
                    expected_support=support, expected_lineage=payload.active_execution_lineage)
                if actual != row:
                    raise ValueError("PWA004 rejection projection differs from canonical original")
            else:
                raise ValueError("PWA004 recovered Candidate is not the original forbidden verdict")
            if runtime.resolve_claim_head(candidate.claim_ref) is not None:
                raise ValueError("PWA004 rejected Claim acquired a formal current head")

    def _commit_canonical_claim_coverage(
        self,
        payload: SectionExecutionGraphPayloadSchema,
    ) -> SectionExecutionGraphPayloadSchema:
        runtime = self.claim_verification_runtime
        paragraph_runtime = self.paragraph_runtime
        if runtime is None:
            return payload
        if paragraph_runtime is None:
            raise ValueError("canonical Claim coverage requires ParagraphRuntime")
        lineage = payload.active_execution_lineage
        invocation_ref = payload.writer_generation_invocation_ref
        if lineage is None or invocation_ref is None:
            raise ValueError(
                "canonical Claim coverage requires exact Attempt and Writer invocation refs"
            )

        manifest, bindings, artifacts = self._canonical_paragraph_projection(payload)
        if not bindings:
            raise ValueError("canonical Claim coverage requires Paragraph membership")

        if (
            getattr(self.coordinator, "section_dependencies", None) is not None
            and manifest.generation_membership_state == "OPEN"
        ):
            writer_key = payload.writer_logical_execution_key
            if writer_key is None:
                raise ValueError("Section scope sealing requires exact Writer key")
            seal_operation = paragraph_runtime.manifest_operation(
                manifest_ref=manifest.manifest_ref,
                operation_kind=SECTION_PARAGRAPH_MANIFEST_SEAL,
                authorization_ref=paragraph_runtime.initial_writer_authorization_ref(writer_key),
                stable_slot_ref="initial-writer-complete-section-scope",
            )
            paragraph_runtime.seal_manifest(
                operation=seal_operation,
                expected_manifest_revision=manifest.manifest_revision,
                expected_members=manifest.members,
                active_lineage=lineage,
            )
            manifest, bindings, artifacts = self._canonical_paragraph_projection(payload)

        extractor = getattr(self.coordinator, "claim_extraction_runtime", None)
        if extractor is None or extractor.claim_runtime is not runtime:
            raise ValueError("canonical Claim extraction producer is not composed")
        operation_context = ClaimOperationContextSchema(
            active_lineage=lineage,
            authorization_ref=(
                "writer-generation-invocation://sha256/"
                + invocation_ref.invocation_sha256
            ),
            operation_purpose=f"claim-coverage:section:{payload.section_id}",
        )
        committed = extractor.extract_and_commit(
            task_id=payload.task_id, bindings=bindings, operation_context=operation_context,
        )
        binder = getattr(self.coordinator, "claim_binding_composition", None)
        if binder is None:
            raise ValueError("canonical Claim binding producer is not composed")
        binding_records = binder.bind_claim_records(
            claim_runtime=runtime, records=committed.records, operation_context=operation_context,
        )
        support_composition = getattr(self.coordinator, "claim_support_composition", None)
        if support_composition is None:
            raise ValueError("canonical Claim support producer is not composed")
        support_records = support_composition.support_bindings(
            records=committed.records, binding_records=binding_records, operation_context=operation_context,
        )
        verifier = getattr(self.coordinator, "claim_verifier_composition", None)
        if verifier is None:
            raise ValueError("canonical Claim Verifier is not composed")
        review_only = self._pwa004_review_enabled(payload.project_input)
        rejections = []
        if review_only:
            if not (len(committed.records) == len(binding_records) == len(support_records)):
                raise RuntimeError("PWA004 canonical Claim/Binding/Support scope differs")
            accepted = []
            for claim, binding, support in zip(committed.records, binding_records, support_records):
                try:
                    accepted.extend(verifier.verify_claims(
                        records=(claim,), binding_records=(binding,), support_records=(support,),
                        operation_context=operation_context))
                except VerifierSupportVerdictAdmissionError as error:
                    rejections.append(self._pwa004_rejection_receipt(
                        error, expected_claim=claim, expected_binding=binding,
                        expected_support=support, expected_lineage=lineage))
            verifications = tuple(accepted)
        else:
            verifications = verifier.verify_claims(
                records=committed.records, binding_records=binding_records, support_records=support_records,
                operation_context=operation_context,
            )

        memory = getattr(self.coordinator, "memory_composition", None)
        if memory is not None and not rejections:
            for binding in bindings:
                memory.project_artifact(binding.version_ref)
            for verification in verifications:
                if verification.verification_result.verdict.value == "VERIFIED":
                    memory.project_claim_revision(verification.verification_revision.revision_ref)
                for head in verification.issue_heads:
                    memory.project_issue(head)

        metadata = dict(payload.retrieval_metadata)
        if rejections:
            metadata[PWA004_REJECTION_KEY] = rejections
            metadata["canonical_claim_verification_incomplete"] = True
        metadata.update(
            {
                "canonical_claim_coverage_state": (
                    committed.coverage_accounting.accounting_state.value
                ),
                "canonical_claim_count": len(committed.records),
                "canonical_claim_binding_count": len(binding_records),
                "canonical_claim_binding_facts_refs": [
                    item.binding_facts_ref.model_dump(mode="json") for item in binding_records
                ],
                "canonical_claim_support_refs": [item.support_ref.model_dump(mode="json") for item in support_records],
                "canonical_claim_support_count": len(support_records),
                "canonical_claim_verification_refs": [
                    item.verification_result.verification_result_ref.model_dump(mode="json") for item in verifications
                ],
                "canonical_claim_verification_count": len(verifications),
                "canonical_claim_extractor_invoked": True,
                "canonical_claim_verifier_invoked": bool(verifications),
            }
        )
        emit_workflow_custom_event(
            "canonical_claim_coverage_committed",
            node_name="evaluate_generation",
            section_id=str(payload.section_id),
            status=committed.coverage_accounting.accounting_state.value.lower(),
            payload={
                "coverage_proof_id": committed.coverage_proof_ref.coverage_proof_id,
                "paragraph_count": len(bindings),
                "claim_count": len(committed.records),
                "claim_authority": "claim_verification_runtime",
            },
        )
        return payload.model_copy(
            update={
                "section_paragraph_manifest": manifest,
                "paragraph_bindings": bindings,
                "paragraph_artifacts": artifacts,
                "canonical_claim_coverage_proof_refs": [
                    committed.coverage_proof_ref
                ],
                "canonical_claim_verification_refs": [
                    item.verification_result.verification_result_ref
                    for item in verifications
                ],
                "retrieval_metadata": metadata,
            },
            deep=True,
        )

    def _evaluate_generation(
        self,
        state: SectionExecutionGraphState,
    ) -> dict[str, Any]:
        payload, shared_state = self._restore(state)
        section = payload.section
        if section is None:
            raise ValueError("evaluate_generation requires section")
        if self.claim_verification_runtime is not None:
            payload = self._commit_canonical_claim_coverage(payload)
            # Canonical quality is decided by the downstream post-write owners.
            # Compatibility hints cannot authorize another Research/Writer run.
            return self._update(
                payload,
                node_name="evaluate_generation",
                route="finalize",
                shared_state=shared_state,
            )
        else:
            payload = self._invoke_verifier_agent(
                payload,
                node_name="evaluate_generation",
                section=section,
            )
        budget = WorkflowBudget.from_snapshot(payload.budget_state)
        route = "finalize"
        updates: dict[str, Any] = {}
        metadata = dict(payload.retrieval_metadata)
        self_rag_rounds = list(payload.self_rag_retrieval_rounds)
        pending_round = (
            dict(payload.pending_self_rag_round)
            if payload.pending_self_rag_round is not None
            else None
        )
        if pending_round is not None:
            pending_round.update(
                {
                    "success": payload.budget_exhaustion_resource is None,
                    "check_after": dict(payload.generation_check),
                    "retrieved_chunk_count": len(payload.active_chunks),
                    "citation_count": len(payload.active_citations),
                }
            )
            if payload.budget_exhaustion_resource is not None:
                pending_round.update(
                    {
                        "error": "workflow_budget_exhausted",
                        "budget_resource": payload.budget_exhaustion_resource,
                        "budget_limit": payload.budget_exhaustion_limit,
                    }
                )
            self_rag_rounds.append(pending_round)
            metadata["self_rag_retrieval_rounds"] = list(self_rag_rounds)
            metadata["self_rag_retrieve_more_success"] = bool(
                pending_round.get("success")
            )
            metadata["self_rag_recheck"] = dict(payload.generation_check)
            updates.update(
                {
                    "self_rag_retrieval_rounds": self_rag_rounds,
                    "pending_self_rag_round": None,
                    "retrieval_metadata": metadata,
                }
            )
        verifier_payload = (
            payload.verifier_agent_payloads[-1]
            if payload.verifier_agent_payloads
            else None
        )
        paragraph_blocking = bool(
            payload.generation_check.get("paragraph_precheck_blocking")
        )
        if paragraph_blocking:
            try:
                budget.consume_rewrite_round()
                route = "generate"
                updates.update(
                    {
                        "generation_attempt": payload.generation_attempt + 1,
                        "budget_state": budget.snapshot(),
                    }
                )
            except WorkflowBudgetExceeded as exc:
                updates.update(
                    {
                        "budget_state": budget.snapshot(),
                        "budget_exhaustion_resource": exc.resource,
                        "budget_exhaustion_limit": exc.limit,
                    }
                )
        elif verifier_payload is not None:
            action = verifier_payload.action
            if action == VerifierAction.RESEARCH_MORE:
                desired_route = str(
                    verifier_payload.decision_metadata.get("retrieval_route")
                    or "retrieve_more"
                )
                if desired_route not in {"retrieve_more", "corrective_retrieval"}:
                    desired_route = "retrieve_more"
                if budget.retrieval_rounds < budget.max_retrieval_rounds:
                    route = desired_route
                else:
                    updates.update(
                        {
                            "budget_exhaustion_resource": "retrieval_rounds",
                            "budget_exhaustion_limit": budget.max_retrieval_rounds,
                        }
                    )
            elif action == VerifierAction.REWRITE:
                try:
                    budget.consume_rewrite_round()
                    route = "generate"
                    updates.update(
                        {
                            "generation_attempt": payload.generation_attempt + 1,
                            "budget_state": budget.snapshot(),
                        }
                    )
                except WorkflowBudgetExceeded as exc:
                    updates.update(
                        {
                            "budget_state": budget.snapshot(),
                            "budget_exhaustion_resource": exc.resource,
                            "budget_exhaustion_limit": exc.limit,
                        }
                    )
            else:
                route = "finalize"
        else:
            need_more = bool(payload.generation_check.get("need_retrieve_more"))
            citation_bound = bool(
                section.eval_result
                and section.eval_result.checks.get("citation_bound", False)
            )
            if need_more:
                if budget.retrieval_rounds < budget.max_retrieval_rounds:
                    route = "retrieve_more"
                else:
                    updates.update(
                        {
                            "budget_exhaustion_resource": "retrieval_rounds",
                            "budget_exhaustion_limit": budget.max_retrieval_rounds,
                        }
                    )
            elif (
                payload.citation_required
                and not payload.evidence_blocked
                and not citation_bound
                and payload.corrective_retrieval_enabled
            ):
                if budget.retrieval_rounds < budget.max_retrieval_rounds:
                    route = "corrective_retrieval"
                else:
                    updates.update(
                        {
                            "budget_exhaustion_resource": "retrieval_rounds",
                            "budget_exhaustion_limit": budget.max_retrieval_rounds,
                        }
                    )
        updated = payload.model_copy(update=updates, deep=True)
        return self._update(
            updated,
            node_name="evaluate_generation",
            route=route,
            shared_state=shared_state,
        )

    def _retrieve_recovery(
        self,
        state: SectionExecutionGraphState,
        *,
        corrective: bool,
    ) -> dict[str, Any]:
        payload, shared_state = self._restore(state)
        budget = WorkflowBudget.from_snapshot(payload.budget_state)
        try:
            budget.consume_retrieval_round()
        except WorkflowBudgetExceeded as exc:
            updated = payload.model_copy(
                update={
                    "budget_state": budget.snapshot(),
                    "budget_exhaustion_resource": exc.resource,
                    "budget_exhaustion_limit": exc.limit,
                },
                deep=True,
            )
            return self._update(
                updated,
                node_name=(
                    "corrective_retrieval" if corrective else "retrieve_more"
                ),
                route="finalize",
                shared_state=shared_state,
            )

        title = str(payload.section_title)
        order = int(payload.section_order or 0)
        recovery_count = payload.recovery_count + 1
        query = self.query_builder.build(
            payload.project_input,
            title,
            recovery=True,
        )
        scope = "recovery" if corrective else "self_rag_recovery"
        label = "CorrectiveSectionRAG" if corrective else "SelfRAGRetrieveMore"
        print(
            f"[{label}] START section={title} query={query[:100]!r}",
            flush=True,
        )
        if (
            self.section_agent_adapter is not None
            and self.section_agent_adapter.uses_research_tool_gateway
        ):
            self_rag_rounds = list(payload.self_rag_retrieval_rounds)
            pending_self_rag_round = (
                {
                    "round": budget.retrieval_rounds,
                    "query": query,
                    "check_before": dict(payload.generation_check),
                    "success": False,
                }
                if not corrective
                else None
            )
            updated = payload.model_copy(
                update={
                    "budget_state": budget.snapshot(),
                    "recovery_count": recovery_count,
                },
                deep=True,
            )
            call_suffix = (
                f"section_{order:03d}_recovery_{recovery_count}"
                if corrective
                else f"section_{order:03d}_self_rag_{recovery_count}"
            )
            updated = self._invoke_research_agent(
                updated,
                node_name=(
                    "corrective_retrieval" if corrective else "retrieve_more"
                ),
                research_mode=(
                    "corrective_retrieval"
                    if corrective
                    else "self_rag_recovery"
                ),
                retrieval_succeeded=False,
                retrieval_scope=scope,
                shared_state=shared_state,
                query=query,
                call_suffix=call_suffix,
                citation_registry=list(payload.citation_registry),
                recovery_candidate=True,
            )
            metadata = dict(updated.retrieval_metadata)
            success = bool(
                metadata.get("research_tool_retrieval_success")
            )
            route = "finalize"
            branch_updates: dict[str, Any] = {}
            candidate_accepted = bool(
                metadata.get("recovery_candidate_accepted")
            )
            candidate_generation_permitted = bool(
                metadata.get("recovery_candidate_generation_permitted")
            )
            assessment = bundle_first_assessment_projection(
                updated.active_evidence_bundle,
            )
            writer_gate = updated.active_writer_gate
            writer_enforcement = (
                self.coordinator.resolve_writer_gate_enforcement(writer_gate)
            )
            metadata["writer_gate_enforcement"] = writer_enforcement.as_metadata(
                gate_id="G06/G11"
            )
            if success and candidate_accepted:
                if not candidate_generation_permitted:
                    block_reason = (
                        "writer_gate_missing_after_research_tool_retrieval"
                        if writer_gate is None
                        else (
                            "legacy_writer_artifact_incomplete_after_research_tool_retrieval"
                            if writer_gate.legacy_artifact_incomplete
                            else (
                                "writer_human_review_required_after_research_tool_retrieval"
                                if writer_gate.gate_decision.human_review_required
                                else "writer_generation_blocked_after_research_tool_retrieval"
                            )
                        )
                    )
                    if writer_gate is not None:
                        section = self.section_generation_service.build_gate_blocked_section(
                            shared_state,
                            project_input=payload.project_input,
                            section_title=title,
                            section_order=order,
                            rag_context=updated.active_context,
                            citations=list(updated.active_citations),
                            writer_gate_binding=writer_gate,
                        )
                    else:
                        section = self.section_generation_service.build_insufficient_evidence_section(
                            shared_state,
                            project_input=payload.project_input,
                            section_title=title,
                            section_order=order,
                            rag_context=updated.active_context,
                            citations=list(updated.active_citations),
                            assessment={
                                **assessment,
                                "status": "not_assessed",
                                "reason": "final writer gate binding is missing",
                            },
                        )
                        section.extra = {
                            **dict(section.extra or {}),
                            "generation_block_reason": block_reason,
                            "writer_execution_status": "not_assessed",
                            "formal_deliverable_eligible": False,
                        }
                    branch_updates.update(
                        {
                            "section": section,
                            "evidence_blocked": True,
                            "generation_check": {},
                        }
                    )
                    if pending_self_rag_round is not None:
                        completed_round = {
                            **pending_self_rag_round,
                            "error": block_reason,
                            "assessment": assessment,
                            "retrieved_chunk_count": len(updated.active_chunks),
                            "citation_count": len(updated.active_citations),
                        }
                        self_rag_rounds.append(completed_round)
                        metadata["self_rag_retrieval_rounds"] = list(
                            self_rag_rounds
                        )
                        metadata["self_rag_retrieve_more_success"] = False
                        branch_updates.update(
                            {
                                "self_rag_retrieval_rounds": self_rag_rounds,
                                "pending_self_rag_round": None,
                            }
                        )
                else:
                    branch_updates["generation_attempt"] = (
                        payload.generation_attempt + 1
                    )
                    if pending_self_rag_round is not None:
                        branch_updates["pending_self_rag_round"] = {
                            **pending_self_rag_round,
                            "retrieved_chunk_count": len(updated.active_chunks),
                            "citation_count": len(updated.active_citations),
                        }
                    route = "generate"
            elif success and not candidate_accepted:
                rejection_reason = str(
                    metadata.get("recovery_candidate_rejection_reason")
                    or metadata.get("self_rag_recovery_rejected")
                    or metadata.get("corrective_recovery_rejected")
                    or "recovery_not_accepted"
                )
                metadata["recovery_candidate_rejected"] = True
                if pending_self_rag_round is not None:
                    completed_round = {
                        **pending_self_rag_round,
                        "error": rejection_reason,
                        "recovery_rejected": True,
                        "retained_scope": updated.active_scope,
                        "retrieved_chunk_count": int(
                            metadata.get("recovery_candidate_chunk_count") or 0
                        ),
                        "citation_count": int(
                            metadata.get("recovery_candidate_citation_count") or 0
                        ),
                    }
                    self_rag_rounds.append(completed_round)
                    metadata["self_rag_retrieval_rounds"] = list(
                        self_rag_rounds
                    )
                    metadata["self_rag_retrieve_more_success"] = False
                    branch_updates.update(
                        {
                            "self_rag_retrieval_rounds": self_rag_rounds,
                            "pending_self_rag_round": None,
                        }
                    )
            elif pending_self_rag_round is not None:
                completed_round = {
                    **pending_self_rag_round,
                    "error": str(
                        metadata.get("research_tool_error")
                        or "empty_recovered_context"
                    ),
                    "retrieved_chunk_count": len(updated.active_chunks),
                    "citation_count": len(updated.active_citations),
                }
                self_rag_rounds.append(completed_round)
                metadata["self_rag_retrieval_rounds"] = list(
                    self_rag_rounds
                )
                metadata["self_rag_retrieve_more_success"] = False
                branch_updates.update(
                    {
                        "self_rag_retrieval_rounds": self_rag_rounds,
                        "pending_self_rag_round": None,
                    }
                )
            if corrective:
                metadata["corrective_retrieval_success"] = success
            elif route == "generate":
                metadata["self_rag_retrieve_more_success"] = True
            branch_updates["retrieval_metadata"] = metadata
            updated = updated.model_copy(update=branch_updates, deep=True)
            if (
                not self._pre_write_ready
                and updated.research_agent_payloads
            ):
                action = (
                    updated.research_agent_payloads[-1]
                    .evidence_summary.recommended_action
                )
                if route == "generate" and action != ResearchRecommendedAction.WRITE:
                    route = "finalize"
            print(
                f"[{label}] END   section={title} success={success} "
                f"chunks={len(updated.active_chunks)} "
                f"citations={len(updated.active_citations)}",
                flush=True,
            )
            return self._update(
                updated,
                node_name=(
                    "corrective_retrieval" if corrective else "retrieve_more"
                ),
                route=route,
                shared_state=shared_state,
            )
        result = self.evidence_service.retrieve(
            shared_state,
            payload.project_input,
            query=query,
            scope=scope,
            section_id=str(payload.section_id),
            section_title=title,
            call_suffix=(
                f"section_{order:03d}_recovery_{recovery_count}"
                if corrective
                else f"section_{order:03d}_self_rag_{recovery_count}"
            ),
        )
        registry = DocumentCitationRegistry.from_citations(
            payload.citation_registry
        )
        tool_call_ids = list(payload.tool_call_ids)
        metadata = dict(payload.retrieval_metadata)
        updates: dict[str, Any] = {
            "budget_state": budget.snapshot(),
            "recovery_count": recovery_count,
        }
        self_rag_rounds = list(payload.self_rag_retrieval_rounds)
        pending_self_rag_round = (
            {
                "round": budget.retrieval_rounds,
                "query": query,
                "check_before": dict(payload.generation_check),
                "success": False,
            }
            if not corrective
            else None
        )
        route = "finalize"
        if result is not None:
            tool_call_ids.append(result.retrieval_trace_id)
        if result is not None and result.success:
            context, chunks, citations, normalized = (
                self.evidence_service.extract_rag_output(shared_state, result)
            )
            context, chunks, citations, normalized = registry.remap_bundle(
                context=context,
                chunks=chunks,
                citations=citations,
                normalized=normalized,
                scope=scope,
                query=query,
            )
            assessment = self._assessment_from_bundle(result)
            writer_gate = self.coordinator._writer_gate_binding(
                normalized,
                evidence_bundle=result,
                section_id=str(payload.section_id),
                section_title=title,
                section_order=int(payload.section_order or 0) or None,
                document_id=payload.document_id,
                run_id=payload.run_id,
            )
            # A rejected candidate may remain in lightweight retrieval history, but
            # it must not reserve document citation labels or persist full chunks.
            updates["rag_outputs"] = [
                *payload.rag_outputs,
                SchemeEvidenceService.retrieval_history_projection(
                    normalized,
                    scope=scope,
                    evidence_bundle=result,
                ),
            ]
            (
                candidate_accepted,
                current_generation_permitted,
                candidate_generation_permitted,
                rejection_reason,
            ) = self._recovery_candidate_decision(
                current_writer_gate=payload.active_writer_gate,
                candidate_writer_gate=writer_gate,
                candidate_context=context,
            )
            metadata.update(
                {
                    "recovery_candidate_accepted": candidate_accepted,
                    "recovery_candidate_generation_permitted": (
                        candidate_generation_permitted
                    ),
                    "recovery_candidate_current_generation_permitted": (
                        current_generation_permitted
                    ),
                    "recovery_candidate_scope": scope,
                    "recovery_candidate_chunk_count": len(chunks),
                    "recovery_candidate_citation_count": len(citations),
                    "recovery_candidate_writer_gate_bound": bool(
                        writer_gate is not None
                    ),
                }
            )
            acceptance_key = (
                "corrective_recovery_accepted"
                if corrective
                else "self_rag_recovery_accepted"
            )
            rejection_key = (
                "corrective_recovery_rejected"
                if corrective
                else "self_rag_recovery_rejected"
            )
            metadata[acceptance_key] = candidate_accepted
            if not candidate_accepted:
                metadata[rejection_key] = rejection_reason
                metadata["recovery_candidate_rejection_reason"] = rejection_reason
                metadata["recovery_candidate_retained_scope"] = payload.active_scope
                metadata["recovery_candidate_retained_query"] = payload.active_query
                metadata["writer_gate_bound"] = bool(
                    payload.active_writer_gate is not None
                )
                active_enforcement = self.coordinator.resolve_writer_gate_enforcement(
                    payload.active_writer_gate
                )
                metadata["writer_gate_enforcement"] = (
                    active_enforcement.as_metadata(gate_id="G06/G11")
                )
                if pending_self_rag_round is not None:
                    completed_round = {
                        **pending_self_rag_round,
                        "error": str(rejection_reason or "recovery_not_accepted"),
                        "recovery_rejected": True,
                        "retained_scope": payload.active_scope,
                        "retrieved_chunk_count": len(chunks),
                        "citation_count": len(citations),
                    }
                    self_rag_rounds.append(completed_round)
                    metadata["self_rag_retrieval_rounds"] = list(
                        self_rag_rounds
                    )
                    metadata["self_rag_retrieve_more_success"] = False
                    updates.update(
                        {
                            "self_rag_retrieval_rounds": self_rag_rounds,
                            "pending_self_rag_round": None,
                        }
                    )
            else:
                # Candidate becomes active only after the Application acceptance
                # decision above. No active Evidence field is written before this.
                updates.update(
                    {
                        "citation_registry": registry.all(),
                        "retrieved_chunks": [*payload.retrieved_chunks, *chunks],
                        "active_evidence_bundle": result,
                        "active_context": context,
                        "active_chunks": list(chunks),
                        "active_citations": list(citations),
                        "active_writer_gate": writer_gate,
                        "active_query": query,
                        "active_scope": scope,
                    }
                )
                metadata["writer_gate_bound"] = bool(writer_gate is not None)
                writer_enforcement = (
                    self.coordinator.resolve_writer_gate_enforcement(writer_gate)
                )
                metadata["writer_gate_enforcement"] = (
                    writer_enforcement.as_metadata(gate_id="G06/G11")
                )
                if not candidate_generation_permitted:
                    block_reason = (
                        "writer_gate_missing_after_retrieval"
                        if writer_gate is None
                        else (
                            "legacy_writer_artifact_incomplete_after_retrieval"
                            if writer_gate.legacy_artifact_incomplete
                            else (
                                "writer_human_review_required_after_retrieval"
                                if writer_gate.gate_decision.human_review_required
                                else "writer_generation_blocked_after_retrieval"
                            )
                        )
                    )
                    if writer_gate is not None:
                        section = self.section_generation_service.build_gate_blocked_section(
                            shared_state,
                            project_input=payload.project_input,
                            section_title=title,
                            section_order=order,
                            rag_context=context,
                            citations=list(citations),
                            writer_gate_binding=writer_gate,
                        )
                    else:
                        section = self.section_generation_service.build_insufficient_evidence_section(
                            shared_state,
                            project_input=payload.project_input,
                            section_title=title,
                            section_order=order,
                            rag_context=context,
                            citations=list(citations),
                            assessment={
                                **dict(assessment),
                                "status": "not_assessed",
                                "reason": "final writer gate binding is missing",
                            },
                        )
                        section.extra = {
                            **dict(section.extra or {}),
                            "generation_block_reason": block_reason,
                            "writer_execution_status": "not_assessed",
                            "formal_deliverable_eligible": False,
                        }
                    updates.update(
                        {
                            "section": section,
                            "evidence_blocked": True,
                            "generation_check": {},
                        }
                    )
                    if pending_self_rag_round is not None:
                        completed_round = {
                            **pending_self_rag_round,
                            "error": block_reason,
                            "assessment": assessment,
                            "retrieved_chunk_count": len(chunks),
                            "citation_count": len(citations),
                        }
                        self_rag_rounds.append(completed_round)
                        metadata["self_rag_retrieval_rounds"] = list(
                            self_rag_rounds
                        )
                        metadata["self_rag_retrieve_more_success"] = False
                        updates.update(
                            {
                                "self_rag_retrieval_rounds": self_rag_rounds,
                                "pending_self_rag_round": None,
                            }
                        )
                    route = "finalize"
                else:
                    updates["generation_attempt"] = payload.generation_attempt + 1
                    if pending_self_rag_round is not None:
                        updates["pending_self_rag_round"] = {
                            **pending_self_rag_round,
                            "retrieved_chunk_count": len(chunks),
                            "citation_count": len(citations),
                        }
                    route = "generate"
            if corrective:
                metadata["corrective_retrieval_success"] = True
            elif route == "generate":
                metadata["self_rag_retrieve_more_success"] = True
            print(
                f"[{label}] END   section={title} success=True "
                f"chunks={len(chunks)} citations={len(citations)}",
                flush=True,
            )
        else:
            metadata[
                "corrective_retrieval_success"
                if corrective
                else "self_rag_retrieve_more_success"
            ] = False
            retrieval_error = result.error_message if result else "not_configured"
            metadata[
                "corrective_retrieval_error"
                if corrective
                else "self_rag_retrieve_more_error"
            ] = retrieval_error
            if pending_self_rag_round is not None:
                completed_round = {
                    **pending_self_rag_round,
                    "error": retrieval_error,
                }
                self_rag_rounds.append(completed_round)
                metadata["self_rag_retrieval_rounds"] = list(self_rag_rounds)
                updates.update(
                    {
                        "self_rag_retrieval_rounds": self_rag_rounds,
                        "pending_self_rag_round": None,
                    }
                )
            print(f"[{label}] END   section={title} success=False", flush=True)
        updates.update(
            {
                "tool_call_ids": list(dict.fromkeys(tool_call_ids)),
                "retrieval_metadata": metadata,
            }
        )
        updated = payload.model_copy(update=updates, deep=True)
        recovery_node = (
            "corrective_retrieval" if corrective else "retrieve_more"
        )
        updated = self._invoke_research_agent(
            updated,
            node_name=recovery_node,
            research_mode=(
                "corrective_retrieval"
                if corrective
                else "self_rag_recovery"
            ),
            retrieval_succeeded=bool(result is not None and result.success),
        )
        if (
            not self._pre_write_ready
            and updated.research_agent_payloads
        ):
            action = (
                updated.research_agent_payloads[-1]
                .evidence_summary.recommended_action
            )
            if route == "generate" and action != ResearchRecommendedAction.WRITE:
                route = "finalize"
        return self._update(
            updated,
            node_name=recovery_node,
            route=route,
            shared_state=shared_state,
        )

    def _retrieve_more(
        self,
        state: SectionExecutionGraphState,
    ) -> dict[str, Any]:
        payload, _ = self._restore(state)
        update = self._retrieve_recovery(state, corrective=False)
        emit_workflow_custom_event(
            "section_repaired",
            node_name="retrieve_more",
            section_id=str(payload.section_id),
            payload={"repair_type": "self_rag_retrieval"},
        )
        return update

    def _corrective_retrieval(
        self,
        state: SectionExecutionGraphState,
    ) -> dict[str, Any]:
        payload, _ = self._restore(state)
        update = self._retrieve_recovery(state, corrective=True)
        emit_workflow_custom_event(
            "section_repaired",
            node_name="corrective_retrieval",
            section_id=str(payload.section_id),
            payload={"repair_type": "corrective_retrieval"},
        )
        return update

    def _finalize_section(
        self,
        state: SectionExecutionGraphState,
    ) -> dict[str, Any]:
        payload, shared_state = self._restore(state)
        section = payload.section
        if section is None:
            content = (
                f"“{payload.section_title}”章节未生成有效内容，需人工复核。"
            )
            section = self.section_generation_service.build_insufficient_evidence_section(
                shared_state,
                project_input=payload.project_input,
                section_title=str(payload.section_title),
                section_order=int(payload.section_order or 0),
                rag_context=self._active_context_projection(payload),
                citations=list(payload.active_citations),
                assessment={"status": "not_assessed", "reason": content},
            )
        budget = WorkflowBudget.from_snapshot(payload.budget_state)
        if payload.budget_exhaustion_resource is not None:
            section = self.coordinator._mark_budget_exhausted(
                section,
                section_budget=budget,
                exhausted=WorkflowBudgetExceeded(
                    payload.budget_exhaustion_resource,
                    int(payload.budget_exhaustion_limit or 0),
                ),
            )
        active_context = self._active_context_projection(payload)
        active_bundle = payload.active_evidence_bundle or payload.document_evidence_bundle
        contract_sha = self._evidence_contract_sha(payload)
        metadata = dict(payload.retrieval_metadata)
        review_rejections = list(metadata.get(PWA004_REJECTION_KEY) or [])
        # Canonical assessment remains owned by active_bundle. Section metadata only
        # retains workflow/retrieval diagnostics, never a second Evidence authority.
        if payload.self_rag_retrieval_rounds:
            metadata["self_rag_retrieval_rounds"] = list(
                payload.self_rag_retrieval_rounds
            )
            metadata["self_rag_retrieve_more_success"] = bool(
                payload.self_rag_retrieval_rounds[-1].get("success")
            )
            metadata["self_rag_recheck"] = dict(payload.generation_check)
        if bool(payload.generation_check.get("need_retrieve_more")):
            metadata["self_rag_retrieve_more_unresolved"] = True
            metadata["self_rag_retrieve_more_budget_exhausted"] = bool(
                budget.retrieval_rounds >= budget.max_retrieval_rounds
            )
        evidence = SectionEvidenceBundleSchema(
            section_id=str(payload.section_id),
            section_title=str(payload.section_title),
            evidence_bundle=active_bundle,
            tool_call_ids=list(dict.fromkeys(payload.tool_call_ids)),
            recovery_count=payload.recovery_count,
            evidence_contract_sha256=contract_sha,
            writer_gate_binding=(
                payload.active_writer_gate.model_dump(
                    mode="json", exclude_none=True
                )
                if payload.active_writer_gate is not None
                else None
            ),
            metadata=metadata,
        )
        budget_usage = budget.snapshot()
        section = section.model_copy(deep=True)
        if review_rejections:
            # Writer content exists, but the exact Claim scope has no complete
            # VerificationResult set. This must not look like Section success.
            section.status = ExecutionStatus.FAILED
        section.extra = {
            **dict(section.extra or {}),
            **({PWA004_REJECTION_KEY: review_rejections,
                "canonical_claim_verification_incomplete": True,
                "pwa004_review_only_not_accepted": True}
               if review_rejections else {}),
            "evidence_scope": payload.active_scope,
            "evidence_query": payload.active_query,
            "evidence_tool_call_ids": list(dict.fromkeys(payload.tool_call_ids)),
            "evidence_contract_sha256": contract_sha,
            "corrective_retrieval_count": payload.recovery_count,
            "workflow_budget": budget_usage,
            "writer_gate_binding": (
                payload.active_writer_gate.model_dump(
                    mode="json", exclude_none=True
                )
                if payload.active_writer_gate is not None
                else None
            ),
            "writer_execution_status": (
                payload.active_writer_gate.execution_status.value
                if payload.active_writer_gate is not None
                else "not_assessed"
            ),
            "section_subgraph_nodes": [
                *payload.executed_nodes,
                "finalize_section",
            ],
            "section_agent_payload_counts": {
                "research": len(payload.research_agent_payloads),
                "writer": len(payload.writer_agent_payloads),
                "verifier": len(payload.verifier_agent_payloads),
            },
            "section_agent_actions": {
                "research": (
                    payload.research_agent_payloads[-1]
                    .evidence_summary.recommended_action.value
                    if payload.research_agent_payloads
                    and payload.research_agent_payloads[-1]
                    .evidence_summary.recommended_action is not None
                    else None
                ),
                "verifier": (
                    payload.verifier_agent_payloads[-1].action.value
                    if payload.verifier_agent_payloads
                    else None
                ),
            },
        }
        self.state_writer.add_generated_section(shared_state, section.section_id)
        result = SectionExecutionResultSchema(
            section=section,
            evidence=evidence,
            section_state=payload.section_state,
            canonical_plan_binding=payload.canonical_plan_binding,
            active_execution_lineage=payload.active_execution_lineage,
            active_evidence_package_binding=payload.active_evidence_package_binding,
            current_research_round_binding=payload.current_research_round_binding,
            writer_execution_ref=payload.writer_execution_ref,
            writer_generation_invocation_ref=(
                payload.writer_generation_invocation_ref
            ),
            section_paragraph_manifest=payload.section_paragraph_manifest,
            canonical_claim_coverage_proof_refs=(
                list(payload.canonical_claim_coverage_proof_refs)
            ),
            canonical_claim_verification_refs=(
                list(payload.canonical_claim_verification_refs)
            ),
            retrieved_chunks=list(payload.retrieved_chunks),
            rag_outputs=list(payload.rag_outputs),
            citation_registry=list(payload.citation_registry),
            budget_usage=budget_usage,
            need_human_review=bool(
                review_rejections
                or (
                    payload.budget_exhaustion_resource is not None
                    and budget.human_review_on_exhaustion
                )
                or (
                    not self._pre_write_ready
                    and (
                        payload.active_writer_gate is None
                        or (
                            payload.active_writer_gate.gate_decision.human_review_required
                        )
                    )
                )
                or bool(
                    isinstance((section.extra or {}).get("claim_audit"), dict)
                    and not (section.extra or {}).get("claim_audit", {}).get(
                        "passed", False
                    )
                )
            ),
            warnings=list(section.warnings),
            error=section.error,
        )
        dependencies = getattr(self.coordinator, "section_dependencies", None)
        if dependencies is not None and result.section_paragraph_manifest is not None and not review_rejections:
            checkpoint_current_execution()
            result = result.model_copy(update={
                "canonical_section_source_binding": dependencies.complete_source(result),
            })
        print(
            f"[Section {payload.section_order}/{payload.total_sections}] END   "
            f"{payload.section_title} status="
            f"{section.status.value if hasattr(section.status, 'value') else section.status} "
            f"chars={len(section.content)} truncated={section.truncation.truncated} "
            f"citations={len(section.citation_bindings)} "
            f"evidence_scope={payload.active_scope}",
            flush=True,
        )
        emit_workflow_custom_event(
            "section_completed",
            node_name="finalize_section",
            section_id=section.section_id,
            status=section.status.value,
            progress=(
                float(payload.section_order) / float(payload.total_sections)
                if payload.section_order and payload.total_sections
                else None
            ),
            payload={
                "section_title": payload.section_title,
                "section_order": payload.section_order,
                "citation_count": len(section.citation_bindings),
            },
        )
        updated = payload.model_copy(
            update={
                "section": section,
                "evidence": evidence,
                "result": result,
                "budget_state": budget_usage,
                "shared_state_payload": self.codec.dump_graph_state(shared_state),
            },
            deep=True,
        )
        return self._update(
            updated,
            node_name="finalize_section",
            route="end",
            shared_state=shared_state,
            completed=True,
        )

    def build_graph(self):
        builder = StateGraph(SectionExecutionGraphState)
        builder.add_node("prepare_section", self._prepare_section)
        builder.add_node(
            "retrieve_section_evidence",
            self._retrieve_section_evidence,
        )
        builder.add_node("evaluate_evidence_gate", self._evaluate_evidence_gate)
        builder.add_node(
            "evaluate_pre_write_admission",
            self._evaluate_pre_write_admission,
        )
        builder.add_node("bind_pre_write_gate", self._bind_pre_write_gate)
        builder.add_node(
            "evaluate_pre_write_enforcement",
            self._evaluate_pre_write_enforcement,
        )
        builder.add_node("commit_pre_write_route", self._commit_pre_write_route)
        builder.add_node(
            "dispatch_pre_write_recovery",
            self._dispatch_pre_write_recovery,
        )
        builder.add_node(
            "execute_pre_write_recovery",
            self._execute_pre_write_recovery,
        )
        builder.add_node(
            "complete_pre_write_recovery_dispatch",
            self._complete_pre_write_recovery_dispatch,
        )
        builder.add_node(
            "invoke_pre_write_writer",
            self._invoke_pre_write_writer,
        )
        builder.add_node("generate_section", self._generate_section)
        builder.add_node(
            "prepare_paragraph_pipeline",
            self._prepare_paragraph_pipeline,
        )
        builder.add_node("persist_paragraph", self._persist_paragraph)
        builder.add_node("precheck_paragraph", self._precheck_paragraph)
        builder.add_node("evaluate_generation", self._evaluate_generation)
        builder.add_node("retrieve_more", self._retrieve_more)
        builder.add_node("corrective_retrieval", self._corrective_retrieval)
        builder.add_node("finalize_section", self._finalize_section)

        builder.add_edge(START, "prepare_section")
        builder.add_conditional_edges(
            "prepare_section",
            self._route,
            {
                "retrieve": "retrieve_section_evidence",
                "evidence_gate": "evaluate_evidence_gate",
                "pre_write_admission": "evaluate_pre_write_admission",
                "finalize": "finalize_section",
            },
        )
        builder.add_conditional_edges(
            "retrieve_section_evidence",
            self._route,
            {
                "evidence_gate": "evaluate_evidence_gate",
                "pre_write_admission": "evaluate_pre_write_admission",
            },
        )
        builder.add_conditional_edges(
            "evaluate_evidence_gate",
            self._route,
            {"generate": "generate_section", "finalize": "finalize_section"},
        )
        builder.add_edge(
            "evaluate_pre_write_admission",
            "bind_pre_write_gate",
        )
        builder.add_edge(
            "bind_pre_write_gate",
            "evaluate_pre_write_enforcement",
        )
        builder.add_edge(
            "evaluate_pre_write_enforcement",
            "commit_pre_write_route",
        )
        builder.add_conditional_edges(
            "commit_pre_write_route",
            self._route,
            {
                "pre_write_recovery_dispatch": (
                    "dispatch_pre_write_recovery"
                ),
                "pre_write_writer": "invoke_pre_write_writer",
            },
        )
        builder.add_edge(
            "dispatch_pre_write_recovery",
            "execute_pre_write_recovery",
        )
        builder.add_conditional_edges(
            "execute_pre_write_recovery",
            self._route,
            {
                "pre_write_recovery_complete": (
                    "complete_pre_write_recovery_dispatch"
                ),
                "pre_write_route": "commit_pre_write_route",
            },
        )
        builder.add_edge(
            "complete_pre_write_recovery_dispatch",
            "evaluate_pre_write_enforcement",
        )
        builder.add_edge(
            "invoke_pre_write_writer",
            "prepare_paragraph_pipeline",
        )
        builder.add_edge("generate_section", "prepare_paragraph_pipeline")
        builder.add_conditional_edges(
            "prepare_paragraph_pipeline",
            self._route,
            {
                "persist_paragraph": "persist_paragraph",
                "evaluate_generation": "evaluate_generation",
            },
        )
        builder.add_edge("persist_paragraph", "precheck_paragraph")
        builder.add_conditional_edges(
            "precheck_paragraph",
            self._route,
            {
                "persist_paragraph": "persist_paragraph",
                "evaluate_generation": "evaluate_generation",
            },
        )
        builder.add_conditional_edges(
            "evaluate_generation",
            self._route,
            {
                "retrieve_more": "retrieve_more",
                "corrective_retrieval": "corrective_retrieval",
                "generate": "generate_section",
                "finalize": "finalize_section",
            },
        )
        builder.add_conditional_edges(
            "retrieve_more",
            self._route,
            {"generate": "generate_section", "finalize": "finalize_section"},
        )
        builder.add_conditional_edges(
            "corrective_retrieval",
            self._route,
            {"generate": "generate_section", "finalize": "finalize_section"},
        )
        builder.add_edge("finalize_section", END)
        return builder.compile(checkpointer=self._langgraph_checkpointer)

    def execute(
        self,
        request: SectionGraphExecutionRequestSchema,
        *,
        shared_state: GraphStateSchema,
        citation_registry: DocumentCitationRegistry,
        workflow_namespace: str,
    ) -> SectionExecutionResultSchema:
        """Execute a serializable section subgraph and project state back."""

        checkpoint_current_execution()
        section_id = (
            f"section_{shared_state.run_id}_{request.section_plan.section_order:03d}"
        )
        task_budget = current_task_draining_budget()
        task_budget_exhaustion: TaskDrainingBudgetExceeded | None = None
        task_budget_admitted = False
        if task_budget is not None:
            try:
                task_budget.begin_section(section_id)
                task_budget_admitted = True
            except TaskDrainingBudgetExceeded as exc:
                task_budget_exhaustion = exc

        initial = SectionExecutionGraphPayloadSchema(
            task_id=shared_state.task_id,
            run_id=shared_state.run_id,
            document_id=request.document_id,
            project_input=request.project_input,
            section_plan=request.section_plan,
            structured_facts=list(request.structured_facts),
            previous_sections=list(request.previous_sections),
            shared_state_payload=self.codec.dump_graph_state(shared_state),
            document_evidence_bundle=request.document_evidence_bundle,
            document_tool_call_ids=list(request.document_tool_call_ids),
            section_retrieval_enabled=request.section_retrieval_enabled,
            corrective_retrieval_enabled=request.corrective_retrieval_enabled,
            parallel_isolated=request.parallel_isolated,
            budget_policy_override=dict(request.budget_policy_override),
            canonical_plan_binding=request.canonical_plan_binding,
            active_execution_lineage=request.active_execution_lineage,
            research_authorization_binding=request.research_authorization_binding,
            active_evidence_package_binding=request.active_evidence_package_binding,
            current_research_round_binding=request.current_research_round_binding,
            citation_registry=citation_registry.all(),
            budget_exhaustion_resource=(
                f"task_{task_budget_exhaustion.resource}"
                if task_budget_exhaustion is not None
                else None
            ),
            budget_exhaustion_limit=(
                task_budget_exhaustion.limit
                if task_budget_exhaustion is not None
                else None
            ),
            budget_state=(
                {
                    "task_budget": task_budget.snapshot(),
                    "task_draining": task_budget.draining,
                }
                if task_budget is not None
                else {}
            ),
        )
        state = self.codec.build_initial_state(initial)
        self.codec.assert_json_serializable(state)
        try:
            checkpoint_current_execution()
            final = self.build_graph().invoke(
                state,
                config=self.checkpoint_config(
                    workflow_namespace=workflow_namespace,
                    run_id=shared_state.run_id,
                    section_id=section_id,
                ),
            )
            checkpoint_current_execution()
            payload = self.codec.restore_payload(final)
            if payload.result is None:
                raise RuntimeError("section subgraph completed without result")
            restored_state = self.codec.restore_graph_state(payload)
            checkpoint_current_execution()
            self.graph_state_codec.project_onto(shared_state, restored_state)
            citation_registry.restore(payload.citation_registry)
        except BaseException:
            if task_budget is not None and task_budget_admitted:
                task_budget.abort_section(section_id)
            raise
        else:
            if task_budget is not None and task_budget_admitted:
                task_budget.finish_section(section_id)
        return payload.result

    def replan(
        self,
        *,
        workflow_namespace: str,
        run_id: str,
        section_id: str,
        revised_plan: SectionPlanSchema,
        shared_state: GraphStateSchema,
        citation_registry: DocumentCitationRegistry,
    ) -> SectionExecutionResultSchema:
        """Start one superseding attempt in the existing Section namespace."""

        checkpoint_current_execution()
        history = self.checkpoint_history(
            workflow_namespace=workflow_namespace,
            run_id=run_id,
            section_id=section_id,
        )
        persisted = None
        for checkpoint_tuple in history:
            checkpoint = getattr(checkpoint_tuple, "checkpoint", {})
            values = (
                checkpoint.get("channel_values", {})
                if isinstance(checkpoint, dict)
                else {}
            )
            raw = values.get("section_payload")
            if not isinstance(raw, dict):
                continue
            persisted = self.codec.restore_payload(
                {
                    "section_graph_state_version": values.get(
                        "section_graph_state_version"
                    ),
                    "section_payload": raw,
                }
            )
            break
        if persisted is None:
            raise KeyError(
                f"section checkpoint not found: run_id={run_id} section_id={section_id}"
            )
        replanned = MainlineReplanAttempt.prepare(
            payload=persisted,
            revised_plan=revised_plan,
        )
        checkpoint_current_execution()
        final = self.build_graph().invoke(
            self.codec.build_initial_state(replanned),
            config=self.checkpoint_config(
                workflow_namespace=workflow_namespace,
                run_id=run_id,
                section_id=section_id,
            ),
        )
        checkpoint_current_execution()
        payload = self.codec.restore_payload(final)
        if payload.result is None:
            raise RuntimeError("replanned section subgraph completed without result")
        restored_state = self.codec.restore_graph_state(payload)
        checkpoint_current_execution()
        self.graph_state_codec.project_onto(shared_state, restored_state)
        citation_registry.restore(payload.citation_registry)
        return payload.result

    def resume(
        self,
        *,
        workflow_namespace: str,
        run_id: str,
        section_id: str,
        shared_state: GraphStateSchema,
        citation_registry: DocumentCitationRegistry,
    ) -> SectionExecutionResultSchema:
        """Resume v3 state only after owner-specific Route currentness proof."""

        checkpoint_current_execution()
        for checkpoint_tuple in self.checkpoint_history(
            workflow_namespace=workflow_namespace,
            run_id=run_id,
            section_id=section_id,
        ):
            checkpoint = getattr(checkpoint_tuple, "checkpoint", {})
            values = (
                checkpoint.get("channel_values", {})
                if isinstance(checkpoint, dict)
                else {}
            )
            raw = values.get("section_payload")
            if not isinstance(raw, dict):
                continue
            restored = self.codec.restore_payload(
                {
                    "section_graph_state_version": values.get(
                        "section_graph_state_version"
                    ),
                    "section_payload": raw,
                }
            )
            route_decision = restored.current_pre_write_route_decision
            if route_decision is not None:
                subject, write_instance_id = self._pre_write_identity(restored)
                chain = self._pre_write_chain(restored)
                applicability = self._pre_write_applicability(restored)
                enforcement = restored.current_enforcement_result
                recovery_control = restored.current_recovery_control_facts
                if enforcement is None or recovery_control is None:
                    raise ValueError(
                        "restored RouteDecision has incomplete currentness inputs"
                    )
                checkpoint_current_execution()
                self.coordinator.commit_pre_write_route(
                    writer_subject_ref=subject,
                    write_instance_id=write_instance_id,
                    decision=chain.decision,
                    binding=chain.binding,
                    applicability=applicability,
                    enforcement=enforcement,
                    recovery_control=recovery_control,
                    route_decision=route_decision,
                )
            break

        checkpoint_current_execution()
        final = self.build_graph().invoke(
            None,
            config=self.checkpoint_config(
                workflow_namespace=workflow_namespace,
                run_id=run_id,
                section_id=section_id,
            ),
        )
        checkpoint_current_execution()
        payload = self.codec.restore_payload(final)
        if payload.result is None:
            raise RuntimeError("resumed section subgraph completed without result")
        restored_state = self.codec.restore_graph_state(payload)
        checkpoint_current_execution()
        self.graph_state_codec.project_onto(shared_state, restored_state)
        citation_registry.restore(payload.citation_registry)
        return payload.result

    def _validate_target_recovery_sources(
        self,
        payload: SectionExecutionGraphPayloadSchema,
        *,
        expected_canonical_plan_binding: SectionPlanBindingSchema | None,
    ) -> None:
        """Resolve canonical owners before checkpoint reuse/resume decisions."""

        runtime = self.research_runtime
        if runtime is None:
            return
        plan_binding = payload.canonical_plan_binding
        lineage = payload.active_execution_lineage
        authorization = payload.research_authorization_binding
        missing = [
            name
            for name, value in (
                ("canonical_plan_binding", plan_binding),
                ("active_execution_lineage", lineage),
                ("research_authorization_binding", authorization),
            )
            if value is None
        ]
        if missing:
            raise RuntimeError(
                "Target Section recovery lacks canonical source refs: "
                + ", ".join(missing)
            )
        assert plan_binding is not None
        assert lineage is not None
        assert authorization is not None
        if (
            expected_canonical_plan_binding is not None
            and plan_binding != expected_canonical_plan_binding
        ):
            raise ValueError("Section recovery canonical Plan binding mismatch")

        runtime.section_plan_runtime.validate_plan_binding_current(plan_binding)
        committed_gate_research = (
            authorization.authorization_kind == "GATE_RECOVERY_RESEARCH"
        )
        source = (payload.result.canonical_section_source_binding
                  if payload.completed and payload.result is not None else None)
        if source is not None:
            # Terminal transport validates current owners without reauthorizing a
            # dispatch against historical Run/Attempt lifecycle revisions.
            from apps.enterprise_document.services.agent_rag_v1.workflow_runtime import TargetWorkflowRuntime

            dependencies = getattr(self.coordinator, "section_dependencies", None)
            if dependencies is None:
                raise RuntimeError("Completed source recovery requires dependency adapter")
            dependencies.validate_source(source)
            if (
                source.plan_binding != plan_binding
                or source.producer_attempt_id != lineage.attempt_id
                or source.input_snapshot_version != lineage.input_snapshot_version
                or lineage.run_id != payload.run_id
                or source.writer_execution_ref != payload.writer_execution_ref
                or source.writer_invocation_ref != payload.writer_generation_invocation_ref
                or source.paragraph_manifest != payload.section_paragraph_manifest
                or payload.result.active_execution_lineage != lineage
            ):
                raise ValueError("Completed source checkpoint identity mismatch")
            if not committed_gate_research:
                expected_authorization = TargetWorkflowRuntime.workflow_research_authorization_ref(
                    lineage=lineage, section_id=str(payload.section_id),
                )
                if (authorization.authorization_kind != "WORKFLOW_RESEARCH"
                        or authorization.authorization_ref != expected_authorization):
                    raise ValueError("Completed source Research occurrence mismatch")
                proof = dependencies.store.get_json(
                    expected_authorization + "/dependency-input",
                    task_id=payload.task_id, run_id=payload.run_id,
                    section_id=payload.section_id, attempt_id=lineage.attempt_id,
                    input_snapshot_version=lineage.input_snapshot_version,
                )
                if (SectionPlanBindingSchema.model_validate(proof["plan_binding"]) != plan_binding
                        or dependencies.require_dependencies(plan_binding).fingerprint
                        != proof["dependency_fingerprint"]):
                    raise ValueError("Completed source dependency observation is stale")
        else:
            current_lineage = runtime.lifecycle_service.resolve_active_lineage(
                lineage.run_id,
                lineage.attempt_scope_ref,
            )
            if current_lineage != lineage:
                raise ValueError("Section recovery Attempt lineage is stale")
        if (
            lineage.attempt_scope_ref != f"section:{payload.section_id}"
            or plan_binding.section_id != payload.section_id
        ):
            raise ValueError("Section recovery canonical Section identity mismatch")

        # Workflow keeps its existing owner validation. Persisted Gate Research
        # must resolve committed truth below, never authorize a new dispatch.
        # Only the external Gate dispatch path supplies transient owner context.
        if source is None and not committed_gate_research:
            runtime.authorize_round_ref(
                run_id=payload.run_id,
                section_id=str(payload.section_id),
                source_plan_binding=plan_binding,
                authorization_binding=authorization,
                active_attempt=lineage,
            )

        if committed_gate_research and payload.current_research_round_binding is None:
            raise ValueError("Section recovery Gate Research requires committed Round binding")
        record = None
        if payload.current_research_round_binding is not None:
            record = runtime.validate_round_binding_current(
                payload.current_research_round_binding
            )
            if (
                record.round_ref.run_id != payload.run_id
                or record.round_ref.section_id != payload.section_id
                or record.round_ref.attempt_id != lineage.attempt_id
            ):
                raise ValueError("Section recovery ResearchRound lineage mismatch")
        if committed_gate_research:
            assert record is not None
            if (
                record.authorization_binding != authorization
                or record.round_ref.run_id != lineage.run_id
                or record.round_ref.attempt_scope_ref != lineage.attempt_scope_ref
                or record.source_plan_version_ref != plan_binding.plan_version_ref
                or plan_binding.plan_version_ref.logical_ref.run_id != payload.run_id
            ):
                raise ValueError("Section recovery committed Gate Research source mismatch")
            # Reuse the Research owner's read-only fresh Plan/Fact/Requirements
            # resolution, including when a technical-failure Round has no Package.
            runtime.resolve_research_requirements(plan_binding)
            expected_package_ref = (
                record.package_progress.prior_evidence_package_version_ref
                if record.execution_outcome is ResearchRoundExecutionOutcome.TECHNICAL_FAILURE
                else record.package_progress.resulting_evidence_package_version_ref
            )
            package = payload.active_evidence_package_binding
            if (package.version_ref if package is not None else None) != expected_package_ref:
                raise ValueError("Section recovery committed Gate Research Package mismatch")
        if payload.active_evidence_package_binding is not None:
            package_content = runtime.validate_evidence_package_binding_current(
                payload.active_evidence_package_binding,
                require_source_fresh=True,
            )
            if committed_gate_research and (
                payload.active_evidence_package_binding.version_ref.logical_ref.run_id != payload.run_id
                or package_content.section_plan_source.plan_version_ref != plan_binding.plan_version_ref
                or package_content.section_plan_source.section_id != payload.section_id
                or (
                    record.execution_outcome is not ResearchRoundExecutionOutcome.TECHNICAL_FAILURE
                    and record.final_evidence_bundle_binding != package_content.evidence_bundle_binding
                )
            ):
                raise ValueError("Section recovery committed Gate Research Package source mismatch")

        if bool(payload.writer_execution_ref) != bool(
            payload.writer_generation_invocation_ref
        ):
            raise ValueError("Section recovery Writer exact refs are incomplete")
        if payload.writer_execution_ref is not None:
            if payload.writer_generation_invocation_ref is None:
                raise ValueError("Section recovery Writer invocation ref is missing")
            execution = self.coordinator.resolve_pre_write_writer_execution(
                payload.writer_execution_ref
            )
            invocation = self.coordinator.resolve_pre_write_writer_invocation(
                payload.writer_generation_invocation_ref
            )
            logical_key = payload.writer_execution_ref.logical_execution_key
            if (
                execution.logical_execution_key != logical_key
                or payload.writer_generation_invocation_ref.logical_execution_key
                != logical_key
                or invocation.writer_subject_ref != logical_key.writer_subject_ref
                or invocation.write_instance_id != logical_key.write_instance_id
                or invocation.route_decision_ref != logical_key.route_decision_ref
                or logical_key.write_instance_id != lineage.attempt_id
            ):
                raise ValueError("Section recovery Writer lineage mismatch")

        manifest = payload.section_paragraph_manifest
        if manifest is not None and source is None:
            paragraph_runtime = self.paragraph_runtime
            if paragraph_runtime is None:
                raise RuntimeError(
                    "Section recovery Paragraph manifest requires ParagraphRuntime"
                )
            resolved_manifest = paragraph_runtime.load_manifest(manifest.manifest_ref)
            if resolved_manifest != manifest:
                raise ValueError("Section recovery Paragraph manifest is stale")
            for member in manifest.members:
                binding = paragraph_runtime.resolve_paragraph_binding(
                    logical_ref=member.logical_ref,
                    manifest_ref=manifest.manifest_ref,
                )
                if binding is None or binding.version_ref != member:
                    raise ValueError(
                        "Section recovery Paragraph member is not current"
                    )
                paragraph_runtime.validate_paragraph_binding_current(binding)
        # A completed review-only checkpoint is a transport projection, not a
        # canonical Section Source. Validate every durable Candidate and exact
        # Claim/Support/Writer/Attempt binding before reusing its body.
        if payload.completed and payload.result is not None:
            review_rows = (payload.retrieval_metadata or {}).get(PWA004_REJECTION_KEY)
            if review_rows:
                if (payload.result.canonical_section_source_binding is not None
                        or payload.result.section.extra.get(PWA004_REJECTION_KEY) != review_rows
                        or payload.result.active_execution_lineage != payload.active_execution_lineage
                        or payload.result.section_paragraph_manifest != payload.section_paragraph_manifest
                        or payload.result.canonical_claim_verification_refs != payload.canonical_claim_verification_refs):
                    raise ValueError("PWA004 recovered review Section checkpoint is not exact")
                self._pwa004_validate_recovered_rejections(payload)

    def recover_or_reuse(
        self,
        *,
        workflow_namespace: str,
        task_id: str,
        run_id: str,
        document_id: str,
        section_id: str,
        project_input,
        section_plan: SectionPlanSchema,
        shared_state: GraphStateSchema,
        citation_registry: DocumentCitationRegistry,
        expected_canonical_plan_binding: SectionPlanBindingSchema | None = None,
    ) -> SectionExecutionResultSchema | None:
        """Reuse a completed Section or resume its exact durable cursor."""

        checkpoint_current_execution()
        history = self.checkpoint_history(
            workflow_namespace=workflow_namespace,
            run_id=run_id,
            section_id=section_id,
        )
        persisted = None
        for checkpoint_tuple in history:
            checkpoint = getattr(checkpoint_tuple, "checkpoint", {})
            values = (
                checkpoint.get("channel_values", {})
                if isinstance(checkpoint, dict)
                else {}
            )
            if not isinstance(values.get("section_payload"), dict):
                continue
            persisted = self.codec.restore_payload(
                {
                    "section_graph_state_version": values.get(
                        "section_graph_state_version"
                    ),
                    "section_payload": values["section_payload"],
                }
            )
            break
        if persisted is None:
            return None

        checkpoint_current_execution()
        self._validate_target_recovery_sources(
            persisted,
            expected_canonical_plan_binding=expected_canonical_plan_binding,
        )

        if persisted.active_execution_lineage is not None:
            expected_snapshot = persisted.active_execution_lineage.input_snapshot_version
            persisted_snapshot = expected_snapshot
        else:
            expected_snapshot = MainlineAttemptGuard.snapshot_version(
                project_input,
                section_plan,
            )
            persisted_snapshot = (
                str(persisted.section_state.attempt.input_snapshot_version or "")
                if persisted.section_state is not None
                else ""
            )
        if (
            persisted.task_id != task_id
            or persisted.run_id != run_id
            or persisted.document_id != document_id
            or persisted.section_id != section_id
            or persisted.project_input.model_dump(mode="json", round_trip=True)
            != project_input.model_dump(mode="json", round_trip=True)
            or persisted.section_plan.model_dump(mode="json", round_trip=True)
            != section_plan.model_dump(mode="json", round_trip=True)
            or (persisted_snapshot and persisted_snapshot != expected_snapshot)
        ):
            raise ValueError("section recovery immutable identity mismatch")

        if persisted.completed:
            if persisted.result is None:
                raise RuntimeError("completed Section checkpoint has no result")
            restored_state = self.codec.restore_graph_state(persisted)
            checkpoint_current_execution()
            self.graph_state_codec.project_onto(shared_state, restored_state)
            citation_registry.restore(persisted.citation_registry)
            return persisted.result

        return self.resume(
            workflow_namespace=workflow_namespace,
            run_id=run_id,
            section_id=section_id,
            shared_state=shared_state,
            citation_registry=citation_registry,
        )

    def checkpoint_history(
        self,
        *,
        workflow_namespace: str,
        run_id: str,
        section_id: str,
    ) -> list:
        if self.checkpointer is None:
            return []
        return list(
            self.checkpointer.list(
                self.checkpoint_config(
                    workflow_namespace=workflow_namespace,
                    run_id=run_id,
                    section_id=section_id,
                )
            )
        )

    def recover_running_invocation(
        self,
        *,
        workflow_namespace: str,
        run_id: str,
        section_id: str,
        invocation: RunningInvocationRecoveryInputSchema,
    ):
        """Decide recovery from the latest persisted Section checkpoint only."""

        history = self.checkpoint_history(
            workflow_namespace=workflow_namespace,
            run_id=run_id,
            section_id=section_id,
        )
        for checkpoint_tuple in history:
            checkpoint = getattr(checkpoint_tuple, "checkpoint", {})
            values = (
                checkpoint.get("channel_values", {})
                if isinstance(checkpoint, dict)
                else {}
            )
            if not isinstance(values.get("section_payload"), dict):
                continue
            payload = self.codec.restore_payload(
                {
                    "section_graph_state_version": values.get(
                        "section_graph_state_version"
                    ),
                    "section_payload": values["section_payload"],
                }
            )
            return self.running_invocation_recovery.decide(
                invocation=invocation,
                payload=payload,
            )
        raise KeyError(
            f"section checkpoint not found: run_id={run_id} section_id={section_id}"
        )

    def assert_native_recovery_admissible(self, *, workflow_namespace: str,
                                         task_id: str, run_id: str, section_ids) -> None:
        """Reject uncertain old effects before a recovery event or re-entry."""
        if self.running_invocation_recovery.lifecycle_service is None:
            if self.coordinator.pre_write_ready:
                raise ValueError("INDETERMINATE: native recovery lifecycle owner is unavailable")
            return
        lifecycle = self.running_invocation_recovery.lifecycle_service
        run = lifecycle.load_run_lifecycle(run_id)
        if run.identity.task_id != task_id:
            raise ValueError("INDETERMINATE: native recovery Task/Run identity differs")
        if not section_ids:
            raise ValueError("INDETERMINATE: no native Section invocation scope is proven")
        for section_id in section_ids:
            row = self.checkpointer.get_tuple(self.checkpoint_config(
                workflow_namespace=workflow_namespace, run_id=run_id, section_id=section_id))
            values = (row.checkpoint or {}).get("channel_values", {}) if row is not None else {}
            if not isinstance(values.get("section_payload"), dict):
                raise ValueError("INDETERMINATE: native Section checkpoint is unavailable")
            payload = self.codec.restore_payload({
                "section_graph_state_version": values.get("section_graph_state_version"),
                "section_payload": values["section_payload"]})
            if payload.task_id != task_id or payload.run_id != run_id or payload.section_id != section_id:
                raise ValueError("INDETERMINATE: native Section recovery identity differs")
            view = self.running_invocation_recovery.reconcile_writer(payload)
            if view["outcome"] != "RECOVERED_DURABLE_RESULT":
                raise ValueError(f"{view['outcome']}: native Writer effect cannot authorize recovery")
        # Canonical failure is not reversible from a receipt or policy. A new
        # Attempt would need immutable native PNE; Writer has no such proof.
        if run.status.value != "running":
            raise ValueError("INDETERMINATE: native result read does not authorize a new Attempt")
