"""Architecture Spec v1.0 Phase 2 确定性基础设施。"""

from .artifact_runtime import (
    ArtifactCurrentnessError,
    ArtifactIdentityCollision,
    ArtifactOperationInProgress,
    ArtifactRuntime,
    ArtifactRuntimeError,
    ArtifactRuntimeIntegrityError,
    ArtifactVersionCollision,
    ArtifactVersionNotFound,
)
from .artifact_store import ArtifactIntegrityError, FileArtifactStore
from .fact_snapshot_runtime import (
    FactSnapshotCurrentnessError,
    FactSnapshotError,
    FactSnapshotRuntime,
    FactSnapshotSourceBindingInvalid,
)
from .budget_manager import (
    BudgetManager,
    CanonicalBudgetCapacityExceeded,
    CanonicalBudgetDeadlineExceeded,
    CanonicalBudgetDraining,
    CanonicalBudgetIdentityConflict,
    CanonicalBudgetIntegrityError,
    CanonicalBudgetRevisionConflict,
    TaskBudgetExhausted,
)
from .checkpoint_store import CheckpointIntegrityError, JsonCheckpointStore
from .claim_pipeline import (
    ClaimBindingResolver,
    ClaimMerger,
    ClaimPipeline,
    DeterministicClaimExtractor,
    IncrementalClaimPipeline,
    SemanticClaimExtractor,
)
from .conflict_repair import ConflictRepairPlanService
from .dag_scheduler import SectionDagError, SectionDagScheduler
from .document_assembler_v1 import MultiAgentDocumentAssembler
from .document_verifier import DocumentVerifierService
from .draft_diff import DraftDiffService
from .event_log import (
    DomainEventLog,
    InMemoryDomainEventLog,
    JsonlDomainEventLog,
)
from .event_replay_recovery import (
    EventReplayRecoveryError,
    EventReplayRecoveryService,
)
from .evidence_sanitizer import EvidenceSanitizer
from .permissions import StateWritePermissionError, WritePermissionValidator
from .paragraph_identity import ParagraphIdentityService
from .paragraph_runtime import (
    PARAGRAPH_ACTIVE_PROMOTION,
    PARAGRAPH_ARTIFACT_TYPE,
    PARAGRAPH_CONTENT_SCHEMA_VERSION,
    PARAGRAPH_VERSION_COMMIT,
    SECTION_PARAGRAPH_MANIFEST_SEAL,
    SECTION_PARAGRAPH_MANIFEST_UPDATE,
    ParagraphCurrentnessError,
    ParagraphIdentityCollision,
    ParagraphManifestConflict,
    ParagraphManifestIntegrityError,
    ParagraphOperationInProgress,
    ParagraphRuntime,
    ParagraphRuntimeError,
    ParagraphSourceBindingError,
)
from .paragraph_precheck import ParagraphPrecheckService
from .repair_authorization_runtime import (
    PARAGRAPH_REPAIR_AUTHORIZATION_COMMIT,
    ParagraphRepairAuthorizationCurrentnessError,
    ParagraphRepairAuthorizationError,
    ParagraphRepairAuthorizationIntegrityError,
    ParagraphRepairAuthorizationRuntime,
    ParagraphRepairAuthorizationStore,
)
from .aggregation import SectionGroupAggregator
from .impact_analyzer import ImpactAnalyzer
from .assembly_validator import DocumentAssemblyValidator
from .final_artifact_review import FinalArtifactReviewService
from .generation_report import GenerationReportService
from .human_review_resume import HumanReviewResumeError, HumanReviewResumeService
from .lifecycle_runtime import (
    LifecycleCurrentnessError,
    LifecycleIntegrityError,
    LifecycleRevisionConflict,
    RunAttemptLifecycleService,
)
from .model_tool_budget_runtime import CanonicalInvocationBudgetAdapter
from .plan_runtime import (
    SectionPlanCommitOperations,
    SectionPlanCurrentnessError,
    SectionPlanRuntime,
    SectionPlanRuntimeError,
    SectionPlanSourceBindingError,
)
from .research_runtime import (
    EVIDENCE_PACKAGE_ARTIFACT_TYPE,
    EVIDENCE_PACKAGE_CONTENT_SCHEMA_VERSION,
    RESEARCH_ROUND_COMMIT_OPERATION_KIND,
    EvidencePackageCommitOperations,
    EvidencePackageCurrentnessError,
    EvidencePackageSourceBindingError,
    ResearchIdentityCollision,
    ResearchOperationInProgress,
    ResearchProgressConflict,
    ResearchRoundCommitResult,
    ResearchRoundNotFound,
    ResearchRuntime,
    ResearchRuntimeError,
    ResearchRuntimeIntegrityError,
)
from .resume_router import ResumeRouter
from .run_completion_assessment_store import (
    RunCompletionAssessmentStore,
    RunCompletionAssessmentStoreConflict,
    RunCompletionAssessmentStoreError,
    RunCompletionAssessmentStoreIntegrityError,
)
from .run_completion_runtime import (
    RUN_COMPLETION_ASSESSMENT_COMMIT,
    RunCompletionAssessmentBuilder,
    RunCompletionAssessmentError,
    RunCompletionAssessmentPrerequisiteError,
    RunCompletionAssessmentStaleError,
    RunCompletionAssessmentTerminalizationResolver,
    RunCompletionTerminalizationSnapshot,
    exact_ref_envelope,
)
from .running_invocation_recovery import RunningInvocationRecoveryService
from .state_gateway import StateCommitResult, StateGateway, StateGatewayError
from .supervisor_state_adapter import SupervisorStateAdapter
from .streaming_writer import StreamingWriterService
from .tool_gateway import AgentToolSession, ToolGateway
from .tool_store import (
    InMemoryToolExecutionStore,
    JsonlToolExecutionStore,
    ToolExecutionStore,
)
from .task_status import TaskStatusCalculator
from .transitions import StateTransitionError, StateTransitionValidator
from .version_policy import VersionPolicyService
from .version_publisher import DocumentVersionPublisher
from .workflow_router import WorkflowRouter
from .writer_event_log import (
    InMemoryWriterProgressEventLog,
    JsonlWriterProgressEventLog,
    WriterProgressEventLog,
)

__all__ = [
    "ArtifactCurrentnessError",
    "ArtifactIdentityCollision",
    "ArtifactIntegrityError",
    "ArtifactOperationInProgress",
    "ArtifactRuntime",
    "ArtifactRuntimeError",
    "ArtifactRuntimeIntegrityError",
    "ArtifactVersionCollision",
    "ArtifactVersionNotFound",
    "EVIDENCE_PACKAGE_ARTIFACT_TYPE",
    "EVIDENCE_PACKAGE_CONTENT_SCHEMA_VERSION",
    "RESEARCH_ROUND_COMMIT_OPERATION_KIND",
    "BudgetManager",
    "CanonicalBudgetCapacityExceeded",
    "CanonicalBudgetDeadlineExceeded",
    "CanonicalBudgetDraining",
    "CanonicalBudgetIdentityConflict",
    "CanonicalBudgetIntegrityError",
    "CanonicalBudgetRevisionConflict",
    "CanonicalInvocationBudgetAdapter",
    "ClaimBindingResolver",
    "ClaimMerger",
    "ClaimPipeline",
    "CheckpointIntegrityError",
    "ConflictRepairPlanService",
    "DomainEventLog",
    "DeterministicClaimExtractor",
    "DraftDiffService",
    "EventReplayRecoveryError",
    "EventReplayRecoveryService",
    "EvidencePackageCommitOperations",
    "EvidencePackageCurrentnessError",
    "EvidencePackageSourceBindingError",
    "EvidenceSanitizer",
    "DocumentAssemblyValidator",
    "DocumentVerifierService",
    "DocumentVersionPublisher",
    "FactSnapshotCurrentnessError",
    "FactSnapshotError",
    "FactSnapshotRuntime",
    "FactSnapshotSourceBindingInvalid",
    "FileArtifactStore",
    "FinalArtifactReviewService",
    "GenerationReportService",
    "HumanReviewResumeError",
    "HumanReviewResumeService",
    "LifecycleCurrentnessError",
    "LifecycleIntegrityError",
    "LifecycleRevisionConflict",
    "RunAttemptLifecycleService",
    "AgentToolSession",
    "InMemoryDomainEventLog",
    "ImpactAnalyzer",
    "InMemoryWriterProgressEventLog",
    "IncrementalClaimPipeline",
    "JsonCheckpointStore",
    "JsonlDomainEventLog",
    "JsonlToolExecutionStore",
    "JsonlWriterProgressEventLog",
    "MultiAgentDocumentAssembler",
    "InMemoryToolExecutionStore",
    "ResearchIdentityCollision",
    "ResearchOperationInProgress",
    "ResearchProgressConflict",
    "ResearchRoundCommitResult",
    "ResearchRoundNotFound",
    "ResearchRuntime",
    "ResearchRuntimeError",
    "ResearchRuntimeIntegrityError",
    "ResumeRouter",
    "RUN_COMPLETION_ASSESSMENT_COMMIT",
    "RunCompletionAssessmentBuilder",
    "RunCompletionAssessmentError",
    "RunCompletionAssessmentPrerequisiteError",
    "RunCompletionAssessmentStaleError",
    "RunCompletionAssessmentStore",
    "RunCompletionAssessmentStoreConflict",
    "RunCompletionAssessmentStoreError",
    "RunCompletionAssessmentStoreIntegrityError",
    "RunCompletionAssessmentTerminalizationResolver",
    "RunCompletionTerminalizationSnapshot",
    "SectionPlanCommitOperations",
    "SectionPlanCurrentnessError",
    "SectionPlanRuntime",
    "SectionPlanRuntimeError",
    "SectionPlanSourceBindingError",
    "RunningInvocationRecoveryService",
    "PARAGRAPH_ACTIVE_PROMOTION",
    "PARAGRAPH_ARTIFACT_TYPE",
    "PARAGRAPH_CONTENT_SCHEMA_VERSION",
    "PARAGRAPH_VERSION_COMMIT",
    "SECTION_PARAGRAPH_MANIFEST_SEAL",
    "SECTION_PARAGRAPH_MANIFEST_UPDATE",
    "ParagraphCurrentnessError",
    "ParagraphIdentityCollision",
    "ParagraphIdentityService",
    "ParagraphManifestConflict",
    "ParagraphManifestIntegrityError",
    "PARAGRAPH_REPAIR_AUTHORIZATION_COMMIT",
    "ParagraphOperationInProgress",
    "ParagraphRepairAuthorizationCurrentnessError",
    "ParagraphRepairAuthorizationError",
    "ParagraphRepairAuthorizationIntegrityError",
    "ParagraphRepairAuthorizationRuntime",
    "ParagraphRepairAuthorizationStore",
    "ParagraphRuntime",
    "ParagraphRuntimeError",
    "ParagraphSourceBindingError",
    "ParagraphPrecheckService",
    "SectionDagError",
    "SectionDagScheduler",
    "SectionGroupAggregator",
    "StateCommitResult",
    "StateGateway",
    "StateGatewayError",
    "StateTransitionError",
    "StateTransitionValidator",
    "StateWritePermissionError",
    "StreamingWriterService",
    "SupervisorStateAdapter",
    "SemanticClaimExtractor",
    "TaskBudgetExhausted",
    "TaskStatusCalculator",
    "ToolExecutionStore",
    "ToolGateway",
    "WritePermissionValidator",
    "exact_ref_envelope",
    "VersionPolicyService",
    "WorkflowRouter",
    "WriterProgressEventLog",
]
