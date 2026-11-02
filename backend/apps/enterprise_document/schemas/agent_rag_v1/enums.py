"""企业建设方案 Agent-RAG V1 使用的稳定枚举。"""

from __future__ import annotations

from enum import Enum


class AgentRole(str, Enum):
    PLANNER = "planner"
    RESEARCH = "research"
    WRITER = "writer"
    VERIFIER = "verifier"


class StateActorRole(str, Enum):
    PLANNER = "planner"
    RESEARCH = "research"
    WRITER = "writer"
    CLAIM_PIPELINE = "claim_pipeline"
    VERIFIER = "verifier"
    ROUTER = "router"
    AGGREGATOR = "aggregator"
    ASSEMBLER = "assembler"
    ASSEMBLY_VALIDATOR = "assembly_validator"
    SUPERVISOR = "supervisor"


class PlannerExecutionMode(str, Enum):
    INITIAL_PLAN = "initial_plan"
    LOCAL_REPLAN = "local_replan"
    CHANGE_IMPACT_ANALYSIS = "change_impact_analysis"


class SectionStrategy(str, Enum):
    SOURCE_ADAPT = "source_adapt"
    RAG_GENERATE = "rag_generate"
    TECHNICAL_GENERATE = "technical_generate"
    SKIP = "skip"
    HUMAN_REVIEW = "human_review"


class ResearchStatus(str, Enum):
    SUFFICIENT = "sufficient"
    INSUFFICIENT = "insufficient"
    CONFLICTED = "conflicted"
    EXHAUSTED = "exhausted"


class ResearchRecommendedAction(str, Enum):
    WRITE = "write"
    RESEARCH_MORE = "research_more"
    HUMAN_REVIEW = "human_review"
    PARTIAL_DELIVERY = "partial_delivery"


class VerifierExecutionMode(str, Enum):
    PARAGRAPH_PRECHECK = "paragraph_precheck"
    SECTION_VERIFY = "section_verify"
    DOCUMENT_VERIFY = "document_verify"
    FINAL_ARTIFACT_REVIEW = "final_artifact_review"


class VerifierAction(str, Enum):
    ACCEPT = "accept"
    REWRITE = "rewrite"
    RESEARCH_MORE = "research_more"
    REPLAN = "replan"
    HUMAN_REVIEW = "human_review"
    PARTIAL_ACCEPT = "partial_accept"
    REJECT = "reject"


class SectionStatus(str, Enum):
    PENDING = "pending"
    PLANNED = "planned"
    RESEARCHING = "researching"
    EVIDENCE_READY = "evidence_ready"
    GENERATING = "generating"
    PAUSED_FOR_PRECHECK = "paused_for_precheck"
    GENERATED = "generated"
    VERIFYING = "verifying"
    ACCEPTED = "accepted"
    PARTIALLY_ACCEPTED = "partially_accepted"
    WAITING_HUMAN = "waiting_human"
    BLOCKED = "blocked"
    FAILED = "failed"
    SKIPPED = "skipped"


class TaskStatus(str, Enum):
    PENDING = "pending"
    RUNNING = "running"
    WAITING_HUMAN = "waiting_human"
    DRAINING = "draining"
    SUCCESS = "success"
    PARTIAL_SUCCESS = "partial_success"
    FAILED = "failed"
    CANCELLED = "cancelled"


class AgentInvocationStatus(str, Enum):
    COMPLETED = "completed"
    PARTIAL = "partial"
    BLOCKED = "blocked"
    FAILED = "failed"


class AgentPayloadType(str, Enum):
    PLANNER_RESULT = "planner_result"
    RESEARCH_RESULT = "research_result"
    WRITER_RESULT = "writer_result"
    VERIFIER_RESULT = "verifier_result"


class ToolResultStatus(str, Enum):
    SUCCESS = "success"
    EMPTY = "empty"
    PARTIAL = "partial"
    RETRYABLE_ERROR = "retryable_error"
    PERMANENT_ERROR = "permanent_error"
    REJECTED = "rejected"
    STALE = "stale"


class PatchOperation(str, Enum):
    ADD = "add"
    REPLACE = "replace"
    REMOVE = "remove"


class WorkflowErrorSource(str, Enum):
    AGENT = "agent"
    TOOL_GATEWAY = "tool_gateway"
    STATE_GATEWAY = "state_gateway"
    WORKFLOW_ENGINE = "workflow_engine"
    DOCUMENT_ASSEMBLER = "document_assembler"
    VALIDATOR = "validator"


class WorkflowErrorCategory(str, Enum):
    TRANSIENT_INFRASTRUCTURE = "transient_infrastructure"
    TOOL_CONTRACT_ERROR = "tool_contract_error"
    AGENT_OUTPUT_INVALID = "agent_output_invalid"
    STATE_VERSION_CONFLICT = "state_version_conflict"
    STALE_EXECUTION = "stale_execution"
    PERMISSION_VIOLATION = "permission_violation"
    SCHEMA_VIOLATION = "schema_violation"
    BUSINESS_INPUT_MISSING = "business_input_missing"
    EVIDENCE_INSUFFICIENT = "evidence_insufficient"
    AUTHORITATIVE_FACT_CONFLICT = "authoritative_fact_conflict"
    BUDGET_EXHAUSTED = "budget_exhausted"
    HUMAN_INPUT_REQUIRED = "human_input_required"
    UNRECOVERABLE_SYSTEM_ERROR = "unrecoverable_system_error"


class WorkflowErrorScope(str, Enum):
    PARAGRAPH = "paragraph"
    SECTION = "section"
    SECTION_GROUP = "section_group"
    TASK = "task"


class WorkflowRetryability(str, Enum):
    RETRY_SAME_OPERATION = "retry_same_operation"
    RERUN_CURRENT_NODE = "rerun_current_node"
    RESUME_FROM_STAGE = "resume_from_stage"
    NOT_RETRYABLE = "not_retryable"


class ClaimSupportType(str, Enum):
    AUTHORITATIVE = "authoritative"
    DIRECT_SUPPORT = "direct_support"
    CONTEXTUAL_SUPPORT = "contextual_support"


class ClaimVerificationStatus(str, Enum):
    PENDING = "pending"
    VERIFIED = "verified"
    CONFLICTED = "conflicted"
    UNSUPPORTED = "unsupported"
    REJECTED = "rejected"


class ClaimLifecycleStatus(str, Enum):
    ACTIVE = "active"
    INVALIDATED = "invalidated"
    REMOVED = "removed"
    SUPERSEDED = "superseded"


class RouterSignalKind(str, Enum):
    TASK_CANCELLED = "task_cancelled"
    STALE_RESULT = "stale_result"
    INPUT_SNAPSHOT_UPDATED = "input_snapshot_updated"
    PERMISSION_ERROR = "permission_error"
    SCHEMA_ERROR = "schema_error"
    ARTIFACT_ERROR = "artifact_error"
    SECURITY_ERROR = "security_error"
    TASK_BUDGET_EXHAUSTED = "task_budget_exhausted"
    TASK_DRAINING = "task_draining"
    TASK_HUMAN_REVIEW = "task_human_review"
    SECTION_HUMAN_REVIEW = "section_human_review"
    HARD_DEPENDENCY_BLOCK = "hard_dependency_block"
    REPLAN = "replan"
    RESEARCH_MORE = "research_more"
    REWRITE = "rewrite"
    VERIFY = "verify"
    ACCEPT = "accept"
    PARTIAL_ACCEPT = "partial_accept"
    REJECT = "reject"
    AGGREGATE_EXPORT = "aggregate_export"


class RouterAction(str, Enum):
    DISCARD_STALE = "discard_stale"
    CANCEL_TASK = "cancel_task"
    REJECT_PROTOCOL = "reject_protocol"
    START_DRAINING = "start_draining"
    WAIT_TASK_HUMAN = "wait_task_human"
    WAIT_SECTION_HUMAN = "wait_section_human"
    BLOCK_SECTION = "block_section"
    REPLAN = "replan"
    RESEARCH_MORE = "research_more"
    REWRITE = "rewrite"
    VERIFY = "verify"
    ACCEPT = "accept"
    PARTIAL_ACCEPT = "partial_accept"
    REJECT = "reject"
    AGGREGATE_EXPORT = "aggregate_export"


class ResumeStage(str, Enum):
    PLANNER = "planner"
    RESEARCH = "research"
    WRITER = "writer"
    VERIFIER = "verifier"


class ResumeTriggerKind(str, Enum):
    EXCEL_CHANGED = "excel_changed"
    TEMPLATE_CHANGED = "template_changed"
    STRATEGY_MAY_CHANGE = "strategy_may_change"
    VERIFIER_REPLAN = "verifier_replan"
    NEW_MATERIAL = "new_material"
    VERIFIER_REQUESTED_EVIDENCE = "verifier_requested_evidence"
    EVIDENCE_READY_REWRITE = "evidence_ready_rewrite"
    DRAFT_REVISED = "draft_revised"


class SectionImpactAction(str, Enum):
    REGENERATE = "regenerate"
    RESEARCH_AGAIN = "research_again"
    REVERIFY_ONLY = "reverify_only"
    REUSE = "reuse"


class ParagraphChangeType(str, Enum):
    ADDED = "added"
    MODIFIED = "modified"
    DELETED = "deleted"
    MOVED = "moved"


class RepairAction(str, Enum):
    REWRITE = "rewrite"
    RESEARCH_MORE = "research_more"
    REPLAN = "replan"
    HUMAN_REVIEW = "human_review"


class RepairTaskStatus(str, Enum):
    PENDING = "pending"
    RUNNING = "running"
    RESOLVED = "resolved"
    OBSOLETE = "obsolete"
    FAILED = "failed"
    BLOCKED = "blocked"


class UnderlyingInvocationStatus(str, Enum):
    COMPLETED = "completed"
    RUNNING = "running"
    NOT_FOUND = "not_found"
    FAILED = "failed"
    UNKNOWN = "unknown"


class InvocationRecoveryAction(str, Enum):
    NO_ACTION = "no_action"
    SUPPLEMENT_EVENT = "supplement_event"
    LOAD_AND_COMMIT = "load_and_commit"
    RESUME_MONITORING = "resume_monitoring"
    IDEMPOTENT_RETRY = "idempotent_retry"
    DISCARD_STALE = "discard_stale"
    DO_NOT_RESUME = "do_not_resume"
