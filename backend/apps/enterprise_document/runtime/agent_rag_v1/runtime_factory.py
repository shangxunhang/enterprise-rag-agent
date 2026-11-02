"""Archived fixture composition root for the retired Standalone runtime."""

from __future__ import annotations

LEGACY_FIXTURE_ONLY = True

from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Callable

from core.persistence.safe_paths import SafeExternalIdentifier, SafePathResolver

from apps.enterprise_document.agents.agent_rag_v1 import (
    PlannerAgent,
    ResearchAgent,
    VerifierAgent,
    WriterAgent,
)
from apps.enterprise_document.schemas.agent_rag_v1 import (
    FactSnapshotSchema,
    TaskBudgetLimitsSchema,
    ToolHandlerOutputSchema,
    ToolInvocationSchema,
    ToolResultStatus,
)
from apps.enterprise_document.services.agent_rag_v1 import (
    BudgetManager,
    ClaimPipeline,
    DocumentAssemblyValidator,
    DocumentVerifierService,
    DocumentVersionPublisher,
    FileArtifactStore,
    FinalArtifactReviewService,
    GenerationReportService,
    IncrementalClaimPipeline,
    JsonCheckpointStore,
    JsonlDomainEventLog,
    JsonlToolExecutionStore,
    JsonlWriterProgressEventLog,
    MultiAgentDocumentAssembler,
    ParagraphPrecheckService,
    ResumeRouter,
    SectionDagScheduler,
    SectionGroupAggregator,
    StateGateway,
    StreamingWriterService,
    TaskStatusCalculator,
    ToolGateway,
    VersionPolicyService,
    WorkflowRouter,
)

from .contracts import (
    MultiAgentProjectInputSchema,
    RetrievalRequestSchema,
    RuntimeTemplateSchema,
    RuntimeTraceSchema,
)
from .ports import (
    AgentReasonerPort,
    DocumentExporterPort,
    FactSnapshotProviderPort,
    RetrievalServicePort,
    TemplateProviderPort,
)
from .standalone_adapters import (
    FixtureFactSnapshotProvider,
    FixtureTemplateProvider,
    MarkdownDocumentExporter,
    ScenarioAgentReasoner,
    ScenarioRetrievalService,
)

if TYPE_CHECKING:
    from .workflow_orchestrator import WorkflowOrchestrator


RetrievalFactory = Callable[
    [MultiAgentProjectInputSchema],
    RetrievalServicePort,
]
ExporterFactory = Callable[
    [MultiAgentProjectInputSchema],
    DocumentExporterPort,
]
ReasonerFactory = Callable[..., AgentReasonerPort]


@dataclass(frozen=True)
class RuntimeStoragePaths:
    run_root: Path
    artifact_root: Path
    checkpoint_root: Path
    event_log_path: Path
    tool_log_path: Path
    writer_event_log_path: Path


@dataclass
class RuntimeComponents:
    project_input: MultiAgentProjectInputSchema
    template: RuntimeTemplateSchema
    fact_snapshot: FactSnapshotSchema
    paths: RuntimeStoragePaths
    artifact_store: FileArtifactStore
    event_log: JsonlDomainEventLog
    tool_execution_store: JsonlToolExecutionStore
    checkpoint_store: JsonCheckpointStore
    writer_event_log: JsonlWriterProgressEventLog
    budget_manager: BudgetManager
    state_gateway: StateGateway
    tool_gateway: ToolGateway
    workflow_router: WorkflowRouter
    resume_router: ResumeRouter
    dag_scheduler: SectionDagScheduler
    planner_agent: PlannerAgent
    research_agent: ResearchAgent
    writer_agent: WriterAgent
    verifier_agent: VerifierAgent
    claim_pipeline: ClaimPipeline
    incremental_claim_pipeline: IncrementalClaimPipeline
    paragraph_precheck: ParagraphPrecheckService
    streaming_writer: StreamingWriterService
    document_verifier: DocumentVerifierService
    document_assembler: MultiAgentDocumentAssembler
    assembly_validator: DocumentAssemblyValidator
    final_artifact_review: FinalArtifactReviewService
    generation_report: GenerationReportService
    version_policy: VersionPolicyService
    version_publisher: DocumentVersionPublisher
    status_calculator: TaskStatusCalculator
    group_aggregator: SectionGroupAggregator
    document_exporter: DocumentExporterPort
    retrieval_service: RetrievalServicePort
    trace: RuntimeTraceSchema


@dataclass
class RuntimeSession:
    components: RuntimeComponents
    orchestrator: "WorkflowOrchestrator"


class RuntimeFactory:
    """Creates one isolated runtime session and injects every outer adapter."""

    def __init__(
        self,
        *,
        fact_provider: FactSnapshotProviderPort | None = None,
        template_provider: TemplateProviderPort | None = None,
        retrieval_factory: RetrievalFactory | None = None,
        exporter_factory: ExporterFactory | None = None,
        reasoner_factory: ReasonerFactory | None = None,
    ) -> None:
        self.fact_provider = fact_provider or FixtureFactSnapshotProvider()
        self.template_provider = template_provider or FixtureTemplateProvider()
        self.retrieval_factory = (
            retrieval_factory
            or (lambda project_input: ScenarioRetrievalService(project_input))
        )
        self.exporter_factory = (
            exporter_factory
            or (
                lambda project_input: MarkdownDocumentExporter(
                    project_input.output_dir
                )
            )
        )
        self.reasoner_factory = reasoner_factory or ScenarioAgentReasoner

    def create(
        self,
        project_input: MultiAgentProjectInputSchema,
    ) -> RuntimeComponents:
        if not project_input.task_id or not project_input.run_id:
            raise ValueError("RuntimeFactory requires resolved task_id and run_id")

        template = self.template_provider.load(project_input)
        fact_snapshot = self.fact_provider.load(project_input)
        paths = self._paths(project_input)
        artifact_store = FileArtifactStore(paths.artifact_root)
        event_log = JsonlDomainEventLog(paths.event_log_path)
        tool_store = JsonlToolExecutionStore(paths.tool_log_path)
        checkpoint_store = JsonCheckpointStore(paths.checkpoint_root)
        writer_event_log = JsonlWriterProgressEventLog(
            paths.writer_event_log_path
        )
        claim_pipeline = ClaimPipeline()
        paragraph_precheck = ParagraphPrecheckService(claim_pipeline)
        streaming_writer = StreamingWriterService(
            artifact_store=artifact_store,
            precheck=paragraph_precheck,
            progress_log=writer_event_log,
        )
        retrieval_service = self.retrieval_factory(project_input)
        tool_gateway = ToolGateway(
            execution_store=tool_store,
            artifact_store=artifact_store,
        )
        tool_gateway.register(
            name="knowledge.retrieve",
            version="standalone-v1",
            handler=self._retrieval_handler(
                project_input,
                retrieval_service,
            ),
            max_retries=0,
        )
        reasoner = self.reasoner_factory(
            project_input=project_input,
            template=template,
            fact_snapshot=fact_snapshot,
            artifact_store=artifact_store,
            streaming_writer=streaming_writer,
        )
        status_calculator = TaskStatusCalculator()
        budget_limits = self._budget_limits(project_input)
        return RuntimeComponents(
            project_input=project_input,
            template=template,
            fact_snapshot=fact_snapshot,
            paths=paths,
            artifact_store=artifact_store,
            event_log=event_log,
            tool_execution_store=tool_store,
            checkpoint_store=checkpoint_store,
            writer_event_log=writer_event_log,
            budget_manager=BudgetManager(
                task_id=project_input.task_id,
                limits=budget_limits,
            ),
            state_gateway=StateGateway(event_log=event_log),
            tool_gateway=tool_gateway,
            workflow_router=WorkflowRouter(),
            resume_router=ResumeRouter(),
            dag_scheduler=SectionDagScheduler(),
            planner_agent=PlannerAgent(
                reasoner=reasoner,
                tool_gateway=tool_gateway,
            ),
            research_agent=ResearchAgent(
                reasoner=reasoner,
                tool_gateway=tool_gateway,
            ),
            writer_agent=WriterAgent(
                reasoner=reasoner,
                tool_gateway=tool_gateway,
            ),
            verifier_agent=VerifierAgent(
                reasoner=reasoner,
                tool_gateway=tool_gateway,
            ),
            claim_pipeline=claim_pipeline,
            incremental_claim_pipeline=IncrementalClaimPipeline(
                claim_pipeline
            ),
            paragraph_precheck=paragraph_precheck,
            streaming_writer=streaming_writer,
            document_verifier=DocumentVerifierService(),
            document_assembler=MultiAgentDocumentAssembler(artifact_store),
            assembly_validator=DocumentAssemblyValidator(artifact_store),
            final_artifact_review=FinalArtifactReviewService(artifact_store),
            generation_report=GenerationReportService(
                status_calculator=status_calculator
            ),
            version_policy=VersionPolicyService(),
            version_publisher=DocumentVersionPublisher(
                artifact_store=artifact_store
            ),
            status_calculator=status_calculator,
            group_aggregator=SectionGroupAggregator(),
            document_exporter=self.exporter_factory(project_input),
            retrieval_service=retrieval_service,
            trace=RuntimeTraceSchema(),
        )

    def create_session(
        self,
        project_input: MultiAgentProjectInputSchema,
    ) -> RuntimeSession:
        from .workflow_orchestrator import WorkflowOrchestrator

        components = self.create(project_input)
        return RuntimeSession(
            components=components,
            orchestrator=WorkflowOrchestrator(components),
        )

    @staticmethod
    def _paths(
        project_input: MultiAgentProjectInputSchema,
    ) -> RuntimeStoragePaths:
        assert project_input.task_id is not None
        assert project_input.run_id is not None
        SafeExternalIdentifier.validate(
            project_input.task_id,
            field_name="task_id",
        )
        SafeExternalIdentifier.validate(
            project_input.run_id,
            field_name="run_id",
        )
        resolver = SafePathResolver(Path(project_input.output_dir) / "_runtime")
        run_root = resolver.resolve(project_input.task_id, project_input.run_id)
        run_root.mkdir(parents=True, exist_ok=True)
        return RuntimeStoragePaths(
            run_root=run_root,
            artifact_root=run_root / "artifacts",
            checkpoint_root=run_root / "checkpoints",
            event_log_path=run_root / "domain-events.jsonl",
            tool_log_path=run_root / "tool-executions.jsonl",
            writer_event_log_path=run_root / "writer-progress.jsonl",
        )

    @staticmethod
    def _budget_limits(
        project_input: MultiAgentProjectInputSchema,
    ) -> TaskBudgetLimitsSchema:
        limits = project_input.budget_limits
        if project_input.scenario != "budget-exhausted":
            return limits
        return TaskBudgetLimitsSchema(
            max_model_calls=min(limits.max_model_calls, 4),
            max_tool_calls=limits.max_tool_calls,
            max_total_replans=limits.max_total_replans,
            max_total_rewrites=limits.max_total_rewrites,
            max_document_repair_tasks=limits.max_document_repair_tasks,
            max_wall_clock_seconds=limits.max_wall_clock_seconds,
        )

    @staticmethod
    def _retrieval_handler(
        project_input: MultiAgentProjectInputSchema,
        retrieval_service: RetrievalServicePort,
    ) -> Callable[[ToolInvocationSchema], ToolHandlerOutputSchema]:
        def retrieve(
            invocation: ToolInvocationSchema,
        ) -> ToolHandlerOutputSchema:
            if invocation.section_id is None:
                raise ValueError("knowledge.retrieve requires section_id")
            request = RetrievalRequestSchema(
                task_id=invocation.task_id,
                run_id=invocation.run_id,
                section_id=invocation.section_id,
                query_topics=[
                    str(value)
                    for value in invocation.arguments.get("query_topics", [])
                ],
                research_round=int(
                    invocation.arguments.get("research_round", 1)
                ),
                scenario=project_input.scenario,
            )
            response = retrieval_service.retrieve(request)
            if response.status == "sufficient":
                status = ToolResultStatus.SUCCESS
            elif response.status == "empty":
                status = ToolResultStatus.EMPTY
            else:
                status = ToolResultStatus.PARTIAL
            return ToolHandlerOutputSchema(
                status=status,
                data_type="retrieval_response_v1",
                data=response.model_dump(mode="json"),
                source_refs=response.source_refs,
                raw_result=response.model_dump(mode="json"),
            )

        return retrieve
