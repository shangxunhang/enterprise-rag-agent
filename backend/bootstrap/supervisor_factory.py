"""Composition root for the Agent-RAG runtime."""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path
from typing import Any, Mapping

from agent.agent_registry import AgentRegistry
from agent.runtime.official_checkpoint_saver import (
    SQLiteCheckpointResource,
    create_sqlite_checkpoint_resource,
)
from agent.runtime.workflow_schema import WorkflowDefinitionSchema
from agent.supervisor_agent import SupervisorAgent
from apps.enterprise_document.agents.project_input_normalizer_agent import (
    ProjectInputNormalizerAgent,
)
from apps.enterprise_document.agents.scheme_writer_agent import SchemeWriterAgent
from apps.enterprise_document.schemas.agent_rag_v1 import AgentExecutionContextSchema, AgentRole
from apps.enterprise_document.schemas.agent_rag_v1.budgets import (
    CanonicalBudgetVectorSchema,
)
from apps.enterprise_document.schemas.agent_rag_v1.lifecycle import (
    RunCompletionAggregateResult,
    RunLifecycleStatus,
    AttemptLifecycleStatus,
)
from apps.enterprise_document.schemas.post_write.release import (
    PostWriteReleaseManifestSchema,
)
from apps.enterprise_document.schemas.pre_write_admission import (
    EvidenceRequirementsSnapshotSchema,
    WriterAdmissionContractSchema,
)
from apps.enterprise_document.schemas.pre_write_control import PreWriteRoute
from apps.enterprise_document.services.agent_rag_v1.artifact_runtime import ArtifactRuntime
from apps.enterprise_document.services.agent_rag_v1.budget_manager import BudgetManager
from apps.enterprise_document.services.agent_rag_v1.disk_guard import (
    DiskBudgetGuard,
    GIB,
)
from apps.enterprise_document.services.agent_rag_v1.fact_snapshot_runtime import (
    FactSnapshotRuntime,
)
from apps.enterprise_document.services.agent_rag_v1.lifecycle_runtime import (
    RunAttemptLifecycleService,
)
from apps.enterprise_document.services.agent_rag_v1.model_tool_budget_runtime import (
    CanonicalInvocationBudgetRouter,
    CanonicalInvocationBudgetAdapter,
)
from apps.enterprise_document.services.agent_rag_v1.paragraph_runtime import ParagraphRuntime
from apps.enterprise_document.services.agent_rag_v1.repair_authorization_runtime import (
    ParagraphRepairAuthorizationRuntime, ParagraphRepairAuthorizationStore,
)
from apps.enterprise_document.services.agent_rag_v1.post_write_composition import (
    TargetPostWriteComposition,
)
from apps.enterprise_document.services.agent_rag_v1.claim_composition import (
    TargetClaimRuntimeValidators,
)
from apps.enterprise_document.services.agent_rag_v1.claim_support_composition import TargetClaimSupportComposition
from apps.enterprise_document.services.agent_rag_v1.claim_verifier_composition import TargetClaimVerifierComposition
from apps.enterprise_document.services.agent_rag_v1.post_write_claim_projection import TargetPostWriteClaimProjection
from apps.enterprise_document.services.agent_rag_v1.claim_verification_runtime import (
    ClaimVerificationRuntime,
)
from apps.enterprise_document.services.agent_rag_v1.paragraph_composition import (
    TargetParagraphRuntimeValidators,
)
from apps.enterprise_document.services.agent_rag_v1.plan_runtime import SectionPlanRuntime
from apps.enterprise_document.services.agent_rag_v1.research_runtime import ResearchRuntime
from apps.enterprise_document.services.agent_rag_v1.tool_gateway import ToolGateway, RegisteredTool
from apps.enterprise_document.services.agent_rag_v1.tool_store import JsonlToolExecutionStore
from apps.enterprise_document.services.scheme_writer.agent_integration.section_agent_ports import (
    MainlineResearchToolGateway,
    MainlineResearchToolSession,
)
from apps.enterprise_document.services.agent_rag_v1.workflow_runtime import (
    TaskRequestFactSourceResolver,
    TargetWorkflowRuntime,
)
from apps.enterprise_document.services.agent_rag_v1.storage_lifecycle import (
    CanonicalStorageSubtype,
    CompositeStorageCatalog,
    FileArtifactStorageAdapter,
    RetentionClass,
    SQLiteCheckpointStorageAdapter,
    StorageLifecycleService,
    StorageObjectClass,
)
from apps.enterprise_document.services.agent_rag_v1.writer_context import (
    TargetWriterContextComposer,
)
from apps.enterprise_document.services.scheme_writer.agent_integration import (
    build_mainline_planner_adapter,
    build_round5_section_agent_adapter,
)
from apps.enterprise_document.services.scheme_writer.gate_recovery_research_authorization import (
    GateRecoveryResearchAuthorizationValidator,
    ResearchAuthorizationDispatcher,
)
from apps.enterprise_document.services.scheme_writer.pre_write_recovery import (
    PreWriteRecoveryController,
)
from apps.enterprise_document.services.scheme_writer.pre_write_router import PreWriteRouter
from apps.enterprise_document.services.scheme_writer.section_execution_coordinator import (
    PreWriteCoordinatorServices,
)
from apps.enterprise_document.services.scheme_writer.writer_admission import (
    EvidenceRequirementsMaterializer,
    LLMCriterionSupportEvaluator,
    SCHEME_GENERATION_MINIMAL_CRITERION_ID,
    SCHEME_GENERATION_MINIMAL_PROFILE_REF,
    SCHEME_GENERATION_MINIMAL_REQUIREMENT_ID,
    SCHEME_GENERATION_MINIMAL_TASK_REF,
    WriterAdmissionFactAssessor,
    WriterAdmissionPolicy,
    scheme_generation_minimal_profile,
    scheme_generation_minimal_task_contract,
)
from apps.enterprise_document.services.scheme_writer.writer_admission_enforcement import (
    WriterAdmissionEnforcement,
)
from apps.enterprise_document.services.scheme_writer.writer_gate_binding import (
    WriterGateApplicabilityValidator,
    WriterGateBinder,
)
from apps.enterprise_document.services.scheme_writer.writer_invocation import (
    PreWriteWriterInvocationService,
    WriterInvocationBuilder,
    WriterInvocationIntegrityValidator,
)
from apps.enterprise_document.workflows.section_execution_graph import (
    SectionExecutionGraph,
)
from apps.enterprise_document.workflows.top_level_business_graph import (
    BusinessNodeContract,
    EnterpriseDocumentWorkflowEngine,
    _default_node_contracts,
)
from bootstrap.agent_quality_factory import AgentQualityFactory
from bootstrap.model_factory import ModelGatewayFactory
from bootstrap.observability_factory import ObservabilityFactory
from bootstrap.rag_service_factory import RAGServiceFactory
from bootstrap.runtime_options import RuntimeOptions
from core.config import AppSettings
from core.runtime.pre_write_execution_store import SQLitePreWriteExecutionStore
from core.runtime.pre_write_route_decision_store import SQLitePreWriteRouteDecisionStore
from core.runtime.side_effects import JsonlSideEffectCommitStore
from model_gateway.integrations.langchain import (
    LangChainModelGatewayFacade,
    build_model_gateway_runnable,
)
from observability.pre_write_trace import PreWriteTraceEmitter
from observability.sqlite_trace_sink import SQLiteTraceSink
from prompt_manager.prompt_manager import PromptManager
from rag.integrations.langchain import (
    LangChainRAGServiceFacade,
    build_rag_runnable,
)
from rag.store.evidence_bundle_store import EvidenceBundleSnapshotStore
from schemas.rag import RetrievalAccessScopeSchema
from task.task_manager import JsonlTaskManager
from apps.enterprise_document.services.agent_rag_v1.planner_fact_requirements import (
    TargetPlannerFactRequirements,
)
from apps.enterprise_document.services.agent_rag_v1.claim_materialization_runtime import (
    TargetClaimExtractionRuntime,
)


class SupervisorFactory:
    """Build the singular Target production runtime and its dependencies."""

    def __init__(
        self,
        *,
        observability_factory: ObservabilityFactory | None = None,
        model_factory: ModelGatewayFactory | None = None,
        rag_factory: RAGServiceFactory | None = None,
        agent_quality_factory: AgentQualityFactory | None = None,
    ) -> None:
        self.observability_factory = observability_factory or ObservabilityFactory()
        self.model_factory = model_factory or ModelGatewayFactory()
        self.rag_factory = rag_factory or RAGServiceFactory()
        self.agent_quality_factory = agent_quality_factory or AgentQualityFactory()

    @staticmethod
    def _node_contracts(options: RuntimeOptions) -> dict[str, BusinessNodeContract] | None:
        """Override only explicitly selected technical guards on fresh contracts."""
        overrides = {
            "execute_sections": options.execute_sections_timeout_seconds,
            "evaluate_document_gate": options.evaluate_document_gate_timeout_seconds,
        }
        if all(value is None for value in overrides.values()):
            return None
        contracts = dict(_default_node_contracts())
        for name, value in overrides.items():
            if value is not None:
                contracts[name] = replace(contracts[name], timeout_seconds=value)
        return contracts

    @staticmethod
    def _close_construction_resources(resources: list[Any]) -> None:
        """Best-effort reverse cleanup for a runtime that never became usable."""

        for resource in reversed(resources):
            close = getattr(resource, "close", None)
            if not callable(close):
                continue
            try:
                close()
            except Exception:
                # Preserve the startup failure as the primary exception.  A runtime
                # that never returned cannot safely continue after any such failure.
                pass

    @staticmethod
    def _validate_target_startup(
        *,
        settings: AppSettings,
        runs_dir: Path,
        captures_dir: Path,
        services: PreWriteCoordinatorServices,
        evaluator: LLMCriterionSupportEvaluator,
        execution_store: SQLitePreWriteExecutionStore,
        route_decision_store: SQLitePreWriteRouteDecisionStore,
        trace_sink: SQLiteTraceSink,
        trace_emitter: PreWriteTraceEmitter,
        checkpoint_resource: SQLiteCheckpointResource,
        coordinator,
        evidence_bundle_store: EvidenceBundleSnapshotStore,
        lifecycle_service: RunAttemptLifecycleService,
        artifact_runtime: ArtifactRuntime,
        fact_snapshot_runtime: FactSnapshotRuntime,
        section_plan_runtime: SectionPlanRuntime,
        research_runtime: ResearchRuntime,
        research_authorization_dispatcher: ResearchAuthorizationDispatcher,
        paragraph_runtime: ParagraphRuntime,
        claim_verification_runtime: ClaimVerificationRuntime,
        target_workflow_runtime: TargetWorkflowRuntime,
        canonical_budget_provider,
        target_post_write_composition: TargetPostWriteComposition,
        storage_lifecycle_service: StorageLifecycleService,
        disk_budget_guard: DiskBudgetGuard,
        storage_filesystem_roots: tuple[Path, ...],
    ) -> None:
        """Fail closed before publishing a usable production Supervisor."""

        if settings.gate_enforcement_mode not in {"strict", "shadow"}:
            raise RuntimeError("invalid Gate enforcement mode at Target startup")
        if settings.gate_recovery_rounds_limit < 0:
            raise RuntimeError("invalid Gate Recovery round limit at Target startup")
        if settings.gate_no_progress_rounds_limit < 1:
            raise RuntimeError("invalid Gate no-progress limit at Target startup")

        expected_requirements = EvidenceRequirementsMaterializer.materialize_profile_task(
            profile=scheme_generation_minimal_profile(),
            task_contract=scheme_generation_minimal_task_contract(),
        )
        if services.requirements != expected_requirements:
            raise RuntimeError("deployment-fixed Requirements Snapshot is not canonical")
        if services.requirements.source_profile_ref != SCHEME_GENERATION_MINIMAL_PROFILE_REF:
            raise RuntimeError("unexpected deployment Business Profile")
        if services.requirements.source_task_ref != SCHEME_GENERATION_MINIMAL_TASK_REF:
            raise RuntimeError("unexpected deployment Task Contract")
        if len(services.requirements.requirements) != 1:
            raise RuntimeError("minimal Business Profile must contain one Requirement")
        requirement = services.requirements.requirements[0]
        if requirement.requirement_id != SCHEME_GENERATION_MINIMAL_REQUIREMENT_ID:
            raise RuntimeError("unexpected deployment Requirement identity")
        if len(requirement.criteria) != 1:
            raise RuntimeError("minimal Requirement must contain one Criterion")
        if requirement.criteria[0].criterion_id != SCHEME_GENERATION_MINIMAL_CRITERION_ID:
            raise RuntimeError("unexpected deployment Criterion identity")
        if getattr(services.fact_assessor, "_evaluator", None) is not evaluator:
            raise RuntimeError("production LLM CriterionSupportEvaluator is not attached")
        if services.recovery_controller._execution_store is not execution_store:
            raise RuntimeError("Recovery does not use the canonical execution store")
        if research_runtime.authorization_validator is not research_authorization_dispatcher:
            raise RuntimeError("Research does not use the canonical authorization dispatcher")
        if research_authorization_dispatcher.workflow_runtime is not target_workflow_runtime:
            raise RuntimeError("Research dispatcher does not use the canonical Workflow owner")
        gate_validator = research_authorization_dispatcher.recovery_validator
        if (gate_validator.recovery_controller is not services.recovery_controller
                or gate_validator.route_decision_store is not services.route_decision_store
                or gate_validator.execution_store is not execution_store):
            raise RuntimeError("Research dispatcher does not share canonical Gate owners")
        if services.writer_invocation.execution_store is not execution_store:
            raise RuntimeError("Writer does not use the canonical execution store")
        if services.route_decision_store is not route_decision_store:
            raise RuntimeError("Coordinator does not use the canonical RouteDecision store")
        if services.writer_invocation.route_decision_store is not route_decision_store:
            raise RuntimeError("Writer does not use the canonical RouteDecision store")
        if services.trace_emitter is not trace_emitter:
            raise RuntimeError("required Target Trace emitter is not attached")
        if trace_emitter.trace_sink is not trace_sink:
            raise RuntimeError("required Target Trace emitter uses the wrong sink")

        effective_runs_dir = runs_dir.expanduser().resolve()
        expected_paths = {
            "checkpoint": (effective_runs_dir / "langgraph_checkpoints.sqlite3").resolve(),
            "execution": (effective_runs_dir / "execution_control.sqlite3").resolve(),
            "trace": (effective_runs_dir / "run_trace.sqlite3").resolve(),
        }
        actual_paths = {
            "checkpoint": checkpoint_resource.path,
            "execution": execution_store.database_path,
            "trace": trace_sink.database_path,
        }
        if route_decision_store.database_path != execution_store.database_path:
            raise RuntimeError(
                "RouteDecision and Writer execution stores must share execution_control.sqlite3"
            )
        if actual_paths != expected_paths:
            raise RuntimeError("Target SQLite stores do not use the effective runs_dir")
        if len(set(actual_paths.values())) != 3:
            raise RuntimeError("Target SQLite persistence domains must be distinct")
        if not coordinator.pre_write_ready:
            raise RuntimeError("SectionExecutionCoordinator is not Target-ready")
        if coordinator.require_pre_write_services() is not services:
            raise RuntimeError("Coordinator published a competing pre-write service set")
        if evidence_bundle_store.root != (runs_dir / "rag_evidence_bundles").resolve():
            raise RuntimeError("canonical RAG EvidenceBundle store is outside runs_dir")
        if coordinator.evidence_service.evidence_bundle_store is not evidence_bundle_store:
            raise RuntimeError("SchemeEvidenceService does not use canonical RAG Bundle store")
        if artifact_runtime.lifecycle_service is not lifecycle_service:
            raise RuntimeError("ArtifactRuntime does not share canonical Lifecycle owner")
        if fact_snapshot_runtime.artifact_runtime is not artifact_runtime:
            raise RuntimeError("FactSnapshotRuntime does not share canonical Artifact owner")
        if section_plan_runtime.artifact_runtime is not artifact_runtime:
            raise RuntimeError("SectionPlanRuntime does not share canonical Artifact owner")
        if section_plan_runtime.fact_snapshot_runtime is not fact_snapshot_runtime:
            raise RuntimeError("SectionPlanRuntime does not share canonical Fact owner")
        if research_runtime.artifact_runtime is not artifact_runtime:
            raise RuntimeError("ResearchRuntime does not share canonical Artifact owner")
        if research_runtime.section_plan_runtime is not section_plan_runtime:
            raise RuntimeError("ResearchRuntime does not share canonical Plan owner")
        if research_runtime.fact_snapshot_runtime is not fact_snapshot_runtime:
            raise RuntimeError("ResearchRuntime does not share canonical Fact owner")
        if research_runtime.lifecycle_service is not lifecycle_service:
            raise RuntimeError("ResearchRuntime does not share canonical Lifecycle owner")
        if getattr(coordinator, "research_runtime", None) is not research_runtime:
            raise RuntimeError("Coordinator does not use canonical ResearchRuntime")
        if getattr(coordinator, "paragraph_runtime", None) is not paragraph_runtime:
            raise RuntimeError("Coordinator does not use canonical ParagraphRuntime")
        if paragraph_runtime.artifact_runtime is not artifact_runtime:
            raise RuntimeError("ParagraphRuntime does not share canonical Artifact owner")
        if paragraph_runtime.lifecycle_service is not lifecycle_service:
            raise RuntimeError("ParagraphRuntime does not share canonical Lifecycle owner")
        if paragraph_runtime.manifest_ledger_path.resolve() != (
            effective_runs_dir / "paragraph_manifest.jsonl"
        ).resolve():
            raise RuntimeError("Paragraph Manifest ledger is outside effective runs_dir")
        if (
            getattr(coordinator, "claim_verification_runtime", None)
            is not claim_verification_runtime
        ):
            raise RuntimeError("Coordinator does not use canonical ClaimVerificationRuntime")
        extraction = getattr(coordinator, "claim_extraction_runtime", None)
        if extraction is None or extraction.claim_runtime is not claim_verification_runtime:
            raise RuntimeError("Target Claim extraction producer is not composed")
        if claim_verification_runtime.paragraph_runtime is not paragraph_runtime:
            raise RuntimeError("ClaimVerificationRuntime does not share Paragraph owner")
        if claim_verification_runtime.lifecycle_service is not lifecycle_service:
            raise RuntimeError("ClaimVerificationRuntime does not share Lifecycle owner")
        if claim_verification_runtime.ledger_path.resolve() != (
            effective_runs_dir / "claim_verification.jsonl"
        ).resolve():
            raise RuntimeError("Claim Verification ledger is outside effective runs_dir")
        if getattr(coordinator, "evidence_service", None) is None:
            raise RuntimeError("Coordinator evidence service is missing")
        if target_workflow_runtime.lifecycle_service is not lifecycle_service:
            raise RuntimeError("TargetWorkflowRuntime does not share Lifecycle owner")
        if target_workflow_runtime.fact_snapshot_runtime is not fact_snapshot_runtime:
            raise RuntimeError("TargetWorkflowRuntime does not share Fact owner")
        if target_workflow_runtime.section_plan_runtime is not section_plan_runtime:
            raise RuntimeError("TargetWorkflowRuntime does not share Plan owner")
        if not callable(canonical_budget_provider):
            raise RuntimeError("canonical Budget provider is not callable")
        guard = lifecycle_service.assert_run_committable
        if (execution_store.run_commit_guard != guard
                or not execution_store.require_run_commit_guard):
            raise RuntimeError("Target Writer Run commit guard is not composed")
        if getattr(canonical_budget_provider, "run_commit_guard", None) != guard:
            raise RuntimeError("Target Budget Run commit guard is not composed")
        database = target_post_write_composition.database
        if database.run_commit_guard != guard or not database.require_run_commit_guard:
            raise RuntimeError("Target PostWrite Run commit guard is not composed")
        repair = getattr(target_post_write_composition, "repair_provider", None)
        validators = getattr(paragraph_runtime.authorization_validator, "__self__", None)
        if (repair is None or getattr(repair, "budget_provider", None) is not canonical_budget_provider
                or getattr(validators, "repair_provider", None) is not repair
                or repair.store.run_commit_guard != guard
                or not repair.store.require_run_commit_guard):
            raise RuntimeError("Target Repair guarded owners are not composed")
        if (
            target_post_write_composition.lifecycle_service
            is not lifecycle_service
        ):
            raise RuntimeError("Target Post-write does not share Lifecycle owner")
        if (
            target_post_write_composition.research_runtime
            is not research_runtime
        ):
            raise RuntimeError("Target Post-write does not share Research owner")
        if (
            target_post_write_composition.writer_execution_store
            is not execution_store
        ):
            raise RuntimeError("Target Post-write does not share Writer execution owner")
        if target_post_write_composition.database.path.resolve() != (
            effective_runs_dir / "post_write.sqlite3"
        ).resolve():
            raise RuntimeError("Target Post-write SQLite owner is outside effective runs_dir")
        if target_post_write_composition.output_store.root != (
            effective_runs_dir / "target_validated_output"
        ).resolve():
            raise RuntimeError("Target validated output store is outside effective runs_dir")
        if target_post_write_composition.release_store.root != (
            effective_runs_dir / "target_release"
        ).resolve():
            raise RuntimeError("Target Release store is outside effective runs_dir")
        if target_post_write_composition.assessment_store.path.resolve() != (
            effective_runs_dir / "run_completion_assessments.jsonl"
        ).resolve():
            raise RuntimeError("RunCompletionAssessment store is outside effective runs_dir")
        release = target_post_write_composition.release_service
        release_guard = release.input_guard
        if release.publication_store is not target_post_write_composition.release_store:
            raise RuntimeError("Target Release does not share publication store")
        if release.release_commit_fence.database is not database:
            raise RuntimeError("Target Release does not share PostWrite fence database")
        if (release_guard.run_completion_assessment_resolver
                != target_post_write_composition.assessment_store.resolve_exact):
            raise RuntimeError("Target Release does not share Assessment resolver")
        if (release_guard.run_lifecycle_revision_resolver
                != target_post_write_composition._resolve_run_lifecycle_revision):
            raise RuntimeError("Target Release does not share exact Lifecycle resolver")
        for attribute, owner in (
            ("artifact_state_store", target_post_write_composition.artifact_store),
            ("control_store", target_post_write_composition.control_store),
            ("currentness_resolver", target_post_write_composition.currentness_resolver),
            ("artifact_usability_resolver", target_post_write_composition.usability_resolver),
            ("output_conformance_verifier", target_post_write_composition.output_conformance_verifier),
        ):
            if getattr(release_guard, attribute, None) is not owner:
                raise RuntimeError("Target Release does not share " + attribute)
        if release_guard.output_conformance_verifier.artifact_store is not target_post_write_composition.output_store:
            raise RuntimeError("Target Release does not share validated output store")
        if not isinstance(storage_lifecycle_service, StorageLifecycleService):
            raise RuntimeError("SIF-13 StorageLifecycleService is not attached")
        if storage_lifecycle_service.family_registry.represented_classes() != frozenset(
            StorageObjectClass
        ):
            raise RuntimeError("SIF-13 managed-family registry does not cover five classes")
        if not isinstance(disk_budget_guard, DiskBudgetGuard):
            raise RuntimeError("SIF-13 DiskBudgetGuard is not attached")
        expected_storage_roots = (
            effective_runs_dir,
            captures_dir.expanduser().resolve(),
        )
        actual_storage_roots = tuple(
            Path(item).expanduser().resolve() for item in storage_filesystem_roots
        )
        if actual_storage_roots != expected_storage_roots:
            raise RuntimeError("SIF-13 storage filesystem roots are not exact")
        if disk_budget_guard.soft_free_ratio != settings.disk_soft_free_ratio:
            raise RuntimeError("Disk Guard SOFT ratio differs from AppSettings")
        if disk_budget_guard.hard_free_ratio != settings.disk_hard_free_ratio:
            raise RuntimeError("Disk Guard HARD ratio differs from AppSettings")
        if disk_budget_guard.critical_free_ratio != settings.disk_critical_free_ratio:
            raise RuntimeError("Disk Guard CRITICAL ratio differs from AppSettings")
        if disk_budget_guard.soft_free_bytes != int(settings.disk_soft_free_gib * GIB):
            raise RuntimeError("Disk Guard SOFT bytes differ from AppSettings")
        if disk_budget_guard.hard_free_bytes != int(settings.disk_hard_free_gib * GIB):
            raise RuntimeError("Disk Guard HARD bytes differ from AppSettings")
        if disk_budget_guard.critical_free_bytes != int(
            settings.disk_critical_free_gib * GIB
        ):
            raise RuntimeError("Disk Guard CRITICAL bytes differ from AppSettings")
        if (
            getattr(coordinator, "stage_service", None) is not None
            and getattr(coordinator.stage_service, "target_post_write_composition", None)
            is not target_post_write_composition
        ):
            raise RuntimeError("StageService does not share Target Post-write composition")

        owner_paths = {
            lifecycle_service.path.resolve(),
            artifact_runtime.ledger_path.resolve(),
            research_runtime.ledger_path.resolve(),
        }
        expected_owner_paths = {
            (effective_runs_dir / "lifecycle.jsonl").resolve(),
            (effective_runs_dir / "artifact_runtime.jsonl").resolve(),
            (effective_runs_dir / "research_runtime.jsonl").resolve(),
        }
        if owner_paths != expected_owner_paths:
            raise RuntimeError("canonical owner ledgers are outside effective runs_dir")

        forbidden_flags = {
            "enable_gate_v2",
            "legacy_gate_fallback",
            "target_gate_enabled",
            "use_legacy_gate",
            "prewrite_v2_enabled",
        }
        if forbidden_flags & set(AppSettings.__dataclass_fields__):
            raise RuntimeError("Gate authority feature flag is forbidden")

    def build(
        self,
        *,
        workflow: WorkflowDefinitionSchema,
        runs_dir: Path,
        captures_dir: Path,
        task_manager: JsonlTaskManager,
        settings: AppSettings,
        options: RuntimeOptions,
        allow_legacy_unscoped_rag: bool = False,
        retrieval_access_scope: RetrievalAccessScopeSchema | None = None,
    ) -> SupervisorAgent:
        """Assemble one isolated Target runtime for a workflow execution."""

        if (
            retrieval_access_scope is not None
            and not isinstance(retrieval_access_scope, RetrievalAccessScopeSchema)
        ):
            raise TypeError(
                "retrieval_access_scope must be a trusted RetrievalAccessScopeSchema instance"
            )
        if (
            options.use_real_rag
            and not allow_legacy_unscoped_rag
            and retrieval_access_scope is None
        ):
            raise ValueError(
                "trusted retrieval_access_scope is required for production secure retrieval"
            )

        runs_dir = runs_dir.expanduser().resolve()
        from apps.enterprise_document.services.agent_rag_v1.memory_composition import TargetDurableMemoryComposition
        memory_composition = TargetDurableMemoryComposition.from_settings(
            required=options.durable_memory_required, settings=settings)
        trace, capture = self.observability_factory.build(
            settings,
            runs_dir,
            captures_dir,
        )
        prompt_manager = PromptManager(prompt_root=settings.prompt_root)
        construction_resources: list[Any] = []

        try:
            model_gateway_backend = self.model_factory.build(settings, trace)
            model_gateway = LangChainModelGatewayFacade(
                delegate=model_gateway_backend,
                runnable=build_model_gateway_runnable(
                    model_gateway_backend,
                    run_name="production_model_gateway",
                ),
            )
            construction_resources.append(model_gateway)

            rag_service_backend = self.rag_factory.build(
                options,
                trace,
                model_gateway=model_gateway,
                model_name=settings.default_model_name,
                allow_legacy_unscoped=allow_legacy_unscoped_rag,
            )
            rag_service = LangChainRAGServiceFacade(
                delegate=rag_service_backend,
                runnable=build_rag_runnable(
                    rag_service_backend,
                    run_name="production_rag_service",
                ),
            )
            construction_resources.append(rag_service)

            rag_service_name = "RAGService" if options.use_real_rag else "FakeRAGService"
            evidence_bundle_store = EvidenceBundleSnapshotStore(
                runs_dir / "rag_evidence_bundles"
            )
            agent_quality = self.agent_quality_factory.build(
                options=options,
                model_gateway=model_gateway,
                model_name=settings.default_model_name,
            )
            canonical_budget_config = dict(
                agent_quality.metadata.get("canonical_budget") or {}
            )
            budget_dimension_names = (
                "logical_model_calls",
                "logical_tool_calls",
                "model_tokens_total",
                "research_rounds",
                "repair_attempts",
                "generation_attempts",
                "replan_attempts",
                "true_retry_attempts",
            )
            missing_budget_config = [
                name
                for name in (*budget_dimension_names, "max_elapsed_seconds", "policy_ref", "policy_version")
                if name not in canonical_budget_config
            ]
            if missing_budget_config:
                raise RuntimeError(
                    "canonical Budget deployment config is incomplete: "
                    + ", ".join(missing_budget_config)
                )
            canonical_budget_limits = CanonicalBudgetVectorSchema(
                **{
                    name: int(canonical_budget_config[name])
                    for name in budget_dimension_names
                }
            )
            canonical_budget_managers: dict[tuple[str, str], BudgetManager] = {}
            lifecycle_service = RunAttemptLifecycleService(runs_dir / "lifecycle.jsonl")

            def canonical_budget_provider(task_id: str, run_id: str, *,
                                          require_existing_account: bool = False) -> BudgetManager:
                key = (str(task_id or "").strip(), str(run_id or "").strip())
                if not key[0] or not key[1]:
                    raise ValueError("canonical Budget requires task_id/run_id")
                manager = canonical_budget_managers.get(key)
                if manager is None:
                    manager = BudgetManager.canonical(
                        task_id=key[0],
                        run_id=key[1],
                        limits=canonical_budget_limits,
                        max_elapsed_seconds=int(
                            canonical_budget_config["max_elapsed_seconds"]
                        ),
                        storage_path=runs_dir / "canonical_budget.jsonl",
                        policy_ref=str(canonical_budget_config["policy_ref"]),
                        policy_version=str(canonical_budget_config["policy_version"]),
                        run_commit_guard=lifecycle_service.assert_run_committable,
                        require_run_commit_guard=True,
                        require_existing_account=require_existing_account,
                    )
                    canonical_budget_managers[key] = manager
                elif require_existing_account:
                    manager.canonical_state()
                return manager

            canonical_budget_provider.run_commit_guard = lifecycle_service.assert_run_committable

            canonical_model_budget_router = CanonicalInvocationBudgetRouter(
                canonical_budget_provider,
                existing_manager_provider=lambda task_id, run_id: canonical_budget_provider(
                    task_id, run_id, require_existing_account=True),
            )
            if not hasattr(model_gateway_backend, "budget_runtime"):
                raise RuntimeError(
                    "production ModelGateway has no canonical Budget runtime seam"
                )
            model_gateway_backend.budget_runtime = canonical_model_budget_router

            prompt_id = settings.default_scheme_prompt_id
            if prompt_id in ("scheme_generation_v1", "scheme_section_generation_v1"):
                original_prompt_id = prompt_id
                prompt_id = "scheme_section_generation_v2"
                print(
                    f"[Prompt Runtime] deprecated prompt_id={original_prompt_id}; "
                    "redirected to scheme_section_generation_v2"
                )

            agents = AgentRegistry()
            agents.register(ProjectInputNormalizerAgent())
            scheme_writer = SchemeWriterAgent(
                rag_service=rag_service,
                model_gateway=model_gateway,
                data_capture_recorder=capture,
                prompt_manager=prompt_manager,
                prompt_id=prompt_id,
                model_name=settings.default_model_name,
                rag_service_name=rag_service_name,
                section_retrieval_default_enabled=options.use_real_rag,
                enable_agent_self_rag=options.enable_agent_self_rag,
                enable_semantic_gate=options.enable_semantic_gate,
                semantic_gate_model_name=options.semantic_gate_model_name,
                generation_checker=agent_quality.generation_checker,
                repair_strategy=agent_quality.repair_strategy,
                generation_quality_metadata=agent_quality.metadata,
                retrieval_access_scope=retrieval_access_scope,
                evidence_bundle_store=evidence_bundle_store,
                section_max_concurrency=options.section_max_concurrency,
                gate_enforcement_mode=settings.gate_enforcement_mode,
            )
            profile = scheme_generation_minimal_profile()
            task_contract = scheme_generation_minimal_task_contract()
            requirements = EvidenceRequirementsMaterializer.materialize_profile_task(
                profile=profile,
                task_contract=task_contract,
            )
            evaluator = LLMCriterionSupportEvaluator(
                model_gateway=model_gateway,
                model_name=settings.default_model_name,
                require_run_identity=True,
            )

            # Wave-13 canonical owner graph. All durable owners share the same
            # application run domain and the same SIF-02 coordination ledger.
            side_effect_commit_store = JsonlSideEffectCommitStore(
                runs_dir / "side_effect_commits.jsonl"
            )
            artifact_runtime = ArtifactRuntime(
                runs_dir / "artifact_runtime.jsonl",
                lifecycle_service=lifecycle_service,
                coordination_store=side_effect_commit_store,
            )
            fact_snapshot_runtime = FactSnapshotRuntime(
                artifact_runtime=artifact_runtime,
                source_binding_resolver=TaskRequestFactSourceResolver(task_manager),
            )
            section_plan_runtime = SectionPlanRuntime(
                artifact_runtime=artifact_runtime,
                fact_snapshot_runtime=fact_snapshot_runtime,
                requirements_resolver=lambda _source: requirements,
            )
            target_workflow_runtime = TargetWorkflowRuntime(
                task_manager=task_manager,
                lifecycle_service=lifecycle_service,
                fact_snapshot_runtime=fact_snapshot_runtime,
                section_plan_runtime=section_plan_runtime,
                requirements=requirements,
            )
            planner_fact_requirements = TargetPlannerFactRequirements(
                workflow_runtime=target_workflow_runtime, model_gateway=model_gateway,
            )
            scheme_writer.stage_service.attach_planner_adapter(
                build_mainline_planner_adapter(
                    planning_service=scheme_writer.document_planning_service,
                    trace_sink=trace,
                    fact_requirement_runtime=planner_fact_requirements,
                )
            )
            if getattr(scheme_writer.stage_service.planner_adapter.planner_agent.reasoner,
                       "fact_requirement_runtime", None) is not planner_fact_requirements:
                raise RuntimeError("Target Planner fact-requirement runtime is not composed")
            execution_store = SQLitePreWriteExecutionStore(
                runs_dir / "execution_control.sqlite3",
                run_commit_guard=lifecycle_service.assert_run_committable,
                require_run_commit_guard=True,
            )
            route_decision_store = SQLitePreWriteRouteDecisionStore(
                runs_dir / "execution_control.sqlite3"
            )
            recovery_controller = PreWriteRecoveryController(
                execution_store=execution_store
            )
            recovery_authorization_validator = GateRecoveryResearchAuthorizationValidator(
                recovery_controller=recovery_controller,
                route_decision_store=route_decision_store,
                execution_store=execution_store,
            )
            research_authorization_dispatcher = ResearchAuthorizationDispatcher(
                workflow_runtime=target_workflow_runtime,
                recovery_validator=recovery_authorization_validator,
            )
            research_runtime = ResearchRuntime(
                artifact_runtime=artifact_runtime,
                section_plan_runtime=section_plan_runtime,
                fact_snapshot_runtime=fact_snapshot_runtime,
                evidence_bundle_resolver=scheme_writer.evidence_service.resolve_canonical_bundle,
                authorization_validator=research_authorization_dispatcher,
                lifecycle_service=lifecycle_service,
                ledger_path=runs_dir / "research_runtime.jsonl",
                coordination_store=side_effect_commit_store,
            )

            tool_execution_store = JsonlToolExecutionStore(
                runs_dir / "research_tool_results.jsonl"
            )

            def canonical_tool_gateway_provider(
                context: AgentExecutionContextSchema,
            ) -> ToolGateway:
                # Capability material comes from the active application binding,
                # not from a caller-supplied authorized_tool_names projection.
                adapter = scheme_writer.section_execution_coordinator.section_agent_adapter
                if adapter is None or adapter.research_tool_gateway is None:
                    raise RuntimeError("canonical Research binding is not attached")
                session = adapter.research_tool_gateway.session(context)
                if not isinstance(session, MainlineResearchToolSession):
                    raise PermissionError("canonical Tool requires bound Research request")
                request = session.request
                round_ref = request.research_round_ref
                if round_ref is None:
                    raise PermissionError("canonical Tool requires authorized ResearchRoundRef")
                authorized_context = context.model_copy(deep=True)
                expected_arguments = {
                    "query": request.query,
                    "scope": request.scope,
                    "section_id": request.section_id,
                    "section_title": request.section_title,
                }
                if (
                    context.agent_role is not AgentRole.RESEARCH
                    or context.task_id != request.shared_state.task_id
                    or context.run_id != request.shared_state.run_id
                    or context.run_id != round_ref.run_id
                    or context.section_id != round_ref.section_id
                    or context.attempt_id != round_ref.attempt_id
                    or context.logical_operation_id
                    != f"research-round:{round_ref.research_round_id}"
                ):
                    raise PermissionError("canonical Tool request identity mismatch")

                def validate_authorization(
                    invoked: AgentExecutionContextSchema,
                    registration: RegisteredTool,
                    arguments: Mapping[str, Any],
                ) -> None:
                    if (
                        invoked != authorized_context
                        or registration.name != MainlineResearchToolGateway.tool_name
                        or registration.version != MainlineResearchToolGateway.tool_version
                        or any(arguments.get(key) != value for key, value in expected_arguments.items())
                        or arguments.get("research_mode") != authorized_context.execution_mode
                    ):
                        raise PermissionError("canonical Research Tool capability mismatch")

                def validate_currentness(invoked: AgentExecutionContextSchema) -> None:
                    identity = lifecycle_service.load_run_identity(invoked.run_id)
                    lineage = lifecycle_service.resolve_active_lineage(
                        invoked.run_id, round_ref.attempt_scope_ref,
                    )
                    if (
                        identity.task_id != invoked.task_id
                        or lineage.run_status is not RunLifecycleStatus.RUNNING
                        or lineage.attempt_status is not AttemptLifecycleStatus.RUNNING
                        or lineage.attempt_scope_ref != f"section:{invoked.section_id}"
                        or lineage.attempt_id != invoked.attempt_id
                        or lineage.input_snapshot_version != invoked.input_snapshot_version
                    ):
                        raise PermissionError("canonical Research Tool Attempt is stale")

                return ToolGateway(
                    execution_store=tool_execution_store,
                    budget_runtime=CanonicalInvocationBudgetAdapter(
                        canonical_budget_provider(context.task_id, context.run_id)
                    ),
                    authorization_validator=validate_authorization,
                    currentness_validator=validate_currentness,
                )

            scheme_writer.section_execution_coordinator.attach_section_agent_adapter(
                build_round5_section_agent_adapter(
                    evidence_service=scheme_writer.evidence_service,
                    model_gateway=model_gateway,
                    trace_sink=trace,
                    canonical_tool_gateway_provider=canonical_tool_gateway_provider,
                )
            )

            target_post_write_composition = TargetPostWriteComposition.create(
                runs_dir=runs_dir,
                research_runtime=research_runtime,
                writer_execution_store=execution_store,
                budget_manager_provider=canonical_budget_provider,
                lifecycle_service=lifecycle_service,
                require_run_commit_guard=True,
            )
            target_post_write_composition.attach_requirement_adherence(
                model_gateway=model_gateway
            )
            target_post_write_composition.attach_coverage(
                model_gateway=model_gateway
            )
            target_post_write_composition.attach_consistency(
                model_gateway=model_gateway
            )

            def storage_scope_gc_closed(scope_ref: str) -> bool:
                """Prove terminal + completion + release closure before destructive GC."""

                prefix = "run:"
                raw = str(scope_ref or "").strip()
                if not raw.startswith(prefix) or not raw[len(prefix):]:
                    return False
                run_id = raw[len(prefix):]
                try:
                    lifecycle = lifecycle_service.load_run_lifecycle(run_id)
                except Exception:
                    return False
                if (
                    lifecycle.status is not RunLifecycleStatus.SUCCESS
                    or lifecycle.run_completion_assessment_ref is None
                ):
                    # FAILED/CANCELLED/PARTIAL terminal-like states can still have
                    # recovery/reconciliation semantics outside SIF-13. Without an
                    # explicit GC-closed authority they remain protected fail-closed.
                    return False
                try:
                    assessment = (
                        target_post_write_composition.assessment_store.resolve_exact(
                            lifecycle.run_completion_assessment_ref
                        )
                    )
                except Exception:
                    return False
                if (
                    assessment.run_id != run_id
                    or assessment.aggregate_result
                    is not RunCompletionAggregateResult.SUCCESS
                ):
                    return False

                try:
                    manifest_refs = tuple(
                        reference
                        for reference in target_post_write_composition.release_store.list_references()
                        if reference.run_id == run_id
                        and reference.artifact_type
                        == "post_write_release_manifest"
                    )
                except Exception:
                    return False
                if not manifest_refs:
                    # SUCCESS-before-manifest is a crash/recovery window, not a
                    # storage-GC closure point.
                    return False
                for reference in manifest_refs:
                    try:
                        payload = target_post_write_composition.release_store.get_json(
                            reference.artifact_ref,
                            task_id=reference.task_id,
                            run_id=run_id,
                        )
                        manifest = PostWriteReleaseManifestSchema.model_validate(
                            payload
                        )
                        release = target_post_write_composition.release_service
                        request = release.build_request(
                            release_contract_version=manifest.release_contract_version,
                            task_id=manifest.task_id, run_id=manifest.run_id,
                            document_logical_id=manifest.document_logical_id,
                            document_artifact_ref=manifest.document_artifact_ref,
                            post_write_control_decision_ref=manifest.post_write_control_decision_ref,
                            output_format=manifest.output_format,
                            validated_output_binding_sha256=manifest.validated_output_binding_sha256,
                            publication_target_id=manifest.publication_target_id,
                        )
                        restored = release._restore_existing(
                            request=request, key=manifest.idempotency_key)
                        if restored is None or restored.manifest != manifest:
                            return False
                    except Exception:
                        return False
                    if (
                        manifest.run_id != run_id
                        or manifest.task_id != lifecycle.identity.task_id
                        or manifest.run_completion_assessment_ref
                        != lifecycle.run_completion_assessment_ref
                        or manifest.run_lifecycle_revision
                        != lifecycle.lifecycle_revision
                    ):
                        return False
                return True

            def artifact_scope_ref(reference) -> str:
                return (
                    f"run:{reference.run_id}"
                    if reference.run_id
                    else f"task:{reference.task_id}"
                )

            release_storage_adapter = FileArtifactStorageAdapter(
                target_post_write_composition.release_store,
                object_family="post_write_release_integrity",
                storage_class=StorageObjectClass.CANONICAL,
                canonical_subtype=CanonicalStorageSubtype.CONTROL,
                retention_class_resolver=lambda _ref: RetentionClass.R0_PROTECTED,
                scope_ref_resolver=artifact_scope_ref,
                retention_expires_resolver=lambda _ref: None,
                protection_resolver=lambda _ref: {"release_protected": True},
                managed_by_local_sif13=False,
                delete_supported=False,
            )
            checkpoint_storage_adapter = SQLiteCheckpointStorageAdapter(
                runs_dir / "langgraph_checkpoints.sqlite3",
                scope_terminal_resolver=storage_scope_gc_closed,
            )
            validated_output_storage_adapter = FileArtifactStorageAdapter(
                target_post_write_composition.output_store,
                object_family="post_write_validated_output",
                storage_class=StorageObjectClass.PROJECTION,
                retention_class_resolver=lambda _ref: RetentionClass.R3_AUDIT_DEBUG_WINDOW,
                scope_ref_resolver=artifact_scope_ref,
                retention_expires_resolver=lambda _ref: None,
                managed_by_local_sif13=False,
                delete_supported=False,
            )
            storage_catalog = CompositeStorageCatalog(
                scope_terminal_resolver=storage_scope_gc_closed,
                adapters=(
                    checkpoint_storage_adapter,
                    release_storage_adapter,
                    validated_output_storage_adapter,
                ),
            )
            storage_lifecycle_service = StorageLifecycleService(storage_catalog)
            disk_budget_guard = DiskBudgetGuard.from_settings(settings)
            storage_filesystem_roots = (
                runs_dir.resolve(),
                captures_dir.resolve(),
            )
            scheme_writer.capture_service.attach_storage_pressure_policy(
                lambda scope_ref: disk_budget_guard.evaluate(
                    captures_dir,
                    scope_ref,
                )[1]
            )
            trace_sink, trace_emitter = (
                self.observability_factory.build_required_pre_write_trace(
                    settings,
                    runs_dir,
                )
            )
            target_writer_context_composer = TargetWriterContextComposer(
                fact_snapshot_runtime=fact_snapshot_runtime,
                section_plan_runtime=section_plan_runtime,
                research_runtime=research_runtime,
                lifecycle_service=lifecycle_service,
                model_gateway=model_gateway,
            )
            paragraph_validators = TargetParagraphRuntimeValidators(
                lifecycle_service=lifecycle_service,
                fact_snapshot_runtime=fact_snapshot_runtime,
                section_plan_runtime=section_plan_runtime,
                research_runtime=research_runtime,
                route_decision_store=route_decision_store,
                writer_execution_store=execution_store,
            )
            paragraph_runtime = ParagraphRuntime(
                artifact_runtime=artifact_runtime,
                authorization_validator=paragraph_validators.validate_authorization,
                source_validator=paragraph_validators.validate_source,
                manifest_authorization_validator=(
                    paragraph_validators.validate_manifest_authorization
                ),
                manifest_ledger_path=runs_dir / "paragraph_manifest.jsonl",
            )
            claim_validators = TargetClaimRuntimeValidators(
                artifact_runtime=artifact_runtime,
                paragraph_runtime=paragraph_runtime,
                research_runtime=research_runtime,
                fact_snapshot_runtime=fact_snapshot_runtime,
                claim_resolver=lambda ref: claim_verification_runtime.resolve_claim(ref),
            )
            claim_verification_runtime = ClaimVerificationRuntime(
                paragraph_runtime=paragraph_runtime,
                binding_source_validator=claim_validators.validate_binding_sources,
                ledger_path=runs_dir / "claim_verification.jsonl",
            )
            repair_store = ParagraphRepairAuthorizationStore(
                runs_dir / "paragraph_repair_authorizations.jsonl",
                run_commit_guard=lifecycle_service.assert_run_committable,
                require_run_commit_guard=True,
            )

            def canonical_repair_provider(task_id: str, run_id: str):
                return ParagraphRepairAuthorizationRuntime(
                    lifecycle_service=lifecycle_service,
                    paragraph_runtime=paragraph_runtime,
                    verification_runtime=claim_verification_runtime,
                    budget_manager=canonical_budget_provider(task_id, run_id),
                    store=repair_store,
                )

            canonical_repair_provider.store = repair_store
            canonical_repair_provider.budget_provider = canonical_budget_provider
            paragraph_validators.repair_provider = canonical_repair_provider
            target_post_write_composition.repair_provider = canonical_repair_provider
            claim_support_composition = TargetClaimSupportComposition(
                claim_runtime=claim_verification_runtime, source_composition=claim_validators,
                model_gateway=model_gateway,
            )
            claim_verification_runtime.support_resolver = claim_support_composition.runtime.resolve_support
            claim_verifier_composition = TargetClaimVerifierComposition(
                claim_runtime=claim_verification_runtime, support_composition=claim_support_composition,
                model_gateway=model_gateway, writer_execution_store=execution_store,
                verifier_max_output_tokens=(1024 if options.verifier_max_output_tokens is None
                    else options.verifier_max_output_tokens),
            )
            if claim_verifier_composition.writer_execution_store is not execution_store:
                raise RuntimeError("Verifier Writer execution owner must be canonical execution_store")
            claim_verification_runtime.verification_identity_validator = claim_verifier_composition.validate_v2_identity
            claim_verification_runtime.verification_source_validator = claim_verifier_composition.validate_verification_sources
            claim_verification_runtime.read_lineage_resolver = claim_validators.resolve_claim_read_lineage
            claim_verification_runtime.read_binding_source_validator = claim_validators.validate_read_binding_sources
            claim_verification_runtime.read_verification_source_validator = claim_verifier_composition.validate_read_sources
            from apps.enterprise_document.services.agent_rag_v1.section_dependency_runtime import SectionDependencyRuntime
            from apps.enterprise_document.services.agent_rag_v1.cross_section_context import CrossSectionClaimContextProvider
            section_dependencies = SectionDependencyRuntime(plan_runtime=section_plan_runtime,
                claim_runtime=claim_verification_runtime, verifier_composition=claim_verifier_composition,
                writer_execution_store=execution_store)
            target_workflow_runtime.section_dependencies = section_dependencies
            target_writer_context_composer.cross_section_provider = CrossSectionClaimContextProvider(section_dependencies)
            memory_composition.bind_owners(fact_snapshot_runtime=fact_snapshot_runtime,
                research_runtime=research_runtime, claim_runtime=claim_verification_runtime,
                lifecycle_service=lifecycle_service)
            target_workflow_runtime.memory_composition = memory_composition
            target_writer_context_composer.memory_composition = memory_composition
            target_post_write_composition.memory_composition = memory_composition
            scheme_writer.section_execution_coordinator.memory_composition = memory_composition
            target_post_write_composition.attach_claim_quality_projection(
                TargetPostWriteClaimProjection(
                    claim_runtime=claim_verification_runtime,
                    verifier_composition=claim_verifier_composition,
                )
            )
            if target_post_write_composition.citation_grounding.support.runtime is not claim_support_composition.runtime:
                raise RuntimeError("default Post-write Grounding must share canonical Support runtime")
            writer_invocation = PreWriteWriterInvocationService(
                builder=WriterInvocationBuilder(
                    prompt_service=scheme_writer.prompt_service,
                    target_context_composer=target_writer_context_composer,
                ),
                integrity_validator=WriterInvocationIntegrityValidator(),
                execution_store=execution_store,
                route_decision_store=route_decision_store,
                section_generation_service=scheme_writer.section_generation_service,
            )
            pre_write_services = PreWriteCoordinatorServices(
                requirements=requirements,
                fact_assessor=WriterAdmissionFactAssessor(evaluator=evaluator),
                admission_policy=WriterAdmissionPolicy(),
                admission_contract=WriterAdmissionContractSchema(),
                gate_binder=WriterGateBinder(),
                applicability_validator=WriterGateApplicabilityValidator(),
                enforcement=WriterAdmissionEnforcement(),
                recovery_controller=recovery_controller,
                router=PreWriteRouter,
                route_decision_store=route_decision_store,
                writer_invocation=writer_invocation,
                trace_emitter=trace_emitter,
                enforcement_mode=settings.gate_enforcement_mode,
                recovery_rounds_limit=settings.gate_recovery_rounds_limit,
                no_progress_rounds_limit=settings.gate_no_progress_rounds_limit,
            )
            scheme_writer.section_execution_coordinator.attach_pre_write_services(
                pre_write_services
            )
            scheme_writer.section_execution_coordinator.attach_research_runtime(
                research_runtime
            )
            scheme_writer.section_execution_coordinator.attach_paragraph_runtime(
                paragraph_runtime
            )
            scheme_writer.section_execution_coordinator.attach_claim_verification_runtime(
                claim_verification_runtime
            )
            scheme_writer.section_execution_coordinator.attach_claim_extraction_runtime(
                TargetClaimExtractionRuntime(claim_runtime=claim_verification_runtime, model_gateway=model_gateway)
            )
            scheme_writer.section_execution_coordinator.attach_claim_binding_composition(claim_validators)
            scheme_writer.section_execution_coordinator.attach_claim_support_composition(claim_support_composition)
            scheme_writer.section_execution_coordinator.attach_claim_verifier_composition(claim_verifier_composition)
            scheme_writer.section_execution_coordinator.attach_section_dependencies(section_dependencies)
            scheme_writer.stage_service.attach_target_workflow_runtime(
                target_workflow_runtime
            )
            scheme_writer.stage_service.attach_target_post_write_composition(
                target_post_write_composition
            )

            checkpoint_resource = create_sqlite_checkpoint_resource(
                runs_dir / "langgraph_checkpoints.sqlite3"
            )
            construction_resources.append(checkpoint_resource)
            checkpoint_resource.saver.setup()

            self._validate_target_startup(
                settings=settings,
                runs_dir=runs_dir,
                captures_dir=captures_dir,
                services=pre_write_services,
                evaluator=evaluator,
                execution_store=execution_store,
                route_decision_store=route_decision_store,
                trace_sink=trace_sink,
                trace_emitter=trace_emitter,
                checkpoint_resource=checkpoint_resource,
                coordinator=scheme_writer.section_execution_coordinator,
                evidence_bundle_store=evidence_bundle_store,
                lifecycle_service=lifecycle_service,
                artifact_runtime=artifact_runtime,
                fact_snapshot_runtime=fact_snapshot_runtime,
                section_plan_runtime=section_plan_runtime,
                research_runtime=research_runtime,
                paragraph_runtime=paragraph_runtime,
                claim_verification_runtime=claim_verification_runtime,
                target_workflow_runtime=target_workflow_runtime,
                research_authorization_dispatcher=research_authorization_dispatcher,
                canonical_budget_provider=canonical_budget_provider,
                target_post_write_composition=target_post_write_composition,
                storage_lifecycle_service=storage_lifecycle_service,
                disk_budget_guard=disk_budget_guard,
                storage_filesystem_roots=storage_filesystem_roots,
            )

            supervisor = SupervisorAgent(
                agent_registry=agents,
                workflows={"scheme_generation": workflow},
                run_trace_recorder=trace,
                task_manager=task_manager,
                model_gateway=model_gateway,
                supervisor_model_name=settings.supervisor_model_name,
                enable_llm_routing=settings.enable_llm_routing,
                workflow_engine=EnterpriseDocumentWorkflowEngine(
                    stage_service=scheme_writer.stage_service,
                    node_contracts=self._node_contracts(options),
                    run_trace_recorder=trace,
                    side_effect_commit_store=side_effect_commit_store,
                    checkpointer=checkpoint_resource.saver,
                    lifecycle_service=lifecycle_service,
                    artifact_runtime=artifact_runtime,
                    fact_snapshot_runtime=fact_snapshot_runtime,
                    section_plan_runtime=section_plan_runtime,
                    research_runtime=research_runtime,
                    budget_runtime=canonical_budget_provider,
                    memory_runtime=memory_composition,
                    storage_lifecycle_service=storage_lifecycle_service,
                    disk_budget_guard=disk_budget_guard,
                    storage_filesystem_roots=storage_filesystem_roots,
                ),
                # Resources are listed in dependency order. Supervisor closes them
                # in reverse after workers are idle: checkpoint, RAG, ModelGateway.
                owned_resources=(
                    model_gateway,
                    rag_service,
                    checkpoint_resource,
                ),
            )
            supervisor.attach_budget_continuation_owners(
                lifecycle_service=lifecycle_service,
                canonical_budget_provider=canonical_budget_provider,
                citation_grounding=target_post_write_composition.citation_grounding,
            )
            return supervisor
        except BaseException:
            self._close_construction_resources(construction_resources)
            raise
